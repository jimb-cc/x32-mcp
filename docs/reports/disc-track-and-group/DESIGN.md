# disc-track-and-group — "track, then group, then classify" feedback discriminator

Branch `wt/disc-track-and-group` (worktree `…/scratchpad/worktrees/disc-track-and-group`), file `src/x32mcp/detector.py`
(FeedbackDetector fully rewritten, 1738 lines incl. the unchanged NotchController; public contract kept:
`FeedbackDetector(cfg, band_hz)`, `.feed(values_db, ts) -> list[Detection]`, `.candidates`, `.reset()`, `.cfg`,
`DetectorConfig.from_descriptor(Descriptor.load())` works with the shipped device.yaml; new optional API:
`FeedbackDetector(cfg, band_hz, mode='watch'|'ringout', lf_feedback_possible=bool)`, `note_gain_step(delta_db, ts)`,
`note_cut(hz, depth, ts)`, `Detection.reasons/onset/tier/centroid`, `det.flags` {PEAK_HOLD_SUSPECTED, FROZEN_LINES}).
Citations: [L §x] loop-physics brief, [A §x] analyser brief, [C §x] CORPUS.md, [N] lead notes. Line numbers refer to
`src/x32mcp/detector.py` on the branch.

## 0. Headline (rtasim corpus, 61 scenarios × seeds 1,2,3; `reports/disc-track-and-group/metrics*.json`)

| run | scenarios pass | events | TP | miss | **FP** | early | tail | harm | latency med / p90 / max | ≤300 ms | within budget |
|---|---|---|---|---|---|---|---|---|---|---|---|
| informed (mode + LF opt-in + steps), open loop | **53/61** | 117 | 117 | **0** | **0** | 5 | 0 | 0 | 150 / 447 / 2502 ms | 97/117 | 104/117 |
| informed, closed loop (NotchController, 32 feedback scenarios) | 24/32 | 116 | 116 | 0 | **0** | 3 | 0 | 0 | 148 / 451 / 2502 | 96/116 | 103/116 |
| blind (`FeedbackDetector(cfg, band_hz)`: watch, no opt-in, no steps), open | 51/61 | 117 | 111 | 6 | **0** | 2 | 0 | 0 | 152 / 447 / 2502 | 94/111 | 98/111 |
| blind, closed loop | 21/32 | 117 | 111 | 6 | **0** | 2 | 0 | 0 | | | |
| *baseline (current detector, [C §6])* | *12/61* | *117* | *108* | *9* | *948* | *9* | *21* | *143* | | | |

Zero false positives on every music / note / control / prefs / crowd / hum scenario in all four runs (baseline: 948), zero
missed rings when the detector is told what cfs knows (mode, LF opt-in), zero HARM/TAIL cuts. The blind misses are exactly
X17 (122 Hz) and X22 (65 Hz): genuine LF loops below the watch window's 160 Hz edge, found with `lf_feedback_possible=True`
(that is what the opt-in is for, [L §3.4]). All 8 remaining informed failures are **latency only** (§4).

## 1. Architecture (per frame; O(bands) + O(peaks·30) lookups; measured 0.29 ms mean, 0.74 ms p99, 2.8 ms max per frame,
CPython 3.13, 8 scenarios / 2320 frames, ≤ 11 live tracks)

1. **Peaks** (`_find_peaks` L575): per band `prominence = level − median(±3)` (L563) and, new, a *cluster prominence* =
   power(b−1..b+1) − median(bands ±2..±4); the larger counts, so a line half-way between centres (−3/−3 dB, S10/X9) is not 3 dB
   short [A §3, C §2 item 11]. A local max with prominence ≥ `peak_floor_db` 6 is a peak. Per peak: **centroid** = vertex of the
   parabola through the dB levels of b−1,b,b+1, clamped ±0.5 (edge tone → b+0.5 from either side ⇒ one object; interior offsets
   are compressed ~25 %, which is fine because harmonic partners share the compression); **cluster power** cp (offset- and
   vibrato-invariant, [A §4.4]); **narrowness** = level − max(level at ±2) [N P1, A §3(a)].
