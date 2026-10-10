import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from watchlist import Watchlist, stamp
import watchlist_sources as sources


EVIDENCE = [{"title": "A release", "url": "https://example.com/release", "snippet": "AMD support is experimental."}]


class WatchlistTests(unittest.TestCase):
    def test_check_all_queues_active_sources_and_preserves_reminders_and_live_claims(self):
        active = self.create()
        paused = self.create(identity='paused')
        self.store.action(paused['id'],'pause')
        archived = self.create(identity='archived')
        self.store.action(archived['id'],'archive')
        reminder = self.store.save(dict(title='Windows reminder',kind='reminder',interval_days=28))
        busy = self.create(identity='busy')
        with self.store.db() as db:
            db.execute('UPDATE watches SET token=?,lease_until=? WHERE id=?',('claimed',stamp(self.now+timedelta(minutes=15)),busy['id']))
        untouched = {w['id']:self.store.get(w['id']) for w in (paused,archived,reminder,busy)}
        result = self.store.check_all()
        self.assertEqual(result,dict(ok=True,queued=1,checking=1))
        self.assertEqual(self.store.get(active['id'])['next_check'],stamp(self.now))
        for key,data in untouched.items():
            self.assertEqual(self.store.get(key),data)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT token FROM watches WHERE id=?',(busy['id'],)).fetchone()[0],'claimed')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 10, 7, tzinfo=timezone.utc)
        self.findings = list(EVIDENCE)
        self.calls = []
        def collect(watch):
            self.calls.append(watch["id"])
            return self.findings
        self.store = Watchlist(Path(self.temp.name) / "watchlist.sqlite3", collect, lambda: self.now)
        self.addCleanup(self.store.stop)

    def create(self, **kwargs):
        return self.store.save(dict(title="Audio AI", kind="topic", query="local audio AI AMD", interval_days=7, **kwargs))

    def test_persistent_watches_and_idempotent_rabbit_import_keep_decisions(self):
        watch = self.create(identity="github:https://github.com/example/audio")
        self.store.action(watch["id"], "archive")
        duplicate = self.create(identity=watch["identity"])
        self.assertEqual(duplicate["id"], watch["id"])
        self.assertEqual(duplicate["state"], "archived")
        for n in range(8):
            self.store.save(dict(title=f"Topic {n}", query=f"question {n}"))
        reopened = Watchlist(self.store.path, lambda _: [], lambda: self.now)
        self.assertEqual(len(reopened.snapshot()["watches"]), 9)
        self.assertEqual(reopened.get(watch["id"])["state"], "archived")

    def test_first_check_unchanged_reordered_results_and_changed_evidence(self):
        watch = self.create()
        self.findings = EVIDENCE + [{"title":"Other", "url":"https://example.com/other", "snippet":"Offline"}]
        self.assertTrue(self.store.tick())
        self.assertFalse(self.store.tick())
        self.assertEqual(self.store.history(watch["id"])["checks"][0]["status"], "baseline")
        self.store.action(watch["id"], "acknowledge")
        self.findings = list(reversed(self.findings))
        self.now += timedelta(days=7)
        self.store.tick()
        self.assertFalse(self.store.get(watch["id"])["unread"])
        self.assertEqual(self.store.history(watch["id"])["checks"][0]["status"], "unchanged")
        self.findings = [{**EVIDENCE[0], "snippet":"AMD support is now documented."}]
        self.now += timedelta(days=7)
        self.store.tick()
        self.assertTrue(self.store.get(watch["id"])["unread"])
        self.assertEqual(self.store.history(watch["id"])["checks"][0]["status"], "changed")

    def test_manual_github_watch_and_rabbit_hole_use_the_same_identity(self):
        manual = self.store.save(dict(title="My project",kind="project",sources=["https://github.com/Example/Audio/blob/main/README.md"]))
        self.store.action(manual["id"],"pause")
        rabbit = self.store.save(dict(title="example/audio",kind="project",sources=["https://github.com/example/audio"],identity="github:https://github.com/example/audio"))
        self.assertEqual(manual["id"],rabbit["id"])
        self.assertEqual(rabbit["state"],"paused")
        self.assertEqual(rabbit["title"],"My project")

    def test_failure_and_empty_results_preserve_evidence_and_success_date(self):
        watch = self.create()
        self.store.tick()
        successful = self.store.get(watch["id"])
        self.now += timedelta(days=7)
        self.findings = []
        self.store.tick()
        failed = self.store.get(watch["id"])
        self.assertEqual(failed["evidence"], successful["evidence"])
        self.assertEqual(failed["last_success"], successful["last_success"])
        self.assertIn("No usable evidence", failed["last_error"])
        self.assertEqual(failed["next_check"], stamp(self.now + timedelta(hours=1)))
        self.assertEqual(self.store.history(watch["id"])["checks"][0]["status"], "failed")

    def test_reminder_catches_up_after_restart_stays_due_and_repeats_from_done(self):
        reminder = self.store.save(dict(title="Windows schedule", kind="reminder", interval_days=28))
        self.assertEqual(reminder["next_check"], stamp(self.now + timedelta(days=28)))
        self.assertFalse(self.store.tick())
        self.now += timedelta(days=35)
        restarted = Watchlist(self.store.path, lambda _: self.fail("Reminder must not search"), lambda: self.now)
        restarted.tick()
        due = restarted.get(reminder["id"])
        self.assertTrue(due["reminder_due"])
        self.assertFalse(restarted.tick())
        restarted.action(reminder["id"], "acknowledge")
        self.assertTrue(restarted.get(reminder["id"])["reminder_due"])
        done = restarted.action(reminder["id"], "done")
        self.assertFalse(done["reminder_due"])
        self.assertEqual(done["next_check"], stamp(self.now + timedelta(days=28)))
        self.assertEqual([c["status"] for c in restarted.history(reminder["id"])["checks"]], ["done", "due"])

    def test_pause_snooze_resume_and_archive_never_remove_history(self):
        watch = self.create()
        self.store.tick()
        self.store.action(watch["id"], "pause")
        self.now += timedelta(days=8)
        self.assertFalse(self.store.tick())
        self.store.action(watch["id"], "resume")
        self.store.action(watch["id"], "snooze", 7)
        self.assertFalse(self.store.tick())
        self.now += timedelta(days=7)
        self.assertTrue(self.store.tick())
        self.store.action(watch["id"], "archive")
        self.assertEqual(len(self.store.history(watch["id"])["checks"]), 2)

    def test_inflight_claim_prevents_double_checks_and_pause_discards_stale_result(self):
        watch = self.create()
        other = Watchlist(self.store.path, lambda _: self.fail("Claim must prevent another worker"), lambda: self.now)
        def collect(_watch):
            self.assertFalse(other.tick())
            busy = self.store.action(watch["id"], "check")
            self.assertTrue(busy["checking"])
            self.store.action(watch["id"], "pause")
            return EVIDENCE
        self.store.collect = collect
        self.store.tick()
        saved = self.store.get(watch["id"])
        self.assertEqual(saved["state"], "paused")
        self.assertIsNone(saved["last_checked"])

    def test_expired_worker_lease_is_recovered(self):
        watch = self.create()
        with self.store.db() as db:
            db.execute("UPDATE watches SET token='abandoned',lease_until=? WHERE id=?", (stamp(self.now + timedelta(minutes=15)),watch["id"]))
        self.assertFalse(self.store.tick())
        self.now += timedelta(minutes=16)
        self.assertTrue(self.store.tick())
        self.assertFalse(self.store.get(watch["id"])["checking"])

    def test_acknowledgement_during_unchanged_check_does_not_discard_the_check(self):
        watch = self.create()
        self.store.tick()
        self.store.action(watch["id"], "check")
        def collect(_watch):
            self.store.action(watch["id"], "acknowledge")
            return EVIDENCE
        self.store.collect = collect
        self.store.tick()
        self.assertFalse(self.store.get(watch["id"])["unread"])
        self.assertEqual(self.store.history(watch["id"])["checks"][0]["status"], "unchanged")

    def test_normal_stop_releases_only_this_workers_claims(self):
        watch = self.create()
        other = self.create(identity="other")
        with self.store.db() as db:
            for watch_id,token in [(watch["id"],self.store.owner+":inflight"),(other["id"],"another:inflight")]:
                db.execute("UPDATE watches SET token=?,lease_until=? WHERE id=?",(token,stamp(self.now+timedelta(minutes=15)),watch_id))
        self.store.stop()
        self.assertFalse(self.store.get(watch["id"])["checking"])
        self.assertTrue(self.store.get(other["id"])["checking"])

    def test_rescheduling_due_reminder_and_snoozing_clear_due_message(self):
        reminder = self.store.save(dict(title="Windows",kind="reminder",next_check=stamp(self.now),interval_days=28))
        self.store.tick()
        self.assertTrue(self.store.get(reminder["id"])["reminder_due"])
        revised = self.store.save({**reminder,"next_check":stamp(self.now+timedelta(days=2))})
        self.assertFalse(revised["reminder_due"])
        self.assertEqual(revised["summary"],"Next reminder scheduled.")
        snoozed = self.store.action(reminder["id"],"snooze",7)
        self.assertIn("Snoozed",snoozed["summary"])

    def test_history_pagination_does_not_drop_old_checks(self):
        watch = self.create()
        for _ in range(33):
            self.store.tick()
            self.now += timedelta(days=7)
        first = self.store.history(watch["id"])
        second = self.store.history(watch["id"],first["before"])
        self.assertEqual(len(first["checks"]),30)
        self.assertEqual(len(second["checks"]),3)
        self.assertFalse({c["id"] for c in first["checks"]} & {c["id"] for c in second["checks"]})

    def test_scheduler_checks_without_a_browser(self):
        done = threading.Event()
        watch = self.create()
        self.store.collect = lambda _: (done.set() or EVIDENCE)
        self.store.start()
        self.assertTrue(done.wait(3))
        self.store.stop()
        self.assertEqual(self.store.get(watch["id"])["evidence"],EVIDENCE)

    def test_invalid_input_does_not_create_watches(self):
        for body in [dict(title=""),dict(title="Bad",interval_days=True),dict(title="Bad",kind="run"),
                     dict(title="Bad",sources=["file:///C:/secret"]),dict(title="Bad",sources=["http://127.0.0.1/"]),
                     dict(title="Bad",query="q",next_check="2026-10-10")]:
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.store.save(body)
        self.assertEqual(self.store.snapshot()["watches"],[])


