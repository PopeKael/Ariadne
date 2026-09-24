from __future__ import annotations

import sys
import os
import threading
import time
import unittest
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
             patch.object(server, "expire_home_chats"), \
             patch.object(server, "ThreadingHTTPServer", FakeHTTPServer), \
             patch.object(server, "start_lifecycle_watchdog", side_effect=lambda: events.append("watchdog")), \
             patch.object(server, "start_home_chat_model_preload", side_effect=lambda: events.append("preload")), \
             patch.object(server, "shutdown_all_workloads"):
            server.main()

        self.assertLess(events.index("preload"), events.index("serve_forever"))

    def test_startup_schedules_preload_without_waiting_for_model_load(self):
        started = threading.Event()
        release = threading.Event()

        def blocking_preload(model, *, keep_alive):
            started.set()
            release.wait(2)
            return {"ok": True, "model": model, "keep_alive": keep_alive}

        with patch.object(server, "ollama_catalog", return_value={"available": True, "loaded": []}), \
             patch.object(server, "preload_ollama_model", side_effect=blocking_preload) as preload:
            begin = time.monotonic()
            result = server.start_home_chat_model_preload()
            elapsed = time.monotonic() - begin

            self.assertLess(elapsed, 0.5)
            self.assertEqual(result["state"], "scheduled")
            self.assertTrue(started.wait(1))
            preload.assert_called_once_with("home-model", keep_alive=server.HOME_MODEL_KEEP_ALIVE)
            release.set()

    def test_home_preload_uses_indefinite_ollama_residency(self):
        with patch.object(server, "post_json", return_value={}) as post:
            result = server.preload_ollama_model("home-model", keep_alive=server.HOME_MODEL_KEEP_ALIVE)

        self.assertTrue(result["ok"])
        self.assertEqual(post.call_args.args[1]["keep_alive"], -1)

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

    def test_resident_home_model_is_not_preloaded_again(self):
        with patch.object(server, "ollama_catalog", return_value={"available": True, "loaded": ["home-model"]}), \
             patch.object(server, "preload_ollama_model") as preload:
            result = server.start_home_chat_model_preload()
            thread = server.HOME_MODEL_PRELOAD_THREAD
            if thread is not None:
                thread.join(1)

        self.assertEqual(result["state"], "scheduled")
        preload.assert_not_called()

    def test_home_and_chat_do_not_trigger_preload(self):
        js = Path(__file__).with_name("home.js").read_text(encoding="utf-8")
        self.assertNotIn("/api/model-control/preload", js)
        self.assertNotIn("preloadHomeChatModel", js)


if __name__ == "__main__":
    unittest.main()
