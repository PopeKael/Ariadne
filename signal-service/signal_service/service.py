"""Signal refinement pipeline and cache lifecycle."""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .diagnostics import emit_diagnostic
from .feeds import FeedDefinition, configured_feeds, fetch_feed
from .models import Signal, normalize_candidate, utc_now
from .ranking import BasicRanker, SignalRanker
from .store import SignalStore


def _iso_age_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
    except ValueError:
        return None


class SignalService:
    """Portable service object used by both the HTTP server and tests."""

    def __init__(self, database_path: str | Path, feeds: Iterable[FeedDefinition] | None = None, *, ranker: SignalRanker | None = None, feed_timeout: float = 15.0, item_limit: int = 20):
        self.store = SignalStore(database_path)
        self.feeds = list(feeds) if feeds is not None else configured_feeds()
        self.ranker = ranker or BasicRanker()
        self.feed_timeout = max(1.0, float(feed_timeout))
        self.item_limit = max(1, min(int(item_limit), 100))
        self._lock = threading.RLock()
        self._last_attempt_at: str | None = None
        self._last_success_at: str | None = None
        self._last_collection_ok: bool | None = None
        self._last_errors: list[dict[str, str]] = []
        self._last_source_status: list[dict[str, Any]] = []
        self._refresh_running = False
        self._successful_feed_collection_logged = False

    def close(self) -> None:
        self.store.close()

    def _build_cached_briefing(self, collection: dict[str, Any]) -> dict[str, Any]:
        ranked = self.ranker.rank(self.store.recent(), limit=40)
        return self.store.save_briefing(ranked, collection)

    def ingest_candidates(self, candidates: Iterable[object], *, default_source_name: str = "External candidate producer", default_source_url: str = "", ingest_type: str = "candidate", adapter: str = "external", default_category: str = "Main News Feed") -> dict[str, Any]:
        accepted = 0
        duplicates = 0
        rejected = 0
        errors: list[str] = []
        for candidate in candidates:
            try:
                signal = normalize_candidate(candidate, default_source_name=default_source_name, default_source_url=default_source_url, ingest_type=ingest_type, adapter=adapter, default_category=default_category)
                _, created = self.store.upsert(signal)
                accepted += 1
                if not created:
                    duplicates += 1
            except ValueError as exc:
                rejected += 1
                errors.append(str(exc))
        collection = {"mode": ingest_type, "accepted": accepted, "duplicates": duplicates, "rejected": rejected, "errors": errors}
        briefing = self._build_cached_briefing(collection) if accepted else self.store.latest_briefing()
        now = utc_now()
        with self._lock:
            self._last_attempt_at = now
            if accepted:
                self._last_success_at = now
                self._last_collection_ok = True
                self._last_errors = []
        return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected, "errors": errors, "briefing": briefing}

    def refresh(self) -> dict[str, Any]:
        with self._lock:
            if self._refresh_running:
                return {"ok": False, "running": True, "briefing": self.store.latest_briefing()}
            self._refresh_running = True
        attempted_at = utc_now()
        started = time.monotonic()
        emit_diagnostic("collection_started", attempted_at=attempted_at, feed_count=len(self.feeds))
        source_status: list[dict[str, Any]] = []
        accepted = 0
        duplicates = 0
        errors: list[dict[str, str]] = []
        try:
            for feed in self.feeds:
                try:
                    candidates = fetch_feed(feed, timeout=self.feed_timeout, item_limit=self.item_limit)
                    result = self.ingest_candidates(candidates, default_source_name=feed.name, default_source_url=feed.url, ingest_type="feed", adapter="rss_atom", default_category=feed.category)
                    accepted += int(result["accepted"])
                    duplicates += int(result["duplicates"])
                    source_status.append({"name": feed.name, "url": feed.url, "state": "healthy", "items": len(candidates), "accepted": result["accepted"], "duplicates": result["duplicates"]})
                except Exception as exc:  # one bad source must not stop the refinery
                    detail = str(exc)[:500]
                    errors.append({"source": feed.name, "url": feed.url, "error": detail})
                    source_status.append({"name": feed.name, "url": feed.url, "state": "attention", "error": detail})
            successful_sources = sum(1 for item in source_status if item.get("state") == "healthy")
            collection = {"mode": "feeds", "attempted_at": attempted_at, "sources": source_status, "successful_sources": successful_sources, "accepted": accepted, "duplicates": duplicates, "errors": errors}
            if successful_sources:
                briefing = self._build_cached_briefing(collection)
                success_at = utc_now()
            else:
                briefing = self.store.latest_briefing()
                success_at = None
            with self._lock:
                first_successful = bool(successful_sources) and not self._successful_feed_collection_logged
                if successful_sources:
                    self._successful_feed_collection_logged = True
                self._last_attempt_at = attempted_at
                self._last_source_status = source_status
                self._last_errors = errors
                self._last_collection_ok = bool(successful_sources)
                if success_at:
                    self._last_success_at = success_at
            emit_diagnostic(
                "collection_completed",
                ok=bool(successful_sources),
                attempted_at=attempted_at,
                completed_at=success_at or utc_now(),
                duration_ms=round((time.monotonic() - started) * 1000, 1),
                successful_sources=successful_sources,
                accepted=accepted,
                duplicates=duplicates,
                error_count=len(errors),
                first_successful=first_successful,
                briefing_generated_at=(briefing or {}).get("generated_at") if isinstance(briefing, dict) else None,
            )
            return {"ok": bool(successful_sources), "accepted": accepted, "duplicates": duplicates, "sources": source_status, "errors": errors, "briefing": briefing}
        finally:
            with self._lock:
                self._refresh_running = False

    def briefing(self, limit: int = 40) -> dict[str, Any] | None:
        cached = self.store.latest_briefing()
        if cached is None:
            self.refresh()
            cached = self.store.latest_briefing()
        if cached is None:
            return None
        result = dict(cached)
        result["signals"] = list(cached.get("signals", []))[: max(1, min(int(limit), 40))]
        age = _iso_age_seconds(self._last_success_at)
        result["stale"] = self._last_collection_ok is False or bool(age is not None and age > max(300, int(os.environ.get("SIGNAL_SERVICE_STALE_AFTER_SECONDS", "21600"))))
        result["last_success_at"] = self._last_success_at or cached.get("generated_at")
        result["last_attempt_at"] = self._last_attempt_at
        result["errors"] = list(self._last_errors)
        result["source_status"] = list(self._last_source_status)
        result["watchlist_topics"] = self.store.watchlist_topics()
        return result

    def health(self) -> dict[str, Any]:
        latest = self.store.latest_briefing()
        with self._lock:
            last_success = self._last_success_at or (latest or {}).get("generated_at")
            errors = list(self._last_errors)
            source_status = list(self._last_source_status)
            running = self._refresh_running
            attempt = self._last_attempt_at
            collection_ok = self._last_collection_ok
        state = "healthy" if latest and not errors and collection_ok is not False else "attention" if latest or errors else "starting"
        return {"ok": True, "service": "ariadne-signal-service", "version": "0.1.0", "state": state, "feeds": [{"name": feed.name, "url": feed.url} for feed in self.feeds], "last_attempt_at": attempt, "last_success_at": last_success, "last_success_age_seconds": _iso_age_seconds(last_success), "last_collection_ok": collection_ok, "refresh_running": running, "source_status": source_status, "errors": errors, "cached_briefing": bool(latest)}

    def record_feedback(self, signal_id: str, value: str, recorded_at: str | None = None) -> dict[str, Any]:
        if value not in {"useful", "interesting", "not_useful"}:
            raise ValueError("Feedback must be useful, interesting, or not_useful")
        return self.store.record_feedback(signal_id, value, recorded_at)

    def add_watchlist_topic(self, topic: str, active: bool = True) -> dict[str, Any]:
        return self.store.upsert_watchlist_topic(topic, active)

    def watchlist_topics(self, active_only: bool = True) -> list[dict[str, Any]]:
        return self.store.watchlist_topics(active_only)

    def start_background_refresh(self, interval_seconds: float | None = None) -> threading.Thread:
        interval = max(60.0, float(interval_seconds if interval_seconds is not None else os.environ.get("SIGNAL_SERVICE_REFRESH_SECONDS", "900")))

        def loop() -> None:
            while True:
                try:
                    self.refresh()
                except Exception:
                    pass
                time.sleep(interval)

        worker = threading.Thread(target=loop, name="ariadne-signal-refresh", daemon=True)
        worker.start()
        return worker


def extract_intake_candidates(payload: object) -> tuple[list[object], dict[str, Any]]:
    """Accept common n8n item wrappers without binding Core to n8n's schema."""
    if isinstance(payload, list):
        return payload, {}
    if not isinstance(payload, dict):
        return [], {}
    for key in ("candidates", "items", "signals", "data", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value, payload
    if any(key in payload for key in ("title", "headline", "link", "url")):
        return [payload], payload
    return [], payload


__all__ = ["SignalService", "extract_intake_candidates"]
