"""Command detection plus the small value builders the bot needs.

The source adapter (see ``adapters.PenpotClient.command_text``) owns the
Penpot-specific addressing: it strips the comment's bot mention and hands the
core a neutral ``command_text``.  This module keeps the owner check and the
neutral ``task <title>`` grammar.
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

ZERO_UUID = "00000000-0000-0000-0000-000000000000"
MAX_TITLE = 200
MAX_PENPOT_CONTENT = 750


def parse_task_command(comment, owner_id, command_text):
    """Return the task title when ``comment`` is a valid ``task`` command.

    The adapter has already stripped the Penpot addressing, so the whole
    ``command_text`` must read ``task`` (any case), whitespace and a 1-200 char
    title.  Everything must hold: the comment belongs to the owner (which also
    excludes the bot's own replies) and the grammar matches.
    """
    if not comment:
        return None
    if comment.get("ownerId") != owner_id:
        return None
    if command_text is None:
        return None
    match = re.match(r"\Atask\s+(?P<title>.*)\Z", command_text, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    title = match.group("title").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    title = title.strip()
    if not 1 <= len(title) <= MAX_TITLE:
        return None
    return title


def build_board_link(base_url, team_id, file_id, page_id, frame_id):
    params = [("team-id", team_id), ("file-id", file_id), ("page-id", page_id)]
    if frame_id and frame_id != ZERO_UUID:
        params.append(("board-id", frame_id))
    return base_url.rstrip("/") + "/#/workspace?" + urlencode(params)


def build_thread_link(board_link, thread_id):
    return board_link + "&comment-id=" + thread_id


def build_issue_description(comment_text, board_link, thread_link):
    """Jira ADF: the comment text plus the board and thread links."""
    content = [_paragraph(comment_text or "")]
    if board_link:
        content.append(_link_paragraph("Board", board_link))
    if thread_link:
        content.append(_link_paragraph("Thread", thread_link))
    return {"type": "doc", "version": 1, "content": content}


def _paragraph(text):
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _link_paragraph(text, url):
    node = {"type": "text", "text": text, "marks": [{"type": "link", "attrs": {"href": url}}]}
    return {"type": "paragraph", "content": [node]}


def build_issue_labels(comment_id):
    return ["figmapot", "figmapot-c-" + comment_id]


def reply_text(issue_key, issue_url):
    return ("Created %s: %s" % (issue_key, issue_url))[:MAX_PENPOT_CONTENT]
