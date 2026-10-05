"""Ariadne-owned Ollama process lifecycle and strict model-store validation."""

from __future__ import annotations

import ctypes
import atexit
import csv
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
MODEL_STORE = Path(r"F:\AI\Models\Ollama")
MODEL_LIBRARY = Path("manifests") / "registry.ollama.ai" / "library"
_LISTENING_LINE = re.compile(r"^\s*TCP\s+(\S+)\s+(\S+)\s+LISTENING\s+(\d+)\s*$", re.IGNORECASE)
_OWNED_OLLAMA_PROCESS: subprocess.Popen | None = None


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


def sync_model_store_environment(required_models: tuple[str, ...] = ()) -> str:
    """Force Ariadne and future Windows user processes to the one model store."""
    selected = str(MODEL_STORE)
    os.environ["OLLAMA_MODELS"] = selected
    if os.name == "nt":
        import winreg

        try:
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                r"Environment",
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.SetValueEx(key, "OLLAMA_MODELS", 0, winreg.REG_SZ, selected)
        except OSError as exc:
            raise RuntimeError(f"Could not persist OLLAMA_MODELS={selected}: {exc}") from exc
    return selected


def _models_in_store(store: str) -> set[str]:
    library = Path(store) / MODEL_LIBRARY
    if not _safe_is_dir(library):
        return set()
    models: set[str] = set()
    try:
        manifests = library.rglob("*")
        for manifest in manifests:
            if not manifest.is_file():
                continue
            relative = manifest.relative_to(library)
            if len(relative.parts) < 2:
                continue
            name = "/".join(relative.parts[:-1])
            models.add(f"{name}:{relative.name}")
    except OSError:
        return set()
    return models


def _stop_process_tree(pid: int) -> None:
    try:
        stopped = subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=8.0,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Could not stop Ollama process {pid}: {exc}") from exc
    if stopped.returncode != 0 and _process_image_path(pid):
        detail = (stopped.stderr or stopped.stdout or "taskkill failed").strip()
        raise RuntimeError(f"Could not stop Ollama process {pid}: {detail}")


def _stop_existing_ollama() -> list[tuple[int, str]]:
    """Stop Ollama Desktop supervisors first, then exact owners of port 11434."""
    stopped: list[tuple[int, str]] = []
    for pid in _ollama_desktop_supervisor_pids():
        path = _process_image_path(pid)
        if not path or Path(path).name.casefold() != "ollama app.exe":
            raise RuntimeError(f"Could not verify Ollama Desktop supervisor {pid}; startup stopped safely.")
        _stop_process_tree(pid)
        stopped.append((pid, path))

    deadline = time.monotonic() + 10.0
    while True:
        listener_pids = _listener_pids()
        if not listener_pids:
            return stopped
        for pid in listener_pids:
            path = _process_image_path(pid)
            if not path:
                raise RuntimeError(f"Could not verify the process using Ollama port {OLLAMA_PORT} (PID {pid}).")
            if Path(path).name.casefold() != "ollama.exe":
                raise RuntimeError(
                    f"Port {OLLAMA_PORT} is owned by {path}, not Ollama; Ariadne will not terminate an unrelated process."
                )
            _stop_process_tree(pid)
            stopped.append((pid, path))
        if time.monotonic() >= deadline:
            raise RuntimeError(f"An Ollama listener still owns port {OLLAMA_PORT} after shutdown.")
        time.sleep(0.1)


