import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from news_discord_auto import deliver_sync
from news_discord_delivery import DeliveryStore
from news_publication_sync import import_snapshot

CID = 'UC' + 'a' * 22
PAYLOAD = {'flags':32768, 'allowed_mentions':{'parse':[]}, 'components':[{'type':10,'content':'fixture'}]}


class AutomaticDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'news.sqlite3'
        now = datetime.now(timezone.utc)
        self.clock = [now.timestamp()]
        db = sqlite3.connect(self.path)
        db.executescript('''CREATE TABLE sources(id TEXT PRIMARY KEY,kind TEXT,name TEXT,friendly_name TEXT,destination TEXT,enabled INTEGER);
            CREATE TABLE news_checkpoints(source_id TEXT PRIMARY KEY,video_id TEXT,published_at TEXT,scanned_at TEXT);
            CREATE TABLE publications(id TEXT PRIMARY KEY,source_id TEXT,original_url TEXT,creator TEXT,title TEXT,published_at TEXT,
            summary TEXT,image_url TEXT,destination TEXT,collected_at TEXT,discord_status TEXT DEFAULT 'pending',external_status TEXT DEFAULT 'not_published',
            UNIQUE(source_id,original_url)); CREATE TABLE news_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);''')
        db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?)',(CID,'youtube','Creator',None,'ai_noise',1))
        db.execute('INSERT INTO news_checkpoints VALUES(?,?,?,?)',(CID,'abcdefghijk',(now-timedelta(days=1)).isoformat(),now.isoformat()))
        db.execute('INSERT INTO news_settings VALUES(?,?)',('stage3_activated_at',(now-timedelta(seconds=5)).isoformat()))
        db.commit(); db.close()
        self.now = now
        self.calls = []
        self.status = 200
        self.message_id = '1234567890'

    def make_store(self):
        def send(secret,payload,options):
            self.calls.append((secret,payload,options))
            return {'status':self.status,'id':self.message_id,'retry_after':0}
        return DeliveryStore(self.path,lambda _p,_c:True,lambda _row:PAYLOAD,
            lambda dest:'secret-'+dest,send,lambda:self.clock[0])

    def item(self, video, when=None):
        return {'source_id':CID,'video_id':video,'title':'Fixture','published_at':(when or self.now-timedelta(seconds=1)).isoformat()}

    def import_one(self, video='ABCDEFGHIJK', when=None):
        result=import_snapshot(self.path,{'version':1,'publications':[self.item(video,when)]})
        return result['inserted_ids']

    def test_dry_run_and_post_activation_delivery_once(self):
        ids=self.import_one()
        self.assertEqual(deliver_sync(self.path,ids,store_factory=self.make_store,dry_run=True)['eligible'],1)
        self.assertEqual(self.calls,[])
        self.assertEqual(deliver_sync(self.path,ids,store_factory=self.make_store)['sent'],1)
        self.assertEqual(self.import_one(), [])
        self.assertEqual(deliver_sync(self.path,[],store_factory=self.make_store)['eligible'],0)
        self.assertEqual(len(self.calls),1)

    def test_pre_activation_publication_is_never_automatically_sent(self):
        old=self.now-timedelta(seconds=10)
        ids=self.import_one(when=old)
        self.assertEqual(deliver_sync(self.path,ids,store_factory=self.make_store)['eligible'],0)
        self.assertEqual(self.calls,[])

    def test_disabled_source_and_current_destination_are_respected(self):
        ids=self.import_one()
        db=sqlite3.connect(self.path);db.execute('UPDATE sources SET enabled=0 WHERE id=?',(CID,));db.commit();db.close()
        self.assertEqual(deliver_sync(self.path,ids,store_factory=self.make_store)['eligible'],0)
        db=sqlite3.connect(self.path);db.execute('UPDATE sources SET enabled=1,destination=? WHERE id=?',('thailand_news',CID));db.commit();db.close()
        self.assertEqual(deliver_sync(self.path,ids,store_factory=self.make_store)['sent'],1)
        self.assertEqual(self.calls[0][0],'secret-thailand_news')

    def test_rate_limits_retry_at_most_three_times(self):
        ids=self.import_one()
        self.status=429
        self.assertEqual(deliver_sync(self.path,ids,store_factory=self.make_store)['failed'],1)
        for _ in range(2):
            self.clock[0]+=2
            self.assertEqual(deliver_sync(self.path,[],store_factory=self.make_store)['failed'],1)
        self.clock[0]+=2
        self.assertEqual(deliver_sync(self.path,[],store_factory=self.make_store)['eligible'],0)
        self.assertEqual(len(self.calls),3)


if __name__ == '__main__':
    unittest.main()
