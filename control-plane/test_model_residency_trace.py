from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class ModelResidencyTraceTests(unittest.TestCase):
    def test_trace_records_correlated_samples_and_memory_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            trace_path = Path(directory) / "model-residency.jsonl"
            snapshot = {
                "gpu": {"used_gb": 9.6, "total_gb": 16.0},
                "ollama_loaded": [{"name": "qwen3.5:9b-q4_K_M", "size_vram": 5_490_000_000}],
            }
            with patch.object(server, "MODEL_RESIDENCY_TRACE_PATH", trace_path), \
                    patch.object(server, "_model_residency_snapshot", return_value=snapshot):
                with server.model_residency_trace("request-123", "qwen3.5:9b-q4_K_M", "home_tldr",
                                                  interval_seconds=0.25, join_timeout_seconds=2):
                    time.sleep(0.3)

            rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["event"], "trace_started")
        self.assertTrue(any(row["event"] == "trace_completed" for row in rows))
        self.assertTrue(any(row["event"] == "trace_sample" for row in rows))
        self.assertTrue(all(row["request_id"] == "request-123" for row in rows))
        snapshots = [row["snapshot"] for row in rows if "snapshot" in row]
        self.assertTrue(snapshots)
        self.assertTrue(all(snapshot["gpu"]["used_gb"] == 9.6 for snapshot in snapshots))

    def test_trace_records_failure_without_swallowing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            trace_path = Path(directory) / "model-residency.jsonl"
            with patch.object(server, "MODEL_RESIDENCY_TRACE_PATH", trace_path), \
                    patch.object(server, "_model_residency_snapshot", return_value={"gpu": {"used_gb": 8.0}}):
                with self.assertRaisesRegex(RuntimeError, "expected"):
                    with server.model_residency_trace("request-456", "qwen3.5:9b-q4_K_M", "home_tldr",
                                                      join_timeout_seconds=2):
                        raise RuntimeError("expected")
            rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["event"], "trace_started")
        failure = next(row for row in rows if row["event"] == "trace_failed")
        self.assertTrue(any(row["event"] == "trace_completed" for row in rows))
        self.assertEqual(failure["detail"]["error_type"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
