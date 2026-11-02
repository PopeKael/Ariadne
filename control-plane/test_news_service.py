import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from news_service import NewsService, resolve_channel

CID = 'UC' + 'a' * 22
AVATAR = 'https://yt3.googleusercontent.com/avatar'

class NewsServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'news.sqlite3'
        self.service = NewsService(self.path)
        self.add = patch('news_service.resolve_channel', return_value=(CID, 'Example Creator', '@oldhandle', AVATAR))
        self.add.start()
        self.addCleanup(self.add.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_source_lifecycle_and_persistence(self):
        self.service.add('@example', 'thailand_news')
        with self.assertRaises(ValueError): self.service.add('@example', 'ai_noise')
        self.service.update(CID, 'destination', 'ai_noise')
        self.service.update(CID, 'disable')
        reopened = NewsService(self.path)
        self.assertEqual((reopened.sources()[0]['destination'], reopened.sources()[0]['enabled']), ('ai_noise', 0))
        reopened.update(CID, 'enable')
        reopened.update(CID, 'remove')
        self.assertEqual(reopened.sources(), [])

    def test_cache_filter_order_and_constraints(self):
        self.service.sources()
        with self.service.connect() as db:
            for i, category in [(1,'thailand_news'), (2,'ai_noise'), (3,'ai_noise')]:
                db.execute('INSERT INTO publications(id,source_id,original_url,creator,title,published_at,destination,collected_at) VALUES(?,?,?,?,?,?,?,?)',
                           (str(i),CID,'https://example.org/'+str(i),'Creator',str(i),f'2026-01-0{i}T00:00:00Z',category,'2026-01-04T00:00:00Z'))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('INSERT INTO publications(id,source_id,original_url,creator,title,published_at,destination,collected_at) VALUES(?,?,?,?,?,?,?,?)',('4',CID,'https://example.org/1','Creator','x','2026','ai_noise','2026'))
        self.assertEqual([x['id'] for x in NewsService(self.path).publications('ai_noise')], ['3','2'])
        self.assertEqual(len(self.service.publications()),3)

    def test_reject_invalid_actions_and_destinations(self):
        with self.assertRaises(ValueError): self.service.add('@x', 'unknown')
        with self.assertRaises(ValueError): self.service.publications('unknown')
        with self.assertRaises(ValueError): self.service.update(CID, 'remove')

    def test_cache_insertion_uses_enabled_source_and_deduplicates(self):
        self.service.add('@example', 'ai_noise')
        item = dict(id='video-1', source_id=CID, original_url='https://youtube.com/watch?v=123', creator='Creator', title='Title', published_at='2026-01-01T00:00:00Z')
        self.assertTrue(self.service.cache_publication(item))
        self.assertFalse(self.service.cache_publication(item))
        self.assertEqual(self.service.publications('ai_noise')[0]['title'], 'Title')
        self.service.update(CID, 'disable')
        with self.assertRaises(ValueError): self.service.cache_publication(dict(item, id='video-2'))

    def test_alias_duplicate_and_friendly_name_survives_rename(self):
        self.service.add('@oldhandle', 'ai_noise')
        self.service.update(CID, 'friendly_name', friendly_name='Expat Paul')
        with self.assertRaises(ValueError): self.service.add('@newhandle', 'thailand_news')
        self.add.stop()
        with patch('news_service.resolve_channel', return_value=(CID, 'New YouTube Name', '@newhandle', 'https://yt3.googleusercontent.com/new')):
            self.service.refresh_identity(CID)
        row = NewsService(self.path).sources()[0]
        self.assertEqual((row['name'],row['handle'],row['friendly_name'],row['destination']), ('New YouTube Name','@newhandle','Expat Paul','ai_noise'))
        self.assertEqual(row['thumbnail_url'],'https://yt3.googleusercontent.com/new')

    def test_invalid_handle_recovers_using_channel_id(self):
        self.service.add('@oldhandle', 'thailand_news')
        self.add.stop()
        with patch('news_service.resolve_channel', side_effect=[ValueError('No such handle'),(CID,'Renamed','@newhandle',None)]) as resolver:
            self.service.refresh_identity(CID)
        self.assertEqual(resolver.call_args_list[1].args[0], 'https://www.youtube.com/channel/'+CID)
        self.assertEqual(self.service.sources()[0]['handle'],'@newhandle')
        self.assertEqual(self.service.sources()[0]['thumbnail_url'],AVATAR)

    def test_failed_refresh_does_not_delete_or_change_source(self):
        self.service.add('@oldhandle', 'thailand_news')
        before = self.service.sources()
        self.add.stop()
        with patch('news_service.resolve_channel', side_effect=ValueError('unavailable')):
            with self.assertRaises(ValueError): self.service.refresh_identity(CID)
        self.assertEqual(before, self.service.sources())

    def test_wrong_channel_at_old_handle_falls_back(self):
        self.service.add('@oldhandle', 'ai_noise')
        self.add.stop()
        with patch('news_service.resolve_channel', side_effect=[('UC'+'b'*22,'Someone else','@oldhandle',None),(CID,'Actual creator','@newhandle',None)]):
            self.service.refresh_identity(CID)
        self.assertEqual(self.service.sources()[0]['name'],'Actual creator')

    def test_migration_preserves_legacy_rows(self):
        from contextlib import closing
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE sources(id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, url TEXT NOT NULL, destination TEXT NOT NULL, enabled INTEGER NOT NULL, created_at TEXT NOT NULL)')
            db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?)',(CID,'youtube','Old name','https://www.youtube.com/channel/'+CID,'thailand_news',1,'2026'))
        row = self.service.sources()[0]
        self.assertEqual((row['id'],row['name'],row['destination'],row['enabled'],row['friendly_name'],row['handle']), (CID,'Old name','thailand_news',1,None,None))
        self.assertIsNone(row['thumbnail_url'])
        self.assertEqual(self.service.sources()[0],row)

    def test_backfill_only_fills_missing_avatar_and_preserves_source(self):
        self.service.add('@example', 'thailand_news')
        self.service.update(CID, 'friendly_name', friendly_name='Keep this')
        with self.service.connect() as db:
            db.execute('UPDATE sources SET thumbnail_url=NULL WHERE id=?',(CID,))
        before = self.service.sources()[0]
        result = self.service.backfill_missing_thumbnails()
        after = self.service.sources()[0]
        self.assertEqual(result, {'checked':1,'updated':1,'unavailable':0})
        self.assertEqual(after['thumbnail_url'],AVATAR)
        self.assertEqual({k:v for k,v in before.items() if k!='thumbnail_url'}, {k:v for k,v in after.items() if k!='thumbnail_url'})
        self.assertEqual(self.service.backfill_missing_thumbnails()['checked'],0)

    def test_backfill_missing_avatar_does_not_remove_source(self):
        self.service.add('@example', 'ai_noise')
        with self.service.connect() as db:
            db.execute('UPDATE sources SET thumbnail_url=NULL WHERE id=?',(CID,))
        self.add.stop()
        with patch('news_service.resolve_channel',return_value=(CID,'Name','@x',None)):
            self.assertEqual(self.service.backfill_missing_thumbnails()['unavailable'],1)
        self.assertIsNone(self.service.sources()[0]['thumbnail_url'])

