import unittest
from unittest.mock import patch

import server


class FakeProcess:
    def __init__(self):
        self.terminated = 0
        self.killed = 0
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated += 1
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed += 1
        self.returncode = -9


class ShutdownLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.old_sessions = server.SESSIONS
        self.old_jobs = server.JOBS
        self.old_wsl = server.WSL_SESSION_PROCESSES
        self.old_shutdown_requested = server.SHUTDOWN_REQUESTED
        self.old_idle_shutdown_done = server.IDLE_SHUTDOWN_DONE
        self.old_profile = server.ACTIVE_PROFILE
        self.old_http_server = server.HTTP_SERVER
        self.old_shutdown_status = server.SHUTDOWN_STATUS
        server.SESSIONS = {}
        server.JOBS = {}
        server.WSL_SESSION_PROCESSES = {}
        server.SHUTDOWN_REQUESTED = False
        server.IDLE_SHUTDOWN_DONE = False
        server.ACTIVE_PROFILE = "Interactive AI"
        server.HTTP_SERVER = None
        server.SHUTDOWN_STATUS = {
            "state": "running",
            "message": "Ariadne is running.",
            "started_at": None,
            "completed_at": None,
        }

    def tearDown(self):
        server.SESSIONS = self.old_sessions
        server.JOBS = self.old_jobs
        server.WSL_SESSION_PROCESSES = self.old_wsl
        server.SHUTDOWN_REQUESTED = self.old_shutdown_requested
        server.IDLE_SHUTDOWN_DONE = self.old_idle_shutdown_done
        server.ACTIVE_PROFILE = self.old_profile
        server.HTTP_SERVER = self.old_http_server
        server.SHUTDOWN_STATUS = self.old_shutdown_status

    def test_shutdown_stops_tracked_jobs_and_managed_wsl_helpers(self):
        plugin = FakeProcess()
        wsl = FakeProcess()
        server.SESSIONS["session"] = {"jobs": {"job"}}
        server.JOBS["job"] = {"process": plugin, "state": "running"}
        server.WSL_SESSION_PROCESSES["Ubuntu"] = wsl

        with patch.object(
            server, "_terminate_process", side_effect=lambda process: process.terminate()
        ) as terminate, patch.object(server, "_unload_ollama_models") as unload, patch.object(
            server, "release_workloads"
        ) as release, patch.object(server, "run_readonly") as run_readonly, patch.object(
            server, "_send_avatar_event_with_retry", return_value=True
        ) as avatar_event:
            server.shutdown_all_workloads(stop_server=False)

        self.assertEqual(terminate.call_count, 2)
        self.assertEqual(plugin.terminated, 1)
        self.assertEqual(wsl.terminated, 1)
        self.assertEqual(server.JOBS["job"]["state"], "cancelled")
        self.assertEqual(server.JOBS["job"]["message"], "Cancelled when Ariadne shut down.")
        self.assertEqual(server.SESSIONS, {})
        self.assertEqual(server.WSL_SESSION_PROCESSES, {})
        unload.assert_called_once_with()
        release.assert_called_once_with(force=True)
        run_readonly.assert_called_once_with(["wsl.exe", "--terminate", "Ubuntu"], timeout=30.0)
        self.assertTrue(server.SHUTDOWN_REQUESTED)
        self.assertEqual(server.shutdown_status_payload()["state"], "complete")
        self.assertIn("Docker was not touched", server.shutdown_status_payload()["message"])
        self.assertEqual(server.ACTIVE_PROFILE, "RUN")
        avatar_event.assert_called_once()

    def test_shutdown_is_idempotent(self):
        process = FakeProcess()
        server.SESSIONS["session"] = {"jobs": {"job"}}
        server.JOBS["job"] = {"process": process, "state": "running"}

        with patch.object(
            server, "_terminate_process", side_effect=lambda process: process.terminate()
        ), patch.object(server, "_unload_ollama_models"), patch.object(server, "release_workloads"):
            server.shutdown_all_workloads(stop_server=False)
            server.shutdown_all_workloads(stop_server=False)

        self.assertEqual(process.terminated, 1)


if __name__ == "__main__":
    unittest.main()
