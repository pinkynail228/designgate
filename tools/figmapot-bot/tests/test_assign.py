"""BOT-D4: assignee + due date — config, command handling, Jira fields, DMs.

These cover AC-1 (people/assignability), AC-2 (parsed command and refusals),
AC-3 (stored fields drive the Jira create), AC-4 (notice/DM) and AC-5
(onboarding confirmations in the loop).
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from figmapot_bot import onboarding
from figmapot_bot.adapters import render_notice
from figmapot_bot.config import local_date

from tests.support import FILE_A, Harness

IVAN = {"key": "ivan", "name": "Ivan Petrov", "jira_account_id": "jira-ivan"}
PETR = {"key": "petr", "name": "Petr Sidorov", "jira_account_id": "jira-petr"}
ISSUE_PATH = "/rest/api/3/issue"


def update(update_id, text, chat_id=555, chat_type="private", user_id=777):
    message = {"chat": {"id": chat_id, "type": chat_type}, "from": {"id": user_id}, "text": text}
    return {"update_id": update_id, "message": message}


def payloads(harness, kind):
    return [json.loads(job["payload"]) for job in harness.state.list_jobs() if job["kind"] == kind]


class AssigneeTestCase(unittest.TestCase):
    def harness(self, people=(IVAN,)):
        harness = Harness(files=(FILE_A,), people=list(people))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        return harness


class AssignabilityTests(AssigneeTestCase):
    """AC-1: at start, only the assignable people stay in the directory."""

    def test_non_assignable_person_is_dropped_and_logged_by_key(self):
        harness = self.harness(people=[IVAN, PETR])
        harness.jira.assignable_account_ids = {"jira-ivan"}

        harness.bot.check_assignees()

        self.assertEqual(harness.bot.directory.resolve("ivan").name, "Ivan Petrov")
        self.assertIsNone(harness.bot.directory.resolve("petr"))
        self.assertIn("petr", harness.logs())
        # The Jira account id is never written to the logs.
        self.assertNotIn("jira-petr", harness.logs())

    def test_a_failing_check_treats_the_person_as_unknown(self):
        harness = self.harness(people=[IVAN])
        harness.jira.fail_assignable = True

        harness.bot.check_assignees()

        self.assertIsNone(harness.bot.directory.resolve("ivan"))
        self.assertIn("ivan", harness.logs())

    def test_an_excluded_person_cannot_be_assigned(self):
        harness = self.harness(people=[IVAN, PETR])
        harness.jira.assignable_account_ids = {"jira-ivan"}
        harness.bot.check_assignees()
        harness.cycle()  # baseline
        harness.command_comment("thread-1", "comment-1", command="task Fix header @petr")

        harness.cycle()

        self.assertIsNone(harness.state.get_thread("thread-1"))
        self.assertEqual(harness.jira.count("POST", ISSUE_PATH), 0)
        self.assertEqual([item["content"] for item in harness.penpot.created_comments()],
                         ["Penpot: unknown person petr"])


class AssigneeCommandTests(AssigneeTestCase):
    """AC-2/AC-3/AC-4: an end-to-end owner command with an assignee and a due date."""

    def test_owner_command_creates_assigned_dated_issue_and_dms(self):
        harness = self.harness()
        harness.bot.check_assignees()
        harness.cycle()  # baseline
        harness.bind_chat("ivan", "ivan-chat")
        harness.command_comment("thread-1", "comment-1", command="task Fix header @ivan due 2026-10-25")

        harness.cycle()

        fields = harness.jira.created_issues()[0]["fields"]
        self.assertEqual(fields["assignee"], {"accountId": "jira-ivan"})
        self.assertEqual(fields["duedate"], "2026-10-25")
        self.assertEqual(harness.state.get_thread("thread-1")["state"], "linked")

        # The reserved fields are stored with the thread (AC-3).
        task = harness.state.get_thread_task("thread-1")
        self.assertEqual(
            (task["assignee_key"], task["assignee_account_id"], task["due"]),
            ("ivan", "jira-ivan", "2026-10-25"),
        )

        # A group notice with the assignee/due, then a private DM (AC-4).
        delivered = harness.telegram.delivered
        self.assertEqual([message["chat_id"] for message in delivered], ["12345", "ivan-chat"])
        self.assertIn("Ivan Petrov", delivered[0]["text"])
        self.assertIn("2026-10-25", delivered[0]["text"])
        self.assertIn("FP-1", delivered[1]["text"])
        self.assertIn("2026-10-25", delivered[1]["text"])

        # The DM job stores its recipient.
        dm_jobs = payloads(harness, "telegram_dm")
        self.assertEqual(len(dm_jobs), 1)
        self.assertEqual(dm_jobs[0]["chat_id"], "ivan-chat")
        self.assertEqual(dm_jobs[0]["notice"]["kind"], "assigned")

        # The Penpot reply names the assignee and the due date too.
        reply = harness.penpot.created_comments()[0]["content"]
        self.assertIn("Ivan Petrov", reply)
        self.assertIn("2026-10-25", reply)

    def test_assignee_without_a_binding_gets_no_dm(self):
        harness = self.harness()
        harness.cycle()  # baseline
        harness.command_comment("thread-1", "comment-1", command="task Fix header @ivan due 2026-10-25")

        harness.cycle()

        self.assertEqual(harness.jira.count("POST", ISSUE_PATH), 1)
        self.assertEqual(payloads(harness, "telegram_dm"), [])
        self.assertEqual(len(harness.telegram.delivered), 1)
        self.assertIn("no onboarding binding for ivan", harness.logs())

    def test_plain_task_keeps_todays_create_call_and_notice(self):
        harness = self.harness()
        harness.cycle()  # baseline
        harness.command_comment("thread-1", "comment-1", title="Plain header")

        harness.cycle()

        fields = harness.jira.created_issues()[0]["fields"]
        self.assertNotIn("assignee", fields)
        self.assertNotIn("duedate", fields)
        self.assertIsNone(harness.state.get_thread_task("thread-1"))
        self.assertEqual(payloads(harness, "telegram_dm"), [])
        self.assertNotIn("Assignee:", harness.telegram.delivered[0]["text"])
        notice = payloads(harness, "telegram_message")[0]["notice"]
        self.assertNotIn("assignee_name", notice)
        self.assertNotIn("due", notice)


class RefusalTests(AssigneeTestCase):
    """AC-2: a refusal marks the comment seen, creates no thread and replies once."""

    def run_command(self, harness, command):
        harness.cycle()  # baseline
        harness.command_comment("thread-1", "comment-1", command=command)
        harness.cycle()

    def assert_refused(self, harness, expected):
        self.assertIsNone(harness.state.get_thread("thread-1"))
        self.assertEqual(harness.jira.count("POST", ISSUE_PATH), 0)
        self.assertTrue(harness.state.is_seen("comment-1"))
        self.assertEqual(
            [item["content"] for item in harness.penpot.created_comments()], [expected]
        )

    def test_unknown_person_is_refused(self):
        harness = self.harness()
        self.run_command(harness, "task Fix header @nobody")
        self.assert_refused(harness, "Penpot: unknown person nobody")

    def test_invalid_due_date_is_refused(self):
        harness = self.harness()
        self.run_command(harness, "task Fix header due 2026-02-30")
        self.assert_refused(harness, "Penpot: invalid date")

    def test_due_date_in_the_past_is_refused(self):
        harness = self.harness()
        today = datetime.date.fromisoformat(local_date("Europe/Moscow", harness.clock.now()))
        past = (today - datetime.timedelta(days=1)).isoformat()
        self.run_command(harness, "task Fix header due %s" % past)
        self.assert_refused(harness, "Penpot: date in the past")

    def test_title_and_command_errors_stay_silent(self):
        harness = self.harness()
        harness.cycle()  # baseline
        harness.command_comment("thread-1", "comment-1", command="task ")
        harness.command_comment("thread-1", "comment-2", command="fix header")
        harness.cycle()

        self.assertIsNone(harness.state.get_thread("thread-1"))
        self.assertTrue(harness.state.is_seen("comment-1"))
        self.assertTrue(harness.state.is_seen("comment-2"))
        self.assertEqual(harness.penpot.created_comments(), [])


class NoticeRenderTests(unittest.TestCase):
    """AC-4: the Telegram renderer adds the assignee/due when present."""

    def test_issue_notice_without_assignee_renders_as_today(self):
        notice = {"kind": "issue_created", "issue_key": "FP-1", "title": "Fix",
                  "issue_url": "http://u", "board_url": "http://b"}
        self.assertEqual(render_notice(notice), "New figmapot issue FP-1: Fix\nhttp://u\nhttp://b")

    def test_issue_notice_shows_assignee_and_due(self):
        notice = {"kind": "issue_created", "issue_key": "FP-1", "title": "Fix",
                  "issue_url": "http://u", "board_url": "http://b",
                  "assignee_name": "Ivan Petrov", "due": "2026-10-25"}
        self.assertEqual(
            render_notice(notice),
            "New figmapot issue FP-1: Fix\nhttp://u\nhttp://b\nAssignee: Ivan Petrov, due 2026-10-25",
        )

    def test_assigned_notice_names_issue_and_due(self):
        notice = {"kind": "assigned", "issue_key": "FP-1", "title": "Fix", "due": "2026-10-25"}
        self.assertEqual(render_notice(notice), "Penpot: you are assigned FP-1: Fix, due 2026-10-25")


class OnboardingLoopTests(AssigneeTestCase):
    """AC-5: the cycle polls onboarding and confirms links / refusals in one line."""

    def test_the_initial_baseline_cycle_polls_onboarding_and_confirms(self):
        harness = self.harness()
        harness.penpot.add_thread(FILE_A, "thread-1")
        # A link is created before the first start, then the person writes.
        code = onboarding.issue_code(harness.state, "ivan", harness.clock.now())
        harness.telegram.updates = [update(1, "/start " + code, chat_id="ivan-chat")]

        harness.bot.run(once=True)  # the one and only cycle is the baseline

        self.assertTrue(harness.state.baseline_complete())
        self.assertEqual(onboarding.get_binding(harness.state, "ivan")["chat_id"], "ivan-chat")
        self.assertEqual(
            [message["chat_id"] for message in harness.telegram.delivered], ["ivan-chat"]
        )

    def test_a_bound_chat_is_confirmed_with_one_dm(self):
        harness = self.harness()
        harness.cycle()  # baseline
        code = onboarding.issue_code(harness.state, "ivan", harness.clock.now())
        harness.telegram.updates = [update(1, "/start " + code, chat_id="ivan-chat")]

        harness.cycle()

        bindings = onboarding.list_bindings(harness.state)
        self.assertEqual([b["chat_id"] for b in bindings], ["ivan-chat"])
        self.assertEqual(
            [m["text"] for m in harness.telegram.delivered if m["chat_id"] == "ivan-chat"],
            ["Penpot: this chat is now linked to ivan"],
        )

    def test_a_rejected_code_is_answered_with_the_reason(self):
        harness = self.harness()
        harness.cycle()  # baseline
        harness.telegram.updates = [update(1, "/start unknown-code", chat_id="ivan-chat")]

        harness.cycle()

        self.assertEqual(onboarding.list_bindings(harness.state), [])
        text = [m["text"] for m in harness.telegram.delivered if m["chat_id"] == "ivan-chat"][0]
        self.assertIn("unknown", text)
        self.assertIn("new link", text)

    def test_a_differently_cased_onboard_key_still_reaches_the_assignee(self):
        harness = self.harness()
        harness.cycle()  # baseline
        url = onboarding.onboard(
            harness.state, harness.bot.chat, "IVAN", harness.clock.now(),
            directory=harness.bot.directory,
        )
        code = url.rsplit("start=", 1)[1]
        harness.telegram.updates = [update(1, "/start " + code, chat_id="ivan-chat")]
        harness.cycle()  # onboarding poll -> binding under the canonical key
        harness.command_comment("thread-1", "comment-1", command="task Fix header @ivan due 2026-10-25")

        harness.cycle()  # assignment -> private DM

        self.assertEqual(
            [binding["person_key"] for binding in onboarding.list_bindings(harness.state)], ["ivan"]
        )
        dms = [message for message in harness.telegram.delivered if message["chat_id"] == "ivan-chat"]
        # The link confirmation and the assignment DM both reach the person.
        self.assertEqual(len(dms), 2)
        self.assertTrue(any("FP-1" in message["text"] for message in dms))

    def test_a_failing_onboarding_poll_does_not_block_the_baseline(self):
        harness = self.harness()
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.telegram.fail_updates = True

        with self.assertRaises(Exception):
            harness.bot.run(once=True)

        self.assertTrue(harness.state.baseline_complete())

    def test_onboarding_failure_is_a_poll_failure_and_deliveries_still_run(self):
        harness = self.harness()
        harness.cycle()  # baseline
        harness.state.add_job("telegram_message", {"text": "written before the outage"})
        harness.telegram.fail_updates = True

        with self.assertRaises(Exception):
            harness.bot.run(once=True)

        self.assertEqual(
            [m["text"] for m in harness.telegram.delivered], ["written before the outage"]
        )
        self.assertEqual(harness.state.list_jobs(["pending"]), [])


if __name__ == "__main__":
    unittest.main()
