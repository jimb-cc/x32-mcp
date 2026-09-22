"""Evaluate ``x32mcp.detector.FeedbackDetector`` (device.yaml config) over the rtasim corpora — the measuring instrument.

    PYTHONPATH=src:tests python -m rtasim.run_detector_eval SUITE [SUITE ...] [--out=DIR] [--tag=T]
        [--seeds=1,2,3] [--scen=A,B] [--modes=watch,ringout] [--an=key=val,...] [--overrides=key=val,...] [--quick]

Suites (each writes metrics_<key>.json + appends to tables_<tag>.md in --out, and prints a summary):
  main         main corpus (61), modes watch + ringout, open AND closed loop, seeds 1-3 (+ watch_lf open)
  holdout      main corpus, watch open, seeds 4-9
  sweeps       main corpus, watch open, seeds 1-3, once per CORPUS §5 analyser variant: attack_k 1.0, BQ, skirt 2 / 5,
               decay 4 s, release law 17, det RMS, noise x1.5, peak-hold 1 s, gain offset +12 / +24
  adversarial  the auditors' 55 breakers (rtasim.scenarios_adversarial.AUDIT_ADVERSARIAL), watch + ringout open, seeds 1-3;
               lf_feedback_possible follows each scenario's lf_optin declaration (what cfs would be told)
  breakers     the verifier's 13 AV breakers (VERIFIER_BREAKERS): watch_tag + ringout_tag open, and watch_tag CLOSED loop for the
               feedback scenes (ring alive at the end = fail), seeds 1-3
  adversarial_closed  all 68 breakers CLOSED loop (watch_tag): cuts per scene and their depth (what a wrong cut costs)
  cost         µs per frame (mean / p99 / max) on busy music scenarios
  all          everything above

Modes: watch | ringout | watch_lf | ringout_lf (LF window forced open) | watch_tag | ringout_tag (LF window per the
scenario's lf_optin tag). In every ring_out-mode run the scenario's own master step times (if it has ring_out steps) are
fed to det.note_gain_step() exactly as cfs.py does after each master write (the active probe); the detector never sees
scenario names, tags (except lf as said) or ground truth.
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
import sys
import time
from typing import Any, Iterable, Sequence

from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig, FeedbackDetector

from rtasim import SCENARIOS, Scenario, evaluate
from rtasim.harness import Results
from rtasim.physics import GEQ_BAND_HZ
from rtasim.scenarios_adversarial import ADVERSARIAL, AUDIT_ADVERSARIAL, VERIFIER_BREAKERS

# output directory: --out, else $RTASIM_EVAL_DIR, else ./rtasim-eval (metrics_<tag>_*.json, summary_<tag>.json, tables_<tag>.md)
DEFAULT_OUT = os.environ.get("RTASIM_EVAL_DIR", "rtasim-eval")

SWEEPS: dict[str, dict[str, Any]] = {
    "attack_k1": {"attack_k": 1.0},
    "bq": {"attack_model": "BQ"},
    "skirt2": {"skirt_order": 2.0},
    "skirt5": {"skirt_order": 5.0},
    "decay4": {"decay_s": 4.0},
    "rel17": {"release_law_db": 17.0},
    "rms": {"det": "RMS"},
    "noise15": {"noise_sd_scale": 1.5},
    "peakhold1": {"peak_hold_s": 1.0},
    "gain12": {"gain_offset_db": 12.0},
    "gain24": {"gain_offset_db": 24.0},
}


class ProbedDetector(FeedbackDetector):
    """FeedbackDetector that is told about the server's own master steps at the right frame (what cfs.py's ring_out
    loop does by calling note_gain_step right after _write_master)."""

    def __init__(self, cfg, band_hz, steps, **kw):
        super().__init__(cfg, band_hz, **kw)
        self._pending_steps = sorted(steps)          # [(t, delta)]

    def feed(self, values, ts):
        while self._pending_steps and self._pending_steps[0][0] <= ts + 1e-9:
            t, d = self._pending_steps.pop(0)
            self.note_gain_step(d, t)
        return super().feed(values, ts)


def step_list(sc: Scenario | str) -> list[tuple[float, float]]:
    """The ring_out master steps of a scenario as (t, delta) — only for scenarios whose mode is ring_out."""
    sc = SCENARIOS[sc] if isinstance(sc, str) else sc
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


def make_factory(cfg: DetectorConfig, sc: Scenario, mode: str):
    """Detector factory for one scenario under an eval mode (see module docstring)."""
    base = mode.split("_")[0]
    m = "ringout" if base == "ringout" else "watch"
    if mode.endswith("_lf"):
        lf = True
    elif mode.endswith("_tag"):
        lf = bool(sc.lf_optin)
    else:
        lf = False
    c = dataclasses.replace(cfg, mode=m, lf_feedback_possible=lf)
    steps = step_list(sc) if m == "ringout" else []
    return (lambda bh, c=c, steps=steps: ProbedDetector(c, bh, steps)), c


def run_mode(cfg: DetectorConfig, mode: str, scens: Sequence[Scenario], seeds, closed: bool, an: dict | None, label: str) -> Results:
    merged = Results(meta={"label": label, "seeds": list(seeds), "closed_loop": closed, "mode": mode,
                           "scenarios": [s.name for s in scens], "analyser_overrides": an or {}})
    t0 = time.perf_counter()
    c_last = cfg
    for sc in scens:
        fac, c_last = make_factory(cfg, sc, mode)
        r = evaluate(fac, [sc], seeds=seeds, closed_loop=closed, notch_cfg=c_last, analyser_overrides=an)
        merged.runs.extend(r.runs)
    merged.meta["wall_s"] = round(time.perf_counter() - t0, 2)
    merged.meta["detector_config"] = c_last.to_dict()
    return merged


# -- reporting ---------------------------------------------------------------------------------------
def md_table(res: Results, title: str, verdicts: dict[str, str] | None = None) -> str:
    rows = res.summary_rows()
    out = [f"### {title}", "",
           "| scenario | ev | TP | miss | FP | early | tail | harm | cuts | alive | lat ms min/med/max | budget | FP at GEQ (Hz) | verdict |"
           + (" note |" if verdicts else ""),
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|" + ("---|" if verdicts else "")]
    for r in rows:
        lat = "–" if r["lat_med_ms"] is None else f"{r['lat_min_ms']:.0f}/{r['lat_med_ms']:.0f}/{r['lat_max_ms']:.0f}"
        fpb = ",".join(str(int(GEQ_BAND_HZ[b - 1])) for b in r["fp_geq_bands"][:8]) + (",…" if len(r["fp_geq_bands"]) > 8 else "")
        line = (f"| {r['scenario']} | {r['events']} | {r['tp']} | {r['miss']} | {r['fp']} | {r['early']} | {r['tail']} | "
                f"{r['harm']} | {r['cuts']} | {r['alive']} | {lat} | {r['budget_ms']:.0f} | {fpb} | {r['verdict']} {r['passed_seeds']}/{r['seeds']} |")
        if verdicts:
            line += f" {verdicts.get(r['scenario'], '')} |"
        out.append(line)
    t = res.totals()
    out.append(f"| **{t['scenarios']} scenarios, {t['pass']} pass** | {t['events']} | {t['tp']} | {t['miss']} | {t['fp']} | "
               f"{t['early']} | {t['tail']} | {t['harm']} | {t['cuts']} | {t['alive']} | p50 {t['lat_p50']} p90 {t['lat_p90']} max {t['lat_max']}; "
               f"≤300 ms {t['le300']}/{t['lat_n']} | | | wall {res.meta.get('wall_s', 0):.1f} s |" + (" |" if verdicts else ""))
    return "\n".join(out) + "\n"


def one_line(res: Results) -> str:
    t = res.totals()
    return (f"{res.meta.get('label','')}: {t['scenarios']} scen {t['pass']} pass; ev {t['events']} TP {t['tp']} miss {t['miss']} "
            f"FP {t['fp']} early {t['early']} tail {t['tail']} harm {t['harm']} cuts {t['cuts']} alive {t['alive']}; lat p50/p90/max "
            f"{t['lat_p50']}/{t['lat_p90']}/{t['lat_max']} ≤300 {t['le300']}/{t['lat_n']}; FP in {t['fp_scenarios']}; "
            f"miss in {t['miss_scenarios']}{'; ALIVE in ' + str(t['alive_scenarios']) if t['alive_scenarios'] else ''} ({res.meta.get('wall_s', 0):.0f} s)")


def first_detection_notes(res: Results) -> dict[str, str]:
    """Per scenario: what the first detection (any seed) looked like — reasons/class — for the adversarial verdict column."""
    notes: dict[str, str] = {}
    for run in res.runs:
        for d in run.detections:
            if run.scenario in notes:
                break
            notes[run.scenario] = f"s{run.seed} t={d.ts:.2f} {d.verdict} {d.klass or ''} {'+'.join(d.reasons[-3:]) if d.reasons else ''}"
            break
    return notes


# -- MODERATE-candidate publication latency (tier-B hook check) ----------------------------------------
def candidate_latency(cfg: DetectorConfig, sc: Scenario, seeds, mode: str = "watch_tag") -> dict[str, Any]:
    """For each visible ring: ms from t_prom until the detector first lists a MODERATE-or-better candidate within ±1 band
    (what cfs would publish as cfs.candidate / act on as tier B). Independent of emission."""
    from rtasim.render import ring_episodes
    from rtasim.scenarios import render
    from rtasim.physics import RTA_BAND_HZ
    out = []
    for seed in seeds:
        fac, _ = make_factory(cfg, sc, mode)
        det = fac(RTA_BAND_HZ)
        r = render(sc, seed)
        eps = [e for e in ring_episodes(r) if e.visible]
        first: dict[int, float] = {}
        fields_ok = True
        for ts, vals in r.frames:
            det.feed(vals, ts)
            for c in det.candidates:
                if c.klass in ("MODERATE", "STRONG", "PROBE") and not c.misses:
                    for i, e in enumerate(eps):
                        if i in first:
                            continue
                        if abs(c.band - e.band) <= 1 and e.t_onset - 1e-9 <= ts <= e.t_end + 1.0:
                            first[i] = round((ts - e.t_prom) * 1000.0, 1)
                            d = c.to_dict()
                            for k in ("class", "level_db", "prominence_db", "excess_db", "age_s", "reasons", "freq_hz"):
                                if k not in d:
                                    fields_ok = False
        out.append({"seed": seed, "events": len(eps), "published_ms": [first.get(i) for i in range(len(eps))], "fields_ok": fields_ok})
    return {"scenario": sc.name, "runs": out}


# -- cost ------------------------------------------------------------------------------------------------
def cost(cfg: DetectorConfig, names=("M1_loud_band_wedge_ring", "X7_plateaued_ring_under_music_from_t0", "X16_wedge_ring_315Hz_loud_band",
                                    "S21_synth_pad_swell", "X18_applause_crowd_30s", "M2_quiet_music_ringout_two_modes", "S20_song_start_stop_crowd"),
         seed: int = 1) -> dict[str, Any]:
    from rtasim import frames
    from rtasim.physics import RTA_BAND_HZ
    per: list[float] = []
    by: dict[str, dict[str, float]] = {}
    for n in names:
        sc = SCENARIOS[n]
        fac, _ = make_factory(cfg, sc, "ringout_tag" if sc.mode == "ring_out" else "watch_tag")
        det = fac(RTA_BAND_HZ)
        fr = frames(n, seed)
        ts_ = []
        for ts, vals in fr:
            t0 = time.perf_counter()
            det.feed(vals, ts)
            ts_.append((time.perf_counter() - t0) * 1e6)
        per.extend(ts_)
        s = sorted(ts_)
        by[n] = {"mean_us": round(sum(s) / len(s), 1), "p99_us": round(s[int(0.99 * (len(s) - 1))], 1), "max_us": round(s[-1], 1)}
    s = sorted(per)
    return {"frames": len(s), "mean_us": round(sum(s) / len(s), 1), "median_us": round(s[len(s) // 2], 1),
            "p99_us": round(s[int(0.99 * (len(s) - 1))], 1), "max_us": round(s[-1], 1), "by_scenario": by}


# -- CLI -------------------------------------------------------------------------------------------------
def parse_kv(s: str) -> dict:
    out: dict[str, Any] = {}
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


def main(argv: list[str] | None = None) -> dict[str, Results]:
    args = sys.argv[1:] if argv is None else argv
    suites = [a for a in args if not a.startswith("--")] or ["main"]
    opt = {a.split("=", 1)[0].lstrip("-"): (a.split("=", 1)[1] if "=" in a else True) for a in args if a.startswith("--")}
    out_dir = str(opt.get("out", DEFAULT_OUT))
    os.makedirs(out_dir, exist_ok=True)
    tag = str(opt.get("tag", "run"))
    seeds_opt = tuple(int(x) for x in str(opt["seeds"]).split(",")) if "seeds" in opt else None
    an_extra = parse_kv(str(opt["an"])) if "an" in opt else None
    cfg = DetectorConfig.from_descriptor(Descriptor.load())
    if "overrides" in opt:
        cfg = dataclasses.replace(cfg, **parse_kv(str(opt["overrides"])))
    quick = bool(opt.get("quick", False))
    scen_filter = str(opt["scen"]).split(",") if "scen" in opt else None
    if "all" in suites:
        suites = ["main", "holdout", "sweeps", "adversarial", "breakers", "cost"]

    md: list[str] = [f"# detector eval `{tag}`", "", f"generated {time.strftime('%Y-%m-%d %H:%M:%S')}; extra analyser overrides {an_extra or {}}", ""]
    summary: dict[str, Any] = {}
    allres: dict[str, Results] = {}

    def record(key: str, res: Results, verdicts: dict[str, str] | None = None) -> None:
        allres[key] = res
        print("== " + one_line(res), flush=True)
        res.dump(os.path.join(out_dir, f"metrics_{tag}_{key}.json"))
        md.append(md_table(res, f"{key}: {res.meta['label']}", verdicts))
        summary[key] = res.totals()

    main_scens = [SCENARIOS[n] for n in (scen_filter or SCENARIOS) if n in SCENARIOS]
    adv_scens = [ADVERSARIAL[n] for n in (scen_filter or AUDIT_ADVERSARIAL) if n in AUDIT_ADVERSARIAL]
    av_scens = [ADVERSARIAL[n] for n in (scen_filter or VERIFIER_BREAKERS) if n in VERIFIER_BREAKERS]

    for suite in suites:
        if suite == "main":
            seeds = seeds_opt or (1, 2, 3)
            modes = str(opt.get("modes", "watch,ringout,watch_lf")).split(",")
            for mode in modes:
                for closed in ((False,) if (quick or mode == "watch_lf") else (False, True)):
                    scens = main_scens if not closed else [s for s in main_scens if s.has_feedback]
                    if not scens:
                        continue
                    res = run_mode(cfg, mode, scens, seeds, closed, an_extra, f"main {mode} {'closed' if closed else 'open'} seeds {list(seeds)}")
                    record(f"main_{mode}_{'closed' if closed else 'open'}", res)
        elif suite == "holdout":
            seeds = seeds_opt or (4, 5, 6, 7, 8, 9)
            for mode in str(opt.get("modes", "watch")).split(","):
                res = run_mode(cfg, mode, main_scens, seeds, False, an_extra, f"holdout {mode} open seeds {list(seeds)}")
                record(f"holdout_{mode}_open", res)
        elif suite == "sweeps":
            seeds = seeds_opt or (1, 2, 3)
            which = str(opt.get("sweeps", ",".join(SWEEPS))).split(",")
            for sw in which:
                an = dict(SWEEPS[sw])
                if an_extra:
                    an.update(an_extra)
                for mode in str(opt.get("modes", "watch")).split(","):
                    res = run_mode(cfg, mode, main_scens, seeds, False, an, f"sweep {sw} {an} {mode} open seeds {list(seeds)}")
                    record(f"sweep_{sw}_{mode}_open", res)
        elif suite == "adversarial":
            seeds = seeds_opt or (1, 2, 3)
            for mode in str(opt.get("modes", "watch_tag,ringout_tag")).split(","):
                res = run_mode(cfg, mode, adv_scens, seeds, False, an_extra, f"adversarial {mode} open seeds {list(seeds)}")
                record(f"adv_{mode}_open", res, first_detection_notes(res))
            # tier-B hook: MODERATE publication latency for every adversarial ring
            pub = [candidate_latency(cfg, sc, seeds) for sc in adv_scens if sc.has_feedback]
            summary["adv_candidate_publication"] = pub
            md.append("### adversarial: first MODERATE-or-better candidate within ±1 band, ms after t_prom (per seed)\n")
            md.append("| scenario | seed: published ms per event | fields ok |\n|---|---|---|")
            for p in pub:
                cells = "; ".join(f"s{r['seed']}: {r['published_ms']}" for r in p["runs"])
                md.append(f"| {p['scenario']} | {cells} | {all(r['fields_ok'] for r in p['runs'])} |")
            md.append("")
        elif suite == "adversarial_closed":
            # closed loop over ALL breakers (programme scenes included): how many cuts a wrong detection costs and how deep
            # they go once note_cut() verdicts gate the deepening (the FP-depth figures)
            seeds = seeds_opt or (1, 2, 3)
            for mode in str(opt.get("modes", "watch_tag")).split(","):
                scens = [s for s in adv_scens + av_scens if not (s.mode == "ring_out" and mode.startswith("watch"))]
                res = run_mode(cfg, mode, scens, seeds, True, an_extra, f"all breakers {mode} CLOSED seeds {list(seeds)}")
                record(f"advall_{mode}_closed", res)
        elif suite == "breakers":
            seeds = seeds_opt or (1, 2, 3)
            for mode in str(opt.get("modes", "watch_tag,ringout_tag")).split(","):
                scens = [s for s in av_scens if not (s.mode == "ring_out" and mode.startswith("watch"))]
                res = run_mode(cfg, mode, scens, seeds, False, an_extra, f"verifier breakers {mode} open seeds {list(seeds)}")
                record(f"av_{mode}_open", res, first_detection_notes(res))
            fb = [s for s in av_scens if s.has_feedback]
            res = run_mode(cfg, "watch_tag", [s for s in fb if s.mode != "ring_out"], seeds, True, an_extra, f"verifier breakers watch_tag CLOSED seeds {list(seeds)}")
            record("av_watch_tag_closed", res)
            res = run_mode(cfg, "ringout_tag", [s for s in fb if s.mode == "ring_out"], seeds, True, an_extra, f"verifier breakers ringout_tag CLOSED seeds {list(seeds)}")
            record("av_ringout_tag_closed", res)
            pub = [candidate_latency(cfg, sc, seeds, "ringout_tag" if sc.mode == "ring_out" else "watch_tag") for sc in fb]
            summary["av_candidate_publication"] = pub
            md.append("### verifier breakers: first MODERATE-or-better candidate within ±1 band, ms after t_prom (per seed)\n")
            md.append("| scenario | seed: published ms per event | fields ok |\n|---|---|---|")
            for p in pub:
                cells = "; ".join(f"s{r['seed']}: {r['published_ms']}" for r in p["runs"])
                md.append(f"| {p['scenario']} | {cells} | {all(r['fields_ok'] for r in p['runs'])} |")
            md.append("")
        elif suite == "cost":
            c = cost(cfg)
            summary["cost"] = c
            print("== cost", json.dumps(c), flush=True)
            md.append(f"### cost per frame\n\n`{json.dumps(c)}`\n")
        else:
            raise SystemExit(f"unknown suite {suite!r}")

    with open(os.path.join(out_dir, f"tables_{tag}.md"), "w") as fh:
        fh.write("\n".join(md))
    with open(os.path.join(out_dir, f"summary_{tag}.json"), "w") as fh:
        json.dump(summary, fh, indent=1, default=str)
    print("written", out_dir, tag)
    return allres


if __name__ == "__main__":
    main()
