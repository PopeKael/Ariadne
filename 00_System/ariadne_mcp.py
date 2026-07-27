#!/usr/bin/env python3
"""Read-only MCP server for an Ariadne KnowledgeVault.

Uses 00_System/library.json as the catalogue and Processed Markdown as the
source corpus. The embedding index is derived, rebuildable state only.
"""

from __future__ import annotations

import json
import os
import re
import sys
import argparse
import hashlib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ariadne_embeddings import DEFAULT_MODEL, DEFAULT_OLLAMA_URL, chunk_hash, cosine, load_index, ollama_embed


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


def markdown_chunks(content: str, size: int = DEFAULT_CHUNK_CHARS) -> list[dict[str, Any]]:
    """Split Markdown into small, self-contained passages.

    Headings begin a new passage. Long passages use a small overlap so a fact
    split across a boundary is not silently lost. Chunks are derived at query
    time: the Markdown remains the only source of truth.
    """
    blocks = []
    for match in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", content, re.DOTALL):
        blocks.append((match.start(), match.end(), match.group(0)))
    chunks: list[dict[str, Any]] = []
    heading = "Document"
    buffer: list[tuple[int, int, str]] = []

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        source_start = buffer[0][0]
        source_end = buffer[-1][1]
        text = content[source_start:source_end].strip()
        source_start += len(content[source_start:source_end]) - len(content[source_start:source_end].lstrip())
        if not text:
            return
        start = 0
        while start < len(text):
            end = min(len(text), start + size)
            if end < len(text):
                boundary = text.rfind("\n", start, end)
                if boundary > start + size // 2:
                    end = boundary
            raw_piece = text[start:end]
            piece = raw_piece.strip()
            if piece:
                piece_start = source_start + start + len(raw_piece) - len(raw_piece.lstrip())
                piece_end = piece_start + len(piece)
                chunks.append({
                    "heading": heading,
                    "content": piece,
                    "line_start": content.count("\n", 0, piece_start) + 1,
                    "line_end": content.count("\n", 0, max(piece_start, piece_end - 1)) + 1,
                })
            if end >= len(text):
                break
            start = max(end - 180, start + 1)
        buffer = []

    for block in blocks:
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", block[2])
        if match:
            flush()
            heading = match.group(2)
            buffer = [block]
        else:
            candidate = "\n\n".join(item[2] for item in buffer + [block])
            if len(candidate) > size and buffer:
                flush()
            buffer.append(block)
    flush()
    return chunks


def build_citation(record: dict[str, Any], path: str, heading: str = "Document",
                   chunk_id: str | None = None, line_start: int | None = None,
                   line_end: int | None = None) -> dict[str, Any]:
    """Return a stable, self-contained provenance record for retrieval output."""
    title = record.get("page_title") or record.get("source_name") or path
    citation = {
        "citation_version": 1,
        "document_id": record.get("document_id"),
        "chunk_id": chunk_id,
        "path": path,
        "title": title,
        "heading": heading,
        "line_start": line_start,
        "line_end": line_end,
        "source_url": record.get("source_url"),
        "source_name": record.get("source_name"),
        "source_language": record.get("source_language"),
        "publication_date": record.get("publication_date"),
        "channel_author": record.get("channel_author"),
    }
    issues = [field for field in ("document_id", "path", "title", "heading") if not citation.get(field)]
    if chunk_id and (not isinstance(line_start, int) or not isinstance(line_end, int) or line_start < 1 or line_end < line_start):
        issues.append("line_anchor")
    citation["status"] = "complete" if not issues and citation["source_url"] else "vault-only" if not issues else "incomplete"
    citation["issues"] = issues
    return citation


