"""Materialise a concise Markdown/JSON record for a completed remediation run."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from run_rebuild import atomic_json, atomic_bytes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--stamp", default=datetime.now(timezone.utc).strftime("%Y%m%d"))
    args = parser.parse_args()
    root = args.vault.resolve()
    receipt_path = args.receipt if args.receipt.is_absolute() else root / args.receipt
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    audit = json.loads((root / receipt["audit"]).read_text(encoding="utf-8"))
    outcomes = receipt["retried"]
    recovered = [item for item in outcomes if item["status"] == "accepted"]
    still_failed = [item for item in outcomes if item["status"] != "accepted"]
    active = json.loads((root / "00_System/library.json").read_text(encoding="utf-8"))
    payload = {
        "report_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "audit": receipt["audit"],
        "receipt": receipt_path.relative_to(root).as_posix(),
        "before": audit["summary"],
        "after": {"active_catalogue_records": len(active), "processed_markdown_files": len(list((root / "Processed").glob("*.md"))),
                  "failed_markdown_files": len(list((root / "Failed").glob("*.md"))), "inbox_markdown_files": len([p for p in (root / "Inbox").glob("*.md") if p.name.lower() != "readme.md"])},
        "recovered": recovered,
        "still_failed_after_retry": still_failed,
        "still_failed_by_reason": dict(sorted(Counter(item.get("reason") for item in still_failed).items())),
        "archived": receipt["archived"],
        "backup": receipt["backup"],
        "embedding_update": receipt["embedding_update"],
        "manual_review_count": len(list((root / "Failed").glob("*.md"))),
    }
    json_path = root / "Reports" / f"Failed-Ingestion-Remediation-{args.stamp}.json"
    md_path = root / "Reports" / f"Failed-Ingestion-Remediation-{args.stamp}.md"
    atomic_json(json_path, payload)
    lines = ["# Failed ingestion remediation", "", f"Generated: {payload['created_at']}", "",
             "## Outcome", "", f"- Recovered into active catalogue: {len(recovered)}", f"- Still in `Failed/` after retry: {len(still_failed)}", f"- Archived obsolete intake: {len(payload['archived'])}", f"- Current manual-review queue (`Failed/`): {payload['manual_review_count']}",
             f"- Active catalogue: {payload['after']['active_catalogue_records']} records", f"- Embeddings: {payload['embedding_update']['documents']} documents / {payload['embedding_update']['chunks']} chunks / {payload['embedding_update']['failed']} failures", "",
             "## Recovered sources", ""]
    lines.extend(f"- `{item['stable_source_id']}`" for item in recovered)
    lines += ["", "## Still failed after safe retry", ""]
    lines.extend(f"- `{item['stable_source_id']}` — `{item['reason']}`" for item in still_failed)
    lines += ["", "## Archived", ""]
    lines.extend(f"- `{item['old_path']}` → `{item['archived_path']}`" for item in payload["archived"])
    lines += ["", "Original bytes for every selected source are retained under the archive path recorded in the JSON companion.", ""]
    atomic_bytes(md_path, "\n".join(lines).encode("utf-8"))
    print(json.dumps({"markdown": md_path.relative_to(root).as_posix(), "json": json_path.relative_to(root).as_posix()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
