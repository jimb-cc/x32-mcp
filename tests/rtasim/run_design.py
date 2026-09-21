"""Design-evaluation driver (disc-track-and-group): run the redesigned FeedbackDetector over the whole rtasim corpus in
two configurations and both loop modes, write JSON + a markdown table per run.

* **informed**: what ``cfs`` would construct — ``mode`` from the scenario (``feedback_watch``/``ring_out``), the LF
  opt-in when the scenario declares an LF-capable source (``Scenario.lf_optin``), and, in ring_out, the server's own
  master steps delivered through ``det.note_gain_step(delta, ts)`` at the moment they are written.
* **blind**: ``FeedbackDetector(cfg, band_hz)`` exactly as the corpus harness' default factory calls it (watch mode,
  no opt-in, no step information) — the worst case for the design.

    cd <worktree> && PYTHONPATH=src:tests python -m rtasim.run_design [out_dir] [--quick] [--only=informed|blind]
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Sequence

from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig, FeedbackDetector

from rtasim import SCENARIOS, evaluate
from rtasim.physics import GEQ_BAND_HZ
from rtasim.harness import run_one, Results

DEFAULT_OUT = ("/private/tmp/claude-502/-Users-jimb-code-studio-intel-demo/d5626e47-bee8-4335-aa3f-27a50ae21455/"
               "scratchpad/reports/disc-track-and-group")


def step_schedule(name: str, seed: int) -> list[tuple[float, float]]:
    """(t, delta_db) of the server's own master steps for ring_out scenarios (the server knows its writes)."""
    sc = SCENARIOS[name]
    if sc.mode != "ring_out":
        return []
    scene = sc.build(seed)
    m = scene.master
    if m is None:
        return []
    out = []
    for a, b in zip(m.points, m.points[1:]):
        if b[0] == a[0] and b[1] != a[1]:
            out.append((float(b[0]), float(b[1] - a[1])))
    return out


class Informed:
    """Wraps the detector so master steps are announced before the first frame that follows them."""

    def __init__(self, det: FeedbackDetector, steps: list[tuple[float, float]]) -> None:
        self.det = det
        self.steps = sorted(steps)
        self.i = 0

    def feed(self, values, ts):
        while self.i < len(self.steps) and self.steps[self.i][0] <= ts + 1e-9:
            t, d = self.steps[self.i]
            self.det.note_gain_step(d, t)
            self.i += 1
        return self.det.feed(values, ts)


