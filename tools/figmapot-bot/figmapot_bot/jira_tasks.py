"""Jira Cloud calls for the task lifecycle: assignee, due date and state.

They take an existing :class:`~figmapot_bot.adapters.JiraClient` and reuse its
``opener``, ``base_url``, ``_headers()``, ``clock`` and ``timeout``. The reads go
through ``request_with_retry`` (a transient failure is retried) while the create
uses exactly one attempt, like ``JiraClient.create_issue``: a transport failure
leaves the issue's outcome unknown, so it is raised and never retried.

Nothing here is wired into the bot loop; that is another card.
"""
from __future__ import annotations

import datetime
import json
from urllib.parse import urlencode

from .adapters import Issue, JiraError
from .httpclient import (
    RETRY_STATUSES,
    RetryableError,
    TransportError,
    do_request,
    request_with_retry,
    retry_after_seconds,
)

# ``search/jql`` is queried with at most this many keys per request.
SEARCH_CHUNK = 50
# The fields the caller needs back; ``statusCategory.key`` is the locale-neutral
# state (status names are localised, e.g. Russian on our site).
SEARCH_FIELDS = "summary,status,assignee,duedate,updated"


def issue_fields(client, summary, description, labels, assignee_account_id=None, due=None):
    """Build the ``fields`` body of a create-issue call.

    Same shape as ``JiraClient.create_issue`` (project/issuetype/summary/
    description/labels), plus ``assignee`` and ``duedate`` only when given.
    """
    fields = {
        "project": {"key": client.project_key},
        "issuetype": {"name": client.issue_type},
        "summary": summary,
        "description": description,
        "labels": list(labels),
    }
    if assignee_account_id is not None:
        fields["assignee"] = {"accountId": assignee_account_id}
    duedate = _duedate(due)
    if duedate is not None:
        fields["duedate"] = duedate
    return fields


def create_issue_with(client, fields):
    """Create an issue from a prepared ``fields`` dict, with one attempt.

    Mirrors ``JiraClient.create_issue``: a transport failure (unknown outcome)
    or a non-retryable error is raised, a retryable status becomes a
    ``RetryableError`` so the caller reschedules instead of hammering Jira.
    """
    body = json.dumps({"fields": fields}).encode("utf-8")
    url = client.base_url + "/rest/api/3/issue"
    try:
        response = do_request(
            client.opener, "POST", url, data=body, headers=client._headers(), timeout=client.timeout
        )
    except TransportError as error:
        raise JiraError("jira issue creation failed: %s" % error)
    if response.status in RETRY_STATUSES:
        # Keep the server's retry deadline so the caller can defer instead of
        # hammering Jira (and so a 429 is never mistaken for success).
        raise RetryableError(
            "jira issue creation failed (status=%s)" % response.status,
            delay=retry_after_seconds(response, client.clock),
            status=response.status,
        )
    if response.status >= 400:
        raise JiraError("jira issue creation failed (status=%s)" % response.status, response.status)
    payload = response.json()
    key = payload["key"]
    return Issue(key=key, url=client.base_url + "/browse/" + key)


def is_assignable(client, account_id):
    """True when ``account_id`` may be assigned in the client's project.

    A well-formed answer (a JSON list) decides True/False; anything else --
    a failing status, an unparseable or non-list body -- raises.
    """
    query = urlencode({"project": client.project_key, "accountId": account_id})
    url = client.base_url + "/rest/api/3/user/assignable/search?" + query
    response = request_with_retry(
        client.opener, "GET", url, headers=client._headers(), timeout=client.timeout,
        clock=client.clock, rng=client.rng,
    )
    if response.status >= 400:
        raise JiraError("jira assignable search failed (status=%s)" % response.status, response.status)
    try:
        payload = response.json()
    except Exception:
        raise RetryableError("jira assignable search returned an invalid body", status=response.status)
    if not isinstance(payload, list):
        # A 200 without a list is a failure, never "not assignable".
        raise RetryableError("jira assignable search returned a malformed body", status=response.status)
    return any(
        isinstance(entry, dict) and entry.get("accountId") == account_id for entry in payload
    )


def read_issues(client, keys):
    """Return ``key -> state`` for the given issue keys.

    ``state`` is ``{status, status_category, assignee_account_id, duedate,
    summary, updated}``; ``status_category`` comes from ``statusCategory.key``
    (locale-neutral: ``new`` / ``indeterminate`` / ``done``). A requested key
    that the answer does not contain is reported as missing (``None``), never as
    Done. Any HTTP or JSON failure raises: a partial answer is not returned.
    """
    keys = list(keys)
    result = {key: None for key in keys}
    for start in range(0, len(keys), SEARCH_CHUNK):
        _read_chunk(client, keys[start:start + SEARCH_CHUNK], result)
    return result


def _read_chunk(client, chunk, result):
    jql = "key in (%s)" % ", ".join(chunk)
    token = None
    while True:
        params = {"jql": jql, "fields": SEARCH_FIELDS}
        if token is not None:
            params["nextPageToken"] = token
        url = client.base_url + "/rest/api/3/search/jql?" + urlencode(params)
        response = request_with_retry(
            client.opener, "GET", url, headers=client._headers(), timeout=client.timeout,
            clock=client.clock, rng=client.rng,
        )
        if response.status >= 400:
            raise JiraError("jira issue read failed (status=%s)" % response.status, response.status)
        try:
            payload = response.json()
        except Exception:
            raise RetryableError("jira issue read returned an invalid body", status=response.status)
        if not isinstance(payload, dict) or not isinstance(payload.get("issues"), list):
            # A 200 without an ``issues`` array is a failure, never an empty
            # answer: returning it would drop requested keys as "missing".
            raise RetryableError("jira issue read returned a malformed body", status=response.status)
        for item in payload["issues"]:
            key = item.get("key")
            if key in result:
                result[key] = _issue_state(item.get("fields") or {})
        token = payload.get("nextPageToken")
        if not token:
            return


def _issue_state(fields):
    status = fields.get("status") or {}
    category = status.get("statusCategory") or {}
    assignee = fields.get("assignee") or {}
    return {
        "status": status.get("name"),
        "status_category": category.get("key"),
        "assignee_account_id": assignee.get("accountId"),
        "duedate": fields.get("duedate"),
        "summary": fields.get("summary"),
        "updated": fields.get("updated"),
    }


def _duedate(due):
    if due is None:
        return None
    if isinstance(due, datetime.datetime):
        return due.date().isoformat()
    if isinstance(due, datetime.date):
        return due.isoformat()
    return str(due)
