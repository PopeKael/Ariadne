import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from evidence_router import classify_request, decide, external_search_needed  # noqa: E402
from search_providers import SearchProvider, SearchProviderRegistry  # noqa: E402
from home_chat_store import ChatStore  # noqa: E402


class EvidenceFirstTests(unittest.TestCase):
    def test_hornsby_question_is_automatically_verification_required(self):
        result = classify_request("Is the Hornsby Water Clock at Hornsby still there, and does it still work?")
        self.assertTrue(result["verification_required"])
        self.assertTrue(result["current_information"])
        self.assertTrue(result["named_or_obscure"])

    def test_simple_conversation_does_not_search(self):
        result = decide(
            "How are you today?",
            planner_result={"semantic": {"needs_personal_history": False, "confidence": 0.95}},
            vault_mode="auto", vault_available=True, search_available=True,
        )
        self.assertFalse(result.verification_required)
        self.assertFalse(result.use_vault)
        self.assertFalse(result.external_search)

    def test_vault_and_attachment_evidence_precede_external_search_when_adequate(self):
        decision = decide(
            "What is the Hornsby Water Clock?",
            planner_result={"semantic": {"needs_personal_history": False, "confidence": 0.9}},
            vault_mode="auto", vault_available=True, search_available=True,
        )
        self.assertFalse(external_search_needed(decision, vault_source_count=1))
        self.assertFalse(external_search_needed(decision, attachment_source_count=2))
        self.assertTrue(external_search_needed(decision, vault_source_count=0, attachment_source_count=0))

    def test_configured_provider_is_routed_and_unique_sources_are_counted(self):
        provider = SearchProvider("test-search", "json", "https://search.test/api", "test config", ("search", "fetch"), "nas", True, 1)
        registry = SearchProviderRegistry([provider])
        with patch("search_providers._request", side_effect=[
            (b'{"results":[{"title":"Clock","url":"https://example.test/clock","content":"It remains."},{"title":"Clock duplicate","url":"https://example.test/clock#map","content":"Duplicate."}]}', "utf-8"),
            (b"<html><body><article><p>The clock remains in place.</p></article></body></html>", "utf-8"),
        ]):
            result = registry.search("Hornsby Water Clock", limit=5, fetch_limit=1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "test-search")
        self.assertEqual(len(result["results"]), 1)
        self.assertTrue(result["results"][0]["fetched"])
        self.assertEqual(registry.snapshot()["providers"][0]["health"], "healthy")

    def test_failed_verification_blocks_model_guess(self):
        class EmptyMcp:
            def identity_system_prefix(self):
                return "IDENTITY", {"id": "ariadne", "version": "1.1.0", "scope": "user"}

            def retrieve_evidence(self, arguments):
                return {"results": [], "candidate_count": 0, "selected_count": 0, "match_count": 0}

            def ollama_chat(self, messages, **kwargs):
                raise AssertionError("The response model must not be called without verification evidence.")

        class EmptySearch:
            def route(self):
                return object()

            def search(self, query, **kwargs):
                return {"ok": False, "results": [], "error": "test unavailable"}

        with tempfile.TemporaryDirectory() as temporary:
            original_store = server.HOME_CHAT_STORE
            original_mcp = server._home_mcp
            original_planner = server.home_planner_request
            original_search = server.SEARCH_PROVIDER_REGISTRY
            original_adaptive = server.home_adaptive_context_for_query
            server.HOME_CHAT_STORE = ChatStore(Path(temporary))
            server._home_mcp = lambda: EmptyMcp()
            server.home_planner_request = lambda *args, **kwargs: {
                "plan": {"use_vault": False, "tools": [], "needs_current_information": False},
                "semantic": {"needs_personal_history": False, "needs_current_information": False, "confidence": 0.9},
                "world_state": {}, "fallback": False, "telemetry": {},
            }
            server.SEARCH_PROVIDER_REGISTRY = EmptySearch()
            server.home_adaptive_context_for_query = lambda *args, **kwargs: {}
            try:
                chat = server.HOME_CHAT_STORE.create()
                result = server.home_chat_payload("Is the Hornsby Water Clock still there?", [], "auto", chat["chat_id"])
            finally:
                server.HOME_CHAT_STORE = original_store
                server._home_mcp = original_mcp
                server.home_planner_request = original_planner
                server.SEARCH_PROVIDER_REGISTRY = original_search
                server.home_adaptive_context_for_query = original_adaptive
        self.assertIn("couldn't verify", result["answer"])
        self.assertEqual(result["sources"], [])

    def test_interest_profile_is_not_injected_for_unrelated_query(self):
        with patch.object(server, "home_adaptive_context", return_value={
            "active_interests": ["AI hardware"],
            "learned_interests": [{"label": "Ollama", "score": 1}],
        }):
            result = server.home_adaptive_context_for_query("What is the Hornsby Water Clock status?", {"semantic": {"needs_personal_history": False}})
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
