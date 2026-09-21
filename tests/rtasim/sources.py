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
    RTA_BANDS, RTA_BAND_HZ, TIMBRES, CLIP_HARMONICS, CLIP_KNEE_DB, TAU_LOOP_TOPS_S, BAND_REL_BW, regen_boost_db,
    comb_gain_lin, comb_band_mean_lin, harmonic_band_offset,
)

INACTIVE_DB = -200.0


class Wobble:
    """Deterministic, smooth, NON-periodic modulation in [-1, 1]: a sum of ``n`` sinusoids at log-spaced random
    rates in [f_lo, f_hi] Hz with random phases (seeded). Used for pitch drift, level flutter, loop-gain wander —
    everything that in the first version of the simulator was either absent (notes dead flat, dead in tune) or a
    single pure sinusoid (ring wander at exactly wander_hz: a template a detector could lock onto)."""

    __slots__ = ("terms",)

    def __init__(self, seed: int, n: int = 4, f_lo: float = 0.1, f_hi: float = 2.0) -> None:
        rng = random.Random(int(seed) * 2654435761 % (2 ** 32) + 97)
        terms = []
        for i in range(n):
            f = f_lo * (f_hi / f_lo) ** ((i + rng.random()) / n)
            terms.append((2.0 * math.pi * f, rng.uniform(0.0, 2.0 * math.pi), rng.uniform(0.6, 1.4)))
        norm = sum(a for _, _, a in terms) or 1.0
        self.terms = tuple((w, ph, a / norm) for w, ph, a in terms)

    def __call__(self, t: float) -> float:
        return sum(a * math.sin(w * t + ph) for w, ph, a in self.terms)


