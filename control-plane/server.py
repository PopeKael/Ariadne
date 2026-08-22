from __future__ import annotations

import csv
import ctypes
import io
import json
import os
import shutil
import subprocess
import ssl
import sys
import threading
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
HOST = os.environ.get("ARIADNE_BIND_ADDRESS", "127.0.0.1")
PORT = int(os.environ.get("ARIADNE_PORT", "8765"))
LM_STUDIO_PATH = Path(r"C:\Program Files\AMD\AI_Bundle\LMStudio\LM Studio.exe")
DOCKER_DESKTOP_PATH = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
DOCKER_PATH = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
OLLAMA_URL = os.environ.get("ARIADNE_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_CHAT_MODEL = os.environ.get("ARIADNE_CHAT_MODEL", "gpt-oss:20b")
HOME_CHAT_MODEL = os.environ.get("ARIADNE_HOME_CHAT_MODEL", "qwen3.5:9b-q4_K_M")
HOME_CONTEXT_TOKENS = max(1_024, int(os.environ.get("ARIADNE_HOME_NUM_CTX", "16384")))
HOME_EVENT_LOCK = threading.Lock()
OLLAMA_PRELOAD_KEEP_ALIVE = os.environ.get("ARIADNE_OLLAMA_PRELOAD_KEEP_ALIVE", "5m")
OPEN_WEBUI_URL = os.environ.get("ARIADNE_OPEN_WEBUI_URL", "http://127.0.0.1:3000/")
OPEN_WEBUI_CONTAINER = os.environ.get("ARIADNE_OPEN_WEBUI_CONTAINER", "open-webui")
OPENAI_STATUS_URL = "https://status.openai.com/api/v2/summary.json"
OPENAI_STATUS_CACHE_TTL_SECONDS = 45
VAULT_ROOT = Path(os.environ.get("ARIADNE_VAULT_ROOT", str(PROJECT_ROOT))).expanduser().resolve()
HOME_EVENTS_PATH = VAULT_ROOT / "Journal" / "Ariadne Home Events.md"
VAULT_SYSTEM = VAULT_ROOT / "00_System"
VAULT_WORKER_PATH = ROOT / "vault_worker.py"
VAULT_JOB_ROOT = ROOT / "runtime" / "vault_jobs"
VIDEO_RENDERER_DISTRO = "Ubuntu-24.04"
VIDEO_RENDERER_ROOT = "/root/lmv-comfyui"
VIDEO_RENDERER_PYTHON = "/root/lmv-rocm-venv/bin/python"
VIDEO_RENDERER_APP = "/home/warren/projects/local-music-video-renderer/app.py"
WAN2GP_LOG = ROOT / "runtime" / "linux-renderer.log"
SESSION_TTL_SECONDS = 20
JOB_TIMEOUT_SECONDS = 300
SESSION_LOCK = threading.RLock()
READER_LOCK = threading.Lock()
OPENAI_STATUS_LOCK = threading.Lock()
OPENAI_STATUS_CACHE: dict[str, object] | None = None
OPENAI_STATUS_CACHE_AT = 0.0
SESSIONS: dict[str, dict[str, object]] = {}
JOBS: dict[str, dict[str, object]] = {}
PROFILE_LOCK = threading.RLock()
ACTIVE_PROFILE = "General"
INTERACTIVE_PROCESS: subprocess.Popen | None = None
WAN2GP_PROCESS: subprocess.Popen | None = None
BROWSER_HEARTBEAT_TIMEOUT_SECONDS = 20
LAST_BROWSER_HEARTBEAT = time.monotonic()
LIFECYCLE_THREAD: threading.Thread | None = None
WSL_SESSION_PROCESSES: dict[str, subprocess.Popen] = {}
IDLE_SHUTDOWN_DONE = False
VAULT_ACTIONS = {
    "ingest": ("Daily-Ingest.ps1", []),
    "embedding_status": ("Build-Embeddings.ps1", ["-Status"]),
    "retrieval_evaluation": ("Evaluate-Retrieval.ps1", []),
    "regression_tests": ("Run-Rebuild-Tests.ps1", []),
    "downloads_preview": ("Organize-Downloads.ps1", ["-WhatIf"]),
    "audit_failures": ("Audit-Failed-Ingestion.ps1", []),
    "embedding_rebuild": ("Build-Embeddings.ps1", ["-Rebuild"]),
}




if os.name == "nt":
    _ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class _MouseInput(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long),
            ("dy", ctypes.c_long),
            ("mouse_data", ctypes.c_ulong),
            ("flags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("extra_info", _ULONG_PTR),
        ]

    class _KeyboardInput(ctypes.Structure):
        _fields_ = [
            ("virtual_key", ctypes.c_ushort),
            ("scan_code", ctypes.c_ushort),
            ("flags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("extra_info", _ULONG_PTR),
        ]

    class _HardwareInput(ctypes.Structure):
        _fields_ = [
            ("message", ctypes.c_ulong),
            ("parameter_low", ctypes.c_ushort),
            ("parameter_high", ctypes.c_ushort),
        ]

    class _InputUnion(ctypes.Union):
        _fields_ = [
            ("mouse", _MouseInput),
            ("keyboard", _KeyboardInput),
            ("hardware", _HardwareInput),
        ]

    class _Input(ctypes.Structure):
        _fields_ = [("input_type", ctypes.c_ulong), ("data", _InputUnion)]


def _windows_clipboard_write(text: str) -> None:
    """Replace the Windows Unicode clipboard without involving the browser."""
    if os.name != "nt":
        raise OSError("Windows clipboard handoff is only available on Windows.")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = ctypes.c_bool
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = ctypes.c_bool
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = ctypes.c_bool
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.restype = ctypes.c_void_p

    data = text.encode("utf-16-le") + b"\x00\x00"
    memory_handle = None
    last_error = 0
    try:
        for _ in range(8):
            if user32.OpenClipboard(None):
                break
            last_error = ctypes.get_last_error()
            time.sleep(0.05)
        else:
            raise OSError(last_error or 5, "Windows clipboard is busy.")

        try:
            if not user32.EmptyClipboard():
                raise ctypes.WinError(ctypes.get_last_error())
            memory_handle = kernel32.GlobalAlloc(0x0002 | 0x0040, len(data))
            if not memory_handle:
                raise ctypes.WinError(ctypes.get_last_error())
            locked = kernel32.GlobalLock(memory_handle)
            if not locked:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                ctypes.memmove(locked, data, len(data))
            finally:
                kernel32.GlobalUnlock(memory_handle)
            if not user32.SetClipboardData(13, memory_handle):  # CF_UNICODETEXT
                raise ctypes.WinError(ctypes.get_last_error())
            memory_handle = None  # ownership moved to the clipboard
        finally:
            user32.CloseClipboard()
    finally:
        if memory_handle:
            kernel32.GlobalFree(memory_handle)


def _send_reader_alt_f1() -> int:
    """Inject only the fixed reader shortcut as genuine Windows input."""
    if os.name != "nt":
        raise OSError("Windows reader shortcut is only available on Windows.")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(_Input), ctypes.c_int]
    user32.SendInput.restype = ctypes.c_uint
    key_up = 0x0002
    inputs = (_Input * 4)()
    inputs[0].input_type = 1  # INPUT_KEYBOARD
    inputs[0].data.keyboard.virtual_key = 0xA4  # VK_LMENU / Alt
    inputs[1].input_type = 1
    inputs[1].data.keyboard.virtual_key = 0x70  # VK_F1
    inputs[2].input_type = 1
    inputs[2].data.keyboard.virtual_key = 0x70
    inputs[2].data.keyboard.flags = key_up
    inputs[3].input_type = 1
    inputs[3].data.keyboard.virtual_key = 0xA4
    inputs[3].data.keyboard.flags = key_up
    accepted = int(user32.SendInput(4, inputs, ctypes.sizeof(_Input)))
    if accepted != 4:
        error_code = ctypes.get_last_error()
        raise OSError(error_code or 87, f"Windows accepted {accepted} of 4 reader shortcut events.")
    return accepted


def reader_read(text: str) -> dict[str, object]:
    """Copy an answer and request the existing reader to consume it."""
    with READER_LOCK:
        _windows_clipboard_write(text)
        time.sleep(0.15)
        try:
            accepted = _send_reader_alt_f1()
        except OSError as exc:
            return {"clipboard_ok": True, "hotkey_ok": False, "input_events": 0, "characters": len(text), "hotkey_error": str(exc)}
    return {"clipboard_ok": True, "hotkey_ok": True, "input_events": accepted, "characters": len(text)}


def decode_output(data: bytes) -> str:
    if not data:
        return ""
    if b"\x00" in data[:200]:
        try:
            return data.decode("utf-16le", errors="replace")
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def run_readonly(command: list[str], timeout: float = 4.0) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return decode_output(completed.stdout).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unavailable: {exc}"


def run_action(command: list[str], timeout: float = 60.0) -> dict[str, object]:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = decode_output(completed.stdout).strip()
        return {"ok": completed.returncode == 0, "detail": output}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "detail": str(exc)}


