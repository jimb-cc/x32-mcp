"""The adversarial corpus: the five auditors' breaker scenarios (55), registered into ``ADVERSARIAL`` — NOT into
``SCENARIOS`` (the main corpus stays byte-stable).

Each scenario is the auditor's code verbatim, renamed with a source prefix:

* ``AP01..AP10``  audit of disc-predicates        (Y1-Y10)
* ``AF01..AF14``  audit of disc-free-hand         (Y1-Y14)
* ``AM01..AM10``  audit of disc-minimal-delta     (A1-A10)
* ``AS01..AS10``  audit of disc-sequential-evidence (A1-A10)
* ``AT01..AT11``  audit of disc-track-and-group   (B1-B11)

``salt_name`` keeps the original name so every realisation is byte-identical to what the auditor measured; the tags
gain ``adversarial``, ``origin:<audit>`` and ``orig:<original name>``. No two scenarios are byte-identical; three are
re-renders of main-corpus builders with a different tag/analyser: AP03 = X1 at RTA gain +18, AT09 = X5 with
``lf_optin``, AT11 = S19 with ``lf_optin`` in ring_out.

Run: ``python -m rtasim.harness --adversarial [names]`` or ``evaluate(factory, scenarios=ADVERSARIAL.values())``.
"""

from __future__ import annotations

from typing import Callable

from .physics import AnalyserSettings, TAU_LOOP_LAV_S, TAU_LOOP_REVERB_S, TAU_LOOP_TOPS_S, TAU_LOOP_WEDGE_S
from .render import Scene
from .scenarios import Scenario, _quiet_combo, _s19, _x1, _x5, loud_band, music_bed, room_noise
from .sources import (
    BassLine, ChordPad, CommonModeGain, DrumHit, DrumPattern, FeedbackRing, Group, HarmonicNote, Melody, NoiseBurst,
    PinkBed, SpeechBursts, band_centre_hz, note_hz,
)

ADVERSARIAL: dict[str, Scenario] = {}

_ORIGIN = {"AP": "audit-disc-predicates", "AF": "audit-disc-free-hand", "AM": "audit-disc-minimal-delta",
           "AS": "audit-disc-sequential-evidence", "AT": "audit-disc-track-and-group", "AV": "verify-detector-final"}


def adversarial(name: str, orig: str, title: str, expect: str, duration_s: float, **kw):
    """Register ``fn`` as ADVERSARIAL[name]; ``orig`` is the auditor's scenario name (kept as the noise salt)."""
    def deco(fn: Callable[[int, AnalyserSettings], Scene]) -> Callable:
        tags = tuple(kw.pop("tags", ())) + ("adversarial", f"origin:{_ORIGIN[name[:2]]}", f"orig:{orig}")
        ADVERSARIAL[name] = Scenario(name, title, expect, duration_s, fn, tags=tags, salt_name=orig, **kw)
        return fn
    return deco


# ==================================================================================================
# AP: audit of disc-predicates (Y1..Y10)
# ==================================================================================================
@adversarial("AP01_flute_crescendo_no_family", "Y1_flute_crescendo_no_family",
             "flute upper register E6 (H2 -26, H3 -40: no partial within 18 dB of H1) held 4.5 s with a "
             "dB-linear CRESCENDO of 10 dB over 2.2 s (4.5 dB/s: sung/blown crescendi run 10-40 dB/s [L §5 P6]), tiny centred vibrato; "
             "then a synth sine lead C6 whose volume/filter swell rises 8 dB over 1.6 s; breath noise, bed -55. Targets RISE (>= 6 dB "
             "net rise of a stable family-less line == STRONG) with nothing else moving (no co-growth, no common mode).",
             "NOTE; 0 detections", 11.5, tags=("music", "near_sine", "held", "swell", "auditor"))
def _ap01(seed, st):
    a = HarmonicNote(f0_hz=note_hz("E6", -6.0), t_on=1.0, dur=4.5, level_db=-26.0, timbre="flute_high", attack_s=2.2,
                     attack_range_db=10.0, vib_rate_hz=5.2, vib_cents=12.0, vib_delay_s=0.8, vib_ramp_s=0.8, drift_cents=6.0,
                     flutter_db=0.6, timbre_jitter_db=2.0, release_db_per_s=100.0, seed=seed * 23 + 1, label="flute_cresc")
    b = HarmonicNote(f0_hz=note_hz("C6", 3.0), t_on=6.5, dur=4.0, level_db=-24.0, timbre="sine_lead", attack_s=1.6,
                     attack_range_db=8.0, flutter_db=0.15, release_db_per_s=120.0, seed=seed * 23 + 2, label="lead_swell")
    breath = PinkBed(level_1k_db=-60.0, tilt_db_per_oct=0.5, f_lo=900.0, f_hi=10000.0, lfo_db=1.5, seed=seed * 5 + 3, label="breath")
    return Scene(11.5, sources=[room_noise(), music_bed(-55.0, seed=seed), breath, a, b])


@adversarial("AP02_organ_note_at_arm_recurs", "Y2_organ_note_at_arm_recurs",
             "electronic flue organ (no family): G5 is sounding when the detector arms and ends at 0.45 s (inside "
             "arm_confirm_s, so it is correctly left alone); the melody moves on and RETURNS to the same G5 at t=3.0 (1.5 s) and t=7.0 (2.5 s) "
             "at the same registration/level. A note SEEN TO START twice. Targets the at-arm re-association (_new_track: any later line "
             "within +-0.25 band and +-6 dB of a line present at arm inherits born_at_arm and the AT-ARM evidence path).",
             "NOTE; 0 detections", 10.5, tags=("music", "near_sine", "held", "auditor"))
def _ap02(seed, st):
    g5 = note_hz("G5", 4.0)
    seq = [(g5, -0.8, 1.25), (note_hz("E5", 4.0), 0.5, 0.9), (note_hz("A5", 4.0), 1.45, 1.4), (g5, 3.0, 1.5),
           (note_hz("F5", 4.0), 4.6, 1.0), (note_hz("D5", 4.0), 5.7, 1.2), (g5, 7.0, 2.5)]
    notes = [HarmonicNote(f0_hz=f, t_on=t, dur=d, level_db=-28.0, timbre="organ_flue", attack_s=0.008, flutter_db=0.1,
                          timbre_jitter_db=3.0, release_db_per_s=90.0, seed=seed * 29 + i, label="organ")
             for i, (f, t, d) in enumerate(seq)]
    return Scene(10.5, sources=[room_noise(), music_bed(-52.0, seed=seed), Group(notes, "organ_melody")])


@adversarial("AP03_rta_gain_offset_18_organ_held", "Y3_rta_gain_offset_18_organ_held",
             "X1 (organ flue E5 held 7 s then B4, -27 over bed -52) rendered with the RTA manual gain left at +18 dB "
             "[A §2: /-prefs/rta/gain 0..60 is never read or written; S23b uses +24]: the note reads -9 dBFS and stands 25 dB above every "
             "other band. Targets LOUD (peak >= loud_line_db -10 AND >= 4 dB above everything => STRONG at K1) as an ABSOLUTE level test.",
             "NOTE; 0 detections", 12.0, tags=("music", "near_sine", "held", "prefs", "auditor"), analyser={"gain_offset_db": 18.0})
def _ap03(seed, st):
    return _x1(seed, st)


@adversarial("AP04_rta_gain_offset_clip_organ_chord", "Y4_rta_gain_offset_clip_organ_chord",
             "8'+4' organ chords (D4 A4 F#5 / E4 B4 G5 / C4 G4 E5, 3 s each from t=0.8, -26) with the RTA gain at "
             "+30 dB: fundamentals and 4' partials pin at the 0.0 clip flag; bed -50 -> -20. A full co-moving harmonic family is present. "
             "Targets the LOUD escape hatch (a line at clip_db keeps NO family veto of its own) on programme [S23b class, A §2].",
             "NOTE; 0 detections (clip flag on programme = misconfigured analyser, not a howl)", 10.0,
             tags=("music", "prefs", "clip", "auditor"), analyser={"gain_offset_db": 30.0})
def _ap04(seed, st):
    organ = ChordPad(chords=[["D4", "A4", "F#5"], ["E4", "B4", "G5"], ["C4", "G4", "E5"]], t_start=0.8, t_end=9.8, chord_s=3.0,
                     level_db=-26.0, timbre="organ_8_4", chorus_db=0.4, chorus_hz=0.9, seed=seed)
    return Scene(10.0, sources=[room_noise(), music_bed(-50.0, seed=seed), organ])


@adversarial("AP05_ring_onset_during_pad_swell", "Y5_ring_onset_during_pad_swell",
             "S21 saw pad (A2 E3 A3, 1.5 s dB-linear attack from t=1.0, chorus +-3 dB, held to 9 s) and a wedge ring at "
             "4.7 kHz (above the pad's partials) crossing threshold at t=1.4 with 0.5 dB on an 8 ms loop -> 63 dB/s to a -14 plateau. Rings "
             "start when the band gets loud [L §1.3]. Targets CO-GROWTH (>= 2 other lines with rise >= 3 dB => MUSICAL for that frame): the pad's "
             "15 lines keep rise_db >= 3 for the whole 64-frame rise window and then through the +-3 dB chorus beating.",
             "FEEDBACK onset 1.4; detect <= 300 ms after visibility; 0 detections on the pad", 10.0,
             tags=("feedback", "music", "swell", "mixture", "auditor"))
def _ap05(seed, st):
    p1 = ChordPad(chords=[["A2", "E3", "A3"]], t_start=1.0, t_end=9.0, chord_s=8.0, level_db=-32.0, timbre="saw",
                  attack_s=1.5, release_db_per_s=30.0, chorus_db=3.0, chorus_hz=1.2, seed=seed)
    for n in p1.children:
        n.attack_range_db = 30.0
    ring = FeedbackRing(freq_hz=4700.0, excess_db=0.5, tau_loop_s=0.008, t_on=1.4, start_db=-66.0, sat_db=-14.0, label="wedge_4k7")
    return Scene(10.0, sources=[room_noise(), music_bed(-55.0, seed=seed), p1], rings=[ring])


@adversarial("AP06_three_rings_after_master_shove", "Y6_three_rings_after_master_shove",
             "a +4 dB bus-master shove at t=2 (pre-fader tap: programme static) pushes THREE near-equal candidates over at "
             "once [L §1.2: the top candidates of a smooth loop envelope sit within 0.04-0.3 dB of each other]: 1.0 kHz (+0.4 dB, tau 6 ms), "
             "2.24 kHz (+0.5, 8 ms), 3.6 kHz (+0.3, 10 ms) -> 67/63/30 dB/s to limiter plateaux -13/-12/-15; quiet bed -52 + hats. Targets "
             "CO-GROWTH: each ring sees two OTHER rising lines and all three veto each other.",
             "three FEEDBACK events from t=2; each detected <= 300 ms", 9.0, tags=("feedback", "auditor"))
def _ap06(seed, st):
    pts = lambda e0, e1: ((0.0, e0), (2.0, e0), (2.05, e1))  # noqa: E731
    a = FeedbackRing(freq_hz=1003.0, excess_db=-3.6, excess_points=pts(-3.6, 0.4), tau_loop_s=0.006, t_on=0.0, start_db=-80.0,
                     sat_db=-13.0, label="ring_1k0")
    b = FeedbackRing(freq_hz=2243.0, excess_db=-3.5, excess_points=pts(-3.5, 0.5), tau_loop_s=0.008, t_on=0.0, start_db=-80.0,
                     sat_db=-12.0, label="ring_2k24")
    c = FeedbackRing(freq_hz=3610.0, excess_db=-3.7, excess_points=pts(-3.7, 0.3), tau_loop_s=0.010, t_on=0.0, start_db=-80.0,
                     sat_db=-15.0, label="ring_3k6")
    hats = DrumPattern(t_start=0.5, t_end=8.5, bpm=112.0, pattern="hats_only", level_db=-36.0, seed=seed)
    return Scene(9.0, sources=[room_noise(), music_bed(-52.0, lfo_db=1.0, seed=seed), hats], rings=[a, b, c])


@adversarial("AP07_very_slow_ring_2dB_s", "Y7_very_slow_ring_2dB_s",
             "a marginal loop that creeps: 1.93 kHz (band 66 +20 c), 0.02 dB excess on a 10 ms loop -> 2.0 dB/s (almost no "
             "excess wander) from -62 at t=0.5 to a -20 dBFS compressor plateau at ~21.5 s (>35 dB prominent), quiet bed -56 + light hats "
             "[L §1.3: e = 0.1 dB on a 35-70 ms loop = 1.4-2.9 dB/s; S13/X8 are the irregular versions]. Targets the RISE reference window: "
             "run_lv is a 64-frame deque (3.2 s) and the 20th-percentile reference, so a steady climb can never show more than ~2.6 s x rate "
             "of 'rise' -- an undeclared rate floor of ~2.3 dB/s under the claimed 'no rate window'; LOUD (-10) never applies.",
             "FEEDBACK; detect within 1 s of visibility", 26.0, tags=("feedback", "slow", "auditor"), latency_budget_ms=1000.0)
def _ap07(seed, st):
    hats = DrumPattern(t_start=0.5, t_end=25.5, bpm=96.0, pattern="hats_only", level_db=-38.0, seed=seed)
    ring = FeedbackRing(freq_hz=band_centre_hz(66, 20.0), excess_db=0.02, excess_wander_db=0.004, tau_loop_s=0.010, t_on=0.5,
                        start_db=-62.0, sat_db=-20.0, wander_db=0.3, label="creep_1k93")
    return Scene(26.0, sources=[room_noise(), music_bed(-56.0, tilt=-1.5, lfo_db=1.0, seed=seed), hats], rings=[ring])


@adversarial("AP08_established_ring_16dB_prominent_at_arm", "Y8_established_ring_16dB_prominent_at_arm",
             "X7's compressor-limited ring (1683 Hz, +-0.4 dB wander) already at its plateau when we arm, but at "
             "-38 dBFS under the same organ chords + piano + bass + bed -50: ~14-17 dB prominent instead of ~20-25 [L §1.4(2): the plateau "
             "level is arbitrary]. Targets AT-ARM evidence, which demands median prominence >= strong_prominence_db 18 over the first K1 "
             "frames (and RISE/LOUD can never apply to a plateau): the lead's '12-25 dB plateaued rings must be reachable' case.",
             "FEEDBACK from t=0; detect <= 1 s", 12.0, tags=("feedback", "established", "music", "mixture", "auditor"),
             latency_budget_ms=1000.0)
def _ap08(seed, st):
    ring = FeedbackRing(freq_hz=band_centre_hz(64, 35.0), excess_db=1.0, tau_loop_s=0.011, sat_db=-38.0, established=True,
                        wander_db=0.4, wander_hz=0.5, label="ring_1k68_plateau_quiet")
    return Scene(12.0, sources=[room_noise(), music_bed(-50.0, seed=seed)] + _quiet_combo(seed, 11.8), rings=[ring])


