"""Low-impact stdout diagnostics for the containerized Signal Service."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def emit_diagnostic(event: str, **data: Any) -> None:
    """Emit one timestamped JSON event without affecting service behaviour."""
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": event,
        **data,
    }
    try:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)
    except OSError:
        # Diagnostics must never interrupt the service or a refresh worker.
        return


__all__ = ["emit_diagnostic"]
