"""Bounded read-until-settled polling for read-after-write verification (REVIEW_BRIEF §5).

The X32 has no write acknowledgement (transport.md §5.2), the writer never sees its own
``/xremote`` echo (§4.2), and real-hardware testing showed a read issued straight after a SET can
return the *previous* value (HANDOVER §4b: ``insert/on``; REVIEW_BRIEF §5: channel names).
Whether the desk applies a SET before serving the next datagram is UNCONFIRMED, so the only
proof that a write took effect is observing it. :func:`read_until` is that observation, bounded:
it re-reads with a short growing delay until ``predicate`` holds or ``deadline_s`` passes, and
reports the outcome as *verified / not yet verified* — a timeout alone is never an error.

Contract:

* The first read happens immediately (a synchronous desk costs one round trip, nothing more).
  After a non-matching read it sleeps ``first_delay_s``, then ``× backoff`` per attempt, capped
  at ``max_delay_s`` and never past the deadline.
* ``deadline_s`` is wall clock from entry and covers everything, reads included. Each read is
  bounded by ``min(read_timeout_s, remaining)``; a further read is only *started* while at least
  ``min_read_s`` remains (the first read always runs, so ``deadline_s=0`` means "read once").
* A read that times out (``asyncio.TimeoutError`` — the per-read cap is ours) or raises one of
  ``retry_on`` (``RequestTimeout`` by default; Desk-level callers add ``DeskError``) counts as an
  attempt, is recorded in ``Settled.error`` and polling continues; any other exception
  propagates (``NotConnected``: polling a dead desk is pointless). ``predicate`` raising
  propagates.
* Cancellation is transparent: nothing is shielded, ``CancelledError`` out of the read or the
  sleep propagates at once.
* Pure asyncio; ``clock``/``sleep`` are injectable for tests (defaults: ``loop.time``,
  ``asyncio.sleep``).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, TypeVar

__all__ = ["Settled", "read_until", "RETRY_ON_DEFAULT"]

log = logging.getLogger(__name__)

T = TypeVar("T")


def _default_retry_on() -> tuple[type[BaseException], ...]:
    from .connection import RequestTimeout  # local import: connection must not depend on this module

    return (RequestTimeout,)


RETRY_ON_DEFAULT: tuple[type[BaseException], ...] | None = None  # resolved lazily (see _default_retry_on)


@dataclass(frozen=True)
class Settled(Generic[T]):
    """Outcome of :func:`read_until`: ``ok`` = the predicate held before the deadline; ``value`` =
    the last value read (``None`` when no read succeeded); ``attempts`` = reads issued (failed
    ones included); ``elapsed_ms``; ``error`` = the last read failure, if the final attempt failed."""

    ok: bool
    value: T | None
    attempts: int
    elapsed_ms: float
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"verified": self.ok, "attempts": self.attempts, "elapsed_ms": round(self.elapsed_ms, 1)}
        if self.error:
            d["error"] = self.error
        return d


async def read_until(
    read: Callable[[], Awaitable[T]],
    predicate: Callable[[T], bool],
    *,
    deadline_s: float,
    first_delay_s: float = 0.04,
    backoff: float = 1.6,
    max_delay_s: float = 0.4,
    read_timeout_s: float | None = None,
    min_read_s: float = 0.02,
    retry_on: tuple[type[BaseException], ...] | None = None,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    what: str = "value",
) -> Settled[T]:
    """Re-read until ``predicate(value)`` or ``deadline_s`` (module doc). Never raises for a
    timeout; returns :class:`Settled` with ``ok=False`` instead."""
    now = clock or asyncio.get_running_loop().time
    retry = retry_on if retry_on is not None else _default_retry_on()
    t0 = now()
    deadline = t0 + max(0.0, float(deadline_s))
    delay = max(0.0, float(first_delay_s))
    attempts = 0
    value: T | None = None
    error: str | None = None
    while True:
        remaining = deadline - now()
        first = attempts == 0
        if not first and remaining < min_read_s:
            break  # not enough budget left for a meaningful read
        if first and remaining < min_read_s:
            per_read = read_timeout_s  # "read once": bounded by the read itself (or read_timeout_s)
        else:
            per_read = remaining if read_timeout_s is None else min(read_timeout_s, remaining)
        attempts += 1
        try:
            got = await asyncio.wait_for(read(), timeout=per_read) if per_read is not None else await read()
        except asyncio.TimeoutError as e:  # our per-read cap (or the read's own): a failed attempt, keep polling
            error = f"TimeoutError: {e}" if str(e) else "TimeoutError"
            log.debug("settle %s: read %d timed out", what, attempts)
        except retry as e:  # a lost/late reply: one failed attempt, keep polling
            error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            log.debug("settle %s: read %d failed: %s", what, attempts, error)
        else:
            error = None
            value = got
            if predicate(got):
                return Settled(True, value, attempts, (now() - t0) * 1000.0)
            log.debug("settle %s: read %d = %r, not settled", what, attempts, got)
        remaining = deadline - now()
        if remaining <= 0:
            break
        pause = min(delay, max_delay_s, remaining)
        if pause > 0:
            await sleep(pause)
        delay = min(max_delay_s, max(delay, 1e-3) * backoff) if delay > 0 else 0.0
    return Settled(False, value, attempts, (now() - t0) * 1000.0, error)
