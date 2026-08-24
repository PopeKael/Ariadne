import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from world_state import WORLD_STATE_VERSION, world_state_for_request  # noqa: E402


class WorldStateTests(unittest.TestCase):
    def test_world_state_is_compact_derived_and_separates_personality(self):
        state = world_state_for_request(
            "Give me ideas for my video channel.",
            [{"role": "user", "content": "What have we been doing lately on the main channel?"}],
            persist=False,
        )
        self.assertEqual(state["world_state_version"], WORLD_STATE_VERSION)
        self.assertTrue(state["derived"])
        self.assertIn("self", state)
        self.assertIn("now", state)
        self.assertIn("request_context", state)
        self.assertNotIn("personality", state)
        self.assertNotIn("identity_guidance", state)
        self.assertTrue(state["request_context"]["matched_subjects"])
        self.assertLess(len(str(state)), 20_000)

    def test_identity_and_current_work_question_selects_self_and_now_subjects(self):
        state = world_state_for_request(
            "Who am I, what are we working on, and what do you think matters most right now?",
            [],
            persist=False,
        )
        context = state["request_context"]
        self.assertTrue(context["retrieval_guidance"]["prefer_self_and_now"])
        self.assertTrue(context["matched_subjects"])
        self.assertTrue(any("Ariadne" in title for title in context["matched_subjects"]))


if __name__ == "__main__":
    unittest.main()
