"""Small append-only JSONL event stream for Librarian observability."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class LibrarianEventStream:
    """Write bounded structured events without making logging a request dependency."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def emit(self, event_type: str, *, request_id: str | None = None, session_id: str | None = None,
             model: str | None = None, latency_ms: int | float | None = None,
             data: dict[str, Any] | None = None) -> None:
        event: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "event_type": str(event_type).upper(),
        }
        if request_id:
            event["request_id"] = request_id
        if session_id:
            event["session_id"] = session_id
        if model:
            event["model"] = model
        if latency_ms is not None:
            event["latency_ms"] = round(float(latency_ms), 1)
        if data:
            event["data"] = data
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            # Observability must never break the Home request path.
            return

    def read_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        result: list[dict[str, Any]] = []
        for line in reversed(lines):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                result.append(value)
            if len(result) >= max(1, int(limit)):
                break
        return result


__all__ = ["LibrarianEventStream"]
