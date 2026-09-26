import tempfile
import unittest
from pathlib import Path

from signal_service.inference import Provider
from signal_service.service import SignalService


class FakeInference:
    def __init__(self):
        self.provider = Provider("fake-embeddings", "test", "semantic-v1", "", "", ("embeddings",), "nas", True, 1, "test-v1")
        self.calls = []

    def route(self, task):
        return self.provider if task == "embedding" else None

    def state(self, provider):
        return "Configured"

    def embed_many(self, texts, provider):
        self.calls.append(list(texts))
        vectors = []
        for text in texts:
            folded = text.casefold()
            if "local ai hardware" in folded or "amd strix halo" in folded or "compact silicon" in folded:
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.0, 1.0])
        return vectors

    def snapshot(self):
        return {"version": 1, "providers": [dict(self.provider.as_dict(), state="Configured", credential_state="Configured")], "routes": {"embedding": {"provider_id": self.provider.provider_id, "model_id": self.provider.model_id, "location": "nas", "state": "Configured", "compatible_provider_ids": [self.provider.provider_id]}}}


class BoundedInference(FakeInference):
    def __init__(self, batch_limit):
        super().__init__()
        self.batch_limit = batch_limit

    def embed_many(self, texts, provider):
        if len(texts) > self.batch_limit:
            raise AssertionError(f"batch exceeded test limit: {len(texts)}")
        return super().embed_many(texts, provider)


