"""detector.py — synthetic RTA streams (built here, independent of meters.py) + notch controller.

Stream time base: frame i is at t = i * 0.05 s (rta.frame_period_s). Levels are RTA dB as /meters/15
reports them (docs/research/meters.md §4.2). Each stream prints the frame index of every detection.
"""

from __future__ import annotations

import math
import random

import pytest

from x32mcp.descriptor import Descriptor
from x32mcp.detector import (
    Candidate,
    Detection,
    DetectorConfig,
    FeedbackDetector,
    Notch,
    NotchController,
    RecordingGeqWriter,
)

FRAME_S = 0.05


@pytest.fixture(scope="module")
def d() -> Descriptor:
    return Descriptor.load()


@pytest.fixture(scope="module")
def band_hz(d) -> tuple[float, ...]:
    return tuple(float(h) for h in d.rta["band_hz"])


@pytest.fixture(scope="module")
def cfg(d) -> DetectorConfig:
    return DetectorConfig.from_descriptor(d)


def nearest_band(band_hz, hz: float) -> int:
    return min(range(len(band_hz)), key=lambda i: abs(math.log(band_hz[i]) - math.log(hz)))


def power_sum(*dbs: float) -> float:
    return 10.0 * math.log10(sum(10.0 ** (x / 10.0) for x in dbs))


