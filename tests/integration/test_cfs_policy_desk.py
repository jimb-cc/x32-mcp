"""The CFS² policy layer (docs/CFS_POLICY.md) end to end against the FakeDesk: cfs.py consumes the detector's hooks
(MODERATE candidates, note_cut() verdicts, flags, programme_present) and turns them into desk actions.

Path under test: CfsManager -> Desk -> X32Connection -> OSC/UDP (in-memory shim in the sandbox) -> FakeDesk -> its
SyntheticRta -> /meters/15 -> FeedbackDetector -> policy -> GEQ / scribble-strip / master writes -> FakeDesk applies the
GEQ to the analyser -> the detector's verdict -> policy follow-up.

The fake desk of each test carries a *quiet* synthetic bed (the shared ``fakedesk`` fixture's default bed reads HOT: its
arm-time p95 is -16 dBFS, which puts the loud-ish line at -6) so that a -18 dBFS line is loud-ish but not LOUD, and a
family-less line arriving within one frame (``inject_note(rise_frames=1)``: a howl slamming into a limiter faster than the
frame rate looks exactly like this) is MODERATE -- the tier-B case the detector publishes but will not cut by itself.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio

from conftest import CONN_OPTS, wait_until
from x32mcp.cfs import CfsManager, CfsMode, ReportStore
from x32mcp.cfs_policy import CfsPolicyConfig
from x32mcp.connection import X32Connection
from x32mcp.desk import Desk
from x32mcp.events import EventBus
from x32mcp.fakedesk import FakeDesk
from x32mcp.meters import SyntheticRta, rta_band_hz
from x32mcp.nodes import SnapshotStore
from x32mcp.policy import Policy
from x32mcp.provision import apply_setup, plan_setup

GEQ_BAND_1K = 18          # 1-based GEQ band at 1 kHz
PAR_1K = f"/fx/5/par/{GEQ_BAND_1K:02d}"
COLOR = "/bus/01/config/color"


@dataclass
class Rig:
    fake: FakeDesk
    conn: X32Connection
    desk: Desk
    events: EventBus
    cfs: CfsManager
    reports: ReportStore
    log: list

    @property
    def rta(self) -> SyntheticRta:
        return self.fake.rta

    async def settle(self) -> None:
        """Writes are fire-and-forget: one GET round trip guarantees the fake processed them."""
        await self.conn.get("/-stat/selidx")

    def notches(self) -> list[dict]:
        return [e for kind, e in self.log if kind == "notch"]

    def events_of(self, kind: str) -> list[dict]:
        return [e for k, e in self.log if k == kind]


@pytest_asyncio.fixture
async def make_rig(descriptor, tmp_path):
    """``await make_rig(level_100hz_db, level_10khz_db, policy={...overrides}, hpf={ch: hz|None})`` -> a provisioned Rig on a
    fake desk with that synthetic bed: GEQ2 in FX5 inserted on bus 1, Ch 1+2 sending to bus 1 at -20, bus master -20, strip GN."""
    made: list[tuple] = []

    async def _make(level_100hz_db: float = -45.0, level_10khz_db: float = -55.0, *, policy: dict | None = None,
                    hpf: dict[int, float | None] | None = None, seed: int = 3, color: str = "GN") -> Rig:
        rta = SyntheticRta(seed=seed, band_hz=rta_band_hz(descriptor), level_100hz_db=level_100hz_db, level_10khz_db=level_10khz_db,
                           noise_db=2.0, wobble_db=1.0)
        fake = FakeDesk(descriptor, host="127.0.0.1", port=0, name="X32-FAKE", rta=rta)
        await fake.start()
        events = EventBus()
        conn = X32Connection(descriptor, events, **CONN_OPTS)
        await conn.connect(fake.host, fake.port)
        pol = Policy(descriptor, events)
        desk = Desk(descriptor, conn, pol, events, SnapshotStore(tmp_path / f"snap{len(made)}"))
        reports = ReportStore(tmp_path / f"reports{len(made)}")
        merged = CfsPolicyConfig.from_descriptor(descriptor).to_dict()
        for k, v in (policy or {}).items():
            if isinstance(v, dict):
                merged[k] = {**merged[k], **v}
            else:
                merged[k] = v
        cfs = CfsManager(desk, pol, events, reports, snapshots=SnapshotStore(tmp_path / f"snap{len(made)}"), cfs_policy=CfsPolicyConfig.from_dict(merged))
        log: list = []
        events.subscribe(lambda ev: log.append(("notch", dict(ev.data))), types={"cfs.notch"})
        events.subscribe(lambda ev: log.append(("candidate", dict(ev.data))) if "alert" in ev.data else None, types={"cfs.candidate"})
        events.subscribe(lambda ev: log.append(("alert", dict(ev.data))), types={"cfs.alert"})
        events.subscribe(lambda ev: log.append(("policy", dict(ev.data))), types={"cfs.policy"})
        events.subscribe(lambda ev: log.append(("programme", dict(ev.data))), types={"cfs.programme_present"})
        plan = await plan_setup(desk, [1])
        await apply_setup(desk, plan)
        for ch in (1, 2):
            fake.set_value(f"/ch/{ch:02d}/mix/01/level", -20.0)
        for ch, hz in ((hpf or {1: 120.0, 2: 160.0})).items():
            fake.set_value(f"/ch/{ch:02d}/preamp/hpon", hz is not None)
            if hz is not None:
                fake.set_value(f"/ch/{ch:02d}/preamp/hpf", float(hz))
        fake.set_value("/bus/01/mix/fader", -20.0)
        fake.set_value(COLOR, color)
        desk.invalidate()
        rig = Rig(fake, conn, desk, events, cfs, reports, log)
        made.append((cfs, desk, conn, fake))
        return rig

    try:
        yield _make
    finally:
        for cfs, desk, conn, fake in made:
            await cfs.close()
            await desk.close()
            await conn.close()
            await fake.stop()


async def _armed(rig: Rig, **kw) -> dict:
    res = await rig.cfs.feedback_watch(1, **kw)
    await wait_until(lambda: rig.cfs.frames.frames_received >= 5, what="RTA frames from the fake desk")
    return res


# ---------------------------------------------------------------------------------------------- item 1: LF edge

async def test_lf_edge_follows_the_open_mics_high_pass_filters(make_rig):
    rig = await make_rig(hpf={1: 120.0, 2: 160.0})
    hpf1 = round(float(rig.fake.value("/ch/01/preamp/hpf")))   # the HPF scale is a 101-step log grid and the node prints integer Hz: 120 -> 121
    res = await _armed(rig)
    le = res["lf_edge"]
    assert le["lf_edge_hz"] == pytest.approx(0.7 * hpf1, abs=0.11) and "ch 1 HPF" in le["from"] and le["lf_feedback_possible"] is False
    assert le["window_low_hz"] == le["lf_edge_hz"] and rig.cfs._ses.det.cfg.window_low_hz == pytest.approx(0.7 * hpf1, abs=0.11)
    assert [m["ch"] for m in le["mics"]] == [1, 2] and le["mics"][1]["edge_hz"] == pytest.approx(0.7 * round(float(rig.fake.value("/ch/02/preamp/hpf"))), abs=0.11)
    rep = (await rig.cfs.stop())["report"]
    assert rep["policy"]["lf_edge"]["lf_edge_hz"] == le["lf_edge_hz"] and rep["detector"]["window_low_hz"] == le["lf_edge_hz"]
    assert "Feedback window from" in rig.reports.markdown(rep)
    # one mic with its HPF off contributes the 100 Hz default: min(0.7 x 121, 100) is still the HPF'd mic's edge
    rig.fake.set_value("/ch/02/preamp/hpon", False)
    rig.desk.invalidate()
    res = await _armed(rig)
    assert res["lf_edge"]["lf_edge_hz"] == pytest.approx(0.7 * hpf1, abs=0.11)
    await rig.cfs.stop()
    # no HPF anywhere -> 100 Hz
    rig.fake.set_value("/ch/01/preamp/hpon", False)
    rig.desk.invalidate()
    res = await _armed(rig)
    assert res["lf_edge"]["lf_edge_hz"] == 100.0 and "HPF off" in res["lf_edge"]["from"]
    await rig.cfs.stop()
    # a very low corner is floored at 60 Hz
    rig.fake.set_value("/ch/01/preamp/hpon", True)
    rig.fake.set_value("/ch/01/preamp/hpf", 40.0)
    rig.desk.invalidate()
    res = await _armed(rig)
    assert res["lf_edge"]["lf_edge_hz"] == 60.0 and res["lf_edge"]["from"].startswith("floor 60 Hz")
    await rig.cfs.stop()
    # the operator's LF declaration wins: window from the detector's 40 Hz, the HPF edge still reported
    res = await _armed(rig, lf_feedback_possible=True)
    assert res["lf_edge"]["lf_feedback_possible"] is True and res["lf_edge"]["window_low_hz"] == 40.0
    assert rig.cfs._ses.det.cfg.window_low_hz == 40.0 and rig.cfs._ses.det.cfg.lf_feedback_possible is True
    rep = (await rig.cfs.stop())["report"]
    assert rep["policy"]["lf_edge"]["lf_feedback_possible"] is True and "lf_feedback_possible declared" in rig.reports.markdown(rep)


# ---------------------------------------------------------------------------------------------- item 3: tier B

async def test_tier_b_cuts_a_loudish_moderate_line_once_then_deepens_only_on_held_verdicts(make_rig):
    """A family-less steady line arriving within a frame at -18 dBFS on a quiet bed: MODERATE (never STRONG), loud-ish ->
    exactly one -3 dB policy cut after >= 0.6 s, tagged tier B; the synthetic note drops by the cut and holds -> 'held' ->
    -6 after held_deepen_s -> 'held' -> -9 -> nothing further (notch_max); the strip is red throughout and green again at stop."""
    rig = await make_rig(policy={"tier_b": {"held_deepen_s": 1.0}})
    await _armed(rig)
    await asyncio.sleep(0.6)
    assert rig.fake.value(COLOR) == "GN"
    t_inject = time.monotonic()
    rig.rta.inject_note(1000.0, -18.0, rise_frames=1)
    ses = rig.cfs._ses
    # published as MODERATE, never cut by the detector itself
    await wait_until(lambda: any(c.klass == "MODERATE" and abs(c.freq_hz - 1015) < 40 for c in ses.det.candidates), timeout=1.5, what="MODERATE candidate")
    await wait_until(lambda: rig.notches(), timeout=3.0, what="the tier-B cut")
    dt = time.monotonic() - t_inject
    n0 = rig.notches()[0]
    assert n0["tier"] == "B" and n0["policy"] == "tier_b" and n0["band"] == GEQ_BAND_1K and n0["depth_db"] == -3.0 and "tier_b" in n0["reasons"]
    assert 0.6 <= dt <= 2.0, f"tier-B cut {dt:.2f} s after the line appeared (min_age 0.6 s + K1)"
    assert ses.detections == [], "the detector must not have emitted this line (MODERATE is published, not cut)"
    await rig.settle()
    assert float(rig.fake.value(PAR_1K)) == pytest.approx(-3.0, abs=0.01)
    assert rig.fake.value(COLOR) == "RDi", "the scribble strip shows the alert colour while the candidate is live"
    # verdict 'held' -> deepen after held_deepen_s, twice, then stop at notch_max
    await wait_until(lambda: any(n["depth_db"] == -6.0 for n in rig.notches()), timeout=4.5, what="deepen to -6 after 'held'")
    assert [n["policy"] for n in rig.notches()] == ["tier_b", "tier_b_held"]
    await wait_until(lambda: any(n["depth_db"] == -9.0 for n in rig.notches()), timeout=4.5, what="deepen to -9 after 'held'")
    await asyncio.sleep(3.2)   # another verdict + held_deepen_s pass: nothing may be written past notch_max
    assert [n["depth_db"] for n in rig.notches()] == [-3.0, -6.0, -9.0] and all(n["tier"] == "B" for n in rig.notches())
    verdicts = [v["verdict"] for v in ses.det.cut_log]
    assert verdicts[:3] == ["held", "held", "held"], verdicts
    await rig.settle()
    assert float(rig.fake.value(PAR_1K)) == pytest.approx(-9.0, abs=0.01) and rig.fake.value(COLOR) == "RDi"
    st = rig.cfs.state.to_dict()
    assert st["alert"] is True and st["candidates"] and st["candidates"][0]["cut_verdict"] == "held" and st["policy"]["strip_alert"] is True
    out = await rig.cfs.stop()
    await rig.settle()
    assert rig.fake.value(COLOR) == "GN", "a session must never leave the strip in the alert colour"
    rep = out["report"]
    tb = rep["policy"]["tier_b"]
    assert [e["depth_db"] for e in tb if e["action"] in ("tier_b", "tier_b_held")] == [-3.0, -6.0, -9.0]
    assert [e["verdict"] for e in tb if e["action"] == "verdict"] == ["held", "held", "held"]
    assert tb[-1]["action"] == "end" and "deepest" in tb[-1]["next_action"] and all(e["tier"] == "B" for e in tb)
    assert rep["notches"][0]["tiers"] == ["B"] and rep["notches"][0]["depth_db"] == -9.0
    assert [e["policy"] for e in rep["notch_log"]] == ["tier_b", "tier_b_held", "tier_b_held"]
    assert rep["policy"]["strip_color"] == {"original": "GN", "alert": "RDi", "writes": 2, "restored": True}
    assert any(a["alert"] == "on" and a["klass"] == "MODERATE" for a in rep["policy"]["alerts"])
    md = rig.reports.markdown(rep)
    assert "## Policy" in md and "Tier-B (policy) steps" in md and "| B |" in md


async def test_tier_b_false_cut_ignore_lists_the_line_and_never_cuts_it_again(make_rig):
    """The note ENDS by itself shortly after the tier-B cut: the detector files 'false_cut' (a loop that survived a cut does
    not switch itself off), the policy ignore-lists the line and never writes the band shallower; the same note coming back is
    alerted but not cut; the strip colour is restored at stop."""
    rig = await make_rig()
    await _armed(rig)
    await asyncio.sleep(0.6)
    rig.rta.inject_note(1000.0, -18.0, rise_frames=1)
    await wait_until(lambda: rig.notches(), timeout=3.0, what="the tier-B cut")
    await asyncio.sleep(1.0)                  # past the response window (a kill would be time-locked), before cut_verify_s
    rig.rta.stop_note(1000.0)
    ses = rig.cfs._ses
    await wait_until(lambda: ses.policy.ignore, timeout=2.0, what="ignore-listing after false_cut")
    assert [v["verdict"] for v in ses.det.cut_log] == ["false_cut"]
    ign = ses.policy.ignore[0]
    assert ign["band"] == GEQ_BAND_1K and "false_cut" in ign["why"]
    await rig.settle()
    assert float(rig.fake.value(PAR_1K)) == pytest.approx(-3.0, abs=0.01)
    # the same note again (once its old track is gone; a note re-struck onto the coasting FALSE_CUT track stays FALSE_CUT):
    # a new MODERATE line -> alerted, never cut again by policy (ignore-listed band)
    await asyncio.sleep(1.0)
    rig.rta.inject_note(1000.0, -18.0, rise_frames=1)
    await asyncio.sleep(2.5)
    assert len(rig.notches()) == 1
    await rig.settle()
    assert float(rig.fake.value(PAR_1K)) == pytest.approx(-3.0, abs=0.01), "the -3 dB stays: never written shallower, never deepened"
    assert rig.fake.value(COLOR) == "RDi" and rig.cfs.state.alert is True
    rep = (await rig.cfs.stop())["report"]
    await rig.settle()
    assert rig.fake.value(COLOR) == "GN"
    assert rep["policy"]["ignore"][0]["band"] == GEQ_BAND_1K and any(e["verdict"] == "false_cut" for e in rep["policy"]["tier_b"])
    assert "Ignore-listed" in rig.reports.markdown(rep)


async def test_tier_b_disabled_leaves_moderate_lines_alone(make_rig):
    rig = await make_rig(policy={"tier_b": {"enabled": False}})
    await _armed(rig)
    await asyncio.sleep(0.6)
    rig.rta.inject_note(1000.0, -18.0, rise_frames=1)
    await asyncio.sleep(2.5)
    assert rig.notches() == [] and rig.cfs.state.alert is True    # alerted, not cut
    await rig.cfs.stop()


# ---------------------------------------------------------------------------------------------- item 4: alerts / scribble strip

async def test_scribble_strip_alert_follows_the_candidates_and_is_restored_when_they_clear(make_rig):
    """A quiet MODERATE line (not tier-B material: below loud-ish and < 20 dB over its baseline) turns the strip red within a
    second and green again clear_s after it ends; alert on/off events are published once each."""
    rig = await make_rig(policy={"alerts": {"clear_s": 0.5}})
    await _armed(rig)
    await asyncio.sleep(0.6)
    t0 = time.monotonic()
    rig.rta.inject_note(2000.0, -34.0, rise_frames=1)       # ~15 dB over a -49 dBFS bed: MODERATE, quiet
    await wait_until(lambda: rig.events_of("alert"), timeout=2.0, what="strip alert write")
    assert time.monotonic() - t0 <= 1.2 and rig.events_of("alert")[0]["on"] is True and rig.events_of("alert")[0]["color"] == "RDi"
    await rig.settle()
    assert rig.fake.value(COLOR) == "RDi"
    ons = [e for e in rig.events_of("candidate") if e["alert"] == "on"]
    assert len(ons) == 1 and ons[0]["klass"] == "MODERATE" and ons[0]["alert_class"] == "MODERATE" and abs(ons[0]["freq_hz"] - 2030) < 60
    assert rig.notches() == []
    rig.rta.stop_note(2000.0)
    await wait_until(lambda: any(e["on"] is False for e in rig.events_of("alert")), timeout=3.0, what="strip colour restored after clear_s")
    await rig.settle()
    assert rig.fake.value(COLOR) == "GN"
    offs = [e for e in rig.events_of("candidate") if e["alert"] == "off"]
    assert len(offs) == 1 and rig.cfs.state.alert is False and rig.cfs.state.candidates == []
    rep = (await rig.cfs.stop())["report"]
    assert [a["alert"] for a in rep["policy"]["alerts"]] == ["on", "off"] and rep["policy"]["strip_color"]["restored"] is True
    assert rep["notches"] == []


async def test_scribble_strip_is_restored_when_a_panic_aborts_the_watch(make_rig):
    rig = await make_rig()
    await _armed(rig)
    await asyncio.sleep(0.6)
    rig.rta.inject_note(2000.0, -34.0, rise_frames=1)
    await wait_until(lambda: rig.events_of("alert"), timeout=2.0, what="strip alert write")
    await rig.settle()
    assert rig.fake.value(COLOR) == "RDi"
    await rig.desk.panic()                                   # the operator's emergency stop aborts the session
    await wait_until(lambda: rig.cfs.state.mode is CfsMode.IDLE, timeout=5.0, what="watch stopped by the panic")
    await rig.settle()
    assert rig.fake.value(COLOR) == "GN", "the abort path must restore the strip colour"
    rep = rig.cfs._last.report
    assert rep["aborted"] and "panic" in rep["abort_reason"] and rep["policy"]["strip_color"]["restored"] is True


async def test_scribble_strip_is_restored_when_the_operator_takes_over_a_ring_out(make_rig):
    rig = await make_rig(policy={"tier_b": {"enabled": False}})
    rig.fake.set_value("/bus/01/mix/fader", -30.0)
    rig.desk.invalidate()
    run = asyncio.create_task(rig.cfs.ring_out(1, target_gain_db=-10.0, step_db=1.0, dwell_ms=400))
    await wait_until(lambda: rig.cfs.mode is CfsMode.RINGOUT and rig.cfs.frames.frames_received >= 5, timeout=5.0, what="ring-out armed")
    rig.rta.inject_note(2000.0, -34.0, rise_frames=1)
    await wait_until(lambda: rig.events_of("alert"), timeout=3.0, what="strip alert write")
    await rig.settle()
    assert rig.fake.value(COLOR) == "RDi"
    rig.fake.set_value("/bus/01/mix/fader", -60.0)         # the engineer yanks the fader: hands off, ABORT
    rep = await asyncio.wait_for(run, timeout=15.0)
    assert rep["final_stage"] == "ABORT" and "moved on the desk" in rep["abort_reason"]
    await rig.settle()
    assert rig.fake.value(COLOR) == "GN" and rep["policy"]["strip_color"]["restored"] is True


async def test_colour_the_engineer_sets_mid_session_is_the_one_restored(make_rig):
    rig = await make_rig(policy={"alerts": {"clear_s": 0.3}})
    await _armed(rig)
    await asyncio.sleep(0.6)
    rig.rta.inject_note(2000.0, -34.0, rise_frames=1)
    await wait_until(lambda: rig.events_of("alert"), timeout=2.0, what="strip alert write")
    rig.fake.set_value(COLOR, "BL")                          # a front-panel change pushed over /xremote
    await asyncio.sleep(0.2)
    rig.rta.stop_note(2000.0)
    await rig.cfs.stop()
    await rig.settle()
    assert rig.fake.value(COLOR) == "BL"


async def test_strip_colour_that_could_not_be_restored_is_paid_back_on_reconnect_or_adopted_by_the_next_session(make_rig, monkeypatch):
    """The desk does not take the colour restore at stop (all three attempts fail): the report says so, the manager remembers
    the owed colour and writes it the moment the connection reports 'connected' again; if instead a new session arms on that
    strip first, it adopts the owed colour as the one to restore (it would otherwise read our RDi as the engineer's colour)."""
    from x32mcp.desk import DeskError

    rig = await make_rig(policy={"alerts": {"clear_s": 0.3}})
    # (1) failed restore -> paid on reconnect
    await _armed(rig)
    await asyncio.sleep(0.6)
    rig.rta.inject_note(2000.0, -34.0, rise_frames=1)
    await wait_until(lambda: rig.events_of("alert"), timeout=2.0, what="strip alert write")
    await rig.settle()
    assert rig.fake.value(COLOR) == "RDi"
    real_label = rig.desk.label

    async def dead_label(*a, **k):
        raise DeskError("NOT_CONNECTED", "test: desk unreachable")
    monkeypatch.setattr(rig.desk, "label", dead_label)
    rep = (await rig.cfs.stop())["report"]
    rig.rta.stop_note(2000.0)
    assert rep["policy"]["strip_color"]["restored"] is False and any("could not restore" in w for w in rep["preflight"]["warnings"])
    await rig.settle()
    assert rig.fake.value(COLOR) == "RDi" and rig.cfs._color_owed is not None
    monkeypatch.setattr(rig.desk, "label", real_label)
    rig.events.publish("connection.state", state="connected", host="x", port=1, console="X32-FAKE", error=None)
    await wait_until(lambda: any(e.get("late") for e in rig.events_of("alert")), timeout=3.0, what="late colour restore on reconnect")
    await rig.settle()
    assert rig.fake.value(COLOR) == "GN" and rig.cfs._color_owed is None
    assert rig.cfs._last.report["policy"]["strip_color"]["restored"] == "late"
    # (2) failed restore -> the next session on the strip adopts the owed colour and puts it back on its first alerts pass
    await asyncio.sleep(0.8)
    await _armed(rig)
    await asyncio.sleep(0.6)
    rig.rta.inject_note(2000.0, -34.0, rise_frames=1)
    await wait_until(lambda: rig.cfs.state.alert and rig.cfs._ses.policy.color_is_alert, timeout=2.0, what="strip alert")
    monkeypatch.setattr(rig.desk, "label", dead_label)
    await rig.cfs.stop()
    rig.rta.stop_note(2000.0)
    monkeypatch.setattr(rig.desk, "label", real_label)
    await rig.settle()
    assert rig.fake.value(COLOR) == "RDi" and rig.cfs._color_owed is not None
    await asyncio.sleep(0.8)
    res = await _armed(rig)
    ses = rig.cfs._ses
    assert ses.policy.color_orig == "GN" and rig.cfs._color_owed is None, (ses.policy.color_orig, res)
    await wait_until(lambda: rig.fake.value(COLOR) == "GN", timeout=3.0, what="owed colour written by the new session")
    rep = (await rig.cfs.stop())["report"]
    await rig.settle()
    assert rig.fake.value(COLOR) == "GN" and rep["policy"]["strip_color"]["original"] == "GN"


# ---------------------------------------------------------------------------------------------- item 5: AT-ARM in watch

async def test_a_quiet_at_arm_line_is_alerted_not_cut_in_watch(make_rig):
    """A -36 dBFS steady family-less line already sounding when the watch arms (a projector whine and an established quiet
    howl look the same): the detector emits it as established_at_arm after arm_confirm_s, the policy does NOT cut it (neither
    LOUD nor >= 30 dB prominent) and alerts instead."""
    rig = await make_rig(-50.0, -62.0)                      # bed ~-56 dBFS at 1 kHz: the line is ~20 dB prominent
    rig.rta.inject_note(1000.0, -36.0, rise_frames=1)
    await asyncio.sleep(0.5)                                 # sounding before we arm
    await _armed(rig)
    ses = rig.cfs._ses
    await wait_until(lambda: ses.policy.at_arm_log, timeout=3.0, what="the at-arm line recorded as not cut")
    await asyncio.sleep(1.5)
    assert rig.notches() == [] and rig.rta.cuts == {}
    rec = ses.policy.at_arm_log[0]
    assert abs(rec["freq_hz"] - 1015) < 40 and rec["level_db"] == pytest.approx(-36.0, abs=1.0) and rec["prominence_db"] < 30
    assert any(d.get("suppressed") == "at_arm" and "established_at_arm" in d["reasons"] for d in ses.detections)
    assert any(e["alert"] == "on" and e["alert_class"] == "AT_ARM" for e in rig.events_of("candidate"))
    await rig.settle()
    assert rig.fake.value(COLOR) == "RDi"
    rep = (await rig.cfs.stop())["report"]
    assert rep["notches"] == [] and rep["policy"]["at_arm_suppressed"][0]["freq_hz"] == rec["freq_hz"]
    assert "At-arm lines NOT cut" in rig.reports.markdown(rep)
    await rig.settle()
    assert rig.fake.value(COLOR) == "GN"


async def test_a_loud_prominent_at_arm_line_is_cut_within_k1(make_rig):
    """The M7 case: a -8 dBFS, > 30 dB prominent steady line sounding at arm is a howl at its limiter -- cut at K1."""
    rig = await make_rig(-38.0, -50.0)                      # bed ~-44 at 1 kHz (skirts 26 dB clear of it: a live, jittering line)
    rig.rta.inject_note(1000.0, -8.0, rise_frames=1)
    await asyncio.sleep(0.5)
    t0 = time.monotonic()
    await rig.cfs.feedback_watch(1)
    t_armed = time.monotonic()
    await wait_until(lambda: rig.notches(), timeout=2.0, what="the at-arm howl cut")
    t_cut = time.monotonic()
    n = rig.notches()[0]
    assert n["tier"] == "A" and n["band"] == GEQ_BAND_1K and n["depth_db"] == -3.0
    assert "established_at_arm" in n["reasons"] or "loud" in n["reasons"]
    assert t_cut - t_armed <= 0.7, f"cut {t_cut - t_armed:.2f} s after arming ({t_cut - t0:.2f} s incl. arming)"
    ses = rig.cfs._ses
    assert ses.policy.at_arm_log == []
    await rig.cfs.stop()


# ---------------------------------------------------------------------------------------------- item 2: ring-out contract check

async def _strum(rta: SyntheticRta, stop: asyncio.Event, level_db: float = -22.0) -> None:
    """Programme: a 5-note (non-harmonic) chord struck every 0.5 s and released after 0.3 s -- broadband transients and note
    events, never held long enough to be tier-B material."""
    chord = (523.0, 660.0, 1175.0, 1480.0, 2350.0)
    try:
        while not stop.is_set():
            for f in chord:
                rta.inject_note(f, level_db, rise_frames=1)
            await asyncio.sleep(0.3)
            for f in chord:
                rta.stop_note(f)
            await asyncio.sleep(0.2)
    finally:
        for f in chord:
            rta.stop_note(f)


async def test_programme_during_a_ring_out_is_reported_and_the_run_continues(make_rig):
    rig = await make_rig()
    rig.fake.set_value("/bus/01/mix/fader", -30.0)
    rig.desk.invalidate()
    stop = asyncio.Event()
    run = asyncio.create_task(rig.cfs.ring_out(1, target_gain_db=-25.0, step_db=1.0, dwell_ms=500))
    await wait_until(lambda: rig.cfs.mode is CfsMode.RINGOUT and rig.cfs.frames.frames_received >= 3, timeout=5.0, what="ring-out armed")
    ses = rig.cfs._ses
    assert ses.det.cfg.ringout_emit_moderate is False
    strummer = asyncio.create_task(_strum(rig.rta, stop))
    try:
        await wait_until(lambda: rig.events_of("programme"), timeout=6.0, what="cfs.programme_present")
        ev = rig.events_of("programme")[0]
        assert ev["mode"] == "ringout" and ev["bus"] == 1
        rep = await asyncio.wait_for(run, timeout=30.0)
    finally:
        stop.set()
        await strummer
    assert rep["final_stage"] == "DONE" and rep["aborted"] is False, rep.get("abort_reason")
    pp = rep["policy"]["programme_present"]
    assert pp["detected"] is True and pp["first_seen_s"] is not None
    assert any("programme detected on Bus 1 during the ring-out" in w and "MODERATE lines are not cut" in w for w in rep["preflight"]["warnings"])
    assert not any("confirmed_K2" in d.get("reasons", []) for d in rep["detection_log"]), "no MODERATE-after-K2 emission under programme"
    assert all(n.get("policy") != "tier_b" for n in rep["notch_log"]), "0.3 s chord notes are never tier-B material"
    assert "Programme detected" in rig.reports.markdown(rep)


async def test_watch_armed_in_silence_refreshes_the_level_reference_when_programme_starts(make_rig):
    rig = await make_rig(policy={"programme_check_s": 2.0})
    await _armed(rig)
    ses = rig.cfs._ses
    await wait_until(lambda: ses.policy.armed_in_silence is True, timeout=4.0, what="armed-in-silence decided")
    await asyncio.sleep(0.3)                                  # past the detector's 2 s arm reference window
    p95_silent = ses.det.arm_p95_db
    stop = asyncio.Event()
    strummer = asyncio.create_task(_strum(rig.rta, stop))
    try:
        await wait_until(lambda: ses.policy.arm_ref_refreshed_s is not None, timeout=6.0, what="arm reference refreshed on programme")
        await asyncio.sleep(2.3)                             # the re-opened 2 s window closes on the programme
    finally:
        stop.set()
        await strummer
    assert ses.policy.programme_present and ses.det.arm_p95_db is not None and ses.det.arm_p95_db > p95_silent + 3.0
    rep = (await rig.cfs.stop())["report"]
    assert rep["policy"]["programme_present"]["armed_in_silence"] is True and rep["policy"]["programme_present"]["arm_reference_refreshed_s"] is not None


# ---------------------------------------------------------------------------------------------- item 6: flags

async def test_a_frozen_display_reforces_the_ballistics_then_aborts(make_rig):
    """The fake keeps sending its last analyser frame when the synthetic source stops ticking: every band bit-identical =
    what a peak-held display looks like. The policy re-forces decay / peak-hold once, and aborts when it stays frozen."""
    rig = await make_rig(policy={"frozen_abort_s": 1.5})
    await _armed(rig)
    await asyncio.sleep(1.0)
    rig.fake.set_value("/-prefs/rta/peakhold", 3)           # somebody touched the prefs on the console ...
    await rig.rta.stop()                                     # ... and the display freezes (identical frames from now on)
    ses = rig.cfs._ses
    await wait_until(lambda: ses.policy.ballistics_reforced is not None and not ses.policy.ballistics_reforced.get("pending"),
                     timeout=4.0, what="ballistics re-forced on PEAK_HOLD_SUSPECTED (background task finished)")
    await rig.settle()
    assert int(rig.fake.get("/-prefs/rta/peakhold")) == 0 and float(rig.fake.get("/-prefs/rta/decay")) == 0.0
    assert any(e.get("what") == "ballistics_reforced" for e in rig.events_of("policy"))
    await wait_until(lambda: rig.cfs.state.mode is CfsMode.IDLE, timeout=6.0, what="abort on a display that stays frozen")
    rep = rig.cfs._last.report
    assert rep["aborted"] and "frozen" in rep["abort_reason"]
    flags = {f["flag"] for f in rep["policy"]["flags_seen"]}
    assert flags & {"PEAK_HOLD_SUSPECTED", "FROZEN_LINES"} and rep["policy"]["ballistics_reforced"]["result"]["written"] == ["decay", "peakhold"]
    assert any("looked frozen" in w for w in rep["preflight"]["warnings"])


async def test_hot_display_is_reported(make_rig):
    rig = await make_rig(-25.0, -55.0)                      # the shared fixture's default bed: LF bands at -15 dBFS -> HOT_SPECTRUM
    await _armed(rig)
    ses = rig.cfs._ses
    await wait_until(lambda: "HOT_SPECTRUM" in ses.policy.flags_seen, timeout=3.0, what="HOT_SPECTRUM seen")
    rep = (await rig.cfs.stop())["report"]
    assert any("display is hot" in w for w in rep["preflight"]["warnings"]) and any(f["flag"] == "HOT_SPECTRUM" for f in rep["policy"]["flags_seen"])


# ---------------------------------------------------------------------------------------------- item 6: back-off probe (ring_out)

class RoomSourceRta(SyntheticRta):
    """A synthetic analyser behind an open mic in a room driven by the PA: everything it shows (the bed and the injected note, a
    SOURCE IN THE ROOM) follows the bus master dB for dB (``howl=False``) -- or the note is a compressor-held howl that follows the
    steps too but DIES once the master comes ``howl_margin_db`` below its last plateau (``howl=True``). The master is read from the
    fake desk on every frame."""

    def __init__(self, *a, master: Any = None, howl: bool = False, howl_margin_db: float = 2.0, **kw) -> None:
        super().__init__(*a, **kw)
        self._master = master
        self._ref: float | None = None      # master at the first frame (levels are given at that master)
        self._peak: float | None = None     # highest master seen: the howl's last plateau
        self._base0 = self._base
        self._offset = 0.0
        self.howl = howl
        self.howl_margin_db = howl_margin_db
        self.dead = False

    def tick(self):
        m = self._master() if self._master else None
        if isinstance(m, (int, float)):
            m = float(m)
            if self._ref is None:
                self._ref = m
            self._peak = m if self._peak is None else max(self._peak, m)
            self._offset = m - self._ref
            if self.howl and m <= self._peak - self.howl_margin_db:
                self.dead = True            # < howl_margin_db of excess over its last plateau: the back-off kills it for good
            self._base = tuple(b + self._offset for b in self._base0)   # the room through the mic rises with the PA
        return super().tick()

    def _note_level(self, n) -> float:
        if self.howl and self.dead:
            return -128.0
        return super()._note_level(n) + self._offset


@pytest.mark.parametrize("howl", [False, True])
async def test_backoff_probe_separates_a_room_source_from_a_compressor_held_howl(descriptor, make_rig, tmp_path, howl):
    """A >= 30 dB-prominent family-less line that FOLLOWS the ring-out's +1 dB steps is STATIONARY with 'backoff_advised': the
    policy lowers the master 3 dB for a dwell. A room source drops 3 dB (left alone, level restored, run continues); a howl held
    by a compressor dies (cut where it stood, reason backoff_probe, tier B)."""
    rig = await make_rig(policy={"tier_b": {"enabled": True}})
    master = lambda: rig.fake.value("/bus/01/mix/fader")  # noqa: E731
    src = RoomSourceRta(seed=5, band_hz=rta_band_hz(descriptor), level_100hz_db=-55.0, level_10khz_db=-65.0, noise_db=2.0, wobble_db=0.5,
                        master=master, howl=howl)
    # open loop on purpose (frames injected, the fake's GEQ never reaches this analyser): only the master coupling matters here
    cfs = CfsManager(rig.desk, rig.cfs._policy, rig.events, rig.reports, frames=src, snapshots=None, cfs_policy=CfsPolicyConfig())
    try:
        rig.fake.set_value("/bus/01/mix/fader", -30.0)
        rig.desk.invalidate()
        src.inject_note(1000.0, -27.0, rise_frames=1)       # sounding before arm: born at arm, ~33 dB prominent
        for _ in range(10):
            src.tick()
        rep = await asyncio.wait_for(cfs.ring_out(1, target_gain_db=-24.0, step_db=1.0, dwell_ms=700), timeout=40.0)
    finally:
        await cfs.close()
    probes = rep["policy"]["backoff_probes"]
    assert probes, ("no back-off probe ran", rep["detection_log"][-3:], rep["stages"][-6:])
    p = probes[0]
    assert abs(p["freq_hz"] - 1015) < 40 and p["master_probe_db"] == pytest.approx(p["master_from_db"] - 3.0, abs=0.15)
    stages = [s["stage"] for s in rep["stages"]]
    assert "PROBE" in stages
    if not howl:
        assert p["verdict"] == "stationary" and p["died"] is False and p["drop_db"] == pytest.approx(3.0, abs=1.2)
        assert p["master_restored_db"] == pytest.approx(p["master_from_db"], abs=0.15)
        assert rep["notches"] == [] and rep["final_stage"] == "DONE" and rep["end_master_db"] == pytest.approx(-27.0, abs=0.15)
    else:
        assert p["verdict"] == "feedback" and p["died"] is True
        assert rep["notches"] and rep["notch_log"][0]["policy"] == "backoff_probe" and rep["notch_log"][0]["tier"] == "B"
        assert rep["notches"][0]["band"] == GEQ_BAND_1K
        assert rep["final_stage"] == "DONE"


# ---------------------------------------------------------------------------------------------- the server tools carry it

async def test_server_tools_plumb_lf_feedback_possible_and_expose_the_alert_list(fakedesk, tmp_path):
    """feedback_watch / ring_out take lf_feedback_possible (bound into the ring_out token payload), cfs_status carries the live
    alert list, and the trimmed report envelope keeps the policy summary."""
    from x32mcp import server as srv
    from test_server_tools import assert_err, assert_pending, fset, make_app, settle

    a = await make_app(fakedesk, tmp_path)
    try:
        token = assert_pending(await srv.setup_ringout_eqs([1]))
        assert (await srv.setup_ringout_eqs([1], confirm_token=token))["ok"]
        for ch in (1, 2):
            fset(a, fakedesk, f"/ch/{ch:02d}/mix/01/level", -20.0)
        fset(a, fakedesk, "/ch/01/preamp/hpon", True)
        fset(a, fakedesk, "/ch/01/preamp/hpf", 120.0)
        fset(a, fakedesk, "/bus/01/mix/fader", -20.0)
        fw = await srv.feedback_watch(1, notch_budget=4, lf_feedback_possible=True)
        assert fw["ok"] and fw["lf_edge"]["lf_feedback_possible"] is True and fw["lf_edge"]["window_low_hz"] == 40.0
        assert fw["lf_edge"]["lf_edge_hz"] == pytest.approx(84.7, abs=0.11) and "feedback window from 40 Hz" in fw["summary"]
        assert fw["summary"].endswith("budget 4 notch(es)")
        st = await srv.cfs_status()
        assert st["ok"] and st["candidates"] == [] and st["alert"] is False and st["policy"]["window_low_hz"] == 40.0 and st["policy"]["lf_edge_hz"] == pytest.approx(84.7, abs=0.11)
        stop = await srv.feedback_watch_stop()
        assert stop["ok"] and stop["report"]["policy"]["lf_edge"]["lf_feedback_possible"] is True
        assert "alerts_entries" in stop["report"]["policy"] and "config_entries" in stop["report"]["policy"]   # trimmed in the envelope
        assert (await srv.get_ringout_report(stop["session_id"]))["policy"]["config"]["tier_b"]["enabled"] is True   # kept on disk
        fw = await srv.feedback_watch(1)
        assert fw["ok"] and fw["lf_edge"]["window_low_hz"] == pytest.approx(84.7, abs=0.11) and "ch 1 HPF 121 Hz" in fw["summary"]
        await srv.feedback_watch_stop()
        # the ring_out payload binds the declaration: a token minted without it does not run a request with it
        pend = await srv.ring_out(1, target_gain_db=-19.0, dwell_ms=10)
        token = assert_pending(pend)
        assert_err(await srv.ring_out(1, target_gain_db=-19.0, dwell_ms=10, lf_feedback_possible=True, confirm_token=token), "BAD_TOKEN")
        pend = await srv.ring_out(1, target_gain_db=-19.0, dwell_ms=10, lf_feedback_possible=True)
        token = assert_pending(pend)
        assert "LF feedback declared possible" in pend["action_summary"]
        rep = await srv.ring_out(1, target_gain_db=-19.0, dwell_ms=10, lf_feedback_possible=True, confirm_token=token)
        assert rep["ok"] and rep["final_stage"] == "DONE" and rep["policy"]["lf_edge"]["window_low_hz"] == 40.0
        await settle(a)
    finally:
        await a.close()
        srv.app = None
