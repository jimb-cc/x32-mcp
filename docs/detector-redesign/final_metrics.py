"""Run the four reference evaluations (open/closed loop × watch/ringout-with-probe, seeds 1-3) and write the harness
JSON dumps plus a combined metrics.json with per-scenario rows, totals and the latency distribution."""
import json, time, dataclasses, os, sys
from statistics import median
from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig, FeedbackDetector
from rtasim import SCENARIOS
from rtasim.harness import run_one, Results

OUT = sys.argv[1]
cfg0 = DetectorConfig.from_descriptor(Descriptor.load())
SEEDS = (1, 2, 3)


class ProbeWrap:
    def __init__(self, det, steps):
        self.det, self.steps, self.i = det, sorted(steps), 0

    def feed(self, vals, ts):
        while self.i < len(self.steps) and ts >= self.steps[self.i][0]:
            self.det.note_gain_step(self.steps[self.i][1], self.steps[self.i][0])
            self.i += 1
        return self.det.feed(vals, ts)


def step_list(sc, seed):
    m = sc.build(seed).master
    if m is None:
        return []
    return [(b[0], b[1] - a[1]) for a, b in zip(m.points, m.points[1:]) if b[0] == a[0] and b[1] != a[1]]


def factory(sc, seed, mode):
    cfg = dataclasses.replace(cfg0, mode=mode)
    def f(bh):
        det = FeedbackDetector(cfg, bh)
        if mode == "ringout" and sc.mode == "ring_out":
            return ProbeWrap(det, step_list(sc, seed))
        return det
    return f


combined = {"detector": "disc-free-hand", "config": cfg0.to_dict(), "runs": {}}
for mode in ("watch", "ringout"):
    for closed in (False, True):
        label = f"{'closed' if closed else 'open'}_{mode}" + ("_probe" if mode == "ringout" else "")
        t0 = time.perf_counter()
        res = Results(meta={"label": label, "seeds": list(SEEDS), "closed_loop": closed, "mode": mode,
                            "probe": mode == "ringout", "detector_config": dataclasses.replace(cfg0, mode=mode).to_dict()})
        for name, sc in SCENARIOS.items():
            if closed and not sc.has_feedback:
                continue
            for seed in SEEDS:
                res.runs.append(run_one(factory(sc, seed, mode), sc, seed, closed_loop=closed, notch_cfg=cfg0))
        res.meta["wall_s"] = round(time.perf_counter() - t0, 2)
        res.dump(os.path.join(OUT, f"metrics_{label}.json"))
        rows = res.summary_rows()
        lats = sorted(l for r in res.runs for l in r.latencies_ms)
        def pct(p):
            return lats[min(len(lats) - 1, int(p * len(lats)))] if lats else None
        tot = dict(scenarios=len(rows), passed=sum(1 for r in rows if r["verdict"] == "PASS"),
                   events=sum(r["events"] for r in rows), tp=sum(r["tp"] for r in rows), miss=sum(r["miss"] for r in rows),
                   fp=sum(r["fp"] for r in rows), early=sum(r["early"] for r in rows), tail=sum(r["tail"] for r in rows),
                   harm=sum(r["harm"] for r in rows), cuts=sum(r["cuts"] for r in rows),
                   latency_ms=dict(n=len(lats), min=lats[0] if lats else None, p50=pct(0.5), p90=pct(0.9), p95=pct(0.95),
                                   max=lats[-1] if lats else None,
                                   over_budget=sum(1 for r in res.runs for l in r.latencies_ms if l > r.latency_budget_ms),
                                   within_300=sum(1 for l in lats if l <= 300.0)))
        combined["runs"][label] = {"totals": tot, "rows": rows,
                                   "failures": {r["scenario"]: {k: r[k] for k in ("fp", "miss", "lat_min_ms", "lat_med_ms", "lat_max_ms", "budget_ms", "fp_geq_bands", "passed_seeds")}
                                                for r in rows if r["verdict"] != "PASS"}}
        print(f"== {label}: {tot}")
        print(res.table())
sw = os.path.join(OUT, "sweeps.json")
if os.path.exists(sw):
    combined["sweeps"] = json.load(open(sw))
json.dump(combined, open(os.path.join(OUT, "metrics.json"), "w"), indent=1, default=str)
print("written", os.path.join(OUT, "metrics.json"))
