"""x32mcp.connection against an in-test asyncio UDP responder (DESIGN.md §8).

The responder is NOT fakedesk: it is the smallest thing that behaves like the desk on the
wire (docs/research/transport.md): replies go to the sender's source port; ``/info`` and
``/xinfo`` answer ``,ssss``; a bare address is a GET answered with the value; an address with
an argument is a SET with no ack; ``/node`` answers on address ``node`` with a ``\\n``-terminated
line; ``/`` is echoed back verbatim; unknown addresses are silently ignored. Test hooks let it
drop the next N packets, go silent, push an update and send a blob.

Real sockets on 127.0.0.1, port 0. Timings are shortened through constructor arguments.
"""

from __future__ import annotations

import asyncio
import struct
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio

from x32mcp.connection import (
    ConnectionState,
    ConsoleInfo,
    NotConnected,
    RequestTimeout,
    X32Connection,
)
from x32mcp.events import EventBus
from x32mcp.osc import OscError, OscMessage, decode, encode

# ---------------------------------------------------------------------------------------
# Responder
# ---------------------------------------------------------------------------------------

NODE_CONFIG = '/ch/01/config "Vox" 1 CY 1'
NODE_MIX = "/ch/01/mix ON  +2.1 ON +0 OFF   -oo"  # real-desk padding, transport.md §6.3


