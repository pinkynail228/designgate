"""Command detection plus the small value builders the bot needs.

The source adapter (see ``adapters.PenpotClient.command_text``) owns the
Penpot-specific addressing: it strips the comment's bot mention and hands the
core a neutral ``command_text``.  This module keeps the owner check; the neutral
``task`` grammar lives in ``grammar.parse_task``.
"""
from __future__ import annotations

from urllib.parse import urlencode

ZERO_UUID = "00000000-0000-0000-0000-000000000000"
MAX_TITLE = 200
MAX_PENPOT_CONTENT = 750


def owner_command(comment, owner_id, command_text):
    """Return ``command_text`` when the comment is the owner's, else ``None``.

    Only the owner's comments are commands (which also excludes the bot's own
    replies). The adapter has already stripped the Penpot addressing, so the
    returned text still carries the ``task`` keyword for ``grammar.parse_task``.
    """
    if not comment:
        return None
    if comment.get("ownerId") != owner_id:
        return None
    if command_text is None:
        return None
    return command_text


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


def reply_text(issue_key, issue_url, assignee_name=None, due=None):
    """The thread reply for a linked issue, naming the assignee/due when set."""
    text = "Created %s: %s" % (issue_key, issue_url)
    details = _details_line(assignee_name, due)
    if details:
        text += "\n" + details
    return text[:MAX_PENPOT_CONTENT]


def reject_text(reason, ref=None):
    """One line explaining why a syntactically valid command was refused."""
    if reason == "unknown_person":
        return "Penpot: unknown person %s" % ref
    if reason == "due_invalid":
        return "Penpot: invalid date"
    if reason == "due_past":
        return "Penpot: date in the past"
    return "Penpot: task rejected (%s)" % reason


def _details_line(assignee_name, due):
    parts = []
    if assignee_name:
        parts.append("Assignee: %s" % assignee_name)
    if due:
        parts.append("due %s" % due)
    return ", ".join(parts)
