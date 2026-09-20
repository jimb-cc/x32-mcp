"""Desk facade against the FakeDesk (DESIGN.md §13, §20): every read/write path, ramp step
count, mute inversion on the wire, clamps, relative limit, panic timing, snapshot-before-write,
name resolution, scenes, restore via ``/`` lines and GEQ writes."""

from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio

from conftest import wait_for_state, wait_until
from x32mcp.connection import ConnectionState
from x32mcp.desk import Desk, DeskError
from x32mcp.events import EventBus
from x32mcp.nodes import SnapshotStore
from x32mcp.policy import Policy, PolicyError
from x32mcp.scales import db_to_fader, quantize
from x32mcp.targets import Target

GRID_1024 = 1.0 / 1023  # one fader step (scales_params.md §2.3)
GRID_161 = 1.0 / 160  # one send step


@pytest.fixture
def events() -> EventBus:
    return EventBus()


@pytest.fixture
def policy(descriptor, events) -> Policy:
    return Policy(descriptor, events)


@pytest_asyncio.fixture
async def desk(descriptor, conn, policy, events, tmp_path) -> Desk:
    d = Desk(descriptor, conn, policy, events, SnapshotStore(tmp_path / "snapshots"))
    try:
        yield d
    finally:
        await d.close()


async def settle(conn) -> None:
    """SETs are fire-and-forget: one GET round trip guarantees the fake desk processed them."""
    await conn.get("/-stat/selidx")


# ---------------------------------------------------------------------------------------- reads


async def test_get_channel_fields_and_units(desk):
    ch = await desk.get_channel(1)
    assert ch["target"] == "ch.1" and ch["label"] == "Ch 1" and ch["family"] == "ch" and ch["index"] == 1
    assert ch["name"] == "Ch01" and ch["color"] == "RD" and ch["icon"] == 1 and ch["source"] == "IN01"
    assert ch["fader_db"] == 0.0 and ch["fader"] == "0.0" and ch["muted"] is False
    assert ch["pan"] == 0 and ch["lr_assigned"] is True and ch["mono_assigned"] is False
    assert ch["mono_level_db"] is None and ch["mono_level"] == "-oo"  # -inf is never a float in results
    assert ch["preamp"] == {"trim_db": 0.0, "invert": False, "hpf_on": False, "hpf_slope": "24", "hpf_hz": pytest.approx(101.0, abs=1.0)}
    eq = ch["eq"]
    assert eq["on"] is True and [b["band"] for b in eq["bands"]] == [1, 2, 3, 4]
    assert eq["bands"][0]["type"] == "PEQ" and eq["bands"][0]["gain_db"] == 0.0 and eq["bands"][0]["q"] == 2.0
    assert eq["bands"][2]["freq_hz"] == pytest.approx(4000, rel=0.03)
    assert ch["comp"]["on"] is False and ch["comp"]["ratio"] == "3.0" and ch["comp"]["threshold_db"] == 0.0
    assert ch["comp"]["mix_pct"] == 100 and ch["comp"]["position"] == "POST" and ch["comp"]["key_source"] == "OFF"
    assert ch["gate"]["on"] is False and ch["gate"]["mode"] == "GATE" and ch["gate"]["threshold_db"] == -80.0
    assert ch["gate"]["range_db"] == 60.0 and ch["gate"]["release_ms"] == pytest.approx(151, abs=3)
    assert ch["insert"] == {"on": False, "pos": "POST", "sel": "OFF", "fx_slot": None, "side": None}


async def test_get_bus_main_and_dca(desk):
    bus = await desk.get_bus(3)
    assert bus["name"] == "Bus03" and bus["gate"] is None and bus["preamp"] is None and bus["comp"]["on"] is False
    assert len(bus["eq"]["bands"]) == 6 and bus["insert"]["sel"] == "OFF" and bus["source"] is None
    main = await desk.get_main()
    assert main["st"]["name"] == "Main" and main["st"]["fader_db"] == 0.0 and main["st"]["pan"] == 0
    assert main["m"]["name"] == "M/C" and main["m"]["pan"] is None and main["m"]["muted"] is False
    dca = await desk.get_strip("dca.2")
    assert dca["name"] == "DCA2" and dca["fader_db"] == 0.0 and dca["muted"] is False and dca["eq"] is None


