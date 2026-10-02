import html
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from search_providers import SearchProviderRegistry, compact_source_query, default_search_providers


FIXTURE_PATH = Path(__file__).parent / "test-fixtures" / "article-followup-08e15e7d1a4748ecbd8fa930c913d87c.json"


def result_html(results, provider):
    blocks = []
    for item in results:
        link = f'<a href="{html.escape(item["url"], quote=True)}">{html.escape(item["title"])}</a>'
        snippet = html.escape(item["snippet"])
        if provider == "searxng":
            blocks.append(f'<article class="result"><h3>{link}</h3><p class="content">{snippet}</p></article>')
        else:
            blocks.append(f'<li class="b_algo"><h2>{link}</h2><p>{snippet}</p></li>')
    return ("<html><body><ol>" + "".join(blocks) + "</ol></body></html>").encode()


class SearchProviderRelevanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        article = self.fixture["document_workspace"]["documents"][0]
        self.query = compact_source_query(article["metadata"]["title"],
            "South Korea's President Lee Jae Myung has threatened action if Ukraine does not apologise "
            "for revealing that two North Korean prisoners of war had been sent to the South.",
            self.fixture["followup"])
        self.relevant = self.fixture["relevant_search_results"]
        self.irrelevant = self.fixture["irrelevant_search_results"]
        self.registry = SearchProviderRegistry(default_search_providers())

    def test_relevant_searxng_results_accepted_and_generic_pages_never_fetched(self):
        def request(url, **kwargs):
            if url.startswith("http://192.168.1.200:8082/search"):
                return result_html([*self.irrelevant, *self.relevant], "searxng"), "utf-8"
            self.assertEqual(url, self.relevant[0]["url"])
            return b"<html><body><p>Relevant reporting.</p></body></html>", "utf-8"

        with patch("search_providers._request", side_effect=request) as requests:
            result = self.registry.search(self.query, limit=2, fetch_limit=1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "searxng")
        self.assertEqual([r["url"] for r in result["results"]], [r["url"] for r in self.relevant[:2]])
        self.assertEqual(requests.call_count, 2)

    def test_nonempty_irrelevant_providers_continue_in_existing_order(self):
        def request(url, **kwargs):
            if url.startswith("http://192.168.1.200"):
                return result_html(self.irrelevant, "searxng"), "utf-8"
            if url.startswith("https://en.wikipedia.org/w/api.php"):
                return json.dumps({"results": self.irrelevant}).encode(), "utf-8"
            return result_html([*self.irrelevant, *self.relevant], "bing-html"), "utf-8"

        with patch("search_providers._request", side_effect=request) as requests:
            result = self.registry.search(self.query, limit=5, fetch_limit=0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "bing-html")
        self.assertEqual([r["url"] for r in result["results"]], [r["url"] for r in self.relevant])
        self.assertEqual([call.args[0].split("?")[0] for call in requests.call_args_list],
                         [p.endpoint.split("?")[0] for p in self.registry.compatible()])

    def test_all_irrelevant_providers_return_failure_with_no_results(self):
        with patch("search_providers._request", return_value=(json.dumps({"results": self.irrelevant}).encode(), "utf-8")) as requests:
            result = self.registry.search(self.query, fetch_limit=0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["results"], [])
        self.assertEqual(requests.call_count, 3)

    def test_shared_entities_without_the_event_do_not_establish_relevance(self):
        city_page = {"title": "Seoul South Korea Lee Jae Myung city tourism guide",
                     "url": "https://example.test/seoul-tourism", "snippet": "City government and visitor information."}
        with patch("search_providers._request", return_value=(json.dumps({"results": [city_page]}).encode(), "utf-8")):
            result = self.registry.search(self.query, fetch_limit=0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["results"], [])

    def test_query_compacts_entities_event_and_intent_without_case_specific_rules(self):
        self.assertLessEqual(len(self.query), 200)
        for term in ("South Korea", "Ukraine", "North Korean", "POW", "apologise", "latest developments", "implications"):
            self.assertIn(term, self.query)
        other = compact_source_query("Microsoft announces new battery recycling plant",
                                     "Microsoft has announced a battery recycling plant in London.",
                                     "Research the latest developments and background.")
        for term in ("Microsoft", "London", "battery", "recycling", "latest developments", "background"):
            self.assertIn(term, other)
        self.assertLessEqual(len(other), 200)


if __name__ == "__main__":
    unittest.main()
