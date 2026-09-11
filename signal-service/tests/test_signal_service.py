import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from signal_service.feeds import FeedDefinition
from signal_service.app import SignalHTTPServer
from signal_service.models import normalize_candidate
from signal_service.ranking import BasicRanker
from signal_service.service import SignalService, extract_intake_candidates, fetch_article_image


def candidate(url="https://example.test/story/1", title="Useful story", summary="A useful summary.", **extra):
    value = {"title": title, "url": url, "summary": summary, "source_name": "Example Feed", "published_at": "2026-09-07T08:00:00+00:00"}
    value.update(extra)
    return value


class SignalServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = SignalService(Path(self.temp.name) / "signals.sqlite3", feeds=[FeedDefinition("Test", "https://example.test/feed.xml")])

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_normalization_preserves_canonical_fields_and_provenance(self):
        signal = normalize_candidate({"headline": "  Story ", "link": "https://Example.test/story/#fragment", "description": "<p>Summary</p>", "published": "Mon, 07 Sep 2026 08:00:00 GMT", "source": "Feed"}, default_source_url="https://example.test/feed.xml", ingest_type="feed", adapter="rss_atom")
        self.assertEqual(signal.title, "Story")
        self.assertEqual(signal.url, "https://example.test/story")
        self.assertEqual(signal.summary, "Summary")
        self.assertEqual(signal.source_url, "https://example.test/feed.xml")
        self.assertEqual(signal.provenance["adapter"], "rss_atom")

    def test_normalization_preserves_additive_discovery_provenance(self):
        signal = normalize_candidate(candidate(provenance={"discovery": {"story_id": "story-1", "source_count": 3}}))
        self.assertEqual(signal.provenance["discovery"]["story_id"], "story-1")
        self.assertEqual(signal.provenance["discovery"]["source_count"], 3)

    def test_normalization_accepts_open_graph_image_metadata(self):
        signal = normalize_candidate({"title": "Story", "url": "https://example.test/story", "summary": "Summary", "metadata": {"og:image": "https://cdn.example.test/story.jpg"}})
        self.assertEqual(signal.image_url, "https://cdn.example.test/story.jpg")

    def test_normalization_preserves_category(self):
        signal = normalize_candidate(candidate(category="AI Watch"))
        self.assertEqual(signal.category, "AI Watch")

    def test_ranker_preserves_explicit_specialist_category_without_semantic_match(self):
        signal = normalize_candidate(candidate(category="AI Watch"))
        ranked = BasicRanker().rank([signal], profile={"semantic_state": "healthy", "semantic_interest_count": 1})
        self.assertEqual(ranked[0].category, "AI Watch")

    def test_ingestion_deduplicates_url_and_content(self):
        first = self.service.ingest_candidates([candidate()])
        second = self.service.ingest_candidates([candidate(), candidate("https://other.test/story", "Useful story", "A useful summary.")])
        self.assertEqual(first["accepted"], 1)
        self.assertEqual(second["duplicates"], 2)
        self.assertEqual(len(self.service.store.recent()), 1)

    def test_cached_briefing_is_created_from_intake(self):
        result = self.service.ingest_candidates([candidate()])
        briefing = self.service.briefing()
        self.assertIsNotNone(result["briefing"])
        self.assertFalse(briefing["stale"])
        self.assertEqual(briefing["signals"][0]["url"], "https://example.test/story/1")
        stored = self.service.store.latest_briefing()
        self.assertEqual(stored["signal_count"], 1)
        provenance = self.service.store._connection.execute("SELECT source_name, adapter, original_url FROM signal_provenance").fetchone()
        self.assertEqual(tuple(provenance), ("Example Feed", "external", "https://example.test/story/1"))

    def test_briefing_ranks_from_the_larger_cached_pool(self):
        candidates = [
            candidate(
                url=f"https://example.test/story/{index}",
                title=f"Useful story {index}",
                summary=f"A distinct retained story about topic {index}.",
                image_url="https://cdn.example.test/story.jpg",
                published_at=f"2026-09-07T08:{index % 60:02d}:00+00:00",
                category="AI Watch" if index % 2 else "Main News Feed",
                source_name="Ariadne Discovery Engine",
                provenance={"discovery": {"source_domains": [f"source-{index % 12}.example"]}},
            )
            for index in range(60)
        ]
        result = self.service.ingest_candidates(candidates)
        self.assertEqual(result["accepted"], 60)
        briefing = self.service.briefing(limit=100)
        self.assertEqual(briefing["signal_count"], 60)
        self.assertEqual(len(briefing["signals"]), 60)

    def test_briefing_capacity_reaches_discover_section_floor(self):
        candidates = [
            candidate(
                url=f"https://example.test/story/{index}",
                title=f"Useful story {index}",
                published_at=f"2026-09-07T08:{index:02d}:00+00:00",
                category="AI Watch",
            )
            for index in range(1, 9)
        ]
        self.service.ingest_candidates(candidates)
        briefing = self.service.briefing()
        self.assertEqual(len(briefing["signals"]), 8)
        self.assertEqual({item["category"] for item in briefing["signals"]}, {"AI Watch"})

    def test_feedback_is_persisted_and_returned_with_briefing(self):
        self.service.ingest_candidates([candidate()])
        signal_id = self.service.briefing()["signals"][0]["signal_id"]
        recorded = self.service.record_feedback(signal_id, "useful")
        self.assertEqual(recorded["signal_id"], signal_id)
        self.assertEqual(self.service.briefing()["signals"][0]["feedback"]["value"], "useful")
        self.assertTrue(self.service.briefing()["signals"][0]["feedback"]["timestamp"])
        self.service.record_feedback(signal_id, "interesting")
        self.service.record_feedback(signal_id, "interesting")
        rows = self.service.store._connection.execute("SELECT feedback_value FROM signal_feedback WHERE signal_id = ?", (signal_id,)).fetchall()
        self.assertEqual([row["feedback_value"] for row in rows], ["interesting"])
        self.assertEqual(self.service.briefing()["signals"][0]["feedback"]["value"], "interesting")

    def test_watchlist_topics_are_persistent(self):
        topic = self.service.add_watchlist_topic("Synology container startup")
        self.assertTrue(topic["active"])
        self.assertEqual(self.service.watchlist_topics()[0]["topic"], "Synology container startup")
        updated = self.service.add_watchlist_topic("synology container startup", active=False)
        self.assertFalse(updated["active"])
        self.assertEqual(self.service.watchlist_topics(), [])

    def test_watchlist_matches_are_annotated_without_reclassifying_signals(self):
        self.service.ingest_candidates([candidate(title="Thailand immigration update", summary="A current visa change.", category="Thailand Focus")])
        self.service.add_watchlist_topic("Thailand immigration")
        briefing = self.service.briefing()
        signal = briefing["signals"][0]
        self.assertEqual(signal["category"], "Thailand Focus")
        self.assertEqual(signal["watchlist_matches"][0]["topic"], "Thailand immigration")

    def test_watchlist_matching_uses_active_topics_and_avoids_substring_hits(self):
        self.service.ingest_candidates([candidate(title="Thailand update", summary="A normal update.")])
        self.service.add_watchlist_topic("AI")
        self.assertEqual(self.service.briefing()["signals"][0]["watchlist_matches"], [])
        self.service.add_watchlist_topic("Thailand", active=False)
        self.assertEqual(self.service.briefing()["signals"][0]["watchlist_matches"], [])

    def test_n8n_json_items_are_accepted_by_intake_adapter(self):
        values, _ = extract_intake_candidates({"items": [{"json": candidate(title="n8n item")}]})
        self.assertEqual(values[0]["title"], "n8n item")
        result = self.service.ingest_candidates(values, adapter="n8n_or_external")
        self.assertEqual(result["accepted"], 1)

    def test_missing_image_is_enriched_from_og_image_and_persisted(self):
        html = b'<html><head><meta property="og:image" content="/images/story.jpg"><meta name="twitter:image" content="/images/twitter.jpg"></head></html>'
        response = type("Response", (), {"headers": type("Headers", (), {"get_content_charset": lambda self: "utf-8"})(), "read": lambda self, limit: html, "__enter__": lambda self: self, "__exit__": lambda self, *args: None})()
        with patch("signal_service.service.urlopen", return_value=response) as fetch:
            result = self.service.ingest_candidates([candidate()])
        self.assertEqual(result["accepted"], 1)
        fetch.assert_called_once()
        self.assertEqual(self.service.briefing()["signals"][0]["image_url"], "https://example.test/images/story.jpg")

    def test_missing_image_falls_back_to_twitter_image(self):
        html = b'<html><head><meta name="twitter:image" content="https://cdn.example.test/twitter.jpg"></head></html>'
        response = type("Response", (), {"headers": type("Headers", (), {"get_content_charset": lambda self: "utf-8"})(), "read": lambda self, limit: html, "__enter__": lambda self: self, "__exit__": lambda self, *args: None})()
        with patch("signal_service.service.urlopen", return_value=response):
            self.service.ingest_candidates([candidate(url="https://example.test/twitter-story")])
        self.assertEqual(self.service.briefing()["signals"][0]["image_url"], "https://cdn.example.test/twitter.jpg")

    def test_image_metadata_falls_back_to_secure_and_src_variants_and_uses_final_url(self):
        html = b'<html><head><meta property="og:image:secure_url" content="/images/secure.jpg"><meta name="twitter:image:src" content="/images/twitter.jpg"></head></html>'
        response = type("Response", (), {"headers": type("Headers", (), {"get_content_charset": lambda self: "utf-8"})(), "read": lambda self, limit: html, "geturl": lambda self: "https://publisher.example.test/section/story", "__enter__": lambda self: self, "__exit__": lambda self, *args: None})()
        with patch("signal_service.service.urlopen", return_value=response):
            image_url = fetch_article_image("https://publisher.example.test/section/story")
        self.assertEqual(image_url, "https://publisher.example.test/images/secure.jpg")

    def test_google_news_article_is_resolved_before_image_fetch(self):
        google_page = b'<c-wiz><div data-n-a-id="article-id" data-n-a-ts="123" data-n-a-sg="signature"></div></c-wiz>'
        batch = b')]}\'\n\n[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://publisher.example.test/real-story\\",1]"]]'
        article = b'<meta property="og:image" content="/images/real.jpg">'

        def response(body, url=""):
            return type("Response", (), {"headers": type("Headers", (), {"get_content_charset": lambda self: "utf-8"})(), "read": lambda self, limit: body, "geturl": lambda self: url, "__enter__": lambda self: self, "__exit__": lambda self, *args: None})()

        with patch("signal_service.service.urlopen", side_effect=[response(google_page), response(batch), response(article, "https://publisher.example.test/real-story")]) as fetch:
            image_url = fetch_article_image("https://news.google.com/rss/articles/article-id")
        self.assertEqual(image_url, "https://publisher.example.test/images/real.jpg")
        self.assertEqual(fetch.call_count, 3)

    def test_incoming_image_is_preserved_without_fetching(self):
        with patch("signal_service.service.urlopen") as fetch:
            self.service.ingest_candidates([candidate(image_url="https://cdn.example.test/incoming.jpg")])
        fetch.assert_not_called()
        self.assertEqual(self.service.briefing()["signals"][0]["image_url"], "https://cdn.example.test/incoming.jpg")

    def test_failed_image_enrichment_does_not_reject_or_repeat_for_known_signal(self):
        with patch("signal_service.service.urlopen", side_effect=TimeoutError("image timeout")) as fetch:
            first = self.service.ingest_candidates([candidate()])
            second = self.service.ingest_candidates([candidate()])
        self.assertEqual(first["accepted"], 1)
        self.assertEqual(second["duplicates"], 1)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.service.briefing()["signals"][0]["image_url"], "")

    def test_feed_failure_returns_last_successful_cached_briefing_as_stale(self):
        self.service.ingest_candidates([candidate()])
        with patch("signal_service.service.fetch_feed", side_effect=RuntimeError("feed unavailable")):
            result = self.service.refresh()
        self.assertFalse(result["ok"])
        briefing = self.service.briefing()
        self.assertTrue(briefing["stale"])
        self.assertEqual(briefing["signals"][0]["title"], "Useful story")
        self.assertEqual(briefing["errors"][0]["source"], "Test")

    def test_feed_success_normalizes_real_adapter_candidates(self):
        with patch("signal_service.service.fetch_feed", return_value=[candidate("https://example.test/live", "Live item")]) as fetch, \
             patch("signal_service.service.emit_diagnostic") as diagnostic:
            result = self.service.refresh()
        fetch.assert_called_once()
        self.assertTrue(result["ok"])
        self.assertEqual(result["accepted"], 1)
        completed = [call for call in diagnostic.call_args_list if call.args and call.args[0] == "collection_completed"]
        self.assertEqual(len(completed), 1)
        self.assertTrue(completed[0].kwargs["first_successful"])
        self.assertTrue(completed[0].kwargs["ok"])

    def test_intake_accepts_common_n8n_wrappers(self):
        values, wrapper = extract_intake_candidates({"items": [candidate()], "source": "Daily Signal Briefing"})
        self.assertEqual(len(values), 1)
        self.assertEqual(wrapper["source"], "Daily Signal Briefing")
        values, _ = extract_intake_candidates(candidate())
        self.assertEqual(len(values), 1)

    def test_http_contract_exposes_health_briefing_and_intake(self):
        httpd = SignalHTTPServer(("127.0.0.1", 0), self.service)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://localhost:{httpd.server_port}"
        try:
            with urlopen(base + "/v1/health", timeout=2) as response:
                health = json.loads(response.read())
            request = Request(base + "/v1/intake/candidates", data=json.dumps({"items": [{"json": candidate()}], "source": "Daily Signal Briefing"}).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(request, timeout=2) as response:
                intake = json.loads(response.read())
            with urlopen(base + "/v1/briefing?limit=1", timeout=2) as response:
                briefing = json.loads(response.read())
            with urlopen(base + "/v1/watchlist/topics", timeout=2) as response:
                watchlist = json.loads(response.read())
            interest_request = Request(base + "/v1/interests", data=json.dumps({"name": "Local AI hardware", "description": "Local inference hardware"}).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(interest_request, timeout=2) as response:
                interest_created = json.loads(response.read())
            with urlopen(base + "/v1/interests", timeout=2) as response:
                interests = json.loads(response.read())
            watchlist_request = Request(base + "/v1/watchlist/topics", data=json.dumps({"topic": "Thailand immigration"}).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(watchlist_request, timeout=2) as response:
                watchlist_created = json.loads(response.read())
            feedback_request = Request(
                base + f"/v1/signals/{briefing['signals'][0]['signal_id']}/feedback",
                data=json.dumps({"feedback": "interesting"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(feedback_request, timeout=2) as response:
                feedback = json.loads(response.read())
            with patch("signal_service.service.fetch_feed", side_effect=RuntimeError("temporary outage")):
                self.service.refresh()
            with urlopen(base + "/v1/briefing?limit=1", timeout=2) as response:
                stale_briefing = json.loads(response.read())
        finally:
            httpd.shutdown()
            httpd.server_close()
        self.assertTrue(health["ok"])
        self.assertEqual(intake["accepted"], 1)
        self.assertEqual(briefing["signals"][0]["url"], "https://example.test/story/1")
        self.assertTrue(feedback["ok"])
        self.assertEqual(feedback["feedback"], "interesting")
        self.assertEqual(watchlist["topics"], [])
        self.assertEqual(watchlist_created["topic"]["topic"], "Thailand immigration")
        self.assertTrue(interest_created["ok"])
        self.assertEqual(interests["interests"][0]["name"], "Local AI hardware")
        self.assertTrue(stale_briefing["stale"])
        self.assertEqual(stale_briefing["signals"][0]["url"], "https://example.test/story/1")


if __name__ == "__main__":
    unittest.main()
