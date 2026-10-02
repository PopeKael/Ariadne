"""Attachment lifetime follows the resumable chat, not the browser session."""
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import server
from home_chat_store import ChatStore


class HomeAttachmentRetentionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(temporary)
        self.now = datetime.now(timezone.utc)
        self.store = ChatStore(self.root, now_fn=lambda: self.now)
        self.docs = self.root / "document_contexts"
        self.fixture = json.loads((ROOT / "test-fixtures" /
            "article-followup-08e15e7d1a4748ecbd8fa930c913d87c.json").read_text(encoding="utf-8"))
        for name, value in {
            "HOME_CHAT_STORE": self.store, "DOCUMENT_WORK_ROOT": self.docs,
            "SESSIONS": {}, "record_home_event": lambda *a, **k: None,
            "home_identity_kernel_metadata": lambda: {"id": "test"},
            "start_home_chat_model_preload": lambda: {"ok": True},
        }.items():
            self.stack.enter_context(patch.object(server, name, value))
        self.stack.enter_context(patch.object(server.CORE_INTERACTION_STREAM, "emit", return_value=None))

    def article_chat(self):
        record = server.HOME_CHAT_STORE.create()
        chat_id = record["chat_id"]
        record["messages"] = self.fixture["messages"]
        (self.store.root / (chat_id + ".json")).write_text(json.dumps(record), encoding="utf-8")
        self.docs.mkdir(exist_ok=True)
        workspace = {**self.fixture["document_workspace"], "chat_id": chat_id}
        path = self.docs / (chat_id + ".json")
        path.write_text(json.dumps(workspace), encoding="utf-8")
        return chat_id, path

    def post(self, port, path, payload):
        request = urllib.request.Request(f"http://localhost:{port}{path}",
            data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    def test_both_archive_paths_preserve_six_chunks_for_resume_then_purge(self):
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            port = httpd.server_address[1]
            for close_path in ("/api/home/chat/new", "/api/home/chat/close"):
                with self.subTest(close_path=close_path):
                    chat_id, workspace_path = self.article_chat()
                    original_bytes = workspace_path.read_bytes()
                    started = self.post(port, "/api/session/start", {"surface": "chat", "chat_id": chat_id})
                    original_documents = started["documents"]
                    self.assertEqual(original_documents[0]["chunk_count"], 6)
                    payload = {"session_id": started["session_id"], "chat_id": chat_id}
                    archived = self.post(port, close_path, payload)
                    self.assertEqual(server.HOME_CHAT_STORE.get(chat_id)["status"], "closed")
                    archive_path = self.root / archived["archive_path"]
                    self.assertTrue(archive_path.is_file())
                    fresh = archived if close_path.endswith("new") else self.post(port, "/api/home/chat/new", payload)
                    self.assertNotEqual(fresh["chat"]["chat_id"], chat_id)
                    self.assertEqual(fresh["documents"], [])
                    self.assertEqual(workspace_path.read_bytes(), original_bytes)

                    # Recreate the store, then select the archived chat through
                    # the same sidebar endpoint as Home. Session start resumes
                    # active chats; sidebar selection reopens closed chats.
                    server.HOME_CHAT_STORE = ChatStore(self.root, now_fn=lambda: self.now)
                    selected = self.post(port, "/api/home/chat/select", {"session_id": started["session_id"], "chat_id": chat_id})
                    self.assertEqual(selected["documents"], original_documents)
                    resumed = self.post(port, "/api/session/start", {"surface": "chat", "chat_id": chat_id})
                    self.assertTrue(resumed["resumed"])
                    self.assertEqual(resumed["documents"], original_documents)
                    evidence = server.retrieve_documents(self.docs, chat_id, self.fixture["followup"], 16384)
                    article = self.fixture["document_workspace"]["documents"][0]
                    self.assertEqual(evidence["retrieved_chunks"], 6)
                    self.assertEqual([c["content"] for c in evidence["chunks"]], [c["content"] for c in article["chunks"]])
                    self.assertEqual(evidence["documents"][0]["document_id"], article["document_id"])
                    self.assertEqual(evidence["documents"][0]["metadata"], article["metadata"])

                    with self.assertRaises(urllib.error.HTTPError) as rejected:
                        self.post(port, "/api/home/chat/purge", payload)
                    self.assertEqual(rejected.exception.code, 400)
                    self.assertTrue(workspace_path.is_file())
                    self.post(port, "/api/home/chat/purge", {**payload, "confirm": True})
                    self.assertFalse(workspace_path.exists())
                    self.assertIsNone(server.HOME_CHAT_STORE.get(chat_id))
                    self.assertTrue(archive_path.is_file())
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_retention_expiry_removes_only_expired_attachment_workspace(self):
        chat_id, workspace_path = self.article_chat()
        server.HOME_CHAT_STORE.close_and_archive(chat_id)
        self.assertEqual(server.expire_home_chats(), [])
        self.assertTrue(workspace_path.is_file())
        self.now += timedelta(days=8)
        active_id, active_workspace = self.article_chat()
        expired = server.expire_home_chats()
        self.assertEqual([record["chat_id"] for record in expired], [chat_id])
        self.assertFalse(workspace_path.exists())
        self.assertTrue((self.root / expired[0]["archive_path"]).is_file())
        self.assertTrue(active_workspace.is_file())
        self.assertIsNotNone(server.HOME_CHAT_STORE.get(active_id))


if __name__ == "__main__":
    unittest.main()
