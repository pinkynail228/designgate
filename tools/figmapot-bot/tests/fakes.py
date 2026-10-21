"""In-process HTTP fakes for Penpot, Jira and Telegram (no sockets).

Every fake answers urllib requests directly: ``build_fake_opener`` registers a
:class:`_FakeHandler` in place of urllib's socket-based ``HTTPHandler`` and the
handler calls ``FakeHttp.serve`` for the request.  urllib itself still turns a
status >= 400 into an ``HTTPError`` and still processes ``Set-Cookie`` headers,
so the production code sees exactly what a loopback server would send.
"""
from __future__ import annotations

import email.message
import http.client
import io
import itertools
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import urllib.response

DROP = object()
ZERO_UUID = "00000000-0000-0000-0000-000000000000"

# Synthetic loopback ports.  They are only labels: the fake transport never
# binds or connects, but a distinct origin per fake lets the handler route.
_PORTS = itertools.count(8100)


class ProcessKilled(BaseException):
    """The client process was killed after the fake persisted the request.

    A ``BaseException`` (like ``KeyboardInterrupt``) escapes the bot's
    ``except Exception`` handlers, exactly as a SIGKILL does in production.
    """


class Request:
    def __init__(self, method, path, headers, body):
        self.method = method
        self.path = path
        self.headers = headers
        self.body = body

    def json(self):
        return json.loads(self.body.decode("utf-8")) if self.body else None


class _FakeResponse(urllib.response.addinfourl):
    """An ``addinfourl`` that also carries the reason phrase urllib expects."""

    def __init__(self, fp, headers, url, code, msg=""):
        super().__init__(fp, headers, url, code)
        self.msg = msg


class _FakeHandler(urllib.request.HTTPHandler):
    """Serve requests from the fakes in-process, never opening a socket.

    Subclassing ``HTTPHandler`` makes ``build_opener`` drop the real
    socket-based handler, so no connection can ever be attempted.
    """

    def __init__(self, fakes):
        self._fakes = list(fakes)

    def http_open(self, request):
        netloc = urllib.parse.urlsplit(request.full_url).netloc
        for fake in self._fakes:
            if fake.netloc == netloc:
                return fake.open(request)
        # Do not echo the URL: a Telegram URL embeds the bot token.
        raise urllib.error.URLError("no fake registered for this host")

    https_open = http_open


def build_fake_opener(fakes, cookiejar=None):
    """An urllib opener that serves every request from ``fakes`` in-process."""
    handlers = []
    if cookiejar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(cookiejar))
    handlers.append(_FakeHandler(fakes))
    return urllib.request.build_opener(*handlers)


class FakeHttp:
    def __init__(self):
        self.requests = []
        self._lock = threading.Lock()
        self.port = next(_PORTS)

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.port

    @property
    def netloc(self):
        return "127.0.0.1:%d" % self.port

    def serve(self, method, path, headers, body):
        with self._lock:
            self.requests.append(Request(method, path, headers, body))
        return self.route(method, path, headers, body)

    def open(self, request):
        """Answer one urllib request, mirroring the old loopback handler."""
        parts = urllib.parse.urlsplit(request.full_url)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        result = self.serve(
            request.get_method(), path, dict(request.header_items()), request.data or b""
        )
        if result is DROP:
            # The old handler closed the connection without a response.
            raise urllib.error.URLError("fake connection dropped")
        status, headers, payload = result
        message = email.message.Message()
        for name, value in headers.items():
            message[name] = value
        return _FakeResponse(
            io.BytesIO(payload), message, request.full_url, status,
            http.client.responses.get(status, ""),
        )

    def route(self, method, path, headers, body):  # pragma: no cover - abstract
        return self.json_response(404, {"error": "not found"})

    def recorded(self, method=None, path_prefix=None):
        with self._lock:
            items = list(self.requests)
        result = []
        for item in items:
            if method and item.method != method:
                continue
            if path_prefix and not item.path.startswith(path_prefix):
                continue
            result.append(item)
        return result

    def count(self, method=None, path_prefix=None):
        return len(self.recorded(method, path_prefix))

    @staticmethod
    def json_response(status, body, headers=None):
        payload = json.dumps(body).encode("utf-8")
        merged = {"Content-Type": "application/json"}
        merged.update(headers or {})
        return status, merged, payload

    def stop(self):
        """No teardown: the fake never held a socket or a server thread."""


