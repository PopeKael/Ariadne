import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ariadne_config import DEFAULT_STORAGE, configuration_snapshot, save_storage  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
