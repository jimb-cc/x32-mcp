"""CFS² feedback discriminator and notch planner (DESIGN.md §12) — pure Python, no I/O, no asyncio.

Two synchronous, side-effect-free pieces used by ``cfs.py``:

* :class:`FeedbackDetector` consumes one RTA frame at a time (100 dB values from ``/meters/15``,
  band ``i`` centred at ``band_hz[i]`` = ``10000 * 2 ** ((i - 90) / 10)`` Hz, docs/research/meters.md §4.2)
  and returns :class:`Detection` objects for spectral lines that satisfy the physical predicates of
  regenerative (electro-acoustic) feedback.
* :class:`NotchController` turns detections into GEQ band cuts (:class:`Notch`); unchanged.

Units and indices at the public boundary
----------------------------------------
* Levels are dB (RTA dB re. full scale, -128 = "no signal", 0.0 = "clipped"), times are seconds
  (``time.time()`` style floats), slopes are dB/s.
* **RTA band indices are 0-based** (``Detection.band``); **GEQ band numbers are 1-based** (``Notch.band``).
* ``Detection.freq_hz`` is the *interpolated* line frequency (power centroid of the peak band and its two
  neighbours, ±0.05 band for a clean line) — not the band centre — so the notch planner can pick the
  right 1/3-octave band for a line that sits between RTA centres (loop brief §2.1/§4.1).

Why this is not a weighted sum any more (REVIEW_BRIEF §1)
----------------------------------------------------------
The old ``0.3·prominence + 0.2·persistence + 0.5·growth`` let surplus in one requirement buy a deficit in
another and made growth mandatory in practice; both of its failure modes were seen on the real desk
(HANDOVER §4b). This module replaces it with a small decision procedure over explicit, independently
testable predicates. Every constant below is a physical quantity with a stated origin; see
``DetectorConfig`` and the report ``DESIGN.md`` that accompanies this branch.

The model in one paragraph. A *line* is a local spectral maximum that is narrow (a lone sinusoid leaks
only through the analyser's own skirts, so it stands ≥10 dB clear of the bands two either side) and
prominent over the local median. Lines are tracked frame to frame by their power centroid (sub-band
frequency). For every track the detector knows (a) how it was **born** — already there when the
detector armed (``est``), popped up at full level inside one analyser rise time (``pop`` = an
instrument onset), or **grew** dB-linearly out of the bed over several frames (the signature of a loop
with |L| > 1: growth rate = excess/loop-delay, loop brief §1.3); (b) whether it carries a **harmonic
family** (partials at +10, +15.85, +20, +23.2 bands present as peaks and co-moving, or it is itself
somebody's 2nd/3rd harmonic) — musical notes do, a linear loop does not (§2.2); (c) whether its centroid
is **stationary** (melody moves ≥0.83 band per semitone, vibrato wobbles it; a ring is fixed by geometry,
§2.3); (d) whether its level is **steady / not decaying** (struck and plucked notes decay, a loop above
threshold never does); (e) whether its rise is explained by a **common-mode** move of the whole spectrum
(operator riding a fader) and, in ``ring_out``, how it **responded to the server's own +1 dB master
steps** (a band within a few dB of threshold over-responds ≥2 dB/dB; programme moves ≤1 dB/dB, §1.5).
Growth is measured only over windows longer than the analyser band could take to settle
(``analyser_settle_cycles``/Δf: 3 frames above 300 Hz, 12 frames at 78 Hz, 23 at 39 Hz), which is what
removes the M7 40/80 Hz false positives: an instant bass onset seen through a 2.7 Hz-wide band *is* a
0.4 s ramp (analyser brief §1), so no ramp shorter than that is admissible evidence at that frequency.

Decision (per track, per frame; first matching rule emits; a track once emitted is re-emitted every
``cooldown_s`` while it is still present and not decaying so ``cfs`` can deepen the notch):

* ``CLIP``   peak ≥ ``clip_level_db`` (the 0.0 dB clip flag) on a narrow line for ``loud_frames`` frames.
* ``LOUD``   narrow, family-less (family ignored above ``family_escape_level_db``: a clipping howl grows odd
             partials), stationary, steady line at ≥ ``loud_level_db`` for ``loud_frames`` frames, above
             ``lf_strict_hz``. "A lone sinusoid within 10 dB of full scale" (loop brief P8).
* ``GROW``   narrow, stationary line whose peak level rose dB-linearly — either FAST (≥3 of the last 5
             frame steps ≥ ``growth_fast_step_db`` summing to ≥ ``growth_fast_total_db``, no single step
             carrying the rise: that would be an onset) or SLOW (least-squares ramp ≥ ``growth_slow_min_db_per_s``
             sustained over ≥ max(``growth_slow_frames``, settle(b)) frames, net rise ≥ ``growth_slow_total_db``,
             both halves rising) — net of any rise of the spectrum reference, with no co-moving family.
* ``EST``    line already present when the detector armed (no onset observable): narrow, family-less,
             stationary, steady, not decaying, ≥ ``est_min_level_db`` (a compressor/limiter plateau cannot sit
             lower at a bus tap with sane gain structure, loop brief §1.4), for ``est_frames`` frames.
* ``PROBE``  (ring_out, after :meth:`FeedbackDetector.note_gain_step`) a tracked line whose level rose by
             ≥ ``probe_excess_db`` more than the spectrum did on two consecutive master steps, or by
             ≥ ``probe_strong_db`` more on one: loop-gain dependent ⇒ regenerative, cut it before it runs away
             (that is what a human ring-out does). A line that answered two steps with ≤ ``probe_linear_db``
             excess is tagged stationary/programme and is never cut on ``EST``/``LOUD`` evidence.

A line that *popped* into existence at full level and then sits flat is programme until proven otherwise
(organ, flute, whistle, sine lead, 808 — the family-less instruments): it is published as a candidate, never
cut on passive evidence below ``loud_level_db``. This is the one irreducible ambiguity (a ring that reached a
compressor plateau inside one frame looks the same); the asymmetry is resolved toward not cutting in
``watch`` (a human is on the fader) and toward the probe in ``ring_out`` (REVIEW_BRIEF Q4).

Only ``logging`` is used for diagnostics (stdout is the MCP transport).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, fields
from statistics import median
from typing import Any, Callable, Mapping, Protocol, Sequence

log = logging.getLogger(__name__)

__all__ = [
    "DetectorConfig",
    "Detection",
    "Candidate",
    "FeedbackDetector",
    "Notch",
    "GeqWriter",
    "RecordingGeqWriter",
    "NotchController",
]


# ---------------------------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------------------------

_WEIGHT_KEYS = {"prominence": "w_prominence", "persistence": "w_persistence", "growth": "w_growth"}
_HARM_OFFSETS = tuple((k, 10.0 * math.log2(k)) for k in (2, 3, 4, 5))   # H2 +10, H3 +15.85, H4 +20, H5 +23.22 bands


@dataclass(frozen=True)
class DetectorConfig:
    """Detector + notch thresholds; one field per ``device.yaml`` ``detector:`` key.

    Keys of the old weighted-sum heuristic (``weights``, ``confidence_threshold``, ``growth_ref_db_per_s``,
    ``growth_max_db_per_s``, ``monotonic_tolerance_db``, ``override_*``) are still accepted so an existing
    ``device.yaml`` loads; ``confidence_threshold`` is still the value an emitted detection's confidence is
    guaranteed to reach (dashboard contract), the others are inert. Every new key has a default, so
    ``DetectorConfig.from_descriptor`` works with a yaml that predates them.
    """

    # --- line qualification (single frame) ---------------------------------------------------
    prominence_db: float = 12.0          # dB over the median of the ±neighbour_bins neighbours to *emit*
    neighbour_bins: int = 3              # bands each side used for the median
    track_prominence_db: float = 6.0     # a local maximum this prominent is *tracked* (history before it matters)
    grow_prominence_db: float = 10.5     # a growing line may be emitted at this prominence: growth is itself evidence,
                                         # and a ring emerging beside programme partials reads a few dB low
    narrow_db: float = 8.0               # peak − max(level 2 bands outside its footprint): a lone sinusoid leaks only via the
                                         # analyser skirts (≥12 dB down at ±2 even for 2nd-order skirts, analyser
                                         # brief §3); formant humps, cymbal wash and PA ripple are ≥3 bands wide
    track_min_level_db: float = -100.0   # absolute floor for tracking at all (only excludes the -128 "no signal" region;
                                         # every absolute gate floats on the RTA gain pref, so prominence, narrowness
                                         # and the line's own history are the gates, not a level)
    # --- harmonic family (single frame, accumulated) -----------------------------------------
    family_prominence_db: float = 6.0    # a partial is "present" when its band (±1) is itself a local peak this
                                         # prominent (presence-as-peak, not energy: the bed has energy everywhere)
    family_tol_bands: float = 0.5        # a partner must sit within this of the exact harmonic position (60 c):
                                         # integer-ratio partials land exactly; an unrelated line a band away does not
    family_ratio_tol_db: float = 3.0     # partials of one source keep their level ratio (dB) within this while the
                                         # note swells or decays (shared envelope; per-partial flutter ≲ 1-2 dB);
                                         # two independent lines growing at their own excess/τ do not
    family_partials: int = 2             # ≥ this many of H2..H5 present ⇒ musical note (one coincident partner
                                         # happens between unrelated rings ~20 % of the time, two < 1 %, [A §4.3])
    family_veto_frac: float = 0.4        # fraction of recent frames with a family that makes a line musical
    family_escape_level_db: float = -6.0 # this close to full scale a howl clips somewhere and grows odd partials
                                         # (loop brief §2.2): the family test is void, level decides
    # --- stationarity / steadiness (temporal) ------------------------------------------------
    centroid_tol_bands: float = 0.6      # centroid excursion over the last 10 frames that still counts as fixed:
                                         # a semitone step is 0.83 band, vibrato ±50 c swings an edge tone's
                                         # centroid ±0.3..0.8 band; a ring's centroid noise is < 0.1 band
    level_unsteady_db: float = 1.5       # sd of frame-to-frame peak steps above which a *plateau* is not one
                                         # (voice/whistle flutter 1-2 dB + vibrato; a sine through PEAK ≈ 0.1-0.4)
    decay_tol_db: float = 2.0            # a line this far below its recent (1 s) maximum is decaying/decayed:
                                         # never (re-)emitted (plucked/struck notes, killed rings, RTA release tails)
    # --- growth (temporal; the loop signature) -----------------------------------------------
    analyser_settle_cycles: float = 3.0  # a rise must outlast this many 1/Δf periods of its own band before it is
                                         # evidence (time-bandwidth bound, any filter bank; 3/Δf = 95 % settled)
    growth_fast_step_db: float = 2.0     # per-frame step that counts as "rising this frame" (≥40 dB/s)
    growth_fast_total_db: float = 12.0   # ≥3 such steps within 5 frames summing to this, none carrying > 55 %:
                                         # exponential growth; an onset is one big step, a swell is < 2 dB/frame
    growth_slow_frames: int = 5          # minimum ramp length for the least-squares path (≥ persistence)
    growth_slow_min_db_per_s: float = 1.5  # marginal loops grow at excess/τ down to ~1 dB/s (§1.3 table);
                                         # below this a "ramp" is air-movement wander
    growth_slow_total_db: float = 6.0    # net dB a slow ramp must have climbed within 1 s (≫ ±0.5 dB loop-gain wander)
    growth_slow_total_long_db: float = 4.0  # ... and over >= 2 s (a marginal loop at 2 dB/s: e ≈ 0.02 dB, τ 11 ms)
    growth_drop_tol_db: float = 3.0      # a fall this far below the ramp's maximum restarts the ramp (a real
                                         # decay); smaller dips are band noise at 15-20 dB SNR
    growth_window_frames: int = 60       # longest ramp considered (3 s)
    # --- birth classes -----------------------------------------------------------------------
    est_arm_frames: int = 3              # tracks born in the first N frames were "already there at arm"
    est_frames: int = 5                  # frames of stationarity/steadiness before an established line is emitted
    est_min_level_db: float = -40.0      # a plateaued howl sits at a limiter/compressor/clip level: -30..0 dBFS at
                                         # a bus tap with sane gain structure (§1.4); a stationary line below this
                                         # that was there at arm is hum/HVAC/room tone
    pop_step_frac: float = 0.55          # one frame step carrying more than this share of a rise = an onset
    # --- absolute level rules ----------------------------------------------------------------
    loud_level_db: float = -10.0         # a lone stationary sinusoid within 10 dB of full scale (P8)
    loud_frames: int = 3               # 150 ms of it: no percussive transient is that narrow for that long
    clip_level_db: float = -0.5          # /meters/15 reads exactly 0.00 when the tap clipped (meters.md §4.2)
    # --- frequency window (prior; soft) ------------------------------------------------------
    lf_edge_hz: float = 0.0              # hard floor (0 = none). Per-session: 0.7 × the open mics' HPF (§3.4)
    lf_strict_hz: float = 160.0          # below this only GROW (over the long analyser-safe window) or PROBE may
                                         # emit — never EST/LOUD: LF is where programme fundamentals live and where
                                         # loop growth is slow enough (τ 15-30 ms) to be watched
    hf_edge_hz: float = 12500.0          # SM58-class mics die above 10 k; condensers ring to 12-13 k (§3.1)
    # --- common mode / probe -----------------------------------------------------------------
    ref_band_lo: int = 25                # spectrum reference = median level of bands 25..85 (110 Hz-7 kHz, where
    ref_band_hi: int = 85                # the analyser settles within a frame)
    probe_settle_frames: int = 2         # frames after a master step before the "after" window (write lands + τ_a)
    probe_window_frames: int = 12        # frames each side of a step compared (medians)
    probe_excess_db: float = 2.0         # (Δline − Δspectrum) per step that marks loop-gain dependence: a mode 4 dB
                                         # under threshold answers +3 dB to +1 dB, programme +0..+1 (§1.5)
    probe_strong_db: float = 4.0         # one step this super-linear is enough (mode within ~2 dB of threshold)
    probe_linear_db: float = 1.3         # ≤ this on two steps: stationary line / programme / driven resonance
    probe_min_history_frames: int = 6    # a line must predate the step by this much to be compared across it
    # --- emission ----------------------------------------------------------------------------
    persistence_frames: int = 3          # minimum track age for any emission
    band_tolerance: int = 1              # drift allowed while tracking / cooldown radius (RTA bands)
    cooldown_s: float = 1.0              # s between emissions for the same line
    confidence_threshold: float = 0.7    # emitted detections report at least this (dashboard contract)
    # --- notch planning / VERIFY (cfs.py) — unchanged ----------------------------------------
    notch_step_db: float = -3.0
    notch_max_db: float = -9.0
    notch_budget_default: int = 6
    merge_adjacent_bands: int = 1
    decay_verify_db: float = 6.0
    decay_verify_s: float = 1.5
    decay_verify_frames: int = 2
    frame_period_s: float = 0.05
    mode: str = "watch"                  # "watch" (human owns the gain) | "ringout" (server steps the master)
    # --- legacy keys of the weighted-sum heuristic (accepted, inert) -------------------------
    min_level_db: float = -45.0          # the M7 candidate gate: one room's background music (HANDOVER §4b(3))
    growth_min_db_per_s: float = 6.0
    growth_ref_db_per_s: float = 20.0
    growth_max_db_per_s: float = 60.0
    monotonic_tolerance_db: float = 1.0
    w_prominence: float = 0.3
    w_persistence: float = 0.2
    w_growth: float = 0.5
    override_prominence_db: float = 25.0
    override_persistence_frames: int = 6

    def __post_init__(self) -> None:
        def need(cond: bool, msg: str) -> None:
            if not cond:
                raise ValueError(f"DetectorConfig: {msg}")

        need(self.prominence_db > 0, "prominence_db must be > 0")
        need(0 < self.track_prominence_db <= self.prominence_db, "track_prominence_db must be in (0, prominence_db]")
        need(self.neighbour_bins >= 1, "neighbour_bins must be >= 1")
        need(self.persistence_frames >= 1, "persistence_frames must be >= 1")
        need(self.narrow_db >= 0, "narrow_db must be >= 0")
        need(self.family_partials >= 1, "family_partials must be >= 1")
        need(0.0 < self.family_veto_frac <= 1.0, "family_veto_frac must be in (0, 1]")
        need(self.analyser_settle_cycles >= 0, "analyser_settle_cycles must be >= 0")
        need(self.growth_fast_step_db > 0 and self.growth_fast_total_db > 0, "growth_fast_* must be > 0")
        need(self.growth_slow_frames >= 2, "growth_slow_frames must be >= 2")
        need(self.growth_slow_min_db_per_s > 0 and self.growth_slow_total_db > 0, "growth_slow_* must be > 0")
        need(self.growth_drop_tol_db > 0, "growth_drop_tol_db must be > 0")
        need(self.growth_window_frames >= max(2, self.persistence_frames, self.growth_slow_frames),
             "growth_window_frames too small")
        need(self.est_arm_frames >= 0 and self.est_frames >= 1, "est_* frames invalid")
        need(0.0 < self.pop_step_frac <= 1.0, "pop_step_frac must be in (0, 1]")
        need(self.loud_frames >= 1, "loud_frames must be >= 1")
        need(self.clip_level_db <= 0.0 and self.loud_level_db <= 0.0, "level thresholds must be <= 0 dBFS")
        need(self.lf_edge_hz >= 0 and self.hf_edge_hz > self.lf_edge_hz, "frequency window invalid")
        need(0 <= self.ref_band_lo < self.ref_band_hi, "ref_band_lo/hi invalid")
        need(self.probe_settle_frames >= 0 and self.probe_window_frames >= 2, "probe_* frames invalid")
        need(self.band_tolerance >= 0, "band_tolerance must be >= 0")
        need(self.cooldown_s >= 0, "cooldown_s must be >= 0")
        need(self.notch_step_db < 0, "notch_step_db must be negative (cuts only)")
        need(self.notch_max_db <= self.notch_step_db, "notch_max_db must be <= notch_step_db")
        need(self.notch_budget_default >= 0, "notch_budget_default must be >= 0")
        need(self.decay_verify_frames >= 1, "decay_verify_frames must be >= 1")
        need(self.merge_adjacent_bands >= 0, "merge_adjacent_bands must be >= 0")
        need(self.mode in ("watch", "ringout"), "mode must be 'watch' or 'ringout'")
        need(min(self.w_prominence, self.w_persistence, self.w_growth) >= 0, "weights must be >= 0")
        need(self.override_prominence_db >= 0 and self.override_persistence_frames >= 1, "override_* invalid")
        need(self.growth_ref_db_per_s > 0 and self.growth_max_db_per_s > self.growth_min_db_per_s
             and self.monotonic_tolerance_db >= 0, "legacy growth keys invalid")

    @property
    def weights(self) -> dict[str, float]:
        return {"prominence": self.w_prominence, "persistence": self.w_persistence, "growth": self.w_growth}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DetectorConfig":
        """Build from a ``detector:`` mapping. Unknown keys are ignored (logged at DEBUG)."""
        known = {f.name: f.type for f in fields(cls)}
        kw: dict[str, Any] = {}
        for key, value in data.items():
            if key == "weights":
                if not isinstance(value, Mapping):
                    raise ValueError("DetectorConfig: weights must be a mapping")
                for wk, wv in value.items():
                    if wk not in _WEIGHT_KEYS:
                        raise ValueError(f"DetectorConfig: unknown weight {wk!r}")
                    kw[_WEIGHT_KEYS[wk]] = float(wv)
                continue
            if key not in known:
                log.debug("DetectorConfig: ignoring unknown key %r", key)
                continue
            typ = known[key]
            kw[key] = int(value) if typ == "int" else str(value) if typ == "str" else float(value)
        return cls(**kw)

    @classmethod
    def from_descriptor(cls, d: Any) -> "DetectorConfig":
        """``DetectorConfig.from_dict(d.detector)`` for a loaded :class:`x32mcp.descriptor.Descriptor`."""
        return cls.from_dict(d.detector)

    def to_dict(self) -> dict[str, Any]:
        out = {f.name: getattr(self, f.name) for f in fields(self) if not f.name.startswith("w_")}
        out["weights"] = self.weights
        return out


# ---------------------------------------------------------------------------------------------
# detector
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Detection:
    """One feedback verdict. ``band`` is the 0-based RTA band of the peak, ``freq_hz`` the interpolated line
    frequency, ``reasons`` the predicates that justified it (for the notch report)."""

    ts: float
    band: int
    freq_hz: float
    level_db: float
    prominence_db: float
    slope_db_per_s: float
    frames: int
    confidence: float
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "band": self.band,
            "freq_hz": self.freq_hz,
            "level_db": self.level_db,
            "prominence_db": self.prominence_db,
            "slope_db_per_s": self.slope_db_per_s,
            "frames": self.frames,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
        }


@dataclass
class Candidate:
    """A tracked spectral line (see module docstring). Histories are per matched frame, oldest first, capped."""

    band: int
    first_ts: float
    frames: int = 0
    confidence: float = 0.0
    freq_hz: float = 0.0
    level_db: float = -128.0
    prominence_db: float = 0.0
    slope_db_per_s: float = 0.0
    growth_score: float = 0.0
    last_ts: float = 0.0
    emitted: int = 0
    # --- new state ---
    centroid: float = 0.0
    narrow_db: float = 0.0
    birth: str = "new"                 # est | pop | grow | static | new
    est: bool = False
    born_frame: int = 0
    pre_level_db: float | None = None  # what this band read just before the line appeared
    feedback: bool = False             # sticky once emitted
    musical: str = ""                  # sticky programme verdict (reason) — never emitted afterwards
    reasons: tuple[str, ...] = ()
    verdict: str = ""                  # last frame's classification (diagnostic)
    hops: int = 0
    coast: int = 0
    last_emit_ts: float = -1e9
    probe_hits: int = 0                # consecutive super-linear step responses
    probe_linear: int = 0              # consecutive linear (≤1 dB/dB) step responses
    probe_last_excess_db: float = 0.0
    probe_steps: int = 0
    kf: list[int] = field(default_factory=list)       # frame index
    ts_list: list[float] = field(default_factory=list)
    levels: list[float] = field(default_factory=list)  # peak band level (dB)
    cen_list: list[float] = field(default_factory=list)
    ref_list: list[float] = field(default_factory=list)
    fam_list: list[bool] = field(default_factory=list)   # family incl. a lone exact-octave partner
    fam_strong: list[bool] = field(default_factory=list) # >= family_partials partners, or somebody's H2/H3/H4
    grow_start: int = 0                # index into the lists where the current monotone ramp starts
    cen0: float | None = None          # centroid at birth (median of the first frames): where this line lives
    diag: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "band": self.band,
            "freq_hz": self.freq_hz,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "frames": self.frames,
            "level_db": self.level_db,
            "prominence_db": self.prominence_db,
            "slope_db_per_s": self.slope_db_per_s,
            "growth_score": self.growth_score,
            "confidence": self.confidence,
            "emitted": self.emitted,
            "centroid": round(self.centroid, 2),
            "narrow_db": round(self.narrow_db, 1),
            "birth": self.birth,
            "verdict": self.verdict,
            "musical": self.musical,
            "reasons": list(self.reasons),
            "probe_excess_db": round(self.probe_last_excess_db, 2),
        }


def _ls_slope(ts: Sequence[float], vs: Sequence[float]) -> float:
    """Least-squares slope of ``vs`` against ``ts`` (dB/s); 0.0 when fewer than two distinct times."""
    n = len(ts)
    if n < 2:
        return 0.0
    tm = sum(ts) / n
    vm = sum(vs) / n
    sxx = sum((t - tm) ** 2 for t in ts)
    if sxx <= 0.0:
        return 0.0
    sxy = sum((t - tm) * (v - vm) for t, v in zip(ts, vs))
    return sxy / sxx


def _ls_fit(ts: Sequence[float], vs: Sequence[float]) -> tuple[float, float]:
    """(slope, intercept) least squares."""
    n = len(ts)
    if n < 2:
        return 0.0, (vs[0] if vs else 0.0)
    tm = sum(ts) / n
    vm = sum(vs) / n
    sxx = sum((t - tm) ** 2 for t in ts)
    if sxx <= 0.0:
        return 0.0, vm
    s = sum((t - tm) * (v - vm) for t, v in zip(ts, vs)) / sxx
    return s, vm - s * tm


def _sd(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


@dataclass
class _Step:
    ts: float
    delta_db: float
    k: int | None = None       # frame index of the first frame at/after the step
    done: bool = False


class FeedbackDetector:
    """Frame-by-frame feedback discriminator over an RTA stream (see module docstring)."""

    HIST = 64          # frames of full-spectrum history kept (probe windows, pre-birth baselines, co-movement)
    GRAVE_FRAMES = 10  # a line reborn within this many frames of dying inherits its history class

    def __init__(self, cfg: DetectorConfig, band_hz: Sequence[float], *, mode: str | None = None) -> None:
        self.cfg = cfg
        self.mode = (mode or cfg.mode)
        if self.mode not in ("watch", "ringout"):
            raise ValueError("mode must be 'watch' or 'ringout'")
        self.band_hz: tuple[float, ...] = tuple(float(h) for h in band_hz)
        n = len(self.band_hz)
        if n < 2 * cfg.neighbour_bins + 1:
            raise ValueError("band_hz too short for neighbour_bins")
        # per-band constants
        self._settle_frames: list[int] = []
        for f in self.band_hz:
            bw = 0.06932 * f   # 1/10-octave -3 dB bandwidth
            fr = cfg.analyser_settle_cycles / (bw * cfg.frame_period_s) if bw > 0 else 0.0
            self._settle_frames.append(max(3, int(math.ceil(fr))))
        self._ref_lo = min(cfg.ref_band_lo, n - 2)
        self._ref_hi = min(cfg.ref_band_hi, n - 1)
        self.reset()

    # -- helpers -------------------------------------------------------------------------------
    def prominences(self, values_db: Sequence[float]) -> list[float]:
        """``level[i] - median(neighbours within ±neighbour_bins, excluding i)`` for every band (dB)."""
        n = len(values_db)
        k = self.cfg.neighbour_bins
        out: list[float] = []
        for i in range(n):
            lo = max(0, i - k)
            hi = min(n, i + k + 1)
            neigh = [values_db[j] for j in range(lo, hi) if j != i]
            out.append(values_db[i] - median(neigh))
        return out

    def freq_of(self, centroid: float) -> float:
        """Interpolated frequency of a (fractional) band position (log-linear between centres)."""
        n = len(self.band_hz)
        c = min(max(centroid, 0.0), n - 1.0)
        i = int(math.floor(c))
        if i >= n - 1:
            return self.band_hz[-1]
        frac = c - i
        return self.band_hz[i] * (self.band_hz[i + 1] / self.band_hz[i]) ** frac

    def _in_cooldown(self, band: int, ts: float) -> bool:
        tol = self.cfg.band_tolerance
        for b in range(band - tol, band + tol + 1):
            until = self._cooldown.get(b)
            if until is not None and ts < until:
                return True
        return False

    # -- API -----------------------------------------------------------------------------------
    def note_gain_step(self, delta_db: float, ts: float) -> None:
        """Tell the detector the server changed the bus master by ``delta_db`` at ``ts`` (ring_out's active
        probe). Lines whose level answers super-linearly are loop-gain dependent (loop brief §1.5)."""
        if not math.isfinite(delta_db) or delta_db == 0.0:
            return
        self._steps.append(_Step(float(ts), float(delta_db)))
        if len(self._steps) > 32:
            del self._steps[0]

    def note_cut(self, band_hz: float, depth_db: float, ts: float) -> None:
        """Tell the detector a GEQ cut of ``depth_db`` at ``band_hz`` was written at ``ts`` (informational: the
        bands under the bell are excluded from the spectrum reference for a few frames)."""
        self._cuts.append((float(ts), float(band_hz), float(depth_db)))
        if len(self._cuts) > 32:
            del self._cuts[0]

    def reset(self) -> None:
        self._cands: list[Candidate] = []
        self._grave: list[tuple[int, Candidate]] = []
        self._cooldown: dict[int, float] = {}
        self._vals: list[list[float]] = []     # last HIST frames
        self._refs: list[float] = []
        self._tss: list[float] = []
        self._k0 = 0                           # frame index of self._vals[0]
        self._steps: list[_Step] = []
        self._recent_step_k: int | None = None   # frame of the last upward master step the server told us about
        self._cuts: list[tuple[float, float, float]] = []
        self.frames_seen: int = 0
        self.last_ts: float | None = None
        self.flags: dict[str, Any] = {}        # diagnostics: analyser misconfiguration hints

    @property
    def candidates(self) -> list[Candidate]:
        """Current qualifying lines (prominence ≥ ``prominence_db``; live objects, read-only), lowest band first."""
        return [c for c in self._cands if c.prominence_db >= self.cfg.prominence_db and c.coast == 0]

    @property
    def tracks(self) -> list[Candidate]:
        """Every tracked line including sub-threshold ones (diagnostics)."""
        return list(self._cands)
    # -- per-frame machinery ------------------------------------------------------------------

    def _hist_level(self, band: int, k: int) -> float | None:
        i = k - self._k0
        if 0 <= i < len(self._vals) and 0 <= band < len(self.band_hz):
            return self._vals[i][band]
        return None

    def _pre_level(self, band: int, k: int) -> float | None:
        """Median of what ``band`` read 2..8 frames before frame ``k`` (where a new line came from)."""
        xs = [self._hist_level(band, kk) for kk in range(k - 8, k - 1)]
        xs = [x for x in xs if x is not None]
        return median(xs) if xs else None

    def _present_since_arm(self, band: int, level: float) -> bool:
        """True when the stored history reaches back to frame 0 and ``band`` (or a neighbour) has read within
        6 dB of ``level`` in every frame since: the line was there when the detector armed."""
        if self._k0 != 0 or not self._vals:
            return False
        n = len(self.band_hz)
        for row in self._vals[:-1]:
            best = max(row[j] for j in (band - 1, band, band + 1) if 0 <= j < n)
            if best < level - 6.0:
                return False
        return True

    @staticmethod
    def _narrowness(vals: Sequence[float], i: int) -> float:
        """How far the peak stands above the *shallower* of its two flanks, where each flank is the lower of the two
        bands just outside the line's footprint. A lone sinusoid leaks only through the analyser skirts, so at
        least one of the two bands on each side is ≥12-18 dB down (analyser brief §3) — also when another line
        sits two bands away (the band between them dips). A broadband hump (formant, cymbal wash, PA ripple) has
        no dip on either side. A line between two centres reads (-3, -3) in a pair of bands: the footprint is
        then the pair and the flanks start beyond it."""
        n = len(vals)
        lo = hi = i
        if i + 1 < n and vals[i + 1] >= vals[i] - 4.0 and (i == 0 or vals[i + 1] >= vals[i - 1]):
            hi = i + 1
        elif i >= 1 and vals[i - 1] >= vals[i] - 4.0:
            lo = i - 1

        def flank(a: int, b: int) -> float:
            xs = [vals[j] for j in (a, b) if 0 <= j < n]
            return min(xs) if xs else -1e9

        return vals[i] - max(flank(lo - 1, lo - 2), flank(hi + 1, hi + 2))

    @staticmethod
    def _centroid(vals: Sequence[float], i: int) -> tuple[float, float]:
        """Power centroid (fractional band) and cluster level (dB) over bands i-1..i+1."""
        n = len(vals)
        num = den = 0.0
        for j in (i - 1, i, i + 1):
            if 0 <= j < n:
                p = 10.0 ** (vals[j] / 10.0)
                num += j * p
                den += p
        if den <= 0.0:
            return float(i), vals[i]
        return num / den, 10.0 * math.log10(den)

    def _family_now(self, c: float, is_peak: Sequence[bool], vals: Sequence[float]) -> tuple[int, bool, list[int]]:
        """(number of H2..H5 present as peaks, is-somebody's-harmonic, partner bands found) for a line at
        fractional band ``c`` in the current frame (presence only; co-movement is judged at decision time).
        A partner counts only if its own centroid is within ``family_tol_bands`` of the exact harmonic position.
        ``partners`` lists the H2..H5 bands found first (``n_up`` of them), then the sub-harmonic evidence."""
        n = len(is_peak)
        tol = self.cfg.family_tol_bands

        def peak_near(x: float) -> int | None:
            r = int(round(x))
            for j in (r, r - 1, r + 1):
                if 0 <= j < n and is_peak[j]:
                    cj, _ = self._centroid(vals, j)
                    if abs(cj - x) <= tol:
                        return j
            return None

        partners: list[int] = []
        found: dict[int, int] = {}
        n_up = 0
        for kh, off in _HARM_OFFSETS:
            if c + off > n - 1.5:
                break
            j = peak_near(c + off)
            if j is not None:
                n_up += 1
                partners.append(j)
                found[kh] = j
        # Odd-dominant partials (3f, 5f with 2f absent or well below 3f, no 4f) on a line far above the rest of the
        # spectrum are the signature of symmetric clipping downstream (amp rails, driver excursion: loop brief
        # §2.2), not of an instrument (whose 2f is at or above 3f) — square-wave synths and clarinets excepted.
        ic = int(round(c))
        if (n_up >= 2 and 3 in found and 4 not in found and 0 <= ic < n
                and (2 not in found or vals[found[2]] < vals[found[3]] - 6.0)
                and vals[ic] >= self._refs[-1] + 18.0):
            n_up = 1 if 2 in found else 0
            partners = [found[2]] if 2 in found else []
        sub = False
        # c = 2·f : f at c-10 with another partial of f (3f at c+5.85, 5f at c+13.22; 4f = 2c is ambiguous)
        if c - 10.0 >= 1.0:
            jf = peak_near(c - 10.0)
            if jf is not None:
                jo = peak_near(c + 5.85)
                if jo is None:
                    jo = peak_near(c + 13.22)
                if jo is not None:
                    sub = True
                    partners.extend([jf, jo])
        # c = 3·f : f at c-15.85 with 2f (c-5.85), 4f (c+4.15) or 5f (c+7.37)
        if not sub and c - 15.85 >= 1.0:
            jf = peak_near(c - 15.85)
            if jf is not None:
                jo = peak_near(c - 5.85)
                if jo is None:
                    jo = peak_near(c + 4.15)
                if jo is None:
                    jo = peak_near(c + 7.37)
                if jo is not None:
                    sub = True
                    partners.extend([jf, jo])
        # c = 4·f : f at c-20 with 2f (c-10) or 3f (c-4.15)
        if not sub and c - 20.0 >= 1.0:
            jf = peak_near(c - 20.0)
            if jf is not None:
                jo = peak_near(c - 10.0)
                if jo is None:
                    jo = peak_near(c - 4.15)
                if jo is not None:
                    sub = True
                    partners.extend([jf, jo])
        return n_up, sub, partners

    def _octave_below_band(self, c: float, is_peak: Sequence[bool], vals: Sequence[float]) -> int | None:
        """The peak band within ``family_tol_bands`` of exactly one octave below ``c`` (the line would be its 2nd
        harmonic), or None."""
        x = c - 10.0
        if x < 1.0:
            return None
        r = int(round(x))
        for j in (r, r - 1, r + 1):
            if 0 <= j < len(is_peak) and is_peak[j]:
                cj, _ = self._centroid(vals, j)
                if abs(cj - x) <= self.cfg.family_tol_bands:
                    return j
        return None

    def _independent(self, t: Candidate, j: int) -> bool:
        """True when band ``j`` (a would-be partial partner) swung by >= 8 dB over the track's recent life while the
        track's own level stayed within 3 dB: a note came or went under a line that did not care."""
        m = min(len(t.kf), 40)
        if m < 8:
            return False
        n = len(self.band_hz)
        lv = t.levels[-m:]
        if max(lv) - min(lv) > 3.0:
            return False
        lo = hi = None
        lo_row = None
        for kk in t.kf[-m:]:
            i = kk - self._k0
            if i < 0 or i >= len(self._vals):
                continue
            row = self._vals[i]
            v = max(row[jj] for jj in (j - 1, j, j + 1) if 0 <= jj < n)
            if lo is None or v < lo:
                lo, lo_row = v, row
            hi = v if hi is None or v > hi else hi
        if lo is None or hi - lo < 8.0:
            return False
        # ... and at its quietest the partner was actually gone (not a steady partial with a passing note on top)
        k3 = self.cfg.neighbour_bins
        neigh = [lo_row[jj] for jj in range(max(0, j - k3), min(n, j + k3 + 1)) if jj != j]
        return lo_row[j] - median(neigh) < self.cfg.family_prominence_db

    def _family_comoving(self, t: Candidate, start_idx: int, k: int, is_peak: Sequence[bool],
                         vals: Sequence[float]) -> bool:
        """Family test for a *growing* line. A note's partials share one envelope: they rise by the same number of
        dB (ratio within [0.5, 2]) and their tracks were born within one analyser rise time of each other. A
        programme line that merely sits at a harmonic position does not follow a ring up, and a second ring an
        octave away starts at its own time and grows at its own rate (loop brief §2.3)."""
        cfg = self.cfg
        n_up, sub, partners = self._family_now(t.centroid, is_peak, vals)
        octave_up = n_up >= 1 and abs(partners[0] - (t.centroid + 10.0)) <= 1.5
        jb = None
        if not sub:
            x = t.centroid - 10.0
            r = int(round(x))
            for j in (r, r - 1, r + 1):
                if 1 <= j < len(vals) and is_peak[j]:
                    cj, _ = self._centroid(vals, j)
                    if abs(cj - x) <= cfg.family_tol_bands:
                        jb = j
                        break
        if n_up < cfg.family_partials and not sub and not octave_up and jb is None:
            return False
        k_start = t.kf[start_idx]
        lv = t.levels
        a = lv[start_idx:start_idx + 3]
        rise = median(lv[-3:]) - median(a) if len(a) >= 2 and len(lv) >= 3 else lv[-1] - lv[start_idx]
        if rise < 0.75 * cfg.growth_slow_total_db or k_start < self._k0:
            # cannot judge co-movement: presence of a full family decides, a lone octave partner does not
            return n_up >= cfg.family_partials or sub
        i0 = max(0, k_start - 6 - self._k0)          # partners that climbed out of the bed a little earlier count
        rows0 = self._vals[i0:i0 + 3]
        rows1 = self._vals[-3:]
        nb = len(vals)

        def lvl(rows: Sequence[Sequence[float]], j: int) -> float:
            return median(max(r[jj] for jj in (j - 1, j, j + 1) if 0 <= jj < nb) for r in rows)

        def track_near(j: int) -> Candidate | None:
            best = None
            for u in self._cands:
                if u is not t and abs(u.centroid - j) <= 1.0 and (best is None or abs(u.centroid - j) < abs(best.centroid - j)):
                    best = u
            return best

        def level_near(band: int, kk: int) -> float | None:
            xs = [self._hist_level(b, kk) for b in (band - 1, band, band + 1)]
            xs = [x for x in xs if x is not None]
            return max(xs) if xs else None

        def comoved(j: int, lone: bool = False) -> bool:
            d = lvl(rows1, j) - lvl(rows0, j)
            # a weaker partial climbs out of the bed later than the fundamental, so its visible rise is smaller:
            # ask for half the candidate's rise or a clear 4 dB, whichever is less
            if not (max(1.5, min(0.5 * rise - 2.0, 4.0)) <= d <= 2.0 * rise + 3.0):
                return False
            if not lone:
                return True
            # A lone exact-octave partner (no 3f/4f/5f) is the < 1 % coincidence for two rings but the norm for
            # two-partial timbres (analyser brief §4.3: "require >= 2 partners or H2^H3"). It vetoes growth only when
            # it is indistinguishable from one swelling source: the two keep their level ratio and dB slope (shared
            # envelope) and the younger appeared no later than its level deficit explains.
            u = track_near(j)
            if u is None:
                return False
            # onset first: a partial cannot appear later than its level deficit divided by the swell rate
            tt0 = [(x, y) for x, y in zip(t.kf, t.levels)][-6:]
            rate0 = max(_ls_slope([float(x) for x, _ in tt0], [y for _, y in tt0]) if len(tt0) >= 3 else 0.0, 0.05)
            if abs(u.kf[0] - t.kf[0]) > max(2.0, abs(t.levels[-1] - u.levels[-1]) / rate0 + 2.0):
                return False
            kb = max(t.kf[0], u.kf[0]) + 1           # skip the frame where the younger is still half in the bed
            tt = [(x, y) for x, y in zip(t.kf, t.levels) if x >= kb]
            uu = [(x, y) for x, y in zip(u.kf, u.levels) if x >= kb]
            if len(tt) < 4 or len(uu) < 4:
                return True                          # too early to tell them apart: hold the veto a few frames
            slope_t = _ls_slope([float(x) for x, _ in tt], [y for _, y in tt])   # dB/frame
            slope_u = _ls_slope([float(x) for x, _ in uu], [y for _, y in uu])
            if slope_t > 0.15 and not (0.85 <= slope_u / slope_t <= 1.18):
                return False
            if self._settle_frames[min(u.band, t.band)] <= 3:
                lt0, lu0 = level_near(t.band, kb), level_near(u.band, kb)
                if lt0 is not None and lu0 is not None and k - kb >= 3:
                    if abs((t.levels[-1] - u.levels[-1]) - (lt0 - lu0)) > cfg.family_ratio_tol_db:
                        return False
            return True

        ups = [j for j in partners[:n_up] if comoved(j)]
        if len(ups) >= cfg.family_partials:
            return True
        if sub:
            subs = partners[n_up:]
            if subs and all(comoved(j) for j in subs):
                return True
        if octave_up and comoved(partners[0], lone=True):
            return True
        if jb is not None and comoved(jb, lone=True):
            return True
        return False

    # -- growth ---------------------------------------------------------------------------------

    def _growth(self, t: Candidate) -> tuple[str, float, float, int]:
        """Evaluate the loop-growth signature on the track's current ramp.

        Returns (kind, net_rise_db, slope_db_per_s, start_index) with kind in {"fast", "slow", ""}.
        """
        cfg = self.cfg
        lv = t.levels
        n = len(lv)
        gs = t.grow_start
        settle = self._settle_frames[min(max(t.band, 0), len(self._settle_frames) - 1)]
        prior_max = max(lv[max(0, gs - 40):gs]) if gs > 0 else None   # what this line had already reached before the ramp
        slope_report = 0.0
        if n - gs >= 2:
            i0 = max(gs, n - 20)
            slope_report = _ls_slope(t.ts_list[i0:], lv[i0:])
        # ---- FAST: exponential growth resolved frame by frame (>= 2 dB/frame) ----------------------
        # steps considered: the last <=5 inside the ramp, plus the step up from the pre-birth level when the
        # ramp starts at birth (so a line that shot out of the bed in 3 frames carries its full rise)
        pts = lv[max(gs, n - 6):]
        virtual = False
        if gs == 0 and n <= 6 and t.pre_level_db is not None and not t.est:
            pts = [t.pre_level_db] + list(pts)
            virtual = True
        j0 = max(gs, n - 6)
        contiguous = n < 2 or (t.kf[-1] - t.kf[j0] == (n - 1 - j0))   # frame-by-frame steps need consecutive frames
        if len(pts) >= 4 and contiguous and (n - 1) >= min(settle, 3) and (settle <= 3 or n - 1 - gs >= settle):
            diffs = [b - a for a, b in zip(pts, pts[1:])]
            diffs = diffs[-5:]
            base_i = len(pts) - 1 - len(diffs)
            tot = pts[-1] - pts[base_i]
            i_ref0 = max(0, n - 1 - len(diffs)) if not virtual else 0
            dref = t.ref_list[-1] - t.ref_list[i_ref0]
            net = tot - max(0.0, dref)
            srt = sorted(diffs, reverse=True)
            big = srt[0]
            top2 = srt[0] + (srt[1] if len(srt) > 1 else 0.0)
            cnt = sum(1 for d in diffs if d >= max(cfg.growth_fast_step_db, 0.15 * tot))
            # exponential growth = at least three comparable steps; an instrument onset is one or two frames of
            # rise (attack + one partially integrated frame) however the noise around it falls
            if (cnt >= 3 and net >= cfg.growth_fast_total_db and big <= cfg.pop_step_frac * tot
                    and top2 <= 0.75 * tot and diffs[-1] >= 0.5 * cfg.growth_fast_step_db
                    and (prior_max is None or lv[-1] >= prior_max + 3.0)):
                start_idx = max(gs, n - 1 - len(diffs))
                return "fast", net, slope_report, start_idx
        # ---- SLOW: least-squares ramp sustained over >= max(growth_slow_frames, settle) intervals --------
        avail = n - 1 - gs
        m_min = max(cfg.growth_slow_frames, settle)
        step_k = self._recent_step_k

        def ramp(ts: Sequence[float], raw: Sequence[float], kfs: Sequence[int], vs: Sequence[float],
                 i0: int, m: int) -> tuple[bool, float, float]:
            """One candidate window, one smoothed series: is it a sustained dB-linear climb?"""
            s, c0 = _ls_fit(ts, vs)
            if s < cfg.growth_slow_min_db_per_s:
                return False, 0.0, s
            T = ts[-1] - ts[0]
            rise = min(s * T, max(vs[-2:]) - min(vs[:2]))
            # common-mode reference over the same span, 5-frame medians at each end (the median of ~60 noise-like
            # bands still wanders by ~0.5 dB frame to frame)
            r_lo = max(0, i0 - 2)
            dref = median(t.ref_list[-5:]) - median(t.ref_list[r_lo:r_lo + 5])
            net = rise - max(0.0, dref)
            # the longer a fixed-frequency, family-less line has climbed dB-linearly, the less rise it takes to
            # exclude wander (±0.5 dB) and expressive swells (≈1 dB over a note): 6 dB inside 1 s, 4 dB over >= 2 s
            need_total = cfg.growth_slow_total_db if T < 1.0 else max(cfg.growth_slow_total_long_db,
                                                                      cfg.growth_slow_total_db - (T - 1.0) * 2.0)
            if step_k is not None and 0 <= kfs[0] - step_k <= 8:
                # a ramp that starts within 0.4 s of the server's own gain step is time-locked to it: that is the
                # moment a latent loop crosses threshold (loop brief §4.3 "certain" tier)
                need_total = cfg.growth_slow_total_long_db
            if net < need_total:
                return False, net, s
            # linear in dB: residuals small against the rise
            resid = [v - (c0 + s * x) for x, v in zip(ts, vs)]
            if max(abs(r) for r in resid) > max(1.5, 0.25 * rise):
                return False, net, s
            # onset guard on the RAW series: one frame step carrying most of the rise *and holding* is an
            # instrument onset (a transient spike that falls back is not, and is what the smoothing removed)
            span = max(rise, raw[-1] - raw[0], 1e-6)
            for i in range(len(raw) - 1):
                st = (raw[i + 1] - raw[i]) / max(1, kfs[i + 1] - kfs[i])   # per frame (a masked gap is not a step)
                if st > cfg.pop_step_frac * span and min(raw[i + 1:i + 4]) > raw[i] + 0.45 * span:
                    return False, net, s
            # exponential growth is spread evenly over the frames; a ratchet of re-plucked strings or repeated
            # syllables piling into one band climbs in a few discrete steps with flats between
            fsteps = sorted(((b - a) / max(1, kb - ka) for a, b, ka, kb in zip(vs, vs[1:], kfs, kfs[1:])), reverse=True)
            if len(fsteps) >= 5 and sum(x for x in fsteps[:2] if x > 0) > 0.5 * max(rise, 1e-6):
                return False, net, s
            # sustained: every third of the window rising (not an attack that has already flattened, not the
            # concave step response of a slow analyser band, not a fader ramp that has ended) ...
            h = max(2, len(vs) // 3)
            s1 = _ls_slope(ts[: h + 1], vs[: h + 1])
            s2 = _ls_slope(ts[h: 2 * h + 1], vs[h: 2 * h + 1])
            s3 = _ls_slope(ts[-(h + 1):], vs[-(h + 1):])
            if s3 < 0.4 * s or s2 < 0.15 * s or s1 < 0.15 * s:
                return False, net, s
            # ... and each half carrying its share of the climb (a 2-4 frame analyser-limited LF onset followed by a
            # flat note fits a line tolerably but puts the whole rise in one place)
            mid = len(vs) // 2
            vmid = median(vs[max(0, mid - 1): mid + 2])
            h1 = vmid - median(vs[:3])
            h2 = median(vs[-3:]) - vmid
            if h1 < max(0.75, 0.25 * rise) or h2 < max(0.75, 0.3 * rise):
                return False, net, s
            if prior_max is not None and lv[-1] < prior_max + 3.0:
                return False, net, s   # climbing back to where it already was (tremolo, beating, a sag) is not growth
            # rise time: a dB-linear ramp takes ~65 % of the window to go from 25 % to 90 % of its climb; the step
            # response of a slow analyser band (or an attack) does it in a few frames wherever it sits in the window
            base = median(vs[:3])
            i25 = next((i for i, v in enumerate(vs) if v >= base + 0.25 * rise), len(vs) - 1)
            i90 = next((i for i, v in enumerate(vs) if v >= base + 0.9 * rise), len(vs) - 1)
            if (i90 - i25) < 0.4 * m:
                return False, net, s
            return True, net, s

        if avail >= m_min:
            tried: set[int] = set()
            for m in (m_min, 8, 12, 20, 40, cfg.growth_window_frames, avail):
                if m < m_min or m > avail or m in tried:
                    continue
                tried.add(m)
                i0 = n - 1 - m
                ts = t.ts_list[i0:]
                raw = lv[i0:]
                kfs = t.kf[i0:]
                if kfs[-1] - kfs[0] > m + max(2, m // 4) or ts[-1] <= ts[0]:
                    continue         # a line above the bed is there every frame; a patchy history is not one ramp
                # two smoothings of the same history: a 3-point median (a one-frame transient on top of the line is
                # not part of its envelope) and a trailing 3-point minimum — the line's *own* level is the lower
                # envelope of the band, because programme sharing the band can only ever add power to it
                med3 = [raw[0]] + [sorted(raw[i - 1:i + 2])[1] for i in range(1, len(raw) - 1)] + [raw[-1]]
                ok, net, s_ = ramp(ts, raw, kfs, med3, i0, m)
                if ok:
                    return "slow", net, s_, i0
                low3 = [min(raw[max(0, i - 2):i + 1]) for i in range(len(raw))]
                ok, net, s_ = ramp(ts, raw, kfs, low3, i0, m)
                if ok:
                    return "slow", net, s_, i0
        return "", 0.0, slope_report, gs

    # -- probe ----------------------------------------------------------------------------------

    def _probe_update(self, k: int) -> None:
        cfg = self.cfg
        if not self._steps:
            return
        ts_now = self._tss[-1]
        for st in self._steps:
            if st.k is None and ts_now >= st.ts - 1e-9:
                st.k = k
                if st.delta_db > 0:
                    self._recent_step_k = k
        N = cfg.probe_window_frames
        settle = cfg.probe_settle_frames
        for idx, st in enumerate(self._steps):
            if st.done or st.k is None:
                continue
            nxt = None
            if idx + 1 < len(self._steps) and self._steps[idx + 1].k is not None:
                nxt = self._steps[idx + 1].k
            a0 = st.k + settle
            a1 = a0 + N - 1
            if nxt is not None:
                a1 = min(a1, nxt - 1)
            if k < a1:
                continue
            st.done = True
            if a1 - a0 + 1 < 4:
                continue
            b1 = st.k - 1
            b0 = max(self._k0, b1 - N + 1)
            if b1 - b0 + 1 < 4 or a0 < self._k0:
                continue
            refs_b = [self._refs[kk - self._k0] for kk in range(b0, b1 + 1)]
            refs_a = [self._refs[kk - self._k0] for kk in range(a0, a1 + 1) if kk - self._k0 < len(self._refs)]
            if len(refs_a) < 4:
                continue
            dref = median(refs_a) - median(refs_b)
            d = st.delta_db
            sgn = 1.0 if d > 0 else -1.0
            expected = sgn * max(sgn * dref, sgn * d, 0.0)   # the larger of "spectrum moved" and "1 dB per dB"
            nwin = b1 - b0 + 1
            refs_0 = [self._refs[kk - self._k0] for kk in range(b0 - nwin, b0) if 0 <= kk - self._k0 < len(self._refs)]
            for t in self._cands:
                if t.born_frame > st.k - cfg.probe_min_history_frames:
                    continue
                before = [lv for kk, lv in zip(t.kf, t.levels) if b0 <= kk <= b1]
                after = [lv for kk, lv in zip(t.kf, t.levels) if a0 <= kk <= a1]
                if len(before) < 3 or len(after) < 3:
                    continue
                if max(after) - min(after) > 12.0 or max(before) - min(before) > 12.0:
                    continue   # a note changed under the window: not a level comparison
                exc = sgn * ((median(after) - median(before)) - expected)
                # control: how much does this line move across a window boundary with NO step? (a room mode driven by
                # a bass line, or any programme line, jumps by whole notes; a stationary or noise-excited line does
                # not). The response counts only if it clearly exceeds that null movement.
                earlier = [lv for kk, lv in zip(t.kf, t.levels) if b0 - nwin <= kk < b0]
                if len(earlier) >= 3 and len(refs_0) >= 3:
                    null = abs((median(before) - median(earlier)) - (median(refs_b) - median(refs_0)))
                    significant = abs(exc) >= 2.0 * null + 1.0
                else:
                    significant = False       # too young to have a control window: no verdict either way
                t.probe_steps += 1
                t.probe_last_excess_db = exc
                if exc >= cfg.probe_excess_db and significant:
                    t.probe_hits += 1
                    t.probe_linear = 0
                elif exc <= cfg.probe_linear_db and significant:
                    t.probe_linear += 1
                    t.probe_hits = 0
                elif exc <= cfg.probe_linear_db:
                    t.probe_hits = 0
        # forget fully evaluated old steps
        while len(self._steps) > 8 and self._steps[0].done:
            del self._steps[0]

    # -- main entry -------------------------------------------------------------------------------

    def feed(self, values_db: Sequence[float], ts: float) -> list[Detection]:
        """Process one RTA frame (``len(values_db) == len(band_hz)``, dB) taken at time ``ts`` (s).

        Returns the detections emitted on this frame (possibly empty). Raises ``ValueError`` on a
        frame of the wrong length.
        """
        cfg = self.cfg
        vals = [float(v) for v in values_db]
        n = len(self.band_hz)
        if len(vals) != n:
            raise ValueError(f"expected {n} RTA values, got {len(vals)}")
        k = self.frames_seen
        ts = float(ts)

        # ---- spectrum history and reference -------------------------------------------------------
        ref = median(vals[self._ref_lo:self._ref_hi + 1])
        self._vals.append(vals)
        self._tss.append(ts)
        self._refs.append(ref)
        if len(self._vals) > self.HIST:
            del self._vals[0]
            del self._tss[0]
            del self._refs[0]
            self._k0 += 1

        prom = self.prominences(vals)
        is_peak = [False] * n
        for i in range(n):
            left = vals[i - 1] if i > 0 else -1e9
            right = vals[i + 1] if i + 1 < n else -1e9
            if vals[i] > left and vals[i] >= right and prom[i] >= cfg.family_prominence_db:
                is_peak[i] = True

        # ---- lines in this frame ------------------------------------------------------------------
        # hysteresis: a line is *born* at track_prominence_db / half the narrowness, but an existing track keeps
        # hold of its local maximum down to half of that (a faint emerging line flickers about the threshold)
        track_narrow = 0.5 * cfg.narrow_db
        weak_prom = 0.5 * cfg.track_prominence_db
        clusters: list[tuple[int, float, float, float, float]] = []   # (band, centroid, Lpeak, prom, narrow)
        strong: list[bool] = []
        for i in range(1, n - 1):
            if vals[i] < cfg.track_min_level_db or prom[i] < weak_prom:
                continue
            if not (vals[i] > vals[i - 1] and vals[i] >= vals[i + 1]):
                continue
            nar = self._narrowness(vals, i)
            if nar < 0.5 * track_narrow:
                continue
            c, _lc = self._centroid(vals, i)
            clusters.append((i, c, vals[i], prom[i], nar))
            strong.append(bool(is_peak[i] and prom[i] >= cfg.track_prominence_db and nar >= track_narrow))

        # ---- match to tracks ----------------------------------------------------------------------
        # nearest-centroid assignment (closest pairs first), then hops for tracks left without a line
        used: set[int] = set()
        tolc = max(1.0, float(cfg.band_tolerance)) + 0.35   # a hop of up to ~160 c re-locks the same track
        pairs: list[tuple[float, int, int]] = []
        for ti, t in enumerate(self._cands):
            for j, (b, c, lp, pr, nar) in enumerate(clusters):
                dd = abs(c - t.centroid)
                if dd <= tolc:
                    pairs.append((dd, ti, j))
        pairs.sort()
        assigned: dict[int, int] = {}
        for dd, ti, j in pairs:
            if ti in assigned or j in used:
                continue
            assigned[ti] = j
            used.add(j)
        survivors: list[Candidate] = []
        released: set[int] = set()
        for ti, t in enumerate(self._cands):
            best_j = assigned.get(ti, -1)
            if best_j >= 0 and t.cen_list:
                cl = sorted(t.cen_list[-10:])
                cnow = clusters[best_j][1]
                drifted = t.cen0 is not None and abs(cnow - t.cen0) > cfg.centroid_tol_bands + 0.2
                if abs(cnow - cl[len(cl) // 2]) > cfg.centroid_tol_bands or drifted:
                    # the line re-appeared a semitone or more away. A ring we are already cutting that re-locks on
                    # the neighbouring loop candidate (hand-held mic, loop brief §2.3) keeps its verdict; anything
                    # else is a different line that must earn its own history — release the cluster so it is born
                    # (and back-filled) as a new track, and let this one lapse.
                    if t.feedback:
                        t.hops += 1
                        t.cen_list.clear()
                        t.cen0 = cnow
                    else:
                        used.discard(best_j)
                        released.add(best_j)
                        best_j = -1
            if best_j < 0:
                t.coast += 1
                if t.coast <= 1:
                    survivors.append(t)     # one missing frame is display/noise flicker, not the end
                else:
                    self._grave.append((k, t))
                continue
            b, c, lp, pr, nar = clusters[best_j]
            t.coast = 0
            self._extend(t, k, ts, b, c, lp, pr, nar, ref, is_peak)
            survivors.append(t)
        # new lines
        self._grave = [(kd, g) for kd, g in self._grave if k - kd <= self.GRAVE_FRAMES]
        for j, (b, c, lp, pr, nar) in enumerate(clusters):
            if j in used or not strong[j]:
                continue
            t = None
            for gi, (kd, g) in enumerate(self._grave):
                if abs(g.centroid - c) > 1.0 or not g.kf:
                    continue
                # where would that line be now?  (a masker — cymbal crash, consonant burst — can hide a growing ring
                # for a few frames; it re-emerges on the same ramp)
                expect = g.level_db
                seg_k = [kk for kk in g.kf[-6:]]
                if len(seg_k) >= 3 and g.kf[-1] - seg_k[0] >= 2:
                    sl = _ls_slope([float(x) for x in seg_k], g.levels[-len(seg_k):])   # dB per frame
                    expect = g.level_db + max(0.0, sl) * (k - g.kf[-1])
                if abs(expect - lp) <= 6.0 or abs(g.level_db - lp) <= 6.0:
                    t = g
                    del self._grave[gi]
                    t.coast = 0
                    if k - t.kf[-1] > 10:
                        t.grow_start = len(t.levels)   # a gap this long (0.5 s) breaks a ramp
                    break
            if t is None:
                t = Candidate(band=b, first_ts=ts, born_frame=k, centroid=c)
                t.pre_level_db = self._pre_level(b, k)
                self._backfill(t, b, k, tol=4.0 if j in released else 1.0)
                if k < cfg.est_arm_frames or (pr >= cfg.prominence_db and self._present_since_arm(b, lp)):
                    t.est = True
                    t.birth = "est"
                elif t.pre_level_db is not None and lp - t.pre_level_db < 6.0:
                    t.birth = "static"
                else:
                    t.birth = "new"
            self._extend(t, k, ts, b, c, lp, pr, nar, ref, is_peak)
            survivors.append(t)
        survivors.sort(key=lambda t: t.centroid)
        self._cands = survivors

        # ---- probe bookkeeping --------------------------------------------------------------------
        self._probe_update(k)

        # ---- decide -------------------------------------------------------------------------------
        out: list[Detection] = []
        for t in self._cands:
            if t.coast:
                continue
            verdict = self._classify(t, k, ts, is_peak, vals)
            t.verdict = verdict or t.verdict if not verdict else verdict
            if not verdict:
                continue
            if t.frames < cfg.persistence_frames:
                continue
            if self._in_cooldown(t.band, ts) or ts - t.last_emit_ts < cfg.cooldown_s - 1e-9:
                continue
            det = Detection(
                ts=ts, band=t.band, freq_hz=t.freq_hz, level_db=t.level_db, prominence_db=t.prominence_db,
                slope_db_per_s=t.slope_db_per_s, frames=t.frames, confidence=t.confidence, reasons=t.reasons,
            )
            out.append(det)
            t.emitted += 1
            t.feedback = True
            if t.birth == "new":
                t.birth = "grow"
            t.last_emit_ts = ts
            self._cooldown[t.band] = ts + cfg.cooldown_s
            log.debug("feedback: band %d (%.0f Hz) %.1f dB prom %.1f [%s]", t.band, t.freq_hz, t.level_db,
                      t.prominence_db, ",".join(t.reasons))
        if self._cooldown and k % 100 == 0:
            self._cooldown = {b: u for b, u in self._cooldown.items() if ts < u}
        if k % 20 == 19:
            self._diagnose()
        self.frames_seen += 1
        self.last_ts = ts
        return out

    def _backfill(self, t: Candidate, b: int, k: int, tol: float = 1.0) -> None:
        """Give a newborn track the recent frames in which its band was already a local maximum (the analyser showed
        the line before it was prominent enough to be promoted to a track): a fast ring in a loud mix shows only 2-4
        frames of growth and none may be wasted; a slow one may have been creeping up for seconds under the music.
        The walk back stops at the pre-birth floor (a line that came out of the bed) or where the band stops being a
        local maximum for more than two frames (masked or absent)."""
        n = len(self.band_hz)
        floor = t.pre_level_db
        cur = self._vals[-1][b] if self._vals else None
        if floor is not None and cur is not None and cur < floor + 6.0:
            floor = None                     # the line did not come out of that level: it *was* that level
        rows: list[tuple[int, float]] = []
        misses = 0
        for j in range(1, self.HIST):
            kk = k - j
            i = kk - self._k0
            if i < 0 or i >= len(self._vals) - 1:
                break
            row = self._vals[i]
            v = max(row[bb] for bb in (b - 1, b, b + 1) if 0 <= bb < n)
            vb = row[b]
            is_max = vb >= v - tol          # the band itself (tol 1) or, for a line that slid off a programme partial's
                                            # track, the cluster it was part of (tol 4)
            above = floor is None or vb >= floor + 3.0
            if is_max and above and vb > -100.0:
                rows.append((kk, vb))
                misses = 0
            else:
                misses += 1
                if misses > 4 or (floor is not None and vb < floor + 3.0):
                    break
        if not rows:
            return
        for kk, v in reversed(rows):
            i = kk - self._k0
            t.kf.append(kk)
            t.ts_list.append(self._tss[i])
            t.levels.append(v)
            t.cen_list.append(float(b))
            t.ref_list.append(self._refs[i])
            t.fam_list.append(False)
            t.fam_strong.append(False)
        t.born_frame = rows[-1][0]
        t.first_ts = self._tss[rows[-1][0] - self._k0]
        t.frames = len(rows)
        pl = self._pre_level(b, t.born_frame)
        if pl is not None:
            t.pre_level_db = pl

    def _extend(self, t: Candidate, k: int, ts: float, band: int, c: float, lp: float, pr: float, nar: float,
                ref: float, is_peak: Sequence[bool]) -> None:
        cfg = self.cfg
        t.frames += 1
        t.band = band
        t.centroid = c
        t.freq_hz = self.freq_of(c)
        t.level_db = lp
        t.prominence_db = pr
        t.narrow_db = nar
        t.last_ts = ts
        # ramp restart on a real drop (against the recent typical level, so a one-frame transient sitting on top of
        # the line — a hi-hat, a consonant — does not reset a slow ramp when it goes away)
        if t.levels:
            seg = t.levels[t.grow_start:]
            if seg:
                recent = sorted(seg[-5:])
                typical = recent[len(recent) // 2]
                earlier = max(median(seg[i:i + 3]) for i in range(0, max(1, len(seg) - 2), 3)) if len(seg) >= 3 else typical
                if lp < max(typical, earlier) - cfg.growth_drop_tol_db:
                    t.grow_start = len(t.levels)   # the new point starts the next ramp
        t.kf.append(k)
        t.ts_list.append(ts)
        t.levels.append(lp)
        t.cen_list.append(c)
        t.ref_list.append(ref)
        if t.frames <= 5:
            cl0 = sorted(t.cen_list[-t.frames:]) if t.frames >= 1 else [c]
            t.cen0 = cl0[len(cl0) // 2]
        elif t.feedback and t.hops and not t.cen_list[:-1]:
            t.cen0 = c                      # re-locked ring: its new home
        vals_now = self._vals[-1]
        n_up0, sub, partners = self._family_now(c, is_peak, vals_now)
        # a partner whose level swung by a note's worth while this line did not move is not this line's partial
        # (the chord changed underneath a ring: "outlasts programme structure", loop brief P12)
        ups = [j for j in partners[:n_up0] if not self._independent(t, j)]
        n_up = len(ups)
        if sub and self._independent(t, partners[n_up0]):
            sub = False
        # An exact-octave partner (2f or f/2 within family_tol_bands) is family for a *steady* line: two coexisting
        # independent rings land within ±50 c of an octave < 1 % of the time (loop brief §2.3), an organ 8'+4', a
        # flute and every two-partial timbre do it always. Growing lines are judged with co-movement instead.
        jb = self._octave_below_band(c, is_peak, vals_now)
        octave = (n_up >= 1 and abs(ups[0] - (c + 10.0)) <= 1.5) or (jb is not None and not self._independent(t, jb))
        strong = bool(n_up >= cfg.family_partials or sub)
        t.fam_list.append(strong or octave)
        t.fam_strong.append(strong)
        cap = max(cfg.growth_window_frames + 4, 24)
        if len(t.levels) > cap:
            drop = len(t.levels) - cap
            del t.kf[:drop], t.ts_list[:drop], t.levels[:drop], t.cen_list[:drop], t.ref_list[:drop], t.fam_list[:drop]
            del t.fam_strong[:drop]
            t.grow_start = max(0, t.grow_start - drop)
        if len(t.cen_list) > 12:
            del t.cen_list[: len(t.cen_list) - 12]
        # birth class: an onset that arrived in one step is an instrument (or a >500 dB/s ring — irreducible)
        if t.birth == "new" and t.frames >= 2 and t.pre_level_db is not None:
            total = lp - t.pre_level_db
            first = t.levels[0] - t.pre_level_db if len(t.levels) <= 6 else 0.0
            steps = [t.levels[0] - t.pre_level_db] + [b - a for a, b in zip(t.levels, t.levels[1:])]
            if total >= 6.0 and max(steps[:3]) >= cfg.pop_step_frac * total and t.frames <= 4:
                t.birth = "pop"
            elif t.frames >= 8 and total < 3.0:
                t.birth = "static"
            del first

    # -- classification -----------------------------------------------------------------------------

    def _classify(self, t: Candidate, k: int, ts: float, is_peak: Sequence[bool], vals: Sequence[float]) -> str:
        """Return the emitting rule name ('' = candidate only) and update the track's report fields."""
        cfg = self.cfg
        f = t.freq_hz
        lv = t.levels
        n = len(lv)
        lp = lv[-1]
        reasons: list[str] = []

        # temporal predicates
        recent_max = max(lv[-20:])
        decaying = lp < recent_max - cfg.decay_tol_db
        cl = t.cen_list[-10:]
        cen_range = (max(cl) - min(cl)) if len(cl) >= 2 else 0.0
        stationary = cen_range <= cfg.centroid_tol_bands
        diffs = [b - a for a, b in zip(lv[-7:], lv[-6:])]
        steady = (len(diffs) >= 3 and _sd(diffs) <= cfg.level_unsteady_db
                  and max(abs(d) for d in diffs) <= 2.5 * cfg.level_unsteady_db)
        fl = t.fam_list[-10:]
        fam_frac = sum(fl) / len(fl) if fl else 0.0
        family = fam_frac >= cfg.family_veto_frac and len(fl) >= 2
        fs = t.fam_strong[-10:]
        fam_strong_frac = sum(fs) / len(fs) if fs else 0.0
        narrow = t.narrow_db >= cfg.narrow_db
        prominent = t.prominence_db >= cfg.prominence_db
        in_window = (f >= cfg.lf_edge_hz) and (f <= cfg.hf_edge_hz)
        lf_strict = f < cfg.lf_strict_hz
        kind, net, slope, gi0 = self._growth(t)
        t.slope_db_per_s = slope
        t.growth_score = min(1.0, net / cfg.growth_fast_total_db) if kind else 0.0

        # onset synchrony: several lines born within ±2 frames = a programme event (chord, crash, song start)
        sync = 0
        if not t.est:
            for u in self._cands:
                if u is not t and u.frames >= 3 and abs(u.born_frame - t.born_frame) <= 2 and not u.est:
                    du = u.centroid - t.centroid
                    if any(abs(du - off) <= 0.6 for _, off in _HARM_OFFSETS):
                        continue    # its own partial/distortion product: the family test's business, not sync
                    sync += 1

        verdict = ""
        if not in_window:
            verdict = ""
        elif t.feedback:
            # sticky: keep reporting a line we already called feedback while it is there and not dying away
            if not decaying and prominent and t.narrow_db >= 0.5 * cfg.narrow_db:
                verdict = "sustained"
                reasons = list(t.reasons[:1]) + ["sustained"]
        else:
            # programme verdicts: set on evidence, cleared when the line has outlived that evidence (a ring seeded
            # by a speech partial keeps the partial's track; the partial's cohort and family die with the syllable)
            if not t.musical:
                if sync >= 4 and t.birth == "pop":
                    t.musical = "sync-onset"
                elif t.frames >= 6 and fam_strong_frac >= 0.8 and t.birth in ("pop", "static", "est"):
                    t.musical = "harmonic-family"
                elif t.frames >= 10 and t.birth == "est" and sum(t.fam_list) >= 0.6 * len(t.fam_list) and len(t.fam_list) >= 10:
                    t.musical = "harmonic-family"   # incl. a steady exact-octave partner (organ 8'+4' voicing)
            elif t.musical == "sync-onset" and sync < 2 and not any(t.fam_list[-10:]) and len(t.fam_list) >= 10:
                t.musical = ""     # its onset cohort is gone and it shows no family: it was not part of that event
            elif t.musical == "harmonic-family" and not any(t.fam_list[-30:]) and len(t.fam_list) >= 30:
                t.musical = ""     # 1.5 s without any partner: the line has outlived the note it was taken for
            clip = lp >= cfg.clip_level_db
            loud = lp >= cfg.loud_level_db
            escape = lp >= cfg.family_escape_level_db
            probe_veto = t.probe_linear >= 2
            # for the level rules, stationarity/steadiness are judged over the plateau the line is sitting on (the
            # frames since it arrived within 3 dB of where it is now), not over the climb that brought it there
            pl = 0
            for x in reversed(lv):
                if abs(x - lp) <= 3.0:
                    pl += 1
                else:
                    break
            plat_ok = False
            if pl >= cfg.loud_frames:
                cpl = t.cen_list[-min(pl, len(t.cen_list)):]
                dpl = [b - a for a, b in zip(lv[-pl:], lv[-pl + 1:])] if pl >= 2 else []
                plat_ok = ((max(cpl) - min(cpl)) <= cfg.centroid_tol_bands
                           and (not dpl or max(abs(d) for d in dpl) <= 2.0 * cfg.level_unsteady_db))
            if clip and narrow and pl >= cfg.loud_frames and min(lv[-cfg.loud_frames:]) >= cfg.clip_level_db \
                    and not lf_strict and plat_ok:
                verdict = "clip"
                reasons = ["clip", "narrow", "stationary"]
            elif (loud and narrow and prominent and plat_ok and not decaying and not lf_strict
                  and pl >= cfg.loud_frames and min(lv[-cfg.loud_frames:]) >= cfg.loud_level_db
                  and (escape or not family) and not probe_veto and not (t.musical and not escape)):
                verdict = "loud"
                reasons = ["loud", "narrow", "stationary", "steady", "no-family" if not family else "near-clip"]
            elif (kind and narrow and stationary
                  and t.prominence_db >= cfg.grow_prominence_db
                  and not self._fc(t, gi0, k, is_peak, vals)):
                verdict = "grow-" + kind
                reasons = [verdict, f"+{net:.0f}dB", f"{slope:.0f}dB/s", "narrow", "stationary", "no-comoving-family"]
            elif (t.est and narrow and prominent and stationary and steady and not decaying and not family
                  and not lf_strict and lp >= cfg.est_min_level_db and t.frames >= cfg.est_frames
                  and abs(lp - median(lv[:5])) <= 4.0      # still the level it had at arm (not a note that landed on it)
                  and not probe_veto and not t.musical):
                verdict = "est"
                reasons = ["established-at-arm", "narrow", "no-family", "stationary", "steady",
                           f"level>={cfg.est_min_level_db:.0f}"]
            elif ((t.probe_hits >= 2 or (t.probe_hits >= 1 and t.probe_last_excess_db >= cfg.probe_strong_db))
                  and t.narrow_db >= 0.8 * cfg.narrow_db and t.prominence_db >= cfg.track_prominence_db + 3.0
                  and stationary and not decaying and not family and not t.musical):
                verdict = "probe"
                reasons = ["probe", f"+{t.probe_last_excess_db:.1f}dB/step", "narrow", "stationary", "no-family"]

        t.diag = {"narrow": narrow, "prom": prominent, "stat": stationary, "steady": steady, "decay": decaying,
                  "family": family, "famfrac": round(fam_frac, 2), "kind": kind, "net": round(net, 1), "sync": sync,
                  "cen_range": round(cen_range, 2), "sd": round(_sd(diffs), 2) if len(diffs) >= 2 else 0.0,
                  "fam_comov": getattr(t, "_fam_comov", None), "birth": t.birth, "mus": t.musical}
        # confidence: a monotone function of the margins, >= threshold iff emitting (dashboard contract)
        base = (0.25 * min(1.0, max(0.0, t.prominence_db) / cfg.prominence_db)
                + 0.15 * min(1.0, t.frames / max(1, cfg.est_frames))
                + 0.25 * t.growth_score)
        if verdict:
            margin = max(0.0, t.prominence_db - cfg.prominence_db) / 24.0 + t.growth_score / 2.0
            t.confidence = max(cfg.confidence_threshold, min(1.0, cfg.confidence_threshold + 0.3 * min(1.0, margin)))
            t.reasons = tuple(reasons)
        else:
            t.confidence = min(base, cfg.confidence_threshold - 0.01)
            if not t.reasons or not t.feedback:
                why = []
                if t.musical:
                    why.append("musical:" + t.musical)
                if family:
                    why.append("family")
                if not stationary:
                    why.append("moving")
                if decaying:
                    why.append("decaying")
                if t.birth == "pop":
                    why.append("popped-onset")
                t.reasons = tuple(why)
        return verdict

    def _fc(self, t: Candidate, gi0: int, k: int, is_peak: Sequence[bool], vals: Sequence[float]) -> bool:
        r = self._family_comoving(t, gi0, k, is_peak, vals)
        t._fam_comov = r
        return r

    def _diagnose(self) -> None:
        """Cheap analyser sanity hints (reported, never a gate): peak-hold leaves prominent bands bit-identical
        for many frames; a display gain offset lifts the whole spectrum toward full scale."""
        if len(self._vals) < 10:
            return
        rows = self._vals[-10:]
        n = len(self.band_hz)
        frozen = 0
        for b in range(n):
            col = [r[b] for r in rows]
            if col[0] > -90.0 and max(col) - min(col) == 0.0:
                frozen += 1
        self.flags["peak_hold_suspect"] = frozen >= 5
        self.flags["hot_spectrum"] = self._refs[-1] > -30.0


# ---------------------------------------------------------------------------------------------
# notch planning
# ---------------------------------------------------------------------------------------------


@dataclass
class Notch:
    """A GEQ cut. ``band`` is 1-based (par number), ``depth_db`` <= 0. ``session_id == ""`` and
    ``detections == 0`` mark a cut that pre-dates this session (from ``existing``)."""

    bus: int
    band: int
    freq_hz: float
    depth_db: float
    session_id: str
    ts: float
    detections: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "bus": self.bus,
            "band": self.band,
            "freq_hz": self.freq_hz,
            "depth_db": self.depth_db,
            "session_id": self.session_id,
            "ts": self.ts,
            "detections": self.detections,
        }


class GeqWriter(Protocol):
    async def set_band_gain(self, bus: int, band: int, gain_db: float) -> None:
        """Write ``gain_db`` (dB, -15..+15) to 1-based GEQ ``band`` of the GEQ serving ``bus``."""
        ...


class RecordingGeqWriter:
    """:class:`GeqWriter` that only records ``(bus, band, gain_db)`` — simulation and tests."""

    def __init__(self) -> None:
        self.writes: list[tuple[int, int, float]] = []
        self.gains: dict[tuple[int, int], float] = {}

    async def set_band_gain(self, bus: int, band: int, gain_db: float) -> None:
        self.writes.append((bus, band, gain_db))
        self.gains[(bus, band)] = gain_db


class NotchController:
    """Plans GEQ cuts for detections on one bus.

    ``policy_validate(current_db, new_db)`` is called before every planned change (``Policy.validate_notch``)
    and its exceptions propagate unchanged. ``budget`` is the number of distinct GEQ bands this session may
    write; deepening a band already written this session is free. ``existing`` (1-based band -> dB) gives
    the GEQ's current gains so deepening starts from them; its cuts (< 0 dB) are listed in :attr:`notches`
    as pre-existing and are not counted against the budget until touched.
    """

    def __init__(
        self,
        cfg: DetectorConfig,
        geq_band_hz: Sequence[float],
        policy_validate: Callable[[float, float], None],
        *,
        budget: int,
        existing: dict[int, float] | None = None,
    ) -> None:
        self.cfg = cfg
        self.geq_band_hz: tuple[float, ...] = tuple(float(h) for h in geq_band_hz)
        if not self.geq_band_hz or any(h <= 0 for h in self.geq_band_hz):
            raise ValueError("geq_band_hz must be positive frequencies")
        self._log_hz = [math.log(h) for h in self.geq_band_hz]
        self._validate = policy_validate
        self.budget = int(budget)
        self._gains: dict[int, float] = {}
        self._notches: dict[int, Notch] = {}
        self._touched: set[int] = set()
        for band, db in (existing or {}).items():
            band = int(band)
            if not 1 <= band <= len(self.geq_band_hz):
                raise ValueError(f"existing notch band {band} out of range 1..{len(self.geq_band_hz)}")
            self._gains[band] = float(db)
            if db < 0:
                self._notches[band] = Notch(bus=0, band=band, freq_hz=self.geq_band_hz[band - 1],
                                            depth_db=float(db), session_id="", ts=0.0, detections=0)

    def band_for_freq(self, hz: float) -> int:
        """1-based GEQ band whose centre is nearest to ``hz`` in log-frequency."""
        if not hz > 0:
            raise ValueError(f"frequency must be > 0 Hz, got {hz!r}")
        lh = math.log(hz)
        return min(range(len(self._log_hz)), key=lambda i: abs(self._log_hz[i] - lh)) + 1

    def _can_deepen(self, band: int) -> bool:
        return self._gains.get(band, 0.0) > self.cfg.notch_max_db

    def _affordable(self, band: int) -> bool:
        return band in self._touched or self.budget_left > 0

    def plan(self, det: Detection, bus: int, session_id: str) -> Notch | None:
        """Decide the cut for ``det``: deepen the nearest notch within ``merge_adjacent_bands`` (exact band
        first), else open a new notch at the detection's band. Returns the updated :class:`Notch` or None
        when nothing can be done (budget spent, or the band is already at ``notch_max_db``)."""
        cfg = self.cfg
        target = self.band_for_freq(det.freq_hz)
        order = sorted(self._notches, key=lambda b: (abs(b - target), b))
        for band in order:
            if abs(band - target) > cfg.merge_adjacent_bands:
                break
            if self._can_deepen(band) and self._affordable(band):
                return self._apply(band, bus, session_id, det.ts)
        if not self._can_deepen(target) or not self._affordable(target):
            return None
        return self._apply(target, bus, session_id, det.ts)

    def _apply(self, band: int, bus: int, session_id: str, ts: float) -> Notch:
        current = self._gains.get(band, 0.0)
        new = max(self.cfg.notch_max_db, current + self.cfg.notch_step_db)
        self._validate(current, new)  # may raise (BOOST_FORBIDDEN / NOT_ALLOWED) — state untouched
        self._gains[band] = new
        self._touched.add(band)
        n = self._notches.get(band)
        if n is None:
            n = Notch(bus=bus, band=band, freq_hz=self.geq_band_hz[band - 1], depth_db=new,
                      session_id=session_id, ts=ts, detections=0)
            self._notches[band] = n
        n.bus = bus
        n.depth_db = new
        n.session_id = session_id
        n.ts = ts
        n.detections += 1
        log.debug("notch plan: bus %d band %d (%.0f Hz) %.1f -> %.1f dB", bus, band, n.freq_hz, current, new)
        return n

    @property
    def notches(self) -> list[Notch]:
        """All known cuts on the GEQ (pre-existing + this session), lowest band first."""
        return [self._notches[b] for b in sorted(self._notches)]

    @property
    def gains(self) -> dict[int, float]:
        """Known GEQ band gains (1-based band -> dB), including untouched pre-existing values."""
        return dict(self._gains)

    @property
    def touched_bands(self) -> set[int]:
        return set(self._touched)

    @property
    def budget_left(self) -> int:
        return max(0, self.budget - len(self._touched))

    @property
    def spent(self) -> bool:
        return self.budget_left <= 0
