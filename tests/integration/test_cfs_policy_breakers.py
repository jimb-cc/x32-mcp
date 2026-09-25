"""Breaker tests for the CFS² policy layer (written by the independent verification of review/detector-cfs, kept as the
regression suite for its findings): tier B, the alerts and the back-off probe against the FakeDesk + SyntheticRta rig, closed
loop over the in-memory UDP shim.

Each test asserts an invariant that must hold on a desk: no double / shallower GEQ writes, nothing written after an abort
begins, the scribble strip ALWAYS restored, colour writes <= 1/s, the RAISE loop terminates, tier-A cuts are not starved by
policy work, the policy never argues with the engineer's hands, a masked line is not mistaken for one that ended.
"""

from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from typing import Any

import pytest

from conftest import CONN_OPTS, wait_until
from test_cfs_policy_desk import COLOR, GEQ_BAND_1K, PAR_1K, Rig, RoomSourceRta, _armed, _strum, make_rig  # noqa: F401 (fixture)
from x32mcp.cfs import CfsManager, CfsMode, ReportStore
from x32mcp.cfs_policy import CfsPolicyConfig
from x32mcp.connection import X32Connection
from x32mcp.desk import Desk, DeskError
from x32mcp.events import EventBus
from x32mcp.fakedesk import FakeDesk
from x32mcp.meters import SyntheticRta, rta_band_hz
from x32mcp.nodes import SnapshotStore
from x32mcp.policy import Policy
from x32mcp.provision import apply_setup, plan_setup


def _band_writes(rig: Rig, band: int) -> list[float]:
    """Every GEQ gain the session's writer sent for ``band`` (in order)."""
    ses = rig.cfs._ses or rig.cfs._last
    return [g for (_bus, b, g) in ses.writer.writes if b == band]


# ------------------------------------------------------------------------------------------ (a) regrowth after a tier-B cut

async def test_a_tier_b_cut_line_that_regrows_gets_strictly_deeper_single_writes_and_one_budget_band(make_rig, caplog):
    """A loud-ish MODERATE line is cut -3 by tier B and holds ('held'); then it starts to regrow like a howl whose excess climbs
    (the note is swapped for a fast ring at the same displayed level). The detector's own RISE re-emission (tier A) and the
    policy's held-deepen (tier B) now both want the band: whatever the interleaving, every write must be one 3 dB step deeper
    than the last, never a repeat, never past -9, the budget charged for ONE band, and the consumer must not log a failure."""
    caplog.set_level(logging.ERROR, logger="x32mcp.cfs")
    rig = await make_rig(policy={"tier_b": {"held_deepen_s": 1.0}})
    await _armed(rig, notch_budget=4)
    await asyncio.sleep(0.6)
    rig.rta.inject_note(1000.0, -18.0, rise_frames=1)
    await wait_until(lambda: rig.notches(), timeout=3.0, what="the tier-B cut")
    ses = rig.cfs._ses
    assert rig.notches()[0]["tier"] == "B"
    await wait_until(lambda: any(v["verdict"] == "held" for v in ses.det.cut_log), timeout=3.0, what="'held' verdict")
    # regrowth: internal -18 under the 3 dB cut reads -21 (where the held note sat); 80 dB/s loop growth nets +1 dB/frame under the cut
    rig.rta.stop_note(1000.0)
    rig.rta.inject_ring(1000.0, 80.0, start_db=-18.0, cap_db=-2.0)
    await wait_until(lambda: len(rig.notches()) >= 2, timeout=4.0, what="a deepening cut on the regrowing line")
    await asyncio.sleep(4.0)   # let every verdict / held timer play out
    writes = _band_writes(rig, GEQ_BAND_1K)
    assert writes == sorted(writes, reverse=True) and len(set(writes)) == len(writes), f"non-monotonic or repeated GEQ writes: {writes}"
    assert all(abs(a - b - 3.0) < 1e-6 for a, b in zip(writes, writes[1:])), writes
    assert min(writes) >= -9.0 - 1e-6
    assert [n["depth_db"] for n in rig.notches()] == writes, "every cfs.notch event corresponds to exactly one write"
    assert ses.nc.budget_left == 3 and ses.nc.touched_bands == {GEQ_BAND_1K}
    await rig.settle()
    assert float(rig.fake.value(PAR_1K)) == pytest.approx(writes[-1], abs=0.01)
    tiers = [n["tier"] for n in rig.notches()]
    assert tiers[0] == "B" and set(tiers) <= {"A", "B"}
    assert not [r for r in caplog.records if "detector task failed" in r.getMessage()], "policy code raised inside the frame consumer"
    rep = (await rig.cfs.stop())["report"]
    await rig.settle()
    assert rig.fake.value(COLOR) == "GN"
    assert [e["depth_db"] for e in rep["notch_log"]] == writes
    print("regrowth sequence (tier, policy, depth):", [(n["tier"], n.get("policy"), n["depth_db"]) for n in rig.notches()])


# ------------------------------------------------------------------------------------------ (b) aborts while a held-deepen is pending

async def test_b_panic_while_a_tier_b_held_deepen_is_pending_writes_nothing_more_and_restores_the_strip(make_rig):
    rig = await make_rig(policy={"tier_b": {"held_deepen_s": 1.0}})
    await _armed(rig)
    await asyncio.sleep(0.6)
    rig.rta.inject_note(1000.0, -18.0, rise_frames=1)
    await wait_until(lambda: rig.notches(), timeout=3.0, what="the tier-B cut")
    ses = rig.cfs._ses
    await wait_until(lambda: any(tb.state == "held" for tb in ses.policy.tier_b.values()), timeout=3.0, what="engagement in 'held'")
    await asyncio.sleep(0.7)                                 # the -6 is due in 0.3 s
    await rig.settle()
    assert rig.fake.value(COLOR) == "RDi"
    writes_before = list(ses.writer.writes)
    await rig.desk.panic()
    await wait_until(lambda: rig.cfs.state.mode is CfsMode.IDLE, timeout=5.0, what="watch stopped by the panic")
    await asyncio.sleep(1.5)                                 # well past the held_deepen deadline
    await rig.settle()
    assert list(ses.writer.writes) == writes_before, f"GEQ write after the panic: {ses.writer.writes[len(writes_before):]}"
    assert float(rig.fake.value(PAR_1K)) == pytest.approx(-3.0, abs=0.01)
    assert rig.fake.value(COLOR) == "GN", "panic path must restore the strip colour"
    rep = rig.cfs._last.report
    assert rep["aborted"] and rep["policy"]["strip_color"]["restored"] is True
    ends = [e for e in rep["policy"]["tier_b"] if e["action"] == "end"]
    # observed: the watch is finished before another frame runs _policy_tier_b, so the open 'held' engagement gets no closing
    # 'end' row in report['policy']['tier_b'] (report gap, not a safety issue)
    print("tier_b log tail:", rep["policy"]["tier_b"][-2:], "| end rows:", ends)


