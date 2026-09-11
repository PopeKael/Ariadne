import unittest
from unittest.mock import patch

import server


def row(name, state="running", status="Up 1 minute"):
    return {"Names": name, "State": state, "Status": status, "Image": "test/image:local"}


class LocalServiceTests(unittest.TestCase):
    def test_status_is_limited_to_named_local_services(self):
        rows = [
            row("portainer"),
            row("open-webui", status="Up 1 minute (healthy)"),
            row("ariadne-signal-dev", state="exited", status="Exited (0)"),
            row("unrelated-workload"),
        ]
        with patch.object(server, "docker_desktop_state", return_value="Running"), patch.object(
            server, "_docker_container_rows", return_value=(rows, None)
        ):
            services = {item["id"]: item for item in server.local_service_statuses({"available": True})}

        self.assertEqual(services["docker"]["state"], "Running")
        self.assertEqual(services["portainer"]["state"], "Running")
        self.assertEqual(services["openwebui"]["state"], "Running")
        self.assertEqual(services["signal-dev"]["state"], "Stopped")
        self.assertEqual(services["discovery-dev"]["state"], "Stopped")
        self.assertNotIn("unrelated-workload", services)

    def test_docker_stop_leaves_unrelated_container_running(self):
        rows = [row("portainer"), row("open-webui"), row("unrelated-workload")]
        with patch.object(server, "docker_desktop_state", return_value="Running"), patch.object(
            server, "_docker_container_rows", return_value=(rows, None)
        ), patch.object(
            server, "_stop_managed_container", return_value={"ok": True, "message": "stopped"}
        ), patch.object(server, "run_action") as run_action:
            result = server.stop_docker_desktop_safely()

        self.assertFalse(result["ok"])
        self.assertIn("unrelated-workload", result["message"])
        run_action.assert_not_called()

    def test_docker_stop_is_verified_when_only_managed_containers_exist(self):
        with patch.object(server, "docker_desktop_state", side_effect=["Running", "Stopped"]), patch.object(
            server, "_docker_container_rows", side_effect=[([], None), ([], None)]
        ), patch.object(
            server, "run_action", return_value={"ok": True, "detail": "stopped"}
        ) as run_action:
            result = server.stop_docker_desktop_safely()

        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "Stopped")
        self.assertEqual(run_action.call_args.args[0][1:3], ["desktop", "stop"])

    def test_docker_stop_when_already_stopped_is_a_verified_noop(self):
        with patch.object(server, "docker_desktop_state", return_value="Stopped"), patch.object(
            server, "run_action"
        ) as run_action:
            result = server.stop_docker_desktop_safely()

        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "Stopped")
        run_action.assert_not_called()


if __name__ == "__main__":
    unittest.main()
