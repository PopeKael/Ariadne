"""SQLite persistence for normalized signals, provenance, and briefings."""
from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Iterable

from .models import Signal, utc_now


SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL,
    content_key TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    content TEXT NOT NULL,
    url TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    published_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    image_url TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Main News Feed',
    media_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    rank_score REAL NOT NULL DEFAULT 0,
    rank_reason TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_url_key ON signals(dedupe_key);
CREATE INDEX IF NOT EXISTS idx_signals_content_key ON signals(content_key);
CREATE INDEX IF NOT EXISTS idx_signals_published_at ON signals(published_at);
CREATE TABLE IF NOT EXISTS signal_provenance (
    provenance_id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL,
    ingest_type TEXT NOT NULL,
    adapter TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    original_url TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    UNIQUE(signal_id, ingest_type, adapter, source_name, source_url, original_url)
);
CREATE INDEX IF NOT EXISTS idx_provenance_signal ON signal_provenance(signal_id);
CREATE TABLE IF NOT EXISTS briefings (
    briefing_id TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    signal_count INTEGER NOT NULL,
    signals_json TEXT NOT NULL,
    collection_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_briefings_generated_at ON briefings(generated_at DESC);
CREATE TABLE IF NOT EXISTS signal_feedback (
    feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
    feedback_value TEXT NOT NULL CHECK (feedback_value IN ('useful', 'interesting', 'not_useful')),
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signal_feedback_signal ON signal_feedback(signal_id, recorded_at DESC);
CREATE TABLE IF NOT EXISTS watchlist_topics (
    topic_id TEXT PRIMARY KEY,
    topic TEXT NOT NULL UNIQUE COLLATE NOCASE,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_watchlist_topics_active ON watchlist_topics(active, updated_at DESC);
"""


class SignalStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript(SCHEMA)
        columns = {str(row[1]) for row in self._connection.execute("PRAGMA table_info(signals)").fetchall()}
        if "category" not in columns:
            self._connection.execute("ALTER TABLE signals ADD COLUMN category TEXT NOT NULL DEFAULT 'Main News Feed'")
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def upsert(self, signal: Signal) -> tuple[Signal, bool]:
        with self._lock:
            connection = self._connection
            row = connection.execute("SELECT * FROM signals WHERE dedupe_key = ? OR content_key = ? LIMIT 1", (signal.dedupe_key, signal.content_key)).fetchone()
            now = utc_now()
            if row:
                signal_id = str(row["signal_id"])
                summary = signal.summary if len(signal.summary) >= len(row["summary"]) else str(row["summary"])
                content = signal.content if len(signal.content) >= len(row["content"]) else str(row["content"])
                connection.execute(
                    """UPDATE signals SET summary=?, content=?, updated_at=?, image_url=?, category=?, media_json=?, provenance_json=?, last_seen_at=? WHERE signal_id=?""",
                    (summary, content, signal.updated_at, signal.image_url or row["image_url"], signal.category or row["category"], json.dumps(signal.media or json.loads(row["media_json"]), ensure_ascii=False), json.dumps(signal.provenance, ensure_ascii=False), now, signal_id),
                )
                stored = Signal(**{**signal.__dict__, "signal_id": signal_id, "summary": summary, "content": content})
                created = False
            else:
                connection.execute(
                    """INSERT INTO signals (signal_id,dedupe_key,content_key,title,summary,content,url,source_name,source_url,published_at,updated_at,discovered_at,image_url,category,media_json,provenance_json,rank_score,rank_reason,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (signal.signal_id, signal.dedupe_key, signal.content_key, signal.title, signal.summary, signal.content, signal.url, signal.source_name, signal.source_url, signal.published_at, signal.updated_at, signal.discovered_at, signal.image_url, signal.category, json.dumps(signal.media, ensure_ascii=False), json.dumps(signal.provenance, ensure_ascii=False), signal.rank_score, signal.rank_reason, now, now),
                )
                stored = signal
                created = True
            provenance = signal.provenance
            connection.execute(
                """INSERT OR IGNORE INTO signal_provenance (signal_id,observed_at,ingest_type,adapter,source_name,source_url,original_url,metadata_json) VALUES (?,?,?,?,?,?,?,?)""",
                (stored.signal_id, provenance.get("observed_at", now), provenance.get("ingest_type", "candidate"), provenance.get("adapter", "external"), signal.source_name, signal.source_url, provenance.get("original_url", signal.url), json.dumps(provenance, ensure_ascii=False)),
            )
            connection.commit()
            return stored, created

    def recent(self, limit: int = 200) -> list[Signal]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM signals ORDER BY published_at DESC, signal_id ASC LIMIT ?", (max(1, min(int(limit), 500)),)).fetchall()
            result: list[Signal] = []
            for row in rows:
                result.append(Signal(
                    signal_id=row["signal_id"], dedupe_key=row["dedupe_key"], content_key=row["content_key"], title=row["title"], summary=row["summary"], content=row["content"], url=row["url"], source_name=row["source_name"], source_url=row["source_url"], published_at=row["published_at"], updated_at=row["updated_at"], discovered_at=row["discovered_at"], image_url=row["image_url"], category=row["category"], media=json.loads(row["media_json"]), provenance=json.loads(row["provenance_json"]), rank_score=float(row["rank_score"]), rank_reason=row["rank_reason"],
                ))
            return result

    def save_briefing(self, signals: Iterable[Signal], collection: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            selected = list(signals)
            payload = {"signals": [signal.as_dict() for signal in selected]}
            briefing = {"briefing_id": "briefing-" + uuid.uuid4().hex[:16], "generated_at": utc_now(), "signal_count": len(selected), "signals": payload["signals"], "collection": collection}
            self._connection.execute("INSERT INTO briefings (briefing_id,generated_at,signal_count,signals_json,collection_json) VALUES (?,?,?,?,?)", (briefing["briefing_id"], briefing["generated_at"], len(selected), json.dumps(payload["signals"], ensure_ascii=False), json.dumps(collection, ensure_ascii=False)))
            self._connection.commit()
            return briefing

    def latest_briefing(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM briefings ORDER BY generated_at DESC LIMIT 1").fetchone()
            if not row:
                return None
            signals = json.loads(row["signals_json"])
            feedback = self.latest_feedback([item.get("signal_id") for item in signals if isinstance(item, dict)])
            for item in signals:
                if isinstance(item, dict) and item.get("signal_id") in feedback:
                    item["feedback"] = feedback[item["signal_id"]]
            return {"briefing_id": row["briefing_id"], "generated_at": row["generated_at"], "signal_count": row["signal_count"], "signals": signals, "collection": json.loads(row["collection_json"])}

    def record_feedback(self, signal_id: str, value: str, recorded_at: str | None = None) -> dict[str, Any]:
        with self._lock:
            timestamp = recorded_at or utc_now()
            row = self._connection.execute("SELECT signal_id FROM signals WHERE signal_id = ?", (signal_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown signal_id")
            self._connection.execute("DELETE FROM signal_feedback WHERE signal_id = ?", (signal_id,))
            self._connection.execute("INSERT INTO signal_feedback (signal_id,feedback_value,recorded_at) VALUES (?,?,?)", (signal_id, value, timestamp))
            self._connection.commit()
            return {"signal_id": signal_id, "feedback": value, "recorded_at": timestamp}

    def latest_feedback(self, signal_ids: Iterable[str | None]) -> dict[str, dict[str, str]]:
        values = [str(value) for value in signal_ids if value]
        if not values:
            return {}
        with self._lock:
            placeholders = ",".join("?" for _ in values)
            rows = self._connection.execute(
                f"SELECT signal_id, feedback_value, recorded_at FROM signal_feedback WHERE signal_id IN ({placeholders}) ORDER BY recorded_at DESC, feedback_id DESC",
                values,
            ).fetchall()
            result: dict[str, dict[str, str]] = {}
            for row in rows:
                result.setdefault(str(row["signal_id"]), {"value": str(row["feedback_value"]), "timestamp": str(row["recorded_at"])})
            return result

    def upsert_watchlist_topic(self, topic: str, active: bool = True) -> dict[str, Any]:
        clean_topic = " ".join(str(topic).split()).strip()
        if not clean_topic or len(clean_topic) > 200:
            raise ValueError("Watchlist topic must be between 1 and 200 characters")
        topic_id = "watch-" + hashlib.sha256(clean_topic.casefold().encode("utf-8")).hexdigest()[:24]
        timestamp = utc_now()
        with self._lock:
            self._connection.execute(
                """INSERT INTO watchlist_topics (topic_id,topic,active,created_at,updated_at) VALUES (?,?,?,?,?)
                   ON CONFLICT(topic) DO UPDATE SET active=excluded.active, updated_at=excluded.updated_at""",
                (topic_id, clean_topic, 1 if active else 0, timestamp, timestamp),
            )
            self._connection.commit()
            row = self._connection.execute("SELECT topic_id,topic,active,created_at,updated_at FROM watchlist_topics WHERE topic = ? COLLATE NOCASE", (clean_topic,)).fetchone()
            return {"topic_id": row["topic_id"], "topic": row["topic"], "active": bool(row["active"]), "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def watchlist_topics(self, active_only: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            query = "SELECT topic_id,topic,active,created_at,updated_at FROM watchlist_topics"
            if active_only:
                query += " WHERE active = 1"
            query += " ORDER BY updated_at DESC, topic COLLATE NOCASE"
            rows = self._connection.execute(query).fetchall()
            return [{"topic_id": row["topic_id"], "topic": row["topic"], "active": bool(row["active"]), "created_at": row["created_at"], "updated_at": row["updated_at"]} for row in rows]


__all__ = ["SignalStore"]
