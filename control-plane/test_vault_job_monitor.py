import time
import unittest
from unittest.mock import patch

import server


class FakeProcess:
    def __init__(self, lines):
        self.stdout = iter(lines)
        self.returncode = 0

    def wait(self):
        return self.returncode

    def poll(self):
        return self.returncode


class VaultJobMonitorTests(unittest.TestCase):
    def setUp(self):
        self.old_jobs = server.JOBS
        self.old_sessions = server.SESSIONS
        server.JOBS = {}
        server.SESSIONS = {"session": {"last_seen": time.monotonic(), "jobs": set()}}

    def tearDown(self):
        server.JOBS = self.old_jobs
        server.SESSIONS = self.old_sessions

    def test_action_output_is_available_while_worker_is_running(self):
        process = FakeProcess(["Rebuild scope: 2 source(s)\n", "[1/2] Inbox/note.md\n"])
        server.JOBS["job"] = {
            "session_id": "session", "state": "running", "message": "Starting…",
            "output": "", "process": process,
        }

        with patch.object(server, "_organiser_summary", return_value={}):
            server._watch_action("job", process)

        payload = server.job_payload("job")
        self.assertEqual(payload["state"], "complete")
        self.assertIn("Rebuild scope", payload["output"])
        self.assertIn("[1/2] Inbox/note.md", payload["output"])

    def test_job_poll_refreshes_session_lease(self):
        before = server.SESSIONS["session"]["last_seen"]
        server.JOBS["job"] = {"session_id": "session", "state": "running", "message": "Working…"}

        self.assertIsNotNone(server._session("session"))
        self.assertGreaterEqual(server.SESSIONS["session"]["last_seen"], before)

    def test_intake_has_long_running_timeout(self):
        self.assertGreaterEqual(server.VAULT_ACTION_TIMEOUT_SECONDS["ingest"], 3600)


if __name__ == "__main__":
    unittest.main()
