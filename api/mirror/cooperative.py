"""Cooperative time slicing for long background evidence passes.

The index-maintenance pass re-reads and re-validates every record in a Python
thread. Without pauses it holds the interpreter for long stretches and starves
MCP request threads. A ``TimeSlice`` lets the pass run for a short slice of work
and then sleep briefly so other threads run. It changes only scheduling, never
which checks run or what they return.
"""
from __future__ import annotations

import time

SLICE_SECONDS = 0.010
YIELD_SECONDS = 0.003

_sleep = time.sleep  # indirection so tests can observe yields
_clock = time.monotonic


class TimeSlice:
    """Call ``checkpoint()`` between records; it sleeps once per elapsed slice."""

    def __init__(self, *, slice_seconds: float = SLICE_SECONDS,
                 yield_seconds: float = YIELD_SECONDS):
        self.slice_seconds = slice_seconds
        self.yield_seconds = yield_seconds
        self.yields = 0
        self._mark = _clock()

    def checkpoint(self) -> None:
        if _clock() - self._mark >= self.slice_seconds:
            _sleep(self.yield_seconds)
            self.yields += 1
            self._mark = _clock()


class NoSlice:
    """Default for foreground callers: never pauses."""
    yields = 0

    def checkpoint(self) -> None:
        return None