class SourceTests(unittest.TestCase):
    def test_private_dns_and_redirects_are_rejected(self):
        with patch.object(sources.socket,"getaddrinfo",return_value=[(None,None,None,None,("192.168.1.200",443))]):
            with self.assertRaises(ValueError): sources.public_url("https://public-looking.example/")
        with self.assertRaises(ValueError):
            sources.PublicRedirects().redirect_request(Request("https://example.com"),None,302,"Found",{},"http://127.0.0.1/")
        with patch.object(sources,"public_url",side_effect=lambda value: value):
            request = Request("https://api.github.com",headers={"Authorization":"Bearer fixture"})
            with self.assertRaises(ValueError):
                sources.PublicRedirects().redirect_request(request,None,302,"Found",{},"https://other.example/")

    def test_github_reads_readme_releases_issues_without_executing(self):
        requests = []
        def read(url, **kwargs):
            requests.append(url)
            if url.endswith("/readme"):
                data = dict(content=base64.b64encode(b"# Tool\nAMD support is experimental.\nNeeds 10 GB disk.").decode(),html_url="https://github.com/example/tool/blob/main/README.md")
            elif "/releases" in url:
                data = [dict(name="v1",html_url="https://github.com/example/tool/releases/tag/v1",body="Windows support")]
            else:
                data = [dict(title="AMD broken",html_url="https://github.com/example/tool/issues/1",body="gfx1101 fails"),
                        dict(title="PR",html_url="https://github.com/example/tool/pull/2",body="ignored",pull_request={})]
            return json.dumps(data).encode(),"utf-8"
        with patch.object(sources,"read_url",side_effect=read):
            evidence = sources.github_sources("https://github.com/example/tool","AMD Windows disk gfx1101")
        self.assertEqual(len(requests),3)
        self.assertEqual(len(evidence),3)
        self.assertIn("experimental",evidence[0]["snippet"])
        self.assertIn("gfx1101 fails",evidence[-1]["snippet"])

    def test_topic_search_and_webpage_share_evidence_contract(self):
        watch = dict(purpose="AMD",query="AMD audio",sources=["https://example.com/tool"])
        with patch.object(sources,"read_url",return_value=(b"<html><title>Tool</title><article><p>AMD local audio support and installation details are described here.</p></article></html>","utf-8")):
            result = sources.collect_evidence(watch,lambda *args,**kwargs: dict(ok=True,results=EVIDENCE))
        self.assertEqual(len(result),2)
        self.assertTrue(all(set(item)=={"title","url","snippet"} for item in result))

    def test_partial_failure_is_visible_not_a_silent_success(self):
        watch = dict(purpose="AMD",query="AMD audio",sources=["https://example.com/tool"])
        with patch.object(sources,"read_url",side_effect=TimeoutError()):
            with self.assertRaisesRegex(ValueError,"Some sources could not be checked"):
                sources.collect_evidence(watch,lambda *args,**kwargs: dict(ok=True,results=EVIDENCE))


