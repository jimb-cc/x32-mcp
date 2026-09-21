"""detector.py — one test per physical predicate of the redesigned discriminator (see the module docstring).

Streams are built here from first principles (a bed + tones through analyser skirts), independent of meters.py and of
tests/rtasim, so each predicate can be exercised alone. Frame i is at t = i * 0.05 s. Levels are RTA dB.
"""

from __future__ import annotations

import math
import random

import pytest

from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig, FeedbackDetector

FRAME_S = 0.05
SKIRT = (-18.0, -36.0, -47.0)      # 3rd-order skirts at ±1/±2/±3 bands (analyser brief §3)


@pytest.fixture(scope="module")
def band_hz() -> tuple[float, ...]:
    return tuple(float(h) for h in Descriptor.load().rta["band_hz"])


@pytest.fixture(scope="module")
def cfg() -> DetectorConfig:
    return DetectorConfig.from_descriptor(Descriptor.load())


def psum(*dbs: float) -> float:
    return 10.0 * math.log10(sum(10.0 ** (x / 10.0) for x in dbs))


class Spectrum:
    """A quiet, slightly ragged bed (deterministic per seed) onto which tones are power-summed with skirts."""

    def __init__(self, seed: int = 1, bed_db: float = -60.0, noise_db: float = 1.0) -> None:
        self.rng = random.Random(seed)
        self.bed_db = bed_db
        self.noise_db = noise_db

    def bed(self, t: float) -> list[float]:
        return [self.bed_db - 0.1 * i + self.rng.uniform(-self.noise_db, self.noise_db) for i in range(100)]

    @staticmethod
    def tone(vals: list[float], band: float, level_db: float) -> None:
        """Add a tone at fractional band position ``band`` (e.g. 70.5 = between two centres)."""
        b0 = int(math.floor(band))
        frac = band - b0
        # split the power between the two nearest centres by position, then apply skirts around each
        parts = [(b0, level_db + 10 * math.log10(max(1e-6, 1.0 - frac)))]
        if frac > 1e-6:
            parts.append((b0 + 1, level_db + 10 * math.log10(frac)))
        for bc, lv in parts:
            for d in range(-3, 4):
                j = bc + d
                if 0 <= j < 100:
                    att = 0.0 if d == 0 else SKIRT[abs(d) - 1]
                    vals[j] = psum(vals[j], lv + att)


def run(det: FeedbackDetector, frames: list[list[float]], t0: float = 0.0):
    out = []
    for i, f in enumerate(frames):
        for d in det.feed(f, t0 + i * FRAME_S):
            out.append((i, d))
    return out


def ring_frames(sp: Spectrum, band: float, *, n: int, start_db: float, rate_db_s: float, t_on: float = 0.5,
                cap_db: float = -8.0, partials: tuple[tuple[float, float], ...] = ()) -> list[list[float]]:
    frames = []
    for i in range(n):
        t = i * FRAME_S
        v = sp.bed(t)
        if t >= t_on:
            lv = min(cap_db, start_db + rate_db_s * (t - t_on))
            sp.tone(v, band, lv)
            for off, rel in partials:
                sp.tone(v, band + off, lv + rel)
        frames.append(v)
    return frames


# -- P1 narrow ---------------------------------------------------------------------------------------

def test_p1_broad_hump_is_not_a_line(cfg, band_hz):
    """A 5-band-wide hump 20 dB high that grows 20 dB/s (a swelling formant / cymbal wash) is never a candidate:
    peak − max(±2) stays ~4 dB (< narrow_db)."""
    sp = Spectrum(3)
    frames = []
    for i in range(120):
        t = i * FRAME_S
        v = sp.bed(t)
        h = min(20.0, max(0.0, 20.0 * (t - 0.5)))
        for d in range(-4, 5):
            v[60 + d] = psum(v[60 + d], sp.bed_db + h * math.exp(-(d * d) / 8.0))
        frames.append(v)
    assert run(FeedbackDetector(cfg, band_hz), frames) == []


def test_p1_split_line_between_centres_is_one_candidate_and_is_caught(cfg, band_hz):
    """A ring exactly between bands 70/71 reads −3/−3 dB in two bands; it must be tracked as ONE line (centroid
    70.5, interpolated frequency) and detected once it has risen rise_db."""
    sp = Spectrum(4)
    frames = ring_frames(sp, 70.5, n=80, start_db=-55.0, rate_db_s=20.0)
    dets = run(FeedbackDetector(cfg, band_hz), frames)
    assert dets, "split-line ring not detected"
    i, d = dets[0]
    assert d.band in (70, 71) and abs(d.centroid_band - 70.5) < 0.25
    assert band_hz[70] < d.freq_hz < band_hz[71]
    assert (i * FRAME_S - 0.5) <= 1.2          # from onset at -55 under a -60 bed: visible + 6 dB rise + K1
    # never two simultaneous detections for the one line
    per_frame: dict[int, int] = {}
    for i, d in dets:
        per_frame[i] = per_frame.get(i, 0) + 1
    assert max(per_frame.values()) == 1


