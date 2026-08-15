from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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


if __name__ == "__main__":
    unittest.main()