class AdaptiveSignalServiceTests(unittest.TestCase):
    def test_builtin_source_migration_is_idempotent_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "signals.sqlite3"
            first = SignalService(path, feeds=None)
            try:
                first.ingest_candidates([{"title": "Historical item", "url": "https://example.test/history", "summary": "Retained historical signal content."}], default_source_name="Example", default_category="AI Watch")
                names = [item["name"] for item in first.sources()]
            finally:
                first.close()
            second = SignalService(path, feeds=None)
            try:
                self.assertEqual(names, ["Ars Technica", "Hacker News", "NASA Breaking News"])
                self.assertEqual([item["name"] for item in second.sources()], names)
                self.assertEqual(len(second.store.recent(limit=10)), 1)
                self.assertEqual(second.feeds[0].name, "Ars Technica")
            finally:
                second.close()

    def test_semantic_interest_matching_is_persisted_and_not_recomputed_when_text_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeInference()
            service = SignalService(Path(temporary) / "signals.sqlite3", feeds=[], inference=fake)
            try:
                interest = service.upsert_interest({"name": "Local AI hardware", "description": "AMD Strix Halo and practical local inference hardware"})
                result = service.ingest_candidates([{"title": "Compact silicon for useful local models", "url": "https://example.test/compact", "summary": "A compact silicon platform for running useful models."}], default_source_name="Example", default_category="AI Watch")
                signal = result["briefing"]["signals"][0]
                self.assertEqual(signal["semantic_matches"][0]["interest"], interest["name"])
                self.assertIn("semantic", signal["why_appeared"])
                call_count = len(fake.calls)
                service.ingest_candidates([{"title": "Compact silicon for useful local models", "url": "https://example.test/compact", "summary": "A compact silicon platform for running useful models."}], default_source_name="Example", default_category="AI Watch")
                self.assertEqual(len(fake.calls), call_count)
            finally:
                service.close()

    def test_semantic_embedding_requests_are_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            fake = BoundedInference(batch_limit=2)
            service = SignalService(Path(temporary) / "signals.sqlite3", feeds=[], inference=fake)
            try:
                service.EMBEDDING_BATCH_SIZE = 2
                service.upsert_interest({"name": "Local AI hardware", "description": "Local AI hardware"})
                candidates = [
                    {"title": f"Local model report {index}", "url": f"https://example.test/{index}", "summary": "Practical local AI hardware", "image_url": "https://example.test/image.png"}
                    for index in range(5)
                ]
                service.ingest_candidates(candidates, default_source_name="Example")
                self.assertEqual(service._semantic_status["state"], "healthy")
                self.assertTrue(all(len(call) <= 2 for call in fake.calls))
                self.assertGreaterEqual(len(fake.calls), 3)
            finally:
                service.close()

    def test_feedback_is_bounded_persistent_and_changes_live_rank_reason(self):
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeInference()
            path = Path(temporary) / "signals.sqlite3"
            service = SignalService(path, feeds=[], inference=fake)
            try:
                service.ingest_candidates([
                    {"title": "Compact silicon for useful local models", "url": "https://example.test/one", "summary": "Compact silicon for local models."},
                    {"title": "Unrelated gardening report", "url": "https://example.test/two", "summary": "A report about soil and gardening."},
                ], default_source_name="Example", default_category="AI Watch")
                ids = [item["signal_id"] for item in service.briefing()["signals"]]
                service.record_feedback(ids[0], "useful")
                profile = service.store.learned_preferences()
                self.assertTrue(any(item["label"] == "Example" and item["score"] > 0 for item in profile["sources"]))
                self.assertIn("learned preference", service.briefing()["signals"][0]["why_appeared"])
            finally:
                service.close()
            reopened = SignalService(path, feeds=[], inference=fake)
            try:
                self.assertTrue(reopened.store.learned_preferences()["sources"])
            finally:
                reopened.close()

    def test_interest_repeated_clicks_are_idempotent_and_survive_reload(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "signals.sqlite3"
            service = SignalService(path, feeds=[])
            first = service.upsert_interest({"name": " Local AI hardware ", "description": "GPUs and local inference"})
            second = service.upsert_interest({"name": "Local  AI hardware", "description": "Updated local model hardware"})
            self.assertEqual(first["interest_id"], second["interest_id"])
            self.assertEqual(len(service.interests()), 1)
            self.assertEqual(service.interests()[0]["description"], "Updated local model hardware")
            service.close()

            reopened = SignalService(path, feeds=[])
            try:
                self.assertEqual(len(reopened.interests()), 1)
                self.assertEqual(reopened.interests()[0]["interest_id"], first["interest_id"])
            finally:
                reopened.close()

    def test_semantic_text_excludes_source_and_category_metadata(self):
        signal = type("Signal", (), {"title": "A general story", "summary": "A summary", "content": "Article content", "source_name": "Hacker News", "category": "AI Watch"})()
        text = SignalService._signal_text(signal)
        self.assertNotIn("Hacker News", text)
        self.assertNotIn("AI Watch", text)
        self.assertIn("Article content", text)

    def test_semantic_threshold_and_ai_watch_projection_filter_unrelated_items(self):
        class ThresholdInference(FakeInference):
            def embed_many(self, texts, provider):
                self.calls.append(list(texts))
                vectors = []
                for text in texts:
                    folded = text.casefold()
                    if "local ai hardware" in folded:
                        vectors.append([1.0, 0.0])
                    elif "compact silicon" in folded:
                        vectors.append([0.8, 0.6])
                    else:
                        vectors.append([0.5, 0.866])
                return vectors

        with tempfile.TemporaryDirectory() as temporary:
            fake = ThresholdInference()
            service = SignalService(Path(temporary) / "signals.sqlite3", feeds=[], inference=fake)
            try:
                service.upsert_interest({"name": "Local AI hardware", "description": "Local AI hardware"})
                service.ingest_candidates([
                    {"title": "Compact silicon for useful local models", "url": "https://example.test/relevant", "summary": "Compact silicon", "category": "AI Watch"},
                    {"title": "A general story", "url": "https://example.test/unrelated", "summary": "A report about gardening", "category": "AI Watch"},
                ], default_source_name="Hacker News", default_category="AI Watch")
                signals = {item["url"]: item for item in service.briefing()["signals"]}
                self.assertEqual(signals["https://example.test/relevant"]["semantic_matches"][0]["interest"], "Local AI hardware")
                self.assertEqual(signals["https://example.test/relevant"]["category"], "AI Watch")
                self.assertEqual(signals["https://example.test/unrelated"]["semantic_matches"], [])
                self.assertEqual(signals["https://example.test/unrelated"]["category"], "Main News Feed")
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
