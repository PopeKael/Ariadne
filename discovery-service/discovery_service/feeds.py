"""RSS/Atom polling with conditional requests and bounded XML parsing."""
from __future__ import annotations

import os
import json
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

from .models import SourceDefinition, canonical_url, clean_text, normalize_timestamp, stable_source_id


DEFAULT_SOURCES = (
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml", "Main News Feed"),
    ("BBC Asia", "https://feeds.bbci.co.uk/news/world/asia/rss.xml", "Main News Feed"),
    ("BBC Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml", "Technology"),
    ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml", "Business"),
    ("BBC Science", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", "Science"),
    ("BBC Health", "https://feeds.bbci.co.uk/news/health/rss.xml", "Health"),
    ("The Guardian World", "https://www.theguardian.com/world/rss", "Main News Feed"),
    ("The Guardian Technology", "https://www.theguardian.com/technology/rss", "Technology"),
    ("The Guardian Science", "https://www.theguardian.com/science/rss", "Science"),
    ("The Guardian Business", "https://www.theguardian.com/business/rss", "Business"),
    ("The Guardian Environment", "https://www.theguardian.com/environment/rss", "Science"),
    ("NPR World", "https://feeds.npr.org/1004/rss.xml", "Main News Feed"),
    ("NPR Technology", "https://feeds.npr.org/1019/rss.xml", "Technology"),
    ("NPR Science", "https://feeds.npr.org/1007/rss.xml", "Science"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "Main News Feed"),
    ("NYT World", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "Main News Feed"),
    ("NYT Technology", "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml", "Technology"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "Technology"),
    ("The Verge", "https://www.theverge.com/rss/index.xml", "Technology"),
    ("MIT Technology Review", "https://www.technologyreview.com/feed/", "Technology"),
    ("Hacker News", "https://hnrss.org/frontpage", "Technology"),
    ("NASA Breaking News", "https://www.nasa.gov/rss/dyn/breaking_news.rss", "Space"),
    ("NASA Image of the Day", "https://www.nasa.gov/rss/dyn/lg_image_of_the_day.rss", "Space"),
    ("ESA", "https://www.esa.int/rssfeed/Our_Activities/Space_News", "Space"),
    ("Nature News", "https://www.nature.com/nature.rss", "Science"),
    ("ScienceDaily", "https://www.sciencedaily.com/rss/all.xml", "Science"),
    ("Krebs on Security", "https://krebsonsecurity.com/feed/", "Security"),
    ("The Record", "https://therecord.media/feed", "Security"),
    ("CISA Alerts", "https://www.cisa.gov/cybersecurity-advisories/all.xml", "Security"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", "Finance"),
    ("Investing.com", "https://www.investing.com/rss/news.rss", "Finance"),
    ("OpenAI News", "https://openai.com/news/rss.xml", "AI Watch"),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml", "AI Watch"),
    ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml", "AI Watch"),
    ("Khaosod English", "https://www.khaosodenglish.com/feed/", "Thailand Focus"),
    ("The Thaiger", "https://thethaiger.com/feed", "Thailand Focus"),
    ("Thailand PBS World", "https://www.thaipbsworld.com/", "Thailand Focus", "html", True),
    ("Bangkok Post", "https://www.bangkokpost.com/rss/data/news.xml", "Thailand Focus", "rss_atom", False),
    ("Bangkok Post Web", "https://www.bangkokpost.com/thailand", "Thailand Focus", "html", True),
)


@dataclass(frozen=True)
class FetchResult:
    candidates: list[dict[str, Any]]
    etag: str = ""
    last_modified: str = ""
    not_modified: bool = False


def configured_sources(raw: str | None = None) -> list[SourceDefinition]:
    value = raw if raw is not None else os.environ.get("DISCOVERY_SERVICE_SOURCES", "")
    pairs: list[tuple[str, str, str]] = []
    source_file = os.environ.get("DISCOVERY_SERVICE_SOURCES_FILE", "").strip()
    if not value.strip() and source_file:
        try:
            with open(source_file, "r", encoding="utf-8") as handle:
                configured = json.load(handle)
            if isinstance(configured, list):
                for entry in configured:
                    if not isinstance(entry, dict):
                        continue
                    name = str(entry.get("name") or "").strip()
                    url = str(entry.get("url") or entry.get("endpoint") or "").strip()
                    category = str(entry.get("category") or "Main News Feed").strip()
                    kind = str(entry.get("kind") or "rss_atom").strip() or "rss_atom"
                    enabled = bool(entry.get("enabled", True))
                    if name and url:
                        pairs.append((name, url, category or "Main News Feed", kind, enabled))
        except (OSError, ValueError, TypeError):
            pairs = []
    if not pairs and value.strip():
        for entry in value.split(","):
            name, separator, rest = entry.partition("|")
            url, separator2, category = rest.partition("|")
            if separator and name.strip() and url.strip():
                pairs.append((name.strip(), url.strip(), category.strip() if separator2 and category.strip() else "Main News Feed", "rss_atom", True))
    elif not pairs:
        for entry in DEFAULT_SOURCES:
            if len(entry) == 3:
                name, url, category = entry
                pairs.append((name, url, category, "rss_atom", True))
            else:
                name, url, category, kind, enabled = entry
                pairs.append((name, url, category, kind, enabled))
    return [SourceDefinition(stable_source_id(name, url), name, url, category, kind, enabled) for name, url, category, kind, enabled in pairs if url.startswith(("http://", "https://"))]


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
        value = clean_text("".join(child.itertext()), 4_000)
        if value:
            return value
    return ""


def _image(element: ET.Element) -> str:
    for child in element.iter():
        if _local_name(child.tag) in {"content", "thumbnail", "enclosure"}:
            value = child.attrib.get("url") or child.attrib.get("href") or ""
            if value and ("image" in str(child.attrib.get("type", "")) or _local_name(child.tag) != "enclosure"):
                return value.strip()
    return ""


class _HTMLArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        values = dict(attrs)
        self._href = str(values.get("href") or "").strip()
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "a" or not self._href:
            return
        self.links.append((self._href, clean_text(" ".join(self._text), 500)))
        self._href = ""
        self._text = []


def _html_candidates(source: SourceDefinition, raw: bytes, *, item_limit: int) -> list[dict[str, Any]]:
    parser = _HTMLArticleParser()
    parser.feed(raw.decode("utf-8", errors="replace"))
    origin = urlsplit(source.url)
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    excluded_prefixes = ("/search", "/about", "/contact", "/category", "/author", "/tag", "/wp-content", "/feed")
    for href, title in parser.links:
        if not title or len(title) < 20 or href.startswith(("#", "mailto:", "javascript:")):
            continue
        url = canonical_url(urljoin(source.url, href))
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname != origin.hostname:
            continue
        if not parsed.path or parsed.path == "/" or parsed.path.casefold().startswith(excluded_prefixes) or url in seen:
            continue
        seen.add(url)
        candidates.append({
            "title": title,
            "url": url,
            "summary": "",
            "content": "",
            "source_id": source.source_id,
            "source_name": source.name,
            "source_url": source.url,
            "category": source.category,
            "published_at": normalize_timestamp(""),
            "image_url": "",
        })
        if len(candidates) >= max(1, min(int(item_limit), 100)):
            break
    return candidates


def fetch_feed(source: SourceDefinition, *, timeout: float = 20.0, item_limit: int = 40, etag: str = "", last_modified: str = "") -> FetchResult:
    headers = {"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5", "User-Agent": "Ariadne Discovery Engine/0.1"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    request = urllib.request.Request(source.url, headers=headers)
    try:
        response_context = urllib.request.urlopen(request, timeout=max(1.0, float(timeout)))
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return FetchResult([], etag, last_modified, True)
        raise
    with response_context as response:
        raw = response.read(4_000_000)
        response_etag = str(response.headers.get("ETag") or "")
        response_modified = str(response.headers.get("Last-Modified") or "")
        charset = response.headers.get_content_charset() or "utf-8"
    if source.kind.casefold() == "html":
        return FetchResult(_html_candidates(source, raw, item_limit=item_limit), response_etag, response_modified)
    root = ET.fromstring(raw.decode(charset, errors="replace"))
    candidates: list[dict[str, Any]] = []
    for element in [item for item in root.iter() if _local_name(item.tag) in {"item", "entry"}][: max(1, min(int(item_limit), 100))]:
        title = _child_text(element, "title")
        url = _link(element)
        summary = _child_text(element, "encoded", "description", "summary", "subtitle", "content")
        published = _child_text(element, "pubdate", "published", "updated", "date", "issued")
        if not title or not url:
            continue
        candidates.append({"title": title, "url": url, "summary": summary, "content": summary, "source_id": source.source_id, "source_name": source.name, "source_url": source.url, "category": source.category, "published_at": normalize_timestamp(published), "image_url": _image(element)})
    return FetchResult(candidates, response_etag, response_modified)


__all__ = ["DEFAULT_SOURCES", "FetchResult", "SourceDefinition", "configured_sources", "fetch_feed"]
