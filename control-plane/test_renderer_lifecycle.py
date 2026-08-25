import unittest
from pathlib import Path
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


class RendererLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.original = {
            "owner": server.GPU_OWNER,
            "transition": server.GPU_TRANSITION_STATE,
            "detail": server.GPU_TRANSITION_DETAIL,
            "operation": server.GPU_TRANSITION_OPERATION,
            "started": server.GPU_TRANSITION_STARTED_AT,
            "lifecycle": server.RENDERER_LIFECYCLE_STATE,
            "error": server.RENDERER_LIFECYCLE_ERROR,
            "stop_requested": server.RENDERER_STOP_REQUESTED,
            "deadline": server.RENDERER_START_DEADLINE_SECONDS,
            "poll": server.RENDERER_POLL_INTERVAL_SECONDS,
        }
        server.GPU_OWNER = "TRANSITION"
        server.GPU_TRANSITION_STATE = "AI_DRAINING"
        server.GPU_TRANSITION_DETAIL = "transition"
        server.GPU_TRANSITION_OPERATION = "test-operation"
        server.GPU_TRANSITION_STARTED_AT = 1.0
        server.RENDERER_LIFECYCLE_STATE = "STARTING_WSL"
        server.RENDERER_LIFECYCLE_ERROR = None
        server.RENDERER_STOP_REQUESTED = False

    def tearDown(self):
        server.GPU_OWNER = self.original["owner"]
        server.GPU_TRANSITION_STATE = self.original["transition"]
        server.GPU_TRANSITION_DETAIL = self.original["detail"]
        server.GPU_TRANSITION_OPERATION = self.original["operation"]
        server.GPU_TRANSITION_STARTED_AT = self.original["started"]
        server.RENDERER_LIFECYCLE_STATE = self.original["lifecycle"]
        server.RENDERER_LIFECYCLE_ERROR = self.original["error"]
        server.RENDERER_STOP_REQUESTED = self.original["stop_requested"]
        server.RENDERER_START_DEADLINE_SECONDS = self.original["deadline"]
        server.RENDERER_POLL_INTERVAL_SECONDS = self.original["poll"]

    def test_slow_backend_eventually_becomes_ready(self):
        responses = iter([
            {"state": "starting", "lifecycle_state": "WAITING_FOR_HEALTH", "detail": "warming"},
            {"state": "starting", "lifecycle_state": "WAITING_FOR_HEALTH", "detail": "warming"},
            {"state": "online", "lifecycle_state": "READY", "detail": "ready"},
        ])
        with patch.object(server, "ai_gpu_work_in_flight", return_value={"busy": False}), \
             patch.object(server, "release_ollama_for_renderer", return_value={}), \
             patch.object(server, "_start_wan2gp_backend", return_value={"ok": True}), \
             patch.object(server, "wan2gp_status", side_effect=lambda **_: next(responses)), \
             patch.object(server, "log_renderer_lifecycle"):
            server._renderer_start_worker("test-operation")
        self.assertEqual(server.GPU_OWNER, "RENDERER")
        self.assertEqual(server.RENDERER_LIFECYCLE_STATE, "READY")

    def test_genuine_start_deadline_becomes_error(self):
        server.RENDERER_START_DEADLINE_SECONDS = 0.02
        server.RENDERER_POLL_INTERVAL_SECONDS = 0.01
        with patch.object(server, "ai_gpu_work_in_flight", return_value={"busy": False}), \
             patch.object(server, "release_ollama_for_renderer", return_value={}), \
             patch.object(server, "_start_wan2gp_backend", return_value={"ok": True}), \
             patch.object(server, "wan2gp_status", return_value={"state": "starting", "lifecycle_state": "WAITING_FOR_HEALTH", "detail": "warming"}), \
             patch.object(server, "log_renderer_lifecycle"):
            server._renderer_start_worker("test-operation")
        self.assertEqual(server.GPU_OWNER, "NONE")
        self.assertEqual(server.RENDERER_LIFECYCLE_STATE, "ERROR")
        self.assertIn("did not become ready", server.RENDERER_LIFECYCLE_ERROR)

    def test_stop_worker_returns_gpu_to_none(self):
        with patch.object(server, "gpu_status", return_value={"available": True, "free_gb": 12}), \
             patch.object(server, "_stop_wan2gp_backend", return_value={"ok": True}), \
             patch.object(server, "wan2gp_status", return_value={"state": "offline", "lifecycle_state": "STOPPED"}), \
             patch.object(server, "log_renderer_lifecycle"):
            server._renderer_stop_worker("test-operation")
        self.assertEqual(server.GPU_OWNER, "NONE")
        self.assertEqual(server.RENDERER_LIFECYCLE_STATE, "STOPPED")

    def test_running_renderer_is_adopted_without_duplicate_process(self):
        server.GPU_OWNER = "NONE"
        server.GPU_TRANSITION_STATE = "IDLE"
        server.GPU_TRANSITION_DETAIL = "GPU is available to the next approved workload."
        payload = {"online": True, "device": "ROCm0 AMD Radeon RX 7800 XT", "vram_total": 16 * 1024**3, "vram_free": 12 * 1024**3, "clip": {"state": "idle"}}
        with patch.object(server, "json_http", return_value=payload):
            result = server.wan2gp_status()
        self.assertEqual(result["lifecycle_state"], "READY")
        self.assertEqual(server.GPU_OWNER, "RENDERER")

    def test_occupied_port_is_not_given_a_second_renderer_process(self):
        with patch.object(server, "json_http", return_value={"service": "other"}), \
             patch.object(server.subprocess, "Popen") as popen:
            result = server._start_wan2gp_backend()
        self.assertFalse(result["ok"])
        self.assertIn("occupied", result["message"])
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