async def test_b_show_mode_on_while_a_ring_out_tier_b_engagement_is_held_aborts_without_a_further_geq_write(make_rig):
    rig = await make_rig(policy={"tier_b": {"held_deepen_s": 1.5}})
    rig.fake.set_value("/bus/01/mix/fader", -30.0)
    rig.desk.invalidate()
    run = asyncio.create_task(rig.cfs.ring_out(1, target_gain_db=-18.0, step_db=1.0, dwell_ms=400))
    await wait_until(lambda: rig.cfs.mode is CfsMode.RINGOUT and rig.cfs.frames.frames_received >= 5, timeout=5.0, what="ring-out armed")
    ses = rig.cfs._ses
    rig.rta.inject_note(1000.0, -18.0, rise_frames=1)
    await wait_until(lambda: rig.notches(), timeout=6.0, what="the tier-B cut in ring_out")
    assert rig.notches()[0]["tier"] == "B"
    await wait_until(lambda: any(tb.state == "held" for tb in ses.policy.tier_b.values()), timeout=4.0, what="engagement in 'held'")
    master_at_hold = ses.master_db
    await asyncio.sleep(0.8)
    assert ses.master_db == master_at_hold, "ringout_hold_raise: the master must not move while a tier-B engagement is unresolved"
    writes_before = list(ses.writer.writes)
    rig.cfs._policy.show_mode = True                         # the engineer flips show mode on mid-run
    try:
        rep = await asyncio.wait_for(run, timeout=15.0)
    finally:
        rig.cfs._policy.show_mode = False
    await asyncio.sleep(1.0)
    await rig.settle()
    assert rep["final_stage"] == "ABORT" and "show mode" in rep["abort_reason"]
    assert list(ses.writer.writes) == writes_before, f"GEQ write after the abort began: {ses.writer.writes[len(writes_before):]}"
    assert rig.fake.value(COLOR) == "GN" and rep["policy"]["strip_color"]["restored"] is True
    assert rep["end_master_db"] <= rep["start_master_db"] + 0.05 or rep["end_master_db"] <= master_at_hold - 5.9


async def test_b_operator_pulls_the_master_while_a_tier_b_engagement_is_held(make_rig):
    """The engineer yanks the bus master during a held engagement in ring_out: the RAISE loop parked on the engagement keeps
    polling the master (one read per second), so the override is noticed within ~1 s, the engagement ends 'aborted' without
    deepening the band further, no MASTER write lands after the pull (hands off) and the strip is restored."""
    rig = await make_rig(policy={"tier_b": {"held_deepen_s": 1.0}})
    rig.fake.set_value("/bus/01/mix/fader", -30.0)
    rig.desk.invalidate()
    fader_writes: list[tuple[float, Any]] = []
    rig.events.subscribe(lambda ev: fader_writes.append((time.monotonic(), ev.data.get("value"))) if ev.data.get("address") == "/bus/01/mix/fader" else None,
                         types={"desk.write"})
    run = asyncio.create_task(rig.cfs.ring_out(1, target_gain_db=-18.0, step_db=1.0, dwell_ms=400))
    await wait_until(lambda: rig.cfs.mode is CfsMode.RINGOUT and rig.cfs.frames.frames_received >= 5, timeout=5.0, what="ring-out armed")
    ses = rig.cfs._ses
    rig.rta.inject_note(1000.0, -18.0, rise_frames=1)
    await wait_until(lambda: rig.notches(), timeout=6.0, what="the tier-B cut in ring_out")
    await wait_until(lambda: any(tb.state == "held" for tb in ses.policy.tier_b.values()), timeout=4.0, what="engagement in 'held'")
    t_pull = time.monotonic()
    rig.fake.set_value("/bus/01/mix/fader", -60.0)          # hands on the desk
    geq_writes_at_pull = len(ses.writer.writes)
    rep = await asyncio.wait_for(run, timeout=20.0)
    t_abort = time.monotonic()
    await rig.settle()
    assert rep["final_stage"] == "ABORT" and "moved on the desk" in (rep["abort_reason"] or ""), rep["abort_reason"]
    assert not [w for w in fader_writes if w[0] > t_pull], f"master written after the operator took over: {fader_writes}"
    assert float(rig.fake.value("/bus/01/mix/fader")) == pytest.approx(-60.0, abs=0.1)
    assert rig.fake.value(COLOR) == "GN"
    geq_after_pull = ses.writer.writes[geq_writes_at_pull:]
    print(f"GEQ writes after the operator pulled the master: {geq_after_pull}; override noticed {t_abort - t_pull:.1f} s after the pull")
    assert t_abort - t_pull <= 2.5, f"override noticed {t_abort - t_pull:.1f} s after the pull (polled once per second while parked)"
    assert geq_after_pull == [], f"GEQ deepened after the operator took over: {geq_after_pull}"
    ends = [e for e in rep["policy"]["tier_b"] if e["action"] == "end"]
    assert ends and ends[-1]["next_action"].startswith("none: session aborting")