class Responder(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.transport: asyncio.DatagramTransport | None = None
        self.values: dict[str, object] = {
            "/ch/01/mix/fader": 0.75,
            "/ch/01/mix/on": 1,
            "/ch/01/config/name": "Vox",
        }
        self.nodes = {"ch/01/config": NODE_CONFIG + "\n", "ch/01/mix": NODE_MIX + "\n"}
        self.name = "X32-TEST"
        self.drop_next = 0  # lose the replies to the next N answerable datagrams
        self.silent = False  # ignore everything (desk hung / cable out)
        self.echo_slash = True
        self.sources: list[tuple[str, int]] = []  # (ip, port) of every datagram received
        self.received: list[OscMessage] = []
        self.last_client: tuple[str, int] | None = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    @property
    def port(self) -> int:
        return self.transport.get_extra_info("sockname")[1]

    def datagram_received(self, data: bytes, addr) -> None:
        self.sources.append(addr)
        self.last_client = addr
        try:
            msg = decode(data)
        except OscError:
            return
        self.received.append(msg)
        if self.silent:
            return
        reply = self._reply_for(msg, data)
        if reply is None:
            return
        # "drop" means "lose the reply": a lost /xremote or SET is invisible to the client
        # (never acked, transport.md §4.1/§5.2), so only answerable datagrams use the budget.
        if self.drop_next > 0:
            self.drop_next -= 1
            return
        self.transport.sendto(reply, addr)  # to the source port (transport.md §2)

    def _reply_for(self, msg: OscMessage, data: bytes) -> bytes | None:
        a = msg.address
        if a == "/info":  # transport.md §3.1 arg order
            return encode("/info", "V2.07", "osc-server", "X32RACK", "4.06")
        if a == "/xinfo":  # §3.2: ip, name, model, firmware
            return encode("/xinfo", "127.0.0.1", self.name, "X32RACK", "4.06")
        if a in ("/xremote", "/meters", "/renew"):
            return None  # never acked (§4.1, §7)
        if a == "/node":
            line = self.nodes.get(str(msg.args[0]).lstrip("/"))
            return None if line is None else encode("node", line)  # address "node", §6.2
        if a == "/":
            return data if self.echo_slash else None  # verbatim echo, §6.6
        if msg.args:  # SET: store, no ack (§5.2)
            self.values[a] = msg.args[0]
            return None
        v = self.values.get(a)
        return None if v is None else encode(a, v)  # unknown address: silence (§5.3)

    # -- test hooks --------------------------------------------------------------------
    def count(self, address: str) -> int:
        return sum(1 for m in self.received if m.address == address)

    def gets(self, address: str) -> int:
        """GET requests only (bare address, no args — transport.md §5.1); SETs are not counted."""
        return sum(1 for m in self.received if m.address == address and not m.args)

    def push(self, address: str, *args) -> None:
        self.transport.sendto(encode(address, *args), self.last_client)

    def send_raw(self, data: bytes) -> None:
        self.transport.sendto(data, self.last_client)

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()


async def start_responder(port: int = 0) -> Responder:
    loop = asyncio.get_running_loop()
    _, proto = await loop.create_datagram_endpoint(Responder, local_addr=("127.0.0.1", port))
    return proto


# ---------------------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------------------

TTL = 0.3
STUB_DESCRIPTOR = SimpleNamespace(policy={"read_cache_ttl_s": TTL})


def make_conn(bus: EventBus, **kw) -> X32Connection:
    opts = dict(timeout_s=0.1, retries=2, heartbeat_s=0.2, watchdog_s=0.4, backoff_s=(0.1, 0.2))
    opts.update(kw)
    return X32Connection(STUB_DESCRIPTOR, bus, **opts)


async def wait_for_state(conn: X32Connection, state: ConnectionState, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while conn.state is not state:
        if time.monotonic() > deadline:
            raise AssertionError(f"state is {conn.state.value}, expected {state.value} within {timeout} s")
        await asyncio.sleep(0.01)


async def wait_until(pred, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


@pytest_asyncio.fixture
async def responder():
    r = await start_responder()
    yield r
    r.close()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest_asyncio.fixture
async def conn(bus, responder):
    c = make_conn(bus)
    await c.connect("127.0.0.1", responder.port)
    yield c
    await c.close()


# ---------------------------------------------------------------------------------------
# Connect / status / heartbeat
# ---------------------------------------------------------------------------------------


async def test_connect_reports_console_and_state(conn, bus, responder):
    st = conn.status
    assert st.state is ConnectionState.CONNECTED and conn.connected
    assert st.console == ConsoleInfo(
        host="127.0.0.1", port=responder.port, name="X32-TEST", model="X32RACK", firmware="4.06", server_version="V2.07"
    )
    assert st.rtt_ms is not None and 0 <= st.rtt_ms < 500
    assert st.last_rx_age_s is not None and st.last_rx_age_s < 1
    assert st.pending_requests == 0 and st.error is None
    states = [e.data["state"] for e in bus.recent(types={"connection.state"})]
    assert states == ["connecting", "connected"]
    assert st.to_dict()["state"] == "connected" and st.to_dict()["console"]["name"] == "X32-TEST"
    # heartbeat: /xremote immediately and every heartbeat_s (0.2 s here)
    await wait_until(lambda: responder.count("/xremote") >= 3, timeout=2.0)
    assert conn.status.heartbeat_ok
    assert responder.received[0].address == "/info"  # first thing on the wire is the probe


async def test_replies_arrive_on_the_single_local_port(conn, responder):
    await conn.get("/ch/01/mix/fader")
    await conn.node("ch/01/config")
    await conn.set("/ch/01/mix/on", 0)
    local = conn.local_addr
    assert local is not None and local[1] > 0
    ports = {p for _, p in responder.sources}
    assert ports == {local[1]}  # every datagram we sent came from the one bound port


async def test_connect_failure_leaves_disconnected(bus):
    probe = await start_responder()
    dead_port = probe.port
    probe.close()
    await asyncio.sleep(0.05)
    c = make_conn(bus, timeout_s=0.1, retries=1)
    t0 = time.monotonic()
    with pytest.raises(RequestTimeout):
        await c.connect("127.0.0.1", dead_port)
    assert time.monotonic() - t0 < 1.0
    assert c.state is ConnectionState.DISCONNECTED and not c.connected
    assert c.status.error and "/info" in c.status.error
    assert c.local_addr is None
    with pytest.raises(NotConnected):
        await c.get("/ch/01/mix/fader")
    await c.close()


# ---------------------------------------------------------------------------------------
# Request / get / retries / timeouts
# ---------------------------------------------------------------------------------------


async def test_get_and_request(conn):
    assert await conn.get("/ch/01/mix/fader") == pytest.approx(0.75)
    assert await conn.get("/ch/01/config/name") == "Vox"
    assert await conn.get("/ch/01/mix/on") == 1
    msg = await conn.request("/ch/01/mix/fader")
    assert msg.address == "/ch/01/mix/fader" and msg.typetags == ",f"
    assert conn.status.pending_requests == 0


async def test_retries_after_dropped_packets(conn, responder):
    responder.drop_next = 2
    t0 = time.monotonic()
    assert await conn.get("/ch/01/mix/fader") == pytest.approx(0.75)
    elapsed = time.monotonic() - t0
    assert 0.18 <= elapsed < 0.6  # two 0.1 s attempts lost, third answered
    assert responder.count("/ch/01/mix/fader") == 3


async def test_request_timeout_budget(bus, responder):
    c = make_conn(bus, watchdog_s=10.0)  # keep the watchdog quiet during the silence
    await c.connect("127.0.0.1", responder.port)
    try:
        t0 = time.monotonic()
        with pytest.raises(RequestTimeout) as ei:
            await c.request("/does/not/exist", timeout=0.5, retries=2)
        elapsed = time.monotonic() - t0
        assert 1.45 <= elapsed <= 1.9  # 3 attempts × 0.5 s
        assert "3 attempt" in str(ei.value)
        assert responder.count("/does/not/exist") == 3
        assert c.status.pending_requests == 0
        assert c.connected  # a single unanswered address is not a dead desk
    finally:
        await c.close()


async def test_concurrent_requests_same_address_fifo(conn, responder):
    a, b = await asyncio.gather(conn.get("/ch/01/mix/fader"), conn.get("/ch/01/mix/fader"))
    assert a == pytest.approx(0.75) and b == pytest.approx(0.75)
    assert responder.count("/ch/01/mix/fader") == 2


# ---------------------------------------------------------------------------------------
# /node and node_many
# ---------------------------------------------------------------------------------------


async def test_node_strips_newline_and_slash(conn, responder):
    assert await conn.node("ch/01/config") == NODE_CONFIG
    assert await conn.node("/ch/01/mix") == NODE_MIX
    sent = [m for m in responder.received if m.address == "/node"]
    assert [m.args[0] for m in sent] == ["ch/01/config", "ch/01/mix"]  # no leading slash on the wire


async def test_node_many_returns_none_for_missing(conn, responder):
    paths = ["ch/01/config", "missing/path", "ch/01/mix"]
    t0 = time.monotonic()
    out = await conn.node_many(paths, concurrency=8)
    elapsed = time.monotonic() - t0
    assert list(out) == paths
    assert out["ch/01/config"] == NODE_CONFIG
    assert out["ch/01/mix"] == NODE_MIX
    assert out["missing/path"] is None
    assert elapsed < 0.8  # pipelined: the missing one costs 0.3 s, not serialised behind the rest
    assert conn.status.pending_requests == 0


# ---------------------------------------------------------------------------------------
# set / send_raw / events
# ---------------------------------------------------------------------------------------


async def test_set_writes_and_publishes(conn, bus, responder):
    await conn.set("/ch/01/mix/fader", 0.5)
    await wait_until(lambda: responder.values["/ch/01/mix/fader"] == 0.5)
    ev = bus.recent(types={"write"})[-1]
    assert ev.data == {"address": "/ch/01/mix/fader", "args": [0.5]}
    await conn.set("/ch/01/mix/fader", 1, typetags="f")  # int coerced to float32 on the wire
    await wait_until(lambda: responder.values["/ch/01/mix/fader"] == 1.0)
    assert isinstance(responder.values["/ch/01/mix/fader"], float)
    assert responder.received[-1].typetags == ",f"


async def test_send_raw(conn, responder):
    await conn.send_raw("/meters", "/meters/6", 1)
    await wait_until(lambda: responder.count("/meters") == 1)
    m = [m for m in responder.received if m.address == "/meters"][0]
    assert m.typetags == ",si" and m.args == ("/meters/6", 1)


async def test_set_refused_when_not_connected(bus):
    c = make_conn(bus)
    with pytest.raises(NotConnected):
        await c.set("/ch/01/mix/fader", 0.5)
    with pytest.raises(NotConnected):
        await c.slash("ch/01/mix/fader -20.5")
    with pytest.raises(NotConnected):
        await c.send_raw("/xremote")


# ---------------------------------------------------------------------------------------
# Cache and pushed updates
# ---------------------------------------------------------------------------------------


async def test_get_cached_ttl_invalidate_and_push(conn, responder):
    assert await conn.get_cached("/ch/01/mix/fader") == pytest.approx(0.75)
    assert await conn.get_cached("/ch/01/mix/fader") == pytest.approx(0.75)
    assert responder.gets("/ch/01/mix/fader") == 1  # served from cache
    await asyncio.sleep(TTL + 0.1)
    await conn.get_cached("/ch/01/mix/fader")
    assert responder.gets("/ch/01/mix/fader") == 2  # TTL expired
    conn.invalidate("/ch/01/mix/fader")
    await conn.get_cached("/ch/01/mix/fader")
    assert responder.gets("/ch/01/mix/fader") == 3
    # our own write invalidates (the SET itself is not a GET and is not counted)
    await conn.set("/ch/01/mix/fader", 0.25)
    assert await conn.get_cached("/ch/01/mix/fader") == pytest.approx(0.25)
    assert responder.gets("/ch/01/mix/fader") == 4
    # concurrent misses share one request
    conn.invalidate()
    vals = await asyncio.gather(*(conn.get_cached("/ch/01/mix/fader") for _ in range(5)))
    assert all(v == pytest.approx(0.25) for v in vals)
    assert responder.gets("/ch/01/mix/fader") == 5


async def test_pushed_update_hits_callbacks_cache_and_events(conn, bus, responder):
    seen: list[tuple[str, tuple]] = []
    unsub = conn.on_update(lambda a, args: seen.append((a, args)))
    responder.push("/ch/02/mix/fader", 0.5)
    await wait_until(lambda: seen == [("/ch/02/mix/fader", (0.5,))])
    assert bus.recent(types={"update"})[-1].data == {"address": "/ch/02/mix/fader", "args": [0.5]}
    # the pushed value is now cached: no GET goes to the desk
    assert await conn.get_cached("/ch/02/mix/fader") == 0.5
    assert responder.count("/ch/02/mix/fader") == 0
    unsub()
    responder.push("/ch/03/mix/on", 1)
    await wait_until(lambda: bus.recent(types={"update"})[-1].data["address"] == "/ch/03/mix/on")
    assert len(seen) == 1  # unsubscribed


async def test_bad_callback_does_not_break_receive(conn, responder):
    def boom(a, args):
        raise RuntimeError("bad subscriber")

    conn.on_update(boom)
    responder.push("/ch/02/mix/fader", 0.5)
    await wait_until(lambda: conn.stats["rx"] >= 3)
    assert await conn.get("/ch/01/mix/fader") == pytest.approx(0.75)


# ---------------------------------------------------------------------------------------
# Blobs and garbage
# ---------------------------------------------------------------------------------------


async def test_blob_goes_to_on_blob_only(conn, bus, responder):
    blobs: list[tuple[str, bytes]] = []
    updates: list = []
    conn.on_blob(lambda a, b: blobs.append((a, b)))
    conn.on_update(lambda a, args: updates.append((a, args)))
    n_updates_before = len(bus.recent(types={"update"}))
    # /meters/6 layout (transport.md §8): LE count, then LE floats; plus bytes that are not UTF-8
    payload = struct.pack("<i", 4) + struct.pack("<4f", 1e-5, 0.5, 1.0, 8.0) + b"\xff\x00\xfe\n"
    responder.send_raw(encode("/meters/6", payload))
    await wait_until(lambda: len(blobs) == 1)
    assert blobs == [("/meters/6", payload)]
    assert updates == []
    assert len(bus.recent(types={"update"})) == n_updates_before
    # decode failures are counted, logged, and never kill the loop
    responder.send_raw(b"\xff\xfe\x00\x01")
    responder.send_raw(b"#bundle\x00" + b"\x00" * 16)
    responder.send_raw(b"/ch/01/mix/fader\x00\x00\x00\x00,f\x00\x00\x3f")  # truncated float
    await wait_until(lambda: conn.stats["decode_errors"] == 3)
    assert await conn.get("/ch/01/mix/fader") == pytest.approx(0.75)


# ---------------------------------------------------------------------------------------
# slash (node-style write)
# ---------------------------------------------------------------------------------------


async def test_slash_resolves_on_echo(conn, responder):
    await conn.slash("ch/01/mix/fader -20.5")
    sent = [m for m in responder.received if m.address == "/"]
    assert len(sent) == 1
    assert sent[0].typetags == ",s" and sent[0].args == ("ch/01/mix/fader -20.5",)
    assert conn.status.pending_requests == 0


async def test_slash_times_out_without_echo(conn, responder):
    responder.echo_slash = False
    t0 = time.monotonic()
    with pytest.raises(RequestTimeout):
        await conn.slash('/ch/01/config "Vox" 1 CY 1')
    elapsed = time.monotonic() - t0
    assert 0.28 <= elapsed < 0.8  # 3 × 0.1 s
    assert sum(1 for m in responder.received if m.address == "/") == 3
    assert conn.connected


# ---------------------------------------------------------------------------------------
# Degraded / reconnect
# ---------------------------------------------------------------------------------------


async def test_degraded_then_reconnect_when_desk_returns(conn, bus, responder):
    responder.silent = True
    await wait_for_state(conn, ConnectionState.DEGRADED, timeout=3.0)
    st = conn.status
    assert not conn.connected and st.error and "not responding" in st.error
    assert not st.heartbeat_ok
    with pytest.raises(NotConnected, match="not responding"):
        await conn.set("/ch/01/mix/fader", 0.5)
    with pytest.raises(NotConnected):
        await conn.slash("ch/01/mix/fader 0")
    # reads still try, but with a single attempt
    t0 = time.monotonic()
    with pytest.raises(RequestTimeout):
        await conn.get("/ch/01/mix/fader")
    assert time.monotonic() - t0 < 0.3
    await wait_until(lambda: conn.status.reconnect_attempts >= 2, timeout=3.0)
    n_xremote = responder.count("/xremote")

    responder.silent = False
    await wait_for_state(conn, ConnectionState.CONNECTED, timeout=3.0)
    assert conn.status.error is None and conn.status.reconnect_attempts >= 2
    states = [e.data["state"] for e in bus.recent(types={"connection.state"})]
    assert states[-2:] == ["degraded", "connected"]
    assert await conn.get("/ch/01/mix/fader") == pytest.approx(0.75)
    await conn.set("/ch/01/mix/fader", 0.5)  # writes allowed again
    await wait_until(lambda: responder.count("/xremote") > n_xremote)  # re-registered for pushes


async def test_reconnect_after_responder_restart_on_same_port(conn, responder):
    # Closing the responder makes every send hit a closed port; on Windows that surfaces as
    # WSAECONNRESET on our socket (ICMP port-unreachable), which must not kill the transport.
    port = responder.port
    responder.close()
    await asyncio.sleep(0.05)
    await wait_for_state(conn, ConnectionState.DEGRADED, timeout=3.0)
    again = await start_responder(port)
    try:
        await wait_for_state(conn, ConnectionState.CONNECTED, timeout=3.0)
        assert await conn.get("/ch/01/mix/fader") == pytest.approx(0.75)
        assert conn.local_addr == conn.local_addr  # same socket, same port
        assert again.sources and {p for _, p in again.sources} == {conn.local_addr[1]}
    finally:
        again.close()


async def test_any_datagram_recovers_degraded(conn, responder):
    responder.silent = True
    await wait_for_state(conn, ConnectionState.DEGRADED, timeout=3.0)
    responder.silent = False
    responder.push("/ch/05/mix/on", 0)  # a push from the desk is proof of life
    await wait_for_state(conn, ConnectionState.CONNECTED, timeout=1.0)


# ---------------------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------------------


async def test_close_fails_pending_and_publishes(bus, responder):
    c = make_conn(bus)
    await c.connect("127.0.0.1", responder.port)
    pending = asyncio.ensure_future(c.request("/does/not/exist", timeout=5.0, retries=0))
    await asyncio.sleep(0.05)
    assert c.status.pending_requests == 1
    await c.close()
    with pytest.raises(NotConnected):
        await pending
    assert c.state is ConnectionState.DISCONNECTED and c.local_addr is None
    assert bus.recent(types={"connection.state"})[-1].data["state"] == "disconnected"
    with pytest.raises(NotConnected):
        await c.get("/ch/01/mix/fader")
    await c.close()  # idempotent
    # and a fresh connect on the same object works
    info = await c.connect("127.0.0.1", responder.port)
    assert info.model == "X32RACK" and c.connected
    await c.close()


# ---------------------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------------------


async def test_discover_finds_responder_via_loopback(bus, responder):
    c = make_conn(bus)
    t0 = time.monotonic()
    found = await c.discover(timeout_s=0.4, port=responder.port)
    assert 0.35 <= time.monotonic() - t0 < 2.0
    ours = [ci for ci in found if ci.host == "127.0.0.1" and ci.port == responder.port]
    assert len(ours) == 1
    assert ours[0].name == "X32-TEST" and ours[0].model == "X32RACK" and ours[0].firmware == "4.06"
    assert ours[0].server_version == ""
    assert responder.count("/xinfo") >= 1
    assert c.state is ConnectionState.DISCONNECTED  # discovery never touches the main socket
