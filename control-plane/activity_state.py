"""Canonical Home activity lifecycle and asynchronous avatar presentation.

The control plane owns one operational state stream.  The browser reads its
snapshot for status text and the optional native host receives a mapped avatar
state on a background executor.  Avatar delivery is deliberately best effort:
it must never add latency to the request that produced the state.
"""
from __future__ import annotations

from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
import threading
import time
from typing import Callable


CANONICAL_ACTIVITY_STATES = (
    "idle",
    "reading",
    "searching",
    "thinking",
    "answering",
    "complete",
    "error",
)
ACTIVITY_LABELS = {
    "idle": "Ready",
    "reading": "Reading source article",
    "searching": "Searching the Vault",
    "thinking": "Thinking",
    "answering": "Answering",
    "complete": "Complete",
    "error": "Error",
}
# These are the established host/asset states.  The operational stream stays
# small and stable while the existing avatar protocol remains compatible.
AVATAR_STATE_FOR_ACTIVITY = {
    "idle": "idle",
    "reading": "reading",
    "searching": "searching_vault",
    "thinking": "thinking",
    "answering": "speaking",
    # The host's existing speaking hold returns the avatar to idle.  Sending
    # another idle event here would cancel that hold, so completion is a
    # status-stream terminal state rather than a second avatar transition.
    "complete": None,
    "error": "error",
}


@dataclass(frozen=True)
class ActivitySnapshot:
    chat_id: str
    state: str
    label: str
    sequence: int
    changed_at: float
    message: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "chat_id": self.chat_id,
            "state": self.state,
            "label": self.label,
            "sequence": self.sequence,
            "changed_at": self.changed_at,
            "message": self.message,
        }


class ActivityStateStream:
    """Small per-chat state stream shared by Home status and avatar output."""

    def __init__(
        self,
        *,
        emit_avatar_state: Callable[[str], bool] | None = None,
        executor: Executor | None = None,
    ) -> None:
        self._emit_avatar_state = emit_avatar_state
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="activity-avatar")
        self._owns_executor = executor is None
        self._lock = threading.RLock()
        self._snapshots: dict[str, ActivitySnapshot] = {}
        self._sequence = 0
        self._last_avatar_state: dict[str, str] = {}

    def publish(self, chat_id: str, state: str, message: str = "") -> ActivitySnapshot:
        if state not in CANONICAL_ACTIVITY_STATES:
            raise ValueError(f"Unknown Home activity state: {state}")
        now = time.time()
        with self._lock:
            self._sequence += 1
            snapshot = ActivitySnapshot(
                chat_id=str(chat_id), state=state, label=ACTIVITY_LABELS[state],
                sequence=self._sequence, changed_at=now, message=str(message or ""),
            )
            self._snapshots[str(chat_id)] = snapshot
            avatar_state = AVATAR_STATE_FOR_ACTIVITY[state]
            should_emit = bool(avatar_state and avatar_state != self._last_avatar_state.get(str(chat_id)))
            if should_emit:
                self._last_avatar_state[str(chat_id)] = str(avatar_state)
        if should_emit and self._emit_avatar_state is not None:
            # Submission is intentionally not awaited.  The native host has
            # its own 800 ms minimum-dwell/coalescing policy.
            self._executor.submit(self._deliver_avatar_state, str(avatar_state))
        return snapshot

    def snapshot(self, chat_id: str) -> ActivitySnapshot:
        with self._lock:
            return self._snapshots.get(str(chat_id)) or ActivitySnapshot(
                chat_id=str(chat_id), state="idle", label=ACTIVITY_LABELS["idle"],
                sequence=0, changed_at=time.time(), message="",
            )

    def _deliver_avatar_state(self, state: str) -> None:
        try:
            self._emit_avatar_state(state)
        except Exception:
            # Avatar presentation is optional and never part of request work.
            return

    def close(self) -> None:
        if self._owns_executor and hasattr(self._executor, "shutdown"):
            self._executor.shutdown(wait=False, cancel_futures=True)


__all__ = [
    "ACTIVITY_LABELS", "AVATAR_STATE_FOR_ACTIVITY", "ActivitySnapshot",
    "ActivityStateStream", "CANONICAL_ACTIVITY_STATES",
]
