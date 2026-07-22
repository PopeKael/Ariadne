from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rebuild_lock import ingestion_lock


class RebuildLockTests(unittest.TestCase):
    def test_second_ingestion_cannot_start_while_lock_is_held(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with ingestion_lock(root):
                with self.assertRaisesRegex(RuntimeError, "Another rebuild-v1 ingestion run"):
                    with ingestion_lock(root):
                        pass
            self.assertFalse((root / "Logs/rebuild-v1-ingest.lock").exists())


if __name__ == "__main__":
    unittest.main()
