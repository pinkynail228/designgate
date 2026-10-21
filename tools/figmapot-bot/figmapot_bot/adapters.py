"""The three small adapters: design (Penpot), tracker (Jira), chat (Telegram).

They are separate classes with a narrow surface so the chat backend can later
become Slack and the tracker can move without touching the bot logic.
"""
from __future__ import annotations

import base64
import http.cookiejar
import json
import re
from urllib.parse import urlencode

from .httpclient import (
    DEFAULT_TIMEOUT,
    RETRY_STATUSES,
    RetryableError,
    TransportError,
    build_opener,
    do_request,
    request_with_retry,
    retry_after_seconds,
    single_request,
)
from .commands import ZERO_UUID

# The Penpot comment editor wraps mentions in zero-width characters (U+200B).
ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")


def mask_secret(text, *secrets):
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


class PenpotError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class PenpotAuthError(PenpotError):
    pass


class PenpotNotFound(PenpotError):
    pass


def _is_auth_error(status, code, hint):
    if status in (401, 403):
        return True
    blob = "%s %s" % (code or "", hint or "")
    blob = blob.lower()
    return "auth" in blob or "unauthor" in blob or "session" in blob or "token" in blob


def _is_not_found(status, code, hint):
    if status == 404:
        return True
    blob = ("%s %s" % (code or "", hint or "")).lower()
    return "not-found" in blob or "not_found" in blob or "notfound" in blob


class PenpotClient:
    """Design adapter talking to the Penpot RPC API."""

    def __init__(self, base_url, email, password, clock, logger, timeout=DEFAULT_TIMEOUT, rng=None,
                 opener=None):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.password = password
        self.clock = clock
        self.logger = logger
        self.timeout = timeout
        self.rng = rng
        self._cookiejar = http.cookiejar.CookieJar()
        self.opener = opener if opener is not None else build_opener(self._cookiejar)
        self._profile_id = None
        self.login_count = 0
        self._logged_in = False

    def get_profile_id(self):
        if self._profile_id:
            return self._profile_id
        # Penpot answers get-profile with an anonymous profile (zero uuid) when
        # unauthenticated, so a session must exist before the profile id is read.
        self.ensure_session()
        profile_id = self._fetch_profile_id()
        if profile_id is None:
            self.login()
            profile_id = self._fetch_profile_id()
        if profile_id is None:
            raise PenpotAuthError("penpot returned an anonymous profile after login")
        self._profile_id = profile_id
        return self._profile_id

    def _fetch_profile_id(self):
        profile = self._rpc("get-profile", {})
        if not isinstance(profile, dict):
            return None
        value = profile.get("id")
        if not value or value == ZERO_UUID:
            return None
        return value

    def ensure_session(self):
        if not self._logged_in:
            self.login()

    def login(self, retry_transport=True):
        self._rpc(
            "login-with-password",
            {"email": self.email, "password": self.password},
            retry_auth=False,
            retry_transport=retry_transport,
        )
        self.login_count += 1
        self._logged_in = True

    def get_comment_threads(self, file_id):
        threads = self._rpc("get-comment-threads", {"fileId": file_id})
        if not isinstance(threads, list):
            # A body that is not a list is a read failure, never "no threads":
            # treating it as empty would silently complete the baseline.
            raise PenpotError("penpot get-comment-threads returned a non-list body")
        return threads

    def get_comments(self, thread_id):
        comments = self._rpc("get-comments", {"threadId": thread_id})
        if not isinstance(comments, list):
            # Likewise, ``{}`` or ``null`` must not look like an empty thread.
            raise PenpotError("penpot get-comments returned a non-list body")
        return comments

    def create_comment(self, thread_id, content, mentions=None):
        # A delivery: one attempt, so a slow retry never blocks other jobs.
        return self._rpc(
            "create-comment",
            {"threadId": thread_id, "content": content, "mentions": list(mentions or [])},
            retry_transport=False,
        )

    @staticmethod
    def command_text(comment, bot_profile_id):
        """Return the command text of a Penpot comment, or None.

        Penpot addressing is adapter business: the ``@[Name](id)`` mention
        syntax, the zero-width characters the editor wraps it in, and the
        comment's own ``mentions`` list.  The core only ever sees the neutral
        text that follows the mention.
        """
        if not comment:
            return None
        if bot_profile_id not in (comment.get("mentions") or []):
            return None
        content = ZERO_WIDTH.sub("", comment.get("content") or "")
        pattern = r"\A\s*@\[[^\]]*\]\(" + re.escape(bot_profile_id) + r"\)(?P<rest>.*)\Z"
        match = re.match(pattern, content, re.IGNORECASE | re.DOTALL)
        if not match:
            return None
        return match.group("rest").strip()

    # -- internals ---------------------------------------------------------
    def _json_headers(self):
        return {"Accept": "application/json", "Content-Type": "application/json"}

    def _request_with_retry(self, method, params):
        url = "%s/api/main/methods/%s" % (self.base_url, method)
        body = json.dumps(params).encode("utf-8")
        return request_with_retry(
            self.opener,
            "POST",
            url,
            data=body,
            headers=self._json_headers(),
            timeout=self.timeout,
            clock=self.clock,
            rng=self.rng,
        )

    def _request_once(self, method, params):
        url = "%s/api/main/methods/%s" % (self.base_url, method)
        body = json.dumps(params).encode("utf-8")
        return single_request(
            self.opener, "POST", url, data=body, headers=self._json_headers(),
            timeout=self.timeout, clock=self.clock,
        )

    def _rpc(self, method, params, retry_auth=True, retry_transport=True):
        if retry_transport:
            try:
                response = self._request_with_retry(method, params)
            except RetryableError as error:
                # Poll/read calls (unlike delivery jobs, which use
                # ``single_request``) surface a PenpotError: the cycle aborts
                # and retries on the next poll instead of leaking transport
                # state to the caller.
                raise PenpotError(
                    "penpot %s failed (status=%s)" % (method, error.status), error.status
                ) from None
        else:
            response = self._request_once(method, params)
        payload = None
        try:
            payload = response.json()
        except Exception:
            payload = None
        error = payload.get("error") if isinstance(payload, dict) else None
        if error is not None or response.status >= 400:
            code = error.get("code") if isinstance(error, dict) else None
            hint = error.get("hint") if isinstance(error, dict) else None
            if retry_auth and _is_auth_error(response.status, code, hint):
                self.logger.info("penpot session expired, logging in again")
                # Match the caller's delivery semantics: a re-login during a
                # delivery must not sleep through retries inline.
                self.login(retry_transport=retry_transport)
                return self._rpc(method, params, retry_auth=False, retry_transport=retry_transport)
            # Remote code/hint text is untrusted and never copied into the
            # exception, so it cannot leak secrets into logs.
            message = "penpot %s failed (status=%s)" % (method, response.status)
            if _is_not_found(response.status, code, hint):
                raise PenpotNotFound(message, response.status)
            if _is_auth_error(response.status, code, hint):
                raise PenpotAuthError(message, response.status)
            raise PenpotError(message, response.status)
        if payload is None and response.body:
            # A successful response with a non-empty body that is not JSON is a
            # failure: swallowing it would turn a broken read into "empty".
            message = "penpot %s returned an invalid body" % method
            if retry_transport:
                raise PenpotError(message, response.status)
            raise RetryableError(message, status=response.status)
        return payload


