from __future__ import annotations

import sys
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import ollama_runtime  # noqa: E402
import server  # noqa: E402


class OllamaRuntimeProfileRequestTests(unittest.TestCase):
    def test_vault_acquisition_marks_explicit_16k_calibration(self):
        with patch.object(ollama_runtime, "_request_host_model", return_value={"lease_id": "lease-1"}) as request:
            lease_id = ollama_runtime.host_acquire_model_lease(
                "gpt-oss:20b", "http://127.0.0.1:11434",
                context_length=16_384, allow_calibration=True,
            )
        self.assertEqual(lease_id, "lease-1")
        request.assert_called_once_with(
            "admit_model", endpoint="http://127.0.0.1:11434", model="gpt-oss:20b",
            context_length=16_384, allow_calibration=True, timeout=330.0,
        )

    def test_inference_lease_forwards_profile_context_and_releases_lease(self):
        with patch.object(ollama_runtime, "_request_host_model", side_effect=[{"lease_id": "lease-2"}, {}]) as request:
            with ollama_runtime.inference_lease("gpt-oss:20b", context_length=16_384):
                pass
        self.assertEqual(request.call_args_list[0].kwargs["context_length"], 16_384)
        self.assertFalse(request.call_args_list[0].kwargs["allow_calibration"])
        self.assertEqual(request.call_args_list[1].args, ("release_model",))
        self.assertEqual(request.call_args_list[1].kwargs["lease_id"], "lease-2")

    def test_explicit_preload_uses_controlled_calibration_path(self):
        with patch.object(server, "inference_lease", return_value=nullcontext()) as lease, \
             patch.object(server, "post_json", return_value={}), \
             patch.object(server, "record_model_residency_event"):
            result = server.preload_ollama_model(
                "gpt-oss:20b", options={"num_ctx": 16_384}, keep_alive=-1,
                reason="test_explicit_calibration", allow_calibration=True,
            )
        self.assertTrue(result["ok"])
        lease.assert_called_once_with(
            "gpt-oss:20b", server.OLLAMA_URL, context_length=16_384,
            allow_calibration=True,
        )

    def test_activity_tracking_does_not_take_a_second_host_lease(self):
        with patch.object(server, "ensure_ai_gpu_access"), \
             patch.object(server, "inference_lease") as lease:
            with server.tracked_model_activity("qwen3.5:9b-q4_K_M"):
                pass
        lease.assert_not_called()


if __name__ == "__main__":
    unittest.main()
