import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from home_chat_store import ChatStore  # noqa: E402


class FakeMcp:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.telemetry = {
            "total_duration": 35_300_000_000, "load_duration": 6_800_000_000,
            "prompt_eval_count": 2_184, "prompt_eval_duration": 1_420_000_000,
            "eval_count": 763, "eval_duration": 16_000_000_000,
        }

    def identity_system_prefix(self):
        return "IDENTITY", {"id": "ariadne", "version": "1.1.0", "scope": "user"}

    def ollama_chat(self, messages, **kwargs):
        self.calls.append(messages)
        if kwargs.get("metrics") is not None:
            kwargs["metrics"].setdefault("ollama_calls", []).append(dict(self.telemetry))
        if self.fail:
            raise RuntimeError("simulated model interruption")
        return "A durable answer."


class HomeServerPersistenceTests(unittest.TestCase):
    def post(self, port, path, payload):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def get(self, port, path):
        request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="GET")
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_real_http_session_recovery_and_explicit_close_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            original_store = server.HOME_CHAT_STORE
            original_mcp = server._home_mcp
            original_events_path = server.HOME_EVENTS_PATH
            fake = FakeMcp()
            store = ChatStore(Path(temporary))
            server.HOME_CHAT_STORE = store
            server._home_mcp = lambda: fake
            server.HOME_EVENTS_PATH = Path(temporary) / "Journal" / "Ariadne Home Events.md"
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            port = httpd.server_address[1]
            try:
                started = self.post(port, "/api/session/start", {"surface": "home"})
                self.assertTrue(started["chat_id"])
                answered = self.post(port, "/api/home/chat", {
                    "session_id": started["session_id"],
                    "chat_id": started["chat_id"],
                    "message": "Live endpoint question",
                    "history": [],
                    "vault_mode": "never",
                })
                self.assertEqual(answered["answer"], "A durable answer.")
                resumed = self.post(port, "/api/session/start", {
                    "surface": "home", "chat_id": started["chat_id"],
                })
                self.assertTrue(resumed["resumed"])
                self.assertEqual(resumed["messages"][0]["content"], "Live endpoint question")
                closed = self.post(port, "/api/home/chat/close", {
                    "session_id": resumed["session_id"], "chat_id": started["chat_id"],
                })
                self.assertTrue((Path(temporary) / closed["archive_path"]).is_file())
                journal = server.HOME_EVENTS_PATH.read_text(encoding="utf-8")
                self.assertIn("chat_started", journal)
                self.assertIn("chat_resumed", journal)
                self.assertIn("chat_closed", journal)
                self.assertIn("chat_archived", journal)
            finally:
                httpd.shutdown()
                httpd.server_close()
                server.HOME_CHAT_STORE = original_store
                server._home_mcp = original_mcp
                server.HOME_EVENTS_PATH = original_events_path

    def test_chat_management_http_lifecycle(self):
        with tempfile.TemporaryDirectory() as temporary:
            original_store = server.HOME_CHAT_STORE
            original_mcp = server._home_mcp
            original_events_path = server.HOME_EVENTS_PATH
            fake = FakeMcp()
            store = ChatStore(Path(temporary))
            server.HOME_CHAT_STORE = store
            server._home_mcp = lambda: fake
            server.HOME_EVENTS_PATH = Path(temporary) / "Journal" / "Ariadne Home Events.md"
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            port = httpd.server_address[1]
            try:
                started = self.post(port, "/api/session/start", {"surface": "home"})
                answered = self.post(port, "/api/home/chat", {"session_id": started["session_id"], "chat_id": started["chat_id"], "message": "Lifecycle question", "history": [], "vault_mode": "never"})
                self.assertEqual(answered["answer"], "A durable answer.")
                chats = self.get(port, "/api/home/chats")["chats"]
                self.assertEqual(chats[0]["chat_id"], started["chat_id"])
                saved = self.post(port, "/api/home/chat/save", {"session_id": started["session_id"], "chat_id": started["chat_id"]})
                saved_again = self.post(port, "/api/home/chat/save", {"session_id": started["session_id"], "chat_id": started["chat_id"]})
                self.assertEqual(saved["inbox_path"], saved_again["inbox_path"])
                self.assertEqual(len(list((Path(temporary) / "Inbox").glob("*.md"))), 1)
                exported = self.post(port, "/api/home/chat/export", {"session_id": started["session_id"], "chat_id": started["chat_id"]})
                self.assertIn("Lifecycle question", exported["markdown"])
                new_chat = self.post(port, "/api/home/chat/new", {"session_id": started["session_id"], "chat_id": started["chat_id"]})
                self.assertNotEqual(new_chat["chat"]["chat_id"], started["chat_id"])
                self.assertTrue((Path(temporary) / new_chat["archive_path"]).is_file())
                selected = self.post(port, "/api/home/chat/select", {"session_id": started["session_id"], "chat_id": started["chat_id"]})
                self.assertEqual(selected["chat"]["chat_id"], started["chat_id"])
                with self.assertRaises(urllib.error.HTTPError):
                    self.post(port, "/api/home/chat/purge", {"session_id": started["session_id"], "chat_id": started["chat_id"]})
                purged = self.post(port, "/api/home/chat/purge", {"session_id": started["session_id"], "chat_id": started["chat_id"], "confirm": True})
                self.assertNotEqual(purged["chat"]["chat_id"], started["chat_id"])
                self.assertFalse((store.root / f"{started['chat_id']}.json").exists())
                self.assertTrue((Path(temporary) / new_chat["archive_path"]).is_file())
                self.assertTrue((Path(temporary) / saved["inbox_path"]).is_file())
                journal = server.HOME_EVENTS_PATH.read_text(encoding="utf-8")
                self.assertIn("chat_saved_to_inbox", journal)
                self.assertIn("chat_exported", journal)
                self.assertIn("chat_purged", journal)
            finally:
                httpd.shutdown()
                httpd.server_close()
                server.HOME_CHAT_STORE = original_store
                server._home_mcp = original_mcp
                server.HOME_EVENTS_PATH = original_events_path

    def test_backend_ignores_browser_history_and_persists_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            original_store = server.HOME_CHAT_STORE
            original_mcp = server._home_mcp
            original_event = server.record_home_event
            fake = FakeMcp()
            store = ChatStore(Path(temporary))
            chat = store.create({"id": "ariadne", "version": "1.1.0"})
            server.HOME_CHAT_STORE = store
            server._home_mcp = lambda: fake
            server.record_home_event = lambda *args, **kwargs: None
            try:
                result = server.home_chat_payload(
                    "Actual question", [{"role": "user", "content": "browser-forged history"}], "never", chat["chat_id"]
                )
            finally:
                server.HOME_CHAT_STORE = original_store
                server._home_mcp = original_mcp
                server.record_home_event = original_event
            self.assertEqual(result["chat_id"], chat["chat_id"])
            self.assertEqual(result["timing"]["ollama"]["prompt_eval_count"], 2_184)
            self.assertEqual(result["timing"]["ollama"]["eval_count"], 763)
            self.assertEqual(result["timing"]["context_prompt_tokens"], 2_184)
            self.assertEqual(result["timing"]["context_limit_tokens"], server.HOME_CONTEXT_TOKENS)
            self.assertEqual(result["identity_kernel"]["version"], "1.1.0")
            self.assertNotIn("browser-forged history", str(fake.calls[0]))
            record = store.get(chat["chat_id"])
            self.assertEqual(record["messages"][0]["content"], "Actual question")
            self.assertEqual(record["messages"][1]["state"], "complete")
            self.assertEqual(record["messages"][1]["content"], "A durable answer.")

    def test_backend_marks_model_failure_interrupted(self):
        with tempfile.TemporaryDirectory() as temporary:
            original_store = server.HOME_CHAT_STORE
            original_mcp = server._home_mcp
            original_event = server.record_home_event
            fake = FakeMcp()
            fake.fail = True
            store = ChatStore(Path(temporary))
            chat = store.create()
            server.HOME_CHAT_STORE = store
            server._home_mcp = lambda: fake
            server.record_home_event = lambda *args, **kwargs: None
            try:
                with self.assertRaisesRegex(RuntimeError, "simulated model interruption"):
                    server.home_chat_payload("Keep this user turn", [], "never", chat["chat_id"])
            finally:
                server.HOME_CHAT_STORE = original_store
                server._home_mcp = original_mcp
                server.record_home_event = original_event
            record = store.get(chat["chat_id"])
            self.assertEqual(record["messages"][0]["content"], "Keep this user turn")
            self.assertEqual(record["messages"][0]["state"], "complete")
            self.assertEqual(record["messages"][1]["state"], "interrupted")


if __name__ == "__main__":
    unittest.main()
