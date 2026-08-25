from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import avatar_events


class AvatarEventTests(unittest.TestCase):
    def test_state_payload_is_versioned_and_bounded(self) -> None:
        with patch.object(avatar_events, "_write_windows_pipe", return_value=True) as write:
            self.assertTrue(avatar_events.emit_state("thinking"))
            payload = json.loads(write.call_args.args[0])
        self.assertEqual(payload, {"v": 1, "type": "state", "state": "thinking"})

    def test_unknown_state_is_rejected_without_pipe_write(self) -> None:
        with patch.object(avatar_events, "_write_windows_pipe") as write:
            self.assertFalse(avatar_events.emit_state("inventing"))
        write.assert_not_called()

    def test_transport_failure_is_returned_as_false(self) -> None:
        with patch.object(avatar_events, "_write_windows_pipe", side_effect=OSError("offline")):
            self.assertFalse(avatar_events.emit("show"))

    def test_reload_avatar_is_versioned(self) -> None:
        with patch.object(avatar_events, "_write_windows_pipe", return_value=True) as write:
            self.assertTrue(avatar_events.reload_avatar())
            payload = json.loads(write.call_args.args[0])
        self.assertEqual(payload, {"v": 1, "type": "reload_avatar"})


if __name__ == "__main__":
    unittest.main()
