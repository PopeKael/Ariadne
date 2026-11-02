import http.client, json, sqlite3, tempfile, threading, time, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import news_discord_admin as app

WEBHOOK='https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwx'
PASSWORD='correct horse battery staple'
PAYLOAD={'flags':32768,'allowed_mentions':{'parse':[]},'components':[{'type':10,'content':'Test'}]}

class Tests(unittest.TestCase):
 def setUp(self):
  app.sessions.clear();app.failures.clear();self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.db=Path(self.tmp.name)/'news.sqlite3';db=sqlite3.connect(self.db);db.execute('CREATE TABLE publications (id TEXT PRIMARY KEY,destination TEXT,discord_status TEXT)');db.execute("INSERT INTO publications VALUES ('one','ai_noise','pending')");db.commit();db.close()
  salt='0123456789abcdef0123456789abcdef';self.secrets={app.ADMIN_REF:salt+':'+app.password_hash(PASSWORD,salt),'ARIADNE_DISCORD_AI_NOISE':WEBHOOK};self.sent=[]
  self.patchers=[patch.object(app.credential_store,'credential_exists',side_effect=lambda r:r in self.secrets),patch.object(app.credential_store,'get_credential',side_effect=lambda r,allow_environment=False:self.secrets.get(r,'')),patch.object(app.credential_store,'save_credential',side_effect=lambda r,v:self.secrets.__setitem__(r,v)),patch.object(app,'format_publication',return_value=PAYLOAD),patch.object(app,'discord_transport',side_effect=lambda secret,payload,options:self.sent.append((secret,payload,options)) or {'status':200,'id':'123'}),patch.object(app,'connection_test',return_value=(True,'Connection successful.'))]
  for p in self.patchers:p.start();self.addCleanup(p.stop)
  testcase=self
  class Handler(BaseHTTPRequestHandler):
   def do_POST(self):app.handle(self,self.path,testcase.db)
   def do_GET(self):app.handle(self,self.path,testcase.db)
   def log_message(self,*args):pass
  self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
  self.cookie='';self.csrf=''
 def request(self,path,body=None,origin=app.ORIGIN,csrf=None,proto='https'):
  conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
  headers={'X-Forwarded-Proto':proto,'Origin':origin,'Sec-Fetch-Site':'same-origin','Content-Type':'application/json'}
  if self.cookie:headers['Cookie']=self.cookie
  if csrf is not None:headers['X-CSRF-Token']=csrf
  conn.request('POST' if body is not None else 'GET','/api/news-admin/'+path,json.dumps(body) if body is not None else None,headers)
  res=conn.getresponse();raw=res.read();setcookie=res.getheader('Set-Cookie');status=res.status;conn.close()
  return status,json.loads(raw),setcookie,raw.decode()
 def login(self):
  status,v,cookie,_=self.request('login',{'password':PASSWORD});self.assertEqual(status,200);self.csrf=v['csrf'];self.cookie=cookie.split(';')[0];self.assertIn('HttpOnly',cookie);self.assertIn('Secure',cookie);self.assertIn('SameSite=Strict',cookie)
 def action(self,path,body):return self.request(path,body,csrf=self.csrf)
 def reset_rate(self):
  for record in app.sessions.values():record['last_action']=0
 def test_complete_mocked_workflow(self):
  self.assertEqual(self.request('status',{'destination':'ai_noise'})[0],403)
  self.assertEqual(self.request('login',{'password':PASSWORD},origin='https://evil.example')[0],403)
  self.login()
  status,v,_,raw=self.action('status',{'destination':'ai_noise'});self.assertEqual(v['status'],'Stored');self.assertNotIn(WEBHOOK,raw)
  self.assertEqual(self.action('status',{'destination':'thailand_news'})[1]['status'],'Missing')
  self.assertEqual(self.request('save',{'destination':'ai_noise','webhook':WEBHOOK,'replace':True},csrf='bad')[0],403)
  self.assertEqual(self.action('save',{'destination':'ai_noise','webhook':WEBHOOK,'replace':False})[0],409)
  self.reset_rate();self.assertEqual(self.action('save',{'destination':'ai_noise','webhook':WEBHOOK,'replace':True})[0],200)
  self.reset_rate();self.assertEqual(self.action('test',{'destination':'ai_noise'})[1]['message'],'Connection successful.')
  self.reset_rate();self.assertEqual(self.action('send',{'id':'one','destination':'ai_noise','payload':PAYLOAD})[0],400);self.assertEqual(self.sent,[])
  self.reset_rate();body={'id':'one','destination':'ai_noise','payload':PAYLOAD,'confirmed':True};status,v,_,raw=self.action('send',body);self.assertEqual(v['result']['status'],'sent');self.assertEqual(v['result']['message_id'],'123');self.assertNotIn(WEBHOOK,raw);self.assertEqual(len(self.sent),1)
  self.reset_rate();self.assertEqual(self.action('send',body)[0],400);self.assertEqual(len(self.sent),1)
  self.reset_rate();body['resend_confirmed']=True;time.sleep(1.01);self.assertEqual(self.action('send',body)[1]['result']['status'],'sent');self.assertEqual(len(self.sent),2)
  self.assertEqual(self.action('logout',{})[0],200);self.assertEqual(self.action('status',{'destination':'ai_noise'})[0],403)
 def test_invalid_input_and_login_throttle(self):
  for _ in range(5):self.assertEqual(self.request('login',{'password':'bad'})[0],401)
  self.assertEqual(self.request('login',{'password':PASSWORD})[0],429)
  app.failures.clear();self.login()
  self.assertEqual(self.action('save',{'destination':'thailand_news','webhook':'https://evil.example/secret','replace':False})[0],400)
  self.assertNotIn('ARIADNE_DISCORD_THAILAND_NEWS',self.secrets)
 def test_https_and_session(self):
  self.assertEqual(self.request('session',proto='http')[0],403);self.login();status,v,_,_=self.request('session');self.assertTrue(v['authenticated']);self.assertEqual(v['csrf'],self.csrf)

if __name__=='__main__':unittest.main()
