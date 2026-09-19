"""provision.py + cfs.py against the FakeDesk (DESIGN.md §14, §15, §20): GEQ setup/validation,
mic discovery, preflight, feedback_watch and ring_out closed loop (the fake attenuates its
synthetic ring by the GEQ inserted on the RTA-source bus), abort/restore on connection loss,
an untamable ring (open-loop injected frames) and a system ring-out."""

from __future__ import annotations

import asyncio
import dataclasses
import time
from pathlib import Path

import pytest
import pytest_asyncio

from conftest import CONN_OPTS, wait_until
from x32mcp.cfs import CfsError, CfsManager, CfsMode, ReportStore
from x32mcp.connection import ConnectionState, X32Connection
from x32mcp.desk import Desk
from x32mcp.detector import DetectorConfig
from x32mcp.events import EventBus
from x32mcp.meters import SyntheticRta, rta_band_hz
from x32mcp.nodes import SnapshotStore
from x32mcp.policy import Policy
from x32mcp.provision import apply_setup, discover_mics, plan_setup, preflight, validate_ringout_eqs

GEQ_BAND_2K5 = 22  # 1-based GEQ band at 2.5 kHz = /fx/N/par/22 (fx_routing_scenes.md §2.3)


# ---------------------------------------------------------------------------------------- fixtures


@pytest.fixture
def events() -> EventBus:
    return EventBus()


@pytest_asyncio.fixture
async def conn(descriptor, fakedesk, events) -> X32Connection:
    """A connection on the SAME bus as policy/desk/cfs (so 'connection.state' reaches cfs)."""
    c = X32Connection(descriptor, events, **CONN_OPTS)
    await c.connect(fakedesk.host, fakedesk.port)
    try:
        yield c
    finally:
        await c.close()


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


@pytest.fixture
def reports(tmp_path) -> ReportStore:
    return ReportStore(tmp_path / "ringout_reports")


@pytest_asyncio.fixture
async def cfs(desk, policy, events, reports, tmp_path) -> CfsManager:
    m = CfsManager(desk, policy, events, reports, snapshots=SnapshotStore(tmp_path / "snapshots"))
    try:
        yield m
    finally:
        await m.close()


async def settle(conn) -> None:
    """SETs are fire-and-forget: one GET round trip guarantees the fake desk processed them."""
    await conn.get("/-stat/selidx")


async def provision(desk, buses):
    plan = await plan_setup(desk, buses)
    return await apply_setup(desk, plan)


def fset(desk, fakedesk, address: str, value) -> None:
    """A front-panel change on the fake; the desk cache is dropped so the next read sees it
    (the /xremote push that would do that arrives asynchronously)."""
    fakedesk.set_value(address, value)
    desk.invalidate()


def route_mics(desk, fakedesk, bus: int, channels, level_db: float = -20.0) -> None:
    for ch in channels:
        fset(desk, fakedesk, f"/ch/{ch:02d}/mix/{bus:02d}/level", level_db)


class _Row:
    def __init__(self, channel, mic=None, owner=None, monitor_bus=None):
        self.channel, self.mic, self.owner, self.monitor_bus = channel, mic, owner, monitor_bus


class _Patch:
    def __init__(self, *rows):
        self.rows = list(rows)


# ---------------------------------------------------------------------------------------- provisioning


async def test_validate_fresh_desk_not_ok(desk):
    status = await validate_ringout_eqs(desk, [1, 2])
    assert set(status) == {1, 2}
    s = status[1]
    assert s.ok is False and s.bus == 1 and s.target == "bus.1"
    assert any("no FX insert" in r for r in s.reasons)
    assert s.insert is not None and s.insert.sel == "OFF" and s.insert.fx_slot is None and s.insert.fx_type is None
    assert s.bands_db is None and s.flat is False and s.notches == [] and s.matched_session is None
    assert s.to_dict()["reasons"] == s.reasons


