"""Thin, failure-contained client for the independently running Signal Service."""
from __future__ import annotations

import json
import hashlib
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from librarian_events import LibrarianEventStream


LEGACY_SOURCE_CATEGORIES = {
    "Ars Technica": "AI Watch",
    "NASA Breaking News": "Main News Feed",
    "Hacker News": "AI Watch",
}


class SignalServiceClient:
    def __init__(self, base_url: str | None = None, *, timeout: float | None = None, diagnostics_path: str | Path | None = None):
        self.base_url = (base_url or os.environ.get("ARIADNE_SIGNAL_SERVICE_URL", "http://192.168.1.200:8788")).rstrip("/")
        self.timeout = max(0.1, float(timeout if timeout is not None else os.environ.get("ARIADNE_SIGNAL_SERVICE_TIMEOUT", "0.5")))
        path = diagnostics_path or os.environ.get("ARIADNE_SIGNAL_SERVICE_EVENTS_PATH") or Path(__file__).resolve().parent / "runtime" / "signal-service-events.jsonl"
        self._diagnostics = LibrarianEventStream(Path(path))

    def _get(self, path: str) -> dict[str, Any]:
        if not self.base_url.startswith(("http://", "https://")):
            self._diagnostics.emit(
                "SIGNAL_SERVICE_REQUEST_FAILED",
                data={"path": path, "url": self.base_url + path, "error_type": "invalid_url", "message": "Signal Service URL is not HTTP(S)."},
            )
            return {"ok": False, "state": "offline", "message": "Signal Service URL is not HTTP(S)."}
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        self._diagnostics.emit(
            "SIGNAL_SERVICE_REQUEST_STARTED",
            request_id=request_id,
            data={"path": path, "url": self.base_url + path, "timeout_seconds": self.timeout, "attempt": 1},
        )
        try:
            request = urllib.request.Request(self.base_url + path, headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                value = json.loads(response.read(2_000_000).decode("utf-8"))
            result = value if isinstance(value, dict) else {"ok": False, "message": "Signal Service returned a non-object response."}
            self._diagnostics.emit(
                "SIGNAL_SERVICE_REQUEST_SUCCEEDED" if result.get("ok", True) else "SIGNAL_SERVICE_REQUEST_FAILED",
                request_id=request_id,
                latency_ms=(time.monotonic() - started) * 1000,
                data={"path": path, "status": getattr(response, "status", None), "state": result.get("state"), "ok": result.get("ok")},
            )
            return result
        except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            self._diagnostics.emit(
                "SIGNAL_SERVICE_REQUEST_FAILED",
                request_id=request_id,
                latency_ms=(time.monotonic() - started) * 1000,
                data={"path": path, "error_type": type(exc).__name__, "message": str(exc)[:180], "attempt": 1},
            )
            return {"ok": False, "state": "offline", "message": f"Signal Service unavailable: {str(exc)[:180]}"}

    def briefing(self, limit: int = 6) -> dict[str, Any]:
        result = self._get(f"/v1/briefing?limit={max(1, min(int(limit), 30))}")
        if not isinstance(result.get("signals"), list):
            result["signals"] = []
        return result

    def feedback(self, signal_id: str, value: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}/v1/signals/{urllib.parse.quote(str(signal_id), safe='')}/feedback",
            data=json.dumps({"feedback": value}).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read(200_000).decode("utf-8"))
            return result if isinstance(result, dict) else {"ok": False, "message": "Signal Service returned a non-object response."}
        except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            return {"ok": False, "message": f"Signal feedback unavailable: {str(exc)[:180]}"}

    def health(self) -> dict[str, Any]:
        result = self._get("/v1/health")
        if not result.get("ok"):
            result.setdefault("state", "offline")
        return result

    def adaptive(self) -> dict[str, Any]:
        result = self._get("/v1/health")
        return result if isinstance(result, dict) else {"ok": False, "state": "offline"}

    def interests(self) -> dict[str, Any]:
        return self._get("/v1/interests")

    def sources(self) -> dict[str, Any]:
        result = self._get("/v1/sources")
        if result.get("ok") and isinstance(result.get("sources"), list):
            return result
        # Signal Service 0.1 predates the source registry endpoint.  Keep
        # Setup truthful during rolling upgrades by projecting its existing
        # configured feeds and health timestamps without mutating the service.
        health = self.health()
        feeds = health.get("feeds") if isinstance(health.get("feeds"), list) else []
        statuses = {
            str(item.get("name")): item
            for item in health.get("source_status", [])
            if isinstance(item, dict) and item.get("name")
        }
        sources: list[dict[str, Any]] = []
        for feed in feeds:
            if not isinstance(feed, dict):
                continue
            name = str(feed.get("name") or "").strip()
            endpoint = str(feed.get("url") or feed.get("endpoint") or "").strip()
            if not name or not endpoint:
                continue
            status = statuses.get(name, {})
            source_id = "legacy-" + hashlib.sha256(f"{name}|{endpoint}".encode("utf-8")).hexdigest()[:24]
            sources.append({
                "source_id": source_id,
                "name": name,
                "adapter_type": "rss_atom",
                "endpoint": endpoint,
                "category": LEGACY_SOURCE_CATEGORIES.get(name, "Main News Feed"),
                "enabled": True,
                "last_attempt_at": status.get("attempted_at") or health.get("last_attempt_at"),
                "last_success_at": status.get("last_success_at") or health.get("last_success_at"),
                "item_count": int(status.get("items") or status.get("item_count") or 0),
                "health": status.get("state") or ("healthy" if health.get("last_collection_ok") else "unknown"),
                "error": status.get("error") or "",
                "legacy_projection": True,
            })
        return {"ok": bool(sources) or health.get("ok", False), "sources": sources, "legacy_projection": True, "message": "Projected configured feeds from the legacy Signal Service health endpoint."}

    def profile(self) -> dict[str, Any]:
        return self._get("/v1/profile")

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(self.base_url + path, data=json.dumps(payload).encode("utf-8"), headers={"Accept": "application/json", "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                value = json.loads(response.read(500_000).decode("utf-8"))
            return value if isinstance(value, dict) else {"ok": False, "message": "Signal Service returned a non-object response."}
        except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            return {"ok": False, "message": f"Signal Service unavailable: {str(exc)[:180]}"}

    def upsert_interest(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/v1/interests", payload)

    def upsert_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/v1/sources", payload)

    def delete_source(self, source_id: str) -> dict[str, Any]:
        request = urllib.request.Request(self.base_url + "/v1/sources/" + urllib.parse.quote(source_id, safe=""), headers={"Accept": "application/json"}, method="DELETE")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                value = json.loads(response.read(200_000).decode("utf-8"))
            return value if isinstance(value, dict) else {"ok": False, "message": "Signal Service returned a non-object response."}
        except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            return {"ok": False, "message": f"Signal Service unavailable: {str(exc)[:180]}"}


__all__ = ["SignalServiceClient"]
