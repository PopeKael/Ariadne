import unittest

from news_discord_auto import deliver_sync


class AutomaticDeliveryDisabledTests(unittest.TestCase):
    def test_ariadne_auto_sender_fails_closed(self):
        calls = []
        result = deliver_sync('unused', ['youtube:ABCDEFGHIJK'],
                              store_factory=lambda: calls.append('created'))
        self.assertEqual(result, {
            'eligible': 0, 'sent': 0, 'failed': 0,
            'dry_run': False, 'disabled': True,
        })
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
