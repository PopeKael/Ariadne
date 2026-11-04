import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from news_sync_api import handle_get, handle_post

CID = "UC" + "a" * 22


class Handler:
    def __init__(self, db_path, headers=None, body=None):
        self.headers = headers or {}
        self.db_path = db_path
        self.body = body or {}
        self.response = None

    def read_json(self):
        return self.body

    def send_json(self, payload, status=200):
        self.response = (payload, status)


class NewsSyncApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "news.sqlite3"
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript("""
              CREATE TABLE sources(id TEXT PRIMARY KEY,kind TEXT,name TEXT,friendly_name TEXT,destination TEXT,enabled INTEGER);
              CREATE TABLE news_checkpoints(source_id TEXT PRIMARY KEY,video_id TEXT,published_at TEXT,scanned_at TEXT);
              CREATE TABLE publications(id TEXT PRIMARY KEY,source_id TEXT,original_url TEXT,creator TEXT,title TEXT,
                published_at TEXT,summary TEXT,image_url TEXT,destination TEXT,collected_at TEXT,
                discord_status TEXT DEFAULT 'pending',external_status TEXT DEFAULT 'not_published', UNIQUE(source_id,original_url));
              INSERT INTO sources VALUES('UCaaaaaaaaaaaaaaaaaaaaaa','youtube','Creator',NULL,'ai_noise',1);
              INSERT INTO sources VALUES('UCbbbbbbbbbbbbbbbbbbbbbb','youtube','Disabled',NULL,'thailand_news',0);
              INSERT INTO news_checkpoints VALUES('UCaaaaaaaaaaaaaaaaaaaaaa','abcdefghijk','2026-01-01T00:00:00Z','2026-01-01');
              INSERT INTO news_checkpoints VALUES('UCbbbbbbbbbbbbbbbbbbbbbb','zyxwvutsrq','2026-01-01T00:00:00Z','2026-01-01');
            """)
            db.commit()

    def tearDown(self):
        self.temp.cleanup()

    def test_registry_requires_bearer_and_preserves_disabled_source_state(self):
        handler = Handler(self.path, {"Authorization": "Bearer sync-secret"})
        with patch("news_sync_api.credential_store.get_credential", return_value="sync-secret"):
            self.assertTrue(handle_get(handler, "/api/news-service/n8n/registry", self.path))
        self.assertEqual(handler.response[1], 200)
        self.assertEqual(len(handler.response[0]["sources"]), 2)
        enabled = {s["id"]: s["enabled"] for s in handler.response[0]["sources"]}
        self.assertTrue(enabled[CID])
        self.assertFalse(enabled["UC" + "b" * 22])
        self.assertIn("stage3_activated_at", handler.response[0])
        unauthorized = Handler(self.path)
        with patch("news_sync_api.credential_store.get_credential", return_value="sync-secret"):
            handle_get(unauthorized, "/api/news-service/n8n/registry", self.path)
        self.assertEqual(unauthorized.response[1], 401)

    def test_publication_sync_preserves_baseline_and_skips_disabled_source(self):
        body = {"version": 1, "publications": [
            {"source_id": CID, "video_id": "ABCDEFGHIJK", "title": "New", "published_at": "2026-01-02T00:00:00Z"},
        ]}
        handler = Handler(self.path, {"Authorization": "Bearer sync-secret", "Content-Type": "application/json",
                                      "Content-Length": str(len(json.dumps(body)))}, body)
        with patch("news_sync_api.credential_store.get_credential", return_value="sync-secret"):
            handle_post(handler, "/api/news-service/n8n/publications", self.path)
        self.assertEqual(handler.response[1], 200)
        self.assertEqual(handler.response[0]["inserted"], 1)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE sources SET enabled=0 WHERE id=?", (CID,))
            db.commit()
        handler = Handler(self.path, {"Authorization": "Bearer sync-secret", "Content-Type": "application/json",
                                      "Content-Length": str(len(json.dumps(body)))}, body)
        with patch("news_sync_api.credential_store.get_credential", return_value="sync-secret"):
            handle_post(handler, "/api/news-service/n8n/publications", self.path)
        self.assertEqual(handler.response[0]["inactive_sources_skipped"], 1)

    def test_sync_dry_run_uses_a_copy_and_never_persists_or_delivers(self):
        body = {"version": 1, "publications": [
            {"source_id": CID, "video_id": "ABCDEFGHIJK", "title": "New", "published_at": "2026-01-02T00:00:00Z"},
        ]}
        headers = {"Authorization": "Bearer sync-secret", "Content-Type": "application/json",
                   "Content-Length": str(len(json.dumps(body))), "X-Ariadne-Dry-Run": "true"}
        handler = Handler(self.path, headers, body)
        with patch("news_sync_api.credential_store.get_credential", return_value="sync-secret"):
            handle_post(handler, "/api/news-service/n8n/publications", self.path)
        payload, status = handler.response
        self.assertEqual(status, 200)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["inserted"], 1)
        self.assertEqual(payload["delivery_history_reconciled"], 0)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM publications").fetchone()[0], 0)
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='news_settings'").fetchone())
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='publication_archive'").fetchone())

    def test_sync_reconciles_delivery_history_idempotently_without_sending(self):
        body = {"version": 1, "publications": [
            {"source_id": CID, "video_id": "ABCDEFGHIJK", "title": "New", "published_at": "2026-01-02T00:00:00Z"},
        ], "delivery_history": [{
            "delivery_id": "video:ABCDEFGHIJK:ai_noise:1", "video_id": "ABCDEFGHIJK",
            "destination": "ai_noise", "status": "inflight", "started_at": "2026-01-02T00:01:00Z",
        }]}
        headers = {"Authorization": "Bearer sync-secret", "Content-Type": "application/json",
                   "Content-Length": str(len(json.dumps(body)))}
        with patch("news_sync_api.credential_store.get_credential", return_value="sync-secret"), \
             patch("news_discord_auto.deliver_sync", side_effect=AssertionError("Ariadne auto sender must stay disabled")):
            first = Handler(self.path, headers, body)
            handle_post(first, "/api/news-service/n8n/publications", self.path)
            body["delivery_history"][0].update({
                "status": "sent", "finished_at": "2026-01-02T00:01:02Z",
                "discord_message_id": "123456789012345678",
                "discord_channel_id": "628411830200434708", "http_status": 200,
            })
            repeated = Handler(self.path, headers, body)
            handle_post(repeated, "/api/news-service/n8n/publications", self.path)
            final = Handler(self.path, headers, body)
            handle_post(final, "/api/news-service/n8n/publications", self.path)
        self.assertEqual(first.response[1], 200)
        self.assertEqual(first.response[0]["delivery_history_reconciled"], 1)
        self.assertEqual(repeated.response[0]["delivery_history_reconciled"], 1)
        self.assertEqual(final.response[0]["delivery_history_reconciled"], 0)
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT publication_id,destination,status,discord_message_id FROM discord_delivery_attempts").fetchall()
        self.assertEqual(rows, [("youtube:ABCDEFGHIJK", "ai_noise", "sent", "123456789012345678")])

    def test_sync_maps_unresolved_inflight_receipt_to_uncertain(self):
        from news_sync_api import reconcile_delivery_history
        self.assertEqual(reconcile_delivery_history(self.path, [{
            "delivery_id": "video:ABCDEFGHIJK:ai_noise:1", "video_id": "ABCDEFGHIJK",
            "destination": "ai_noise", "status": "inflight", "started_at": 1790000000,
        }]), 1)
        with closing(sqlite3.connect(self.path)) as db:
            status = db.execute("SELECT status FROM discord_delivery_attempts").fetchone()[0]
        self.assertEqual(status, "uncertain")


if __name__ == "__main__":
    unittest.main()