async def test_plan_setup_allocates_slot_5_l_r(desk):
    plan = await plan_setup(desk, [1, 2])
    assert plan.buses == [1, 2] and plan.reuse == {} and plan.blockers == [] and plan.needs_tier2 is True and not plan.empty
    assert plan.type_loads == [{"slot": 5, "fx_type": "GEQ2", "current_type": "DES2"}]
    assert plan.inserts == [
        {"target": "bus.1", "bus": 1, "sel": "FX5L", "on": True, "pos": "POST"},
        {"target": "bus.2", "bus": 2, "sel": "FX5R", "on": True, "pos": "POST"},
    ]
    assert len(plan.allocations) == 1 and plan.allocations[0].slot == 5 and plan.allocations[0].sides == {"A": 1, "B": 2}
    assert plan.allocations[0].load_type is True
    assert plan.to_dict()["allocations"][0]["fx_type"] == "GEQ2"
    # three buses: a second slot for the odd one; 'main' is accepted as a bus spelling
    plan3 = await plan_setup(desk, [1, 2, "main"])
    assert [tl["slot"] for tl in plan3.type_loads] == [5, 6]
    assert plan3.inserts[2] == {"target": "main.st", "bus": "main", "sel": "FX6L", "on": True, "pos": "POST"}


async def test_apply_setup_validates_and_is_idempotent(desk, conn, fakedesk, events):
    writes: list[dict] = []
    events.subscribe(lambda ev: writes.append(ev.data), types={"desk.write"})
    plan = await plan_setup(desk, [1, 2])
    res = await apply_setup(desk, plan)
    assert res["changed"] == 3 and res["ok"] is True and res["geq"]["1"]["ok"] and res["geq"]["2"]["ok"]
    await settle(conn)
    assert fakedesk.get("/fx/5/type") == 0  # fx_type_58: GEQ2 = 0
    assert fakedesk.value("/bus/01/insert/sel") == "FX5L" and fakedesk.value("/bus/02/insert/sel") == "FX5R"
    assert fakedesk.get("/bus/01/insert/on") == 1 and fakedesk.value("/bus/02/insert/pos") == "POST"
    status = await validate_ringout_eqs(desk, [1, 2])
    assert status[1].ok and status[2].ok and status[1].flat and status[2].flat
    assert status[1].insert.fx_slot == 5 and status[1].insert.side == "A" and status[1].insert.fx_type == "GEQ2"
    assert status[2].insert.side == "B" and status[2].bands_db == [0.0] * 31
    n_before = len(writes)
    # second apply of the same plan: nothing to write
    res2 = await apply_setup(desk, plan)
    assert res2["changed"] == 0 and res2["writes"] == [] and len(res2["skipped"]) == 3 and res2["ok"] is True
    assert len(writes) == n_before
    # a fresh plan reuses the existing inserts
    plan2 = await plan_setup(desk, [1, 2])
    assert plan2.empty and plan2.needs_tier2 is False and set(plan2.reuse) == {1, 2}
    # bus 3 pairs with a new slot (5 is taken by buses 1/2, both sides)
    plan3 = await plan_setup(desk, [1, 3])
    assert plan3.type_loads == [{"slot": 6, "fx_type": "GEQ2", "current_type": "P1A"}]
    assert plan3.inserts == [{"target": "bus.3", "bus": 3, "sel": "FX6L", "on": True, "pos": "POST"}]
    # a bus already on one side of a dual GEQ lends its free side to a partner
    await desk.set_insert("bus.2", sel="OFF", on=False)
    plan4 = await plan_setup(desk, [1, 4])
    assert plan4.type_loads == [] and plan4.inserts == [{"target": "bus.4", "bus": 4, "sel": "FX5R", "on": True, "pos": "POST"}]


