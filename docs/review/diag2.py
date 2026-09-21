"""diag with analyser preset + auto mode/probe: diag2.py SCEN seed preset [band t0 t1]"""
import sys
sys.path.insert(0, "tests"); sys.path.insert(0, "src")
from rtasim import frames, ground_truth, RTA_BAND_HZ, SCENARIOS
from rtasim.physics import ANALYSER_PRESETS
from rtasim.harness import run_one
import rtasim.run_eval_disc as R
name, seed, preset = sys.argv[1], int(sys.argv[2]), sys.argv[3]
band = int(sys.argv[4]) if len(sys.argv) > 4 else -1
t0 = float(sys.argv[5]) if len(sys.argv) > 5 else 0; t1 = float(sys.argv[6]) if len(sys.argv) > 6 else 1e9
ov = ANALYSER_PRESETS[preset]
f = R.factory_for(name, "auto", False)
r = run_one(f, name, seed, analyser_overrides=ov, notch_cfg=R.CFG)
print("GT:", [(e.band, round(e.freq_hz), e.t_onset, e.t_prom) for e in r.episodes])
for d in r.detections:
    print(f"  det t={d.ts:.3f} b{d.band} {d.freq_hz:.0f}Hz L{d.level_db:.1f} P{d.prominence_db:.1f} conf{d.confidence:.2f} -> {d.verdict}")
print("lat:", r.latencies_ms, "fp:", len(r.fps))
if band >= 0:
    det = f(RTA_BAND_HZ)
    inner = getattr(det, "det", det)
    for ts, v in frames(name, seed, **ov):
        out = det.feed(v, ts)
        if ts < t0 or ts > t1: continue
        s = f"t={ts:6.3f} ref{inner.ref_db:6.1f} v={v[band]:6.1f}"
        for c in inner.candidates:
            if abs(c.band - band) <= 1 and not c.missed:
                s += (f" | b{c.band} c{c.centroid:5.2f} L{c.level_db:6.1f} P{c.prominence_db:5.1f} N{c.narrow_db:5.1f} fr{c.frames:3d} fam{c.family_frac:.2f} "
                      f"step{int(c.step_onset)} n{len(c.levels)} sl{c.slope_db_per_s:6.1f} G{int(c.growth)} S{int(c.sustained)} M{int(c.musical)} ph{c.probe_hits} pb{c.probe_base} {','.join(c.reasons)}")
        for o in out: s += f" *** EMIT b{o.band} {o.reasons}"
        print(s)
