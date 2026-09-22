"""CFS² feedback discriminator (explicit physical predicates) and notch planner — pure Python, no I/O.

Two synchronous, side-effect-free pieces used by ``cfs.py``:

* :class:`FeedbackDetector` consumes one RTA frame at a time (100 dB values from ``/meters/15``,
  band ``i`` centred at ``band_hz[i]`` = ``10000 * 2 ** ((i - 90) / 10)`` Hz, docs/research/meters.md §4.2)
  and returns :class:`Detection` objects for spectral lines that satisfy the feedback predicates below.
* :class:`NotchController` turns detections into GEQ band cuts (:class:`Notch`) — the planner is unchanged.

The full design, every constant with its physical origin and the corpus evidence, the irreducible cases and
the measured results are in **docs/DETECTOR.md**; this docstring is the map of the code.

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
7 ms at 2 kHz), so every instant bass onset renders at 40–160 Hz as a 4–8-frame 10–40 dB/s ramp (M7's 40/80 Hz
cuts) while real rings on short loops grow at 100–500 dB/s and tripped the 60 dB/s onset guard. Here each
predicate is a physical fact with its own threshold, tested alone (tests/test_detector_predicates.py), and
``Detection.reasons`` names the facts that justified a cut.

Per frame, over the whole spectrum  (``feed``; O(bands·k) + O(lines²) for the family test)
--------------------------------------------------------------------------------------
* ``prom[i]`` = level[i] − median(level[i±1..±k] without i), k = ``neighbour_bins``.
* A *line* (``_lines``) is a local maximum with prom ≥ ``track_prominence_db``. Its *cluster* is the peak plus any
  neighbour within ``cluster_merge_db`` (a tone between two centres reads −3/−3 dB). Per line: cluster power,
  cluster prominence (over the median of the bands ±2..±(k+1) beyond the cluster), narrowness (peak − max of the
  two bands just outside the cluster), and the power-weighted **centroid** of peak±1 with the local floor removed.
* Common mode (``_cm``/``_cm_rows``): the change between two stored spectra as the MEDIAN of the per-band changes of
  the signal-carrying reference bands (25..85, ≥ floor + ``ref_signal_db``; whole spectrum ≥ floor + 3 when the
  room is quiet). Rows are 3-frame means. A fader/master step moves every band; one growing line does not. [A §5]
* ``base[i]``: per-band slow low-percentile tracker (fast down, slow up) = what the band read before anything
  happened (P5). No absolute ``min_level_db`` gate.
* ``_analyser_flags``: PEAK_HOLD_SUSPECTED (≥ 15 signal bands bit-identical ≥ 10 frames), HOT_SPECTRUM (arm-time
  p95 ≥ −20 dBFS and p90 ≥ −26, or ≥ 3 bands clipping across more than one cluster: the clip flag is then neither
  a flag nor a level, and LOUD also needs the line above the arm-window maximum + 3 dB), SLOW_RELEASE (display
  release measured on live reference bands < 30 dB/s: growth evidence then needs ≥ ``strong_prominence_db``),
  PROGRAMME_PRESENT (``programme_present()``: note events / transients / line occupancy over 2 s — the ring_out
  contract check), FROZEN_LINES. The arm-time p95 (``refresh_arm_reference()`` re-opens it) is the reference of
  LOUD's level leg and of the loud-ish line (tier B: max(``loudish_level_db``, p95 + ``loudish_above_arm_db``)).
* ``base[i]`` freezes upward under a band that holds a qualified tracked line; input values are clamped to [−128, 0].
* Tracking: lines ↔ tracks by centroid (≤ ``track_match_bands``; tracks younger than K1 pay a matching penalty);
  unmatched tracks coast ``coast_frames``; a track whose centroid walked > ``drift_max_bands`` within
  ``drift_window_frames`` is RE-BORN with no history (``glided_in``). A newborn track receives its band's own
  visible pre-birth climb from the 96-frame spectrum store (``_backfill``; ``_seed_run`` lets that climb start
  the RISE reference where the band settles within a frame).

Per track  (K1 = ``stable_frames`` = 5 frames = 250 ms is the binding latency term; ``_extend``, ``_family``, ``_judge``)
--------------------------------------------------------------------------------------------------------------
P1 NARROW      cluster prominence ≥ ``prominence_db`` (12) and narrowness ≥ ``narrow_db`` (8).              [A §3]
P2 NO FAMILY   partials H2..H5 at +10/+15.85/+20/+23.2 bands (±``partial_tol_bands``) *present as lines*
               ≥ ``partial_prominence_db``, within ``partial_rel_db`` of the candidate's level, and *co-moving*
               (level change over K1 within ``comove_tol_db``); NOT co-moving when the partner APPEARED (band
               swing ≥ ``independent_swing_db``) after the candidate while it held within ``independent_hold_db``
               (G5), or when one line is dead still in pitch and the other, a clear line, moves (``pitch_*``).
               Family = ≥ 2 partials, or a lone exact H2 within ``h2_pair_rel_db`` born within ``co_onset_frames``.
               Also vetoed: a line that is H2/H3 of a lower line owning another partial (not if it is more than
               ``harm_over_base_max_db`` above it), or H4/H5 of a base ``subharm_hi_margin_db`` louder. A LOUD line
               keeps no veto of its own family (clipped-howl harmonics); being somebody's harmonic never escapes.
               A family first seen after the line rose ``late_partials_rise_db`` is distortion. MUSICAL when vetoed
               on ≥ ``family_veto_fraction`` of the last ``family_window_frames`` or most of the last K1. [A §4.1/4.3]
P3 STABLE      centroid range over the last K1 ≤ 2·``centroid_tol_bands`` and the presence run ≥ K1 (FAST-RISE may
               use track age: its restarting jumps were the growth itself).                          [A §4.3 v, §4.4]
P4 SUSTAINED   not decaying (LS slope ≤ −``decay_db_per_s`` with a drop ≥ ``decay_drop_db``) and not sagging >
               ``sag_db`` under the K2 maximum.                                                              [L §5]
   COMMON-MODE |Δcm| ≥ ``common_mode_db`` over K2 with the line following within ``common_mode_tol_db`` ⇒ MUSICAL.
   CO-GROWTH   ≥ ``cogrowth_lines`` OTHER lines whose runs started within max(``cogrowth_coborn_frames``, 6 dB/rate)
               of mine, swelling ≥ ``cogrowth_rise_db`` (net of cm, still rising) at 0.5–2× my rate, on ≥ 2
               consecutive frames ⇒ MUSICAL; on the 3rd the RISE reference is re-anchored. Never on an emitted line.
P5 NEW ENERGY  peak − base ≥ ``baseline_excess_db``; a line born within ``arm_frames`` carries AT-ARM evidence instead.
P6 WINDOW      centroid frequency within [``window_low_hz``, ``window_high_hz``]: 160 Hz watch / 63 ring_out / 40 with
               ``lf_feedback_possible`` / ``lf_edge_hz`` when cfs derives it from the channel HPFs.       [L §3.4]
   FROZEN      peak AND a skirt band bit-identical over the last ``frozen_frames`` (below clip − 3 dB): a peak-held
               display value, not a line (a steady tone's skirts still jitter).
EVIDENCE (any one makes a BASE line STRONG; none is required):
   RISE        cluster level − the ``rise_quantile`` low quantile of the presence run's settled levels, kept as an
               age-unbounded LOW-WATER MARK (with its own spectrum row for the cm comparison; re-anchored if the
               centroid moves > ±2·``centroid_tol_bands``), net of common mode beyond ``cm_deadband_db``, capped by
               the peak band's own rise + 3 dB, ≥ ``rise_db`` (6). The run starts 1.5·k/Δf after birth (never 0
               frames) and RESTARTS on a > ``onset_step_db`` jump out of a line not already climbing INTO this
               frame, on re-acquisition after > K1 missed frames unless climbing, and below ~300 Hz on a >
               ``lf_onset_step_db`` jump within one settle window or a ``restart_drop_db`` fall; there half the
               rise must be older than one settle window. No rate window.                          [L §1.3, §1.6]
   FAST-RISE   ≥ ``fast_rise_min_steps`` of the last ≤ 5 increments ≥ ``fast_rise_step_db`` (the band's pre-birth
               climb included; the step out of the bed counts only for a loud-ish line: two OBSERVED increments are
               also a soft attack), summing to ≥ ``fast_rise_db`` net of cm, no increment > ``fast_rise_max_share``
               (two ≤ 75 %), still rising at the end, a new high; LF: an unbroken run longer than settle + 2. An
               instrument attack is 1–2 increments.                                              [L §1.3, judge G1]
   LOUD        peak at the clip flag (unless HOT_SPECTRUM), or ≥ max(``loud_line_db`` −10, min(arm p95 +
               ``loud_above_arm_db`` 20, ``loud_ceiling_db`` −6)) — under HOT also ≥ arm max + ``loud_hot_over_max_db``
               — AND ≥ ``loud_margin_db`` above every band outside its neighbourhood; below 160 Hz only the clip flag
               inside a declared LF window. Void for a STATIONARY line.                                    [L §4.3]
   AT-ARM      median level ≥ ``arm_line_min_level_db`` and prominence ≥ ``strong_prominence_db`` over the first
               K1 frames, present ≥ ``arm_presence`` and steady within ``arm_range_db``, family-vetoed on ≤
               ``arm_family_life_max`` of its frames, < ``cogrowth_lines`` other such lines within
               ``arm_cohort_rel_db``; emitted at K1 if ≥ ``arm_fast_level_db``, else after ``arm_confirm_s``.
               ring_out: below ``arm_fast_level_db`` only after ``probe_min_hits`` judged steps, never if the line
               FOLLOWED them (STATIONARY).                                                          [judge G4]
   PROBE       ring_out, ``note_gain_step(Δ, ts)``: a line steady before the step answers HIT (≥ Δ + ``probe_over_db``,
               cm ≤ Δ + 0.5), FOLLOWS (≤ Δ + 1: a room source through the mic → STATIONARY, never cut, AT-ARM/LOUD
               void) or PINNED (< ``probe_pinned_fraction``·Δ: electrical, or a plateau held downstream of the tap —
               no verdict). ``probe_min_hits`` hits = evidence; on a still sub-threshold TRACK line = pre-emptive
               PROBE emission.                                                                     [L §1.5, P7]
Classes / emission
   FALSE_CUT ≻ MUSICAL ≻ (BASE ∧ evidence ∧ family on this frame → MODERATE "family_now") ≻ STRONG (BASE ∧ evidence)
   ≻ STATIONARY (+ "backoff_advised" when ≥ 30 dB prominent) ≻ MODERATE (BASE) ≻ TRACK.  watch and ring_out emit
   STRONG (and ring_out PROBE); MODERATE is *published* in ``candidates`` (klass, level_db, prominence_db, excess_db,
   age_s, reasons, freq_hz, cut_verdict, …) for cfs's tier-B policy and never cut by the detector. Re-emission (=
   deepen) only on evidence gathered since the last emission: regrowth ≥ ``reemit_rise_db`` above the emitted level,
   LOUD now with the line not having come down, a new probe hit, or a 'held'/'insufficient' ``note_cut()`` verdict
   on a loud-ish line that was emitted on plateau-class evidence (FAST-RISE / LOUD / PROBE / AT-ARM: one re-emission
   per verdict) — never while a verdict is pending / false_cut / ambiguous. ``note_cut()`` classifies the post-cut
   response against the RBJ bell attenuation expected at the line (Q ``geq_q_min``..``geq_q_max``): confirmed /
   insufficient / held / false_cut (the line ended by itself: klass FALSE_CUT, never re-emitted unless it grows
   ``rise_db`` again) / ambiguous (``cut_log``, ``Candidate.cut_verdict``).
``confidence`` is a monotone function of the margins for the dashboard (≥ ``confidence_threshold`` exactly when
emitted); it is not the decision variable. Only ``logging`` is used for diagnostics (stdout is the MCP transport).
[L §x] = loop-physics brief, [A §x] = analyser brief, [C §x] = tests/rtasim/CORPUS.md (docs/DETECTOR.md §refs).
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
    "NotchPlan",
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
    cogrowth_lines: int = 2              # this many OTHER lines swelling WITH me (born within cogrowth_coborn_frames of my
    cogrowth_rise_db: float = 3.0        #   run start, risen >= cogrowth_rise_db over the last second and still rising, at
    cogrowth_coborn_frames: int = 4      #   0.5-2x my rate) = one swelling source / a pad / a fade-in, not a loop (rings start
    cogrowth_window_frames: int = 20     #   alone and grow at their own e/tau); never applied to a line already emitted
    family_min_partials: int = 2         # of H2..H5 present ⇒ the frame is family-vetoed
    family_veto_fraction: float = 0.4    # vetoed on ≥ this share of its frames ⇒ MUSICAL
    late_partials_rise_db: float = 12.0  # a family that first appears after the line rose this much is distortion
    h2_pair_rel_db: float = 12.0         # a lone H2 within this of H1's level, born with it, is a family (8'+4' organ)
    co_onset_frames: int = 2             # 'born together' = track births within this many frames
    subharm_hi_margin_db: float = 3.0    # I am somebody's H4/H5 only if that somebody is this much louder than me (speech H1 vs H4/H5)
    harm_over_base_max_db: float = 12.0  # ... and nobody's H2/H3 if I am more than this above them (partials: 0..-15 dB re H1, <= +10 for vowels/HPF'd bass)
    comove_tol_db: float = 3.0           # partials co-move: level changes over K1 frames agree within this
    independent_swing_db: float = 8.0    # G5: a would-be partial that APPEARED (>= this above its band's baseline) >= K1 frames after the
    independent_hold_db: float = 3.0     #   candidate, while the candidate's level held within independent_hold_db, is an independent
                                         #   source (a note arriving under a line that did not care), not a partial of it [P12]
    pitch_still_bands: float = 0.1       # G5b: a line whose centroid ranged <= this over K1 (a loop: sd < 0.05 band) and one that ranged
    pitch_moving_bands: float = 0.3      #   >= this (vibrato +-30 c and up, a scoop, drift) do not share a pitch modulation: not partials
                                         #   of one source [L §2.3, A §4.3(v)/4.4]
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
    cm_smooth_frames: int = 3            # common mode is measured between cm_smooth_frames-frame mean spectra (estimate jitter)
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
    arm_family_life_max: float = 0.2     # AT-ARM needs a family-free life: family-vetoed on at most this fraction of the track's frames
    arm_cohort_rel_db: float = 12.0      # >= cogrowth_lines OTHER at-arm lines within this of mine (a chord / registration sounding when
                                         #   we armed) => none of them is 'an established howl': howls start alone and one howl at its
                                         #   limiter suppresses the next [L §2.3]; two (two mics) stay allowed, three do not
    # -- frequency window (P6) -----------------------------------------------------------------
    window_low_hz_watch: float = 160.0
    window_low_hz_ringout: float = 63.0
    window_low_hz_lf: float = 40.0
    window_high_hz: float = 12500.0
    lf_feedback_possible: bool = False
    lf_edge_hz: float | None = None      # explicit low edge (cfs derives it from the open channels' HPF: ~0.7·f_hpf);
                                         # overrides the three mode defaults above when set
    # -- evidence ------------------------------------------------------------------------------
    strong_prominence_db: float = 18.0   # AT-ARM evidence needs this median prominence
    rise_db: float = 6.0                 # RISE evidence: net rise since the analyser settled
    onset_step_db: float = 4.0           # a single-frame jump larger than this out of a flat line (previous step <
    onset_flat_db: float = 1.0           # onset_flat_db) is an onset landing on the track, not growth: the run restarts
    lf_onset_step_db: float = 6.0        # LF: the same, measured over one analyser-settle window (per-frame jumps are smeared):
    lf_onset_flat_db: float = 2.0        #     a rise > 6 dB within one window out of a window that moved < 2 dB is an onset
    onset_rising_db_per_s: float = 10.0  # a line whose recent settled samples climb at least this fast ...
    onset_rising_resid_db: float = 0.75  # ... and this cleanly was 'already climbing': a jump then is not an onset
    rise_quantile: float = 0.2           # RISE reference = this quantile of the run's last rise_window_frames settled levels
    rise_window_frames: int = 64         #   (flutter- and tremolo-robust: 3.2 s spans vibrato, Leslie and 'dynamics' cycles) ...
    rise_ref_min_samples: int = 8        # ... and once the window holds this many samples (its 20 % quantile then skips a
                                         #   flutter dip or a 1-frame attack tail) it is kept as an age-unbounded LOW-WATER MARK, so a
                                         #   loop creeping at 1-2 dB/s still adds up (reset on run restart / pitch move)
    restart_drop_db: float = 6.0         # a fall this far under the run's maximum ends the run (next rise = re-excitation)
    growth_frames: int = 8               # GROWTH evidence (the optional early upgrade): over this many settled frames ...
    growth_min_db_per_s: float = 10.0    # ... a least-squares slope of at least this (no upper bound) ...
    growth_resid_db: float = 1.0         # ... with rms residual about the dB-linear fit no larger than this ...
    growth_rise_db: float = 6.0          # ... and a net rise of at least this. = rise_db means OFF (default): at 4.0 it buys
                                         #     100-200 ms on 10-15 dB/s rings but fired on a 10 dB/s pad-swell partial on a
                                         #     hold-out seed (programme margin too thin: whistle swell 6.2 dB/s at 3.1 dB)
    analyser_rise_k: float = 1.0         # analyser settle time = k/Δf (uncertainty bound; U-model = 1.0)
    fast_rise_db: float = 15.0           # FAST-RISE evidence: a run of >= fast_rise_min_steps consecutive frame-to-frame
    fast_rise_step_db: float = 2.0       #   increments each >= this (>= 40 dB/s resolved frame by frame), summing to >=
    fast_rise_min_steps: int = 3         #   fast_rise_db, no single increment carrying more than fast_rise_max_share of
    fast_rise_max_share: float = 0.55    #   the rise: exponential loop growth [L §1.3]. An instrument attack (10-80 ms
                                         #   [A §4.2]) is one or two increments however the frame boundaries fall; three
                                         #   comparable ones need >= 100 ms of sustained dB-linear climb at >= 40 dB/s.
                                         #   Includes the band's pre-birth frames from the spectrum store (a 150 dB/s howl
                                         #   in a loud mix shows 2-4 frames of growth and none may be wasted). Below
                                         #   ~300 Hz the run must also outlast the band's settle window + 2.
    loudish_level_db: float = -20.0      # the LOUD-ISH line = max(this, arm p95 + loudish_above_arm_db): tier B's level [L §4.3 'loud-ish
    loudish_above_arm_db: float = 10.0   #   >= -20 dBFS'], one tier (10 dB) under LOUD's on both legs. Used twice: (1) FAST-RISE counts the
                                         #   step OUT OF THE BED (the virtual first sample) as one of its fast_rise_min_steps increments only
                                         #   for a line that reached it -- two OBSERVED increments (100-150 ms of climb) are also a soft /
                                         #   breath / legato attack [A §4.2: 100-250 ms], so '2 frames + the bed' is admitted only where the
                                         #   plateau is loud-ish; quieter lines need three observed increments and are published MODERATE
                                         #   meanwhile [judge G1: 'do not admit 2-frame evidence in watch']; (2) a cut whose line then HOLDS
                                         #   (dropped by the bell and sits there) is deepened only for a loud-ish line cut on plateau-class
                                         #   evidence (see cut_verify_s) [L §4.3 tier A 'deepen when VERIFY fails and the line is still at limit']
    backfill_frames: int = 12            # how far back a newborn track looks for its band's pre-birth climb ...
    backfill_floor_margin_db: float = 3.0 #   taking only frames this far above the band's median over that span (its bed)
    loud_line_db: float = -10.0          # LOUD evidence: peak >= max(this, min(arm-time p95 + loud_above_arm_db, loud_ceiling_db))
    loud_above_arm_db: float = 20.0      #   [L §4.3 tier A]: referenced to what the whole display read over the first arm_baseline_s (its
    loud_ceiling_db: float = -6.0        #   p95 ~ the loudest programme bands; +20 = more than a sine source's concentration gain above
    arm_baseline_s: float = 2.0          #   them [A §1]) so a loud show raises the bar with it -- up to loud_ceiling_db: within 6 dB of full
                                         #   scale a steady line that also dominates the frame by loud_margin_db is at an electronic limit
                                         #   (the loop's) or a deliberate solo tone (a whistle into the mic: the irreducible AF08 class),
                                         #   never a mix balance, however loud the show; -10 dBFS is the floor ...
    loud_margin_db: float = 6.0          # ... AND this far above every band outside its own ±k neighbourhood (relative leg)
    clip_db: float = -1.0                # ... or at the analyser's clip flag (0.0 = 'clipping occurred', meters.md §4.2)
    hot_spectrum_p95_db: float = -20.0   # HOT_SPECTRUM (flag): arm p95 at least this and p90 within a further 6 dB (a display gain of tens
                                         #   of dB -- UNCONFIRMED whether /-prefs/rta/gain reaches /meters/15; cfs pins it to 0 -- or a bus
    hot_hold_s: float = 5.0              #   living at its limiter), or >= 3 bands at the clip flag spanning more than one line's cluster
                                         #   (programme clipping the display), held hot_hold_s. While up, the clip flag is not LOUD evidence
    loud_hot_over_max_db: float = 3.0    #   (0.0 then means 'display clipped') and the level legs additionally require the line to stand
                                         #   loud_hot_over_max_db above the loudest cell of the arm window (near full scale is then ordinary;
                                         #   above everything the programme ever reached is not)
    frozen_frames: int = 5               # a peak level bit-identical over the last frozen_frames frames (K1: 4 of 4 steps) is a frozen
                                         #   display (peak-hold), not a live line: no evidence counts for confirm_frames [A §2]
    peak_hold_bands: int = 15            # >= this many bands unchanged for >= peak_hold_frames frames => flag PEAK_HOLD_SUSPECTED
    peak_hold_frames: int = 10
    slow_release_db_per_s: float = 30.0  # the display's release rate is MEASURED (20 x the largest single-frame fall of any reference band
                                         #   over the last 2 s: the bed always exercises it); below this (/-prefs/rta/decay >~ 2 s) the flag
                                         #   SLOW_RELEASE is raised (cfs re-forces decay 0.25) and, while it is up, growth evidence is acted
                                         #   on only for lines >= strong_prominence_db (programme continuity is being stretched by the display)
    reemit_rise_db: float = 3.0          # a line is re-emitted (the cut deepened) only when it has grown this far above its level at the
                                         #   previous emission (a loop that beat the cut climbs on by far more; a plateau merely wanders
                                         #   / pumps 1-3 dB [C §7.5]), or is LOUD now, or over-responded to a probe step since [L §4.1]
    cut_confirm_extra_db: float = 3.0    # note_cut(): drop >= bell + this (or the line collapses time-locked to the cut) => 'confirmed':
                                         #   a killed loop falls away by far more than the bell [L §4.1 / P13]
    cut_false_tol_db: float = 1.0        # drop within bell +- this and flat at cut_verify_s, line still there => 'held' (programme through
    cut_verify_s: float = 1.5            #   the EQ, or a howl held by a limiter/compressor with more excess than the cut: passively identical);
                                         #   the line ENDS on its own after the response window => 'false_cut' (a loop that survived a cut
                                         #   does not switch itself off: it was a note; never re-emitted); drop < bell - this after the
                                         #   response window => 'insufficient'. A 'held'/'insufficient' line that was cut on plateau-class
                                         #   evidence (FAST-RISE / LOUD / PROBE / AT-ARM) and is loud-ish is re-emitted once per verdict
                                         #   (deepen -3 -> -6 -> -9, each step re-verified); rise-only and quiet lines are cut once and the
                                         #   verdict is reported for cfs / the operator
    cut_settle_s: float = 0.15           # ignore this long after the cut lands (write latency + one frame)
    cut_response_s: float = 0.3          # a killed ring collapses within this after settling ((atten-e)/tau >= tens of dB/s [L §4.1])
    geq_q_min: float = 2.0               # X32 GEQ bell Q is UNCERTAIN between a proportional-Q graphic (~2) and true 1/3 octave (4.3)
    geq_q_max: float = 4.3               #   [L §4.1]: the bell attenuation expected AT THE LINE is bracketed with both
    programme_window_s: float = 2.0      # programme_present(): over this window, >= programme_min_events note events (a line born
    programme_min_events: int = 3        #   after arm reaching 3 qualified frames, a re-struck / re-pitched line), or >=
    programme_min_transients: int = 2    #   programme_min_transients broadband transients (>= programme_transient_fraction of the fast
    programme_transient_fraction: float = 0.25  # reference bands up >= 6 dB in one frame: a drum hit / attack; independent noise bands
    programme_occupancy: float = 1.0     #   never jump together), or on average >= programme_occupancy qualified lines born after arm
                                         #   alive (a melody / a pad). A quiet room, hum, a whine, a standing howl make none of these.
    probe_over_db: float = 2.0           # PROBE: response ≥ step + this
    probe_settle_s: float = 0.15         # ignore this long after the step (LF bands lag)
    probe_window_s: float = 1.4          # judge within this after the step (< ring_out dwell)
    probe_early_hits: int = 2            # ring_out: over-responses to this many steps ⇒ pre-emptive (sub-threshold) emit
    probe_min_hits: int = 2              # PROBE evidence needs this many over-responding steps
    probe_steady_db: float = 3.0         # the line must be steady (range <= this) before a step to judge it (2x after)
    probe_pinned_fraction: float = 0.35  # a response below this fraction of the step = the line ignored the master (see _probe)
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
        need(0 < min(self.window_low_hz_lf, self.window_low_hz_ringout, self.window_low_hz_watch)
             and max(self.window_low_hz_lf, self.window_low_hz_ringout, self.window_low_hz_watch) < self.window_high_hz,
             "frequency window low edges must be > 0 and below window_high_hz")
        need(self.lf_edge_hz is None or 0 < self.lf_edge_hz < self.window_high_hz, "lf_edge_hz must be in (0, window_high_hz)")
        need(self.loud_ceiling_db >= self.loud_line_db, "loud_ceiling_db must be >= loud_line_db")
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
        if self.lf_edge_hz is not None:
            return float(self.lf_edge_hz)
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
            if "None" in typ:                       # Optional[...] keys: null/None/'' -> None, else the base type
                if value is None or (isinstance(value, str) and value.strip().lower() in ("", "none", "null")):
                    kw[key] = None
                    continue
                typ = typ.replace("| None", "").replace("None |", "").strip()
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
    onset_fi: int = 0              # frame index at which the source now on this track arrived (birth, or the onset jump /
                                   #   long-gap re-acquisition that restarted its presence run): 'born together' compares these
    born_at_arm: bool = False      # the line was already sounding within arm_frames of arming -- a property of the LINE, not of the
                                   #   track slot: cleared the moment another source lands on the track (run restart) [F1]
    arm_evidence: bool = False     # AT-ARM evidence, evaluated once over the track's first K1 frames
    narrow_db: float = 0.0
    cluster_db: float = -128.0
    excess_db: float = 0.0
    rise_db: float = 0.0
    veto_frames: int = 0
    veto_total: int = 0           # matched frames on which a harmonic family was present (lifetime, incl. late partials)
    glided: bool = False           # this track was (re)born from a line that walked > drift_max_bands (P3)
    common_mode: bool = False
    stationary: bool = False       # ring_out: answered a probe step like programme (≤ Δ + 1 dB)
    probe_hits: int = 0
    probe_linear: int = 0
    probe_pinned: int = 0          # steps the line ignored (0 dB/dB): electrical, or a plateau held downstream of the tap
    qual_frames: int = 0           # matched frames on which P1 held (programme-activity bookkeeping)
    klass: str = "TRACK"
    reasons: tuple[str, ...] = ()
    misses: int = 0                # consecutive frames without the line (coasting)
    settle_frames: int = 1         # frames of a presence run before rises count (analyser settle)
    run_frames: int = 0            # length of the current continuous-presence run (restarts on a miss or an onset jump)
    run_lv: deque = field(default_factory=lambda: deque(maxlen=64))  # (cluster_db, frame index, spectrum row, peak_db) of the run's last rise_window_frames settled frames
    run_low: tuple | None = None   # (level, frame index, spectrum, centroid, peak): the lowest windowed low-quantile the run has
                                   # produced -- the RISE reference, unbounded in age (reset when the run restarts or the
                                   # line's centroid leaves +-2*centroid_tol_bands of where the mark was taken)
    run_settled: int = 0           # settled samples in the current run (unbounded count; run_lv keeps the last 64)
    run_start_fi: int = 0          # frame index at which the current presence run started
    swell_db: float = 0.0          # rise over the last cogrowth_window_frames (0 unless still rising): co-growth bookkeeping
    swell_rate: float = 0.0        # dB/s over the last 8 frames while swelling
    cogrow_streak: int = 0         # consecutive frames on which >= cogrowth_lines mates were swelling with this line
    prev_cluster_db: float | None = None
    prev_inc_db: float = 0.0
    prev_ts: float | None = None
    rise_streak: int = 0           # consecutive matched frames (no gap) on which the level rose >= onset_flat_db
    run_max_db: float = -128.0     # highest cluster level of the current run
    rise_old_db: float = 0.0       # the part of rise_db already present one analyser-settle time ago
    growth_db_per_s: float = 0.0   # clean dB-linear slope over the last growth_frames settled frames (0 if not clean)
    pre_lv: list = field(default_factory=list)   # back-filled (cluster_db, spectrum) of the band's own climb before birth
    fast_rise_db: float = 0.0      # FAST-RISE evidence: net rise of the qualifying run of increments (0 = none); sticky
    fast_rate_db_per_s: float = 0.0
    last_emit_level_db: float | None = None   # cluster level at the previous emission (re-emission needs >= this + reemit_rise_db)
    probe_hits_at_emit: int = 0    # probe hits at the previous emission (a new hit since re-arms re-emission)
    frozen_until: int = -1         # frame index until which the line counts as a frozen display value (peak-hold)
    cut: tuple | None = None       # pending note_cut(): (ts, step_db, level_before, total_depth_db, atten_lo, atten_hi)
    cut_verdict: str | None = None # None | 'pending' | 'confirmed' | 'insufficient' | 'held' | 'false_cut' | 'ambiguous'
    cut_deepen: bool = False       # the latest 'held'/'insufficient' verdict entitles ONE re-emission (deepen) not yet made
    cuts_held: int = 0             # consecutive held/insufficient verdicts on this line (report)
    emit_evidence: tuple[str, ...] = ()   # evidence names at the latest emission ('fastrise', 'loud', 'probe', 'established_at_arm', 'rise')
    emit_peak_db: float | None = None     # peak level at the latest emission
    false_cut: bool = False        # a cut went through this line like programme through an EQ: never re-emitted (klass FALSE_CUT)
    false_cut_level_db: float | None = None   # cluster level when false_cut was declared (fresh growth above it re-admits the line)
    ever_qualified: bool = False   # has reached P1 qualification at least once (programme-flux bookkeeping)
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
            "klass": self.klass,
            "age_s": round(self.age_s, 3),
            "born_at_arm": self.born_at_arm,
            "glided_in": self.glided,
            "probe_hits": self.probe_hits,
            "steps_seen": self.steps_seen,
            "stationary": self.stationary,
            "cut_verdict": self.cut_verdict,
            "cut_deepen": self.cut_deepen,
            "cuts_held": self.cuts_held,
            "fast_rise_db": round(self.fast_rise_db, 1),
        }

    @property
    def age_s(self) -> float:
        """Seconds this track has existed (last matched frame - birth)."""
        return max(0.0, self.last_ts - self.first_ts)


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
                 lf_feedback_possible: bool | None = None, lf_edge_hz: float | None = None) -> None:
        if mode is not None or lf_feedback_possible is not None or lf_edge_hz is not None:
            from dataclasses import replace
            kw: dict[str, Any] = {}
            if mode is not None:
                kw["mode"] = mode
            if lf_feedback_possible is not None:
                kw["lf_feedback_possible"] = bool(lf_feedback_possible)
            if lf_edge_hz is not None:
                kw["lf_edge_hz"] = float(lf_edge_hz)
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
        self._steps: list[tuple[int, float, float]] = []     # (seq, ts, delta_db) from note_gain_step; seq is monotone
        self._step_seq: int = 0                              #   so pruning old steps never renumbers judged ones
        self._cut_depth: dict[int, float] = {}               # last known total GEQ depth per rounded frequency (note_cut)
        self.cut_log: list[dict[str, Any]] = []              # note_cut() outcomes: {ts, freq_hz, step_db, verdict, drop_db}
        self.flags: set[str] = set()                         # PEAK_HOLD_SUSPECTED / FROZEN_LINES / HOT_SPECTRUM / PROGRAMME_PRESENT
        self._last_vals: list[float] | None = None
        self._flat_run: list[int] = []
        self._arm_levels: list[float] = []                   # every band level over the first arm_baseline_s (for the arm p95)
        self.arm_p95_db: float | None = None                 # 95th percentile of the arm-time spectrum (LOUD reference)
        self.arm_p90_db: float | None = None                 # 90th percentile (HOT_SPECTRUM guard)
        self.arm_max_db: float | None = None                 # loudest cell over the arm window (LOUD under HOT_SPECTRUM)
        self._arm_ref_t0: float | None = None                # start of the arm reference window (None = first frame)
        self._events: deque = deque()                        # ts of programme note events (see programme_present)
        self._transients: deque = deque()                    # ts of broadband transients
        self._occupancy: deque = deque()                     # (ts, live qualified non-at-arm lines) per frame
        self._arm_cohort: list[Candidate] = []               # tracks carrying AT-ARM evidence, present this frame
        self._hot_until: float = -1.0                        # HOT_SPECTRUM (broadband clip) hold
        self._falls: deque = deque(maxlen=40)                # largest single-frame fall among the reference bands, per frame
        self.release_db_per_s: float | None = None           # measured display release rate (None until 1 s of frames)
        self.frames_seen: int = 0
        self.first_ts: float | None = None
        self.last_ts: float | None = None
        self.ref_db: float = -128.0
        self._sorted_levels: list[tuple[float, int]] = []
        self._swelling: list[Candidate] = []
        self._spec: deque = deque(maxlen=96)                   # (frame index, levels, floor) for the common-mode reference

    # -- optional hooks ------------------------------------------------------------------------
    @property
    def mode(self) -> str:
        return self.cfg.mode

    def note_gain_step(self, delta_db: float, ts: float) -> None:
        """ring_out: the server stepped the bus master by ``delta_db`` at ``ts`` (its own, timed write). Lines
        that answer with more than ``delta_db + probe_over_db`` within the dwell are loop-gain dependent."""
        self._step_seq += 1
        self._steps.append((self._step_seq, float(ts), float(delta_db)))
        # keep the steps a track could still be judged on (probe window) plus a margin; tracks remember the
        # sequence numbers they have judged, not list positions, so pruning is safe (a -40 -> 0 dB ring-out in
        # 1 dB steps has 40 steps and the ones that matter are the last)
        horizon = float(ts) - 4.0 * max(self.cfg.probe_window_s, 1.0)
        if len(self._steps) > 64 or (self._steps and self._steps[0][1] < horizon and len(self._steps) > 8):
            self._steps = [st for st in self._steps if st[1] >= horizon][-64:]

    def note_emission(self, cand: "Candidate", ts: float | None = None, *, reason: str = "tier_b") -> bool:
        """cfs cut the line ``cand`` (one of :attr:`candidates`) BY POLICY -- tier B, an at-arm alert it later acted on, a
        back-off probe -- without a :class:`Detection` from :meth:`feed`. Record that emission on the track exactly as
        ``feed()`` records its own (emission count, level and probe hits at emission, per-band cooldown) so that (1) the
        :meth:`note_cut` verdict treats it as THE cut line -- a policy-cut ring that collapses time-locked to the write is
        'confirmed', not a verdict-less bystander -- and (2) a later re-emission by the detector needs evidence gathered
        since (regrowth above the cut level, LOUD without having come down, a new probe hit). ``reason`` is added to the
        track's emission evidence; it is not plateau-class evidence, so the detector never deepens such a cut by itself.
        Call it right before :meth:`note_cut` for the write. Returns False when ``cand`` is not a live track."""
        if not isinstance(cand, Candidate) or not any(c is cand for c in self._cands):
            return False
        t = float(self.last_ts if ts is None else ts) if (ts is not None or self.last_ts is not None) else 0.0
        c = cand
        c.emitted += 1
        c.last_emit_ts = t
        c.last_emit_level_db = c.cluster_db
        c.emit_peak_db = c.level_db if c.emit_peak_db is None else max(c.emit_peak_db, c.level_db)
        c.emit_evidence = tuple(sorted(set(c.emit_evidence) | {str(reason)}))
        c.probe_hits_at_emit = c.probe_hits
        self._cooldown[c.band] = t + self.cfg.cooldown_s
        log.debug("policy emission noted: band %d (%.0f Hz) %.1f dB [%s]", c.band, c.freq_hz, c.level_db, reason)
        return True

    def note_suppressed(self, cand: "Candidate", *, reason: str = "at_arm") -> bool:
        """cfs DECLINED to act on the :class:`Detection` that :meth:`feed` just returned for ``cand`` (the watch's at-arm
        rule: an established-at-arm line that is neither LOUD nor prominent enough is alerted and left to the policy's tier
        B, docs/CFS_POLICY.md §5). The emission stays on the track's record -- so the detector re-emits only on evidence
        gathered since (regrowth, LOUD, a probe hit) -- but the AT-ARM name is withdrawn from the emission evidence and any
        pending deepen right is cleared: a later 'held' / 'insufficient' verdict on a POLICY cut of this line must not
        entitle the detector to deepen it by itself on the very observation cfs declined to act on (that deepening is the
        policy's verdict-gated cadence). ``suppressed_<reason>`` is recorded in its place. Nothing else about the track
        changes (klass, reasons, verdict machinery). Returns False when ``cand`` is not a live track."""
        if not isinstance(cand, Candidate) or not any(c is cand for c in self._cands):
            return False
        c = cand
        c.emit_evidence = tuple(sorted((set(c.emit_evidence) - {"established_at_arm"}) | {f"suppressed_{reason}"}))
        c.cut_deepen = False
        log.debug("emission suppressed by cfs (%s): band %d (%.0f Hz) %.1f dB; evidence now [%s]", reason, c.band, c.freq_hz, c.level_db,
                  ",".join(c.emit_evidence))
        return True

    @staticmethod
    def bell_attenuation_db(depth_db: float, offset_oct: float, q: float) -> float:
        """Attenuation (dB, >= 0) of an RBJ peaking cut of ``depth_db`` (< 0) and quality ``q`` at ``offset_oct`` octaves
        from its centre -- what a PRE-insert GEQ cut does to a line at that distance, i.e. what the RTA tap should show for
        programme (analog prototype |H|^2 = ((1-r^2)^2 + (rA/Q)^2) / ((1-r^2)^2 + (r/(AQ))^2), A = 10^(G/40)) [L §4.1]."""
        if depth_db >= 0.0:
            return 0.0
        a = 10.0 ** (depth_db / 40.0)
        r = 2.0 ** offset_oct
        d = (1.0 - r * r) ** 2
        h2 = (d + (r * a / q) ** 2) / (d + (r / (a * q)) ** 2)
        return -10.0 * math.log10(h2)

    def note_cut(self, freq_hz: float | None = None, depth_db: float = 0.0, ts: float | None = None, *,
                 band: int | None = None, step_db: float | None = None) -> None:
        """A GEQ cut landed: ``depth_db`` is the GEQ band's new TOTAL gain (<= 0) at centre ``freq_hz`` (or RTA ``band``)
        at time ``ts``; ``step_db`` the change just written (default: difference to the last depth noted for that
        frequency, else ``depth_db`` itself). Every live track within +-1/3 octave starts a post-cut watch against the
        bell attenuation expected AT THE LINE (bracketed between GEQ Q ``geq_q_min`` and ``geq_q_max``, X32 shape
        UNCERTAIN [L §4.1]); :attr:`cut_log` and the track's ``cut_verdict`` then say:

        * ``'confirmed'`` -- the line fell >= bell + ``cut_confirm_extra_db``, or collapsed / vanished within
          ``cut_settle_s + cut_response_s`` of the cut: a loop that lost its gain decays at (atten-e)/tau, tens to hundreds
          of dB/s, i.e. by far more than the bell and time-locked to the write [L §4.1, P13];
        * ``'insufficient'`` -- after the response window the drop is short of the bell (or the line climbs again): the
          excess exceeds the attenuation at the line, or the cut did not reach the line at all;
        * ``'held'`` -- the drop equals the bell within ``cut_false_tol_db`` and the line sits there, flat and still
          present, at ``cut_verify_s``: EITHER programme that went through the EQ (a held note) OR a howl whose plateau is
          set by a limiter / compressor (downstream of the tap the SPL is pinned and the tap simply drops by the cut;
          upstream, a limiter output through the GEQ drops by the cut too; a compressor of ratio r shows 3r/(r-1) dB) with
          more excess than the cut [L §1.4]. Passively identical. The detector deepens (re-emits once per verdict, so -3 ->
          -6 -> -9 with each step verified) only when the line was cut on PLATEAU-CLASS evidence -- FAST-RISE (>= 40 dB/s:
          only such a loop can hold e > 3 dB at a plateau; a ring that grew slower has e < 3 dB and a 3 dB cut takes it
          under), LOUD, PROBE or AT-ARM -- and is loud-ish (>= :attr:`loudish_threshold_db` before the cut); a line cut on
          RISE alone, or a quiet one, is cut once and the verdict is exposed (``Candidate.cut_verdict``, :attr:`cut_log`)
          for cfs to alert 'cut did not remove the line';
        * ``'false_cut'`` -- the line ENDS on its own (abruptly, after the response window, from the post-bell level: a
          loop that survived a cut does not switch itself off, a note does). The track becomes klass ``FALSE_CUT`` and is
          never emitted again unless it later grows >= ``rise_db`` above that level (cfs: ignore-list / release the band);
        * ``'ambiguous'`` -- anything else at ``cut_verify_s`` (e.g. a slow partial decay).

        Only the track that was EMITTED can be 'confirmed' by disappearing; a bystander line within reach of the bell that
        merely ends near the cut gets no verdict from that (it may still read 'held' / 'false_cut', which is information
        about the EQ path, not about a ring).
        """
        cfg = self.cfg
        t = float(self.last_ts if ts is None else ts)
        hz = float(freq_hz) if freq_hz is not None else (self.freq_of(float(band)) if band is not None else None)
        if hz is None or hz <= 0:
            return
        key = int(round(12.0 * math.log2(hz / 20.0)))          # ~semitone bucket: fine enough to key GEQ centres
        prev = self._cut_depth.get(key, 0.0)
        step = float(step_db) if step_db is not None else (float(depth_db) - prev if key in self._cut_depth else float(depth_db))
        self._cut_depth[key] = float(depth_db)
        if step >= 0.0:
            return                                              # a release / no change: nothing to verify
        lh = math.log2(hz)
        for c in self._cands:
            off = abs(math.log2(max(1e-9, c.freq_hz)) - lh)
            if off > 1.0 / 3.0 or not c.hist:
                continue                                        # beyond the neighbouring GEQ centre the bell does ~nothing
            # what the tap should lose at THIS line if it is programme: the change of the bell's attenuation at the line's
            # offset from the GEQ centre, for the narrow and the wide guess of the GEQ's Q
            att_new = sorted((self.bell_attenuation_db(float(depth_db), off, cfg.geq_q_max), self.bell_attenuation_db(float(depth_db), off, cfg.geq_q_min)))
            att_old = sorted((self.bell_attenuation_db(float(depth_db) - step, off, cfg.geq_q_max), self.bell_attenuation_db(float(depth_db) - step, off, cfg.geq_q_min)))
            lo = max(0.0, att_new[0] - att_old[1])
            hi = max(lo, att_new[1] - att_old[0])
            if hi < 0.5:
                continue                                        # too far from the bell to tell anything
            # the level the cut acts on: where the line had got to when the write landed (a line still climbing between
            # the emission and the write is judged from there, not from the emitted level; a plateau's wander is < the tolerance)
            before = max(h[1] for h in list(c.hist)[-3:])
            if c.last_emit_level_db is not None and c.last_emit_ts is not None and t - c.last_emit_ts < 1.0:
                before = max(before, c.last_emit_level_db)
            c.cut = (t, step, before, float(depth_db), lo, hi)
            c.cut_verdict = "pending"
            c.cut_deepen = False

    def _verify_cut(self, c: Candidate, ts: float, present: bool) -> None:
        """Post-cut classification for a track with a pending note_cut() (see :meth:`note_cut`)."""
        cfg = self.cfg
        if c.cut is None:
            return
        t_cut, step, before, depth, att_lo, att_hi = c.cut
        t0 = t_cut + cfg.cut_settle_s + c.settle_frames * cfg.frame_period_s   # the band must also have settled (LF)
        if ts < t0:
            return
        post = [h[1] for h in c.hist if h[0] >= t0]
        now = c.cluster_db if present else (c.hist[-1][1] if c.hist else before)
        drop = before - now
        in_response = ts <= t0 + cfg.cut_response_s
        tol = cfg.cut_false_tol_db
        verdict = None
        if drop >= att_hi + cfg.cut_confirm_extra_db:
            verdict = "confirmed"                   # fell away by far more than the bell: the loop lost its gain
        elif not present and c.misses >= 2:
            # the line is gone: time-locked to the cut = a killed ring (short loops collapse within a frame or two);
            # later, from a level that had settled at ~before - bell = a note that went through the EQ and then ended.
            # Only the line that was CUT can be confirmed by vanishing: a bystander note within reach of the bell that
            # happens to end just then says nothing about a ring (the watch is dropped without a verdict)
            last_seen_ts = c.hist[-1][0] if c.hist else ts
            if last_seen_ts > t0 + cfg.cut_response_s:
                verdict = "false_cut"
            elif c.emitted:
                verdict = "confirmed"
            else:
                c.cut = None
                c.cut_verdict = None
                return
        elif not in_response and drop < att_lo - tol:
            verdict = "insufficient"                # short of the bell / climbing again: excess larger than the cut
        elif ts >= t_cut + cfg.cut_verify_s:
            recent = post[-cfg.stable_frames:]
            flat = len(recent) >= 3 and (max(recent) - min(recent)) <= 2.0 * tol
            verdict = "held" if (att_lo - tol <= drop <= att_hi + tol and flat) else "ambiguous"
        if verdict is None:
            return
        c.cut_verdict = verdict
        c.cut = None
        c.cut_deepen = False
        if verdict == "false_cut":
            c.false_cut = True
            c.false_cut_level_db = now
            c.cuts_held = 0
        elif verdict in ("held", "insufficient"):
            c.cuts_held += 1
            # deepen (one re-emission per verdict) only for a line that was cut on plateau-class evidence and is loud-ish:
            # a howl that HOLDS after a 3 dB cut has e > 3 dB, which on any loop (tau <= ~70 ms) means it grew >= 40 dB/s
            # (FAST-RISE) or was already at its plateau when judged (LOUD / AT-ARM / PROBE); a line cut on a slower rise
            # alone had e < 3 dB and cannot be 'held' as a howl, so for it (and for quiet lines: tier B's level line) the
            # ambiguity is resolved as 'a note went through the EQ': cut once, report [L §4.3 tier A/B]
            plateau_class = any(e in ("fastrise", "loud", "probe", "established_at_arm") for e in c.emit_evidence)
            c.cut_deepen = bool(c.emitted and plateau_class and c.emit_peak_db is not None and c.emit_peak_db >= self.loudish_threshold_db)
        else:
            c.cuts_held = 0
        self.cut_log.append({"ts": round(ts, 3), "freq_hz": round(c.freq_hz, 1), "band": c.band, "step_db": step,
                             "depth_db": depth, "bell_db": [round(att_lo, 2), round(att_hi, 2)], "drop_db": round(drop, 2),
                             "verdict": verdict, "deepen": c.cut_deepen, "emitted": bool(c.emitted)})
        if len(self.cut_log) > 64:
            del self.cut_log[0]
        log.debug("cut near %.0f Hz: drop %.1f dB for a %+.1f dB step (bell %.1f-%.1f) -> %s%s", c.freq_hz, drop, step, att_lo, att_hi,
                  verdict, " (deepen)" if c.cut_deepen else "")

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
        return self._cm_rows(self._spec[ia], self._spec[ib], band)

    def _cm_rows(self, spec_a: tuple, spec_b: tuple, band: int) -> float:
        """:meth:`_cm` between two stored spectrum rows ``(frame index, levels, floor)`` (any age)."""
        if spec_a is spec_b:
            return 0.0
        va, fa = spec_a[3], spec_a[4]
        vb, fb = spec_b[3], spec_b[4]
        cfg = self.cfg
        k = cfg.neighbour_bins + 1
        thr_a, thr_b = fa + cfg.ref_signal_db, fb + cfg.ref_signal_db
        diffs = [vb[i] - va[i] for i in range(cfg.ref_band_lo, min(len(vb), cfg.ref_band_hi + 1))
                 if abs(i - band) > k and va[i] > thr_a and vb[i] > thr_b]
        if len(diffs) < cfg.ref_min_bands:
            # a quiet room (ring_out at home) has few bands 6 dB clear of the floor: fall back to every band that is
            # at least 3 dB above it at both instants, over the whole spectrum, so the server's own +1 dB steps
            # (room noise through the open mic rises with them) are still seen as common mode
            thr_a, thr_b = fa + 0.5 * cfg.ref_signal_db, fb + 0.5 * cfg.ref_signal_db
            diffs = [vb[i] - va[i] for i in range(len(vb)) if abs(i - band) > k and va[i] > thr_a and vb[i] > thr_b]
            if len(diffs) < 3:
                return 0.0
        return median(diffs)

    def _others_max(self, band: int) -> float:
        """Loudest band level outside ``band``'s own ±neighbour_bins neighbourhood on the current frame."""
        k = self.cfg.neighbour_bins
        for v, i in self._sorted_levels:
            if abs(i - band) > k:
                return v
        return -128.0

    @property
    def loud_threshold_db(self) -> float:
        """Absolute leg of LOUD: max(loud_line_db, min(arm p95 + loud_above_arm_db, loud_ceiling_db)). Referenced to the
        arm-time spectrum, so a loud show (or a display gain offset) raises the bar with it, up to loud_ceiling_db: within 6 dB
        of full scale the bar stops rising (a line there that dominates the frame is at an electronic limit)."""
        cfg = self.cfg
        if self.arm_p95_db is None:
            return cfg.loud_line_db
        return max(cfg.loud_line_db, min(self.arm_p95_db + cfg.loud_above_arm_db, cfg.loud_ceiling_db))

    @property
    def loudish_threshold_db(self) -> float:
        """Tier B's 'loud-ish' line: max(loudish_level_db, arm p95 + loudish_above_arm_db) -- one tier under LOUD on both legs."""
        cfg = self.cfg
        if self.arm_p95_db is None:
            return cfg.loudish_level_db
        return max(cfg.loudish_level_db, self.arm_p95_db + cfg.loudish_above_arm_db)

    def _loud(self, peak_db: float, band: int) -> bool:
        """LOUD evidence: at the analyser's clip flag (unless the spectrum is HOT: then 0.0 means 'display clipped', not
        'bus clipped'), or >= the arm-referenced loud line (max(loud_line_db, arm p95 + loud_above_arm_db): more than a
        sine-like source's ~18 dB concentration gain above the show's loudest programme bands [A §1]) AND loud_margin_db
        above every band outside its own neighbourhood."""
        cfg = self.cfg
        hot = "HOT_SPECTRUM" in self.flags
        if peak_db >= cfg.clip_db:
            # the analyser's clip flag: the bus clipped in this band = a full-scale line [L §1.4(3)] -- unless the display is HOT,
            # when 0.0 means 'the display clipped' and is neither a flag nor a level (programme reads 0.0 there too)
            return not hot
        thr = self.loud_threshold_db
        if hot and self.arm_max_db is not None:
            # a hot display (bus near full scale, or a display gain offset: passively the same picture) makes 'within 6 dB
            # of full scale' ordinary for programme; the line must then also stand loud_hot_over_max_db above the LOUDEST
            # cell the display showed over the arm window -- a level this show's programme demonstrably never reached
            thr = max(thr, self.arm_max_db + cfg.loud_hot_over_max_db)
        return peak_db >= thr and peak_db >= self._others_max(band) + cfg.loud_margin_db

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
            # the tracks were born together, or the sources now on them ARRIVED together (a repeated 8'+4' organ note
            # lands on its own coasting tracks: the fundamental's track is old, its onset is not)
            return (abs(ta.first_frame - tb.first_frame) <= cfg.co_onset_frames
                    or abs(ta.onset_fi - tb.onset_fi) <= cfg.co_onset_frames)

        K1 = cfg.stable_frames

        def recent_move(a: _Line) -> float | None:
            t = a.track
            if t is None or len(t.hist) < K1 or t.misses:
                return None
            return (a.cluster_db - t.hist[-K1][1]) - 0.0

        def independent(a: _Line, b: _Line) -> bool:
            # b APPEARED (a new track, >= independent_swing_db above its band's baseline) at least K1 frames after a,
            # while a's own level held within independent_hold_db from just before b's birth until now: a note came
            # in under a line that did not care -- not a partial of a's source (partials share onset and envelope)
            # [free-hand '_independent', L §5 P1/P12]. A swelling line (pad partials surfacing one by one) is not
            # 'holding', so its late partials still count.
            ta, tb = a.track, b.track
            if ta is None or tb is None or tb.first_frame <= ta.first_frame + cfg.co_onset_frames:
                return False
            if b.peak_db - self._base[b.band] < cfg.independent_swing_db:
                return False
            since = tb.first_frame - 2
            lv = [h[1] for h in ta.hist if h[4] >= since]
            if len(lv) < K1:
                return False
            if (max(lv + [a.cluster_db]) - min(lv + [a.cluster_db])) > cfg.independent_hold_db:
                return False
            # ... and the partner's BAND really was empty before its track was born (>= independent_swing_db below
            # its present level over the K1 frames before): a track re-formed on energy that was there all along
            # (masking, a neighbouring partial re-shaping the cluster, a common tone held across a chord change) is
            # track churn, not an arrival [free-hand measured the band swing, not the track birth]
            first = self._spec[0][0] if self._spec else 0
            rows = [self._spec[f - first][1] for f in range(tb.first_frame - K1, tb.first_frame) if 0 <= f - first < len(self._spec)]
            if len(rows) < 2:
                return False
            before = sorted(r[b.band] for r in rows)[len(rows) // 2]
            return b.peak_db - before >= cfg.independent_swing_db

        def pitch_independent(a: _Line, b: _Line) -> bool:
            # all partials of one source carry the SAME pitch modulation in cents (vibrato, drift, a scoop move Hk
            # exactly as they move H1); a loop's frequency is fixed by geometry [L §2.3, A §4.3(v): centroid sd
            # < 0.05 band]. One line dead still (range <= pitch_still_bands over K1) while the other, itself a clear
            # line (>= prominence_db, so its centroid is not estimation noise), moves >= pitch_moving_bands: two sources.
            ta, tb = a.track, b.track
            if ta is None or tb is None or len(ta.hist) < K1 or len(tb.hist) < K1 or ta.misses or tb.misses:
                return False
            ca = [h[3] for h in list(ta.hist)[-K1:]]
            cb = [h[3] for h in list(tb.hist)[-K1:]]
            ra, rb = max(ca) - min(ca), max(cb) - min(cb)
            if ra <= cfg.pitch_still_bands and rb >= cfg.pitch_moving_bands:
                return min(list(tb.proms)[-K1:]) >= cfg.prominence_db
            if rb <= cfg.pitch_still_bands and ra >= cfg.pitch_moving_bands:
                return min(list(ta.proms)[-K1:]) >= cfg.prominence_db
            return False

        def co_moving(a: _Line, b: _Line) -> bool:
            # partials of one source share its envelope; an independent line (a ring growing through a held
            # note's harmonic position, a syllable landing on a ring's harmonic position) does not
            # [L §5 P1 'co-moving']. Without K1 frames of history on both sides the benefit of the doubt goes
            # to 'co-moving' (a pad swell's partials surface one after another; treating newcomers as
            # independent lets pad swells, speech formants and crowd noise through) -- unless the
            # newcomer arrived under a line that held dead steady through its arrival (independent()) or the two
            # do not share their pitch modulation (pitch_independent()).
            if independent(a, b) or independent(b, a) or pitch_independent(a, b):
                return False
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
            # co-onset [L §2.3], so two rings that happen to sit an exact octave apart are not dismissed.
            if not family and h2 is not None and h2.peak_db >= ln.peak_db - cfg.h2_pair_rel_db and co_onset(ln, h2):
                family = True
                names.append("H2pair")
            harm = False
            for kk, off in _PARTIAL_OFFSETS:               # am I somebody's H2..H5?
                base = present_near(ln.centroid - off)
                if base is None or base is ln or base.prom_db < cfg.partial_prominence_db or not co_moving(ln, base):
                    continue
                if ln.peak_db > base.peak_db + cfg.harm_over_base_max_db:
                    # instrument partials run 0..-15 dB re their fundamental, up to ~+10 dB for formant-boosted vowels
                    # and HPF'd bass [A §4.2, L §2.1]: a line this far ABOVE its would-be fundamental is not its harmonic
                    continue
                if kk >= 4 and base.peak_db < ln.peak_db + cfg.subharm_hi_margin_db:
                    # H4/H5 only under a base that is louder (a note's fundamental/formant region, a clipped howl
                    # under its own H5); testing H4/H5 under ANY base -- or under a base owning two partials -- costs
                    # coincidence vetoes on real rings under dense music (chord partials land on those slots continually)
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
        c.run_lv = deque(maxlen=max(8, cfg.rise_window_frames))
        c.first_centroid = ln.centroid
        c.centroid = ln.centroid
        c.first_frame = self.frames_seen
        c.onset_fi = self.frames_seen
        c.run_start_fi = self.frames_seen
        c.settle_frames = self._settle_frames[ln.band]
        # 'born at arm' is a fact about the first arm_frames only: a line that appears later at the pitch and level
        # of one that was sounding at arm (an organ note that RECURS) is a new source seen to start, and a track that
        # was there at arm and is lost for longer than coast_frames has to re-earn everything like any newcomer.
        c.born_at_arm = self.frames_seen < cfg.arm_frames
        if not c.born_at_arm:
            self._backfill(c, ln)
            self._seed_run(c, ln)
        return c

    def _seed_run(self, c: Candidate, ln: _Line) -> None:
        """Where the band settles within a frame (>= ~300 Hz), the line's own visible pre-birth climb (back-fill, without
        the virtual bed sample) is part of its presence run: those are observed levels of this line rising continuously
        (each step <= onset_step_db, i.e. not an attack landing) before it was prominent enough to be a track, so the RISE
        reference may start there. A slow ring under a bed is a 6 dB-prominent track only for the last part of its climb;
        counting from birth alone costs it up to backfill_frames of reference. LF bands are excluded (their pre-birth frames
        are the analyser's own step response)."""
        cfg = self.cfg
        if c.settle_frames > 1 or len(c.pre_lv) < 3:
            return
        b, n = ln.band, len(self.band_hz)
        # in CLUSTER terms (power sum of the peak band and its two neighbours), the currency of run_lv -- a peak-band
        # level against later cluster levels would credit a broad hump with its own width as 'rise'
        climb = [(_db(sum(_pw(row[1][j]) for j in (b - 1, b, b + 1) if 0 <= j < n)), row) for _lv, row in c.pre_lv[1:]]
        # keep the trailing stretch whose increments are a continuous climb (no attack-sized jump)
        k = len(climb) - 1
        while k > 0 and 0.0 < climb[k][0] - climb[k - 1][0] <= cfg.onset_step_db:
            k -= 1
        climb = climb[k:]
        if len(climb) < 2 or ln.cluster_db - climb[-1][0] > cfg.onset_step_db or ln.cluster_db <= climb[-1][0]:
            return
        for lv, row in climb[1:]:                            # the first one plays the role of the unscored settle frame
            c.run_lv.append((lv, row[0], row, row[1][b]))
            c.run_settled += 1
        c.run_frames = len(climb)
        c.run_start_fi = climb[0][1][0]
        c.run_max_db = max(x[0] for x in climb)
        c.prev_cluster_db = climb[-1][0]
        c.rise_streak = len(climb) - 1

    def _backfill(self, c: Candidate, ln: _Line) -> None:
        """Give a newborn track its band's own climb from the frames before it was promoted to a track: walk back
        through the spectrum store while the cluster level keeps falling (going back in time), i.e. while the line
        was rising, and keep one more frame as the level it rose out of. A fast ring in a loud mix is a trackable
        line for only 2-4 frames before its plateau; a note onset gives one or two frames. [free-hand _backfill]"""
        cfg = self.cfg
        b = ln.band
        n = len(self.band_hz)
        rows = list(self._spec)
        if len(rows) < 2 or cfg.backfill_frames <= 0:
            return
        look = rows[:-1][-cfg.backfill_frames:]
        floor = median(row[1][b] for row in look)          # what the band read before the line: the bed there
        out: list[tuple[float, tuple]] = []
        newer = ln.peak_db
        first_row = look[0]
        for row in reversed(look):
            v = row[1][b]                      # the peak band itself: the 3-band cluster is 2/3 bed before the line dominates
            nb = max(row[1][j] for j in (b - 1, b + 1) if 0 <= j < n)
            if v < floor + cfg.backfill_floor_margin_db or v >= newer - 0.5 * cfg.onset_flat_db or v < nb - 1.0:
                first_row = row                # bed noise, not lower any more, or not the local bump: the climb starts after this
                break
            out.append((v, row))
            newer = v
        out.reverse()
        # the virtual first sample is the bed level the line rose out of (its spectrum: the frame just before the climb)
        c.pre_lv = [(floor, first_row)] + out  # used by FAST-RISE only; the RISE run logic never sees pre-birth frames

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
        if qualified:
            c.qual_frames += 1
            if c.qual_frames == 3 and not c.born_at_arm:
                c.ever_qualified = True
                if not self._surfaced_by_step(c):
                    self._events.append(ts)           # programme activity: a new line appeared and held (see programme_present)
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
                # ... and still going up INTO this frame (last two settled increments positive): a line that stepped up
                # and then sat flat for a few frames was not climbing -- the next jump is another arrival (a strummed
                # chord's second string, a syllable), not masked loop growth
                still_up = tail_lv[-1] - tail_lv[-2] > 0.25 * cfg.onset_flat_db and tail_lv[-2] - tail_lv[-3] > 0.25 * cfg.onset_flat_db
                rising_before = sl >= cfg.onset_rising_db_per_s and resid <= cfg.onset_rising_resid_db and still_up
        if c.run_frames > 1 and not rising_before and (inc > cfg.onset_step_db or gap > cfg.stable_frames):
            # A jump out of a flat (or falling) line within ONE frame is an onset landing on the track (a note
            # re-struck at the same pitch, a syllable, a note arriving on a noise bump): a new source, so the
            # presence run restarts and the analyser-settle allowance applies again (the partially integrated
            # remainder frame is not scored). Loop growth is continuous and loses at most its first frame. The same
            # after the line was absent for more than K1 frames without having been climbing: whatever comes back
            # must re-earn its rise. [L §1.3]
            c.run_frames = 1
            c.run_lv.clear()
            c.run_low = None
            c.run_settled = 0
            c.run_start_fi = ref
            c.run_max_db = ln.cluster_db
            # whatever is on the track from now on was SEEN TO START: it is not the line that was sounding when we
            # armed (a note re-struck at the at-arm pitch after a rest shorter than the coast window, a note landing
            # on a bed bump that happened to be a 6 dB local maximum on the arm frames) -- 'born at arm' and its
            # evidence belong to the line, not to the track slot [F1; verifier AV03 / X1 s11]
            c.born_at_arm = False
            c.arm_evidence = False
            c.onset_fi = ref
            if c.ever_qualified and inc > cfg.onset_step_db:
                self._events.append(ts)               # programme activity: a note re-struck / a syllable on an existing line
        elif (c.settle_frames >= 2 and len(c.hist) >= 2 * c.settle_frames
              and ln.cluster_db - c.hist[-c.settle_frames][1] > cfg.lf_onset_step_db
              and abs(c.hist[-c.settle_frames][1] - c.hist[-2 * c.settle_frames][1]) < cfg.lf_onset_flat_db):
            # LF version of the onset jump: below ~300 Hz an instant onset is smeared over settle_frames, so the
            # jump is measured over that window (a note: >= 8-10 dB within one settle time out of a flat line; an
            # LF loop, long and low-excess, grows <= ~5 dB per settle time and was already rising before).
            c.run_frames = 1
            c.run_lv.clear()
            c.run_low = None
            c.run_settled = 0
            c.run_start_fi = ref
            c.run_max_db = ln.cluster_db
            c.born_at_arm = False
            c.arm_evidence = False
            c.onset_fi = ref
        elif c.settle_frames >= 2 and ln.cluster_db < c.run_max_db - cfg.restart_drop_db:
            # The line has fallen well below its own recent maximum: whatever rises next is a RE-excitation (a
            # driven room mode or a re-struck note seen through a slow LF band: the analyser's step response
            # would otherwise read as growth), so the run restarts and must settle again. Only where the band is
            # slow (settle >= 2 frames, below ~300 Hz): higher up the analyser is instant and the onset-jump rule
            # above already separates re-struck sources from continuous growth. [A §6, L §1.6]
            c.run_frames = 1
            c.run_lv.clear()
            c.run_low = None
            c.run_settled = 0
            c.run_start_fi = ref
            c.run_max_db = ln.cluster_db
        c.run_max_db = max(c.run_max_db, ln.cluster_db)
        c.rise_streak = (c.rise_streak + 1 if gap == 1 else 1) if (c.prev_cluster_db is not None and inc >= cfg.onset_flat_db) else 0
        c.prev_cluster_db = ln.cluster_db
        c.prev_inc_db = inc
        c.prev_ts = ts
        spec_now = self._spec[-1]
        if c.run_frames > c.settle_frames:
            c.run_lv.append((ln.cluster_db, ref, spec_now, ln.peak_db))
            c.run_settled += 1
        rise = 0.0
        nlv = len(c.run_lv)
        if nlv >= 2:
            # reference = a LOW QUANTILE of the run's recent settled levels (not the minimum: on a flat line with
            # ±2 dB flutter the minimum keeps falling and would manufacture a 'rise') ...
            srt = sorted(c.run_lv, key=lambda x: x[0])
            q = srt[int(cfg.rise_quantile * (nlv - 1))]
            # ... remembered as a LOW-WATER MARK for the whole run: the deque only spans the last 64 frames, and a
            # loop creeping at 1-2 dB/s [L §1.3: e = 0.1 dB on a 35-70 ms loop] needs the level it started from,
            # however long ago, or its rise can never add up. The mark is committed once the window holds
            # rise_ref_min_samples (the low quantile of 2-3 samples is just the minimum) and is reset only when
            # the run restarts (onset jump, long gap, LF re-excitation) — the same rules as before.
            if c.run_low is not None and abs(ln.centroid - c.run_low[3]) > 2.0 * cfg.centroid_tol_bands:
                # the mark belongs to a line at THIS frequency: a loop does not move while it creeps up [L §2.3];
                # a programme partial that has drifted half a band since is another pitch, and a level reference
                # across a pitch change means nothing -> re-anchor
                c.run_low = None
            if nlv >= cfg.rise_ref_min_samples and (c.run_low is None or q[0] < c.run_low[0]):
                c.run_low = (q[0], q[1], q[2], ln.centroid, q[3])
            lv0, fi0, sp0, pk0 = (c.run_low[0], c.run_low[1], c.run_low[2], c.run_low[4]) if (c.run_low is not None and c.run_low[0] < q[0]) else (q[0], q[1], q[2], q[3])
            # net of any common-mode raise beyond cm_deadband_db (a mix breathes +-1..2 dB; a fader/autogain move
            # that could fake a 6 dB rise is larger than that and is subtracted in full beyond the dead band). The
            # reference carries its own spectrum, so the comparison does not depend on how far back the store goes.
            rise = (ln.cluster_db - lv0) - max(0.0, self._cm_rows(sp0, spec_now, ln.band) - cfg.cm_deadband_db)
            # ... and it must be the line's OWN rise: the cluster (peak band + neighbours) also grows when a second
            # programme line lands one band away and joins it (up to +3 dB of summed power with the peak unchanged),
            # so the cluster rise counts only up to the peak band's own rise + 3 dB (an edge line whose louder band
            # alternates loses at most those 3 dB on its peak reading while its cluster is invariant [A §3, §4.4])
            rise = min(rise, (ln.peak_db - pk0) + 3.0)
            # the part of the rise that is older than one analyser-settle time: a programme onset seen through a
            # slow LF band is a rise confined to ~settle_frames; loop growth was already under way before that
            # and has continued since [L §1.6]. (At HF settle = 1 frame: costs nothing.)
            k_old = c.settle_frames + 1
            if nlv > k_old:
                lv1, fi1, sp1 = c.run_lv[-1 - k_old][:3]
                c.rise_old_db = (lv1 - lv0) - max(0.0, self._cm_rows(sp0, sp1, ln.band) - cfg.cm_deadband_db)
            else:
                c.rise_old_db = 0.0
        c.rise_db = rise
        # GROWTH: a clean dB-linear climb over the last growth_frames settled frames (slope >= growth_min_db_per_s,
        # fit residual <= growth_resid_db) -- exponential loop growth is exactly linear in dB [L §1.3]; no upper
        # rate bound, and LF-safe because the run only starts after the analyser settle allowance.
        c.growth_db_per_s = 0.0
        W = cfg.growth_frames
        if cfg.growth_rise_db < cfg.rise_db and nlv >= W:
            spW = c.run_lv[-W][2]
            ys = [x[0] - max(0.0, self._cm_rows(spW, x[2], ln.band)) for x in list(c.run_lv)[-W:]]
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
            c.veto_total += 1
            # Instrument partials onset WITH (or, through the analyser at LF, before) their fundamental; distortion
            # partials of a howl appear only after the fundamental has grown loud [A §4.3]. A veto that first fires
            # after the line has already risen late_partials_rise_db does not count.
            if c.first_veto_rise is None:
                c.first_veto_rise = rise
            if c.first_veto_rise < cfg.late_partials_rise_db:
                c.veto_frames += 1
                counted = True
        c.hist.append((ts, ln.cluster_db, ln.peak_db, ln.centroid, ref, qualified, counted))
        self._fast_rise(c, ln)
        # co-growth bookkeeping: how much this line has swelled over the last second, if it is still going up
        # (>= 1 dB over the last 8 frames); a plateaued line is not 'swelling' however much it rose before
        c.swell_db = 0.0
        c.swell_rate = 0.0
        if len(c.hist) >= 4:
            w = list(c.hist)[-cfg.cogrowth_window_frames:]
            recent = w[-8:]
            if ln.cluster_db - min(h[1] for h in recent) >= cfg.onset_flat_db:
                lo = min(w, key=lambda h: h[1])
                # net of common mode: lines riding the mix's own dynamics or a fader together are the common-mode
                # rule's business; co-growth is about lines swelling TOGETHER RELATIVE to the rest of the spectrum
                c.swell_db = (ln.cluster_db - lo[1]) - max(0.0, self._cm(lo[4], ref, ln.band))
                c.swell_rate = (ln.cluster_db - recent[0][1]) / max(cfg.frame_period_s, ts - recent[0][0])
        # -- P3 over the drift window: a line that walked more than drift_max_bands is a NEW line ------------
        # (glide, portamento, scoop, melody step, or a hand-held ring hopping to the next loop candidate): the
        # track is re-born here with no history, so everything (K1 stability, rise, at-arm) must be re-earned
        # at the new frequency, and it remembers that it arrived by gliding. [L §2.3, A §4.3 vi]
        dc = [h[3] for h in list(c.hist)[-cfg.drift_window_frames:]]
        if len(dc) >= 2 and (max(dc) - min(dc)) > cfg.drift_max_bands:
            if c.ever_qualified:
                self._events.append(ts)               # programme activity: a line that moved in pitch (melody step / glide)
            self._rebirth(c, ts)
            c.glided = True
        # -- AT-ARM evidence: judged once, on what the line looked like when we armed --------------------
        K1 = cfg.stable_frames
        if c.born_at_arm and c.frames == K1:
            # ... over its first K1 matched frames, which for a line that WAS there at arm (a plateaued howl is
            # present on every frame short of a masking transient) fall within its first K1 + 2 frames of life: a
            # track that coasted longer than that before collecting K1 frames is judging something else's frames
            lv = sorted(h[2] for h in c.hist)[len(c.hist) // 2]
            pm = sorted(c.proms)[len(c.proms) // 2]
            c.arm_evidence = (lv >= cfg.arm_line_min_level_db and pm >= cfg.strong_prominence_db
                              and ref - c.first_frame <= K1 + 1)

    def _fast_rise(self, c: Candidate, ln: _Line) -> None:
        """FAST-RISE evidence (G1): over the track's last frames plus its pre-birth back-fill, a trailing run of
        >= max(fast_rise_min_steps, settle + 2) consecutive increments each >= fast_rise_step_db, summing (net of
        common mode) to >= fast_rise_db, none carrying more than fast_rise_max_share of the rise, reaching a new high.
        The run may end on this frame or the one before (the limiter knee). Sticky once seen; cleared when the line
        falls restart_drop_db under its run maximum (a transient that shot up and is coming down)."""
        cfg = self.cfg
        if c.fast_rise_db > 0.0:
            if ln.cluster_db < c.run_max_db - cfg.restart_drop_db:
                c.fast_rise_db = 0.0
            return
        n_req = max(cfg.fast_rise_min_steps, (c.settle_frames + 2) if c.settle_frames >= 2 else 0)
        m = n_req + 3
        hl = list(c.hist)
        own = hl[-m:]
        k0 = len(own) - 1                                   # contiguous tail only: a coast gap breaks the increments
        while k0 > 0 and own[k0][4] - own[k0 - 1][4] == 1:
            k0 -= 1
        own = own[k0:]
        seq: list[tuple[float, Any, int | None]] = []
        has_bed = bool(k0 == 0 and len(hl) <= m and c.pre_lv)
        if has_bed:                                         # the tail reaches back to birth: prepend the back-fill
            seq = [(lv, sp, None) for lv, sp in c.pre_lv]   # (seq[0] = the bed the line rose out of: the step out of the bed
        seq += [(h[2], None, h[4]) for h in own]            #  is an increment of a line that was below it a frame earlier)
        if len(seq) < n_req + 1:
            return
        lv = [x[0] for x in seq]
        incs = [b - a for a, b in zip(lv, lv[1:])]
        older = [h[2] for h in hl[:len(hl) - len(own)]]
        older_max = max(older) if older else None
        sp_now = self._spec[-1]
        step = cfg.fast_rise_step_db
        # the bed step is an inferred increment, not an observed one: with it, two observed increments would do, and two
        # increments (100-150 ms of climb) are also what a soft attack shows -- so it counts only for a line whose level is
        # loud-ish (tier B's line: max(loudish_level_db, arm p95 + loudish_above_arm_db))
        short_ok_db = self.loudish_threshold_db

        def accept(start: int, end: int, big: float, top2: float) -> bool:
            """Common tail of both variants: shares of the rise, new high, net of common mode; sets the evidence."""
            rise = lv[end] - lv[start]
            if rise <= 0.0 or big > cfg.fast_rise_max_share * rise or top2 > 0.75 * rise:
                return False                                # one or two increments carry the rise: an attack
            prior = lv[:start] + ([older_max] if older_max is not None else [])
            if prior and lv[end] < max(prior) + 3.0:
                return False                                # not a new high for this line (re-growth must clear its past)
            sp0, fi0 = seq[start][1], seq[start][2]
            cm = self._cm_rows(sp0, sp_now, ln.band) if sp0 is not None else self._cm(fi0, sp_now[0], ln.band)
            net = rise - max(0.0, cm - cfg.cm_deadband_db)  # a fader shove lifts every band: not the line's own rise
            if net < cfg.fast_rise_db:
                return False
            c.fast_rise_db = net
            c.fast_rate_db_per_s = rise / max(1, end - start) / cfg.frame_period_s
            return True

        for end in (len(incs), len(incs) - 1):              # the climb ends now, or one frame ago (limiter knee)
            if end < n_req:
                continue
            if end == len(incs) - 1 and incs[-1] < -cfg.onset_flat_db:
                continue                                    # fell straight back: a transient, not a plateau knee
            if c.settle_frames >= 2:
                # LF: the band's own step response smears any onset over ~settle frames, so only an unbroken run of
                # increments LONGER than that (settle + 2) is growth the analyser could not have manufactured
                i = end - 1
                run: list[float] = []
                while i >= 0 and incs[i] >= step:
                    run.append(incs[i])
                    i -= 1
                if len(run) >= n_req:
                    srt = sorted(run, reverse=True)
                    if accept(i + 1, end, srt[0], srt[0] + (srt[1] if len(srt) > 1 else 0.0)):
                        return
            else:
                # HF (analyser settles within a frame): >= 3 of the last <= 5 increments are real steps (>= 2 dB and
                # >= 15 % of the rise) and the climb was still going at the window's end; one flat increment inside
                # the window is tolerated (a programme partial sharing the band for a frame, a frame-boundary split)
                for w in (5, 4, 3):
                    start = end - w
                    if start < 0:
                        continue
                    win = incs[start:end]
                    tot = lv[end] - lv[start]
                    if tot <= 0.0 or win[-1] < 0.5 * step:
                        continue
                    big_enough = max(step, 0.15 * tot)
                    cnt = sum(1 for d in win if d >= big_enough)
                    if cnt < cfg.fast_rise_min_steps:
                        continue
                    if has_bed and start == 0 and win[0] >= big_enough and cnt - 1 < cfg.fast_rise_min_steps and lv[end] < short_ok_db:
                        continue                            # only two OBSERVED increments and not loud-ish: a soft attack too
                    srt = sorted(win, reverse=True)
                    if accept(start, end, srt[0], srt[0] + srt[1]):
                        return

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
        c.onset_fi = self.frames_seen
        c.first_centroid = c.centroid
        c.born_at_arm = False
        c.arm_evidence = False
        c.veto_frames = 1 if last[6] else 0
        c.first_veto_rise = 0.0 if last[6] else None
        c.run_frames = 1
        c.run_lv.clear()
        c.run_low = None
        c.run_settled = 0
        c.run_start_fi = self.frames_seen
        c.run_max_db = last[1]
        c.rise_db = 0.0
        c.growth_db_per_s = 0.0
        c.pre_lv = []
        c.fast_rise_db = 0.0
        c.base_streak = 0
        c.cut = None
        c.cut_verdict = None
        c.cut_deepen = False
        c.cuts_held = 0
        c.emit_evidence = ()
        c.emit_peak_db = None
        c.false_cut = False
        c.false_cut_level_db = None
        c.last_emit_level_db = None
        c.probe_hits = 0
        c.probe_linear = 0
        c.probe_pinned = 0
        c.stationary = False
        c.judged_steps = set()
        c.steps_seen = 0
        c.qual_frames = 1 if last[5] else 0
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
        for k, t_s, delta in self._steps:
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
            if len(c.judged_steps) > 80:
                live = {st[0] for st in self._steps}
                c.judged_steps &= live
            c.steps_seen += 1
            d_line = median([p[0] for p in post]) - median([p[0] for p in pre])
            d_ref = self._cm(pre[-1][1], post[len(post) // 2][1], c.band)
            pre_levels = [p[0] for p in pre]
            post_levels = [p[0] for p in post]
            if (max(pre_levels) - min(pre_levels)) > cfg.probe_steady_db or (max(post_levels) - min(post_levels)) > 2 * cfg.probe_steady_db:
                continue                      # something else was moving the line around the step: no verdict
            # Three physically distinct answers to a step of Δ dB on the bus master [L §1.5, A §0.3]:
            #  * HIT      the band moved by >= Δ + probe_over_db (or, for a back-off, fell by that much more / died):
            #             regenerative gain 1/(1-g) within ~3 dB of threshold -- loop evidence;
            #  * FOLLOWS  it moved ~1 dB per dB (up to Δ + 1): a source in the ROOM heard through the open mic
            #             (projector / HVAC whine, an amp, spill of playback) -- stationary, never cut;
            #  * PINNED   it did not move at all (< probe_pinned_fraction·Δ): either electrical (hum, DI/playback at the
            #             pre-fader tap -- absent by the ring_out contract, and hum carries an exact family) or a howl
            #             whose plateau is set DOWNSTREAM of the tap (speaker limiter: 0 dB/dB; channel compressor:
            #             ~1/ratio) -- so 'pinned' is NOT programme evidence and must not make the line STATIONARY.
            hit = follows = pinned = False
            if delta > 0:
                hit = d_line >= delta + cfg.probe_over_db and d_ref <= delta + 0.5
                pinned = (not hit) and d_line < cfg.probe_pinned_fraction * delta
                follows = (not hit) and (not pinned) and d_line <= delta + 1.0
            elif delta < 0:
                hit = d_line <= delta - cfg.probe_over_db and d_ref >= delta - 0.5
                pinned = (not hit) and d_line > cfg.probe_pinned_fraction * delta
                follows = (not hit) and (not pinned) and d_line >= delta - 1.0
            if hit:
                c.probe_hits += 1
            elif follows:
                c.probe_linear += 1
            elif pinned:
                c.probe_pinned += 1
            # 'stationary' (follows the gain 1 dB per dB: a room source / spill) only while no step has ever shown an
            # over-response and the 1:1 answers are not outnumbered by pinned ones; one noisy step must not condemn a
            # line that answered others
            c.stationary = c.probe_hits == 0 and c.probe_linear >= 1 and c.probe_linear >= c.probe_pinned

    def _surfaced_by_step(self, c: Candidate) -> bool:
        """ring_out: was this track born within a probe window after one of the server's own gain steps? Then it is a
        stationary line (hum partial, HVAC, room tone) that the rising master lifted over the tracking threshold, not a
        programme event -- programme_present() must not count it."""
        return any(t_s - 1e-9 <= c.first_ts <= t_s + self.cfg.probe_window_s for _k, t_s, _d in self._steps)

    def _cogrowth_mates(self, c: Candidate) -> int:
        """Other lines swelling WITH ``c``: present run started within cogrowth_coborn_frames of c's, currently swelling
        (swell_db >= cogrowth_rise_db) at 0.5-2x c's swell, and not part of c's own cluster (> 1.5 bands away)."""
        cfg = self.cfg
        if c.swell_rate <= 0.0:
            return 0
        # lines of ONE swelling source become trackable at different moments according to the bed level at each
        # one's band (+-3..4 dB humps [C §2.9]): allow the birth spread that 6 dB of level difference costs at this rate
        tol = max(cfg.cogrowth_coborn_frames, int(math.ceil(6.0 / c.swell_rate / cfg.frame_period_s)))
        mates = 0
        for o in self._swelling:
            if o is c or abs(o.centroid - c.centroid) <= 1.5:
                continue
            if abs(o.run_start_fi - c.run_start_fi) > tol:
                continue
            # same source => same dB slope (within 2x: a note still close to the bed shows a compressed slope);
            # two rings that happen to start together grow at their own, unrelated e/tau
            if 0.5 * c.swell_rate <= o.swell_rate <= 2.0 * c.swell_rate:
                mates += 1
        return mates

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
        if c.swell_db >= cfg.cogrowth_rise_db and not c.emitted and self._cogrowth_mates(c) >= cfg.cogrowth_lines:
            c.cogrow_streak += 1
        else:
            c.cogrow_streak = 0
        if c.cogrow_streak >= 2:
            musical.append("cogrowth")          # >= 2 OTHER lines born with me and swelling at my rate on consecutive
                                                # frames: one swelling source (pad / organ swell / fade-in), not a loop --
                                                # rings start alone and grow at their own e/tau [A §5, L §2.3]. A single
                                                # frame of coincidence (a hat, a syllable next to a ring) does nothing.
            if c.cogrow_streak >= 3:
                # the rise accumulated so far is that common swell's, not a loop's: re-anchor the RISE reference so
                # that once the chord has plateaued only a rise the line then earns ALONE can make it STRONG
                c.run_lv.clear()
                c.run_low = None
                c.run_settled = 0
                c.rise_db = 0.0
                c.fast_rise_db = 0.0
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
        # age: K1 frames of continuous run; when the increments that restarted the run were themselves the watched
        # frame-by-frame climb of a fast ring (FAST-RISE), they were growth and not a source landing on the track, so
        # the track's own matched age counts instead (a 150 dB/s howl plateaus 2-3 frames after it becomes visible)
        aged = c.run_frames >= K1 or (c.fast_rise_db > 0.0 and c.frames >= K1 and len(hl) >= K1)
        stable = aged and (max(cents) - min(cents)) <= 2.0 * cfg.centroid_tol_bands
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
        if sustained and aged:
            reasons.append("sustained")
        if new_energy:
            reasons.append("new_energy")
        elif c.born_at_arm:
            reasons.append("at_arm")
        if in_window:
            reasons.append("in_window")
        if c.glided:
            reasons.append("glided_in")
        # frozen display value (peak-hold reaching the stream): a live line through a real analyser never repeats its
        # level bit-for-bit frame after frame [A §2]; lines at the clip flag legitimately read 0.00 repeatedly
        # (a tone clipping the desk reads a clamped, hence constant, value: 0.0 in its band, up to 3 dB less in each
        # band when it sits on an edge -- those are exempt)
        pk = [h[2] for h in hl[-cfg.frozen_frames:]]
        if (len(pk) >= cfg.frozen_frames and c.level_db < cfg.clip_db - 3.0 and not c.misses
                and sum(1 for a, b in zip(pk, pk[1:]) if a == b) >= cfg.frozen_frames - 1 and self._skirt_frozen(c.band)):
            c.frozen_until = self.frames_seen + cfg.confirm_frames
        frozen = self.frames_seen < c.frozen_until
        if frozen:
            self.flags.add("FROZEN_LINES")
            reasons.append("frozen")
        base_ok = (qualified_now and narrow and not musical and stable and sustained and in_window and level_ok
                   and not frozen and (new_energy or c.born_at_arm))
        c.base_streak = c.base_streak + 1 if base_ok else 0
        rise = c.rise_db
        evidence: list[str] = []
        lf_ok = c.settle_frames <= 1 or c.rise_old_db >= 0.5 * cfg.rise_db
        # under a slow display release (measured), programme lines are stretched into continuous 'sustained' tracks whose
        # dynamics read as rises: growth evidence then counts only where the family test is conclusive on its own
        # (>= strong_prominence_db) -- the instrument is misconfigured and the flag says so [A §2 decay]
        growth_ok = "SLOW_RELEASE" not in self.flags or c.prominence_db >= cfg.strong_prominence_db
        if rise >= cfg.rise_db and lf_ok and growth_ok:
            evidence.append(f"rise{rise:.0f}dB")
        elif cfg.growth_rise_db < cfg.rise_db and rise >= cfg.growth_rise_db and c.growth_db_per_s >= cfg.growth_min_db_per_s and lf_ok:
            evidence.append(f"growth{c.growth_db_per_s:.0f}dB/s")     # optional early upgrade (OFF by default)
        if c.fast_rise_db >= cfg.fast_rise_db and growth_ok:
            # >= 3 consecutive frame-by-frame increments (incl. the pre-birth frames) adding up to >= 12 dB: the
            # line was WATCHED growing exponentially into its plateau [L §1.3]; an attack is 1-2 increments
            evidence.append(f"fastrise{c.fast_rise_db:.0f}dB@{c.fast_rate_db_per_s:.0f}dB/s")
        # The two PLATEAU evidences (LOUD, AT-ARM) say 'this steady line is implausible as programme'; a line that
        # answered the server's own gain steps 1 dB per dB (c.stationary) is a source in the room heard through the
        # mic, however loud or established, so both are void for it. (RISE / FAST-RISE / PROBE are changes the line
        # made on its own and stay admissible: a loop far below threshold also answers early steps ~1:1.) [L §1.5]
        if self._loud(c.level_db, c.band) and not c.stationary and (
                c.freq_hz >= cfg.window_low_hz_watch or (in_window and c.level_db >= cfg.clip_db)):
            # LOUD says nothing below ~160 Hz short of the clip flag: LF loops are long (sub DSP + distance >= 15 ms)
            # with small excess, so an LF howl shows seconds of observable growth, while kick / 808 / toms routinely
            # put a single LF band far above everything else [L §3.2 (e), A §4.2]. A band PINNED at the desk's clip
            # flag for K1 with a stable centroid inside a declared LF window is not a kick (100 ms, glides) nor an
            # 808 (glides, decays): it is a full-scale howl [L §1.4 (3)].
            evidence.append("loud")
        armed_for = ts - self.first_ts if self.first_ts is not None else 0.0
        arm_lv = [h[1] for h in hl[-cfg.arm_range_frames:]]
        presence = c.frames / max(1, self.frames_seen - c.first_frame + 1)
        arm_steady = (max(arm_lv) - min(arm_lv)) <= cfg.arm_range_db and presence >= cfg.arm_presence
        loud_now = "loud" in evidence
        # an established howl carries no harmonic family for its whole life (coincidental vetoes are brief); a line that
        # carried a co-moving family on a good part of its frames is somebody's note even at a moment when interference
        # makes the family fail the co-movement test (a howl: none of its frames; an instrument note: nearly all of them)
        family_free_life = c.veto_total <= cfg.arm_family_life_max * max(1, c.frames)
        if (c.arm_evidence and arm_steady and family_free_life and not c.stationary
                and (c.level_db >= cfg.arm_fast_level_db or armed_for >= cfg.arm_confirm_s)):
            # an established line found at arm: immediately if loud-ish, otherwise once it has outlasted a note
            # (a steady family-less NOTE that happened to be sounding when we armed looks identical for as long as
            # it lasts; organ/flute melody notes end within ~0.3-1 s, a plateaued ring does not)
            cohort = sum(1 for o in self._arm_cohort
                         if o is not c and abs(o.centroid - c.centroid) > 1.5 and abs(o.level_db - c.level_db) <= cfg.arm_cohort_rel_db)
            if cohort >= cfg.cogrowth_lines:
                # three or more family-less steady lines at comparable levels already sounding when we armed are a
                # chord / an organ registration, not three established howls: a howl at its limiter suppresses the
                # next candidate, so simultaneous plateaued howls beyond two (two open mics) do not occur [L §2.3]
                reasons.append("at_arm_cohort")
            elif cfg.mode == "ringout" and not loud_now and c.level_db < cfg.arm_fast_level_db and c.steps_seen < cfg.probe_min_hits:
                # ring_out: nothing regenerative can be ESTABLISHED at arm unless the system is already howling (then
                # it is LOUD); a whine / hum / HVAC line present at arm must first be judged on probe_min_hits of the
                # server's own gain steps (1 dB/dB => STATIONARY, never cut; a plateau held downstream of the tap
                # ignores the steps and stays eligible) before AT-ARM may emit it [G4]
                reasons.append("at_arm_awaiting_probe")
            else:
                evidence.append("established_at_arm")
        if c.probe_hits >= cfg.probe_min_hits:
            # over-response to the server's own gain steps, twice: a programme crescendo or a bass note can coincide
            # with one step by chance, not with two [L §1.5, §5 P7]
            evidence.append(f"probe{c.probe_hits}")
        if c.false_cut and c.false_cut_level_db is not None and (
                c.cluster_db >= c.false_cut_level_db + cfg.rise_db or c.fast_rise_db >= cfg.fast_rise_db and c.run_max_db > c.false_cut_level_db + cfg.rise_db):
            # the line has grown a doubling above the level at which it was written off: a limiter-held howl whose excess
            # rose again, or a loop re-forming on the band -- it is a candidate again (the verdict stays in cut_log)
            c.false_cut = False
            c.false_cut_level_db = None
            c.cut_verdict = None
            reasons.append("regrew_after_false_cut")
        if c.false_cut:
            klass = "FALSE_CUT"                 # a cut went through it like programme through an EQ: reported, never cut again
            reasons.append("false_cut")
        elif musical:
            klass = "MUSICAL"
            reasons = ["musical:" + "+".join(musical)]
        elif base_ok and evidence and latest[6] and not any(e == "loud" or e.startswith("probe") for e in evidence):
            # a family is present on THIS frame (though not yet on enough frames for MUSICAL): a source swelling out of
            # the bed shows its fundamental first and its partials surface a few frames later, exactly while the
            # fundamental 'rises' -- so growth evidence is not acted on while the partial slots are occupied; the
            # verdict waits for a clean frame (a ring's coincidental veto costs it a frame or two) [A §4.3]
            klass = "MODERATE"
            reasons.append("family_now")
        elif base_ok and evidence:
            klass = "STRONG"
            reasons += evidence
        elif base_ok and c.stationary:
            klass = "STATIONARY"                # answered the server's gain step like programme: report, never cut
            reasons.append("probe_linear")
            if c.prominence_db >= cfg.strong_prominence_db + 12.0 and c.level_db >= cfg.arm_line_min_level_db and c.veto_total == 0:
                # a >= 30 dB-prominent family-less line that FOLLOWS +1 dB steps is a room source through the mic -- or a howl
                # whose plateau is held by a channel compressor (2:1 gives exactly 1 dB/dB [L §1.4(2)]); only a BACK-OFF larger
                # than the excess separates them (the howl dies, the whine drops dB for dB): advise cfs to make one
                reasons.append("backoff_advised")
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

    def _skirt_frozen(self, band: int) -> bool:
        """Is at least one of ``band``'s ±1 neighbours also bit-identical over the last frozen_frames frames? A peak-held display
        freezes a line together with its skirts; a genuinely rock-steady tone (a howl on a brick-wall limiter in still air) can
        repeat its own int16/256 code while its skirts, 18+ dB nearer the noise, still jitter."""
        k = self.cfg.frozen_frames
        rows = list(self._spec)[-k:]
        if len(rows) < k:
            return False
        for j in (band - 1, band + 1):
            if 0 <= j < len(self.band_hz):
                col = [r[1][j] for r in rows]
                if col[0] > -127.0 and all(v == col[0] for v in col):
                    return True
        return False

    def _analyser_flags(self, vals: list[float], floor: float, ts: float) -> None:
        """Cheap whole-spectrum tells, refreshed every frame into :attr:`flags` (``floor`` = the frame's low quantile
        of the reference bands):

        * ``PEAK_HOLD_SUSPECTED`` -- >= peak_hold_bands bands that carry signal (>= floor + 3 dB) bit-identical for >=
          peak_hold_frames frames: a peak-hold display. A live band through int16/256 quantisation always jitters by
          >= 1 LSB; bands parked on a constant floor code are not counted [A §2, §7 S16];
        * ``HOT_SPECTRUM`` -- absolute levels carry no information: the arm-time p95 of the whole display is within
          |hot_spectrum_p95_db| of full scale (an RTA display gain of tens of dB, or a bus slammed into its limiter),
          or programme is clipping BROADBAND (>= 3 bands at the clip flag spanning more than one line's cluster) --
          held hot_hold_s after last seen. LOUD and the clip flag are disabled while it is up;
        * ``PROGRAMME_PRESENT`` -- see :meth:`programme_present`;
        * ``FROZEN_LINES`` -- some tracked line is currently frozen (set in _judge).
        """
        cfg = self.cfg
        n = len(vals)
        flags = self.flags
        flags.discard("FROZEN_LINES")
        last = self._last_vals
        live_thr = floor + 3.0
        if last is not None:
            # peak-hold: exact repeats across many signal-carrying bands
            fr = self._flat_run
            flat = 0
            for i in range(n):
                if vals[i] == last[i] and vals[i] >= live_thr and vals[i] > -100.0:
                    fr[i] += 1
                    if fr[i] >= cfg.peak_hold_frames:
                        flat += 1
                else:
                    fr[i] = 0
            if flat >= cfg.peak_hold_bands:
                flags.add("PEAK_HOLD_SUSPECTED")
            elif flat == 0:
                flags.discard("PEAK_HOLD_SUSPECTED")
            # broadband transient: many FAST bands (analyser settle <= 1 frame) up >= 6 dB at once = a drum hit / an
            # attack / a song start; independent noise bands never jump together [A §5 'song start', §4.2 cymbals]
            fast = [i for i in range(cfg.ref_band_lo, min(n - 1, cfg.ref_band_hi) + 1) if self._settle_frames[i] <= 1]
            if fast:
                up = sum(1 for i in fast if vals[i] - last[i] >= 6.0 and vals[i] >= live_thr)
                if up >= cfg.programme_transient_fraction * len(fast):
                    self._transients.append(ts)
            # display release rate: the largest single-frame fall anywhere in the reference bands. A display whose
            # /-prefs/rta/decay is long cannot fall faster than its release law, and the noisy bed exercises that limit
            # on every frame, so max-fall x frame rate IS the release rate [A §2] -- an instrument self-measurement
            # (only bands that carry signal at both instants: a band parked on the floor code cannot fall and says nothing)
            live = [last[i] - vals[i] for i in range(cfg.ref_band_lo, min(n - 1, cfg.ref_band_hi) + 1)
                    if last[i] > -127.0 and vals[i] > -127.0 and last[i] >= live_thr]
            if len(live) >= cfg.ref_min_bands:
                self._falls.append(max(live))
            if len(self._falls) >= 20:
                self.release_db_per_s = max(self._falls) / cfg.frame_period_s
                if self.release_db_per_s < cfg.slow_release_db_per_s:
                    flags.add("SLOW_RELEASE")
                else:
                    flags.discard("SLOW_RELEASE")
            else:
                flags.discard("SLOW_RELEASE")
        else:
            self._flat_run = [0] * n
        self._last_vals = vals
        # arm-time level reference for LOUD: p95 of everything the analyser showed over the first arm_baseline_s
        # (re-estimated every few frames while the window is open, then frozen)
        t_arm = self._arm_ref_t0 if self._arm_ref_t0 is not None else self.first_ts
        if t_arm is not None and ts - t_arm <= cfg.arm_baseline_s:
            self._arm_levels.extend(v for v in vals if v > -127.0)
            if self._arm_levels and (self.arm_p95_db is None or self.frames_seen % 5 == 0):
                srt = sorted(self._arm_levels)
                self.arm_p95_db = srt[int(0.95 * (len(srt) - 1))]
                self.arm_p90_db = srt[int(0.90 * (len(srt) - 1))]
                self.arm_max_db = srt[-1]
        elif self._arm_levels:
            srt = sorted(self._arm_levels)
            self.arm_p95_db = srt[int(0.95 * (len(srt) - 1))]
            self.arm_p90_db = srt[int(0.90 * (len(srt) - 1))]
            self.arm_max_db = srt[-1]
            self._arm_levels = []                       # frozen after the window; keep only the numbers
        # HOT: broadband clipping = >= 3 bands at the clip flag this frame that do not all belong to one line's cluster
        # (a full-scale howl pins its own band, or two adjacent ones when it sits on an edge; programme driven into
        # display clip by a gain offset pins fundamentals AND partials, kick AND bass...)
        clipped = [i for i in range(n) if vals[i] >= cfg.clip_db]
        if len(clipped) >= 3 and clipped[-1] - clipped[0] > 2:
            self._hot_until = ts + cfg.hot_hold_s
        # (p95 alone is reached by ONE full-scale howl with its skirts and two distortion partials -- 5 of 100 bands -- so
        # a hot DISPLAY also needs a tenth of it within a further 6 dB: programme, not one line family)
        hot = (self.arm_p95_db is not None and self.arm_p95_db >= cfg.hot_spectrum_p95_db
               and self.arm_p90_db is not None and self.arm_p90_db >= cfg.hot_spectrum_p95_db - 6.0) or ts < self._hot_until
        if hot:
            flags.add("HOT_SPECTRUM")
        else:
            flags.discard("HOT_SPECTRUM")
        if self.programme_present(ts):
            flags.add("PROGRAMME_PRESENT")
        else:
            flags.discard("PROGRAMME_PRESENT")

    def refresh_arm_reference(self) -> None:
        """Re-open the arm-time level reference (p95 / p90 / max over the next ``arm_baseline_s``): LOUD's level legs and the
        loud-ish line are referenced to what the display showed when the session armed, a 2 s snapshot -- cfs calls this when
        the programme situation changes for good (e.g. on PROGRAMME_PRESENT's rising edge after arming in silence), so that a
        show that started after arm does not keep a silent-room reference."""
        self._arm_levels = []
        self.arm_p95_db = None
        self.arm_p90_db = None
        self.arm_max_db = None
        self._arm_ref_t0 = self.last_ts

    def programme_present(self, ts: float | None = None) -> bool:
        """Is programme (music / speech / drums) present, judged over the last ``programme_window_s``? True when the
        window holds >= ``programme_min_events`` NOTE EVENTS (a line born after the arm frames reaching three qualified
        frames, a qualified line re-struck by an onset jump, or one that moved in pitch: melody, bass, chords and
        syllables produce several per second), >= ``programme_min_transients`` broadband transients (drums), or an
        average of >= ``programme_occupancy`` qualified lines born after the arm frames alive (a melody, a pad). A quiet
        room, mains hum, a standing whine and an established howl produce none of these (their lines are born at arm
        and do nothing); a dense mix with no line reaching 12 dB prominence and soft transients is the programme this
        under-reports. cfs uses it to check the ring_out contract ('stage is quiet') at arm and to say so in the
        report [L §4.3, judge G8]; it is not part of any cut decision."""
        cfg = self.cfg
        t = self.last_ts if ts is None else ts
        if t is None:
            return False
        lo = t - cfg.programme_window_s
        while self._events and self._events[0] < lo:
            self._events.popleft()
        while self._transients and self._transients[0] < lo:
            self._transients.popleft()
        while self._occupancy and self._occupancy[0][0] < lo:
            self._occupancy.popleft()
        occ = sum(o[1] for o in self._occupancy) / len(self._occupancy) if self._occupancy else 0.0
        return (len(self._events) >= cfg.programme_min_events or len(self._transients) >= cfg.programme_min_transients
                or occ >= cfg.programme_occupancy)

    # -- API -----------------------------------------------------------------------------------
    def feed(self, values_db: Sequence[float], ts: float) -> list[Detection]:
        """Process one RTA frame (``len(values_db) == len(band_hz)``, dB) taken at time ``ts`` (s).

        Returns the detections emitted on this frame (possibly empty). Raises ``ValueError`` on a
        frame of the wrong length.
        """
        cfg = self.cfg
        n = len(self.band_hz)
        if len(values_db) != n:
            raise ValueError(f"expected {n} RTA values, got {len(values_db)}")
        # clamp to the analyser's range: parse_meter_blob can only produce [-128, 0], but feed() is public (harness, replay
        # loader) and one NaN/inf would poison the per-band baseline and the arm reference for the rest of the session
        vals = [(-128.0 if v != v else (0.0 if v > 0.0 else (-128.0 if v < -128.0 else float(v)))) for v in values_db]
        if self.first_ts is None:
            self.first_ts = ts
        # -- spectrum store (common-mode reference) and per-band baseline -----------------------------
        lo_r, hi_r = cfg.ref_band_lo, min(n - 1, cfg.ref_band_hi)
        rb = sorted(vals[lo_r:hi_r + 1])
        floor = rb[int(cfg.ref_floor_quantile * (len(rb) - 1))]
        fi = self.frames_seen
        # each stored row also carries a short (cm_smooth_frames) running mean of the spectrum and of its floor:
        # common-mode moves are measured between smoothed rows, which halves the ±1.5-3 dB frame-to-frame jitter of
        # the median-of-changes in a sparse (quiet-room) spectrum and lets a low-water mark taken long ago be compared
        prev = list(self._spec)[-(cfg.cm_smooth_frames - 1):] if cfg.cm_smooth_frames > 1 else []
        if prev:
            m = len(prev) + 1
            sm = [(v + sum(r[1][i] for r in prev)) / m for i, v in enumerate(vals)]
            sf = (floor + sum(r[2] for r in prev)) / m
        else:
            sm, sf = vals, floor
        self._spec.append((fi, vals, floor, sm, sf))
        self.ref_db = rb[len(rb) // 2]
        ref = fi                     # histories store the frame index; common-mode moves are computed on demand (_cm)
        self._analyser_flags(vals, floor, ts)
        if self._base is None:
            self._base = list(vals)
        else:
            seeding = (ts - self.first_ts) < cfg.baseline_seed_s
            a_dn, a_up = (0.5, 0.05) if seeding else (0.15, 0.002)
            base = self._base
            # a band holding a QUALIFIED tracked line does not pull its own baseline up towards the line (the baseline is
            # 'what the band read before anything happened'; a standing line would otherwise absorb itself in ~40 s and its
            # excess_db -- P5 and the tier-B 'excess' leg -- would melt away)
            held: set[int] = set()
            for c in self._cands:
                if not c.misses and c.qual_frames and c.klass != "TRACK":
                    held.update((c.band - 1, c.band, c.band + 1))
            for i in range(n):
                d = vals[i] - base[i]
                if d > 0 and i in held and not seeding:
                    continue
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
        # candidate mates for the co-growth test: lines that have visibly started to swell (half the candidate's own
        # threshold: a chord's quieter notes clear the bed later and show less of the same swell at any instant)
        self._swelling = [ln.track for ln in lines if ln.track is not None and ln.track.swell_db >= 0.5 * cfg.cogrowth_rise_db]
        # AT-ARM cohort: lines carrying at-arm evidence that are candidates in their own right (a line with a harmonic
        # family is somebody's note or partial and says nothing about how many howls were sounding when we armed)
        self._arm_cohort = [ln.track for ln in lines if ln.track is not None and ln.track.arm_evidence and not ln.vetoed
                            and ln.track.klass != "MUSICAL"]
        for ln in lines:
            c = ln.track
            if self._steps:
                self._probe(c, ts)
            self._judge(c, ts)
        survivors.sort(key=lambda c: c.centroid)
        self._cands = survivors
        # programme occupancy: qualified lines born after arm and alive now -- not counting lines this detector calls
        # (or has called) feedback, nor lines that over-responded to a probe step: those are what ring_out looks for
        self._occupancy.append((ts, sum(1 for ln in lines if ln.track.qual_frames >= 3 and not ln.track.born_at_arm
                                              and not ln.track.emitted and ln.track.klass not in ("STRONG", "STATIONARY")
                                              and ln.track.probe_hits == 0 and ln.track.probe_linear == 0
                                              and not self._surfaced_by_step(ln.track))))
        for c in self._cands:
            if c.cut is not None:
                self._verify_cut(c, ts, present=not c.misses)
        # -- emit ----------------------------------------------------------------------------------
        out: list[Detection] = []
        for c in self._cands:
            if c.misses:
                continue
            emit = c.klass == "STRONG"
            klass = c.klass
            if not emit and c.klass == "MODERATE" and c.emitted and c.cut_deepen and c.cut_verdict in ("held", "insufficient"):
                # a deepen right earned by a 'held' / 'insufficient' verdict is exercised on the evidence the line was CUT on
                # (recorded at emission), as long as the line is still a BASE line: after two cuts a held howl sits 6 dB under
                # its maximum, where FAST-RISE has lapsed and LOUD no longer holds, yet it is the same line, still there
                emit = True
            if not emit and cfg.mode == "ringout":
                if c.klass == "MODERATE" and cfg.ringout_emit_moderate:
                    # programme absent by contract: a MODERATE line that no gain step could test in its lifetime
                    # is emitted once it has held BASE for K2 frames
                    untestable = not any(t_s >= c.first_ts - 0.5 for _k, t_s, _d in self._steps)
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
            if c.emitted:
                # RE-EMISSION = a request to deepen the cut. Only on evidence gathered SINCE the previous emission: the
                # line has grown reemit_rise_db above its level at that emission (the loop beat the cut and is climbing
                # again), or it is LOUD / at the clip flag now, or it over-responded to another probe step since. A line
                # that went DOWN by the bell and sits there (programme through the EQ -- or a howl held by a limiter
                # downstream of the tap, passively identical: cfs policy) is never ratcheted to -9 dB on the evidence
                # that produced the first cut [L §4.1, judge G2/G7 'deepen only on regrowth']. A pending / false_cut /
                # ambiguous note_cut() verdict also blocks it.
                if c.cut_verdict in ("pending", "false_cut", "ambiguous"):
                    continue
                regrew = c.last_emit_level_db is not None and c.cluster_db >= c.last_emit_level_db + cfg.reemit_rise_db
                # LOUD again only counts if the line did NOT come down since the emission (the cut never reached it: desk
                # clip after the insert, a POST insert); a line that dropped by the bell is the note_cut() verdict's business
                loud_now = "loud" in c.reasons and (c.last_emit_level_db is None or c.cluster_db >= c.last_emit_level_db - cfg.cut_false_tol_db)
                probed = c.probe_hits > c.probe_hits_at_emit
                deepen = c.cut_deepen and c.cut_verdict in ("held", "insufficient")
                if not (regrew or loud_now or probed or deepen):
                    continue
                if deepen:
                    c.cut_deepen = False                    # one re-emission per verdict; the next needs the deeper cut verified
                    c.reasons = c.reasons + (f"deepen_{c.cut_verdict}",)
            conf = max(c.confidence, cfg.confidence_threshold)
            det = Detection(
                ts=ts, band=c.band, freq_hz=c.freq_hz, level_db=c.level_db, prominence_db=c.prominence_db,
                slope_db_per_s=c.slope_db_per_s, frames=c.frames, confidence=conf, reasons=c.reasons,
                klass=klass, centroid_band=c.centroid, narrow_db=c.narrow_db, rise_db=c.rise_db, excess_db=c.excess_db,
            )
            out.append(det)
            c.emitted += 1
            c.last_emit_ts = ts
            c.last_emit_level_db = c.cluster_db
            c.emit_peak_db = c.level_db if c.emit_peak_db is None else max(c.emit_peak_db, c.level_db)
            names = tuple(x for x in ("fastrise", "loud", "probe", "established_at_arm", "rise", "growth") if any(r.startswith(x) for r in c.reasons))
            c.emit_evidence = tuple(sorted(set(c.emit_evidence) | set(names)))
            c.probe_hits_at_emit = c.probe_hits
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
        self._cut_depth = {}
        self.cut_log = []
        self.flags = set()
        self._last_vals = None
        self._flat_run = []
        self._arm_levels = []
        self.arm_p95_db = None
        self.arm_p90_db = None
        self.arm_max_db = None
        self._arm_ref_t0 = None
        self._events = deque()
        self._transients = deque()
        self._occupancy = deque()
        self._arm_cohort = []
        self._hot_until = -1.0
        self._falls = deque(maxlen=40)
        self.release_db_per_s = None
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


@dataclass(frozen=True)
class NotchPlan:
    """A cut that has been decided but not yet written (:meth:`NotchController.propose`). The caller
    writes ``new_db`` to the desk and only then calls :meth:`NotchController.commit`; a write that fails
    or is cancelled is simply never committed, so the controller can never run ahead of the GEQ.
    ``opens`` is True when the band is a new budget line."""

    bus: int
    band: int
    freq_hz: float
    current_db: float
    new_db: float
    session_id: str
    ts: float
    opens: bool

    @property
    def depth_db(self) -> float:
        return self.new_db


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

    def propose(self, det: Detection, bus: int, session_id: str) -> NotchPlan | None:
        """Decide the cut for ``det`` **without touching any state**: deepen the nearest notch within
        ``merge_adjacent_bands`` (exact band first), else open a new notch at the detection's band.
        Returns the :class:`NotchPlan` to write, or None when nothing can be done (budget spent, or the
        band is already at ``notch_max_db``). ``policy_validate`` runs here and its refusal propagates.

        The live path is two-phase — propose, write, :meth:`commit` — so that a GEQ write that raises
        (read timeout, RATE_LIMITED, NOT_A_GEQ) or is cancelled (a stop while the write is in flight)
        leaves gains, notches and budget exactly at what the desk holds. Committing before the write
        made the controller believe in cuts the console never received, charged the budget for them,
        and turned the next detection's "deepen" into a 6 dB first step on the desk."""
        cfg = self.cfg
        target = self.band_for_freq(det.freq_hz)
        order = sorted(self._notches, key=lambda b: (abs(b - target), b))
        band: int | None = None
        for b in order:
            if abs(b - target) > cfg.merge_adjacent_bands:
                break
            if self._can_deepen(b) and self._affordable(b):
                band = b
                break
        if band is None:
            if not self._can_deepen(target) or not self._affordable(target):
                return None
            band = target
        current = self._gains.get(band, 0.0)
        new = max(cfg.notch_max_db, current + cfg.notch_step_db)
        self._validate(current, new)  # may raise (BOOST_FORBIDDEN / NOT_ALLOWED) — state untouched
        return NotchPlan(bus=int(bus), band=band, freq_hz=self.geq_band_hz[band - 1], current_db=current, new_db=new,
                         session_id=str(session_id), ts=float(det.ts), opens=band not in self._touched)

    def commit(self, plan: NotchPlan) -> Notch:
        """Record a :class:`NotchPlan` whose write has reached the desk (gains, notch list, budget)."""
        return self._apply(plan.band, plan.bus, plan.session_id, plan.ts, plan.new_db)

    def plan(self, det: Detection, bus: int, session_id: str) -> Notch | None:
        """:meth:`propose` + :meth:`commit` in one call — offline planning and tests only; anything that
        writes to a desk must use the two-phase form."""
        p = self.propose(det, bus, session_id)
        return None if p is None else self.commit(p)

    def observe(self, band: int, gain_db: float) -> None:
        """The desk reports ``band`` at ``gain_db`` (a pushed change made on the console or by another
        client). Adopt it as the truth the next proposal starts from: a band the engineer cut deeper by
        hand must never be written back shallower, and a band they released is no longer our notch.
        Budget accounting is untouched (what this session wrote, it wrote)."""
        band = int(band)
        if not 1 <= band <= len(self.geq_band_hz):
            return
        g = float(gain_db)
        self._gains[band] = g
        n = self._notches.get(band)
        if g < 0:
            if n is None:
                self._notches[band] = Notch(bus=0, band=band, freq_hz=self.geq_band_hz[band - 1], depth_db=g,
                                            session_id="", ts=0.0, detections=0)
            else:
                n.depth_db = g
        elif n is not None:
            del self._notches[band]

    def _apply(self, band: int, bus: int, session_id: str, ts: float, new: float) -> Notch:
        current = self._gains.get(band, 0.0)
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
