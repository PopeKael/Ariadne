import sqlite3,tempfile,unittest,threading
from pathlib import Path
from news_discord_delivery import DeliveryStore,Denied,Blocked

PAYLOAD={'flags':32768,'allowed_mentions':{'parse':[]},'components':[{'type':12,'items':[{'media':{'url':'https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg'}}]},{'type':10,'content':'Example'}]}

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'test.sqlite'
  db=sqlite3.connect(self.path);db.execute("CREATE TABLE publications(id TEXT PRIMARY KEY,destination TEXT,discord_status TEXT)");db.executemany("INSERT INTO publications VALUES(?,?,?)",[('one','ai_noise','pending'),('two','thailand_news','pending')]);db.commit();db.close()
  self.now=100.0;self.calls=[];self.code=200;self.msg='123456789'
  def transport(secret,payload,options):
   self.calls.append((secret,payload,options))
   if isinstance(self.code,Exception):raise self.code
   return {'status':self.code,'id':self.msg,'retry_after':12}
  self.store=DeliveryStore(self.path,lambda p,c:p=='authorized' and c=='csrf',lambda row:PAYLOAD,lambda dest:{'ai_noise':'fake-ai','thailand_news':'fake-thai'}.get(dest),transport,lambda:self.now)
 def send(self,id='one',**kwargs):return self.store.send(id,'authorized','csrf',confirmed=True,**kwargs)
 def test_requires_auth_csrf_and_confirmation(self):
  for principal,csrf,confirmed in [('wrong','csrf',True),('authorized','wrong',True),('authorized','csrf',False)]:
   with self.assertRaises(Denied):self.store.send('one',principal,csrf,confirmed=confirmed)
  self.assertEqual(self.calls,[])
 def test_success_routes_and_prevents_duplicate(self):
  result=self.send();self.assertEqual(result['status'],'sent');self.assertEqual(result['message_id'],'123456789')
  self.assertEqual(self.calls[0][0],'fake-ai');self.assertEqual(self.calls[0][2],{'wait':True,'with_components':True})
  self.now+=2
  with self.assertRaises(Blocked):self.send()
  self.assertEqual(self.send(resend_confirmed=True)['status'],'sent')
  self.now+=2;self.send('two');self.assertEqual(self.calls[-1][0],'fake-thai')
  db=sqlite3.connect(self.path)
  try:self.assertEqual(db.execute("SELECT count(*) FROM discord_delivery_attempts WHERE status='sent'").fetchone()[0],3);self.assertEqual(db.execute("SELECT discord_status FROM publications WHERE id='one'").fetchone()[0],'sent')
  finally:db.close()
 def test_timeout_uncertain_and_never_retried_automatically(self):
  self.code=TimeoutError('fake-secret should never be stored');self.assertEqual(self.send()['status'],'uncertain');self.now+=30
  with self.assertRaises(Blocked):self.send(resend_confirmed=True)
  db=sqlite3.connect(self.path)
  try:self.assertNotIn('fake-secret',repr(db.execute('SELECT * FROM discord_delivery_attempts').fetchall()));self.assertEqual(db.execute("SELECT discord_status FROM publications WHERE id='one'").fetchone()[0],'uncertain')
  finally:db.close()
 def test_rejection_and_rate_limit_require_deliberate_retry(self):
  self.code=429;self.assertEqual(self.send()['status'],'rate_limited');self.now+=5
  with self.assertRaises(Blocked):self.send()
  with self.assertRaises(Blocked):self.send('two')
  self.now+=8;self.code=400;self.assertEqual(self.send()['status'],'rejected');self.now+=2;self.code=200;self.assertEqual(self.send()['status'],'sent')
 def test_failed_resend_does_not_erase_prior_send_protection(self):
  self.assertEqual(self.send()['status'],'sent');self.now+=2;self.code=400
  self.assertEqual(self.send(resend_confirmed=True)['status'],'rejected');self.now+=2
  with self.assertRaises(Blocked):self.send()
 def test_server_failure_is_uncertain(self):
  self.code=503;self.assertEqual(self.send()['status'],'uncertain');self.now+=30
  with self.assertRaises(Blocked):self.send(resend_confirmed=True)
 def test_concurrent_double_click_allows_only_one_transport(self):
  started=threading.Event();finish=threading.Event();results=[]
  def slow(secret,payload,options):
   started.set();finish.wait(3);return {'status':200,'id':'123'}
  self.store.transport=slow
  t=threading.Thread(target=lambda:results.append(self.send()));t.start()
  try:
   self.assertTrue(started.wait(2))
   with self.assertRaises(Blocked):self.send()
  finally:finish.set();t.join(3)
  self.assertEqual(results[0]['status'],'sent')
 def test_changed_destination_or_payload_requires_new_preview(self):
  with self.assertRaises(Blocked):self.store.send('one','authorized','csrf',confirmed=True,expected_destination='thailand_news',expected_payload=PAYLOAD)
  with self.assertRaises(Blocked):self.store.send('one','authorized','csrf',confirmed=True,expected_destination='ai_noise',expected_payload={'flags':0})
  self.assertEqual(self.calls,[])
 def test_missing_credentials_and_unlisted_destination_fail_closed(self):
  self.store.credential_lookup=lambda _:None
  with self.assertRaises(Blocked):self.send()
  self.assertEqual(self.calls,[])
  db=sqlite3.connect(self.path);db.execute("UPDATE publications SET destination='other' WHERE id='one'");db.commit();db.close()
  with self.assertRaises(Blocked):self.send()

if __name__=='__main__':unittest.main()
