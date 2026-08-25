import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


class RendererStartTests(unittest.TestCase):
    def test_renderer_start_returns_pending_operation_without_browser_timeout(self):
        original_process = server.WAN2GP_PROCESS
        original_log = server.WAN2GP_LOG
        original_thread = server.RENDERER_START_THREAD
        original_owner = (server.GPU_OWNER, server.GPU_TRANSITION_STATE, server.GPU_TRANSITION_DETAIL,
                          server.GPU_TRANSITION_OPERATION, server.GPU_TRANSITION_STARTED_AT,
                          server.RENDERER_LIFECYCLE_STATE, server.RENDERER_LIFECYCLE_ERROR)
        try:
            server.WAN2GP_PROCESS = None
            fake_thread = MagicMock()
            fake_thread.is_alive.return_value = True
            with tempfile.TemporaryDirectory() as temporary:
                server.WAN2GP_LOG = Path(temporary) / "linux-renderer.log"
                def status(*, ignore_transition=False):
                    return {"state": "offline", "detail": "offline"} if ignore_transition else {"state": "starting", "lifecycle_state": "STARTING_WSL", "detail": "transition pending"}
                with patch.object(server, "wan2gp_status", side_effect=status), \
                     patch.object(server.threading, "Thread", return_value=fake_thread):
                    result = server.start_wan2gp()
            self.assertTrue(result["ok"])
            self.assertEqual(result["wan2gp"]["state"], "starting")
            self.assertEqual(result["wan2gp"]["lifecycle_state"], "STARTING_WSL")
            fake_thread.start.assert_called_once()
        finally:
            server.WAN2GP_PROCESS = original_process
            server.WAN2GP_LOG = original_log
            server.RENDERER_START_THREAD = original_thread
            (server.GPU_OWNER, server.GPU_TRANSITION_STATE, server.GPU_TRANSITION_DETAIL,
             server.GPU_TRANSITION_OPERATION, server.GPU_TRANSITION_STARTED_AT,
             server.RENDERER_LIFECYCLE_STATE, server.RENDERER_LIFECYCLE_ERROR) = original_owner

    def test_duplicate_start_reuses_existing_operation(self):
        original_thread = server.RENDERER_START_THREAD
        original_owner = (server.GPU_OWNER, server.GPU_TRANSITION_STATE, server.GPU_TRANSITION_DETAIL,
                          server.GPU_TRANSITION_OPERATION, server.GPU_TRANSITION_STARTED_AT)
        try:
            existing = MagicMock()
            existing.is_alive.return_value = True
            server.RENDERER_START_THREAD = existing
            server.GPU_OWNER = "TRANSITION"
            server.GPU_TRANSITION_STATE = "WAITING_FOR_HEALTH"
            with patch.object(server, "wan2gp_status", return_value={"state": "starting", "lifecycle_state": "WAITING_FOR_HEALTH", "detail": "waiting"}), \
                 patch.object(server.threading, "Thread") as thread_factory:
                result = server.start_wan2gp()
            self.assertTrue(result["ok"])
            self.assertEqual(result["wan2gp"]["lifecycle_state"], "WAITING_FOR_HEALTH")
            thread_factory.assert_not_called()
        finally:
            server.RENDERER_START_THREAD = original_thread
            (server.GPU_OWNER, server.GPU_TRANSITION_STATE, server.GPU_TRANSITION_DETAIL,
             server.GPU_TRANSITION_OPERATION, server.GPU_TRANSITION_STARTED_AT) = original_owner


if __name__ == "__main__":
    unittest.main()
