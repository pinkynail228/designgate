"""AC-2: command detection rules and the edit rule."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from figmapot_bot.commands import parse_task_command

from tests.support import BOT_PROFILE_ID, OWNER_ID, Harness

OTHER_ID = "other-uuid"
MENTION = "@[FigmaPot Bot](%s)" % BOT_PROFILE_ID


def comment(owner_id, content, mentions):
    return {"id": "c1", "ownerId": owner_id, "content": content, "mentions": list(mentions)}


class ParseTests(unittest.TestCase):
    def test_valid_command(self):
        self.assertEqual(
            parse_task_command(comment(OWNER_ID, MENTION + " task Fix the header", [BOT_PROFILE_ID]), OWNER_ID, BOT_PROFILE_ID),
            "Fix the header",
        )

    def test_case_insensitive_task_and_title_trimmed(self):
        content = MENTION + "   TaSk    Fix  the header   "
        self.assertEqual(parse_task_command(comment(OWNER_ID, content, [BOT_PROFILE_ID]), OWNER_ID, BOT_PROFILE_ID),
                         "Fix  the header")

    def test_newlines_in_title_become_spaces(self):
        content = MENTION + " task line one\nline two"
        self.assertEqual(parse_task_command(comment(OWNER_ID, content, [BOT_PROFILE_ID]), OWNER_ID, BOT_PROFILE_ID),
                         "line one line two")

    def test_bots_own_reply_is_not_a_command(self):
        content = "@[Owner](%s) task something" % OWNER_ID
        self.assertIsNone(parse_task_command(comment(BOT_PROFILE_ID, content, [OWNER_ID]), OWNER_ID, BOT_PROFILE_ID))

    def test_non_owner_comment_is_ignored(self):
        content = MENTION + " task Fix the header"
        self.assertIsNone(parse_task_command(comment(OTHER_ID, content, [BOT_PROFILE_ID]), OWNER_ID, BOT_PROFILE_ID))

    def test_mention_on_thread_but_not_on_comment(self):
        # Otherwise-valid command text, but the comment's own mentions list is
        # empty (only the thread accumulated the bot mention).
        content = MENTION + " task Fix the header"
        self.assertIsNone(parse_task_command(comment(OWNER_ID, content, []), OWNER_ID, BOT_PROFILE_ID))
        # A different mention on the comment must not satisfy the guard either.
        self.assertIsNone(
            parse_task_command(comment(OWNER_ID, content, [OTHER_ID]), OWNER_ID, BOT_PROFILE_ID)
        )

    def test_extra_text_before_mention(self):
        content = "hello " + MENTION + " task Fix the header"
        self.assertIsNone(parse_task_command(comment(OWNER_ID, content, [BOT_PROFILE_ID]), OWNER_ID, BOT_PROFILE_ID))

    def test_title_too_long_or_empty(self):
        self.assertIsNone(parse_task_command(comment(OWNER_ID, MENTION + " task " + "x" * 201, [BOT_PROFILE_ID]), OWNER_ID, BOT_PROFILE_ID))
        self.assertIsNone(parse_task_command(comment(OWNER_ID, MENTION + " task   ", [BOT_PROFILE_ID]), OWNER_ID, BOT_PROFILE_ID))
        self.assertIsNone(parse_task_command(comment(OWNER_ID, MENTION + " task", [BOT_PROFILE_ID]), OWNER_ID, BOT_PROFILE_ID))


class EditTests(unittest.TestCase):
    def test_edit_into_command_does_nothing(self):
        harness = Harness()
        self.addCleanup(harness.close)
        harness.penpot.add_thread(harness.file_ids[0], "thread-1")
        harness.penpot.add_comment("thread-1", "comment-1", OWNER_ID, "just a thought", [])
        harness.cycle()  # marks it seen, not a command
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 0)

        harness.penpot.edit_comment("thread-1", "comment-1", MENTION + " task late edit", [BOT_PROFILE_ID])
        harness.cycle()
        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 0)
        self.assertIsNone(harness.state.get_thread("thread-1"))


if __name__ == "__main__":
    unittest.main()