def md_table(res, title: str) -> str:
    rows = res.summary_rows()
    out = [f"### {title}", "",
           "| scenario | ev | TP | miss | FP | early | tail | harm | cuts | lat ms min/med/max | budget | FP at GEQ (Hz) | verdict |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lat = "–" if r["lat_med_ms"] is None else f"{r['lat_min_ms']:.0f}/{r['lat_med_ms']:.0f}/{r['lat_max_ms']:.0f}"
        fpb = ",".join(str(int(GEQ_BAND_HZ[b - 1])) for b in r["fp_geq_bands"][:8]) + (",…" if len(r["fp_geq_bands"]) > 8 else "")
        out.append(f"| {r['scenario']} | {r['events']} | {r['tp']} | {r['miss']} | {r['fp']} | {r['early']} | {r['tail']} | "
                   f"{r['harm']} | {r['cuts']} | {lat} | {r['budget_ms']:.0f} | {fpb} | {r['verdict']} {r['passed_seeds']}/{r['seeds']} |")
    tot = dict(ev=sum(r["events"] for r in rows), tp=sum(r["tp"] for r in rows), miss=sum(r["miss"] for r in rows),
               fp=sum(r["fp"] for r in rows), early=sum(r["early"] for r in rows), tail=sum(r["tail"] for r in rows),
               harm=sum(r["harm"] for r in rows), npass=sum(1 for r in rows if r["verdict"] == "PASS"))
    out.append(f"| **{len(rows)} scenarios, {tot['npass']} pass** | {tot['ev']} | {tot['tp']} | {tot['miss']} | {tot['fp']} | "
               f"{tot['early']} | {tot['tail']} | {tot['harm']} | | | | | wall {res.meta.get('wall_s', 0):.1f} s |")
    return "\n".join(out) + "\n"


def run_variant(cfg: DetectorConfig, variant: str, names: Sequence[str], seeds: Sequence[int], closed: bool,
                analyser_overrides: dict[str, Any] | None = None) -> Results:
    t0 = time.perf_counter()
    res = Results(meta={"label": f"{variant} {'closed' if closed else 'open'} loop", "seeds": list(seeds), "closed_loop": closed,
                        "analyser_overrides": analyser_overrides or {}, "scenarios": list(names), "variant": variant})
    for name in names:
        sc = SCENARIOS[name]
        for seed in seeds:
            if variant == "informed":
                mode = "ringout" if sc.mode == "ring_out" else "watch"
                steps = step_schedule(name, seed)
                factory = (lambda bh, mode=mode, lf=sc.lf_optin, steps=steps:
                           Informed(FeedbackDetector(cfg, bh, mode=mode, lf_feedback_possible=lf), steps))
            else:
                factory = lambda bh: FeedbackDetector(cfg, bh)
            res.runs.append(run_one(factory, name, seed, closed_loop=closed, analyser_overrides=analyser_overrides, notch_cfg=cfg))
    res.meta["wall_s"] = round(time.perf_counter() - t0, 2)
    return res


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out_dir = args[0] if args else os.environ.get("RTASIM_REPORT_DIR", DEFAULT_OUT)
    os.makedirs(out_dir, exist_ok=True)
    quick = "--quick" in sys.argv
    only = [a[7:] for a in sys.argv[1:] if a.startswith("--only=")]
    variants = only or ["informed", "blind"]
    cfg = DetectorConfig.from_descriptor(Descriptor.load())
    seeds = (1,) if quick else (1, 2, 3)
    all_names = list(SCENARIOS)
    fb = [n for n, s in SCENARIOS.items() if s.has_feedback]
    md = ["# disc-track-and-group: FeedbackDetector over the rtasim corpus", "",
          f"generated {time.strftime('%Y-%m-%d %H:%M:%S')} — seeds {list(seeds)}; config from device.yaml + defaults: "
          f"prominence {cfg.prominence_db}/±{cfg.neighbour_bins}, track floor {cfg.track_floor_db}, min_level {cfg.min_level_db}, "
          f"K1 {cfg.confirm_frames}, K2 {cfg.watch_confirm_frames}, window watch {cfg.f_low_watch_hz:.0f}/ringout "
          f"{cfg.f_low_ringout_hz:.0f}/LF {cfg.f_low_lf_hz:.0f}..{cfg.f_high_hz:.0f} Hz, loud {cfg.loud_level_db}, budget {cfg.notch_budget_default}", ""]
    summary: dict[str, Any] = {}
    for variant in variants:
        for closed in (False, True):
            names = fb if closed else all_names
            res = run_variant(cfg, variant, names, seeds, closed)
            res.meta["detector_config"] = cfg.to_dict()
            fname = f"metrics_{variant}_{'closed' if closed else 'open'}.json"
            res.dump(os.path.join(out_dir, fname))
            print(f"\n== {variant} {'closed' if closed else 'open'} loop ({res.meta['wall_s']} s)")
            print(res.table())
            md.append(md_table(res, res.meta["label"]))
            rows = res.summary_rows()
            lats = [l for r in res.runs for l in r.latencies_ms]
            summary[f"{variant}_{'closed' if closed else 'open'}"] = {
                "scenarios": len(rows), "pass": sum(1 for r in rows if r["verdict"] == "PASS"),
                "events": sum(r["events"] for r in rows), "tp": sum(r["tp"] for r in rows), "miss": sum(r["miss"] for r in rows),
                "fp": sum(r["fp"] for r in rows), "early": sum(r["early"] for r in rows), "tail": sum(r["tail"] for r in rows),
                "harm": sum(r["harm"] for r in rows), "worst_latency_ms": max(lats) if lats else None,
                "failing": [r["scenario"] for r in rows if r["verdict"] != "PASS"],
            }
            if closed:
                md.append("<details><summary>cuts per run</summary>\n")
                for run in res.runs:
                    md.append(f"- {run.scenario} s{run.seed}: cuts={[(round(t, 2), int(GEQ_BAND_HZ[b - 1]), g) for t, b, g in run.cuts]} "
                              f"miss={[round(e.freq_hz) for e in run.misses]} lat={run.latencies_ms}")
                md.append("\n</details>\n")
    with open(os.path.join(out_dir, "metrics_tables.md"), "w") as fh:
        fh.write("\n".join(md))
    with open(os.path.join(out_dir, "metrics.json"), "w") as fh:
        json.dump(summary, fh, indent=1)
    print(json.dumps(summary, indent=1))
    print("written", out_dir)


if __name__ == "__main__":
    main()