_ZERO_WOBBLE = lambda t: 0.0  # noqa: E731
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
    AM: ``am_db`` at ``am_hz`` (Leslie, chorus beating, tremolo — genuinely periodic modulations).
    Human/analogue imperfection (seeded from ``seed``, smooth and non-periodic, see :class:`Wobble`):
    ``drift_cents`` slow intonation drift (voice 5-15 c, whistle 20-40 c, fretted/keyed ~0-3 c),
    ``flutter_db`` level flutter (voice/wind 0.5-1.5 dB, organ/synth ~0.1), ``timbre_jitter_db`` a fixed
    per-note random offset of each partial k>=2 (vowel / pluck position / register: partial tables are not a
    fingerprint). All default to 0 so exact-arithmetic tests keep working; the sequencers set realistic values.
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
    drift_cents: float = 0.0
    drift_hz: tuple[float, float] = (0.15, 1.5)
    flutter_db: float = 0.0
    flutter_hz: tuple[float, float] = (0.3, 4.0)
    timbre_jitter_db: float = 0.0
    seed: int = 0
    label: str = "note"
    kind: str = "NOTE"

    def __post_init__(self) -> None:
        base = tuple(TIMBRES[self.timbre]) if isinstance(self.timbre, str) else tuple(self.timbre)
        if self.timbre_jitter_db:
            rng = random.Random(int(self.seed) * 7907 + 5)
            base = (base[0],) + tuple(x + rng.uniform(-self.timbre_jitter_db, self.timbre_jitter_db) for x in base[1:])
        self.partials = base
        self._drift = Wobble(self.seed * 3 + 1, 4, *self.drift_hz) if self.drift_cents else _ZERO_WOBBLE
        self._flutter = Wobble(self.seed * 3 + 2, 5, *self.flutter_hz) if self.flutter_db else _ZERO_WOBBLE
        self.t0 = self.t_on
        tail = 70.0 / max(1.0, self.release_db_per_s)
        self.t1 = self.t_on + self.dur + tail

    def pitch_hz(self, t: float) -> float:
        x = t - self.t_on
        cents = self.detune_cents + (self.drift_cents * self._drift(t) if self.drift_cents else 0.0)
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
        if self.flutter_db:
            env += self.flutter_db * self._flutter(t)
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
    random_humps: int = 0            # add this many seeded random humps (±hump_db, sigma 2-6 bands): a mix residue
    hump_db: float = 4.0             # is not a smooth pink line, and its shape differs per song (seed)
    swell: Sequence[tuple[float, float]] = ()          # extra piecewise-linear gain envelope [(t, dB), ...]
    region_mod_db: float = 0.0       # independent slow (0.2-1.5 Hz) level modulation of ~1-octave regions:
                                     # spectral flux of a mix (LF, low-mid, presence, air do not move together)
    seed: int = 0
    label: str = "bed"
    kind: str = "BED"

    def __post_init__(self) -> None:
        rng = random.Random(int(self.seed) * 1543 + 11)
        bumps = list(self.bumps)
        for _ in range(int(self.random_humps)):
            bumps.append((rng.uniform(8.0, 95.0), rng.uniform(-self.hump_db, self.hump_db), rng.uniform(2.0, 6.0)))
        tilt = self.tilt_db_per_oct + (rng.uniform(-0.5, 0.5) if self.random_humps else 0.0)
        lv = []
        for b, f in enumerate(RTA_BAND_HZ):
            x = self.level_1k_db + tilt * math.log2(f / 1000.0)
            if f < self.f_lo:
                x -= 12.0 * math.log2(self.f_lo / f)
            elif f > self.f_hi:
                x -= 12.0 * math.log2(f / self.f_hi)
            for c, h, sg in bumps:
                x += h * math.exp(-((b - c) ** 2) / (2.0 * sg * sg))
            lv.append(x)
        self._levels = tuple(lv)
        self._lfo = Wobble(self.seed * 5 + 3, 4, min(0.15, self.lfo_hz), max(0.9, 2.5 * self.lfo_hz)) if self.lfo_db else _ZERO_WOBBLE
        self._regions: list[tuple[int, int, Wobble]] = []
        if self.region_mod_db:
            b = 0
            i = 0
            while b < RTA_BANDS:
                w = rng.randint(8, 14)
                self._regions.append((b, min(RTA_BANDS, b + w), Wobble(self.seed * 41 + i, 3, 0.2, 1.5)))
                b += w
                i += 1
        self._swell = sorted(self.swell)
        self.t0 = self.t_on
        self.t1 = self.t_off + 130.0 / max(1.0, self.release_db_per_s)

    def _swell_db(self, t: float) -> float:
        pts = self._swell
        if not pts:
            return 0.0
        if t <= pts[0][0]:
            return pts[0][1]
        prev = pts[0]
        for p in pts[1:]:
            if t < p[0]:
                return prev[1] + (p[1] - prev[1]) * (t - prev[0]) / max(1e-9, p[0] - prev[0])
            prev = p
        return prev[1]

    def gain_db(self, t: float) -> float:
        if t < self.t_on:
            return INACTIVE_DB
        g = 0.0
        if self.attack_s > 0.0 and t - self.t_on < self.attack_s:
            g -= 40.0 * (1.0 - (t - self.t_on) / self.attack_s)
        if t > self.t_off:
            g -= self.release_db_per_s * (t - self.t_off)
        if self.lfo_db:
            g += self.lfo_db * self._lfo(t)
        if self._swell:
            g += self._swell_db(t)
        return g

    def noise(self, t: float) -> list[tuple[int, float]]:
        g = self.gain_db(t)
        if g <= -130.0:
            return []
        if not self._regions:
            return [(b, lv + g) for b, lv in enumerate(self._levels)]
        out = []
        lv = self._levels
        rm = self.region_mod_db
        for lo, hi, wob in self._regions:
            m = g + rm * wob(t)
            for b in range(lo, hi):
                out.append((b, lv[b] + m))
        return out


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
             decay_db_per_s: float = 5.0, attack_s: float = 0.0, humanize_s: float = 0.01,
             drift_cents: float = 3.0, flutter_db: float = 0.3, timbre_jitter_db: float = 3.0, **note_kw) -> Group:
    """Cycle through ``notes`` (names or Hz) from t_start to t_end; instant acoustic onsets by default
    (the analyser makes them slow at LF), sag while held (analyser brief §4.2 bass). Each note gets its own
    small intonation drift, level flutter and partial-balance jitter (pluck position / string)."""
    rng = random.Random(seed * 17 + 3)
    out: list[Source] = []
    t = t_start
    i = 0
    while t < t_end - 0.05:
        f = note_hz(notes[i % len(notes)])
        out.append(HarmonicNote(f0_hz=f, t_on=t + rng.uniform(-humanize_s, humanize_s), dur=note_s,
                                level_db=level_db + rng.uniform(-1.5, 1.5), timbre=timbre, attack_s=attack_s,
                                decay_db_per_s=decay_db_per_s, release_db_per_s=120.0, label="bass",
                                drift_cents=drift_cents, flutter_db=flutter_db, timbre_jitter_db=timbre_jitter_db,
                                seed=rng.randrange(1 << 30), **note_kw))
        t += note_s + gap_s
        i += 1
    return Group(out, label="bassline")


