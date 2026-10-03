import concurrent.futures
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from startup_telemetry import StartupTrace


class StartupTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 10.0
        self.trace = StartupTrace(self.temp.name, run_id="test", monotonic=lambda: self.now,
                                  wall=lambda: "2026-10-03T00:00:00+00:00")

    def rows(self):
        return [json.loads(line) for line in self.trace.path.read_text().splitlines()]

    def test_persistent_phase_duration_uses_monotonic_clock(self):
        self.trace.mark("config_load_start", "started")
        self.now += 2.5
        self.trace.mark("config_load_end")
        row = self.rows()[-1]
        self.assertEqual(row["duration_ms"], 2500)
        self.assertEqual(row["elapsed_ms"], 2500)
        self.assertEqual(row["timestamp"], "2026-10-03T00:00:00+00:00")
        self.assertEqual(self.trace.snapshot()["events"], self.rows())

    def test_concurrent_repeated_polling_writes_one_milestone(self):
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            list(pool.map(lambda _: self.trace.connection("signal", "start"), range(100)))
        self.assertEqual(len(self.rows()), 1)

    def test_ready_waits_for_render_and_preload_and_reports_degradation(self):
        self.trace.mark("core_ready")
        self.trace.mark("model_preload_end", "degraded")
        for worker in ("news", "images", "lifecycle"):
            self.trace.worker_done(worker, worker != "news")
        self.assertNotIn("application_ready", [row["phase"] for row in self.rows()])
        self.assertTrue(self.trace.ui_rendered(dict(instance_id=self.trace.instance_id, surface="home",
                                                   navigation_elapsed_ms=456, browser_wall_time_ms=1234)))
        ready = self.rows()[-1]
        self.assertEqual(ready["phase"], "application_ready")
        self.assertEqual(ready["status"], "degraded")
        self.assertEqual(ready["detail"]["degraded"], ["background_jobs_ready", "model_preload_end"])

    def test_rejects_stale_instance_and_invalid_render_measurements(self):
        body = dict(instance_id=self.trace.instance_id, surface="chat", navigation_elapsed_ms=1, browser_wall_time_ms=1234)
        for update in ({"instance_id": "old"}, {"surface": "other"}, {"navigation_elapsed_ms": float("nan")}, {"browser_wall_time_ms": True}):
            self.assertFalse(self.trace.ui_rendered({**body, **update}))
        self.assertFalse(self.trace.path.exists())

    def test_write_failure_does_not_break_startup(self):
        with patch.object(Path, "mkdir", side_effect=PermissionError("denied")):
            self.trace.mark("core_start", "started")
        self.assertEqual(self.trace.snapshot()["write_error"], "PermissionError")

    def test_disabled_trace_writes_nothing(self):
        self.trace.enabled = False
        self.trace.mark("core_start")
        self.trace.worker_done("news", True)
        self.assertFalse(self.trace.path.exists())

    def test_hera_failure_and_recovery_are_separate_milestones(self):
        self.trace.connection("news", "start")
        self.now += 3
        self.trace.connection("news", "failed", error_type="TimeoutError")
        self.assertNotIn("hera_news_ready", [row["phase"] for row in self.rows()])
        self.now += 60
        self.trace.connection("news", "ready")
        self.assertEqual(self.rows()[-1]["duration_ms"], 63000)

    def test_real_news_poll_reports_connection_and_first_worker_outcome(self):
        from news_briefing_cache import NewsBriefingCache
        cache = NewsBriefingCache(cache_path=Path(self.temp.name) / "news.json", startup_trace=self.trace)
        with patch.object(cache, "_request_briefing", return_value={"ok": True, "articles": []}):
            cache.start_background_sync()
            cache.stop_background_sync()
        phases = [row["phase"] for row in self.rows()]
        self.assertIn("hera_news_start", phases)
        self.assertIn("hera_news_ready", phases)
        self.assertIn("background_news_end", phases)

    def test_live_http_contract_accepts_render_and_serves_observer(self):
        import server
        import threading
        import urllib.request
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        with patch.object(server, "STARTUP_TRACE", self.trace):
            thread.start()
            try:
                origin = f"http://127.0.0.1:{httpd.server_port}"
                with urllib.request.urlopen(origin + "/api/startup") as response:
                    state = json.load(response)
                with urllib.request.urlopen(origin + "/startup-telemetry.js") as response:
                    self.assertIn(b"AriadneStartup", response.read())
                body = dict(instance_id=state["instance_id"], surface="chat", navigation_elapsed_ms=123, browser_wall_time_ms=456)
                request = urllib.request.Request(origin + "/api/startup/ui-rendered", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request) as response:
                    self.assertTrue(json.load(response)["ok"])
                self.assertIn("ui_rendered", [row["phase"] for row in self.rows()])
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(2)


if __name__ == "__main__":
    unittest.main()
