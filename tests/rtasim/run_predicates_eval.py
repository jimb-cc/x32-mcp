"""Evaluate the predicate detector over the rtasim corpus: modes × open/closed loop × seeds (1,2,3).

    cd $WT && PYTHONPATH=src:tests $PY $SP/reports/disc-predicates/run_eval.py [--quick] [--modes watch,ringout,...]
        [--scen A,B] [--seeds 1,2,3] [--out DIR] [--overrides key=val,...] [--an key=val,...]

Modes evaluated (each over ALL scenarios unless --scen):
  watch            cfg.mode='watch'   (default window 160 Hz .. 12.5 kHz)
  ringout          cfg.mode='ringout' (63 Hz ..); the scenario's own master step times (if it has ring_out steps)
                   are fed to det.note_gain_step() as cfs.py would when it writes the master (active probe)
  watch_lf         watch + lf_feedback_possible (40 Hz ..)
  ringout_lf       ringout + lf_feedback_possible
For every mode: open loop over all scenarios and closed loop over the feedback scenarios.
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
from rtasim.harness import Results
from rtasim.physics import GEQ_BAND_HZ

SP = "/private/tmp/claude-502/-Users-jimb-code-studio-intel-demo/d5626e47-bee8-4335-aa3f-27a50ae21455/scratchpad"
DEFAULT_OUT = SP + "/reports/disc-predicates"


class ProbedDetector(FeedbackDetector):
    """FeedbackDetector that is told about the server's own master steps at the right frame (what cfs.py's
    ring_out loop would do by calling note_gain_step right after _write_master)."""

    def __init__(self, cfg, band_hz, steps, **kw):
        super().__init__(cfg, band_hz, **kw)
        self._pending_steps = sorted(steps)          # [(t, delta)]

    def feed(self, values, ts):
        while self._pending_steps and self._pending_steps[0][0] <= ts + 1e-9:
            t, d = self._pending_steps.pop(0)
            self.note_gain_step(d, t)
        return super().feed(values, ts)


def step_list(name: str) -> list[tuple[float, float]]:
    sc = SCENARIOS[name]
    scene = sc.build(1)
    m = scene.master
    if m is None or sc.mode != "ring_out":
        return []
    pts = list(m.points)
    out = []
    for (t0, g0), (t1, g1) in zip(pts, pts[1:]):
        if abs(t1 - t0) < 1e-9 and abs(g1 - g0) > 1e-9:
            out.append((t1, g1 - g0))
    return out


def md_table(res, title: str) -> str:
    rows = res.summary_rows()
    out = [f"### {title}", "",
           "| scenario | ev | TP | miss | FP | early | tail | harm | dup | cuts | lat ms min/med/max | budget | FP at GEQ (Hz) | verdict |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lat = "–" if r["lat_med_ms"] is None else f"{r['lat_min_ms']:.0f}/{r['lat_med_ms']:.0f}/{r['lat_max_ms']:.0f}"
        fpb = ",".join(str(int(GEQ_BAND_HZ[b - 1])) for b in r["fp_geq_bands"][:8]) + (",…" if len(r["fp_geq_bands"]) > 8 else "")
        out.append(f"| {r['scenario']} | {r['events']} | {r['tp']} | {r['miss']} | {r['fp']} | {r['early']} | {r['tail']} | "
                   f"{r['harm']} | {r['dup']} | {r['cuts']} | {lat} | {r['budget_ms']:.0f} | {fpb} | {r['verdict']} {r['passed_seeds']}/{r['seeds']} |")
    tot = dict(ev=sum(r["events"] for r in rows), tp=sum(r["tp"] for r in rows), miss=sum(r["miss"] for r in rows),
               fp=sum(r["fp"] for r in rows), early=sum(r["early"] for r in rows), tail=sum(r["tail"] for r in rows),
               harm=sum(r["harm"] for r in rows), dup=sum(r["dup"] for r in rows), cuts=sum(r["cuts"] for r in rows),
               npass=sum(1 for r in rows if r["verdict"] == "PASS"))
    lats = [l for run in res.runs for l in run.latencies_ms]
    worst = max(lats) if lats else None
    out.append(f"| **{len(rows)} scenarios, {tot['npass']} pass** | {tot['ev']} | {tot['tp']} | {tot['miss']} | {tot['fp']} | "
               f"{tot['early']} | {tot['tail']} | {tot['harm']} | {tot['dup']} | {tot['cuts']} | worst {worst} | | | wall {res.meta.get('wall_s', 0):.1f} s |")
    return "\n".join(out) + "\n"


def run_mode(cfg: DetectorConfig, mode: str, names: list[str], seeds, closed: bool, an: dict | None) -> Results:
    lf = mode.endswith("_lf")
    m = "ringout" if mode.startswith("ringout") else "watch"
    c = dataclasses.replace(cfg, mode=m, lf_feedback_possible=lf)
    merged = Results(meta={"label": f"disc-predicates {mode} {'closed' if closed else 'open'} loop", "seeds": list(seeds),
                           "closed_loop": closed, "mode": mode, "scenarios": names, "analyser_overrides": an or {}})
    t0 = time.perf_counter()
    for name in names:
        steps = step_list(name) if m == "ringout" else []
        fac = (lambda bh, c=c, steps=steps: ProbedDetector(c, bh, steps))
        r = evaluate(fac, [name], seeds=seeds, closed_loop=closed, notch_cfg=c, analyser_overrides=an)
        merged.runs.extend(r.runs)
    merged.meta["wall_s"] = round(time.perf_counter() - t0, 2)
    merged.meta["detector_config"] = c.to_dict()
    return merged


def parse_kv(s: str) -> dict:
    out = {}
    for part in s.split(","):
        if not part:
            continue
        k, v = part.split("=", 1)
        try:
            out[k] = int(v)
        except ValueError:
            try:
                out[k] = float(v)
            except ValueError:
                out[k] = {"true": True, "false": False}.get(v.lower(), v)
    return out


def main() -> None:
    args = sys.argv[1:]
    opt = {a.split("=", 1)[0].lstrip("-"): (a.split("=", 1)[1] if "=" in a else True) for a in args if a.startswith("--")}
    out_dir = opt.get("out", DEFAULT_OUT)
    os.makedirs(out_dir, exist_ok=True)
    seeds = tuple(int(x) for x in str(opt.get("seeds", "1,2,3")).split(","))
    modes = str(opt.get("modes", "watch,ringout,watch_lf,ringout_lf")).split(",")
    names = str(opt["scen"]).split(",") if "scen" in opt else list(SCENARIOS)
    an = parse_kv(opt["an"]) if "an" in opt else None
    cfg = DetectorConfig.from_descriptor(Descriptor.load())
    if "overrides" in opt:
        cfg = dataclasses.replace(cfg, **parse_kv(opt["overrides"]))
    quick = bool(opt.get("quick", False))
    tag = str(opt.get("tag", ""))
    md = [f"# disc-predicates corpus results {tag}", "", f"generated {time.strftime('%Y-%m-%d %H:%M:%S')}; seeds {list(seeds)}; "
          f"analyser overrides {an or {}}", ""]
    allres = {}
    for mode in modes:
        for closed in ((False,) if quick else (False, True)):
            nm = names if not closed else [n for n in names if SCENARIOS[n].has_feedback]
            if not nm:
                continue
            res = run_mode(cfg, mode, nm, seeds, closed, an)
            key = f"{mode}_{'closed' if closed else 'open'}{('_' + tag) if tag else ''}"
            allres[key] = res
            print(f"\n== {res.meta['label']}  ({res.meta['wall_s']:.1f} s)")
            print(res.table())
            res.dump(os.path.join(out_dir, f"metrics_{key}.json"))
            md.append(md_table(res, res.meta["label"]))
            if closed:
                md.append("<details><summary>cuts per run</summary>\n")
                for run in res.runs:
                    md.append(f"- {run.scenario} s{run.seed}: cuts={[(round(t, 2), int(GEQ_BAND_HZ[b - 1]), g) for t, b, g in run.cuts]} "
                              f"miss={[round(e.freq_hz) for e in run.misses]} lat={run.latencies_ms}")
                md.append("\n</details>\n")
    with open(os.path.join(out_dir, f"tables{('_' + tag) if tag else ''}.md"), "w") as fh:
        fh.write("\n".join(md))
    # combined metrics.json (summaries of every run + per-run detections of the two primary modes)
    combined = {k: v.to_json() for k, v in allres.items()}
    with open(os.path.join(out_dir, f"metrics{('_' + tag) if tag else ''}.json"), "w") as fh:
        json.dump(combined, fh, indent=1, default=str)
    print("written", out_dir)


if __name__ == "__main__":
    main()
