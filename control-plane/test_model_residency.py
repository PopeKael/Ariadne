import time
import unittest
from contextlib import nullcontext
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

    def test_host_admission_performs_preload_without_python_ollama_load(self):
        with patch.object(server, "inference_lease", return_value=nullcontext({"detail": "host loaded", "residency": {}})) as lease, \
             patch.object(server, "post_json") as post:
            result = server.preload_ollama_model("test-model")
        self.assertTrue(result["ok"])
        lease.assert_called_once_with("test-model", server.OLLAMA_URL, context_length=4096, allow_calibration=False)
        post.assert_not_called()

    def test_inflight_model_is_not_evicted(self):
        original_sessions = dict(server.SESSIONS)
        with server.SESSION_LOCK:
            server.SESSIONS["test-home"] = {"surface": "home", "last_seen": time.monotonic()}
        try:
            with server.MODEL_ACTIVITY_LOCK:
                server.MODEL_IN_FLIGHT["active-model"] = 1
                server.MODEL_LAST_USED["active-model"] = time.monotonic() - 3_600
            catalog = {"available": True, "loaded_details": [{"name": "active-model", "size_vram": 1_000}]}
            with patch.object(server, "ollama_catalog", return_value=catalog), \
                 patch.object(server, "unload_ollama_model") as unload:
                result = server.monitor_ollama_models()
            unload.assert_not_called()
            self.assertEqual(result["unloaded"], [])
        finally:
            with server.SESSION_LOCK:
                server.SESSIONS.clear()
                server.SESSIONS.update(original_sessions)

    def test_idle_model_is_evicted_without_deleting_catalogue(self):
        with server.MODEL_ACTIVITY_LOCK:
            server.MODEL_LAST_USED["idle-model"] = time.monotonic() - 1_000
        catalog = {"available": True, "loaded": [server.HOME_CHAT_MODEL, "idle-model"],
                   "loaded_details": [{"name": server.HOME_CHAT_MODEL}, {"name": "idle-model", "size_vram": 1_000}]}
        with patch.object(server, "ollama_catalog", return_value=catalog), \
             patch.object(server, "gpu_status", return_value={"available": True, "total_gb": 16, "free_gb": 8, "state": "nominal"}), \
             patch.object(server, "ollama_update_in_progress", return_value=False), \
             patch.object(server, "observe_ollama_runtime_identity", return_value=(False, {})), \
             patch.object(server, "ollama_chat_endpoint_status", return_value=(True, "ready")), \
             patch.object(server, "release_idle_ollama_models", return_value={"available": True, "unloaded": ["idle-model"]}) as release:
            result = server.monitor_ollama_models()
        release.assert_called_once()
        self.assertEqual(result["unloaded"], ["idle-model"])

    def test_idle_release_delegates_selection_and_unload_to_rust_host(self):
        with patch.object(server, "host_release_idle_models", return_value={"models": ["idle-model"], "detail": "released"}) as release:
            result = server.release_idle_ollama_models(force=True)
        release.assert_called_once_with(endpoint=server.OLLAMA_URL, force=True)
        self.assertEqual(result["unloaded"], ["idle-model"])

    def test_home_readiness_recovery_preloads_home_model_not_vault_default(self):
        old = (
            server.HOME_CHAT_MODEL, server.OLLAMA_CHAT_MODEL,
            server.HOME_CONTEXT_TOKENS, server.OLLAMA_RECOVERY_LAST_ATTEMPT,
            server.OLLAMA_RUNTIME_IDENTITY, server.HOME_MODEL_PRELOAD_STATUS,
        )
        server.HOME_CHAT_MODEL = "home-model"
        server.OLLAMA_CHAT_MODEL = "vault-model"
        server.HOME_CONTEXT_TOKENS = 32768
        server.OLLAMA_RECOVERY_LAST_ATTEMPT = None
        server.OLLAMA_RUNTIME_IDENTITY = {"version": "0.40.6"}
        server.HOME_MODEL_PRELOAD_STATUS = {"state": "unknown", "model": "home-model", "detail": ""}
        original_sessions = dict(server.SESSIONS)
        with server.SESSION_LOCK:
            server.SESSIONS["test-home"] = {"surface": "home", "last_seen": time.monotonic()}
        empty_residency = {"available": True, "models": [{"name": "home-model"}], "loaded": [], "loaded_details": []}
        home_resident = {"available": True, "models": [{"name": "home-model"}], "loaded": ["home-model"],
                         "loaded_details": [{"name": "home-model", "size_vram": 1000}]}
        try:
            with patch.object(server, "ollama_update_in_progress", return_value=False), \
                 patch.object(server, "observe_ollama_runtime_identity", return_value=(False, {"version": "0.40.6"})), \
                 patch.object(server, "ollama_catalog", side_effect=[empty_residency, home_resident]), \
                 patch.object(server, "ollama_chat_endpoint_status", return_value=(True, "ready")), \
                 patch.object(server, "ollama_status", return_value={"available": True, "state": "online"}), \
                 patch.object(server, "preload_ollama_model", return_value={"ok": True, "detail": "home admitted"}) as preload, \
                 patch.object(server, "ai_gpu_admission"), \
                 patch.object(server, "release_idle_ollama_models", return_value={"available": True, "unloaded": []}), \
                 patch.object(server, "runtime_identity", return_value={"version": "0.40.6"}), \
                 patch.object(server.STARTUP_TRACE, "mark"), \
                 patch.object(server, "record_model_residency_event"):
                result = server.monitor_ollama_models()
            preload.assert_called_once_with(
                "home-model", options={"num_ctx": 32768},
                reason="runtime_readiness_check", allow_calibration=True,
            )
            self.assertEqual(result["state"], "nominal")
        finally:
            (server.HOME_CHAT_MODEL, server.OLLAMA_CHAT_MODEL,
             server.HOME_CONTEXT_TOKENS, server.OLLAMA_RECOVERY_LAST_ATTEMPT,
             server.OLLAMA_RUNTIME_IDENTITY, server.HOME_MODEL_PRELOAD_STATUS) = old
            with server.SESSION_LOCK:
                server.SESSIONS.clear()
                server.SESSIONS.update(original_sessions)

    def test_missing_home_model_stays_unloaded_without_active_home_session(self):
        original_status = server.HOME_MODEL_PRELOAD_STATUS
        original_attempt = server.OLLAMA_RECOVERY_LAST_ATTEMPT
        original_sessions = dict(server.SESSIONS)
        with server.SESSION_LOCK:
            server.SESSIONS.clear()
        server.OLLAMA_RECOVERY_LAST_ATTEMPT = None
        empty = {"available": True, "models": [{"name": server.HOME_CHAT_MODEL}],
                 "loaded": [], "loaded_details": []}
        try:
            with patch.object(server, "ollama_update_in_progress", return_value=False), \
                 patch.object(server, "observe_ollama_runtime_identity", return_value=(False, {})), \
                 patch.object(server, "ollama_catalog", return_value=empty), \
                 patch.object(server, "ollama_chat_endpoint_status", return_value=(True, "ready")), \
                 patch.object(server, "ollama_status", return_value={"available": True, "state": "online"}), \
                 patch.object(server, "preload_ollama_model") as preload, \
                 patch.object(server, "release_idle_ollama_models") as release, \
                 patch.object(server, "runtime_identity", return_value={"version": "test"}), \
                 patch.object(server, "STARTUP_TRACE") as trace, \
                 patch.object(server, "record_model_residency_event"):
                result = server.monitor_ollama_models()
            preload.assert_not_called()
            release.assert_not_called()
            self.assertEqual(result["state"], "inactive")
            self.assertEqual(server.HOME_MODEL_PRELOAD_STATUS["state"], "inactive")
        finally:
            server.HOME_MODEL_PRELOAD_STATUS = original_status
            server.OLLAMA_RECOVERY_LAST_ATTEMPT = original_attempt
            with server.SESSION_LOCK:
                server.SESSIONS.clear()
                server.SESSIONS.update(original_sessions)

    def test_home_model_eviction_policy_is_owned_by_rust_host(self):
        original_model = server.HOME_CHAT_MODEL
        server.HOME_CHAT_MODEL = "home-model"
        try:
            with server.MODEL_ACTIVITY_LOCK:
                server.MODEL_LAST_USED["home-model"] = time.monotonic() - 3_600
            catalog = {"available": True, "loaded": ["home-model"], "loaded_details": [{"name": "home-model", "size_vram": 1_000}]}
            with patch.object(server, "ollama_catalog", return_value=catalog), \
                 patch.object(server, "ollama_update_in_progress", return_value=False), \
                 patch.object(server, "observe_ollama_runtime_identity", return_value=(False, {})), \
                 patch.object(server, "ollama_chat_endpoint_status", return_value=(True, "ready")), \
                 patch.object(server, "release_idle_ollama_models", return_value={"available": True, "unloaded": []}) as release:
                result = server.monitor_ollama_models()
            release.assert_called_once_with()
            self.assertEqual(result["unloaded"], [])
        finally:
            server.HOME_CHAT_MODEL = original_model

    def test_idle_session_cleanup_preserves_home_model(self):
        original_done = server.IDLE_SHUTDOWN_DONE
        with server.SESSION_LOCK:
            original_sessions = dict(server.SESSIONS)
            server.SESSIONS.clear()
        server.IDLE_SHUTDOWN_DONE = False
        try:
            with patch.object(server, "release_idle_ollama_models") as release, \
                 patch.object(server, "release_workloads"):
                server.shutdown_idle_workloads()
            release.assert_called_once_with(force=True)
        finally:
            server.IDLE_SHUTDOWN_DONE = original_done
            with server.SESSION_LOCK:
                server.SESSIONS.clear()
                server.SESSIONS.update(original_sessions)

    def test_explicit_unload_path_still_releases_home_model(self):
        with patch.object(server, "release_idle_ollama_models") as release:
            server._unload_ollama_models()
        release.assert_called_once_with(force=True)

    def test_gpu_status_uses_rust_host_snapshot(self):
        snapshot = {
            "adapters": ["Radeon RX 7800 XT"],
            "total_bytes": 16 * 1024**3,
            "used_bytes": 5 * 1024**3,
            "free_bytes": 11 * 1024**3,
            "process_allocations": [{"pid": 123, "bytes": 5 * 1024**3}],
        }
        with patch.object(server, "host_gpu_snapshot", return_value=snapshot) as read:
            result = server.gpu_status()
        read.assert_called_once_with(server.OLLAMA_URL)
        self.assertEqual(result["used_bytes"], snapshot["used_bytes"])
        self.assertEqual(result["free_bytes"], snapshot["free_bytes"])
        self.assertEqual(result["process_allocations"], snapshot["process_allocations"])

    def test_unload_delegates_ownership_and_verification_to_rust_host(self):
        with patch.object(server, "host_unload_model", return_value={"ok": True, "state": "unloaded"}) as unload, \
             patch.object(server, "record_model_residency_event"):
            self.assertTrue(server.unload_ollama_model("reloadable-model"))
        unload.assert_called_once_with("reloadable-model", server.OLLAMA_URL)

    def test_ai_admission_uses_rust_host_workload_lease(self):
        original = (server.GPU_OWNER, server.GPU_AI_ADMISSIONS)
        try:
            server.GPU_OWNER = "NONE"
            server.GPU_AI_ADMISSIONS = 0
            with patch.object(server, "host_gpu_lease", return_value=nullcontext("lease")) as lease:
                with server.ai_gpu_admission():
                    self.assertEqual(server.GPU_AI_ADMISSIONS, 1)
            lease.assert_called_once_with("ai", server.OLLAMA_URL)
            self.assertEqual(server.GPU_AI_ADMISSIONS, 0)
        finally:
            server.GPU_OWNER, server.GPU_AI_ADMISSIONS = original

    def test_rust_host_denial_stops_ai_work_before_the_body_runs(self):
        entered = False
        with patch.object(server, "host_gpu_lease", side_effect=RuntimeError("renderer lease is active")):
            with self.assertRaisesRegex(RuntimeError, "renderer lease is active"):
                with server.ai_gpu_admission():
                    entered = True
        self.assertFalse(entered)


if __name__ == "__main__":
    unittest.main()
