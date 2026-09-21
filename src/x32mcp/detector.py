"""CFS² feedback discriminator (track → group → classify) and notch planner — pure Python, no I/O.

Two synchronous, side-effect-free pieces used by ``cfs.py``:

* :class:`FeedbackDetector` consumes one RTA frame at a time (100 dB values from ``/meters/15``,
  band ``i`` centred at ``band_hz[i]`` = ``10000 * 2 ** ((i - 90) / 10)`` Hz, docs/research/meters.md §4.2)
  and returns :class:`Detection` objects for spectral lines that behave like regenerative feedback.
* :class:`NotchController` turns detections into GEQ band cuts (:class:`Notch`); unchanged from the
  original design (DESIGN.md §12), see its docstring.

Units and indices at the public boundary
----------------------------------------
* Levels are dB (RTA dB re. full scale, -128 = "no signal", 0.0 = the desk's clip flag), times are
  seconds, slopes are dB/s, "bands" are 0-based RTA band indices; a *centroid* is a fractional band
  index (sub-band frequency estimate), ``freq_hz`` of a detection is interpolated from it.
* **GEQ band numbers are 1-based** (``Notch.band`` 1..31).

How the discriminator decides (replaces DESIGN §12's weighted sum; full rationale in the design report)
------------------------------------------------------------------------------------------------------
Per frame, O(bands + peaks·k):

1. **Peaks.** ``prominence[i] = level[i] - median(level[i±1..±neighbour_bins])``. A band that is a local
   maximum with prominence >= ``peak_floor_db`` is a *peak*; its sub-band *centroid* is the vertex of the
   parabola through the dB levels of (i-1, i, i+1) (a tone on a band edge reads -3/-3 dB in two bands and
   gets centroid i+0.5 from either side, so a split line is ONE object), its *cluster power* is the power
   sum of the three bands (invariant to centre offset and vibrato), its *narrowness* is
   ``level[i] - max(level[i±2])`` (a single sinusoid clears the analyser's ±2 skirt by > 20 dB; formant
   humps, cymbal wash and bed humps do not).
2. **Harmonic grouping (single frame).** For every peak the partners at ×2, ×3, ×4, ×5 (+10, +15.85, +20,
   +23.2 bands) are looked up among the *peaks* (presence-as-peak within ``harmonic_tol_bands`` of the
   predicted centroid and within ``partner_window_db`` of the candidate's level). A peak is *in a family*
   when it has >= 2 such partners, or when it sits at ×2/×3/×4 above a peak that has at least one OTHER
   partner (it is somebody's overtone). Two lone lines an exact octave apart (two rings of one rig) are
   therefore NOT a family: neither has a second partner.
3. **Tracks.** Peaks with prominence >= ``track_floor_db`` start a :class:`Candidate` track (below the
   emission threshold, so evidence accumulates before a line becomes "visible"); tracks follow their peak by
   centroid (±1 band per frame), survive a one-frame gap, and keep short histories of centroid, level,
   cluster power, prominence, narrowness, the spectrum reference (median of bands 25..85) and the family
   flag. At birth the band's recent past is back-filled from a frame ring buffer and the **onset is
   classified**: ``at_arm`` (present when the detector started: no onset information), ``adult`` (arrived at
   full level within <= 2 frames and stopped: an instrument note, or an LF onset smeared by the analyser's
   own rise time), ``ramp`` (>= 3 frames of roughly constant dB increments from the floor: exponential
   regeneration) or ``slow`` (emerged gradually).
4. **Behaviour over time** (windowed, so a track can change its nature, e.g. a chord tone's band taken over
   by a ring): *family* (family flag on >= ``family_ratio`` of the last ``family_window_frames``), *moving*
   (centroid outliers beyond ``centroid_tol_bands``, or a glide), *co-moving* with the spectrum reference
   (level regresses on the reference with slope ≈ 1: a fader/common-mode move or a note breathing with the
   mix), *decaying* (raw slope <= -``decay_db_per_s``: plucked/struck notes, killed rings, display release),
   *steady* (cluster-power range over the window), *ramp* (linear-in-dB rise: frames, total rise, linearity,
   not explained by the reference), *probe* (ring_out only: response to the server's own master step minus
   the step; >= ``probe_over_db`` twice, or once by a large margin, is loop-gain dependence; ≈ the step is a
   linear responder = programme/hum/HVAC).
5. **Decision** = explicit predicates, no weighted sum. A track must be in the session's frequency window
   (mode-dependent low edge, LF opt-in), above ``min_level_db``, prominent (>= ``prominence_db``), narrow,
   stationary and not in a family. Then
     TIER A (emit now): clip flag; or level >= ``loud_level_db`` held ``confirm_frames``; or a *strong ramp*
       (>= ``ramp_strong_frames`` linear increments and still rising, or >= ``ramp_strong_rise_db`` total);
       or (onset at_arm/slow, prominence >= ``strong_prominence_db``, steady, ``confirm_frames`` old);
       or probe-confirmed (ring_out).
     TIER B (emit after a clean window of ``watch_confirm_frames`` in watch, ``confirm_frames`` in ring_out):
       onset not ``adult``, not decaying, not co-moving, steady or slowly rising.
     An ``adult``-onset line below ``loud_level_db`` is never cut in watch mode (whistle, organ, flute, sine
     lead are indistinguishable from a compressor-caught fast ring by passive physics; the human owns the
     fader there) — it is published as a candidate. In ring_out the probe resolves it.
   Emitted tracks re-emit once per ``cooldown_s`` while they still qualify and are not decaying (so a notch
   can be deepened, and a killed ring's slowly-releasing display is not re-cut).

Optional API used by ``cfs`` when available: ``mode`` ('watch'|'ringout') and ``lf_feedback_possible`` in the
config or as constructor keywords, :meth:`FeedbackDetector.note_gain_step` (the server's own master step =
active probe) and :meth:`FeedbackDetector.note_cut`.

Only ``logging`` is used for diagnostics (stdout is the MCP transport).
"""

from __future__ import annotations

import logging
import math
from collections import deque
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

# Harmonic partner offsets in 1/10-octave bands: Hk sits 10*log2(k) bands above H1 (analyser brief §4.1).
_LOG_RATIO = {k: 10.0 * math.log2(k) for k in range(1, 9)}   # +10, +15.85, +20, +23.2, +25.85, +28.1, +30
_MODES = ("watch", "ringout")