@adversarial("AP09_projector_whine_minus36_at_arm", "Y9_projector_whine_minus36_at_arm",
             "X20's stationary lines with the family-less projector/HVAC whine moved to 1187 Hz at -36 dBFS (+-0.3 dB, 2 c "
             "drift) -- a loud-ish whine in the vocal band, present before arm, dead steady, zero growth [L §3.2 'stationary non-feedback "
             "lines': hum bars sit around -40]; mains hum family at -46, bed -62, speech 3-9 s. Targets AT-ARM's one absolute number "
             "(arm_line_min_level_db -40): passively this is the X7/X20 irreducible pair, and the design resolves it by level alone.",
             "0 detections (a probe would show 1 dB/dB; watch mode has no probe)", 12.0,
             tags=("stationary", "trap", "irreducible_passive", "auditor"))
def _ap09(seed, st):
    hum = HarmonicNote(f0_hz=50.0, t_on=-5.0, dur=30.0, level_db=-46.0, timbre="hum", flutter_db=0.05, timbre_jitter_db=2.0,
                       seed=seed * 19 + 1, label="mains_hum")
    whine = HarmonicNote(f0_hz=1187.0, t_on=-5.0, dur=30.0, level_db=-36.0, timbre="sine_lead", flutter_db=0.3, drift_cents=2.0,
                         seed=seed * 19 + 2, label="projector_whine")
    sp = SpeechBursts(t_start=3.0, t_end=9.0, level_db=-34.0, seed=seed)
    return Scene(12.0, sources=[room_noise(-74.0), music_bed(-62.0, lfo_db=0.5, seed=seed), hum, whine, sp])


@adversarial("AP10_ring_at_song_start", "Y10_ring_at_song_start",
             "the band kicks in at t=1 (S20 mix: piano chords, bass, rock drums, bed -44 -> dozens of lines rise through their "
             "attacks / analyser rise) and the vocal-mic loop goes with it: 2.65 kHz crosses at t=1.3 with 0.6 dB on a 10 ms loop -> 60 dB/s "
             "to a -12 plateau [L §1.3: gain arrives in human-sized steps when the show gets loud]. Targets CO-GROWTH at a song start "
             "[A §5 'dozens of new lines appear at once'] and the 64-frame memory of every line's rise.",
             "FEEDBACK onset 1.3; detect <= 300 ms after visibility; 0 detections on the band", 10.0,
             tags=("feedback", "music", "mixture", "common_mode", "auditor"))
def _ap10(seed, st):
    bed = music_bed(-44.0, lfo_db=2.0, t_on=1.0, t_off=9.5, release_db_per_s=60.0, seed=seed)
    chords = ChordPad(chords=[["A3", "C#4", "E4"], ["F#3", "A3", "C#4"], ["D3", "F#3", "A3"], ["E3", "G#3", "B3"]],
                      t_start=1.0, t_end=9.5, chord_s=2.0, level_db=-34.0, timbre="piano", decay_db_per_s=3.0, seed=seed)
    bass = BassLine(notes=["A1", "F#1", "D2", "E2"], t_start=1.0, t_end=9.5, note_s=1.8, gap_s=0.2, level_db=-36.0, seed=seed)
    drums = DrumPattern(t_start=1.0, t_end=9.5, bpm=120.0, pattern="rock", level_db=-30.0, seed=seed)
    ring = FeedbackRing(freq_hz=2650.0, excess_db=0.6, tau_loop_s=0.010, t_on=1.3, start_db=-66.0, sat_db=-12.0, label="ring_2k65_songstart")
    return Scene(10.0, sources=[room_noise(), bed, chords, bass, drums], rings=[ring])


# ==================================================================================================
# AF: audit of disc-free-hand (Y1..Y14)
# ==================================================================================================
@adversarial("AF01_flute_crescendo_messa_di_voce", "Y1_flute_crescendo_messa_di_voce",
             "solo flute upper register (flute_high: H2 -26, i.e. at/below the bed) plays E6 with a "
             "1.5 s dB-linear crescendo of 16 dB (messa di voce, ~11 dB/s) then holds 3 s and a second phrase on C6; vibrato +-15 c "
             "developing late, 1 dB flutter, breath noise; bed -52 [A §4.2 flute; §4.3 'family-less sources'; L P6 'sung/blown "
             "crescendi of 10-40 dB/s exist']", "NOTE; 0 detections", 12.0, tags=("music", "near_sine", "swell", "auditor"))
def _af01(seed, st):
    a = HarmonicNote(f0_hz=note_hz("E6", 6.0), t_on=1.0, dur=4.5, level_db=-26.0, timbre="flute_high", attack_s=1.5,
                     attack_range_db=16.0, vib_rate_hz=5.2, vib_cents=15.0, vib_delay_s=1.8, vib_ramp_s=0.8, drift_cents=6.0,
                     flutter_db=1.0, timbre_jitter_db=3.0, release_db_per_s=90.0, seed=seed * 23 + 1, label="flute_cresc_E6")
    b = HarmonicNote(f0_hz=note_hz("C6", -8.0), t_on=6.5, dur=4.0, level_db=-27.0, timbre="flute_high", attack_s=1.2,
                     attack_range_db=14.0, vib_rate_hz=5.0, vib_cents=15.0, vib_delay_s=1.5, vib_ramp_s=0.8, drift_cents=6.0,
                     flutter_db=1.0, timbre_jitter_db=3.0, release_db_per_s=90.0, seed=seed * 23 + 2, label="flute_cresc_C6")
    breath = PinkBed(level_1k_db=-62.0, tilt_db_per_oct=0.0, f_lo=800.0, f_hi=9000.0, label="breath")
    return Scene(12.0, sources=[room_noise(), music_bed(-52.0, seed=seed), breath, a, b])


@adversarial("AF02_sine_pad_two_line_swell", "Y2_sine_pad_two_line_swell",
             "synth sine/triangle pad (sine_lead: H2 -48) playing an open fifth A4+E5 (ratio 1.498: NOT a harmonic "
             "pair) with a 1.2 s dB-linear attack of 24 dB (20 dB/s on exactly TWO lines), sustain 3.5 s with slow chorus AM, "
             "twice; bed -52 [A §4.2 'Pads: attack 0.3-2 s -> 10-40 dB/s = the ring signature verbatim'; two lines < the design's "
             "3-line synchrony veto]", "NOTE; 0 detections", 13.0, tags=("music", "near_sine", "swell", "auditor"))
def _af02(seed, st):
    p1 = ChordPad(chords=[["A4", "E5"]], t_start=1.0, t_end=6.0, chord_s=5.0, level_db=-26.0, timbre="sine_lead", attack_s=1.2,
                  release_db_per_s=40.0, chorus_db=1.0, chorus_hz=0.5, seed=seed, flutter_db=0.15)
    p2 = ChordPad(chords=[["G4", "D5"]], t_start=7.0, t_end=12.0, chord_s=5.0, level_db=-26.0, timbre="sine_lead", attack_s=1.2,
                  release_db_per_s=40.0, chorus_db=1.0, chorus_hz=0.5, seed=seed + 1, flutter_db=0.15)
    for n in p1.children + p2.children:
        n.attack_range_db = 24.0
    return Scene(13.0, sources=[room_noise(), music_bed(-52.0, seed=seed), p1, p2])


@adversarial("AF03_mic_cupped_wedge_howl_plateau_m13", "Y3_mic_cupped_wedge_howl_plateau_m13",
             "singer cups the mic in front of a wedge: 4 dB excess on a 5 ms loop -> 800 dB/s [L §1.3 table: "
             "'-60 -> -10 dBFS in 2-5 frames is the NORMAL watch case'] from -62 at t=3 to a speaker-limiter plateau at -13 dBFS "
             "(3 dB under the design's loud_level_db) at 2980 Hz, family-less, dead steady; vocal melody -28 + bed -50 continue",
             "FEEDBACK onset 3.0; detect <= 300 ms (it is a howl 35 dB over the mix for 6 s)", 10.0,
             tags=("feedback", "fast", "music", "auditor"))
def _af03(seed, st):
    vox = Melody(t_start=0.4, t_end=9.6, low="A3", high="D5", note_s=(0.3, 1.2), level_db=-28.0, timbre="voice", seed=seed,
                 vib_rate_hz=5.5, vib_cents=50.0, attack_s=0.05, glide_cents=-100.0, glide_s=0.1, repeat_prob=0.2)
    ring = FeedbackRing(freq_hz=band_centre_hz(72, 28.0), excess_db=4.0, tau_loop_s=TAU_LOOP_WEDGE_S, t_on=3.0, start_db=-62.0,
                        sat_db=-13.0, wander_db=0.3, label="cupped_2k98")
    return Scene(10.0, sources=[room_noise(), music_bed(-50.0, seed=seed), vox], rings=[ring])


@adversarial("AF04_established_lf_ring_122Hz_at_arm", "Y4_established_lf_ring_122Hz_at_arm",
             "feedback_watch armed while an acoustic guitar is ALREADY howling through its wedge at 121.8 Hz "
             "(body resonance, [L §3.2(f)]: '95-110 and 180-220 Hz dominate') at -15 dBFS, 35+ dB prominent, family-less, dead steady; "
             "fingerstyle guitar continues around it", "FEEDBACK from t=0; detect <= 600 ms (LF opt-in class)", 10.0,
             tags=("feedback", "established", "lf", "music", "auditor"), latency_budget_ms=600.0, lf_optin=True)
def _af04(seed, st):
    gtr = ChordPad(chords=[["G2", "B2", "D3", "G3"], ["E2", "B2", "E3", "G3"], ["C3", "E3", "G3", "C4"], ["D3", "A3", "D4", "F#4"]],
                   t_start=0.5, t_end=9.5, chord_s=2.0, level_db=-33.0, timbre="ac_guitar", decay_db_per_s=5.0, strum_s=0.22,
                   release_db_per_s=70.0, seed=seed)
    body = PinkBed(level_1k_db=-56.0, tilt_db_per_oct=-1.0, f_lo=60.0, f_hi=8000.0, bumps=((23.5, 7.0, 1.8),), label="guitar_body")
    ring = FeedbackRing(freq_hz=band_centre_hz(26, 42.0), excess_db=1.0, tau_loop_s=0.012, sat_db=-15.0, established=True,
                        wander_db=0.4, label="wedge_122_established")
    return Scene(10.0, sources=[room_noise(), body, gtr], rings=[ring])


@adversarial("AF05_ringout_projector_whine_minus36", "Y5_ringout_projector_whine_minus36",
             "ring_out in a quiet conference room: NO programme, +1 dB steps every 1.5 s from t=2, and a projector / "
             "moving-light whine at 1180 Hz [L §3.2 'stationary non-feedback lines: HVAC 100-600 Hz, projector whine 1-4 kHz'] at "
             "-36 dBFS (lectern condenser at +45 dB gain), family-less, +-0.3 dB, from before arm; it tracks the master 1 dB/dB "
             "(prog_coupling 1: it reaches the bus through the open mic). No loop within 20 dB of threshold.",
             "0 detections (a probe shows 1 dB/dB; passively it is X20's whine 14 dB louder)", 14.0,
             tags=("stationary", "trap", "ring_out", "probe", "irreducible_passive", "auditor"), mode="ring_out")
def _af05(seed, st):
    whine = HarmonicNote(f0_hz=1180.0, t_on=-5.0, dur=30.0, level_db=-36.0, timbre="sine_lead", flutter_db=0.3, drift_cents=2.0,
                         seed=seed * 19 + 2, label="projector_whine")
    master = CommonModeGain.ring_out_steps(t_first=2.0, dwell_s=1.5, step_db=1.0, n_steps=8)
    return Scene(14.0, sources=[room_noise(-74.0), whine], master=master, prog_coupling=1.0)


@adversarial("AF06_ring_onset_during_pad_swell", "Y6_ring_onset_during_pad_swell",
             "a ring starts DURING a synth-pad crescendo (song intro): saw pad A2-E3-A3 swelling 30 dB over 2.5 s from t=2 "
             "(12 dB/s on ~15 lines, un-chorused analogue pad), and at t=3.3 a 3.4 kHz loop with 0.4 dB excess on a 10 ms path (40 dB/s) grows from -66 to a "
             "-16 dBFS limiter plateau, which it reaches while the pad is still swelling; the pad then sustains [A §4.2 pads; L §1.3]",
             "FEEDBACK onset 3.3; detect <= 300 ms after visibility; 0 detections on the pad", 11.0,
             tags=("feedback", "music", "swell", "masked", "auditor"))
def _af06(seed, st):
    pad = ChordPad(chords=[["A2", "E3", "A3"]], t_start=2.0, t_end=10.5, chord_s=8.5, level_db=-30.0, timbre="saw", attack_s=2.5,
                   release_db_per_s=30.0, chorus_db=0.4, chorus_hz=1.1, seed=seed)
    for n in pad.children:
        n.attack_range_db = 30.0
    ring = FeedbackRing(freq_hz=band_centre_hz(74, 33.0), excess_db=0.4, tau_loop_s=0.010, t_on=3.3, start_db=-66.0, sat_db=-16.0,
                        wander_db=0.3, label="ring_3k4_under_swell")
    return Scene(11.0, sources=[room_noise(), music_bed(-54.0, seed=seed), pad], rings=[ring])


@adversarial("AF07_kick_sub_howl_fast_52Hz", "Y7_kick_sub_howl_fast_52Hz",
             "GENUINE LF feedback, fast: kick mic into a drum-fill sub, 52 Hz loop (band 14 +15 c), tau 20 ms with 2 dB "
             "excess -> 100 dB/s [L §3.2(e): 'drum-fill howl', tau 15-30 ms; e of a few dB is normal in watch, §1.3] from -70 at "
             "t=5 to a -9 dBFS amp-limiter plateau in ~0.6 s = 12 frames < the design's 18-frame settle window at 52 Hz; rock kick "
             "62 Hz + bass + bed continue", "FEEDBACK onset 5.0; detect <= 1 s (LF opt-in class)", 12.0,
             tags=("feedback", "lf", "fast", "music", "auditor"), latency_budget_ms=1000.0, lf_optin=True)
def _af07(seed, st):
    kick = DrumPattern(t_start=0.4, t_end=11.6, bpm=100.0, pattern="rock", level_db=-28.0, seed=seed, kick_hz=62.0)
    bass = BassLine(notes=["E1", "G1", "A1", "B1"], t_start=0.4, t_end=11.6, note_s=0.5, gap_s=0.1, level_db=-38.0, seed=seed)
    ring = FeedbackRing(freq_hz=band_centre_hz(14, 15.0), excess_db=2.0, tau_loop_s=0.020, t_on=5.0, start_db=-70.0, sat_db=-9.0,
                        excite_coupling_db=-3.0, wander_db=0.4, label="kick_sub_52_fast")
    return Scene(12.0, sources=[room_noise(), music_bed(-56.0, tilt=-1.8, lfo_db=1.5, seed=seed), kick, bass], rings=[ring])


@adversarial("AF08_loud_crowd_whistle_minus7dBFS", "Y8_loud_crowd_whistle_minus7dBFS",
             "a fingers-in-mouth crowd/referee whistle right at an open vocal mic: three whistles 1.9-2.4 kHz held "
             "0.9-1.4 s at -7 dBFS (bus running hot; [A §1 'a whistle is ~+18 dB prominent over programme of equal loudness'; "
             "§4.2 'audience whistles are loud']), +-35 c vibrato, 1.2 dB flutter, entry glide; band + crowd bed -40",
             "NOTE; 0 detections", 10.0, tags=("music", "near_sine", "loud", "trap", "auditor"))