async def test_validate_reports_bypassed_insert_and_preflight_refuses(desk, conn, fakedesk, cfs):
    await provision(desk, [1])
    route_mics(desk, fakedesk, 1, [1, 2])
    fset(desk, fakedesk, "/bus/01/mix/fader", -20.0)
    pf = await preflight(desk, 1)
    assert pf.ok and pf.blockers == [] and [m.ch for m in pf.included_mics] == [1, 2] and pf.master_db == -20.0
    fset(desk, fakedesk, "/bus/01/insert/on", False)
    status = await validate_ringout_eqs(desk, [1])
    assert status[1].ok is False and any("insert bypassed" in r for r in status[1].reasons)
    pf = await preflight(desk, 1)
    assert pf.ok is False and any("insert bypassed" in b for b in pf.blockers)
    with pytest.raises(CfsError) as ei:
        await cfs.feedback_watch(1)
    assert ei.value.code == "PREFLIGHT_FAILED" and "insert bypassed" in ei.value.message
    assert ei.value.to_dict()["preflight"]["ok"] is False and cfs.state.mode is CfsMode.IDLE
    with pytest.raises(CfsError) as ei:
        await cfs.ring_out(1, dwell_ms=10)
    assert ei.value.code == "PREFLIGHT_FAILED"
    # muted bus and a -oo master are blockers too; a hot master is a warning
    fset(desk, fakedesk, "/bus/01/insert/on", True)
    fset(desk, fakedesk, "/bus/01/mix/on", False)
    pf = await preflight(desk, 1)
    assert not pf.ok and any("muted" in b for b in pf.blockers)
    fset(desk, fakedesk, "/bus/01/mix/on", True)
    fset(desk, fakedesk, "/bus/01/mix/fader", float("-inf"))
    pf = await preflight(desk, 1)
    assert not pf.ok and any("-oo" in b for b in pf.blockers) and pf.master == "-oo"
    fset(desk, fakedesk, "/bus/01/mix/fader", -3.0)
    pf = await preflight(desk, 1)
    assert pf.ok and any("above the -10 dB warning level" in w for w in pf.warnings)
    # shared stereo GEQ is refused (a cut would hit both buses)
    await desk.set_fx_type(5, "GEQ")
    await desk.set_insert("bus.2", sel="FX5R", on=True, pos="POST")
    status = await validate_ringout_eqs(desk, [1])
    assert not status[1].ok and any("shared with Bus 2" in r for r in status[1].reasons)


async def test_discover_mics(desk, conn, fakedesk):
    route_mics(desk, fakedesk, 1, [1, 2, 3, 4, 9])
    fset(desk, fakedesk, "/ch/05/mix/01/level", -50.0)  # below the -40 dB floor
    fset(desk, fakedesk, "/ch/03/mix/on", False)  # muted
    fset(desk, fakedesk, "/ch/04/config/source", "USBL")  # USB: no preamp
    fset(desk, fakedesk, "/config/routing/IN/9-16", "CARD9-16")  # ch 9 (IN09) now comes from the card
    fset(desk, fakedesk, "/ch/01/grp/mute", 32)  # mute group 6 = bit 5 (fx_routing_scenes.md §5.1)
    patch = _Patch(_Row(2, mic=True, owner="Tony", monitor_bus=1), _Row(3, mic=True, monitor_bus=1),
                   _Row(5, mic=True, monitor_bus=1), _Row(1, mic=True, monitor_bus=2))
    mics = await discover_mics(desk, 1, patch=patch)
    by_ch = {m.ch: m for m in mics}
    assert [m.ch for m in mics] == [1, 2, 3, 4, 5, 9]
    assert by_ch[1].include and by_ch[1].in_mute_group is True and by_ch[1].send_db == -20.0 and by_ch[1].physical_input
    assert any("bus 2" in n for n in by_ch[1].notes)  # patch puts it on another bus
    assert by_ch[2].include and by_ch[2].owner == "Tony" and by_ch[2].patch_mic is True and by_ch[2].in_mute_group is False
    assert any("not in mute group 6" in n for n in by_ch[2].notes)
    assert not by_ch[3].include and by_ch[3].muted and any("muted" in n for n in by_ch[3].notes)
    assert not by_ch[4].include and by_ch[4].source == "USBL" and not by_ch[4].physical_input
    assert not by_ch[5].include and any("below the -40 dB floor" in n for n in by_ch[5].notes)
    assert not by_ch[9].include and by_ch[9].source == "IN09" and not by_ch[9].physical_input
    assert by_ch[2].to_dict()["send"] == "-20.0" and by_ch[5].to_dict()["target"] == "ch.5"
    # without a patch, only desk evidence counts
    mics = await discover_mics(desk, 1)
    assert [m.ch for m in mics if m.include] == [1, 2] and [m.ch for m in mics] == [1, 2, 3, 4, 9]
    assert (await discover_mics(desk, 1, floor_db=-60.0))[4].ch == 5  # floor override includes ch 5's -50 dB send


# ---------------------------------------------------------------------------------------- feedback_watch