async def test_b_an_exception_in_policy_code_does_not_kill_the_consumer_and_the_strip_is_still_restored(make_rig, monkeypatch, caplog):
    """A bug in a policy rule (here: the tier-B eligibility function raising on every frame once the strip is red) must not stop
    feed(): a real ring is still cut by tier A, the OTHER policy steps keep running (the alerts step after it clears the strip
    when the candidate ends), the traceback is logged once (not per frame), the report carries a warning and the error count,
    and stop() gives the strip its colour back."""
    import x32mcp.cfs as cfs_mod

    rig = await make_rig(policy={"alerts": {"clear_s": 0.3}})
    await _armed(rig)
    await asyncio.sleep(0.6)
    rig.rta.inject_note(2000.0, -34.0, rise_frames=1)       # quiet MODERATE: alert only
    await wait_until(lambda: rig.events_of("alert"), timeout=2.0, what="strip alert write")
    await rig.settle()
    assert rig.fake.value(COLOR) == "RDi"
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise RuntimeError("verifier-injected policy bug")

    monkeypatch.setattr(cfs_mod, "tier_b_eligible", boom)
    caplog.set_level(logging.ERROR, logger="x32mcp.cfs")
    await asyncio.sleep(0.5)
    assert calls["n"] >= 5, "the faulty rule is being hit every frame"
    ses = rig.cfs._ses
    frames0 = ses.frames
    rig.rta.inject_ring(2400.0, 20.0, start_db=-45.0, cap_db=-3.0)
    await wait_until(lambda: rig.notches(), timeout=4.0, what="tier-A cut of a real ring while the policy code is raising")
    assert rig.notches()[0]["tier"] == "A" and ses.frames > frames0 + 10
    # the quiet note ends: the alerts step still runs after the failing tier-B step, so the strip is cleared while armed
    rig.rta.stop_note(2000.0)
    await wait_until(lambda: any(e["on"] is False for e in rig.events_of("alert")), timeout=4.0, what="strip cleared although tier B keeps raising")
    await rig.settle()
    colour_while_armed = rig.fake.value(COLOR)
    errors = [r for r in caplog.records if "detector task failed" in r.getMessage() or "policy step tier_b raised" in r.getMessage()]
    rep = (await rig.cfs.stop())["report"]
    await rig.settle()
    assert colour_while_armed == "GN"
    assert rig.fake.value(COLOR) == "GN" and rep["policy"]["strip_color"]["restored"] is True
    warnings = [w for w in rep["preflight"]["warnings"] if "policy step 'tier_b' raised" in w]
    print(f"strip while armed after the candidate cleared: {colour_while_armed}; traceback log records: {len(errors)} "
          f"in ~{ses.frames - frames0} frames; policy warnings in report: {warnings}; errors: {rep['policy']['errors']}")
    assert len(errors) == 1, "the traceback is logged once per step, then only counted"
    assert warnings and "RuntimeError" in warnings[0] and rep["policy"]["errors"]["tier_b"] >= 20


# ------------------------------------------------------------------------------------------ (c) two candidates alternating

async def test_c_two_alternating_candidates_never_drive_more_than_one_colour_write_per_second(make_rig):
    rig = await make_rig(policy={"alerts": {"clear_s": 0.1, "hold_s": 0.1, "min_write_interval_s": 1.0}, "tier_b": {"enabled": False}})
    stamps: list[tuple[float, Any]] = []
    rig.events.subscribe(lambda ev: stamps.append((time.monotonic(), ev.data.get("value"))) if ev.data.get("address") == COLOR else None,
                         types={"desk.write"})
    await _armed(rig)
    await asyncio.sleep(0.6)
    t_end = time.monotonic() + 9.0
    i = 0
    while time.monotonic() < t_end:                          # 0.45 s on / 0.35 s off, alternating between two frequencies
        f = 1000.0 if i % 2 == 0 else 2500.0
        rig.rta.inject_note(f, -34.0, rise_frames=1)
        await asyncio.sleep(0.45)
        rig.rta.stop_note(f)
        await asyncio.sleep(0.35)
        i += 1
    await asyncio.sleep(1.6)
    ses = rig.cfs._ses
    writes_in_session = [t for t, _v in stamps]
    gaps = [b - a for a, b in zip(writes_in_session, writes_in_session[1:])]
    ons = [e for e in rig.events_of("candidate") if e["alert"] == "on"]
    offs = [e for e in rig.events_of("candidate") if e["alert"] == "off"]
    print(f"colour writes: {len(stamps)} over 9 s, min gap {min(gaps) if gaps else None}; alert on/off events {len(ons)}/{len(offs)}")
    assert len(stamps) >= 2, "the alternation should have produced at least one on and one off write"
    assert all(g >= 0.98 for g in gaps), f"colour writes closer than min_write_interval_s: {gaps}"
    assert ses.policy.color_writes == len(stamps)
    await rig.cfs.stop()
    await rig.settle()
    assert rig.fake.value(COLOR) == "GN"


# ------------------------------------------------------------------------------------------ (d) budget exhausted by tier B

async def test_d_tier_b_spending_the_last_budget_band_ends_the_ring_out_cleanly(make_rig):
    rig = await make_rig()
    rig.fake.set_value("/bus/01/mix/fader", -30.0)
    rig.desk.invalidate()
    t0 = time.monotonic()
    run = asyncio.create_task(rig.cfs.ring_out(1, target_gain_db=-15.0, step_db=1.0, dwell_ms=400, notch_budget=1))
    await wait_until(lambda: rig.cfs.mode is CfsMode.RINGOUT and rig.cfs.frames.frames_received >= 5, timeout=5.0, what="ring-out armed")
    rig.rta.inject_note(1000.0, -18.0, rise_frames=1)
    rep = await asyncio.wait_for(run, timeout=25.0)
    dt = time.monotonic() - t0
    assert rep["final_stage"] == "DONE" and rep["budget_left"] == 0, (rep["final_stage"], rep.get("abort_reason"))
    assert rep["notches"] and rep["notches"][0]["tiers"] == ["B"]
    done = [s for s in rep["stages"] if s["stage"] == "DONE"]
    assert done and done[-1].get("reason") == "notch budget spent"
    assert rep["end_master_db"] < rep["max_master_db"] or rep["max_master_db"] == rep["start_master_db"]
    await rig.settle()
    assert rig.fake.value(COLOR) == "GN"
    tb = rep["policy"]["tier_b"]
    print(f"budget-1 ring-out with a tier-B cut finished in {dt:.1f} s at {rep['end_master_db']} dB; tier_b log: "
          f"{[(e['action'], e['verdict'], e['depth_db']) for e in tb]}")
    # the run does not call itself DONE under an unjudged policy cut: the verdict (and the held follow-up) came first
    assert any(e["action"] == "verdict" for e in tb) and tb[-1]["action"] == "end" and tb[-1]["next_action"] != "none: the session finished with the engagement open"


