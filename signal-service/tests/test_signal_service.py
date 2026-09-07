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
from signal_service.service import SignalService, extract_intake_candidates


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
        with patch("signal_service.service.fetch_feed", return_value=[candidate("https://example.test/live", "Live item")]) as fetch:
            result = self.service.refresh()
        fetch.assert_called_once()
        self.assertTrue(result["ok"])
        self.assertEqual(result["accepted"], 1)

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
        base = f"http://127.0.0.1:{httpd.server_port}"
        try:
            with urlopen(base + "/v1/health", timeout=2) as response:
                health = json.loads(response.read())
            request = Request(base + "/v1/intake/candidates", data=json.dumps({"items": [candidate()]}).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(request, timeout=2) as response:
                intake = json.loads(response.read())
            with urlopen(base + "/v1/briefing?limit=1", timeout=2) as response:
                briefing = json.loads(response.read())
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
        self.assertTrue(stale_briefing["stale"])
        self.assertEqual(stale_briefing["signals"][0]["url"], "https://example.test/story/1")


if __name__ == "__main__":
    unittest.main()
