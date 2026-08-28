import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from plugin_registry import MANIFEST_VERSION, ManifestValidationError, PluginRegistry, validate_manifest  # noqa: E402


def manifest(plugin_id="example.test"):
    return {
        "manifest_version": MANIFEST_VERSION,
        "plugin_id": plugin_id,
        "name": "Example",
        "version": "0.1.0",
        "description": "Example capability",
        "author": "Test",
        "plugin_type": "capability",
        "capabilities": ["example.run"],
        "entry_point": "example:run",
        "ui": {"available": False, "route": None},
        "settings": {"available": False, "route": None},
        "permissions": [],
        "dependencies": [],
        "hardware_requirements": {},
        "resource_requirements": {},
        "startup": "on_demand",
        "enabled": True,
        "health": {"state": "healthy", "detail": "Ready"},
        "activity": {"supported": True, "transport": "ariadne-core", "progress": True},
    }


class PluginRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bundled = self.root / "bundled"
        self.user = self.root / "user"

    def tearDown(self):
        self.temp.cleanup()

    def write_manifest(self, root, folder, value):
        path = root / folder / "plugin.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_bundled_manifest_is_discovered_and_capability_is_queryable(self):
        self.write_manifest(self.bundled, "example", manifest())
        registry = PluginRegistry(self.bundled, self.user)
        records = registry.discover()
        self.assertEqual(len(records), 1)
        self.assertEqual(registry.providers_for("example.run")[0].manifest["plugin_id"], "example.test")
        payload = registry.payload()
        self.assertEqual(payload["capabilities"], {"example.run": ["example.test"]})
        self.assertEqual(payload["plugins"][0]["source"], "bundled")

    def test_malformed_manifest_becomes_unavailable_record(self):
        self.write_manifest(self.bundled, "broken", {"manifest_version": MANIFEST_VERSION})
        registry = PluginRegistry(self.bundled, self.user)
        payload = registry.payload()
        self.assertEqual(payload["plugin_count"], 1)
        self.assertEqual(payload["plugins"][0]["status"], "invalid")
        self.assertTrue(payload["plugins"][0]["error"])

    def test_duplicate_ids_are_not_silently_overridden(self):
        self.write_manifest(self.bundled, "one", manifest())
        self.write_manifest(self.user, "two", manifest())
        records = PluginRegistry(self.bundled, self.user).discover()
        self.assertEqual([record.status for record in records], ["healthy", "invalid"])

    def test_unknown_fields_and_bad_routes_are_rejected(self):
        value = manifest()
        value["future_plugin_field"] = True
        with self.assertRaises(ManifestValidationError):
            validate_manifest(value)
        value = manifest()
        value["ui"] = {"available": True, "route": "https://example.test"}
        with self.assertRaises(ManifestValidationError):
            validate_manifest(value)

    def test_optional_action_metadata_is_validated_for_scheduler_readiness(self):
        value = manifest()
        value["actions"] = ["preview", "apply"]
        value["action_metadata"] = {"preview": {"schedulable": True, "mutating": False}, "apply": {"schedulable": True, "mutating": True}}
        normalized = validate_manifest(value)
        self.assertEqual(normalized["action_metadata"]["preview"]["mutating"], False)
        value["action_metadata"]["missing"] = {"schedulable": True}
        with self.assertRaises(ManifestValidationError):
            validate_manifest(value)


if __name__ == "__main__":
    unittest.main()
