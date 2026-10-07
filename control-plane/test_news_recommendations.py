import tempfile
import unittest
from pathlib import Path
from news_recommendations import NewsRecommendations


def card(key, title, source="A", score=80):
    return dict(article_id=key, title=title, summary="", source=source,
                category=source, briefing_score=score, content_ready=1)


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "journal.sqlite3"
        self.store = NewsRecommendations(self.path)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_changes_clears_retries_and_restart(self):
        item = card("a", "Local AI inference hardware")
        self.store.observe([item])
        self.store.react("a", "useful", "one")
        self.store.react("a", "interesting", "two")
        self.store.react("a", "interesting", "two")
        self.assertEqual(self.store.profile()["rated_articles"], 1)
        self.store.react("a", "", "three")
        self.assertEqual(self.store.react("a", "useful", "one")["feedback"], "")
        self.store.db.close()
        self.store = NewsRecommendations(self.path)
        item["feedback"] = {"value": "useful"}
        result = self.store.rank([item])[0]
        self.assertEqual(result["feedback"]["value"], "")
        self.assertEqual(result["interaction_state"], "consumed")
        self.assertEqual(self.store.profile()["rated_articles"], 0)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 3)

    def test_unseen_first_and_rating_never_boosts_itself(self):
        items = [card("a", "GPU local inference benchmarks", score=100), card("b", "Garden planting", score=20)]
        configured = [{"name": "Local inference", "aliases": ["GPU"], "priority": 1}]
        before = self.store.rank(items, configured)[0]["local_rank_score"]
        self.store.react("a", "interesting", "heart")
        ranked = self.store.rank(items, configured)
        self.assertEqual([i["article_id"] for i in ranked], ["b", "a"])
        self.assertEqual(ranked[1]["local_rank_score"], before)
        self.assertEqual(self.store.profile(configured)["interests"][0]["evidence_count"], 1)

    def test_negative_does_not_penalize_country_or_source(self):
        items = [card("a", "France riots violent street clashes"), card("b", "France gardens spring flowers")]
        baseline = {i["article_id"]: i["local_rank_score"] for i in self.store.rank(items)}
        self.store.react("a", "not_useful", "negative")
        self.assertEqual(self.store.rank(items)[0]["local_rank_score"], baseline["b"])
        self.assertEqual(self.store.profile()["interests"], [])

    def test_related_learning_is_bounded_and_silence_is_neutral(self):
        items = [card("a", "GPU inference memory benchmark"), card("b", "GPU inference memory performance")]
        baseline = self.store.rank(items)[1]["local_rank_score"]
        self.store.react("a", "interesting", "heart")
        related = next(i for i in self.store.rank(items) if i["article_id"] == "b")
        self.assertGreater(related["local_rank_score"], baseline)
        self.assertLess(related["local_rank_score"] - baseline, 8)
        self.assertEqual(self.store.profile()["rated_articles"], 1)

    def test_diversity_and_retained_seen_archive(self):
        items = [card("a", "Solar power battery storage"), card("b", "Solar power battery storage investment"),
                 card("c", "Wildlife garden birds", source="B")]
        self.assertEqual(self.store.rank(items)[1]["article_id"], "c")
        self.store.opened("a")
        self.store.rank([items[2]])
        self.assertEqual(self.store.seen_cards()[0]["article_id"], "a")
        self.assertEqual(self.store.profile()["rated_articles"], 0)

    def test_cached_semantic_interest_uses_real_signal_contract(self):
        configured = [{"name": "Robotics", "priority": 2}]
        item = card("a", "Unusual research report")
        item["semantic_matches"] = [{"interest": "Robotics", "semantic_score": .8}]
        self.store.rank([item], configured)
        self.store.react("a", "interesting", "heart")
        self.store.observe([card("a", "Unusual research report")])
        self.assertEqual(self.store.profile(configured)["interests"][0]["label"], "Robotics")
        future = card("b", "Machines navigating autonomously")
        future["semantic_matches"] = [{"interest": "Robotics", "semantic_score": .8}]
        ranked = self.store.rank([item, future], configured)
        self.assertGreater(ranked[0]["local_rank_score"], 92)

    def test_priority_full_range_and_current_settings_override_cached_priority(self):
        item = card("a", "Unexpected research")
        item["semantic_matches"] = [{"interest_id": "custom", "interest": "Old name",
                                     "semantic_score": .8, "priority": 5}]
        scores = []
        for priority in (0, .5, 1, 2, 3, 4, 5):
            settings = [{"interest_id": "custom", "name": "My subject", "priority": priority}]
            result = self.store.rank([item, card("b", "Wider coverage", score=95)], settings)
            scores.append(next(c["interest_rank_score"] for c in result if c["article_id"] == "a"))
            self.assertEqual(result[0]["article_id"], "a" if priority >= 2 else "b")
        self.assertEqual(scores, [0, 4.8, 9.6, 19.2, 28.8, 38.4, 48])

    def test_disabled_deleted_and_semantic_disabled_interests_ignore_stale_matches(self):
        item = card("a", "Custom pottery")
        item["semantic_matches"] = [{"interest_id": "custom", "interest": "Ceramics",
                                     "semantic_score": .9, "priority": 5}]
        setting = {"interest_id": "custom", "name": "Ceramics", "aliases": ["pottery"], "priority": 5}
        self.assertEqual(self.store.rank([item], [{**setting, "enabled": False}])[0]["interest_rank_score"], 0)
        self.assertEqual(self.store.rank([item], [])[0]["interest_rank_score"], 0)
        semantic_only = {**item, "title": "Unexpected research"}
        self.assertEqual(self.store.rank([semantic_only], [{**setting, "semantic_enabled": False}])[0]["interest_rank_score"], 0)
        self.assertEqual(self.store.rank([item], [{**setting, "semantic_enabled": False}])[0]["interest_rank_score"], 60)

    def test_arbitrary_aliases_and_no_implicit_personal_topics(self):
        items = [card("a", "Orchid propagation techniques"), card("b", "Trump Thailand local AI")]
        settings = [{"name": "My greenhouse", "description": "Growing exotic plants", "aliases": ["orchid"], "priority": 3}]
        result = self.store.rank(items, settings)
        self.assertEqual(result[0]["interest_rank_score"], 36)
        self.assertEqual(result[1]["interest_rank_score"], 0)
        self.store.react("a", "interesting", "orchids")
        self.assertEqual(self.store.profile(settings)["interests"][0]["label"], "My greenhouse")
        self.assertEqual(self.store.profile([{**settings[0], "enabled": False}])["interests"], [])

    def test_high_priority_cannot_crowd_out_other_publishers(self):
        items = [card(str(i), f"Orchid bulletin {i}", source="A") for i in range(20)]
        items += [card("b" + str(i), f"Independent coverage {i}", source="B", score=70) for i in range(10)]
        selected = self.store.rank(items, [{"name": "Orchid", "priority": 5}], limit=10)
        self.assertEqual(len(selected), 10)
        self.assertLessEqual(sum(c["source"] == "A" for c in selected), 5)
        self.assertTrue(any(c["interest_rank_score"] == 0 for c in selected))


if __name__ == "__main__":
    unittest.main()
