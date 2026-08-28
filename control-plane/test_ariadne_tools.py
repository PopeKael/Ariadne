import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ariadne_tools import (  # noqa: E402
    TOOL_REGISTRY,
    attach_document,
    clear_documents,
    list_documents,
    parse_front_matter,
    remove_document,
    retrieve_documents,
)


class AriadneToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.chat_id = "0123456789abcdef0123456789abcdef"

    def tearDown(self):
        self.temp.cleanup()

    def test_registry_exposes_document_analysis_metadata(self):
        tool = next(item for item in TOOL_REGISTRY.discover() if item["tool_id"] == "document-analysis")
        self.assertIn(".md", tool["supported_input_types"])
        self.assertIn("retrieve chunks", tool["capabilities"])

    def test_front_matter_is_preserved_separately_from_body(self):
        metadata, body = parse_front_matter("---\ntitle: Test article\nauthor: Wazza\ntags: [ai, vault]\n---\n\nBody text")
        self.assertEqual(metadata["title"], "Test article")
        self.assertEqual(metadata["author"], "Wazza")
        self.assertEqual(metadata["tags"], ["ai", "vault"])
        self.assertEqual(body.strip(), "Body text")
        block_metadata, _ = parse_front_matter("---\nauthor:\n  - Wazza\ntags:\n  - clippings\n---\n\nBody")
        self.assertEqual(block_metadata["author"], ["Wazza"])
        self.assertEqual(block_metadata["tags"], ["clippings"])

    def test_large_document_retrieves_late_chunk_without_direct_injection(self):
        content = "# Introduction\n\n" + ("ordinary material\n\n" * 900) + "# Narathiwat\n\nThe late-document answer is Narathiwat."
        document = attach_document(self.root, self.chat_id, "article.md", content)
        self.assertEqual(document["handling"], "chunked")
        result = retrieve_documents(self.root, self.chat_id, "What does this say about Narathiwat?", 16_384)
        self.assertGreater(result["retrieved_chunks"], 0)
        self.assertIn("Narathiwat", result["context"])
        self.assertLess(result["context_chars"], len(content))

    def test_remove_and_new_chat_isolation(self):
        first = attach_document(self.root, self.chat_id, "one.txt", "private temporary text")
        self.assertEqual(len(list_documents(self.root, self.chat_id)), 1)
        self.assertTrue(remove_document(self.root, self.chat_id, first["document_id"]))
        self.assertEqual(list_documents(self.root, self.chat_id), [])
        other_chat = "fedcba9876543210fedcba9876543210"
        attach_document(self.root, other_chat, "two.txt", "different chat")
        self.assertEqual(list_documents(self.root, self.chat_id), [])
        clear_documents(self.root, other_chat)
        self.assertEqual(list_documents(self.root, other_chat), [])

    def test_original_content_is_not_modified_and_unsupported_types_fail(self):
        source = self.root / "source.md"
        original = "---\ntitle: Immutable\n---\n\nDo not edit me."
        source.write_text(original, encoding="utf-8")
        attach_document(self.root, self.chat_id, source.name, source.read_text(encoding="utf-8"))
        self.assertEqual(source.read_text(encoding="utf-8"), original)
        with self.assertRaisesRegex(ValueError, "Only .md and .txt"):
            attach_document(self.root, self.chat_id, "image.png", "not supported")


if __name__ == "__main__":
    unittest.main()
