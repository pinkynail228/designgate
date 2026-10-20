"""AC-8: ``python3 -m figmapot_bot --once`` runs one cycle and exits 0."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.support import FILE_A, Harness

PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class RunTests(unittest.TestCase):
    def test_once_exits_zero(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.release()
        result = subprocess.run(
            [sys.executable, "-m", "figmapot_bot", "--once"],
            cwd=PACKAGE_DIR,
            env=harness.env_environment(),
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        harness.reopen()
        self.assertTrue(harness.state.baseline_complete())


if __name__ == "__main__":
    unittest.main()
