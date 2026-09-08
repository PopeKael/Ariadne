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
    def test_home_is_signals_first_and_keeps_chat_memory(self):
        html = Path(__file__).with_name("home.html").read_text(encoding="utf-8")
        css = Path(__file__).with_name("home.css").read_text(encoding="utf-8")
        self.assertIn('href="/" aria-label="Ariadne Home"', html)
        self.assertIn('id="recent-chat-list"', html)
        self.assertIn('id="today-list"', html)
        self.assertIn('id="activity-details"', html)
        self.assertIn("Discover", html)
        js = Path(__file__).with_name("home.js").read_text(encoding="utf-8")
        self.assertIn("Main News Feed", js)
        self.assertIn("Thailand Focus", js)
        self.assertIn("AI Watch", js)
        self.assertIn("Watchlist", js)
        self.assertIn("signal-image-placeholder", js)
        self.assertIn("signal-watchlist-match", js)
        self.assertIn("Think with Ariadne", js)
        self.assertIn("/api/home/signals/promote", js)
        self.assertIn("signal-card-actions", js)
        self.assertIn("openAskAriadne", js)
        self.assertIn("renderAttachments", js)
        self.assertIn("+ Add article", js)
        self.assertIn("responseFeedback", js)
        self.assertIn("/api/home/feedback", js)
        self.assertIn("active_source_signal_ids", js)
        self.assertIn("Reading source article…", js)
        self.assertIn("contextMutationInFlight", js)
        self.assertIn("response-feedback", css)
        self.assertNotIn("visibleMatches.pop()", js)
        self.assertIn("signal-feedback", css)
        self.assertIn("signal-card-actions", css)
        self.assertIn("live-module", css)
        self.assertIn("signal-section-grid", css)

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
