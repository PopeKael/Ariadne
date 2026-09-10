"""Discovery Engine lifecycle, optional SearXNG expansion, and Signal intake."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from .clustering import cluster_articles, diversify, rank_stories, story_from_cluster
from .feeds import FetchResult, configured_sources, fetch_feed
from .models import Article, SourceDefinition, source_domain, stable_source_id, utc_now
from .store import DiscoveryStore


class DiscoveryEngine:
    def __init__(self, database_path: str, *, sources: list[SourceDefinition] | None = None):
        self.database_path = database_path
        self.store = DiscoveryStore(database_path)
        self.sources = sources or configured_sources()
        self.store.upsert_sources(self.sources)
        self.fetch_timeout = max(2.0, float(os.environ.get("DISCOVERY_SERVICE_FETCH_TIMEOUT_SECONDS", "20")))
        self.push_timeout = max(5.0, float(os.environ.get("DISCOVERY_SERVICE_PUSH_TIMEOUT_SECONDS", "120")))
        self.push_batch_size = max(1, min(int(os.environ.get("DISCOVERY_SERVICE_PUSH_BATCH_SIZE", "20")), 50))
        self.push_retries = max(0, min(int(os.environ.get("DISCOVERY_SERVICE_PUSH_RETRIES", "2")), 4))
        self.item_limit = max(1, min(int(os.environ.get("DISCOVERY_SERVICE_ITEMS_PER_SOURCE", "40")), 100))
        self.concurrency = max(1, min(int(os.environ.get("DISCOVERY_SERVICE_CONCURRENCY", "6")), 16))
        self.interval_seconds = max(60, int(os.environ.get("DISCOVERY_SERVICE_REFRESH_SECONDS", "900")))
        self.lookback_days = max(1, min(int(os.environ.get("DISCOVERY_SERVICE_LOOKBACK_DAYS", "7")), 30))
        # Keep a useful rolling story cache. The API remains bounded below
        # this value, while Home chooses a smaller browse projection.
        self.max_stories = max(10, min(int(os.environ.get("DISCOVERY_SERVICE_MAX_STORIES", "240")), 300))
        self.searxng_url = os.environ.get("DISCOVERY_SERVICE_SEARXNG_URL", "").strip().rstrip("/")
        self.search_queries = [item.strip() for item in os.environ.get("DISCOVERY_SERVICE_SEARCH_QUERIES", "world news|Main News Feed,AI artificial intelligence|AI Watch,Thailand news|Thailand Focus,science discovery|Science,technology hardware|Technology,cybersecurity|Security,finance markets|Finance,space exploration|Space,health research|Health").split(",") if "|" in item and item.split("|", 1)[0].strip()]
        self.signal_service_url = os.environ.get("DISCOVERY_SERVICE_SIGNAL_SERVICE_URL", "").strip().rstrip("/")
        self._lock = threading.RLock()
        self._refresh_running = False
        self._last_attempt_at: str | None = None
        self._last_success_at: str | None = None
        self._last_result: dict[str, Any] = {}
        self._last_push: dict[str, Any] = {}

    def close(self) -> None:
        self.store.close()

    def _fetch_one(self, row: dict[str, Any]) -> tuple[dict[str, Any], FetchResult | None, str | None]:
        source = SourceDefinition(str(row["source_id"]), str(row["name"]), str(row["url"]), str(row["category"]), str(row["kind"]), bool(row["enabled"]))
        try:
            result = fetch_feed(source, timeout=self.fetch_timeout, item_limit=self.item_limit, etag=str(row.get("etag") or ""), last_modified=str(row.get("last_modified") or ""))
            return row, result, None
        except Exception as exc:
            return row, None, f"{type(exc).__name__}: {str(exc)[:400]}"

    def _search(self) -> list[Article]:
        if not self.searxng_url:
            return []
        articles: list[Article] = []
        for query_entry in self.search_queries:
            query, category = query_entry.split("|", 1)
            params = urllib.parse.urlencode({"q": query, "format": "json", "language": "en"})
            request = urllib.request.Request(f"{self.searxng_url}/search?{params}", headers={"Accept": "application/json", "User-Agent": "Ariadne Discovery Engine/0.1"})
            try:
                with urllib.request.urlopen(request, timeout=self.fetch_timeout) as response:
                    payload = json.loads(response.read(2_000_000).decode("utf-8"))
            except Exception:
                continue
            results = payload.get("results") if isinstance(payload, dict) else []
            for item in results if isinstance(results, list) else []:
                if not isinstance(item, dict) or not item.get("url") or not item.get("title"):
                    continue
                url = str(item["url"])
                domain = source_domain(url) or "search result"
                source_id = stable_source_id(domain, urlsplit_origin(url))
                articles.append(Article.from_candidate({"title": item.get("title"), "url": url, "summary": item.get("content") or item.get("snippet"), "content": item.get("content") or "", "source_id": source_id, "source_name": domain, "source_url": urlsplit_origin(url), "category": category, "published_at": item.get("publishedDate"), "image_url": item.get("thumbnail")}, None))
        return articles

    def _build_stories(self) -> list[dict[str, Any]]:
        articles = self.store.recent_articles(lookback_days=self.lookback_days)
        clusters = cluster_articles(articles)
        stories = rank_stories(story_from_cluster(cluster) for cluster in clusters if cluster)
        selected = diversify(stories, limit=self.max_stories)
        self.store.save_stories(selected)
        return selected

    def _push(self, stories: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.signal_service_url or not stories:
            return {"enabled": bool(self.signal_service_url), "attempted": False, "accepted": 0}
        candidates = []
        for story in stories:
            evidence_lines = "\n".join(f"- {item.get('source_name')}: {item.get('title')} ({item.get('url')})" for item in story.get("evidence", [])[:12])
            candidates.append({"title": story["title"], "url": story["url"], "summary": story["summary"], "content": story["summary"] + ("\n\nReports found:\n" + evidence_lines if evidence_lines else ""), "source_name": "Ariadne Discovery Engine", "source_url": self.signal_service_url, "category": _signal_category(story), "published_at": story.get("published_at"), "image_url": story.get("image_url", ""), "provenance": {"discovery": {"story_id": story["story_id"], "article_count": story.get("article_count", 0), "source_count": story.get("source_count", 0), "source_names": story.get("source_names", []), "source_domains": story.get("source_domains", []), "evidence": story.get("evidence", []), "rank_score": story.get("rank_score", 0), "discovery_category": story.get("category", "Main News Feed"), "representative_url": story.get("representative_url", story["url"])}}})
        accepted = 0
        duplicates = 0
        failed_batches = 0
        attempts = 0
        errors: list[str] = []
        batches = [candidates[index:index + self.push_batch_size] for index in range(0, len(candidates), self.push_batch_size)]
        for batch_number, batch in enumerate(batches, 1):
            payload = json.dumps({"items": batch, "source_name": "Ariadne Discovery Engine", "source_url": self.signal_service_url}).encode("utf-8")
            request = urllib.request.Request(self.signal_service_url + "/v1/intake/candidates", data=payload, headers={"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "Ariadne Discovery Engine/0.1"}, method="POST")
            completed = False
            last_error = ""
            for retry in range(self.push_retries + 1):
                attempts += 1
                try:
                    with urllib.request.urlopen(request, timeout=self.push_timeout) as response:
                        result = json.loads(response.read(2_000_000).decode("utf-8"))
                    if not isinstance(result, dict) or not result.get("ok", True):
                        raise RuntimeError(str(result.get("message") if isinstance(result, dict) else "Signal Service returned an invalid response.")[:400])
                    accepted += int(result.get("accepted", 0))
                    duplicates += int(result.get("duplicates", 0))
                    completed = True
                    break
                except (OSError, TimeoutError, urllib.error.URLError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
                    last_error = f"{type(exc).__name__}: {str(exc)[:400]}"
                    if retry < self.push_retries:
                        time.sleep(min(2 ** retry, 4))
            if not completed:
                failed_batches += 1
                errors.append(f"batch {batch_number}/{len(batches)}: {last_error}")
        result = {
            "enabled": True,
            "attempted": True,
            "ok": failed_batches == 0,
            "accepted": accepted,
            "duplicates": duplicates,
            "batch_count": len(batches),
            "batch_size": self.push_batch_size,
            "attempts": attempts,
            "failed_batches": failed_batches,
        }
        if errors:
            result["errors"] = errors
            result["error"] = errors[-1]
        return result

    def refresh(self) -> dict[str, Any]:
        with self._lock:
            if self._refresh_running:
                return {"ok": False, "running": True, **self._last_result}
            self._refresh_running = True
            self._last_attempt_at = utc_now()
        accepted = 0
        failures = []
        successful_sources = 0
        try:
            due = self.store.due_sources(limit=len(self.sources))
            with ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="discovery-fetch") as executor:
                futures = [executor.submit(self._fetch_one, row) for row in due]
                for future in as_completed(futures):
                    row, result, error = future.result()
                    if error or result is None:
                        self.store.source_result(str(row["source_id"]), success=False, error=error or "No result", interval_seconds=self.interval_seconds)
                        failures.append({"source": row["name"], "url": row["url"], "error": error or "No result"})
                        continue
                    successful_sources += 1
                    articles = [Article.from_candidate(candidate) for candidate in result.candidates]
                    accepted += self.store.upsert_articles(articles)
                    self.store.source_result(str(row["source_id"]), success=True, item_count=int(row.get("item_count") or 0) if result.not_modified else len(articles), etag=result.etag, last_modified=result.last_modified, interval_seconds=self.interval_seconds)
            search_articles = self._search()
            if search_articles:
                accepted += self.store.upsert_articles(search_articles)
            stories = self._build_stories()
            push = self._push(stories)
            collection_ok = bool(successful_sources or search_articles or (not due and stories))
            result = {"ok": collection_ok, "attempted_sources": len(due), "successful_sources": successful_sources, "accepted_articles": accepted, "search_articles": len(search_articles), "story_count": len(stories), "failures": failures, "push": push, "generated_at": utc_now()}
            with self._lock:
                if successful_sources or search_articles:
                    self._last_success_at = utc_now()
                self._last_result = result
                self._last_push = push
            return result
        finally:
            with self._lock:
                self._refresh_running = False

    def start_background_refresh(self) -> threading.Thread:
        def loop() -> None:
            while True:
                try:
                    self.refresh()
                except Exception as exc:
                    with self._lock:
                        self._last_result = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:400]}"}
                time.sleep(self.interval_seconds)
        worker = threading.Thread(target=loop, name="ariadne-discovery-refresh", daemon=True)
        worker.start()
        return worker

    def health(self) -> dict[str, Any]:
        with self._lock:
            result = dict(self._last_result)
            running = self._refresh_running
            attempt = self._last_attempt_at
            success = self._last_success_at
        counts = self.store.counts()
        state = "healthy" if success and not result.get("failures") else "attention" if result else "starting"
        return {"ok": True, "service": "ariadne-discovery-service", "version": "0.1.0", "state": state, "refresh_running": running, "refresh_seconds": self.interval_seconds, "last_attempt_at": attempt, "last_success_at": success, "source_count": counts["sources"], "article_count": counts["articles"], "story_count": counts["stories"], "search_enabled": bool(self.searxng_url), "signal_service_configured": bool(self.signal_service_url), "last_refresh": result, "last_push": self._last_push}

    def stories(self, limit: int = 80) -> list[dict[str, Any]]:
        return self.store.stories(limit=limit)


def urlsplit_origin(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


_VALID_HANDOFF_CATEGORIES = {"Main News Feed", "Thailand Focus", "AI Watch"}
_AI_MEANING_PATTERNS = (
    r"\bartificial intelligence\b",
    r"\ba\.i\.",
    r"\bai\b",
    r"\bmachine learning\b",
    r"\bdeep learning\b",
    r"\blarge language models?\b",
    r"\bllms?\b",
    r"\bgenerative ai\b",
    r"\bgenai\b",
    r"\bfoundation models?\b",
    r"\bopenai\b",
    r"\banthropic\b",
    r"\bdeepmind\b",
    r"\bhugging face\b",
    r"\bchatgpt\b",
    r"\bgemini\b",
    r"\bclaude\b",
    r"\bcopilot\b",
    r"\bgpt(?:[- ]?\d+(?:\.\d+)?)?\b",
    r"\bneural networks?\b",
    r"\bmodel training\b",
    r"\binference\b",
    r"\brobotics?\b",
)
_THAILAND_MEANING_PATTERNS = (
    r"\bthailand\b",
    r"\bthai\b",
    r"\bbangkok\b",
    r"\bphuket\b",
    r"\bpattaya\b",
    r"\bchiang mai\b",
    r"\bthai pbs\b",
    r"\bbangkok post\b",
    r"\bbaht\b",
    r"\bthai visa\b",
    r"\bthai immigration\b",
    r"\bexpat(?:s|riate)?\b",
)


def _story_meaning_text(story: dict[str, Any]) -> str:
    values = [story.get("title"), story.get("summary"), story.get("content")]
    values.extend(story.get("source_names", []) if isinstance(story.get("source_names"), list) else [])
    evidence = story.get("evidence", [])
    if isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, dict):
                values.extend((item.get("title"), item.get("source_name")))
    return " ".join(str(value or "") for value in values).casefold()


def _matches_meaning(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def _signal_category(story: dict[str, Any]) -> str:
    """Map stories to stable UI views while retaining explicit valid labels."""
    categories = {str(value) for value in story.get("categories", [])}
    declared = str(story.get("category") or "")
    explicit = categories & _VALID_HANDOFF_CATEGORIES
    if declared in _VALID_HANDOFF_CATEGORIES:
        return declared
    if explicit:
        if "Thailand Focus" in explicit:
            return "Thailand Focus"
        if "AI Watch" in explicit:
            return "AI Watch"
        return "Main News Feed"
    meaning = _story_meaning_text(story)
    if _matches_meaning(meaning, _THAILAND_MEANING_PATTERNS):
        return "Thailand Focus"
    if _matches_meaning(meaning, _AI_MEANING_PATTERNS):
        return "AI Watch"
    return "Main News Feed"


__all__ = ["DiscoveryEngine"]
