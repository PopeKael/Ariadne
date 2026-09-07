"""Thin, failure-contained client for the independently running Signal Service."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


class SignalServiceClient:
    def __init__(self, base_url: str | None = None, *, timeout: float | None = None):
        self.base_url = (base_url or os.environ.get("ARIADNE_SIGNAL_SERVICE_URL", "http://127.0.0.1:8788")).rstrip("/")
        self.timeout = max(0.1, float(timeout if timeout is not None else os.environ.get("ARIADNE_SIGNAL_SERVICE_TIMEOUT", "0.5")))

    def _get(self, path: str) -> dict[str, Any]:
        if not self.base_url.startswith(("http://", "https://")):
            return {"ok": False, "state": "offline", "message": "Signal Service URL is not HTTP(S)."}
        try:
            request = urllib.request.Request(self.base_url + path, headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                value = json.loads(response.read(2_000_000).decode("utf-8"))
            return value if isinstance(value, dict) else {"ok": False, "message": "Signal Service returned a non-object response."}
        except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            return {"ok": False, "state": "offline", "message": f"Signal Service unavailable: {str(exc)[:180]}"}

    def briefing(self, limit: int = 6) -> dict[str, Any]:
        result = self._get(f"/v1/briefing?limit={max(1, min(int(limit), 20))}")
        if not isinstance(result.get("signals"), list):
            result["signals"] = []
        return result

    def health(self) -> dict[str, Any]:
        result = self._get("/v1/health")
        if not result.get("ok"):
            result.setdefault("state", "offline")
        return result


__all__ = ["SignalServiceClient"]
