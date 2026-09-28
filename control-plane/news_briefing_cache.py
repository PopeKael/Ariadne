"""Persistent local snapshot of Hera's card-only ranked news briefing."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
DEFAULT_URL = os.environ.get("ARIADNE_NEWS_BACKEND_URL", "http://192.168.1.200:8791").rstrip("/")
DEFAULT_CACHE_PATH = Path(os.environ.get(
    "ARIADNE_NEWS_BRIEFING_CACHE_PATH", str(ROOT / "runtime" / "news-briefing.json")
))
DEFAULT_TIMEOUT = max(0.2, float(os.environ.get("ARIADNE_NEWS_BRIEFING_TIMEOUT", "3")))
DEFAULT_REFRESH_SECONDS = max(15, int(os.environ.get("ARIADNE_NEWS_BRIEFING_REFRESH_SECONDS", "60")))
MAX_SNAPSHOT_BYTES = 5_000_000
_ARTICLE_BODY_FIELDS = {"markdown", "content", "body", "html", "article_text", "full_text"}


def _fingerprint(briefing: dict[str, Any]) -> str:
    input_hash = str(briefing.get("input_hash") or "")
    result_hash = str(briefing.get("result_hash") or "")
    if input_hash or result_hash:
        # Interaction state is persisted separately from immutable article text
        # and can change without affecting the curator's ordering hashes.
        feedback = [
            (str(card.get("article_id") or ""), card.get("feedback"), card.get("interaction_state"))
            for card in briefing.get("articles", []) if isinstance(card, dict)
        ]
        material = json.dumps([input_hash, result_hash, feedback], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    else:
        stable = {key: value for key, value in briefing.items()
                  if key not in {"generated_at", "run_count", "elapsed_ms"}}
        material = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _validated_card_snapshot(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("ok") is False:
        return None
    cards = value.get("articles")
    if not isinstance(cards, list) or len(cards) > 100 or any(not isinstance(card, dict) for card in cards):
        return None
    # The briefing endpoint is a card/index contract. Defensively strip any
    # unexpected article-body fields; Markdown is fetched only by article ID.
    cleaned = dict(value)
    cleaned["articles"] = [
        {key: field for key, field in card.items() if key.casefold() not in _ARTICLE_BODY_FIELDS}
        for card in cards
    ]
    return cleaned


def _feedback_value(card: dict[str, Any]) -> str:
    feedback = card.get("feedback")
    return str(feedback.get("value") or "") if isinstance(feedback, dict) else ""


def _feedback_timestamp(card: dict[str, Any]) -> str:
    feedback = card.get("feedback")
    return str(feedback.get("updated_at") or feedback.get("timestamp") or "") if isinstance(feedback, dict) else ""


def _merge_cards(previous: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge Hera candidates by article ID while retaining local interaction state."""
    previous_by_id = {
        str(card.get("article_id")): card
        for card in previous
        if isinstance(card, dict) and str(card.get("article_id") or "").strip()
    }
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for remote in incoming:
        article_id = str(remote.get("article_id") or "").strip()
        if not article_id or article_id in seen:
            continue
        local = previous_by_id.get(article_id, {})
        card = {**local, **remote}
        local_feedback = _feedback_value(local)
        remote_feedback = _feedback_value(remote)
        if local_feedback and (not remote_feedback or _feedback_timestamp(local) > _feedback_timestamp(remote)):
            card["feedback"] = local.get("feedback")
        if local.get("interaction_state") and not remote.get("interaction_state"):
            card["interaction_state"] = local["interaction_state"]
        merged.append(card)
        seen.add(article_id)
    # Keep locally known candidates until Hera replaces them. Final local
    # ranking and the 100-card limit decide what remains visible.
    for local in previous:
        if not isinstance(local, dict):
            continue
        article_id = str(local.get("article_id") or "").strip()
        if article_id and article_id not in seen:
            merged.append(dict(local))
            seen.add(article_id)
    return merged


