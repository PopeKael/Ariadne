import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from plugin_activity import ActivityValidationError, PluginActivityStream  # noqa: E402


class PluginActivityTests(unittest.TestCase):
    def test_structured_lifecycle_is_async_and_does_not_use_avatar_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "activity.jsonl"
            stream = PluginActivityStream(path)
            reporter = stream.reporter(activity_id="activity-1", plugin_id="example.test", capability_id="example.run")
            reporter.started("Starting example.")
            reporter.stage("preparing", "Preparing input.")
            reporter.progress(42.5, "Processing input.", stage="processing")
            reporter.warning("Input contains an optional field.")
            reporter.completed("Example complete.")
            stream.flush()
            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            stream.close()
        self.assertEqual([event["state"] for event in events], ["started", "stage", "progress", "warning", "completed"])
        self.assertEqual(events[2]["progress"], 42.5)
        self.assertEqual(events[2]["stage"], "processing")
        self.assertNotIn("avatar", events[0])
        self.assertNotIn("image", events[0])

    def test_invalid_state_and_progress_are_rejected(self):
        stream = PluginActivityStream(Path(tempfile.gettempdir()) / "ariadne-plugin-activity-test.jsonl")
        try:
            with self.assertRaises(ActivityValidationError):
                stream.emit(activity_id="a", plugin_id="p", capability_id="c", state="idle", status_text="No")
            with self.assertRaises(ActivityValidationError):
                stream.emit(activity_id="a", plugin_id="p", capability_id="c", state="progress", progress=101, status_text="Too far")
        finally:
            stream.close()

    def test_recent_status_is_available_while_writer_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            stream = PluginActivityStream(Path(temporary) / "activity.jsonl")
            try:
                event = stream.emit(activity_id="a", plugin_id="p", capability_id="c", state="started", status_text="Working")
                self.assertEqual(stream.recent(1)[0]["event_id"], event.event_id)
                self.assertFalse(stream._worker.ident is None)
            finally:
                stream.close()

    def test_failed_and_cancelled_are_contract_states(self):
        with tempfile.TemporaryDirectory() as temporary:
            stream = PluginActivityStream(Path(temporary) / "activity.jsonl")
            try:
                reporter = stream.reporter(activity_id="a", plugin_id="p", capability_id="c")
                reporter.failed("Backend failed.", stage="rendering")
                reporter.cancelled("User cancelled the job.")
                self.assertEqual([item["state"] for item in stream.recent(2)], ["cancelled", "failed"])
            finally:
                stream.close()


if __name__ == "__main__":
    unittest.main()
