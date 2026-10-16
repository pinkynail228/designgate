#!/usr/bin/env python3
"""Responses-API adapter for Yandex AI Studio.

Codex sends `text.format.strict=true` when a node declares a structured output.
Yandex AI Studio forbids tool calls while a strict json_schema format is set, so
the model answers with plain JSON instead of calling tools and a Zeroshot
worker does no real work. This adapter rewrites `strict` to `false` and proxies
the stream to Yandex unchanged. With `strict=false` tool calls work and the
final message still carries the schema-shaped JSON.

A second rewrite handles the "empty visit": when the schema is present in the
very first request of a fresh session (no tool outputs and no assistant turn in
`input`), deepseek-v4.1-flash sometimes answers the bare envelope at once
(`{"response": null}` in 2-4 s) without running a single command. The adapter
drops `text.format` from such first requests only; every later request of the
turn carries it again, so the final message is still schema-shaped.

Model alias: the runtime names the model `deepseek-v4.1-flash` (no folder id in tracked files or run records);
the adapter sends `gpt://<YANDEX_FOLDER_ID>/<alias>` upstream and masks the folder id in the streamed answer
(a per-request redactor holds back a possible partial match between reads).
`GET /designgate-adapter/health` identifies the adapter without a provider call (run.sh readiness check).
Reads YANDEX_API_KEY and YANDEX_FOLDER_ID from the environment (devtools/zeroshot/adapter.sh). No secret lives in
the repo. Copied from the owner's adapter (2026-10-15) and kept separate: own port and log.
"""

import http.client
import http.server
import json
import os
import sys

UP_HOST = "ai.api.cloud.yandex.net"
PORT = int(os.environ.get("ADAPTER_PORT", "8798"))
KEY = os.environ.get("YANDEX_API_KEY", "")
FOLDER = os.environ.get("YANDEX_FOLDER_ID", "")
LOG = os.environ.get("ADAPTER_LOG", "")


def log(message: str) -> None:
    if LOG:
        with open(LOG, "a") as handle:
            handle.write(message + "\n")


HEALTH_PATH = "/designgate-adapter/health"
HEALTH_BODY = b'{"adapter":"designgate-yandex"}'


def upstream_connection() -> http.client.HTTPConnection:
    return http.client.HTTPSConnection(UP_HOST, timeout=600)


class Redactor:
    """Replace `secret` in a byte stream split at arbitrary points; one instance per response."""

    def __init__(self, secret: bytes, mask: bytes = b"<folder>"):
        self.secret, self.mask, self.rest = secret, mask, b""

    def feed(self, chunk: bytes) -> bytes:
        if not self.secret:
            return chunk
        data = (self.rest + chunk).replace(self.secret, self.mask)
        keep = 0
        for size in range(min(len(self.secret) - 1, len(data)), 0, -1):
            if self.secret.startswith(data[-size:]):
                keep = size
                break
        self.rest = data[len(data) - keep:] if keep else b""
        return data[:len(data) - keep]

    def flush(self) -> bytes:
        rest, self.rest = self.rest, b""
        return rest


def _fresh_session(data: dict) -> bool:
    """True only for a confirmed first model call: no previous response, only user/developer/system messages."""
    items = data.get("input")
    if data.get("previous_response_id") or not isinstance(items, list) or not items:
        return False
    for item in items:
        if not isinstance(item, dict) or item.get("type", "message") != "message":
            return False
        if item.get("role") not in ("user", "developer", "system"):
            return False
    return True


def rewrite_request(raw: bytes) -> tuple[bytes, dict]:
    """Return the body to forward upstream and a small record for the log."""
    info: dict = {}
    try:
        data = json.loads(raw)
    except Exception:
        return raw, info
    if not isinstance(data, dict):
        return raw, info
    model = data.get("model")
    if FOLDER and isinstance(model, str) and not model.startswith("gpt://"):
        data["model"] = "gpt://%s/%s" % (FOLDER, model)
        info["model"] = "alias"
        raw = json.dumps(data).encode()
    text = data.get("text")
    fmt = text.get("format") if isinstance(text, dict) else None
    if not (isinstance(fmt, dict) and fmt.get("type") == "json_schema"):
        return raw, info
    info["strict_before"] = fmt.get("strict", "<absent>")
    if _fresh_session(data):
        del text["format"]
        if not text:
            del data["text"]
        info["format"] = "stripped_first_call"
    else:
        fmt["strict"] = False
        info["format"] = "strict_false"
    return json.dumps(data).encode(), info


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        found = self.path == HEALTH_PATH
        body = HEALTH_BODY if found else b"{}"
        self.send_response(200 if found else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        raw, info = rewrite_request(raw)
        log(f"POST {self.path} {info}")

        conn = upstream_connection()
        headers = {
            "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
            "Accept": self.headers.get("Accept", "text/event-stream"),
        }
        try:
            conn.request("POST", self.path, body=raw, headers=headers)
            up = conn.getresponse()
        except Exception:  # upstream transport failure; never log the header/key
            log("upstream transport error")
            conn.close()
            body = json.dumps(
                {"error": {"type": "adapter_error", "message": "upstream transport error"}}
            ).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(up.status)
        self.send_header("Content-Type", up.getheader("Content-Type", "application/json"))
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        redactor = Redactor(FOLDER.encode())
        try:
            # read1 forwards each available SSE chunk promptly instead of
            # buffering until 1024 bytes, so Codex never sees a stalled stream.
            while True:
                chunk = up.read1(1024)
                if not chunk:
                    break
                self._send_chunk(redactor.feed(chunk))
            if up.length:  # Content-Length body cut short: read1 returns b"" instead of raising
                raise http.client.IncompleteRead(b"", up.length)
            self._send_chunk(redactor.flush())
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except Exception:
            # Mid-stream failure: abort the client connection instead of
            # pretending the chunked response completed.
            log("stream interrupted; aborting client connection")
            self.close_connection = True
        finally:
            conn.close()

    def _send_chunk(self, chunk: bytes) -> None:
        if chunk:
            self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
            self.wfile.flush()


def main() -> int:
    if not KEY:
        print("YANDEX_API_KEY is not set", file=sys.stderr)
        return 2
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    log(f"adapter listening on 127.0.0.1:{PORT}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
