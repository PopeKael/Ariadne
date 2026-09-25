"""Fast, bounded Ollama startup validation and recovery for Ariadne.

Ollama is an external Windows process. Ariadne may repair only the exact
Ollama listener that owns its configured endpoint; it must never use a broad
image-name kill or wait for a model to load during application startup.
"""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Callable


DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
DEFAULT_PROBE_TIMEOUT_SECONDS = 0.75
DEFAULT_REPAIR_TIMEOUT_SECONDS = 4.0
OLLAMA_PORT = 11434
_LISTENING_LINE = re.compile(r"^\s*TCP\s+(\S+)\s+(\S+)\s+LISTENING\s+(\d+)\s*$", re.IGNORECASE)


def _safe_is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _safe_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _read_user_model_store() -> str:
    """Read the persistent Windows setting without changing the environment."""
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            value, _ = winreg.QueryValueEx(key, "OLLAMA_MODELS")
        return value.strip() if isinstance(value, str) else ""
    except (FileNotFoundError, OSError):
        return ""


def sync_model_store_environment(required_models: tuple[str, ...] = ()) -> str:
    """Make the persisted model-store choice visible to Ariadne and children."""
    current = os.environ.get("OLLAMA_MODELS", "").strip()
    persistent = _read_user_model_store()
    # A stale process environment is exactly what this preflight is designed
    # to correct. Prefer the durable user setting when it exists.
    selected = persistent or current
    if selected and required_models and not all(store_contains_model(selected, model) for model in required_models):
        selected = ""
    if not selected and required_models:
        candidates = []
        if current:
            candidates.append(Path(current))
        if persistent:
            candidates.append(Path(persistent))
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        user_profile = os.environ.get("USERPROFILE", "").strip()
        if local_app_data:
            candidates.append(Path(local_app_data) / "Ollama" / "models")
        if user_profile:
            candidates.append(Path(user_profile) / ".ollama" / "models")
        # This is the active store documented by this installation. Selection
        # is manifest-based, so it is never used merely because the drive exists.
        if os.name == "nt":
            candidates.append(Path(r"F:\AI\Models\Ollama"))
        for candidate in dict.fromkeys(candidates):
            if _safe_is_dir(candidate) and all(store_contains_model(str(candidate), model) for model in required_models):
                selected = str(candidate)
                break
    if selected:
        os.environ["OLLAMA_MODELS"] = selected
    return selected


def model_manifest_path(store: str, model: str) -> Path:
    """Return Ollama's registry manifest path for a model name."""
    repository, separator, tag = model.partition(":")
    tag = tag or "latest"
    repository_parts = [part for part in repository.split("/") if part]
    if repository_parts and repository_parts[0] == "library":
        repository_parts = repository_parts[1:]
    return Path(store) / "manifests" / "registry.ollama.ai" / "library" / Path(*repository_parts) / tag


def store_contains_model(store: str, model: str) -> bool:
    if not store or not model:
        return False
    return _safe_is_file(model_manifest_path(store, model))


