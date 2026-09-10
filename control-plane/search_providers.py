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
    # install useful while allowing SearXNG or any other provider to replace it
    # through configuration without changing Home or the model route.
    return [
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


def _canonical_url(value: str) -> str:
    parsed = urlsplit(str(value or ""))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _request(url: str, *, max_bytes: int = 2_000_000) -> tuple[bytes, str]:
    request = Request(url, headers={"Accept": "application/json,text/html;q=0.9", "User-Agent": "Ariadne/1.0 evidence-search"})
    with urlopen(request, timeout=SEARCH_TIMEOUT_SECONDS) as response:
        raw = response.read(max(10_000, min(int(max_bytes), 2_000_000)))
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

    def search(self, query: str, *, limit: int = DEFAULT_RESULT_LIMIT, fetch_limit: int = 3) -> dict[str, Any]:
        providers = self.compatible()
        if not providers:
            return {"ok": False, "provider": None, "results": [], "error": "No enabled search provider is configured."}
        errors: list[str] = []
        for provider in providers:
            try:
                raw, charset = _request(_search_url(provider.endpoint, query, provider.provider_type))
                try:
                    decoded = raw.decode(charset, errors="replace")
                except LookupError:
                    decoded = raw.decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(decoded)
                except (ValueError, json.JSONDecodeError):
                    parsed = None
                if parsed is not None:
                    results = _json_results(parsed)
                elif provider.provider_id == "bing-html" or "b_algo" in decoded:
                    results = _bing_results(decoded)
                else:
                    results = _DuckDuckGoParser().parse(decoded)
                unique: list[dict[str, str]] = []
                seen: set[str] = set()
                for item in results:
                    canonical = _canonical_url(item["url"])
                    if not canonical or canonical in seen:
                        continue
                    seen.add(canonical)
                    unique.append({**item, "url": canonical, "source_id": canonical, "source_type": "live"})
                    if len(unique) >= max(1, min(int(limit), 8)):
                        break
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
                    return {"ok": True, "provider": provider.provider_id, "provider_metadata": current_provider.as_dict(), "results": unique, "query": query}
                errors.append(f"{provider.provider_id}: no usable results")
            except (OSError, ValueError, TypeError, UnicodeError, LookupError, json.JSONDecodeError) as exc:
                self.providers = [provider.__class__(**{**item.__dict__, "health": "unavailable"}) if item.provider_id == provider.provider_id else item for item in self.providers]
                errors.append(f"{provider.provider_id}: {str(exc)[:240]}")
        return {"ok": False, "provider": providers[0].provider_id, "provider_metadata": self.route().as_dict() if self.route() else providers[0].as_dict(), "results": [], "query": query, "error": "; ".join(errors)[:420]}


__all__ = ["SearchProvider", "SearchProviderRegistry", "default_search_providers"]
