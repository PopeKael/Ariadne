"""SQLite state for source polling, raw articles, and materialized stories."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import Article, SourceDefinition, utc_now


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  source_id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL UNIQUE, category TEXT NOT NULL,
  kind TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, etag TEXT NOT NULL DEFAULT '',
  last_modified TEXT NOT NULL DEFAULT '', last_attempt_at TEXT, last_success_at TEXT,
  next_poll_at TEXT, failure_count INTEGER NOT NULL DEFAULT 0, item_count INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS articles (
  article_id TEXT PRIMARY KEY, canonical_url TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
  summary TEXT NOT NULL, content TEXT NOT NULL, published_at TEXT NOT NULL, discovered_at TEXT NOT NULL,
  image_url TEXT NOT NULL DEFAULT '', content_hash TEXT NOT NULL, last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS article_sources (
  article_id TEXT NOT NULL REFERENCES articles(article_id) ON DELETE CASCADE,
  source_id TEXT NOT NULL, source_name TEXT NOT NULL, source_url TEXT NOT NULL, category TEXT NOT NULL,
  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  PRIMARY KEY(article_id, source_id)
);
CREATE TABLE IF NOT EXISTS stories (
  story_id TEXT PRIMARY KEY, title TEXT NOT NULL, summary TEXT NOT NULL, url TEXT NOT NULL,
  image_url TEXT NOT NULL DEFAULT '', category TEXT NOT NULL, categories_json TEXT NOT NULL,
  published_at TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  article_count INTEGER NOT NULL, source_count INTEGER NOT NULL, source_names_json TEXT NOT NULL,
  source_domains_json TEXT NOT NULL, evidence_json TEXT NOT NULL, rank_score REAL NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS story_articles (
  story_id TEXT NOT NULL REFERENCES stories(story_id) ON DELETE CASCADE,
  article_id TEXT NOT NULL REFERENCES articles(article_id) ON DELETE CASCADE,
  PRIMARY KEY(story_id, article_id)
);
CREATE INDEX IF NOT EXISTS idx_sources_due ON sources(enabled, next_poll_at);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_stories_rank ON stories(rank_score DESC, published_at DESC);
"""


class DiscoveryStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def upsert_sources(self, sources: Iterable[SourceDefinition]) -> None:
        stamp = utc_now()
        configured = list(sources)
        configured_ids = {source.source_id for source in configured}
        with self._lock:
            for source in configured:
                self._connection.execute(
                    """INSERT INTO sources(source_id,name,url,category,kind,enabled) VALUES(?,?,?,?,?,?)
                       ON CONFLICT(source_id) DO UPDATE SET name=excluded.name,url=excluded.url,category=excluded.category,kind=excluded.kind,enabled=excluded.enabled""",
                    (source.source_id, source.name, source.url, source.category, source.kind, int(source.enabled)),
                )
            if configured_ids:
                placeholders = ",".join("?" for _ in configured_ids)
                self._connection.execute(
                    f"UPDATE sources SET enabled=0 WHERE source_id NOT IN ({placeholders})",
                    tuple(configured_ids),
                )
            self._connection.commit()

    def due_sources(self, *, limit: int = 100) -> list[dict[str, Any]]:
        now = utc_now()
        with self._lock:
            rows = self._connection.execute("SELECT * FROM sources WHERE enabled=1 AND (next_poll_at IS NULL OR next_poll_at <= ?) ORDER BY COALESCE(last_success_at, '') ASC LIMIT ?", (now, max(1, int(limit)))).fetchall()
            return [dict(row) for row in rows]

    def source_result(self, source_id: str, *, success: bool, item_count: int = 0, etag: str = "", last_modified: str = "", error: str = "", interval_seconds: int = 900) -> None:
        now = datetime.now(timezone.utc)
        next_poll = (now + timedelta(seconds=max(60, int(interval_seconds)))).isoformat(timespec="seconds")
        stamp = now.isoformat(timespec="seconds")
        with self._lock:
            self._connection.execute(
                """UPDATE sources SET etag=CASE WHEN ? <> '' THEN ? ELSE etag END, last_modified=CASE WHEN ? <> '' THEN ? ELSE last_modified END,
                   last_attempt_at=?, last_success_at=CASE WHEN ? THEN ? ELSE last_success_at END, next_poll_at=?,
                   failure_count=CASE WHEN ? THEN 0 ELSE failure_count+1 END, item_count=?, error=? WHERE source_id=?""",
                (etag, etag, last_modified, last_modified, stamp, int(success), stamp, next_poll, int(success), int(item_count), error[:500], source_id),
            )
            self._connection.commit()

    def upsert_articles(self, articles: Iterable[Article]) -> int:
        accepted = 0
        stamp = utc_now()
        with self._lock:
            for article in articles:
                if not article.title or not article.canonical_url:
                    continue
                self._connection.execute(
                    """INSERT INTO articles(article_id,canonical_url,title,summary,content,published_at,discovered_at,image_url,content_hash,last_seen_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(canonical_url) DO UPDATE SET title=excluded.title,
                       summary=CASE WHEN length(excluded.summary)>length(articles.summary) THEN excluded.summary ELSE articles.summary END,
                       content=CASE WHEN length(excluded.content)>length(articles.content) THEN excluded.content ELSE articles.content END,
                       published_at=excluded.published_at,image_url=CASE WHEN excluded.image_url<>'' THEN excluded.image_url ELSE articles.image_url END,
                       content_hash=excluded.content_hash,last_seen_at=excluded.last_seen_at""",
                    (article.article_id, article.canonical_url, article.title, article.summary, article.content, article.published_at, article.discovered_at, article.image_url, article.content_hash, stamp),
                )
                row = self._connection.execute("SELECT article_id FROM articles WHERE canonical_url=?", (article.canonical_url,)).fetchone()
                article_id = str(row[0])
                self._connection.execute(
                    """INSERT INTO article_sources(article_id,source_id,source_name,source_url,category,first_seen_at,last_seen_at)
                       VALUES(?,?,?,?,?,?,?) ON CONFLICT(article_id,source_id) DO UPDATE SET source_name=excluded.source_name,
                       source_url=excluded.source_url,category=excluded.category,last_seen_at=excluded.last_seen_at""",
                    (article_id, article.source_id, article.source_name, article.source_url, article.category, stamp, stamp),
                )
                accepted += 1
            self._connection.commit()
        return accepted

    def recent_articles(self, *, lookback_days: int = 7) -> list[Article]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(lookback_days)))).isoformat(timespec="seconds")
        with self._lock:
            rows = self._connection.execute(
                """SELECT a.*, s.source_id, s.source_name, s.source_url, s.category FROM articles a
                   LEFT JOIN article_sources s ON s.article_id=a.article_id WHERE a.published_at >= ? OR a.discovered_at >= ?
                   ORDER BY a.published_at DESC, a.discovered_at DESC""", (cutoff, cutoff),
            ).fetchall()
            result: list[Article] = []
            for row in rows:
                result.append(Article(str(row["article_id"]), str(row["canonical_url"]), str(row["title"]), str(row["summary"]), str(row["content"]), str(row["source_id"] or ""), str(row["source_name"] or "Unknown source"), str(row["source_url"] or ""), str(row["category"] or "Main News Feed"), str(row["published_at"]), str(row["discovered_at"]), str(row["image_url"] or ""), str(row["content_hash"])))
            return result

    def save_stories(self, stories: Iterable[dict[str, Any]]) -> None:
        values = list(stories)
        stamp = utc_now()
        with self._lock:
            self._connection.execute("DELETE FROM story_articles")
            self._connection.execute("DELETE FROM stories")
            for story in values:
                self._connection.execute(
                    """INSERT INTO stories(story_id,title,summary,url,image_url,category,categories_json,published_at,first_seen_at,last_seen_at,article_count,source_count,source_names_json,source_domains_json,evidence_json,rank_score,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (story["story_id"], story["title"], story["summary"], story["url"], story.get("image_url", ""), story.get("category", "Main News Feed"), json.dumps(story.get("categories", []), ensure_ascii=False), story["published_at"], story["first_seen_at"], story["last_seen_at"], int(story.get("article_count", 0)), int(story.get("source_count", 0)), json.dumps(story.get("source_names", []), ensure_ascii=False), json.dumps(story.get("source_domains", []), ensure_ascii=False), json.dumps(story.get("evidence", []), ensure_ascii=False), float(story.get("rank_score", 0)), stamp),
                )
                for article_id in story.get("article_ids", []):
                    self._connection.execute("INSERT INTO story_articles(story_id,article_id) VALUES(?,?)", (story["story_id"], article_id))
            self._connection.commit()

    def stories(self, *, limit: int = 240) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM stories ORDER BY rank_score DESC, published_at DESC LIMIT ?", (max(1, min(int(limit), 300)),)).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                for key in ("categories_json", "source_names_json", "source_domains_json", "evidence_json"):
                    item[key.removesuffix("_json")] = json.loads(item.pop(key))
                result.append(item)
            return result

    def sources(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._connection.execute("SELECT * FROM sources ORDER BY name COLLATE NOCASE").fetchall()]

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {"sources": int(self._connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]), "articles": int(self._connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0]), "stories": int(self._connection.execute("SELECT COUNT(*) FROM stories").fetchone()[0])}


__all__ = ["DiscoveryStore"]
