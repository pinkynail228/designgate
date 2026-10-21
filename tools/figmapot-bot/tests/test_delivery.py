"""AC-6: independent delivery jobs; BOT-X AC-2: neutral notices rendered by the chat;
BOT-X AC-3: deliveries survive a failing poll."""
from __future__ import annotations

import json
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


class NoticeTests(unittest.TestCase):
    def telegram_job(self, harness, kind):
        jobs = [job for job in harness.state.list_jobs() if job["kind"] == kind]
        self.assertEqual(len(jobs), 1)
        return json.loads(jobs[0]["payload"])

    def test_issue_notice_is_rendered_by_the_chat_adapter(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()
        harness.command_comment("thread-1", "comment-1", title="Deliver me")

        harness.cycle()

        thread = harness.state.get_thread("thread-1")
        expected = "New figmapot issue %s: %s\n%s\n%s" % (
            thread["issue_key"], "Deliver me", thread["issue_url"], harness.bot._board_link(thread),
        )
        self.assertEqual([message["text"] for message in harness.telegram.delivered], [expected])
        # The queued job carries the neutral notice, not the rendered text.
        payload = self.telegram_job(harness, "telegram_message")
        self.assertNotIn("text", payload)
        notice = payload["notice"]
        self.assertEqual(notice["kind"], "issue_created")
        self.assertEqual(notice["title"], "Deliver me")
        self.assertEqual(notice["issue_key"], thread["issue_key"])
        self.assertEqual(notice["issue_url"], thread["issue_url"])
        self.assertEqual(notice["board_url"], harness.bot._board_link(thread))
        self.assertEqual(notice["thread_url"], harness.bot._thread_link(thread))

    def test_needs_manual_notice_is_rendered_by_the_chat_adapter(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()
        harness.command_comment("thread-1", "comment-1", title="Lost")
        harness.jira.drop_post = True

        harness.cycle()  # empty search #1
        harness.clock.advance(60)
        harness.cycle()  # empty search #2 -> needs manual

        self.assertEqual(
            [message["text"] for message in harness.telegram.delivered],
            ["figmapot: no Jira issue found for thread thread-1 (comment comment-1) "
             "after two empty searches. Manual action needed."],
        )
        payload = self.telegram_job(harness, "telegram_alert")
        self.assertNotIn("text", payload)
        self.assertEqual(payload["notice"]["kind"], "needs_manual")

    def test_pending_text_jobs_are_delivered_unchanged(self):
        """BOT-X AC-2: jobs already in a live DB carry ready text."""
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.cycle()  # baseline
        harness.state.add_job("telegram_message", {"text": "legacy notice"})
        harness.state.add_job("telegram_alert", {"text": "legacy alert"})
        harness.restart()  # the pending jobs come from a previous run

        harness.cycle()

        self.assertEqual(
            [message["text"] for message in harness.telegram.delivered],
            ["legacy notice", "legacy alert"],
        )
        self.assertEqual(harness.state.list_jobs(["pending"]), [])


class PollFailureTests(unittest.TestCase):
    def test_pending_deliveries_survive_a_failing_penpot_poll(self):
        """BOT-X AC-3: a Penpot read failure must not starve the deliveries."""
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()  # baseline
        harness.state.add_job("telegram_message", {"text": "written before the outage"})
        harness.penpot.fail_threads_for.add(FILE_A)  # every Penpot read fails now

        # The poll error is re-raised after the deliveries, so --once still fails.
        with self.assertRaises(Exception):
            harness.bot.run(once=True)

        self.assertEqual(
            [message["text"] for message in harness.telegram.delivered],
            ["written before the outage"],
        )
        self.assertEqual(harness.state.list_jobs(["pending"]), [])
        self.assertIn("poll cycle failed", harness.logs())

    def test_pending_deliveries_survive_a_penpot_outage_on_restart(self):
        """BOT-X AC-3: even a failing profile read must not starve the deliveries."""
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()  # baseline
        harness.state.add_job("telegram_message", {"text": "written before the outage"})
        harness.restart()  # a fresh run reads the profile again
        harness.penpot.fail_logins = 1000  # Penpot is unreachable

        # The poll error is re-raised after the deliveries, so --once still fails.
        with self.assertRaises(Exception):
            harness.bot.run(once=True)

        self.assertEqual(
            [message["text"] for message in harness.telegram.delivered],
            ["written before the outage"],
        )
        self.assertEqual(harness.state.list_jobs(["pending"]), [])


if __name__ == "__main__":
    unittest.main()
