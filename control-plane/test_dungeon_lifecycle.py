import unittest
from unittest.mock import patch

import server


class DungeonLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.host_acquire_patch = patch.object(server, "host_acquire_gpu_lease", return_value="dungeon-test-lease")
        self.host_acquire_patch.start()
        self.host_release_patch = patch.object(server, "host_release_gpu_lease")
        self.host_release_patch.start()
        self.original = {
            "owner": server.GPU_OWNER,
            "transition": server.GPU_TRANSITION_STATE,
            "detail": server.GPU_TRANSITION_DETAIL,
            "operation": server.GPU_TRANSITION_OPERATION,
            "started": server.GPU_TRANSITION_STARTED_AT,
            "admissions": server.GPU_AI_ADMISSIONS,
            "shutdown_requested": server.SHUTDOWN_REQUESTED,
            "profile": server.ACTIVE_PROFILE,
            "delegation": dict(server.ARIADNE_DELEGATION),
        }
        server.GPU_OWNER = "NONE"
        server.GPU_TRANSITION_STATE = "IDLE"
        server.GPU_TRANSITION_DETAIL = "GPU is available."
        server.GPU_TRANSITION_OPERATION = None
        server.GPU_TRANSITION_STARTED_AT = None
        server.GPU_AI_ADMISSIONS = 0
        server.SHUTDOWN_REQUESTED = False
        server.ACTIVE_PROFILE = "DEV"
        server.GPU_VAULT_RESERVATION = None
        server.MODEL_IN_FLIGHT.clear()
        server.ARIADNE_DELEGATION.update(state="ARIADNE_ACTIVE", operation_id=None, models=[], detail="Ariadne is active.")

    def tearDown(self):
        self.host_acquire_patch.stop()
        self.host_release_patch.stop()
        server.GPU_OWNER = self.original["owner"]
        server.GPU_TRANSITION_STATE = self.original["transition"]
        server.GPU_TRANSITION_DETAIL = self.original["detail"]
        server.GPU_TRANSITION_OPERATION = self.original["operation"]
        server.GPU_TRANSITION_STARTED_AT = self.original["started"]
        server.GPU_AI_ADMISSIONS = self.original["admissions"]
        server.SHUTDOWN_REQUESTED = self.original["shutdown_requested"]
        server.ACTIVE_PROFILE = self.original["profile"]
        server.ARIADNE_DELEGATION.clear()
        server.ARIADNE_DELEGATION.update(self.original["delegation"])

    def test_handoff_releases_workloads_models_and_keeps_core_out_of_shutdown(self):
        order = []
        with patch.object(server, "ollama_catalog", side_effect=lambda: (order.append("residency_snapshot") or {"available": True, "loaded": ["qwen3.5:9b-q4_K_M"]})), \
             patch.object(server, "host_acquire_gpu_lease", side_effect=lambda *args, **kwargs: (order.append("host_lease") or "dungeon-test-lease")), \
             patch.object(server, "_stop_gods_eye_view"), \
             patch.object(server, "gods_eye_view_status", return_value={"state": "Stopped", "managed": True}), \
             patch.object(server, "release_workloads") as workloads, \
             patch.object(server, "release_idle_ollama_models", return_value={"available": True, "remaining": []}) as models, \
             patch.object(server, "host_suspend_for_external_workload", return_value={"ok": True}), \
             patch.object(server, "emit", return_value=True) as launch:
            result = server.begin_dungeon_handoff()

        self.assertTrue(result["ok"])
        self.assertEqual(server.GPU_OWNER, "TRANSITION")
        self.assertEqual(server.GPU_TRANSITION_STATE, "DUNGEON_STARTING")
        self.assertEqual(server.ARIADNE_DELEGATION["state"], "DUNGEON_STARTING")
        self.assertEqual(server.ARIADNE_DELEGATION["models"], ["qwen3.5:9b-q4_K_M"])
        self.assertEqual(order, ["residency_snapshot", "host_lease"])
        workloads.assert_called_once_with(force=True, terminate_wsl=False)
        models.assert_not_called()
        launch.assert_called_once_with("launch_dungeon", operation_id=result["operation_id"])
        self.assertFalse(server.SHUTDOWN_REQUESTED)

    def test_failed_launch_recovers_reservation_and_runtime(self):
        with patch.object(server, "ollama_catalog", return_value={"available": True, "loaded": []}), \
             patch.object(server, "_stop_gods_eye_view"), \
             patch.object(server, "gods_eye_view_status", return_value={"state": "Stopped", "managed": True}), \
             patch.object(server, "release_workloads"), \
             patch.object(server, "restore_dungeon_suspended_workloads"), \
             patch.object(server, "release_idle_ollama_models", return_value={"available": True, "remaining": []}), \
             patch.object(server, "host_suspend_for_external_workload", return_value={"ok": True}), \
             patch.object(server, "host_resume_after_external_workload") as resume, \
             patch.object(server, "set_profile"), \
             patch.object(server, "emit", return_value=False):
            result = server.begin_dungeon_handoff()

        self.assertFalse(result["ok"])
        self.assertEqual(server.GPU_OWNER, "NONE")
        self.assertEqual(server.ARIADNE_DELEGATION["state"], "ARIADNE_ACTIVE")
        resume.assert_called_once()

    def test_host_runtime_ipc_rejection_stops_before_dungeon_launch(self):
        with patch.object(server, "ollama_catalog", return_value={"available": True, "loaded": []}), \
             patch.object(server, "_stop_gods_eye_view"), \
             patch.object(server, "gods_eye_view_status", return_value={"state": "Stopped", "managed": True}), \
             patch.object(server, "release_workloads"), \
             patch.object(server, "restore_dungeon_suspended_workloads"), \
             patch.object(server, "release_idle_ollama_models", return_value={"available": True, "remaining": []}), \
             patch.object(server, "host_suspend_for_external_workload", side_effect=RuntimeError("Unsupported runtime IPC request.")), \
             patch.object(server, "host_resume_after_external_workload") as resume, \
             patch.object(server, "set_profile"), \
             patch.object(server, "emit") as launch:
            result = server.begin_dungeon_handoff()

        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "Unsupported runtime IPC request.")
        launch.assert_not_called()
        resume.assert_called_once()
        self.assertEqual(server.GPU_OWNER, "NONE")
        self.assertEqual(server.GPU_TRANSITION_STATE, "IDLE")
        self.assertEqual(server.ARIADNE_DELEGATION["state"], "ARIADNE_ACTIVE")

    def test_failed_runtime_recovery_keeps_host_lease_and_transition_blocked(self):
        with patch.object(server, "ollama_catalog", return_value={"available": True, "loaded": []}), \
             patch.object(server, "_stop_gods_eye_view"), \
             patch.object(server, "gods_eye_view_status", return_value={"state": "Stopped", "managed": True}), \
             patch.object(server, "release_workloads"), \
             patch.object(server, "release_idle_ollama_models", return_value={"available": True, "remaining": []}), \
             patch.object(server, "host_suspend_for_external_workload", return_value={"ok": True}), \
             patch.object(server, "host_resume_after_external_workload", side_effect=RuntimeError("restore failed")), \
             patch.object(server, "restore_dungeon_suspended_workloads"), \
             patch.object(server, "emit", return_value=False), \
             patch.object(server, "host_release_gpu_lease") as release:
            result = server.begin_dungeon_handoff()
        self.assertEqual(result["state"], "recovery_failed")
        self.assertEqual(server.GPU_TRANSITION_STATE, "DUNGEON_RECOVERY_FAILED")
        self.assertEqual(server.ARIADNE_DELEGATION["state"], "RECOVERY_FAILED")
        self.assertEqual(server.ARIADNE_DELEGATION["gpu_lease_id"], "dungeon-test-lease")
        release.assert_not_called()

    def test_normal_exit_restores_runtime_and_clears_delegation(self):
        server.ARIADNE_DELEGATION.update(state="DUNGEON_STARTING", operation_id="a" * 32, models=[], profile="DEV")
        server.GPU_OWNER = "TRANSITION"
        server.GPU_TRANSITION_STATE = "DUNGEON_STARTING"
        with patch.object(server, "host_resume_after_external_workload") as resume, \
             patch.object(server, "set_profile") as profile, \
             patch.object(server, "start_home_chat_model_preload") as preload:
            active = server.report_dungeon_handoff({"operation_id": "a" * 32, "state": "ACTIVE"})
            self.assertEqual(server.GPU_OWNER, "DUNGEON")
            self.assertEqual(server.GPU_TRANSITION_DETAIL, "Ariadne is currently running Dungeon.")
            result = server.report_dungeon_handoff({"operation_id": "a" * 32, "state": "CLOSED"})

        self.assertTrue(active["ok"])
        self.assertTrue(result["ok"])
        self.assertEqual(server.GPU_OWNER, "NONE")
        self.assertEqual(server.ARIADNE_DELEGATION["state"], "ARIADNE_ACTIVE")
        resume.assert_called_once()
        profile.assert_called_once_with("DEV")
        preload.assert_not_called()

    def test_normal_ai_admission_is_rejected_with_dungeon_busy_detail(self):
        with patch.object(server, "host_gpu_lease", side_effect=RuntimeError("Rust host has an exclusive GPU workload lease; AI admission was denied.")):
            with self.assertRaisesRegex(RuntimeError, "Rust host has an exclusive GPU workload lease"):
                with server.ai_gpu_admission():
                    self.fail("AI admission must not proceed during Dungeon ownership")

    def test_crash_recovery_is_idempotent_and_restores_stopped_home_model(self):
        server.ARIADNE_DELEGATION.update(state="DUNGEON_ACTIVE", operation_id="b" * 32, models=[server.HOME_CHAT_MODEL])
        server.GPU_OWNER = "DUNGEON"
        with patch.object(server, "host_resume_after_external_workload") as resume, \
             patch.object(server, "set_profile"), \
             patch.object(server, "start_home_chat_model_preload") as preload:
            result = server.report_dungeon_handoff({"operation_id": "b" * 32, "state": "FAILED"})
            duplicate = server.report_dungeon_handoff({"operation_id": "b" * 32, "state": "CLOSED"})

        self.assertTrue(result["ok"])
        self.assertFalse(duplicate["ok"])
        self.assertEqual(server.ARIADNE_DELEGATION["state"], "ARIADNE_ACTIVE")
        resume.assert_called_once()
        preload.assert_called_once_with()

    def test_standalone_dungeon_does_not_require_ariadne_protocol_or_changes(self):
        # Integration launches the existing binary from Ariadne; Dungeon's
        # standalone launcher remains outside this repository and unmodified.
        import pathlib

        dungeon = pathlib.Path(r"F:\AI\DungeonCrawler\scripts\DungeonCrawler.cs")
        if dungeon.exists():
            source = dungeon.read_text(encoding="utf-8")
            self.assertIn('Path.Combine(root, "scripts", "dungeon-tray.ps1")', source)
            self.assertNotIn("ariadne-runtime", source)


if __name__ == "__main__":
    unittest.main()
