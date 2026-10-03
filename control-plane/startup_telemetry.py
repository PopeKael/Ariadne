"""Small, process-local startup trace; never controls startup behaviour."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid


class StartupTrace:
    def __init__(self, directory=None, *, run_id=None, enabled=True,
                 monotonic=time.monotonic, wall=lambda: datetime.now(timezone.utc).isoformat()):
        self.enabled = enabled
        self.run_id = run_id or uuid.uuid4().hex
        self.instance_id = f"{os.getpid()}-{uuid.uuid4().hex}"
        self.path = Path(directory or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Ariadne" / "startup") / self.run_id / f"core-{self.instance_id}.jsonl"
        self._clock, self._wall = monotonic, wall
        self._origin = monotonic()
        self._lock = threading.RLock()
        self._events = {}
        self._workers = {}
        self._write_error = None

    def mark(self, phase, status="ready", **detail):
        if not self.enabled:
            return
        with self._lock:
            if phase in self._events:
                return
            elapsed = round((self._clock() - self._origin) * 1000, 3)
            row = dict(run_id=self.run_id, instance_id=self.instance_id, source="python",
                       pid=os.getpid(), timestamp=self._wall(), elapsed_ms=elapsed,
                       clock_scope="python_process", phase=phase, status=status, detail=detail)
            if phase.endswith(("_end", "_ready", "_failed")):
                start = self._events.get(phase.rsplit("_", 1)[0] + "_start")
                if start:
                    row["duration_ms"] = round(elapsed - start["elapsed_ms"], 3)
            self._events[phase] = row
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row, ensure_ascii=True) + "\n")
                    stream.flush()
            except OSError as exc:
                self._write_error = type(exc).__name__
            self._check_ready()

    def connection(self, service, event, **detail):
        self.mark(f"hera_{service}_{event}", "error" if event == "failed" else ("started" if event == "start" else "ready"), **detail)

    def worker_done(self, name, ok):
        if not self.enabled:
            return
        with self._lock:
            self._workers.setdefault(name, bool(ok))
            self.mark(f"background_{name}_end", "ready" if ok else "degraded")
            if {"news", "images", "lifecycle"}.issubset(self._workers):
                self.mark("background_jobs_ready", "ready" if all(self._workers.values()) else "degraded", workers=dict(self._workers), meaning="workers started and first sync attempts finished")

    def _check_ready(self):
        required = {"core_ready", "ui_rendered", "background_jobs_ready", "model_preload_end"}
        if required.issubset(self._events) and "application_ready" not in self._events:
            degraded = [phase for phase in required if self._events[phase]["status"] != "ready"]
            self.mark("application_ready", "degraded" if degraded else "ready", degraded=sorted(degraded), meaning="core served, browser rendered, initial background work and preload finished")

    def snapshot(self):
        with self._lock:
            return dict(ok=True, enabled=self.enabled, run_id=self.run_id,
                        instance_id=self.instance_id, trace_path=str(self.path),
                        write_error=self._write_error, events=list(self._events.values()))

    def ui_rendered(self, body):
        elapsed = body.get("navigation_elapsed_ms")
        wall = body.get("browser_wall_time_ms")
        valid_number = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1e15
        if (body.get("instance_id") != self.instance_id or body.get("surface") not in {"home", "chat"}
                or not valid_number(elapsed) or not valid_number(wall)):
            return False
        self.mark("core_ready", meaning="core served a browser render acknowledgement")
        self.mark("ui_rendered", surface=body["surface"], navigation_elapsed_ms=elapsed,
                  browser_wall_time_ms=wall, meaning="initial data rendered followed by two animation frames; paint opportunity, not a compositor measurement")
        return True


def process_trace(enabled):
    run_id = os.environ.get("ARIADNE_STARTUP_RUN_ID", "")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", run_id):
        run_id = None
    return StartupTrace(run_id=run_id, enabled=enabled)
