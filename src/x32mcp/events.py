"""In-process event bus (DESIGN.md §3).

Used by the connection layer (state changes, writes, pushed updates), CFS² (detector
candidates/notches/stages) and the dashboard (which mirrors everything to the browser).
Synchronous ``publish``; async consumers use :meth:`EventBus.stream`.

``meters.frame`` events are high-rate and are deliberately *not* retained in the ring buffer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

log = logging.getLogger(__name__)

TRANSIENT_TYPES = frozenset({"meters.frame"})


@dataclass(frozen=True)
class Event:
    ts: float
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "type": self.type, "data": self.data}


Callback = Callable[[Event], None]


class EventBus:
    def __init__(self, *, history: int = 500, clock: Callable[[], float] = time.time) -> None:
        self._subs: list[tuple[Callback, frozenset[str] | None]] = []
        self._history: deque[Event] = deque(maxlen=history)
        self._queues: list[tuple[asyncio.Queue[Event], frozenset[str] | None]] = []
        self._clock = clock

    # -- publish -------------------------------------------------------------------
    def publish(self, type: str, **data: Any) -> Event:
        ev = Event(ts=self._clock(), type=type, data=data)
        if type not in TRANSIENT_TYPES:
            self._history.append(ev)
        for cb, types in list(self._subs):
            if types is None or type in types:
                try:
                    cb(ev)
                except Exception:  # a bad subscriber must never break the publisher
                    log.exception("event subscriber failed for %s", type)
        for q, types in list(self._queues):
            if types is None or type in types:
                try:
                    q.put_nowait(ev)
                except asyncio.QueueFull:
                    # Drop the oldest frame-type event rather than block the publisher.
                    try:
                        q.get_nowait()
                        q.put_nowait(ev)
                    except (asyncio.QueueEmpty, asyncio.QueueFull):
                        pass
        return ev

    # -- subscribe -----------------------------------------------------------------
    def subscribe(self, callback: Callback, *, types: set[str] | None = None) -> Callable[[], None]:
        entry = (callback, frozenset(types) if types else None)
        self._subs.append(entry)

        def _unsub() -> None:
            try:
                self._subs.remove(entry)
            except ValueError:
                pass

        return _unsub

    def recent(self, n: int = 200, types: set[str] | None = None) -> list[Event]:
        evs = list(self._history)
        if types:
            evs = [e for e in evs if e.type in types]
        return evs[-n:]

    async def stream(self, types: set[str] | None = None, *, maxsize: int = 256) -> AsyncIterator[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        entry = (q, frozenset(types) if types else None)
        self._queues.append(entry)
        try:
            while True:
                yield await q.get()
        finally:
            try:
                self._queues.remove(entry)
            except ValueError:
                pass