def _request_models(endpoint: str, timeout: float) -> list[str] | None:
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/api/tags",
        headers={"Accept": "application/json", "User-Agent": "Ariadne Ollama preflight"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        rows = payload.get("models", []) if isinstance(payload, dict) else []
        return [str(row["name"]) for row in rows if isinstance(row, dict) and row.get("name")]
    except (OSError, ValueError, TypeError, KeyError, urllib.error.URLError, json.JSONDecodeError):
        return None


def _listener_pids(port: int = OLLAMA_PORT) -> list[int]:
    """Return exact TCP listener owners for a port, without killing anything."""
    try:
        completed = subprocess.run(
            ["netstat.exe", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    owners: set[int] = set()
    for line in completed.stdout.splitlines():
        match = _LISTENING_LINE.match(line)
        if not match:
            continue
        local_port = match.group(1).rsplit(":", 1)[-1]
        if local_port == str(port):
            owners.add(int(match.group(3)))
    return sorted(owners)


def _process_image_path(pid: int) -> str:
    """Read a process image path for ownership verification on Windows."""
    if os.name != "nt":
        return ""
    process_query_limited_information = 0x1000
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    except OSError:
        return ""
    if not handle:
        return ""
    try:
        size = ctypes.c_uint32(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value[: size.value]
    finally:
        kernel32.CloseHandle(handle)


def _ollama_listener_paths() -> list[tuple[int, str]]:
    listeners: list[tuple[int, str]] = []
    for pid in _listener_pids():
        image_path = _process_image_path(pid)
        if image_path and Path(image_path).name.casefold() == "ollama.exe":
            listeners.append((pid, image_path))
    return listeners


def _find_ollama_executable(listener_paths: list[tuple[int, str]]) -> str:
    for _, path in listener_paths:
        if path:
            return path
    candidates = []
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe")
    candidates.extend(
        [
            Path(os.environ.get("PROGRAMFILES", "")) / "Ollama" / "ollama.exe",
            Path(shutil.which("ollama.exe") or ""),
        ]
    )
    return next((str(path) for path in candidates if str(path) and _safe_is_file(path)), "")


def _restart_exact_ollama(listener_paths: list[tuple[int, str]], store: str) -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Automatic Ollama recovery is only available on Windows."
    executable = _find_ollama_executable(listener_paths)
    if not executable:
        return False, "Ollama executable could not be located."
    for pid, _ in listener_paths:
        try:
            stopped = subprocess.run(
                ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"Could not stop the verified Ollama listener {pid}: {exc}"
        if stopped.returncode != 0:
            return False, f"Could not stop the verified Ollama listener {pid}."
    child_environment = os.environ.copy()
    child_environment["OLLAMA_MODELS"] = store
    try:
        subprocess.Popen(
            [executable, "serve"],
            cwd=str(Path(executable).parent),
            env=child_environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Ollama could not be restarted: {exc}"
    return True, f"Restarted Ollama listener {', '.join(str(pid) for pid, _ in listener_paths)} with the configured model store."


def startup_preflight(
    endpoint: str = DEFAULT_ENDPOINT,
    required_models: tuple[str, ...] = (),
    *,
    probe_timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    repair_timeout: float = DEFAULT_REPAIR_TIMEOUT_SECONDS,
    request_models: Callable[[str, float], list[str] | None] = _request_models,
    listener_paths: Callable[[], list[tuple[int, str]]] = _ollama_listener_paths,
    restart_ollama: Callable[[list[tuple[int, str]], str], tuple[bool, str]] = _restart_exact_ollama,
) -> dict[str, object]:
    """Validate Ollama quickly and repair a stale model-store process once."""
    required = tuple(dict.fromkeys(model.strip() for model in required_models if model.strip()))
    store = sync_model_store_environment(required)
    models = request_models(endpoint, probe_timeout)
    if models is not None and all(model in models for model in required):
        return {"state": "online", "available": True, "repaired": False, "models": models, "store": store, "detail": "Required Ollama models are available."}

    missing = [model for model in required if models is None or model not in models]
    if not store or not any(store_contains_model(store, model) for model in missing):
        if models is None:
            detail = "Ollama did not answer its model catalogue."
        else:
            detail = f"Required Ollama model(s) missing: {', '.join(missing)}."
        return {"state": "degraded", "available": models is not None, "repaired": False, "models": models or [], "store": store, "missing": missing, "detail": detail}

    paths = listener_paths()
    if models is not None and not paths:
        return {"state": "degraded", "available": True, "repaired": False, "models": models, "store": store, "missing": missing, "detail": "Ollama answered, but its listener owner could not be verified; no process was changed."}
    restarted, restart_detail = restart_ollama(paths, store)
    if not restarted:
        return {"state": "degraded", "available": models is not None, "repaired": False, "models": models or [], "store": store, "missing": missing, "detail": restart_detail}

    deadline = time.monotonic() + max(0.1, repair_timeout)
    repaired_models: list[str] | None = None
    while time.monotonic() < deadline:
        repaired_models = request_models(endpoint, min(probe_timeout, max(0.1, deadline - time.monotonic())))
        if repaired_models is not None and all(model in repaired_models for model in required):
            return {"state": "online", "available": True, "repaired": True, "models": repaired_models, "store": store, "detail": restart_detail}
        time.sleep(0.1)
    return {"state": "degraded", "available": repaired_models is not None, "repaired": False, "models": repaired_models or [], "store": store, "missing": missing, "detail": f"{restart_detail} The required model was still not visible within the startup recovery window."}
