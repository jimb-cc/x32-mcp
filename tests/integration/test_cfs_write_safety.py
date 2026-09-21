"""CFS² must never act on a belief the desk contradicts.

Regression tests for four reproduced failure modes:
* commit-before-write: a GEQ write that failed or was interrupted left the NotchController (and the
  report, and cfs_status) believing in a cut the console never received; the next detection then
  "deepened" a flat band straight to −6 dB;
* the RAISE loop wrote belief + step as an absolute fader value, so an operator's emergency pull-down
  mid-run was answered with a jump straight back up;
* with the RTA frames stopped the ring-out kept raising the master blind to the ceiling;
* a GEQ band the engineer deepened by hand was later written back shallower (a boost) because the
  writer validated against its arm-time snapshot of the GEQ.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from conftest import wait_until
from x32mcp import server as srv
from x32mcp.descriptor import Descriptor
from x32mcp.desk import DeskError
from x32mcp.meters import SyntheticRta, rta_band_hz

from test_server_tools import GEQ_BAND_2K5, assert_pending, fset, make_app, settle

PAR_2K5 = f"/fx/5/par/{GEQ_BAND_2K5:02d}"


@pytest_asyncio.fixture
async def rig(fakedesk, tmp_path):
    """App on the fake desk with an injected SyntheticRta, GEQ set up on bus 1, two mics routed."""
    rta = SyntheticRta(seed=2, period_s=0.05, band_hz=rta_band_hz(Descriptor.load()))
    a = await make_app(fakedesk, tmp_path, frames=rta)
    token = assert_pending(await srv.setup_ringout_eqs([1]))
    done = await srv.setup_ringout_eqs([1], confirm_token=token)
    assert done["ok"]
    for ch in (1, 2):
        fset(a, fakedesk, f"/ch/{ch:02d}/mix/01/level", -20.0)
    fset(a, fakedesk, "/bus/01/mix/fader", -20.0)
    try:
        yield a, rta
    finally:
        await a.close()
        srv.app = None


async def test_failed_notch_write_is_not_committed_and_the_retry_is_still_minus_3(rig, fakedesk, monkeypatch):
    a, rta = rig
    real = a.desk.set_geq_band
    calls = {"n": 0}

    async def flaky(fx_slot, side, band, gain_db, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise DeskError("TIMEOUT", "simulated: /fx/5 did not answer")
        return await real(fx_slot, side, band, gain_db, **kw)

    monkeypatch.setattr(a.desk, "set_geq_band", flaky)
    failed: list = []
    a.events.subscribe(lambda ev: failed.append(ev.data), types={"cfs.notch_failed"})
    fw = await srv.feedback_watch(1, notch_budget=4)
    assert fw["ok"]
    rta.inject_ring(2400.0, 20.0, start_db=-34.0)
    await wait_until(lambda: failed, timeout=4.0, what="the first (failing) notch write")
    # nothing was recorded for the failed write: no notch, budget untouched, desk flat
    st = await srv.cfs_status()
    assert st["notches"] == [] and st["budget_left"] == 4
    await settle(a)
    assert float(fakedesk.value(PAR_2K5)) == pytest.approx(0.0, abs=0.01)
    # the ring is still there; after the cooldown the detector re-emits and the SAME first step is written
    await wait_until(lambda: len(a.cfs.state.notches) >= 1, timeout=4.0, what="the retried notch")
    await settle(a)
    assert float(fakedesk.value(PAR_2K5)) == pytest.approx(-3.0, abs=0.01), "the retry must be the first -3 dB step, not a 'deepen' to -6"
    st = await srv.cfs_status()
    assert st["notches"][0]["depth_db"] == -3.0 and st["budget_left"] == 3
    await srv.feedback_watch_stop()


async def test_stop_during_an_inflight_notch_write_keeps_controller_and_desk_consistent(rig, fakedesk, monkeypatch):
    """The original interleaving: stop() lands while the GEQ write is suspended. Either the write lands
    and is committed, or it does not and is not — never 'reported but not on the desk'."""
    a, rta = rig
    real = a.desk.set_geq_band
    entered = asyncio.Event()

    async def slow(fx_slot, side, band, gain_db, **kw):
        entered.set()
        await asyncio.sleep(0.3)  # a /node round trip on a slow desk
        return await real(fx_slot, side, band, gain_db, **kw)

    monkeypatch.setattr(a.desk, "set_geq_band", slow)
    fw = await srv.feedback_watch(1, notch_budget=4)
    assert fw["ok"]
    rta.inject_ring(2400.0, 20.0, start_db=-34.0)
    await asyncio.wait_for(entered.wait(), timeout=4.0)
    stop = await srv.feedback_watch_stop()  # lands mid-write; _disarm lets the write finish
    assert stop["ok"] and stop["stopped"]
    await settle(a)
    on_desk = float(fakedesk.value(PAR_2K5))
    reported = [n["depth_db"] for n in stop["report"]["notches"]]
    assert (reported == [-3.0] and on_desk == pytest.approx(-3.0, abs=0.01)) or (reported == [] and on_desk == pytest.approx(0.0, abs=0.01)), (reported, on_desk)


async def test_operator_moving_the_master_mid_run_stops_the_ringout_hands_off(rig, fakedesk):
    a, rta = rig
    fset(a, fakedesk, "/bus/01/mix/fader", -30.0)
    token = assert_pending(await srv.ring_out(1, target_gain_db=-5.0, dwell_ms=150))
    run = asyncio.create_task(srv.ring_out(1, target_gain_db=-5.0, dwell_ms=150, confirm_token=token))
    await wait_until(lambda: (fakedesk.value("/bus/01/mix/fader") or -90) >= -27.0, timeout=5.0, what="raises under way")
    fakedesk.set_value("/bus/01/mix/fader", -60.0)  # the engineer yanks the fader down at the desk
    rep = await asyncio.wait_for(run, timeout=10.0)
    assert rep["ok"] and rep["final_stage"] == "ABORT" and "moved on the desk" in rep["abort_reason"]
    await settle(a)
    await asyncio.sleep(0.3)
    assert fakedesk.value("/bus/01/mix/fader") == pytest.approx(-60.0, abs=0.1), "the ring-out wrote the master after the operator took over"
    assert rep["end_master_db"] == pytest.approx(-60.0, abs=0.1)


async def test_ringout_holds_then_aborts_when_rta_frames_stop(rig, fakedesk):
    a, rta = rig
    fset(a, fakedesk, "/bus/01/mix/fader", -30.0)
    token = assert_pending(await srv.ring_out(1, target_gain_db=-5.0, dwell_ms=150))
    run = asyncio.create_task(srv.ring_out(1, target_gain_db=-5.0, dwell_ms=150, confirm_token=token))
    await wait_until(lambda: (fakedesk.value("/bus/01/mix/fader") or -90) >= -28.0, timeout=5.0, what="raises under way")
    await rta.stop()  # the meter stream dies (subscription lapsed / desk busy / Wi-Fi)
    stalled_at = fakedesk.value("/bus/01/mix/fader")
    rep = await asyncio.wait_for(run, timeout=15.0)
    assert rep["ok"] and rep["final_stage"] == "ABORT" and "frames stopped" in rep["abort_reason"]
    assert rep["max_master_db"] <= stalled_at + 1.0 + 0.05, "the master kept climbing with the detector blind"
    await settle(a)
    assert fakedesk.value("/bus/01/mix/fader") < stalled_at  # backed off


async def test_a_band_the_engineer_cut_deeper_is_never_written_shallower(rig, fakedesk, caplog):
    a, rta = rig
    fw = await srv.feedback_watch(1, notch_budget=4)
    assert fw["ok"] and fw["geq"]["existing_cuts"] == []
    fakedesk.set_value(PAR_2K5, -12.0)  # hand-made cut on the console, pushed to us over /xremote
    await wait_until(lambda: a.cfs._ses is not None and a.cfs._ses.writer.gains.get(GEQ_BAND_2K5) == pytest.approx(-12.0, abs=0.01),
                     timeout=3.0, what="the pushed GEQ change adopted")
    rta.inject_ring(2400.0, 20.0, start_db=-34.0)
    await asyncio.sleep(2.0)  # detections happen; every proposal from -12 would be a boost and is refused
    await settle(a)
    assert float(fakedesk.value(PAR_2K5)) == pytest.approx(-12.0, abs=0.01), "CFS² wrote over the engineer's deeper cut"
    st = await srv.cfs_status()
    assert st["budget_left"] == 4
    assert any(n["band"] == GEQ_BAND_2K5 and n["depth_db"] == pytest.approx(-12.0) for n in st["notches"])  # shown as the cut it is
    await srv.feedback_watch_stop()


def test_notch_controller_two_phase_unit():
    """propose() is pure; commit() records; a proposal that is never committed leaves no trace."""
    from x32mcp.detector import Detection, DetectorConfig, NotchController

    cfg = DetectorConfig()
    hz = [float(h) for h in Descriptor.load().geq["band_hz"]]
    nc = NotchController(cfg, hz, lambda cur, new: None, budget=2)
    det = Detection(ts=1.0, band=70, freq_hz=2400.0, level_db=-20, prominence_db=20, slope_db_per_s=0, frames=5, confidence=1)
    p1 = nc.propose(det, 1, "s")
    assert p1 is not None and p1.band == 22 and p1.current_db == 0.0 and p1.new_db == -3.0 and p1.opens
    assert nc.notches == [] and nc.budget_left == 2 and nc.gains == {}      # nothing recorded
    p1b = nc.propose(det, 1, "s")
    assert p1b == p1                                                          # pure: same answer
    n = nc.commit(p1)
    assert n.depth_db == -3.0 and nc.budget_left == 1 and nc.gains == {22: -3.0}
    p2 = nc.propose(det, 1, "s")
    assert p2.current_db == -3.0 and p2.new_db == -6.0 and not p2.opens
    nc.observe(22, -12.0)                                                     # the engineer deepened it by hand
    assert nc.gains[22] == -12.0 and nc.propose(det, 1, "s") is None          # already past -9: nothing to do, never shallower
    nc.observe(22, -4.0)                                                      # ... or made it shallower by hand
    p3 = nc.propose(det, 1, "s")
    assert p3.current_db == -4.0 and p3.new_db == -7.0                        # deepen from the DESK's value
    nc.observe(22, 0.0)                                                       # released on the desk
    assert 22 not in {x.band for x in nc.notches} and nc.gains[22] == 0.0
