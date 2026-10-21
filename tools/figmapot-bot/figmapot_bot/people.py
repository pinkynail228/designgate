"""Load and query the owner-edited ``people.json`` directory.

The file maps the short names typed in comments (``@ivan``) to the Jira account
and chat ids the bot needs. Loading is strict, because a silently wrong mapping
would route work to the wrong person: every problem raises :class:`PeopleError`
naming the offending entry (by key, or by position when the key is missing)
without echoing any other value. A missing file is not an error: it yields an
empty directory and the feature stays off.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Person:
    key: str
    name: str
    jira_account_id: str
    penpot_profile_id: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    owner: bool = False


class PeopleError(Exception):
    """Raised when ``people.json`` cannot be trusted."""


class Directory:
    """Resolved people, queryable by key or by ``penpot:<id>``."""

    def __init__(self, people):
        self._by_key = {}
        self._by_penpot = {}
        self._owner = None
        for person in people:
            self._by_key[person.key.lower()] = person
            if person.penpot_profile_id:
                self._by_penpot[person.penpot_profile_id] = person
            if person.owner:
                self._owner = person

    def __len__(self):
        return len(self._by_key)

    def resolve(self, ref):
        """Return the person named ``ref`` or ``None``.

        ``ref`` is a people key (case-insensitive) or ``penpot:<id>`` matched
        against ``penpot_profile_id``. Any other ``<source>:<id>`` is unknown.
        """
        if not ref:
            return None
        source, sep, ident = ref.partition(":")
        if sep:
            if source.lower() != "penpot":
                return None
            return self._by_penpot.get(ident)
        return self._by_key.get(ref.lower())

    def owner(self):
        """Return the person marked as owner, or ``None``."""
        return self._owner


def load(path):
    """Load the directory from ``path``; a missing file yields an empty one."""
    if not path or not os.path.exists(path):
        return Directory([])
    with open(path, "r", encoding="utf-8") as handle:
        try:
            data = json.load(handle)
        except ValueError as error:
            raise PeopleError("%s: not valid JSON (%s)" % (path, error))
    if not isinstance(data, dict):
        raise PeopleError("%s: top level must be an object" % path)
    entries = data.get("people")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise PeopleError('%s: "people" must be a list' % path)
    return Directory(_parse_entries(entries))


def _parse_entries(entries):
    people = []
    seen_keys = {}
    seen_jira = {}
    seen_penpot = {}
    owner_key = None
    for index, entry in enumerate(entries):
        position = "person %d" % (index + 1)
        if not isinstance(entry, dict):
            raise PeopleError("%s: not an object" % position)
        key = entry.get("key")
        if not _text(key):
            raise PeopleError("%s: missing key" % position)
        name = entry.get("name")
        if not _text(name):
            raise PeopleError("person %r: missing name" % key)
        jira_account_id = entry.get("jira_account_id")
        if not _text(jira_account_id):
            raise PeopleError("person %r: missing jira_account_id" % key)
        lowered = key.lower()
        if lowered in seen_keys:
            raise PeopleError("duplicate key %r" % key)
        if jira_account_id in seen_jira:
            raise PeopleError(
                "person %r: duplicate jira_account_id (already used by %r)" % (key, seen_jira[jira_account_id])
            )
        penpot_profile_id = entry.get("penpot_profile_id")
        if not _text(penpot_profile_id):
            penpot_profile_id = None
        elif penpot_profile_id in seen_penpot:
            raise PeopleError(
                "person %r: duplicate penpot_profile_id (already used by %r)" % (key, seen_penpot[penpot_profile_id])
            )
        is_owner = bool(entry.get("owner"))
        if is_owner and owner_key is not None:
            raise PeopleError("more than one owner: %r and %r" % (owner_key, key))
        telegram_chat_id = entry.get("telegram_chat_id")
        if not _text(telegram_chat_id):
            telegram_chat_id = None
        seen_keys[lowered] = key
        seen_jira[jira_account_id] = key
        if penpot_profile_id:
            seen_penpot[penpot_profile_id] = key
        if is_owner:
            owner_key = key
        people.append(
            Person(
                key=key,
                name=name,
                jira_account_id=jira_account_id,
                penpot_profile_id=penpot_profile_id,
                telegram_chat_id=telegram_chat_id,
                owner=is_owner,
            )
        )
    return people


def _text(value):
    return isinstance(value, str) and value.strip() != ""
