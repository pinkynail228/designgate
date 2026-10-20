"""AC-7: locking, backoff and session recovery."""
from __future__ import annotations

import email.utils
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from figmapot_bot.adapters import TelegramClient
from figmapot_bot.clock import FakeClock
from figmapot_bot.httpclient import USER_AGENT, Response, do_request, retry_after_seconds
from figmapot_bot.state import AlreadyRunning, State

from tests.support import BOT_PROFILE_ID, FILE_A, Harness, make_logger


class RobustnessTests(unittest.TestCase):
    def test_second_instance_is_refused(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        with self.assertRaises(AlreadyRunning):
            State(harness.db_path)
        harness.release()
        state = State(harness.db_path)
        state.close()

    def test_telegram_retry_after_is_scheduled_on_the_fake_clock(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()
        harness.telegram.fail_times = 1
        harness.telegram.retry_after = 7
        harness.command_comment("thread-1", "comment-1", title="Rate limited")

        harness.cycle()
        self.assertEqual(harness.telegram.delivered, [])
        jobs = harness.state.list_jobs(["pending"])
        self.assertEqual([job["kind"] for job in jobs], ["telegram_message"])
        self.assertAlmostEqual(jobs[0]["not_before"] - harness.clock.now(), 7.0, places=3)
        # The wait is scheduled, never slept through inline.
        self.assertEqual(harness.clock.sleeps, [])

        harness.clock.advance(7)
        harness.cycle()
        self.assertEqual(len(harness.telegram.delivered), 1)
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 1)

    def test_profile_is_fetched_after_login(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()
        self.assertEqual(harness.bot.bot_profile_id, BOT_PROFILE_ID)
        self.assertEqual(harness.penpot.login_count, 1)
        # The unauthenticated get-profile did not poison the cached id.
        self.assertNotEqual(harness.bot.bot_profile_id, "00000000-0000-0000-0000-000000000000")

    def test_retry_after_is_not_capped_and_read_case_insensitively(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()
        harness.command_comment("thread-1", "comment-1", title="Slow down")
        harness.jira.drop_post = True
        harness.jira.fail_search_times = 1
        harness.jira.retry_after = 120
        harness.jira.retry_after_header = "retry-after"
        harness.jira.add_issue("FP-1", labels=["figmapot", "figmapot-c-comment-1"])

        harness.cycle()
        # The full server delay is honoured, and the lowercase header is read.
        self.assertIn(120.0, harness.clock.sleeps)
        thread = harness.state.get_thread("thread-1")
        self.assertEqual(thread["state"], "linked")
        self.assertEqual(thread["issue_key"], "FP-1")

    def test_requests_send_own_user_agent(self):
        sent = []

        class Reply:
            status = 200
            headers = {}

            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class Opener:
            def open(self, request, timeout=None):
                sent.append(request.get_header("User-agent"))
                return Reply()

        do_request(Opener(), "POST", "https://relay.test/bot1:x/sendMessage", data=b"{}")
        self.assertEqual(sent, [USER_AGENT])
        self.assertNotIn("urllib", USER_AGENT.lower())

    def test_longer_of_header_and_json_retry_after_wins(self):
        body = b'{"ok": false, "parameters": {"retry_after": 60}}'
        short_header = Response(429, {"Retry-After": "1"}, body)
        self.assertEqual(retry_after_seconds(short_header), 60.0)
        long_header = Response(429, {"Retry-After": "90"}, body)
        self.assertEqual(retry_after_seconds(long_header), 90.0)

    def test_http_date_retry_after_is_honoured(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()
        harness.command_comment("thread-1", "comment-1", title="Date header")
        harness.jira.drop_post = True
        harness.jira.fail_search_times = 1
        harness.jira.retry_after_raw = email.utils.formatdate(harness.clock.now() + 90, usegmt=True)
        harness.jira.add_issue("FP-1", labels=["figmapot", "figmapot-c-comment-1"])

        harness.cycle()
        self.assertIn(90.0, harness.clock.sleeps)
        self.assertEqual(harness.state.get_thread("thread-1")["state"], "linked")

    def test_relogin_during_delivery_does_not_sleep_inline(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()
        harness.command_comment("thread-1", "comment-1", title="Expire me")
        # The session expires right after the poll, so the reply delivery hits
        # the auth error and must re-login without sleeping inline.
        harness.penpot.expire_after_get_comments = 1
        harness.penpot.fail_logins = 1000
        harness.penpot.login_retry_after = 3600

        harness.cycle()
        self.assertEqual(harness.clock.sleeps, [])
        self.assertEqual(len(harness.telegram.delivered), 1)
        pending = [job for job in harness.state.list_jobs(["pending"]) if job["kind"] == "penpot_reply"]
        self.assertEqual(len(pending), 1)
        self.assertAlmostEqual(pending[0]["not_before"] - harness.clock.now(), 3600.0, places=3)
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 1)

    def test_invalid_url_does_not_expose_the_token(self):
        import io

        token = "bad token with spaces"
        client = TelegramClient(token, "1", FakeClock(), make_logger(io.StringIO()),
                                api_base="http://127.0.0.1:1")
        with self.assertRaises(Exception) as caught:
            client.send_message("hi")
        self.assertNotIn(token, str(caught.exception))
        self.assertNotIn(token, repr(caught.exception))

    def test_expired_session_triggers_one_relogin(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.add_thread(FILE_A, "thread-1")
        harness.cycle()
        self.assertEqual(harness.penpot.login_count, 1)

        harness.penpot.expire_sessions()
        harness.cycle()
        self.assertEqual(harness.penpot.login_count, 2)
        harness.cycle()
        self.assertEqual(harness.penpot.login_count, 2)


if __name__ == "__main__":
    unittest.main()
