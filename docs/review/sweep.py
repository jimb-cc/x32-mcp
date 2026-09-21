"""Robustness sweep over analyser uncertainties (CORPUS §5): one seed, all scenarios, auto mode."""
import sys, json, time, dataclasses
sys.path.insert(0, "tests"); sys.path.insert(0, "src")
from rtasim import SCENARIOS
from rtasim.physics import ANALYSER_PRESETS
from rtasim.harness import Results, run_one
import rtasim.run_eval_disc as R

presets = sys.argv[1].split(",") if len(sys.argv) > 1 else ["u_slow", "bq", "skirt_n2", "skirt_steep", "rms", "decay_4s", "release_law_17", "flat_beds"]
seeds = (1,)
out = {}
for pr in presets:
    ov = ANALYSER_PRESETS[pr]
    res = Results(meta={"label": pr})
    t0 = time.perf_counter()
    for n in SCENARIOS:
        f = R.factory_for(n, "auto", False)
        for sd in seeds:
            res.runs.append(run_one(f, n, sd, analyser_overrides=ov, notch_cfg=R.CFG))
    rows = res.summary_rows()
    fp = sum(r["fp"] for r in rows); miss = sum(r["miss"] for r in rows); tp = sum(r["tp"] for r in rows)
    fails = [(r["scenario"], r["fp"], r["miss"], r["lat_max_ms"]) for r in rows if r["verdict"] != "PASS"]
    print(f"== {pr} {ov}: TP {tp} miss {miss} FP {fp} pass {sum(1 for r in rows if r['verdict']=='PASS')}/{len(rows)} ({time.perf_counter()-t0:.0f}s)")
    for fz in fails: print("   ", fz)
    out[pr] = {"summary": rows}
json.dump(out, open(sys.argv[2] if len(sys.argv) > 2 else "sweep.json", "w"), indent=1, default=str)
