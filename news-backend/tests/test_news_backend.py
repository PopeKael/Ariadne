import sys
import tempfile
import http.client
import json
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "discovery-service"))
sys.path.insert(0, str(ROOT / "news-backend"))

from discovery_service.models import Article, SourceDefinition
from news_backend.service import NewsHandler, NewsStore, _card_summary, _clean_article_markdown, _page_metadata, collect_once
from discovery_service.feeds import FetchResult


class NewsStoreTests(unittest.TestCase):
    def test_existing_stage_one_index_migrates_category_and_duplicate_count(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "news.sqlite3"
            connection = __import__("sqlite3").connect(database)
            connection.executescript("""
                CREATE TABLE articles (
                  article_id TEXT PRIMARY KEY,title TEXT NOT NULL,canonical_url TEXT NOT NULL UNIQUE,
                  source TEXT NOT NULL,published_at TEXT NOT NULL,discovered_at TEXT NOT NULL,
                  last_seen_at TEXT NOT NULL,image_url TEXT NOT NULL DEFAULT '',summary TEXT NOT NULL DEFAULT '',
                  markdown_path TEXT NOT NULL DEFAULT '',content_hash TEXT NOT NULL DEFAULT '',
                  content_ready INTEGER NOT NULL DEFAULT 0,scraped_at TEXT,scrape_error TEXT NOT NULL DEFAULT '',
                  rank_score REAL NOT NULL DEFAULT 0,interaction_state TEXT NOT NULL DEFAULT 'unseen'
                );
                CREATE TABLE briefing_state (
                  briefing_id TEXT PRIMARY KEY,generated_at TEXT NOT NULL,candidate_count INTEGER NOT NULL,
                  article_count INTEGER NOT NULL,input_hash TEXT NOT NULL,result_hash TEXT NOT NULL,
                  elapsed_ms REAL NOT NULL DEFAULT 0,run_count INTEGER NOT NULL DEFAULT 1
                );
            """)
            connection.close()
            store = NewsStore(root)
            article_columns = {row[1] for row in store.db.execute("PRAGMA table_info(articles)")}
            briefing_columns = {row[1] for row in store.db.execute("PRAGMA table_info(briefing_state)")}
            self.assertIn("category", article_columns)
            self.assertIn("duplicate_suppressed", briefing_columns)
            store.close()

    def test_card_text_and_page_metadata_are_cleaned_generically(self):
        summary = _card_summary(
            "A major international story unfolds today",
            "A major international story unfolds today. Officials said the agreement will begin next month. "
            "Officials said the agreement will begin next month. Continue reading for more updates.",
        )
        self.assertEqual(summary, "Officials said the agreement will begin next month.")
        markdown = _clean_article_markdown(
            "A useful article title",
            "# A useful article title\n\nA useful article title\n\nA clean paragraph with details.\n\n"
            "A clean paragraph with details.\n\nContinue reading...",
        )
        self.assertEqual(markdown, "A clean paragraph with details.")
        metadata = _page_metadata(
            b'<meta property="og:image" content="https://example.test/photo.jpg">'
            b'<meta name="description" content="A useful page description">', "utf-8"
        )
        self.assertEqual(metadata["og:image"], "https://example.test/photo.jpg")
        self.assertEqual(metadata["description"], "A useful page description")

    def test_article_upsert_and_local_only_retrieval(self):
        with tempfile.TemporaryDirectory() as temp:
            store = NewsStore(Path(temp))
            source = SourceDefinition("source-test", "Test Source", "https://example.test/feed")
            article = Article.from_candidate({"title": "A useful article title", "url": "https://example.test/story?utm_source=x",
                                               "summary": "A sufficiently descriptive summary."}, source)
            self.assertTrue(store.upsert_discovered(article))
            self.assertFalse(store.upsert_discovered(article))
            path = store.article_root / f"{article.article_id}.md"
            markdown = "# A useful article title\n\n" + ("A clean local paragraph. " * 8)
            path.write_text(markdown, encoding="utf-8")
            store.mark_cached(article.article_id, path, markdown)
            with patch("urllib.request.urlopen", side_effect=AssertionError("network attempted")) as urlopen:
                metadata, result = store.get_article(article.article_id)
            self.assertEqual(result, markdown)
            self.assertEqual(metadata["content_ready"], 1)
            urlopen.assert_not_called()
            store.close()
            readonly = NewsStore(Path(temp), read_only=True)
            with patch("urllib.request.urlopen", side_effect=AssertionError("network attempted")) as urlopen:
                metadata, result = readonly.get_article(article.article_id)
            self.assertEqual(result, markdown)
            self.assertEqual(metadata["content_ready"], 1)
            urlopen.assert_not_called()
            readonly.close()

    def test_article_interactions_and_feedback_are_separate_and_exposed_by_api(self):
        with tempfile.TemporaryDirectory() as temp:
            store = NewsStore(Path(temp))
            source = SourceDefinition("source-test", "Test Source", "https://example.test/feed")
            article = Article.from_candidate({
                "title": "An interaction test article", "url": "https://example.test/interaction",
                "summary": "A useful and sufficiently long test summary.",
            }, source)
            store.upsert_discovered(article)
            path = store.article_root / f"{article.article_id}.md"
            markdown = "# An interaction test article\n\n" + ("A clean cached fact. " * 12)
            path.write_text(markdown, encoding="utf-8")
            store.mark_cached(article.article_id, path, markdown)
            store.curate_top100(limit=1)
            NewsHandler.store = store
            server = ThreadingHTTPServer(("127.0.0.1", 0), NewsHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                def request(method, route, payload=None):
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                    body = json.dumps(payload).encode("utf-8") if payload is not None else None
                    headers = {"Content-Type": "application/json"} if body is not None else {}
                    connection.request(method, route, body=body, headers=headers)
                    response = connection.getresponse()
                    result = json.loads(response.read())
                    status = response.status
                    connection.close()
                    return status, result

                with patch("urllib.request.urlopen", side_effect=AssertionError("publisher fetch attempted")) as fetch:
                    status, article_result = request("GET", f"/articles/{article.article_id}")
                    self.assertEqual(status, 200)
                    self.assertEqual(article_result["markdown"], markdown)
                    self.assertFalse(article_result["retrieval"]["publisher_fetch_occurred"])
                    for kind in ("tldr_opened", "discussion_opened"):
                        self.assertEqual(request("POST", f"/articles/{article.article_id}/interactions", {"kind": kind})[0], 200)
                    self.assertEqual(request("POST", f"/articles/{article.article_id}/feedback", {"value": "not_useful"})[0], 200)
                    status, interactions = request("GET", f"/articles/{article.article_id}/interactions")
                fetch.assert_not_called()
                self.assertEqual(status, 200)
                self.assertEqual([event["kind"] for event in interactions["events"]],
                                 ["tldr_opened", "discussion_opened", "feedback"])
                self.assertEqual(interactions["feedback"]["value"], "not_useful")
                self.assertEqual(interactions["interaction_state"], "consumed")
                card = store.curated_briefing()["articles"][0]
                self.assertEqual(card["feedback"]["value"], "not_useful")
            finally:
                server.shutdown()
                server.server_close()
                store.close()

    def test_pending_article_is_scraped_when_feed_returns_304(self):
        with tempfile.TemporaryDirectory() as temp:
            store = NewsStore(Path(temp))
            source = SourceDefinition("source-test", "Test Source", "https://example.test/feed")
            article = Article.from_candidate({
                "title": "A useful article title", "url": "https://example.test/story",
                "summary": "A useful summary", "published_at": "2026-09-25T04:00:00Z",
            }, source)
            store.upsert_discovered(article)
            markdown = "# A useful article title\n\n" + ("A clean extracted paragraph. " * 8)

            def cache_pending(pending, target_store):
                path = target_store.article_root / f"{pending.article_id}.md"
                path.write_text(markdown, encoding="utf-8")
                target_store.mark_cached(pending.article_id, path, markdown)
                return True, ""

            with patch("news_backend.service.fetch_feed", return_value=FetchResult([], not_modified=True)), \
                    patch("news_backend.service._fetch_publisher", side_effect=cache_pending) as publisher_fetch:
                report = collect_once(store, [source], cycle_article_limit=1)
            publisher_fetch.assert_called_once()
            self.assertEqual(report["publisher_fetches"], 1)
            self.assertEqual(report["cached_this_cycle"], 1)
            self.assertEqual(report["pending_deferred"], 0)
            metadata, result = store.get_article(article.article_id)
            self.assertEqual(metadata["content_ready"], 1)
            self.assertEqual(result, markdown)
            store.close()

    def test_curator_is_stable_capped_and_never_fetches_publishers(self):
        with tempfile.TemporaryDirectory() as temp:
            store = NewsStore(Path(temp))
            source = SourceDefinition("source-test", "Test Source", "https://example.test/feed")
            for index in range(105):
                article = Article.from_candidate({
                    "title": f"Article {index:03d}", "url": f"https://example.test/story/{index}",
                    "summary": f"Summary for article {index}",
                    "published_at": f"2026-09-{25 - index // 24:02d}T{index % 24:02d}:00:00Z",
                }, source)
                store.upsert_discovered(article)
                path = store.article_root / f"{article.article_id}.md"
                markdown = f"# {article.title}\n\n" + ("Cached article text. " * 8)
                path.write_text(markdown, encoding="utf-8")
                store.mark_cached(article.article_id, path, markdown)

            with patch("urllib.request.urlopen", side_effect=AssertionError("publisher fetch attempted")) as fetch:
                first = store.curate_top100()
                second = store.curate_top100()
                third = store.curate_top100()
            fetch.assert_not_called()
            self.assertEqual(first["candidate_count"], 105)
            self.assertEqual(first["article_count"], 100)
            self.assertEqual(first["input_hash"], second["input_hash"])
            self.assertEqual(first["result_hash"], second["result_hash"])
            self.assertEqual(first["articles"], second["articles"])
            self.assertEqual(second["articles"], third["articles"])
            self.assertEqual(third["run_count"], 3)
            briefing = store.curated_briefing()
            self.assertEqual(briefing["article_count"], 100)
            self.assertEqual(briefing["run_count"], 3)
            self.assertEqual([row["position"] for row in briefing["articles"]], list(range(1, 101)))

            NewsHandler.store = store
            server = ThreadingHTTPServer(("127.0.0.1", 0), NewsHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch("urllib.request.urlopen", side_effect=AssertionError("publisher fetch attempted")) as fetch:
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                    connection.request("GET", "/briefing")
                    response = connection.getresponse()
                    payload = json.loads(response.read())
                    connection.close()
                fetch.assert_not_called()
                self.assertEqual(response.status, 200)
                self.assertEqual(payload["article_count"], 100)
                self.assertEqual(len(payload["articles"]), 100)
            finally:
                server.shutdown()
                server.server_close()
            store.close()

    def test_curator_suppresses_near_duplicates_and_varies_source_and_category(self):
        with tempfile.TemporaryDirectory() as temp:
            store = NewsStore(Path(temp))
            definitions = [
                SourceDefinition("s1", "Source One", "https://one.example/feed", "World"),
                SourceDefinition("s2", "Source Two", "https://two.example/feed", "Technology"),
                SourceDefinition("s3", "Source Three", "https://three.example/feed", "Science"),
            ]
            candidates = [
                (0, "Researchers discover new battery technology for electric cars"),
                (1, "Researchers discover new battery technology for electric vehicles"),
                (2, "Researchers discover new battery technology for electric autos"),
            ]
            distinct_titles = [
                "Volcano activity prompts flight warning across Indonesia",
                "Court blocks merger between regional telecom operators",
                "Scientists map ancient river beneath Sahara desert",
                "Wildfire evacuation expands across northern province",
                "Central bank holds interest rates amid inflation concerns",
                "Archaeologists uncover Bronze Age settlement near coast",
                "New satellite measures melting ice across Antarctic shelf",
                "Public hospitals report shortage of essential medicines",
                "Rural schools launch nationwide digital learning program",
                "Energy regulator approves offshore wind development zone",
                "Flood defenses fail after record rainfall in mountain valley",
                "Research team identifies previously unknown deep sea species",
            ]
            for index in range(15):
                source_index = index % len(definitions)
                title = candidates[index][1] if index < 3 else distinct_titles[index - 3]
                article = Article.from_candidate({
                    "title": title, "url": f"https://{source_index}.example/story/{index}",
                    "summary": f"Officials released verified details for story number {index} today.",
                    "published_at": "2026-09-25T10:00:00Z",
                }, definitions[source_index])
                store.upsert_discovered(article)
                path = store.article_root / f"{article.article_id}.md"
                markdown = f"# {title}\n\n" + (f"This cached report has enough local body text for article {index}. " * 12)
                path.write_text(markdown, encoding="utf-8")
                store.mark_cached(article.article_id, path, markdown)

            with patch("urllib.request.urlopen", side_effect=AssertionError("network attempted")) as fetch:
                first = store.curate_top100(limit=12)
                second = store.curate_top100(limit=12)
            fetch.assert_not_called()
            self.assertGreaterEqual(first["duplicate_suppressed"], 1)
            self.assertEqual(first["articles"], second["articles"])
            selected = store.curated_briefing()["articles"]
            self.assertTrue(all(row["category"] in {"World", "Technology", "Science"} for row in selected))
            source_counts = {}
            for row in selected:
                source_counts[row["source"]] = source_counts.get(row["source"], 0) + 1
            self.assertGreater(len(source_counts), 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
