from __future__ import annotations

import json
import tempfile
import unittest
import threading
import urllib.request
from pathlib import Path
from unittest.mock import patch

from production_projects import ASSET_SCHEMA, PROJECT_SCHEMA, ProductionProjectStore
import server


class ProductionProjectStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "projects"
        self.store = ProductionProjectStore(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_create_builds_a_provider_neutral_manifest_and_handoff_folders(self):
        project = self.store.create("Bangkok After Rain")
        directory = self.root / "bangkok-after-rain"
        self.assertEqual(project["schema"], PROJECT_SCHEMA)
        self.assertEqual(project["project_id"], "bangkok-after-rain")
        self.assertEqual(project["components"]["music"]["state"], "provider_required")
        self.assertTrue((directory / "music" / "accepted").is_dir())
        self.assertTrue((directory / "candidates" / "images").is_dir())
        self.assertTrue((directory / "accepted" / "video").is_dir())
        self.assertTrue((directory / "handoff").is_dir())

    def test_candidate_sidecar_preserves_provenance_and_acceptance_is_explicit(self):
        project = self.store.create("Night Market")
        candidate = self.store.image_candidate_directory(project["project_id"]) / "market.png"
        candidate.write_bytes(b"not-a-real-png")
        asset = self.store.record_image_candidate(project["project_id"], candidate, {
            "engine": {"id": "comfyui"},
            "prompt": "A wet Bangkok night market",
            "settings": {"seed": 42},
        })
        sidecar = candidate.with_suffix(".json")
        self.assertEqual(asset["schema"], ASSET_SCHEMA)
        self.assertEqual(asset["status"], "candidate")
        self.assertTrue(sidecar.is_file())
        self.assertEqual(json.loads(sidecar.read_text(encoding="utf-8"))["provenance"]["prompt"], "A wet Bangkok night market")
        self.assertTrue(candidate.is_file())

        accepted = self.store.accept_image(project["project_id"], asset["asset_id"])
        accepted_media = self.root / project["project_id"] / accepted["files"]["media"]
        accepted_metadata = self.root / project["project_id"] / accepted["files"]["metadata"]
        self.assertEqual(accepted["status"], "accepted")
        self.assertFalse(candidate.exists())
        self.assertTrue(accepted_media.is_file())
        self.assertTrue(accepted_metadata.is_file())
        self.assertEqual(json.loads(accepted_metadata.read_text(encoding="utf-8"))["status"], "accepted")

    def test_invalid_project_identifier_cannot_escape_the_project_root(self):
        with self.assertRaises(ValueError):
            self.store.project("../outside")

    def test_http_project_creation_uses_the_project_store_without_touching_media_engines(self):
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
        try:
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            request = urllib.request.Request(
                f"http://127.0.0.1:{httpd.server_port}/api/sequence/projects",
                data=b'{"name":"HTTP Project"}',
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with patch.object(server, "SEQUENCE_PROJECTS", self.store):
                with urllib.request.urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read())
            self.assertEqual(payload["project"]["project_id"], "http-project")
            self.assertFalse((self.root / "http-project" / "candidates" / "images").joinpath("unexpected.png").exists())
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_http_acceptance_moves_only_the_requested_candidate_and_serves_it_back(self):
        project = self.store.create("HTTP Acceptance")
        candidate = self.store.image_candidate_directory(project["project_id"]) / "candidate.png"
        candidate.write_bytes(b"png-payload")
        asset = self.store.record_image_candidate(project["project_id"], candidate, {"prompt": "exact prompt"})
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
        try:
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{httpd.server_port}"
            accept = urllib.request.Request(
                f"{base}/api/sequence/projects/{project['project_id']}/assets/{asset['asset_id']}/accept",
                data=b"{}", headers={"Content-Type": "application/json"}, method="POST",
            )
            with patch.object(server, "SEQUENCE_PROJECTS", self.store):
                with urllib.request.urlopen(accept, timeout=5) as response:
                    accepted = json.loads(response.read())
                self.assertEqual(accepted["asset"]["status"], "accepted")
                content = f"{base}/api/sequence/projects/{project['project_id']}/assets/{asset['asset_id']}/content"
                with urllib.request.urlopen(content, timeout=5) as response:
                    self.assertEqual(response.read(), b"png-payload")
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
