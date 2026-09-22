"""Drive a CFS² feedback_watch session end to end against the in-process FakeDesk and print what an operator-facing consumer
sees: cfs.notch events, the fake analyser's ring / note levels, and the session report's "detector" block (flags, release rate,
arm p95, post-cut verdicts). Path: CfsManager -> Desk -> X32Connection -> OSC/UDP -> FakeDesk -> SyntheticRta -> /meters/15 ->
FeedbackDetector -> NotchController -> GEQ write -> FakeDesk applies the cut -> detector.note_cut() verdict.

    .venv/bin/python scripts/verify_watch_fakedesk.py            # from the repo root

Works in sandboxes without IP sockets too: if binding 127.0.0.1 is forbidden and tests/conftest.py (the in-memory UDP loopback used
by the integration suite) is present, it is loaded first.
Scenario A: a 20 dB/s ring at 2400 Hz -> one -3 dB cut within ~1 s, ring collapses, verdict 'confirmed'.
Scenario B: a steady family-less line arriving at -6 dBFS and HOLDING (to the tap a limiter-held howl and a ff whistle are the same
thing): cut at K1 (LOUD / FAST-RISE), the line drops by exactly the bell and holds -> 'held' -> one deepen per verdict -> -6 -> -9 ->
stops at notch_max (bounded).
Scenario C (the policy layer, docs/CFS_POLICY.md; a second session on a QUIET fake desk so that -18 dBFS is loud-ish but not LOUD): a
steady family-less line arriving within one frame at -18 dBFS -- a howl slamming into a quiet limiter plateau looks exactly like this
and the detector publishes it as MODERATE without cutting it. Tier B cuts it ONCE (-3 dB, tagged tier "B"), the detector files 'held',
the policy alerts (bus scribble strip -> RDi) and deepens at held_deepen_s intervals to -6 / -9, then stops; when the note ends the strip
colour is restored. The policy log (report["policy"]["tier_b"]) is printed.
Scenario D (same quiet desk): the held line is MASKED by a loud passage for 0.6 s (the detector drops its track) -- the engagement must
end 'lost', NOT ignore-listed, and the re-acquired line is engaged again (-6); then the engineer RELEASES the band by hand on the desk:
the engagement ends 'operator', the band is ignore-listed for policy cuts and never re-cut; strip restored at stop.
Scenario E (ring_out, a room whose bed and a 33 dB-prominent room-source line follow the master): the STATIONARY line triggers the
back-off probe; a 25 dB/s ring takes off 0.3 s into the probe's dip -> the probe yields (verdict 'interrupted', master left at the
probe level, re-queued once) and the ring is cut within a frame or two of its detection; the run completes."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
_shim_path = ROOT / "tests" / "conftest.py"
if _shim_path.exists():
    _spec = importlib.util.spec_from_file_location("udp_shim", _shim_path)
    _shim = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_shim)                      # installs the in-memory UDP loopback only when AF_INET binds are forbidden
    print("in-memory UDP shim active:", getattr(_shim, "MEM_UDP_ACTIVE", False))

from x32mcp.cfs import CfsManager, ReportStore  # noqa: E402
from x32mcp.cfs_policy import CfsPolicyConfig  # noqa: E402
from x32mcp.connection import X32Connection  # noqa: E402
from x32mcp.descriptor import Descriptor  # noqa: E402
from x32mcp.desk import Desk  # noqa: E402
from x32mcp.events import EventBus  # noqa: E402
from x32mcp.fakedesk import FakeDesk  # noqa: E402
from x32mcp.meters import SyntheticRta, rta_band_hz  # noqa: E402
from x32mcp.nodes import SnapshotStore  # noqa: E402
from x32mcp.policy import Policy  # noqa: E402
from x32mcp.provision import apply_setup, plan_setup  # noqa: E402

CONN_OPTS = dict(timeout_s=0.25, retries=1, heartbeat_s=0.5, watchdog_s=1.0, backoff_s=(0.1, 0.2))
GEQ_PAR_1K = "/fx/5/par/18"      # GEQ2 in FX5, side A, band 18 = 1 kHz (what setup_ringout_eqs provisions for bus 1)


class RoomSourceRta(SyntheticRta):
    """A synthetic analyser behind an open mic in a room driven by the PA: the bed and the injected notes (SOURCES IN THE ROOM)
    follow the bus master dB for dB; injected rings do not (they stand for a loop taking off on its own)."""

    def __init__(self, *a, master=None, **kw) -> None:
        super().__init__(*a, **kw)
        self._master = master
        self._ref = None
        self._base0 = self._base
        self._offset = 0.0

    def tick(self):
        m = self._master() if self._master else None
        if isinstance(m, (int, float)):
            if self._ref is None:
                self._ref = float(m)
            self._offset = float(m) - self._ref
            self._base = tuple(b + self._offset for b in self._base0)
        return super().tick()

    def _note_level(self, n) -> float:
        return super()._note_level(n) + self._offset


async def _until(pred, timeout: float, step: float = 0.02) -> bool:
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end:
        if pred():
            return True
        await asyncio.sleep(step)
    return bool(pred())


async def main() -> None:
    desc = Descriptor.load()
    fake = FakeDesk(desc, host="127.0.0.1", port=0, name="X32-FAKE")
    await fake.start()
    tmp = Path(tempfile.mkdtemp())
    events = EventBus()
    conn = X32Connection(desc, events, **CONN_OPTS)
    await conn.connect(fake.host, fake.port)
    policy = Policy(desc, events)
    desk = Desk(desc, conn, policy, events, SnapshotStore(tmp / "snapshots"))
    reports = ReportStore(tmp / "reports")
    cfs = CfsManager(desk, policy, events, reports, snapshots=SnapshotStore(tmp / "snapshots"))
    t0 = time.monotonic()
    notches: list[tuple] = []
    events.subscribe(lambda ev: notches.append((round(time.monotonic() - t0, 2), ev.data["band"], ev.data["freq_hz"], ev.data["depth_db"])),
                     types={"cfs.notch"})
    plan = await plan_setup(desk, [1])
    await apply_setup(desk, plan)
    for ch in (1, 2):
        fake.set_value(f"/ch/{ch:02d}/mix/01/level", -20.0)
    fake.set_value("/bus/01/mix/fader", -20.0)
    desk.invalidate()
    res = await cfs.feedback_watch(1)
    print("ARMED:", res["session_id"], "preflight ok", res["preflight"]["ok"], "rta verified", res["rta"]["verified"], "geq", res["geq"])
    while cfs.frames.frames_received < 10:
        await asyncio.sleep(0.02)

    print("\n== A: ring 2400 Hz, 20 dB/s from -34 dBFS")
    fake.rta.inject_ring(2400.0, 20.0, start_db=-34.0)
    await asyncio.sleep(2.5)
    print(f"  t+{time.monotonic() - t0:5.2f}s notches (t, GEQ band, Hz, dB): {notches}")
    print(f"  ring level now {fake.rta.ring_level(2400.0):.1f} dBFS; fake GEQ cuts by RTA band: {fake.rta.cuts}")

    print("\n== B: steady family-less line arriving at -6 dBFS at 1000 Hz and held for 6.5 s")
    fake.rta.inject_note(1000.0, -6.0)
    await asyncio.sleep(6.5)
    print(f"  t+{time.monotonic() - t0:5.2f}s notches: {notches}")
    print(f"  note {fake.rta.notes}; fake GEQ cuts by RTA band: {fake.rta.cuts}")
    fake.rta.stop_note(1000.0)
    await asyncio.sleep(1.0)

    out = await cfs.stop()
    rep = out["report"]
    print("\n== session report notches:", [(n["band"], n["freq_hz"], n["depth_db"], n.get("tiers")) for n in rep["notches"]])
    print("== report['detector']:", json.dumps({k: v for k, v in rep["detector"].items() if k != "cut_verdicts"}))
    for v in rep["detector"]["cut_verdicts"]:
        print("   verdict", v)
    print("== detection_log:", [(dd["freq_hz"], dd["reasons"][-2:]) for dd in rep["detection_log"]])
    pol = rep.get("policy") or {}
    print("== report['policy']: lf_edge", json.dumps(pol.get("lf_edge")), "| flags", [f["flag"] for f in pol.get("flags_seen", [])],
          "| warnings:", rep["preflight"]["warnings"])
    await cfs.close()
    await desk.close()
    await conn.close()
    await fake.stop()

    # ------------------------------------------------------------------------------------------------------------------
    print("\n== C: policy layer on a quiet desk (bed ~-50 dBFS at 1 kHz): tier B on a quiet held line + desk alerts")
    quiet = SyntheticRta(seed=3, band_hz=rta_band_hz(desc), level_100hz_db=-45.0, level_10khz_db=-55.0, noise_db=2.0, wobble_db=1.0)
    fake = FakeDesk(desc, host="127.0.0.1", port=0, name="X32-FAKE", rta=quiet)
    await fake.start()
    events = EventBus()
    conn = X32Connection(desc, events, **CONN_OPTS)
    await conn.connect(fake.host, fake.port)
    policy = Policy(desc, events)
    desk = Desk(desc, conn, policy, events, SnapshotStore(tmp / "snapshots2"))
    reports = ReportStore(tmp / "reports2")
    pcfg = CfsPolicyConfig.from_dict({**desc.cfs_policy, "tier_b": {**desc.cfs_policy.get("tier_b", {}), "held_deepen_s": 1.5}})
    cfs = CfsManager(desk, policy, events, reports, snapshots=SnapshotStore(tmp / "snapshots2"), cfs_policy=pcfg)
    t0 = time.monotonic()
    plog: list[str] = []

    def _t() -> str:
        return f"{time.monotonic() - t0:5.2f}s"

    events.subscribe(lambda ev: plog.append(f"  {_t()} cfs.notch      band {ev.data['band']} {ev.data['freq_hz']:g} Hz -> {ev.data['depth_db']:+.0f} dB "
                                             f"tier {ev.data.get('tier')} {ev.data.get('policy') or ''}"), types={"cfs.notch"})
    events.subscribe(lambda ev: plog.append(f"  {_t()} cfs.candidate  alert {ev.data['alert'].upper()} {ev.data.get('alert_class')} {ev.data.get('freq_hz')} Hz "
                                             f"{ev.data.get('level_db')} dBFS verdict={ev.data.get('cut_verdict')}") if "alert" in ev.data else None,
                     types={"cfs.candidate"})
    events.subscribe(lambda ev: plog.append(f"  {_t()} cfs.alert      strip {'-> ' + str(ev.data.get('color')) if ev.data.get('on') else 'restored to ' + str(ev.data.get('color'))}"),
                     types={"cfs.alert"})
    events.subscribe(lambda ev: plog.append(f"  {_t()} cfs.policy     {ev.data.get('what')} " + json.dumps({k: v for k, v in ev.data.items()
                                             if k in ("action", "verdict", "next_action", "depth_db", "flag", "why", "text")})), types={"cfs.policy"})
    plan = await plan_setup(desk, [1])
    await apply_setup(desk, plan)
    for ch in (1, 2):
        fake.set_value(f"/ch/{ch:02d}/mix/01/level", -20.0)
    fake.set_value("/ch/01/preamp/hpon", True)
    fake.set_value("/ch/01/preamp/hpf", 120.0)
    fake.set_value("/ch/02/preamp/hpon", True)
    fake.set_value("/ch/02/preamp/hpf", 160.0)
    fake.set_value("/bus/01/mix/fader", -20.0)
    fake.set_value("/bus/01/config/color", "GN")
    desk.invalidate()
    res = await cfs.feedback_watch(1)
    print("ARMED:", res["session_id"], "| lf_edge:", json.dumps({k: res["lf_edge"][k] for k in ("lf_edge_hz", "from", "window_low_hz")}),
          "| strip colour at arm:", fake.value("/bus/01/config/color"))
    while cfs.frames.frames_received < 10:
        await asyncio.sleep(0.02)
    await asyncio.sleep(1.0)
    print(f"  {_t()} inject: steady family-less line at 1000 Hz, -18 dBFS, arriving within one frame")
    fake.rta.inject_note(1000.0, -18.0, rise_frames=1)
    for _ in range(11):
        await asyncio.sleep(1.0)
        ses = cfs._ses
        cands = [(round(c.freq_hz), c.klass, round(c.level_db, 1), c.cut_verdict) for c in (ses.det.candidates if ses else []) if c.klass != "TRACK"]
        print(f"  {_t()} strip={fake.value('/bus/01/config/color')} fake GEQ cuts={ {k: round(v, 1) for k, v in fake.rta.cuts.items()} } candidates={cands}")
    print(f"  {_t()} note ends")
    fake.rta.stop_note(1000.0)
    await asyncio.sleep(3.2)
    await conn.get("/-stat/selidx")
    print(f"  {_t()} strip={fake.value('/bus/01/config/color')} (restored once no candidate has been live for alerts.clear_s)")
    out = await cfs.stop()
    rep = out["report"]
    await conn.get("/-stat/selidx")
    print("== policy log (events):")
    print("\n".join(plog))
    print("== report['policy']['tier_b']:")
    for e in rep["policy"]["tier_b"]:
        print("   ", {k: e[k] for k in ("t", "freq_hz", "band", "depth_db", "action", "verdict", "next_action")})
    print("== report notches:", [(n["band"], n["freq_hz"], n["depth_db"], n.get("tiers")) for n in rep["notches"]],
          "| strip_color:", rep["policy"]["strip_color"], "| detector detections:", rep["detections"], "| strip now:", fake.value("/bus/01/config/color"))
    print("== markdown Policy section:")
    md = ReportStore.markdown(rep)
    print(md[md.index("## Policy"):].split("## Stages")[0].rstrip())

    # ------------------------------------------------------------------------------------------------------------------
    print("\n== D: the held line is MASKED by a loud passage (track dropped), then the engineer RELEASES the band by hand")
    fake.rta.stop_note(1000.0)
    fake.set_value(GEQ_PAR_1K, 0.0)                      # start from a flat band again
    desk.invalidate()
    await asyncio.sleep(1.0)
    plog.clear()
    t0 = time.monotonic()
    res = await cfs.feedback_watch(1)
    ses = cfs._ses
    while cfs.frames.frames_received < 10:
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.8)
    print(f"  {_t()} inject: steady family-less line at 1000 Hz, -18 dBFS")
    fake.rta.inject_note(1000.0, -18.0, rise_frames=1)
    await _until(lambda: any(tb.state == "held" for tb in ses.policy.tier_b.values()), 6.0)
    await asyncio.sleep(0.4)
    print(f"  {_t()} 'held' after the -3; a loud passage now swamps the analyser (+35 dB on every band) for 0.6 s")
    base0 = fake.rta._base
    fake.rta._base = tuple(b + 35.0 for b in base0)
    await asyncio.sleep(0.6)
    fake.rta._base = base0
    ok = await _until(lambda: float(fake.value(GEQ_PAR_1K)) <= -5.9, 6.0)
    await conn.get("/-stat/selidx")
    ends = [e["next_action"] for e in ses.policy.tier_b_log if e["action"] == "end"]
    print(f"  {_t()} after the passage: ignore list {ses.policy.ignore} | first engagement ended: {ends[:1]} | band now {float(fake.value(GEQ_PAR_1K)):+.1f} dB "
          f"({'re-engaged and deepened' if ok else 'NOT deepened'})")
    await _until(lambda: any(tb.state == "held" and tb.depth_db is not None and tb.depth_db <= -5.9 for tb in ses.policy.tier_b.values()), 4.0)
    print(f"  {_t()} the engineer flattens GEQ band 18 on the desk (front-panel move, pushed over /xremote)")
    fake.set_value(GEQ_PAR_1K, 0.0)
    await asyncio.sleep(4.5)                             # > held_deepen_s + a verdict: nothing may re-cut the released band
    await conn.get("/-stat/selidx")
    writes = [g for (_b, band, g) in ses.writer.writes if band == 18]
    print(f"  {_t()} band now {float(fake.value(GEQ_PAR_1K)):+.1f} dB | session GEQ writes on band 18: {writes} | ignore: "
          f"{[(i['band'], i['why']) for i in ses.policy.ignore]} | open engagements: {[tb.brief()['outcome'] for tb in ses.policy.tier_b.values()]}")
    fake.rta.stop_note(1000.0)
    out = await cfs.stop()
    rep = out["report"]
    await conn.get("/-stat/selidx")
    print("== policy log (events):")
    print("\n".join(plog))
    print("== D result: strip now", fake.value("/bus/01/config/color"), "| writes on band 18", writes, "| verdict rows:",
          [(e["action"], e["verdict"], e["depth_db"]) for e in rep["policy"]["tier_b"] if e["action"] in ("verdict", "end", "operator")])
    assert not any("ended by itself" in i["why"] for i in rep["policy"]["ignore"]), "a MASKED held line was ignore-listed as ended"
    assert writes[:2] == [-3.0, -6.0] and all(w <= -3.0 for w in writes) and max(writes[2:], default=-99) <= -6.0, writes
    assert float(fake.value(GEQ_PAR_1K)) == 0.0, "the policy re-cut a band the engineer released"
    assert fake.value("/bus/01/config/color") == "GN"
    await cfs.close()
    await desk.close()
    await conn.close()
    await fake.stop()

    # ------------------------------------------------------------------------------------------------------------------
    print("\n== E: ring_out with a room source that follows the master (back-off probe) and a ring taking off mid-probe")
    src = RoomSourceRta(seed=5, band_hz=rta_band_hz(desc), level_100hz_db=-55.0, level_10khz_db=-65.0, noise_db=2.0, wobble_db=0.5, master=None)
    fake = FakeDesk(desc, host="127.0.0.1", port=0, name="X32-FAKE", rta=src)
    await fake.start()
    events = EventBus()
    conn = X32Connection(desc, events, **CONN_OPTS)
    await conn.connect(fake.host, fake.port)
    policy = Policy(desc, events)
    desk = Desk(desc, conn, policy, events, SnapshotStore(tmp / "snapshots3"))
    reports = ReportStore(tmp / "reports3")
    cfs = CfsManager(desk, policy, events, reports, snapshots=None, cfs_policy=CfsPolicyConfig.from_dict(desc.cfs_policy))
    t0 = time.monotonic()
    elog: list[tuple[str, float, dict]] = []
    for kind, typ in (("notch", "cfs.notch"), ("stage", "cfs.stage"), ("policy", "cfs.policy")):
        events.subscribe(lambda ev, kind=kind: elog.append((kind, time.monotonic(), dict(ev.data))), types={typ})
    plan = await plan_setup(desk, [1])
    await apply_setup(desk, plan)
    for ch in (1, 2):
        fake.set_value(f"/ch/{ch:02d}/mix/01/level", -20.0)
    fake.set_value("/bus/01/mix/fader", -30.0)
    fake.set_value("/bus/01/config/color", "GN")
    desk.invalidate()
    src._master = lambda: fake.value("/bus/01/mix/fader")   # coupled from HERE: the levels above are given at this master setting
    src.inject_note(1000.0, -27.0, rise_frames=1)        # ~33 dB prominent room source: follows the steps 1 dB/dB -> STATIONARY backoff_advised
    for _ in range(10):
        src.tick()
    run = asyncio.create_task(cfs.ring_out(1, target_gain_db=-22.0, step_db=1.0, dwell_ms=700))
    got_probe = await _until(lambda: any(k == "stage" and e.get("stage") == "PROBE" for k, _t2, e in elog), 30.0)
    t_probe = time.monotonic()
    print(f"  {_t()} back-off PROBE stage {'reached' if got_probe else 'NOT reached'}; injecting a 25 dB/s ring at 2500 Hz in 0.3 s")
    await asyncio.sleep(0.3)
    ses = cfs._ses
    if ses is None:
        rep = await asyncio.wait_for(run, timeout=60.0)
        raise SystemExit(f"E: the ring-out finished without a back-off probe: {[(s['stage'], s.get('reason')) for s in rep['stages'][-4:]]}")
    n_det0 = len(ses.detections)
    src.inject_ring(2500.0, 25.0, start_db=-50.0, cap_db=-6.0)
    await _until(lambda: len(ses.detections) > n_det0, 8.0, step=0.005)
    t_detect = time.monotonic()
    level_at_detect = ses.detections[-1]["level_db"] if len(ses.detections) > n_det0 else None
    await _until(lambda: any(k == "notch" and e.get("band") == 22 for k, _t2, e in elog), 15.0, step=0.005)
    t_cut = next((t for k, t, e in elog if k == "notch" and e.get("band") == 22), None)
    rep = await asyncio.wait_for(run, timeout=60.0)
    latency = (t_cut - t_detect) if t_cut is not None else None
    probes = rep["policy"]["backoff_probes"]
    print(f"  ring detected at {level_at_detect} dBFS {t_detect - t_probe:.2f} s into the probe; detection -> cut latency "
          f"{'%.2f s' % latency if latency is not None else 'NO CUT'}")
    for pr in probes:
        print("   probe:", {k: pr.get(k) for k in ("t", "freq_hz", "verdict", "drop_db", "master_from_db", "master_probe_db", "master_restored_db", "retry")})
    print(f"  stages: {[s['stage'] + ('/' + str(s.get('verdict')) if s.get('verdict') else '') for s in rep['stages'] if s['stage'] in ('PROBE', 'HOLD', 'NOTCH', 'VERIFY', 'DONE', 'ABORT')]}")
    print(f"  final {rep['final_stage']} {rep.get('abort_reason') or ''} | master {rep['start_master_db']} -> {rep['end_master_db']} (max {rep['max_master_db']}) | "
          f"notches {[(n['band'], n['depth_db'], n.get('tiers')) for n in rep['notches']]}")
    assert latency is not None and latency <= 0.5, f"tier-A cut delayed {latency} s behind the back-off probe"
    assert probes and probes[0]["verdict"] == "interrupted" and probes[0]["master_restored_db"] is None
    assert rep["final_stage"] in ("DONE", "ABORT")
    await cfs.close()
    await desk.close()
    await conn.close()
    await fake.stop()
    print("\nALL SCENARIOS OK")


if __name__ == "__main__":
    os.chdir(ROOT)
    asyncio.run(main())