async def test_get_sends_eq_dynamics_units(desk, conn, descriptor):
    await conn.set("/ch/01/mix/03/level", descriptor.scale("send").to_raw(-12.0))
    await conn.set("/ch/01/mix/04/on", 0)
    await conn.set("/ch/01/mix/03/pan", 0.75)
    sends = await desk.get_sends(Target("ch", 1))
    assert len(sends) == 16
    s3, s4 = sends[2], sends[3]
    assert s3["bus"] == 3 and s3["bus_name"] == "Bus03" and s3["to"] == "bus.3"
    assert s3["level_db"] == -12.0 and s3["level"] == "-12.0" and s3["muted"] is False and s3["pan"] == 50 and s3["type"] == "POST"
    assert s4["muted"] is True and s4["level_db"] is None and s4["level"] == "-oo"
    assert s4["pan"] == 50 and s4["type"] == "POST"  # even sends share the odd partner's pan/type (scales_params.md §4.9)
    bus_sends = await desk.get_sends("bus.1")
    assert len(bus_sends) == 6 and bus_sends[0]["to"] == "mtx.1" and bus_sends[0]["name"] == "Mtx1"
    with pytest.raises(DeskError) as ei:
        await desk.get_sends("mtx.1")
    assert ei.value.code == "NOT_SUPPORTED"
    eq = await desk.get_eq("ch.2")
    assert eq["on"] is True and len(eq["bands"]) == 4
    dyn = await desk.get_dynamics("ch.2")
    assert dyn["gate"]["on"] is False and dyn["comp"]["attack_ms"] == 10.0 and dyn["comp"]["hold_ms"] == pytest.approx(10.0, abs=0.5)
    dyn_bus = await desk.get_dynamics("bus.2")
    assert dyn_bus["gate"] is None and dyn_bus["comp"]["on"] is False


async def test_cache_is_invalidated_by_pushes_and_own_writes(desk, conn, fakedesk, events):
    await wait_until(lambda: len(fakedesk.clients) >= 1, what="/xremote registration")
    assert (await desk.get_channel(7))["name"] == "Ch07"
    fakedesk.set_value("/ch/07/config/name", "Pushed")  # a front-panel change is pushed to us
    deadline = time.monotonic() + 3.0
    while (await desk.get_channel(7))["name"] != "Pushed":
        assert time.monotonic() < deadline, "push did not invalidate the cached section"
        await asyncio.sleep(0.02)
    assert desk.stats["cached_sections"] > 0
    await desk.label("ch.7", name="Mine")  # our own write drops the section too
    assert (await desk.get_channel(7))["name"] == "Mine"
    # a write by another module through the same connection is seen via the bus's "write" event
    assert "/ch/07/config" in desk._cache
    events.publish("write", address="/ch/07/config/color", args=[6])
    assert "/ch/07/config" not in desk._cache
    desk.invalidate()
    assert desk.stats["cached_sections"] == 0


async def test_a_cancelled_reader_does_not_cancel_the_other_waiters(desk, conn, monkeypatch):
    """Concurrent reads of one path share a single request. When the caller that created it is
    cancelled (its tool timed out), the others must get a reportable error, not a CancelledError
    that escapes the tool wrapper into the MCP task group."""
    real = conn.node_many
    started = asyncio.Event()

    async def slow(paths, concurrency=16):
        started.set()
        await asyncio.sleep(0.5)
        return await real(paths, concurrency)

    monkeypatch.setattr(conn, "node_many", slow)
    a = asyncio.create_task(desk._read_sections(["/ch/01/config"]))
    await asyncio.wait_for(started.wait(), 2.0)
    b = asyncio.create_task(desk._read_sections(["/ch/01/config"]))
    await asyncio.sleep(0.05)
    a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await a
    with pytest.raises(DeskError) as ei:
        await asyncio.wait_for(b, timeout=2.0)
    assert ei.value.code == "TIMEOUT" and b.cancelled() is False
    monkeypatch.undo()
    vals = await desk._read_sections(["/ch/01/config"])  # and the path is readable again
    assert vals["/ch/01/config"]["config/name"] == "Ch01"


# ---------------------------------------------------------------------------------------- faders


async def test_set_fader_ramps_to_minus_6(desk, conn, conn_factory, fakedesk, policy):
    other = await conn_factory()
    pushes: list[float] = []
    other.on_update(lambda a, args: pushes.append(args[0]) if a == "/ch/01/mix/fader" else None)
    await wait_until(lambda: len(fakedesk.clients) == 2, what="both /xremote registrations")
    t0 = time.perf_counter()
    res = await desk.set_level("ch.1", -6.0, ramp_ms=300)
    dt = time.perf_counter() - t0
    assert res["before_db"] == 0.0 and res["after_db"] == -6.0 and res["after"] == "-6.0"
    assert res["ramp_ms"] == 300 and res["steps"] == 15 and res["clamped"] is None and res["superseded"] is False
    assert 0.25 <= dt < 1.5
    await settle(conn)
    assert fakedesk.get("/ch/01/mix/fader") == pytest.approx(db_to_fader(-6.0), abs=GRID_1024)
    await wait_until(lambda: len(pushes) >= 15, what="15 ramp step pushes")
    assert len(pushes) == 15 and pushes == sorted(pushes, reverse=True)  # monotonic glide, one push per step
    assert pushes[-1] == pytest.approx(quantize(db_to_fader(-6.0), 1024), abs=1e-6)
    assert policy.snapshot_before_write is False