2. **Tracks** (`Candidate`, `_associate` L876): peaks ≥ `track_floor_db` 8 dB start a track — 4 dB under the emission
   threshold, so a ring already has 5–15 frames of history when it becomes "visible". Association by centroid: pass 1 radius
   `assoc_tol_bands` 0.6; pass 2 radius 0.85 only for a track whose own centroid has been swinging (vibrato); pass 3 lets an
   unmatched track claim a peak anywhere inside the span it swung over recently (±0.35). A peak ≥ 0.35 band away that is ≥ 6 dB
   above the track's last cluster power (or turns up after the track went missing ≥ 2 frames) is a **new object** and starts its
   own track (the X16 lesson: a wedge ring born a band away from a bass partial must not inherit the partial's history). Tracks
   survive `max_gap_frames` 5 masked frames (crash/snare, speech partials hopping over a ring). Per track, bounded histories
   (48) of centroid, peak-band level, cp, cp lower envelope (3-frame min: removes additive hits), prominence, narrowness,
   spectrum reference, family flag; and a back-fill of the band's last 16 frames (cluster power and peak-band level) taken at
   birth from a frame ring buffer.
3. **Onset class** at frame 3 of a track (`_classify_onset` L961), from back-fill + first 3 frames of the peak-band level:
   `at_arm` (born in the first 4 frames: no onset information); `adult` (arrived gliding ≥ 0.4 band over its first 3 frames, or
   reached within 1.5 dB of its 3-frame max within ≤ 2 frames of leaving the floor [floor = median of the back-fill; "left" =
   +min(6, 0.35·rise)]: an instrument note, or an LF onset smeared by the analyser — a one-pole at τ_a still puts > 65 % of the
   dB rise in the first two frames [A §1 table, L §1.6]); `ramp` (best run of ≥ 3 consecutive increments ≥ 0.4 dB, ≥ 4 dB total,
   second-half mean ≥ 0.5 × first-half, no single/pair of increments carrying the rise); else `slow`.
4. **Grouping** (`_group` L712, `_compatible` L652, `_coborn` L607, `_cogrowing` L631, `_coramping` L616). For each peak P
   and hypothesis "P is harmonic m of f0=f/m", m∈{1..5}, partials k∈{1..6}\{m} are predicted at centroid+10·log2(k/m) bands
   [A §4.1] and looked up among *peaks* within `harmonic_tol_bands` 0.3 (36 ¢): a partial above P must be within
   `partner_window_db` 12 dB of P's level (howl distortion products are ≥ 12–30 dB down [L §2.2]; X11's H3 at −14 dB is thereby
   not a partner), one below may be up to `sub_window_db` 15 dB quieter (HPF'd bass H1 under its H2 [A §4.2]); m ≥ 4 only if k=1
   is present and ≥ 10 dB louder (otherwise chords "explain" too many positions by chance — X7's ring sat 36 ¢ from 5×E4).
   A partner counts only if its track is **compatible** with P's: born within 3 frames (or both at arm) *and not diverged since*
   (level difference changed ≤ 5 dB — S3 seed 1: an organ C6 struck in the very frame the 3×C6 ring became trackable; the
   ring then out-climbed it by 30 dB); or ≥ 6 frame-aligned common samples whose envelopes correlate (both ranges ≥ 2.5 dB,
   Pearson ≥ 0.85, difference drift ≤ 4 dB: co-swell/co-decay) or whose increments correlate (ρ ≥ 0.6, both sd ≥ 0.3 dB:
   vibrato through the skirts, tremolo); or < 6 common samples (too young to judge: benefit of the doubt — a partial crosses the
   tracking floor later than its fundamental in a slow swell or through a slower LF band). **Family** := some hypothesis has ≥ 2
   partners, or exactly 1 partner within 0.2 band of the exact position that is *co-born* (±2 frames, or both at arm) or
   *co-growing* (≥ 5 common frames both ≥ 8 dB prominent, both risen ≥ 3 dB, level-difference range ≤ 0.8 dB). Two rings of one
   rig at ×2 (X10, S11b ×2.016) are born 6–12 frames apart and their difference drifts at |R1−R2| = 10–12 dB/s, so they are
   neither; the 8′+4′ organ pair (S6/S6b/X12), flute+H2, pad H1+H2 are. If the single exact-position partner is < 5 frames old,
   or is itself climbing (≥ 3 dB/8 frames) at a level within 10 dB of P, P is marked **pending**: growth-based cuts are *held*
   (not vetoed) until the relation resolves — this is what absorbs the chorused synth pad (S21), whose partials beat at k×
   the detune rate and therefore fail every envelope test, at a cost of ≤ 250 ms on the one S11b seed where the second ring is
   born inside the first one's decision window.
5. **Behaviour** (windowed, in `_decide` L1247): `family` = family flag on ≥ 40 % of the last 20 frames, or lineage (≥ 6 of
   10 once; fades 2 s after the last family frame — S5's guitar note decaying to a sine is meanwhile `adult` and `decaying`);
   `stationary_k` (`_stationary` L1165) = over the last k centroids, outliers beyond 0.35 band ≤ 25 %, **MAD ≤ 0.15 band**
   (±25 ¢ vibrato shows; a neighbour partial yanking the interpolation for a frame does not) and last within 0.75 of the
   median; `glide` = birth glide (first 15 frames) or a window-to-window median step > 0.5 band (legato note change / hop);
   `comoving` (`_comoving` L1181) = over 12 frames, on frames where the reference (median of bands 25..85) is within 4 dB of its
   window floor (drum hits excluded), reference range ≥ 1.0 dB, regression of the cp lower envelope on the reference has slope
   0.6..1.5 and residual ≤ 0.35·sd+0.15 (fader/common-mode move [A §5]; also set when ≥ 2 other tracks ramp at 0.5–2× this
   one's rate = ensemble swell/crescendo, rings start alone [A §5]); `decaying` = lower-envelope LS slope ≤ −3 dB/s over 12
   frames with ≥ 1 dB drop (plucked/struck notes 3–15 dB/s [L §2.1]; a killed ring; S15's 3.75 dB/s display release);
   `frozen` = peak-band value bit-identical on ≥ 6 of the last 7 steps (peak-hold display, S16 — a live line through a real
   analyser never repeats to 1/256 dB; exempt at clip); `steady` = lower-envelope range ≤ 2.0 dB over 15 frames; `slow_rise` =
   LS over the last 16–30 lower-envelope samples: 0.8–8 dB/s, ≥ 3 dB, residual ≤ 0.8 dB, not comoving (S13/X8 marginal loops
   [L §1.3 row e=0.1]); **ramp** (`_ramp_stats` L1019, cached per frame) = better of (a) the run of per-frame increments ≥ 0.4 dB
   ending now (one dip allowed, gaps time-normalised, leading noise blips trimmed) and (b) an LS line over the last 6..16 samples
   (slope ≥ 6 dB/s, rise ≥ 4 dB, residual ≤ max(0.6, 0.06·rise) [0.12·rise for ≥ 12 samples at ≥ 12 dB/s], halves' slopes
   within 0.5–2.5×, no increment > half the rise, last increment ≥ 30 % of the fitted per-frame rate) → (n_inc, rise, slope,
   still, ref_floor_change, linear, strict); `ramp_ok` additionally needs the reference's *lower envelope* not to have risen by
   more than half the line's rise [N P4] and the line's own lower envelope to climb at ≥ 40 % of the fitted rate (vibrato
   riding the skirts cannot fake it); `fast` (`_fast_ramp` L1139) = peak-band level (with back-fill) rose ≥ 15 dB in ≥ 3
   consecutive steps of ≥ 5 dB/frame, none > half the rise, not fallen back; **probe** (`_probe` L1216, ring_out only): for
   each announced master step the track lived through, median cp over [t+0.2, t+0.65] minus median over [t−0.45, t) minus the
   step (sign-aware) → `probe_strong` (≥ 4 dB once or ≥ 2 dB with ≥ 1.2 dB the step before), `probe_mild` (≥ 1 dB),
   `probe_linear` (|·| ≤ 0.8 on the last steps) [L §1.5, A §0.3].
   **Re-qualification**: ≥ 6 linear increments and ≥ 8 dB of solitary growth turn an `adult/slow/at_arm` track into `ramp` and
   truncate its family record to the last 3 frames; ≥ 0.8 s of solitary dB-linear creep turns `adult` into `slow` (a chord
   tone's/kick's band taken over by a ring: X12, X17, X19, X21, X22, X8).

## 2. Decision logic (L1428–1462) — predicates, no weighted sum [brief Q2]

Gate (`base`): centroid inside the window [c(f_low)−0.5, c(f_high)+0.5]; level ≥ `level_floor_db` −72; visible = prominence
reached `prominence_db` 12 within the last 5 frames and is ≥ 9 now; narrow (≥ 9 dB on ≥ half of the last 5 frames); age ≥
`persistence_frames` 3; not family; stationary over 5; not frozen. `settled` = gate ∧ no glide.

* **A:clip** — level ≥ `clip_level_db` −3 (family ignored: a howl at full scale grows odd harmonics [L §2.2, A §4.3]; the
  corpus renders a line pinned at 0.0 as −1.7).
* **A:loud** — level ≥ `loud_level_db` −10 for 5 frames, settled, not moving [L §4.3 tier A]. (Loudest programme line in the
  corpus: crowd whistle −17; legacy held-note test peaks at −11.9.)
* **A:ramp** — `ramp_strong` ∧ stationary over the ramp's own span ∧ no family flag now ∧ no partner pending, where
  ramp_strong = ramp_ok ∧ [(n_inc ≥ 6 ∧ still rising) ∨ (rise ≥ 24 ∧ n_inc ≥ 4) ∨ (n_inc ≥ 4 ∧ still ∧ strictly linear ∧ rate ≤ 45
  dB/s ∧ prominence ≥ 14)] ∨ fast; an `adult`-born track needs rise ≥ 10 dB. Physical content: exponential regeneration is
  linear in dB at e/τ [L §1.3]; ≥ 300 ms of it, or ≥ 24 dB of it, or ≥ 200 ms of it slower than any note attack and not
  decelerating (the analyser's step response decelerates [L §1.6]), or ≥ 150 ms at ≥ 100 dB/s in even steps (a 40–80 ms note
  attack makes ≤ 2 steps) — while nothing else rose (reference floor) and no partial co-onset.
* **A:probe** — probe_strong, settled (ring_out; also admits an `adult` steady line after 15 frames).
* **A:prominent** — onset `at_arm`, prominence ≥ `strong_prominence_db` 18 now and as 5-frame median, steady, level ≥
  `min_level_db`, settled, not decaying, not probe-linear: the established ring at arm (S2a/b/c, X7, X12 episode 1) in 250 ms
  [N "established ring at arm is emitted 250 ms after arming"].
* **B** — after `watch_confirm_frames` 15 clean frames (stationary over 15, not moving/comoving/decaying/glide/probe-linear),
  onset ∈ {at_arm, slow, ramp} and one of: slow_rise; ramp_ok; steady ∧ level ≥ min_level ∧ (prominence median ≥ 18 [watch] |
  in ring_out: probe_mild on the last step, or no steps announced).
* `adult` onset + not loud/clip ⇒ never cut in watch (published as candidate; `reasons` says why). In ring_out only the probe
  can promote it.
* Emission: per-track cooldown `cooldown_s`, band cooldown ±`band_tolerance`; no re-emission while `decaying` (VERIFY sees the
  cut work; a 16 s RTA release is not re-cut: S15 0 TAIL). `Detection.freq_hz` is interpolated from the window-median centroid
  (X9's 525 Hz between GEQ 500/630 reports 522–526 Hz), `band` = nearest RTA band, `reasons` lists tier, onset, prominence,
  narrowness, ramp figures, probe response. `confidence` is a monotone display figure (≥ threshold iff a tier fired).

What is deliberately NOT used: absolute growth window/onset guard (growth is evidence, never required, never capped [N]);
`min_level_db` as a global gate (it gates only steady no-growth lines: X20's −50 dBFS whine is the corpus's own
"irreducible_passive"; a line seen to grow is judged from −72); P5 "new energy over a per-band baseline" [N] — **rejected**: an
established ring at arm IS the baseline, and X7/S2 must be cut; the information P5 wanted is carried by the onset class instead.

## 3. Frequency window [brief Q3]
`f_low` = 160 Hz watch / 100 Hz ring_out / 40 Hz with `lf_feedback_possible`; `f_high` 12.5 kHz (`DetectorConfig.f_low_hz`,
L262) — the per-source derivation of [L §3.4] reduced to what the detector can be told today (cfs should compute it from open
channels' HPF and patch kind and pass it; keys are in device.yaml). LF lines inside the window get no special tier: the
onset/decay/family/stationarity predicates already reject τ_a-smeared bass (S1, S7, X5, X15, S17, S19, S22/X9/X20 speech F0)
— in the informed run S12 (196 Hz), X17 (122 Hz), X22 (65 Hz, kick+bass on the same band) are found with 0 FP.

## 4. Per-scenario results (informed, open loop; closed loop identical except S11b max 1451 ms) and failure analysis

53/61 pass, FP 0 everywhere; the 8 failures are all late TPs:

| scenario | lat min/med/max | budget | why late (mechanism) | class |
|---|---|---|---|---|
| S3 ring during music | 1/102/352 | 300 | crash at t=5.0 lands 1–3 frames after visibility on seeds 1–2 and masks the 30 dB/s ring for 250 ms; the harness counts masked time | sim coincidence (fixed crash grid) / real: masking costs what it lasts |
| S11b two rings ×2.016 | 48/174/447 | 300 | second ring born inside the first one's decision window at an exact-octave position ⇒ "pending" hold ≤ 250 ms (§1.4) | designed trade: pad/organ octave pairs vs a < 1 % coincidence [L §2.3] |
| S12 ac-guitar 196 Hz | 402/451/652 | 600 | ring shares band with the G3 chord tone; visible growth starts under the chord's decay; A:ramp after 8–9 frames | real limitation at 1/10-oct: merged lines |
| X8 slow 1.29 k under chords | 502/2000/2502 | 1000 | 1.4–5 dB/s under organ chords + piano: the track is repeatedly re-born/merged with chord partials 1–2 bands away (narrowness < 9, prominence < 12 for seconds); found by slow_rise once clean | real: a 3 dB/s ring 12–15 dB prominent inside a chord is at the edge of what this resolution shows |
| X9 525 Hz mid-point + speech | 899/1100/1102 | 300 | speech partials sweep across the ring every syllable: the track is born on a partial (adult/glide), the ring must re-qualify by ≥ 8 dB solitary ramp at 13.6 dB/s ⇒ ~0.9 s | real; a human sees it faster only by knowing speech |
| X11 amp-clipped −12 dBFS | 100/849/849 | 300 | 150 dB/s under a −36 dB bed leaves only 2 clean steps before the −12 plateau on 2 seeds (a 2-step arrival = a 50 ms note attack); caught by tier B (ramp onset, steady, 24 dB) at 850 ms | the irreducible corner: a compressor-caught ≥ 150 dB/s ring 2 dB under the loud tier; `loud_level_db −12` would take it (and the legacy held-note test's −11.9 dBFS plateau with it) |
| X19 hand-held stall/hop | 51/248/400 | 300 | seed 2: vocal partial on the band at birth ⇒ re-qualification | as X9 |
| X21 reverberant 11 dB/s + choir | 298/298/603 | 600 | seed 3: choir D4 0.7 band below merges into one peak; 3 ms over | merged lines |

Blind adds X17/X22 (missed: below 160 Hz without opt-in) and X23 closed-loop 300.3 ms on one seed. EARLY detections (X23 5,
M2 1–2): probe-confirmed sub-threshold modes 0.4–2 s before runaway — the desirable ring_out behaviour [L §4.3].

Sweeps not run for lack of time (declared): attack_k, skirt_order, decay_s/release_law, RMS, noise_sd_scale [C §5]. By
construction the design does not read absolute slopes against fixed windows, uses lower envelopes against release/peak-hold,
and flags peak-hold (S16: 0 FP + `PEAK_HOLD_SUSPECTED`), but the numbers above are for the default analyser only.

## 5. Tests
`tests/test_detector.py` 20 pass, `tests/test_rtasim.py` + `tests/test_detector_corpus.py` sanity 21 pass, integration
`tests/integration/test_cfs.py` + `test_server_tools.py -k "cfs or ring or watch"` 24 pass, whole suite (minus TCP) 931 pass /
1 unrelated env failure (`test_settings_defaults_and_env` asserts the checkout dir is named `x32-mcp`; fails identically on
`wt/corpus-critic`). Assertions changed (3), each replacing an encoded property of the old heuristic:
1. `test_vibrato_rejected_with_longer_persistence`: `run(cfg3,…) != []` → `== []`. It asserted the old detector's *known false
   positive* on a vibrato note at persistence 3. A note hopping ±1 band at 5 Hz is one track whose centroid swings a band
   (vibrato ⇒ programme [L §5 P4]) and which arrived at full level in one frame; it must not be cut at any persistence. The
   ring half of the test is unchanged and passes (detected +5 frames).
2. `test_override_is_disabled_by_zero` → `test_override_key_is_accepted_but_no_longer_needed`: it asserted that
   `override_prominence_db=0` restores the pre-M7 miss of a 60 dB-prominent established howl, i.e. it certified the bug. The key
   still loads (old yaml files work) and is ignored; the howl is caught (A:prominent, 200 ms).
3. `test_two_rings_both_detected`: latency bound `0 ≤ i−t0 ≤ 6` → `−8 ≤ i−t0 ≤ 6`. `first_crossing` uses single-band prominence;
   the detector also measures cluster prominence [A §3] and legitimately fires up to 5 frames *before* that crossing on the
   630 Hz ring. Later than 6 frames still fails.
All requirement tests pass unchanged: clean music 0, held note 0 (its 80 dB/s 5-increment attack is not a strong ramp: that
needs ≥ 6 increments still rising, or ≥ 24 dB, or ≤ 45 dB/s, or ≥ 3 steps of ≥ 5 dB; its 20 dB-prominent plateau at −12…−18 dBFS
is then kept out of tier B because it co-moves with the breathing floor and the LFO's down-slopes read as decay — see §6 for
the honest caveat), ring ≤ 6 frames after crossing (+5), two rings, noisy ring ≤ 12, plateau+transient 0, plateaued 60 dB
howl caught (200 ms), modest 15 dB plateau not caught, notch e2e, candidates/reset, config.

## 6. Known weaknesses (do not hide)
* The `adult`-onset veto is the load-bearing wall against near-sine instruments (X1–X4, S8, S20/X18 whistles, S24). A ring with
  e ≥ 3 dB on a ≤ 10 ms loop (≥ 300 dB/s) that plateaus below −10 dBFS in ≤ 2 frames is classified `adult` and, in watch mode,
  only published. [L §1.3] says such rates are normal in watch mode; the mitigation is the loud tier (−10) and the human on the
  fader; in ring_out the probe resolves it within one step.
* Merged lines (< 1 band apart) are one peak at this resolution; a ring emerging next to a sustained partial is late by the time
  it needs to out-climb it (S12, X21, X8).
* The legacy synthetic held note (80 dB/s linear-dB attack, no partials, no vibrato, −12…−18 dBFS) is physically a ring
  signature for its first 250 ms; it is not cut only because a strong ramp demands ≥ 300 ms or ≥ 24 dB or ≤ 45 dB/s or ≥ 100
  dB/s, and its plateau is then vetoed by co-movement with the breathing floor. A real 80 dB/s ring that is limiter-caught after
  exactly 5 frames at −15 dBFS would be treated the same (published, cut only via tier B after 750 ms if ≥ 18 dB prominent and
  not co-moving — which a ring is not, so it IS cut at ~1 s). I consider that acceptable and said so rather than tune to it.
* Coincidence budget of the grouping: harmonic_tol 0.3 band and compat rules were set by physics (interpolation error, co-onset
  within the analyser rise, envelope ratios of partials) but validated only on this corpus; dense real mixes will produce more
  chance partners than organ+piano. The failure direction is *late*, not *wrong cut*.
* `min_level_db` from cfs `_calibrate_floor` (max band + 8 dB over 2 s) can be raised to −20 dBFS by music at arm and would
  then silence A:prominent/tier B; the detector no longer needs that calibration (recommend removing it in cfs).
* RTA prefs: the design tolerates decay 16 (S15/X13 pass) and detects peak-hold (S16/X14 pass + flag) but cfs should still force
  decay 0.25 / peakhold OFF / autogain OFF at arm and restore at disarm [A §2, N].

## 7. Config keys added to `device.yaml detector:` (all defaulted in `DetectorConfig`, L129–221; old keys still load)
peak_floor_db 6, track_floor_db 8, narrow_db 9, level_floor_db −72, f_low_watch_hz 160, f_low_ringout_hz 100, f_low_lf_hz 40,
f_high_hz 12500, lf_feedback_possible false, (mode 'watch' — set per session by cfs), harmonic_tol_bands 0.3,
partner_window_db 12, sub_window_db 15, family_ratio 0.4, family_window_frames 20, confirm_frames 5, watch_confirm_frames 15,
centroid_tol_bands 0.35, centroid_mad_bands 0.15, stationary_outlier_frac 0.25, glide_bands 0.75, strong_prominence_db 18,
loud_level_db −10, clip_level_db −3, ramp_min_step_db 0.4, ramp_strong_frames 6, ramp_strong_rise_db 24,
ramp_moderate_db_per_s 45, ramp_fast_db_per_s 100, ramp_linearity 0.5, decay_db_per_s 3, decay_window_frames 12,
plateau_range_db 2.0, ref_lo_band 25, ref_hi_band 85, comove_ref_range_db 1.0, comove_window_frames 12, probe_over_db 2.0,
probe_settle_s 0.2, probe_window_s 0.45, arm_frames 4, assoc_tol_bands 0.6, max_gap_frames 5, history_frames 48,
backfill_frames 16, max_tracks 48. Legacy keys (growth_*, weights, confidence_threshold, override_*) are read and inert.
`from_dict` now also parses bool/str fields.

## 8. The brief's four questions, as this design answers them
1. **Can a harmonic test replace growth?** It replaces growth *as a requirement* and does it in one frame, but presence-as-peak
   alone is both too weak (near-sines have no family: X1–X5, S7, S8, whistles, bells, hum's neighbour the HVAC whine) and too
   strong (chords explain many positions by chance; two rings can sit at ×2; a clipping howl has odd partials). What made it
   work was **grouping tracks, not peaks**: a partner counts only with a shared history (co-onset without divergence, or
   correlated envelopes), single partners need exact position + co-birth/co-growth, and the clip level overrides. Growth is
   retained as *positive* evidence (tier A:ramp) measured as linearity of the dB envelope with no rate ceiling, and the onset
   *shape* (adult vs ramp) does most of the note-vs-ring separation that "growth" was trying to do. Latency: grouping and
   onset are decided by frame 3 of a track; because tracks start 4 dB below visibility the decision is usually ready at
   visibility: median 150 ms, 83 % ≤ 300 ms, established rings 200 ms, fast wedge rings 50–150 ms.
2. **Weighted sum or predicates?** Predicates (§2). Every cut carries `reasons`; every threshold is a physical quantity with a
   cited origin; no surplus in one dimension buys a deficit in another (the M7 bass note had prominence, persistence and
   analyser-made growth — here it is `adult`/`decaying`/in a family/out of window, four independent vetoes).
3. **Frequency window?** A legitimate prior when derived per session, a hack as a constant [L §3]. Implemented as mode-dependent
   edges + LF opt-in; the informed/blind split on X17/X22 (found/missed, never mis-cut) is exactly the cost of not telling the
   detector about a kick mic. Inside the window LF programme is rejected by physics, not by the edge (S1/S7/X5/X15/S19: 0 FP in
   ring_out where f_low is 100 Hz and in the LF-opt-in scenarios where it is 40 Hz).
4. **When uncertain?** Watch: cut only on tier-A evidence or on a steady ≥ 18 dB line that survived 750 ms of scrutiny; an
   ambiguous (`adult`, 12–18 dB, moving, co-moving) line is published as `cfs.candidate` with its reasons and never deepened —
   the human owns the fader and a wrong −3 dB is cumulative [L §4.2–4.3]. Ring_out: the server owns the gain, so uncertainty is
   resolved actively — every +1 dB step is a probe (`note_gain_step`), super-linear responders are cut *before* runaway (X23:
   5 EARLY, M2: 1–2 EARLY), linear responders (hum, HVAC, driven room mode S19, playback) are vetoed, and a steady line with no
   step history waits. Recommended cfs follow-ups: call `note_gain_step` on every RAISE/back-off write; pass `mode` and
   `lf_feedback_possible`; on `PEAK_HOLD_SUSPECTED`/`FROZEN_LINES` refuse to arm or force prefs; use `Detection.reasons` in the
   notch report; drop `_calibrate_floor`.
