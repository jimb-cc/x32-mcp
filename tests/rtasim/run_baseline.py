"""Baseline driver (corpus-critic): run the CURRENT x32mcp FeedbackDetector (device.yaml config) over the whole corpus,
3 seeds, open loop and closed loop, plus the pre-M7 variant (override_prominence_db = 0), and write JSON + a markdown
table per run.

    cd <worktree> && PYTHONPATH=src:tests python -m rtasim.run_baseline [out_dir]
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import time

from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig, FeedbackDetector

from rtasim import SCENARIOS, evaluate
from rtasim.physics import GEQ_BAND_HZ

DEFAULT_OUT = "ringout_reports/rtasim"  # override with an argument or RTASIM_REPORT_DIR


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


def main() -> None:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("RTASIM_REPORT_DIR", DEFAULT_OUT)
    os.makedirs(out_dir, exist_ok=True)
    cfg = DetectorConfig.from_descriptor(Descriptor.load())
    cfg0 = dataclasses.replace(cfg, override_prominence_db=0.0)
    seeds = (1, 2, 3)
    fb = [n for n, s in SCENARIOS.items() if s.has_feedback]
    runs = [
        ("baseline_current_detector", "current FeedbackDetector (device.yaml), open loop", cfg, None, False),
        ("baseline_current_detector_closed_loop", "current FeedbackDetector (device.yaml), closed loop (NotchController, 1-frame actuation delay)", cfg, fb, True),
        ("baseline_preM7_no_override", "pre-M7 detector (override_prominence_db=0), open loop", cfg0, None, False),
        ("baseline_preM7_no_override_closed_loop", "pre-M7 detector (override_prominence_db=0), closed loop", cfg0, fb, True),
    ]
    md = ["# Current-detector baselines over the extended corpus (corpus-critic)", "",
          f"generated {time.strftime('%Y-%m-%d %H:%M:%S')} — seeds {list(seeds)}; detector config from device.yaml: "
          f"prominence {cfg.prominence_db}/±{cfg.neighbour_bins}, min_level {cfg.min_level_db}, persistence {cfg.persistence_frames}, "
          f"growth {cfg.growth_min_db_per_s}..{cfg.growth_max_db_per_s} ref {cfg.growth_ref_db_per_s}, weights "
          f"{cfg.w_prominence}/{cfg.w_persistence}/{cfg.w_growth}, threshold {cfg.confidence_threshold}, override "
          f"{cfg.override_prominence_db} dB / {cfg.override_persistence_frames} fr, budget {cfg.notch_budget_default}", ""]
    for fname, label, c, names, closed in runs:
        t0 = time.perf_counter()
        res = evaluate(lambda bh, c=c: FeedbackDetector(c, bh), names, seeds=seeds, closed_loop=closed, notch_cfg=c, label=label)
        res.meta["detector_config"] = c.to_dict()
        res.dump(os.path.join(out_dir, fname + ".json"))
        print(f"\n== {label}  ({time.perf_counter() - t0:.1f} s)")
        print(res.table())
        md.append(md_table(res, label))
        if closed:
            md.append("<details><summary>cuts per run</summary>\n")
            for run in res.runs:
                md.append(f"- {run.scenario} s{run.seed}: cuts={[(round(t, 2), int(GEQ_BAND_HZ[b - 1]), g) for t, b, g in run.cuts]} "
                          f"miss={[round(e.freq_hz) for e in run.misses]} lat={run.latencies_ms}")
            md.append("\n</details>\n")
    with open(os.path.join(out_dir, "baseline_tables.md"), "w") as fh:
        fh.write("\n".join(md))
    print("written", out_dir)


if __name__ == "__main__":
    main()
