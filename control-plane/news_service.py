from news_destinations import reference as destination_reference
"""Independent News Service registry and publication cache. No background jobs."""
import json
import html as html_module
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from html.parser import HTMLParser

DESTINATIONS = {'thailand_news', 'ai_noise'}
CHANNEL = re.compile(r'UC[A-Za-z0-9_-]{22}')
AVATAR_HOSTS = {'yt3.googleusercontent.com', 'yt3.ggpht.com'}


class ChannelAvatarParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.url = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() != 'meta':
            return
        fields = dict(attrs)
        if fields.get('property', '').lower() != 'og:image':
            return
        value = fields.get('content') or ''
        try:
            u = urllib.parse.urlsplit(value)
            if (u.scheme == 'https' and u.hostname in AVATAR_HOSTS and not u.username
                    and not u.password and not u.port and len(value) <= 2048):
                self.url = value
        except ValueError:
            pass


def resolve_channel(value):
    value = str(value or '').strip()
    if value.startswith('@'):
        value = 'https://www.youtube.com/' + value
    u = urllib.parse.urlsplit(value)
    if (u.scheme != 'https' or u.hostname not in {'youtube.com', 'www.youtube.com', 'm.youtube.com'}
            or u.username or u.password or u.port or u.query or u.fragment):
        raise ValueError('Enter a YouTube channel URL or @handle.')
    path = u.path.rstrip('/')
    if not (re.fullmatch(r'/@[A-Za-z0-9_.-]{3,100}', path) or re.fullmatch(r'/channel/UC[A-Za-z0-9_-]{22}', path)):
        raise ValueError('Use a channel URL or @handle, not a video or playlist.')
    # Request only a fixed YouTube host; do not follow redirects to other hosts.
    class SafeRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            target = urllib.parse.urlsplit(newurl)
            if target.scheme != 'https' or target.hostname not in {'youtube.com', 'www.youtube.com', 'm.youtube.com'}:
                raise ValueError('YouTube redirected to an unexpected host.')
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    request = urllib.request.Request('https://www.youtube.com' + path, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.build_opener(SafeRedirect).open(request, timeout=12) as response:
            html = response.read(2_000_001)
        text = html.decode('utf-8', errors='replace')
        matches = re.findall(r'"externalId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"', text)
        canonical = re.search(r'<link[^>]+rel="canonical"[^>]+href="https://www\.youtube\.com/channel/(UC[A-Za-z0-9_-]{22})"', text)
        channel_id = canonical.group(1) if canonical else matches[0] if matches and len(html) <= 2_000_000 else ''
        if not CHANNEL.fullmatch(channel_id) or (path.startswith('/channel/') and channel_id != path[9:]):
            raise ValueError('The channel could not be verified. Check the link and try again.')
        title = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)', text)
        vanity = re.search(r'"vanityChannelUrl"\s*:\s*"https?://(?:www\.)?youtube\.com/(@[A-Za-z0-9_.-]{3,100})"', text, re.I)
        handle = vanity.group(1) if vanity else path[1:] if path.startswith('/@') else None
        parser = ChannelAvatarParser()
        parser.feed(text[:min(len(text), 2_000_000)])
        return channel_id, html_module.unescape(title.group(1)) if title else channel_id, handle, parser.url
    except (OSError, urllib.error.URLError) as exc:
        raise ValueError('YouTube could not be reached to verify this channel.') from exc


