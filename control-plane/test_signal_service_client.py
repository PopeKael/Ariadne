import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from signal_service_client import SignalServiceClient


class SignalServiceClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.diagnostics_path = Path(self.temp.name) / "signal-service-events.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def test_briefing_normalizes_missing_signals(self):
        client = SignalServiceClient("http://localhost:8788", diagnostics_path=self.diagnostics_path)
        with patch.object(client, "_get", return_value={"ok": True, "stale": False}):
            result = client.briefing()
        self.assertEqual(result["signals"], [])

    def test_failure_is_contained_as_offline_fallback(self):
        client = SignalServiceClient("http://localhost:8788", diagnostics_path=self.diagnostics_path)
        with patch("signal_service_client.urllib.request.urlopen", side_effect=URLError("offline")):
            result = client.briefing()
        self.assertFalse(result["ok"])
        self.assertEqual(result["signals"], [])
        self.assertEqual(result["state"], "offline")

    def test_populated_briefing_survives_timeout(self):
        client = SignalServiceClient("http://localhost:8788", diagnostics_path=self.diagnostics_path)
        briefing = {
            "ok": True,
            "stale": False,
            "generated_at": "2026-09-11T01:00:00+00:00",
            "signals": [{"signal_id": "signal-1", "title": "First signal"}],
        }
        with patch.object(client, "_get", side_effect=[briefing, {"ok": False, "state": "offline", "message": "timed out"}]):
            client.briefing()
            result = client.briefing()
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "offline")
        self.assertTrue(result["stale"])
        self.assertEqual(result["generated_at"], briefing["generated_at"])
        self.assertEqual(result["signals"], briefing["signals"])

    def test_populated_briefing_survives_temporary_offline_response(self):
        client = SignalServiceClient("http://localhost:8788", diagnostics_path=self.diagnostics_path)
        briefing = {
            "ok": True,
            "stale": False,
            "generated_at": "2026-09-11T01:00:00+00:00",
            "signals": [{"signal_id": "signal-1", "title": "First signal"}],
        }
        with patch.object(client, "_get", side_effect=[briefing, {"ok": False, "state": "offline", "message": "unavailable"}]):
            client.briefing()
            result = client.briefing()
        self.assertEqual(result["signals"], briefing["signals"])
        self.assertTrue(result["stale"])

    def test_subsequent_success_replaces_cached_briefing(self):
        client = SignalServiceClient("http://localhost:8788", diagnostics_path=self.diagnostics_path)
        first = {"ok": True, "stale": False, "generated_at": "2026-09-11T01:00:00+00:00", "signals": [{"signal_id": "signal-1"}]}
        second = {"ok": True, "stale": False, "generated_at": "2026-09-11T01:05:00+00:00", "signals": [{"signal_id": "signal-2"}]}
        with patch.object(client, "_get", side_effect=[first, {"ok": False, "state": "offline"}, second]):
            client.briefing()
            fallback = client.briefing()
            result = client.briefing()
        self.assertEqual(fallback["signals"], first["signals"])
        self.assertEqual(result["signals"], second["signals"])
        self.assertFalse(result["stale"])
        self.assertEqual(result["generated_at"], second["generated_at"])

    def test_health_failure_keeps_status_separate_from_cached_briefing(self):
        client = SignalServiceClient("http://localhost:8788", diagnostics_path=self.diagnostics_path)
        briefing = {"ok": True, "stale": False, "generated_at": "2026-09-11T01:00:00+00:00", "signals": [{"signal_id": "signal-1"}]}
        with patch.object(client, "_get", side_effect=[briefing, {"ok": False, "state": "offline"}, {"ok": False, "state": "offline"}]):
            client.briefing()
            health = client.health()
            preserved = client.briefing()
        self.assertEqual(health["state"], "offline")
        self.assertEqual(preserved["signals"], briefing["signals"])
        self.assertTrue(preserved["stale"])

    def test_invalid_url_does_not_raise(self):
        client = SignalServiceClient("file:///not-allowed", diagnostics_path=self.diagnostics_path)
        self.assertEqual(client.health()["state"], "offline")

    def test_request_diagnostics_capture_success(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return json.dumps({"ok": True, "state": "healthy"}).encode("utf-8")

        client = SignalServiceClient("http://localhost:8788", diagnostics_path=self.diagnostics_path)
        with patch("signal_service_client.urllib.request.urlopen", return_value=Response()):
            result = client.health()
        self.assertEqual(result["state"], "healthy")
        events = [json.loads(line) for line in self.diagnostics_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([event["event_type"] for event in events], ["SIGNAL_SERVICE_REQUEST_STARTED", "SIGNAL_SERVICE_REQUEST_SUCCEEDED"])
        self.assertTrue(events[0]["timestamp"])
        self.assertEqual(events[0]["data"]["path"], "/v1/health")
        self.assertEqual(events[1]["data"]["state"], "healthy")

    def test_sources_projects_legacy_health_feeds_during_rolling_upgrade(self):
        client = SignalServiceClient("http://localhost:8788", diagnostics_path=self.diagnostics_path)
        legacy_health = {
            "ok": True,
            "state": "healthy",
            "feeds": [
                {"name": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/index"},
                {"name": "NASA Breaking News", "url": "https://www.nasa.gov/rss/dyn/breaking_news.rss"},
                {"name": "Hacker News", "url": "https://hnrss.org/frontpage"},
            ],
            "last_attempt_at": "2026-09-09T01:00:00+00:00",
            "last_success_at": "2026-09-09T01:00:01+00:00",
            "last_collection_ok": True,
            "source_status": [],
        }
        with patch.object(client, "_get", side_effect=[{"ok": False, "state": "offline"}, legacy_health]):
            result = client.sources()
        self.assertTrue(result["legacy_projection"])
        self.assertEqual([item["name"] for item in result["sources"]], ["Ars Technica", "NASA Breaking News", "Hacker News"])
        self.assertEqual(result["sources"][0]["adapter_type"], "rss_atom")
        self.assertEqual(result["sources"][0]["category"], "AI Watch")


if __name__ == "__main__":
    unittest.main()