_SCALE = (0, 2, 4, 5, 7, 9, 11)


# per-timbre "humanisation" defaults for Melody: (drift_cents, flutter_db, expressive_db, timbre_jitter_db)
HUMANIZE: dict[str, tuple[float, float, float, float]] = {
    "voice": (8.0, 1.2, 1.0, 3.0), "voice_closed": (10.0, 1.5, 1.0, 4.0), "flute": (10.0, 1.0, 1.0, 3.0),
    "flute_high": (10.0, 1.0, 1.0, 3.0), "whistle": (25.0, 1.5, 1.0, 2.0), "organ_flue": (0.0, 0.15, 0.0, 3.0),
    "organ_8_4": (0.0, 0.15, 0.0, 2.0), "piano": (0.0, 0.2, 0.0, 2.0), "sine_lead": (0.0, 0.15, 0.3, 1.0),
    "el_guitar": (4.0, 0.5, 0.5, 3.0), "ac_guitar": (3.0, 0.5, 0.5, 3.0),
}
_HUMANIZE_DEFAULT = (5.0, 0.8, 0.5, 3.0)


def Melody(*, t_start: float, t_end: float, low: str = "C4", high: str = "C6", note_s: tuple[float, float] = (0.3, 1.0),
           gap_s: tuple[float, float] = (0.02, 0.15), level_db: float = -30.0, timbre: str = "voice", seed: int = 1,
           vib_rate_hz: float = 5.5, vib_cents: float = 40.0, legato: bool = False, drift_cents: float | None = None,
           expressive_db: float | None = None, flutter_db: float | None = None, timbre_jitter_db: float | None = None,
           vib_rate_spread: float = 0.12, repeat_prob: float = 0.0, **note_kw) -> Group:
    """Random diatonic melody (C major) between ``low`` and ``high``, deterministic from ``seed``.
    Steps are mostly ±1..2 scale degrees (music 'moves', analyser brief §4.3 ii); ``repeat_prob`` re-strikes the
    same pitch (a repeated note is NOT evidence of a fixed-frequency ring). Per note: own drift/flutter/partial
    jitter and a slight swell or sag (``expressive_db``), vibrato rate varied ±``vib_rate_spread`` (singers are not
    quartz-locked at 5.500 Hz). Unset humanisation parameters come from HUMANIZE[timbre] (organ/piano: no drift,
    no expression, 0.15-0.2 dB flutter; voice 8 c / 1.2 dB / ±1 dB; whistle 25 c / 1.5 dB)."""
    rng = random.Random(seed * 101 + 11)
    hd, hf, he, hj = HUMANIZE.get(timbre, _HUMANIZE_DEFAULT) if isinstance(timbre, str) else _HUMANIZE_DEFAULT
    drift_cents = hd if drift_cents is None else drift_cents
    flutter_db = hf if flutter_db is None else flutter_db
    expressive_db = he if expressive_db is None else expressive_db
    timbre_jitter_db = hj if timbre_jitter_db is None else timbre_jitter_db
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
        kw = dict(release_db_per_s=80.0 if legato else 150.0, label="melody")
        if expressive_db and "decay_db_per_s" not in note_kw:
            kw["decay_db_per_s"] = rng.uniform(-expressive_db, expressive_db) / max(0.3, d)   # slight swell or sag per note
        kw.update(note_kw)
        out.append(HarmonicNote(f0_hz=f, t_on=t, dur=d, level_db=level_db + rng.uniform(-2.0, 2.0), timbre=timbre,
                                vib_rate_hz=vib_rate_hz * rng.uniform(1.0 - vib_rate_spread, 1.0 + vib_rate_spread),
                                vib_cents=vib_cents * rng.uniform(0.8, 1.2) if vib_cents else 0.0, vib_phase=rng.uniform(0, 6.28),
                                drift_cents=drift_cents, flutter_db=flutter_db, timbre_jitter_db=timbre_jitter_db,
                                seed=rng.randrange(1 << 30), **kw))
        t += d + (0.0 if legato else rng.uniform(*gap_s))
        if repeat_prob and rng.random() < repeat_prob:
            continue
        idx = min(len(degrees) - 1, max(0, idx + rng.choice((-2, -1, -1, 1, 1, 2, 3, -3))))
    return Group(out, label="melody")


