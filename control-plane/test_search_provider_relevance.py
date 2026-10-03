import html
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

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

    def test_incidental_query_words_in_a_biography_do_not_establish_its_subject(self):
        query = "Thailand flood insurance commercial buildings"
        biography = {"title": "Jackie Chan", "url": "https://en.wikipedia.org/wiki/Jackie_Chan",
            "snippet": "His foundation supports flood relief in Thailand; commercial buildings and insurance were mentioned in his career."}
        relevant = {"title": "Commercial flood insurance in Thailand", "url": "https://example.test/insurance",
                    "snippet": "Property insurance for commercial buildings covers specified flood damage."}
        with patch("search_providers._request", return_value=(json.dumps({"results": [biography, relevant]}).encode(), "utf-8")):
            result = self.registry.search(query, fetch_limit=0)
        self.assertEqual([r["url"] for r in result["results"]], [relevant["url"]])
        self.assertEqual(result["attempts"][0]["rejection_counts"], {"topical_mismatch": 1})

    def test_wrong_country_topic_matches_continue_to_next_provider(self):
        query = "Thailand business flood insurance coverage claims process"
        wrong = [
            {"title": "Vehicle insurance", "url": "https://en.wikipedia.org/wiki/Vehicle_insurance",
             "snippet": "The claims of auto insurance in India can be accidental, theft claims or third party claims."},
            {"title": "2015 South India floods", "url": "https://en.wikipedia.org/wiki/2015_South_India_floods",
             "snippet": "Flood claims and insurance losses in Chennai were reported by insurers."},
        ]
        relevant = {"title": "Business flood insurance", "url": "https://example.test/property",
                    "snippet": "Thailand commercial property insurance covers specified flood damage and business interruption claims."}
        with patch("search_providers._request", side_effect=[
                (b'<html>No results found</html>', 'utf-8'),
                (json.dumps({'results': wrong}).encode(), 'utf-8'),
                (result_html([relevant], 'bing-html'), 'utf-8')]):
            result = self.registry.search(query, fetch_limit=0)
        self.assertEqual(result['provider'], 'bing-html')
        self.assertEqual([r['url'] for r in result['results']], [relevant['url']])
        self.assertEqual(result['attempts'][1]['rejection_counts'], {'topical_mismatch': 2})

    def test_diagnostics_distinguish_empty_response_from_relevance_rejection(self):
        bodies = [b'<html><body>No results were found. Google: CAPTCHA</body></html>',
                  json.dumps({"results": self.irrelevant}).encode(),
                  result_html(self.irrelevant, "bing-html")]
        responses = []
        for body in bodies:
            response = MagicMock()
            response.__enter__.return_value = response
            response.status = 200
            response.read.return_value = body
            response.headers.get_content_charset.return_value = "utf-8"
            response.headers.get_content_type.return_value = "text/html"
            responses.append(response)
        # Exercise the real request boundary, not a mocked status value.
        with patch("search_providers.urlopen", side_effect=responses) as requests:
            result = self.registry.search(self.query, fetch_limit=0)
        self.assertFalse(result["ok"])
        self.assertEqual(requests.call_count, 3)  # no retry
        attempts = result["attempts"]
        self.assertEqual([a["provider_id"] for a in attempts], ["searxng", "wikipedia-api", "bing-html"])
        self.assertTrue(all(a["http_status"] == 200 for a in attempts))
        self.assertEqual(attempts[0]["response_classification"], "html_no_results")
        self.assertEqual(attempts[0]["parsed_candidate_count"], 0)
        self.assertEqual(attempts[0]["outcome"], "zero_parsed_results")
        self.assertEqual(attempts[0]["rejections"], [])
        for attempt, classification in zip(attempts[1:], ("json", "html")):
            self.assertEqual(attempt["response_classification"], classification)
            self.assertEqual(attempt["parsed_candidate_count"], len(self.irrelevant))
            self.assertEqual(attempt["accepted_result_count"], 0)
            self.assertEqual(attempt["outcome"], "no_accepted_results")
            self.assertEqual(attempt["rejection_counts"], {"topical_mismatch": len(self.irrelevant)})

    def test_diagnostics_are_bounded_and_record_success_and_http_failure(self):
        irrelevant = [{"title": "Tourism " + "x" * 1000,
                       "url": "https://example.test/" + "x" * 1000 + str(i),
                       "snippet": "City guide"} for i in range(20)]
        with patch("search_providers._request", side_effect=[
                HTTPError("https://example.test", 429, "Too Many Requests", {}, None),
                (json.dumps({"results": irrelevant}).encode(), "utf-8"),
                (result_html(self.relevant, "bing-html"), "utf-8")]):
            result = self.registry.search(self.query, limit=2, fetch_limit=0)
        self.assertTrue(result["ok"])
        failure, rejection, success = result["attempts"]
        self.assertEqual(failure["http_status"], 429)
        self.assertEqual(failure["response_classification"], "http_error")
        self.assertEqual(len(rejection["rejections"]), 8)
        self.assertEqual(rejection["rejection_counts"], {"topical_mismatch": 20})
        self.assertTrue(all(len(r["title"]) <= 160 and len(r["url"]) <= 240 for r in rejection["rejections"]))
        self.assertEqual(success["parsed_candidate_count"], len(self.relevant))
        self.assertEqual(success["accepted_result_count"], 2)
        self.assertEqual(success["evaluated_candidate_count"], 2)
        self.assertEqual(success["outcome"], "accepted")

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

    def test_interpreted_query_preserves_task_terms_and_has_existing_bounds(self):
        query = "Thailand commercial property flood insurance business interruption coverage"
        self.assertEqual(compact_source_query("Unrelated article title", "", "Why like this?", task_query=query), query)
        comparison = "Chiang Mai versus other northern Thailand cities technology business location advantages"
        self.assertEqual(compact_source_query("New investment", "", "Why there?", task_query=comparison), comparison)
        bounded = compact_source_query("", "", "", task_query="word " * 100)
        self.assertLessEqual(len(bounded), 200)
        self.assertLessEqual(len(bounded.split()), 30)
        self.assertNotIn("\n", compact_source_query("", "", "", task_query="flood\ninsurance"))


if __name__ == "__main__":
    unittest.main()
