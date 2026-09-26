import json
import unittest
from unittest.mock import patch

from signal_service.inference import InferenceRegistry, Provider, ProviderUnavailable


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, limit=-1):
        return self.payload


class InferenceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = InferenceRegistry()
        self.provider = Provider(
            "ollama-lan-embedding", "ollama", "nomic-embed-text",
            "http://192.168.1.100:11434", "", ("embeddings",), "lan", True, 5,
            "ollama-embed-v1",
        )

    def test_ollama_state_requires_the_configured_model(self):
        tags = {"models": [{"name": "other-model:latest"}]}
        with patch("signal_service.inference.urllib.request.urlopen", return_value=_Response(tags)):
            self.assertEqual(self.registry.state(self.provider), "Unavailable")

    def test_ollama_state_accepts_implicit_latest_and_embed_uses_api_embed(self):
        tags = {"models": [{"name": "nomic-embed-text:latest"}]}
        calls = []

        def urlopen(request, timeout):
            calls.append((request, timeout))
            if request.full_url.endswith("/api/tags"):
                return _Response(tags)
            self.assertTrue(request.full_url.endswith("/api/embed"))
            self.assertEqual(json.loads(request.data.decode("utf-8")), {
                "model": "nomic-embed-text", "input": ["semantic test"], "truncate": True,
            })
            return _Response({"embeddings": [[0.1, 0.2]]})

        with patch("signal_service.inference.urllib.request.urlopen", side_effect=urlopen):
            self.assertEqual(self.registry.state(self.provider), "Configured")
            self.assertEqual(self.registry.embed_many(["semantic test"], self.provider), [[0.1, 0.2]])
        self.assertEqual([call[0].full_url for call in calls], [
            "http://192.168.1.100:11434/api/tags",
            "http://192.168.1.100:11434/api/tags",
            "http://192.168.1.100:11434/api/embed",
        ])

    def test_missing_ollama_model_prevents_embedding_request(self):
        tags = {"models": []}
        with patch("signal_service.inference.urllib.request.urlopen", return_value=_Response(tags)) as urlopen:
            with self.assertRaisesRegex(ProviderUnavailable, "ollama-lan-embedding is unavailable"):
                self.registry.embed_many(["semantic test"], self.provider)
        self.assertEqual(urlopen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