# ------------------------------------------------------------------------------------------ (e) operator's deeper cut on the band

async def test_e_tier_b_never_writes_a_band_shallower_than_the_operators_own_cut(make_rig):
    """(1) the band already carries -6 dB before arming: the tier-B step is -9, not -3. (2) after a tier-B -3 the engineer cuts the
    band to -9 by hand: the held-deepen writes nothing. (3) an off-grid hand cut (-5) is deepened to -8, never rounded up."""
    # (1) pre-existing -6 dB
    rig = await make_rig(policy={"tier_b": {"held_deepen_s": 1.0}})
    rig.fake.set_value(PAR_1K, -6.0)
    rig.desk.invalidate()
    await _armed(rig)
    ses = rig.cfs._ses
    assert ses.nc.gains.get(GEQ_BAND_1K) == pytest.approx(-6.0, abs=0.01)
    await asyncio.sleep(0.6)
    rig.rta.inject_note(1000.0, -18.0, rise_frames=1)
    await wait_until(lambda: rig.notches(), timeout=3.0, what="the tier-B cut on a pre-cut band")
    assert rig.notches()[0]["depth_db"] == -9.0 and _band_writes(rig, GEQ_BAND_1K) == [-9.0]
    await asyncio.sleep(3.0)
    assert _band_writes(rig, GEQ_BAND_1K) == [-9.0]
    await rig.cfs.stop()
    rig.rta.stop_note(1000.0)
    await asyncio.sleep(0.8)
    # (2) hand cut to -9 after the policy's -3
    rig.fake.set_value(PAR_1K, 0.0)
    rig.desk.invalidate()
    await _armed(rig)
    ses = rig.cfs._ses
    await asyncio.sleep(0.6)
    rig.rta.inject_note(1000.0, -18.0, rise_frames=1)
    await wait_until(lambda: _band_writes(rig, GEQ_BAND_1K) == [-3.0], timeout=3.0, what="the tier-B -3")
    rig.fake.set_value(PAR_1K, -9.0)                         # front-panel move, pushed over /xremote
    await wait_until(lambda: ses.nc.gains.get(GEQ_BAND_1K, 0.0) <= -8.9 and ses.writer.gains.get(GEQ_BAND_1K, 0.0) <= -8.9,
                     timeout=3.0, what="hand cut adopted by controller and writer")
    await asyncio.sleep(3.5)                                 # verdict + held_deepen_s + margin
    assert _band_writes(rig, GEQ_BAND_1K) == [-3.0], f"wrote over the operator's -9: {_band_writes(rig, GEQ_BAND_1K)}"
    await rig.settle()
    assert float(rig.fake.value(PAR_1K)) == pytest.approx(-9.0, abs=0.01)
    ends = [tb.outcome for tb in ses.policy.tier_b.values()]
    await rig.cfs.stop()
    rig.rta.stop_note(1000.0)
    await asyncio.sleep(0.8)
    # (3) off-grid hand cut -5
    rig.fake.set_value(PAR_1K, -5.0)
    rig.desk.invalidate()
    await _armed(rig)
    ses = rig.cfs._ses
    assert ses.nc.gains.get(GEQ_BAND_1K) == pytest.approx(-5.0, abs=0.01)   # the session starts from the desk's value (as in (1))
    await asyncio.sleep(0.6)
    rig.rta.inject_note(1000.0, -18.0, rise_frames=1)
    await wait_until(lambda: rig.notches() and _band_writes(rig, GEQ_BAND_1K), timeout=3.0, what="tier-B on an off-grid band")
    first = _band_writes(rig, GEQ_BAND_1K)[0]
    assert first == pytest.approx(-8.0, abs=0.01), first
    await asyncio.sleep(3.0)
    ws = _band_writes(rig, GEQ_BAND_1K)
    assert ws == sorted(ws, reverse=True) and min(ws) >= -9.0 - 1e-6, ws
    await rig.cfs.stop()
    await rig.settle()
    assert rig.fake.value(COLOR) == "GN"
    print(f"(2) engagement outcomes after the hand cut: {ends}; (3) writes from -5: {ws}")


async def test_e_operator_releasing_a_tier_b_band_while_held(make_rig):
    """The engineer flattens the band the policy cut (-3 -> 0) while the engagement is 'held'. The push is adopted (gains 0),
    the engagement ends 'operator', the band is ignore-listed for policy cuts and never re-cut by the policy: the released
    band stays where the engineer put it."""
    rig = await make_rig(policy={"tier_b": {"held_deepen_s": 1.0}})
    await _armed(rig)
    ses = rig.cfs._ses
    await asyncio.sleep(0.6)
    rig.rta.inject_note(1000.0, -18.0, rise_frames=1)
    await wait_until(lambda: _band_writes(rig, GEQ_BAND_1K) == [-3.0], timeout=3.0, what="the tier-B -3")
    await wait_until(lambda: any(tb.state == "held" for tb in ses.policy.tier_b.values()), timeout=3.0, what="'held'")
    rig.fake.set_value(PAR_1K, 0.0)                          # the engineer releases it
    await wait_until(lambda: ses.nc.gains.get(GEQ_BAND_1K, -3.0) == 0.0, timeout=2.0, what="release adopted")
    await asyncio.sleep(4.0)
    ws = _band_writes(rig, GEQ_BAND_1K)
    await rig.settle()
    briefs = [tb.brief() for tb in ses.policy.tier_b.values()]
    print(f"writes after the engineer released the band: {ws}; band now {rig.fake.value(PAR_1K)}; engagements: {briefs}; ignore: {ses.policy.ignore}")
    rep = (await rig.cfs.stop())["report"]
    assert ws == [-3.0], f"the policy re-cut a band the engineer released: {ws}"
    assert float(rig.fake.value(PAR_1K)) == pytest.approx(0.0, abs=0.01)
    assert any(i["band"] == GEQ_BAND_1K and "released by hand" in i["why"] for i in rep["policy"]["ignore"])
    assert any(e["action"] == "end" and e["next_action"].startswith("ignore-listed: the engineer released") for e in rep["policy"]["tier_b"])
    await rig.settle()
    assert rig.fake.value(COLOR) == "GN"


