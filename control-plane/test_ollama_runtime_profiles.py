from __future__ import annotations

import sys
import json
import os
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
SYSTEM = ROOT.parent / "00_System"
sys.path.insert(0, str(SYSTEM))
sys.path.insert(0, str(ROOT))

import ollama_runtime  # noqa: E402
import server  # noqa: E402
import ariadne_embeddings  # noqa: E402
import ariadne_mcp  # noqa: E402


class OllamaRuntimeProfileRequestTests(unittest.TestCase):
    def test_model_lease_api_requires_an_explicit_context_budget(self):
        with self.assertRaisesRegex(TypeError, "context_length"):
            ollama_runtime.inference_lease("qwen3.5:9b-q4_K_M")
        with self.assertRaisesRegex(TypeError, "context_length"):
            ollama_runtime.host_acquire_model_lease("qwen3.5:9b-q4_K_M")

    def test_gpu_snapshot_is_requested_from_rust_host(self):
        snapshot = {"total_bytes": 10, "used_bytes": 4, "free_bytes": 6, "process_allocations": []}
        with patch.object(ollama_runtime, "_request_host_model", return_value={"residency": snapshot}) as request:
            result = ollama_runtime.host_gpu_snapshot("http://127.0.0.1:11434")
        self.assertEqual(result, snapshot)
        request.assert_called_once_with(
            "gpu_status", endpoint="http://127.0.0.1:11434", timeout=10.0,
        )

    def test_gpu_snapshot_rejects_missing_rust_measurement(self):
        with patch.object(ollama_runtime, "_request_host_model", return_value={"residency": None}):
            with self.assertRaisesRegex(RuntimeError, "no verified WDDM GPU snapshot"):
                ollama_runtime.host_gpu_snapshot()

    def test_workload_lease_requests_reserve_and_releases_through_rust(self):
        with patch.object(ollama_runtime, "_request_host_model", side_effect=[{"lease_id": "gpu-1"}, {}]) as request:
            with ollama_runtime.gpu_lease(
                "renderer", "http://127.0.0.1:11434", min_free_bytes=12 * 1024**3,
            ) as lease_id:
                self.assertEqual(lease_id, "gpu-1")
        self.assertEqual(request.call_args_list[0].args, ("acquire_gpu_lease",))
        self.assertEqual(request.call_args_list[0].kwargs["workload"], "renderer")
        self.assertEqual(request.call_args_list[0].kwargs["min_free_bytes"], 12 * 1024**3)
        self.assertEqual(request.call_args_list[0].kwargs["owner_pid"], os.getpid())
        self.assertEqual(request.call_args_list[1].args, ("release_gpu_lease",))
        self.assertEqual(request.call_args_list[1].kwargs["lease_id"], "gpu-1")

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
        self.assertEqual(request.call_args_list[0].kwargs["inference_kind"], "generate")
        self.assertEqual(request.call_args_list[1].args, ("release_model",))
        self.assertEqual(request.call_args_list[1].kwargs["lease_id"], "lease-2")

    def test_explicit_preload_uses_controlled_calibration_path(self):
        admission = {"detail": "calibrated by host", "residency": {"full_gpu_residency": True}}
        with patch.object(server, "inference_lease", return_value=nullcontext(admission)) as lease, \
             patch.object(server, "post_json") as post, \
             patch.object(server, "record_model_residency_event"):
            result = server.preload_ollama_model(
                "gpt-oss:20b", options={"num_ctx": 16_384},
                reason="test_explicit_calibration", allow_calibration=True,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["residency"], admission["residency"])
        post.assert_not_called()
        lease.assert_called_once_with(
            "gpt-oss:20b", server.OLLAMA_URL, context_length=16_384,
            allow_calibration=True,
        )

    def test_cold_home_preload_uses_the_configured_home_context(self):
        model = server.HOME_CHAT_MODEL
        catalogues = iter([
            {"available": True, "loaded": []},
            {"available": True, "loaded": [model]},
        ])
        with patch.object(server, "HOME_CONTEXT_TOKENS", 32_768), \
             patch.object(server, "ollama_catalog", side_effect=lambda: next(catalogues)), \
             patch.object(server, "host_gpu_lease", return_value=nullcontext("test-lease")), \
             patch.object(server, "tracked_model_activity", return_value=nullcontext()), \
             patch.object(server, "preload_ollama_model", return_value={"ok": True}) as preload, \
             patch.object(server, "record_model_residency_event"), \
             patch.object(server.STARTUP_TRACE, "mark"):
            server._preload_home_chat_model_worker(model)
        self.assertEqual(preload.call_args.kwargs["options"], {"num_ctx": 32_768})
        self.assertTrue(preload.call_args.kwargs["allow_calibration"])

    def test_model_switch_without_num_ctx_uses_registry_budget_not_ollama_default(self):
        model = server.HOME_CHAT_MODEL
        with patch.object(server, "HOME_CONTEXT_POLICY_TOKENS", 32_768), \
             patch.object(server, "_context_for_model", return_value=32_768) as effective, \
             patch.object(server, "inference_lease", return_value=nullcontext({"detail": "admitted"})) as lease, \
             patch.object(server, "record_model_residency_event"):
            result = server.preload_ollama_model(model)
        self.assertTrue(result["ok"])
        effective.assert_called_once_with(model, 32_768)
        self.assertEqual(lease.call_args.kwargs["context_length"], 32_768)

    def test_generate_stream_rejects_missing_registry_context_before_admission(self):
        with patch.object(server, "inference_lease") as lease:
            with self.assertRaisesRegex(ValueError, "registry-resolved num_ctx"):
                next(server._ollama_generate_stream("qwen", {"prompt": "test"}))
        lease.assert_not_called()

    def test_warm_home_preload_still_verifies_the_exact_host_profile(self):
        model = server.HOME_CHAT_MODEL
        with patch.object(server, "HOME_CONTEXT_TOKENS", 32_768), \
             patch.object(server, "ollama_catalog", return_value={"available": True, "loaded": [model]}), \
             patch.object(server, "host_gpu_lease", return_value=nullcontext("test-lease")), \
             patch.object(server, "tracked_model_activity", return_value=nullcontext()), \
             patch.object(server, "preload_ollama_model", return_value={"ok": True}) as preload, \
             patch.object(server, "record_model_residency_event"), \
             patch.object(server.STARTUP_TRACE, "mark"):
            server._preload_home_chat_model_worker(model)
        preload.assert_called_once_with(model, options={"num_ctx": 32_768},
                                        reason="home_session_preload", allow_calibration=True)

    def test_warm_home_chat_leases_the_context_sent_to_ollama(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"message":{"content":"warm reply"}}'
        with patch.object(ariadne_mcp, "host_inference_lease", return_value=nullcontext()) as lease, \
             patch.object(ariadne_mcp.urllib.request, "urlopen", return_value=response):
            answer = ariadne_mcp.ollama_chat(
                [{"role": "user", "content": "follow up"}],
                model="qwen3.5:9b-q4_K_M", context_tokens=32_768,
            )
        self.assertEqual(answer, "warm reply")
        lease.assert_called_once_with(
            "qwen3.5:9b-q4_K_M", ariadne_mcp.DEFAULT_OLLAMA_URL,
            context_length=32_768,
        )

    def test_embedding_host_adapter_forwards_explicit_context(self):
        with patch.object(ollama_runtime, "inference_lease", return_value=nullcontext()) as lease:
            ariadne_embeddings.host_inference_lease(
                "qwen3.5:9b-q4_K_M", "http://127.0.0.1:11434", context_length=32_768,
            )
        lease.assert_called_once_with(
            "qwen3.5:9b-q4_K_M", "http://127.0.0.1:11434", context_length=32_768,
            allow_calibration=False, inference_kind="embed",
        )

    def test_embedding_host_adapter_defaults_to_verified_runtime_context(self):
        with patch.object(ollama_runtime, "inference_lease", return_value=nullcontext()) as lease:
            ariadne_embeddings.host_inference_lease("nomic-embed-text", "http://127.0.0.1:11434")
        lease.assert_called_once_with(
            "nomic-embed-text", "http://127.0.0.1:11434", context_length=2_048,
            allow_calibration=False, inference_kind="embed",
        )

    def test_embedding_calibration_is_explicit_and_uses_embed_host_path(self):
        with patch.object(ollama_runtime, "inference_lease", return_value=nullcontext()) as lease:
            ariadne_embeddings.host_inference_lease(
                "nomic-embed-text", "http://127.0.0.1:11434", context_length=2_048,
                allow_calibration=True,
            )
        lease.assert_called_once_with(
            "nomic-embed-text", "http://127.0.0.1:11434", context_length=2_048,
            allow_calibration=True, inference_kind="embed",
        )

    def test_embedding_request_and_host_profile_use_verified_runtime_context(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"embeddings":[[0.1,0.2]]}'
        with patch.object(ariadne_embeddings, "host_inference_lease", return_value=nullcontext()) as lease, \
                patch.object(ariadne_embeddings.urllib.request, "urlopen", return_value=response) as urlopen:
            self.assertEqual(ariadne_embeddings.ollama_embed("profile check"), [0.1, 0.2])
        self.assertEqual(ariadne_embeddings.EMBED_CONTEXT_TOKENS, 2_048)
        request = urlopen.call_args.args[0]
        self.assertEqual(json.loads(request.data)["options"]["num_ctx"], 2_048)
        lease.assert_called_once_with(
            "nomic-embed-text", ariadne_embeddings.DEFAULT_OLLAMA_URL,
            context_length=2_048, allow_calibration=False,
        )

    def test_activity_tracking_does_not_take_a_second_host_lease(self):
        with patch.object(server, "inference_lease") as lease:
            with server.tracked_model_activity("qwen3.5:9b-q4_K_M"):
                pass
        lease.assert_not_called()


if __name__ == "__main__":
    unittest.main()