def _af08(seed, st):
    import random as _r
    rng = _r.Random(seed * 313 + 5)
    ws = []
    for i, (t, band) in enumerate(((1.5, 66), (4.3, 68), (7.0, 69))):
        ws.append(HarmonicNote(f0_hz=band_centre_hz(band, rng.uniform(-15, 15)), t_on=t + rng.uniform(-0.2, 0.2), dur=rng.uniform(0.9, 1.4),
                               level_db=-7.0 + rng.uniform(-1.0, 0.5), timbre="whistle", attack_s=0.06, vib_rate_hz=rng.uniform(5.5, 6.5),
                               vib_cents=35.0, vib_delay_s=0.25, vib_ramp_s=0.3, glide_cents=-120.0, glide_s=0.12, drift_cents=20.0,
                               flutter_db=1.2, release_db_per_s=150.0, seed=seed * 41 + i, label="loud_whistle"))
    crowd = PinkBed(level_1k_db=-40.0, tilt_db_per_oct=-1.0, f_lo=200.0, f_hi=8000.0, lfo_db=2.0, region_mod_db=2.0, random_humps=3,
                    hump_db=3.0, seed=seed * 5 + 3, label="crowd")
    return Scene(10.0, sources=[room_noise(), crowd, Group(ws, "whistles")])


@adversarial("AF09_channel_fader_ride_on_held_organ_note", "Y9_channel_fader_ride_on_held_organ_note",
             "operator rides ONE channel: an electronic organ flue note (E5, H2 -36, family-less at this SNR) has "
             "been sounding at -44 (below the design's est_min_level_db) since before arm; at t=3 its channel fader is pushed +9 dB "
             "over 1.0 s (dB-linear, 9 dB/s) and it then holds at -35 for 5 s. The rest of the mix (bed -56, piano melody) is on other channels and does NOT move, so the "
             "spectrum median is flat [L §1.5/§5: channel-fader moves are per-channel; A §5]", "COMMON_MODE(one channel)+NOTE; 0 detections",
             11.0, tags=("music", "near_sine", "common_mode", "held", "auditor"))
def _af09(seed, st):
    # one continuous line: -36 from before arm, ramp +9 dB over 1 s at t=3, hold. Built from two back-to-back notes of the same
    # pitch/seed (the second one's 'attack' is the fader ride); the first releases instantly at the seam.
    f = note_hz("E5", 4.0)
    a = HarmonicNote(f0_hz=f, t_on=-2.0, dur=5.0, level_db=-44.0, timbre="organ_flue", flutter_db=0.1, timbre_jitter_db=3.0,
                     release_db_per_s=4000.0, seed=seed * 3 + 1, label="organ_E5_pre")
    b = HarmonicNote(f0_hz=f, t_on=3.0, dur=6.0, level_db=-35.0, timbre="organ_flue", attack_s=1.0, attack_range_db=9.0,
                     flutter_db=0.1, timbre_jitter_db=3.0, release_db_per_s=90.0, seed=seed * 3 + 1, label="organ_E5_ride")
    mel = Melody(t_start=0.4, t_end=10.5, low="C4", high="A4", note_s=(0.3, 0.9), level_db=-38.0, timbre="piano", seed=seed,
                 vib_cents=0.0, decay_db_per_s=4.0, attack_s=0.005, repeat_prob=0.2)
    return Scene(11.0, sources=[room_noise(), music_bed(-56.0, seed=seed), mel, a, b])


@adversarial("AF10_established_ring_at_arm_minus44", "Y10_established_ring_at_arm_minus44",
             "as S2a/X7 but the compressor-limited plateau sits at -44 dBFS (talker channel with a low comp threshold and "
             "a conservative gain structure; [L §1.4(2)]: the plateau is 'a bus level, unrelated to SPL'): 2.2 kHz, established before "
             "arm, ~28 dB prominent over a quiet bed -75, family-less, +-0.4 dB", "FEEDBACK from t=0; detect <= 1 s", 8.0,
             tags=("feedback", "established", "auditor"), latency_budget_ms=1000.0)
def _af10(seed, st):
    ring = FeedbackRing(freq_hz=band_centre_hz(68, 20.0), excess_db=1.0, tau_loop_s=0.011, sat_db=-44.0, established=True,
                        wander_db=0.4, wander_hz=0.5, label="ring_2k2_plateau_m44")
    return Scene(8.0, sources=[room_noise(), music_bed(-75.0, tilt=-1.5, lfo_db=1.0, seed=seed)], rings=[ring])


@adversarial("AF11_ringout_steps_over_leslie_organ", "Y11_ringout_steps_over_leslie_organ",
             "ring_out with programme present (as S6b): +1 dB steps every 1.5 s from t=2 over a sustained organ chord "
             "(8'+4', 8 lines) with a DEEP SLOW Leslie/chorale amplitude modulation (+-3 dB at ~0.35 Hz, independent phase per "
             "voice) + bed -55, prog_coupling 1; NO loop anywhere near threshold [A §4.2 Hammond/Leslie; C §7.6 probe statistics]",
             "0 detections (12-frame medians of a 0.35 Hz +-3 dB AM differ by up to several dB step to step by chance)", 15.0,
             tags=("music", "ring_out", "probe", "trap", "auditor"), mode="ring_out")
def _af11(seed, st):
    chord = ChordPad(chords=[["C3", "G3", "E4", "C5"]], t_start=-2.0, t_end=14.7, chord_s=16.7, level_db=-32.0,
                     timbre=(0.0, -4.0, -30.0), chorus_db=3.0, chorus_hz=0.35, seed=seed)
    master = CommonModeGain.ring_out_steps(t_first=2.0, dwell_s=1.5, step_db=1.0, n_steps=8)
    return Scene(15.0, sources=[room_noise(), music_bed(-55.0, seed=seed), chord], master=master, prog_coupling=1.0)


@adversarial("AF12_soprano_closed_vowel_crescendo", "Y12_soprano_closed_vowel_crescendo",
             "soprano closed vowel /u/ (voice_closed: H2 -20, H3 -26 -> below the bed at this level) on B5 (988 Hz, "
             "centred) with a 1.2 s crescendo of 14 dB then held 2.5 s, vibrato +-30 c developing after 1 s, 1.2 dB shimmer, 10 c "
             "drift; twice; bed -50 [A §4.2 voice: 'closed vowels at high pitch: H1 dominant, H2 -15..-30'; L P6 crescendi]",
             "NOTE; 0 detections", 11.0, tags=("music", "near_sine", "swell", "auditor"))
def _af12(seed, st):
    a = HarmonicNote(f0_hz=note_hz("B5", 3.0), t_on=1.0, dur=3.7, level_db=-27.0, timbre="voice_closed", attack_s=1.2,
                     attack_range_db=14.0, vib_rate_hz=5.8, vib_cents=30.0, vib_delay_s=1.0, vib_ramp_s=0.6, drift_cents=10.0,
                     flutter_db=1.2, timbre_jitter_db=3.0, release_db_per_s=100.0, seed=seed * 29 + 1, label="sop_cresc_B5")
    b = HarmonicNote(f0_hz=note_hz("G5", -5.0), t_on=6.0, dur=3.7, level_db=-28.0, timbre="voice_closed", attack_s=1.2,
                     attack_range_db=14.0, vib_rate_hz=5.6, vib_cents=30.0, vib_delay_s=1.0, vib_ramp_s=0.6, drift_cents=10.0,
                     flutter_db=1.2, timbre_jitter_db=3.0, release_db_per_s=100.0, seed=seed * 29 + 2, label="sop_cresc_G5")
    return Scene(11.0, sources=[room_noise(), music_bed(-50.0, seed=seed), a, b])


@adversarial("AF13_organ_note_held_at_arm_minus30", "Y13_organ_note_held_at_arm_minus30",
             "feedback_watch armed mid-song while an electronic organ flue note (E5, H2 -36: family-less at this SNR) is "
             "being HELD at -30 dBFS: it sounds from before arm for 3.5 s, then the organ melody moves on (B4, G4...); piano + bed -52 "
             "underneath [A §4.2 Hammond: 'pads of 2-10 s'; C §7.10 irreducible pair X4/X7 — here at X7's own level]",
             "NOTE; 0 detections", 10.0, tags=("music", "near_sine", "held", "established", "irreducible_passive", "auditor"))
def _af13(seed, st):
    held = HarmonicNote(f0_hz=note_hz("E5", 4.0), t_on=-2.5, dur=6.0, level_db=-30.0, timbre="organ_flue", flutter_db=0.1,
                        timbre_jitter_db=3.0, release_db_per_s=90.0, seed=seed * 3 + 1, label="organ_E5_held_at_arm")
    mel = Melody(t_start=3.6, t_end=9.6, low="G4", high="E5", note_s=(0.4, 1.2), level_db=-30.0, timbre="organ_flue", seed=seed,
                 vib_rate_hz=0.0, vib_cents=0.0, legato=True)
    pno = Melody(t_start=0.4, t_end=9.5, low="C4", high="A4", note_s=(0.3, 0.9), level_db=-36.0, timbre="piano", seed=seed + 5,
                 vib_cents=0.0, decay_db_per_s=4.0, attack_s=0.005, repeat_prob=0.2)
    return Scene(10.0, sources=[room_noise(), music_bed(-52.0, seed=seed), pno, held, mel])


@adversarial("AF14_lf_howl_to_desk_clip_100Hz", "Y14_lf_howl_to_desk_clip_100Hz",
             "an un-HPF'd instrument mic on a 15-inch wedge [L §3.2(d): 'can ring a wedge at 100-160 Hz'] howls at 100.8 Hz "
             "(band 23 +40 c): 3 dB excess on a 6 ms path -> 500 dB/s from -60 at t=4 straight into DESK CLIP (RTA reads 0.00, "
             "hard-clip odd partials appear); bass + kick + bed continue. Below the design's lf_strict_hz every rule but GROW/PROBE "
             "is disabled and GROW needs a >= 9-frame ramp at 100 Hz.", "FEEDBACK onset 4.0; detect <= 600 ms (it is pinned at full scale)",
             10.0, tags=("feedback", "lf", "clip", "fast", "music", "auditor"), latency_budget_ms=600.0, lf_optin=True)
def _af14(seed, st):
    kick = DrumPattern(t_start=0.4, t_end=9.6, bpm=100.0, pattern="rock", level_db=-30.0, seed=seed, kick_hz=62.0)
    bass = BassLine(notes=["E1", "G1", "A1", "B1"], t_start=0.4, t_end=9.6, note_s=0.5, gap_s=0.1, level_db=-38.0, seed=seed)
    ring = FeedbackRing(freq_hz=band_centre_hz(23, 40.0), excess_db=3.0, tau_loop_s=0.006, t_on=4.0, start_db=-60.0, sat_db=0.0,
                        wander_db=0.0, label="wedge_howl_101_clip")
    return Scene(10.0, sources=[room_noise(), music_bed(-54.0, tilt=-1.8, lfo_db=1.5, seed=seed), kick, bass], rings=[ring])


# ==================================================================================================
# AM: audit of disc-minimal-delta (A1..A10)
# ==================================================================================================
@adversarial("AM01_fast_howl_limiter_plateau_minus14", "A1_fast_howl_limiter_plateau_minus14",
             "feedback_watch, the NORMAL watch-mode howl (loop brief 1.3/1.4(1)): singer cups the mic / +3 dB "
             "fader shove -> 2.5 dB excess on a 6 ms wedge loop = 417 dB/s; 2.24 kHz from -62 to a CLEAN DSP-limiter plateau at "
             "-14 dBFS (no harmonics, desk not clipped) in ~2.5 frames, under a vocal melody -30 + bed -48",
             "FEEDBACK onset 3.0; detect <= 300 ms (a 2-frame rise to a -14 plateau is a howl, not an 'arrival')", 10.0,
             tags=("feedback", "fast", "music", "auditor"))
def _am01(seed, st):
    vox = Melody(t_start=0.4, t_end=9.6, low="A3", high="D5", note_s=(0.3, 1.1), level_db=-30.0, timbre="voice", seed=seed,
                 vib_rate_hz=5.5, vib_cents=50.0, attack_s=0.05, glide_cents=-100.0, glide_s=0.1, repeat_prob=0.2)
    ring = FeedbackRing(freq_hz=band_centre_hz(68, 20.0), excess_db=2.5, tau_loop_s=0.006, t_on=3.0, start_db=-62.0, sat_db=-14.0,
                        wander_db=0.4, label="howl_2k24_fast")
    return Scene(10.0, sources=[room_noise(), music_bed(-48.0, seed=seed), vox], rings=[ring])


@adversarial("AM02_sine_lead_slow_portamento", "A2_sine_lead_slow_portamento",
             "X4's synth sine lead (H2 -48) but with a slow 0.8 s portamento of 200-300 c into every note (theremin / "
             "slide-whistle / long-glide synth lead, analyser brief 4.2 'portamento common', 4.3(vi) 'a ring does not glide'), notes "
             "1.3-1.8 s at -26, LFO vibrato +-30 c delayed 1.2 s: the glide enters the destination band over many frames (not a <=3-frame "
             "'arrival'), then the line is dead flat for ~0.4 s",
             "NOTE; 0 detections (glide = programme; it also ends and moves with the melody)", 12.0,
             tags=("music", "near_sine", "glide", "auditor"))
def _am02(seed, st):
    import random as _r
    rng = _r.Random(seed * 101 + 7)
    seq = [("G5", 0.6, 1.5, 250.0), ("Bb5", 2.4, 1.4, 300.0), ("C6", 4.1, 1.8, 200.0), ("A5", 6.2, 1.5, -300.0), ("D6", 8.0, 1.6, 250.0),
           ("C6", 9.9, 1.4, -200.0)]
    notes = []
    for i, (nm, t, d, gl) in enumerate(seq):
        notes.append(HarmonicNote(f0_hz=note_hz(nm, rng.uniform(-15, 15)), t_on=t + rng.uniform(-0.05, 0.05), dur=d, level_db=-26.0,
                                  timbre="sine_lead", attack_s=0.01, glide_cents=-gl, glide_s=0.8, vib_rate_hz=5.8, vib_cents=30.0,
                                  vib_delay_s=1.2, vib_ramp_s=0.3, flutter_db=0.15, release_db_per_s=120.0, seed=seed * 11 + i,
                                  label="sine_lead_gliss"))
    return Scene(12.0, sources=[room_noise(), music_bed(-52.0, seed=seed), Group(notes, "lead")])


@adversarial("AM03_loud_whistle_near_full_scale", "A3_loud_whistle_near_full_scale",
             "X16's LOUD band (vox -18 broadband) plus performer/crowd whistles into the vocal mic at the same SPL: a sine "
             "concentrates ~18 dB more per 1/10-oct band than pink programme (analyser brief 1 'concentration gain'), so the whistle band "
             "reads -4..-2 dBFS (>= the design's -6 'clip' lane) with +-45 c vibrato, drift and glides; no loop anywhere",
             "NOTE; 0 detections (loud is not clipped: the desk flag is 0.00; vibrato/glide/ending say programme)", 12.0,
             tags=("music", "loud", "near_sine", "trap", "auditor"))
