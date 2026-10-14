"""Host-owned full-screen runner for verified Qwen Code workspaces."""
from __future__ import annotations

import secrets
import re
import threading
import time
from urllib.parse import quote

import coding_agent
from avatar_events import emit

_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, object]] = {}


def launch(run_id: str) -> dict[str, object]:
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("A valid coding run ID is required.")
    result = coding_agent.coding_run_result(run_id)
    if not result or result.get("status") != "SUCCESS":
        raise ValueError("Only successful Qwen Code jobs can launch in the full-screen Runner.")
    tests = result.get("tests") if isinstance(result.get("tests"), list) else []
    if any(item.get("result") == "failed" for item in tests if isinstance(item, dict)):
        raise ValueError("The coding job has a failed test result; repair and verify it before Runner launch.")
    verification = result.get("verification")
    if not isinstance(verification, dict) or verification.get("passed") is not True:
        raise ValueError("Run and pass the interactive preview checks before Runner launch.")
    root, entry = coding_agent.coding_preview_entry(run_id)
    token = secrets.token_hex(16)
    preview_url = f"/coding-preview/{run_id}/{quote(entry, safe='/')}"
    with _LOCK:
        for key in list(_JOBS):
            if time.monotonic() - float(_JOBS[key]["created"]) > 3600:
                del _JOBS[key]
        _JOBS[token] = {
            "run_id": run_id, "workspace": str(root), "entry_path": entry,
            "preview_url": preview_url, "state": "REQUESTED", "created": time.monotonic(),
        }
    if not emit("launch_lab_runner", run_id=run_id, launch_id=token):
        report({"launch_id": token, "state": "FAILED", "message": "Windows host is unavailable. Start Ariadne's resident host and retry."})
    return status(token)


def status(token: str) -> dict[str, object]:
    with _LOCK:
        if token not in _JOBS:
            raise ValueError("Coding Runner launch was not found.")
        return {"launch_id": token, **{key: value for key, value in _JOBS[token].items() if key != "created"}}


def report(body: dict[str, object]) -> None:
    token, state = body.get("launch_id"), body.get("state")
    if state not in {"LAUNCHED", "READY", "FAILED", "CLOSED", "FOCUS"}:
        raise ValueError("Invalid Coding Runner state.")
    with _LOCK:
        if token not in _JOBS:
            raise ValueError("Unknown Coding Runner launch.")
        job = _JOBS[str(token)]
        if job["state"] in {"FAILED", "CLOSED"} or (job["state"] == "READY" and state == "LAUNCHED"):
            return
        if state == "FOCUS":
            if not isinstance(body.get("foreground"), bool):
                raise ValueError("A boolean foreground result is required.")
            job["foreground"] = body["foreground"]
            return
        job.update(state=state, message=str(body.get("message") or "")[:1000])
        if state == "READY":
            job["viewport"] = body.get("viewport")


def runner_page(token: str) -> str:
    status(token)
    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ariadne Coding Runner</title><style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#000}iframe{position:fixed;inset:0;width:100%;height:100%;border:0}#failure{position:fixed;inset:0;background:#07151e;color:white;display:grid;place-content:center;font:20px system-ui;padding:3rem}#failure[hidden]{display:none}</style></head><body><iframe title="Qwen Code workspace preview" sandbox="allow-scripts" referrerpolicy="no-referrer" allow="camera 'none'; microphone 'none'; geolocation 'none'; clipboard-read 'none'; clipboard-write 'none'; usb 'none'; serial 'none'; display-capture 'none'"></iframe><div id="failure" hidden></div><script src="/lab-runner.js"></script></body></html>"""
