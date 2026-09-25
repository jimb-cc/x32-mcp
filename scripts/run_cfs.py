#!/usr/bin/env python
"""Drive CFS² (ring_out / feedback_watch) from the current checkout without the MCP server: the same App the server
builds (Desk, Policy, snapshots, reports, CfsManager), dashboard off, so a code change on disk is live at once.

The MCP tool layer's confirmation dance is NOT here: this script is for the operator who has already said yes in
chat. `preflight` is read-only. `ringout` and `watch` write to the desk (master, GEQ, RTA prefs, scribble strip) and
need `--yes`. Reports land in the same report store as the server's (`ringout_reports/`), snapshots in the same
snapshot store, so `restore_snapshot` from the MCP server undoes what this script did.

    python scripts/run_cfs.py HOST preflight main
    python scripts/run_cfs.py HOST ringout main --target 0 --step 1 --dwell 1500 --budget 6 --yes
    python scripts/run_cfs.py HOST watch main --seconds 180 --budget 6 --yes
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
import time

from x32mcp import provision as _prov
from x32mcp.config import Settings
from x32mcp.server import App, _bus_target


def _settings(dash_port: int | None) -> Settings:
    """The server's settings with autoconnect off and the dashboard on ``dash_port`` (None = off): the MCP server's own
    dashboard (default 8032) keeps its port, so a script run is watched on a second one (``--dash 8033``)."""
    s = Settings.from_env()
    kw = {"x32_host": None, "dash_enabled": dash_port is not None}
    if dash_port is not None:
        kw["dash_port"] = int(dash_port)
    try:
        return dataclasses.replace(s, **kw)
    except TypeError:
        for k, v in kw.items():
            setattr(s, k, v)
        return s


def _short(res: dict) -> str:
    keys = ("summary", "session_id", "final_stage", "aborted", "abort_reason", "gain_before_feedback_db", "start_master_db",
            "end_master_db", "max_master_db", "budget_left", "duration_s", "path")
    return json.dumps({k: res.get(k) for k in keys if k in res}, indent=1)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=10023)
    ap.add_argument("--dash", type=int, default=None, help="serve this run's dashboard on this port (e.g. 8033; the MCP server keeps 8032)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("preflight"); p.add_argument("bus")
    r = sub.add_parser("ringout"); r.add_argument("bus"); r.add_argument("--target", type=float, default=0.0)
    r.add_argument("--step", type=float, default=1.0); r.add_argument("--dwell", type=int, default=1500)
    r.add_argument("--budget", type=int, default=6); r.add_argument("--yes", action="store_true")
    w = sub.add_parser("watch"); w.add_argument("bus"); w.add_argument("--seconds", type=float, default=180.0)
    w.add_argument("--budget", type=int, default=6); w.add_argument("--yes", action="store_true")
    a = ap.parse_args()

    app = App(_settings(a.dash))
    await app.start()
    if a.dash is not None:
        print(f"dashboard for this run: http://127.0.0.1:{a.dash}/", file=sys.stderr)
    info = await app.connect(a.host, a.port)
    print(f"connected to {info.name} FW {info.firmware} (code: {App.__module__} from this checkout)", file=sys.stderr)
    t = _bus_target(a.bus)
    try:
        if a.cmd == "preflight" or not getattr(a, "yes", False):
            pf = await _prov.preflight(app.desk, t, patch=None, reports=app.reports, input_levels=None)
            d = pf.to_dict()
            print(json.dumps({k: d.get(k) for k in ("ok", "bus", "master_db", "muted", "blockers", "warnings", "included")}, indent=1))
            for m in d.get("mics", []):
                print(f"  ch {m['ch']:2d} {m['name']!r:14} send {m['send']:>6} muted={m['muted']} include={m['include']} {m.get('notes')}")
            if a.cmd != "preflight":
                print("(dry run: add --yes to run)", file=sys.stderr)
            return 0 if d.get("ok") else 1
        if a.cmd == "ringout":
            print(f"ring_out {t.label}: target {a.target} dB, step {a.step}, dwell {a.dwell} ms, budget {a.budget}", file=sys.stderr)
            res = await app.cfs.ring_out(t, target_gain_db=a.target, step_db=a.step, dwell_ms=a.dwell, notch_budget=a.budget)
            print(_short(res))
            print("rta_wake:", res.get("rta_wake"), "| detector_set_peak:", (res.get("rta") or {}).get("detector_set_peak"))
            for n in res.get("notch_log", []):
                print(f"  master {n['master_db']:6.1f}  ring {n['rta_freq_hz']:8.1f} Hz level {n['level_db']:6.1f} -> GEQ {n['freq_hz']:>7} Hz {n['depth_db']:+.0f}  "
                      f"{[x for x in n['reasons'] if x == 'loud' or 'rise' in x]}")
            return 0
        # watch
        res = await app.cfs.feedback_watch(t, notch_budget=a.budget)
        print("armed:", json.dumps({k: res.get(k) for k in ("session_id", "rta_wake")}), "| detector_set_peak:", (res.get("rta") or {}).get("detector_set_peak"), file=sys.stderr)
        t0 = time.time()
        try:
            while time.time() - t0 < a.seconds:
                await asyncio.sleep(10.0)
                st = app.cfs.state.to_dict()
                print(f"  t+{time.time() - t0:5.0f}s notches {len(st.get('notches') or [])} budget_left {st.get('budget_left')} "
                      f"alert {st.get('alert')} candidates {[(round(c.get('freq_hz', 0)), c.get('klass'), round(c.get('level_db', 0), 1)) for c in (st.get('candidates') or [])][:4]}", file=sys.stderr)
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("stopping early", file=sys.stderr)
        out = await app.cfs.stop()
        rep = out.get("report") or {}
        print(_short(rep))
        for n in rep.get("notch_log", []):
            print(f"  t+{n.get('ts', 0) - rep.get('started_ts', n.get('ts', 0)):6.1f}s  ring {n['rta_freq_hz']:8.1f} Hz level {n['level_db']:6.1f} -> GEQ {n['freq_hz']:>7} Hz {n['depth_db']:+.0f} tier {n.get('tier')} policy {n.get('policy')}  "
                  f"{[x for x in n['reasons'] if x == 'loud' or 'rise' in x]}")
        print("report:", rep.get("path"))
        return 0
    finally:
        await app.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
