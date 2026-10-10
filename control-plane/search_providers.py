"""Provider-neutral live search and bounded source fetching for Ariadne Home."""
from __future__ import annotations

import base64
import html
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, quote_plus, unquote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from ariadne_config import configuration_path
from source_article import extract_article_text


SEARCH_TIMEOUT_SECONDS = max(2.0, min(float(os.environ.get("ARIADNE_SEARCH_TIMEOUT_SECONDS", "8")), 20.0))
MAX_RESULT_CHARS = 8_000
DEFAULT_RESULT_LIMIT = 5

# Shared by source-query construction and result validation. Conversation and
# recency words must not count as evidence that a result covers the subject.
SEARCH_STOP_WORDS = set("""
a an and are as at be been being but by can could did do does for from had has
have he her his how i if in into is it its may me my not of on or our s she so
that the their them there these they this those to was we were what when where
which who will with would you your okay ari article search web related
information see going need deeper dive please current latest recent news
developments background implications analysis explain compare coverage update
updates further action actions warns warned warning row take unspecified
president public issue two over announces announced new
""".split())


def _subject_tokens(text: str) -> set[str]:
    text = re.sub(r"\b(?:prisoners?[-\s]+of[-\s]+war|captured(?:[-\s]+[A-Za-z]+){0,3}[-\s]+soldiers?|pows?)\b", "pow", text, flags=re.I)
    text = re.sub(r"\b(?:apolog(?:is|iz)(?:e|ed|es|ing)|apolog(?:y|ies))\b", "apology", text, flags=re.I)
    tokens = set()
    for word in re.findall(r"[A-Za-z][A-Za-z0-9]*", text):
        word = word.casefold()
        if word in SEARCH_STOP_WORDS:
            continue
        if len(word) > 6 and word.endswith("ing"):
            word = word[:-3]
        elif len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        tokens.add(word)
    return tokens


def compact_source_query(title: str, lead: str, intent: str, *, task_query: str = "") -> str:
    """Bounded, deterministic source entities + event words + research intent."""
    if task_query.strip():
        # The existing interpreter already resolved the subject and task. Preserve
        # its substantive terms instead of applying the article's two-topic budget.
        terms = []
        for term in task_query.split()[:30]:
            if len(" ".join([*terms, term])) > 200:
                break
            terms.append(term)
        return " ".join(terms)
    source = title + " " + lead
    entities = []
    seen = set()
    for match in re.finditer(r"\b[A-Z][A-Za-z0-9]*(?:[ -]+[A-Z][A-Za-z0-9]*){0,4}\b", source):
        words = [word for word in match.group().split() if word.casefold() not in SEARCH_STOP_WORDS]
        entity = " ".join(words)
        if (entity and entity.casefold() not in seen
                and not _subject_tokens(entity).issubset(_subject_tokens(" ".join(entities)))):
            entities.append(entity)
            seen.add(entity.casefold())
    entity_tokens = _subject_tokens(" ".join(entities))
    normalized = re.sub(r"\b(?:prisoners?[-\s]+of[-\s]+war|pows?)\b", "POW", source, flags=re.I)
    topics = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9]*", normalized):
        folded = word.casefold()
        if folded not in SEARCH_STOP_WORDS and not _subject_tokens(word).issubset(entity_tokens) and folded not in seen:
            topics.append("POW" if folded == "pow" else folded)
            seen.add(folded)
        # Two core event terms avoid reintroducing the body paragraph's many
        # incidental verbs as mandatory search terms.
        if len(topics) >= 2:
            break
    intent_words = []
    if re.search(r"\b(?:latest|current|recent|today|now|going)\b", intent, re.I):
        intent_words.extend(["latest", "developments"])
    if re.search(r"\b(?:deeper|background|related|research)\b", intent, re.I):
        intent_words.append("background")
    if re.search(r"\b(?:going|implications|outlook|next)\b", intent, re.I):
        intent_words.append("implications")
    suffix = " ".join(intent_words)
    # Keep complete terms and leave room for intent within a 200-character cap.
    terms = []
    for part in [*entities, *topics]:
        if len(" ".join([*terms, part, suffix])) <= 200:
            terms.append(part)
    return " ".join([*terms, suffix]).strip()