async def test_feedback_watch_closed_loop(desk, conn, fakedesk, events, cfs, reports):
    await provision(desk, [1])
    route_mics(desk, fakedesk, 1, [1, 2])
    fset(desk, fakedesk, "/bus/01/mix/fader", -20.0)
    notches: list[dict] = []
    geq_writes: list[dict] = []
    candidates: list[dict] = []
    events.subscribe(lambda ev: notches.append(ev.data), types={"cfs.notch"})
    events.subscribe(lambda ev: candidates.append(ev.data), types={"cfs.candidate"})
    events.subscribe(lambda ev: geq_writes.append(ev.data) if ev.data.get("tool") == "set_geq_band" else None, types={"desk.write"})
    assert (await cfs.stop())["stopped"] is False
    res = await cfs.feedback_watch(1)
    assert res["preflight"]["ok"] and res["rta"]["verified"] and res["rta"]["stat_actual"] == 146  # Bus 1 post-EQ
    assert res["geq"] == {"fx_slot": 5, "side": "A", "sel": "FX5L", "existing_cuts": []}
    st = cfs.state
    assert st.mode is CfsMode.WATCH and st.bus == 1 and st.bus_name == "Bus01" and st.budget_left == 6 and st.rta_source == "bus.1"
    assert st.master_db == -20.0 and st.session_id == res["session_id"] and st.session_id.endswith("-watch-bus01")
    with pytest.raises(CfsError) as ei:
        await cfs.feedback_watch(2)
    assert ei.value.code == "BUSY"
    await wait_until(lambda: cfs.frames.frames_received >= 3, what="RTA frames from the fake desk")
    t0 = time.perf_counter()
    fakedesk.rta.inject_ring(2400.0, 20.0, start_db=-34.0)
    await wait_until(lambda: bool(notches), timeout=1.0, what="cfs.notch event")
    assert time.perf_counter() - t0 <= 1.0
    n = notches[0]
    assert n["band"] == GEQ_BAND_2K5 and n["freq_hz"] == 2500 and n["depth_db"] == -3.0 and n["bus"] == 1
    assert n["fx_slot"] == 5 and n["side"] == "A" and n["session_id"] == res["session_id"] and n["rta_band"] == 69
    assert 0.7 <= n["confidence"] <= 1.0 and n["detections"] == 1
    await settle(conn)
    assert fakedesk.get("/fx/5/par/22") == pytest.approx(0.4, abs=1e-6)  # -3 dB: (dB + 15) / 30
    assert candidates and candidates[-1]["band"] in (68, 69, 70)
    l1 = fakedesk.rta.ring_level(2400.0)
    await asyncio.sleep(0.4)
    l2 = fakedesk.rta.ring_level(2400.0)
    assert l2 < l1 - 3.0  # closed loop: the cut exceeds the per-frame growth (meters.py cut model)
    assert geq_writes and all(w["value"]["gain_db"] <= 0.0 for w in geq_writes)
    st = cfs.state
    assert st.budget_left == 5 and st.notches[0]["band"] == GEQ_BAND_2K5 and st.notches[0]["depth_db"] == -3.0
    res = await cfs.stop()
    assert res["stopped"] and res["mode"] == "watch"
    path = Path(res["path"])
    assert path.exists() and path.with_suffix(".md").exists() and path.name == f"{res['session_id']}.json"
    rep = res["report"]
    assert rep["mode"] == "watch" and rep["bus"] == 1 and rep["final_stage"] == "DONE" and rep["aborted"] is False
    assert rep["notches"][0]["band"] == GEQ_BAND_2K5 and rep["notches"][0]["depth_db"] == -3.0 and rep["notches"][0]["confidence"] >= 0.7
    assert rep["start_master_db"] == -20.0 and rep["end_master_db"] == -20.0 and rep["detections"] >= 1
    assert cfs.state.mode is CfsMode.IDLE and not cfs.frames.running
    assert reports.latest_for_bus(1)["session_id"] == rep["session_id"] and reports.list()[0]["notches"] == 1
    assert reports.load(rep["session_id"][:8] + rep["session_id"][8:])["bus"] == 1
    assert "2500 Hz" in path.with_suffix(".md").read_text(encoding="utf-8")
    status = await validate_ringout_eqs(desk, [1], reports)
    assert status[1].ok and not status[1].flat and status[1].matched_session == rep["session_id"]
    assert status[1].notches == [{"band": GEQ_BAND_2K5, "freq_hz": 2500.0, "depth_db": -3.0}]
    pf = await preflight(desk, 1, reports=reports)
    assert pf.ok and any("already carries cuts" in w and rep["session_id"] in w for w in pf.warnings)


