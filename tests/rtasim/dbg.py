"""Ad-hoc: run the detector (informed) on named scenarios, print detections with verdicts + reasons, and for misses
print the ring-band track state over time.   PYTHONPATH=src:tests python -m rtasim.dbg [--closed] [--blind] [--seed=N] NAME...
"""

from __future__ import annotations

import sys
import time

from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig, FeedbackDetector

from rtasim import SCENARIOS
from rtasim.harness import run_one
from rtasim.run_design import Informed, step_schedule


def main() -> None:
    names = [a for a in sys.argv[1:] if not a.startswith("--")]
    closed = "--closed" in sys.argv
    blind = "--blind" in sys.argv
    seeds = [int(a[7:]) for a in sys.argv[1:] if a.startswith("--seed=")] or [1]
    trace_band = [int(a[8:]) for a in sys.argv[1:] if a.startswith("--trace=")]
    cfg = DetectorConfig.from_descriptor(Descriptor.load())
    for name in names:
        sc = SCENARIOS[name]
        for seed in seeds:
            holder = {}

            def factory(bh, sc=sc, seed=seed):
                if blind:
                    det = FeedbackDetector(cfg, bh)
                    holder["det"] = det
                    return det
                mode = "ringout" if sc.mode == "ring_out" else "watch"
                det = FeedbackDetector(cfg, bh, mode=mode, lf_feedback_possible=sc.lf_optin)
                holder["det"] = det
                w = Informed(det, step_schedule(sc.name, seed))
                if trace_band:
                    orig = w.feed

                    def feed(values, ts, orig=orig, det=det):
                        out = orig(values, ts)
                        for c in det.tracks:
                            if any(abs(c.centroid - b) <= 1.2 for b in trace_band):
                                print(f"  t={ts:6.2f} uid={c.uid:3d} c={c.centroid:6.2f} L={c.level_db:6.1f} cp={c.cpows[-1]:6.1f} prom={c.prominence_db:5.1f} "
                                      f"nar={c.narrows[-1]:5.1f} fr={c.frames:3d} onset={c.onset:7s} fam={int(c.fam[-1])} st={c.state:9s} "
                                      f"tier={c.tier:11s} conf={c.confidence:.2f} slope={c.slope_db_per_s:6.1f} {','.join(c.reasons)}")
                        return out
                    w.feed = feed
                return w

            t0 = time.perf_counter()
            r = run_one(factory, name, seed, closed_loop=closed, notch_cfg=cfg)
            print(f"== {name} seed {seed} {'closed' if closed else 'open'}: tp={r.tp} miss={len(r.misses)} fp={len(r.fps)} "
                  f"early={len(r.early)} tail={len(r.tail)} harm={len(r.harm)} lat={r.latencies_ms} budget={r.latency_budget_ms} "
                  f"pass={r.passed} ({time.perf_counter() - t0:.1f}s) flags={holder['det'].flags}")
            for e in r.episodes:
                print(f"   episode ring{e.ring} {e.freq_hz:.0f} Hz band {e.band} on={e.t_onset} prom={e.t_prom} end={e.t_end} vis={e.visible}")
            for d in r.detections:
                print(f"   {d.verdict:5s} t={d.ts:6.2f} band={d.band} f={d.freq_hz:7.1f} L={d.level_db} prom={d.prominence_db} conf={d.confidence:.2f}")
            if closed:
                print("   cuts:", [(round(t, 2), b, g) for t, b, g in r.cuts])


if __name__ == "__main__":
    main()
