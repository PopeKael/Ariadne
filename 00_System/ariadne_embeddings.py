#!/usr/bin/env python3
"""Local, atomic embedding index support for Ariadne.

The store is deliberately a plain JSON file: it is inspectable, portable, and
does not introduce a vector database or any network destination beyond Ollama's
loopback HTTP API.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

INDEX_VERSION = 1
DEFAULT_MODEL = os.environ.get("ARIADNE_EMBEDDING_MODEL", "nomic-embed-text")
DEFAULT_OLLAMA_URL = os.environ.get("ARIADNE_OLLAMA_URL", "http://127.0.0.1:11434")


def index_path(root: Path) -> Path:
    return root / "00_System" / "Data" / "embedding-index.json"


def chunk_hash(heading: str, content: str) -> str:
    return hashlib.sha256((heading + "\n" + content).encode("utf-8")).hexdigest()


def embedding_key(path: str, heading: str, chunk_id: str, content_hash: str, model: str) -> str:
    raw = "\x1f".join((path, heading, chunk_id, content_hash, model, str(INDEX_VERSION)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def empty_index(model: str = DEFAULT_MODEL) -> dict[str, Any]:
    return {"format_version": INDEX_VERSION, "model": model, "dimensions": 0,
            "created_at": time.time(), "updated_at": time.time(), "entries": {}, "failures": {}}


def load_index(root: Path) -> dict[str, Any] | None:
    path = index_path(root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("format_version") != INDEX_VERSION or not isinstance(value.get("entries"), dict):
            return None
        return value
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def atomic_write(root: Path, value: dict[str, Any]) -> None:
    path = index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="embedding-index-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def ollama_embed(text: str, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_OLLAMA_URL) -> list[float]:
    """Use only an explicitly local Ollama endpoint."""
    if not base_url.startswith(("http://127.0.0.1", "http://localhost", "https://127.0.0.1", "https://localhost")):
        raise RuntimeError("Ariadne only permits a loopback Ollama endpoint.")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/embed",
        data=json.dumps({"model": model, "input": text, "truncate": True}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama is unavailable at {base_url}: {exc.reason}") from exc
    except (TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Ollama embedding request failed: {exc}") from exc
    vectors = payload.get("embeddings")
    if not isinstance(vectors, list) or not vectors or not isinstance(vectors[0], list):
        raise RuntimeError(f"Ollama did not return an embedding; ensure model '{model}' is installed.")
    return [float(item) for item in vectors[0]]


def ollama_embed_many(texts: list[str], model: str = DEFAULT_MODEL, base_url: str = DEFAULT_OLLAMA_URL) -> list[list[float]]:
    """Batch inputs to reduce local Ollama request overhead."""
    if not texts:
        return []
    if not base_url.startswith(("http://127.0.0.1", "http://localhost", "https://127.0.0.1", "https://localhost")):
        raise RuntimeError("Ariadne only permits a loopback Ollama endpoint.")
    request = urllib.request.Request(base_url.rstrip("/") + "/api/embed",
        data=json.dumps({"model": model, "input": texts, "truncate": True}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            vectors = json.loads(response.read().decode("utf-8")).get("embeddings")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama is unavailable at {base_url}: {exc.reason}") from exc
    if not isinstance(vectors, list) or len(vectors) != len(texts) or not all(isinstance(v, list) and v for v in vectors):
        raise RuntimeError(f"Ollama did not return embeddings; ensure model '{model}' is installed.")
    return [[float(item) for item in vector] for vector in vectors]


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def build_index(root: Path, chunks: list[dict[str, Any]], rebuild: bool = False,
                embed: Callable[[str, str], list[float]] | None = None, model: str = DEFAULT_MODEL) -> dict[str, Any]:
    """Build a new complete state in memory, then commit it once atomically."""
    started = time.monotonic()
    old = None if rebuild else load_index(root)
    base = empty_index(model) if old is None or old.get("model") != model else old
    old_entries = base.get("entries", {})
    next_index = empty_index(model)
    next_index["created_at"] = base.get("created_at", time.time())
    existing_by_key = old_entries
    custom_embed = embed
    stats = {"indexed": 0, "skipped": 0, "failed": 0, "removed": 0, "documents": len({c['path'] for c in chunks})}
    wanted: set[str] = set()
    pending: list[tuple[str, dict[str, Any]]] = []
    for chunk in chunks:
        key = embedding_key(chunk["path"], chunk["heading"], chunk["chunk_id"], chunk["content_hash"], model)
        wanted.add(key)
        existing = existing_by_key.get(key)
        if existing and isinstance(existing.get("embedding"), list):
            next_index["entries"][key] = existing
            stats["skipped"] += 1
            continue
        pending.append((key, chunk))
    for start in range(0, len(pending), 32):
        batch = pending[start:start + 32]
        try:
            if custom_embed is None:
                vectors = ollama_embed_many([chunk["heading"] + "\n\n" + chunk["content"] for _, chunk in batch], model)
            else:
                vectors = [custom_embed(chunk["heading"] + "\n\n" + chunk["content"], model) for _, chunk in batch]
            for (key, chunk), vector in zip(batch, vectors):
                next_index["entries"][key] = {**chunk, "key": key, "model": model, "embedding_version": INDEX_VERSION,
                                                "embedding": vector, "indexed_at": time.time()}
                next_index["dimensions"] = len(vector)
                stats["indexed"] += 1
        except Exception as exc:  # Each chunk is independently resumable.
            for key, chunk in batch:
                next_index["failures"][key] = {**chunk, "key": key, "model": model, "embedding_version": INDEX_VERSION,
                                                 "error": str(exc), "failed_at": time.time()}
                stats["failed"] += 1
    stats["removed"] = len(set(existing_by_key) - wanted)
    if not next_index["dimensions"] and next_index["entries"]:
        next_index["dimensions"] = len(next(iter(next_index["entries"].values())).get("embedding", []))
    next_index["updated_at"] = time.time()
    atomic_write(root, next_index)
    stats["elapsed_seconds"] = round(time.monotonic() - started, 3)
    stats["chunks"] = len(next_index["entries"])
    stats["storage_bytes"] = index_path(root).stat().st_size
    return {**stats, "model": model, "dimensions": next_index["dimensions"], "failure_count": len(next_index["failures"])}