def _am03(seed, st):
    import random as _r
    rng = _r.Random(seed * 131 + 3)
    whistles = []
    t = 1.2
    i = 0
    while t < 11.0:
        f = rng.uniform(1300.0, 2500.0)
        d = rng.uniform(0.7, 1.4)
        whistles.append(HarmonicNote(f0_hz=f, t_on=t, dur=d, level_db=-3.0 + rng.uniform(-1.0, 1.0), timbre="whistle", attack_s=0.04,
                                     vib_rate_hz=rng.uniform(5.0, 6.5), vib_cents=rng.uniform(35, 55), vib_delay_s=0.25, vib_ramp_s=0.3,
                                     glide_cents=rng.choice((-120.0, 100.0, 150.0)), glide_s=rng.uniform(0.08, 0.15), drift_cents=30.0,
                                     flutter_db=1.5, release_db_per_s=150.0, seed=seed * 41 + i, label="loud_whistle"))
        t += d + rng.uniform(0.6, 1.5)
        i += 1
    return Scene(12.0, sources=[room_noise()] + loud_band(seed) + [Group(whistles, "whistles")])


@adversarial("AM04_projector_whine_minus40_before_arm", "A4_projector_whine_minus40_before_arm",
             "X20's stationary-line trap one gain-staging step louder: a family-less projector/moving-light whine at "
             "1180 Hz (loop brief 3.2 'stationary non-feedback lines to expect', 1-4 kHz) at -40 dBFS, +-0.3 dB, present since before "
             "arm; quiet bed -62, speech 3-9 s. Zero growth, responds 1 dB/dB, would sit in any per-band arm-time baseline (lead P5)",
             "0 detections (stationary line from before arm; the -45 dBFS floor is a room number, not physics)", 12.0,
             tags=("stationary", "trap", "auditor"))
def _am04(seed, st):
    whine = HarmonicNote(f0_hz=1180.0 * 2 ** ((seed % 3 - 1) * 17 / 1200.0), t_on=-5.0, dur=30.0, level_db=-40.0, timbre="sine_lead",
                         flutter_db=0.3, drift_cents=2.0, seed=seed * 19 + 2, label="projector_whine")
    hum = HarmonicNote(f0_hz=50.0, t_on=-5.0, dur=30.0, level_db=-46.0, timbre="hum", flutter_db=0.05, timbre_jitter_db=2.0,
                       seed=seed * 19 + 1, label="mains_hum")
    sp = SpeechBursts(t_start=3.0, t_end=9.0, level_db=-34.0, seed=seed)
    return Scene(12.0, sources=[room_noise(-74.0), music_bed(-62.0, lfo_db=0.5, seed=seed), hum, whine, sp])


@adversarial("AM05_flute_crescendo_straight_tone", "A5_flute_crescendo_straight_tone",
             "flute/recorder upper register (H2 -26, under the bed): A5 887 Hz swelled in from niente over 0.35 s "
             "(25 dB dB-linear = 71 dB/s: loop brief P6/P9 'blown/sung crescendi, fade-ins'), held 2.6 s straight (vibrato +-15 c only "
             "after 1.0 s), 8 c drift, 0.8 dB flutter; then E6 the same way; breath noise, bed -52",
             "NOTE; 0 detections (a swell is not a loop: it stops rising where the player wants, then gains vibrato and ends)", 9.0,
             tags=("music", "near_sine", "swell", "auditor"))
def _am05(seed, st):
    a = HarmonicNote(f0_hz=note_hz("A5", 14.0), t_on=1.0, dur=2.6, level_db=-28.0, timbre="flute_high", attack_s=0.35, attack_range_db=25.0,
                     vib_rate_hz=5.2, vib_cents=15.0, vib_delay_s=1.0, vib_ramp_s=0.6, drift_cents=8.0, flutter_db=0.8, timbre_jitter_db=3.0,
                     release_db_per_s=100.0, seed=seed * 5 + 1, label="flute_swell_A5")
    b = HarmonicNote(f0_hz=note_hz("E6", -9.0), t_on=4.6, dur=2.8, level_db=-29.0, timbre="flute_high", attack_s=0.45, attack_range_db=25.0,
                     vib_rate_hz=5.0, vib_cents=15.0, vib_delay_s=1.2, vib_ramp_s=0.6, drift_cents=8.0, flutter_db=0.8, timbre_jitter_db=3.0,
                     release_db_per_s=100.0, seed=seed * 5 + 2, label="flute_swell_E6")
    breath = PinkBed(level_1k_db=-60.0, tilt_db_per_oct=0.5, f_lo=900.0, f_hi=10000.0, lfo_db=1.5, seed=seed * 5 + 3, label="breath")
    return Scene(9.0, sources=[room_noise(), music_bed(-52.0, seed=seed), breath, a, b])


@adversarial("AM06_organ_swell_pedal_chord", "A6_organ_swell_pedal_chord",
             "electronic organ flue 8' (H2 -36, no Leslie) chord C5-E5-G5 brought in with the SWELL PEDAL: 1.2 s "
             "dB-linear rise of 24 dB (20 dB/s) on three family-less lines AT ONCE (analyser brief 4.2 pads; 5 'rings start alone'), held "
             "4 s, then A4-C5-F5 the same way", "NOTE; 0 detections (synchronous multi-line swell = programme)", 12.0,
             tags=("music", "near_sine", "swell", "auditor"))
def _am06(seed, st):
    p1 = ChordPad(chords=[["C5", "E5", "G5"]], t_start=1.0, t_end=6.2, chord_s=5.2, level_db=-30.0, timbre="organ_flue", attack_s=1.2,
                  release_db_per_s=90.0, chorus_db=0.3, chorus_hz=0.8, flutter_db=0.1, timbre_jitter_db=3.0, seed=seed)
    p2 = ChordPad(chords=[["A4", "C5", "F5"]], t_start=7.0, t_end=11.6, chord_s=4.6, level_db=-30.0, timbre="organ_flue", attack_s=1.2,
                  release_db_per_s=90.0, chorus_db=0.3, chorus_hz=0.8, flutter_db=0.1, timbre_jitter_db=3.0, seed=seed + 1)
    for n in p1.children + p2.children:
        n.attack_range_db = 24.0
    return Scene(12.0, sources=[room_noise(), music_bed(-52.0, seed=seed), p1, p2])


@adversarial("AM07_quiet_plateaued_howl_minus48_from_t0", "A7_quiet_plateaued_howl_minus48_from_t0",
             "X7's compressor-limited plateaued ring from before arm, but on a quiet lectern bus: 1683 Hz at -48 dBFS "
             "(loop brief 0(ii): the plateau is a BUS level at the pre-fader tap, unrelated to SPL; 1.4(2): -25..-10 typical but "
             "amp gain / master position put it anywhere), ~22 dB prominent over room noise + quiet bed -72, +-0.4 dB wander",
             "FEEDBACK from t=0; detect <= 1 s", 10.0, tags=("feedback", "established", "quiet", "auditor"), latency_budget_ms=1000.0)
def _am07(seed, st):
    ring = FeedbackRing(freq_hz=band_centre_hz(64, 35.0), excess_db=1.0, tau_loop_s=0.011, sat_db=-48.0, established=True,
                        wander_db=0.4, wander_hz=0.5, label="ring_1k68_quiet_plateau")
    return Scene(10.0, sources=[room_noise(), music_bed(-72.0, tilt=-1.5, lfo_db=1.0, seed=seed)], rings=[ring])


@adversarial("AM08_plateaued_ring_14dB_at_arm", "A8_plateaued_ring_14dB_at_arm",
             "ring already at its plateau when the detector arms, but only ~14-16 dB prominent: 3.3 kHz at -38 dBFS over a "
             "-50 bed + hats (compressor-held howl in a running mix, loop brief 1.4(2)); no onset, no growth, no clip",
             "FEEDBACK from t=0; detect <= 300 ms (brief: every ring within ~300 ms)", 8.0, tags=("feedback", "established", "auditor"))
def _am08(seed, st):
    ring = FeedbackRing(freq_hz=band_centre_hz(74, -22.0), excess_db=1.0, tau_loop_s=0.010, sat_db=-38.0, established=True,
                        wander_db=0.4, wander_hz=0.6, label="ring_3k3_14dB")
    hats = DrumPattern(t_start=0.5, t_end=7.5, bpm=112.0, pattern="hats_only", level_db=-36.0, seed=seed)
    return Scene(8.0, sources=[room_noise(), music_bed(-50.0, tilt=-1.0, lfo_db=1.5, seed=seed), hats], rings=[ring])


@adversarial("AM09_ring_onset_inside_cymbal_wash", "A9_ring_onset_inside_cymbal_wash",
             "S14 with a faster loop: crash at t=2.0 (+22 dB over bands 58-97), ring 3.3 kHz crosses threshold at 2.05 with "
             "1.0 dB excess on a 6 ms loop (167 dB/s: 6 rising frames, not an 'arrival') and reaches its -12 limiter plateau while the "
             "wash still covers it; it emerges as a plateau as the crash decays. The crash's +22 dB step sits in the ring band's memory.",
             "FEEDBACK onset 2.05; detect <= 300 ms after visibility", 8.0, tags=("feedback", "music", "masked", "fast", "auditor"))
def _am09(seed, st):
    crash = DrumHit("crash", 2.0, -26.0)
    ring = FeedbackRing(freq_hz=band_centre_hz(74, 10.0), excess_db=1.0, tau_loop_s=0.006, t_on=2.05, start_db=-60.0, sat_db=-12.0,
                        wander_db=0.4, label="ring_3k3_in_wash")
    return Scene(8.0, sources=[room_noise(), music_bed(-50.0, seed=seed), crash], rings=[ring])


@adversarial("AM10_organ_chord_sounding_at_arm", "A10_organ_chord_sounding_at_arm",
             "feedback_watch armed mid-song while an organ flue 8' chord (D5-F#5-A5, H2 -36: no usable family, no "
             "Leslie) is ALREADY sounding at -30: three dead-steady family-less lines with no observable onset for 5 s, then the chord "
             "changes (G4-B4-D5) and later stops: they END and MOVE with the music (analyser brief 4.3 i/ii), a ring would not",
             "NOTE; 0 detections", 12.0, tags=("music", "near_sine", "held", "at_arm", "auditor"))
def _am10(seed, st):
    p = ChordPad(chords=[["D5", "F#5", "A5"], ["G4", "B4", "D5"]], t_start=-1.5, t_end=9.5, chord_s=6.5, level_db=-30.0,
                 timbre="organ_flue", attack_s=0.008, release_db_per_s=90.0, chorus_db=0.3, chorus_hz=0.8, flutter_db=0.1,
                 timbre_jitter_db=3.0, seed=seed)
    return Scene(12.0, sources=[room_noise(), music_bed(-52.0, seed=seed), p])


# ==================================================================================================
# AS: audit of disc-sequential-evidence (A1..A10)
# ==================================================================================================
@adversarial("AS01_loud_sine_lead_minus12dBFS", "A1_loud_sine_lead_minus12dBFS",
             "X4's synth sine lead (H2 -48, portamento, vibrato delayed 0.5-1.5 s) played as a LOUD solo at -12 dBFS "
             "over a quiet combo (organ/piano/bass ~-34) + bed -52: family-less, dead-stable for 0.5-1.5 s per note, the loudest line "
             "by >10 dB, 35-40 dB prominent. Targets the 'level' (+1.2 .. +2.1 with 'dominant'), 'narrow' isolation (+1.3) and "
             "family-absence (+1.6) terms, which together outvote the -1.9 onset-step penalty [A §4.2 keys/§4.3 sine leads; L §1.4: "
             "a moderate RTA level says nothing about SPL]", "NOTE; 0 detections", 14.0,
             tags=("music", "near_sine", "held", "loud", "auditor"))
def _as01(seed, st):
    seq = [("G5", 0.6, 1.2, 0.0), ("Bb5", 1.85, 1.0, 300.0), ("C6", 2.9, 1.6, 200.0), ("D6", 4.55, 1.1, 200.0), ("Bb5", 5.7, 1.3, -400.0)]
    notes = []
    for i, (nm, t, d, gl) in enumerate(seq):
        notes.append(HarmonicNote(f0_hz=note_hz(nm), t_on=t, dur=d, level_db=-12.0, timbre="sine_lead", attack_s=0.01,
                                  glide_cents=-gl, glide_s=0.08 if gl else 0.0, vib_rate_hz=5.8, vib_cents=30.0, vib_delay_s=0.5,
                                  vib_ramp_s=0.3, flutter_db=0.15, release_db_per_s=120.0, seed=seed * 11 + i, label="sine_lead_loud"))
    held = HarmonicNote(f0_hz=note_hz("C6"), t_on=7.3, dur=6.0, level_db=-12.0, timbre="sine_lead", attack_s=0.01, glide_cents=-200.0,
                        glide_s=0.08, vib_rate_hz=5.8, vib_cents=35.0, vib_delay_s=1.5, vib_ramp_s=1.0, flutter_db=0.15,
                        release_db_per_s=120.0, seed=seed * 11 + 9, label="sine_lead_loud_held")
    return Scene(14.0, sources=[room_noise(), music_bed(-52.0, seed=seed)] + _quiet_combo(seed, 13.8) + [Group(notes, "lead"), held])


@adversarial("AS02_cupped_mic_howl_compressor_minus22", "A2_cupped_mic_howl_compressor_minus22",
             "feedback_watch, the NORMAL watch case per L §1.3/§1.4(2): singer cups the mic -> +6 dB excess on a 5 ms "
             "wedge loop = 1200 dB/s; 2.83 kHz (band 71 +20 c) goes from the bed to a channel-compressor plateau at -22 dBFS INSIDE ONE "
             "FRAME (clean sine, no harmonics, ~28 dB prominent) and sits there; vocal melody -30 + bed -52. Targets the onset-step "
             "penalty (-1.9 at levels <= -15 dBFS) and the 'fast_acc only above -16 dBFS' rule", "FEEDBACK onset 3.0; detect <= 300 ms", 9.0,
             tags=("feedback", "fast", "music", "compressor", "auditor"))
def _as02(seed, st):
    vox = Melody(t_start=0.4, t_end=8.6, low="A3", high="D5", note_s=(0.3, 1.2), level_db=-30.0, timbre="voice", seed=seed,
                 vib_rate_hz=5.5, vib_cents=50.0, attack_s=0.05, glide_cents=-100.0, glide_s=0.1, repeat_prob=0.2)
    ring = FeedbackRing(freq_hz=band_centre_hz(71, 20.0), excess_db=-12.0, excess_points=((0.0, -12.0), (3.0, -12.0), (3.01, 6.0)),
                        tau_loop_s=TAU_LOOP_WEDGE_S, t_on=0.0, start_db=-75.0, sat_db=-22.0, wander_db=0.4, label="cupped_2k8")
    return Scene(9.0, sources=[room_noise(), music_bed(-52.0, seed=seed), vox], rings=[ring], sat_coupling=1.0)


