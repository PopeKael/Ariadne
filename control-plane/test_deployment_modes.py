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

    def test_dev_becomes_active_while_long_refresh_continues(self):
        dev_health = {
            "signal": {},
            "discovery": {},
        }
        with patch.object(server, "start_docker_desktop", return_value={"ok": True}), patch.object(
            server, "_set_dev_build_sha", return_value="abc123"
        ), patch.object(server, "run_action", return_value={"ok": True, "detail": "started"}), patch.object(
            server, "_wait_for_deployment_health", return_value=(True, dev_health)
        ), patch.object(
            server, "_refresh_dev_data", return_value={"ok": True, "running": True}
        ), patch.object(
            server.SIGNAL_SERVICE_CLIENTS["DEV"], "briefing", return_value={"ok": True, "signals": []}
        ), patch.object(server, "_start_dev_refresh_background") as background, patch.object(
            server, "_announce_deployment_transition"
        ), patch.object(server, "_send_avatar_event_async"):
            result = server.set_deployment_mode("DEV")

        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "DEV")
        self.assertEqual(server.ACTIVE_DEPLOYMENT_MODE, "DEV")
        self.assertEqual(server.deployment_status()["transition_state"], "starting")
        self.assertIn("continues in the background", server.deployment_status()["transition_detail"])
        background.assert_called_once_with()

    def test_dev_refresh_waits_for_discovery_startup_refresh(self):
        responses = iter([
            {"ok": False, "running": True},
            {"ok": True, "refresh_running": True},
            {"ok": True, "refresh_running": False, "last_refresh": {"ok": True, "accepted_articles": 7}},
        ])
        with patch.object(server, "post_json", return_value=next(responses)), patch.object(
            server, "json_http", side_effect=lambda *_args, **_kwargs: next(responses)
        ), patch.object(server.time, "sleep"):
            result = server._refresh_dev_data()

        self.assertTrue(result["ok"])
        self.assertEqual(result["accepted_articles"], 7)

    def test_dev_refresh_failure_preserves_discovery_details(self):
        with patch.object(server, "post_json", return_value={
            "ok": False,
            "attempted_sources": 38,
            "successful_sources": 0,
            "story_count": 0,
            "failures": [{"source": "BBC World", "error": "TimeoutError: feed unavailable"}],
        }):
            with self.assertRaisesRegex(RuntimeError, "successful_sources=0.*BBC World"):
                server._refresh_dev_data()

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
