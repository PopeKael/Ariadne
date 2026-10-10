import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from rabbit_library import RabbitLibrary, repository_name


def card(i):
    return dict(repository_name=f'owner/project-{i}', description='Local AI tool',
                github_url=f'https://github.com/owner/project-{i}', assessment={'recommendation':'Watch'})


class LibraryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'library.sqlite3'
        self.library = RabbitLibrary(self.path)

    def test_paging_survives_reopen_and_loops_without_losing_cards(self):
        self.library.add([card(i) for i in range(23)])
        self.assertEqual(self.library.view()['total'], 23)
        first = self.library.view()['results']
        second = self.library.view(move='next')['results']
        self.assertFalse({c['repository_name'] for c in first} & {c['repository_name'] for c in second})
        reopened = RabbitLibrary(self.path)
        self.assertEqual(reopened.view()['page'], 2)
        self.assertEqual(len(reopened.view(move='next')['results']), 5)
        self.assertEqual(reopened.view(move='next')['results'], first)
        self.assertEqual(reopened.view(move='previous')['page'], 3)

    def test_decisions_refill_locally_and_rediscovery_never_undoes_dismissal(self):
        self.library.add([card(i) for i in range(10)])
        self.library.decide('OWNER/project-3', 'dismiss')
        self.library.add([card(3)])
        view = RabbitLibrary(self.path).view()
        self.assertEqual(view['total'], 9)
        self.assertEqual(view['dismissed_count'], 1)
        self.assertNotIn('owner/project-3', [c['repository_name'] for c in view['results']])
        self.assertIn('owner/project-9', [c['repository_name'] for c in view['results']])
        self.library.decide(view['undo']['repository_name'], 'undo')
        self.assertEqual(self.library.view()['total'], 10)
        self.assertEqual(self.library.view({'owner/project-3'})['total'], 9)

    def test_import_is_idempotent_and_does_not_replace_newer_direct_check(self):
        self.library.add([card(1)])
        changed = dict(card(1), description='New inspection')
        self.library.add([changed], focus=True)
        self.library.add([card(1)], update=False)
        self.assertEqual(self.library.view()['total'], 1)
        self.assertEqual(self.library.view()['results'][0]['description'], 'New inspection')

    def test_legacy_recovery_uses_only_existing_metadata_and_evidence(self):
        cache = self.path.parent / 'cache.sqlite3'
        with sqlite3.connect(cache) as db:
            db.execute('CREATE TABLE cache(body BLOB)')
            db.execute('INSERT INTO cache VALUES(?)', (json.dumps({'items':[{'full_name':'owner/previous'}]}),))
        db.close()
        with patch('rabbit_assessment.rescore_cached', side_effect=lambda c:dict(c, assessment={'relevant':True,'recommendation':'Watch'})):
            self.library.recover_legacy({'_deck':{'seen':['owner/previous']}}, cache)
        self.assertEqual(self.library.view()['total'], 1)
        self.assertEqual(self.library.view()['results'][0]['repository_name'], 'owner/previous')

    def test_repository_links_cannot_select_other_hosts_or_endpoints(self):
        self.assertEqual(repository_name('https://github.com/Niko1221/Strata.git/'), 'Niko1221/Strata')
        self.assertEqual(repository_name('Niko1221/Strata'), 'Niko1221/Strata')
        for value in ['https://evil.test/a/b','http://github.com/a/b','https://github.com/a/b/issues/1',
                      'https://user:secret@github.com/a/b','https://github.com:8443/a/b','../a','a/b?query=x']:
            with self.subTest(value=value), self.assertRaises(ValueError): repository_name(value)


if __name__ == '__main__': unittest.main()