# -- P2 harmonic family ------------------------------------------------------------------------------

def test_p2_note_with_family_is_musical_same_line_without_family_is_feedback(cfg, band_hz):
    """Identical envelope (20 dB/s ramp then hold): with H2/H3/H4 partials co-born it is a note (0 detections);
    the bare line is a ring (detected, 'no_family' in reasons)."""
    fam = ((10.0, -3.0), (15.85, -8.0), (20.0, -12.0))
    note = ring_frames(Spectrum(5), 45.3, n=100, start_db=-50.0, rate_db_s=20.0, cap_db=-25.0, partials=fam)
    bare = ring_frames(Spectrum(5), 45.3, n=100, start_db=-50.0, rate_db_s=20.0, cap_db=-25.0)
    assert run(FeedbackDetector(cfg, band_hz), note) == []
    dets = run(FeedbackDetector(cfg, band_hz), bare)
    assert dets and "no_family" in dets[0][1].reasons


def test_p2_octave_pair_born_together_is_music_two_rings_an_octave_apart_are_not(cfg, band_hz):
    """An 8'+4' organ note (H1 + H2 only, co-onset) is a family. Two rings at f and exactly 2f with different onset
    times and rates (loop brief §2.3: independent candidates do not co-onset) must BOTH be detected."""
    organ = ring_frames(Spectrum(6), 50.0, n=100, start_db=-50.0, rate_db_s=20.0, cap_db=-25.0, partials=((10.0, -4.0),))
    assert run(FeedbackDetector(cfg, band_hz), organ) == []
    sp = Spectrum(7)
    frames = []
    for i in range(120):
        t = i * FRAME_S
        v = sp.bed(t)
        if t >= 0.5:
            sp.tone(v, 60.0, min(-10.0, -55.0 + 40.0 * (t - 0.5)))
        if t >= 1.2:
            sp.tone(v, 70.0, min(-12.0, -55.0 + 55.0 * (t - 1.2)))
        frames.append(v)
    got = {d.band for _, d in run(FeedbackDetector(cfg, band_hz), frames)}
    assert 60 in got and 70 in got, got


def test_p2_clipped_howl_keeps_no_family_veto_and_its_harmonics_are_not_cut(cfg, band_hz):
    """A howl pinned at the clip flag (0.0) with odd partials H3 −12 / H5 −18: the fundamental is detected (LOUD
    escape), its H3/H5 lines are somebody's harmonics and are never emitted."""
    sp = Spectrum(8)
    frames = []
    for i in range(60):
        v = sp.bed(i * FRAME_S)
        sp.tone(v, 67.0, 0.0)
        sp.tone(v, 67.0 + 15.85, -12.0)
        sp.tone(v, 67.0 + 23.22, -18.0)
        frames.append([min(0.0, x) for x in v])
    dets = run(FeedbackDetector(cfg, band_hz), frames)
    assert dets and all(abs(d.band - 67) <= 1 for _, d in dets), {d.band for _, d in dets}
    assert "loud" in dets[0][1].reasons


# -- P3 stable ---------------------------------------------------------------------------------------

def test_p3_gliding_tone_is_never_stable(cfg, band_hz):
    """A family-less tone rising 20 dB/s while gliding 4 bands/s (480 cents/s: portamento / siren / 808 sweep)
    never holds its centroid within ±centroid_tol_bands for K1 frames: no detection. (Boundary, stated honestly:
    a glide slower than 2·centroid_tol_bands per K1 frames = 2 bands/s passes P3 and is then only re-born once per
    drift window; nothing in live programme glides that slowly for seconds while growing without partials.)"""
    sp = Spectrum(9)
    frames = []
    for i in range(100):
        t = i * FRAME_S
        v = sp.bed(t)
        if t >= 0.5:
            sp.tone(v, 55.0 + 4.0 * (t - 0.5), min(-15.0, -50.0 + 20.0 * (t - 0.5)))
        frames.append(v)
    assert run(FeedbackDetector(cfg, band_hz), frames) == []


def test_p3_vibrato_straddling_a_band_edge_is_rejected(cfg, band_hz):
    """±80 cent vibrato at 5.5 Hz centred on a band edge: the centroid swings ~±0.6 band -> unstable -> nothing,
    however long it is held (no partials given, so only P3/RISE protect it)."""
    sp = Spectrum(10)
    frames = []
    for i in range(160):
        t = i * FRAME_S
        v = sp.bed(t)
        if t >= 0.5:
            cents = 80.0 * math.sin(2 * math.pi * 5.5 * t) * min(1.0, (t - 0.5) / 0.4)
            sp.tone(v, 52.5 + cents / 120.0, -28.0)
        frames.append(v)
    assert run(FeedbackDetector(cfg, band_hz), frames) == []


