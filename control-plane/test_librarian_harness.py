import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from librarian_events import LibrarianEventStream  # noqa: E402
from librarian_harness import resolve_policy, semantic_schema, validate_interpretation  # noqa: E402


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

    def test_vault_modes_are_controller_overrides(self):
        history = semantic(needs_personal_history=True)
        never = resolve_policy(history, context(active_knowledge_source="never"))
        self.assertFalse(never["plan"]["use_vault"])
        self.assertIn("vault_never", never["policy_overrides"])
        always = resolve_policy(semantic(), context(active_knowledge_source="always"))
        self.assertTrue(always["plan"]["use_vault"])
        self.assertIn("vault_always", always["policy_overrides"])

    def test_unavailable_current_capability_does_not_change_semantics(self):
        result = resolve_policy(
            semantic(needs_current_information=True),
            context(),
        )
        self.assertTrue(result["plan"]["needs_current_information"])
        self.assertEqual(result["plan"]["primary_source"], "external")
        self.assertEqual(result["plan"]["tools"], [])
        self.assertIn("current_source_unavailable", result["capability_gaps"])

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