def drive_status(letter: str) -> dict[str, object]:
    path = f"{letter}:\\"
    try:
        total, used, free = shutil.disk_usage(path)
        return {
            "letter": letter,
            "total_gb": round(total / 1_000_000_000, 1),
            "used_gb": round(used / 1_000_000_000, 1),
            "free_gb": round(free / 1_000_000_000, 1),
            "used_percent": round((used / total) * 100, 1) if total else 0,
            "state": "online",
        }
    except OSError:
        return {"letter": letter, "state": "unavailable"}


def usage_state(used_percent: float) -> str:
    if used_percent >= 90:
        return "critical"
    if used_percent >= 75:
        return "warning"
    return "nominal"


def memory_status() -> dict[str, object]:
    """Return physical memory figures without adding a third-party dependency."""
    if os.name != "nt":
        return {"available": False, "detail": "Windows memory telemetry is unavailable on this host."}

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    try:
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("GlobalMemoryStatusEx failed")
    except (AttributeError, OSError) as exc:
        return {"available": False, "detail": str(exc)}

    total = int(status.ullTotalPhys)
    free = int(status.ullAvailPhys)
    used = max(0, total - free)
    used_percent = round((used / total) * 100, 1) if total else 0
    return {
        "available": True,
        "used_gb": round(used / 1024**3, 1),
        "total_gb": round(total / 1024**3, 1),
        "free_gb": round(free / 1024**3, 1),
        "used_percent": used_percent,
        "state": usage_state(used_percent),
    }


def gpu_adapters() -> tuple[list[str], int]:
    """Read the 64-bit VRAM capacity exposed by Windows display drivers."""
    if os.name != "nt":
        return [], 0

    try:
        import winreg

        root = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        names: list[str] = []
        total = 0
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as base:
            for index in range(winreg.QueryInfoKey(base)[0]):
                try:
                    subkey_name = winreg.EnumKey(base, index)
                    with winreg.OpenKey(base, subkey_name) as adapter:
                        name = winreg.QueryValueEx(adapter, "DriverDesc")[0]
                        capacity = winreg.QueryValueEx(adapter, "HardwareInformation.qwMemorySize")[0]
                        if name and isinstance(capacity, int) and capacity > 0:
                            names.append(str(name))
                            total += capacity
                except OSError:
                    continue
        return names, total
    except (ImportError, OSError):
        return [], 0


def typeperf_sum(counter: str) -> float | None:
    raw = run_readonly(["typeperf.exe", counter, "-sc", "1"], timeout=4.0)
    if raw.startswith("unavailable:"):
        return None

    for row in csv.reader(io.StringIO(raw)):
        if len(row) < 2:
            continue
        try:
            values = [float(value) for value in row[1:]]
        except ValueError:
            continue
        if values:
            return sum(values)
    return None


def gpu_status() -> dict[str, object]:
    names, total = gpu_adapters()
    used = typeperf_sum(r"\GPU Adapter Memory(*)\Dedicated Usage")

    # NVIDIA systems can provide a complete reading when Windows adapter
    # registry fields are absent or incomplete.
    if not total or used is None:
        raw = run_readonly(
            [
                "nvidia-smi.exe",
                "--query-gpu=name,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            timeout=4.0,
        )
        nvidia_rows = []
        for line in raw.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) == 3:
                try:
                    nvidia_rows.append((parts[0], float(parts[1]), float(parts[2])))
                except ValueError:
                    continue
        if nvidia_rows:
            names = [row[0] for row in nvidia_rows]
            total = int(sum(row[1] for row in nvidia_rows) * 1024**2)
            used = sum(row[2] for row in nvidia_rows) * 1024**2

    if not total or used is None:
        return {"available": False, "detail": "GPU memory telemetry is unavailable."}

    total = int(total)
    used = min(total, max(0, int(used)))
    free = total - used
    used_percent = round((used / total) * 100, 1) if total else 0
    return {
        "available": True,
        "name": ", ".join(dict.fromkeys(names)) or "GPU",
        "used_gb": round(used / 1024**3, 1),
        "total_gb": round(total / 1024**3, 1),
        "free_gb": round(free / 1024**3, 1),
        "used_percent": used_percent,
        "state": usage_state(used_percent),
    }


def probe_http(url: str, timeout: float = 2.5) -> bool:
    try:
        context = ssl._create_unverified_context() if url.startswith("https://") else None
        with urllib.request.urlopen(url, timeout=timeout, context=context) as response:
            return 200 <= response.status < 500
    except (OSError, urllib.error.URLError):
        return False


def json_http(url: str, timeout: float = 2.5) -> dict[str, object]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    context = ssl._create_unverified_context() if url.startswith("https://") else None
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return json.loads(response.read().decode("utf-8"))


def ollama_catalog() -> dict[str, object]:
    try:
        tags = json_http(f"{OLLAMA_URL}/api/tags")
        models = tags.get("models", []) if isinstance(tags, dict) else []
        running = json_http(f"{OLLAMA_URL}/api/ps")
        loaded = running.get("models", []) if isinstance(running, dict) else []
        model_rows = []
        for item in models:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            model_rows.append({
                "name": str(item["name"]),
                "size": item.get("size"),
                "modified_at": item.get("modified_at"),
            })
        loaded_names = [
            str(item.get("name") or item.get("model"))
            for item in loaded
            if isinstance(item, dict) and (item.get("name") or item.get("model"))
        ]
        return {"available": True, "models": model_rows, "loaded": loaded_names}
    except (OSError, ValueError, TypeError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"available": False, "models": [], "loaded": [], "detail": str(exc)}


def ollama_status() -> dict[str, object]:
    catalog = ollama_catalog()
    if not catalog["available"]:
        return {"available": False, "state": "offline", "detail": catalog.get("detail", "Ollama is unavailable.")}
    models = catalog["models"]
    loaded_names = catalog["loaded"]
    detail = f"{len(models)} local model{'s' if len(models) != 1 else ''} available"
    if loaded_names:
        detail = f"{loaded_names[0]} loaded"
    return {
        "available": True,
        "state": "online",
        "detail": detail,
        "models": len(models),
        "loaded": loaded_names,
    }