class JiraError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class Issue:
    def __init__(self, key, url, created=None):
        self.key = key
        self.url = url
        self.created = created


class JiraClient:
    """Tracker adapter talking to the Jira Cloud REST v3 API."""

    def __init__(self, base_url, email, api_token, project_key, issue_type, clock, logger,
                 timeout=DEFAULT_TIMEOUT, rng=None, opener=None):
        self.base_url = base_url.rstrip("/")
        self.project_key = project_key
        self.issue_type = issue_type
        self.clock = clock
        self.logger = logger
        self.timeout = timeout
        self.rng = rng
        token = base64.b64encode(("%s:%s" % (email, api_token)).encode("utf-8")).decode("ascii")
        self.auth_header = "Basic " + token
        self.opener = opener if opener is not None else build_opener()

    def _headers(self):
        return {"Authorization": self.auth_header, "Accept": "application/json", "Content-Type": "application/json"}

    def create_issue(self, summary, description, labels):
        fields = {
            "project": {"key": self.project_key},
            "issuetype": {"name": self.issue_type},
            "summary": summary,
            "description": description,
            "labels": list(labels),
        }
        body = json.dumps({"fields": fields}).encode("utf-8")
        url = self.base_url + "/rest/api/3/issue"
        try:
            response = do_request(self.opener, "POST", url, data=body, headers=self._headers(), timeout=self.timeout)
        except TransportError as error:
            raise JiraError("jira issue creation failed: %s" % error)
        if response.status in RETRY_STATUSES:
            # Keep the server's retry deadline so the caller can defer instead
            # of hammering Jira (and so a 429 is never mistaken for success).
            raise RetryableError(
                "jira issue creation failed (status=%s)" % response.status,
                delay=retry_after_seconds(response, self.clock),
                status=response.status,
            )
        if response.status >= 400:
            raise JiraError("jira issue creation failed (status=%s)" % response.status, response.status)
        payload = response.json()
        key = payload["key"]
        return Issue(key=key, url=self.base_url + "/browse/" + key)

    def search_by_label(self, comment_id):
        """Search for the issue of a comment. Raises on any failure."""
        jql = 'project = %s AND labels = "figmapot-c-%s"' % (self.project_key, comment_id)
        query = urlencode({"jql": jql, "fields": "key,created", "maxResults": "50"})
        url = self.base_url + "/rest/api/3/search/jql?" + query
        response = request_with_retry(
            self.opener, "GET", url, headers=self._headers(), timeout=self.timeout,
            clock=self.clock, rng=self.rng,
        )
        if response.status >= 400:
            raise JiraError("jira search failed (status=%s)" % response.status, response.status)
        try:
            payload = response.json()
        except Exception:
            raise RetryableError("jira search returned an invalid body", status=response.status)
        if not isinstance(payload, dict) or not isinstance(payload.get("issues"), list):
            # A 200 without an ``issues`` array is a failure, never an empty
            # search: only a well-formed zero-length list counts as empty.
            raise RetryableError("jira search returned a malformed body", status=response.status)
        issues = []
        for item in payload["issues"]:
            fields = item.get("fields") or {}
            issues.append(Issue(item["key"], self.base_url + "/browse/" + item["key"], fields.get("created")))
        return issues

    def set_link_property(self, issue_key, props):
        # A delivery: one attempt. Retryable failures (429/5xx/timeout) bubble
        # up so the caller reschedules the job instead of sleeping inline.
        url = "%s/rest/api/3/issue/%s/properties/figmapot.link" % (self.base_url, issue_key)
        body = json.dumps(props).encode("utf-8")
        response = single_request(
            self.opener, "PUT", url, data=body, headers=self._headers(),
            timeout=self.timeout, clock=self.clock,
        )
        if response.status >= 400:
            raise JiraError("jira property update failed (status=%s)" % response.status, response.status)