def _topical_result(query: str, item: dict[str, str]) -> bool:
    subject = _subject_tokens(query)
    if not subject:
        return False
    evidence = _subject_tokens(item.get("title", "") + " " + item.get("snippet", ""))
    # Long snippets can coincidentally contain many query words (e.g. a biography
    # returned for business insurance). Require the result's subject to match too.
    if not subject.intersection(_subject_tokens(item.get("title", ""))):
        return False
    # Require meaningful subject coverage, not a single leading place name.
    required = min(4, max(1, (len(subject) * 3 + 9) // 10))
    if len(subject & evidence) < required:
        return False
    entity_words = _subject_tokens(" ".join(re.findall(r"\b[A-Z][A-Za-z0-9]*\b", query)))
    # A shared topic is insufficient when the requested place/entity is absent:
    # insurance claims in India cannot establish insurance practice in Thailand.
    if entity_words and not entity_words.intersection(evidence):
        return False
    topics = (subject - entity_words) | (subject & {"pow"})
    return not topics or bool(topics & evidence)


@dataclass(frozen=True)
class SearchProvider:
    provider_id: str
    provider_type: str
    endpoint: str
    endpoint_reference: str
    capabilities: tuple[str, ...] = ("search", "fetch")
    location: str = "desktop"
    enabled: bool = True
    priority: int = 100
    health: str = "unknown"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SearchProvider":
        return cls(
            str(value.get("provider_id") or "").strip(),
            str(value.get("provider_type") or "json").strip().casefold(),
            str(value.get("endpoint") or "").strip().rstrip("/"),
            str(value.get("endpoint_reference") or value.get("config_reference") or "saved configuration").strip(),
            tuple(sorted({str(item).strip() for item in value.get("capabilities", ("search", "fetch")) if str(item).strip()})),
            str(value.get("location") or "desktop").strip().casefold(),
            bool(value.get("enabled", True)),
            int(value.get("priority", 100)),
            str(value.get("health") or "unknown").strip().casefold(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_type": self.provider_type,
            "endpoint": self.endpoint,
            "endpoint_reference": self.endpoint_reference,
            "capability": list(self.capabilities),
            "capabilities": list(self.capabilities),
            "location": self.location,
            "enabled": self.enabled,
            "health": self.health,
        }


def _saved_search_providers() -> list[SearchProvider]:
    try:
        saved = json.loads(configuration_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        saved = {}
    evidence = saved.get("evidence", {}) if isinstance(saved, dict) else {}
    values = evidence.get("search_providers") if isinstance(evidence, dict) else None
    if not isinstance(values, list):
        return []
    return [SearchProvider.from_dict(item) for item in values if isinstance(item, dict) and str(item.get("provider_id") or "").strip()]


def default_search_providers() -> list[SearchProvider]:
    configured_endpoint = os.environ.get("ARIADNE_SEARCH_ENDPOINT", "").strip()
    if configured_endpoint:
        provider_type = os.environ.get("ARIADNE_SEARCH_PROVIDER_TYPE", "json").strip().casefold() or "json"
        return [SearchProvider(
            os.environ.get("ARIADNE_SEARCH_PROVIDER", "configured-search").strip() or "configured-search",
            provider_type,
            configured_endpoint,
            "ARIADNE_SEARCH_ENDPOINT",
            ("search", "fetch"),
            os.environ.get("ARIADNE_SEARCH_LOCATION", "desktop").strip().casefold() or "desktop",
            True,
            10,
        )]
    # A configured endpoint always wins.  This public fallback keeps a clean
    # install useful. Prefer Ariadne's available local SearXNG instance; public
    # fallbacks remain available if it is offline.
    return [
        SearchProvider(
            "searxng", "searxng", "http://192.168.1.200:8082/search",
            "built-in local SearXNG", ("search", "fetch"), "nas", True, 10,
        ),
        SearchProvider(
            "wikipedia-api", "json", "https://en.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=8&srsearch={query}",
            "built-in public fallback", ("search", "fetch"), "cloud", True, 80,
        ),
        SearchProvider(
            "bing-html", "html", "https://www.bing.com/search",
            "built-in public fallback", ("search", "fetch"), "cloud", True, 100,
        ),
    ]


class _DuckDuckGoParser:
    def __init__(self) -> None:
        self.results: list[dict[str, str]] = []

    def parse(self, content: str) -> list[dict[str, str]]:
        pattern = re.compile(
            r'<a[^>]+class=["\']result__a["\'][^>]+href=["\'](?P<url>.*?)["\'][^>]*>(?P<title>.*?)</a>(?P<tail>.*?)(?=<a[^>]+class=["\']result__a|</body>)',
            re.IGNORECASE | re.DOTALL,
        )
        for match in pattern.finditer(content):
            url = html.unescape(re.sub(r"<.*?>", "", match.group("url"))).strip()
            title = html.unescape(re.sub(r"<.*?>", "", match.group("title"))).strip()
            snippet_match = re.search(r'class=["\']result__snippet["\'][^>]*>(.*?)</', match.group("tail"), re.IGNORECASE | re.DOTALL)
            snippet = html.unescape(re.sub(r"<.*?>", "", snippet_match.group(1))).strip() if snippet_match else ""
            if url.startswith("//"):
                url = "https:" + url
            if url.startswith(("http://", "https://")):
                self.results.append({"title": re.sub(r"\s+", " ", title), "url": url, "snippet": re.sub(r"\s+", " ", snippet)})
        return self.results


def _bing_url(value: str) -> str:
    parsed = urlsplit(html.unescape(value))
    encoded = parse_qs(parsed.query).get("u", [""])[0]
    if encoded.startswith("a1"):
        try:
            decoded = base64.urlsafe_b64decode(encoded[2:] + "=" * (-len(encoded[2:]) % 4)).decode("utf-8", "replace")
            if decoded.startswith(("http://", "https://")):
                return decoded
        except (ValueError, UnicodeError):
            pass
    return unquote(html.unescape(value))


def _bing_results(content: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    blocks = re.findall(
        r'<li\b[^>]*class=["\'][^"\']*b_algo[^"\']*["\'][^>]*>(.*?)(?=<li\b[^>]*class=["\'][^"\']*b_algo|</ol>)',
        content, re.IGNORECASE | re.DOTALL,
    )
    for block in blocks:
        match = re.search(r'<h2[^>]*>\s*<a[^>]+href=["\'](.*?)["\'][^>]*>(.*?)</a>', block, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        url = _bing_url(match.group(1))
        if not url.startswith(("http://", "https://")):
            continue
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<.*?>", "", match.group(2)))).strip()
        snippet_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.IGNORECASE | re.DOTALL)
        snippet = re.sub(r"\s+", " ", html.unescape(re.sub(r"<.*?>", "", snippet_match.group(1)))).strip() if snippet_match else ""
        results.append({"title": title or url, "url": url, "snippet": snippet})
    return results


def _searxng_results(content: str) -> list[dict[str, str]]:
    """Extract standard SearXNG HTML result cards when JSON output is disabled."""
    results: list[dict[str, str]] = []
    for match in re.finditer(r'<article\b[^>]*class=["\'][^"\']*\bresult\b[^"\']*["\'][^>]*>(.*?)</article>', content, re.IGNORECASE | re.DOTALL):
        block = match.group(1)
        title_match = re.search(r'<h3\b[^>]*>\s*<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', block, re.IGNORECASE | re.DOTALL)
        if not title_match:
            continue
        url = html.unescape(title_match.group(1)).strip()
        if not url.startswith(("http://", "https://")):
            continue
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<.*?>", "", title_match.group(2)))).strip()
        snippet_match = re.search(r'<p\b[^>]*class=["\'][^"\']*\bcontent\b[^"\']*["\'][^>]*>(.*?)</p>', block, re.IGNORECASE | re.DOTALL)
        snippet = re.sub(r"\s+", " ", html.unescape(re.sub(r"<.*?>", "", snippet_match.group(1)))).strip() if snippet_match else ""
        results.append({"title": title or url, "url": url, "snippet": snippet})
    return results


def _canonical_url(value: str) -> str:
    parsed = urlsplit(str(value or ""))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _request(url: str, *, max_bytes: int = 2_000_000, diagnostics: dict[str, Any] | None = None) -> tuple[bytes, str]:
    request = Request(url, headers={"Accept": "application/json,text/html;q=0.9", "User-Agent": "Ariadne/1.0 evidence-search"})
    with urlopen(request, timeout=SEARCH_TIMEOUT_SECONDS) as response:
        if diagnostics is not None:
            diagnostics["http_status"] = response.status
            diagnostics["content_type"] = response.headers.get_content_type()[:90]
        raw = response.read(max(10_000, min(int(max_bytes), 2_000_000)))
        if diagnostics is not None:
            diagnostics["response_bytes"] = len(raw)
        return raw, response.headers.get_content_charset() or "utf-8"


def _search_url(endpoint: str, query: str, provider_type: str = "json") -> str:
    if "{query}" in endpoint:
        return endpoint.replace("{query}", quote_plus(query))
    separator = "&" if "?" in endpoint else "?"
    suffix = "q=" + quote_plus(query)
    if provider_type == "json" and "format=" not in endpoint and endpoint.startswith(("http://", "https://")):
        suffix += "&format=json"
    return endpoint + separator + suffix


def _json_results(payload: object) -> list[dict[str, str]]:
    values = payload.get("results") if isinstance(payload, dict) else None
    if values is None and isinstance(payload, dict):
        values = payload.get("query", {}).get("search") if isinstance(payload.get("query"), dict) else None
        if isinstance(values, list):
            return [{
                "title": str(item.get("title") or "Wikipedia result"),
                "url": "https://en.wikipedia.org/wiki/" + quote(str(item.get("title") or "").replace(" ", "_")),
                "snippet": re.sub(r"<.*?>", "", str(item.get("snippet") or "")),
            } for item in values if isinstance(item, dict) and item.get("title")]
    if not isinstance(values, list):
        return []
    results: list[dict[str, str]] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("link") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        results.append({
            "title": re.sub(r"\s+", " ", str(item.get("title") or url)).strip(),
            "url": url,
            "snippet": re.sub(r"\s+", " ", str(item.get("content") or item.get("snippet") or item.get("description") or "")).strip(),
        })
    return results


class SearchProviderRegistry:
    def __init__(self, providers: list[SearchProvider] | None = None) -> None:
        self.providers = providers or _saved_search_providers() or default_search_providers()

    def compatible(self) -> list[SearchProvider]:
        return sorted((item for item in self.providers if item.enabled and "search" in item.capabilities and item.endpoint), key=lambda item: (item.priority, item.provider_id))

    def route(self) -> SearchProvider | None:
        return next(iter(self.compatible()), None)

    def snapshot(self) -> dict[str, Any]:
        route = self.route()
        return {
            "active_provider_id": route.provider_id if route else None,
            "providers": [item.as_dict() for item in self.providers],
            "available": route is not None,
        }

    def search(self, query: str, *, limit: int = DEFAULT_RESULT_LIMIT, fetch_limit: int = 3,
               exclude_provider_ids: tuple[str, ...] = ()) -> dict[str, Any]:
        providers = [provider for provider in self.compatible() if provider.provider_id not in exclude_provider_ids]
        if not providers:
            return {"ok": False, "provider": None, "results": [], "error": "No enabled search provider is configured.", "attempts": []}
        errors: list[str] = []
        attempts: list[dict[str, Any]] = []
        for provider in providers:
            attempt: dict[str, Any] = {
                "provider_id": provider.provider_id, "http_status": None,
                "response_classification": "request_error", "parsed_candidate_count": 0,
                "evaluated_candidate_count": 0, "accepted_result_count": 0,
                "rejection_counts": {}, "rejections": [],
            }
            attempts.append(attempt)
            def reject(item: dict[str, str], reason: str) -> None:
                counts = attempt["rejection_counts"]
                counts[reason] = counts.get(reason, 0) + 1
                if len(attempt["rejections"]) < 8:
                    attempt["rejections"].append({
                        "reason": reason, "title": item.get("title", "")[:160],
                        "url": item.get("url", "")[:240],
                    })
            try:
                raw, charset = _request(_search_url(provider.endpoint, query, provider.provider_type), diagnostics=attempt)
                try:
                    decoded = raw.decode(charset, errors="replace")
                except LookupError:
                    decoded = raw.decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(decoded)
                except (ValueError, json.JSONDecodeError):
                    parsed = None
                if parsed is not None:
                    attempt["response_classification"] = "json"
                    results = _json_results(parsed)
                elif provider.provider_type == "searxng" or provider.provider_id == "searxng":
                    results = _searxng_results(decoded)
                elif provider.provider_id == "bing-html" or "b_algo" in decoded:
                    results = _bing_results(decoded)
                else:
                    results = _DuckDuckGoParser().parse(decoded)
                if parsed is None:
                    classification = "html" if re.search(r"<(?:html|article|li|body)\b", decoded, re.I) else "text"
                    if "no results were found" in decoded.casefold():
                        classification = "html_no_results"
                    elif not results and re.search(r"captcha|too many requests|rate.limit", decoded, re.I):
                        classification = "html_challenge_or_rate_limit"
                    attempt["response_classification"] = classification
                attempt["parsed_candidate_count"] = len(results)
                unique: list[dict[str, str]] = []
                seen: set[str] = set()
                for item in results:
                    attempt["evaluated_candidate_count"] += 1
                    if not _topical_result(query, item):
                        reject(item, "topical_mismatch")
                        continue
                    canonical = _canonical_url(item["url"])
                    if not canonical or canonical in seen:
                        reject(item, "duplicate_url" if canonical else "invalid_url")
                        continue
                    seen.add(canonical)
                    unique.append({**item, "url": canonical, "source_id": canonical, "source_type": "live"})
                    if len(unique) >= max(1, min(int(limit), 8)):
                        break
                attempt["accepted_result_count"] = len(unique)
                attempt["outcome"] = ("accepted" if unique else "zero_parsed_results" if not results
                                      else "no_accepted_results")
                for item in unique[:max(0, min(int(fetch_limit), len(unique)))]:
                    try:
                        fetch_url = item["url"]
                        if provider.provider_id == "wikipedia-api":
                            title = urlsplit(fetch_url).path.rsplit("/", 1)[-1]
                            fetch_url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + title
                            page_raw, page_charset = _request(fetch_url, max_bytes=400_000)
                            payload = json.loads(page_raw.decode(page_charset, errors="replace"))
                            text = str(payload.get("extract") or "") if isinstance(payload, dict) else ""
                            page_title = str(payload.get("title") or "") if isinstance(payload, dict) else ""
                        else:
                            page_raw, page_charset = _request(fetch_url, max_bytes=500_000)
                            try:
                                text, page_title = extract_article_text(page_raw, page_charset)
                            except LookupError:
                                text, page_title = extract_article_text(page_raw, "utf-8")
                        if text:
                            item["content"] = text[:MAX_RESULT_CHARS]
                            item["fetched"] = True
                        if page_title and not item.get("title"):
                            item["title"] = page_title
                    except (OSError, ValueError, TypeError, LookupError):
                        item["fetched"] = False
                    item.setdefault("content", item.get("snippet", ""))
                self.providers = [provider.__class__(**{**item.__dict__, "health": "healthy"}) if item.provider_id == provider.provider_id else item for item in self.providers]
                if unique:
                    current_provider = next((item for item in self.providers if item.provider_id == provider.provider_id), provider)
                    return {"ok": True, "provider": provider.provider_id, "provider_metadata": current_provider.as_dict(), "results": unique, "query": query, "attempts": attempts}
                errors.append(f"{provider.provider_id}: no usable results")
            except (OSError, ValueError, TypeError, UnicodeError, LookupError, json.JSONDecodeError) as exc:
                if isinstance(getattr(exc, "code", None), int):
                    attempt["http_status"] = exc.code
                    attempt["response_classification"] = "http_error"
                attempt["outcome"] = "request_or_processing_error"
                attempt["error"] = str(exc)[:240]
                self.providers = [provider.__class__(**{**item.__dict__, "health": "unavailable"}) if item.provider_id == provider.provider_id else item for item in self.providers]
                errors.append(f"{provider.provider_id}: {str(exc)[:240]}")
        return {"ok": False, "provider": providers[0].provider_id, "provider_metadata": self.route().as_dict() if self.route() else providers[0].as_dict(), "results": [], "query": query, "error": "; ".join(errors)[:420], "attempts": attempts}


__all__ = ["SearchProvider", "SearchProviderRegistry", "default_search_providers"]