def prominence(values: list[float], i: int, k: int = 3) -> float:
    """Same definition as DESIGN §12 (median of ±k neighbours excluding i), computed independently."""
    neigh = [values[j] for j in range(max(0, i - k), min(len(values), i + k + 1)) if j != i]
    neigh.sort()
    m = len(neigh)
    med = neigh[m // 2] if m % 2 else 0.5 * (neigh[m // 2 - 1] + neigh[m // 2])
    return values[i] - med


# -- synthetic RTA generator ---------------------------------------------------------------------


class SyntheticRta:
    """Deterministic 100-band 'music' spectrum with optional tonal components.

    Base: tilted floor with smooth bumps, a slow overall LFO (dynamics), broad drum hits and a melody of
    notes with instantaneous onsets (fundamental + 2 harmonics, steady while held). Everything is
    combined by power summation, so a tone dominates its band smoothly as it emerges from the floor.
    """

    def __init__(self, band_hz, *, seed: int = 1, floor_noise_db: float = 1.5, melody: bool = True) -> None:
        self.band_hz = band_hz
        self.n = len(band_hz)
        self.rng = random.Random(seed)
        self.floor_noise_db = floor_noise_db
        self.melody = melody
        self.rings: list[tuple[float, float, float, float]] = []   # (hz, start_t, start_db, rate_db_s)
        self.notes: list[tuple[float, float, float, float, float]] = []  # (hz, t_on, rise_s, level_db_over_floor, t_off)
        self.extra_noise_db = 0.0

    # -- components --
    def add_ring(self, hz: float, *, start_t: float, start_db: float = -40.0, rate_db_s: float = 15.0) -> None:
        self.rings.append((hz, start_t, start_db, rate_db_s))

    def add_note(self, hz: float, *, t_on: float, rise_s: float, above_floor_db: float, t_off: float = 1e9) -> None:
        self.notes.append((hz, t_on, rise_s, above_floor_db, t_off))

    def floor_db(self, i: int, t: float) -> float:
        lvl = -30.0 - 0.22 * i                       # gentle HF tilt
        for centre, height, sigma in ((15, 6.0, 5.0), (40, 4.0, 6.0), (60, 3.0, 8.0)):
            lvl += height * math.exp(-((i - centre) ** 2) / (2 * sigma * sigma))
        lvl += 3.0 * math.sin(2 * math.pi * 0.4 * t)  # dynamics
        return lvl

    def _tone(self, comps: list[float], i_c: int, level: float) -> None:
        # analyser leakage: ±1 band at -8 dB
        for di, att in ((-1, -8.0), (0, 0.0), (1, -8.0)):
            j = i_c + di
            if 0 <= j < self.n:
                comps_j = comps[j]
                comps[j] = power_sum(comps_j, level + att)

    def frame(self, idx: int) -> list[float]:
        t = idx * FRAME_S
        rng = self.rng
        vals = [self.floor_db(i, t) + rng.uniform(-self.floor_noise_db, self.floor_noise_db) for i in range(self.n)]
        # broad drum hits every 25 frames, decaying 3 dB/frame, centred on band 15 (~55 Hz)
        k = idx % 25
        if k < 5:
            for i in range(self.n):
                vals[i] = power_sum(vals[i], self.floor_db(i, t) + 8.0 - 3.0 * k + 6.0 * math.exp(-((i - 15) ** 2) / 200.0))
        # melody: a new note every 20 frames, held 14 frames, released over 4 (-4 dB/frame)
        if self.melody:
            note_idx = idx // 20
            phase = idx % 20
            nr = random.Random(1000 + note_idx)
            fund = nr.randint(30, 60)
            gain = 0.0 if phase < 14 else -4.0 * (phase - 13)
            if phase < 18:
                for band, lvl in ((fund, 16.0), (fund + 10, 12.0), (fund + 16, 8.0)):
                    steady = self.floor_db(band, t) + lvl + gain + rng.uniform(-0.2, 0.2)
                    self._tone(vals, band, steady)
        for hz, t_on, rise_s, above, t_off in self.notes:
            if t_on <= t <= t_off:
                i_c = nearest_band(self.band_hz, hz)
                frac = 1.0 if rise_s <= 0 else min(1.0, (t - t_on + FRAME_S) / rise_s)
                self._tone(vals, i_c, self.floor_db(i_c, t) + above * frac)
        for hz, start_t, start_db, rate in self.rings:
            if t >= start_t:
                i_c = nearest_band(self.band_hz, hz)
                self._tone(vals, i_c, min(0.0, start_db + rate * (t - start_t)))
        if self.extra_noise_db:
            vals = [v + rng.uniform(-self.extra_noise_db, self.extra_noise_db) for v in vals]
        return [max(-128.0, min(0.0, v)) for v in vals]

    def frames(self, n: int) -> list[list[float]]:
        return [self.frame(i) for i in range(n)]


def run(cfg: DetectorConfig, band_hz, frames: list[list[float]], label: str) -> list[tuple[int, Detection]]:
    det = FeedbackDetector(cfg, band_hz)
    out: list[tuple[int, Detection]] = []
    for i, f in enumerate(frames):
        for dd in det.feed(f, i * FRAME_S):
            out.append((i, dd))
            print(f"[{label}] detection at frame {i}: band {dd.band} ({dd.freq_hz:.0f} Hz) "
                  f"{dd.level_db:.1f} dB, prominence {dd.prominence_db:.1f} dB, slope {dd.slope_db_per_s:.1f} dB/s, "
                  f"frames {dd.frames}, confidence {dd.confidence:.2f}")
    if not out:
        print(f"[{label}] no detections in {len(frames)} frames")
    return out


def first_crossing(frames, band: int, threshold: float, tol: int = 1) -> int | None:
    for i, f in enumerate(frames):
        if any(prominence(f, b) >= threshold for b in range(band - tol, band + tol + 1)):
            return i
    return None


# -- config ---------------------------------------------------------------------------------------


def test_config_from_descriptor(d, cfg):
    y = d.detector
    assert cfg.prominence_db == y["prominence_db"] == 12
    assert cfg.neighbour_bins == 3 and isinstance(cfg.neighbour_bins, int)
    assert cfg.persistence_frames == 3
    assert cfg.weights == {"prominence": 0.3, "persistence": 0.2, "growth": 0.5}
    assert cfg.notch_step_db == -3 and cfg.notch_max_db == -9 and cfg.notch_budget_default == 6
    assert cfg.merge_adjacent_bands == 1 and cfg.band_tolerance == 1 and cfg.cooldown_s == 1.0
    assert cfg.frame_period_s == 0.05
    assert cfg.to_dict()["weights"] == cfg.weights
    # a plateau can never reach the threshold: prominence + persistence alone stay below it
    assert cfg.w_prominence + cfg.w_persistence < cfg.confidence_threshold


def test_config_validation():
    with pytest.raises(ValueError):
        DetectorConfig(notch_step_db=3)
    with pytest.raises(ValueError):
        DetectorConfig(notch_max_db=-1, notch_step_db=-3)
    with pytest.raises(ValueError):
        DetectorConfig.from_dict({"weights": {"bogus": 1}})
    c = DetectorConfig.from_dict({"prominence_db": 10, "weights": {"growth": 0.6}, "unknown_key": 5})
    assert c.prominence_db == 10.0 and c.w_growth == 0.6 and c.w_prominence == 0.3


def test_prominence_definition(cfg, band_hz):
    det = FeedbackDetector(cfg, band_hz)
    vals = [-60.0] * 100
    vals[50] = -30.0
    p = det.prominences(vals)
    assert p[50] == pytest.approx(30.0)
    assert p[49] == pytest.approx(0.0)      # median of neighbours ignores the single spike
    assert p[0] == pytest.approx(0.0)       # truncated edge window
    with pytest.raises(ValueError):
        det.feed([-60.0] * 99, 0.0)


# -- (a) clean music ------------------------------------------------------------------------------


def test_clean_music_no_detections(cfg, band_hz):
    src = SyntheticRta(band_hz, seed=7)
    frames = src.frames(200)
    assert len(frames) == 200 and all(len(f) == 100 for f in frames)
    dets = run(cfg, band_hz, frames, "a:music")
    assert dets == []


# -- (b) held note --------------------------------------------------------------------------------


def test_held_note_not_detected(cfg, band_hz):
    src = SyntheticRta(band_hz, seed=3, melody=False)
    # rises to +20 dB over neighbours across 5 frames (0.25 s), then holds for 100 frames
    src.add_note(330.0, t_on=1.0, rise_s=5 * FRAME_S, above_floor_db=20.0)
    frames = src.frames(20 + 5 + 100)
    band = nearest_band(band_hz, 330.0)
    assert prominence(frames[24], band) > 18.0          # the note really is there
    assert prominence(frames[124], band) > 18.0
    dets = run(cfg, band_hz, frames, "b:note")
    assert dets == []


# -- (c) regenerating ring ------------------------------------------------------------------------


def test_ring_detected_quickly(cfg, band_hz):
    src = SyntheticRta(band_hz, seed=5)
    src.add_ring(2400.0, start_t=1.0, start_db=-40.0, rate_db_s=15.0)
    frames = src.frames(120)
    band = nearest_band(band_hz, 2400.0)
    t0 = first_crossing(frames, band, cfg.prominence_db)
    assert t0 is not None and t0 > 20
    dets = run(cfg, band_hz, frames, "c:ring")
    assert dets, "ring not detected"
    i, first = dets[0]
    print(f"[c:ring] prominence first >= {cfg.prominence_db} dB at frame {t0}; detected at frame {i} (+{i - t0})")
    assert abs(first.band - band) <= 1
    assert 0 <= i - t0 <= 6
    assert first.slope_db_per_s == pytest.approx(15.0, abs=3.0)
    assert first.confidence >= cfg.confidence_threshold
    assert all(abs(dd.band - band) <= 1 for _, dd in dets)
    # cooldown: re-emissions for the same band are at least cooldown_s apart
    ts = [dd.ts for _, dd in dets]
    assert all(b - a >= cfg.cooldown_s - 1e-9 for a, b in zip(ts, ts[1:]))
    assert len(dets) >= 2   # the ring keeps growing, so it is re-reported once per cooldown


# -- (d) two rings ---------------------------------------------------------------------------------


def test_two_rings_both_detected(cfg, band_hz):
    src = SyntheticRta(band_hz, seed=11)
    src.add_ring(2400.0, start_t=1.0, start_db=-40.0, rate_db_s=15.0)
    src.add_ring(630.0, start_t=1.5, start_db=-38.0, rate_db_s=10.0)
    frames = src.frames(140)
    b_hi, b_lo = nearest_band(band_hz, 2400.0), nearest_band(band_hz, 630.0)
    dets = run(cfg, band_hz, frames, "d:two")
    got = {dd.band for _, dd in dets}
    assert any(abs(b - b_hi) <= 1 for b in got), got
    assert any(abs(b - b_lo) <= 1 for b in got), got
    assert all(abs(b - b_hi) <= 1 or abs(b - b_lo) <= 1 for b in got), got
    for target in (b_hi, b_lo):
        t0 = first_crossing(frames, target, cfg.prominence_db)
        i = next(i for i, dd in dets if abs(dd.band - target) <= 1)
        print(f"[d:two] band {target}: crossing at frame {t0}, detected at frame {i}")
        # ``first_crossing`` uses the single-band prominence (median of ±3). The detector also measures the
        # cluster prominence (power of b-1..b+1 over the ring of bands ±2..±4, analyser brief §3) which sees a
        # line a few dB earlier while it climbs out of the floor, so a detection may now precede this crossing by
        # a few frames: earlier is better, later than 6 frames is still a failure.
        assert t0 is not None and -8 <= i - t0 <= 6


# -- (e) noise robustness --------------------------------------------------------------------------


def test_ring_detected_with_noise(cfg, band_hz):
    """Stream (c) with ±2 dB uniform noise added to every band on every frame.

    Melody off: with uncorrelated ±2 dB per-frame noise on a *held note's own bin*, a 3-frame
    least-squares slope exceeds growth_min ~15 % of the time, so persistence_frames=3 cannot separate a
    noisy plateau from a slow ring (see module docstring). Real RTA bins dominated by a steady tone are
    steady; the floor is what is noisy, and that is what this stream exercises.
    """
    src = SyntheticRta(band_hz, seed=5, melody=False)
    src.add_ring(2400.0, start_t=1.0, start_db=-40.0, rate_db_s=15.0)
    src.extra_noise_db = 2.0
    frames = src.frames(120)
    band = nearest_band(band_hz, 2400.0)
    t0 = first_crossing(frames, band, cfg.prominence_db)
    dets = run(cfg, band_hz, frames, "e:noisy")
    assert dets, "noisy ring not detected"
    i, first = dets[0]
    print(f"[e:noisy] first noisy crossing at frame {t0}; detected at frame {i} (+{i - t0})")
    assert abs(first.band - band) <= 1
    assert t0 is not None and 0 <= i - t0 <= 12   # noise makes the streak flicker near the threshold
    assert all(abs(dd.band - band) <= 1 for _, dd in dets)


def test_plateau_and_transient_do_not_detect(cfg, band_hz):
    src = SyntheticRta(band_hz, seed=9)
    src.add_note(1000.0, t_on=0.5, rise_s=0.0, above_floor_db=18.0)             # instant onset, held
    src.add_note(4000.0, t_on=2.0, rise_s=0.0, above_floor_db=20.0, t_off=2.1)  # 3-frame transient
    frames = src.frames(140)
    assert run(cfg, band_hz, frames, "f:plateau") == []
    # (a linear 20 dB/s crescendo held for >= persistence_frames IS the ring signature — BRIEF §5 —
    #  and is reported; ring-out runs without programme audio for that reason)


def test_vibrato_rejected_with_longer_persistence(d, band_hz):
    """A held note wobbling ±1 band / ±2 dB at 5 Hz. The original weighted-sum heuristic reported it at
    persistence_frames=3 (each rising half-cycle looked like +40 dB/s growth to a 3-frame window) and this test
    used to assert that false positive. The track/group/classify detector follows the hopping peak as ONE track
    whose centroid swings by a band (vibrato => programme) and which arrived at full level in one frame (a note
    onset, not regeneration): it must not be reported at either persistence setting, while the 15 dB/s ring is
    still caught."""

    def stream(src: SyntheticRta, n: int) -> list[list[float]]:
        frames = []
        b0 = nearest_band(band_hz, 440.0)
        for i in range(n):
            f = src.frame(i)
            if i >= 10:
                ph = 2 * math.pi * 5.0 * i * FRAME_S
                b = b0 + (1 if math.sin(ph) > 0.5 else 0)
                src._tone(f, b, src.floor_db(b, i * FRAME_S) + 18.0 + 2.0 * math.sin(ph))
            frames.append(f)
        return frames

    cfg3 = DetectorConfig.from_descriptor(d)
    assert run(cfg3, band_hz, stream(SyntheticRta(band_hz, seed=2, melody=False), 120), "h:vibrato/3") == []
    cfg6 = DetectorConfig.from_dict({**d.detector, "persistence_frames": 6})
    assert run(cfg6, band_hz, stream(SyntheticRta(band_hz, seed=2, melody=False), 120), "h:vibrato/6") == []
    src = SyntheticRta(band_hz, seed=5, melody=False)
    src.add_ring(2400.0, start_t=1.0)
    frames = src.frames(120)
    band = nearest_band(band_hz, 2400.0)
    dets = run(cfg6, band_hz, frames, "h:ring/6")
    t0 = first_crossing(frames, band, cfg6.prominence_db)
    # persistence 6 needs 3 more frames than the default; the streak may also restart once at the threshold
    print(f"[h:ring/6] crossing at frame {t0}, detected at frame {dets[0][0]} (+{dets[0][0] - t0})")
    assert dets and abs(dets[0][1].band - band) <= 1 and 0 <= dets[0][0] - t0 <= 9


def test_candidates_and_reset(cfg, band_hz):
    src = SyntheticRta(band_hz, seed=5, melody=False)
    src.add_ring(2400.0, start_t=0.0, start_db=-20.0, rate_db_s=15.0)
    det = FeedbackDetector(cfg, band_hz)
    for i in range(2):
        det.feed(src.frame(i), i * FRAME_S)
    cands = det.candidates
    assert len(cands) == 1 and isinstance(cands[0], Candidate)
    c = cands[0]
    assert c.frames == 2 and abs(c.band - nearest_band(band_hz, 2400.0)) <= 1
    assert 0.0 <= c.confidence < cfg.confidence_threshold
    assert set(c.to_dict()) >= {"band", "freq_hz", "frames", "confidence", "slope_db_per_s"}
    det.reset()
    assert det.candidates == [] and det.frames_seen == 0


# -- notch controller ------------------------------------------------------------------------------


class Rejected(Exception):
    pass


def validate_cut(current_db: float, new_db: float) -> None:
    """Stand-in for Policy.validate_notch: cuts only, never shallower, never below -9 dB."""
    if not (new_db < 0 and new_db <= current_db):
        raise Rejected(f"BOOST_FORBIDDEN {current_db} -> {new_db}")
    if new_db < -9:
        raise Rejected("NOT_ALLOWED too deep")


def det_at(hz: float, ts: float = 10.0) -> Detection:
    return Detection(ts=ts, band=0, freq_hz=hz, level_db=-20.0, prominence_db=15.0,
                     slope_db_per_s=15.0, frames=3, confidence=0.9)


@pytest.fixture(scope="module")
def geq_hz(d) -> tuple[float, ...]:
    return tuple(float(h) for h in d.geq["band_hz"])


def test_band_for_freq(cfg, geq_hz):
    nc = NotchController(cfg, geq_hz, validate_cut, budget=6)
    assert len(geq_hz) == 31
    assert nc.band_for_freq(2400.0) == 22 and geq_hz[21] == 2500      # 1-based = par number
    assert nc.band_for_freq(2200.0) == 21                              # log-nearest: 2000, not 2500
    assert nc.band_for_freq(630.0) == 16 and nc.band_for_freq(1000.0) == 18
    assert nc.band_for_freq(1.0) == 1 and nc.band_for_freq(1e6) == 31
    with pytest.raises(ValueError):
        nc.band_for_freq(0.0)


async def test_notch_sequence_minus_3_6_9_then_none(cfg, geq_hz):
    nc = NotchController(cfg, geq_hz, validate_cut, budget=6)
    w = RecordingGeqWriter()
    depths = []
    for k in range(4):
        n = nc.plan(det_at(2400.0, ts=10.0 + k), bus=3, session_id="s1")
        if n is None:
            depths.append(None)
            continue
        assert isinstance(n, Notch) and n.bus == 3 and n.band == 22 and n.freq_hz == 2500
        await w.set_band_gain(n.bus, n.band, n.depth_db)
        depths.append(n.depth_db)
    assert depths == [-3.0, -6.0, -9.0, None]
    assert w.writes == [(3, 22, -3.0), (3, 22, -6.0), (3, 22, -9.0)]
    assert len(nc.notches) == 1 and nc.notches[0].detections == 3 and nc.notches[0].session_id == "s1"
    assert nc.notches[0].ts == 12.0
    assert nc.budget_left == 5 and not nc.spent
    assert nc.gains == {22: -9.0}


def test_adjacent_detection_merges(cfg, geq_hz):
    nc = NotchController(cfg, geq_hz, validate_cut, budget=6)
    a = nc.plan(det_at(2400.0), bus=3, session_id="s")
    b = nc.plan(det_at(2100.0), bus=3, session_id="s")   # 2100 Hz -> band 21, adjacent to 22
    assert a is not None and b is not None and a is b
    assert b.band == 22 and b.depth_db == -6.0 and b.detections == 2
    assert [n.band for n in nc.notches] == [22] and nc.budget_left == 5
    # two bands apart is not adjacent (merge_adjacent_bands = 1): 1600 Hz -> band 20
    c = nc.plan(det_at(1600.0), bus=3, session_id="s")
    assert c is not None and c.band == 20 and c.depth_db == -3.0
    assert [n.band for n in nc.notches] == [20, 22] and nc.budget_left == 4
    # with merging disabled the adjacent detection opens its own band
    nc0 = NotchController(DetectorConfig(merge_adjacent_bands=0), geq_hz, validate_cut, budget=6)
    nc0.plan(det_at(2400.0), bus=3, session_id="s")
    n21 = nc0.plan(det_at(2100.0), bus=3, session_id="s")
    assert n21 is not None and n21.band == 21 and [n.band for n in nc0.notches] == [21, 22]


def test_budget_exhaustion(cfg, geq_hz):
    nc = NotchController(cfg, geq_hz, validate_cut, budget=2)
    assert nc.plan(det_at(630.0), bus=1, session_id="s").band == 16
    assert nc.plan(det_at(1000.0), bus=1, session_id="s").band == 18
    assert nc.spent and nc.budget_left == 0
    assert nc.plan(det_at(2400.0), bus=1, session_id="s") is None          # new band: refused
    deeper = nc.plan(det_at(630.0), bus=1, session_id="s")                  # existing band: still deepens
    assert deeper is not None and deeper.band == 16 and deeper.depth_db == -6.0
    assert nc.plan(det_at(700.0), bus=1, session_id="s").band == 16         # 700 Hz -> 17, merges into 16
    assert nc.plan(det_at(630.0), bus=1, session_id="s") is None            # -9 reached, budget spent
    assert nc.budget_left == 0 and [n.band for n in nc.notches] == [16, 18]


def test_existing_notches_and_policy_rejection(cfg, geq_hz):
    nc = NotchController(cfg, geq_hz, validate_cut, budget=1, existing={22: -6.0, 10: -2.0, 5: 3.0})
    assert [(n.band, n.depth_db, n.session_id, n.detections) for n in nc.notches] == [(10, -2.0, "", 0), (22, -6.0, "", 0)]
    assert nc.gains == {22: -6.0, 10: -2.0, 5: 3.0}
    assert nc.budget_left == 1                       # pre-existing cuts do not consume the budget
    n = nc.plan(det_at(2400.0), bus=3, session_id="s")
    assert n is not None and n.depth_db == -9.0 and n.session_id == "s" and n.detections == 1 and n.bus == 3
    assert nc.spent
    assert nc.plan(det_at(2400.0), bus=3, session_id="s") is None       # at notch_max_db
    assert nc.plan(det_at(200.0), bus=3, session_id="s") is None        # band 10 would need budget
    # a boosted band: -3 from +3 is 0 dB, which the policy rejects; the error propagates, state untouched
    nc2 = NotchController(cfg, geq_hz, validate_cut, budget=6, existing={22: 3.0})
    with pytest.raises(Rejected):
        nc2.plan(det_at(2400.0), bus=3, session_id="s")
    assert nc2.notches == [] and nc2.gains == {22: 3.0} and nc2.budget_left == 6
    with pytest.raises(ValueError):
        NotchController(cfg, geq_hz, validate_cut, budget=6, existing={32: -3.0})


async def test_detector_to_notch_end_to_end(cfg, band_hz, geq_hz):
    """Detections from stream (c) drive the controller: the first cut lands on the 2.5 kHz GEQ band."""
    src = SyntheticRta(band_hz, seed=5)
    src.add_ring(2400.0, start_t=1.0)
    nc = NotchController(cfg, geq_hz, validate_cut, budget=cfg.notch_budget_default)
    w = RecordingGeqWriter()
    for i, dd in run(cfg, band_hz, src.frames(120), "g:e2e"):
        n = nc.plan(dd, bus=3, session_id="e2e")
        if n is not None:
            await w.set_band_gain(3, n.band, n.depth_db)
    assert w.writes[0] == (3, 22, -3.0)
    assert [g for _, _, g in w.writes][:3] == [-3.0, -6.0, -9.0][: len(w.writes)]


# --------------------------------------------------------------------------- M7 regression tests


def _plateaued_ring(band_hz, ring_hz, *, prominence_db, frames, floor_db=-85.0, period=0.05):
    """Frames of an ALREADY-ESTABLISHED ring: loud, steady, no growth whatsoever.

    Every synthetic stream in this file until now modelled a ring *growing* from a quiet
    background, which is the easy case. On the real desk at M7 the operator let a howl run for
    15 s; by the time anyone looked at it, it had long since reached its ceiling and sat flat.
    """
    idx = min(range(len(band_hz)), key=lambda i: abs(band_hz[i] - ring_hz))
    out = []
    for n in range(frames):
        vals = [floor_db] * len(band_hz)
        vals[idx] = floor_db + prominence_db          # dead flat, frame after frame
        out.append((vals, n * period))
    return idx, out


def test_an_established_plateaued_howl_is_caught(cfg, band_hz):
    """The M7 failure: a 60 dB-prominent ring scored 0.50 forever and was never emitted.

    0.50 is exactly w_prominence + w_persistence, i.e. the ceiling when growth scores 0 — so no
    amount of loudness or patience could ever reach the 0.7 threshold.
    """
    det = FeedbackDetector(cfg, band_hz)
    idx, frames = _plateaued_ring(band_hz, 8000.0, prominence_db=60.0, frames=40)
    fired = [d for vals, ts in frames for d in det.feed(vals, ts)]
    assert fired, "an established 60 dB-prominent ring must be emitted even with zero growth"
    first = fired[0]
    assert abs(first.band - idx) <= cfg.band_tolerance
    assert first.slope_db_per_s == pytest.approx(0.0, abs=0.5), "it really is flat - no growth"
    assert first.confidence >= cfg.confidence_threshold


def test_the_override_needs_real_prominence_not_just_patience(cfg, band_hz):
    """A modest sustained peak must still be ignored: that is what the growth test is for.

    A held vocal note sits a few dB above its neighbours and plateaus. It must not be notched
    however long it is held, or the system cuts the singer.
    """
    det = FeedbackDetector(cfg, band_hz)
    # comfortably over the 12 dB qualifying prominence, well under the 25 dB override
    _, frames = _plateaued_ring(band_hz, 330.0, prominence_db=15.0, frames=120)
    fired = [d for vals, ts in frames for d in det.feed(vals, ts)]
    assert not fired, f"a modest plateaued peak must not be notched, got {len(fired)}"


def test_override_key_is_accepted_but_no_longer_needed(band_hz):
    """``override_prominence_db`` was the M7 patch on the weighted sum (a threshold bolted onto a threshold).
    The predicate detector has no weighted sum to patch: an established, solitary, stationary, 60 dB-prominent
    line at -25 dBFS is feedback by every physical test, whatever the legacy key says. The key still loads (old
    device.yaml files keep working) and is ignored; this test used to assert that setting it to 0 restored the
    pre-M7 miss, i.e. it certified the bug."""
    cfg = DetectorConfig(override_prominence_db=0.0)
    assert cfg.override_prominence_db == 0.0
    det = FeedbackDetector(cfg, band_hz)
    idx, frames = _plateaued_ring(band_hz, 8000.0, prominence_db=60.0, frames=40)
    fired = [d for vals, ts in frames for d in det.feed(vals, ts)]
    assert fired and abs(fired[0].band - idx) <= 1
