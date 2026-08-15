"""Daily Inbox ingestion using the rebuild-v1 review-only architecture."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ariadne_embeddings import build_index
from ariadne_mcp import chunk_records
from rebuild_lock import ingestion_lock
from run_rebuild import atomic_json, main as rebuild_main, materialise, now, run_record
from run_rebuild_pilot import load_domains
from vault_rebuild import SOURCE_FOLDERS, build_manifest, content_hash, manifest_bytes, validate_records, write_manifest


def _record_sort_key(record: dict[str, Any]) -> tuple[str, str]:
    """Return a deterministic newest-first comparison key for Inbox snapshots."""
    return (str(record.get("modified_at") or ""), str(record.get("relative_path") or "").casefold())


def _record_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "relative_path": record.get("relative_path"),
        "stable_source_id": record.get("stable_source_id"),
        "canonical_content_sha256": record.get("canonical_content_sha256"),
        "modified_at": record.get("modified_at"),
    }


def resolve_inbox_duplicates(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resolve recoverable Inbox conflicts while preserving an audit trail.

    A conversation export can be a newer snapshot of the same stable ChatGPT
    conversation identity.  The latest readable snapshot wins.  The same rule
    is used for duplicate canonical content, which is normally a repeated
    export under a different filename or URL.  Unreadable records are left in
    the input so the caller can still apply the hard read-integrity check.
    """
    remaining = {str(record["relative_path"]): record for record in records}
    decisions: list[dict[str, Any]] = []

    for field, conflict_type in (
        ("stable_source_id", "duplicate_stable_id"),
        ("canonical_content_sha256", "duplicate_content_hash"),
    ):
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in remaining.values():
            if record.get("read_error"):
                continue
            value = record.get(field)
            if value:
                groups[str(value)].append(record)
        for value, group in sorted(groups.items()):
            if len(group) < 2:
                continue
            winner = max(group, key=_record_sort_key)
            losers = sorted((record for record in group if record is not winner),
                            key=lambda record: str(record["relative_path"]).casefold())
            for loser in losers:
                remaining.pop(str(loser["relative_path"]), None)
            decisions.append({
                "conflict_type": conflict_type,
                "identity_field": field,
                "identity_value": value,
                "policy": "newest_snapshot_wins",
                "selected": _record_summary(winner),
                "superseded": [_record_summary(record) for record in losers],
            })

    selected = sorted(remaining.values(), key=lambda record: (
        str(record.get("stable_source_id")), str(record.get("relative_path") or "").casefold()))
    return selected, {
        "policy": "newest_snapshot_wins",
        "resolved_conflict_count": len(decisions),
        "superseded_file_count": sum(len(item["superseded"]) for item in decisions),
        "decisions": decisions,
    }


def _archive_destination(root: Path, record: dict[str, Any]) -> Path:
    source = root / str(record["relative_path"])
    archive = root / "Archive" / "Duplicates"
    candidate = archive / source.name
    if not candidate.exists():
        return candidate
    if content_hash(candidate.read_text(encoding="utf-8-sig")) == record.get("canonical_content_sha256"):
        return candidate
    suffix = str(record.get("canonical_content_sha256") or "unknown")[:12]
    candidate = archive / f"{source.stem}__superseded_{suffix}{source.suffix}"
    if not candidate.exists():
        return candidate
    if content_hash(candidate.read_text(encoding="utf-8-sig")) == record.get("canonical_content_sha256"):
        return candidate
    counter = 2
    while True:
        candidate = archive / f"{source.stem}__superseded_{suffix}_{counter}{source.suffix}"
        if not candidate.exists():
            return candidate
        if content_hash(candidate.read_text(encoding="utf-8-sig")) == record.get("canonical_content_sha256"):
            return candidate
        counter += 1


def archive_superseded_records(root: Path, deduplication: dict[str, Any]) -> list[dict[str, Any]]:
    """Move superseded Inbox snapshots to the reversible duplicate archive."""
    superseded: dict[str, dict[str, Any]] = {}
    for decision in deduplication.get("decisions", []):
        for record in decision.get("superseded", []):
            item = dict(record)
            item["superseded_by"] = decision["selected"]["relative_path"]
            item["conflict_type"] = decision["conflict_type"]
            superseded[item["relative_path"]] = item

    moved: list[tuple[Path, Path]] = []
    report: list[dict[str, Any]] = []
    try:
        for relative_path in sorted(superseded, key=str.casefold):
            record = superseded[relative_path]
            source = root / relative_path
            if not source.is_file():
                report.append({**record, "action": "source_missing"})
                continue
            destination = _archive_destination(root, record)
            if destination.exists():
                report.append({**record, "action": "already_archived",
                               "archived_path": destination.relative_to(root).as_posix()})
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
            moved.append((source, destination))
            report.append({**record, "action": "moved",
                           "archived_path": destination.relative_to(root).as_posix()})
    except Exception:
        for source, destination in reversed(moved):
            if destination.exists() and not source.exists():
                destination.rename(source)
        raise
    return report


