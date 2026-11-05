"""Disabled Ariadne automatic sender.

Stage 4 assigns unattended Discord delivery to the existing n8n collector.
The module remains as a compatibility shim so older local integrations fail
closed instead of creating a second publisher.
"""


def deliver_sync(database, inserted_ids, *, store_factory=None, dry_run=False):
    return {
        'eligible': 0,
        'sent': 0,
        'failed': 0,
        'dry_run': bool(dry_run),
        'disabled': True,
    }
