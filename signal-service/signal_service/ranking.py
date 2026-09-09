"""Inspectable profile-aware ranking for the live briefing."""
from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
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
    AI_WATCH_REQUIRES_SEMANTIC_MATCH = True

    DEFAULT_WEIGHTS = {
        "semantic_interest": 0.40,
        "learned_affinity": 0.20,
        "recency": 0.20,
        "content_quality": 0.12,
        "source_diversity": 0.08,
    }

    def rank(self, signals: Iterable[Signal], limit: int = 30, profile: object = None) -> list[Signal]:
        now = datetime.now(timezone.utc)
        scored: list[Signal] = []
        profile = profile if isinstance(profile, dict) else {}
        weights = dict(self.DEFAULT_WEIGHTS)
        weights.update(profile.get("weights") if isinstance(profile.get("weights"), dict) else {})
        source_scores = {str(item.get("label")): float(item.get("score", 0.0)) for item in profile.get("sources", []) if isinstance(item, dict)}
        category_scores = {str(item.get("label")): float(item.get("score", 0.0)) for item in profile.get("categories", []) if isinstance(item, dict)}
        semantic_available = profile.get("semantic_state") == "healthy" and int(profile.get("semantic_interest_count", 0)) > 0
        for signal in signals:
            if self.AI_WATCH_REQUIRES_SEMANTIC_MATCH and semantic_available and signal.category == "AI Watch" and not signal.semantic_matches:
                # Keep raw source category/provenance intact and adjust only the
                # briefing projection when semantic matching is available.
                signal = replace(signal, category="Main News Feed")
            age = _age_hours(signal.published_at, now)
            recency = max(0.0, 1.0 - min(age, 168.0) / 168.0)
            content = min(1.0, len(signal.summary or signal.content) / 600.0)
            title_quality = min(1.0, len(signal.title) / 100.0)
            quality = (content * 0.7) + (title_quality * 0.3)
            matches = list(signal.semantic_matches or [])
            semantic = max((float(item.get("semantic_score", 0.0)) * min(1.0, float(item.get("priority", 1.0))) for item in matches if isinstance(item, dict)), default=0.0)
            affinity = max(-1.0, min(1.0, source_scores.get(signal.source_name, 0.0) + category_scores.get(signal.category, 0.0)))
            affinity_component = (affinity + 1.0) / 2.0
            score = (
                semantic * float(weights["semantic_interest"])
                + affinity_component * float(weights["learned_affinity"])
                + recency * float(weights["recency"])
                + quality * float(weights["content_quality"])
                + float(weights["source_diversity"])
            )
            reasons: list[str] = []
            if matches:
                reasons.append("; ".join(f"{item.get('interest')} · semantic {float(item.get('semantic_score', 0.0)):.2f}" for item in matches[:2]))
            if affinity > 0.08:
                reasons.append("learned preference")
            elif affinity < -0.08:
                reasons.append("reduced learned preference")
            reasons.extend(["recent", "complete content"])
            scored.append(signal.__class__(**{**signal.__dict__, "rank_score": score, "rank_reason": " · ".join(reasons)}))
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
