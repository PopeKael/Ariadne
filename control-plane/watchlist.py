"""Local follow-up ownership. Search providers collect evidence; never execute it."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import uuid
from urllib.parse import urlsplit


def stamp(now=None):
    return (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")


def parse_time(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("A date must include a timezone.")
    return result.astimezone(timezone.utc)


class Watchlist:
    def __init__(self, path, collect, clock=None):
        self.path, self.collect = Path(path), collect
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.owner = uuid.uuid4().hex
        self.scheduler_error = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS watches (
                    id TEXT PRIMARY KEY, identity TEXT UNIQUE, data TEXT NOT NULL,
                    next_check TEXT NOT NULL, state TEXT NOT NULL,
                    token TEXT, lease_until TEXT);
                CREATE TABLE IF NOT EXISTS checks (
                    id INTEGER PRIMARY KEY, watch_id TEXT NOT NULL,
                    checked_at TEXT NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS checks_watch ON checks(watch_id, id DESC);
            """)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, watch_id):
        with self.db() as db:
            row = db.execute("SELECT data,token,lease_until FROM watches WHERE id=?", (watch_id,)).fetchone()
        if row is None:
            raise ValueError("Watch not found.")
        data = json.loads(row["data"])
        data["checking"] = bool(row["token"] and row["lease_until"] > stamp(self.clock()))
        return data

    def snapshot(self):
        from github_budget import BUDGET
        with self.db() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM watches ORDER BY next_check,id")]
        items = [self.get(watch_id) for watch_id in ids]
        return {"ok": True, "watches": items, "github_budget": BUDGET.status(),
                "attention": sum(bool(w["unread"] or w["reminder_due"]) for w in items if w["state"] == "active"),
                "scheduler": {"running": bool(self.thread and self.thread.is_alive()),
                              "error": self.scheduler_error,
                              "detail": "Checks run while Ariadne is running. Overdue work is picked up on startup."}}

    @staticmethod
    def validate(body):
        title = str(body.get("title") or "").strip()
        kind = body.get("kind", "topic")
        purpose = str(body.get("purpose") or "").strip()
        query = str(body.get("query") or "").strip()
        sources = body.get("sources", [])
        if not 1 <= len(title) <= 200 or len(purpose) > 2000 or len(query) > 500:
            raise ValueError("Use a title up to 200 characters, purpose up to 2000 and query up to 500.")
        if kind not in {"project", "topic", "reminder"}:
            raise ValueError("Choose project, topic or reminder.")
        interval = body.get("interval_days", 7)
        if type(interval) is not int or not 1 <= interval <= 366:
            raise ValueError("Check frequency must be 1–366 days.")
        if not isinstance(sources, list) or len(sources) > 6 or any(not isinstance(s, str) for s in sources):
            raise ValueError("Provide up to six source URLs.")
        from watchlist_sources import public_url
        sources = list(dict.fromkeys(public_url(s.strip(), resolve=False) for s in sources if s.strip()))
        if kind != "reminder" and not (query or sources):
            raise ValueError("Add a search query or at least one source URL.")
        return dict(title=title, kind=kind, purpose=purpose, query=query, sources=sources, interval_days=interval)

    def save(self, body):
        fields = self.validate(body)
        watch_id = body.get("id")
        now = self.clock()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if watch_id:
                row = db.execute("SELECT data,identity FROM watches WHERE id=?", (watch_id,)).fetchone()
                if row is None:
                    raise ValueError("Watch not found.")
                data, identity = json.loads(row["data"]), row["identity"]
                if fields["kind"] != data["kind"]:
                    raise ValueError("Keep the watch type; create a new entry to change it.")
            else:
                # Repeated Rabbit Hole clicks and news-topic imports cannot erase decisions.
                identity = str(body.get("identity") or "").strip()[:600] or None
                if fields["kind"] == "project" and len(fields["sources"]) == 1:
                    source = urlsplit(fields["sources"][0])
                    repo = source.path.strip("/").split("/")
                    if source.hostname == "github.com" and len(repo) >= 2:
                        identity = "github:https://github.com/" + "/".join(repo[:2]).removesuffix(".git").casefold()
                if identity:
                    row = db.execute("SELECT id FROM watches WHERE identity=?", (identity,)).fetchone()
                    if row:
                        return self.get(row["id"])
                watch_id = uuid.uuid4().hex
                data = dict(id=watch_id, identity=identity, created_at=stamp(now), state="active", last_checked=None,
                            last_success=None, last_error=None, unread=False, reminder_due=False,
                            evidence=[], seen={}, summary="Not checked yet.",
                            next_check=stamp(now + timedelta(days=fields["interval_days"])) if fields["kind"] == "reminder" else stamp(now))
            data.update(fields, updated_at=stamp(now))
            if body.get("next_check"):
                data["next_check"] = stamp(parse_time(body["next_check"]))
                if fields["kind"] == "reminder":
                    data.update(reminder_due=False, unread=False, summary="Next reminder scheduled.")
            elif data.get("last_success") and fields["kind"] != "reminder":
                data["next_check"] = stamp(now)  # Revised question/sources deserve a new check.
            self._write(db, data, identity)
        self.wake.set()
        return self.get(watch_id)

    @staticmethod
    def _write(db, data, identity=None):
        db.execute("""INSERT INTO watches(id,identity,data,next_check,state) VALUES(?,?,?,?,?)
                      ON CONFLICT(id) DO UPDATE SET data=excluded.data,next_check=excluded.next_check,
                      state=excluded.state,token=NULL,lease_until=NULL""",
                   (data["id"], identity, json.dumps(data, ensure_ascii=False), data["next_check"], data["state"]))

    def action(self, watch_id, action, days=1):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data,identity FROM watches WHERE id=?", (watch_id,)).fetchone()
            if row is None:
                raise ValueError("Watch not found.")
            data, now = json.loads(row["data"]), self.clock()
            if action in {"pause", "archive", "resume"}:
                data["state"] = {"pause": "paused", "archive": "archived", "resume": "active"}[action]
                if action == "resume" and data["kind"] != "reminder":
                    data["next_check"] = stamp(now)
            elif action == "snooze":
                if type(days) is not int or not 1 <= days <= 366:
                    raise ValueError("Snooze for 1–366 days.")
                data.update(next_check=stamp(now + timedelta(days=days)), reminder_due=False, unread=False,
                            summary="Snoozed. We’ll come back to this.")
            elif action == "acknowledge":
                data["unread"] = False
            elif action == "done" and data["kind"] == "reminder":
                data.update(reminder_due=False, unread=False, last_success=stamp(now),
                            next_check=stamp(now + timedelta(days=data["interval_days"])), summary="Done. Next reminder scheduled.")
                self._history(db, watch_id, dict(checked_at=stamp(now), status="done", summary=data["summary"], evidence=[]))
            elif action == "check" and data["kind"] != "reminder" and data["state"] == "active":
                # A second request must not invalidate the in-flight collection.
                busy = db.execute("SELECT token,lease_until FROM watches WHERE id=?", (watch_id,)).fetchone()
                if busy["token"] and busy["lease_until"] > stamp(now):
                    return self.get(watch_id)
                data["next_check"] = stamp(now)
            else:
                raise ValueError("Action is unavailable for this watch.")
            data["updated_at"] = stamp(now)
            if action == "acknowledge":
                db.execute("UPDATE watches SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), watch_id))
            else:
                self._write(db, data, row["identity"])
        self.wake.set()
        return self.get(watch_id)

    def check_all(self):
        """Queue active source watches atomically without disturbing live claims."""
        queued, checking = 0, 0
        now = stamp(self.clock())
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT * FROM watches WHERE state='active'").fetchall():
                data = json.loads(row['data'])
                if data['kind'] == 'reminder':
                    continue
                if row['token'] and row['lease_until'] > now:
                    checking += 1
                    continue
                data.update(next_check=now, updated_at=now)
                self._write(db, data, row['identity'])
                queued += 1
        self.wake.set()
        return dict(ok=True, queued=queued, checking=checking)

    def history(self, watch_id, before=None):
        self.get(watch_id)
        with self.db() as db:
            rows = db.execute("SELECT id,data FROM checks WHERE watch_id=? AND id<? ORDER BY id DESC LIMIT 30",
                              (watch_id, int(before or 9223372036854775807))).fetchall()
        return {"ok": True, "checks": [{"id": row["id"], **json.loads(row["data"])} for row in rows],
                "before": rows[-1]["id"] if len(rows) == 30 else None}

    @staticmethod
    def _history(db, watch_id, check):
        db.execute("INSERT INTO checks(watch_id,checked_at,data) VALUES(?,?,?)",
                   (watch_id, check["checked_at"], json.dumps(check, ensure_ascii=False)))

    def tick(self):
        """Claim one due watch transactionally; network work never holds the DB lock."""
        now, token = self.clock(), self.owner + ":" + uuid.uuid4().hex
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT id,data FROM watches WHERE state='active' AND next_check<=?
                AND (token IS NULL OR lease_until<=?) ORDER BY next_check,id LIMIT 1""", (stamp(now), stamp(now))).fetchone()
            if row is None:
                return False
            data = json.loads(row["data"])
            db.execute("UPDATE watches SET token=?,lease_until=? WHERE id=?",
                       (token, stamp(now + timedelta(minutes=15)), row["id"]))
        checked_at = stamp(self.clock())
        evidence, error = [], None
        try:
            if data["kind"] != "reminder":
                evidence = self.collect(data)
                if not evidence:
                    raise ValueError("No usable evidence returned. Previous findings retained.")
        except Exception as exc:
            error = str(exc)[:700]
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT token,data FROM watches WHERE id=?", (data["id"],)).fetchone()
            if current["token"] != token:
                # A user changed/paused this watch while it was collecting.
                return True
            data["unread"] = json.loads(current["data"])["unread"]
            data["last_checked"] = checked_at
            if error:
                data.update(last_error=error, summary="Check failed. Previous findings retained.", unread=True,
                            next_check=stamp(self.clock() + timedelta(hours=1)))
                status = "failed"
            elif data["kind"] == "reminder":
                data.update(reminder_due=True, unread=True, summary="This reminder is due. Mark it done when you’ve finished.",
                            next_check="9999-12-31T00:00:00+00:00", last_error=None)
                status = "due"
            else:
                seen = data.get("seen", {})
                changed = []
                for item in evidence:
                    key = item["url"]
                    digest = hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                    if seen.get(key) != digest:
                        changed.append(item)
                    seen[key] = digest
                first = data["last_success"] is None
                status = "baseline" if first else "changed" if changed else "unchanged"
                summary = (f"First check: {len(evidence)} sources to review." if first else
                           f"{len(changed)} new or changed sources to review." if changed else "No new evidence in this check.")
                data.update(evidence=evidence, seen=seen, summary=summary, last_error=None, last_success=checked_at,
                            unread=data["unread"] or bool(changed), next_check=stamp(self.clock() + timedelta(days=data["interval_days"])))
            self._history(db, data["id"], dict(checked_at=checked_at, status=status, summary=data["summary"],
                                              error=error, evidence=evidence, query=data["query"], sources=data["sources"]))
            self._write(db, data)
        return True

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        def loop():
            while not self.stop_event.is_set():
                try:
                    worked = self.tick()
                    self.scheduler_error = None
                except Exception as exc:
                    self.scheduler_error = f"Watchlist storage check failed ({type(exc).__name__}). Retrying."
                    worked = False  # Retry storage errors without losing the durable queue.
                if not worked:
                    self.wake.wait(30)
                    self.wake.clear()
        self.thread = threading.Thread(target=loop, name="watchlist", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=2)
        # A normal core restart need not wait out an interrupted worker's lease.
        with self.db() as db:
            db.execute("UPDATE watches SET token=NULL,lease_until=NULL WHERE token LIKE ?", (self.owner + ":%",))
