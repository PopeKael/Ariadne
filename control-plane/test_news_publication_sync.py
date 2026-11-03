import sqlite3
import tempfile
import unittest
import json
from unittest.mock import patch
from contextlib import closing
from pathlib import Path

from news_publication_sync import import_snapshot, registry_snapshot, pull_snapshot
from news_service import NewsService

CID = 'UC' + 'a' * 22


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'news.sqlite3'
        db = sqlite3.connect(self.path)
        db.executescript('''
          CREATE TABLE sources(id TEXT PRIMARY KEY,kind TEXT,name TEXT,friendly_name TEXT,destination TEXT,enabled INTEGER);
          CREATE TABLE news_checkpoints(source_id TEXT PRIMARY KEY,video_id TEXT,published_at TEXT,scanned_at TEXT);
          CREATE TABLE publications(id TEXT PRIMARY KEY,source_id TEXT,original_url TEXT,creator TEXT,title TEXT,
            published_at TEXT,summary TEXT,image_url TEXT,destination TEXT,collected_at TEXT,
            discord_status TEXT DEFAULT 'pending',external_status TEXT DEFAULT 'not_published', UNIQUE(source_id,original_url));
        ''')
        db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?)', (CID,'youtube','Creator',None,'ai_noise',1))
        db.execute('INSERT INTO news_checkpoints VALUES(?,?,?,?)', (CID,'abcdefghijk','2026-01-01T00:00:00Z','2026-01-01'))
        db.execute('INSERT INTO publications(id,source_id,original_url,creator,title,published_at,summary,image_url,destination,collected_at,discord_status) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   ('youtube:abcdefghijk',CID,'https://www.youtube.com/watch?v=abcdefghijk','Creator','Baseline','2026-01-01T00:00:00Z','','','ai_noise','2026-01-01','sent'))
        db.commit()
        db.close()

    def tearDown(self):
        self.temp.cleanup()

    def item(self, vid, stamp):
        return {'source_id': CID, 'video_id': vid, 'title': 'Video', 'published_at': stamp}

    def test_baseline_dedup_and_delivery_state(self):
        batch = {'version': 1, 'publications': [self.item('abcdefghijk','2026-01-01T00:00:00Z'),
                 self.item('ABCDEFGHIJK','2026-01-02T00:00:00Z'), self.item('01234567890','2025-12-01T00:00:00Z')]}
        self.assertEqual(registry_snapshot(self.path)['sources'][0]['video_id'], 'abcdefghijk')
        self.assertEqual(import_snapshot(self.path,batch), {'inserted':1,'duplicates':1,'baseline_skipped':1,'inserted_ids':['youtube:ABCDEFGHIJK']})
        self.assertEqual(import_snapshot(self.path,batch)['inserted'], 0)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT discord_status FROM publications WHERE id='youtube:abcdefghijk'").fetchone()[0], 'sent')
            self.assertEqual(db.execute('SELECT count(*) FROM publications').fetchone()[0], 2)

    def test_invalid_source_rolls_back_entire_batch(self):
        item = self.item('01234567890','2026-01-02T00:00:00Z')
        item['source_id'] = 'UC' + 'b' * 22
        with self.assertRaises(ValueError):
            import_snapshot(self.path, {'version':1, 'publications':[self.item('ABCDEFGHIJK','2026-01-02T00:00:00Z'),item]})
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM publications').fetchone()[0], 1)

    def test_pull_imports_only_after_a_bounded_snapshot(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit):
                return json.dumps({'version': 1, 'publications': [
                    self_outer.item('ABCDEFGHIJK', '2026-01-02T00:00:00Z')]}).encode()
        class Opener:
            def open(self, request, timeout):
                self_outer.assertEqual(request.method, 'GET')
                return Response()
        self_outer = self
        with patch('news_publication_sync.urllib.request.build_opener', return_value=Opener()):
            self.assertEqual(pull_snapshot(self.path, 'http://192.168.1.200:5678/webhook/news')['inserted'], 1)
            self.assertEqual(pull_snapshot(self.path, 'http://192.168.1.200:5678/webhook/news')['duplicates'], 1)
        for url in ('http://8.8.8.8:5678/x', 'http://user:pass@192.168.1.200:5678/x',
                    'http://192.168.1.200:5678/x?token=secret', 'http://example.com:5678/x'):
            with self.assertRaises(ValueError): pull_snapshot(self.path, url)

    def test_authenticated_pull_drops_inactive_sources(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit):
                return json.dumps({'version': 1, 'publications': [
                    self_outer.item('ABCDEFGHIJK', '2026-01-02T00:00:00Z'),
                    {**self_outer.item('01234567890', '2026-01-02T00:00:00Z'), 'source_id': 'UC' + 'b' * 22},
                ]}).encode()
        class Opener:
            def open(self, request, timeout):
                self_outer.assertEqual(request.get_header('Authorization'), 'Bearer test-token')
                return Response()
        self_outer = self
        with patch('news_publication_sync.urllib.request.build_opener', return_value=Opener()):
            result = pull_snapshot(
                self.path, 'http://192.168.1.200:5678/webhook/news',
                headers={'Authorization': 'Bearer test-token'},
                enabled_source_ids={CID},
            )
        self.assertEqual(result['inserted'], 1)
        self.assertEqual(result['inactive_sources_skipped'], 1)

    def test_active_window_orders_by_publication_time_and_archives_oldest(self):
        batch = {'version': 1, 'publications': [
            {**self.item('ABCDEFGHIJK', '2026-01-02T01:00:00+02:00'), 'title':'Older regular video'},
            self.item('01234567890', '2026-01-01T23:30:00Z'),
            {**self.item('abcdefghijK', '2026-01-02T00:00:00Z'), 'title':'Newer YouTube Short'},
        ]}
        result = import_snapshot(self.path, batch)
        self.assertEqual(result['inserted'], 3)
        service = NewsService(self.path)
        self.assertEqual([x['id'] for x in service.publications()],
                         ['youtube:abcdefghijK', 'youtube:01234567890'])
        self.assertEqual(service.publications()[0]['title'], 'Newer YouTube Short')
        self.assertEqual([x['id'] for x in service.publications('ai_noise')],
                         ['youtube:abcdefghijK', 'youtube:01234567890'])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM publications WHERE source_id=?', (CID,)).fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT id FROM publication_archive').fetchone()[0], 'youtube:ABCDEFGHIJK')
            self.assertEqual(db.execute('SELECT discord_status FROM publication_archive WHERE id=?',
                                        ('youtube:ABCDEFGHIJK',)).fetchone()[0], 'pending')
        replay = import_snapshot(self.path, {'version':1, 'publications':[self.item('ABCDEFGHIJK','2026-01-02T01:00:00+02:00')]})
        self.assertEqual(replay['duplicates'], 1)
        self.assertEqual(replay['inserted'], 0)

    def test_equal_publication_times_use_id_as_deterministic_tiebreaker(self):
        batch = {'version': 1, 'publications': [
            self.item('AAAAAAAAAAA', '2026-01-02T00:00:00Z'),
            self.item('ZZZZZZZZZZZ', '2026-01-02T00:00:00Z'),
        ]}
        import_snapshot(self.path, batch)
        self.assertEqual([x['id'] for x in NewsService(self.path).publications()],
                         ['youtube:ZZZZZZZZZZZ', 'youtube:AAAAAAAAAAA'])


if __name__ == '__main__':
    unittest.main()
