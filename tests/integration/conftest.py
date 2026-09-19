"""Integration fixtures: a FakeDesk on 127.0.0.1 (port 0) and X32Connections to it (DESIGN.md §18).

``descriptor`` is loaded once per session (immutable); ``fakedesk`` and the connections are
fresh for every test. Connection timeouts are short so fault tests finish in seconds:
``timeout_s`` 0.25 × 2 attempts, heartbeat 0.5 s, watchdog 1 s, reconnect backoff 0.1/0.2 s.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

import pytest
import pytest_asyncio

from x32mcp.connection import ConnectionState, X32Connection
from x32mcp.descriptor import Descriptor
from x32mcp.events import EventBus
from x32mcp.fakedesk import FakeDesk

CONN_OPTS: dict[str, Any] = dict(timeout_s=0.25, retries=1, heartbeat_s=0.5, watchdog_s=1.0, backoff_s=(0.1, 0.2))


@pytest.fixture(scope="session")
def descriptor() -> Descriptor:
    return Descriptor.load()


@pytest_asyncio.fixture
async def fakedesk(descriptor: Descriptor):
    desk = FakeDesk(descriptor, host="127.0.0.1", port=0, name="X32-FAKE")
    await desk.start()
    try:
        yield desk
    finally:
        await desk.stop()


@pytest_asyncio.fixture
async def conn_factory(descriptor: Descriptor, fakedesk: FakeDesk):
    """``await conn_factory(**overrides)`` -> a connected X32Connection (closed on teardown)."""
    conns: list[X32Connection] = []

    async def make(**overrides: Any) -> X32Connection:
        opts = {**CONN_OPTS, **overrides}
        c = X32Connection(descriptor, EventBus(), **opts)
        await c.connect(fakedesk.host, fakedesk.port)
        conns.append(c)
        return c

    try:
        yield make
    finally:
        for c in conns:
            await c.close()


@pytest_asyncio.fixture
async def conn(conn_factory: Callable[..., Awaitable[X32Connection]]) -> X32Connection:
    return await conn_factory()


async def wait_until(pred: Callable[[], Any], timeout: float = 3.0, what: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError(f"{what} not met within {timeout} s")
        await asyncio.sleep(0.01)


async def wait_for_state(conn: X32Connection, state: ConnectionState, timeout: float = 5.0) -> None:
    await wait_until(lambda: conn.state is state, timeout, f"connection state {state.value} (is {conn.state.value})")
