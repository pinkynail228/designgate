"""AC-1..AC-4: the ``task`` command grammar, assignee ref and due date."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from figmapot_bot.grammar import ParseError, ParsedTask, parse_task

TODAY = "2026-10-21"


class KeywordTests(unittest.TestCase):
    def test_parses_title_assignee_and_due(self):
        self.assertEqual(
            parse_task("task Fix header @ivan due 2026-10-25", TODAY),
            ParsedTask(title="Fix header", assignee_ref="ivan", due="2026-10-25"),
        )

    def test_task_keyword_is_case_insensitive(self):
        self.assertEqual(
            parse_task("TaSk Fix header", TODAY),
            ParsedTask(title="Fix header", assignee_ref=None, due=None),
        )

    def test_optionals_are_case_insensitive(self):
        self.assertEqual(
            parse_task("task Fix header @Ivan До 2026-10-25", TODAY),
            ParsedTask(title="Fix header", assignee_ref="Ivan", due="2026-10-25"),
        )

    def test_plain_title_has_no_optionals(self):
        self.assertEqual(
            parse_task("task Fix header", TODAY),
            ParsedTask(title="Fix header", assignee_ref=None, due=None),
        )

    def test_text_without_task_keyword_is_a_command_error(self):
        self.assertEqual(parse_task("fix header", TODAY), ParseError("command"))


class OptionalPartTests(unittest.TestCase):
    def test_assignee_and_due_in_either_order(self):
        expected = ParsedTask(title="Fix header", assignee_ref="ivan", due="2026-10-25")
        self.assertEqual(parse_task("task Fix header @ivan due 2026-10-25", TODAY), expected)
        self.assertEqual(parse_task("task Fix header due 2026-10-25 @ivan", TODAY), expected)

    def test_russian_due_keyword(self):
        self.assertEqual(
            parse_task("task Fix header до 2026-10-25", TODAY),
            ParsedTask(title="Fix header", assignee_ref=None, due="2026-10-25"),
        )

    def test_penpot_source_id_assignee(self):
        self.assertEqual(
            parse_task("task Fix header @penpot:6f1c-abc-123", TODAY),
            ParsedTask(title="Fix header", assignee_ref="penpot:6f1c-abc-123", due=None),
        )

    def test_mention_not_at_the_end_stays_in_title(self):
        self.assertEqual(
            parse_task("task @ivan review", TODAY),
            ParsedTask(title="@ivan review", assignee_ref=None, due=None),
        )

    def test_email_like_mention_stays_in_title(self):
        self.assertEqual(
            parse_task("task email a@b.c", TODAY),
            ParsedTask(title="email a@b.c", assignee_ref=None, due=None),
        )

    def test_due_word_without_a_date_stays_in_title(self):
        self.assertEqual(
            parse_task("task fix due date", TODAY),
            ParsedTask(title="fix due date", assignee_ref=None, due=None),
        )

    def test_each_optional_part_is_consumed_at_most_once(self):
        self.assertEqual(
            parse_task("task Fix header @ivan @petr", TODAY),
            ParsedTask(title="Fix header @ivan", assignee_ref="petr", due=None),
        )
        self.assertEqual(
            parse_task("task Fix header due 2026-10-25 due 2026-11-01", TODAY),
            ParsedTask(title="Fix header due 2026-10-25", assignee_ref=None, due="2026-11-01"),
        )


class TitleTests(unittest.TestCase):
    def test_newlines_become_spaces_and_edges_are_trimmed(self):
        self.assertEqual(
            parse_task("task line one\nline two", TODAY),
            ParsedTask(title="line one line two", assignee_ref=None, due=None),
        )
        self.assertEqual(
            parse_task("task   Fix  the header   ", TODAY),
            ParsedTask(title="Fix  the header", assignee_ref=None, due=None),
        )

    def test_title_length_bounds(self):
        self.assertEqual(
            parse_task("task " + "x" * 200, TODAY),
            ParsedTask(title="x" * 200, assignee_ref=None, due=None),
        )
        self.assertEqual(parse_task("task " + "x" * 201, TODAY), ParseError("title"))
        self.assertEqual(parse_task("task", TODAY), ParseError("title"))
        self.assertEqual(parse_task("task @ivan", TODAY), ParseError("title"))


class DueTests(unittest.TestCase):
    def test_invalid_calendar_date(self):
        self.assertEqual(parse_task("task Fix header due 2026-02-30", TODAY), ParseError("due_invalid"))

    def test_date_before_today_is_rejected(self):
        self.assertEqual(parse_task("task Fix header due 2026-10-20", TODAY), ParseError("due_past"))

    def test_today_is_accepted(self):
        self.assertEqual(
            parse_task("task Fix header due 2026-10-21", TODAY),
            ParsedTask(title="Fix header", assignee_ref=None, due="2026-10-21"),
        )


if __name__ == "__main__":
    unittest.main()
