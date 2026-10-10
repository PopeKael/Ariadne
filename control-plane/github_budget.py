"""Shared, persistent and fail-closed GitHub API budget and bounded cache.

Local limits are deliberately lower than GitHub's; its response headers and
retry instructions can only reduce our allowance. No credentials are persisted.
"""
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import hashlib
from pathlib import Path
import sqlite3
import threading
import time
from urllib.error import HTTPError

BANGKOK = timezone(timedelta(hours=7))
HOUR_LIMIT, DAY_LIMIT, SEARCH_MINUTE_LIMIT = 45, 90, 6
CACHE_SECONDS = 86400


class GitHubPaused(RuntimeError):
    pass


class GitHubBudget:
    def __init__(self, path, clock=time.time, sleep=time.sleep):
        self.path, self.clock, self.sleep = Path(path), clock, sleep
        self.lock = threading.RLock()
        self.read_context = threading.local()

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('CREATE TABLE IF NOT EXISTS calls (at REAL, bucket TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, at REAL, body BLOB, charset TEXT)')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def get(db, key, default='0'):
        row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    @staticmethod
    def put(db, key, value):
        db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', (key, str(value)))

    def day_start(self, now):
        return datetime.fromtimestamp(now, BANGKOK).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def status(self):
        now = self.clock()
        with self.lock, self.connect() as db:
            hour = db.execute('SELECT COUNT(*) FROM calls WHERE at>?', (now-3600,)).fetchone()[0]
            day = db.execute('SELECT COUNT(*) FROM calls WHERE at>=?', (self.day_start(now),)).fetchone()[0]
            pause = float(self.get(db, 'pause'))
            searched = self.get(db, 'discovery_day', '') == datetime.fromtimestamp(now, BANGKOK).date().isoformat()
            message = f'Ariadne GitHub budget: {hour}/{HOUR_LIMIT} requests this hour · {day}/{DAY_LIMIT} today.'
            if pause > now:
                message += ' Checks paused until ' + datetime.fromtimestamp(pause, BANGKOK).strftime('%d %b %H:%M Bangkok') + '.'
            elif hour >= HOUR_LIMIT or day >= DAY_LIMIT:
                message += ' Local budget reached; network checks will wait.'
            elif hour >= 30 or day >= 60:
                message += ' Approaching the local safety cap; use cached results.'
            for bucket in ('core', 'search'):
                reset = float(self.get(db, bucket+'_reset'))
                remaining = float(self.get(db, bucket+'_remaining', '999999'))
                if reset > now and remaining <= (2 if bucket == 'search' else 15):
                    message += f' GitHub {bucket} allowance is low ({int(remaining)} remaining); safety reserve applies until ' + datetime.fromtimestamp(reset, BANGKOK).strftime('%H:%M Bangkok') + '.'
            if searched:
                message += ' Fresh discovery already requested today; cached browsing remains available.'
            return dict(message=message, hour_used=hour, hour_limit=HOUR_LIMIT, day_used=day,
                        day_limit=DAY_LIMIT, paused_until=pause if pause > now else None,
                        discovery_requested_today=searched)

    def begin_discovery(self):
        """One fresh search batch/day, including partial/failed attempts."""
        today = datetime.fromtimestamp(self.clock(), BANGKOK).date().isoformat()
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.get(db, 'discovery_day', '') == today:
                raise GitHubPaused('Fresh GitHub discovery was already requested today. Browse cached cards; another fresh search is available tomorrow (Bangkok time).')
            self.put(db, 'discovery_day', today)

    def request(self, request, opener, *, timeout=10, max_bytes=512000):
        # Include auth identity in the hash so private responses can never be
        # returned to an unauthenticated/different identity. Store no token.
        key = hashlib.sha256((request.full_url + '\n' + (request.get_header('Authorization') or '')).encode()).hexdigest()
        bucket = 'search' if '/search/' in request.full_url else 'core'
        with self.lock:
            now = self.clock()
            with self.connect() as db:
                cached = db.execute('SELECT at,body,charset FROM cache WHERE key=?', (key,)).fetchone()
                if cached and 0 <= now-cached[0] < CACHE_SECONDS:
                    self.read_context.last = (request.full_url, cached[0])
                    return bytes(cached[1]), cached[2]
                db.execute('BEGIN IMMEDIATE')
                db.execute('DELETE FROM calls WHERE at<?', (min(now-3600, self.day_start(now)),))
                pause = float(self.get(db, 'pause'))
                if pause > now:
                    raise GitHubPaused('GitHub checks are paused until ' + datetime.fromtimestamp(pause, BANGKOK).strftime('%d %b %H:%M Bangkok') + '. Cached results remain available.')
                hour = db.execute('SELECT COUNT(*) FROM calls WHERE at>?', (now-3600,)).fetchone()[0]
                day = db.execute('SELECT COUNT(*) FROM calls WHERE at>=?', (self.day_start(now),)).fetchone()[0]
                if hour >= HOUR_LIMIT or day >= DAY_LIMIT:
                    reason = 'hourly (45/hour)' if hour >= HOUR_LIMIT else 'daily (90/Bangkok day)'
                    raise GitHubPaused('Ariadne local ' + reason + ' GitHub safety cap reached; no request sent. Cached results remain available.')
                reset = float(self.get(db, bucket+'_reset'))
                remaining = float(self.get(db, bucket+'_remaining', '999999'))
                if reset > now and remaining <= (1 if bucket == 'search' else 10):
                    raise GitHubPaused('GitHub safety reserve reached. Checks paused until ' + datetime.fromtimestamp(reset, BANGKOK).strftime('%H:%M Bangkok') + '; cached results remain available.')
                searches = db.execute("SELECT COUNT(*) FROM calls WHERE bucket='search' AND at>?", (now-60,)).fetchone()[0]
                if bucket == 'search' and searches >= SEARCH_MINUTE_LIMIT:
                    raise GitHubPaused('Search pacing cap reached; wait one minute. Cached results remain available.')
                last = float(self.get(db, 'last_request'))
                self.sleep(max(0, 2-(now-last)))
                now = self.clock()
                db.execute('INSERT INTO calls VALUES (?,?)', (now, bucket))
                self.put(db, 'last_request', now)
            # One in-flight request across discovery and saved watches.
            try:
                with opener(request, timeout=timeout) as response:
                    self.observe(response.headers, bucket)
                    raw = response.read(max_bytes+1)
                    if len(raw) > max_bytes:
                        raise ValueError('GitHub response exceeded the bounded evidence limit.')
                    charset = response.headers.get_content_charset() or 'utf-8'
            except HTTPError as exc:
                self.observe(exc.headers or {}, bucket)
                if exc.code in {403, 429}:
                    with self.connect() as db:
                        failures = int(self.get(db, 'failures')) + 1
                        self.put(db, 'failures', failures)
                        delay = min(3600, 60 * 2**min(failures-1, 6))
                        try:
                            delay = max(delay, float((exc.headers or {}).get('Retry-After', 0)))
                        except ValueError:
                            pass
                        reset = float(self.get(db, bucket+'_reset'))
                        if (exc.headers or {}).get('X-RateLimit-Remaining') == '0':
                            delay = max(delay, reset-self.clock()+5)
                        self.put(db, 'pause', self.clock()+delay)
                    raise GitHubPaused('GitHub refused further requests. ' + self.status()['message']) from None
                raise
            with self.connect() as db:
                self.put(db, 'failures', 0)
                db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?,?)', (key, self.clock(), raw, charset))
                db.execute('DELETE FROM cache WHERE at<?', (self.clock()-7*86400,))
                db.execute('DELETE FROM cache WHERE key NOT IN (SELECT key FROM cache ORDER BY at DESC LIMIT 200)')
            self.read_context.last = (request.full_url, self.clock())
            return raw, charset

    def observe(self, headers, bucket):
        with self.connect() as db:
            for header, field in [('X-RateLimit-Remaining','remaining'), ('X-RateLimit-Reset','reset')]:
                value = headers.get(header)
                if value is not None:
                    self.put(db, bucket+'_'+field, float(value))


BUDGET = GitHubBudget(Path(__file__).parent / 'runtime' / 'github-budget.sqlite3')
