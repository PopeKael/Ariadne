import datetime
import ipaddress
import os
from pathlib import Path
import ssl
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from tls_transport import client_context, server_context


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"verified HTTPS")

    def log_message(self, *args):
        pass


class TlsTests(unittest.TestCase):
    def test_required_tls_never_falls_back_to_http(self):
        for environment in ({"ARIADNE_TLS_REQUIRED": "true"}, {"ARIADNE_TLS_CERT_FILE": "missing"}):
            with self.subTest(environment=environment), self.assertRaises(RuntimeError):
                server_context(environment)

    def test_unconfigured_development_listener_remains_available(self):
        self.assertIsNone(server_context({}))

    def test_real_https_accepts_trusted_certificate_and_rejects_untrusted(self):
        with tempfile.TemporaryDirectory() as temporary:
            certificate_path = Path(temporary) / "certificate.pem"
            key_path = Path(temporary) / "key.pem"
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test TLS certificate")])
            now = datetime.datetime.now(datetime.timezone.utc)
            certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(minutes=1))
                .not_valid_after(now + datetime.timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .sign(key, hashes.SHA256()))
            certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
            key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            tls = server_context({"ARIADNE_TLS_CERT_FILE": str(certificate_path), "ARIADNE_TLS_KEY_FILE": str(key_path)})
            self.assertEqual(tls.minimum_version, ssl.TLSVersion.TLSv1_2)
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            httpd.socket = tls.wrap_socket(httpd.socket, server_side=True)
            worker = threading.Thread(target=httpd.serve_forever, daemon=True)
            worker.start()
            url = f"https://127.0.0.1:{httpd.server_port}/"
            try:
                with patch.dict(os.environ, {"ARIADNE_TLS_CA_FILE": ""}):
                    with self.assertRaises(urllib.error.URLError):
                        urllib.request.urlopen(url, context=client_context(), timeout=3)
                with patch.dict(os.environ, {"ARIADNE_TLS_CA_FILE": str(certificate_path)}):
                    with urllib.request.urlopen(url, context=client_context(), timeout=3) as response:
                        self.assertEqual(response.read(), b"verified HTTPS")
                    with self.assertRaises(urllib.error.URLError):
                        urllib.request.urlopen(url.replace("127.0.0.1", "localhost"), context=client_context(), timeout=3)
            finally:
                httpd.shutdown()
                httpd.server_close()
                worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
