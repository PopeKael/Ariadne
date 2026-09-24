"""Small standard-library HTTP API for the portable Signal Service."""
from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .diagnostics import emit_diagnostic
from .models import normalize_category
from .service import SignalService, extract_intake_candidates


MAX_BODY_BYTES = 8_000_000


class SignalHandler(BaseHTTPRequestHandler):
    server: "SignalHTTPServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send(self, payload: object, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> object:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("Request body is too large")
        return json.loads((self.rfile.read(length) if length else b"{}").decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/v1/health":
            payload = self.server.service.health()
            emit_diagnostic(
                "health_response",
                state=payload.get("state"),
                cached_briefing=payload.get("cached_briefing"),
                last_success_at=payload.get("last_success_at"),
            )
            self._send(payload)
            return
        if parsed.path == "/v1/briefing":
            try:
                limit = int(parse_qs(parsed.query).get("limit", ["100"])[0])
            except ValueError:
                limit = 100
            briefing = self.server.service.briefing(limit)
            if briefing is None:
                self._send({"ok": False, "stale": True, "signals": [], "message": "No successful briefing is cached.", "health": self.server.service.health()}, 503)
            else:
                self._send({"ok": True, **briefing})
            return
        if parsed.path == "/v1/watchlist/topics":
            self._send({"ok": True, "topics": self.server.service.watchlist_topics()})
            return
        if parsed.path == "/v1/interests":
            self._send({"ok": True, "interests": self.server.service.interests()})
            return
        if parsed.path == "/v1/profile":
            self._send({"ok": True, "profile": self.server.service.store.learned_preferences()})
            return
        if parsed.path == "/v1/sources":
            self._send({"ok": True, "sources": self.server.service.sources()})
            return
        if parsed.path == "/v1/inference":
            self._send({"ok": True, **self.server.service.inference.snapshot()})
            return
        self._send({"ok": False, "message": "Not found."}, 404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        feedback_match = re.fullmatch(r"/v1/signals/([^/]+)/feedback", parsed.path)
        if feedback_match:
            try:
                payload = self._read_json()
                value = payload.get("feedback") if isinstance(payload, dict) else None
                result = self.server.service.record_feedback(feedback_match.group(1), str(value or ""))
                self._send({"ok": True, **result})
            except ValueError as exc:
                self._send({"ok": False, "message": str(exc)}, 400)
            return
        interaction_match = re.fullmatch(r"/v1/signals/([^/]+)/interaction", parsed.path)
        if interaction_match:
            try:
                payload = self._read_json()
                value = payload.get("interaction") if isinstance(payload, dict) else None
                result = self.server.service.record_interaction(interaction_match.group(1), str(value or ""))
                self._send({"ok": True, **result})
            except ValueError as exc:
                self._send({"ok": False, "message": str(exc)}, 400)
            return
        if parsed.path == "/v1/watchlist/topics":
            try:
                payload = self._read_json()
                topic = payload.get("topic") if isinstance(payload, dict) else None
                active = payload.get("active", True) if isinstance(payload, dict) else True
                result = self.server.service.add_watchlist_topic(str(topic or ""), bool(active))
                self._send({"ok": True, "topic": result})
            except ValueError as exc:
                self._send({"ok": False, "message": str(exc)}, 400)
            return
        if parsed.path == "/v1/interests":
            try:
                payload = self._read_json()
                result = self.server.service.upsert_interest(payload if isinstance(payload, dict) else {})
                self._send({"ok": True, "interest": result})
            except ValueError as exc:
                self._send({"ok": False, "message": str(exc)}, 400)
            return
        if parsed.path == "/v1/sources":
            try:
                payload = self._read_json()
                result = self.server.service.upsert_source(payload if isinstance(payload, dict) else {})
                self._send({"ok": True, "source": result})
            except ValueError as exc:
                self._send({"ok": False, "message": str(exc)}, 400)
            return
        if parsed.path == "/v1/inference":
            try:
                payload = self._read_json()
                providers = payload.get("providers") if isinstance(payload, dict) else None
                routes = payload.get("routes") if isinstance(payload, dict) else None
                self._send({"ok": True, **self.server.service.inference.save(providers if isinstance(providers, list) else None, routes if isinstance(routes, dict) else None)})
            except (ValueError, TypeError) as exc:
                self._send({"ok": False, "message": str(exc)}, 400)
            return
        if parsed.path == "/v1/profile/reset":
            self._send({"ok": True, "profile": self.server.service.reset_learned_preferences()})
            return
        if parsed.path != "/v1/intake/candidates":
            self._send({"ok": False, "message": "Not found."}, 404)
            return
        try:
            payload = self._read_json()
            candidates, wrapper = extract_intake_candidates(payload)
            source_name = str(wrapper.get("source_name") or wrapper.get("source") or "n8n candidate producer") if isinstance(wrapper, dict) else "n8n candidate producer"
            source_url = str(wrapper.get("source_url") or wrapper.get("feed_url") or "") if isinstance(wrapper, dict) else ""
            requested_category = wrapper.get("category") or wrapper.get("section") if isinstance(wrapper, dict) else ""
            default_category = normalize_category(requested_category, "Thailand Focus")
            result = self.server.service.ingest_candidates(candidates, default_source_name=source_name, default_source_url=source_url, ingest_type="candidate_intake", adapter="n8n_or_external", default_category=default_category)
            briefing = self.server.service.briefing()
            self._send({"ok": True, "accepted": result["accepted"], "duplicates": result["duplicates"], "rejected": result["rejected"], "errors": result["errors"], "briefing": briefing})
        except (ValueError, json.JSONDecodeError) as exc:
            self._send({"ok": False, "message": str(exc)}, 400)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        match = re.fullmatch(r"/v1/sources/([^/]+)", parsed.path)
        if not match:
            self._send({"ok": False, "message": "Not found."}, 404)
            return
        removed = self.server.service.delete_source(match.group(1))
        self._send({"ok": removed, "removed": removed}, 200 if removed else 404)


class SignalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: SignalService):
        super().__init__(address, SignalHandler)
        self.service = service


def build_service() -> SignalService:
    database_path = Path(os.environ.get("SIGNAL_SERVICE_DATABASE", str(Path(__file__).resolve().parents[1] / "data" / "signals.sqlite3")))
    feeds = [] if os.environ.get("SIGNAL_SERVICE_DISABLE_BUILTIN_FEEDS", "").casefold() in {"1", "true", "yes", "on"} else None
    service = SignalService(database_path, feeds=feeds)
    # Keep the ownership boundary explicit even if an older service module is
    # present in a cached image: Hera's receiver must never collect locally.
    if feeds is not None:
        service.feeds = []
    return service


def main() -> None:
    host = os.environ.get("SIGNAL_SERVICE_BIND_ADDRESS", "127.0.0.1")
    port = int(os.environ.get("SIGNAL_SERVICE_PORT", "8788"))
    emit_diagnostic("service_starting", host=host, port=port)
    service = build_service()
    emit_diagnostic("service_initialized", feed_count=len(service.feeds))
    service.start_background_refresh()
    httpd = SignalHTTPServer((host, port), service)
    emit_diagnostic("service_listening", host=host, port=port, url=f"http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        service.close()


if __name__ == "__main__":
    main()
