"""Manual News Service registry export and transactional publication import.

This module never polls or opens an HTTP endpoint. Its manual pull uses public
metadata over LAN HTTP and requires a separately verified LAN-only endpoint.
"""
import re
import sqlite3
import json
import ipaddress
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

CHANNEL = re.compile(r'UC[A-Za-z0-9_-]{22}\Z')
VIDEO = re.compile(r'[A-Za-z0-9_-]{11}\Z')
MAX_SNAPSHOT_BYTES = 5_000_000


def pull_snapshot(path, url, timeout=5, *, headers=None, enabled_source_ids=None):
    """Manually pull a bounded public snapshot from a private IPv4 address.

    This does not authenticate the server or provide encryption. The caller
    must establish that the endpoint itself is restricted to the private LAN.
    """
    try:
        parsed = urlsplit(url)
        address = ipaddress.IPv4Address(parsed.hostname or '')
        port = parsed.port
    except (ValueError, TypeError) as exc:
        raise ValueError('Expected a private IPv4 snapshot URL') from exc
    private = any(address in ipaddress.IPv4Network(net) for net in
                  ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
    if (parsed.scheme != 'http' or not private or not port or parsed.username or parsed.password
            or parsed.query or parsed.fragment or not parsed.path.startswith('/')):
        raise ValueError('Expected a private IPv4 snapshot URL')

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError('Snapshot redirects are not allowed')

    request_headers = {'Accept': 'application/json'}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers, method='GET')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(MAX_SNAPSHOT_BYTES + 1)
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise ValueError('Publication snapshot exceeds the size limit')
    snapshot = json.loads(raw.decode('utf-8'))
    inactive_sources_skipped = 0
    if enabled_source_ids is not None and isinstance(snapshot, dict):
        items = snapshot.get('publications')
        if isinstance(items, list):
            inactive_sources_skipped = sum(
                1 for item in items if not isinstance(item, dict)
                or item.get('source_id') not in enabled_source_ids
            )
            snapshot = {
                **snapshot,
                'publications': [item for item in items if isinstance(item, dict)
                                 and item.get('source_id') in enabled_source_ids],
            }
    result = import_snapshot(path, snapshot)
    if enabled_source_ids is not None:
        result['inactive_sources_skipped'] = inactive_sources_skipped
    return result


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError('Invalid timestamp')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Timestamp requires a timezone')
    return stamp.astimezone(timezone.utc)


def registry_snapshot(path):
    """Return canonical channel state and checkpoints for the n8n last-known mirror."""
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute('CREATE TABLE IF NOT EXISTS news_settings (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute("INSERT OR IGNORE INTO news_settings(key,value) VALUES('stage3_activated_at',?)",
                   (datetime.now(timezone.utc).isoformat(),))
        db.commit()
        rows = db.execute('''SELECT s.id,s.name,s.friendly_name,s.destination,s.enabled,
            c.video_id,c.published_at FROM sources s LEFT JOIN news_checkpoints c
            ON c.source_id=s.id WHERE s.kind='youtube' ORDER BY s.id''')
        sources = [dict(row) for row in rows]
        channel_ids = {}
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='discord_delivery_attempts'").fetchone():
            columns = {row['name'] for row in db.execute('PRAGMA table_info(discord_delivery_attempts)')}
            if 'discord_channel_id' in columns:
                for row in db.execute('''SELECT destination,discord_channel_id FROM discord_delivery_attempts
                    WHERE status='sent' AND discord_channel_id IS NOT NULL
                    ORDER BY started_at DESC,rowid DESC'''):
                    channel_ids.setdefault(row['destination'], row['discord_channel_id'])
        for source in sources:
            source['enabled'] = bool(source['enabled'])
            source['discord_channel_id'] = channel_ids.get(source['destination'])
        activated = db.execute("SELECT value FROM news_settings WHERE key='stage3_activated_at'").fetchone()
        return {'version': 1, 'stage3_activated_at': activated['value'] if activated else None,
                'sources': sources}
    finally:
        db.close()


