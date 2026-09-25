from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from news_briefing_cache import NewsBriefingCache


def briefing(result_hash: str, *, generated_at: str = "2026-09-25T10:00:00+00:00") -> dict:
    return {
        "ok": True,
        "briefing_id": "top100",
        "generated_at": generated_at,
        "candidate_count": 1,
        "article_count": 1,
        "input_hash": f"input-{result_hash}",
        "result_hash": result_hash,
        "run_count": 4,
        "elapsed_ms": 2.1,
        "articles": [{
            "position": 1,
            "article_id": "article-test-1",
            "title": "A cached news card",
            "source": "Example News",
            "canonical_url": "https://example.test/story",
            "summary": "A short card summary.",
            "content_ready": 1,
            "markdown_path": "/data/articles/article-test-1.md",
            "markdown": "This body must not be included in the local card cache.",
        }],
    }


class NewsBriefingCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "runtime" / "news-briefing.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_cold_local_read_is_immediate_and_never_contacts_hera(self):
        expected = briefing("hash-a")
        seeder = NewsBriefingCache("http://hera.test:8791", cache_path=self.path)
        with patch.object(seeder, "_request_briefing", return_value=expected):
            self.assertTrue(seeder.sync_once()["changed"])

        with patch("news_briefing_cache.urllib.request.urlopen", side_effect=AssertionError("Hera contacted during local read")) as fetch:
            started = time.perf_counter()
            restarted = NewsBriefingCache("http://hera.test:8791", cache_path=self.path)
            response = restarted.snapshot()
            read_ms = (time.perf_counter() - started) * 1000
        fetch.assert_not_called()
        self.assertTrue(response["available"])
        self.assertEqual(response["source"], "local_snapshot")
        self.assertEqual(response["briefing"]["result_hash"], "hash-a")
        self.assertEqual(response["briefing"]["articles"][0]["article_id"], "article-test-1")
        self.assertNotIn("markdown", response["briefing"]["articles"][0])
        self.assertLess(read_ms, 100)
        response["briefing"]["articles"][0]["title"] = "Mutated caller copy"
        self.assertEqual(restarted.snapshot()["briefing"]["articles"][0]["title"], "A cached news card")

    def test_background_sync_atomically_updates_only_changed_version(self):
        first = briefing("hash-a")
        second = briefing("hash-b", generated_at="2026-09-25T10:05:00+00:00")
        client = NewsBriefingCache("http://hera.test:8791", cache_path=self.path)
        with patch.object(client, "_request_briefing", return_value=first):
            self.assertEqual(client.sync_once()["state"], "updated")
        prior_bytes = self.path.read_bytes()
        replaced = threading.Event()
        real_replace = __import__("os").replace

        def checked_replace(source, destination):
            self.assertEqual(Path(destination), self.path)
            self.assertEqual(Path(source).parent, self.path.parent)
            self.assertEqual(self.path.read_bytes(), prior_bytes)
            real_replace(source, destination)
            replaced.set()

        with patch.object(client, "_request_briefing", return_value=second), \
                patch("news_briefing_cache.os.replace", side_effect=checked_replace):
            self.assertTrue(client.start_background_sync())
            self.assertTrue(replaced.wait(3), "background sync did not atomically replace the snapshot")
            client.stop_background_sync()
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["result_hash"], "hash-b")
        self.assertEqual(client.snapshot()["briefing"]["result_hash"], "hash-b")
        self.assertEqual(client.snapshot()["sync"]["state"], "updated")

    def test_unchanged_hash_does_not_rewrite_even_if_generated_at_advances(self):
        old = briefing("hash-a")
        newer_timestamp_same_content = briefing("hash-a", generated_at="2026-09-25T10:30:00+00:00")
        client = NewsBriefingCache("http://hera.test:8791", cache_path=self.path)
        with patch.object(client, "_request_briefing", return_value=old):
            client.sync_once()
        prior_bytes = self.path.read_bytes()
        prior_mtime = self.path.stat().st_mtime_ns
        with patch.object(client, "_request_briefing", return_value=newer_timestamp_same_content), \
                patch("news_briefing_cache.os.replace") as replace:
            result = client.sync_once()
        replace.assert_not_called()
        self.assertEqual(result["state"], "unchanged")
        self.assertFalse(result["changed"])
        self.assertEqual(self.path.read_bytes(), prior_bytes)
        self.assertEqual(self.path.stat().st_mtime_ns, prior_mtime)

    def test_hera_failure_preserves_valid_local_snapshot(self):
        value = briefing("hash-a")
        client = NewsBriefingCache("http://hera.test:8791", cache_path=self.path)
        with patch.object(client, "_request_briefing", return_value=value):
            client.sync_once()
        prior_bytes = self.path.read_bytes()
        with patch.object(client, "_request_briefing", side_effect=URLError("blocked for test")):
            result = client.sync_once()
        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(self.path.read_bytes(), prior_bytes)
        local = client.snapshot()
        self.assertTrue(local["available"])
        self.assertEqual(local["briefing"]["result_hash"], "hash-a")
        self.assertEqual(local["sync"]["state"], "unavailable")

    def test_remote_sync_requests_briefing_only_and_saves_card_metadata(self):
        value = briefing("hash-a")
        client = NewsBriefingCache("http://hera.test:8791", cache_path=self.path)
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self, _limit):
                return json.dumps(value).encode("utf-8")

        with patch("news_briefing_cache.urllib.request.urlopen", return_value=Response()) as fetch:
            result = client.sync_once()
        fetch.assert_called_once()
        request = fetch.call_args.args[0]
        self.assertEqual(request.full_url, "http://hera.test:8791/briefing")
        self.assertEqual(result["state"], "updated")
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn("markdown", saved["articles"][0])

    def test_feedback_is_atomically_persisted_locally_without_changing_card_order(self):
        value = briefing("hash-a")
        value["articles"][0]["article_id"] = "article-test-1"
        second = dict(value["articles"][0], article_id="article-test-2", title="Second card")
        value["articles"].append(second)
        client = NewsBriefingCache("http://hera.test:8791", cache_path=self.path)
        with patch.object(client, "_request_briefing", return_value=value):
            client.sync_once()
        previous_fingerprint = client.snapshot()["briefing_hash"]
        self.assertTrue(client.update_article_feedback("article-test-1", "interesting", "2026-09-25T12:00:00Z"))
        local = client.snapshot()
        self.assertNotEqual(local["briefing_hash"], previous_fingerprint)
        self.assertEqual([card["article_id"] for card in local["briefing"]["articles"]],
                         ["article-test-1", "article-test-2"])
        self.assertEqual(local["briefing"]["articles"][0]["feedback"]["value"], "interesting")
        restarted = NewsBriefingCache("http://127.0.0.1:1", cache_path=self.path)
        self.assertEqual(restarted.snapshot()["briefing"]["articles"][0]["feedback"]["value"], "interesting")


if __name__ == "__main__":
    unittest.main()
