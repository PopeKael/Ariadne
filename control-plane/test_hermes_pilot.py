from __future__ import annotations

import ctypes
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import hermes_pilot
import hermes_pilot_worker


class HermesAgentEngineTests(unittest.TestCase):
    def provider(self, **overrides):
        values = {
            "provider_id": "ollama-desktop", "enabled": True, "location": "desktop",
            "provider_type": "ollama", "model_id": "qwen3.5:9b-q4_K_M",
            "endpoint": "http://127.0.0.1:11434", "capabilities": ("chat", "structured_output"),
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def registry(self, provider=None):
        selected = provider or self.provider()
        return SimpleNamespace(route=lambda task: selected if task == "home_chat" else None)

    def test_unverified_engine_is_disabled_by_default_and_requires_explicit_enable(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(hermes_pilot.enabled())
        with patch.dict(os.environ, {"ARIADNE_HERMES_ENGINE": "hermes"}, clear=True):
            self.assertTrue(hermes_pilot.enabled())
        with patch.dict(os.environ, {"ARIADNE_HERMES_ENGINE": "legacy"}, clear=True):
            self.assertFalse(hermes_pilot.enabled())

    def test_message_conversion_keeps_ariadne_history_separate_from_identity(self):
        system, user, history = hermes_pilot._messages([
            {"role": "system", "content": "Ariadne identity and turn state"},
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "current"},
        ])
        self.assertEqual(system, "Ariadne identity and turn state")
        self.assertEqual(user, "current")
        self.assertEqual(history, [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "reply"}])

    def test_selected_registry_route_is_model_and_provider_agnostic(self):
        provider = hermes_pilot._provider(self.registry(), "qwen3.5:9b-q4_K_M")
        self.assertEqual(provider.provider_id, "ollama-desktop")
        cloud = self.provider(provider_id="configured-provider", provider_type="custom-provider",
                              location="cloud", model_id="configured-model",
                              endpoint="https://models.example/v1")
        self.assertEqual(hermes_pilot._provider(self.registry(cloud), "configured-model"), cloud)
        with self.assertRaisesRegex(RuntimeError, "configured Home chat route"):
            hermes_pilot._provider(self.registry(cloud), "different-model")

    def test_run_passes_chat_scope_to_ephemeral_worker_and_streams_events(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            repo = root / "hermes"
            repo.mkdir()
            (repo / "run_agent.py").write_text("# isolated source placeholder", encoding="utf-8")
            mcp_script = root / "ariadne_mcp.py"
            mcp_script.write_text("# existing MCP entrypoint placeholder", encoding="utf-8")
            agent_script = root / "ariadne_agent_tools.py"
            agent_script.write_text("# per-turn capability bridge", encoding="utf-8")
            captured = {}

            def fake_worker(command, **kwargs):
                captured.update(command=command, **kwargs, worker_home=os.environ.get("HERMES_HOME"))
                return {"ok": True, "answer": "Ariadne answer", "tool_receipts": [{"tool": "search_knowledge_chunks"}]}

            env_patch = {
                "ARIADNE_HERMES_AGENT_REPO": str(repo),
                "ARIADNE_HERMES_PYTHON": sys.executable,
                "ARIADNE_HERMES_ENGINE": "hermes",
                "ARIADNE_CONFIG_PATH": str(root / "configuration.json"),
                "ARIADNE_VAULT_ROOT": str(root / "vault"),
                "OPENAI_API_KEY": "must-not-inherit",
                "ARIADNE_MCP_BEARER_TOKEN": "must-not-inherit",
            }
            with patch.dict(os.environ, env_patch, clear=True), \
                 patch.object(hermes_pilot, "_stream_worker", side_effect=fake_worker):
                answer, trace = hermes_pilot.run(
                    [{"role": "system", "content": "Identity"}, {"role": "user", "content": "Question"}],
                    "chat-1", "turn-1", self.registry(), "qwen3.5:9b-q4_K_M",
                    context_tokens=32_768, output_tokens=2_048, api_key="ollama",
                    mcp_python=sys.executable, mcp_script=str(mcp_script),
                    allowed_tools=["coding_agent"], web_sources=[],
                    control_plane=str(root), vault_root=str(root),
                )
            self.assertEqual(answer, "Ariadne answer")
            self.assertEqual(trace["memory_owner"], "ariadne-chat-store")
            self.assertTrue(trace["hermes_memory_disabled"])
            self.assertTrue(trace["profile_disposed"])
            self.assertEqual(captured["payload"]["turn_id"], "turn-1")
            self.assertEqual(captured["payload"]["conversation_history"], [])
            self.assertEqual(captured["payload"]["model"]["registry_model_id"], "qwen3.5:9b-q4_K_M")
            self.assertEqual(captured["payload"]["capabilities"]["allowed_tools"], ["coding_agent"])
            self.assertEqual(captured["payload"]["mcp"]["agent_script"], str(root / "ariadne_agent_tools.py"))
            self.assertEqual(captured["env"]["ARIADNE_CONFIG_PATH"], str(root / "configuration.json"))
            self.assertEqual(captured["env"]["ARIADNE_VAULT_ROOT"], str(root / "vault"))
            self.assertNotIn("OPENAI_API_KEY", captured["env"])
            self.assertNotIn("ARIADNE_MCP_BEARER_TOKEN", captured["env"])
            self.assertFalse(Path(captured["env"]["HERMES_HOME"]).exists())

    def test_worker_lifecycle_receipt_correlates_pid_exit_and_turn(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            child = root / "worker.py"
            child.write_text(
                "import json, sys\n"
                "sys.stdin.read()\n"
                "print(json.dumps({'type':'result','ok':True,'answer':'ok'}))\n",
                encoding="utf-8",
            )
            result = hermes_pilot._stream_worker(
                [sys.executable, str(child)],
                payload={"agent_run_id": "engine-run-1", "turn_id": "turn-1"},
                env=os.environ.copy(), cwd=root, on_delta=None, on_tool_event=None,
            )
            lifecycle = result["_hermes_worker_process"]
            self.assertEqual(lifecycle["run_id"], "engine-run-1")
            self.assertEqual(lifecycle["turn_id"], "turn-1")
            self.assertEqual(lifecycle["parent_pid"], os.getpid())
            self.assertGreater(lifecycle["pid"], 0)
            self.assertEqual(lifecycle["exit_code"], 0)
            self.assertTrue(lifecycle["started_at"].endswith("Z"))
            self.assertTrue(lifecycle["exited_at"].endswith("Z"))

    def test_failed_worker_preserves_completed_tool_receipts_in_exit_diagnostics(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            child = root / "worker.py"
            child.write_text(
                "import json, sys\n"
                "sys.stdin.read()\n"
                "print(json.dumps({'type':'result','ok':False,'error':'final validation failed',"
                "'tool_receipts':[{'tool':'run_coding_task','status':'completed','execution':{'status':'FAILED'}}]}))\n"
                "raise SystemExit(1)\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "final validation failed") as raised:
                hermes_pilot._stream_worker(
                    [sys.executable, str(child)],
                    payload={"agent_run_id": "engine-run-2", "turn_id": "turn-2"},
                    env=os.environ.copy(), cwd=root, on_delta=None, on_tool_event=None,
                )
            lifecycle = raised.exception.hermes_worker_process
            self.assertEqual(lifecycle["run_id"], "engine-run-2")
            self.assertEqual(lifecycle["tool_receipts"][0]["execution"]["status"], "FAILED")

    def test_timed_out_worker_is_terminated_and_cleanup_is_recorded(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            child_pid_path = root / "child.pid"
            child = root / "worker.py"
            child.write_text(
                "import subprocess, sys, time\n"
                "sys.stdin.read()\n"
                "grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
                "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, "
                "creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))\n"
                f"open({str(child_pid_path)!r}, 'w').write(str(grandchild.pid))\n"
                "time.sleep(60)\n",
                encoding="utf-8",
            )
            with patch.object(hermes_pilot, "ENGINE_TIMEOUT_SECONDS", 0.25):
                with self.assertRaisesRegex(TimeoutError, "time limit") as raised:
                    hermes_pilot._stream_worker(
                        [sys.executable, str(child)],
                        payload={"agent_run_id": "engine-timeout", "turn_id": "turn-timeout"},
                        env=os.environ.copy(), cwd=root, on_delta=None, on_tool_event=None,
                    )
            lifecycle = raised.exception.hermes_worker_process
            self.assertNotEqual(lifecycle["exit_code"], 0)
            self.assertEqual(lifecycle["process_tree_owner"], "windows-job-object")
            cleanup = lifecycle["tree_cleanup"]
            self.assertEqual(cleanup["method"], "windows-job-object-terminate")
            self.assertTrue(cleanup["worker_exit_verified"], cleanup)
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                except OSError:
                    break
                time.sleep(0.05)
            else:
                self.fail(f"Hermes worker child PID {child_pid} survived Job Object cleanup")

    def test_worker_exit_reaps_a_child_holding_the_result_pipe(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            child_pid_path = root / "child.pid"
            child = root / "worker.py"
            child.write_text(
                "import json, subprocess, sys\n"
                "sys.stdin.read()\n"
                "grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
                "stdout=sys.stdout, stderr=sys.stderr, "
                "creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))\n"
                f"open({str(child_pid_path)!r}, 'w').write(str(grandchild.pid))\n"
                "print(json.dumps({'type': 'result', 'ok': True, 'answer': 'accepted'}), flush=True)\n",
                encoding="utf-8",
            )
            result = hermes_pilot._stream_worker(
                [sys.executable, str(child)],
                payload={"agent_run_id": "engine-orphan", "turn_id": "turn-orphan"},
                env=os.environ.copy(), cwd=root, on_delta=None, on_tool_event=None,
            )
            self.assertEqual(result["answer"], "accepted")
            lifecycle = result["_hermes_worker_process"]
            self.assertEqual(lifecycle["exit_code"], 0)
            self.assertEqual(lifecycle["process_tree_owner"], "windows-job-object")
            if "orphan_child_cleanup" in lifecycle:
                self.assertEqual(
                    lifecycle["orphan_child_cleanup"]["method"],
                    "windows-job-object-orphan-cleanup",
                )
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))
            if os.name == "nt":
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
                kernel32.OpenProcess.restype = ctypes.c_void_p
                kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
                kernel32.GetExitCodeProcess.restype = ctypes.c_bool
                process = kernel32.OpenProcess(0x1000, False, child_pid)
                if not process:
                    # A process that has already exited may no longer have a
                    # queryable PID. That is successful cleanup; any other
                    # OpenProcess error still needs investigation.
                    self.assertEqual(
                        ctypes.get_last_error(), 87,
                        f"Could not inspect Hermes worker child PID {child_pid}",
                    )
                else:
                    exit_code = ctypes.c_uint32()
                    checked = kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code))
                    kernel32.CloseHandle(process)
                    self.assertTrue(checked)
                    self.assertNotEqual(exit_code.value, 259, f"Hermes worker child PID {child_pid} survived Job Object cleanup")
            else:
                with self.assertRaises(OSError):
                    os.kill(child_pid, 0)

    def _worker_request(self, root: Path) -> dict:
        repo = root / "hermes"
        repo.mkdir(exist_ok=True)
        (repo / "run_agent.py").write_text("# source", encoding="utf-8")
        mcp_script = root / "ariadne_mcp.py"
        mcp_script.write_text("# MCP", encoding="utf-8")
        agent_script = root / "ariadne_agent_tools.py"
        agent_script.write_text("# Toolshed MCP", encoding="utf-8")
        return {
            "repo": str(repo), "model": {
                "provider_id": "ollama-desktop", "provider_type": "ollama",
                "model_id": "qwen3.5:9b-q4_K_M", "api_key": "ollama",
                "endpoint": "http://127.0.0.1:11434", "context_tokens": 65_536,
                "output_tokens": 4_096,
            },
            "chat_id": "chat-1", "turn_id": "turn-1", "system_message": "Ariadne identity",
            "user_message": "Find this in my Vault", "conversation_history": [], "max_iterations": 8,
            "mcp": {"python": sys.executable, "script": str(mcp_script),
                    "agent_script": str(agent_script),
                    "control_plane": str(root), "vault_root": str(root)},
            "capabilities": {"allowed_tools": [], "web_sources": []},
        }

    def test_worker_accepts_registry_selected_cloud_route(self):
        with tempfile.TemporaryDirectory() as root:
            request = self._worker_request(Path(root))
            request["model"]["provider_id"] = "configured-provider"
            request["model"]["provider_type"] = "custom-provider"
            request["model"]["endpoint"] = "https://models.example/v1"
            request["model"]["api_key"] = "configured-secret"
            with patch.object(hermes_pilot_worker.json, "load", return_value=request), \
                 patch.object(hermes_pilot_worker.sys, "stdin", SimpleNamespace()):
                self.assertEqual(hermes_pilot_worker._read_request(), request)

    def test_worker_records_only_allowlisted_vault_tool_receipts(self):
        receipts = hermes_pilot_worker._extract_tool_receipts([
            {"tool_calls": [
                {"function": {"name": "mcp__ariadne_knowledge_vault__search_knowledge_chunks"}},
                {"function": {"name": "mcp__filesystem__read_file"}},
            ]}
        ])
        self.assertEqual(receipts, [{"tool": "search_knowledge_chunks"}])

    def test_coding_capability_uses_hermes_native_scripted_tool_round(self):
        prelude = hermes_pilot_worker._coding_task_prelude("Create hello.py and run it")
        content, name, arguments = next(prelude)
        self.assertEqual(name, "mcp__ariadne_toolshed__run_coding_task")
        self.assertEqual(arguments, {"task": "Create hello.py and run it"})
        self.assertIn("approved coding capability", content)
        with self.assertRaises(StopIteration):
            prelude.send("tool result")

    def test_worker_profile_uses_registry_model_and_only_per_turn_tools(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"HERMES_HOME": root}, clear=True):
            mcp = {"python": sys.executable, "script": "D:\\vault\\00_System\\ariadne_mcp.py",
                   "agent_script": "D:\\Downloads\\Ariadne\\control-plane\\ariadne_agent_tools.py",
                   "control_plane": "D:\\Downloads\\Ariadne\\control-plane",
                   "vault_root": "D:\\Downloads\\KnowledgeVault"}
            model = {"provider_type": "ollama", "model_id": "configured-local-model",
                     "registry_model_id": "configured-registry-model",
                     "api_key": "ollama", "endpoint": "http://127.0.0.1:11434",
                     "context_tokens": 32_768, "output_tokens": 2_048}
            hermes_pilot_worker._configure_profile(model, mcp)
            config = json.loads((Path(root) / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(config["model"]["provider"], "custom")
        self.assertEqual(config["model"]["default"], "configured-local-model")
        self.assertEqual(config["model"]["context_length"], 32_768)
        server = config["mcp_servers"]["ariadne-knowledge-vault"]
        self.assertEqual(server["args"], [mcp["script"]])
        self.assertEqual(set(server["tools"]["include"]), hermes_pilot_worker.TOOL_NAMES)
        self.assertFalse(server["tools"]["prompts"])
        self.assertFalse(server["tools"]["resources"])
        self.assertFalse(server["elicitation"]["enabled"])
        self.assertFalse(config["memory"]["enabled"])
        self.assertEqual(config["tools"]["tool_search"]["enabled"], "off")
        self.assertNotIn("headers", server)
        self.assertNotIn("ariadne-toolshed", config["mcp_servers"])
        with patch.dict(os.environ, {
            "HERMES_HOME": root,
            "ARIADNE_WORKER_CAPABILITIES": json.dumps({"allowed_tools": ["coding_agent", "web_research"]}),
            "ARIADNE_TURN_CHAT_ID": "chat-1", "ARIADNE_TURN_ID": "turn-1",
            "ARIADNE_CONFIG_PATH": str(Path(root) / "configuration.json"),
            "ARIADNE_QWEN_DEBUG": "1",
            "ARIADNE_WEB_SOURCES_PATH": str(Path(root) / "sources.json"),
        }, clear=True):
            hermes_pilot_worker._configure_profile(model, mcp)
            config = json.loads((Path(root) / "config.yaml").read_text(encoding="utf-8"))
        toolshed = config["mcp_servers"]["ariadne-toolshed"]
        self.assertEqual(set(toolshed["tools"]["include"]), {"run_coding_task", "get_web_research_evidence"})
        self.assertEqual(toolshed["env"]["ARIADNE_VAULT_ROOT"], mcp["vault_root"])
        self.assertEqual(toolshed["env"]["ARIADNE_HOME_MODEL_ID"], "configured-registry-model")
        self.assertEqual(toolshed["env"]["ARIADNE_CONFIG_PATH"], str(Path(root) / "configuration.json"))
        self.assertEqual(toolshed["env"]["ARIADNE_QWEN_DEBUG"], "1")
        self.assertFalse(toolshed["tools"]["prompts"])
        self.assertFalse(toolshed["tools"]["resources"])

    def test_worker_stream_callbacks_emit_only_text_and_approved_tool_names(self):
        stream = io.StringIO()
        with patch.object(hermes_pilot_worker, "JSONL_STREAM", stream):
            hermes_pilot_worker._delta_callback("hello")
            hermes_pilot_worker._tool_callback("started")(
                "call-1", "mcp__ariadne_knowledge_vault__search_knowledge_chunks", {},
            )
            hermes_pilot_worker._tool_callback("started")("call-2", "mcp__filesystem__write_file", {})
        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual(events[0], {"type": "delta", "text": "hello"})
        self.assertEqual(events[1]["type"], "tool")
        self.assertEqual(len(events), 2)

    def test_coding_receipt_unwraps_hermes_mcp_json_text_envelope(self):
        stream = io.StringIO()
        execution = {"status": "SUCCESS", "run_id": "run-1", "changed_files": ["hello.py"]}
        adapter_result = json.dumps({"result": json.dumps({
            "ok": True, "status": "SUCCESS", "run_id": "run-1",
            "changed_files": ["hello.py"], "executions": [{"result": "passed", "exit_code": 0}],
        })})
        with patch.object(hermes_pilot_worker, "JSONL_STREAM", stream):
            hermes_pilot_worker._tool_callback("completed")(
                "call-1", "mcp__ariadne_toolshed__run_coding_task", {"task": "Create and run"}, adapter_result,
            )
        event = json.loads(stream.getvalue())
        receipt = hermes_pilot_worker._TOOL_RECEIPTS[-1]
        self.assertEqual(event["status"], "completed")
        self.assertEqual(receipt["execution"]["status"], "SUCCESS")
        self.assertEqual(receipt["execution"]["changed_files"], ["hello.py"])
        self.assertNotEqual(execution["run_id"], "")

    def test_toolshed_mcp_hyphen_is_normalized_in_hermes_tool_namespace(self):
        self.assertEqual(
            hermes_pilot_worker.AGENT_TOOL_PREFIX,
            "mcp__ariadne_toolshed__",
        )


if __name__ == "__main__":
    unittest.main()
