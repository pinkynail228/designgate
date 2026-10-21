"""The poll loop: detect commands, create issues, deliver notifications."""
from __future__ import annotations

import json

from . import onboarding, people
from .commands import (
    build_board_link,
    build_issue_description,
    build_issue_labels,
    build_thread_link,
    owner_command,
    reject_text,
    reply_text,
)
from .adapters import PenpotNotFound
from .config import local_date
from .grammar import ParseError, parse_task
from .httpclient import RetryableError, backoff_delay
from .jira_tasks import create_issue_with, is_assignable, issue_fields

# One-line confirmations the onboarding poll sends back to the person (AC-5).
LINK_CONFIRMATIONS = {
    "bound": "Penpot: this chat is now linked to %s",
    "rebound": "Penpot: this chat is now linked to %s",
}
LINK_REJECTIONS = {
    "expired": "Penpot: this link expired; ask the owner for a new link",
    "used": "Penpot: this link was already used; ask the owner for a new link",
    "unknown": "Penpot: unknown link code; ask the owner for a new link",
}


def assignable_directory(directory, tracker, logger):
    """Keep only the people the Jira project can assign (AC-1).

    Each person is checked once; one whose check fails or says "no" is logged
    by key and dropped, so it stays unknown until the next start.
    """
    kept = []
    for person in _directory_members(directory):
        try:
            allowed = is_assignable(tracker, person.jira_account_id)
        except Exception as error:
            # The reason is only the failure class: the person is named by key.
            logger.warning("assignee check failed for %s (%s)", person.key, type(error).__name__)
            allowed = False
        if not allowed:
            logger.info("person %s is not assignable; treating as unknown", person.key)
            continue
        kept.append(person)
    return people.Directory(kept)


def _directory_members(directory):
    # ``people.Directory`` only exposes lookups; its key map is the flat list.
    members = getattr(directory, "_by_key", None) or {}
    return sorted(members.values(), key=lambda person: person.key)


