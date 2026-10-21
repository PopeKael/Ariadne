import json
import unittest
from unittest.mock import patch

from coding_agent import (WORKSPACE_ROOT, _parse_transcript, _shell_execution_receipts,
                          _qwen_process_environment, _resolve_qwen_node, _task_evidence_errors,
                          _within_root, create_context_model_profile, is_coding_intent)


class CodingAgentTests(unittest.TestCase):
    def test_ollama_openai_context_alias_is_derived_and_confirmed(self):
        import io
        responses = [
            {"parameters": "temperature 1"},
            {"models": []},
            {"status": "success"},
            {"parameters": "num_ctx 32768"},
        ]
        requests = []

        def fake_urlopen(request, timeout):
            requests.append(request)
            return io.BytesIO(json.dumps(responses.pop(0)).encode("utf-8"))

        with patch("coding_agent.urlopen", side_effect=fake_urlopen):
            alias, created = create_context_model_profile(
                "configured-coder-model", 32_768, "http://127.0.0.1:11434"
            )
        self.assertTrue(created)
        self.assertIn("-ctx32768", alias)
        create_body = json.loads(requests[2].data)
        self.assertEqual(create_body["from"], "configured-coder-model")
        self.assertEqual(create_body["parameters"]["num_ctx"], 32_768)
        self.assertEqual(requests[1].method, "GET")

    def test_qwen_runtime_resolves_pinned_node_and_adds_it_to_child_path(self):
        with patch("coding_agent.shutil.which", return_value=None):
            node = _resolve_qwen_node()
            environment = _qwen_process_environment()
        self.assertTrue(node.is_file())
        self.assertIn(str(node.parent), environment["PATH"].split(";"))

    def test_qwen_child_does_not_inherit_codex_session_or_cloud_credentials(self):
        with patch("coding_agent.shutil.which", return_value=None), patch.dict(
            "coding_agent.os.environ",
            {"CODEX_THREAD_ID": "parent-thread", "CODEX_APP_TOOLS_PIPE_PATH": "parent-pipe",
             "OPENAI_API_KEY": "parent-secret", "PATH": "C:\\Windows\\System32"},
            clear=True,
        ):
            environment = _qwen_process_environment()
        self.assertFalse(any(name.upper().startswith(("CODEX_", "OPENAI_")) for name in environment))

    def test_chat_intent_detects_code_work_without_tool_name(self):
        self.assertTrue(is_coding_intent("Please fix the broken Python program and run its tests."))
        self.assertTrue(is_coding_intent("Can you write a Python script to rename these files?"))
        self.assertTrue(is_coding_intent("Add validation to invoice.py."))
        self.assertFalse(is_coding_intent("What does a Python program do?"))
        self.assertFalse(is_coding_intent("Okay, Ariadne. They tell me you can write code now."))
        self.assertFalse(is_coding_intent("Can you write code now?"))
        self.assertFalse(is_coding_intent("So what did you write?"))

    def test_workspace_must_remain_inside_configured_lab_root(self):
        child = WORKSPACE_ROOT / "coding-agent-test"
        self.assertTrue(_within_root(child, WORKSPACE_ROOT))
        self.assertFalse(_within_root(WORKSPACE_ROOT.parent / "outside", WORKSPACE_ROOT))

    def test_qwen_json_transcript_returns_tool_and_test_evidence(self):
        transcript = [
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "I added the function and am checking it."},
                {"type": "tool_use", "id": "tool-1", "name": "run_shell_command",
                 "input": {"command": "python -m unittest"}},
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "tool-1", "is_error": False,
                 "content": "Ran 2 tests in 0.01s\nOK\nExit code: 0"},
            ]}},
            {"type": "result", "is_error": False, "result": "Both tests passed.",
             "usage": {"input_tokens": 120, "output_tokens": 25},
             "modelUsage": {"ariadne-qwen-code:9b-32k": {"inputTokens": 120, "outputTokens": 25}}},
        ]
        summary, calls, tests, errors, model_calls, usage, model_usage, final_error = _parse_transcript(
            json.dumps(transcript), "", 0
        )
        self.assertEqual(summary, "I added the function and am checking it.\nBoth tests passed.")
        self.assertEqual(model_calls, 1)
        self.assertEqual(calls[0]["name"], "run_shell_command")
        self.assertEqual(tests[0]["result"], "passed")
        self.assertEqual(errors, [])
        self.assertFalse(final_error)
        self.assertEqual(usage["input_tokens"], 120)
        self.assertIn("ariadne-qwen-code:9b-32k", model_usage)

    def test_shell_receipt_captures_program_output_and_exit_status(self):
        receipt = _shell_execution_receipts([{
            "name": "run_shell_command",
            "input": {"command": "python hello.py"},
            "result": "Hello from Ariadne\nExit code: 0",
            "is_error": False,
        }])[0]
        self.assertEqual(receipt["result"], "passed")
        self.assertEqual(receipt["exit_code"], 0)
        self.assertIn("Hello from Ariadne", receipt["output"])

    def test_change_and_verification_requests_fail_without_receipts(self):
        errors = _task_evidence_errors("Create hello.py and run it to verify output", [], [], [])
        self.assertEqual(len(errors), 2)
        errors = _task_evidence_errors(
            "Create hello.py and run it to verify output", ["hello.py"], [],
            [{"command": "python hello.py", "result": "passed", "exit_code": 0}],
        )
        self.assertEqual(errors, [])

    def test_cli_result_error_is_not_reported_as_success(self):
        transcript = [{"type": "result", "is_error": True, "error": "model stopped early"}]
        _, _, _, errors, _, _, _, final_error = _parse_transcript(json.dumps(transcript), "", 0)
        self.assertTrue(final_error)
        self.assertIn("model stopped early", errors[0])

    def test_non_json_cli_output_returns_consistent_failure_evidence(self):
        summary, calls, tests, errors, model_calls, usage, model_usage, final_error = _parse_transcript(
            "plain CLI failure", "diagnostic", 1
        )
        self.assertEqual(summary, "plain CLI failure")
        self.assertEqual((calls, tests, model_calls, usage, model_usage, final_error), ([], [], 0, {}, {}, False))
        self.assertIn("diagnostic", errors[0])

    def test_zero_exit_without_final_result_event_is_not_success_evidence(self):
        _, _, _, errors, _, _, _, final_error = _parse_transcript(
            json.dumps([{"type": "assistant", "message": {"content": [
                {"type": "text", "text": "I will inspect the workspace."}
            ]}}]), "", 0
        )
        self.assertTrue(final_error)
        self.assertIn("without emitting its final result event", errors[-1])


if __name__ == "__main__":
    unittest.main()
