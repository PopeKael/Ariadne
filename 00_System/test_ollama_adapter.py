from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_rebuild_pilot import final_content, model_request


class OllamaAdapterTests(unittest.TestCase):
    def test_uses_verified_chat_schema_adapter(self) -> None:
        record = {"stable_source_id": "sha256:test", "title": "Test", "source_type": "markdown"}
        request = model_request(record, "The server is named Cerberus.", ["Infrastructure"])
        self.assertEqual(request["model"], "gpt-oss:20b")
        self.assertFalse(request["stream"])
        self.assertIn("messages", request)
        self.assertNotIn("prompt", request)
        self.assertNotIn("think", request)
        self.assertEqual(request["format"]["required"], ["proposed_domains", "summary", "entities", "people", "concepts", "links", "confidence", "notes"])
        self.assertEqual(request["format"]["properties"]["proposed_domains"]["items"]["enum"], ["Infrastructure"])
        self.assertEqual(request["options"], {"temperature": 0, "seed": 42})

    def test_reads_chat_final_content_and_thinking(self) -> None:
        content, thinking = final_content({"message": {"content": '{"answer":"Cerberus"}', "thinking": "brief reasoning"}})
        self.assertEqual(content, '{"answer":"Cerberus"}')
        self.assertEqual(thinking, "brief reasoning")


if __name__ == "__main__":
    unittest.main()
