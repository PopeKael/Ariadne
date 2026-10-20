import datetime
import ipaddress
import json
from pathlib import Path
import ssl
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import nullcontext
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from https_gateway import create_gateway, start_gateway


class Upstream(BaseHTTPRequestHandler):
    calls = []

    def do_GET(self):
        self.calls.append(self.path)
        if self.path == "/stream":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"first")
            self.wfile.flush()
            self.server.release_stream.wait(timeout=3)
            self.wfile.write(b"last")
            return
        body = json.dumps({"models": [{"name": "nomic-embed-text:latest"}, {"name": "qwen3.5:9b"}]}).encode() if self.path == "/api/tags" else b"core response"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.calls.append(self.path)
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.received_post_payloads = getattr(self.server, "received_post_payloads", []) + [payload]
        body = json.dumps({"embeddings": [[0.1, 0.2] for _ in payload["input"]]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.host_lease_patch = patch("ollama_runtime.inference_lease", return_value=nullcontext())
        self.host_lease_mock = self.host_lease_patch.start()
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test gateway")])
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1)).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
        self.certificate = root / "certificate.pem"
        self.private_key = root / "key.pem"
        self.certificate.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        self.private_key.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.context = ssl.create_default_context(cafile=str(self.certificate))
        Upstream.calls = []
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        self.upstream.received_post_payloads = []
        self.upstream_worker = start_gateway(self.upstream)
        self.settings = {"bind": "127.0.0.1", "port": 0, "allowed_peers": ["127.0.0.1"], "certificate": str(self.certificate), "private_key": str(self.private_key), "core_url": f"http://127.0.0.1:{self.upstream.server_port}", "ollama_url": f"http://127.0.0.1:{self.upstream.server_port}"}
        self.gateway = create_gateway(self.settings)
        self.gateway_worker = start_gateway(self.gateway)
        self.url = f"https://127.0.0.1:{self.gateway.server_port}"

    def tearDown(self):
        for server, worker in ((self.gateway, self.gateway_worker), (self.upstream, self.upstream_worker)):
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)
        self.temporary.cleanup()
        self.host_lease_patch.stop()

    def request(self, path, payload=None):
        request = urllib.request.Request(self.url + path, data=json.dumps(payload).encode() if payload is not None else None, headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(request, context=self.context, timeout=3)

    def test_https_routes_core_and_filters_model_catalogue(self):
        with self.request("/api/status") as response:
            self.assertEqual(response.read(), b"core response")
        with self.request("/internal/ollama/api/tags") as response:
            self.assertEqual(json.load(response)["models"], [{"name": "nomic-embed-text:latest"}])

    def test_embedding_only_route_rejects_model_operations(self):
        # The gateway adds a verified context option. Its forwarded length
        # must match the rewritten JSON body, including when the caller omits
        # options and the gateway body grows.
        with self.request("/internal/ollama/api/embed", {"model": "nomic-embed-text", "input": ["test"]}) as response:
            self.assertEqual(len(json.load(response)["embeddings"]), 1)
        self.assertEqual(self.upstream.received_post_payloads, [{
            "model": "nomic-embed-text", "input": ["test"], "options": {"num_ctx": 2048},
        }])
        self.host_lease_mock.assert_called_once()
        self.assertEqual(self.host_lease_mock.call_args.kwargs["inference_kind"], "embed")
        self.assertEqual(self.host_lease_mock.call_args.kwargs["context_length"], 2048)
        before = list(Upstream.calls)
        for path, payload in (("/internal/ollama/api/pull", {}),
                              ("/internal/ollama/api/embed", {"model": "qwen3.5:9b", "input": ["test"]}),
                              ("/internal/ollama/api/embed", {"model": "nomic-embed-text", "input": ["test"], "options": {"num_ctx": 4096}})):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as error:
                self.request(path, payload)
            self.assertIn(error.exception.code, {400, 404})
        self.assertEqual(Upstream.calls, before)

    def test_shutdown_closes_gateway_before_new_embedding_leases(self):
        self.gateway.admissions_closed.set()
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/internal/ollama/api/embed", {"model": "nomic-embed-text", "input": ["test"]})
        self.assertEqual(error.exception.code, 503)
        self.host_lease_mock.assert_not_called()
        self.assertEqual(Upstream.calls, [])

    def test_unapproved_peer_cannot_reach_either_backend(self):
        denied = create_gateway({**self.settings, "allowed_peers": ["192.168.1.200"]})
        worker = start_gateway(denied)
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(f"https://127.0.0.1:{denied.server_port}/api/status", context=self.context, timeout=3)
            self.assertEqual(error.exception.code, 403)
            self.assertEqual(Upstream.calls, [])
        finally:
            denied.shutdown()
            denied.server_close()
            worker.join(timeout=3)

    def test_remote_raw_backend_is_rejected(self):
        with self.assertRaises(ValueError):
            create_gateway({**self.settings, "core_url": "http://192.168.1.200:8788"})

    def test_stream_is_delivered_before_the_backend_finishes(self):
        self.upstream.release_stream = threading.Event()
        try:
            with self.request("/stream") as response:
                self.assertEqual(response.read1(5), b"first")
                self.assertFalse(self.upstream.release_stream.is_set())
                self.upstream.release_stream.set()
                self.assertEqual(response.read(), b"last")
        finally:
            self.upstream.release_stream.set()

    def test_idle_connection_does_not_block_other_tls_clients(self):
        idle = socket.create_connection(("127.0.0.1", self.gateway.server_port), timeout=3)
        try:
            with self.request("/api/status") as response:
                self.assertEqual(response.read(), b"core response")
        finally:
            idle.close()


if __name__ == "__main__":
    unittest.main()
