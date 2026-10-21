"""SQLite-backed state: seen comments, thread reservations and delivery jobs.

The whole state lives in a single SQLite file next to which a lock file guards
against a second instance. Every mutation that groups a seen-mark with the work
it produced runs inside one transaction.
"""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS seen (
    comment_id TEXT PRIMARY KEY,
    seen_at    REAL
);
CREATE TABLE IF NOT EXISTS threads (
    thread_id      TEXT PRIMARY KEY,
    file_id        TEXT,
    page_id        TEXT,
    frame_id       TEXT,
    comment_id     TEXT,
    title          TEXT,
    content        TEXT,
    state          TEXT NOT NULL,
    issue_key      TEXT,
    issue_url      TEXT,
    empty_searches INTEGER DEFAULT 0,
    last_empty_at  REAL,
    search_attempts INTEGER DEFAULT 0,
    next_search_at REAL,
    updated_at     REAL
);
CREATE TABLE IF NOT EXISTS thread_task (
    thread_id           TEXT PRIMARY KEY,
    assignee_key        TEXT,
    assignee_account_id TEXT,
    due                 TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT 'pending',
    attempts   INTEGER DEFAULT 0,
    not_before REAL DEFAULT 0,
    created_at REAL,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS onboarding_codes (
    code       TEXT PRIMARY KEY,
    person_key TEXT NOT NULL,
    created_at REAL,
    expires_at REAL,
    used_at    REAL
);
CREATE TABLE IF NOT EXISTS onboarding_bindings (
    person_key TEXT PRIMARY KEY,
    chat_id,
    user_id,
    bound_at   REAL
);
CREATE TABLE IF NOT EXISTS onboarding_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

THREAD_STATES = ("pending", "sending", "linked", "needs-manual")


class AlreadyRunning(Exception):
    """Another instance holds the state lock."""


class State:
    def __init__(self, db_path, clock=None):
        self.clock = clock
        self.db_path = db_path
        directory = os.path.dirname(os.path.abspath(db_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._lock_path = db_path + ".lock"
        self._lock_handle = open(self._lock_path, "a+")
        try:
            fcntl.flock(self._lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_handle.close()
            raise AlreadyRunning("another figmapot-bot instance is already running for %s" % db_path)
        self.conn = sqlite3.connect(db_path, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 30000")
        self.conn.executescript(SCHEMA)

    # -- lifecycle ---------------------------------------------------------
    def close(self):
        try:
            self.conn.close()
        finally:
            try:
                fcntl.flock(self._lock_handle, fcntl.LOCK_UN)
            except OSError:
                pass
            self._lock_handle.close()

    @contextmanager
    def transaction(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def _now(self):
        return self.clock.now() if self.clock is not None else 0.0

    # -- meta --------------------------------------------------------------
    def get_meta(self, key, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key, value):
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def baseline_complete(self):
        return self.get_meta("baseline_complete") == "1"

    # -- comments ----------------------------------------------------------
    def is_seen(self, comment_id):
        return self.conn.execute("SELECT 1 FROM seen WHERE comment_id = ?", (comment_id,)).fetchone() is not None

    def seen_count(self):
        return self.conn.execute("SELECT COUNT(*) AS n FROM seen").fetchone()["n"]

    def apply_comment(self, comment_id, seen_at, reservation=None, jobs=()):
        """Mark a comment seen and, in the same transaction, enqueue its work."""
        with self.transaction():
            self.conn.execute(
                "INSERT OR IGNORE INTO seen(comment_id, seen_at) VALUES (?, ?)", (comment_id, seen_at)
            )
            if reservation is not None:
                self.conn.execute(
                    "INSERT OR IGNORE INTO threads"
                    "(thread_id, file_id, page_id, frame_id, comment_id, title, content, state, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                    (
                        reservation["thread_id"],
                        reservation["file_id"],
                        reservation.get("page_id"),
                        reservation.get("frame_id"),
                        reservation["comment_id"],
                        reservation["title"],
                        reservation.get("content"),
                        seen_at,
                    ),
                )
                # The assignee/due of the reservation live in an additive table
                # (an upgrade never rewrites the rows written before it). A row
                # is only written when the command actually carries one, so a
                # plain ``task <title>`` keeps today's create-issue call.
                if reservation.get("assignee_key") or reservation.get("due"):
                    self.conn.execute(
                        "INSERT OR IGNORE INTO thread_task"
                        "(thread_id, assignee_key, assignee_account_id, due) VALUES (?, ?, ?, ?)",
                        (
                            reservation["thread_id"],
                            reservation.get("assignee_key"),
                            reservation.get("assignee_account_id"),
                            reservation.get("due"),
                        ),
                    )
            self._insert_jobs(jobs, seen_at)

    def commit_baseline(self, seen_items, bot_profile_id):
        """Store every baseline seen-mark and the completion flag atomically."""
        with self.transaction():
            self.conn.executemany(
                "INSERT OR IGNORE INTO seen(comment_id, seen_at) VALUES (?, ?)", list(seen_items)
            )
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES ('baseline_complete', '1') "
                "ON CONFLICT(key) DO UPDATE SET value = '1'"
            )
            if bot_profile_id:
                self.conn.execute(
                    "INSERT INTO meta(key, value) VALUES ('bot_profile_id', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (bot_profile_id,),
                )

    # -- threads -----------------------------------------------------------
    def get_thread(self, thread_id):
        row = self.conn.execute("SELECT * FROM threads WHERE thread_id = ?", (thread_id,)).fetchone()
        return dict(row) if row else None

    def list_threads(self, states=None):
        if states:
            placeholders = ",".join("?" for _ in states)
            rows = self.conn.execute(
                "SELECT * FROM threads WHERE state IN (%s) ORDER BY updated_at" % placeholders, tuple(states)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM threads ORDER BY updated_at").fetchall()
        return [dict(row) for row in rows]

    def get_thread_task(self, thread_id):
        """The assignee/due reserved for a thread, or ``None`` when it has none."""
        row = self.conn.execute(
            "SELECT * FROM thread_task WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return dict(row) if row else None

    def set_thread_state(self, thread_id, state):
        self.conn.execute(
            "UPDATE threads SET state = ?, updated_at = ? WHERE thread_id = ?",
            (state, self._now(), thread_id),
        )

    def link_thread(self, thread_id, issue_key, issue_url, jobs=()):
        with self.transaction():
            self.conn.execute(
                "UPDATE threads SET state = 'linked', issue_key = ?, issue_url = ?, updated_at = ? "
                "WHERE thread_id = ?",
                (issue_key, issue_url, self._now(), thread_id),
            )
            self._insert_jobs(jobs, self._now())

    def record_empty_search(self, thread_id, now):
        # Keep the first empty-search timestamp so the 60 s "two empty searches"
        # rule cannot be reset by intermediate polls.
        self.conn.execute(
            "UPDATE threads SET empty_searches = empty_searches + 1, "
            "last_empty_at = COALESCE(last_empty_at, ?), search_attempts = 0, "
            "next_search_at = NULL, updated_at = ? "
            "WHERE thread_id = ?",
            (now, now, thread_id),
        )

    def defer_search(self, thread_id, now, delay):
        """Record a failed search and the earliest time to try again."""
        self.conn.execute(
            "UPDATE threads SET search_attempts = search_attempts + 1, next_search_at = ?, updated_at = ? "
            "WHERE thread_id = ?",
            (now + delay, now, thread_id),
        )

    def clear_search_deferral(self, thread_id, now):
        self.conn.execute(
            "UPDATE threads SET search_attempts = 0, next_search_at = NULL, updated_at = ? WHERE thread_id = ?",
            (now, thread_id),
        )

    def mark_needs_manual(self, thread_id, now, jobs=()):
        with self.transaction():
            self.conn.execute(
                "UPDATE threads SET state = 'needs-manual', updated_at = ? WHERE thread_id = ?",
                (now, thread_id),
            )
            self._insert_jobs(jobs, now)

    # -- jobs --------------------------------------------------------------
    def _insert_jobs(self, jobs, now):
        for kind, payload in jobs:
            self.conn.execute(
                "INSERT INTO jobs(kind, payload, state, attempts, not_before, created_at, updated_at) "
                "VALUES (?, ?, 'pending', 0, ?, ?, ?)",
                (kind, json.dumps(payload), now, now, now),
            )

    def add_job(self, kind, payload, now=None):
        now = self._now() if now is None else now
        self._insert_jobs([(kind, payload)], now)

    def list_jobs(self, states=None):
        if states:
            placeholders = ",".join("?" for _ in states)
            rows = self.conn.execute(
                "SELECT * FROM jobs WHERE state IN (%s) ORDER BY id" % placeholders, tuple(states)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def list_due_jobs(self, now):
        rows = self.conn.execute(
            "SELECT * FROM jobs WHERE state = 'pending' AND not_before <= ? ORDER BY id", (now,)
        ).fetchall()
        return [dict(row) for row in rows]

    def complete_job(self, job_id):
        self.conn.execute("UPDATE jobs SET state = 'done', updated_at = ? WHERE id = ?", (self._now(), job_id))

    def drop_job(self, job_id):
        self.conn.execute("UPDATE jobs SET state = 'dropped', updated_at = ? WHERE id = ?", (self._now(), job_id))

    def fail_job(self, job_id, now, delay):
        self.conn.execute(
            "UPDATE jobs SET attempts = attempts + 1, not_before = ?, updated_at = ? WHERE id = ?",
            (now + delay, now, job_id),
        )
