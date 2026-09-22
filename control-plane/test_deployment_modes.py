import unittest
from unittest.mock import patch

import server


class DeploymentModeTests(unittest.TestCase):
    def setUp(self):
        self.old_mode = server.ACTIVE_DEPLOYMENT_MODE
        self.old_profile = server.ACTIVE_PROFILE
        self.old_client = server.SIGNAL_SERVICE_CLIENT
        self.old_state = server.DEPLOYMENT_TRANSITION_STATE
        self.old_detail = server.DEPLOYMENT_TRANSITION_DETAIL
        server.ACTIVE_DEPLOYMENT_MODE = "RUN"
        server.ACTIVE_PROFILE = "RUN"
        server.SIGNAL_SERVICE_CLIENT = server.SIGNAL_SERVICE_CLIENTS["RUN"]

    def tearDown(self):
        server.ACTIVE_DEPLOYMENT_MODE = self.old_mode
        server.ACTIVE_PROFILE = self.old_profile
        server.SIGNAL_SERVICE_CLIENT = self.old_client
        server.DEPLOYMENT_TRANSITION_STATE = self.old_state
        server.DEPLOYMENT_TRANSITION_DETAIL = self.old_detail

    def test_modes_have_separate_local_and_hera_clients(self):
        self.assertEqual(server.SIGNAL_SERVICE_CLIENTS["RUN"].base_url, "http://192.168.1.200:8788")
        self.assertEqual(server.SIGNAL_SERVICE_CLIENTS["DEV"].base_url, "http://localhost:18788")
        self.assertIsNot(server.SIGNAL_SERVICE_CLIENTS["RUN"], server.SIGNAL_SERVICE_CLIENTS["DEV"])
        self.assertFalse(server.deployment_status()["persistent"])

    def test_dev_is_explicitly_unavailable_without_touching_docker(self):
        with patch.object(server, "run_action") as run_action, patch.object(server, "run_readonly") as run_readonly, patch.object(
            server, "_announce_deployment_transition"
        ), patch.object(server, "_send_avatar_event_async"):
            result = server.set_deployment_mode("DEV")

        self.assertFalse(result["ok"])
        self.assertEqual(result["mode"], "RUN")
        self.assertIn("Docker", result["message"])
        self.assertIn("unavailable", result["message"])
        self.assertEqual(server.ACTIVE_DEPLOYMENT_MODE, "RUN")
        run_action.assert_not_called()
        run_readonly.assert_not_called()

    def test_run_verifies_hera_without_dev_cleanup(self):
        with patch.object(server, "_wait_for_deployment_health", return_value=(True, {"signal": {}, "discovery": {}})), patch.object(
            server.SIGNAL_SERVICE_CLIENTS["RUN"], "briefing", return_value={"ok": True, "signals": []}
        ), patch.object(server, "run_action") as run_action, patch.object(
            server, "_announce_deployment_transition"
        ), patch.object(server, "_send_avatar_event_async"):
            server.ACTIVE_DEPLOYMENT_MODE = "DEV"
            server.ACTIVE_PROFILE = "DEV"
            result = server.set_deployment_mode("RUN")

        self.assertTrue(result["ok"])
        self.assertEqual(server.ACTIVE_DEPLOYMENT_MODE, "RUN")
        self.assertIs(server.SIGNAL_SERVICE_CLIENT, server.SIGNAL_SERVICE_CLIENTS["RUN"])
        self.assertIn("Docker was not touched", result["message"])
        run_action.assert_not_called()

    def test_shutdown_reset_is_run_and_does_not_touch_docker(self):
        server.ACTIVE_DEPLOYMENT_MODE = "DEV"
        server.ACTIVE_PROFILE = "DEV"
        with patch.object(server, "run_action") as run_action, patch.object(server, "run_readonly") as run_readonly:
            server.reset_deployment_mode_for_shutdown()
        self.assertEqual(server.ACTIVE_DEPLOYMENT_MODE, "RUN")
        self.assertEqual(server.ACTIVE_PROFILE, "RUN")
        self.assertIs(server.SIGNAL_SERVICE_CLIENT, server.SIGNAL_SERVICE_CLIENTS["RUN"])
        self.assertFalse(server.deployment_status()["persistent"])
        run_action.assert_not_called()
        run_readonly.assert_not_called()


if __name__ == "__main__":
    unittest.main()