@adversarial("AS03_amp_clipped_howl_minus24dBFS", "A3_amp_clipped_howl_minus24dBFS",
             "X11's amp-clipped howl (905 Hz, 150 dB/s, acoustic H2 -22 / H3 -14 / H4 -30 / H5 -20 heard by the mic) but "
             "with the amp reaching clip while the desk tap reads only -24 dBFS (L §1.4(1): 'anywhere from -30 to 0 dBFS depending on amp "
             "gain and master position'); the same band 12 dB lower at the desk (amp gain high). Targets harmonics_void_level_db = -16: below it the odd/even partial "
             "family is scored as music", "FEEDBACK onset 4.0; detect <= 300 ms", 10.0,
             tags=("feedback", "clip", "harmonics", "fast", "music", "auditor"))
def _as03(seed, st):
    ring = FeedbackRing(freq_hz=band_centre_hz(55, 27.0), excess_db=1.2, tau_loop_s=0.008, t_on=4.0, start_db=-60.0, sat_db=-24.0,
                        harmonics=((2, -22.0), (3, -14.0), (4, -30.0), (5, -20.0)), harmonics_knee_db=-36.0, wander_db=0.5,
                        label="howl_905_ampclip_quiet")
    return Scene(10.0, sources=[room_noise()] + loud_band(seed, bed_db=-48.0, drums_db=-38.0, bass_db=-42.0, gtr_db=-44.0, vox_db=-38.0,
                                                          t1=9.7), rings=[ring])


@adversarial("AS04_plateaued_ring_at_H7_slot_of_loud_organ", "A4_plateaued_ring_at_H7_slot_of_loud_organ",
             "compressor-limited 3.66 kHz ring ALREADY at -26 dBFS when armed (35+ dB prominent over a -60 bed: "
             "S2a-like, which this design emits in ~50 ms) while a LOUD sustained organ note C5 (-11 dBFS, organ_8_4: H2-H4 only) sits "
             "exactly 28.07 bands (x7) below it for 7 s, then moves to A4. The ring is nobody's partial (organ has no H7; different onset; "
             "no co-movement) [L §2.2: distortion partials need the fundamental at clip, >= 40 dB prominent]. Targets the emit-time "
             "'is_harm' suppression (any non-growing line within +-0.8 band of x2..x8 above any line >= -15 dBFS)",
             "FEEDBACK from t=0; detect <= 1 s", 12.0, tags=("feedback", "established", "music", "loud", "auditor"), latency_budget_ms=1000.0)
def _as04(seed, st):
    c5 = note_hz("C5")
    organ_a = HarmonicNote(f0_hz=c5, t_on=-1.0, dur=8.0, level_db=-11.0, timbre="organ_8_4", attack_s=0.01, flutter_db=0.15,
                           timbre_jitter_db=2.0, release_db_per_s=90.0, am_db=0.4, am_hz=0.9, seed=seed * 23 + 1, label="organ_C5_loud")
    organ_b = HarmonicNote(f0_hz=note_hz("A4"), t_on=7.1, dur=4.5, level_db=-12.0, timbre="organ_8_4", attack_s=0.01, flutter_db=0.15,
                           timbre_jitter_db=2.0, release_db_per_s=90.0, am_db=0.4, am_hz=0.9, seed=seed * 23 + 2, label="organ_A4_loud")
    ring = FeedbackRing(freq_hz=c5 * 7.0, excess_db=1.0, tau_loop_s=0.010, sat_db=-26.0, established=True, wander_db=0.4,
                        label="ring_3k66_plateau")
    return Scene(12.0, sources=[room_noise(), music_bed(-60.0, lfo_db=1.0, seed=seed), organ_a, organ_b], rings=[ring])


@adversarial("AS05_organ_dyad_swell_pedal", "A5_organ_dyad_swell_pedal",
             "Hammond flue 8' (H2 -36) DYAD E5 + C6 (659 / 1047 Hz, not harmonically related) faded in with the expression pedal: "
             "1.2 s linear-in-dB attack over 24 dB (20 dB/s) to -26, held 2.5 s, released; three times. Two family-less, dead-stable "
             "lines rising together at a loop-like rate [A §4.2 pads/organ: '10-40 dB/s rise for up to 2 s = the ring signature verbatim'; "
             "L §5 P6 caveat]. Targets the synchrony veto, which needs >= 3 rising lines", "NOTE; 0 detections", 13.0,
             tags=("music", "near_sine", "swell", "auditor"))
def _as05(seed, st):
    notes = []
    for i, t in enumerate((0.8, 4.9, 9.0)):
        for j, nm in enumerate(("E5", "C6")):
            notes.append(HarmonicNote(f0_hz=note_hz(nm, 4.0), t_on=t, dur=3.7, level_db=-26.0 - 1.5 * j, timbre="organ_flue",
                                      attack_s=1.2, attack_range_db=24.0, flutter_db=0.1, timbre_jitter_db=3.0, release_db_per_s=90.0,
                                      seed=seed * 29 + 3 * i + j, label="organ_swell"))
    return Scene(13.0, sources=[room_noise(), music_bed(-52.0, seed=seed), Group(notes, "organ_dyad")])


@adversarial("AS06_ring_under_sixteenth_hats", "A6_ring_under_sixteenth_hats",
             "5.04 kHz ring, 0.4 dB on a 10 ms loop -> 40 dB/s from -66 at t=2 to -14, under a 16th-note closed hi-hat "
             "pattern (hat every 125 ms = 2.5 frames, -30) + bed -52: the +-2..4 neighbourhood floor jumps >= 4 dB on most frames while "
             "the line is < 15 dB prominent [A §4.2 cymbals/hi-hat]. Targets the 'masked frame is skipped, not scored' rule",
             "FEEDBACK onset 2.0; detect <= 300 ms", 8.0, tags=("feedback", "music", "masking", "auditor"))
def _as06(seed, st):
    hats = DrumPattern(t_start=0.5, t_end=7.8, bpm=240.0, pattern="hats_only", level_db=-26.0, seed=seed)
    ring = FeedbackRing(freq_hz=band_centre_hz(80, 12.0), excess_db=0.4, tau_loop_s=0.010, t_on=2.0, start_db=-66.0, sat_db=-14.0,
                        wander_db=0.4, label="ring_5k_hats16")
    return Scene(8.0, sources=[room_noise(), music_bed(-52.0, tilt=-1.0, lfo_db=1.0, seed=seed), hats], rings=[ring])


@adversarial("AS07_lectern_ring_140Hz_speech_no_optin", "A7_lectern_ring_140Hz_speech_no_optin",
             "lectern gooseneck (HPF 100 Hz, tops to 55 Hz): room+lectern-cavity mode at 140.6 Hz (band 28 +45 c), 0.5 dB "
             "on a 20 ms loop -> 25 dB/s from -70 at t=4 to a limiter plateau -15; speech -32 (F0 110-140 Hz: its H1 sweeps the band "
             "below) + quiet bed. Plausible per L §3.2(c) '160-400 Hz most likely, range ~100 Hz-11 kHz' and A §4.5 'lavalier/lectern "
             "ring at 150-800 Hz' — no LF opt-in is implied by a lectern. Targets f_low_watch = 160 Hz prior (-0.7 nats here) + the "
             "tau_a growth discount (gw 0.66, minrun 4 at 140 Hz)", "FEEDBACK onset 4.0; detect <= 600 ms", 12.0,
             tags=("feedback", "lf_mid", "speech", "auditor"), latency_budget_ms=600.0)
def _as07(seed, st):
    sp = SpeechBursts(t_start=0.4, t_end=11.6, level_db=-32.0, seed=seed)
    form = Group([NoiseBurst(band_lo=56, band_hi=76, t_on=n.t_on, level_db=-52.0, attack_s=0.03, hold_s=n.dur * 0.7,
                             decay_db_per_s=150.0, dur=n.dur + 0.3, label="formants") for n in sp.children], "formants", "NOTE")
    ring = FeedbackRing(freq_hz=band_centre_hz(28, 45.0), excess_db=0.5, tau_loop_s=0.020, t_on=4.0, start_db=-70.0, sat_db=-15.0,
                        wander_db=0.3, label="lectern_140")
    return Scene(12.0, sources=[room_noise(), music_bed(-60.0, lfo_db=0.5, seed=seed), sp, form], rings=[ring])


@adversarial("AS08_edge_ring_busmaster_drop20", "A8_edge_ring_busmaster_drop20",
             "established ring EXACTLY on the band 64/65 edge (1740 Hz) at -14 dBFS under organ chord + bass; the BUS MASTER "
             "(pre-fader tap: programme static, prog_coupling 0) drops -20 dB at t=3 (ring dies) and returns at t=6 (ring regrows at "
             "100 dB/s to -14). Split line + both signs of a gain step [A §3 edge tone; L §0, §1.5]", "FEEDBACK episodes [0,~3] and from 6.0; "
             "detect <= 300 ms; one stream", 10.0, tags=("feedback", "edge", "common_mode", "music", "established", "auditor"))
def _as08(seed, st):
    organ = ChordPad(chords=[["D3", "A3", "F#4", "D5"]], t_start=-2.0, t_end=9.8, chord_s=11.8, level_db=-32.0, timbre="organ_8_4",
                     chorus_db=0.4, chorus_hz=0.9, seed=seed)
    bass = BassLine(notes=["D2", "A1", "B1", "G1"], t_start=0.2, t_end=9.8, note_s=0.9, gap_s=0.1, level_db=-38.0, seed=seed)
    master = CommonModeGain(points=((0.0, 0.0), (3.0, 0.0), (3.0, -20.0), (6.0, -20.0), (6.0, 0.0)), label="busmaster_drop_raise")
    ring = FeedbackRing(freq_hz=band_centre_hz(64.5), excess_db=1.0, tau_loop_s=0.010, sat_db=-14.0, established=True,
                        wander_db=0.4, label="ring_1k74_edge")
    return Scene(10.0, sources=[room_noise(), music_bed(-52.0, seed=seed), organ, bass], rings=[ring], master=master,
                 prog_coupling=0.0, loop_coupling=1.0, sat_coupling=0.0)


@adversarial("AS09_two_rings_octave_co_onset", "A9_two_rings_octave_co_onset",
             "operator shoves the vocal fader at t=2: TWO candidates of the same rig an exact octave apart (1250 / 2500 Hz = "
             "bands 60 / 70, comb peaks of one reflection, L §2.3) cross threshold TOGETHER with different excess (0.35 / 0.6 dB on 8 ms "
             "-> 44 / 75 dB/s), plateaux -12 / -16; vocal melody -30 + bed -52. Co-onset + x2: the family/synchrony logic must still "
             "let growth-rate and level differences win", "both FEEDBACK; both detected <= 300 ms", 8.0,
             tags=("feedback", "coincidence", "music", "auditor"))
def _as09(seed, st):
    vox = Melody(t_start=0.4, t_end=7.6, low="A3", high="D5", note_s=(0.3, 1.2), level_db=-30.0, timbre="voice", seed=seed,
                 vib_rate_hz=5.5, vib_cents=50.0, attack_s=0.05, glide_cents=-100.0, glide_s=0.1, repeat_prob=0.2)
    a = FeedbackRing(freq_hz=band_centre_hz(60, 0.0), excess_db=0.35, tau_loop_s=0.008, t_on=2.0, start_db=-66.0, sat_db=-12.0, label="ring_1250")
    b = FeedbackRing(freq_hz=band_centre_hz(70, 0.0), excess_db=0.6, tau_loop_s=0.008, t_on=2.0, start_db=-68.0, sat_db=-16.0, label="ring_2500")
    return Scene(8.0, sources=[room_noise(), music_bed(-52.0, seed=seed), vox], rings=[a, b])


@adversarial("AS10_theremin_style_single_sine_swell", "A10_theremin_style_single_sine_swell",
             "a single family-less line (sine lead / theremin / flute-stop organ with swell pedal, H2 -48) that FADES IN "
             "alone: 1.0 s linear-in-dB attack over 30 dB (30 dB/s) to -24, no vibrato for the first 1.2 s, held 3 s, then the next note a "
             "third away; bed -52 + quiet piano. One line rising alone at a constant dB rate with no partials is this design's definition "
             "of a loop [L §5 P6 caveat: 'bowed/blown/sung crescendi (dB-linear swells of 10-40 dB/s exist)'; A §4.3 irreducible set] — "
             "included to MEASURE how fast it fires, tagged irreducible_passive", "NOTE; 0 detections (arguable)", 12.0,
             tags=("music", "near_sine", "swell", "irreducible_passive", "auditor"))
def _as10(seed, st):
    notes = []
    for i, (nm, t) in enumerate((("A5", 0.8), ("C6", 5.0), ("G5", 8.9))):
        notes.append(HarmonicNote(f0_hz=note_hz(nm, -7.0), t_on=t, dur=3.6, level_db=-24.0, timbre="sine_lead", attack_s=1.0,
                                  attack_range_db=30.0, vib_rate_hz=5.0, vib_cents=25.0, vib_delay_s=1.2, vib_ramp_s=0.6,
                                  flutter_db=0.15, release_db_per_s=100.0, seed=seed * 41 + i, label="theremin"))
    piano = Melody(t_start=0.4, t_end=11.6, low="C4", high="C5", note_s=(0.4, 1.0), level_db=-36.0, timbre="piano", seed=seed,
                   vib_cents=0.0, decay_db_per_s=4.0, attack_s=0.005, repeat_prob=0.2)
    return Scene(12.0, sources=[room_noise(), music_bed(-52.0, seed=seed), piano, Group(notes, "theremin")])


# ==================================================================================================
# AT: audit of disc-track-and-group (B1..B11)
# ==================================================================================================
@adversarial("AT01_solo_sine_pad_swell", "B1_solo_sine_pad_swell",
             "SOLO synth sine pad / flute-like line (H2 -48: no family, ONE oscillator: no chorus mates) whose notes "
             "C6 A5 E6 G5 fade in dB-linearly over 1.0 s (25 dB -> 25 dB/s), hold 1.2 s, vibrato only after 1.2 s [A 4.2 'pads: attack "
             "0.3-2 s -> 10-40 dB/s = the ring signature verbatim'; L 5 P6 'dB-linear swells of 10-40 dB/s exist']; bed -52",
             "NOTE; 0 detections (targets A:ramp: >= 6 linear increments, still rising, stationary, no family, no mates)", 11.0,
             tags=("music", "near_sine", "swell", "breaker"))
def _at01(seed, st):
    seq = [("C6", 7.0, 0.6), ("A5", -12.0, 3.1), ("E6", 15.0, 5.6), ("G5", 4.0, 8.1)]
    notes = []
    for i, (nm, cents, t) in enumerate(seq):
        notes.append(HarmonicNote(f0_hz=note_hz(nm, cents), t_on=t, dur=2.2, level_db=-26.0, timbre="sine_lead", attack_s=1.0,
                                  attack_range_db=25.0, vib_rate_hz=5.5, vib_cents=25.0, vib_delay_s=1.2, vib_ramp_s=0.4,
                                  flutter_db=0.3, drift_cents=3.0, release_db_per_s=60.0, seed=seed * 31 + i, label="sine_pad_swell"))
    return Scene(11.0, sources=[room_noise(), music_bed(-52.0, seed=seed), Group(notes, "pad")])


