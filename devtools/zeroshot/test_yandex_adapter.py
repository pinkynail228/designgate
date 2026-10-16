"""Offline tests for the Yandex adapter: request rewrite, stream redaction, transport (no network).

Run: python3 -m unittest discover -s devtools/zeroshot
"""

import http.client
import http.server
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yandex_adapter  # noqa: E402
from yandex_adapter import Redactor, rewrite_request  # noqa: E402

SCHEMA = {"type": "json_schema", "strict": True, "schema": {"type": "object"}}


def body(items, fmt=SCHEMA):
    return json.dumps({"model": "m", "input": items, "text": {"format": fmt}}).encode()


class RewriteTest(unittest.TestCase):
    def test_first_call_of_fresh_session_drops_the_format(self):
        raw, info = rewrite_request(body([{"type": "message", "role": "user", "content": "task"}]))
        self.assertEqual(info["format"], "stripped_first_call")
        self.assertNotIn("text", json.loads(raw))

    def test_call_after_tool_output_keeps_schema_with_strict_false(self):
        items = [
            {"type": "message", "role": "user", "content": "task"},
            {"type": "function_call", "name": "shell", "arguments": "{}"},
            {"type": "function_call_output", "output": "ok"},
        ]
        raw, info = rewrite_request(body(items))
        self.assertEqual(info["format"], "strict_false")
        self.assertIs(json.loads(raw)["text"]["format"]["strict"], False)

    def test_correction_after_assistant_message_keeps_schema(self):
        items = [
            {"type": "message", "role": "user", "content": "task"},
            {"type": "message", "role": "assistant", "content": "prose summary"},
            {"type": "message", "role": "user", "content": "answer with JSON"},
        ]
        _raw, info = rewrite_request(body(items))
        self.assertEqual(info["format"], "strict_false")

    def test_not_fresh_cases_keep_the_schema(self):
        user = {"type": "message", "role": "user", "content": "task"}
        cases = {
            "assistant without type": [user, {"role": "assistant", "content": "x"}],
            "unknown item type": [user, {"type": "custom_tool_call"}],
            "non-dict item": [user, "text"],
            "empty input": [],
        }
        for label, items in cases.items():
            with self.subTest(label):
                _raw, info = rewrite_request(body(items))
                self.assertEqual(info["format"], "strict_false")
        data = json.loads(body([user]))
        data["previous_response_id"] = "resp_1"
        _raw, info = rewrite_request(json.dumps(data).encode())
        self.assertEqual(info["format"], "strict_false")

    def test_developer_and_untyped_user_messages_are_fresh(self):
        items = [{"type": "message", "role": "developer", "content": "rules"}, {"role": "user", "content": "task"}]
        _raw, info = rewrite_request(body(items))
        self.assertEqual(info["format"], "stripped_first_call")

    def test_request_without_schema_passes_unchanged(self):
        raw = json.dumps({"model": "m", "input": []}).encode()
        out, info = rewrite_request(raw)
        self.assertEqual(out, raw)
        self.assertEqual(info, {})


class AliasTest(unittest.TestCase):
    def test_alias_becomes_full_uri_and_plain_body_is_rewritten(self):
        import yandex_adapter
        yandex_adapter.FOLDER = "f123"
        try:
            raw, info = rewrite_request(json.dumps({"model": "deepseek-v4.1-flash", "input": []}).encode())
            self.assertEqual(json.loads(raw)["model"], "gpt://f123/deepseek-v4.1-flash")
            self.assertEqual(info["model"], "alias")
            raw, _ = rewrite_request(json.dumps({"model": "gpt://f123/x", "input": []}).encode())
            self.assertEqual(json.loads(raw)["model"], "gpt://f123/x")
        finally:
            yandex_adapter.FOLDER = ""


class RedactorTest(unittest.TestCase):
    def test_every_split_point_is_masked(self):
        stream = b"data: gpt://f123456/m and again f123456 end"
        for cut in range(len(stream) + 1):
            with self.subTest(cut=cut):
                red = Redactor(b"f123456")
                out = red.feed(stream[:cut]) + red.feed(stream[cut:]) + red.flush()
                self.assertEqual(out, stream.replace(b"f123456", b"<folder>"))

    def test_byte_by_byte_and_partial_tail_flushed(self):
        red = Redactor(b"abc")
        out = b"".join(red.feed(bytes([b])) for b in b"xabcyab") + red.flush()
        self.assertEqual(out, b"x<folder>yab")


class Upstream(http.server.BaseHTTPRequestHandler):
    """Fake Yandex: echoes the request tag; `/error` -> 400, `/cut` -> disconnect mid-stream."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        tag = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["tag"]
        if self.path == "/error":
            body = b'{"error":"bad"}'
            self.send_response(400)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        if self.path == "/cut":
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b"data: partial f12")
            self.wfile.flush()
            self.close_connection = True
            return
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for part in (b"data: %s gpt://f12" % tag.encode(), b"345/m\n\n"):
            self.wfile.write(b"%x\r\n%s\r\n" % (len(part), part))
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")


class TransportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.up = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        cls.ad = http.server.ThreadingHTTPServer(("127.0.0.1", 0), yandex_adapter.Handler)
        for server in (cls.up, cls.ad):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.saved = yandex_adapter.upstream_connection, yandex_adapter.FOLDER
        port = cls.up.server_address[1]
        yandex_adapter.upstream_connection = lambda: http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        yandex_adapter.FOLDER = "f12345"

    @classmethod
    def tearDownClass(cls):
        yandex_adapter.upstream_connection, yandex_adapter.FOLDER = cls.saved
        for server in (cls.up, cls.ad):
            server.shutdown()
            server.server_close()

    def post(self, path, tag):
        conn = http.client.HTTPConnection("127.0.0.1", self.ad.server_address[1], timeout=10)
        try:
            conn.request("POST", path, body=json.dumps({"model": "gpt://x/m", "tag": tag}))
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def test_health_identifies_the_adapter(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.ad.server_address[1], timeout=10)
        conn.request("GET", yandex_adapter.HEALTH_PATH)
        resp = conn.getresponse()
        self.assertEqual((resp.status, resp.read()), (200, yandex_adapter.HEALTH_BODY))
        conn.close()

    def test_stream_is_forwarded_and_folder_masked_across_chunks(self):
        status, data = self.post("/v1/responses", "t0")
        self.assertEqual(status, 200)
        self.assertEqual(data, b"data: t0 gpt://<folder>/m\n\n")

    def test_upstream_http_error_is_passed_through(self):
        self.assertEqual(self.post("/error", "t"), (400, b'{"error":"bad"}'))

    def test_midstream_disconnect_is_not_reported_as_complete(self):
        with self.assertRaises(http.client.HTTPException):
            self.post("/cut", "t")

    def test_concurrent_requests_do_not_mix(self):
        results = {}

        def run(tag):
            results[tag] = self.post("/v1/responses", tag)

        threads = [threading.Thread(target=run, args=("c%d" % i,)) for i in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        for tag, (status, data) in results.items():
            self.assertEqual((status, data), (200, b"data: %s gpt://<folder>/m\n\n" % tag.encode()))
        self.assertEqual(len(results), 3)


if __name__ == "__main__":
    unittest.main()
