from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from activity_state import ActivityStateStream  # noqa: E402
from home_chat_store import ChatStore  # noqa: E402
from librarian_events import LibrarianEventStream  # noqa: E402


class SourceTestMcp:
    def __init__(self):
        self.chat_calls = []
        self.retrieve_calls = []

    def identity_system_prefix(self):
        return "IDENTITY", {"id": "ariadne", "version": "1.1.0", "scope": "user"}

    def retrieve_evidence(self, arguments):
        self.retrieve_calls.append(arguments)
        return {"results": [], "candidate_count": 0, "selected_count": 0, "match_count": 0}

    def ollama_chat(self, messages, **kwargs):
        self.chat_calls.append(messages)
        if kwargs.get("metrics") is not None:
            kwargs["metrics"].setdefault("ollama_calls", []).append({
                "total_duration": 1_000_000,
                "prompt_eval_count": 10,
                "eval_count": 4,
                "eval_duration": 1_000_000,
                "done_reason": "stop",
            })
        return "I checked the current sources and found the reported coverage."


class SourceTestSearch:
    def __init__(self):
        self.calls = []

    def route(self):
        return object()

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return {
            "ok": True,
            "provider": "test-bing",
            "provider_metadata": {"provider_id": "test-bing"},
            "results": [{
                "title": "Bangkok Post flooding coverage",
                "url": "https://www.bangkokpost.com/thailand/general/test-floods",
                "source_id": "https://www.bangkokpost.com/thailand/general/test-floods",
                "snippet": "Current flooding update.",
                "content": "Current flooding update from the test provider.",
                "fetched": True,
            }],
        }


class NoopInteractionStream:
    def emit(self, *args, **kwargs):
        return None


class ImmediateExecutor:
    def submit(self, function, *args):
        function(*args)


class HomeSourceRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.originals = {
            "HOME_CHAT_STORE": server.HOME_CHAT_STORE,
            "HOME_ACTIVITY_STREAM": server.HOME_ACTIVITY_STREAM,
            "_home_mcp": server._home_mcp,
            "home_planner_request": server.home_planner_request,
            "SEARCH_PROVIDER_REGISTRY": server.SEARCH_PROVIDER_REGISTRY,
            "home_adaptive_context_for_query": server.home_adaptive_context_for_query,
            "record_home_event": server.record_home_event,
            "publish_home_activity": server.publish_home_activity,
            "record_model_residency_event": server.record_model_residency_event,
            "model_activity": server.model_activity,
            "LIBRARIAN_EVENT_STREAM": server.LIBRARIAN_EVENT_STREAM,
            "CORE_INTERACTION_STREAM": server.CORE_INTERACTION_STREAM,
        }
        self.mcp = SourceTestMcp()
        self.search = SourceTestSearch()
        self.avatar_activity = []
        server.HOME_ACTIVITY_STREAM = ActivityStateStream(
            emit_avatar_state=lambda state, status: self.avatar_activity.append((state, status)),
            executor=ImmediateExecutor(),
        )
        server.HOME_CHAT_STORE = ChatStore(root)
        server._home_mcp = lambda: self.mcp
        def planner(query, *args, **kwargs):
            current = "latest" in query.casefold()
            return {
                "plan": {"intent": "current_news" if current else "ordinary_conversation", "use_vault": False, "tools": [], "needs_current_information": current},
                "semantic": {
                    "intent": "current_news" if current else "ordinary_conversation", "needs_personal_history": False,
                    "needs_current_information": current, "needs_attachment": False,
                    "reasoning_complexity": "low", "ambiguity": "low", "confidence": 0.95,
                },
                "world_state": {}, "fallback": False, "telemetry": {},
            }
        server.home_planner_request = planner
        server.SEARCH_PROVIDER_REGISTRY = self.search
        server.home_adaptive_context_for_query = lambda *args, **kwargs: {}
        server.record_home_event = lambda *args, **kwargs: None
        server.publish_home_activity = lambda chat_id, state, message="": server.HOME_ACTIVITY_STREAM.publish(chat_id, state, message).as_dict()
        server.record_model_residency_event = lambda *args, **kwargs: None
        server.model_activity = lambda *args, **kwargs: nullcontext()
        server.LIBRARIAN_EVENT_STREAM = LibrarianEventStream(root / "librarian-events.jsonl")
        server.CORE_INTERACTION_STREAM = NoopInteractionStream()

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(server, name, value)
        self.temporary.cleanup()

    def test_existing_chat_can_enable_search_mid_conversation_and_prompt_gets_runtime_truth(self):
        chat = server.HOME_CHAT_STORE.create()
        chat_id = chat["chat_id"]

        # An existing conversation starts Search off in Conversation only mode.
        first = server.home_chat_payload(
            "Say hello.", [], "never", chat_id, [],
        )
        self.assertFalse(first["runtime_source_state"]["web_search_attempted"])
        self.assertEqual(self.search.calls, [])

        # Search is switched on for a later turn in the same conversation.
        query = "Check the latest Bangkok Post coverage of flooding in Thailand."
        second = server.home_chat_payload(
            query, [], "never", chat_id, ["external-research"],
        )
        self.assertEqual(len(self.search.calls), 1)
        self.assertEqual(self.search.calls[0][0], query)
        self.assertTrue(second["runtime_source_state"]["web_search_available"])
        self.assertTrue(second["runtime_source_state"]["web_search_selected"])
        self.assertTrue(second["runtime_source_state"]["web_search_attempted"])
        self.assertEqual(second["runtime_source_state"]["mode"], "never")
        self.assertEqual(second["runtime_source_state"]["web_search_provider"], "test-bing")
        self.assertEqual(second["runtime_source_state"]["web_search_result_count"], 1)
        self.assertIn("AUTHORITATIVE PER-TURN SOURCE STATE", self.mcp.chat_calls[-1][0]["content"])
        self.assertIn('"web_search_available":true', self.mcp.chat_calls[-1][0]["content"])

    def test_fresh_all_sources_turn_automatically_searches_for_current_information(self):
        chat = server.HOME_CHAT_STORE.create()
        query = "Check the latest Bangkok Post coverage of flooding in Thailand."
        events = []
        result = server.home_chat_payload(query, [], "all", chat["chat_id"], [], on_event=events.append)
        self.assertEqual(len(self.search.calls), 1)
        self.assertTrue(result["runtime_source_state"]["web_search_automatic"])
        self.assertTrue(result["runtime_source_state"]["web_search_attempted"])
        self.assertEqual(result["runtime_source_state"]["mode"], "all")
        activity_events = [event["activity"] for event in events if event.get("type") == "activity"]
        self.assertTrue(any(item["message"] == "Searching web" for item in activity_events))
        self.assertTrue(any(item["message"] == "Reading sources" for item in activity_events))
        self.assertEqual(activity_events[-1]["state"], "complete")
        self.assertEqual(activity_events[-1]["label"], "Idle")
        self.assertEqual(server.HOME_ACTIVITY_STREAM.current_snapshot().as_dict(), activity_events[-1])
        self.assertEqual(server.home_activity_state_payload(chat["chat_id"])["activity"], activity_events[-1])
        self.assertEqual(self.avatar_activity[-1], ("idle", "Idle"))


if __name__ == "__main__":
    unittest.main()
