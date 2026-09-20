"""x32mcp.webui (DESIGN.md §17): static page + /healthz over raw HTTP, and the WS push contract
(hello → rta/state/notches/event) against ``meters.SyntheticRta`` and a fake CFS² state provider.

Slow-client behaviour, frame decimation, JSON hygiene and start/stop/bind-failure are covered too.
The page's own static contract lives in tests/test_webui_page.py.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from dataclasses import dataclass
from enum import Enum
from types import SimpleNamespace
from typing import Any

import pytest
from websockets.asyncio.client import connect

from x32mcp.cfs import CfsMode, CfsState
from x32mcp.config import Settings
from x32mcp.connection import ConnectionState, ConnectionStatus, ConsoleInfo
from x32mcp.descriptor import Descriptor
from x32mcp.events import EventBus
from x32mcp.meters import RTA_BANDS, MeterFrame, SyntheticRta, rta_band_hz
from x32mcp.targets import Target
from x32mcp.webui import DashboardServer, WebUIError, _Client, dumps

CONN = {"state": "connected", "console": {"name": "X32-FAKE", "model": "X32RACK"}, "rtt_ms": 3.2}


class FakeCfs:
    """Duck-typed state provider: a ``state`` attribute holding a plain dict."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {
            "mode": "watch", "session_id": "s-1", "bus": 3, "bus_name": "Wedge A", "master_db": -12.0,
            "budget_left": 4, "candidate": None, "stage": "RAISE", "notches": [], "rta_source": "bus.3",
        }


# -- helpers ----------------------------------------------------------------------------------


