"""AC-1..AC-4: Jira lifecycle calls (assignee, due date, issue state).

No sockets: a small in-file fake opener answers canned HTTP replies and records
the requests, in the spirit of ``test_requests_send_own_user_agent``.
"""
from __future__ import annotations

import base64
import datetime
import io
import json
import os
import sys
import unittest
import urllib.error
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from figmapot_bot.adapters import JiraClient, JiraError
from figmapot_bot.clock import FakeClock
from figmapot_bot.httpclient import RetryableError
from figmapot_bot.jira_tasks import (
    create_issue_with,
    is_assignable,
    issue_fields,
    read_issues,
)

from tests.support import make_logger


class FakeReply:
    """Minimal stand-in for ``opener.open(...)``'s context manager."""

    def __init__(self, status, body, headers=None):
        self.status = status
        self._headers = dict(headers or {})
        if isinstance(body, bytes):
            self._body = body
        elif isinstance(body, str):
            self._body = body.encode("utf-8")
        else:
            self._body = json.dumps(body).encode("utf-8")

    @property
    def headers(self):
        return dict(self._headers)

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Answers queued replies (or raises queued exceptions) and records requests."""

    def __init__(self, *replies):
        self.requests = []
        self.replies = list(replies)

    def open(self, request, timeout=None):
        self.requests.append(request)
        if not self.replies:
            raise AssertionError("unexpected request to %s" % request.full_url)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def body(self, index=0):
        return json.loads(self.requests[index].data.decode("utf-8"))

    def url(self, index=0):
        return self.requests[index].full_url


def make_client(*replies):
    stream = io.StringIO()
    client = JiraClient(
        "http://jira.test", "bot@example.com", "secret-token", "SCRUM", "Task",
        FakeClock(), make_logger(stream),
    )
    client.opener = FakeOpener(*replies)
    client.log_stream = stream
    return client


def issue(key, **fields):
    return {"id": key, "key": key, "fields": fields}


def page(issues, next_page_token=None, is_last=True):
    body = {"issues": issues, "isLast": is_last}
    if next_page_token is not None:
        body["nextPageToken"] = next_page_token
    return FakeReply(200, body)


def query_of(url):
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


class IssueFieldsTests(unittest.TestCase):
    def test_fields_match_create_issue_today(self):
        client = make_client()
        description = {"type": "doc", "version": 1, "content": []}
        fields = issue_fields(client, "Fix header", description, ("figmapot", "figmapot-c-1"))
        self.assertEqual(fields, {
            "project": {"key": "SCRUM"},
            "issuetype": {"name": "Task"},
            "summary": "Fix header",
            "description": description,
            "labels": ["figmapot", "figmapot-c-1"],
        })
        self.assertNotIn("assignee", fields)
        self.assertNotIn("duedate", fields)

    def test_assignee_and_due_are_added_only_when_given(self):
        client = make_client()
        fields = issue_fields(
            client, "s", {"type": "doc"}, [], assignee_account_id="acc-1",
            due=datetime.date(2026, 11, 1),
        )
        self.assertEqual(fields["assignee"], {"accountId": "acc-1"})
        self.assertEqual(fields["duedate"], "2026-11-01")

        fields = issue_fields(client, "s", {"type": "doc"}, [], due="2026-12-31")
        self.assertEqual(fields["duedate"], "2026-12-31")
        self.assertNotIn("assignee", fields)

    def test_create_issue_with_posts_the_fields_once(self):
        client = make_client(FakeReply(201, {"id": "10001", "key": "SCRUM-7"}))
        fields = issue_fields(
            client, "T", {"type": "doc"}, ["l"], assignee_account_id="acc-1", due="2026-11-01"
        )
        created = create_issue_with(client, fields)
        self.assertEqual(len(client.opener.requests), 1)
        self.assertEqual(client.opener.requests[0].get_method(), "POST")
        self.assertEqual(client.opener.url(), "http://jira.test/rest/api/3/issue")
        self.assertEqual(client.opener.body(), {"fields": fields})
        self.assertEqual(client.opener.body()["fields"]["assignee"], {"accountId": "acc-1"})
        self.assertEqual(created.key, "SCRUM-7")
        self.assertEqual(created.url, "http://jira.test/browse/SCRUM-7")

    def test_create_issue_with_raises_on_unknown_outcome_without_retrying(self):
        client = make_client(urllib.error.URLError("connection reset"))
        with self.assertRaises(JiraError):
            create_issue_with(client, issue_fields(client, "T", {}, []))
        self.assertEqual(len(client.opener.requests), 1)

    def test_create_issue_with_defers_a_rate_limit_without_retrying(self):
        client = make_client(
            FakeReply(429, {"errorMessages": ["rate limited"]}, {"Retry-After": "7"})
        )
        with self.assertRaises(RetryableError) as caught:
            create_issue_with(client, issue_fields(client, "T", {}, []))
        self.assertEqual(len(client.opener.requests), 1)
        self.assertEqual(caught.exception.delay, 7.0)

    def test_create_issue_with_raises_on_a_client_error(self):
        client = make_client(FakeReply(400, {"errorMessages": ["bad request"]}))
        with self.assertRaises(JiraError) as caught:
            create_issue_with(client, issue_fields(client, "T", {}, []))
        self.assertEqual(caught.exception.status, 400)


class AssignableTests(unittest.TestCase):
    def test_true_when_the_account_is_listed(self):
        client = make_client(
            FakeReply(200, [{"accountId": "acc-1", "accountType": "atlassian", "active": True}])
        )
        self.assertTrue(is_assignable(client, "acc-1"))
        self.assertEqual(client.opener.requests[0].get_method(), "GET")
        self.assertEqual(
            client.opener.url(),
            "http://jira.test/rest/api/3/user/assignable/search?project=SCRUM&accountId=acc-1",
        )

    def test_false_when_the_list_is_empty(self):
        client = make_client(FakeReply(200, []))
        self.assertFalse(is_assignable(client, "acc-1"))

    def test_false_when_only_a_different_account_is_listed(self):
        client = make_client(FakeReply(200, [{"accountId": "acc-2", "active": True}]))
        self.assertFalse(is_assignable(client, "acc-1"))

    def test_raises_on_an_http_error(self):
        client = make_client(FakeReply(401, {"errorMessages": ["unauthorized"]}))
        with self.assertRaises(JiraError) as caught:
            is_assignable(client, "acc-1")
        self.assertEqual(caught.exception.status, 401)

    def test_raises_on_a_body_that_is_not_a_list(self):
        client = make_client(FakeReply(200, {"accountId": "acc-1"}))
        with self.assertRaises((JiraError, RetryableError)):
            is_assignable(client, "acc-1")


class ReadIssuesTests(unittest.TestCase):
    def test_reports_state_from_status_category_key(self):
        client = make_client(page([issue(
            "SCRUM-5",
            summary="Something",
            assignee=None,
            updated="2026-10-20T16:43:43.618+0300",
            duedate=None,
            status={"name": "К выполнению", "statusCategory": {"id": 2, "key": "new", "name": "К выполнению"}},
        )]))
        result = read_issues(client, ["SCRUM-5"])
        self.assertEqual(result, {"SCRUM-5": {
            "status": "К выполнению",
            "status_category": "new",
            "assignee_account_id": None,
            "duedate": None,
            "summary": "Something",
            "updated": "2026-10-20T16:43:43.618+0300",
        }})

    def test_query_uses_key_in_and_the_needed_fields(self):
        client = make_client(page([]))
        read_issues(client, ["SCRUM-5", "SCRUM-6"])
        query = query_of(client.opener.url())
        self.assertEqual(query["jql"], ["key in (SCRUM-5, SCRUM-6)"])
        self.assertIn("status", query["fields"][0])
        self.assertIn("assignee", query["fields"][0])
        self.assertIn("duedate", query["fields"][0])

    def test_assigned_issue_exposes_the_account_id(self):
        client = make_client(page([issue("SCRUM-5", assignee={"accountId": "acc-9"}, status={})]))
        result = read_issues(client, ["SCRUM-5"])
        self.assertEqual(result["SCRUM-5"]["assignee_account_id"], "acc-9")

    def test_follows_next_page_token_until_absent(self):
        client = make_client(
            page([issue("SCRUM-5", summary="a")], next_page_token="token-1", is_last=False),
            page([issue("SCRUM-6", summary="b")], is_last=True),
        )
        result = read_issues(client, ["SCRUM-5", "SCRUM-6"])
        self.assertEqual(set(result), {"SCRUM-5", "SCRUM-6"})
        self.assertEqual(result["SCRUM-6"]["summary"], "b")
        self.assertEqual(len(client.opener.requests), 2)
        self.assertNotIn("nextPageToken", query_of(client.opener.url(0)))
        self.assertEqual(query_of(client.opener.url(1))["nextPageToken"], ["token-1"])

    def test_chunks_keys_at_most_50_per_query(self):
        keys = ["SCRUM-%d" % number for number in range(1, 52)]
        client = make_client(
            page([issue(key) for key in keys[:50]], is_last=True),
            page([issue(keys[50])], is_last=True),
        )
        result = read_issues(client, keys)
        self.assertEqual(len(client.opener.requests), 2)
        self.assertIsNone(result[keys[50]]["status"])
        for index in range(2):
            jql = query_of(client.opener.url(index))["jql"][0]
            self.assertLessEqual(jql.count(",") + 1, 50)

    def test_no_keys_makes_no_request(self):
        client = make_client()
        self.assertEqual(read_issues(client, []), {})
        self.assertEqual(client.opener.requests, [])

    def test_a_key_missing_from_the_answer_is_not_done(self):
        # The answer only holds SCRUM-5 (Done). SCRUM-404 does not exist and the
        # server answers 200 with it absent: it must be reported as missing,
        # never as Done.
        client = make_client(page([issue(
            "SCRUM-5",
            status={"name": "Готово", "statusCategory": {"key": "done", "name": "Готово"}},
        )]))
        result = read_issues(client, ["SCRUM-5", "SCRUM-404"])
        self.assertEqual(result["SCRUM-5"]["status_category"], "done")
        self.assertIsNone(result["SCRUM-404"])

    def test_raises_on_an_http_error_without_returning_a_partial_result(self):
        keys = ["SCRUM-%d" % number for number in range(1, 52)]
        client = make_client(
            page([issue(key) for key in keys[:50]], is_last=True),
            FakeReply(400, {"errorMessages": ["bad jql"]}),
        )
        with self.assertRaises(JiraError):
            read_issues(client, keys)

    def test_raises_on_an_invalid_json_body(self):
        client = make_client(FakeReply(200, b"{not json"))
        with self.assertRaises((JiraError, RetryableError)):
            read_issues(client, ["SCRUM-5"])

    def test_raises_on_a_malformed_body(self):
        client = make_client(FakeReply(200, {"issue": []}))
        with self.assertRaises((JiraError, RetryableError)):
            read_issues(client, ["SCRUM-5"])


class SecretTests(unittest.TestCase):
    def test_api_token_never_appears_in_errors_or_logs(self):
        secrets = [
            "secret-token",
            base64.b64encode(b"bot@example.com:secret-token").decode("ascii"),
        ]
        client = make_client(FakeReply(400, {"errorMessages": ["bad request"]}))
        with self.assertRaises(JiraError) as caught:
            read_issues(client, ["SCRUM-5"])
        for secret in secrets:
            self.assertNotIn(secret, str(caught.exception))
            self.assertNotIn(secret, repr(caught.exception))
            self.assertNotIn(secret, client.log_stream.getvalue())

    def test_transport_error_has_no_token(self):
        client = make_client(urllib.error.URLError("connection refused"))
        with self.assertRaises(JiraError) as caught:
            create_issue_with(client, issue_fields(client, "T", {}, []))
        self.assertNotIn("secret-token", str(caught.exception))
        self.assertNotIn("secret-token", client.log_stream.getvalue())


if __name__ == "__main__":
    unittest.main()
