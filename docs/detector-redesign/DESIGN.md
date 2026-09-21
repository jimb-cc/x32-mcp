# disc-free-hand — a predicate discriminator for CFS² (REVIEW_BRIEF §1), "free hand, physics first"

Branch `wt/disc-free-hand` (worktree `…/scratchpad/worktrees/disc-free-hand`), built on `wt/corpus-critic` @ 1be8603.
Files: `src/x32mcp/detector.py` (rewritten; `NotchController` & friends verbatim), `device.yaml` (`detector:` keys added,
none changed), `src/x32mcp/cfs.py` (+9 lines: session mode + active-probe notification), `tests/test_detector.py`
(assertions changed in 5 tests, 1 test added — §6). Evidence: `metrics.json` (+ four harness dumps `metrics_{open,closed}_{watch,ringout_probe}.json`),
`sweeps.json`, scripts `run_eval.py`, `sweep.py`, `final_metrics.py`, `why.py`/`gdbg.py`/`pdbg.py` (diagnostics) in this directory.
Citations: [L §x] loop brief, [A §x] analyser brief, [C §x] CORPUS.md, [N] lead notes.

Headline (seeds 1-3, all 61 scenarios, open loop, both modes): **117/117 rings detected, 0 false positives, 0 HARM cuts in
closed loop; 55/61 scenarios inside their latency budget; latency p50 100 ms, p90 400 ms; 100/117 events ≤ 300 ms.**
Hold-out seeds 4-6: 0 FP, 114/117. Twelve of fourteen analyser-model sweeps: 0 FP (§4). The current detector on the same
corpus: 948 FP, 9 misses, 12/61 [C §6].

---------------------------------------------------------------------------------------------------------------------

## 1. What the detector models, and why it is not a score

The old `0.3·prominence + 0.2·persistence + 0.5·growth ≥ 0.7` failed on the desk both ways for one structural reason
[N 1-2, A §0]: a sum lets surplus in one physical requirement buy a deficit in another, and its one informative term
(growth) is manufactured by the analyser at LF (a 1/10-octave band cannot settle faster than ~1/Δf = 1/(0.069 f):
370 ms at 39 Hz, 185 ms at 78 Hz [A §1]) and is absent for the physically normal end state of a howl (plateau at a
limiter/compressor level, no growth, no clip [L §1.4]). This design replaces the sum with **tracked spectral lines** and a
short **decision list of independent physical predicates**; each threshold is a physical quantity, each emitted
`Detection.reasons` names the predicates that fired (for the notch report).

### 1.1 Per-frame front end (`feed`, detector.py:1072)
* Spectrum history: last 64 frames of the 100 values, and the **spectrum reference** `ref` = median of bands 25..85
  (110 Hz–7 kHz, where the analyser settles within a frame) — the common-mode signal [A §5].
* `prominence[i]` = level − median of ±3 neighbours (unchanged definition; `prominences()` kept for tests/dashboard).
* **Narrowness** (`_narrowness`, :567): peak minus the *shallower* of its two flanks, a flank being the lower of the two
  bands just outside the line's footprint; a line between two centres reads (−3,−3) in a band pair [A §3] so the pair is
  the footprint. A lone sinusoid leaks only through the analyser skirts (≥12 dB down at ±2 even for 2nd-order skirts
  [A §3]) so one of the two outer bands on each side is deep; a formant hump, cymbal wash, PA ripple or applause has no
  dip on either side. Threshold `narrow_db` 8 (tracked from 4). This also survives another line two bands away (the
  band between them dips), which the naive "vs ±2" form did not (X11 in a loud band).
* **Centroid** (`_centroid`, :588): power centroid of peak±1 → sub-band frequency (±0.05 band on a clean line); it absorbs
  edge-straddling and vibrato band-hopping [A §4.4] and gives `Detection.freq_hz` as the *interpolated* frequency so the
  notch planner can pick the right 1/3-octave band for a line between RTA centres [L §2.1/§4.1].
* Lines (local maximum, prominence ≥ `track_prominence_db` 6, narrowness ≥ 4) are matched to **tracks** by nearest
  centroid within ±1.35 band (a hand-held mic re-locking ≤160 c stays one track [L §2.3]); hysteresis keeps a track on a
  weaker maximum for ≤12 frames; a track that re-appears within 10 frames on its extrapolated ramp is resurrected (a
  cymbal crash hides a growing ring for a few frames, S3/S14). A matched line whose centroid sits >0.6 band from the
  track's home is a *different* line (melody step) and is born anew — unless the track is an already-reported ring
  (hop). Newborn tracks are **back-filled** from the spectrum history with the frames in which their band was already a
  local maximum (`_backfill`, :1263): a 150 dB/s howl in a loud mix shows 2–4 frames of growth and none may be wasted.
* Each track records how it was **born**: `est` (present at arm: born in the first 3 frames or present in every stored
  frame), `pop` (≥55 % of its rise above the pre-birth level arrived in one frame step: an instrument onset — or a
  >500 dB/s ring, the irreducible case [L §1.3, C §7.10]), `static` (was already at this level before being tracked),
  `new`/`grow`.

