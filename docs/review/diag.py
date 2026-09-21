"""Run one scenario/seed through the detector; print detections (with harness verdicts) and per-frame
candidate state near a band. usage: diag.py SCEN seed [band|-1] [t0 t1] [mode]"""
import sys
sys.path.insert(0, "tests"); sys.path.insert(0, "src")
from rtasim import frames, ground_truth, RTA_BAND_HZ, SCENARIOS
from rtasim.harness import run_one
from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig, FeedbackDetector
import dataclasses
name, seed = sys.argv[1], int(sys.argv[2])
band = int(sys.argv[3]) if len(sys.argv) > 3 else -1
t0 = float(sys.argv[4]) if len(sys.argv) > 4 else 0; t1 = float(sys.argv[5]) if len(sys.argv) > 5 else 1e9
mode = sys.argv[6] if len(sys.argv) > 6 else "watch"
cfg = DetectorConfig.from_descriptor(Descriptor.load())
cfg = dataclasses.replace(cfg, mode=mode)
gt = ground_truth(name, seed)
print("GT:", [(e["band"], round(e["freq_hz"]), e["t_onset"], e["t_prom"], e.get("peak_prom_db")) for e in gt["events"]])
r = run_one(lambda bh: FeedbackDetector(cfg, bh), name, seed)
for d in r.detections:
    print(f"  det t={d.ts:.3f} b{d.band} {d.freq_hz:.0f}Hz L{d.level_db:.1f} P{d.prominence_db:.1f} s{d.slope_db_per_s:.0f} conf{d.confidence:.2f} -> {d.verdict}")
print("lat:", r.latencies_ms, "miss:", [(e.band, e.t_prom) for e in r.misses], "fp:", len(r.fps))
if band >= 0:
    det = FeedbackDetector(cfg, RTA_BAND_HZ)
    for ts, v in frames(name, seed):
        out = det.feed(v, ts)
        if ts < t0 or ts > t1: continue
        cs = [c for c in det.candidates if abs(c.band - band) <= 2]
        s = f"t={ts:6.3f} ref{det.ref_db:6.1f} v[{band}]={v[band]:6.1f}"
        for c in cs:
            s += (f" | b{c.band} c{c.centroid:5.2f} L{c.level_db:6.1f} C{c.cluster_db:6.1f} P{c.prominence_db:5.1f} N{c.narrow_db:5.1f} fr{c.frames:3d} "
                  f"fam{c.family_frac:.2f} step{int(c.step_onset)} arm{int(c.at_arm)} n{len(c.levels)} sl{c.slope_db_per_s:6.1f} "
                  f"G{int(c.growth)} S{int(c.sustained)} M{int(c.musical)} wnd{(max(c.centroids)-min(c.centroids)) if c.centroids else 0:.2f} "
                  f"rec[{min(c.recent):.1f},{max(c.recent):.1f}] {','.join(c.reasons)} conf{c.confidence:.2f}")
        for o in out:
            s += f"  *** EMIT b{o.band} {o.reasons}"
        print(s)