class FakePenpot(FakeHttp):
    def __init__(self, profile_id="bot-uuid-0000"):
        self.profile_id = profile_id
        self.valid_tokens = set()
        self._next_token = 1
        self.files = []
        self.threads = {}
        self.comments = {}
        self.login_count = 0
        self.fail_threads_for = set()
        self.not_found_comments = 0
        self.fail_comments_for = set()
        self.error_hint = "boom"
        self.fail_logins = 0
        self.login_retry_after = None
        self.expire_after_get_comments = 0
        self.comments_bad_json_times = 0
        self.comments_body = None
        self.comments_body_times = 0
        super().__init__()

    # -- fixtures ----------------------------------------------------------
    def add_file(self, file_id):
        self.files.append(file_id)

    def add_thread(self, file_id, thread_id, page_id="page-1", frame_id="frame-1"):
        self.threads.setdefault(file_id, []).append(
            {"id": thread_id, "pageId": page_id, "frameId": frame_id,
             "modifiedAt": "2026-10-20T10:00:00Z", "countComments": 0}
        )
        self.comments.setdefault(thread_id, [])

    def add_comment(self, thread_id, comment_id, owner_id, content, mentions=None):
        self.comments.setdefault(thread_id, []).append(
            {"id": comment_id, "ownerId": owner_id, "content": content,
             "mentions": list(mentions or []), "createdAt": "2026-10-20T10:00:00Z"}
        )

    def edit_comment(self, thread_id, comment_id, content, mentions=None):
        for comment in self.comments.get(thread_id, []):
            if comment["id"] == comment_id:
                comment["content"] = content
                if mentions is not None:
                    comment["mentions"] = list(mentions)

    def expire_sessions(self):
        self.valid_tokens.clear()

    def created_comments(self):
        return [req.json() for req in self.recorded("POST", "/api/main/methods/create-comment")]

    # -- routing -----------------------------------------------------------
    def route(self, method, path, headers, body):
        if not path.startswith("/api/main/methods/"):
            return self.json_response(404, {"error": "unknown"})
        name = path.rstrip("/").rsplit("/", 1)[-1]
        params = json.loads(body.decode("utf-8")) if body else {}
        if name == "login-with-password":
            if self.fail_logins > 0:
                self.fail_logins -= 1
                headers = {}
                if self.login_retry_after is not None:
                    headers["Retry-After"] = str(self.login_retry_after)
                return self.json_response(429, {"errorMessages": ["rate limited"]}, headers)
            self.login_count += 1
            token = "token-%d" % self._next_token
            self._next_token += 1
            self.valid_tokens.add(token)
            return self.json_response(200, {}, {"Set-Cookie": "auth-token=%s; Path=/" % token})
        if name == "get-profile":
            # Unauthenticated Penpot answers with an anonymous (zero uuid)
            # profile, not an error; a client must log in first.
            if not self._authenticated(headers):
                return self.json_response(200, {"id": ZERO_UUID, "email": None, "isActive": False})
            return self.json_response(200, {"id": self.profile_id, "email": "bot@example.com"})
        if not self._authenticated(headers):
            return self.json_response(200, {"error": {"code": "authentication", "hint": "invalid-token"}})
        if name == "get-comment-threads":
            file_id = params.get("fileId")
            if file_id in self.fail_threads_for:
                return self.json_response(500, {"error": {"code": "internal", "hint": self.error_hint}})
            return self.json_response(200, list(self.threads.get(file_id, [])))
        if name == "get-comments":
            thread_id = params.get("threadId")
            if thread_id in self.fail_comments_for:
                return self.json_response(500, {"error": {"code": "internal", "hint": self.error_hint}})
            if self.comments_bad_json_times > 0:
                self.comments_bad_json_times -= 1
                return 200, {"Content-Type": "application/json"}, b"{not valid json"
            if self.comments_body_times > 0:
                self.comments_body_times -= 1
                return self.json_response(200, self.comments_body)
            if self.expire_after_get_comments > 0:
                self.expire_after_get_comments -= 1
                self.expire_sessions()
            return self.json_response(200, list(self.comments.get(thread_id, [])))
        if name == "create-comment":
            if self.not_found_comments > 0:
                self.not_found_comments -= 1
                return self.json_response(200, {"error": {"code": "not-found", "hint": "thread deleted"}})
            return self.json_response(200, {"id": "new-comment"})
        return self.json_response(404, {"error": {"code": "not-found", "hint": name}})

    def _authenticated(self, headers):
        cookie = headers.get("Cookie") or ""
        match = re.search(r"auth-token=([^;]+)", cookie)
        return bool(match) and match.group(1) in self.valid_tokens


def is_valid_adf(description):
    if not isinstance(description, dict) or description.get("type") != "doc" or description.get("version") != 1:
        return False
    content = description.get("content")
    if not isinstance(content, list) or not content:
        return False
    for block in content:
        if block.get("type") != "paragraph":
            return False
        for node in block.get("content", []):
            if node.get("type") != "text" or not isinstance(node.get("text"), str):
                return False
            for mark in node.get("marks", []):
                if mark.get("type") != "link" or "href" not in mark.get("attrs", {}):
                    return False
    return True