class ChannelValidationTests(unittest.TestCase):
    def test_untrusted_hosts_and_non_channel_urls_rejected(self):
        for value in ('https://evil.example/@test','http://youtube.com/@test','https://youtube.com/watch?v=abc','https://youtube.com.evil.example/@test','https://youtube.com:444/@test'):
            with self.subTest(value=value), self.assertRaises(ValueError): resolve_channel(value)

    def test_large_page_uses_canonical_identity_in_bounded_prefix(self):
        from unittest.mock import MagicMock
        page = (f'<link rel="canonical" href="https://www.youtube.com/channel/{CID}"><meta property="og:title" content="The Thaiger">'.encode() + b'x' * 2_100_000)
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = lambda count: page[:count]
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = response
            self.assertEqual(resolve_channel('@TheThaiger'), (CID, 'The Thaiger', '@TheThaiger', None))
            response.read.assert_called_once_with(2_000_001)

    def test_truncated_page_without_canonical_is_not_verified(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = (f'"externalId":"{CID}"'.encode() + b'x' * 2_000_001)[:2_000_001]
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = response
            with self.assertRaises(ValueError): resolve_channel('@unresolved')

    def test_canonical_url_extracts_current_handle(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = (f'<link rel="canonical" href="https://www.youtube.com/channel/{CID}"><meta property="og:title" content="New name">'+ '"vanityChannelUrl":"http://www.youtube.com/@newhandle"').encode()
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = response
            self.assertEqual(resolve_channel('https://www.youtube.com/channel/'+CID),(CID,'New name','@newhandle',None))

    def test_channel_avatar_metadata_and_untrusted_fallback(self):
        from unittest.mock import MagicMock
        response=MagicMock(); response.__enter__.return_value=response
        prefix=f'<link rel="canonical" href="https://www.youtube.com/channel/{CID}">'
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=response
            response.read.return_value=(prefix+'<meta content="https://yt3.googleusercontent.com/avatar?a=1&amp;b=2" property="og:image">').encode()
            self.assertEqual(resolve_channel('@example')[3], 'https://yt3.googleusercontent.com/avatar?a=1&b=2')
            response.read.return_value=(prefix+'<meta property="og:image" content="https://evil.example/avatar">').encode()
            self.assertIsNone(resolve_channel('@example')[3])

if __name__ == '__main__': unittest.main()
