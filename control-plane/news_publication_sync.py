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
    """Return enabled canonical channels with their existing baseline checkpoints."""
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute('''SELECT s.id,s.name,s.friendly_name,s.destination,
            c.video_id,c.published_at FROM sources s LEFT JOIN news_checkpoints c
            ON c.source_id=s.id WHERE s.enabled=1 AND s.kind='youtube' ORDER BY s.id''')
        return {'version': 1, 'sources': [dict(row) for row in rows]}
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
    result = {'inserted': 0, 'duplicates': 0, 'baseline_skipped': 0}
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
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
