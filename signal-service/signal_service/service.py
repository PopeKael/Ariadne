"""Signal refinement pipeline and cache lifecycle."""
from __future__ import annotations

import os
import json
import hashlib
import math
import mimetypes
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit, urlencode
from urllib.request import Request, urlopen

from .diagnostics import emit_diagnostic
from .feeds import FeedDefinition, configured_feeds, fetch_feed
from .models import Signal, canonical_url, normalize_candidate, utc_now
from .ranking import BasicRanker, SignalRanker
from .store import SignalStore
from .inference import InferenceRegistry, ProviderUnavailable


class _ImageMetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.og_image = ""
        self.og_image_url = ""
        self.og_secure_image = ""
        self.twitter_image = ""
        self.twitter_src_image = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "meta":
            return
        values = {str(key).casefold(): str(value or "").strip() for key, value in attrs}
        key = values.get("property", "").casefold() or values.get("name", "").casefold()
        content = values.get("content", "").strip()
        if not content:
            return
        if key == "og:image" and not self.og_image:
            self.og_image = content
        elif key == "og:image:url" and not self.og_image_url:
            self.og_image_url = content
        elif key == "og:image:secure_url" and not self.og_secure_image:
            self.og_secure_image = content
        elif key == "twitter:image" and not self.twitter_image:
            self.twitter_image = content
        elif key == "twitter:image:src" and not self.twitter_src_image:
            self.twitter_src_image = content


class _GoogleNewsParamsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.params: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "div":
            return
        values = {str(key).casefold(): str(value or "").strip() for key, value in attrs}
        if values.get("data-n-a-id"):
            for key in ("data-n-a-id", "data-n-a-ts", "data-n-a-sg"):
                if values.get(key):
                    self.params[key] = values[key]


def _google_news_article_id(url: str) -> str:
    parts = urlsplit(url)
    if (parts.hostname or "").casefold() != "news.google.com":
        return ""
    segments = [segment for segment in parts.path.split("/") if segment]
    for marker in ("articles", "read"):
        if marker in segments:
            index = segments.index(marker)
            return segments[index + 1] if index + 1 < len(segments) else ""
    return ""


def _resolve_google_news_url(url: str, timeout: float) -> str:
    article_id = _google_news_article_id(url)
    if not article_id:
        return url
    page_request = Request(
        f"https://news.google.com/articles/{article_id}?hl=en-US&gl=US&ceid=US:en",
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cookie": "CONSENT=PENDING+987",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        },
    )
    with urlopen(page_request, timeout=timeout) as response:
        raw = response.read(2_000_000)
        charset = response.headers.get_content_charset() or "utf-8"
    parser = _GoogleNewsParamsParser()
    parser.feed(raw.decode(charset, errors="replace"))
    source = parser.params
    if not all(source.get(key) for key in ("data-n-a-id", "data-n-a-ts", "data-n-a-sg")):
        return url
    inner = [
        "garturlreq",
        [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
        source["data-n-a-id"],
        int(source["data-n-a-ts"]),
        source["data-n-a-sg"],
    ]
    articles_request = ["Fbv4je", json.dumps(inner, separators=(",", ":"))]
    body = urlencode({"f.req": json.dumps([[articles_request]], separators=(",", ":"))}).encode("utf-8")
    batch_request = Request(
        "https://news.google.com/_/DotsSplashUi/data/batchexecute",
        data=body,
        headers={
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Cookie": "CONSENT=PENDING+987",
            "Origin": "https://news.google.com",
            "Referer": "https://news.google.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            "X-Same-Domain": "1",
        },
    )
    with urlopen(batch_request, timeout=timeout) as response:
        result = response.read(200_000).decode(response.headers.get_content_charset() or "utf-8", errors="replace")
    json_part = result.split("\n\n", 1)[1]
    outer = json.loads(json_part)
    decoded = json.loads(outer[0][2])
    resolved = decoded[1] if isinstance(decoded, list) and len(decoded) > 1 else ""
    return resolved if isinstance(resolved, str) and resolved.startswith(("http://", "https://")) else url


_ARTICLE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
    "Cache-Control": "no-cache",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 AriadneSignal/0.2",
}


