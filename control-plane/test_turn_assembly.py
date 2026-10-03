"""Synthetic state and real Home controller regressions; no personal fixtures."""
import copy
import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import server
from conversation_orchestration import (assemble_turn, assembled_history, history_candidates,
    generation_messages, relevant_state, turn_context, validate_turn)
from librarian_harness import interpret_request, request_needs_personal_context, resolve_policy, validate_interpretation
import test_home_source_routing as routing_tests


def turn(types=None, subject="VoiceWorks support relationship", objective="Clarify the relationship", changes=None, response="answer"):
    return {"types": types or ["question"], "subject": subject, "objective": objective,
            "response_objective": response, "changes": changes or []}


def semantic(job=None, **overrides):
    result = {"intent": "conversation", "needs_personal_history": False,
        "needs_current_information": False, "needs_attachment": False,
        "reasoning_complexity": "low", "ambiguity": "low", "confidence": .95}
    if job is not None:
        result["turn"] = job
    return {**result, **overrides}


OLD_CLAIM = "Morgan is a valued partner to VoiceWorks engineering."
CORRECTION = ("I've identified myself with their support team and we communicate by email. "
              "Whether I'm really inside the fold is from their perspective. I'm just here to help.")


def previous_record():
    return {"messages": [{"role": "assistant", "turn_id": "previous", "state": "complete", "content": OLD_CLAIM}]}


def correction_turn(record):
    return turn(["correction", "refinement"], changes=[
        {"kind": "user_fact", "quote": CORRECTION.split(" Whether")[0],
         "supersedes": [history_candidates(record)[0]["id"]]},
        {"kind": "uncertainty", "quote": "Whether I'm really inside the fold is from their perspective.", "supersedes": []},
    ], response="acknowledge_update")


