from __future__ import annotations

import inspect
import unittest
from pathlib import Path

import server


class DockerBoundaryTests(unittest.TestCase):
    def test_runtime_control_functions_have_no_docker_lifecycle_calls(self):
        names = (
            "set_deployment_mode",
            "reset_deployment_mode_for_shutdown",
            "shutdown_all_workloads",
            "local_service_action",
            "wsl_environment_action",
            "_build_status_payload",
            "launch_openwebui",
        )
        forbidden = (
            "start_docker",
            "stop_docker",
            "docker_status",
            "docker_desktop_state",
            "_docker_container_rows",
            "run_action(_dev",
        )
        for name in names:
            source = inspect.getsource(getattr(server, name))
            for token in forbidden:
                self.assertNotIn(token, source, f"{name} still contains {token}")

    def test_status_schema_has_native_runtime_and_no_docker_snapshot(self):
        source = inspect.getsource(server._build_status_payload)
        self.assertIn('"native_runtime"', source)
        self.assertNotIn('"docker"', source)

    def test_manual_deployment_script_is_the_only_docker_workflow_boundary(self):
        script = (Path(__file__).resolve().parents[1] / "scripts" / "discovery-signal.ps1").read_text(encoding="utf-8")
        self.assertIn("Manual packaging/deployment utility", script)
        self.assertIn("never invokes this script", script)


if __name__ == "__main__":
    unittest.main()
