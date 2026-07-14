#!/usr/bin/env python3
"""Read-only MCP server for an Ariadne KnowledgeVault.

Uses 00_System/library.json as the catalogue and Processed Markdown as the
source corpus. The embedding index is derived, rebuildable state only.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from ariadne_embeddings import DEFAULT_MODEL, chunk_hash, cosine, load_index, ollama_embed


# MCP stdio transport is UTF-8 JSON; Windows PowerShell may otherwise select a
# legacy console code page when stdout is redirected.
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")


ROOT = Path(__file__).resolve().parent.parent
LIBRARY_PATH = ROOT / "00_System" / "library.json"
PROCESSED_ROOT = (ROOT / "Processed").resolve()
MAX_RESULT_LIMIT = 20
MAX_DOCUMENT_CHARS = 24_000
MAX_CHUNK_CHARS = 2_400
DEFAULT_CHUNK_CHARS = 1_600
TOKEN_RE = re.compile(r"[\w-]+", re.UNICODE)
EMBEDDING_INDEX_CACHE: tuple[float, dict[str, Any] | None] | None = None


def send(message: dict[str, Any]) -> None:
    print(json.dumps(message, ensure_ascii=False, separators=(",", ":")), flush=True)


def error(request_id: Any, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def load_library() -> list[dict[str, Any]]:
    try:
        data = json.loads(LIBRARY_PATH.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Authoritative library index is missing: {LIBRARY_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Authoritative library index is invalid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise RuntimeError("Authoritative library index must contain a JSON array.")
    return [item for item in data if isinstance(item, dict)]


def tokens(value: str) -> list[str]:
    return [token.casefold() for token in TOKEN_RE.findall(value)]


def string_list(value: Any) -> str:
    return " ".join(str(item) for item in value) if isinstance(value, list) else ""


def score_record(record: dict[str, Any], query: str) -> float:
    query_tokens = set(tokens(query))
    if not query_tokens:
        return 0.0
    fields = (
        ("page_title", 8),
        ("primary_topic", 7),
        ("subtopics", 6),
        ("tags", 5),
        ("map_entry", 4),
        ("summary", 3),
        ("links", 1),
    )
    score = 0.0
    query_folded = query.casefold().strip()
    for name, weight in fields:
        value = string_list(record.get(name)) if name in {"subtopics", "tags", "links"} else str(record.get(name) or "")
        text = value.casefold()
        if query_folded and query_folded in text:
            score += weight * 2
        score += weight * len(query_tokens.intersection(tokens(value)))
    return score


def processed_path(record: dict[str, Any]) -> Path | None:
    relative = record.get("processed_path")
    if not isinstance(relative, str) or not relative:
        return None
    candidate = (ROOT / relative).resolve()
    try:
        candidate.relative_to(PROCESSED_ROOT)
    except ValueError:
        return None
    return candidate


def excerpt(record: dict[str, Any], query: str, limit: int = 700) -> str:
    path = processed_path(record)
    if not path or not path.is_file():
        return str(record.get("summary") or "")[:limit]
    try:
        content = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return str(record.get("summary") or "")[:limit]
    match = re.search(re.escape(query.strip()), content, re.IGNORECASE) if query.strip() else None
    start = max(0, match.start() - 220) if match else 0
    snippet = re.sub(r"\s+", " ", content[start : start + limit]).strip()
    return ("…" if start else "") + snippet


def markdown_chunks(content: str, size: int = DEFAULT_CHUNK_CHARS) -> list[tuple[str, str]]:
    """Split Markdown into small, self-contained passages.

    Headings begin a new passage. Long passages use a small overlap so a fact
    split across a boundary is not silently lost. Chunks are derived at query
    time: the Markdown remains the only source of truth.
    """
    blocks = [block.strip() for block in re.split(r"\n\s*\n", content) if block.strip()]
    chunks: list[tuple[str, str]] = []
    heading = "Document"
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        text = "\n\n".join(buffer).strip()
        if not text:
            return
        start = 0
        while start < len(text):
            end = min(len(text), start + size)
            if end < len(text):
                boundary = text.rfind("\n", start, end)
                if boundary > start + size // 2:
                    end = boundary
            piece = text[start:end].strip()
            if piece:
                chunks.append((heading, piece))
            if end >= len(text):
                break
            start = max(end - 180, start + 1)
        buffer = []

    for block in blocks:
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", block)
        if match:
            flush()
            heading = match.group(2)
            buffer = [block]
        else:
            candidate = "\n\n".join(buffer + [block])
            if len(candidate) > size and buffer:
                flush()
            buffer.append(block)
    flush()
    return chunks


def score_text(text: str, query: str) -> float:
    query_tokens = set(tokens(query))
    if not query_tokens:
        return 0.0
    folded = text.casefold()
    score = 0.0
    query_folded = query.casefold().strip()
    if query_folded and query_folded in folded:
        score += 12.0
    score += 3.0 * len(query_tokens.intersection(tokens(text)))
    return score


def chunk_records() -> list[dict[str, Any]]:
    """Return the exact heading-aware chunks used by both indexing and MCP."""
    result = []
    for record in load_library():
        path = processed_path(record)
        if not path or not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        relative = path.relative_to(ROOT).as_posix()
        citation = {
            "source_url": record.get("source_url"),
            "source_name": record.get("source_name"),
            "source_language": record.get("source_language"),
            "publication_date": record.get("publication_date"),
            "channel_author": record.get("channel_author"),
        }
        for index, (heading, chunk) in enumerate(markdown_chunks(content)):
            chunk_id = f"{record.get('document_id')}#chunk-{index}"
            result.append({"path": relative, "document_id": record.get("document_id"), "chunk_id": chunk_id,
                           "heading": heading, "title": record.get("page_title") or record.get("source_name"),
                           "content": chunk, "content_hash": chunk_hash(heading, chunk),
                           "document_content_hash": record.get("content_sha256"),
                           "citation": citation})
    return result


def embedding_index() -> dict[str, Any] | None:
    global EMBEDDING_INDEX_CACHE
    path = ROOT / "00_System" / "Data" / "embedding-index.json"
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return None
    if EMBEDDING_INDEX_CACHE and EMBEDDING_INDEX_CACHE[0] == stamp:
        return EMBEDDING_INDEX_CACHE[1]
    index = load_index(ROOT)
    EMBEDDING_INDEX_CACHE = (stamp, index)
    return index


def graph_score(record: dict[str, Any], query: str) -> float:
    """Conservative existing entity/graph metadata signal, normalized to 0..1."""
    query_tokens = set(tokens(query))
    signals = []
    for field in ("links", "entities", "people", "related_notes", "subtopics", "primary_topic", "secondary_domains"):
        value = string_list(record.get(field)) if isinstance(record.get(field), list) else str(record.get(field) or "")
        if query_tokens and query_tokens.intersection(tokens(value)):
            signals.append(1)
    return min(1.0, len(signals) / 3.0)


def search(arguments: dict[str, Any]) -> dict[str, Any]:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("'query' must be a non-empty string.")
    limit = arguments.get("limit", 5)
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("'limit' must be an integer.")
    limit = max(1, min(limit, MAX_RESULT_LIMIT))
    ranked = [(score_record(record, query), record) for record in load_library()]
    ranked = [(score, record) for score, record in ranked if score > 0]
    ranked.sort(key=lambda item: (-item[0], str(item[1].get("document_id") or "")))
    results = []
    for score, record in ranked[:limit]:
        results.append({
            "document_id": record.get("document_id"),
            "title": record.get("page_title") or record.get("source_name"),
            "score": score,
            "source_url": record.get("source_url"),
            "source_language": record.get("source_language"),
            "primary_topic": record.get("primary_topic"),
            "summary": record.get("summary"),
            "processed_path": record.get("processed_path"),
            "excerpt": excerpt(record, query),
        })
    return {"query": query, "match_count": len(results), "results": results}


def search_chunks(arguments: dict[str, Any]) -> dict[str, Any]:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("'query' must be a non-empty string.")
    limit = arguments.get("limit", 5)
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("'limit' must be an integer.")
    limit = max(1, min(limit, MAX_RESULT_LIMIT))

    # Bounded candidate selection: lexical/catalogue candidates and semantic
    # candidates are unioned before a final hybrid rank. This permits synonym
    # queries while avoiding a full-vault result payload.
    ranked_records = [(score_record(record, query), record) for record in load_library()]
    ranked_records.sort(key=lambda item: (-item[0], str(item[1].get("document_id") or "")))
    records_by_id = {record.get("document_id"): record for _, record in ranked_records}
    lexical_ids = {record.get("document_id") for score, record in ranked_records[: max(limit * 6, 24)] if score > 0}
    index = embedding_index()
    indexed_by_chunk = {
        str(entry.get("chunk_id")): entry
        for entry in (index or {}).get("entries", {}).values()
        if isinstance(entry, dict)
    }
    semantic_by_chunk: dict[str, float] = {}
    if index and index.get("entries"):
        try:
            query_vector = ollama_embed(query, str(index.get("model") or DEFAULT_MODEL))
            scored = [(cosine(query_vector, entry.get("embedding", [])), entry) for entry in index["entries"].values()]
            scored.sort(key=lambda item: -item[0])
            for semantic, entry in scored[: max(limit * 12, 48)]:
                if semantic > 0:
                    semantic_by_chunk[str(entry.get("chunk_id"))] = semantic
        except RuntimeError:
            # Search remains usable if Ollama is offline after indexing.
            pass
    candidate_ids = lexical_ids | {item.rsplit("#chunk-", 1)[0] for item in semantic_by_chunk}
    candidates = []
    for document_id in candidate_ids:
        record = records_by_id.get(document_id)
        if not record:
            continue
        document_score = score_record(record, query)
        path = processed_path(record)
        if not path or not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for index, (heading, chunk) in enumerate(markdown_chunks(content)):
            passage_score = score_text(chunk, query)
            chunk_id = f"{record.get('document_id')}#chunk-{index}"
            lexical = document_score + passage_score
            semantic = semantic_by_chunk.get(chunk_id, 0.0)
            graph = graph_score(record, query)
            # Lexical values are unbounded; compress them before blending.
            lexical_normalized = lexical / (lexical + 12.0) if lexical else 0.0
            combined = 0.50 * lexical_normalized + 0.40 * semantic + 0.10 * graph
            if combined > 0:
                candidates.append((combined, lexical, semantic, graph, record, index, heading, chunk))

    candidates.sort(key=lambda item: (-item[0], str(item[4].get("document_id") or ""), item[5]))
    results = []
    seen = set()
    for combined, lexical, semantic, graph, record, index, heading, chunk in candidates:
        # Avoid returning overlapping windows from the same part of a document.
        key = (record.get("document_id"), index)
        if key in seen:
            continue
        seen.add(key)
        indexed_entry = indexed_by_chunk.get(chunk_id)
        if indexed_entry and indexed_entry.get("content_hash") != chunk_hash(heading, chunk):
            indexed_entry = None
        citation = indexed_entry.get("citation", {}) if indexed_entry else {}
        results.append({
            "chunk_id": f"{record.get('document_id')}#chunk-{index}",
            "document_id": record.get("document_id"),
            "path": indexed_entry.get("path") if indexed_entry else path.relative_to(ROOT).as_posix(),
            "title": indexed_entry.get("title") if indexed_entry else record.get("page_title") or record.get("source_name"),
            "source_url": citation.get("source_url", record.get("source_url")),
            "citation": citation,
            "heading": heading,
            "score": combined,
            "lexical_score": round(lexical, 6),
            "semantic_score": round(semantic, 6),
            "graph_score": round(graph, 6),
            "combined_score": round(combined, 6),
            "content": chunk,
        })
        if len(results) >= limit:
            break
    return {"query": query, "match_count": len(results), "results": results}


def get_chunk(arguments: dict[str, Any]) -> dict[str, Any]:
    chunk_id = arguments.get("chunk_id")
    if not isinstance(chunk_id, str) or "#chunk-" not in chunk_id:
        raise ValueError("'chunk_id' must be a chunk_id returned by search_knowledge_chunks.")
    document_id, _, index_text = chunk_id.rpartition("#chunk-")
    try:
        index = int(index_text)
    except ValueError as exc:
        raise ValueError("'chunk_id' has an invalid chunk index.") from exc
    record = next((item for item in load_library() if item.get("document_id") == document_id), None)
    if not record:
        raise ValueError("No document with that document_id exists in library.json.")
    path = processed_path(record)
    if not path or not path.is_file():
        raise ValueError("The indexed processed Markdown file is unavailable.")
    chunks = markdown_chunks(path.read_text(encoding="utf-8-sig", errors="replace"))
    if index < 0 or index >= len(chunks):
        raise ValueError("The requested chunk no longer exists; run search_knowledge_chunks again.")
    heading, content = chunks[index]
    return {"chunk_id": chunk_id, "document_id": document_id, "title": record.get("page_title") or record.get("source_name"), "source_url": record.get("source_url"), "heading": heading, "content": content}


def get_document(arguments: dict[str, Any]) -> dict[str, Any]:
    document_id = arguments.get("document_id")
    if not isinstance(document_id, str) or not document_id:
        raise ValueError("'document_id' must be a non-empty string.")
    record = next((item for item in load_library() if item.get("document_id") == document_id), None)
    if not record:
        raise ValueError("No document with that document_id exists in library.json.")
    path = processed_path(record)
    if not path or not path.is_file():
        raise ValueError("The indexed processed Markdown file is unavailable.")
    offset = arguments.get("offset", 0)
    max_chars = arguments.get("max_chars", 12_000)
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("'offset' must be a non-negative integer.")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1:
        raise ValueError("'max_chars' must be a positive integer.")
    max_chars = min(max_chars, MAX_DOCUMENT_CHARS)
    content = path.read_text(encoding="utf-8-sig", errors="replace")
    return {
        "document_id": document_id,
        "title": record.get("page_title") or record.get("source_name"),
        "source_url": record.get("source_url"),
        "offset": offset,
        "content": content[offset : offset + max_chars],
        "next_offset": offset + max_chars if offset + max_chars < len(content) else None,
    }


TOOLS = [
    {"name": "search_knowledge_chunks", "description": "Preferred retrieval tool. Search the KnowledgeVault and return only the highest-ranked Markdown passages, with source and heading. Use this before answering vault questions; answer from these chunks unless more context is necessary.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": MAX_RESULT_LIMIT}}, "required": ["query"], "additionalProperties": False}},
    {"name": "get_knowledge_chunk", "description": "Retrieve one exact passage returned by search_knowledge_chunks. Use only when the returned passage was truncated or needs re-reading.", "inputSchema": {"type": "object", "properties": {"chunk_id": {"type": "string"}}, "required": ["chunk_id"], "additionalProperties": False}},
    {"name": "search_knowledge_vault", "description": "Legacy document-level catalogue search with excerpts. Prefer search_knowledge_chunks for question answering.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": MAX_RESULT_LIMIT}}, "required": ["query"], "additionalProperties": False}},
    {"name": "get_knowledge_document", "description": "Read a processed Markdown document returned by search_knowledge_vault. Content is read-only and may be paged with offset.", "inputSchema": {"type": "object", "properties": {"document_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}, "max_chars": {"type": "integer", "minimum": 1, "maximum": MAX_DOCUMENT_CHARS}}, "required": ["document_id"], "additionalProperties": False}},
]


def handle(request: dict[str, Any]) -> None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "notifications/initialized":
        return
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": request.get("params", {}).get("protocolVersion", "2024-11-05"), "capabilities": {"tools": {}}, "serverInfo": {"name": "ariadne-knowledge-vault", "version": "0.1.0"}}})
        return
    if method == "tools/list":
        send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})
        return
    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            if name == "search_knowledge_chunks":
                result = search_chunks(arguments)
            elif name == "get_knowledge_chunk":
                result = get_chunk(arguments)
            elif name == "search_knowledge_vault":
                result = search(arguments)
            elif name == "get_knowledge_document":
                result = get_document(arguments)
            else:
                raise ValueError(f"Unknown tool: {name}")
            send({"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}})
        except (RuntimeError, ValueError, OSError) as exc:
            send({"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": str(exc)}], "isError": True}})
        return
    if request_id is not None:
        error(request_id, -32601, f"Method not found: {method}")


def main() -> None:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Request must be a JSON object.")
            handle(request)
        except json.JSONDecodeError:
            error(None, -32700, "Parse error")
        except ValueError as exc:
            error(None, -32600, str(exc))


if __name__ == "__main__":
    main()
