"""Automatic, post-activation delivery for newly synchronized publications."""
import sqlite3
from datetime import datetime, timezone


def _stamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Publication timestamp requires a timezone')
    return parsed.astimezone(timezone.utc)


def deliver_sync(database, inserted_ids, *, store_factory=None, dry_run=False):
    """Publish new rows and safely retry rate limits, at most three attempts.

    Transport ambiguity is left to DeliveryStore's inflight/uncertain guard;
    only explicit Discord 429 responses are retried on a later sync cycle.
    """
    if store_factory is None:
        from news_discord_local import format_publication, discord_transport
        import credential_store
        from news_discord_delivery import DeliveryStore
        from news_destinations import reference

        def credentials(destination):
            return credential_store.get_credential(reference(database, destination), allow_environment=False)

        store_factory = lambda: DeliveryStore(database, lambda _principal, _csrf: True,
            format_publication, credentials, discord_transport)

    db = sqlite3.connect(database, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        settings = dict(db.execute('SELECT key,value FROM news_settings'))
        activated = settings.get('stage3_activated_at')
        if not activated:
            return {'eligible': 0, 'sent': 0, 'failed': 0, 'dry_run': dry_run}
        activated = _stamp(activated)
        candidates = set(inserted_ids)
        # Pending work is retried if credentials become available; only
        # definitely rejected/rate-limited attempts are retried. Inflight and
        # uncertain outcomes are deliberately never retried automatically.
        for table in ('publications', 'publication_archive'):
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                candidates.update(row[0] for row in db.execute(
                    f"SELECT id FROM {table} WHERE discord_status IN ('pending','rate_limited','rejected')"))
        work = []
        for publication_id in sorted(candidates):
            row = db.execute('SELECT * FROM publications WHERE id=?', (publication_id,)).fetchone()
            table = 'publications'
            if not row:
                row = db.execute('SELECT * FROM publication_archive WHERE id=?', (publication_id,)).fetchone()
                table = 'publication_archive'
            if not row or _stamp(row['published_at']) <= activated:
                continue
            source = db.execute('SELECT destination,enabled FROM sources WHERE id=?', (row['source_id'],)).fetchone()
            if not source or not source['enabled']:
                continue
            destination = source['destination']
            if row['discord_status'] == 'sent':
                continue
            attempts = db.execute('''SELECT count(*) FROM discord_delivery_attempts
                WHERE publication_id=? AND destination=?''', (publication_id, destination)).fetchone()[0] if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='discord_delivery_attempts'").fetchone() else 0
            previous = db.execute("SELECT status FROM discord_delivery_attempts WHERE publication_id=? AND destination=? ORDER BY started_at DESC,rowid DESC LIMIT 1",
                (publication_id, destination)).fetchone() if attempts else None
            newly_inserted = publication_id in inserted_ids
            if previous and previous['status'] != 'rate_limited':
                continue
            if attempts >= 3 or (not newly_inserted and row['discord_status'] not in ('pending','rate_limited','rejected')):
                continue
            work.append((publication_id, table, destination))
        result = {'eligible': len(work), 'sent': 0, 'failed': 0, 'dry_run': dry_run}
        for publication_id, table, destination in work:
            if dry_run:
                continue
            db.execute(f'UPDATE {table} SET destination=? WHERE id=?', (destination, publication_id))
            db.commit()
            try:
                outcome = store_factory().send(publication_id, 'automatic-sync', 'internal', confirmed=True)
                if outcome['status'] == 'sent':
                    result['sent'] += 1
                else:
                    result['failed'] += 1
            except Exception:
                # Preserve pending status if no attempt was made; prior
                # uncertain/inflight records remain protected by the store.
                result['failed'] += 1
        return result
    finally:
        db.close()
