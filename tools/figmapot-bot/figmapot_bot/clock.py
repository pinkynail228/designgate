"""Injectable time source.

All time reads go through ``Clock.now`` and all waits through ``Clock.sleep``
so tests can drive the bot on a fake clock without real delays.
"""
from __future__ import annotations

import time


class Clock:
    """Real, monotonic-ish clock used in production."""

    def now(self):
        return time.time()

    def sleep(self, seconds):
        if seconds > 0:
            time.sleep(seconds)


class FakeClock(Clock):
    """Deterministic clock for tests. ``sleep`` records and advances."""

    def __init__(self, start=1_000_000.0):
        self._now = float(start)
        self.sleeps = []

    def now(self):
        return self._now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self._now += max(0.0, float(seconds))

    def advance(self, seconds):
        self._now += float(seconds)
