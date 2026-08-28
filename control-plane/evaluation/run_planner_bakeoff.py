"""Run the frozen Ariadne Librarian Harness evaluation for one local model.

This uses the same semantic-interpreter and policy-resolver path as Home. It intentionally
does not send Vault content; attachment fixtures are metadata only.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any


CONTROL_PLANE = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CONTROL_PLANE.parent
sys.path.insert(0, str(CONTROL_PLANE))

import server  # noqa: E402
from librarian_harness import fallback_interpretation, fallback_plan, interpret_and_resolve  # noqa: E402


ENDPOINT = "http://127.0.0.1:11434"
CASES_PATH = Path(__file__).with_name("planner_cases.json")
RESULTS_DIR = Path(__file__).with_name("results")


def read_cases() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) < 60:
        raise RuntimeError("The frozen planner suite must contain at least 60 cases.")
    return payload, cases


def unload(model: str) -> None:
    request = urllib.request.Request(
        ENDPOINT + "/api/generate",
        data=json.dumps({"model": model, "prompt": "", "stream": False, "keep_alive": 0}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        response.read()


def fixture_attachments(case: dict[str, Any], shared: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return shared if case.get("runtime", {}).get("attachments") == "fixture" else []


def apply_runtime_fixture_constraints(context: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    """Apply per-case availability metadata without changing semantic expectations."""
    availability = runtime.get("tool_availability")
    if not isinstance(availability, dict):
        return context
    allowed = {str(tool_id) for tool_id, enabled in availability.items() if enabled is True}
    tools = context.get("available_tools")
    if isinstance(tools, list):
        context["available_tools"] = [
            tool for tool in tools
            if isinstance(tool, dict) and str(tool.get("tool_id")) in allowed
        ]
    return context


def semantic_expected(case: dict[str, Any]) -> dict[str, Any | None]:
    """Derive semantic targets from frozen v1 expectations without editing them."""
    expected = case.get("expected", {})
    intent = str(expected.get("intent") or "")
    needs_history = "vault" in intent and intent != "vault_or_current_ambiguous"
    needs_attachment = expected.get("primary_source") == "attachment"
    needs_current = bool(expected.get("needs_current_information"))
    if expected.get("use_heavy_model"):
        complexity = "high"
    elif int(expected.get("task_count_min", 0)) >= 2 or (needs_attachment and needs_current) or (needs_history and needs_current):
        complexity = "medium"
    else:
        complexity = "low"
    return {
        "intent": intent,
        "needs_personal_history": needs_history,
        "needs_current_information": needs_current,
        "needs_attachment": needs_attachment,
        "reasoning_complexity": complexity,
        "ambiguity": "high" if "ambiguous" in intent else None,
    }


def semantic_score(expected: dict[str, Any | None], semantic: dict[str, Any]) -> dict[str, Any]:
    fields = ["intent", "needs_personal_history", "needs_current_information", "needs_attachment", "reasoning_complexity"]
    if expected.get("ambiguity") is not None:
        fields.append("ambiguity")
    matches = {field: semantic.get(field) == expected.get(field) for field in fields}
    return {"correct": bool(matches) and all(matches.values()), "matches": matches, "scored_fields": fields}

def final_route_expected(case: dict[str, Any]) -> dict[str, Any]:
    """Normalize the frozen v1 Vault-never expectation at the controller boundary."""
    expected = dict(case.get("expected", {}))
    runtime = case.get("runtime", {})
    if runtime.get("vault_mode") == "never" and expected.get("primary_source") == "vault" and expected.get("use_vault") is False:
        expected["primary_source"] = "user_message"
    return expected



def effective_controller(case: dict[str, Any], plan: dict[str, Any], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    runtime = case.get("runtime", {})
    mode = runtime.get("vault_mode", "auto")
    policy = policy if isinstance(policy, dict) else {}
    planner_tools = plan.get("tools") if isinstance(plan.get("tools"), list) else []
    return {
        "use_vault": bool(plan.get("use_vault")),
        "tools": planner_tools,
        "policy_overrides": policy.get("policy_overrides", []),
        "capability_gaps": policy.get("capability_gaps", []),
        "controller_authoritative": bool(policy.get("controller_authoritative", False)),
        "vault_constraint_satisfied": mode != "never" or not bool(plan.get("use_vault")),
    }


def core_correct(expected: dict[str, Any], plan: dict[str, Any]) -> bool:
    return all(plan.get(field) == expected.get(field) for field in (
        "primary_source", "use_vault", "needs_current_information", "use_heavy_model", "tools"
    ))


def run_one(model: str, case: dict[str, Any], attachments: list[dict[str, Any]]) -> dict[str, Any]:
    runtime = case.get("runtime", {})
    query = str(case.get("request", ""))
    selected = set(runtime.get("selected_tools") or [])
    context = server.home_planner_context(
        query,
        [],
        fixture_attachments(case, attachments),
        str(runtime.get("vault_mode", "auto")),
        selected,
    )
    context = apply_runtime_fixture_constraints(context, runtime)
    started = time.perf_counter()
    try:
        result = interpret_and_resolve(query, context, endpoint=ENDPOINT, model=model, keep_alive=-1)
        plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
        semantic = result.get("semantic") if isinstance(result.get("semantic"), dict) else {}
        policy = result.get("policy") if isinstance(result.get("policy"), dict) else {}
        telemetry = result.get("telemetry") if isinstance(result.get("telemetry"), dict) else {}
        fallback = bool(result.get("fallback"))
        error = None
    except Exception as exc:  # the runner records failures rather than stopping the evaluation
        error = str(exc)
        available_tool_ids = [
            str(item.get("tool_id"))
            for item in context.get("available_tools", [])
            if isinstance(item, dict) and item.get("enabled") and item.get("tool_id")
        ]
        plan = fallback_plan(
            has_attachments=bool(context.get("attachments")),
            legacy_use_vault=server.home_query_requires_vault(query),
            vault_mode=str(runtime.get("vault_mode", "auto")),
            selected_tool_ids=selected,
            available_tool_ids=available_tool_ids,
            reason=error,
        )
        semantic = fallback_interpretation(
            has_attachments=bool(context.get("attachments")),
            legacy_use_vault=server.home_query_requires_vault(query),
            reason=error,
        )
        policy = {"policy_overrides": ["interpreter_fallback"], "capability_gaps": [], "controller_authoritative": True}
        telemetry = {}
        fallback = True
    elapsed = round((time.perf_counter() - started) * 1000)
    expected = case.get("expected", {})
    route_expected = final_route_expected(case)
    semantic_target = semantic_expected(case)
    semantic_result = semantic_score(semantic_target, semantic)
    controller = effective_controller(case, plan, policy)
    fallback_route_correct = bool(fallback and core_correct(route_expected, plan))
    final_correct = bool(not fallback and core_correct(route_expected, plan))
    return {
        "id": case.get("id"),
        "category": case.get("category"),
        "request": query,
        "expected": expected,
        "final_route_expected": route_expected,
        "semantic_expected": semantic_target,
        "semantic": semantic,
        "semantic_score": semantic_result,
        "plan": plan,
        "fallback_route_correct": fallback_route_correct,
        "core_correct": final_correct,
        "final_route_correct": final_correct,
        "tasks_present": len(plan.get("tasks", [])) >= int(expected.get("task_count_min", 0)) if isinstance(plan.get("tasks"), list) else False,
        "schema_valid": bool(semantic) and not fallback,
        "fallback": fallback,
        "controller": controller,
        "constraint_violation": not controller["vault_constraint_satisfied"],
        "wrapper_latency_ms": elapsed,
        "telemetry": telemetry,
        "error": error,
    }


def summary(rows: list[dict[str, Any]], model: str) -> dict[str, Any]:
    warm = [row["telemetry"].get("planner_latency_ms") for row in rows if isinstance(row.get("telemetry"), dict) and isinstance(row["telemetry"].get("planner_latency_ms"), (int, float)) and not row.get("telemetry", {}).get("model_load_occurred")]
    cold = [row["telemetry"].get("planner_latency_ms") for row in rows if isinstance(row.get("telemetry"), dict) and row.get("telemetry", {}).get("model_load_occurred")]
    scores = [row for row in rows if row.get("category")]
    semantic_scores = [row for row in scores if not row.get("fallback")]
    fields = ["intent", "needs_personal_history", "needs_current_information", "needs_attachment", "reasoning_complexity", "ambiguity"]
    field_accuracy = {}
    for field in fields:
        applicable = [row for row in semantic_scores if field in row.get("semantic_score", {}).get("scored_fields", [])]
        field_accuracy[field] = {
            "correct": sum(bool(row.get("semantic_score", {}).get("matches", {}).get(field)) for row in applicable),
            "total": len(applicable),
        }
    semantic_total = sum(item["total"] for item in field_accuracy.values())
    semantic_correct = sum(item["correct"] for item in field_accuracy.values())
    non_intent_total = semantic_total - field_accuracy["intent"]["total"]
    non_intent_correct = semantic_correct - field_accuracy["intent"]["correct"]
    return {
        "model": model,
        "cases": len(rows),
        "semantic_cases_scored": len(semantic_scores),
        "semantic_correct": semantic_correct,
        "intent_exact_accuracy": round(field_accuracy["intent"]["correct"] / field_accuracy["intent"]["total"], 4) if field_accuracy["intent"]["total"] else 0,
        "semantic_accuracy_without_intent": round(non_intent_correct / non_intent_total, 4) if non_intent_total else 0,
        "semantic_total": semantic_total,
        "semantic_accuracy": round(semantic_correct / semantic_total, 4) if semantic_total else 0,
        "semantic_field_accuracy": field_accuracy,
        "final_route_correct": sum(bool(row.get("final_route_correct")) for row in scores),
        "final_route_accuracy": round(sum(bool(row.get("final_route_correct")) for row in scores) / len(scores), 4) if scores else 0,
        "core_correct": sum(bool(row.get("core_correct")) for row in scores),
        "core_accuracy": round(sum(bool(row.get("core_correct")) for row in scores) / len(scores), 4) if scores else 0,
        "schema_valid": sum(bool(row.get("schema_valid")) for row in scores),
        "fallbacks": sum(bool(row.get("fallback")) for row in scores),
        "constraint_violations": sum(bool(row.get("constraint_violation")) for row in scores),
        "fallback_route_correct": sum(bool(row.get("fallback_route_correct")) for row in scores),
        "policy_overrides": sum(len(row.get("controller", {}).get("policy_overrides", [])) for row in scores),
        "capability_gaps": sum(len(row.get("controller", {}).get("capability_gaps", [])) for row in scores),
        "cold_latency_ms": cold,
        "warm_latency_ms": warm,
        "warm_p50_ms": round(statistics.median(warm), 1) if warm else None,
        "warm_p95_ms": round(sorted(warm)[max(0, min(len(warm) - 1, int(len(warm) * 0.95) - 1))], 1) if warm else None,
        "confidence_mean": round(statistics.mean([float(row["semantic"]["confidence"]) for row in scores if isinstance(row.get("semantic"), dict) and isinstance(row["semantic"].get("confidence"), (int, float))]), 4) if any(isinstance(row.get("semantic"), dict) and isinstance(row["semantic"].get("confidence"), (int, float)) for row in scores) else None,
        "high_confidence_incorrect": sum(bool(row.get("semantic", {}).get("confidence", 0) >= 0.9 and not row.get("semantic_score", {}).get("correct")) for row in scores),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--skip-unload", action="store_true")
    args = parser.parse_args()
    suite, cases = read_cases()
    attachments = suite["attachment"]
    if not args.skip_unload:
        unload(args.model)
    rows = [run_one(args.model, case, attachments) for case in cases]
    result = {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model": args.model,
        "suite": str(CASES_PATH.relative_to(PROJECT_ROOT)),
        "summary": summary(rows, args.model),
        "cases": rows,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = args.model.replace(":", "-").replace("/", "-")
    path = RESULTS_DIR / f"{safe_name}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