async def test_feedback_watch_stops_on_connection_loss(desk, conn, fakedesk, events, cfs, reports):
    await provision(desk, [1])
    route_mics(desk, fakedesk, 1, [1])
    fset(desk, fakedesk, "/bus/01/mix/fader", -20.0)
    aborts: list[dict] = []
    events.subscribe(lambda ev: aborts.append(ev.data), types={"cfs.abort"})
    await cfs.feedback_watch(1)
    fakedesk.silent = True
    try:
        await wait_until(lambda: cfs.state.mode is CfsMode.IDLE, timeout=8.0, what="watch stopped after the desk went silent")
    finally:
        fakedesk.silent = False
    assert aborts and aborts[0]["reason"].startswith("connection lost")
    rep = reports.latest_for_bus(1)
    assert rep["aborted"] and rep["final_stage"] == "ABORT" and rep["abort_reason"].startswith("connection lost")
    await wait_until(lambda: conn.state is ConnectionState.CONNECTED, timeout=5.0, what="reconnect")


# ---------------------------------------------------------------------------------------- ring_out


async def test_ring_out_closed_loop(desk, conn, fakedesk, events, cfs, tmp_path):
    await provision(desk, [1])
    route_mics(desk, fakedesk, 1, [1, 2])
    fset(desk, fakedesk, "/bus/01/mix/fader", -20.0)
    fader_writes: list[float] = []
    stages: list[dict] = []
    notches: list[dict] = []
    injected = False

    def on_stage(ev):
        nonlocal injected
        stages.append(ev.data)
        if ev.data["stage"] == "RAISE" and not injected and ev.data["master_db"] >= -18.0:
            injected = True
            fakedesk.rta.inject_ring(2400.0, 20.0, start_db=-34.0)

    events.subscribe(on_stage, types={"cfs.stage"})
    events.subscribe(lambda ev: notches.append(ev.data), types={"cfs.notch"})
    events.subscribe(lambda ev: fader_writes.append(ev.data["value"]) if ev.data["address"] == "/bus/01/mix/fader" else None, types={"desk.write"})
    rep = await asyncio.wait_for(cfs.ring_out(1, target_gain_db=-6.0, step_db=1.0, dwell_ms=60), timeout=30)
    assert injected
    assert rep["final_stage"] == "DONE" and rep["aborted"] is False and rep["abort_reason"] is None and rep["mode"] == "ringout"
    assert rep["start_master_db"] == -20.0 and rep["max_master_db"] == -6.0 and rep["end_master_db"] == -9.0  # target - 3 dB margin
    assert rep["target_db"] == -6.0 and rep["step_db"] == 1.0 and rep["dwell_ms"] == 60 and rep["snapshot"]
    assert -18.0 <= rep["gain_before_feedback_db"] <= -7.0 and rep["budget_left"] == 5
    assert len(rep["notches"]) == 1 and rep["notches"][0]["band"] == GEQ_BAND_2K5 and rep["notches"][0]["depth_db"] == -3.0
    assert notches[0]["band"] == GEQ_BAND_2K5 and rep["notches"][0]["confidence"] >= 0.7
    raises, backoff = fader_writes[:-1], fader_writes[-1]
    assert raises[0] == -19.0 and raises[-1] == -6.0 and raises == sorted(raises)
    assert all(b - a == pytest.approx(1.0) for a, b in zip(raises, raises[1:]))
    assert backoff == -9.0
    names = [s["stage"] for s in stages]
    assert names[:4] == ["PREFLIGHT", "SNAPSHOT", "ARM", "RAISE"] and names[-2:] == ["BACKOFF", "DONE"]
    i = names.index("HOLD")
    assert names[i:i + 3] == ["HOLD", "NOTCH", "VERIFY"]
    verify = next(s for s in stages if s["stage"] == "VERIFY")
    assert verify["ok"] is True and verify["drop_db"] >= 6.0 and verify["depth_db"] == -3.0
    assert names.count("RAISE") == 14 and names.count("NOTCH") == 1
    await settle(conn)
    assert fakedesk.value("/bus/01/mix/fader") == pytest.approx(-9.0, abs=0.1)
    assert fakedesk.get("/fx/5/par/22") == pytest.approx(0.4, abs=1e-6)
    path = Path(rep["path"])
    assert path.exists() and path.with_suffix(".md").exists()
    md = path.with_suffix(".md").read_text(encoding="utf-8")
    assert "-20.0 dB → -9.0 dB" in md and "Result: DONE" in md
    assert cfs.state.mode is CfsMode.IDLE and cfs.state.stage == "DONE" and cfs.state.bus == 1
    assert (tmp_path / "snapshots").glob("*ringout-pre-bus-1*") or any("ringout" in p.name for p in (tmp_path / "snapshots").glob("*.json"))


