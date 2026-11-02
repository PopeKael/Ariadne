import http.client,json,secrets,sqlite3,tempfile,threading,unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import credential_store, news_discord_admin, news_admin_setup

class RealCredentialHTTPTest(unittest.TestCase):
 def test_initial_setup_replacement_and_real_http_login(self):
  ref='ARIADNE_EXTERNAL_'+secrets.token_hex(6)+'_API_KEY'
  prior_ref=news_discord_admin.ADMIN_REF
  tmp=tempfile.TemporaryDirectory();server=None
  news_discord_admin.ADMIN_REF=ref;news_discord_admin.sessions.clear();news_discord_admin.failures.clear()
  try:
   self.assertFalse(credential_store.credential_exists(ref))
   for value,confirm in [('1234567','1234567'),('12345678','different')]:
    with self.assertRaises(ValueError):news_admin_setup.save_password(value,confirm,reference=ref)
   self.assertFalse(credential_store.credential_exists(ref))
   news_admin_setup.save_password('12345678','12345678',reference=ref)
   self.assertTrue(credential_store.credential_exists(ref))
   with self.assertRaises(FileExistsError):news_admin_setup.save_password('abcdefgh','abcdefgh',reference=ref)
   db=Path(tmp.name)/'isolated.sqlite3';sqlite3.connect(db).close()
   class Handler(BaseHTTPRequestHandler):
    def do_POST(self):news_discord_admin.handle(self,self.path,db)
    def do_GET(self):news_discord_admin.handle(self,self.path,db)
    def log_message(self,*args):pass
   server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
   def req(path,body=None,cookie=None,csrf=None):
    conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
    headers={'X-Forwarded-Proto':'https','Origin':news_discord_admin.ORIGIN,'Sec-Fetch-Site':'same-origin','Content-Type':'application/json'}
    if cookie:headers['Cookie']=cookie
    if csrf:headers['X-CSRF-Token']=csrf
    conn.request('POST' if body is not None else 'GET','/api/news-admin/'+path,json.dumps(body) if body is not None else None,headers)
    response=conn.getresponse();result=(response.status,json.loads(response.read()),response.getheader('Set-Cookie'));conn.close();return result
   status,body,cookie=req('login',{'password':'12345678'});self.assertEqual(status,200);self.assertIn('HttpOnly',cookie);self.assertIn('Secure',cookie);self.assertIn('SameSite=Strict',cookie)
   cookie=cookie.split(';')[0];self.assertTrue(req('session',cookie=cookie)[1]['authenticated'])
   self.assertEqual(req('status',{'destination':'ai_noise'},cookie=cookie)[0],403)
   self.assertEqual(req('status',{'destination':'ai_noise'},cookie=cookie,csrf=body['csrf'])[0],200)
   news_admin_setup.save_password('abcdefgh','abcdefgh',reference=ref,replace=True)
   self.assertEqual(req('login',{'password':'12345678'})[0],401)
   self.assertEqual(req('login',{'password':'abcdefgh'})[0],200)
  finally:
   if server:server.shutdown();server.server_close()
   credential_store.delete_credential(ref)
   news_discord_admin.ADMIN_REF=prior_ref;news_discord_admin.sessions.clear();news_discord_admin.failures.clear();tmp.cleanup()
  self.assertFalse(credential_store.credential_exists(ref))

if __name__=='__main__':unittest.main()
