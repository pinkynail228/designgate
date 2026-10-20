"""Thin stdlib HTTP helpers with timeouts, retries and backoff.

Only ``urllib`` is used. Every call has a timeout; GETs, searches and
deliveries retry with exponential backoff and jitter. HTTP 429 honours the
``Retry-After`` header and Jira/Telegram JSON hints.
"""
from __future__ import annotations

import json
import datetime
import email.utils
import http.client
import random
import socket
import time
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 15.0
RETRY_STATUSES = (429, 500, 502, 503, 504)
# Cap the exponential's exponent so ``2 ** attempt`` cannot overflow a float
# for large attempt counters (a job may retry for a very long time).
MAX_BACKOFF_EXPONENT = 63


class Headers(dict):
    """A header mapping whose lookups ignore case."""

    def get(self, key, default=None):
        wanted = str(key).lower()
        for name, value in self.items():
            if str(name).lower() == wanted:
                return value
        return default


class TransportError(Exception):
    """A network-level failure (timeout, reset, DNS)."""


class RequestError(Exception):
    """A malformed request (bad URL or headers) that must not be retried.

    The message deliberately omits the URL: ``http.client.InvalidURL`` embeds
    the full URL, which can contain a bot token.
    """


class RetryableError(Exception):
    """A delivery attempt that should be retried later.

    ``delay`` carries the server-provided wait (``Retry-After`` or Telegram
    ``parameters.retry_after``) when present; the caller schedules the retry
    through the job's ``not_before`` instead of sleeping inline.
    """

    def __init__(self, message, delay=None, status=None):
        super().__init__(message)
        self.delay = delay
        self.status = status


class Response:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = Headers(headers or {})
        self.body = body or b""

    def json(self):
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))


def do_request(opener, method, url, data=None, headers=None, timeout=DEFAULT_TIMEOUT):
    """Perform exactly one request. HTTP errors become ``Response`` objects."""
    try:
        request = urllib.request.Request(url, data=data, method=method)
        for name, value in (headers or {}).items():
            request.add_header(name, value)
    except (ValueError, http.client.InvalidURL):
        raise RequestError("invalid request (bad url or headers)") from None
    try:
        with opener.open(request, timeout=timeout) as response:
            return Response(response.status, dict(response.headers), response.read())
    except urllib.error.HTTPError as error:
        body = b""
        try:
            body = error.read()
        except Exception:
            pass
        return Response(error.code, dict(error.headers or {}), body)
    except http.client.InvalidURL:
        # The raw message includes the URL (and any token in it).
        raise RequestError("invalid request url") from None
    except (urllib.error.URLError, socket.timeout, OSError) as error:
        raise TransportError(str(error))


def backoff_delay(attempt, base=1.0, cap=60.0, rng=None):
    """Exponential backoff with full jitter."""
    rng = rng or random
    exponent = min(max(int(attempt), 0), MAX_BACKOFF_EXPONENT)
    ceiling = min(cap, base * (2.0 ** exponent))
    return ceiling * (1.0 + rng.uniform(0.0, 0.5))


def _http_date_delay(value, clock):
    """Delay until an HTTP-date ``Retry-After`` value, or ``None``."""
    try:
        parsed = email.utils.parsedate_to_datetime(str(value).strip())
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    now = clock.now() if clock is not None else time.time()
    return max(0.0, parsed.timestamp() - now)


def retry_after_seconds(response, clock=None):
    """Read a retry delay from the header and/or a JSON ``retry_after`` hint.

    The header may be a number of seconds or an HTTP-date; the date is resolved
    against the injectable clock. Both sources are honoured: the caller waits
    for the longest valid deadline, so a ``Retry-After`` header can never
    shorten a Telegram ``parameters.retry_after`` (or the other way round).
    """
    candidates = []
    header = response.headers.get("Retry-After")
    if header:
        try:
            candidates.append(float(header))
        except (TypeError, ValueError):
            delay = _http_date_delay(header, clock)
            if delay is not None:
                candidates.append(delay)
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except Exception:
        payload = None
    parameters = payload.get("parameters") if isinstance(payload, dict) else None
    if isinstance(parameters, dict) and parameters.get("retry_after") is not None:
        try:
            candidates.append(float(parameters["retry_after"]))
        except (TypeError, ValueError):
            pass
    if not candidates:
        return None
    return max(candidates)


def request_with_retry(
    opener,
    method,
    url,
    data=None,
    headers=None,
    timeout=DEFAULT_TIMEOUT,
    clock=None,
    max_attempts=6,
    base=1.0,
    cap=60.0,
    rng=None,
    retry_statuses=RETRY_STATUSES,
):
    """Run a request, retrying transport errors and retryable statuses."""
    if clock is None:
        raise ValueError("request_with_retry needs a clock")
    attempt = 0
    while True:
        try:
            response = do_request(opener, method, url, data=data, headers=headers, timeout=timeout)
        except TransportError:
            if attempt >= max_attempts - 1:
                raise
            clock.sleep(backoff_delay(attempt, base, cap, rng))
            attempt += 1
            continue
        if response.status in retry_statuses:
            delay = retry_after_seconds(response, clock)
            if delay is None:
                delay = backoff_delay(attempt, base, cap, rng)
            if attempt >= max_attempts - 1:
                # Hand the server's last deadline to the caller so the next
                # poll does not retry before the server asked us to.
                raise RetryableError(
                    "retryable status %s" % response.status, delay=delay, status=response.status
                )
            clock.sleep(delay)
            attempt += 1
            continue
        return response


def single_request(opener, method, url, data=None, headers=None, timeout=DEFAULT_TIMEOUT, clock=None):
    """One delivery attempt, never sleeping.

    A transport failure or a retryable status raises ``RetryableError`` so the
    caller can reschedule the job without blocking other deliveries.
    """
    try:
        response = do_request(opener, method, url, data=data, headers=headers, timeout=timeout)
    except TransportError as error:
        raise RetryableError("transport error: %s" % error)
    if response.status in RETRY_STATUSES:
        raise RetryableError(
            "retryable status %s" % response.status,
            delay=retry_after_seconds(response, clock),
            status=response.status,
        )
    return response


def build_opener(cookiejar=None):
    handlers = []
    if cookiejar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(cookiejar))
    return urllib.request.build_opener(*handlers)