# -- P4 sustained ------------------------------------------------------------------------------------

def test_p4_decaying_line_is_not_re_emitted(cfg, band_hz):
    """A ring that is detected while growing and then decays 8 dB/s (loop broken by a cut; long RTA release) must
    not keep re-emitting once it is falling: every detection happens while the line is not decaying."""
    sp = Spectrum(11)
    frames = []
    for i in range(200):
        t = i * FRAME_S
        v = sp.bed(t)
        if t >= 0.5:
            lv = -55.0 + 25.0 * (t - 0.5) if t < 2.3 else -10.0 - 8.0 * (t - 2.3)
            sp.tone(v, 72.0, min(-10.0, lv))
        frames.append(v)
    dets = run(FeedbackDetector(cfg, band_hz), frames)
    assert dets
    assert all(i * FRAME_S <= 2.3 + 0.6 for i, _ in dets), [round(i * FRAME_S, 2) for i, _ in dets]


# -- common mode / P5 baseline / at-arm --------------------------------------------------------------

def test_common_mode_raise_is_not_growth(cfg, band_hz):
    """Three established family-less lines (organ-ish) and the whole spectrum rise +10 dB over 1 s together
    (operator pushes the master, prog_coupling 1): the rise is common mode, not loop growth -> 0 detections."""
    sp = Spectrum(12, bed_db=-62.0)
    frames = []
    for i in range(160):
        t = i * FRAME_S
        g = 10.0 * min(1.0, max(0.0, (t - 3.0) / 1.0))
        v = [x + g for x in sp.bed(t)]
        for b in (44.0, 51.2, 57.6):
            sp.tone(v, b, -45.0 + g)
        frames.append(v)
    assert run(FeedbackDetector(cfg, band_hz), frames) == []


def test_p5_lines_present_at_arm(cfg, band_hz):
    """Present from frame 0: mains hum −46 with an exact family -> never; a family-less whine at −50 -> never
    (below arm_line_min_level_db); an established ring at −8 -> detected within 300 ms with 'established_at_arm'."""
    sp = Spectrum(13, bed_db=-70.0)
    frames = []
    for i in range(80):
        v = sp.bed(i * FRAME_S)
        for k, rel in ((1, 0.0), (2, -6.0), (3, -3.0), (4, -14.0), (5, -8.0)):      # 50 Hz hum + buzz family
            sp.tone(v, 10.0 * math.log2(50.0 * k / 19.53), -46.0 + rel)
        sp.tone(v, 10.0 * math.log2(587.0 / 19.53), -50.0)                          # HVAC whine
        sp.tone(v, 87.0, -8.0)                                                       # established ring, 8.1 kHz
        frames.append(v)
    dets = run(FeedbackDetector(cfg, band_hz), frames)
    assert dets and all(abs(d.band - 87) <= 1 for _, d in dets), {d.band for _, d in dets}
    assert dets[0][0] * FRAME_S <= 0.3 and "established_at_arm" in dets[0][1].reasons


# -- P6 window + LF analyser smear -------------------------------------------------------------------

def _lf_onset_frames(band_hz, band: int, *, level_db: float, tau_s: float, hold_s: float, n: int, seed: int):
    """An acoustically INSTANT LF tone rendered through a one-pole band filter of time constant tau (the analyser's
    own rise time at that band, loop brief §1.6): a decelerating multi-frame 'growth' that is not growth."""
    sp = Spectrum(seed, bed_db=-64.0)
    frames = []
    for i in range(n):
        t = i * FRAME_S
        v = sp.bed(t)
        if 0.5 <= t <= 0.5 + hold_s:
            frac = 1.0 - math.exp(-(t - 0.5 + FRAME_S) / tau_s)
            sp.tone(v, float(band), level_db + 10.0 * math.log10(max(1e-6, frac)))
        frames.append(v)
    return frames


def test_p6_lf_note_onset_through_the_analyser_is_not_feedback_even_with_lf_enabled(cfg, band_hz):
    """Instant bass/808 onsets at 41 Hz (tau 185 ms) and 78 Hz (tau 92 ms), family-less, 25-30 dB prominent, held
    1.5 s: 0 detections in watch mode (outside the window) AND with lf_feedback_possible (the analyser-settle
    allowance, not the window, is what protects them)."""
    for band, tau in ((11, 0.185), (20, 0.092)):
        frames = _lf_onset_frames(band_hz, band, level_db=-30.0, tau_s=tau, hold_s=1.5, n=80, seed=20 + band)
        assert run(FeedbackDetector(cfg, band_hz), frames) == []
        assert run(FeedbackDetector(cfg, band_hz, lf_feedback_possible=True), frames) == []


