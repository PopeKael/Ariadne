import sys
import unittest
from pathlib import Path


SYSTEM_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SYSTEM_ROOT))

import ariadne_mcp  # noqa: E402


class IdentityKernelTests(unittest.TestCase):
    def test_active_kernel_loads_only_runtime_section(self):
        runtime, metadata = ariadne_mcp.identity_kernel_runtime()
        self.assertEqual(metadata["id"], "ariadne")
        self.assertEqual(metadata["version"], "1.0.0")
        self.assertLessEqual(len(runtime), 2_200)
        self.assertIn("BEHAVIOURAL GUIDANCE", runtime)
        self.assertNotIn("## Role", runtime)
        self.assertNotIn("## Change control", runtime)

    def test_prompt_prefix_is_delimited(self):
        prefix, metadata = ariadne_mcp.identity_system_prefix()
        self.assertEqual(metadata["version"], "1.0.0")
        self.assertIn("BEGIN IDENTITY", prefix)
        self.assertIn("END IDENTITY", prefix)


if __name__ == "__main__":
    unittest.main()