class TelegramError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def render_notice(notice):
    """Render a neutral notice payload into today's Telegram text."""
    notice = notice or {}
    kind = notice.get("kind")
    if kind == "issue_created":
        text = "New figmapot issue %s: %s\n%s\n%s" % (
            notice.get("issue_key"),
            notice.get("title"),
            notice.get("issue_url"),
            notice.get("board_url"),
        )
        details = _assignee_line(notice)
        if details:
            text += "\n" + details
        return text
    if kind == "assigned":
        text = "Penpot: you are assigned %s: %s" % (notice.get("issue_key"), notice.get("title"))
        if notice.get("due"):
            text += ", due %s" % notice.get("due")
        return text
    if kind == "needs_manual":
        return (
            "figmapot: no Jira issue found for thread %s (comment %s) after two empty searches. "
            "Manual action needed." % (notice.get("thread_id"), notice.get("comment_id"))
        )
    raise TelegramError("unknown notice kind %r" % (kind,))


def _assignee_line(notice):
    """``Assignee: <name>, due <date>`` for the parts the notice carries, else ""."""
    parts = []
    if notice.get("assignee_name"):
        parts.append("Assignee: %s" % notice.get("assignee_name"))
    if notice.get("due"):
        parts.append("due %s" % notice.get("due"))
    return ", ".join(parts)


class TelegramClient:
    """Chat adapter talking to the Telegram Bot API."""

    def __init__(self, bot_token, chat_id, clock, logger, api_base=None,
                 timeout=DEFAULT_TIMEOUT, rng=None, opener=None):
        from .config import DEFAULT_TELEGRAM_API_BASE

        self.bot_token = bot_token
        self.chat_id = chat_id
        self.clock = clock
        self.logger = logger
        self.api_base = (api_base or DEFAULT_TELEGRAM_API_BASE).rstrip("/")
        self.timeout = timeout
        self.rng = rng
        self.opener = opener if opener is not None else build_opener()

    def send_message(self, text=None, notice=None, chat_id=None):
        """Send ``text``, or render the neutral ``notice`` and send that.

        ``chat_id`` defaults to the configured group chat; a private delivery
        passes the recipient's chat id explicitly.
        """
        if notice is not None:
            text = render_notice(notice)
        target = self.chat_id if chat_id is None else chat_id
        url = "%s/bot%s/sendMessage" % (self.api_base, self.bot_token)
        body = json.dumps({"chat_id": target, "text": text}).encode("utf-8")
        try:
            response = single_request(
                self.opener, "POST", url, data=body, headers={"Content-Type": "application/json"},
                timeout=self.timeout, clock=self.clock,
            )
        except RetryableError as error:
            raise RetryableError(
                mask_secret(str(error), self.bot_token), delay=error.delay, status=error.status
            ) from None
        if response.status >= 400:
            raise TelegramError("telegram sendMessage failed (status=%s)" % response.status, response.status)
