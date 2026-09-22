"""Run the verifier's AV breakers (rtasim.scenarios_adversarial.VERIFIER_BREAKERS) open loop (watch_tag / ringout_tag as
run_detector_eval does, probes fed) and, for feedback scenes, CLOSED loop with introspection: detections + verdicts,
note_cut() log, whether the ring is still alive (regenerating / at its plateau) at the end, MODERATE publication latency,
PROGRAMME_PRESENT timeline.  cd $WT && PYTHONPATH=src:tests python -m rtasim.run_verifier_breakers [--seeds=1,2,3] [--json=out]
"""
from __future__ import annotations

import json
import sys
from typing import Any

from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig, Detection, NotchController

from rtasim import evaluate
from rtasim.harness import Det, _attribute, RunResult, ACTUATION_DELAY_FRAMES
from rtasim.physics import GEQ_BAND_HZ, RTA_BAND_HZ
from rtasim.render import Renderer, ring_episodes
from rtasim.run_detector_eval import make_factory, candidate_latency
from rtasim.scenarios import render
from rtasim.scenarios_adversarial import ADVERSARIAL, VERIFIER_BREAKERS


def closed_loop(cfg: DetectorConfig, sc, seed: int, mode: str) -> dict[str, Any]:
    fac, c = make_factory(cfg, sc, mode)
    det = fac(RTA_BAND_HZ)
    nc = NotchController(c, GEQ_BAND_HZ, lambda cur, new: None, budget=c.notch_budget_default)
    r = Renderer(sc.build(seed), seed, geq_band_hz=GEQ_BAND_HZ)
    pending = []
    dets: list[Det] = []
    cuts = []
    flags_tl = []
    while not r.done:
        while pending and pending[0][0] <= r.k:
            _, band, gain, ts0 = pending.pop(0)
            r.set_geq_gain(band, gain)
            cuts.append((round(ts0, 3), band, gain))
            det.note_cut(float(GEQ_BAND_HZ[band - 1]), float(gain), None)
        fr = r.step_frame()
        if fr is None:
            continue
        ts, vals = fr
        out = det.feed(vals, ts)
        flags_tl.append((round(ts, 2), sorted(det.flags)))
        for o in out:
            d = Det(ts=float(o.ts), band=int(o.band), freq_hz=float(o.freq_hz), confidence=float(o.confidence), level_db=o.level_db,
                    prominence_db=o.prominence_db, slope_db_per_s=o.slope_db_per_s, reasons=tuple(o.reasons), klass=o.klass)
            dets.append(d)
            dd = Detection(ts=d.ts, band=d.band, freq_hz=d.freq_hz, level_db=d.level_db or 0.0, prominence_db=d.prominence_db or 0.0,
                           slope_db_per_s=d.slope_db_per_s or 0.0, frames=0, confidence=d.confidence)
            notch = nc.plan(dd, 0, "sim")
            if notch is not None:
                pending.append((r.k + ACTUATION_DELAY_FRAMES, notch.band, notch.depth_db, ts))
    eps = ring_episodes(r)
    _attribute(dets, r, eps)
    rings_end = [{"label": rg.label, "level_db": round(rg.level_db, 1), "e_eff": round(rg.e_eff, 2), "display_db": round(rg.display_level_db(rg.t), 1),
                  "alive": rg.e_eff > -0.05 or rg.level_db > rg.sat_now - 6.0} for rg in r.scene.rings]
    cands_end = [{"band": cd.band, "freq": round(cd.freq_hz), "klass": cd.klass, "cut_verdict": cd.cut_verdict, "level": round(cd.level_db, 1),
                  "reasons": list(cd.reasons)} for cd in det.candidates if cd.klass not in ("TRACK",) or cd.emitted]
    return {"seed": seed, "detections": [d.to_dict() for d in dets], "cuts": cuts, "geq_end": dict(r.geq), "cut_log": det.cut_log,
            "rings_end": rings_end, "cands_end": cands_end}


