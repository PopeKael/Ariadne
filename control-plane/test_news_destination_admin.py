import sqlite3
from test_news_discord_admin import Tests, WEBHOOK, PAYLOAD
class DestinationAdminTests(Tests):
 def test_admin_add_and_route(self):
  db=sqlite3.connect(self.db);db.execute("CREATE TABLE sources(id TEXT PRIMARY KEY, destination TEXT NOT NULL CHECK(destination IN ('thailand_news','ai_noise'))) ");db.execute("INSERT INTO sources VALUES ('channel','ai_noise')");db.commit();db.close()
  self.assertEqual(self.request('add-destination',{'name':'Chanya & Wazza','webhook':WEBHOOK})[0],403)
  self.login();status,v,_,raw=self.action('add-destination',{'name':'Chanya & Wazza','webhook':WEBHOOK});self.assertEqual(status,200);self.assertNotIn(WEBHOOK,raw);key=v['id']
  import news_destinations
  self.assertEqual(news_destinations.list_destinations(self.db)[-1]['name'],'Chanya & Wazza')
  db=sqlite3.connect(self.db);db.execute('UPDATE publications SET destination=? WHERE id=?',(key,'one'));db.commit();db.close()
  self.reset_rate();status,v,_,_=self.action('send',{'id':'one','destination':key,'payload':PAYLOAD,'confirmed':True});self.assertEqual(status,200);self.assertEqual(v['result']['status'],'sent');self.assertEqual(len(self.sent),1)
