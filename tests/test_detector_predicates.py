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


def test_f2_probe_bookkeeping_survives_forty_steps(cfg, band_hz):
    """F2: a -40 -> 0 dB ring-out has 40 master steps; the detector prunes old steps from its list but tracks remember the
    SEQUENCE NUMBERS they judged, so probe evidence keeps working past step 32: a latent loop line that appears only after
    step 34 (+4 dB per step) collects probe hits on steps 36+ and is emitted on 'probe' evidence, while a whine following
    every step 1 dB/dB stays STATIONARY through all the pruning; the step list stays bounded."""
    sp = Spectrum(41, bed_db=-75.0, noise_db=0.5)
    det = FeedbackDetector(cfg, band_hz, mode="ringout")
    steps = [1.0 + 1.5 * k for k in range(40)]
    dets = []
    max_steps_kept = 0
    n = int((steps[-1] + 3.0) / FRAME_S)
    for i in range(n):
        t = i * FRAME_S
        n_steps = sum(1 for s_ in steps if s_ <= t + 1e-9)
        for s_ in steps:
            if abs(t - s_) < 1e-9:
                det.note_gain_step(1.0, t)
        max_steps_kept = max(max_steps_kept, len(det._steps))
        v = [x + n_steps * 0.3 for x in sp.bed(t)]                       # room noise through the mic: +0.3 dB/step (spill)
        if n_steps >= 34:
            sp.tone(v, 62.0, -60.0 + 4.0 * (n_steps - 34))                # latent loop mode surfacing late: +4 dB per step
        sp.tone(v, 48.3, -45.0 + 0.3 * n_steps)                           # whine following the programme coupling
        for d in det.feed(v, t):
            dets.append((round(t, 2), d))
    assert max_steps_kept <= 64, max_steps_kept
    assert len(det._steps) < 40, "old steps are pruned"
    loop = [c for c in det.candidates if abs(c.centroid - 62.0) < 1.0]
    assert loop and loop[0].probe_hits >= cfg.probe_min_hits, [(c.probe_hits, c.steps_seen) for c in loop]
    assert max(loop[0].judged_steps) >= 36 and all(isinstance(k, int) for k in loop[0].judged_steps), sorted(loop[0].judged_steps)
    assert loop[0].judged_steps <= {st[0] for st in det._steps} | set(range(1, 41)), "judged steps are sequence numbers, not list positions"
    assert dets and all(abs(d.band - 62) <= 1 for _, d in dets), {d.band for _, d in dets}
    assert any(any(r.startswith("probe") for r in d.reasons) for _, d in dets)
    assert dets[0][0] > steps[34], "emitted on the late steps, i.e. after more than 32 steps had been noted"
    whine = [c for c in det.candidates if abs(c.centroid - 48.3) < 0.6]
    assert whine and whine[0].probe_hits == 0 and whine[0].klass in ("STATIONARY", "TRACK", "MODERATE") and not whine[0].emitted


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


# -- G1: back-filled FAST-RISE ------------------------------------------------------------------------

def _howl_into_plateau(sp: Spectrum, band: float, *, rate_db_s: float, plateau_db: float, bed_db: float, t_on: float, n: int):
    """A loop that grows dB-linearly at ``rate_db_s`` from far under the bed to a limiter plateau (loud bed: the line is
    visible for only a few frames before the knee)."""
    frames = []
    for i in range(n):
        t = i * FRAME_S
        v = sp.bed(t)
        if t >= t_on:
            sp.tone(v, band, min(plateau_db, bed_db - 30.0 + rate_db_s * (t - t_on)))
        frames.append(v)
    return frames


