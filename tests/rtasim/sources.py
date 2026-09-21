"""Programme generators and the feedback-loop model (test support).

Every source answers, for a scene time ``t`` (s):

* ``tones(t)  -> list[(f_hz, level_db)]``   narrow spectral lines (rendered through the filter skirts)
* ``noise(t)  -> list[(band, level_db)]``   noise-like per-band power (beds, cymbals, clicks)

Levels are dB at the RTA tap ("as displayed under PEAK once settled"). Programme sources are moved by the
scene's :class:`CommonModeGain`; a :class:`FeedbackRing` is NOT moved 1:1 — its *excess loop gain* moves
(loop brief §1.5, analyser brief §0.3) and its level then follows the loop dynamics.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable, Sequence

from .physics import (
    RTA_BANDS, RTA_BAND_HZ, TIMBRES, CLIP_HARMONICS, CLIP_KNEE_DB, TAU_LOOP_TOPS_S, regen_boost_db,
    harmonic_band_offset,
)

INACTIVE_DB = -200.0
_NOTE_INDEX = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def note_hz(name: str | float | int, cents: float = 0.0) -> float:
    """'A4' -> 440.0; 'Bb1', 'F#3'; a number is taken as Hz. ``cents`` detunes."""
    if isinstance(name, (int, float)):
        return float(name) * 2.0 ** (cents / 1200.0)
    s = name.strip()
    letter = s[0].upper()
    i = 1
    acc = 0
    while i < len(s) and s[i] in "#b":
        acc += 1 if s[i] == "#" else -1
        i += 1
    octave = int(s[i:])
    midi = 12 * (octave + 1) + _NOTE_INDEX[letter] + acc
    return 440.0 * 2.0 ** ((midi - 69) / 12.0 + cents / 1200.0)


def band_centre_hz(band: float, cents: float = 0.0) -> float:
    """Frequency of (possibly fractional) RTA band coordinate ``band`` plus ``cents``."""
    return RTA_BAND_HZ[0] * 2.0 ** (band / 10.0 + cents / 1200.0)


class Source:
    """Base class. ``t0``/``t1`` bound the active window (renderer skips inactive sources)."""

    t0: float = -1e9
    t1: float = 1e9
    label: str = "source"
    kind: str = "NOTE"          # ground-truth class: NOTE | TRANSIENT | BED | DRIVEN_RESONANCE | COMMON_MODE

    def tones(self, t: float) -> list[tuple[float, float]]:
        return []

    def noise(self, t: float) -> list[tuple[int, float]]:
        return []


# ---------------------------------------------------------------------------------------------
# harmonic note with ADSR, vibrato, glide, per-partial decay
# ---------------------------------------------------------------------------------------------
@dataclass
class HarmonicNote(Source):
    """A pitched note: partial k at k·f(t), level = level_db + timbre[k-1] + envelope − extra decay.

    Envelope (dB): attack: linear-in-dB rise over ``attack_s`` from ``level_db - attack_range_db``
    (attack_s = 0 -> acoustically instant; the analyser then imposes its own τ_a rise, analyser brief §0.1);
    while held the note sags at ``decay_db_per_s`` (plucked/struck 3..15 dB/s, bowed/blown/organ 0);
    partial k additionally decays at ``partial_decay_db_per_s·(k-1)`` (guitar: upper partials die 2-4x
    faster, S5); after ``t_on+dur`` it releases at ``release_db_per_s``.
    Vibrato: ``vib_rate_hz``, extent ±``vib_cents`` reached after ``vib_delay_s`` + ``vib_ramp_s``.
    Glide: starts ``glide_cents`` away and reaches the target pitch after ``glide_s`` (scoops, 808 drops).
    AM: ``am_db`` at ``am_hz`` (Leslie, chorus beating, shimmer).
    """

    f0_hz: float = 220.0
    t_on: float = 0.0
    dur: float = 1.0
    level_db: float = -30.0
    timbre: str | Sequence[float] = "voice"
    attack_s: float = 0.0
    attack_range_db: float = 40.0
    decay_db_per_s: float = 0.0
    partial_decay_db_per_s: float = 0.0
    release_db_per_s: float = 200.0
    vib_rate_hz: float = 0.0
    vib_cents: float = 0.0
    vib_delay_s: float = 0.2
    vib_ramp_s: float = 0.3
    vib_phase: float = 0.0
    glide_cents: float = 0.0
    glide_s: float = 0.0
    am_db: float = 0.0
    am_hz: float = 0.0
    am_phase: float = 0.0
    detune_cents: float = 0.0
    max_hz: float = 20000.0
    label: str = "note"
    kind: str = "NOTE"

    def __post_init__(self) -> None:
        self.partials = tuple(TIMBRES[self.timbre]) if isinstance(self.timbre, str) else tuple(self.timbre)
        self.t0 = self.t_on
        tail = 70.0 / max(1.0, self.release_db_per_s)
        self.t1 = self.t_on + self.dur + tail

    def pitch_hz(self, t: float) -> float:
        x = t - self.t_on
        cents = self.detune_cents
        if self.glide_s > 0.0 and x < self.glide_s:
            cents += self.glide_cents * (1.0 - x / self.glide_s)
        if self.vib_cents and self.vib_rate_hz:
            if x > self.vib_delay_s:
                depth = self.vib_cents * min(1.0, (x - self.vib_delay_s) / max(1e-6, self.vib_ramp_s))
                cents += depth * math.sin(2.0 * math.pi * self.vib_rate_hz * x + self.vib_phase)
        return self.f0_hz * 2.0 ** (cents / 1200.0)

    def envelope_db(self, t: float) -> float:
        x = t - self.t_on
        if x < 0.0:
            return INACTIVE_DB
        env = 0.0
        if self.attack_s > 0.0 and x < self.attack_s:
            env -= self.attack_range_db * (1.0 - x / self.attack_s)
        held = min(x, self.dur)
        env -= self.decay_db_per_s * held
        if x > self.dur:
            env -= self.release_db_per_s * (x - self.dur)
        if self.am_db and self.am_hz:
            env += self.am_db * math.sin(2.0 * math.pi * self.am_hz * x + self.am_phase)
        return env

    def tones(self, t: float) -> list[tuple[float, float]]:
        env = self.envelope_db(t)
        if env <= -120.0:
            return []
        f = self.pitch_hz(t)
        held = max(0.0, t - self.t_on)
        out = []
        pd = self.partial_decay_db_per_s
        base = self.level_db + env
        for k, rel in enumerate(self.partials, start=1):
            fk = f * k
            if fk > self.max_hz:
                break
            lv = base + rel - (pd * (k - 1) * held if pd else 0.0)
            if lv > -130.0:
                out.append((fk, lv))
        return out


# ---------------------------------------------------------------------------------------------
# decaying tonal burst (toms, kick thump, bells, whistles with wobble) and noise bursts
# ---------------------------------------------------------------------------------------------
@dataclass
class TonalBurst(Source):
    """A struck/decaying line: instant onset at ``level_db``, dB-linear decay, optional pitch glide
    (kick 62->50 Hz over 120 ms; toms -100..-300 c), optional inharmonic partners ``partials`` as
    (ratio, rel_db) pairs (bells/triangle 1:2.19:3.48, analyser brief §4.2), optional wobble."""

    f_hz: float = 60.0
    t_on: float = 0.0
    level_db: float = -30.0
    decay_db_per_s: float = 25.0
    dur: float = 1.0
    glide_cents: float = 0.0
    glide_s: float = 0.0
    partials: Sequence[tuple[float, float]] = ((1.0, 0.0),)
    wobble_cents: float = 0.0
    wobble_hz: float = 0.0
    label: str = "burst"
    kind: str = "TRANSIENT"

    def __post_init__(self) -> None:
        self.t0 = self.t_on
        self.t1 = self.t_on + self.dur

    def tones(self, t: float) -> list[tuple[float, float]]:
        x = t - self.t_on
        if x < 0.0 or x > self.dur:
            return []
        cents = 0.0
        if self.glide_s > 0.0 and x < self.glide_s:
            cents += self.glide_cents * (1.0 - x / self.glide_s)
        if self.wobble_cents:
            cents += self.wobble_cents * math.sin(2.0 * math.pi * self.wobble_hz * x)
        f = self.f_hz * 2.0 ** (cents / 1200.0)
        lv = self.level_db - self.decay_db_per_s * x
        return [(f * r, lv + rel) for r, rel in self.partials if f * r < 20000.0 and lv + rel > -130.0]


@dataclass
class NoiseBurst(Source):
    """Noise-like energy over RTA bands ``band_lo..band_hi`` (inclusive): rises in ``attack_s`` to
    ``level_db`` per band (+``tilt_db_per_band``·(b-band_lo)), holds ``hold_s``, decays at
    ``decay_db_per_s``. Cymbal crash: bands 58-97 +22 dB, 12 dB/s; hat: 75-95, 40 ms; kick click:
    68-78 one frame; snare wires 67-90 (analyser brief §4.2). Edges taper over ``edge_bands``."""

    band_lo: int = 60
    band_hi: int = 97
    t_on: float = 0.0
    level_db: float = -40.0
    attack_s: float = 0.0
    hold_s: float = 0.0
    decay_db_per_s: float = 12.0
    dur: float = 3.0
    tilt_db_per_band: float = 0.0
    edge_bands: int = 2
    label: str = "noise_burst"
    kind: str = "TRANSIENT"

    def __post_init__(self) -> None:
        self.t0 = self.t_on
        self.t1 = self.t_on + self.dur
        shape = []
        for b in range(self.band_lo, self.band_hi + 1):
            edge = min(b - self.band_lo, self.band_hi - b)
            taper = -6.0 * (self.edge_bands - edge) if edge < self.edge_bands else 0.0
            shape.append((b, self.tilt_db_per_band * (b - self.band_lo) + taper))
        self._shape = tuple(shape)

    def gain_db(self, t: float) -> float:
        x = t - self.t_on
        if x < 0.0 or x > self.dur:
            return INACTIVE_DB
        if self.attack_s > 0.0 and x < self.attack_s:
            return self.level_db - 40.0 * (1.0 - x / self.attack_s)
        x -= self.attack_s
        if x <= self.hold_s:
            return self.level_db
        return self.level_db - self.decay_db_per_s * (x - self.hold_s)

    def noise(self, t: float) -> list[tuple[int, float]]:
        g = self.gain_db(t)
        if g <= -130.0:
            return []
        return [(b, g + s) for b, s in self._shape]


@dataclass
class PinkBed(Source):
    """Broadband programme bed / room noise: per-band level = ``level_1k_db + tilt_db_per_oct·log2(f/1k)``
    inside ``f_lo..f_hi`` (12 dB/oct roll-off outside), optional slow dynamics LFO and an on/off
    envelope (song start/stop, applause swell: ``t_on``, ``t_off``, ``attack_s``, ``release_db_per_s``).
    Pink-ish programme at broadband L spreads to ≈ L−18.5 dB per band (analyser brief §1)."""

    level_1k_db: float = -50.0
    tilt_db_per_oct: float = -1.5
    f_lo: float = 30.0
    f_hi: float = 16000.0
    lfo_db: float = 0.0
    lfo_hz: float = 0.3
    t_on: float = -1e9
    t_off: float = 1e9
    attack_s: float = 0.0
    release_db_per_s: float = 60.0
    bumps: Sequence[tuple[float, float, float]] = ()   # (centre_band, height_db, sigma_bands) smooth humps
    label: str = "bed"
    kind: str = "BED"

    def __post_init__(self) -> None:
        lv = []
        for b, f in enumerate(RTA_BAND_HZ):
            x = self.level_1k_db + self.tilt_db_per_oct * math.log2(f / 1000.0)
            if f < self.f_lo:
                x -= 12.0 * math.log2(self.f_lo / f)
            elif f > self.f_hi:
                x -= 12.0 * math.log2(f / self.f_hi)
            for c, h, sg in self.bumps:
                x += h * math.exp(-((b - c) ** 2) / (2.0 * sg * sg))
            lv.append(x)
        self._levels = tuple(lv)
        self.t0 = self.t_on
        self.t1 = self.t_off + 130.0 / max(1.0, self.release_db_per_s)

    def gain_db(self, t: float) -> float:
        if t < self.t_on:
            return INACTIVE_DB
        g = 0.0
        if self.attack_s > 0.0 and t - self.t_on < self.attack_s:
            g -= 40.0 * (1.0 - (t - self.t_on) / self.attack_s)
        if t > self.t_off:
            g -= self.release_db_per_s * (t - self.t_off)
        if self.lfo_db:
            g += self.lfo_db * math.sin(2.0 * math.pi * self.lfo_hz * t)
        return g

    def noise(self, t: float) -> list[tuple[int, float]]:
        g = self.gain_db(t)
        if g <= -130.0:
            return []
        return [(b, lv + g) for b, lv in enumerate(self._levels)]


# ---------------------------------------------------------------------------------------------
# drums
# ---------------------------------------------------------------------------------------------
class DrumHit(Source):
    """kick | snare | hat | crash | ride as a bundle of TonalBurst + NoiseBurst (analyser brief §4.2)."""

    kind = "TRANSIENT"

    def __init__(self, what: str, t: float, level_db: float = -30.0, *, tune_hz: float | None = None) -> None:
        self.what = what
        self.label = what
        self.parts: list[Source] = []
        if what == "kick":
            f = tune_hz or 62.0
            # thump 62 Hz gliding down ~370 c over 120 ms, 25 dB/s; H2 -20; beater click 2-5 kHz one frame
            self.parts.append(TonalBurst(f_hz=f * 2 ** (-370 / 1200), t_on=t, level_db=level_db, decay_db_per_s=25.0,
                                         dur=0.5, glide_cents=370.0, glide_s=0.12,
                                         partials=((1.0, 0.0), (2.0, -20.0)), label="kick"))
            self.parts.append(NoiseBurst(band_lo=66, band_hi=80, t_on=t, level_db=level_db - 18.0, hold_s=0.02,
                                         decay_db_per_s=400.0, dur=0.12, label="kick_click"))
        elif what == "snare":
            f = tune_hz or 190.0
            self.parts.append(TonalBurst(f_hz=f, t_on=t, level_db=level_db - 4.0, decay_db_per_s=60.0, dur=0.25,
                                         glide_cents=120.0, glide_s=0.05, partials=((1.0, 0.0), (1.6, -6.0)), label="snare"))
            self.parts.append(NoiseBurst(band_lo=62, band_hi=92, t_on=t, level_db=level_db - 14.0, hold_s=0.03,
                                         decay_db_per_s=90.0, dur=0.4, label="snare_wires"))
        elif what == "hat":
            self.parts.append(NoiseBurst(band_lo=74, band_hi=96, t_on=t, level_db=level_db - 12.0, hold_s=0.01,
                                         decay_db_per_s=250.0, dur=0.12, label="hat"))
        elif what == "crash":
            self.parts.append(NoiseBurst(band_lo=58, band_hi=97, t_on=t, level_db=level_db, hold_s=0.05,
                                         decay_db_per_s=12.0, dur=3.0, label="crash"))
        elif what == "ride":
            self.parts.append(NoiseBurst(band_lo=62, band_hi=96, t_on=t, level_db=level_db - 16.0, hold_s=0.02,
                                         decay_db_per_s=20.0, dur=1.5, label="ride_wash"))
            self.parts.append(TonalBurst(f_hz=2900.0, t_on=t, level_db=level_db - 6.0, decay_db_per_s=15.0, dur=1.5,
                                         partials=((1.0, 0.0), (1.47, -3.0), (1.71, -5.0)), label="ride_bell"))
        else:
            raise ValueError(f"unknown drum {what!r}")
        self.t0 = min(p.t0 for p in self.parts)
        self.t1 = max(p.t1 for p in self.parts)

    def tones(self, t: float) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for p in self.parts:
            if p.t0 <= t <= p.t1:
                out.extend(p.tones(t))
        return out

    def noise(self, t: float) -> list[tuple[int, float]]:
        out: list[tuple[int, float]] = []
        for p in self.parts:
            if p.t0 <= t <= p.t1:
                out.extend(p.noise(t))
        return out


class Group(Source):
    """A bag of child sources (sequencer output). Only children active at ``t`` are evaluated."""

    def __init__(self, children: Sequence[Source], label: str = "group", kind: str = "NOTE") -> None:
        self.children = sorted(children, key=lambda c: c.t0)
        self.label = label
        self.kind = kind
        self.t0 = min((c.t0 for c in self.children), default=0.0)
        self.t1 = max((c.t1 for c in self.children), default=0.0)

    def _active(self, t: float):
        for c in self.children:
            if c.t0 > t:
                break
            if t <= c.t1:
                yield c

    def tones(self, t: float) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for c in self._active(t):
            out.extend(c.tones(t))
        return out

    def noise(self, t: float) -> list[tuple[int, float]]:
        out: list[tuple[int, float]] = []
        for c in self._active(t):
            out.extend(c.noise(t))
        return out


def DrumPattern(*, t_start: float, t_end: float, bpm: float = 124.0, pattern: str = "four_on_floor",
                level_db: float = -30.0, seed: int = 1, kick_hz: float = 62.0, humanize_s: float = 0.004) -> Group:
    """Deterministic drum sequencer. Patterns: 'four_on_floor' (kick every beat, hat 8ths, snare 2&4),
    'kick_only', 'hats_only', 'rock' (kick 1&3, snare 2&4, hats, crash every 8 bars)."""
    rng = random.Random(seed * 31 + 7)
    beat = 60.0 / bpm
    hits: list[Source] = []
    t = t_start
    i = 0
    while t < t_end:
        h = rng.uniform(-humanize_s, humanize_s)
        if pattern in ("four_on_floor", "kick_only") or (pattern == "rock" and i % 4 in (0, 2)):
            hits.append(DrumHit("kick", t + h, level_db, tune_hz=kick_hz))
        if pattern in ("four_on_floor", "rock") and i % 4 in (1, 3):
            hits.append(DrumHit("snare", t + h, level_db - 2.0))
        if pattern in ("four_on_floor", "rock", "hats_only"):
            hits.append(DrumHit("hat", t + h, level_db - 4.0))
            hits.append(DrumHit("hat", t + beat / 2 + h, level_db - 8.0))
        if pattern == "rock" and i % 32 == 0:
            hits.append(DrumHit("crash", t + h, level_db - 6.0))
        t += beat
        i += 1
    return Group(hits, label=f"drums_{pattern}", kind="TRANSIENT")


# ---------------------------------------------------------------------------------------------
# sequencers
# ---------------------------------------------------------------------------------------------
def BassLine(*, notes: Sequence[str | float], t_start: float, t_end: float, note_s: float = 0.6,
             gap_s: float = 0.15, level_db: float = -38.0, timbre: str = "bass_gtr", seed: int = 1,
             decay_db_per_s: float = 5.0, attack_s: float = 0.0, humanize_s: float = 0.01, **note_kw) -> Group:
    """Cycle through ``notes`` (names or Hz) from t_start to t_end; instant acoustic onsets by default
    (the analyser makes them slow at LF), sag while held (analyser brief §4.2 bass)."""
    rng = random.Random(seed * 17 + 3)
    out: list[Source] = []
    t = t_start
    i = 0
    while t < t_end - 0.05:
        f = note_hz(notes[i % len(notes)])
        out.append(HarmonicNote(f0_hz=f, t_on=t + rng.uniform(-humanize_s, humanize_s), dur=note_s,
                                level_db=level_db + rng.uniform(-1.5, 1.5), timbre=timbre, attack_s=attack_s,
                                decay_db_per_s=decay_db_per_s, release_db_per_s=120.0, label="bass", **note_kw))
        t += note_s + gap_s
        i += 1
    return Group(out, label="bassline")


_SCALE = (0, 2, 4, 5, 7, 9, 11)


def Melody(*, t_start: float, t_end: float, low: str = "C4", high: str = "C6", note_s: tuple[float, float] = (0.3, 1.0),
           gap_s: tuple[float, float] = (0.02, 0.15), level_db: float = -30.0, timbre: str = "voice", seed: int = 1,
           vib_rate_hz: float = 5.5, vib_cents: float = 40.0, legato: bool = False, **note_kw) -> Group:
    """Random diatonic melody (C major) between ``low`` and ``high``, deterministic from ``seed``.
    Steps are mostly ±1..2 scale degrees (music 'moves', analyser brief §4.3 ii)."""
    rng = random.Random(seed * 101 + 11)
    lo_hz, hi_hz = note_hz(low), note_hz(high)
    lo_m = int(round(69 + 12 * math.log2(lo_hz / 440.0)))
    hi_m = int(round(69 + 12 * math.log2(hi_hz / 440.0)))
    degrees = [m for m in range(lo_m, hi_m + 1) if (m % 12) in _SCALE]
    idx = rng.randrange(len(degrees))
    out: list[Source] = []
    t = t_start
    while t < t_end - 0.1:
        d = rng.uniform(*note_s)
        m = degrees[idx]
        f = 440.0 * 2.0 ** ((m - 69) / 12.0)
        out.append(HarmonicNote(f0_hz=f, t_on=t, dur=d, level_db=level_db + rng.uniform(-2.0, 2.0), timbre=timbre,
                                vib_rate_hz=vib_rate_hz, vib_cents=vib_cents, vib_phase=rng.uniform(0, 6.28),
                                release_db_per_s=80.0 if legato else 150.0, label="melody", **note_kw))
        t += d + (0.0 if legato else rng.uniform(*gap_s))
        idx = min(len(degrees) - 1, max(0, idx + rng.choice((-2, -1, -1, 1, 1, 2, 3, -3))))
    return Group(out, label="melody")


def ChordPad(*, chords: Sequence[Sequence[str | float]], t_start: float, t_end: float, chord_s: float = 2.0,
             level_db: float = -34.0, timbre: str = "organ_8_4", attack_s: float = 0.0, release_db_per_s: float = 60.0,
             chorus_db: float = 0.0, chorus_hz: float = 1.2, seed: int = 1, **note_kw) -> Group:
    """Block chords changing every ``chord_s`` (chord tones end with the chord — a ring does not, S12)."""
    rng = random.Random(seed * 53 + 5)
    out: list[Source] = []
    t = t_start
    i = 0
    while t < t_end - 0.05:
        for nm in chords[i % len(chords)]:
            out.append(HarmonicNote(f0_hz=note_hz(nm), t_on=t, dur=min(chord_s, t_end - t), level_db=level_db,
                                    timbre=timbre, attack_s=attack_s, release_db_per_s=release_db_per_s,
                                    am_db=chorus_db, am_hz=chorus_hz * rng.uniform(0.8, 1.25),
                                    am_phase=rng.uniform(0, 6.28), label="chord", **note_kw))
        t += chord_s
        i += 1
    return Group(out, label="chords")


def SpeechBursts(*, t_start: float, t_end: float, level_db: float = -32.0, seed: int = 1,
                 f0_range: tuple[float, float] = (110.0, 140.0)) -> Group:
    """Speech-like syllables: 150-400 ms voiced bursts, 100-300 ms gaps, F0 110-140 Hz with a falling
    contour, formant-ish partial table 'speech' (analyser brief S22)."""
    rng = random.Random(seed * 71 + 9)
    out: list[Source] = []
    t = t_start
    while t < t_end - 0.2:
        d = rng.uniform(0.15, 0.4)
        f = rng.uniform(*f0_range)
        out.append(HarmonicNote(f0_hz=f, t_on=t, dur=d, level_db=level_db + rng.uniform(-3, 2), timbre="speech",
                                attack_s=0.03, glide_cents=rng.uniform(80, 200), glide_s=d, release_db_per_s=300.0,
                                vib_rate_hz=0.0, label="speech"))
        t += d + rng.uniform(0.1, 0.3)
    return Group(out, label="speech")


# ---------------------------------------------------------------------------------------------
# common-mode gain (master / channel fader moves, ring_out steps, autogain drift)
# ---------------------------------------------------------------------------------------------
@dataclass
class CommonModeGain:
    """Piecewise-linear gain (dB) vs time from ``points`` [(t, gain_db), ...] (a step = two points at the
    same t). :meth:`ring_out_steps` builds the server's +step_db every dwell_s staircase (cfs.py ring_out).
    The scene applies ``gain·prog_coupling`` to programme and ``gain·loop_coupling`` to every ring's excess
    (loop brief §0: at the pre-fader tap a *bus-master* move has prog_coupling≈0..1, loop 1)."""

    points: Sequence[tuple[float, float]] = ((0.0, 0.0),)
    label: str = "master"
    kind: str = "COMMON_MODE"

    def __post_init__(self) -> None:
        self.points = sorted(self.points, key=lambda p: p[0])

    def gain_db(self, t: float) -> float:
        pts = self.points
        if t <= pts[0][0]:
            return pts[0][1]
        prev = pts[0]
        for p in pts[1:]:
            if t < p[0]:
                if p[0] == prev[0]:
                    return p[1]
                return prev[1] + (p[1] - prev[1]) * (t - prev[0]) / (p[0] - prev[0])
            prev = p
        return prev[1]

    def step_times(self) -> list[float]:
        """Times at which the gain jumps (for COMMON_MODE ground truth)."""
        out = []
        for a, b in zip(self.points, self.points[1:]):
            if b[0] == a[0] and b[1] != a[1]:
                out.append(b[0])
        return out

    @classmethod
    def ring_out_steps(cls, *, t_first: float, dwell_s: float = 1.5, step_db: float = 1.0, n_steps: int = 8,
                       start_db: float = 0.0) -> "CommonModeGain":
        pts = [(0.0, start_db)]
        g = start_db
        for k in range(n_steps):
            t = t_first + k * dwell_s
            pts.append((t, g))
            g += step_db
            pts.append((t, g))
        return cls(points=tuple(pts), label="ring_out_steps")

    @classmethod
    def ramp(cls, *, t0: float, t1: float, delta_db: float, then: Sequence[tuple[float, float]] = ()) -> "CommonModeGain":
        pts = [(0.0, 0.0), (t0, 0.0), (t1, delta_db)] + [(t, delta_db + d) for t, d in then]
        return cls(points=tuple(pts), label="fader_ramp")


# ---------------------------------------------------------------------------------------------
# the electro-acoustic loop
# ---------------------------------------------------------------------------------------------
@dataclass
class FeedbackRing:
    """One regenerative loop candidate (loop brief §1.3-1.5, §2.2-2.3; analyser brief §0.2-0.3).

    ``excess_db`` is the open-loop gain above threshold (dB) from ``t_on`` (``excess_points`` overrides it
    with a piecewise-linear schedule, e.g. a performer walking into a wedge). The scene adds the common-mode
    gain × loop coupling and the (negative) GEQ gain at the ring frequency. Then, per substep:

    * e_eff > 0: the line grows at e_eff/τ_loop dB/s (200 dB/s per dB on a 5 ms wedge loop, 100 on 10 ms
      tops, 14 on a 70 ms reverberant loop) up to ``sat_db`` (limiter / compressor plateau — the M7
      "60 dB-prominent line at a fixed level for 15 s"); ``sat_db >= 0`` pins it at 0 dBFS (RTA clip flag)
      and grows hard-clip partials (H3 −12, H5 −18, H2 −30 once within ``clip_knee_db`` of full scale).
    * e_eff <= 0: the line relaxes at max(|e_eff|, 0.5)/τ_loop dB/s toward the regenerative floor
      = excitation + 1/(1−g) boost (+6 dB @ −6, +10.7 @ −3, +19 @ −1): 'ringing tails' on programme, and
      the super-linear response to a +1 dB step that makes ring_out's staircase a free probe.
    * ``established``: at ``sat_db`` from before frame 0 (no onset is ever observed, S2).
    * ``wander_db``/``wander_hz``: slow level wander (air movement; a real ring is not dead flat).
    * ``hop_at_s``/``hop_cents``: mode hop (hand-held mic moved: neighbouring candidate takes over).
    * excitation: ``excitation_db`` constant floor, plus, if ``excite_from_programme``, the loudest
      programme line/bed within ±``excite_bw_oct`` of f (coupled at ``excite_coupling_db``).
    """

    freq_hz: float = 3150.0
    excess_db: float = 0.5
    tau_loop_s: float = TAU_LOOP_TOPS_S
    t_on: float = 0.0
    start_db: float = -80.0
    sat_db: float = -6.0
    established: bool = False
    excess_points: Sequence[tuple[float, float]] | None = None
    excitation_db: float = -90.0
    excite_from_programme: bool = True
    excite_coupling_db: float = -6.0
    excite_bw_oct: float = 0.1
    wander_db: float = 0.3
    wander_hz: float = 0.7
    wander_phase: float = 0.0
    hop_at_s: float | None = None
    hop_cents: float = 0.0
    clip_harmonics: Sequence[tuple[int, float]] = CLIP_HARMONICS
    clip_knee_db: float = CLIP_KNEE_DB
    growth_cap_db_per_s: float = 2000.0
    label: str = "ring"
    kind: str = "FEEDBACK"

    def __post_init__(self) -> None:
        self.reset()

    # -- state ---------------------------------------------------------------------------------
    def reset(self) -> None:
        self.level_db: float = self.sat_db if self.established else self.start_db
        self.e_eff: float = self.excess_db
        self.t: float = -1e9

    def base_excess_db(self, t: float) -> float:
        if self.excess_points:
            pts = self.excess_points
            if t <= pts[0][0]:
                return pts[0][1]
            prev = pts[0]
            for p in pts[1:]:
                if t < p[0]:
                    if p[0] == prev[0]:
                        return p[1]
                    return prev[1] + (p[1] - prev[1]) * (t - prev[0]) / (p[0] - prev[0])
                prev = p
            return prev[1]
        return self.excess_db

    def current_hz(self, t: float) -> float:
        f = self.freq_hz
        if self.hop_at_s is not None and t >= self.hop_at_s:
            f *= 2.0 ** (self.hop_cents / 1200.0)
        return f

    def active(self, t: float) -> bool:
        return self.established or t >= self.t_on

    def step(self, t: float, dt: float, *, common_db: float, geq_gain_db: float, excitation_db: float) -> None:
        """Advance the loop by ``dt`` to time ``t``. ``common_db`` = common-mode gain × loop coupling,
        ``geq_gain_db`` = (negative) GEQ gain at the ring frequency, ``excitation_db`` = programme level
        near f at the tap (already coupled) or -inf."""
        self.t = t
        if not self.active(t):
            self.e_eff = -60.0
            self.level_db = self.start_db
            return
        e = self.base_excess_db(t) + common_db + geq_gain_db
        self.e_eff = e
        exc = max(self.excitation_db, excitation_db)
        L = self.level_db
        if e > 0.0:
            rate = min(self.growth_cap_db_per_s, e / self.tau_loop_s)
            L = min(self.sat_db, L + rate * dt)
            floor = exc  # the loop output is at least the excitation passing through it
            if L < floor:
                L = min(self.sat_db, floor)
        else:
            target = min(self.sat_db, exc + regen_boost_db(e))
            rate = max(abs(e), 0.5) / self.tau_loop_s
            if L > target:
                L = max(target, L - rate * dt)
            else:
                L = min(target, L + rate * dt)
        self.level_db = max(-160.0, L)

    def display_level_db(self, t: float) -> float:
        wob = self.wander_db * math.sin(2.0 * math.pi * self.wander_hz * t + self.wander_phase) if self.wander_db else 0.0
        return min(0.0 if self.sat_db >= 0.0 else self.sat_db + abs(self.wander_db), self.level_db + wob)

    def tones(self, t: float) -> list[tuple[float, float]]:
        if not self.active(t) or self.level_db <= -130.0:
            return []
        f = self.current_hz(t)
        L = self.display_level_db(t)
        out = [(f, L)]
        if self.sat_db >= -0.5 and L > self.clip_knee_db:
            # distortion products grow ~2-3 dB per dB as the fundamental approaches full scale
            ramp = min(1.0, (L - self.clip_knee_db) / max(1e-6, -self.clip_knee_db))
            for k, rel in self.clip_harmonics:
                fk = f * k
                if fk < 20000.0:
                    out.append((fk, L + rel - 20.0 * (1.0 - ramp)))
        return out


# ---------------------------------------------------------------------------------------------
# driven room mode (analyser brief §6, S19)
# ---------------------------------------------------------------------------------------------
class DrivenResonance(Source):
    """A fixed-frequency acoustic resonance (room axial mode, Q 10-30) *driven* by programme: whenever a
    driver line is within ``bw_bands`` of ``f_hz`` the mode re-radiates at that line's level + ``gain_db``
    (minus detuning), building up with τ = Q/(π f) and decaying at 60/T60 dB/s when the drive stops.
    It is programme (tracks the master 1:1) — ground-truth class DRIVEN_RESONANCE, never to be cut."""

    kind = "DRIVEN_RESONANCE"

    def __init__(self, f_hz: float, drivers: Sequence[Source], *, gain_db: float = 10.0, q: float = 20.0,
                 t60_s: float = 1.2, bw_bands: float = 1.5, label: str = "room_mode") -> None:
        self.f_hz = f_hz
        self.drivers = list(drivers)
        self.gain_db = gain_db
        self.tau_s = q / (math.pi * f_hz)
        self.fall_db_per_s = 60.0 / t60_s
        self.bw_bands = bw_bands
        self.label = label
        self.t0 = min(d.t0 for d in self.drivers)
        self.t1 = max(d.t1 for d in self.drivers) + 3.0
        self._lv = -160.0
        self._t = None

    def reset(self) -> None:
        self._lv = -160.0
        self._t = None

    def _drive_db(self, t: float) -> float:
        best = -160.0
        lf = math.log2(self.f_hz)
        for d in self.drivers:
            if d.t0 <= t <= d.t1:
                for f, lv in d.tones(t):
                    db = abs(math.log2(f) - lf) * 10.0
                    if db < self.bw_bands:
                        x = lv + self.gain_db - 12.0 * (db / self.bw_bands) ** 2
                        if x > best:
                            best = x
        return best

    def tones(self, t: float) -> list[tuple[float, float]]:
        dt = 0.0 if self._t is None else max(0.0, t - self._t)
        self._t = t
        target = self._drive_db(t)
        lv = self._lv
        if target > lv:
            a = 1.0 - math.exp(-dt / self.tau_s) if dt > 0 else 1.0
            # power-domain approach to the driven level
            p = 10 ** (lv / 10.0) + (10 ** (target / 10.0) - 10 ** (lv / 10.0)) * a
            lv = 10.0 * math.log10(max(1e-16, p))
        else:
            lv = max(target, lv - self.fall_db_per_s * dt)
        self._lv = lv
        return [(self.f_hz, lv)] if lv > -130.0 else []


__all__ = [
    "Source", "HarmonicNote", "TonalBurst", "NoiseBurst", "PinkBed", "DrumHit", "DrumPattern", "Group", "BassLine",
    "Melody", "ChordPad", "SpeechBursts", "CommonModeGain", "FeedbackRing", "DrivenResonance", "note_hz",
    "band_centre_hz", "TIMBRES", "harmonic_band_offset",
]
