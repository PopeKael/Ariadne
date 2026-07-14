import unittest

from ariadne_mcp import build_citation, format_citation, markdown_chunks


class CitationTests(unittest.TestCase):
    def test_chunk_line_anchors_cover_the_source_passage(self):
        content = "# Heading\n\nFirst paragraph.\n\nSecond paragraph."
        chunks = markdown_chunks(content, size=100)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["line_start"], 1)
        self.assertEqual(chunks[0]["line_end"], 5)

    def test_complete_external_citation_has_human_readable_form(self):
        record = {"document_id": "doc-1", "page_title": "Example", "source_url": "https://example.test", "source_name": "Example source"}
        citation = build_citation(record, "Processed/example.md", "Heading", "doc-1#chunk-0", 4, 7)
        self.assertEqual(citation["status"], "complete")
        self.assertEqual(citation["issues"], [])
        self.assertEqual(format_citation(citation), "Example — Heading, lines 4–7; https://example.test")

    def test_local_note_is_valid_vault_only_citation(self):
        record = {"document_id": "doc-2", "source_name": "Local note"}
        citation = build_citation(record, "Processed/local.md", "Document", "doc-2#chunk-0", 1, 1)
        self.assertEqual(citation["status"], "vault-only")
        self.assertEqual(citation["issues"], [])


if __name__ == "__main__":
    unittest.main()