def test_g1_fast_howl_watched_into_its_plateau_is_fast_rise_an_instant_arrival_is_only_moderate(cfg, band_hz):
    """A 160 dB/s howl (8 dB per frame) under a −40 bed plateaus at −14 dBFS 3-4 frames after it clears the bed: the
    back-filled pre-birth frames + the first tracked frames are >= 3 watched increments -> 'fastrise' within 300 ms of
    visibility. The same plateau reached in ONE frame (a sine-lead note, a whistle -- or a >400 dB/s howl: passively
    identical [L §1.3]) is published as a MODERATE candidate at K1 and never cut by the detector itself."""
    sp = Spectrum(70, bed_db=-40.0, noise_db=1.5)
    frames = _howl_into_plateau(sp, 61.4, rate_db_s=160.0, plateau_db=-14.0, bed_db=-40.0, t_on=1.0, n=80)
    det = FeedbackDetector(cfg, band_hz)
    dets = run(det, frames)
    assert dets and abs(dets[0][1].band - 61) <= 1
    assert any(r.startswith("fastrise") for r in dets[0][1].reasons), dets[0][1].reasons
    t_vis = 1.0 + (40.0 - 30.0 - 30.0 + 30.0) / 160.0          # crosses the bed ~1.19 s
    assert dets[0][0] * FRAME_S - 1.19 <= 0.35
    # instant arrival at the same plateau: MODERATE at K1, no emission
    sp2 = Spectrum(71, bed_db=-40.0, noise_db=1.5)
    frames2 = []
    for i in range(80):
        t = i * FRAME_S
        v = sp2.bed(t)
        if t >= 1.0:
            sp2.tone(v, 61.4, -14.0)
        frames2.append(v)
    det2 = FeedbackDetector(cfg, band_hz)
    published = None
    for i, f in enumerate(frames2):
        assert det2.feed(f, i * FRAME_S) == []
        for c in det2.candidates:
            if abs(c.band - 61) <= 1 and c.klass == "MODERATE" and published is None:
                published = i * FRAME_S
                dd = c.to_dict()
                assert {"klass", "level_db", "prominence_db", "excess_db", "age_s", "reasons", "freq_hz"} <= set(dd)
    assert published is not None and published - 1.0 <= 0.26


# -- G2: re-emission only on fresh evidence; note_cut() post-cut classification -------------------------

def _established_line_frames(sp: Spectrum, band: float, level_fn, n: int):
    frames = []
    for i in range(n):
        v = sp.bed(i * FRAME_S)
        lv = level_fn(i * FRAME_S)
        if lv is not None:
            sp.tone(v, band, lv)
        frames.append(v)
    return frames


def test_g2_held_line_cut_on_plateau_evidence_and_loudish_is_deepened_once_per_verdict(cfg, band_hz):
    """An established −15 dBFS line (AT-ARM at K1, loud-ish) is cut −3 dB at t=1 (note_cut); it drops EXACTLY 3 dB and
    stays: a limiter-held howl with more excess than the cut and a held note read the same, and for a loud-ish line cut on
    plateau-class evidence the detector deepens -- ONE re-emission per 'held' verdict (cut_verify_s after the cut, reason
    'deepen_held'), never a free-running ratchet: with no further note_cut() there is no further emission."""
    sp = Spectrum(72, bed_db=-70.0, noise_db=0.5)
    hz = 2000.0
    band = 10.0 * math.log2(hz / 19.53)
    frames = _established_line_frames(sp, band, lambda t: -15.0 if t < 1.0 else -18.0, 120)
    det = FeedbackDetector(cfg, band_hz)
    dets = []
    for i, f in enumerate(frames):
        t = i * FRAME_S
        if abs(t - 1.0) < 1e-9:
            det.note_cut(hz, -3.0, t)
        for d in det.feed(f, t):
            dets.append((t, d))
    first = [t for t, d in dets if t < 1.0]
    assert first and first[0] <= 0.3                         # established at arm, >= -20 dBFS: K1
    assert not [t for t, d in dets if 1.0 <= t < 1.0 + cfg.cut_verify_s], "no re-emission before the cut is verified"
    later = [(t, d) for t, d in dets if t >= 1.0 + cfg.cut_verify_s]
    assert len(later) == 1, "exactly one deepen per 'held' verdict (no note_cut followed it, so no second one)"
    assert "deepen_held" in later[0][1].reasons
    assert det.cut_log and det.cut_log[0]["verdict"] == "held" and det.cut_log[0]["deepen"] is True
    cand = [c for c in det.candidates if abs(c.freq_hz - hz) < 100][0]
    assert cand.cut_verdict == "held" and cand.cut_deepen is False and cand.cuts_held == 1


