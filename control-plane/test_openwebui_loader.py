from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import server


class OpenWebUILoaderTests(unittest.TestCase):
    def test_legacy_launcher_is_retired_without_ollama_or_docker_calls(self):
        with patch.object(server, "run_action") as run_action, patch.object(server, "run_readonly") as run_readonly, patch.object(
            server, "post_json"
        ) as post_json:
            result = server.launch_openwebui()
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "retired")
        self.assertIn("Docker is never started", result["detail"])
        run_action.assert_not_called()
        run_readonly.assert_not_called()
        post_json.assert_not_called()

    def test_loader_ui_is_inert_and_points_to_native_control(self):
        root = Path(__file__).resolve().parent
        html = (root / "openwebui-loader.html").read_text(encoding="utf-8")
        script = (root / "openwebui-loader.js").read_text(encoding="utf-8")
        self.assertIn("retired from Ariadne runtime", html)
        self.assertIn("native Ollama model control", html)
        self.assertNotIn("/api/openwebui/prepare", script)
        self.assertNotIn("Docker Desktop launch", script)
        self.assertNotIn("fetch(", script)


if __name__ == "__main__":
    unittest.main()
