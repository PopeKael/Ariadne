from __future__ import annotations

import unittest
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
        with server.MODEL_ACTIVITY_LOCK:
            server.MODEL_IN_FLIGHT.clear()

    def tearDown(self):
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

    def test_model_payload_separates_installed_and_resident_models(self):
        with patch.object(server, "ollama_catalog", return_value=self.catalog("alpha", "beta", loaded=("alpha",))):
            payload = server.model_control_payload()
        self.assertEqual([item["name"] for item in payload["models"]], ["alpha", "beta"])
        self.assertEqual(payload["loaded"], ["alpha"])
        self.assertEqual(payload["active_model"], server.HOME_CHAT_MODEL)

    def test_switch_rejects_model_not_in_installed_catalogue(self):
        with patch.object(server, "ollama_catalog", return_value=self.catalog("alpha")), \
             patch.object(server, "save_configuration") as save:
            payload, status = server.switch_active_model("missing")
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        save.assert_not_called()

    def test_switch_rejects_embedding_only_model(self):
        with patch.object(server, "ollama_catalog", return_value=self.catalog("embed-model")), \
             patch.object(server, "ollama_model_capabilities", return_value=("embedding",)), \
             patch.object(server, "save_configuration") as save:
            payload, status = server.switch_active_model("embed-model")
        self.assertEqual(status, 400)
        self.assertIn("not a conversation model", payload["message"])
        save.assert_not_called()

    def test_switch_is_blocked_while_renderer_owns_gpu(self):
        server.GPU_OWNER = "RENDERER"
        with patch.object(server, "ollama_catalog", return_value=self.catalog("alpha")):
            payload, status = server.switch_active_model("alpha")
        self.assertEqual(status, 409)
        self.assertIn("video renderer", payload["message"])

    def test_successful_switch_preloads_then_persists_and_activates(self):
        server.HOME_CHAT_MODEL = "old-model"
        server.PLANNER_MODEL = "old-model"

        def activate():
            server.HOME_CHAT_MODEL = "new-model"
            server.PLANNER_MODEL = "new-model"
            return {}

        with patch.object(server, "ollama_catalog", return_value=self.catalog("old-model", "new-model", loaded=("old-model",))), \
             patch.object(server, "ollama_model_capabilities", return_value=("completion",)), \
             patch.object(server, "unload_ollama_model", return_value=True) as unload, \
             patch.object(server, "preload_ollama_model", return_value={"ok": True, "model": "new-model", "detail": "loaded"}) as preload, \
             patch.object(server, "save_configuration", return_value={"updated_at": "now"}) as save, \
             patch.object(server, "apply_runtime_configuration", side_effect=activate), \
             patch.object(server, "configuration_snapshot", return_value={"revision": "revision"}):
            payload, status = server.switch_active_model("new-model")

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        unload.assert_called_once_with("old-model")
        preload.assert_called_once_with("new-model")
        inference = save.call_args.kwargs["inference"]
        self.assertEqual(inference["routes"]["home_chat"], "ollama-desktop")
        self.assertEqual(inference["routes"]["planner"], "ollama-planner")
        self.assertEqual(server.GPU_TRANSITION_STATE, "IDLE")
        self.assertEqual(server.GPU_OWNER, "AI")

    def test_ai_admission_and_transition_are_atomic(self):
        with server.ai_gpu_admission():
            self.assertEqual(server.GPU_AI_ADMISSIONS, 1)
            server.GPU_TRANSITION_STATE = "SWITCHING_MODEL"
            with self.assertRaises(RuntimeError):
                with server.ai_gpu_admission():
                    pass
            server.GPU_TRANSITION_STATE = "IDLE"
        self.assertEqual(server.GPU_AI_ADMISSIONS, 0)

    def test_failed_activation_restores_previous_inference_routes(self):
        server.HOME_CHAT_MODEL = "old-model"
        server.PLANNER_MODEL = "old-model"
        previous_providers = [provider.as_dict() for provider in server.INFERENCE_REGISTRY.providers]
        previous_routes = dict(server.INFERENCE_REGISTRY.routes)
        with patch.object(server, "ollama_catalog", return_value=self.catalog("old-model", "new-model")), \
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
