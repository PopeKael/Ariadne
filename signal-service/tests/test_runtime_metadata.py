import os
import tempfile
import unittest
from unittest.mock import patch

from signal_service.service import SignalService


class RuntimeMetadataTests(unittest.TestCase):
    def test_health_identifies_environment_instance_and_build(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "ARIADNE_ENVIRONMENT": "dev",
                "ARIADNE_INSTANCE": "local-dev-signal",
                "ARIADNE_BUILD_SHA": "abc123",
            },
            clear=False,
        ):
            service = SignalService(os.path.join(directory, "signals.sqlite3"), feeds=[])
            try:
                health = service.health()
            finally:
                service.close()
        self.assertEqual(health["environment"], "dev")
        self.assertEqual(health["instance"], "local-dev-signal")
        self.assertEqual(health["build_sha"], "abc123")


if __name__ == "__main__":
    unittest.main()
