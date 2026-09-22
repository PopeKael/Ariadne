import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
RABBIT_MODULE_PATH = ROOT / "plugins" / "rabbit-hole" / "rabbit_hole.py"
spec = importlib.util.spec_from_file_location("rabbit_hole_test_adapter", RABBIT_MODULE_PATH)
rabbit_hole = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(rabbit_hole)
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


def repository(name, *, language="Python", description="An experimental visual AI playground.", days=2, topics=None, stars=2):
    stamp = (rabbit_hole.datetime.now(rabbit_hole.timezone.utc) - rabbit_hole.timedelta(days=days)).isoformat().replace("+00:00", "Z")
    return {
        "full_name": f"example/{name}",
        "name": name,
        "description": description,
        "html_url": f"https://github.com/example/{name}",
        "updated_at": stamp,
        "pushed_at": stamp,
        "open_issues_count": 3,
        "language": language,
        "topics": topics or ["experimental", "visual"],
        "stargazers_count": stars,
        "fork": False,
        "archived": False,
    }


class RabbitHoleAdapterTests(unittest.TestCase):
    def test_explore_returns_small_read_only_candidates_and_platform_notes(self):
        calls = []
        items = [
            repository("cuda-camera", language="C++", description="Real-time computer vision camera experiment", topics=["computer-vision", "cuda"]),
            repository("browser-simulation", language="TypeScript", description="Interactive browser simulation playground", topics=["simulation", "browser"]),
            repository("offline-agent", language="Python", description="A local AI agent automation toy", topics=["agent", "local-ai"]),
            repository("data-oddity", language="Rust", description="Unusual finance data visualizer", topics=["finance", "visualization"]),
            repository("media-lab", language="JavaScript", description="Generative audio and video experiment", topics=["generative-art", "audio"]),
            repository("too-many", language="Python", description="Another ordinary project", topics=["tool"]),
        ]

        def fake_urlopen(request, timeout):
            calls.append((request.full_url, dict(request.header_items()), timeout))
            return FakeResponse({"items": items})

        with patch.object(rabbit_hole, "urlopen", side_effect=fake_urlopen):
            result = rabbit_hole.explore()

        self.assertTrue(result["ok"])
        self.assertGreaterEqual(len(result["results"]), 3)
        self.assertLessEqual(len(result["results"]), 5)
        self.assertEqual(len(calls), 5)
        self.assertTrue(all("sort=updated" in call[0] for call in calls))
        self.assertTrue(all("github_url" in item and item["github_url"].startswith("https://github.com/") for item in result["results"]))
        self.assertIn("GPU-heavy", next(item["platform_concerns"] for item in result["results"] if item["repository_name"].endswith("cuda-camera")))
        self.assertTrue(all("why_interesting" in item and "maintenance_signal" in item for item in result["results"]))

    def test_optional_token_is_header_only_and_not_result_data(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["headers"] = dict(request.header_items())
            return FakeResponse({"items": [repository("token-check")]})

        with patch.dict("os.environ", {"ARIADNE_GITHUB_TOKEN": "secret-token"}), patch.object(rabbit_hole, "urlopen", side_effect=fake_urlopen):
            result = rabbit_hole.explore()
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret-token")
        self.assertNotIn("secret-token", json.dumps(result))


class RabbitHoleServerTests(unittest.TestCase):
    def test_manifest_page_and_read_only_in_process_job(self):
        plugin = next(item for item in server.PLUGIN_REGISTRY.payload()["plugins"] if item.get("plugin_id") == "rabbit-hole")
        self.assertEqual(plugin["status"], "healthy")
        self.assertTrue(plugin["has_ui"])
        self.assertFalse(plugin["action_metadata"]["explore"]["mutating"])

        session_id = uuid.uuid4().hex
        result_path = Path(tempfile.mkdtemp()) / "rabbit-hole-result.json"
        with server.SESSION_LOCK:
            server.SESSIONS[session_id] = {"last_seen": time.monotonic(), "jobs": set(), "used_ollama": False, "chat_id": "chat", "processing": False}

        def fake_adapter(_config, report):
            report("searching", "Searching test GitHub data…", 40)
            report("completed", "Found 3 test candidates.", 100)
            return {"ok": True, "completed_at": "2026-09-20T00:00:00+00:00", "results": [{"repository_name": "example/test", "github_url": "https://github.com/example/test"}], "warnings": [], "message": "Found 1 test candidate."}

        try:
            with patch.object(server, "load_plugin_callable", return_value=fake_adapter), patch.object(server, "RABBIT_HOLE_RESULT_PATH", result_path), patch.object(server, "_start_process", side_effect=AssertionError("Rabbit Hole must not start a process")):
                job_id = server.run_plugin_action(session_id, "rabbit-hole", "explore")
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    payload = server.job_payload(job_id)
                    if payload and payload.get("state") in {"complete", "error"}:
                        break
                    time.sleep(0.02)
                payload = server.job_payload(job_id)
                self.assertEqual(payload["state"], "complete")
                self.assertEqual(payload["results"][0]["repository_name"], "example/test")
                self.assertTrue(result_path.is_file())
                self.assertTrue(server.rabbit_hole_result_payload()["has_result"])
        finally:
            server._close_session(session_id)

    def test_http_page_start_poll_and_last_result_surface(self):
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://localhost:{httpd.server_port}"
        session_id = uuid.uuid4().hex
        result_path = Path(tempfile.mkdtemp()) / "rabbit-hole-result.json"
        with server.SESSION_LOCK:
            server.SESSIONS[session_id] = {"last_seen": time.monotonic(), "jobs": set(), "used_ollama": False, "chat_id": "chat", "processing": False}

        def request(path, *, method="GET", payload=None):
            data = json.dumps(payload).encode("utf-8") if payload is not None else None
            request_obj = urllib.request.Request(base + path, data=data, method=method)
            if data is not None:
                request_obj.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(request_obj, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8")) if "json" in response.headers.get("Content-Type", "") else response.read().decode("utf-8")

        def fake_adapter(_config, report):
            report("searching", "Searching test GitHub data…", 40)
            return {"ok": True, "completed_at": "2026-09-20T00:00:00+00:00", "results": [{"repository_name": "example/http", "github_url": "https://github.com/example/http"}], "warnings": [], "message": "Found 1 test candidate."}

        try:
            with patch.object(server, "_expire_sessions"), patch.object(server, "load_plugin_callable", return_value=fake_adapter), patch.object(server, "RABBIT_HOLE_RESULT_PATH", result_path):
                status, html = request("/rabbit-hole")
                self.assertEqual(status, 200)
                self.assertIn("Explore GitHub", html)
                self.assertEqual(request("/rabbit-hole.js")[0], 200)
                self.assertEqual(request("/rabbit-hole.css")[0], 200)
                _, started = request("/api/plugins/rabbit-hole/run", method="POST", payload={"session_id": session_id, "action": "explore"})
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    _, job = request(f"/api/vault/jobs/{started['job_id']}?session_id={session_id}")
                    if job.get("state") in {"complete", "error"}:
                        break
                    time.sleep(0.02)
                self.assertEqual(job["state"], "complete")
                _, result = request("/api/rabbit-hole/result")
                self.assertTrue(result["has_result"])
                self.assertEqual(result["result"]["results"][0]["repository_name"], "example/http")
        finally:
            server._close_session(session_id)
            httpd.shutdown()
            httpd.server_close()

    def test_empty_exploration_does_not_replace_last_completed_result(self):
        session_id = uuid.uuid4().hex
        result_path = Path(tempfile.mkdtemp()) / "rabbit-hole-result.json"
        previous = {"ok": True, "completed_at": "2026-09-19T00:00:00+00:00", "results": [{"repository_name": "example/previous"}]}
        result_path.write_text(json.dumps(previous), encoding="utf-8")
        with server.SESSION_LOCK:
            server.SESSIONS[session_id] = {"last_seen": time.monotonic(), "jobs": set(), "used_ollama": False, "chat_id": "chat", "processing": False}

        def empty_adapter(_config, _report):
            return {"ok": False, "completed_at": "2026-09-20T00:00:00+00:00", "results": [], "warnings": ["rate limited"], "message": "No usable candidates."}

        try:
            with patch.object(server, "load_plugin_callable", return_value=empty_adapter), patch.object(server, "RABBIT_HOLE_RESULT_PATH", result_path):
                job_id = server.run_plugin_action(session_id, "rabbit-hole", "explore")
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    payload = server.job_payload(job_id)
                    if payload and payload.get("state") in {"complete", "error"}:
                        break
                    time.sleep(0.02)
                self.assertEqual(payload["state"], "error")
                self.assertEqual(json.loads(result_path.read_text(encoding="utf-8"))["results"][0]["repository_name"], "example/previous")
        finally:
            server._close_session(session_id)


if __name__ == "__main__":
    unittest.main()
