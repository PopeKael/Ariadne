import sys
import unittest
from concurrent.futures import ThreadPoolExecutor

ROOT = __import__("pathlib").Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from activity_state import ActivityStateStream  # noqa: E402


class ActivityStateTests(unittest.TestCase):
    def test_one_stream_drives_status_snapshot_and_async_avatar_mapping(self):
        emitted = []
        statuses = []
        executor = ThreadPoolExecutor(max_workers=1)
        stream = ActivityStateStream(
            emit_avatar_state=lambda state: emitted.append(state),
            emit_avatar_status=lambda status: statuses.append(status),
            executor=executor,
        )
        try:
            stream.publish("chat-1", "reading", "Reading source article.")
            stream.publish("chat-1", "thinking", "Thinking.")
            stream.publish("chat-1", "answering", "Answering.")
            stream.publish("chat-1", "complete", "Complete.")
            executor.shutdown(wait=True)
            snapshot = stream.snapshot("chat-1").as_dict()
            self.assertEqual(snapshot["state"], "complete")
            self.assertEqual(snapshot["label"], "Complete")
            self.assertEqual(emitted, ["reading", "thinking", "speaking"])
            self.assertEqual(statuses, ["Reading source article.", "Thinking.", "Answering.", "Complete."])
        finally:
            stream.close()

    def test_duplicate_visual_states_are_coalesced_and_error_maps_immediately(self):
        emitted = []
        executor = ThreadPoolExecutor(max_workers=1)
        stream = ActivityStateStream(emit_avatar_state=lambda state: emitted.append(state), executor=executor)
        try:
            stream.publish("chat-2", "thinking")
            stream.publish("chat-2", "thinking")
            stream.publish("chat-2", "error", "The request failed.")
            executor.shutdown(wait=True)
            self.assertEqual(emitted, ["thinking", "error"])
        finally:
            stream.close()


if __name__ == "__main__":
    unittest.main()