def ChordPad(*, chords: Sequence[Sequence[str | float]], t_start: float, t_end: float, chord_s: float = 2.0,
             level_db: float = -34.0, timbre: str = "organ_8_4", attack_s: float = 0.0, release_db_per_s: float = 60.0,
             chorus_db: float = 0.0, chorus_hz: float = 1.2, seed: int = 1, drift_cents: float = 0.0,
             flutter_db: float = 0.15, timbre_jitter_db: float = 2.0, level_spread_db: float = 1.0,
             strum_s: float = 0.0, **note_kw) -> Group:
    """Block chords changing every ``chord_s`` (chord tones end with the chord — a ring does not, S12).
    ``strum_s`` spreads the onsets of the chord tones (guitar strum 20-40 ms); voices differ by ±level_spread."""
    rng = random.Random(seed * 53 + 5)
    out: list[Source] = []
    t = t_start
    i = 0
    while t < t_end - 0.05:
        for j, nm in enumerate(chords[i % len(chords)]):
            out.append(HarmonicNote(f0_hz=note_hz(nm), t_on=t + j * strum_s, dur=min(chord_s, t_end - t),
                                    level_db=level_db + rng.uniform(-level_spread_db, level_spread_db),
                                    timbre=timbre, attack_s=attack_s, release_db_per_s=release_db_per_s,
                                    am_db=chorus_db, am_hz=chorus_hz * rng.uniform(0.8, 1.25),
                                    am_phase=rng.uniform(0, 6.28), label="chord", drift_cents=drift_cents,
                                    flutter_db=flutter_db, timbre_jitter_db=timbre_jitter_db,
                                    seed=rng.randrange(1 << 30), **note_kw))
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
                                vib_rate_hz=0.0, label="speech", drift_cents=15.0, flutter_db=1.5, timbre_jitter_db=5.0,
                                seed=rng.randrange(1 << 30)))
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
    gain × loop coupling and the (negative) GEQ gain at the ring frequency; ``excess_wander_db`` adds a slow,
    non-periodic loop-gain wander (air movement, head/mic motion: tenths of a dB — a real ring's growth rate is
    NOT constant and a marginal loop flickers either side of threshold). Then, per substep:

    * e_eff > 0: the line grows at e_eff/τ_loop dB/s (200 dB/s per dB on a 5 ms wedge loop, 100 on 10 ms
      tops, 14 on a 70 ms reverberant loop) up to the plateau ``sat_db`` (limiter / compressor — the M7
      "60 dB-prominent line at a fixed level for 15 s"); ``sat_db >= 0`` pins it at 0 dBFS (RTA clip flag)
      and grows hard-clip partials (H3 −12, H5 −18, H2 −30 once within ``clip_knee_db`` of full scale).
      A plateau set downstream of the tap (speaker limiter, amp clip, SPL) moves with any gain between mic and
      tap: the renderer passes the programme common-mode gain and the plateau follows it (``sat_tracks_gain``);
      a desk-clip plateau (sat_db >= 0) does not move.
    * e_eff <= 0: the line relaxes at max(|e_eff|, 0.5)/τ_loop dB/s toward the regenerated EXTRA power the loop
      adds on top of its excitation: Σ_lines P_line·c·(comb(δ)−1) + P_bed·c·(mean_band(comb)−1), comb =
      1/((1−g)²+4g sin²(πδτ)) (physics.comb_gain_lin): a programme line ON the mode rings on at +6 dB (g=−6) …
      +19 dB (−1); a line 100 cents away or a broadband bed gets far less. This is what makes ring_out's +1 dB
      staircase a probe and what makes speech 'ring' below threshold — now with the right selectivity.
    * ``established``: at ``sat_db`` from before frame 0 (no onset is ever observed, S2).
    * ``wander_db``: level wander at the plateau/tail — a seeded sum of incommensurate slow sinusoids
      (0.2-2.5 Hz), NOT one pure tone at ``wander_hz`` (kept only as the centre of the rate range).
    * ``hop_at_s``/``hop_cents``: mode hop (hand-held mic moved: neighbouring candidate takes over);
      ``freq_drift_cents``: slow drift of the mode itself (temperature/geometry; normally ~0).
    * ``harmonics``: acoustic distortion partials of a howl that is NOT clipping the desk (powered-speaker amp
      clipping / driver excursion heard by the mic, loop brief §1.4(1)): (k, rel_db) pairs that fade in 2 dB/dB
      above ``harmonics_knee_db``. Defeats "partials present ⇒ music unless the band reads 0.0".
    * excitation: ``excitation_db`` constant on-mode floor (room noise in the mode), plus, if
      ``excite_from_programme``, programme lines within ±1 band (comb-weighted) and the bed power in the band,
      both coupled at ``excite_coupling_db`` (share of the tap signal that arrives through the open mic path).
    """

    freq_hz: float = 3150.0
    excess_db: float = 0.5
    tau_loop_s: float = TAU_LOOP_TOPS_S
    t_on: float = 0.0
    start_db: float = -80.0
    sat_db: float = -6.0
    established: bool = False
    excess_points: Sequence[tuple[float, float]] | None = None
    excess_wander_db: float = 0.08
    excess_wander_hz: tuple[float, float] = (0.05, 0.6)
    excitation_db: float = -90.0
    excite_from_programme: bool = True
    excite_coupling_db: float = -6.0
    excite_bw_oct: float = 0.1
    wander_db: float = 0.3
    wander_hz: float = 0.7
    wander_phase: float = 0.0
    hop_at_s: float | None = None
    hop_cents: float = 0.0
    freq_drift_cents: float = 0.0
    clip_harmonics: Sequence[tuple[int, float]] = CLIP_HARMONICS
    clip_knee_db: float = CLIP_KNEE_DB
    harmonics: Sequence[tuple[int, float]] = ()
    harmonics_knee_db: float = -40.0
    sat_tracks_gain: bool = True
    growth_cap_db_per_s: float = 2000.0
    seed: int = 0
    label: str = "ring"
    kind: str = "FEEDBACK"

    def __post_init__(self) -> None:
        self.randomize(self.seed)
        self.reset()

    # -- state ---------------------------------------------------------------------------------
    def randomize(self, seed: int) -> None:
        """(Re)draw the wander processes from ``seed`` (the renderer calls this with its own seed)."""
        lo = max(0.05, self.wander_hz * 0.3)
        # slow wander (air movement, performer) plus a faster 2-7 Hz flutter component at ~1/3 of the depth
        # (turbulence / limiter and compressor gain riding): a plateaued ring is steady but not synthetic-steady
        if self.wander_db:
            slow = Wobble(seed * 11 + 1, 4, lo, max(lo * 4.0, self.wander_hz * 3.0))
            fast = Wobble(seed * 11 + 4, 4, 2.0, 7.0)
            self._wander = lambda t, s_=slow, f_=fast: 0.75 * s_(t) + 0.35 * f_(t)
        else:
            self._wander = _ZERO_WOBBLE
        self._ewander = Wobble(seed * 11 + 2, 3, *self.excess_wander_hz) if self.excess_wander_db else _ZERO_WOBBLE
        self._fdrift = Wobble(seed * 11 + 3, 3, 0.02, 0.3) if self.freq_drift_cents else _ZERO_WOBBLE

    def reset(self) -> None:
        self.level_db: float = self.sat_db if self.established else self.start_db
        self.e_eff: float = self.excess_db
        self.sat_now: float = self.sat_db
        self.t: float = -1e9

    def base_excess_db(self, t: float) -> float:
        if self.excess_points:
            pts = self.excess_points
            if t <= pts[0][0]:
                e = pts[0][1]
            else:
                prev = pts[0]
                e = prev[1]
                for p in pts[1:]:
                    if t < p[0]:
                        e = p[1] if p[0] == prev[0] else prev[1] + (p[1] - prev[1]) * (t - prev[0]) / (p[0] - prev[0])
                        break
                    prev = p
                else:
                    e = prev[1]
        else:
            e = self.excess_db
        if self.excess_wander_db:
            e += self.excess_wander_db * self._ewander(t)
        return e

    def current_hz(self, t: float) -> float:
        f = self.freq_hz
        if self.hop_at_s is not None and t >= self.hop_at_s:
            f *= 2.0 ** (self.hop_cents / 1200.0)
        if self.freq_drift_cents:
            f *= 2.0 ** (self.freq_drift_cents * self._fdrift(t) / 1200.0)
        return f

    def active(self, t: float) -> bool:
        return self.established or t >= self.t_on

    def regen_extra_db(self, e_db: float, f_hz: float, lines: Sequence[tuple[float, float]], bed_db: float) -> float:
        """Regenerated extra power (dB) the loop adds at the tap for round-trip excess ``e_db`` (<0 meaningful;
        clamped at −0.25 dB), given programme ``lines`` [(f, L)] near the mode and the bed power in the band."""
        g = 10.0 ** (min(e_db, 0.0) / 20.0)
        c = 10.0 ** (self.excite_coupling_db / 10.0)
        tau = self.tau_loop_s
        # the constant on-mode floor (room noise in the mode) is not rendered by anyone else: full comb gain
        extra = 10.0 ** (self.excitation_db / 10.0) * comb_gain_lin(g, 0.0, tau)
        if bed_db > -150.0:
            extra += 10.0 ** (bed_db / 10.0) * c * max(0.0, comb_band_mean_lin(g, BAND_REL_BW * f_hz, tau) - 1.0)
        for lf, lv in lines:
            extra += 10.0 ** (lv / 10.0) * c * max(0.0, comb_gain_lin(g, lf - f_hz, tau) - 1.0)
        return 10.0 * math.log10(extra) if extra > 1e-20 else -200.0

    def step(self, t: float, dt: float, *, common_db: float, geq_gain_db: float, excitation_db: float | None = None,
             lines: Sequence[tuple[float, float]] = (), bed_db: float = -200.0, prog_gain_db: float = 0.0) -> None:
        """Advance the loop by ``dt`` to time ``t``. ``common_db`` = common-mode gain × loop coupling,
        ``geq_gain_db`` = (negative) GEQ gain at the ring frequency, ``lines``/``bed_db`` = programme near the
        mode at the tap (uncoupled levels), ``prog_gain_db`` = gain between mic and tap applied to programme
        (moves a downstream-set plateau). ``excitation_db`` (legacy): an on-mode line level, already coupled."""
        self.t = t
        if not self.active(t):
            self.e_eff = -60.0
            self.level_db = self.start_db
            return
        e = self.base_excess_db(t) + common_db + geq_gain_db
        self.e_eff = e
        sat = self.sat_db
        if sat < 0.0 and self.sat_tracks_gain:
            sat = min(-0.5, sat + prog_gain_db)
        self.sat_now = sat
        f = self.current_hz(t)
        if excitation_db is not None and excitation_db > -150.0:
            lines = tuple(lines) + ((f, excitation_db - self.excite_coupling_db),)
        L = self.level_db
        if e > 0.0:
            rate = min(self.growth_cap_db_per_s, e / self.tau_loop_s)
            L = min(sat, L + rate * dt)
            floor = min(sat, self.seed_db(f, lines, bed_db))   # what recirculates before any build-up
            if L < floor:
                L = floor
        else:
            target = min(sat, self.regen_extra_db(e, f, lines, bed_db))
            # build-up and ring-down both converge at |e| dB per round trip (critical slowing near threshold:
            # T60 = 60·τ/|e|); the 0.1 dB floor only stops the state freezing at e == 0 exactly
            rate = max(abs(e), 0.1) / self.tau_loop_s
            if L > target:
                L = max(target, L - rate * dt)
            else:
                L = min(target, L + rate * dt)
        self.level_db = max(-160.0, L)

    def seed_db(self, f_hz: float, lines: Sequence[tuple[float, float]], bed_db: float) -> float:
        """Level the oscillation starts from when the loop goes over threshold with no prior build-up: the coupled
        excitation inside the loop's capture range (comb shape at g=0.7: half-width ≈0.057/τ Hz) — the bed power
        within that width and any programme line that close — plus the on-mode floor. No regenerative gain."""
        c = 10.0 ** (self.excite_coupling_db / 10.0)
        tau = self.tau_loop_s
        p = 10.0 ** (self.excitation_db / 10.0)
        if bed_db > -150.0:
            frac = min(1.0, (0.114 / tau) / max(1e-9, BAND_REL_BW * f_hz))
            p += 10.0 ** (bed_db / 10.0) * c * frac
        norm = comb_gain_lin(0.7, 0.0, tau)
        for lf, lv in lines:
            p += 10.0 ** (lv / 10.0) * c * comb_gain_lin(0.7, lf - f_hz, tau) / norm
        return 10.0 * math.log10(p) if p > 1e-20 else -200.0

    def display_level_db(self, t: float) -> float:
        wob = self.wander_db * self._wander(t) if self.wander_db else 0.0
        cap = 0.0 if self.sat_db >= 0.0 else self.sat_now + abs(self.wander_db)
        return min(cap, self.level_db + wob)

    def harmonics_active(self, level_db: float | None = None) -> bool:
        L = self.level_db if level_db is None else level_db
        return bool((self.sat_db >= -0.5 and L > self.clip_knee_db) or (self.harmonics and L > self.harmonics_knee_db))

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
        if self.harmonics and L > self.harmonics_knee_db:
            over = L - self.harmonics_knee_db
            for k, rel in self.harmonics:
                fk = f * k
                if fk < 20000.0:
                    out.append((fk, L + rel - max(0.0, 20.0 - 2.0 * over)))   # 2 dB/dB fade-in over the first 10 dB
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