def _as_bool(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


@dataclass(frozen=True)
class DetectorConfig:
    """Detector + notch thresholds; one field per ``device.yaml`` ``detector:`` key (unknown keys ignored,
    missing keys defaulted). The yaml ``weights`` mapping and the growth/override keys of the original
    weighted-sum heuristic are still accepted so an old ``device.yaml`` loads; they no longer drive the
    decision (see module docstring) and are kept only for the dashboard's confidence figure."""

    # -- peak picking ----------------------------------------------------------------------------
    prominence_db: float = 12.0          # dB over the ±neighbour_bins median a line needs to be EMITTED
    neighbour_bins: int = 3              # bands each side used for the median
    peak_floor_db: float = 6.0           # prominence for "present as a peak" (harmonic partners)
    track_floor_db: float = 8.0          # prominence at which a peak starts being tracked (< prominence_db:
                                         # evidence accumulates before the line is visible)
    narrow_db: float = 9.0               # level - max(level at ±2 bands): single line vs hump
    min_level_db: float = -60.0          # absolute plausibility floor (RTA dB) for STEADY lines (no growth evidence);
                                         # cfs may raise it at arm. A line that is visibly regenerating is judged
                                         # from level_floor_db: it will not stay quiet (loop brief §1.3)
    level_floor_db: float = -72.0        # nothing below this is tracked or emitted (analyser statistics region)
    # -- frequency window (loop brief §3.4) --------------------------------------------------------
    f_low_watch_hz: float = 160.0        # programme present, vocal-mic loop gain is lowest at LF
    f_low_ringout_hz: float = 100.0      # no programme by contract; a missed LF mode is what ring-out is for
    f_low_lf_hz: float = 40.0            # with lf_feedback_possible (kick/tom/acoustic pickup near subs)
    f_high_hz: float = 12500.0           # SM58 dies > 10 k; condensers ring to 12-13 k
    lf_feedback_possible: bool = False   # operator/patch opt-in for LF loops
    mode: str = "watch"                  # 'watch' (human owns the gain) | 'ringout' (server owns it)
    # -- grouping ----------------------------------------------------------------------------------
    harmonic_tol_bands: float = 0.3      # |partner centroid - predicted| (a semitone is 0.83 band; exact partials of one
                                         # source agree to ~0.1-0.2 band after interpolation)
    partner_window_db: float = 10.0      # a partner ABOVE the candidate counts if within this of its level (howl
                                         # distortion products sit >= 10-30 dB down; musical partials 0..-10)
    sub_window_db: float = 15.0          # a would-be fundamental below the candidate may be this much quieter
    family_ratio: float = 0.4            # family on >= this fraction of the window => musical
    family_window_frames: int = 20
    # -- behaviour ---------------------------------------------------------------------------------
    persistence_frames: int = 3          # minimum tracked frames before ANY emission
    confirm_frames: int = 5              # K1: stationarity / loud / strong-prominence confirmation (250 ms)
    watch_confirm_frames: int = 15       # K2: clean window for TIER B in watch mode (750 ms)
    centroid_tol_bands: float = 0.35     # |centroid - window median| beyond this is an outlier
    stationary_outlier_frac: float = 0.25
    glide_bands: float = 0.75            # window median drifted this far from the birth centroid => moved
    strong_prominence_db: float = 18.0   # TIER A for at_arm/slow lines (with steadiness)
    loud_level_db: float = -12.0         # a narrow stationary line this close to full scale is cut regardless
    clip_level_db: float = -0.5          # RTA 0.0 = "clipping occurred" (meters.md §4.2)
    ramp_min_step_db: float = 0.4        # per-frame increment that counts toward a ramp
    ramp_strong_frames: int = 6          # >= this many linear increments and still rising => TIER A
    ramp_strong_rise_db: float = 24.0    # or this much total linear rise from the floor => TIER A
    ramp_moderate_db_per_s: float = 45.0 # a still-rising linear ramp this slow qualifies after 4 increments
    ramp_linearity: float = 0.5          # second-half mean increment / first-half >= this (analyser-smeared
                                         # onsets decelerate; regeneration is linear in dB)
    decay_db_per_s: float = 3.0          # raw slope <= -this over the decay window => decaying (programme,
                                         # or a killed ring's display release) — never re-cut
    decay_window_frames: int = 12
    plateau_range_db: float = 2.0        # cluster-power range allowed over the TIER B window ("steady")
    ref_lo_band: int = 25                # spectrum reference = median(level[ref_lo..ref_hi])
    ref_hi_band: int = 85
    comove_ref_range_db: float = 1.5     # reference must move this much before co-movement is judged
    comove_window_frames: int = 12
    probe_over_db: float = 2.0           # response - step >= this => loop-gain dependent (ring_out probe)
    probe_settle_s: float = 0.2          # ignore this long after the step (OSC latency + analyser rise)
    probe_window_s: float = 0.45         # median windows each side of the step
    arm_frames: int = 4                  # tracks born within this many frames of start are "at_arm"
    assoc_tol_bands: float = 0.6         # frame-to-frame centroid match radius (a new strong line >= 1 band away
                                         # from a note must start its OWN track, not inherit the note's history)
    max_gap_frames: int = 3              # a track survives this many consecutive frames without its peak (masking
                                         # transients: a crash or snare hides a line for 100-150 ms)
    history_frames: int = 48             # per-track history cap
    backfill_frames: int = 16            # frame ring buffer for pre-birth back-fill (0.8 s)
    max_tracks: int = 48
    band_tolerance: int = 1              # cooldown radius (RTA bands) shared with cfs/NotchController users
    cooldown_s: float = 1.0              # s between emissions for the same line
    # -- legacy weighted-sum keys (accepted, informational only) -------------------------------------
    growth_min_db_per_s: float = 6.0     # minimum LS slope for ramp evidence
    growth_ref_db_per_s: float = 20.0    # slope that maps to growth_score 1.0 (dashboard only)
    growth_max_db_per_s: float = 60.0    # UNUSED (the onset guard was physically wrong: loop brief §1.3)
    monotonic_tolerance_db: float = 1.0  # UNUSED
    w_prominence: float = 0.3            # dashboard confidence weights only
    w_persistence: float = 0.2
    w_growth: float = 0.5
    confidence_threshold: float = 0.7    # emitted detections report confidence >= this
    growth_window_frames: int = 60       # UNUSED
    override_prominence_db: float = 25.0     # UNUSED (subsumed by strong_prominence_db / TIER A)
    override_persistence_frames: int = 6     # UNUSED
    # -- notch controller / VERIFY (cfs.py) ----------------------------------------------------------
    notch_step_db: float = -3.0
    notch_max_db: float = -9.0
    notch_budget_default: int = 6
    merge_adjacent_bands: int = 1
    decay_verify_db: float = 6.0
    decay_verify_s: float = 1.5
    decay_verify_frames: int = 2
    frame_period_s: float = 0.05

    def __post_init__(self) -> None:
        def need(cond: bool, msg: str) -> None:
            if not cond:
                raise ValueError(f"DetectorConfig: {msg}")

        need(self.prominence_db > 0, "prominence_db must be > 0")
        need(0 < self.peak_floor_db <= self.track_floor_db <= self.prominence_db,
             "need 0 < peak_floor_db <= track_floor_db <= prominence_db")
        need(self.neighbour_bins >= 2, "neighbour_bins must be >= 2")
        need(self.persistence_frames >= 1, "persistence_frames must be >= 1")
        need(self.confirm_frames >= 2, "confirm_frames must be >= 2")
        need(self.watch_confirm_frames >= self.confirm_frames, "watch_confirm_frames must be >= confirm_frames")
        need(self.growth_ref_db_per_s > 0, "growth_ref_db_per_s must be > 0")
        need(self.growth_max_db_per_s > self.growth_min_db_per_s, "growth_max_db_per_s must exceed growth_min_db_per_s")
        need(self.monotonic_tolerance_db >= 0, "monotonic_tolerance_db must be >= 0")
        need(min(self.w_prominence, self.w_persistence, self.w_growth) >= 0, "weights must be >= 0")
        need(self.override_prominence_db >= 0, "override_prominence_db must be >= 0")
        need(self.override_persistence_frames >= 1, "override_persistence_frames must be >= 1")
        need(self.band_tolerance >= 0, "band_tolerance must be >= 0")
        need(self.cooldown_s >= 0, "cooldown_s must be >= 0")
        need(self.notch_step_db < 0, "notch_step_db must be negative (cuts only)")
        need(self.notch_max_db <= self.notch_step_db, "notch_max_db must be <= notch_step_db")
        need(self.notch_budget_default >= 0, "notch_budget_default must be >= 0")
        need(self.decay_verify_frames >= 1, "decay_verify_frames must be >= 1")
        need(self.merge_adjacent_bands >= 0, "merge_adjacent_bands must be >= 0")
        need(self.growth_window_frames >= max(2, self.persistence_frames), "growth_window_frames too small")
        need(self.mode in _MODES, f"mode must be one of {_MODES}")
        need(0 < self.f_low_lf_hz <= self.f_low_ringout_hz <= self.f_high_hz and self.f_low_watch_hz <= self.f_high_hz,
             "frequency window edges out of order")
        need(self.history_frames >= max(self.watch_confirm_frames, self.family_window_frames, self.decay_window_frames,
                                        self.comove_window_frames) + 2, "history_frames too small")
        need(self.ref_lo_band < self.ref_hi_band, "ref_lo_band must be < ref_hi_band")
        need(self.loud_level_db > self.min_level_db >= self.level_floor_db, "need loud_level_db > min_level_db >= level_floor_db")

    @property
    def weights(self) -> dict[str, float]:
        return {"prominence": self.w_prominence, "persistence": self.w_persistence, "growth": self.w_growth}

    @property
    def f_low_hz(self) -> float:
        """Low edge of the feedback window for this mode / LF opt-in (loop brief §3.4)."""
        if self.lf_feedback_possible:
            return self.f_low_lf_hz
        return self.f_low_ringout_hz if self.mode == "ringout" else self.f_low_watch_hz

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
            if typ == "int":
                kw[key] = int(value)
            elif typ == "bool":
                kw[key] = _as_bool(value)
            elif typ == "str":
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
# detector data types
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Detection:
    """One feedback verdict. ``band`` is the 0-based RTA band nearest the line, ``freq_hz`` the line's
    interpolated frequency (from the centroid), ``reasons`` the predicates that justified the cut."""

    ts: float
    band: int
    freq_hz: float
    level_db: float
    prominence_db: float
    slope_db_per_s: float
    frames: int
    confidence: float
    centroid: float = -1.0
    onset: str = ""
    tier: str = ""
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
            "centroid": self.centroid,
            "onset": self.onset,
            "tier": self.tier,
            "reasons": list(self.reasons),
        }


