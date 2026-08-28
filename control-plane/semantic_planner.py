"""Small, local semantic planner for Ariadne Home.

The planner interprets a request; the Home controller remains responsible for
constraints, validation, and execution.  This module intentionally has no
knowledge of the Knowledge Vault or Home's controller branches.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any


PLANNER_MODEL = "qwen3.5:9b-q4_K_M"
PLANNER_KEEP_ALIVE: int | str = -1
PLANNER_CONTEXT_TOKENS = 4_096
PLANNER_OUTPUT_TOKENS = 256
PLANNER_TIMEOUT_SECONDS = 45.0
ALLOWED_PRIMARY_SOURCES = (
    "user_message",
    "conversation_history",
    "attachment",
    "vault",
    "model_prior_knowledge",
    "external",
)


def planner_schema(tool_ids: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """Return the strict schema sent to Ollama and used for local validation."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "intent": {"type": "string", "minLength": 1, "maxLength": 96},
            "primary_source": {"type": "string", "enum": list(ALLOWED_PRIMARY_SOURCES)},
            "tools": {
                "type": "array",
                "maxItems": 8,
                "items": {"type": "string", "enum": list(tool_ids)},
            },
            "use_vault": {"type": "boolean"},
            "needs_current_information": {"type": "boolean"},
            "use_heavy_model": {"type": "boolean"},
            "tasks": {
                "type": "array",
                "maxItems": 6,
                "items": {"type": "string", "minLength": 1, "maxLength": 220},
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [
            "intent", "primary_source", "tools", "use_vault",
            "needs_current_information", "use_heavy_model", "tasks", "confidence",
        ],
    }