# Only remove parameters that are unambiguously used for campaign/referrer
# tracking. Article-identifying or publisher-specific query parameters remain
# untouched.
_KNOWN_TRACKING_PARAMETERS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "gclid", "dclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "traffic_source",
})


def _without_known_tracking_parameters(url: str) -> str:
    parts = urlsplit(url)
    if not parts.query:
        return url
    query = parse_qsl(parts.query, keep_blank_values=True)
    filtered = [(key, value) for key, value in query if key.casefold() not in _KNOWN_TRACKING_PARAMETERS]
    if len(filtered) == len(query):
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(filtered, doseq=True), parts.fragment))


def _fetch_article_image_once(article_url: str, timeout: float) -> str:
    request_headers = dict(_ARTICLE_HEADERS)
    request_headers["Referer"] = article_url
    request = Request(article_url, headers=request_headers)
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(2_000_000)
        charset = response.headers.get_content_charset() or "utf-8"
        final_url = response.geturl() if hasattr(response, "geturl") else article_url
    parser = _ImageMetaParser()
    parser.feed(raw.decode(charset, errors="replace"))
    candidate = parser.og_image or parser.og_image_url or parser.og_secure_image or parser.twitter_image or parser.twitter_src_image
    if not candidate:
        return ""
    resolved = canonical_url(urljoin(final_url, candidate))
    return resolved if resolved.startswith(("http://", "https://")) else ""


def fetch_article_image(url: str, timeout: float = 2.0) -> str:
    """Resolve an article's preferred social image from its bounded ``<head>``."""
    bounded_timeout = max(0.5, min(float(timeout), 10.0))
    article_url = _resolve_google_news_url(url, bounded_timeout)
    fetch_timeout = max(0.5, min(float(timeout), 10.0))
    image_url = _fetch_article_image_once(article_url, fetch_timeout)
    if image_url:
        return image_url
    fallback_url = _without_known_tracking_parameters(article_url)
    if fallback_url == article_url:
        return ""
    return _fetch_article_image_once(fallback_url, fetch_timeout)


_IMAGE_RETRY_DELAYS = (300, 1_800, 10_800, 43_200)
_IMAGE_MAX_BYTES = 12_000_000
_IMAGE_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "image/gif": ".gif", "image/avif": ".avif", "image/svg+xml": ".svg",
}


def _retry_delay(attempt_count: int) -> int:
    return _IMAGE_RETRY_DELAYS[min(max(0, attempt_count), len(_IMAGE_RETRY_DELAYS) - 1)]


