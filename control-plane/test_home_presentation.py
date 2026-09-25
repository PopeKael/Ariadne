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
        self.assertIn('<script src="/signal-popover-position.js"></script>', html)
        server_source = Path(__file__).with_name("server.py").read_text(encoding="utf-8")
        self.assertIn('path == "/signal-popover-position.js"', server_source)
        self.assertIn('id="recent-chat-list"', html)
        self.assertIn('id="today-list"', html)
        self.assertIn('id="activity-details"', html)
        self.assertIn("Discover", html)
        js = Path(__file__).with_name("home.js").read_text(encoding="utf-8")
        self.assertIn("Coverage", js)
        render_today = js.split("function renderToday(items) {", 1)[1].split("\nfunction renderAdaptive", 1)[0]
        self.assertIn("cards.forEach(item => grid.append(renderSignalCard(item)))", render_today)
        self.assertNotIn("SIGNAL_SECTIONS", render_today)
        self.assertNotIn("INITIAL_SIGNALS_PER_SECTION", render_today)
        self.assertIn("signal-image-placeholder", js)
        self.assertIn("signal-watchlist-match", js)
        self.assertIn("signal-details-popover", js)
        self.assertIn("signal-info-button", js)
        self.assertIn("details.hidden = true", js)
        self.assertIn("document.body.append(details)", js)
        self.assertIn("positionSignalDetails", js)
        self.assertIn("const anchorRect = anchor.getBoundingClientRect()", js)
        self.assertIn("window.calculateSignalPopoverPosition", js)
        self.assertIn('window.addEventListener("scroll", repositionDetails, true)', js)
        self.assertIn('addDetail("Rank score"', js)
        self.assertIn('addDetail("Coverage"', js)
        self.assertIn('addDetail("Discovery category"', js)
        self.assertIn('actions.append(feedback, info, promotion)', js)
        self.assertIn("mouseenter", js)
        self.assertIn("Original source", js)
        self.assertIn("Think with Ariadne", js)
        self.assertIn('"TLDR"', js)
        self.assertNotIn("Not useful", js)
        self.assertIn("/api/home/signals/interaction", js)
        self.assertIn("TLDR_PROMPT", js)
        self.assertIn("waitForSignalArticleReady", js)
        self.assertIn("summarizing the published signal context instead", js)
        self.assertIn("{monitor: false}", js)
        self.assertIn("state.signalArticleBusy.delete(signalId)", js)
        self.assertIn("replaceSignalArticleDocument(result.document)", js)
        self.assertIn("/api/home/signals/promote", js)
        self.assertIn("signal-card-actions", js)
        self.assertIn("openAskAriadne", js)
        self.assertIn("renderAttachments", js)
        self.assertIn("fresh: !CHAT_PAGE", js)
        self.assertIn("+ Add article", js)
        self.assertIn("responseFeedback", js)
        self.assertIn("/api/home/feedback", js)
        self.assertIn("active_source_signal_ids", js)
        self.assertIn("Reading source article…", js)
        self.assertIn("Opening discussion…", js)
        self.assertIn("Answering…", js)
        self.assertIn("/api/home/signals/promote/status", js)
        self.assertIn("ARTICLE CACHE HIT", js + Path(ROOT / "server.py").read_text(encoding="utf-8"))
        self.assertIn("document ready ${elapsed} ms after TLDR click", js)
        self.assertIn("/api/home/activity-state", js)
        self.assertIn("generation_truncated", js)
        self.assertIn("Continue", js)
        self.assertIn("summarizeCitations", js)
        self.assertIn("cited passage", js)
        self.assertIn("Evidence: ", js)
        self.assertIn("activity.message || activity.label", js)
        self.assertIn("signalArticleBusy", js)
        self.assertIn("contextMutationInFlight", js)
        self.assertIn("loadingSourceArticles", js)
        self.assertIn("Wait for the selected source article to finish loading before asking Ariadne.", js)
        self.assertIn('document.body.classList.toggle("chat-expanded", meaningful)', js)
        self.assertIn("collapse.hidden = !meaningful", js)
        self.assertIn('Number(chat.turn_count || 0)', js)
        self.assertIn("response-feedback", css)
        self.assertNotIn("visibleMatches.pop()", js)
        self.assertIn("signal-feedback", css)
        self.assertIn("signal-card-actions", css)
        self.assertIn("live-module", css)
        self.assertIn("signal-section-grid", css)
        self.assertIn("signal-section-more", css)
        self.assertIn("signal-details-popover", css)
        self.assertIn("signal-info-button", css)
        self.assertIn("position:relative", css)
        self.assertIn("bottom:26px", css)
        self.assertIn("signal-details-floating", css)
        self.assertIn("signal-details-positioning", css)
        self.assertIn("position:fixed", css)
        self.assertIn("const scrollTop = root.scrollTop", js)
        self.assertIn("root.scrollTop = Math.min(scrollTop", js)
        self.assertIn("generation-warning", css)

    def test_canonical_activity_stream_has_operational_states(self):
        self.assertEqual(
            server.HOME_ACTIVITY_STREAM.snapshot("missing-chat").state,
            "idle",
        )
        self.assertEqual(
            server.home_activity_state_payload("missing-chat")["activity"]["state"],
            "idle",
        )

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