@dataclass
class _Peak:
    band: int
    centroid: float
    level: float          # peak-band level (dB)
    cpow: float           # cluster power b-1..b+1 (dB)
    prom: float
    narrow: float
    partners: int = 0     # count of ×2..×5 partners present as peaks
    partner_ks: tuple[int, ...] = ()
    family: bool = False
    track: Any = None     # the Candidate this peak was associated with (set by _associate)


@dataclass
class Candidate:
    """A tracked spectral line. The first block of fields is the historical public shape (``cfs`` publishes
    ``to_dict()`` as ``cfs.candidate``); the rest is the tracker's state. ``levels``/``ts_list`` are the
    tracked history (peak-band level, frame time); ``frames`` counts tracked frames since ``first_ts``."""

    band: int
    first_ts: float
    frames: int
    levels: list[float]
    ts_list: list[float]
    confidence: float
    override: bool = False
    freq_hz: float = 0.0
    level_db: float = -128.0
    prominence_db: float = 0.0
    slope_db_per_s: float = 0.0
    growth_score: float = 0.0
    last_ts: float = 0.0
    emitted: int = 0
    # -- tracker state -------------------------------------------------------------------------
    uid: int = 0
    centroid: float = 0.0
    centroids: list[float] = field(default_factory=list)
    cpows: list[float] = field(default_factory=list)
    proms: list[float] = field(default_factory=list)
    narrows: list[float] = field(default_factory=list)
    refs: list[float] = field(default_factory=list)
    fam: list[bool] = field(default_factory=list)
    pre_levels: list[float] = field(default_factory=list)   # back-filled cluster power before birth (oldest first)
    pre_band: list[float] = field(default_factory=list)     # back-filled peak-band level before birth
    pre_ts: list[float] = field(default_factory=list)
    born_frame: int = 0
    birth_centroid: float = 0.0
    onset: str = "pending"          # pending | at_arm | adult | ramp | slow
    misses: int = 0
    fam_frames: int = 0             # lifetime count of family frames
    fam_lineage: bool = False       # a family was seen for a solid stretch: remember (S5: note decays to a sine)
    last_family_frame: int = -10**9
    last_moving_frame: int = -10**9
    last_comove_frame: int = -10**9
    last_decay_frame: int = -10**9
    probe_over: list[float] = field(default_factory=list)   # (response - step) per master step seen
    probe_steps_done: int = 0
    state: str = "track"            # track | candidate | musical | feedback
    tier: str = ""
    reasons: tuple[str, ...] = ()
    last_emit_ts: float | None = None
    hops: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "band": self.band,
            "freq_hz": self.freq_hz,
            "centroid": round(self.centroid, 3),
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "frames": self.frames,
            "level_db": self.level_db,
            "prominence_db": self.prominence_db,
            "slope_db_per_s": self.slope_db_per_s,
            "growth_score": self.growth_score,
            "confidence": self.confidence,
            "emitted": self.emitted,
            "onset": self.onset,
            "state": self.state,
            "tier": self.tier,
            "reasons": list(self.reasons),
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


