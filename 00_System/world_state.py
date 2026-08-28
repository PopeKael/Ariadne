"""Ariadne World State v1: derived SELF + NOW context.

World State is a compact routing projection, not a second memory store. The
Markdown Vault, identity files, catalogue, and runtime clock remain the
authoritative sources. The derived snapshot is refreshable and may guide what
to search, but it is never sufficient evidence for a factual answer by itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_VAULT_ROOT = Path(r"D:\Downloads\KnowledgeVault")
ROOT = Path(os.environ.get("ARIADNE_VAULT_ROOT", str(DEFAULT_VAULT_ROOT))).expanduser().resolve()
SYSTEM_ROOT = ROOT / "00_System"
WORLD_STATE_VERSION = "1.0.0"
WORLD_STATE_PATH = SYSTEM_ROOT / "Data" / "WorldState" / "world-state-v1.json"
IDENTITY_KERNEL_PATH = ROOT / "Ariadne Identity Kernel v1.1.0.md"
ALIASES_PATH = SYSTEM_ROOT / "PersonAliases.json"
IDENTITY_INDEX_PATH = SYSTEM_ROOT / "PersonIdentityIndex.json"
LIBRARY_PATH = SYSTEM_ROOT / "library.json"
SOURCE_DIRS = (ROOT / "People", ROOT / "Entities", ROOT / "Projects")
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_@&'-]*")
STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "what", "where", "when", "have",
    "has", "does", "about", "into", "your", "our", "my", "how", "why", "are", "was", "were",
    "been", "being", "will", "would", "could", "should", "their", "there", "then", "than",
}
CHANNEL_TERMS = {"channel", "channels", "youtube", "video", "videos", "content", "vlog", "vlogs"}
PROJECT_TERMS = {"project", "projects", "ariadne", "librarian", "vault", "mcp", "architecture", "workflow"}

_CACHE: tuple[str, dict[str, Any]] | None = None


def _tokens(value: object) -> set[str]:
    result: set[str] = set()
    for token in TOKEN_RE.findall(str(value or "").casefold()):
        token = token.strip("'_")
        if len(token) > 2 and token not in STOPWORDS:
            result.add(token)
    return result


def _self_now_focus(terms: set[str]) -> bool:
    """Recognise a compact SELF + NOW orientation request."""
    return (
        {"who", "working", "now"}.issubset(terms)
        or "matters" in terms and bool({"now", "working"}.intersection(terms))
        or "priorities" in terms and bool({"now", "working"}.intersection(terms))
    )


def _label(path: Path) -> str:
    return re.sub(r"\s+", " ", path.stem.replace("_", " ")).strip()


def _safe_json(path: Path, fallback: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return fallback


def _owner_from_kernel() -> str:
    try:
        for line in IDENTITY_KERNEL_PATH.read_text(encoding="utf-8-sig").splitlines()[:20]:
            if line.casefold().startswith("owner:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "Warren Gerdes"


def _source_signature() -> str:
    parts: list[str] = []
    fixed = (IDENTITY_KERNEL_PATH, ALIASES_PATH, IDENTITY_INDEX_PATH, LIBRARY_PATH)
    paths: list[Path] = [path for path in fixed if path.exists()]
    for directory in SOURCE_DIRS:
        if directory.exists():
            paths.extend(path for path in directory.rglob("*") if path.is_file())
    for path in sorted(paths):
        try:
            stat = path.stat()
            parts.append(f"{path.relative_to(ROOT).as_posix()}:{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            continue
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:24]


def _identity_projection() -> dict[str, Any]:
    aliases_payload = _safe_json(ALIASES_PATH, {})
    aliases: dict[str, list[str]] = {}
    if isinstance(aliases_payload, dict):
        for alias, canonical in aliases_payload.items():
            key = str(canonical).strip()
            if key:
                aliases.setdefault(key, []).append(str(alias).strip())

    index_payload = _safe_json(IDENTITY_INDEX_PATH, [])
    handles: list[dict[str, Any]] = []
    if isinstance(index_payload, list):
        ranked = [item for item in index_payload if isinstance(item, dict)]
        ranked.sort(key=lambda item: (-int(item.get("interaction_count") or 0), str(item.get("canonical_name") or "").casefold()))
        for item in ranked[:16]:
            canonical = str(item.get("canonical_name") or "").strip()
            if not canonical:
                continue
            values = sorted({canonical, *[str(value).strip() for value in item.get("aliases", []) if str(value).strip()]})
            handles.append({"canonical": canonical, "aliases": values, "interaction_count": int(item.get("interaction_count") or 0)})

    return {"owner": _owner_from_kernel(), "handles": handles, "aliases": aliases}


def _file_labels(directory: Path, limit: int = 24) -> list[str]:
    rows: list[tuple[int, str]] = []
    if not directory.exists():
        return []
    for path in directory.glob("*.md"):
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        links = text.count("[[")
        rows.append((links, _label(path)))
    rows.sort(key=lambda item: (-item[0], item[1].casefold()))
    result: list[str] = []
    seen: set[str] = set()
    for _, value in rows:
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
        if len(result) >= limit:
            break
    return result


def _catalogue_subjects() -> dict[str, list[dict[str, str]]]:
    payload = _safe_json(LIBRARY_PATH, [])
    records = payload if isinstance(payload, list) else []
    channels: list[tuple[float, dict[str, str]]] = []
    projects: list[tuple[float, dict[str, str]]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        title = str(record.get("page_title") or record.get("source_name") or "").strip()
        summary = str(record.get("summary") or "").strip()
        searchable = " ".join([
            title,
            summary,
            str(record.get("primary_topic") or ""),
            " ".join(str(item) for item in record.get("entities", []) if isinstance(item, str)),
        ])
        lowered = searchable.casefold()
        terms = _tokens(searchable)
        if terms.intersection(CHANNEL_TERMS):
            score = float(len(terms.intersection(CHANNEL_TERMS)))
            if "main youtube channel" in lowered or "chanya & wazza" in lowered or "c&w channel" in lowered:
                score += 8
            channels.append((score, {"title": title, "summary": summary[:180]}))
        if terms.intersection(PROJECT_TERMS):
            score = float(len(terms.intersection(PROJECT_TERMS)))
            if "ariadne" in lowered or "knowledge vault" in lowered:
                score += 5
            projects.append((score, {"title": title, "summary": summary[:180]}))

    def compact(rows: list[tuple[float, dict[str, str]]]) -> list[dict[str, str]]:
        rows.sort(key=lambda item: (-item[0], item[1]["title"].casefold()))
        result: list[dict[str, str]] = []
        seen: set[str] = set()
        for _, item in rows:
            key = item["title"].casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(item)
            if len(result) >= 10:
                break
        return result

    return {"channels": compact(channels), "projects": compact(projects)}


def _base_state(signature: str, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now().astimezone()
    identity = _identity_projection()
    subjects = _catalogue_subjects()
    state = {
        "world_state_version": WORLD_STATE_VERSION,
        "derived": True,
        "source_signature": signature,
        "generated_at": current.isoformat(timespec="seconds"),
        "self": {
            "owner": identity["owner"],
            "known_handles": identity["handles"][:10],
            "known_aliases": identity["aliases"],
            "people_labels": _file_labels(ROOT / "People", 10),
            "entity_labels": _file_labels(ROOT / "Entities", 16),
            "channels": subjects["channels"],
            "projects": subjects["projects"],
        },
        "now": {
            "local_date": current.date().isoformat(),
            "local_time": current.strftime("%H:%M:%S"),
            "timezone": str(current.tzinfo),
            "vault_root": str(ROOT),
            "library_records": len(_safe_json(LIBRARY_PATH, [])) if isinstance(_safe_json(LIBRARY_PATH, []), list) else 0,
            "processed_markdown": len(list((ROOT / "Processed").glob("*.md"))),
        },
        "request_context": {},
    }
    return state


def _persist(state: dict[str, Any]) -> None:
    try:
        WORLD_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_path = tempfile.mkstemp(prefix="world-state-", suffix=".tmp", dir=str(WORLD_STATE_PATH.parent))
        temporary = Path(raw_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, WORLD_STATE_PATH)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)
    except OSError:
        return


def world_state_for_request(query: str = "", history: object = None, *, persist: bool = True) -> dict[str, Any]:
    """Return a compact base snapshot plus a non-durable request overlay."""
    global _CACHE
    signature = _source_signature()
    if _CACHE is None or _CACHE[0] != signature:
        base = _base_state(signature)
        _CACHE = (signature, base)
        if persist:
            _persist(base)
    else:
        base = _CACHE[1]

    focus_parts = [str(query or "").strip()]
    if isinstance(history, list):
        focus_parts.extend(
            str(item.get("content") or "")[:400]
            for item in history[-6:]
            if isinstance(item, dict) and item.get("role") == "user"
        )
    focus = " ".join(part for part in focus_parts if part)
    focus_terms = _tokens(focus)
    channel_focus = bool(focus_terms.intersection(CHANNEL_TERMS))
    project_focus = bool(focus_terms.intersection(PROJECT_TERMS))
    self_now_focus = _self_now_focus(focus_terms)

    self_projection = base["self"]
    if self_now_focus:
        candidates = [
            *self_projection.get("channels", [])[:3],
            *self_projection.get("projects", [])[:3],
        ]
    elif channel_focus:
        candidates = self_projection["channels"]
    elif project_focus:
        candidates = self_projection["projects"]
    else:
        candidates = []
    matched: list[str] = []
    for item in candidates:
        searchable = f"{item.get('title', '')} {item.get('summary', '')}"
        if focus_terms.intersection(_tokens(searchable)):
            matched.append(str(item.get("title") or ""))
        if len(matched) >= 6:
            break
    if self_now_focus and not matched:
        matched = [str(item.get("title") or "") for item in candidates[:6]]
    elif channel_focus and not matched:
        matched = [str(item.get("title") or "") for item in candidates[:6]]

    overlay = {
        "query": str(query or "")[:800],
        "conversation_user_messages": [
            str(item.get("content") or "")[:400]
            for item in history[-3:]
            if isinstance(history, list) and isinstance(item, dict) and item.get("role") == "user"
        ] if isinstance(history, list) else [],
        "focus_terms": sorted(focus_terms)[:32],
        "matched_subjects": matched,
        "retrieval_guidance": {
            "search_known_subjects": matched,
            "prefer_self_and_now": bool(
                focus_terms.intersection(CHANNEL_TERMS | PROJECT_TERMS) or self_now_focus
            ),
        },
    }
    result = dict(base)
    result["request_context"] = overlay
    return result


def refresh_world_state() -> dict[str, Any]:
    """Force a source-signature check and refresh the persisted base snapshot."""
    return world_state_for_request(persist=True)


def world_state_planner_view(state: dict[str, Any]) -> dict[str, Any]:
    """Return the smaller view safe to place in the planner's prompt budget."""
    if not isinstance(state, dict):
        return {"world_state_version": WORLD_STATE_VERSION, "derived": True, "self": {}, "now": {}, "request_context": {}}
    self_projection = state.get("self") if isinstance(state.get("self"), dict) else {}
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    matched = {str(value).casefold() for value in request_context.get("matched_subjects", []) if str(value).strip()}

    def selected_subjects(key: str) -> list[dict[str, str]]:
        values = self_projection.get(key) if isinstance(self_projection.get(key), list) else []
        selected = [item for item in values if isinstance(item, dict) and str(item.get("title") or "").casefold() in matched]
        if not selected:
            selected = [item for item in values if isinstance(item, dict)][:3]
        return [{"title": str(item.get("title") or ""), "summary": str(item.get("summary") or "")[:140]} for item in selected[:6]]

    return {
        "world_state_version": state.get("world_state_version", WORLD_STATE_VERSION),
        "derived": True,
        "self": {
            "owner": self_projection.get("owner"),
            "known_handles": self_projection.get("known_handles", [])[:6],
            "people_labels": self_projection.get("people_labels", [])[:6],
            "entity_labels": self_projection.get("entity_labels", [])[:8],
            "channels": selected_subjects("channels"),
            "projects": selected_subjects("projects"),
        },
        "now": {
            key: state.get("now", {}).get(key)
            for key in ("local_date", "local_time", "timezone")
            if isinstance(state.get("now"), dict) and key in state.get("now", {})
        },
        "request_context": {
            "query": request_context.get("query", ""),
            "conversation_user_messages": request_context.get("conversation_user_messages", [])[:3],
            "focus_terms": request_context.get("focus_terms", [])[:20],
            "matched_subjects": request_context.get("matched_subjects", [])[:6],
            "retrieval_guidance": request_context.get("retrieval_guidance", {}),
        },
    }


__all__ = [
    "WORLD_STATE_PATH", "WORLD_STATE_VERSION", "refresh_world_state",
    "world_state_for_request", "world_state_planner_view",
]
