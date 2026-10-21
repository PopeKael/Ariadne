"""Per-turn MCP bridge from Hermes to Ariadne-owned execution capabilities.

Tools are registered only when the Ariadne Home controller explicitly grants
them for this turn. The controller remains the authority for policy, memory,
model routing, research and Rust Host admission.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
    _MCP_V2 = False
except ModuleNotFoundError:
    from mcp.server.mcpserver import MCPServer as FastMCP
    _MCP_V2 = True


_INSTRUCTIONS = (
    "These tools are Ariadne capabilities for this one chat turn. Use only "
    "the explicitly exposed capabilities. Tool results are authoritative "
    "execution receipts; do not claim work that did not complete."
)
SERVER = FastMCP("ariadne-toolshed", instructions=_INSTRUCTIONS, version="1.0.0") if _MCP_V2 else FastMCP(
    "ariadne-toolshed", instructions=_INSTRUCTIONS
)
CONTROL_PLANE = Path(os.environ["ARIADNE_CONTROL_PLANE"]).resolve()
sys.path.insert(0, str(CONTROL_PLANE))
CHAT_ID = os.environ.get("ARIADNE_CHAT_ID", "")
TURN_ID = os.environ.get("ARIADNE_TURN_ID", "")
HOME_MODEL_ID = os.environ.get("ARIADNE_HOME_MODEL_ID", "")
HOME_PROVIDER_TYPE = os.environ.get("ARIADNE_HOME_PROVIDER_TYPE", "")
HOME_CONTEXT_TOKENS = int(os.environ.get("ARIADNE_HOME_CONTEXT_TOKENS", "0"))
try:
    ALLOWED = set(json.loads(os.environ.get("ARIADNE_AGENT_ALLOWED_TOOLS", "[]")))
except (TypeError, ValueError, json.JSONDecodeError):
    ALLOWED = set()
try:
    source_path = Path(os.environ.get("ARIADNE_WEB_SOURCES_PATH", ""))
    WEB_SOURCES = json.loads(source_path.read_text(encoding="utf-8")) if source_path.is_file() else []
except (OSError, TypeError, ValueError, json.JSONDecodeError):
    WEB_SOURCES = []


def _bounded_sources() -> list[dict[str, Any]]:
    if not isinstance(WEB_SOURCES, list):
        return []
    return [
        {key: str(item[key])[:12000] for key in ("source_id", "title", "url", "content", "snippet")
         if isinstance(item, dict) and isinstance(item.get(key), (str, int, float))}
        for item in WEB_SOURCES[:5]
        if isinstance(item, dict)
    ]


if "web_research" in ALLOWED:
    @SERVER.tool(
        name="get_web_research_evidence",
        description=(
            "Read the current turn's live sources already retrieved by Ariadne's "
            "authorized one-pass research policy. This tool does not initiate a second search."
        ),
        structured_output=True,
    )
    def get_web_research_evidence() -> dict[str, Any]:
        return {"ok": bool(_bounded_sources()), "sources": _bounded_sources(),
                "source_count": len(_bounded_sources()), "turn_id": TURN_ID}


if "coding_agent" in ALLOWED:
    @SERVER.tool(
        name="run_coding_task",
        description=(
            "Create, run and verify a small program using Ariadne's existing Qwen Code "
            "capability. Ariadne assigns a fresh child workspace under its configured "
            "coding root; do not request or choose another path."
        ),
        structured_output=True,
    )
    def run_coding_task(task: str) -> dict[str, Any]:
        if not CHAT_ID or not TURN_ID:
            raise RuntimeError("Ariadne chat and turn identity are required.")
        task = str(task or "").strip()
        if not task or len(task) > 16000:
            raise ValueError("Coding task must contain 1 to 16000 characters.")

        # Reuse Ariadne's established runner and registry-configured coding
        # model. The OpenAI-compatible Ollama API has no per-request context
        # field, so Qwen Code must address the registered context-qualified
        # coding model rather than the Home route's raw model ID.
        import server as ariadne
        from ariadne_config import configuration_path
        coding_route = ariadne.INFERENCE_REGISTRY.route("coding")
        if not coding_route or coding_route.provider_type != "ollama" or coding_route.location != "desktop":
            raise RuntimeError("Ariadne has no configured local coding-model route.")
        model = coding_route.model_id
        context_tokens = ariadne._context_for_model(
            model, ariadne.CODING_DEFAULT_CONTEXT_TOKENS
        )
        if coding_route.endpoint.rstrip("/") != ariadne.OLLAMA_URL.rstrip("/"):
            raise RuntimeError("The configured coding route does not use Ariadne's Rust-owned Ollama endpoint.")
        preserve_residency = bool(
            HOME_PROVIDER_TYPE == "ollama"
            and HOME_MODEL_ID == model
            and HOME_CONTEXT_TOKENS == context_tokens
            and coding_route.endpoint.rstrip("/") == ariadne.OLLAMA_URL.rstrip("/")
        )
        if preserve_residency:
            # Reuse Home's exact context-qualified Ollama tag. Creating a
            # second alias for the same underlying model/context makes Rust
            # Host correctly treat it as a fresh residency request.
            qwen_model = os.environ.get("ARIADNE_HOME_RUNTIME_MODEL_ID", "").strip()
            if not qwen_model:
                raise RuntimeError("The same-model coding turn has no leased Home runtime model identity.")
            temporary_profile = False
        else:
            qwen_model, temporary_profile = ariadne.create_context_model_profile(
                model, context_tokens, coding_route.endpoint
            )
        admission = lambda: ariadne._coding_resource_admission(
            qwen_model, context_tokens, allow_calibration=True,
            preserve_residency=preserve_residency,
            home_inference_lease_id=os.environ.get("ARIADNE_HOME_INFERENCE_LEASE_ID") or None,
        )
        admission_path = "rust-host-registered-coding-model-and-gpu-leases"
        try:
            result = ariadne.run_qwen_code_task(
                task,
                model=qwen_model,
                context_tokens=context_tokens,
                constraints=(
                    f"This task belongs to Ariadne chat {CHAT_ID}, turn {TURN_ID}.",
                    "Keep the program and verification artifacts inside the assigned child workspace.",
                    "Return actual commands, exit status and changed file paths as execution evidence.",
                ),
                resource_admission=admission,
            )
        finally:
            if temporary_profile:
                # The runner releases both Host leases before this block. Ask
                # Rust Host to unload the temporary model profile, then remove
                # the Ollama alias; preserve the alias if safe unload is denied.
                unload = ariadne.host_unload_model(qwen_model, ariadne.OLLAMA_URL)
                if not unload.get("ok"):
                    raise RuntimeError("Rust Host refused to unload the temporary coding profile; alias cleanup was skipped.")
                ariadne.delete_context_model_profile(qwen_model, coding_route.endpoint)
        task_requires_files = bool(re.search(
            r"\b(?:create|build|implement|modify|change|edit|fix|repair|add|write)\b",
            task, re.IGNORECASE,
        ))
        task_requires_verification = bool(re.search(
            r"\b(?:run|test|verify|execute|check)\b", task, re.IGNORECASE
        ))
        task_requires_test = bool(re.search(r"\b(?:test|pytest|unittest|vitest|jest)\b", task, re.IGNORECASE))
        changed_files = result.get("changed_files", [])
        tests = result.get("tests", [])
        executions = result.get("executions", [])
        success = (
            result.get("status") == "SUCCESS"
            and (not task_requires_files or bool(changed_files))
            and (not task_requires_verification or any(item.get("result") == "passed" for item in executions))
            and (not task_requires_test or any(item.get("result") == "passed" for item in tests))
        )
        errors = list(result.get("errors", []))[:10]
        if task_requires_files and not changed_files:
            errors.append("The requested file/code change was not recorded in the assigned workspace.")
        if task_requires_verification and not any(item.get("result") == "passed" for item in executions):
            errors.append("No shell execution with a recorded zero exit code was captured for verification.")
        if task_requires_test and not any(item.get("result") == "passed" for item in tests):
            errors.append("No passing test command was recorded for the requested tests.")
        return {
            "ok": success,
            "run_id": result.get("run_id"),
            "chat_id": CHAT_ID,
            "turn_id": TURN_ID,
            "status": result.get("status"),
            "workspace": result.get("workspace"),
            "model": result.get("model"),
            "configured_model": model,
            "registry_route": {
                "provider_id": coding_route.provider_id,
                "provider_type": coding_route.provider_type,
                "endpoint": coding_route.endpoint,
                "configuration_path": str(configuration_path()),
            },
            "context_tokens": result.get("context_tokens"),
            "host_admission": result.get("host_admission"),
            "host_admission_path": admission_path,
            "changed_files": changed_files,
            "tests": tests,
            "executions": executions,
            "exit_code": result.get("exit_code"),
            "tool_call_count": result.get("tool_call_count"),
            "child_diagnostics": result.get("child_diagnostics"),
            "summary": str(result.get("summary") or "")[:4000],
            "errors": errors[:12],
        }


if __name__ == "__main__":
    SERVER.run(transport="stdio")