### 1.2 Predicates (per track, per frame; `_classify`, :1385)
| # | predicate | threshold (key) | physics / source |
|---|---|---|---|
| A | **narrow** | `narrow_db` 8 | single sinusoid vs hump, above |
| B | **prominent** | `prominence_db` 12 to emit on EST/LOUD; `grow_prominence_db` 10.5 on growth; tracked from 6 | 12 is the display convention [C §1]; a ring beside programme partials reads a few dB low and growth is itself the evidence |
| C | **stationary**: centroid range over last 10 frames | `centroid_tol_bands` 0.6 | semitone = 0.83 band; vibrato ±50 c swings an edge tone's centroid ±0.3..0.8 band [A §4.4]; ring centroid noise <0.1 band [A §4.3(v)]; loop frequency fixed by geometry [L §2.3] |
| D | **steady**: sd of last 6 frame steps, no step > 2.5× | `level_unsteady_db` 1.5 | sine through PEAK det ≈ deterministic (±0.5 wander [C §2.1]); voice/whistle flutter 1–2 dB, vibrato AM [A §4.2]; for LOUD/CLIP judged over the plateau only |
| E | **not decaying**: level ≥ max(last 1 s) − 2 | `decay_tol_db` 2 | plucked/struck notes decay 3–15 dB/s; a loop above threshold never does [L §1.4/P6]; killed rings & RTA release tails fall [A §2] |
| F | **harmonic family** (`_family_now`, :601): ≥`family_partials` (2) of {2f,3f,4f,5f} present as lines (local max or masked shoulder ≥`family_prominence_db` 6 clear of both flanks) whose centroid is within `family_tol_bands` 0.5 (60 c) of the exact position; OR the line is somebody's 2f/3f/4f (fundamental present *and not >10 dB quieter than the line*, plus one more partial of it); for steady lines additionally a lone exact-octave partner counts | 0.5 band: integer ratios are exact, unrelated lines are not (G4's 4f at 1568 Hz vs a ring at 1683 Hz = 1.2 band apart, X7); two partners: one coincidence ≈20 %, two <1 % [A §4.3]; fundamental level cap: no instrument's 3f/4f stands 15 dB over its 1f, a howl over a programme line does (X11, X21); odd-dominant sets (3f,5f with 2f ≥6 dB under 3f, no 4f) on a line ≥18 dB over `ref` are clip products, not a timbre [L §2.2, A §4.3] |
| F' | **co-moving family** for *growing* lines (`_family_comoving`, :749): a partner counts only if it rose ≥ min(½·rise−2, 4) dB (≥1.5) over the ramp (window starts 6 frames early so a weaker partial that climbed out of the bed first still counts); a *lone* exact-octave partner vetoes growth only if it is indistinguishable from one swelling source: onset no later than (level deficit ÷ swell rate), equal dB slope within −15/+18 %, level ratio kept within `family_ratio_tol_db` 3 (HF only: LF band lag distorts early ratios) | partials share one envelope; two rings on an octave start when their own loops cross and grow at their own e/τ [L §2.3, C X10/S11b notes]; "require ≥2 partners or 2f∧3f" [A §4.3] |
| F'' | **independent partner** (`_independent`, :721): a partner band that swung ≥8 dB and *vanished* (prominence <6 at its minimum) while the line held within 3 dB is not this line's partial | chord changed under a ring = P12 "outlasts programme structure" [L §5] |
| G | **growth** (`_growth`, :848), on the *peak level*, net of any rise of `ref` over the same span (5-frame medians): **FAST** — ≥3 of the last ≤5 *consecutive* frame steps ≥ max(`growth_fast_step_db` 2, 0.15·rise), summing to ≥ `growth_fast_total_db` 12, largest ≤55 %, two largest ≤75 %, last step still ≥1, and a new high (≥ prior max + 3); **SLOW** — over windows m ∈ {max(`growth_slow_frames` 5, settle(b)), 8, 12, 20, 40, 60}: LS slope ≥ `growth_slow_min_db_per_s` 1.5, net rise ≥ `growth_slow_total_db` 6 within 1 s tapering to `growth_slow_total_long_db` 4 at ≥2 s (or 4 when the ramp starts ≤0.4 s after a *known* master step), max |residual| ≤ max(1.5, ¼·rise), no held single-frame step >55 % (per-frame, so a masked gap is not a step), two largest steps ≤ ½·rise (ratchet of re-plucked strings/syllables), every third rising (s₃ ≥0.4 s, s₁,s₂ ≥0.15 s), each half carrying ≥¼ / ≥0.3 of the climb, 25 %→90 % rise time ≥0.4·window, new high, ≤¼ of frames missing, and below 300 Hz ≥75 % of the window frames actually prominent. Evaluated on a 3-point-median series and on a trailing-min series (programme sharing the band only ever *adds* power: the line's own level is the lower envelope). | growth = excess/τ, 1..>500 dB/s, linear in dB [L §1.3] — so *no upper rate cap*; **settle(b) = ⌈`analyser_settle_cycles` 3 /(0.069·f_b·0.05 s)⌉** frames (3 above 300 Hz, 6 @156 Hz, 12 @78 Hz, 23 @39 Hz): any 1/10-octave filter needs ~1/Δf, 3/Δf covers the critically-resolved bank and the BQ model alike [A §1, L §1.6] — this is what removes the M7 40/80 Hz mechanism; ±0.5 dB loop-gain wander [C §2.2] ≪ 6 dB; expressive swells ≈1 dB/note [C X2]; step-locked relaxation: "growth onset time-locked to the server's own step ⇒ certain" [L §4.3] |
| H | **synchronous growth**: ≥3 tracks ramping in the same frame ⇒ common cause, growth void | pads/faders/song starts move many lines; a loop crosses alone; two at once = two rings (S11, X10) [A §5] |
| I | **sync-onset**: ≥4 non-harmonic tracks born within ±2 frames of a `pop` line ⇒ programme event (blocks EST/LOUD, expires when the cohort is gone) | chord/crash/song start [A §5]; rings start alone |
| J | **level**: `clip_level_db` −0.5 (0.00 = clip flag [meters.md §4.2]); `loud_level_db` −10 [L P8]; `est_min_level_db` −40 (a limiter/compressor/clip plateau sits at −30..0 dBFS at a bus tap with sane gain structure [L §1.4]; a stationary line below it at arm is hum/HVAC/room tone); `family_escape_level_db` −6 (this close to FS something clips and odd partials appear [L §2.2]) | all four assume `/-prefs/rta/gain` = 0 dB, to be forced at arm [A §2, N] — §4 shows what a +12 dB offset does |
| K | **window**: `hf_edge_hz` 12.5 k (SM58-class dies >10 k, condensers ring to 12–13 k [L §3.1]); `lf_strict_hz` 160: below it only GROW (over the long analyser-safe window) or PROBE may emit, never EST/LOUD (LF is where programme fundamentals carry the energy; LF loops have τ 15–30 ms and small e, i.e. grow slowly enough to be watched [L §3.2(e), §5]); `lf_edge_hz` 0 = **no hard floor** (see Q3) | |
| L | **active probe** (`note_gain_step` :499, `_probe_update` :996): after each server master step δ, per track that predates it by ≥6 frames: excess = (median level over ≤12 frames after settle 2) − (before) − max(Δref, δ, 0); counts only if |excess| ≥ 2×(the same line's change across the previous, step-free window boundary)+1 (control: a room mode driven by a changing bass line jumps whole notes, S19); `probe_hits`++ if ≥ `probe_excess_db` 2 (two consecutive ⇒ PROBE), one ≥ `probe_strong_db` 4 ⇒ PROBE; ≤ `probe_linear_db` 1.3 twice ⇒ stationary/programme veto for EST/LOUD | regenerative gain 1/(1−g): a mode 4→3 dB under threshold answers +3 dB to +1 dB, programme 0 (electrical, pre-fader tap) to +1 (spill) [L §0, §1.5]; for a noise-excited HF band the band-mean of the comb makes this ≥2 dB/dB only within ~3 dB of threshold and noisy [C §2.3, §7.6] — hence "two consecutive or one ≥4" and the null control |

### 1.3 Decision list (first match emits; :1425-1505)
1. **Already reported** (sticky): re-emit every `cooldown_s` while prominent, narrow, not decaying **and still within
   3 dB of the level it was reported at** (`sustained`) — a notch that worked leaves nothing or a quieter programme line
   that shared the band; a ring that survives keeps its level; one that re-grows from lower down is caught as `regrow`
   (growth predicate, ≥ level−12). Family appearing *after* the verdict is ignored (distortion products appear once the
   fundamental nears clip [A §4.3]).
2. **CLIP**: ≥ −0.5 dBFS on a narrow line, plateau stationary/steady, `loud_frames` 3 frames, ≥160 Hz.
3. **LOUD**: ≥ −10 dBFS, narrow, prominent, plateau stationary+steady for 3 frames (150 ms: no percussive transient is
   that narrow that long), not decaying, ≥160 Hz, no family unless ≥ −6 (escape), no probe-linear veto, not sync-onset.
4. **GROW-fast / GROW-slow**: predicate G ∧ narrow ∧ stationary ∧ prominence ≥10.5 ∧ ¬co-moving family (F').
   *Any birth class* — this is what catches rings seeded by a speech/choir partial (X9, X21) and re-growth after a fader
   move (X12) — and *any rate*.
5. **EST**: born at arm ∧ narrow ∧ prominent ∧ stationary ∧ steady ∧ not decaying ∧ ¬family (presence, incl. lone
   octave) ∧ ≥160 Hz ∧ level ≥ −40 ∧ still within 4 dB of its level at arm ∧ `est_frames` 5 frames ∧ ¬probe-linear ∧
   ¬musical. Latency after arm: 250 ms at any prominence ≥12 (M7's 15-s miss at 60 dB; X7 at 15–25 dB).
6. **PROBE** (only when steps were noted): (2 consecutive hits ∨ 1 hit ≥4 dB) ∧ narrowness ≥6.4 ∧ prominence ≥9 ∧
   stationary ∧ not decaying ∧ ¬family ∧ ¬musical → cut *before* runaway [L §4.3 "act on sub-threshold evidence"].
7. Otherwise: candidate only (published with `reasons` such as `popped-onset`, `family`, `moving`, `decaying`).
   **A line that popped into existence at full level and sits flat below −10 dBFS is never cut on passive evidence**
   (organ X1, flute X2, whistle X3/S8c/S20/X18, sine lead X4, 808 X5/S7, bells S24, hum X20): that is the irreducible
   window [C §7.10] resolved toward the human in `watch` and toward the probe in `ring_out`.

`confidence` is a monotone function of the margins, ≥ `confidence_threshold` iff emitted (dashboard contract kept);
it is not the decision variable.

### 1.4 Modes
`DetectorConfig.mode` / `FeedbackDetector(cfg, band_hz, mode=)`: `watch` | `ringout`. The decision list is the same;
`ringout` additionally receives `note_gain_step(delta_db, ts)` (cfs.py:1196-1202 now calls it after every master
write; cfs.py:891 passes the session mode) which enables rule 6, the probe-linear veto and the step-locked 4 dB growth
threshold. `note_cut(band_hz, depth, ts)` is accepted (reserved: exclude the bell from `ref`). Without steps `ringout`
behaves as `watch` (safe default).

---------------------------------------------------------------------------------------------------------------------

## 2. Results (rtasim corpus, 61 scenarios × seeds 1,2,3; harness as CORPUS.md §1; `final_metrics.py`)

Cell = TP/events, FP count, latency min/med/max ms (vs. the harness `t_prom`), E=EARLY (sub-threshold ringing of the
same loop, credited if ≤2 s before onset), H=HARM (a clipping howl's own partials), **P** = all seeds pass, F(k/3).
Closed loop = NotchController −3 dB steps, 1-frame actuation delay; feedback scenarios only.

| scenario | budget | open watch | closed watch | open ring_out+probe | closed ring_out+probe |
|---|---|---|---|---|---|
| C0_silent_room | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| C1_music_bed_drums | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| S1_bass_under_quiet_music | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| S2a_established_ring_8k | 300 | 3/3 fp0 98/100/100 **P** | 3/3 fp0 98/100/100 **P** | 3/3 fp0 98/100/100 **P** | 3/3 fp0 98/100/100 **P** |
| S2b_established_ring_8k_steep | 300 | 3/3 fp0 98/100/100 **P** | 3/3 fp0 98/100/100 **P** | 3/3 fp0 98/100/100 **P** | 3/3 fp0 98/100/100 **P** |
| S2c_established_clipped_2k4 | 300 | 3/3 fp0 98/100/101 H24 **P** | 3/3 fp0 98/100/101 **P** | 3/3 fp0 98/100/101 H24 **P** | 3/3 fp0 98/100/101 **P** |
| S3_ring_during_music | 300 | 3/3 fp0 -1/449/752 F(1/3) | 3/3 fp0 -1/449/752 F(1/3) | 3/3 fp0 -1/449/752 F(1/3) | 3/3 fp0 -1/449/752 F(1/3) |
| S4a_vocal_vibrato | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| S4b_vocal_vibrato_band_edge | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| S5_guitar_note_decays_to_sine | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| S6_master_ramp_feedback_watch | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| S6b_ringout_steps_latent_loop | 300 | 3/3 fp0 52/200/252 **P** | 3/3 fp0 52/200/252 **P** | 3/3 fp0 98/253/300 **P** | 3/3 fp0 98/253/300 **P** |
| S7_808_sub_bassline | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| S8a_organ_melody | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| S8b_flute_held_note | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| S8c_whistle | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| S9_clipped_howl_fast | 300 | 3/3 fp0 -2/-1/48 **P** | 3/3 fp0 -2/-1/48 **P** | 3/3 fp0 -2/-1/48 **P** | 3/3 fp0 -2/-1/48 **P** |
| S10_ring_between_bands | 300 | 3/3 fp0 98/102/251 **P** | 3/3 fp0 98/102/251 **P** | 3/3 fp0 98/102/251 **P** | 3/3 fp0 98/102/251 **P** |
| S11a_two_rings | 300 | 6/6 fp0 -52/-2/2 **P** | 6/6 fp0 -52/-2/2 **P** | 6/6 fp0 -52/-2/2 **P** | 6/6 fp0 -52/-2/2 **P** |
| S11b_two_rings_near_octave | 300 | 6/6 fp0 -53/149/248 **P** | 6/6 fp0 -53/149/248 **P** | 6/6 fp0 -53/149/248 **P** | 6/6 fp0 -53/149/248 **P** |
| S12_acoustic_guitar_wedge_ring_196Hz | 600 | 3/3 fp0 301/349/647 F(2/3) | 3/3 fp0 301/349/647 F(2/3) | 3/3 fp0 301/349/647 F(2/3) | 3/3 fp0 301/349/647 F(2/3) |
| S13_slow_ring_3dB_s | 1000 | 3/3 fp0 -1151/-701/0 **P** | 1/1 fp0 -51/-51/-51 **P** | 3/3 fp0 -1151/-701/0 **P** | 1/1 fp0 -51/-51/-51 **P** |
| S14_ring_masked_by_cymbal | 300 | 3/3 fp0 98/150/153 **P** | 3/3 fp0 98/150/153 **P** | 3/3 fp0 98/150/153 **P** | 3/3 fp0 98/150/153 **P** |
| S15_long_rta_decay_tails | 300 | 3/3 fp0 -49/-49/-1 **P** | 3/3 fp0 -49/-49/-1 **P** | 3/3 fp0 -49/-49/-1 **P** | 3/3 fp0 -49/-49/-1 **P** |
| S16_peak_hold_on | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| S17_kick_pattern | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| S18_vibrato_on_band_edge | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| S19_driven_room_mode | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| S20_song_start_stop_crowd | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| S21_synth_pad_swell | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| S22_speech_ringing_then_feedback | 300 | 3/3 fp0 3/102/202 **P** | 3/3 fp0 3/102/202 **P** | 3/3 fp0 3/102/202 **P** | 3/3 fp0 3/102/202 **P** |
| S23a_autogain_drift | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| S23b_gain_offset_clip | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| S24_bells_triangle_glock | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| M1_loud_band_wedge_ring | 300 | 3/3 fp0 97/98/102 **P** | 3/3 fp0 97/98/102 **P** | 3/3 fp0 97/98/102 **P** | 3/3 fp0 97/98/102 **P** |
| M2_quiet_music_ringout_two_modes | 300 | 6/6 fp0 -0/149/152 E1 **P** | 9/9 fp0 -49/98/152 **P** | 6/6 fp0 -1151/152/202 E1 **P** | 7/7 fp0 -1051/100/153 E1 **P** |
| M3_jazz_trio_lav_ring_400Hz | 300 | 3/3 fp0 -51/1/149 **P** | 3/3 fp0 -51/1/149 **P** | 3/3 fp0 -51/1/149 **P** | 3/3 fp0 -51/1/149 **P** |
| X1_organ_held_notes | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| X2_flute_held_vibrato | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| X3_whistle_held_drift | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| X4_sine_lead_portamento | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| X5_808_bassline_40_60Hz | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| X6_soprano_closed_vowel_band_edge | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| X7_plateaued_ring_under_music_from_t0 | 1000 | 3/3 fp0 199/200/202 **P** | 3/3 fp0 199/200/202 **P** | 3/3 fp0 199/200/202 **P** | 3/3 fp0 199/200/202 **P** |
| X8_slow_ring_midband_under_chords | 1000 | 3/3 fp0 50/2252/2948 F(1/3) | 3/3 fp0 50/2252/2948 F(1/3) | 3/3 fp0 50/2252/2948 F(1/3) | 3/3 fp0 50/2252/2948 F(1/3) |
| X9_ring_rta_midpoint_525Hz_speech | 300 | 3/3 fp0 498/1000/1500 F(0/3) | 3/3 fp0 498/1000/1500 F(0/3) | 3/3 fp0 498/1000/1500 F(0/3) | 3/3 fp0 498/1000/1500 F(0/3) |
| X10_two_rings_exact_octave | 300 | 6/6 fp0 -3/74/349 F(2/3) | 6/6 fp0 48/124/349 F(2/3) | 6/6 fp0 -3/74/349 F(2/3) | 6/6 fp0 48/124/349 F(2/3) |
| X11_amp_clipped_howl_minus12dBFS | 300 | 3/3 fp0 -3/23/49 **P** | 3/3 fp0 -3/23/49 **P** | 3/3 fp0 -3/23/49 **P** | 3/3 fp0 -3/23/49 **P** |
| X12a_master_drop20_raise_channel | 300 | 6/6 fp0 99/199/202 **P** | 3/3 fp0 199/200/202 **P** | 6/6 fp0 99/199/202 **P** | 3/3 fp0 199/200/202 **P** |
| X12b_master_drop20_raise_busmaster | 300 | 6/6 fp0 149/175/202 **P** | 3/3 fp0 199/200/202 **P** | 6/6 fp0 149/175/202 **P** | 3/3 fp0 199/200/202 **P** |
| X13_decay16_jazz_lav_ring | 600 | 3/3 fp0 -51/102/202 **P** | 3/3 fp0 -51/102/202 **P** | 3/3 fp0 -51/102/202 **P** | 3/3 fp0 -51/102/202 **P** |
| X14_peakhold_loud_band_wedge_ring | 300 | 3/3 fp0 97/98/147 **P** | 3/3 fp0 97/98/147 **P** | 3/3 fp0 97/98/147 **P** | 3/3 fp0 97/98/147 **P** |
| X15_kick_bass_unison_55Hz | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| X16_wedge_ring_315Hz_loud_band | 300 | 3/3 fp0 98/101/103 **P** | 3/3 fp0 98/101/103 **P** | 3/3 fp0 98/101/103 **P** | 3/3 fp0 98/101/103 **P** |
| X17_ring_122Hz_acoustic_guitar_body | 600 | 3/3 fp0 97/198/400 **P** | 3/3 fp0 97/198/400 **P** | 3/3 fp0 97/198/400 **P** | 3/3 fp0 97/198/400 **P** |
| X18_applause_crowd_30s | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| X19_handheld_ring_stalls_and_hops | 300 | 3/3 fp0 2/100/149 **P** | 3/3 fp0 2/100/149 **P** | 3/3 fp0 2/100/149 **P** | 3/3 fp0 2/100/149 **P** |
| X20_mains_hum_and_hvac_whine | 300 | 0/0 fp0 – **P** | – | 0/0 fp0 – **P** | – |
| X21_reverberant_area_mic_slow_ring | 600 | 3/3 fp0 202/452/652 F(2/3) | 3/3 fp0 202/452/652 F(2/3) | 3/3 fp0 202/452/652 F(2/3) | 3/3 fp0 202/452/652 F(2/3) |
| X22_kick_mic_sub_ring_65Hz | 1000 | 3/3 fp0 303/352/401 **P** | 3/3 fp0 303/352/401 **P** | 3/3 fp0 303/352/401 **P** | 3/3 fp0 303/352/401 **P** |
| X23_ringout_quiet_room_two_modes | 300 | 6/6 fp0 -1100/76/248 E3 **P** | 7/7 fp0 50/150/248 E2 **P** | 6/6 fp0 -1202/-347/248 E6 **P** | 6/6 fp0 53/176/300 E3 F(2/3) |

Totals:
* **open watch**: 55/61 pass; TP 117/117, miss 0, **FP 0**, EARLY 4, HARM 24 (S2c's clipped howl partials; 0 once the
  loop is closed), TAIL 0. Latency (n 117): min −1151, p50 100, p90 400, p95 652, max 2948 ms; ≤300 ms: 100/117; over
  the scenario budget: 10.
* **closed watch** (32 feedback scenarios): 26/32 pass; TP 113/113, miss 0, **FP 0**, EARLY 2, HARM 0, cuts 122 (current
  detector: 286 FP, 334 cuts, loop pre-empted by FP cuts in S12/X8/X9/X17 [C §6]).
* **open ring_out + probe**: 55/61; TP 117/117, miss 0, **FP 0**, EARLY 7 (M2 and X23 modes flagged by the probe 1–3 s
  before runaway; S6b's noise-excited 2.5 kHz mode never becomes a trackable line before it runs away and is caught by
  growth in 98–300 ms), HARM 24.
* **closed ring_out + probe**: 25/32; TP 112/112, miss 0, **FP 0**; X23 shows the probe doing a human's ring-out: seed 2 —
  both modes cut −3 dB *before either ever oscillated* (EARLY at e ≈ −1.5 dB), the 625 Hz mode then crossed three steps
  later at a 3 dB higher master and was deepened to −6 within 300 ms; one latency of 300.3 ms is the only budget miss added.
* Watch mode with `EARLY_CREDIT_S = 0` [C §7.7]: still 0 miss, 0 FP (X23's watch-mode latency becomes 999 ms on one seed
  because its sub-threshold detection no longer counts).

Latency distribution (open watch, 117 events): ≤0 ms 24 (growth evidence accumulated while tracked at 6–12 dB
prominence, or sub-threshold), 0–100 ms 31, 100–200 32, 200–300 13, 300–600 10, 600–1000 4, >1000 3 (X8 ×2, X9 ×1).

---------------------------------------------------------------------------------------------------------------------

## 3. Failure analysis (every budget miss; none is an FP or a missed ring)

| case | what happens | limitation of | could be fixed by |
|---|---|---|---|
| **S3 s2** 449/752 ms | a cymbal crash lands within 50 ms of `t_prom` and buries the 30 dB/s ring for 6 frames; the track is resurrected on its extrapolated ramp but the +12 dB common-mode step (crash) is subtracted from the rise until the wash decays | corpus coincidence + my conservative common-mode rule (a broadband *transient* is treated like a fader step) | distinguishing transient (decaying) from sustained reference rises; ~200 ms to gain on one seed |
| **S12 s3** 647 (600) | 196 Hz wedge ring on a strummed chord tone (G3); settle(band 33)=5, LF prominence rule; the ramp is contaminated by the chord's own G3 for the first 300 ms | LF caution by design | – |
| **X8 s2/s3** 2.2/2.9 s (1 s) | 1.4–5 dB/s ring whose band also holds the organ chord's E4-4f/C4-5f partials (0.3 band away): the level is a sum, prominence stays 6–10 for seconds, family flags flicker; growth needs 4 dB net of a 1.5–2.5 dB/s ramp = 1.6–2.7 s | genuinely marginal loop (e ≈ 0.03 dB) under partials in the same band; the old detector took 10–12 s or never [C §6] | a sub-band (two-line) decomposition of the band pair, or the probe (a human would nudge the fader) |
| **X9** 0.5/1.0/1.5 s (300) | ring *exactly* between bands 47/48, seeded by speech H4/H5 landing on those very bands every syllable: each syllable buries the pair for 4–6 frames, pulls the centroid, and resets the ramp; passively the ring must out-climb two syllables (13.6 dB/s) before its ramp is clean | 300 ms is below what passive evidence supports here (the old detector's 151 ms was luck: growth scored on speech energy, with 95 FP alongside [C §6]) | pair-equality template (P11) as extra evidence for exact-midpoint lines; watch-mode alert is immediate (candidate published from 6 dB) |
| **X10 s2** 349 (300) | second ring at exactly 2× the first, born 4 frames later at a 15 dB deficit and growing at a similar rate: for 4 frames it is *indistinguishable from a two-partial swell*, and the lone-octave rule holds the veto until the level ratio has drifted 3 dB | deliberate (§1.2 F'): the alternative is to cut swelling 2-partial tones (organ 8'+4', flute) on sight; the corpus calls this a <1 % coincidence [L §2.3] | accept |
| **X21 s3** 652 (600) | τ 70 ms reverberant ring seeded at −31 by a choir partial in the *same* band, 11 dB total growth to −20 with prominence <12 throughout; detected via the split-track back-fill 50 ms over budget on one seed; hold-out seeds miss 2/3 (the ramp never exceeds 6 dB net inside 1 s once the partial's share is subtracted) | hardest case in the corpus by construction [C X21 note]; old detector 0/3 | lower `growth_slow_total_db` (costs FP margin) or the probe |
| hold-out **X11 s4** miss | 150 dB/s howl emerging one band beside a loud vocal partial: only two growth frames are visible before the −12 dBFS plateau (below LOUD), and the odd clip partials + a coincident vocal line make a family | POP/GROW boundary at >100 dB/s with ≤2 visible frames [L §1.3 "single-frame evidence must be admissible"] — I chose not to admit 2-frame evidence in watch mode | `loud_level_db` −15, or an odd-harmonic clip-signature rule (tried: +3 FP under dense chords, withdrawn) |

Simulator vs reality: S3/X9/X8 depend on exact coincidences the corpus planted (crash at t_prom; speech partials on the
ring's band pair; chord partials 0.3 band from the ring); X10/X21 are the corpus's declared <1 %/irreducible cases. None
of the six is an LF/analyser artefact, none is a false positive, and all rings are reported within 3 s.

---------------------------------------------------------------------------------------------------------------------

## 4. Robustness (sweep.py; open loop watch; `sweeps.json`)

| configuration [C §5] | pass | TP | miss | FP | note |
|---|---|---|---|---|---|
| seeds 1-3 (reference) | 55/61 | 117/117 | 0 | **0** | |
| **hold-out seeds 4-6** | 56/61 | 114/117 | 3 | **0** | misses: X11 s4 (above), X21 ×2 |
| attack_k 0.32 (BQ, 3× faster LF bands) | 57/61 | 78/78 | 0 | 0 | seeds 1-2 for all sweeps |
| attack_k 1.0 (slower LF bands) | 57/61 | 78/78 | 0 | 0 | the settle rule is model-free (3/Δf) |
| skirt N=2 (leaky, 24 dB prominence ceiling) | 56/61 | 77/78 | 1 | 0 | X21 |
| skirt N=5 (M7 8 kHz datum) | 59/61 | 78/78 | 0 | 0 | |
| decay 4 s | 57/61 | 77/78 | 1 | 0 | X21 |
| release law 17 dB / 240 dB | 57 / 55 | 77 / 78 | 1 / 0 | 0 / 0 | 12 TAIL at law 17 (slow release re-detections of killed rings) |
| noise ×1.5 | 56/61 | 77/78 | 1 | 0 | |
| substeps 1 (onset phase) | 57/61 | 77/78 | 1 | 0 | X9 |
| 1 % dropped frames | 56/61 | 78/78 | 0 | 0 | |
| **det RMS** | 55/61 | 78/78 | 0 | **3** | X21 choir lines: `set_rta_source` forces PEAK; keep it forced |
| **display gain +12 dB** | 50/61 | 78/78 | 0 | **45** | whistles/crowd/808 at −9..−6 dBFS trip LOUD/escape: the four absolute thresholds (§1.2 J) *require* `/-prefs/rta/gain` = 0 at arm [A §2]; growth/family/probe logic is offset-free |

Arm-time requirements this design inherits from the briefs and now *depends on*: force `gain 0`, `det PEAK`,
`decay 0.25`, `peakhold OFF`, verify by delayed read-back, restore at disarm [A §2, N]. The detector reports
`flags['peak_hold_suspect']` (≥5 prominent bands bit-identical for 10 frames) and `flags['hot_spectrum']` (ref > −30)
as diagnostics; it does not gate on them. S15/S16/X13/X14 (long decay / peak-hold left on) pass regardless: the
decay predicate ignores release tails and growth survives max-hold.

---------------------------------------------------------------------------------------------------------------------

## 5. Cost
Pure Python 3.13, stdlib only, deterministic, no I/O. Measured over 14 540 corpus frames (seed 1, all scenarios):
**mean 422 µs/frame, median 304 µs, p95 1.24 ms, p99 1.70 ms, max 2.15 ms** (M-series Mac). Worst scenarios are the
dense organ+bass mixes (X12b mean 1.5 ms). O(bands·k) for prominence/peaks + O(tracks·window) for the ramp fits
(≤7 windows × ≤61 points × 2 smoothings per track); 20 fps leaves >95 % of a core idle. Memory: 64×100 floats of
spectrum history + ≤64-point histories per track.

---------------------------------------------------------------------------------------------------------------------

## 6. Tests (runtests: `tests/test_detector.py tests/test_rtasim.py tests/test_detector_corpus.py` 44 pass;
`tests/integration/test_cfs.py tests/integration/test_server_tools.py -k "cfs or ring or watch"` 24 pass; full suite
935 pass, 1 pre-existing environment failure `test_settings_defaults_and_env` asserting the checkout dir is named
`x32-mcp` — fails identically on `wt/corpus-critic`.)

Assertions changed in `tests/test_detector.py`, each with the physics:
1. `test_held_note_not_detected`: the held note now carries 2f (−4 dB) and 3f (−8 dB) — the partials this file's own
   melody generator gives every note; assertion `dets == []` kept. The original stream was a *family-less* line rising
   dB-linearly at 80 dB/s for 5 frames to −15 dBFS then flat: by every passive observable that is a loop with ≈0.4 dB
   excess on a wedge meeting a compressor [L §1.3/§1.4]; it passed the old detector only through the 60 dB/s onset
   guard that discards 35–70 % of genuine ring-out howls [A §0.2, L §1.3]. New companion test
   `test_familyless_dblinear_ramp_is_the_feedback_signature` asserts that stream IS reported within 6 frames with a
   `grow-*` reason. (If this is judged wrong, the residual risk it stands for — a solo family-less swell: theremin, sine
   pad with slow attack — is the one programme class this design will cut in watch mode; §7 Q4.)
2. `test_ring_detected_quickly` / `test_two_rings_both_detected` / vibrato-ring: lower latency bound −4 frames instead
   of 0 (`-4 <= i - t0 <= 6`). A line tracked from 6 dB prominence that has risen dB-linearly for ≥5 frames may be
   reported a frame or two before it crosses the 12 dB display threshold (`grow_prominence_db` 10.5); the upper bound
   (the requirement) is unchanged. `slope ≈ 15 ± 3` kept (reported slope = current rate over the last 6 frames).
3. `test_vibrato_rejected_with_longer_persistence`: the first assertion documented the OLD flaw ("persistence 3
   reports the vibrato note"); now both persistence settings must report nothing (centroid wobble >0.6 band is not
   stationary; climbing back to a previous maximum is not growth); the ring-with-persistence-6 assertion kept.
4. `test_override_is_disabled_by_zero` → `test_override_knob_is_legacy_and_inert`: the override belonged to the sum;
   an established, narrow, family-less, stationary, steady −25 dBFS line is feedback by P1/P3/P5/P8 whatever the knob
   says; the key still loads.
Unchanged and passing: clean music → 0; established 60 dB howl caught (now at frame 4 via `est`, slope 0); modest
15 dB plateau at −70 dBFS NOT caught (an `est` line below `est_min_level_db`); instant-onset note & 3-frame
transient → 0; noisy ring ≤12 frames; candidates/reset; all NotchController tests; detector→notch e2e.

---------------------------------------------------------------------------------------------------------------------

## 7. The brief's four questions, as this design answers them

**Q1 — Can a harmonic-structure test replace growth? Latency?** No — it replaces growth's *job as the music
discriminator* for lines that popped or were present at arm (single frame, and it is what lets an established howl be
cut 250 ms after arm at any prominence ≥12 dB instead of never), but it cannot be the whole discriminator: the corpus's
family-less programme (808, organ flue, flute top register, whistle, sine lead, bells, hum's exact family aside) is
exactly the set a harmonic test passes as "feedback" [A §4.3], and coincident partials in dense music (X7: G4's 4f one
band from the ring; X8/X21: a chord partial *in* the ring's band) make single-frame presence unreliable in both
directions. What carries those cases is **onset history** (popped vs grew vs was-there) and **co-movement** (a note's
partials share one envelope; a ring's neighbours do not), which are temporal but cheap: growth here costs 3–5 frames
*before* `t_prom` because lines are tracked from 6 dB, so the median latency is 100 ms and 24/117 detections precede
the 12 dB crossing. Growth is retained as evidence with **no rate ceiling** and an analyser-derived *floor on duration*
(3/Δf), which is the actual fix for M7's 40/80 Hz cuts.

**Q2 — Weighted sum or predicates?** Predicates. Each threshold above is a measurable physical quantity set from the
briefs, testable alone, and reported by name in `Detection.reasons`; there is no region of the input space that is
unreachable by construction, and no weight to re-fit per room. The sum's specific pathology — a 30 dB-prominent,
persistent, analyser-smeared bass note outscoring a plateaued howl — cannot be expressed in this structure.

**Q3 — Frequency window?** A hard 250 Hz–8 kHz window is a correct prior for one rig (HPF'd SM58 into tops) and wrong
for kick/tom mics on subs (X22, 65 Hz), acoustic guitars on wedges (X17 122 Hz, S12 196 Hz), lavs/lecterns (125–250 Hz)
and condensers (8–12 kHz) [L §3.2-3.3]. This design ships **no hard floor** (`lf_edge_hz` 0) and passes S1/S7/X5/X15/
S17/S19/S23 (every LF programme trap) *and* X22/X17/S12 (every LF ring) on physics alone: below `lf_strict_hz` 160 Hz
only sustained growth over ≥3/Δf (0.35–1.1 s) of a line that was prominent throughout, with no co-moving family, or a
probe response, can emit — bass has a family, kicks/808s decay and glide, room modes track the programme and answer
the probe 1 dB/dB, sustained subs pop. The window keys remain for the per-session prior the loop brief derives from the
open channels' HPF and mic kind (`lf_edge_hz` = 0.7×HPF, `hf_edge_hz` 10 k for all-dynamic rigs [L §3.4]); cfs should
fill them from preflight and print them in the report. The M7 cuts needed no window: they needed the analyser bound.

**Q4 — Uncertain ⇒ ? and does it differ by mode?** The asymmetry sits on *onset class and level*, and yes it differs.
A line that was **seen to pop** into existence and sits flat is programme until it does something only a loop does
(grows dB-linearly, reaches −10 dBFS/clip, or over-responds to a gain step): in `watch` it is published as a candidate
immediately (latency matters more than certainty for the human [L §4.3]) and never cut on passive evidence — cutting
tier-B lines at −20 dBFS as the loop brief floats would have notched every whistle, flute, organ and sine lead in the
corpus. A line **seen to grow** is cut at once at any rate (bounded harm: −3 dB, and VERIFY classifies it post hoc);
re-emission (deepening) requires the line to *hold its level*, so a cut that worked is never deepened onto the
programme line that shared the band. A line **present at arm** is cut after 250 ms if it is where a plateau can
physically be (≥ −40 dBFS) and shows none of a note's properties. In `ring_out` the server owns the safest actuator and
a free experiment: every +1 dB step is a probe, a line that answers ≥2 dB/dB twice (or ≥4 once) is cut *before* it
howls, one that answers ≤1.3 dB/dB twice is tagged stationary and protected from the level rules (hum, HVAC, driven
room mode S19), and a ramp that starts within 0.4 s of a step needs only 4 dB. What the detector cannot do alone and
cfs should: when uncertain in `ring_out`, step the master *down* 1 dB and watch the candidate (§L, [L §4.3]) instead of
dwelling under it; and force/restore the four RTA prefs the absolute thresholds depend on.

---------------------------------------------------------------------------------------------------------------------

## 8. Config keys added to `device.yaml detector:` (defaults = dataclass; existing keys untouched; legacy keys inert)
`mode`, `track_prominence_db`, `grow_prominence_db`, `narrow_db`, `track_min_level_db`, `family_prominence_db`,
`family_tol_bands`, `family_partials`, `family_veto_frac`, `family_ratio_tol_db`, `family_escape_level_db`,
`centroid_tol_bands`, `level_unsteady_db`, `decay_tol_db`, `analyser_settle_cycles`, `growth_fast_step_db`,
`growth_fast_total_db`, `growth_slow_frames`, `growth_slow_min_db_per_s`, `growth_slow_total_db`,
`growth_slow_total_long_db`, `growth_drop_tol_db`, `growth_window_frames`, `pop_step_frac`, `est_arm_frames`,
`est_frames`, `est_min_level_db`, `loud_level_db`, `loud_frames`, `clip_level_db`, `lf_edge_hz`, `lf_strict_hz`,
`hf_edge_hz`, `ref_band_lo/hi`, `probe_settle_frames`, `probe_window_frames`, `probe_excess_db`, `probe_strong_db`,
`probe_linear_db`, `probe_min_history_frames` — each with its rationale in the yaml comment. API additions:
`FeedbackDetector(cfg, band_hz, mode=None)`, `.note_gain_step(delta_db, ts)`, `.note_cut(band_hz, depth_db, ts)`,
`.tracks`, `.flags`; `Detection.reasons: tuple[str, ...]`; `Candidate` gains `centroid`, `birth`, `verdict`, `reasons`,
`probe_*` (all in `to_dict()` for the dashboard). `min_level_db −45` is no longer a gate (it was one room's music
[HANDOVER §4b(3)]); nothing absolute gates tracking.

Honest list of constants that are *tuned* rather than derived: `grow_prominence_db` 10.5 (12 → X21 misses a seed, 9 →
3 FP), the co-movement bounds (½·rise−2, 4 dB floor, ratio 3 dB, slope −15/+18 %), the ramp-shape fractions (55 %, 75 %,
thirds 0.4/0.15, halves ¼/0.3, rise-time 0.4), `probe` null factor 2×+1. They were set once and hold on hold-out seeds
and 12/14 analyser variants with 0 FP; they should be re-checked against logged frames from the real desk [L §6.6].
