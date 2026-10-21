"""Shared harness: fakes, fake clock, temp state db and a wired Bot."""
from __future__ import annotations

import contextlib
import http.cookiejar
import io
import json
import logging
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from figmapot_bot import people as people_module
from figmapot_bot.adapters import JiraClient, PenpotClient, TelegramClient
from figmapot_bot.bot import Bot
from figmapot_bot.clock import FakeClock
from figmapot_bot.config import load_config
from figmapot_bot.state import State

from .fakes import FakeJira, FakePenpot, FakeTelegram, build_fake_opener

OWNER_ID = "owner-uuid"
BOT_PROFILE_ID = "bot-uuid-0000"
FILE_A = "file-a"
FILE_B = "file-b"


def make_logger(stream):
    logger = logging.getLogger("figmapot_test_%d" % id(stream))
    logger.handlers = []
    logger.propagate = False
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


class Harness:
    def __init__(self, files=(FILE_A,), people=None):
        self.tmp = tempfile.mkdtemp(prefix="figmapot-test-")
        self.db_path = os.path.join(self.tmp, "state.db")
        self.env_path = os.path.join(self.tmp, "env")
        self.people_path = None
        self.clock = FakeClock()
        self.penpot = FakePenpot(profile_id=BOT_PROFILE_ID)
        self.jira = FakeJira()
        self.telegram = FakeTelegram()
        self.log_stream = io.StringIO()
        self.logger = make_logger(self.log_stream)
        self.file_ids = list(files)
        for file_id in self.file_ids:
            self.penpot.add_file(file_id)
        if people is not None:
            self._write_people(people)
        self._write_env()
        self.config = load_config(environ={"FIGMAPOT_BOT_ENV": self.env_path}, env_path=self.env_path)
        self.state = State(self.db_path, self.clock)
        self._open = True
        self.bot = self._build_bot()

    def _write_people(self, people):
        self.people_path = os.path.join(self.tmp, "people.json")
        with open(self.people_path, "w", encoding="utf-8") as handle:
            json.dump({"people": list(people)}, handle)

    def _write_env(self):
        lines = [
            "PENPOT_URL=%s" % self.penpot.url,
            "PENPOT_TEAM_ID=team-1",
            "PENPOT_BOT_EMAIL=bot@example.com",
            "PENPOT_BOT_PASSWORD=penpot-secret",
            "PENPOT_OWNER_ID=%s" % OWNER_ID,
            "PENPOT_FILE_IDS=%s" % ",".join(self.file_ids),
            "JIRA_URL=%s" % self.jira.url,
            "JIRA_EMAIL=bot@example.com",
            "JIRA_API_TOKEN=jira-secret-token",
            "JIRA_PROJECT_KEY=FP",
            "JIRA_ISSUE_TYPE=Task",
            "TELEGRAM_BOT_TOKEN=tg-secret-token",
            "TELEGRAM_CHAT_ID=12345",
            "TELEGRAM_API_BASE=%s" % self.telegram.url,
            "STATE_DB=%s" % self.db_path,
            "POLL_SECONDS=0",
        ]
        if self.people_path is not None:
            lines.append("PEOPLE_FILE=%s" % self.people_path)
        with open(self.env_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def _build_bot(self):
        fakes = [self.penpot, self.jira, self.telegram]
        design = PenpotClient(
            self.config.penpot_url, self.config.bot_email, self.config.bot_password, self.clock, self.logger,
            opener=build_fake_opener(fakes, http.cookiejar.CookieJar()),
        )
        tracker = JiraClient(
            self.config.jira_url, self.config.jira_email, self.config.jira_api_token,
            self.config.jira_project_key, self.config.jira_issue_type, self.clock, self.logger,
            opener=build_fake_opener(fakes),
        )
        chat = TelegramClient(
            self.config.telegram_bot_token, self.config.telegram_chat_id, self.clock, self.logger,
            api_base=self.telegram.url, opener=build_fake_opener(fakes),
        )
        directory = people_module.load(self.config.people_file)
        return Bot(self.config, self.clock, self.state, design, tracker, chat, self.logger,
                   directory=directory)

    def cycle(self):
        self.bot.cycle()

    def release(self):
        """Close the state db so another run can take the lock."""
        if self._open:
            self.state.close()
            self._open = False

    def reopen(self, clock=None):
        if clock is not None:
            self.clock = clock
        self.state = State(self.db_path, self.clock)
        self._open = True
        self.bot = self._build_bot()

    def restart(self):
        self.release()
        self.reopen()

    def command_comment(self, thread_id, comment_id, title="Fix the header", command=None):
        body = command if command is not None else ("task %s" % title)
        content = "@[FigmaPot Bot](%s) %s" % (BOT_PROFILE_ID, body)
        self.penpot.add_comment(thread_id, comment_id, OWNER_ID, content, [BOT_PROFILE_ID])
        return content

    def bind_chat(self, person_key, chat_id, user_id=7):
        """Bind a person's private chat the way the onboarding poll would."""
        with self.state.transaction():
            self.state.conn.execute(
                "INSERT INTO onboarding_bindings(person_key, chat_id, user_id, bound_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(person_key) DO UPDATE SET chat_id = excluded.chat_id, "
                "user_id = excluded.user_id, bound_at = excluded.bound_at",
                (person_key, chat_id, user_id, self.clock.now()),
            )

    def logs(self):
        return self.log_stream.getvalue()

    def env_environment(self):
        env = dict(os.environ)
        env["FIGMAPOT_BOT_ENV"] = self.env_path
        env["HOME"] = self.tmp
        env["PYTHONPATH"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return env

    def close(self):
        try:
            if self._open:
                self.state.close()
        finally:
            self.penpot.stop()
            self.jira.stop()
            self.telegram.stop()
            shutil.rmtree(self.tmp, ignore_errors=True)


@contextlib.contextmanager
def _fake_transport(fakes):
    """Make every opener the production code builds serve ``fakes`` in-process."""
    from figmapot_bot import adapters

    real_build_opener = adapters.build_opener
    adapters.build_opener = lambda cookiejar=None: build_fake_opener(fakes, cookiejar)
    try:
        yield
    finally:
        adapters.build_opener = real_build_opener


def run_once(harness):
    """Run the ``--once`` CLI entry point against the harness fakes, in-process.

    The sandbox forbids binding or connecting sockets, so the child is not a
    fresh process: ``figmapot_bot.__main__.main`` runs here with the same
    in-process fake transport the harness uses.  The state db the run opens is
    closed before returning so the caller can reopen its own.
    """
    import figmapot_bot.__main__ as cli

    fakes = [harness.penpot, harness.jira, harness.telegram]
    real_state = cli.State
    created = []

    class TrackedState(real_state):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    saved_environ = dict(os.environ)
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    output = io.StringIO()

    try:
        cli.State = TrackedState
        os.environ.clear()
        os.environ.update(harness.env_environment())
        root.handlers[:] = []
        with _fake_transport(fakes), contextlib.redirect_stderr(output):
            return cli.main(["--once"])
    finally:
        harness.last_run_output = output.getvalue()
        cli.State = real_state
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        for state in created:
            try:
                state.close()
            except Exception:
                pass
        os.environ.clear()
        os.environ.update(saved_environ)
