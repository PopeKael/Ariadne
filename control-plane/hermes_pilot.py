"""Ariadne's internal, local-first agent orchestration adapter."""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

ENGINE_ITERATION_LIMIT = 8
ENGINE_TIMEOUT_SECONDS = 300
VAULT_SERVER_NAME = "ariadne-knowledge-vault"

DEFAULT_HERMES_REPO = Path(r"F:\AI\Ariadne-Agent-Worker\extensions\hermes-agent-pilot")


class _WindowsProcessTreeJob:
    """Own the worker subprocess tree and terminate leftovers on scope exit."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self._ctypes = ctypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self._kernel32.TerminateJobObject.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._handle = self._kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        information = ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not self._kernel32.SetInformationJobObject(
            self._handle, 9, ctypes.byref(information), ctypes.sizeof(information),
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process: subprocess.Popen[str]) -> None:
        from ctypes import wintypes

        process_handle = wintypes.HANDLE(int(process._handle))  # noqa: SLF001 - Windows Popen handle
        if not self._kernel32.AssignProcessToJobObject(self._handle, process_handle):
            raise self._ctypes.WinError(self._ctypes.get_last_error())

    def terminate(self, exit_code: int = 1) -> None:
        if self._handle and not self._kernel32.TerminateJobObject(self._handle, exit_code):
            raise self._ctypes.WinError(self._ctypes.get_last_error())

    def close(self) -> None:
        if getattr(self, "_handle", None):
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def enabled() -> bool:
    """Keep the unverified engine opt-in until its configured model is supported."""
    value = os.environ.get("ARIADNE_HERMES_ENGINE", "legacy").strip().casefold()
    return value in {"1", "true", "yes", "on", "enabled", "hermes"}


def _worker_paths() -> tuple[Path, Path]:
    configured_repo = os.environ.get("ARIADNE_HERMES_AGENT_REPO", "").strip()
    repo = Path(configured_repo).expanduser() if configured_repo else DEFAULT_HERMES_REPO
    if not repo.is_absolute() or not (repo / "run_agent.py").is_file():
        raise RuntimeError("The isolated Hermes source checkout is unavailable.")

    configured_python = os.environ.get("ARIADNE_HERMES_PYTHON", "").strip()
    if configured_python:
        python = Path(configured_python).expanduser()
    else:
        profile_root = repo.parent / "hermes-agent-pilot-profile"
        facts_files = sorted(
            (profile_root / "installs").glob("*/facts.json"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        python = Path()
        for facts_path in facts_files:
            try:
                facts = json.loads(facts_path.read_text(encoding="utf-8"))
                environment = facts.get("packages", {}).get("venv", {}).get("environment")
            except (OSError, ValueError, AttributeError):
                continue
            if isinstance(environment, str) and environment:
                candidate = Path(environment) / "Scripts" / "python.exe"
                if candidate.is_file():
                    python = candidate
                    break
    if not python.is_absolute() or not python.is_file():
        raise RuntimeError("The isolated Hermes Python runtime is unavailable.")
    return repo.resolve(), python.resolve()


def _provider(registry: Any, model_id: str) -> Any:
    provider = registry.route("home_chat")
    if not provider or not provider.enabled or provider.model_id != model_id:
        raise RuntimeError("Ariadne's configured Home chat route is unavailable.")
    if "chat" not in provider.capabilities:
        raise RuntimeError("Ariadne's selected Home provider does not advertise chat capability.")
    endpoint = urlsplit(provider.endpoint)
    if endpoint.scheme not in {"http", "https"} or not endpoint.netloc:
        raise RuntimeError("Ariadne's selected Home provider endpoint is invalid.")
    return provider


def _messages(messages: object) -> tuple[str, str, list[dict[str, str]]]:
    if not isinstance(messages, list) or not messages:
        raise ValueError("The agent engine requires Ariadne's prepared messages.")
    normalized: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant"}:
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            normalized.append({"role": message["role"], "content": content})
    system = next((item["content"] for item in normalized if item["role"] == "system"), "")
    current_index = next(
        (index for index in range(len(normalized) - 1, -1, -1)
         if normalized[index]["role"] == "user"),
        None,
    )
    if not system or current_index is None:
        raise ValueError("Ariadne's prepared messages need system identity and the current user turn.")
    history = [item for item in normalized if item["role"] != "system"]
    current_index = next(index for index in range(len(history) - 1, -1, -1) if history[index]["role"] == "user")
    user_message = history.pop(current_index)["content"]
    return system, user_message, history


def _stream_worker(
    command: list[str], *, payload: dict[str, Any], env: dict[str, str], cwd: Path,
    on_delta: Callable[[str], None] | None,
    on_tool_event: Callable[[str, str], None] | None,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    tree_job = _WindowsProcessTreeJob() if os.name == "nt" else None
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1, cwd=str(cwd), env=env,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)
                           | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)),
            start_new_session=os.name != "nt",
        )
    except BaseException:
        if tree_job is not None:
            tree_job.close()
        raise
    if tree_job is not None:
        try:
            # The worker blocks on its request pipe before it can start Hermes
            # or MCP children; assign it before sending that request.
            tree_job.assign(process)
        except BaseException:
            process.kill()
            process.wait(timeout=5.0)
            tree_job.close()
            raise
    lines: queue.Queue[str | None] = queue.Queue()
    worker_diagnostics: dict[str, Any] = {
        "run_id": payload.get("agent_run_id"),
        "turn_id": payload.get("turn_id"),
        "pid": process.pid,
        "parent_pid": os.getpid(),
        "started_at": started_at,
        "process_tree_owner": "windows-job-object" if tree_job is not None else "process-group",
    }

    def read_lines() -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                lines.put(line)
        finally:
            lines.put(None)

    reader = threading.Thread(target=read_lines, name="AriadneHermesEvents", daemon=True)
    reader.start()
    try:
        assert process.stdin is not None
        process.stdin.write(json.dumps(payload, ensure_ascii=False))
        process.stdin.close()
        deadline = time.monotonic() + ENGINE_TIMEOUT_SECONDS
        result: dict[str, Any] | None = None
        stream_closed = False
        process_exit_observed_at: float | None = None
        orphan_tree_terminated = False
        while not stream_closed:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("The local agent turn exceeded its time limit.")
            try:
                line = lines.get(timeout=min(0.25, remaining))
            except queue.Empty:
                if process.poll() is not None:
                    if not reader.is_alive():
                        stream_closed = True
                    elif process_exit_observed_at is None:
                        process_exit_observed_at = time.monotonic()
                    elif (not orphan_tree_terminated
                          and time.monotonic() - process_exit_observed_at >= 1.0):
                        # A completed worker must not leave MCP/CLI children
                        # holding its stdout pipe or issuing late requests.
                        # Give buffered result events time to drain, then close
                        # the worker-owned process tree and read the EOF.
                        if tree_job is not None:
                            tree_job.terminate()
                            method = "windows-job-object-orphan-cleanup"
                        else:
                            os.killpg(process.pid, signal.SIGKILL)
                            method = "process-group-orphan-cleanup"
                        worker_diagnostics["orphan_child_cleanup"] = {
                            "method": method,
                            "after_root_exit_seconds": round(time.monotonic() - process_exit_observed_at, 3),
                        }
                        orphan_tree_terminated = True
                continue
            if line is None:
                stream_closed = True
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError("The local agent worker emitted invalid event data.") from exc
            if not isinstance(event, dict):
                continue
            if event.get("type") == "delta":
                text = event.get("text")
                if isinstance(text, str) and text and on_delta is not None:
                    on_delta(text)
            elif event.get("type") == "tool":
                status, name = event.get("status"), event.get("name")
                if (status in {"started", "completed"} and isinstance(name, str)
                        and on_tool_event is not None):
                    on_tool_event(status, name)
            elif event.get("type") == "result":
                result = event

        return_code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        if tree_job is not None and "tree_cleanup" not in worker_diagnostics:
            try:
                # KILL_ON_JOB_CLOSE is a final guard; terminate any remaining
                # descendants explicitly after the worker's result is drained.
                tree_job.terminate()
                worker_diagnostics["post_exit_tree_cleanup"] = {
                    "method": "windows-job-object-terminate-after-result",
                    "verified": True,
                }
            except OSError as cleanup_error:
                worker_diagnostics["post_exit_tree_cleanup"] = {
                    "verified": False,
                    "error": str(cleanup_error)[:300],
                }
                raise RuntimeError("Hermes worker exited but its subprocess-tree cleanup could not be verified.") from cleanup_error
        worker_diagnostics.update({
            "exit_code": return_code,
            "exited_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        })
        if return_code != 0 or result is None or result.get("ok") is not True:
            message = result.get("error") if isinstance(result, dict) else None
            detail = str(message or f"worker exited with status {return_code}")
            if isinstance(result, dict):
                worker_diagnostics["tool_receipts"] = result.get("tool_receipts", [])
                worker_diagnostics["worker_error"] = str(result.get("error") or "")[:500]
            raise RuntimeError(detail[:500])
        result["_hermes_worker_process"] = worker_diagnostics
        return result
    except BaseException as exc:
        if process.poll() is None:
            cleanup: dict[str, Any] = {"method": "process-kill", "requested": True}
            try:
                if tree_job is not None:
                    tree_job.terminate()
                    cleanup = {"method": "windows-job-object-terminate"}
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                    cleanup = {"method": "process-group-kill", "signal": "SIGKILL"}
            except (OSError, subprocess.SubprocessError) as cleanup_error:
                cleanup["error"] = str(cleanup_error)[:300]
                try:
                    process.kill()
                    cleanup["fallback"] = "worker-process-kill"
                except OSError as fallback_error:
                    cleanup["fallback_error"] = str(fallback_error)[:300]
            worker_diagnostics["tree_cleanup"] = cleanup
        try:
            return_code = process.wait(timeout=5.0)
            if "tree_cleanup" in worker_diagnostics:
                worker_diagnostics["tree_cleanup"]["worker_exit_verified"] = True
            elif tree_job is not None:
                worker_diagnostics["tree_cleanup"] = {
                    "method": "windows-job-object-close-on-exit",
                    "worker_exit_verified": True,
                }
        except subprocess.TimeoutExpired:
            cleanup = worker_diagnostics.setdefault("tree_cleanup", {})
            cleanup["worker_wait_timeout_seconds"] = 5
            try:
                # taskkill /T can report success before Windows has signalled
                # the root process. Bound the wait, then terminate the exact
                # Popen handle and verify exit rather than hanging Home forever.
                process.kill()
                return_code = process.wait(timeout=5.0)
                cleanup["root_termination"] = "Popen.kill"
                cleanup["worker_exit_verified"] = True
            except subprocess.TimeoutExpired:
                return_code = None
                cleanup["worker_exit_verified"] = False
                cleanup["worker_exit_error"] = "worker remained active after bounded process-tree and root termination attempts"
            except OSError as cleanup_error:
                return_code = process.poll()
                cleanup["worker_exit_verified"] = return_code is not None
                cleanup["worker_exit_error"] = str(cleanup_error)[:300]
        worker_diagnostics.update({
            "exit_code": return_code,
            "exited_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "failure_type": type(exc).__name__,
            "failure": str(exc)[:500],
        })
        try:
            setattr(exc, "hermes_worker_process", worker_diagnostics)
        except Exception:
            pass
        raise
    finally:
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        if tree_job is not None:
            tree_job.close()


def run(
    messages: object, chat_id: str, turn_id: str, registry: Any, model_id: str, *,
    runtime_model_id: str | None = None,
    context_tokens: int, output_tokens: int, api_key: str,
    mcp_python: str, mcp_script: str, control_plane: str, vault_root: str,
    home_inference_lease_id: str = "",
    allowed_tools: list[str] | None = None,
    web_sources: list[dict[str, Any]] | None = None,
    on_delta: Callable[[str], None] | None = None,
    on_tool_event: Callable[[str, str], None] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Run one Ariadne turn through Hermes with local Qwen and Ariadne's read-only MCP."""
    if not enabled():
        raise RuntimeError("The Hermes agent engine is disabled by its rollback switch.")
    if not isinstance(chat_id, str) or not chat_id.strip() or not isinstance(turn_id, str) or not turn_id.strip():
        raise ValueError("The agent engine requires Ariadne chat and turn identifiers.")
    repo, python = _worker_paths()
    provider = _provider(registry, model_id)
    if runtime_model_id and runtime_model_id != model_id:
        # Keep provider/endpoint selection anchored to Ariadne's registry. The
        # temporary runtime tag only conveys Ollama context on the OpenAI wire.
        provider = replace(provider, model_id=runtime_model_id)
    if context_tokens <= 0 or output_tokens <= 0:
        raise ValueError("Ariadne's selected context and output budgets must be positive.")
    system, user_message, history = _messages(messages)
    for required_path in (mcp_python, mcp_script, control_plane, vault_root):
        if not Path(required_path).is_absolute():
            raise ValueError("Ariadne's MCP runtime paths must be absolute.")

    agent_run_id = uuid.uuid4().hex
    payload = {
        "repo": str(repo),
        "model": {
            "provider_id": provider.provider_id,
            "provider_type": provider.provider_type,
            "model_id": provider.model_id,
            # Keep registry identity distinct from the context-qualified
            # runtime alias used on the model API. Toolshed uses this value
            # to decide whether a coding call is a same-model nested lease.
            "registry_model_id": model_id,
            "home_inference_lease_id": home_inference_lease_id,
            "endpoint": provider.endpoint,
            "context_tokens": context_tokens,
            "output_tokens": output_tokens,
            "api_key": api_key,
        },
        "chat_id": chat_id,
        "turn_id": turn_id,
        "agent_run_id": agent_run_id,
        "system_message": system,
        "user_message": user_message,
        "conversation_history": history,
        "mcp": {
            "python": mcp_python,
            "script": mcp_script,
            "agent_script": str(Path(control_plane) / "ariadne_agent_tools.py"),
            "control_plane": control_plane,
            "vault_root": vault_root,
        },
        "capabilities": {
            "allowed_tools": sorted(set(allowed_tools or [])),
            "web_sources": web_sources if isinstance(web_sources, list) else [],
        },
        "max_iterations": ENGINE_ITERATION_LIMIT,
    }
    worker = Path(__file__).resolve().with_name("hermes_pilot_worker.py")
    with tempfile.TemporaryDirectory(prefix="ariadne-agent-turn-") as profile:
        inherited_names = {
            "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE",
            "APPDATA", "LOCALAPPDATA", "USERNAME", "USERDOMAIN", "HOMEDRIVE", "HOMEPATH",
            "PROCESSOR_ARCHITECTURE", "ARIADNE_CODING_WORKSPACE_ROOT",
            # The per-turn Toolshed process imports Ariadne's live registry
            # and storage configuration. Keep it on the same installation
            # configuration as Home; otherwise it silently falls back to the
            # production/default registry while retaining Home's model lease.
            "ARIADNE_CONFIG_PATH", "ARIADNE_VAULT_ROOT", "ARIADNE_QWEN_DEBUG",
        }
        env = {key: value for key, value in os.environ.items() if key.upper() in inherited_names}
        env["HERMES_HOME"] = profile
        env["HERMES_RUNTIME_DIR"] = str(Path(profile) / "runtime")
        env["ARIADNE_HERMES_RUN_ID"] = agent_run_id
        result = _stream_worker(
            [str(python), str(worker)], payload=payload, env=env, cwd=repo,
            on_delta=on_delta, on_tool_event=on_tool_event,
        )

    answer = result.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError("The local agent engine returned no final answer.")
    receipts = result.get("tool_receipts")
    if not isinstance(receipts, list):
        receipts = []
    return answer.strip(), {
        "engine": "hermes",
        "agent_run_id": agent_run_id,
        "turn_id": turn_id,
        "worker_process": result.get("_hermes_worker_process"),
        "provider_id": provider.provider_id,
        "provider_type": provider.provider_type,
        "model": provider.model_id,
        "configured_model": model_id,
        "context_tokens": context_tokens,
        "max_iterations": ENGINE_ITERATION_LIMIT,
        "tool_receipts": receipts,
        "tool_call_count": len(receipts),
        "context_budget": result.get("context_budget", {}),
        "execution_path": ["Ariadne Home controller", "Hermes AIAgent Python API", "Ariadne MCP capability bridge"],
        "legacy_fallback": None,
        "memory_owner": "ariadne-chat-store",
        "hermes_memory_disabled": True,
        "profile_disposed": True,
    }


__all__ = ["enabled", "run", "ENGINE_ITERATION_LIMIT"]
