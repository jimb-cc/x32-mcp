"""Run the CURRENT x32mcp.detector.FeedbackDetector (device.yaml config) over the whole rtasim corpus and
record its metrics. Nothing is asserted about pass/fail of the detector — only that the harness runs and
produces a consistent report. Run with ``-s`` to see the table.

Baseline JSON is written to $RTASIM_REPORT_DIR (default: the review scratchpad reports/corpus directory,
falling back to the pytest tmp dir when that is not writable).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig, FeedbackDetector

from rtasim import SCENARIOS, evaluate
from rtasim.harness import run_one

DEFAULT_REPORT_DIR = ("/private/tmp/claude-502/-Users-jimb-code-studio-intel-demo/d5626e47-bee8-4335-aa3f-27a50ae21455/"
                      "scratchpad/reports/corpus")


def _report_dir(tmp_path_factory) -> Path:
    p = Path(os.environ.get("RTASIM_REPORT_DIR", DEFAULT_REPORT_DIR))
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".write_test"
        probe.write_text("ok")
        probe.unlink()
        return p
    except OSError:
        return Path(tmp_path_factory.mktemp("corpus_report"))


@pytest.fixture(scope="module")
def cfg() -> DetectorConfig:
    return DetectorConfig.from_descriptor(Descriptor.load())


@pytest.fixture(scope="module")
def factory(cfg):
    return lambda band_hz: FeedbackDetector(cfg, band_hz)


@pytest.mark.timeout(900)
def test_current_detector_baseline_open_loop(factory, cfg, tmp_path_factory):
    res = evaluate(factory, seeds=(1, 2, 3), label="current FeedbackDetector (device.yaml), open loop")
    out = _report_dir(tmp_path_factory) / "baseline_current_detector.json"
    res.meta["detector_config"] = cfg.to_dict()
    res.dump(str(out))
    print()
    print(res.table())
    print(f"\n[baseline written to {out}]")
    # harness sanity only
    rows = res.summary_rows()
    assert len(rows) == len(SCENARIOS)
    assert all(r["seeds"] == 3 for r in rows)
    data = json.loads(out.read_text())
    assert data["summary"] and data["runs"] and len(data["runs"]) == 3 * len(SCENARIOS)
    for run in res.runs:
        for d in run.detections:
            assert d.verdict in ("TP", "DUP", "EARLY", "TAIL", "HARM", "FP")
        assert run.tp + len(run.misses) == len(run.visible_episodes)


@pytest.mark.timeout(600)
def test_current_detector_closed_loop_ringout(factory, cfg, tmp_path_factory):
    names = [n for n, s in SCENARIOS.items() if s.has_feedback and ("ring_out" in s.tags or "mixture" in s.tags or "established" in s.tags)]
    res = evaluate(factory, scenarios=names, seeds=(1, 2), closed_loop=True, notch_cfg=cfg,
                   label="current FeedbackDetector, closed loop (NotchController -3 dB steps)")
    out = _report_dir(tmp_path_factory) / "baseline_current_detector_closed_loop.json"
    res.dump(str(out))
    print()
    print(res.table())
    for run in res.runs:
        print(f"  {run.scenario} seed {run.seed}: cuts={[(round(t, 2), b, g) for t, b, g in run.cuts]} "
              f"episodes={[(round(e.freq_hz), e.t_onset, e.t_prom, e.t_end) for e in run.episodes]}")
    print(f"\n[closed-loop baseline written to {out}]")
    assert len(res.runs) == 2 * len(names)


def test_harness_scores_a_perfect_oracle_and_a_null_detector():
    """The harness itself: a detector that never fires misses every visible event and has 0 FP; an 'oracle'
    that fires on the ground-truth band at t_prom scores TP with 0 latency."""
    from rtasim import frames, ground_truth
    from rtasim.physics import RTA_BAND_HZ

    class Null:
        def feed(self, values, ts):
            return []

    r = run_one(lambda bh: Null(), "S3_ring_during_music", 1)
    assert r.tp == 0 and len(r.misses) == 1 and not r.fps and not r.passed

    class Oracle:
        def __init__(self, events):
            self.events = events

        def feed(self, values, ts):
            out = []
            for e in self.events:
                if e["t_prom"] is not None and abs(ts - e["t_prom"]) < 1e-6:
                    out.append(type("D", (), {"ts": ts, "band": e["band"], "freq_hz": RTA_BAND_HZ[e["band"]], "confidence": 1.0})())
            return out

    ev = ground_truth("S11a_two_rings", 1)["events"]
    r = run_one(lambda bh: Oracle(ev), "S11a_two_rings", 1)
    assert r.tp == 2 and not r.misses and not r.fps and r.latencies_ms == [0.0, 0.0] and r.passed
    # a detection on a music-only scenario is an FP with a GEQ band attached
    class Blurter:
        n = 0

        def feed(self, values, ts):
            self.n += 1
            if self.n == 50:
                return [type("D", (), {"ts": ts, "band": 21, "freq_hz": RTA_BAND_HZ[21], "confidence": 0.9})()]
            return []

    r = run_one(lambda bh: Blurter(), "S1_bass_under_quiet_music", 1)
    assert len(r.fps) == 1 and r.fp_geq_bands == [7] and not r.passed     # GEQ band 7 = 80 Hz
