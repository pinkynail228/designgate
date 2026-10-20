"""AC-4: reservation, one issue per thread, reply with the existing key."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.support import FILE_A, Harness


class IssueCreationTests(unittest.TestCase):
    def test_two_commands_one_thread_only_one_post(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()  # baseline
        harness.command_comment("thread-1", "comment-1", title="First")
        harness.command_comment("thread-1", "comment-2", title="Second")

        harness.cycle()
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 1)
        self.assertEqual(harness.state.get_thread("thread-1")["state"], "linked")

        harness.restart()
        harness.cycle()
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 1)

    def test_command_on_linked_thread_replies_with_key(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()
        harness.command_comment("thread-1", "comment-1", title="First")
        harness.cycle()
        key = harness.state.get_thread("thread-1")["issue_key"]
        url = harness.state.get_thread("thread-1")["issue_url"]
        before = len(harness.penpot.created_comments())

        harness.command_comment("thread-1", "comment-2", title="Second")
        harness.cycle()
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 1)
        replies = [item["content"] for item in harness.penpot.created_comments()]
        # A new reply must appear for the second command, carrying the key.
        self.assertEqual(len(replies), before + 1, replies)
        self.assertEqual(replies[-1], "Created %s: %s" % (key, url))

    def test_property_failure_does_not_block_notifications(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()
        harness.jira.fail_property_times = 100
        harness.command_comment("thread-1", "comment-1", title="Retry me")

        harness.cycle()
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 1)
        self.assertEqual(len(harness.telegram.messages), 1)
        self.assertEqual(len(harness.penpot.created_comments()), 1)
        pending = [job for job in harness.state.list_jobs(["pending"]) if job["kind"] == "property"]
        self.assertEqual(len(pending), 1)

        harness.jira.fail_property_times = 0
        harness.clock.advance(60)
        harness.cycle()
        self.assertEqual(len(harness.state.list_jobs(["pending"])), 0)
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 1)


if __name__ == "__main__":
    unittest.main()
