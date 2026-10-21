"""AC-5: the harness opens no socket at all.

If ``FakeHttp`` ever starts a listening server again, or a client falls back to
a real transport, this test fails on the first bind/connect.
"""
from __future__ import annotations

import os
import socket
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.support import FILE_A, Harness


def _deny(*args, **kwargs):
    raise AssertionError("the harness opened a socket")


class NoSocketTests(unittest.TestCase):
    def test_cycle_opens_no_socket(self):
        with mock.patch.object(socket.socket, "bind", _deny), \
                mock.patch.object(socket.socket, "connect", _deny), \
                mock.patch.object(socket, "create_connection", _deny):
            harness = Harness(files=(FILE_A,))
            self.addCleanup(harness.close)
            harness.penpot.add_thread(FILE_A, "thread-1")
            harness.cycle()  # baseline
            harness.command_comment("thread-1", "comment-1", title="Socketless")
            harness.cycle()  # detection, issue creation and delivery

        self.assertEqual(harness.jira.count("POST", "/rest/api/3/issue"), 1)
        self.assertEqual(len(harness.penpot.created_comments()), 1)
        self.assertEqual(len(harness.telegram.messages), 1)
        # The fakes answered through FakeHttp.serve, with no server involved.
        self.assertGreaterEqual(harness.penpot.count("POST", "/api/main/methods/get-comment-threads"), 1)


if __name__ == "__main__":
    unittest.main()