def test_g2_held_line_cut_on_rise_alone_is_cut_once_and_regrowth_re_admits_it(cfg, band_hz):
    """A family-less line swelling 25 dB/s from −50 to a −30 plateau is cut once on RISE (a slow swell / a slow ring: e < 3 dB
    if it is a loop, so a 3 dB cut takes a loop under); it drops by the bell and holds = 'held' with NO deepen right (rise-only
    evidence, and quiet): never re-emitted while it sits there. When it later grows 7 dB above the held level it is re-emitted
    (fresh regrowth), and a line that ENDS by itself after a cut is 'false_cut' (klass FALSE_CUT)."""
    hz = 2000.0
    band = 10.0 * math.log2(hz / 19.53)
    def level(t):
        if t < 0.5:
            return None
        lv = min(-30.0, -50.0 + 25.0 * (t - 0.5))
        if t >= 2.5:
            lv -= 3.0                                              # the cut's bell
        if t >= 5.0:
            lv += min(7.0, 40.0 * (t - 5.0))                       # regrowth
        return lv
    sp = Spectrum(75, bed_db=-70.0, noise_db=0.5)
    frames = _established_line_frames(sp, band, level, 140)
    det = FeedbackDetector(cfg, band_hz)
    dets = []
    for i, f in enumerate(frames):
        t = i * FRAME_S
        if abs(t - 2.5) < 1e-9:
            det.note_cut(hz, -3.0, t)
        for d in det.feed(f, t):
            dets.append((t, d))
    first = [(t, d) for t, d in dets if t < 2.5]
    assert first and any(r.startswith("rise") for r in first[0][1].reasons) and not any(r.startswith("fastrise") for r in first[0][1].reasons)
    assert not [t for t, d in dets if 2.5 <= t < 5.0], "a rise-only line sitting 3 dB down after the cut is never re-emitted"
    held = [x for x in det.cut_log if x["verdict"] == "held"]
    assert held and held[0]["deepen"] is False
    again = [(t, d) for t, d in dets if t >= 5.0]
    assert again, "7 dB of regrowth above the held level re-admits the line"
    # (b) the same kind of line that ENDS 0.8 s after the cut: 'false_cut', klass FALSE_CUT while the track coasts
    sp = Spectrum(76, bed_db=-70.0, noise_db=0.5)
    frames = _established_line_frames(sp, band, lambda t: (None if t < 0.5 or t >= 3.3 else min(-30.0, -50.0 + 25.0 * (t - 0.5)) - (3.0 if t >= 2.5 else 0.0)), 80)
    det = FeedbackDetector(cfg, band_hz)
    for i, f in enumerate(frames):
        t = i * FRAME_S
        if abs(t - 2.5) < 1e-9:
            det.note_cut(hz, -3.0, t)
        det.feed(f, t)
    assert [x["verdict"] for x in det.cut_log] == ["false_cut"], det.cut_log


def test_g2_note_cut_confirms_a_killed_ring_and_flags_an_insufficient_cut(cfg, band_hz):
    """Same line; (a) after the cut it collapses 20 dB within two frames -> 'confirmed'; (b) it drops only 0.5 dB ->
    'insufficient' (excess larger than the bell: deepening is legitimate, and a +4 dB regrowth re-emits)."""
    hz = 2000.0
    band = 10.0 * math.log2(hz / 19.53)
    for drop, want in ((20.0, "confirmed"), (0.5, "insufficient")):
        sp = Spectrum(73, bed_db=-70.0, noise_db=0.5)
        frames = _established_line_frames(sp, band, lambda t: -15.0 if t < 1.05 else -15.0 - drop, 60)
        det = FeedbackDetector(cfg, band_hz)
        for i, f in enumerate(frames):
            t = i * FRAME_S
            if abs(t - 1.0) < 1e-9:
                det.note_cut(hz, -3.0, t)
            det.feed(f, t)
        assert det.cut_log and det.cut_log[-1]["verdict"] == want, (want, det.cut_log)


