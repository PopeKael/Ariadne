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

    def identity_system_prefix(self):
        return "IDENTITY", {"id": "ariadne", "version": "1.1.0", "scope": "user"}

    def ollama_chat(self, messages, **kwargs):
        self.kwargs.append(kwargs)
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


if __name__ == "__main__":
    unittest.main()
