from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_rebuild import classify_outcome, run_record
from run_rebuild_pilot import reviewable_candidates, validate_semantics


class SemanticSafeguardTests(unittest.TestCase):
    def proposal(self, summary: str) -> dict[str, object]:
        return {"summary": summary, "entities": [], "people": [], "concepts": [], "links": []}

    def test_rejects_empty_and_refusal_summaries(self) -> None:
        self.assertEqual(validate_semantics(self.proposal("")), "summary_too_short")
        self.assertEqual(validate_semantics(self.proposal("I'm sorry, but I cannot provide that information in this request.")),
                         "policy_refusal_or_non_enrichment")

    def test_holds_generic_candidates_from_promotion_consideration(self) -> None:
        proposal = self.proposal("A sufficiently detailed summary of the source material for a semantic validation test.")
        proposal["concepts"] = ["model", "runtime", "useful concept"]
        held = reviewable_candidates(proposal)
        self.assertEqual(held["concepts"], ["model", "runtime"])

    def test_processing_failures_are_not_source_rejections(self) -> None:
        source = "---\ntitle: Useful source\n---\nA nonempty source with enough useful material for review."
        self.assertEqual(classify_outcome("empty_model_response", source), "retryable_processing_failure")
        self.assertEqual(classify_outcome("invalid_json_in_model_response", source), "retryable_processing_failure")
        self.assertEqual(classify_outcome("schema_keys_mismatch", source), "retryable_processing_failure")

    def test_short_summary_keeps_nonempty_source_for_review(self) -> None:
        source = "---\ntitle: Useful source\n---\nA nonempty source with enough useful material for review."
        self.assertEqual(classify_outcome("summary_too_short", source), "manual_review")
        self.assertEqual(classify_outcome("summary_too_short", "---\ntitle: Empty\n---\n"), "rejected_content")

    def test_run_record_preserves_retryable_empty_response_classification(self) -> None:
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Inbox").mkdir()
            path = root / "Inbox/source.md"
            path.write_text("---\ntitle: Source\n---\nUseful source text.\n", encoding="utf-8")
            record = {"stable_source_id": "sha256:test", "relative_path": "Inbox/source.md", "title": "Source", "source_type": "markdown"}
            with patch("run_rebuild.invoke_ollama", return_value=("{\"message\":{\"content\":\"\"}}", {"message": {"content": ""}}, None)):
                outcome, capture = run_record(root, record, ["Infrastructure"])
            self.assertEqual(outcome["status"], "retryable_processing_failure")
            self.assertEqual(outcome["reason"], "empty_model_response")
            self.assertEqual(capture["final_attempt"]["message_content"], "")


if __name__ == "__main__":
    unittest.main()