def test_g2_bell_attenuation_matches_the_loop_brief_table(cfg, band_hz):
    """RBJ peaking cut: −3 dB slider reads −3.0/−2.75/−2.2/−1.5/−0.6 at 0/0.05/0.10/0.167/0.333 oct for Q 4.3 and
    −3.0/−2.9/−2.8/−2.5/−1.6 for Q 2 (loop brief §4.1)."""
    f = FeedbackDetector.bell_attenuation_db
    assert [round(f(-3.0, x, 4.3), 1) for x in (0, 0.05, 0.10, 0.167, 0.333)] == [3.0, 2.8, 2.2, 1.5, 0.6]
    assert [round(f(-3.0, x, 2.0), 1) for x in (0, 0.05, 0.10, 0.167, 0.333)] == [3.0, 2.9, 2.8, 2.5, 1.6]


# -- G3: frozen display ------------------------------------------------------------------------------------

def test_g3_bit_identical_line_is_frozen_not_evidence(cfg, band_hz):
    """A −8 dBFS line whose value AND skirts repeat bit for bit (peak-hold reaching the stream freezes a line together with
    its neighbours) over a live bed: FROZEN_LINES, never emitted. The same line with ±0.2 dB of life is an established howl,
    emitted at K1 -- and so is a rock-steady tone whose PEAK band repeats its code exactly while its skirts (18+ dB nearer the
    noise) still jitter: a brick-wall-limited howl in still air is not a frozen display."""
    for mode, expect in (("peakhold", 0), ("live", 1), ("steady_peak_only", 1)):
        sp = Spectrum(74, bed_db=-70.0, noise_db=1.0)
        rng = random.Random(5)
        frames = []
        for i in range(40):
            v = sp.bed(i * FRAME_S)
            sp.tone(v, 80.0, -8.0 + (rng.uniform(-0.2, 0.2) if mode == "live" else 0.0))
            if mode == "peakhold":
                v[79], v[80], v[81] = -26.0, -8.0, -26.0                # the display holds the line and its skirts exactly
            elif mode == "steady_peak_only":
                v[80] = -8.0                                            # only the peak code repeats; the skirts carry bed jitter
            frames.append(v)
        det = FeedbackDetector(cfg, band_hz)
        dets = run(det, frames)
        assert bool(dets) == bool(expect), (mode, dets[:2])
        if mode == "peakhold":
            assert "FROZEN_LINES" in det.flags
        else:
            assert dets[0][0] <= 6


# -- G4: ring_out -- AT-ARM waits for the probe; follows / pinned ----------------------------------------------

def _ringout_whine_run(cfg, band_hz, *, response_db_per_db: float, n: int = 120):
    """A family-less −36 dBFS whine present at arm in a quiet room; ring_out steps +1 dB at 2.0 and 3.5 s and the whine
    answers ``response_db_per_db`` per dB; the room noise (through the mic) follows 1 dB/dB."""
    sp = Spectrum(75, bed_db=-72.0, noise_db=0.5)
    det = FeedbackDetector(cfg, band_hz, mode="ringout")
    steps = [2.0, 3.5]
    dets = []
    for i in range(n):
        t = i * FRAME_S
        k = sum(1 for s in steps if s <= t)
        for s in steps:
            if abs(t - s) < 1e-9:
                det.note_gain_step(1.0, t)
        v = [x + k for x in sp.bed(t)]
        sp.tone(v, 59.0, -36.0 + response_db_per_db * k)
        for d in det.feed(v, t):
            dets.append((t, d))
    whine = [c for c in det.candidates if abs(c.centroid - 59.0) < 0.6][0]
    return dets, whine


def test_g4_ringout_at_arm_line_waits_for_the_probe_and_a_1dB_per_dB_answer_is_stationary(cfg, band_hz):
    """In ring_out nothing regenerative can be ESTABLISHED at arm unless it is already loud: a −36 dBFS whine is not
    emitted at 0.8 s (as watch would); it follows the two +1 dB steps 1 dB/dB -> STATIONARY, never emitted."""
    dets, whine = _ringout_whine_run(cfg, band_hz, response_db_per_db=1.0)
    assert dets == []
    assert whine.stationary and whine.klass == "STATIONARY" and whine.steps_seen >= 2
    # watch mode (no probe available) emits the same line once at arm_confirm_s: the documented irreducible pair
    sp = Spectrum(75, bed_db=-72.0, noise_db=0.5)
    frames = _established_line_frames(sp, 59.0, lambda t: -36.0, 40)
    wd = run(FeedbackDetector(cfg, band_hz), frames)
    assert len(wd) == 1 and "established_at_arm" in wd[0][1].reasons and 0.8 <= wd[0][0] * FRAME_S <= 0.9


