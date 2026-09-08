"""Replaceable RSS/Atom source adapter."""
from __future__ import annotations

import os
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from .models import clean_text


DEFAULT_FEEDS = (
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "AI Watch"),
    ("NASA Breaking News", "https://www.nasa.gov/rss/dyn/breaking_news.rss", "Main News Feed"),
    ("Hacker News", "https://hnrss.org/frontpage", "AI Watch"),
)


@dataclass(frozen=True)
class FeedDefinition:
    name: str
    url: str
    category: str = "Main News Feed"


def configured_feeds(raw: str | None = None) -> list[FeedDefinition]:
    value = raw if raw is not None else os.environ.get("SIGNAL_SERVICE_FEEDS", "")
    if not value.strip():
        pairs = DEFAULT_FEEDS
    else:
        pairs = []
        for entry in value.split(","):
            name, separator, url = entry.partition("|")
            if separator and name.strip() and url.strip():
                category = "Main News Feed"
                if "|" in url:
                    url, category = url.split("|", 1)
                pairs.append((name.strip(), url.strip(), category.strip() or "Main News Feed"))
    return [FeedDefinition(name, url, category) for name, url, category in pairs if url.startswith(("http://", "https://"))]


def _local_name(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1].casefold()


def _child_text(element: ET.Element, *names: str) -> str:
    wanted = {name.casefold() for name in names}
    for child in list(element):
        if _local_name(child.tag) in wanted:
            return clean_text("".join(child.itertext()), 20_000)
    return ""


def _link(element: ET.Element) -> str:
    for child in list(element):
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return href.strip()
        text = clean_text("".join(child.itertext()), 4_000)
        if text:
            return text
    return ""


def _media(element: ET.Element) -> tuple[str, dict[str, Any]]:
    image = ""
    metadata: dict[str, Any] = {}
    for child in element.iter():
        name = _local_name(child.tag)
        url = child.attrib.get("url") or child.attrib.get("href") or ""
        if name in {"content", "thumbnail", "enclosure"} and url:
            if not image or name == "thumbnail":
                image = url
            metadata.setdefault(name, []).append({key: value for key, value in child.attrib.items() if key in {"url", "type", "medium", "width", "height"}})
    return image, metadata


def fetch_feed(feed: FeedDefinition, *, timeout: float = 15.0, item_limit: int = 20) -> list[dict[str, Any]]:
    request = urllib.request.Request(feed.url, headers={"User-Agent": "Ariadne Signal Service/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        root = ET.fromstring(response.read(2_000_000))
    feed_title = _child_text(root, "title") or feed.name
    items = [element for element in root.iter() if _local_name(element.tag) in {"item", "entry"}]
    candidates: list[dict[str, Any]] = []
    for element in items[: max(1, min(int(item_limit), 100))]:
        title = _child_text(element, "title")
        url = _link(element)
        summary = _child_text(element, "encoded", "description", "summary", "subtitle", "content")
        published = _child_text(element, "pubdate", "published", "updated", "date", "issued")
        image_url, media = _media(element)
        if not title or not url:
            continue
        candidates.append({
            "title": title,
            "url": url,
            "summary": summary,
            "content": summary,
            "source_name": feed_title,
            "source_url": feed.url,
            "category": feed.category,
            "published_at": published,
            "image_url": image_url,
            "media": media,
        })
    return candidates


__all__ = ["DEFAULT_FEEDS", "FeedDefinition", "configured_feeds", "fetch_feed"]
