"""Quarantine legacy Wiki graph material without changing its bytes."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


KEEP = {"Wiki/index.md", "Wiki/index.html", "Wiki/KnowledgeMap.md"}
ADMIN_PREFIXES = ("Archive/", "Review/", "Reports/", "Logs/", "00_System/Data/")
WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def markdown_files(root: Path) -> list[Path]:
    return sorted((p for p in root.rglob("*.md") if ".git" not in p.parts), key=lambda p: relative(root, p).casefold())


def graph_visible(root: Path, path: Path) -> bool:
    rel = relative(root, path)
    return not rel.startswith(ADMIN_PREFIXES)


def link_target(raw: str) -> str:
    return raw.strip().replace("\\", "/").casefold()


def topology(root: Path, paths: Iterable[Path]) -> tuple[dict[str, int], dict[str, int], list[str]]:
    visible = [p for p in paths if graph_visible(root, p)]
    by_stem: dict[str, list[str]] = collections.defaultdict(list)
    for path in visible:
        by_stem[path.stem.casefold()].append(relative(root, path))
    inbound: collections.Counter[str] = collections.Counter()
    outbound: collections.Counter[str] = collections.Counter()
    for source in visible:
        source_rel = relative(root, source)
        text = source.read_text(encoding="utf-8-sig", errors="replace")
        targets = [link_target(item) for item in WIKILINK.findall(text)]
        outbound[source_rel] = len(targets)
        for target in targets:
            target_name = Path(target).name
            target_stem = target_name[:-3] if target_name.endswith(".md") else target_name
            for destination in by_stem.get(target_stem, []):
                inbound[destination] += 1
    orphans = [relative(root, p) for p in visible if inbound[relative(root, p)] == 0 and outbound[relative(root, p)] == 0]
    return dict(inbound), dict(outbound), sorted(orphans, key=str.casefold)


def duplicate_groups(root: Path, paths: Iterable[Path]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = collections.defaultdict(list)
    for path in paths:
        if graph_visible(root, path):
            groups[path.stem.casefold()].append(relative(root, path))
    return {key: sorted(value, key=str.casefold) for key, value in groups.items() if len(value) > 1}


def main() -> int:
    parser = argparse.ArgumentParser(description="Move unreferenced legacy Wiki graph material into Archive.")
    parser.add_argument("--vault", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    root = args.vault.resolve()
    all_before = markdown_files(root)
    candidates = [p for p in all_before if relative(root, p).startswith("Wiki/") and relative(root, p) not in KEEP]
    library = json.loads((root / "00_System/library.json").read_text(encoding="utf-8-sig"))
    active_refs = {str(item.get("wiki_path", "")).split("#", 1)[0] for item in library if item.get("wiki_path")}
    generated_index_text = (root / "Wiki/index.md").read_text(encoding="utf-8-sig")
    generated_index_refs = {item.lstrip("/").split("#", 1)[0] for item in re.findall(r"\]\((/[^)]+)\)", generated_index_text)}
    candidate_refs = sorted((relative(root, p) for p in candidates if relative(root, p) in active_refs or relative(root, p) in generated_index_refs), key=str.casefold)
    if candidate_refs:
        raise RuntimeError(f"Active/generated reference prevents cleanup: {candidate_refs[:10]}")
    before_inbound, before_outbound, before_orphans = topology(root, all_before)
    before_duplicates = duplicate_groups(root, all_before)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_root = root / "Archive/LegacyGraphCleanup" / stamp / "Wiki"
    rows = []
    for source in candidates:
        source_rel = relative(root, source)
        destination = archive_root / Path(source_rel).relative_to("Wiki")
        if destination.exists():
            if digest(source) != digest(destination):
                raise RuntimeError(f"Archive collision with different bytes: {destination}")
            action = "already_archived"
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            source_hash = digest(source)
            source.rename(destination)
            if digest(destination) != source_hash:
                raise RuntimeError(f"Post-move hash mismatch: {source_rel}")
            action = "moved"
        rows.append({
            "original_path": source_rel,
            "archived_path": relative(root, destination),
            "reason": "legacy Wiki graph-only material excluded from rebuild-v1 active retrieval",
            "active_catalogue_reference": source_rel in active_refs,
            "generated_index_reference": source_rel in generated_index_refs,
            "inbound_link_count": before_inbound.get(source_rel, 0),
            "outbound_link_count": before_outbound.get(source_rel, 0),
            "content_sha256": digest(destination),
            "action": action,
        })
    all_after = markdown_files(root)
    after_inbound, after_outbound, after_orphans = topology(root, all_after)
    after_duplicates = duplicate_groups(root, all_after)
    report = {
        "cleanup_version": 1,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "archive_root": relative(root, archive_root.parent.parent),
        "candidate_count": len(candidates),
        "moved_count": sum(item["action"] == "moved" for item in rows),
        "already_archived_count": sum(item["action"] == "already_archived" for item in rows),
        "graph_visible_markdown_before": sum(graph_visible(root, p) for p in all_before),
        "graph_visible_markdown_after": sum(graph_visible(root, p) for p in all_after),
        "duplicate_display_name_group_count_before": len(before_duplicates),
        "duplicate_display_name_group_count_after": len(after_duplicates),
        "content_creation_before": before_duplicates.get("content creation", []),
        "content_creation_after": after_duplicates.get("content creation", []),
        "orphan_count_before": len(before_orphans),
        "orphan_count_after": len(after_orphans),
        "active_catalogue_reference_count": len(candidate_refs),
        "movements": rows,
    }
    report_path = root / "Reports" / f"legacy-graph-cleanup-{stamp}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "movements"}, ensure_ascii=False, indent=2))
    print(f"movement_report={relative(root, report_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
