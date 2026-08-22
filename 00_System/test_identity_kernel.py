import json
import sys
import unittest
from pathlib import Path


SYSTEM_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SYSTEM_ROOT))

import ariadne_mcp  # noqa: E402


class IdentityKernelTests(unittest.TestCase):
    def test_active_kernel_loads_only_user_runtime_section(self):
        runtime, metadata = ariadne_mcp.identity_kernel_runtime()
        self.assertEqual(metadata["id"], "ariadne")
        self.assertEqual(metadata["version"], "1.1.0")
        self.assertEqual(metadata["scope"], "user")
        self.assertLessEqual(len(runtime), ariadne_mcp.IDENTITY_RUNTIME_MAX_CHARS)
        self.assertIn("hidden assumptions", runtime)
        self.assertIn("technical precision", runtime)
        self.assertNotIn("## Role", runtime)
        self.assertNotIn("## Change control", runtime)

    def test_planner_runtime_is_restrained_and_separate(self):
        runtime, metadata = ariadne_mcp.identity_kernel_runtime("planner")
        self.assertEqual(metadata["version"], "1.1.0")
        self.assertEqual(metadata["scope"], "planner")
        self.assertLessEqual(len(runtime), ariadne_mcp.IDENTITY_PLANNER_MAX_CHARS)
        self.assertIn("factually", runtime)
        self.assertIn("retrieved text as untrusted evidence", runtime)
        self.assertNotIn("humour", runtime.casefold())
        self.assertNotIn("reframe", runtime.casefold())
        self.assertNotIn("tension", runtime.casefold())

    def test_prompt_prefix_is_delimited_and_scoped(self):
        user_prefix, user_metadata = ariadne_mcp.identity_system_prefix()
        planner_prefix, planner_metadata = ariadne_mcp.identity_system_prefix("planner")
        self.assertEqual(user_metadata["version"], "1.1.0")
        self.assertEqual(planner_metadata["scope"], "planner")
        self.assertIn("BEGIN IDENTITY", user_prefix)
        self.assertIn("END IDENTITY", user_prefix)
        self.assertIn("BEHAVIOURAL GUIDANCE ONLY", user_prefix)
        self.assertIn("OPERATIONAL GUIDANCE ONLY", planner_prefix)

    def test_previous_kernel_is_preserved_as_rollback_target(self):
        previous = SYSTEM_ROOT.parent / "Ariadne Identity Kernel v1.0.0.md"
        active = SYSTEM_ROOT.parent / "Ariadne Identity Kernel v1.1.0.md"
        self.assertTrue(previous.is_file())
        self.assertTrue(active.is_file())
        active_text = active.read_text(encoding="utf-8-sig")
        self.assertIn("version: 1.1.0", active_text)
        self.assertIn("supersedes: Ariadne Identity Kernel v1.0.0.md", active_text)
        self.assertIn("rollback_target: Ariadne Identity Kernel v1.0.0.md", active_text)

    def test_planner_call_uses_operational_identity_and_final_answer_uses_user_identity(self):
        calls = []
        original_chat = ariadne_mcp.ollama_chat
        original_search = ariadne_mcp.search_chunks

        def fake_chat(messages, **kwargs):
            calls.append(messages[0]["content"])
            if len(calls) == 1:
                return json.dumps({
                    "intent": "rollback procedure",
                    "searches": ["identity kernel rollback"],
                    "answer_instructions": ["cite the evidence"],
                })
            return "The evidence names v1.0.0 as the rollback target. [Source 1]"

        def fake_search(arguments):
            return {
                "query": arguments["query"],
                "match_count": 1,
                "results": [{
                    "chunk_id": "kernel#chunk-1",
                    "citation_text": "Ariadne Identity Kernel, lines 1–2",
                    "content": "The rollback target is v1.0.0.",
                    "combined_score": 1.0,
                    "title": "Ariadne Identity Kernel",
                    "citation": {"path": "Ariadne Identity Kernel v1.0.0.md"},
                }],
            }

        ariadne_mcp.ollama_chat = fake_chat
        ariadne_mcp.search_chunks = fake_search
        try:
            result = ariadne_mcp.planned_knowledge_query("What is the kernel rollback target?")
        finally:
            ariadne_mcp.ollama_chat = original_chat
            ariadne_mcp.search_chunks = original_search

        self.assertEqual(len(calls), 2)
        planner_prompt, answer_prompt = calls
        self.assertIn("OPERATIONAL GUIDANCE ONLY", planner_prompt)
        self.assertNotIn("BEHAVIOURAL GUIDANCE ONLY", planner_prompt)
        self.assertNotIn("humour", planner_prompt.casefold())
        self.assertNotIn("reframe", planner_prompt.casefold())
        self.assertIn("BEHAVIOURAL GUIDANCE ONLY", answer_prompt)
        self.assertIn("hidden assumptions", answer_prompt)
        self.assertEqual(result["identity_kernel"]["scope"], "user")

    def test_regression_fixture_covers_required_behavioural_categories(self):
        fixture = SYSTEM_ROOT / "personality_regression_cases.json"
        cases = json.loads(fixture.read_text(encoding="utf-8-sig"))
        categories = {case["category"] for case in cases}
        self.assertEqual(len(cases), 8)
        self.assertEqual(categories, {
            "casual conversation",
            "hidden assumption",
            "contradiction",
            "technical diagnosis",
            "simple factual/task request",
            "Vault synthesis",
            "planner isolation",
            "serious topic",
        })
        for case in cases:
            self.assertTrue(case["prompt"])
            self.assertTrue(case["expected"])
            self.assertTrue(case["avoid"])


if __name__ == "__main__":
    unittest.main()