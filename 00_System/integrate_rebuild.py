"""Integrate a completed rebuild-v1 run into the active KnowledgeVault.

The runner is deliberately deterministic and conservative:
* accepted records are the only records promoted into library.json;
* source Markdown is moved without changing its bytes;
* uncertain model candidates remain in a review queue;
* active JSON/Markdown/HTML outputs are replaced atomically with bounded
  retries for Windows sharing violations;
* the previous active generated system is copied to a rollback directory.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import sys
import tempfile
import time
from urllib.parse import quote
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_rebuild import content_hash


RETRYABLE_WINERRORS = {5, 32, 33}
DOMAINS = [
    "AI & LLMs", "Archive", "Business", "Content Creation", "Gaming",
    "General Reference", "Health & Medicine", "History & Society",
    "Infrastructure", "Knowledge Management", "News & Current Affairs",
    "Personal", "Philosophy", "Projects", "Science & Technology",
    "Travel & Expat Experience",
]


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def atomic_write(path: Path, data: bytes, attempts: int = 8) -> None:
    """Replace *path* atomically, retaining the old file on failure."""
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
                if getattr(exc, "winerror", None) not in RETRYABLE_WINERRORS and not isinstance(exc, PermissionError):
                    raise
                if attempt == attempts - 1:
                    raise RuntimeError(f"Could not replace active output after {attempts} attempts: {path}") from exc
                time.sleep(0.15 * (2 ** attempt))
    finally:
        if os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preserve_previous(root: Path, rollback: Path) -> list[str]:
    targets = [
        root / "00_System/library.json", root / "00_System/KnowledgeMap.md",
        root / "Wiki/index.md", root / "Wiki/index.html", root / "Wiki/KnowledgeMap.md",
        root / "00_System/Data/embedding-index.json",
    ]
    copied: list[str] = []
    for source in targets:
        if source.is_file():
            destination = rollback / source.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.append(source.relative_to(root).as_posix())
    write_json(rollback / "rollback-manifest.json", {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": [{"path": p, "sha256": sha256(root / p)} for p in copied],
    })
    return copied


def source_destination(root: Path, old_relative: str, target_folder: str, stable_id: str) -> Path:
    old = Path(old_relative)
    candidate = root / target_folder / old.name
    if not candidate.exists():
        return candidate
    suffix = hashlib.sha256(stable_id.encode("utf-8")).hexdigest()[:12]
    return root / target_folder / f"{old.stem}__{suffix}{old.suffix}"


def file_sources(root: Path, records: list[dict[str, Any]], by_id: dict[str, dict[str, Any]], report_path: Path,
                 prior_moves: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    moves: list[dict[str, Any]] = []
    for record in records:
        sid = record["stable_source_id"]
        outcome = by_id[sid]["outcome"]
        old_relative = outcome["relative_path"]
        current_relative = prior_moves.get(sid, {}).get("new_path", old_relative)
        old = root / current_relative
        target_folder = "Processed" if outcome["status"] == "accepted" else "Failed"
        if not old.exists():
            raise RuntimeError(f"Source disappeared during integration: {old_relative}")
        destination = old if old.parent.name.casefold() == target_folder.casefold() else source_destination(root, old_relative, target_folder, sid)
        old_hash = content_hash(old.read_text(encoding="utf-8-sig"))
        if old_hash != record["canonical_content_sha256"]:
            raise RuntimeError(f"Source changed during integration: {old_relative}")
        if old.resolve() == destination.resolve():
            action = "already_in_destination"
        elif destination.exists():
            destination_hash = content_hash(destination.read_text(encoding="utf-8-sig"))
            if destination_hash != old_hash:
                safe_suffix = hashlib.sha256(sid.encode("utf-8")).hexdigest()[:12]
                destination = root / target_folder / f"{Path(old_relative).stem}__{safe_suffix}{Path(old_relative).suffix}"
                if destination.exists():
                    raise RuntimeError(f"Deterministic filename collision with different content: {destination}")
            else:
                action = "destination_already_contains_same_source"
        if "action" not in locals():
            destination.parent.mkdir(parents=True, exist_ok=True)
            old.rename(destination)
            action = "moved"
        moves.append({
            "stable_source_id": sid, "old_path": old_relative,
            "new_path": destination.relative_to(root).as_posix(),
            "processing_result": outcome["status"], "action": action,
            "content_sha256": record["canonical_content_sha256"],
        })
        if "action" in locals():
            del action
    write_json(report_path, moves)
    return moves


def build_library(root: Path, records: list[dict[str, Any]], outcomes: dict[str, dict[str, Any]], moves: dict[str, dict[str, Any]], existing: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    result_by_id = {item["document_id"]: item for item in (existing or []) if item.get("document_id")}
    for record in records:
        sid = record["stable_source_id"]
        outcome = outcomes[sid]
        if outcome["status"] != "accepted":
            continue
        proposal = outcome["proposal"]
        path = moves[sid]["new_path"]
        domains = [d for d in proposal.get("proposed_domains", []) if d in DOMAINS]
        title = record.get("title") or Path(path).stem
        result_by_id[sid] = {
            "source_name": Path(path).name, "document_id": sid,
            "stable_source_id": sid, "source_url": record.get("canonical_url"),
            "external_identity": record.get("external_identity"),
            "page_title": title, "content_sha256": record["canonical_content_sha256"],
            "primary_topic": domains[0] if domains else "General Reference",
            "secondary_domains": domains[1:], "proposed_domains": domains,
            "subtopics": proposal.get("concepts", []), "tags": proposal.get("entities", []),
            "links": proposal.get("links", []), "entities": proposal.get("entities", []),
            "people": proposal.get("people", []), "related_notes": [],
            "map_entry": title, "summary": proposal["summary"],
            "source_type": record.get("source_type"), "source_language": "en",
            "workflow_status": "accepted", "retrieval_status": "active",
            "retrieval_metadata": {"stable_source_id": sid, "content_sha256": record["canonical_content_sha256"],
                                    "model_confidence": proposal.get("confidence"), "enrichment_completed_at": outcome.get("completed_at")},
            "review_path": f"Review/rebuild-v1/{sid.replace(':', '_')}.md",
            "processed_path": path, "wiki_path": f"Wiki/index.md#{domains[0].lower().replace(' & ', '-').replace(' ', '-') if domains else 'general-reference'}",
            "indexed_at": datetime.now(timezone.utc).isoformat(),
        }
    result = list(result_by_id.values())
    result.sort(key=lambda x: x["document_id"])
    return result


def build_review_queue(root: Path, catalogue: list[dict[str, Any]], outcomes: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    queue: list[dict[str, Any]] = []
    for outcome in outcomes.values():
        held = outcome.get("generic_candidates_held") or {}
        for kind, candidates in held.items():
            for candidate in candidates if isinstance(candidates, list) else [candidates]:
                queue.append({"stable_source_id": outcome["stable_source_id"], "candidate_type": kind,
                              "candidate": candidate, "status": "needs_human_review", "source_path": outcome["relative_path"]})
    queue.sort(key=lambda x: (x["stable_source_id"], x["candidate_type"], str(x["candidate"])))
    lines = ["# Rebuild-v1 human-review queue", "", "Generated from held model candidates. No canonical People, Entities, or Wiki pages were created.", ""]
    for item in queue:
        lines.append(f"- `{item['candidate_type']}` `{item['candidate']}` — `{item['stable_source_id']}` ([source](/" + item["source_path"] + "))")
    return queue, "\n".join(lines) + "\n"


def build_wiki(root: Path, catalogue: list[dict[str, Any]], queue_count: int) -> tuple[str, str, str]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in catalogue:
        grouped[item["primary_topic"]].append(item)
    generated = datetime.now(timezone.utc).isoformat()
    md = ["# Ariadne Knowledge Vault", "", "> Generated from the accepted rebuild-v1 catalogue. Source Markdown remains canonical.", "", f"Generated: {generated}", "", f"Active sources: {len(catalogue)}", f"Human-review candidates: {queue_count}", "", "## Browse", ""]
    for domain in sorted(grouped):
        anchor = domain.lower().replace(" & ", "-").replace(" ", "-")
        md.append(f"- [{domain}](#{anchor}) ({len(grouped[domain])})")
    for domain in sorted(grouped):
        anchor = domain.lower().replace(" & ", "-").replace(" ", "-")
        md.extend(["", f'<a id="{anchor}"></a>', "", f"## {domain}", ""])
        for item in sorted(grouped[domain], key=lambda x: x["page_title"].casefold()):
            path = quote(item["processed_path"], safe="/")
            md.append(f"- [{item['page_title']}](/{{path}}) — {item['summary']}".replace("/{path}", "/" + path))
    md.append("")
    html_items = []
    for domain in sorted(grouped):
        html_items.append(f"<section id=\"{html.escape(domain.lower().replace(' & ', '-').replace(' ', '-'))}\"><h2>{html.escape(domain)}</h2><ul>")
        for item in sorted(grouped[domain], key=lambda x: x["page_title"].casefold()):
            href = "/" + quote(item["processed_path"], safe="/")
            html_items.append(f"<li><a href=\"{html.escape(href, quote=True)}\">{html.escape(item['page_title'])}</a><p>{html.escape(item['summary'])}</p></li>")
        html_items.append("</ul></section>")
    html_doc = "<!doctype html><meta charset=\"utf-8\"><title>Ariadne Knowledge Vault</title><style>body{font-family:system-ui;max-width:1200px;margin:2rem auto;line-height:1.5}section{margin-top:2rem}li{margin:.7rem 0}p{margin:.1rem 0 1rem}</style><h1>Ariadne Knowledge Vault</h1><p>Generated from accepted rebuild-v1 results. Active sources: %d. Human-review candidates: %d.</p>%s\n" % (len(catalogue), queue_count, "".join(html_items))
    map_doc = "# Active Knowledge Map\n\nGenerated from the accepted rebuild-v1 catalogue. Canonical concepts and entities are not auto-created.\n\n" + "\n".join(f"- **{d}** — {len(grouped[d])} active sources" for d in sorted(grouped)) + "\n"
    return "\n".join(md), html_doc, map_doc


def reconcile_movement_report(root: Path, manifest: dict[str, Any], outcomes: dict[str, dict[str, Any]]) -> None:
    """Rebuild the movement report from current bytes without moving files."""
    by_hash: dict[str, list[str]] = defaultdict(list)
    for folder in ("Inbox", "Processed", "Failed"):
        for path in (root / folder).glob("*.md"):
            if path.name.lower() == "readme.md":
                continue
            by_hash[content_hash(path.read_text(encoding="utf-8-sig"))].append(path.relative_to(root).as_posix())
    rows = []
    for record in manifest["records"]:
        paths = by_hash.get(record["canonical_content_sha256"], [])
        if len(paths) != 1:
            raise RuntimeError(f"Cannot reconcile movement report for {record['stable_source_id']}: {paths}")
        rows.append({
            "stable_source_id": record["stable_source_id"], "old_path": record["relative_path"],
            "new_path": paths[0], "processing_result": outcomes[record["stable_source_id"]]["status"],
            "action": "already_in_destination", "content_sha256": record["canonical_content_sha256"],
        })
    write_json(root / "Reports/rebuild-v1-movement.json", rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--reconcile-movement-report", action="store_true")
    parser.add_argument("--merge-existing", action="store_true", help="Merge accepted daily results into the active catalogue.")
    args = parser.parse_args()
    root = args.vault.resolve()
    run_dir = (args.run_dir or root / "00_System/Data/rebuild-v1/bulk").resolve()
    state = load_json(run_dir / "state.json")
    manifest = load_json(run_dir / "source-manifest.json")
    catalogue_run = load_json(run_dir / "catalogue.json")
    if args.reconcile_movement_report:
        reconcile_movement_report(root, manifest, {r["stable_source_id"]: r for r in catalogue_run})
        print("Movement report reconciled from current source hashes; no files moved.")
        return 0
    captures = [json.loads(line) for line in (run_dir / "model-captures.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    prior_moves: dict[str, dict[str, Any]] = {}
    prior_report = root / ("Reports/rebuild-v1-daily-movement.json" if args.merge_existing else "Reports/rebuild-v1-movement.json")
    if prior_report.exists():
        prior_rows = load_json(prior_report)
        if isinstance(prior_rows, list):
            prior_moves = {row["stable_source_id"]: row for row in prior_rows if isinstance(row, dict) and row.get("stable_source_id")}
    if len(manifest["records"]) != 810 or len(state.get("completed", {})) != 810 or len(captures) != 810:
        raise RuntimeError("Integration requires the completed 810-source run.")
    if len({r["stable_source_id"] for r in manifest["records"]}) != 810:
        raise RuntimeError("Duplicate stable identities in source manifest.")
    if manifest.get("validation", {}).get("duplicate_stable_ids") or manifest.get("validation", {}).get("duplicate_content_hashes"):
        raise RuntimeError("Manifest reports duplicate identities or content hashes.")
    records_by_id = {r["stable_source_id"]: r for r in manifest["records"]}
    outcomes = {r["stable_source_id"]: r for r in catalogue_run}
    state_outcomes = {sid: item["outcome"] for sid, item in state["completed"].items()}
    if outcomes != state_outcomes:
        raise RuntimeError("State and catalogue outcomes differ.")
    for r in manifest["records"]:
        current_relative = prior_moves.get(r["stable_source_id"], {}).get("new_path", r["relative_path"])
        path = root / current_relative
        if not path.is_file() or content_hash(path.read_text(encoding="utf-8-sig")) != r["canonical_content_sha256"]:
            raise RuntimeError(f"Source integrity failure before movement: {r['relative_path']}")
    stamp = now_stamp()
    rollback = root / "Archive/IntegrationRollback" / stamp
    preserved = preserve_previous(root, rollback)
    movement_path = root / ("Reports/rebuild-v1-daily-movement.json" if args.merge_existing else "Reports/rebuild-v1-movement.json")
    movement_rows = file_sources(root, manifest["records"], state["completed"], movement_path, prior_moves)
    movement_by_id = {x["stable_source_id"]: x for x in movement_rows}
    existing = load_json(root / "00_System/library.json") if args.merge_existing and (root / "00_System/library.json").exists() else None
    active = build_library(root, manifest["records"], outcomes, movement_by_id, existing)
    review_queue, review_md = build_review_queue(root, active, outcomes)
    if args.merge_existing and (root / "Review/rebuild-v1-human-review.json").exists():
        prior_queue = load_json(root / "Review/rebuild-v1-human-review.json")
        combined = {(item.get("stable_source_id"), item.get("candidate_type"), str(item.get("candidate"))): item for item in prior_queue}
        combined.update({(item.get("stable_source_id"), item.get("candidate_type"), str(item.get("candidate"))): item for item in review_queue})
        review_queue = sorted(combined.values(), key=lambda x: (x.get("stable_source_id", ""), x.get("candidate_type", ""), str(x.get("candidate", ""))))
        review_md = "# Rebuild-v1 human-review queue\n\nGenerated from held model candidates. No canonical People, Entities, or Wiki pages were created.\n\n" + "\n".join(f"- `{item['candidate_type']}` `{item['candidate']}` — `{item['stable_source_id']}` ([source](/" + item["source_path"] + "))" for item in review_queue) + "\n"
    write_json(root / "00_System/library.json", active)
    write_json(root / "00_System/Data/rebuild-v1/active-catalogue.json", active)
    write_json(root / "Review/rebuild-v1-human-review.json", review_queue)
    atomic_write(root / "Review/rebuild-v1-human-review.md", review_md.encode("utf-8"))
    md, html_doc, map_doc = build_wiki(root, active, len(review_queue))
    atomic_write(root / "Wiki/index.md", md.encode("utf-8"))
    atomic_write(root / "Wiki/index.html", html_doc.encode("utf-8"))
    atomic_write(root / "Wiki/KnowledgeMap.md", map_doc.encode("utf-8"))
    report = {
        "integration_version": 1, "completed_at": datetime.now(timezone.utc).isoformat(),
        "run_dir": run_dir.relative_to(root).as_posix(), "rollback_dir": rollback.relative_to(root).as_posix(),
        "preserved_previous_outputs": preserved, "accepted": sum(1 for x in catalogue_run if x["status"] == "accepted"),
        "rejected": sum(1 for x in catalogue_run if x["status"] == "rejected"), "failed": 0,
        "active_catalogue_records": len(active), "review_queue_count": len(review_queue),
        "movement_report": movement_path.relative_to(root).as_posix(), "active_catalogue": "00_System/library.json",
        "active_catalogue_copy": "00_System/Data/rebuild-v1/active-catalogue.json",
        "wiki_outputs": ["Wiki/index.md", "Wiki/index.html", "Wiki/KnowledgeMap.md"],
    }
    write_json(root / "Reports/rebuild-v1-integration.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