def test_g4_a_line_that_ignores_the_steps_is_not_stationary_and_becomes_eligible_after_two_judged_steps(cfg, band_hz):
    """A plateau held downstream of the pre-fader tap (speaker limiter) answers 0 dB/dB: 'pinned' is not programme
    evidence -- after probe_min_hits judged steps AT-ARM may emit it."""
    dets, whine = _ringout_whine_run(cfg, band_hz, response_db_per_db=0.0)
    assert not whine.stationary and whine.probe_pinned >= 2
    assert dets and "established_at_arm" in dets[0][1].reasons and dets[0][0] >= 3.5


# -- G5: independent partner ------------------------------------------------------------------------------

def test_g5_notes_arriving_at_a_steady_lines_harmonic_slots_are_not_its_family(cfg, band_hz):
    """An established −25 dBFS ring (emitted at arm_confirm_s in watch); at 0.3 s two programme lines appear exactly at
    its H2 and H3 slots and stay. Born together with the ring they would be its family (organ 8'+4'+2 2/3'); born 6
    frames AFTER it while it held dead steady they are independent sources -> the ring is still emitted."""
    def build(partials_from: float):
        sp = Spectrum(76, bed_db=-68.0, noise_db=0.5)
        frames = []
        for i in range(40):
            t = i * FRAME_S
            v = sp.bed(t)
            sp.tone(v, 55.3, -25.0)
            if t >= partials_from:
                sp.tone(v, 65.3, -30.0)
                sp.tone(v, 55.3 + 15.85, -33.0)
            frames.append(v)
        return frames
    late = run(FeedbackDetector(cfg, band_hz), build(0.3))
    assert late and abs(late[0][1].band - 55) <= 1 and "established_at_arm" in late[0][1].reasons
    together = run(FeedbackDetector(cfg, band_hz), build(0.0))
    assert together == []


# -- G6: LOUD is referenced to the arm-time spectrum; HOT_SPECTRUM -----------------------------------------

def test_g6_loud_line_scales_with_the_arm_spectrum_and_a_hot_display_disables_the_clip_flag(cfg, band_hz):
    """A whistle-like line arriving instantly at −9 dBFS over a QUIET bed (p95 ~ −60) is LOUD (>= −10 and 6 dB above all
    else) -> cut at K1 [tier A, L §4.3]; the same line over a display whose arm-time p95 is −25 (loud show / RTA gain
    offset: indistinguishable) needs >= min(p95 + 20, loud_ceiling −6) = −6 dBFS and is only MODERATE; with the whole bed
    within 20 dB of full scale the spectrum is HOT: 0.0 (the clip flag) is then not LOUD, a line at −4 that stands >= 3 dB
    above the loudest cell of the arm window IS (an electronic limit above anything the programme reached), and the same −4
    line in a hot show whose arm window already contained a −4 line is not."""
    def scene(bed_db, line_db, seed, pre_line_db=None):
        sp = Spectrum(seed, bed_db=bed_db, noise_db=1.0)
        frames = []
        for i in range(70):
            t = i * FRAME_S
            v = sp.bed(t)
            if pre_line_db is not None and 0.3 <= t < 1.3:
                sp.tone(v, 40.0, pre_line_db)                      # a programme line that loud during the arm window
            if t >= 2.5:
                sp.tone(v, 68.0, line_db)
            frames.append([min(0.0, x) for x in v])
        return frames
    quiet = FeedbackDetector(cfg, band_hz)
    d1 = run(quiet, scene(-60.0, -9.0, 77))
    assert d1 and "loud" in d1[0][1].reasons and d1[0][0] * FRAME_S - 2.5 <= 0.3
    loud_show = FeedbackDetector(cfg, band_hz)
    assert run(loud_show, scene(-25.0, -9.0, 78)) == []
    assert loud_show.arm_p95_db is not None and -9.0 < loud_show.loud_threshold_db <= cfg.loud_ceiling_db
    hot = FeedbackDetector(cfg, band_hz)
    assert run(hot, scene(-14.0, 0.0, 79)) == []
    assert "HOT_SPECTRUM" in hot.flags
    hot2 = FeedbackDetector(cfg, band_hz)
    d2 = run(hot2, scene(-14.0, -4.0, 79))
    assert d2 and "loud" in d2[0][1].reasons and "HOT_SPECTRUM" in hot2.flags
    hot3 = FeedbackDetector(cfg, band_hz)
    assert run(hot3, scene(-14.0, -4.0, 79, pre_line_db=-4.0)) == [] and hot3.arm_max_db >= -5.0


