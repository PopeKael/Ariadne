from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

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
            "DOCUMENT_WORK_ROOT": server.DOCUMENT_WORK_ROOT,
        }
        self.mcp = SourceTestMcp()
        self.search = SourceTestSearch()
        self.avatar_activity = []
        server.HOME_ACTIVITY_STREAM = ActivityStateStream(
            emit_avatar_state=lambda state, status: self.avatar_activity.append((state, status)),
            executor=ImmediateExecutor(),
        )
        server.HOME_CHAT_STORE = ChatStore(root)
        server.DOCUMENT_WORK_ROOT = root / "document_contexts"
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

    def test_web_first_skips_generic_vault_and_measures_generation(self):
        chat = server.HOME_CHAT_STORE.create()
        result = server.home_chat_payload("Check the latest flooding coverage.", [], "all", chat["chat_id"])
        self.assertEqual(self.mcp.retrieve_calls, [])
        metrics = result["timing"]["orchestration"]
        self.assertEqual(metrics["route"], "web_first")
        self.assertFalse(metrics["vault"]["used"])
        self.assertTrue(metrics["web"]["used"])
        self.assertGreaterEqual(metrics["before_generation_ms"], 0)
        self.assertGreater(metrics["context"]["total"]["characters"], 0)
        self.assertEqual(metrics["personality_percentage"], 60)
        stored = server.HOME_CHAT_STORE.get(chat["chat_id"])["messages"][-1]
        self.assertEqual(stored["timing"]["orchestration"], metrics)

    def test_optional_personal_vault_runs_after_web(self):
        chat = server.HOME_CHAT_STORE.create()
        order = []
        original_search = self.search.search
        self.search.search = lambda *a, **kw: (order.append("web"), original_search(*a, **kw))[1]
        original_vault = server._home_vault_retrieval
        with patch.object(server, "_home_vault_retrieval", side_effect=lambda *a, **kw: (order.append("vault"), original_vault(*a, **kw))[1]):
            server.home_chat_payload("Research the latest AI for my project.", [], "all", chat["chat_id"])
        self.assertEqual(order, ["web", "vault"])

    def test_focus_anchors_planning_search_and_generation(self):
        chat = server.HOME_CHAT_STORE.create()
        chat_id = chat["chat_id"]
        for number in range(5):
            server.home_chat_payload(f"Say hello {number}.", [], "never", chat_id)
        source = server.HOME_CHAT_STORE.get(chat_id)["messages"][-1]
        focus = {"text": "reported coverage", "source_turn_id": source["turn_id"]}
        with patch.object(server, "configuration_snapshot", return_value={"personality": {"intensity": {"normal": 37}}}):
            result = server.home_chat_payload("Research the latest details.", [], "all", chat_id, focus=focus)
        self.assertIn("reported", self.search.calls[-1][0])
        self.assertLessEqual(len(self.search.calls[-1][0]), 200)
        prompt = self.mcp.chat_calls[-1]
        content = prompt[-1]["content"]
        self.assertLess(content.index("Current Focus"), content.index("New instruction"))
        self.assertLess(content.index("New instruction"), content.index("Relevant evidence"))
        self.assertLess(content.index("Relevant evidence"), content.index("Recent conversation"))
        self.assertNotIn("Say hello 0", content)
        self.assertEqual(len(prompt), 2)
        self.assertIn("personality intensity 37%", prompt[0]["content"])
        self.assertEqual(result["timing"]["orchestration"]["context"]["older_context"]["characters"], 0)
        other = server.HOME_CHAT_STORE.create()
        with self.assertRaises(ValueError):
            server.home_chat_payload("Explain", [], "never", other["chat_id"], focus=focus)
        with self.assertRaises(ValueError):
            server.home_chat_payload("Explain", [], "never", chat_id, focus={**focus, "text": "invented passage"})

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

    def test_pow_article_followup_preserves_documents_and_enriches_search(self):
        fixture = json.loads((ROOT / "test-fixtures" /
            "article-followup-08e15e7d1a4748ecbd8fa930c913d87c.json").read_text(encoding="utf-8"))
        chat_id = fixture["chat_id"]
        workspace = fixture["document_workspace"]
        article = workspace["documents"][0]
        planner_inputs = []

        def planner(query, history, attachments, *args, **kwargs):
            planner_inputs.append((query, history, attachments))
            return fixture["planner_result"]

        server.home_planner_request = planner
        external_evidence = "Mock external coverage: the South Korea/Ukraine POW dispute remains unresolved."
        original_search = self.search.search

        def search(query, **kwargs):
            result = original_search(query, **kwargs)
            result["results"][0].update({
                "title": "Related POW reporting (mock)",
                "content": external_evidence,
                "snippet": external_evidence,
            })
            return result

        self.search.search = search
        # Replay the saved pre-follow-up context in temporary storage, including
        # the planner's incorrect needs_attachment=false. Never mutate the real chat.
        for selected_tools in ([], ["external-research"]):
            with self.subTest(selected_tools=selected_tools):
                record = server.HOME_CHAT_STORE.create()
                old_path = server.HOME_CHAT_STORE.root / (record["chat_id"] + ".json")
                old_path.unlink()
                record.update(chat_id=chat_id, messages=fixture["messages"])
                (server.HOME_CHAT_STORE.root / (chat_id + ".json")).write_text(
                    json.dumps(record), encoding="utf-8")
                server.DOCUMENT_WORK_ROOT.mkdir(exist_ok=True)
                (server.DOCUMENT_WORK_ROOT / (chat_id + ".json")).write_text(
                    json.dumps(workspace), encoding="utf-8")
                with patch.object(server.PLUGIN_REGISTRY, "providers_for", return_value=[]):
                    result = server.home_chat_payload(fixture["followup"], [], "all", chat_id, selected_tools)

                self.assertFalse(fixture["planner_result"]["semantic"]["needs_attachment"])
                self.assertEqual(planner_inputs[-1][1][-1]["content"], fixture["messages"][1]["content"])
                self.assertEqual(planner_inputs[-1][2][0]["document_id"], article["document_id"])
                self.assertTrue(result["used_documents"])
                self.assertEqual(result["runtime_source_state"]["attachment_chunks_used"], 6)
                self.assertEqual({chunk["document_id"] for chunk in result["document_analysis"]["chunks"]},
                                 {article["document_id"]})
                query = self.search.calls[-1][0]
                self.assertNotEqual(query, fixture["followup"])
                for subject in ("South Korea", "Ukraine", "North Korean", "POW"):
                    self.assertIn(subject, query)
                self.assertLessEqual(len(query), 200)
                self.assertLessEqual(len(query.split()), 30)
                self.assertIn("latest developments", query)
                self.assertIn("background", query)
                self.assertNotIn(fixture["followup"], query)
                self.assertEqual(self.search.calls[-1][1], {"limit": 5, "fetch_limit": 3})
                # Preserve the diagnosed Vault route and inspect the actual final
                # generation boundary, rather than merely asserting retrieval flags.
                self.assertTrue(result["used_vault"])
                self.assertTrue(self.mcp.retrieve_calls)
                final_prompt = self.mcp.chat_calls[-1][-1]["content"]
                self.assertIn("Temporary document evidence", final_prompt)
                self.assertIn("South Korea's President Lee Jae Myung", final_prompt)
                self.assertIn("Live source evidence", final_prompt)
                self.assertIn(external_evidence, final_prompt)

    def test_article_reference_guard_leaves_unrelated_or_ambiguous_queries_unchanged(self):
        fixture = json.loads((ROOT / "test-fixtures" /
            "article-followup-08e15e7d1a4748ecbd8fa930c913d87c.json").read_text(encoding="utf-8"))
        article = fixture["document_workspace"]["documents"][0]
        unrelated = "Check the latest Bangkok Post flooding coverage."
        self.assertIsNone(server._referenced_attached_article(unrelated, [article]))
        self.assertEqual(server._article_followup_search_query(unrelated, None, {}), unrelated)
        other = {**article, "document_id": "other", "metadata": {
            **article["metadata"], "article_id": "article-" + "0" * 28, "title": "Other story"}}
        self.assertIsNone(server._referenced_attached_article(fixture["followup"], [article, other]))
        self.assertIs(server._referenced_attached_article(
            "Search related information about this article: " + article["metadata"]["title"], [article, other]), article)

    def test_pow_zero_search_does_not_recycle_stale_assistant_claims(self):
        fixture = json.loads((ROOT / "test-fixtures" /
            "article-followup-08e15e7d1a4748ecbd8fa930c913d87c.json").read_text(encoding="utf-8"))
        stale = ("Article analysis still matters.\n\n**Web search context:**\n"
                 "The live web search results ([Live Source 1]–[5]) focus on Seoul's geography, "
                 "tourism, and government services rather than the current diplomatic dispute.")
        chat_id = fixture["chat_id"]
        record = server.HOME_CHAT_STORE.create()
        (server.HOME_CHAT_STORE.root / (record["chat_id"] + ".json")).unlink()
        messages = [dict(message) for message in fixture["messages"]]
        messages[-1]["content"] = stale
        record.update(chat_id=chat_id, messages=messages)
        (server.HOME_CHAT_STORE.root / (chat_id + ".json")).write_text(json.dumps(record), encoding="utf-8")
        server.DOCUMENT_WORK_ROOT.mkdir(exist_ok=True)
        (server.DOCUMENT_WORK_ROOT / (chat_id + ".json")).write_text(
            json.dumps(fixture["document_workspace"]), encoding="utf-8")
        planner_history = []
        def planner(query, history, *args, **kwargs):
            planner_history.extend(history)
            return fixture["planner_result"]
        server.home_planner_request = planner
        diagnostics = [{"provider_id": "searxng", "http_status": 200,
                        "response_classification": "html_no_results", "parsed_candidate_count": 0,
                        "accepted_result_count": 0, "rejections": []}]
        self.search.search = lambda *args, **kwargs: {
            "ok": False, "provider": "searxng", "results": [],
            "error": "searxng: no usable results", "attempts": diagnostics}
        # Simulate a model repeating the known stale paragraph despite the prompt.
        def model(messages, **kwargs):
            self.mcp.chat_calls.append(messages)
            if kwargs.get("on_delta"):
                kwargs["on_delta"](stale)
            return stale
        self.mcp.ollama_chat = model
        events = []
        with patch.object(server.PLUGIN_REGISTRY, "providers_for", return_value=[]):
            result = server.home_chat_payload(fixture["followup"], [], "all", chat_id, [], on_event=events.append)
        prompt = self.mcp.chat_calls[-1]
        self.assertIn(server._NO_CURRENT_LIVE_SOURCES, prompt[0]["content"])
        self.assertIn("Article analysis still matters.", prompt[2]["content"])
        self.assertNotIn("tourism", "\n".join(m["content"] for m in prompt))
        self.assertNotIn("[Live Source 1]", "\n".join(m["content"] for m in prompt))
        self.assertEqual(planner_history[-1]["content"], stale)
        self.assertEqual(result["runtime_source_state"]["attachment_chunks_used"], 6)
        self.assertEqual(result["runtime_source_state"]["web_search_result_count"], 0)
        self.assertEqual(result["answer"], server._NO_CURRENT_LIVE_SOURCES)
        self.assertEqual(result["retrieval"]["live_search"]["attempts"], diagnostics)
        stored = json.loads((server.HOME_CHAT_STORE.root / (chat_id + ".json")).read_text(encoding="utf-8"))
        self.assertEqual(stored["messages"][1]["content"], stale)
        self.assertEqual(stored["messages"][-1]["content"], result["answer"])
        self.assertEqual(stored["messages"][-1]["retrieval"]["live_search"]["attempts"], diagnostics)
        self.assertFalse(any("tourism" in event.get("text", "") or "[Live Source 1]" in event.get("text", "") for event in events))

    def test_zero_source_output_guard_covers_citation_variants_and_no_search(self):
        for citation in ("[Live Source 1]", "[live source 12]", "[ Live Source 3 ]", "[Live Sources 1–5]"):
            with self.subTest(citation=citation):
                unsafe = "Earlier results prove this. " + citation
                self.assertNotRegex(server._home_current_source_answer(unsafe, 0, True), server._LIVE_SOURCE_CITATION)
                self.assertNotRegex(server._home_current_source_answer(unsafe, 0, False), server._LIVE_SOURCE_CITATION)
                self.assertEqual(server._home_current_source_answer(unsafe, 1, True), unsafe)
        safe = "From the attached article, further action remains unspecified."
        self.assertEqual(server._home_current_source_answer(safe, 0, True), safe)

    def test_no_search_cannot_emit_citations_but_current_sources_still_stream(self):
        unsafe = "The supplied live source reports flooding. [Live Source 1]"
        def model(messages, **kwargs):
            self.mcp.chat_calls.append(messages)
            if kwargs.get("on_delta"):
                kwargs["on_delta"](unsafe)
            return unsafe
        self.mcp.ollama_chat = model
        for query, tools, has_sources in (("Say hello.", [], False),
                ("Check the latest flooding coverage.", ["external-research"], True)):
            with self.subTest(has_sources=has_sources):
                chat = server.HOME_CHAT_STORE.create()
                events = []
                result = server.home_chat_payload(query, [], "never", chat["chat_id"], tools, on_event=events.append)
                deltas = "".join(e.get("text", "") for e in events if e.get("type") == "delta")
                if has_sources:
                    self.assertEqual(result["answer"], unsafe)
                    self.assertEqual(deltas, unsafe)
                else:
                    self.assertFalse(result["runtime_source_state"]["web_search_attempted"])
                    self.assertNotRegex(result["answer"], server._LIVE_SOURCE_CITATION)
                    self.assertNotRegex(deltas, server._LIVE_SOURCE_CITATION)
                    self.assertNotIn("Search was attempted", result["answer"])

    def test_pow_provider_validation_controls_final_model_context(self):
        from search_providers import SearchProviderRegistry, default_search_providers

        fixture = json.loads((ROOT / "test-fixtures" /
            "article-followup-08e15e7d1a4748ecbd8fa930c913d87c.json").read_text(encoding="utf-8"))
        chat_id = fixture["chat_id"]
        server.home_planner_request = lambda *args, **kwargs: fixture["planner_result"]
        for relevant_available in (True, False):
            with self.subTest(relevant_available=relevant_available):
                record = server.HOME_CHAT_STORE.create()
                (server.HOME_CHAT_STORE.root / (record["chat_id"] + ".json")).unlink()
                record.update(chat_id=chat_id, messages=fixture["messages"])
                (server.HOME_CHAT_STORE.root / (chat_id + ".json")).write_text(json.dumps(record), encoding="utf-8")
                server.DOCUMENT_WORK_ROOT.mkdir(exist_ok=True)
                (server.DOCUMENT_WORK_ROOT / (chat_id + ".json")).write_text(
                    json.dumps(fixture["document_workspace"]), encoding="utf-8")
                server.SEARCH_PROVIDER_REGISTRY = SearchProviderRegistry(default_search_providers())

                def request(url, **kwargs):
                    if "search?" in url or "w/api.php?" in url:
                        results = fixture["irrelevant_search_results"]
                        if relevant_available and url.startswith("http://192.168.1.200"):
                            results = [*results, *fixture["relevant_search_results"]]
                        return json.dumps({"results": results}).encode(), "utf-8"
                    return b"<html><body>Related POW reporting.</body></html>", "utf-8"

                with patch("search_providers._request", side_effect=request), \
                        patch.object(server.PLUGIN_REGISTRY, "providers_for", return_value=[]):
                    result = server.home_chat_payload(fixture["followup"], [], "all", chat_id, [])
                prompt = self.mcp.chat_calls[-1][-1]["content"]
                self.assertEqual(result["runtime_source_state"]["attachment_chunks_used"], 6)
                self.assertTrue(result["used_vault"])
                for item in fixture["irrelevant_search_results"]:
                    self.assertNotIn(item["url"], prompt)
                    self.assertNotIn(item["title"], prompt)
                if relevant_available:
                    self.assertEqual(result["runtime_source_state"]["web_search_provider"], "searxng")
                    self.assertEqual(result["runtime_source_state"]["web_search_result_count"], 3)
                    self.assertIn(fixture["relevant_search_results"][0]["title"], prompt)
                else:
                    self.assertEqual(result["runtime_source_state"]["web_search_result_count"], 0)
                    self.assertIn("Live search returned no usable sources", prompt)


if __name__ == "__main__":
    unittest.main()
