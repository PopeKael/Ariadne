"""Daily Inbox ingestion using the rebuild-v1 review-only architecture."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from ariadne_embeddings import build_index
from ariadne_mcp import chunk_records
from run_rebuild import atomic_json, main as rebuild_main, materialise, now, run_record
from run_rebuild_pilot import load_domains
from vault_rebuild import SOURCE_FOLDERS, build_manifest, manifest_bytes, validate_records, write_manifest


def inbox_manifest(root: Path) -> dict:
    records = []
    inbox = root / "Inbox"
    for path in sorted(inbox.glob("*.md"), key=lambda item: item.name.lower()):
        if path.name.lower() == "readme.md":
            continue
        from vault_rebuild import source_record
        records.append(source_record(root, "Inbox", path))
    records.sort(key=lambda item: (str(item.get("stable_source_id")), item["relative_path"].lower()))
    return {"schema_version": 1, "source_folders": ["Inbox"], "records": records,
            "validation": validate_records(records)}


def manifest_hash(manifest: dict) -> str:
    return hashlib.sha256(manifest_bytes(manifest)).hexdigest()


def choose_output(root: Path, digest: str) -> Path:
    current = root / "00_System/Data/rebuild-v1/daily-current"
    state_path = current / "state.json"
    if not state_path.exists():
        return current
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("manifest_sha256") == digest:
        return current
    return root / "00_System/Data/rebuild-v1/daily" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily rebuild-v1 Inbox ingestion and active retrieval update.")
    parser.add_argument("--vault", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    root = args.vault.resolve()
    manifest = inbox_manifest(root)
    if manifest["validation"]["unreadable_count"] or manifest["validation"]["duplicate_stable_ids"] or manifest["validation"]["duplicate_content_hashes"]:
        raise RuntimeError("Inbox manifest validation failed; no ingestion started.")
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