# -- hooks and input hygiene ---------------------------------------------------------------------------------

def test_lf_edge_hz_overrides_the_mode_window(cfg, band_hz):
    """cfs derives the low edge from the open channels' HPF and passes it: it replaces the mode default (F5)."""
    assert FeedbackDetector(cfg, band_hz).cfg.window_low_hz == cfg.window_low_hz_watch
    assert FeedbackDetector(cfg, band_hz, mode="ringout").cfg.window_low_hz == cfg.window_low_hz_ringout
    assert FeedbackDetector(cfg, band_hz, lf_edge_hz=56.0).cfg.window_low_hz == 56.0
    assert FeedbackDetector(cfg, band_hz, mode="ringout", lf_feedback_possible=True, lf_edge_hz=90.0).cfg.window_low_hz == 90.0
    assert DetectorConfig.from_dict({"lf_edge_hz": "null"}).lf_edge_hz is None
    assert DetectorConfig.from_dict({"lf_edge_hz": 70}).window_low_hz == 70.0


def test_note_cut_by_rta_band_and_ambiguous_verdict(cfg, band_hz):
    """note_cut(band=...) addresses the cut by RTA band; a line that comes down SLOWLY after the cut (neither the bell at
    once nor a collapse) is 'ambiguous' at cut_verify_s."""
    hz_band = 66
    sp = Spectrum(90, bed_db=-70.0, noise_db=0.5)
    frames = _established_line_frames(sp, float(hz_band), lambda t: -15.0 if t < 1.0 else -15.0 - 4.0 * (t - 1.0), 60)
    det = FeedbackDetector(cfg, band_hz)
    for i, f in enumerate(frames):
        t = i * FRAME_S
        if abs(t - 1.0) < 1e-9:
            det.note_cut(band=hz_band, depth_db=-3.0, ts=t)
        det.feed(f, t)
    assert det.cut_log and det.cut_log[-1]["verdict"] == "ambiguous", det.cut_log


def test_slow_release_is_measured_from_live_bands_only(cfg, band_hz):
    """The display release rate is self-measured (20 x the largest single-frame fall of a live reference band): a jittery
    bed reads >= 30 dB/s and raises nothing; a bed whose falls are limited to 0.5 dB/frame (decay ~ 10 dB/s) raises
    SLOW_RELEASE; a floor-pinned display (every reference band at -128) measures nothing and raises nothing."""
    sp = Spectrum(91, bed_db=-60.0, noise_db=1.5)
    det = FeedbackDetector(cfg, band_hz)
    run(det, [sp.bed(i * FRAME_S) for i in range(60)])
    assert det.release_db_per_s is not None and det.release_db_per_s >= cfg.slow_release_db_per_s and "SLOW_RELEASE" not in det.flags
    det = FeedbackDetector(cfg, band_hz)
    prev = None
    frames = []
    for i in range(60):
        v = sp.bed(i * FRAME_S)
        if prev is not None:
            v = [max(x, p - 0.5) for x, p in zip(v, prev)]           # a display that cannot fall faster than 10 dB/s
        prev = v
        frames.append(v)
    run(det, frames)
    assert det.release_db_per_s is not None and det.release_db_per_s < cfg.slow_release_db_per_s and "SLOW_RELEASE" in det.flags
    det = FeedbackDetector(cfg, band_hz)
    run(det, [[-128.0] * 100 for _ in range(60)])
    assert det.release_db_per_s is None and "SLOW_RELEASE" not in det.flags


