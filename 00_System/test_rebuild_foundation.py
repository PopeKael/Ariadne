from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_rebuild import build_manifest, canonical_content, content_hash, manifest_bytes
from integrate_rebuild import current_source_relative
from daily_rebuild_ingest import choose_output


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

    def test_prior_destination_is_ignored_for_new_revision(self) -> None:
        record = {
            "stable_source_id": "url:https://example.com/a",
            "relative_path": "Inbox/new.md",
            "canonical_content_sha256": "new-hash",
        }
        prior = {
            record["stable_source_id"]: {
                "new_path": "Processed/old.md",
                "content_sha256": "old-hash",
            }
        }
        self.assertEqual(current_source_relative(record, prior), "Inbox/new.md")

    def test_daily_runner_reuses_matching_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            checkpoint = root / "00_System/Data/rebuild-v1/daily/20260725T010000Z"
            checkpoint.mkdir(parents=True)
            (checkpoint / "state.json").write_text(
                json.dumps({"manifest_sha256": "same-batch"}), encoding="utf-8"
            )
            self.assertEqual(choose_output(root, "same-batch"), checkpoint)


if __name__ == "__main__":
    unittest.main()
