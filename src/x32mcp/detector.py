"""CFS² feedback detector and notch planner (DESIGN.md §12) — pure Python, no I/O, no asyncio.

Two synchronous, side-effect-free pieces used by ``cfs.py``:

* :class:`FeedbackDetector` consumes one RTA frame at a time (100 dB values from ``/meters/15``,
  band ``i`` centred at ``band_hz[i]`` = ``10000 * 2 ** ((i - 90) / 10)`` Hz, docs/research/meters.md §4.2)
  and returns :class:`Detection` objects for bands that look like regenerative feedback.
* :class:`NotchController` turns detections into GEQ band cuts (:class:`Notch`), merging adjacent
  detections, deepening in ``notch_step_db`` steps to ``notch_max_db`` and respecting a per-session budget.
  It never writes anything itself; ``cfs`` applies the returned :class:`Notch` through a :class:`GeqWriter`.

Units and indices at the public boundary
----------------------------------------
* Levels are dB (RTA dB re. full scale, -128 = "no signal", 0.0 = the desk's clip flag), times are
  seconds (``time.time()`` style floats), slopes are dB/s.
* **RTA band indices are 0-based** (``Detection.band`` indexes ``band_hz``; matches the meters.md formula
  and the dashboard's ``db[]`` array).
* **GEQ band numbers are 1-based** (``Notch.band`` 1..31 == the ``/fx/N/par/NN`` parameter number,
  ``geq_band_hz[band - 1]`` is its centre; docs/research/fx_routing_scenes.md §2.3).

Decision procedure (replaces the DESIGN §12 weighted sum; see docs/REVIEW_BRIEF.md §1)
--------------------------------------------------------------------------------------
The M7 detector scored ``0.3·prominence + 0.2·persistence + 0.5·growth`` against a 0.7 threshold, which
made growth mandatory (0.3 + 0.2 < 0.7: a plateaued howl was unreachable) while the growth it measured
below ~300 Hz was manufactured by the analyser (a 1/10-octave band cannot settle faster than ~1/Δf,
Δf = 0.069·f, so an instant bass onset renders as a 6..60 dB/s ramp) and nothing looked at the one
thing that separates a note from a ring in a single frame: a note has a harmonic family. The three
mechanisms are removed as follows; the tracking structure (prominence → qualifying peaks →
:class:`Candidate` streaks → verdict, per-band cooldown, re-emission for deepening) is unchanged.

Per frame, per band ``i``:

* ``prominence[i] = level[i] - median(level[i-k .. i+k] without i)``, ``k = neighbour_bins``.
* ``narrowness[i] = level[i] - max(level[i±2], level[i±3])``. A lone sinusoid on a 1/10-octave bank
  clears ≥ 24 dB at ±2 for any plausible skirt; a formant hump, cymbal wash or a broadband bump does not.
* A band *qualifies* when ``prominence >= prominence_db`` and ``narrowness >= narrow_db`` and
  ``level >= min_level_db`` and its centre lies inside the session's frequency window (below).
  Adjacent qualifying bands are one peak (the loudest); the peak carries a power **centroid** over
  ``b-1..b+1`` (a line between two centres reads -3/-3 dB in both) and the cluster level.
* **Harmonic family (single frame).** Partial ``Hk`` of a peak at ``b`` sits ``10·log2(k)`` bands up
  (H2 +10, H3 +16, H4 +20, H5 +23). ``Hk`` is *present* when a band within ±1 of that offset is itself a
  local maximum with prominence ``>= harmonic_presence_db`` and within ``harmonic_rel_db`` of the
  candidate's prominence (a clipping howl's odd partials sit ≥ 10–17 dB under it and appear only near
  full scale; musical partials sit 0..-15 dB). The frame is *family-positive* for the peak when ≥
  ``family_min_partials`` of H2..H5 are present, or when the peak is itself H2/H3 of a lower present
  peak that owns at least one other partial (the bass-note-shows-as-its-H2 case: the M7 40/80 Hz pair).

Per candidate (a peak tracked within ``±band_tolerance`` from frame to frame):

* ``family_frac`` = family-positive frames / frames. ``MUSICAL`` once ``frames >= 3`` and
  ``family_frac >= family_veto_frac``, or the centroid has wandered more than ``centroid_wander_bands``
  over the sustain window (melody, glide, vibrato wider than a band), unless the level is within
  ``clip_level_db`` of full scale (anything that loud and that narrow is cut whatever it is).
* **Step onset** (programme arrives at full level within one analyser rise time; a loop has to grow
  through every level at e/τ dB/s): a single-frame rise of the cluster level ``>= step_db`` that is not
  followed by a further rise of at least ``step_continue_frac`` of it on the next frame marks the track
  ``step_onset`` and restarts the growth window after the step. The pre-qualification history of the band
  (``history_frames`` frames are kept) is prepended when a track starts, so a note that qualifies on its
  first full frame still shows its step. This replaces the 60 dB/s onset guard, which discarded ~half of
  all real ring-out howls (1 dB excess on a 5 ms wedge loop = 200 dB/s) and then scored the decelerating
  tail of every LF note onset as growth.
* **GROWTH evidence** (fast lane, never required): over the growth window (restarted by a drop of more
  than ``monotonic_tolerance_db`` or by a step) of at least ``n_g(band)`` samples the least-squares slope
  of the *reference-corrected* level (level minus the frame's spectrum reference = median of bands
  ``ref_lo_band..ref_hi_band``, so a fader/common-mode move is not growth) is ``>= growth_min_db_per_s``,
  the corrected rise is ``>= growth_rise_db``, no two consecutive intervals carry more than
  ``growth_step_share`` of the rise (a step is not a ramp) and the later half of the window carries at
  least ``growth_late_share`` of it (an analyser-smeared step decelerates to nothing; a loop's dB-linear
  ramp does not). ``n_g(band) = max(growth_min_frames, ceil(growth_lf_periods / (Δf·frame_period)))``:
  the window must span ``growth_lf_periods``/Δf so that no filter resolving Δf can fill it with a steady
  ramp from a step (3 frames above ~300 Hz, 5 at 125 Hz, 10 at 63 Hz). There is no upper slope bound.
* **SUSTAINED evidence** (the plateau lane): ``frames >= sustain_frames``, no step onset seen, the
  centroid stayed within ``centroid_wander_bands``, the level did not fall more than ``sustain_drop_db``
  below its window maximum (plucked/struck notes and RTA release tails decay; an intact loop does not),
  still narrow and prominent, and ``level >= sustain_min_level_db`` (the one place an absolute level is
  used: with no onset and no growth observed, level is the only passive evidence left, and a -50 dBFS
  line at a pre-fader bus tap is not a howl worth a notch).
* **CLIP**: ``level >= clip_level_db`` for ``persistence_frames`` frames, narrow: emitted regardless of
  family (a clipping howl grows odd harmonics) or onset.
* A detection is emitted when ``frames >= persistence_frames`` and (CLIP or (not MUSICAL and (GROWTH or
  SUSTAINED))) and the band (±band_tolerance) is not in its ``cooldown_s``. A candidate that keeps
  ringing is re-emitted once per cooldown so the notch can be deepened. ``Detection.reasons`` names the
  predicates that fired; ``confidence`` is a monotone function of the margins for the dashboard and is
  ``>= confidence_threshold`` exactly when the predicates say feedback (it is reported, not decided on).
* **Frequency window**: ``window_lo_hz..window_hi_hz`` (``mode='watch'``: programme present, vocal-mic
  loop gain is 20–30 dB down below ~150 Hz) or ``ringout_window_lo_hz`` (``mode='ringout'``: no
  programme by contract, a missed LF mode is what a ring-out exists to find); ``lf_feedback_possible``
  (kick/tom/acoustic-pickup mics on a bus that reaches subs) lowers the edge to ``lf_window_lo_hz``.
  ``cfs`` may narrow it further from what the desk knows (0.7 × the lowest open channel HPF).
* **Active probe** (optional, ring-out): ``note_gain_step(delta_db, ts)`` records a server-owned master
  step; a tracked candidate whose reference-corrected level then rises by more than
  ``delta_db + probe_excess_db`` within ``probe_window_s`` on ``probe_confirmations`` steps is
  loop-gain-dependent (regenerating within a few dB of threshold) and is emitted with reason ``probe``.

Kept from the previous heuristic and still meaningful: ``prominence_db``, ``neighbour_bins``,
``persistence_frames`` (minimum age of any verdict), ``growth_min_db_per_s``, ``monotonic_tolerance_db``,
``band_tolerance``, ``cooldown_s``, ``growth_window_frames``, all notch/verify keys. Deprecated (parsed,
validated, no longer decision variables): ``w_*``/``weights`` and ``growth_ref_db_per_s`` shape the
reported confidence only; ``growth_max_db_per_s`` (the onset guard) is superseded by the step test;
``override_prominence_db``/``override_persistence_frames`` are superseded by the SUSTAINED lane (a
25 dB / 6-frame line with no family, no step onset and a stable centroid is exactly what SUSTAINED
emits; one *with* a step onset — whistle, flute, organ, sine lead, bell — is exactly what the override
got wrong); ``min_level_db`` keeps its meaning as the qualification gate but defaults to -90 (prominence
and narrowness are the spatial floor; ``sustain_min_level_db`` is the level prior where one is physical).

* ``decay_verify_frames`` (extension, default 2) belongs to the VERIFY stage in ``cfs.py``: the notched
  band must sit ``decay_verify_db`` below its level at the cut on that many *consecutive* frames.

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
_MODES = ("watch", "ringout")
# partial k -> offset in 1/10-octave bands (10·log2 k): H2, H3, H4, H5
_HARMONIC_OFFSETS = tuple((k, 10.0 * math.log2(k)) for k in (2, 3, 4, 5))


@dataclass(frozen=True)
class DetectorConfig:
    """Detector + notch thresholds; one field per ``device.yaml`` ``detector:`` key.

    The yaml ``weights: {prominence, persistence, growth}`` mapping is flattened into
    ``w_prominence`` / ``w_persistence`` / ``w_growth`` (dashboard confidence only). ``mode`` is
    ``'watch'`` or ``'ringout'``; ``lf_feedback_possible`` widens the window to ``lf_window_lo_hz``.
    """

    prominence_db: float = 12.0          # dB above the median of the ±neighbour_bins neighbours
    neighbour_bins: int = 3              # bands each side used for the median
    min_level_db: float = -90.0          # qualification gate (RTA dB); prominence/narrowness are the real floor
    persistence_frames: int = 3          # minimum age (frames) of any verdict
    growth_min_db_per_s: float = 6.0     # reference-corrected slopes below this are not growth
    growth_ref_db_per_s: float = 20.0    # slope that scores 1.0 in the reported confidence (dashboard only)
    growth_max_db_per_s: float = 60.0    # DEPRECATED onset guard (superseded by the step test); validated only
    monotonic_tolerance_db: float = 1.0  # a step below -this restarts the growth window
    w_prominence: float = 0.3            # dashboard confidence weights (not decision variables)
    w_persistence: float = 0.2
    w_growth: float = 0.5
    confidence_threshold: float = 0.7    # reported confidence of an emitted detection is >= this
    band_tolerance: int = 1              # drift allowed while tracking / cooldown radius (RTA bands)
    cooldown_s: float = 1.0              # s between emissions for the same band
    notch_step_db: float = -3.0          # per detection (negative)
    notch_max_db: float = -9.0           # deepest cut (negative, <= notch_step_db)
    notch_budget_default: int = 6        # distinct GEQ bands per session
    merge_adjacent_bands: int = 1        # GEQ bands: a detection this close to a notch deepens it
    decay_verify_db: float = 6.0         # used by cfs.py (VERIFY stage)
    decay_verify_s: float = 1.5          # used by cfs.py
    decay_verify_frames: int = 2         # consecutive frames that must show the drop (cfs.py, extension)
    frame_period_s: float = 0.05         # nominal RTA frame period (n_g(band) is derived from it)
    growth_window_frames: int = 60       # bound on the growth window
    override_prominence_db: float = 25.0     # DEPRECATED (superseded by the SUSTAINED lane); validated only
    override_persistence_frames: int = 6     # DEPRECATED
    # --- discriminator (2026-09 redesign) -------------------------------------------------------
    narrow_db: float = 12.0              # level - 2nd loudest of the four bands at ±2, ±3: a line, not a hump
    window_lo_hz: float = 160.0          # feedback_watch low edge (vocal mics, programme present)
    ringout_window_lo_hz: float = 100.0  # ring_out low edge (no programme by contract)
    lf_window_lo_hz: float = 40.0        # low edge when lf_feedback_possible (kick/tom/pickup near subs)
    window_hi_hz: float = 12500.0        # condensers ring to 10-12 kHz; SM58-class dies above ~10 kHz
    lf_feedback_possible: float = 0.0    # 1 = an LF-capable source is open on the bus (operator/cfs sets it)
    harmonic_presence_db: float = 6.0    # a partial is "present" as a local peak this prominent
    harmonic_rel_db: float = 18.0        # ... and within this of the candidate's own prominence
    harmonic_tol_bands: float = 0.5      # partial centroid must sit within this of k x the candidate's centroid
    family_min_partials: int = 2         # of H2..H5 present on one frame => family-positive frame
    single_partial_rel_db: float = 10.0  # ... or ONE of H2/H3 this close to the candidate's own prominence
    single_partial_tol_bands: float = 0.35   # and this close to the exact harmonic position (organ 8'+4', flute: one strong exact partial)
    single_partial_comove_db: float = 1.5    # and tracking the candidate this tightly (two independent rings an octave apart do not)
    parent_excess_low_db: float = 8.0    # b as H2/H3 of a lower line may be at most this much LOUDER than that line (voice, HPF'd bass)
    parent_excess_high_db: float = 2.0   # b as H4/H5: at most this (no instrument's 4th/5th partial towers over its fundamental)
    family_veto_frac: float = 0.4        # family-positive fraction of the last family_window_frames => MUSICAL
    family_window_frames: int = 12       # (a note keeps its partials for life; a ring's coincidences come and go)
    centroid_wander_bands: float = 0.75  # max centroid range over the sustain window (melody/glide/vibrato move more)
    step_db: float = 9.0                 # single-frame cluster rise that is a programme onset, not a loop
    step_continue_frac: float = 0.3      # ... unless the next frame rises by this fraction of it again (fast ramp)
    step_release_db: float = 3.0         # a band's step memory clears once its level is back within this of its pre-arrival level
    comove_frames: int = 6               # a partial counts only if its level tracked the candidate's over these frames
    comove_db: float = 3.0               # ... to within this (a note's partials share one envelope; coincidences do not)
    history_frames: int = 8              # per-band pre-qualification history kept (frames)
    gap_frames: int = 2                  # non-qualifying frames a track survives (masking transients, threshold flicker)
    growth_min_frames: int = 3           # growth window samples above ~300 Hz
    growth_lf_periods: float = 2.0       # growth window must span this many 1/Δf below that
    growth_rise_db: float = 6.0          # reference-corrected rise required over the growth window
    growth_step_share: float = 0.7       # max share of the rise carried by two consecutive intervals
    growth_late_share: float = 0.25      # min share of the rise carried by the later half of the window
    growth_min_steps: int = 3            # intervals that must each carry >= growth_step_min_share of the rise
    growth_step_min_share: float = 0.1   # (a loop rises continuously; partial frames / neighbour arrivals are lone jumps)
    sustain_frames: int = 6              # SUSTAINED lane: frames of stable, non-decaying, family-free line (300 ms)
    sustain_strong_prominence_db: float = 18.0   # below this the lane waits sustain_moderate_frames instead: at 12-18 dB the
    sustain_moderate_frames: int = 12    # absence of a family proves little (partials may sit under the floor), so ask for time
    sustain_drop_db: float = 3.0         # max fall below the window maximum (decaying notes / RTA release fail)
    sustain_min_level_db: float = -45.0  # SUSTAINED lane only: no onset, no growth => level is the last evidence
    clip_level_db: float = -6.0          # narrow line this close to full scale is cut whatever else is true
    frozen_eps_db: float = 0.002         # a level repeating to within this on most frames of the sustain window is a held
                                         # display value (peak-hold left on), not a measurement: never plateau evidence
    ref_lo_band: int = 25                # spectrum reference = median(level[ref_lo_band .. ref_hi_band])
    ref_hi_band: int = 85
    probe_excess_db: float = 2.0         # ring-out probe: rise beyond the step that marks loop-gain dependence
    probe_window_s: float = 1.2          # ... measured within this long after the step
    probe_confirmations: int = 2         # steps that must confirm before a probe verdict
    mode: str = "watch"

    def __post_init__(self) -> None:
        def need(cond: bool, msg: str) -> None:
            if not cond:
                raise ValueError(f"DetectorConfig: {msg}")

        need(self.prominence_db > 0, "prominence_db must be > 0")
        need(self.override_prominence_db >= 0, "override_prominence_db must be >= 0")
        need(self.override_persistence_frames >= 1, "override_persistence_frames must be >= 1")
        need(self.neighbour_bins >= 1, "neighbour_bins must be >= 1")
        need(self.persistence_frames >= 1, "persistence_frames must be >= 1")
        need(self.growth_ref_db_per_s > 0, "growth_ref_db_per_s must be > 0")
        need(self.growth_max_db_per_s > self.growth_min_db_per_s, "growth_max_db_per_s must exceed growth_min_db_per_s")
        need(self.monotonic_tolerance_db >= 0, "monotonic_tolerance_db must be >= 0")
        need(min(self.w_prominence, self.w_persistence, self.w_growth) >= 0, "weights must be >= 0")
        need(self.band_tolerance >= 0, "band_tolerance must be >= 0")
        need(self.cooldown_s >= 0, "cooldown_s must be >= 0")
        need(self.notch_step_db < 0, "notch_step_db must be negative (cuts only)")
        need(self.notch_max_db <= self.notch_step_db, "notch_max_db must be <= notch_step_db")
        need(self.notch_budget_default >= 0, "notch_budget_default must be >= 0")
        need(self.decay_verify_frames >= 1, "decay_verify_frames must be >= 1")
        need(self.merge_adjacent_bands >= 0, "merge_adjacent_bands must be >= 0")
        need(self.growth_window_frames >= max(2, self.persistence_frames), "growth_window_frames too small")
        need(self.narrow_db >= 0, "narrow_db must be >= 0")
        need(0 < self.lf_window_lo_hz <= self.ringout_window_lo_hz <= self.window_hi_hz, "window edges out of order")
        need(self.lf_window_lo_hz <= self.window_lo_hz <= self.window_hi_hz, "window_lo_hz outside the window")
        need(self.frame_period_s > 0, "frame_period_s must be > 0")
        need(self.family_min_partials >= 1, "family_min_partials must be >= 1")
        need(0.0 < self.harmonic_tol_bands <= 1.5 and self.family_window_frames >= 3, "harmonic_tol/family_window out of range")
        need(0.0 < self.single_partial_tol_bands <= self.harmonic_tol_bands and self.single_partial_rel_db >= 0
             and 0.0 < self.single_partial_comove_db <= self.comove_db, "single_partial_* out of range")
        need(0.0 <= self.family_veto_frac <= 1.0, "family_veto_frac must be in [0, 1]")
        need(self.step_db > 0 and 0.0 <= self.step_continue_frac < 1.0, "step_db/step_continue_frac out of range")
        need(self.history_frames >= 4, "history_frames must be >= 4")
        need(self.gap_frames >= 0 and self.step_release_db > 0, "gap_frames/step_release_db out of range")
        need(1 <= self.comove_frames <= self.history_frames and self.comove_db > 0, "comove settings out of range")
        need(self.history_frames >= 5, "history_frames must be >= 5 (the step test needs 5)")
        need(self.growth_min_frames >= 3, "growth_min_frames must be >= 3")
        need(self.growth_rise_db > 0, "growth_rise_db must be > 0")
        need(0.0 < self.growth_step_share <= 1.0 and 0.0 <= self.growth_late_share < 1.0, "growth share bounds out of range")
        need(self.growth_min_steps >= 1 and 0.0 <= self.growth_step_min_share < 1.0, "growth_min_steps/step_min_share out of range")
        need(self.sustain_frames >= self.persistence_frames, "sustain_frames must be >= persistence_frames")
        need(self.sustain_moderate_frames >= self.sustain_frames, "sustain_moderate_frames must be >= sustain_frames")
        need(self.sustain_drop_db >= 0, "sustain_drop_db must be >= 0")
        need(0 <= self.ref_lo_band < self.ref_hi_band, "ref_lo_band/ref_hi_band out of order")
        need(self.probe_confirmations >= 1 and self.probe_window_s > 0, "probe settings out of range")
        need(self.mode in _MODES, f"mode must be one of {_MODES}")

    @property
    def weights(self) -> dict[str, float]:
        return {"prominence": self.w_prominence, "persistence": self.w_persistence, "growth": self.w_growth}

    @property
    def window_hz(self) -> tuple[float, float]:
        """The feedback window (Hz) this configuration admits: mode default, widened by ``lf_feedback_possible``."""
        lo = self.ringout_window_lo_hz if self.mode == "ringout" else self.window_lo_hz
        if self.lf_feedback_possible:
            lo = min(lo, self.lf_window_lo_hz)
        return (lo, self.window_hi_hz)

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
            t = known[key]
            if t == "str":
                kw[key] = str(value)
            elif t == "int":
                kw[key] = int(value)
            else:
                kw[key] = float(value)
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
    """One feedback verdict. ``band`` is the 0-based RTA band, ``freq_hz`` the interpolated line frequency
    (log-frequency centroid of the ±1 cluster), ``reasons`` the predicates that justified it."""

    ts: float
    band: int
    freq_hz: float
    level_db: float
    prominence_db: float
    slope_db_per_s: float
    frames: int
    confidence: float
    reasons: tuple[str, ...] = ()
    centroid: float = -1.0

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
            "centroid": self.centroid,
        }


@dataclass
class Candidate:
    """A tracked peak. ``levels``/``ts_list``/``refs`` are the growth window (bounded, may restart; see module
    doc); ``frames`` is the full persistence count since ``first_ts``; ``recent``/``centroids`` cover the last
    ``sustain_frames`` frames."""

    band: int
    first_ts: float
    frames: int
    levels: list[float]
    ts_list: list[float]
    confidence: float
    override: bool = False               # kept for the dashboard: True when emitted without growth (SUSTAINED/CLIP)
    freq_hz: float = 0.0
    level_db: float = -128.0
    prominence_db: float = 0.0
    slope_db_per_s: float = 0.0
    growth_score: float = 0.0
    last_ts: float = 0.0
    emitted: int = 0
    centroid: float = 0.0
    narrow_db: float = 0.0
    cluster_db: float = -128.0
    cluster_prominence_db: float = 0.0
    stable_frames: int = 0               # consecutive qualifying frames meeting the sustained-lane conditions
    refs: list[float] = field(default_factory=list)
    recent: list[float] = field(default_factory=list)      # last sustain_frames levels
    centroids: list[float] = field(default_factory=list)   # last sustain_frames centroids
    family_frames: int = 0
    family_hist: list[int] = field(default_factory=list)    # last family_window_frames verdicts (0 none, 1 pair, 2 family)
    missed: int = 0                      # consecutive non-qualifying frames survived (<= gap_frames)
    step_onset: bool = False
    step_level: float = -128.0           # level right after the arrival that flagged step_onset
    at_arm: bool = False                 # already present when the detector armed (no onset observable)
    musical: bool = False
    frozen: bool = False                 # level exactly constant over the sustain window: held display, not a plateau
    growth: bool = False
    sustained: bool = False
    clip_frames: int = 0
    probe_hits: int = 0
    probe_base: float | None = None      # reference-corrected level at the last noted gain step
    probe_until: float = 0.0
    probe_delta: float = 0.0
    reasons: tuple[str, ...] = ()

    @property
    def family_frac(self) -> float:
        """Fraction of the recent window on which a pair or a family was present."""
        return sum(1 for f in self.family_hist if f) / len(self.family_hist) if self.family_hist else 0.0

    @property
    def strict_family_frac(self) -> float:
        """Fraction of the recent window on which a full family (not just a pair) was present."""
        return sum(1 for f in self.family_hist if f >= 2) / len(self.family_hist) if self.family_hist else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "band": self.band,
            "freq_hz": self.freq_hz,
            "centroid": round(self.centroid, 2),
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "frames": self.frames,
            "level_db": self.level_db,
            "prominence_db": self.prominence_db,
            "narrow_db": self.narrow_db,
            "slope_db_per_s": self.slope_db_per_s,
            "growth_score": self.growth_score,
            "family_frac": round(self.family_frac, 2),
            "step_onset": self.step_onset,
            "musical": self.musical,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "emitted": self.emitted,
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


def _pw(db: float) -> float:
    return 10.0 ** (db / 10.0)


class FeedbackDetector:
    """Frame-by-frame feedback detector over an RTA stream (see module docstring for the procedure)."""

    def __init__(self, cfg: DetectorConfig, band_hz: Sequence[float], *, mode: str | None = None,
                 lf_feedback_possible: bool | None = None) -> None:
        if mode is not None or lf_feedback_possible is not None:
            import dataclasses as _dc
            repl: dict[str, Any] = {}
            if mode is not None:
                repl["mode"] = mode
            if lf_feedback_possible is not None:
                repl["lf_feedback_possible"] = 1.0 if lf_feedback_possible else 0.0
            cfg = _dc.replace(cfg, **repl)
        self.cfg = cfg
        self.band_hz: tuple[float, ...] = tuple(float(h) for h in band_hz)
        n = len(self.band_hz)
        if n < 2 * cfg.neighbour_bins + 1:
            raise ValueError("band_hz too short for neighbour_bins")
        lo_hz, hi_hz = cfg.window_hz
        edge = 2.0 ** (1.0 / 20.0)   # half a band of slack: a line just below a centre reads in that band
        self._in_window = tuple(lo_hz / edge <= h <= hi_hz * edge for h in self.band_hz)
        # growth window length per band: max(growth_min_frames, ceil(growth_lf_periods / (Δf·T))), Δf = 0.0693 f
        self._n_growth = tuple(
            max(cfg.growth_min_frames, math.ceil(cfg.growth_lf_periods / (0.06932 * h * cfg.frame_period_s)))
            for h in self.band_hz)
        self._log_hz = tuple(math.log(h) for h in self.band_hz)
        self._cands: list[Candidate] = []
        self._cooldown: dict[int, float] = {}
        self._hist: list[list[float]] = []          # last history_frames raw frames (newest last)
        self._chist: list[list[float]] = []         # per-band cluster levels, last comove_frames frames incl. current
        self._step_alive: list[bool] = [False] * n  # a programme onset (step) arrived on this band and is still sounding
        self._step_ref: list[float] = [-128.0] * n  # level right after that step
        self._step_floor: list[float] = [-128.0] * n  # level just before it
        self._step_frame: list[int] = [-1] * n      # frame index at which the step was recognised
        self._steps: list[tuple[float, float]] = []  # (ts, delta_db) noted gain steps still open
        self.frames_seen: int = 0
        self.last_ts: float | None = None
        self.ref_db: float = -128.0

    # -- spatial features ----------------------------------------------------------------------
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

    @staticmethod
    def narrowness(values_db: Sequence[float], i: int) -> float:
        """``level[i]`` minus the second loudest of the four bands at ``i±2, i±3`` (dB). A lone line clears
        >= 24 dB there on any plausible 1/10-octave skirt; a hump three or more bands wide does not. The
        loudest of the four is forgiven: one unrelated programme partial two bands away is common and
        says nothing about the width of *this* line."""
        n = len(values_db)
        far = sorted((values_db[j] for j in (i - 3, i - 2, i + 2, i + 3) if 0 <= j < n), reverse=True)
        if not far:
            return 0.0
        return values_db[i] - (far[1] if len(far) > 1 else far[0])

    @staticmethod
    def _cluster(values: Sequence[float], b: int) -> tuple[float, float]:
        """(power centroid in bands, cluster level dB) of ``b-1..b+1``."""
        n = len(values)
        acc = 0.0
        mom = 0.0
        for d in (-1, 0, 1):
            j = b + d
            if 0 <= j < n:
                p = _pw(values[j])
                acc += p
                mom += d * p
        if acc <= 0.0:
            return float(b), -128.0
        return b + mom / acc, 10.0 * math.log10(acc)

    def cluster_prominence(self, values: Sequence[float], b: int, cluster_db: float) -> float:
        """Cluster level minus the median of the six bands at ``b±2..b±4``: the prominence of a line that
        sits between two centres (reads -3/-3 dB) is not under-stated the way the single-band figure is."""
        n = len(values)
        ring = [values[j] for j in (b - 4, b - 3, b - 2, b + 2, b + 3, b + 4) if 0 <= j < n]
        return cluster_db - median(ring) if ring else 0.0

    @staticmethod
    def _peaks(qualifying: Sequence[int], values: Sequence[float]) -> list[int]:
        """Collapse runs of adjacent qualifying bands into their loudest band."""
        peaks: list[int] = []
        run: list[int] = []
        for b in qualifying:
            if run and b != run[-1] + 1:
                peaks.append(max(run, key=lambda j: values[j]))
                run = []
            run.append(b)
        if run:
            peaks.append(max(run, key=lambda j: values[j]))
        return peaks

    def _freq_at(self, centroid: float) -> float:
        n = len(self.band_hz)
        c = min(max(centroid, 0.0), n - 1.0)
        i = min(int(c), n - 2)
        f = c - i
        return math.exp(self._log_hz[i] * (1.0 - f) + self._log_hz[i + 1] * f)

    def _ref_of(self, vals: Sequence[float]) -> float:
        cfg = self.cfg
        hi = min(cfg.ref_hi_band, len(vals) - 1)
        return median(vals[cfg.ref_lo_band:hi + 1])

    # -- harmonic family -----------------------------------------------------------------------
    def _comoves(self, q: int, b: int, age: int, tol_db: float | None = None) -> bool:
        """True when band ``q``'s cluster level tracked band ``b``'s over the frames since the candidate at
        ``b`` was born (at most ``comove_frames``; with fewer than 3 frames there is nothing to compare and
        presence alone counts): partials of one note share one envelope; a programme line that merely sits
        at a harmonic offset of a ring pulses, decays or holds on its own."""
        k = min(self.cfg.comove_frames, age)
        if k < 3:
            return True
        diffs = [fr[q] - fr[b] for fr in self._chist[-k:]]
        return max(diffs) - min(diffs) <= (self.cfg.comove_db if tol_db is None else tol_db)

    def _partial_at(self, pos: float, need: float, b: int, age: int, prom: Sequence[float],
                    vals: Sequence[float], tol: float | None = None, comove_db: float | None = None,
                    min_level: float = -1e9) -> bool:
        """Is there a line whose centroid sits within ``tol`` (default harmonic_tol_bands) of fractional band
        ``pos``, that is a local maximum with prominence >= ``need``, level >= ``min_level`` and co-moves with
        band ``b`` (to within ``comove_db``, default cfg.comove_db)?"""
        n = len(vals)
        tol = self.cfg.harmonic_tol_bands if tol is None else tol
        lo, hi = int(math.floor(pos - 1.0)), int(math.ceil(pos + 1.0))
        for q in range(max(1, lo), min(n - 1, hi + 1)):
            if prom[q] < need or vals[q] < min_level or vals[q] < vals[q - 1] or vals[q] < vals[q + 1]:
                continue
            cq, _ = self._cluster(vals, q)
            if abs(cq - pos) <= tol and self._comoves(q, b, age, comove_db):
                return True
        return False

    def _family(self, b: int, centroid: float, age: int, prom: Sequence[float], vals: Sequence[float]) -> int:
        """Single-frame harmonic-family test for a peak at band ``b`` with fractional ``centroid``; ``age`` is
        the number of frames the candidate has existed (co-movement is judged over those).

        Returns 2 for a *family* (>= family_min_partials of H2..H5 present, or b is a partial of a lower line
        that owns another partial), 1 for a *pair* (exactly one comparably strong, dead-on, tightly co-moving
        H2/H3 above, or such a line exactly an octave below: organ 8'+4', octave doubling, low flute - a loop
        never makes a subharmonic), 0 for none. A pair vetoes the plateau lane; only a family vetoes growth
        (two coexisting rings a near-octave apart form a pair while they grow, loop brief 2.3)."""
        cfg = self.cfg
        floor = max(cfg.harmonic_presence_db, prom[b] - cfg.harmonic_rel_db)
        count = sum(1 for _, off in _HARMONIC_OFFSETS if self._partial_at(centroid + off, floor, b, age, prom, vals))
        if count >= cfg.family_min_partials:
            return 2
        # b is itself partial k of a lower line that owns at least one other partial j != k. The lower line
        # must be plausibly a fundamental for b's level: no source's 4th/5th partial towers over its
        # fundamental; H2/H3 may (open vowels, a bass whose H1 the PA's high-pass ate).
        weak = cfg.harmonic_presence_db
        for k, off in _HARMONIC_OFFSETS:
            f0 = centroid - off
            floor_lv = vals[b] - (cfg.parent_excess_low_db if k <= 3 else cfg.parent_excess_high_db)
            if f0 < 1.0 or not self._partial_at(f0, weak, b, age, prom, vals, min_level=floor_lv):
                continue
            if any(j != k and self._partial_at(f0 + oj, weak, b, age, prom, vals) for j, oj in _HARMONIC_OFFSETS):
                return 2
        # pair: one partial is enough when it is H2 or H3, comparably strong and dead on the harmonic position
        strong = max(cfg.harmonic_presence_db, prom[b] - cfg.single_partial_rel_db)
        tight = dict(tol=cfg.single_partial_tol_bands, comove_db=cfg.single_partial_comove_db)
        for _, off in _HARMONIC_OFFSETS[:2]:
            if self._partial_at(centroid + off, strong, b, age, prom, vals, **tight):
                return 1
        # ... or a comparably strong, tightly co-moving line exactly an octave below (b is its H2)
        if centroid - 10.0 >= 1.0 and self._partial_at(centroid - 10.0, strong, b, age, prom, vals, **tight):
            return 1
        return 0

    # -- onset shape (per band, independent of qualification) -----------------------------------
    def _is_step(self, w: Sequence[float]) -> bool:
        """``w`` = five consecutive levels l0..l4 of one band. True when the rise l1 -> l3 (at most two
        frames) is >= step_db and neither the frame before (l0 -> l1) nor the frame after (l3 -> l4)
        carries step_continue_frac of it: an arrival, not a ramp. A loop growing at up to ~2 x step_db per
        frame rises on >= 3 consecutive frames and never satisfies this; programme above ~300 Hz always
        does (one partially integrated frame at most), LF programme does once the analyser has settled."""
        cfg = self.cfg
        core = w[3] - w[1]
        if core < cfg.step_db:
            return False
        lim = cfg.step_continue_frac * core
        return (w[1] - w[0]) < lim and (w[4] - w[3]) < lim

    def _update_steps(self, vals: Sequence[float]) -> None:
        """Per-band step memory: a note that stepped in while something else masked its neighbourhood is
        still a note when it finally qualifies, and its release tail (however long the RTA decay stretches
        it) is still that note. The memory clears when the band is back within step_release_db of its
        pre-arrival level (the arrival has gone)."""
        cfg = self.cfg
        alive, ref, pre = self._step_alive, self._step_ref, self._step_floor
        h = self._hist
        full = len(h) >= 4
        for b in range(len(vals)):
            v = vals[b]
            if alive[b] and v < pre[b] + cfg.step_release_db:
                alive[b] = False
            if full and self._is_step((h[-4][b], h[-3][b], h[-2][b], h[-1][b], v)):
                if not alive[b]:
                    pre[b] = min(h[-4][b], h[-3][b])     # a re-attack keeps the original floor
                alive[b] = True
                ref[b] = max(h[-1][b], v)
                self._step_frame[b] = self.frames_seen

    # -- tracking ------------------------------------------------------------------------------
    def _in_cooldown(self, band: int, ts: float) -> bool:
        tol = self.cfg.band_tolerance
        for b in range(band - tol, band + tol + 1):
            until = self._cooldown.get(b)
            if until is not None and ts < until:
                return True
        return False

    def _restart_window(self, c: Candidate, keep: int = 1) -> None:
        if keep <= 0:
            c.levels.clear(); c.ts_list.clear(); c.refs.clear()
        else:
            del c.levels[:-keep], c.ts_list[:-keep], c.refs[:-keep]

    def _push_level(self, c: Candidate, level: float, ts: float, ref: float) -> None:
        """Append one sample to the growth window, restarting it on a drop beyond the tolerance."""
        cfg = self.cfg
        if c.levels and level - c.levels[-1] < -cfg.monotonic_tolerance_db:
            self._restart_window(c, 0)
        c.levels.append(level)
        c.ts_list.append(ts)
        c.refs.append(ref)
        if len(c.levels) > cfg.growth_window_frames:
            del c.levels[0], c.ts_list[0], c.refs[0]

    def _extend(self, c: Candidate, band: int, vals: Sequence[float], prom: Sequence[float], ts: float) -> None:
        cfg = self.cfg
        n = len(vals)
        c.frames += 1
        c.band = band
        c.level_db = vals[band]
        c.prominence_db = prom[band]
        c.narrow_db = self.narrowness(vals, band)
        c.centroid, c.cluster_db = self._cluster(vals, band)
        c.cluster_prominence_db = self.cluster_prominence(vals, band, c.cluster_db)
        c.freq_hz = self._freq_at(c.centroid)
        c.last_ts = ts
        fam = self._family(band, c.centroid, c.frames, prom, vals)
        if fam:
            c.family_frames += 1
        c.family_hist.append(fam)
        if len(c.family_hist) > cfg.family_window_frames:
            del c.family_hist[0]
        self._push_level(c, c.level_db, ts, self.ref_db)
        # Step onset: the per-band memory says a programme line arrived on this line's band(s) and is still
        # sounding. Judge what follows an arrival, never the arrival: the growth window restarts whenever
        # a new step lands on the line. A line between two centres owns both bands.
        # The line's own bands are those within 0.8 of its centroid (a line between two centres owns both;
        # a neighbour's arrival 1 band from a centred line is not this line's onset); once flagged, the
        # flag holds while any band within 1.0 still carries the arrival (vibrato swings the centroid).
        rng = range(max(0, band - 1), min(n, band + 2))
        alive = [q for q in rng if self._step_alive[q] and abs(q - c.centroid) <= 0.8]
        if alive:
            if not c.step_onset or any(self._step_frame[q] == self.frames_seen for q in alive):
                self._restart_window(c, 1)
                c.step_level = max(self._step_ref[q] for q in alive)
            c.step_onset = True
        elif c.step_onset and not any(self._step_alive[q] and abs(q - c.centroid) < 1.0 for q in rng):
            c.step_onset = False     # the arrival that flagged this line has gone (its bands are back at their old level)
        c.recent.append(c.level_db)
        c.centroids.append(c.centroid)
        if len(c.recent) > cfg.sustain_frames:
            del c.recent[0], c.centroids[0]
        c.clip_frames = c.clip_frames + 1 if c.level_db >= cfg.clip_level_db else 0

    def _seed_history(self, c: Candidate, band: int) -> None:
        """Prepend the band's pre-qualification levels so an onset that completed before the band qualified
        is still inside the growth window's view."""
        k = len(self._hist)
        for i, past in enumerate(self._hist):
            self._push_level(c, past[band], c.first_ts - (k - i) * self.cfg.frame_period_s, self._ref_of(past))

    # -- verdict -------------------------------------------------------------------------------
    def _score(self, c: Candidate, ts: float) -> None:
        cfg = self.cfg
        reasons: list[str] = []
        # growth lane: both the physical series and the reference-corrected one must rise (a common-mode
        # move has no corrected rise; a steady line over a fading mix has no absolute rise); the shape
        # tests run on the physical series.
        n = len(c.levels)
        lv = c.levels
        rel = [l - r for l, r in zip(lv, c.refs)]
        slope_rel = _ls_slope(c.ts_list, rel)
        slope_abs = _ls_slope(c.ts_list, lv)
        c.slope_db_per_s = slope_abs
        growth = False
        if n >= self._n_growth[c.band] and min(slope_rel, slope_abs) >= cfg.growth_min_db_per_s:
            rise = lv[-1] - lv[0]
            if min(rise, rel[-1] - rel[0]) >= cfg.growth_rise_db:
                two = max((lv[i + 2] - lv[i] for i in range(n - 2)), default=rise)
                late = lv[-1] - lv[n // 2]
                steps = sum(1 for i in range(n - 1) if lv[i + 1] - lv[i] >= cfg.growth_step_min_share * rise)
                if (two <= cfg.growth_step_share * rise and late >= cfg.growth_late_share * rise
                        and steps >= cfg.growth_min_steps):
                    growth = True
        c.growth = growth
        c.growth_score = min(1.0, max(0.0, slope_rel) / cfg.growth_ref_db_per_s) if growth else 0.0
        # musical
        loud = c.level_db >= cfg.clip_level_db
        wander = (max(c.centroids) - min(c.centroids)) if c.centroids else 0.0
        musical = strict = False
        if c.frames >= 3 and c.strict_family_frac >= cfg.family_veto_frac:
            musical = strict = True
            reasons.append("family")
        elif c.frames >= 3 and c.family_frac >= cfg.family_veto_frac:
            musical = True
            reasons.append("pair")
        if len(c.centroids) >= cfg.persistence_frames and wander > cfg.centroid_wander_bands:
            musical = strict = True
            reasons.append("wander")
        c.musical = musical and not loud
        strict = strict and not loud
        # sustained lane: the conditions must hold now; a strongly prominent line needs sustain_frames of track,
        # a moderate one (whose partials could be hiding under the floor) sustain_moderate_frames
        live = sum(1 for a, b in zip(c.recent, c.recent[1:]) if abs(b - a) > cfg.frozen_eps_db)
        frozen = len(c.recent) >= cfg.sustain_frames and live < len(c.recent) // 2
        c.frozen = frozen
        ok = (not c.step_onset and wander <= cfg.centroid_wander_bands and not frozen
              and len(c.recent) >= 2 and c.recent[-1] >= max(c.recent) - cfg.sustain_drop_db
              and c.level_db >= cfg.sustain_min_level_db and c.narrow_db >= cfg.narrow_db)
        strong_line = max(c.prominence_db, c.cluster_prominence_db) >= cfg.sustain_strong_prominence_db
        need_frames = cfg.sustain_frames if strong_line else cfg.sustain_moderate_frames
        sustained = ok and c.frames >= need_frames and len(c.recent) >= cfg.sustain_frames
        c.sustained = sustained
        # probe (ring-out)
        if c.probe_base is not None and ts <= c.probe_until:
            if (c.level_db - self.ref_db) - c.probe_base > c.probe_delta + cfg.probe_excess_db:
                c.probe_hits += 1
                c.probe_base = None          # one hit per step
        probe = c.probe_hits >= cfg.probe_confirmations
        clip = c.clip_frames >= cfg.persistence_frames and c.narrow_db >= cfg.narrow_db
        # verdict
        verdict = False
        if c.frames >= cfg.persistence_frames:
            if clip:
                verdict = True
                reasons.append("clip")
            if growth and not strict:
                verdict = True
                reasons.append("growth")
            if sustained and not c.musical:
                verdict = True
                reasons.append("sustained@arm" if c.at_arm else "sustained")
            if probe and not strict:
                verdict = True
                reasons.append("probe")
        if c.step_onset:
            reasons.append("step")
        if c.frozen:
            reasons.append("frozen")
        c.override = verdict and not growth
        c.reasons = tuple(reasons)
        # reported confidence: monotone in the margins, not the decision variable
        conf = (cfg.w_prominence * min(1.0, c.prominence_db / (2.0 * cfg.prominence_db))
                + cfg.w_persistence * min(1.0, c.frames / cfg.sustain_frames)
                + cfg.w_growth * (1.0 if verdict else 0.5 * c.growth_score))
        tot = cfg.w_prominence + cfg.w_persistence + cfg.w_growth
        conf = conf / tot if tot > 0 else (1.0 if verdict else 0.0)
        c.confidence = max(conf, cfg.confidence_threshold) if verdict else min(conf, cfg.confidence_threshold - 1e-6)

    # -- API -----------------------------------------------------------------------------------
    def note_gain_step(self, delta_db: float, ts: float) -> None:
        """Ring-out active probe: the server raised the loop gain by ``delta_db`` at ``ts``. Every live
        candidate records its reference-corrected level; one that then over-responds is regenerating."""
        cfg = self.cfg
        for c in self._cands:
            c.probe_base = c.level_db - self.ref_db
            c.probe_until = ts + cfg.probe_window_s
            c.probe_delta = float(delta_db)
        self._steps.append((ts, float(delta_db)))
        self._steps = [(t, d) for t, d in self._steps if ts - t <= cfg.probe_window_s]

    def note_cut(self, band_hz: float, depth_db: float, ts: float) -> None:
        """A GEQ cut landed (informational; the growth windows near it restart on the drop by themselves)."""
        return None

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
        self.ref_db = self._ref_of(vals)
        self._update_steps(vals)
        clus = [self._cluster(vals, i)[1] for i in range(n)]
        self._chist.append(clus)
        if len(self._chist) > cfg.comove_frames:
            del self._chist[0]
        prom = self.prominences(vals)
        qualifying = []
        for i in range(n):
            if not self._in_window[i] or vals[i] < cfg.min_level_db:
                continue
            if self.narrowness(vals, i) < cfg.narrow_db:
                continue
            if prom[i] >= cfg.prominence_db or self.cluster_prominence(vals, i, clus[i]) >= cfg.prominence_db:
                qualifying.append(i)
        peaks = self._peaks(qualifying, vals)

        used: set[int] = set()
        survivors: list[Candidate] = []
        for c in self._cands:
            best: int | None = None
            for p in peaks:
                if p in used:
                    continue
                dist = abs(p - c.band)
                if dist > cfg.band_tolerance:
                    continue
                if best is None or (dist, -vals[p]) < (abs(best - c.band), -vals[best]):
                    best = p
            if best is None:
                c.missed += 1          # masking transient / threshold flicker: survive gap_frames of it
                if c.missed <= cfg.gap_frames:
                    survivors.append(c)
                continue
            used.add(best)
            c.missed = 0
            self._extend(c, best, vals, prom, ts)
            survivors.append(c)
        for p in peaks:
            if p in used:
                continue
            c = Candidate(band=p, first_ts=ts, frames=0, levels=[], ts_list=[], confidence=0.0)
            c.at_arm = self.frames_seen < cfg.persistence_frames
            self._seed_history(c, p)
            self._extend(c, p, vals, prom, ts)
            if self._steps:   # a live probe step applies to newcomers too (a line that *appears* after a step)
                t0, d0 = self._steps[-1]
                if ts - t0 <= cfg.probe_window_s and c.levels:
                    c.probe_base = min(l - r for l, r in zip(c.levels, c.refs))
                    c.probe_until = t0 + cfg.probe_window_s
                    c.probe_delta = d0
            survivors.append(c)
        survivors.sort(key=lambda c: c.band)
        self._cands = survivors

        out: list[Detection] = []
        for c in self._cands:
            if c.missed:
                continue
            self._score(c, ts)
            if c.confidence >= cfg.confidence_threshold and not self._in_cooldown(c.band, ts):
                det = Detection(
                    ts=ts, band=c.band, freq_hz=c.freq_hz, level_db=c.level_db, prominence_db=c.prominence_db,
                    slope_db_per_s=c.slope_db_per_s, frames=c.frames, confidence=c.confidence,
                    reasons=c.reasons, centroid=round(c.centroid, 3),
                )
                out.append(det)
                c.emitted += 1
                self._cooldown[c.band] = ts + cfg.cooldown_s
                log.debug("feedback detected: band %d (%.0f Hz) %.1f dB slope %.1f dB/s conf %.2f %s",
                          c.band, c.freq_hz, c.level_db, c.slope_db_per_s, c.confidence, ",".join(c.reasons))
        if self._cooldown and self.frames_seen % 100 == 0:
            self._cooldown = {b: t for b, t in self._cooldown.items() if ts < t}
        self._hist.append(vals)
        if len(self._hist) > cfg.history_frames:
            del self._hist[0]
        self.frames_seen += 1
        self.last_ts = ts
        return out

    @property
    def candidates(self) -> list[Candidate]:
        """Current streaks (live objects; treat as read-only), lowest band first."""
        return list(self._cands)

    @property
    def window_hz(self) -> tuple[float, float]:
        return self.cfg.window_hz

    def reset(self) -> None:
        n = len(self.band_hz)
        self._cands = []
        self._cooldown = {}
        self._hist = []
        self._chist = []
        self._step_alive = [False] * n
        self._step_ref = [-128.0] * n
        self._step_floor = [-128.0] * n
        self._step_frame = [-1] * n
        self._steps = []
        self.frames_seen = 0
        self.last_ts = None
        self.ref_db = -128.0


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
