"""One-turn Hermes worker; all durable identity and memory stay in Ariadne."""
from __future__ import annotations

import io
import json
import os
import sys
import hashlib
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


JSONL_STREAM = sys.stdout
SERVER_NAME = "ariadne-knowledge-vault"
AGENT_SERVER_NAME = "ariadne-toolshed"
TOOL_NAMES = {
    "search_knowledge_chunks",
    "summarize_knowledge",
    "get_knowledge_chunk",
    "search_knowledge_vault",
    "get_knowledge_document",
}
TOOL_PREFIX = "mcp__ariadne_knowledge_vault__"
# Hermes normalizes MCP server-name hyphens to underscores in tool names.
AGENT_TOOL_PREFIX = "mcp__ariadne_toolshed__"
CONTEXT_SAFETY_MARGIN_RATIO = 0.05
AGENT_TOOL_NAMES = {
    "web_research": {"get_web_research_evidence"},
    "coding_agent": {"run_coding_task"},
}
_TOOL_RECEIPTS: list[dict[str, Any]] = []


def _emit(payload: dict[str, Any]) -> None:
    JSONL_STREAM.write(json.dumps(payload, ensure_ascii=False) + "\n")
    JSONL_STREAM.flush()


def _read_request() -> dict[str, Any]:
    value = json.load(sys.stdin)
    if not isinstance(value, dict):
        raise ValueError("Worker request must be a JSON object.")
    model = value.get("model")
    if (not isinstance(model, dict) or int(model.get("context_tokens") or 0) <= 0
            or int(model.get("output_tokens") or 0) <= 0):
        raise ValueError("Worker requires Ariadne's configured context and output budgets.")
    if not all(isinstance(model.get(key), str) and model[key].strip()
               for key in ("provider_id", "provider_type", "model_id", "api_key")):
        raise ValueError("Worker requires Ariadne's selected provider credentials and model route.")
    parsed = urlsplit(str(model.get("endpoint") or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Worker refuses an invalid model endpoint.")
    mcp = value.get("mcp")
    required = ("python", "script", "agent_script", "control_plane", "vault_root")
    if not isinstance(mcp, dict) or any(not Path(str(mcp.get(key) or "")).is_absolute() for key in required):
        raise ValueError("Worker requires Ariadne's absolute local MCP runtime paths.")
    return value


def _configure_profile(model: dict[str, Any], mcp: dict[str, str]) -> None:
    profile = Path(os.environ["HERMES_HOME"])
    config_path = profile / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    base_url = str(model["endpoint"]).rstrip("/")
    if model["provider_type"] == "ollama" and not base_url.casefold().endswith("/v1"):
        base_url += "/v1"
    capability = json.loads(os.environ.get("ARIADNE_WORKER_CAPABILITIES", "{}"))
    allowed = capability.get("allowed_tools", []) if isinstance(capability, dict) else []
    allowed_names = sorted({name for key in allowed if key in AGENT_TOOL_NAMES for name in AGENT_TOOL_NAMES[key]})
    agent_env = {
        "ARIADNE_CONTROL_PLANE": mcp["control_plane"],
        "ARIADNE_VAULT_ROOT": mcp["vault_root"],
        "ARIADNE_CHAT_ID": os.environ.get("ARIADNE_TURN_CHAT_ID", ""),
        "ARIADNE_TURN_ID": os.environ.get("ARIADNE_TURN_ID", ""),
        "ARIADNE_CODING_RUN_ID": os.environ.get("ARIADNE_CODING_RUN_ID", ""),
        "ARIADNE_HOME_MODEL_ID": model.get("registry_model_id") or model["model_id"],
        "ARIADNE_HOME_RUNTIME_MODEL_ID": model["model_id"],
        "ARIADNE_HOME_INFERENCE_LEASE_ID": model.get("home_inference_lease_id", ""),
        "ARIADNE_HOME_PROVIDER_TYPE": model["provider_type"],
        "ARIADNE_HOME_CONTEXT_TOKENS": str(model["context_tokens"]),
        "ARIADNE_AGENT_ALLOWED_TOOLS": json.dumps(allowed, separators=(",", ":")),
        "ARIADNE_WEB_SOURCES_PATH": os.environ.get("ARIADNE_WEB_SOURCES_PATH", ""),
    }
    # Preserve the controller's assigned coding root across the isolated
    # Hermes -> MCP subprocess boundary. Otherwise coding_agent falls back to
    # the configured production root during an isolated development run.
    if os.environ.get("ARIADNE_CODING_WORKSPACE_ROOT"):
        agent_env["ARIADNE_CODING_WORKSPACE_ROOT"] = os.environ["ARIADNE_CODING_WORKSPACE_ROOT"]
    # The capability bridge imports Ariadne's model registry. Without the
    # controller's explicit path it can silently load a different (usually
    # production) registry than the Home turn that granted the tool.
    if os.environ.get("ARIADNE_CONFIG_PATH"):
        agent_env["ARIADNE_CONFIG_PATH"] = os.environ["ARIADNE_CONFIG_PATH"]
    if os.environ.get("ARIADNE_QWEN_DEBUG") == "1":
        agent_env["ARIADNE_QWEN_DEBUG"] = "1"
    config = {
        "model": {
            "provider": "custom",
            "default": model["model_id"],
            "base_url": base_url,
            "api_key": model["api_key"],
            "context_length": model["context_tokens"],
            "ollama_num_ctx": model["context_tokens"],
        },
        "mcp_servers": {
            SERVER_NAME: {
                "enabled": True,
                "command": mcp["python"],
                "args": [mcp["script"]],
                "env": {
                    "ARIADNE_CONTROL_PLANE": mcp["control_plane"],
                    "ARIADNE_VAULT_ROOT": mcp["vault_root"],
                },
                "timeout": 30,
                "tools": {"include": sorted(TOOL_NAMES), "prompts": False, "resources": False},
                "elicitation": {"enabled": False},
                "sampling": {"enabled": False},
            },
            **({AGENT_SERVER_NAME: {
                "enabled": True,
                "command": mcp["python"],
                "args": [mcp["agent_script"]],
                "env": agent_env,
                "timeout": 900,
                "tools": {"include": allowed_names, "prompts": False, "resources": False},
                "elicitation": {"enabled": False},
                "sampling": {"enabled": False},
            }} if allowed_names else {}),
        },
        "memory": {"enabled": False, "user_profile_enabled": False},
        "tools": {"tool_search": {"enabled": "off"}},
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")


def _extract_tool_receipts(messages: object) -> list[dict[str, str]]:
    if not isinstance(messages, list):
        return []
    receipts: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        tool_calls = message.get("tool_calls")
        for call in tool_calls if isinstance(tool_calls, list) else []:
            function = call.get("function") if isinstance(call, dict) else None
            name = function.get("name") if isinstance(function, dict) else None
            if isinstance(name, str) and name.startswith(TOOL_PREFIX):
                tool = name[len(TOOL_PREFIX):]
                if tool in TOOL_NAMES:
                    receipts.append({"tool": tool})
    return receipts


def _tool_callback(status: str, agent_ref: dict[str, Any] | None = None):
    def coding_payload(raw: object) -> dict[str, Any] | None:
        """Unwrap the exact JSON envelope returned by Hermes' MCP adapter.

        Its native handler preserves both text and structuredContent. FastMCP
        may dual-emit the structured payload as JSON text; Hermes then returns
        that text inside the adapter's outer ``result`` field. Decode that
        documented wire shape without treating a prose summary as evidence.
        """
        value: object = raw
        for _ in range(4):
            if isinstance(value, dict):
                structured = value.get("structuredContent", value.get("structured_content"))
                if isinstance(structured, dict):
                    value = structured
                    continue
                nested = value.get("result")
                if isinstance(nested, dict):
                    value = nested
                    continue
                if isinstance(nested, str):
                    try:
                        value = json.loads(nested)
                        continue
                    except (ValueError, json.JSONDecodeError):
                        return None
                return value
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                    continue
                except (ValueError, json.JSONDecodeError):
                    return None
            # MCP SDK versions expose either snake_case/camelCase attributes
            # or a Pydantic model. Normalize only structured fields.
            structured = getattr(value, "structuredContent", None)
            if structured is None:
                structured = getattr(value, "structured_content", None)
            if isinstance(structured, dict):
                value = structured
                continue
            dump = getattr(value, "model_dump", None)
            if callable(dump):
                try:
                    value = dump(by_alias=True)
                    continue
                except (TypeError, ValueError):
                    return None
            return None
        return value if isinstance(value, dict) else None

    def emit_tool(_call_id: str, name: str, *_args: object) -> None:
        if not isinstance(name, str):
            return
        callback_agent = agent_ref.get("agent") if isinstance(agent_ref, dict) else None
        if (status == "started" and callback_agent is not None
                and getattr(callback_agent, "_required_tool_for_next_api_call", None) == name):
            callback_agent._required_tool_for_next_api_call = None
        if name.startswith(TOOL_PREFIX) and name[len(TOOL_PREFIX):] in TOOL_NAMES:
            tool = name[len(TOOL_PREFIX):]
        elif name.startswith(AGENT_TOOL_PREFIX):
            tool = name[len(AGENT_TOOL_PREFIX):]
        else:
            return
        receipt: dict[str, Any] = {"tool": tool, "server": SERVER_NAME if name.startswith(TOOL_PREFIX) else AGENT_SERVER_NAME,
                                   "status": status}
        if status == "completed" and _args:
            raw = _args[-1]
            try:
                encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            except (TypeError, ValueError):
                encoded = str(raw).encode("utf-8", errors="replace")
            receipt["result_sha256"] = hashlib.sha256(encoded).hexdigest()
            receipt["result_bytes"] = len(encoded)
            parsed = coding_payload(raw) if tool == "run_coding_task" else None
            if isinstance(parsed, dict) and tool == "run_coding_task":
                receipt["execution"] = {
                    key: parsed.get(key)
                    for key in ("status", "workspace", "model", "configured_model", "context_tokens",
                                "host_admission", "host_admission_path", "registry_route",
                                "run_id", "chat_id", "turn_id", "changed_files", "tests", "executions",
                                "exit_code", "tool_call_count", "child_diagnostics")
                    if key in parsed
                }
        if status == "started" or status == "completed":
            _TOOL_RECEIPTS.append(receipt)
            _emit({"type": "tool", "status": status, "name": name})
    return emit_tool


def _delta_callback(delta: object) -> None:
    if isinstance(delta, str) and delta:
        _emit({"type": "delta", "text": delta})
    elif isinstance(delta, dict):
        text = delta.get("text") or delta.get("content")
        if isinstance(text, str) and text:
            _emit({"type": "delta", "text": text})


def _coding_task_prelude(task: str):
    """Run a controller-authorized coding capability through Hermes' native tool round."""
    yield (
        "I am using Ariadne's approved coding capability to make and verify this change.",
        f"{AGENT_TOOL_PREFIX}run_coding_task",
        {"task": str(task)},
    )


def _budget_conversation(agent: Any, system_message: str, user_message: str,
                         history: list[dict[str, str]], context_tokens: int,
                         output_tokens: int) -> tuple[list[dict[str, str]], dict[str, int]]:
    """Fit the full system/tool/request estimate inside Ariadne's input budget.

    Oldest completed user/assistant exchanges are pruned first. Ariadne retains
    the full durable transcript, so this only bounds the one-turn Hermes prompt.
    Hermes' ContextCompressor handles additional tool-round growth during the turn.
    """
    from agent.model_metadata import _estimate_tools_tokens_rough, estimate_messages_tokens_rough

    compressor = getattr(agent, "context_compressor", None)
    runtime_window = int(getattr(compressor, "context_length", 0) or context_tokens)
    usable_window = min(runtime_window, context_tokens)
    if usable_window <= output_tokens:
        raise ValueError("Ariadne's context window leaves no input budget after output reservation.")
    safety_margin = max(256, int(usable_window * CONTEXT_SAFETY_MARGIN_RATIO))
    input_budget = usable_window - output_tokens - safety_margin
    threshold = int(getattr(compressor, "threshold_tokens", input_budget) or input_budget)
    safe_budget = min(input_budget, threshold)
    tool_tokens = _estimate_tools_tokens_rough(getattr(agent, "tools", []) or [])
    effective_system = agent._build_system_prompt(system_message)

    def estimate(items: list[dict[str, str]]) -> int:
        messages = [{"role": "system", "content": effective_system}, *items,
                    {"role": "user", "content": user_message}]
        return estimate_messages_tokens_rough(messages) + tool_tokens

    bounded_history = list(history)
    estimated = estimate(bounded_history)
    removed_messages = 0
    while estimated > safe_budget and bounded_history:
        # Ariadne's ChatStore supplies completed user/assistant messages in pairs.
        # Remove one oldest exchange at a time and preserve the current request.
        if (len(bounded_history) >= 2 and bounded_history[0].get("role") == "user"
                and bounded_history[1].get("role") == "assistant"):
            del bounded_history[:2]
            removed_messages += 2
        else:
            bounded_history.pop(0)
            removed_messages += 1
        estimated = estimate(bounded_history)
    if estimated > safe_budget:
        raise ValueError(
            f"Current Ariadne prompt and tool schemas need about {estimated:,} input tokens; "
            f"the safe budget is {safe_budget:,} within the configured {usable_window:,}-token window."
        )
    return bounded_history, {
        "window_tokens": usable_window,
        "output_reserve_tokens": output_tokens,
        "input_budget_tokens": input_budget,
        "safe_prompt_budget_tokens": safe_budget,
        "estimated_request_tokens": estimated,
        "tool_schema_tokens": tool_tokens,
        "pruned_history_messages": removed_messages,
    }


def main() -> int:
    _TOOL_RECEIPTS.clear()
    try:
        request = _read_request()
        model = request["model"]
        mcp = request["mcp"]
        repo = Path(str(request.get("repo", ""))).resolve()
        if not (repo / "run_agent.py").is_file():
            raise ValueError("Pinned Hermes source checkout is unavailable.")
        if not Path(mcp["script"]).is_file() or not Path(mcp["python"]).is_file():
            raise ValueError("Ariadne's existing KnowledgeVault MCP runtime is unavailable.")
        sys.path.insert(0, str(repo))

        capability = request.get("capabilities") if isinstance(request.get("capabilities"), dict) else {}
        allowed = capability.get("allowed_tools", [])
        if not isinstance(allowed, list) or any(item not in AGENT_TOOL_NAMES for item in allowed):
            raise ValueError("Ariadne supplied an unsupported agent capability grant.")
        if allowed and not Path(mcp["agent_script"]).is_file():
            raise ValueError("Ariadne's Toolshed MCP bridge is unavailable.")
        os.environ["ARIADNE_WORKER_CAPABILITIES"] = json.dumps({"allowed_tools": allowed})
        os.environ["ARIADNE_TURN_CHAT_ID"] = str(request.get("chat_id") or "")
        os.environ["ARIADNE_TURN_ID"] = str(request.get("turn_id") or "")
        source_path = Path(os.environ["HERMES_HOME"]) / "turn-web-sources.json"
        source_path.write_text(json.dumps(capability.get("web_sources", []), ensure_ascii=False), encoding="utf-8")
        os.environ["ARIADNE_WEB_SOURCES_PATH"] = str(source_path)
        _configure_profile(model, mcp)
        # AIAgent snapshots its tool registry during construction.  MCP discovery is
        # intentionally an explicit lifecycle step in Hermes, so connect the single
        # Ariadne server first and only then construct the one-turn agent.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            from tools.mcp_tool_discovery import discover_mcp_tools
            discovered = discover_mcp_tools(allowed_mcp_names=[SERVER_NAME, *([AGENT_SERVER_NAME] if allowed else [])])
        if not discovered:
            raise RuntimeError("Hermes could not discover Ariadne's configured Vault MCP tools.")
        from run_agent import AIAgent

        agent_ref: dict[str, Any] = {}
        agent = AIAgent(
            provider="custom",
            model=model["model_id"],
            base_url=(str(model["endpoint"]).rstrip("/") + ("" if str(model["endpoint"]).rstrip("/").casefold().endswith("/v1") else "/v1"))
            if model["provider_type"] == "ollama" else str(model["endpoint"]).rstrip("/"),
            api_key=model["api_key"],
            enabled_toolsets=[SERVER_NAME, *([AGENT_SERVER_NAME] if allowed else [])],
            max_iterations=int(request["max_iterations"]),
            max_tokens=int(model["output_tokens"]),
            context_budget_tokens=int(model["context_tokens"]),
            run_budget_seconds=ENGINE_TIMEOUT_SECONDS,
            quiet_mode=True,
            save_trajectories=False,
            skip_context_files=True,
            skip_memory=True,
            skip_background_review=True,
            load_soul_identity=False,
            tool_start_callback=_tool_callback("started", agent_ref),
            tool_complete_callback=_tool_callback("completed", agent_ref),
        )
        agent_ref["agent"] = agent
        expected = {f"{TOOL_PREFIX}{name}" for name in TOOL_NAMES}
        expected.update(f"{AGENT_TOOL_PREFIX}{name}" for key in allowed for name in AGENT_TOOL_NAMES[key])
        actual = set()
        for item in getattr(agent, "tools", []):
            function = item.get("function") if isinstance(item, dict) else None
            name = function.get("name") if isinstance(function, dict) else None
            if isinstance(name, str):
                actual.add(name)
        if actual != expected:
            from tools.registry import registry
            registered = set(registry.get_tool_names_for_toolset(f"mcp-{SERVER_NAME}"))
            if allowed:
                registered.update(registry.get_tool_names_for_toolset(f"mcp-{AGENT_SERVER_NAME}"))
            raise RuntimeError(
                "Hermes MCP tools differ from Ariadne's per-turn capability allowlist "
                f"(agent={','.join(sorted(actual)) or 'none'}; "
                f"mcp={','.join(sorted(registered)) or 'none'}; "
                f"discovered={','.join(sorted(discovered)) or 'none'})."
            )
        from tools.registry import registry
        registered = set(registry.get_tool_names_for_toolset(f"mcp-{SERVER_NAME}"))
        if allowed:
            registered.update(registry.get_tool_names_for_toolset(f"mcp-{AGENT_SERVER_NAME}"))
        if registered != expected:
            raise RuntimeError("Hermes registered tools differ from Ariadne's per-turn capability allowlist.")

        bounded_history, context_budget = _budget_conversation(
            agent, request["system_message"], request["user_message"],
            request.get("conversation_history") or [],
            int(model["context_tokens"]), int(model["output_tokens"]),
        )
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = agent.run_conversation(
                user_message=request["user_message"],
                system_message=request["system_message"],
                conversation_history=bounded_history,
                task_id=str(request.get("turn_id") or "ariadne-turn"),
                stream_callback=_delta_callback,
                prelude=_coding_task_prelude(request["user_message"]) if "coding_agent" in allowed else None,
            )
        if "coding_agent" in allowed:
            completed_coding = next((
                item for item in reversed(_TOOL_RECEIPTS)
                if item.get("tool") == "run_coding_task" and item.get("status") == "completed"
            ), None)
            execution = completed_coding.get("execution") if isinstance(completed_coding, dict) else None
            if not isinstance(execution, dict) or execution.get("status") != "SUCCESS":
                raise RuntimeError("Ariadne's granted coding capability did not return verified SUCCESS evidence.")
        answer = result.get("final_response") if isinstance(result, dict) else None
        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError("The local agent engine returned no final response.")
        _emit({"type": "result", "ok": True, "answer": answer,
               "tool_receipts": list(_TOOL_RECEIPTS), "context_budget": context_budget})
        return 0
    except Exception as exc:
        _emit({"type": "result", "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:400]}",
               "tool_receipts": list(_TOOL_RECEIPTS)})
        return 1
    finally:
        try:
            from tools.mcp_tool_lifecycle import shutdown_mcp_servers
            shutdown_mcp_servers(timeout=10)
        except Exception:
            # The disposable worker process is the outer cleanup boundary; a
            # failed best-effort SDK shutdown cannot alter Ariadne's MCP service.
            pass


ENGINE_TIMEOUT_SECONDS = 300

if __name__ == "__main__":
    raise SystemExit(main())
