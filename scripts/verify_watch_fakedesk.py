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
stops at notch_max (bounded)."""
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
from x32mcp.connection import X32Connection  # noqa: E402
from x32mcp.descriptor import Descriptor  # noqa: E402
from x32mcp.desk import Desk  # noqa: E402
from x32mcp.events import EventBus  # noqa: E402
from x32mcp.fakedesk import FakeDesk  # noqa: E402
from x32mcp.nodes import SnapshotStore  # noqa: E402
from x32mcp.policy import Policy  # noqa: E402
from x32mcp.provision import apply_setup, plan_setup  # noqa: E402

CONN_OPTS = dict(timeout_s=0.25, retries=1, heartbeat_s=0.5, watchdog_s=1.0, backoff_s=(0.1, 0.2))


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
    print("\n== session report notches:", [(n["band"], n["freq_hz"], n["depth_db"]) for n in rep["notches"]])
    print("== report['detector']:", json.dumps({k: v for k, v in rep["detector"].items() if k != "cut_verdicts"}))
    for v in rep["detector"]["cut_verdicts"]:
        print("   verdict", v)
    print("== detection_log:", [(dd["freq_hz"], dd["reasons"][-2:]) for dd in rep["detection_log"]])
    await cfs.close()
    await desk.close()
    await conn.close()
    await fake.stop()


if __name__ == "__main__":
    os.chdir(ROOT)
    asyncio.run(main())