def start_owned_ollama(
    endpoint: str,
    required_models: tuple[str, ...],
    *,
    startup_timeout: float = 45.0,
) -> dict[str, object]:
    """Replace unmanaged Ollama, launch it with F: explicitly, then verify its full catalogue."""
    global _OWNED_OLLAMA_PROCESS
    if os.name != "nt":
        raise RuntimeError("Ariadne-owned Ollama startup is only supported on Windows.")
    store_error: RuntimeError | None = None
    try:
        store = sync_model_store_environment()
    except RuntimeError as exc:
        store = str(MODEL_STORE)
        store_error = exc
    stopped = _stop_existing_ollama()
    if store_error:
        raise store_error
    if not _safe_is_dir(MODEL_STORE):
        raise RuntimeError(f"The required Ollama model store is unavailable: {store}")
    expected_models = _models_in_store(store)
    if not expected_models:
        raise RuntimeError(f"No Ollama model manifests were found under {store}.")
    missing_required = sorted(
        model for model in set(required_models)
        if model not in expected_models or not store_contains_model(store, model)
    )
    if missing_required:
        raise RuntimeError(f"Required Ollama models are missing from {store}: {', '.join(missing_required)}")

    executable = _find_ollama_executable(stopped)
    if not executable:
        raise RuntimeError("The Ollama executable could not be located; Ariadne did not start the service.")

    child_environment = os.environ.copy()
    child_environment["OLLAMA_MODELS"] = store
    try:
        _OWNED_OLLAMA_PROCESS = subprocess.Popen(
            [executable, "serve"],
            cwd=str(Path(executable).parent),
            env=child_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _OWNED_OLLAMA_PROCESS = None
        raise RuntimeError(f"Ariadne could not start Ollama from {executable}: {exc}") from exc

    owned_pid = _OWNED_OLLAMA_PROCESS.pid
    deadline = time.monotonic() + max(1.0, startup_timeout)
    last_models: list[str] | None = None
    try:
        while time.monotonic() < deadline:
            if _OWNED_OLLAMA_PROCESS.poll() is not None:
                code = _OWNED_OLLAMA_PROCESS.returncode
                _OWNED_OLLAMA_PROCESS = None
                raise RuntimeError(f"Ariadne-owned Ollama exited during startup (code {code}).")
            if owned_pid in _listener_pids():
                last_models = _request_models(endpoint, DEFAULT_PROBE_TIMEOUT_SECONDS)
                if last_models is not None and expected_models.issubset(set(last_models)):
                    return {
                        "state": "online",
                        "available": True,
                        "owned": True,
                        "pid": owned_pid,
                        "store": store,
                        "models": last_models,
                        "detail": f"Ariadne owns Ollama PID {owned_pid}; all {len(expected_models)} models are visible from {store}.",
                    }
            time.sleep(0.2)
    except Exception:
        stop_owned_ollama()
        raise

    stop_owned_ollama()
    visible = ", ".join(last_models or []) or "no catalogue response"
    missing = sorted(expected_models - set(last_models or []))
    raise RuntimeError(
        f"Ariadne stopped startup because Ollama did not expose every model from {store}. "
        f"Missing: {', '.join(missing) or 'Ollama listener verification failed'}. Visible: {visible}."
    )


def stop_owned_ollama() -> None:
    """Stop only the Ollama server process Ariadne launched, including its children."""
    global _OWNED_OLLAMA_PROCESS
    process = _OWNED_OLLAMA_PROCESS
    if process is None:
        return
    _OWNED_OLLAMA_PROCESS = None
    if process.poll() is not None:
        return
    if os.name == "nt":
        _stop_process_tree(process.pid)
    else:
        process.terminate()
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2.0)


atexit.register(stop_owned_ollama)


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
    except (OSError, subprocess.SubprocessError) as exc:
        if os.name == "nt":
            raise RuntimeError(f"Could not inspect Windows listeners on Ollama port {port}.") from exc
        return []
    if completed.returncode != 0 and os.name == "nt":
        raise RuntimeError(f"Could not inspect Windows listeners on Ollama port {port}.")
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
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
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


def _ollama_desktop_supervisor_pids() -> list[int]:
    """Return verified Ollama Desktop supervisor PIDs or fail closed on Windows."""
    if os.name != "nt":
        return []
    try:
        completed = subprocess.run(
            ["tasklist.exe", "/FI", "IMAGENAME eq ollama app.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Could not inspect Ollama Desktop processes; Ariadne cannot establish process ownership.") from exc
    if completed.returncode != 0:
        raise RuntimeError("Could not inspect Ollama Desktop processes; Ariadne cannot establish process ownership.")
    pids: set[int] = set()
    for row in csv.reader(completed.stdout.splitlines()):
        if len(row) < 2 or row[0].strip().casefold() != "ollama app.exe":
            continue
        try:
            pid = int(row[1])
        except ValueError:
            continue
        image_path = _process_image_path(pid)
        if not image_path or Path(image_path).name.casefold() != "ollama app.exe":
            raise RuntimeError(f"Could not verify Ollama Desktop process {pid}; startup stopped safely.")
        pids.add(pid)
    return sorted(pids)


def _find_ollama_executable(listener_paths: list[tuple[int, str]]) -> str:
    candidates = []
    for _, path in listener_paths:
        if path and Path(path).name.casefold() == "ollama.exe":
            return path
        if path and Path(path).name.casefold() == "ollama app.exe":
            candidates.append(Path(path).with_name("ollama.exe"))
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
    supervisor_pids: Callable[[], list[int]] = _ollama_desktop_supervisor_pids,
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
    supervisors = supervisor_pids()
    if supervisors:
        return {"state": "degraded", "available": models is not None, "repaired": False, "models": models or [], "store": store, "missing": missing, "detail": f"Ollama Desktop supervisor(s) {', '.join(map(str, supervisors))} are running. Automatic listener restart was skipped because the supervisor can respawn with a different model catalogue."}
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
