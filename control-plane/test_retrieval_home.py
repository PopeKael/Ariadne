import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import server
from home_chat_store import ChatStore
from librarian_events import LibrarianEventStream


class FakeRetrievalMcp:
    def __init__(self):
        self.retrieve_calls = []
        self.chat_calls = []

    def identity_system_prefix(self):
        return "IDENTITY", {"id": "ariadne", "version": "1.1.0", "scope": "user"}

    def retrieve_evidence(self, arguments):
        self.retrieve_calls.append(arguments)
        return {
            "query": arguments["query"],
            "candidate_count": 2,
            "selected_count": 1,
            "match_count": 1,
            "results": [{
                "chunk_id": "doc-1#chunk-0",
                "document_id": "doc-1",
                "title": "Vault note",
                "source_path": "Processed/vault-note.md",
                "path": "Processed/vault-note.md",
                "citation": {"title": "Vault note", "path": "Processed/vault-note.md"},
                "citation_text": "Vault note - Document; vault:Processed/vault-note.md",
                "combined_score": 0.91,
                "retrieval_method": "lexical+semantic",
                "content": "Bounded Vault evidence.",
                "excerpt": "Bounded Vault evidence.",
            }],
            "telemetry": {
                "pipeline": "bounded_hybrid_v1",
                "candidate_count": 2,
                "selected_count": 1,
                "evidence_chars": 24,
                "evidence_tokens_estimate": 6,
                "methods": ["lexical", "semantic"],
                "total_ms": 12.0,
            },
        }

    def ollama_chat(self, messages, **kwargs):
        self.chat_calls.append(messages)
        if kwargs.get("metrics") is not None:
            kwargs["metrics"].setdefault("ollama_calls", []).append({
                "total_duration": 1_000_000,
                "prompt_eval_count": 10,
                "eval_count": 4,
                "eval_duration": 1_000_000,
            })
        return "Vault-grounded answer."


class HomeRetrievalPathTests(unittest.TestCase):
    def test_personal_subject_context_resolves_channel_reference_from_catalogue(self):
        original_mcp = server._home_mcp
        fake = SimpleNamespace(
            meaningful_tokens=lambda value: {
                item.casefold() for item in str(value).replace(".", " ").split()
                if len(item) > 2
            },
            load_library=lambda: [{
                "page_title": "C&W Channel - Thumbnail Redesign Process",
                "summary": "Main YouTube Channel. Weekly Sunday public videos with our bigger story-style Thailand content.",
                "entities": ["Chanya & Wazza's Thailand"],
                "people": [],
            }],
        )
        server._home_mcp = lambda: fake
        try:
            retrieval_query = server._home_retrieval_query(
                "Give me new ideas for our video channel.",
                [{"role": "user", "content": "What should we make for the main channel?"}],
            )
        finally:
            server._home_mcp = original_mcp
        self.assertIn("C&W Channel - Thumbnail Redesign Process", retrieval_query)
        self.assertIn("Main YouTube Channel", retrieval_query)

    def test_world_state_subjects_prevent_generic_catalogue_contamination(self):
        world_state = {
            "self": {
                "channels": [{
                    "title": "C&W Channel - Living in Thailand Challenges",
                    "summary": "Authentic Thailand life and long-term expat reality.",
                }],
                "projects": [{
                    "title": "Ariadne, the librarian",
                    "summary": "The provider-independent personal Knowledge Vault and MCP project.",
                }],
            },
            "request_context": {
                "matched_subjects": ["C&W Channel - Living in Thailand Challenges", "Ariadne, the librarian"],
            },
        }
        retrieval_query = server._home_retrieval_query(
            "Who am I, what are we working on, and what matters now?", [], world_state,
        )
        self.assertIn("C&W Channel - Living in Thailand Challenges", retrieval_query)
        self.assertIn("Ariadne, the librarian", retrieval_query)
        self.assertNotIn("Is it working now?", retrieval_query)

    def test_final_world_state_context_contains_self_now_and_request_fields(self):
        context = server._home_world_state_context({
            "world_state_version": "1.0.0",
            "derived": True,
            "self": {
                "owner": "Warren Gerdes",
                "known_handles": [],
                "people_labels": [],
                "entity_labels": [],
                "channels": [{"title": "C&W Channel", "summary": "Thailand life."}],
                "projects": [{"title": "Ariadne", "summary": "Knowledge Vault."}],
            },
            "now": {"local_date": "2026-08-24", "timezone": "SE Asia Standard Time"},
            "request_context": {"matched_subjects": ["Ariadne"]},
        })
        self.assertIn('"owner":"Warren Gerdes"', context)
        self.assertIn('"title":"C&W Channel"', context)
        self.assertIn('"title":"Ariadne"', context)
        self.assertIn('"matched_subjects":["Ariadne"]', context)

    def test_vault_route_uses_evidence_set_and_disabled_route_skips_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original_store = server.HOME_CHAT_STORE
            original_mcp = server._home_mcp
            original_planner = server.home_planner_request
            original_events = server.LIBRARIAN_EVENT_STREAM
            original_event = server.record_home_event
            fake = FakeRetrievalMcp()
            server.HOME_CHAT_STORE = ChatStore(root)
            server._home_mcp = lambda: fake
            server.record_home_event = lambda *args, **kwargs: None
            server.LIBRARIAN_EVENT_STREAM = LibrarianEventStream(root / "events.jsonl")
            planner = {
                "plan": {"use_vault": True, "tools": [], "needs_current_information": False},
                "semantic": {"intent": "personal_history", "reasoning_complexity": "medium", "ambiguity": "low", "confidence": 0.9},
                "fallback": False,
                "telemetry": {},
            }
            server.home_planner_request = lambda *args, **kwargs: planner
            try:
                chat = server.HOME_CHAT_STORE.create()
                enabled = server.home_chat_payload("What is in my Vault?", [], "auto", chat["chat_id"])
                self.assertTrue(enabled["used_vault"])
                self.assertEqual(len(fake.retrieve_calls), 1)
                self.assertEqual(enabled["retrieval"]["candidate_count"], 2)
                event_types = {item["event_type"] for item in server.LIBRARIAN_EVENT_STREAM.read_recent()}
                self.assertIn("RETRIEVAL_STARTED", event_types)
                self.assertIn("RETRIEVAL_RESULT", event_types)

                disabled_chat = server.HOME_CHAT_STORE.create()
                disabled = server.home_chat_payload("What is in my Vault?", [], "never", disabled_chat["chat_id"])
                self.assertFalse(disabled["used_vault"])
                self.assertEqual(len(fake.retrieve_calls), 1)
            finally:
                server.HOME_CHAT_STORE = original_store
                server._home_mcp = original_mcp
                server.home_planner_request = original_planner
                server.LIBRARIAN_EVENT_STREAM = original_events
                server.record_home_event = original_event


if __name__ == "__main__":
    unittest.main()