def programme_timeline(cfg, sc, seed, mode):
    fac, _ = make_factory(cfg, sc, mode)
    det = fac(RTA_BAND_HZ)
    r = render(sc, seed)
    tl = []
    prev = None
    stationary = set()
    for ts, vals in r.frames:
        det.feed(vals, ts)
        p = "PROGRAMME_PRESENT" in det.flags
        if p != prev:
            tl.append((round(ts, 2), p))
            prev = p
        for c in det.candidates:
            if c.klass == "STATIONARY":
                stationary.add(round(c.freq_hz))
    return {"seed": seed, "programme_present_changes": tl, "flags_end": sorted(det.flags), "stationary_lines_hz": sorted(stationary)[:12]}


def main():
    opt = {a.split("=", 1)[0].lstrip("-"): (a.split("=", 1)[1] if "=" in a else True) for a in sys.argv[1:] if a.startswith("--")}
    names = [a for a in sys.argv[1:] if not a.startswith("--")] or list(VERIFIER_BREAKERS)
    seeds = tuple(int(x) for x in str(opt.get("seeds", "1,2,3")).split(","))
    cfg = DetectorConfig.from_descriptor(Descriptor.load())
    out: dict[str, Any] = {}
    for n in names:
        sc = ADVERSARIAL[n]
        modes = ["ringout_tag"] if sc.mode == "ring_out" else ["watch_tag", "ringout_tag"]
        rec: dict[str, Any] = {"title": sc.title, "expect": sc.expect}
        for mode in modes:
            fac, c = make_factory(cfg, sc, mode)
            res = evaluate(fac, [sc], seeds=seeds, closed_loop=False, notch_cfg=c)
            t = res.totals()
            row = res.summary_rows()[0]
            rec[f"open_{mode}"] = {"tp": t["tp"], "miss": t["miss"], "fp": t["fp"], "early": t["early"], "events": t["events"],
                                   "lat": [row["lat_min_ms"], row["lat_med_ms"], row["lat_max_ms"]],
                                   "dets": [[run.seed, d.to_dict()] for run in res.runs for d in run.detections]}
            print(f"== {n} open {mode}: ev {t['events']} TP {t['tp']} miss {t['miss']} FP {t['fp']} early {t['early']} lat {rec[f'open_{mode}']['lat']}", flush=True)
            for run in res.runs:
                for d in run.detections:
                    print(f"     s{run.seed} {d.verdict:5s} t={d.ts:.2f} {d.freq_hz:.0f}Hz {d.level_db:.1f}dB {d.klass} {'+'.join(d.reasons)}")
        if sc.has_feedback:
            m = modes[0]
            rec["moderate_pub"] = candidate_latency(cfg, sc, seeds, m)
            print("   MODERATE published ms:", [(x["seed"], x["published_ms"], x["fields_ok"]) for x in rec["moderate_pub"]["runs"]])
            rec["closed"] = [closed_loop(cfg, sc, s, m) for s in seeds]
            for cl in rec["closed"]:
                print(f"   closed s{cl['seed']}: cuts {cl['cuts']} geq_end {cl['geq_end']} rings_end {cl['rings_end']}")
                print(f"      cut_log {[(x['verdict'], x['drop_db'], x['bell_db']) for x in cl['cut_log']]}")
                print(f"      dets {[(d['ts'], d['verdict'], d['class'], d.get('reasons', [])[-1:] ) for d in cl['detections']]}")
                print(f"      cands_end {cl['cands_end'][:6]}")
        if sc.mode == "ring_out" or "contract" in sc.tags:
            rec["programme"] = [programme_timeline(cfg, sc, s, modes[0]) for s in seeds]
            for pt in rec["programme"]:
                print(f"   programme s{pt['seed']}: PROGRAMME_PRESENT changes {pt['programme_present_changes']} stationary {pt['stationary_lines_hz']} flags_end {pt['flags_end']}")
        out[n] = rec
    if "json" in opt:
        with open(str(opt["json"]), "w") as fh:
            json.dump(out, fh, indent=1, default=str)
        print("written", opt["json"])


if __name__ == "__main__":
    main()