class TurnAssemblyTests(unittest.TestCase):
    def test_correction_supersedes_assistant_inference_before_generation(self):
        record = previous_record()
        job = assemble_turn(semantic(correction_turn(record)), CORRECTION, record, "new")
        self.assertTrue(job["correction"])
        self.assertTrue(job["supersession"])
        context = turn_context(job)
        self.assertNotIn(OLD_CLAIM, context)
        self.assertIn("support team", context)
        self.assertIn("uncertainties", context)
        self.assertEqual(job["response_objective"], "acknowledge_update")
        history = assembled_history([{"role": "assistant", "content": OLD_CLAIM}], job)
        self.assertEqual(history, [])
        self.assertEqual(record["messages"][0]["content"], OLD_CLAIM)

    def test_only_grounded_user_quotes_enter_state(self):
        job = assemble_turn(semantic(turn(changes=[
            {"kind": "user_fact", "quote": "Morgan is their engineering partner.", "supersedes": []}
        ])), CORRECTION, previous_record(), "new")
        self.assertEqual(job["state"]["items"], [])
        self.assertIn("ungrounded_change", job["rejected_changes"])

    def test_unknown_reference_cannot_supersede_state(self):
        job = assemble_turn(semantic(turn(["correction"], changes=[
            {"kind": "user_fact", "quote": CORRECTION[:75], "supersedes": ["invented-id"]}
        ])), CORRECTION, previous_record(), "new")
        self.assertIn("invalid_supersession_reference", job["rejected_changes"])
        self.assertNotIn("invented-id", json.dumps(job["state"]))
        self.assertIn("conservative_conversation_supersession", job["controller_fallbacks"])

    def test_grounded_acknowledgement_quarantines_unreferenced_assistant_claim(self):
        value = turn(["clarification"], response="acknowledge_update", changes=[
            {"kind": "user_fact", "quote": CORRECTION[:75], "supersedes": []}])
        job = assemble_turn(semantic(value), CORRECTION, previous_record(), "new")
        self.assertTrue(job["correction"])
        self.assertTrue(job["supersession"])
        self.assertIn("correction", job["types"])

    def test_malformed_user_quote_cannot_become_a_fact(self):
        text = "Explain a hash table, splork @@%%, and collisions."
        job = assemble_turn(semantic(turn(changes=[
            {"kind": "user_fact", "quote": "splork @@%%", "supersedes": []}])), text, {}, "new")
        self.assertEqual(job["state"]["items"], [])
        self.assertIn("unusable_source_span", job["rejected_changes"])

    def test_acknowledgement_uses_current_state_without_overriding_forced_vault(self):
        value = semantic(turn(["clarification"], response="acknowledge_update", changes=[
            {"kind": "user_fact", "quote": CORRECTION[:75], "supersedes": []}]), needs_personal_history=True)
        runtime = {"request": CORRECTION, "conversation_state": {"recent_messages": [{}]},
                   "capabilities": {"vault_available": True}}
        policy = resolve_policy(value, runtime)
        self.assertFalse(policy["plan"]["use_vault"])
        self.assertTrue(policy["conversation_update_sufficient"])
        self.assertTrue(resolve_policy(value, {**runtime, "active_knowledge_source": "always"})["plan"]["use_vault"])

    def test_correction_survives_next_turn_and_model_change(self):
        record = previous_record()
        job = assemble_turn(semantic(correction_turn(record)), CORRECTION, record, "new")
        record["conversation_state"] = job["state"]
        next_job = assemble_turn(semantic(turn(["continuation"])), "What next?", record, "next")
        self.assertEqual(assembled_history([{"role": "assistant", "content": OLD_CLAIM}], next_job), [])
        self.assertIn("support team", turn_context(next_job))
        # State and prompt construction have no model field or provider API.
        self.assertNotIn("model", next_job["state"])

    def test_older_unrelated_state_is_not_prefill(self):
        record = previous_record()
        job = assemble_turn(semantic(correction_turn(record)), CORRECTION, record, "new")
        record["conversation_state"] = job["state"]
        unrelated = assemble_turn(semantic(turn(subject="integer arithmetic")), "What is 2 + 2?", record, "next")
        self.assertEqual(relevant_state(unrelated), [])
        self.assertNotIn("support team", turn_context(unrelated))

    def test_history_is_bounded_without_editing_chronology(self):
        job = assemble_turn(semantic(turn()), "Hello", {}, "new")
        history = [{"role": "user", "content": "x" * 4000} for _ in range(20)]
        original = copy.deepcopy(history)
        self.assertLessEqual(sum(len(m["content"]) for m in assembled_history(history, job)), 8000)
        self.assertEqual(history, original)

    def test_focus_stays_first_and_state_cannot_replace_instruction(self):
        job = assemble_turn(semantic(turn()), "Explain", {}, "new")
        messages = generation_messages([{ "role": "system", "content": "Identity"},
            {"role": "user", "content": "Evidence"}], {"text": "Selected text", "source_turn_id": "one"},
            "Explain", "normal", 60, job)
        content = messages[-1]["content"]
        self.assertLess(content.index("Selected text"), content.index("New instruction"))
        self.assertLess(content.index("New instruction"), content.index("Assembled conversation"))

    def test_legacy_and_malformed_interpretations_are_distinct(self):
        self.assertNotIn("turn", validate_interpretation(semantic()))
        with self.assertRaises(ValueError):
            validate_turn({**turn(), "response_objective": "invent_fact"})
        with self.assertRaises(ValueError):
            validate_interpretation(semantic({"types": ["question"]}))
        self.assertEqual(validate_turn({**turn(), "search_query": "support email relationship"})["search_query"], "support email relationship")
        for invalid in (None, 42, "x" * 201):
            with self.subTest(search_query=invalid), self.assertRaises(ValueError):
                validate_turn({**turn(), "search_query": invalid})

    def test_generic_questions_no_longer_hit_personal_policy_floor(self):
        for query in ("What is a hash table?", "Where does a compiler store symbols?", "Write a Python sorting function."):
            self.assertFalse(request_needs_personal_context(query), query)
        self.assertTrue(request_needs_personal_context("What did we decide last month?"))

    def test_instruction_to_create_an_artifact_compiles_to_perform_task(self):
        job = assemble_turn(semantic(turn(["instruction"])), "Write a Python sorting function.", {}, "new")
        self.assertEqual(job["response_objective"], "perform_task")
        explanation = assemble_turn(semantic(turn(["instruction"])), "Explain a hash table.", {}, "next")
        self.assertEqual(explanation["response_objective"], "answer")

    def test_interpreter_uses_one_call_with_expanded_schema(self):
        with patch("librarian_harness._get_json", return_value={}), patch("librarian_harness._post_json",
            return_value={"message": {"content": json.dumps(semantic(turn()))}}) as post:
            result = interpret_request("Hello", {}, endpoint="http://localhost:11434")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(result["semantic"]["turn"]["types"], ["question"])
        self.assertIn("turn", post.call_args.args[1]["format"]["required"])
        self.assertIn("search_query", post.call_args.args[1]["format"]["properties"]["turn"]["required"])