# ------------------------------------------------------------------------------------------ (f) LF edge edge cases

async def test_f_lf_edge_with_no_included_mic_aux_only_partial_and_failed_reads(make_rig, monkeypatch):
    rig = await make_rig()
    cfs = rig.cfs
    # zero included mics -> the detector's mode default (None)
    e = await cfs._lf_edge(SimpleNamespace(included_mics=[]))
    assert e.hz is None and "no included mic" in e.source
    # only an aux-in (ch 33+, no channel HPF at all): the permissive 100 Hz, not the detector's 160 Hz watch default
    e = await cfs._lf_edge(SimpleNamespace(included_mics=[SimpleNamespace(ch=33)]))
    print("aux-only included mics ->", e.to_dict())
    assert e.hz == 100.0 and "no HPF" in e.source
    # a mic whose preamp read fails -> permissive 100 Hz, arming not refused
    async def failing_dump(*a, **k):
        raise DeskError("TIMEOUT", "verifier: desk did not answer")
    monkeypatch.setattr(rig.desk, "dump", failing_dump)
    e = await cfs._lf_edge(SimpleNamespace(included_mics=[SimpleNamespace(ch=1), SimpleNamespace(ch=2)]))
    assert e.hz == 100.0 and "unread" in e.source
    # an unexpected exception type from the (advisory) read path does not refuse arming either: 100 Hz 'unread'
    async def exploding_dump(*a, **k):
        raise RuntimeError("verifier: unexpected")
    monkeypatch.setattr(rig.desk, "dump", exploding_dump)
    e = await cfs._lf_edge(SimpleNamespace(included_mics=[SimpleNamespace(ch=1)]))
    assert e.hz == 100.0 and "unread" in e.source
    monkeypatch.undo()
    # partial section (hpon present, hpf missing) -> counts as HPF on but corner unread -> 100 Hz default for that mic
    real_dump = rig.desk.dump

    async def partial_dump(*a, sections=None, **k):
        st = await real_dump(*a, sections=sections, **k)
        for p in list(st.sections):
            if p.endswith("/preamp"):
                st.sections[p] = {kk: v for kk, v in st.sections[p].items() if kk != "preamp/hpf"}
        return st
    monkeypatch.setattr(rig.desk, "dump", partial_dump)
    e = await cfs._lf_edge(SimpleNamespace(included_mics=[SimpleNamespace(ch=1)]))
    assert e.hz == 100.0, e.to_dict()
    monkeypatch.undo()
    # and the whole thing end to end with the strip colour unreadable: arming still works, colour alerts off with a warning
    real_dump2 = rig.desk.dump

    async def no_config_dump(*a, sections=None, **k):
        if sections and any(str(s).endswith("/config") for s in sections):
            raise DeskError("TIMEOUT", "verifier: config read failed")
        return await real_dump2(*a, sections=sections, **k)
    monkeypatch.setattr(rig.desk, "dump", no_config_dump)
    res = await _armed(rig)
    assert res["lf_edge"]["lf_edge_hz"] == pytest.approx(84.7, abs=0.11)
    ses = rig.cfs._ses
    assert ses.policy.color_orig is None and any("colour alerts are off" in w for w in ses.warnings)
    rig.rta.inject_note(2000.0, -34.0, rise_frames=1)
    await asyncio.sleep(1.5)
    assert rig.events_of("alert") == [] and any(e["alert"] == "on" for e in rig.events_of("candidate"))
    await rig.cfs.stop()


# ------------------------------------------------------------------------------------------ (g) ring_out with music

class ProbeRoomRta(RoomSourceRta):
    """RoomSourceRta whose note at ``howl_hz`` is a regenerative line: it answers every master move ``k`` dB per dB (the active
    probe's over-response) and dies once the GEQ has cut its band by >= ``kill_db`` (excess below the cut). Every other note is
    stage programme that does not follow the bus master at all."""

    def __init__(self, *a, howl_hz: float = 1000.0, k: float = 4.0, kill_db: float = 2.5, bed_follows: bool = True, **kw) -> None:
        super().__init__(*a, **kw)
        self.howl_band = self.band_for_hz(howl_hz)
        self.k = float(k)
        self.kill_db = float(kill_db)
        self.bed_follows = bed_follows

    def tick(self):
        fr = super().tick()
        if not self.bed_follows:
            self._base = self._base0
        return fr

    def _note_level(self, n) -> float:
        base = SyntheticRta._note_level(self, n)
        if n.band == self.howl_band:
            if self._cuts.get(n.band, 0.0) >= self.kill_db:
                return -128.0
            return base + self.k * self._offset
        return base


async def _closed_loop_rig(descriptor, tmp_path, src: SyntheticRta, *, policy: CfsPolicyConfig | None = None, name: str = "g"):
    fake = FakeDesk(descriptor, host="127.0.0.1", port=0, name="X32-FAKE", rta=src)
    await fake.start()
    events = EventBus()
    conn = X32Connection(descriptor, events, **CONN_OPTS)
    await conn.connect(fake.host, fake.port)
    pol = Policy(descriptor, events)
    desk = Desk(descriptor, conn, pol, events, SnapshotStore(tmp_path / f"snap-{name}"))
    reports = ReportStore(tmp_path / f"reports-{name}")
    cfs = CfsManager(desk, pol, events, reports, snapshots=None, cfs_policy=policy or CfsPolicyConfig())
    log: list = []
    for kind, typ in (("notch", "cfs.notch"), ("policy", "cfs.policy"), ("programme", "cfs.programme_present"), ("stage", "cfs.stage"),
                      ("alert", "cfs.alert")):
        events.subscribe(lambda ev, kind=kind: log.append((kind, time.monotonic(), dict(ev.data))), types={typ})
    plan = await plan_setup(desk, [1])
    await apply_setup(desk, plan)
    for ch in (1, 2):
        fake.set_value(f"/ch/{ch:02d}/mix/01/level", -20.0)
    fake.set_value("/bus/01/mix/fader", -30.0)
    fake.set_value(COLOR, "GN")
    desk.invalidate()
    rig = Rig(fake, conn, desk, events, cfs, reports, log)

    async def close():
        await cfs.close()
        await desk.close()
        await conn.close()
        await fake.stop()
    return rig, close