def openwebui_status() -> dict[str, object]:
    available = probe_http(OPEN_WEBUI_URL)
    return {
        "available": available,
        "state": "online" if available else "offline",
        "detail": "Open WebUI · local browser interface" if available else "Open WebUI is not running",
    }


def openai_status() -> dict[str, object]:
    """Read the public OpenAI status feed without blocking the dashboard repeatedly."""
    global OPENAI_STATUS_CACHE, OPENAI_STATUS_CACHE_AT
    now = time.monotonic()
    with OPENAI_STATUS_LOCK:
        if OPENAI_STATUS_CACHE and now - OPENAI_STATUS_CACHE_AT < OPENAI_STATUS_CACHE_TTL_SECONDS:
            return dict(OPENAI_STATUS_CACHE)

        try:
            payload = json_http(OPENAI_STATUS_URL, timeout=3.0)
            page_status = payload.get("status", {}) if isinstance(payload, dict) else {}
            incidents = payload.get("incidents", []) if isinstance(payload, dict) else []
            active_incidents = [
                incident for incident in incidents
                if isinstance(incident, dict)
                and str(incident.get("status") or "").lower() not in {"resolved", "completed"}
            ]

            if active_incidents:
                incident = active_incidents[0]
                impact = str(incident.get("impact") or "").lower()
                state = "critical" if impact in {"critical", "major"} else "degraded"
                name = str(incident.get("name") or "OpenAI service incident")
                incident_state = str(incident.get("status") or "ongoing").replace("_", " ").title()
                result = {
                    "available": True,
                    "state": state,
                    "summary": f"{name} · {incident_state}",
                    "detail": f"{name} · {incident_state}. OpenAI status page has the latest update.",
                    "url": "https://status.openai.com/",
                }
            else:
                indicator = str(page_status.get("indicator") or "none").lower()
                state = "online" if indicator in {"none", "operational"} else "critical" if indicator == "critical" else "degraded"
                description = str(page_status.get("description") or "OpenAI status available")
                result = {
                    "available": True,
                    "state": state,
                    "summary": description,
                    "detail": description,
                    "url": "https://status.openai.com/",
                }
            OPENAI_STATUS_CACHE = result
            OPENAI_STATUS_CACHE_AT = now
            return dict(result)
        except (OSError, ValueError, TypeError, urllib.error.URLError, json.JSONDecodeError) as exc:
            if OPENAI_STATUS_CACHE:
                result = dict(OPENAI_STATUS_CACHE)
                result["summary"] = f"{result.get('summary', 'Last known status')} · feed delayed"
                result["detail"] = f"{result.get('detail', 'Last known status')} The latest check failed: {exc}"
                OPENAI_STATUS_CACHE = result
                OPENAI_STATUS_CACHE_AT = now
                return result
            result = {
                "available": False,
                "state": "unknown",
                "summary": "Status feed unavailable",
                "detail": f"Could not read the OpenAI public status feed: {exc}",
                "url": "https://status.openai.com/",
            }
            OPENAI_STATUS_CACHE = result
            OPENAI_STATUS_CACHE_AT = now
            return result


