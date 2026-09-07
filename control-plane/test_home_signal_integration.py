import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


class HomeSignalIntegrationTests(unittest.TestCase):
    def test_today_renders_cached_signal_with_source_url(self):
        fake_client = Mock()
        fake_client.briefing.return_value = {"ok": True, "stale": True, "signals": [{"title": "A signal", "summary": "A concise summary", "source_name": "Example", "published_at": "2026-09-07T12:34:00+07:00", "url": "https://example.test/story"}]}
        with patch.object(server, "SIGNAL_SERVICE_CLIENT", fake_client):
            result = server.home_today_payload({"services": []})
        self.assertEqual(result[0]["label"], "A signal")
        self.assertEqual(result[0]["url"], "https://example.test/story")
        self.assertEqual(result[0]["summary"], "A concise summary")
        self.assertEqual(result[0]["source"], "Example")
        self.assertEqual(result[0]["published_at"], "2026-09-07T12:34:00+07:00")
        self.assertTrue(result[0]["detail"].startswith("Cached · Example"))

    def test_today_ignores_unavailable_signal_service_without_breaking_local_status(self):
        fake_client = Mock()
        fake_client.briefing.return_value = {"ok": False, "stale": True, "signals": []}
        with patch.object(server, "SIGNAL_SERVICE_CLIENT", fake_client):
            result = server.home_today_payload({"services": [{"name": "Ollama", "state": "offline", "detail": "Unavailable"}]})
        self.assertEqual(result[0]["label"], "Ollama")
        self.assertEqual(result[0]["tone"], "offline")


if __name__ == "__main__":
    unittest.main()
