import os
import tempfile
import unittest
from unittest.mock import patch

from discovery_service.engine import DiscoveryEngine
from discovery_service.models import SourceDefinition


class RuntimeMetadataTests(unittest.TestCase):
    def test_health_identifies_environment_instance_build_and_target(self):
        source = SourceDefinition("test", "Test", "https://example.test/feed.xml", "Technology", "rss", True)
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "ARIADNE_ENVIRONMENT": "dev",
                "ARIADNE_INSTANCE": "local-dev-discovery",
                "ARIADNE_BUILD_SHA": "abc123",
                "DISCOVERY_SERVICE_SIGNAL_SERVICE_URL": "http://signal:8788",
            },
            clear=False,
        ):
            engine = DiscoveryEngine(os.path.join(directory, "discovery.sqlite3"), sources=[source])
            try:
                health = engine.health()
            finally:
                engine.close()
        self.assertEqual(health["environment"], "dev")
        self.assertEqual(health["instance"], "local-dev-discovery")
        self.assertEqual(health["build_sha"], "abc123")
        self.assertEqual(health["signal_service_url"], "http://signal:8788")


if __name__ == "__main__":
    unittest.main()
