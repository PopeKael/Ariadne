from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


class HomePresentationTests(unittest.TestCase):
    def test_home_activity_hides_routine_events_but_keeps_raw_journal(self):
        with tempfile.TemporaryDirectory() as temporary:
            original_path = server.HOME_EVENTS_PATH
            server.HOME_EVENTS_PATH = Path(temporary) / "Journal" / "Ariadne Home Events.md"
            try:
                server.record_home_event("home_opened", "Ariadne Home opened.")
                server.record_home_event("question_submitted", "A question was submitted.")
                server.record_home_event("document_analysis_performed", "Retrieved 2 attachment chunk(s).")
                visible = server.read_home_events()
                raw = server.read_home_events(visible_only=False)
            finally:
                server.HOME_EVENTS_PATH = original_path
            self.assertEqual([item["kind"] for item in visible], ["document_analysis_performed"])
            self.assertEqual(
                [item["kind"] for item in raw],
                ["document_analysis_performed", "question_submitted", "home_opened"],
            )

    def test_avatar_event_retry_covers_named_pipe_listener_gap(self):
        sender = Mock(side_effect=[False, False, True])
        with patch.object(server.time, "sleep") as sleep:
            self.assertTrue(server._send_avatar_event_with_retry(sender, attempts=3))
        self.assertEqual(sender.call_count, 3)
        self.assertEqual(sleep.call_count, 2)


if __name__ == "__main__":
    unittest.main()
