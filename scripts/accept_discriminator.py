#!/usr/bin/env python
"""Acceptance gate for a CFS² feedback discriminator, over the offline corpus in tests/rtasim.

Pre-registered before any candidate existed (2026-09-21), so the bar cannot move after the numbers
are in. Every gate below is one of the ways CORPUS.md §7 says a design can pass by fitting the
simulator rather than the physics; the gate turns each into a condition the candidate must hold.

    python scripts/accept_discriminator.py                     # the shipped detector: must REJECT
    python scripts/accept_discriminator.py --factory pkg.mod:make_detector
    python scripts/accept_discriminator.py --quick             # ~2 min smoke version
    python scripts/accept_discriminator.py --replay-only       # G5 alone (seconds)

A factory is ``callable(band_hz: Sequence[float]) -> detector`` where the detector exposes
``feed(values_db: list[float], ts: float) -> iterable`` of objects with ``.ts .band .freq_hz
.confidence`` (the harness protocol, tests/rtasim/harness.py). It receives NOTHING about the
scenario: no tags, no ``lf_optin``, no name.

Gates (all must hold for ACCEPT):
  G1  hold-out seeds 4..9, open loop, all 61 scenarios: FP = 0, miss = 0, latency <= budget   (§7.9)
  G2  same, with the analyser's unmeasured constants moved: attack_k 0.32 and 1.0, skirt_order
      2 and 5, a slow display (decay 16 s / 240 dB release), peak-hold 2 s, RMS detector       (§7.1)
  G3  EARLY_CREDIT_S = 0: watch-mode latencies with no credit for pre-onset detections         (§7.7)
  G4  closed loop through the real NotchController, the 32 feedback scenarios                  (§7.8)
  G5  real programme replay (added 2026-09-25, before any candidate answered it): every desk-logged
      programme file docs/research/data/programme_*.jsonl.gz, replayed frame for frame through the
      candidate in watch AND ring-out mode: 0 emissions. The files are real music through the real
      analyser (meters.md 2026-09-25 item 2); the shipped detector gives 20 on the first one.
Reported, not gated:
  R1  the irreducible pairs (X4/X7/X20/S2a): passing all four with one threshold is a corpus
      regularity, not a law — vary levels before believing it                                  (§7.10)
  R2  the price: FP by GEQ band, EARLY/TAIL/HARM counts, and the detector's wall time

Exit 0 = ACCEPT, 1 = REJECT, 2 = could not run.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import inspect
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from rtasim import harness  # noqa: E402
from rtasim.harness import evaluate  # noqa: E402
from rtasim.scenarios import SCENARIOS  # noqa: E402
from x32mcp.descriptor import Descriptor  # noqa: E402
from x32mcp.detector import DetectorConfig, FeedbackDetector  # noqa: E402

HOLD_OUT_SEEDS = (4, 5, 6, 7, 8, 9)
QUICK_SEEDS = (4, 5)
IRREDUCIBLE = ("X4_sine_lead_portamento", "X7_plateaued_ring_under_music_from_t0",
               "X20_mains_hum_and_hvac_whine", "S2a_established_ring_8k")

# CORPUS.md §5: the constants a candidate must be indifferent to. Defaults are attack_k 0.5,
# skirt_order 3, decay 0.25 s, peak-hold off, PEAK.
SWEEPS: list[tuple[str, dict[str, Any]]] = [
    ("attack_k=0.32 (faster LF attack)", {"attack_k": 0.32}),
    ("attack_k=1.0 (slower LF attack)", {"attack_k": 1.0}),
    ("skirt_order=2 (shallow skirts, 24 dB prominence ceiling)", {"skirt_order": 2}),
    ("skirt_order=5 (steep skirts, 60 dB ceiling)", {"skirt_order": 5}),
    ("decay_s=16, release 240 dB (slow display release)", {"decay_s": 16.0, "release_law_db": 240.0}),
    ("peak_hold_s=2", {"peak_hold_s": 2.0}),
    ("det=RMS", {"det": "RMS"}),
]
QUICK_SWEEPS = [SWEEPS[1], SWEEPS[3]]


def load_factory(spec: str) -> tuple[Callable[[Sequence[float]], Any], DetectorConfig, str]:
    """``current`` | ``preM7`` | ``module:callable``. Returns (factory, notch policy cfg, label)."""
    cfg = DetectorConfig.from_descriptor(Descriptor.load())
    if spec == "current":
        return (lambda bh: FeedbackDetector(cfg, bh)), cfg, "shipped FeedbackDetector (device.yaml)"
    if spec == "preM7":
        c0 = dataclasses.replace(cfg, override_prominence_db=0.0)
        return (lambda bh: FeedbackDetector(c0, bh)), cfg, "pre-M7 FeedbackDetector (override off)"
    if ":" not in spec:
        raise SystemExit(f"--factory must be 'current', 'preM7' or 'module:callable', got {spec!r}")
    mod, _, attr = spec.partition(":")
    obj = getattr(importlib.import_module(mod), attr)
    if not callable(obj):
        raise SystemExit(f"{spec} is not callable")
    params = [p for p in inspect.signature(obj).parameters.values()
              if p.default is inspect.Parameter.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    if len(params) != 1:
        print(f"WARNING: factory {spec} takes {len(params)} required positional args; the harness passes exactly one (band_hz). "
              "A factory that needs anything else is being told something about the scenario.", file=sys.stderr)
    return obj, cfg, spec


def _evaluate(factory, names, *, seeds, closed_loop, notch_cfg, label, overrides=None, actuator="geq"):
    """evaluate() with only the keyword arguments this harness version accepts."""
    accepted = inspect.signature(evaluate).parameters
    kw: dict[str, Any] = {"seeds": seeds, "closed_loop": closed_loop}
    if actuator != "geq":
        if "actuator" not in accepted:
            raise SystemExit(f"this harness version has no actuator {actuator!r}; cannot run the gate on it")
        kw["actuator"] = actuator
    if "notch_cfg" in accepted:
        kw["notch_cfg"] = notch_cfg
    if "label" in accepted:
        kw["label"] = label
    if overrides:
        if "analyser_overrides" not in accepted:
            raise SystemExit("this harness version does not accept analyser_overrides; cannot run gate G2")
        kw["analyser_overrides"] = overrides
    return evaluate(factory, names, **kw)


REPLAY_GLOB = "docs/research/data/programme_*.jsonl.gz"


def _load_log(path: Path) -> list[tuple[float, list[float]]]:
    import gzip
    opener = gzip.open if path.suffix == ".gz" else open
    frames = []
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if "db" in row:
                frames.append((float(row["ts"]), [float(v) for v in row["db"]]))
    return frames


def replay_gate(factory, files: list[Path]) -> dict[str, Any]:
    """G5: emissions of the candidate on every desk-logged programme file, watch and ring-out mode. The band grid is
    the product's (device.yaml). A factory whose detector has no ``cfg.mode`` is replayed in its default mode only."""
    from x32mcp.descriptor import Descriptor
    from x32mcp.meters import rta_band_hz
    band_hz = list(rta_band_hz(Descriptor.load()))
    rows = []
    for path in files:
        frames = _load_log(path)
        if not frames or len(frames[0][1]) != len(band_hz):
            rows.append({"file": path.name, "mode": "-", "frames": len(frames), "emissions": None, "error": "empty or wrong band count"})
            continue
        t0 = frames[0][0]
        for mode in ("watch", "ringout"):
            det = factory(band_hz)
            cfg = getattr(det, "cfg", None)
            if cfg is not None and dataclasses.is_dataclass(cfg) and hasattr(cfg, "mode"):
                if getattr(cfg, "mode", None) != mode:
                    try:
                        det = type(det)(dataclasses.replace(cfg, mode=mode), band_hz)
                    except Exception:  # noqa: BLE001 - a custom detector class: default mode only
                        if mode == "ringout":
                            continue
            elif mode == "ringout":
                continue
            emitted = []
            for ts, db in frames:
                for e in det.feed(db, ts - t0):
                    emitted.append({"t": round(float(e.ts), 2), "freq_hz": round(float(e.freq_hz)), "band": int(e.band)})
            rows.append({"file": path.name, "mode": mode, "frames": len(frames), "seconds": round(frames[-1][0] - t0, 1),
                         "emissions": len(emitted), "first": emitted[:5]})
    total = sum(r["emissions"] or 0 for r in rows)
    bad = [r for r in rows if r["emissions"] is None or r["emissions"] > 0]
    return {"rows": rows, "files": len(files), "emissions": total, "failing": bad}