def format_citation(citation: dict[str, Any]) -> str:
    """Provide an immediately usable citation while retaining structured fields."""
    anchor = ""
    if citation.get("line_start"):
        anchor = f", lines {citation['line_start']}–{citation['line_end']}"
    location = citation.get("source_url") or f"vault:{citation.get('path')}"
    return f"{citation.get('title')} — {citation.get('heading')}{anchor}; {location}"


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
        for index, chunk in enumerate(markdown_chunks(content)):
            chunk_id = f"{record.get('document_id')}#chunk-{index}"
            citation = build_citation(record, relative, chunk["heading"], chunk_id, chunk["line_start"], chunk["line_end"])
            result.append({"path": relative, "document_id": record.get("document_id"), "chunk_id": chunk_id,
                           "heading": chunk["heading"], "title": record.get("page_title") or record.get("source_name"),
                           "content": chunk["content"], "content_hash": chunk_hash(chunk["heading"], chunk["content"]),
                           "document_content_hash": record.get("content_sha256"),
                           "citation": citation, "citation_text": format_citation(citation)})
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
        path = processed_path(record)
        relative = path.relative_to(ROOT).as_posix() if path else str(record.get("processed_path") or "")
        citation = build_citation(record, relative)
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
            "citation": citation,
            "citation_text": format_citation(citation),
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
        for index, chunk_data in enumerate(markdown_chunks(content)):
            heading = chunk_data["heading"]
            chunk = chunk_data["content"]
            passage_score = score_text(chunk, query)
            chunk_id = f"{record.get('document_id')}#chunk-{index}"
            lexical = document_score + passage_score
            semantic = semantic_by_chunk.get(chunk_id, 0.0)
            graph = graph_score(record, query)
            # Lexical values are unbounded; compress them before blending.
            lexical_normalized = lexical / (lexical + 12.0) if lexical else 0.0
            combined = 0.50 * lexical_normalized + 0.40 * semantic + 0.10 * graph
            if combined > 0:
                candidates.append((combined, lexical, semantic, graph, record, index, heading, chunk, chunk_data, path))

    candidates.sort(key=lambda item: (-item[0], str(item[4].get("document_id") or ""), item[5]))
    results = []
    seen = set()
    for combined, lexical, semantic, graph, record, index, heading, chunk, chunk_data, path in candidates:
        # Avoid returning overlapping windows from the same part of a document.
        key = (record.get("document_id"), index)
        if key in seen:
            continue
        seen.add(key)
        chunk_id = f"{record.get('document_id')}#chunk-{index}"
        indexed_entry = indexed_by_chunk.get(chunk_id)
        if indexed_entry and indexed_entry.get("content_hash") != chunk_hash(heading, chunk):
            indexed_entry = None
        citation = indexed_entry.get("citation") if indexed_entry else None
        if not isinstance(citation, dict):
            citation = build_citation(record, path.relative_to(ROOT).as_posix(), heading, chunk_id,
                                      chunk_data["line_start"], chunk_data["line_end"])
        results.append({
            "chunk_id": chunk_id,
            "document_id": record.get("document_id"),
            "path": indexed_entry.get("path") if indexed_entry else path.relative_to(ROOT).as_posix(),
            "title": indexed_entry.get("title") if indexed_entry else record.get("page_title") or record.get("source_name"),
            "source_url": citation.get("source_url", record.get("source_url")),
            "citation": citation,
            "citation_text": format_citation(citation),
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


def ollama_chat(messages: list[dict[str, str]], model: str | None = None) -> str:
    """Generate text only through the configured loopback Ollama endpoint."""
    base_url = DEFAULT_OLLAMA_URL
    selected_model = model or os.environ.get("ARIADNE_CHAT_MODEL", "gpt-oss:20b")
    if not base_url.startswith(("http://127.0.0.1", "http://localhost", "https://127.0.0.1", "https://localhost")):
        raise RuntimeError("Ariadne only permits a loopback Ollama endpoint.")
    body = {"model": selected_model, "messages": messages, "stream": False,
            "options": {"temperature": 0, "seed": 42}}
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama chat is unavailable at {base_url}: {exc.reason}") from exc
    except (TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Ollama chat request failed: {exc}") from exc
    content = payload.get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"Ollama returned no final answer; ensure model '{selected_model}' is installed.")
    return content.strip()


def summarize_knowledge(arguments: dict[str, Any]) -> dict[str, Any]:
    """Retrieve evidence, then produce a bounded, evidence-grounded summary."""
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("'query' must be a non-empty string.")
    limit = arguments.get("limit", 8)
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("'limit' must be an integer.")
    limit = max(1, min(limit, MAX_RESULT_LIMIT))
    retrieval = search_chunks({"query": query, "limit": limit})
    results = retrieval["results"]
    if not results:
        return {"query": query, "summary": "No relevant vault passages were found.", "sources": [], "retrieved": retrieval}

    evidence = []
    sources = []
    for number, item in enumerate(results, 1):
        evidence.append(f"[Source {number}] {item['citation_text']}\n{item['content']}")
        sources.append({"source_number": number, "chunk_id": item.get("chunk_id"),
                        "title": item.get("title"), "citation": item.get("citation"),
                        "citation_text": item.get("citation_text")})
    system = ("You are the KnowledgeVault briefing librarian. Answer only from the supplied vault evidence. "
              "Synthesize the main points clearly and concisely. Do not invent facts or silently use general knowledge. "
              "If the evidence is incomplete, contradictory, or does not answer the question, say so. "
              "Cite claims inline using [Source N]. Use simple Markdown only; do not emit HTML tags such as <br>. "
              "When the evidence contains a web address, preserve it exactly as plain text.")
    user = f"Question: {query}\n\nVault evidence:\n\n" + "\n\n".join(evidence)
    summary = ollama_chat([{"role": "system", "content": system}, {"role": "user", "content": user}])
    return {"query": query, "summary": summary, "sources": sources, "retrieved": retrieval,
            "model": os.environ.get("ARIADNE_CHAT_MODEL", "gpt-oss:20b")}


PLANNER_MAX_SEARCHES = 6
PLANNER_MAX_QUERY_CHARS = 220
QUERY_CACHE_ROOT = ROOT / "00_System" / "Data" / "QueryCache"


def planner_json(text: str) -> dict[str, Any]:
    """Parse a planner response while tolerating a small amount of Markdown fencing."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"The local planner returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("searches"), list):
        raise RuntimeError("The local planner returned no usable search plan.")
    searches = []
    for item in value["searches"][:PLANNER_MAX_SEARCHES]:
        if isinstance(item, str) and item.strip():
            searches.append(item.strip()[:PLANNER_MAX_QUERY_CHARS])
    if not searches:
        raise RuntimeError("The local planner returned an empty search plan.")
    value["searches"] = searches
    return value


def planned_knowledge_query(query: str, limit: int = 6, progress=None, answer_mode: str = "answer") -> dict[str, Any]:
    """Interpret a question, retrieve several focused evidence sets, then answer from them."""
    def report(stage: str, message: str, completed: int = 0, total: int = 0) -> None:
        if progress:
            progress(stage, message, completed, total)

    report("planning", "Interpreting your question and preparing a retrieval plan…")
    planner_system = (
        "You are a careful library query planner. Convert the user's question into a bounded search plan "
        "for a private personal Markdown knowledge vault containing chat transcripts, project notes, and source clippings. "
        "This is not a web search and it is not an academic research database. Do not answer the question and do not invent facts. "
        "Return JSON only with keys intent, searches, and answer_instructions. "
        f"Create 2 to {PLANNER_MAX_SEARCHES} focused searches, each under {PLANNER_MAX_QUERY_CHARS} characters. "
        "Use names, aliases, projects, places, and distinctive keywords likely to occur in the vault. "
        "For identity or first-person questions, consider Warren Gerdes, Warren, Wazza, Pope Kael, "
        "Garage Alchemy, Chanya and Wazza, Ariadne, and KnowledgeVault as possible search anchors. "
        "Do not add institutions, laboratories, grants, publications, employers, or academic roles unless the user explicitly mentions them. "
        "Do not use placeholders such as [institution], and do not formulate searches as if querying the public web."
    )
    planner_text = ollama_chat([
        {"role": "system", "content": planner_system},
        {"role": "user", "content": query},
    ])
    plan = planner_json(planner_text)
    searches = plan["searches"]
    report("retrieving", f"Retrieval plan ready: searching {len(searches)} focused areas…", 0, len(searches))

    merged: dict[str, dict[str, Any]] = {}
    search_reports = []
    for number, search_query in enumerate(searches, 1):
        report("retrieving", f"Searching vault: {search_query}", number - 1, len(searches))
        retrieval = search_chunks({"query": search_query, "limit": limit})
        search_reports.append({"query": search_query, "match_count": retrieval["match_count"]})
        for item in retrieval["results"]:
            chunk_id = item.get("chunk_id")
            if chunk_id and chunk_id not in merged:
                merged[chunk_id] = item
        report("retrieving", f"Completed search {number} of {len(searches)}", number, len(searches))

    items = list(merged.values())
    items.sort(key=lambda item: (-float(item.get("combined_score", 0)), str(item.get("chunk_id", ""))))
    items = items[: max(limit * 2, 12)]
    if not items:
        return {"query": query, "summary": "No relevant vault passages were found.", "sources": [],
                "plan": plan, "searches": search_reports, "model": os.environ.get("ARIADNE_CHAT_MODEL", "gpt-oss:20b")}

    report("synthesizing", "Combining evidence and writing a cited answer…", len(searches), len(searches))
    evidence = []
    sources = []
    for number, item in enumerate(items, 1):
        evidence.append(f"[Source {number}] {item['citation_text']}\n{item['content']}")
        sources.append({"source_number": number, "chunk_id": item.get("chunk_id"), "title": item.get("title"),
                        "citation": item.get("citation"), "citation_text": item.get("citation_text")})
    answer_instructions = plan.get("answer_instructions")
    if isinstance(answer_instructions, list):
        instructions = "\n".join(f"- {str(item)}" for item in answer_instructions[:8])
    else:
        instructions = "- Answer clearly and cite significant claims."
    length_instruction = "Keep it concise and focused." if answer_mode == "summary" else "Give a useful, conversational explanation with enough context to make it understandable."
    answer_system = (
        "You are the KnowledgeVault librarian speaking to Warren, a technically experienced person who prefers plain, direct language. "
        "Answer only from the supplied vault evidence. Do not silently use general knowledge or planner assumptions. "
        "If evidence is incomplete or contradictory, say so. Cite claims inline using [Source N]. Use simple Markdown only. "
        "Sound like a thoughtful human librarian: vary sentence rhythm, explain terms naturally, and avoid boilerplate such as "
        "'This document provides an overview' or a dry academic report. Do not add fake humour or personality that is not supported by the evidence. "
        f"{length_instruction} "
        "For a normal answer, do not stop after one generic sentence when the evidence supports more detail; "
        "give a few short, well-connected paragraphs or sections."
    )
    answer_user = (f"Original question:\n{query}\n\nAnswer instructions:\n{instructions}\n\n"
                   "Retrieved vault evidence:\n\n" + "\n\n".join(evidence))
    summary = ollama_chat([{"role": "system", "content": answer_system}, {"role": "user", "content": answer_user}])
    return {"query": query, "summary": summary, "sources": sources, "plan": plan, "searches": search_reports,
            "model": os.environ.get("ARIADNE_CHAT_MODEL", "gpt-oss:20b")}


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
    chunk = chunks[index]
    citation = build_citation(record, path.relative_to(ROOT).as_posix(), chunk["heading"], chunk_id,
                              chunk["line_start"], chunk["line_end"])
    return {"chunk_id": chunk_id, "document_id": document_id, "title": record.get("page_title") or record.get("source_name"), "source_url": record.get("source_url"), "heading": chunk["heading"], "content": chunk["content"], "citation": citation, "citation_text": format_citation(citation)}


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
    {"name": "summarize_knowledge", "description": "Retrieve relevant KnowledgeVault passages, then summarize only that evidence with inline source references. Uses the configured local Ollama chat model and remains read-only.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": MAX_RESULT_LIMIT}}, "required": ["query"], "additionalProperties": False}},
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
            elif name == "summarize_knowledge":
                result = summarize_knowledge(arguments)
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


def write_job_status(path: Path, status: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def planned_job_main(job_path: str) -> None:
    path = Path(job_path).resolve()
    job = json.loads(path.read_text(encoding="utf-8"))
    status_path = Path(str(path) + ".status.json")
    query = str(job["query"]).strip()
    mode = str(job.get("mode", "answer"))
    session = str(job.get("session", ""))
    cache_key = hashlib.sha256(f"{session}\n{mode}\n{query.casefold()}".encode("utf-8")).hexdigest()
    cache_path = QUERY_CACHE_ROOT / f"{cache_key}.json"

    def progress(stage: str, message: str, completed: int = 0, total: int = 0) -> None:
        write_job_status(status_path, {"state": "running", "stage": stage, "message": message,
                                       "completed": completed, "total": total})

    try:
        if cache_path.is_file():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            write_job_status(status_path, {"state": "complete", "stage": "complete",
                                           "message": "Using the cached answer for this question.",
                                           "completed": 1, "total": 1, "result": cached})
            return
        progress("starting", "Starting local KnowledgeVault librarian…")
        result = planned_knowledge_query(query, int(job.get("limit", 6)), progress, mode)
        QUERY_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        temporary.replace(cache_path)
        write_job_status(status_path, {"state": "complete", "stage": "complete", "message": "Answer ready.",
                                       "completed": 1, "total": 1, "result": result})
    except Exception as exc:
        write_job_status(status_path, {"state": "error", "stage": "error", "message": str(exc),
                                       "completed": 0, "total": 0})


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--planned-job")
    args, _ = parser.parse_known_args()
    if args.planned_job:
        planned_job_main(args.planned_job)
        return
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
