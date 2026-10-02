"""Owned HTTPS edge for the local core and Hera's embedding-only Ollama route."""

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit

from tls_transport import server_context

HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"}


def create_gateway(settings):
    peers = set(settings["allowed_peers"])
    if not peers:
        raise ValueError("HTTPS gateway requires an explicit peer allowlist.")
    origins = {name: urlsplit(settings[name]) for name in ("core_url", "ollama_url")}
    for origin in origins.values():
        if origin.scheme != "http" or origin.hostname not in {"127.0.0.1", "localhost", "::1"} or origin.path not in {"", "/"}:
            raise ValueError("Raw HTTPS gateway backends must be local loopback listeners.")
    tls = server_context({"ARIADNE_TLS_REQUIRED": "true", "ARIADNE_TLS_CERT_FILE": settings["certificate"], "ARIADNE_TLS_KEY_FILE": settings["private_key"]})

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def error(self, code, message):
            body = json.dumps({"ok": False, "message": message}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def forward(self):
            if self.client_address[0] not in peers:
                self.error(403, "This HTTPS listener accepts only its configured proxy peers.")
                return
            internal = self.path.startswith("/internal/ollama")
            route = self.path.removeprefix("/internal/ollama") if internal else self.path
            if internal and (self.command, route) not in {("GET", "/api/tags"), ("POST", "/api/embed")}:
                self.error(404, "Only model availability and embeddings are exposed on this private route.")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.error(400, "Invalid content length.")
                return
            if length < 0 or self.headers.get("Transfer-Encoding"):
                self.error(400, "A finite Content-Length is required for request bodies.")
                return
            if internal and length > 1_048_576:
                self.error(413, "Embedding request exceeds the private route's body limit.")
                return
            body = self.rfile.read(length) if length else None
            if internal and self.command == "POST":
                try:
                    payload = json.loads(body or b"{}")
                    if not isinstance(payload, dict) or payload.get("model") not in {"nomic-embed-text", "nomic-embed-text:latest"}:
                        raise ValueError("The private embedding route permits only nomic-embed-text.")
                    if set(payload) - {"model", "input", "truncate"}:
                        raise ValueError("Unsupported embedding options.")
                except (ValueError, TypeError) as exc:
                    self.error(400, str(exc))
                    return
            origin = origins["ollama_url" if internal else "core_url"]
            headers = {key: value for key, value in self.headers.items() if key.casefold() not in HOP_HEADERS | {"host"}}
            headers["Host"] = origin.netloc
            headers["X-Forwarded-Proto"] = "https"
            upstream = http.client.HTTPConnection(origin.hostname, origin.port, timeout=300)
            started = False
            try:
                upstream.request(self.command, route, body=body, headers=headers)
                response = upstream.getresponse()
                rewritten = None
                if internal and self.command == "GET" and response.status == 200:
                    catalogue = json.loads(response.read())
                    rewritten = json.dumps({"models": [model for model in catalogue.get("models", []) if model.get("name") in {"nomic-embed-text", "nomic-embed-text:latest"}]}).encode()
                self.send_response(response.status)
                for name, value in response.getheaders():
                    if name.casefold() not in HOP_HEADERS and not (rewritten is not None and name.casefold() == "content-length"):
                        self.send_header(name, value)
                if rewritten is not None:
                    self.send_header("Content-Length", str(len(rewritten)))
                self.send_header("Connection", "close")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                started = True
                if rewritten is not None:
                    self.wfile.write(rewritten)
                else:
                    while chunk := response.read1(16_384):
                        self.wfile.write(chunk)
                        self.wfile.flush()
            except (OSError, ValueError, http.client.HTTPException):
                if not started:
                    self.error(502, "The local backend could not complete this request.")
            finally:
                self.close_connection = True
                upstream.close()

        do_GET = forward
        do_POST = forward
        do_DELETE = forward
        do_PUT = forward
        do_HEAD = forward

        def log_message(self, *args):
            # Avoid logging private request URLs, prompts or credentials.
            pass

    class GatewayServer(ThreadingHTTPServer):
        def process_request_thread(self, request, client_address):
            # Browser preconnections may accept TCP without starting TLS.
            # Handshake in the worker so one idle peer cannot stop accept().
            try:
                request.settimeout(10)
                request.do_handshake()
                request.settimeout(None)
            except OSError:
                self.shutdown_request(request)
                return
            super().process_request_thread(request, client_address)

    httpd = GatewayServer((settings["bind"], int(settings["port"])), Handler)
    try:
        httpd.socket = tls.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
    except Exception:
        httpd.server_close()
        raise
    return httpd


def load_gateway(path):
    path = Path(path)
    if not path.is_file():
        return None
    return create_gateway(json.loads(path.read_text(encoding="utf-8-sig")))


def start_gateway(httpd):
    worker = threading.Thread(target=httpd.serve_forever, name="ariadne-https", daemon=True)
    worker.start()
    return worker
