"""AC-3: baseline is atomic and never imports history."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from figmapot_bot.adapters import PenpotError

from tests.support import FILE_A, FILE_B, Harness


class BaselineTests(unittest.TestCase):
    def test_crash_mid_baseline_then_restart(self):
        harness = Harness(files=(FILE_A, FILE_B))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-a")
        harness.penpot.add_thread(FILE_B, "thread-b")
        harness.penpot.add_comment("thread-b", "comment-b", "someone", "history", [])
        # a command already present before the baseline is history
        harness.command_comment("thread-a", "comment-a", title="Old work")
        harness.penpot.fail_threads_for.add(FILE_B)

        with self.assertRaises(PenpotError):
            harness.cycle()
        self.assertFalse(harness.state.baseline_complete())
        self.assertEqual(harness.state.seen_count(), 0)
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 0)

        harness.penpot.fail_threads_for.clear()
        harness.cycle()  # restart: baseline is redone
        self.assertTrue(harness.state.baseline_complete())
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 0)
        self.assertIsNone(harness.state.get_thread("thread-a"))
        self.assertEqual(harness.state.seen_count(), 2)

        harness.command_comment("thread-a", "comment-c", title="New work")
        harness.cycle()
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 1)

    def test_normal_restart_processes_backlog(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()  # first run: baseline with no comments
        self.assertTrue(harness.state.baseline_complete())

        harness.command_comment("thread-1", "comment-1", title="Backlog work")
        harness.restart()
        harness.cycle()
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 1)
        thread = harness.state.get_thread("thread-1")
        self.assertEqual(thread["state"], "linked")

    def test_bad_json_read_keeps_baseline_incomplete(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.command_comment("thread-1", "comment-1", title="History")
        harness.penpot.comments_bad_json_times = 1

        with self.assertRaises(PenpotError):
            harness.cycle()
        self.assertFalse(harness.state.baseline_complete())
        self.assertEqual(harness.state.seen_count(), 0)
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 0)

        # Recovery: the baseline is redone cleanly and imports no history.
        harness.cycle()
        self.assertTrue(harness.state.baseline_complete())
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 0)

        harness.command_comment("thread-1", "comment-2", title="New work")
        harness.cycle()
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 1)

    def test_non_list_read_keeps_baseline_incomplete(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.command_comment("thread-1", "comment-1", title="History")
        harness.penpot.comments_body = {}
        harness.penpot.comments_body_times = 1

        with self.assertRaises(PenpotError):
            harness.cycle()
        self.assertFalse(harness.state.baseline_complete())
        self.assertEqual(harness.state.seen_count(), 0)
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 0)


if __name__ == "__main__":
    unittest.main()