@adversarial("AT02_fast_wedge_ring_600dBps_plateau_m15", "B2_fast_wedge_ring_600dBps_plateau_m15",
             "medium-loud band (bed -40, drums -30, bass -34, guitars -34, vocal -28) and a wedge ring at 2.26 kHz "
             "(band 68 +20 c) with 3 dB excess on a 5 ms loop -> 600 dB/s from -60 at t=4, caught by the speaker limiter at -15 dBFS "
             "(desk not clipped, 5 dB under loud_level_db). L 1.3: 'in feedback_watch e of several dB and R of hundreds of dB/s are the "
             "NORMAL case: -60 -> -10 dBFS in 2-5 frames'. Targets the adult-onset veto (arrival <= 2 frames => never cut in watch).",
             "FEEDBACK onset 4.0; detect <= 300 ms after prominence", 10.0, tags=("feedback", "music", "fast", "breaker"))
def _at02(seed, st):
    band = loud_band(seed, bed_db=-40.0, drums_db=-30.0, bass_db=-34.0, gtr_db=-34.0, vox_db=-28.0, t0=0.3, t1=9.7)
    ring = FeedbackRing(freq_hz=band_centre_hz(68, 20.0), excess_db=3.0, tau_loop_s=TAU_LOOP_WEDGE_S, t_on=4.0, start_db=-60.0,
                        sat_db=-15.0, label="wedge_2k26_fast")
    return Scene(10.0, sources=[room_noise()] + band, rings=[ring])


@adversarial("AT03_two_rings_octave_same_step", "B3_two_rings_octave_same_step",
             "two modes of ONE 8 ms loop an exact octave apart (1480 / 2960 Hz: comb candidates n/tau are harmonically "
             "related by construction, L 2.3) whose magnitude-envelope peaks are equal to a fraction of a dB (L 1.2: top candidates lie "
             "within 0.04-0.3 dB), so ONE gain move at t=2.5 takes both over threshold with the same 0.5 dB excess -> both ~62 dB/s, born "
             "within a frame or two of each other; plateaux -14/-17; hats + bed -55. Targets the single-partner rule 'exact position AND "
             "co-born (+-2 frames) => family' and the co-growing rule (constant level difference).",
             "both FEEDBACK; both detected <= 300 ms", 9.0, tags=("feedback", "trap", "coincidence", "breaker"))
def _at03(seed, st):
    a = FeedbackRing(freq_hz=1480.0, excess_db=0.5, tau_loop_s=0.008, t_on=2.5, start_db=-66.0, sat_db=-14.0, label="ring_1480")
    b = FeedbackRing(freq_hz=2960.0, excess_db=0.5, tau_loop_s=0.008, t_on=2.5, start_db=-67.0, sat_db=-17.0, label="ring_2960")
    hats = DrumPattern(t_start=0.5, t_end=8.5, bpm=116.0, pattern="hats_only", level_db=-34.0, seed=seed)
    return Scene(9.0, sources=[room_noise(), music_bed(-55.0, lfo_db=1.0, seed=seed), hats], rings=[a, b])


@adversarial("AT04_lectern_ring_300Hz_over_mains_hum", "B4_lectern_ring_300Hz_over_mains_hum",
             "X20's mains hum + buzz family (50..350 Hz, from before arm, dead steady) and HVAC whine; a lectern loop "
             "(tau 20 ms) at 300.0 Hz = 6x50 = 3x100 = 2x150 crosses threshold at t=3 with 0.3 dB -> 15 dB/s from -64 to a -22 plateau. "
             "L 5 P2: 'a real ring at 2x a hum component (100/120 Hz region) — use co-movement, hum does not move'. Targets the harmonic "
             "look-up (m=2/m=3 hypotheses find the hum's 150/100/200 Hz lines while the ring is young), the 6-frame benefit-of-the-doubt in "
             "_compatible and the lineage memory.", "FEEDBACK onset 3.0; detect <= 300 ms; 0 detections on hum/whine", 12.0,
             tags=("feedback", "stationary", "lf", "breaker"))
def _at04(seed, st):
    hum = HarmonicNote(f0_hz=50.0, t_on=-5.0, dur=30.0, level_db=-46.0, timbre="hum", flutter_db=0.05, timbre_jitter_db=2.0,
                       seed=seed * 19 + 1, label="mains_hum")
    whine = HarmonicNote(f0_hz=587.0, t_on=-5.0, dur=30.0, level_db=-50.0, timbre="sine_lead", flutter_db=0.3, drift_cents=2.0,
                         seed=seed * 19 + 2, label="hvac_whine")
    ring = FeedbackRing(freq_hz=300.0, excess_db=0.3, tau_loop_s=0.020, t_on=3.0, start_db=-64.0, sat_db=-22.0, label="lectern_300")
    return Scene(12.0, sources=[room_noise(-74.0), music_bed(-62.0, lfo_db=0.5, seed=seed), hum, whine], rings=[ring])


@adversarial("AT05_ring_grows_out_of_held_organ_note", "B5_ring_grows_out_of_held_organ_note",
             "electronic organ flue E5 (659 Hz, H2 -36) held 0.5-8 s at -30 over bed -52; a tops loop (tau 10 ms) whose "
             "mode sits 8 cents above the note is seeded BY the note (L 1.5: the oscillation starts from the coupled programme line) "
             "and is pushed over threshold at t=3 with 0.2 dB -> 20 dB/s from the note's level to a -12 plateau. Targets the "
             "re-qualification rule: the track is the note's (adult) and must show >= 8 dB / >= 6 increments of solitary growth "
             "AFTER it dominates the band before A:ramp may fire.", "FEEDBACK onset 3.0; detect <= 300 ms after the ring dominates",
             11.0, tags=("feedback", "music", "near_sine", "breaker"))
def _at05(seed, st):
    f = note_hz("E5", 4.0)
    organ = HarmonicNote(f0_hz=f, t_on=0.5, dur=7.5, level_db=-30.0, timbre="organ_flue", attack_s=0.008, flutter_db=0.1,
                         timbre_jitter_db=3.0, release_db_per_s=90.0, seed=seed * 3 + 1, label="organ_E5")
    ring = FeedbackRing(freq_hz=f * 2.0 ** (8.0 / 1200.0), excess_db=0.2, tau_loop_s=TAU_LOOP_TOPS_S, t_on=0.0, start_db=-80.0,
                        sat_db=-12.0, excess_points=((0.0, -3.0), (3.0, -3.0), (3.0, 0.2)), label="mode_on_E5")
    return Scene(11.0, sources=[room_noise(), music_bed(-52.0, seed=seed), organ], rings=[ring])


@adversarial("AT06_fast_ring_plateaus_inside_cymbal_wash", "B6_fast_ring_plateaus_inside_cymbal_wash",
             "bed -48 + hats; crash at t=3.0 (+22 dB over bands 58-97, 12 dB/s); a wedge ring at 3.4 kHz (band 74 "
             "+20 c) crosses at t=3.1 with 2 dB on 5 ms -> 400 dB/s and is limiter-caught at -24 dBFS within two frames, INSIDE the wash "
             "(A 4.2: 'a wash raises the HF floor 15-20 dB -> masks a real 3-8 kHz ring; as it decays a constant ring underneath gains "
             "prominence at +10-20 dB/s without any level change'). S14's fast twin. Targets the onset classifier (flat back-fill => "
             "'slow') + the tier-B window (15 clean frames, prominence median >= 18, not 'decaying' while the neighbours fall).",
             "FEEDBACK onset 3.1; detect <= 300 ms after prominence (~t=4)", 9.0, tags=("feedback", "music", "masked", "fast", "breaker"))
def _at06(seed, st):
    hats = DrumPattern(t_start=0.5, t_end=8.5, bpm=120.0, pattern="hats_only", level_db=-34.0, seed=seed)
    crash = DrumHit("crash", 3.0, -26.0)
    ring = FeedbackRing(freq_hz=band_centre_hz(74, 20.0), excess_db=2.0, tau_loop_s=TAU_LOOP_WEDGE_S, t_on=3.1, start_db=-60.0,
                        sat_db=-24.0, label="wedge_3k4_in_wash")
    return Scene(9.0, sources=[room_noise(), music_bed(-48.0, seed=seed), hats, crash], rings=[ring])


@adversarial("AT07_watch_fader_push_into_feedback", "B7_watch_fader_push_into_feedback",
             "THE feedback_watch case: organ chord (8'+4', sounding before arm) + bed -55; the operator pushes the vocal "
             "CHANNEL fader +6 dB over t=3..5 (3 dB/s; prog_coupling 1: every line rises, LF per tau_a; sat_coupling 1) and a 2.2 kHz "
             "tops loop (tau 10 ms) sitting at -2.5 dB goes through threshold at t~3.8: growth ACCELERATES 0 -> 350 dB/s while the whole "
             "spectrum is still rising, plateau -14 (+6 with the fader). L 1.5 / A 5: common mode + a real loop at once. Probes the "
             "reference-floor test, the ensemble-swell veto and the linearity tests on an accelerating ramp.",
             "FEEDBACK onset ~3.8; detect <= 300 ms; 0 detections on the organ lines", 10.0,
             tags=("feedback", "music", "common_mode", "breaker"))
def _at07(seed, st):
    chord = ChordPad(chords=[["C3", "G3", "E4", "C5"]], t_start=-2.0, t_end=9.7, chord_s=11.7, level_db=-34.0,
                     timbre=(0.0, -4.0, -30.0), chorus_db=0.4, chorus_hz=0.9, seed=seed)
    master = CommonModeGain(points=((0.0, 0.0), (3.0, 0.0), (5.0, 6.0)), label="channel_fader")
    ring = FeedbackRing(freq_hz=2200.0, excess_db=-2.5, tau_loop_s=TAU_LOOP_TOPS_S, t_on=0.0, start_db=-80.0, sat_db=-20.0,
                        label="tops_2k2")
    return Scene(10.0, sources=[room_noise(), music_bed(-55.0, seed=seed), chord], rings=[ring], master=master,
                 prog_coupling=1.0, sat_coupling=1.0)


@adversarial("AT08_organ_note_sounding_at_arm", "B8_organ_note_sounding_at_arm",
             "feedback_watch armed mid-song: an electronic organ flue E5 (H2 -36) has been holding since before arm "
             "(-32 over bed -52: ~20 dB prominent, 0.1 dB flutter, zero drift) and holds 5.5 s more, then B4. CORPUS 7.10: passive "
             "physics cannot separate this from X7 except by level/duration; L 4.3 watch tier A asks >= 25-30 dB for a no-family "
             "stationary line, the design cuts at_arm lines at >= 18 dB after 250 ms (A:prominent).",
             "NOTE; 0 detections (documents the at_arm operating point)", 10.0,
             tags=("music", "near_sine", "held", "irreducible_passive", "breaker"))
def _at08(seed, st):
    a = HarmonicNote(f0_hz=note_hz("E5", 4.0), t_on=-1.2, dur=6.7, level_db=-32.0, timbre="organ_flue", attack_s=0.008,
                     flutter_db=0.1, timbre_jitter_db=3.0, release_db_per_s=90.0, seed=seed * 3 + 1, label="organ_E5_prearm")
    b = HarmonicNote(f0_hz=note_hz("B4", 4.0), t_on=6.0, dur=3.5, level_db=-32.0, timbre="organ_flue", attack_s=0.008,
                     flutter_db=0.1, timbre_jitter_db=3.0, release_db_per_s=90.0, seed=seed * 3 + 2, label="organ_B4")
    return Scene(10.0, sources=[room_noise(), music_bed(-52.0, seed=seed), a, b])


@adversarial("AT09_808_bassline_lf_optin", "B9_808_bassline_lf_optin",
             "X5's clean 808 line + 4 s sustained sine A1, rendered for a session where a kick mic IS declared "
             "(lf_feedback_possible -> window opens to 40 Hz). The design says 'LF lines inside the window get no special tier: the "
             "onset/decay/family/stationarity predicates already reject tau_a-smeared bass' — but no family-less LF programme scenario was "
             "ever inside its window (S7/X5 are watch scenes below 160 Hz; S19 is below the 100 Hz ring_out edge).",
             "NOTE; 0 detections", 15.0, tags=("music", "lf", "near_sine", "breaker"), lf_optin=True)
def _at09(seed, st):
    return _x5(seed, st)


@adversarial("AT10_sustained_sine_sub_lf_optin", "B10_sustained_sine_sub_lf_optin",
             "sustained synth sine sub-bass (A 4.2: 'reese/sine pad: steady for bars (2-8 s), pure -> the single hardest "
             "LF case'): E1 41.2 Hz (band 10.4) 4 s then F1 43.7 Hz (band 11.3) 4.5 s at -30, instant onsets, H2 -35, over room rumble, "
             "in a session where a kick mic IS declared (lf_feedback_possible -> window from 40 Hz), DEFAULT analyser (tau_a = 0.5/df = "
             "185 ms at band 10; the same happens at attack_k 1.0 and with the BQ model). The tau_a-smeared onset is read as a 12-frame "
             "'ramp' (A:ramp, ~23-41 dB/s) and the plateau then re-cut by tier B every cooldown: the M7 40 Hz mechanism, inside the window.",
             "NOTE; 0 detections", 12.0, tags=("music", "lf", "near_sine", "held", "breaker"), lf_optin=True)
def _at10(seed, st):
    a = HarmonicNote(f0_hz=note_hz("E1", 6.0), t_on=1.0, dur=4.0, level_db=-30.0, timbre="synth_sine_bass", flutter_db=0.1,
                     release_db_per_s=60.0, seed=seed * 13 + 1, label="sine_sub_E1")
    b = HarmonicNote(f0_hz=note_hz("F1", -5.0), t_on=5.6, dur=4.5, level_db=-30.0, timbre="synth_sine_bass", flutter_db=0.1,
                     release_db_per_s=60.0, seed=seed * 13 + 2, label="sine_sub_F1")
    return Scene(12.0, sources=[room_noise(), music_bed(-64.0, tilt=-1.6, lfo_db=1.0, seed=seed), a, b])


@adversarial("AT11_driven_room_mode_ringout_lf_optin", "B11_driven_room_mode_ringout_lf_optin",
             "S19 (43 Hz axial room mode, Q 20, driven +10 dB by a bass line while ring_out steps the master "
             "+1 dB / 1.5 s; everything at the tap follows 1.00 dB/dB) in a session where the kick/bass-cab mic IS declared, so the window "
             "opens to 40 Hz and the mode is finally INSIDE it (in the designer's runs S19 sat below the 100 Hz ring_out edge). A 6 / L 5 "
             "P7/P14: a driven resonance responds 1 dB/dB and decays at 60/T60 when the note ends; the design's own answer is 'probe-linear "
             "=> veto'. Observed: the resonator+tau_a build-up is read as an 11-frame 'ramp' (A:ramp), and a bass-note onset landing in a "
             "probe window reads as +6 dB over-response (A:probe): GEQ 40 (and 80) cut = the M7 outcome.",
             "DRIVEN_RESONANCE + NOTE; 0 detections", 14.0, tags=("music", "lf", "ring_out", "trap", "breaker"), mode="ring_out", lf_optin=True)
def _at11(seed, st):
    return _s19(seed, st)


ALL_ADVERSARIAL = tuple(ADVERSARIAL)
assert len(ADVERSARIAL) == 55, len(ADVERSARIAL)
AUDIT_ADVERSARIAL = ALL_ADVERSARIAL          # the five auditors' 55 (what FINAL.md §4 reports on)


