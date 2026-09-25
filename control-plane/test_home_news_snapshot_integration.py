from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from news_briefing_cache import NewsBriefingCache  # noqa: E402


class HomeNewsSnapshotIntegrationTests(unittest.TestCase):
    def test_snapshot_route_is_local_and_bypasses_home_session_and_signal_work(self):
        cards = [
            {"article_id": "article-a", "title": "First", "canonical_url": "https://example.test/a"},
            {"article_id": "article-b", "title": "Second", "canonical_url": "https://example.test/b"},
        ]
        signal_client = Mock()
        previous_cache = server.NEWS_BRIEFING_CACHE
        previous_signal_client = server.SIGNAL_SERVICE_CLIENT
        server.SIGNAL_SERVICE_CLIENT = signal_client
        with tempfile.TemporaryDirectory() as temporary:
            cache_path = Path(temporary) / "news-briefing.json"
            cache_path.write_text(json.dumps({"articles": cards}), encoding="utf-8")
            cache = NewsBriefingCache("http://127.0.0.1:1", cache_path=cache_path)
            server.NEWS_BRIEFING_CACHE = cache
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(cache, "_request_briefing", side_effect=AssertionError("offline cache tried Hera")) as network_fetch, patch.object(server, "_expire_sessions") as expire:
                    started = time.perf_counter()
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{httpd.server_address[1]}/api/news/briefing-snapshot",
                        timeout=2,
                    ) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    elapsed_ms = (time.perf_counter() - started) * 1000
                with self.subTest(local_api_elapsed_ms=round(elapsed_ms, 3)):
                    self.assertLess(elapsed_ms, 250)
                self.assertEqual(payload["source"], "local_snapshot")
                self.assertEqual(payload["briefing"]["articles"], cards)
                network_fetch.assert_not_called()
                expire.assert_not_called()
                signal_client.briefing.assert_not_called()
            finally:
                httpd.shutdown()
                httpd.server_close()
                server.NEWS_BRIEFING_CACHE = previous_cache
                server.SIGNAL_SERVICE_CLIENT = previous_signal_client

    def test_home_loads_news_only_from_snapshot_on_page_entry(self):
        source = (ROOT / "home.js").read_text(encoding="utf-8")
        self.assertIn('getJson("/api/news/briefing-snapshot")', source)
        self.assertIn('label: item.title || item.label || "Article"', source)
        self.assertIn('url: item.canonical_url || item.url || ""', source)
        self.assertIn("cards.forEach(item => grid.append(renderSignalCard(item)))", source)
        self.assertNotIn("renderToday(data.today)", source)
        home_load = source.split("async function loadHome(", 1)[1].split("\nfunction sessionLost", 1)[0]
        self.assertIn("if (refreshNews) await loadNewsSnapshot();", home_load)
        self.assertLess(home_load.index("await loadNewsSnapshot()"), home_load.index('getJson("/api/home/activity")'))
        self.assertIn("loadHome({refreshNews: true})", source)
        self.assertIn("window.setInterval(loadHome, 15000)", source)

    def test_home_news_actions_are_article_id_only_and_stay_out_of_startup(self):
        source = (ROOT / "home.js").read_text(encoding="utf-8")
        self.assertIn('if (item.article_id)', source)
        self.assertIn('["useful", "Useful"], ["interesting", "Interesting"], ["not_useful", "Not useful"]', source)
        self.assertIn('"/api/home/news/feedback"', source)
        self.assertIn('"/api/home/news/article-context"', source)
        self.assertIn('params.get("article_id")', source)
        self.assertIn('state.articleTldrPending = articleAction === "tldr_opened"', source)
        self.assertIn("article_tldr: articleTldr", source)
        self.assertIn('Opening TLDR · loading the cached article from Hera…', source)
        news_open = source.split("async function openNewsArticle", 1)[1].split("async function submitSignalFeedback", 1)[0]
        self.assertIn("articleId, articleAction: action", news_open)
        self.assertNotIn("signal_id", news_open)
        session_start = source.split("async function startSession()", 1)[1].split("\nasync function ask(", 1)[0]
        self.assertIn("loadRecentChats();", session_start)
        self.assertNotIn("await loadRecentChats();", session_start)
        self.assertIn("if (!openingArticle) loadRecentChats();", session_start)
        self.assertIn("TLDR · Loading cached article from Hera…", source)
        self.assertIn("TLDR · Hera cache", source)
        self.assertIn("cache_retrieval_ms", source)
        snapshot_loader = source.split("async function loadNewsSnapshot", 1)[1].split("async function loadHome", 1)[0]
        self.assertIn('getJson("/api/news/briefing-snapshot")', snapshot_loader)
        self.assertNotIn("8791", source)

    def test_verified_hera_article_attachment_is_required_for_tldr_fast_path(self):
        article_id = "article-0123456789abcdef0123456789ab"
        self.assertTrue(server._is_verified_hera_article_attachment([{
            "metadata": {"type": "source-article", "article_cache": "hera-news-backend",
                         "article_status": "ready", "article_id": article_id},
        }]))
        for metadata in (
            {"type": "source-article", "article_cache": "hera-news-backend", "article_status": "loading", "article_id": article_id},
            {"type": "source-article", "article_cache": "publisher", "article_status": "ready", "article_id": article_id},
            {"type": "source-article", "article_cache": "hera-news-backend", "article_status": "ready", "article_id": "not-an-article-id"},
        ):
            self.assertFalse(server._is_verified_hera_article_attachment([{"metadata": metadata}]))

    def test_news_article_context_and_feedback_use_hera_and_temporary_documents_only(self):
        article_id = "article-0123456789abcdef0123456789ab"
        article = {"article_id": article_id, "title": "A cached test article", "source": "Test Source",
                   "canonical_url": "https://example.test/story", "published_at": "2026-09-25T12:00:00Z"}
        original_store = server.HOME_CHAT_STORE
        original_context_root = server.DOCUMENT_WORK_ROOT
        original_events = server.HOME_EVENTS_PATH
        original_sessions = server.SESSIONS
        original_cache = server.NEWS_BRIEFING_CACHE
        original_signal = server.SIGNAL_SERVICE_CLIENT
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache_path = root / "runtime" / "news-briefing.json"
            cache = NewsBriefingCache("http://127.0.0.1:1", cache_path=cache_path)
            seed = {"ok": True, "briefing_id": "top100", "input_hash": "a", "result_hash": "b",
                    "articles": [{"article_id": article_id, "title": article["title"], "source": "Test Source"}]}
            with patch.object(cache, "_request_briefing", return_value=seed):
                cache.sync_once()
            server.HOME_CHAT_STORE = server.ChatStore(root / "chats")
            server.DOCUMENT_WORK_ROOT = root / "contexts"
            server.HOME_EVENTS_PATH = root / "Journal" / "events.md"
            server.SESSIONS = {}
            server.NEWS_BRIEFING_CACHE = cache
            signal_client = Mock()
            server.SIGNAL_SERVICE_CLIENT = signal_client
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()

            def post(route, payload):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}{route}",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                try:
                    with urllib.request.urlopen(request, timeout=5) as response:
                        return response.status, json.loads(response.read().decode("utf-8"))
                except Exception as exc:
                    if hasattr(exc, "read"):
                        return exc.code, json.loads(exc.read().decode("utf-8"))
                    raise

            api_log = []
            interaction_done = threading.Event()
            def news_request(requested_id, action="", payload=None):
                self.assertEqual(requested_id, article_id)
                api_log.append((requested_id, action))
                if action == "interactions":
                    interaction_done.set()
                    return {"ok": True, "event": {"created_at": "2026-09-25T12:30:00Z"}}
                if action == "feedback":
                    return {"ok": True, "event": {"created_at": "2026-09-25T12:31:00Z"},
                            "feedback": {"value": payload["value"], "updated_at": "2026-09-25T12:31:00Z"}}
                return {"ok": True, "article": article, "markdown": "# A cached test article\n\n" + ("Cached article evidence. " * 8),
                        "retrieval": {"storage": "local_file", "network_fetch": False,
                                      "publisher_fetch_occurred": False, "retrieval_ms": 3.2}}

            try:
                with patch.object(server, "_news_backend_request", side_effect=news_request), \
                        patch.object(server, "promote_signal", side_effect=AssertionError("Vault promotion attempted")):
                    status, started = post("/api/session/start", {})
                    self.assertEqual(status, 200)
                    session_id = started["session_id"]
                    status, attached = post("/api/home/news/article-context", {
                        "session_id": session_id, "article_id": article_id, "action": "tldr_opened",
                    })
                    self.assertEqual(status, 200)
                    self.assertFalse(attached["publisher_fetch_occurred"])
                    self.assertTrue(attached["interaction_queued"])
                    self.assertIsNone(attached["interaction_persisted"])
                    self.assertEqual(attached["retrieval"]["storage"], "local_file")
                    document = attached["document"]
                    self.assertEqual(document["metadata"]["article_id"], article_id)
                    context_file = root / "contexts" / f"{started['chat_id']}.json"
                    status, feedback = post("/api/home/news/feedback", {
                        "session_id": session_id, "article_id": article_id, "feedback": "interesting",
                    })
                    self.assertEqual(status, 200)
                    self.assertTrue(feedback["persisted_to_hera"])
                    self.assertTrue(feedback["local_snapshot_updated"])
                    self.assertTrue(interaction_done.wait(1.0))
                    self.assertCountEqual(api_log, [(article_id, "interactions"), (article_id, ""), (article_id, "feedback")])
                signal_client.briefing.assert_not_called()
                self.assertTrue(context_file.is_file())
                self.assertIn("Ariadne inference boundary", context_file.read_text(encoding="utf-8"))
                reloaded = NewsBriefingCache("http://127.0.0.1:1", cache_path=cache_path)
                self.assertEqual(reloaded.snapshot()["briefing"]["articles"][0]["feedback"]["value"], "interesting")
            finally:
                httpd.shutdown()
                httpd.server_close()
                server.HOME_CHAT_STORE = original_store
                server.DOCUMENT_WORK_ROOT = original_context_root
                server.HOME_EVENTS_PATH = original_events
                server.SESSIONS = original_sessions
                server.NEWS_BRIEFING_CACHE = original_cache
                server.SIGNAL_SERVICE_CLIENT = original_signal


if __name__ == "__main__":
    unittest.main()
