"""sweep.py: robustness of the detector across hold-out seeds and analyser-model overrides (open loop, watch)."""
import sys, json, time, dataclasses
from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig, FeedbackDetector
from rtasim import SCENARIOS
from rtasim.harness import evaluate

cfg = DetectorConfig.from_descriptor(Descriptor.load())
which = sys.argv[1] if len(sys.argv) > 1 else "all"
configs = {
    "baseline_s123": dict(seeds=(1, 2, 3), ov=None),
    "holdout_s456": dict(seeds=(4, 5, 6), ov=None),
    "attack_bq_0.32": dict(seeds=(1, 2), ov={"attack_k": 0.32}),
    "attack_slow_1.0": dict(seeds=(1, 2), ov={"attack_k": 1.0}),
    "skirt_n2": dict(seeds=(1, 2), ov={"skirt_order": 2}),
    "skirt_n5": dict(seeds=(1, 2), ov={"skirt_order": 5}),
    "decay_4s": dict(seeds=(1, 2), ov={"decay_s": 4.0}),
    "release_law_17": dict(seeds=(1, 2), ov={"release_law_db": 17.0}),
    "release_law_240": dict(seeds=(1, 2), ov={"release_law_db": 240.0}),
    "det_rms": dict(seeds=(1, 2), ov={"det": "RMS"}),
    "noise_x1.5": dict(seeds=(1, 2), ov={"noise_sd_scale": 1.5}),
    "substeps_1": dict(seeds=(1, 2), ov={"substeps": 1}),
    "frame_drop_1pct": dict(seeds=(1, 2), ov={"drop_frame_prob": 0.01}),
    "gain_offset_12": dict(seeds=(1, 2), ov={"gain_offset_db": 12.0}),
}
names = [which] if which != "all" else list(configs)
out = {}
for name in names:
    c = configs[name]
    t0 = time.perf_counter()
    res = evaluate(lambda bh: FeedbackDetector(cfg, bh), seeds=c["seeds"], analyser_overrides=c["ov"], label=name)
    rows = res.summary_rows()
    tot = dict(npass=sum(1 for r in rows if r["verdict"] == "PASS"), n=len(rows), fp=sum(r["fp"] for r in rows),
               miss=sum(r["miss"] for r in rows), tp=sum(r["tp"] for r in rows), ev=sum(r["events"] for r in rows),
               harm=sum(r["harm"] for r in rows), tail=sum(r["tail"] for r in rows))
    fails = {r["scenario"]: dict(fp=r["fp"], miss=r["miss"], lat=(r["lat_min_ms"], r["lat_med_ms"], r["lat_max_ms"]), fpb=r["fp_geq_bands"])
             for r in rows if r["verdict"] != "PASS"}
    out[name] = dict(tot=tot, fails=fails, wall=round(time.perf_counter() - t0, 1))
    print(f"{name:18s} pass {tot['npass']}/{tot['n']}  TP {tot['tp']}/{tot['ev']}  miss {tot['miss']}  FP {tot['fp']}  harm {tot['harm']} tail {tot['tail']}  ({out[name]['wall']} s)")
    for k, v in fails.items():
        print(f"      {k}: fp={v['fp']} miss={v['miss']} lat={v['lat']} fpbands={v['fpb']}")
    sys.stdout.flush()
json.dump(out, open(sys.argv[2] if len(sys.argv) > 2 else "/dev/null", "w"), indent=1, default=str)
