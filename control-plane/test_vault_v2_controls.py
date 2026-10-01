"""Knowledge Vault control dispatch tests; no scripts or model calls execute."""
import time
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server


class VaultV2ControlTests(unittest.TestCase):
    def test_five_buttons_dispatch_external_vault_wrappers(self):
        system = Path(__file__).resolve().parents[2] / "KnowledgeVault/00_System"
        actions = {"ingest": ("Daily-Ingest.ps1", []), "audit_failures": ("Audit-Failed-Ingestion.ps1", []),
                   "embedding_rebuild": ("Build-Embeddings.ps1", ["-Rebuild"]),
                   "full_rebuild": ("Full-Vault-Rebuild.ps1", []),
                   "regression_tests": ("Run-Rebuild-Tests.ps1", [])}
        page = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
        for action, (script, arguments) in actions.items():
            with self.subTest(action=action), patch.object(server, "VAULT_SYSTEM", system), \
                    patch.object(server, "VAULT_ROOT", system.parent), \
                    patch.object(server, "JOBS", {}), \
                    patch.object(server, "SESSIONS", {"test": {"last_seen": time.monotonic(), "jobs": set()}}), \
                    patch.object(server.shutil, "which", return_value="powershell.exe"), \
                    patch.object(server, "_acquire_vault_ingestion_gpu", return_value=None), \
                    patch.object(server.threading, "Thread"), \
                    patch.object(server, "_start_process", return_value=Mock()) as start:
                self.assertIn(f'data-vault-action="{action}"', page)
                self.assertEqual(server.VAULT_ACTIONS[action], (script, arguments))
                job_id = server.start_vault_action("test", action)
                command, cwd = start.call_args.args
                self.assertEqual(command[command.index("-File") + 1], str(system / script))
                self.assertEqual(command[command.index("-File") + 2:], arguments)
                self.assertEqual(cwd, system.parent)
                self.assertEqual(server.JOBS[job_id]["action"], action)

    def test_ui_and_wrappers_describe_current_implementations(self):
        page = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("rebuild-v1", page)
        system = Path(__file__).resolve().parents[2] / "KnowledgeVault/00_System"
        self.assertIn("audit_v2_failures.py", (system / "Audit-Failed-Ingestion.ps1").read_text(encoding="utf-8-sig"))
        self.assertIn("full_vault_rebuild.py", (system / "Full-Vault-Rebuild.ps1").read_text(encoding="utf-8-sig"))
        suite = (system / "Run-Rebuild-Tests.ps1").read_text(encoding="utf-8-sig")
        for test in ("test_full_vault_rebuild.py", "test_daily_v2_selection.py", "test_chronology_retrieval.py",
                     "test_embedding_publication.py", "test_vault_v2_operations.py"):
            self.assertIn(test, suite)

    @unittest.skipUnless(os.name == "nt", "PowerShell compatibility entrypoints are Windows controls")
    def test_compatibility_wrappers_forward_read_only_switches_to_v2(self):
        root = Path(__file__).resolve().parents[1]
        expected = {"Daily-Ingest.ps1": (["-DryRun"], "read_only_v2_inbox_plan"),
                    "Audit-Failed-Ingestion.ps1": (["-DryRun"], "read_only_v2_state_audit"),
                    "Build-Embeddings.ps1": (["-Rebuild", "-DryRun"], "read_only_v2_embedding_plan"),
                    "Full-Vault-Rebuild.ps1": (["-DryRun"], "read_only_v2_rebuild_plan")}
        for script, (arguments, mode) in expected.items():
            with self.subTest(script=script):
                process = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                          str(root / "00_System" / script), *arguments],
                                         cwd=root, capture_output=True, text=True, timeout=30,
                                         env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(json.loads(process.stdout)["mode"], mode)

    def test_compatibility_menu_exits_before_retained_historical_server(self):
        script = (Path(__file__).resolve().parents[1] / "00_System/Start-AriadneControl.ps1").read_text(encoding="utf-8-sig")
        self.assertLess(script.index("Invoke-VaultV2.ps1"), script.index("exit $LASTEXITCODE"))
        self.assertLess(script.index("exit $LASTEXITCODE"), script.index("$MenuPath"))

    def test_standalone_v2_menu_has_all_five_matching_dispatch_actions(self):
        system = Path(__file__).resolve().parents[2] / "KnowledgeVault/00_System"
        page = (system / "Ariadne-Control.html").read_text(encoding="utf-8")
        dispatch = (system / "Start-AriadneControl.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("fetch('/run'", page)
        self.assertIn("$Path -ne '/run'", dispatch)
        self.assertNotIn("rebuild-v1", page)
        for action, script in (("ingest", "Daily-Ingest.ps1"), ("audit_failures", "Audit-Failed-Ingestion.ps1"),
                               ("embedding_rebuild", "Build-Embeddings.ps1"), ("full_rebuild", "Full-Vault-Rebuild.ps1"),
                               ("regression_tests", "Run-Rebuild-Tests.ps1")):
            self.assertIn(f'data-action="{action}"', page)
            self.assertIn(f"{action} =", dispatch)
            self.assertIn(f"Script = '{script}'", dispatch)


if __name__ == "__main__":
    unittest.main()
