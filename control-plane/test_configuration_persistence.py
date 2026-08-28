import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

import server
from ariadne_config import configuration_snapshot, save_configuration
from plugins.cleanup.cleanup import default_configuration, effective_configuration, normalize_configuration


class ConfigurationPersistenceTests(unittest.TestCase):
    def storage(self, root: Path) -> dict[str, str]:
        vault = root / "Vault"
        (vault / "00_System").mkdir(parents=True, exist_ok=True)
        return {
            "knowledge_vault": str(vault),
            "documents": str(root / "Documents"),
            "images": str(root / "Images"),
            "videos": str(root / "Videos"),
            "screenshots": str(root / "Screenshots"),
            "intake_root": str(root / "Downloads"),
        }

    def config_a(self, root: Path) -> dict[str, object]:
        storage = self.storage(root)
        source = root / "Source A"
        source.mkdir(parents=True, exist_ok=True)
        config = default_configuration(storage)
        config["sources"] = [{"path": str(source), "enabled": True}]
        return normalize_configuration(config, storage)

    def config_b(self, root: Path) -> dict[str, object]:
        storage = self.storage(root)
        first = root / "Source A"
        second = root / "Source B"
        first.mkdir(parents=True, exist_ok=True)
        second.mkdir(parents=True, exist_ok=True)
        config = self.config_a(root)
        config["sources"] = [
            {"path": str(first), "enabled": True},
            {"path": str(second), "enabled": False},
        ]
        config["filing_classes"].append({
            "name": "Three D Models",
            "extensions": [".3mf", ".stl"],
            "destination": str(root / "Models"),
            "enabled": True,
            "patterns": [],
        })
        config["recurse"] = True
        config["confirmation_required"] = False
        return normalize_configuration(config, storage)

    def config_c(self, root: Path) -> dict[str, object]:
        config = self.config_b(root)
        config["confirmation_required"] = True
        config["sources"] = [{"path": str(root / "Source B"), "enabled": True}]
        return normalize_configuration(config, self.storage(root))

    @staticmethod
    def request(base: str, path: str, payload: dict[str, object] | None = None) -> tuple[int, dict[str, object]]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            base + path,
            data=data,
            headers={"Content-Type": "application/json"} if data else {},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def restart_read(self, target: Path) -> dict[str, object]:
        code = (
            "import json, sys; "
            "sys.path.insert(0, sys.argv[1]); "
            "from ariadne_config import configuration_snapshot; "
            "from plugins.cleanup.cleanup import effective_configuration; "
            "snapshot = configuration_snapshot(); "
            "config, error = effective_configuration(snapshot['plugins'], snapshot['storage']); "
            "print(json.dumps({'path': snapshot['path'], 'config': config, 'error': error}, sort_keys=True))"
        )
        environment = dict(os.environ)
        environment["ARIADNE_CONFIG_PATH"] = str(target)
        for key in ("ARIADNE_VAULT_ROOT", "ARIADNE_DOCUMENTS_ROOT", "ARIADNE_IMAGES_ROOT", "ARIADNE_VIDEOS_ROOT", "ARIADNE_SCREENSHOTS_ROOT", "ARIADNE_INTAKE_ROOT"):
            environment.pop(key, None)
        result = subprocess.run(
            [sys.executable, "-c", code, str(Path(__file__).resolve().parent)],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_api_save_persists_cleanup_and_fresh_process_reads_config_b(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "user" / "configuration.json"
            storage = self.storage(root)
            config_a = self.config_a(root)
            config_b = self.config_b(root)
            save_configuration(storage=storage, plugins={"cleanup": config_a}, path=target)
            request_payload = {"storage": storage, "plugins": {"cleanup": config_b}}
            old_values = (server.VAULT_ROOT, server.VAULT_SYSTEM, server.VAULT_JOB_ROOT, server.HOME_CHAT_STORE)
            httpd = None
            try:
                environment = dict(os.environ)
                environment["ARIADNE_CONFIG_PATH"] = str(target)
                for key in ("ARIADNE_VAULT_ROOT", "ARIADNE_DOCUMENTS_ROOT", "ARIADNE_IMAGES_ROOT", "ARIADNE_VIDEOS_ROOT", "ARIADNE_SCREENSHOTS_ROOT", "ARIADNE_INTAKE_ROOT"):
                    environment.pop(key, None)
                with patch.dict(os.environ, environment, clear=True):
                    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
                    threading.Thread(target=httpd.serve_forever, daemon=True).start()
                    base = f"http://127.0.0.1:{httpd.server_port}"
                    status, response = self.request(base, "/api/configuration", request_payload)
                    self.assertEqual(status, 200)
                    self.assertTrue(response["persistence"]["verified"])
                    self.assertEqual(Path(response["persistence"]["path"]), target.resolve())
                    self.assertRegex(response["persistence"]["revision"], r"^[0-9a-f]{64}$")
                    self.assertEqual(response["plugins"]["cleanup"]["config"], config_b)

                    persisted = json.loads(target.read_text(encoding="utf-8"))
                    self.assertEqual(persisted["plugins"]["cleanup"], config_b)
                    self.assertEqual(configuration_snapshot(target)["plugins"]["cleanup"], config_b)
                    loaded, error = effective_configuration(configuration_snapshot(target)["plugins"], storage)
                    self.assertIsNone(error)
                    self.assertEqual(loaded, config_b)

                    _, reloaded = self.request(base, "/api/configuration")
                    self.assertEqual(reloaded["plugins"]["cleanup"]["config"], config_b)
            finally:
                if httpd is not None:
                    httpd.shutdown()
                    httpd.server_close()
                server.VAULT_ROOT, server.VAULT_SYSTEM, server.VAULT_JOB_ROOT, server.HOME_CHAT_STORE = old_values

            restarted = self.restart_read(target)
            self.assertEqual(Path(restarted["path"]), target.resolve())
            self.assertIsNone(restarted["error"])
            self.assertEqual(restarted["config"], config_b)

    def test_defaults_and_partial_current_schema_preserve_saved_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            storage = self.storage(root)
            (root / "Downloads").mkdir()
            defaults, error = effective_configuration({}, storage)
            self.assertIsNone(error)
            self.assertEqual(defaults["sources"], [{"path": str((root / "Downloads").resolve()), "enabled": True}])

            source = root / "Custom Source"
            source.mkdir()
            destination = root / "Custom Destination"
            partial = {
                "enabled": False,
                "sources": [{"path": str(source), "enabled": True}],
                "filing_classes": [{
                    "name": "Custom",
                    "extensions": [".custom"],
                    "destination": str(destination),
                    "enabled": True,
                }],
                "confirmation_required": False,
            }
            normalized = normalize_configuration(partial, storage)
            self.assertEqual(normalized["sources"], [{"path": str(source.resolve()), "enabled": True}])
            self.assertEqual(normalized["filing_classes"][0]["name"], "Custom")
            self.assertEqual(normalized["filing_classes"][0]["destination"], str(destination.resolve()))
            self.assertFalse(normalized["confirmation_required"])
            self.assertTrue(normalized["recurse"] is False)
            self.assertEqual(normalized["collision_policy"], "skip")
            self.assertEqual(normalized["unmatched_policy"], "leave_in_place")

    def test_multiple_saves_end_with_c_and_do_not_resurrect_a_or_b(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "configuration.json"
            storage = self.storage(root)
            configs = [self.config_a(root), self.config_b(root), self.config_c(root)]
            for expected in configs:
                save_configuration(storage=storage, plugins={"cleanup": expected}, path=target)
                snapshot = configuration_snapshot(target)
                loaded, error = effective_configuration(snapshot["plugins"], snapshot["storage"])
                self.assertIsNone(error)
                self.assertEqual(loaded, expected)
            restarted = self.restart_read(target)
            self.assertEqual(restarted["config"], configs[-1])
            self.assertNotEqual(restarted["config"], configs[0])
            self.assertNotEqual(restarted["config"], configs[1])


if __name__ == "__main__":
    unittest.main()
