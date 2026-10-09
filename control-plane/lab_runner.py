"""Host-owned presentation launch; generated code stays in the preview sandbox."""
import secrets
import re
import threading
import time
import code_lab
from avatar_events import emit

_LOCK = threading.Lock()
_JOBS = {}


def launch(run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("A valid saved Lab run ID is required.")
    result = code_lab.saved_result(run_id)
    if not result["ok"]:
        raise ValueError("Only successful saved builds can launch in Lab Runner.")
    if result['run'].get('pipeline_version',0) >= 2 and not result['run'].get('verified'):
        raise ValueError('The candidate must pass verification before Lab Runner launch.')
    token = secrets.token_hex(16)
    with _LOCK:
        for key in list(_JOBS):
            if time.monotonic() - _JOBS[key]["created"] > 3600:
                del _JOBS[key]
        _JOBS[token] = {"run_id": run_id, "state": "REQUESTED", "created": time.monotonic()}
    if not emit("launch_lab_runner", run_id=run_id, launch_id=token):
        report({"launch_id": token, "state": "FAILED", "message": "Windows host is unavailable. Start Ariadne's resident host and retry."})
    return status(token)


def status(token):
    with _LOCK:
        if token not in _JOBS:
            raise ValueError("Lab Runner launch was not found.")
        return {"launch_id": token, **{k: v for k, v in _JOBS[token].items() if k != "created"}}


def report(body):
    token, state = body.get("launch_id"), body.get("state")
    if state not in {"LAUNCHED", "READY", "FAILED", "CLOSED", "FOCUS"}:
        raise ValueError("Invalid Lab Runner state.")
    with _LOCK:
        if token not in _JOBS:
            raise ValueError("Unknown Lab Runner launch.")
        job = _JOBS[token]
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


def runner_page(token):
    job = status(token)
    result = code_lab.saved_result(job["run_id"])
    if not result["ok"]:
        raise ValueError("The saved build is unavailable.")
    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ariadne Lab Runner</title><style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#000}iframe{position:fixed;inset:0;width:100%;height:100%;border:0}#failure{position:fixed;inset:0;background:#07151e;color:white;display:grid;place-content:center;font:20px system-ui;padding:3rem}#failure[hidden]{display:none}</style></head><body><iframe title="Running Lab build" sandbox="allow-scripts" referrerpolicy="no-referrer" allow="camera 'none'; microphone 'none'; geolocation 'none'; clipboard-read 'none'; clipboard-write 'none'; usb 'none'; serial 'none'; display-capture 'none'"></iframe><div id="failure" hidden></div><script src="/lab-runner.js"></script></body></html>"""
