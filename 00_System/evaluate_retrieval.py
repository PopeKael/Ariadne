#!/usr/bin/env python3
"""Run Ariadne retrieval benchmark cases against the local MCP search code.

The fixture deliberately supports document-level expectations as well as exact
chunk IDs. Prefer document IDs until a question genuinely needs one passage;
chunk IDs change when source Markdown is edited or rechunked.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from ariadne_mcp import search_chunks


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES_PATH = ROOT / "00_System" / "evaluation" / "retrieval_cases.json"


def load_cases(path: Path) -> list[dict[str, Any]]:
    """Load and minimally validate versioned benchmark cases."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("Benchmark fixture must be a JSON object with version 1.")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Benchmark fixture must contain at least one case.")
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("query"), str):
            raise ValueError("Every benchmark case requires a string query.")
        if not case.get("expected_document_ids") and not case.get("expected_chunk_ids"):
            raise ValueError(f"Benchmark case {case.get('id', '<unnamed>')} has no expected result.")
    return cases


def relevant_rank(results: list[dict[str, Any]], case: dict[str, Any]) -> int | None:
    """Return the first one-based relevant rank for a benchmark case."""
    document_ids = set(case.get("expected_document_ids", []))
    chunk_ids = set(case.get("expected_chunk_ids", []))
    for rank, result in enumerate(results, start=1):
        if result.get("document_id") in document_ids or result.get("chunk_id") in chunk_ids:
            return rank
    return None


def evaluate(cases: list[dict[str, Any]], search: Callable[[dict[str, Any]], dict[str, Any]] = search_chunks,
             limit: int = 5) -> dict[str, Any]:
    """Evaluate cases and return Recall@K and mean reciprocal rank."""
    if limit < 1:
        raise ValueError("limit must be at least 1.")
    outcomes = []
    for case in cases:
        results = search({"query": case["query"], "limit": limit}).get("results", [])
        rank = relevant_rank(results, case)
        outcomes.append({
            "id": case.get("id"), "query": case["query"], "relevant_rank": rank,
            "reciprocal_rank": 1 / rank if rank else 0.0,
            "hit": rank is not None,
            "returned_chunk_ids": [item.get("chunk_id") for item in results],
        })
    count = len(outcomes)
    return {
        "case_count": count,
        "limit": limit,
        "recall_at_k": sum(item["hit"] for item in outcomes) / count,
        "mrr": sum(item["reciprocal_rank"] for item in outcomes) / count,
        "outcomes": outcomes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Ariadne chunk retrieval.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH, help="Path to benchmark JSON.")
    parser.add_argument("--limit", type=int, default=5, help="Results evaluated per query (default: 5).")
    args = parser.parse_args()
    report = evaluate(load_cases(args.cases), limit=args.limit)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
