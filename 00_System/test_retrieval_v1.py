import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import ariadne_mcp  # noqa: E402


class RetrievalV1Tests(unittest.TestCase):
    def fake_record(self, document_id="doc-1", title="MCP retrieval"):
        return {
            "document_id": document_id,
            "page_title": title,
            "source_name": title,
            "processed_path": "Processed/test.md",
            "summary": "A local MCP retrieval note with chunk embeddings.",
            "primary_topic": "Knowledge Management",
            "people": [],
            "entities": [],
            "links": [],
            "related_notes": [],
            "subtopics": ["retrieval", "embeddings"],
            "tags": ["MCP"],
        }

    def run_fake(self, query, content, *, index=None, record=None):
        temporary = tempfile.TemporaryDirectory()
        path = Path(temporary.name) / "test.md"
        path.write_text(content, encoding="utf-8")
        record = record or self.fake_record()
        patches = [
            patch.object(ariadne_mcp, "load_library", return_value=[record]),
            patch.object(ariadne_mcp, "processed_path", return_value=path),
            patch.object(ariadne_mcp, "embedding_index", return_value=index),
            patch.object(ariadne_mcp, "ROOT", path.parent),
        ]
        for item in patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(patches)])
        self.addCleanup(temporary.cleanup)
        return ariadne_mcp.retrieve_evidence({"query": query, "limit": 5})

    def test_evidence_set_is_bounded_and_provenance_rich(self):
        result = self.run_fake(
            "MCP retrieval embeddings",
            "# Retrieval\n\nThis note describes MCP retrieval and chunk embeddings.",
        )
        self.assertLessEqual(result["selected_count"], 5)
        self.assertGreaterEqual(result["telemetry"]["candidate_count"], 1)
        item = result["results"][0]
        for field in (
            "source_path", "title", "excerpt", "score", "retrieval_method",
            "matched_terms", "entity_matches", "date", "reason", "citation",
        ):
            self.assertIn(field, item)
        self.assertLessEqual(len(item["excerpt"]), ariadne_mcp.MAX_CHUNK_CHARS)

    def test_existing_identity_aliases_are_reused(self):
        record = self.fake_record(title="Pope Kael project")
        record["people"] = ["@PopeKael"]
        matches = ariadne_mcp.identity_matches(record, "What did @pope_kael say?")
        self.assertIn("popekael", matches)

    def test_negative_partial_match_is_not_promoted(self):
        result = self.run_fake(
            "What is the recipe for quantum banana propulsion?",
            "# Science\n\nQuantum propulsion is discussed, but there is no banana recipe.",
        )
        self.assertEqual(result["selected_count"], 0)
        self.assertEqual(result["results"], [])

    def test_embedding_failure_preserves_lexical_fallback_and_logs_error(self):
        index = {
            "entries": {"chunk": {"chunk_id": "doc-1#chunk-0", "document_id": "doc-1", "embedding": [1.0]}},
            "model": "nomic-embed-text",
        }
        with patch.object(ariadne_mcp, "ollama_embed", side_effect=RuntimeError("offline")):
            result = self.run_fake(
                "MCP retrieval",
                "# Retrieval\n\nMCP retrieval remains available through lexical matching.",
                index=index,
            )
        self.assertEqual(result["telemetry"]["embedding_error"], "offline")
        self.assertGreaterEqual(result["selected_count"], 1)
        self.assertIn("lexical", result["results"][0]["retrieval_method"])


if __name__ == "__main__":
    unittest.main()
