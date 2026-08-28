"""Provider-independent Core interaction events.

Home is the first producer, but the event shape is intentionally not a Home
UI contract.  Other interfaces and plugins can consume conversation, turn,
response, selection, and feedback references without owning their storage.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from librarian_events import LibrarianEventStream


INTERACTION_EVENT_TYPES = frozenset({
    "conversation_attached",
    "turn_started",
    "response_completed",
    "response_interrupted",
    "selection_created",
    "feedback_recorded",
})


class InteractionEventError(ValueError):
    """Raised when a Core interaction event is not structurally valid."""


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InteractionEventError(f"{field} must be a non-empty string")
    return value.strip()


class CoreInteractionStream:
    """Small append-only event seam shared by Core surfaces and plugins."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._stream = LibrarianEventStream(self.path)

    def emit(self, event_type: str, *, conversation_id: str, turn_id: str | None = None,
             response_id: str | None = None, data: Mapping[str, Any] | None = None) -> None:
        event_type = _required_text(event_type, "event_type").casefold()
        if event_type not in INTERACTION_EVENT_TYPES:
            raise InteractionEventError(f"event_type must be one of: {', '.join(sorted(INTERACTION_EVENT_TYPES))}")
        conversation_id = _required_text(conversation_id, "conversation_id")
        if turn_id is not None:
            turn_id = _required_text(turn_id, "turn_id")
        if response_id is not None:
            response_id = _required_text(response_id, "response_id")
        payload: dict[str, Any] = {"conversation_id": conversation_id}
        if turn_id:
            payload["turn_id"] = turn_id
        if response_id:
            payload["response_id"] = response_id
        if data:
            payload["data"] = dict(data)
        self._stream.emit("CORE_INTERACTION", data={"event_type": event_type, **payload})

    def read_recent(self, limit: int = 50, *, conversation_id: str | None = None) -> list[dict[str, Any]]:
        events = self._stream.read_recent(max(1, min(int(limit) * 2, 200)))
        result: list[dict[str, Any]] = []
        for event in events:
            data = event.get("data") if isinstance(event, dict) else None
            if not isinstance(data, dict):
                continue
            if conversation_id and data.get("conversation_id") != conversation_id:
                continue
            result.append(event)
            if len(result) >= max(1, min(int(limit), 100)):
                break
        return result


__all__ = ["CoreInteractionStream", "INTERACTION_EVENT_TYPES", "InteractionEventError"]
