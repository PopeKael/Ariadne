"""Checkpointed, review-only rebuild runner. Derived output only; canonical sources are read-only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_rebuild_pilot import (MAX_TRANSPORT_ATTEMPTS, final_content, invoke_ollama, load_domains,
                               model_request, response_metrics, reviewable_candidates, select_pilot,
                               validate_proposal, validate_semantics)
from vault_rebuild import build_manifest, manifest_bytes, write_manifest


def atomic_bytes(path: Path, data: bytes, attempts: int = 8) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(attempts):
            try:
                os.replace(temp_name, path)
                return
            except OSError as exc:
                if not isinstance(exc, PermissionError) and getattr(exc, "winerror", None) not in {5, 32, 33}:
                    raise
                if attempt == attempts - 1:
                    raise RuntimeError(f"Could not replace checkpoint after {attempts} attempts: {path}") from exc
                time.sleep(0.15 * (2 ** attempt))
    finally:
        if os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def atomic_json(path: Path, value: Any) -> None:
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def atomic_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    atomic_bytes(path, "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values).encode("utf-8"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_record(root: Path, record: dict[str, Any], domains: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    text = (root / record["relative_path"]).read_text(encoding="utf-8-sig")
    attempts: list[dict[str, Any]] = []
    raw = None
    envelope = None
    error = None
    content = ""
    for attempt_number in range(1, MAX_TRANSPORT_ATTEMPTS + 1):
        request = model_request(record, text, domains)
        started = time.perf_counter()
        raw, envelope, error = invoke_ollama(request)
        latency_ms = round((time.perf_counter() - started) * 1000)
        content, thinking = final_content(envelope)
        attempts.append({"attempt": attempt_number, "request": request, "raw_response": raw,
                         "response_envelope": envelope, "message_content": content,
                         "message_thinking": thinking, "error": error,
                         "metrics": response_metrics(envelope, raw, latency_ms)})
        if not error:
            break
    proposal = None
    reason = error
    if not reason and not content.strip():
        reason = "empty_model_response"
    if not reason:
        try:
            proposal, reason = validate_proposal(json.loads(content), domains)
            if proposal and not reason:
                reason = validate_semantics(proposal)
                if reason:
                    proposal = None
        except json.JSONDecodeError:
            reason = "invalid_json_in_model_response"
    status = "accepted" if proposal else ("failed" if error else "rejected")
    outcome = {"stable_source_id": record["stable_source_id"], "relative_path": record["relative_path"],
               "status": status, "reason": reason, "proposal": proposal,
               "generic_candidates_held": reviewable_candidates(proposal) if proposal else {},
               "attempt_count": len(attempts), "completed_at": now()}
    capture = {"stable_source_id": record["stable_source_id"], "relative_path": record["relative_path"],
               "attempts": attempts, "final_attempt": attempts[-1], "error": error}
    return outcome, capture


def materialise(output: Path, state: dict[str, Any]) -> None:
    completed = list(state["completed"].values())
    atomic_jsonl(output / "model-captures.jsonl", [item["capture"] for item in completed])
    atomic_json(output / "catalogue.json", [item["outcome"] for item in completed])
    atomic_jsonl(output / "rejections.jsonl", [item["outcome"] for item in completed
                                                  if item["outcome"]["status"] != "accepted"])
    atomic_json(output / "state.json", state)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run or resume the review-only rebuild.")
    parser.add_argument("--vault", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--pilot", action="store_true", help="Use the fixed 12-source pilot instead of all readable sources.")
    parser.add_argument("--output", type=Path, help="Derived output directory; defaults to rebuild-v1/bulk.")
    parser.add_argument("--retry-recorded", action="store_true", help="Retry prior rejected/failed records; accepted records remain skipped.")
    parser.add_argument("--test-stop-after", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = args.vault.resolve()
    output = (args.output or root / "00_System" / "Data" / "rebuild-v1" / ("pilot-resume" if args.pilot else "bulk")).resolve()
    manifest = build_manifest(root)
    if manifest["validation"]["unreadable_count"] or manifest["validation"]["duplicate_stable_ids"] or manifest["validation"]["duplicate_content_hashes"]:
        raise RuntimeError("Manifest validation failed; rebuild not started.")
    write_manifest(manifest, output / "source-manifest.json")
    records = select_pilot(root, manifest["records"]) if args.pilot else [r for r in manifest["records"] if not r.get("read_error")]
    manifest_hash = hashlib.sha256(manifest_bytes(manifest)).hexdigest()
    state_path = output / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("manifest_sha256") != manifest_hash or state.get("source_ids") != [r["stable_source_id"] for r in records]:
            raise RuntimeError("Checkpoint does not match the current source manifest/scope; choose a new output directory.")
    else:
        state = {"version": 1, "scope": "pilot" if args.pilot else "full", "manifest_sha256": manifest_hash,
                 "source_ids": [r["stable_source_id"] for r in records], "created_at": now(), "completed": {}}
    domains = load_domains(root)
    completed = state["completed"]
    pending = [r for r in records if r["stable_source_id"] not in completed or
               (args.retry_recorded and completed[r["stable_source_id"]]["outcome"]["status"] != "accepted")]
    print(f"Rebuild scope: {len(records)} source(s); completed: {len(completed)}; pending now: {len(pending)}")
    if not pending:
        print("Nothing to do. It is safe to close PowerShell.")
        return 0
    try:
        for index, record in enumerate(pending, start=1):
            print(f"[{index}/{len(pending)}] {record['relative_path']}", flush=True)
            outcome, capture = run_record(root, record, domains)
            completed[record["stable_source_id"]] = {"record": record, "outcome": outcome, "capture": capture}
            state["updated_at"] = now()
            materialise(output, state)
            print(f"  {outcome['status']}: {outcome['reason'] or 'schema and semantic validation passed'}", flush=True)
            if args.test_stop_after and index == args.test_stop_after:
                raise KeyboardInterrupt
    except KeyboardInterrupt:
        state["updated_at"] = now()
        materialise(output, state)
        print("Interrupted safely. Checkpoint saved; rerun the same command to resume. It is safe to close PowerShell.")
        return 130
    print("Rebuild run complete. It is safe to close PowerShell.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
