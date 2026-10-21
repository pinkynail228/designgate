"""One-time onboarding links: a person links their private Telegram chat.

``issue_code`` mints a single-use, URL-safe code for a person and ``link``
turns it into a ``t.me`` deep link the owner can send them. ``poll_updates``
reads one ``getUpdates`` batch and turns the ``/start <code>`` messages into
``bound``/``rebound``/``rejected`` events, storing the private chat id so later
cards can DM the person (Telegram can only message users who wrote first).

Bindings, the code use and the new ``getUpdates`` offset are written in ONE
``State.transaction`` per poll call, so a failed write can neither lose a
binding nor advance the offset past an update that was not applied.
"""
from __future__ import annotations

import json
import os
import re
import secrets

from .adapters import mask_secret
from .httpclient import RetryableError, single_request

# A code lives a week and is long enough not to be guessable.
CODE_TTL = 7 * 24 * 60 * 60
CODE_BYTES = 16
CODE_PATTERN = re.compile(r"\A[A-Za-z0-9_\-]{%d,}\Z" % CODE_BYTES)

# ``people.json`` (the person directory) is owned by another card; only its keys
# matter here. The path follows the env-file convention of ``config.py``.
DEFAULT_PEOPLE_PATH = "~/.config/figmapot-bot/people.json"

# ``onboarding_meta`` key holding the highest applied ``update_id``.
OFFSET_KEY = "telegram_offset"

START_COMMAND = "/start"


class OnboardingError(Exception):
    """A failed onboarding action the caller reports instead of retrying blindly."""


def issue_code(state, person_key, now):
    """Return a fresh single-use code, invalidating the person's previous one.

    The returned code is URL-safe and at least 16 characters long. It expires
    ``CODE_TTL`` seconds after ``now``; the person's previous unused code stops
    working, so only the most recent link can bind a chat.
    """
    code = secrets.token_urlsafe(CODE_BYTES)
    with state.transaction():
        state.conn.execute(
            "UPDATE onboarding_codes SET used_at = ? WHERE person_key = ? AND used_at IS NULL",
            (now, person_key),
        )
        state.conn.execute(
            "INSERT INTO onboarding_codes(code, person_key, created_at, expires_at, used_at) "
            "VALUES (?, ?, ?, ?, NULL)",
            (code, person_key, now, now + CODE_TTL),
        )
    return code


def link(bot_username, code):
    """The deep link that opens a private chat and sends ``/start <code>``."""
    return "https://t.me/%s?start=%s" % (bot_username.lstrip("@"), code)


def load_people(path=None):
    """The person keys in the people file, or ``None`` when it is not configured."""
    path = os.path.expanduser(path or DEFAULT_PEOPLE_PATH)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return people_keys(json.load(handle))


def people_keys(document):
    """The keys of a people document, in any of its plausible shapes.

    ``{"alice": {...}}``, ``{"people": {"alice": {...}}}``, ``["alice"]`` and
    ``{"people": [{"key": "alice"}]}`` all yield ``{"alice"}``; the exact format
    lands with the people card.
    """
    people = document.get("people", document) if isinstance(document, dict) else document
    if isinstance(people, dict):
        return set(people)
    keys = set()
    for item in people or ():
        if isinstance(item, str):
            keys.add(item)
        elif isinstance(item, dict) and item.get("key"):
            keys.add(item["key"])
    return keys


def onboard(state, telegram, person_key, now, people_loader=load_people):
    """Issue a code for ``person_key`` and return the deep link to send them.

    ``people_loader`` is called without arguments and returns the known person
    keys (or ``None`` when the people file is not configured); an unknown key is
    refused before any code is issued.
    """
    if people_loader is not None:
        people = people_loader()
        if people is not None and person_key not in people:
            raise OnboardingError("unknown person key: %s" % person_key)
    username = get_bot_username(telegram)
    code = issue_code(state, person_key, now)
    logger = getattr(telegram, "logger", None)
    if logger is not None:
        # Only the first four characters: a code is a credential.
        logger.info("onboarding: issued code %s... for person %s", code[:4], person_key)
    return link(username, code)


def poll_updates(state, telegram, now):
    """Apply one ``getUpdates`` batch and return its events.

    The batch is requested with ``offset = stored_offset + 1``, ``timeout=0``
    and ``allowed_updates=["message"]``. Every private ``/start <code>`` (or
    bare code) message with a valid unused code binds the chat and reports
    ``bound`` (``rebound`` when the person had a binding); a code that is used,
    expired or unknown, and any non-private chat, report ``rejected`` with the
    reason. Unrelated messages and non-message updates produce no event.
    """
    stored = last_update_id(state)
    updates = _call(telegram, "getUpdates", {
        "offset": stored + 1,
        "timeout": 0,
        "allowed_updates": ["message"],
    })
    if not isinstance(updates, list):
        raise OnboardingError("telegram getUpdates returned a non-list result")

    events = []
    writes = []
    used_codes = set()
    highest = stored
    for update in updates:
        if not isinstance(update, dict):
            continue
        update_id = update.get("update_id")
        if not isinstance(update_id, int):
            continue
        highest = max(highest, update_id)
        if update_id <= stored:
            # Already applied in an earlier call: never apply it twice.
            continue
        event, write = _apply_update(state, update, now, used_codes)
        if event is not None:
            events.append(event)
        if write is not None:
            writes.append(write)

    if writes or highest > stored:
        with state.transaction():
            for write in writes:
                write(state.conn)
            if highest > stored:
                _set_meta(state.conn, OFFSET_KEY, highest)
    return events


