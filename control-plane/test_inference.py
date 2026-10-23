import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import credential_store
from inference import (
    DEFAULT_CONTEXT_TOKENS, InferenceRegistry, Provider, default_providers,
    effective_context_tokens, local_model_context_limit, normalize_ollama_model_id, ollama_model_catalogue,
    resolve_ollama_model_id,
)


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
        }, clear=True):
            registry = InferenceRegistry(Path(temporary) / "configuration.json")
            with patch.object(credential_store, "get_credential", return_value=""):
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

    def test_ollama_aliases_resolve_to_the_installed_latest_tag(self):
        self.assertEqual(resolve_ollama_model_id("nomic-embed-text", ["nomic-embed-text:latest"]), "nomic-embed-text:latest")
        self.assertEqual(resolve_ollama_model_id("nomic-embed-text:latest", ["nomic-embed-text:latest"]), "nomic-embed-text:latest")
        self.assertEqual(Provider.from_dict({"provider_type": "ollama", "model_id": "nomic-embed-text:latest"}).model_id,
                         "nomic-embed-text")

    def test_same_weights_and_configuration_group_as_one_canonical_model(self):
        entries = [
            {"name": "qwen3.5:9b-q4_K_M", "digest": "base-manifest", "size": 700, "details": {"family": "qwen35"}},
            {"name": "ariadne-qwen-code:9b-32k", "digest": "coding-manifest", "size": 701, "details": {"family": "qwen35"}},
        ]
        common = {
            "modelfile": "FROM F:\\AI\\Models\\Ollama\\blobs\\sha256-shared\nPARAMETER temperature 1\n",
            "template": "{{ .Prompt }}", "system": "",
            "model_info": {"qwen35.context_length": 262144, "qwen35.block_count": 32},
            "capabilities": ["completion", "vision", "tools"],
        }
        metadata = {
            "qwen3.5:9b-q4_K_M": {**common, "parameters": "temperature 1"},
            "ariadne-qwen-code:9b-32k": {**common, "parameters": "num_ctx 32768\ntemperature 1"},
        }
        catalogue = ollama_model_catalogue(entries, metadata)
        self.assertEqual(len(catalogue), 1)
        self.assertEqual(catalogue[0]["canonical_name"], "qwen3.5:9b-q4_K_M")
        self.assertEqual(catalogue[0]["effective_context_tokens"], 32768)
        self.assertIn({"name": "ariadne-qwen-code:9b-32k", "purpose": "coding alias", "role": "Coding"}, catalogue[0]["aliases"])
        self.assertIn("Coding", catalogue[0]["roles"])
        self.assertIn("Chat", catalogue[0]["roles"])

        tag_entries = [
            {"name": "qwen3.8-27b:ctx32k", "digest": "ctx", "details": {"family": "qwen35"}},
            {"name": "qwen3.8-27b:latest", "digest": "latest", "details": {"family": "qwen35"}},
        ]
        shared = {"modelfile": "FROM /models/blobs/sha256-shared\n", "template": "x", "parameters": ""}
        aliases = ollama_model_catalogue(tag_entries, {"qwen3.8-27b:ctx32k": shared, "qwen3.8-27b:latest": shared})
        self.assertEqual(aliases[0]["canonical_name"], "qwen3.8-27b")
        self.assertEqual(resolve_ollama_model_id("qwen3.8-27b", [row["name"] for row in tag_entries], aliases),
                         "qwen3.8-27b:ctx32k")

    def test_default_context_is_32k_and_clamps_only_to_declared_model_limit(self):
        self.assertEqual(DEFAULT_CONTEXT_TOKENS, 32768)
        self.assertEqual(effective_context_tokens(262144), 32768)
        self.assertEqual(effective_context_tokens(16384), 16384)
        self.assertEqual(effective_context_tokens(None), 32768)

    def test_local_gpt_oss_20b_catalogue_limit_overrides_larger_runtime_metadata(self):
        entry = {"name": "gpt-oss:20b", "digest": "gpt-oss", "details": {"family": "gptoss"}}
        metadata = {"gpt-oss:20b": {"capabilities": ["completion"], "model_info": {"gptoss.context_length": 131072}}}
        row = ollama_model_catalogue([entry], metadata)[0]
        self.assertEqual(row["context_max"], 8192)
        self.assertEqual(row["effective_context_tokens"], 8192)
        self.assertEqual(local_model_context_limit("gpt-oss:20b:latest", 32768), 8192)
        self.assertEqual(local_model_context_limit("qwen3.5:9b", 32768), 32768)

    def test_gpt_oss_local_context_limit_follows_same_weight_catalogue_aliases(self):
        names = ["gpt-oss:20b", "ariadne-gpt-oss:20b-16k"]
        shared = {
            "modelfile": "FROM /models/blobs/sha256-gpt-oss\n",
            "template": "{{ .Prompt }}", "parameters": "num_ctx 16384",
            "model_info": {"gptoss.context_length": 131072},
        }
        catalogue = ollama_model_catalogue(
            [{"name": name, "details": {"family": "gptoss"}} for name in names],
            {name: shared for name in names},
        )
        self.assertEqual(len(catalogue), 1)
        self.assertEqual(catalogue[0]["context_max"], 8192)

    def test_embedding_only_model_is_not_chat_capable(self):
        catalogue = ollama_model_catalogue([
            {"name": "nomic-embed-text:latest", "details": {"family": "nomic-bert"}},
            {"name": "qwen3.5:9b", "details": {"family": "qwen35"}},
        ], {
            "nomic-embed-text:latest": {"capabilities": ["embedding"]},
            "qwen3.5:9b": {"capabilities": ["completion", "vision"]},
        })
        embedding = next(item for item in catalogue if item["role"] == "Embedding")
        chat = next(item for item in catalogue if item["canonical_name"] == "qwen3.5:9b")
        self.assertFalse(embedding["can_chat"])
        self.assertTrue(chat["can_chat"])
        self.assertIn("chat", chat["capabilities"])

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
