import sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import news_destinations as dest
from news_service import NewsService
from news_discord_delivery import DeliveryStore
class Tests(unittest.TestCase):
 def test_snapshot_migration_and_routing(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'test.sqlite3';db=sqlite3.connect(path)
   db.execute("CREATE TABLE sources (id TEXT PRIMARY KEY,kind TEXT NOT NULL,name TEXT NOT NULL,url TEXT NOT NULL,destination TEXT NOT NULL CHECK(destination IN ('thailand_news','ai_noise')),enabled INTEGER NOT NULL,created_at TEXT NOT NULL,handle TEXT,friendly_name TEXT,thumbnail_url TEXT)")
   db.execute("CREATE TABLE publications (id TEXT PRIMARY KEY,source_id TEXT,original_url TEXT,creator TEXT,title TEXT,published_at TEXT,summary TEXT,image_url TEXT,destination TEXT,collected_at TEXT,discord_status TEXT,external_status TEXT,description TEXT)")
   for i in range(24):
    channel='UC'+str(i).zfill(22); db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?)",(channel,'youtube',str(i),'url','ai_noise',1,'date',None,None,None));db.execute("INSERT INTO publications VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(str(i),channel,'url','creator','title','date','','','ai_noise','date','pending','not_published',None))
   db.commit();db.close()
   db=sqlite3.connect(path);before=db.execute('SELECT * FROM sources ORDER BY id').fetchall();pubs=db.execute('SELECT * FROM publications ORDER BY id').fetchall();db.close();secrets={}
   def exists(ref):return ref in secrets
   def save(ref,value):secrets[ref]=value
   def remove(ref):secrets.pop(ref,None)
   with patch.object(dest.credential_store,'credential_exists',side_effect=exists),patch.object(dest.credential_store,'save_credential',side_effect=save),patch.object(dest.credential_store,'delete_credential',side_effect=remove):
    id=dest.add_destination(path,'Chanya & Wazza','fake-value',lambda _:None)
    self.assertEqual(dest.reference(path,id),next(iter(secrets)))
    self.assertEqual(len(dest.list_destinations(path)),3)
    with self.assertRaises(ValueError):dest.add_destination(path,'chanya & wazza','other',lambda _:None)
    db=sqlite3.connect(path);self.assertEqual(before,db.execute('SELECT * FROM sources ORDER BY id').fetchall());self.assertEqual(pubs,db.execute('SELECT * FROM publications ORDER BY id').fetchall());db.close()
    service=NewsService(path);service.update(before[0][0],'destination',id);self.assertEqual(next(x for x in service.sources() if x['id']==before[0][0])['destination'],id)
    db=sqlite3.connect(path);pub=pubs[0][0];db.execute('UPDATE publications SET destination=? WHERE id=?',(id,pub));db.commit();db.close()
    calls=[];payload={'flags':32768,'allowed_mentions':{'parse':[]},'components':[{'type':10,'content':'Test'}]}
    store=DeliveryStore(path,lambda p,c:True,lambda _:payload,lambda key:secrets[dest.reference(path,key)],lambda secret,body,opts:calls.append((secret,body)) or {'status':200,'id':'123'})
    self.assertEqual(store.send(pub,1,1,confirmed=True)['status'],'sent');self.assertEqual(calls[0][0],'fake-value')
if __name__=='__main__':unittest.main()
