import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class FakeProcess:
    pid = 4173

    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode


class GodsEyeViewServiceTests(unittest.TestCase):
    def tearDown(self):
        server.GODS_EYE_VIEW_PROCESS = None

    def test_status_reports_stopped_when_port_is_not_serving(self):
        with patch.object(server, "_gods_eye_view_probe", return_value=False):
            status = server.gods_eye_view_status()

        self.assertEqual(status["state"], "Stopped")
        self.assertEqual(status["action"], "start")

    def test_start_runs_the_installed_vite_command_and_waits_for_http_readiness(self):
        process = FakeProcess()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text("{}", encoding="utf-8")
            vite_entry = root / "node_modules" / "vite" / "bin" / "vite.js"
            vite_entry.parent.mkdir(parents=True)
            vite_entry.write_text("// test entrypoint\n", encoding="utf-8")
            with patch.object(server, "GODS_EYE_VIEW_ROOT", root), patch.object(
                server, "_node_command", return_value="node.exe"
            ), patch.object(
                server, "_gods_eye_view_probe", side_effect=[False, True]
            ), patch.object(server.subprocess, "Popen", return_value=process) as popen:
                result = server._start_gods_eye_view()

        self.assertTrue(result["ok"])
        self.assertIn("localhost:4173", result["message"])
        self.assertEqual(
            popen.call_args.args[0],
            ["node.exe", str(root / "node_modules" / "vite" / "bin" / "vite.js"), "--host", "localhost", "--port", "4173", "--configLoader", "runner"],
        )
        self.assertEqual(popen.call_args.kwargs["cwd"], root)

    def test_stop_targets_only_the_owned_process_tree(self):
        process = FakeProcess()
        server.GODS_EYE_VIEW_PROCESS = process
        with patch.object(server, "run_action", return_value={"ok": True, "detail": ""}) as action, patch.object(
            server, "_gods_eye_view_probe", return_value=False
        ):
            result = server._stop_gods_eye_view()

        self.assertTrue(result["ok"])
        action.assert_called_once_with(["taskkill.exe", "/PID", "4173", "/T", "/F"], timeout=15.0)


if __name__ == "__main__":
    unittest.main()