def _preference_score(card: dict[str, Any], preferences: dict[str, Any] | None) -> float:
    """Apply local Warren preference evidence to one Hera candidate."""
    preferences = preferences if isinstance(preferences, dict) else {}
    source_scores = {
        str(item.get("label") or "").casefold(): float(item.get("score") or 0)
        for item in preferences.get("sources", [])
        if isinstance(item, dict) and item.get("label")
    }
    category_scores = {
        str(item.get("label") or "").casefold(): float(item.get("score") or 0)
        for item in preferences.get("categories", [])
        if isinstance(item, dict) and item.get("label")
    }
    interest_items = [
        (str(item.get("label") or "").casefold(), float(item.get("score") or 0))
        for item in preferences.get("interests", [])
        if isinstance(item, dict) and item.get("label")
    ]
    source = str(card.get("source") or card.get("source_name") or "").casefold()
    category = str(card.get("category") or "").casefold()
    text = " ".join(str(card.get(key) or "") for key in ("title", "summary", "source", "category")).casefold()
    score = float(card.get("rank_score") or card.get("briefing_score") or 0)
    if score <= 1.0:
        score *= 100.0
    score += source_scores.get(source, 0.0) * 8.0
    score += category_scores.get(category, 0.0) * 5.0
    score += sum(value * 6.0 for label, value in interest_items if label and label in text)
    feedback = _feedback_value(card)
    score += {"useful": 8.0, "interesting": 6.0, "not_useful": -18.0}.get(feedback, 0.0)
    if str(card.get("interaction_state") or "") == "consumed":
        score -= 2.0
    return score


