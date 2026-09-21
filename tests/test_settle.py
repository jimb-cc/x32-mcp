"""read_until: bounded read-after-write verification (settle.py)."""
from __future__ import annotations

import asyncio
import time

import pytest

from x32mcp.connection import NotConnected, RequestTimeout
from x32mcp.settle import Settled, read_until


class Seq:
    """An async read() that returns scripted values (an exception instance is raised)."""

    def __init__(self, *values):
        self.values = list(values)
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        v = self.values[min(self.calls - 1, len(self.values) - 1)]
        if isinstance(v, BaseException):
            raise v
        return v


async def test_immediate_match_reads_once_and_never_sleeps():
    slept: list[float] = []

    async def sleep(d):
        slept.append(d)

    r = Seq(5)
    s = await read_until(r, lambda v: v == 5, deadline_s=1.0, sleep=sleep)
    assert s == Settled(True, 5, 1, s.elapsed_ms) and r.calls == 1 and slept == [] and s.to_dict()["verified"] is True


async def test_settles_on_third_read_with_growing_delays():
    slept: list[float] = []

    async def sleep(d):
        slept.append(round(d, 4))

    r = Seq(0, 0, 7)
    s = await read_until(r, lambda v: v == 7, deadline_s=5.0, first_delay_s=0.05, backoff=2.0, sleep=sleep)
    assert s.ok and s.value == 7 and s.attempts == 3 and slept == [0.05, 0.1]


async def test_deadline_gives_not_verified_with_last_value_never_raises():
    t0 = time.monotonic()
    r = Seq(1)
    s = await read_until(r, lambda v: v == 2, deadline_s=0.2, first_delay_s=0.03, max_delay_s=0.05)
    dt = time.monotonic() - t0
    assert s.ok is False and s.value == 1 and s.attempts >= 3 and s.error is None
    assert 0.18 <= dt < 0.35, dt  # bounded by the deadline (plus one capped pause), not by attempts
    assert s.to_dict()["verified"] is False and "error" not in s.to_dict()


async def test_deadline_zero_means_exactly_one_read():
    r = Seq(1, 2)
    s = await read_until(r, lambda v: v == 2, deadline_s=0.0)
    assert s.ok is False and s.attempts == 1 and r.calls == 1


async def test_transport_timeouts_count_as_attempts_and_polling_continues():
    r = Seq(RequestTimeout("lost"), RequestTimeout("lost"), 3)
    s = await read_until(r, lambda v: v == 3, deadline_s=1.0, first_delay_s=0.01)
    assert s.ok and s.attempts == 3 and s.error is None
    r2 = Seq(RequestTimeout("lost"))
    s2 = await read_until(r2, lambda v: True, deadline_s=0.1, first_delay_s=0.01)
    assert s2.ok is False and s2.value is None and s2.error.startswith("RequestTimeout") and s2.to_dict()["error"]


async def test_not_connected_and_predicate_errors_propagate():
    with pytest.raises(NotConnected):
        await read_until(Seq(NotConnected("gone")), lambda v: True, deadline_s=1.0)
    with pytest.raises(ZeroDivisionError):
        await read_until(Seq(1), lambda v: 1 / 0, deadline_s=1.0)


async def test_slow_read_is_capped_by_remaining_budget():
    async def slow():
        await asyncio.sleep(10)

    for retry_on in (None, ()):  # the per-read cap is the helper's own: never an exception, whatever retry_on says
        t0 = time.monotonic()
        s = await read_until(slow, lambda v: True, deadline_s=0.15, retry_on=retry_on)
        assert s.ok is False and s.attempts == 1 and time.monotonic() - t0 < 0.4 and s.error.startswith("TimeoutError")


async def test_cancellation_is_prompt_during_sleep_and_during_read():
    async def never():
        await asyncio.sleep(10)
        return 1

    for read in (Seq(0), never):
        task = asyncio.create_task(read_until(read, lambda v: False, deadline_s=5.0, first_delay_s=1.0))
        await asyncio.sleep(0.05)
        task.cancel()
        t0 = time.monotonic()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.monotonic() - t0 < 0.1
