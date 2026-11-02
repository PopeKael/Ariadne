import io,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
import news_discord_local as app

WEBHOOK='https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwx'
PUBLICATION={'id':'youtube:abcdefghijk','creator':'Creator','title':'A video','destination':'ai_noise','discord_status':'pending','original_url':'https://www.youtube.com/watch?v=abcdefghijk','image_url':'https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg','summary':'The first useful sentence. Another useful sentence.','published_at':'2026-01-01T00:00:00Z'}

class LocalTests(unittest.TestCase):
 def test_validates_fixed_discord_host_and_hides_invalid_secret(self):
  self.assertTrue(app.validate_webhook(WEBHOOK).startswith('/api/webhooks/'))
  for value in ('https://evil.example/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwx',WEBHOOK+'?a=1','https://discord.com@evil.example/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwx'):
   with self.assertRaises(ValueError) as exc:app.validate_webhook(value)
   self.assertNotIn('abcdefghijklmnopqrstuvwx',str(exc.exception))
 def test_uses_actual_shared_formatter(self):
  payload=app.format_publication(PUBLICATION)
  self.assertEqual(payload['flags'],32768);self.assertEqual(payload['components'][0]['type'],12)
  self.assertEqual(payload['components'][0]['items'][0]['media']['url'],PUBLICATION['image_url'])
  self.assertEqual(payload['components'][-1]['content'],PUBLICATION['original_url']);self.assertEqual(payload['allowed_mentions'],{'parse':[]})
 def test_transport_sends_exact_payload_and_confirm_options(self):
  conn=MagicMock();response=conn.getresponse.return_value;response.status=200;response.read.return_value=b'{"id":"123456789012345678","channel_id":"234567890123456789","timestamp":"2026-11-02T00:00:00+00:00"}'
  with patch.object(app.http.client,'HTTPSConnection',return_value=conn) as make:
   result=app.discord_transport(WEBHOOK,app.format_publication(PUBLICATION),{'wait':True,'with_components':True})
  make.assert_called_once_with('discord.com',timeout=20)
  args,kwargs=conn.request.call_args
  self.assertEqual(args[0],'POST');self.assertTrue(args[1].endswith('?wait=true&with_components=true'))
  self.assertEqual(json.loads(kwargs['body'])['flags'],32768);self.assertEqual(result['id'],'123456789012345678');self.assertEqual(result['channel_id'],'234567890123456789');conn.close.assert_called_once()
 def test_transport_rate_limit_never_displays_webhook(self):
  conn=MagicMock();response=conn.getresponse.return_value;response.status=429;response.read.return_value=b'{"retry_after":12.5,"global":true}'
  with patch.object(app.http.client,'HTTPSConnection',return_value=conn):result=app.discord_transport(WEBHOOK,{}, {'wait':True,'with_components':True})
  self.assertEqual(result,{'status':429,'retry_after':12.5});self.assertNotIn('abcdefghijklmnopqrstuvwx',repr(result))
 def test_cancel_does_not_call_delivery(self):
  with patch.object(app,'history',return_value=[]),patch.object(app.credential_store,'get_credential',return_value=WEBHOOK),patch('builtins.input',return_value=''),patch.object(app,'DeliveryStore') as store:
   app.send_interactive(PUBLICATION)
  store.assert_not_called()
 def test_confirmation_routes_selected_publication_and_payload(self):
  service=MagicMock();service.send.return_value={'status':'sent','message_id':'123','attempt_id':'a','retry_after':None}
  with patch.object(app,'history',return_value=[]),patch.object(app.credential_store,'get_credential',return_value=WEBHOOK),patch('builtins.input',return_value='SEND'),patch.object(app,'DeliveryStore',return_value=service):
   app.send_interactive(PUBLICATION)
  args,kwargs=service.send.call_args;self.assertEqual(args[0],PUBLICATION['id']);self.assertEqual(kwargs['expected_destination'],'ai_noise');self.assertEqual(kwargs['expected_payload'],app.format_publication(PUBLICATION))
 def test_existing_send_requires_separate_resend_confirmation(self):
  prior=[{'status':'sent','destination':'ai_noise','started_at':1,'discord_message_id':'123','http_status':200}]
  with patch.object(app,'history',return_value=prior),patch.object(app.credential_store,'get_credential',return_value=WEBHOOK),patch('builtins.input',return_value=''),patch.object(app,'DeliveryStore') as store:
   app.send_interactive(PUBLICATION)
  store.assert_not_called()
 def test_registration_opens_gui_with_vault_adapters(self):
  import news_discord_registration
  with patch.object(news_discord_registration,'open_registration') as gui:
   app.register()
  gui.assert_called_once_with(app.REFERENCES,app.LABELS,app.validate_webhook,app.credential_store.credential_exists,app.credential_store.save_credential)
 def test_credential_refs_ignore_environment_override(self):
  self.assertEqual(app.credential_store._target('ARIADNE_DISCORD_AI_NOISE'),'Ariadne/NewsService/Discord/AINoise')
  with patch.dict(app.credential_store.os.environ,{'ARIADNE_DISCORD_AI_NOISE':'fake-override'}),patch.object(app.credential_store.os,'name','posix'):
   self.assertEqual(app.credential_store.get_credential('ARIADNE_DISCORD_AI_NOISE',allow_environment=False),'')
 def test_readonly_listing_and_history_with_no_attempt_table(self):
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'news.sqlite3';db=sqlite3.connect(path)
   db.execute('CREATE TABLE publications(id TEXT,published_at TEXT)');db.execute("INSERT INTO publications VALUES('video','2026')");db.commit();db.close()
   self.assertEqual(app.list_publications(path)[0]['id'],'video');self.assertEqual(app.history('video',path),[])

if __name__=='__main__':unittest.main()
