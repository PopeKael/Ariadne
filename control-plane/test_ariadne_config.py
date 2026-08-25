import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ariadne_config import (  # noqa: E402
    CANONICAL_AVATAR_STATES,
    DEFAULT_STORAGE,
    avatar_pack_status,
    configuration_snapshot,
    effective_avatar,
    save_avatar,
    save_storage,
)


class AriadneConfigurationTests(unittest.TestCase):
    def test_saved_storage_is_used_after_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Vault"
            (root / "00_System").mkdir(parents=True)
            path = Path(temporary) / "configuration.json"
            values = dict(DEFAULT_STORAGE)
            values["knowledge_vault"] = str(root)
            values["documents"] = str(Path(temporary) / "Docs")
            with patch.dict(os.environ, {}, clear=True):
                save_storage(values, path)
                snapshot = configuration_snapshot(path)
            self.assertEqual(snapshot["storage"]["knowledge_vault"], str(root.resolve()))
            self.assertEqual(snapshot["sources"]["knowledge_vault"], "saved Ariadne configuration")
            self.assertTrue(path.read_text(encoding="utf-8").endswith("\n"))

    def test_environment_override_wins_without_mutating_saved_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Vault"
            (root / "00_System").mkdir(parents=True)
            path = Path(temporary) / "configuration.json"
            values = dict(DEFAULT_STORAGE)
            values["knowledge_vault"] = str(root)
            save_storage(values, path)
            override = Path(temporary) / "Override"
            with patch.dict(os.environ, {"ARIADNE_VAULT_ROOT": str(override)}, clear=False):
                snapshot = configuration_snapshot(path)
            self.assertEqual(snapshot["storage"]["knowledge_vault"], str(override.resolve()))
            self.assertEqual(snapshot["sources"]["knowledge_vault"], "environment override")

    def test_knowledge_vault_requires_operational_system_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            values = dict(DEFAULT_STORAGE)
            values["knowledge_vault"] = str(Path(temporary) / "not-a-vault")
            with self.assertRaises(ValueError) as raised:
                save_storage(values, Path(temporary) / "configuration.json")
            self.assertIn("Knowledge Vault", str(raised.exception))

    def test_avatar_defaults_are_backward_compatible(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "legacy-configuration.json"
            path.write_text("{\"version\": 1, \"storage\": {}}", encoding="utf-8")
            avatar, sources = effective_avatar(path)
            self.assertTrue(avatar["enabled"])
            self.assertEqual(sources["asset_directory"], "installation default")

    def test_avatar_save_preserves_existing_storage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Vault"
            (root / "00_System").mkdir(parents=True)
            path = Path(temporary) / "configuration.json"
            values = dict(DEFAULT_STORAGE)
            values["knowledge_vault"] = str(root)
            save_storage(values, path)
            avatar_root = Path(temporary) / "avatars"
            avatar_root.mkdir()
            save_avatar({"enabled": False, "asset_directory": str(avatar_root)}, path)
            saved = __import__("json").loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["storage"]["knowledge_vault"], str(root.resolve()))
            self.assertFalse(saved["avatar"]["enabled"])

    def test_avatar_pack_reports_all_canonical_states(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "avatars"
            root.mkdir()
            (root / "avatar_states.json").write_text(
                '{"version": 1, "states": {"idle": "idle.png", "thinking": "../escape.png"}}',
                encoding="utf-8",
            )
            (root / "idle.png").write_bytes(b"placeholder")
            status = avatar_pack_status(root)
            self.assertEqual(status["state"], "partial")
            self.assertEqual(len(status["states"]), len(CANONICAL_AVATAR_STATES))
            self.assertEqual(status["available_count"], 1)
            self.assertEqual(status["states"][2]["state"], "invalid")


if __name__ == "__main__":
    unittest.main()
