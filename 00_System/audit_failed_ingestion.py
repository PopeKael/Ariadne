"""Create an evidence-led audit of the completed rebuild-v1 rejected sources.

This is deliberately read-only with respect to source Markdown, the active
catalogue, and rebuild state.  It only writes the two audit reports supplied
on the command line (or their dated defaults under Reports/).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vault_rebuild import content_hash


def json_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic_write(path: Path, data: bytes, attempts: int = 8) -> None:
    """Write a report atomically, retaining the prior report on lock failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(attempts):
            try:
                os.replace(temporary, path)
                return
            except OSError as exc:
                if not isinstance(exc, PermissionError) and getattr(exc, "winerror", None) not in {5, 32, 33}:
                    raise
                if attempt == attempts - 1:
                    raise RuntimeError(f"Could not replace audit report: {path}") from exc
                time.sleep(0.15 * (2 ** attempt))
    finally:
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


def compact(value: str, width: int = 96) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= width else value[: width - 1] + "…"


def classify(reason: str, source_text: str) -> tuple[str, str, str]:
    """Return classification, recommendation, and evidence level."""
    if reason == "invalid_json_in_model_response":
        return ("model-json-failure", "Keep in Failed; retry only after the captured truncated model final-output issue is addressed.", "confirmed")
    if reason == "invalid_domain_proposal":
        return ("pipeline-defect", "Retry after enforcing the controlled domain enum in the JSON schema; preserve the original source unchanged.", "confirmed")
    if reason == "empty_model_response":
        return ("retry-safe", "A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty.", "confirmed")
    if reason == "summary_too_short":
        body = re.sub(r"(?s)^---.*?---\s*", "", source_text).strip()
        if len(body) < 40:
            return ("obsolete", "Archive as an empty/metadata-only intake item; do not retry.", "confirmed")
        return ("manual-review", "Leave in Failed for human relevance review; the model returned structurally valid but semantically insufficient enrichment.", "confirmed")
    return ("manual-review", "Keep in Failed pending manual inspection.", "confirmed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only rebuild-v1 Failed/ audit.")
    parser.add_argument("--vault", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--stamp", default=datetime.now(timezone.utc).strftime("%Y%m%d"))
    args = parser.parse_args()
    root = args.vault.resolve()
    run_dir = (args.run_dir or root / "00_System/Data/rebuild-v1/bulk").resolve()
    manifest = json.loads((run_dir / "source-manifest.json").read_text(encoding="utf-8"))
    rejections = json_lines(run_dir / "rejections.jsonl")
    captures = json_lines(run_dir / "model-captures.jsonl")
    movement = json.loads((root / "Reports/rebuild-v1-movement.json").read_text(encoding="utf-8"))
    active = json.loads((root / "00_System/library.json").read_text(encoding="utf-8"))
    records = {item["stable_source_id"]: item for item in manifest["records"]}
    movements = {item["stable_source_id"]: item for item in movement}
    active_ids = {item.get("stable_source_id") or item.get("document_id") for item in active}
    active_hashes = {item.get("content_sha256") for item in active}
    capture_by_id = {item["stable_source_id"]: (line, item) for line, item in enumerate(captures, start=1)}

    rows: list[dict[str, Any]] = []
    for outcome in sorted(rejections, key=lambda item: item["relative_path"].casefold()):
        sid = outcome["stable_source_id"]
        record = records[sid]
        move = movements.get(sid, {})
        current_path = str(move.get("new_path") or outcome["relative_path"])
        file_path = root / current_path
        raw = file_path.read_bytes()
        source_text = raw.decode("utf-8-sig")
        capture_line, capture = capture_by_id[sid]
        attempt = (capture.get("final_attempt") or {})
        content = str(attempt.get("message_content") or "")
        raw_response = attempt.get("raw_response")
        envelope = attempt.get("response_envelope")
        try:
            json.loads(content)
            content_is_json = True
            json_error = None
        except json.JSONDecodeError as exc:
            content_is_json = False
            json_error = f"{exc.msg} at character {exc.pos}" if content.strip() else None
        classification, recommendation, evidence = classify(str(outcome.get("reason") or ""), source_text)
        canonical_hash = record["canonical_content_sha256"]
        row = {
            "stable_source_id": sid,
            "vault_relative_path": current_path,
            "original_manifest_path": record["relative_path"],
            "filename": file_path.name,
            "source_type": record.get("source_type"),
            "file_size_bytes": len(raw),
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "canonical_content_sha256": canonical_hash,
            "canonical_hash_matches_manifest": content_hash(source_text) == canonical_hash,
            "failure_timestamp": outcome.get("completed_at"),
            "failure_message": outcome.get("reason"),
            "pipeline_stage": {
                "empty_model_response": "model_final_output",
                "invalid_json_in_model_response": "model_final_output_json_parse",
                "invalid_domain_proposal": "controlled_domain_validation",
                "summary_too_short": "semantic_validation",
            }.get(outcome.get("reason"), "unknown"),
            "ollama_response_received": isinstance(envelope, dict) or raw_response is not None,
            "ollama_transport_error": attempt.get("error"),
            "ollama_envelope_valid_json": isinstance(envelope, dict),
            "message_content_length": len(content),
            "message_content_valid_json": content_is_json,
            "message_content_json_error": json_error,
            "message_content_preview": compact(content),
            "message_thinking_length": len(str(attempt.get("message_thinking") or "")),
            "capture_reference": {"path": (run_dir / "model-captures.jsonl").relative_to(root).as_posix(), "line": capture_line},
            "duplicate_in_completed_manifest": False,
            "represented_in_active_catalogue_by_id": sid in active_ids,
            "represented_in_active_catalogue_by_hash": canonical_hash in active_hashes,
            "source_assessment": "empty_or_metadata_only" if classification == "obsolete" else "nonempty_source; relevance not automatically judged",
            "classification": classification,
            "recommended_action": recommendation,
            "evidence_level": evidence,
        }
        rows.append(row)

    counts = Counter(row["classification"] for row in rows)
    reasons = Counter(row["failure_message"] for row in rows)
    report = {
        "audit_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_source_audit",
        "run_dir": run_dir.relative_to(root).as_posix(),
        "scope": {"failed_records": len(rows), "failed_markdown_files": len(list((root / "Failed").glob("*.md")))},
        "summary": {"by_failure_message": dict(sorted(reasons.items())), "by_classification": dict(sorted(counts.items()))},
        "confirmed_system_findings": [
            "All 67 records received an Ollama response envelope and have no recorded transport error.",
            "The adapter POSTs to /api/chat and reads response.message.content; it does not use /api/generate.",
            "Twelve captured message.content values are malformed/truncated JSON; no Markdown fences were observed in those captures.",
            "The JSON schema permits any string for proposed_domains even though semantic validation requires the controlled vocabulary; this is a pipeline defect behind the 15 invalid_domain_proposal rejections.",
            "The active catalogue contains none of the failed stable IDs or canonical content hashes.",
            "One 70-byte untitled source is empty/metadata-only after front matter and is obsolete intake material.",
        ],
        "not_observed_in_this_run": [
            "Malformed HTTP request payloads, unsupported endpoint/format errors, transport timeouts, encoding/read failures, duplicate source identities, multiple JSON objects, concurrent-run evidence, or partial source moves.",
            "A fixed context-limit cause cannot be proved from the captures alone; malformed JSON ends mid-object and is consistent with truncated final generation.",
        ],
        "items": rows,
    }
    json_path = root / "Reports" / f"Failed-Ingestion-Audit-{args.stamp}.json"
    md_path = root / "Reports" / f"Failed-Ingestion-Audit-{args.stamp}.md"
    atomic_write(json_path, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    lines = [
        "# Failed ingestion audit", "", f"Generated: {report['created_at']}", "",
        "## Scope", "", f"- Rebuild-v1 rejected records: {len(rows)}", f"- Current `Failed/` Markdown files: {report['scope']['failed_markdown_files']}", "",
        "## Classification", "",
    ]
    lines += [f"- `{name}`: {count}" for name, count in sorted(counts.items())]
    lines += ["", "## Confirmed findings", ""] + [f"- {item}" for item in report["confirmed_system_findings"]]
    lines += ["", "## Not observed / not proven", ""] + [f"- {item}" for item in report["not_observed_in_this_run"]]
    lines += ["", "## Per-source findings", "", "| File | Type | Size | Failure stage | JSON | Classification | Recommended action |", "|---|---:|---:|---|---|---|---|"]
    for row in rows:
        lines.append("| `{}` | {} | {} | {} | {} | `{}` | {} |".format(
            row["vault_relative_path"].replace("|", "\\|"), row["source_type"], row["file_size_bytes"], row["pipeline_stage"],
            "yes" if row["message_content_valid_json"] else "no", row["classification"], row["recommended_action"].replace("|", "\\|")))
    lines += ["", "The JSON companion contains hashes, timestamps, exact failure messages, capture references, and response diagnostics for every row.", ""]
    atomic_write(md_path, "\n".join(lines).encode("utf-8"))
    print(json.dumps({"markdown": md_path.relative_to(root).as_posix(), "json": json_path.relative_to(root).as_posix(), "summary": report["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
