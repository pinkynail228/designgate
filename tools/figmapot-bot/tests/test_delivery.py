"""AC-6: independent delivery jobs."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.support import FILE_A, Harness


class DeliveryTests(unittest.TestCase):
    def test_telegram_failure_does_not_block_penpot_reply(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()
        harness.telegram.fail_times = 100
        harness.command_comment("thread-1", "comment-1", title="Deliver me")

        harness.cycle()
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 1)
        self.assertEqual(len(harness.penpot.created_comments()), 1)
        self.assertEqual(len(harness.telegram.delivered), 0)
        pending = [job for job in harness.state.list_jobs(["pending"]) if job["kind"] == "telegram_message"]
        self.assertEqual(len(pending), 1)

        harness.telegram.fail_times = 0
        harness.clock.advance(60)
        harness.cycle()
        self.assertEqual(len(harness.telegram.delivered), 1)
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 1)
        self.assertEqual(len(harness.state.list_jobs(["pending"])), 0)

    def test_penpot_not_found_drops_reply_job(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()
        harness.penpot.not_found_comments = 1
        harness.command_comment("thread-1", "comment-1", title="Gone thread")

        harness.cycle()
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 1)
        self.assertEqual(len(harness.penpot.created_comments()), 1)
        self.assertEqual(len(harness.telegram.messages), 1)
        replies = [job for job in harness.state.list_jobs() if job["kind"] == "penpot_reply"]
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["state"], "dropped")
        self.assertEqual(harness.state.get_thread("thread-1")["state"], "linked")

    def test_property_retry_after_does_not_block_notifications(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()
        harness.jira.fail_property_times = 1
        harness.jira.property_retry_after = 3600
        harness.command_comment("thread-1", "comment-1", title="Slow property")

        harness.cycle()
        # Notifications are delivered in the same cycle; only the property job waits.
        self.assertEqual(len(harness.telegram.delivered), 1)
        self.assertEqual(len(harness.penpot.created_comments()), 1)
        self.assertEqual(harness.clock.sleeps, [])
        property_jobs = [job for job in harness.state.list_jobs(["pending"]) if job["kind"] == "property"]
        self.assertEqual(len(property_jobs), 1)
        self.assertAlmostEqual(property_jobs[0]["not_before"] - harness.clock.now(), 3600.0, places=3)
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 1)

        harness.clock.advance(3600)
        harness.cycle()
        self.assertEqual(len(harness.state.list_jobs(["pending"])), 0)
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 1)

    def test_huge_attempt_count_does_not_block_other_deliveries(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.cycle()  # baseline
        harness.state.add_job("property", {"key": "FP-1", "props": {}})
        harness.state.add_job("telegram_message", {"text": "queued behind"})
        harness.state.conn.execute("UPDATE jobs SET attempts = 1024 WHERE kind = 'property'")
        harness.jira.fail_property_times = 100
        harness.clock.advance(1)

        harness.cycle()
        # A huge attempt count must not raise; the queued delivery still runs.
        self.assertEqual(len(harness.telegram.delivered), 1)
        pending = [job for job in harness.state.list_jobs(["pending"]) if job["kind"] == "property"]
        self.assertEqual(len(pending), 1)
        self.assertLessEqual(pending[0]["not_before"] - harness.clock.now(), 90)


if __name__ == "__main__":
    unittest.main()
