import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from semantic_planner import fallback_plan, plan_request, planner_schema, validate_plan  # noqa: E402


class SemanticPlannerTests(unittest.TestCase):
    def context(self, attachments=None):
        return {
            "current_local_date": "2026-08-23",
            "current_local_time": "14:20:00",
            "timezone": "Asia/Bangkok",
            "available_tools": [{
                "tool_id": "document-analysis",
                "enabled": True,
                "description": "Temporary attachment analysis",
            }],
            "attachments": attachments or [],
            "active_knowledge_source": "auto",
            "selected_tool_ids": [],
            "model_roles": {
                "planner_model": "qwen3:0.6b",
                "conversation_model": "qwen3.5:9b-q4_K_M",
                "knowledge_model": "gpt-oss:20b",
            },
            "conversation_state": {"recent_messages": [], "message_count": 0},
        }

    def valid_plan(self, **overrides):
        value = {
            "intent": "document_summary",
            "primary_source": "attachment",
            "tools": ["document-analysis"],
            "use_vault": False,
            "needs_current_information": False,
            "use_heavy_model": False,
            "tasks": ["summarise the attached document"],
            "confidence": 0.94,
        }
        value.update(overrides)
        return value

    def test_schema_is_strict_and_tool_bound(self):
        schema = planner_schema(["document-analysis"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["tools"]["items"]["enum"], ["document-analysis"])
        self.assertEqual(validate_plan(self.valid_plan(), ["document-analysis"], has_attachments=True)["confidence"], 0.94)

    def test_unavailable_tool_is_rejected_before_execution(self):
        with self.assertRaisesRegex(ValueError, "unavailable tool"):
            validate_plan(self.valid_plan(tools=["web-research"]), ["document-analysis"], has_attachments=True)

    def test_attachment_primary_plan_uses_structured_output_and_residency(self):
        captured = {}
        response = {
            "message": {"content": json.dumps(self.valid_plan())},
            "load_duration": 8_000_000,
            "total_duration": 42_000_000,
            "prompt_eval_count": 210,
            "eval_count": 38,
        }

        def request(url, body, timeout):
            captured.update({"url": url, "body": body, "timeout": timeout})
            return response

        status_calls = []

        def status(url, timeout):
            status_calls.append(url)
            return {"models": []} if len(status_calls) == 1 else {"models": [{"name": "qwen3:0.6b"}]}

        result = plan_request(
            "Let's chat about this.",
            self.context([{"filename": "article.md", "title": "Article", "size_bytes": 100}]),
            endpoint="http://127.0.0.1:11434",
            model="qwen3:0.6b",
            request_fn=request,
            status_fn=status,
        )
        self.assertEqual(captured["body"]["format"], planner_schema(["document-analysis"]))
        self.assertEqual(captured["body"]["keep_alive"], -1)
        self.assertFalse(captured["body"]["think"])
        self.assertEqual(result["plan"]["primary_source"], "attachment")
        self.assertTrue(result["telemetry"]["model_load_occurred"])
        self.assertTrue(result["telemetry"]["residency_verified"])
        self.assertEqual(result["telemetry"]["prompt_tokens"], 210)
        self.assertEqual(result["telemetry"]["output_tokens"], 38)

    def test_no_attachment_schema_exposes_no_applicable_tools(self):
        captured = {}
        plan = self.valid_plan(
            primary_source="user_message",
            tools=[],
            tasks=[],
            confidence=0.8,
        )

        def request(url, body, timeout):
            captured["schema"] = body["format"]
            return {"message": {"content": json.dumps(plan)}}

        result = plan_request(
            "What is the capital of Thailand?",
            self.context(),
            endpoint="http://127.0.0.1:11434",
            request_fn=request,
            status_fn=lambda url, timeout: {},
        )
        self.assertEqual(captured["schema"]["properties"]["tools"]["items"]["enum"], [])
        self.assertEqual(result["plan"]["tools"], [])
    def test_current_request_can_be_planned_without_inventing_external_tool(self):
        plan = self.valid_plan(
            intent="current_status_check",
            primary_source="user_message",
            tools=[],
            needs_current_information=True,
            tasks=["check whether the supplied information is still current"],
        )
        result = plan_request(
            "Is this information still current?",
            self.context(),
            endpoint="http://127.0.0.1:11434",
            request_fn=lambda url, body, timeout: {"message": {"content": json.dumps(plan)}},
            status_fn=lambda url, timeout: {},
        )
        self.assertTrue(result["plan"]["needs_current_information"])
        self.assertEqual(result["plan"]["tools"], [])

    def test_fallback_preserves_legacy_vault_route(self):
        plan = fallback_plan(
            has_attachments=False,
            legacy_use_vault=True,
            vault_mode="auto",
            selected_tool_ids=set(),
            available_tool_ids=["document-analysis"],
            reason="planner unavailable",
        )
        self.assertTrue(plan["use_vault"])
        self.assertEqual(plan["primary_source"], "vault")
        self.assertEqual(plan["tools"], [])


if __name__ == "__main__":
    unittest.main()