def summarise(res) -> dict[str, Any]:
    rows = res.summary_rows()
    failing = [r for r in rows if r["verdict"] != "PASS"]
    return {
        "scenarios": len(rows), "passed": sum(1 for r in rows if r["verdict"] == "PASS"),
        "events": sum(r["events"] for r in rows), "tp": sum(r["tp"] for r in rows),
        "miss": sum(r["miss"] for r in rows), "fp": sum(r["fp"] for r in rows),
        "early": sum(r["early"] for r in rows), "tail": sum(r["tail"] for r in rows), "harm": sum(r["harm"] for r in rows),
        "fp_geq_bands": sorted({b for r in rows for b in (r.get("fp_geq_bands") or [])}),
        "failing": [{"scenario": r["scenario"], "fp": r["fp"], "miss": r["miss"], "lat_max_ms": r.get("lat_max_ms"),
                     "budget_ms": r.get("budget_ms"), "passed_seeds": f"{r['passed_seeds']}/{r['seeds']}"} for r in failing],
        "rows": rows,
    }


def gate_line(name: str, s: dict[str, Any], wall: float) -> str:
    ok = not s["failing"]
    return (f"  [{'PASS' if ok else 'FAIL'}] {name}: {s['passed']}/{s['scenarios']} scenarios, "
            f"TP {s['tp']}/{s['events']}, miss {s['miss']}, FP {s['fp']}"
            f"{' (EARLY ' + str(s['early']) + ', TAIL ' + str(s['tail']) + ', HARM ' + str(s['harm']) + ')' if (s['early'] or s['tail'] or s['harm']) else ''}"
            f"  [{wall:.0f} s]")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--factory", default="current", help="'current' | 'preM7' | module:callable")
    ap.add_argument("--quick", action="store_true", help="2 hold-out seeds, 2 sweeps (~2 min)")
    ap.add_argument("--out", default=None, help="directory for gate.json / gate.md")
    ap.add_argument("--max-fails", type=int, default=12, help="failing scenarios to list per gate")
    ap.add_argument("--replay", default=REPLAY_GLOB, help="glob of desk-logged programme files for G5 ('' to skip)")
    ap.add_argument("--replay-only", action="store_true", help="run G5 alone")
    ap.add_argument("--actuator", default="geq", choices=("geq", "peq"),
                    help="closed-loop actuator: the GEQ insert (pre-registered) or the bus PEQ stand-in (docs/PEQ_ACTUATOR_DESIGN.md)")
    a = ap.parse_args()

    try:
        factory, notch_cfg, label = load_factory(a.factory)
    except Exception as e:  # noqa: BLE001
        print(f"cannot load factory: {e}", file=sys.stderr)
        return 2

    seeds = QUICK_SEEDS if a.quick else HOLD_OUT_SEEDS
    sweeps = QUICK_SWEEPS if a.quick else SWEEPS
    all_names = list(SCENARIOS)
    feedback_names = [n for n, sc in SCENARIOS.items() if sc.has_feedback]
    print(f"acceptance gate - candidate: {label}" + (f"  [closed-loop actuator: {a.actuator}]" if a.actuator != "geq" else ""))
    print(f"  corpus {len(all_names)} scenarios ({len(feedback_names)} with feedback), hold-out seeds {seeds}, "
          f"{len(sweeps)} analyser sweeps, EARLY_CREDIT_S default {harness.EARLY_CREDIT_S}\n")

    gates: dict[str, dict[str, Any]] = {}
    verdicts: list[tuple[str, bool]] = []

    def run_replay() -> None:
        root = Path(__file__).resolve().parent.parent
        files = sorted(root.glob(a.replay)) if a.replay else []
        if not files:
            print(f"  [WARN] G5 real programme replay: no files match {a.replay!r}; gate not run")
            return
        t0 = time.perf_counter()
        g5 = replay_gate(factory, files)
        g5["wall_s"] = round(time.perf_counter() - t0, 1)
        gates["G5"] = g5
        ok = not g5["failing"]
        verdicts.append(("G5 real programme replay", ok))
        print(f"  [{'PASS' if ok else 'FAIL'}] G5 real programme replay: {g5['emissions']} emissions over {g5['files']} file(s) x 2 modes ({g5['wall_s']} s)")
        for r in g5["rows"]:
            print(f"         {r['file'][:44]:44} {r['mode']:8} {r.get('seconds', 0):6.0f} s  emissions {r['emissions']}  {r.get('first', r.get('error', ''))}")

    if a.replay_only:
        run_replay()
        ok = all(ok for _, ok in verdicts)
        print(f"\nVERDICT (G5 only): {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1

    def run_gate(key: str, name: str, names: list[str], *, closed: bool = False, overrides=None) -> dict[str, Any]:
        t0 = time.perf_counter()
        res = _evaluate(factory, names, seeds=seeds, closed_loop=closed, notch_cfg=notch_cfg, label=name, overrides=overrides,
                        actuator=a.actuator if closed else "geq")
        s = summarise(res)
        s["wall_s"] = round(time.perf_counter() - t0, 1)
        gates[key] = s
        verdicts.append((name, not s["failing"]))
        print(gate_line(name, s, s["wall_s"]))
        for f in s["failing"][: a.max_fails]:
            lat = f"lat max {f['lat_max_ms']:.0f} > {f['budget_ms']:.0f} ms" if (f["lat_max_ms"] or 0) > (f["budget_ms"] or 0) else ""
            print(f"         {f['scenario']:42} FP {f['fp']:3}  miss {f['miss']:2}  {lat}  ({f['passed_seeds']} seeds)")
        if len(s["failing"]) > a.max_fails:
            print(f"         ... and {len(s['failing']) - a.max_fails} more")
        return s

    # G1 — hold-out seeds, defaults
    run_gate("G1", "G1 hold-out seeds, open loop", all_names)

    # G2 — analyser constants moved
    for sname, ov in sweeps:
        run_gate(f"G2 {sname}", f"G2 {sname}", all_names, overrides=ov)

    # G3 — no EARLY credit (watch-mode honesty)
    saved = harness.EARLY_CREDIT_S
    harness.EARLY_CREDIT_S = 0.0
    try:
        run_gate("G3", "G3 EARLY_CREDIT_S = 0", feedback_names)
    finally:
        harness.EARLY_CREDIT_S = saved

    # G4 — closed loop through the real NotchController
    run_gate("G4", "G4 closed loop (real NotchController)", feedback_names, closed=True)

    # G5 — real programme replay
    run_replay()

    # R1 — the irreducible pairs
    g1rows = {r["scenario"]: r for r in gates["G1"]["rows"]}
    irr = {n: g1rows[n]["verdict"] for n in IRREDUCIBLE if n in g1rows}
    print(f"\n  [INFO] R1 irreducible pairs (G1): {irr}")
    if irr and all(v == "PASS" for v in irr.values()):
        print("         all four pass — CORPUS.md §7.10: passive physics separates these only by onset history, level and "
              "duration. Vary sat_db / note levels before believing this is a law rather than a corpus regularity.")

    # R2 — the price
    g1 = gates["G1"]
    print(f"  [INFO] R2 price (G1): FP would cut GEQ bands {g1['fp_geq_bands'] or 'none'}; "
          f"EARLY {g1['early']}, TAIL {g1['tail']}, HARM {g1['harm']}")

    accept = all(ok for _, ok in verdicts)
    print(f"\nVERDICT: {'ACCEPT' if accept else 'REJECT'}  ({sum(ok for _, ok in verdicts)}/{len(verdicts)} gates)")
    if a.factory == "current" and accept:
        print("NOTE: the shipped detector passed the gate. That contradicts the corpus baseline (12/61) — suspect the gate, not the detector.")

    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "gate.json").write_text(json.dumps({"candidate": label, "seeds": seeds, "accept": accept,
                                                   "gates": {k: {kk: vv for kk, vv in v.items() if kk != "rows"} for k, v in gates.items()}},
                                                  indent=2), encoding="utf-8")
        lines = [f"# Acceptance gate - {label}", "", f"Verdict: **{'ACCEPT' if accept else 'REJECT'}**", ""]
        for name, ok in verdicts:
            lines.append(f"- [{'x' if ok else ' '}] {name}")
        (out / "gate.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"written {out / 'gate.json'}")
    return 0 if accept else 1


if __name__ == "__main__":
    sys.exit(main())
