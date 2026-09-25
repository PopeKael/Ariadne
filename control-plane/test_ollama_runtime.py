import unittest
from pathlib import Path
from unittest.mock import patch

import ollama_runtime


class OllamaRuntimeTests(unittest.TestCase):
    def test_manifest_path_matches_ollama_library_layout(self):
        path = ollama_runtime.model_manifest_path(
            r"F:\AI\Models\Ollama", "qwen3.5:9b-q4_K_M"
        )
        self.assertEqual(
            path,
            Path(r"F:\AI\Models\Ollama\manifests\registry.ollama.ai\library\qwen3.5\9b-q4_K_M"),
        )

    def test_healthy_catalogue_does_not_restart_or_wait(self):
        with patch.object(ollama_runtime, "sync_model_store_environment", return_value=r"F:\AI\Models\Ollama"), \
             patch.object(ollama_runtime, "store_contains_model") as contains, \
             patch.object(ollama_runtime, "_restart_exact_ollama") as restart:
            result = ollama_runtime.startup_preflight(
                required_models=("qwen3.5:9b-q4_K_M",),
                request_models=lambda _endpoint, _timeout: ["qwen3.5:9b-q4_K_M"],
            )
        self.assertEqual(result["state"], "online")
        self.assertFalse(result["repaired"])
        contains.assert_not_called()
        restart.assert_not_called()

    def test_stale_catalogue_restarts_exact_verified_listener(self):
        responses = iter([[], ["qwen3.5:9b-q4_K_M"]])
        with patch.object(ollama_runtime, "sync_model_store_environment", return_value=r"F:\AI\Models\Ollama"), \
             patch.object(ollama_runtime, "store_contains_model", return_value=True), \
             patch.object(ollama_runtime, "time") as clock:
            clock.monotonic.side_effect = [0.0, 0.0, 0.1]
            clock.sleep.return_value = None
            result = ollama_runtime.startup_preflight(
                required_models=("qwen3.5:9b-q4_K_M",),
                request_models=lambda _endpoint, _timeout: next(responses),
                listener_paths=lambda: [(25344, r"C:\Users\Warren\AppData\Local\Programs\Ollama\ollama.exe")],
                restart_ollama=lambda paths, store: (paths == [(25344, r"C:\Users\Warren\AppData\Local\Programs\Ollama\ollama.exe")], "restarted"),
            )
        self.assertEqual(result["state"], "online")
        self.assertTrue(result["repaired"])
        self.assertEqual(result["detail"], "restarted")

    def test_missing_store_does_not_kill_or_start_process(self):
        with patch.object(ollama_runtime, "sync_model_store_environment", return_value=""), \
             patch.object(ollama_runtime, "_restart_exact_ollama") as restart:
            result = ollama_runtime.startup_preflight(
                required_models=("qwen3.5:9b-q4_K_M",),
                request_models=lambda _endpoint, _timeout: [],
            )
        self.assertEqual(result["state"], "degraded")
        self.assertIn("missing", result["detail"])
        restart.assert_not_called()

    def test_offline_ollama_starts_without_killing_an_unverified_process(self):
        with patch.object(ollama_runtime, "sync_model_store_environment", return_value=r"F:\AI\Models\Ollama"), \
             patch.object(ollama_runtime, "store_contains_model", return_value=True):
            result = ollama_runtime.startup_preflight(
                required_models=("qwen3.5:9b-q4_K_M",),
                repair_timeout=0.1,
                request_models=lambda _endpoint, _timeout: None,
                listener_paths=lambda: [],
                restart_ollama=lambda paths, store: (paths == [], store == r"F:\AI\Models\Ollama" and "started" or ""),
            )
        self.assertEqual(result["state"], "degraded")
        self.assertIn("still not visible", result["detail"])


if __name__ == "__main__":
    unittest.main()