def inbox_manifest(root: Path) -> dict:
    records = []
    inbox = root / "Inbox"
    for path in sorted(inbox.glob("*.md"), key=lambda item: item.name.lower()):
        if path.name.lower() == "readme.md":
            continue
        from vault_rebuild import source_record
        records.append(source_record(root, "Inbox", path))
    records, deduplication = resolve_inbox_duplicates(records)
    return {"schema_version": 1, "source_folders": ["Inbox"], "records": records,
            "deduplication": deduplication, "validation": validate_records(records)}


def manifest_hash(manifest: dict) -> str:
    return hashlib.sha256(manifest_bytes(manifest)).hexdigest()


def choose_output(root: Path, digest: str) -> Path:
    current = root / "00_System/Data/rebuild-v1/daily-current"
    candidates = [current]
    daily_root = root / "00_System/Data/rebuild-v1/daily"
    if daily_root.is_dir():
        candidates.extend(sorted((p for p in daily_root.iterdir() if p.is_dir()), reverse=True))
    for candidate in candidates:
        state_path = candidate / "state.json"
        if not state_path.is_file():
            continue
        try:
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if state.get("manifest_sha256") == digest:
            return candidate
    if not (current / "state.json").exists():
        return current
    return daily_root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily rebuild-v1 Inbox ingestion and active retrieval update.")
    parser.add_argument("--vault", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    root = args.vault.resolve()
    with ingestion_lock(root):
        manifest = inbox_manifest(root)
        deduplication = manifest["deduplication"]
        if deduplication["resolved_conflict_count"]:
            print(f"Inbox duplicate policy: newest snapshot wins; resolved {deduplication['resolved_conflict_count']} conflict(s), "
                  f"archiving {deduplication['superseded_file_count']} older snapshot(s).", flush=True)
        if manifest["validation"]["unreadable_count"]:
            unreadable = [record for record in manifest["records"] if record.get("read_error")]
            for record in unreadable:
                print(f"Unreadable Inbox source: {record['relative_path']} — {record['read_error']}", flush=True)
            raise RuntimeError(f"Inbox manifest validation failed for {len(unreadable)} unreadable source(s); no ingestion started.")
        if not manifest["records"]:
            print("Inbox is empty. Nothing to ingest.")
            return 0
        output = choose_output(root, manifest_hash(manifest))
        output.mkdir(parents=True, exist_ok=True)
        write_manifest(manifest, output / "source-manifest.json")
        state_path = output / "state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("manifest_sha256") != manifest_hash(manifest):
                raise RuntimeError("Daily checkpoint does not match current Inbox; a new run directory should have been selected.")
        else:
            state = {"version": 1, "scope": "daily-inbox", "manifest_sha256": manifest_hash(manifest),
                     "source_ids": [r["stable_source_id"] for r in manifest["records"]], "created_at": now(), "completed": {}}
        deduplication_report = archive_superseded_records(root, deduplication)
        atomic_json(output / "deduplication-report.json", {
            **deduplication,
            "actions": deduplication_report,
        })
        domains = load_domains(root)
        pending = [r for r in manifest["records"] if r["stable_source_id"] not in state["completed"]]
        print(f"Daily rebuild-v1 scope: {len(manifest['records'])}; completed: {len(state['completed'])}; pending: {len(pending)}")
        for index, record in enumerate(pending, start=1):
            print(f"[{index}/{len(pending)}] {record['relative_path']}", flush=True)
            outcome, capture = run_record(root, record, domains)
            state["completed"][record["stable_source_id"]] = {"record": record, "outcome": outcome, "capture": capture}
            state["updated_at"] = now()
            materialise(output, state)
            print(f"  {outcome['status']}: {outcome['reason'] or 'schema and semantic validation passed'}", flush=True)
        if len(state["completed"]) != len(manifest["records"]):
            raise RuntimeError("Daily run did not reach a terminal checkpoint.")
        command = [sys.executable, str(root / "00_System/integrate_rebuild.py"), "--vault", str(root), "--run-dir", str(output), "--merge-existing"]
        subprocess.run(command, cwd=root, check=True)
        index_report = build_index(root, list(chunk_records()), rebuild=False)
        atomic_json(output / "embedding-update.json", index_report)
        print(json.dumps(index_report, ensure_ascii=False, indent=2))
        print("Daily rebuild-v1 ingestion complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