async def test_g_ring_out_with_programme_playing_still_cuts_a_line_that_answers_the_gain_steps(descriptor, tmp_path):
    """Music on stage during a ring-out (contract broken, reported): no MODERATE-after-K2 emission, tier B never touches the
    strummed 0.3 s notes, but a line that over-responds to the run's own +1 dB steps (PROBE / RISE evidence) is still cut, and the
    run completes."""
    src = ProbeRoomRta(seed=7, band_hz=rta_band_hz(descriptor), level_100hz_db=-50.0, level_10khz_db=-60.0, noise_db=2.0, wobble_db=0.5,
                       howl_hz=1000.0, k=4.0, bed_follows=False)
    rig, close = await _closed_loop_rig(descriptor, tmp_path, src)
    src._master = lambda: rig.fake.value("/bus/01/mix/fader")
    stop = asyncio.Event()
    strummer = None
    try:
        src.inject_note(1000.0, -30.0, rise_frames=1)        # the suspect line, sounding before arm
        strummer = asyncio.create_task(_strum(src, stop, level_db=-24.0))
        await asyncio.sleep(0.6)
        rep = await asyncio.wait_for(rig.cfs.ring_out(1, target_gain_db=-24.0, step_db=1.0, dwell_ms=600), timeout=45.0)
    finally:
        stop.set()
        if strummer is not None:
            await strummer
        await close()
    pp = rep["policy"]["programme_present"]
    notches = [e for k, _t, e in rig.log if k == "notch"]
    print("programme:", pp, "| notches:", [(n["tier"], n.get("policy"), n["band"], n["depth_db"], n["reasons"]) for n in notches],
          "| final:", rep["final_stage"], rep.get("abort_reason"))
    assert pp["detected"] is True
    assert any("programme detected on Bus 1" in w for w in rep["preflight"]["warnings"])
    assert not any("confirmed_K2" in d.get("reasons", []) for d in rep["detection_log"])
    howl_cuts = [n for n in notches if n["band"] == GEQ_BAND_1K]
    assert howl_cuts, "the over-responding line at 1 kHz was not cut with programme playing"
    assert any(any(r.startswith(("probe", "rise", "fastrise")) for r in n["reasons"]) for n in howl_cuts if n["tier"] == "A") or \
        any(n.get("policy") == "backoff_probe" for n in howl_cuts)
    assert not [n for n in notches if n.get("policy") == "tier_b" and n["band"] != GEQ_BAND_1K], "tier B cut a strummed programme note"
    assert rep["final_stage"] in ("DONE", "ABORT")


# ------------------------------------------------------------------------------------------ back-off probe starves tier-A cuts

async def test_backoff_probe_yields_to_a_ring_that_starts_during_the_probe(descriptor, tmp_path):
    """A STATIONARY room source triggers the back-off probe (master -3 dB for a dwell, restore, wait a probe window). A real ring
    that takes off while the ring-out task sits in the probe's frame-paced waits is detected by the consumer (pending) and must be
    cut at once: the probe's waits yield to the pending Detection, the probe is abandoned with the master left at the probe level
    (re-queued once), and HOLD -> NOTCH runs within a few frames."""
    src = RoomSourceRta(seed=5, band_hz=rta_band_hz(descriptor), level_100hz_db=-55.0, level_10khz_db=-65.0, noise_db=2.0, wobble_db=0.5,
                        master=None, howl=False)
    rig, close = await _closed_loop_rig(descriptor, tmp_path, src, name="probe-latency")
    src._master = lambda: rig.fake.value("/bus/01/mix/fader")
    try:
        src.inject_note(1000.0, -27.0, rise_frames=1)        # ~33 dB prominent, follows the steps 1 dB/dB -> STATIONARY backoff_advised
        for _ in range(10):
            src.tick()
        run = asyncio.create_task(rig.cfs.ring_out(1, target_gain_db=-22.0, step_db=1.0, dwell_ms=700))
        await wait_until(lambda: any(k == "stage" and e.get("stage") == "PROBE" for k, _t, e in rig.log), timeout=25.0, what="back-off PROBE stage")
        t_probe = time.monotonic()
        await asyncio.sleep(0.3)
        ses = rig.cfs._ses
        n_det0 = len(ses.detections)
        src.inject_ring(2500.0, 25.0, start_db=-50.0, cap_db=-6.0)   # a ring takes off mid-probe
        t_inject = time.monotonic()
        await wait_until(lambda: len(ses.detections) > n_det0, timeout=6.0, what="the ring detected (pending)")
        t_detect = time.monotonic()
        level_at_detect = ses.detections[-1]["level_db"]
        await wait_until(lambda: any(k == "notch" and e.get("band") == 22 for k, _t, e in rig.log), timeout=15.0, what="the ring's cut")
        t_cut = next(t for k, t, e in rig.log if k == "notch" and e.get("band") == 22)
        ring_level_at_cut = src.ring_level(2500.0)
        rep = await asyncio.wait_for(run, timeout=40.0)
    finally:
        await close()
    latency = t_cut - t_detect
    print(f"back-off probe started {t_inject - t_probe:.2f} s before the ring; detection->cut latency {latency:.2f} s "
          f"(ring at {level_at_detect} dBFS when detected, internal {ring_level_at_cut} dBFS when cut); probes: {rep['policy']['backoff_probes']}; "
          f"final {rep['final_stage']} {rep.get('abort_reason')}")
    # BRIEF §5 budgets detect -> cut well under a second: the probe's waits yield to the pending Detection
    assert latency <= 0.5, f"tier-A cut delayed {latency:.2f} s by the back-off probe"
    probes = rep["policy"]["backoff_probes"]
    assert probes, "the back-off probe never ran"
    if t_inject - t_probe < 1.2:                              # the ring took off during the dip: the probe was abandoned, master left lowered
        first = probes[0]
        assert first["verdict"] == "interrupted" and first["master_restored_db"] is None and first["retry"] is True, first
    assert rep["final_stage"] in ("DONE", "ABORT")


