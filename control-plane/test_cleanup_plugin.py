import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from ariadne_config import configuration_snapshot, save_configuration
from plugins.cleanup.cleanup import build_command, default_configuration, normalize_configuration


class CleanupPluginTests(unittest.TestCase):
    def storage(self, root: Path) -> dict[str, str]:
        vault = root / "Vault"
        (vault / "00_System").mkdir(parents=True, exist_ok=True)
        return {
            "knowledge_vault": str(vault), "documents": str(root / "Docs"),
            "images": str(root / "Images"), "videos": str(root / "Videos"),
            "screenshots": str(root / "Screenshots"), "intake_root": str(root / "Downloads"),
        }

    def configured(self, root: Path, sources: list[dict[str, object]] | None = None) -> dict[str, object]:
        storage = self.storage(root)
        (root / "Downloads").mkdir(exist_ok=True)
        config = default_configuration(storage)
        if sources is not None:
            config["sources"] = sources
        config["filing_classes"][0]["destination"] = str(root / "Inbox")
        config["filing_classes"][2]["destination"] = str(root / "Screenshots")
        config["filing_classes"][3]["destination"] = str(root / "Images")
        config["filing_classes"][4]["destination"] = str(root / "Videos")
        return normalize_configuration(config, storage)

    def run_organiser(self, root: Path, config: dict[str, object], action: str) -> subprocess.CompletedProcess[str]:
        powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("PowerShell is unavailable")
        context = {
            "powershell_path": powershell,
            "organiser_path": Path(__file__).resolve().parent.parent / "00_System" / "Organize-Downloads.ps1",
            "config_path": root / "runtime" / "cleanup.json",
            "result_path": root / "runtime" / "cleanup-results.json",
            "storage": self.storage(root),
        }
        return subprocess.run(build_command(action, config, context), capture_output=True, text=True, check=False)

    @staticmethod
    def read_result(root: Path) -> dict[str, object]:
        return json.loads((root / "runtime" / "cleanup-results.json").read_text(encoding="utf-8-sig"))

    def test_defaults_and_legacy_v1_migrate_to_filing_assistant_shape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            storage = self.storage(root)
            (root / "Downloads").mkdir()
            default = normalize_configuration(default_configuration(storage), storage)
            self.assertEqual(default["sources"], [{"path": str((root / "Downloads").resolve()), "enabled": True}])
            self.assertEqual(default["filing_classes"][0]["destination"], str((root / "Vault" / "Inbox").resolve()))
            legacy = {"enabled": True, "source_folder": str(root / "Downloads"), "rules": [{"category": "Markdown", "extensions": [".md"], "destination": str(root / "Inbox"), "patterns": []}, {"category": "Screenshot", "extensions": [], "destination": str(root / "Screenshots"), "patterns": ["screenshot"]}], "recurse": False, "exclusions": [], "confirmation_required": True, "collision_policy": "skip"}
            migrated = normalize_configuration(legacy, storage)
            self.assertEqual(migrated["sources"][0]["path"], str((root / "Downloads").resolve()))
            self.assertEqual(migrated["filing_classes"][0]["name"], "Markdown")
            self.assertEqual(migrated["filing_classes"][1]["patterns"], ["screenshot"])
            self.assertNotIn("source_folder", migrated)
            self.assertNotIn("rules", migrated)

    def test_multiple_sources_and_disabled_source_use_same_rules(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, second, disabled = root / "Downloads", root / "InboxDrop", root / "DisabledDrop"
            first.mkdir(); second.mkdir(); disabled.mkdir()
            config = self.configured(root, [{"path": str(first), "enabled": True}, {"path": str(second), "enabled": True}, {"path": str(disabled), "enabled": False}])
            (first / "one.md").write_text("one", encoding="utf-8")
            (second / "two.md").write_text("two", encoding="utf-8")
            (disabled / "ignored.md").write_text("ignored", encoding="utf-8")
            preview = self.run_organiser(root, config, "preview")
            self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
            self.assertEqual(preview.stdout.count("What if"), 2)
            preview_report = self.read_result(root)
            self.assertEqual([item["status"] for item in preview_report["results"]], ["planned", "planned"])
            self.assertEqual(preview_report["summary"]["planned"], len(preview_report["results"]))
            self.assertTrue(all(item["source"] in {str(first.resolve()), str(second.resolve())} for item in preview_report["results"]))
            self.assertTrue(all(item["destination"] == str((root / "Inbox").resolve()) for item in preview_report["results"]))
            self.assertTrue((first / "one.md").exists() and (second / "two.md").exists() and (disabled / "ignored.md").exists())
            applied = self.run_organiser(root, config, "apply")
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            apply_report = self.read_result(root)
            self.assertEqual([item["status"] for item in apply_report["results"]], ["moved", "moved"])
            self.assertEqual(apply_report["summary"]["moved"], 2)
            self.assertTrue((root / "Inbox" / "one.md").exists() and (root / "Inbox" / "two.md").exists())
            self.assertTrue((disabled / "ignored.md").exists())

    def test_custom_class_multiple_extensions_disabled_class_and_unmatched_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "Downloads"; source.mkdir()
            config = self.configured(root)
            config["filing_classes"].append({"name": "3D Models", "extensions": [".stl", ".obj", ".3mf"], "destination": str(root / "3D Models"), "enabled": True})
            config["filing_classes"].append({"name": "Disabled", "extensions": [".disabled"], "destination": str(root / "Should Not Exist"), "enabled": False})
            config = normalize_configuration(config, self.storage(root))
            for filename in ("part.stl", "part.obj", "part.3mf"):
                (source / filename).write_text("model", encoding="utf-8")
            (source / "leave.xyz").write_text("unknown", encoding="utf-8")
            (source / "leave.disabled").write_text("disabled", encoding="utf-8")
            result = self.run_organiser(root, config, "apply")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = self.read_result(root)
            self.assertEqual(sorted(item["status"] for item in report["results"]), ["moved", "moved", "moved"])
            self.assertTrue(all((root / "3D Models" / filename).exists() for filename in ("part.stl", "part.obj", "part.3mf")))
            self.assertTrue((source / "leave.xyz").exists() and (source / "leave.disabled").exists())
            self.assertFalse((root / "Should Not Exist").exists())

    def test_collision_is_skipped_and_existing_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "Downloads"; source.mkdir()
            config = self.configured(root)
            (source / "same.md").write_text("new", encoding="utf-8")
            (root / "Inbox").mkdir(); (root / "Inbox" / "same.md").write_text("existing", encoding="utf-8")
            result = self.run_organiser(root, config, "apply")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((source / "same.md").exists())
            self.assertEqual((root / "Inbox" / "same.md").read_text(encoding="utf-8"), "existing")
            self.assertIn("Skipped collisions:   1", result.stdout)
            report = self.read_result(root)
            self.assertEqual(report["results"][0]["status"], "duplicate")
            self.assertIn("left untouched", report["results"][0]["reason"])
            self.assertEqual(report["summary"]["skipped_collisions"], 1)

    def test_move_failure_is_reported_as_failed_with_reason(self):
        powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("PowerShell is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "Downloads"; source.mkdir()
            config = self.configured(root)
            locked = source / "locked.md"
            locked.write_text("locked", encoding="utf-8")
            lock_code = (
                f"$handle = [System.IO.File]::Open('{locked}', [System.IO.FileMode]::Open, "
                "[System.IO.FileAccess]::Read, [System.IO.FileShare]::None); "
                "try { Start-Sleep -Seconds 8 } finally { $handle.Dispose() }"
            )
            locker = subprocess.Popen([powershell, "-NoProfile", "-NonInteractive", "-Command", lock_code])
            try:
                time.sleep(0.5)
                result = self.run_organiser(root, config, "apply")
            finally:
                locker.terminate()
                locker.wait(timeout=5)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = self.read_result(root)
            self.assertEqual(report["results"][0]["status"], "failed")
            self.assertTrue(report["results"][0]["reason"])
            self.assertEqual(report["summary"]["failed"], 1)
            self.assertTrue(locked.exists())

    def test_invalid_paths_and_conflicting_extensions_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); storage = self.storage(root); (root / "Downloads").mkdir()
            with self.assertRaises(ValueError):
                normalize_configuration({"sources": [{"path": "relative", "enabled": True}], "filing_classes": []}, storage)
            with self.assertRaises(ValueError):
                normalize_configuration({"sources": [{"path": str(root / "Downloads"), "enabled": True}], "filing_classes": [{"name": "A", "extensions": [".x"], "destination": str(root / "A"), "enabled": True}, {"name": "B", "extensions": [".x"], "destination": str(root / "B"), "enabled": True}]}, storage)

    def test_configuration_is_saved_as_new_shape_and_cleanup_does_not_call_vault_ingestion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); storage = self.storage(root); (root / "Downloads").mkdir()
            config = self.configured(root)
            target = root / "user" / "configuration.json"
            save_configuration(storage=storage, plugins={"cleanup": config}, path=target)
            snapshot = configuration_snapshot(target)
            self.assertIn("sources", snapshot["plugins"]["cleanup"])
            self.assertIn("filing_classes", snapshot["plugins"]["cleanup"])
            command = build_command("preview", config, {"powershell_path": "powershell.exe", "organiser_path": Path(__file__).resolve().parent.parent / "00_System" / "Organize-Downloads.ps1", "config_path": root / "runtime" / "cleanup.json", "storage": storage})
            self.assertNotIn("Daily-Ingest.ps1", command)

    def test_structured_summary_counts_records_and_preserves_failure_reason(self):
        import server

        payload = {
            "results": [
                {"status": "planned"},
                {"status": "moved"},
                {"status": "duplicate"},
                {"status": "failed", "reason": "Access denied."},
            ],
            "summary": {"unmatched_left_alone": 7},
        }
        summary = server._structured_organiser_summary(payload, {"sources_checked": 2})
        self.assertEqual(summary, {"sources_checked": 2, "planned": 4, "moved": 1, "skipped_collisions": 1, "failed": 1, "unmatched_left_alone": 7})
        self.assertEqual(payload["results"][3]["reason"], "Access denied.")

    def test_cleanup_plugin_uses_current_repository_organiser(self):
        import server

        organiser = server.cleanup_organiser_path()
        self.assertEqual(organiser, Path(__file__).resolve().parent.parent / "00_System" / "Organize-Downloads.ps1")
        self.assertIn("$ConfigPath", organiser.read_text(encoding="utf-8"))

    def test_plugin_api_runs_preview_and_controlled_apply(self):
        powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("PowerShell is unavailable")
        import server
        from plugin_activity import PluginActivityStream
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "Downloads"; source.mkdir(); storage = self.storage(root)
            (source / "api-note.md").write_text("markdown", encoding="utf-8")
            config = self.configured(root)
            snapshot = {"storage": storage, "plugins": {"cleanup": config}}
            old_values = (server.VAULT_ROOT, server.VAULT_SYSTEM, server.VAULT_JOB_ROOT, server.HOME_CHAT_STORE, server.PLUGIN_ACTIVITY_STREAM)
            activity = PluginActivityStream(root / "activity.jsonl"); httpd = None
            try:
                server.VAULT_ROOT = root; server.VAULT_SYSTEM = Path(__file__).resolve().parent.parent / "00_System"; server.VAULT_JOB_ROOT = root / "runtime" / "jobs"; server.HOME_CHAT_STORE = server.ChatStore(root); server.PLUGIN_ACTIVITY_STREAM = activity
                httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler); threading.Thread(target=httpd.serve_forever, daemon=True).start(); base = f"http://127.0.0.1:{httpd.server_port}"
                def request(path, payload=None):
                    data = None if payload is None else json.dumps(payload).encode("utf-8")
                    request_obj = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"} if data else {})
                    with urllib.request.urlopen(request_obj, timeout=10) as response: return response.status, json.loads(response.read().decode("utf-8"))
                with patch.object(server, "configuration_snapshot", return_value=snapshot):
                    _, session = request("/api/session/start", {}); session_id = session["session_id"]
                    _, started = request("/api/plugins/cleanup/run", {"session_id": session_id, "action": "preview"})
                    self.assertEqual(started["trigger"], "manual")
                    for _ in range(80):
                        _, job = request(f"/api/vault/jobs/{started['job_id']}?session_id={session_id}")
                        if job["state"] in {"complete", "error", "cancelled"}: break
                        time.sleep(0.05)
                    self.assertEqual(job["state"], "complete", job); self.assertTrue((source / "api-note.md").exists()); self.assertIn("What if", job["output"])
                    self.assertEqual(job["results"][0]["status"], "planned"); self.assertEqual(job["results"][0]["source"], str(source.resolve())); self.assertEqual(job["results"][0]["destination"], str((root / "Inbox").resolve()))
                    self.assertEqual(job["summary"]["planned"], len(job["results"])); self.assertEqual(job["summary"]["moved"], 0); self.assertEqual(job["summary"]["sources_checked"], 1)
                    with self.assertRaises(urllib.error.HTTPError) as rejected: request("/api/plugins/cleanup/run", {"session_id": session_id, "action": "apply"})
                    self.assertEqual(rejected.exception.code, 400)
                    _, applied = request("/api/plugins/cleanup/run", {"session_id": session_id, "action": "apply", "confirm": True})
                    for _ in range(80):
                        _, applied_job = request(f"/api/vault/jobs/{applied['job_id']}?session_id={session_id}")
                        if applied_job["state"] in {"complete", "error", "cancelled"}: break
                        time.sleep(0.05)
                    self.assertEqual(applied_job["state"], "complete", applied_job); self.assertTrue((root / "Inbox" / "api-note.md").exists()); self.assertEqual(applied_job["results"][0]["status"], "moved"); self.assertEqual(applied_job["summary"]["moved"], 1)
                    events = [event for event in activity.recent(30) if event["plugin_id"] == "cleanup"]
                    self.assertTrue(any(event["state"] == "started" for event in events)); self.assertTrue(any(event["state"] == "completed" for event in events))
            finally:
                if httpd is not None: httpd.shutdown(); httpd.server_close()
                activity.close(); server.VAULT_ROOT, server.VAULT_SYSTEM, server.VAULT_JOB_ROOT, server.HOME_CHAT_STORE, server.PLUGIN_ACTIVITY_STREAM = old_values


if __name__ == "__main__":
    unittest.main()