def _median_small(xs: list[float]) -> float:
    xs.sort()
    m = len(xs)
    if m % 2:
        return xs[m // 2]
    return 0.5 * (xs[m // 2 - 1] + xs[m // 2])


def _pow(db: float) -> float:
    return 10.0 ** (0.1 * db)


def _db(p: float) -> float:
    return 10.0 * math.log10(p) if p > 1e-30 else -300.0


# ---------------------------------------------------------------------------------------------
# detector
# ---------------------------------------------------------------------------------------------


class FeedbackDetector:
    """Frame-by-frame feedback discriminator over an RTA stream (see module docstring).

    ``FeedbackDetector(cfg, band_hz, mode=None, lf_feedback_possible=None)``: the keyword arguments, when
    given, override ``cfg.mode`` / ``cfg.lf_feedback_possible`` (``cfg`` is replaced by a copy).
    """

    def __init__(self, cfg: DetectorConfig, band_hz: Sequence[float], *, mode: str | None = None,
                 lf_feedback_possible: bool | None = None) -> None:
        if mode is not None or lf_feedback_possible is not None:
            import dataclasses as _dc
            kw: dict[str, Any] = {}
            if mode is not None:
                kw["mode"] = "ringout" if str(mode).lower().replace("_", "") in ("ringout", "ring") else "watch"
            if lf_feedback_possible is not None:
                kw["lf_feedback_possible"] = bool(lf_feedback_possible)
            cfg = _dc.replace(cfg, **kw)
        self.cfg = cfg
        self.band_hz: tuple[float, ...] = tuple(float(h) for h in band_hz)
        n = len(self.band_hz)
        if n < 2 * cfg.neighbour_bins + 1:
            raise ValueError("band_hz too short for neighbour_bins")
        self._n = n
        self._log_hz = [math.log(h) for h in self.band_hz]
        lo_hz, hi_hz = cfg.f_low_hz, cfg.f_high_hz
        # window in centroid units (fractional band index), edges half a band generous
        self._c_lo = self._centroid_for_hz(lo_hz) - 0.5
        self._c_hi = self._centroid_for_hz(hi_hz) + 0.5
        self._ref_lo = max(0, min(n - 2, cfg.ref_lo_band))
        self._ref_hi = max(self._ref_lo + 1, min(n - 1, cfg.ref_hi_band))
        self._uid = 0
        self.reset()

    # -- lifecycle -------------------------------------------------------------------------------
    def reset(self) -> None:
        self._tracks: list[Candidate] = []
        self._cooldown: dict[int, float] = {}
        self._hist: deque[tuple[float, list[float]]] = deque(maxlen=self.cfg.backfill_frames)
        self._refs: deque[float] = deque(maxlen=self.cfg.history_frames)
        self._steps: list[tuple[float, float]] = []       # (ts, delta_db) master steps (ring_out probe)
        self._cuts: list[tuple[float, float, float]] = [] # (ts, hz, depth)
        self._recent_dead: list[tuple[int, Candidate]] = []
        self.frames_seen: int = 0
        self.last_ts: float | None = None
        self.flags: set[str] = set()
        self._flat_run: list[int] = [0] * self._n
        self._last_vals: list[float] | None = None

    # -- optional hints from cfs -----------------------------------------------------------------
    def note_gain_step(self, delta_db: float, ts: float) -> None:
        """The server changed the bus master by ``delta_db`` at ``ts`` (ring_out RAISE step or back-off).
        Every live track's response to it is measured (active probe, loop brief §1.5 / P7)."""
        self._steps.append((float(ts), float(delta_db)))
        if len(self._steps) > 64:
            del self._steps[0]

    def note_cut(self, freq_hz: float, depth_db: float, ts: float) -> None:
        """A GEQ cut of ``depth_db`` centred at ``freq_hz`` was written at ``ts`` (informational: a track
        near it that then decays is the cut working, and is not re-emitted while it decays)."""
        self._cuts.append((float(ts), float(freq_hz), float(depth_db)))
        if len(self._cuts) > 64:
            del self._cuts[0]

    # -- geometry helpers ------------------------------------------------------------------------
    def _centroid_for_hz(self, hz: float) -> float:
        lh = math.log(max(1e-6, hz))
        L = self._log_hz
        if lh <= L[0]:
            return (lh - L[0]) / (L[1] - L[0])
        if lh >= L[-1]:
            return (self._n - 1) + (lh - L[-1]) / (L[-1] - L[-2])
        # bands are log-uniform; find by formula then refine
        i = int((lh - L[0]) / (L[-1] - L[0]) * (self._n - 1))
        i = max(0, min(self._n - 2, i))
        while i > 0 and L[i] > lh:
            i -= 1
        while i < self._n - 2 and L[i + 1] < lh:
            i += 1
        return i + (lh - L[i]) / (L[i + 1] - L[i])

    def hz_for_centroid(self, c: float) -> float:
        """Frequency (Hz) of a fractional band index (log-interpolated between band centres)."""
        L = self._log_hz
        if c <= 0:
            return math.exp(L[0] + c * (L[1] - L[0]))
        if c >= self._n - 1:
            return math.exp(L[-1] + (c - (self._n - 1)) * (L[-1] - L[-2]))
        i = int(c)
        f = c - i
        return math.exp(L[i] + f * (L[i + 1] - L[i]))

    # -- per-frame spectral analysis ---------------------------------------------------------------
    def prominences(self, values_db: Sequence[float]) -> list[float]:
        """``level[i] - median(neighbours within ±neighbour_bins, excluding i)`` for every band (dB)."""
        n = len(values_db)
        k = self.cfg.neighbour_bins
        out: list[float] = []
        for i in range(n):
            lo = max(0, i - k)
            hi = min(n, i + k + 1)
            neigh = [values_db[j] for j in range(lo, hi) if j != i]
            out.append(values_db[i] - _median_small(neigh))
        return out

    def _find_peaks(self, v: list[float], p: list[float], prom: list[float]) -> list[_Peak]:
        cfg = self.cfg
        n = self._n
        peaks: list[_Peak] = []
        floor = cfg.peak_floor_db
        for i in range(1, n - 1):
            vi = v[i]
            if prom[i] < floor or vi <= -127.0:
                continue
            if not (vi > v[i - 1] and vi >= v[i + 1]):
                continue
            a, b_ = v[i - 1], v[i + 1]
            den = a - 2.0 * vi + b_
            d = 0.5 * (a - b_) / den if den < -1e-9 else 0.0
            if d > 0.5:
                d = 0.5
            elif d < -0.5:
                d = -0.5
            cp = _db(p[i - 1] + p[i] + p[i + 1])
            lo2 = v[i - 2] if i >= 2 else -128.0
            hi2 = v[i + 2] if i + 2 < n else -128.0
            narrow = vi - max(lo2, hi2)
            # cluster prominence: the whole line (b-1..b+1) over the ring of bands ±2..±4 — the fair measure for a
            # line sitting between two centres (each band then reads -3 dB) [analyser brief §3]
            ring_ = [v[j] for j in (i - 4, i - 3, i - 2, i + 2, i + 3, i + 4) if 0 <= j < n]
            cprom = cp - _median_small(ring_) if ring_ else prom[i]
            peaks.append(_Peak(band=i, centroid=i + d, level=vi, cpow=cp, prom=max(prom[i], cprom), narrow=narrow))
        return peaks

    @staticmethod
    def _coborn(a: "Candidate | None", b: "Candidate | None") -> bool:
        """Strict co-onset: both tracked and born within 2 frames of each other (or both present at arm)."""
        if a is None or b is None or a is b:
            return False
        if a.onset == "at_arm" and b.onset == "at_arm":
            return True
        return abs(a.born_frame - b.born_frame) <= 2 and a.onset != "at_arm" and b.onset != "at_arm"

    @staticmethod
    def _compatible(a: "Candidate | None", b: "Candidate | None") -> bool:
        """Could two peaks belong to ONE source? Partials of a note onset together (within the analyser rise time)
        and co-modulate (vibrato/tremolo/decay); an unrelated line that happens to sit at an integer ratio does
        neither [loop brief §5 P1, analyser brief §4.3]. Untracked (weak) partners get the benefit of the doubt."""
        if a is None or b is None or a is b:
            return True
        if abs(a.born_frame - b.born_frame) <= 3 or (a.onset == "at_arm" and b.onset == "at_arm"):
            # co-onset (or both older than our memory): one source unless the envelopes have since DIVERGED —
            # partials of a note keep their level ratio to a few dB; a ring growing through a note's harmonic
            # position changes it by tens of dB
            k = min(len(a.cpows), len(b.cpows))
            if k >= 4:
                d_then = a.cpows[-k] - b.cpows[-k]
                d_now = 0.5 * ((a.cpows[-1] - b.cpows[-1]) + (a.cpows[-2] - b.cpows[-2]))
                if abs(d_now - d_then) > 5.0:
                    return False
            return True
        xa = a.cpows[-9:]
        xb = b.cpows[-9:]
        m = min(len(xa), len(xb))
        if m >= 6:
            da = [xa[-m:][i + 1] - xa[-m:][i] for i in range(m - 1)]
            db_ = [xb[-m:][i + 1] - xb[-m:][i] for i in range(m - 1)]
            k = len(da)
            ma = sum(da) / k
            mb = sum(db_) / k
            va = sum((x - ma) ** 2 for x in da)
            vb = sum((x - mb) ** 2 for x in db_)
            if va >= 0.09 * k and vb >= 0.09 * k:          # both actually modulate (sd >= 0.3 dB/frame)
                cov = sum((x - ma) * (y - mb) for x, y in zip(da, db_))
                if cov / math.sqrt(va * vb) >= 0.6:
                    return True
        return False

    def _group(self, peaks: list[_Peak]) -> None:
        """Single-frame harmonic grouping (with the tracks' onset/co-modulation as tie-breaker): mark peaks that
        belong to a harmonic family.

        For each peak P the hypotheses "P is the m-th harmonic of f0 = f/m" (m = 1, 2, 3) are tested: the other
        partials k = 1..6 of that f0 are predicted at centroid + 10*log2(k/m) bands and looked up among the peaks
        (presence-as-peak within ``harmonic_tol_bands``; a partial ABOVE P must be within ``partner_window_db`` of
        P's level - distortion products of a howl sit lower than that -, one BELOW P may be louder or up to
        ``sub_window_db`` quieter; and its track must be onset/co-modulation compatible with P's, or clearly louder
        = P was unmasked). P is *in a family* if some hypothesis finds >= 2 partials, or exactly 1 that was born in
        the same frame (±2) as P (organ 8'+4', flute + H2, a fifth of a hidden fundamental). Two rings of one rig
        at an exact ratio start at different moments and never satisfy either rule [loop brief §2.3, §5 P1/P2]."""
        cfg = self.cfg
        tol = cfg.harmonic_tol_bands
        n = self._n
        by_band: list[_Peak | None] = [None] * n
        for pk in peaks:
            by_band[pk.band] = pk
        compat = self._compatible
        coborn = self._coborn

        def near(target: float, min_level: float, me: _Peak, louder_ok: float | None) -> _Peak | None:
            b0 = int(round(target))
            best = None
            bd = tol
            for b in (b0 - 1, b0, b0 + 1):
                if 0 <= b < n:
                    q = by_band[b]
                    if q is not None and q is not me and q.level >= min_level:
                        dd = abs(q.centroid - target)
                        if dd <= bd and (compat(me.track, q.track)
                                         or (louder_ok is not None and q.level >= me.level + louder_ok)):
                            best, bd = q, dd
            return best

        for pk in peaks:
            pk.family = False
            best_ks: tuple[int, ...] = ()
            for m in (1, 2, 3, 4, 5):
                found: list[tuple[int, _Peak]] = []
                for k in range(1, 7):
                    if k == m:
                        continue
                    t = pk.centroid + _LOG_RATIO[k] - _LOG_RATIO[m]
                    if t < 0.5 or t > n - 1.5:
                        continue
                    if k > m:
                        q = near(t, pk.level - cfg.partner_window_db, pk, None)
                    else:
                        q = near(t, pk.level - cfg.sub_window_db, pk, None)
                    if q is not None:
                        found.append((k, q))
                if m >= 4:
                    # "I am the 4th/5th harmonic": only of a fundamental that is really there and much louder
                    # (clip/drive harmonics, brass); otherwise chords explain too many positions by chance
                    if not any(k == 1 and q.level >= pk.level + 10.0 for k, q in found):
                        found = []
                if len(found) >= 2:
                    pk.family = True
                    best_ks = tuple(k for k, _ in found)
                    break
                if len(found) == 1 and coborn(pk.track, found[0][1].track):
                    pk.family = True
                    best_ks = (found[0][0],)
                    break
                if m == 1:
                    best_ks = tuple(k for k, _ in found)
            pk.partner_ks = best_ks
            pk.partners = len(best_ks)

    # -- tracks ----------------------------------------------------------------------------------
    def _new_track(self, pk: _Peak, ts: float, ref: float) -> Candidate:
        cfg = self.cfg
        self._uid += 1
        c = Candidate(band=pk.band, first_ts=ts, frames=0, levels=[], ts_list=[], confidence=0.0, uid=self._uid)
        c.born_frame = self.frames_seen
        c.birth_centroid = pk.centroid
        c.centroid = pk.centroid
        # back-fill the cluster power of this band from the frame buffer (pre-birth history)
        b = pk.band
        pre_l: list[float] = []
        pre_b: list[float] = []
        pre_t: list[float] = []
        for hts, hv in self._hist:
            lo = max(0, b - 1)
            hi = min(self._n - 1, b + 1)
            pre_l.append(_db(sum(_pow(hv[j]) for j in range(lo, hi + 1))))
            pre_b.append(max(hv[lo:hi + 1]) if abs(pk.centroid - b) > 0.3 else hv[b])
            pre_t.append(hts)
        c.pre_levels = pre_l
        c.pre_band = pre_b
        c.pre_ts = pre_t
        if self.frames_seen < cfg.arm_frames:
            c.onset = "at_arm"
        # continuation of a line that just died next door (hand-held hop, loop brief §2.3): inherit status
        for dead_frame, d in self._recent_dead:
            if self.frames_seen - dead_frame <= 2 and d.emitted > 0 and abs(d.centroid - pk.centroid) <= 2.5:
                c.emitted = d.emitted
                c.last_emit_ts = d.last_emit_ts
                c.hops = d.hops + 1
                c.onset = d.onset if d.onset != "pending" else c.onset
                c.state = d.state
                break
        return c

    def _extend(self, c: Candidate, pk: _Peak, ts: float, ref: float) -> None:
        cap = self.cfg.history_frames
        c.frames += 1
        c.misses = 0
        c.band = pk.band
        c.centroid = pk.centroid
        c.freq_hz = self.hz_for_centroid(pk.centroid)
        c.level_db = pk.level
        c.prominence_db = pk.prom
        c.last_ts = ts
        c.levels.append(pk.level)
        c.ts_list.append(ts)
        c.centroids.append(pk.centroid)
        c.cpows.append(pk.cpow)
        c.proms.append(pk.prom)
        c.narrows.append(pk.narrow)
        c.refs.append(ref)
        pk.track = c
        if len(c.levels) > cap:
            for lst in (c.levels, c.ts_list, c.centroids, c.cpows, c.proms, c.narrows, c.refs):
                del lst[0]
            if len(c.fam) > cap:
                del c.fam[0]

    def _note_family(self, peaks: list[_Peak]) -> None:
        cap = self.cfg.history_frames
        for pk in peaks:
            c = pk.track
            if c is None:
                continue
            c.fam.append(pk.family)
            if pk.family:
                c.fam_frames += 1
                c.last_family_frame = self.frames_seen
            if len(c.fam) > cap:
                del c.fam[0]

    def _associate(self, peaks: list[_Peak], ts: float, ref: float) -> None:
        cfg = self.cfg
        pairs: list[tuple[float, int, int]] = []
        for ti, t in enumerate(self._tracks):
            for pi, pk in enumerate(peaks):
                d = abs(pk.centroid - t.centroid)
                if d <= cfg.assoc_tol_bands:
                    pairs.append((d, ti, pi))
        pairs.sort()
        used_t: set[int] = set()
        used_p: set[int] = set()
        for d, ti, pi in pairs:
            if ti in used_t or pi in used_p:
                continue
            used_t.add(ti)
            used_p.add(pi)
            self._extend(self._tracks[ti], peaks[pi], ts, ref)
        survivors: list[Candidate] = []
        self._recent_dead = [(f, d) for f, d in self._recent_dead if self.frames_seen - f <= 3]
        for ti, t in enumerate(self._tracks):
            if ti in used_t:
                survivors.append(t)
                continue
            t.misses += 1
            if t.misses <= cfg.max_gap_frames:
                survivors.append(t)
            else:
                self._recent_dead.append((self.frames_seen, t))
        # births
        if len(survivors) < cfg.max_tracks:
            for pi, pk in enumerate(peaks):
                if pi in used_p:
                    continue
                if pk.prom < cfg.track_floor_db or pk.level < cfg.level_floor_db:
                    continue
                c = self._new_track(pk, ts, ref)
                self._extend(c, pk, ts, ref)
                survivors.append(c)
                if len(survivors) >= cfg.max_tracks:
                    break
        survivors.sort(key=lambda c: c.centroid)
        self._tracks = survivors

    # -- per-track features ----------------------------------------------------------------------
    def _classify_onset(self, c: Candidate) -> None:
        """Decide how the line arrived, once two frames after birth are in (see module docstring)."""
        cfg = self.cfg
        if c.onset != "pending":
            return
        if c.frames < 3:
            return
        # the peak band's own past (not the cluster: a neighbouring note that just ended must not count as
        # "this energy was already here")
        pre = c.pre_band[-6:]
        if len(pre) < 3:
            c.onset = "at_arm"
            return
        seq = pre + c.levels[:3]
        npre = len(pre)
        floor_ref = _median_small(list(pre))          # robust floor: the bed under the line fluctuates by dB
        top = max(seq[npre:])
        total = top - floor_ref
        incs = [b - a for a, b in zip(seq, seq[1:])]
        # arrival: from the last "still near the floor" sample to the first sample within 1.5 dB of the top
        i_top = next(i for i in range(len(seq)) if seq[i] >= top - 1.5)
        near_floor = floor_ref + min(6.0, 0.35 * total)
        i_start = max((i for i in range(i_top) if seq[i] <= near_floor), default=None)
        arrival = (i_top - i_start) if i_start is not None else len(seq)   # 1 = full level in one frame
        # ``top`` is the max of the first three tracked frames, so "arrival <= 2" means the line reached its
        # level within two frames of leaving the floor and did not keep rising: a note onset (or an onset the
        # analyser smeared over ~2 frames at LF), not regeneration.
        if total >= 8.0 and arrival <= 2:
            c.onset = "adult"
            return
        # ramp: the longest run of consecutive increments >= ramp_min_step (>= 3 of them, >= 4 dB, roughly
        # linear: regeneration is linear in dB, an onset through the analyser decelerates)
        best: list[float] = []
        cur: list[float] = []
        for x in incs + [-999.0]:
            if x >= cfg.ramp_min_step_db:
                cur.append(x)
            else:
                if len(cur) > len(best):
                    best = cur
                cur = []
        run = best
        if len(run) >= 3 and sum(run) >= 4.0:
            h = len(run) // 2
            first = sum(run[:h]) / max(1, h)
            second = sum(run[h:]) / max(1, len(run) - h)
            tot = sum(run)
            srt = sorted(run, reverse=True)
            even = srt[0] <= 0.5 * tot and (len(run) < 4 or srt[0] + srt[1] <= (2.0 / len(run) + 0.25) * tot)
            if first > 0 and second / first >= cfg.ramp_linearity and even:
                c.onset = "ramp"
                return
        c.onset = "slow"

    def _ramp_stats(self, c: Candidate) -> tuple[int, float, float, bool, float, bool, bool]:
        """Current rise ending now: (n_increments, rise_db, slope_db_s, still_rising, ref_change, linear, strict).
        Uses cluster power (offset/vibrato invariant) over back-fill + tracked history."""
        cfg = self.cfg
        seq = c.pre_levels[-8:] + c.cpows
        tss = c.pre_ts[-8:] + c.ts_list
        if len(seq) < 3:
            return 0, 0.0, 0.0, False, 0.0, False, False
        # walk back while increments stay >= min step (allow ONE sub-threshold increment inside the run
        # if the one before it is fine: RTA noise on a slow ramp)
        step = cfg.ramp_min_step_db
        fp = cfg.frame_period_s

        def inc(i: int) -> float:      # per-frame increment ending at sample i (gaps from masked frames normalised)
            dt = tss[i] - tss[i - 1]
            k = max(1.0, round(dt / fp)) if dt > 0 else 1.0
            return (seq[i] - seq[i - 1]) / k

        i = len(seq) - 1
        n_inc = 0
        skips = 0
        while i > 0:
            d = inc(i)
            if d >= step:
                n_inc += 1
                i -= 1
                continue
            if d > -step and skips == 0 and i - 1 > 0 and inc(i - 1) >= step and n_inc >= 1:
                skips += 1
                n_inc += 1
                i -= 1
                continue
            break
        start = i
        if n_inc < 2:
            return n_inc, 0.0, 0.0, False, 0.0, False, False
        run = seq[start:]
        rts = tss[start:]
        rise = run[-1] - run[0]
        incs = [inc(j) for j in range(start + 1, len(seq))]
        h = len(incs) // 2
        first = sum(incs[:h]) / max(1, h)
        second = sum(incs[h:]) / max(1, len(incs) - h)
        linear = first <= 0 or (second / first >= cfg.ramp_linearity)
        # spread of increments: regeneration is linear in dB; an onset through the analyser decelerates,
        # a swell that then holds has a knee. Allow generous spread for wander/noise.
        if len(incs) >= 3:
            m = sorted(incs)[len(incs) // 2]
            linear = linear and max(incs) <= 3.0 * max(m, step) + 1.0
        if rise >= 8.0:
            # no one or two frames carry the rise: regeneration spreads it evenly (linear in dB); a note onset
            # that straddles a frame boundary shows as one or two big increments and then nothing
            srt = sorted(incs, reverse=True)
            linear = linear and srt[0] <= 0.5 * rise
            if len(incs) >= 4:
                linear = linear and (srt[0] + srt[1]) <= (2.0 / len(incs) + 0.25) * rise
        slope = _ls_slope(rts, run) if len(run) >= 2 else 0.0
        still = incs[-1] >= max(step, 0.3 * (sum(incs) / len(incs)))
        # reference change over the same span (tracked part only; pre-birth refs from the detector history)
        refs = list(self._refs)
        span = len(run)
        ref_change = 0.0
        if len(refs) >= span:
            rseg = refs[-span:]
            ref_change = max(rseg) - rseg[0]
        strict = linear and first > 0 and second / first >= 0.7      # not decelerating even mildly (LF analyser smear)
        return n_inc, rise, slope, still and linear, ref_change, linear, strict

    def _stationary(self, c: Candidate, k: int) -> tuple[bool, float]:
        """Centroid stationarity over the last ``k`` tracked frames: (stationary, window median)."""
        cfg = self.cfg
        cs = c.centroids[-k:]
        if len(cs) < 2:
            return True, c.centroid
        m = _median_small(list(cs))
        tol = cfg.centroid_tol_bands
        out = sum(1 for x in cs if abs(x - m) > tol)
        ok = out <= cfg.stationary_outlier_frac * len(cs) and abs(cs[-1] - m) <= cfg.glide_bands
        return ok, m

    def _comoving(self, c: Candidate) -> bool | None:
        """Does the line's level follow the spectrum reference (≈1 dB/dB)? None when the reference did not move."""
        cfg = self.cfg
        w = cfg.comove_window_frames
        ys = c.cpows[-w:]
        xs = c.refs[-w:]
        if len(ys) < 6:
            return None
        xr = max(xs) - min(xs)
        if xr < cfg.comove_ref_range_db:
            return None
        n = len(xs)
        xm = sum(xs) / n
        ym = sum(ys) / n
        sxx = sum((x - xm) ** 2 for x in xs)
        if sxx <= 1e-9:
            return None
        sxy = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
        a = sxy / sxx
        syy = sum((y - ym) ** 2 for y in ys)
        resid = max(0.0, syy - a * sxy)
        sd_y = math.sqrt(syy / n)
        sd_r = math.sqrt(resid / n)
        if 0.6 <= a <= 1.5 and sd_r <= 0.35 * sd_y + 0.15:
            return True
        return False

    def _probe(self, c: Candidate) -> None:
        """Evaluate any master steps whose post-window has just completed for this track."""
        cfg = self.cfg
        if not self._steps:
            return
        ts_now = c.last_ts
        tss = c.pre_ts + c.ts_list
        lv = c.pre_levels + c.cpows
        while c.probe_steps_done < len(self._steps):
            st, delta = self._steps[c.probe_steps_done]
            if ts_now < st + cfg.probe_settle_s + cfg.probe_window_s:
                break
            c.probe_steps_done += 1
            pre = [l for t, l in zip(tss, lv) if st - cfg.probe_window_s - 0.02 <= t < st - 0.02]
            post = [l for t, l in zip(tss, lv) if st + cfg.probe_settle_s <= t <= st + cfg.probe_settle_s + cfg.probe_window_s + 0.02]
            if len(pre) < 3 or len(post) < 3:
                continue
            resp = _median_small(post) - _median_small(pre)
            # a positive step: programme moves 0..+delta (pre-fader tap, spill), a loop near threshold more;
            # a negative step (back-off): programme 0..delta, a regenerating line falls much further.
            over = (resp - delta) if delta >= 0 else (delta - resp)
            c.probe_over.append(over)
            if len(c.probe_over) > 8:
                del c.probe_over[0]

    # -- decision --------------------------------------------------------------------------------
    def _in_window(self, centroid: float) -> bool:
        return self._c_lo <= centroid <= self._c_hi

    def _decide(self, c: Candidate, ts: float) -> Detection | None:
        cfg = self.cfg
        fno = self.frames_seen
        ringout = cfg.mode == "ringout"
        K1 = cfg.confirm_frames
        K2 = cfg.confirm_frames if ringout else cfg.watch_confirm_frames
        reasons: list[str] = []

        self._classify_onset(c)
        # -- windowed behaviour flags ------------------------------------------------------------
        wf = cfg.family_window_frames
        famwin = c.fam[-wf:]
        fam_ratio = (sum(1 for f in famwin if f) / len(famwin)) if famwin else 0.0
        if len(famwin) >= 8 and sum(1 for f in famwin[-10:] if f) >= 6:
            c.fam_lineage = True
        elif c.fam_lineage and fno - c.last_family_frame > 3 * cfg.family_window_frames:
            c.fam_lineage = False       # lineage memory (a note decaying to a sine) fades after ~3 s without family
        family = fam_ratio >= cfg.family_ratio or c.fam_lineage or (c.frames <= 3 and c.fam and c.fam[-1])
        fam_recent = any(c.fam[-3:]) if c.fam else False

        stat_k1, med_c = self._stationary(c, K1)
        stat_k2, med_k2 = self._stationary(c, K2)
        glide = False
        if c.frames <= 3 * K2 and abs(med_c - c.birth_centroid) > cfg.glide_bands:
            glide = True                                   # portamento / scoop at birth
        elif len(c.centroids) >= 2 * K2:
            prev = _median_small(list(c.centroids[-2 * K2:-K2]))
            if abs(med_k2 - prev) > 0.5:                   # stepped to a new pitch (legato note change / hop)
                glide = True
        if not stat_k1 or glide:
            c.last_moving_frame = fno
        moving_recent = fno - c.last_moving_frame < K2

        cm = self._comoving(c)
        if cm is True:
            c.last_comove_frame = fno
        comove_recent = fno - c.last_comove_frame < K2

        dw = cfg.decay_window_frames
        lv = c.cpows[-dw:]
        lt = c.ts_list[-dw:]
        slope_dw = _ls_slope(lt, lv) if len(lv) >= 4 else 0.0
        if len(lv) >= 6 and slope_dw <= -cfg.decay_db_per_s and (lv[0] - lv[-1]) >= 1.0:
            c.last_decay_frame = fno
        decay_recent = fno - c.last_decay_frame < K2

        n_inc, rise, rslope, still, ref_change, linear, strict_linear = self._ramp_stats(c)
        ramp_ok = linear and n_inc >= 3 and rise >= 4.0 and ref_change <= 0.5 * rise and rslope >= cfg.growth_min_db_per_s
        ramp_strong = ramp_ok and (
            (n_inc >= cfg.ramp_strong_frames and still)                       # >= 300 ms of steady exponential growth
            or (rise >= cfg.ramp_strong_rise_db and n_inc >= 4)              # or a lot of it (fast loop)
            or (n_inc >= 4 and still and rslope <= cfg.ramp_moderate_db_per_s and strict_linear
                and c.prominence_db >= cfg.prominence_db + 2.0)              # or >= 200 ms of it at a rate no
        )                                                                    # note attack is that slow AND that even
        # a fresh ramp re-qualifies a line whose earlier life looked like programme (chord tone's band taken
        # over by a ring; kick re-triggering the band of an LF loop): behaviour NOW wins over lineage,
        # except for a family visible NOW.
        if ramp_ok and n_inc >= cfg.ramp_strong_frames and rise >= 10.0 and not fam_recent:
            if c.onset in ("adult", "slow", "at_arm"):
                c.onset = "ramp"
            if c.fam_lineage and fam_ratio < cfg.family_ratio:
                c.fam_lineage = False   # >= 300 ms of solitary exponential growth: whatever shared this band before,
                family = False          # what is growing now has no family

        # steadiness over the tier-B window (cluster power)
        wlv = c.cpows[-K2:]
        steady = len(wlv) >= 2 and (max(wlv) - min(wlv)) <= cfg.plateau_range_db
        # slow rise (marginal loop, 1-6 dB/s): judged over >= 1.2 s so tremolo/Leslie AM (0.8-7 Hz) cannot pose as one
        LW = 2 * K2
        llv = c.cpows[-LW:]
        lts = c.ts_list[-LW:]
        slow_rise = False
        wslope = 0.0
        if len(llv) >= max(24, K2 + 4):
            wslope = _ls_slope(lts, llv)
            if 0.8 <= wslope < max(cfg.growth_min_db_per_s, 8.0):
                n_ = len(llv)
                tm = sum(lts) / n_
                vm = sum(llv) / n_
                res = math.sqrt(sum((v - vm - wslope * (t - tm)) ** 2 for t, v in zip(lts, llv)) / n_)
                slow_rise = res <= 0.8

        # short-window slope for reporting
        sl = c.cpows[-10:]
        st_ = c.ts_list[-10:]
        c.slope_db_per_s = _ls_slope(st_, sl) if len(sl) >= 3 else 0.0
        c.growth_score = max(0.0, min(1.0, rslope / cfg.growth_ref_db_per_s)) if ramp_ok else 0.0

        # probe
        if ringout:
            self._probe(c)
        po = c.probe_over
        probe_strong = bool(po) and (po[-1] >= 2.0 * cfg.probe_over_db or (len(po) >= 2 and po[-1] >= cfg.probe_over_db and po[-2] >= 0.6 * cfg.probe_over_db))
        probe_linear = len(po) >= 2 and all(abs(x) <= 0.8 for x in po[-2:]) and not probe_strong

        # -- gates -------------------------------------------------------------------------------
        level = c.level_db
        prom_now = c.prominence_db
        proms = c.proms[-K1:]
        prom_med = _median_small(list(proms[-3:])) if proms else prom_now
        prom_ok = prom_med >= cfg.prominence_db and prom_now >= cfg.prominence_db - 1.5
        nar = c.narrows[-K1:]
        narrow_ok = sum(1 for x in nar if x >= cfg.narrow_db) * 2 >= len(nar)
        in_win = self._in_window(med_c)
        level_ok = level >= cfg.level_floor_db
        steady_level_ok = level >= cfg.min_level_db
        clip = level >= cfg.clip_level_db
        loud = level >= cfg.loud_level_db
        age_ok = c.frames >= cfg.persistence_frames

        if clip:
            family = False       # the desk's clip flag: a howl at full scale grows odd harmonics; nothing narrow that
                                 # loud is left alone because of them (loop brief §2.2, analyser brief §4.3)
        base = in_win and level_ok and prom_ok and narrow_ok and age_ok and not family and stat_k1 and not glide
        musical = family or moving_recent or comove_recent
        c.state = "musical" if musical else ("candidate" if (prom_ok and level_ok) else "track")

        tier = ""
        if base:
            if clip:
                tier = "A:clip"
            elif loud and c.frames >= K1 and not moving_recent:
                tier = "A:loud"
            elif ramp_strong:
                tier = "A:ramp"
            elif probe_strong and not moving_recent:
                tier = "A:probe"
            elif (c.onset == "at_arm" and prom_now >= cfg.strong_prominence_db and c.frames >= K1 and steady_level_ok
                  and steady and not musical and not decay_recent and not probe_linear
                  and _median_small(list(proms)) >= cfg.strong_prominence_db):
                tier = "A:prominent"
            elif (c.onset in ("at_arm", "slow", "ramp") and c.frames >= K2 and stat_k2 and not musical
                  and not decay_recent and not probe_linear
                  and (slow_rise or ramp_ok
                       or (steady and steady_level_ok and (ringout or prom_med >= cfg.strong_prominence_db)))):
                # watch: a steady line with no growth evidence must at least be strongly prominent (a 12-18 dB
                # plateau that was never seen to grow is published as a candidate, not cut: loop brief §4.3 tier B/C)
                tier = "B"
            elif ringout and c.onset == "adult" and c.frames >= K2 and stat_k2 and not musical and not decay_recent \
                    and steady and probe_strong:
                tier = "A:probe"
        # confidence for the dashboard: monotone in the margins, >= threshold iff a tier is reached
        conf = 0.0
        conf += 0.25 * min(1.0, max(0.0, prom_now) / max(cfg.prominence_db, 1e-6))
        conf += 0.15 * min(1.0, c.frames / max(1, K2))
        conf += 0.2 * (1.0 if ramp_ok else 0.0) + 0.1 * (1.0 if ramp_strong else 0.0)
        conf += 0.1 * (1.0 if loud or clip else 0.0)
        conf += 0.1 * (1.0 if (stat_k1 and not musical) else 0.0)
        if musical or c.onset == "adult" and not (loud or clip):
            conf = min(conf, 0.5)
        if tier:
            conf = max(conf, cfg.confidence_threshold)
        else:
            conf = min(conf, cfg.confidence_threshold - 0.01)
        c.confidence = round(min(1.0, conf), 3)
        c.tier = tier
        if not tier:
            c.reasons = tuple(r for r, on in (("family", family), ("moving", moving_recent), ("comoving", comove_recent),
                                               ("decaying", decay_recent), ("adult_onset", c.onset == "adult"),
                                               ("out_of_window", not in_win), ("below_floor", not level_ok),
                                               ("below_gate", not steady_level_ok),
                                               ("not_prominent", not prom_ok), ("broad", not narrow_ok),
                                               ("probe_linear", probe_linear)) if on)
            return None
        # -- emission with cooldown -----------------------------------------------------------------
        if c.last_emit_ts is not None and ts - c.last_emit_ts < cfg.cooldown_s:
            return None
        if c.emitted and decay_recent:
            return None          # the cut is working (or the display is releasing): do not deepen
        band = int(round(med_c))
        band = max(0, min(self._n - 1, band))
        if self._in_cooldown(band, ts):
            return None
        reasons = [tier, f"onset={c.onset}", f"prom={prom_now:.0f}", f"narrow={c.narrows[-1]:.0f}",
                   f"stationary±{cfg.centroid_tol_bands}", "no_family"]
        if ramp_ok:
            reasons.append(f"ramp={n_inc}fr/{rise:.0f}dB/{rslope:.0f}dB/s")
        if probe_strong:
            reasons.append(f"probe+{po[-1]:.1f}dB")
        c.reasons = tuple(reasons)
        c.state = "feedback"
        c.emitted += 1
        c.last_emit_ts = ts
        self._cooldown[band] = ts + cfg.cooldown_s
        det = Detection(ts=ts, band=band, freq_hz=self.hz_for_centroid(med_c), level_db=level, prominence_db=prom_now,
                        slope_db_per_s=c.slope_db_per_s, frames=c.frames, confidence=c.confidence, centroid=round(med_c, 3),
                        onset=c.onset, tier=tier, reasons=c.reasons)
        log.debug("feedback detected: band %d (%.0f Hz) %.1f dB %s conf %.2f %s", band, det.freq_hz, level, tier,
                  c.confidence, ",".join(reasons))
        return det

    def _in_cooldown(self, band: int, ts: float) -> bool:
        tol = self.cfg.band_tolerance
        for b in range(band - tol, band + tol + 1):
            until = self._cooldown.get(b)
            if until is not None and ts < until:
                return True
        return False

    def _analyser_flags(self, vals: list[float]) -> None:
        """Cheap misconfiguration tells: peak-hold (many live bands exactly flat for >= 10 frames)."""
        last = self._last_vals
        if last is not None:
            flat = 0
            fr = self._flat_run
            for i in range(self._n):
                if vals[i] > -100.0 and vals[i] == last[i]:
                    fr[i] += 1
                    if fr[i] >= 10:
                        flat += 1
                else:
                    fr[i] = 0
            if flat >= 15:
                self.flags.add("PEAK_HOLD_SUSPECTED")
        self._last_vals = vals

    # -- API -----------------------------------------------------------------------------------
    def feed(self, values_db: Sequence[float], ts: float) -> list[Detection]:
        """Process one RTA frame (``len(values_db) == len(band_hz)``, dB) taken at time ``ts`` (s).
        Returns the detections emitted on this frame (possibly empty)."""
        n = self._n
        vals = [float(v) for v in values_db]
        if len(vals) != n:
            raise ValueError(f"expected {n} RTA values, got {len(vals)}")
        cfg = self.cfg
        p = [_pow(v) for v in vals]
        prom = self.prominences(vals)
        ref = _median_small(vals[self._ref_lo:self._ref_hi + 1])
        self._refs.append(ref)
        peaks = self._find_peaks(vals, p, prom)
        self._associate(peaks, ts, ref)
        self._group(peaks)
        self._note_family(peaks)
        self._analyser_flags(vals)
        out: list[Detection] = []
        for c in self._tracks:
            if c.misses:
                continue
            det = self._decide(c, ts)
            if det is not None:
                out.append(det)
        self._hist.append((ts, vals))
        if self._cooldown and self.frames_seen % 100 == 0:
            self._cooldown = {b: t for b, t in self._cooldown.items() if ts < t}
        self.frames_seen += 1
        self.last_ts = ts
        return out

    @property
    def candidates(self) -> list[Candidate]:
        """Tracked lines that currently clear the tracking floor (live objects; treat as read-only), lowest band
        first."""
        return [c for c in self._tracks if not c.misses and c.prominence_db >= self.cfg.track_floor_db
                and c.level_db >= self.cfg.level_floor_db]

    @property
    def tracks(self) -> list[Candidate]:
        """Every live track including sub-floor ones (diagnostics)."""
        return list(self._tracks)


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
