"""Small standard-library HTTP API for the portable Signal Service."""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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
            self._send(self.server.service.health())
            return
        if parsed.path == "/v1/briefing":
            try:
                limit = int(parse_qs(parsed.query).get("limit", ["6"])[0])
            except ValueError:
                limit = 6
            briefing = self.server.service.briefing(limit)
            if briefing is None:
                self._send({"ok": False, "stale": True, "signals": [], "message": "No successful briefing is cached.", "health": self.server.service.health()}, 503)
            else:
                self._send({"ok": True, **briefing})
            return
        self._send({"ok": False, "message": "Not found."}, 404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/v1/intake/candidates":
            self._send({"ok": False, "message": "Not found."}, 404)
            return
        try:
            payload = self._read_json()
            candidates, wrapper = extract_intake_candidates(payload)
            source_name = str(wrapper.get("source_name") or wrapper.get("source") or "n8n candidate producer") if isinstance(wrapper, dict) else "n8n candidate producer"
            source_url = str(wrapper.get("source_url") or wrapper.get("feed_url") or "") if isinstance(wrapper, dict) else ""
            result = self.server.service.ingest_candidates(candidates, default_source_name=source_name, default_source_url=source_url, ingest_type="candidate_intake", adapter="n8n_or_external")
            briefing = self.server.service.briefing()
            self._send({"ok": True, "accepted": result["accepted"], "duplicates": result["duplicates"], "rejected": result["rejected"], "errors": result["errors"], "briefing": briefing})
        except (ValueError, json.JSONDecodeError) as exc:
            self._send({"ok": False, "message": str(exc)}, 400)


class SignalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: SignalService):
        super().__init__(address, SignalHandler)
        self.service = service


def build_service() -> SignalService:
    database_path = Path(os.environ.get("SIGNAL_SERVICE_DATABASE", str(Path(__file__).resolve().parents[1] / "data" / "signals.sqlite3")))
    return SignalService(database_path)


def main() -> None:
    host = os.environ.get("SIGNAL_SERVICE_BIND_ADDRESS", "127.0.0.1")
    port = int(os.environ.get("SIGNAL_SERVICE_PORT", "8788"))
    service = build_service()
    service.start_background_refresh()
    httpd = SignalHTTPServer((host, port), service)
    print(f"Ariadne Signal Service listening at http://{host}:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        service.close()


if __name__ == "__main__":
    main()
