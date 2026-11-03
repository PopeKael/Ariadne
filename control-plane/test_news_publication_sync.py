import sqlite3
import tempfile
import unittest
import json
from unittest.mock import patch
from contextlib import closing
from pathlib import Path

from news_publication_sync import import_snapshot, registry_snapshot, pull_snapshot

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
        self.assertEqual(import_snapshot(self.path,batch), {'inserted':1,'duplicates':1,'baseline_skipped':1})
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


if __name__ == '__main__':
    unittest.main()