# ==================================================================================================
# AV: verifier's breakers against the FINAL integration (wt/detector-final @ 8094eed) -- one per new mechanism:
# G1 back-filled FAST-RISE, F1/at-arm bookkeeping, G6 relative LOUD, G2 note_cut()/false_cut, G3 frozen, G4 probe-before-
# AT-ARM, G5 independent partner, G8 programme_present(), G7 tier-B publication. [L §x] = physics-loop brief,
# [A §x] = physics-analyser brief. Registered after the auditors' 55 so ALL_ADVERSARIAL / FINAL.md §4 stay comparable;
# ``VERIFIER_BREAKERS`` lists them.
# ==================================================================================================
import random as _random


@adversarial("AV01_voice_chord_onsets_strummed", "AV01_voice_chord_onsets_strummed",
             "G1 FAST-RISE vs staggered chord onsets: a vocal group / SATB pad (timbre 'voice': H2..H4 within +-6 dB of H1 "
             "[A §4.2 voice]) sings 4-voice chords with 120 ms attacks [A §4.2: onset scoops 80-200 ms], entries staggered 50 ms "
             "(strum_s), a new chord every 1.6 s (8 onsets), vibrato +-45 c developing after 0.4 s, bed -50. In a D/G/A-major "
             "voicing the voices' partials COINCIDE in one 1/10-oct band (A3 H5 = 1100 Hz, F#4 H3 = 1110 Hz, D4 H4 = 1175 Hz): the "
             "cluster climbs in 3-4 equal ~5 dB steps as successive voices arrive = the 'bed step + >= 3 increments' FAST-RISE "
             "signature, while the crowded low-mid leaves the fundamentals' other partials < 6 dB prominent for a few frames so the "
             "'isH3' veto flickers off. A ring starts ALONE [A §5 'onset synchrony across >= 3 unrelated bands within 2 frames => "
             "programme'; L §2.3]; here >= 6 lines are born within +-2 frames. (X21's hold-out/unseen FPs at 1110 Hz, without the ring.)",
             "NOTE; 0 detections", 14.0, tags=("music", "voice", "chord", "onset", "verifier"))
def _av01(seed, st):
    choir = ChordPad(chords=[["D3", "A3", "D4", "F#4"], ["G3", "B3", "D4", "G4"], ["A3", "C#4", "E4", "A4"], ["D3", "F#3", "A3", "D4"],
                            ["B2", "F#3", "B3", "D4"], ["E3", "G#3", "B3", "E4"]],
                     t_start=0.3, t_end=13.7, chord_s=1.6, level_db=-33.0, timbre="voice", attack_s=0.12, release_db_per_s=40.0,
                     vib_rate_hz=5.4, vib_cents=45.0, vib_delay_s=0.4, drift_cents=10.0, flutter_db=1.2, timbre_jitter_db=4.0,
                     strum_s=0.05, seed=seed)
    return Scene(14.0, sources=[room_noise(), music_bed(-50.0, seed=seed), choir])


@adversarial("AV02_flute_soft_attacks_120ms", "AV02_flute_soft_attacks_120ms",
             "G1 FAST-RISE vs family-less SOFT attacks: flute upper register (flute_high: H2 -26, H3 -40 -> no partial within "
             "partial_rel_db 18 of H1 [A §4.3 'flute/recorder/piccolo upper register: no detectable family']) playing a legato-ish "
             "melody C6-A6, notes 0.45-1.2 s, breath/legato attacks of 100-170 ms (tongued flute attacks are 20-60 ms; slurred / soft / "
             "'ha' attacks 100-250 ms -- the swell-into-the-note that wind pedagogy calls a breath attack), each rising dB-linearly ~25 dB "
             "out of the bed (-50) to -28..-24: at 20 fps that is the bed step + 2-3 comparable increments summing >= 15 dB and 'still "
             "rising' at the last one -- exactly FAST-RISE's admission rule (fast_rise_min_steps 3 incl. the virtual bed sample). Then the "
             "note holds nearly flat (flutter 1 dB, vibrato +-15 c after 0.5 s) for >= K1. The judge's G1 guard was '>= 3 SETTLED "
             "increments ... do not admit 2-frame evidence in watch'; the bed-as-first-sample makes a 2-frame attack admissible.",
             "NOTE; 0 detections", 13.0, tags=("music", "near_sine", "onset", "verifier"))
def _av02(seed, st):
    rng = _random.Random(seed * 977 + 31)
    names = ["C6", "D6", "E6", "G6", "A6", "F6", "D6", "B5", "C6", "E6", "G6", "A6", "F6", "D6"]
    notes = []
    t = 0.6
    i = rng.randrange(len(names))
    while t < 12.2:
        d = rng.uniform(0.45, 1.2)
        att = rng.uniform(0.10, 0.17)
        notes.append(HarmonicNote(f0_hz=note_hz(names[i % len(names)], rng.uniform(-15, 15)), t_on=t, dur=d, level_db=-26.0 + rng.uniform(-2, 2),
                                  timbre="flute_high", attack_s=att, attack_range_db=30.0, vib_rate_hz=5.2, vib_cents=15.0, vib_delay_s=0.5,
                                  vib_ramp_s=0.4, drift_cents=8.0, flutter_db=1.0, timbre_jitter_db=2.0, release_db_per_s=90.0,
                                  seed=seed * 41 + i, label="flute_soft"))
        t += d + rng.uniform(0.08, 0.25)
        i += rng.choice((1, 1, 2, -1, 3))
    breath = PinkBed(level_1k_db=-58.0, tilt_db_per_oct=0.5, f_lo=900.0, f_hi=10000.0, lfo_db=1.5, seed=seed * 5 + 3, label="breath")
    return Scene(13.0, sources=[room_noise(), music_bed(-50.0, seed=seed), breath, Group(notes, "flute_melody")])


@adversarial("AV03_organ_notes_start_within_1s_of_arm", "AV03_organ_notes_start_within_1s_of_arm",
             "AT-ARM bookkeeping (F1's twin): `born_at_arm` is stamped on ANY 6 dB-prominent local maximum present in the first "
             "arm_frames (3) and such a track then COASTS coast_frames (8 = 0.4 s), re-matching anything within +-0.6 band; the onset-jump "
             "rule restarts the RUN of whatever lands on it but not born_at_arm, and arm_evidence is evaluated on the track's 'first K1 "
             "matched frames' whenever they happen. Two everyday ways a NOTE that the detector SAW START inherits 'established at arm': "
             "(a) a repeated note across the arm instant -- electronic organ flue (H2 -36, no usable family) plays G5 staccato (sounding "
             "at arm, ends at +0.12 s), rests 0.3 s (< the coast window) and re-strikes the same G5 at 0.45 s, held 4 s (AP02 tested 1.5 s "
             "and 2.5 s gaps, after the at-arm track had died); (b) fresh pitches E5/C5/A4 starting 0.35-0.7 s after arm over a bed with "
             "+-4 dB humps [C §2.9] -- when one lands within 0.6 band of a bed bump tracked at frames 0-2 it inherits the flag (X1 seed 11: "
             "organ E5 starting at 0.5 s cut as 'established_at_arm' at 2.0 s). All notes -27..-30 dBFS (< arm_fast_level, > arm_line_min).",
             "NOTE; 0 detections", 8.0, tags=("music", "near_sine", "held", "at_arm", "verifier"))
def _av03(seed, st):
    rng = _random.Random(seed * 313 + 7)
    g5 = note_hz("G5", rng.uniform(-30, 30))
    pre = HarmonicNote(f0_hz=g5, t_on=-0.35, dur=0.47, level_db=-28.0, timbre="organ_flue", attack_s=0.008, flutter_db=0.1,
                       timbre_jitter_db=3.0, release_db_per_s=120.0, seed=seed * 3 + 9, label="organ_G5_staccato")
    again = HarmonicNote(f0_hz=g5, t_on=0.45 + rng.uniform(-0.03, 0.03), dur=4.0, level_db=-28.0 + rng.uniform(-1, 1), timbre="organ_flue",
                         attack_s=0.008, flutter_db=0.1, timbre_jitter_db=3.0, release_db_per_s=90.0, seed=seed * 3 + 10, label="organ_G5_held")
    pitches = ["E5", "C5", "A4"]
    notes = [HarmonicNote(f0_hz=note_hz(nm, rng.uniform(-40, 40)), t_on=0.35 + 0.17 * k + rng.uniform(-0.02, 0.02), dur=rng.uniform(3.0, 5.0),
                          level_db=-29.0 + rng.uniform(-1.5, 1.5), timbre="organ_flue", attack_s=0.008, flutter_db=0.1, timbre_jitter_db=3.0,
                          release_db_per_s=90.0, seed=seed * 3 + k, label=f"organ_{nm}") for k, nm in enumerate(pitches)]
    return Scene(8.0, sources=[room_noise(), music_bed(-50.0, seed=seed, hump_db=4.0), pre, again, Group(notes, "organ_lh")])


@adversarial("AV04_soprano_ff_climax_note_minus6", "AV04_soprano_ff_climax_note_minus6",
             "G6 relative LOUD vs a solo line 6+ dB above everything: a cappella / ballad ending -- the accompaniment stops and a "
             "soprano belts a closed-vowel climax note (B5 988 Hz, voice_closed: H2 -20, H3 -26 [A §4.2 'closed vowels /u/ /i/ at "
             "high pitch: H1 dominant, H2 -15..-30']) at -6 dBFS at the tap for 2.5 s over a -42 sustained-hum bed (p95 ~ -40 -> LOUD line "
             "= max(-10, -40+20) = -10). Vibrato develops 250-400 ms into the note [A §4.2: 'vibrato ... develops 200-400 ms into the "
             "note'] to +-70 c; the first 300 ms are straight with an 80 ms attack and a -120 c scoop. LOUD + BASE at K1 = 250 ms, i.e. "
             "BEFORE the vibrato that would clear it. A whistle is the corpus's version (AF08, argued irreducible); a sung ff note into a "
             "close mic with the band tacet is the common one. L §4.3 tier A 'level >= -10 dBFS narrowband' is the physics brief's own "
             "rule, so this documents the price of that rule rather than a bug: expected by L §4.3 = cut; expected by ground truth = NOTE.",
             "NOTE; 0 detections", 8.0, tags=("music", "voice", "loud", "near_sine", "verifier"))
def _av04(seed, st):
    v1 = HarmonicNote(f0_hz=note_hz("B5", 6.0), t_on=1.5, dur=2.5, level_db=-6.0, timbre="voice_closed", attack_s=0.08, attack_range_db=30.0,
                      vib_rate_hz=5.6, vib_cents=70.0, vib_delay_s=0.3, vib_ramp_s=0.35, glide_cents=-120.0, glide_s=0.1, am_db=0.8, am_hz=5.6,
                      drift_cents=8.0, flutter_db=1.0, timbre_jitter_db=3.0, release_db_per_s=80.0, seed=seed * 7 + 1, label="soprano_B5_ff")
    v2 = HarmonicNote(f0_hz=note_hz("G5", -4.0), t_on=4.8, dur=2.0, level_db=-8.0, timbre="voice_closed", attack_s=0.08, attack_range_db=30.0,
                      vib_rate_hz=5.4, vib_cents=60.0, vib_delay_s=0.35, vib_ramp_s=0.35, glide_cents=-100.0, glide_s=0.1, am_db=0.8, am_hz=5.4,
                      drift_cents=8.0, flutter_db=1.0, timbre_jitter_db=3.0, release_db_per_s=80.0, seed=seed * 7 + 2, label="soprano_G5_ff")
    # what is left of the band: a held pad/hum residue 35 dB down and the room
    return Scene(8.0, sources=[room_noise(), music_bed(-42.0, lfo_db=1.0, seed=seed), v1, v2])


@adversarial("AV05_howl_e4_limiter_on_geq_centre_watch", "AV05_howl_e4_limiter_on_geq_centre_watch",
             "G2 note_cut()/false_cut vs the NORMAL watch howl [L §1.3: 'in feedback_watch gain arrives in human-sized steps (+3..+6 dB "
             "fader shove, mic cupping +6..+10 dB) so e of several dB ... are the normal case'; L §1.4(1): the plateau is a speaker-DSP "
             "limiter DOWNSTREAM of the pre-fader tap]: lav -> tops, tau 22 ms, +4 dB excess at t=3 -> 180 dB/s from -70 to a -16 dBFS "
             "limiter plateau (6 frames of dB-linear climb: FAST-RISE/RISE, tier A) at 1250 Hz = RTA band 60 = GEQ 19 CENTRE, under speech "
             "-34 + bed -52. Closed loop: the -3 dB cut leaves e = +1 dB, the SPL stays pinned at the limiter, the tap drops by EXACTLY the "
             "bell (3.0 dB) and sits flat -> note_cut() files 'false_cut' (klass FALSE_CUT, never re-emitted) although L §4.1 says 'a ring "
             "with e > attenuation ... -> deepen' and L §4.3 tier A 'deepen when VERIFY fails AND the line is still at limit'; cfs watch has no "
             "VERIFY of its own (cfs.py:54: 'a cut deepens by the detector re-emitting'). -6 dB would kill it (e = -2 -> -90 dB/s).",
             "FEEDBACK onset 3.0; detect <= 300 ms; closed loop: ring must be DEAD (deepened to -6) within ~2 s of the first cut", 10.0,
             tags=("feedback", "speech", "limiter", "closed_loop", "verifier"))
def _av05(seed, st):
    sp = SpeechBursts(t_start=0.5, t_end=9.5, level_db=-34.0, seed=seed)
    ring = FeedbackRing(freq_hz=1250.0, excess_db=-10.0, excess_points=((0.0, -10.0), (3.0, -10.0), (3.02, 4.0)), tau_loop_s=TAU_LOOP_LAV_S,
                        t_on=0.0, start_db=-70.0, sat_db=-16.0, wander_db=0.4, label="howl_1250_e4")
    return Scene(10.0, sources=[room_noise(), music_bed(-52.0, seed=seed), sp], rings=[ring])


@adversarial("AV06_howl_geq_midpoint_e2p5_limiter_watch", "AV06_howl_geq_midpoint_e2p5_limiter_watch",
             "G2 false_cut vs an OFF-CENTRE ring [L §4.1: 'bands i = 5 (mod 10) sit at the exact GEQ midpoint ... a single -3 gives "
             "-1.5 (Q 4.3) .. -2.5 (Q 2) dB at the ring ... prefer cutting both flanking bands / deepening']: ring at 1768 Hz = RTA band 65 "
             "= midway between GEQ 1.6 k and 2 k, +2.5 dB excess on a 22 ms path (114 dB/s) from t=3 to a -16 dBFS limiter plateau, speech + "
             "bed. Closed loop: the first -3 dB cut at 1.6 k (or 2 k) attenuates the line by ~2.0-2.2 dB (renderer GEQ Q 3) < e, so the loop "
             "KEEPS HOWLING at its limiter while the tap drops by exactly that bell -> 'false_cut' -> the detector never asks again, so cfs "
             "can neither deepen nor add the flanking band. Expected: ring dead within ~2 s (either -6 at one flank or -3/-3).",
             "FEEDBACK onset 3.0; detect <= 300 ms; closed loop: ring must be DEAD by the end", 10.0,
             tags=("feedback", "speech", "limiter", "midpoint", "closed_loop", "verifier"))
