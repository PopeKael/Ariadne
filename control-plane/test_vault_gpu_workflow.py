from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


class VaultGpuWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.original = {
            "owner": server.GPU_OWNER,
            "admissions": server.GPU_AI_ADMISSIONS,
            "reservation": server.GPU_VAULT_RESERVATION,
            "transition": server.GPU_TRANSITION_STATE,
            "transition_detail": server.GPU_TRANSITION_DETAIL,
            "home_model": server.HOME_CHAT_MODEL,
            "ingest_model": server.OLLAMA_CHAT_MODEL,
            "home_preload_thread": server.HOME_MODEL_PRELOAD_THREAD,
            "jobs": server.JOBS,
            "sessions": server.SESSIONS,
        }
        server.GPU_OWNER = "NONE"
        server.GPU_AI_ADMISSIONS = 0
        server.GPU_VAULT_RESERVATION = None
        server.GPU_TRANSITION_STATE = "IDLE"
        server.GPU_TRANSITION_DETAIL = "GPU is available."
        server.HOME_CHAT_MODEL = "home-model"
        server.OLLAMA_CHAT_MODEL = "ingestion-model"
        server.JOBS = {}
        server.SESSIONS = {}
        with server.MODEL_ACTIVITY_LOCK:
            server.MODEL_IN_FLIGHT.clear()
            server.MODEL_LAST_USED.clear()

    def tearDown(self):
        with server.GPU_ARBITRATION_LOCK:
            server.GPU_OWNER = self.original["owner"]
            server.GPU_AI_ADMISSIONS = self.original["admissions"]
            server.GPU_VAULT_RESERVATION = self.original["reservation"]
            server.GPU_TRANSITION_STATE = self.original["transition"]
            server.GPU_TRANSITION_DETAIL = self.original["transition_detail"]
        server.HOME_CHAT_MODEL = self.original["home_model"]
        server.OLLAMA_CHAT_MODEL = self.original["ingest_model"]
        server.HOME_MODEL_PRELOAD_THREAD = self.original["home_preload_thread"]
        server.JOBS = self.original["jobs"]
        server.SESSIONS = self.original["sessions"]
        with server.MODEL_ACTIVITY_LOCK:
            server.MODEL_IN_FLIGHT.clear()
            server.MODEL_LAST_USED.clear()

    def test_page_entry_checks_idle_gpu_and_loads_protected_ingestion_model(self):
        server.GPU_OWNER = "AI"
        catalog = {"available": True, "loaded": [], "loaded_details": []}
        with patch.object(server, "ollama_catalog", side_effect=[catalog, catalog, {"available": True, "loaded": ["ingestion-model"], "loaded_details": [{"name": "ingestion-model"}]}]), \
             patch.object(server, "release_idle_ollama_models", return_value={"available": True, "unloaded": ["old-model"], "protected": []}), \
             patch.object(server, "preload_ollama_model", return_value={"ok": True, "model": "ingestion-model"}) as preload:
            result = server.vault_gpu_entry_preflight("session-1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["models_released"], ["old-model"])
        preload.assert_called_once_with("ingestion-model", keep_alive=-1, reason="vault_ingestion_page_entry")
        self.assertIn("resident", result["detail"])
        self.assertEqual(server.MODEL_IN_FLIGHT.get("ingestion-model"), 1)
        self.assertEqual(server.GPU_OWNER, "NONE")

    def test_page_entry_refuses_active_gpu_admission(self):
        server.GPU_AI_ADMISSIONS = 1
        result = server.vault_gpu_entry_preflight()
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "busy")

    def test_page_entry_waits_for_in_flight_home_preload_before_gpu_handoff(self):
        class PreloadThread:
            alive = True
            joined_with = None

            def is_alive(self):
                return self.alive

            def join(self, timeout):
                self.joined_with = timeout
                self.alive = False

        thread = PreloadThread()
        server.HOME_MODEL_PRELOAD_THREAD = thread
        state = server._wait_for_home_model_preload(timeout_seconds=300)
        self.assertTrue(state["ok"])
        self.assertEqual(state["state"], "settled")
        self.assertEqual(thread.joined_with, 300)

    def test_batch_loads_once_and_governor_defers_while_reserved(self):
        catalog_before = {"available": True, "loaded": [], "loaded_details": []}
        catalog_after = {"available": True, "loaded": ["ingestion-model"], "loaded_details": [{"name": "ingestion-model"}]}
        with patch.object(server, "ollama_catalog", side_effect=[catalog_before, catalog_before, catalog_after]), \
             patch.object(server, "release_idle_ollama_models", return_value={"available": True, "unloaded": ["home-model"]}), \
             patch.object(server, "preload_ollama_model", return_value={"ok": True, "model": "ingestion-model"}) as preload:
            state = server._acquire_vault_ingestion_gpu("job-1")
        self.assertEqual(state["state"], "resident")
        preload.assert_called_once_with("ingestion-model", keep_alive=-1, reason="vault_ingestion_preflight")
        self.assertEqual(server.GPU_OWNER, "VAULT")
        self.assertEqual(server.GPU_VAULT_RESERVATION, "job-1")
        self.assertEqual(server.MODEL_IN_FLIGHT.get("ingestion-model"), 1)
        with patch.object(server, "ollama_catalog") as catalog, patch.object(server, "gpu_status") as gpu:
            monitor = server.monitor_ollama_models()
        self.assertEqual(monitor["state"], "deferred")
        catalog.assert_not_called()
        gpu.assert_not_called()

    def test_batch_completion_releases_reservation_but_leaves_model_to_idle_governor(self):
        with patch.object(server, "ollama_catalog", side_effect=[
            {"available": True, "loaded": [], "loaded_details": []},
            {"available": True, "loaded": [], "loaded_details": []},
            {"available": True, "loaded": ["ingestion-model"], "loaded_details": [{"name": "ingestion-model"}]},
            {"available": True, "loaded": ["ingestion-model"], "loaded_details": [{"name": "ingestion-model"}]},
        ]), patch.object(server, "release_idle_ollama_models", return_value={"available": True, "unloaded": []}), \
             patch.object(server, "preload_ollama_model", return_value={"ok": True}), \
             patch.object(server, "unload_ollama_model", return_value=True) as unload, \
             patch.object(server, "model_residency_policy", return_value={"idle_seconds": 900}):
            state = server._acquire_vault_ingestion_gpu("job-2")
            job = {"gpu_reservation_id": "job-2", "gpu_model": state["model"],
                   "gpu_activity": state["_activity"], "gpu_admission": state["_admission"]}
            released = server._release_vault_ingestion_gpu(job)
            self.assertIsNone(server._release_vault_ingestion_gpu(job))
        unload.assert_not_called()
        self.assertEqual(released["state"], "cooldown")
        self.assertTrue(released["resident"])
        self.assertTrue(released["verified"])
        self.assertIn("15-minute idle cooldown", released["detail"])
        self.assertIsNone(server.GPU_VAULT_RESERVATION)
        self.assertEqual(server.GPU_OWNER, "NONE")
        self.assertNotIn("ingestion-model", server.MODEL_IN_FLIGHT)

    def test_batch_completion_starts_page_model_idle_cooldown(self):
        session = {"ingestion_model": "ingestion-model", "ingestion_model_protected": True}
        server.SESSIONS["session-cooldown"] = session
        server.MODEL_IN_FLIGHT["ingestion-model"] = 1
        with patch.object(server.time, "monotonic", return_value=123.0):
            self.assertTrue(server._start_vault_session_model_cooldown("session-cooldown", "ingestion-model"))
        self.assertTrue(session["ingestion_model_protected"])
        self.assertTrue(session["ingestion_model_cooldown"])
        self.assertEqual(server.MODEL_IN_FLIGHT["ingestion-model"], 1)
        self.assertEqual(server.MODEL_LAST_USED["ingestion-model"], 123.0)

    def test_governor_respects_cooldown_then_releases_expired_page_pin(self):
        server.SESSIONS["session-cooldown"] = {
            "surface": "knowledge-vault", "ingestion_model": "ingestion-model",
            "ingestion_model_protected": True, "ingestion_model_cooldown": True,
        }
        server.MODEL_IN_FLIGHT["ingestion-model"] = 1
        server.MODEL_LAST_USED["ingestion-model"] = 0.0
        catalog = {"available": True, "loaded": ["ingestion-model"],
                   "loaded_details": [{"name": "ingestion-model"}]}
        with patch.object(server, "ollama_catalog", return_value=catalog), \
             patch.object(server, "unload_ollama_model", return_value=True) as unload, \
             patch.object(server.time, "monotonic", return_value=100.0):
            early = server.release_idle_ollama_models(policy={"idle_seconds": 900}, pressure=True)
        unload.assert_not_called()
        self.assertEqual(early["protected"], ["ingestion-model"])
        with patch.object(server, "ollama_catalog", return_value=catalog), \
             patch.object(server, "unload_ollama_model", return_value=True) as unload, \
             patch.object(server.time, "monotonic", return_value=901.0):
            expired = server.release_idle_ollama_models(policy={"idle_seconds": 900})
        unload.assert_called_once_with("ingestion-model", reason="residency_governor")
        self.assertEqual(expired["unloaded"], ["ingestion-model"])
        self.assertFalse(server.SESSIONS["session-cooldown"]["ingestion_model_protected"])
        self.assertNotIn("ingestion-model", server.MODEL_IN_FLIGHT)

    def test_batch_watcher_reports_cooldown_after_job_finishes(self):
        class Process:
            stdout = iter([])
            returncode = 0

            def wait(self):
                return 0

        with patch.object(server, "ollama_catalog", side_effect=[
            {"available": True, "loaded": [], "loaded_details": []},
            {"available": True, "loaded": [], "loaded_details": []},
            {"available": True, "loaded": ["ingestion-model"], "loaded_details": [{"name": "ingestion-model"}]},
            {"available": True, "loaded": ["ingestion-model"], "loaded_details": [{"name": "ingestion-model"}]},
        ]), patch.object(server, "release_idle_ollama_models", return_value={"available": True, "unloaded": []}), \
             patch.object(server, "preload_ollama_model", return_value={"ok": True}), \
             patch.object(server, "unload_ollama_model", return_value=True) as unload, \
             patch.object(server, "model_residency_policy", return_value={"idle_seconds": 900}), \
             patch.object(server, "_organiser_summary", return_value={}):
            state = server._acquire_vault_ingestion_gpu("job-watch")
            process = Process()
            job = {"process": process, "state": "running", "gpu_reservation_id": "job-watch",
                   "gpu_model": state["model"], "gpu_activity": state["_activity"],
                   "gpu_admission": state["_admission"], "gpu_state": {"state": "resident"}}
            server.JOBS["job-watch"] = job
            server._watch_action("job-watch", process)
        unload.assert_not_called()
        self.assertEqual(job["state"], "complete")
        self.assertEqual(job["gpu_state"]["state"], "cooldown")

    def test_page_exit_releases_running_batch_and_hands_off_to_home_model(self):
        with patch.object(server, "ollama_catalog", side_effect=[
            {"available": True, "loaded": [], "loaded_details": []},
            {"available": True, "loaded": [], "loaded_details": []},
            {"available": True, "loaded": ["ingestion-model"], "loaded_details": [{"name": "ingestion-model"}]},
            {"available": True, "loaded": ["ingestion-model"], "loaded_details": [{"name": "ingestion-model"}]},
        ]), patch.object(server, "release_idle_ollama_models", return_value={"available": True, "unloaded": []}), \
             patch.object(server, "preload_ollama_model", return_value={"ok": True}), \
             patch.object(server, "unload_ollama_model", return_value=True) as unload, \
             patch.object(server, "model_residency_policy", return_value={"idle_seconds": 900}), \
             patch.object(server, "_terminate_process"), patch.object(server, "shutdown_idle_workloads"), \
             patch.object(server, "start_home_chat_model_preload") as handoff:
            state = server._acquire_vault_ingestion_gpu("job-3")
            with server.MODEL_ACTIVITY_LOCK:
                server.MODEL_IN_FLIGHT["ingestion-model"] += 1
            server.SESSIONS["session"] = {"jobs": {"job-3"}, "surface": "knowledge-vault",
                                          "ingestion_model": "ingestion-model", "ingestion_model_protected": True}
            server.JOBS["job-3"] = {
                "session_id": "session", "state": "running", "process": None,
                "gpu_reservation_id": "job-3", "gpu_model": state["model"],
                "gpu_activity": state["_activity"], "gpu_admission": state["_admission"],
            }
            self.assertTrue(server._close_session("session"))
        unload.assert_called_once_with("ingestion-model", reason="knowledge_vault_page_exit")
        handoff.assert_called_once_with()
        self.assertIsNone(server.GPU_VAULT_RESERVATION)

    def test_page_exit_releases_idle_ingestion_model_and_hands_off_home(self):
        with patch.object(server, "unload_ollama_model", return_value=True) as unload, \
             patch.object(server, "shutdown_idle_workloads"), \
             patch.object(server, "start_home_chat_model_preload") as handoff:
            server.MODEL_IN_FLIGHT["ingestion-model"] = 1
            server.SESSIONS["session-idle"] = {
                "jobs": set(), "surface": "knowledge-vault", "ingestion_model": "ingestion-model",
                "ingestion_model_protected": True,
            }
            self.assertTrue(server._close_session("session-idle"))
        unload.assert_called_once_with("ingestion-model", reason="knowledge_vault_page_exit")
        handoff.assert_called_once_with()
        self.assertNotIn("ingestion-model", server.MODEL_IN_FLIGHT)

    def test_preload_failure_rolls_back_gpu_reservation(self):
        with patch.object(server, "ollama_catalog", return_value={"available": True, "loaded": [], "loaded_details": []}), \
             patch.object(server, "release_idle_ollama_models", return_value={"available": True, "unloaded": []}), \
             patch.object(server, "preload_ollama_model", return_value={"ok": False, "detail": "model load failed"}), \
             patch.object(server, "unload_ollama_model", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "model load failed"):
                server._acquire_vault_ingestion_gpu("job-fail")
        self.assertIsNone(server.GPU_VAULT_RESERVATION)
        self.assertEqual(server.GPU_AI_ADMISSIONS, 0)
        self.assertEqual(server.GPU_OWNER, "NONE")
        self.assertFalse(server.MODEL_IN_FLIGHT)

    def test_ingestion_uses_local_gpu_arbitration_without_signal_service(self):
        catalog_before = {"available": True, "loaded": [], "loaded_details": []}
        catalog_after = {"available": True, "loaded": ["ingestion-model"], "loaded_details": [{"name": "ingestion-model"}]}
        with patch.object(server.SIGNAL_SERVICE_CLIENT, "health", side_effect=AssertionError("Knowledge Vault must not call Signal Service")), \
             patch.object(server, "ollama_catalog", side_effect=[catalog_before, catalog_before, catalog_after, catalog_after]), \
             patch.object(server, "release_idle_ollama_models", return_value={"available": True, "unloaded": []}), \
             patch.object(server, "preload_ollama_model", return_value={"ok": True}), \
             patch.object(server, "model_residency_policy", return_value={"idle_seconds": 900}):
            state = server._acquire_vault_ingestion_gpu("job-independent")
        self.assertEqual(state["model"], "ingestion-model")
        server._release_vault_ingestion_gpu({"gpu_reservation_id": "job-independent", "gpu_model": state["model"],
                                             "gpu_activity": state["_activity"], "gpu_admission": state["_admission"]})

    def test_page_entry_gpu_preflight_failure_rolls_back_reservation(self):
        catalog = {"available": False, "detail": "Ollama offline", "loaded": [], "loaded_details": []}
        with patch.object(server, "ollama_catalog", return_value=catalog):
            result = server.vault_gpu_entry_preflight("session-fail")
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "unverified")
        self.assertIsNone(server.GPU_VAULT_RESERVATION)

    def test_page_entry_model_residency_failure_unloads_partial_model(self):
        catalog = {"available": True, "loaded": [], "loaded_details": []}
        resident = {"available": True, "loaded": ["other-model"], "loaded_details": [{"name": "other-model"}]}
        with patch.object(server, "ollama_catalog", side_effect=[catalog, catalog, resident]), \
             patch.object(server, "release_idle_ollama_models", return_value={"available": True, "unloaded": []}), \
             patch.object(server, "preload_ollama_model", return_value={"ok": True}), \
             patch.object(server, "unload_ollama_model", return_value=True) as unload:
            result = server.vault_gpu_entry_preflight("session-residency-fail")
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "model_residency_unverified")
        unload.assert_called_once_with("ingestion-model", reason="vault_ingestion_preflight_recovery")


if __name__ == "__main__":
    unittest.main()
