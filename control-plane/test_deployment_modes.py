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

    def test_dev_waits_for_identity_refresh_before_switching_home(self):
        dev_health = {
            "signal": {"ok": True, "state": "healthy", "environment": "dev", "instance": "local-dev-signal", "build_sha": "abc123"},
            "discovery": {
                "ok": True, "state": "healthy", "environment": "dev", "instance": "local-dev-discovery",
                "build_sha": "abc123", "signal_service_configured": True, "signal_service_url": "http://signal:8788",
            },
        }
        with patch.object(server, "start_docker_desktop", return_value={"ok": True}), patch.object(
            server, "_set_dev_build_sha", return_value="abc123"
        ), patch.object(server, "run_action", return_value={"ok": True, "detail": "started"}) as compose, patch.object(
            server, "_wait_for_deployment_health", return_value=(True, dev_health)
        ), patch.object(server, "_refresh_dev_data", return_value={"ok": True, "accepted_articles": 4}), patch.object(
            server.SIGNAL_SERVICE_CLIENTS["DEV"], "briefing", return_value={"ok": True, "signals": []}
        ), patch.object(server, "_announce_deployment_transition"), patch.object(
            server, "_send_avatar_event_async"
        ):
            result = server.set_deployment_mode("DEV")

        self.assertTrue(result["ok"])
        self.assertEqual(server.ACTIVE_DEPLOYMENT_MODE, "DEV")
        self.assertIs(server.SIGNAL_SERVICE_CLIENT, server.SIGNAL_SERVICE_CLIENTS["DEV"])
        self.assertIn("compose.local.yaml", " ".join(compose.call_args.args[0]))

    def test_run_verifies_hera_before_stopping_dev(self):
        with patch.object(server, "_wait_for_deployment_health", return_value=(True, {"signal": {}, "discovery": {}})), patch.object(
            server.SIGNAL_SERVICE_CLIENTS["RUN"], "briefing", return_value={"ok": True, "signals": []}
        ), patch.object(server, "_stop_dev_stack", return_value={"ok": True, "message": "stopped"}), patch.object(
            server, "_announce_deployment_transition"), patch.object(server, "_send_avatar_event_async"
        ):
            server.ACTIVE_DEPLOYMENT_MODE = "DEV"
            server.ACTIVE_PROFILE = "DEV"
            result = server.set_deployment_mode("RUN")

        self.assertTrue(result["ok"])
        self.assertEqual(server.ACTIVE_DEPLOYMENT_MODE, "RUN")
        self.assertIs(server.SIGNAL_SERVICE_CLIENT, server.SIGNAL_SERVICE_CLIENTS["RUN"])

    def test_shutdown_reset_is_run_and_non_persistent(self):
        server.ACTIVE_DEPLOYMENT_MODE = "DEV"
        server.ACTIVE_PROFILE = "DEV"
        with patch.object(server, "_stop_dev_stack", return_value={"ok": True, "message": "stopped"}):
            server.reset_deployment_mode_for_shutdown()
        self.assertEqual(server.ACTIVE_DEPLOYMENT_MODE, "RUN")
        self.assertEqual(server.ACTIVE_PROFILE, "RUN")
        self.assertIs(server.SIGNAL_SERVICE_CLIENT, server.SIGNAL_SERVICE_CLIENTS["RUN"])
        self.assertFalse(server.deployment_status()["persistent"])


if __name__ == "__main__":
    unittest.main()
