"""AC-5: no duplicate on crash or lost response."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fakes import ProcessKilled
from tests.support import FILE_A, Harness, run_once

ISSUE_PATH = "/rest/api/3/issue"


class RecoveryTests(unittest.TestCase):
    def test_post_then_crash_is_reconciled_by_label(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")

        harness.release()
        self.assertEqual(run_once(harness), 0, harness.last_run_output)
        harness.reopen()
        self.assertTrue(harness.state.baseline_complete())

        harness.command_comment("thread-1", "comment-1", title="Crashy")
        # The fake persists the issue and then the client dies before reading
        # the response, which is what a killed process leaves behind.
        harness.jira.crash_after_post = True
        harness.release()
        with self.assertRaises(ProcessKilled):
            run_once(harness)
        harness.jira.crash_after_post = False
        harness.reopen()

        thread = harness.state.get_thread("thread-1")
        self.assertEqual(thread["state"], "sending")
        self.assertEqual(harness.jira.count("POST", ISSUE_PATH), 1)
        # The fake persisted the issue before the crash, so it is a real one.
        self.assertEqual([issue["key"] for issue in harness.jira.issues], ["FP-1"])

        harness.release()
        self.assertEqual(run_once(harness), 0, harness.last_run_output)
        harness.reopen()

        thread = harness.state.get_thread("thread-1")
        self.assertEqual(thread["state"], "linked")
        self.assertEqual(thread["issue_key"], "FP-1")
        self.assertEqual(harness.jira.count("POST", ISSUE_PATH), 1)
        self.assertEqual(len(harness.penpot.created_comments()), 1)
        self.assertEqual(len(harness.telegram.messages), 1)

    def test_timeout_then_two_empty_searches_needs_manual(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()
        harness.command_comment("thread-1", "comment-1", title="Lost")
        harness.jira.drop_post = True

        harness.cycle()
        self.assertEqual(harness.jira.count("POST", ISSUE_PATH), 1)
        thread = harness.state.get_thread("thread-1")
        self.assertEqual(thread["state"], "sending")
        self.assertEqual(thread["empty_searches"], 1)

        harness.jira.drop_post = False
        harness.clock.advance(60)
        harness.cycle()
        thread = harness.state.get_thread("thread-1")
        self.assertEqual(thread["state"], "needs-manual")
        self.assertEqual(harness.jira.count("POST", ISSUE_PATH), 1)
        alerts = [job for job in harness.state.list_jobs() if job["kind"] == "telegram_alert"]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(len(harness.telegram.messages), 1)

    def test_empty_searches_at_poll_interval_reach_needs_manual(self):
        """Intermediate polls must not reset the 60 s empty-search window."""
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()
        harness.command_comment("thread-1", "comment-1", title="Slow empty")
        harness.jira.drop_post = True

        harness.cycle()  # empty search #1 at t0
        first = harness.state.get_thread("thread-1")["last_empty_at"]

        harness.clock.advance(30)
        harness.cycle()  # empty search #2 at t0 + 30 (below the gap)
        thread = harness.state.get_thread("thread-1")
        self.assertEqual(thread["state"], "sending")
        self.assertEqual(thread["last_empty_at"], first)

        harness.clock.advance(30)
        harness.cycle()  # empty search #3 at t0 + 60 -> needs-manual
        thread = harness.state.get_thread("thread-1")
        self.assertEqual(thread["state"], "needs-manual")
        self.assertEqual(harness.jira.count("POST", ISSUE_PATH), 1)
        self.assertEqual(len(harness.telegram.messages), 1)

    def test_failed_search_is_not_empty(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()
        harness.command_comment("thread-1", "comment-1", title="Rate limited")
        harness.jira.drop_post = True
        harness.jira.fail_search_times = 1
        harness.jira.retry_after = 5
        harness.jira.add_issue("FP-1", labels=["figmapot", "figmapot-c-comment-1"])

        harness.cycle()
        thread = harness.state.get_thread("thread-1")
        self.assertEqual(thread["state"], "linked")
        self.assertEqual(thread["issue_key"], "FP-1")
        self.assertEqual(thread["empty_searches"], 0)
        self.assertEqual(harness.jira.count("POST", ISSUE_PATH), 1)
        self.assertIn(5.0, harness.clock.sleeps)

    def test_search_failure_is_not_counted_as_empty(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()
        harness.command_comment("thread-1", "comment-1", title="Broken search")
        harness.jira.drop_post = True
        harness.jira.search_status = 500

        harness.cycle()
        thread = harness.state.get_thread("thread-1")
        self.assertEqual(thread["state"], "sending")
        self.assertEqual(thread["empty_searches"], 0)
        self.assertIsNone(thread["last_empty_at"])
        self.assertEqual(harness.jira.count("POST", ISSUE_PATH), 1)
        self.assertEqual([job for job in harness.state.list_jobs() if job["kind"] == "telegram_alert"], [])

    def test_malformed_search_body_is_not_counted_as_empty(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()
        harness.command_comment("thread-1", "comment-1", title="Malformed")
        harness.jira.drop_post = True
        harness.jira.search_body = {}

        harness.cycle()
        harness.clock.advance(60)
        harness.cycle()
        thread = harness.state.get_thread("thread-1")
        self.assertEqual(thread["state"], "sending")
        self.assertEqual(thread["empty_searches"], 0)
        self.assertIsNone(thread["last_empty_at"])
        self.assertEqual([job for job in harness.state.list_jobs() if job["kind"] == "telegram_alert"], [])
        self.assertEqual(harness.jira.count("POST", ISSUE_PATH), 1)

    def test_exhausted_search_retries_preserve_the_deadline(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()
        harness.command_comment("thread-1", "comment-1", title="Rate limited")
        harness.jira.drop_post = True
        harness.jira.fail_search_times = 1000
        harness.jira.retry_after = 120

        harness.cycle()  # six in-call retries, all 429 with Retry-After: 120
        searches = harness.jira.count("GET", "/rest/api/3/search/jql")
        self.assertGreaterEqual(searches, 6)
        thread = harness.state.get_thread("thread-1")
        self.assertEqual(thread["state"], "sending")
        self.assertIsNotNone(thread["next_search_at"])

        # The next poll, 30 s later, must not search before the server's deadline.
        harness.clock.advance(30)
        harness.cycle()
        self.assertEqual(harness.jira.count("GET", "/rest/api/3/search/jql"), searches)

        harness.clock.advance(120)
        harness.cycle()
        self.assertGreater(harness.jira.count("GET", "/rest/api/3/search/jql"), searches)

    def test_multiple_issues_link_oldest_and_alert(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()
        harness.command_comment("thread-1", "comment-1", title="Duplicate")
        harness.jira.drop_post = True
        harness.jira.add_issue("FP-1", labels=["figmapot", "figmapot-c-comment-1"],
                               created="2026-10-20T10:00:00.000+0000")
        harness.jira.add_issue("FP-2", labels=["figmapot", "figmapot-c-comment-1"],
                               created="2026-10-20T10:05:00.000+0000")

        harness.cycle()
        thread = harness.state.get_thread("thread-1")
        self.assertEqual(thread["state"], "linked")
        self.assertEqual(thread["issue_key"], "FP-1")
        self.assertEqual(harness.jira.count("POST", ISSUE_PATH), 1)
        alerts = [job for job in harness.state.list_jobs() if job["kind"] == "telegram_alert"]
        self.assertEqual(len(alerts), 1)
        self.assertIn("FP-1", alerts[0]["payload"])
        self.assertIn("FP-2", alerts[0]["payload"])
        self.assertEqual(len(harness.telegram.messages), 2)


if __name__ == "__main__":
    unittest.main()
