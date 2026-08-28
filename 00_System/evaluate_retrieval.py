#!/usr/bin/env python3
"""Run the frozen Ariadne Vault Retrieval v1 evaluation set."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any, Callable

from ariadne_mcp import search_chunks


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES_PATH = ROOT / "00_System" / "evaluation" / "retrieval_cases.json"


def load_cases(path: Path) -> list[dict[str, Any]]:
    """Load and validate a frozen benchmark fixture."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") not in {1, 2}:
        raise ValueError("Benchmark fixture must be an object with version 1 or 2.")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Benchmark fixture must contain at least one case.")
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("query"), str):
            raise ValueError("Every benchmark case requires a string query.")
        negative = bool(case.get("negative"))
        if not negative and not case.get("expected_document_ids") and not case.get("expected_chunk_ids"):
            raise ValueError(f"Benchmark case {case.get('id', '<unnamed>')} has no expected result.")
        if negative and (case.get("expected_document_ids") or case.get("expected_chunk_ids")):
            raise ValueError(f"Negative benchmark case {case.get('id', '<unnamed>')} must have no expected result.")
    return cases


def relevant_rank(results: list[dict[str, Any]], case: dict[str, Any]) -> int | None:
    """Return the first one-based relevant rank for a benchmark case."""
    document_ids = set(case.get("expected_document_ids", []))
    chunk_ids = set(case.get("expected_chunk_ids", []))
    for rank, result in enumerate(results, start=1):
        if result.get("document_id") in document_ids or result.get("chunk_id") in chunk_ids:
            return rank
    return None


def precision_at_k(results: list[dict[str, Any]], case: dict[str, Any], k: int) -> float:
    expected_documents = set(case.get("expected_document_ids", []))
    expected_chunks = set(case.get("expected_chunk_ids", []))
    selected = results[:k]
    if not selected:
        return 0.0
    relevant = sum(
        1 for item in selected
        if item.get("document_id") in expected_documents or item.get("chunk_id") in expected_chunks
    )
    return relevant / len(selected)


def _mean(values: list[float]) -> float:
    return round(statistics.fmean(values), 2) if values else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 2)


def evaluate(cases: list[dict[str, Any]], search: Callable[[dict[str, Any]], dict[str, Any]] = search_chunks,
             limit: int = 5) -> dict[str, Any]:
    """Measure rank recall, usefulness, negative handling, latency, and evidence bounds."""
    if limit < 1:
        raise ValueError("limit must be at least 1.")
    outcomes = []
    latency: dict[str, list[float]] = {key: [] for key in ("total_ms", "lexical_ms", "vector_ms", "scoring_ms")}
    evidence: dict[str, list[float]] = {key: [] for key in ("candidate_count", "selected_count", "evidence_chars", "evidence_tokens_estimate")}
    suppression: dict[str, list[float]] = {
        key: [] for key in (
            "duplicate_suppressed_count", "near_duplicate_suppressed_count",
            "document_cap_suppressed_count", "quality_suppressed_count",
        )
    }
    positive = []
    negative = []
    for case in cases:
        result = search({"query": case["query"], "limit": limit, "diagnostics": True})
        results = result.get("results", []) if isinstance(result, dict) else []
        telemetry = result.get("telemetry", {}) if isinstance(result, dict) else {}
        if not isinstance(telemetry, dict):
            telemetry = {}
        rank = relevant_rank(results, case)
        is_negative = bool(case.get("negative"))
        outcome = {
            "id": case.get("id"),
            "category": case.get("category"),
            "query": case["query"],
            "negative": is_negative,
            "relevant_rank": rank,
            "reciprocal_rank": 1 / rank if rank else 0.0,
            "hit_at_1": bool(rank and rank <= 1),
            "hit_at_3": bool(rank and rank <= 3),
            "hit_at_5": bool(rank and rank <= 5),
            "precision_at_5": precision_at_k(results, case, min(5, limit)) if not is_negative else 0.0,
            "no_evidence": not results,
            "returned_chunk_ids": [item.get("chunk_id") for item in results[:limit]],
            "returned_documents": [item.get("document_id") for item in results[:limit]],
            "selected_evidence": [
                {
                    "title": item.get("title"),
                    "path": item.get("source_path") or item.get("path"),
                    "score": item.get("combined_score", item.get("score")),
                    "method": item.get("retrieval_method"),
                }
                for item in results[:3]
                if isinstance(item, dict)
            ],
            "telemetry": {key: telemetry.get(key) for key in (
                "total_ms", "lexical_ms", "vector_ms", "scoring_ms",
                "candidate_count", "selected_count", "evidence_chars", "evidence_tokens_estimate",
                "duplicate_suppressed_count", "near_duplicate_suppressed_count",
                "document_cap_suppressed_count", "quality_suppressed_count",
            )},
            "retrieval_diagnostics": telemetry.get("diagnostics", {}),
        }
        outcomes.append(outcome)
        (negative if is_negative else positive).append(outcome)
        for key in latency:
            if isinstance(telemetry.get(key), (int, float)):
                latency[key].append(float(telemetry[key]))
        for key in evidence:
            if isinstance(telemetry.get(key), (int, float)):
                evidence[key].append(float(telemetry[key]))
        for key in suppression:
            if isinstance(telemetry.get(key), (int, float)):
                suppression[key].append(float(telemetry[key]))

    def recall(k: int) -> float:
        return _mean([float(item[f"hit_at_{k}"]) for item in positive])

    negative_no_evidence = sum(item["no_evidence"] for item in negative) / len(negative) if negative else 0.0
    return {
        "case_count": len(cases),
        "positive_case_count": len(positive),
        "negative_case_count": len(negative),
        "limit": limit,
        "recall_at_1": recall(1),
        "recall_at_3": recall(3),
        "recall_at_5": recall(5),
        "recall_at_k": recall(min(5, limit)),
        "mrr_positive": _mean([item["reciprocal_rank"] for item in positive]),
        "mrr": _mean([item["reciprocal_rank"] for item in positive]),
        "precision_at_5_positive": _mean([item["precision_at_5"] for item in positive]),
        "usefulness_at_5_positive": recall(5),
        "negative_no_evidence_rate": round(negative_no_evidence, 3),
        "negative_false_positive_rate": round(1 - negative_no_evidence, 3) if negative else 0.0,
        "latency_ms": {
            key: {"mean": _mean(values), "p50": _percentile(values, 0.50), "p95": _percentile(values, 0.95)}
            for key, values in latency.items()
        },
        "evidence_bounds": {
            key: {"mean": _mean(values), "max": round(max(values), 2) if values else 0.0}
            for key, values in evidence.items()
        },
        "suppression": {
            key: {"mean": _mean(values), "total": round(sum(values), 2), "max": round(max(values), 2) if values else 0.0}
            for key, values in suppression.items()
        },
        "outcomes": outcomes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Ariadne Vault Retrieval v1.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH, help="Path to frozen benchmark JSON.")
    parser.add_argument("--limit", type=int, default=5, help="Evidence limit (default: 5).")
    args = parser.parse_args()
    print(json.dumps(evaluate(load_cases(args.cases), limit=args.limit), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
