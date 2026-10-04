"""Small, sidecar article cache for one explicit publisher fetch per article."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sqlite3
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from .article_images import HEADERS, prepare_image


_BLOCK_TAGS = {"blockquote", "dd", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6", "li", "p", "pre", "td", "th"}
_DROP_TAGS = {"aside", "button", "canvas", "dialog", "footer", "form", "header", "nav", "noscript", "script", "style", "svg", "template"}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_ARTICLE_HINTS = re.compile(r"(?:^|[\s_-])(?:article|body|entry|main|post|story)(?:[\s_-]|$)", re.I)
_DROP_HINTS = re.compile(r"(?:^|[\s_-])(?:ad(?:vert(?:isement)?)?|breadcrumb|comment|menu|nav(?:igation)?|newsletter|recommend(?:ation|ations)?|related|share|sidebar|social|widget)(?:[\s_-]|$)", re.I)
_SPACE_RE = re.compile(r"\s+")
_ARTICLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(value: object, limit: int = 20_000) -> str:
    return _SPACE_RE.sub(" ", html.unescape(str(value or ""))).strip()[:limit]


class _ArticleBodyParser(HTMLParser):
    """Dependency-free article extractor aimed at ordinary publisher HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._drop_depth: int | None = None
        self._article_depths: list[int] = []
        self._depth = 0
        self._block_tag = ""
        self._block_depth = -1
        self._block_text: list[str] = []
        self._blocks: list[tuple[str, str, int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        attributes = {key.casefold(): str(value or "") for key, value in attrs}
        classes = attributes.get("class", "")
        hint = f"{attributes.get('id', '')} {classes}"
        if tag not in _VOID_TAGS:
            self._depth += 1
        if self._drop_depth is not None:
            return
        style = attributes.get("style", "").replace(" ", "").casefold()
        hidden = "hidden" in attributes or attributes.get("aria-hidden", "").casefold() == "true"
        hidden = hidden or "display:none" in style or "visibility:hidden" in style
        if tag in _DROP_TAGS or _DROP_HINTS.search(hint) or hidden:
            self._drop_depth = self._depth if tag not in _VOID_TAGS else None
            return
        if tag == "article" or _ARTICLE_HINTS.search(hint):
            self._article_depths.append(self._depth)
        if tag in _BLOCK_TAGS and not self._block_tag:
            self._block_tag = tag
            self._block_depth = self._depth
            self._block_text = []

    def handle_data(self, data: str) -> None:
        if not self._drop_depth and self._block_tag:
            self._block_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in _VOID_TAGS:
            return
        if self._drop_depth is not None:
            if self._drop_depth == self._depth:
                self._drop_depth = None
            self._depth = max(0, self._depth - 1)
            return
        if self._block_tag == tag and self._block_depth == self._depth:
            text = _clean(" ".join(self._block_text), 12_000)
            if len(text) >= 25 or (tag.startswith("h") and text):
                in_article = any(depth <= self._block_depth for depth in self._article_depths)
                self._blocks.append((tag, text, 1 if in_article else 0))
            self._block_tag = ""
            self._block_depth = -1
            self._block_text = []
        if self._depth in self._article_depths:
            self._article_depths.remove(self._depth)
        self._depth = max(0, self._depth - 1)

    def markdown(self) -> str:
        preferred = [item for item in self._blocks if item[2]]
        blocks = preferred if len(preferred) >= 2 else self._blocks
        seen: set[str] = set()
        rendered: list[str] = []
        for tag, text, _ in blocks:
            if text in seen:
                continue
            seen.add(text)
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                level = min(int(tag[1]), 4)
                rendered.append(f"{'#' * level} {text}")
            elif tag == "blockquote":
                rendered.append("\n".join(f"> {line}" for line in text.splitlines() if line.strip()))
            elif tag == "li":
                rendered.append(f"- {text}")
            else:
                rendered.append(text)
        return "\n\n".join(rendered).strip()


def _extract_markdown(raw: bytes, url: str, *, charset: str = "utf-8") -> str:
    parser = _ArticleBodyParser()
    parser.feed(raw.decode(charset or "utf-8", errors="replace"))
    body = parser.markdown()
    if body:
        return body
    # A useful fallback for very small or unusual pages; this intentionally
    # returns text only and never persists the fetched HTML.
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw.decode(charset or "utf-8", errors="replace"), flags=re.I | re.S)
    return _clean(re.sub(r"<[^>]+>", " ", text), 100_000)


class ArticleCache:
    def __init__(self, source_database: str | Path, index_database: str | Path, cache_root: str | Path, *, read_only: bool = False):
        self.source_database = Path(source_database)
        self.index_database = Path(index_database)
        self.cache_root = Path(cache_root)
        self.article_root = self.cache_root / "articles"
        self.read_only = read_only
        if not read_only:
            self.article_root.mkdir(parents=True, exist_ok=True)
            self.index_database.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._fetch_count = 0
        self._article_locks: dict[str, threading.Lock] = {}
        if read_only:
            uri = f"file:{self.index_database.as_posix()}?mode=ro"
            self._connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
        else:
            self._connection = sqlite3.connect(self.index_database, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        if not read_only:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS article_cache (
                  article_id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
                  url TEXT NOT NULL, published_at TEXT NOT NULL, summary TEXT NOT NULL,
                  image_url TEXT NOT NULL DEFAULT '', markdown_path TEXT NOT NULL,
                  content_hash TEXT NOT NULL DEFAULT '', content_ready INTEGER NOT NULL DEFAULT 0,
                  scraped_at TEXT, scrape_error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_article_cache_ready ON article_cache(content_ready);
                """
            )
            self._connection.commit()
            columns = {r[1] for r in self._connection.execute("PRAGMA table_info(article_cache)")}
            if "prepared_json" not in columns:
                self._connection.execute("ALTER TABLE article_cache ADD COLUMN prepared_json TEXT NOT NULL DEFAULT '{}'")
                self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _source_connection(self) -> sqlite3.Connection:
        # URI mode=ro is a second guard: the sidecar cannot alter Discovery's DB.
        uri = f"file:{self.source_database.as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def source_article(self, article_id: str) -> dict[str, Any] | None:
        if not _ARTICLE_ID_RE.fullmatch(article_id):
            return None
        try:
            connection = self._source_connection()
        except sqlite3.Error:
            return None
        try:
            row = connection.execute(
                """SELECT a.article_id, a.title, a.canonical_url AS url, a.summary,
                          a.published_at, a.image_url,
                          COALESCE((SELECT source_name FROM article_sources s
                                    WHERE s.article_id=a.article_id
                                    ORDER BY s.last_seen_at DESC LIMIT 1), '') AS source
                   FROM articles a WHERE a.article_id=?""", (article_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            connection.close()

    def entry(self, article_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM article_cache WHERE article_id=?", (article_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result.update(json.loads(result.pop("prepared_json", "{}")))
            return result

    def _upsert_metadata(self, article: dict[str, Any], **values: Any) -> None:
        if self.read_only:
            raise RuntimeError("Article cache was opened read-only.")
        payload = {
            "article_id": article["article_id"], "title": _clean(article.get("title"), 500),
            "source": _clean(article.get("source"), 300), "url": str(article.get("url") or ""),
            "published_at": str(article.get("published_at") or ""), "summary": _clean(article.get("summary"), 4_000),
            "image_url": str(article.get("image_url") or ""),
            "markdown_path": values.get("markdown_path", ""), "content_hash": values.get("content_hash", ""),
            "content_ready": int(values.get("content_ready", 0)), "scraped_at": values.get("scraped_at"),
            "scrape_error": str(values.get("scrape_error", ""))[:500],
        }
        self._connection.execute(
            """INSERT INTO article_cache(article_id,title,source,url,published_at,summary,image_url,markdown_path,content_hash,content_ready,scraped_at,scrape_error)
               VALUES(:article_id,:title,:source,:url,:published_at,:summary,:image_url,:markdown_path,:content_hash,:content_ready,:scraped_at,:scrape_error)
               ON CONFLICT(article_id) DO UPDATE SET title=excluded.title,source=excluded.source,url=excluded.url,
               published_at=excluded.published_at,summary=excluded.summary,image_url=excluded.image_url,
               markdown_path=excluded.markdown_path,content_hash=excluded.content_hash,content_ready=excluded.content_ready,
               scraped_at=excluded.scraped_at,scrape_error=excluded.scrape_error""", payload,
        )

    def populate(self, article_id: str, *, timeout: float = 25.0, retry_image: bool = False) -> dict[str, Any]:
        if self.read_only:
            return {"ok": False, "article_id": article_id, "error": "Article cache was opened read-only."}
        article = self.source_article(article_id)
        if not article:
            return {"ok": False, "article_id": article_id, "error": "Article ID was not found in Discovery."}
        return self.prepare(article, timeout=timeout, retry_image=retry_image)

    def prepare(self, article: dict[str, Any], *, timeout: float = 25.0, retry_image: bool = False) -> dict[str, Any]:
        article = dict(article)
        article_id = str(article.get("article_id") or "")
        if self.read_only or not _ARTICLE_ID_RE.fullmatch(article_id) or urlsplit(str(article.get("url") or "")).scheme not in {"http", "https"}:
            return {"ok": False, "error": "Invalid article preparation request."}
        with self._lock:
            article_lock = self._article_locks.setdefault(article_id, threading.Lock())
        with article_lock:
            path = self.article_root / f"{article_id}.md"
            existing = self.entry(article_id)
            healthy_image = existing and existing.get("image_status") == "cached" and (self.cache_root / "images" / str(existing.get("image_cache_path") or "missing")).is_file()
            retry_at = str((existing or {}).get("next_retry_at") or "")
            image_due = retry_image or not retry_at or retry_at <= _now()
            if existing and existing.get("prepared_version") and not image_due and not healthy_image:
                return {"ok": bool(existing.get("content_ready")), "cached": True, "fetched": False,
                        "error": existing.get("scrape_error", ""), **existing}
            if existing and existing.get("content_ready") and path.is_file() and (healthy_image or not image_due):
                return {"ok": True, "cached": True, "fetched": False, **existing}
            prepared = {"extracted_text": str((existing or {}).get("extracted_text") or ""), "prepared_version": 1, "final_url": str(article["url"]), "image_status": "article_fetch_failed", "image_method": "none", "image_error": "", "image_attempts": []}
            attempt_count = int((existing or {}).get("image_attempt_count") or 0) + 1
            try:
                request = urllib.request.Request(str(article["url"]), headers=HEADERS)
                with urllib.request.urlopen(request, timeout=max(2.0, float(timeout))) as response:
                    raw = response.read(10_000_001)
                    charset = response.headers.get_content_charset() or "utf-8"
                    prepared["final_url"] = response.geturl() if hasattr(response, "geturl") else str(article["url"])
                    prepared["article_http_status"] = getattr(response, "status", 200)
                if len(raw) > 10_000_000:
                    raise ValueError("Article page exceeds 10 MB")
                prepared.update(prepare_image(raw.decode(charset, errors="replace"), prepared["final_url"], str(article.get("image_url") or ""), self.cache_root / "images"))
                article["image_url"] = prepared["image_url"]
                markdown_body = _extract_markdown(raw, prepared["final_url"], charset=charset)
                prepared["extracted_text"] = markdown_body
                text_ready = len(markdown_body.strip()) >= 80
                prepared["provenance"] = {"preparation": "article-cache", "source_url": article["url"], "final_url": prepared["final_url"]}
                markdown = f"# {article['title']}\n\n**Source:** {article.get('source') or 'Unknown source'}  \n**Published:** {article.get('published_at') or ''}  \n**URL:** {article['url']}\n\n{markdown_body}\n"
                content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
                temporary = path.with_suffix(".md.tmp")
                temporary.write_text(markdown, encoding="utf-8", newline="\n")
                os.replace(temporary, path)
                self._fetch_count += 1
                with self._lock:
                    self._upsert_metadata(article, markdown_path=str(path), content_hash=content_hash, content_ready=int(text_ready), scraped_at=_now(), scrape_error="" if text_ready else "Publisher page did not yield a useful article body.")
                    self._persist_prepared(article_id, prepared, attempt_count)
                    self._connection.commit()
                return {"ok": True, "cached": False, "fetched": True, **(self.entry(article_id) or {})}
            except Exception as exc:
                self._fetch_count += 1
                prepared["image_error"] = f"{type(exc).__name__}: {exc}"[:500]
                with self._lock:
                    self._upsert_metadata(article, markdown_path=str(path), content_ready=int(bool(existing and existing.get("content_ready") and path.is_file())), content_hash=(existing or {}).get("content_hash", ""), scraped_at=_now(), scrape_error=prepared["image_error"])
                    self._persist_prepared(article_id, prepared, attempt_count)
                    self._connection.commit()
                return {"ok": False, "article_id": article_id, "error": f"{type(exc).__name__}: {exc}", **(self.entry(article_id) or {})}

    def _persist_prepared(self, article_id, prepared, attempt_count):
        prepared.update(image_attempt_count=attempt_count, image_last_attempt_at=_now(),
                        next_retry_at=None if prepared["image_status"] == "cached" else (datetime.now(timezone.utc) + timedelta(seconds=(300, 1800, 10800, 43200)[min(attempt_count - 1, 3)])).isoformat(timespec="seconds"))
        prepared["image_cache_url"] = (os.environ.get("ARTICLE_CACHE_PUBLIC_URL", "http://192.168.1.200:8790").rstrip("/") + "/v1/cache/images/" + prepared["image_cache_path"]) if prepared.get("image_cache_path") else ""
        self._connection.execute("UPDATE article_cache SET prepared_json=? WHERE article_id=?", (json.dumps(prepared, ensure_ascii=False), article_id))
        print(f"{article_id} | {prepared.get('image_method')} | {prepared.get('image_status')} | {prepared.get('image_cache_mime', '')} | {prepared.get('image_error') or 'OK'}", flush=True)

    def cached_image(self, filename):
        if Path(filename).name != filename or not filename.startswith("image-"):
            return None
        path = self.cache_root / "images" / filename
        if not path.is_file():
            return None
        # OS MIME registries differ; these suffixes are produced by our decoder.
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif"}.get(path.suffix.lower())
        return (path.read_bytes(), mime) if mime else None

    def retrieve(self, article_id: str) -> tuple[dict[str, Any] | None, str | None]:
        with self._lock:
            entry = self.entry(article_id)
            if not entry or not entry.get("content_ready"):
                return entry, None
            path = Path(str(entry["markdown_path"]))
            if not path.is_file() or path.parent.resolve() != self.article_root.resolve():
                return entry, None
            return entry, path.read_text(encoding="utf-8")

    def health(self) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) AS total, COALESCE(SUM(content_ready),0) AS ready FROM article_cache").fetchone()
            return {"ok": True, "prepared_version": 1, "build_sha": os.environ.get("ARIADNE_BUILD_SHA", "unknown"), "cache_root": str(self.cache_root), "index_database": str(self.index_database), "entries": int(row["total"]), "ready": int(row["ready"]), "fetch_count": self._fetch_count}


__all__ = ["ArticleCache"]
