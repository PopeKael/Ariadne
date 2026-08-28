import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


CONTROL_ROOT = Path(__file__).resolve().parent
SYSTEM_ROOT = CONTROL_ROOT.parent / "00_System"
sys.path.insert(0, str(CONTROL_ROOT))
sys.path.insert(0, str(SYSTEM_ROOT))

import ariadne_mcp  # noqa: E402
import server  # noqa: E402
from home_chat_store import ChatStore  # noqa: E402


class IdentityKernelLoaderTests(unittest.TestCase):
    def setUp(self):
        self.original_root = ariadne_mcp.ROOT
        self.original_path = ariadne_mcp.IDENTITY_KERNEL_PATH

    def tearDown(self):
        ariadne_mcp.ROOT = self.original_root
        ariadne_mcp.IDENTITY_KERNEL_PATH = self.original_path

    def test_v1_1_kernel_present_loads_normally(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "Ariadne Identity Kernel v1.1.0.md"
            shutil.copyfile(SYSTEM_ROOT.parent / "Ariadne Identity Kernel v1.1.0.md", target)
            ariadne_mcp.ROOT = root
            ariadne_mcp.IDENTITY_KERNEL_PATH = target

            runtime, metadata = ariadne_mcp.identity_kernel_runtime()

            self.assertEqual(metadata["version"], "1.1.0")
            self.assertEqual(metadata["source"], "Ariadne Identity Kernel v1.1.0.md")
            self.assertIn("technical precision", runtime)

    def test_v1_1_kernel_absent_reports_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ariadne_mcp.ROOT = root
            ariadne_mcp.IDENTITY_KERNEL_PATH = root / "Ariadne Identity Kernel v1.1.0.md"

            runtime, metadata = ariadne_mcp.identity_kernel_runtime()

            self.assertEqual(metadata["version"], "fallback")
            self.assertIsNone(metadata["source"])
            self.assertIn("stable behavioural guidance", runtime)


class IdentityHealthTests(unittest.TestCase):
    @staticmethod
    def health_with_metadata(metadata):
        class FakeMcp:
            def identity_system_prefix(self, scope="user"):
                return "IDENTITY", {**metadata, "scope": scope}

        counts = {
            "root": "C:/test-vault",
            "available": True,
            "catalogue_records": 1,
            "embedding_documents": 1,
            "embedding_chunks": 1,
            "embedding_failures": 0,
        }
        with patch.object(server, "_home_mcp", return_value=FakeMcp()), \
             patch.object(server, "vault_counts", return_value=counts), \
             patch.object(server, "vault_control_available", return_value=True), \
             patch.object(server, "ollama_status", return_value={"state": "online", "detail": "online"}), \
             patch.object(server, "home_index_status", return_value={"state": "healthy", "detail": "indexed"}), \
             patch.object(server, "configured_ollama_store", return_value="configured"):
            return server.home_health_payload()

    def test_health_distinguishes_loaded_and_fallback(self):
        loaded = self.health_with_metadata({
            "id": "ariadne", "version": "1.1.0",
            "source": "Ariadne Identity Kernel v1.1.0.md",
        })
        fallback = self.health_with_metadata({
            "id": "ariadne", "version": "fallback", "source": None,
        })

        self.assertEqual(loaded["identity_kernel"]["state"], "loaded")
        self.assertEqual(loaded["identity_kernel"]["status"], "healthy")
        self.assertEqual(loaded["identity_kernel"]["version"], "1.1.0")
        self.assertEqual(fallback["identity_kernel"]["state"], "fallback")
        self.assertEqual(fallback["identity_kernel"]["status"], "warning")
        identity_service = next(item for item in fallback["services"] if item["name"] == "Identity kernel")
        self.assertEqual(identity_service["state"], "attention")

    def test_health_metadata_matches_home_prompt_and_persisted_chat_metadata(self):
        metadata = {
            "id": "ariadne", "version": "1.1.0",
            "source": "Ariadne Identity Kernel v1.1.0.md",
        }

        class FakeMcp:
            def __init__(self):
                self.calls = []

            def identity_system_prefix(self, scope="user"):
                return f"IDENTITY {scope} v1.1.0\n", {**metadata, "scope": scope}

            def ollama_chat(self, messages, **kwargs):
                self.calls.append(messages)
                return "A durable answer."

        fake = FakeMcp()
        with tempfile.TemporaryDirectory() as temporary:
            store = ChatStore(Path(temporary))
            chat = store.create()
            planner = {
                "plan": {"use_vault": False, "tools": [], "needs_current_information": False},
                "semantic": {}, "fallback": False, "telemetry": {},
            }
            with patch.object(server, "_home_mcp", return_value=fake), \
                 patch.object(server, "HOME_CHAT_STORE", store), \
                 patch.object(server, "home_planner_request", return_value=planner), \
                 patch.object(server, "record_home_event"), \
                 patch.object(server, "emit_state"), \
                 patch.object(server, "_send_avatar_event_with_retry", return_value=True), \
                 patch.object(server, "CORE_INTERACTION_STREAM"):
                health = self.health_with_metadata(metadata)
                response = server.home_chat_payload("A direct question", [], "never", chat["chat_id"])

            self.assertEqual(health["identity_kernel"]["version"], response["identity_kernel"]["version"])
            self.assertIn("IDENTITY user v1.1.0", fake.calls[-1][0]["content"])
            self.assertEqual(store.get(chat["chat_id"])["identity_kernel"]["version"], "1.1.0")


if __name__ == "__main__":
    unittest.main()
