"""CFS² feedback discriminator (sequential evidence accumulation) and notch planner — pure Python, no I/O.

Two synchronous, side-effect-free pieces used by ``cfs.py``:

* :class:`FeedbackDetector` consumes one RTA frame at a time (100 dB values from ``/meters/15``,
  band ``i`` centred at ``band_hz[i]`` = ``10000 * 2 ** ((i - 90) / 10)`` Hz, docs/research/meters.md §4.2)
  and returns :class:`Detection` objects for tracked spectral lines whose accumulated evidence says
  "regenerative electro-acoustic feedback" rather than "programme".
* :class:`NotchController` turns detections into GEQ band cuts (unchanged; see its docstring).

Units and indices at the public boundary
----------------------------------------
* Levels are dB (RTA dB re. full scale, -128 = "no signal", 0.0 = the desk's clip flag), times are
  seconds, slopes are dB/s. **RTA band indices are 0-based**, **GEQ band numbers are 1-based**.

Discriminator (replaces the DESIGN §12 weighted sum; see docs/REVIEW_BRIEF.md §1)
---------------------------------------------------------------------------------
Every narrow spectral line that stands ``track_prominence_db`` above its neighbourhood is tracked as a
:class:`Candidate` (a *hypothesis test*: H_F "this line is a self-oscillating loop" vs H_P "this line is
programme / a stationary environmental tone"). Per frame the detector measures physically motivated
features of the line and adds their **log-likelihood ratio** ln P(feature | H_F) / P(feature | H_P) to the
candidate's evidence ``llr``. Features whose successive observations are strongly correlated (a held organ
note is "still stable" on its 100th frame) enter as *saturating* terms, so nothing is counted twice; the
per-frame increment is the change of each bounded term plus the genuinely sequential terms (growth,
probe response, survival of programme boundaries). The likelihoods are set from the physics briefs
(loop physics: growth = excess/τ, plateau mechanisms, harmonics only at clip, frequency fixed by geometry;
analyser physics: 1/10-octave skirts, per-band rise time 0.5/Δf, harmonic offsets +10/+15.85/+20/+23.2
bands, vibrato through 120-cent bands) — NOT fitted to the validation corpus.

Three-way output per candidate and frame (Wald SPRT with cost-derived thresholds):

* ``llr >= emit_llr``      → EMIT a :class:`Detection` (the caller cuts);
* ``llr <= dismiss_llr``   → DISMISSED-AS-MUSICAL (published with ``state='musical'``, never emitted while
  it stays below; evidence keeps flowing so a loop that later starts to oscillate at a chord-tone's
  frequency can still climb out);
* otherwise                → KEEP WATCHING (published as ``cfs.candidate`` with its evidence breakdown).

The thresholds differ by ``mode`` because the costs differ (REVIEW_BRIEF Q4): in ``watch`` (human on the
fader, programme present, a wrong cut is audible for the rest of the show) EMIT needs posterior odds
≈ 20:1 (``emit_llr_watch`` = 3.0 nats); in ``ringout`` (server owns the gain, no programme by contract, a
missed howl has nobody on the fader, a wrong −3 dB cut costs a report line) ≈ 4:1 (``emit_llr_ringout``
= 1.4). Growth contributes evidence when present but is never required: an already-plateaued ring crosses
EMIT on narrowness + harmonic-family absence + sub-band frequency stability + level steadiness (+ level
near full scale when it is) within 5–6 frames.

Features (each a bounded term of ``Candidate.terms``; thresholds in :class:`DetectorConfig`):

``prior``     mode prior + frequency prior (soft window: ``-lf_penalty_per_third_oct`` per 1/3 octave
              below ``f_low`` — 160 Hz watch / 100 Hz ring-out / 40 Hz with ``lf_feedback_possible`` —
              and above ``f_high`` 12.5 kHz; loop brief §3.4). A prior, not a gate.
``narrow``    single-line test: peak − max(level at ±2 bands) and cluster prominence (power sum of ±1 over
              the median of ±2..±4). Separates lines from humps (formants, cymbal wash, body resonance);
              cannot separate a partial from a ring (analyser brief §3, §0.5) so it is worth little.
``family``    harmonic family presence-as-peak at +10, +15.85, +20, +23.2 bands (±0.75, using the sub-band
              centroid) and "am I somebody's H2/H3" (a comparable peak at −10/−15.85 that owns another
              partial). ≥2 partials or being-a-harmonic ⇒ strong H_P evidence, latched once confirmed
              (lineage: a guitar note that decays to a near-sine was seen with its family). Absence is H_F
              evidence only in proportion to prominence (partials 15 dB down are invisible below ~20 dB
              prominence, analyser brief §4.3). Ignored at the clip flag (a clipped howl grows odd
              harmonics, loop brief §2.2) and for partials that appear only after the line rose ≥10 dB.
``stable``    sub-band centroid (power-weighted over ±1) range over the last 8 frames: < 0.12 band ⇒ H_F
              evidence (a loop frequency is fixed by geometry); oscillation/glide > 0.3 band ⇒ vibrato /
              portamento / pitch envelope ⇒ H_P, latched when seen repeatedly. A one-frame jump followed
              by stability is a *hop* (hand-held mic, loop brief §2.3): same candidate, neutral.
``steady``    RMS of the detrended frame-to-frame cluster-level change over 6 frames: a sinusoid through a
              PEAK detector is deterministic (< 0.25 dB ⇒ H_F evidence); flutter/shimmer/beating/AM of
              voice, whistle, flute, Leslie, chorus (> 0.6 dB) ⇒ H_P. Exactly zero variance ⇒ analyser
              peak-hold/frozen ⇒ no credit (and ``analyser_suspect``).
``shape``     level trajectory relative to the spectrum reference (median of bands 25..85, so a master /
              common-mode move cancels): sustained linear-in-dB rise with near-constant increments for
              longer than the band's own analyser rise time ⇒ H_F evidence per frame (growth = e/τ, loop
              brief §1.3; the analyser's step response is concave and lasts ≈3·τ_a, analyser brief §1, so
              below ~150 Hz growth evidence is down-weighted and needs longer runs); slow linear rise over
              ≥10 frames likewise; appearing at (near) full level within one analyser rise time and then
              flat at a moderate level ⇒ note onset ⇒ H_P (strongly in ring-out where excess ≤ 1 dB makes
              that trajectory nearly impossible); falling ≥2 dB below its own maximum while the reference
              is flat ⇒ plucked/struck/released ⇒ H_P (a loop does not decay while intact).
``level``     RTA clip flag (≥ −1 dBFS) or a narrow line within 10 dB of full scale ⇒ H_F (loop brief P8).
``persist``   survival: ln 1/S(k) for programme lines that already passed the other tests (saturating), plus
              survival of programme boundaries (other lines starting/ending while this one holds ±1 dB).
``probe``     ring-out active probe (:meth:`FeedbackDetector.note_gain_step`): after a known +Δ master step a
              line that over-responds (Δband − Δ ≥ 2 dB, 8-frame medians) is loop-gain dependent ⇒ strong
              H_F; a line that moves ≤ +1 dB/dB is programme / hum / a driven room resonance ⇒ H_P (loop
              brief §1.5, P7); a line born inside the dwell after a step and rising ⇒ H_F.

Graceful degradation: a feature that cannot be measured (edge bands without ±2/±4 neighbours, partial
positions beyond band 99, fewer frames than a window needs, analyser frozen) contributes 0 — neither
hypothesis is favoured — and the remaining terms decide; the EMIT threshold is then simply reached later
(or, for a line that only has "narrow + stable + steady" going for it in watch mode, never: it is
published as a candidate for the human instead).

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
_LN10_OVER_10 = math.log(10.0) / 10.0


def _mode_name(mode: str | None) -> str:
    m = (mode or "watch").lower().replace("-", "_")
    if m in ("watch", "feedback_watch"):
        return "watch"
    if m in ("ringout", "ring_out"):
        return "ringout"
    raise ValueError(f"DetectorConfig: mode must be 'watch' or 'ringout', got {mode!r}")


@dataclass(frozen=True)
class DetectorConfig:
    """Detector + notch thresholds; one field per ``device.yaml`` ``detector:`` key (unknown keys are ignored,
    missing keys take the defaults below, so an older ``device.yaml`` keeps working).

    The legacy weighted-sum keys (``weights``, ``confidence_threshold``, ``growth_*``, ``override_*``,
    ``persistence_frames``, ``monotonic_tolerance_db``) are still parsed so existing descriptors load; the
    sequential-evidence discriminator does not use them (``confidence_threshold`` only scales the reported
    ``confidence`` so that an emitted detection reads ≥ the threshold on the dashboard).
    """

    # -- candidate qualification / tracking -------------------------------------------------------
    prominence_db: float = 12.0          # dB over the median of ±neighbour_bins: required at EMIT time
    neighbour_bins: int = 3              # bands each side used for the median
    track_prominence_db: float = 6.0     # start tracking (accumulating evidence) this early
    emit_min_level_db: float = -100.0    # absolute level floor for EMIT (keep very low: absolute RTA levels float on unread prefs)
    min_level_db: float = -90.0          # LEGACY absolute gate (M7). Not used by the discriminator: it floats on /-prefs/rta/gain and
                                         # a room-fitted value blinds split lines (S10) — the LF prior + baseline evidence replace it
    band_tolerance: int = 1              # peak-to-candidate association radius / cooldown radius (RTA bands)
    coast_frames: int = 3                # frames a candidate survives without a qualifying peak (masking, threshold flicker)
    cooldown_s: float = 1.0              # s between emissions for the same candidate
    # -- mode, window (loop brief §3.4) ------------------------------------------------------------
    mode: str = "watch"                  # 'watch' | 'ringout' (cfs passes it per session)
    f_low_watch_hz: float = 160.0        # soft low edge of the feedback window in watch mode
    f_low_ringout_hz: float = 100.0      # ... in ring-out (no programme by contract, probe available)
    f_low_lf_optin_hz: float = 40.0      # ... when the operator declares an LF-capable source (kick/tom/acoustic near subs/wedges)
    f_high_hz: float = 12500.0           # soft high edge (condenser headsets ring to 12 kHz; SM58 dies above ~10 kHz)
    lf_feedback_possible: bool = False   # session opt-in for LF loops
    lf_penalty_per_third_oct: float = 1.2  # nats per 1/3 octave outside the window
    lf_strict_penalty: float = 1.0       # residual prior against bands < 100 Hz even with the LF opt-in
    prior_llr_watch: float = -1.0        # ln prior odds F:P for a qualifying in-window line, show running
    prior_llr_ringout: float = 0.0       # ... in ring-out
    # -- decision thresholds (nats; REVIEW_BRIEF Q4 cost asymmetry) --------------------------------
    emit_llr_watch: float = 3.0          # ≈ ln 20: a wrong cut on programme during a show costs ~20× a 300 ms later alert
    emit_llr_ringout: float = 1.4        # ≈ ln 4: wrong −3 dB pre-show is cheap, an unattended howl is not
    dismiss_llr_watch: float = -2.5
    dismiss_llr_ringout: float = -3.5    # dismissing wrongly = a miss with nobody on the fader
    llr_floor: float = -6.0              # evidence is clamped to [floor, ceil] so a state can be left again
    llr_ceil: float = 8.0
    # -- feature constants -------------------------------------------------------------------------
    analyser_attack_k: float = 0.5       # per-band rise time τ_a = k / (0.0693 f), clamped 5..600 ms (analyser brief §1, UNCERTAIN)
    ref_band_lo: int = 25                # spectrum reference = median of these bands (τ_a < 1 frame there)
    ref_band_hi: int = 85
    family_present_prom_db: float = 6.0  # a partial "is present" if it is a local peak this prominent
    family_window_bands: float = 0.75    # ± bands around the predicted partial position
    growth_min_step_db: float = 0.35     # per-frame ref-corrected rise that counts as growing (7 dB/s)
    growth_ratio_tol: float = 1.6        # successive increments within this ratio = linear-in-dB (exponential amplitude)
    slow_growth_min_db_per_s: float = 1.5
    steady_rms_db: float = 0.25          # detrended frame-to-frame RMS below this = deterministic (a sinusoid)
    rough_rms_db: float = 0.6            # above this = flutter / shimmer / beating
    stable_range_bands: float = 0.12     # centroid range (8 frames) below this = frequency fixed
    wobble_range_bands: float = 0.30     # above this = vibrato / glide
    hop_min_bands: float = 0.55          # a one-frame centroid jump at least this big, stable either side = hop
    clip_level_db: float = -1.0          # RTA clip flag region
    loud_level_db: float = -10.0         # "narrow line within 10 dB of full scale"
    harmonics_void_level_db: float = -16.0  # a line this loud that rose fast may carry amp/driver distortion partials (loop brief §2.2)
    step_moderate_level_db: float = -15.0  # an instant onset that plateaus below this level is note-like
    probe_settle_frames: int = 3         # frames after a master step before the post-step median starts
    probe_window_frames: int = 8         # frames each side of a step for the medians
    probe_over_db: float = 2.0           # over-response (Δband − Δmaster) that marks a regenerating line
    # per-frame growth evidence (nats) for a validated linear-in-dB rise: multi-frame fast (>= 2.5 dB/frame for >= 3 frames),
    # fast (consistent increments >= 0.9 dB/frame), medium (8-frame fit >= 6 dB/s), slow (12-20 frame fit >= 1.5 dB/s)
    ev_growth_multi: float = 1.2
    ev_growth_fast: float = 1.1
    ev_growth_medium: float = 0.8
    ev_growth_slow: float = 0.35
    iso_nats_per_db: float = 0.08          # isolation evidence per dB of cluster prominence above 24 dB (narrow lines only)
    iso_nats_max: float = 2.4
    # -- notch planning (unchanged) ----------------------------------------------------------------
    notch_step_db: float = -3.0          # per detection (negative)
    notch_max_db: float = -9.0           # deepest cut (negative, <= notch_step_db)
    notch_budget_default: int = 6        # distinct GEQ bands per session
    merge_adjacent_bands: int = 1        # GEQ bands: a detection this close to a notch deepens it
    decay_verify_db: float = 6.0         # used by cfs.py (VERIFY stage)
    decay_verify_s: float = 1.5          # used by cfs.py
    decay_verify_frames: int = 2         # consecutive frames that must show the drop (cfs.py)
    frame_period_s: float = 0.05         # nominal RTA frame period (informational; the detector uses timestamps)
    # -- legacy keys (parsed, not used by the discriminator) ----------------------------------------
    persistence_frames: int = 3
    growth_min_db_per_s: float = 6.0
    growth_ref_db_per_s: float = 20.0
    growth_max_db_per_s: float = 60.0
    monotonic_tolerance_db: float = 1.0
    w_prominence: float = 0.3
    w_persistence: float = 0.2
    w_growth: float = 0.5
    confidence_threshold: float = 0.7
    growth_window_frames: int = 60
    override_prominence_db: float = 25.0
    override_persistence_frames: int = 6

    def __post_init__(self) -> None:
        def need(cond: bool, msg: str) -> None:
            if not cond:
                raise ValueError(f"DetectorConfig: {msg}")

        object.__setattr__(self, "mode", _mode_name(self.mode))
        need(self.prominence_db > 0, "prominence_db must be > 0")
        need(0 < self.track_prominence_db <= self.prominence_db, "track_prominence_db must be in (0, prominence_db]")
        need(self.neighbour_bins >= 1, "neighbour_bins must be >= 1")
        need(self.band_tolerance >= 0, "band_tolerance must be >= 0")
        need(self.coast_frames >= 0, "coast_frames must be >= 0")
        need(self.cooldown_s >= 0, "cooldown_s must be >= 0")
        need(0 < self.f_low_lf_optin_hz <= self.f_low_ringout_hz <= self.f_low_watch_hz < self.f_high_hz, "window edges out of order")
        need(self.lf_penalty_per_third_oct >= 0 and self.lf_strict_penalty >= 0, "penalties must be >= 0")
        need(self.dismiss_llr_watch < self.emit_llr_watch and self.dismiss_llr_ringout < self.emit_llr_ringout, "dismiss must be below emit")
        need(self.llr_floor < min(self.dismiss_llr_watch, self.dismiss_llr_ringout), "llr_floor must be below the dismiss thresholds")
        need(self.llr_ceil > max(self.emit_llr_watch, self.emit_llr_ringout), "llr_ceil must be above the emit thresholds")
        need(0.1 <= self.analyser_attack_k <= 2.0, "analyser_attack_k out of range")
        need(0 <= self.ref_band_lo < self.ref_band_hi, "ref bands out of order")
        need(self.growth_min_step_db > 0 and self.growth_ratio_tol > 1.0, "growth constants invalid")
        need(0 < self.steady_rms_db < self.rough_rms_db, "steady_rms_db must be below rough_rms_db")
        need(0 < self.stable_range_bands < self.wobble_range_bands, "stable_range_bands must be below wobble_range_bands")
        need(self.probe_window_frames >= 3 and self.probe_settle_frames >= 0, "probe windows invalid")
        need(self.notch_step_db < 0, "notch_step_db must be negative (cuts only)")
        need(self.notch_max_db <= self.notch_step_db, "notch_max_db must be <= notch_step_db")
        need(self.notch_budget_default >= 0, "notch_budget_default must be >= 0")
        need(self.decay_verify_frames >= 1, "decay_verify_frames must be >= 1")
        need(self.merge_adjacent_bands >= 0, "merge_adjacent_bands must be >= 0")
        # legacy sanity (kept so old descriptors still fail loudly on nonsense)
        need(self.persistence_frames >= 1, "persistence_frames must be >= 1")
        need(self.growth_ref_db_per_s > 0, "growth_ref_db_per_s must be > 0")
        need(min(self.w_prominence, self.w_persistence, self.w_growth) >= 0, "weights must be >= 0")
        need(self.confidence_threshold > 0, "confidence_threshold must be > 0")
        need(self.override_prominence_db >= 0 and self.override_persistence_frames >= 1, "override_* invalid")

    # -- mode-resolved views -------------------------------------------------------------------------
    @property
    def emit_llr(self) -> float:
        return self.emit_llr_ringout if self.mode == "ringout" else self.emit_llr_watch

    @property
    def dismiss_llr(self) -> float:
        return self.dismiss_llr_ringout if self.mode == "ringout" else self.dismiss_llr_watch

    @property
    def prior_llr(self) -> float:
        return self.prior_llr_ringout if self.mode == "ringout" else self.prior_llr_watch

    @property
    def f_low_hz(self) -> float:
        if self.lf_feedback_possible:
            return self.f_low_lf_optin_hz
        return self.f_low_ringout_hz if self.mode == "ringout" else self.f_low_watch_hz

    @property
    def weights(self) -> dict[str, float]:
        return {"prominence": self.w_prominence, "persistence": self.w_persistence, "growth": self.w_growth}

    def with_mode(self, mode: str, *, lf_feedback_possible: bool | None = None) -> "DetectorConfig":
        """Copy with ``mode`` (and optionally the LF opt-in) changed — what ``cfs`` calls per session."""
        import dataclasses
        kw: dict[str, Any] = {"mode": _mode_name(mode)}
        if lf_feedback_possible is not None:
            kw["lf_feedback_possible"] = bool(lf_feedback_possible)
        return dataclasses.replace(self, **kw)

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
            if t == "int":
                kw[key] = int(value)
            elif t == "bool":
                kw[key] = bool(value) if not isinstance(value, str) else value.strip().lower() in ("1", "true", "yes", "on")
            elif t == "str":
                kw[key] = str(value)
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
    """One feedback verdict. ``band`` is the 0-based RTA band nearest the line, ``freq_hz`` the sub-band
    interpolated frequency (log-frequency centroid of the ±1 cluster), ``llr`` the accumulated evidence in
    nats and ``reasons`` the terms that carried it (for the notch report)."""

    ts: float
    band: int
    freq_hz: float
    level_db: float
    prominence_db: float
    slope_db_per_s: float
    frames: int
    confidence: float
    llr: float = 0.0
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
            "llr": self.llr,
            "reasons": list(self.reasons),
        }


_TERMS = ("prior", "narrow", "family", "stable", "steady", "shape", "level", "persist", "probe")


@dataclass
class Candidate:
    """A tracked spectral line and its evidence. ``levels``/``ts_list`` hold the recent cluster-level history
    (bounded); ``frames`` counts frames since ``first_ts`` (including coasted ones); ``terms`` is the evidence
    breakdown (nats) whose clamped sum is ``llr``; ``state`` is 'watching' | 'feedback' | 'musical'."""

    band: int
    first_ts: float
    frames: int = 0
    levels: list[float] = field(default_factory=list)      # cluster level (power sum of ±1), dB
    ts_list: list[float] = field(default_factory=list)
    confidence: float = 0.0
    override: bool = False                                 # legacy: True when emitted with no growth evidence
    freq_hz: float = 0.0
    level_db: float = -128.0                               # peak single-band level, dB
    prominence_db: float = 0.0
    slope_db_per_s: float = 0.0                            # ref-corrected slope over the recent window
    growth_score: float = 0.0                              # legacy: growth evidence mapped to 0..1
    last_ts: float = 0.0
    emitted: int = 0
    # -- evidence -----------------------------------------------------------------------------------
    llr: float = 0.0
    terms: dict[str, float] = field(default_factory=lambda: {k: 0.0 for k in _TERMS})
    state: str = "watching"
    centroid: float = 0.0                                  # sub-band position (band units, float)
    cents: list[float] = field(default_factory=list)       # centroid history (intrusion-filtered)
    peak_levels: list[float] = field(default_factory=list) # single-band peak level history
    refs: list[float] = field(default_factory=list)        # spectrum reference at each frame
    corr: list[float] = field(default_factory=list)        # ref-corrected cluster level history
    skirts: list[tuple[float, float]] = field(default_factory=list)   # (left, right) neighbour level − peak level
    cl_prom_db: float = 0.0
    narrow_db: float = 0.0
    narrow_ew: float = 0.0                                 # smoothed narrowness evidence
    coast: int = 0
    seen: int = 0                                          # frames actually observed (not coasted)
    est_frames: int = 0                                    # frames at >= prominence_db (an established line)
    run_max_db: float = -200.0
    first_level_db: float = -200.0
    pre_birth_db: float = -200.0                           # the cluster's level on the frame before the line appeared
    pre_birth_peak_db: float = -200.0                      # the peak BAND's own level on that frame (a legato neighbour note does not count)
    onset_done: bool = False
    step_pen: float = 0.0
    fam_frames: int = 0                                    # frames with >=2 partials present
    sub_frames: int = 0                                    # frames on which the line sat at a harmonic offset above an owner
    fam1_frames: int = 0
    fam_obs: int = 0
    family_latched: bool = False
    family_level_db: float = -200.0
    wobble_windows: int = 0
    stable_windows: int = 0
    modulated_latched: bool = False
    grow_run: int = 0
    grow_acc: float = 0.0                                  # accumulated growth evidence (part of shape)
    slow_acc: float = 0.0
    decay_acc: float = 0.0
    boundary_acc: float = 0.0
    probe_acc: float = 0.0
    probe_done: set = field(default_factory=set)           # master steps already evaluated for this candidate
    probe_pre: dict = field(default_factory=dict)           # step key -> (median level, median ref, level range, max step) at the step
    probe_evals: int = 0                                   # probe comparisons actually made for this line
    sub_owner_db: float = -200.0                           # level of the lower line this one last sat above at a harmonic offset
    sub_self_db: float = -200.0
    last_emit_ts: float = -1e9
    last_emit_level: float = -200.0
    hop_count: int = 0
    born_after_step: float | None = None                  # seconds after the latest master step at birth (ring-out)
    birth_frame: int = 0
    fast_rise: int = 0                                     # consecutive multi-dB per-frame rises seen at/after birth (sub-frame τ_a bands)
    fast_acc: float = 0.0
    fast_peak: int = 0                                     # longest run of fast increments seen (sticky)
    harm_flags: tuple = ()
    frozen: bool = False                                   # display looks frozen (peak-hold): exact-zero steps dominate
    dominant_frames: int = 0                               # consecutive frames as the loudest established line by >= 2 dB
    birth_jump_db: float = 0.0
    nf_hist: list[float] = field(default_factory=list)     # neighbourhood floor history (median of ±2..±4)
    masked: int = 0                                        # consecutive frames masked by a local broadband transient
    floor_bonus: float = 0.0                               # one-off: the line emerged from the floor and kept rising (loop brief P9)
    sync_frames: int = 0
    sync_base: float = 0.0
    sync_base_parts: tuple = (0.0, 0.0, 0.0)
    growing_now: bool = False
    rising_now: bool = False
    trough_db: float = 200.0                               # lowest cluster level since the line last fell >= 8 dB under its maximum
    fell: bool = False
    rearmed: int = 0

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
            "llr": round(self.llr, 3),
            "state": self.state,
            "terms": {k: round(v, 3) for k, v in self.terms.items()},
            "centroid": round(self.centroid, 3),
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


def _ls_fit(ts: Sequence[float], vs: Sequence[float]) -> tuple[float, float, float]:
    """(slope dB/s, max |residual| dB, residual std dB) of a straight-line fit."""
    n = len(ts)
    if n < 3:
        return 0.0, 0.0, 0.0
    tm = sum(ts) / n
    vm = sum(vs) / n
    sxx = sum((t - tm) ** 2 for t in ts)
    if sxx <= 0.0:
        return 0.0, 0.0, 0.0
    slope = sum((t - tm) * (v - vm) for t, v in zip(ts, vs)) / sxx
    res = [v - (vm + slope * (t - tm)) for t, v in zip(ts, vs)]
    return slope, max(abs(r) for r in res), math.sqrt(sum(r * r for r in res) / n)


def _sigma_iid(vs: Sequence[float]) -> float:
    """White-noise level estimate of a series from its first differences (sd(diff)/sqrt 2): a trend or a slow wobble
    contributes little to it, frame-to-frame measurement jitter contributes fully."""
    if len(vs) < 4:
        return 0.0
    d = [vs[i + 1] - vs[i] for i in range(len(vs) - 1)]
    m = sum(d) / len(d)
    return math.sqrt(sum((x - m) ** 2 for x in d) / len(d)) / math.sqrt(2.0)


def _pow(db: float) -> float:
    return math.exp(db * _LN10_OVER_10)


def _db(p: float) -> float:
    return 10.0 * math.log10(p) if p > 0 else -200.0


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


_HARM_OFFSETS = (10.0, 15.85, 20.0, 23.22)            # H2..H5 in 1/10-octave bands (analyser brief §4.1)
_HARM_OFFSETS_ALL = (10.0, 15.85, 20.0, 23.22, 25.85, 28.07, 30.0)
_HIST = 24                                             # frames of per-candidate history kept


class FeedbackDetector:
    """Frame-by-frame feedback discriminator over an RTA stream (see module docstring)."""

    def __init__(self, cfg: DetectorConfig, band_hz: Sequence[float], *, mode: str | None = None,
                 lf_feedback_possible: bool | None = None) -> None:
        if mode is not None or lf_feedback_possible is not None:
            cfg = cfg.with_mode(mode or cfg.mode, lf_feedback_possible=lf_feedback_possible)
        self.cfg = cfg
        self.band_hz: tuple[float, ...] = tuple(float(h) for h in band_hz)
        n = len(self.band_hz)
        if n < 2 * cfg.neighbour_bins + 1:
            raise ValueError("band_hz too short for neighbour_bins")
        self._n = n
        self._log_hz = [math.log(h) for h in self.band_hz]
        T = cfg.frame_period_s
        # per-band analyser rise time (s) and what it implies for growth evidence (analyser brief §1)
        self._tau_a = [_clamp(cfg.analyser_attack_k / (0.06932 * h), 0.005, 0.6) for h in self.band_hz]
        self._grow_weight = [_clamp((0.85 - math.exp(-T / ta)) / 0.5, 0.0, 1.0) for ta in self._tau_a]
        self._grow_min_run = [max(2, int(round(3.0 * ta / T))) for ta in self._tau_a]
        self._onset_frames = [max(1, int(math.ceil(2.0 * ta / T))) for ta in self._tau_a]
        self._prior_band = [self._frequency_prior(h) for h in self.band_hz]
        self._cands: list[Candidate] = []
        self._cooldown: dict[int, float] = {}
        self.frames_seen: int = 0
        self.last_ts: float | None = None
        self._prev_vals: list[float] | None = None
        self._hist_vals: list[list[float]] = []             # the two frames before _prev_vals (pre-birth trajectory of new lines)
        self._ref_hist: list[float] = []
        self._ref_raw: list[float] = []
        self._steps: list[tuple[float, float]] = []          # (ts, delta_db) master steps noted by cfs (ring-out probe)
        self._cuts: list[tuple[float, float, float]] = []    # (ts, hz, depth) cuts noted by cfs
        self.analyser_suspect: bool = False
        self._frozen_frac: float = 0.0
        self._sync_ts: float = -1e9
        self._ref_jump_ts: float = -1e9

    # -- static helpers ------------------------------------------------------------------------------
    def _frequency_prior(self, hz: float) -> float:
        cfg = self.cfg
        p = cfg.prior_llr
        third = math.log(2.0) / 3.0
        if hz < cfg.f_low_hz:
            p -= cfg.lf_penalty_per_third_oct * (math.log(cfg.f_low_hz / hz) / third)
        if cfg.lf_feedback_possible and hz < 100.0:
            p -= cfg.lf_strict_penalty
        if hz > cfg.f_high_hz:
            p -= cfg.lf_penalty_per_third_oct * (math.log(hz / cfg.f_high_hz) / third)
        return p

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

    def _in_cooldown(self, band: int, ts: float) -> bool:
        tol = self.cfg.band_tolerance
        for b in range(band - tol, band + tol + 1):
            until = self._cooldown.get(b)
            if until is not None and ts < until:
                return True
        return False

    # -- per-frame spectral measurements ----------------------------------------------------------------
    def _cluster(self, vals: Sequence[float], b: int, anchor: float | None = None) -> tuple[float, float]:
        """(cluster level dB, centroid in band units relative to b). The cluster is the bands within ±1.6 of ``anchor``
        (the line's previous sub-band position; default b) inside b-2..b+2: for a line sitting on a band edge the loudest
        band alternates between the pair, and anchoring keeps the same bands in the sum so the centroid does not flap
        (analyser brief §3: a line is one band or two adjacent bands plus skirts)."""
        n = self._n
        a = float(b) if anchor is None else anchor
        tot = 0.0
        mom = 0.0
        for j in range(max(0, b - 2), min(n - 1, b + 2) + 1):
            if abs(j - a) > 1.6:
                continue
            p = _pow(vals[j])
            tot += p
            mom += (j - b) * p
        return _db(tot), (mom / tot if tot > 0 else 0.0)

    def _cluster_prom(self, vals: Sequence[float], b: int, cl_db: float) -> float | None:
        n = self._n
        far = [vals[j] for d in (2, 3, 4) for j in (b - d, b + d) if 0 <= j < n]
        if len(far) < 3:
            return None
        return cl_db - median(far)

    def _narrow(self, vals: Sequence[float], b: int) -> float | None:
        nn = [vals[j] for j in (b - 2, b + 2) if 0 <= j < self._n]
        if not nn:
            return None
        return vals[b] - max(nn)

    def _is_peak(self, vals: Sequence[float], prom: Sequence[float], j: int, min_prom: float, min_level: float) -> bool:
        n = self._n
        if not 0 <= j < n:
            return False
        if vals[j] < min_level or prom[j] < min_prom:
            return False
        left = vals[j - 1] if j - 1 >= 0 else -1e9
        right = vals[j + 1] if j + 1 < n else -1e9
        return vals[j] >= left and vals[j] >= right

    def _partial_present(self, vals: Sequence[float], prom: Sequence[float], pos: float, min_level: float,
                         weak: bool = False) -> bool | None:
        """Is there a local peak (>= family_present_prom_db prominent, >= min_level) within ±family_window of
        fractional band position ``pos``? None when the position is off the analyser (feature unavailable).
        ``weak``: accept a local maximum standing >= 4 dB over the lowest of its ±2 neighbours — in a dense chord the
        ±3 median is itself made of partials and the strict prominence test goes blind (analyser brief §4.1)."""
        n = self._n
        w = self.cfg.family_window_bands
        lo = int(math.floor(pos - w))
        hi = int(math.ceil(pos + w))
        if lo > n - 1 or hi < 0:
            return None
        any_in = False
        for j in range(max(0, lo), min(n - 1, hi) + 1):
            if abs(j - pos) > w + 1e-9:
                continue
            any_in = True
            if self._is_peak(vals, prom, j, self.cfg.family_present_prom_db, min_level):
                return True
            if weak and vals[j] >= min_level and self._is_peak(vals, prom, j, -99.0, min_level):
                nb = [vals[k] for k in (j - 2, j - 1, j + 1, j + 2) if 0 <= k < n and k != j]
                if nb and vals[j] - min(nb) >= 4.0:
                    return True
        return False if any_in else None

    def _family(self, vals: Sequence[float], prom: Sequence[float], pos: float, level: float) -> tuple[int, int, bool, tuple]:
        """(partials present among H2..H5, partial positions available, is-somebody's-harmonic, per-partial flags)."""
        present = 0
        weak = 0
        avail = 0
        flags: list[bool | None] = []
        plv: list[float | None] = []
        for off in _HARM_OFFSETS:
            r = self._partial_present(vals, prom, pos + off, level - 30.0)
            flags.append(r)
            q = pos + off
            jj = [j for j in (int(math.floor(q)), int(math.ceil(q))) if 0 <= j < self._n]
            plv.append(max((vals[j] for j in jj), default=None) if r else None)
            if r is None:
                continue
            avail += 1
            if r:
                present += 1
                weak += 1
            elif self._partial_present(vals, prom, pos + off, level - 24.0, weak=True):
                weak += 1
        if present < 2 and weak >= 3:
            present = 2                        # three of H2..H5 stand out locally in a dense spectrum: a family all the same
        # odd-dominant partials (H3 above H2, H5 not below H4) are the signature of symmetric clipping, not of a voice or an
        # instrument (loop brief §2.2; analyser brief §4.3) — reported through the flags tuple's last slot
        l2, l3, l4, l5 = plv
        odd_dom = l3 is not None and (l2 is None or l3 >= l2 + 3.0) and (l5 is None or l4 is None or l5 >= l4)
        flags.append(odd_dom)
        sub = False
        owner_db: float | None = None
        w = self.cfg.family_window_bands
        for off in (10.0, 15.85, 20.0):
            base = pos - off
            for j in range(max(0, int(math.floor(base - w))), min(self._n - 1, int(math.ceil(base + w))) + 1):
                if abs(j - base) > w + 1e-9:
                    continue
                if not self._is_peak(vals, prom, j, self.cfg.family_present_prom_db, level - 12.0):
                    continue
                # j is a comparable-or-louder lower peak at a harmonic offset below us; does it own another partial?
                _, cj = self._cluster(vals, j)
                pj = j + cj
                for oo in _HARM_OFFSETS:
                    if abs(oo - off) < 0.5:
                        continue
                    q = pj + oo
                    if abs(q - pos) <= 1.0:
                        continue
                    if self._partial_present(vals, prom, q, vals[j] - 30.0):
                        sub = True
                        owner_db = vals[j]
                        break
                if sub:
                    break
            if sub:
                break
        flags.append(owner_db)
        return present, avail, sub, tuple(flags)

    # -- tracking --------------------------------------------------------------------------------------
    def _new_candidate(self, b: int, ts: float, vals: Sequence[float]) -> Candidate:
        c = Candidate(band=b, first_ts=ts)
        c.terms = {k: 0.0 for k in _TERMS}
        c.birth_frame = self.frames_seen
        if self._steps:
            c.born_after_step = ts - self._steps[-1][0]
            # steps that predate this line cannot be evaluated against it (no "before" window); only the latest one may
            # explain its birth (onset time-locked to the server's own gain change)
            for (t_step, _d) in self._steps[:-1]:
                c.probe_done.add(round(t_step, 3))
            if not (0.1 <= c.born_after_step <= 1.2):
                c.probe_done.add(round(self._steps[-1][0], 3))
        if self._prev_vals is not None:
            c.pre_birth_db, _ = self._cluster(self._prev_vals, b)
            c.pre_birth_peak_db = self._prev_vals[b]
            # was this band already climbing before the line qualified? (a loop rising out of the bed at e/τ shows 1-3
            # frames of rise below the tracking threshold; a note does not — analyser brief §0.1, loop brief §1.3)
            hist = [self._cluster(v, b)[0] for v in self._hist_vals] + [c.pre_birth_db]
            run = 0
            for i in range(len(hist) - 1, 0, -1):
                if hist[i] - hist[i - 1] >= 2.0:
                    run += 1
                else:
                    break
            if run:
                c.fast_rise = run
                c.pre_birth_db = hist[len(hist) - 1 - run]
        else:
            c.pre_birth_db = -200.0     # present at arm: no onset observed
        return c

    def _rearm(self, c: Candidate, ts: float, pre_db: float) -> None:
        """A new event has started at this line's frequency (it decayed away and is now rising again — a syllable / chord
        tone ended and a loop, or another note, took over the band): test it as a fresh hypothesis. Lineage that belongs
        to the *previous* event (family, vibrato, onset shape, growth) is dropped; position and probe bookkeeping stay."""
        keep_pos = c.centroid
        c.fam_frames = c.sub_frames = c.fam1_frames = 0
        c.fam_obs = 0
        c.family_latched = False
        c.modulated_latched = False
        c.wobble_windows = c.stable_windows = 0
        c.hop_count = 0
        c.grow_acc = c.slow_acc = c.fast_acc = c.floor_bonus = 0.0
        c.grow_run = c.fast_rise = c.fast_peak = 0
        c.decay_acc = 0.0
        c.step_pen = 0.0
        c.onset_done = False
        c.boundary_acc = 0.0
        c.narrow_ew = 0.0
        c.dominant_frames = 0
        c.first_ts = ts
        c.frames = 0
        c.seen = 0
        c.est_frames = 0
        c.run_max_db = -200.0
        c.first_level_db = -200.0
        c.pre_birth_db = pre_db
        c.birth_frame = self.frames_seen
        c.fell = False
        c.trough_db = 200.0
        c.rearmed += 1
        del c.levels[:-1], c.peak_levels[:-1], c.ts_list[:-1], c.refs[:-1], c.corr[:-1], c.cents[:-1]
        c.levels.clear(); c.peak_levels.clear(); c.ts_list.clear(); c.refs.clear(); c.corr.clear(); c.cents.clear()
        c.skirts.clear()
        c.centroid = keep_pos
        c.terms = {k: 0.0 for k in _TERMS}
        if self._steps:
            c.born_after_step = ts - self._steps[-1][0]
            c.probe_done = {round(t, 3) for (t, _d) in self._steps[:-1]}
            if not (0.1 <= c.born_after_step <= 1.2):
                c.probe_done.add(round(self._steps[-1][0], 3))

    def _observe_after_rearm(self, c: Candidate, obs: dict[str, Any], cl_db: float, ts: float) -> None:
        """Seed the fresh hypothesis with the current frame (called when a re-arm happens during evidence evaluation)."""
        c.frames = 1
        c.seen = 1
        c.first_level_db = cl_db
        c.birth_jump_db = (cl_db - c.pre_birth_db) if c.pre_birth_db > -199.0 else 0.0
        c.run_max_db = cl_db
        c.levels.append(cl_db)
        c.peak_levels.append(c.level_db)
        c.ts_list.append(ts)
        c.refs.append(self._ref_hist[-1] if self._ref_hist else 0.0)
        c.corr.append(cl_db - (self._ref_hist[-1] if self._ref_hist else 0.0))
        c.cents.append(obs["pos"])
        if max(c.prominence_db, c.cl_prom_db) >= self.cfg.prominence_db:
            c.est_frames = 1

    def _observe(self, c: Candidate, b: int, vals: Sequence[float], prom: Sequence[float], ref: float, ts: float) -> dict[str, Any]:
        """Measure this frame's features for candidate ``c`` at peak band ``b`` and update its histories."""
        cfg = self.cfg
        n = self._n
        anchor = c.centroid if (c.seen >= 1 and abs(c.centroid - b) <= 1.05) else None
        cl_db, cent = self._cluster(vals, b, anchor)
        pos = b + cent
        # local broadband transient (hi-hat, cymbal, chord onset next to the line): the neighbourhood floor jumps while the
        # spectrum reference does not follow fully. The line's own reading is contaminated on such frames: keep the track,
        # skip the measurement (a masked frame is not evidence either way).
        far = [vals[j] for d in (2, 3, 4) for j in (b - d, b + d) if 0 <= j < n]
        nf = median(far) if far else -128.0
        ref_prev = self._ref_hist[-2] if len(self._ref_hist) >= 2 else ref
        masked = False
        if len(c.nf_hist) >= 4:
            base_nf = median(c.nf_hist[-8:])
            own_rise = (vals[b] - c.peak_levels[-1]) if c.peak_levels else 0.0
            if (nf - base_nf) - max(0.0, ref - ref_prev) >= 4.0 and (nf - base_nf) >= 4.0 and own_rise < (nf - base_nf):
                masked = True                  # (not when the line itself out-climbs the neighbourhood: that is its own event)
        c.nf_hist.append(nf)
        if len(c.nf_hist) > 12:
            del c.nf_hist[0]
        if masked and c.masked < 6:
            c.masked += 1
            c.frames += 1
            c.coast = 0
            c.band = b
            c.last_ts = ts
            c.level_db = vals[b]
            c.prominence_db = prom[b]
            return {"masked": True, "cl_db": cl_db}
        c.masked = 0
        # neighbour intrusion: a programme partial landing in b±1 lifts that skirt while the peak band itself does not
        # move; a real FM of THIS line moves energy out of the peak band (anti-correlated, analyser brief §4.4).
        left = (vals[b - 1] - vals[b]) if b - 1 >= 0 else -60.0
        right = (vals[b + 1] - vals[b]) if b + 1 < n else -60.0
        intrusion = False
        if c.seen >= 3 and c.band == b and c.skirts and c.peak_levels:
            ml = median([s[0] for s in c.skirts[-6:]])
            mr = median([s[1] for s in c.skirts[-6:]])
            pk = c.peak_levels
            trend = (pk[-1] - pk[-3]) / 2.0 if len(pk) >= 3 else 0.0        # the peak band's own recent per-frame slope
            dpk = abs(vals[b] - (pk[-1] + trend))                            # is the peak on its own trajectory?
            if dpk < 1.0 and ((left - ml > 3.0 and left > -15.0) or (right - mr > 3.0 and right > -15.0)):
                intrusion = True
        if intrusion:
            pos = c.cents[-1] if c.cents else pos
            cl_db = (c.levels[-1] + (vals[b] - c.peak_levels[-1])) if c.levels else cl_db
        else:
            c.skirts.append((left, right))
            if len(c.skirts) > 12:
                del c.skirts[0]
        c.frames += 1
        c.seen += 1
        c.coast = 0
        c.band = b
        c.centroid = pos
        c.level_db = vals[b]
        c.prominence_db = prom[b]
        c.last_ts = ts
        cp = self._cluster_prom(vals, b, cl_db)
        c.cl_prom_db = cp if cp is not None else prom[b]
        nw = self._narrow(vals, b)
        c.narrow_db = nw if nw is not None else 0.0
        if max(c.prominence_db, c.cl_prom_db) >= cfg.prominence_db:
            c.est_frames += 1
        f_est = math.exp(self._log_hz[0] + pos * (math.log(2.0) / 10.0))
        c.freq_hz = f_est if abs(pos - b) <= 1.0 else self.band_hz[b]
        # a new, much louder event landing on top of an established weaker line (a loop taking off in a band that held a
        # programme partial; a note struck where a tail was): >= 8 dB up within <= 2 frames while the old occupant was not
        # itself rising. What was measured so far describes the old occupant — test the newcomer afresh.
        if c.seen >= 4 and c.grow_run <= 1 and c.fast_rise <= 1 and c.grow_acc <= 0.0 and len(c.levels) >= 2 and not c.fell:
            l1, l2 = c.levels[-1], c.levels[-2]
            if cl_db >= min(l1, l2) + 8.0:
                prior_rise = (l1 - l2) >= 2.5                  # the previous frame was already the newcomer climbing
                self._rearm(c, ts, l2 if prior_rise else l1)
                if prior_rise and (cl_db - l1) >= 2.5:
                    c.fast_rise = c.fast_peak = 1              # that first climbing frame counts toward the multi-frame fast rise
        # renewed onset at the same frequency: the previous event decayed >= 8 dB and the line is climbing again
        if c.run_max_db > -199.0 and cl_db <= c.run_max_db - 8.0:
            c.fell = True
        if c.fell:
            c.trough_db = min(c.trough_db, cl_db)
            if cl_db >= c.trough_db + 4.0 and len(c.levels) >= 2 and c.levels[-1] > c.levels[-2] and cl_db > c.levels[-1]:
                self._rearm(c, ts, c.trough_db)
        if c.first_level_db <= -199.0:
            c.first_level_db = cl_db
            c.birth_jump_db = (cl_db - c.pre_birth_db) if c.pre_birth_db > -199.0 else 0.0
        c.run_max_db = max(c.run_max_db, cl_db)
        c.levels.append(cl_db)
        c.peak_levels.append(vals[b])
        c.ts_list.append(ts)
        c.refs.append(ref)
        c.corr.append(cl_db - ref)
        c.cents.append(pos)
        for lst in (c.levels, c.peak_levels, c.ts_list, c.refs, c.corr, c.cents):
            if len(lst) > _HIST:
                del lst[0]
        fam_n, fam_avail, sub, flags = self._family(vals, prom, pos, vals[b])
        c.harm_flags = flags
        return {"cl_db": cl_db, "pos": pos, "fam_n": fam_n, "fam_avail": fam_avail, "sub": sub, "narrow": nw,
                "cl_prom": cp, "intrusion": intrusion, "flags": flags, "masked": False}

    # -- evidence --------------------------------------------------------------------------------------
    def _update_evidence(self, c: Candidate, obs: dict[str, Any], ts: float, boundary_w: float, n_other: int, loudest_other: float,
                         n_growing_other: int = 0) -> None:
        cfg = self.cfg
        T = cfg.frame_period_s
        b = c.band
        lvl = c.level_db
        cl_db = obs["cl_db"]
        t = c.terms

        # ---- prior: mode + frequency (a prior, not a gate; follows the line if it hops) ----------------
        pr = self._prior_band[b]
        if cfg.mode == "ringout" and n_other > 0:
            pr -= min(2.0, 0.7 * math.log1p(n_other))   # "no programme" contract visibly violated: base rate falls with every extra line
        t["prior"] = pr

        # ---- narrow: line vs hump (weak; smoothed so one masked frame does not flip it) -----------------
        nw = obs["narrow"]
        cp = obs["cl_prom"]
        if nw is None or cp is None:
            x = 0.0
        elif nw >= 15.0 and cp >= 15.0:
            x = 0.4
        elif nw < 8.0 and cp < 12.0:
            x = -1.0
        else:
            x = 0.4 * _clamp((min(nw, cp) - 8.0) / 7.0, 0.0, 1.0)
        c.narrow_ew += (x - c.narrow_ew) * (1.0 if c.seen <= 1 else 0.3)
        # isolation beyond what programme produces: a line standing >= 25-30 dB over EVERYTHING within ±4 bands is, short of
        # a test tone or a lone sine synth in silence, a howl (loop brief §4.3 tier A; the M7 8 kHz line read 60 dB). Each
        # further dB makes the programme explanations rarer: +iso_nats_per_db per dB of cluster prominence above 24, capped.
        iso = 0.0
        if cp is not None and nw is not None and nw >= 15.0:
            iso = _clamp(cfg.iso_nats_per_db * (min(cp, c.prominence_db + 6.0) - 24.0), 0.0, cfg.iso_nats_max)
        t["narrow"] = c.narrow_ew + iso * _clamp(c.seen / 3.0, 0.34, 1.0)

        # ---- family: presence-as-peak, lineage latch, clip escape ---------------------------------------
        clip = cl_db >= cfg.clip_level_db - 0.5 or lvl >= cfg.clip_level_db     # an edge tone at 0 dBFS reads -3/-3
        base_lv = min(c.first_level_db, c.pre_birth_db) if c.pre_birth_db > -199.0 else c.first_level_db
        rose = cl_db - base_lv
        flags = obs["flags"]
        odd_only = len(flags) >= 5 and bool(flags[4])                        # odd-dominant partial pattern: symmetric clip
        if obs["fam_avail"] > 0 or obs["sub"]:
            c.fam_obs += 1
            late = rose >= 10.0 and c.fam_frames == 0 and c.seen > 3     # partials that appear only after the line grew: distortion
            if obs["fam_n"] >= 2 and not late:
                c.fam_frames += 1
            elif obs["fam_n"] == 1:
                c.fam1_frames += 1
            if obs["sub"]:
                # a partial co-moves with its fundamental; a loop that merely sits at somebody's H2..H4 position does not.
                # When the owner's level moved >= 3 dB since the last coincidence and this line did not follow, the
                # coincidence is not counted (speech / a moving melody under a fixed ring, X9-type).
                owner_now = flags[5] if len(flags) >= 6 and flags[5] is not None else None
                comoving = True
                if owner_now is not None and c.sub_owner_db > -199.0:
                    d_own = owner_now - c.sub_owner_db
                    d_me = cl_db - c.sub_self_db
                    if abs(d_own) >= 3.0 and abs(d_me - d_own) > 0.6 * abs(d_own):
                        comoving = False
                if owner_now is not None:
                    c.sub_owner_db = owner_now
                    c.sub_self_db = cl_db
                if comoving:
                    c.sub_frames += 1
                else:
                    c.sub_frames = max(0, c.sub_frames - 1)
        fo = max(1, c.fam_obs)
        fam_frac = c.fam_frames / fo
        sub_frac = c.sub_frames / fo
        fam1_frac = c.fam1_frames / fo
        # latch a confirmed family (lineage memory). Two or more co-located partials are decisive quickly; "sits at a harmonic
        # offset above a lower line" needs longer, because under running speech/music a fixed line coincides with SOME moving
        # fundamental's H2..H4 position a good fraction of the time — a true partial coincides ~always (it co-moves).
        if not c.family_latched and ((c.fam_obs >= 4 and c.fam_frames >= 4 and fam_frac >= 0.5) or (c.fam_obs >= 10 and sub_frac >= 0.8)):
            c.family_latched = True
            c.family_level_db = c.run_max_db
        grown_out = (c.grow_acc >= 3.0 and cl_db > c.family_level_db + 5.0 and obs["fam_n"] < 2)   # grew >= 5 dB at a loop's rate and
                                                                                                     # shows no family NOW (partials co-move)
        jumped_out = (cl_db > c.family_level_db + 8.0 and obs["fam_n"] < 2 and not obs["sub"])
        if c.family_latched and (jumped_out or grown_out):
            # whatever occupies the band now is well above the note that owned the partials and has none itself: the
            # lineage (family, vibrato, onset shape) belongs to the previous occupant — a chord tone / sung partial that
            # seeded the loop (X21/S12-type) or a bass partial a band away (X16-type). Test the newcomer afresh.
            if jumped_out and not grown_out:
                prev = c.levels[-2] if len(c.levels) >= 2 else c.family_level_db
                self._rearm(c, ts, prev)
                self._observe_after_rearm(c, obs, cl_db, ts)
                fam_frac = sub_frac = fam1_frac = 0.0
            else:
                c.family_latched = False
                c.fam_frames = c.sub_frames = c.fam1_frames = 0
                c.fam_obs = 1
                fam_frac = sub_frac = fam1_frac = 0.0
                c.modulated_latched = False
                c.wobble_windows = 0
                c.family_level_db = cl_db
        distortion = (lvl >= cfg.harmonics_void_level_db and rose >= 12.0 and c.fast_peak >= 2) or (odd_only and lvl >= cfg.harmonics_void_level_db)
        if clip or distortion:
            t["family"] = 0.0                  # a clipping / limiting howl carries harmonics: the test is void there (loop brief §2.2)
        elif c.family_latched:
            t["family"] = -3.0
        elif c.fam_obs == 0:
            t["family"] = 0.0                  # partial positions off the analyser: unavailable
        else:
            neg = -3.0 * fam_frac - 2.0 * _clamp((sub_frac - 0.3) / 0.7, 0.0, 1.0) - 0.6 * fam1_frac
            informative = _clamp((c.prominence_db - 14.0) / 10.0, 0.0, 1.0)
            clean = max(0.0, 1.0 - 2.5 * fam_frac - 1.5 * sub_frac - 1.0 * fam1_frac)
            settle = _clamp(c.fam_obs / 4.0, 0.25, 1.0)
            very = 0.6 * _clamp((c.prominence_db - 24.0) / 12.0, 0.0, 1.0)     # at >= 24 dB even -25 dB partials would show: the impostor set shrinks further
            t["family"] = settle * (neg + (1.0 * informative + very) * clean)

        # ---- stable: centroid range over the last 8 observed frames, hop-aware ---------------------------
        cents = c.cents
        if len(cents) >= 2 and not obs["intrusion"]:
            jump = cents[-1] - cents[-2]
            if abs(jump) >= cfg.hop_min_bands and len(cents) >= 4:
                pre = cents[-4:-1]
                if max(pre) - min(pre) < cfg.stable_range_bands * 1.5:
                    c.hop_count += 1           # re-centre: the jump itself is not wobble; judged by what follows
                    del cents[:-1]
        if len(cents) >= 5:
            w = sorted(cents[-8:])
            # range with the single most extreme sample dropped: one intruded frame (a programme partial crossing the ±1
            # neighbourhood) is not wobble; vibrato / glide move most samples
            rng = min(w[-1] - w[1], w[-2] - w[0]) if len(w) >= 6 else (w[-1] - w[0])
            lo_r, hi_r = cfg.stable_range_bands, cfg.wobble_range_bands
            if rng < lo_r:
                t_st = 0.5 * _clamp((len(w) - 2) / 4.0, 0.25, 1.0)
                if len(w) >= 8:
                    c.stable_windows += 1
            elif rng > hi_r:
                t_st = -1.5
                if len(w) >= 8:
                    c.wobble_windows += 1
                    if c.wobble_windows >= 6 and c.wobble_windows > c.stable_windows // 4:
                        c.modulated_latched = True      # FM seen for >= 6 windows: the source has vibrato / glides
            else:
                xx = (rng - lo_r) / (hi_r - lo_r)
                t_st = 0.5 * (1.0 - xx) * 0.6 - 0.9 * xx
            if c.hop_count >= 3:
                t_st -= 1.0                    # hopping every few frames is a melody / edge vibrato, not a hand-held mic
        else:
            t_st = 0.0
        if c.modulated_latched and c.grow_acc >= 3.0 and cl_db >= c.run_max_db - 0.5 and c.stable_windows >= 8 \
                and c.stable_windows >= 2 * c.wobble_windows:
            c.modulated_latched = False        # the FM belonged to a previous occupant of the band; what grows here now is stable
            c.wobble_windows = 0
        if c.modulated_latched:
            t_st = min(t_st, -2.5)
        t["stable"] = t_st

        # ---- steady: level micro-variation (gap-normalised first differences + residual about the trend) -----
        lv = c.levels
        tsl = c.ts_list
        t_sd = 0.0
        if len(lv) >= 5:
            k0 = max(0, len(lv) - 8)
            d = []
            for i in range(k0, len(lv) - 1):
                gap = max(1.0, round((tsl[i + 1] - tsl[i]) / T))
                d.append((lv[i + 1] - lv[i]) / gap)
            d_r = [x for x in d if abs(x) < 2.5]           # onset / hop-sized steps are judged by 'shape' and 'stable', not here
            if len(d_r) >= 5:
                d_r = sorted(d_r, key=abs)[:-1]            # and drop the single largest remaining step (one intruded frame)
            if len(d_r) < 3:
                d_r = d
            m = sum(d_r) / len(d_r)
            rms = math.sqrt(sum((x - m) ** 2 for x in d_r) / len(d_r))
            # residual about the trend, measured after the last onset/hop-sized step in the window (the step itself is not
            # "unsteadiness"; what matters is how the line sits once it is there), worst sample dropped
            k1 = k0
            for i in range(k0, len(lv) - 1):
                if abs(d[i - k0]) >= 2.5:
                    k1 = i + 1
            res_sd = 0.0
            if len(lv) - k1 >= 4:
                sl_, _, _ = _ls_fit(tsl[k1:], lv[k1:])
                tm_ = sum(tsl[k1:]) / (len(lv) - k1)
                vm_ = sum(lv[k1:]) / (len(lv) - k1)
                rs_ = sorted(abs(v - (vm_ + sl_ * (t - tm_))) for t, v in zip(tsl[k1:], lv[k1:]))
                if len(rs_) >= 6:
                    rs_ = rs_[:-1]
                res_sd = math.sqrt(sum(r * r for r in rs_) / len(rs_))
            pk = c.peak_levels
            nz = sum(1 for i in range(k0, len(pk) - 1) if abs(pk[i + 1] - pk[i]) < 1e-9)
            # peak-hold / frozen display: this band AND a large share of the whole display sit at exactly repeated values
            # (a live band always jitters by >= 1 LSB; analyser brief §2, §7 S16)
            c.frozen = self.analyser_suspect and nz >= 0.6 * (len(pk) - 1 - k0) and lvl < cfg.loud_level_db
            if c.frozen:
                t_sd = 0.0
            else:
                r = max(rms / cfg.rough_rms_db, res_sd / 0.45)
                s = max(rms / cfg.steady_rms_db, res_sd / 0.30)
                if s < 1.0:
                    t_sd = 0.5 * _clamp((len(d) - 1) / 4.0, 0.25, 1.0)
                elif r > 1.0:
                    t_sd = -2.0 if c.prominence_db >= 16.0 else -0.8
                else:
                    t_sd = 0.25 * (1.0 - _clamp((s - 1.0) / 1.5, 0.0, 1.0)) - 1.0 * _clamp((r - 0.6) / 0.4, 0.0, 1.0)
        t["steady"] = t_sd

        # ---- shape: growth (fast / medium / slow), onset step, decay — all against the spectrum reference ----
        corr = c.corr
        gw = self._grow_weight[b]
        minrun = self._grow_min_run[b]
        n_h = len(corr)
        credit = 0.0                           # this frame's growth evidence: the strongest of the paths, not their sum
        # fast path: consecutive per-frame increments >= 1.2 dB of similar size (>= 24 dB/s; loop brief §1.3)
        if n_h >= 2:
            gap = max(1.0, round((tsl[-1] - tsl[-2]) / T))
            g = (corr[-1] - corr[-2]) / gap
            graw = (lv[-1] - lv[-2]) / gap
            if g >= 0.9 and graw >= 0.7:
                if c.grow_run >= 1 and n_h >= 3:
                    gp = (corr[-2] - corr[-3]) / max(1.0, round((tsl[-2] - tsl[-3]) / T))
                    consistent = abs(g - gp) <= 0.45 * max(g, gp) + 0.4
                    c.grow_run = c.grow_run + 1 if consistent else 1
                else:
                    c.grow_run = 1
                if c.grow_run >= minrun:
                    credit = max(credit, cfg.ev_growth_fast * gw)
            else:
                c.grow_run = 0
        # emerged from the floor: first seen within a few dB of the pre-existing level of its band and rising since — the
        # onset-from-the-floor signature (loop brief P9); a note arrives at its level, it does not climb out of the bed
        if c.floor_bonus <= 0.0 and c.seen <= 10 and c.pre_birth_db > -199.0 and c.birth_jump_db < 4.0 \
                and (c.grow_acc > 0.0 or c.grow_run >= 2) and cl_db >= c.first_level_db + 2.0:
            c.floor_bonus = 0.7 * gw
        # very fast path: above ~250 Hz the analyser settles within a frame, so an instrument onset is ONE partially
        # integrated frame then full level (analyser brief §0.1); a rise that continues for a THIRD frame at >= 2.5 dB/frame
        # (>= 50 dB/s), or a two-frame rise that only stops within ~16 dB of full scale (limiter / clip), is a loop with
        # e >~ 0.5 dB on a short path (loop brief §1.3: "single-frame or 2-frame evidence must be admissible").
        if self._tau_a[b] < 0.5 * T and n_h >= 1:
            if n_h >= 2:
                gap = max(1.0, round((tsl[-1] - tsl[-2]) / T))
                inc = (lv[-1] - lv[-2]) / gap
                incc = (corr[-1] - corr[-2]) / gap
            else:
                inc = incc = (lv[-1] - c.pre_birth_db) if c.pre_birth_db > -199.0 else 0.0
            if inc >= 2.5 and (incc >= 2.0 or n_h < 2):
                c.fast_rise += 1
                c.fast_peak = max(c.fast_peak, c.fast_rise)
                if c.fast_rise >= 3:
                    credit = max(credit, cfg.ev_growth_multi)
            else:
                if c.fast_rise == 2 and c.fast_acc <= 0.0 and lvl >= cfg.harmonics_void_level_db and inc < 2.5:
                    c.fast_acc = max(c.fast_acc, 1.0)     # two fast frames that stopped only near full scale: limiter-caught loop
                c.fast_rise = 0
        # medium path: 4..8-frame linear rise >= 6 dB/s. Fit the RAW level (the reference's frame-to-frame programme
        # jitter must not pollute the residuals) and subtract only the reference's fitted slope when it rises
        # (a master ramp / common-mode move cancels; analyser brief §5).
        slope8 = 0.0
        refs = c.refs
        if n_h >= 4:
            k0 = max(0, n_h - 8)
            raw_s, res_max, res_sd = _ls_fit(tsl[k0:], lv[k0:])
            ref_s = _ls_slope(tsl[k0:], refs[k0:])
            s = raw_s - max(0.0, ref_s)
            slope8 = s
            span = tsl[-1] - tsl[k0]
            rise = s * span
            if s >= 6.0 and rise >= 2.5 and res_sd <= max(0.35, 0.1 * rise) and res_max <= max(0.8, 0.25 * rise) \
                    and (n_h - k0) >= min(8, max(4, minrun + 2)) and not c.frozen:
                # sustained, not decelerating: both halves of the window rise (an attack / swell / analyser step response
                # front-loads the rise; a loop keeps going until something limits it — loop brief §1.3-1.4)
                mid = k0 + (n_h - k0) // 2
                s1 = _ls_slope(tsl[k0:mid + 1], lv[k0:mid + 1])
                s2 = _ls_slope(tsl[mid:], lv[mid:])
                if s2 >= max(0.45 * s1, 0.4 * raw_s) and (minrun <= 2 or s2 >= 0.55 * s1):
                    credit = max(credit, cfg.ev_growth_medium * gw)
        # slow path: 12..20-frame linear rise >= 1.5 dB/s (marginal loops: e of hundredths of a dB, loop brief §1.3), fitted
        # on a 3-frame running median of the level so a hi-hat / consonant landing in the band for one frame does not break it
        slope12 = 0.0
        if n_h >= 8:
            k0 = max(0, n_h - 20)
            seg = lv[k0:]
            tseg = tsl[k0:]
            if len(seg) >= 12:
                med3 = [median(seg[max(0, i - 1):i + 2]) for i in range(len(seg))]
                raw_s, res_max, res_sd = _ls_fit(tseg, med3)
            else:
                raw_s, res_max, res_sd = _ls_fit(tseg, seg)
            ref_s = _ls_slope(tseg, refs[k0:])
            s = raw_s - max(0.0, ref_s)
            slope12 = s
            span = tseg[-1] - tseg[0]
            rise = s * span
            if s >= cfg.slow_growth_min_db_per_s and rise >= 1.2 and res_sd <= 0.35 and res_max <= 1.0 and not c.frozen \
                    and len(seg) >= 12:
                mid = len(seg) // 2
                s1 = _ls_slope(tseg[:mid + 1], seg[:mid + 1])
                s2 = _ls_slope(tseg[mid:], seg[mid:])
                if s1 >= 0.4 * raw_s and s2 >= 0.4 * raw_s:      # rising throughout, not "rose then sat"
                    credit = max(credit, cfg.ev_growth_slow * gw)
            # (decay uses the plain 12-frame fit)
            k0 = max(0, n_h - 12)
            raw_s, _, _ = _ls_fit(tsl[k0:], lv[k0:])
            ref_s = _ls_slope(tsl[k0:], refs[k0:])
            # decay: falling on its own while the spectrum is not (plucked / struck / released / a killed loop)
            fall = raw_s - min(0.0, ref_s)
            if fall <= -2.0 and cl_db <= c.run_max_db - 2.0:
                c.decay_acc += 0.35
            elif fall > -1.0 and cl_db >= c.run_max_db - 1.0:
                c.decay_acc = max(0.0, c.decay_acc - 0.1)      # recovered (a stalled loop regrew)
        c.slope_db_per_s = slope8 if n_h < 8 else slope12
        c.growing_now = credit > 0.0               # passed a growth test this frame (used for synchrony)
        c.rising_now = n_h >= 4 and c.seen >= 4 and (corr[-1] - corr[-2]) >= 0.5 and (corr[-1] - corr[-4]) >= 2.0 \
            and (lv[-1] - lv[-4]) >= 2.0
        if credit > 0.0:
            if (n_growing_other >= 2 and (ts - self._ref_jump_ts) > 0.5) or (ts - self._sync_ts) <= 0.6:
                # three or more lines rising together (now or within the last 0.6 s) is a programme event — pad swell, song
                # start, crescendo of a chord: loops start alone (analyser brief §5). No growth evidence for anyone meanwhile.
                c.sync_frames += 1
            else:
                c.grow_acc += credit
        # onset step: the line appeared at (near) its plateau within the band's analyser rise time, then sat flat at a
        # moderate level. A loop only stops growing at a limiter / clip / compressor after rising e/τ dB/s (loop brief
        # §1.3-1.4): arriving inside one rise time at a moderate level needs several dB of excess AND a compressor —
        # uncommon in watch, ~impossible in ring-out where the crossing step leaves <= 1 dB of excess.
        k_on = self._onset_frames[b]
        if not c.onset_done and c.seen >= k_on + 4 and len(lv) >= k_on + 4:
            c.onset_done = True
            if c.pre_birth_db > -199.0:
                k_a = min(k_on, len(lv) - 1)
                arrived = lv[k_a] - c.pre_birth_db                             # rise completed within the rise time
                if c.pre_birth_peak_db > -199.0 and len(c.peak_levels) > k_a:
                    arrived = max(arrived, c.peak_levels[k_a] - c.pre_birth_peak_db)   # (a legato note next door shared the cluster)
                after = lv[k_on:k_on + 4]
                flat_after = (max(after) - min(after)) < 1.5 and (lv[-1] - lv[k_on]) < 2.0
                if arrived >= 6.0 and flat_after and c.grow_acc <= 0.0 and c.fast_acc <= 0.0:
                    lvl_factor = _clamp((cfg.loud_level_db - lvl) / (cfg.loud_level_db - cfg.step_moderate_level_db), 0.0, 1.0)
                    # (also right after the server's own +1 dB step: that leaves <= 1 dB of excess, so a loop born there still
                    # climbs from the floor at <= ~200 dB/s — it does not appear at its plateau inside one frame)
                    base = -2.4 if cfg.mode == "ringout" else -1.9
                    c.step_pen = base * lvl_factor
        if c.step_pen < 0.0 and (c.grow_acc + c.fast_acc) >= 1.5 and cl_db >= lv[min(k_on, len(lv) - 1)] + 4.0:
            c.step_pen = 0.0                   # it grew like a loop after all (>= 4 dB past its "plateau"): the onset verdict was wrong
        grow = c.grow_acc + c.slow_acc + c.fast_acc + c.floor_bonus
        t["shape"] = _clamp(grow, 0.0, 4.5) + c.step_pen - _clamp(c.decay_acc, 0.0, 4.0)
        c.growth_score = _clamp(grow / 3.0, 0.0, 1.0)

        # ---- level: clip flag / near full scale (loop brief P8) ---------------------------------------------
        if lvl >= cfg.clip_level_db:
            t["level"] = 3.5
        elif lvl >= cfg.loud_level_db:
            t["level"] = 1.5 + 2.0 * (lvl - cfg.loud_level_db) / (cfg.clip_level_db - cfg.loud_level_db)
        elif lvl >= cfg.loud_level_db - 10.0:
            t["level"] = 1.5 * (lvl - (cfg.loud_level_db - 10.0)) / 10.0
        else:
            t["level"] = 0.0
        if lvl < cfg.clip_level_db and n_other >= 1 and cl_db < loudest_other + 3.0:
            t["level"] *= 0.25                 # in a mix running this hot, "near full scale" says little unless it is THE loudest line
        if n_other >= 2 and cl_db >= loudest_other + 2.0:
            c.dominant_frames += 1
        else:
            c.dominant_frames = 0
        if c.dominant_frames >= 8:
            t["level"] += 0.9                  # the loudest line in a busy spectrum, family-less or not, is where a limited howl sits (loop brief §1.4, §2.2)

        # ---- persist: survival (saturating) + programme-boundary survival --------------------------------------
        dur = 0.0 if c.frozen else 0.5 * math.log1p(c.frames / 10.0)
        if boundary_w > 0.0 and len(lv) >= 5 and max(lv[-5:]) - min(lv[-5:]) < 1.0 and c.seen >= 5:
            c.boundary_acc += boundary_w
        t["persist"] = _clamp(dur, 0.0, 0.6) + _clamp(c.boundary_acc, 0.0, 1.2)

        # ---- probe (ring-out): accumulated in _probe_update ----------------------------------------------------
        t["probe"] = _clamp(c.probe_acc, -4.0, 5.0)

        self._finish(c)

    def _finish(self, c: Candidate) -> None:
        cfg = self.cfg
        c.llr = _clamp(sum(c.terms.values()), cfg.llr_floor, cfg.llr_ceil)
        span = max(1.0, cfg.emit_llr - cfg.dismiss_llr)
        if c.llr < cfg.emit_llr:
            c.confidence = _clamp(cfg.confidence_threshold * (1.0 + 0.6 * (c.llr - cfg.emit_llr) / span), 0.0, cfg.confidence_threshold - 1e-6)
        else:
            c.confidence = _clamp(cfg.confidence_threshold + (1.0 - cfg.confidence_threshold) * (c.llr - cfg.emit_llr) / max(1.0, cfg.llr_ceil - cfg.emit_llr),
                                  cfg.confidence_threshold, 1.0)
        c.state = "musical" if c.llr <= cfg.dismiss_llr else "feedback" if c.llr >= cfg.emit_llr else "watching"

    def _probe_update(self, c: Candidate, ts: float, n_other: int = 0) -> None:
        """Ring-out active probe (loop brief §1.5, P7): compare the line's median level over ``probe_window_frames`` after a
        noted master step (past ``probe_settle_frames`` + 2·τ_a) with the snapshot taken at the step. Programme at the
        pre-fader tap moves 0 dB (electrical) to +Δ (acoustic spill); a loop within ~4 dB of threshold over-responds
        (>= +2 dB per dB); a driven resonance / hum / spill moves 1 dB per dB."""
        cfg = self.cfg
        if not self._steps:
            return
        W = cfg.probe_window_frames
        S = cfg.probe_settle_frames
        T = cfg.frame_period_s
        changed = False
        for (t_step, delta) in self._steps:
            key = round(t_step, 3)
            if key in c.probe_done:
                continue
            extra = int(round(2.0 * self._tau_a[c.band] / T))
            post = [(t, v, r) for t, v, r in zip(c.ts_list, c.levels, c.refs) if t > t_step + S * T]
            if len(post) < W + extra:
                continue
            c.probe_done.add(key)
            changed = True
            snap = c.probe_pre.pop(key, None)
            if snap is None:
                # born just after the step and rising since: an onset time-locked to the server's own gain change
                # (loop brief §4.3 "certain" tier). Only the step immediately before birth can claim this, once, and only
                # when nothing else is playing (with programme present, notes start inside every dwell).
                if abs((c.first_ts - t_step) - (c.born_after_step or -1.0)) < 1e-6 and 0.1 <= (c.born_after_step or 0.0) <= 1.2 \
                        and (c.grow_acc + c.fast_acc > 0 or c.levels[-1] - c.first_level_db >= 3.0) and not c.family_latched \
                        and n_other == 0:
                    c.probe_acc += 1.2
                continue
            pre_med, pre_ref, pre_rng, spread = snap
            c.probe_evals += 1
            post = post[extra:extra + W]
            post_v = [v for _, v, _ in post]
            dv = median(post_v) - pre_med
            dr = median([r for _, _, r in post]) - pre_ref
            over = dv - max(dr, 0.0) if delta > 0 else -(dv - min(dr, 0.0))
            if pre_rng > 5.0 or (max(post_v) - min(post_v)) > 5.0 + 2.0 * max(0.0, over):
                continue                                   # the line itself jumped inside a window (a note change): inconclusive
            noisy = spread > 1.0 or pre_rng > 2.5          # noise-excited regeneration fluctuates; weigh accordingly
            lvl_now = c.level_db
            if over >= cfg.probe_over_db:
                c.probe_acc += 1.2 if noisy else 2.0       # super-linear: a loop within ~4 dB of threshold
            elif over >= 1.0:
                c.probe_acc += 0.4 if noisy else 0.8
            elif delta > 0 and (abs(dv - delta) <= 0.5 or (dr >= 0.5 and abs(dv - dr) <= 0.4)):
                c.probe_acc -= 0.5 if noisy else 0.9       # 1 dB per dB: acoustic spill, a driven resonance, programme riding the gain
            elif abs(dv) <= 0.3 and delta > 0:
                # did not move at all: electrical programme at the pre-fader tap OR a limited/compressed howl whose plateau is
                # pinned downstream (loop brief §1.4) — weakly against, and not at all for a loud line or one that grew like a loop
                if lvl_now < -20.0 and (c.grow_acc + c.fast_acc) < 2.0:
                    c.probe_acc -= 0.3
            elif delta < 0 and over >= 1.0:
                c.probe_acc += 0.8                         # a back-off that made it fall more than the programme: regenerative
        if changed:
            c.terms["probe"] = _clamp(c.probe_acc, -4.0, 5.0)
            self._finish(c)

    # -- API ---------------------------------------------------------------------------------------------
    def note_gain_step(self, delta_db: float, ts: float) -> None:
        """Ring-out: the server has just stepped the bus master by ``delta_db`` at ``ts`` (a free active probe, loop brief
        §1.5). Every tracked line's pre-step level is snapshotted now; the response is read ``probe_settle_frames`` +
        ``probe_window_frames`` frames later in :meth:`feed`."""
        key = round(float(ts), 3)
        W = self.cfg.probe_window_frames
        for c in self._cands:
            if len(c.levels) >= 3:
                lv = c.levels[-W:]
                c.probe_pre[key] = (median(lv), median(c.refs[-W:]), max(lv) - min(lv),
                                    max((abs(a - b) for a, b in zip(lv, lv[1:])), default=0.0))
        self._steps.append((float(ts), float(delta_db)))
        if len(self._steps) > 16:
            old = round(self._steps[0][0], 3)
            del self._steps[0]
            for c in self._cands:
                c.probe_pre.pop(old, None)

    def note_cut(self, freq_hz: float, depth_db: float, ts: float) -> None:
        """A GEQ cut of ``depth_db`` at ``freq_hz`` was written at ``ts``: candidates within 1/3 octave restart their
        growth bookkeeping (the expected response is a drop; only renewed growth or an undiminished level re-emits)."""
        self._cuts.append((float(ts), float(freq_hz), float(depth_db)))
        if len(self._cuts) > 32:
            del self._cuts[0]
        for c in self._cands:
            if c.freq_hz > 0 and freq_hz > 0 and abs(math.log(c.freq_hz / freq_hz)) <= math.log(2.0) / 3.0:
                c.grow_acc = min(c.grow_acc, 1.0)
                c.slow_acc = 0.0
                c.grow_run = 0

    def feed(self, values_db: Sequence[float], ts: float) -> list[Detection]:
        """Process one RTA frame (``len(values_db) == len(band_hz)``, dB) taken at time ``ts`` (s).

        Returns the detections emitted on this frame (possibly empty). Raises ``ValueError`` on a frame of
        the wrong length.
        """
        cfg = self.cfg
        vals = [float(v) for v in values_db]
        n = self._n
        if len(vals) != n:
            raise ValueError(f"expected {n} RTA values, got {len(vals)}")
        prom = self.prominences(vals)
        lo, hi = cfg.ref_band_lo, min(n - 1, cfg.ref_band_hi)
        ref_raw = median(vals[lo:hi + 1]) if hi > lo else median(vals)
        self._ref_raw.append(ref_raw)
        if len(self._ref_raw) > 64:
            del self._ref_raw[0]
        ref = median(self._ref_raw[-3:])          # 3-frame median: keeps steps and ramps, drops single-frame programme jitter
        self._ref_hist.append(ref)
        if len(self._ref_hist) > 64:
            del self._ref_hist[0]

        qualifying = [i for i in range(n) if prom[i] >= cfg.track_prominence_db and vals[i] > -127.0]
        peaks = self._peaks(qualifying, vals)
        if self._prev_vals is not None:
            live = [i for i in range(n) if vals[i] > -120.0]
            if live:
                frac = sum(1 for i in live if abs(vals[i] - self._prev_vals[i]) < 1e-9) / len(live)
                self._frozen_frac += 0.15 * (frac - self._frozen_frac)
                self.analyser_suspect = self._frozen_frac >= 0.35   # peak-hold ON or a stalled meter stream: report, and trust no plateau

        # associate peaks with existing candidates (strongest evidence first), coast the unmatched, create the new
        used: set[int] = set()
        survivors: list[Candidate] = []
        births = 0
        deaths = 0
        obs_map: dict[int, dict[str, Any]] = {}
        lim = cfg.band_tolerance + 0.55
        for c in sorted(self._cands, key=lambda c: (-c.est_frames, -c.llr)):
            best: int | None = None
            bestd = 1e9
            for p in peaks:
                if p in used:
                    continue
                d = abs(p - c.centroid)
                if d > lim or d >= bestd:
                    continue
                if d >= 0.7 and c.levels:
                    lvl_p, _ = self._cluster(vals, p)
                    if lvl_p - c.levels[-1] >= 6.0:
                        continue      # a much louder line a band away is a NEW line, not a hop of this one (a hop keeps its level)
                best, bestd = p, d
            if best is None:
                c.coast += 1
                c.frames += 1
                if c.coast > cfg.coast_frames:
                    bb = min(n - 1, max(0, int(round(c.centroid))))
                    now, _ = self._cluster(vals, bb)
                    if c.est_frames >= 8 and now <= c.run_max_db - 6.0:
                        deaths += 1                       # an established line really went away (not a threshold flicker)
                    continue
                survivors.append(c)
                continue
            used.add(best)
            obs_map[id(c)] = self._observe(c, best, vals, prom, ref, ts)
            survivors.append(c)
        for p in peaks:
            if p in used:
                continue
            c = self._new_candidate(p, ts, vals)
            obs_map[id(c)] = self._observe(c, p, vals, prom, ref, ts)
            survivors.append(c)
        for c in survivors:
            if c.est_frames == 4 and id(c) in obs_map and c.birth_frame > 0 and c.birth_jump_db >= 6.0:
                births += 1          # a genuinely new line (>= 6 dB onset, now established) has started since arm
        ref_jump = len(self._ref_hist) >= 2 and abs(self._ref_hist[-1] - self._ref_hist[-2]) >= 2.0
        n_ev = births + deaths
        boundary_w = 0.5 if (n_ev >= 3 or ref_jump) else 0.3 if n_ev == 2 else 0.15 if n_ev == 1 else 0.0
        survivors.sort(key=lambda c: c.centroid)
        merged: list[Candidate] = []
        for c in survivors:
            if merged and c.band == merged[-1].band and c.coast == 0 and merged[-1].coast == 0:
                keep = c if (c.est_frames, c.llr) > (merged[-1].est_frames, merged[-1].llr) else merged[-1]
                merged[-1] = keep
                continue
            merged.append(c)
        self._cands = merged

        out: list[Detection] = []
        loud_lines = [(c.centroid, c.level_db) for c in self._cands if c.level_db >= cfg.loud_level_db - 5.0 and c.est_frames >= 2]
        est = [c for c in self._cands if c.est_frames >= 6 and c.coast <= 1]
        growing = [c.centroid for c in self._cands if c.growing_now or c.rising_now]
        if len(self._ref_hist) >= 2 and abs(self._ref_hist[-1] - self._ref_hist[-2]) >= 3.0:
            self._ref_jump_ts = ts                        # a common-mode step: what rises now is the analyser catching up, not a swell
        if len(growing) >= 3 and (max(growing) - min(growing)) > 3.0 and (ts - self._ref_jump_ts) > 0.5:
            self._sync_ts = ts
        for c in self._cands:
            obs = obs_map.get(id(c))
            if obs is None or obs.get("masked"):
                continue                                  # coasting / masked by a transient: no new evidence this frame
            w = 0.0 if c.est_frames == 4 else boundary_w   # its own establishment is not a boundary it survived
            others = [o for o in est if o is not c and abs(o.centroid - c.centroid) > 1.5]
            loudest_other = max((o.levels[-1] for o in others if o.levels), default=-200.0)
            growing_other = sum(1 for o in self._cands if o is not c and (o.growing_now or o.rising_now) and abs(o.centroid - c.centroid) > 1.5)
            self._update_evidence(c, obs, ts, w, len(others), loudest_other, growing_other)
            if self._steps:
                self._probe_update(c, ts, len(others))
            if c.state != "feedback":
                continue
            if max(c.prominence_db, c.cl_prom_db) < cfg.prominence_db:
                continue
            if cfg.mode == "ringout" and self._steps and c.probe_evals == 0 and (c.grow_acc + c.fast_acc + c.floor_bonus) < 1.0 \
                    and c.level_db < cfg.loud_level_db - 5.0 and c.terms.get("family", 0.0) < 0.9:
                c.state = "watching"                      # uncertain and the server owns the gain: let the next step's probe decide
                continue
            if obs["cl_db"] < cfg.emit_min_level_db:
                continue
            # a distortion product of a loud line (clipping howl): cut the fundamental, its harmonics die with it
            is_harm = False
            if c.grow_acc + c.slow_acc < 1.0:              # a line growing at its own rate is its own loop, not a distortion product
                for pos0, lv0 in loud_lines:
                    if abs(pos0 - c.centroid) < 1.0 or lv0 < c.level_db + 6.0:
                        continue                          # distortion partials sit >= 10 dB under their fundamental (loop brief §2.2)
                    if any(abs((c.centroid - pos0) - off) <= 0.8 for off in _HARM_OFFSETS_ALL):
                        is_harm = True
                        break
            if is_harm:
                continue
            if self._in_cooldown(c.band, ts):
                continue
            if c.emitted and obs["cl_db"] < c.last_emit_level - 2.0 and c.grow_run < 2 and c.grow_acc < 1.0:
                continue                                  # the previous cut is working (line diminished, not regrowing)
            reasons = tuple(f"{k}={v:+.1f}" for k, v in sorted(c.terms.items(), key=lambda kv: -abs(kv[1])) if abs(v) >= 0.25)
            det = Detection(
                ts=ts, band=c.band, freq_hz=c.freq_hz, level_db=c.level_db, prominence_db=max(c.prominence_db, c.cl_prom_db),
                slope_db_per_s=c.slope_db_per_s, frames=c.frames, confidence=c.confidence, llr=c.llr, reasons=reasons,
            )
            out.append(det)
            c.emitted += 1
            c.override = (c.grow_acc + c.slow_acc + c.fast_acc) <= 0.0
            c.last_emit_ts = ts
            c.last_emit_level = obs["cl_db"]
            self._cooldown[c.band] = ts + cfg.cooldown_s
            log.debug("feedback detected: band %d (%.0f Hz) %.1f dB llr %.2f [%s]", c.band, c.freq_hz, c.level_db, c.llr, " ".join(reasons))
        if self._cooldown and self.frames_seen % 100 == 0:
            self._cooldown = {b: t for b, t in self._cooldown.items() if ts < t}
        if self._prev_vals is not None:
            self._hist_vals.append(self._prev_vals)
            if len(self._hist_vals) > 2:
                del self._hist_vals[0]
        self._prev_vals = vals
        self.frames_seen += 1
        self.last_ts = ts
        return out

    @property
    def candidates(self) -> list[Candidate]:
        """Current tracked lines (live objects; treat as read-only), lowest band first."""
        return list(self._cands)

    def reset(self) -> None:
        self._cands = []
        self._cooldown = {}
        self.frames_seen = 0
        self.last_ts = None
        self._prev_vals = None
        self._hist_vals = []
        self._ref_hist = []
        self._ref_raw = []
        self._steps = []
        self._cuts = []
        self._frozen_frac = 0.0
        self.analyser_suspect = False
        self._sync_ts = -1e9
        self._ref_jump_ts = -1e9


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