def _av06(seed, st):
    sp = SpeechBursts(t_start=0.5, t_end=9.5, level_db=-34.0, seed=seed)
    ring = FeedbackRing(freq_hz=band_centre_hz(65, 0.0), excess_db=-10.0, excess_points=((0.0, -10.0), (3.0, -10.0), (3.02, 2.5)),
                        tau_loop_s=TAU_LOOP_LAV_S, t_on=0.0, start_db=-70.0, sat_db=-16.0, wander_db=0.4, label="howl_1768_midpoint")
    return Scene(10.0, sources=[room_noise(), music_bed(-52.0, seed=seed), sp], rings=[ring])


@adversarial("AV07_marginal_ring_e2p85_reverberant_cut", "AV07_marginal_ring_e2p85_reverberant_cut",
             "G2 note_cut() vs a ring the -3 dB cut only JUST kills: area mic in the reverberant field, tau_eff 70 ms [L §1.2], "
             "+2.85 dB excess from t=2.5 (41 dB/s) at 1250 Hz (GEQ centre) to a -18 plateau, quiet-ish bed -56 + soft pad chords. Closed "
             "loop: after -3 dB, e = -0.15 -> the loop rings DOWN at only |e|/tau ~ 2 dB/s [L §1.5: T60 = 60 tau/d = 28 s]: the tap shows "
             "-3.0 dB at once (the bell) and then a slow fall -- 'drop ~ bell, nearly flat' for the first second. Must NOT be filed "
             "false_cut (it is a real ring, and false_cut = 'release the band' in cfs's policy would bring it straight back); "
             "'confirmed'/'ambiguous' are acceptable. Checks the false_cut tolerance (+-1 dB, flat = range <= 2 dB over K1) against slow ring-down.",
             "FEEDBACK onset 2.5; detect <= 600 ms; closed loop: not false_cut", 12.0,
             tags=("feedback", "music", "reverberant", "closed_loop", "verifier"), latency_budget_ms=600.0)
def _av07(seed, st):
    pad = ChordPad(chords=[["C4", "E4", "G4"], ["A3", "C4", "F4"]], t_start=0.4, t_end=11.5, chord_s=2.8, level_db=-40.0, timbre="organ_8_4",
                   chorus_db=0.5, chorus_hz=0.9, seed=seed)
    ring = FeedbackRing(freq_hz=1250.0, excess_db=-8.0, excess_points=((0.0, -8.0), (2.5, -8.0), (2.52, 2.85)), tau_loop_s=TAU_LOOP_REVERB_S,
                        t_on=0.0, start_db=-70.0, sat_db=-18.0, wander_db=0.4, excess_wander_db=0.05, label="ring_1250_marginal")
    return Scene(12.0, sources=[room_noise(), music_bed(-56.0, lfo_db=1.0, seed=seed), pad], rings=[ring])


@adversarial("AV08_ringout_arm_while_howling_minus28_limiter", "AV08_ringout_arm_while_howling_minus28_limiter",
             "G4 probe-before-AT-ARM: ring_out is armed while the system is ALREADY howling quietly -- an established 2.1 kHz line at -28 "
             "dBFS at the tap held by the powered speaker's limiter (L §1.4(1): the tap level of a limiter-held howl is 'anywhere from "
             "-30 to 0 dBFS depending on amp gain and master position' and says nothing about SPL), 45 dB prominent, dead steady (+-0.3 dB), "
             "no family, silent room (-74), NO programme; the server steps +1 dB every 1.5 s from t=2 (prog_coupling 0.3 = spill, the "
             "downstream plateau ignores the master: sat_coupling 0 -> probe answer PINNED). L §4.3 ring_out 'certain: no-family line >= 30 "
             "dB prominent and stationary >= 3 frames => cut -3 immediately'; the winner cut it at 0.8 s; G4 makes AT-ARM below "
             "arm_fast_level (-20) wait for probe_min_hits (2) JUDGED steps = first step 2.0 s + settle/window + second step 3.5 s + window "
             "~ 4.6-5 s of howling. Measures that delay and that PROGRAMME_PRESENT stays down.",
             "FEEDBACK from t=0; ring_out contract says cut; report the latency G4 costs", 12.0,
             tags=("feedback", "ring_out", "established", "probe", "verifier"), mode="ring_out", latency_budget_ms=1000.0)
def _av08(seed, st):
    ring = FeedbackRing(freq_hz=band_centre_hz(67, 30.0), excess_db=1.5, tau_loop_s=TAU_LOOP_TOPS_S, sat_db=-28.0, established=True,
                        wander_db=0.3, label="howl_2k1_m28")
    master = CommonModeGain.ring_out_steps(t_first=2.0, dwell_s=1.5, step_db=1.0, n_steps=6)
    return Scene(12.0, sources=[room_noise(-74.0)], rings=[ring], master=master, prog_coupling=0.3, sat_coupling=0.0)


@adversarial("AV09_ringout_arm_while_howling_2to1_compressor", "AV09_ringout_arm_while_howling_2to1_compressor",
             "G4 + PROBE semantics vs a CHANNEL-COMPRESSOR-held howl (L §1.4(2), the M7 case: 'clean, unclipped, rock-steady sine at "
             "-25..-10 dBFS at the tap'). Compressor statics out = T + (in-T)/r with the loop closing in = out + G give the plateau "
             "out - T = G/(r-1), so d(tap)/d(master) = 1/(r-1): a 2:1 vocal compressor makes the howl FOLLOW the server's +1 dB steps "
             "EXACTLY 1.00 dB/dB (2.5:1 -> 0.67, 3:1 -> 0.5) -- the probe's definition of a room source ('FOLLOWS => STATIONARY, never cut, "
             "AT-ARM/LOUD void'). Same scene as AV08 with sat_coupling 1.0 (the compressor sits between mic and tap) and the plateau at "
             "-24. A whine cannot be told from this passively or by +1 dB probes; only a back-off larger than the excess separates them (howl "
             "dies, whine drops dB for dB) [L §4.3 'prefer a -1..-6 dB back-off']. Expected by contract: cut (or at least never a FINAL "
             "'stationary' verdict on a 45 dB-prominent -24 dBFS line: flag it for a back-off test).",
             "FEEDBACK from t=0; must be cut (or escalated), never silently STATIONARY", 12.0,
             tags=("feedback", "ring_out", "established", "probe", "compressor", "verifier"), mode="ring_out", latency_budget_ms=1000.0)
def _av09(seed, st):
    ring = FeedbackRing(freq_hz=band_centre_hz(67, 30.0), excess_db=1.5, tau_loop_s=TAU_LOOP_TOPS_S, sat_db=-24.0, established=True,
                        wander_db=0.3, label="howl_2k1_comp2to1")
    master = CommonModeGain.ring_out_steps(t_first=2.0, dwell_s=1.5, step_db=1.0, n_steps=6)
    return Scene(12.0, sources=[room_noise(-74.0)], rings=[ring], master=master, prog_coupling=0.3, sat_coupling=1.0)


@adversarial("AV10_organ_pedal_note_plus_octave_melody", "AV10_organ_pedal_note_plus_octave_melody",
             "G5 independent-partner rule on an instrument: electronic organ flue (no family) holds a pedal C5 (523 Hz) for 9 s at -30 "
             "while the right hand plays a melody C6/G6/E6/C6... (organ_flue, 0.4-1 s notes) whose notes land EXACTLY on the pedal's H2 "
             "(+10 bands), H3 (+15.85) and H4 (+20) slots >= K1 after the pedal started and while it holds within 0.3 dB: by G5 every one "
             "is 'independent' (correct: they are other keys), so the pedal stays a family-less, dead-steady BASE line for 9 s that was seen "
             "to start (t=0.4) -- MODERATE for ever, never cut in watch (no rise, not loud, not at arm). Also checks the melody notes are "
             "not cut and that the pedal's own onset (instant, 1 increment) is not FAST-RISE. Sanity breaker: expected to HOLD.",
             "NOTE; 0 detections", 11.0, tags=("music", "near_sine", "held", "verifier"))
def _av10(seed, st):
    rng = _random.Random(seed * 59 + 3)
    pedal = HarmonicNote(f0_hz=note_hz("C5", 3.0), t_on=0.4, dur=9.0, level_db=-30.0, timbre="organ_flue", attack_s=0.008, flutter_db=0.1,
                         timbre_jitter_db=3.0, release_db_per_s=90.0, seed=seed * 3 + 1, label="organ_pedal_C5")
    seq = ["C6", "G6", "E6", "C6", "G6", "C7", "G6", "E6", "C6", "G6", "E6", "C7"]
    notes = []
    t = 1.2
    for k, nm in enumerate(seq):
        d = rng.uniform(0.4, 1.0)
        notes.append(HarmonicNote(f0_hz=note_hz(nm, 3.0), t_on=t, dur=d, level_db=-29.0 + rng.uniform(-1.5, 1.5), timbre="organ_flue",
                                  attack_s=0.008, flutter_db=0.1, timbre_jitter_db=3.0, release_db_per_s=90.0, seed=seed * 17 + k, label="organ_rh"))
        t += d + rng.uniform(0.02, 0.12)
        if t > 10.0:
            break
    return Scene(11.0, sources=[room_noise(), music_bed(-52.0, seed=seed), pedal, Group(notes, "organ_melody")])


@adversarial("AV11_quiet_stage_hvac_intermittent_talker_ringout", "AV11_quiet_stage_hvac_intermittent_talker_ringout",
             "G8 programme_present() as the ring_out contract check: 'quiet' stage = room -74, HVAC/fan tone 240 Hz at -46 (+ trace H2) "
             "and mains hum 50 Hz family at -50 [L §3.2 'stationary non-feedback lines'], and ONE person talking intermittently near the "
             "open lectern mic (speech bursts -36 dBFS during 2.0-4.2 s and 7.0-8.6 s, silence otherwise) while the server steps +1 dB / 1.5 s "
             "from t=1.5 (prog_coupling 1: everything reaches the bus through the mic). Expected: PROGRAMME_PRESENT up while (and ~2 s "
             "after) the talker speaks, DOWN in the gaps and before 2 s; the whine/hum follow the steps 1 dB/dB -> STATIONARY; 0 detections.",
             "0 detections; PROGRAMME_PRESENT tracks the talker", 12.0,
             tags=("speech", "stationary", "ring_out", "probe", "contract", "verifier"), mode="ring_out")
def _av11(seed, st):
    whine = HarmonicNote(f0_hz=240.0 * 2 ** (seed * 7 % 30 / 1200.0), t_on=-5.0, dur=30.0, level_db=-46.0, timbre="sine_lead", flutter_db=0.3,
                         drift_cents=2.0, seed=seed * 19 + 2, label="hvac_240")
    hum = HarmonicNote(f0_hz=50.0, t_on=-5.0, dur=30.0, level_db=-50.0, timbre="hum", flutter_db=0.2, seed=seed * 19 + 3, label="mains_hum")
    sp1 = SpeechBursts(t_start=2.0, t_end=4.2, level_db=-36.0, seed=seed)
    sp2 = SpeechBursts(t_start=7.0, t_end=8.6, level_db=-36.0, seed=seed + 5)
    master = CommonModeGain.ring_out_steps(t_first=1.5, dwell_s=1.5, step_db=1.0, n_steps=7)
    return Scene(12.0, sources=[room_noise(-74.0), whine, hum, sp1, sp2], master=master, prog_coupling=1.0)


@adversarial("AV12_fast_howl_350dBps_compressor_m19_lectern", "AV12_fast_howl_350dBps_compressor_m19_lectern",
             "G7 tier-B publication in watch: lectern talker leans in -> +3.5 dB excess on a 10 ms path = 350 dB/s [L §1.3 table] at "
             "1.9 kHz (band 66 +25 c) from the speech-excited floor to a channel-compressor plateau at -19 dBFS in ~3 frames, then a clean "
             "steady sine (wander 0.4 dB) for 5 s while the talk continues (-32) over bed -55. > ~300 dB/s under a bed = '<= 2 visible "
             "increments' = the sine-lead onset [judge §5.1]: not tier A. Required: published as a MODERATE candidate within 250 ms of "
             "visibility with klass/level_db/prominence_db/excess_db/age_s/reasons/freq_hz so cfs's one-shot policy (loud-ish: >= -20 dBFS) "
             "can act; freq_hz within +-0.03 oct of 1934 Hz.", "FEEDBACK onset 3.0; tier B: MODERATE <= 250 ms (a cut is a bonus)", 9.0,
             tags=("feedback", "speech", "fast", "compressor", "tier_b", "verifier"))
def _av12(seed, st):
    sp = SpeechBursts(t_start=0.4, t_end=8.6, level_db=-32.0, seed=seed)
    ring = FeedbackRing(freq_hz=band_centre_hz(66, 25.0), excess_db=-9.0, excess_points=((0.0, -9.0), (3.0, -9.0), (3.02, 3.5)),
                        tau_loop_s=TAU_LOOP_TOPS_S, t_on=0.0, start_db=-72.0, sat_db=-19.0, wander_db=0.4, label="lectern_1k9_fast")
    return Scene(9.0, sources=[room_noise(), music_bed(-55.0, seed=seed), sp], rings=[ring], sat_coupling=1.0)


@adversarial("AV13_ultra_steady_limiter_howl_at_arm_watch", "AV13_ultra_steady_limiter_howl_at_arm_watch",
             "G3 frozen-line rule vs a legitimately steady howl: watch arms with an established 3.4 kHz howl at -15 dBFS held by a "
             "DIGITAL limiter with only +-0.06 dB of wander (still air, fixed install mic: A §4.3(v) puts air-movement wander at 0.2-0.5 dB "
             "but a brick-wall look-ahead limiter regulates far tighter) over a -62 bed: a tone 45 dB above the noise in its band moves the "
             "int16/256 code by << 1 LSB from noise alone (10 log10(1+1e-4.5) = 0.0001 dB), so consecutive frames CAN repeat bit-for-bit "
             "although the level is below clip-3 (the rule's only exemption). frozen_frames = 5 identical values -> 'frozen', no evidence "
             "for confirm_frames. Expected: caught at K1 (AT-ARM, >= arm_fast_level) or at worst arm_confirm_s; reports FROZEN_LINES if it trips.",
             "FEEDBACK from t=0; detect <= 300 ms (<= 1 s acceptable)", 8.0,
             tags=("feedback", "established", "limiter", "frozen", "verifier"), latency_budget_ms=1000.0)
def _av13(seed, st):
    ring = FeedbackRing(freq_hz=band_centre_hz(74, 18.0), excess_db=2.0, tau_loop_s=TAU_LOOP_TOPS_S, sat_db=-15.0, established=True,
                        wander_db=0.06, wander_hz=0.4, label="howl_3k4_steady")
    return Scene(8.0, sources=[room_noise(), music_bed(-62.0, lfo_db=0.8, seed=seed)], rings=[ring])


VERIFIER_BREAKERS = tuple(n for n in ADVERSARIAL if n.startswith("AV"))
assert len(VERIFIER_BREAKERS) == 13, len(VERIFIER_BREAKERS)