class HomeTurnAssemblyTests(unittest.TestCase):
    """Reuse the existing real persistence and controller boundary harness."""
    setUp = routing_tests.HomeSourceRoutingTests.setUp
    tearDown = routing_tests.HomeSourceRoutingTests.tearDown
    def install_semantic(self, value):
        def planner(*args, **kwargs):
            policy = resolve_policy(value, {"request": args[0], "attachments": [],
                "available_tools": [], "capabilities": {"vault_available": True},
                "conversation_state": {"recent_messages": args[1]},
                "active_knowledge_source": "all"})
            return {"semantic": value, "plan": policy["plan"], "policy": policy,
                    "fallback": False, "telemetry": {}, "world_state": {}}
        server.home_planner_request = planner

    def test_six_semantic_cases_have_persisted_recipe_and_answer(self):
        cases = [
            ("generic", "What is a hash table?", turn(subject="hash tables"), {}, False, "answer"),
            ("history", "What did we decide about VoiceWorks last month?", turn(), {"needs_personal_history": True}, True, "answer"),
            ("idea", "I'm thinking we could use a small cache between those layers.", turn(["refinement", "exploration"], subject="cache architecture", response="advance_idea"), {}, False, "advance_idea"),
            ("code", "Write a Python function to sort these values.", turn(["artifact", "instruction"], subject="Python sorting", response="perform_task"), {}, False, "perform_task"),
            ("stt", "Explain a hash table, splork @@%%, and collision handling.", turn(subject="hash tables", changes=[]), {"ambiguity": "medium"}, False, "answer"),
        ]
        for label, query, interpreted, flags, vault, objective in cases:
            with self.subTest(case=label):
                self.install_semantic(semantic(interpreted, **flags))
                chat = server.HOME_CHAT_STORE.create()
                result = server.home_chat_payload(query, [], "all", chat["chat_id"])
                recipe = result["timing"]["context_recipe"]
                stored = server.HOME_CHAT_STORE.get(chat["chat_id"])["messages"][-1]
                self.assertEqual(recipe, stored["context_recipe"])
                self.assertEqual(recipe["vault"]["used"], vault)
                self.assertEqual(recipe["response_objective"], objective)
                self.assertTrue(recipe["context_blocks"]["total"]["characters"])
                self.assertTrue(result["answer"])
                self.assertNotIn("splork", json.dumps(stored["turn_assembly"]["state"]))
        chat = server.HOME_CHAT_STORE.create()
        old_id, _ = server.HOME_CHAT_STORE.begin_turn(chat["chat_id"], "Tell me about that relationship.", "old-model", {})
        server.HOME_CHAT_STORE.complete_turn(chat["chat_id"], old_id, OLD_CLAIM, model="old-model",
            used_vault=False, sources=[], retrieval={}, timing={}, identity_kernel={})
        record = server.HOME_CHAT_STORE.get(chat["chat_id"])
        self.install_semantic(semantic(correction_turn(record), needs_personal_history=True))
        result = server.home_chat_payload(CORRECTION, [], "all", chat["chat_id"])
        recipe = result["timing"]["context_recipe"]
        self.assertTrue(recipe["supersession"])
        self.assertEqual(recipe["generation"]["status"], "complete")
        self.assertFalse(recipe["vault"]["used"])
        self.assertNotIn(OLD_CLAIM, json.dumps(self.mcp.chat_calls[-1]))
        self.assertIn("support team", json.dumps(self.mcp.chat_calls[-1]))

    def test_model_failure_keeps_recipe_and_updated_state(self):
        self.install_semantic(semantic(turn()))
        chat = server.HOME_CHAT_STORE.create()
        with patch.object(self.mcp, "ollama_chat", side_effect=RuntimeError("synthetic transport failure")):
            with self.assertRaises(RuntimeError):
                server.home_chat_payload("Say hello.", [], "never", chat["chat_id"])
        message = server.HOME_CHAT_STORE.get(chat["chat_id"])["messages"][-1]
        self.assertEqual(message["context_recipe"]["generation"]["status"], "failed")
        self.assertEqual(message["state"], "interrupted")

    def test_malformed_model_response_is_not_a_reasoning_result(self):
        self.install_semantic(semantic(turn()))
        chat = server.HOME_CHAT_STORE.create()
        with patch.object(self.mcp, "ollama_chat", return_value=""):
            with self.assertRaisesRegex(ValueError, "Malformed model"):
                server.home_chat_payload("Say hello.", [], "never", chat["chat_id"])
        receipt = server.HOME_CHAT_STORE.get(chat["chat_id"])["messages"][-1]["context_recipe"]
        self.assertEqual(receipt["generation"]["failure_stage"], "malformed_model_response")

    def test_diagnostic_endpoint_uses_exact_turn_and_validates_ids(self):
        chat = server.HOME_CHAT_STORE.create()
        result = server.home_chat_payload("Say hello.", [], "never", chat["chat_id"])
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://localhost:{httpd.server_address[1]}/api/core/context-recipe"
        try:
            with urllib.request.urlopen(base + f"?chat_id={chat['chat_id']}&turn_id={result['turn_id']}") as response:
                self.assertEqual(json.load(response)["recipe"]["turn_id"], result["turn_id"])
            for suffix, status in ((f"?chat_id={chat['chat_id']}&turn_id=missing", 404), ("?chat_id=..", 400)):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(base + suffix)
                self.assertEqual(caught.exception.code, status)
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