class Bot:
    def __init__(self, config, clock, state, design, tracker, chat, logger,
                 rng=None, search_gap=60.0, directory=None):
        self.config = config
        self.clock = clock
        self.state = state
        self.design = design
        self.tracker = tracker
        self.chat = chat
        self.logger = logger
        self.rng = rng
        self.search_gap = search_gap
        self.bot_profile_id = None
        self.directory = directory if directory is not None else people.Directory([])

    # -- top level ---------------------------------------------------------
    def run(self, once=False):
        # At start, once per person: keep only the assignable ones (AC-1).
        self.check_assignees()
        while True:
            try:
                self.cycle()
            except Exception:
                self.logger.exception("poll cycle failed")
                if once:
                    raise
            if once:
                return
            self.clock.sleep(self.config.poll_seconds)

    def cycle(self):
        # The whole poll part shares one guard: a failing read must not starve
        # the deliveries, so the delivery pass runs and the error is re-raised
        # afterwards (run() logs it and --once still exits non-zero).
        poll_error = None
        try:
            self.bot_profile_id = self.design.get_profile_id()
            # Baseline or comment work first: a failing onboarding poll must
            # never block the first-time import or the command scan.
            if self.state.baseline_complete():
                self._poll()
                self._process_threads()
            else:
                self._run_baseline()
                self.logger.info("poll cycle: baseline complete")
            # Onboarding is polled on every cycle, the initial one included, so
            # a chat that linked before the first start is still confirmed.
            self._poll_onboarding()
        except Exception as error:
            poll_error = error
        self._process_jobs()
        if poll_error is not None:
            raise poll_error
        self.logger.info("poll cycle: done")

    # -- baseline (AC-3) ---------------------------------------------------
    def _run_baseline(self):
        now = self.clock.now()
        seen = {}
        for file_id in self.config.file_ids:
            threads = self.design.get_comment_threads(file_id)
            for thread in threads:
                for comment in self.design.get_comments(thread["id"]):
                    seen[comment["id"]] = now
        self.state.commit_baseline(seen.items(), self.bot_profile_id)
        self.logger.info("baseline: %d comments marked seen", len(seen))

    # -- assignability (AC-1) ----------------------------------------------
    def check_assignees(self):
        """Drop every person the Jira project cannot assign (once per start)."""
        self.directory = assignable_directory(self.directory, self.tracker, self.logger)

    # -- onboarding (AC-5) -------------------------------------------------
    def _poll_onboarding(self):
        """Poll the private-chat links; called on every cycle."""
        now = self.clock.now()
        events = onboarding.poll_updates(self.state, self.chat, now)
        self._confirm_links(events, now)

    # -- command detection (AC-2) ------------------------------------------
    def _poll(self):
        now = self.clock.now()
        today = local_date(self.config.bot_timezone, now)
        for file_id in self.config.file_ids:
            threads = self.design.get_comment_threads(file_id)
            for thread in threads:
                for comment in self.design.get_comments(thread["id"]):
                    comment_id = comment["id"]
                    if self.state.is_seen(comment_id):
                        continue
                    command_text = owner_command(
                        comment, self.config.owner_id,
                        self.design.command_text(comment, self.bot_profile_id),
                    )
                    if command_text is None:
                        self.state.apply_comment(comment_id, now)
                        continue
                    self._handle_parsed(file_id, thread, comment, parse_task(command_text, today), now)

    def _confirm_links(self, events, now):
        """Enqueue the one-line onboarding confirmations (AC-5).

        These jobs follow the poll transaction, so a crash in between may lose a
        confirmation; that is accepted.
        """
        for event in events:
            kind = event.get("type")
            if kind in LINK_CONFIRMATIONS:
                binding = event.get("binding") or {}
                chat_id = binding.get("chat_id")
                if chat_id is None:
                    continue
                text = LINK_CONFIRMATIONS[kind] % binding.get("person_key")
            elif kind == "rejected" and event.get("reason") in LINK_REJECTIONS:
                chat_id = event.get("chat_id")
                if chat_id is None:
                    continue
                text = LINK_REJECTIONS[event["reason"]]
            else:
                continue
            self.state.add_job("telegram_dm", {"chat_id": chat_id, "text": text}, now=now)

    def _handle_parsed(self, file_id, thread, comment, parsed, now):
        """Route a parsed command: refusal reply, or a thread reservation."""
        thread_id = thread["id"]
        comment_id = comment["id"]
        if isinstance(parsed, ParseError):
            if parsed.reason in ("due_invalid", "due_past"):
                self._reject(thread_id, comment_id, reject_text(parsed.reason), now)
            else:
                # ``command``/``title`` errors stay silently ignored, as today.
                self.state.apply_comment(comment_id, now)
            return
        person = self._resolve_person(parsed.assignee_ref)
        if parsed.assignee_ref and person is None:
            self._reject(thread_id, comment_id, reject_text("unknown_person", parsed.assignee_ref), now)
            return
        self._handle_command(file_id, thread, comment, parsed, person, now)

    def _reject(self, thread_id, comment_id, text, now):
        """Mark the comment seen and reply with the one-line refusal, no thread."""
        self.state.apply_comment(
            comment_id, now,
            jobs=[("penpot_reply", {"threadId": thread_id, "content": text})],
        )
        self.logger.info("task command rejected for thread %s", thread_id)

    def _resolve_person(self, ref):
        return self.directory.resolve(ref) if ref else None

    def _handle_command(self, file_id, thread, comment, parsed, person, now):
        thread_id = thread["id"]
        comment_id = comment["id"]
        existing = self.state.get_thread(thread_id)
        if existing is None:
            reservation = {
                "thread_id": thread_id,
                "file_id": file_id,
                "page_id": thread.get("pageId"),
                "frame_id": thread.get("frameId"),
                "comment_id": comment_id,
                "title": parsed.title,
                "content": comment.get("content") or "",
                "assignee_key": person.key if person is not None else None,
                "assignee_account_id": person.jira_account_id if person is not None else None,
                "due": parsed.due,
            }
            self.state.apply_comment(comment_id, now, reservation=reservation)
            self.logger.info("task command accepted: thread=%s comment=%s", thread_id, comment_id)
        elif existing["state"] == "linked":
            content = reply_text(existing["issue_key"], existing["issue_url"])
            self.state.apply_comment(
                comment_id, now, jobs=[("penpot_reply", {"threadId": thread_id, "content": content})]
            )
            self.logger.info("task command on linked thread: reply with %s", existing["issue_key"])
        else:
            self.state.apply_comment(comment_id, now)
            self.logger.info("task command ignored (thread %s is %s)", thread_id, existing["state"])

    # -- issue creation and recovery (AC-4/AC-5) ---------------------------
    def _process_threads(self):
        now = self.clock.now()
        for thread in self.state.list_threads(("pending", "sending")):
            if thread["state"] == "pending":
                self.state.set_thread_state(thread["thread_id"], "sending")
                self._post_issue(thread)
            else:
                deadline = thread.get("next_search_at")
                if deadline is not None and deadline > now:
                    continue
                self._reconcile(thread)

    def _post_issue(self, thread):
        task = self.state.get_thread_task(thread["thread_id"])
        try:
            if task is None:
                # A thread with no stored fields keeps today's call.
                issue = self.tracker.create_issue(
                    summary=thread["title"],
                    description=self._description(thread),
                    labels=build_issue_labels(thread["comment_id"]),
                )
            else:
                fields = issue_fields(
                    self.tracker,
                    summary=thread["title"],
                    description=self._description(thread),
                    labels=build_issue_labels(thread["comment_id"]),
                    assignee_account_id=task["assignee_account_id"],
                    due=task["due"],
                )
                issue = create_issue_with(self.tracker, fields)
        except RetryableError as error:
            # The creation call asked us to wait (e.g. a 429 with Retry-After):
            # defer the reconciliation to the server's deadline instead of
            # searching Jira immediately, and never repeat the POST.
            delay = error.delay if error.delay is not None else backoff_delay(
                thread.get("search_attempts") or 0, rng=self.rng
            )
            self.state.defer_search(thread["thread_id"], self.clock.now(), delay)
            self.logger.warning(
                "issue creation deferred %ss for %s: %s", delay, thread["thread_id"], error
            )
            return
        except Exception as error:
            self.logger.warning("issue creation failed for %s: %s", thread["thread_id"], error)
            self._reconcile(thread)
            return
        self._link(thread, issue)

    def _reconcile(self, thread):
        try:
            issues = self.tracker.search_by_label(thread["comment_id"])
        except RetryableError as error:
            delay = error.delay if error.delay is not None else backoff_delay(
                thread.get("search_attempts") or 0, rng=self.rng
            )
            self.state.defer_search(thread["thread_id"], self.clock.now(), delay)
            self.logger.warning("label search deferred %ss for %s: %s", delay, thread["thread_id"], error)
            return
        except Exception as error:
            delay = backoff_delay(thread.get("search_attempts") or 0, rng=self.rng)
            self.state.defer_search(thread["thread_id"], self.clock.now(), delay)
            self.logger.warning("label search failed for %s: %s", thread["thread_id"], error)
            return
        self.state.clear_search_deferral(thread["thread_id"], self.clock.now())
        if not issues:
            now = self.clock.now()
            last = thread["last_empty_at"]
            if last is not None and now - last >= self.search_gap:
                notice = self._notice(
                    "needs_manual", thread,
                    thread_id=thread["thread_id"], comment_id=thread["comment_id"],
                )
                self.state.mark_needs_manual(thread["thread_id"], now, [("telegram_alert", {"notice": notice})])
                self.logger.warning("thread %s needs manual action", thread["thread_id"])
            else:
                self.state.record_empty_search(thread["thread_id"], now)
                self.logger.info("search for %s is empty, will confirm later", thread["thread_id"])
            return
        if len(issues) == 1:
            self._link(thread, issues[0])
            return
        oldest = sorted(issues, key=lambda item: item.created or "")[0]
        keys = ", ".join(sorted(item.key for item in issues))
        text = "figmapot: multiple Jira issues for thread %s: %s" % (thread["thread_id"], keys)
        self._link(thread, oldest, alert_text=text)

    def _link(self, thread, issue, alert_text=None):
        jobs = self._delivery_jobs(thread, issue.key, issue.url)
        if alert_text is not None:
            jobs.append(("telegram_alert", {"text": alert_text}))
        self.state.link_thread(thread["thread_id"], issue.key, issue.url, jobs)
        self.logger.info("thread %s linked to %s", thread["thread_id"], issue.key)

    def _delivery_jobs(self, thread, issue_key, issue_url):
        task = self.state.get_thread_task(thread["thread_id"])
        assignee_name = self._assignee_name(task)
        due = task.get("due") if task is not None else None
        properties = {
            "fileId": thread["file_id"],
            "pageId": thread["page_id"],
            "frameId": thread["frame_id"],
            "threadId": thread["thread_id"],
            "commentId": thread["comment_id"],
        }
        jobs = [
            ("property", {"key": issue_key, "props": properties}),
            (
                "penpot_reply",
                {
                    "threadId": thread["thread_id"],
                    "content": reply_text(issue_key, issue_url, assignee_name, due),
                },
            ),
            (
                "telegram_message",
                {
                    "notice": self._notice(
                        "issue_created", thread, issue_key, issue_url,
                        assignee_name=assignee_name, due=due,
                    )
                },
            ),
        ]
        if task is not None and task.get("assignee_key"):
            binding = onboarding.get_binding(self.state, task["assignee_key"])
            if binding is None:
                self.logger.info("no onboarding binding for %s; no DM", task["assignee_key"])
            else:
                jobs.append((
                    "telegram_dm",
                    {
                        "chat_id": binding["chat_id"],
                        "notice": self._notice(
                            "assigned", thread, issue_key, issue_url,
                            assignee_name=assignee_name, due=due,
                        ),
                    },
                ))
        return jobs

    def _assignee_name(self, task):
        if task is None or not task.get("assignee_key"):
            return None
        person = self.directory.resolve(task["assignee_key"])
        return person.name if person is not None else task["assignee_key"]

    def _notice(self, kind, thread, issue_key=None, issue_url=None, assignee_name=None, due=None, **extra):
        """A neutral notice the chat adapter renders at send time (AC-2)."""
        board = self._board_link(thread)
        notice = {
            "kind": kind,
            "title": thread["title"],
            "issue_key": issue_key,
            "issue_url": issue_url,
            "board_url": board,
            "thread_url": build_thread_link(board, thread["thread_id"]),
        }
        # Only set when present, so a plain notice renders exactly as today.
        if assignee_name:
            notice["assignee_name"] = assignee_name
        if due:
            notice["due"] = due
        notice.update(extra)
        return notice

    # -- delivery jobs (AC-6) ----------------------------------------------
    def _process_jobs(self):
        for job in self.state.list_due_jobs(self.clock.now()):
            try:
                self._run_job(job)
            except PenpotNotFound as error:
                self.state.drop_job(job["id"])
                self.logger.info("job %s %s dropped: %s", job["id"], job["kind"], error)
                continue
            except RetryableError as error:
                delay = error.delay if error.delay is not None else backoff_delay(job["attempts"], rng=self.rng)
                self.state.fail_job(job["id"], self.clock.now(), delay)
                self.logger.info("job %s %s deferred %.1fs: %s", job["id"], job["kind"], delay, error)
                continue
            except Exception as error:
                delay = backoff_delay(job["attempts"], rng=self.rng)
                self.state.fail_job(job["id"], self.clock.now(), delay)
                self.logger.info("job %s %s failed: %s", job["id"], job["kind"], error)
                continue
            self.state.complete_job(job["id"])
            self.logger.info("job %s %s done", job["id"], job["kind"])

    def _run_job(self, job):
        payload = json.loads(job["payload"])
        kind = job["kind"]
        if kind == "property":
            self.tracker.set_link_property(payload["key"], payload["props"])
        elif kind == "penpot_reply":
            self.design.create_comment(payload["threadId"], payload["content"], [])
        elif kind == "telegram_dm":
            # A private delivery: the recipient rides on the job (AC-4).
            self.chat.send_message(
                payload.get("text"), notice=payload.get("notice"), chat_id=payload["chat_id"]
            )
        elif kind in ("telegram_message", "telegram_alert"):
            self.chat.send_message(payload.get("text"), notice=payload.get("notice"))
        else:
            raise RuntimeError("unknown job kind %r" % kind)

    # -- helpers -----------------------------------------------------------
    def _board_link(self, thread):
        return build_board_link(
            self.config.penpot_url,
            self.config.team_id,
            thread["file_id"],
            thread["page_id"],
            thread["frame_id"],
        )

    def _thread_link(self, thread):
        return build_thread_link(self._board_link(thread), thread["thread_id"])

    def _description(self, thread):
        return build_issue_description(thread["content"], self._board_link(thread), self._thread_link(thread))
