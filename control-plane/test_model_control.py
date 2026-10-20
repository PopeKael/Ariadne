from __future__ import annotations

import unittest
from contextlib import nullcontext
from unittest.mock import patch

import server


class ModelControlTests(unittest.TestCase):
    def setUp(self):
        self.runtime = (
            server.HOME_CHAT_MODEL,
            server.PLANNER_MODEL,
            server.GPU_OWNER,
            server.GPU_TRANSITION_STATE,
            server.GPU_TRANSITION_DETAIL,
            server.GPU_TRANSITION_OPERATION,
            server.GPU_TRANSITION_STARTED_AT,
            server.GPU_AI_ADMISSIONS,
        )
        server.GPU_OWNER = "AI"
        server.GPU_TRANSITION_STATE = "IDLE"
        server.GPU_TRANSITION_DETAIL = "Ready"
        server.GPU_TRANSITION_OPERATION = None
        server.GPU_TRANSITION_STARTED_AT = None
        server.GPU_AI_ADMISSIONS = 0
        self.gpu_lease_patch = patch.object(server, "host_gpu_lease", return_value=nullcontext("test-gpu-lease"))
        self.gpu_lease_patch.start()
        self.host_transition_patch = patch.object(server, "host_acquire_gpu_lease", return_value="test-transition-lease")
        self.host_transition_patch.start()
        self.host_release_patch = patch.object(server, "host_release_gpu_lease")
        self.host_release_patch.start()
        with server.MODEL_ACTIVITY_LOCK:
            server.MODEL_IN_FLIGHT.clear()

    def tearDown(self):
        self.gpu_lease_patch.stop()
        self.host_transition_patch.stop()
        self.host_release_patch.stop()
        (
            server.HOME_CHAT_MODEL,
            server.PLANNER_MODEL,
            server.GPU_OWNER,
            server.GPU_TRANSITION_STATE,
            server.GPU_TRANSITION_DETAIL,
            server.GPU_TRANSITION_OPERATION,
            server.GPU_TRANSITION_STARTED_AT,
            server.GPU_AI_ADMISSIONS,
        ) = self.runtime
        with server.MODEL_ACTIVITY_LOCK:
            server.MODEL_IN_FLIGHT.clear()

    @staticmethod
    def catalog(*names: str, loaded: tuple[str, ...] = ()) -> dict[str, object]:
        return {
            "available": True,
            "models": [{"name": name, "size": 1_000} for name in names],
            "loaded": list(loaded),
            "loaded_details": [{"name": name, "size_vram": 1_000} for name in loaded],
        }

    @staticmethod
    def index(*names: str) -> list[dict[str, object]]:
        return [{"id": name, "name": name, "canonical_name": name, "runtime_model": name,
                 "installed_names": [name], "aliases": [], "role": "Chat", "roles": ["Chat"],
                 "capabilities": ["chat", "structured_output"], "can_chat": True,
                 "context_max": 262144, "effective_context_tokens": 32768, "size": 1000}
                for name in names]

    def test_model_payload_separates_installed_and_resident_models(self):
        index = self.index("alpha", "beta")
        with patch.object(server, "ollama_catalog", return_value=self.catalog("alpha", "beta", loaded=("alpha",))), \
             patch.object(server, "ollama_status", return_value={"available": True}), \
             patch.object(server, "_ollama_model_index", return_value=index):
            payload = server.model_control_payload()
        self.assertEqual([item["canonical_name"] for item in payload["models"]], ["alpha", "beta"])
        self.assertEqual(payload["loaded"], ["alpha"])
        self.assertEqual(payload["active_model"], server.HOME_CHAT_MODEL)

    def test_stale_home_residency_cache_cannot_report_ready_when_host_ps_says_absent(self):
        index = self.index("home-model")
        with patch.object(server, "HOME_CHAT_MODEL", "home-model"), \
             patch.object(server, "HOME_MODEL_PRELOAD_STATUS", {
                 "state": "resident", "model": "home-model", "detail": "Previously resident."
             }), \
             patch.object(server, "ollama_catalog", return_value=self.catalog("home-model")), \
             patch.object(server, "ollama_status", return_value={"available": False,
                                                                  "detail": "Home model is not resident."}), \
             patch.object(server, "_ollama_model_index", return_value=index):
            payload = server.model_control_payload()

        self.assertEqual(payload["home_model_residency"]["state"], "unavailable")
        self.assertIn("not resident in Ollama /api/ps", payload["home_model_residency"]["detail"])

    def test_normal_chat_model_selection_excludes_embedding_only_models(self):
        original_model = server.HOME_CHAT_MODEL
        server.HOME_CHAT_MODEL = "chat-model"
        index = [
            {"id": "chat-model", "name": "chat-model", "canonical_name": "chat-model", "runtime_model": "chat-model",
             "installed_names": ["chat-model"], "aliases": [], "role": "Chat", "roles": ["Chat"],
             "can_chat": True, "context_max": 262144, "effective_context_tokens": 32768, "size": 1000},
            {"id": "embed-model", "name": "embed-model", "canonical_name": "embed-model", "runtime_model": "embed-model",
             "installed_names": ["embed-model"], "aliases": [], "role": "Embedding", "roles": ["Embedding"],
             "can_chat": False, "context_max": 2048, "effective_context_tokens": 2048, "size": 1000},
        ]
        try:
            with patch.object(server, "ollama_catalog", return_value=self.catalog("chat-model", "embed-model")), \
                 patch.object(server, "ollama_status", return_value={"available": True}), \
                 patch.object(server, "_ollama_model_index", return_value=index):
                payload = server.model_control_payload()
        finally:
            server.HOME_CHAT_MODEL = original_model
        self.assertEqual([item["canonical_name"] for item in payload["models"]], ["chat-model"])
        self.assertEqual(len(payload["catalogue"]), 2)

    def test_runtime_context_uses_32k_or_declared_model_limit(self):
        with patch.object(server, "ollama_model_capability_details", return_value={"available": True, "context_max": 262144}):
            self.assertEqual(server._context_for_model("qwen", 32768), 32768)
        with patch.object(server, "ollama_model_capability_details", return_value={"available": True, "context_max": 16384}):
            self.assertEqual(server._context_for_model("phi", 32768), 16384)

    def test_switch_rejects_model_not_in_installed_catalogue(self):
        with patch.object(server, "ollama_catalog", return_value=self.catalog("alpha")), \
             patch.object(server, "_ollama_model_index", return_value=self.index("alpha")), \
             patch.object(server, "save_configuration") as save:
            payload, status = server.switch_active_model("missing")
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        save.assert_not_called()

    def test_switch_rejects_embedding_only_model(self):
        with patch.object(server, "ollama_catalog", return_value=self.catalog("embed-model")), \
             patch.object(server, "_ollama_model_index", return_value=[{**self.index("embed-model")[0], "can_chat": False, "role": "Embedding"}]), \
             patch.object(server, "ollama_model_capabilities", return_value=("embedding",)), \
             patch.object(server, "save_configuration") as save:
            payload, status = server.switch_active_model("embed-model")
        self.assertEqual(status, 400)
        self.assertIn("not a conversation model", payload["message"])
        save.assert_not_called()

    def test_switch_is_blocked_while_renderer_owns_gpu(self):
        server.GPU_OWNER = "RENDERER"
        with patch.object(server, "host_acquire_gpu_lease", side_effect=RuntimeError("Rust host has an exclusive GPU workload lease; transition admission was denied.")), \
             patch.object(server, "ollama_catalog", return_value=self.catalog("alpha")), \
             patch.object(server, "_ollama_model_index", return_value=self.index("alpha")):
            payload, status = server.switch_active_model("alpha")
        self.assertEqual(status, 409)
        self.assertIn("exclusive GPU workload lease", payload["message"])

    def test_successful_switch_preloads_then_persists_and_activates(self):
        server.HOME_CHAT_MODEL = "old-model"
        server.PLANNER_MODEL = "old-model"

        def activate():
            server.HOME_CHAT_MODEL = "new-model"
            server.PLANNER_MODEL = "new-model"
            return {}

        with patch.object(server, "ollama_catalog", return_value=self.catalog("old-model", "new-model", loaded=("old-model",))), \
             patch.object(server, "_ollama_model_index", return_value=self.index("old-model", "new-model")), \
             patch.object(server, "ollama_model_capabilities", return_value=("completion",)), \
             patch.object(server, "unload_ollama_model", return_value=True) as unload, \
             patch.object(server, "preload_ollama_model", return_value={"ok": True, "model": "new-model", "detail": "loaded"}) as preload, \
             patch.object(server, "save_configuration", return_value={"updated_at": "now"}) as save, \
             patch.object(server, "apply_runtime_configuration", side_effect=activate), \
             patch.object(server, "configuration_snapshot", return_value={"revision": "revision"}):
            payload, status = server.switch_active_model("new-model")

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        unload.assert_not_called()
        preload.assert_called_once_with("new-model", reason="selected_model_switch", allow_calibration=True)
        inference = save.call_args.kwargs["inference"]
        self.assertEqual(inference["routes"]["home_chat"], "ollama-desktop")
        self.assertEqual(inference["routes"]["planner"], "ollama-planner")
        self.assertEqual(server.GPU_TRANSITION_STATE, "IDLE")
        self.assertEqual(server.GPU_OWNER, "AI")

    def test_ai_admission_and_transition_are_atomic(self):
        with patch.object(server, "host_gpu_lease", side_effect=[nullcontext(), RuntimeError("Host lease conflicts with transition")]):
            with server.ai_gpu_admission():
                self.assertEqual(server.GPU_AI_ADMISSIONS, 1)
                with self.assertRaisesRegex(RuntimeError, "Host lease conflicts"):
                    with server.ai_gpu_admission():
                        pass
        self.assertEqual(server.GPU_AI_ADMISSIONS, 0)

    def test_failed_activation_restores_previous_inference_routes(self):
        server.HOME_CHAT_MODEL = "old-model"
        server.PLANNER_MODEL = "old-model"
        previous_providers = [provider.as_dict() for provider in server.INFERENCE_REGISTRY.providers]
        previous_routes = dict(server.INFERENCE_REGISTRY.routes)
        with patch.object(server, "ollama_catalog", return_value=self.catalog("old-model", "new-model")), \
             patch.object(server, "_ollama_model_index", return_value=self.index("old-model", "new-model")), \
             patch.object(server, "ollama_model_capabilities", return_value=("completion",)), \
             patch.object(server, "preload_ollama_model", return_value={"ok": True, "model": "new-model"}), \
             patch.object(server, "save_configuration", return_value={"updated_at": "now"}) as save, \
             patch.object(server, "apply_runtime_configuration", side_effect=[RuntimeError("reload failed"), {}]):
            payload, status = server.switch_active_model("new-model")
        self.assertEqual(status, 500)
        self.assertFalse(payload["ok"])
        self.assertIn("previous inference routes were restored", payload["message"])
        self.assertEqual(save.call_count, 2)
        rollback = save.call_args_list[1].kwargs["inference"]
        self.assertEqual(rollback["providers"], previous_providers)
        self.assertEqual(rollback["routes"], previous_routes)


if __name__ == "__main__":
    unittest.main()