class NewsService:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
          url TEXT NOT NULL, destination TEXT NOT NULL CHECK(destination IN ('thailand_news','ai_noise')),
          enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)), created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS publications (id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
          original_url TEXT NOT NULL, creator TEXT NOT NULL, title TEXT NOT NULL, published_at TEXT NOT NULL,
          summary TEXT NOT NULL DEFAULT '', image_url TEXT NOT NULL DEFAULT '', destination TEXT NOT NULL,
          collected_at TEXT NOT NULL, discord_status TEXT NOT NULL DEFAULT 'pending',
          external_status TEXT NOT NULL DEFAULT 'not_published', UNIQUE(source_id, original_url));
        CREATE TABLE IF NOT EXISTS news_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS publication_archive AS SELECT * FROM publications WHERE 0;
        CREATE UNIQUE INDEX IF NOT EXISTS publication_archive_id ON publication_archive(id);
        CREATE INDEX IF NOT EXISTS publications_date ON publications(published_at DESC);
        ''')
        try:
            # Serialize introspection and ALTER so concurrent first requests cannot race.
            db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(sources)')}
            for column in ('handle', 'friendly_name', 'thumbnail_url'):
                if column not in columns:
                    db.execute(f'ALTER TABLE sources ADD COLUMN {column} TEXT')
            db.commit()
            with db:
                yield db
        finally:
            db.close()

    def sources(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM sources ORDER BY name COLLATE NOCASE')]

    def add(self, url, destination):
        if not destination_reference(self.path, destination):
            raise ValueError('Choose a destination.')
        channel_id, name, handle, thumbnail_url = resolve_channel(url)
        with self.connect() as db:
            try:
                db.execute('INSERT INTO sources (id,kind,name,url,destination,enabled,created_at,handle,thumbnail_url) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)',
                    (channel_id, 'youtube', name, 'https://www.youtube.com/channel/' + channel_id,
                     destination, datetime.now(timezone.utc).isoformat(), handle, thumbnail_url))
            except sqlite3.IntegrityError as exc:
                raise ValueError('This channel is already in the source list.') from exc
        return channel_id

    def update(self, channel_id, action, destination=None, friendly_name=None):
        if not CHANNEL.fullmatch(str(channel_id or '')):
            raise ValueError('Invalid source identifier.')
        with self.connect() as db:
            if action == 'destination' and destination_reference(self.path, destination):
                cur = db.execute('UPDATE sources SET destination=? WHERE id=?', (destination, channel_id))
            elif action in ('enable', 'disable'):
                cur = db.execute('UPDATE sources SET enabled=? WHERE id=?', (int(action == 'enable'), channel_id))
            elif action == 'remove':
                cur = db.execute('DELETE FROM sources WHERE id=?', (channel_id,))
            elif action == 'friendly_name':
                if not isinstance(friendly_name, str) or len(friendly_name.strip()) > 200:
                    raise ValueError('Friendly name must be text up to 200 characters.')
                cur = db.execute('UPDATE sources SET friendly_name=? WHERE id=?', (friendly_name.strip() or None, channel_id))
            else:
                raise ValueError('Invalid source action.')
            if cur.rowcount != 1:
                raise ValueError('Source not found.')

    def refresh_identity(self, channel_id):
        if not CHANNEL.fullmatch(str(channel_id or '')):
            raise ValueError('Invalid source identifier.')
        with self.connect() as db:
            source = db.execute('SELECT id,handle FROM sources WHERE id=? AND kind=?', (channel_id, 'youtube')).fetchone()
        if not source:
            raise ValueError('Source not found.')
        # A stale handle can fail or now refer to a different channel. Only the ID
        # establishes identity; fall back to the canonical URL in either case.
        try:
            if not source['handle']:
                raise ValueError('No known handle.')
            result = resolve_channel(source['handle'])
            if result[0] != channel_id:
                raise ValueError('Handle no longer refers to this channel.')
        except ValueError:
            result = resolve_channel('https://www.youtube.com/channel/' + channel_id)
        if result[0] != channel_id:
            raise ValueError('YouTube returned a different channel ID; no changes made.')
        with self.connect() as db:
            # A failed refresh never changes the stored source or marks it deleted.
            cur = db.execute('UPDATE sources SET name=?, handle=COALESCE(?,handle), thumbnail_url=COALESCE(?,thumbnail_url) WHERE id=?', (result[1], result[2], result[3], channel_id))
            if cur.rowcount != 1:
                raise ValueError('Source not found.')

    def backfill_missing_thumbnails(self):
        """One-time, restartable metadata backfill; never change source identity or routing."""
        pending = [row['id'] for row in self.sources() if row['kind'] == 'youtube' and not row['thumbnail_url']]
        result = {'checked': len(pending), 'updated': 0, 'unavailable': 0}
        for channel_id in pending:
            try:
                resolved = resolve_channel('https://www.youtube.com/channel/' + channel_id)
                if resolved[0] != channel_id or not resolved[3]:
                    result['unavailable'] += 1
                    continue
            except ValueError:
                result['unavailable'] += 1
                continue
            with self.connect() as db:
                cur = db.execute("UPDATE sources SET thumbnail_url=? WHERE id=? AND kind='youtube' AND (thumbnail_url IS NULL OR thumbnail_url='')", (resolved[3], channel_id))
                result['updated'] += cur.rowcount
        return result

    def publications(self, destination='all'):
        if destination != 'all' and not destination_reference(self.path, destination):
            raise ValueError('Invalid category.')
        with self.connect() as db:
            if destination == 'all':
                rows = db.execute('SELECT * FROM publications ORDER BY julianday(published_at) DESC, id DESC LIMIT 500')
            else:
                rows = db.execute('SELECT * FROM publications WHERE destination=? ORDER BY julianday(published_at) DESC, id DESC LIMIT 500', (destination,))
            return [dict(row) for row in rows]

    def cache_publication(self, item):
        """Store a validated publication. This is not exposed over HTTP in Stage 1."""
        if not isinstance(item, dict):
            raise ValueError('Publication must be an object.')
        fields = ('id', 'source_id', 'original_url', 'creator', 'title', 'published_at')
        if any(not isinstance(item.get(k), str) or not item[k].strip() or len(item[k]) > 2048 for k in fields):
            raise ValueError('Publication is missing required fields.')
        for field in ('original_url', 'image_url'):
            value = item.get(field, '')
            if not isinstance(value, str) or len(value) > 2048:
                raise ValueError('Invalid publication URL.')
            if value:
                parsed = urllib.parse.urlsplit(value)
                if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError('Publication URLs must be HTTPS.')
        try:
            published = datetime.fromisoformat(item['published_at'].replace('Z', '+00:00'))
            if published.tzinfo is None:
                raise ValueError()
        except ValueError as exc:
            raise ValueError('Publication timestamp must include a timezone.') from exc
        summary = item.get('summary', '')
        if not isinstance(summary, str) or len(summary) > 5000:
            raise ValueError('Invalid summary.')
        with self.connect() as db:
            source = db.execute('SELECT destination, enabled FROM sources WHERE id=?', (item['source_id'],)).fetchone()
            if not source or not source['enabled']:
                raise ValueError('Source must exist and be enabled.')
            try:
                db.execute('''INSERT INTO publications
                  (id,source_id,original_url,creator,title,published_at,summary,image_url,destination,collected_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?)''', (item['id'], item['source_id'], item['original_url'],
                  item['creator'], item['title'], published.astimezone(timezone.utc).isoformat(), summary,
                  item.get('image_url', ''), source['destination'], datetime.now(timezone.utc).isoformat()))
            except sqlite3.IntegrityError:
                return False
        return True

    def prune_publications(self, limit=2):
        """Keep the newest rows active; archive full rows before removing older ones."""
        if limit != 2:
            raise ValueError('Only the two-publication window is supported.')
        with self.connect() as db:
            old = db.execute('''SELECT p.* FROM publications p WHERE p.id IN (
                SELECT id FROM (SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY source_id ORDER BY julianday(published_at) DESC, id DESC) AS position
                    FROM publications) WHERE position > 2)''').fetchall()
            live = {r['name'] for r in db.execute('PRAGMA table_info(publications)')}
            archived = {r['name'] for r in db.execute('PRAGMA table_info(publication_archive)')}
            common = [r['name'] for r in db.execute('PRAGMA table_info(publications)') if r['name'] in archived]
            for column in sorted(live - archived):
                kind = next(r['type'] for r in db.execute('PRAGMA table_info(publications)') if r['name'] == column)
                db.execute(f'ALTER TABLE publication_archive ADD COLUMN {column} {kind}')
                common.append(column)
            fields = ','.join('"' + name.replace('"', '""') + '"' for name in common)
            for row in old:
                db.execute(f'INSERT OR IGNORE INTO publication_archive ({fields}) SELECT {fields} FROM publications WHERE id=?', (row['id'],))
                db.execute('DELETE FROM publications WHERE id=?', (row['id'],))
            return len(old)
