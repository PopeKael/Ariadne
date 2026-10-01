"""Exercise control ownership, worker state and safe recovery without models."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import server


class VaultIngestControlTests(unittest.TestCase):
    def test_controlled_ingest_is_not_killed_by_elapsed_batch_timeout(self):
        for action in ("ingest", "full_rebuild"):
            with patch.object(server, "JOBS", {"job": {"action": action, "controllable": True}}), \
                    patch.object(server.time, "sleep") as sleep, \
                    patch.object(server, "_terminate_process") as terminate:
                server._timeout_job("job")
                sleep.assert_not_called()
                terminate.assert_not_called()

    def test_pause_resume_stop_and_terminal_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "control.json"
            status_path = Path(str(path) + ".status.json")
            worker = Mock()
            worker.poll.return_value = None
            job = {"session_id": "owner", "action": "ingest", "state": "running", "process": worker,
                   "control_path": path, "control_status_path": status_path, "controllable": True}
            with patch.object(server, "JOBS", {"job": job}):
                with self.assertRaises(RuntimeError):
                    server.control_vault_action("other", "job", "stop")
                for command, stored in (("pause", "pause"), ("resume", "run"), ("stop", "stop")):
                    result = server.control_vault_action("owner", "job", command)
                    self.assertTrue(result["ok"])
                    self.assertEqual(json.loads(path.read_text())["command"], stored)
                status_path.write_text(json.dumps({"state": "paused", "run_dir": str(root), "controllable": True}))
                self.assertEqual(server.job_payload("job")["state"], "paused")
                self.assertNotIn("control_path", server.job_payload("job"))
                job["state"] = "error"
                status_path.write_text(json.dumps({"state": "stopped", "run_dir": str(root), "controllable": True}))
                self.assertEqual(server.job_payload("job")["state"], "stopped")

    def test_stopped_resume_restarts_only_its_saved_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "00_System/Data/vault-v2/daily-current"
            run.mkdir(parents=True)
            status = root / "status.json"
            status.write_text(json.dumps({"state": "stopped", "run_dir": str(run)}))
            worker = Mock()
            worker.poll.return_value = 76
            job = {"session_id": "owner", "action": "ingest", "state": "error", "process": worker,
                   "control_path": root / "command.json", "control_status_path": status}
            with patch.object(server, "VAULT_ROOT", root), patch.object(server, "JOBS", {"job": job}), \
                    patch.object(server, "start_vault_action", return_value="resumed") as start:
                self.assertEqual(server.control_vault_action("owner", "job", "resume")["job_id"], "resumed")
                start.assert_called_once_with("owner", "ingest", resume_run=run)


if __name__ == "__main__":
    unittest.main()
