"""HTTP API and process entry point for the always-on Discovery Engine."""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .engine import DiscoveryEngine


class DiscoveryHandler(BaseHTTPRequestHandler):
    server: "DiscoveryHTTPServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send(self, payload: object, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/v1/health":
            self._send(self.server.engine.health())
            return
        if parsed.path == "/v1/stories":
            try:
                limit = int(parse_qs(parsed.query).get("limit", ["240"])[0])
            except ValueError:
                limit = 240
            self._send({"ok": True, "stories": self.server.engine.stories(limit)})
            return
        if parsed.path == "/v1/sources":
            self._send({"ok": True, "sources": self.server.engine.store.sources()})
            return
        self._send({"ok": False, "message": "Not found."}, 404)

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path not in {"/v1/refresh", "/v1/collect"}:
            self._send({"ok": False, "message": "Not found."}, 404)
            return
        self._send(self.server.engine.refresh())


class DiscoveryHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], engine: DiscoveryEngine):
        super().__init__(address, DiscoveryHandler)
        self.engine = engine


def main() -> None:
    engine = DiscoveryEngine(os.environ.get("DISCOVERY_SERVICE_DATABASE", "/data/discovery.sqlite3"))
    host = os.environ.get("DISCOVERY_SERVICE_BIND_ADDRESS", "0.0.0.0")
    port = int(os.environ.get("DISCOVERY_SERVICE_PORT", "8789"))
    engine.start_background_refresh()
    server = DiscoveryHTTPServer((host, port), engine)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        engine.close()


if __name__ == "__main__":
    main()
