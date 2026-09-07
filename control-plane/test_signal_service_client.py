import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from signal_service_client import SignalServiceClient


class SignalServiceClientTests(unittest.TestCase):
    def test_briefing_normalizes_missing_signals(self):
        client = SignalServiceClient("http://127.0.0.1:8788")
        with patch.object(client, "_get", return_value={"ok": True, "stale": False}):
            result = client.briefing()
        self.assertEqual(result["signals"], [])

    def test_failure_is_contained_as_offline_fallback(self):
        client = SignalServiceClient("http://127.0.0.1:8788")
        with patch("signal_service_client.urllib.request.urlopen", side_effect=URLError("offline")):
            result = client.briefing()
        self.assertFalse(result["ok"])
        self.assertEqual(result["signals"], [])
        self.assertEqual(result["state"], "offline")

    def test_invalid_url_does_not_raise(self):
        client = SignalServiceClient("file:///not-allowed")
        self.assertEqual(client.health()["state"], "offline")


if __name__ == "__main__":
    unittest.main()
