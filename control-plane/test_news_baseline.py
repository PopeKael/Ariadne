import sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from news_baseline import baseline,latest_video
import news_youtube_fallback as yf

CID='UC'+'a'*22
XML=f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/"><yt:channelId>{CID}</yt:channelId><entry><yt:videoId>abcdefghijk</yt:videoId><yt:channelId>{CID}</yt:channelId><title>Latest video</title><published>2026-11-01T00:00:00Z</published><media:group><media:description>Example</media:description><media:thumbnail url="https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg"/></media:group></entry></feed>'''.encode()

class BaselineTests(unittest.TestCase):
 def test_resolves_videos_shorts_streams_and_selects_newest(self):
  def tabs(cid,tab):return {'videos':['abcdefghijk'],'shorts':['ABCDEFGHIJK'],'streams':[]}[tab]
  def video(cid,vid):return {'video_id':vid,'published_at':'2026-10-01T00:00:00+00:00' if vid=='abcdefghijk' else '2026-11-01T00:00:00+00:00'}
  with patch.object(yf,'_tabs',side_effect=tabs),patch.object(yf,'_video',side_effect=video):
   self.assertEqual(latest_video(CID)['video_id'],'ABCDEFGHIJK')
 def test_player_validation_and_upcoming_exclusion(self):
  import json
  payload={'playabilityStatus':{'status':'OK'},'videoDetails':{'videoId':'abcdefghijk','channelId':CID,'title':'Example','shortDescription':'Description','isPrivate':False},'microformat':{'playerMicroformatRenderer':{'publishDate':'2026-01-01T00:00:00Z','isUnlisted':False}}}
  with patch.object(yf,'_page',side_effect=lambda _: 'var ytInitialPlayerResponse = '+json.dumps(payload)):
   item=yf._video(CID,'abcdefghijk');self.assertEqual(item['description'],'Description')
   payload['videoDetails']['isUpcoming']=True;self.assertIsNone(yf._video(CID,'abcdefghijk'))
   payload['videoDetails']['isUpcoming']=False;payload['microformat']['playerMicroformatRenderer']['isUnlisted']=True;self.assertIsNone(yf._video(CID,'abcdefghijk'))
   payload['microformat']['playerMicroformatRenderer']['isUnlisted']=False;payload['videoDetails']['channelId']='UC'+'b'*22
   with self.assertRaises(ValueError):yf._video(CID,'abcdefghijk')
 def test_unavailable_tab_does_not_claim_latest(self):
  with patch.object(yf,'_tabs',side_effect=ValueError('shorts unavailable')):
   with self.assertRaises(ValueError):latest_video(CID)
 def test_tab_requires_canonical_identity_and_reads_ordered_video_ids(self):
  import json
  tab={'selected':True,'endpoint':{'commandMetadata':{'webCommandMetadata':{'url':'/@example/videos'}}},'content':{'richGridRenderer':{'contents':[{'richItemRenderer':{'content':{'lockupViewModel':{'contentType':'LOCKUP_CONTENT_TYPE_VIDEO','contentId':'abcdefghijk'}}}}]}}}
  data={'metadata':{'channelMetadataRenderer':{'externalId':CID}},'contents':{'twoColumnBrowseResultsRenderer':{'tabs':[{'tabRenderer':tab}]}}}
  with patch.object(yf,'_page',side_effect=lambda _: 'var ytInitialData = '+json.dumps(data)):
   self.assertEqual(yf._tabs(CID,'videos'),['abcdefghijk'])
   data['metadata']['channelMetadataRenderer']['externalId']='UC'+'b'*22
   with self.assertRaises(ValueError):yf._tabs(CID,'videos')
 def test_skips_unavailable_candidate_and_uses_next_published(self):
  def tabs(cid,tab):return ['abcdefghijk','ABCDEFGHIJK'] if tab=='videos' else []
  def video(cid,vid):return None if vid=='abcdefghijk' else {'video_id':vid,'published_at':'2026-01-01T00:00:00+00:00'}
  with patch.object(yf,'_tabs',side_effect=tabs),patch.object(yf,'_video',side_effect=video):
   self.assertEqual(latest_video(CID)['video_id'],'ABCDEFGHIJK')
 def test_one_time_and_unavailable_preserves_delivery(self):
  with tempfile.TemporaryDirectory() as temp:
   path=Path(temp)/'test.sqlite';db=sqlite3.connect(path)
   db.executescript('''CREATE TABLE sources(id TEXT PRIMARY KEY,kind TEXT,name TEXT,friendly_name TEXT,destination TEXT,enabled INTEGER);CREATE TABLE publications(id TEXT PRIMARY KEY,source_id TEXT,original_url TEXT,creator TEXT,title TEXT,published_at TEXT,summary TEXT,image_url TEXT,destination TEXT,collected_at TEXT,discord_status TEXT DEFAULT 'pending',external_status TEXT DEFAULT 'not_published',UNIQUE(source_id,original_url));''')
   db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?)',(CID,'youtube','Creator',None,'ai_noise',1));db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?)',('UC'+'b'*22,'youtube','Unavailable',None,'thailand_news',1));db.commit();db.close()
   video=dict(video_id='abcdefghijk',title='title',summary='description',image_url='https://i.ytimg.com/x',published_at='2026-11-01T00:00:00+00:00')
   def fetch(cid):
    if cid!=CID:raise ValueError('feed unavailable')
    return video
   result=baseline(path,fetch);self.assertEqual(result['baselined'],['Creator']);self.assertEqual(len(result['failed']),1)
   db=sqlite3.connect(path);db.execute("UPDATE publications SET discord_status='sent'");db.commit();checkpoint=db.execute('SELECT * FROM news_checkpoints').fetchone();db.close()
   again=baseline(path,lambda cid: (_ for _ in ()).throw(ValueError('offline')))
   self.assertEqual(again['already_baselined'],['Creator'])
   db=sqlite3.connect(path);self.assertEqual(db.execute('SELECT * FROM news_checkpoints').fetchone(),checkpoint);self.assertEqual(db.execute('SELECT count(*),max(discord_status) FROM publications').fetchone(),(1,'sent'));db.close()

 def test_targeted_baseline_preserves_other_sources_and_checkpoint(self):
  with tempfile.TemporaryDirectory() as temp:
   path=Path(temp)/'test.sqlite';db=sqlite3.connect(path)
   db.executescript("CREATE TABLE sources(id TEXT PRIMARY KEY,kind TEXT,name TEXT,friendly_name TEXT,destination TEXT,enabled INTEGER); CREATE TABLE publications(id TEXT PRIMARY KEY,source_id TEXT,original_url TEXT,creator TEXT,title TEXT,published_at TEXT,summary TEXT,image_url TEXT,destination TEXT,collected_at TEXT,discord_status TEXT DEFAULT 'pending',external_status TEXT DEFAULT 'not_published',UNIQUE(source_id,original_url)); CREATE TABLE news_checkpoints(source_id TEXT PRIMARY KEY,video_id TEXT,published_at TEXT,scanned_at TEXT);")
   other='UC'+'b'*22;db.executemany('INSERT INTO sources VALUES(?,?,?,?,?,?)',[(CID,'youtube','Creator',None,'custom',1),(other,'youtube','Other',None,'ai_noise',1)]);db.execute('INSERT INTO news_checkpoints VALUES(?,?,?,?)',(other,'ABCDEFGHIJK','date','date'));db.commit();db.close()
   calls=[];video=dict(video_id='abcdefghijk',title='title',summary='description',image_url='https://i.ytimg.com/x',published_at='2026-11-01T00:00:00+00:00')
   result=baseline(path,lambda cid:calls.append(cid) or video,source_id=CID);self.assertEqual(calls,[CID]);self.assertEqual(result['baselined'],['Creator'])
   self.assertEqual(baseline(path,lambda _:self.fail('Existing baseline should be skipped'),source_id=CID)['already_baselined'],['Creator'])
   db=sqlite3.connect(path);self.assertEqual(db.execute('SELECT destination FROM publications').fetchone()[0],'custom');self.assertEqual(db.execute('SELECT video_id FROM news_checkpoints WHERE source_id=?',(other,)).fetchone()[0],'ABCDEFGHIJK');db.close()
   with self.assertRaises(ValueError):baseline(path,source_id='bad')

if __name__=='__main__':unittest.main()
