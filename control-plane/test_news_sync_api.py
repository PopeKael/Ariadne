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
              INSERT INTO news_checkpoints VALUES('UCaaaaaaaaaaaaaaaaaaaaaa','abcdefghijk','2026-01-01T00:00:00Z','2026-01-01');
            """)
            db.commit()

    def tearDown(self):
        self.temp.cleanup()

    def test_registry_requires_bearer_and_excludes_disabled_sources(self):
        handler = Handler(self.path, {"Authorization": "Bearer sync-secret"})
        with patch("news_sync_api.credential_store.get_credential", return_value="sync-secret"):
            self.assertTrue(handle_get(handler, "/api/news-service/n8n/registry", self.path))
        self.assertEqual(handler.response[1], 200)
        self.assertEqual(len(handler.response[0]["sources"]), 1)
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
        self.assertEqual(payload["discord_delivery"]["sent"], 0)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM publications").fetchone()[0], 0)
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='news_settings'").fetchone())
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='publication_archive'").fetchone())


if __name__ == "__main__":
    unittest.main()