def _retry_is_due(state: dict[str, Any] | None) -> bool:
    if not state:
        return True
    next_retry = str(state.get("next_retry_at") or "")
    if not next_retry:
        return not bool(state.get("cache_path"))
    try:
        parsed = datetime.fromisoformat(next_retry.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= parsed.astimezone(timezone.utc)


def _image_extension(image_url: str, content_type: str) -> str:
    mime = content_type.casefold().split(";", 1)[0].strip()
    if mime in _IMAGE_MIME_EXTENSIONS:
        return _IMAGE_MIME_EXTENSIONS[mime]
    return mimetypes.guess_extension(mime) or Path(urlsplit(image_url).path).suffix.casefold() or ".img"


def fetch_remote_image(image_url: str, source_url: str, timeout: float = 5.0) -> tuple[bytes, str]:
    """Fetch one selected image with browser-like headers and a hard byte cap."""
    bounded_timeout = max(0.5, min(float(timeout), 15.0))
    request = Request(
        image_url,
        headers={
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8",
            "Referer": source_url,
            "User-Agent": _ARTICLE_HEADERS["User-Agent"],
        },
    )
    with urlopen(request, timeout=bounded_timeout) as response:
        headers = response.headers
        content_type = str(headers.get("Content-Type") or "") if hasattr(headers, "get") else ""
        if not content_type and hasattr(headers, "get_content_type"):
            content_type = str(headers.get_content_type() or "")
        body = response.read(_IMAGE_MAX_BYTES + 1)
    if len(body) > _IMAGE_MAX_BYTES:
        raise ValueError(f"image exceeds {_IMAGE_MAX_BYTES} byte limit")
    extension = _image_extension(image_url, content_type)
    if content_type and not content_type.startswith("image/"):
        raise ValueError(f"remote image returned non-image content type {content_type}")
    if not content_type and extension == ".img":
        raise ValueError(f"remote image returned non-image content type {content_type or 'unknown'}")
    if not body:
        raise ValueError("remote image returned an empty body")
    return body, content_type.split(";", 1)[0].strip() or "application/octet-stream"


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
    EMBEDDING_BATCH_SIZE = 32
    MIN_SEMANTIC_MATCH_SCORE = 0.58

    """Portable service object used by both the HTTP server and tests."""

    def __init__(self, database_path: str | Path, feeds: Iterable[FeedDefinition] | None = None, *, ranker: SignalRanker | None = None, feed_timeout: float = 15.0, item_limit: int = 20, inference: InferenceRegistry | None = None):
        self.store = SignalStore(database_path)
        self.image_cache_dir = Path(os.environ.get("SIGNAL_SERVICE_IMAGE_CACHE_DIR", str(Path(database_path).parent / "images")))
        self.image_cache_dir.mkdir(parents=True, exist_ok=True)
        if feeds is not None:
            self.feeds = list(feeds)
        elif os.environ.get("SIGNAL_SERVICE_DISABLE_BUILTIN_FEEDS", "").casefold() in {"1", "true", "yes", "on"}:
            # Discovery Engine owns collection in the NAS deployment. Keep
            # this opt-in so existing standalone Signal Service deployments
            # retain their historical feed behaviour.
            self.feeds = []
        elif os.environ.get("SIGNAL_SERVICE_FEEDS", "").strip():
            self.feeds = configured_feeds()
        else:
            self.feeds = [FeedDefinition(item["name"], item["endpoint"], item["category"]) for item in self.store.enabled_rss_sources()]
        if os.environ.get("SIGNAL_SERVICE_DISABLE_BUILTIN_FEEDS", "").casefold() in {"1", "true", "yes", "on"}:
            # Final guard for the Hera deployment: intake-only means no local
            # feed collector regardless of persisted source configuration.
            self.feeds = []
        self.ranker = ranker or BasicRanker()
        self.inference = inference or InferenceRegistry(Path(database_path).parent / "inference.json")
        self.feed_timeout = max(1.0, float(feed_timeout))
        self.item_limit = max(1, min(int(item_limit), 100))
        self.briefing_pool_limit = max(40, min(int(os.environ.get("SIGNAL_SERVICE_BRIEFING_POOL", "240")), 300))
        self._lock = threading.RLock()
        self._semantic_lock = threading.RLock()
        self._last_attempt_at: str | None = None
        self._last_success_at: str | None = None
        self._last_collection_ok: bool | None = None
        self._last_errors: list[dict[str, str]] = []
        self._last_source_status: list[dict[str, Any]] = []
        self._refresh_running = False
        self._successful_feed_collection_logged = False
        self._semantic_status: dict[str, Any] = {"state": "pending", "provider_id": None, "model_id": None, "embedded_signals": 0, "embedded_interests": 0, "match_count": 0, "error": ""}

    def _cache_image(self, image_url: str, source_url: str) -> tuple[str, str]:
        body, mime = fetch_remote_image(
            image_url,
            source_url,
            timeout=float(os.environ.get("SIGNAL_SERVICE_IMAGE_TIMEOUT_SECONDS", "5")),
        )
        suffix = _image_extension(image_url, mime)
        filename = "image-" + hashlib.sha256(image_url.encode("utf-8")).hexdigest()[:40] + suffix
        target = self.image_cache_dir / filename
        if not target.is_file():
            temporary = target.with_name(f".{target.name}.{threading.get_ident()}.tmp")
            try:
                temporary.write_bytes(body)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        return filename, mime

    def cached_image(self, filename: str) -> tuple[bytes, str] | None:
        if not filename or Path(filename).name != filename or not filename.startswith("image-"):
            return None
        target = (self.image_cache_dir / filename).resolve()
        try:
            target.relative_to(self.image_cache_dir.resolve())
            body = target.read_bytes()
        except (OSError, ValueError):
            return None
        if len(body) > _IMAGE_MAX_BYTES:
            return None
        suffix = target.suffix.casefold()
        mime = next((value for value, extension in _IMAGE_MIME_EXTENSIONS.items() if extension == suffix), None) or "application/octet-stream"
        return body, mime

    def close(self) -> None:
        self.store.close()

    def _build_cached_briefing(self, collection: dict[str, Any]) -> dict[str, Any]:
        profile = self.store.learned_preferences()
        profile["semantic_state"] = self._semantic_status.get("state")
        profile["semantic_interest_count"] = len(self.store.list_interests(active_only=True))
        recent = self.store.recent(limit=self.briefing_pool_limit)
        profile["feedback"] = self.store.latest_feedback([signal.signal_id for signal in recent])
        ranked = self.ranker.rank(recent, limit=self.briefing_pool_limit, profile=profile)
        return self.store.save_briefing(ranked, collection)

    @staticmethod
    def _signal_text(signal: Signal) -> str:
        # Source and category are downstream metadata, not article meaning.
        # Including them makes every item from the AI Watch feed look related.
        parts = [signal.title, signal.summary, signal.content[:4_000]]
        return "\n".join(part.strip() for part in parts if part and part.strip())[:8_000]

    @staticmethod
    def _interest_text(interest: dict[str, Any]) -> str:
        aliases = ", ".join(str(item) for item in interest.get("aliases", []) if str(item).strip())
        return "\n".join(part for part in (interest.get("name"), interest.get("description"), aliases) if str(part or "").strip())[:4_000]

    def _embed_texts(self, texts: list[str], provider: Any) -> list[list[float]]:
        """Keep provider requests bounded; Ollama rejects oversized batches."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.EMBEDDING_BATCH_SIZE):
            batch = texts[start:start + self.EMBEDDING_BATCH_SIZE]
            result = self.inference.embed_many(batch, provider)
            if len(result) != len(batch):
                raise ProviderUnavailable("Embedding provider returned an incompatible batch response.")
            vectors.extend(result)
        return vectors

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if not left or len(left) != len(right):
            return 0.0
        product = sum(a * b for a, b in zip(left, right))
        norm_left = math.sqrt(sum(a * a for a in left))
        norm_right = math.sqrt(sum(b * b for b in right))
        return product / (norm_left * norm_right) if norm_left and norm_right else 0.0

    def _semantic_enrich(self, signals: Iterable[Signal] | None = None) -> dict[str, Any]:
        with self._semantic_lock:
            return self._semantic_enrich_impl(signals)

    def _semantic_enrich_impl(self, signals: Iterable[Signal] | None = None) -> dict[str, Any]:
        selected_provider = self.inference.route("embedding")
        if selected_provider is None:
            self._semantic_status = {"state": "unavailable", "provider_id": None, "model_id": None, "embedded_signals": 0, "embedded_interests": 0, "match_count": 0, "error": "No compatible embedding provider is configured."}
            return self._semantic_status
        provider_state = self.inference.state(selected_provider)
        if provider_state in {"Missing", "Unavailable"}:
            self._semantic_status = {"state": provider_state.casefold(), "provider_id": selected_provider.provider_id, "model_id": selected_provider.model_id, "location": selected_provider.location, "embedded_signals": 0, "embedded_interests": 0, "match_count": 0, "error": f"Embedding provider is {provider_state.casefold()}."}
            return self._semantic_status
        interests = self.store.list_interests(active_only=True)
        signal_list = list(signals) if signals is not None else self.store.recent(limit=500)
        interest_vectors: dict[str, list[float]] = {}
        interest_pending: list[tuple[dict[str, Any], str]] = []
        for interest in interests:
            text = self._interest_text(interest)
            source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            cached = self.store.embedding("interest", interest["interest_id"], selected_provider.provider_id, selected_provider.model_id, selected_provider.embedding_version)
            if cached and cached["source_text_hash"] == source_hash and isinstance(cached.get("vector"), list):
                interest_vectors[interest["interest_id"]] = cached["vector"]
            else:
                interest_pending.append((interest, source_hash))
        signal_pending: list[tuple[Signal, str]] = []
        signal_vectors: dict[str, list[float]] = {}
        for signal in signal_list:
            text = self._signal_text(signal)
            source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            cached = self.store.embedding("signal", signal.signal_id, selected_provider.provider_id, selected_provider.model_id, selected_provider.embedding_version)
            if cached and cached["source_text_hash"] == source_hash and isinstance(cached.get("vector"), list):
                signal_vectors[signal.signal_id] = cached["vector"]
            else:
                signal_pending.append((signal, source_hash))
        embedded_interests = 0
        embedded_signals = 0
        try:
            if interest_pending:
                vectors = self._embed_texts([self._interest_text(item) for item, _ in interest_pending], selected_provider)
                for (interest, source_hash), vector in zip(interest_pending, vectors):
                    self.store.save_embedding("interest", interest["interest_id"], selected_provider.provider_id, selected_provider.model_id, len(vector), selected_provider.embedding_version, source_hash, vector)
                    interest_vectors[interest["interest_id"]] = vector
                    embedded_interests += 1
            if signal_pending:
                vectors = self._embed_texts([self._signal_text(item) for item, _ in signal_pending], selected_provider)
                for (signal, source_hash), vector in zip(signal_pending, vectors):
                    self.store.save_embedding("signal", signal.signal_id, selected_provider.provider_id, selected_provider.model_id, len(vector), selected_provider.embedding_version, source_hash, vector)
                    signal_vectors[signal.signal_id] = vector
                    embedded_signals += 1
        except ProviderUnavailable as exc:
            self._semantic_status = {"state": "unavailable", "provider_id": selected_provider.provider_id, "model_id": selected_provider.model_id, "location": selected_provider.location, "embedded_signals": embedded_signals, "embedded_interests": embedded_interests, "match_count": 0, "error": str(exc)[:500]}
            return self._semantic_status
        match_count = 0
        for signal in signal_list:
            signal_vector = signal_vectors.get(signal.signal_id)
            if not signal_vector:
                continue
            matches = []
            for interest in interests:
                interest_vector = interest_vectors.get(interest["interest_id"])
                if not interest_vector or len(interest_vector) != len(signal_vector):
                    continue
                score = self._cosine(signal_vector, interest_vector)
                if score >= self.MIN_SEMANTIC_MATCH_SCORE:
                    matches.append({"interest_id": interest["interest_id"], "semantic_score": score})
            matches.sort(key=lambda item: (-float(item["semantic_score"]), item["interest_id"]))
            # Keep every qualifying configured interest: a similarity-only
            # top-six cut could discard the user's highest-priority match.
            self.store.replace_signal_matches(signal.signal_id, matches, selected_provider.provider_id, selected_provider.model_id, selected_provider.embedding_version)
            match_count += len(matches)
        embedding_counts = self.store.embedding_counts(selected_provider.provider_id, selected_provider.model_id, selected_provider.embedding_version)
        self._semantic_status = {"state": "healthy", "provider_id": selected_provider.provider_id, "model_id": selected_provider.model_id, "location": selected_provider.location, "embedded_signals": embedded_signals, "embedded_interests": embedded_interests, "stored_signal_embeddings": embedding_counts["signals"], "stored_interest_embeddings": embedding_counts["interests"], "match_count": match_count, "error": ""}
        return self._semantic_status

    def ingest_candidates(self, candidates: Iterable[object], *, default_source_name: str = "External candidate producer", default_source_url: str = "", ingest_type: str = "candidate", adapter: str = "external", default_category: str = "Main News Feed") -> dict[str, Any]:
        if ingest_type == "candidate_intake":
            try:
                self.store.upsert_source({"name": default_source_name, "endpoint": default_source_url or "n8n://candidate-intake", "adapter_type": adapter, "category": default_category, "enabled": True})
            except ValueError:
                pass
        accepted = 0
        duplicates = 0
        rejected = 0
        errors: list[str] = []
        for candidate in candidates:
            try:
                signal = normalize_candidate(candidate, default_source_name=default_source_name, default_source_url=default_source_url, ingest_type=ingest_type, adapter=adapter, default_category=default_category)
                prepared = candidate.get("prepared_article", {}) if isinstance(candidate, dict) else {}
                prepared = prepared if isinstance(prepared, dict) and prepared.get("prepared_version") == 1 else {}
                if prepared:
                    # Upstream owns publisher fetching; preserve its diagnostic record.
                    diagnostics = {key: value for key, value in prepared.items() if key != "extracted_text"}
                    signal = replace(signal, image_url=str(prepared.get("image_url") or ""), provenance={**signal.provenance, "article_preparation": diagnostics})
                existing = self.store.image_enrichment_state(signal.dedupe_key)
                image_error = ""
                image_status = "not_attempted"
                image_cache_path = ""
                image_cache_mime = ""
                image_source_url = str(signal.image_url or (existing or {}).get("image_url") or "")
                can_attempt = bool(prepared.get("image_cache_url")) or _retry_is_due(existing)
                existing_cache = str((existing or {}).get("cache_path") or "")
                existing_cache_file = self.image_cache_dir / existing_cache if existing_cache else None
                if existing_cache and (existing_cache_file is None or not existing_cache_file.is_file()):
                    can_attempt = True
                if existing and existing_cache and existing_cache_file.is_file():
                    can_attempt = False
                    signal = replace(signal, image_url=str(existing.get("image_url") or signal.image_url or ""))
                    image_status = str(existing.get("status") or "cached")
                elif can_attempt:
                    try:
                        if prepared:
                            image_source_url = str(prepared.get("image_url") or "")
                            local_url = str(prepared.get("image_cache_url") or "")
                            if local_url:
                                image_cache_path, image_cache_mime = self._cache_image(local_url, str(prepared.get("final_url") or signal.url))
                                image_status = "cached"
                            else:
                                image_status = str(prepared.get("image_status") or "no_url_found")
                                image_error = str(prepared.get("image_error") or "")
                        elif not image_source_url:
                            image_source_url = fetch_article_image(signal.url, timeout=float(os.environ.get("SIGNAL_SERVICE_IMAGE_TIMEOUT_SECONDS", "5")))
                        if not prepared:
                            if image_source_url:
                                try:
                                    image_cache_path, image_cache_mime = self._cache_image(image_source_url, signal.url)
                                except Exception:
                                    # A broken feed hint must not poison future lookups.
                                    replacement = fetch_article_image(signal.url, timeout=float(os.environ.get("SIGNAL_SERVICE_IMAGE_TIMEOUT_SECONDS", "5")))
                                    if not replacement or replacement == image_source_url:
                                        raise
                                    image_source_url = replacement
                                    image_cache_path, image_cache_mime = self._cache_image(image_source_url, signal.url)
                                image_status = "cached"
                            else:
                                image_status = "no_url_found"
                    except Exception as exc:
                        image_error = str(exc)
                        image_status = "remote_image_failed"
                    signal = replace(signal, image_url=image_source_url)
                elif existing:
                    signal = replace(signal, image_url=str(existing.get("image_url") or signal.image_url or ""))
                    image_status = str(existing.get("status") or "not_attempted")
                stored, created = self.store.upsert(signal)
                if can_attempt and image_status != "not_attempted":
                    attempt_count = int((existing or {}).get("attempt_count") or 0)
                    next_retry_at = None if image_status == "cached" else datetime.fromtimestamp(time.time() + _retry_delay(attempt_count), timezone.utc).isoformat()
                    self.store.mark_image_enrichment(
                        stored.signal_id,
                        image_url=image_source_url,
                        cache_path=image_cache_path,
                        cache_mime=image_cache_mime,
                        status=image_status,
                        error=image_error,
                        next_retry_at=next_retry_at,
                    )
                    emit_diagnostic(
                        "image_enrichment_completed",
                        signal_id=stored.signal_id,
                        status=image_status,
                        source_url_found=bool(image_source_url),
                        attempt_count=attempt_count + 1,
                        next_retry_at=next_retry_at,
                        error=image_error[:180],
                    )
                accepted += 1
                if not created:
                    duplicates += 1
            except ValueError as exc:
                rejected += 1
                errors.append(str(exc))
        collection = {"mode": ingest_type, "accepted": accepted, "duplicates": duplicates, "rejected": rejected, "errors": errors}
        if accepted:
            self._semantic_enrich()
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
        if not self.feeds:
            # Hera is intake-only. An empty local collection is not a failed
            # Discovery intake and must not overwrite the last success state.
            cached = self.store.latest_briefing()
            return {"ok": bool(cached), "intake_only": True, "briefing": cached}
        # Hera receives candidates from Discovery/n8n rather than collecting
        # local RSS feeds. A scheduled refresh still runs for that deployment,
        # but an empty feed set is an intentional intake-only mode, not a
        # failed collection. Preserve the last successful intake/briefing
        # state so the health indicator does not turn red every 15 minutes.
        if not self.feeds:
            return {
                "ok": True,
                "mode": "intake_only",
                "accepted": 0,
                "duplicates": 0,
                "sources": [],
                "errors": [],
                "briefing": self.store.latest_briefing(),
            }
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
                    self.store.update_source_status(feed.name, state="healthy", item_count=len(candidates))
                except Exception as exc:  # one bad source must not stop the refinery
                    detail = str(exc)[:500]
                    errors.append({"source": feed.name, "url": feed.url, "error": detail})
                    source_status.append({"name": feed.name, "url": feed.url, "state": "attention", "error": detail})
                    self.store.update_source_status(feed.name, state="attention", error=detail)
            successful_sources = sum(1 for item in source_status if item.get("state") == "healthy")
            collection = {"mode": "feeds", "attempted_at": attempted_at, "sources": source_status, "successful_sources": successful_sources, "accepted": accepted, "duplicates": duplicates, "errors": errors}
            if successful_sources:
                self._semantic_enrich()
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

    def briefing(self, limit: int = 100) -> dict[str, Any] | None:
        cached = self.store.latest_briefing()
        if cached is None:
            self.refresh()
            cached = self.store.latest_briefing()
        if cached is None:
            return None
        result = dict(cached)
        result["signals"] = list(cached.get("signals", []))[: max(1, min(int(limit), 200))]
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
        semantic_state = dict(self._semantic_status)
        if semantic_state.get("state") in {"unavailable", "missing"} and state == "healthy":
            state = "attention"
        return {"ok": True, "service": "ariadne-signal-service", "version": "0.2.0", "environment": os.environ.get("ARIADNE_ENVIRONMENT", "unknown"), "instance": os.environ.get("ARIADNE_INSTANCE", "signal"), "build_sha": os.environ.get("ARIADNE_BUILD_SHA", "unknown"), "state": state, "feeds": [{"name": feed.name, "url": feed.url} for feed in self.feeds], "sources": self.store.list_sources(), "last_attempt_at": attempt, "last_success_at": last_success, "last_success_age_seconds": _iso_age_seconds(last_success), "last_collection_ok": collection_ok, "refresh_running": running, "source_status": source_status, "errors": errors, "cached_briefing": bool(latest), "semantic": semantic_state, "inference": self.inference.snapshot(probe=False), "learned_preferences": self.store.learned_preferences(), "active_interests": [item for item in self.store.list_interests() if item["enabled"]]}

    def record_feedback(self, signal_id: str, value: str, recorded_at: str | None = None) -> dict[str, Any]:
        if value not in {"useful", "interesting", "not_useful"}:
            raise ValueError("Feedback must be useful, interesting, or not_useful")
        result = self.store.record_feedback(signal_id, value, recorded_at)
        result["learned_preferences"] = self.store.rebuild_learned_preferences()
        self._build_cached_briefing({"mode": "feedback", "signal_id": signal_id, "feedback": value})
        return result

    def record_interaction(self, signal_id: str, value: str, recorded_at: str | None = None) -> dict[str, Any]:
        if value != "tldr":
            raise ValueError("Interaction must be tldr")
        return self.store.record_interaction(signal_id, value, recorded_at)

    def add_watchlist_topic(self, topic: str, active: bool = True) -> dict[str, Any]:
        return self.store.upsert_watchlist_topic(topic, active)

    def watchlist_topics(self, active_only: bool = True) -> list[dict[str, Any]]:
        return self.store.watchlist_topics(active_only)

    def interests(self, active_only: bool = False) -> list[dict[str, Any]]:
        return self.store.list_interests(active_only)

    def upsert_interest(self, value: dict[str, Any]) -> dict[str, Any]:
        result = self.store.upsert_interest(value)
        self._semantic_enrich()
        self._build_cached_briefing({"mode": "interest_updated", "interest_id": result["interest_id"]})
        return result

    def sources(self) -> list[dict[str, Any]]:
        return self.store.list_sources()

    def upsert_source(self, value: dict[str, Any]) -> dict[str, Any]:
        result = self.store.upsert_source(value)
        self.feeds = [FeedDefinition(item["name"], item["endpoint"], item["category"]) for item in self.store.enabled_rss_sources()]
        return result

    def delete_source(self, source_id: str) -> bool:
        removed = self.store.delete_source(source_id)
        self.feeds = [FeedDefinition(item["name"], item["endpoint"], item["category"]) for item in self.store.enabled_rss_sources()]
        return removed

    def reset_learned_preferences(self) -> dict[str, Any]:
        return self.store.reset_learned_preferences()

    def start_background_refresh(self, interval_seconds: float | None = None) -> threading.Thread:
        interval = max(60.0, float(interval_seconds if interval_seconds is not None else os.environ.get("SIGNAL_SERVICE_REFRESH_SECONDS", "900")))

        def loop() -> None:
            next_refresh = 0.0
            while True:
                try:
                    if time.monotonic() >= next_refresh:
                        self.refresh()
                        next_refresh = time.monotonic() + interval
                    self.recover_semantics()
                except Exception:
                    pass
                time.sleep(min(30.0, interval))

        worker = threading.Thread(target=loop, name="ariadne-signal-refresh", daemon=True)
        worker.start()
        return worker

    def recover_semantics(self) -> bool:
        """Retry cached matching after the desktop returns, without feed intake."""
        if self._semantic_status.get("state") not in {"pending", "unavailable", "missing", "error"}:
            return False
        cached = self.store.latest_briefing()
        if not cached:
            return False
        with self._lock:
            if self._refresh_running:
                return False
        status = self._semantic_enrich()
        if status.get("state") != "healthy":
            return False
        collection = dict(cached.get("collection") or {})
        collection["semantic_recovered_at"] = utc_now()
        self._build_cached_briefing(collection)
        emit_diagnostic("semantic_recovered", match_count=status.get("match_count"))
        return True


def extract_intake_candidates(payload: object) -> tuple[list[object], dict[str, Any]]:
    """Accept common n8n item wrappers without binding Core to n8n's schema."""
    def unwrap(value: object) -> object:
        if isinstance(value, dict) and isinstance(value.get("json"), dict):
            return value["json"]
        return value

    if isinstance(payload, list):
        return [unwrap(item) for item in payload], {}
    if not isinstance(payload, dict):
        return [], {}
    for key in ("candidates", "items", "signals", "data", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [unwrap(item) for item in value], payload
    if isinstance(payload.get("json"), dict):
        return [payload["json"]], payload
    if any(key in payload for key in ("title", "headline", "link", "url")):
        return [payload], payload
    return [], payload


__all__ = ["SignalService", "extract_intake_candidates", "fetch_article_image"]
