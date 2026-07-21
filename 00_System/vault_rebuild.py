"""Deterministic, read-only source manifest support for the rebuild-v1 pilot."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

SOURCE_FOLDERS = ("Inbox", "Processed", "Failed")
SCHEMA_VERSION = 1


def canonical_content(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.rstrip()


def content_hash(text: str) -> str:
    return hashlib.sha256(canonical_content(text).encode("utf-8")).hexdigest()


def read_source(path: Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8-sig"), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, str(exc)


def front_matter(text: str) -> tuple[dict[str, str], list[str]]:
    warnings: list[str] = []
    if not text.startswith("---"):
        return {}, ["missing_front_matter"]
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, ["malformed_front_matter_opening"]
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return {}, ["unterminated_front_matter"]
    values: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not match:
            warnings.append("unparsed_front_matter_line")
            continue
        key, value = match.groups()
        values[key.lower()] = value.strip().strip('"').strip("'")
    return values, warnings


def canonical_url(value: str) -> str | None:
    if not value or not re.match(r"^https?://", value, re.I):
        return None
    try:
        parsed = urlsplit(value.strip())
        if not parsed.hostname:
            return None
        host = parsed.hostname.lower()
        if parsed.port:
            host = f"{host}:{parsed.port}"
        path = parsed.path or "/"
        if path != "/":
            path = path.rstrip("/")
        return urlunsplit((parsed.scheme.lower(), host, path, parsed.query, ""))
    except ValueError:
        return None


def youtube_id(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    candidate = ""
    if host in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/").split("/")[0]
    elif "youtube.com" in host:
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
        else:
            match = re.match(r"^/(?:shorts|embed|live)/([^/]+)", parsed.path)
            candidate = match.group(1) if match else ""
    return candidate if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate) else None


def source_type(url: str | None, text: str) -> str:
    value = (url or "").lower()
    if "youtube.com" in value or "youtu.be" in value:
        return "youtube"
    if "chatgpt.com" in value or "chat.openai.com" in value:
        return "chatgpt"
    if "gemini.google.com" in value:
        return "gemini"
    if "openwebui" in value:
        return "openwebui"
    if "facebook.com" in value:
        return "facebook"
    if value:
        return "web"
    return "markdown"


def source_record(root: Path, workflow: str, path: Path) -> dict[str, Any]:
    text, read_error = read_source(path)
    relative = path.relative_to(root).as_posix()
    stat = path.stat() if path.exists() else None
    record: dict[str, Any] = {
        "relative_path": relative,
        "workflow_state": workflow.lower(),
        "file_size_bytes": stat.st_size if stat else None,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat() if stat else None,
        "parse_warnings": [],
    }
    if read_error or text is None:
        record.update({"stable_source_id": None, "canonical_content_sha256": None, "source_type": "unknown", "title": None,
                       "canonical_url": None, "external_identity": None, "read_error": read_error})
        record["parse_warnings"] = ["unreadable_source"]
        return record
    metadata, warnings = front_matter(text)
    raw_url = next((metadata.get(key, "") for key in ("source_url", "canonical_url", "source", "url", "link") if metadata.get(key, "")), "")
    url = canonical_url(raw_url)
    if raw_url and not url:
        warnings.append("invalid_source_url")
    digest = content_hash(text)
    video = youtube_id(url)
    stable = f"youtube:{video}" if video else f"url:{url}" if url else f"sha256:{digest}"
    title = metadata.get("title", "").strip()
    if not title:
        heading = re.search(r"(?m)^#\s+(.+?)\s*$", text)
        title = heading.group(1).strip() if heading else path.stem
        warnings.append("title_inferred" if heading else "title_from_filename")
    record.update({
        "stable_source_id": stable,
        "canonical_content_sha256": digest,
        "source_type": source_type(url, text),
        "title": title,
        "canonical_url": url,
        "external_identity": {"kind": "youtube", "value": video} if video else {"kind": "url", "value": url} if url else None,
        "read_error": None,
        "parse_warnings": sorted(set(warnings)),
    })
    return record


def validate_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    readable = [record for record in records if not record.get("read_error")]
    def duplicate_groups(field: str) -> list[dict[str, Any]]:
        groups: dict[str, list[str]] = {}
        for record in readable:
            value = record.get(field)
            if value:
                groups.setdefault(str(value), []).append(str(record["relative_path"]))
        return [{"value": value, "paths": sorted(paths)} for value, paths in sorted(groups.items()) if len(paths) > 1]
    return {
        "readable_count": len(readable),
        "unreadable_count": len(records) - len(readable),
        "duplicate_stable_ids": duplicate_groups("stable_source_id"),
        "duplicate_content_hashes": duplicate_groups("canonical_content_sha256"),
    }


def build_manifest(root: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for folder in SOURCE_FOLDERS:
        directory = root / folder
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md"), key=lambda item: item.name.lower()):
            if path.name.lower() == "readme.md":
                continue
            records.append(source_record(root, folder, path))
    records.sort(key=lambda item: (str(item.get("stable_source_id")), item["relative_path"].lower()))
    validation = validate_records(records)
    return {"schema_version": SCHEMA_VERSION, "source_folders": list(SOURCE_FOLDERS), "records": records, "validation": validation}


def manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def write_manifest(manifest: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(manifest_bytes(manifest))