async def http_get(port: int, path: str, method: str = "GET") -> tuple[int, dict[str, str], bytes]:
    """Minimal HTTP/1.1 request with stdlib asyncio only (the server answers Connection: close)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode())
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(), timeout=5.0)
    writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ")[1])
    headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
    return status, headers, body


async def recv_json(ws, timeout: float = 5.0) -> dict:
    return json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))


async def collect(ws, want: set[str], timeout: float = 5.0, *, stop_on: str | None = None) -> dict[str, list[dict]]:
    """Read until every message type in ``want`` was seen (or ``stop_on`` event type arrives)."""
    got: dict[str, list[dict]] = {}
    deadline = time.monotonic() + timeout
    while want - got.keys():
        left = deadline - time.monotonic()
        assert left > 0, f"timed out waiting for {want - got.keys()}; got {sorted(got)}"
        m = await recv_json(ws, timeout=left)
        got.setdefault(m["t"], []).append(m)
        if stop_on and m["t"] == "event" and m.get("type") == stop_on:
            break
    return got


def ws_url(dash: DashboardServer) -> str:
    return f"ws://127.0.0.1:{dash.port}/ws"


def client(dash: DashboardServer):
    return connect(ws_url(dash), proxy=None, open_timeout=5)


# -- fixtures ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def d() -> Descriptor:
    return Descriptor.load()


@pytest.fixture
def settings() -> Settings:
    return Settings.from_env({})  # home = repo root → the real webui/index.html


@pytest.fixture
async def rig(settings: Settings, d: Descriptor):
    events = EventBus()
    cfs = FakeCfs()
    rta = SyntheticRta(period_s=0.01, band_hz=rta_band_hz(d))  # faster than the 20 fps cap → must decimate
    dash = DashboardServer(
        settings, events, cfs, rta, band_hz=rta_band_hz(d), geq_band_hz=d.geq["band_hz"],
        connection_status=lambda: CONN, host="127.0.0.1", port=0, state_period_s=0.3,
    )
    await rta.start()
    await dash.start()
    try:
        yield SimpleNamespace(events=events, cfs=cfs, rta=rta, dash=dash)
    finally:
        await dash.stop()
        await rta.stop()


# -- HTTP -------------------------------------------------------------------------------------


async def test_index_served_as_html(rig) -> None:
    for path in ("/", "/index.html", "/?demo=1"):
        status, headers, body = await http_get(rig.dash.port, path)
        assert status == 200, path
        assert headers["content-type"].startswith("text/html"), headers
        assert headers.get("cache-control") == "no-store"
        assert b"<canvas" in body and b"'/ws'" in body
        assert int(headers["content-length"]) == len(body)


async def test_healthz_json(rig) -> None:
    status, headers, body = await http_get(rig.dash.port, "/healthz")
    assert status == 200
    assert headers["content-type"].startswith("application/json")
    h = json.loads(body)
    assert h["ok"] is True and h["running"] is True and h["fps"] == 20
    assert h["mode"] == "watch" and h["source"] == "SyntheticRta" and h["version"] == rig.dash.version


async def test_unknown_path_404_and_non_get_405(rig) -> None:
    status, _, body = await http_get(rig.dash.port, "/nope")
    assert status == 404 and b"not found" in body
    status, _, _ = await http_get(rig.dash.port, "/", method="POST")
    assert status == 405


async def test_page_reread_per_request(settings: Settings, d: Descriptor, tmp_path) -> None:
    s = dataclasses.replace(settings, webui_dir=tmp_path)
    dash = DashboardServer(s, EventBus(), None, None, band_hz=rta_band_hz(d), geq_band_hz=d.geq["band_hz"],
                           host="127.0.0.1", port=0)
    await dash.start()
    try:
        status, _, body = await http_get(dash.port, "/")
        assert status == 404 and b"missing" in body
        (tmp_path / "index.html").write_text("<html><canvas id='rta'></canvas>v1</html>", encoding="utf-8")
        status, _, body = await http_get(dash.port, "/")
        assert status == 200 and b"v1" in body
        (tmp_path / "index.html").write_text("<html>v2 − edited</html>", encoding="utf-8")
        status, _, body = await http_get(dash.port, "/")
        assert status == 200 and "v2 − edited" in body.decode("utf-8"), "edits show without a restart"
    finally:
        await dash.stop()


# -- WebSocket push ---------------------------------------------------------------------------


async def test_ws_hello_rta_state(rig) -> None:
    async with client(rig.dash) as ws:
        hello = await recv_json(ws)
        assert hello["t"] == "hello"
        assert len(hello["band_hz"]) == RTA_BANDS == 100 and hello["band_hz"][90] == 10000.0
        assert len(hello["geq_band_hz"]) == 31 and hello["geq_band_hz"][0] == 20.0
        assert hello["version"] == "0.1.0" and hello["fps"] == 20
        got = await collect(ws, {"rta", "state", "notches"})
        frame = got["rta"][0]
        assert len(frame["db"]) == 100 and all(isinstance(v, float) for v in frame["db"])
        assert all(-128.0 <= v <= 0.0 for v in frame["db"])
        assert isinstance(frame["ts"], float) and frame["ts"] > 1e9, "ts is epoch seconds"
        st = got["state"][0]
        assert st["mode"] == "watch" and st["bus"] == 3 and st["bus_name"] == "Wedge A"
        assert st["master_db"] == -12.0 and st["budget_left"] == 4 and st["candidate"] is None
        assert st["stage"] == "RAISE" and st["rta_source"] == "bus.3" and st["session_id"] == "s-1"
        assert st["connection"] == {"state": "connected", "console": "X32-FAKE", "rtt_ms": 3.2}
        assert got["notches"][0] == {"t": "notches", "items": []}


async def test_frames_decimated_to_fps(rig) -> None:
    async with client(rig.dash) as ws:
        await collect(ws, {"rta"})
        n = 0
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.0:
            m = await recv_json(ws, timeout=2.0)
            n += m["t"] == "rta"
    # source runs at ~60-100 fps; the push is capped at 20 (Windows timers may run a little slow)
    assert 8 <= n <= 24, n
    assert rig.dash.frames_seen > rig.dash.frames_pushed > 0


async def test_event_forwarded_and_frames_events_skipped(rig) -> None:
    async with client(rig.dash) as ws:
        await collect(ws, {"state"})
        rig.events.publish("meters.frame", meter_type=15, values=[0.0] * 100)  # transient: never mirrored
        ev = rig.events.publish("cfs.stage", stage="RAISE", bus=3, master_db=-11.0)
        got = await collect(ws, {"event"}, stop_on="cfs.stage")
        types = [m["type"] for m in got["event"]]
        assert "meters.frame" not in types
        m = got["event"][-1]
        assert m == {"t": "event", "ts": ev.ts, "type": "cfs.stage", "data": {"stage": "RAISE", "bus": 3, "master_db": -11.0}}


async def test_ring_visible_in_pushed_frames_before_the_notch(rig, d) -> None:
    """BRIEF §7 M8: "the waterfall shows a scripted ring as a streak before the detector trips".

    The drawing is the page's job; what the server owes it is the ring — rising, in the right
    band, on the wire — and the notch message after it.
    """
    bands = rta_band_hz(d)
    band = bands.index(min(bands, key=lambda h: abs(h - 2400.0)))
    async with client(rig.dash) as ws:
        await collect(ws, {"rta", "notches"})
        rig.rta.inject_ring(2400.0, 15.0, start_db=-45.0)
        levels: list[float] = []
        while len(levels) < 24:
            m = await recv_json(ws, timeout=5.0)
            if m["t"] == "rta":
                levels.append(m["db"][band])
        # the streak is on the wire, and rising: compare the ends in means, the generator
        # puts ±3 dB of noise and ±2 dB of wobble on every single frame
        head, tail = sum(levels[:5]) / 5, sum(levels[-5:]) / 5
        assert tail > head + 6.0, levels
        assert all(-128.0 <= v <= 0.0 for v in levels)
        # ... and only then does the detector's notch reach the page
        rig.cfs.state["notches"] = [{"bus": 3, "band": 22, "freq_hz": 2500.0, "depth_db": -3.0,
                                     "session_id": "s-1", "ts": 1.0}]
        rig.cfs.state["budget_left"] = 3
        rig.events.publish("cfs.notch", bus=3, band=22, freq_hz=2500.0, depth_db=-3.0, session_id="s-1")
        got = await collect(ws, {"notches"})
        assert got["notches"][-1]["items"][0]["freq_hz"] == 2500.0
        rig.rta.stop_ring(2400.0)


async def test_notches_on_notch_event(rig) -> None:
    @dataclass
    class Notch:  # shaped like detector.Notch (+ an extra field the page ignores)
        bus: int
        band: int
        freq_hz: float
        depth_db: float
        session_id: str
        ts: float
        detections: int

    async with client(rig.dash) as ws:
        await collect(ws, {"notches"})
        rig.cfs.state["notches"] = [
            {"bus": 3, "band": 22, "freq_hz": 2500.0, "depth_db": -3.0, "session_id": "s-1", "ts": 1.0},
            Notch(3, 16, 630.0, -6.0, "s-1", 2.0, 4),
        ]
        rig.cfs.state["budget_left"] = 2
        rig.events.publish("cfs.notch", bus=3, band=22, freq_hz=2500.0, depth_db=-3.0, session_id="s-1")
        got = await collect(ws, {"notches", "state"})
        items = got["notches"][-1]["items"]
        assert [(i["band"], i["depth_db"]) for i in items] == [(22, -3.0), (16, -6.0)]
        assert items[1]["detections"] == 4 and items[1]["freq_hz"] == 630.0
        assert got["state"][-1]["budget_left"] == 2, "state pushed on change (via the event)"
        # a bare cfs.notch event re-sends the (unchanged) list — full replacement semantics
        rig.events.publish("cfs.notch", bus=3, band=22, freq_hz=2500.0, depth_db=-6.0, session_id="s-1")
        got = await collect(ws, {"notches"})
        assert len(got["notches"][-1]["items"]) == 2


async def test_state_on_change_and_periodic(rig) -> None:
    async with client(rig.dash) as ws:
        await collect(ws, {"state"})
        rig.cfs.state["mode"] = "RINGOUT"  # provider may use upper-case / enum spellings
        rig.cfs.state["stage"] = "NOTCH"
        rig.events.publish("cfs.state", mode="ringout")
        got = await collect(ws, {"state"})
        assert got["state"][-1]["mode"] == "ringout" and got["state"][-1]["stage"] == "NOTCH"
        # no more events: the 0.3 s ticker keeps state flowing
        t0 = time.monotonic()
        seen = 0
        while seen < 2:
            m = await recv_json(ws, timeout=2.0)
            seen += m["t"] == "state"
        assert time.monotonic() - t0 < 1.5


async def test_slow_client_does_not_starve_others(rig) -> None:
    slow = await connect(ws_url(rig.dash), proxy=None, open_timeout=5)  # never reads
    try:
        async with client(rig.dash) as fast:
            await collect(fast, {"rta"})
            assert rig.dash.clients == 2
            before = rig.dash.frames_seen
            for _ in range(200):  # 200 frames "at once": only the latest survives per flush
                rig.rta.tick()
                await asyncio.sleep(0)
            assert rig.dash.frames_seen >= before + 200
            got = await collect(fast, {"rta"}, timeout=3.0)
            assert len(got["rta"][-1]["db"]) == 100
            assert rig.dash.running
            status, _, body = await http_get(rig.dash.port, "/healthz")
            assert status == 200 and json.loads(body)["clients"] == 2
    finally:
        await slow.close()


def test_latest_frame_slot_drops_older() -> None:
    c = _Client(ws=None)  # type: ignore[arg-type]
    dash = DashboardServer.__new__(DashboardServer)
    dash._clients = {c}
    dash._broadcast_frame("f1")
    dash._broadcast_frame("f2")
    assert c.frame == "f2" and c.frames_dropped == 1 and c.wake.is_set()
    for i in range(300):
        dash._broadcast(f"e{i}")
    assert len(c.control) == 256 and c.control[0] == "e44" and c.control_dropped == 44


async def test_inbound_messages_ignored(rig) -> None:
    async with client(rig.dash) as ws:
        await collect(ws, {"rta"})
        await ws.send("hello?")
        await ws.send(json.dumps({"t": "set_fader", "db": 0}))
        got = await collect(ws, {"rta"})
        assert got["rta"] and rig.dash.running and rig.dash.clients == 1


# -- lifecycle --------------------------------------------------------------------------------


async def test_start_stop_idempotent_and_bind_failure(settings: Settings, d: Descriptor) -> None:
    kw = dict(band_hz=rta_band_hz(d), geq_band_hz=d.geq["band_hz"], host="127.0.0.1")
    a = DashboardServer(settings, EventBus(), None, None, port=0, **kw)
    assert not a.running
    await a.start()
    await a.start()
    assert a.running and a.port != 0 and a.url == f"http://127.0.0.1:{a.port}/"
    b = DashboardServer(settings, EventBus(), None, None, port=a.port, **kw)
    with pytest.raises(WebUIError):
        await b.start()
    assert not b.running
    await a.stop()
    await a.stop()
    assert not a.running
    with pytest.raises(OSError):
        await asyncio.wait_for(asyncio.open_connection("127.0.0.1", a.port), timeout=5.0)


async def test_stop_closes_connected_clients(settings: Settings, d: Descriptor) -> None:
    dash = DashboardServer(settings, EventBus(), None, None, band_hz=rta_band_hz(d), geq_band_hz=d.geq["band_hz"],
                           host="127.0.0.1", port=0)
    await dash.start()
    ws = await connect(ws_url(dash), proxy=None, open_timeout=5)
    await collect(ws, {"hello", "state", "notches"})
    t0 = time.monotonic()
    await dash.stop()
    assert time.monotonic() - t0 < 3.5
    with pytest.raises(Exception):
        await asyncio.wait_for(ws.recv(), timeout=5.0)
    await ws.close()


async def test_handler_propagates_its_own_cancellation(settings: Settings, d: Descriptor) -> None:
    """``stop()`` closes the server with ``close_connections=True``, which cancels the handler
    tasks — including one already in its ``finally``, waiting for a slow-to-die sender. Absorbing
    *that* CancelledError (rather than only the sender's) makes the task report a normal result
    and the cancellation never takes effect.
    """
    dash = DashboardServer(settings, EventBus(), None, None, band_hz=rta_band_hz(d), geq_band_hz=d.geq["band_hz"])

    class FakeWs:
        """A client whose connection drops while its third message is still going out."""

        remote_address = ("127.0.0.1", 1234)

        def __init__(self) -> None:
            self.sent: list[str] = []
            self.closed = asyncio.Event()

        async def send(self, text: str) -> None:
            self.sent.append(text)
            if len(self.sent) < 3:
                return
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                await asyncio.shield(asyncio.sleep(0.4))  # the send takes a moment to unwind
                raise

        def __aiter__(self):
            return self

        async def __anext__(self) -> str:
            while len(self.sent) < 3:
                await asyncio.sleep(0.01)
            self.closed.set()
            raise StopAsyncIteration  # the browser went away

    ws = FakeWs()
    task = asyncio.create_task(dash._handler(ws))
    await asyncio.wait_for(ws.closed.wait(), timeout=5.0)
    await asyncio.sleep(0.05)  # the handler is now parked in its finally, awaiting the sender
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled() and not dash._clients


# -- message shapes (no network) --------------------------------------------------------------


def test_state_without_provider_or_connection(settings: Settings, d: Descriptor) -> None:
    dash = DashboardServer(settings, EventBus(), None, None, band_hz=rta_band_hz(d), geq_band_hz=d.geq["band_hz"])
    st = dash.state_message()
    assert st["mode"] == "idle" and st["bus"] is None and st["candidate"] is None
    assert st["connection"] == {"state": "disconnected", "console": None, "rtt_ms": None}
    assert dash.notches_message() == {"t": "notches", "items": []}
    assert dash.host == settings.dash_host and dash.port == settings.dash_port
    h = dash.hello_message()
    assert h["fps"] == 20 and len(h["band_hz"]) == 100 and len(h["geq_band_hz"]) == 31


def test_state_from_the_real_cfs_state_and_connection_status(settings: Settings, d: Descriptor) -> None:
    """The real :class:`~x32mcp.cfs.CfsState`, so a rename or retype of its fields breaks here."""
    status = ConnectionStatus(
        state=ConnectionState.DEGRADED, console=ConsoleInfo("10.0.0.5", 10023, "X32-RACK", "X32RACK", "4.06", "V2.07"),
        last_rx_age_s=9.0, heartbeat_ok=False, pending_requests=1, rtt_ms=4.26, reconnect_attempts=2, error="silent",
    )
    provider = SimpleNamespace(state=CfsState(
        mode=CfsMode.RINGOUT, session_id="abc", bus="main", bus_name="Main LR", master_db=None, budget_left=0,
        candidate={"band": 69, "freq_hz": 2332.6, "confidence": 0.71, "level_db": -20.0},
        stage="HOLD", started=1700000000.0, rta_source="main.st",
    ))
    dash = DashboardServer(settings, EventBus(), provider, None, band_hz=rta_band_hz(d), geq_band_hz=d.geq["band_hz"],
                           connection_status=lambda: status, fps=500)
    assert dash.fps == 100, "fps clamped to 1..100"
    st = json.loads(dumps(dash.state_message()))
    assert st["mode"] == "ringout" and st["bus"] == "main" and st["session_id"] == "abc"
    assert st["master_db"] is None, "master_db None = not read (distinct from fully down)"
    assert st["rta_source"] == "main.st" and st["candidate"]["confidence"] == 0.71 and st["stage"] == "HOLD"
    assert st["connection"] == {"state": "degraded", "console": "X32-RACK", "rtt_ms": 4.3}
    assert dash.health()["mode"] == "ringout"
    assert dash.notches_message() == {"t": "notches", "items": []}


def test_state_from_a_duck_typed_provider(settings: Settings, d: Descriptor) -> None:
    """Any object (or dataclass) with a ``state`` works — the dashboard never imports cfs."""
    class Mode(str, Enum):
        WATCH = "CfsMode.watch"

    @dataclass
    class State:
        mode: Mode
        bus: int
        master_db: float
        rta_source: Target

    provider = SimpleNamespace(state=State(Mode.WATCH, 3, float("-inf"), Target("bus", 3)))
    dash = DashboardServer(settings, EventBus(), provider, None, band_hz=rta_band_hz(d), geq_band_hz=d.geq["band_hz"])
    st = json.loads(dumps(dash.state_message()))
    assert st["mode"] == "watch" and st["bus"] == 3 and st["master_db"] == "-oo" and st["rta_source"] == "bus.3"
    assert st["session_id"] is None and st["candidate"] is None


def test_frame_text_and_json_hygiene() -> None:
    frame = MeterFrame(15, 1700000000.5, tuple([-128.0, -31.756, -0.0039] + [-60.0] * 97))
    m = json.loads(DashboardServer.frame_text(frame))
    assert m["t"] == "rta" and m["ts"] == 1700000000.5 and len(m["db"]) == 100
    assert m["db"][:3] == [-128.0, -31.76, -0.0]
    lin = MeterFrame(0, 1.0, (1.0, 0.1, 0.0))
    assert json.loads(DashboardServer.frame_text(lin))["db"] == [0.0, -20.0, -90.0], "linear meters → dB"
    # lin_to_db floors at −90 but does not clamp above: a bare Infinity would break JSON.parse
    hot = MeterFrame(0, 2.0, (float("inf"), float("nan"), 0.1))
    assert json.loads(DashboardServer.frame_text(hot))["db"] == ["+oo", -90.0, -20.0]  # never Infinity/NaN
    assert dumps({"x": float("nan"), "y": float("inf"), "z": {1, }}) == '{"x":null,"y":"+oo","z":[1]}'


def test_a_fully_down_master_reaches_the_dashboard_as_minus_oo(settings: Settings, d: Descriptor) -> None:
    """−∞ must not arrive as null: the page renders null as "—" (unknown) but "-oo" as "−oo".

    cfs._db1 used to collapse −∞ and "not read" into None, so a bus master sitting fully down
    showed as unknown on the dashboard — the same conflation fixed in desk._db1 at M5.
    """
    from x32mcp.cfs import _db1 as cfs_db1

    assert cfs_db1(float("-inf")) == float("-inf"), "−∞ is preserved, not nulled"
    assert cfs_db1(None) is None, "not-read stays None"
    assert cfs_db1(-12.34) == -12.3

    provider = SimpleNamespace(state=CfsState(
        mode=CfsMode.RINGOUT, session_id="s", bus=3, bus_name="Wedge A",
        master_db=cfs_db1(float("-inf")), budget_left=2, candidate=None, stage="HOLD",
        started=1700000000.0, rta_source="bus.3",
    ))
    dash = DashboardServer(settings, EventBus(), provider, None, band_hz=rta_band_hz(d),
                           geq_band_hz=d.geq["band_hz"], connection_status=None)
    st = json.loads(dumps(dash.state_message()))
    assert st["master_db"] == "-oo", st
