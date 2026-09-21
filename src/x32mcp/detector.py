"""CFS² feedback discriminator (explicit physical predicates) and notch planner — pure Python, no I/O.

Two synchronous, side-effect-free pieces used by ``cfs.py``:

* :class:`FeedbackDetector` consumes one RTA frame at a time (100 dB values from ``/meters/15``,
  band ``i`` centred at ``band_hz[i]`` = ``10000 * 2 ** ((i - 90) / 10)`` Hz, docs/research/meters.md §4.2)
  and returns :class:`Detection` objects for spectral lines that satisfy the feedback predicates below.
* :class:`NotchController` turns detections into GEQ band cuts (:class:`Notch`) — the planner is unchanged.

Units and indices at the public boundary
----------------------------------------
* Levels are dB (RTA dB re. full scale, -128 = "no signal", 0.0 = the analyser's clip flag), times are
  seconds (``time.time()`` style floats), slopes are dB/s.
* **RTA band indices are 0-based** (``Detection.band`` = the line's peak band); ``Detection.freq_hz`` is the
  *interpolated* line frequency (log-frequency centroid of the peak band ±1, local floor removed), which is
  what the GEQ band choice should use for a line sitting between two RTA centres.
* **GEQ band numbers are 1-based** (``Notch.band`` 1..31 == the ``/fx/N/par/NN`` parameter number).

Why predicates and not the old weighted sum (REVIEW_BRIEF §1 Q2)
-----------------------------------------------------------------
``0.3·prominence + 0.2·persistence + 0.5·growth ≥ 0.7`` let surplus in one physical requirement buy a deficit in
another, made anything already plateaued unreachable (0.5 < 0.7: the M7 60 dB howl at 0.50 for 15 s) and
confounded growth with *frequency*: a 1/10-octave band cannot respond faster than ~1/Δf (370 ms at 39 Hz,
185 ms at 78 Hz, 7 ms at 2 kHz), so every instant bass onset renders at 40–160 Hz as a 4–8-frame 10–40 dB/s
ramp — inside the 6..60 dB/s "regenerative growth" window (M7's 40/80 Hz cuts) — while real rings on short loops
grow at 100–500 dB/s and tripped the 60 dB/s onset guard. Here each predicate is a physical fact with its own
threshold, tested alone (tests/test_detector_predicates.py), and ``Detection.reasons`` names the facts that
justified a cut.

Per frame, over the whole spectrum  (O(bands·k))
-------------------------------------------------
* ``prom[i]`` = level[i] − median(level[i±1..±k] without i), k = ``neighbour_bins``.
* A *line* is a local maximum with prom ≥ ``track_prominence_db`` (6 dB). Its *cluster* is the peak plus any
  neighbour within ``cluster_merge_db`` (a tone between two centres reads −3/−3 dB; edge vibrato alternates the
  louder band). Per line: cluster power, cluster prominence (over the median of the bands ±2..±(k+1) beyond the
  cluster), narrowness (peak − max of the two bands just outside the cluster's skirts), and the power-weighted
  **centroid** of peak±1 with the local floor removed (sub-band frequency, ~±0.1 band).
* Common-mode reference: the last frames' spectra are kept; the common-mode change between two frames is the
  MEDIAN of the per-band changes of the bands that carry signal (bands 25..85 above the frame floor +
  ``ref_signal_db``, outside the line's own neighbourhood). A fader/autogain/master step moves every signal band
  by the same amount; a chord change, a cymbal or one growing line moves a minority. Rises are taken net of any
  common-mode raise beyond ``cm_deadband_db`` (a mix breathes ±1–2 dB).                              [A §5]
* ``base[i]`` = per-band slow low-percentile tracker (fast down, slow up) seeded over the first second: what the
  band read before anything happened. Replaces the absolute ``min_level_db`` gate fitted to one room.
* Lines are matched to tracks by centroid (≤ ``track_match_bands``; established tracks outbid tracks younger than
  K1); an unmatched track coasts ``coast_frames`` (a hat over a quiet ring must not erase its history); a track
  whose centroid walked > ``drift_max_bands`` within ``drift_window_frames`` is RE-BORN at the new frequency with
  no history (glide, scoop, melody step, hand-held hop → everything must be re-earned; ``glided_in`` is noted).

Per track  (K1 = ``stable_frames`` = 5 frames = 250 ms is the binding latency term)
------------------------------------------------------------------------------------
P1 NARROW      cluster prominence ≥ ``prominence_db`` (12) and narrowness ≥ ``narrow_db`` (8). A sinusoid on a
               1/10-oct bank clears 24–60 dB at ±2; formant humps, cymbal wash, body resonances read 0–6. [A §3]
P2 NO FAMILY   partials H2..H5 at +10/+15.85/+20/+23.2 bands from the centroid (±``partial_tol_bands``), each
               *present* only as a line itself ≥ ``partial_prominence_db`` prominent whose LEVEL is within
               ``partial_rel_db`` of the candidate's (instrument partials run 0…−15 dB re H1; a coincidental peak far
               below a strong ring does not count) and which *co-moves* with it (level change over K1 within
               ``comove_tol_db``: an independent ring growing through a held note's harmonic slot does not). Family
               = ≥ 2 partials, or a lone exact H2 within ``h2_pair_rel_db`` born within ``co_onset_frames`` of the
               line (8'+4' organ; two rings an octave apart do not co-onset [L §2.3]). Also vetoed: a line that is
               itself H2/H3 of a lower line owning another partial (H4/H5 only under a base ``subharm_hi_margin_db``
               louder: speech/voice formants, a clipped howl's H5). Escape: a LOUD line keeps no family veto of its
               own (a howl driving an amp into clip grows harmonics the mic hears [L §2.2]); being somebody's harmonic
               has no escape. A veto that first fires after the line already rose ``late_partials_rise_db`` is
               distortion, not a family. MUSICAL when vetoed on ≥ ``family_veto_fraction`` of the last
               ``family_window_frames`` (and not clean for all of the last K1) or on most of the last K1. [A §4.1/4.3]
P3 STABLE      centroid range over the last K1 frames ≤ 2·``centroid_tol_bands`` (±0.25 band = ±30 c). A ring's
               centroid sd is < 0.05 band; vibrato ≥ ±30 c on an edge, scoops and glides fail; slow glides are
               re-born by the drift rule.                                                              [A §4.4]
P4 SUSTAINED   not decaying (LS slope over ≤ ``decay_window_frames`` ≤ −``decay_db_per_s`` with a drop ≥
               ``decay_drop_db``) and not sagging > ``sag_db`` under the recent maximum: plucked/struck notes,
               bells and the RTA *release* of a ring already killed by a cut decay; a ring with the loop intact,
               organ and held notes do not. (Kills the wasted deepening on long ``/-prefs/rta/decay``.)   [L §5]
   COMMON-MODE |Δref| ≥ ``common_mode_db`` over K2 with the line following it within ``common_mode_tol_db`` ⇒
               programme riding a fader/autogain ⇒ MUSICAL for that frame.                              [A §5]
   CO-GROWTH   ≥ ``cogrowth_lines`` other lines rising ≥ ``cogrowth_rise_db`` at once ⇒ pad swell / fade-in
               (rings start alone; two simultaneous rings stay allowed).                                 [A §5]
P5 NEW ENERGY  peak − base ≥ ``baseline_excess_db``; hum, HVAC, rumble live in the baseline. A line born within
               ``arm_frames`` cannot be new: it carries AT-ARM evidence instead iff, over its first K1 frames, its
               median level ≥ ``arm_line_min_level_db`` (−40) and median prominence ≥ ``strong_prominence_db``, and
               it has stayed present (≥ ``arm_presence`` of frames) and steady (range ≤ ``arm_range_db``) — a howl
               found at arm is flat and continuous, a kick pattern present at arm is neither. It is emitted at K1
               if ≥ ``arm_fast_level_db`` (−20), otherwise after ``arm_confirm_s`` (0.8 s: a family-less NOTE that
               happened to be sounding when we armed looks identical until it ends; melody notes end first).
P6 WINDOW      centroid frequency within [``window_low_hz``, ``window_high_hz``]: 160 Hz in watch, 63 Hz in
               ring_out, 40 Hz with ``lf_feedback_possible``; 12.5 kHz.                              [L §3.4]
EVIDENCE (any one makes a BASE line STRONG; none is required, growth in particular is not):
   RISE        cluster level now − the ``rise_quantile`` low quantile of the levels of the current *presence run*,
               net of common mode, ≥ ``rise_db`` (6 dB: a doubling of amplitude, 2.4 dB above the largest
               expressive swell measured on the programme corpus). The run starts ``analyser settle`` = 1.5·k/Δf
               after birth (1 frame ≥ 300 Hz, 2 @ 200 Hz, 4 @ 100 Hz, 7 @ 63 Hz, 11 @ 39 Hz: the band filter's own
               step response is never scored) and RESTARTS (i) on a > ``onset_step_db`` single-frame jump out of a
               line that was not already climbing (two consecutive rising frames, or ≥ 4 contiguous settled samples
               climbing ≥ ``onset_rising_db_per_s`` cleanly): a note re-struck at the same pitch, a syllable, a
               shout landing on a bump; (ii) on re-acquisition after > K1 missed frames unless it was climbing;
               (iii) below ~300 Hz on a > ``lf_onset_step_db`` jump within one settle window or a fall of
               ``restart_drop_db`` (re-excited room mode / re-struck bass note); there, half the rise must also be
               older than one settle window (it was under way before and continued). No rate window: 3 dB/s and
               500 dB/s both count. GROWTH (a clean dB-linear climb ≥ ``growth_min_db_per_s`` over
               ``growth_frames`` with ≥ ``growth_rise_db``) is an optional earlier upgrade, OFF by default.  [L §1.3/1.6]
   LOUD        peak ≥ ``clip_db`` (the 0.0 flag), or ≥ ``loud_line_db`` (−10 dBFS) AND ≥ ``loud_margin_db``
               above every band outside its own neighbourhood; not below 160 Hz (LF loops are long and slow; kick
               and 808 put single LF bands far above everything routinely).                    [L §1.4, §4.3]
   AT-ARM      see P5: an established howl found when arming is emitted K1 = 250 ms after arm.
   PROBE       ring_out: ``note_gain_step(Δ, ts)`` from cfs after each master step; a line steady before the step
               that answers ≥ Δ + ``probe_over_db`` while ref moved ≤ Δ + 0.5, on ``probe_min_hits`` steps, is
               loop-gain dependent (regenerative gain 1/(1−g)) — usually BEFORE it runs away. A line that answers
               ≤ Δ + 1 dB is STATIONARY (hum/HVAC/playback): reported, never cut.               [L §1.5, P7]
   CONFIRMED   emitted before and still BASE: stays eligible (re-emitted once per ``cooldown_s`` = deepening).
Classes / emission
   MUSICAL := family ∨ common-mode ∨ co-growth.   BASE := ¬MUSICAL ∧ P1..P6 ∧ run ≥ K1.
   STRONG := BASE ∧ evidence → emitted (both modes).   MODERATE := BASE only → published as ``candidates`` (the
   dashboard/human sees it), not cut: passively, a dead-steady family-less note (organ flue, sine lead, whistle,
   flute top register) and a compressor-limited ring that was never seen to start are the same observation, and in
   watch a wrong cut is audible, cumulative and spends budget. ring_out adds PROBE, the pre-emptive emission of a
   sub-threshold line that over-responded twice, and — only with ``ringout_emit_moderate`` in a verified
   programme-free room — MODERATE after K2.
``confidence`` is a monotone function of the margins for the dashboard (≥ ``confidence_threshold`` exactly when
emitted); it is not the decision variable. Only ``logging`` is used for diagnostics (stdout is the MCP transport).
[L §x] = loop-physics brief, [A §x] = analyser brief (review scratchpad reports/physics-*/brief.md).
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

# partial offsets in 1/10-octave bands: Hk sits 10·log2(k) bands above H1 [A §4.1]
_PARTIAL_OFFSETS: tuple[tuple[int, float], ...] = tuple((k, 10.0 * math.log2(k)) for k in (2, 3, 4, 5))
_BANDWIDTH_FRACTION = 2.0 ** 0.05 - 2.0 ** -0.05     # Δf/f of a 1/10-octave band = 0.06932


# ---------------------------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------------------------

_WEIGHT_KEYS = {"prominence": "w_prominence", "persistence": "w_persistence", "growth": "w_growth"}
_MODES = ("watch", "ringout")


@dataclass(frozen=True)
class DetectorConfig:
    """Detector + notch thresholds; one field per ``device.yaml`` ``detector:`` key (unknown keys ignored,
    missing keys take these defaults). The yaml ``weights`` mapping is still accepted (flattened into
    ``w_*``) but the weights, ``growth_*``, ``override_*`` and ``min_level_db`` no longer take part in the
    decision — see the module docstring."""

    # -- qualification (P1) --------------------------------------------------------------------
    prominence_db: float = 12.0          # cluster prominence needed to *qualify* (dB over the ±2..±(k+1) median)
    neighbour_bins: int = 3              # k: bands each side for the single-band prominence median
    track_prominence_db: float = 6.0     # a local max this prominent is a *line*: tracked (history) but not judged
    narrow_db: float = 8.0               # P1: peak − max(level at ±2 outside the cluster); >=3-band humps read 0-6
    cluster_merge_db: float = 6.0        # a neighbour within this of the peak belongs to the same line (split line)
    track_match_bands: float = 0.6       # a line continues a track whose last centroid is within this (a ring moves
                                         # < 0.05 band/frame; vibrato, glides and note changes move more: new track)
    coast_frames: int = 8                # a track survives this many frames without its line (masking transient)
    track_young_penalty: float = 0.3     # matching cost added for tracks younger than K1 (established tracks win ties)
    # -- harmonic family (P2) ------------------------------------------------------------------
    partial_prominence_db: float = 6.0   # a partial is 'present' when it is itself a local peak this prominent
    partial_tol_bands: float = 0.6       # ± around centroid + offset (true partials sit at the exact offset ± centroid error)
    partial_rel_db: float = 18.0         # a partial counts only if its LEVEL is within this of the candidate's (partials: 0..-15 dB)
    family_window_frames: int = 20       # the veto fraction is taken over the last this-many matched frames
    cogrowth_lines: int = 2              # this many OTHER lines rising >= cogrowth_rise_db while I rise = programme swell
    cogrowth_rise_db: float = 3.0
    family_min_partials: int = 2         # of H2..H5 present ⇒ the frame is family-vetoed
    family_veto_fraction: float = 0.4    # vetoed on ≥ this share of its frames ⇒ MUSICAL
    late_partials_rise_db: float = 12.0  # a family that first appears after the line rose this much is distortion
    h2_pair_rel_db: float = 12.0         # a lone H2 within this of H1's level, born with it, is a family (8'+4' organ)
    co_onset_frames: int = 2             # 'born together' = track births within this many frames
    subharm_hi_margin_db: float = 3.0    # I am somebody's H4/H5 only if that somebody is this much louder than me (speech H1 vs H4/H5)
    comove_tol_db: float = 3.0           # partials co-move: level changes over K1 frames agree within this
    # -- stability / sustain (P3, P4) ----------------------------------------------------------
    stable_frames: int = 5               # K1
    confirm_frames: int = 12             # K2 (ring_out MODERATE confirmation)
    centroid_tol_bands: float = 0.25     # P3: centroid within ± this over K1 (±30 cents; a ring's sd is < 0.05 band)
    drift_max_bands: float = 1.0         # centroid range over the last drift_window_frames above this ⇒ the line
    drift_window_frames: int = 10        #   moved (glide/step/hop): the track is re-born at the new frequency
    decay_db_per_s: float = 3.0          # P4: falling faster than this (LS over the decay window) ...
    decay_drop_db: float = 1.5           # ... by at least this much ⇒ decaying (not sustained)
    decay_window_frames: int = 12
    sag_db: float = 4.0                  # P4: more than this under the recent (K2-frame) maximum ⇒ not sustained
    common_mode_db: float = 6.0          # |Δref| at least this and |ΔL−Δref| ≤ common_mode_tol ⇒ rides the master
    common_mode_tol_db: float = 2.0
    ref_band_lo: int = 25                # common-mode reference bands (τ_a < 1 frame there): the median CHANGE of the bands
    ref_band_hi: int = 85                #   above the frame floor (ref_floor_quantile + ref_signal_db) at both instants
    ref_floor_quantile: float = 0.1
    ref_signal_db: float = 6.0
    ref_min_bands: int = 6               # fewer signal bands than this: no common-mode estimate (0)
    cm_deadband_db: float = 2.0          # common-mode moves smaller than this are mix dynamics, not gain riding
    # -- new energy (P5) -----------------------------------------------------------------------
    baseline_excess_db: float = 10.0
    baseline_seed_s: float = 1.0         # fast seeding period after arm
    arm_frames: int = 3                  # a line born within this many frames of arming was 'already there'
    arm_line_min_level_db: float = -40.0 # an at-arm line quieter than this is left alone (hum/whine/rumble class)
    arm_fast_level_db: float = -20.0     # an at-arm line at least this loud is emitted at K1 (250 ms) ...
    arm_confirm_s: float = 0.8           # ... a quieter one once it has lasted this long (outlasts a melody note)
    arm_range_frames: int = 16           # ... and only while its level has stayed within arm_range_db over these frames
    arm_range_db: float = 6.0            #     (an established howl is flat; a kick/bass pattern present at arm pumps)
    arm_presence: float = 0.8            # ... and present on >= 80 % of the frames since arm (a re-triggered drum is not)
    # -- frequency window (P6) -----------------------------------------------------------------
    window_low_hz_watch: float = 160.0
    window_low_hz_ringout: float = 63.0
    window_low_hz_lf: float = 40.0
    window_high_hz: float = 12500.0
    lf_feedback_possible: bool = False
    # -- evidence ------------------------------------------------------------------------------
    strong_prominence_db: float = 18.0   # AT-ARM evidence needs this median prominence
    rise_db: float = 6.0                 # RISE evidence: net rise since the analyser settled
    onset_step_db: float = 4.0           # a single-frame jump larger than this out of a flat line (previous step <
    onset_flat_db: float = 1.0           # onset_flat_db) is an onset landing on the track, not growth: the run restarts
    lf_onset_step_db: float = 6.0        # LF: the same, measured over one analyser-settle window (per-frame jumps are smeared):
    lf_onset_flat_db: float = 2.0        #     a rise > 6 dB within one window out of a window that moved < 2 dB is an onset
    onset_rising_db_per_s: float = 10.0  # a line whose recent settled samples climb at least this fast ...
    onset_rising_resid_db: float = 0.75  # ... and this cleanly was 'already climbing': a jump then is not an onset
    rise_quantile: float = 0.2           # RISE reference = this quantile of the run's settled levels
    restart_drop_db: float = 6.0         # a fall this far under the run's maximum ends the run (next rise = re-excitation)
    growth_frames: int = 8               # GROWTH evidence (the optional early upgrade): over this many settled frames ...
    growth_min_db_per_s: float = 10.0    # ... a least-squares slope of at least this (no upper bound) ...
    growth_resid_db: float = 1.0         # ... with rms residual about the dB-linear fit no larger than this ...
    growth_rise_db: float = 6.0          # ... and a net rise of at least this. = rise_db means OFF (default): at 4.0 it buys
                                         #     100-200 ms on 10-15 dB/s rings but fired on a 10 dB/s pad-swell partial on a
                                         #     hold-out seed (programme margin too thin: whistle swell 6.2 dB/s at 3.1 dB)
    analyser_rise_k: float = 1.0         # analyser settle time = k/Δf (uncertainty bound; U-model = 1.0)
    loud_line_db: float = -10.0          # LOUD evidence: peak at least this (loop brief §4.3 tier A) ...
    loud_margin_db: float = 4.0          # ... AND this far above every band outside its own ±k neighbourhood
    clip_db: float = -1.0                # ... or at the analyser's clip flag (0.0 = 'clipping occurred', meters.md §4.2)
    probe_over_db: float = 2.0           # PROBE: response ≥ step + this
    probe_settle_s: float = 0.15         # ignore this long after the step (LF bands lag)
    probe_window_s: float = 1.4          # judge within this after the step (< ring_out dwell)
    probe_early_hits: int = 2            # ring_out: over-responses to this many steps ⇒ pre-emptive (sub-threshold) emit
    probe_min_hits: int = 2              # PROBE evidence needs this many over-responding steps
    probe_steady_db: float = 3.0         # the line must be steady (range <= this) before a step to judge it (2x after)
    mode: str = "watch"                  # 'watch' | 'ringout'
    ringout_emit_moderate: bool = False  # ring_out: MODERATE emits after K2 when no step could test it. OFF: only safe
                                         # in a verified programme-free room (226 FP on the music corpus when ON)
    # -- emission / planner (unchanged semantics) ----------------------------------------------
    confidence_threshold: float = 0.7    # emitted detections report confidence ≥ this (display contract)
    band_tolerance: int = 1              # cooldown radius (RTA bands); cfs._band_level uses it too
    cooldown_s: float = 1.0              # s between emissions for the same line
    notch_step_db: float = -3.0          # per detection (negative)
    notch_max_db: float = -9.0           # deepest cut (negative, <= notch_step_db)
    notch_budget_default: int = 6        # distinct GEQ bands per session
    merge_adjacent_bands: int = 1        # GEQ bands: a detection this close to a notch deepens it
    decay_verify_db: float = 6.0         # used by cfs.py (VERIFY stage)
    decay_verify_s: float = 1.5          # used by cfs.py
    decay_verify_frames: int = 2         # consecutive frames that must show the drop (cfs.py, extension)
    frame_period_s: float = 0.05         # nominal RTA frame period (informational)
    level_gate_db: float = -128.0        # optional absolute gate (OFF): P5's per-band baseline replaces min_level_db
    # -- legacy keys (accepted from device.yaml so the current file loads; NOT used by the decision) ----------
    min_level_db: float = -45.0          # was the absolute candidate gate fitted to one room (HANDOVER §4b(3))
    persistence_frames: int = 3
    growth_min_db_per_s: float = 6.0
    growth_ref_db_per_s: float = 20.0
    growth_max_db_per_s: float = 60.0
    monotonic_tolerance_db: float = 1.0
    w_prominence: float = 0.3
    w_persistence: float = 0.2
    w_growth: float = 0.5
    growth_window_frames: int = 60
    override_prominence_db: float = 25.0
    override_persistence_frames: int = 6

    def __post_init__(self) -> None:
        def need(cond: bool, msg: str) -> None:
            if not cond:
                raise ValueError(f"DetectorConfig: {msg}")

        need(self.prominence_db > 0, "prominence_db must be > 0")
        need(0 < self.track_prominence_db <= self.prominence_db, "track_prominence_db must be in (0, prominence_db]")
        need(self.neighbour_bins >= 1, "neighbour_bins must be >= 1")
        need(self.narrow_db >= 0, "narrow_db must be >= 0")
        need(self.cluster_merge_db >= 0, "cluster_merge_db must be >= 0")
        need(self.track_match_bands > 0, "track_match_bands must be > 0")
        need(self.coast_frames >= 0, "coast_frames must be >= 0")
        need(self.partial_prominence_db > 0, "partial_prominence_db must be > 0")
        need(self.family_min_partials >= 1, "family_min_partials must be >= 1")
        need(0.0 < self.family_veto_fraction <= 1.0, "family_veto_fraction must be in (0, 1]")
        need(self.stable_frames >= 2, "stable_frames must be >= 2")
        need(self.confirm_frames >= self.stable_frames, "confirm_frames must be >= stable_frames")
        need(self.centroid_tol_bands > 0, "centroid_tol_bands must be > 0")
        need(self.drift_max_bands > 0, "drift_max_bands must be > 0")
        need(self.decay_db_per_s > 0 and self.decay_drop_db > 0, "decay thresholds must be > 0")
        need(self.decay_window_frames >= 3, "decay_window_frames must be >= 3")
        need(self.sag_db > 0, "sag_db must be > 0")
        need(0 <= self.ref_band_lo < self.ref_band_hi, "ref band range invalid")
        need(self.baseline_excess_db >= 0, "baseline_excess_db must be >= 0")
        need(self.arm_frames >= 1, "arm_frames must be >= 1")
        need(0 < self.window_low_hz_lf <= self.window_low_hz_ringout <= self.window_low_hz_watch < self.window_high_hz,
             "frequency window edges must be ordered lf <= ringout <= watch < high")
        need(self.rise_db > 0, "rise_db must be > 0")
        need(self.analyser_rise_k >= 0, "analyser_rise_k must be >= 0")
        need(self.mode in _MODES, f"mode must be one of {_MODES}")
        need(0 < self.confidence_threshold <= 1.0, "confidence_threshold must be in (0, 1]")
        need(self.band_tolerance >= 0, "band_tolerance must be >= 0")
        need(self.cooldown_s >= 0, "cooldown_s must be >= 0")
        need(self.notch_step_db < 0, "notch_step_db must be negative (cuts only)")
        need(self.notch_max_db <= self.notch_step_db, "notch_max_db must be <= notch_step_db")
        need(self.notch_budget_default >= 0, "notch_budget_default must be >= 0")
        need(self.decay_verify_frames >= 1, "decay_verify_frames must be >= 1")
        need(self.merge_adjacent_bands >= 0, "merge_adjacent_bands must be >= 0")
        need(min(self.w_prominence, self.w_persistence, self.w_growth) >= 0, "weights must be >= 0")
        need(self.persistence_frames >= 1, "persistence_frames must be >= 1")

    @property
    def weights(self) -> dict[str, float]:
        return {"prominence": self.w_prominence, "persistence": self.w_persistence, "growth": self.w_growth}

    @property
    def window_low_hz(self) -> float:
        """Effective low edge of the feedback window for this mode / LF declaration (P6)."""
        if self.lf_feedback_possible:
            return self.window_low_hz_lf
        return self.window_low_hz_ringout if self.mode == "ringout" else self.window_low_hz_watch

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
            typ = str(known[key])
            if typ == "int":
                kw[key] = int(value)
            elif typ == "bool":
                kw[key] = value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")
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
# detector
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Detection:
    """One feedback verdict. ``band`` is the 0-based RTA peak band, ``freq_hz`` the interpolated line frequency,
    ``reasons`` the predicates/evidence that justified it (for the notch report), ``klass`` STRONG/MODERATE."""

    ts: float
    band: int
    freq_hz: float
    level_db: float
    prominence_db: float
    slope_db_per_s: float
    frames: int
    confidence: float
    reasons: tuple[str, ...] = ()
    klass: str = "STRONG"
    centroid_band: float = 0.0
    narrow_db: float = 0.0
    rise_db: float = 0.0
    excess_db: float = 0.0

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
            "class": self.klass,
            "centroid_band": round(self.centroid_band, 2),
            "narrow_db": round(self.narrow_db, 1),
            "rise_db": round(self.rise_db, 1),
            "excess_db": round(self.excess_db, 1),
        }


@dataclass
class _Line:
    """One local maximum found in a frame (private)."""
    band: int
    lo: int
    hi: int
    peak_db: float
    cluster_db: float      # power sum of band-1..band+1 (dB)
    centroid: float        # log-frequency centroid (band units) over band-1..band+1
    prom_db: float         # single-band prominence (median of ±k)
    prom_c_db: float       # cluster prominence (cluster power over the ±2..±(k+1) median outside it)
    narrow_db: float       # peak − max(level at ±2 outside the cluster)
    partials: int = 0      # H2..H5 present this frame
    is_harmonic: bool = False
    vetoed: bool = False
    partial_names: tuple[str, ...] = ()
    track: "Candidate | None" = None


@dataclass
class Candidate:
    """A tracked line. Histories are bounded deques; ``frames`` counts matched frames since ``first_ts``.
    ``klass`` is TRACK (a line, not qualified) / MUSICAL / STATIONARY / MODERATE / STRONG; ``reasons`` lists the
    predicates that hold on the latest frame. Treat as read-only outside the detector."""

    band: int
    first_ts: float
    frames: int = 0
    confidence: float = 0.0
    freq_hz: float = 0.0
    level_db: float = -128.0
    prominence_db: float = 0.0
    slope_db_per_s: float = 0.0
    last_ts: float = 0.0
    emitted: int = 0
    centroid: float = 0.0
    first_centroid: float = 0.0
    first_frame: int = 0
    born_at_arm: bool = False
    arm_evidence: bool = False     # AT-ARM evidence, evaluated once over the track's first K1 frames
    narrow_db: float = 0.0
    cluster_db: float = -128.0
    excess_db: float = 0.0
    rise_db: float = 0.0
    veto_frames: int = 0
    glided: bool = False           # this track was (re)born from a line that walked > drift_max_bands (P3)
    common_mode: bool = False
    stationary: bool = False       # ring_out: answered a probe step like programme (≤ Δ + 1 dB)
    probe_hits: int = 0
    probe_linear: int = 0
    klass: str = "TRACK"
    reasons: tuple[str, ...] = ()
    misses: int = 0                # consecutive frames without the line (coasting)
    settle_frames: int = 1         # frames of a presence run before rises count (analyser settle)
    run_frames: int = 0            # length of the current continuous-presence run (restarts on a miss or an onset jump)
    run_lv: deque = field(default_factory=lambda: deque(maxlen=64))  # (cluster_db, frame index) of the run after the analyser settled
    prev_cluster_db: float | None = None
    prev_inc_db: float = 0.0
    prev_ts: float | None = None
    rise_streak: int = 0           # consecutive matched frames (no gap) on which the level rose >= onset_flat_db
    run_max_db: float = -128.0     # highest cluster level of the current run
    rise_old_db: float = 0.0       # the part of rise_db already present one analyser-settle time ago
    growth_db_per_s: float = 0.0   # clean dB-linear slope over the last growth_frames settled frames (0 if not clean)
    confirmed: bool = False        # emitted once: stays emit-eligible while BASE holds (so a surviving ring keeps deepening)
    base_streak: int = 0           # consecutive frames on which BASE held
    first_veto_rise: float | None = None   # RISE accumulated when the family veto first fired (late partials test)
    last_emit_ts: float | None = None
    steps_seen: int = 0
    judged_steps: set = field(default_factory=set)
    hist: deque = field(default_factory=lambda: deque(maxlen=64))   # (ts, cluster_db, peak_db, centroid, ref, qualified, vetoed)
    proms: deque = field(default_factory=lambda: deque(maxlen=64))  # cluster prominence per matched frame

    def to_dict(self) -> dict[str, Any]:
        return {
            "band": self.band,
            "freq_hz": round(self.freq_hz, 1),
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "frames": self.frames,
            "level_db": round(self.level_db, 2),
            "prominence_db": round(self.prominence_db, 2),
            "slope_db_per_s": round(self.slope_db_per_s, 2),
            "growth_score": 0.0,
            "confidence": round(self.confidence, 3),
            "emitted": self.emitted,
            "centroid_band": round(self.centroid, 2),
            "narrow_db": round(self.narrow_db, 1),
            "rise_db": round(self.rise_db, 1),
            "excess_db": round(self.excess_db, 1),
            "class": self.klass,
            "reasons": list(self.reasons),
            "born_at_arm": self.born_at_arm,
            "glided_in": self.glided,
            "probe_hits": self.probe_hits,
            "stationary": self.stationary,
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


def _db(p: float) -> float:
    return 10.0 * math.log10(p) if p > 0 else -128.0


class FeedbackDetector:
    """Frame-by-frame feedback discriminator over an RTA stream (see module docstring for the predicates).

    Optional API used by ``cfs.py`` (all no-ops if never called): ``mode`` ('watch'|'ringout', from
    ``cfg.mode`` or the constructor), :meth:`note_gain_step` (ring_out tells the detector when it stepped
    the master: the active probe), :meth:`note_cut` (a GEQ cut landed; informational)."""

    def __init__(self, cfg: DetectorConfig, band_hz: Sequence[float], *, mode: str | None = None,
                 lf_feedback_possible: bool | None = None) -> None:
        if mode is not None or lf_feedback_possible is not None:
            from dataclasses import replace
            kw: dict[str, Any] = {}
            if mode is not None:
                kw["mode"] = mode
            if lf_feedback_possible is not None:
                kw["lf_feedback_possible"] = bool(lf_feedback_possible)
            cfg = replace(cfg, **kw)
        self.cfg = cfg
        self.band_hz: tuple[float, ...] = tuple(float(h) for h in band_hz)
        n = len(self.band_hz)
        if n < 2 * cfg.neighbour_bins + 1:
            raise ValueError("band_hz too short for neighbour_bins")
        if any(h <= 0 for h in self.band_hz):
            raise ValueError("band_hz must be positive frequencies")
        self._log_hz = [math.log(h) for h in self.band_hz]
        # analyser settle allowance per band, in frames: 1.5 · k/Δf — the band filter's own step response must
        # never be scored as a rise [L §1.6, A §1]
        # (1 frame above ~300 Hz -- never 0: the birth frame of a soft 2-3-frame acoustic attack (flute, bowed,
        # sung: 20-100 ms [A §4.2]) must not seed the rise reference -- 2 at 200 Hz, 4 at 100 Hz, 7 at 63 Hz,
        # 11 at 39 Hz for k = 1)
        self._settle_frames = [
            max(1, int(math.ceil(1.5 * cfg.analyser_rise_k / max(1e-6, _BANDWIDTH_FRACTION * h) / cfg.frame_period_s - 0.5)))
            for h in self.band_hz
        ]
        self._cands: list[Candidate] = []
        self._cooldown: dict[int, float] = {}
        self._base: list[float] | None = None
        self._steps: list[tuple[float, float]] = []          # (ts, delta_db) from note_gain_step
        self._cuts: list[tuple[float, float, float]] = []    # (ts, hz, depth)
        self._arm_lines: list[tuple[float, float]] = []      # (centroid, peak_db) of lines present at arm
        self.frames_seen: int = 0
        self.first_ts: float | None = None
        self.last_ts: float | None = None
        self.ref_db: float = -128.0
        self._sorted_levels: list[tuple[float, int]] = []
        self._n_growing: int = 0
        self._spec: deque = deque(maxlen=96)                   # (frame index, levels, floor) for the common-mode reference

    # -- optional hooks ------------------------------------------------------------------------
    @property
    def mode(self) -> str:
        return self.cfg.mode

    def note_gain_step(self, delta_db: float, ts: float) -> None:
        """ring_out: the server stepped the bus master by ``delta_db`` at ``ts`` (its own, timed write). Lines
        that answer with more than ``delta_db + probe_over_db`` within the dwell are loop-gain dependent."""
        self._steps.append((float(ts), float(delta_db)))
        if len(self._steps) > 32:
            del self._steps[0]

    def note_cut(self, freq_hz: float, depth_db: float, ts: float) -> None:
        """A GEQ cut of ``depth_db`` at ``freq_hz`` landed at ``ts`` (informational; reserved for VERIFY-aware
        re-emission policies)."""
        self._cuts.append((float(ts), float(freq_hz), float(depth_db)))
        if len(self._cuts) > 32:
            del self._cuts[0]

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

    def freq_of(self, c: float) -> float:
        """Interpolated frequency (Hz) of fractional band coordinate ``c`` (log-linear between centres)."""
        n = len(self.band_hz)
        if c <= 0:
            return self.band_hz[0] * math.exp((self._log_hz[1] - self._log_hz[0]) * c)
        if c >= n - 1:
            return self.band_hz[-1] * math.exp((self._log_hz[-1] - self._log_hz[-2]) * (c - (n - 1)))
        i = int(math.floor(c))
        f = c - i
        return math.exp(self._log_hz[i] * (1.0 - f) + self._log_hz[i + 1] * f)

    def _cm(self, fi_a: int, fi_b: int, band: int) -> float:
        """Common-mode level change between frames ``fi_a`` and ``fi_b`` as seen by the bands that carry signal
        (bands ref_band_lo..ref_band_hi, above the frame floor + ref_signal_db at both frames, outside ``band``'s
        own neighbourhood): the MEDIAN of their individual changes. A fader / autogain / master step moves every
        signal band by the same amount; a chord change, a cymbal or one growing line moves a minority. [A §5]"""
        if fi_a == fi_b or not self._spec:
            return 0.0
        first = self._spec[0][0]
        ia, ib = fi_a - first, fi_b - first
        if ia < 0 or ib < 0 or ia >= len(self._spec) or ib >= len(self._spec):
            return 0.0
        _, va, fa = self._spec[ia]
        _, vb, fb = self._spec[ib]
        cfg = self.cfg
        k = cfg.neighbour_bins + 1
        thr_a, thr_b = fa + cfg.ref_signal_db, fb + cfg.ref_signal_db
        diffs = [vb[i] - va[i] for i in range(cfg.ref_band_lo, min(len(vb), cfg.ref_band_hi + 1))
                 if abs(i - band) > k and va[i] > thr_a and vb[i] > thr_b]
        if len(diffs) < cfg.ref_min_bands:
            return 0.0
        return median(diffs)

    def _others_max(self, band: int) -> float:
        """Loudest band level outside ``band``'s own ±neighbour_bins neighbourhood on the current frame."""
        k = self.cfg.neighbour_bins
        for v, i in self._sorted_levels:
            if abs(i - band) > k:
                return v
        return -128.0

    def _loud(self, peak_db: float, band: int) -> bool:
        """LOUD evidence: at the clip flag, or >= loud_line_db AND loud_margin_db above every other band."""
        cfg = self.cfg
        return peak_db >= cfg.clip_db or (peak_db >= cfg.loud_line_db and peak_db >= self._others_max(band) + cfg.loud_margin_db)

    def _in_cooldown(self, band: int, ts: float) -> bool:
        tol = self.cfg.band_tolerance
        for b in range(band - tol, band + tol + 1):
            until = self._cooldown.get(b)
            if until is not None and ts < until:
                return True
        return False

    def _lines(self, vals: Sequence[float], prom: Sequence[float]) -> list[_Line]:
        """Local maxima with prom ≥ track_prominence_db, with cluster/centroid/narrowness/cluster prominence."""
        cfg = self.cfg
        n = len(vals)
        k = cfg.neighbour_bins
        out: list[_Line] = []
        for i in range(2, n - 2):
            v = vals[i]
            if prom[i] < cfg.track_prominence_db:
                continue
            if not (v > vals[i - 1] and v >= vals[i + 1]):
                continue                      # local maximum (the lower band wins an exact tie: split line)
            lo = i - 1 if vals[i - 1] >= v - cfg.cluster_merge_db else i
            hi = i + 1 if vals[i + 1] >= v - cfg.cluster_merge_db else i
            jl, jr = lo - 2, hi + 2
            outside = [vals[j] for j in (jl, jr) if 0 <= j < n]
            narrow = v - max(outside) if outside else 0.0
            neigh = [vals[j] for d in range(2, k + 2) for j in (lo - d, hi + d) if 0 <= j < n]
            floor_db = median(neigh) if neigh else v - prom[i]
            pc = sum(_pw(vals[j]) for j in range(lo, hi + 1))
            prom_c = _db(pc) - floor_db
            p_l, p_c, p_r = _pw(vals[i - 1]), _pw(v), _pw(vals[i + 1])
            ptot = p_l + p_c + p_r
            # centroid of the line's OWN power: the local floor (median of the bands beyond the skirts) is removed
            # from each of the three bands first, so a weak line's position is not dragged about by the bed
            pf = _pw(floor_db)
            q_l, q_c, q_r = max(0.0, p_l - pf), max(0.0, p_c - pf), max(0.0, p_r - pf)
            qtot = q_l + q_c + q_r
            centroid = ((i - 1) * q_l + i * q_c + (i + 1) * q_r) / qtot if qtot > 0 else float(i)
            out.append(_Line(band=i, lo=lo, hi=hi, peak_db=v, cluster_db=_db(ptot), centroid=centroid,
                             prom_db=prom[i], prom_c_db=max(prom[i], prom_c), narrow_db=narrow))
        return out

    def _family(self, lines: list[_Line]) -> None:
        """P2 per line (tracks already attached): count partials H2..H5 present as lines near centroid+offset;
        flag lines that are themselves H2/H3 of a lower line owning another partial; set ``vetoed``."""
        cfg = self.cfg
        tol = cfg.partial_tol_bands

        def present_near(x: float) -> _Line | None:
            best = None
            bd = tol + 1e-9
            for ln in lines:
                d = abs(ln.centroid - x)
                if d <= bd:
                    best, bd = ln, d
            return best

        def co_onset(a: _Line, b: _Line) -> bool:
            ta, tb = a.track, b.track
            if ta is None or tb is None:
                return False
            if ta.born_at_arm and tb.born_at_arm:
                return True
            return abs(ta.first_frame - tb.first_frame) <= cfg.co_onset_frames

        K1 = cfg.stable_frames

        def recent_move(a: _Line) -> float | None:
            t = a.track
            if t is None or len(t.hist) < K1 or t.misses:
                return None
            return (a.cluster_db - t.hist[-K1][1]) - 0.0

        def co_moving(a: _Line, b: _Line) -> bool:
            # partials of one source share its envelope; an independent line (a ring growing through a held
            # note's harmonic position, a syllable landing on a ring's harmonic position) does not
            # [L §5 P1 'co-moving']. Without K1 frames of history on both sides the benefit of the doubt goes
            # to 'co-moving' (a pad swell's partials surface one after another; treating newcomers as
            # independent lets swells and speech through -- measured: +13 FP on S21/S22/X9/X18).
            da, db = recent_move(a), recent_move(b)
            if da is None or db is None:
                return True
            return abs(da - db) <= cfg.comove_tol_db

        for ln in lines:
            names: list[str] = []
            cnt = 0
            # a partial counts when it is itself a peak (>= partial_prominence_db) whose LEVEL is within partial_rel_db
            # of the candidate's (instrument partials run 0..-15 dB re H1 [L §2.1]; a coincidental programme peak far
            # below a strong ring does not count; prominence-relative was tried and flickers inside chords, where a
            # partial's neighbours are other partials)
            need_lv = ln.peak_db - cfg.partial_rel_db
            h2: _Line | None = None
            for kk, off in _PARTIAL_OFFSETS:
                hit = present_near(ln.centroid + off)
                if (hit is not None and hit is not ln and hit.prom_db >= cfg.partial_prominence_db
                        and hit.peak_db >= need_lv and co_moving(ln, hit)):
                    cnt += 1
                    names.append(f"H{kk}")
                    if kk == 2:
                        h2 = hit
            ln.partials = cnt
            family = cnt >= cfg.family_min_partials
            # A single exact-octave partner is a family only when the two lines were born together (an 8'+4'
            # organ registration, flute/clean-guitar H1+H2): two independent rings an octave apart do not
            # co-onset [L §2.3], so X10-type coincidences are not dismissed.
            if not family and h2 is not None and h2.peak_db >= ln.peak_db - cfg.h2_pair_rel_db and co_onset(ln, h2):
                family = True
                names.append("H2pair")
            harm = False
            for kk, off in _PARTIAL_OFFSETS:               # am I somebody's H2..H5?
                base = present_near(ln.centroid - off)
                if base is None or base is ln or base.prom_db < cfg.partial_prominence_db or not co_moving(ln, base):
                    continue
                if kk >= 4 and base.peak_db < ln.peak_db + cfg.subharm_hi_margin_db:
                    # H4/H5 only under a base that is louder (a note's fundamental/formant region, a clipped howl
                    # under its own H5); testing H4/H5 under ANY base -- or under a base owning two partials -- costs
                    # coincidence vetoes on real rings under music (measured: X7/M1/X8/X11 +0.3..1.6 s and a miss)
                    continue
                for mm, off_m in _PARTIAL_OFFSETS:
                    if mm == kk:
                        continue
                    other = present_near(base.centroid + off_m)
                    if other is not None and other is not ln and other is not base and other.prom_db >= cfg.partial_prominence_db:
                        harm = True
                        names.append(f"isH{kk}of{base.band}")
                        break
                if not harm and kk == 2 and base.peak_db >= ln.peak_db - cfg.h2_pair_rel_db and co_onset(ln, base):
                    harm = True                              # the upper member of a co-born octave pair
                    names.append(f"H1pair{base.band}")
                if harm:
                    break
            ln.is_harmonic = harm
            # escape hatch for the line's OWN family: a line that is LOUD (at the clip flag, or >= loud_line_db and
            # loud_margin_db above every band outside its neighbourhood -- which already implies its 'partials' are
            # far below it) keeps no family veto: a howl driving an amp/speaker into clipping grows harmonics the
            # mic hears while the desk reads well under 0 dBFS [L §1.4(1), §2.2]. Being somebody's harmonic (harm)
            # has no escape: a clipped howl's own H3/H5 must never be cut.
            ln.vetoed = harm or (family and not self._loud(ln.peak_db, ln.band))
            ln.partial_names = tuple(names)

    # -- per-track bookkeeping -----------------------------------------------------------------
    def _new_track(self, ln: _Line, ts: float) -> Candidate:
        cfg = self.cfg
        c = Candidate(band=ln.band, first_ts=ts)
        c.first_centroid = ln.centroid
        c.centroid = ln.centroid
        c.first_frame = self.frames_seen
        c.settle_frames = self._settle_frames[ln.band]
        if self.frames_seen < cfg.arm_frames:
            c.born_at_arm = True
            if ln.prom_c_db >= cfg.prominence_db:
                self._arm_lines.append((ln.centroid, ln.peak_db))
        else:
            # a prominent line that was there at arm and lost its track for a moment (masked by a transient) is
            # still the same at-arm line when it comes back at the same place and the same level
            c.born_at_arm = any(abs(ln.centroid - ac) <= cfg.centroid_tol_bands and abs(ln.peak_db - adb) <= 6.0
                                for ac, adb in self._arm_lines)
        return c

    def _extend(self, c: Candidate, ln: _Line, ts: float, ref: int, base: list[float]) -> None:
        cfg = self.cfg
        c.frames += 1
        c.misses = 0
        c.band = ln.band
        c.centroid = ln.centroid
        c.freq_hz = self.freq_of(ln.centroid)
        c.level_db = ln.peak_db
        c.cluster_db = ln.cluster_db
        c.prominence_db = ln.prom_c_db
        c.narrow_db = ln.narrow_db
        c.excess_db = ln.peak_db - base[ln.band]
        c.last_ts = ts
        qualified = ln.prom_c_db >= cfg.prominence_db and ln.narrow_db >= cfg.narrow_db
        c.proms.append(ln.prom_c_db)
        # -- RISE bookkeeping over the continuous-presence run ----------------------------------------------
        c.run_frames += 1
        inc = 0.0 if c.prev_cluster_db is None else ln.cluster_db - c.prev_cluster_db
        gap = max(1, int(round((ts - c.prev_ts) / cfg.frame_period_s))) if c.prev_ts is not None else 1
        # was the line already climbing before this frame? two consecutive rising frames just before, or a clean
        # climb over the last settled samples (a ring that kept growing while a transient masked it). A single
        # previous increment is not enough: it may be a coast re-acquisition on noise.
        rising_before = gap == 1 and c.rise_streak >= 2
        if not rising_before and len(c.run_lv) >= 4:
            tail = list(c.run_lv)[-6:]
            # only a CONTIGUOUS stretch of settled samples counts (coast re-acquisitions on noise are not a climb)
            k0 = len(tail) - 1
            while k0 > 0 and tail[k0][1] - tail[k0 - 1][1] == 1:
                k0 -= 1
            tail = tail[k0:]
            if len(tail) >= 4:
                tail_lv = [x[0] for x in tail]
                xs = [(x[1] - tail[0][1]) * cfg.frame_period_s for x in tail]
                sl = _ls_slope(xs, tail_lv)
                m = sum(tail_lv) / len(tail_lv)
                mx = sum(xs) / len(xs)
                resid = math.sqrt(sum((y - (m + sl * (x - mx))) ** 2 for x, y in zip(xs, tail_lv)) / len(tail_lv))
                rising_before = sl >= cfg.onset_rising_db_per_s and resid <= cfg.onset_rising_resid_db
        if c.run_frames > 1 and not rising_before and (inc > cfg.onset_step_db or gap > cfg.stable_frames):
            # A jump out of a flat (or falling) line within ONE frame is an onset landing on the track (a note
            # re-struck at the same pitch, a syllable, a note arriving on a noise bump): a new source, so the
            # presence run restarts and the analyser-settle allowance applies again (the partially integrated
            # remainder frame is not scored). Loop growth is continuous and loses at most its first frame. The same
            # after the line was absent for more than K1 frames without having been climbing: whatever comes back
            # must re-earn its rise. [L §1.3]
            c.run_frames = 1
            c.run_lv.clear()
            c.run_max_db = ln.cluster_db
        elif (c.settle_frames >= 2 and len(c.hist) >= 2 * c.settle_frames
              and ln.cluster_db - c.hist[-c.settle_frames][1] > cfg.lf_onset_step_db
              and abs(c.hist[-c.settle_frames][1] - c.hist[-2 * c.settle_frames][1]) < cfg.lf_onset_flat_db):
            # LF version of the onset jump: below ~300 Hz an instant onset is smeared over settle_frames, so the
            # jump is measured over that window (a note: >= 8-10 dB within one settle time out of a flat line; an
            # LF loop, long and low-excess, grows <= ~5 dB per settle time and was already rising before).
            c.run_frames = 1
            c.run_lv.clear()
            c.run_max_db = ln.cluster_db
        elif c.settle_frames >= 2 and ln.cluster_db < c.run_max_db - cfg.restart_drop_db:
            # The line has fallen well below its own recent maximum: whatever rises next is a RE-excitation (a
            # driven room mode or a re-struck note seen through a slow LF band: the analyser's step response
            # would otherwise read as growth), so the run restarts and must settle again. Only where the band is
            # slow (settle >= 2 frames, below ~300 Hz): higher up the analyser is instant and the onset-jump rule
            # above already separates re-struck sources from continuous growth. [A §6, L §1.6]
            c.run_frames = 1
            c.run_lv.clear()
            c.run_max_db = ln.cluster_db
        c.run_max_db = max(c.run_max_db, ln.cluster_db)
        c.rise_streak = (c.rise_streak + 1 if gap == 1 else 1) if (c.prev_cluster_db is not None and inc >= cfg.onset_flat_db) else 0
        c.prev_cluster_db = ln.cluster_db
        c.prev_inc_db = inc
        c.prev_ts = ts
        if c.run_frames > c.settle_frames:
            c.run_lv.append((ln.cluster_db, ref))
        rise = 0.0
        nlv = len(c.run_lv)
        if nlv >= 2:
            # reference = a LOW QUANTILE of the run's settled levels (not the minimum: on a flat line with ±2 dB
            # flutter the minimum keeps falling and would manufacture a 'rise'), net of any common-mode raise
            srt = sorted(c.run_lv)
            lv0, fi0 = srt[int(cfg.rise_quantile * (nlv - 1))]
            # net of any common-mode raise beyond cm_deadband_db (a mix breathes +-1..2 dB; a fader/autogain move
            # that could fake a 6 dB rise is larger than that and is subtracted in full beyond the dead band)
            rise = (ln.cluster_db - lv0) - max(0.0, self._cm(fi0, ref, ln.band) - cfg.cm_deadband_db)
            # the part of the rise that is older than one analyser-settle time: a programme onset seen through a
            # slow LF band is a rise confined to ~settle_frames; loop growth was already under way before that
            # and has continued since [L §1.6]. (At HF settle = 1 frame: costs nothing.)
            k_old = c.settle_frames + 1
            if nlv > k_old:
                lv1, fi1 = c.run_lv[-1 - k_old]
                c.rise_old_db = (lv1 - lv0) - max(0.0, self._cm(fi0, fi1, ln.band) - cfg.cm_deadband_db)
            else:
                c.rise_old_db = 0.0
        c.rise_db = rise
        # GROWTH: a clean dB-linear climb over the last growth_frames settled frames (slope >= growth_min_db_per_s,
        # fit residual <= growth_resid_db) -- exponential loop growth is exactly linear in dB [L §1.3]; no upper
        # rate bound, and LF-safe because the run only starts after the analyser settle allowance.
        c.growth_db_per_s = 0.0
        W = cfg.growth_frames
        if nlv >= W:
            fiW = c.run_lv[-W][1]
            ys = [x[0] - max(0.0, self._cm(fiW, x[1], ln.band)) for x in list(c.run_lv)[-W:]]
            xs = [j * cfg.frame_period_s for j in range(W)]
            sl = _ls_slope(xs, ys)
            my = sum(ys) / W
            mx = xs[-1] / 2.0
            resid = math.sqrt(sum((y - (my + sl * (x - mx))) ** 2 for x, y in zip(xs, ys)) / W)
            if resid <= cfg.growth_resid_db:
                c.growth_db_per_s = sl
        # -- family veto (P2) with the late-partials exemption ---------------------------------------------
        counted = False
        if ln.vetoed:
            # Instrument partials onset WITH (or, through the analyser at LF, before) their fundamental; distortion
            # partials of a howl appear only after the fundamental has grown loud [A §4.3]. A veto that first fires
            # after the line has already risen late_partials_rise_db does not count.
            if c.first_veto_rise is None:
                c.first_veto_rise = rise
            if c.first_veto_rise < cfg.late_partials_rise_db:
                c.veto_frames += 1
                counted = True
        c.hist.append((ts, ln.cluster_db, ln.peak_db, ln.centroid, ref, qualified, counted))
        # -- P3 over the drift window: a line that walked more than drift_max_bands is a NEW line ------------
        # (glide, portamento, scoop, melody step, or a hand-held ring hopping to the next loop candidate): the
        # track is re-born here with no history, so everything (K1 stability, rise, at-arm) must be re-earned
        # at the new frequency, and it remembers that it arrived by gliding. [L §2.3, A §4.3 vi]
        dc = [h[3] for h in list(c.hist)[-cfg.drift_window_frames:]]
        if len(dc) >= 2 and (max(dc) - min(dc)) > cfg.drift_max_bands:
            self._rebirth(c, ts)
            c.glided = True
        # -- AT-ARM evidence: judged once, on what the line looked like when we armed --------------------
        K1 = cfg.stable_frames
        if c.born_at_arm and c.frames == K1:
            lv = sorted(h[2] for h in c.hist)[len(c.hist) // 2]
            pm = sorted(c.proms)[len(c.proms) // 2]
            c.arm_evidence = lv >= cfg.arm_line_min_level_db and pm >= cfg.strong_prominence_db

    def _rebirth(self, c: Candidate, ts: float) -> None:
        """Restart ``c`` as a fresh track at its current position (keeps only the latest history entry)."""
        last = c.hist[-1]
        lastp = c.proms[-1]
        c.hist.clear()
        c.hist.append(last)
        c.proms.clear()
        c.proms.append(lastp)
        c.frames = 1
        c.first_ts = ts
        c.first_frame = self.frames_seen
        c.first_centroid = c.centroid
        c.born_at_arm = False
        c.arm_evidence = False
        c.confirmed = False
        c.veto_frames = 1 if last[6] else 0
        c.first_veto_rise = 0.0 if last[6] else None
        c.run_frames = 1
        c.run_lv.clear()
        c.run_max_db = last[1]
        c.rise_db = 0.0
        c.growth_db_per_s = 0.0
        c.base_streak = 0
        c.probe_hits = 0
        c.probe_linear = 0
        c.stationary = False
        c.judged_steps = set()
        c.steps_seen = 0
        c.settle_frames = self._settle_frames[c.band]

    def _miss(self, c: Candidate) -> None:
        # The line is not a local maximum this frame (masked by a transient, or gone). The track coasts for
        # coast_frames keeping its run (a hat hit over a quiet ring must not erase the ring's history); the
        # run ends when the track is dropped. A different source arriving on the track afterwards is an onset
        # jump and restarts the run in _extend.
        c.misses += 1
        c.base_streak = 0
        if c.klass != "MUSICAL":
            c.klass = "TRACK"
        c.confidence = min(c.confidence, self.cfg.confidence_threshold - 1e-6)

    def _probe(self, c: Candidate, ts: float) -> None:
        """Judge each noted gain step once, when its window has data: response = median level after the step
        (settled) − median level over ≤ 0.55 s before it, against the reference's response."""
        cfg = self.cfg
        for k, (t_s, delta) in enumerate(self._steps):
            if k in c.judged_steps:
                continue
            if ts < t_s + cfg.probe_settle_s + 4 * cfg.frame_period_s:
                continue                      # not enough post-step frames yet
            pre = [(lv, fi) for (t, lv, pk, cen, fi, q, v) in c.hist if t_s - 0.55 <= t < t_s]
            post = [(lv, fi) for (t, lv, pk, cen, fi, q, v) in c.hist if t_s + cfg.probe_settle_s <= t <= t_s + cfg.probe_window_s]
            if len(pre) < 3 or len(post) < 4:
                if ts > t_s + cfg.probe_window_s:
                    c.judged_steps.add(k)     # the line was not there around the step: nothing to judge
                continue
            if ts < t_s + cfg.probe_window_s and len(post) < 8:
                continue                      # wait for a fuller window unless it is closing
            c.judged_steps.add(k)
            c.steps_seen += 1
            d_line = median([p[0] for p in post]) - median([p[0] for p in pre])
            d_ref = self._cm(pre[-1][1], post[len(post) // 2][1], c.band)
            pre_levels = [p[0] for p in pre]
            post_levels = [p[0] for p in post]
            if (max(pre_levels) - min(pre_levels)) > cfg.probe_steady_db or (max(post_levels) - min(post_levels)) > 2 * cfg.probe_steady_db:
                continue                      # something else was moving the line around the step: no verdict
            hit = linear = False
            if delta > 0:
                hit = d_line >= delta + cfg.probe_over_db and d_ref <= delta + 0.5
                linear = (not hit) and d_line <= delta + 1.0
            elif delta < 0:
                hit = d_line <= delta - cfg.probe_over_db and d_ref >= delta - 0.5
                linear = (not hit) and d_line >= delta - 1.0
            if hit:
                c.probe_hits += 1
                c.stationary = False
            elif linear:
                c.probe_linear += 1
                # 'stationary' (hum / HVAC / playback: follows the gain 1 dB per dB) only while no step has ever
                # shown an over-response; one noisy flat step must not condemn a line that answered others
                c.stationary = c.probe_hits == 0

    def _judge(self, c: Candidate, ts: float) -> None:
        """Evaluate P1..P6 + evidence on the track's latest frame; set klass, reasons, confidence, slope."""
        cfg = self.cfg
        K1 = cfg.stable_frames
        hl = list(c.hist)
        latest = hl[-1]
        qualified_now = bool(latest[5])
        reasons: list[str] = []
        # -- MUSICAL -------------------------------------------------------------------------------
        musical: list[str] = []
        fam = hl[-cfg.family_window_frames:]
        nveto = sum(1 for h in fam if h[6])
        recent_veto = sum(1 for h in hl[-K1:] if h[6])
        frac = nveto / len(fam) if fam else 0.0
        if ((len(fam) >= 3 and frac >= cfg.family_veto_fraction and (recent_veto > 0 or frac >= 0.7))
                or (len(hl) >= K1 and 2 * recent_veto > K1)):
            # vetoed on >= 40 % of the last 20 frames (and not clean for the whole last K1: a family that has
            # stopped co-moving with the line is forgiven quickly), or on most of the last K1
            musical.append("family")
        if self._n_growing >= cfg.cogrowth_lines + (1 if c.rise_db >= cfg.cogrowth_rise_db else 0):
            musical.append("cogrowth")          # >= 3 lines swelling together: a pad/fade-in/crescendo, not a loop
                                                # (rings start alone; two simultaneous rings remain allowed) [A §5]
        win = hl[-cfg.confirm_frames:]
        c.common_mode = False
        if len(win) >= K1 + 3:
            d_ref = self._cm(win[1][4], win[-2][4], c.band)
            d_lv = median(h[1] for h in win[-3:]) - median(h[1] for h in win[:3])
            if abs(d_ref) >= cfg.common_mode_db and abs(d_lv - d_ref) <= cfg.common_mode_tol_db:
                c.common_mode = True
                musical.append("common_mode")
        # -- P3 stable / P4 sustained ------------------------------------------------------------------
        recent = hl[-K1:]
        cents = [h[3] for h in recent]
        stable = c.run_frames >= K1 and (max(cents) - min(cents)) <= 2.0 * cfg.centroid_tol_bands
        dec = hl[-cfg.decay_window_frames:]
        slope = _ls_slope([h[0] for h in dec], [h[1] for h in dec]) if len(dec) >= 3 else 0.0
        c.slope_db_per_s = slope
        drop = max(h[1] for h in dec) - latest[1]
        decaying = slope <= -cfg.decay_db_per_s and drop >= cfg.decay_drop_db
        sag = max(h[1] for h in win) - latest[1]
        sustained = (not decaying) and sag <= cfg.sag_db
        # -- P5 / P6 -----------------------------------------------------------------------------------
        new_energy = c.excess_db >= cfg.baseline_excess_db
        in_window = cfg.window_low_hz <= c.freq_hz <= cfg.window_high_hz
        narrow = c.narrow_db >= cfg.narrow_db
        level_ok = c.level_db >= cfg.level_gate_db
        if narrow:
            reasons.append("narrow")
        if c.frames >= 3 and not musical:
            reasons.append("no_family")
        if stable:
            reasons.append("stable")
        if sustained and c.run_frames >= K1:
            reasons.append("sustained")
        if new_energy:
            reasons.append("new_energy")
        elif c.born_at_arm:
            reasons.append("at_arm")
        if in_window:
            reasons.append("in_window")
        if c.glided:
            reasons.append("glided_in")
        base_ok = (qualified_now and narrow and not musical and stable and sustained and in_window and level_ok
                   and (new_energy or c.born_at_arm))
        c.base_streak = c.base_streak + 1 if base_ok else 0
        rise = c.rise_db
        evidence: list[str] = []
        lf_ok = c.settle_frames <= 1 or c.rise_old_db >= 0.5 * cfg.rise_db
        if rise >= cfg.rise_db and lf_ok:
            evidence.append(f"rise{rise:.0f}dB")
        elif cfg.growth_rise_db < cfg.rise_db and rise >= cfg.growth_rise_db and c.growth_db_per_s >= cfg.growth_min_db_per_s and lf_ok:
            evidence.append(f"growth{c.growth_db_per_s:.0f}dB/s")     # optional early upgrade (OFF by default)
        if self._loud(c.level_db, c.band) and c.freq_hz >= cfg.window_low_hz_watch:
            # LOUD says nothing below ~160 Hz: LF loops are long (sub DSP + distance >= 15 ms) with small excess,
            # so an LF howl always shows seconds of observable growth, while kick / 808 / toms routinely put a
            # single LF band far above everything else [L §3.2 (e), A §4.2]. LF needs RISE, AT-ARM or PROBE.
            evidence.append("loud")
        armed_for = ts - self.first_ts if self.first_ts is not None else 0.0
        arm_lv = [h[1] for h in hl[-cfg.arm_range_frames:]]
        presence = c.frames / max(1, self.frames_seen - c.first_frame + 1)
        arm_steady = (max(arm_lv) - min(arm_lv)) <= cfg.arm_range_db and presence >= cfg.arm_presence
        if c.arm_evidence and arm_steady and (c.level_db >= cfg.arm_fast_level_db or armed_for >= cfg.arm_confirm_s):
            # an established line found at arm: immediately if loud-ish, otherwise once it has outlasted a note
            # (a steady family-less NOTE that happened to be sounding when we armed looks identical for as long as
            # it lasts; organ/flute melody notes end within ~0.3-1 s, a plateaued ring does not)
            evidence.append("established_at_arm")
        if c.probe_hits >= cfg.probe_min_hits:
            # over-response to the server's own gain steps, twice: a programme crescendo or a bass note can coincide
            # with one step by chance, not with two [L §1.5, §5 P7]
            evidence.append(f"probe{c.probe_hits}")
        if c.confirmed and not evidence:
            evidence.append("confirmed")           # emitted before and still BASE: keep it eligible (deepening)
        if musical:
            klass = "MUSICAL"
            reasons = ["musical:" + "+".join(musical)]
        elif base_ok and evidence:
            klass = "STRONG"
            reasons += evidence
        elif base_ok and c.stationary:
            klass = "STATIONARY"                # answered the server's gain step like programme: report, never cut
            reasons.append("probe_linear")
        elif base_ok:
            klass = "MODERATE"
        else:
            klass = "TRACK"
        c.klass = klass
        c.reasons = tuple(reasons)
        # -- confidence (monotone in the margins; display only) ------------------------------------
        thr = cfg.confidence_threshold
        m_prom = min(1.0, max(0.0, (c.prominence_db - cfg.prominence_db) / 24.0))
        m_nar = min(1.0, max(0.0, (c.narrow_db - cfg.narrow_db) / 20.0))
        m_frames = min(1.0, c.run_frames / float(cfg.confirm_frames))
        m_ev = min(1.0, max(rise / 20.0,
                             (c.level_db - cfg.loud_line_db + 12.0) / 12.0 if c.level_db >= cfg.loud_line_db else 0.0,
                             0.6 if c.arm_evidence else 0.0, 0.5 * c.probe_hits))
        if klass == "STRONG":
            conf = thr + (1.0 - thr) * min(1.0, 0.25 * (m_prom + m_nar + m_frames + m_ev))
            c.confidence = min(0.999, conf)
        elif klass == "MODERATE":
            c.confidence = min(thr - 1e-6, 0.4 * thr + 0.55 * thr * min(1.0, (m_prom + m_nar + m_frames) / 3.0))
        elif klass in ("MUSICAL", "STATIONARY"):
            c.confidence = 0.1 * thr * m_prom
        else:
            c.confidence = min(thr - 1e-6, 0.35 * thr * min(1.0, 0.5 * (m_prom + m_frames)))

    # -- API -----------------------------------------------------------------------------------
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
        if self.first_ts is None:
            self.first_ts = ts
        # -- spectrum store (common-mode reference) and per-band baseline -----------------------------
        lo_r, hi_r = cfg.ref_band_lo, min(n - 1, cfg.ref_band_hi)
        rb = sorted(vals[lo_r:hi_r + 1])
        floor = rb[int(cfg.ref_floor_quantile * (len(rb) - 1))]
        fi = self.frames_seen
        self._spec.append((fi, vals, floor))
        self.ref_db = rb[len(rb) // 2]
        ref = fi                     # histories store the frame index; common-mode moves are computed on demand (_cm)
        if self._base is None:
            self._base = list(vals)
        else:
            seeding = (ts - self.first_ts) < cfg.baseline_seed_s
            a_dn, a_up = (0.5, 0.05) if seeding else (0.15, 0.002)
            base = self._base
            for i in range(n):
                d = vals[i] - base[i]
                base[i] += (a_dn if d < 0 else a_up) * d
        base = self._base
        self._sorted_levels = sorted(((v, i) for i, v in enumerate(vals)), reverse=True)[:4 * cfg.neighbour_bins + 8]
        # -- lines in this frame, matched to tracks by centroid ---------------------------------------
        prom = self.prominences(vals)
        lines = self._lines(vals, prom)
        pairs: list[tuple[float, int, int]] = []
        K1 = cfg.stable_frames
        for ci, c in enumerate(self._cands):
            young = cfg.track_young_penalty if c.frames < K1 else 0.0   # an established track outbids a fresh one
            for li, ln in enumerate(lines):
                d = abs(ln.centroid - c.centroid)
                if d <= cfg.track_match_bands:
                    pairs.append((d + young, ci, li))
        pairs.sort()
        used_c: set[int] = set()
        used_l: set[int] = set()
        for d, ci, li in pairs:
            if ci in used_c or li in used_l:
                continue
            used_c.add(ci)
            used_l.add(li)
            lines[li].track = self._cands[ci]
        survivors: list[Candidate] = []
        for ci, c in enumerate(self._cands):
            if ci in used_c:
                survivors.append(c)
                continue
            self._miss(c)
            if c.misses <= cfg.coast_frames:
                survivors.append(c)
        for ln in lines:
            if ln.track is None:
                ln.track = self._new_track(ln, ts)
                survivors.append(ln.track)
        # -- P2 needs the tracks (co-onset), then every matched track is extended and judged ---------------
        self._family(lines)
        for ln in lines:
            c = ln.track
            assert c is not None
            self._extend(c, ln, ts, ref, base)
        self._n_growing = sum(1 for ln in lines if ln.track is not None and ln.track.rise_db >= cfg.cogrowth_rise_db)
        for ln in lines:
            c = ln.track
            if self._steps:
                self._probe(c, ts)
            self._judge(c, ts)
        survivors.sort(key=lambda c: c.centroid)
        self._cands = survivors
        # -- emit ----------------------------------------------------------------------------------
        out: list[Detection] = []
        for c in self._cands:
            if c.misses:
                continue
            emit = c.klass == "STRONG"
            klass = c.klass
            if not emit and cfg.mode == "ringout":
                if c.klass == "MODERATE" and cfg.ringout_emit_moderate:
                    # programme absent by contract: a MODERATE line that no gain step could test in its lifetime
                    # is emitted once it has held BASE for K2 frames
                    untestable = not any(t_s >= c.first_ts - 0.5 for t_s, _ in self._steps)
                    if untestable and not c.glided and c.base_streak > cfg.confirm_frames - cfg.stable_frames:
                        emit = True
                        c.reasons = c.reasons + ("confirmed_K2",)
                elif (c.klass == "TRACK" and c.probe_hits >= cfg.probe_early_hits and not c.stationary
                      and c.run_frames >= cfg.stable_frames and "stable" in c.reasons and "in_window" in c.reasons):
                    # sub-threshold regeneration: a line that over-responded to two of the server's own +1 dB
                    # steps is within ~3 dB of ringing [L §1.5] — pre-emptive notch (what a human ring-out does)
                    emit = True
                    klass = "PROBE"
                    c.reasons = c.reasons + (f"probe{c.probe_hits}", "pre_emptive")
            if not emit:
                continue
            if c.last_emit_ts is not None and ts < c.last_emit_ts + cfg.cooldown_s:
                continue
            if c.last_emit_ts is None and self._in_cooldown(c.band, ts):
                continue
            conf = max(c.confidence, cfg.confidence_threshold)
            det = Detection(
                ts=ts, band=c.band, freq_hz=c.freq_hz, level_db=c.level_db, prominence_db=c.prominence_db,
                slope_db_per_s=c.slope_db_per_s, frames=c.frames, confidence=conf, reasons=c.reasons,
                klass=klass, centroid_band=c.centroid, narrow_db=c.narrow_db, rise_db=c.rise_db, excess_db=c.excess_db,
            )
            out.append(det)
            c.emitted += 1
            c.confirmed = True
            c.last_emit_ts = ts
            c.confidence = conf
            self._cooldown[c.band] = ts + cfg.cooldown_s
            log.debug("feedback detected: band %d (%.0f Hz) %.1f dB %s conf %.2f [%s]",
                      c.band, c.freq_hz, c.level_db, klass, conf, ",".join(c.reasons))
        if self._cooldown and self.frames_seen % 100 == 0:
            self._cooldown = {b: t for b, t in self._cooldown.items() if ts < t}
        self.frames_seen += 1
        self.last_ts = ts
        return out

    @property
    def candidates(self) -> list[Candidate]:
        """Current tracks (live objects; treat as read-only), lowest frequency first."""
        return list(self._cands)

    def reset(self) -> None:
        self._cands = []
        self._cooldown = {}
        self._base = None
        self._steps = []
        self._cuts = []
        self._arm_lines = []
        self._spec = deque(maxlen=96)
        self.frames_seen = 0
        self.first_ts = None
        self.last_ts = None


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
