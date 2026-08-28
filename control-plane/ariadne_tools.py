"""Small, local tool registry and temporary document-analysis workspace.

Attachments are deliberately kept outside the Knowledge Vault.  The workspace
contains derived, inspectable JSON under control-plane/runtime and is keyed by
the durable Home chat id, so a new chat cannot inherit another chat's files.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_DOCUMENT_BYTES = 6_000_000
MAX_DOCUMENTS_PER_CHAT = 8
MAX_DOCUMENT_CONTEXT_CHARS = 24_000
DIRECT_DOCUMENT_CHARS = 12_000
CHUNK_CHARS = 1_600
TOKEN_RE = re.compile(r"[\w-]+", re.UNICODE)
SUPPORTED_EXTENSIONS = {".md", ".txt"}
METADATA_KEYS = {
    "title", "source", "author", "published", "published_date", "created",
    "created_date", "description", "tags",
}


@dataclass(frozen=True)
class ToolDefinition:
    tool_id: str
    display_name: str
    description: str
    capabilities: tuple[str, ...]
    supported_input_types: tuple[str, ...]
    enabled: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "display_name": self.display_name,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "supported_input_types": list(self.supported_input_types),
            "enabled": self.enabled,
        }


class ToolRegistry:
    """In-process registry.  Later planner tools can register beside this one."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.tool_id in self._tools:
            raise ValueError(f"Tool already registered: {definition.tool_id}")
        self._tools[definition.tool_id] = definition

    def discover(self) -> list[dict[str, Any]]:
        return [tool.as_dict() for tool in self._tools.values()]

    def get(self, tool_id: str) -> ToolDefinition | None:
        return self._tools.get(tool_id)


TOOL_REGISTRY = ToolRegistry()
TOOL_REGISTRY.register(ToolDefinition(
    tool_id="document-analysis",
    display_name="Document Analysis",
    description="Ask questions about temporary Markdown or text attachments.",
    capabilities=("summarise", "extract claims", "answer questions", "retrieve chunks"),
    supported_input_types=("text/markdown", "text/plain", ".md", ".txt"),
))


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with open(descriptor, "w", encoding="utf-8", newline="\n", closefd=True) as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
        Path(temporary_name).replace(path)
    finally:
        temporary = Path(temporary_name)
        if temporary.exists():
            temporary.unlink()


def _workspace_path(root: Path, chat_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", chat_id):
        raise ValueError("Invalid durable chat_id.")
    return (root / f"{chat_id}.json").resolve()


def _load_workspace(root: Path, chat_id: str) -> dict[str, Any]:
    path = _workspace_path(root, chat_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"schema_version": 1, "chat_id": chat_id, "documents": []}
    if not isinstance(value, dict) or value.get("chat_id") != chat_id or not isinstance(value.get("documents"), list):
        return {"schema_version": 1, "chat_id": chat_id, "documents": []}
    return value


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        try:
            parsed = json.loads(value.replace("'", '"'))
            if isinstance(parsed, list):
                return parsed
        except (ValueError, json.JSONDecodeError):
            pass
        return [_parse_scalar(item) for item in inner.split(",") if item.strip()]
    if value.startswith("{") and value.endswith("}"):
        try:
            return json.loads(value.replace("'", '"'))
        except (ValueError, json.JSONDecodeError):
            pass
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_front_matter(content: str) -> tuple[dict[str, Any], str]:
    """Parse the useful, simple YAML front-matter forms used by Markdown notes."""
    if not content.startswith("---"):
        return {}, content
    match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", content, flags=re.DOTALL)
    if not match:
        return {}, content
    metadata: dict[str, Any] = {}
    current_list: str | None = None
    for line in match.group(1).splitlines():
        list_item = re.match(r"^\s*-\s+(.+?)\s*$", line)
        if list_item and current_list:
            if not isinstance(metadata.get(current_list), list):
                metadata[current_list] = []
            metadata[current_list].append(_parse_scalar(list_item.group(1)))
            continue
        field = re.match(r"^\s*([A-Za-z][\w-]*)\s*:\s*(.*?)\s*$", line)
        if not field:
            continue
        key, raw = field.group(1).casefold(), field.group(2)
        if key not in METADATA_KEYS:
            continue
        value = _parse_scalar(raw)
        metadata[key] = value
        current_list = key if not raw else None
    return metadata, content[match.end():]


def _chunk_markdown(content: str) -> list[dict[str, Any]]:
    """Reuse the Vault's heading-aware chunker without a second chunking model."""
    import sys

    root = Path(__file__).resolve().parent.parent / "00_System"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from ariadne_mcp import markdown_chunks  # local import avoids startup coupling

    return markdown_chunks(content, size=CHUNK_CHARS)


def _document_summary(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": document["document_id"],
        "filename": document["filename"],
        "title": document.get("metadata", {}).get("title") or document["filename"],
        "metadata": document.get("metadata", {}),
        "size_bytes": document["size_bytes"],
        "content_chars": document["content_chars"],
        "chunk_count": len(document.get("chunks", [])),
        "handling": document.get("handling", "chunked"),
        "content_hash": document["content_hash"],
    }


def list_documents(root: Path, chat_id: str) -> list[dict[str, Any]]:
    workspace = _load_workspace(root, chat_id)
    return [_document_summary(item) for item in workspace["documents"] if isinstance(item, dict)]


def attach_document(root: Path, chat_id: str, filename: str, content: str) -> dict[str, Any]:
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("A document filename is required.")
    safe_name = Path(filename.replace("\\", "/")).name.strip()
    if not safe_name or Path(safe_name).suffix.casefold() not in SUPPORTED_EXTENSIONS:
        raise ValueError("Only .md and .txt attachments are supported in Tools v1.")
    if not isinstance(content, str) or not content:
        raise ValueError("The attached document is empty.")
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"Keep each attached document below {MAX_DOCUMENT_BYTES // 1_000_000} MB.")
    workspace = _load_workspace(root, chat_id)
    if len(workspace["documents"]) >= MAX_DOCUMENTS_PER_CHAT:
        raise ValueError(f"A chat can have at most {MAX_DOCUMENTS_PER_CHAT} temporary documents.")
    metadata, body = parse_front_matter(content) if Path(safe_name).suffix.casefold() == ".md" else ({}, content)
    chunks = _chunk_markdown(body)
    document = {
        "document_id": uuid.uuid4().hex,
        "filename": safe_name,
        "metadata": metadata,
        "size_bytes": len(content_bytes),
        "content_chars": len(content),
        "content_hash": hashlib.sha256(content_bytes).hexdigest(),
        "handling": "direct" if len(body) <= DIRECT_DOCUMENT_CHARS else "chunked",
        "created_at": time.time(),
        "chunks": chunks,
    }
    workspace["documents"].append(document)
    _atomic_write(_workspace_path(root, chat_id), workspace)
    return _document_summary(document)