def _post_json(url: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Planner Ollama endpoint is unavailable: {exc.reason}") from exc
    except (TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Planner Ollama request failed: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Planner Ollama returned a non-object response.")
    return value


def _get_json(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _duration_ms(value: object) -> int | None:
    return round(float(value) / 1_000_000) if isinstance(value, (int, float)) else None


def _extract_json(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        candidate = "\n".join(lines[1:-1] if lines and lines[-1].strip().startswith("```") else lines[1:]).strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Planner returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("Planner returned a JSON value other than an object.")
    return value


def validate_plan(value: object, available_tool_ids: list[str] | tuple[str, ...], *, has_attachments: bool) -> dict[str, Any]:
    """Validate planner output before any controller branch can use it."""
    if not isinstance(value, dict):
        raise ValueError("Planner output must be an object.")
    required = {
        "intent", "primary_source", "tools", "use_vault",
        "needs_current_information", "use_heavy_model", "tasks", "confidence",
    }
    if set(value) != required:
        unknown = sorted(set(value) - required)
        missing = sorted(required - set(value))
        raise ValueError(f"Planner schema mismatch; missing={missing}, unknown={unknown}.")
    if not isinstance(value["intent"], str) or not value["intent"].strip() or len(value["intent"]) > 96:
        raise ValueError("Planner intent is invalid.")
    if value["primary_source"] not in ALLOWED_PRIMARY_SOURCES:
        raise ValueError("Planner primary_source is invalid.")
    if value["primary_source"] == "attachment" and not has_attachments:
        raise ValueError("Planner selected an attachment when none is attached.")
    if not isinstance(value["tools"], list) or len(value["tools"]) > 8:
        raise ValueError("Planner tools must be a bounded list.")
    available = set(available_tool_ids)
    tools: list[str] = []
    for tool_id in value["tools"]:
        if not isinstance(tool_id, str) or tool_id not in available:
            raise ValueError(f"Planner selected unavailable tool: {tool_id!r}.")
        if tool_id not in tools:
            tools.append(tool_id)
    if "document-analysis" in tools and not has_attachments:
        raise ValueError("Planner selected document analysis when no attachment exists.")
    for field in ("use_vault", "needs_current_information", "use_heavy_model"):
        if type(value[field]) is not bool:
            raise ValueError(f"Planner field {field} must be boolean.")
    if not isinstance(value["tasks"], list) or len(value["tasks"]) > 6:
        raise ValueError("Planner tasks must be a bounded list.")
    tasks: list[str] = []
    for task in value["tasks"]:
        if not isinstance(task, str) or not task.strip() or len(task) > 220:
            raise ValueError("Planner task is invalid.")
        tasks.append(task.strip())
    confidence = value["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("Planner confidence must be a number from 0 to 1.")
    return {
        "intent": value["intent"].strip(),
        "primary_source": value["primary_source"],
        "tools": tools,
        "use_vault": value["use_vault"],
        "needs_current_information": value["needs_current_information"],
        "use_heavy_model": value["use_heavy_model"],
        "tasks": tasks,
        "confidence": round(float(confidence), 4),
    }


def fallback_plan(
    *,
    has_attachments: bool,
    legacy_use_vault: bool,
    vault_mode: str,
    selected_tool_ids: set[str],
    available_tool_ids: list[str] | tuple[str, ...],
    reason: str,
) -> dict[str, Any]:
    """Preserve the existing safe behaviour when semantic planning is unavailable."""
    use_vault = vault_mode == "always" or (vault_mode == "auto" and legacy_use_vault)
    can_use_documents = has_attachments and (
        not selected_tool_ids or "document-analysis" in selected_tool_ids
    )
    tools = ["document-analysis"] if can_use_documents and "document-analysis" in available_tool_ids else []
    primary_source = "attachment" if can_use_documents else "vault" if use_vault else "user_message"
    return {
        "intent": "legacy_safe_fallback",
        "primary_source": primary_source,
        "tools": tools,
        "use_vault": use_vault,
        "needs_current_information": False,
        "use_heavy_model": False,
        "tasks": [],
        "confidence": 0.0,
        "fallback_reason": reason,
    }


def _loaded_models(payload: dict[str, Any]) -> list[str]:
    models = payload.get("models", [])
    result: list[str] = []
    if isinstance(models, list):
        for item in models:
            if isinstance(item, dict):
                name = item.get("name") or item.get("model")
                if isinstance(name, str) and name and name not in result:
                    result.append(name)
    return result


def plan_request(
    query: str,
    runtime_context: dict[str, Any],
    *,
    endpoint: str,
    model: str = PLANNER_MODEL,
    keep_alive: int | str = PLANNER_KEEP_ALIVE,
    context_tokens: int = PLANNER_CONTEXT_TOKENS,
    output_tokens: int = PLANNER_OUTPUT_TOKENS,
    timeout: float = PLANNER_TIMEOUT_SECONDS,
    request_fn: Callable[[str, dict[str, Any], float], dict[str, Any]] | None = None,
    status_fn: Callable[[str, float], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ask the local planner for a validated plan and return diagnostics."""
    if not endpoint.startswith(("http://127.0.0.1", "http://localhost", "https://127.0.0.1", "https://localhost")):
        raise RuntimeError("Ariadne only permits a loopback planner Ollama endpoint.")
    available = [
        str(item.get("tool_id"))
        for item in runtime_context.get("available_tools", [])
        if isinstance(item, dict) and item.get("enabled") and item.get("tool_id")
    ]
    has_attachments = bool(runtime_context.get("attachments"))
    # A registered tool may still be inapplicable to this request. Do not expose
    # document analysis as a selectable schema value when no attachment exists.
    applicable_tools = available if has_attachments else []
    schema = planner_schema(applicable_tools)
    schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    system = (
        "You are Ariadne's semantic request planner. Decide the controller route; do not answer the user. "
        "Use only the supplied runtime facts. The date, timezone, attachments, available tools, and knowledge mode are authoritative. "
        "Controller precedence: mode 'always' requires the source/capability where operationally possible; mode 'never' forbids it and sets the related request field false; mode 'auto' lets you decide. "
        "The controller constraint overrides your semantic preference, so never contradict always/never in use_vault, needs_current_information, or tools. A forbidden Vault route must not use primary_source 'vault'. "
        "Choose the cheapest sufficient route. First identify the user's actual need; then decide personal/project history, live information, attachment evidence, and reasoning complexity. "
        "Use Vault only for personal or project history, prior discussions, decisions, plans, tests, notes, or explicit Vault requests; general facts are not Vault requests. "
        "Use current information only for genuinely live or time-sensitive answers such as latest news, current reporting, today's rate/weather, current support, or current office-holder; dates or technology names alone do not make a request current. A tomorrow/date calculation from the supplied runtime date is not external current information. Phrases such as 'still current' and 'what is the go with' a changing service require the current-information decision. If live information is needed, primary_source remains 'external' even when no external tool is available. "
        "An attachment is primary when the request refers to this, it, the document, or article; use document-analysis only when an attachment exists and that tool is in the schema. "
        "Heavy-model routing is only for genuine architecture, comparison, trade-offs, diagnosis, synthesis, robust planning, or complex attachment reasoning; ordinary facts, explanations, definitions, writing, ideas, and simple lists stay light. User style words such as simple or without fancy do not suppress heavy routing when the requested operation itself requires diagnosis or multi-constraint reasoning. "
        "Decide Vault, current-information, attachment, and heavy escalation independently; one may coexist with another when the request has multiple operations. primary_source is the controller's primary evidence route, not every capability used while answering: use user_message for direct ordinary or reasoning requests when no special source is required, vault for project history, external for live information, attachment for attached evidence, and do not select model_prior_knowledge as primary unless the user explicitly requests that route. "
        "tools must contain only needed available tools; with no attachment, tools must be empty. tasks are the bounded operations the controller should perform, not an answer. "
        "confidence is confidence in this entire routing plan: high only when source, mode, tools, and escalation are clear; use moderate or low confidence for ambiguity. Never use confidence to mean merely understanding the words. "
        "Return only the JSON object required by this schema. Schema: " + schema_text
    )
    user = json.dumps({"request": query, "runtime": runtime_context}, ensure_ascii=False, separators=(",", ":"))
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "format": schema,
        "keep_alive": keep_alive,
        "options": {
            "temperature": 0,
            "seed": 42,
            "num_ctx": max(1_024, int(context_tokens)),
            "num_predict": max(64, int(output_tokens)),
        },
    }
    if model.casefold().startswith("qwen3"):
        body["think"] = False
    measurement_started = time.perf_counter()
    loaded_before: list[str] = []
    if status_fn is not None:
        loaded_before = _loaded_models(status_fn(endpoint.rstrip("/") + "/api/ps", min(timeout, 3.0)))
    elif request_fn is None:
        loaded_before = _loaded_models(_get_json(endpoint.rstrip("/") + "/api/ps", min(timeout, 3.0)))
    started = time.perf_counter()
    response = (request_fn or _post_json)(endpoint.rstrip("/") + "/api/chat", body, timeout)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    message = response.get("message") if isinstance(response, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Planner returned no JSON content.")
    raw_plan = _extract_json(content)
    plan = validate_plan(raw_plan, applicable_tools, has_attachments=has_attachments)
    load_duration_ms = _duration_ms(response.get("load_duration"))
    loaded_models: list[str] = []
    if status_fn is not None:
        status = status_fn(endpoint.rstrip("/") + "/api/ps", min(timeout, 3.0))
        loaded_models = _loaded_models(status)
    elif request_fn is None:
        loaded_models = _loaded_models(_get_json(endpoint.rstrip("/") + "/api/ps", min(timeout, 3.0)))
    telemetry = {
        "planner_model": model,
        "keep_alive": keep_alive,
        "planning_duration_ms": elapsed_ms,
        "planner_latency_ms": round((time.perf_counter() - measurement_started) * 1000),
        "load_duration_ms": load_duration_ms,
        "model_load_occurred": model not in loaded_before and model in loaded_models,
        "prompt_tokens": response.get("prompt_eval_count"),
        "output_tokens": response.get("eval_count"),
        "residency_before": model in loaded_before,
        "residency_verified": model in loaded_models,
        "loaded_models": loaded_models,
    }
    return {"plan": plan, "telemetry": telemetry, "fallback": False}
