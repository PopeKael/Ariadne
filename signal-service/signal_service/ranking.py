"""Deterministic v0.1 ranking with a future profile-aware extension point."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Protocol

from .models import Signal


class SignalRanker(Protocol):
    def rank(self, signals: Iterable[Signal], limit: int = 30, profile: object = None) -> list[Signal]: ...


def _age_hours(timestamp: str, now: datetime) -> float:
    try:
        value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return max(0.0, (now - value.astimezone(timezone.utc)).total_seconds() / 3600)
    except ValueError:
        return 9999.0


class BasicRanker:
    """Recency plus useful-content scoring; profile is intentionally unused in v0.1."""

    def rank(self, signals: Iterable[Signal], limit: int = 30, profile: object = None) -> list[Signal]:
        now = datetime.now(timezone.utc)
        scored: list[Signal] = []
        for signal in signals:
            age = _age_hours(signal.published_at, now)
            recency = max(0.0, 1.0 - min(age, 168.0) / 168.0)
            content = min(1.0, len(signal.summary or signal.content) / 600.0)
            title_quality = min(1.0, len(signal.title) / 100.0)
            score = (recency * 0.65) + (content * 0.25) + (title_quality * 0.10)
            scored.append(signal.__class__(**{**signal.__dict__, "rank_score": score, "rank_reason": "recency + content completeness"}))
        scored.sort(key=lambda item: (-item.rank_score, item.published_at, item.signal_id))
        selected: list[Signal] = []
        source_counts: dict[str, int] = {}
        for signal in scored:
            source_key = signal.source_name.casefold()
            # Keep source diversity as a guardrail, but allow enough items for
            # the Discover sections to reach their six-item floor.
            if source_counts.get(source_key, 0) >= 10:
                continue
            selected.append(signal)
            source_counts[source_key] = source_counts.get(source_key, 0) + 1
            if len(selected) >= max(1, min(int(limit), 40)):
                break
        return selected


__all__ = ["BasicRanker", "SignalRanker"]
