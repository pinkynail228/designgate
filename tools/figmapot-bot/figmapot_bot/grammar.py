"""Parse the ``task`` command text into a title, an assignee and a due date.

The source adapter strips the bot addressing before the command reaches this
module, so ``text`` still carries the leading ``task`` keyword (matched
case-insensitively, like ``commands.parse_task_command``). Nothing here reads a
clock: ``today`` is passed in as an ISO date string (a ``datetime.date`` is
also accepted).
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from typing import Optional

from .commands import MAX_TITLE

# Assignee reference: a people key (``ivan``) or ``<source>:<id>``
# (``penpot:<uuid>``). Matched only as the last token, so ``email a@b.c`` and a
# mid-text ``@ivan review`` stay in the title.
_ASSIGNEE = re.compile(r"(?:\A|\s)@([a-z0-9_.-]+(?::[a-z0-9_.-]+)?)\s*\Z", re.IGNORECASE)
# Due part: ``due 2026-10-25`` (or the Russian ``до``), also only at the end.
_DUE = re.compile(r"(?:\A|\s)(?:due|до)\s+(\d{4}-\d{2}-\d{2})\s*\Z", re.IGNORECASE)
# The whole command: the ``task`` keyword and an optional title/optionals body.
_COMMAND = re.compile(r"\A\s*task(?:\s+(?P<body>.*))?\Z", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class ParsedTask:
    """A well-formed ``task`` command."""

    title: str
    assignee_ref: Optional[str] = None
    due: Optional[str] = None


@dataclass(frozen=True)
class ParseError:
    """A rejection; ``reason`` is ``command``, ``title``, ``due_invalid`` or ``due_past``."""

    reason: str


def parse_task(text, today):
    """Parse ``text`` against the passed-in ``today``.

    Returns a :class:`ParsedTask` on success or a :class:`ParseError` otherwise.
    The trailing ``@<ref>`` and ``due <date>`` parts are optional, may appear in
    either order, only at the end, and each at most once; everything else is the
    title.
    """
    if not isinstance(text, str):
        return ParseError("command")
    match = _COMMAND.match(text)
    if not match:
        return ParseError("command")
    body = match.group("body") or ""
    assignee_ref = None
    due = None
    while True:
        if due is None:
            found = _DUE.search(body)
            if found:
                due = found.group(1)
                body = body[: found.start()]
                continue
        if assignee_ref is None:
            found = _ASSIGNEE.search(body)
            if found:
                assignee_ref = found.group(1)
                body = body[: found.start()]
                continue
        break
    title = body.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()
    if not 1 <= len(title) <= MAX_TITLE:
        return ParseError("title")
    if due is not None:
        try:
            due_date = datetime.date.fromisoformat(due)
        except ValueError:
            return ParseError("due_invalid")
        if due_date < _as_date(today):
            return ParseError("due_past")
    return ParsedTask(title=title, assignee_ref=assignee_ref, due=due)


def _as_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value))