async def test_adjust_fader_relative_limit_force_and_clamp(desk, conn, fakedesk, descriptor):
    fader = descriptor.scale("fader")
    res = await desk.adjust_level(2, 3.0, ramp_ms=0)
    assert res["before_db"] == 0.0 and res["after_db"] == 3.0 and res["delta_db"] == 3.0 and res["steps"] == 1
    await settle(conn)
    assert fakedesk.get("/ch/02/mix/fader") == pytest.approx(fader.to_raw(3.0), abs=GRID_1024)
    # > 6 dB relative without force is refused before anything is written
    with pytest.raises(PolicyError) as ei:
        await desk.adjust_level("ch.3", -9.0, ramp_ms=0)
    assert ei.value.code == "RELATIVE_TOO_LARGE" and ei.value.details["limit_db"] == 6
    await settle(conn)
    assert fakedesk.value("/ch/03/mix/fader") == pytest.approx(0.0, abs=0.05)
    res = await desk.adjust_level("ch.3", -9.0, ramp_ms=0, force=True)
    assert res["after_db"] == -9.0
    await settle(conn)
    assert fakedesk.value("/ch/03/mix/fader") == pytest.approx(-9.0, abs=0.05)
    # absolute moves obey the same limit; fades to / from -oo do not (bounded by silence / the ceiling)
    with pytest.raises(PolicyError):
        await desk.set_level("ch.3", -20.0, ramp_ms=0)
    res = await desk.set_level("ch.4", -90.0, ramp_ms=0)  # -90 dB is the bottom stop = -oo
    assert res["after_db"] is None and res["after"] == "-oo"
    await settle(conn)
    assert fakedesk.get("/ch/04/mix/fader") == 0.0
    res = await desk.set_level("ch.4", -20.0, ramp_ms=0)
    assert res["before_db"] is None and res["before"] == "-oo" and res["after_db"] == -20.0
    # ceiling clamp is applied and reported
    res = await desk.set_level("ch.5", +8.0, ramp_ms=0)
    assert res["after_db"] == 5.0 and res["requested_db"] == 8.0
    assert res["clamped"]["limit"] == 5.0 and res["clamped"]["requested"] == 8.0 and res["clamped"]["reason"].startswith("CLAMPED_TO_LIMIT")
    await settle(conn)
    assert fakedesk.value("/ch/05/mix/fader") == pytest.approx(5.0, abs=0.05)
    # bus masters clamp at 0 dB
    res = await desk.set_level("bus.2", +3.0, ramp_ms=0)
    assert res["after_db"] == 0.0 and res["clamped"]["limit"] == 0.0


async def test_newer_ramp_supersedes_older_on_same_address(desk, conn, fakedesk):
    await desk.set_level("ch.9", -1.0, ramp_ms=0)  # take the pre-write snapshot out of the timing
    first = asyncio.create_task(desk.set_level("ch.8", -6.0, ramp_ms=1000))
    await asyncio.sleep(0.25)
    second = await desk.set_level("ch.8", -2.0, ramp_ms=100)
    res = await first
    assert res["superseded"] is True and res["after_db"] is not None and -6.0 < res["after_db"] < 0.0
    assert second["superseded"] is False and second["after_db"] == -2.0 and desk.stats["ramps"] == 0
    await settle(conn)
    assert fakedesk.value("/ch/08/mix/fader") == pytest.approx(-2.0, abs=0.05)


async def test_mute_is_inverted_on_the_wire(desk, conn, fakedesk):
    res = await desk.set_mute("ch.1", True)
    assert res["muted"] is True and res["was_muted"] is False
    await settle(conn)
    assert fakedesk.get("/ch/01/mix/on") == 0  # 0 = OFF = muted (scales_params.md §4.8)
    assert (await desk.get_channel(1))["muted"] is True
    res = await desk.set_mute(Target("ch", 1), False)
    assert res["was_muted"] is True
    await settle(conn)
    assert fakedesk.get("/ch/01/mix/on") == 1
    assert (await desk.get_channel(1))["muted"] is False
    await desk.set_mute("dca.3", True)
    await settle(conn)
    assert fakedesk.get("/dca/3/on") == 0


async def test_sends_set_adjust_and_units(desk, conn, fakedesk, descriptor):
    send = descriptor.scale("send")
    res = await desk.set_send("ch.1", 3, -10.0, ramp_ms=0)
    assert res["send_to"] == 3 and res["kind"] == "send" and res["before_db"] is None and res["after_db"] == -10.0
    await settle(conn)
    assert fakedesk.get("/ch/01/mix/03/level") == pytest.approx(send.to_raw(-10.0), abs=GRID_161)
    res = await desk.adjust_send("ch.1", 3, +3.0, ramp_ms=0)
    assert res["before_db"] == -10.0 and res["after_db"] == -7.0
    await settle(conn)
    assert fakedesk.value("/ch/01/mix/03/level") == pytest.approx(-7.0, abs=0.05)
    with pytest.raises(PolicyError):  # -7 -> 0 (clamped) is a 7 dB move: refused like any other
        await desk.set_send("ch.1", 3, +4.0, ramp_ms=0)
    await desk.adjust_send("ch.1", 3, +3.0, ramp_ms=0)  # -4
    res = await desk.set_send("ch.1", 3, +4.0, ramp_ms=0)  # sends clamp at 0 dB (a 4 dB move)
    assert res["after_db"] == 0.0 and res["clamped"]["limit"] == 0.0
    sends = await desk.get_sends("ch.1")
    assert sends[2]["level_db"] == 0.0
    await desk.set_send_mute("ch.1", 3, True)
    await settle(conn)
    assert fakedesk.get("/ch/01/mix/03/on") == 0
    with pytest.raises(DeskError) as ei:
        await desk.set_send("ch.1", 17, -3.0)
    assert ei.value.code == "BAD_ARGUMENT"


