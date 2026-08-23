from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
VAULT_ROOT = Path(os.environ.get("ARIADNE_VAULT_ROOT", str(PROJECT_ROOT))).expanduser().resolve()
VAULT_SYSTEM = VAULT_ROOT / "00_System"
sys.path.insert(0, str(VAULT_SYSTEM))

import ariadne_mcp  # noqa: E402


def write_status(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    spec_path = Path(sys.argv[1]).resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    status_path = Path(str(spec_path) + ".status.json")
    query = str(spec.get("query", "")).strip()
    mode = str(spec.get("mode", "search"))
    limit = max(1, min(int(spec.get("limit", 8)), 20))

    def progress(stage: str, message: str, completed: int = 0, total: int = 0) -> None:
        write_status(status_path, {
            "state": "running", "stage": stage, "message": message,
            "completed": completed, "total": total,
        })

    try:
        if not query:
            raise ValueError("A non-empty vault query is required.")
        progress("starting", "Starting the local KnowledgeVault worker…")
        if mode == "search":
            result = ariadne_mcp.search_chunks({"query": query, "limit": limit})
        elif mode == "summary":
            result = ariadne_mcp.summarize_knowledge({"query": query, "limit": limit})
        elif mode == "answer":
            result = ariadne_mcp.planned_knowledge_query(query, limit, progress, "answer")
        else:
            raise ValueError(f"Unknown query mode: {mode}")
        write_status(status_path, {
            "state": "complete", "stage": "complete", "message": "Vault query complete.",
            "completed": 1, "total": 1, "result": result,
        })
        return 0
    except Exception as exc:
        write_status(status_path, {
            "state": "error", "stage": "error", "message": str(exc),
            "completed": 0, "total": 0,
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
