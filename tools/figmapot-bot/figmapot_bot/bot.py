"""The poll loop: detect commands, create issues, deliver notifications."""
from __future__ import annotations

import json

from .commands import (
    build_board_link,
    build_issue_description,
    build_issue_labels,
    build_thread_link,
    parse_task_command,
    reply_text,
)
from .adapters import PenpotNotFound
from .httpclient import RetryableError, backoff_delay


class Bot:
    def __init__(self, config, clock, state, design, tracker, chat, logger,
                 rng=None, search_gap=60.0):
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

    # -- top level ---------------------------------------------------------
    def run(self, once=False):
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
        self.bot_profile_id = self.design.get_profile_id()
        if not self.state.baseline_complete():
            self._run_baseline()
            self.logger.info("poll cycle: baseline complete")
            return
        self._poll()
        self._process_threads()
        self._process_jobs()
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

    # -- command detection (AC-2) ------------------------------------------
    def _poll(self):
        now = self.clock.now()
        for file_id in self.config.file_ids:
            threads = self.design.get_comment_threads(file_id)
            for thread in threads:
                for comment in self.design.get_comments(thread["id"]):
                    comment_id = comment["id"]
                    if self.state.is_seen(comment_id):
                        continue
                    title = parse_task_command(comment, self.config.owner_id, self.bot_profile_id)
                    if title is None:
                        self.state.apply_comment(comment_id, now)
                        continue
                    self._handle_command(file_id, thread, comment, title, now)

    def _handle_command(self, file_id, thread, comment, title, now):
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
                "title": title,
                "content": comment.get("content") or "",
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
        try:
            issue = self.tracker.create_issue(
                summary=thread["title"],
                description=self._description(thread),
                labels=build_issue_labels(thread["comment_id"]),
            )
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
                text = (
                    "figmapot: no Jira issue found for thread %s (comment %s) after two empty searches. "
                    "Manual action needed." % (thread["thread_id"], thread["comment_id"])
                )
                self.state.mark_needs_manual(thread["thread_id"], now, [("telegram_alert", {"text": text})])
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
        board = self._board_link(thread)
        properties = {
            "fileId": thread["file_id"],
            "pageId": thread["page_id"],
            "frameId": thread["frame_id"],
            "threadId": thread["thread_id"],
            "commentId": thread["comment_id"],
        }
        message = "New figmapot issue %s: %s\n%s\n%s" % (issue_key, thread["title"], issue_url, board)
        return [
            ("property", {"key": issue_key, "props": properties}),
            ("penpot_reply", {"threadId": thread["thread_id"], "content": reply_text(issue_key, issue_url)}),
            ("telegram_message", {"text": message}),
        ]

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
        elif kind in ("telegram_message", "telegram_alert"):
            self.chat.send_message(payload["text"])
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
