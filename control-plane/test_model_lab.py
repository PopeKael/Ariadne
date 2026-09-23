from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


QWEN_CAPABILITIES = {
    "available": True,
    "capabilities": ["completion", "thinking"],
    "context_max": 262144,
    "text": True,
    "vision": False,
    "tools": False,
    "structured_output": False,
    "reasoning_modes": ["off", "thinking"],
}


class ModelLabTests(unittest.TestCase):
    @staticmethod
    def canonical_documents():
        documents, error = server.model_lab_fixture_documents("test-01")
        if error:
            raise AssertionError(error)
        return documents

    def test_payload_exposes_recipes_and_controlled_boundaries(self):
        payload = server.model_lab_payload()
        self.assertEqual(payload["default_profile"], "long-document")
        self.assertTrue(any(item["id"] == "reasoning-on" for item in payload["profiles"]))
        self.assertEqual({item["id"] for item in payload["reasoning_levels"]}, {"off", "low", "medium", "high", "max"})
        self.assertEqual([item["id"] for item in payload["test_cases"]], [f"test-0{index}" for index in range(1, 8)])
        self.assertEqual(payload["test_cases"][0]["canonical_parameters"]["context_tokens"], 16384)
        self.assertEqual(payload["test_cases"][0]["source_material"]["count"], 2)
        self.assertEqual([item["name"] for item in payload["test_cases"][0]["source_material"]["default_documents"]], ["1. Ten Years in Thailand.md", "2. YouTube Package.md"])
        documents = self.canonical_documents()
        self.assertEqual(payload["test_cases"][0]["benchmark_instructions"], documents[1]["content"])
        self.assertEqual(payload["test_cases"][0]["source_material"]["default_documents"][0]["role"], "source material")
        self.assertEqual(payload["test_cases"][0]["source_material"]["default_documents"][1]["role"], "test instructions")
        generation = payload["benchmark_generations"]["test-01"]
        self.assertEqual(generation["id"], server.model_lab_generation_id("test-01"))
        self.assertEqual(generation["definition"]["documents"][1]["sha256"], "73ce8076796de542e18c5831939fcb0e764db7a33fb454a7fd6861baf83aee03")
        self.assertFalse(payload["controls"]["web"])
        self.assertFalse(payload["controls"]["vault_retrieval"])

    def test_test_one_fixture_bundle_is_loadable_without_upload(self):
        documents, error = server.model_lab_fixture_documents("test-01")
        self.assertIsNone(error)
        self.assertEqual([item["name"] for item in documents], ["1. Ten Years in Thailand.md", "2. YouTube Package.md"])
        self.assertTrue(all(item["content"] and len(item["sha256"]) == 64 for item in documents))
        self.assertEqual([item["role"] for item in documents], ["source material", "test instructions"])
        self.assertEqual([item["sha256"] for item in documents], [item["canonical_sha256"] for item in documents])
        self.assertNotIn("Open WebUI", documents[1]["content"])
        self.assertNotIn("qwen3.5", documents[1]["content"])
        self.assertIn("Use exactly these seven sections, in this order.", documents[1]["content"])
        self.assertIn("Create 8–12 YouTube chapters.", documents[1]["content"])
        self.assertIn("Before answering, silently verify:", documents[1]["content"])

    def test_standard_run_records_effective_options_without_changing_home_route(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.jsonl"
            catalog = {"available": True, "models": [{"name": "test-model", "size": 123}], "loaded": [], "loaded_details": []}
            response = {
                "response": "The answer is in the supplied context.",
                "thinking": "",
                "done": True,
                "done_reason": "stop",
                "total_duration": 2_000_000_000,
                "prompt_eval_count": 120,
                "eval_count": 24,
                "eval_duration": 1_000_000_000,
            }
            documents = self.canonical_documents()
            with patch.object(server, "MODEL_LAB_RUNS_PATH", path), \
                 patch.object(server, "ollama_catalog", return_value=catalog), \
                 patch.object(server, "ollama_model_capability_details", return_value=QWEN_CAPABILITIES), \
                 patch.object(server, "_send_avatar_event_with_retry", return_value=True), \
                 patch.object(server, "ai_gpu_admission"), \
                 patch.object(server, "model_activity"), \
                 patch.object(server, "post_json", return_value=response) as post:
                result, status = server.run_model_lab({
                    "model": "test-model",
                    "profile": "long-document",
                    "test_case_id": "test-01",
                    "context_tokens": 16384,
                    "output_tokens": 4096,
                    "temperature": 0,
                    "top_p": 0.9,
                    "seed": 42,
                    "reasoning_level": "off",
                    "documents": [{key: item[key] for key in ("order", "name", "role", "size", "sha256")} for item in documents],
                    "prefill": documents[0]["content"],
                })
            self.assertEqual(status, 200)
            self.assertTrue(result["ok"])
            run = result["run"]
            self.assertEqual(run["classification"], "STANDARD")
            self.assertEqual(run["benchmark_status"], "CANONICAL")
            self.assertEqual(run["benchmark_generation_id"], server.model_lab_generation_id("test-01"))
            self.assertEqual(run["run_state"], "COMPLETED")
            self.assertEqual(run["effective"]["context_tokens"], 16384)
            self.assertEqual(run["effective"]["thinking"], "off")
            self.assertEqual(run["effective"]["reasoning_level"], "off")
            self.assertFalse(run["effective"]["native_reasoning_mapping"]["value"])
            self.assertEqual(run["effective"]["document_count"], 2)
            self.assertEqual(run["test_case_id"], "test-01")
            self.assertIn("10 Years in Thailand", run["test_case_label"])
            self.assertEqual([item["name"] for item in run["source_documents"]], ["1. Ten Years in Thailand.md", "2. YouTube Package.md"])
            self.assertFalse(run["effective"]["web"])
            self.assertEqual(run["telemetry"]["eval_tokens_per_second"], 24.0)
            sent = post.call_args.args[1]
            self.assertEqual(sent["options"]["num_ctx"], 16384)
            self.assertFalse(sent["think"])
            self.assertEqual(sent["prompt"], f"TEST INSTRUCTIONS\n{documents[1]['content']}\n\nSOURCE MATERIAL\n{documents[0]['content']}")
            self.assertEqual(sent["prompt"].count(documents[0]["content"]), 1)
            self.assertEqual(sent["prompt"].count(documents[1]["content"]), 1)
            self.assertEqual(run["benchmark_instructions"], documents[1]["content"])
            self.assertEqual(run["source_material"], documents[0]["content"])
            self.assertNotIn("Work through the test question carefully", sent["prompt"])
            self.assertNotIn("documents", sent)
            stored = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(stored["run_id"], run["run_id"])
            self.assertEqual(stored["benchmark_generation_id"], server.model_lab_generation_id("test-01"))

    def test_history_marks_legacy_without_rewriting_records(self):
        documents = self.canonical_documents()
        canonical_metadata = [{key: item[key] for key in ("order", "name", "role", "sha256")} for item in documents]
        canonical_effective = {"context_tokens": 16384, "output_tokens": 4096, "temperature": 0.0, "top_p": 0.9, "seed": 42}
        current = {
            "run_id": "current-run",
            "test_case_id": "test-01",
            "benchmark_generation_id": server.model_lab_generation_id("test-01"),
            "source_documents": canonical_metadata,
            "effective": canonical_effective,
        }
        adapted = {**current, "run_id": "adapted-run", "effective": {**canonical_effective, "temperature": 0.2}}
        legacy = {**current, "run_id": "legacy-run", "benchmark_generation_id": "mlg-pre-freeze", "source_documents": []}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.jsonl"
            original = "\n".join(json.dumps(item) for item in (legacy, adapted, current)) + "\n"
            path.write_text(original, encoding="utf-8")
            with patch.object(server, "MODEL_LAB_RUNS_PATH", path):
                rows = server._model_lab_run_rows()
            self.assertEqual([row["benchmark_status"] for row in rows], ["CANONICAL", "CURRENT / ADAPTED", "LEGACY / PRE-FREEZE"])
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_reasoning_level_controls_ollama_thinking(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = {"available": True, "models": [{"name": "test-model", "size": 123}], "loaded": [], "loaded_details": []}
            response = {"response": "ok", "thinking": "trace", "done": True, "total_duration": 1_000_000}
            with patch.object(server, "MODEL_LAB_RUNS_PATH", Path(directory) / "runs.jsonl"), \
                 patch.object(server, "ollama_catalog", return_value=catalog), \
                 patch.object(server, "ollama_model_capability_details", return_value=QWEN_CAPABILITIES), \
                 patch.object(server, "_send_avatar_event_with_retry", return_value=True), \
                 patch.object(server, "ai_gpu_admission"), \
                 patch.object(server, "model_activity"), \
                 patch.object(server, "post_json", return_value=response) as post:
                result, status = server.run_model_lab({"model": "test-model", "profile": "long-document", "reasoning_level": "low", "prompt": "Say ok."})
            self.assertEqual(status, 200)
            self.assertEqual(result["run"]["effective"]["reasoning_level"], "low")
            self.assertEqual(result["run"]["effective"]["thinking"], "on")
            self.assertTrue(post.call_args.args[1]["think"])

    def test_standard_run_rejects_context_above_model_capability(self):
        caps = {**QWEN_CAPABILITIES, "context_max": 8192, "capabilities": ["completion"], "reasoning_modes": ["off"]}
        documents = self.canonical_documents()
        with patch.object(server, "ollama_catalog", return_value={"available": True, "models": [{"name": "test-model"}]}), \
             patch.object(server, "ollama_model_capability_details", return_value=caps):
            result, status = server.run_model_lab({"model": "test-model", "profile": "long-document", "test_case_id": "test-01", "documents": documents, "prefill": documents[0]["content"]})
        self.assertEqual(status, 409)
        self.assertFalse(result["ok"])
        self.assertEqual(result["classification"], "STANDARD INCOMPATIBLE")

    def test_standard_run_rejects_noncanonical_document_identity(self):
        documents = self.canonical_documents()
        documents[1] = {**documents[1], "role": "source material"}
        with patch.object(server, "ollama_catalog", return_value={"available": True, "models": [{"name": "test-model"}]}), \
             patch.object(server, "ollama_model_capability_details", return_value=QWEN_CAPABILITIES):
            result, status = server.run_model_lab({"model": "test-model", "profile": "long-document", "test_case_id": "test-01", "documents": documents, "prefill": documents[0]["content"]})
        self.assertEqual(status, 409)
        self.assertFalse(result["ok"])
        self.assertEqual(result["classification"], "STANDARD INCOMPATIBLE")

    def test_standard_run_rejects_changed_canonical_fixture(self):
        documents = self.canonical_documents()
        fixture_documents = self.canonical_documents()
        fixture_documents[0] = {**fixture_documents[0], "sha256": "0" * 64}
        with patch.object(server, "model_lab_fixture_documents", return_value=(fixture_documents, None)), \
             patch.object(server, "ollama_catalog") as catalog:
            result, status = server.run_model_lab({"model": "test-model", "profile": "long-document", "test_case_id": "test-01", "documents": documents, "prefill": documents[0]["content"]})
        self.assertEqual(status, 409)
        self.assertFalse(result["ok"])
        self.assertEqual(result["classification"], "STANDARD INCOMPATIBLE")
        catalog.assert_not_called()

    def test_run_rejects_invalid_profile_and_range(self):
        result, status = server.run_model_lab({"profile": "missing"})
        self.assertEqual(status, 400)
        self.assertFalse(result["ok"])
        result, status = server.run_model_lab({"profile": "long-document", "test_case_id": "test-01", "context_tokens": 100})
        self.assertEqual(status, 400)
        self.assertIn("context_tokens", result["message"])


if __name__ == "__main__":
    unittest.main()
