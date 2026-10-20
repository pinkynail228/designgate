"""AC-1: configuration loading, missing keys and secret hygiene."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from figmapot_bot.config import ConfigError, load_config, parse_env_file
from figmapot_bot.adapters import PenpotError

from tests.support import FILE_A, Harness

FULL_ENV = {
    "PENPOT_URL": "http://penpot.test",
    "PENPOT_TEAM_ID": "team-1",
    "PENPOT_BOT_EMAIL": "bot@example.com",
    "PENPOT_BOT_PASSWORD": "penpot-secret",
    "PENPOT_OWNER_ID": "owner-uuid",
    "PENPOT_FILE_IDS": "file-a,file-b",
    "JIRA_URL": "http://jira.test",
    "JIRA_EMAIL": "bot@example.com",
    "JIRA_API_TOKEN": "jira-secret-token",
    "JIRA_PROJECT_KEY": "FP",
    "TELEGRAM_BOT_TOKEN": "tg-secret-token",
    "TELEGRAM_CHAT_ID": "12345",
}


def write_env(tmp, values):
    path = os.path.join(tmp, "env")
    with open(path, "w", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write("%s=%s\n" % (key, value))
    return path


class ConfigTests(unittest.TestCase):
    def test_missing_key_names_it(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            values = dict(FULL_ENV)
            del values["JIRA_API_TOKEN"]
            path = write_env(tmp, values)
            with self.assertRaises(ConfigError) as caught:
                load_config(environ={"FIGMAPOT_BOT_ENV": path}, env_path=path)
            self.assertIn("JIRA_API_TOKEN", str(caught.exception))

    def test_empty_value_counts_as_missing(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            values = dict(FULL_ENV)
            values["TELEGRAM_CHAT_ID"] = ""
            path = write_env(tmp, values)
            with self.assertRaises(ConfigError) as caught:
                load_config(environ={"FIGMAPOT_BOT_ENV": path}, env_path=path)
            self.assertIn("TELEGRAM_CHAT_ID", str(caught.exception))

    def test_password_may_contain_spaces(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            values = dict(FULL_ENV)
            values["PENPOT_BOT_PASSWORD"] = "correct horse battery staple"
            path = write_env(tmp, values)
            config = load_config(environ={"FIGMAPOT_BOT_ENV": path}, env_path=path)
            self.assertEqual(config.bot_password, "correct horse battery staple")

    def test_token_with_spaces_is_rejected(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            values = dict(FULL_ENV)
            values["JIRA_API_TOKEN"] = "jira secret"
            path = write_env(tmp, values)
            with self.assertRaises(ConfigError) as caught:
                load_config(environ={"FIGMAPOT_BOT_ENV": path}, env_path=path)
            self.assertIn("JIRA_API_TOKEN", str(caught.exception))
            self.assertNotIn("jira secret", str(caught.exception))

    def test_defaults_and_file_ids(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = write_env(tmp, FULL_ENV)
            config = load_config(environ={"FIGMAPOT_BOT_ENV": path}, env_path=path)
            self.assertEqual(config.file_ids, ["file-a", "file-b"])
            self.assertEqual(config.jira_issue_type, "Task")
            self.assertEqual(config.poll_seconds, 30.0)
            self.assertTrue(config.state_db.endswith("state.db"))

    def test_env_file_comments_and_quotes(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "env")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("# a comment\n")
                handle.write('PENPOT_URL="http://penpot.test"\n')
                handle.write("\n")
            parsed = parse_env_file(path)
            self.assertEqual(parsed["PENPOT_URL"], "http://penpot.test")

    def test_secret_never_appears_in_logs(self):
        harness = Harness()
        self.addCleanup(harness.close)
        harness.penpot.add_thread(harness.file_ids[0], "thread-1")
        harness.cycle()  # baseline with no comments
        harness.command_comment("thread-1", "comment-1", title="Telegram hygiene")
        harness.cycle()
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 1)
        logs = harness.logs()
        for secret in ("tg-secret-token", "jira-secret-token", "penpot-secret"):
            self.assertNotIn(secret, logs)

    def test_penpot_error_does_not_expose_secrets(self):
        harness = Harness(files=(FILE_A,))
        self.addCleanup(harness.close)
        harness.penpot.error_hint = "penpot-secret"
        harness.penpot.fail_threads_for.add(FILE_A)
        with self.assertRaises(PenpotError) as caught:
            harness.bot.cycle()
        self.assertNotIn("penpot-secret", str(caught.exception))
        self.assertNotIn("penpot-secret", repr(caught.exception))
        self.assertNotIn("penpot-secret", harness.logs())

    def test_url_with_space_is_rejected_without_echoing_the_value(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            values = dict(FULL_ENV)
            values["PENPOT_URL"] = "http://pen pot.test"
            path = write_env(tmp, values)
            with self.assertRaises(ConfigError) as caught:
                load_config(environ={"FIGMAPOT_BOT_ENV": path}, env_path=path)
            self.assertIn("PENPOT_URL", str(caught.exception))
            self.assertNotIn("pen pot.test", str(caught.exception))

    def test_token_with_space_is_rejected_without_echoing_the_value(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            values = dict(FULL_ENV)
            values["TELEGRAM_BOT_TOKEN"] = "secret token value"
            path = write_env(tmp, values)
            with self.assertRaises(ConfigError) as caught:
                load_config(environ={"FIGMAPOT_BOT_ENV": path}, env_path=path)
            self.assertIn("TELEGRAM_BOT_TOKEN", str(caught.exception))
            self.assertNotIn("secret token value", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
