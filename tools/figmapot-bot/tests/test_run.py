"""AC-8: ``python3 -m figmapot_bot --once`` runs one cycle and exits 0."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.support import FILE_A, Harness, run_once


class RunTests(unittest.TestCase):
    def test_once_exits_zero(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.release()
        self.assertEqual(run_once(harness), 0, harness.last_run_output)
        harness.reopen()
        self.assertTrue(harness.state.baseline_complete())


if __name__ == "__main__":
    unittest.main()