def test_p6_genuine_lf_ring_needs_the_lf_declaration(cfg, band_hz):
    """A 65 Hz loop growing 20 dB/s for 2 s (kick mic into a sub): ignored in watch mode (below 160 Hz), detected
    with lf_feedback_possible=True and in ring_out mode (63 Hz edge) within ~1 s of becoming visible."""
    sp = Spectrum(30, bed_db=-64.0)
    frames = ring_frames(sp, 17.3, n=100, start_db=-62.0, rate_db_s=20.0, cap_db=-12.0)
    assert run(FeedbackDetector(cfg, band_hz), frames) == []
    for det in (FeedbackDetector(cfg, band_hz, lf_feedback_possible=True), FeedbackDetector(cfg, band_hz, mode="ringout")):
        dets = run(det, frames)
        assert dets and abs(dets[0][1].band - 17) <= 1
        assert any(r.startswith("rise") for r in dets[0][1].reasons)
        assert dets[0][0] * FRAME_S - 0.5 <= 1.6      # onset at -62 under a -64 bed; visible ~0.4 s later


# -- active probe (ring_out) -------------------------------------------------------------------------

def test_probe_over_response_is_evidence_and_linear_response_is_stationary(cfg, band_hz):
    """ring_out: the server steps the master +1 dB every 1.5 s and tells the detector. A sub-threshold loop line
    answers +4 dB per step (regenerative gain 1/(1-g), loop brief §1.5) -> 'probe' evidence, emitted BEFORE any
    runaway; a whine that follows the gain 1 dB/dB is tagged STATIONARY and never emitted."""
    sp = Spectrum(40, bed_db=-70.0, noise_db=0.5)
    det = FeedbackDetector(cfg, band_hz, mode="ringout")
    steps = [1.0 + 1.5 * k for k in range(6)]
    dets = []
    for i in range(220):
        t = i * FRAME_S
        n_steps = sum(1 for s in steps if s <= t)
        for s in steps:
            if abs(t - s) < 1e-9:
                det.note_gain_step(1.0, t)
        v = [x + n_steps * 1.0 for x in sp.bed(t)]                       # everything through the mic: +1 dB/step
        sp.tone(v, 62.0, -55.0 + 4.0 * n_steps)                           # latent loop mode: +4 dB per step
        sp.tone(v, 48.3, -45.0 + 1.0 * n_steps)                           # HVAC whine: +1 dB per step
        for d in det.feed(v, t):
            dets.append((i, d))
    assert dets and all(abs(d.band - 62) <= 1 for _, d in dets), {d.band for _, d in dets}
    assert any(any(r.startswith("probe") for r in d.reasons) for _, d in dets)
    whine = [c for c in det.candidates if abs(c.centroid - 48.3) < 0.6]
    assert whine and whine[0].stationary and whine[0].klass in ("STATIONARY", "TRACK")


# -- contract ----------------------------------------------------------------------------------------

def test_detection_carries_reasons_and_interpolated_frequency(cfg, band_hz):
    sp = Spectrum(50)
    frames = ring_frames(sp, 66.7, n=80, start_db=-55.0, rate_db_s=30.0)
    dets = run(FeedbackDetector(cfg, band_hz), frames)
    assert dets
    d = dets[0][1]
    dd = d.to_dict()
    assert set(dd) >= {"ts", "band", "freq_hz", "level_db", "prominence_db", "slope_db_per_s", "frames", "confidence",
                       "reasons", "class", "centroid_band"}
    assert d.klass == "STRONG" and d.confidence >= cfg.confidence_threshold
    assert band_hz[66] < d.freq_hz < band_hz[67]
    assert {"narrow", "no_family", "stable", "sustained", "new_energy", "in_window"} <= set(d.reasons)


def test_cost_per_frame_is_small(cfg, band_hz):
    """O(bands·k) per frame: well under a millisecond of pure Python on a busy music frame."""
    import time
    sp = Spectrum(60, bed_db=-50.0, noise_db=3.0)
    frames = []
    for i in range(400):
        v = sp.bed(i * FRAME_S)
        for b in (33.3, 41.0, 47.7, 55.2, 60.4, 66.1, 72.9):
            sp.tone(v, b, -30.0 + 3 * math.sin(i / 7.0 + b))
        frames.append(v)
    det = FeedbackDetector(cfg, band_hz)
    t0 = time.perf_counter()
    run(det, frames)
    per_frame = (time.perf_counter() - t0) / len(frames)
    print(f"[cost] {per_frame * 1e6:.0f} us/frame")
    assert per_frame < 0.005
