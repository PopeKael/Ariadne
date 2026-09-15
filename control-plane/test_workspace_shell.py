from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

import server


ROOT = Path(__file__).resolve().parent


class WorkspaceShellTests(unittest.TestCase):
    def test_all_primary_pages_use_the_shared_shell(self):
        for filename in (
            "home.html",
            "create.html",
            "plugins.html",
            "workshop.html",
            "configuration.html",
            "configuration-avatar.html",
            "index.html",
        ):
            with self.subTest(filename=filename):
                html = (ROOT / filename).read_text(encoding="utf-8")
                self.assertIn('<link rel="stylesheet" href="/page-shell.css">', html)
                self.assertIn('<script src="/page-shell.js"></script>', html)

    def test_navigation_and_workspace_boundaries_are_explicit(self):
        shell = (ROOT / "page-shell.js").read_text(encoding="utf-8")
        create = (ROOT / "create.html").read_text(encoding="utf-8")
        workshop = (ROOT / "workshop.html").read_text(encoding="utf-8")
        plugins = (ROOT / "plugins.html").read_text(encoding="utf-8")
        for label in ("Home", "Create", "Tools", "Workshop", "Setup", "System"):
            self.assertIn(f'"{label}"', shell)
        self.assertIn("Image", create)
        self.assertIn("Video", create)
        self.assertIn("Transcription", create)
        self.assertIn("Provider required", create)
        self.assertIn("Open WebUI", workshop)
        self.assertIn("LM Studio", workshop)
        self.assertIn("Tools &amp; Plugins", plugins)

    def test_workspace_routes_and_assets_are_served(self):
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
        try:
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            base = f"http://localhost:{httpd.server_port}"

            def get(path: str):
                with urllib.request.urlopen(base + path, timeout=5) as response:
                    return response.status, response.headers["Content-Type"], response.read()

            with patch.object(server, "_expire_sessions"):
                for path in ("/create", "/workshop"):
                    status, content_type, body = get(path)
                    self.assertEqual(status, 200)
                    self.assertIn("text/html", content_type)
                    self.assertIn(b"page-shell.js", body)
                for path in ("/page-shell.js", "/create.js", "/workshop.js"):
                    status, content_type, _ = get(path)
                    self.assertEqual(status, 200)
                    self.assertIn("text/javascript", content_type)

            expected = {"ok": True, "active_model": "test-model", "models": []}
            with patch.object(server, "_expire_sessions"), patch.object(server, "model_control_payload", return_value=expected):
                status, content_type, body = get("/api/model-control")
                self.assertEqual(status, 200)
                self.assertIn("application/json", content_type)
                self.assertEqual(json.loads(body), expected)
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
