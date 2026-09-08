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
        client = SignalServiceClient("http://127.0.0.1:8788", diagnostics_path=self.diagnostics_path)
        with patch.object(client, "_get", return_value={"ok": True, "stale": False}):
            result = client.briefing()
        self.assertEqual(result["signals"], [])

    def test_failure_is_contained_as_offline_fallback(self):
        client = SignalServiceClient("http://127.0.0.1:8788", diagnostics_path=self.diagnostics_path)
        with patch("signal_service_client.urllib.request.urlopen", side_effect=URLError("offline")):
            result = client.briefing()
        self.assertFalse(result["ok"])
        self.assertEqual(result["signals"], [])
        self.assertEqual(result["state"], "offline")

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

        client = SignalServiceClient("http://127.0.0.1:8788", diagnostics_path=self.diagnostics_path)
        with patch("signal_service_client.urllib.request.urlopen", return_value=Response()):
            result = client.health()
        self.assertEqual(result["state"], "healthy")
        events = [json.loads(line) for line in self.diagnostics_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([event["event_type"] for event in events], ["SIGNAL_SERVICE_REQUEST_STARTED", "SIGNAL_SERVICE_REQUEST_SUCCEEDED"])
        self.assertTrue(events[0]["timestamp"])
        self.assertEqual(events[0]["data"]["path"], "/v1/health")
        self.assertEqual(events[1]["data"]["state"], "healthy")


if __name__ == "__main__":
    unittest.main()