# ---------------------------------------------------------------------------------------- processing


async def test_set_eq_band_writes_raw_on_the_grids(desk, conn, fakedesk, descriptor):
    freq, gain, q = descriptor.scale("freq"), descriptor.scale("eq_gain"), descriptor.scale("q")
    res = await desk.set_eq_band("ch.1", 2, freq_hz=1000.0, gain_db=-3.25, q=4.0, type="HShv", on=True)
    a = res["applied"]
    assert a["type"] == "HShv" and a["on"] is True and a["gain_db"] == -3.25 and a["q"] == pytest.approx(4.0, rel=0.03)
    assert a["freq_hz"] == pytest.approx(1000.0, rel=0.02) and res["clamped"] is None
    await settle(conn)
    assert fakedesk.get("/ch/01/eq/2/f") == pytest.approx(freq.to_raw(1000.0), abs=1.0 / 200)
    assert fakedesk.get("/ch/01/eq/2/g") == pytest.approx(gain.to_raw(-3.25), abs=1.0 / 120)
    assert fakedesk.get("/ch/01/eq/2/q") == pytest.approx(q.to_raw(4.0), abs=1.0 / 71)
    assert fakedesk.get("/ch/01/eq/2/type") == 4 and fakedesk.get("/ch/01/eq/on") == 1
    res = await desk.set_eq_band(1, 2, gain_db=+20.0)  # ±15 dB clamp reported
    assert res["applied"]["gain_db"] == 15.0 and res["clamped"][0]["limit"] == 15.0
    eq = await desk.get_eq("ch.1")
    assert eq["bands"][1]["gain_db"] == 15.0 and eq["bands"][1]["type"] == "HShv"
    with pytest.raises(DeskError) as ei:
        await desk.set_eq_band("ch.1", 5, gain_db=0.0)
    assert ei.value.code == "BAD_ARGUMENT"
    with pytest.raises(DeskError) as ei:
        await desk.set_eq_band("ch.1", 1, type="BOGUS")
    assert ei.value.code == "BAD_ARGUMENT"


async def test_set_pan_comp_gate(desk, conn, fakedesk):
    res = await desk.set_pan("ch.1", 50)
    assert res["pan"] == 50
    await settle(conn)
    assert fakedesk.get("/ch/01/mix/pan") == pytest.approx(0.75, abs=1e-6)
    with pytest.raises(DeskError):
        await desk.set_pan("ch.1", 150)
    res = await desk.set_comp("ch.1", on=True, threshold_db=-20.0, ratio=4, attack_ms=10, release_ms=100, knee=2, makeup_db=3.0, mix_pct=100)
    a = res["applied"]
    assert a["on"] is True and a["threshold_db"] == -20.0 and a["ratio"] == "4.0" and a["knee"] == 2.0 and a["makeup_db"] == 3.0
    await settle(conn)
    assert fakedesk.value("/ch/01/dyn/on") is True
    assert fakedesk.value("/ch/01/dyn/thr") == pytest.approx(-20.0, abs=0.3)
    assert fakedesk.value("/ch/01/dyn/ratio") == "4.0"
    assert fakedesk.value("/ch/01/dyn/release") == pytest.approx(100.0, rel=0.05)
    assert fakedesk.value("/ch/01/dyn/mgain") == pytest.approx(3.0, abs=0.3)
    comp = (await desk.get_dynamics("ch.1"))["comp"]
    assert comp["on"] is True and comp["ratio"] == "4.0" and comp["threshold_db"] == -20.0
    res = await desk.set_gate("ch.1", on=True, threshold_db=-40.0, range_db=30.0, attack_ms=5, hold_ms=50, release_ms=200)
    assert res["applied"]["on"] is True and res["applied"]["range_db"] == 30.0
    await settle(conn)
    assert fakedesk.value("/ch/01/gate/thr") == pytest.approx(-40.0, abs=0.3)
    assert fakedesk.value("/ch/01/gate/hold") == pytest.approx(50.0, rel=0.1)
    with pytest.raises(DeskError) as ei:
        await desk.set_gate("bus.1", on=True)
    assert ei.value.code == "NOT_SUPPORTED"


# ---------------------------------------------------------------------------------------- names


