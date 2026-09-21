"""Corpus driver for the sequential-evidence discriminator (disc-sequential-evidence).

Runs ``x32mcp.detector.FeedbackDetector`` over the whole rtasim corpus exactly as ``rtasim.harness`` does
(``run_one`` per scenario × seed, open and closed loop), but builds the detector per scenario so it can be
told the session facts ``cfs`` would tell it: the mode (``Scenario.mode``), the LF opt-in (``Scenario.lf_optin``)
and — in ring-out — the server's own master steps (``Scene.master.step_times()`` → ``note_gain_step``) and
its own cuts (``note_cut`` for every emitted detection, as ``cfs._notch`` would).

    cd <worktree> && PYTHONPATH=src:tests python -m rtasim.run_disc [out_dir] [--modes native,watch,ringout] [--seeds 1,2,3]

Writes ``metrics.json`` (all runs, harness JSON per configuration) and ``metrics_tables.md`` to out_dir.
"""

from __future__ import annotations

import json
import os
import sys
import time
from statistics import median
from typing import Any, Sequence

from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig, FeedbackDetector

from rtasim import SCENARIOS
from rtasim.harness import Results, run_one
from rtasim.physics import GEQ_BAND_HZ

DEFAULT_OUT = ("/private/tmp/claude-502/-Users-jimb-code-studio-intel-demo/d5626e47-bee8-4335-aa3f-27a50ae21455/"
               "scratchpad/reports/disc-sequential-evidence")


class SessionDetector:
    """What cfs would build for one session: a FeedbackDetector in the session's mode, fed the server's own
    master steps (ring-out) just before the frame in which they land, and told about its own cuts."""

    def __init__(self, cfg: DetectorConfig, band_hz: Sequence[float], *, mode: str, lf: bool,
                 step_times: Sequence[float] = (), step_db: float = 1.0, use_probe: bool = True, use_note_cut: bool = True):
        self.det = FeedbackDetector(cfg, band_hz, mode=mode, lf_feedback_possible=lf)
        self.steps = sorted(step_times) if use_probe else []
        self.step_db = step_db
        self.i = 0
        self.use_note_cut = use_note_cut

    def feed(self, values, ts):
        while self.i < len(self.steps) and self.steps[self.i] <= ts + 1e-9:
            self.det.note_gain_step(self.step_db, self.steps[self.i])
            self.i += 1
        out = self.det.feed(values, ts)
        if self.use_note_cut:
            for d in out:
                self.det.note_cut(d.freq_hz, -3.0, ts)
        return out

    @property
    def candidates(self):
        return self.det.candidates


def make_factory(cfg: DetectorConfig, name: str, seed: int, mode_override: str | None, *, probe: bool = True, note_cut: bool = True):
    sc = SCENARIOS[name]
    mode = mode_override or ("ringout" if sc.mode == "ring_out" else "watch")
    steps: list[float] = []
    step_db = 1.0
    if mode == "ringout":
        scene = sc.build(seed)
        if scene.master is not None and sc.mode == "ring_out":
            steps = list(scene.master.step_times())
            pts = scene.master.points
            for a, b in zip(pts, pts[1:]):
                if b[0] == a[0] and b[1] != a[1]:
                    step_db = b[1] - a[1]
                    break

    def factory(band_hz):
        return SessionDetector(cfg, band_hz, mode=mode, lf=sc.lf_optin, step_times=steps, step_db=step_db,
                               use_probe=probe, use_note_cut=note_cut)
    return factory


def evaluate_modeaware(cfg: DetectorConfig, names: Sequence[str], seeds: Sequence[int], *, closed_loop: bool,
                       mode_override: str | None, label: str, probe: bool = True, note_cut: bool = True) -> Results:
    t0 = time.perf_counter()
    res = Results(meta={"label": label, "seeds": list(seeds), "closed_loop": closed_loop, "mode_override": mode_override,
                        "probe": probe, "note_cut": note_cut, "scenarios": list(names)})
    for n in names:
        for s in seeds:
            res.runs.append(run_one(make_factory(cfg, n, s, mode_override, probe=probe, note_cut=note_cut), n, s,
                                    closed_loop=closed_loop, notch_cfg=cfg))
    res.meta["wall_s"] = round(time.perf_counter() - t0, 2)
    return res


def md_table(res: Results, title: str) -> str:
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
               harm=sum(r["harm"] for r in rows), dup=sum(r["dup"] for r in rows), npass=sum(1 for r in rows if r["verdict"] == "PASS"))
    lats = [l for run in res.runs for l in run.latencies_ms]
    out.append(f"| **{len(rows)} scenarios, {tot['npass']} pass** | {tot['ev']} | {tot['tp']} | {tot['miss']} | {tot['fp']} | "
               f"{tot['early']} | {tot['tail']} | {tot['harm']} | {tot['dup']} | | "
               f"{(str(round(min(lats))) + '/' + str(round(median(lats))) + '/' + str(round(max(lats)))) if lats else '–'} | | | wall {res.meta.get('wall_s', 0):.1f} s |")
    return "\n".join(out) + "\n"