def get_bot_username(telegram):
    """The bot's ``@username`` from ``getMe``, needed for the deep link."""
    result = _call(telegram, "getMe", {})
    username = result.get("username") if isinstance(result, dict) else None
    if not username:
        raise OnboardingError("telegram getMe returned no bot username")
    return username


# -- state readers ---------------------------------------------------------
def get_code(state, code):
    row = state.conn.execute("SELECT * FROM onboarding_codes WHERE code = ?", (code,)).fetchone()
    return dict(row) if row else None


def get_binding(state, person_key):
    row = state.conn.execute(
        "SELECT * FROM onboarding_bindings WHERE person_key = ?", (person_key,)
    ).fetchone()
    return dict(row) if row else None


def list_bindings(state):
    rows = state.conn.execute("SELECT * FROM onboarding_bindings ORDER BY bound_at").fetchall()
    return [dict(row) for row in rows]


def last_update_id(state):
    """The highest ``update_id`` already applied, or ``-1`` before the first poll."""
    value = _get_meta(state.conn, OFFSET_KEY)
    return -1 if value is None else int(value)


# -- internals -------------------------------------------------------------
def _get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM onboarding_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def _set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO onboarding_meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _call(telegram, method, params):
    """POST one Bot API method through the relay; the token never leaks."""
    url = "%s/bot%s/%s" % (telegram.api_base, telegram.bot_token, method)
    body = json.dumps(params).encode("utf-8")
    try:
        response = single_request(
            telegram.opener,
            "POST",
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            timeout=telegram.timeout,
            clock=telegram.clock,
        )
    except RetryableError as error:
        raise RetryableError(
            mask_secret(str(error), telegram.bot_token), delay=error.delay, status=error.status
        ) from None
    if response.status == 409:
        raise OnboardingError(
            "telegram %s failed with 409 Conflict: a webhook is set for this bot, so "
            "getUpdates is unavailable until it is removed (deleteWebhook)" % method
        )
    if response.status >= 400:
        raise OnboardingError("telegram %s failed (status=%s)" % (method, response.status))
    try:
        payload = response.json()
    except Exception:
        raise OnboardingError("telegram %s returned an invalid body" % method) from None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        description = payload.get("description") if isinstance(payload, dict) else None
        raise OnboardingError(
            "telegram %s failed: %s"
            % (method, mask_secret(str(description or "unknown error"), telegram.bot_token))
        )
    return payload.get("result")


def _apply_update(state, update, now, used_codes):
    """Return the ``(event, write)`` pair for one update."""
    message = update.get("message")
    if not isinstance(message, dict):
        return None, None  # not a message: unrelated
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat.get("type") != "private":
        return _rejected(update, chat_id, "not private"), None
    candidate = _candidate_code(state, message.get("text"))
    if candidate is None:
        return None, None  # unrelated text
    row = get_code(state, candidate)
    if row is None:
        return _rejected(update, chat_id, "unknown"), None
    if row["used_at"] is not None or candidate in used_codes:
        return _rejected(update, chat_id, "used"), None
    if row["expires_at"] is not None and row["expires_at"] <= now:
        return _rejected(update, chat_id, "expired"), None
    person_key = row["person_key"]
    binding = {
        "person_key": person_key,
        "chat_id": chat_id,
        "user_id": (message.get("from") or {}).get("id"),
        "bound_at": now,
    }
    kind = "rebound" if get_binding(state, person_key) is not None else "bound"
    used_codes.add(candidate)
    event = {"type": kind, "update_id": update["update_id"], "binding": dict(binding)}
    return event, _bind_write(binding, candidate, now)


def _candidate_code(state, text):
    """The code a private message asks for, or ``None`` when it is unrelated.

    ``/start <code>`` and a bare code are accepted; ``/start`` without a code is
    a start attempt with an empty code, while plain chatter and messages that do
    not look like (or are not) a code are ignored.
    """
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("/"):
        command, _, rest = text.partition(" ")
        if command.split("@", 1)[0].lower() != START_COMMAND:
            return None
        return rest.strip()
    if CODE_PATTERN.match(text) or get_code(state, text) is not None:
        return text
    return None


def _bind_write(binding, code, now):
    """The binding upsert plus the code use, to run inside one transaction."""
    def write(conn):
        conn.execute(
            "INSERT INTO onboarding_bindings(person_key, chat_id, user_id, bound_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(person_key) DO UPDATE SET chat_id = excluded.chat_id, "
            "user_id = excluded.user_id, bound_at = excluded.bound_at",
            (binding["person_key"], binding["chat_id"], binding["user_id"], binding["bound_at"]),
        )
        conn.execute(
            "UPDATE onboarding_codes SET used_at = ? WHERE code = ? AND used_at IS NULL", (now, code)
        )

    return write


def _rejected(update, chat_id, reason):
    return {
        "type": "rejected",
        "reason": reason,
        "update_id": update.get("update_id"),
        "chat_id": chat_id,
    }
