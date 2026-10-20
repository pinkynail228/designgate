"""Shared harness: fakes, fake clock, temp state db and a wired Bot."""
from __future__ import annotations

import io
import logging
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from figmapot_bot.adapters import JiraClient, PenpotClient, TelegramClient
from figmapot_bot.bot import Bot
from figmapot_bot.clock import FakeClock
from figmapot_bot.config import load_config
from figmapot_bot.state import State

from .fakes import FakeJira, FakePenpot, FakeTelegram

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
    def __init__(self, files=(FILE_A,)):
        self.tmp = tempfile.mkdtemp(prefix="figmapot-test-")
        self.db_path = os.path.join(self.tmp, "state.db")
        self.env_path = os.path.join(self.tmp, "env")
        self.clock = FakeClock()
        self.penpot = FakePenpot(profile_id=BOT_PROFILE_ID)
        self.jira = FakeJira()
        self.telegram = FakeTelegram()
        self.log_stream = io.StringIO()
        self.logger = make_logger(self.log_stream)
        self.file_ids = list(files)
        for file_id in self.file_ids:
            self.penpot.add_file(file_id)
        self._write_env()
        self.config = load_config(environ={"FIGMAPOT_BOT_ENV": self.env_path}, env_path=self.env_path)
        self.state = State(self.db_path, self.clock)
        self._open = True
        self.bot = self._build_bot()

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
        with open(self.env_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def _build_bot(self):
        design = PenpotClient(
            self.config.penpot_url, self.config.bot_email, self.config.bot_password, self.clock, self.logger
        )
        tracker = JiraClient(
            self.config.jira_url, self.config.jira_email, self.config.jira_api_token,
            self.config.jira_project_key, self.config.jira_issue_type, self.clock, self.logger,
        )
        chat = TelegramClient(
            self.config.telegram_bot_token, self.config.telegram_chat_id, self.clock, self.logger,
            api_base=self.telegram.url,
        )
        return Bot(self.config, self.clock, self.state, design, tracker, chat, self.logger)

    def cycle(self):
        self.bot.cycle()

    def release(self):
        """Close the state db so a subprocess can take the lock."""
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

    def command_comment(self, thread_id, comment_id, title="Fix the header"):
        content = "@[FigmaPot Bot](%s) task %s" % (BOT_PROFILE_ID, title)
        self.penpot.add_comment(thread_id, comment_id, OWNER_ID, content, [BOT_PROFILE_ID])
        return content

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
