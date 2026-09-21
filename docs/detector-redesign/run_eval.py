"""Evaluate the disc-free-hand detector on the rtasim corpus.

usage: run_eval.py [--mode watch|ringout|both] [--closed] [--probe] [--seeds 1,2,3] [--json out] [--fp] [scenario ...]
  --probe : in ringout mode, call det.note_gain_step(delta, ts) at the scenario's master step times
            (only for scenarios whose mode is ring_out: the server knows its own writes).
  --fp    : print every FP / miss detail
"""
import sys, json, time, dataclasses
from statistics import median
from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig, FeedbackDetector
from rtasim import SCENARIOS
from rtasim.harness import run_one, Results
from rtasim.physics import RTA_BAND_HZ

args = sys.argv[1:]
def opt(name, default=None):
    for a in args:
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return default
mode = opt("--mode", "watch")
closed = "--closed" in args
probe = "--probe" in args
showfp = "--fp" in args
seeds = tuple(int(x) for x in opt("--seeds", "1,2,3").split(","))
out = opt("--json")
names = [a for a in args if not a.startswith("--")] or list(SCENARIOS)
cfg0 = DetectorConfig.from_descriptor(Descriptor.load())


class ProbeWrap:
    def __init__(self, det, steps):
        self.det = det
        self.steps = sorted(steps)
        self.i = 0
    def feed(self, vals, ts):
        while self.i < len(self.steps) and ts >= self.steps[self.i][0]:
            self.det.note_gain_step(self.steps[self.i][1], self.steps[self.i][0])
            self.i += 1
        return self.det.feed(vals, ts)


def step_list(sc, seed):
    scene = sc.build(seed)
    m = scene.master
    if m is None:
        return []
    out = []
    pts = m.points
    for a, b in zip(pts, pts[1:]):
        if b[0] == a[0] and b[1] != a[1]:
            out.append((b[0], b[1] - a[1]))
    return out


def make_factory(sc, seed, m):
    cfg = dataclasses.replace(cfg0, mode=m)
    def f(bh):
        det = FeedbackDetector(cfg, bh)
        if probe and m == "ringout" and sc.mode == "ring_out":
            return ProbeWrap(det, step_list(sc, seed))
        return det
    return f

modes = ["watch", "ringout"] if mode == "both" else [mode]
for m in modes:
    t0 = time.perf_counter()
    res = Results(meta={"label": f"disc-free-hand mode={m} closed={closed} probe={probe}", "seeds": list(seeds),
                        "closed_loop": closed, "scenarios": names})
    for name in names:
        sc = SCENARIOS[name]
        if closed and not sc.has_feedback:
            continue
        for seed in seeds:
            res.runs.append(run_one(make_factory(sc, seed, m), sc, seed, closed_loop=closed, notch_cfg=cfg0))
    res.meta["wall_s"] = round(time.perf_counter() - t0, 2)
    print(f"\n== mode={m} closed={closed} probe={probe}")
    print(res.table())
    if showfp:
        for r in res.runs:
            bad = [d for d in r.detections if d.verdict == "FP"]
            if bad or r.misses or any(l > r.latency_budget_ms for l in r.latencies_ms):
                print(f"-- {r.scenario} s{r.seed}: lat={r.latencies_ms} miss={[ (round(e.freq_hz), e.t_onset, e.t_prom) for e in r.misses]}")
                for d in bad[:12]:
                    print(f"     FP t={d.ts:.2f} band={d.band} f={d.freq_hz:.0f} L={d.level_db} prom={d.prominence_db} slope={d.slope_db_per_s}")
    if out:
        p = out if len(modes) == 1 else out.replace(".json", f"_{m}.json")
        res.meta["detector_config"] = dataclasses.replace(cfg0, mode=m).to_dict()
        res.dump(p)
        print("written", p)
