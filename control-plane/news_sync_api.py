"""Narrow authenticated n8n bridge for the existing News Service cache."""
import hmac
import hashlib
import math
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import credential_store
from news_publication_sync import import_snapshot, registry_snapshot
from news_baseline import baseline

TOKEN_REFERENCE = "ARIADNE_NEWS_N8N_SYNC_TOKEN"
MAX_BODY_BYTES = 5_000_000


def _authorized(handler):
    expected = credential_store.get_credential(TOKEN_REFERENCE, allow_environment=False)
    supplied = handler.headers.get("Authorization", "")
    if not expected or not supplied.startswith("Bearer "):
        return False
    return hmac.compare_digest(supplied[7:], expected)


def _send(handler, payload, status=200):
    handler.send_json(payload, status)


def _apply_snapshot(db_path, snapshot, dry_run):
    # Source state is authoritative at ingest time. Drop records for sources
    # disabled or removed after collection, then let the transactional
    # importer revalidate the remaining records and preserve delivery state.
    registry = registry_snapshot(db_path)
    enabled = {source["id"] for source in registry["sources"] if source.get("enabled", True)}
    items = snapshot["publications"]
    active = [item for item in items if not isinstance(item, dict)
              or not isinstance(item.get("source_id"), str)
              or item["source_id"] in enabled]
    result = import_snapshot(db_path, {
        "version": snapshot.get("version"), "publications": active,
    })
    result.pop('inserted_ids', None)
    reconciled = reconcile_delivery_history(db_path, snapshot.get('delivery_history', []))
    return {"ok": True, "dry_run": dry_run, **result,
            "inactive_sources_skipped": len(items) - len(active),
            "delivery_history_reconciled": reconciled}


