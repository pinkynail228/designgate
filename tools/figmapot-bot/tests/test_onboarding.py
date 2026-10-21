"""BOT-D3: one-time onboarding codes and private chat bindings."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from figmapot_bot import __main__ as cli
from figmapot_bot import state as state_module
from figmapot_bot.adapters import TelegramClient
from figmapot_bot.clock import FakeClock
from figmapot_bot.httpclient import RetryableError
from figmapot_bot.onboarding import (
    CODE_TTL,
    OnboardingError,
    get_binding,
    get_code,
    issue_code,
    last_update_id,
    link,
    list_bindings,
    onboard,
    poll_updates,
)
from figmapot_bot.state import State

from tests.support import make_logger

TOKEN = "123456:telegram-secret"
API_BASE = "https://relay.test"


class Reply:
    """The minimal HTTP response the opener helpers read."""

    def __init__(self, status, body, headers=None):
        self.status = status
        self.body = body
        self.headers = dict(headers or {})

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """An in-file replacement for urllib: replays queued replies, no sockets."""

    def __init__(self, replies=()):
        self.replies = list(replies)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        if not self.replies:
            raise AssertionError("unexpected request: %s" % request.full_url)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def json_reply(status, payload, headers=None):
    body = json.dumps(payload).encode("utf-8")
    merged = {"Content-Type": "application/json"}
    merged.update(headers or {})
    return Reply(status, body, merged)


def update(update_id, text, chat_id=555, chat_type="private", user_id=777):
    message = {"chat": {"id": chat_id, "type": chat_type}, "from": {"id": user_id}}
    if text is not None:
        message["text"] = text
    return {"update_id": update_id, "message": message}


def ok(updates):
    return json_reply(200, {"ok": True, "result": list(updates)})


def close_state(state):
    if getattr(state, "_closed_by_test", False):
        return
    state._closed_by_test = True
    state.close()


class OnboardingTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="figmapot-onboarding-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db_path = os.path.join(self.tmp, "state.db")
        self.clock = FakeClock()
        self.log_stream = io.StringIO()
        self.logger = make_logger(self.log_stream)
        self.state = self.open_state(self.db_path)

    def open_state(self, path):
        state = State(path, self.clock)
        self.addCleanup(close_state, state)
        return state

    def client(self, replies=()):
        opener = FakeOpener(replies)
        client = TelegramClient(TOKEN, 4242, self.clock, self.logger, api_base=API_BASE)
        client.opener = opener
        return client, opener

    def bind(self, person_key, code=None, update_id=1, chat_id=555):
        """A poll that binds ``person_key``; returns the issued code."""
        code = code or issue_code(self.state, person_key, self.clock.now())
        client, _ = self.client([ok([update(update_id, "/start " + code, chat_id=chat_id)])])
        poll_updates(self.state, client, self.clock.now())
        return code


class IssueCodeTests(OnboardingTestCase):
    def test_code_is_url_safe_random_and_at_least_sixteen_chars(self):
        now = self.clock.now()
        first = issue_code(self.state, "alice", now)
        second = issue_code(self.state, "alice", now)
        self.assertRegex(first, r"\A[A-Za-z0-9_\-]{16,}\Z")
        self.assertGreaterEqual(len(first), 16)
        self.assertNotEqual(first, second)
        self.assertEqual(get_code(self.state, first)["person_key"], "alice")

    def test_code_expires_after_seven_days(self):
        now = self.clock.now()
        row = get_code(self.state, issue_code(self.state, "alice", now))
        self.assertEqual(row["expires_at"], now + CODE_TTL)
        self.assertEqual(CODE_TTL, 7 * 24 * 60 * 60)
        self.assertIsNone(row["used_at"])

    def test_a_new_code_invalidates_the_previous_unused_one(self):
        now = self.clock.now()
        first = issue_code(self.state, "alice", now)
        second = issue_code(self.state, "alice", now)
        self.assertIsNotNone(get_code(self.state, first)["used_at"])
        self.assertIsNone(get_code(self.state, second)["used_at"])
        # Another person's code is untouched.
        other = issue_code(self.state, "bob", now)
        issue_code(self.state, "alice", now)
        self.assertIsNone(get_code(self.state, other)["used_at"])

    def test_link_is_a_start_deep_link(self):
        self.assertEqual(link("figmapot_bot", "abc123"), "https://t.me/figmapot_bot?start=abc123")
        self.assertEqual(link("@figmapot_bot", "abc123"), "https://t.me/figmapot_bot?start=abc123")


class PollUpdatesTests(OnboardingTestCase):
    def test_poll_calls_get_updates_once_with_the_documented_payload(self):
        client, opener = self.client([ok([])])
        self.assertEqual(poll_updates(self.state, client, self.clock.now()), [])
        self.assertEqual(len(opener.requests), 1)
        request = opener.requests[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, "%s/bot%s/getUpdates" % (API_BASE, TOKEN))
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {"offset": last_update_id(self.state) + 1, "timeout": 0, "allowed_updates": ["message"]},
        )
        # With nothing confirmed yet the offset is 0, i.e. Telegram's
        # "earliest unconfirmed update" (stored_offset plus one).
        self.assertEqual(json.loads(request.data.decode("utf-8"))["offset"], 0)
        self.assertEqual(last_update_id(self.state), -1)

    def test_the_next_poll_asks_for_the_offset_after_the_highest_update(self):
        client, _ = self.client([ok([update(20, "hello"), update(31, "hi")])])
        poll_updates(self.state, client, self.clock.now())
        self.assertEqual(last_update_id(self.state), 31)
        client, opener = self.client([ok([])])
        poll_updates(self.state, client, self.clock.now())
        self.assertEqual(json.loads(opener.requests[0].data.decode("utf-8"))["offset"], 32)

    def test_start_with_a_valid_code_binds_the_private_chat(self):
        now = self.clock.now()
        code = issue_code(self.state, "alice", now)
        client, _ = self.client([ok([update(7, "/start " + code, chat_id=99, user_id=5)])])
        events = poll_updates(self.state, client, now)
        self.assertEqual([event["type"] for event in events], ["bound"])
        self.assertEqual(
            events[0]["binding"],
            {"person_key": "alice", "chat_id": 99, "user_id": 5, "bound_at": now},
        )
        binding = get_binding(self.state, "alice")
        self.assertEqual((binding["chat_id"], binding["user_id"], binding["bound_at"]), (99, 5, now))
        self.assertEqual(get_code(self.state, code)["used_at"], now)

    def test_a_bare_code_is_accepted(self):
        code = issue_code(self.state, "alice", self.clock.now())
        client, _ = self.client([ok([update(1, code)])])
        events = poll_updates(self.state, client, self.clock.now())
        self.assertEqual([event["type"] for event in events], ["bound"])
        self.assertEqual(get_binding(self.state, "alice")["chat_id"], 555)

    def test_a_code_may_carry_the_bot_username(self):
        code = issue_code(self.state, "alice", self.clock.now())
        client, _ = self.client([ok([update(1, "/start@figmapot_bot " + code)])])
        events = poll_updates(self.state, client, self.clock.now())
        self.assertEqual([event["type"] for event in events], ["bound"])

    def test_rejections_name_the_reason(self):
        now = self.clock.now()
        used = self.bind("alice", update_id=1)
        expired = issue_code(self.state, "bob", now)
        self.clock.advance(CODE_TTL + 1)
        client, _ = self.client([ok([
            update(2, "/start " + used),
            update(3, "/start " + expired),
            update(4, "/start " + "z" * 22),
            update(5, "/start " + "z" * 22, chat_type="group"),
            update(6, "/start " + "z" * 22, chat_id=-100500, chat_type="supergroup"),
        ])])
        events = poll_updates(self.state, client, self.clock.now())
        self.assertEqual(
            [(event["type"], event["reason"]) for event in events],
            [
                ("rejected", "used"),
                ("rejected", "expired"),
                ("rejected", "unknown"),
                ("rejected", "not private"),
                ("rejected", "not private"),
            ],
        )
        self.assertIsNone(get_binding(self.state, "bob"))

    def test_an_expired_code_does_not_bind(self):
        code = issue_code(self.state, "alice", self.clock.now())
        self.clock.advance(CODE_TTL)
        client, _ = self.client([ok([update(1, "/start " + code)])])
        events = poll_updates(self.state, client, self.clock.now())
        self.assertEqual(events[0]["reason"], "expired")
        self.assertIsNone(get_binding(self.state, "alice"))

    def test_unrelated_messages_produce_no_event(self):
        client, _ = self.client([ok([
            update(1, "hello there"),
            update(2, "thanks!"),
            update(3, "/help"),
            {"update_id": 4},
            {"update_id": 5, "edited_message": {"chat": {"id": 555, "type": "private"}}},
            update(6, None),
        ])])
        self.assertEqual(poll_updates(self.state, client, self.clock.now()), [])
        self.assertEqual(last_update_id(self.state), 6)

    def test_start_without_a_code_is_rejected_as_unknown(self):
        client, _ = self.client([ok([update(1, "/start"), update(2, "/start ")])])
        events = poll_updates(self.state, client, self.clock.now())
        self.assertEqual([event["reason"] for event in events], ["unknown", "unknown"])

    def test_a_single_poll_binds_every_valid_message_in_its_batch(self):
        now = self.clock.now()
        first = issue_code(self.state, "alice", now)
        second = issue_code(self.state, "bob", now)
        client, _ = self.client([ok([
            update(1, "/start " + first, chat_id=1, user_id=10),
            update(2, "/start " + second, chat_id=2, user_id=20),
        ])])
        events = poll_updates(self.state, client, now)
        self.assertEqual([event["type"] for event in events], ["bound", "bound"])
        self.assertEqual([binding["person_key"] for binding in list_bindings(self.state)], ["alice", "bob"])
        self.assertEqual(get_binding(self.state, "bob")["chat_id"], 2)

    def test_a_code_is_single_use_within_one_batch(self):
        code = issue_code(self.state, "alice", self.clock.now())
        client, _ = self.client([ok([update(1, "/start " + code), update(2, "/start " + code)])])
        events = poll_updates(self.state, client, self.clock.now())
        self.assertEqual([event["type"] for event in events], ["bound", "rejected"])
        self.assertEqual(events[1]["reason"], "used")
        self.assertEqual(len(list_bindings(self.state)), 1)


class AtomicityTests(OnboardingTestCase):
    def test_a_failed_write_leaves_the_offset_and_the_binding_untouched(self):
        code = issue_code(self.state, "alice", self.clock.now())
        self.state.conn.execute(
            "CREATE TRIGGER fail_binding BEFORE INSERT ON onboarding_bindings "
            "BEGIN SELECT RAISE(ABORT, 'disk full'); END"
        )
        client, _ = self.client([ok([update(11, "/start " + code)])])
        with self.assertRaises(sqlite3.IntegrityError):
            poll_updates(self.state, client, self.clock.now())
        # The whole call rolled back: neither the offset nor the code use survived.
        self.assertEqual(last_update_id(self.state), -1)
        self.assertIsNone(get_binding(self.state, "alice"))
        self.assertIsNone(get_code(self.state, code)["used_at"])

        # Telegram redelivers the update; once the write works it is applied once.
        self.state.conn.execute("DROP TRIGGER fail_binding")
        client, _ = self.client([ok([update(11, "/start " + code)])])
        events = poll_updates(self.state, client, self.clock.now())
        self.assertEqual([event["type"] for event in events], ["bound"])
        self.assertEqual(last_update_id(self.state), 11)
        self.assertEqual(get_binding(self.state, "alice")["chat_id"], 555)

    def test_an_already_applied_update_is_never_applied_again(self):
        code = self.bind("alice", update_id=4)
        bound_at = get_binding(self.state, "alice")["bound_at"]

        # Telegram (or a replayed batch) hands the same update id back.
        client, _ = self.client([ok([update(4, "/start " + code), update(2, "/start " + code)])])
        self.assertEqual(poll_updates(self.state, client, self.clock.now()), [])
        self.assertEqual(last_update_id(self.state), 4)
        self.assertEqual(get_binding(self.state, "alice")["bound_at"], bound_at)
        self.assertEqual(len(list_bindings(self.state)), 1)

    def test_offset_advances_past_ignored_updates(self):
        client, _ = self.client([ok([update(3, "hello"), update(8, "hi")])])
        self.assertEqual(poll_updates(self.state, client, self.clock.now()), [])
        self.assertEqual(last_update_id(self.state), 8)

    def test_a_failed_get_updates_writes_nothing(self):
        client, _ = self.client([json_reply(500, {"ok": False})])
        with self.assertRaises(RetryableError):
            poll_updates(self.state, client, self.clock.now())
        self.assertEqual(last_update_id(self.state), -1)


class RebindTests(OnboardingTestCase):
    def test_a_second_bind_replaces_the_binding_and_reports_rebound(self):
        now = self.clock.now()
        self.bind("alice", update_id=1, chat_id=111)
        second = issue_code(self.state, "alice", now)
        client, _ = self.client([ok([update(2, "/start " + second, chat_id=222, user_id=9)])])
        events = poll_updates(self.state, client, now)
        self.assertEqual([event["type"] for event in events], ["rebound"])
        self.assertEqual(events[0]["binding"]["chat_id"], 222)
        bindings = list_bindings(self.state)
        self.assertEqual(len(bindings), 1)
        self.assertEqual((bindings[0]["chat_id"], bindings[0]["user_id"]), (222, 9))


def write_env(directory, db_path, people_path=None):
    path = os.path.join(directory, "env")
    lines = [
        "PENPOT_URL=http://penpot.test",
        "PENPOT_TEAM_ID=team-1",
        "PENPOT_BOT_EMAIL=bot@example.com",
        "PENPOT_BOT_PASSWORD=penpot-secret",
        "PENPOT_OWNER_ID=owner-1",
        "PENPOT_FILE_IDS=file-a",
        "JIRA_URL=http://jira.test",
        "JIRA_EMAIL=bot@example.com",
        "JIRA_API_TOKEN=jira-secret",
        "JIRA_PROJECT_KEY=FP",
        "TELEGRAM_BOT_TOKEN=%s" % TOKEN,
        "TELEGRAM_CHAT_ID=12345",
        "TELEGRAM_API_BASE=%s" % API_BASE,
        "STATE_DB=%s" % db_path,
        "POLL_SECONDS=0",
    ]
    if people_path is not None:
        lines.append("PEOPLE_FILE=%s" % people_path)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return path


class _StubBot:
    def run(self, once=False):
        self.once = once


def write_people(path, *entries):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"people": list(entries)}, handle)
    return path


ALICE = {"key": "alice", "name": "Alice", "jira_account_id": "jira-alice"}


class CliTests(OnboardingTestCase):
    def setUp(self):
        super().setUp()
        # The CLI opens its own state file, so the harness keeps its own lock.
        self.cli_db_path = os.path.join(self.tmp, "cli-state.db")
        self.people_path = write_people(os.path.join(self.tmp, "people.json"), ALICE)
        self.env_path = write_env(self.tmp, self.cli_db_path, self.people_path)

    def run_cli(self, argv, replies):
        opener = FakeOpener(replies)
        chat = TelegramClient(TOKEN, 4242, self.clock, self.logger, api_base=API_BASE)
        chat.opener = opener
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"FIGMAPOT_BOT_ENV": self.env_path}):
            with mock.patch.object(cli, "TelegramClient", lambda *args, **kwargs: chat):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    status = cli.main(argv)
        return status, out.getvalue(), err.getvalue(), opener

    def issued_codes(self):
        # Read the file directly: the CLI owns the state lock while it runs.
        conn = sqlite3.connect(self.cli_db_path)
        try:
            rows = conn.execute("SELECT code FROM onboarding_codes ORDER BY created_at").fetchall()
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()
        return [row[0] for row in rows]

    def test_onboard_prints_the_deep_link_and_exits(self):
        status, out, err, opener = self.run_cli(
            ["onboard", "alice"], [json_reply(200, {"ok": True, "result": {"username": "figmapot_bot"}})]
        )
        self.assertEqual(status, 0)
        self.assertEqual(opener.requests[0].full_url, "%s/bot%s/getMe" % (API_BASE, TOKEN))
        self.assertEqual(opener.requests[0].get_method(), "POST")
        codes = self.issued_codes()
        self.assertEqual(len(codes), 1)
        self.assertEqual(out.strip(), link("figmapot_bot", codes[0]))
        self.assertNotIn(TOKEN, out)
        self.assertNotIn(TOKEN, err)
        # The state lock is released again on exit: the bot can start.
        self.open_state(self.cli_db_path)

    def test_onboard_refuses_an_unknown_person_key(self):
        status, out, err, opener = self.run_cli(["onboard", "bob"], [])
        self.assertNotEqual(status, 0)
        self.assertEqual(out, "")
        self.assertIn("bob", err)
        self.assertEqual(opener.requests, [])
        self.assertEqual(self.issued_codes(), [])

    def test_onboard_accepts_a_known_person_key(self):
        status, out, _, _ = self.run_cli(
            ["onboard", "alice"], [json_reply(200, {"ok": True, "result": {"username": "figmapot_bot"}})]
        )
        self.assertEqual(status, 0)
        self.assertTrue(out.startswith("https://t.me/figmapot_bot?start="))

    def test_a_broken_people_file_stops_the_start(self):
        broken = os.path.join(self.tmp, "broken-people.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        write_env(self.tmp, self.cli_db_path, broken)
        status, out, err, opener = self.run_cli(["--once"], [])
        self.assertEqual(status, 2)
        self.assertEqual(out, "")
        self.assertIn("broken-people.json", err)
        self.assertIn("not valid JSON", err)
        self.assertEqual(opener.requests, [])

    def test_onboard_refuses_while_the_bot_holds_the_lock(self):
        self.open_state(self.cli_db_path)  # the running bot holds the lock
        status, out, err, _ = self.run_cli(["onboard", "alice"], [])
        self.assertEqual(status, 3)
        self.assertEqual(out, "")
        self.assertTrue(err.strip())

    def test_a_failed_get_me_leaves_no_code_behind(self):
        status, out, err, opener = self.run_cli(
            ["onboard", "alice"], [json_reply(401, {"ok": False, "description": "Unauthorized"})]
        )
        self.assertEqual(status, 4)
        self.assertEqual(out, "")
        self.assertEqual(self.issued_codes(), [])
        self.assertNotIn(TOKEN, err)

    def test_the_plain_bot_entry_point_is_unchanged(self):
        with mock.patch.dict(os.environ, {"FIGMAPOT_BOT_ENV": self.env_path}):
            with mock.patch.object(cli, "build_bot", lambda config, logger: _StubBot()):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    status = cli.main(["--once"])
        self.assertEqual(status, 0)
        self.assertEqual(out.getvalue(), "")


class SecretsTests(OnboardingTestCase):
    def test_the_token_never_reaches_logs_or_errors(self):
        client, _ = self.client([json_reply(401, {"ok": False, "description": "Unauthorized"})])
        with self.assertRaises(OnboardingError) as raised:
            onboard(self.state, client, "alice", self.clock.now())
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertNotIn(TOKEN, self.log_stream.getvalue())

    def test_a_code_is_logged_only_by_its_first_four_characters(self):
        client, _ = self.client([json_reply(200, {"ok": True, "result": {"username": "figmapot_bot"}})])
        url = onboard(self.state, client, "alice", self.clock.now())
        code = self.issued_codes()[0]
        self.assertEqual(url, link("figmapot_bot", code))
        logs = self.log_stream.getvalue()
        self.assertIn(code[:4], logs)
        self.assertNotIn(code, logs)
        self.assertNotIn(TOKEN, logs)
        self.assertNotIn(TOKEN, url)

    def issued_codes(self):
        return [
            row["code"]
            for row in self.state.conn.execute("SELECT code FROM onboarding_codes ORDER BY created_at")
        ]


class TelegramErrorTests(OnboardingTestCase):
    def test_webhook_conflict_raises_a_clear_error_naming_the_cause(self):
        client, _ = self.client([json_reply(409, {
            "ok": False,
            "description": "Conflict: can't use getUpdates method while webhook is active",
        })])
        with self.assertRaises(OnboardingError) as raised:
            poll_updates(self.state, client, self.clock.now())
        message = str(raised.exception)
        self.assertIn("409", message)
        self.assertIn("webhook", message.lower())
        self.assertNotIn(TOKEN, message)
        self.assertEqual(last_update_id(self.state), -1)

    def test_a_webhook_conflict_does_not_count_as_success(self):
        client, _ = self.client([json_reply(409, {"ok": False, "description": "Conflict"})])
        with self.assertRaises(OnboardingError):
            onboard(self.state, client, "alice", self.clock.now())

    def test_rate_limit_uses_the_existing_retry_after_handling(self):
        client, _ = self.client([json_reply(429, {"ok": False, "parameters": {"retry_after": 13}})])
        with self.assertRaises(RetryableError) as raised:
            poll_updates(self.state, client, self.clock.now())
        self.assertEqual(raised.exception.delay, 13.0)
        self.assertEqual(raised.exception.status, 429)
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertEqual(last_update_id(self.state), -1)

    def test_a_non_ok_body_is_an_error_not_an_empty_batch(self):
        client, _ = self.client([json_reply(200, {"ok": False, "description": "Unauthorized"})])
        with self.assertRaises(OnboardingError) as raised:
            poll_updates(self.state, client, self.clock.now())
        self.assertNotIn(TOKEN, str(raised.exception))


class SchemaUpgradeTests(unittest.TestCase):
    def test_an_existing_database_upgrades_in_place_without_data_loss(self):
        tmp = tempfile.mkdtemp(prefix="figmapot-upgrade-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        db_path = os.path.join(tmp, "state.db")
        # The schema as it is before this card: the onboarding tables removed.
        legacy = ";".join(
            statement for statement in state_module.SCHEMA.split(";") if "onboarding_" not in statement
        )
        conn = sqlite3.connect(db_path)
        conn.executescript(legacy)
        conn.execute("INSERT INTO meta(key, value) VALUES ('baseline_complete', '1')")
        conn.execute("INSERT INTO seen(comment_id, seen_at) VALUES ('comment-1', 10.0)")
        conn.execute(
            "INSERT INTO threads(thread_id, state, title, updated_at) VALUES ('t-1', 'linked', 'Fix', 5.0)"
        )
        conn.execute("INSERT INTO jobs(kind, payload, state) VALUES ('telegram_message', '{}', 'pending')")
        conn.commit()
        conn.close()

        state = State(db_path, FakeClock())
        self.addCleanup(close_state, state)
        # The old rows are intact and the old tables keep working.
        self.assertTrue(state.baseline_complete())
        self.assertEqual(state.seen_count(), 1)
        self.assertEqual(state.get_thread("t-1")["title"], "Fix")
        self.assertEqual(len(state.list_jobs(["pending"])), 1)
        # The new tables exist and are usable in the upgraded database.
        code = issue_code(state, "alice", 20.0)
        self.assertEqual(get_code(state, code)["expires_at"], 20.0 + CODE_TTL)
        self.assertEqual(last_update_id(state), -1)


if __name__ == "__main__":
    unittest.main()
