"""Asynchronous host-facing activity events for Ariadne plugins.

Plugins report work here; Ariadne Core decides how those events are rendered to
the user.  This module never selects avatar images, animation names, or speech.
"""
from __future__ import annotations

import json
import queue
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ACTIVITY_STATES = frozenset({"started", "running", "progress", "stage", "completed", "warning", "failed", "cancelled"})
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
MAX_STATUS_TEXT = 500
MAX_RECENT_EVENTS = 100


class ActivityValidationError(ValueError):
    """Raised when a plugin activity event is not safe for the host boundary."""


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActivityValidationError(f"{field} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class PluginActivityEvent:
    activity_id: str
    plugin_id: str
    capability_id: str
    state: str
    status_text: str
    progress: float | None = None
    stage: str | None = None
    event_id: str = ""
    timestamp: str = ""

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "activity_id": self.activity_id,
            "plugin_id": self.plugin_id,
            "capability_id": self.capability_id,
            "state": self.state,
            "status_text": self.status_text,
        }
        if self.progress is not None:
            value["progress"] = self.progress
        if self.stage:
            value["stage"] = self.stage
        return value


class PluginActivityStream:
    """Non-blocking JSONL activity sink with a small in-memory status window."""

    def __init__(self, path: Path, *, queue_size: int = 512):
        self.path = Path(path)
        self._queue: queue.Queue[PluginActivityEvent | None] = queue.Queue(maxsize=max(8, int(queue_size)))
        self._recent: deque[PluginActivityEvent] = deque(maxlen=MAX_RECENT_EVENTS)
        self._lock = threading.Lock()
        self._closed = False
        self.dropped_events = 0
        self._worker = threading.Thread(target=self._write_loop, name="ariadne-plugin-activity", daemon=True)
        self._worker.start()

    def _write_loop(self) -> None:
        while True:
            event = self._queue.get()
            try:
                if event is None:
                    return
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                        handle.write(json.dumps(event.as_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")
                except OSError:
                    # Plugin observability cannot stop the Core or a plugin job.
                    continue
            finally:
                self._queue.task_done()

    def emit(self, *, activity_id: str, plugin_id: str, capability_id: str, state: str,
             status_text: str, progress: float | int | None = None, stage: str | None = None) -> PluginActivityEvent:
        activity_id = _text(activity_id, "activity_id")
        plugin_id = _text(plugin_id, "plugin_id")
        capability_id = _text(capability_id, "capability_id")
        state = _text(state, "state").casefold()
        if state not in ACTIVITY_STATES:
            raise ActivityValidationError(f"state must be one of: {', '.join(sorted(ACTIVITY_STATES))}")
        status_text = _text(status_text, "status_text")[:MAX_STATUS_TEXT]
        if progress is not None:
            if isinstance(progress, bool) or not isinstance(progress, (int, float)) or not 0 <= float(progress) <= 100:
                raise ActivityValidationError("progress must be a percentage between 0 and 100")
            progress = round(float(progress), 1)
        if stage is not None:
            stage = _text(stage, "stage")[:120]
        event = PluginActivityEvent(
            activity_id=activity_id,
            plugin_id=plugin_id,
            capability_id=capability_id,
            state=state,
            status_text=status_text,
            progress=progress,
            stage=stage,
            event_id=uuid.uuid4().hex,
            timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        )
        with self._lock:
            self._recent.append(event)
            closed = self._closed
        if not closed:
            try:
                self._queue.put_nowait(event)
            except queue.Full:
                with self._lock:
                    self.dropped_events += 1
        return event

    def reporter(self, *, activity_id: str, plugin_id: str, capability_id: str) -> "PluginActivityReporter":
        return PluginActivityReporter(self, activity_id, plugin_id, capability_id)

    def recent(self, limit: int = 25) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._recent)[-max(1, min(int(limit), MAX_RECENT_EVENTS)):]
        return [event.as_dict() for event in reversed(events)]

    def flush(self, timeout: float = 2.0) -> None:
        if self._closed:
            return
        waiter = threading.Thread(target=self._queue.join, daemon=True)
        waiter.start()
        waiter.join(max(0.0, float(timeout)))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.flush()
        self._queue.put(None)
        self._worker.join(timeout=2.0)


class PluginActivityReporter:
    """Convenience reporter supplied to one independent plugin activity."""

    def __init__(self, stream: PluginActivityStream, activity_id: str, plugin_id: str, capability_id: str):
        self.stream = stream
        self.activity_id = activity_id
        self.plugin_id = plugin_id
        self.capability_id = capability_id

    def report(self, state: str, status_text: str, *, progress: float | int | None = None, stage: str | None = None) -> PluginActivityEvent:
        return self.stream.emit(
            activity_id=self.activity_id, plugin_id=self.plugin_id, capability_id=self.capability_id,
            state=state, status_text=status_text, progress=progress, stage=stage,
        )

    def started(self, status_text: str = "Capability started.", *, stage: str | None = None) -> PluginActivityEvent:
        return self.report("started", status_text, progress=0, stage=stage)

    def progress(self, percentage: float | int, status_text: str, *, stage: str | None = None) -> PluginActivityEvent:
        return self.report("progress", status_text, progress=percentage, stage=stage)

    def running(self, status_text: str = "Capability is running.", *, stage: str | None = None) -> PluginActivityEvent:
        return self.report("running", status_text, stage=stage)

    def stage(self, stage: str, status_text: str) -> PluginActivityEvent:
        return self.report("stage", status_text, stage=stage)

    def completed(self, status_text: str = "Capability completed.") -> PluginActivityEvent:
        return self.report("completed", status_text, progress=100)

    def warning(self, status_text: str, *, stage: str | None = None) -> PluginActivityEvent:
        return self.report("warning", status_text, stage=stage)

    def failed(self, status_text: str, *, stage: str | None = None) -> PluginActivityEvent:
        return self.report("failed", status_text, stage=stage)

    def cancelled(self, status_text: str = "Capability cancelled.") -> PluginActivityEvent:
        return self.report("cancelled", status_text)


__all__ = [
    "ACTIVITY_STATES", "TERMINAL_STATES", "ActivityValidationError", "PluginActivityEvent",
    "PluginActivityReporter", "PluginActivityStream",
]
