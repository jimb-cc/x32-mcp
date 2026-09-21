"""The validation corpus: ``SCENARIOS[name] -> Scenario`` and ``frames(scenario, seed)``.

Each scenario builds a :class:`rtasim.render.Scene` from a seed (music randomisation, noise) and carries
its ground truth: the feedback events are *derived from the rendered trace* (see
:func:`rtasim.render.ring_episodes`) and the invariant for everything else is "must not be detected".
Scenario numbers Sxx follow the analyser brief §7 table; Cx are controls; Mx are the music+ring mixtures.

Levels are RTA dB as displayed (PEAK, settled). Frequencies are deliberately NOT on band centres unless the
brief says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Sequence

from .physics import FRAME_S, RTA_BAND_HZ, AnalyserSettings, DEFAULT_ANALYSER, TAU_LOOP_WEDGE_S, TAU_LOOP_TOPS_S
from .render import Episode, Renderer, Scene, ring_episodes
from .sources import (
    BassLine, ChordPad, CommonModeGain, DrivenResonance, DrumHit, DrumPattern, FeedbackRing, Group, HarmonicNote,
    Melody, NoiseBurst, PinkBed, SpeechBursts, TonalBurst, band_centre_hz, note_hz,
)


@dataclass
class Scenario:
    name: str
    title: str                      # what it tests
    expect: str                     # ground truth in words
    duration_s: float
    build_fn: Callable[[int, AnalyserSettings], Scene]
    analyser: dict[str, Any] = field(default_factory=dict)   # per-scenario analyser overrides
    latency_budget_ms: float = 300.0
    tags: tuple[str, ...] = ()
    mode: str = "feedback_watch"    # or "ring_out" (informational: who owns the gain)
    lf_optin: bool = False          # scenario needs the LF window relaxed (loop brief §3.4)

    def settings(self, overrides: dict[str, Any] | None = None) -> AnalyserSettings:
        kw = dict(self.analyser)
        if overrides:
            kw.update(overrides)
        return DEFAULT_ANALYSER.with_overrides(**kw) if kw else DEFAULT_ANALYSER

    def build(self, seed: int, analyser_overrides: dict[str, Any] | None = None) -> Scene:
        st = self.settings(analyser_overrides)
        sc = self.build_fn(seed, st)
        sc.analyser = st
        sc.duration_s = self.duration_s
        sc.salt = sum((i + 1) * b for i, b in enumerate(self.name.encode())) % 9973
        return sc

    @property
    def has_feedback(self) -> bool:
        return "feedback" in self.tags


SCENARIOS: dict[str, Scenario] = {}


def scenario(name: str, title: str, expect: str, duration_s: float, **kw):
    def deco(fn: Callable[[int, AnalyserSettings], Scene]) -> Callable:
        SCENARIOS[name] = Scenario(name, title, expect, duration_s, fn, **kw)
        return fn
    return deco


# -- common beds ------------------------------------------------------------------------------------
def room_noise(level_1k_db: float = -80.0) -> PinkBed:
    """Silent-room rumble/HVAC rising toward LF: -56 dB @ 25 Hz, -61 @ 50 Hz, -80 @ 1 kHz (the M7 studio's
    -51.7 dB @ 48 Hz was measured with background music playing, HANDOVER §4b(3))."""
    return PinkBed(level_1k_db=level_1k_db, tilt_db_per_oct=-4.5, f_lo=25.0, f_hi=12000.0, label="room")


def music_bed(level_1k_db: float = -50.0, tilt: float = -2.0, lfo_db: float = 2.0, seed: int | None = None, **kw) -> PinkBed:
    """Dense programme residue (everything that is not a resolvable line) as a pink-ish bed. With a ``seed`` the bed
    gets 4 random ±4 dB humps (sigma 2-6 bands), a ±0.5 dB/oct tilt change, non-periodic dynamics (lfo_db) and
    independent ±1.5 dB slow modulation of ~octave-wide regions (spectral flux): the neighbour median a detector
    uses is neither flat nor static, and differs per song."""
    extra = {}
    if seed is not None:
        extra = dict(seed=int(seed) * 13 + 7, random_humps=4, hump_db=4.0, region_mod_db=1.5)
    extra.update(kw)
    return PinkBed(level_1k_db=level_1k_db, tilt_db_per_oct=tilt, f_lo=45.0, f_hi=14000.0, lfo_db=lfo_db,
                   lfo_hz=0.37, label="mix_bed", **extra)


# ==================================================================================================
# controls (no feedback at all)
# ==================================================================================================
@scenario("C0_silent_room", "control: room noise only (rumble -56 dB @ 25 Hz, -61 @ 50 Hz, -80 @ 1 kHz)",
          "0 detections", 10.0, tags=("control",))
def _c0(seed, st):
    return Scene(10.0, sources=[room_noise()])


@scenario("C1_music_bed_drums", "control: programme bed + hats/snare/crash, no tonal instrument, no ring",
          "0 detections (broadband transients, synchronised multi-band onsets)", 12.0, tags=("control", "music"))
def _c1(seed, st):
    hits = []
    t = 0.5
    beat = 60.0 / 118.0
    i = 0
    while t < 11.5:
        if i % 4 in (1, 3):
            hits.append(DrumHit("snare", t, -30.0))
        hits.append(DrumHit("hat", t, -32.0))
        hits.append(DrumHit("hat", t + beat / 2, -36.0))
        if i % 16 == 8:
            hits.append(DrumHit("crash", t, -34.0))
        t += beat
        i += 1
    return Scene(12.0, sources=[room_noise(), music_bed(-46.0, lfo_db=3.0, seed=seed), Group(hits, "drums", "TRANSIENT")])


# ==================================================================================================
# the five brief-mandated streams (REVIEW_BRIEF §1) and the analyser-brief §7 table
# ==================================================================================================
@scenario("S1_bass_under_quiet_music", "M7 replica: bass line E1 A1 D2 G1 (41-73 Hz) + harmonics (H2>H1) under a quiet bed; "
          "instant acoustic onsets rendered as tau_a-limited 17-30 dB/s rises on bands 10-21",
          "all NOTE; 0 detections (current detector: FP at 40-80 Hz expected)", 16.0, tags=("music", "lf", "mandated"))
def _s1(seed, st):
    bass = BassLine(notes=["E1", "A1", "D2", "G1"], t_start=0.6, t_end=15.5, note_s=0.45, gap_s=0.15,
                    level_db=-38.0, timbre="bass_gtr", seed=seed, decay_db_per_s=5.0)
    return Scene(16.0, sources=[room_noise(), music_bed(-58.0, tilt=-2.2, lfo_db=1.5, seed=seed), bass])


@scenario("S2a_established_ring_8k", "ring ALREADY at its plateau when the detector arms: 8.12 kHz at -8 dBFS, "
          "flat +-0.3 dB, default N=3 skirts (prominence ceiling ~36 dB), no onset ever observed",
          "FEEDBACK from t=0; detect <= 300 ms", 8.0, tags=("feedback", "mandated", "established"))
def _s2a(seed, st):
    ring = FeedbackRing(freq_hz=8122.5, excess_db=1.0, tau_loop_s=TAU_LOOP_TOPS_S, sat_db=-8.0, established=True,
                        wander_db=0.3, wander_hz=0.6, label="ring_8k")
    return Scene(8.0, sources=[room_noise(), music_bed(-60.0, tilt=-1.5, lfo_db=1.0, seed=seed)], rings=[ring])


@scenario("S2b_established_ring_8k_steep", "as S2a with steep (N=5) skirts: prominence ~55 dB = the M7 datum "
          "(60 dB-prominent 8 kHz line that sat at confidence 0.50 for 15 s)",
          "FEEDBACK from t=0; detect <= 300 ms", 8.0, tags=("feedback", "established"), analyser={"skirt_order": 5.0})
def _s2b(seed, st):
    return _s2a(seed, st)


@scenario("S2c_established_clipped_2k4", "established howl pinned at 0.0 dBFS (RTA clip flag) at 2.4 kHz with hard-clip "
          "partials H3 -12, H5 -18, H2 -30 (a naive harmonic-family test calls it music)",
          "FEEDBACK from t=0; detect <= 300 ms despite odd partners", 8.0, tags=("feedback", "established", "clip"))
def _s2c(seed, st):
    ring = FeedbackRing(freq_hz=2405.0, excess_db=2.0, sat_db=0.0, established=True, wander_db=0.0, label="howl_clip")
    return Scene(8.0, sources=[room_noise(), music_bed(-60.0, seed=seed)], rings=[ring])


@scenario("S3_ring_during_music", "ring emerging DURING music: organ melody + bed -45 + cymbal every 2 s; loop at 3.15 kHz "
          "(+25 c) crosses threshold at t=4 with 0.3 dB excess, tau 10 ms -> 30 dB/s from -70 to limiter -6",
          "FEEDBACK onset 4.0 s (prominence >= 12 dB ~1 s later); detect <= 300 ms after; 0 detections on melody/cymbals",
          12.0, tags=("feedback", "music", "mandated", "mixture"))
def _s3(seed, st):
    mel = Melody(t_start=0.3, t_end=11.5, low="C5", high="C6", note_s=(0.3, 1.1), level_db=-30.0, timbre="organ_flue",
                 seed=seed, vib_rate_hz=0.8, vib_cents=8.0, legato=True, am_db=1.5, am_hz=0.8)
    cym = Group([DrumHit("crash", 1.0 + 2.0 * i, -33.0) for i in range(6)], "cymbals", "TRANSIENT")
    ring = FeedbackRing(freq_hz=band_centre_hz(73, 25.0), excess_db=0.3, tau_loop_s=0.010, t_on=4.0, start_db=-70.0,
                        sat_db=-6.0, label="ring_3k15")
    return Scene(12.0, sources=[room_noise(), music_bed(-45.0, seed=seed), mel, cym], rings=[ring])


@scenario("S4a_vocal_vibrato", "sustained vocal A4 (+5 c) 4 s: H1 -30, H2 -28, H3 -33, H4 -38 ..., vibrato 5.5 Hz ramping to "
          "+-80 c, shimmer +-1 dB, onset scoop -150 c / 120 ms", "NOTE; 0 detections", 7.0, tags=("music", "mandated"))
def _s4a(seed, st, cents=5.0):
    v = HarmonicNote(f0_hz=note_hz("A4", cents), t_on=1.0, dur=4.0, level_db=-30.0, timbre="voice", attack_s=0.06,
                     vib_rate_hz=5.5, vib_cents=80.0, vib_delay_s=0.2, vib_ramp_s=0.4, glide_cents=-150.0, glide_s=0.12,
                     am_db=1.0, am_hz=6.8, release_db_per_s=120.0, label="vocal_A4")
    return Scene(7.0, sources=[room_noise(), music_bed(-58.0, lfo_db=1.0, seed=seed), v])


@scenario("S4b_vocal_vibrato_band_edge", "as S4a but centred on the band 45/46 edge (457 Hz): peak band hops ~50 % of frames",
          "NOTE; 0 detections", 7.0, tags=("music",))
def _s4b(seed, st):
    v = HarmonicNote(f0_hz=band_centre_hz(45.5), t_on=1.0, dur=4.0, level_db=-30.0, timbre="voice", attack_s=0.06,
                     vib_rate_hz=5.5, vib_cents=80.0, vib_delay_s=0.2, vib_ramp_s=0.4, glide_cents=-150.0, glide_s=0.12,
                     am_db=1.0, am_hz=6.8, release_db_per_s=120.0, label="vocal_edge")
    return Scene(7.0, sources=[room_noise(), music_bed(-58.0, lfo_db=1.0, seed=seed), v])


@scenario("S5_guitar_note_decays_to_sine", "held E3 (164.8 Hz, -12 c) 6 s: full series at onset (H1 -32 .. H6 -45), Hk decaying at "
          "(3+2k) dB/s so after ~3 s only H1/H2 remain (near-sine); finger vibrato +-25 c from 1 s",
          "NOTE; 0 detections including the late near-sine phase", 9.0, tags=("music", "mandated"))
def _s5(seed, st):
    g = HarmonicNote(f0_hz=note_hz(164.8, -12.0), t_on=1.0, dur=6.0, level_db=-32.0, timbre="el_guitar",
                     decay_db_per_s=5.0, partial_decay_db_per_s=2.0, vib_rate_hz=5.0, vib_cents=25.0, vib_delay_s=1.0,
                     vib_ramp_s=0.5, release_db_per_s=60.0, label="guitar_E3")
    return Scene(9.0, sources=[room_noise(), music_bed(-60.0, lfo_db=1.0, seed=seed), g])


@scenario("S6_master_ramp_feedback_watch", "operator raises the master: organ chord C3-G3-E4-C5 (+H2s): 8 lines 15-25 dB prominent over bed -58, sounding "
          "since before arm; +9 dB over 0.9 s at t=3 (10 dB/s common mode; LF lags per tau_a), -5 dB step at t=7; no loop",
          "COMMON_MODE; 0 detections",
          11.0, tags=("music", "mandated", "common_mode"))
def _s6(seed, st):
    # open voicing C3 G3 E4 C5 on an 8'+4' registration: 8 stable lines 131 Hz..1.05 kHz, 15-25 dB prominent
    # (sounding since before arm: no onsets are observed, only the fader move)
    chord = ChordPad(chords=[["C3", "G3", "E4", "C5"]], t_start=-2.0, t_end=10.7, chord_s=12.7, level_db=-32.0,
                     timbre=(0.0, -4.0, -30.0), chorus_db=0.4, chorus_hz=0.9, seed=seed)
    master = CommonModeGain(points=((0.0, 0.0), (3.0, 0.0), (3.9, 9.0), (7.0, 9.0), (7.0, 4.0)), label="operator")
    return Scene(11.0, sources=[room_noise(), music_bed(-58.0, seed=seed), chord], master=master)


@scenario("S6b_ringout_steps_latent_loop", "ring_out: server steps the master +1 dB every 1.5 s from t=2 over an organ chord (8 lines) + bed -55; latent loop at "
          "2.5 kHz with excess -7.5 dB at t=0 -> programme +1.00 dB/step, ring band +1.6..+6.5 dB/step, runaway (+0.5 dB, "
          "50 dB/s) after the 8th step at t=12.5", "FEEDBACK onset 12.5 s (detect <= 300 ms after prominence); early "
          "detection via probe over-response is DESIRABLE; organ lines must not be detected", 16.0,
          tags=("feedback", "music", "ring_out", "probe"), mode="ring_out")
def _s6b(seed, st):
    chord = ChordPad(chords=[["C3", "G3", "E4", "C5"]], t_start=-2.0, t_end=15.7, chord_s=17.7, level_db=-32.0,
                     timbre=(0.0, -4.0, -30.0), chorus_db=0.4, seed=seed)
    master = CommonModeGain.ring_out_steps(t_first=2.0, dwell_s=1.5, step_db=1.0, n_steps=8)
    # prog_coupling 1.0: everything at the tap arrives through the open mic (PA spill + room), so the loop sees all of
    # it (excite_coupling 0 dB). Band 70 spans ~1.7 comb periods (1/tau = 100 Hz): the noise-driven over-response is
    # the band MEAN of the comb, +3 dB @ e=-3, +6.9 @ -1, +9.6 @ -0.5 — weaker and later than the on-mode line table.
    ring = FeedbackRing(freq_hz=2500.0, excess_db=-7.5, tau_loop_s=0.010, t_on=0.0, start_db=-80.0, sat_db=-6.0,
                        excite_coupling_db=0.0, label="latent_2k5")
    return Scene(16.0, sources=[room_noise(), music_bed(-55.0, seed=seed), chord], rings=[ring], master=master)


@scenario("S7_808_sub_bassline", "808/sine sub-bass: 0.5-7 s F1 G1 Bb1 C2 (43.7-65.4 Hz) 0.9 s notes with +300 c glide over 150 ms, "
          "12 dB/s decay from -25, H2 -35 (no family), kick layered; 7.5-12 s SUSTAINED sine-bass notes (C2, G1, 2 s each, "
          "dead steady, -28): the hardest LF case", "NOTE; 0 detections (current detector: override / growth fire)", 12.0,
          tags=("music", "lf"))
def _s7(seed, st):
    sub = BassLine(notes=["F1", "G1", "Bb1", "C2"], t_start=0.5, t_end=7.0, note_s=0.9, gap_s=0.1, level_db=-25.0,
                   timbre="808", seed=seed, decay_db_per_s=12.0, glide_cents=300.0, glide_s=0.15)
    kick = DrumPattern(t_start=0.5, t_end=7.0, bpm=120.0, pattern="kick_only", level_db=-32.0, seed=seed)
    pad = BassLine(notes=["C2", "G1"], t_start=7.5, t_end=11.8, note_s=2.0, gap_s=0.15, level_db=-28.0,
                   timbre="synth_sine_bass", seed=seed, decay_db_per_s=0.0)
    return Scene(12.0, sources=[room_noise(), music_bed(-66.0, tilt=-1.6, lfo_db=1.0, seed=seed), sub, kick, pad])


@scenario("S8a_organ_melody", "Hammond 8'-only melody C5-C6 (523-1047 Hz), 0.3-1.2 s legato, H1 -28, H2 -52, H3 -58, Leslie AM "
          "+-1.5 dB @ 0.8 Hz: near-sines that END and MOVE", "NOTE; 0 detections", 12.0, tags=("music", "near_sine"))
def _s8a(seed, st):
    mel = Melody(t_start=0.4, t_end=11.6, low="C5", high="C6", note_s=(0.3, 1.2), level_db=-28.0, timbre="organ_flue",
                 seed=seed, vib_rate_hz=0.8, vib_cents=10.0, legato=True, am_db=1.5, am_hz=0.8)
    return Scene(12.0, sources=[room_noise(), music_bed(-50.0, seed=seed), mel])


@scenario("S8b_flute_held_note", "flute: melody 600 Hz-1.6 kHz (H2 -15) with one 3 s held note, vibrato +-20 c 5 Hz, breath bed",
          "NOTE; 0 detections", 12.0, tags=("music", "near_sine"))
def _s8b(seed, st):
    mel = Melody(t_start=0.4, t_end=7.0, low="D5", high="G6", note_s=(0.3, 0.9), level_db=-30.0, timbre="flute",
                 seed=seed, vib_rate_hz=5.0, vib_cents=20.0, attack_s=0.04)
    held = HarmonicNote(f0_hz=note_hz("A5", 12.0), t_on=7.3, dur=3.0, level_db=-29.0, timbre="flute", attack_s=0.05,
                        vib_rate_hz=5.0, vib_cents=20.0, vib_delay_s=0.3, am_db=0.8, am_hz=5.0, label="flute_held")
    breath = PinkBed(level_1k_db=-62.0, tilt_db_per_oct=0.0, f_lo=800.0, f_hi=9000.0, label="breath")
    return Scene(12.0, sources=[room_noise(), music_bed(-54.0, seed=seed), breath, mel, held])


@scenario("S8c_whistle", "human whistling 1.2-2.2 kHz, 0.5-1.5 s notes, +-60 c 6 Hz vibrato, ~35 dB prominent, no family, in the "
          "vocal-mic feedback band", "NOTE; 0 detections", 10.0, tags=("music", "near_sine", "trap"))
def _s8c(seed, st):
    mel = Melody(t_start=0.5, t_end=9.5, low="D6", high="C#7", note_s=(0.5, 1.5), gap_s=(0.1, 0.4), level_db=-22.0,
                 timbre="whistle", seed=seed, vib_rate_hz=6.0, vib_cents=60.0, attack_s=0.05, glide_cents=-80.0, glide_s=0.08)
    return Scene(10.0, sources=[room_noise(), music_bed(-52.0, seed=seed), mel])


@scenario("S9_clipped_howl_fast", "fast howl: 2.0 kHz (-20 c) crosses threshold at t=2 with 0.8 dB excess, tau 7 ms -> 115 dB/s "
          "(above the 60 dB/s onset guard) from -75 to 0.0 dBFS clip in 0.65 s, then odd partials appear",
          "FEEDBACK onset 2.0; detect <= 300 ms after prominence crossing (~2.15 s)", 6.0, tags=("feedback", "clip", "fast"))
def _s9(seed, st):
    ring = FeedbackRing(freq_hz=band_centre_hz(67, -20.0), excess_db=0.8, tau_loop_s=0.007, t_on=2.0, start_db=-75.0,
                        sat_db=0.0, label="howl_2k")
    return Scene(6.0, sources=[room_noise(), music_bed(-55.0, lfo_db=1.0, seed=seed)], rings=[ring])


@scenario("S10_ring_between_bands", "ring exactly on the band 70/71 edge (2588 Hz): two equal bands at -3, 20 dB/s from -65 at t=1 to "
          "-15 plateau, +-0.3 dB wander at 1.5 Hz makes the louder band alternate",
          "FEEDBACK; ONE detection stream (GEQ 2.5 k), latency <= 300 ms", 8.0, tags=("feedback", "edge"))
def _s10(seed, st):
    ring = FeedbackRing(freq_hz=band_centre_hz(70.5), excess_db=0.2, tau_loop_s=0.010, t_on=1.0, start_db=-65.0,
                        sat_db=-15.0, wander_db=0.3, wander_hz=1.5, label="ring_edge_2k59")
    return Scene(8.0, sources=[room_noise(), music_bed(-60.0, lfo_db=1.0, seed=seed)], rings=[ring])


@scenario("S11a_two_rings", "two simultaneous non-harmonic rings: 1.25 kHz 15 dB/s from t=1 and 3.55 kHz 25 dB/s from t=1.6",
          "both FEEDBACK; both detected <= 300 ms", 8.0, tags=("feedback",))
def _s11a(seed, st):
    a = FeedbackRing(freq_hz=1250.0, excess_db=0.15, tau_loop_s=0.010, t_on=1.0, start_db=-62.0, sat_db=-10.0, label="ring_1k25")
    b = FeedbackRing(freq_hz=band_centre_hz(75, 7.0), excess_db=0.25, tau_loop_s=0.010, t_on=1.6, start_db=-65.0,
                     sat_db=-8.0, label="ring_3k55")
    return Scene(8.0, sources=[room_noise(), music_bed(-58.0, lfo_db=1.0, seed=seed)], rings=[a, b])


@scenario("S11b_two_rings_near_octave", "two rings at 1.25 kHz and 2.52 kHz (x2.02, inside an H2 tolerance): different onsets and rates, "
          "no H3/H4", "both FEEDBACK; must not be dismissed as note+H2", 8.0, tags=("feedback", "trap"))
def _s11b(seed, st):
    a = FeedbackRing(freq_hz=1250.0, excess_db=0.15, tau_loop_s=0.010, t_on=1.0, start_db=-62.0, sat_db=-10.0, label="ring_1k25")
    b = FeedbackRing(freq_hz=2520.0, excess_db=0.25, tau_loop_s=0.010, t_on=1.6, start_db=-65.0, sat_db=-8.0, label="ring_2k52")
    return Scene(8.0, sources=[room_noise(), music_bed(-58.0, lfo_db=1.0, seed=seed)], rings=[a, b])


@scenario("S12_acoustic_guitar_wedge_ring_196Hz", "acoustic guitar strums (G-C-D every 2 s, body hump +8 dB at bands 21-26) through wedges; loop at "
          "196 Hz (+32 c, = the G3 chord tone!) rings sub-threshold (excess -4) then +0.3 dB at t=5 -> 25 dB/s (tau 12 ms) "
          "to limiter -10", "chord tones before t=5 are NOTE (they end); FEEDBACK onset 5.0, detect <= 600 ms; needs the LF "
          "prior relaxed (instrument + wedges)", 14.0, tags=("feedback", "music", "lf", "mixture"), latency_budget_ms=600.0,
          lf_optin=True)
def _s12(seed, st):
    strum = ChordPad(chords=[["G2", "B2", "D3", "G3"], ["C3", "E3", "G3", "C4"], ["D3", "F#3", "A3", "D4"]], t_start=0.5,
                     t_end=13.5, chord_s=2.0, level_db=-34.0, timbre="ac_guitar", decay_db_per_s=4.0, release_db_per_s=80.0,
                     seed=seed, vib_rate_hz=0.0)
    body = PinkBed(level_1k_db=-55.0, tilt_db_per_oct=-1.0, f_lo=60.0, f_hi=8000.0, bumps=((23.5, 8.0, 1.5),), label="guitar_body")
    ring = FeedbackRing(freq_hz=note_hz(196.0, 32.0), excess_db=-4.0, excess_points=((0.0, -4.0), (4.0, -4.0), (5.0, 0.3)),
                        tau_loop_s=0.012, t_on=0.0, start_db=-80.0, sat_db=-10.0, label="wedge_196")
    return Scene(14.0, sources=[room_noise(), body, strum], rings=[ring])


@scenario("S13_slow_ring_3dB_s", "marginal loop: 5.04 kHz (+12 c), excess 0.03 dB -> 3 dB/s from -70 (t=1) to -40 (t=11), +-0.4 dB wander "
          "@ 1 Hz, under an HF bed -55 with hi-hat 8ths", "FEEDBACK; detect within 1 s of prominence >= 12 dB (growth path "
          "useless at 3 < 6 dB/s)", 16.0, tags=("feedback", "slow", "music"), latency_budget_ms=1000.0)
def _s13(seed, st):
    hats = DrumPattern(t_start=0.5, t_end=15.5, bpm=120.0, pattern="hats_only", level_db=-32.0, seed=seed)
    # a loop cannot be held 0.03 dB over threshold for 10 s: model it as 0.035 ± 0.02 dB wander (1.5-5.5 dB/s,
    # mean ~3.5) so the ramp is slow AND irregular but never actually stalls
    ring = FeedbackRing(freq_hz=band_centre_hz(80, 12.0), excess_db=0.035, excess_wander_db=0.02, tau_loop_s=0.010, t_on=1.0,
                        start_db=-70.0, sat_db=-38.0, wander_db=0.4, wander_hz=1.0, label="slow_5k")
    return Scene(16.0, sources=[room_noise(), music_bed(-52.0, tilt=-1.0, lfo_db=1.0, seed=seed), hats], rings=[ring])


@scenario("S14_ring_masked_by_cymbal", "crash at t=2.0 (+22 dB bands 58-97, 12 dB/s decay); ring 4.2 kHz crosses threshold at t=2.1, 40 dB/s "
          "from -70; becomes prominent only as the wash falls (apparent relative growth 52 dB/s)",
          "FEEDBACK onset 2.1; detect <= 300 ms after prominence crossing (~3 s); no detection on the crash", 8.0,
          tags=("feedback", "music", "masked"))
def _s14(seed, st):
    crash = DrumHit("crash", 2.0, -28.0)
    ring = FeedbackRing(freq_hz=4200.0, excess_db=0.4, tau_loop_s=0.010, t_on=2.1, start_db=-70.0, sat_db=-8.0, label="ring_4k2")
    return Scene(8.0, sources=[room_noise(), music_bed(-50.0, seed=seed), crash], rings=[ring])


@scenario("S15_long_rta_decay_tails", "RTA decay pref left LONG (16 -> 3.75 dB/s release): S1 bass + S7 808 notes leave flat, prominent, "
          "family-less tails for seconds; plus a real 3.15 kHz ring from t=3 (30 dB/s) hard-notched -9 dB at t=8 whose display "
          "falls at only 3.75 dB/s", "NOTE tails: 0 detections; FEEDBACK onset 3.0: detect; (VERIFY would wrongly fail)", 16.0,
          tags=("feedback", "music", "lf", "prefs"), analyser={"decay_s": 16.0})
def _s15(seed, st):
    bass = BassLine(notes=["E1", "A1", "D2", "G1"], t_start=0.6, t_end=7.5, note_s=0.45, gap_s=0.15, level_db=-38.0,
                    timbre="bass_gtr", seed=seed, decay_db_per_s=5.0)
    sub = BassLine(notes=["F1", "G1", "Bb1", "C2"], t_start=8.5, t_end=15.5, note_s=0.9, gap_s=0.1, level_db=-25.0,
                   timbre="808", seed=seed, decay_db_per_s=12.0, glide_cents=300.0, glide_s=0.15)
    ring = FeedbackRing(freq_hz=band_centre_hz(73, 25.0), excess_db=0.3, tau_loop_s=0.010, t_on=3.0, start_db=-70.0,
                        sat_db=-6.0, label="ring_3k15")
    return Scene(16.0, sources=[room_noise(), music_bed(-58.0, tilt=-2.2, lfo_db=1.5, seed=seed), bass, sub], rings=[ring],
                 geq_schedule=[(8.0, 23, -9.0)])   # operator/other system kills it hard at t=8 (GEQ 3.15 kHz)


@scenario("S16_peak_hold_on", "RTA peak-hold left ON (2 s): S4 vocal + S8 organ material -> dead-flat plateaus after every note peak",
          "0 detections is only achievable if arm forces peakhold OFF; a detector should flag ANALYSER_MISCONFIGURED", 10.0,
          tags=("music", "prefs", "trap"), analyser={"peak_hold_s": 2.0})
def _s16(seed, st):
    v = HarmonicNote(f0_hz=note_hz("A4", 5.0), t_on=1.0, dur=3.0, level_db=-30.0, timbre="voice", attack_s=0.06,
                     vib_rate_hz=5.5, vib_cents=60.0, am_db=1.0, am_hz=6.8, label="vocal_A4")
    mel = Melody(t_start=4.5, t_end=9.5, low="C5", high="C6", note_s=(0.3, 1.0), level_db=-28.0, timbre="organ_flue",
                 seed=seed, vib_rate_hz=0.8, vib_cents=10.0, legato=True, am_db=1.5, am_hz=0.8)
    return Scene(10.0, sources=[room_noise(), music_bed(-52.0, seed=seed), v, mel])


@scenario("S17_kick_pattern", "four-on-the-floor kick 124 bpm: thump 62->50 Hz over 120 ms, -30 peak, 25 dB/s, beater click; RTA decay "
          "1.0 keeps bands 14-17 pumped at 2 Hz with no family", "TRANSIENT; 0 detections", 10.0, tags=("music", "lf"))
def _s17(seed, st):
    kick = DrumPattern(t_start=0.4, t_end=9.6, bpm=124.0, pattern="kick_only", level_db=-30.0, seed=seed)
    return Scene(10.0, sources=[room_noise(), music_bed(-64.0, tilt=-1.4, lfo_db=1.0, seed=seed), kick])


@scenario("S18_vibrato_on_band_edge", "G5-ish note ON the band 53/54 edge (797 Hz), +-70 c @ 6 Hz for 3 s, H1 -30, H2 -36, H3 -40: peak band "
          "alternates 53<->54 on ~60 % of frames, single-band swing 8 dB, cluster power +-0.2 dB",
          "NOTE; 0 detections; tracker should keep ONE candidate", 6.0, tags=("music", "edge"))
def _s18(seed, st):
    v = HarmonicNote(f0_hz=band_centre_hz(53.5), t_on=1.0, dur=3.0, level_db=-30.0, timbre=(0.0, -6.0, -10.0, -16.0),
                     attack_s=0.05, vib_rate_hz=6.0, vib_cents=70.0, vib_delay_s=0.15, vib_ramp_s=0.3, am_db=0.5, am_hz=6.0,
                     label="note_edge_797")
    return Scene(6.0, sources=[room_noise(), music_bed(-58.0, seed=seed), v])


@scenario("S19_driven_room_mode", "43 Hz axial room mode (Q 20, +10 dB, T60 1.5 s) DRIVEN by the S1 bass line, recurring at a fixed centroid "
          "across all notes, while ring_out steps the master +1 dB/1.5 s (everything +1.00 dB/step)",
          "DRIVEN_RESONANCE + NOTE; 0 detections / 0 cuts", 14.0, tags=("music", "lf", "ring_out", "trap"), mode="ring_out")
def _s19(seed, st):
    bass = BassLine(notes=["E1", "A1", "F1", "G1"], t_start=0.6, t_end=13.5, note_s=0.5, gap_s=0.2, level_db=-38.0,
                    timbre="bass_gtr", seed=seed, decay_db_per_s=5.0)
    mode = DrivenResonance(band_centre_hz(11, 35.0), [bass], gain_db=10.0, q=20.0, t60_s=1.5, bw_bands=1.5)
    master = CommonModeGain.ring_out_steps(t_first=3.0, dwell_s=1.5, step_db=1.0, n_steps=6)
    return Scene(14.0, sources=[room_noise(), music_bed(-58.0, tilt=-2.2, lfo_db=1.0, seed=seed), bass, mode], master=master)


@scenario("S20_song_start_stop_crowd", "t=1 full mix starts (+35 dB broadband; HF in a frame, LF per tau_a), t=9 stops (release-limited fall), "
          "t=10-14 applause bed +15 dB 0.5-4 kHz with two crowd whistles (1.8/2.3 kHz, 0.8 s, ~30 dB prominent, +-50 c wobble)",
          "COMMON_MODE + NOTE; 0 detections", 16.0, tags=("music", "common_mode", "trap"))
def _s20(seed, st):
    bed = music_bed(-44.0, lfo_db=2.0, t_on=1.0, t_off=9.0, release_db_per_s=60.0, seed=seed)
    chords = ChordPad(chords=[["A3", "C#4", "E4"], ["F#3", "A3", "C#4"], ["D3", "F#3", "A3"], ["E3", "G#3", "B3"]],
                      t_start=1.0, t_end=9.0, chord_s=2.0, level_db=-34.0, timbre="piano", decay_db_per_s=3.0, seed=seed)
    bass = BassLine(notes=["A1", "F#1", "D2", "E2"], t_start=1.0, t_end=9.0, note_s=1.8, gap_s=0.2, level_db=-36.0, seed=seed)
    drums = DrumPattern(t_start=1.0, t_end=9.0, bpm=120.0, pattern="rock", level_db=-30.0, seed=seed)
    crowd = PinkBed(level_1k_db=-46.0, tilt_db_per_oct=0.0, f_lo=500.0, f_hi=4000.0, t_on=10.0, attack_s=0.6, t_off=14.0,
                    release_db_per_s=15.0, lfo_db=2.0, lfo_hz=1.3, label="applause")
    w1 = HarmonicNote(f0_hz=1800.0, t_on=11.0, dur=0.8, level_db=-17.0, timbre="whistle", attack_s=0.05, vib_rate_hz=6.0,
                      vib_cents=50.0, vib_delay_s=0.0, vib_ramp_s=0.1, glide_cents=-100.0, glide_s=0.1, label="crowd_whistle")
    w2 = HarmonicNote(f0_hz=2300.0, t_on=12.3, dur=0.8, level_db=-19.0, timbre="whistle", attack_s=0.05, vib_rate_hz=5.0,
                      vib_cents=50.0, vib_delay_s=0.0, vib_ramp_s=0.1, glide_cents=150.0, glide_s=0.15, label="crowd_whistle")
    return Scene(16.0, sources=[room_noise(), bed, chords, bass, drums, crowd, w1, w2])


@scenario("S21_synth_pad_swell", "saw pad A2-E3-A3 (+harmonics to H8, -6 dB/oct) with a 1.5 s linear-in-dB attack = 20 dB/s on ~15 lines at "
          "once, sustain with chorus beating +-3 dB @ ~1.2 Hz, release; second chord F2-C3-F3",
          "NOTE; 0 detections (synchronised multi-line growth + family + beating)", 13.0, tags=("music", "swell"))
def _s21(seed, st):
    p1 = ChordPad(chords=[["A2", "E3", "A3"]], t_start=1.0, t_end=6.5, chord_s=5.5, level_db=-32.0, timbre="saw",
                  attack_s=1.5, release_db_per_s=30.0, chorus_db=3.0, chorus_hz=1.2, seed=seed)
    p2 = ChordPad(chords=[["F2", "C3", "F3"]], t_start=7.5, t_end=12.0, chord_s=4.5, level_db=-32.0, timbre="saw",
                  attack_s=1.5, release_db_per_s=30.0, chorus_db=3.0, chorus_hz=1.2, seed=seed + 1)
    for n in p1.children + p2.children:
        n.attack_range_db = 30.0
    return Scene(13.0, sources=[room_noise(), music_bed(-55.0, seed=seed), p1, p2])


@scenario("S22_speech_ringing_then_feedback", "speech (F0 110-140 Hz syllables + formant noise 1-3 kHz) through a loop at 2.8 kHz sitting at -2.5 dB: "
          "every syllable rings on at +12 dB and tails off (280 dB/s, display release-limited); operator +3 dB at t=6 -> "
          "+0.5 dB excess -> 55 dB/s (tau 9 ms)", "before t=6 FEEDBACK-RISK (early warning desirable, not FP); FEEDBACK onset "
          "6.0, detect <= 300 ms after prominence", 10.0, tags=("feedback", "music", "probe", "mixture"))
def _s22(seed, st):
    sp = SpeechBursts(t_start=0.4, t_end=9.6, level_db=-32.0, seed=seed)
    form = Group([NoiseBurst(band_lo=58, band_hi=76, t_on=n.t_on, level_db=-50.0, attack_s=0.03, hold_s=n.dur * 0.7,
                             decay_db_per_s=150.0, dur=n.dur + 0.3, label="formants") for n in sp.children], "formants", "NOTE")
    master = CommonModeGain(points=((0.0, 0.0), (6.0, 0.0), (6.0, 3.0)), label="operator+3")
    ring = FeedbackRing(freq_hz=2800.0, excess_db=-2.5, tau_loop_s=0.009, t_on=0.0, start_db=-80.0, sat_db=-8.0,
                        excite_coupling_db=-4.0, excite_bw_oct=0.15, label="lectern_2k8")
    return Scene(10.0, sources=[room_noise(), music_bed(-60.0, lfo_db=0.5, seed=seed), sp, form], rings=[ring], master=master)


@scenario("S23a_autogain_drift", "RTA autogain accidentally ON: S1 material with a common-mode drift of -0.5 dB/s for 8 s then a +4 dB "
          "re-normalisation jump at t=9 (no fader write)", "0 detections; detector should flag ANALYSER_MISCONFIGURED", 12.0,
          tags=("music", "prefs", "common_mode"))
def _s23a(seed, st):
    bass = BassLine(notes=["E1", "A1", "D2", "G1"], t_start=0.6, t_end=11.5, note_s=0.45, gap_s=0.15, level_db=-38.0,
                    timbre="bass_gtr", seed=seed, decay_db_per_s=5.0)
    master = CommonModeGain(points=((0.0, 0.0), (1.0, 0.0), (9.0, -4.0), (9.0, 0.0)), label="autogain")
    return Scene(12.0, sources=[room_noise(), music_bed(-58.0, tilt=-2.2, lfo_db=1.5, seed=seed), bass], master=master)


@scenario("S23b_gain_offset_clip", "RTA manual gain left at +24 dB: S7 808 material shifted up so sub-bass peaks hit the 0.0 clip flag",
          "0 detections (clip flag on programme = misconfigured analyser, not a howl)", 10.0, tags=("music", "prefs", "clip", "lf"),
          analyser={"gain_offset_db": 24.0})
def _s23b(seed, st):
    sub = BassLine(notes=["F1", "G1", "Bb1", "C2"], t_start=0.5, t_end=9.5, note_s=0.9, gap_s=0.1, level_db=-25.0,
                   timbre="808", seed=seed, decay_db_per_s=12.0, glide_cents=300.0, glide_s=0.15)
    return Scene(10.0, sources=[room_noise(), music_bed(-62.0, tilt=-1.6, lfo_db=1.0, seed=seed), sub])


@scenario("S24_bells_triangle_glock", "triangle hit (2.1 kHz, x2.19, x3.48; 25-30 dB prominent, 6 dB/s, no vibrato) then glockenspiel notes "
          "1.05-2.1 kHz (partials x2.76, x5.4) 1 s each", "NOTE/TRANSIENT; 0 detections", 10.0, tags=("music", "trap"))
def _s24(seed, st):
    tri = TonalBurst(f_hz=2100.0, t_on=1.0, level_db=-24.0, decay_db_per_s=6.0, dur=4.0,
                     partials=((1.0, 0.0), (2.19, -2.0), (3.48, -5.0)), label="triangle")
    gl = Group([TonalBurst(f_hz=note_hz(n), t_on=5.5 + i * 1.0, level_db=-26.0, decay_db_per_s=15.0, dur=1.2,
                           partials=((1.0, 0.0), (2.76, -8.0), (5.4, -14.0)), label="glock")
                for i, n in enumerate(["C6", "E6", "G6", "C7"])], "glock", "NOTE")
    return Scene(10.0, sources=[room_noise(), music_bed(-55.0, seed=seed), tri, gl])


# ==================================================================================================
# music + ring mixtures at realistic levels
# ==================================================================================================
@scenario("M1_loud_band_wedge_ring", "loud band on stage (bed -32, rock drums -22, bass -28, power chords -30, vocal -24 with vibrato) and a "
          "wedge ring at 2.83 kHz: 1 dB excess on a 5 ms loop -> 200 dB/s from -60 at t=5 to limiter -4",
          "FEEDBACK onset 5.0; detect <= 300 ms after prominence; 0 detections on the band", 12.0,
          tags=("feedback", "music", "mixture", "fast", "loud"))
def _m1(seed, st):
    drums = DrumPattern(t_start=0.3, t_end=11.7, bpm=132.0, pattern="rock", level_db=-22.0, seed=seed)
    bass = BassLine(notes=["A1", "C2", "D2", "E2"], t_start=0.3, t_end=11.7, note_s=0.4, gap_s=0.05, level_db=-28.0, seed=seed)
    gtr = ChordPad(chords=[["A2", "E3", "A3"], ["C3", "G3", "C4"], ["D3", "A3", "D4"], ["E3", "B3", "E4"]], t_start=0.3,
                   t_end=11.7, chord_s=1.82, level_db=-30.0, timbre="el_guitar", decay_db_per_s=2.0, seed=seed)
    vox = Melody(t_start=0.5, t_end=11.5, low="C4", high="G5", note_s=(0.25, 1.2), level_db=-24.0, timbre="voice", seed=seed,
                 vib_rate_hz=5.5, vib_cents=50.0, attack_s=0.05, glide_cents=-120.0, glide_s=0.1)
    ring = FeedbackRing(freq_hz=band_centre_hz(72, -25.0), excess_db=1.0, tau_loop_s=TAU_LOOP_WEDGE_S, t_on=5.0,
                        start_db=-60.0, sat_db=-4.0, label="wedge_2k83")
    return Scene(12.0, sources=[room_noise(), music_bed(-32.0, lfo_db=3.0, seed=seed), drums, bass, gtr, vox], rings=[ring])


@scenario("M2_quiet_music_ringout_two_modes", "M7 replica end-to-end: quiet background music (bed -50, organ melody -34, bass -40) while ring_out steps "
          "the master +1 dB/1.5 s from t=2; loop modes at 5.04 kHz (excess -2.6) and 8.12 kHz (excess -5.3) cross threshold on "
          "the 3rd and 6th steps (t=5.0, t=9.5). In closed loop the first cut re-arms mode A for a later step.",
          "FEEDBACK 5.04 k onset 5.0 and 8.12 k onset 9.5; detect each <= 300 ms; 0 cuts on music", 16.0,
          tags=("feedback", "music", "mixture", "ring_out", "closed_loop"), mode="ring_out")
def _m2(seed, st):
    mel = Melody(t_start=0.4, t_end=15.5, low="C5", high="A5", note_s=(0.4, 1.2), level_db=-34.0, timbre="organ_flue",
                 seed=seed, vib_rate_hz=0.8, vib_cents=8.0, legato=True, am_db=1.0, am_hz=0.8)
    bass = BassLine(notes=["C2", "G1", "A1", "F1"], t_start=0.4, t_end=15.5, note_s=0.7, gap_s=0.1, level_db=-40.0, seed=seed)
    master = CommonModeGain.ring_out_steps(t_first=2.0, dwell_s=1.5, step_db=1.0, n_steps=9)
    a = FeedbackRing(freq_hz=band_centre_hz(80, 12.0), excess_db=-2.6, tau_loop_s=0.010, t_on=0.0, start_db=-80.0,
                     sat_db=-6.0, excite_coupling_db=-3.0, label="mode_5k")
    b = FeedbackRing(freq_hz=band_centre_hz(87, -10.0), excess_db=-5.3, tau_loop_s=0.010, t_on=0.0, start_db=-80.0,
                     sat_db=-6.0, excite_coupling_db=-3.0, label="mode_8k")
    return Scene(16.0, sources=[room_noise(), music_bed(-50.0, seed=seed), mel, bass], rings=[a, b], master=master, prog_coupling=0.5)


@scenario("M3_jazz_trio_lav_ring_400Hz", "piano comping + walking upright-ish bass + ride, lav/lectern loop at 412 Hz (band 44 +15 c) with 0.5 dB "
          "excess on a 22 ms loop -> 23 dB/s from t=4, plateau -18 (compressor); piano chord tones share the band",
          "FEEDBACK onset 4.0; detect <= 300 ms after prominence; 0 detections on piano/bass", 14.0,
          tags=("feedback", "music", "mixture"))
def _m3(seed, st):
    piano = ChordPad(chords=[["D3", "F3", "A3", "C4"], ["G3", "B3", "D4", "F4"], ["C3", "E3", "G3", "B3"], ["A3", "C4", "E4", "G4"]],
                     t_start=0.4, t_end=13.6, chord_s=1.6, level_db=-36.0, timbre="piano", decay_db_per_s=6.0, seed=seed)
    bass = BassLine(notes=["D2", "F2", "A2", "G2", "B1", "C2", "E2", "A1"], t_start=0.4, t_end=13.6, note_s=0.36, gap_s=0.04,
                    level_db=-38.0, timbre="ac_guitar", seed=seed, decay_db_per_s=8.0)
    ride = Group([DrumHit("ride", 0.4 + 0.4 * i, -30.0) for i in range(33)], "ride", "TRANSIENT")
    ring = FeedbackRing(freq_hz=band_centre_hz(44, 15.0), excess_db=0.5, tau_loop_s=0.022, t_on=4.0, start_db=-70.0,
                        sat_db=-18.0, label="lav_412")
    return Scene(14.0, sources=[room_noise(), music_bed(-52.0, seed=seed), piano, bass, ride], rings=[ring])


# ==================================================================================================
# rendering + cache
# ==================================================================================================
_CACHE: dict[tuple, tuple[list[tuple[float, list[float]]], list[Episode], Renderer]] = {}


def _key(name: str, seed: int, overrides: dict[str, Any] | None) -> tuple:
    return (name, int(seed), tuple(sorted((overrides or {}).items())))


def render(scenario: Scenario | str, seed: int = 1, analyser_overrides: dict[str, Any] | None = None) -> Renderer:
    """Render (or fetch from cache) and return the finished :class:`Renderer` (frames, trace, episodes)."""
    sc = SCENARIOS[scenario] if isinstance(scenario, str) else scenario
    key = _key(sc.name, seed, analyser_overrides)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit[2]
    scene = sc.build(seed, analyser_overrides)
    r = Renderer(scene, seed)
    r.run()
    eps = ring_episodes(r)
    _CACHE[key] = (r.frames, eps, r)
    return r


def frames(scenario: Scenario | str, seed: int = 1, **analyser_overrides: Any) -> list[tuple[float, list[float]]]:
    """Pure, cached: the rendered ``[(ts, values[100]), ...]`` for (scenario, seed)."""
    return render(scenario, seed, analyser_overrides or None).frames


def ground_truth(scenario: Scenario | str, seed: int = 1, **analyser_overrides: Any) -> dict[str, Any]:
    sc = SCENARIOS[scenario] if isinstance(scenario, str) else scenario
    r = render(sc, seed, analyser_overrides or None)
    eps = _CACHE[_key(sc.name, seed, analyser_overrides or None)][1]
    return {"scenario": sc.name, "title": sc.title, "expect": sc.expect, "mode": sc.mode, "lf_optin": sc.lf_optin,
            "latency_budget_ms": sc.latency_budget_ms, "events": [e.to_dict() for e in eps],
            "invariant": "no detection that is not attributable to a listed feedback event"}


def clear_cache() -> None:
    _CACHE.clear()


ALL = tuple(SCENARIOS)