def totals(res: Results) -> dict[str, Any]:
    rows = res.summary_rows()
    lats = sorted(l for run in res.runs for l in run.latencies_ms)
    over = [(run.scenario, run.seed, l) for run in res.runs for l in run.latencies_ms if l > run.latency_budget_ms]
    return {
        "scenarios": len(rows), "pass": sum(1 for r in rows if r["verdict"] == "PASS"),
        "events": sum(r["events"] for r in rows), "tp": sum(r["tp"] for r in rows), "miss": sum(r["miss"] for r in rows),
        "fp": sum(r["fp"] for r in rows), "early": sum(r["early"] for r in rows), "tail": sum(r["tail"] for r in rows),
        "harm": sum(r["harm"] for r in rows), "dup": sum(r["dup"] for r in rows),
        "lat_ms": {"min": lats[0] if lats else None, "p50": lats[len(lats) // 2] if lats else None,
                   "p90": lats[int(0.9 * len(lats))] if lats else None, "max": lats[-1] if lats else None,
                   "over_300": sum(1 for l in lats if l > 300), "over_budget": over},
        "failing": [(r["scenario"], r["passed_seeds"], r["fp"], r["miss"], r["lat_max_ms"]) for r in rows if r["verdict"] != "PASS"],
    }


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out_dir = args[0] if args else os.environ.get("RTASIM_REPORT_DIR", DEFAULT_OUT)
    os.makedirs(out_dir, exist_ok=True)
    opts = {a.split("=", 1)[0][2:]: (a.split("=", 1)[1] if "=" in a else "1") for a in sys.argv[1:] if a.startswith("--")}
    modes = opts.get("modes", "native,watch,ringout").split(",")
    seeds = tuple(int(x) for x in opts.get("seeds", "1,2,3").split(","))
    only = opts.get("only")
    quick = "quick" in opts
    cfg = DetectorConfig.from_descriptor(Descriptor.load())
    names = list(SCENARIOS) if not only else [n for n in SCENARIOS if any(tok in n for tok in only.split(","))]
    fb = [n for n in names if SCENARIOS[n].has_feedback]
    all_json: dict[str, Any] = {"detector": "disc-sequential-evidence", "config": cfg.to_dict(), "runs": {}}
    md = ["# disc-sequential-evidence — corpus metrics", "",
          f"generated {time.strftime('%Y-%m-%d %H:%M:%S')}; seeds {list(seeds)}; config from device.yaml + defaults "
          f"(emit {cfg.emit_llr_watch}/{cfg.emit_llr_ringout} nats watch/ring-out, dismiss {cfg.dismiss_llr_watch}/{cfg.dismiss_llr_ringout}, "
          f"window {cfg.f_low_watch_hz}/{cfg.f_low_ringout_hz}/{cfg.f_low_lf_optin_hz}–{cfg.f_high_hz} Hz, track ≥{cfg.track_prominence_db} dB, "
          f"emit ≥{cfg.prominence_db} dB, min_level {cfg.min_level_db})", ""]
    configs = []
    for m in modes:
        mo = None if m == "native" else m
        lab = {"native": "scenario's own mode (watch / ring-out as cfs would arm it; probe + note_cut in ring-out)",
               "watch": "EVERY scenario forced to watch mode (no probe)",
               "ringout": "EVERY scenario forced to ring-out mode (probe where the scene has server steps)"}[m]
        configs.append((f"{m}_open", lab + ", open loop", mo, names, False))
        if not quick:
            configs.append((f"{m}_closed", lab + ", CLOSED loop (NotchController −3 dB steps, 1-frame actuation)", mo, fb, True))
    for key, label, mo, nm, closed in configs:
        t0 = time.perf_counter()
        res = evaluate_modeaware(cfg, nm, seeds, closed_loop=closed, mode_override=mo, label=label)
        res.meta["detector_config"] = cfg.to_dict()
        all_json["runs"][key] = res.to_json()
        all_json["runs"][key]["totals"] = totals(res)
        print(f"\n== {label}  ({time.perf_counter() - t0:.1f} s)")
        print(res.table())
        print("totals:", json.dumps(all_json["runs"][key]["totals"], default=str))
        md.append(md_table(res, label))
        if closed:
            md.append("<details><summary>cuts per run</summary>\n")
            for run in res.runs:
                md.append(f"- {run.scenario} s{run.seed}: cuts={[(round(t, 2), int(GEQ_BAND_HZ[b - 1]), g) for t, b, g in run.cuts]} "
                          f"miss={[round(e.freq_hz) for e in run.misses]} lat={run.latencies_ms}")
            md.append("\n</details>\n")
    suffix = opts.get("tag", "")
    with open(os.path.join(out_dir, f"metrics{suffix}.json"), "w") as fh:
        json.dump(all_json, fh, indent=1, default=str)
    with open(os.path.join(out_dir, f"metrics_tables{suffix}.md"), "w") as fh:
        fh.write("\n".join(md))
    print("written", out_dir)


if __name__ == "__main__":
    main()
