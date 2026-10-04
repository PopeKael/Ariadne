import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from news_briefing_cache import NewsBriefingCache


class RetainedImagesTests(unittest.TestCase):
    def test_retained_card_preparation_preserves_order_feedback_and_excludes_body(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = NewsBriefingCache(cache_path=Path(folder) / 'cards.json')
            cache._briefing = {'articles': [
                {'article_id': 'article-missing', 'title': 'Retained story', 'canonical_url': 'https://example.test/story',
                 'feedback': {'value': 'useful'}, 'position': 1},
                {'article_id': 'article-healthy', 'title': 'Healthy', 'image_cache_url': 'http://cache/healthy.jpg', 'position': 2}]}
            prepared = {'ok': True, 'article_id': 'article-missing', 'prepared_version': 1,
                        'image_cache_url': 'http://cache/lead.webp', 'image_status': 'cached',
                        'extracted_text': 'Full body must remain in preparation storage.'}
            with patch('news_briefing_cache.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(prepared).encode())) as request:
                result = cache.sync_prepared_images()
            self.assertEqual(result, {'attempted': 1, 'updated': 1})
            payload = json.loads(request.call_args.args[0].data)
            self.assertEqual(payload['url'], 'https://example.test/story')
            cards = cache.snapshot()['briefing']['articles']
            self.assertEqual([c['article_id'] for c in cards], ['article-missing', 'article-healthy'])
            self.assertEqual(cards[0]['feedback'], {'value': 'useful'})
            self.assertEqual(cards[0]['image_url'], 'http://cache/lead.webp')
            self.assertNotIn('extracted_text', cards[0])
            with patch('news_briefing_cache.urllib.request.urlopen') as request:
                self.assertEqual(cache.sync_prepared_images()['attempted'], 0)
                request.assert_not_called()

    def test_future_retry_skips_publisher_preparation(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = NewsBriefingCache(cache_path=Path(folder) / 'cards.json')
            cache._briefing = {'articles': [{'article_id': 'article-failed', 'next_retry_at': '2099-01-01T00:00:00+00:00'}]}
            with patch('news_briefing_cache.urllib.request.urlopen') as request:
                self.assertEqual(cache.sync_prepared_images()['attempted'], 0)
                request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
