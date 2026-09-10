import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from discovery_service.clustering import cluster_articles, diversify, rank_stories, story_from_cluster
from discovery_service.engine import DiscoveryEngine, _signal_category
from discovery_service.feeds import FetchResult, SourceDefinition, configured_sources, fetch_feed
from discovery_service.models import Article, canonical_url


def article(url, title, summary, source, category="Main News Feed", published_at="2026-09-10T06:00:00+00:00"):
    return Article.from_candidate({"url": url, "title": title, "summary": summary, "content": summary, "source_id": source.lower().replace(" ", "-"), "source_name": source, "source_url": "https://" + source.lower().replace(" ", "") + ".example/", "category": category, "published_at": published_at})


class DiscoveryTests(unittest.TestCase):
    def test_signal_category_derives_from_story_meaning_and_preserves_explicit_labels(self):
        self.assertEqual(_signal_category({"category": "Technology", "title": "OpenAI releases a new model", "summary": "A new model announcement."}), "AI Watch")
        self.assertEqual(_signal_category({"category": "Business", "title": "Thailand visa changes announced", "summary": "A new immigration rule."}), "Thailand Focus")
        self.assertEqual(_signal_category({"category": "AI Watch", "title": "A general update", "summary": "No keyword required."}), "AI Watch")
        self.assertEqual(_signal_category({"category": "Thailand Focus", "title": "A general update", "summary": "No keyword required."}), "Thailand Focus")
        self.assertEqual(_signal_category({"category": "Finance", "title": "Oil prices rise", "summary": "Markets moved higher."}), "Main News Feed")

    def test_canonical_url_removes_tracking_without_merging_real_query(self):
        self.assertEqual(canonical_url("https://Example.com/story/?utm_source=x&id=7#fragment"), "https://example.com/story?id=7")

    def test_related_reports_cluster_and_copied_text_is_not_counted_as_independent(self):
        first = article("https://one.example/apple", "Apple launches a foldable iPhone", "Apple announced a new foldable phone at an event.", "One News")
        second = article("https://two.example/apple", "Apple unveils foldable iPhone at event", "Apple announced a new foldable phone at an event.", "Two News")
        third = article("https://three.example/apple", "Apple foldable phone reviewed", "Independent analysis of Apple's new foldable phone and its price.", "Three News")
        clusters = cluster_articles([first, second, third])
        self.assertEqual(len(clusters), 1)
        story = story_from_cluster(clusters[0])
        self.assertEqual(story["article_count"], 3)
        self.assertEqual(story["source_count"], 2)
        self.assertEqual(len(story["evidence"]), 3)

    def test_diversity_prevents_one_category_consuming_every_slot(self):
        stories = rank_stories([{"story_id": f"story-{i}", "title": str(i), "summary": "", "category": "Technology" if i < 5 else "Science", "published_at": "2026-09-10T06:00:00+00:00", "source_count": 1, "article_count": 1} for i in range(6)])
        result = diversify(stories, limit=4)
        self.assertEqual([item["category"] for item in result[:2]], ["Science", "Technology"])

    def test_engine_refresh_persists_stories_and_reports_signal_push_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            source = SourceDefinition("source-test", "Test Feed", "https://example.test/feed.xml", "Technology")
            with patch.dict("os.environ", {"DISCOVERY_SERVICE_SIGNAL_SERVICE_URL": "http://127.0.0.1:9", "DISCOVERY_SERVICE_REFRESH_SECONDS": "900"}, clear=False), patch("discovery_service.engine.fetch_feed", return_value=FetchResult([{"url": "https://example.test/story", "title": "A useful technology story", "summary": "A short report.", "published_at": "2026-09-10T06:00:00Z", "source_id": "source-test", "source_name": "Test Feed", "source_url": "https://example.test/feed.xml", "category": "Technology"}])):
                engine = DiscoveryEngine(str(Path(directory) / "discovery.sqlite3"), sources=[source])
                try:
                    result = engine.refresh()
                    self.assertTrue(result["ok"])
                    self.assertEqual(result["story_count"], 1)
                    self.assertFalse(result["push"]["ok"])
                    self.assertEqual(engine.stories(10)[0]["source_count"], 1)
                finally:
                    engine.close()

    def test_signal_push_batches_and_retries_without_changing_story_identity(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit):
                return json.dumps(self.payload).encode("utf-8")

        stories = [
            {"story_id": f"story-{index}", "title": f"Story {index}", "summary": "A story.", "url": f"https://example.test/story-{index}", "published_at": "2026-09-10T06:00:00+00:00", "article_count": 1, "source_count": 1, "source_names": ["Example"], "source_domains": ["example.test"], "evidence": [], "rank_score": 1.0 - index / 100, "category": "Technology"}
            for index in range(3)
        ]
        requests = []
        outcomes = [TimeoutError("first request timed out"), {"ok": True, "accepted": 2, "duplicates": 0}, {"ok": True, "accepted": 1, "duplicates": 0}]

        def urlopen(request, timeout):
            requests.append(json.loads(request.data.decode("utf-8")))
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return Response(outcome)

        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DISCOVERY_SERVICE_SIGNAL_SERVICE_URL": "http://signal.test", "DISCOVERY_SERVICE_PUSH_BATCH_SIZE": "2", "DISCOVERY_SERVICE_PUSH_RETRIES": "1"}, clear=False), patch("discovery_service.engine.urllib.request.urlopen", side_effect=urlopen), patch("discovery_service.engine.time.sleep"):
            engine = DiscoveryEngine(str(Path(directory) / "discovery.sqlite3"), sources=[])
            try:
                result = engine._push(stories)
            finally:
                engine.close()

        self.assertTrue(result["ok"])
        self.assertEqual(result["accepted"], 3)
        self.assertEqual(result["batch_count"], 2)
        self.assertEqual(result["attempts"], 3)
        self.assertEqual([item["url"] for item in requests[0]["items"]], [item["url"] for item in requests[1]["items"]])
        self.assertEqual(requests[0]["items"][0]["provenance"]["discovery"]["story_id"], "story-0")

    def test_source_file_is_supported_for_large_catalogues(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            path.write_text(json.dumps([{"name": "One", "url": "https://one.example/feed", "category": "Science"}]), encoding="utf-8")
            with patch.dict("os.environ", {"DISCOVERY_SERVICE_SOURCES": "", "DISCOVERY_SERVICE_SOURCES_FILE": str(path)}, clear=False):
                result = configured_sources()
            self.assertEqual([(item.name, item.category) for item in result], [("One", "Science")])

    def test_curated_thailand_sources_are_first_class_and_html_sources_are_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            path.write_text(json.dumps([
                {"name": "Khaosod English", "url": "https://www.khaosodenglish.com/feed/", "category": "Thailand Focus"},
                {"name": "Thailand PBS World", "url": "https://www.thaipbsworld.com/feed/", "category": "Thailand Focus", "kind": "html"},
                {"name": "Retired", "url": "https://old.example/feed.xml", "category": "Thailand Focus", "enabled": False},
            ]), encoding="utf-8")
            with patch.dict("os.environ", {"DISCOVERY_SERVICE_SOURCES": "", "DISCOVERY_SERVICE_SOURCES_FILE": str(path)}, clear=False):
                result = configured_sources()
            self.assertEqual([(item.name, item.kind, item.enabled) for item in result], [
                ("Khaosod English", "rss_atom", True),
                ("Thailand PBS World", "html", True),
                ("Retired", "rss_atom", False),
            ])

        source = SourceDefinition("source-html", "Example", "https://example.test/", "Thailand Focus", "html")
        html = b'<html><a href="/news/one">A sufficiently long Thailand headline here</a><a href="/about">About Ariadne and news</a></html>'
        with patch("discovery_service.feeds.urllib.request.urlopen") as urlopen:
            class Headers(dict):
                def get_content_charset(self): return None

            class Response:
                headers = Headers()
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def read(self, limit): return html
            urlopen.return_value = Response()
            result = fetch_feed(source)
        self.assertEqual([item["title"] for item in result.candidates], ["A sufficiently long Thailand headline here"])

    def test_repeated_url_from_two_feeds_keeps_both_source_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            from discovery_service.store import DiscoveryStore

            store = DiscoveryStore(Path(directory) / "discovery.sqlite3")
            try:
                store.upsert_articles([
                    article("https://story.example/item", "A story", "The same report body.", "One News"),
                    article("https://story.example/item", "A story", "The same report body.", "Two News"),
                ])
                clusters = cluster_articles(store.recent_articles())
                story = story_from_cluster(clusters[0])
                self.assertEqual(story["article_count"], 1)
                self.assertEqual(story["source_names"], ["One News", "Two News"])
            finally:
                store.close()

    def test_materialized_story_cache_survives_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            from discovery_service.store import DiscoveryStore

            path = Path(directory) / "discovery.sqlite3"
            stories = [
                {
                    "story_id": f"story-{index}",
                    "title": f"Story {index}",
                    "summary": "A retained story.",
                    "url": f"https://example.test/story-{index}",
                    "published_at": "2026-09-10T06:00:00+00:00",
                    "first_seen_at": "2026-09-10T06:00:00+00:00",
                    "last_seen_at": "2026-09-10T06:00:00+00:00",
                    "article_count": 1,
                    "source_count": 1,
                    "source_names": ["Example"],
                    "source_domains": ["example.test"],
                    "evidence": [],
                    "rank_score": 1.0 - index / 1000,
                    "article_ids": [],
                }
                for index in range(240)
            ]
            store = DiscoveryStore(path)
            store.save_stories(stories)
            self.assertEqual(len(store.stories(limit=240)), 240)
            store.close()

            reopened = DiscoveryStore(path)
            try:
                self.assertEqual(len(reopened.stories(limit=240)), 240)
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
