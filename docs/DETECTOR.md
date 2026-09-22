# CFS² feedback discriminator — design, evidence, results

`src/x32mcp/detector.py` (FeedbackDetector; NotchController unchanged), `device.yaml detector:` (every key = the dataclass default,
asserted by `tests/test_detector.py::test_device_yaml_detector_block_equals_the_dataclass_defaults`), tests in
`tests/test_detector.py`, `tests/test_detector_predicates.py` (one per predicate / evidence rule / flag / hook),
`tests/test_detector_regressions.py` (rendered corpus scenarios), `tests/test_detector_corpus.py` (harness). Offline corpora:
`tests/rtasim` — 61-scenario main corpus (`CORPUS.md`) + 68 adversarial breakers (`scenarios_adversarial.py`: the five design
auditors' 55, `AUDIT_ADVERSARIAL`, and the final verifier's 13, `VERIFIER_BREAKERS` AV01–AV13); the measuring instrument is
`python -m rtasim.run_detector_eval {main,holdout,sweeps,adversarial,breakers,cost,all}` (+ `rtasim.run_verifier_breakers` for
closed-loop introspection of the AV set).
Citations: **[L §x]** loop-physics brief, **[A §x]** analyser brief (x32-mcp review material, kept with the review, not in this tree), **[C §x]**
`tests/rtasim/CORPUS.md`, **[J]** the design-competition judge's verdict. Frame = one `/meters/15` frame = 50 ms; levels are RTA dB
(−128 floor, 0.0 = the analyser's clip flag); band *i* centre = 10000·2^((i−90)/10) Hz; K1 = 5 frames = 250 ms; K2 = 12 frames.

This detector is the competition winner ("explicit physical predicates") with the judge's fixes F1–F6 and grafts G1–G6 and the
detector side of G7/G8 integrated, then audited against the 55 breakers, the hold-out seeds and the analyser sweeps (§6), then
re-verified by two independent reviewers whose blocking findings (B1 at-arm inheritance, B2 two-frame FAST-RISE evidence, B3 held
howl never deepened in watch, LOUD unreachable in loud shows, missing F2 test) are fixed here (§11 lists every finding and its fate).

## 0. What the instrument shows (the facts the design is built on)

1. **Growth is manufactured by the analyser at LF.** A 1/10-octave band (Δf = 0.069 f) cannot settle faster than ~1/Δf: 370 ms at
   39 Hz, 185 ms at 78 Hz, 7 ms at 2 kHz. An instant bass onset renders below ~300 Hz as a decelerating multi-frame ramp; any rate
   window on "growth" therefore selects LF programme (M7's 40/80 Hz cuts) [A §0.1, L §1.6]. → rises inside 1.5·k/Δf of a line's
   birth are never scored (`analyser_rise_k` 1.0 = twice the simulator's τ, an uncertainty bound, not a fit [C §7.1]).
2. **Real rings grow at e/τ = 1 to > 500 dB/s** and plateau wherever a limiter, a compressor or the bus clip stops them, at any tap
   level from −30 to 0 dBFS [L §1.3-1.4]. Growth is visible for 0.1–3 s; a plateaued howl with no growth and no clip is the expected
   steady state, so growth can be *evidence* but never a *requirement*.
3. **A note is a harmonic family** (partials at exactly +10, +15.85, +20, +23.2 bands, 0…−15 dB re H1, sharing onset and envelope);
   a linear loop has none, a clipped howl grows odd partials ≥ 10 dB down [L §2.2, A §4.1]. The family test is decisive only when the
   candidate is ≳ 20 dB prominent; below that partials can be buried [A §4.3].
4. **A ring's frequency is fixed by geometry** (centroid sd < 0.05 band); vibrato, scoops, glides, portamento move [A §4.3 v, §4.4].
   Hand-held mics make rings *hop* to a neighbouring candidate (a new line), not glide [L §2.3].
5. **The tap is pre-fader**: a bus-master move does not move electrical programme; the open mic's pickup follows 1 dB/dB; a loop
   within 3 dB of threshold over-responds ≥ 2 dB per dB (1/(1−g)) [L §0, §1.5]. In ring_out the server owns the steps: a free probe.
6. **Absolute levels float on unread prefs** (`/-prefs/rta/gain`, `decay`, `peakhold` — UNCONFIRMED whether gain reaches /meters/15
   [meters.md:311]); the detector references its one absolute leg to the arm-time spectrum, measures the display release itself, flags
   peak-hold and hot displays, and cfs pins the prefs (gain 0, decay 0.25, peakhold off) [A §2, J G6].

## 1. Per frame, whole spectrum (`feed`)

* `prom[i]` = level − median(±3). A **line** (`_lines`) is a local maximum with prom ≥ `track_prominence_db` 6; its **cluster** is the
  peak plus a neighbour within `cluster_merge_db` 6 (a tone between two centres reads −3/−3; edge vibrato alternates the louder band).
  Per line: cluster power; **cluster prominence** over the median of the six bands ±2..±4 beyond the cluster; **narrowness** = peak −
  max(the two bands just outside the cluster, i.e. at lo−2 / hi+2); **centroid** = power-weighted position of peak±1 with the local
  floor removed (±0.1–0.2 band = ±12–25 c against rendered tones, e.g. a 1934 Hz line reports 1901 Hz; `freq_hz` is interpolated
  from it, so a 525 Hz midpoint ring reports 525 Hz for the GEQ choice — ample for a 1/3-octave GEQ).
* **Common mode** (`_cm_rows`): the change between two stored spectrum rows (96 kept, each with a 3-frame running mean) = MEDIAN of the
  per-band changes of the reference bands 25..85 that sit ≥ `ref_signal_db` 6 above the frame's 10th-percentile floor at both
  instants, outside the line's own ±4; in a quiet room (< `ref_min_bands` such bands) every band ≥ floor + 3 over the whole spectrum
  (F5: the winner returned 0 there, which made its own +1 dB ring_out steps invisible to the correction). Rises are taken net of
  max(0, Δcm − `cm_deadband_db` 2) (a mix breathes ±1–2 dB; a fader shove that could fake 6 dB is larger).
* **Baseline** `base[i]`: asymmetric one-pole low tracker (0.5 down / 0.05 up per frame during the first `baseline_seed_s` 1 s, then
  fast down 0.15, slow up 0.002); P5 excess = peak − base. A band that currently holds a qualified tracked line does not pull its
  baseline UP (the baseline is "what the band read before anything happened"; a standing line would otherwise absorb itself in ~40 s
  and demote from MODERATE to TRACK while the tier-B `excess_db` melted away — verifier finding). There is no absolute candidate gate
  (`min_level_db` is a legacy key; `level_gate_db` −128 = off). Input values are clamped to [−128, 0] (NaN → −128): `feed()` is public
  and one non-finite frame must not poison the baseline or the arm reference.
* **Tracking**: lines ↔ tracks by centroid within `track_match_bands` 0.6 (tracks younger than K1 pay `track_young_penalty` 0.3);
  unmatched tracks coast `coast_frames` 8; a track whose centroid range over `drift_window_frames` 10 exceeds `drift_max_bands` 1.0 is
  **re-born** with no history (`glided_in`). "Born at arm" = born in the first `arm_frames` 3 only (F1: the winner re-granted it to any
  later line at the same pitch ±0.25 band and level ±6 dB — an organ note that recurred was cut, AP02 15 FP → 0) **and it is a property
  of the line, not of the track slot** (B1): the moment the presence run restarts because another source landed on the track (a >
  `onset_step_db` jump out of a line that was not climbing, a re-acquisition after > K1 missed frames, the LF onset variant) the flag and
  its evidence are cleared — a staccato note re-struck at the at-arm pitch after a 0.3 s rest, or a note landing on a bed bump that was
  a 6 dB local maximum on the arm frames, was cut as `established_at_arm` before (AV03 5 FP / 9 seeds, X1 seed 11 → 0). Each track also
  keeps `onset_fi`, the frame at which the source now on it arrived; "born together" (the H2-pair rule) compares births OR onsets.
* **Back-fill** (`_backfill`, G1): a newborn track (≥ 300 Hz or LF alike) pulls its band's own visible climb from the spectrum store —
  the trailing frames on which the peak band kept falling going back in time while ≥ bed median + `backfill_floor_margin_db` 3 and a
  local bump — with the bed median as the level it rose out of. `_seed_run` (bands settling within a frame only) makes that climb the
  start of the RISE reference when its steps are each ≤ `onset_step_db` (a slow ring under a bed is a track only for the last part of
  its climb).
* **Instrument flags** (`_analyser_flags`, refreshed every frame into `det.flags`; none of them cuts anything, cfs acts on them):
  `PEAK_HOLD_SUSPECTED` (≥ `peak_hold_bands` 15 bands ≥ floor+3 bit-identical for ≥ `peak_hold_frames` 10: a live band through
  int16/256 always jitters [A §2, C S16]); `HOT_SPECTRUM` (arm-time p95 ≥ `hot_spectrum_p95_db` −20 dBFS **and** p90 ≥ −26 — one
  full-scale howl with skirts and two partials is 5 % of the bands, not 10 % — or ≥ 3 bands at the clip flag spanning more than one
  cluster, held `hot_hold_s` 5 s: a display gain of tens of dB or a bus slammed into its limiter; the clip flag then means "display
  clipped" — it is neither LOUD evidence nor a level — and LOUD's level legs additionally require the line to stand
  `loud_hot_over_max_db` 3 above the loudest cell of the arm window, §3 LOUD); `SLOW_RELEASE` (display release rate, MEASURED as 20 ×
  the largest single-frame fall of any LIVE reference band — ≥ floor + 3 and not parked on the −128 code at either instant, ≥
  `ref_min_bands` 6 of them — over 2 s; the bed always exercises it: 60 dB/s on the default corpus (which is the simulator's own release
  cap: the reading there is the instrument's limit, exactly as it will be on the desk), 15 at decay 4 s, 240 at decay 0.25; a floor-pinned
  display measures nothing and raises nothing; the number is "the display CAN fall at least this fast" — an instant note-off on a
  fast display reads hundreds of dB/s, a display limited by its release law reads that law — below `slow_release_db_per_s` 30:
  programme lines are being stretched into continuous tracks, so growth evidence is acted on only for lines ≥ `strong_prominence_db`; the
  first item of the desk re-test is to read `det.release_db_per_s` at decay 0.25 before trusting this gate); `PROGRAMME_PRESENT` (§4.6);
  `FROZEN_LINES` (§2, FROZEN).

## 2. Per track — predicates (each independently tested; thresholds = `device.yaml detector:`)

| # | predicate (feedback ⇒ true) | rule / threshold | physics | corpus evidence |
|---|---|---|---|---|
| P1 NARROW | cluster prominence ≥ `prominence_db` **12** ∧ narrowness ≥ `narrow_db` **8** | | a sinusoid clears 24–60 dB at ±2 (2nd–5th-order skirts); humps, washes read 0–6 [A §3]. 8 not the physics brief's 15: narrowness is bounded by the louder ±2 band, and a ring two bands from a chord partial reads 8.6–9.8 (X7 s3) | X7, S10, S3; unit `test_p1_*` |
| P2 NO FAMILY | see module docstring / §0.3; partial present = a line ≥ `partial_prominence_db` 6 within ±`partial_tol_bands` 0.6 of the exact offset, level within `partial_rel_db` 18, **co-moving** (Δlevel over K1 within `comove_tol_db` 3); family = ≥ 2 of H2..H5 or a lone exact H2 within `h2_pair_rel_db` 12 born within `co_onset_frames` 2; also "I am H2/H3 of a lower line owning another partial" (unless I am > `harm_over_base_max_db` 12 above it) or "H4/H5 of a base ≥ `subharm_hi_margin_db` 3 louder"; LOUD escape for the line's own family; late family (after `late_partials_rise_db` 12 of rise) = distortion; MUSICAL when vetoed on ≥ `family_veto_fraction` 0.4 of the last 20 frames (and not clean for the whole last K1 — unless the fraction is ≥ 0.7) or most of the last K1; "born together" = track births OR source onsets (`onset_fi`) within `co_onset_frames` 2. **G5**: a partner that APPEARED (its band ≥ `independent_swing_db` 8 quieter over the K1 frames before its birth, and ≥ 8 over its baseline now) more than `co_onset_frames` after the candidate while the candidate held within `independent_hold_db` 3 is an independent source; one line still (≤ `pitch_still_bands` 0.1 over K1) and a clear one moving (≥ `pitch_moving_bands` 0.3) do not share a pitch modulation | | partials are exact multiples sharing onset and envelope [A §4.1]; instrument partials 0…−15 dB re H1, ≤ +10 for vowels/HPF'd bass [A §4.2] — a line 15–18 dB ABOVE its would-be fundamental is not its H3 (AS08); a note landing at a steady ring's harmonic slot is not its partial [L §5 P1/P12]; all partials carry the same vibrato in cents [A §4.4] | S4, S5, S6, S12, X10, X19, S2c/S9 (0 HARM), AS08 0/3 → 3/3, AS02 published 2.75 s → 0.4 s, X7-under-RMS (band swing, §6.4) |
| P3 STABLE | centroid range over K1 ≤ 2·`centroid_tol_bands` **0.25** (±30 c) and presence run ≥ K1 | | ring sd < 0.05 band; vibrato ≥ ±30 c on an edge, scoops, glides fail [A §4.3 v/4.4]; slower glides are re-born by the drift rule | S4a/b, S18, X3, X4, X6, AM02 0 FP |
| P4 SUSTAINED | not (LS slope over ≤ 12 frames ≤ −`decay_db_per_s` **3** with drop ≥ `decay_drop_db` 1.5) and sag under the K2 max ≤ `sag_db` 4 | | plucked/struck notes decay 3–15 dB/s; the RTA release of a killed ring falls ≥ 3.75 dB/s even at decay 16 [L §2.1, A §2] | S15 0 TAIL, S24, S5 |
| COMMON MODE | |Δcm| ≥ `common_mode_db` 6 over K2 with the line within `common_mode_tol_db` 2 ⇒ MUSICAL (that frame) | | programme riding a fader / autogain [A §5] | S6, S23a, X12a |
| CO-GROWTH (F4) | I am swelling ≥ `cogrowth_rise_db` 3 (net of cm over the last 20 frames, ≥ 1 dB of it in the last 8) and ≥ `cogrowth_lines` **2** OTHER lines whose runs started within max(`cogrowth_coborn_frames` 4, 6 dB/my rate) of mine are swelling ≥ 1.5 (half: a chord's quieter notes clear the bed later) at 0.5–2× my rate, > 1.5 bands from me, on ≥ 2 consecutive frames ⇒ MUSICAL; on the 3rd the RISE reference is re-anchored; never applied to an emitted line | | one swelling source lifts its lines together; rings start alone and grow at their own e/τ [A §5, L §2.3]; two simultaneous rings stay allowed | S21; AP06 (three rings after one shove) 5/9 → 9/9 ≤ 250 ms; AP05/AP10 pass |
| P5 NEW / AT-ARM | excess ≥ `baseline_excess_db` **10**; a line born within `arm_frames` carries AT-ARM evidence instead (§3) | | hum/HVAC/rumble live in the baseline; an established howl is judged on what it looked like at arm | X20 hum, C0, S17 |
| P6 WINDOW | `window_low_hz` ≤ centroid ≤ `window_high_hz` 12.5 k: **160 Hz watch / 63 ring_out / 40 with `lf_feedback_possible` / `lf_edge_hz`** (cfs: ≈0.7×the open channels' HPF, F5: no ordering constraint) | | [L §3.4]; measured: with the window at 40 Hz the corpus still gives 0 FP on every LF programme scene — the settle rule, not the edge, keeps LF programme out [J Q3] | X17/X22/S12 with declaration; S1/S7/X5/X15/S19/AT09-11 0 FP |
| FROZEN (G3) | peak AND at least one of its ±1 skirt bands bit-identical over the last `frozen_frames` 5 (= K1), below clip − 3 dB ⇒ no BASE for K2, flag | | a peak-held display freezes a line together with its skirts; a rock-steady tone (brick-wall limiter, still air) can repeat its own int16/256 code while its skirts, 18+ dB nearer the noise, still jitter — that is a line, not a frozen display (verifier AV13: 0.00 dB wander was never cut before); a clamped (clipping) tone is exempt | peakhold sweep 0 FP + flags; unit test (peak-hold vs live vs steady-peak-only) |

## 3. Evidence (any one turns BASE into STRONG; none is required)

**RISE** — cluster level now − reference ≥ `rise_db` **6 dB** (a doubling of amplitude; ≥ 1 dB above the largest excursion any
BASE-qualified programme line reaches on either corpus outside the irreducible swell class: 5.0 dB LF-808 with the LF window open,
4.5 peak-held organ, 4.2 whistle, 4.1 saw pad — the review's programme rise-margin scan over both corpora). Reference = the `rise_quantile` 0.2 low
quantile of the presence run's last `rise_window_frames` 64 settled levels, remembered as an age-unbounded **low-water mark** once
`rise_ref_min_samples` 8 exist (F3: the winner's bare 64-frame window made rings slower than ~2.3 dB/s unreachable — AP07 0/3 → 3/3,
S13 2/3 → 3/3), carrying its own spectrum row for the common-mode comparison and re-anchored when the centroid moves > ±0.5 band (a
loop does not move while it creeps; a drifting vocal partial does — X16 hold-out FPs in the first F3). The rise is net of common mode
and **capped by the peak band's own rise + 3 dB** (a second programme line joining the cluster adds power without raising the peak; an
edge line loses at most 3 dB on its peak reading). The run starts after the analyser settle allowance (1 frame ≥ 300 Hz — never 0: the
birth frame of a 20–100 ms acoustic attack must not seed the reference — 2 @ 200 Hz, 4 @ 100 Hz, 7 @ 63 Hz, 11 @ 39 Hz) and
**restarts** (i) on a > `onset_step_db` 4 dB single-frame jump when the line was not already climbing — two consecutive rising frames, or
≥ 4 contiguous settled samples fitting ≥ `onset_rising_db_per_s` 10 dB/s within `onset_rising_resid_db` 0.75 **and the last two
increments still positive** (a line that stepped up and sat flat was not climbing: the next jump is a strummed chord's second string —
hold-out X17 s7); (ii) on re-acquisition after > K1 missed frames unless climbing; (iii) below ~300 Hz on > `lf_onset_step_db` 6
within one settle window out of a window that moved < `lf_onset_flat_db` 2, or a fall of `restart_drop_db` 6 under the run maximum, and
there half the rise must be older than one settle window. No rate window: 2 dB/s and 500 dB/s both count. The optional GROWTH
early-upgrade (clean dB-linear fit) ships OFF (`growth_rise_db` = `rise_db`; at 4 it fired on a pad partial on a hold-out seed).

**FAST-RISE** (G1) — over the track's matched frames plus its back-filled pre-birth climb (bed median as a virtual first sample): ≥
`fast_rise_min_steps` **3** of the last ≤ 5 increments (windows of 5, 4, 3 tried) are each ≥ `fast_rise_step_db` 2 and ≥ 15 % of the
window's total — the other increments in the window are unconstrained except that the last must be ≥ 1 dB (still rising) — summing to
≥ `fast_rise_db` **15 dB** net of common mode, largest ≤ `fast_rise_max_share` 0.55 of it and the two largest ≤ 0.75, the window ending
on this frame or the one before (the limiter knee; not if it then fell back > 1 dB), a new high for the line (+3 dB over anything
older); LF: an unbroken run of ≥ settle + 2 increments ≥ 2 dB. **The step out of the bed is an inferred increment (B2)**: it counts
toward the three only for a line that has reached the LOUD-ISH line max(`loudish_level_db` **−20** dBFS, arm p95 + `loudish_above_arm_db`
**10**) — tier B's level, one tier (10 dB) under LOUD on both legs [L §4.3]; below it three OBSERVED increments (≥ 150 ms of dB-linear
climb) are required, because two observed increments (100–150 ms) are also what a soft / breath / legato attack shows [A §4.2:
100–250 ms] and the judge's G1 guard was "do not admit 2-frame evidence in watch". Sticky until the line falls `restart_drop_db` under
its maximum. With FAST-RISE the K1 age may be taken on matched frames (the restarting jumps were the growth). Physics: exponential loop
growth is exactly dB-linear [L §1.3]; an instrument attack (5–80 ms) is one or two increments however the frame boundaries fall.
Evidence: X11 0/3 → 3/3 (151–203 ms), AS03 3/3, M1/S9/X14/X12 50–100 ms sooner (all loud-ish: their two visible increments + the bed
step count); AV02 (flute breath attacks 100–170 ms at −24…−29 dBFS) 43 FP / 15 seeds → 10, the strummed-choir partial of X21 hold-out
s6/s11/s13 and AV01 s6 → 0. Boundary that remains (§7.9): a family-less note whose attack from the bed lasts ≥ 150 ms shows three
observed increments — the judge-literal G1 signature of a 100–170 dB/s howl under a bed — and is cut once.

**LOUD** (G6) — peak at the clip flag (unless HOT_SPECTRUM), or peak ≥ max(`loud_line_db` **−10**, min(arm-time p95 +
`loud_above_arm_db` **20**, `loud_ceiling_db` **−6**)) AND ≥ `loud_margin_db` **6** above every band outside its ±3 neighbourhood; below
160 Hz only the clip flag inside a declared LF window (LF loops are long and slow, kick/808 put single LF bands far above everything
[L §3.2 e]); void for a STATIONARY line. The p95 is the 95th percentile of every (band, frame) value over the first `arm_baseline_s` 2 s
(≈ the show's loudest programme bands; `refresh_arm_reference()` re-opens the window, §9); a sine-like source concentrates ~18 dB more
per band than the programme it is as loud as [A §1], so "20 dB above p95" is louder than programme can be, and the bar stops rising at
−6 dBFS: within 6 dB of full scale a steady line that also dominates the frame by 6 dB is at an electronic limit (the loop's) or is a
deliberate solo tone into the mic (the irreducible whistle class, §7.3) — never a mix balance, however loud the show. **Under
HOT_SPECTRUM** (arm p95 ≥ −20 and p90 ≥ −26, or broadband display clipping) 0.0 is not LOUD and the level legs also require the line to
stand `loud_hot_over_max_db` **3** above the loudest cell the display showed over the arm window (within 6 dB of full scale is then
ordinary; above everything the programme reached is not — and a display-gain offset shifts the arm maximum with everything else).
Measured thresholds: quiet music p95 −38…−45 → −10; loud band p95 −14…−20 (HOT) → max(−6, arm max + 3) = −4…−7: X16's 240 dB/s howl at
−2.5 (programme max −7) is cut at 200 ms on 8 of 9 seeds (the ninth had a programme cell within 3 dB: MODERATE, tier B), X14 hold-out
6/6 (was 3/6), M1 6/6; AM03's whistles (−4…−2 dBFS in a show that reached −5 during the arm window) stay MODERATE (0 FP); a whistle
6 dB above everything AND 3 dB above anything the show reached would be cut (the AF08 class, §7.3). Display gain sweeps (HOT lets the
level legs act on lines that clear the arm maximum): +12 dB 53 (winner) → 18 FP, +24 dB 310 → 25 (§6.3; cfs pins `/-prefs/rta/gain` 0).

**AT-ARM** (P5) — over the track's first K1 matched frames (which must fall within its first K1 + 2 frames of life: a plateaued howl
is present on every frame short of a masking transient) median peak ≥ `arm_line_min_level_db` **−40** and median cluster prominence ≥
`strong_prominence_db` **18**; present ≥ `arm_presence` 0.8 of the frames since arm and steady within `arm_range_db` 6 over
`arm_range_frames` 16; family-vetoed on ≤ `arm_family_life_max` 0.2 of its frames (at-arm rings measure 0.00, an organ note whose
partials momentarily fail co-movement 0.86–1.00); fewer than `cogrowth_lines` other such family-less at-arm lines within
`arm_cohort_rel_db` 12 (a chord; ≥ 3 simultaneous plateaued howls do not occur [L §2.3]); then emitted at K1 if ≥ `arm_fast_level_db`
**−20**, else once it outlasted a note (`arm_confirm_s` 0.8). **ring_out** (G4): below −20 dBFS only after `probe_min_hits` 2 judged
steps, and never if it FOLLOWED them (§PROBE): nothing regenerative is *established* at ring_out arm unless the room already howls.
−40 / 18 / 0.8 s are single-room guesses (§8): X7's ring at −30 vs a whine at −36…−50 vs an organ note at −30 are the same passive
observation in watch [C §7.10, J §5.6].

**PROBE** (ring_out; cfs calls `note_gain_step(Δ, ts)` after each master write) — for a line steady (range ≤ `probe_steady_db` 3)
before the step: response = median over [ts + `probe_settle_s` 0.15, ts + `probe_window_s` 1.4] − median of the 0.55 s before. **HIT**:
≥ Δ + `probe_over_db` 2 with the common mode ≤ Δ + 0.5 (regenerative gain 1/(1−g) [L §1.5]); **FOLLOWS**: ≤ Δ + 1 and ≥
`probe_pinned_fraction`·Δ (a source in the room heard through the mic: `stationary` while no hit ever and follows ≥ pinned →
STATIONARY, never cut, AT-ARM and LOUD void); **PINNED**: < 0.35·Δ (electrical — absent by contract, hum has a family — or a howl
whose plateau is held DOWNSTREAM of the pre-fader tap: a speaker limiter answers 0 dB/dB, a channel compressor ~1/ratio; no verdict,
the step counts as judged). Steps are keyed by a sequence number (F2: the winner's list indices broke after 32 steps). `probe_min_hits`
2 hits = evidence; two hits on a still sub-threshold, stable, in-window TRACK line = a pre-emptive PROBE emission (X23: 1.15–1.3 s
before the crossing; S6b, M2 by RISE at ≤ 250 ms). S19/AT11 (driven room mode, 1 dB/dB) and AF05 (whine, 1 dB/dB) → STATIONARY. A
STATIONARY line that is ≥ 30 dB prominent, ≥ `arm_line_min_level_db` and never carried a family gets `backoff_advised` in its reasons:
a howl whose plateau is held by a 2:1 channel compressor also follows +1 dB steps exactly 1 dB/dB [L §1.4(2); verifier AV09], and only a
BACK-OFF larger than the excess separates it from a whine (the howl dies, the whine drops dB for dB) — cfs should make one (§11 N1).

## 4. Classes, emission, hooks

```
per track and frame (after MUSICAL bookkeeping):
  FALSE_CUT   the line ENDED by itself after a cut (note_cut verdict): a note went through the EQ; never emitted again unless it grows rise_db anew
  MUSICAL     family ∨ common-mode ∨ co-growth
  MODERATE*   BASE ∧ evidence ∧ a family present on THIS frame ('family_now': a swell's partials surface after its fundamental)
  STRONG      BASE ∧ (RISE ∨ FAST-RISE ∨ LOUD ∨ AT-ARM ∨ PROBE)          → Detection(klass STRONG, reasons)
  STATIONARY  BASE ∧ followed the probe steps                              → reported, never cut
  MODERATE    BASE only                                                     → published in det.candidates, never cut by the detector
  TRACK       everything else (incl. WINDOW / frozen / young)
BASE := P1 ∧ ¬MUSICAL ∧ P3 ∧ P4 ∧ (P5 ∨ born-at-arm) ∧ P6 ∧ ¬frozen
watch: emit STRONG.   ring_out: emit STRONG and pre-emptive PROBE (Detection.klass "PROBE"); MODERATE after K2 only with
                      ringout_emit_moderate (off: 226 FP on music; when on, Detection.klass "MODERATE", reason confirmed_K2).
```
* **Re-emission = a request to deepen** (G2/G7): only on evidence gathered since the previous emission — the line grew
  `reemit_rise_db` **3 dB** above its emitted level (a loop that beat the cut climbs on; a plateau wanders/pumps 1–3 dB [C §7.5]), or is
  LOUD now **and did not come down since the emission** (the cut never reached it: desk clip after the insert, a POST insert — a line
  that dropped by the bell is the verdict's business), or scored a new probe hit, or `note_cut()` filed **held / insufficient with a
  deepen right** (below) — never while a verdict is pending / false_cut / ambiguous, at most once per `cooldown_s`. With `note_cut()`
  wired (cfs.py `_notch` calls it after every GEQ write) a wrong cut on a quiet or rise-only line is one −3 dB step; a wrong cut on a
  loud-ish line made on plateau-class evidence can be walked to `notch_max_db` −9 in `cut_verify_s` steps if the note is held that long
  (§7.1/7.3 give the measured counts) — the price of deepening limiter-held howls, bounded to one GEQ band.
* **`note_cut(freq_hz | band, depth_db, ts, step_db=None)`** — after each GEQ write cfs reports the band's new TOTAL gain at the GEQ
  centre; every track within 1/3 octave is watched against the bell attenuation expected AT THE LINE = RBJ peaking cut at the line's
  offset from the centre, bracketed for GEQ Q `geq_q_min` 2 … `geq_q_max` 4.3 (X32 shape UNCERTAIN [L §4.1]; −3 slider = −3.0 /
  −2.2 / −1.5 dB at 0 / 0.1 / 0.167 oct for Q 4.3). Verdicts (`Candidate.cut_verdict`, `det.cut_log`): **confirmed** — drop ≥ bell +
  `cut_confirm_extra_db` 3, or the line collapsed/vanished within `cut_settle_s` 0.15 + settle + `cut_response_s` 0.3 (a killed loop
  decays at (atten−e)/τ, tens to hundreds of dB/s, time-locked to the write [L §4.1, P13]) — only the EMITTED track can be confirmed by
  vanishing (a bystander note within reach of the bell that merely ends then gets no verdict: verifier N4); **insufficient** — after
  that window drop < bell − `cut_false_tol_db` 1 or climbing again (excess exceeds the cut, or the bell missed the line); **held** (B3)
  — drop = bell ± 1, flat, line still present at `cut_verify_s` 1.5: EITHER a note that went through the EQ OR a howl whose plateau is
  set by a limiter / compressor with more excess than the cut (downstream of the tap the SPL is pinned and the tap simply drops by the
  cut; upstream, a limiter's output through the GEQ drops by the cut too; a compressor of ratio r reads 3r/(r−1) dB [L §1.4]) —
  passively identical; **false_cut** — the line ENDS by itself after the response window (a loop that survived a cut does not switch
  itself off; a note does) → klass FALSE_CUT, never re-emitted (cfs ignore-lists / releases), re-admitted if it later grows `rise_db`
  above that level; **ambiguous** otherwise (e.g. a slow ring-down: AV07's marginal ring). **Deepening on held / insufficient**: one
  re-emission per verdict (so −3 → −6 → −9, each step verified again, reason `deepen_held` / `deepen_insufficient`) iff the line was
  emitted on PLATEAU-CLASS evidence — FAST-RISE, LOUD, PROBE or AT-ARM — and was loud-ish (≥ the LOUD-ISH line of §3 FAST-RISE) when
  emitted. Physics: a howl that HOLDS after a 3 dB cut has e > 3 dB, which on any loop up to τ ≈ 70 ms means it grew ≥ 43 dB/s
  (FAST-RISE territory: ≥ 40 dB/s resolved frame by frame) or had already plateaued when judged (LOUD / AT-ARM / PROBE); a ring cut on a
  slower RISE alone had e < 3 dB and a 3 dB cut takes it under threshold (it decays, possibly slowly: 'insufficient'/'ambiguous', never
  'held'), so for rise-only and for quiet lines "held" resolves to "a note went through the EQ": cut once, verdict exposed
  (`Candidate.cut_verdict` = 'held', `cuts_held`, `det.cut_log`) for cfs to alert "cut did not remove the line". Measured (verifier
  AV05: +4 dB excess, 180 dB/s to a −16 dBFS limiter plateau on a GEQ centre, watch, closed loop): before — one −3 dB cut, 'false_cut',
  howl alive at the end 3/3; now — 'held' at +1.5 s, `deepen_held` → −6 dB, 'confirmed' (drop 12 dB), dead 3/3. AV06 (GEQ midpoint) is
  killed via regrowth as before; AV07 (e 2.85, the cut just kills it) gets one cut and no false_cut. The deepen right is exercised on the
  evidence the line was CUT on, as long as it is still a BASE line (klass MODERATE suffices): after two cuts a held line sits 6 dB under
  its maximum, where FAST-RISE has lapsed and LOUD no longer holds — driven end to end against FakeDesk (cfs feedback_watch, in-memory
  UDP): a held −6 dBFS line goes −3 → −6 → −9 at 1.55 s intervals, each step 'held', and stops at `notch_max_db` (the planner refuses,
  no write, no new verdict, no further emission). In ring_out cfs's own NOTCH→VERIFY loop deepens on drop < 6 dB regardless and drops
  the detector's duplicate request for that band.
* **Tier-B hook** (G7 is cfs policy): every `Candidate.to_dict()` carries `klass`, `level_db`, `prominence_db`, `excess_db` (over the
  band's baseline, which no longer absorbs a standing line), `age_s`, `reasons`, `freq_hz` (interpolated, ±0.03 oct), `cut_verdict`,
  `cut_deepen`, `cuts_held`, `steps_seen`, `fast_rise_db`, `born_at_arm`, `stationary`, `probe_hits`. The fast howl to a quiet
  limiter/compressor plateau (≤ 2 visible increments: > ~300 dB/s under a bed, plateau below the LOUD line) is passively a sine-lead
  note onset [J §5.1]; it is published as MODERATE at K1 = 250 ms (AM01 248–251, AT02 199–250, AF03 197–200, AS02 197/400/200 ms).
* **`programme_present()` / `PROGRAMME_PRESENT`** (G8 hook): over `programme_window_s` 2 s, ≥ `programme_min_events` 3 note events (a
  line born after arm reaching 3 qualified frames, a re-struck line, a re-pitched line), or ≥ `programme_min_transients` 2 broadband
  transients (≥ `programme_transient_fraction` 25 % of the fast reference bands up ≥ 6 dB in one frame), or mean occupancy ≥
  `programme_occupancy` 1.0 qualified non-at-arm lines not called feedback, not STATIONARY / probe-following, and not born inside a
  probe window after one of the server's own steps (hum partials and room tones that the rising master lifts over the tracking
  threshold are not programme — verifier AV11: the flag used to latch for seconds after the talker stopped under ring_out steps).
  Quiet room, X23/S6b/AF05 ring_out rooms, S2a, hum+whine: never; speech, kick, 808, bass lines, pads: yes; organ melody 57–84 % of
  frames, M2's quiet background music 40 % (False at the t = 2 s arm check on 2 of 3 seeds), a dense loud band with no line at 12 dB
  prominence (M1) 0 % — it is informational; cfs should evaluate it over the whole PREFLIGHT + first steps (≥ 5 s) and OR it with what
  it knows (playback channel open, preamp meters) [§11].

## 5. Cost
Pure Python 3.13, per 100-band frame on busy music scenes (M1, X7, X16, S21, X18, M2, S20; `run_detector_eval cost`, 2260 frames):
**mean 296 µs, p99 729 µs, max 849 µs** this round (275 / 688 / 829 at 8094eed, the verifiers re-measured 282-293 / 711-729 / 848-894:
load-dependent, always < 1 ms; winner 250 / 768 / 950; the X7 scene with organ chords + piano + bass is the worst at 547 / 777 / 849).
See §6.5. A 48 000-frame soak (code reviewer) showed no structure growing.
O(bands·k) for prominences/lines, O(lines²·4) for the family test (present_near scans), per track O(64 log 64) for the rise quantile
and ≤ 8 common-mode row comparisons (each O(61)); all histories are bounded deques (64), the spectrum store 96 rows, steps 64,
cut_log 64, cooldown pruned every 100 frames. Deterministic (no RNG, no set iteration in decisions).

## 6. Results

Measured with `python -m rtasim.run_detector_eval main holdout sweeps adversarial breakers cost` (tag final2_a), `main holdout sweeps
--modes=watch_tag,ringout_tag` (final2_b), `holdout --seeds=10..15` (final2_unseen) and `adversarial_closed`, device.yaml config, frame
jitter ±3 ms, at the fix-round commit. Scoring per CORPUS.md §1: TP within [t_onset, t_end + 1 s], latency from the 12 dB-visibility frame,
EARLY = sub-threshold ringing of the same loop ≤ 2 s before onset, TAIL = after the episode, HARM = a clipped howl's own harmonic; closed
loop additionally fails a detected ring left ALIVE (effective excess > 0.25 dB at the last frame). "8094eed" columns = the measurement the
verifiers reviewed. **Shipped configuration** = the plain `watch` / `ringout` columns (this branch's cfs passes no LF declaration); `_tag` =
with the LF window cfs is meant to derive from the channel HPFs / `lf_feedback_possible`.

### 6.1 Main corpus (61 scenarios, seeds 1-3), open loop, per scenario

Cell = TP/miss/FP [eN EARLY tN TAIL hN HARM] latency min/med/max ms, P = all seeds pass (0 FP, 0 miss, latency ≤ budget), Fk = k seeds pass. `watch`/`ringout` = drop-in (LF window 160 / 63 Hz); `_tag` = as cfs configures it (LF window opened for the scenes that declare an LF-capable source: S12, X17, X22, AF04/07/14, AT09-11). Winner = wt/disc-predicates @ 2fc52ee, watch open.

| scenario | ev | budget | winner watch | 8094eed watch_tag | watch | watch_tag | ringout | ringout_tag |
|---|---|---|---|---|---|---|---|---|
| C0_silent_room | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| C1_music_bed_drums | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| S1_bass_under_quiet_music | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| S2a_established_ring_8k | 3 | 300 | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P |
| S2b_established_ring_8k_steep | 3 | 300 | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P |
| S2c_established_clipped_2k4 | 3 | 300 | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P |
| S3_ring_during_music | 3 | 300 | 3/0/0 199/398/702 F1 | 3/0/0 199/398/702 F1 | 3/0/0 199/398/702 F1 | 3/0/0 199/398/702 F1 | 3/0/0 199/398/702 F1 | 3/0/0 199/398/702 F1 |
| S4a_vocal_vibrato | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| S4b_vocal_vibrato_band_edge | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| S5_guitar_note_decays_to_sine | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| S6_master_ramp_feedback_watch | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| S6b_ringout_steps_latent_loop | 3 | 300 | 3/0/0 200/250/300 P | 3/0/0 2/202/250 P | 3/0/0 2/202/250 P | 3/0/0 2/202/250 P | 3/0/0 2/202/250 P | 3/0/0 2/202/250 P |
| S7_808_sub_bassline | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| S8a_organ_melody | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| S8b_flute_held_note | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| S8c_whistle | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| S9_clipped_howl_fast | 3 | 300 | 3/0/0 248/252/253 P | 3/0/0 147/150/152 P | 3/0/0 147/150/152 P | 3/0/0 147/150/152 P | 3/0/0 147/150/152 P | 3/0/0 147/150/152 P |
| S10_ring_between_bands | 3 | 300 | 3/0/0 298/301/502 F1 | 3/0/0 298/301/452 F1 | 3/0/0 298/301/452 F1 | 3/0/0 298/301/452 F1 | 3/0/0 298/301/452 F1 | 3/0/0 298/301/452 F1 |
| S11a_two_rings | 6 | 300 | 6/0/0 149/301/598 F1 | 6/0/0 2/277/498 F1 | 6/0/0 2/277/498 F1 | 6/0/0 2/277/498 F1 | 6/0/0 2/277/498 F1 | 6/0/0 2/277/498 F1 |
| S11b_two_rings_near_octave | 6 | 300 | 6/0/0 147/225/302 F2 | 6/0/0 99/175/252 P | 6/0/0 99/175/252 P | 6/0/0 99/175/252 P | 6/0/0 99/175/252 P | 6/0/0 99/175/252 P |
| S12_acoustic_guitar_wedge_ring_196Hz | 3 | 600 | 3/0/0 500/551/553 P | 3/0/0 500/551/553 P | 3/0/0 500/551/553 P | 3/0/0 500/551/553 P | 3/0/0 500/551/553 P | 3/0/0 500/551/553 P |
| S13_slow_ring_3dB_s | 3 | 1000 | 1/2/0 1048/1048/1048 F0 | 3/0/0 652/752/1198 F2 | 3/0/0 652/752/1198 F2 | 3/0/0 652/752/1198 F2 | 3/0/0 652/752/1198 F2 | 3/0/0 652/752/1198 F2 |
| S14_ring_masked_by_cymbal | 3 | 300 | 3/0/0 198/198/200 P | 3/0/0 198/198/200 P | 3/0/0 198/198/200 P | 3/0/0 198/198/200 P | 3/0/0 198/198/200 P | 3/0/0 198/198/200 P |
| S15_long_rta_decay_tails | 3 | 300 | 3/0/0 149/152/202 P | 3/0/0 202/250/298 P | 3/0/0 202/250/298 P | 3/0/0 202/250/298 P | 3/0/0 202/250/298 P | 3/0/0 202/250/298 P |
| S16_peak_hold_on | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| S17_kick_pattern | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| S18_vibrato_on_band_edge | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| S19_driven_room_mode | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| S20_song_start_stop_crowd | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| S21_synth_pad_swell | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| S22_speech_ringing_then_feedback | 3 | 300 | 3/0/0 48/102/148 P | 3/0/0 48/102/148 P | 3/0/0 48/102/148 P | 3/0/0 48/102/148 P | 3/0/0 48/102/148 P | 3/0/0 48/102/148 P |
| S23a_autogain_drift | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| S23b_gain_offset_clip | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| S24_bells_triangle_glock | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| M1_loud_band_wedge_ring | 3 | 300 | 3/0/0 248/251/300 P | 3/0/0 147/153/197 P | 3/0/0 147/153/197 P | 3/0/0 147/153/197 P | 3/0/0 147/153/197 P | 3/0/0 147/153/197 P |
| M2_quiet_music_ringout_two_modes | 6 | 300 | 6/0/0 -0/48/52 P | 6/0/0 -0/47/52 P | 6/0/0 -0/47/52 P | 6/0/0 -0/47/52 P | 6/0/0 -0/47/52 P | 6/0/0 -0/47/52 P |
| M3_jazz_trio_lav_ring_400Hz | 3 | 300 | 3/0/0 198/302/550 F1 | 3/0/0 150/198/550 F2 | 3/0/0 150/198/550 F2 | 3/0/0 150/198/550 F2 | 3/0/0 150/198/550 F2 | 3/0/0 150/198/550 F2 |
| X1_organ_held_notes | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| X2_flute_held_vibrato | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| X3_whistle_held_drift | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| X4_sine_lead_portamento | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| X5_808_bassline_40_60Hz | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| X6_soprano_closed_vowel_band_edge | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| X7_plateaued_ring_under_music_from_t0 | 3 | 1000 | 3/0/0 801/802/853 P | 3/0/0 801/802/853 P | 3/0/0 801/802/853 P | 3/0/0 801/802/853 P | 0/3/0 F0 | 0/3/0 F0 |
| X8_slow_ring_midband_under_chords | 3 | 1000 | 3/0/0 1850/3098/4252 F0 | 3/0/0 1449/2502/3901 F0 | 3/0/0 1449/2502/3901 F0 | 3/0/0 1449/2502/3901 F0 | 3/0/0 1449/2502/3901 F0 | 3/0/0 1449/2502/3901 F0 |
| X9_ring_rta_midpoint_525Hz_speech | 3 | 300 | 3/0/0 1053/1402/1549 F0 | 3/0/0 1000/1448/1449 F0 | 3/0/0 1000/1448/1449 F0 | 3/0/0 1000/1448/1449 F0 | 3/0/0 1000/1448/1449 F0 | 3/0/0 1000/1448/1449 F0 |
| X10_two_rings_exact_octave | 6 | 300 | 6/0/0 99/148/150 P | 6/0/0 48/75/148 P | 6/0/0 48/75/148 P | 6/0/0 48/75/148 P | 6/0/0 48/75/148 P | 6/0/0 48/75/148 P |
| X11_amp_clipped_howl_minus12dBFS | 3 | 300 | 0/3/0 F0 | 3/0/0 151/200/203 P | 3/0/0 151/200/203 P | 3/0/0 151/200/203 P | 3/0/0 151/200/203 P | 3/0/0 151/200/203 P |
| X12a_master_drop20_raise_channel | 6 | 300 | 6/0/0 199/225/252 P | 6/0/0 51/174/202 P | 6/0/0 51/174/202 P | 6/0/0 51/174/202 P | 6/0/0 51/174/202 P | 6/0/0 51/174/202 P |
| X12b_master_drop20_raise_busmaster | 6 | 300 | 6/0/0 199/201/249 P | 6/0/0 -0/174/202 P | 6/0/0 -0/174/202 P | 6/0/0 -0/174/202 P | 6/0/0 -0/174/202 P | 6/0/0 -0/174/202 P |
| X13_decay16_jazz_lav_ring | 3 | 600 | 3/0/0 98/250/350 P | 3/0/0 298/352/499 P | 3/0/0 298/352/499 P | 3/0/0 298/352/499 P | 3/0/0 298/352/499 P | 3/0/0 298/352/499 P |
| X14_peakhold_loud_band_wedge_ring | 3 | 300 | 3/0/0 248/250/298 P | 3/0/0 147/147/202 P | 3/0/0 147/147/202 P | 3/0/0 147/147/202 P | 3/0/0 147/147/202 P | 3/0/0 147/147/202 P |
| X15_kick_bass_unison_55Hz | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| X16_wedge_ring_315Hz_loud_band | 3 | 300 | 3/0/0 201/203/203 P | 0/3/0 F0 | 2/1/0 201/202/203 F2 | 2/1/0 201/202/203 F2 | 2/1/0 201/202/203 F2 | 2/1/0 201/202/203 F2 |
| X17_ring_122Hz_acoustic_guitar_body | 3 | 600 | 0/3/0 F0 | 3/0/0 148/252/450 P | 0/3/0 F0 | 3/0/0 148/252/450 P | 3/0/0 148/252/450 P | 3/0/0 148/252/450 P |
| X18_applause_crowd_30s | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| X19_handheld_ring_stalls_and_hops | 3 | 300 | 3/0/0 200/201/348 F2 | 3/0/0 200/201/348 F2 | 3/0/0 200/201/348 F2 | 3/0/0 200/201/348 F2 | 3/0/0 200/201/348 F2 | 3/0/0 200/201/348 F2 |
| X20_mains_hum_and_hvac_whine | 0 | 300 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P |
| X21_reverberant_area_mic_slow_ring | 3 | 600 | 2/1/0 399/474/550 F2 | 2/1/0 399/474/550 F2 | 2/1/0 399/474/550 F2 | 2/1/0 399/474/550 F2 | 2/1/0 399/474/550 F2 | 2/1/0 399/474/550 F2 |
| X22_kick_mic_sub_ring_65Hz | 3 | 1000 | 0/3/0 F0 | 3/0/0 497/552/751 P | 0/3/0 F0 | 3/0/0 497/552/751 P | 3/0/0 497/552/751 P | 3/0/0 497/552/751 P |
| X23_ringout_quiet_room_two_modes | 6 | 300 | 6/0/0 e2 -1252/102/150 P | 6/0/0 e2 -1299/77/150 P | 6/0/0 e2 -1299/77/150 P | 6/0/0 e2 -1299/77/150 P | 6/0/0 e3 -1299/77/150 P | 6/0/0 e3 -1299/77/150 P |

#### Main corpus totals (all runs)

| run | scen pass | events | TP | miss | FP | EARLY | TAIL | HARM | cuts | alive | lat p50 / p90 / max ms | ≤ 300 ms | missed in |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| winner watch open (2fc52ee) | 48/61 | 117 | 105 | 12 | **0** | 2 | 0 | 0 | 0 | – | 202 / 702 / 4252 | 77/105 | S13, X11, X17, X21, X22 |
| winner ringout open | 50/61 | 117 | 111 | 6 | **0** | 2 | 0 | 0 | 0 | – | 203 / 702 / 4252 | 79/111 | X11, X21, S13 |
| 8094eed watch_tag open (before this round) | 51/61 | 117 | 113 | 4 | **0** | 2 | 0 | 0 | 0 | – | 200.3 / 750.8 / 3900.9 | 81/113 | X16, X21 |
| 8094eed ringout_tag open | 50/61 | 117 | 110 | 7 | **0** | 3 | 0 | 0 | 0 | – | 199.7 / 552.9 / 3900.9 | 81/110 | X16, X21, X7 |
| watch open | 49/61 | 117 | 109 | 8 | **0** | 2 | 0 | 0 | 0 | 0 | 199.8 / 751.7 / 3900.9 | 81/109 | X16, X17, X21, X22 |
| watch closed | 18/32 | 116 | 108 | 8 | **0** | 2 | 1 | 0 | 114 | 0 | 200.3 / 800.8 / 3900.9 | 77/108 | X16, X17, X21, X22 |
| watch_tag open | 51/61 | 117 | 115 | 2 | **0** | 2 | 0 | 0 | 0 | 0 | 200.3 / 750.8 / 3900.9 | 83/115 | X16, X21 |
| watch_tag closed | 20/32 | 116 | 114 | 2 | **0** | 2 | 1 | 0 | 120 | 0 | 201.1 / 751.7 / 3900.9 | 79/114 | X16, X21 |
| ringout open | 50/61 | 117 | 112 | 5 | **0** | 3 | 0 | 0 | 0 | 0 | 199.8 / 552.9 / 3900.9 | 83/112 | X16, X21, X7 |
| ringout closed | 19/32 | 116 | 111 | 5 | **0** | 2 | 1 | 0 | 117 | 0 | 200.9 / 702.0 / 3900.9 | 79/111 | X16, X21, X7 |
| ringout_tag open | 50/61 | 117 | 112 | 5 | **0** | 3 | 0 | 0 | 0 | 0 | 199.8 / 552.9 / 3900.9 | 83/112 | X16, X21, X7 |
| ringout_tag closed | 19/32 | 116 | 111 | 5 | **0** | 2 | 1 | 0 | 117 | 0 | 200.9 / 702.0 / 3900.9 | 79/111 | X16, X21, X7 |
| watch_lf open (LF window 40 Hz everywhere) | 51/61 | 117 | 115 | 2 | **0** | 2 | 0 | 0 | 0 | 0 | 200.3 / 750.8 / 3900.9 | 83/115 | X16, X21 |

### 6.2 Hold-out seeds 4-9 and never-run seeds 10-15, open loop

| run | scen pass | events | TP | miss | FP | EARLY | lat p50 / p90 / max | ≤ 300 | FP (scenario, seed, Hz, reasons) | missed in (scenario × seeds) |
|---|---|---|---|---|---|---|---|---|---|---|
| winner watch 4-9 (audit) | 45+45/61 | 234 | 212 | 22 | **2** | | max 6049 | | S21 s5 160 Hz; X17 s? 250 Hz | S13, X8 (6 s), X11, X17, X21, X22 |
| 8094eed watch_tag 4-9 | 44/61 | 234 | 223 | 11 | **1** | 10 | 199.6 / 948.3 / 4700.0 | 155/223 | X21 s6 1111 Hz fastrise17dB@123dB/s | X11, X14, X16, X21 |
| 8094eed watch_tag 10-15 (verifier) | – | 234 | 224 | 10 | **4** | | max 1252 (X11) | | X1 s11 est_at_arm; X21 s11+s13 fastrise; X21 s13 rise6dB | X11 1/6, X14 3/6, X16 5/6, X21 1/6 |
| watch 4-9 | 45/61 | 234 | 220 | 14 | **0** | 10 | 199.2 / 802.7 / 4700.0 | 161/220 | – | X11×1, X17×6, X21×1, X22×6 |
| watch_tag 4-9 | 45/61 | 234 | 232 | 2 | **0** | 10 | 199.6 / 947.1 / 4700.0 | 163/232 | – | X11×1, X21×1 |
| ringout_tag 4-9 (info: watch scenes forced to ring_out) | 44/61 | 234 | 226 | 8 | **2** | 10 | 199.5 / 947.1 / 4700.0 | 163/226 | S21 s6 130 Hz rise6dB; S21 s7 129 Hz rise7dB | X7×6, X11×1, X21×1 |
| watch 10-15 | 43/61 | 234 | 220 | 14 | **1** | 10 | 200.5 / 751.4 / 3252.5 | 157/220 | X21 s13 1129 Hz rise6dB | X11×1, X17×6, X21×1, X22×6 |
| watch_tag 10-15 | 43/61 | 234 | 232 | 2 | **1** | 10 | 201.6 / 801.8 / 3252.5 | 157/232 | X21 s13 1129 Hz rise6dB | X11×1, X21×1 |
| ringout_tag 10-15 (info) | 41/61 | 234 | 226 | 8 | **2** | 10 | 200.9 / 751.4 / 3252.5 | 157/226 | S21 s14 130 Hz rise6dB; X21 s13 1129 Hz rise6dB | X7×6, X11×1, X21×1 |

### 6.3 Analyser / prefs sweeps (main corpus, seeds 1-3, open loop; CORPUS §5)

| sweep | winner FP (audit) | 8094eed FP watch/watch_tag | mode | scen pass | TP | miss | FP | TAIL | HARM | FP in (scenario: count) | missed in |
|---|---|---|---|---|---|---|---|---|---|---|---|
| attack_k1 | 0 | 0/0 | watch | 49/61 | 110 | 7 | **0** | 0 | 0 | – | X17, X21, X22 |
| attack_k1 | 0 | 0/0 | watch_tag | 50/61 | 116 | 1 | **0** | 0 | 0 | – | X21 |
| bq | 0 | 0/0 | watch | 49/61 | 109 | 8 | **0** | 0 | 0 | – | X16, X17, X21, X22 |
| bq | 0 | 0/0 | watch_tag | 50/61 | 115 | 2 | **0** | 0 | 0 | – | X16, X21 |
| skirt2 | 0 | 0/0 | watch | 46/61 | 108 | 9 | **0** | 0 | 0 | – | X16, X17, X21, X22 |
| skirt2 | 0 | 0/0 | watch_tag | 47/61 | 114 | 3 | **0** | 0 | 0 | – | X16, X21 |
| skirt5 | 1 (S16) | 0/0 | watch | 48/61 | 109 | 8 | **0** | 0 | 0 | – | X16, X17, X21, X22 |
| skirt5 | 1 (S16) | 0/0 | watch_tag | 50/61 | 115 | 2 | **0** | 0 | 0 | – | X16, X21 |
| decay4 | 0 | 0/0 | watch | 45/61 | 104 | 13 | **0** | 0 | 0 | – | S13, X16, X17, X21, X22 |
| decay4 | 0 | 0/0 | watch_tag | 47/61 | 110 | 7 | **0** | 0 | 0 | – | S13, X16, X21 |
| rel17 | 0 (+21 TAIL) | 0/0 (+9 TAIL) | watch | 45/61 | 104 | 13 | **0** | 0 | 0 | – | S13, X16, X17, X21, X22 |
| rel17 | 0 (+21 TAIL) | 0/0 (+9 TAIL) | watch_tag | 47/61 | 110 | 7 | **0** | 0 | 0 | – | S13, X16, X21 |
| rms | 0 | 0/0 | watch | 50/61 | 108 | 9 | **0** | 0 | 0 | – | X11, X17, X21, X22 |
| rms | 0 | 0/0 | watch_tag | 52/61 | 114 | 3 | **0** | 0 | 0 | – | X11, X21 |
| noise15 | 0 | 0/0 | watch | 46/61 | 108 | 9 | **0** | 0 | 0 | – | X16, X17, X21, X22 |
| noise15 | 0 | 0/0 | watch_tag | 47/61 | 114 | 3 | **0** | 0 | 0 | – | X16, X21 |
| peakhold1 | 4 (S22, X7, X9) + X7 miss | 0/0 | watch | 38/61 | 95 | 22 | **0** | 0 | 0 | – | S13, X11, X12a, X12b, X16, X17, X21, X22, X7 |
| peakhold1 | 4 (S22, X7, X9) + X7 miss | 0/0 | watch_tag | 40/61 | 101 | 16 | **0** | 0 | 0 | – | S13, X11, X12a, X12b, X16, X21, X7 |
| gain12 | 53 | 15/15 | watch | 42/61 | 98 | 19 | **18** | 3 | 0 | S8c: 1, S20: 3, X3: 5, X18: 9 | M1, S2c, X14, X16, X17, X21, X22 |
| gain12 | 53 | 15/15 | watch_tag | 44/61 | 104 | 13 | **18** | 3 | 0 | S8c: 1, S20: 3, X3: 5, X18: 9 | M1, S2c, X14, X16, X21 |
| gain24 | 310 | 10/10 +3 HARM | watch | 42/61 | 89 | 25 | **25** | 0 | 3 | X2: 9, X6: 8, X18: 8 | M1, S14, S2c, S3, X11, X14, X17, X21, X22, X9 |
| gain24 | 310 | 10/10 +3 HARM | watch_tag | 44/61 | 95 | 19 | **25** | 0 | 3 | X2: 9, X6: 8, X18: 8 | M1, S14, S2c, S3, X11, X14, X21, X9 |

### 6.4 Adversarial corpus (the five auditors' 55 breakers), seeds 1-3, open loop

Cell as in §1. `winner` = wt/disc-predicates on the same scenes (watch_tag). `MODERATE ms` = first frame a MODERATE-or-better candidate within ±1 band of the ring was published (per seed; tier-B hook; fields klass/level/prominence/excess/age/reasons/freq_hz present = ✓).

| scenario | ev | winner watch_tag | 8094eed watch_tag | watch_tag | ringout_tag | closed watch_tag: cuts / deepest | MODERATE ms per seed | verdict |
|---|---|---|---|---|---|---|---|---|
| AP01_flute_crescendo_no_family | 0 | 0/0/18 F0 | 0/0/9 F0 | 0/0/9 F0 | 0/0/9 F0 | 6 cuts, min -6 dB | – | irreducible swell (flute crescendo 4.5 dB/s): cut on rise6dB; the continuing crescendo re-arms one deepen per +3 dB (closed loop −6); FALSE_CUT when the note ends |
| AP02_organ_note_at_arm_recurs | 0 | 0/0/15 F0 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0 | – | FIXED (F1): a note recurring at the at-arm pitch is a new source — 0 FP |
| AP03_rta_gain_offset_18_organ_held | 0 | 0/0/21 F0 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0 | – | FIXED (G6): display gain +18 puts the organ at −9 dBFS: the display is HOT and the organ is not above the arm-window maximum → not LOUD, 0 FP |
| AP04_rta_gain_offset_clip_organ_chord | 0 | 0/0/54 F0 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0 | – | FIXED (G6/HOT): programme clipping the display across many bands → HOT_SPECTRUM, clip flag ignored — 0 FP |
| AP05_ring_onset_during_pad_swell | 3 | 3/0/0 52/100/149 P | 3/0/0 -2/52/149 P | 3/0/0 -2/52/149 P | 3/0/0 -2/52/149 P | 3 cuts, min -3 dB | -2 / 149 / 52 ✓ | pass: ring during a pad swell caught at 52-149 ms |
| AP06_three_rings_after_master_shove | 9 | 9/0/0 3/152/2000 F0 | 9/0/0 3/150/250 P | 9/0/0 3/150/250 P | 9/0/0 3/150/250 P | 9 cuts, min -3 dB | 98,49,2 / 101,150,53 / 3,152,103 ✓ | FIXED (F4): three rings after one shove, all ≤ 250 ms (co-growth needs co-born mates at a matched rate; two rings allowed) |
| AP07_very_slow_ring_2dB_s | 3 | 0/3/0 F0 | 3/0/0 851/1199/1551 F1 | 3/0/0 851/1199/1551 F1 | 3/0/0 851/1199/1551 F1 | 3 cuts, min -3 dB | 449 / 452 / 648 ✓ | FIXED (F3): 2 dB/s creep now reachable 3/3 (851-1551 ms: 6/R after visibility; budget 1 s met on 1 seed) |
| AP08_established_ring_16dB_prominent_at_arm | 3 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 0 | – / – / – ✓ | irreducible at-arm (16-17 dB prominent, mostly < 12 in the mix): never even MODERATE; tier-B cannot see it either |
| AP09_projector_whine_minus36_at_arm | 0 | 0/0/33 F0 | 0/0/3 F0 | 0/0/3 F0 | 0/0/0 P | 3 cuts, min -3 dB | – | irreducible at-arm pair in WATCH (whine −36 from before arm = X7's observation): one cut at 0.85 s, no deepen; in ring_out the probe clears it (AF05) |
| AP10_ring_at_song_start | 3 | 3/0/0 101/149/151 P | 3/0/0 101/149/151 P | 3/0/0 101/149/151 P | 3/0/0 101/149/151 P | 3 cuts, min -3 dB | 101 / 151 / 149 ✓ | pass: ring at song start 101-151 ms |
| AF01_flute_crescendo_messa_di_voce | 0 | 0/0/24 F0 | 0/0/12 F0 | 0/0/12 F0 | 0/0/12 F0 | 12 cuts, min -9 dB | – | irreducible swell (flute messa di voce): cut on the rise, deepened while the swell continues (+3 dB per step: closed loop −9 on half the note-bands), released as false_cut when it ends |
| AF02_sine_pad_two_line_swell | 0 | 0/0/58 F0 | 0/0/24 F0 | 0/0/24 F0 | 0/0/24 F0 | 17 cuts, min -9 dB | – | irreducible 2-line swell (sine pad fifth): cut per line per chord on the rise, deepened while the swell continues (closed loop down to −9) |
| AF03_mic_cupped_wedge_howl_plateau_m13 | 3 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 0 | 197 / 200 / 200 ✓ | tier-B: 800 dB/s howl to −13 dBFS = 1 visible increment; MODERATE published at 197-200 ms with fields; not cut by the detector |
| AF04_established_lf_ring_122Hz_at_arm | 3 | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P | 3 cuts, min -3 dB | 199 / 200 / 202 ✓ | pass (watch_tag): established LF ring at −15 caught at K1 via AT-ARM (LF window declared); plain watch: below window |
| AF05_ringout_projector_whine_minus36 | 0 | 0/0/39 F0 | 0/0/9 F0 | 0/0/9 F0 | 0/0/0 P | 0 | – | FIXED (G4): ring_out whine follows the +1 dB steps 1 dB/dB → STATIONARY, 0 FP (watch mode without steps: 1 cut at 0.85 s) |
| AF06_ring_onset_during_pad_swell | 3 | 3/0/0 98/102/1950 F2 | 3/0/0 98/102/150 P | 3/0/0 98/102/150 P | 3/0/0 98/102/150 P | 3 cuts, min -3 dB | 50 / 52 / 98 ✓ | pass: ring during pad swell 98-150 ms |
| AF07_kick_sub_howl_fast_52Hz | 3 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 0 | 198 / 300 / 199 ✓ | irreducible LF (52 Hz, 100 dB/s, plateau after 1.2 settle times): MODERATE at 198-300 ms (−9 dBFS: tier-B loud-ish) |
| AF08_loud_crowd_whistle_minus7dBFS | 0 | 0/0/13 F0 | 0/0/13 F0 | 0/0/13 F0 | 0/0/13 F0 | 9 cuts, min -9 dB | – | irreducible loud whistle (−7 dBFS, 35 dB prominent, ±35 c vibrato invisible to the centroid): LOUD → cut at K1; open loop re-emitted each cooldown (line never comes down), closed loop see §4b |
| AF09_channel_fader_ride_on_held_organ_note | 0 | 0/0/13 F0 | 0/0/3 F0 | 0/0/1 F2 | 0/0/1 F2 | 1 cuts, min -3 dB | – | irreducible single-channel fader ride (+9 dB/1 s on a family-less organ note): 3 → 1 FP (one seed), one −3 dB cut, no deepen |
| AF10_established_ring_at_arm_minus44 | 3 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 0 | 199 / 200 / 202 ✓ | irreducible at-arm below −40 dBFS (ring at −44): MODERATE only (published at 200 ms) |
| AF11_ringout_steps_over_leslie_organ | 0 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0 | – | pass: Leslie organ under ring_out steps, 0 FP |
| AF12_soprano_closed_vowel_crescendo | 0 | 0/0/19 F0 | 0/0/12 F0 | 0/0/12 F0 | 0/0/12 F0 | 12 cuts, min -9 dB | – | irreducible swell (soprano closed vowel crescendo): cut per note on the rise, deepened while the crescendo continues (closed loop −9 on half the bands) |
| AF13_organ_note_held_at_arm_minus30 | 0 | 0/0/8 F0 | 0/0/3 F0 | 0/0/3 F0 | 0/0/0 P | 3 cuts, min -3 dB | – | irreducible at-arm pair (organ note held at −30 = X7): one cut at 0.85 s, no deepen |
| AF14_lf_howl_to_desk_clip_100Hz | 3 | 0/3/0 F0 | 3/0/0 402/402/450 P | 3/0/0 402/402/450 P | 3/0/0 402/402/450 P | 3 cuts, min -3 dB | -2 / 402 / 402 ✓ | FIXED: LF howl pinned at the desk clip flag inside the declared LF window → LOUD, 3/3 at 402-450 ms |
| AM01_fast_howl_limiter_plateau_minus14 | 3 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 0 | 251 / 248 / 251 ✓ | tier-B: 417 dB/s → −14 dBFS limiter, ≤ 2 increments; MODERATE at 248-251 ms; not cut |
| AM02_sine_lead_slow_portamento | 0 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0 | – | pass: slow-portamento sine lead 0 FP (drift re-birth) |
| AM03_loud_whistle_near_full_scale | 0 | 0/0/20 F0 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0 | – | 0 FP kept: whistles at −4..−2 dBFS in a loud (HOT) show do not clear the arm-window maximum (−5) + 3 dB → MODERATE only; a whistle 3 dB above anything the show reached would be LOUD (§7.3) |
| AM04_projector_whine_minus40_before_arm | 0 | 0/0/21 F1 | 0/0/2 F1 | 0/0/2 F1 | 0/0/0 P | 2 cuts, min -3 dB | – | irreducible at-arm pair in watch (whine −40): one cut (2 of 3 seeds), no deepen (below the loud-ish line) |
| AM05_flute_crescendo_straight_tone | 0 | 0/0/18 F0 | 0/0/10 F0 | 0/0/10 F0 | 0/0/10 F0 | 9 cuts, min -6 dB | – | irreducible swell (flute 55-71 dB/s crescendo from niente = FAST-RISE + rise): cut per swell, −6 where the crescendo continues 3 dB past the cut |
| AM06_organ_swell_pedal_chord | 0 | 0/0/0 P | 0/0/2 F2 | 0/0/2 F2 | 0/0/2 F2 | 1 cuts, min -3 dB | – | 2-line-equivalent swell: organ 3-note swell where one note sits in a bed hump → 1 mate only; seed 2: 4 → 1-2 FP; irreducible class |
| AM07_quiet_plateaued_howl_minus48_from_t0 | 3 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 0 | 199 / 200 / 202 ✓ | irreducible at-arm below −40 (howl at −48): MODERATE only |
| AM08_plateaued_ring_14dB_at_arm | 3 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 0 | 199 / 200 / 202 ✓ | irreducible at-arm (14 dB prominent): MODERATE only at 200 ms |
| AM09_ring_onset_inside_cymbal_wash | 3 | 0/3/0 F0 | 1/2/0 -2/-2/-2 F1 | 1/2/0 -2/-2/-2 F1 | 1/2/0 -2/-2/-2 F1 | 1 cuts, min -3 dB | 147 / 150 / -2 ✓ | tier-B: ring plateauing INSIDE a cymbal wash (growth masked): MODERATE at 147-202 ms; 1/3 caught by rise as the wash decays |
| AM10_organ_chord_sounding_at_arm | 0 | 0/0/10 F1 | 0/0/2 F1 | 0/0/2 F1 | 0/0/0 P | 2 cuts, min -3 dB | – | at-arm dyad-equivalent (organ chord at −30, two tones qualify): one cut each (cohort rule needs ≥ 3) |
| AS01_loud_sine_lead_minus12dBFS | 0 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0 | – | pass: loud sine lead −12 dBFS solo, 0 FP |
| AS02_cupped_mic_howl_compressor_minus22 | 3 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 0 | 197 / 401 / 200 ✓ | tier-B: 1200 dB/s → −22 compressor plateau in one frame; MODERATE at 197/400/200 ms (s2: a vocal note held it as 'isH3' for 0.3 s until G5 freed it) |
| AS03_amp_clipped_howl_minus24dBFS | 3 | 0/3/0 F0 | 3/0/0 151/152/200 P | 3/0/0 151/152/200 P | 3/0/0 151/152/200 P | 3 cuts, min -3 dB | 152 / 200 / 151 ✓ | FIXED (G1): amp-clipped howl at −24 dBFS with harmonics fading in: FAST-RISE 3/3 at 151-200 ms |
| AS04_plateaued_ring_at_H7_slot_of_loud_organ | 3 | 3/0/0 801/802/853 P | 3/0/0 801/802/853 P | 3/0/0 801/802/853 P | 0/3/0 F0 | 3 cuts, min -3 dB | 199 / 200 / 202 ✓ | pass (watch): established ring −26 next to a loud organ's H7 slot: AT-ARM at 800 ms (< arm_fast_level); forced ring_out mode waits for steps the scene never makes (contract: PROGRAMME_PRESENT) |
| AS05_organ_dyad_swell_pedal | 0 | 0/0/63 F0 | 0/0/18 F0 | 0/0/18 F0 | 0/0/18 F0 | 13 cuts, min -9 dB | – | irreducible 2-line swell (organ dyad with swell pedal): cut per note per swell, deepened while the pedal keeps opening (closed loop to −9) |
| AS06_ring_under_sixteenth_hats | 3 | 3/0/0 148/150/203 P | 3/0/0 148/150/203 P | 3/0/0 148/150/203 P | 3/0/0 148/150/203 P | 3 cuts, min -3 dB | 98 / 149 / 101 ✓ | pass: ring under 16th-note hats 148-203 ms |
| AS07_lectern_ring_140Hz_speech_no_optin | 3 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 3/0/0 401/752/1100 F1 | 0 | – / – / – ✓ | window policy: lectern ring at 140 Hz without LF declaration is below the 160 Hz watch edge (never MODERATE); cfs's HPF-derived lf_edge_hz (0.7 x 100-150 Hz) puts it in window |
| AS08_edge_ring_busmaster_drop20 | 6 | 2/4/0 448/450/452 F0 | 6/0/0 199/277/352 F0 | 6/0/0 199/277/352 F0 | 6/0/0 199/277/352 F0 | 3 cuts, min -3 dB | 199,352 / 200,352 / 202,352 ✓ | FIXED (harmonic level plausibility): established edge ring 18 dB above the organ note whose H3 slot it sits near: 3/3 at 199 ms; regrowth episode 276-352 ms |
| AS09_two_rings_octave_co_onset | 6 | 5/1/0 150/352/447 F0 | 5/1/0 52/352/447 F0 | 5/1/0 52/352/447 F0 | 5/1/0 52/352/447 F0 | 5 cuts, min -3 dB | 447,398 / 352,1 / 398,52 ✓ | co-onset octave rings at different rates: 5/6 (150-447 ms); one lower ring missed on one seed while judged the other's H1 pair |
| AS10_theremin_style_single_sine_swell | 0 | 0/0/24 F0 | 0/0/17 F0 | 0/0/17 F0 | 0/0/17 F0 | 12 cuts, min -9 dB | – | irreducible swell (theremin-style sine fade-in 30 dB/s; the corpus marks it irreducible_passive): cut per swell, deepened while it keeps rising (to −9) |
| AT01_solo_sine_pad_swell | 0 | 0/0/24 F0 | 0/0/24 F0 | 0/0/24 F0 | 0/0/24 F0 | 24 cuts, min -9 dB | – | irreducible swell (solo sine pad 25 dB/1 s fade-in): cut per note; a 25 dB fade-in re-arms deepening every +3 dB (closed loop −9 on 6 of 9 note-bands) |
| AT02_fast_wedge_ring_600dBps_plateau_m15 | 3 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 0 | 199 / 250 / 250 ✓ | tier-B: 600 dB/s → −15 dBFS; MODERATE at 199-250 ms; not cut |
| AT03_two_rings_octave_same_step | 6 | 4/2/0 650/701/752 F0 | 4/2/0 650/701/752 F0 | 4/2/0 650/701/752 F0 | 4/2/0 650/701/752 F0 | 4 cuts, min -3 dB | –,– / 652,752 / 650,750 ✓ | co-onset exact-octave rings with EQUAL excess (same rate): an 8'+4' registration until the plateaux diverge — 4/6 at 650-752 ms, 2 missed (irreducible at onset) |
| AT04_lectern_ring_300Hz_over_mains_hum | 3 | 3/0/0 350/548/551 F0 | 3/0/0 350/548/551 F0 | 3/0/0 350/548/551 F0 | 3/0/0 350/548/551 F0 | 3 cuts, min -3 dB | 152 / 250 / 302 ✓ | lectern ring at 300 Hz = 6x50 = 3x100 Hz over mains hum: 3/3 but 249-502 ms (hum family coincidences cost frames; budget 300) |
| AT05_ring_grows_out_of_held_organ_note | 3 | 3/0/0 500/503/552 F0 | 3/0/0 500/503/552 F0 | 3/0/0 500/503/552 F0 | 3/0/0 500/503/552 F0 | 3 cuts, min -3 dB | 49 / 0 / 51 ✓ | ring growing out of a held organ note 8 c away: 3/3 at 452-552 ms (the note's cluster masks the first 6 dB; budget 300) |
| AT06_fast_ring_plateaus_inside_cymbal_wash | 3 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0 F0 | 0 | 252 / 101 / 150 ✓ | tier-B: fast ring plateaus inside a cymbal wash: MODERATE at 101-252 ms; not cut |
| AT07_watch_fader_push_into_feedback | 3 | 3/0/0 0/47/151 P | 3/0/0 0/47/151 P | 3/0/0 0/47/151 P | 3/0/0 0/47/151 P | 6 cuts, min -6 dB | 47 / 151 / 0 ✓ | pass: watch fader push into feedback 0-151 ms |
| AT08_organ_note_sounding_at_arm | 0 | 0/0/10 F1 | 0/0/2 F1 | 0/0/2 F1 | 0/0/0 P | 2 cuts, min -3 dB | – | irreducible at-arm pair (organ note −32 at arm): one cut at 0.85 s (2 of 3 seeds) |
| AT09_808_bassline_lf_optin | 0 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0 | – | FIXED-by-design: 808 bassline with the LF window OPEN (40 Hz): 0 FP (settle rule, not the window) |
| AT10_sustained_sine_sub_lf_optin | 0 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0 | – | FIXED-by-design: sustained sine sub 41-44 Hz with LF window open: 0 FP (track-and-group: 21 FP) |
| AT11_driven_room_mode_ringout_lf_optin | 0 | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0/0/0 P | 0 | – | FIXED-by-design: driven room mode under ring_out steps with LF window open: STATIONARY (1 dB/dB), 0 FP (t&g: 10 FP) |
| **totals** | 99 | winner: TP 47 miss 52 FP 505 (13/55 pass) | 8094eed: TP 61 miss 38 FP 165 (20/55) | TP 61 miss 38 FP **163** (20/55 pass) | TP 61 miss 38 FP **142** (25/55) | 189 cuts | | |

#### Adversarial FP by class (watch_tag)

* swell (irreducible §7.1): **129** — AP01 9, AF01 12, AF02 24, AF09 1, AF12 12, AM05 10, AM06 2, AS05 18, AS10 17, AT01 24
* at-arm pair in watch (§7.4): **21** — AP09 3, AF05 9, AF13 3, AM04 2, AM10 2, AT08 2
* loud whistle (§7.3): **13** — AF08 13
* display gain offset (§7.10): **0** — 
* anything else: **0**

### 6.4a Verifier breakers AV01-AV13 (seeds 1-3): open loop, and closed loop for the feedback scenes

Cell as in §1; closed = TP/miss/FP, cuts, rings ALIVE at the end (must be 0). `MODERATE ms` as in §4.

| scenario | ev | watch_tag open | ringout_tag open | closed (watch_tag; ringout_tag for AV08/09/11): cuts / deepest / alive | MODERATE ms per seed | verdict |
|---|---|---|---|---|---|---|
| AV01_voice_chord_onsets_strummed | 0 | 0/0/0 P | 0/0/0 P | – | – | HELD: strummed 4-voice chord onsets whose partials coincide in one band (the G1 chord-onset case): 0 FP seeds 1-3 (was 0; seed 6 of 4-15 had 1 FP, now 0 — B2) |
| AV02_flute_soft_attacks_120ms | 0 | 0/0/1 F2 | 0/0/1 F2 | – | – | B2 FIXED for the 100-150 ms attacks: 8 → 1 FP on seeds 1-3, 43 → 10 on 15 seeds; the residue are ≥ 150 ms breath attacks with three OBSERVED increments = the judge-literal G1 boundary (§7.9): cut once, 'held' without deepen, FALSE_CUT on note end |
| AV03_organ_notes_start_within_1s_of_arm | 0 | 0/0/0 P | 0/0/0 P | – | – | B1 FIXED: re-struck / fresh organ notes within 1 s of arm no longer inherit 'established at arm': 5 FP / 9 seeds → 0 |
| AV04_soprano_ff_climax_note_minus6 | 0 | 0/0/15 F0 | 0/0/15 F0 | – | – | irreducible-by-spec (L §4.3 tier A): soprano ff closed vowel at −6 dBFS with the band tacet, vibrato only after 300 ms → LOUD at K1; open loop re-emitted each cooldown (the line never comes down); closed loop: held → −6/−9 while she holds it (§4b) |
| AV05_howl_e4_limiter_on_geq_centre_watch | 3 | 3/0/0 147/151/198 P | 3/0/0 147/151/198 P | 3/0/0, 6 cuts, min -6 dB, alive 0 | 198 / 151 / 147 ✓ | B3 FIXED: 180 dB/s howl to a −16 limiter plateau on a GEQ centre, e=+4: cut at 147-198 ms, 'held' at +1.5 s → deepen_held → −6 dB → confirmed, ring DEAD 3/3 (was: false_cut, alive 3/3) |
| AV06_howl_geq_midpoint_e2p5_limiter_watch | 3 | 3/0/0 101/148/151 P | 3/0/0 101/148/151 P | 3/0/0, 6 cuts, min -6 dB, alive 0 | 148 / 101 / 151 ✓ | HELD: same on the GEQ midpoint (bell 1.7-2.6 dB at the line): first cut lands mid-climb ('insufficient'), regrowth → −6 kills it 3/3 |
| AV07_marginal_ring_e2p85_reverberant_cut | 3 | 3/0/0 98/99/101 P | 3/0/0 98/99/101 P | 3/0/0, 3 cuts, min -3 dB, alive 0 | 50 / 98 / 49 ✓ | HELD: marginal reverberant ring (e 2.85) that −3 dB only just kills: one cut, verdict insufficient/ambiguous (never false_cut), not alive; the bystander 'confirmed 0.0 dB' entry is gone (N4) |
| AV08_ringout_arm_while_howling_minus28_limiter | 3 | – | 3/0/0 3998/4001/4050 F0 | 3/0/0, 3 cuts, min -3 dB, alive 3 | 199 / 200 / 202 ✓ | by design (G4, N1 documented): ring_out armed into a −28 dBFS limiter-held howl, PINNED under +1 dB steps: MODERATE at 200 ms, cut after 2 judged steps (4.0 s); closed loop 'held' → AT-ARM is plateau-class but −28 < loud-ish → no detector deepen (cfs ring_out VERIFY deepens); backoff probe advised for cfs |
| AV09_ringout_arm_while_howling_2to1_compressor | 3 | – | 3/0/0 9503/9552/9598 F0 | 3/0/0, 3 cuts, min -3 dB, alive 3 | 199 / 200 / 202 ✓ | by design (N1): 2:1-compressor-held howl FOLLOWS +1 dB steps exactly 1 dB/dB = a whine to any +1 dB probe → STATIONARY + 'backoff_advised'; cut only via rise after +6 dB of steps (9.5 s). Needs the back-off probe (cfs) |
| AV10_organ_pedal_note_plus_octave_melody | 0 | 0/0/0 P | 0/0/0 P | – | – | HELD: organ pedal note + melody landing on its exact H2/H3/H4 slots (G5): 0 FP |
| AV11_quiet_stage_hvac_intermittent_talker_ringout | 0 | – | 0/0/0 P | – | – | N3 FIXED: PROGRAMME_PRESENT under ring_out steps tracks the talker and no longer latches on step-surfaced hum/HVAC lines; 0 FP, whine/hum STATIONARY |
| AV12_fast_howl_350dBps_compressor_m19_lectern | 3 | 0/3/0 F0 | 0/3/0 F0 | 0/3/0, 0 cuts, min 0 dB, alive 0 | 248 / 249 / 247 ✓ | HELD (tier B): 350 dB/s howl to a −19 compressor plateau during speech: MODERATE at 247-249 ms with all fields (freq 1901 Hz for a 1934 Hz tone: 0.02 oct); not cut by the detector (cfs tier-B, not in this branch) |
| AV13_ultra_steady_limiter_howl_at_arm_watch | 3 | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P | 3/0/0, 3 cuts, min -3 dB, alive 0 | 199 / 200 / 202 ✓ | HELD: ultra-steady (±0.06 dB) limiter howl at −15 at arm: established_at_arm 199-202 ms, no FROZEN, confirmed kill; with 0.00 dB wander (synthetic) it is now also cut (N7: frozen needs a frozen skirt) |
| **totals** | | TP 12 miss 3 FP **16** | TP 18 miss 3 FP **16** | | | |

### 6.4b What a wrong cut costs (all 68 breakers, watch_tag, CLOSED loop, note_cut() wired, seeds 1-3)

| scenario (programme / irreducible classes with cuts) | detections | cuts | deepest gain | bands |
|---|---|---|---|---|
| AP01_flute_crescendo_no_family | 6 | 6 | -6 dB | 1 |
| AP05_ring_onset_during_pad_swell (feedback scene) | 3 | 3 | -3 dB | 1 |
| AP06_three_rings_after_master_shove (feedback scene) | 9 | 9 | -3 dB | 3 |
| AP07_very_slow_ring_2dB_s (feedback scene) | 3 | 3 | -3 dB | 1 |
| AP09_projector_whine_minus36_at_arm | 3 | 3 | -3 dB | 1 |
| AP10_ring_at_song_start (feedback scene) | 3 | 3 | -3 dB | 1 |
| AF01_flute_crescendo_messa_di_voce | 12 | 12 | -9 dB | 2 |
| AF02_sine_pad_two_line_swell | 20 | 17 | -9 dB | 2 |
| AF04_established_lf_ring_122Hz_at_arm (feedback scene) | 3 | 3 | -3 dB | 1 |
| AF06_ring_onset_during_pad_swell (feedback scene) | 3 | 3 | -3 dB | 1 |
| AF08_loud_crowd_whistle_minus7dBFS | 9 | 9 | -9 dB | 1 |
| AF09_channel_fader_ride_on_held_organ_note | 1 | 1 | -3 dB | 1 |
| AF12_soprano_closed_vowel_crescendo | 12 | 12 | -9 dB | 2 |
| AF13_organ_note_held_at_arm_minus30 | 3 | 3 | -3 dB | 1 |
| AF14_lf_howl_to_desk_clip_100Hz (feedback scene) | 3 | 3 | -3 dB | 1 |
| AM04_projector_whine_minus40_before_arm | 2 | 2 | -3 dB | 1 |
| AM05_flute_crescendo_straight_tone | 9 | 9 | -6 dB | 2 |
| AM06_organ_swell_pedal_chord | 1 | 1 | -3 dB | 1 |
| AM09_ring_onset_inside_cymbal_wash (feedback scene) | 1 | 1 | -3 dB | 1 |
| AM10_organ_chord_sounding_at_arm | 2 | 2 | -3 dB | 1 |
| AS03_amp_clipped_howl_minus24dBFS (feedback scene) | 3 | 3 | -3 dB | 1 |
| AS04_plateaued_ring_at_H7_slot_of_loud_organ (feedback scene) | 3 | 3 | -3 dB | 1 |
| AS05_organ_dyad_swell_pedal | 19 | 13 | -9 dB | 2 |
| AS06_ring_under_sixteenth_hats (feedback scene) | 3 | 3 | -3 dB | 1 |
| AS08_edge_ring_busmaster_drop20 (feedback scene) | 3 | 3 | -3 dB | 1 |
| AS09_two_rings_octave_co_onset (feedback scene) | 5 | 5 | -3 dB | 2 |
| AS10_theremin_style_single_sine_swell | 14 | 12 | -9 dB | 2 |
| AT01_solo_sine_pad_swell | 24 | 24 | -9 dB | 3 |
| AT03_two_rings_octave_same_step (feedback scene) | 4 | 4 | -3 dB | 2 |
| AT04_lectern_ring_300Hz_over_mains_hum (feedback scene) | 3 | 3 | -3 dB | 1 |
| AT05_ring_grows_out_of_held_organ_note (feedback scene) | 3 | 3 | -3 dB | 1 |
| AT07_watch_fader_push_into_feedback (feedback scene) | 6 | 6 | -6 dB | 1 |
| AT08_organ_note_sounding_at_arm | 2 | 2 | -3 dB | 1 |
| AV02_flute_soft_attacks_120ms | 1 | 1 | -3 dB | 1 |
| AV04_soprano_ff_climax_note_minus6 | 9 | 9 | -9 dB | 1 |
| AV05_howl_e4_limiter_on_geq_centre_watch (feedback scene) | 6 | 6 | -6 dB | 1 |
| AV06_howl_geq_midpoint_e2p5_limiter_watch (feedback scene) | 6 | 6 | -6 dB | 1 |
| AV07_marginal_ring_e2p85_reverberant_cut (feedback scene) | 3 | 3 | -3 dB | 1 |
| AV13_ultra_steady_limiter_howl_at_arm_watch (feedback scene) | 3 | 3 | -3 dB | 1 |
| **all** | | 217 cuts | scenes by deepest cut: {-9.0: 8, -6.0: 5, -3.0: 26} | |

### 6.5 Cost per frame (pure Python 3.13, `run_detector_eval cost`: 7 busy scenes)

frames 2260: mean **287.9 µs**, median 244.7, p99 **716.8**, max **857.3 µs**; per scene: M1 245/380/420; X7 532/763/832; X16 264/413/514; S21 407/767/857; X18 215/408/471; M2 302/442/473; S20 182/311/360 (mean/p99/max µs). Winner: 250 / 768 / 950.

## 7. Irreducible on a magnitude-only 1/10-octave analyser (stated plainly, with what the detector does instead)

1. **Solo family-less dB-linear swell** — flute/recorder/soprano-closed-vowel crescendo, sine pad or theremin fade-in, organ swell
   pedal on one or two notes, one channel's fader ride on a held family-less note (AP01, AF01, AF12, AM05, AS10, AT01, AF02, AS05, AM06,
   AF09). A family-less stable line rising ≥ 6 dB *is* the ring signature [L §5 P6, J §5.2]. The detector cuts on the rise (−3 dB) and,
   exactly as for a howl that beats its cut, deepens each time the line climbs `reemit_rise_db` 3 dB above the emitted level: a swell
   that CONTINUES after the cut walks its band down (§6.4b closed loop: a 25 dB sine-pad fade-in ends at −9 dB on 6 of 9 note-bands, a
   flute messa di voce at −9 on 3 of 6, an organ swell pedal likewise; a swell that stops at the cut stays at −3: 'held' without a
   deepen right, FALSE_CUT when the note ends, cfs releases). Two lines swelling together have one co-growth mate each (three are
   vetoed); two candidates crossing on one shove look the same.
2. **Fast howl to a quiet plateau** — > ~300 dB/s under a bed reaches a limiter/compressor plateau below the LOUD line within ≤ 2
   visible increments (AM01 417 dB/s → −14, AT02 600 → −15, AF03 800 → −13, AS02 1200 → −22, AT06/AM09 inside a cymbal wash): the same
   observation as a sine-lead/whistle onset [J §5.1, C §7.10]. Published as MODERATE at K1 (250 ms) with the tier-B fields; cfs's
   one-shot policy decides (docs/CFS_POLICY.md §3: loud-ish or ≥ 20 dB over the band's baseline and ≥ 0.6 s old → one −3 dB cut, then
   the verdict; quieter ones are alerted on the desk, not cut). The same holds for any fast howl in a HOT show that does not clear the
   arm-window maximum by 3 dB (X16 seed 3 of 9), and for M1 / X14 under a +12/+24 dB display gain offset (the loud-ish line and the
   LOUD legs shift with p95).
3. **A loud steady whistle / ff sung closed vowel** at ≥ the LOUD line with < ±30 c of movement for 250 ms (AF08 −7 dBFS over a −40
   bed; AV04 a soprano's ff climax note at −6 dBFS with the band tacet, vibrato developing only 300 ms in; in a HOT show a whistle
   3 dB above anything the show reached — AM03's are not and stay MODERATE): LOUD by the loop brief's own tier-A criterion [L §4.3];
   passively identical to a howl at its limiter. Cut at K1; with `note_cut()` wired the whistle that ends within `cut_verify_s` is
   'false_cut' (released); one held longer is 'held' → deepened −6/−9 (LOUD is plateau-class evidence). Open loop (no cuts land)
   AF08 13, AV04 15 detections per 3 seeds (re-emitted each `cooldown_s` because the line never comes down); closed loop each
   whistle costs one band −3…−9 dB for its duration (§6.4b).
4. **The at-arm pair in watch** — a family-less steady line already sounding when watch arms: an established howl at −30 (X7, caught at
   0.8 s), an organ note or chord at −30 (AF13, AT08, AM10: one −3 dB cut at 0.85 s, FALSE_CUT later), a projector whine at −36/−40
   (AP09, AM04: one cut) versus a howl at −44/−48 (AF10, AM07: MODERATE only, below `arm_line_min_level_db`) or 14–17 dB prominent
   (AM08, AP08: below `strong_prominence_db`, where "no family" says nothing). Passively identical [C §7.10, J §5.6]; ring_out resolves
   them with the probe (AF05 0 FP), watch reports AT-ARM cuts distinctly (`established_at_arm` in reasons) for the operator to veto, and
   §8 says how to calibrate the two thresholds per venue.
5. **LF howl that plateaus within ~2 analyser settle times** (AF07: 52 Hz, 100 dB/s to −9 dBFS in 0.6 s; settle(52 Hz) = 0.42 s at
   k = 1): the band's own step response to a bass onset lasts as long [L §1.6]; MODERATE at 200–300 ms, tier-B. A full-scale LF howl
   (clip flag, AF14) IS cut when the LF window is declared. Any LF verdict awaits the oscillator measurement of the real analyser [J §5.4].
6. **Ring exactly between two RTA bands seeded by speech partials** (X9: 1.0–1.4 s): the split line's centroid is dragged by every
   syllable landing at ±1 band, the track is re-born and the rise reference restarts; no design in the competition beat 0.9 s here.
7. **Slow ring inside a chord** (X8 1.4–3.9 s, X21 s1 missed): 6/R after visibility for R = 1.4–5 dB/s, plus coincidental family vetoes
   from chord partials re-born together with the ring at every chord change (no birth asymmetry for G5 to use); X21 s1's ring grows
   inside the cluster of the choir partial that seeded it and plateaus 4 dB after the tracker separates them.
8. **Exact-octave co-onset rings with equal excess** (AT03: two modes of one loop crossing on the same step, same rate, plateaux 3 dB
   apart): an 8′+4′ registration until their envelopes diverge (650–750 ms; 1 of 6 missed). Different rates (AS09, X10) are separated.
9. **A ≥ 150 ms family-less acoustic attack from the bed** (flute breath / legato attacks 150–250 ms, slow-attack synth leads, some
   sung onsets): three OBSERVED dB-linear increments summing ≥ 15 dB into a knee = the judge-literal G1 signature of a 100–170 dB/s
   howl under a bed (§3). AV02 (flute upper register, attacks 100–170 ms, −24…−29 dBFS): 10 FP / 15 seeds (was 43 before B2: the
   100–150 ms attacks — two observed increments — no longer count below the loud-ish line); cut once (−3 dB), 'held' without a
   deepen right (below the loud-ish line), FALSE_CUT when the note ends. The former hold-out FP (X21 s6, a 100 ms choir attack) is gone.
10. **Instrument settings the detector can only flag**: peak-hold (frozen lines never become BASE; plateau scenes missed under the
   peak-hold sweep, `PEAK_HOLD_SUSPECTED`/`FROZEN_LINES` raised; a live line must move ≥ 1 LSB in its peak band OR its skirts), display
   gain offsets (`HOT_SPECTRUM`; 18/25 residual FP at +12/+24 dB on whistles / crowd whoops / flute / soprano pushed to within 6 dB of
   display full scale — LOUD believes the display near full scale, §3), slow release (`SLOW_RELEASE`: growth evidence restricted to ≥ 18
   dB-prominent lines; decay 4 s and release-law 17 sweeps 0 FP). cfs pins `/-prefs/rta/gain 0`, `decay 0.25`, `peakhold OFF` and
   re-reads them; whether `gain` reaches /meters/15 at all is UNCONFIRMED.

## 8. Constants that remain single-room guesses, and how to measure them (one evening, X32 Rack + oscillator + logs [J §3])

Log everything as JSONL (every /meters/15 frame with monotonic ts, every OSC write, `/-prefs/rta` at arm/disarm, channel HPF/comp/fader,
a hand event log) and add an `rtasim` replay loader so this corpus harness scores real frames.

| constant(s) | why a guess | measurement |
|---|---|---|
| `analyser_rise_k` 1.0, `lf_onset_step_db` 6, `onset_step_db` 4, FAST-RISE's LF run length | X32 filter-bank architecture unknown; an FFT would smear LF over ±2–4 bands | oscillator sine gated on at 31/40/63/80/125/160/315/1k/4k/8k Hz, −20 dBFS, 5 reps with random gate phase: frames-to-90 % per band, ±1..±4 leakage during and after the rise. k must bound the slowest band |
| `narrow_db` 8, `cluster_merge_db` 6, `partial_tol_bands` 0.6, `centroid_tol_bands` 0.25, `strong_prominence_db` 18 | Butterworth skirts are a model | oscillator at band centre and +20/+40/+60 c at 100 Hz, 1 kHz, 8 kHz; log ±4 bands steady state (also calibrates the centroid and NotchController's flanking rule) |
| `decay_db_per_s` 3, `sag_db` 4, `slow_release_db_per_s` 30, cfs `decay_verify_*` | `/-prefs/rta/decay` law UNCONFIRMED | oscillator off after 2 s on at decay 0.25 / 1 / 4 / 16: fall law and rate (the detector's own `release_db_per_s` reading is the check) |
| absolute legs: `loud_line_db` −10, `loud_above_arm_db` 20, `loud_ceiling_db` −6, `loud_hot_over_max_db` 3, `hot_spectrum_p95_db` −20, `clip_db` −1, `loudish_level_db` −20 / `loudish_above_arm_db` 10, `arm_line_min_level_db` −40, `arm_fast_level_db` −20 | whether `gain`/det shift /meters/15 is UNCONFIRMED; −40 sits between X7 (−30) and X20 (−50) | fixed oscillator while toggling gain 0/6/12/24, det RMS/PEAK, peakhold: does the stream move/freeze (`arm_p95_db`, `PEAK_HOLD_SUSPECTED` are the read-outs)? pink noise at −20 dBFS: RMS vs PEAK per band; drive the bus to clip: 0.00 and neighbours |
| `probe_over_db` 2, `probe_min_hits` 2, `probe_window_s` 1.4, `probe_settle_s` 0.15, `probe_steady_db` 3, `probe_pinned_fraction` 0.35, `cm_deadband_db` 2 | probe visibility rests on guessed excite_coupling and an ideal comb [C §7.6]; three corpus scenes | real ring_out on Main and on a wedge from 10 dB under threshold in +1 dB/1.5 s steps, ×3: silent room / playback injected electrically / playback through a second open mic; per-step 8/12/20-frame medians for the ring band(s) and 20 programme bands → over-response vs deficit, programme response distribution (expect 0 electrical, +1 acoustic), noise vs window; also a limiter-held howl under the steps (expect PINNED) |
| `rise_db` 6, `reemit_rise_db` 3, `cogrowth_*` 2/3/4, `common_mode_db` 6, `family_veto_fraction` 0.4, `partial_rel_db` 18, `comove_tol_db` 3, `independent_*` 8/3, `pitch_*` 0.1/0.3, `arm_family_life_max` 0.2 | fitted to static timbre tables and Wobble modulations; corpus max non-swell programme rise 5 dB | 30 min of programme through the PA with the vocal mic open ≥ 6 dB under ringing (M7 playlist, held vocal notes with vibrato, flute/whistle/Hammond/sine-lead/pad, applause, lectern speech); run the detector offline; list every BASE/MODERATE/STRONG candidate with reasons; the largest family-less stable-line rise sets the `rise_db` margin |
| `stable_frames` 5, `track_match_bands` 0.6, `drift_max_bands` 1.0, cooldown/merge radius, `fast_rise_*` 15/2/3/0.55, `backfill_*` | hand-held hop statistics and real plateau pumping (1–3 dB) are not in the corpus | provoke real rings: fader +3/+6 dB shoves, mic cupped at the wedge, walking the mic through the wedge field, lav at a lectern, with and without programme: frames from visibility to plateau, plateau level vs comp/limiter settings (validates tier-B −20 and `arm_line_min_level_db`), plateau wander (sets `sag_db`, `reemit_rise_db`), hop sizes |
| `window_low_hz_*` 160/63/40, `window_high_hz` 12.5 k | per-rig prior | read `hpon/hpslope/hpf` of the open channels, derive `lf_edge_hz` ≈ 0.7·f_hpf, print it in the report; on a kick-mic → sub path repeat ring_out with `lf_feedback_possible` |
| GEQ bell Q (`geq_q_min/max` 2/4.3), `cut_*` 3/1/1.5/0.15/0.3 | X32 GEQ shape UNCERTAIN; the 'held' tolerance ±1 dB and 1.5 s assume decay 0.25 | pink noise → bus, GEQ on the PRE insert, −3/−6/−9 at 1 kHz; read the RTA at 0, ±1, ±2, ±3 bands; then one Q in device.yaml and in SyntheticRta; a held test tone through the −3 dB cut gives the 'held' drop distribution |
| the FAST-RISE window literals (windows 5/4/3, 15 % share, two-largest ≤ 0.75, new-high +3), MUSICAL's 0.7, the probe HIT/FOLLOWS margins (Δ+0.5 / Δ+1.0), HOT's p90 offset 6 | inline in detector.py (documented at their use); calibration targets of the rows above | promote to yaml keys when the desk log gives values |
| frame timing | jitter/drops under load unmeasured | histogram inter-frame Δt over the evening; sets `coast_frames` and whether K1/K2 should be seconds |

## 9. API for cfs (what changed)

```python
det = FeedbackDetector(cfg, band_hz, mode="watch" | "ringout", lf_feedback_possible=False, lf_edge_hz=None)  # lf_edge_hz overrides the mode defaults
dets = det.feed(values_db, ts)              # list[Detection]; .reasons / .klass ("STRONG" | "PROBE"; "MODERATE" only with ringout_emit_moderate)
                                            #   / .freq_hz (interpolated); a deepen request carries "deepen_held" / "deepen_insufficient"
det.note_gain_step(delta_db, ts)            # ring_out: right after each master write (wired at the RAISE step)
det.note_cut(freq_hz=geq_centre_hz, depth_db=new_total_gain_db, ts=now)   # after each GEQ write (wired in cfs._notch); or note_cut(band=rta_band, ...)
det.candidates -> list[Candidate]           # .to_dict(): klass, level_db, prominence_db, excess_db, age_s, reasons, freq_hz, cut_verdict,
                                            #   cut_deepen, cuts_held, steps_seen, fast_rise_db, born_at_arm, stationary, probe_hits
                                            #   reasons may carry "backoff_advised" (STATIONARY, >= 30 dB prominent: make a back-off probe)
det.cut_log                                  # [{ts, freq_hz, band, step_db, depth_db, bell_db:[lo,hi], drop_db, verdict, deepen, emitted}]
                                             #   verdict in confirmed | insufficient | held | false_cut | ambiguous
det.flags                                    # {"PROGRAMME_PRESENT", "PEAK_HOLD_SUSPECTED", "FROZEN_LINES", "HOT_SPECTRUM", "SLOW_RELEASE"}
det.programme_present()                      # the ring_out contract check (G8: if True, run watch policy and say so) -- evaluate over >= 5 s
det.refresh_arm_reference()                  # re-open the 2 s arm level reference (call on PROGRAMME_PRESENT's rising edge after arming in silence)
det.note_emission(candidate, ts, reason="tier_b")   # cfs cut this live Candidate BY POLICY (no Detection from feed): record the emission on the
                                             #   track as feed() does for its own (emitted, level / probe hits at emission, cooldown) so note_cut()'s
                                             #   verdict treats it as THE cut line and a later re-emission needs fresh evidence; not plateau-class,
                                             #   so never self-deepened. Call right before note_cut(). Returns False for a non-track. (policy round)
det.note_suppressed(candidate, reason="at_arm")     # cfs DECLINED the Detection feed() just returned for this track (watch at-arm rule, CFS_POLICY §5):
                                             #   the emission stays on record (re-emission needs fresh evidence) but 'established_at_arm' leaves
                                             #   emit_evidence (-> 'suppressed_at_arm') and cut_deepen is cleared, so a later held / insufficient
                                             #   verdict on a POLICY cut of the line does not hand the detector a deepen right on the observation cfs
                                             #   declined to act on. Klass / reasons / verdict machinery untouched. False for a non-track. (fix round)
det.arm_p95_db, det.loud_threshold_db, det.loudish_threshold_db, det.release_db_per_s   # report them (cfs puts flags / release / p95 /
                                             #   cut verdicts in the session report under "detector")
```
Tier-B (G7, cfs policy — implemented in docs/CFS_POLICY.md §3): a MODERATE candidate with `level_db ≥ det.loudish_threshold_db` (or
`excess_db ≥ 20`), `age_s ≥ 0.6` and a presence run as long, `cut_verdict is None` → one −3 dB cut → `note_emission()` + `note_cut()`
→ `confirmed` (deepen only on regrowth), `false_cut` (ignore-list; the cut stays, never written shallower), `held` (alert; deepen
after `held_deepen_s` only while still a held family-less BASE line — never on drop ≈ bell alone), `insufficient` (one immediate
deepen), `ambiguous` (report). The detector never deepens a tier-B cut by itself (MODERATE is not plateau-class evidence). Requires
decay forced to 0.25 or the verdicts read the display.
**What ships in cfs.py**: mode + `note_gain_step` (winner), `note_cut()` after every GEQ write and the detector's flags / release /
p95 / cut verdicts in the report, `_calibrate_floor` removed (F6), prefs pinning (rta-ballistics branch) — and, with the policy layer
(**docs/CFS_POLICY.md**, `src/x32mcp/cfs_policy.py` + the "policy layer" section of `cfs.py`): the LF declaration (`lf_edge_hz` from
the included mics' `preamp/hpon`/`hpf`, ≈ 0.7 × the lowest corner, 100 Hz without an HPF, floored at 60; `lf_feedback_possible` on
`feedback_watch` / `ring_out` and the server tools), the tier-B one-shot with the verdict-driven follow-up described above (held →
alert, deepen after `held_deepen_s` only while still a held family-less BASE line; false_cut → ignore-list, never written shallower;
insufficient → one immediate deepen; every step tagged tier "B"), the G8 contract check over the first 5 s and on later rising edges
(`cfs.programme_present`, report warning, `ringout_emit_moderate` forced off, `refresh_arm_reference()` in a watch armed in silence),
candidate alerts that reach the desk (`cfs.candidate` on/off + the bus scribble strip turned RDi while a MODERATE / held / at-arm /
backoff-advised line is live, always restored), the AT-ARM rule in watch (an `established_at_arm` cut only if LOUD or ≥ 30 dB
prominent: AP09's −36 dBFS whine is now alerted, not cut; M7's 60 dB howl still cut at K1), the flag actions (re-force decay /
peak-hold on PEAK_HOLD_SUSPECTED / FROZEN_LINES, abort after `frozen_abort_s`; SLOW_RELEASE / HOT_SPECTRUM warnings) and the
back-off probe on `backoff_advised` (§11 N1). With the LF edge wired, the `_tag` columns of §6 are the shipped configuration for rigs
whose channels carry HPFs at or below the scene's LF source; `lf_feedback_possible` gives the 40 Hz window.

## 10. Tests

* `tests/test_detector.py` — synthetic streams (module-local generator): clean music 0; held note 0 and the same 80 dB/s family-less
  ramp IS feedback unless it carries a family; 15 dB/s ring ≤ 7 frames after its 12 dB crossing in a stream where a melody note masks its
  early climb and a 20 dB/s ring ≤ 6; two rings; noise; plateau/transient; vibrato; established 60 dB howl caught with zero growth, its
  bit-identical (peak-held) copy flagged FROZEN and not cut, a 15 dB plateau not cut; `device.yaml` == `DetectorConfig` defaults; notch
  controller.
* `tests/test_detector_predicates.py` — one test per predicate / evidence rule / flag / hook: P1 hump vs split line, P2 family / octave
  pair / clipped-howl harmonics, P3 glide / edge vibrato, P4 decaying line, common mode, P5 at-arm lines (hum, whine, howl), P6 LF onsets
  and LF ring with declaration, probe over-response vs 1 dB/dB, **F2 forty steps** (`test_f2_probe_bookkeeping_survives_forty_steps`: a
  loop surfacing after step 34 of 40 is judged on sequence numbers ≥ 36 while the step list stays pruned and a whine stays STATIONARY),
  G1 fast howl vs instant arrival (MODERATE with tier-B fields), G2 held-and-deepened (plateau-class, loud-ish: one re-emission per
  verdict) / held-not-deepened (rise-only) / false_cut on note end / confirmed / insufficient and the bell table, G3 frozen (peak-hold vs
  live vs steady-peak-only), G4 ring_out at-arm waits / follows / pinned, G5 independent partner, G6 arm-referenced LOUD with ceiling
  and HOT (0.0 not loud; above the arm maximum loud; not when the arm window held such a line), G8 programme presence, hooks (`lf_edge_hz`,
  `note_cut(band=)` + 'ambiguous', SLOW_RELEASE from live bands only, non-finite clamp, `refresh_arm_reference`, `to_dict` fields),
  reasons/interpolated frequency, cost.
* `tests/test_detector_regressions.py` — rendered corpus scenarios: X11 3/3 ≤ 300 ms, AP02 (Y2) 0 FP, AF05 (Y5) 0 FP in ring_out, AP07
  (Y7) reachable, AM01/AT02/AF03/AS02 published MODERATE ≤ 250 ms (+ jitter), AF14 LF clip flag, AS08 edge ring next to an H3 slot;
  verifier round: B1 (AV03 seeds 1/2/4/7/8 and X1 s11 0 FP, X7 kept), B2 (AV02 s2/s3 0 FP, AV01 s6, X21 s6/s11 0 FP with the ring caught,
  M1/X11/AS03 3/3 ≤ 300 ms), B3 (AV05/AV06 closed loop: two cuts, −6 dB, ring dead; AV07 one cut, not alive, no false_cut), loud show
  (X16 hold-out 4-6 and X14 hold-out 4/5/8 caught ≤ 300 ms, AP03/AP04 gain offsets 0 FP).
* `tests/test_detector.py::test_ring_detected_quickly` asserts ≤ 7 frames after the 12 dB crossing for its 15 dB/s stream (the judge asked
  for ≤ 6: in that fixture a melody note shares the ring's band until 4 frames before the crossing, so the ring's earlier climb is not
  its own and 6 dB at 0.75 dB/frame lands at +7 = 350 ms; 6/R after visibility is the physical bound) and ≤ 6 for a 20 dB/s ring in the
  same programme. The held-note acceptance fixture uses a 2-frame attack: its former 5-frame (250 ms, 80 dB/s, family-less, to −14
  dBFS) ramp IS the FAST-RISE signature and is asserted detected unless it carries a family (§7.9, a lead decision recorded here).
* `tests/test_detector_corpus.py` — harness plumbing over the whole corpus (closed-loop runs now record `rings_end` / `alive` and fail a
  detected ring left alive); `tests/integration/test_cfs.py` — 20 CFS session tests (mode + `note_gain_step` + `note_cut` wiring;
  `_calibrate_floor` test removed with the method).

## 11. Verifier findings (fix round) and known limitations

Two independent reviews of commit 8094eed (metrics re-measurement with 13 new breakers AV01–AV13; line-by-line code review). Every
published number reproduced exactly. Disposition:

| finding | disposition |
|---|---|
| **B1** at-arm evidence inherited through coasting tracks by notes starting after arm (AV03 5/9 seeds, X1 s11) | **fixed** — `born_at_arm` / `arm_evidence` cleared on every presence-run restart, `onset_fi` for co-onset, AT-ARM evaluated only if the first K1 matched frames fall in the track's first K1+2 frames (`_extend`); AV03 0/9, X1 s10-15 0, at-arm TPs unchanged |
| **B2** FAST-RISE admitted two observed increments via the virtual bed sample (AV02 43 FP/15 seeds, X21 s6/s11/s13, AV01 s6) | **fixed** — the bed step counts only for loud-ish lines (`loudish_level_db` −20 / `loudish_above_arm_db` 10, `_fast_rise`); AV02 → 10/15 seeds (the ≥ 150 ms attacks, §7.9), X21/AV01 chord partials 0, M1/X11/X14/AS03 kept; hold-out 4-9 FP 1 → 0 |
| **B3** limiter-held howl with e > bell filed false_cut and never deepened in watch (AV05 alive 3/3) | **fixed** — verdict 'held' vs 'false_cut', one deepen per held/insufficient verdict for plateau-class loud-ish lines (`_verify_cut`, emission gate), pre-cut level = max of the last frames, `note_cut()` wired in cfs `_notch`, harness scores `alive`; AV05 dead 3/3 at −6 dB |
| **#4** G6 + HOT made LOUD unreachable in loud shows (X16 0/3, X14 hold-out 3/6; no tier-B in this cfs) | **fixed (detector side)** — `loud_ceiling_db` −6 caps the p95-relative leg; under HOT the clip value is neither flag nor level and the level legs need ≥ arm max + `loud_hot_over_max_db` 3; X16 8/9, X14 hold-out 6/6, M1 hold-out 6/6, AM03 still 0; price gain +12 15 → 18, +24 10 → 25 (§6.3); tier-B policy remains cfs's (documented above as the gating follow-up) |
| **#5** claimed F2 forty-steps test did not exist | **fixed** — `test_f2_probe_bookkeeping_survives_forty_steps` |
| N1 G4 costs 4 s (AV08 limiter, PINNED) / 9.5 s (AV09 2:1 compressor FOLLOWS → STATIONARY) when ring_out arms into a quiet howl | **documented + hook** — PINNED is not made positive evidence: with the RTA tap upstream of the bus master a ROOM whine also reads ~0 dB/dB (only the tap-downstream-of-master rig makes it follow), so the tap point must be known first; `backoff_advised` is now raised on ≥ 30 dB-prominent STATIONARY lines and cfs should answer an AT-ARM MODERATE at ring_out arm with a −3 dB back-off probe at K1 instead of the first +1 dB step (howl dies or is pinned; whine follows) — `_probe` already judges negative steps |
| N2 LOUD emits at K1, 50–100 ms before sung vibrato develops (AV04); loud_now re-emission ratchet | LOUD-at-K1 kept (by spec, §7.3; K2 would cost S2a/b/c their 300 ms budget); **fixed**: LOUD re-emits only if the line did not come down since the emission; closed loop the verdict path governs (held → deepen for LOUD, §4) |
| N3 PROGRAMME_PRESENT latched under ring_out steps (AV11) | **fixed** — STATIONARY / probe-following lines and lines born inside a step's probe window no longer count as occupancy / events |
| N4 bystander tracks 'confirmed' with drop 0.0 | **fixed** — only the emitted track is confirmed by vanishing |
| N5 seeds 10-15 unreported | **reported** (§6.2) |
| N6 what ships in cfs.py | **stated** (§9); `note_cut` + report fields wired this round |
| N7 frozen rule margin (0.00 dB synthetic tone never cut) | **fixed** — frozen needs a skirt band frozen too (a peak-held display freezes regions; a steady tone's skirts jitter) |
| N8 (a) ≤ 7 vs ≤ 6 frames, (b) at-arm count 19 vs 21, (c) freq precision claim, (d) cost, (e) alive not scored | (a) documented §10; (b)(c)(d) corrected in the results; (e) **fixed** in the harness |
| held-note fixture 5 → 2-frame attack | lead decision, recorded in §10 / §7.9 |
| non-finite input blinds the detector | **fixed** — clamp to [−128, 0] in `feed()` + test |
| SLOW_RELEASE measured on floor-pinned bands; reads the simulator's own 60 dB/s cap | **fixed** — live bands only, ≥ 6 of them, else no measurement; the 60 dB/s reading is the corpus's release law by construction and the desk's first read-out replaces it (§8) |
| arm p95 is a frozen 2 s snapshot (arming in silence vs in the show) | **hook + documented** — `refresh_arm_reference()`; operators: arm watch at show level or let cfs refresh on PROGRAMME_PRESENT's rising edge; LOUD/loud-ish otherwise keep the silent-room reference (a −8 dBFS whistle after arming in silence is LOUD; arming during the same show it needs −6…−4) |
| programme_present() False at t = 2 s on quiet background music (M2 2/3 seeds) | **documented** (§4): informational; evaluate ≥ 5 s and OR other signals |
| P5 baseline absorbed a standing line in ~40 s | **fixed** (§1) |
| doc-vs-code divergences (co-growth mates 1.5, FAST-RISE window wording, MUSICAL 0.7, klass MODERATE emission, seeding coefficients, LOUD under HOT, AF08 counts) | **fixed** in §1–§4, §7, DESIGN §12 |
| decision-affecting inline literals not in yaml | **documented** (§8 last row); none is a simulator value |
| hook test gaps (lf_edge_hz, note_cut(band=), SLOW_RELEASE, ambiguous, to_dict keys, G2 assertion precedence) | **fixed** (§10) |
| stale README / DESIGN §5 example / unused cfs import | **fixed** |
| scenario names cited as evidence in code comments | **fixed** (rephrased as the physical statement) |

Known limitations that remain (besides §7): the at-arm pair and its three single-room thresholds (§3 AT-ARM, §8); G4's 2-step wait
in ring_out for a quiet established line (N1); LOUD's whistle class incl. loud shows (§7.3); the display-gain sweeps (§7.10);
`programme_present()` under-reports quiet dense mixes; every LF verdict and every absolute leg await the §8 desk measurements; the
closed-loop policy (deepen on held) assumes the GEQ is inserted PRE (HANDOVER §4b) and decay 0.25.
