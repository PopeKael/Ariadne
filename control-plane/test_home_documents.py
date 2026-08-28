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


class FakeDocumentMcp:
    def __init__(self):
        self.calls = []

    def identity_system_prefix(self):
        return "IDENTITY", {"id": "ariadne", "version": "1.1.0", "scope": "user"}

    def planned_knowledge_query(self, *args, **kwargs):
        return {"summary": "Vault summary.", "sources": [{"title": "Vault note", "citation_text": "Vault citation"}], "searches": [{"query": "test"}], "identity_kernel": {"version": "1.1.0"}}

    def ollama_chat(self, messages, **kwargs):
        self.calls.append(messages)
        return "Document answer."


class HomeDocumentHttpTests(unittest.TestCase):
    def post(self, port, path, payload):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_attachment_lifecycle_and_document_answer_are_chat_scoped(self):
        with tempfile.TemporaryDirectory() as temporary:
            original_store = server.HOME_CHAT_STORE
            original_mcp = server._home_mcp
            original_docs = server.DOCUMENT_WORK_ROOT
            original_events = server.record_home_event
            fake = FakeDocumentMcp()
            server.HOME_CHAT_STORE = ChatStore(Path(temporary))
            server._home_mcp = lambda: fake
            server.DOCUMENT_WORK_ROOT = Path(temporary) / "document_contexts"
            server.record_home_event = lambda *args, **kwargs: None
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            port = httpd.server_address[1]
            try:
                started = self.post(port, "/api/session/start", {"surface": "home"})
                attached = self.post(port, "/api/home/documents/attach", {
                    "session_id": started["session_id"], "chat_id": started["chat_id"],
                    "filename": "late.md",
                    "content": "---\ntitle: Late article\nauthor: Wazza\n---\n\n# Opening\n\nContext.\n\n# Narathiwat\n\nThe answer is Narathiwat.",
                })
                self.assertEqual(attached["document"]["title"], "Late article")
                self.assertEqual(attached["document"]["handling"], "direct")
                answered = self.post(port, "/api/home/chat", {
                    "session_id": started["session_id"], "chat_id": started["chat_id"],
                    "message": "What does this document say about Narathiwat?", "vault_mode": "never",
                    "tool_ids": ["document-analysis"],
                })
                self.assertTrue(answered["used_documents"])
                self.assertFalse(answered["used_vault"])
                self.assertIn("Narathiwat", fake.calls[0][-1]["content"])
                self.assertEqual(answered["document_analysis"]["retrieved_chunks"], 2)
                combined = self.post(port, "/api/home/chat", {
                    "session_id": started["session_id"], "chat_id": started["chat_id"],
                    "message": "Compare this document with my Vault.", "vault_mode": "always",
                    "tool_ids": ["document-analysis"],
                })
                self.assertTrue(combined["used_documents"])
                self.assertTrue(combined["used_vault"])
                self.assertTrue(any(item.get("source_type") == "attachment" for item in combined["sources"]))
                self.assertIn("Temporary document evidence", fake.calls[-1][-1]["content"])
                removed = self.post(port, "/api/home/documents/remove", {
                    "session_id": started["session_id"], "chat_id": started["chat_id"],
                    "document_id": attached["document"]["document_id"],
                })
                self.assertEqual(removed["documents"], [])
                fresh = self.post(port, "/api/home/chat/new", {
                    "session_id": started["session_id"], "chat_id": started["chat_id"],
                })
                self.assertEqual(fresh["documents"], [])
                with self.assertRaises(urllib.error.HTTPError):
                    self.post(port, "/api/home/documents/attach", {
                        "session_id": started["session_id"], "chat_id": fresh["chat"]["chat_id"],
                        "filename": "unsupported.pdf", "content": "not allowed",
                    })
            finally:
                httpd.shutdown()
                httpd.server_close()
                server.HOME_CHAT_STORE = original_store
                server._home_mcp = original_mcp
                server.DOCUMENT_WORK_ROOT = original_docs
                server.record_home_event = original_events


if __name__ == "__main__":
    unittest.main()
