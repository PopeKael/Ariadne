"""SQLite persistence for normalized signals, provenance, and briefings."""
from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import threading
import uuid
import shutil
from datetime import datetime, timezone
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
    last_seen_at TEXT NOT NULL,
    image_enrichment_attempted_at TEXT,
    image_enrichment_error TEXT NOT NULL DEFAULT ''
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
CREATE TABLE IF NOT EXISTS signal_interactions (
    interaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
    interaction_type TEXT NOT NULL CHECK (interaction_type IN ('tldr')),
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signal_interactions_signal ON signal_interactions(signal_id, recorded_at DESC);
CREATE TABLE IF NOT EXISTS watchlist_topics (
    topic_id TEXT PRIMARY KEY,
    topic TEXT NOT NULL UNIQUE COLLATE NOCASE,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_watchlist_topics_active ON watchlist_topics(active, updated_at DESC);
CREATE TABLE IF NOT EXISTS interests (
    interest_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    priority REAL NOT NULL DEFAULT 1.0,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    semantic_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_interests_active ON interests(enabled, semantic_enabled, priority DESC);
CREATE TABLE IF NOT EXISTS signal_embeddings (
    signal_id TEXT NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    embedding_version TEXT NOT NULL,
    source_text_hash TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(signal_id, provider_id, model_id, embedding_version)
);
CREATE INDEX IF NOT EXISTS idx_signal_embeddings_lookup ON signal_embeddings(signal_id, provider_id, model_id, embedding_version);
CREATE TABLE IF NOT EXISTS interest_embeddings (
    interest_id TEXT NOT NULL REFERENCES interests(interest_id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    embedding_version TEXT NOT NULL,
    source_text_hash TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(interest_id, provider_id, model_id, embedding_version)
);
CREATE TABLE IF NOT EXISTS signal_interest_matches (
    signal_id TEXT NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
    interest_id TEXT NOT NULL REFERENCES interests(interest_id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    embedding_version TEXT NOT NULL,
    semantic_score REAL NOT NULL,
    matched_at TEXT NOT NULL,
    PRIMARY KEY(signal_id, interest_id, provider_id, model_id, embedding_version)
);
CREATE INDEX IF NOT EXISTS idx_signal_interest_matches_signal ON signal_interest_matches(signal_id, semantic_score DESC);
CREATE TABLE IF NOT EXISTS learned_preferences (
    preference_key TEXT PRIMARY KEY,
    dimension TEXT NOT NULL,
    label TEXT NOT NULL,
    score REAL NOT NULL,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    useful_count INTEGER NOT NULL DEFAULT 0,
    interesting_count INTEGER NOT NULL DEFAULT 0,
    not_useful_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signal_sources (
    source_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    adapter_type TEXT NOT NULL,
    endpoint TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'Main News Feed',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_attempt_at TEXT,
    last_success_at TEXT,
    item_count INTEGER NOT NULL DEFAULT 0,
    health_state TEXT NOT NULL DEFAULT 'unknown',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signal_sources_enabled ON signal_sources(enabled, updated_at DESC);
"""


class SignalStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        had_schema = self._connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='signal_embeddings'").fetchone() is not None
        if not had_schema and self.path.exists() and self.path.stat().st_size:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = self.path.with_name(f"{self.path.name}.pre-migration-{stamp}.bak")
            if not backup.exists():
                shutil.copy2(self.path, backup)
        self._connection.executescript(SCHEMA)
        columns = {str(row[1]) for row in self._connection.execute("PRAGMA table_info(signals)").fetchall()}
        if "category" not in columns:
            self._connection.execute("ALTER TABLE signals ADD COLUMN category TEXT NOT NULL DEFAULT 'Main News Feed'")
        if "image_enrichment_attempted_at" not in columns:
            self._connection.execute("ALTER TABLE signals ADD COLUMN image_enrichment_attempted_at TEXT")
        if "image_enrichment_error" not in columns:
            self._connection.execute("ALTER TABLE signals ADD COLUMN image_enrichment_error TEXT NOT NULL DEFAULT ''")
        self._migrate_watchlist_topics()
        self._ensure_builtin_sources()
        self._connection.commit()

    def _migrate_watchlist_topics(self) -> None:
        rows = self._connection.execute("SELECT topic_id,topic,active,created_at,updated_at FROM watchlist_topics").fetchall()
        for row in rows:
            self._connection.execute(
                """INSERT OR IGNORE INTO interests (interest_id,name,description,enabled,priority,aliases_json,semantic_enabled,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (str(row["topic_id"]).replace("watch-", "interest-", 1), row["topic"], row["topic"], int(row["active"]), 1.0, json.dumps([row["topic"]]), 1, row["created_at"], row["updated_at"]),
            )

    def _ensure_builtin_sources(self) -> None:
        builtins = (
            ("source-ars-technica", "Ars Technica", "rss_atom", "https://feeds.arstechnica.com/arstechnica/index", "AI Watch"),
            ("source-nasa-breaking-news", "NASA Breaking News", "rss_atom", "https://www.nasa.gov/rss/dyn/breaking_news.rss", "Main News Feed"),
            ("source-hacker-news", "Hacker News", "rss_atom", "https://hnrss.org/frontpage", "AI Watch"),
        )
        stamp = utc_now()
        for source_id, name, adapter, endpoint, category in builtins:
            self._connection.execute(
                """INSERT OR IGNORE INTO signal_sources (source_id,name,adapter_type,endpoint,category,enabled,health_state,created_at,updated_at)
                   VALUES (?,?,?,?,?,1,'unknown',?,?)""",
                (source_id, name, adapter, endpoint, category, stamp, stamp),
            )

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
                title = signal.title if signal.provenance.get("discovery") else str(row["title"])
                connection.execute(
                    """UPDATE signals SET title=?, summary=?, content=?, updated_at=?, image_url=?, category=?, media_json=?, provenance_json=?, last_seen_at=? WHERE signal_id=?""",
                    (title, summary, content, signal.updated_at, signal.image_url or row["image_url"], signal.category or row["category"], json.dumps(signal.media or json.loads(row["media_json"]), ensure_ascii=False), json.dumps(signal.provenance, ensure_ascii=False), now, signal_id),
                )
                stored = Signal(**{**signal.__dict__, "signal_id": signal_id, "title": title, "summary": summary, "content": content})
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
                semantic_matches = self._semantic_matches_for_signal(str(row["signal_id"]))
                result.append(Signal(
                    signal_id=row["signal_id"], dedupe_key=row["dedupe_key"], content_key=row["content_key"], title=row["title"], summary=row["summary"], content=row["content"], url=row["url"], source_name=row["source_name"], source_url=row["source_url"], published_at=row["published_at"], updated_at=row["updated_at"], discovered_at=row["discovered_at"], image_url=row["image_url"], category=row["category"], media=json.loads(row["media_json"]), provenance=json.loads(row["provenance_json"]), rank_score=float(row["rank_score"]), rank_reason=row["rank_reason"],
                    semantic_matches=semantic_matches,
                ))
            return result

    def _semantic_matches_for_signal(self, signal_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            """SELECT m.interest_id, i.name, i.priority, m.semantic_score, m.provider_id, m.model_id, m.embedding_version
               FROM signal_interest_matches m JOIN interests i ON i.interest_id = m.interest_id
               WHERE m.signal_id = ? AND i.enabled = 1 ORDER BY m.semantic_score DESC, i.name COLLATE NOCASE""",
            (signal_id,),
        ).fetchall()
        return [{"interest_id": row["interest_id"], "interest": row["name"], "priority": float(row["priority"]), "semantic_score": round(float(row["semantic_score"]), 4), "provider_id": row["provider_id"], "model_id": row["model_id"], "embedding_version": row["embedding_version"]} for row in rows]

    def embedding(self, kind: str, item_id: str, provider_id: str, model_id: str, embedding_version: str) -> dict[str, Any] | None:
        table = "signal_embeddings" if kind == "signal" else "interest_embeddings"
        key = "signal_id" if kind == "signal" else "interest_id"
        with self._lock:
            row = self._connection.execute(
                f"SELECT * FROM {table} WHERE {key}=? AND provider_id=? AND model_id=? AND embedding_version=? LIMIT 1",
                (item_id, provider_id, model_id, embedding_version),
            ).fetchone()
            if row is None:
                return None
            return {"item_id": item_id, "provider_id": row["provider_id"], "model_id": row["model_id"], "dimensions": int(row["dimensions"]), "embedding_version": row["embedding_version"], "source_text_hash": row["source_text_hash"], "vector": json.loads(row["vector_json"]), "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def save_embedding(self, kind: str, item_id: str, provider_id: str, model_id: str, dimensions: int, embedding_version: str, source_text_hash: str, vector: list[float]) -> None:
        table = "signal_embeddings" if kind == "signal" else "interest_embeddings"
        key = "signal_id" if kind == "signal" else "interest_id"
        stamp = utc_now()
        with self._lock:
            self._connection.execute(
                f"""INSERT INTO {table} ({key},provider_id,model_id,dimensions,embedding_version,source_text_hash,vector_json,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT({key},provider_id,model_id,embedding_version) DO UPDATE SET dimensions=excluded.dimensions,source_text_hash=excluded.source_text_hash,vector_json=excluded.vector_json,updated_at=excluded.updated_at""",
                (item_id, provider_id, model_id, int(dimensions), embedding_version, source_text_hash, json.dumps([float(value) for value in vector], separators=(",", ":")), stamp, stamp),
            )
            self._connection.commit()

    def embedding_counts(self, provider_id: str, model_id: str, embedding_version: str) -> dict[str, int]:
        with self._lock:
            interest_count = self._connection.execute(
                "SELECT COUNT(*) FROM interest_embeddings WHERE provider_id=? AND model_id=? AND embedding_version=?",
                (provider_id, model_id, embedding_version),
            ).fetchone()[0]
            signal_count = self._connection.execute(
                "SELECT COUNT(*) FROM signal_embeddings WHERE provider_id=? AND model_id=? AND embedding_version=?",
                (provider_id, model_id, embedding_version),
            ).fetchone()[0]
            return {"interests": int(interest_count), "signals": int(signal_count)}

    def replace_signal_matches(self, signal_id: str, interest_matches: list[dict[str, Any]], provider_id: str, model_id: str, embedding_version: str) -> None:
        stamp = utc_now()
        with self._lock:
            self._connection.execute("DELETE FROM signal_interest_matches WHERE signal_id=? AND provider_id=? AND model_id=? AND embedding_version=?", (signal_id, provider_id, model_id, embedding_version))
            for item in interest_matches:
                self._connection.execute(
                    "INSERT INTO signal_interest_matches (signal_id,interest_id,provider_id,model_id,embedding_version,semantic_score,matched_at) VALUES (?,?,?,?,?,?,?)",
                    (signal_id, item["interest_id"], provider_id, model_id, embedding_version, float(item["semantic_score"]), stamp),
                )
            self._connection.commit()

    def list_interests(self, active_only: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            query = "SELECT * FROM interests"
            if active_only:
                query += " WHERE enabled=1 AND semantic_enabled=1"
            query += " ORDER BY priority DESC, name COLLATE NOCASE"
            rows = self._connection.execute(query).fetchall()
            return [{"interest_id": row["interest_id"], "name": row["name"], "description": row["description"], "enabled": bool(row["enabled"]), "priority": float(row["priority"]), "aliases": json.loads(row["aliases_json"]), "semantic_enabled": bool(row["semantic_enabled"]), "created_at": row["created_at"], "updated_at": row["updated_at"]} for row in rows]

    def upsert_interest(self, value: dict[str, Any]) -> dict[str, Any]:
        name = " ".join(str(value.get("name") or "").split()).strip()
        if not name or len(name) > 200:
            raise ValueError("Interest name must be between 1 and 200 characters")
        description = " ".join(str(value.get("description") or name).split()).strip()[:2_000]
        interest_id = str(value.get("interest_id") or "interest-" + hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()[:24])
        aliases = value.get("aliases") if isinstance(value.get("aliases"), list) else []
        aliases = [" ".join(str(item).split()).strip()[:120] for item in aliases if str(item).strip()][:30]
        stamp = utc_now()
        with self._lock:
            self._connection.execute(
                """INSERT INTO interests (interest_id,name,description,enabled,priority,aliases_json,semantic_enabled,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(interest_id) DO UPDATE SET name=excluded.name,description=excluded.description,enabled=excluded.enabled,priority=excluded.priority,aliases_json=excluded.aliases_json,semantic_enabled=excluded.semantic_enabled,updated_at=excluded.updated_at""",
                (interest_id, name, description, 1 if value.get("enabled", True) else 0, max(0.0, min(float(value.get("priority", 1.0)), 5.0)), json.dumps(aliases, ensure_ascii=False), 1 if value.get("semantic_enabled", True) else 0, stamp, stamp),
            )
            self._connection.commit()
            return next(item for item in self.list_interests() if item["interest_id"] == interest_id)

    def learned_preferences(self) -> dict[str, Any]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM learned_preferences ORDER BY score DESC, label COLLATE NOCASE").fetchall()
            grouped: dict[str, list[dict[str, Any]]] = {"source": [], "category": [], "interest": []}
            for row in rows:
                grouped.setdefault(str(row["dimension"]), []).append({"key": row["preference_key"], "label": row["label"], "score": round(float(row["score"]), 4), "evidence_count": int(row["evidence_count"]), "useful_count": int(row["useful_count"]), "interesting_count": int(row["interesting_count"]), "not_useful_count": int(row["not_useful_count"]), "state": "strong interest" if float(row["score"]) >= 0.35 else "reduced interest" if float(row["score"]) <= -0.2 else "emerging interest"})
            return {"sources": grouped.get("source", []), "categories": grouped.get("category", []), "interests": grouped.get("interest", []), "evidence_total": sum(item["evidence_count"] for values in grouped.values() for item in values)}

    def rebuild_learned_preferences(self) -> dict[str, Any]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT f.feedback_value, s.source_name, s.category, m.interest_id, i.name
                   FROM signal_feedback f JOIN signals s ON s.signal_id=f.signal_id
                   LEFT JOIN signal_interest_matches m ON m.signal_id=s.signal_id AND m.semantic_score >= 0.55
                   LEFT JOIN interests i ON i.interest_id=m.interest_id AND i.enabled=1"""
            ).fetchall()
            aggregate: dict[tuple[str, str], dict[str, int]] = {}
            for row in rows:
                targets = [("source", str(row["source_name"])), ("category", str(row["category"]))]
                if row["interest_id"] and row["name"]:
                    targets.append(("interest", str(row["name"])))
                for dimension, label in targets:
                    stats = aggregate.setdefault((dimension, label), {"evidence_count": 0, "useful_count": 0, "interesting_count": 0, "not_useful_count": 0})
                    stats["evidence_count"] += 1
                    stats[f"{row['feedback_value']}_count"] += 1
            self._connection.execute("DELETE FROM learned_preferences")
            stamp = utc_now()
            for (dimension, label), stats in aggregate.items():
                score = max(-1.0, min(1.0, (stats["useful_count"] * 1.0 + stats["interesting_count"] * 0.45 - stats["not_useful_count"] * 0.7) / max(3.0, stats["evidence_count"])))
                key = dimension + ":" + hashlib.sha256(label.casefold().encode("utf-8")).hexdigest()[:20]
                self._connection.execute("INSERT INTO learned_preferences (preference_key,dimension,label,score,evidence_count,useful_count,interesting_count,not_useful_count,updated_at) VALUES (?,?,?,?,?,?,?,?,?)", (key, dimension, label, score, stats["evidence_count"], stats["useful_count"], stats["interesting_count"], stats["not_useful_count"], stamp))
            self._connection.commit()
        return self.learned_preferences()

    def reset_learned_preferences(self) -> dict[str, Any]:
        with self._lock:
            self._connection.execute("DELETE FROM learned_preferences")
            self._connection.commit()
        return self.learned_preferences()

    def list_sources(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM signal_sources ORDER BY name COLLATE NOCASE").fetchall()
            return [{"source_id": row["source_id"], "name": row["name"], "adapter_type": row["adapter_type"], "endpoint": row["endpoint"], "category": row["category"], "enabled": bool(row["enabled"]), "last_attempt_at": row["last_attempt_at"], "last_success_at": row["last_success_at"], "item_count": int(row["item_count"]), "health": row["health_state"], "error": row["error"]} for row in rows]

    def upsert_source(self, value: dict[str, Any]) -> dict[str, Any]:
        name = " ".join(str(value.get("name") or "").split()).strip()
        endpoint = str(value.get("endpoint") or value.get("url") or "").strip()
        adapter = str(value.get("adapter_type") or "rss_atom").strip()
        if not name or (not endpoint.startswith(("http://", "https://")) and not (adapter == "n8n_or_external" and endpoint.startswith("n8n://"))):
            raise ValueError("A source name and HTTP(S) endpoint are required")
        source_id = str(value.get("source_id") or "source-" + hashlib.sha256((name + endpoint).casefold().encode("utf-8")).hexdigest()[:24])
        stamp = utc_now()
        with self._lock:
            self._connection.execute("""INSERT INTO signal_sources (source_id,name,adapter_type,endpoint,category,enabled,health_state,error,created_at,updated_at)
                VALUES (?,?,?,?,?,?, 'unknown','',?,?) ON CONFLICT(source_id) DO UPDATE SET name=excluded.name,adapter_type=excluded.adapter_type,endpoint=excluded.endpoint,category=excluded.category,enabled=excluded.enabled,updated_at=excluded.updated_at""", (source_id, name, adapter, endpoint, str(value.get("category") or "Main News Feed"), 1 if value.get("enabled", True) else 0, stamp, stamp))
            self._connection.commit()
            return next(item for item in self.list_sources() if item["source_id"] == source_id)

    def update_source_status(self, name: str, *, state: str, item_count: int = 0, error: str = "") -> None:
        stamp = utc_now()
        with self._lock:
            self._connection.execute("UPDATE signal_sources SET last_attempt_at=?, last_success_at=CASE WHEN ?='healthy' THEN ? ELSE last_success_at END, item_count=?, health_state=?, error=?, updated_at=? WHERE name=? COLLATE NOCASE", (stamp, state, stamp, int(item_count), state, error[:500], stamp, name))
            self._connection.commit()

    def enabled_rss_sources(self) -> list[dict[str, Any]]:
        return [item for item in self.list_sources() if item["enabled"] and item["adapter_type"] in {"rss_atom", "rss", "atom"}]

    def delete_source(self, source_id: str) -> bool:
        with self._lock:
            cursor = self._connection.execute("DELETE FROM signal_sources WHERE source_id=?", (source_id,))
            self._connection.commit()
            return cursor.rowcount > 0

    def save_briefing(self, signals: Iterable[Signal], collection: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            selected = list(signals)
            topics = self.watchlist_topics()
            payload = {"signals": [self._signal_payload(signal.as_dict(), topics) for signal in selected]}
            briefing = {"briefing_id": "briefing-" + uuid.uuid4().hex[:16], "generated_at": utc_now(), "signal_count": len(selected), "signals": payload["signals"], "collection": collection}
            self._connection.execute("INSERT INTO briefings (briefing_id,generated_at,signal_count,signals_json,collection_json) VALUES (?,?,?,?,?)", (briefing["briefing_id"], briefing["generated_at"], len(selected), json.dumps(payload["signals"], ensure_ascii=False), json.dumps(collection, ensure_ascii=False)))
            self._connection.commit()
            return briefing

    def latest_briefing(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM briefings ORDER BY generated_at DESC, rowid DESC LIMIT 1").fetchone()
            if not row:
                return None
            signals = json.loads(row["signals_json"])
            feedback = self.latest_feedback([item.get("signal_id") for item in signals if isinstance(item, dict)])
            topics = self.watchlist_topics()
            for item in signals:
                if not isinstance(item, dict):
                    continue
                item["watchlist_matches"] = self._signal_watchlist_matches(item, topics)
                if item.get("signal_id") in feedback:
                    item["feedback"] = feedback[item["signal_id"]]
            return {"briefing_id": row["briefing_id"], "generated_at": row["generated_at"], "signal_count": row["signal_count"], "signals": signals, "collection": json.loads(row["collection_json"])}

    @staticmethod
    def _watchlist_text(value: object) -> str:
        text = str(value or "").casefold()
        return re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).strip()

    @classmethod
    def _signal_watchlist_matches(cls, signal: dict[str, Any], topics: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        haystack = cls._watchlist_text(" ".join(str(signal.get(key) or "") for key in ("title", "summary", "content", "source_name")))
        if not haystack:
            return []
        padded_haystack = f" {haystack} "
        matches: list[dict[str, Any]] = []
        for topic in topics:
            normalized = cls._watchlist_text(topic.get("topic"))
            if normalized and f" {normalized} " in padded_haystack:
                matches.append({"topic_id": topic["topic_id"], "topic": topic["topic"]})
        return matches

    @classmethod
    def _signal_payload(cls, signal: dict[str, Any], topics: Iterable[dict[str, Any]]) -> dict[str, Any]:
        payload = dict(signal)
        payload["watchlist_matches"] = cls._signal_watchlist_matches(payload, topics)
        return payload

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

    def record_interaction(self, signal_id: str, value: str, recorded_at: str | None = None) -> dict[str, Any]:
        if value != "tldr":
            raise ValueError("Interaction must be tldr")
        with self._lock:
            timestamp = recorded_at or utc_now()
            row = self._connection.execute("SELECT signal_id FROM signals WHERE signal_id = ?", (signal_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown signal_id")
            self._connection.execute(
                "INSERT INTO signal_interactions (signal_id,interaction_type,recorded_at) VALUES (?,?,?)",
                (signal_id, value, timestamp),
            )
            self._connection.commit()
            return {"signal_id": signal_id, "interaction": value, "recorded_at": timestamp}

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

    def image_enrichment_state(self, dedupe_key: str) -> dict[str, str] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT signal_id,image_url,image_enrichment_attempted_at FROM signals WHERE dedupe_key = ? LIMIT 1",
                (dedupe_key,),
            ).fetchone()
            if row is None:
                return None
            return {"signal_id": str(row["signal_id"]), "image_url": str(row["image_url"] or ""), "attempted_at": str(row["image_enrichment_attempted_at"] or "")}

    def mark_image_enrichment(self, signal_id: str, image_url: str = "", error: str = "") -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE signals SET image_url = CASE WHEN ? <> '' THEN ? ELSE image_url END, image_enrichment_attempted_at = ?, image_enrichment_error = ? WHERE signal_id = ?",
                (image_url, image_url, utc_now(), error[:500], signal_id),
            )
            self._connection.commit()

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