# ------------------------------------------------------------------------------------------ item 5 x item 3: loud-ish at-arm line < 30 dB prominent

async def test_at_arm_loudish_line_under_30db_prominent_is_deepened_by_the_policy_only(make_rig):
    """A -18 dBFS line ~26 dB prominent sounding at arm: watch declines the at-arm cut (not LOUD, < 30 dB) and withdraws the
    at-arm name from the detector's emission record (note_suppressed), tier B cuts it once (loud-ish, policy tag 'at_arm');
    after 'held' the POLICY deepens it held_deepen_s apart (tier B, tier_b_held) -- the detector does not re-emit deepen_held on
    the observation the watch declined, and the at-arm rule does not let such a re-emission through."""
    rig = await make_rig(-38.0, -50.0, policy={"tier_b": {"held_deepen_s": 3.0}})
    rig.rta.inject_note(1000.0, -18.0, rise_frames=1)
    await asyncio.sleep(0.5)
    await _armed(rig)
    ses = rig.cfs._ses
    await wait_until(lambda: ses.policy.at_arm_log or rig.notches(), timeout=3.0, what="at-arm decision")
    assert ses.policy.at_arm_log, f"expected the at-arm rule to decline; got notches {rig.notches()}"
    await wait_until(lambda: rig.notches(), timeout=4.0, what="tier-B cut of the at-arm line")
    n0 = rig.notches()[0]
    assert n0["tier"] == "B" and n0["policy"] == "at_arm", n0     # the notch's policy tag names the rule that engaged the line
    cand = next(c for c in ses.det.candidates if abs(c.freq_hz - 1015) < 50)
    assert "established_at_arm" not in cand.emit_evidence and "suppressed_at_arm" in cand.emit_evidence
    await asyncio.sleep(7.5)
    notches = rig.notches()
    print("at-arm loud-ish line: prominence", ses.policy.at_arm_log[0]["prominence_db"], "level", ses.policy.at_arm_log[0]["level_db"],
          "| cut sequence (tier, policy, depth, reasons):", [(n["tier"], n.get("policy"), n["depth_db"], n["reasons"]) for n in notches],
          "| cut_log:", [(v["verdict"], v["deepen"]) for v in ses.det.cut_log],
          "| tier_b log:", [(e["t"], e["action"], e["verdict"], e["depth_db"]) for e in ses.policy.tier_b_log])
    ws = _band_writes(rig, GEQ_BAND_1K)
    assert ws == sorted(ws, reverse=True) and len(set(ws)) == len(ws) and min(ws) >= -9.0 - 1e-6
    await rig.cfs.stop()
    await rig.settle()
    assert rig.fake.value(COLOR) == "GN"
    # the brief: an at-arm line < 30 dB prominent is tier-B material only; deepening after 'held' is the policy's, held_deepen_s apart
    deepens = [n for n in notches[1:]]
    assert deepens and all(n["tier"] == "B" and n["policy"] == "tier_b_held" for n in deepens), [(n["tier"], n.get("policy"), n["reasons"][-1]) for n in deepens]
    assert not any(v["deepen"] for v in ses.det.cut_log), "the detector granted itself a deepen right on the declined at-arm observation"


# ------------------------------------------------------------------------------------------ item 3 'held' handling: masking is not 'ended'

async def test_held_line_masked_by_a_transient_is_not_ignore_listed_as_ended(make_rig):
    """A 'held' engagement whose track the detector drops because a loud passage MASKED the line for > coast_frames (0.4 s) has
    not 'ended': its band still reads at (above) the held level, so the engagement finishes 'lost' WITHOUT ignore-listing, and
    the line -- back 0.6 s later on a new track -- is engaged again and deepened (verdict-gated) like any held line."""
    rig = await make_rig(policy={"tier_b": {"held_deepen_s": 2.0}})
    await _armed(rig)
    ses = rig.cfs._ses
    await asyncio.sleep(0.8)
    rig.rta.inject_note(1000.0, -18.0, rise_frames=1)       # stands in for a compressor-held howl: holds through the -3
    await wait_until(lambda: any(tb.state == "held" for tb in ses.policy.tier_b.values()), timeout=5.0, what="'held'")
    await asyncio.sleep(0.5)
    base0 = rig.rta._base
    rig.rta._base = tuple(b + 35.0 for b in base0)            # a loud chord swamps the analyser for 0.6 s
    await asyncio.sleep(0.6)
    rig.rta._base = base0
    await asyncio.sleep(6.0)                                 # > held_deepen_s + a verdict, with the line standing again
    ign = list(ses.policy.ignore)
    writes = _band_writes(rig, GEQ_BAND_1K)
    live = [(round(c.freq_hz), c.klass, c.cut_verdict) for c in ses.det.candidates if abs(c.freq_hz - 1015) < 50 and not c.misses]
    outcomes = [e["next_action"] for e in ses.policy.tier_b_log if e["action"] == "end"]
    print(f"ignore list: {ign}; band writes: {writes}; line now: {live}; alert: {rig.cfs.state.alert}; end rows: {outcomes}")
    await rig.cfs.stop()
    assert live, "the line is standing again after the transient"
    assert not ign, "a masked (not ended) held line was ignore-listed"
    assert outcomes and outcomes[0].startswith("none: the track was lost while its band still read at the held level")
    assert writes[:2] == [-3.0, -6.0], "the held line should have been deepened once it stood again"


