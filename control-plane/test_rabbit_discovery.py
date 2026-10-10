import json
import unittest
from unittest.mock import Mock
from rabbit_discovery import advance, visible_result, excluded_names


def card(index):
    return dict(repository_name=f'owner/project-{index}', github_url=f'https://github.com/owner/project-{index}', score=100-index,
                assessment=dict(relevant=True, recommendation='Watch', errors=[], selection_score=10))


class DiscoveryPagingTests(unittest.TestCase):
    def test_next_pages_survive_reload_without_repeating_and_fetch_deeper(self):
        searched = []
        def search(page, report):
            searched.append(page)
            return dict(ok=True, candidates=[card(i) for i in range((page-1)*30,page*30)], search_matches=30)
        previous, shown = {}, set()
        for _ in range(4):
            result = advance(dict(previous=json.loads(json.dumps(previous))), search, lambda c:c, lambda *_:None)
            self.assertTrue(result['ok'])
            self.assertEqual(len(result['results']), 9)
            names = {c['repository_name'] for c in result['results']}
            self.assertFalse(names & shown)
            shown |= names
            previous = result
        self.assertEqual(searched, [1,2])

    def test_saving_replaces_only_one_card_from_reserve_on_every_refresh(self):
        original = dict(results=[card(i) for i in range(9)], _deck=dict(reserve=[card(9)], pending=[], search_page=1))
        excluded = excluded_names([dict(sources=['https://github.com/OWNER/project-3/'],state='active')])
        before = json.dumps(original)
        for _ in range(2):
            view = visible_result(original, excluded)
            self.assertEqual(len(view['results']), 9)
            self.assertNotIn('owner/project-3', [c['repository_name'] for c in view['results']])
            self.assertIn('owner/project-9', [c['repository_name'] for c in view['results']])
        self.assertEqual(json.dumps(original), before)
        search = Mock(side_effect=AssertionError('A reserve card should not need network work'))
        result = advance(dict(previous=original,excluded=excluded,mode='refill'), search, lambda c:c, lambda *_:None)
        self.assertEqual(result['results'], view['results'])
        self.assertFalse(search.called)

    def test_refill_preserves_other_cards_and_next_skips_visible_replacement(self):
        original = dict(results=[card(i) for i in range(9)], _deck=dict(reserve=[card(9)],pending=[card(i) for i in range(10,26)],search_page=1))
        result = advance(dict(previous=original,excluded=['owner/project-3']), Mock(), lambda c:c, lambda *_:None)
        self.assertEqual(len(result['results']), 9)
        self.assertTrue(all(int(c['repository_name'].split('-')[-1]) >= 10 for c in result['results']))
        self.assertIn('owner/project-9', result['_deck']['seen'])
        original['_deck']['reserve'] = []
        result = advance(dict(previous=original,excluded=['owner/project-3'],mode='refill'), Mock(), lambda c:c, lambda *_:None)
        self.assertEqual(len(result['results']), 9)
        self.assertTrue({f'owner/project-{i}' for i in range(9) if i != 3} <= {c['repository_name'] for c in result['results']})

    def test_failed_source_collection_keeps_previous_page_and_cursor(self):
        previous = dict(results=[card(i) for i in range(9)], _deck=dict(reserve=[],pending=[card(11)],search_page=1))
        before = json.dumps(previous)
        def fail(c):
            c['assessment']['errors']=['GitHub HTTP 403']
            return c
        result = advance(dict(previous=previous), Mock(), fail, lambda *_:None)
        self.assertFalse(result['ok'])
        self.assertEqual(json.dumps(previous), before)

    def test_empty_successful_page_advances_but_search_failure_does_not(self):
        empty = lambda *_: dict(ok=True,candidates=[],search_matches=0)
        result = advance({}, empty, Mock(), lambda *_:None)
        self.assertTrue(result['ok'])
        self.assertTrue(visible_result(result,[])['exhausted'])
        failed = advance({}, lambda *_:dict(ok=False,warnings=['rate limited']),Mock(),lambda *_:None)
        self.assertFalse(failed['ok'])


if __name__ == '__main__':
    unittest.main()
