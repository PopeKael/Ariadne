import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from inference import InferenceRegistry, Provider, default_providers


class InferenceRegistryTests(unittest.TestCase):
    def test_local_model_defaults_and_legacy_overrides_use_ipv4(self):
        for override in (None, "http://localhost:11434/", "http://[::1]:11434/"):
            with self.subTest(override=override), patch.dict(os.environ, {}, clear=True):
                if override is not None:
                    os.environ["ARIADNE_OLLAMA_URL"] = override
                providers = default_providers()
                self.assertTrue(all(item.endpoint == "http://127.0.0.1:11434" for item in providers if item.provider_type == "ollama"))

    def test_saved_ollama_loopback_routes_use_ipv4_and_public_routes_are_preserved(self):
        for endpoint, expected in (
            ("http://localhost:11434", "http://127.0.0.1:11434"),
            ("http://[::1]:11434", "http://127.0.0.1:11434"),
            ("https://ariadne.dia.net.au/internal/ollama", "https://ariadne.dia.net.au/internal/ollama"),
        ):
            with self.subTest(endpoint=endpoint):
                provider = Provider.from_dict({"provider_type": "ollama", "endpoint": endpoint})
                self.assertEqual(provider.endpoint, expected)

    def test_legacy_environment_models_become_visible_routes_without_inventing_classification(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {
            "ARIADNE_HOME_CHAT_MODEL": "qwen3.5:9b-q4_K_M",
            "ARIADNE_PLANNER_MODEL": "qwen3.5:9b-q4_K_M",
            "ARIADNE_EMBEDDING_MODEL": "nomic-embed-text",
        }, clear=False):
            registry = InferenceRegistry(Path(temporary) / "configuration.json")
            snapshot = registry.snapshot({"home_chat": "Configured", "planner": "Configured"})
        self.assertEqual(snapshot["routes"]["home_chat"]["provider_id"], "ollama-desktop")
        self.assertEqual(snapshot["routes"]["home_chat"]["model_id"], "qwen3.5:9b-q4_K_M")
        self.assertEqual(snapshot["routes"]["planner"]["model_id"], "qwen3.5:9b-q4_K_M")
        self.assertEqual(snapshot["routes"]["embedding"]["model_id"], "nomic-embed-text")
        self.assertEqual(snapshot["routes"]["summarization_classification"]["state"], "Unconfigured")

    def test_explicit_saved_route_overrides_legacy_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "configuration.json"
            path.write_text(json.dumps({"inference": {"routes": {"home_chat": "ollama-planner"}}}), encoding="utf-8")
            with patch.dict(os.environ, {
                "ARIADNE_HOME_PROVIDER": "", "ARIADNE_PLANNER_PROVIDER": "",
                "ARIADNE_EMBEDDING_PROVIDER": "", "ARIADNE_SUMMARIZATION_PROVIDER": "",
            }, clear=False):
                registry = InferenceRegistry(path)
            self.assertEqual(registry.route("home_chat").provider_id, "ollama-planner")


if __name__ == "__main__":
    unittest.main()
