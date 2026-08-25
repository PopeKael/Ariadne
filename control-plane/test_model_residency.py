import time
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


class ModelResidencyTests(unittest.TestCase):
    def tearDown(self):
        with server.MODEL_ACTIVITY_LOCK:
            server.MODEL_IN_FLIGHT.clear()
            server.MODEL_LAST_USED.clear()

    def test_policy_relaxes_with_more_vram(self):
        constrained = server.model_residency_policy({"total_gb": 12})
        relaxed = server.model_residency_policy({"total_gb": 32})
        self.assertEqual(constrained["tier"], "constrained")
        self.assertEqual(relaxed["tier"], "relaxed")
        self.assertGreater(relaxed["idle_seconds"], constrained["idle_seconds"])

    def test_inflight_model_is_not_evicted(self):
        with server.MODEL_ACTIVITY_LOCK:
            server.MODEL_IN_FLIGHT["active-model"] = 1
            server.MODEL_LAST_USED["active-model"] = time.monotonic() - 3_600
        catalog = {"available": True, "loaded_details": [{"name": "active-model", "size_vram": 1_000}]}
        with patch.object(server, "ollama_catalog", return_value=catalog), \
             patch.object(server, "gpu_status", return_value={"available": True, "total_gb": 16, "free_gb": 1, "state": "critical"}), \
             patch.object(server, "unload_ollama_model") as unload:
            result = server.monitor_ollama_models()
        unload.assert_not_called()
        self.assertEqual(result["unloaded"], [])

    def test_idle_model_is_evicted_without_deleting_catalogue(self):
        with server.MODEL_ACTIVITY_LOCK:
            server.MODEL_LAST_USED["idle-model"] = time.monotonic() - 1_000
        catalog = {"available": True, "loaded_details": [{"name": "idle-model", "size_vram": 1_000}]}
        with patch.object(server, "ollama_catalog", return_value=catalog), \
             patch.object(server, "gpu_status", return_value={"available": True, "total_gb": 16, "free_gb": 8, "state": "nominal"}), \
             patch.object(server, "unload_ollama_model", return_value=True) as unload:
            result = server.monitor_ollama_models()
        unload.assert_called_once_with("idle-model")
        self.assertEqual(result["unloaded"], ["idle-model"])

    def test_unload_uses_reversible_keep_alive_zero(self):
        with patch.object(server, "post_json", return_value={} ) as post:
            self.assertTrue(server.unload_ollama_model("reloadable-model"))
        post.assert_called_once_with(
            f"{server.OLLAMA_URL}/api/generate",
            {"model": "reloadable-model", "keep_alive": 0},
            timeout=8.0,
        )

    def test_renderer_transition_blocks_new_ai_work(self):
        original_owner = (server.GPU_OWNER, server.GPU_TRANSITION_STATE, server.GPU_TRANSITION_DETAIL)
        try:
            server.GPU_OWNER = "TRANSITION"
            server.GPU_TRANSITION_STATE = "UNLOADING_OLLAMA"
            server.GPU_TRANSITION_DETAIL = "Unloading resident Ollama models safely."
            with self.assertRaises(RuntimeError) as raised:
                server.ensure_ai_gpu_access()
            self.assertIn("Unloading resident Ollama models", str(raised.exception))
        finally:
            server.GPU_OWNER, server.GPU_TRANSITION_STATE, server.GPU_TRANSITION_DETAIL = original_owner


if __name__ == "__main__":
    unittest.main()