def test_non_finite_and_out_of_range_values_are_clamped(cfg, band_hz):
    """One NaN / inf frame must not poison the baseline or the arm reference: values are clamped to [-128, 0]."""
    sp = Spectrum(92, bed_db=-70.0, noise_db=1.0)
    det = FeedbackDetector(cfg, band_hz)
    frames = [sp.bed(i * FRAME_S) for i in range(20)] + [[float("nan")] * 100, [float("inf")] * 100, [float("-inf")] * 100]
    frames += ring_frames(Spectrum(93, bed_db=-70.0, noise_db=1.0), 60.0, n=60, start_db=-50.0, rate_db_s=40.0, t_on=0.0)
    dets = run(det, frames)
    assert det.arm_p95_db is not None and -128.0 <= det.arm_p95_db <= 0.0
    assert all(-128.0 <= b <= 0.0 for b in det._base)
    assert dets, "a 40 dB/s ring after the bad frames is still detected"
    with pytest.raises(ValueError):
        det.feed([-60.0] * 99, 10.0)


def test_refresh_arm_reference_reopens_the_window(cfg, band_hz):
    """Armed in silence (p95 ~ -70), then the show starts at -25: refresh_arm_reference() re-measures the reference so the
    loud line follows the show instead of the empty room."""
    quiet = Spectrum(94, bed_db=-70.0, noise_db=1.0)
    show = Spectrum(95, bed_db=-25.0, noise_db=1.0)
    det = FeedbackDetector(cfg, band_hz)
    run(det, [quiet.bed(i * FRAME_S) for i in range(60)])
    p95_quiet = det.arm_p95_db
    det.refresh_arm_reference()
    run(det, [show.bed(i * FRAME_S) for i in range(60)], t0=3.0)
    assert det.arm_p95_db > p95_quiet + 30.0 and det.loud_threshold_db > cfg.loud_line_db


def test_candidate_to_dict_carries_the_tier_b_fields(cfg, band_hz):
    sp = Spectrum(96, bed_db=-60.0, noise_db=1.0)
    det = FeedbackDetector(cfg, band_hz)
    frames = []
    for i in range(30):
        v = sp.bed(i * FRAME_S)
        if i >= 10:
            sp.tone(v, 66.0, -24.0)
        frames.append(v)
    run(det, frames)
    cand = [c for c in det.candidates if abs(c.centroid - 66.0) < 0.6]
    assert cand and cand[0].klass == "MODERATE"
    d = cand[0].to_dict()
    for k in ("klass", "class", "level_db", "prominence_db", "excess_db", "age_s", "reasons", "freq_hz", "cut_verdict", "cut_deepen",
              "cuts_held", "steps_seen", "fast_rise_db", "born_at_arm", "stationary", "probe_hits"):
        assert k in d, k
    assert d["cut_verdict"] is None and d["excess_db"] >= 20.0 and d["age_s"] >= 0.9


# -- G8: programme presence ---------------------------------------------------------------------------------

def test_g8_programme_present_flag(cfg, band_hz):
    """A quiet room (bed only), a room with a standing whine, and an established howl: no programme. A melody of notes
    with partials (one every 0.4 s): PROGRAMME_PRESENT within 2 s."""
    sp = Spectrum(80, bed_db=-66.0, noise_db=1.0)
    det = FeedbackDetector(cfg, band_hz)
    for i in range(60):
        v = sp.bed(i * FRAME_S)
        sp.tone(v, 59.0, -40.0)                       # whine from before arm
        sp.tone(v, 84.0, -12.0)                       # established howl
        det.feed(v, i * FRAME_S)
    assert not det.programme_present() and "PROGRAMME_PRESENT" not in det.flags
    sp = Spectrum(81, bed_db=-60.0, noise_db=1.0)
    det = FeedbackDetector(cfg, band_hz)
    rng = random.Random(3)
    for i in range(60):
        t = i * FRAME_S
        v = sp.bed(t)
        k = int(t / 0.4)
        b = 45.0 + rng.Random(k).randint(0, 12) if False else 45.0 + (k * 7) % 13
        for off, rel in ((0.0, 0.0), (10.0, -4.0), (15.85, -8.0), (20.0, -12.0)):
            sp.tone(v, b + off, -30.0 + rel)
        det.feed(v, t)
    assert det.programme_present() and "PROGRAMME_PRESENT" in det.flags