async def test_label_and_resolve(desk, conn, fakedesk):
    res = await desk.label("ch.5", name="Vox Tony", color="CY", icon=3)
    assert res["applied"] == {"name": "Vox Tony", "color": "CY", "icon": 3}
    await settle(conn)
    assert await conn.node("/ch/05/config") == '/ch/05/config "Vox Tony" 3 CY 5'
    assert (await desk.resolve("tony")).key == "ch.5"
    assert (await desk.resolve("VOX TONY")).key == "ch.5"
    assert (await desk.resolve("ch.6")).key == "ch.6" and (await desk.resolve(7)).key == "ch.7"
    assert (await desk.resolve("bus 3")).key == "bus.3"
    await desk.label("bus.1", name="Tony IEM")
    with pytest.raises(DeskError) as ei:
        await desk.resolve("tony")
    assert ei.value.code == "AMBIGUOUS_NAME"
    assert {c["target"] for c in ei.value.details["candidates"]} == {"ch.5", "bus.1"}
    assert (await desk.resolve("tony", families=("ch",))).key == "ch.5"
    assert (await desk.resolve("tony iem")).key == "bus.1"  # exact name beats the substring hit
    with pytest.raises(DeskError) as ei:
        await desk.resolve("nobody")
    assert ei.value.code == "UNKNOWN_TARGET"
    names = await desk.get_names("ch")
    assert names[5] == "Vox Tony" and names[1] == "Ch01" and len(names) == 32
    res = await desk.label("ch.6", name="A name that is far too long")
    assert res["truncated"] is True and res["applied"]["name"] == "A name that "
    with pytest.raises(DeskError):
        await desk.label("ch.6", color="PINK")
    # transport.md §6.3/§6.6: node text has no escape for a double quote and the desk's parser
    # stops at the closing one, so it never reaches the wire.
    res = await desk.label("ch.8", name='Vox "T"')
    assert res["quote_replaced"] is True and res["applied"]["name"] == "Vox 'T'"
    await settle(conn)
    assert fakedesk.value("/ch/08/config/name") == "Vox 'T'"
    assert '"' not in await conn.node("/ch/08/config") or (await conn.node("/ch/08/config")).count('"') == 2


# ---------------------------------------------------------------------------------------- panic


async def test_panic_mutes_24_outputs_fast(desk, conn, fakedesk, policy):
    res = await desk.panic()
    assert res["count"] == 24 and res["elapsed_ms"] < 200 and res["delivered"] == "sent"
    assert res["muted"][:2] == ["main.st", "main.m"] and "bus.16" in res["muted"] and "mtx.6" in res["muted"]
    await settle(conn)
    for key in res["muted"]:
        t = Target(*key.split(".")) if key.startswith("main") else Target(key.split(".")[0], int(key.split(".")[1]))
        assert fakedesk.get(f"{t.osc_prefix}/mix/on") == 0, key
    assert fakedesk.get("/ch/01/mix/on") == 1  # inputs are left alone
    assert policy.snapshot_before_write is True  # no dump on the panic path
    assert res["failed"] == []


async def test_panic_still_mutes_while_degraded(desk, conn, fakedesk):
    """BRIEF §9: the PSU cuts out mid-show. conn.set refuses while DEGRADED, so panic falls back to
    a raw fire-and-forget datagram and reports delivered='unconfirmed' — it never gives up early."""
    fakedesk.silent = True
    await wait_for_state(conn, ConnectionState.DEGRADED, timeout=8.0)
    fakedesk.silent = False  # the desk is alive again; we are still DEGRADED until it answers
    res = await asyncio.wait_for(desk.panic(), timeout=5.0)
    assert res["count"] == 24 and res["delivered"] == "unconfirmed" and res["elapsed_ms"] < 200
    assert res["failed"] == []
    await wait_until(  # fire-and-forget: wait for the last of the 24 datagrams, not just the first
        lambda: all(fakedesk.get(a) == 0 for a in ("/main/st/mix/on", "/bus/16/mix/on", "/mtx/06/mix/on")),
        what="panic datagrams applied",
    )
    assert fakedesk.get("/ch/01/mix/on") == 1  # inputs are left alone


async def test_panic_keeps_going_when_a_send_fails(desk, conn, fakedesk, monkeypatch):
    """One unreachable address must not cost the other 23 outputs (BRIEF §4: never blocked)."""
    from x32mcp.connection import NotConnected

    real_set = conn.set
    bad = {"/bus/01/mix/on", "/mtx/06/mix/on"}

    async def flaky(address, *args, **kw):
        if address in bad:
            raise NotConnected("socket went away mid-panic")
        await real_set(address, *args, **kw)

    async def no_raw(address, *args, **kw):
        raise NotConnected("no socket")

    monkeypatch.setattr(conn, "set", flaky)
    monkeypatch.setattr(conn, "send_raw", no_raw)
    res = await desk.panic()
    assert res["count"] == 22 and sorted(res["failed"]) == ["bus.1", "mtx.6"]
    assert "mtx.6" not in res["muted"] and "main.st" in res["muted"]
    await settle(conn)
    assert fakedesk.get("/main/st/mix/on") == 0 and fakedesk.get("/bus/16/mix/on") == 0


# ---------------------------------------------------------------------------------------- snapshots


