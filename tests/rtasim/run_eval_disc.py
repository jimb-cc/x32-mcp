"""Full evaluation of the disc-minimal-delta detector over the corpus.

    cd <worktree> && PYTHONPATH=src:tests python run_eval.py [out.json]

Runs (seeds 1,2,3):
  * auto/open      : each scenario in its own mode (ring_out scenarios: mode='ringout' + note_gain_step at the
                     server's own master steps; everything else 'watch'), default window (no LF opt-in)
  * auto/closed    : the same, closed loop (NotchController, 1-frame actuation delay), feedback scenarios only
  * watch/open     : every scenario forced to mode='watch' (no probe)
  * ringout/open   : every scenario forced to mode='ringout' (probe where the scenario has steps)
  * auto+lf/open   : as auto/open with lf_feedback_possible=1 (40 Hz window) - what the LF opt-in buys and costs
Writes metrics JSON with per-run detections and prints the summary tables.
"""
from __future__ import annotations
import dataclasses, json, os, sys, time
from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig, FeedbackDetector
from rtasim import SCENARIOS, evaluate
from rtasim.harness import Results, run_one

CFG = DetectorConfig.from_descriptor(Descriptor.load())


class ProbeWrapper:
    """FeedbackDetector plus the note_gain_step calls cfs.ring_out would make after each master step."""
    def __init__(self, cfg, band_hz, steps):
        self.det = FeedbackDetector(cfg, band_hz)
        self.steps = sorted(steps)
        self.i = 0
    def feed(self, values, ts):
        while self.i < len(self.steps) and self.steps[self.i][0] <= ts:
            self.det.note_gain_step(self.steps[self.i][1], self.steps[self.i][0])
            self.i += 1
        return self.det.feed(values, ts)


def steps_of(name):
    sc = SCENARIOS[name].build(1)
    if sc.master is None:
        return []
    pts = sc.master.points
    out = []
    for a, b in zip(pts, pts[1:]):
        if b[0] == a[0] and b[1] != a[1] and b[1] > a[1]:
            out.append((b[0], b[1] - a[1]))
    return out


def factory_for(name, mode_policy, lf):
    sc = SCENARIOS[name]
    if mode_policy == "auto":
        mode = "ringout" if sc.mode == "ring_out" else "watch"
    else:
        mode = mode_policy
    cfg = dataclasses.replace(CFG, mode=mode, lf_feedback_possible=1.0 if lf else 0.0)
    steps = steps_of(name) if (mode == "ringout" and sc.mode == "ring_out") else []
    if steps:
        return lambda bh: ProbeWrapper(cfg, bh, steps)
    return lambda bh: FeedbackDetector(cfg, bh)


def run(mode_policy, lf=False, closed=False, seeds=(1, 2, 3), only_feedback=False):
    names = [n for n, s in SCENARIOS.items() if (s.has_feedback or not only_feedback)]
    res = Results(meta={"label": f"{mode_policy}{'+lf' if lf else ''}/{'closed' if closed else 'open'}", "seeds": list(seeds),
                        "closed_loop": closed, "scenarios": names})
    t0 = time.perf_counter()
    for n in names:
        f = factory_for(n, mode_policy, lf)
        for sd in seeds:
            res.runs.append(run_one(f, n, sd, closed_loop=closed, notch_cfg=CFG))
    res.meta["wall_s"] = round(time.perf_counter() - t0, 2)
    return res


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "metrics.json"
    allres = {}
    for key, kw in (("auto_open", dict(mode_policy="auto")),
                    ("auto_closed", dict(mode_policy="auto", closed=True, only_feedback=True)),
                    ("watch_open", dict(mode_policy="watch")),
                    ("ringout_open", dict(mode_policy="ringout")),
                    ("auto_lf_open", dict(mode_policy="auto", lf=True))):
        r = run(**kw)
        print(f"\n===== {r.meta['label']} =====")
        print(r.table())
        allres[key] = r.to_json()
    allres["detector_config"] = CFG.to_dict()
    with open(out, "w") as fh:
        json.dump(allres, fh, indent=1, default=str)
    print("written", out)


if __name__ == "__main__":
    main()
