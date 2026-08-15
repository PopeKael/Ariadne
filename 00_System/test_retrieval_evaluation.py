import unittest

from evaluate_retrieval import evaluate, relevant_rank


class RetrievalEvaluationTests(unittest.TestCase):
    def test_relevant_rank_accepts_document_or_chunk_expectations(self):
        results = [
            {"document_id": "other", "chunk_id": "other#chunk-0"},
            {"document_id": "wanted", "chunk_id": "wanted#chunk-2"},
        ]
        self.assertEqual(relevant_rank(results, {"expected_document_ids": ["wanted"]}), 2)
        self.assertEqual(relevant_rank(results, {"expected_chunk_ids": ["wanted#chunk-2"]}), 2)

    def test_evaluate_calculates_recall_and_mrr(self):
        responses = {
            "first": {"results": [{"document_id": "wanted", "chunk_id": "wanted#chunk-0"}]},
            "second": {"results": [{"document_id": "other", "chunk_id": "other#chunk-0"},
                                   {"document_id": "wanted", "chunk_id": "wanted#chunk-1"}]},
        }
        report = evaluate(
            [{"id": "one", "query": "first", "expected_document_ids": ["wanted"]},
             {"id": "two", "query": "second", "expected_document_ids": ["wanted"]}],
            search=lambda arguments: responses[arguments["query"]],
        )
        self.assertEqual(report["recall_at_k"], 1.0)
        self.assertEqual(report["mrr"], 0.75)


if __name__ == "__main__":
    unittest.main()