def reconcile_delivery_history(db_path, records):
    """Idempotently import n8n delivery receipts without sending anything."""
    if not isinstance(records, list) or len(records) > 5000:
        raise ValueError('Invalid delivery history')
    allowed = {'inflight', 'uncertain', 'sent', 'rate_limited', 'rejected', 'pending'}
    validated = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('Invalid delivery record')
        video_id = record.get('video_id')
        destination = record.get('destination')
        status = record.get('status')
        delivery_id = record.get('delivery_id')
        if (not isinstance(video_id, str) or len(video_id) != 11
                or any(ch not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-' for ch in video_id)
                or not isinstance(destination, str) or not destination or len(destination) > 128
                or any(ch not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-' for ch in destination)
                or not isinstance(delivery_id, str) or not delivery_id or len(delivery_id) > 256
                or not isinstance(status, str) or status not in allowed):
            raise ValueError('Invalid delivery record')
        if delivery_id in seen:
            continue
        seen.add(delivery_id)
        stamp = record.get('started_at')
        if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
            started = float(stamp)
        elif isinstance(stamp, str):
            parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError('Delivery timestamp requires a timezone')
            started = parsed.astimezone(timezone.utc).timestamp()
        else:
            raise ValueError('Invalid delivery timestamp')
        if not math.isfinite(started):
            raise ValueError('Invalid delivery timestamp')
        finished = record.get('finished_at')
        if isinstance(finished, str):
            parsed = datetime.fromisoformat(finished.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError('Delivery timestamp requires a timezone')
            finished = parsed.astimezone(timezone.utc).timestamp()
        elif finished is not None and (isinstance(finished, bool) or not isinstance(finished, (int, float))):
            raise ValueError('Invalid delivery timestamp')
        if finished is not None and not math.isfinite(float(finished)):
            raise ValueError('Invalid delivery timestamp')
        http_status = record.get('http_status')
        if http_status is not None and (isinstance(http_status, bool) or not isinstance(http_status, int) or not 100 <= http_status <= 599):
            raise ValueError('Invalid delivery response status')
        message_id = record.get('discord_message_id')
        if message_id is not None and (not isinstance(message_id, str) or not message_id.isdigit() or len(message_id) > 25):
            raise ValueError('Invalid Discord message identifier')
        channel_id = record.get('discord_channel_id')
        if channel_id is not None and (not isinstance(channel_id, str) or not channel_id.isdigit() or len(channel_id) > 25):
            raise ValueError('Invalid Discord channel identifier')
        retry_after = record.get('retry_after')
        if retry_after is not None and (isinstance(retry_after, bool) or not isinstance(retry_after, (int, float)) or not math.isfinite(retry_after)):
            raise ValueError('Invalid delivery retry timestamp')
        discord_timestamp = record.get('discord_timestamp')
        if discord_timestamp is not None and (not isinstance(discord_timestamp, str) or len(discord_timestamp) > 64):
            raise ValueError('Invalid Discord timestamp')
        # A crashed n8n execution must not turn an unresolved send into a retry.
        imported_status = 'uncertain' if status == 'inflight' else status
        stable_id = hashlib.sha256(('n8n:' + delivery_id).encode('utf-8')).hexdigest()
        validated.append((stable_id, 'youtube:' + video_id, destination, started,
                          finished, imported_status, message_id,
                          retry_after, http_status, channel_id, discord_timestamp))
    if not validated:
        return 0
    db = sqlite3.connect(db_path, timeout=10)
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''CREATE TABLE IF NOT EXISTS discord_delivery_attempts (
            id TEXT PRIMARY KEY, publication_id TEXT NOT NULL, destination TEXT NOT NULL,
            started_at REAL NOT NULL, finished_at REAL, status TEXT NOT NULL,
            discord_message_id TEXT, retry_after REAL)''')
        columns = {row[1] for row in db.execute('PRAGMA table_info(discord_delivery_attempts)')}
        for name, sql_type in (('http_status', 'INTEGER'), ('discord_channel_id', 'TEXT'),
                               ('discord_timestamp', 'TEXT')):
            if name not in columns:
                db.execute(f'ALTER TABLE discord_delivery_attempts ADD COLUMN {name} {sql_type}')
        added = 0
        for receipt in validated:
            stable_id, publication_id, destination, started, finished, status, message_id, retry_after, http_status, channel_id, discord_timestamp = receipt
            prior = db.execute('SELECT status FROM discord_delivery_attempts WHERE id=?', (stable_id,)).fetchone()
            if not prior:
                db.execute('''INSERT INTO discord_delivery_attempts
                    (id,publication_id,destination,started_at,finished_at,status,discord_message_id,
                     retry_after,http_status,discord_channel_id,discord_timestamp)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)''', receipt)
                added += 1
            elif prior[0] != 'sent' and status == 'sent':
                # The same stable attempt may move from unresolved/inflight to
                # confirmed success when the n8n execution posts its receipt.
                db.execute('''UPDATE discord_delivery_attempts SET finished_at=?,status='sent',
                    discord_message_id=?,retry_after=NULL,http_status=?,discord_channel_id=?,
                    discord_timestamp=? WHERE id=? AND status!='sent' ''',
                    (finished, message_id, http_status, channel_id, discord_timestamp, stable_id))
                added += db.execute('SELECT changes()').fetchone()[0]
            elif prior[0] in ('pending', 'inflight') and status in ('uncertain', 'rate_limited', 'rejected'):
                db.execute('''UPDATE discord_delivery_attempts SET finished_at=?,status=?,
                    retry_after=?,http_status=? WHERE id=? AND status IN ('pending','inflight')''',
                    (finished, status, retry_after, http_status, stable_id))
                added += db.execute('SELECT changes()').fetchone()[0]
        # Mirror a sent receipt onto a publication if the row is still in the
        # active cache or archive. The attempt table remains the durable record.
        for row in validated:
            stable_id, publication_id, *_ = row
            receipt = db.execute('SELECT status FROM discord_delivery_attempts WHERE id=?', (stable_id,)).fetchone()
            if receipt and receipt[0] == 'sent':
                db.execute("UPDATE publications SET discord_status='sent' WHERE id=?", (publication_id,))
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='publication_archive'").fetchone():
                    db.execute("UPDATE publication_archive SET discord_status='sent' WHERE id=?", (publication_id,))
        db.commit()
        return added
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def handle_get(handler, path, db_path):
    if path != "/api/news-service/n8n/registry":
        return False
    if not _authorized(handler):
        _send(handler, {"ok": False, "message": "Unauthorized."}, 401)
        return True
    try:
        # Give newly enabled channels the existing one-time baseline before
        # exposing their checkpoints to n8n, preventing old backlog imports.
        baseline(db_path)
        _send(handler, {"ok": True, **registry_snapshot(db_path)})
    except (OSError, ValueError):
        _send(handler, {"ok": False, "message": "News Service registry is unavailable."}, 503)
    return True


def handle_post(handler, path, db_path):
    if path != "/api/news-service/n8n/publications":
        return False
    if not _authorized(handler):
        _send(handler, {"ok": False, "message": "Unauthorized."}, 401)
        return True
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("Snapshot exceeds the size limit.")
        if "application/json" not in handler.headers.get("Content-Type", "").lower():
            raise ValueError("Expected an application/json request.")
        snapshot = handler.read_json()
        if not isinstance(snapshot.get("publications"), list):
            raise ValueError("Invalid publication list.")

        dry_run = handler.headers.get("X-Ariadne-Dry-Run", "").strip().casefold() == "true"
        if not dry_run:
            _send(handler, _apply_snapshot(db_path, snapshot, False))
        else:
            # Exercise the exact importer and eligibility logic against a
            # consistent copy, without changing the live cache or calling Discord.
            with tempfile.TemporaryDirectory(prefix="ariadne-news-sync-dry-run-") as temporary:
                target_db = Path(temporary) / "news-service.sqlite3"
                with closing(sqlite3.connect(db_path, timeout=10)) as source, closing(sqlite3.connect(target_db, timeout=10)) as copy:
                    source.backup(copy)
                _send(handler, _apply_snapshot(target_db, snapshot, True))
    except ValueError as exc:
        _send(handler, {"ok": False, "message": str(exc)}, 400)
    except Exception:
        # Keep filesystem/SQLite/parser details out of the public response.
        _send(handler, {"ok": False, "message": "News Service sync is unavailable."}, 503)
    return True
