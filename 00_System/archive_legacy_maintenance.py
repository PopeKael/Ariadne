"""Archive superseded maintenance scripts without changing their bytes.

The current rebuild-v1 menu and supported-command document deliberately retain
only current entry points. This one-time helper moves the audited legacy
scripts into an excluded namespace and records every byte hash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


LEGACY_FILES = [
    "ariadne.ps1", "Capture-ControlledOllama.ps1", "Commit.ps1",
    "Compile-Knowledge.ps1", "GraphHealth.ps1", "Invoke-GraphLinking.ps1",
    "Migrate-LegacyGraph.ps1", "Migrate-PersonEntities.ps1",
    "Publish-Knowledge.ps1", "Rebuild-GraphRelations.ps1",
    "Reclassification-Status.ps1", "Reclassify-All.ps1",
    "Reconcile-Graph.ps1", "Repair-LibraryIndex.ps1", "Repair-RetryQueue.ps1",
    "Resolve-PersonIdentities.ps1", "Resort-Archive.ps1",
    "Retry-FailedIngestion.ps1", "Run Injest.ps1", "cleanup_legacy_graph.py",
    "System-Reference.html",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.15 * (2 ** attempt))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive audited legacy maintenance scripts.")
    parser.add_argument("--vault", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--stamp", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = args.vault.resolve()
    archive_root = root / "Archive" / "LegacyMaintenance" / args.stamp / "00_System"
    report_path = root / "Reports" / f"legacy-maintenance-archive-{args.stamp}.json"
    rows = []
    for name in LEGACY_FILES:
        source = root / "00_System" / name
        destination = archive_root / name
        if source.exists():
            source_hash = sha256(source)
            if destination.exists() and sha256(destination) != source_hash:
                raise RuntimeError(f"Archive collision with different bytes: {destination}")
            rows.append({"original_path": source.relative_to(root).as_posix(), "archived_path": destination.relative_to(root).as_posix(),
                         "sha256": source_hash, "action": "would_move" if args.dry_run else ("already_archived" if destination.exists() else "moved")})
        elif destination.exists():
            rows.append({"original_path": (Path("00_System") / name).as_posix(), "archived_path": destination.relative_to(root).as_posix(),
                         "sha256": sha256(destination), "action": "already_archived"})
        else:
            raise RuntimeError(f"Neither active nor archived legacy script exists: {name}")
    if not args.dry_run:
        for row in rows:
            if row["action"] != "moved":
                continue
            source = root / row["original_path"]
            destination = root / row["archived_path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
            if sha256(destination) != row["sha256"]:
                raise RuntimeError(f"Hash verification failed after archive move: {destination}")
    report = {"archive_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "archive_root": archive_root.relative_to(root).as_posix(),
              "dry_run": args.dry_run, "files": rows, "count": len(rows)}
    atomic_json(report_path, report)
    print(json.dumps({"report": report_path.relative_to(root).as_posix(), "count": len(rows), "dry_run": args.dry_run}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
