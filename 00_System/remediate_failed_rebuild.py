"""Safely retry the unambiguous rebuild-v1 failed-ingestion cases.

Only audit classifications explicitly selected on the command line are touched.
Original Markdown is backed up byte-for-byte before an accepted retry is moved
to Processed; rejected retry outcomes remain in Failed.  The one confirmed
obsolete item may be archived into the excluded Archive namespace.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ariadne_embeddings import build_index
from ariadne_mcp import chunk_records
from rebuild_lock import ingestion_lock
from run_rebuild import atomic_json, materialise, now, run_record
from run_rebuild_pilot import load_domains
from vault_rebuild import manifest_bytes, validate_records, write_manifest


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_backup(root: Path, rows: list[dict[str, Any]], backup: Path) -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    for row in rows:
        source = root / row["vault_relative_path"]
        destination = backup / source.name
        expected_hash = row["raw_sha256"]
        if not source.is_file():
            # An earlier interrupted invocation can have already archived an
            # obsolete source after this same byte-for-byte backup completed.
            if destination.exists() and sha256(destination) == expected_hash:
                report.append({"stable_source_id": row["stable_source_id"], "source": row["vault_relative_path"],
                               "backup": destination.relative_to(root).as_posix(), "raw_sha256": expected_hash,
                               "action": "already_backed_up_source_already_archived"})
                continue
            raise RuntimeError(f"Failed source disappeared before backup: {source}")
        source_hash = sha256(source)
        if source_hash != expected_hash:
            raise RuntimeError(f"Failed source changed since audit: {source}")
        if destination.exists() and sha256(destination) != source_hash:
            destination = backup / f"{source.stem}__{source_hash[:12]}{source.suffix}"
        if destination.exists():
            if sha256(destination) != source_hash:
                raise RuntimeError(f"Backup collision: {destination}")
            action = "already_backed_up"
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if sha256(destination) != source_hash:
                raise RuntimeError(f"Backup verification failed: {destination}")
            action = "copied"
        report.append({"stable_source_id": row["stable_source_id"], "source": source.relative_to(root).as_posix(),
                       "backup": destination.relative_to(root).as_posix(), "raw_sha256": source_hash, "action": action})
    return report


def archive_obsolete(root: Path, rows: list[dict[str, Any]], archive: Path) -> list[dict[str, Any]]:
    moves: list[dict[str, Any]] = []
    for row in rows:
        source = root / row["vault_relative_path"]
        if not source.exists():
            continue
        original_hash = sha256(source)
        destination = archive / source.name
        if destination.exists() and sha256(destination) != original_hash:
            destination = archive / f"{source.stem}__{original_hash[:12]}{source.suffix}"
        if destination.exists():
            if sha256(destination) != original_hash:
                raise RuntimeError(f"Archive collision: {destination}")
            source.unlink()
            action = "duplicate_source_removed_after_verified_archive"
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
            action = "moved"
        moves.append({"stable_source_id": row["stable_source_id"], "old_path": row["vault_relative_path"],
                      "archived_path": destination.relative_to(root).as_posix(), "raw_sha256": original_hash, "action": action})
    return moves


def main() -> int:
    parser = argparse.ArgumentParser(description="Retry only audited, unambiguous rebuild-v1 failures.")
    parser.add_argument("--vault", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--audit", type=Path, default=Path("Reports/Failed-Ingestion-Audit-20260722.json"))
    parser.add_argument("--classes", nargs="+", default=["retry-safe", "pipeline-defect"])
    parser.add_argument("--archive-obsolete", action="store_true")
    args = parser.parse_args()
    root = args.vault.resolve()
    audit_path = args.audit if args.audit.is_absolute() else root / args.audit
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    selected = [row for row in audit["items"] if row["classification"] in set(args.classes)]
    obsolete = [row for row in audit["items"] if row["classification"] == "obsolete"] if args.archive_obsolete else []
    if not selected and not obsolete:
        print("No audited items match the requested remediation classes.")
        return 0
    selection_key = hashlib.sha256(json.dumps([(row["stable_source_id"], row["raw_sha256"]) for row in selected + obsolete], sort_keys=True).encode("utf-8")).hexdigest()[:16]
    output = root / "00_System/Data/rebuild-v1/failed-remediation" / selection_key
    receipt = output / "remediation-receipt.json"
    backup = root / "Archive/FailedRemediation" / selection_key / "originals"
    obsolete_archive = root / "Archive/FailedRemediation" / selection_key / "obsolete"
    with ingestion_lock(root):
        if receipt.exists():
            print(f"Remediation {selection_key} is already complete; no model calls, file moves, or catalogue changes performed.")
            return 0
        backup_rows = copy_backup(root, selected + obsolete, backup)
        if obsolete:
            archive_rows = archive_obsolete(root, obsolete, obsolete_archive)
        else:
            archive_rows = []
        if not selected:
            atomic_json(output / "remediation-receipt.json", {"selection_key": selection_key, "backup": backup_rows, "archived": archive_rows, "retried": []})
            return 0
        original_manifest = json.loads((root / "00_System/Data/rebuild-v1/bulk/source-manifest.json").read_text(encoding="utf-8"))
        originals = {record["stable_source_id"]: record for record in original_manifest["records"]}
        records = [{**originals[row["stable_source_id"]], "relative_path": row["vault_relative_path"], "workflow_state": "failed"} for row in selected]
        manifest = {"schema_version": 1, "source_folders": ["Failed"], "records": records, "validation": validate_records(records)}
        if manifest["validation"]["unreadable_count"] or manifest["validation"]["duplicate_stable_ids"] or manifest["validation"]["duplicate_content_hashes"]:
            raise RuntimeError("Remediation manifest integrity validation failed.")
        output.mkdir(parents=True, exist_ok=True)
        write_manifest(manifest, output / "source-manifest.json")
        digest = hashlib.sha256(manifest_bytes(manifest)).hexdigest()
        state_path = output / "state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("manifest_sha256") != digest:
                raise RuntimeError("Remediation checkpoint does not match its selected failed records.")
        else:
            state = {"version": 1, "scope": "failed-remediation", "manifest_sha256": digest,
                     "source_ids": [record["stable_source_id"] for record in records], "created_at": now(), "completed": {}}
        domains = load_domains(root)
        pending = [record for record in records if record["stable_source_id"] not in state["completed"]]
        for index, record in enumerate(pending, start=1):
            print(f"[{index}/{len(pending)}] {record['relative_path']}", flush=True)
            outcome, capture = run_record(root, record, domains)
            state["completed"][record["stable_source_id"]] = {"record": record, "outcome": outcome, "capture": capture}
            state["updated_at"] = now()
            materialise(output, state)
            print(f"  {outcome['status']}: {outcome['reason'] or 'schema and semantic validation passed'}", flush=True)
        if len(state["completed"]) != len(records):
            raise RuntimeError("Remediation did not reach terminal checkpoints for every selected source.")
        subprocess.run([sys.executable, str(root / "00_System/integrate_rebuild.py"), "--vault", str(root), "--run-dir", str(output), "--merge-existing"], cwd=root, check=True)
        index_report = build_index(root, list(chunk_records()), rebuild=False)
        atomic_json(output / "embedding-update.json", index_report)
        outcomes = [item["outcome"] for item in state["completed"].values()]
        result = {"selection_key": selection_key, "audit": audit_path.relative_to(root).as_posix(), "backup": backup_rows,
                  "archived": archive_rows, "retried": [{"stable_source_id": item["stable_source_id"], "status": item["status"], "reason": item["reason"]} for item in outcomes],
                  "embedding_update": index_report, "completed_at": now()}
        atomic_json(receipt, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