async def test_pre_write_snapshot_taken_exactly_once(desk, conn, policy, tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    assert store.list() == [] and policy.snapshot_before_write is True
    await desk.set_level("ch.1", -3.0, ramp_ms=0)
    await desk.set_mute("ch.2", True)
    await desk.set_pan("ch.3", -20)
    metas = store.list()
    assert len(metas) == 1 and metas[0].label == "auto-pre-write" and metas[0].id.endswith("-auto-pre-write")
    assert policy.snapshot_before_write is False and desk.pre_write_snapshot is not None
    snap = store.load("latest")
    assert snap.state.get("/ch/01/mix/fader") == pytest.approx(0.0, abs=0.05)  # the state BEFORE the first write
    assert snap.state.missing == []
    assert await desk.ensure_pre_write_snapshot() is None


async def test_restore_writes_slash_lines_and_puts_fader_back(desk, conn, fakedesk, tmp_path):
    slash_lines: list[str] = []
    orig = fakedesk._handlers["/"]

    def counting(msg, data, addr):
        slash_lines.append(msg.args[0])
        orig(msg, data, addr)

    fakedesk._handlers["/"] = counting
    await desk.set_level("ch.1", -6.0, ramp_ms=0)  # first write: auto snapshot at 0 dB
    await desk.set_mute("ch.2", True)
    await settle(conn)
    assert fakedesk.value("/ch/01/mix/fader") == pytest.approx(-6.0, abs=0.05)
    snap = desk.pre_write_snapshot
    assert snap is not None
    n_before = len(slash_lines)
    res = await desk.restore(snap)
    assert res["written"] >= 2 and res["failed"] == [] and res["lines"] == res["written"] and "aborted" not in res
    written = slash_lines[n_before:]
    assert any(line.startswith("/ch/01/mix ") for line in written) and any(line.startswith("/ch/02/mix ") for line in written)
    assert fakedesk.value("/ch/01/mix/fader") == pytest.approx(0.0, abs=0.05)
    assert fakedesk.get("/ch/02/mix/on") == 1
    assert (await desk.get_channel(1))["fader_db"] == 0.0  # cache dropped by the restore
    # scoped restore only touches the scope
    await desk.set_level("ch.1", -4.0, ramp_ms=0)
    await desk.set_level("ch.3", -4.0, ramp_ms=0)
    res = await desk.restore(snap, scope="ch.1")
    assert res["written"] == 1 and res["scope"] == "ch.1"
    assert fakedesk.value("/ch/01/mix/fader") == pytest.approx(0.0, abs=0.05)
    assert fakedesk.value("/ch/03/mix/fader") == pytest.approx(-4.0, abs=0.05)


# ---------------------------------------------------------------------------------------- scenes


async def test_list_scenes_current_scene_recall_and_save(desk, conn, fakedesk, policy):
    scenes = await desk.list_scenes()
    assert len(scenes) == 100
    assert scenes[0] == {"index": 0, "name": "Init", "notes": "", "has_data": True}
    assert scenes[1]["name"] == "The Molecules" and scenes[1]["has_data"] is True
    assert scenes[3]["name"] == "Empty" and scenes[3]["has_data"] is False and scenes[50]["has_data"] is False
    assert await desk.current_scene() == {"index": 0, "name": "Init", "has_data": True, "show_control": "SCENES"}
    assert (await desk.get_channel(5))["name"] == "Ch05"
    res = await desk.recall_scene(1)
    assert res == {"index": 1, "name": "The Molecules", "has_data": True, "verified": True,
                   "previous_index": 0, "show_control": "SCENES"}
    assert await conn.get("/-show/prepos/current") == 1
    assert (await desk.get_channel(5))["name"] == "Vox Tony"  # cache dropped by the recall
    assert (await desk.current_scene())["index"] == 1
    assert (await desk.resolve("tony", families=("ch",))).key == "ch.5"
    with pytest.raises(DeskError) as ei:  # the scene also names bus 1 'Tony IEM'
        await desk.resolve("tony")
    assert ei.value.code == "AMBIGUOUS_NAME"
    assert (await desk.resolve("tony iem")).key == "bus.1"
    # save a scene, verify the slot
    res = await desk.save_scene(9, "Soundcheck", "after line check")
    assert res == {"index": 9, "name": "Soundcheck", "notes": "after line check", "ok": True}
    scenes = await desk.list_scenes()
    assert scenes[9]["name"] == "Soundcheck" and scenes[9]["has_data"] is True and scenes[9]["notes"] == "after line check"
    assert fakedesk.scenes[9]["state"]["/ch/05/config/name"] == "Vox Tony"
    # show mode refuses both (defence in depth; the server checks too)
    policy.show_mode = True
    with pytest.raises(PolicyError) as ei:
        await desk.recall_scene(0)
    assert ei.value.code == "SHOW_MODE_BLOCKS"
    with pytest.raises(PolicyError):
        await desk.save_scene(10, "x")
    policy.show_mode = False
    with pytest.raises(DeskError):
        await desk.recall_scene(100)


async def test_scene_pointer_is_not_trusted_unless_show_control_is_scenes(desk, conn, fakedesk):
    """fx_routing_scenes.md §6.2: /-show/prepos/current indexes the CUES/SCENES/SNIPPETS list that
    /-prefs/show_control selects, so it is only a scene slot while that preference says SCENES."""
    fakedesk.set_value("/-prefs/show_control", "CUES")
    desk.invalidate()
    cur = await desk.current_scene()
    assert cur["index"] is None and cur["name"] == "" and cur["show_control"] == "CUES"
    assert "CUES" in cur["note"]
    t0 = time.perf_counter()
    res = await desk.recall_scene(1)  # the recall still goes out; only the verification is impossible
    assert res["verified"] is None and res["show_control"] == "CUES" and "note" in res
    assert (time.perf_counter() - t0) < 1.0  # no 2 s poll of a pointer that means something else
    await settle(conn)
    assert fakedesk.value("/ch/05/config/name") == "Vox Tony"  # the desk really did load scene 1
    fakedesk.set_value("/-prefs/show_control", "SCENES")
    desk.invalidate()
    assert (await desk.current_scene())["index"] == 1  # back to a real scene index


# ---------------------------------------------------------------------------------------- guarded executors


async def test_tier1_refuses_guarded_and_tier2_executors_work(desk, conn, fakedesk, policy):
    with pytest.raises(DeskError) as ei:
        await desk.set_level("main.st", -3.0, ramp_ms=0)
    assert ei.value.code == "GUARDED"
    with pytest.raises(DeskError) as ei:
        await desk.set_mute("main.st", True)
    assert ei.value.code == "GUARDED"
    assert "set_main_mute" in str(ei.value) and "Main LR" in str(ei.value)  # name the tool, not the OSC address
    with pytest.raises(DeskError) as ei:
        await desk.set_mute("main.m", True)
    assert "Main M/C mute is a guarded (Tier 2) parameter; use set_main_mute" in str(ei.value)
    with pytest.raises(DeskError) as ei:
        await desk.set_send("main.st", 1, -3.0)
    assert ei.value.code == "GUARDED"
    await settle(conn)
    assert fakedesk.value("/main/st/mix/fader") == pytest.approx(0.0, abs=0.05)
    res = await desk.set_main_level("st", -3.0, ramp_ms=100)
    assert res["after_db"] == -3.0 and res["steps"] == 5
    res = await desk.set_main_level("m", +2.0, ramp_ms=0)  # main ceiling 0 dB
    assert res["after_db"] == 0.0 and res["clamped"]["limit"] == 0.0
    res = await desk.set_main_level("lr", -20.0, ramp_ms=0)  # no relative limit on the confirmed path
    assert res["after_db"] == -20.0
    res = await desk.set_main_mute("st", True)
    assert res["muted"] is True and res["was_muted"] is False
    await settle(conn)
    assert fakedesk.get("/main/st/mix/on") == 0 and fakedesk.value("/main/st/mix/fader") == pytest.approx(-20.0, abs=0.05)
    assert policy.snapshot_before_write is False  # executors still snapshot before the first write
    with pytest.raises(DeskError):
        await desk.set_main_level("x", 0.0)


async def test_set_source_phantom_insert_and_inserts(desk, conn, fakedesk, descriptor):
    res = await desk.set_source("ch.1", "IN05")
    assert res["source"] == "IN05"
    await settle(conn)
    assert fakedesk.get("/ch/01/config/source") == 5
    with pytest.raises(DeskError):
        await desk.set_source("ch.1", "NOPE")
    res = await desk.set_phantom(0, True)
    assert res == {"headamp": 0, "address": "/headamp/000/phantom", "on": True, "target": None}
    await settle(conn)
    assert fakedesk.get("/headamp/000/phantom") == 1
    # ch 1 now sources IN05; routing IN 1-8 = AN1-8 -> head amp 004 (scales_params.md §12.4)
    assert await desk.headamp_index_for("ch.1") == 4
    res = await desk.set_phantom(Target("ch", 1), True)
    assert res["headamp"] == 4 and res["target"] == "ch.1"
    await settle(conn)
    assert fakedesk.get("/headamp/004/phantom") == 1
    await desk.set_source("ch.2", "USBL")
    assert await desk.headamp_index_for("ch.2") is None
    with pytest.raises(DeskError) as ei:
        await desk.set_phantom("ch.2", True)
    assert ei.value.code == "NOT_SUPPORTED"
    res = await desk.set_insert("bus.3", sel="FX5L", on=True, pos="POST")
    assert res["applied"] == {"sel": "FX5L", "pos": "POST", "on": True}
    await settle(conn)
    assert fakedesk.value("/bus/03/insert/sel") == "FX5L" and fakedesk.get("/bus/03/insert/on") == 1
    inserts = await desk.get_inserts()
    assert len(inserts) == 32 + 16 + 6 + 2
    assert inserts["bus.3"] == {"on": True, "pos": "POST", "sel": "FX5L", "fx_slot": 5, "side": "A"}
    assert inserts["ch.1"]["sel"] == "OFF"
    with pytest.raises(DeskError) as ei:
        await desk.set_insert("auxin.1", on=True)
    assert ei.value.code == "NOT_SUPPORTED"


# ---------------------------------------------------------------------------------------- GEQ


async def test_set_geq_band_and_get_fx_decode(desk, conn, fakedesk, descriptor):
    geq = descriptor.scale("geq_gain")
    fx = await desk.get_fx(5)
    assert fx["type"] == "DES2" and fx["is_geq"] is False and fx["geq"] is None and len(fx["params"]) == 64
    with pytest.raises(DeskError) as ei:
        await desk.set_geq_band(5, "A", 22, -6.0)
    assert ei.value.code == "NOT_A_GEQ"
    res = await desk.set_fx_type(5, "GEQ2")
    assert res == {"slot": 5, "type": "GEQ2", "index": 0}  # fx_type_58: GEQ2 = 0
    await settle(conn)
    assert fakedesk.get("/fx/5/type") == 0
    assert await desk.set_geq_band(5, "A", 22, -6.0) is None  # band 22 = 2.5 kHz -> par/22, side A
    await desk.set_geq_band(5, "B", 22, -9.0)  # side B -> par/54 (fx_routing_scenes.md §2.1)
    await desk.set_geq_band(5, "L", 32, -1.5)  # 32 = master A
    await settle(conn)
    assert fakedesk.get("/fx/5/par/22") == pytest.approx(geq.to_raw(-6.0), abs=1e-6)
    assert fakedesk.get("/fx/5/par/22") == pytest.approx(0.3, abs=1e-6)
    assert fakedesk.get("/fx/5/par/54") == pytest.approx(0.2, abs=1e-6)
    assert fakedesk.get("/fx/5/par/32") == pytest.approx(0.45, abs=1e-6)
    assert fakedesk.get("/fx/5/par/64") == pytest.approx(0.5, abs=1e-6)
    fx = await desk.get_fx(5)
    assert fx["is_geq"] is True and fx["geq"]["kind"] == "dual" and fx["geq"]["band_hz"][21] == 2500
    assert fx["geq"]["a"]["bands_db"][21] == -6.0 and fx["geq"]["b"]["bands_db"][21] == -9.0
    assert fx["geq"]["a"]["master_db"] == -1.5 and fx["geq"]["b"]["master_db"] == 0.0 and fx["source"] is None
    # stereo GEQ: both sides share par 1-31
    await desk.set_fx_type(6, "GEQ")
    await desk.set_geq_band(6, "B", 1, +3.0)
    await settle(conn)
    assert fakedesk.get("/fx/6/par/01") == pytest.approx(0.6, abs=1e-6)
    fx = await desk.get_fx(6)
    assert fx["geq"]["kind"] == "stereo" and fx["geq"]["b"] is None and fx["geq"]["a"]["bands_db"][0] == 3.0
    # slots 1-4 use the long enum and carry sources
    await desk.set_fx_type(1, "GEQ2")
    await settle(conn)
    assert fakedesk.get("/fx/1/type") == descriptor.enum("fx_type_14").index("GEQ2")
    fx = await desk.get_fx(1)
    assert fx["source"] == {"l": "MIX13", "r": "MIX13"} and fx["is_geq"] is True
    with pytest.raises(DeskError):
        await desk.set_fx_type(5, "HALL")  # not available in an insert-only slot
    with pytest.raises(DeskError):
        await desk.set_geq_band(5, "A", 33, 0.0)


# ---------------------------------------------------------------------------------------- events / dump


async def test_events_and_dump(desk, conn, events, policy):
    seen: list[str] = []
    events.subscribe(lambda ev: seen.append(ev.type))
    await desk.set_level("ch.1", -2.0, ramp_ms=0)
    assert "desk.snapshot" in seen and "desk.write" in seen
    ev = events.recent(types={"desk.write"})[-1]
    assert ev.data["address"] == "/ch/01/mix/fader" and ev.data["value"] == -2.0 and ev.data["tier"] == 1 and ev.data["tool"] == "set_fader"
    await desk.set_level("ch.1", -90.0, ramp_ms=0, force=True)
    ev = events.recent(types={"desk.write"})[-1]
    assert ev.data["value"] == "-oo"  # never a non-JSON float
    state = await desk.dump()
    assert state.missing == [] and state.get("/ch/01/mix/fader") == float("-inf")
    assert desk.stats["cached_sections"] >= len(state.sections) - 5


async def test_scene_pointer_minus_one_reports_no_scene_loaded(fakedesk, conn, descriptor, tmp_path):
    """A real X32 Rack (FW 4.13) freshly powered up reports /-show/prepos/current = -1.

    Reporting that verbatim reads as scene number -1 to both the operator and the model, so
    current_scene() must say plainly that nothing is loaded.
    """
    from x32mcp.desk import Desk
    from x32mcp.events import EventBus
    from x32mcp.nodes import SnapshotStore
    from x32mcp.policy import Policy

    ev = EventBus()
    d = Desk(descriptor, conn, Policy(descriptor, ev), ev, SnapshotStore(tmp_path))
    fakedesk.set("/-show/prepos/current", -1)
    conn.invalidate()
    cur = await d.current_scene()
    assert cur["index"] is None, cur
    assert cur["has_data"] is False
    assert "no scene" in (cur.get("note") or "").lower()
