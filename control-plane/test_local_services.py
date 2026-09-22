import unittest
from unittest.mock import patch

import server


class LocalServiceTests(unittest.TestCase):
    def test_status_is_native_only(self):
        with patch.object(server, "gods_eye_view_status", return_value={"state": "Stopped", "detail": "not running"}):
            services = server.local_service_statuses()
        self.assertEqual([item["id"] for item in services], ["gods-eye-view"])
        self.assertNotIn("docker", {item["id"] for item in services})
        self.assertNotIn("openwebui", {item["id"] for item in services})

    def test_docker_named_service_is_manual_only(self):
        with patch.object(server, "run_action") as run_action, patch.object(server, "run_readonly") as run_readonly:
            result = server.local_service_action("docker", "start")
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "manual_only")
        self.assertIn("build/deploy", result["message"])
        run_action.assert_not_called()
        run_readonly.assert_not_called()

    def test_dev_services_are_manual_only(self):
        with patch.object(server, "run_action") as run_action:
            result = server.local_service_action("signal-dev", "start")
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "manual_only")
        run_action.assert_not_called()

    def test_docker_desktop_wsl_target_is_manual_only(self):
        with patch.object(server, "run_action") as run_action, patch.object(server, "run_readonly") as run_readonly:
            result = server.wsl_environment_action("docker-desktop", "start")
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "manual_only")
        run_action.assert_not_called()
        run_readonly.assert_not_called()


if __name__ == "__main__":
    unittest.main()
