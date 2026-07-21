from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_rebuild import build_manifest, canonical_content, content_hash, manifest_bytes


class RebuildFoundationTests(unittest.TestCase):
    def write_source(self, root: Path, folder: str, name: str, content: str) -> Path:
        path = root / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_normalisation_is_documented_and_repeatable(self) -> None:
        self.assertEqual(canonical_content("a  \r\nb\t\r\n\r\n"), "a\nb")
        self.assertEqual(content_hash("a  \r\nb\t\r\n\r\n"), content_hash("a\nb"))

    def test_manifest_does_not_change_source_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self.write_source(root, "Inbox", "one.md", "---\ntitle: One\nsource: https://example.com/a#part\n---\nBody  \n")
            before = path.read_bytes()
            manifest = build_manifest(root)
            self.assertEqual(before, path.read_bytes())
            self.assertEqual(manifest["records"][0]["stable_source_id"], "url:https://example.com/a")

    def test_duplicate_content_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            content = "---\ntitle: Same\n---\nSame body\n"
            self.write_source(root, "Inbox", "one.md", content)
            self.write_source(root, "Processed", "two.md", content)
            validation = build_manifest(root)["validation"]
            self.assertEqual(len(validation["duplicate_content_hashes"]), 1)

    def test_rebuild_manifest_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_source(root, "Inbox", "one.md", "---\ntitle: One\n---\nBody\n")
            first = build_manifest(root)
            second = build_manifest(root)
            self.assertEqual(manifest_bytes(first), manifest_bytes(second))
            self.assertEqual(json.loads(manifest_bytes(first)), json.loads(manifest_bytes(second)))


if __name__ == "__main__":
    unittest.main()
