"""Small, dependency-free models used by the Discovery Engine."""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid", "mc_cid", "mc_eid", "ref", "ref_src"}
STOPWORDS = {
    "about", "after", "against", "among", "before", "being", "between", "could", "first", "from", "have", "into", "just", "more", "most", "other", "over", "said", "says", "should", "than", "that", "their", "there", "these", "they", "this", "those", "through", "under", "very", "was", "were", "what", "when", "where", "which", "while", "with", "would", "your", "news", "new", "report", "reports", "live", "update", "updates", "latest", "breaking",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(value: object, limit: int = 20_000) -> str:
    if value is None:
        return ""
    text = html.unescape(TAG_RE.sub(" ", str(value)))
    return SPACE_RE.sub(" ", text).strip()[:limit]


def normalize_timestamp(value: object, fallback: str | None = None) -> str:
    text = clean_text(value, 200)
    if not text:
        return fallback or utc_now()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError, OverflowError):
            return fallback or utc_now()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def canonical_url(value: object) -> str:
    raw = clean_text(value, 4_000)
    if not raw:
        return ""
    parts = urlsplit(raw)
    if parts.scheme.casefold() not in {"http", "https"} or not parts.netloc:
        return raw
    hostname = (parts.hostname or "").casefold()
    netloc = hostname
    if parts.port:
        netloc += f":{parts.port}"
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.casefold() not in TRACKING_PARAMS]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), netloc, path, urlencode(query), ""))


def source_domain(value: object) -> str:
    host = (urlsplit(canonical_url(value)).hostname or "").casefold()
    return host.removeprefix("www.")


def tokens(value: object) -> set[str]:
    words = re.findall(r"[\w]{3,}", clean_text(value, 8_000).casefold(), flags=re.UNICODE)
    return {word for word in words if word not in STOPWORDS and not word.isdigit()}


@dataclass(frozen=True)
class SourceDefinition:
    source_id: str
    name: str
    url: str
    category: str = "Main News Feed"
    kind: str = "rss_atom"
    enabled: bool = True


@dataclass(frozen=True)
class Article:
    article_id: str
    canonical_url: str
    title: str
    summary: str
    content: str
    source_id: str
    source_name: str
    source_url: str
    category: str
    published_at: str
    discovered_at: str
    image_url: str = ""
    content_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_candidate(cls, candidate: dict[str, Any], source: SourceDefinition | None = None) -> "Article":
        observed = utc_now()
        url = canonical_url(candidate.get("url") or candidate.get("link"))
        title = clean_text(candidate.get("title") or candidate.get("headline"), 500)
        summary = clean_text(candidate.get("summary") or candidate.get("description") or candidate.get("snippet"), 4_000)
        content = clean_text(candidate.get("content") or summary, 12_000)
        if not summary:
            summary = content[:4_000]
        if not content:
            content = summary
        source_name = clean_text(candidate.get("source_name") or (source.name if source else "Unknown source"), 300)
        source_url = canonical_url(candidate.get("source_url") or (source.url if source else ""))
        source_id = clean_text(candidate.get("source_id") or (source.source_id if source else "source-" + hashlib.sha256(source_name.casefold().encode()).hexdigest()[:20]), 100)
        category = clean_text(candidate.get("category") or (source.category if source else "Main News Feed"), 80) or "Main News Feed"
        published = normalize_timestamp(candidate.get("published_at") or candidate.get("published") or candidate.get("date"), observed)
        # Keep the evidence hash focused on the report body. Headlines often
        # differ between outlets even when the underlying wire copy is exact.
        material = " ".join((summary, content)).casefold()
        content_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
        article_id = "article-" + hashlib.sha256(url.encode("utf-8") if url else material.encode("utf-8")).hexdigest()[:28]
        return cls(article_id, url, title, summary, content, source_id, source_name, source_url, category, published, observed, canonical_url(candidate.get("image_url") or candidate.get("image") or ""), content_hash, dict(candidate.get("metadata") or {}))

    def as_dict(self) -> dict[str, Any]:
        return {
            "article_id": self.article_id,
            "url": self.canonical_url,
            "title": self.title,
            "summary": self.summary,
            "content": self.content,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "category": self.category,
            "published_at": self.published_at,
            "discovered_at": self.discovered_at,
            "image_url": self.image_url,
            "content_hash": self.content_hash,
        }


def stable_source_id(name: str, url: str) -> str:
    return "source-" + hashlib.sha256(f"{name}|{url}".casefold().encode("utf-8")).hexdigest()[:24]


__all__ = ["Article", "SourceDefinition", "canonical_url", "clean_text", "normalize_timestamp", "source_domain", "stable_source_id", "tokens", "utc_now"]
