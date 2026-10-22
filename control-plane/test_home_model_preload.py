from __future__ import annotations

import sys
import os
import threading
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


class HomeModelPreloadTests(unittest.TestCase):
    def setUp(self):
        self.original_model = server.HOME_CHAT_MODEL
        self.original_owner = server.GPU_OWNER
        self.original_transition = server.GPU_TRANSITION_STATE
        server.HOME_CHAT_MODEL = "home-model"
        server.GPU_OWNER = "AI"
        server.GPU_TRANSITION_STATE = "IDLE"
        self.gpu_lease_patch = patch.object(server, "host_gpu_lease", return_value=nullcontext("test-lease"))
        self.gpu_lease_patch.start()
        with server.HOME_MODEL_PRELOAD_LOCK:
            thread = server.HOME_MODEL_PRELOAD_THREAD
        if thread is not None and thread.is_alive():
            thread.join(2)
        server.HOME_MODEL_PRELOAD_THREAD = None

    def tearDown(self):
        with server.HOME_MODEL_PRELOAD_LOCK:
            thread = server.HOME_MODEL_PRELOAD_THREAD
        if thread is not None and thread.is_alive():
            thread.join(2)
        server.HOME_MODEL_PRELOAD_THREAD = None
        server.HOME_CHAT_MODEL = self.original_model
        server.GPU_OWNER = self.original_owner
        server.GPU_TRANSITION_STATE = self.original_transition
        self.gpu_lease_patch.stop()

    def test_core_startup_schedules_preload_before_serving_requests(self):
        events = []

        class FakeHTTPServer:
            def __init__(self, *_args):
                events.append("bound")

            def serve_forever(self):
                events.append("serve_forever")
                raise KeyboardInterrupt

            def server_close(self):
                events.append("closed")

        with patch.dict(server.os.environ, {"ARIADNE_ALLOW_UNSUPERVISED_CORE": "1"}), \
             patch.object(server, "start_owned_ollama", return_value={"state": "online"}), \
             patch.object(server, "server_context", return_value=None), \
             patch.object(server, "load_gateway", return_value=None), \
             patch.object(server.NEWS_BRIEFING_CACHE, "start_background_sync"), \
             patch.object(server, "start_news_image_sync"), \
             patch.object(server, "expire_home_chats"), \
             patch.object(server, "_wait_for_home_model_preload", return_value={"ok": True, "state": "resident"}), \
             patch.object(server, "HOME_MODEL_PRELOAD_STATUS", {"state": "resident", "model": "home-model"}), \
             patch.object(server, "ollama_catalog", return_value={"available": True, "models": ["home-model"]}), \
             patch.object(server, "ollama_chat_endpoint_status", return_value=(True, "ready")), \
             patch.object(server, "ThreadingHTTPServer", FakeHTTPServer), \
             patch.object(server, "start_lifecycle_watchdog", side_effect=lambda: events.append("watchdog")), \
             patch.object(server, "start_home_chat_model_preload", side_effect=lambda: (events.append("preload") or {"ok": True, "state": "scheduled"})), \
             patch.object(server, "shutdown_all_workloads"):
            server.main()

        self.assertLess(events.index("preload"), events.index("serve_forever"))

    def test_core_remains_available_when_host_defers_home_model_admission(self):
        events = []

        class FakeHTTPServer:
            def __init__(self, *_args):
                events.append("bound")

            def serve_forever(self):
                events.append("serve_forever")
                raise KeyboardInterrupt

            def server_close(self):
                events.append("closed")

        residency = {"state": "error", "model": "home-model", "detail": "insufficient VRAM; reserve preserved"}
        trace = unittest.mock.Mock()
        with patch.dict(server.os.environ, {"ARIADNE_ALLOW_UNSUPERVISED_CORE": "1"}), \
             patch.object(server, "start_owned_ollama", return_value={"state": "online"}), \
             patch.object(server, "server_context", return_value=None), \
             patch.object(server, "load_gateway", return_value=None), \
             patch.object(server.NEWS_BRIEFING_CACHE, "start_background_sync"), \
             patch.object(server, "start_news_image_sync"), \
             patch.object(server.WATCHLIST, "start"), \
             patch.object(server, "expire_home_chats"), \
             patch.object(server, "_wait_for_home_model_preload", return_value={"ok": True, "state": "settled"}), \
             patch.object(server, "HOME_MODEL_PRELOAD_STATUS", residency), \
             patch.object(server, "ollama_catalog", return_value={"available": True, "models": ["home-model"]}), \
             patch.object(server, "ollama_chat_endpoint_status", return_value=(True, "ready")), \
             patch.object(server, "ThreadingHTTPServer", FakeHTTPServer), \
             patch.object(server, "start_lifecycle_watchdog", side_effect=lambda: events.append("watchdog")), \
             patch.object(server, "start_home_chat_model_preload", side_effect=lambda: (events.append("preload") or {"ok": True, "state": "scheduled"})), \
             patch.object(server, "monitor_ollama_models") as monitor, \
             patch.object(server, "STARTUP_TRACE", trace), \
             patch.object(server, "shutdown_all_workloads"):
            server.main()

        self.assertIn("serve_forever", events)
        self.assertEqual(residency["state"], "error")
        monitor.assert_not_called()
        trace.mark.assert_any_call("home_model_unavailable", "degraded", model="home-model",
                                   state="error", detail="insufficient VRAM; reserve preserved")

    def test_startup_schedules_preload_without_waiting_for_model_load(self):
        started = threading.Event()
        release = threading.Event()

        def blocking_preload(model, *, reason, **_kwargs):
            started.set()
            release.wait(2)
            return {"ok": True, "model": model}

        with patch.object(server, "ollama_catalog", return_value={"available": True, "loaded": []}), \
             patch.object(server, "inference_lease", return_value=nullcontext()), \
             patch.object(server, "preload_ollama_model", side_effect=blocking_preload) as preload:
            begin = time.monotonic()
            result = server.start_home_chat_model_preload()
            elapsed = time.monotonic() - begin

            self.assertLess(elapsed, 0.5)
            self.assertEqual(result["state"], "scheduled")
            self.assertTrue(started.wait(1))
            preload.assert_called_once_with("home-model", reason="home_session_preload",
                                            options={"num_ctx": server.HOME_CONTEXT_TOKENS},
                                            allow_calibration=True)
            release.set()

    def test_home_preload_is_performed_by_rust_host(self):
        host_result = {"detail": "Host verified residency", "residency": {"full_gpu_residency": True}}
        with patch.object(server, "inference_lease", return_value=nullcontext(host_result)) as lease, \
             patch.object(server, "post_json") as post:
            result = server.preload_ollama_model(
                "home-model", options={"num_ctx": server.HOME_CONTEXT_TOKENS}
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["residency"], host_result["residency"])
        lease.assert_called_once_with(
            "home-model", server.OLLAMA_URL,
            context_length=server.HOME_CONTEXT_TOKENS,
            allow_calibration=False,
        )
        post.assert_not_called()

    def test_home_generation_keeps_the_model_resident(self):
        calls = []

        class FakeMcp:
            def ollama_chat(self, messages, **kwargs):
                calls.append(kwargs)
                return "answer"

        with server.ai_gpu_admission():
            answer = server._home_model_chat(FakeMcp(), [{"role": "user", "content": "hello"}], {})

        self.assertEqual(answer, "answer")
        self.assertEqual(calls[0]["keep_alive"], server.HOME_MODEL_KEEP_ALIVE)

    def test_home_session_close_releases_its_rust_host_model_lease(self):
        original_sessions = server.SESSIONS
        server.SESSIONS = {
            "home-session": {
                "surface": "home", "jobs": set(), "host_model_lease_id": "home-host-lease",
            },
        }
        try:
            with patch.object(server, "host_release_model_lease") as release, \
                 patch.object(server, "shutdown_idle_workloads"):
                self.assertTrue(server._close_session("home-session"))
        finally:
            server.SESSIONS = original_sessions
        release.assert_called_once_with("home-host-lease", server.OLLAMA_URL)

    def test_home_session_owns_and_renews_rust_host_model_lease(self):
        class Request:
            def __init__(self, path, body):
                self.path = path
                self.body = body
                self.response = None

            def workspace_request(self):
                return False

            def read_json(self):
                return self.body

            def send_json(self, payload, status=200):
                self.response = (payload, status)

        original_sessions = server.SESSIONS
        original_idle_done = server.IDLE_SHUTDOWN_DONE
        server.SESSIONS = {}
        try:
            with patch.object(server, "host_acquire_model_lease", return_value="home-host-lease") as acquire, \
                 patch.object(server.HOME_CHAT_STORE, "get_or_create", return_value=({"chat_id": "chat", "messages": [], "title": "Test"}, False)), \
                 patch.object(server, "home_identity_kernel_metadata", return_value={}), \
                 patch.object(server, "record_home_event"), \
                 patch.object(server.CORE_INTERACTION_STREAM, "emit"), \
                 patch.object(server, "list_documents", return_value=[]), \
                 patch.object(server, "host_renew_model_lease") as renew, \
                 patch.object(server, "vault_session_status", return_value={}), \
                 patch.object(server, "host_release_model_lease") as release, \
                 patch.object(server, "shutdown_idle_workloads"):
                start = Request("/api/session/start", {"surface": "home"})
                server.AriadneHandler.do_POST(start)
                self.assertEqual(start.response[1], 200)
                session_id = start.response[0]["session_id"]
                self.assertEqual(server.SESSIONS[session_id]["host_model_lease_id"], "home-host-lease")
                acquire.assert_called_once_with(
                    "home-model", server.OLLAMA_URL,
                    context_length=server.HOME_CONTEXT_TOKENS, allow_calibration=True,
                )

                heartbeat = Request("/api/session/heartbeat", {"session_id": session_id})
                server.AriadneHandler.do_POST(heartbeat)
                renew.assert_called_once_with("home-host-lease", server.OLLAMA_URL)

                close = Request("/api/session/close", {"session_id": session_id})
                server.AriadneHandler.do_POST(close)
                release.assert_called_once_with("home-host-lease", server.OLLAMA_URL)
        finally:
            server.SESSIONS = original_sessions
            server.IDLE_SHUTDOWN_DONE = original_idle_done

    def test_home_preload_is_rejected_after_shutdown_begins(self):
        original_shutdown = server.SHUTDOWN_REQUESTED
        server.SHUTDOWN_REQUESTED = True
        try:
            with patch.object(server, "inference_lease") as lease:
                result = server.preload_ollama_model("home-model")
                scheduled = server.start_home_chat_model_preload()
            self.assertFalse(result["ok"])
            self.assertEqual(scheduled["state"], "stopping")
            lease.assert_not_called()
        finally:
            server.SHUTDOWN_REQUESTED = original_shutdown

    def test_resident_home_model_is_rechecked_for_the_configured_host_profile(self):
        with patch.object(server, "ollama_catalog", return_value={"available": True, "loaded": ["home-model"]}), \
             patch.object(server, "preload_ollama_model") as preload:
            result = server.start_home_chat_model_preload()
            thread = server.HOME_MODEL_PRELOAD_THREAD
            if thread is not None:
                thread.join(1)

        self.assertEqual(result["state"], "scheduled")
        preload.assert_called_once_with("home-model", options={"num_ctx": server.HOME_CONTEXT_TOKENS},
                                        reason="home_session_preload", allow_calibration=True)

    def test_home_model_handoff_verifies_residency(self):
        original_status = dict(server.HOME_MODEL_PRELOAD_STATUS)
        try:
            with patch.object(server, "ollama_catalog", side_effect=[
                {"available": True, "loaded": []},
                {"available": True, "loaded": ["home-model"]},
            ]), patch.object(server, "preload_ollama_model", return_value={"ok": True}):
                server._preload_home_chat_model_worker("home-model")
            self.assertEqual(server.HOME_MODEL_PRELOAD_STATUS["state"], "resident")
            self.assertIn("verified home-model residency", server.HOME_MODEL_PRELOAD_STATUS["detail"])
        finally:
            server.HOME_MODEL_PRELOAD_STATUS = original_status

    def test_home_and_chat_do_not_trigger_preload(self):
        js = Path(__file__).with_name("home.js").read_text(encoding="utf-8")
        self.assertNotIn("/api/model-control/preload", js)
        self.assertNotIn("preloadHomeChatModel", js)


if __name__ == "__main__":
    unittest.main()
