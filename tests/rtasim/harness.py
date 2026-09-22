"""Detector evaluation harness over the corpus (open loop and closed loop).

``evaluate(detector_factory, scenarios=ALL, seeds=(1, 2, 3))`` runs a fresh detector per (scenario, seed) frame by
frame exactly like ``tests/test_detector.py::run`` and scores it against the ground truth derived from the
rendered trace.

detector_factory(band_hz) -> object with ``.feed(values, ts) -> iterable`` of objects having
``.freq_hz``, ``.band``, ``.ts``, ``.confidence`` (``x32mcp.detector.FeedbackDetector`` qualifies).

Matching rule (stated precisely, per the task): a detection is *attributed* to ring r when
  (a) frequency: its RTA band is within ±1 band of the band nearest the ring's true frequency (the ring may
      sit between centres), OR |log2(f_det / f_ring)| <= 1/6 octave (one 1/3-octave GEQ band) — whichever is
      looser; and
  (b) presence: the ring dominated (>= -3 dB share) some band within ±1 of its centre on the display at that
      frame or at any frame in the preceding 1.0 s (so a detection on a ring's decaying tail just after a
      cut is a duplicate, not a false positive).
An attributed detection inside an episode [t_onset, t_end + 1 s] is a TP (the first one sets the latency
relative to t_prom = first frame the ring was >= 12 dB prominent); one before any episode while the loop is
sub-threshold-but-ringing is EARLY (reported separately: desirable in ring_out, not an FP); everything else
is a FALSE POSITIVE, listed with time/freq/band/confidence and the GEQ band it would have cut — except
two attributable-but-useless kinds reported separately: TAIL (the ring's band after its episode ended: the
display still shows the dead/decaying line, e.g. long RTA decay — a wasted deepen) and HARM (a hard-clipped
howl's own distortion partial H2..H7 — a wasted notch, but not music). An EARLY detection within
EARLY_CREDIT_S before the onset satisfies the event (negative latency).

Closed loop (``closed_loop=True``): each detection is passed to ``x32mcp.detector.NotchController.plan``
(budget = cfg.notch_budget_default, −3 dB steps to −9) and the resulting GEQ gain is written into the live
renderer (PRE insert: programme at that band drops by the bell, every ring's excess drops by the bell's gain
at its frequency), so "ring emerges, gets cut, next ring appears" is scored end to end. The detector's ``note_cut()``
is called as cfs does after each GEQ write.

SURVIVED (closed loop only; the pass/fail criterion): a ring that is still regenerating (e_eff > 0) at the last frame
although the detector saw it (a TP/DUP/EARLY of its episode) or a cut landed within one GEQ band of it. The detector did
its job and the notch policy did not finish it: a plateaued howl with excess >= the cut depth answers a cut
with exactly the cut depth and sits flat (limiter/compressor/clip re-pins it), which is what programme
through the same EQ does too, so a policy that stops on "dropped ~ bell, flat" leaves the room howling.
Every applied cut is also handed to ``det.note_cut(freq_hz, depth_db, None)`` when the detector has that hook
(cfs.py does the same after each GEQ write), so verdict-gated deepening is exercised as on the desk.
A closed-loop run fails if any ring survived (``RunResult.survived``, table column ``surv``). Misses are
not survivors (nothing was done about them); open loop never has survivors (nothing is cut).
``rings_end`` additionally records every ring's state at the last frame (level, e_eff, and ``alive`` = e_eff above
ALIVE_EXCESS_DB 0.25 dB, a lenient variant that excuses a marginal re-crossing in the scene's last second) for
reporting; it is not the pass criterion.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Callable, Iterable, Sequence

from .physics import FRAME_S, RTA_BAND_HZ, GEQ_BAND_HZ
from .render import Episode, Renderer, ring_episodes
from .scenarios import SCENARIOS, Scenario, render as render_cached

ATTRIB_LOOKBACK_S = 1.0
EPISODE_GRACE_S = 1.0
ACTUATION_DELAY_FRAMES = 1 # closed loop: a cut decided on frame k is written over OSC and lands during frame k+1,
                           # so it is fully in effect from frame k+2 (0 = the optimistic "before the next frame")
EARLY_CREDIT_S = 2.0       # an EARLY (sub-threshold ringing) detection this close before onset satisfies the event
OCT_TOL = 1.0 / 6.0
ALIVE_EXCESS_DB = 0.25     # closed loop: a ring with more effective excess than this at the last frame is still howling
HARMONIC_KS = (2, 3, 4, 5, 6, 7)
VERDICTS = ("TP", "DUP", "EARLY", "TAIL", "HARM", "FP")


def geq_band_for(hz: float, geq_band_hz: Sequence[float] = GEQ_BAND_HZ) -> int:
    lh = math.log(hz)
    return min(range(len(geq_band_hz)), key=lambda i: abs(math.log(geq_band_hz[i]) - lh)) + 1


@dataclass
class Det:
    ts: float
    band: int
    freq_hz: float
    confidence: float
    level_db: float | None = None
    prominence_db: float | None = None
    slope_db_per_s: float | None = None
    attributed: int | None = None      # ring index
    verdict: str = "FP"                # TP | DUP | EARLY | TAIL | HARM | FP  (see module docstring)
    reasons: tuple[str, ...] = ()      # the detector's own justification, when it provides one (diagnostics only)
    klass: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {"ts": round(self.ts, 3), "band": self.band, "freq_hz": round(self.freq_hz, 1),
             "confidence": round(self.confidence, 3), "verdict": self.verdict, "ring": self.attributed,
             "geq_band": geq_band_for(self.freq_hz)}
        for k in ("level_db", "prominence_db", "slope_db_per_s"):
            v = getattr(self, k)
            if v is not None:
                d[k] = round(float(v), 2)
        if self.reasons:
            d["reasons"] = list(self.reasons)
        if self.klass:
            d["class"] = self.klass
        return d


@dataclass
class RunResult:
    scenario: str
    seed: int
    closed_loop: bool
    episodes: list[Episode]
    detections: list[Det]
    cuts: list[tuple[float, int, float]]
    latency_budget_ms: float
    wall_s: float = 0.0
    rings_end: list[dict[str, Any]] = field(default_factory=list)   # closed loop: per ring {label, level_db, e_eff, alive} at the end
    survived: list[int] = field(default_factory=list)   # closed loop: rings (indices) still regenerating at the end (pass criterion)

    # derived -----------------------------------------------------------------------------------
    @property
    def visible_episodes(self) -> list[Episode]:
        return [e for e in self.episodes if e.visible]

    def first_tp(self, ep_index: int) -> Det | None:
        """Earliest detection satisfying episode ``ep_index``: a TP/DUP inside [t_onset, t_end + grace], or an
        EARLY one (sub-threshold ringing of the same loop) within EARLY_CREDIT_S before onset."""
        ep = self.episodes[ep_index]
        best = None
        for d in self.detections:
            if d.attributed != ep.ring:
                continue
            ok = (d.verdict in ("TP", "DUP") and ep.t_onset - 1e-9 <= d.ts <= ep.t_end + EPISODE_GRACE_S) or \
                 (d.verdict == "EARLY" and ep.t_onset - EARLY_CREDIT_S <= d.ts < ep.t_onset)
            if ok and (best is None or d.ts < best.ts):
                best = d
        return best

    @property
    def tp(self) -> int:
        return sum(1 for i, e in enumerate(self.episodes) if e.visible and self.first_tp(i) is not None)

    @property
    def misses(self) -> list[Episode]:
        return [e for i, e in enumerate(self.episodes) if e.visible and self.first_tp(i) is None]

    @property
    def latencies_ms(self) -> list[float]:
        out = []
        for i, e in enumerate(self.episodes):
            if not e.visible:
                continue
            d = self.first_tp(i)
            if d is not None:
                out.append(round((d.ts - e.t_prom) * 1000.0, 1))
        return out

    @property
    def fps(self) -> list[Det]:
        return [d for d in self.detections if d.verdict == "FP"]

    @property
    def early(self) -> list[Det]:
        return [d for d in self.detections if d.verdict == "EARLY"]

    @property
    def tail(self) -> list[Det]:
        return [d for d in self.detections if d.verdict == "TAIL"]

    @property
    def harm(self) -> list[Det]:
        return [d for d in self.detections if d.verdict == "HARM"]

    @property
    def fp_geq_bands(self) -> list[int]:
        return sorted({geq_band_for(d.freq_hz) for d in self.fps})

    @property
    def alive(self) -> list[dict[str, Any]]:
        """Closed loop: rings that had a visible, DETECTED episode and are still regenerating / held at their plateau at the
        end of the run (a missed ring is a miss, not an 'alive'; an open-loop run has no cuts and reports none)."""
        if not self.closed_loop:
            return []
        hit = {e.ring for i, e in enumerate(self.episodes) if e.visible and self.first_tp(i) is not None}
        return [r for r in self.rings_end if r.get("alive") and r.get("ring") in hit]

    @property
    def passed(self) -> bool:
        return (not self.fps and not self.misses and all(l <= self.latency_budget_ms for l in self.latencies_ms)
                and not self.survived)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario, "seed": self.seed, "closed_loop": self.closed_loop,
            "episodes": [e.to_dict() for e in self.episodes],
            "detections": [d.to_dict() for d in self.detections],
            "tp": self.tp, "miss": len(self.misses), "fp": len(self.fps), "early": len(self.early),
            "tail": len(self.tail), "harm": len(self.harm),
            "fp_geq_bands": self.fp_geq_bands, "latencies_ms": self.latencies_ms,
            "cuts": [{"ts": round(t, 3), "geq_band": b, "gain_db": g} for t, b, g in self.cuts],
            "rings_end": self.rings_end, "alive": len(self.alive), "survived": list(self.survived),
            "passed": self.passed, "latency_budget_ms": self.latency_budget_ms, "wall_s": round(self.wall_s, 3),
        }


def _attribute(dets: list[Det], renderer: Renderer, episodes: list[Episode]) -> None:
    scene = renderer.scene
    lookback = int(round(ATTRIB_LOOKBACK_S / FRAME_S))
    for d in dets:
        k = int(round(d.ts / FRAME_S))
        best = None
        best_dist = 1e9
        for i, r in enumerate(scene.rings):
            tr = renderer.trace[i]
            if not tr:
                continue
            kk = min(max(0, k), len(tr) - 1)
            fr = tr[kk]
            f_ring = fr.f_hz
            u_ring = 10.0 * math.log2(f_ring / RTA_BAND_HZ[0])
            b_ring = int(round(u_ring))
            freq_ok = abs(d.band - b_ring) <= 1 or abs(math.log2(max(1e-9, d.freq_hz) / f_ring)) <= OCT_TOL
            if not freq_ok:
                continue
            present = any(tr[j].prom_db > float("-inf") for j in range(max(0, kk - lookback), kk + 1))
            if not present:
                continue
            dist = abs(d.band - u_ring)
            if dist < best_dist:
                best, best_dist = i, dist
        if best is None:
            # a hard-clipped howl's own distortion partials (H2..H7 within ±1 band) while it is clipping
            for i, r in enumerate(scene.rings):
                tr = renderer.trace[i]
                if not tr:
                    continue
                kk = min(max(0, k), len(tr) - 1)
                fr = tr[kk]
                if not (fr.active and r.harmonics_active(fr.level_db)):
                    continue
                u_ring = 10.0 * math.log2(fr.f_hz / RTA_BAND_HZ[0])
                ks = set(HARMONIC_KS) | {k for k, _ in r.harmonics}
                if any(abs(d.band - (u_ring + 10.0 * math.log2(kh))) <= 1.0 for kh in ks):
                    best = i
                    d.attributed = i
                    d.verdict = "HARM"
                    break
            if d.verdict != "HARM":
                d.verdict = "FP"
            continue
        d.attributed = best
        # inside an episode of that ring?
        verdict = None
        for e in episodes:
            if e.ring != best:
                continue
            if e.t_onset - 1e-9 <= d.ts <= e.t_end + EPISODE_GRACE_S:
                verdict = "TP"
                break
        if verdict is None:
            later = [e for e in episodes if e.ring == best and e.t_onset > d.ts]
            earlier = [e for e in episodes if e.ring == best and e.t_end + EPISODE_GRACE_S < d.ts]
            if later or not earlier:
                verdict = "EARLY"      # sub-threshold regeneration detected before the loop ran away
            else:
                verdict = "TAIL"       # after the episode ended: the display still shows the dead ring (slow RTA
                                       # release / peak-hold) or its sub-threshold tail — a wasted deepen, not music
        d.verdict = verdict
    # mark duplicates (TP after the first per episode)
    seen: set[tuple[int, float]] = set()
    for d in sorted(dets, key=lambda x: x.ts):
        if d.verdict != "TP":
            continue
        for e in episodes:
            if e.ring == d.attributed and e.t_onset - 1e-9 <= d.ts <= e.t_end + EPISODE_GRACE_S:
                key = (e.ring, e.t_onset)
                if key in seen:
                    d.verdict = "DUP"
                seen.add(key)
                break


def _survivors(rr: "RunResult", renderer: Renderer, geq_band_hz: Sequence[float]) -> list[int]:
    """Rings still regenerating (e_eff > 0) at the last frame that were detected or cut: the notch policy
    failed to kill them (see module docstring, SURVIVED). Never reports a ring nobody acted on (a miss)."""
    seen = {rr.episodes[i].ring for i, e in enumerate(rr.episodes) if e.visible and rr.first_tp(i) is not None}
    cut_bands = {b for _, b, _ in rr.cuts}
    out: list[int] = []
    for i, tr in renderer.trace.items():
        if not tr:
            continue
        fr = tr[-1]
        if not (fr.active and fr.e_eff > 0.0):
            continue
        gb = geq_band_for(fr.f_hz, geq_band_hz)
        if i in seen or any(abs(b - gb) <= 1 for b in cut_bands):
            out.append(i)
    return out


def run_one(detector_factory: Callable[[Sequence[float]], Any], scenario: Scenario | str, seed: int, *,
            closed_loop: bool = False, analyser_overrides: dict[str, Any] | None = None,
            notch_cfg: Any = None, geq_band_hz: Sequence[float] = GEQ_BAND_HZ,
            actuation_delay_frames: int = ACTUATION_DELAY_FRAMES) -> RunResult:
    sc = SCENARIOS[scenario] if isinstance(scenario, str) else scenario
    t0 = time.perf_counter()
    det = detector_factory(RTA_BAND_HZ)
    dets: list[Det] = []
    cuts: list[tuple[float, int, float]] = []

    def collect(out: Iterable[Any]) -> list[Det]:
        got = []
        for o in out or ():
            got.append(Det(ts=float(o.ts), band=int(o.band), freq_hz=float(o.freq_hz), confidence=float(o.confidence),
                           level_db=getattr(o, "level_db", None), prominence_db=getattr(o, "prominence_db", None),
                           slope_db_per_s=getattr(o, "slope_db_per_s", None),
                           reasons=tuple(getattr(o, "reasons", ()) or ()), klass=getattr(o, "klass", None)))
        return got

    if not closed_loop:
        r = render_cached(sc, seed, analyser_overrides)
        for ts, vals in r.frames:
            dets.extend(collect(det.feed(vals, ts)))
        episodes = ring_episodes(r)
    else:
        from x32mcp.detector import DetectorConfig, NotchController
        cfg = notch_cfg or DetectorConfig()
        nc = NotchController(cfg, geq_band_hz, lambda cur, new: None, budget=cfg.notch_budget_default)
        from x32mcp.detector import Detection
        r = Renderer(sc.build(seed, analyser_overrides), seed, geq_band_hz=geq_band_hz)
        pending: list[tuple[int, int, float, float]] = []      # (apply_before_frame, band, gain, ts_decided)
        while not r.done:
            while pending and pending[0][0] <= r.k:
                _, band, gain, ts0 = pending.pop(0)
                r.set_geq_gain(band, gain)
                cuts.append((ts0, band, gain))
                if hasattr(det, "note_cut"):
                    # what cfs.py does after every GEQ write: hand the detector its own cut so a verdict-gated
                    # deepening policy (post-cut watch) is exercised here exactly as on the desk; a detector without
                    # the hook is unaffected. Same call as the implementer's harness (review/detector).
                    det.note_cut(float(geq_band_hz[band - 1]), float(gain), None)
            fr = r.step_frame()
            if fr is None:
                continue
            ts, vals = fr
            new = collect(det.feed(vals, ts))
            dets.extend(new)
            for d in new:
                dd = Detection(ts=d.ts, band=d.band, freq_hz=d.freq_hz, level_db=d.level_db or 0.0,
                               prominence_db=d.prominence_db or 0.0, slope_db_per_s=d.slope_db_per_s or 0.0,
                               frames=0, confidence=d.confidence)
                notch = nc.plan(dd, 0, "sim")
                if notch is not None:
                    pending.append((r.k + actuation_delay_frames, notch.band, notch.depth_db, ts))
        episodes = ring_episodes(r)
    _attribute(dets, r, episodes)
    rings_end: list[dict[str, Any]] = []
    if closed_loop:
        for i, rg in enumerate(r.scene.rings):
            tr = r.trace[i]
            if not tr:
                continue
            last = tr[-1]
            rings_end.append({"ring": i, "label": rg.label, "level_db": round(last.level_db, 1), "e_eff": round(last.e_eff, 2),
                              "alive": bool(last.active and last.e_eff > ALIVE_EXCESS_DB)})
    rr = RunResult(sc.name, seed, closed_loop, episodes, dets, cuts, sc.latency_budget_ms, time.perf_counter() - t0, rings_end)
    if closed_loop:
        rr.survived = _survivors(rr, r, geq_band_hz)
    return rr


@dataclass
class Results:
    runs: list[RunResult] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def by_scenario(self) -> dict[str, list[RunResult]]:
        out: dict[str, list[RunResult]] = {}
        for r in self.runs:
            out.setdefault(r.scenario, []).append(r)
        return out

    def summary_rows(self) -> list[dict[str, Any]]:
        rows = []
        for name, runs in self.by_scenario().items():
            sc = SCENARIOS.get(name)
            budget = sc.latency_budget_ms if sc else runs[0].latency_budget_ms
            lats = [l for r in runs for l in r.latencies_ms]
            rows.append({
                "scenario": name,
                "seeds": len(runs),
                "events": sum(len(r.visible_episodes) for r in runs),
                "invisible_events": sum(len(r.episodes) - len(r.visible_episodes) for r in runs),
                "tp": sum(r.tp for r in runs),
                "miss": sum(len(r.misses) for r in runs),
                "fp": sum(len(r.fps) for r in runs),
                "fp_geq_bands": sorted({b for r in runs for b in r.fp_geq_bands}),
                "early": sum(len(r.early) for r in runs),
                "tail": sum(len(r.tail) for r in runs),
                "harm": sum(len(r.harm) for r in runs),
                "dup": sum(1 for r in runs for d in r.detections if d.verdict == "DUP"),
                "cuts": sum(len(r.cuts) for r in runs),
                "alive": sum(len(r.alive) for r in runs),
                "survived": sum(len(r.survived) for r in runs),
                "lat_min_ms": min(lats) if lats else None,
                "lat_med_ms": median(lats) if lats else None,
                "lat_max_ms": max(lats) if lats else None,
                "budget_ms": budget,
                "passed_seeds": sum(1 for r in runs if r.passed),
                "verdict": "PASS" if all(r.passed for r in runs) else "FAIL",
            })
        return rows

    def table(self) -> str:
        rows = self.summary_rows()
        hdr = (f"{'scenario':<38} {'ev':>3} {'TP':>3} {'miss':>4} {'FP':>4} {'erly':>4} {'tail':>4} {'harm':>4} {'cuts':>4} {'surv':>4} "
               f"{'lat ms min/med/max':>19} {'bud':>4}  FP at GEQ bands (Hz)       verdict")
        lines = [hdr, "-" * len(hdr)]
        for r in rows:
            lat = "-" if r["lat_med_ms"] is None else f"{r['lat_min_ms']:.0f}/{r['lat_med_ms']:.0f}/{r['lat_max_ms']:.0f}"
            fpb = ",".join(str(int(GEQ_BAND_HZ[b - 1])) for b in r["fp_geq_bands"][:7])
            if len(r["fp_geq_bands"]) > 7:
                fpb += ",..."
            lines.append(f"{r['scenario']:<38} {r['events']:>3} {r['tp']:>3} {r['miss']:>4} {r['fp']:>4} {r['early']:>4} "
                         f"{r['tail']:>4} {r['harm']:>4} {r['cuts']:>4} {r['survived']:>4} {lat:>19} {r['budget_ms']:>4.0f}  {fpb:<26} "
                         f"{r['verdict']} ({r['passed_seeds']}/{r['seeds']})")
        tot_fp = sum(r["fp"] for r in rows)
        tot_miss = sum(r["miss"] for r in rows)
        tot_ev = sum(r["events"] for r in rows)
        tot_tp = sum(r["tp"] for r in rows)
        tot_surv = sum(r["survived"] for r in rows)
        npass = sum(1 for r in rows if r["verdict"] == "PASS")
        lines.append("-" * len(hdr))
        lines.append(f"{len(rows)} scenarios, {npass} pass; events {tot_ev}, TP {tot_tp}, miss {tot_miss}, FP {tot_fp}, survived {tot_surv}; "
                     f"wall {self.meta.get('wall_s', 0):.1f} s")
        return "\n".join(lines)

    def totals(self) -> dict[str, Any]:
        """Whole-run-set totals + latency distribution (first TP per visible event, ms, nearest-rank quantiles)."""
        rows = self.summary_rows()
        lats = sorted(l for r in self.runs for l in r.latencies_ms)

        def q(p: float) -> float | None:
            if not lats:
                return None
            return lats[min(len(lats) - 1, max(0, int(math.ceil(p * len(lats))) - 1))]

        return {
            "scenarios": len(rows), "pass": sum(1 for r in rows if r["verdict"] == "PASS"),
            "events": sum(r["events"] for r in rows), "tp": sum(r["tp"] for r in rows), "miss": sum(r["miss"] for r in rows),
            "fp": sum(r["fp"] for r in rows), "early": sum(r["early"] for r in rows), "tail": sum(r["tail"] for r in rows),
            "harm": sum(r["harm"] for r in rows), "dup": sum(r["dup"] for r in rows), "cuts": sum(r["cuts"] for r in rows),
            "alive": sum(r["alive"] for r in rows), "survived": sum(r["survived"] for r in rows),
            "lat_n": len(lats), "lat_min": lats[0] if lats else None, "lat_p50": q(0.5), "lat_p75": q(0.75), "lat_p90": q(0.9),
            "lat_p95": q(0.95), "lat_max": lats[-1] if lats else None,
            "le300": sum(1 for l in lats if l <= 300.0),
            "in_budget": sum(1 for r in self.runs for l in r.latencies_ms if l <= r.latency_budget_ms),
            "fp_scenarios": sorted({r["scenario"] for r in rows if r["fp"]}),
            "miss_scenarios": sorted({r["scenario"] for r in rows if r["miss"]}),
            "alive_scenarios": sorted({r["scenario"] for r in rows if r["alive"]}),
            "survived_scenarios": sorted({r["scenario"] for r in rows if r["survived"]}),
        }

    def to_json(self) -> dict[str, Any]:
        return {"meta": self.meta, "summary": self.summary_rows(), "totals": self.totals(), "runs": [r.to_dict() for r in self.runs]}

    def dump(self, path: str) -> None:
        with open(path, "w") as fh:
            json.dump(self.to_json(), fh, indent=1, default=str)


def evaluate(detector_factory: Callable[[Sequence[float]], Any], scenarios: Iterable[str | Scenario] | None = None,
             seeds: Sequence[int] = (1, 2, 3), *, closed_loop: bool = False,
             analyser_overrides: dict[str, Any] | None = None, notch_cfg: Any = None,
             geq_band_hz: Sequence[float] = GEQ_BAND_HZ, label: str = "") -> Results:
    names = list(scenarios) if scenarios is not None else list(SCENARIOS)
    t0 = time.perf_counter()
    res = Results(meta={"label": label, "seeds": list(seeds), "closed_loop": closed_loop,
                        "analyser_overrides": analyser_overrides or {}, "scenarios": [s if isinstance(s, str) else s.name for s in names]})
    for s in names:
        for seed in seeds:
            res.runs.append(run_one(detector_factory, s, seed, closed_loop=closed_loop,
                                    analyser_overrides=analyser_overrides, notch_cfg=notch_cfg, geq_band_hz=geq_band_hz))
    res.meta["wall_s"] = round(time.perf_counter() - t0, 2)
    return res


def _main() -> None:   # pragma: no cover - CLI: python -m rtasim.harness [--closed] [--adversarial] [--mode=watch|ringout] [scenario ...]
    import sys
    from x32mcp.descriptor import Descriptor
    from x32mcp.detector import DetectorConfig, FeedbackDetector
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    closed = "--closed" in sys.argv
    mode = ([a[7:] for a in sys.argv[1:] if a.startswith("--mode=")] or ["watch"])[0]
    cfg = DetectorConfig.from_descriptor(Descriptor.load())
    scen: Any = args or None
    if "--adversarial" in sys.argv:
        from .scenarios_adversarial import ADVERSARIAL
        scen = [ADVERSARIAL[a] for a in args] if args else list(ADVERSARIAL.values())
    res = evaluate(lambda bh: FeedbackDetector(cfg, bh, mode=mode), scen, closed_loop=closed, notch_cfg=cfg,
                   label=f"current FeedbackDetector ({mode})")
    print(res.table())
    out = [a[7:] for a in sys.argv[1:] if a.startswith("--json=")]
    if out:
        res.dump(out[0])
        print(f"written {out[0]}")


if __name__ == "__main__":
    _main()
