import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import credential_store
from inference import InferenceRegistry, Provider, default_providers, normalize_ollama_model_id, ollama_model_catalogue


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

    def test_ollama_latest_alias_matches_untagged_model_without_rewriting_saved_route(self):
        self.assertEqual(normalize_ollama_model_id("nomic-embed-text"), "nomic-embed-text")
        self.assertEqual(normalize_ollama_model_id("nomic-embed-text:latest"), "nomic-embed-text")
        catalogue = ollama_model_catalogue([
            {"name": "nomic-embed-text:latest", "details": {"family": "nomic-bert"}},
            {"model": "nomic-embed-text", "details": {"family": "nomic-bert"}},
        ])
        self.assertEqual([item["id"] for item in catalogue], ["nomic-embed-text"])
        self.assertEqual(catalogue[0]["capabilities"], ["embeddings"])
        self.assertEqual(credential_store._target("ARIADNE_EXTERNAL_012345abcdef_API_KEY"), "Ariadne/ExternalProviders/012345abcdef/API_KEY")
        with self.assertRaises(ValueError):
            credential_store._target("ARIADNE_EXTERNAL_not-a-safe-id_API_KEY")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "configuration.json"
            saved = {"inference": {"routes": {"embedding": "ollama-embedding"}, "providers": [
                {**next(item.as_dict() for item in default_providers() if item.provider_id == "ollama-embedding"), "model_id": "nomic-embed-text"},
            ]}}
            path.write_text(json.dumps(saved), encoding="utf-8")
            registry = InferenceRegistry(path)
            route = registry.route("embedding")
            self.assertEqual(route.model_id, "nomic-embed-text")
            self.assertEqual(registry.routes["embedding"], "ollama-embedding")
            self.assertEqual(registry.snapshot()["routes"]["embedding"]["route_selection"], "ollama-embedding")

    def test_added_openai_compatible_provider_preserves_route_and_label(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "configuration.json"
            model_id = "provider-model-v2"
            saved_route = f"external-012345abcdef::{model_id}"
            provider = {
                "provider_id": "external-012345abcdef", "provider_type": "openai-compatible",
                "display_name": "Workshop API", "model_id": "default-model",
                "endpoint": "https://api.example.test/v1",
                "credential_reference": "ARIADNE_EXTERNAL_012345ABCDEF_API_KEY",
                "capabilities": ["chat", "structured_output"], "location": "cloud",
                "enabled": True, "priority": 70,
            }
            path.write_text(json.dumps({"inference": {"routes": {"home_chat": saved_route}, "providers": [provider]}}), encoding="utf-8")
            registry = InferenceRegistry(path)
            route = registry.route("home_chat")
            self.assertEqual(route.model_id, model_id)
            self.assertEqual(route.display_name, "Workshop API")
            self.assertEqual(registry.routes["home_chat"], saved_route)


if __name__ == "__main__":
    unittest.main()
