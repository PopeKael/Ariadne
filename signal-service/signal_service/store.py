"""SQLite persistence for normalized signals, provenance, and briefings."""
from __future__ import annotations

import json
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
                    """UPDATE signals SET summary=?, content=?, updated_at=?, image_url=?, media_json=?, provenance_json=?, last_seen_at=? WHERE signal_id=?""",
                    (summary, content, signal.updated_at, signal.image_url or row["image_url"], json.dumps(signal.media or json.loads(row["media_json"]), ensure_ascii=False), json.dumps(signal.provenance, ensure_ascii=False), now, signal_id),
                )
                stored = Signal(**{**signal.__dict__, "signal_id": signal_id, "summary": summary, "content": content})
                created = False
            else:
                connection.execute(
                    """INSERT INTO signals (signal_id,dedupe_key,content_key,title,summary,content,url,source_name,source_url,published_at,updated_at,discovered_at,image_url,media_json,provenance_json,rank_score,rank_reason,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (signal.signal_id, signal.dedupe_key, signal.content_key, signal.title, signal.summary, signal.content, signal.url, signal.source_name, signal.source_url, signal.published_at, signal.updated_at, signal.discovered_at, signal.image_url, json.dumps(signal.media, ensure_ascii=False), json.dumps(signal.provenance, ensure_ascii=False), signal.rank_score, signal.rank_reason, now, now),
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
                    signal_id=row["signal_id"], dedupe_key=row["dedupe_key"], content_key=row["content_key"], title=row["title"], summary=row["summary"], content=row["content"], url=row["url"], source_name=row["source_name"], source_url=row["source_url"], published_at=row["published_at"], updated_at=row["updated_at"], discovered_at=row["discovered_at"], image_url=row["image_url"], media=json.loads(row["media_json"]), provenance=json.loads(row["provenance_json"]), rank_score=float(row["rank_score"]), rank_reason=row["rank_reason"],
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
            return {"briefing_id": row["briefing_id"], "generated_at": row["generated_at"], "signal_count": row["signal_count"], "signals": json.loads(row["signals_json"]), "collection": json.loads(row["collection_json"])}


__all__ = ["SignalStore"]
