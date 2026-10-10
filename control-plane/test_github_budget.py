import tempfile
import threading
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request

from github_budget import GitHubBudget, GitHubPaused


class Response:
    def __init__(self, headers=None):
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = str(value)
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, _): return b'{"items":[]}'


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1791600000.0
        self.path = Path(self.tmp.name)/'budget.sqlite3'
        self.budget = self.new_budget()
        self.open = Mock(return_value=Response())

    def new_budget(self):
        return GitHubBudget(self.path, clock=lambda:self.now, sleep=lambda _:None)

    def get(self, number, budget=None, headers=None):
        return (budget or self.budget).request(Request(f'https://api.github.com/repos/example/{number}', headers=headers or {}), self.open)

    def test_hour_cap_shared_across_restart_and_clients(self):
        for number in range(45): self.get(number)
        restarted = self.new_budget()
        with self.assertRaisesRegex(GitHubPaused, 'hourly'): self.get(46, restarted)
        self.assertEqual(self.open.call_count, 45)
        self.now += 3601
        self.get(46, restarted)
        self.assertEqual(self.open.call_count, 46)

    def test_day_cap_and_bangkok_day_rollover(self):
        for number in range(45): self.get(number)
        self.now += 3601
        for number in range(45,90): self.get(number)
        self.now += 3601
        with self.assertRaisesRegex(GitHubPaused, 'daily'): self.get(90)
        self.now = self.budget.day_start(self.now)+86401
        self.get(90)

    def test_cache_costs_nothing_even_during_persisted_pause(self):
        first = self.get(1)
        self.open.side_effect = HTTPError('url',429,'Rate limit',{'Retry-After':'300'},None)
        with self.assertRaises(GitHubPaused): self.get(2)
        self.assertEqual(self.get(1,self.new_budget()), first)
        with self.assertRaises(GitHubPaused): self.get(3,self.new_budget())
        self.assertEqual(self.open.call_count, 2)
        self.assertIn('paused',self.budget.status()['message'])
        self.now += 301
        self.open.side_effect = None
        self.get(3)

    def test_cache_expires_and_never_crosses_auth_identities(self):
        self.get(1, headers={'Authorization':'Bearer test-a'})
        self.get(1, headers={'Authorization':'Bearer test-b'})
        self.get(1)
        self.get(1)
        self.assertEqual(self.open.call_count, 3)
        self.now += 86401
        self.get(1)
        self.assertEqual(self.open.call_count, 4)
        self.assertNotIn(b'test-a', self.path.read_bytes())

    def test_server_allowance_reserve_prevents_next_call(self):
        self.open.return_value = Response({'X-RateLimit-Remaining':10,'X-RateLimit-Reset':int(self.now+600)})
        self.get(1)
        with self.assertRaisesRegex(GitHubPaused, 'reserve'): self.get(2,self.new_budget())
        self.assertEqual(self.open.call_count,1)
        self.assertIn('allowance is low',self.budget.status()['message'])

    def test_primary_limit_pauses_until_reset_not_short_retry(self):
        self.open.side_effect = HTTPError('url',403,'Rate limit',{'X-RateLimit-Remaining':'0','X-RateLimit-Reset':str(int(self.now+500))},None)
        with self.assertRaises(GitHubPaused): self.get(1)
        self.now += 300
        with self.assertRaises(GitHubPaused): self.get(2,self.new_budget())
        self.assertEqual(self.open.call_count,1)

    def test_search_has_separate_minute_pacing_cap(self):
        for number in range(6):
            self.budget.request(Request(f'https://api.github.com/search/repositories?q={number}'),self.open)
        with self.assertRaisesRegex(GitHubPaused,'pacing'):
            self.budget.request(Request('https://api.github.com/search/repositories?q=7'),self.open)
        self.assertEqual(self.open.call_count,6)

    def test_daily_discovery_attempt_persists_and_rolls_over(self):
        self.budget.begin_discovery()
        with self.assertRaisesRegex(GitHubPaused,'already requested'): self.new_budget().begin_discovery()
        self.now = self.budget.day_start(self.now)+86401
        self.new_budget().begin_discovery()

    def test_warning_arrives_before_local_cap(self):
        for number in range(30): self.get(number)
        self.assertIn('Approaching',self.budget.status()['message'])

    def test_unavailable_budget_storage_fails_before_network(self):
        self.path.write_text('not a directory', encoding='utf-8')
        invalid = GitHubBudget(self.path/'impossible.sqlite3', sleep=lambda _:None)
        with self.assertRaises(OSError):
            self.get(1,invalid)
        self.open.assert_not_called()

    def test_concurrent_requests_cannot_overrun_last_slot(self):
        for number in range(44): self.get(number)
        outcomes = []
        def call(number):
            try:
                self.get(number)
                outcomes.append('sent')
            except GitHubPaused:
                outcomes.append('blocked')
        threads = [threading.Thread(target=call,args=(n,)) for n in (45,46)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertCountEqual(outcomes,['sent','blocked'])
        self.assertEqual(self.open.call_count,45)


if __name__ == '__main__': unittest.main()
