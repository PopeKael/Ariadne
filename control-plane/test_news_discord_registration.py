import io,unittest
from unittest.mock import MagicMock,patch
import news_discord_local as local
import news_discord_registration as ui

FAKE='https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwx'

class Var:
 def __init__(self,*a,value='',**k):self.value=value;self.traces=[]
 def get(self):return self.value
 def set(self,value):self.value=value;[f() for f in self.traces]
 def trace_add(self,mode,callback):self.traces.append(callback)
class Widget:
 def __init__(self,*a,**k):self.options=k;self.destroyed=False
 def __getattr__(self,name):
  if name in ('pack','grid','columnconfigure','bind','title','minsize','resizable','protocol'):return lambda *a,**k:None
  raise AttributeError(name)
 def configure(self,**kw):self.options.update(kw)
 def destroy(self):self.destroyed=True
 def clipboard_get(self):return self.clipboard

class RegistrationTests(unittest.TestCase):
 def setUp(self):
  self.root=Widget();self.root.clipboard=FAKE;self.stored={};self.save=MagicMock(side_effect=lambda k,v:self.stored.__setitem__(k,v))
  patches=[patch.object(ui.tk,'StringVar',Var),patch.object(ui.tk,'BooleanVar',Var)]+[patch.object(ui.ttk,name,Widget) for name in ('Frame','Label','Combobox','Entry','Button','Checkbutton')]
  for item in patches:item.start();self.addCleanup(item.stop)
  self.window=ui.RegistrationWindow(self.root,local.REFERENCES,local.LABELS,local.validate_webhook,lambda key:key in self.stored,self.save)
 def test_paste_feedback_and_mask_show_hide(self):
  self.assertEqual(self.window.entry.options['show'],'•')
  self.window.paste();self.assertEqual(self.window.value.get(),FAKE)
  self.assertIn('Thailand News: URL received and recognised',self.window.input_status.get());self.assertNotIn(FAKE,self.window.input_status.get())
  self.window.show.set(True);self.window.toggle_visibility();self.assertEqual(self.window.entry.options['show'],'')
  self.window.show.set(False);self.window.toggle_visibility();self.assertEqual(self.window.entry.options['show'],'•')
 def test_empty_invalid_and_nontext_clipboard(self):
  self.root.clipboard='  ';self.window.paste();self.assertIn('empty',self.window.input_status.get());self.window.save();self.save.assert_not_called()
  self.root.clipboard='not a webhook';self.window.paste();self.assertIn('not a recognised',self.window.input_status.get());self.window.save();self.save.assert_not_called()
  self.root.clipboard_get=MagicMock(side_effect=ui.tk.TclError('clipboard details'))
  self.window.paste();self.assertIn('does not contain text',self.window.input_status.get())
 def test_store_new_and_check_other_destination_independently(self):
  self.window.paste();self.window.save();self.save.assert_called_once_with(local.REFERENCES['thailand_news'],FAKE)
  self.assertIn('successfully stored',self.window.result.get());self.assertIn('credential is stored',self.window.vault_status.get());self.assertEqual(self.window.value.get(),'')
  self.window.destination.set('AI Noise');self.window.changed_destination();self.assertIn('no credential is stored',self.window.vault_status.get())
 def test_explicit_overwrite_confirmation(self):
  self.stored[local.REFERENCES['ai_noise']]='old';self.window.destination.set('AI Noise');self.window.changed_destination();self.window.paste()
  with patch.object(ui.messagebox,'askyesno',return_value=False) as ask:self.window.save()
  ask.assert_called_once();self.save.assert_not_called();self.assertEqual(self.stored[local.REFERENCES['ai_noise']],'old')
  with patch.object(ui.messagebox,'askyesno',return_value=True):self.window.save()
  self.save.assert_called_once_with(local.REFERENCES['ai_noise'],FAKE)
 def test_cancel_does_not_save(self):
  self.window.paste();self.window.cancel();self.assertTrue(self.root.destroyed);self.assertEqual(self.window.value.get(),'');self.save.assert_not_called()
 def test_storage_failure_and_check_failure_are_safe(self):
  self.window.paste();self.save.side_effect=OSError(5,'sensitive '+FAKE);self.window.save()
  self.assertIn('denied access',self.window.result.get());self.assertNotIn(FAKE,self.window.result.get())
  self.window.exists=MagicMock(side_effect=OSError(5,'details'));self.save.reset_mock();self.window.save()
  self.assertIn('Nothing was saved',self.window.result.get());self.save.assert_not_called()
 def test_save_readback_failure_does_not_claim_success(self):
  self.window.paste();self.window.exists=MagicMock(side_effect=[False,OSError(5,'fail')]);self.window.save()
  self.assertIn('could not be confirmed',self.window.result.get());self.assertNotIn('successfully stored',self.window.result.get())

class CredentialCheckTests(unittest.TestCase):
 def test_missing_and_access_failure_are_distinct(self):
  cred=local.credential_store;dll=MagicMock();dll.CredReadW.return_value=0
  with patch.object(cred.os,'name','nt'),patch.object(cred.ctypes,'WinDLL',return_value=dll),patch.object(cred.ctypes,'get_last_error',return_value=1168):
   self.assertFalse(cred.credential_exists('ARIADNE_DISCORD_AI_NOISE'))
  with patch.object(cred.os,'name','nt'),patch.object(cred.ctypes,'WinDLL',return_value=dll),patch.object(cred.ctypes,'get_last_error',return_value=5):
   with self.assertRaises(OSError):cred.credential_exists('ARIADNE_DISCORD_AI_NOISE')

if __name__=='__main__':unittest.main()
