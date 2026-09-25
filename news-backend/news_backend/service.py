from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

from discovery_service.article_cache import _extract_markdown
from discovery_service.feeds import fetch_feed, configured_sources
from discovery_service.models import Article, SourceDefinition, tokens, utc_now


DATA_ROOT = Path(os.environ.get("NEWS_BACKEND_DATA", "/data"))
DATABASE = DATA_ROOT / "news.sqlite3"
ARTICLE_ROOT = DATA_ROOT / "articles"
MAX_ARTICLES_PER_CYCLE = int(os.environ.get("NEWS_BACKEND_MAX_ARTICLES", "8"))
FEED_ITEM_LIMIT = int(os.environ.get("NEWS_BACKEND_FEED_ITEM_LIMIT", "4"))
REFRESH_SECONDS = max(60, int(os.environ.get("NEWS_BACKEND_REFRESH_SECONDS", "900")))
CURATOR_REFRESH_SECONDS = max(30, int(os.environ.get("NEWS_BACKEND_CURATOR_REFRESH_SECONDS", "300")))
_SUMMARY_TRAILERS = re.compile(r"\b(?:continue reading|read more|related stories|sign up for our newsletter)\b.*$", re.I)
_SPACE = re.compile(r"\s+")


def _card_summary(title: str, value: str, limit: int = 280) -> str:
    """Make a compact deterministic summary from feed/page text without a model or network."""
    text = _SPACE.sub(" ", re.sub(r"<[^>]+>", " ", value or "")).strip()
    text = _SUMMARY_TRAILERS.sub("", text).strip(" -|:")
    title_key = _SPACE.sub(" ", title.casefold()).strip(" .!?\u2014-")
    sentences: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"(?<=[.!?])\s+", text):
        clean = part.strip(" -|:")
        key = _SPACE.sub(" ", clean.casefold()).strip(" .!?\u2014-")
        if not clean or len(clean) < 24 or key == title_key or key in seen:
            continue
        sentences.append(clean)
        seen.add(key)
        if len(" ".join(sentences)) >= limit:
            break
    result = " ".join(sentences) or text
    if len(result) > limit:
        result = result[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return result


def _clean_article_markdown(title: str, body: str) -> str:
    """Remove repeated headings/blocks and common page furniture from extracted text."""
    title_key = _SPACE.sub(" ", title.casefold()).strip(" .!?\u2014-")
    seen: set[str] = set()
    blocks: list[str] = []
    for raw in re.split(r"\n\s*\n", body):
        block = _SPACE.sub(" ", raw).strip()
        key = block.lstrip("#>*- ").casefold().strip(" .!?\u2014-")
        if not block or not key or key == title_key or key in seen:
            continue
        if re.match(r"^(?:continue reading|read more|sign up for|share this article)\b", key):
            continue
        seen.add(key)
        blocks.append(block)
    return "\n\n".join(blocks).strip()


class _PageMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "meta":
            return
        values = {key.casefold(): (value or "").strip() for key, value in attrs}
        key = (values.get("property") or values.get("name") or "").casefold()
        if key in {"og:image", "og:description", "twitter:image", "description"} and values.get("content"):
            self.values.setdefault(key, values["content"])


def _page_metadata(raw: bytes, charset: str) -> dict[str, str]:
    parser = _PageMetadataParser()
    try:
        parser.feed(raw.decode(charset or "utf-8", errors="replace"))
    except (LookupError, ValueError):
        return {}
    return parser.values


def _rank_score(published_at: str) -> float:
    try:
        stamp = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        age_hours = max(0.0, (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds() / 3600)
        return round(max(0.0, 100.0 - age_hours / 24.0), 4)
    except (ValueError, TypeError):
        return 0.0


class NewsStore:
    """New isolated SQLite index and local Markdown files; retrieval does no network I/O."""

    def __init__(self, data_root: Path = DATA_ROOT, *, read_only: bool = False):
        self.data_root = Path(data_root)
        self.article_root = self.data_root / "articles"
        self.database = self.data_root / "news.sqlite3"
        self.read_only = read_only
        if not read_only:
            self.data_root.mkdir(parents=True, exist_ok=True)
            self.article_root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        target = f"file:{self.database.as_posix()}?mode=ro" if read_only else str(self.database)
        self.db = sqlite3.connect(target, uri=read_only, check_same_thread=False, timeout=20)
        self.db.row_factory = sqlite3.Row
        if read_only:
            return
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS articles (
              article_id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              canonical_url TEXT NOT NULL UNIQUE,
              source TEXT NOT NULL,
              category TEXT NOT NULL DEFAULT 'Main News Feed',
              published_at TEXT NOT NULL,
              discovered_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              image_url TEXT NOT NULL DEFAULT '',
              summary TEXT NOT NULL DEFAULT '',
              markdown_path TEXT NOT NULL DEFAULT '',
              content_hash TEXT NOT NULL DEFAULT '',
              content_ready INTEGER NOT NULL DEFAULT 0 CHECK(content_ready IN (0,1)),
              scraped_at TEXT,
              scrape_error TEXT NOT NULL DEFAULT '',
              rank_score REAL NOT NULL DEFAULT 0,
              interaction_state TEXT NOT NULL DEFAULT 'unseen'
            );
            CREATE INDEX IF NOT EXISTS idx_articles_rank ON articles(rank_score DESC, published_at DESC);
            CREATE INDEX IF NOT EXISTS idx_articles_ready ON articles(content_ready, published_at DESC);
            CREATE TABLE IF NOT EXISTS sources (
              source_id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL,
              category TEXT NOT NULL, kind TEXT NOT NULL, etag TEXT NOT NULL DEFAULT '',
              last_modified TEXT NOT NULL DEFAULT '', last_polled_at TEXT NOT NULL DEFAULT '',
              last_error TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS service_state (
              key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS briefing_state (
              briefing_id TEXT PRIMARY KEY,
              generated_at TEXT NOT NULL,
              candidate_count INTEGER NOT NULL,
              article_count INTEGER NOT NULL,
              input_hash TEXT NOT NULL,
              result_hash TEXT NOT NULL,
              elapsed_ms REAL NOT NULL DEFAULT 0,
              run_count INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS article_interactions (
              event_id INTEGER PRIMARY KEY AUTOINCREMENT,
              article_id TEXT NOT NULL,
              kind TEXT NOT NULL CHECK(kind IN ('tldr_opened','discussion_opened','feedback')),
              value TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              FOREIGN KEY(article_id) REFERENCES articles(article_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_article_interactions_latest
              ON article_interactions(article_id, event_id DESC);
            CREATE TABLE IF NOT EXISTS briefing_articles (
              briefing_id TEXT NOT NULL,
              article_id TEXT NOT NULL,
              position INTEGER NOT NULL,
              rank_score REAL NOT NULL,
              PRIMARY KEY(briefing_id, article_id),
              UNIQUE(briefing_id, position),
              FOREIGN KEY(article_id) REFERENCES articles(article_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_briefing_position ON briefing_articles(briefing_id, position);
            """
        )
        briefing_columns = {str(row[1]) for row in self.db.execute("PRAGMA table_info(briefing_state)")}
        if "elapsed_ms" not in briefing_columns:
            self.db.execute("ALTER TABLE briefing_state ADD COLUMN elapsed_ms REAL NOT NULL DEFAULT 0")
        if "duplicate_suppressed" not in briefing_columns:
            self.db.execute("ALTER TABLE briefing_state ADD COLUMN duplicate_suppressed INTEGER NOT NULL DEFAULT 0")
        article_columns = {str(row[1]) for row in self.db.execute("PRAGMA table_info(articles)")}
        if "category" not in article_columns:
            self.db.execute("ALTER TABLE articles ADD COLUMN category TEXT NOT NULL DEFAULT 'Main News Feed'")
        self.db.commit()

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def source_headers(self, source: SourceDefinition) -> tuple[str, str]:
        with self.lock:
            row = self.db.execute("SELECT etag,last_modified FROM sources WHERE source_id=?", (source.source_id,)).fetchone()
            return (str(row["etag"]), str(row["last_modified"])) if row else ("", "")

    def update_source(self, source: SourceDefinition, etag: str, last_modified: str, error: str = "") -> None:
        with self.lock:
            self.db.execute(
                """INSERT INTO sources(source_id,name,url,category,kind,etag,last_modified,last_polled_at,last_error)
                   VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET
                   name=excluded.name,url=excluded.url,category=excluded.category,kind=excluded.kind,
                   etag=excluded.etag,last_modified=excluded.last_modified,
                   last_polled_at=excluded.last_polled_at,last_error=excluded.last_error""",
                (source.source_id, source.name, source.url, source.category, source.kind, etag,
                 last_modified, utc_now(), error[:500]),
            )
            self.db.commit()

    def upsert_discovered(self, article: Article) -> bool:
        """Return true only for a newly inserted article; conflict is canonical URL/article ID dedupe."""
        now = utc_now()
        with self.lock:
            existed = self.db.execute(
                "SELECT 1 FROM articles WHERE article_id=? OR canonical_url=?",
                (article.article_id, article.canonical_url),
            ).fetchone() is not None
            self.db.execute(
                """INSERT INTO articles(article_id,title,canonical_url,source,category,published_at,discovered_at,last_seen_at,
                   image_url,summary,rank_score) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(article_id) DO UPDATE SET title=excluded.title,canonical_url=excluded.canonical_url,
                   source=excluded.source,category=excluded.category,published_at=excluded.published_at,last_seen_at=excluded.last_seen_at,
                   image_url=excluded.image_url,summary=excluded.summary,rank_score=excluded.rank_score""",
                (article.article_id, article.title, article.canonical_url, article.source_name, article.category,
                 article.published_at, article.discovered_at, now, article.image_url,
                 _card_summary(article.title, article.summary or article.content),
                 _rank_score(article.published_at)),
            )
            self.db.commit()
        return not existed

    def mark_cached(self, article_id: str, markdown_path: Path, markdown: str) -> None:
        digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        with self.lock:
            self.db.execute(
                """UPDATE articles SET markdown_path=?,content_hash=?,content_ready=1,scraped_at=?,scrape_error=''
                   WHERE article_id=?""",
                (str(markdown_path), digest, utc_now(), article_id),
            )
            self.db.commit()

    def mark_failed(self, article_id: str, error: str) -> None:
        with self.lock:
            self.db.execute(
                "UPDATE articles SET content_ready=0,scraped_at=?,scrape_error=? WHERE article_id=?",
                (utc_now(), error[:500], article_id),
            )
            self.db.commit()

    def update_card_metadata(self, article_id: str, *, image_url: str, summary: str) -> None:
        with self.lock:
            self.db.execute(
                "UPDATE articles SET image_url=CASE WHEN image_url='' THEN ? ELSE image_url END, summary=CASE WHEN summary='' THEN ? ELSE summary END WHERE article_id=?",
                (image_url, summary, article_id),
            )
            self.db.commit()

    def record_interaction(self, article_id: str, kind: str, value: str = "") -> dict[str, Any] | None:
        if kind not in {"tldr_opened", "discussion_opened", "feedback"}:
            raise ValueError("Unsupported article interaction.")
        if kind == "feedback" and value not in {"useful", "interesting", "not_useful"}:
            raise ValueError("Feedback must be useful, interesting, or not_useful.")
        timestamp = utc_now()
        with self.lock:
            exists = self.db.execute("SELECT 1 FROM articles WHERE article_id=?", (article_id,)).fetchone()
            if not exists:
                return None
            self.db.execute(
                "INSERT INTO article_interactions(article_id,kind,value,created_at) VALUES(?,?,?,?)",
                (article_id, kind, value, timestamp),
            )
            if kind in {"tldr_opened", "discussion_opened"}:
                self.db.execute("UPDATE articles SET interaction_state='consumed' WHERE article_id=?", (article_id,))
            self.db.commit()
        return {"article_id": article_id, "kind": kind, "value": value or None, "created_at": timestamp}

    def article_interactions(self, article_id: str) -> dict[str, Any] | None:
        with self.lock:
            article = self.db.execute(
                "SELECT article_id,interaction_state FROM articles WHERE article_id=?", (article_id,)
            ).fetchone()
            if not article:
                return None
            rows = self.db.execute(
                "SELECT kind,value,created_at FROM article_interactions WHERE article_id=? ORDER BY event_id ASC",
                (article_id,),
            ).fetchall()
        events = [dict(row) for row in rows]
        feedback = next(
            ({"value": row["value"], "updated_at": row["created_at"]}
             for row in reversed(events) if row["kind"] == "feedback"),
            None,
        )
        return {"article_id": article_id, "interaction_state": article["interaction_state"],
                "feedback": feedback, "events": events}

    def is_cached(self, article_id: str) -> bool:
        with self.lock:
            row = self.db.execute(
                "SELECT markdown_path,content_ready,content_hash FROM articles WHERE article_id=?",
                (article_id,),
            ).fetchone()
        if not row or not row["content_ready"]:
            return False
        path = Path(str(row["markdown_path"])).resolve()
        if path.parent != self.article_root.resolve() or not path.is_file():
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == row["content_hash"]

    def get_article(self, article_id: str) -> tuple[dict[str, Any] | None, str | None]:
        # Deliberately limited to SQLite + local file access. Never calls a URL client.
        with self.lock:
            row = self.db.execute("SELECT * FROM articles WHERE article_id=?", (article_id,)).fetchone()
            metadata = dict(row) if row else None
        if not metadata or not metadata["content_ready"]:
            return metadata, None
        path = Path(metadata["markdown_path"]).resolve()
        if path.parent != self.article_root.resolve() or not path.is_file():
            return metadata, None
        markdown = path.read_text(encoding="utf-8")
        if hashlib.sha256(markdown.encode("utf-8")).hexdigest() != metadata["content_hash"]:
            return metadata, None
        return metadata, markdown

    def list_articles(self, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        with self.lock:
            count = int(self.db.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
            rows = self.db.execute(
                """SELECT article_id,title,canonical_url,source,category,published_at,discovered_at,last_seen_at,
                   image_url,summary,markdown_path,content_hash,content_ready,scraped_at,scrape_error,
                   rank_score,interaction_state FROM articles
                   ORDER BY rank_score DESC,published_at DESC LIMIT ? OFFSET ?""", (limit, offset)
            ).fetchall()
        return [dict(row) for row in rows], count

    def pending_articles(self, source_name: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                """SELECT article_id,title,canonical_url,source,category,published_at,discovered_at,image_url,summary
                   FROM articles WHERE source=? AND content_ready=0
                   ORDER BY published_at DESC,article_id ASC""", (source_name,),
            ).fetchall()
        return [dict(row) for row in rows]

    def curated_briefing(self) -> dict[str, Any] | None:
        with self.lock:
            state = self.db.execute("SELECT * FROM briefing_state WHERE briefing_id='top100'").fetchone()
            if not state:
                return None
            rows = self.db.execute(
                """SELECT ba.position,ba.rank_score,ba.rank_score AS briefing_score,
                          a.article_id,a.title,a.canonical_url,a.source,a.category,a.published_at,
                          a.discovered_at,a.last_seen_at,a.image_url,a.summary,
                          a.markdown_path,a.content_hash,a.content_ready,a.scraped_at,
                          a.scrape_error,a.interaction_state
                   FROM briefing_articles ba JOIN articles a USING(article_id)
                   WHERE ba.briefing_id='top100' ORDER BY ba.position ASC"""
            ).fetchall()
            articles = [dict(row) for row in rows]
            for article in articles:
                interaction = self.article_interactions(str(article["article_id"]))
                article["feedback"] = interaction["feedback"] if interaction else None
        return {"briefing_id": state["briefing_id"], "generated_at": state["generated_at"],
                "candidate_count": state["candidate_count"], "article_count": state["article_count"],
                "input_hash": state["input_hash"], "result_hash": state["result_hash"],
                "run_count": state["run_count"], "elapsed_ms": state["elapsed_ms"],
                "duplicate_suppressed": state["duplicate_suppressed"],
                "articles": articles}

    def curate_top100(self, *, limit: int = 100) -> dict[str, Any]:
        """Persist a deterministic, diverse, duplicate-suppressed briefing from local cache only."""
        started = time.perf_counter()
        with self.lock:
            rows = self.db.execute(
                """SELECT article_id,title,canonical_url,source,category,published_at,discovered_at,
                          last_seen_at,image_url,summary,markdown_path,content_hash,content_ready,
                          scraped_at,scrape_error,interaction_state
                   FROM articles WHERE content_ready=1 ORDER BY article_id ASC"""
            ).fetchall()
            candidates: list[dict[str, Any]] = []
            summaries_to_clean: list[tuple[str, str]] = []
            for raw in rows:
                article = dict(raw)
                path = Path(str(article["markdown_path"])).resolve()
                if path.parent != self.article_root.resolve() or not path.is_file():
                    continue
                cleaned_summary = _card_summary(str(article["title"]), str(article["summary"] or ""))
                if cleaned_summary != article["summary"]:
                    summaries_to_clean.append((cleaned_summary, str(article["article_id"])))
                    article["summary"] = cleaned_summary
                candidates.append(article)
            if summaries_to_clean:
                self.db.executemany("UPDATE articles SET summary=? WHERE article_id=?", summaries_to_clean)
                self.db.commit()

            def publication_time(article: dict[str, Any]) -> datetime:
                try:
                    value = datetime.fromisoformat(str(article["published_at"]).replace("Z", "+00:00"))
                    if value.tzinfo is None:
                        value = value.replace(tzinfo=timezone.utc)
                    return value.astimezone(timezone.utc)
                except (ValueError, TypeError):
                    return datetime.min.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            for article in candidates:
                age_hours = max(0, math.floor((now - publication_time(article)).total_seconds() / 3600))
                article["briefing_score"] = round(max(0.0, 100.0 - age_hours * 1.5), 3)
                article["story_tokens"] = tokens(article["title"])
            ranked = sorted(candidates, key=lambda item: (
                -item["briefing_score"], -publication_time(item).timestamp(), item["article_id"]
            ))
            target = max(1, min(int(limit), 100))
            selected: list[dict[str, Any]] = []
            source_counts: dict[str, int] = {}
            category_counts: dict[str, int] = {}
            unique_sources = max(1, len({item["source"] for item in ranked}))
            source_cap = max(3, math.ceil(target * 0.4))
            category_cap = max(4, math.ceil(target * 0.6))
            remaining = list(ranked)
            duplicate_suppressed = 0
            while remaining and len(selected) < target:
                available: list[dict[str, Any]] = []
                duplicate_ids: set[str] = set()
                for item in remaining:
                    story_words = item["story_tokens"]
                    duplicate = False
                    for chosen in selected:
                        chosen_words = chosen["story_tokens"]
                        overlap = len(story_words & chosen_words)
                        if overlap >= 4 and (
                            overlap / max(1, len(story_words | chosen_words)) >= 0.68
                            or overlap / max(1, min(len(story_words), len(chosen_words))) >= 0.88
                        ):
                            duplicate = True
                            break
                    if duplicate:
                        duplicate_ids.add(item["article_id"])
                    else:
                        available.append(item)
                duplicate_suppressed += len(duplicate_ids)
                if duplicate_ids:
                    remaining = [item for item in remaining if item["article_id"] not in duplicate_ids]
                if not available:
                    break
                eligible = [item for item in available
                            if source_counts.get(item["source"], 0) < source_cap
                            and category_counts.get(item["category"], 0) < category_cap]
                if not eligible:
                    eligible = [item for item in available if source_counts.get(item["source"], 0) < source_cap] or available
                chosen = max(eligible, key=lambda item: (
                    item["briefing_score"]
                    - source_counts.get(item["source"], 0) * (8.0 / unique_sources)
                    - category_counts.get(item["category"], 0) * 1.5,
                    item["briefing_score"], publication_time(item).timestamp(), item["article_id"]
                ))
                selected.append(chosen)
                source_counts[chosen["source"]] = source_counts.get(chosen["source"], 0) + 1
                category_counts[chosen["category"]] = category_counts.get(chosen["category"], 0) + 1
                remaining.remove(chosen)
            input_material = [
                {key: article[key] for key in (
                    "article_id", "title", "canonical_url", "source", "category", "published_at", "summary", "image_url", "content_hash"
                )}
                for article in sorted(candidates, key=lambda item: item["article_id"])
            ]
            input_hash = hashlib.sha256(json.dumps(
                input_material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")).hexdigest()
            result_material = [
                {"article_id": item["article_id"], "rank_score": item["briefing_score"]}
                for item in selected
            ]
            result_hash = hashlib.sha256(json.dumps(
                result_material, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")).hexdigest()
            previous = self.db.execute(
                "SELECT run_count,input_hash,result_hash FROM briefing_state WHERE briefing_id='top100'"
            ).fetchone()
            run_count = int(previous[0]) + 1 if previous else 1
            generated_at = utc_now()
            elapsed = round((time.perf_counter() - started) * 1000, 3)
            with self.db:
                if previous and previous[1] == input_hash and previous[2] == result_hash:
                    self.db.execute(
                        "UPDATE briefing_state SET elapsed_ms=?,run_count=? WHERE briefing_id='top100'",
                        (elapsed, run_count),
                    )
                else:
                    self.db.execute("DELETE FROM briefing_articles WHERE briefing_id='top100'")
                    self.db.executemany(
                        "INSERT INTO briefing_articles(briefing_id,article_id,position,rank_score) VALUES('top100',?,?,?)",
                        [(item["article_id"], position, item["briefing_score"]) for position, item in enumerate(selected, 1)],
                    )
                    self.db.execute(
                        """INSERT INTO briefing_state(briefing_id,generated_at,candidate_count,article_count,input_hash,result_hash,elapsed_ms,run_count,duplicate_suppressed)
                           VALUES('top100',?,?,?,?,?,?,?,?) ON CONFLICT(briefing_id) DO UPDATE SET
                           generated_at=excluded.generated_at,candidate_count=excluded.candidate_count,
                           article_count=excluded.article_count,input_hash=excluded.input_hash,
                           result_hash=excluded.result_hash,elapsed_ms=excluded.elapsed_ms,run_count=excluded.run_count,
                           duplicate_suppressed=excluded.duplicate_suppressed""",
                        (generated_at, len(candidates), len(selected), input_hash, result_hash,
                         elapsed, run_count, duplicate_suppressed),
                    )
        return {"briefing_id": "top100", "generated_at": generated_at, "candidate_count": len(candidates),
                "article_count": len(selected), "input_hash": input_hash, "result_hash": result_hash,
                "run_count": run_count, "elapsed_ms": elapsed, "duplicate_suppressed": duplicate_suppressed,
                "publisher_fetches": 0, "articles": result_material}

    def stats(self) -> dict[str, Any]:
        with self.lock:
            totals = self.db.execute(
                "SELECT COUNT(*) AS total,SUM(content_ready) AS ready,SUM(CASE WHEN scrape_error<>'' THEN 1 ELSE 0 END) AS failed FROM articles"
            ).fetchone()
            state = self.db.execute("SELECT value FROM service_state WHERE key='last_collection'").fetchone()
            briefing = self.db.execute(
                "SELECT generated_at,article_count,result_hash,run_count,elapsed_ms FROM briefing_state WHERE briefing_id='top100'"
            ).fetchone()
        return {"articles": int(totals["total"] or 0), "content_ready": int(totals["ready"] or 0),
                "scrape_failed": int(totals["failed"] or 0),
                "last_collection": json.loads(state[0]) if state else None,
                "curator": dict(briefing) if briefing else None}

    def save_cycle(self, report: dict[str, Any]) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO service_state(key,value) VALUES('last_collection',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(report, ensure_ascii=False),),
            )
            self.db.commit()

    def collection_offset(self, source_count: int) -> int:
        if source_count < 1:
            return 0
        with self.lock:
            row = self.db.execute("SELECT value FROM service_state WHERE key='collection_source_offset'").fetchone()
        return int(row[0]) % source_count if row else 0

    def save_collection_offset(self, offset: int) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO service_state(key,value) VALUES('collection_source_offset',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(offset),),
            )
            self.db.commit()


def _fetch_publisher(article: Article, store: NewsStore, timeout: float = 25.0) -> tuple[bool, str]:
    path = store.article_root / f"{article.article_id}.md"
    try:
        request = urllib.request.Request(
            article.canonical_url,
            headers={"Accept": "text/html,application/xhtml+xml", "User-Agent": "Ariadne Hera News Backend/0.1"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(10_000_000)
            charset = response.headers.get_content_charset() or "utf-8"
            final_url = response.geturl()
        body = _clean_article_markdown(article.title, _extract_markdown(raw, final_url, charset=charset))
        if len(body.strip()) < 220:
            raise ValueError("publisher page did not yield a useful article body")
        page_meta = _page_metadata(raw, charset)
        image_url = article.image_url or urljoin(final_url, page_meta.get("og:image") or page_meta.get("twitter:image", ""))
        summary_source = article.summary or page_meta.get("og:description") or page_meta.get("description", "")
        summary = _card_summary(article.title, summary_source or body.replace("\n", " "))
        markdown = (
            f"# {article.title}\n\n**Source:** {article.source_name}\n\n"
            f"**Published:** {article.published_at}\n\n**URL:** {article.canonical_url}\n\n{body}\n"
        )
        temporary = path.with_suffix(".md.tmp")
        temporary.write_text(markdown, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
        store.mark_cached(article.article_id, path, markdown)
        store.update_card_metadata(article.article_id, image_url=image_url, summary=summary)
        return True, ""
    except Exception as exc:  # A per-article failure is recorded; the collection cycle continues.
        store.mark_failed(article.article_id, f"{type(exc).__name__}: {exc}")
        return False, f"{type(exc).__name__}: {exc}"


def collect_once(store: NewsStore, sources: list[SourceDefinition] | None = None,
                 *, force: bool = False, cycle_article_limit: int = MAX_ARTICLES_PER_CYCLE) -> dict[str, Any]:
    sources = sources if sources is not None else [source for source in configured_sources() if source.enabled]
    source_offset = store.collection_offset(len(sources))
    if sources:
        sources = sources[source_offset:] + sources[:source_offset]
    per_source_limit = max(1, math.ceil(max(1, cycle_article_limit) / max(1, len(sources))))
    report: dict[str, Any] = {
        "started_at": utc_now(), "sources_tested": [], "sources_succeeded": [], "sources_failed": [],
        "feed_failures": [], "scrape_failures": [],
        "discovered": 0, "new_articles": 0, "duplicates_updated": 0,
        "cached_this_cycle": 0, "already_cached": 0, "publisher_fetches": 0, "pending_deferred": 0,
        "failure_types": {}, "source_rotation_offset": source_offset,
    }
    def record_failure(source_name: str, error: str) -> None:
        if source_name not in report["sources_failed"]:
            report["sources_failed"].append(source_name)
        match = re.search(r"HTTP Error (\d{3})", error)
        if match:
            result_type = "HTTP " + match.group(1)
        elif "timed out" in error.casefold() or "timeout" in error.casefold():
            result_type = "timeout"
        elif "urlerror" in error.casefold() or "url error" in error.casefold():
            result_type = "network error"
        elif "useful article body" in error.casefold():
            result_type = "extraction too short"
        else:
            result_type = error.split(":", 1)[0] or "unknown"
        by_source = report["failure_types"].setdefault(source_name, {})
        by_source[result_type] = by_source.get(result_type, 0) + 1
    for source in sources:
        report["sources_tested"].append(source.name)
        old_etag, old_modified = store.source_headers(source)
        candidates: list[dict[str, Any]] = []
        try:
            result = fetch_feed(source, timeout=20, item_limit=FEED_ITEM_LIMIT,
                                etag="" if force else old_etag, last_modified="" if force else old_modified)
            store.update_source(source, result.etag or old_etag, result.last_modified or old_modified)
            report["sources_succeeded"].append(source.name)
            if not result.not_modified:
                candidates = result.candidates
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            store.update_source(source, old_etag, old_modified, message)
            report["feed_failures"].append({"source": source.name, "error": message})
            record_failure(source.name, message)

        seen_cycle: set[str] = set()
        for candidate in candidates:
            article = Article.from_candidate(candidate, source)
            if not article.canonical_url or not article.title or article.article_id in seen_cycle:
                continue
            seen_cycle.add(article.article_id)
            report["discovered"] += 1
            if store.upsert_discovered(article):
                report["new_articles"] += 1
            else:
                report["duplicates_updated"] += 1
            if store.is_cached(article.article_id):
                report["already_cached"] += 1

        # A 304 only says the feed is unchanged; it must not strand already
        # indexed, uncached articles. Drain this source's pending rows even on
        # 304 (and after a feed error), without refetching ready cache entries.
        pending = store.pending_articles(source.name)
        source_fetches = 0
        for index, row in enumerate(pending):
            article_id = str(row["article_id"])
            if store.is_cached(article_id):
                continue
            if report["publisher_fetches"] >= max(1, cycle_article_limit):
                report["pending_deferred"] += len(pending) - index
                break
            if source_fetches >= per_source_limit:
                report["pending_deferred"] += len(pending) - index
                break
            article = Article(
                article_id=article_id, canonical_url=str(row["canonical_url"]), title=str(row["title"]),
                summary=str(row["summary"]), content=str(row["summary"]), source_id=source.source_id,
                source_name=source.name, source_url=source.url, category=source.category,
                published_at=str(row["published_at"]), discovered_at=str(row["discovered_at"]),
                image_url=str(row["image_url"] or ""), content_hash="", metadata={},
            )
            report["publisher_fetches"] += 1
            source_fetches += 1
            ok, error = _fetch_publisher(article, store)
            if ok:
                report["cached_this_cycle"] += 1
            else:
                report["scrape_failures"].append({"article_id": article.article_id, "title": article.title,
                                                   "source": source.name, "error": error})
                record_failure(source.name, error)
    report["finished_at"] = utc_now()
    if sources:
        store.save_collection_offset((source_offset + 1) % len(sources))
    report["elapsed_ms"] = round((time.perf_counter() - _CYCLE_START) * 1000, 2) if _CYCLE_START else None
    store.save_cycle(report)
    return report


_CYCLE_START = 0.0


class NewsHandler(BaseHTTPRequestHandler):
    store: NewsStore

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/health":
            self._json(200, {"ok": True, "service": "ariadne-news-backend", **self.store.stats()})
            return
        if parsed.path == "/articles":
            from urllib.parse import parse_qs
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", [50])[0])
                offset = int(query.get("offset", [0])[0])
            except ValueError:
                self._json(400, {"ok": False, "error": "limit and offset must be integers"})
                return
            articles, total = self.store.list_articles(limit, offset)
            self._json(200, {"ok": True, "total": total, "limit": max(1, min(limit, 100)),
                             "offset": max(0, offset), "articles": articles})
            return
        if parsed.path == "/briefing":
            briefing = self.store.curated_briefing()
            if briefing is None:
                self._json(503, {"ok": False, "error": "briefing has not been curated yet"})
            else:
                self._json(200, {"ok": True, **briefing})
            return
        prefix = "/articles/"
        if parsed.path.startswith(prefix):
            article_id = unquote(parsed.path[len(prefix):])
            interaction_suffix = "/interactions"
            if article_id.endswith(interaction_suffix):
                interaction_id = article_id[:-len(interaction_suffix)]
                if not interaction_id or "/" in interaction_id:
                    self._json(404, {"ok": False, "error": "article not found"})
                    return
                interactions = self.store.article_interactions(interaction_id)
                if interactions is None:
                    self._json(404, {"ok": False, "error": "article not found", "article_id": interaction_id})
                else:
                    self._json(200, {"ok": True, **interactions})
                return
            if not article_id or "/" in article_id:
                self._json(404, {"ok": False, "error": "article not found"})
                return
            started = time.perf_counter()
            metadata, markdown = self.store.get_article(article_id)
            retrieval_ms = round((time.perf_counter() - started) * 1000, 3)
            if not metadata:
                self._json(404, {"ok": False, "error": "article not found", "article_id": article_id})
            elif markdown is None:
                self._json(409, {"ok": False, "error": "article content is not ready", "article": metadata})
            else:
                self._json(200, {"ok": True, "article": metadata, "markdown": markdown,
                                 "retrieval": {"storage": "local_file", "network_fetch": False,
                                               "publisher_fetch_occurred": False, "retrieval_ms": retrieval_ms}})
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        prefix = "/articles/"
        if not parsed.path.startswith(prefix):
            self._json(405, {"ok": False, "error": "unsupported API operation"})
            return
        remainder = unquote(parsed.path[len(prefix):])
        if "/" in remainder:
            article_id, action = remainder.rsplit("/", 1)
        else:
            article_id, action = remainder, ""
        if not article_id:
            self._json(404, {"ok": False, "error": "article not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 16_384:
                raise ValueError("request body is too large")
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            if not isinstance(body, dict):
                raise ValueError("request body must be a JSON object")
            if action == "interactions":
                kind = body.get("kind")
                if kind not in {"tldr_opened", "discussion_opened"}:
                    raise ValueError("kind must be tldr_opened or discussion_opened")
                event = self.store.record_interaction(article_id, str(kind))
            elif action == "feedback":
                value = body.get("value")
                if not isinstance(value, str):
                    raise ValueError("feedback value is required")
                event = self.store.record_interaction(article_id, "feedback", value)
            else:
                self._json(404, {"ok": False, "error": "not found"})
                return
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        if event is None:
            self._json(404, {"ok": False, "error": "article not found", "article_id": article_id})
            return
        interactions = self.store.article_interactions(article_id)
        self._json(200, {"ok": True, "event": event, **(interactions or {})})

    def log_message(self, fmt: str, *args: Any) -> None:
        print("NEWS API " + (fmt % args), flush=True)


def serve(host: str = "0.0.0.0", port: int = 8791) -> None:
    store = NewsStore()
    NewsHandler.store = store
    server = ThreadingHTTPServer((host, port), NewsHandler)

    collection_ready = threading.Event()

    def loop() -> None:
        while True:
            try:
                global _CYCLE_START
                _CYCLE_START = time.perf_counter()
                report = collect_once(store)
                print("COLLECTION " + json.dumps(report, ensure_ascii=False), flush=True)
            except Exception as exc:
                print(f"COLLECTION_FATAL {type(exc).__name__}: {exc}", flush=True)
            finally:
                collection_ready.set()
            time.sleep(REFRESH_SECONDS)

    def curator_loop() -> None:
        collection_ready.wait()
        while True:
            try:
                report = store.curate_top100()
                print("CURATOR " + json.dumps(report, ensure_ascii=False), flush=True)
            except Exception as exc:
                print(f"CURATOR_FATAL {type(exc).__name__}: {exc}", flush=True)
            time.sleep(CURATOR_REFRESH_SECONDS)

    threading.Thread(target=loop, name="news-collector", daemon=True).start()
    threading.Thread(target=curator_loop, name="news-curator", daemon=True).start()
    print(f"NEWS_BACKEND_LISTENING http://{host}:{port} data={store.data_root}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        store.close()