async def test_ring_out_restores_master_on_connection_loss(desk, conn, fakedesk, events, cfs):
    await provision(desk, [1])
    route_mics(desk, fakedesk, 1, [1])
    fset(desk, fakedesk, "/bus/01/mix/fader", -20.0)
    states: list[str] = []
    restores: list[dict] = []
    events.subscribe(lambda ev: states.append(ev.data["state"]), types={"connection.state"})
    events.subscribe(lambda ev: restores.append(ev.data), types={"cfs.restore"})
    silenced = False

    def on_stage(ev):
        nonlocal silenced
        if ev.data["stage"] == "RAISE" and not silenced and ev.data["master_db"] >= -17.0:
            silenced = True
            fakedesk.silent = True  # the PSU cut out mid ring-out
            asyncio.get_running_loop().call_later(2.2, setattr, fakedesk, "silent", False)

    events.subscribe(on_stage, types={"cfs.stage"})
    rep = await asyncio.wait_for(cfs.ring_out(1, target_gain_db=0.0, dwell_ms=100), timeout=40)
    assert silenced
    assert rep["final_stage"] == "ABORT" and rep["aborted"] and rep["connection_lost"] and rep["restored"] is True
    assert rep["end_master_db"] == -20.0 and rep["max_master_db"] >= -17.0
    assert "desk" in rep["abort_reason"] or "connection" in rep["abort_reason"]
    assert restores and restores[0]["restored"] and restores[0]["master_db"] == -20.0 and restores[0]["attempts"] >= 2
    assert "degraded" in states  # the watchdog noticed as well
    await wait_until(lambda: conn.state is ConnectionState.CONNECTED, timeout=5.0, what="reconnect")
    await settle(conn)
    assert fakedesk.value("/bus/01/mix/fader") == pytest.approx(-20.0, abs=0.1)
    assert cfs.state.mode is CfsMode.IDLE and cfs.state.stage == "ABORT"
    assert [s["stage"] for s in rep["stages"]][-1] == "ABORT" and "BACKOFF" not in [s["stage"] for s in rep["stages"]]
    assert Path(rep["path"]).exists()