class FakeJira(FakeHttp):
    def __init__(self, project_key="FP"):
        self.project_key = project_key
        self.issues = []
        self._next_number = 1
        # ``None`` means every account is assignable; a set restricts it.
        self.assignable_account_ids = None
        self.fail_assignable = False
        self.search_issues = None
        self.search_body = None
        self.fail_search_times = 0
        self.retry_after = None
        self.retry_after_raw = None
        self.retry_after_header = "Retry-After"
        self.search_status = None
        self.drop_post = False
        self.block_post = None
        self.post_started = threading.Event()
        self.fail_property_times = 0
        self.property_retry_after = None
        self.fail_post_times = 0
        self.post_retry_after = None
        self.post_retry_after_header = "Retry-After"
        self.retry_after_body = None
        self.crash_after_post = False
        super().__init__()

    def add_issue(self, key, labels=(), created="2026-10-20T10:00:00.000+0000"):
        self.issues.append({"key": key, "fields": {"labels": list(labels), "created": created}})

    def created_issues(self):
        return [req.json() for req in self.recorded("POST", "/rest/api/3/issue")]

    def route(self, method, path, headers, body):
        if method == "GET" and path.startswith("/rest/api/3/user/assignable/search"):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            account_id = (query.get("accountId") or [None])[0]
            if self.fail_assignable:
                return self.json_response(500, {"errorMessages": ["boom"]})
            if self.assignable_account_ids is None or account_id in self.assignable_account_ids:
                return self.json_response(200, [{"accountId": account_id}])
            return self.json_response(200, [])
        if method == "POST" and path == "/rest/api/3/issue":
            if self.drop_post:
                return DROP
            if self.fail_post_times > 0:
                self.fail_post_times -= 1
                headers = {}
                if self.post_retry_after is not None:
                    headers[self.post_retry_after_header] = str(self.post_retry_after)
                return self.json_response(429, {"errorMessages": ["rate limited"]}, headers)
            data = json.loads(body.decode("utf-8"))
            fields = data.get("fields", {})
            if not is_valid_adf(fields.get("description")):
                return self.json_response(400, {"errorMessages": ["invalid ADF description"]})
            key = "%s-%d" % (self.project_key, self._next_number)
            self._next_number += 1
            # The issue is persisted before any barrier, so a request that is
            # killed waiting for its response still leaves a real issue behind.
            self.issues.append({"key": key, "fields": fields})
            if self.block_post is not None:
                self.post_started.set()
                self.block_post.wait(30)
            if self.crash_after_post:
                # The issue is persisted, but the client is gone before it can
                # read the response: the state stays "sending" for recovery.
                raise ProcessKilled()
            return self.json_response(201, {"id": key, "key": key})
        if method == "GET" and path.startswith("/rest/api/3/search/jql"):
            if self.fail_search_times > 0:
                self.fail_search_times -= 1
                headers = {}
                if self.retry_after_raw is not None:
                    headers[self.retry_after_header] = self.retry_after_raw
                elif self.retry_after is not None:
                    headers[self.retry_after_header] = str(self.retry_after)
                payload = {"errorMessages": ["rate limited"]}
                if self.retry_after_body is not None:
                    payload["parameters"] = {"retry_after": self.retry_after_body}
                return self.json_response(429, payload, headers)
            if self.search_status:
                return self.json_response(self.search_status, {"errorMessages": ["boom"]})
            if self.search_body is not None:
                return self.json_response(200, self.search_body)
            return self.json_response(200, {"issues": self._search_results(path)})
        if method == "PUT" and "/properties/figmapot.link" in path:
            if self.fail_property_times > 0:
                self.fail_property_times -= 1
                if self.property_retry_after is not None:
                    return self.json_response(
                        429, {"errorMessages": ["rate limited"]},
                        {self.retry_after_header: str(self.property_retry_after)},
                    )
                return self.json_response(500, {"errorMessages": ["boom"]})
            return self.json_response(200, {})
        return self.json_response(404, {"errorMessages": ["unknown"]})

    def _search_results(self, path):
        if self.search_issues is not None:
            selected = self.search_issues
        else:
            match = re.search(r"figmapot-c-([0-9a-zA-Z\-]+)", path)
            label = "figmapot-c-" + match.group(1) if match else None
            selected = [i for i in self.issues if label in i["fields"].get("labels", [])]
        return [
            {"key": item["key"], "fields": {"created": item["fields"].get("created")}} for item in selected
        ]


class FakeTelegram(FakeHttp):
    def __init__(self):
        self.messages = []
        self.delivered = []
        self.updates = []
        self.username = "figmapot_bot"
        self.fail_updates = False
        self.fail_times = 0
        self.fail_status = 500
        self.retry_after = None
        super().__init__()

    def route(self, method, path, headers, body):
        if method == "POST" and "/getUpdates" in path:
            if self.fail_updates:
                return self.json_response(200, {"ok": False, "description": "boom"})
            # Offsets are the poller's business; the fake replays its batch.
            return self.json_response(200, {"ok": True, "result": list(self.updates)})
        if method == "POST" and "/getMe" in path:
            return self.json_response(200, {"ok": True, "result": {"username": self.username}})
        if method != "POST" or "/sendMessage" not in path:
            return self.json_response(404, {"ok": False})
        data = json.loads(body.decode("utf-8"))
        self.messages.append(data)
        if self.fail_times > 0:
            self.fail_times -= 1
            if self.retry_after is not None:
                return self.json_response(429, {"ok": False, "parameters": {"retry_after": self.retry_after}})
            return self.json_response(self.fail_status, {"ok": False, "description": "boom"})
        self.delivered.append(data)
        return self.json_response(200, {"ok": True, "result": {"message_id": len(self.messages)}})
