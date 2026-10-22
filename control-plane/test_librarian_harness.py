import json
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import nullcontext
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from home_chat_store import ChatStore  # noqa: E402
from librarian_events import LibrarianEventStream  # noqa: E402
from librarian_harness import interpret_request, resolve_policy, semantic_schema, validate_interpretation  # noqa: E402


def semantic(**overrides):
    value = {
        "intent": "ordinary_factual",
        "needs_personal_history": False,
        "needs_current_information": False,
        "needs_attachment": False,
        "reasoning_complexity": "low",
        "ambiguity": "low",
        "confidence": 0.82,
    }
    value.update(overrides)
    return value


def context(**overrides):
    value = {
        "active_knowledge_source": "auto",
        "selected_tool_ids": [],
        "attachments": [],
        "available_tools": [{"tool_id": "document-analysis", "enabled": True}],
        "capabilities": {"vault_available": True},
    }
    value.update(overrides)
    return value


class LibrarianHarnessTests(unittest.TestCase):
    def test_semantic_contract_is_compact_and_strict(self):
        schema = semantic_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertNotIn("tools", schema["properties"])
        self.assertEqual(validate_interpretation(semantic())["confidence"], 0.82)

    def test_existing_semantic_call_selects_only_relevant_approved_memories(self):
        memory_id = "a" * 32
        candidate = {"memory_id": memory_id, "scope": "general",
                     "lesson": "Engage with personal observations thoughtfully; avoid unsupported generalisations."}
        turn = {"types": ["question"], "subject": "workplace changes", "objective": "acknowledge the observation",
                "search_query": "", "response_objective": "answer", "changes": []}
        raw = semantic(turn=turn, relevant_behavioral_memory_ids=[memory_id])
        response = {"message": {"content": json.dumps(raw)}}
        with patch("librarian_harness._post_json", return_value=response) as post, \
             patch("librarian_harness.host_ollama_residency", return_value=[]):
            result = interpret_request("My team used to do handovers on one page; now it takes ten forms.",
                                       context(approved_behavioral_memories=[candidate]),
                                       endpoint="http://localhost:11434", model="qwen3.5:9b")
        schema = post.call_args.args[1]["format"]
        self.assertEqual(schema, semantic_schema([memory_id]))
        self.assertEqual(result["semantic"]["relevant_behavioral_memory_ids"], [memory_id])
        resolved = resolve_policy(result["semantic"], context())
        self.assertEqual(resolved["plan"]["relevant_behavioral_memory_ids"], [memory_id])

        unrelated_raw = semantic(turn=turn, relevant_behavioral_memory_ids=[])
        with patch("librarian_harness._post_json", return_value={"message": {"content": json.dumps(unrelated_raw)}}), \
             patch("librarian_harness.host_ollama_residency", return_value=[]):
            unrelated = interpret_request("How do I inspect a router's DHCP lease table?",
                                          context(approved_behavioral_memories=[candidate]),
                                          endpoint="http://localhost:11434", model="qwen3.5:9b")
        self.assertEqual(unrelated["semantic"]["relevant_behavioral_memory_ids"], [])
        with self.assertRaisesRegex(ValueError, "unavailable behavioral memory"):
            validate_interpretation(semantic(turn=turn, relevant_behavioral_memory_ids=["b" * 32]), [memory_id])

    def test_semantic_feedback_pattern_proposal_is_bounded_and_provenance_checked(self):
        evidence_ids = [f"{index:032x}:{index + 100:032x}" for index in range(1, 5)]
        responses = [{"evidence_id": evidence_id, "ratings": ["insightful", "overreaching"],
                      "comment": "Useful point, but do not overstate it."} for evidence_id in evidence_ids]
        lesson = "Preserve useful insights while keeping conclusions within the evidence."
        proposal = {"lesson": lesson, "scope": "response style", "evidence_ids": evidence_ids[:3]}
        raw = semantic(behavioral_learning_candidate=proposal)
        with patch("librarian_harness._post_json", return_value={"message": {"content": json.dumps(raw)}}) as post, \
             patch("librarian_harness.host_ollama_residency", return_value=[]):
            result = interpret_request("How should the answer be framed?",
                context(response_feedback_evidence={"counts": {"insightful": 4, "overreaching": 4}, "responses": responses}),
                endpoint="http://localhost:11434", model="qwen3.5:9b")
        self.assertEqual(result["semantic"]["behavioral_learning_candidate"], proposal)
        self.assertIn("at least three distinct responses", post.call_args.args[1]["messages"][0]["content"])
        self.assertEqual(post.call_args.args[1]["format"], semantic_schema(None, evidence_ids))
        with self.assertRaisesRegex(ValueError, "without valid provenance"):
            validate_interpretation(
                semantic(behavioral_learning_candidate={**proposal, "evidence_ids": ["x", *evidence_ids[:2]]}),
                available_feedback_evidence_ids=evidence_ids,
            )

    def test_home_planner_saves_semantic_feedback_proposal_as_pending_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original_store = server.HOME_CHAT_STORE
            original_events = server.HOME_EVENTS_PATH
            original_stream = server.LIBRARIAN_EVENT_STREAM
            store = ChatStore(root / "Vault")
            server.HOME_CHAT_STORE = store
            server.HOME_EVENTS_PATH = root / "Journal" / "events.md"
            server.LIBRARIAN_EVENT_STREAM = LibrarianEventStream(root / "events.jsonl")
            for index in range(3):
                chat = store.create()
                turn_id, started = store.begin_turn(chat["chat_id"], f"Request {index}", "qwen3.5:9b", {})
                assistant = next(item for item in started["messages"] if item["role"] == "assistant")
                store.complete_turn(chat["chat_id"], turn_id, f"Response {index}", model="qwen3.5:9b",
                                    used_vault=False, sources=[], retrieval={}, timing={}, identity_kernel={})
                store.set_feedback_tags(chat["chat_id"], assistant["message_id"], ["insightful", "overreaching"])
            evidence = store.response_feedback_evidence(12)["responses"]
            evidence_ids = [item["evidence_id"] for item in evidence]
            proposal = {"lesson": "Retain useful insights while keeping conclusions within the evidence.",
                        "scope": "response style", "evidence_ids": evidence_ids}

            def interpret(_query, runtime_context, **_kwargs):
                self.assertEqual(len(runtime_context["response_feedback_evidence"]["responses"]), 3)
                return {"semantic": semantic(behavioral_learning_candidate=proposal), "policy": {},
                        "telemetry": {}, "plan": {}}

            try:
                with patch.object(server, "home_planner_context", return_value={
                         "conversation_state": {}, "world_state": {}, "identity_guidance": ""}), \
                     patch.object(server.TOOL_REGISTRY, "discover", return_value=[]), \
                     patch.object(server, "model_activity", side_effect=lambda *a, **k: nullcontext()), \
                     patch.object(server, "interpret_and_resolve", side_effect=interpret):
                    result = server.home_planner_request("A new request", [], [], "auto", set(), session_id=None)
                    candidate = result["semantic"]["behavioral_learning_candidate"]
                    self.assertEqual(candidate["status"], "candidate")
                    self.assertEqual(candidate["evidence_count"], 3)
                    again = server.home_planner_request("Another request", [], [], "auto", set(), session_id=None)
                    self.assertEqual(again["semantic"]["behavioral_learning_candidate"]["memory_id"], candidate["memory_id"])
                    self.assertEqual(len(store.interaction_memories()), 1)
                    self.assertEqual(store.interaction_memories()[0]["status"], "candidate")
            finally:
                server.HOME_CHAT_STORE = original_store
                server.HOME_EVENTS_PATH = original_events
                server.LIBRARIAN_EVENT_STREAM = original_stream

    def test_vault_modes_are_controller_overrides(self):
        history = semantic(needs_personal_history=True)
        never = resolve_policy(history, context(active_knowledge_source="never"))
        self.assertFalse(never["plan"]["use_vault"])
        self.assertIn("vault_never", never["policy_overrides"])
        always = resolve_policy(semantic(), context(active_knowledge_source="always"))
        self.assertTrue(always["plan"]["use_vault"])
        self.assertIn("vault_always", always["policy_overrides"])

    def test_personal_world_reference_is_a_vault_floor(self):
        result = resolve_policy(
            semantic(),
            context(request="Give me some ideas for my main video channel."),
        )
        self.assertTrue(result["plan"]["use_vault"])
        self.assertEqual(result["plan"]["primary_source"], "vault")
        self.assertIn("personal_context_floor", result["policy_overrides"])

    def test_unavailable_current_capability_does_not_change_semantics(self):
        result = resolve_policy(
            semantic(needs_current_information=True),
            context(),
        )
        self.assertTrue(result["plan"]["needs_current_information"])
        self.assertEqual(result["plan"]["primary_source"], "external")
        self.assertEqual(result["plan"]["tools"], [])
        self.assertIn("current_source_unavailable", result["capability_gaps"])

    def test_all_sources_routes_current_information_to_live_search(self):
        result = resolve_policy(
            semantic(needs_current_information=True),
            context(
                active_knowledge_source="all",
                available_tools=[{"tool_id": "external-research", "enabled": True}],
                capabilities={"vault_available": True, "external_research_available": True, "external_research_allowed": True},
            ),
        )
        self.assertEqual(result["plan"]["tools"], ["external-research"])
        self.assertNotIn("current_source_unavailable", result["capability_gaps"])

    def test_local_only_is_an_explicit_live_search_restriction(self):
        result = resolve_policy(
            semantic(needs_current_information=True),
            context(
                active_knowledge_source="local",
                available_tools=[],
                capabilities={"vault_available": True, "external_research_available": True, "external_research_allowed": False},
            ),
        )
        self.assertEqual(result["plan"]["tools"], [])
        self.assertIn("current_source_restricted", result["capability_gaps"])

    def test_attachment_route_depends_on_runtime_state(self):
        attached = resolve_policy(
            semantic(needs_attachment=True),
            context(attachments=[{"filename": "article.md"}]),
        )
        self.assertEqual(attached["plan"]["primary_source"], "attachment")
        self.assertEqual(attached["plan"]["tools"], ["document-analysis"])
        missing = resolve_policy(semantic(needs_attachment=True), context())
        self.assertEqual(missing["plan"]["tools"], [])
        self.assertIn("attachment_missing", missing["capability_gaps"])

    def test_fallback_emits_error_and_preserves_legacy_route(self):
        with tempfile.TemporaryDirectory() as temporary:
            original_stream = server.LIBRARIAN_EVENT_STREAM
            server.LIBRARIAN_EVENT_STREAM = LibrarianEventStream(Path(temporary) / "events.jsonl")
            try:
                with patch.object(server, "interpret_and_resolve", side_effect=RuntimeError("test interpreter failure")):
                    result = server.home_planner_request(
                        "What have we discussed about local AI models?", [], [], "auto", set(),
                        request_id="req-1", session_id="chat-1",
                    )
            finally:
                stream = server.LIBRARIAN_EVENT_STREAM
                server.LIBRARIAN_EVENT_STREAM = original_stream
            self.assertTrue(result["fallback"])
            self.assertTrue(result["plan"]["use_vault"])
            events = stream.read_recent()
            self.assertEqual({event["event_type"] for event in events}, {"ERROR", "POLICY_RESOLUTION", "EXECUTION_PLAN"})
            errors = [event for event in events if event["event_type"] == "ERROR"]
            self.assertEqual(errors[0]["request_id"], "req-1")
            self.assertEqual(errors[0]["data"]["fallback"], True)


if __name__ == "__main__":
    unittest.main()