async def test_ring_out_untamable_ring_backs_off(descriptor, desk, policy, events, reports, conn, fakedesk):
    await provision(desk, [1])
    route_mics(desk, fakedesk, 1, [1])
    fset(desk, fakedesk, "/bus/01/mix/fader", -20.0)
    cfg = dataclasses.replace(DetectorConfig.from_descriptor(descriptor), decay_verify_s=0.3, cooldown_s=0.3)
    rta = SyntheticRta(seed=3, period_s=0.05, band_hz=rta_band_hz(descriptor))
    rta.inject_ring(2400.0, 15.0, start_db=-30.0)  # open loop: our GEQ cuts never reach this analyser
    geq_writes: list[dict] = []
    events.subscribe(lambda ev: geq_writes.append(ev.data) if ev.data.get("tool") == "set_geq_band" else None, types={"desk.write"})
    cfs = CfsManager(desk, policy, events, reports, frames=rta, detector_cfg=cfg)
    try:
        assert cfs.frames is rta and cfs.detector_cfg.decay_verify_s == 0.3
        rep = await asyncio.wait_for(cfs.ring_out(1, target_gain_db=0.0, dwell_ms=400), timeout=30)
    finally:
        await cfs.close()
    assert rep["final_stage"] == "ABORT" and rep["aborted"] and "not tamed" in rep["abort_reason"] and rep["connection_lost"] is False
    assert len(rep["notches"]) == 1 and rep["notches"][0]["band"] == GEQ_BAND_2K5 and rep["notches"][0]["depth_db"] == -9.0
    assert rep["notches"][0]["detections"] == 3
    assert rep["end_master_db"] == pytest.approx(rep["max_master_db"] - 6.0)  # abort_backoff_db
    assert [w["value"]["gain_db"] for w in geq_writes] == [-3.0, -6.0, -9.0]
    names = [s["stage"] for s in rep["stages"]]
    assert names.count("NOTCH") == 3 and names.count("VERIFY") == 3 and names[-2:] == ["BACKOFF", "ABORT"]
    assert all(s["ok"] is False for s in rep["stages"] if s["stage"] == "VERIFY")
    await settle(conn)
    assert fakedesk.get("/fx/5/par/22") == pytest.approx(0.2, abs=1e-6)  # -9 dB
    assert fakedesk.value("/bus/01/mix/fader") == pytest.approx(rep["end_master_db"], abs=0.1)
    assert not rta.running


async def test_ring_out_system_consolidated_report(desk, conn, fakedesk, events, cfs, reports):
    await provision(desk, [1, 2])
    route_mics(desk, fakedesk, 1, [1])
    route_mics(desk, fakedesk, 2, [2])
    fset(desk, fakedesk, "/bus/01/mix/fader", -20.0)
    fset(desk, fakedesk, "/bus/02/mix/fader", -20.0)
    modes: list[str] = []
    events.subscribe(lambda ev: modes.append(ev.data.get("mode")), types={"cfs.state"})
    plan = {"stages": [{"bus": 1, "target_gain_db": -17.0, "dwell_ms": 30, "mics": [1, 2]},
                       {"bus": 2, "target_gain_db": -17.0, "dwell_ms": 30}]}
    rep = await asyncio.wait_for(cfs.ring_out_system(plan), timeout=60)
    assert rep["mode"] == "system" and rep["final_stage"] == "DONE" and rep["aborted"] is False and rep["session_id"].endswith("-system")
    assert [s["bus"] for s in rep["stages"]] == [1, 2] and all(s["final_stage"] == "DONE" for s in rep["stages"])
    assert all(s["end_master_db"] == -20.0 and s["max_master_db"] == -17.0 for s in rep["stages"])
    assert rep["stages"][0]["expected_mics"] == [1, 2] and any("mics [2]" in w for w in rep["stages"][0]["preflight"]["warnings"])
    assert rep["stages"][0]["system_id"] == rep["session_id"] and rep["notches"] == []
    assert "system ring-out" in rep["summary"] and rep["plan"] == plan
    assert modes[0] == "system" and modes[-1] == "idle"
    path = Path(rep["path"])
    assert path.exists() and "system ring-out" in path.with_suffix(".md").read_text(encoding="utf-8")
    ids = {m["session_id"] for m in reports.list()}
    assert rep["session_id"] in ids and all(s["session_id"] in ids for s in rep["stages"])
    assert cfs.state.mode is CfsMode.IDLE
    await settle(conn)
    assert fakedesk.value("/bus/02/mix/fader") == pytest.approx(-20.0, abs=0.1)
    # a stage that fails preflight is skipped, the rest still runs
    rep2 = await asyncio.wait_for(cfs.ring_out_system({"stages": [{"bus": 3, "dwell_ms": 10}, {"bus": 1, "target_gain_db": -19.0, "dwell_ms": 10}]}), timeout=60)
    assert rep2["stages"][0]["skipped"] and rep2["stages"][0]["error"]["code"] == "PREFLIGHT_FAILED"
    assert rep2["stages"][1]["final_stage"] == "DONE" and rep2["stages"][1]["end_master_db"] == -22.0
    with pytest.raises(CfsError) as ei:
        await cfs.ring_out_system({"stages": []})
    assert ei.value.code == "BAD_ARGUMENT"
