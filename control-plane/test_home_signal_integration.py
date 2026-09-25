import sys
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from home_chat_store import ChatStore  # noqa: E402


class HomeSignalIntegrationTests(unittest.TestCase):
    def test_discovery_article_id_uses_discovery_canonical_url_rule(self):
        self.assertEqual(
            server._discovery_article_id_for_url("https://WWW.Example.test/story/?utm_source=mail&id=7#top"),
            server._discovery_article_id_for_url("https://www.example.test/story?id=7"),
        )
        self.assertIsNone(server._discovery_article_id_for_url("not a publisher URL"))

    def test_signal_article_cache_hit_attaches_markdown_without_publisher_fetch(self):
        signal = {"signal_id": "signal-1234567890abcdef", "title": "Cached report", "source_name": "Example", "url": "https://example.test/story", "summary": "Stored digest."}
        key = server._signal_article_job_key("chat-cache-hit", signal["signal_id"])
        original_jobs = server.SIGNAL_ARTICLE_JOBS
        original_events = server.HOME_EVENTS_PATH
        original_documents = server.DOCUMENT_WORK_ROOT
        with tempfile.TemporaryDirectory() as temporary:
            server.SIGNAL_ARTICLE_JOBS = {key: {"status": "loading"}}
            server.HOME_EVENTS_PATH = Path(temporary) / "Journal" / "events.md"
            server.DOCUMENT_WORK_ROOT = Path(temporary) / "documents"
            try:
                cache = {"status": "hit", "label": "ARTICLE CACHE HIT", "article_id": "article-123", "retrieval_ms": 4.2, "publisher_fetch_occurred": False, "storage": "local_file", "network_fetch": False}
                with patch.object(server, "_fetch_cached_signal_article", return_value=("Cached body from Hera.", cache)), patch.object(server, "promote_signal") as promote, patch.object(server, "update_document", return_value={"document_id": "doc-1", "content_hash": "abc"}) as update, patch.object(server, "publish_home_activity"), patch.object(server, "_session_processing", return_value=False):
                    server._run_signal_article_job(key, "session", "chat-cache-hit", signal, "doc-1")
                promote.assert_not_called()
                attached = update.call_args.args[-1]
                self.assertIn("Cached body from Hera.", attached)
                self.assertIn('article_status: "ready"', attached)
                result = server.SIGNAL_ARTICLE_JOBS[key]
                self.assertEqual(result["article_cache"]["label"], "ARTICLE CACHE HIT")
                self.assertFalse(result["result"]["publisher_fetch_occurred"])
                self.assertEqual(result["status"], "ready")
                self.assertIn("Cached article loaded", result["message"])
            finally:
                server.SIGNAL_ARTICLE_JOBS = original_jobs
                server.HOME_EVENTS_PATH = original_events
                server.DOCUMENT_WORK_ROOT = original_documents

    def test_signal_article_cache_miss_uses_existing_publisher_fallback(self):
        signal = {"signal_id": "signal-1234567890abcdef", "title": "Uncached report", "url": "https://example.test/uncached"}
        key = server._signal_article_job_key("chat-cache-miss", signal["signal_id"])
        original_jobs = server.SIGNAL_ARTICLE_JOBS
        original_events = server.HOME_EVENTS_PATH
        original_documents = server.DOCUMENT_WORK_ROOT
        original_vault = server.VAULT_ROOT
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            note = root / "Inbox" / "Uncached report.md"
            note.parent.mkdir(parents=True)
            note.write_text("---\ntype: source-article\narticle_status: ready\n---\n\nPublisher fallback body.\n", encoding="utf-8")
            server.SIGNAL_ARTICLE_JOBS = {key: {"status": "loading"}}
            server.HOME_EVENTS_PATH = root / "Journal" / "events.md"
            server.DOCUMENT_WORK_ROOT = root / "documents"
            server.VAULT_ROOT = root
            try:
                cache = {"status": "miss", "label": "ARTICLE CACHE MISS", "article_id": "article-uncached", "retrieval_ms": 3.1, "publisher_fetch_occurred": False}
                fallback = {"signal_id": signal["signal_id"], "path": "Inbox/Uncached report.md", "fetch_error": "", "cache_hit": False}
                with patch.object(server, "_fetch_cached_signal_article", return_value=(None, cache)), patch.object(server, "promote_signal", return_value=fallback) as promote, patch.object(server, "update_document", return_value={"document_id": "doc-2"}), patch.object(server, "publish_home_activity"), patch.object(server, "_session_processing", return_value=False):
                    server._run_signal_article_job(key, "session", "chat-cache-miss", signal, "doc-2")
                promote.assert_called_once_with(root, signal)
                result = server.SIGNAL_ARTICLE_JOBS[key]
                self.assertEqual(result["article_cache"]["label"], "ARTICLE CACHE MISS")
                self.assertTrue(result["article_cache"]["publisher_fetch_occurred"])
                self.assertTrue(result["result"]["publisher_fetch_occurred"])
                self.assertEqual(result["status"], "ready")
                self.assertEqual(result["message"], "Cache miss · fetching publisher")
            finally:
                server.SIGNAL_ARTICLE_JOBS = original_jobs
                server.HOME_EVENTS_PATH = original_events
                server.DOCUMENT_WORK_ROOT = original_documents
                server.VAULT_ROOT = original_vault

    def test_chat_refuses_to_run_while_source_article_is_loading(self):
        with tempfile.TemporaryDirectory() as temporary:
            original_store = server.HOME_CHAT_STORE
            original_events = server.HOME_EVENTS_PATH
            server.HOME_CHAT_STORE = ChatStore(Path(temporary))
            server.HOME_EVENTS_PATH = Path(temporary) / "Journal" / "Ariadne Home Events.md"
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                port = httpd.server_address[1]
                start_request = urllib.request.Request(
                    f"http://localhost:{port}/api/session/start",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(start_request, timeout=5) as response:
                    started = json.loads(response.read().decode("utf-8"))
                chat_request = urllib.request.Request(
                    f"http://localhost:{port}/api/home/chat",
                    data=json.dumps({
                        "session_id": started["session_id"],
                        "chat_id": started["chat_id"],
                        "message": "What does this article mean?",
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                loading_document = {
                    "document_id": "doc-loading",
                    "filename": "signal-context__signal-1234567890abcdef.md",
                    "metadata": {
                        "type": "source-article",
                        "signal_id": "signal-1234567890abcdef",
                        "article_status": "loading",
                    },
                }
                with patch.object(server, "_loading_source_article_documents", return_value=[loading_document]), patch.object(server, "home_chat_payload") as generation:
                    with self.assertRaises(urllib.error.HTTPError) as raised:
                        urllib.request.urlopen(chat_request, timeout=5)
                    payload = json.loads(raised.exception.read().decode("utf-8"))
                self.assertEqual(raised.exception.code, 409)
                self.assertEqual(payload["code"], "source_article_loading")
                self.assertEqual(payload["article_status"], "loading")
                self.assertEqual(payload["document_ids"], ["doc-loading"])
                generation.assert_not_called()
            finally:
                httpd.shutdown()
                httpd.server_close()
                server.HOME_CHAT_STORE = original_store
                server.HOME_EVENTS_PATH = original_events

    def test_signal_promotion_endpoint_uses_current_signal_service_record(self):
        signal = {"signal_id": "signal-1234567890abcdef", "title": "A signal", "url": "https://example.test/story"}
        fake_client = Mock()
        fake_client.briefing.return_value = {
            "ok": False,
            "stale": True,
            "message": "Signal Service unavailable: timed out",
            "signals": [signal],
        }
        with tempfile.TemporaryDirectory() as temporary:
            original_store = server.HOME_CHAT_STORE
            original_client = server.SIGNAL_SERVICE_CLIENT
            original_events = server.HOME_EVENTS_PATH
            original_vault_root = server.VAULT_ROOT
            vault_root = Path(temporary) / "vault"
            (vault_root / "Inbox").mkdir(parents=True)
            note_path = vault_root / "Inbox" / "A signal.md"
            note_path.write_text("---\nsignal_id: signal-1234567890abcdef\ntitle: A signal\n---\n\n# A signal\n", encoding="utf-8")
            server.HOME_CHAT_STORE = ChatStore(Path(temporary))
            server.SIGNAL_SERVICE_CLIENT = fake_client
            server.HOME_EVENTS_PATH = Path(temporary) / "Journal" / "Ariadne Home Events.md"
            server.VAULT_ROOT = vault_root
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                port = httpd.server_address[1]
                start_request = urllib.request.Request(
                    f"http://localhost:{port}/api/session/start",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(start_request, timeout=5) as response:
                    started = json.loads(response.read().decode("utf-8"))
                promote_request = urllib.request.Request(
                    f"http://localhost:{port}/api/home/signals/promote",
                    data=json.dumps({"session_id": started["session_id"], "signal_id": signal["signal_id"]}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch.object(server, "_start_signal_article_job", return_value={"status": "loading", "stage": "reading"}) as start:
                    with patch.object(server, "attach_document", return_value={"document_id": "doc-1", "filename": "signal-context__signal-1234567890abcdef.md", "title": "A signal", "metadata": {"type": "source-article", "signal_id": signal["signal_id"]}}) as attach:
                        with urllib.request.urlopen(promote_request, timeout=5) as response:
                            result = json.loads(response.read().decode("utf-8"))
                self.assertTrue(result["ok"])
                self.assertEqual(result["document"]["document_id"], "doc-1")
                fake_client.briefing.assert_called_once_with(limit=100)
                self.assertEqual(result["stage"], "opening_discussion")
                start.assert_called_once_with(started["session_id"], started["chat_id"], signal, "doc-1")
                attach.assert_called_once()
                self.assertIn("signal-context__signal-1234567890abcdef.md", attach.call_args.args)
                self.assertIn("Signal context", attach.call_args.args[-1])
            finally:
                httpd.shutdown()
                httpd.server_close()
                server.HOME_CHAT_STORE = original_store
                server.SIGNAL_SERVICE_CLIENT = original_client
                server.HOME_EVENTS_PATH = original_events
                server.VAULT_ROOT = original_vault_root

    def test_signal_promotion_reuses_existing_active_source_article_context(self):
        signal = {"signal_id": "signal-1234567890abcdef", "title": "A signal", "url": "https://example.test/story"}
        fake_client = Mock()
        fake_client.briefing.return_value = {"ok": True, "signals": [signal]}
        existing_document = {"document_id": "doc-existing", "filename": "A signal.md", "title": "A signal", "metadata": {"type": "source-article", "signal_id": signal["signal_id"]}}
        with tempfile.TemporaryDirectory() as temporary:
            original_store = server.HOME_CHAT_STORE
            original_client = server.SIGNAL_SERVICE_CLIENT
            original_events = server.HOME_EVENTS_PATH
            original_vault_root = server.VAULT_ROOT
            vault_root = Path(temporary) / "vault"
            (vault_root / "Inbox").mkdir(parents=True)
            note_path = vault_root / "Inbox" / "A signal.md"
            note_path.write_text("---\nsignal_id: signal-1234567890abcdef\ntitle: A signal\n---\n\n# A signal\n", encoding="utf-8")
            server.HOME_CHAT_STORE = ChatStore(Path(temporary))
            server.SIGNAL_SERVICE_CLIENT = fake_client
            server.HOME_EVENTS_PATH = Path(temporary) / "Journal" / "Ariadne Home Events.md"
            server.VAULT_ROOT = vault_root
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                port = httpd.server_address[1]
                start_request = urllib.request.Request(
                    f"http://localhost:{port}/api/session/start",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(start_request, timeout=5) as response:
                    started = json.loads(response.read().decode("utf-8"))
                promote_request = urllib.request.Request(
                    f"http://localhost:{port}/api/home/signals/promote",
                    data=json.dumps({"session_id": started["session_id"], "signal_id": signal["signal_id"]}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch.object(server, "_start_signal_article_job", return_value={"status": "loading", "stage": "reading"}) as start, patch.object(server, "list_documents", return_value=[existing_document]) as listed, patch.object(server, "attach_document") as attach:
                    with urllib.request.urlopen(promote_request, timeout=5) as response:
                        result = json.loads(response.read().decode("utf-8"))
                self.assertTrue(result["ok"])
                self.assertEqual(result["document"], existing_document)
                start.assert_called_once_with(started["session_id"], started["chat_id"], signal, "doc-existing")
                self.assertEqual(listed.call_count, 2)
                listed.assert_any_call(server.DOCUMENT_WORK_ROOT, started["chat_id"])
                attach.assert_not_called()
            finally:
                httpd.shutdown()
                httpd.server_close()
                server.HOME_CHAT_STORE = original_store
                server.SIGNAL_SERVICE_CLIENT = original_client
                server.HOME_EVENTS_PATH = original_events
                server.VAULT_ROOT = original_vault_root

    def test_signal_promotion_add_mode_preserves_existing_article_context(self):
        signal = {"signal_id": "signal-2234567890abcdef", "title": "Second signal", "url": "https://example.test/second"}
        fake_client = Mock()
        fake_client.briefing.return_value = {"ok": True, "signals": [signal]}
        existing_documents = [
            {"document_id": "doc-existing", "filename": "First signal.md", "title": "First signal", "metadata": {"type": "source-article", "signal_id": "signal-1234567890abcdef"}},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            original_store = server.HOME_CHAT_STORE
            original_client = server.SIGNAL_SERVICE_CLIENT
            original_events = server.HOME_EVENTS_PATH
            original_vault_root = server.VAULT_ROOT
            vault_root = Path(temporary) / "vault"
            (vault_root / "Inbox").mkdir(parents=True)
            note_path = vault_root / "Inbox" / "Second signal.md"
            note_path.write_text("---\nsignal_id: signal-2234567890abcdef\ntype: source-article\n---\n\n# Second signal\n", encoding="utf-8")
            server.HOME_CHAT_STORE = ChatStore(Path(temporary))
            server.SIGNAL_SERVICE_CLIENT = fake_client
            server.HOME_EVENTS_PATH = Path(temporary) / "Journal" / "Ariadne Home Events.md"
            server.VAULT_ROOT = vault_root
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                port = httpd.server_address[1]
                start_request = urllib.request.Request(
                    f"http://localhost:{port}/api/session/start", data=b"{}",
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(start_request, timeout=5) as response:
                    started = json.loads(response.read().decode("utf-8"))
                promote_request = urllib.request.Request(
                    f"http://localhost:{port}/api/home/signals/promote",
                    data=json.dumps({"session_id": started["session_id"], "signal_id": signal["signal_id"], "mode": "add"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with patch.object(server, "_start_signal_article_job", return_value={"status": "loading", "stage": "reading"}) as start, patch.object(server, "list_documents", side_effect=[existing_documents, existing_documents + [{"document_id": "doc-second", "filename": "signal-context__signal-2234567890abcdef.md", "title": "Second signal", "metadata": {"type": "source-article", "signal_id": signal["signal_id"]}}]]) as listed, patch.object(server, "attach_document", return_value={"document_id": "doc-second", "filename": "signal-context__signal-2234567890abcdef.md", "title": "Second signal", "metadata": {"type": "source-article", "signal_id": signal["signal_id"]}}) as attach, patch.object(server, "remove_document") as remove:
                    with urllib.request.urlopen(promote_request, timeout=5) as response:
                        result = json.loads(response.read().decode("utf-8"))
                self.assertTrue(result["ok"])
                self.assertEqual(result["mode"], "add")
                attach.assert_called_once()
                start.assert_called_once_with(started["session_id"], started["chat_id"], signal, "doc-second")
                remove.assert_not_called()
                self.assertEqual(listed.call_count, 2)
            finally:
                httpd.shutdown()
                httpd.server_close()
                server.HOME_CHAT_STORE = original_store
                server.SIGNAL_SERVICE_CLIENT = original_client
                server.HOME_EVENTS_PATH = original_events
                server.VAULT_ROOT = original_vault_root

    def test_response_feedback_endpoint_persists_rating_and_article_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            original_store = server.HOME_CHAT_STORE
            original_events = server.HOME_EVENTS_PATH
            server.HOME_CHAT_STORE = ChatStore(Path(temporary))
            server.HOME_EVENTS_PATH = Path(temporary) / "Journal" / "Ariadne Home Events.md"
            chat = server.HOME_CHAT_STORE.create()
            turn_id, started = server.HOME_CHAT_STORE.begin_turn(chat["chat_id"], "Question", "qwen3.5:9b", {})
            assistant = next(item for item in started["messages"] if item["role"] == "assistant")
            server.HOME_CHAT_STORE.complete_turn(
                chat["chat_id"], turn_id, "Answer", model="qwen3.5:9b", used_vault=False,
                sources=[], retrieval={}, timing={}, identity_kernel={},
                active_source_signal_ids=["signal-1234567890abcdef"],
            )
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                port = httpd.server_address[1]
                start_request = urllib.request.Request(
                    f"http://localhost:{port}/api/session/start",
                    data=json.dumps({"chat_id": chat["chat_id"]}).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(start_request, timeout=5) as response:
                    started_session = json.loads(response.read().decode("utf-8"))
                feedback_request = urllib.request.Request(
                    f"http://localhost:{port}/api/home/feedback",
                    data=json.dumps({
                        "session_id": started_session["session_id"], "chat_id": chat["chat_id"],
                        "message_id": assistant["message_id"], "active_source_signal_ids": ["signal-1234567890abcdef"],
                        "rating": "wrong", "comment": "The conclusion is not supported.",
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(feedback_request, timeout=5) as response:
                    result = json.loads(response.read().decode("utf-8"))
                self.assertTrue(result["ok"])
                self.assertEqual(result["feedback"]["rating"], "wrong")
                self.assertEqual(result["feedback"]["active_source_signal_ids"], ["signal-1234567890abcdef"])
                self.assertEqual(result["feedback"]["comment"], "The conclusion is not supported.")
            finally:
                httpd.shutdown()
                httpd.server_close()
                server.HOME_CHAT_STORE = original_store
                server.HOME_EVENTS_PATH = original_events

    def test_tldr_interaction_endpoint_forwards_distinct_interaction(self):
        original_store = server.HOME_CHAT_STORE
        original_client = server.SIGNAL_SERVICE_CLIENT
        with tempfile.TemporaryDirectory() as temporary:
            server.HOME_CHAT_STORE = ChatStore(Path(temporary))
            fake_client = Mock()
            fake_client.interaction.return_value = {"ok": True, "signal_id": "signal-1", "interaction": "tldr"}
            server.SIGNAL_SERVICE_CLIENT = fake_client
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                start_request = urllib.request.Request(
                    f"http://localhost:{httpd.server_address[1]}/api/session/start",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(start_request, timeout=5) as response:
                    started = json.loads(response.read().decode("utf-8"))
                interaction_request = urllib.request.Request(
                    f"http://localhost:{httpd.server_address[1]}/api/home/signals/interaction",
                    data=json.dumps({"session_id": started["session_id"], "signal_id": "signal-1", "interaction": "tldr"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(interaction_request, timeout=5) as response:
                    result = json.loads(response.read().decode("utf-8"))
                self.assertTrue(result["ok"])
                fake_client.interaction.assert_called_once_with("signal-1", "tldr")
            finally:
                httpd.shutdown()
                httpd.server_close()
                server.HOME_CHAT_STORE = original_store
                server.SIGNAL_SERVICE_CLIENT = original_client

    def test_today_renders_cached_signal_with_source_url(self):
        fake_client = Mock()
        fake_client.briefing.return_value = {"ok": True, "stale": True, "signals": [{"signal_id": "signal-1", "title": "A signal", "summary": "A concise summary with enough context for the card.", "source_name": "Example", "category": "AI Watch", "watchlist_matches": [{"topic_id": "watch-1", "topic": "A signal"}], "published_at": "2026-09-07T12:34:00+07:00", "url": "https://example.test/story", "image_url": "https://example.test/image.jpg", "provenance": {"discovery": {"story_id": "story-1", "article_count": 3, "source_count": 2, "source_names": ["Example", "Another Example"], "rank_score": 0.8123, "discovery_category": "Technology", "first_seen_at": "2026-09-07T12:30:00+07:00", "last_seen_at": "2026-09-07T12:35:00+07:00"}}, "feedback": {"value": "useful", "timestamp": "2026-09-07T12:35:00+07:00"}}]}
        with patch.object(server, "SIGNAL_SERVICE_CLIENT", fake_client):
            result = server.home_today_payload({"services": []})
        self.assertEqual(result[0]["label"], "A signal")
        self.assertEqual(result[0]["url"], "https://example.test/story")
        self.assertEqual(result[0]["summary"], "A concise summary with enough context for the card.")
        self.assertEqual(result[0]["source"], "Example")
        self.assertEqual(result[0]["published_at"], "2026-09-07T12:34:00+07:00")
        self.assertEqual(result[0]["signal_id"], "signal-1")
        self.assertEqual(result[0]["image_url"], "https://example.test/image.jpg")
        self.assertEqual(result[0]["category"], "AI Watch")
        self.assertEqual(result[0]["watchlist_matches"][0]["topic"], "A signal")
        self.assertEqual(result[0]["feedback"]["value"], "useful")
        self.assertEqual(result[0]["provenance"]["discovery"]["source_count"], 2)
        self.assertEqual(result[0]["provenance"]["discovery"]["rank_score"], 0.8123)
        self.assertEqual(result[0]["provenance"]["discovery"]["first_seen_at"], "2026-09-07T12:30:00+07:00")
        self.assertTrue(result[0]["detail"].startswith("Cached · Example"))

    def test_home_requests_discover_sized_briefing(self):
        fake_client = Mock()
        fake_client.briefing.return_value = {"ok": True, "stale": False, "signals": []}
        with patch.object(server, "SIGNAL_SERVICE_CLIENT", fake_client):
            server.home_today_payload({"services": []})
        fake_client.briefing.assert_called_once_with(limit=100)

    def test_today_ignores_unavailable_signal_service_without_breaking_local_status(self):
        fake_client = Mock()
        fake_client.briefing.return_value = {"ok": False, "stale": True, "signals": []}
        with patch.object(server, "SIGNAL_SERVICE_CLIENT", fake_client):
            result = server.home_today_payload({"services": [{"name": "Ollama", "state": "offline", "detail": "Unavailable"}]})
        self.assertEqual(result[0]["label"], "Ollama")
        self.assertEqual(result[0]["tone"], "offline")


if __name__ == "__main__":
    unittest.main()