class WatchlistHTTPTests(unittest.TestCase):
    def test_watchlist_is_a_tool_and_links_keep_the_current_origin(self):
        from plugin_registry import validate_manifest
        root = Path(__file__).resolve().parent
        shell = (root / "page-shell.js").read_text(encoding="utf-8")
        self.assertNotIn('["Watchlist","/watchlist"', shell)
        self.assertIn('["Tools","/plugins",["/plugins","/rabbit-hole","/watchlist"]]', shell)
        for name, route in [("watchlist", "/watchlist"), ("rabbit-hole", "/rabbit-hole")]:
            manifest = validate_manifest(json.loads((root / "plugins" / name / "plugin.json").read_text(encoding="utf-8")))
            self.assertEqual(manifest["ui"]["route"], route)
        page = (root / "watchlist.html").read_text(encoding="utf-8")
        self.assertIn('href="/plugins"', page)
        self.assertIn('href="/rabbit-hole"', page)
        self.assertNotIn("127.0.0.1", page)
        self.assertNotIn("localhost", page)

    def test_http_crud_history_duplicate_and_news_import(self):
        import server
        with tempfile.TemporaryDirectory() as temp:
            store = Watchlist(Path(temp)/"watchlist.sqlite3",lambda _:EVIDENCE)
            httpd = server.ThreadingHTTPServer(("127.0.0.1",0),server.AriadneHandler)
            thread = threading.Thread(target=httpd.serve_forever,daemon=True)
            thread.start()
            def request(path,body=None):
                req = Request(f"http://127.0.0.1:{httpd.server_port}"+path,
                              data=json.dumps(body).encode() if body is not None else None,
                              headers={"Content-Type":"application/json","X-Forwarded-Proto":"https"})
                with urlopen(req,timeout=5) as response:
                    raw = response.read().decode()
                    return json.loads(raw) if "json" in response.headers.get("Content-Type","") else raw
            try:
                with patch.object(server,"WATCHLIST",store),patch.object(server,"_expire_sessions"),patch.object(server.SIGNAL_SERVICE_CLIENT,"_get",return_value={"ok":True,"topics":[{"topic_id":"legacy","topic":"Privacy watch"}]}):
                    self.assertIn("Check all now",request("/watchlist"))
                    self.assertIn("Save watch",request("/watchlist"))
                    for asset in ["/watchlist.css","/watchlist.js"]: self.assertTrue(request(asset))
                    body=dict(title="Project",kind="project",sources=["https://github.com/example/tool"],identity="github:https://github.com/example/tool")
                    watch=request("/api/watchlist",body)["watch"]
                    self.assertEqual(request("/api/watchlist",body)["watch"]["id"],watch["id"])
                    store.tick()
                    self.assertEqual(request('/api/watchlist/check-all',{})['queued'],1)
                    self.assertEqual(request(f'/api/watchlist/{watch["id"]}/history')["checks"][0]["status"],"baseline")
                    for action in ["pause","resume","snooze","archive"]:
                        self.assertTrue(request(f'/api/watchlist/{watch["id"]}/action',dict(action=action,days=7))["ok"])
                    request("/api/watchlist/import-news-topics",{})
                    request("/api/watchlist/import-news-topics",{})
                    self.assertEqual(len(request("/api/watchlist")["watches"]),2)
                    with self.assertRaises(HTTPError) as caught: request("/api/watchlist",dict(title="Invalid"))
                    self.assertEqual(caught.exception.code,400)
            finally:
                httpd.shutdown();httpd.server_close();thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