def _rank_cards(cards: list[dict[str, Any]], preferences: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
    ranked = []
    for index, card in enumerate(cards):
        if not isinstance(card, dict) or not card.get("article_id"):
            continue
        item = dict(card)
        item["local_rank_score"] = round(_preference_score(item, preferences), 4)
        ranked.append((
            -float(item["local_rank_score"]),
            int(item.get("position") or index),
            str(item.get("article_id")),
            item,
        ))
    ranked.sort(key=lambda value: value[:3])
    result = []
    for position, (_, _, _, card) in enumerate(ranked[:max(1, min(int(limit), 100))], 1):
        card["position"] = position
        card["local_ranked"] = True
        result.append(card)
    return result


class NewsBriefingCache:
    """Local-first snapshot access plus asynchronous, last-known-good Hera sync."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        cache_path: str | Path | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        refresh_seconds: int = DEFAULT_REFRESH_SECONDS,
    ) -> None:
        self.base_url = (base_url or DEFAULT_URL).rstrip("/")
        self.cache_path = Path(cache_path) if cache_path is not None else DEFAULT_CACHE_PATH
        self.timeout = max(0.2, float(timeout))
        self.refresh_seconds = max(15, int(refresh_seconds))
        self._lock = threading.RLock()
        self._sync_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._briefing = self._load_snapshot()
        self._fingerprint = _fingerprint(self._briefing) if self._briefing is not None else ""
        self._cached_at = self._file_mtime() if self._briefing is not None else None
        self._last_sync: dict[str, Any] = {"state": "not_checked", "message": "Background sync has not run yet."}

    def _file_mtime(self) -> str | None:
        try:
            return datetime.fromtimestamp(self.cache_path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        except OSError:
            return None

    def _load_snapshot(self) -> dict[str, Any] | None:
        try:
            if not self.cache_path.is_file() or self.cache_path.stat().st_size > MAX_SNAPSHOT_BYTES:
                return None
            parsed = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return _validated_card_snapshot(parsed)
        except (OSError, ValueError, TypeError):
            return None

    def snapshot(self) -> dict[str, Any]:
        """Return a defensive copy from memory/disk. This method never contacts Hera."""
        with self._lock:
            briefing = copy.deepcopy(self._briefing)
            sync = copy.deepcopy(self._last_sync)
            cached_at = self._cached_at
            fingerprint = self._fingerprint
        return {
            "ok": briefing is not None,
            "available": briefing is not None,
            "source": "local_snapshot",
            "cached_at": cached_at,
            "briefing_hash": fingerprint,
            "sync": sync,
            "briefing": briefing,
            **({} if briefing is not None else {"message": "No local news briefing snapshot is available yet."}),
        }

    def ranked_articles(self, *, limit: int = 100, preferences: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return and persist Ariadne's final local card ordering."""
        with self._lock:
            if self._briefing is None:
                return []
            ranked = _rank_cards(list(self._briefing.get("articles", [])), preferences, limit)
            current_ids = [str(card.get("article_id") or "") for card in self._briefing.get("articles", [])]
            ranked_ids = [str(card.get("article_id") or "") for card in ranked]
            if current_ids != ranked_ids or any(
                card.get("local_rank_score") != current.get("local_rank_score")
                for card, current in zip(ranked, self._briefing.get("articles", []))
            ):
                updated = copy.deepcopy(self._briefing)
                updated["articles"] = ranked
                updated["article_count"] = len(ranked)
                self._atomic_write(updated)
                self._briefing = updated
                self._fingerprint = _fingerprint(updated)
                self._cached_at = self._file_mtime()
            return copy.deepcopy(ranked)

    def update_article_feedback(self, article_id: str, value: str, updated_at: str) -> bool:
        """Atomically update local card state after Hera confirms persisted feedback."""
        if value not in {"useful", "interesting", "not_useful"}:
            raise ValueError("Unsupported news feedback value.")
        with self._lock:
            if self._briefing is None:
                return False
            updated = copy.deepcopy(self._briefing)
            article = next((card for card in updated.get("articles", [])
                            if isinstance(card, dict) and card.get("article_id") == article_id), None)
            if article is None:
                return False
            article["feedback"] = {"value": value, "updated_at": updated_at}
            updated["articles"] = _rank_cards(updated.get("articles", []), limit=100)
            self._atomic_write(updated)
            self._briefing = updated
            self._fingerprint = _fingerprint(updated)
            self._cached_at = self._file_mtime()
        return True

    def _request_briefing(self) -> dict[str, Any]:
        parts = urlsplit(self.base_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("News backend URL must use HTTP or HTTPS.")
        request = urllib.request.Request(
            f"{self.base_url}/briefing", headers={"Accept": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read(MAX_SNAPSHOT_BYTES + 1)
        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise ValueError("Hera briefing response exceeds the local cache limit.")
        return json.loads(raw.decode("utf-8"))

    def _atomic_write(self, briefing: dict[str, Any]) -> None:
        temporary: Path | None = None
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.cache_path.parent,
                prefix=f".{self.cache_path.name}.", suffix=".tmp", delete=False,
                newline="\n",
            ) as handle:
                temporary = Path(handle.name)
                json.dump(briefing, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.cache_path)
        except OSError:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    def sync_once(self) -> dict[str, Any]:
        """Fetch only /briefing; commit changed card metadata atomically."""
        started = time.perf_counter()
        with self._sync_lock:
            try:
                remote = _validated_card_snapshot(self._request_briefing())
                if remote is None:
                    raise ValueError("Hera returned an invalid or unsuccessful briefing.")
                with self._lock:
                    previous = self._briefing or {}
                    merged = dict(remote)
                    merged["articles"] = _rank_cards(
                        _merge_cards(previous.get("articles", []) if isinstance(previous, dict) else [], remote["articles"]),
                        limit=100,
                    )
                    merged["article_count"] = len(merged["articles"])
                    version = _fingerprint(merged)
                    previous_ids = [
                        card.get("article_id") for card in previous.get("articles", [])
                    ] if isinstance(previous, dict) else []
                    merged_ids = [card.get("article_id") for card in merged["articles"]]
                    changed = self._briefing is None or version != self._fingerprint or merged_ids != previous_ids
                    if changed:
                        self._atomic_write(merged)
                        self._briefing = merged
                        self._fingerprint = version
                        self._cached_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    result = {
                        "state": "updated" if changed else "unchanged",
                        "ok": True,
                        "changed": changed,
                        "briefing_hash": version,
                        "generated_at": remote.get("generated_at"),
                    }
            except (OSError, urllib.error.URLError, TimeoutError, ValueError, TypeError, json.JSONDecodeError) as exc:
                with self._lock:
                    result = {
                        "state": "unavailable", "ok": False, "changed": False,
                        "message": f"Hera news briefing sync failed: {str(exc)[:200]}",
                    }
            result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            with self._lock:
                self._last_sync = dict(result)
            return result

    def start_background_sync(self) -> bool:
        """Start the poller once; returning never waits for a Hera request."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()

            def poll() -> None:
                while not self._stop.is_set():
                    self.sync_once()
                    if self._stop.wait(self.refresh_seconds):
                        return

            self._thread = threading.Thread(target=poll, name="ariadne-news-briefing-sync", daemon=True)
            self._thread.start()
            return True

    def stop_background_sync(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(max(0.0, timeout))


__all__ = ["NewsBriefingCache"]
