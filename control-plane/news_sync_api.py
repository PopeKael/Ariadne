"""Narrow authenticated n8n bridge for the existing News Service cache."""
import hmac
import sqlite3
import tempfile
from contextlib import closing
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
    enabled = {source["id"] for source in registry["sources"]}
    items = snapshot["publications"]
    active = [item for item in items if not isinstance(item, dict)
              or not isinstance(item.get("source_id"), str)
              or item["source_id"] in enabled]
    result = import_snapshot(db_path, {
        "version": snapshot.get("version"), "publications": active,
    })
    from news_discord_auto import deliver_sync
    delivery = deliver_sync(db_path, result.pop('inserted_ids', []), dry_run=dry_run)
    return {"ok": True, "dry_run": dry_run, **result,
            "inactive_sources_skipped": len(items) - len(active), "discord_delivery": delivery}


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
