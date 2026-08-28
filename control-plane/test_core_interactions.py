import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core_interactions import CoreInteractionStream, InteractionEventError  # noqa: E402


class CoreInteractionTests(unittest.TestCase):
    def test_turn_and_response_references_are_provider_independent(self):
        with tempfile.TemporaryDirectory() as temporary:
            stream = CoreInteractionStream(Path(temporary) / "interactions.jsonl")
            stream.emit("turn_started", conversation_id="chat-1", turn_id="turn-1")
            stream.emit("response_completed", conversation_id="chat-1", turn_id="turn-1", response_id="turn-1", data={"answer_chars": 12})
            events = stream.read_recent(10, conversation_id="chat-1")
            raw = Path(temporary, "interactions.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["data"]["event_type"], "response_completed")
        self.assertEqual(events[0]["data"]["response_id"], "turn-1")
        self.assertEqual(json.loads(raw[0])["event_type"], "CORE_INTERACTION")

    def test_future_feedback_and_selection_events_are_contract_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            stream = CoreInteractionStream(Path(temporary) / "interactions.jsonl")
            stream.emit("selection_created", conversation_id="chat-1", turn_id="turn-1", data={"quoted_chars": 20})
            stream.emit("feedback_recorded", conversation_id="chat-1", turn_id="turn-1", response_id="turn-1", data={"kind": "positive"})
            self.assertEqual(len(stream.read_recent(10)), 2)

    def test_unknown_event_type_is_rejected(self):
        stream = CoreInteractionStream(Path(tempfile.gettempdir()) / "ariadne-interactions-test.jsonl")
        with self.assertRaises(InteractionEventError):
            stream.emit("model_weights_changed", conversation_id="chat-1")


if __name__ == "__main__":
    unittest.main()
