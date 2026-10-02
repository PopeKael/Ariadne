import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import server
import vault_config
from workspace_proxy import rewrite_workspace_text


class HttpsEntrypointTests(unittest.TestCase):
    def test_browser_redirects_bypass_health_work(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.object(server, "_expire_sessions", side_effect=AssertionError("unnecessary work")):
                for path in ("/", "/chat?chat_id=existing", "/configuration/avatar", "/system-details", "/music", "/workspaces/video/"):
                    connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port)
                    connection.request("GET", path)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 302)
                    self.assertEqual(response.getheader("Location"), server.PUBLIC_ORIGIN + path)
                    response.read()
                    connection.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join()

    def test_proxy_requests_do_not_redirect_back_to_themselves(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.object(server, "_expire_sessions"), patch.object(server, "record_home_event"):
                connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port)
                connection.request("GET", "/", headers={"X-Forwarded-Proto": "https"})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn(b"Ariadne", response.read())
                connection.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join()

    def test_workspace_assets_and_api_keep_secure_origin(self):
        original = b'<img src="/image.png"><script>fetch("/api/status"); const u="http://127.0.0.1:8766/output";</script>'
        rewritten = rewrite_workspace_text(original, "/workspaces/video/", "http://127.0.0.1:8766")
        self.assertIn(b'src="/workspaces/video/image.png"', rewritten)
        self.assertIn(b'fetch("/workspaces/video/api/status")', rewritten)
        self.assertIn(b'"/workspaces/video/output"', rewritten)
        self.assertNotIn(b"http://", rewritten)

    def test_counts_reused_and_invalidated_when_index_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            system = root / "00_System"
            (system / "Data").mkdir(parents=True)
            (system / "library.json").write_text("[]")
            index = system / "Data" / "embedding-index.json"
            index.write_text(json.dumps({"entries": {"one": {"path": "doc"}}}))
            with patch.object(vault_config, "_read_vault_counts", wraps=vault_config._read_vault_counts) as read:
                first = vault_config.vault_counts(root)
                first["embedding_chunks"] = 999
                self.assertEqual(vault_config.vault_counts(root)["embedding_chunks"], 1)
                self.assertEqual(read.call_count, 1)
                index.write_text(json.dumps({"entries": {"one": {"path": "doc"}, "two": {"path": "doc"}}}))
                self.assertEqual(vault_config.vault_counts(root)["embedding_chunks"], 2)
                self.assertEqual(read.call_count, 2)


if __name__ == "__main__":
    unittest.main()