def remove_document(root: Path, chat_id: str, document_id: str) -> bool:
    workspace = _load_workspace(root, chat_id)
    before = len(workspace["documents"])
    workspace["documents"] = [item for item in workspace["documents"] if item.get("document_id") != document_id]
    if len(workspace["documents"]) == before:
        return False
    path = _workspace_path(root, chat_id)
    if workspace["documents"]:
        _atomic_write(path, workspace)
    elif path.exists():
        path.unlink()
    return True


def clear_documents(root: Path, chat_id: str) -> None:
    path = _workspace_path(root, chat_id)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _tokens(text: str) -> set[str]:
    return {item.casefold() for item in TOKEN_RE.findall(text)}


def _is_summary_request(query: str) -> bool:
    folded = query.casefold()
    return any(term in folded for term in ("summar", "overview", "important points", "key points", "main points", "outline"))


def retrieve_documents(root: Path, chat_id: str, query: str, context_tokens: int) -> dict[str, Any]:
    workspace = _load_workspace(root, chat_id)
    documents = [item for item in workspace["documents"] if isinstance(item, dict)]
    if not documents:
        return {"documents": [], "chunks": [], "context": "", "context_chars": 0, "retrieved_chunks": 0, "handling": "none"}
    budget = min(MAX_DOCUMENT_CONTEXT_CHARS, max(8_000, int(context_tokens * 1.5)))
    query_tokens = _tokens(query)
    selected: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    summary_request = _is_summary_request(query)
    for document in documents:
        chunks = document.get("chunks", [])
        if document.get("handling") == "direct":
            for index, chunk in enumerate(chunks):
                selected.append((1000 - index, document, {**chunk, "chunk_index": index}))
            continue
        ranked: list[tuple[float, int, dict[str, Any]]] = []
        for index, chunk in enumerate(chunks):
            text = f"{chunk.get('heading', '')}\n{chunk.get('content', '')}"
            words = _tokens(text)
            score = float(len(query_tokens & words))
            if query.strip() and query.casefold() in text.casefold():
                score += 12
            if summary_request and index in {0, len(chunks) - 1}:
                score += 2
            ranked.append((score, index, chunk))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        selected.extend((score, document, {**chunk, "chunk_index": index}) for score, index, chunk in ranked[:12])
    selected.sort(key=lambda item: (-item[0], item[1].get("filename", ""), item[2].get("chunk_index", 0)))
    chosen: list[dict[str, Any]] = []
    used_chars = 0
    seen: set[tuple[str, int]] = set()
    for _, document, chunk in selected:
        key = (str(document["document_id"]), int(chunk.get("chunk_index", 0)))
        if key in seen:
            continue
        rendered = str(chunk.get("content") or "")
        if used_chars and used_chars + len(rendered) > budget:
            continue
        seen.add(key)
        chunk_id = f"attachment:{document['document_id']}#chunk-{key[1]}"
        citation = {
            "source_type": "attachment",
            "document_id": document["document_id"],
            "filename": document["filename"],
            "title": document.get("metadata", {}).get("title") or document["filename"],
            "heading": chunk.get("heading") or "Document",
            "line_start": chunk.get("line_start"),
            "line_end": chunk.get("line_end"),
        }
        chosen.append({
            "source_type": "attachment",
            "chunk_id": chunk_id,
            "document_id": document["document_id"],
            "title": citation["title"],
            "filename": document["filename"],
            "heading": chunk.get("heading") or "Document",
            "content": rendered,
            "citation": citation,
            "citation_text": f"Attachment: {citation['title']} — {citation['heading']}, lines {citation['line_start']}–{citation['line_end']}",
        })
        used_chars += len(rendered)
    context_parts = []
    for document in documents:
        metadata = document.get("metadata") or {}
        metadata_lines = [f"{key}: {value}" for key, value in metadata.items() if value not in (None, "", [])]
        context_parts.append(
            f"[Temporary attachment: {document['filename']}]\n"
            + ("Metadata (front matter):\n" + "\n".join(metadata_lines) + "\n" if metadata_lines else "")
        )
    context_parts.extend(f"[Attachment passage: {item['citation_text']}]\n{item['content']}" for item in chosen)
    return {
        "documents": [_document_summary(item) for item in documents],
        "chunks": chosen,
        "context": "\n\n".join(context_parts)[:budget],
        "context_chars": min(used_chars, budget),
        "retrieved_chunks": len(chosen),
        "handling": "direct" if all(item.get("handling") == "direct" for item in documents) else "chunked",
        "context_budget_chars": budget,
    }
