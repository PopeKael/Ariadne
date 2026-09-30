import sys
import unittest
from concurrent.futures import ThreadPoolExecutor

ROOT = __import__("pathlib").Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from activity_state import ActivityStateStream  # noqa: E402


class ActivityStateTests(unittest.TestCase):
    def test_one_stream_drives_status_snapshot_and_async_avatar_mapping(self):
        emitted = []
        executor = ThreadPoolExecutor(max_workers=1)
        stream = ActivityStateStream(
            emit_avatar_state=lambda state, status: emitted.append((state, status)),
            executor=executor,
        )
        try:
            stream.publish("chat-1", "reading", "Reading sources")
            stream.publish("chat-1", "searching", "Checking Vault")
            stream.publish("chat-1", "thinking", "Thinking")
            stream.publish("chat-1", "answering", "Answering")
            stream.publish("chat-1", "complete", "Idle")
            executor.shutdown(wait=True)
            snapshot = stream.snapshot("chat-1").as_dict()
            self.assertEqual(snapshot["state"], "complete")
            self.assertEqual(snapshot["label"], "Idle")
            self.assertEqual(stream.current_snapshot().as_dict(), snapshot)
            self.assertEqual(emitted, [
                ("reading", "Reading sources"),
                ("searching_vault", "Checking Vault"),
                ("thinking", "Thinking"),
                ("speaking", "Answering"),
                ("idle", "Idle"),
            ])
        finally:
            stream.close()

    def test_duplicate_visual_states_are_coalesced_and_error_maps_immediately(self):
        emitted = []
        executor = ThreadPoolExecutor(max_workers=1)
        stream = ActivityStateStream(emit_avatar_state=lambda state, status: emitted.append(state), executor=executor)
        try:
            stream.publish("chat-2", "thinking")
            stream.publish("chat-2", "thinking")
            stream.publish("chat-2", "error", "The request failed.")
            executor.shutdown(wait=True)
            self.assertEqual(emitted, ["thinking", "error"])
        finally:
            stream.close()

    def test_same_avatar_pose_receives_each_authoritative_status_change(self):
        emitted = []
        executor = ThreadPoolExecutor(max_workers=1)
        stream = ActivityStateStream(emit_avatar_state=lambda state, status: emitted.append((state, status)), executor=executor)
        try:
            stream.publish("chat-3", "searching", "Checking Vault")
            stream.publish("chat-3", "searching", "Searching web")
            executor.shutdown(wait=True)
            self.assertEqual(emitted, [
                ("searching_vault", "Checking Vault"),
                ("searching_vault", "Searching web"),
            ])
            self.assertEqual(stream.current_snapshot().message, "Searching web")
        finally:
            stream.close()


if __name__ == "__main__":
    unittest.main()
