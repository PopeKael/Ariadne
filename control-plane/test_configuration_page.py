import json
import tempfile
import threading
import urllib.request
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from ariadne_config import save_configuration
from plugin_registry import PluginRecord


class ConfigurationPageTests(unittest.TestCase):
    def snapshot(self, root: Path, plugins: dict[str, object] | None = None) -> dict[str, object]:
        vault = root / "Vault"
        (vault / "00_System").mkdir(parents=True)
        storage = {
            "knowledge_vault": str(vault),
            "documents": str(root / "Documents"),
            "images": str(root / "Images"),
            "videos": str(root / "Videos"),
            "screenshots": str(root / "Screenshots"),
            "intake_root": str(root / "Downloads"),
        }
        return {
            "path": str(root / "configuration.json"),
            "version": 2,
            "precedence": [],
            "storage": storage,
            "sources": {key: "test" for key in storage},
            "avatar": {"enabled": True, "asset_directory": str(root / "Avatar"), "state_assets": {}},
            "avatar_sources": {},
            "plugins": plugins or {},
        }

    @staticmethod
    def settings_record(plugin_id: str, *, enabled: bool = True) -> PluginRecord:
        manifest = {
            "manifest_version": 1,
            "plugin_id": plugin_id,
            "name": plugin_id.title(),
            "version": "1.0.0",
            "description": "Test plugin",
            "enabled": enabled,
            "capabilities": [],
            "settings": {"available": True, "route": "/configuration", "label": "Configure"},
        }
        return PluginRecord(manifest, "test", f"{plugin_id}/plugin.json", "healthy")

    def test_configuration_payload_is_config_first_and_registry_driven(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = self.snapshot(Path(temporary))
            records = [self.settings_record("disabled-plugin", enabled=False), self.settings_record("enabled-plugin")]
            with patch.object(server.PLUGIN_REGISTRY, "discover", return_value=records), \
                patch.object(server, "vault_counts", side_effect=AssertionError("health ran during config load")), \
                patch.object(server, "vault_activity_status", side_effect=AssertionError("health ran during config load")), \
                patch.object(server, "ollama_catalog", side_effect=AssertionError("health ran during config load")), \
                patch.object(server, "ollama_status", side_effect=AssertionError("health ran during config load")), \
                patch.object(server, "model_memory_snapshot", side_effect=AssertionError("health ran during config load")):
                payload = server.configuration_payload(snapshot)

            self.assertEqual(list(payload["plugins"]), ["disabled-plugin", "enabled-plugin"])
            self.assertFalse(payload["plugins"]["disabled-plugin"]["enabled"])
            self.assertIsNone(payload["runtime"])
            self.assertIsNone(payload["vault"])

            with patch.object(server.PLUGIN_REGISTRY, "discover", return_value=[]):
                self.assertEqual(server.configuration_payload(snapshot)["plugins"], {})

    def test_configuration_routes_are_separate_and_helper_asset_is_served(self):
        config_payload = {"ok": True, "config": {}, "plugins": []}
        health_payload = {"ok": True, "runtime": {}, "vault": {}}
        httpd = None
        try:
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{httpd.server_port}"

            def get(path: str):
                with urllib.request.urlopen(base + path, timeout=5) as response:
                    return response.status, response.headers["Content-Type"], response.read()

            with patch.object(server, "_expire_sessions"), \
                patch.object(server, "configuration_payload", return_value=config_payload) as cheap, \
                patch.object(server, "configuration_health_payload", side_effect=AssertionError("health endpoint called by config endpoint")):
                status, content_type, body = get("/api/configuration")
                self.assertEqual(status, 200)
                self.assertIn("application/json", content_type)
                self.assertEqual(json.loads(body), config_payload)
                cheap.assert_called_once_with()

            with patch.object(server, "_expire_sessions"), patch.object(server, "configuration_health_payload", return_value=health_payload):
                status, _, body = get("/api/configuration/health")
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body), health_payload)

            with patch.object(server, "_expire_sessions"):
                status, content_type, body = get("/configuration-state.js")
                self.assertEqual(status, 200)
                self.assertIn("text/javascript", content_type)
                self.assertIn(b"AriadneCleanupState", body)
        finally:
            if httpd is not None:
                httpd.shutdown()
                httpd.server_close()

    def test_configuration_page_uses_dynamic_plugin_mount_and_focused_mode(self):
        html = Path(__file__).with_name("configuration.html").read_text(encoding="utf-8")
        javascript = Path(__file__).with_name("configuration.js").read_text(encoding="utf-8")
        self.assertIn('id="plugin-configuration-sections"', html)
        self.assertNotIn('id="cleanup-enabled"', html)
        self.assertIn("requestedPluginId", javascript)
        self.assertIn("unavailablePluginMarkup", javascript)
        self.assertIn('data-plugin-id="cleanup"', javascript)
        self.assertIn("configuration/health", javascript)
        self.assertIn('id="configuration-dialog"', html)
        self.assertIn('id="cleanup-preview-run"', javascript)
        self.assertIn('id="cleanup-apply-run"', javascript)
        self.assertIn('id="cleanup-filing-report"', javascript)
        self.assertIn("Technical details", javascript)
        self.assertIn("Array.isArray(payload.results)", javascript)
        self.assertNotIn("What if", javascript)
        self.assertIn("Save configuration before running.", javascript)
        self.assertIn("/api/plugins/cleanup/run", javascript)
        self.assertIn('trigger: "manual"', javascript)
        self.assertIn("showConfigurationDialog", javascript)
        self.assertIn("pluginSettingsRoute", Path(__file__).with_name("plugin.js").read_text(encoding="utf-8"))

    def test_invalid_configuration_save_leaves_existing_file_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = self.snapshot(root)
            target = root / "configuration.json"
            save_configuration(storage=snapshot["storage"], path=target)
            before = target.read_bytes()
            with self.assertRaises(ValueError):
                save_configuration(storage={**snapshot["storage"], "knowledge_vault": "relative"}, path=target)
            self.assertEqual(target.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
