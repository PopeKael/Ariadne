import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from home_chat_store import ChatStore  # noqa: E402


class FakeGenerationMcp:
    def __init__(self, done_reason="stop", eval_count=1_500):
        self.done_reason = done_reason
        self.eval_count = eval_count
        self.kwargs = []
        self.messages = []

    def identity_system_prefix(self):
        return "IDENTITY", {"id": "ariadne", "version": "1.1.0", "scope": "user"}

    def ollama_chat(self, messages, **kwargs):
        self.kwargs.append(kwargs)
        self.messages.append(messages)
        kwargs["metrics"].setdefault("ollama_calls", []).append({
            "prompt_eval_count": 3_497,
            "eval_count": self.eval_count,
            "done_reason": self.done_reason,
        })
        return " ".join(f"token-{index}" for index in range(self.eval_count))


def fake_planner(*args, **kwargs):
    return {
        "plan": {"intent": "conversation", "tools": [], "use_vault": False, "primary_source": "conversation"},
        "world_state": {}, "fallback": False, "telemetry": {},
    }


class HomeGenerationTests(unittest.TestCase):
    def run_generation(self, done_reason):
        with tempfile.TemporaryDirectory() as temporary:
            original_store = server.HOME_CHAT_STORE
            original_mcp = server._home_mcp
            original_planner = server.home_planner_request
            server.HOME_CHAT_STORE = ChatStore(Path(temporary))
            server._home_mcp = lambda: fake
            server.home_planner_request = fake_planner
            fake = FakeGenerationMcp(done_reason=done_reason)
            try:
                chat = server.HOME_CHAT_STORE.create()
                result = server.home_chat_payload(
                    "Give me a long answer.", [], "never", chat["chat_id"], [],
                )
                return result, fake
            finally:
                server.HOME_CHAT_STORE = original_store
                server._home_mcp = original_mcp
                server.home_planner_request = original_planner

    def test_home_generation_budget_is_explicit_and_allows_more_than_1024_tokens(self):
        result, fake = self.run_generation("stop")
        self.assertGreater(result["timing"]["eval_count"], 1_024)
        self.assertEqual(fake.kwargs[0]["output_tokens"], server.HOME_OUTPUT_TOKENS)
        self.assertEqual(fake.kwargs[0]["context_tokens"], server.HOME_CONTEXT_TOKENS)
        self.assertEqual(result["generation_status"], "complete")
        self.assertFalse(result["generation_truncated"])

    def test_length_finish_reason_is_exposed_and_offers_continue(self):
        result, _ = self.run_generation("length")
        self.assertEqual(result["generation_status"], "truncated")
        self.assertTrue(result["generation_truncated"])
        self.assertTrue(result["continue_available"])
        self.assertEqual(result["timing"]["generation_finish_reason"], "length")

    def test_approved_memory_reaches_model_and_context_recipe(self):
        with tempfile.TemporaryDirectory() as temporary:
            original_store = server.HOME_CHAT_STORE
            original_mcp = server._home_mcp
            original_planner = server.home_planner_request
            original_hermes_enabled = server.hermes_pilot.enabled
            store = ChatStore(Path(temporary))
            server.HOME_CHAT_STORE = store
            fake = FakeGenerationMcp()
            server._home_mcp = lambda: fake
            server.home_planner_request = fake_planner
            server.hermes_pilot.enabled = lambda: False
            try:
                chat = store.create()
                source_turn, started = store.begin_turn(chat["chat_id"], "Airbnb reflection", "qwen3.5:9b-q4_K_M", {})
                source_assistant = next(item for item in started["messages"] if item["role"] == "assistant")
                store.complete_turn(chat["chat_id"], source_turn, "A reflective answer.", model="qwen3.5:9b-q4_K_M",
                                    used_vault=False, sources=[], retrieval={}, timing={}, identity_kernel={})
                candidate = store.create_interaction_memory_candidate(
                    "Engage with personal observations thoughtfully, without excessive praise, repetition, or unsupported generalisations.",
                    source={"chat_id": chat["chat_id"], "message_id": source_assistant["message_id"], "kind": "explicit_instruction"},
                )
                store.approve_interaction_memory(candidate["memory_id"])
                server.home_planner_request = lambda *args, **kwargs: {
                    "plan": {"intent": "conversation", "tools": [], "use_vault": False,
                             "primary_source": "user_message", "relevant_behavioral_memory_ids": [candidate["memory_id"]]},
                    "semantic": {}, "policy": {}, "world_state": {}, "fallback": False, "telemetry": {},
                }
                result = server.home_chat_payload("I remember when handovers fitted on one page. The process has grown to ten forms.", [], "never", chat["chat_id"], [])
                self.assertTrue(fake.messages, result)
                self.assertIn(candidate["memory_id"], fake.messages[0][0]["content"])
                self.assertIn("semantic planner selected these by meaning", fake.messages[0][0]["content"])
                completed = store.get(chat["chat_id"])
                receipt = next(item["context_recipe"] for item in reversed(completed["messages"])
                               if item.get("role") == "assistant" and item.get("context_recipe", {}).get("turn_id") != source_turn)
                self.assertTrue(receipt["behavioral_memory"]["model_received"])
                self.assertEqual(receipt["behavioral_memory"]["memory_ids"], [candidate["memory_id"]])
                self.assertGreater(receipt["context_blocks"]["behavioral_memory"]["characters"], 0)
                self.assertEqual(result["generation_status"], "complete")
                server.home_planner_request = lambda *args, **kwargs: {
                    "plan": {"intent": "conversation", "tools": [], "use_vault": False,
                             "primary_source": "user_message", "relevant_behavioral_memory_ids": []},
                    "semantic": {}, "policy": {}, "world_state": {}, "fallback": False, "telemetry": {},
                }
                unrelated = server.home_chat_payload("Explain how DHCP leases work.", [], "never", chat["chat_id"], [])
                self.assertEqual(unrelated["generation_status"], "complete")
                self.assertNotIn("APPROVED INTERACTION MEMORIES", fake.messages[1][0]["content"])
                unrelated_turn = store.get(chat["chat_id"])["messages"][-1]
                unrelated_receipt = unrelated_turn["context_recipe"]["behavioral_memory"]
                self.assertEqual(unrelated_receipt["candidate_count"], 1)
                self.assertEqual(unrelated_receipt["retrieved_count"], 0)
                self.assertFalse(unrelated_receipt["model_received"])
            finally:
                server.HOME_CHAT_STORE = original_store
                server._home_mcp = original_mcp
                server.home_planner_request = original_planner
                server.hermes_pilot.enabled = original_hermes_enabled


if __name__ == "__main__":
    unittest.main()