def launch_docker_desktop() -> str:
    if not DOCKER_DESKTOP_PATH.exists():
        return "Docker Desktop launcher was not found."
    try:
        subprocess.Popen(
            [str(DOCKER_DESKTOP_PATH)],
            cwd=str(DOCKER_DESKTOP_PATH.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return "Docker Desktop launch requested."
    except OSError as exc:
        return f"Docker Desktop could not be launched: {exc}"


def start_openwebui_container() -> str | None:
    if not DOCKER_PATH.exists():
        return None
    result = run_action([str(DOCKER_PATH), "start", OPEN_WEBUI_CONTAINER], timeout=4.0)
    if result["ok"]:
        return f"Container '{OPEN_WEBUI_CONTAINER}' started."
    detail = str(result.get("detail") or "Docker did not start the container.")
    return f"Container start not confirmed: {detail}"


def preload_ollama_model(model: str | None = None) -> dict[str, object]:
    selected_model = (model or OLLAMA_CHAT_MODEL).strip() or OLLAMA_CHAT_MODEL
    keep_alive: int | str = OLLAMA_PRELOAD_KEEP_ALIVE
    if OLLAMA_PRELOAD_KEEP_ALIVE.strip().lstrip("-").isdigit():
        keep_alive = int(OLLAMA_PRELOAD_KEEP_ALIVE)
    payload = {
        "model": selected_model,
        "stream": False,
        "keep_alive": keep_alive,
    }
    try:
        response = post_json(f"{OLLAMA_URL}/api/generate", payload, timeout=300.0)
        load_duration = response.get("load_duration")
        detail = f"{selected_model} is loaded in memory"
        if isinstance(load_duration, int):
            detail += f" · load {load_duration / 1_000_000_000:.1f}s"
        return {"ok": True, "model": selected_model, "detail": detail, "response": response}
    except (OSError, urllib.error.URLError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"ok": False, "model": selected_model, "detail": f"Model preload failed: {exc}"}


def launch_openwebui(model: str | None = None) -> dict[str, object]:
    details: list[str] = []
    if not probe_http(OPEN_WEBUI_URL):
        details.append(launch_docker_desktop())
        container_detail = None
        for _ in range(15):
            container_detail = start_openwebui_container()
            if container_detail and container_detail.startswith("Container '"):
                break
            time.sleep(2)
        if container_detail:
            details.append(container_detail)
    preload = preload_ollama_model(model)
    openwebui = openwebui_status()
    ollama = ollama_status()
    loaded_names = [str(name) for name in ollama.get("loaded", [])]
    selected_model = str(preload["model"])
    model_loaded = selected_model in loaded_names
    if not openwebui["available"]:
        details.append("Open WebUI is still starting or its container name differs from the configured name.")
    return {
        "ok": bool(preload["ok"]),
        "url": OPEN_WEBUI_URL,
        "openwebui": openwebui,
        "model": preload["model"],
        "ready": bool(preload["ok"] and openwebui["available"] and model_loaded),
        "ollama": {"state": "online" if preload["ok"] else "offline", "detail": preload["detail"], "loaded": loaded_names},
        "detail": " ".join(details) if details else "Open WebUI is ready and the model preload was requested.",
    }


def container_status(name: str, detail: str) -> dict[str, object]:
    raw = run_readonly([str(DOCKER_PATH), "inspect", "--format", "{{.State.Status}}", name])
    state = raw.strip().lower()
    if state == "running":
        return {"available": True, "state": "online", "detail": detail}
    if state in {"created", "restarting", "paused"}:
        return {"available": True, "state": "starting", "detail": f"{detail} · {state}"}
    return {"available": False, "state": "offline", "detail": f"{detail} · {state or 'not found'}"}


def lmstudio_running() -> bool:
    if os.name != "nt":
        return False
    raw = run_readonly(["tasklist.exe", "/FI", "IMAGENAME eq LM Studio.exe", "/NH"])
    return "LM Studio.exe" in raw


def lmstudio_status() -> dict[str, object]:
    available = lmstudio_running() or probe_http("http://127.0.0.1:1234/v1/models")
    return {
        "available": available,
        "state": "online" if available else "offline",
        "detail": "Desktop app · server 1234" if available else "LM Studio is not running",
    }


def interactive_ai_status() -> dict[str, object]:
    wan2gp = wan2gp_status()
    process_running = INTERACTIVE_PROCESS is not None and INTERACTIVE_PROCESS.poll() is None
    wsl_running = any(item.get("name") == "Ubuntu-24.04" and item.get("state") == "Running" for item in parse_wsl(run_readonly(["wsl.exe", "--list", "--verbose"])))
    return {
        "ubuntu": {"state": "online" if (process_running or wsl_running) else "offline", "detail": "Ubuntu 24.04 Linux Environment · WSL 2 · ROCm"},
        "wan2gp": wan2gp,
    }


def wan2gp_status() -> dict[str, object]:
    global WAN2GP_PROCESS
    try:
        renderer = json_http("http://127.0.0.1:8766/api/status")
        if renderer.get("online"):
            return {"state": "online", "detail": "Local Music Video Renderer · GPU backend ready · Ubuntu 24.04"}
        renderer_state = str(renderer.get("state") or "").lower()
        if renderer_state == "starting":
            return {"state": "starting", "detail": "Local Music Video Renderer · GPU backend is starting"}
        if renderer_state in {"stopped", "idle"}:
            return {"state": "standby", "detail": "Local Music Video Renderer · GPU backend unloaded after idle"}
        renderer_error = renderer.get("error")
        if renderer_error:
            return {"state": "error", "detail": f"Local Music Video Renderer · {renderer_error}"}
    except (OSError, urllib.error.URLError, ValueError, TypeError, json.JSONDecodeError):
        pass
    if WAN2GP_PROCESS is not None:
        returncode = WAN2GP_PROCESS.poll()
        if returncode is None:
            return {"state": "starting", "detail": "Linux video renderer is starting - ROCm environment loading"}
        if returncode != 0:
            return {"state": "error", "detail": f"Linux video renderer stopped during startup - exit {returncode} - see runtime/linux-renderer.log"}
        WAN2GP_PROCESS = None
    return {"state": "offline", "detail": "Linux video renderer is stopped - port 8766 is not listening"}


def start_wan2gp() -> dict[str, object]:
    global WAN2GP_PROCESS

    def start_renderer_backend() -> dict[str, object]:
        renderer = None
        for _ in range(40):
            try:
                renderer = json_http("http://127.0.0.1:8766/api/status")
                break
            except (OSError, urllib.error.URLError, ValueError, TypeError, json.JSONDecodeError):
                time.sleep(0.5)
        if renderer is None:
            current_status = wan2gp_status()
            if current_status["state"] == "error":
                return {"ok": False, "message": current_status["detail"], "wan2gp": current_status}
            return {"ok": True, "wan2gp": current_status}
        if renderer.get("online"):
            return {"ok": True, "wan2gp": {"state": "online", "detail": "Local Music Video Renderer · GPU backend ready · Ubuntu 24.04"}}
        try:
            response = post_json("http://127.0.0.1:8766/api/start", {})
        except (OSError, urllib.error.URLError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return {"ok": False, "message": f"Could not start the Linux GPU backend: {exc}", "wan2gp": {"state": "error", "detail": str(exc)}}
        status = response.get("status") or {}
        if status.get("online"):
            return {"ok": True, "wan2gp": {"state": "online", "detail": "Local Music Video Renderer · GPU backend ready · Ubuntu 24.04"}}
        if str(status.get("state") or "").lower() == "starting":
            return {"ok": True, "wan2gp": {"state": "starting", "detail": "Local Music Video Renderer · GPU backend is starting"}}
        detail = status.get("error") or "The GPU backend did not become ready."
        return {"ok": False, "message": f"Linux video renderer failed to start: {detail}", "wan2gp": {"state": "error", "detail": str(detail)}}

    with PROFILE_LOCK:
        current = wan2gp_status()
        if current["state"] == "online":
            return {"ok": True, "wan2gp": current}
        if current["state"] == "standby":
            return start_renderer_backend()
        if current["state"] == "starting":
            return {"ok": True, "wan2gp": current}
        WAN2GP_LOG.parent.mkdir(parents=True, exist_ok=True)
        log_handle = WAN2GP_LOG.open("a", encoding="utf-8", buffering=1)
        try:
            environment = os.environ.copy()
            environment.update({"PYTHONUNBUFFERED": "1"})
            WAN2GP_PROCESS = subprocess.Popen(
                [
                    "wsl.exe", "-d", VIDEO_RENDERER_DISTRO, "--user", "root", "--",
                    VIDEO_RENDERER_PYTHON, VIDEO_RENDERER_APP, "--renderer-app",
                ],
                cwd=ROOT,
                env=environment,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        finally:
            log_handle.close()
        return start_renderer_backend()

def stop_wan2gp() -> dict[str, object]:
    global WAN2GP_PROCESS
    with PROFILE_LOCK:
        try:
            renderer = json_http("http://127.0.0.1:8766/api/status")
            if renderer.get("online"):
                post_json("http://127.0.0.1:8766/api/stop", {})
        except (OSError, urllib.error.URLError, ValueError, TypeError, json.JSONDecodeError):
            pass
        process = WAN2GP_PROCESS
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=4)
        WAN2GP_PROCESS = None
    return {"ok": True, "wan2gp": wan2gp_status()}


def stop_interactive_session() -> None:
    global INTERACTIVE_PROCESS
    with PROFILE_LOCK:
        process = INTERACTIVE_PROCESS
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=4)
        INTERACTIVE_PROCESS = None


def renderer_is_busy() -> bool:
    try:
        renderer = json_http("http://127.0.0.1:8766/api/status")
    except (OSError, urllib.error.URLError, ValueError, TypeError, json.JSONDecodeError):
        return False
    clip = renderer.get("clip") if isinstance(renderer, dict) else None
    return isinstance(clip, dict) and clip.get("state") in {"queued", "running"}


def release_workloads(force: bool = False) -> None:
    global ACTIVE_PROFILE
    if not force and renderer_is_busy():
        return
    stop_wan2gp()
    stop_interactive_session()
    run_readonly(["wsl.exe", "--terminate", VIDEO_RENDERER_DISTRO])
    ACTIVE_PROFILE = "General"

def start_lifecycle_watchdog() -> None:
    global LIFECYCLE_THREAD
    if LIFECYCLE_THREAD is not None and LIFECYCLE_THREAD.is_alive():
        return

    def watch() -> None:
        while True:
            time.sleep(5)
            if time.monotonic() - LAST_BROWSER_HEARTBEAT > BROWSER_HEARTBEAT_TIMEOUT_SECONDS:
                shutdown_idle_workloads()

    LIFECYCLE_THREAD = threading.Thread(target=watch, name="ariadne-lifecycle", daemon=True)
    LIFECYCLE_THREAD.start()


def set_profile(profile: str) -> dict[str, object]:
    global ACTIVE_PROFILE, INTERACTIVE_PROCESS
    if profile not in {"General", "Interactive AI"}:
        raise ValueError("Unknown Ariadne profile.")
    with PROFILE_LOCK:
        if profile == "Interactive AI":
            if INTERACTIVE_PROCESS is None or INTERACTIVE_PROCESS.poll() is not None:
                INTERACTIVE_PROCESS = subprocess.Popen(
                    ["wsl.exe", "-d", "Ubuntu-24.04", "--exec", "sleep", "infinity"],
                    cwd=ROOT,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
        elif INTERACTIVE_PROCESS is not None and INTERACTIVE_PROCESS.poll() is None:
            stop_interactive_session()
        ACTIVE_PROFILE = profile
    return {"ok": True, "profile": profile, "interactive_ai": interactive_ai_status()}


def launch_lmstudio() -> None:
    if os.name != "nt" or not LM_STUDIO_PATH.exists():
        return
    # LM Studio may leave background Electron processes running without a
    # visible window. Invoking the executable again lets its single-instance
    # handler restore or activate the existing GUI.
    subprocess.Popen(
        [str(LM_STUDIO_PATH)],
        cwd=str(LM_STUDIO_PATH.parent),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def quick_launch_status() -> dict[str, object]:
    return {
        "synology": {
            "available": probe_http("http://192.168.1.200:5000/"),
            "state": "online" if probe_http("http://192.168.1.200:5000/") else "offline",
            "detail": "Hera NAS · DSM",
        },
        "ollama": ollama_status(),
        "openwebui": openwebui_status(),
        "openai": openai_status(),
        "portainer": container_status("portainer", "Docker management · HTTP 9000"),
        "lmstudio": lmstudio_status(),
        "ariadne-control": ariadne_control_status(),
    }

def post_json(url: str, payload: dict[str, object], timeout: float = 10.0) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def vault_control_available() -> bool:
    required = (VAULT_ROOT, VAULT_SYSTEM / "ariadne_mcp.py", VAULT_SYSTEM / "Ariadne-Control.html")
    return all(path.exists() for path in required)


def ariadne_control_status() -> dict[str, object]:
    available = vault_control_available()
    return {
        "available": available,
        "state": "online" if available else "offline",
        "detail": "Knowledge Vault · integrated controls" if available else "Knowledge Vault files unavailable",
    }


def _unload_ollama_models() -> None:
    try:
        running = json_http("http://127.0.0.1:11434/api/ps", timeout=3.0)
        models = running.get("models", []) if isinstance(running, dict) else []
        for item in models:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("model")
            if isinstance(name, str) and name:
                try:
                    post_json("http://127.0.0.1:11434/api/generate", {"model": name, "keep_alive": 0}, timeout=8.0)
                except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
                    continue
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
        return


def shutdown_idle_workloads() -> None:
    """Release managed resources once no Ariadne browser session remains."""
    global IDLE_SHUTDOWN_DONE
    with SESSION_LOCK:
        if SESSIONS:
            IDLE_SHUTDOWN_DONE = False
            return
        if IDLE_SHUTDOWN_DONE:
            return
        IDLE_SHUTDOWN_DONE = True
    _unload_ollama_models()
    release_workloads(force=True)


def _terminate_process(process: object) -> None:
    if not isinstance(process, subprocess.Popen) or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _close_session(session_id: str) -> bool:
    with SESSION_LOCK:
        session = SESSIONS.pop(session_id, None)
        if not session:
            return False
        job_ids = list(session.get("jobs", set()))
        jobs = [JOBS.get(job_id) for job_id in job_ids]
        last_session = not SESSIONS
    for job in jobs:
        if not job:
            continue
        _terminate_process(job.get("process"))
        with SESSION_LOCK:
            job["state"] = "cancelled"
            job["message"] = "Cancelled when the Ariadne page closed."
    if last_session:
        shutdown_idle_workloads()
    return True


def _expire_sessions() -> None:
    now = time.monotonic()
    with SESSION_LOCK:
        expired = [session_id for session_id, session in SESSIONS.items()
                   if now - float(session.get("last_seen", 0)) > SESSION_TTL_SECONDS]
    for session_id in expired:
        _close_session(session_id)


def _session(session_id: object) -> dict[str, object] | None:
    if not isinstance(session_id, str) or not session_id:
        return None
    with SESSION_LOCK:
        session = SESSIONS.get(session_id)
        if session:
            session["last_seen"] = time.monotonic()
        return session


def _start_process(command: list[str], cwd: Path) -> subprocess.Popen:
    return subprocess.Popen(
        command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _watch_action(job_id: str, process: subprocess.Popen) -> None:
    output: list[str] = []
    if process.stdout:
        for line in process.stdout:
            output.append(line.rstrip())
            output[:] = output[-80:]
    return_code = process.wait()
    with SESSION_LOCK:
        job = JOBS.get(job_id)
        if not job or job.get("state") == "cancelled":
            return
        job["state"] = "complete" if return_code == 0 else "error"
        job["message"] = "Operation complete." if return_code == 0 else f"Operation exited with code {return_code}."
        job["output"] = "\n".join(output)[-8000:]


def _timeout_job(job_id: str) -> None:
    time.sleep(JOB_TIMEOUT_SECONDS)
    with SESSION_LOCK:
        job = JOBS.get(job_id)
        if not job or job.get("state") != "running":
            return
        process = job.get("process")
    _terminate_process(process)
    with SESSION_LOCK:
        job = JOBS.get(job_id)
        if job and job.get("state") == "running":
            job["state"] = "error"
            job["message"] = "Worker timed out and was terminated after five minutes."

def start_vault_action(session_id: str, action: str) -> str:
    script_name, arguments = VAULT_ACTIONS[action]
    script_path = VAULT_SYSTEM / script_name
    if not script_path.is_file():
        raise FileNotFoundError(f"Knowledge Vault workflow not found: {script_path}")
    shell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if not shell:
        raise RuntimeError("PowerShell is not available.")
    process = _start_process(
        [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script_path), *arguments],
        VAULT_ROOT,
    )
    job_id = uuid.uuid4().hex
    job = {"session_id": session_id, "kind": "action", "process": process, "started": time.monotonic(),
           "state": "running", "message": "Starting…", "action": action}
    with SESSION_LOCK:
        JOBS[job_id] = job
        SESSIONS[session_id].setdefault("jobs", set()).add(job_id)
    threading.Thread(target=_watch_action, args=(job_id, process), daemon=True).start()
    threading.Thread(target=_timeout_job, args=(job_id,), daemon=True).start()
    return job_id


def start_vault_query(session_id: str, query: str, mode: str, limit: int) -> str:
    VAULT_JOB_ROOT.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    spec_path = VAULT_JOB_ROOT / f"{job_id}.json"
    status_path = Path(str(spec_path) + ".status.json")
    spec_path.write_text(json.dumps({"query": query, "mode": mode, "limit": limit}, ensure_ascii=False), encoding="utf-8")
    status_path.write_text(json.dumps({"state": "starting", "stage": "starting", "message": "Starting vault worker…", "completed": 0, "total": 0}), encoding="utf-8")
    process = _start_process([sys.executable, "-u", str(VAULT_WORKER_PATH), str(spec_path)], VAULT_ROOT)
    job = {"session_id": session_id, "kind": "query", "process": process, "started": time.monotonic(),
           "state": "running", "message": "Starting vault worker…", "spec_path": spec_path, "status_path": status_path, "mode": mode}
    with SESSION_LOCK:
        JOBS[job_id] = job
        SESSIONS[session_id].setdefault("jobs", set()).add(job_id)
        SESSIONS[session_id]["used_ollama"] = True
    threading.Thread(target=_timeout_job, args=(job_id,), daemon=True).start()
    return job_id


def job_payload(job_id: str) -> dict[str, object] | None:
    with SESSION_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
        process = job.get("process")
        if isinstance(process, subprocess.Popen) and process.poll() is not None and job.get("state") == "running":
            job["state"] = "error"
            job["message"] = "Worker exited before reporting a result."
        result = {key: value for key, value in job.items() if key not in {"process", "spec_path", "status_path"}}
        status_path = job.get("status_path")
        if isinstance(status_path, Path) and status_path.is_file():
            try:
                result.update(json.loads(status_path.read_text(encoding="utf-8")))
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        result["job_id"] = job_id
        result.pop("started", None)
        return result


def vault_session_status() -> dict[str, object]:
    with SESSION_LOCK:
        active = len(SESSIONS)
        jobs = sum(1 for job in JOBS.values() if job.get("state") == "running")
    return {"available": vault_control_available(), "active_sessions": active, "active_jobs": jobs,
            "detail": "Integrated Knowledge Vault controls" if vault_control_available() else "Vault unavailable"}

def parse_wsl(raw: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for line in raw.splitlines():
        line = line.strip().lstrip("* ")
        if not line or line.lower().startswith("name") or set(line) <= {"-", " "}:
            continue
        parts = line.split()
        if len(parts) >= 3 and parts[-1] in {"1", "2"}:
            entries.append(
                {
                    "name": " ".join(parts[:-2]),
                    "state": parts[-2],
                    "version": parts[-1],
                }
            )
    return entries


def wsl_environment_action(name: str, action: str) -> dict[str, object]:
    global INTERACTIVE_PROCESS
    if name not in {"Ubuntu", "Ubuntu-24.04", "docker-desktop"}:
        return {"ok": False, "message": "That WSL environment is not an allowed Ariadne target."}
    if action not in {"start", "stop"}:
        return {"ok": False, "message": "Unknown environment action."}

    if name == "docker-desktop":
        if action == "start":
            launch_detail = launch_docker_desktop()
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                docker = docker_status()
                if docker.get("available"):
                    return {"ok": True, "message": "Docker Desktop is running.", "docker": docker}
                time.sleep(1)
            return {"ok": False, "message": f"{launch_detail} Docker engine is not ready yet.", "docker": docker_status()}
        result = run_action(["taskkill.exe", "/IM", "Docker Desktop.exe", "/T", "/F"], timeout=20.0)
        if not result["ok"]:
            return {"ok": False, "message": f"Docker Desktop stop failed: {result['detail'] or 'unknown error'}"}
        return {"ok": True, "message": "Docker Desktop stopped.", "docker": docker_status()}

    if action == "start":
        existing = INTERACTIVE_PROCESS if name == VIDEO_RENDERER_DISTRO else WSL_SESSION_PROCESSES.get(name)
        if existing is not None and existing.poll() is None:
            return {"ok": True, "message": f"{name} is already running."}
        try:
            process = subprocess.Popen(
                ["wsl.exe", "-d", name, "--user", "root", "--exec", "sleep", "infinity"],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            return {"ok": False, "message": f"Could not start {name}: {exc}"}
        if name == VIDEO_RENDERER_DISTRO:
            INTERACTIVE_PROCESS = process
        else:
            WSL_SESSION_PROCESSES[name] = process
        time.sleep(0.8)
        state = next((item for item in parse_wsl(run_readonly(["wsl.exe", "--list", "--verbose"])) if item["name"] == name), {})
        if state.get("state", "").lower() == "running":
            return {"ok": True, "message": f"{name} is running.", "wsl": state}
        return {"ok": False, "message": f"{name} did not reach the running state."}

    if name == VIDEO_RENDERER_DISTRO:
        stop_wan2gp()
        stop_interactive_session()
    else:
        process = WSL_SESSION_PROCESSES.pop(name, None)
        if process is not None and process.poll() is None:
            process.terminate()
    result = run_action(["wsl.exe", "--terminate", name], timeout=30.0)
    if not result["ok"]:
        return {"ok": False, "message": f"Could not stop {name}: {result['detail'] or 'unknown error'}"}
    return {"ok": True, "message": f"{name} stopped."}


def docker_status() -> dict[str, object]:
    raw = run_readonly(
        [
            str(DOCKER_PATH),
            "ps",
            "-a",
            "--format",
            "{{.Names}}|{{.Status}}|{{.Image}}",
        ]
    )
    normalized = raw.strip().lower()
    docker_unavailable_markers = (
        "failed to connect to the docker api",
        "cannot connect to the docker daemon",
        "is the docker daemon running",
        "cannot find the file specified",
        "permission denied while trying to connect to the docker api",
        "unavailable:",
    )
    if any(marker in normalized for marker in docker_unavailable_markers):
        return {
            "available": False,
            "state": "offline",
            "containers": [],
            "detail": "Docker Desktop is not started.",
        }
    containers = []
    for line in raw.splitlines():
        name, _, remainder = line.partition("|")
        status, _, image = remainder.partition("|")
        if name and status and image:
            containers.append({"name": name, "status": status, "image": image})
    return {"available": True, "state": "online", "containers": containers}

def _clean_home_event_text(value: object, limit: int = 420) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    return text.replace("·", "/").replace(" — ", " - ")[:limit].strip()


def record_home_event(kind: str, summary: str, source: str = "Ariadne Home") -> None:
    """Append one bounded, human-readable event to the intended Journal file."""
    safe_kind = _clean_home_event_text(kind, 80) or "event"
    safe_summary = _clean_home_event_text(summary)
    safe_source = _clean_home_event_text(source, 100) or "Ariadne Home"
    try:
        with HOME_EVENT_LOCK:
            HOME_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
            if not HOME_EVENTS_PATH.exists():
                HOME_EVENTS_PATH.write_text("# Ariadne Home Events\n\n", encoding="utf-8")
            timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
            with HOME_EVENTS_PATH.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"- {timestamp} · {safe_kind} · {safe_summary} · source={safe_source}\n")
    except OSError as exc:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Home event was not recorded: {exc}")


def read_home_events(limit: int = 12) -> list[dict[str, str]]:
    try:
        lines = HOME_EVENTS_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[dict[str, str]] = []
    for line in reversed(lines):
        if not line.startswith("- "):
            continue
        parts = line[2:].split(" · ", 3)
        if len(parts) != 4:
            continue
        timestamp, kind, summary, source = parts
        events.append({
            "timestamp": timestamp,
            "kind": kind,
            "summary": summary,
            "source": source.removeprefix("source="),
        })
        if len(events) >= limit:
            break
    return events


def home_index_status() -> dict[str, object]:
    path = VAULT_SYSTEM / "Data" / "embedding-index.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = payload.get("entries", {}) if isinstance(payload, dict) else {}
        count = len(entries) if isinstance(entries, dict) else 0
        updated = payload.get("updated_at") if isinstance(payload, dict) else None
        return {
            "state": "healthy" if count else "attention",
            "detail": f"{count:,} semantic passages indexed" if count else "Semantic index exists but contains no passages",
            "entries": count,
            "updated_at": updated,
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {
            "state": "attention",
            "detail": "Semantic index has not been built on this machine.",
            "entries": 0,
            "updated_at": None,
        }


def configured_ollama_store() -> str:
    value = os.environ.get("OLLAMA_MODELS", "").strip()
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                user_value, _ = winreg.QueryValueEx(key, "OLLAMA_MODELS")
                if isinstance(user_value, str) and user_value.strip():
                    value = user_value.strip()
        except (FileNotFoundError, OSError):
            pass
    return value or "not set"

def home_health_payload() -> dict[str, object]:
    services: list[dict[str, object]] = []

    def add(name: str, state: str, detail: str) -> None:
        services.append({"name": name, "state": state, "detail": detail})

    add("Ariadne backend", "healthy", "Home API is responding on loopback.")
    vault_ready = vault_control_available()
    add(
        "Knowledge Vault",
        "healthy" if vault_ready else "offline",
        "Markdown catalogue and control files are available." if vault_ready else "Vault files are not available.",
    )
    retrieval_ready = (VAULT_SYSTEM / "ariadne_mcp.py").is_file() and (VAULT_SYSTEM / "library.json").is_file()
    add(
        "MCP / retrieval",
        "healthy" if retrieval_ready else "offline",
        "Read-only retrieval path is ready." if retrieval_ready else "Retrieval source files are unavailable.",
    )
    ollama = ollama_status()
    add(
        "Ollama",
        "healthy" if ollama.get("state") == "online" else "offline",
        str(ollama.get("detail") or "Ollama is unavailable."),
    )
    index = home_index_status()
    add("Semantic index", str(index["state"]), str(index["detail"]))

    states = {str(item["state"]) for item in services}
    overall = "healthy" if states == {"healthy"} else "offline" if "offline" in states and states <= {"healthy", "offline"} else "attention"
    return {
        "overall": overall,
        "services": services,
        "resident_model": HOME_CHAT_MODEL,
        "context_tokens": HOME_CONTEXT_TOKENS,
        "ollama_store": configured_ollama_store(),
        "ollama": ollama,
        "index": index,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def home_today_payload(health: dict[str, object]) -> list[dict[str, str]]:
    signals: list[dict[str, str]] = []
    for service in health.get("services", []):
        if not isinstance(service, dict) or service.get("state") == "healthy":
            continue
        signals.append({
            "label": str(service.get("name") or "System"),
            "detail": str(service.get("detail") or "Needs attention."),
            "tone": "offline" if service.get("state") == "offline" else "attention",
        })
    catalogue = VAULT_SYSTEM / "library.json"
    try:
        changed = datetime.fromtimestamp(catalogue.stat().st_mtime).astimezone().strftime("%d %b %H:%M")
        signals.append({"label": "Vault catalogue", "detail": f"Last changed {changed}.", "tone": "quiet"})
    except OSError:
        pass
    if not signals:
        signals.append({"label": "System attention", "detail": "No local attention items are currently reported.", "tone": "healthy"})
    return signals[:6]


def home_query_requires_vault(query: str) -> bool:
    terms = (
        "my ", " i ", "wazza", "warren", "chanya", "ariadne", "knowledge vault",
        "knowledgevault", "project", "decision", "setup", "remember", "what do you know",
        "where did", "when did", "retirement", "garage alchemy", "pope kael",
    )
    folded = f" {query.casefold()} "
    return any(term in folded for term in terms)


def _home_mcp():
    if str(VAULT_SYSTEM) not in sys.path:
        sys.path.insert(0, str(VAULT_SYSTEM))
    import ariadne_mcp
    return ariadne_mcp


def home_chat_payload(query: str, history: object, vault_mode: str = "auto") -> dict[str, object]:
    query = query.strip()
    if not query:
        raise ValueError("A non-empty question is required.")
    if len(query) > 8_000:
        raise ValueError("Keep the question below 8,000 characters.")
    mode = vault_mode if vault_mode in {"auto", "always", "never"} else "auto"
    use_vault = mode == "always" or (mode == "auto" and home_query_requires_vault(query))
    mcp = _home_mcp()
    safe_history: list[dict[str, str]] = []
    if isinstance(history, list):
        for item in history[-8:]:
            if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
                continue
            content = str(item.get("content") or "").strip()
            if content:
                safe_history.append({"role": str(item["role"]), "content": content[:4_000]})
    record_home_event("question_submitted", query[:300])
    try:
        if use_vault:
            result = mcp.planned_knowledge_query(
                query,
                limit=6,
                answer_mode="answer",
                model=HOME_CHAT_MODEL,
                context_tokens=HOME_CONTEXT_TOKENS,
            )
            answer = str(result.get("summary") or "The local librarian returned no answer.")
            sources = result.get("sources") if isinstance(result.get("sources"), list) else []
            retrieval = {
                "match_count": len(sources),
                "sources": sources,
                "searches": result.get("searches", []),
            }
            record_home_event("vault_retrieval_performed", f"Retrieved {len(sources)} cited passage(s) for this question.")
        else:
            identity, identity_meta = mcp.identity_system_prefix()
            system = identity + (
                "You are Ariadne Home, Warren's local conversational assistant. "
                "Answer clearly and directly. Keep identity, conversation state, retrieved knowledge, "
                "and system output separate. Do not claim to have used the Knowledge Vault unless it was supplied. "
                "If you do not know something, say so plainly."
            )
            messages = [{"role": "system", "content": system}, *safe_history, {"role": "user", "content": query}]
            answer = mcp.ollama_chat(messages, model=HOME_CHAT_MODEL, context_tokens=HOME_CONTEXT_TOKENS)
            sources = []
            retrieval = {"match_count": 0, "sources": []}
        record_home_event("model_response_completed", f"Local {HOME_CHAT_MODEL} response completed.")
        return {
            "ok": True,
            "answer": answer,
            "model": HOME_CHAT_MODEL,
            "context_tokens": HOME_CONTEXT_TOKENS,
            "used_vault": use_vault,
            "sources": sources,
            "retrieval": retrieval,
        }
    except Exception as exc:
        record_home_event("significant_error", f"Ask Ariadne failed: {exc}", source="Ariadne Home")
        raise


def home_activity_payload() -> dict[str, object]:
    health = home_health_payload()
    return {
        "today": home_today_payload(health),
        "activity": read_home_events(),
        "health": health,
    }


def status_payload() -> dict[str, object]:
    global LAST_BROWSER_HEARTBEAT
    LAST_BROWSER_HEARTBEAT = time.monotonic()
    wsl_raw = run_readonly(["wsl.exe", "--list", "--verbose"])
    return {
        "service": "online",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "host": os.environ.get("COMPUTERNAME", "Windows host"),
        "profile": ACTIVE_PROFILE,
        "profile_detail": "Selected Linux services" if ACTIVE_PROFILE == "Interactive AI" else "Read-only foundation",
        "interactive_ai": interactive_ai_status(),
        "memory": memory_status(),
        "gpu": gpu_status(),
        "quick_launch": quick_launch_status(),
        "vault": vault_session_status(),
        "drives": [drive_status(letter) for letter in ("C", "D", "E", "F", "G")],
        "wsl": parse_wsl(wsl_raw),
        "docker": docker_status(),
        "controls_enabled": True,
        "note": "Knowledge Vault controls run inside an active Ariadne session; workers are bounded and cleaned up when the session ends.",
    }


class AriadneHandler(BaseHTTPRequestHandler):
    server_version = "AriadneLocal/0.1"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {format % args}")

    def send_bytes(self, payload: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def send_asset(self, filename: str, content_type: str) -> None:
        try:
            payload = (ROOT / filename).read_bytes()
            self.send_bytes(payload, content_type)
        except OSError as exc:
            self.log_message("asset failure for %s: %s", filename, exc)
            self.send_bytes(f"Ariadne asset unavailable: {filename}".encode("utf-8"), "text/plain; charset=utf-8", 500)

    def send_json(self, payload: dict[str, object], status: int = 200) -> None:
        self.send_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 64_000:
            raise ValueError("Request body is too large.")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object.")
        return value

    def do_GET(self) -> None:  # noqa: N802
        _expire_sessions()
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/home/health":
            self.send_json(home_health_payload())
            return
        if path == "/api/home/activity":
            self.send_json(home_activity_payload())
            return
        if path == "/launch/lmstudio":
            launch_lmstudio()
            self.send_json({"launched": True, "detail": "LM Studio launch requested."})
            return
        if path == "/launch/openwebui":
            self.send_json(launch_openwebui())
            return
        if path == "/api/openwebui/models":
            catalog = ollama_catalog()
            self.send_json({
                "ok": bool(catalog["available"]),
                "default_model": OLLAMA_CHAT_MODEL,
                "models": catalog.get("models", []),
                "loaded": catalog.get("loaded", []),
                "openwebui": openwebui_status(),
                "detail": catalog.get("detail", "Ollama is ready."),
            })
            return
        if path == "/api/status":
            self.send_json(status_payload())
            return
        if path == "/api/vault/jobs/" or path.startswith("/api/vault/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            query = dict(item.split("=", 1) for item in parsed.query.split("&") if "=" in item)
            session_id = query.get("session_id")
            with SESSION_LOCK:
                job = JOBS.get(job_id)
                permitted = bool(job and job.get("session_id") == session_id and session_id in SESSIONS)
            if not permitted:
                self.send_json({"ok": False, "message": "Unknown or inactive Ariadne session."}, 404)
                return
            payload = job_payload(job_id)
            if payload is None:
                self.send_json({"ok": False, "message": "Job not found."}, 404)
            else:
                self.send_json(payload)
            return
        if path in {"/", "/home"}:
            record_home_event("home_opened", "Ariadne Home opened.")
            self.send_asset("home.html", "text/html; charset=utf-8")
            return
        if path in {"/system-details", "/details", "/index.html"}:
            self.send_asset("index.html", "text/html; charset=utf-8")
            return
        if path == "/home.css":
            self.send_asset("home.css", "text/css; charset=utf-8")
            return
        if path == "/home.js":
            self.send_asset("home.js", "text/javascript; charset=utf-8")
            return
        if path in {"/", "/index.html"}:
            self.send_asset("index.html", "text/html; charset=utf-8")
            return
        if path == "/styles.css":
            self.send_asset("styles.css", "text/css; charset=utf-8")
            return
        if path == "/ariadne-network-backdrop.png":
            self.send_asset("ariadne-network-backdrop.png", "image/png")
            return
        if path == "/ariadne-original.png":
            self.send_asset("ariadne-original.png", "image/png")
            return
        if path == "/openwebui-loader":
            self.send_asset("openwebui-loader.html", "text/html; charset=utf-8")
            return
        if path == "/openwebui-loader.js":
            self.send_asset("openwebui-loader.js", "text/javascript; charset=utf-8")
            return
        if path == "/favicon.svg":
            self.send_asset("favicon.svg", "image/svg+xml")
            return
        if path == "/app.js":
            self.send_asset("app.js", "text/javascript; charset=utf-8")
            return
        self.send_bytes(b"Not found", "text/plain; charset=utf-8", 404)

    def do_POST(self) -> None:  # noqa: N802
        _expire_sessions()
        path = urlparse(self.path).path
        try:
            body = self.read_json()
            if path == "/api/wsl/start":
                self.send_json(wsl_environment_action(str(body.get("name") or ""), "start"))
                return
            if path == "/api/wsl/stop":
                self.send_json(wsl_environment_action(str(body.get("name") or ""), "stop"))
                return
            if path == "/api/docker/start":
                self.send_json(wsl_environment_action("docker-desktop", "start"))
                return
            if path == "/api/docker/stop":
                self.send_json(wsl_environment_action("docker-desktop", "stop"))
                return
            if path == "/api/openwebui/prepare":
                model = body.get("model")
                if model is not None and not isinstance(model, str):
                    self.send_json({"ok": False, "detail": "Model name must be text."}, 400)
                    return
                self.send_json(launch_openwebui(model))
                return
            if path == "/api/profile":
                profile = body.get("profile")
                if not isinstance(profile, str):
                    self.send_json({"ok": False, "message": "A profile name is required."}, 400)
                    return
                self.send_json(set_profile(profile))
                return
            if path == "/api/wan2gp/start":
                self.send_json(start_wan2gp())
                return
            if path == "/api/wan2gp/stop":
                self.send_json(stop_wan2gp())
                return
            if path == "/api/session/start":
                session_id = uuid.uuid4().hex
                global IDLE_SHUTDOWN_DONE
                IDLE_SHUTDOWN_DONE = False
                with SESSION_LOCK:
                    SESSIONS[session_id] = {"last_seen": time.monotonic(), "jobs": set(), "used_ollama": False}
                record_home_event("session_started", "Ariadne session started.")
                self.send_json({"ok": True, "session_id": session_id, "heartbeat_seconds": 5})
                return
            if path in {"/api/session/heartbeat", "/api/session/close"}:
                session_id = body.get("session_id")
                if path.endswith("heartbeat"):
                    if not _session(session_id):
                        self.send_json({"ok": False, "message": "Ariadne session is not active."}, 404)
                    else:
                        self.send_json({"ok": True, "session": vault_session_status()})
                    return
                if not _close_session(str(session_id or "")):
                    self.send_json({"ok": False, "message": "Ariadne session is already closed."}, 404)
                else:
                    self.send_json({"ok": True, "message": "Ariadne session closed; active workers cancelled."})
                return
            session_id = body.get("session_id")
            if not _session(session_id):
                self.send_json({"ok": False, "message": "Start an Ariadne session first."}, 409)
                return
            session_id = str(session_id)
            if path == "/reader/read":
                answer = body.get("answer")
                if not isinstance(answer, str) or not answer.strip():
                    self.send_json({"ok": False, "clipboard_ok": False, "hotkey_ok": False, "message": "A non-empty answer is required."}, 400)
                    return
                if len(answer) > 50_000:
                    self.send_json({"ok": False, "clipboard_ok": False, "hotkey_ok": False, "message": "The answer is too large for the reader handoff."}, 413)
                    return
                try:
                    result = reader_read(answer)
                except OSError as exc:
                    self.send_json({"ok": False, "clipboard_ok": False, "hotkey_ok": False, "message": f"Could not copy the answer to the Windows clipboard: {exc}"}, 500)
                    return
                if not result["hotkey_ok"]:
                    self.send_json({"ok": False, **result, "message": "Answer copied to the Windows clipboard, but Alt+F1 could not be sent. Press Alt+F1 manually."}, 503)
                    return
                self.send_json({"ok": True, **result, "message": "Answer copied to the Windows clipboard and Alt+F1 sent."})
                return
            if path == "/api/home/chat":
                query = body.get("message")
                history = body.get("history", [])
                vault_mode = body.get("vault_mode", "auto")
                if not isinstance(query, str):
                    self.send_json({"ok": False, "message": "A text question is required."}, 400)
                    return
                if not isinstance(vault_mode, str):
                    vault_mode = "auto"
                with SESSION_LOCK:
                    SESSIONS[session_id]["used_ollama"] = True
                self.send_json(home_chat_payload(query, history, vault_mode))
                return
            if path == "/api/vault/run":
                action = body.get("action")
                if not isinstance(action, str) or action not in VAULT_ACTIONS:
                    self.send_json({"ok": False, "message": "Unknown Knowledge Vault operation."}, 400)
                    return
                self.send_json({"ok": True, "job_id": start_vault_action(session_id, action)})
                return
            if path == "/api/vault/query":
                query = body.get("query")
                mode = body.get("mode", "search")
                limit = body.get("limit", 8)
                if not isinstance(query, str) or not query.strip():
                    self.send_json({"ok": False, "message": "A non-empty vault query is required."}, 400)
                    return
                if mode not in {"search", "summary", "answer"}:
                    self.send_json({"ok": False, "message": "Unknown vault query mode."}, 400)
                    return
                if not isinstance(limit, int) or isinstance(limit, bool):
                    self.send_json({"ok": False, "message": "Query limit must be an integer."}, 400)
                    return
                self.send_json({"ok": True, "job_id": start_vault_query(session_id, query.strip(), str(mode), max(1, min(limit, 20)))})
                return
            self.send_json({"ok": False, "message": "Not found."}, 404)
        except (OSError, RuntimeError, ValueError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            self.send_json({"ok": False, "message": str(exc)}, 500)


def main() -> None:
    httpd = ThreadingHTTPServer((HOST, PORT), AriadneHandler)
    start_lifecycle_watchdog()
    print(f"Ariadne listening at http://{HOST}:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        release_workloads(force=True)
        httpd.server_close()


if __name__ == "__main__":
    main()
