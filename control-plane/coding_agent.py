"""Ariadne's reusable bridge to the installed, unmodified Qwen Code CLI."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import queue
import shutil
import subprocess
import threading
import time
import uuid
from typing import Any, Callable

from ollama_runtime import DEFAULT_ENDPOINT, inference_lease


WORKER_ROOT = Path(r"F:\AI\Ariadne-Agent-Worker")
LAUNCHER = WORKER_ROOT / "Start-AriadneQwenCode.ps1"
WORKSPACE_ROOT = Path(os.environ.get(
    "ARIADNE_CODING_WORKSPACE_ROOT", r"F:\AriadneLab\agent-workspaces"
)).resolve()
EVIDENCE_ROOT = WORKER_ROOT / "logs" / "qwen-code" / "ariadne-runs"
RESULT_ROOT = WORKSPACE_ROOT / ".ariadne-results"
DEFAULT_MODEL = "ariadne-qwen-code:9b-32k"
DEFAULT_CONTEXT_TOKENS = 32_768
MAX_CONTEXT_TOKENS = 1_048_576
PROCESS_POLL_INTERVAL_SECONDS = 1
CLIENT_HEARTBEAT_INTERVAL_SECONDS = 15
_RUN_LOCK = threading.Lock()
_RUN_WORKSPACES: dict[str, Path] = {}
_RUN_RESULTS: dict[str, dict[str, Any]] = {}


def is_coding_intent(text: str) -> bool:
    """Recognize actionable coding requests, not remarks about coding capability."""
    text = str(text or "").strip()
    if not text:
        return False
    # Capability statements/questions are conversation, not delegated work.
    if re.search(
        r"\b(?:they tell me|i(?:'ve| have) heard|i was told|apparently)\b.{0,100}"
        r"\b(?:you|ariadne)\s+(?:can|could|are able to)\s+(?:write|change|fix|create|build|edit)\b",
        text, re.IGNORECASE,
    ):
        return False
    action = re.search(
        r"\b(?:write|create|build|implement|modify|change|edit|fix|repair|debug|refactor|add|remove|update|test|run)\b",
        text, re.IGNORECASE,
    )
    subject = re.search(
        r"\b(?:code|coding|program|script|app|application|repository|repo|project|file|bug|error|test|function|class|module|software|python|rust|javascript|typescript|html|css|json|sql)\b",
        text, re.IGNORECASE,
    ) or re.search(r"\b[\w./\\-]+\.(?:py|rs|js|jsx|ts|tsx|html|css|json|sql|go|java|cs|sh|ps1)\b", text, re.IGNORECASE)
    if not action or not subject:
        return False
    # Require a recognizable request form. A bare observation such as
    # "they tell me you can write code now" must stay in ordinary Chat.
    request = re.search(
        r"^\s*(?:please\s+)?(?:write|create|build|implement|modify|change|edit|fix|repair|debug|refactor|add|remove|update|test|run)\b"
        r"|\b(?:please|can you|could you|would you|i need you to|i want you to|help me|let's)\b.{0,180}"
        r"\b(?:write|create|build|implement|modify|change|edit|fix|repair|debug|refactor|add|remove|update|test|run)\b",
        text, re.IGNORECASE,
    )
    if not request:
        return False
    # A general capability question is still not a work request unless it
    # names a concrete thing to produce or change.
    if re.search(r"\b(?:can|could|would)\s+you\s+(?:write|create|build|code)\b", text, re.IGNORECASE) and not re.search(
        r"\b(?:that|which|to|for|in|into|from|using|named|called)\b|\.[A-Za-z0-9]{1,8}\b",
        text, re.IGNORECASE,
    ):
        return False
    return True


def _within_root(path: Path, root: Path) -> bool:
    try:
        candidate = path.resolve()
        base = root.resolve()
        return os.path.commonpath((str(candidate), str(base))).casefold() == str(base).casefold()
    except (OSError, ValueError):
        return False


def _file_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file() or any(part.casefold() in {".git", ".qwen", "node_modules"} for part in path.relative_to(root).parts):
            continue
        try:
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return result


def _write_json_record(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def coding_run_result(run_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[a-f0-9]{32}", str(run_id or "")):
        raise ValueError("Invalid coding run ID.")
    with _RUN_LOCK:
        result = _RUN_RESULTS.get(run_id)
    if result is not None:
        return result
    result_path = (RESULT_ROOT / f"{run_id}.json").resolve()
    legacy_path = (EVIDENCE_ROOT / f"{run_id}.json").resolve()
    if not _within_root(result_path, RESULT_ROOT) or not _within_root(legacy_path, EVIDENCE_ROOT):
        raise ValueError("Coding result path escaped its result directory.")
    path = result_path if result_path.is_file() else legacy_path
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Coding result could not be read: {exc}") from exc
    if not isinstance(value, dict) or value.get("run_id") != run_id:
        raise ValueError("Coding result record is invalid.")
    return value


def record_coding_verification(run_id: str, report: dict[str, Any]) -> dict[str, Any]:
    result = coding_run_result(run_id)
    if result is None:
        raise FileNotFoundError("Coding job has not produced its final result yet.")
    if not isinstance(report, dict) or len(json.dumps(report, ensure_ascii=False)) > 500_000:
        raise ValueError("Interactive verification report is invalid or too large.")
    if not isinstance(report.get("passed"), bool) or not isinstance(report.get("usable"), bool):
        raise ValueError("Interactive verification must include boolean passed and usable values.")
    actions = report.get("actions", [])
    errors = report.get("errors", [])
    if not isinstance(actions, list) or len(actions) > 100 or not isinstance(errors, list) or len(errors) > 100:
        raise ValueError("Interactive verification evidence exceeds its bounds.")
    if any(not isinstance(item, dict) or not isinstance(item.get("changed"), bool) for item in actions):
        raise ValueError("Interactive verification actions are malformed.")
    if any(not isinstance(item, str) for item in errors):
        raise ValueError("Interactive verification errors must be text.")
    report = {**report, "passed": report.get("passed") is True and report.get("usable") is True and not errors}
    result = {**result, "verification": report}
    _write_json_record(RESULT_ROOT / f"{run_id}.json", result)
    _write_json_record(EVIDENCE_ROOT / f"{run_id}.json", result)
    with _RUN_LOCK:
        _RUN_RESULTS[run_id] = result
    return result


def _coding_workspace(run_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", str(run_id or "")):
        raise ValueError("Invalid coding run ID.")
    with _RUN_LOCK:
        workspace = _RUN_WORKSPACES.get(run_id)
    if workspace is None:
        result = coding_run_result(run_id)
        if result is None:
            raise FileNotFoundError("Coding workspace is not available yet.")
        workspace = Path(str(result.get("workspace") or "")).resolve()
    workspace = workspace.resolve()
    if workspace == WORKSPACE_ROOT or _within_root(workspace, RESULT_ROOT) or not _within_root(workspace, WORKSPACE_ROOT) or not workspace.is_dir():
        raise ValueError("Coding workspace is outside Ariadne's coding workspace root.")
    return workspace


def _workspace_file(run_id: str, relative_path: str) -> tuple[Path, Path]:
    root = _coding_workspace(run_id)
    relative = str(relative_path or "").replace("\\", "/")
    candidate = Path(relative)
    if candidate.is_absolute() or any(part == ".." for part in relative.split("/")):
        raise ValueError("Workspace path must be relative and may not traverse upward.")
    target = (root / candidate).resolve()
    if not _within_root(target, root):
        raise ValueError("Workspace path escaped the assigned project.")
    return root, target


def inspect_coding_workspace(run_id: str, relative_path: str = "", *, read_file: bool = False) -> dict[str, Any]:
    root, target = _workspace_file(run_id, relative_path)
    relative = target.relative_to(root).as_posix()
    if target.is_dir():
        entries = []
        for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold())):
            if child.name.casefold() in {".git", ".qwen", "node_modules"}:
                continue
            resolved = child.resolve()
            if not _within_root(resolved, root):
                continue
            try:
                size = child.stat().st_size if child.is_file() else None
            except OSError:
                continue
            entries.append({
                "name": child.name,
                "path": resolved.relative_to(root).as_posix(),
                "kind": "directory" if child.is_dir() else "file",
                "size": size,
            })
            if len(entries) >= 500:
                break
        return {"ok": True, "run_id": run_id, "workspace": str(root), "path": relative, "entries": entries}
    if not target.is_file():
        raise FileNotFoundError("Workspace path was not found.")
    size = target.stat().st_size
    if size > 2_000_000:
        raise ValueError("Files above 2 MB are not shown in the Chat workspace viewer.")
    if not read_file:
        return {"ok": True, "run_id": run_id, "workspace": str(root), "path": relative,
                "kind": "file", "size": size}
    raw = target.read_bytes()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("This file is binary and cannot be shown as source text.") from exc
    limit = 1_000_000
    return {"ok": True, "run_id": run_id, "workspace": str(root), "path": relative,
            "kind": "file", "size": size, "content": content[:limit], "truncated": len(content) > limit}


def coding_preview_entry(run_id: str) -> tuple[Path, str]:
    root = _coding_workspace(run_id)
    for candidate in ("index.html", "index.htm", "dist/index.html", "public/index.html"):
        path = (root / candidate).resolve()
        if _within_root(path, root) and path.is_file():
            return root, candidate
    raise FileNotFoundError("No index.html, dist/index.html, or public/index.html is available to preview.")


def coding_preview_file(run_id: str, relative_path: str) -> tuple[Path, Path, bytes]:
    root, target = _workspace_file(run_id, relative_path)
    if target.is_dir():
        target = (target / "index.html").resolve()
    if not target.is_file():
        raise FileNotFoundError("Preview asset was not found.")
    if target.suffix.casefold() not in {".html", ".htm", ".css", ".js", ".mjs", ".json", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".txt", ".wasm", ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".mp4", ".webm"}:
        raise ValueError("This file type is not available in the browser preview.")
    if target.stat().st_size > 10_000_000:
        raise ValueError("Preview files above 10 MB are not served.")
    return root, target, target.read_bytes()


def _parse_transcript(stdout: str, stderr: str, exit_code: int | None) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[str], int, dict[str, Any], dict[str, Any], bool]:
    messages: Any = None
    try:
        messages = json.loads(stdout.strip())
    except (ValueError, TypeError, json.JSONDecodeError):
        # Qwen's stream-json format emits one JSON event per line.
        start = stdout.find("[{")
        if start >= 0:
            try:
                messages = json.loads(stdout[start:].strip())
            except (ValueError, TypeError, json.JSONDecodeError):
                messages = None
        if messages is None:
            streamed: list[dict[str, Any]] = []
            for line in stdout.splitlines():
                try:
                    event = json.loads(line)
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue
                if isinstance(event, dict):
                    streamed.append(event)
            messages = streamed or None
    if not isinstance(messages, list):
        errors = [stderr.strip()] if stderr.strip() else []
        if exit_code not in (None, 0) and not errors:
            errors.append(f"Qwen Code exited with code {exit_code}.")
        missing_result = exit_code == 0
        if missing_result:
            errors.append("Qwen Code exited successfully without emitting its final result event.")
        return stdout.strip()[-12_000:], [], [], errors, 0, {}, {}, missing_result

    tool_calls: list[dict[str, Any]] = []
    errors: list[str] = [stderr.strip()[:4_000]] if stderr.strip() else []
    summary_parts: list[str] = []
    assistant_messages = 0
    model_usage: dict[str, Any] = {}
    usage: dict[str, Any] = {}
    final_result_error = False
    final_result_seen = False
    for message in messages:
        if not isinstance(message, dict):
            continue
        kind = str(message.get("type") or "")
        if kind == "assistant":
            assistant_messages += 1
            body = message.get("message", {})
            blocks = body.get("content", []) if isinstance(body, dict) else []
            if isinstance(blocks, list):
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and isinstance(block.get("text"), str):
                        summary_parts.append(block["text"])
                    elif block.get("type") == "tool_use":
                        call_input = block.get("input", {})
                        call = {"id": block.get("id"), "name": block.get("name"),
                                "input": call_input if isinstance(call_input, (dict, list, str, int, float, bool)) else {}}
                        tool_calls.append(call)
                        if str(call.get("name") or "").casefold() in {"run_shell_command", "shell", "execute"}:
                            data = call.get("input") if isinstance(call.get("input"), dict) else {}
                            command = str(data.get("command") or "")
        elif kind == "user":
            body = message.get("message", {})
            blocks = body.get("content", []) if isinstance(body, dict) else []
            if isinstance(blocks, list):
                for block in blocks:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        if block.get("is_error"):
                            errors.append(str(block.get("content") or "Tool call failed.")[:2_000])
                        tool_use_id = block.get("tool_use_id")
                        for call in reversed(tool_calls):
                            if call.get("id") == tool_use_id:
                                call["result"] = block.get("content")
                                call["is_error"] = bool(block.get("is_error"))
                                break
        elif kind == "result":
            final_result_seen = True
            result = message.get("result")
            if isinstance(result, str) and result.strip():
                summary_parts.append(result)
            if message.get("is_error"):
                final_result_error = True
                errors.append(str(message.get("error") or "Qwen Code reported an unsuccessful result.")[:2_000])
            if isinstance(message.get("usage"), dict):
                usage = message["usage"]
            if isinstance(message.get("modelUsage"), dict):
                model_usage = message["modelUsage"]
    summary = "\n".join(dict.fromkeys(part.strip() for part in summary_parts if part.strip()))[-12_000:]
    if exit_code not in (None, 0):
        errors.append(f"Qwen Code exited with code {exit_code}.")
    elif exit_code == 0 and not final_result_seen:
        final_result_error = True
        errors.append("Qwen Code exited successfully without emitting its final result event.")
    tests: list[dict[str, Any]] = []
    for call in tool_calls:
        if str(call.get("name") or "").casefold() not in {"run_shell_command", "shell", "execute"}:
            continue
        data = call.get("input") if isinstance(call.get("input"), dict) else {}
        command = str(data.get("command") or "")
        if not re.search(r"\b(test|pytest|unittest|cargo\s+test|npm\s+(?:run\s+)?test|vitest|jest|go\s+test|mvn\s+test)\b", command, re.IGNORECASE):
            continue
        captured = str(call.get("result") or "")
        if call.get("is_error") or re.search(r"(?:exit(?:ed)?(?: with)? (?:code|status)\s*[:=]?\s*[1-9]|\bfailed\b|\btests? failed\b)", captured, re.IGNORECASE):
            outcome = "failed"
        elif re.search(r"(?:exit(?:ed)?(?: with)? (?:code|status)\s*[:=]?\s*0|\bpassed\b|\btests? passed\b|\b0 failures?\b)", captured, re.IGNORECASE):
            outcome = "passed"
        elif call.get("result") is not None:
            outcome = "result captured"
        else:
            outcome = "command issued; result not captured"
        tests.append({"command": command, "result": outcome, "output": captured[-2_000:]})
    return summary, tool_calls, tests, errors, assistant_messages, usage, model_usage, final_result_error


def _stop_child_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    subprocess.run(
        ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
        capture_output=True, text=True, timeout=10, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def run_qwen_code_task(
    task: str,
    *,
    workspace: str | Path | None = None,
    model: str = DEFAULT_MODEL,
    context_tokens: int = DEFAULT_CONTEXT_TOKENS,
    constraints: list[str] | tuple[str, ...] | None = None,
    resource_admission: Callable[[], Any] | None = None,
    progress: Callable[[str, str], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run Qwen Code until it finishes, is cancelled, or stops making progress."""
    started = time.perf_counter()
    run_id = uuid.uuid4().hex
    task = str(task or "").strip()
    model = str(model or DEFAULT_MODEL).strip()
    if not task:
        raise ValueError("Coding task must not be empty.")
    if len(task) > 16_000:
        raise ValueError("Keep a coding task below 16,000 characters.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,180}", model):
        raise ValueError("Model name contains unsupported characters.")
    context_tokens = int(context_tokens)
    if not 1_024 <= context_tokens <= MAX_CONTEXT_TOKENS:
        raise ValueError(f"context_tokens must be between 1024 and {MAX_CONTEXT_TOKENS}.")
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    if workspace is None or not str(workspace).strip():
        workspace_path = WORKSPACE_ROOT / f"chat-{run_id}"
    else:
        workspace_path = Path(workspace).expanduser()
        if not workspace_path.is_absolute():
            workspace_path = WORKSPACE_ROOT / workspace_path
    workspace_path = workspace_path.resolve()
    if not _within_root(workspace_path, WORKSPACE_ROOT):
        raise ValueError(f"Coding workspace must be under {WORKSPACE_ROOT}.")
    if workspace_path == WORKSPACE_ROOT:
        raise ValueError("Coding tasks require a dedicated child workspace, not the workspace root itself.")
    if _within_root(workspace_path, RESULT_ROOT):
        raise ValueError("The reserved Ariadne result directory cannot be used as a coding project workspace.")
    if not LAUNCHER.is_file():
        raise RuntimeError(f"Installed Qwen Code launcher is missing: {LAUNCHER}")
    workspace_path.mkdir(parents=True, exist_ok=True)
    with _RUN_LOCK:
        _RUN_WORKSPACES[run_id] = workspace_path
    if progress:
        progress("started", json.dumps({"run_id": run_id, "workspace": str(workspace_path), "model": model,
                                         "context_tokens": context_tokens}, ensure_ascii=False))

    baseline = _file_hashes(workspace_path)
    default_constraints = [
        "Keep all project reads, writes, generated files, and commands inside the assigned workspace.",
        "Inspect the project instructions and existing code before editing.",
        "Make the smallest coherent change, then run the relevant tests or direct verification.",
        "Repair any failure caused by the change and rerun verification before reporting.",
        "Do not delete unrelated files or access other repositories.",
    ]
    all_constraints = [str(item).strip()[:1_000] for item in (constraints or []) if str(item).strip()]
    prompt = (
        "Ariadne coding task. Inspect the assigned workspace, make the requested code change, execute and verify it.\n\n"
        f"Task:\n{task}\n\n"
        f"Selected model: {model}\nQwen Code context window: {context_tokens} tokens.\n\n"
        "Constraints:\n- " + "\n- ".join([*default_constraints, *all_constraints])
    )
    command = [
        shutil.which("powershell.exe") or "powershell.exe", "-NoLogo", "-NoProfile",
        "-ExecutionPolicy", "Bypass", "-File", str(LAUNCHER),
        "-Workspace", str(workspace_path), "-Prompt", prompt, "-Model", model,
        "-ContextTokens", str(context_tokens), "-OutputFormat", "stream-json",
        "-IncludePartialMessages", "-Autonomous",
    ]
    if progress:
        progress("admission", f"Requesting Rust Host model/GPU admission for {model}.")
    context = resource_admission() if resource_admission else inference_lease(model, DEFAULT_ENDPOINT)
    process: subprocess.Popen | None = None
    stdout = ""
    stderr = ""
    exit_code: int | None = None
    cancelled = False
    admission_acquired = False
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    output_events: queue.Queue[tuple[str, str | None]] = queue.Queue()
    try:
        with context:
            admission_acquired = True
            if cancel_requested and cancel_requested():
                cancelled = True
            elif progress:
                progress("running", f"Qwen Code is working in {workspace_path}.")
            if not cancelled:
                process = subprocess.Popen(
                    command, cwd=str(workspace_path), stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )

                def drain(stream: Any, name: str, parts: list[str]) -> None:
                    try:
                        for line in iter(stream.readline, ""):
                            parts.append(line)
                            output_events.put((name, line))
                    finally:
                        output_events.put((name, None))

                readers = [
                    threading.Thread(target=drain, args=(process.stdout, "stdout", stdout_parts), daemon=True),
                    threading.Thread(target=drain, args=(process.stderr, "stderr", stderr_parts), daemon=True),
                ]
                for reader in readers:
                    reader.start()

                last_agent_progress = time.monotonic()
                last_client_heartbeat = last_agent_progress
                while process.poll() is None:
                    now = time.monotonic()
                    if cancel_requested and cancel_requested():
                        cancelled = True
                        _stop_child_tree(process)
                        break
                    try:
                        stream_name, line = output_events.get(timeout=PROCESS_POLL_INTERVAL_SECONDS)
                        if line is not None:
                            last_agent_progress = time.monotonic()
                            if progress:
                                progress("output", json.dumps({"stream": stream_name, "line": line[-32_000:]}, ensure_ascii=False))
                    except queue.Empty:
                        pass
                    now = time.monotonic()
                    if progress and now - last_client_heartbeat >= CLIENT_HEARTBEAT_INTERVAL_SECONDS:
                        elapsed = int(now - started)
                        quiet = int(now - last_agent_progress)
                        progress("running", f"Qwen Code process is still running; {elapsed}s elapsed, {quiet}s since its last model/tool event. No elapsed-time limit is applied.")
                        last_client_heartbeat = now
                exit_code = process.wait()
                for reader in readers:
                    reader.join(timeout=5)
                stdout = "".join(stdout_parts)
                stderr = "".join(stderr_parts)
    except Exception as exc:
        if process is not None and process.poll() is None:
            _stop_child_tree(process)
            process.wait()
        stdout = "".join(stdout_parts)
        stderr = "".join(stderr_parts) + f"\n{type(exc).__name__}: {exc}"
        exit_code = None

    summary, tool_calls, tests, errors, model_calls, usage, model_usage, final_result_error = _parse_transcript(stdout or "", stderr or "", exit_code)
    if cancelled:
        errors.append("Qwen Code was cancelled by the user or the Chat connection closed.")
    after = _file_hashes(workspace_path)
    changed_files = sorted(path for path in set(baseline) | set(after) if baseline.get(path) != after.get(path))
    if not summary:
        summary = "Qwen Code completed without a final summary." if exit_code == 0 else "Qwen Code did not return a successful final response."
    status = "CANCELLED" if cancelled else "FAILED" if exit_code != 0 or final_result_error else "SUCCESS"
    result = {
        "run_id": run_id,
        "status": status,
        "workspace": str(workspace_path),
        "model": model,
        "context_tokens": context_tokens,
        "changed_files": changed_files,
        "tests": tests,
        "elapsed_time_seconds": round(time.perf_counter() - started, 3),
        "tool_calls": tool_calls,
        "tool_call_count": len(tool_calls),
        "model_calls": model_calls,
        "usage": usage,
        "model_usage": model_usage,
        "errors": [item for item in errors if item],
        "summary": summary,
        "exit_code": exit_code,
        "cancelled": cancelled,
        "cli_result_error": final_result_error,
        "host_admission": "Rust Host lease held for the complete Qwen Code process lifetime" if admission_acquired else "Rust Host admission failed",
    }
    _write_json_record(RESULT_ROOT / f"{run_id}.json", result)
    _write_json_record(EVIDENCE_ROOT / f"{run_id}.json", result)
    with _RUN_LOCK:
        _RUN_RESULTS[run_id] = result
    if progress:
        progress("completed" if status == "SUCCESS" else "cancelled" if status == "CANCELLED" else "failed", summary[:450])
    return result


__all__ = ["DEFAULT_CONTEXT_TOKENS", "DEFAULT_MODEL", "WORKSPACE_ROOT", "coding_preview_entry",
           "coding_preview_file", "coding_run_result", "inspect_coding_workspace", "is_coding_intent",
           "record_coding_verification",
           "run_qwen_code_task"]
