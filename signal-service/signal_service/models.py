"""Canonical signal and intake normalization models."""
from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit


TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(value: object, limit: int = 20_000) -> str:
    if value is None:
        return ""
    text = html.unescape(TAG_RE.sub(" ", str(value)))
    return WHITESPACE_RE.sub(" ", text).strip()[:limit]


def normalize_timestamp(value: object, fallback: str | None = None) -> str:
    text = clean_text(value, 200)
    if not text:
        return fallback or utc_now()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
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
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), netloc, path, parts.query, ""))


def _candidate_value(candidate: dict[str, Any], *names: str) -> object:
    for name in names:
        value = candidate.get(name)
        if value not in (None, "", []):
            return value
    return ""


@dataclass(frozen=True)
class Signal:
    signal_id: str
    dedupe_key: str
    content_key: str
    title: str
    summary: str
    content: str
    url: str
    source_name: str
    source_url: str
    published_at: str
    updated_at: str
    discovered_at: str
    image_url: str = ""
    media: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    rank_score: float = 0.0
    rank_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "title": self.title,
            "summary": self.summary,
            "content": self.content,
            "url": self.url,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "published_at": self.published_at,
            "updated_at": self.updated_at,
            "discovered_at": self.discovered_at,
            "image_url": self.image_url,
            "media": self.media,
            "provenance": self.provenance,
            "rank_score": round(self.rank_score, 4),
            "rank_reason": self.rank_reason,
        }


def normalize_candidate(
    candidate: object,
    *,
    default_source_name: str = "Unknown source",
    default_source_url: str = "",
    ingest_type: str = "candidate",
    adapter: str = "external",
) -> Signal:
    if not isinstance(candidate, dict):
        raise ValueError("Candidate must be an object")
    observed_at = utc_now()
    title = clean_text(_candidate_value(candidate, "title", "headline", "name"), 500)
    url = canonical_url(_candidate_value(candidate, "url", "link", "original_url", "source_url"))
    if not title:
        raise ValueError("Candidate title is required")
    if not url or not url.startswith(("http://", "https://")):
        raise ValueError("Candidate URL must be an HTTP or HTTPS URL")
    summary = clean_text(_candidate_value(candidate, "summary", "description", "excerpt", "snippet"), 4_000)
    content = clean_text(_candidate_value(candidate, "content", "content_html", "body", "text"), 20_000)
    if not summary:
        summary = content[:4_000]
    if not content:
        content = summary
    source_name = clean_text(_candidate_value(candidate, "source_name", "source", "feed_name", "publisher"), 300) or default_source_name
    source_url = canonical_url(_candidate_value(candidate, "source_url", "feed_url")) or canonical_url(default_source_url)
    published_at = normalize_timestamp(_candidate_value(candidate, "published_at", "published", "pubDate", "date", "timestamp"), observed_at)
    updated_at = normalize_timestamp(_candidate_value(candidate, "updated_at", "updated", "modified"), published_at)
    image_url = canonical_url(_candidate_value(candidate, "image_url", "image", "thumbnail"))
    media = _candidate_value(candidate, "media", "media_metadata", "enclosure")
    if not isinstance(media, dict):
        media = {}
    content_material = json.dumps({"title": title.casefold(), "summary": summary.casefold(), "content": content.casefold()}, sort_keys=True, ensure_ascii=False)
    content_key = hashlib.sha256(content_material.encode("utf-8")).hexdigest()
    dedupe_key = "url:" + hashlib.sha256(url.encode("utf-8")).hexdigest()
    signal_id = "signal-" + hashlib.sha256((dedupe_key + content_key).encode("ascii")).hexdigest()[:24]
    provenance = {
        "ingest_type": ingest_type,
        "adapter": adapter,
        "observed_at": observed_at,
        "original_url": clean_text(_candidate_value(candidate, "url", "link", "original_url"), 4_000) or url,
        "source_name": source_name,
        "source_url": source_url,
    }
    return Signal(
        signal_id=signal_id,
        dedupe_key=dedupe_key,
        content_key="content:" + content_key,
        title=title,
        summary=summary,
        content=content,
        url=url,
        source_name=source_name,
        source_url=source_url,
        published_at=published_at,
        updated_at=updated_at,
        discovered_at=observed_at,
        image_url=image_url,
        media=media,
        provenance=provenance,
    )