async def test_held_line_that_really_ends_is_ignore_listed(make_rig):
    """The counterpart: a 'held' line that switches itself OFF (its band falls back to the bed) did end -- a howl never does --
    so its band is ignore-listed with the cut left in place, and the same note returning later is alerted, not cut again."""
    rig = await make_rig(policy={"tier_b": {"held_deepen_s": 2.0}})
    await _armed(rig)
    ses = rig.cfs._ses
    await asyncio.sleep(0.8)
    rig.rta.inject_note(1000.0, -18.0, rise_frames=1)
    await wait_until(lambda: any(tb.state == "held" for tb in ses.policy.tier_b.values()), timeout=5.0, what="'held'")
    await asyncio.sleep(0.3)
    rig.rta.stop_note(1000.0)                                # the note ends while 'held' (no verdict pending any more)
    await wait_until(lambda: ses.policy.ignore, timeout=3.0, what="ignore-listing of the ended held line")
    ign = ses.policy.ignore[0]
    assert ign["band"] == GEQ_BAND_1K and "ended by itself after holding" in ign["why"]
    rig.rta.inject_note(1000.0, -18.0, rise_frames=1)       # the same note again
    await asyncio.sleep(3.0)
    assert _band_writes(rig, GEQ_BAND_1K) == [-3.0], _band_writes(rig, GEQ_BAND_1K)
    assert rig.cfs.state.alert is True
    await rig.cfs.stop()
    await rig.settle()
    assert rig.fake.value(COLOR) == "GN" and float(rig.fake.value(PAR_1K)) == pytest.approx(-3.0, abs=0.01)


# ------------------------------------------------------------------------------------------ (h) a bystander verdict must not orphan a line

async def test_h_bystander_verdict_from_a_neighbours_cut_does_not_bar_the_line_from_its_own_tier_b_engagement(make_rig):
    """K8 seed 5 at the cfs level (review response 2026-09-24, C2). A loud-ish MODERATE line A at 1 kHz is about to be cut -3
    by tier B; a second family-less line B appears 0.3 octave up (1250 Hz: three RTA bands clear of A so both stay narrow,
    outside the fake desk's +-1/6-octave bell) shortly before the write lands. ``note_cut()`` judges every live line within
    1/3 octave of the bell, so B -- never emitted, never cut -- is filed with a bystander verdict ('held': the bell's expected
    reach at 0.3 octave is under a decibel and B did not move; 'insufficient' / 'ambiguous' under noise). On main that verdict
    barred B from tier B for ever ("cut verdict held") and the hopped howl of K8 was orphaned. Now B gets its own tier-B
    engagement once the verdict has settled. Its write goes where the GEQ ladder puts a line one band from an open notch
    (``merge_adjacent_bands`` 1): the 1 kHz notch is deepened to -6 (B's own band would be 1.25 kHz); the PEQ actuator
    (design S5, merge 0.08 oct) is what gives such a line its own notch. A's own ladder is not touched by the policy
    (held_deepen_s is out of reach here)."""
    rig = await make_rig(policy={"tier_b": {"held_deepen_s": 30.0}})
    await _armed(rig, notch_budget=4)
    await asyncio.sleep(0.6)
    hz_b = 1250.0
    rig.rta.inject_note(1000.0, -18.0, rise_frames=1)
    await asyncio.sleep(0.45)                                  # A is past its onset (not 'swelling'); B is born 9 frames later
    rig.rta.inject_note(hz_b, -18.0, rise_frames=1)
    ses = rig.cfs._ses
    await wait_until(lambda: rig.notches(), timeout=3.0, what="A's tier-B cut")
    n0 = rig.notches()[0]
    assert n0["band"] == GEQ_BAND_1K and n0["tier"] == "B" and n0["depth_db"] == -3.0, n0
    assert ses.nc.band_for_freq(hz_b) == GEQ_BAND_1K + 1       # B's own band is 1.25 kHz ...
    assert ses.det.cfg.merge_adjacent_bands == 1               # ... one band from the open notch: the ladder merges into it

    def b_verdicts() -> list[dict]:
        return [v for v in ses.det.cut_log if abs(v["freq_hz"] - hz_b) < 60.0 and not v["emitted"]]

    def b_engagements() -> list[dict]:
        return [e for e in ses.policy.tier_b_log if e["action"] == "tier_b" and abs(e["freq_hz"] - hz_b) < 60.0]

    await wait_until(b_verdicts, timeout=3.0, what="a bystander verdict on B from A's cut")
    vb = b_verdicts()[0]
    assert vb["verdict"] in ("held", "insufficient", "ambiguous") and vb["depth_db"] == -3.0, vb
    assert not b_engagements(), "B must not be engaged while its bystander verdict is pending"
    assert ses.detections == [], "neither line may be emitted by the detector (both MODERATE)"
    # C2: a settled bystander verdict no longer bars B from its own engagement (after the 2 s engagement cooldown)
    try:
        await wait_until(b_engagements, timeout=6.0, what="B's own tier-B engagement after the bystander verdict")
    except AssertionError:
        print("candidates:", [(round(c.freq_hz), c.klass, round(c.level_db, 1), c.cut_verdict, c.emitted, tuple(c.reasons)[:6]) for c in ses.det.candidates])
        print("tier_b log:", ses.policy.tier_b_log[-4:], "| cut_log:", ses.det.cut_log[-3:])
        raise
    eb = b_engagements()[0]
    assert eb["tier"] == "B" and eb["reason"] == "tier_b", eb
    await wait_until(lambda: len(rig.notches()) >= 2, timeout=3.0, what="the write for B's engagement")
    n1 = rig.notches()[1]
    assert n1["tier"] == "B" and n1["policy"] == "tier_b" and n1["band"] == GEQ_BAND_1K and n1["depth_db"] == -6.0, n1
    assert eb["band"] == GEQ_BAND_1K and eb["depth_db"] == -6.0, eb
    assert _band_writes(rig, GEQ_BAND_1K) == [-3.0, -6.0] and _band_writes(rig, GEQ_BAND_1K + 1) == []
    await rig.settle()
    assert float(rig.fake.value(PAR_1K)) == pytest.approx(-6.0, abs=0.01)
    assert ses.nc.budget_left == 3 and ses.nc.touched_bands == {GEQ_BAND_1K}
    rep = (await rig.cfs.stop())["report"]
    await rig.settle()
    assert rig.fake.value(COLOR) == "GN"
    starts = [e for e in rep["policy"]["tier_b"] if e["action"] == "tier_b"]
    assert [round(e["freq_hz"]) for e in starts] == [1015, 1250], starts
    print("bystander verdict on B:", vb, "| engagements:", [(e["freq_hz"], e["band"], e["depth_db"]) for e in starts],
          "| notches:", [(n["band"], n["tier"], n.get("policy"), n["depth_db"]) for n in rig.notches()])