def import_snapshot(path, snapshot):
    """Insert post-baseline records without changing prior delivery state.

    All input is validated before the transaction. Missing checkpoints fail
    closed; timestamps at or before the checkpoint are never imported as new.
    """
    if not isinstance(snapshot, dict) or snapshot.get('version') != 1:
        raise ValueError('Unsupported snapshot version')
    items = snapshot.get('publications')
    if not isinstance(items, list) or len(items) > 5000:
        raise ValueError('Invalid publication list')
    validated = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Invalid publication')
        cid, vid = item.get('source_id'), item.get('video_id')
        if not isinstance(cid, str) or not CHANNEL.fullmatch(cid) or not isinstance(vid, str) or not VIDEO.fullmatch(vid):
            raise ValueError('Invalid canonical identity')
        if (cid, vid) in seen:
            raise ValueError('Duplicate identity in snapshot')
        seen.add((cid, vid))
        title, summary = item.get('title'), item.get('summary', '')
        if not isinstance(title, str) or not title.strip() or len(title) > 2048:
            raise ValueError('Invalid title')
        if not isinstance(summary, str) or len(summary) > 5000:
            raise ValueError('Invalid summary')
        stamp = _timestamp(item.get('published_at'))
        if stamp > datetime.now(timezone.utc):
            raise ValueError('Publication is in the future')
        validated.append((cid, vid, title.strip(), summary, stamp))
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    result = {'inserted': 0, 'duplicates': 0, 'baseline_skipped': 0, 'inserted_ids': []}
    try:
        db.execute('BEGIN IMMEDIATE')
        now = datetime.now(timezone.utc).isoformat()
        for cid, vid, title, summary, stamp in validated:
            source = db.execute('''SELECT s.name,s.friendly_name,s.destination,c.video_id,c.published_at
                FROM sources s LEFT JOIN news_checkpoints c ON c.source_id=s.id
                WHERE s.id=? AND s.kind='youtube' AND s.enabled=1''', (cid,)).fetchone()
            if not source or not source['video_id']:
                raise ValueError('Enabled source with baseline required')
            identity = 'youtube:' + vid
            url = 'https://www.youtube.com/watch?v=' + vid
            prior = db.execute('SELECT source_id,original_url FROM publications WHERE id=?', (identity,)).fetchone()
            if not prior and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='publication_archive'").fetchone():
                prior = db.execute('SELECT source_id,original_url FROM publication_archive WHERE id=?', (identity,)).fetchone()
            if prior:
                if prior['source_id'] != cid or prior['original_url'] != url:
                    raise ValueError('Publication identity conflict')
                result['duplicates'] += 1
                continue
            if stamp <= _timestamp(source['published_at']):
                result['baseline_skipped'] += 1
                continue
            db.execute('''INSERT INTO publications
                (id,source_id,original_url,creator,title,published_at,summary,image_url,destination,collected_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)''', (identity,cid,url,source['friendly_name'] or source['name'],
                title,stamp.isoformat(),summary,'https://i.ytimg.com/vi/'+vid+'/hqdefault.jpg',source['destination'],now))
            result['inserted'] += 1
            result['inserted_ids'].append(identity)
        # Keep a two-row active window per canonical channel. Archive every
        # evicted row first so delivery history and recovery data remain intact.
        db.execute('CREATE TABLE IF NOT EXISTS publication_archive AS SELECT * FROM publications WHERE 0')
        live_columns = [row['name'] for row in db.execute('PRAGMA table_info(publications)')]
        archive_columns = {row['name'] for row in db.execute('PRAGMA table_info(publication_archive)')}
        for name in live_columns:
            if name not in archive_columns:
                declared = next(row['type'] for row in db.execute('PRAGMA table_info(publications)') if row['name'] == name)
                db.execute(f'ALTER TABLE publication_archive ADD COLUMN "{name.replace(chr(34), chr(34)*2)}" {declared}')
        fields = ','.join('"' + name.replace('"', '""') + '"' for name in live_columns)
        evicted = db.execute('''SELECT id FROM (SELECT id,ROW_NUMBER() OVER (
            PARTITION BY source_id ORDER BY julianday(published_at) DESC,id DESC) AS position
            FROM publications) WHERE position>2''').fetchall()
        for row in evicted:
            db.execute(f'INSERT OR IGNORE INTO publication_archive ({fields}) SELECT {fields} FROM publications WHERE id=?', (row['id'],))
            db.execute('DELETE FROM publications WHERE id=?', (row['id'],))
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
