# disc-minimal-delta — CFS² feedback discriminator: smallest change that is actually correct

Worktree `…/scratchpad/worktrees/disc-minimal-delta`, branch `wt/disc-minimal-delta` (from `wt/corpus-critic` @ 1be8603).
Files: `src/x32mcp/detector.py` (FeedbackDetector rewritten in place; NotchController byte-identical), `device.yaml`
(`detector:` section), `src/x32mcp/cfs.py` (+10 lines, optional), `tests/test_detector.py` (fixtures/assertions, listed in §7),
`tests/rtasim/run_eval_disc.py` (evaluation driver). Metrics: `reports/disc-minimal-delta/metrics.json` (5 runs × 3 seeds),
`holdout_seeds456.json`, `holdout_seeds789.json`, `sweep.json` (analyser presets), `eval_tables.txt`.
Citations: [L §x] loop-physics brief, [A §x] analyser brief, [C §x] CORPUS.md, [N] lead reviewer notes.

Headline (device.yaml config, seeds 1-3, each scenario in its own mode, default window, open loop):
**61 scenarios, 117 events: FP 0, TP 107, miss 10, 51 scenarios pass; median TP latency 152 ms, 90/107 within 300 ms.**
Hold-out seeds 4-6 / 7-9: FP 0 / 0. Closed loop: FP 0, 0 HARM, 0 TAIL, 115 cuts for 108 TP. Analyser sweep (u_slow, bq,
skirt N=2/5, RMS, flat beds, decay 0.25, release law 240): FP 0; decay 4 s / release law 17: 1 FP each (prefs arm must force).
Baseline (current detector, same corpus): FP 948, miss 9, 12 pass.

## 1. What was wrong, mechanically, and what replaces it

| mechanism (brief §1, [N]) | old code | replacement |
|---|---|---|
| growth mandatory: `0.3·P + 0.2·S + 0.5·G ≥ 0.7` unreachable without G; 25 dB/6 fr override bolted on | detector.py `_score` | explicit lanes: CLIP ∨ GROWTH ∨ SUSTAINED ∨ PROBE, each independently sufficient, each gated by MUSICAL; the weighted sum survives only as the *reported* confidence |
| analyser LF smear read as growth (1/Δf rise, [A §0.1, §1], [L §1.6]) + 60 dB/s onset guard that discards ~half of real howls ([L §1.3]) | `growth_max_db_per_s`, `_score` restart | (a) frequency window per mode; (b) growth window must span `4/Δf` (n_g(band)); (c) shape tests: continuous ramp, late-half share; (d) per-band **step memory** replaces the rate cap: an arrival confined to ≤ 3 frames is programme whatever its dB/s, a ramp over ≥ 4 frames is admissible whatever its dB/s |
| harmonic programme accepted (no family test) | – | single-frame harmonic family with sub-band (centroid) precision, co-movement and level plausibility; family vs pair levels |

The tracking skeleton is unchanged: prominence → qualifying bands → `_peaks` → `Candidate` streaks matched within
`±band_tolerance` → verdict → per-band `cooldown_s` re-emission (deepening). Public contract unchanged
(`FeedbackDetector(cfg, band_hz)`, `.feed() -> list[Detection]`, `.candidates`, `.reset()`, `.cfg`,
`DetectorConfig.from_descriptor(Descriptor.load())` with the repo's current device.yaml → all new keys default). Added:
`Detection.reasons: tuple[str,...]`, `Detection.centroid`, `freq_hz` = centroid frequency (sub-band, for GEQ choice [L §4.1]);
`FeedbackDetector(cfg, band_hz, *, mode=None, lf_feedback_possible=None)`, `.note_gain_step(delta_db, ts)`, `.note_cut(...)`
(no-op), `.window_hz`; `DetectorConfig.mode ('watch'|'ringout')`, `.window_hz`.

## 2. Decision logic in full (src/x32mcp/detector.py; thresholds = device.yaml values)

Per frame (`feed`, l.890): `ref` = median of bands 25..85 (spectrum reference, [A §5]); per-band step memory updated
(`_update_steps` l.680); cluster levels cached for co-movement; prominences.

**Q. Qualification** (l.900-909). Band i qualifies iff inside the window (Q4), `level ≥ min_level_db` (−80: numeric floor only),
`narrowness ≥ narrow_db` (10 dB) and (`prominence ≥ 12` or `cluster_prominence ≥ 12`).
* prominence = level − median(±3) (unchanged, DESIGN §12). cluster prominence = powersum(i−1..i+1) − median(i±2..±4)
  (l.561): a line between two centres reads −3/−3 dB [A §3]; this is also the corpus's visibility definition [C §1].
* narrowness (l.534) = level − 2nd loudest of {i±2, i±3}. Physics [A §3, N P1]: a lone sinusoid clears ≥ 24 dB at ±2 on
  N ≥ 2 skirts; formant humps / cymbal wash / PA ripple are ≥ 3 bands wide. The loudest of the four is forgiven because one
  unrelated partial two bands away is common and says nothing about this line's width (X9, X11, M3 otherwise never
  qualify). 10 not 12: it is an order statistic on a ±1–4 dB floor, 2 dB under the median-based prominence (S11a/S10 data).
* Runs of adjacent qualifying bands → loudest band = peak (unchanged); each peak gets a power centroid over b−1..b+1 (l.549).
**Q4 window** (`DetectorConfig.window_hz` l.306; [L §3.3-3.4], [A §4.5]): watch 160 Hz–12.5 kHz; ringout 100 Hz–12.5 kHz;
`lf_feedback_possible` → 40 Hz. Derivation: loop gain of a far-field vocal mic into tops is 20–30 dB down below ~150 Hz
(mic roll-off × HPF 0.7·fc × box cut-off) so in watch (programme present, LF = where programme energy is highest) LF lines are
programme; ring_out has no programme by contract and exists to find modes → wider; kick/tom/acoustic pickups near subs ring at
40–200 Hz → operator/cfs opt-in. 12.5 kHz: condensers ring to 10–12 kHz, M7's SM58 rang at 8.1 kHz. cfs may narrow further
(0.7 × lowest open HPF) — hook is the `window_lo_hz` key; not wired to the patch sheet in this change.

**S. Step memory** (`_is_step` l.661, `_update_steps` l.680; per band, independent of qualification). Over six consecutive levels
l0..l5 of a band: an *arrival* is a rise ≥ `step_db` (9 dB) confined to k ≤ `step_max_frames` (3) frames ending at l4 with
the frame before the span and the frame after (l4→l5) each < `step_continue_frac` (0.3) of it. The band stores the arrival
level and the pre-arrival level and stays "alive" until its level is back within `step_release_db` (3 dB) of the pre-arrival
level (the release tail of a note, however long RTA decay stretches it, is still that note: S15). Physics [L §1.3, §1.6],
[A §1]: programme above ~300 Hz reaches full level within one frame (+ one partially integrated frame); portamento/scoops
take 80–150 ms [A §4.2]; a loop grows through every level at e/τ dB/s and even at 18 dB/frame (360 dB/s) rises on ≥ 4
consecutive frames for any plateau ≥ 30 dB above its seed, so it is never an arrival. There is no dB/s cap anywhere.
A candidate is `step_onset` while any band within 0.8 of its centroid is alive (l.752-766; 0.8: a line between centres owns
both bands, a neighbour's arrival one band from a centred line is not this line's onset — M3/X8); the flag clears when no band
within 1.0 is alive (hysteresis for vibrato on an edge, X6). When an arrival lands inside the growth window the window
restarts after it (judge what follows an arrival, never the arrival) and any open probe measurement is void.
When a track starts, the band's last `history_frames` (8) levels seed the growth window (`_seed_history`), so an onset that
completed before qualification is still in view (X11/X16-type late qualification, S3 crash masking).

**F. Harmonic family** (`_family` l.623, `_partial_at` l.606, `_comoves` l.595). Offsets 10·log2 k: H2 +10, H3 +15.85,
H4 +20, H5 +23.22 bands [A §4.1]. Partial present at fractional position p iff a local-maximum band q within
`harmonic_tol_bands` (0.5) of p by centroid has prominence ≥ max(`harmonic_presence_db` 6, own prominence − `harmonic_rel_db`
18) [N P2 "presence as peak", L §2.2 "within ~15 dB"], and **co-moves**: range of (C_q − C_b) over the frames since the
candidate's birth (≤ `comove_frames` 6; < 3 frames ⇒ accept) ≤ `comove_db` (5 dB) [L P1 "envelope co-moves"; A §4.4:
cluster power is vibrato-immune; 5 dB admits chorus/ensemble beating ±3 dB (S21) and piano partials decaying at different
rates, rejects syllables/notes pulsing next to a steady ring (X9, X7)]. Verdict per frame:
* **family (2)**: ≥ `family_min_partials` (2) of H2..H5 present; or the line is partial k of a lower present line that owns
  another partial j≠k, where the lower line is plausibly the fundamental for level: the candidate may exceed it by
  `parent_excess_low_db` (8) for k = 2,3 (open vowels, bass whose H1 the PA high-passed [A §4.2]) but only
  `parent_excess_high_db` (2) for k = 4,5 (no source's 4th/5th partial towers over its fundamental; X7's ring sits 35 c from
  organ E4×5 and 4 dB above E4 — a coincidence, not a partial).
* **pair (1)**: exactly one H2/H3 within `single_partial_rel_db` (15 dB) of the line's *level* (organ 8'+4' −4 dB, flute −15;
  a clipping howl's H2 ≤ −20, H3 −10…−17 and only near clip [L §2.2]), within `single_partial_tol_bands` (0.35) and
  co-moving within `single_partial_comove_db` (1.5); or such a line exactly an octave *below* (b is its H2: "no subharmonics
  ever" [L §2.2]). Two coexisting rings ×2.00/×2.02 apart (X10, S11b, < 1 % of events [L §2.3]) form at most a pair and,
  growing at their own e/τ, fail the 1.5 dB co-movement while either grows.
* Candidate keeps the last `family_window_frames` (12) verdicts and a lifetime count.
**MUSICAL** (l.800-812): strict := family on ≥ `family_veto_frac` (0.4) of the window, or centroid range over the sustain
window > `centroid_wander_bands` (0.35; a ring's centroid sd < 0.05 band, vibrato ±30 c ≈ ±0.25 band [A §4.3 v, §4.4]);
loose := pair on ≥ 0.4 of the window or pair/family on ≥ `family_lifetime_frac` (0.5) of the track's life (a held chord tone
keeps its partners for life while partners' bands get shared/unshared by other chord tones; a ring's coincidences are rarer).
A line ≥ `clip_level_db` is never MUSICAL.

**G. GROWTH lane** (l.782-798) — positive evidence, never required. Window = candidate levels since the last restart
(drop > `monotonic_tolerance_db` 1 dB, or an arrival inside it), n ≥ n_g(band) = max(`growth_min_frames` 3,
⌈`growth_lf_periods` 4 /(Δf·T)⌉), Δf = 0.0693 f: 3 frames ≥ 400 Hz, 5 @ 250 Hz, 8 @ 160 Hz, 11 @ 110 Hz, 19 @ 63 Hz
[A §1 table; L §1.6: any filter resolving Δf needs ≥ 1/Δf; 4/Δf puts a smeared onset in the first half of the window].
Require: LS slope of level AND of (level − ref) ≥ `growth_min_db_per_s` (6); rise of both ≥ `growth_rise_db` (6 dB)
[common-mode/fader moves have no corrected rise, S6/X12a; a steady line over a fading mix has no absolute rise, S20];
two largest single-frame increments ≤ `growth_step_share` (0.7) × rise and ≥ `growth_min_steps` (3) increments each
≥ `growth_step_min_share` (0.5) × mean increment (a continuous ramp: a partially integrated first frame, a neighbour note's
arrival or a vibrato swing are one or two jumps — the S3/S8/X3/X4 baseline FP mechanism [A §0.1]); later-half share ≥
`growth_late_share` (0.25) (an analyser-limited step decelerates: last 3 dB at 6–26 dB/s [L §1.6 table]; a loop's ramp is
dB-linear [L §1.3]). Blocked by strict MUSICAL, and deferred while the current frame shows a family or the track is younger
than `sustain_frames` and has shown a family (swelling pads surface high partials first, S21; voiced syllables glide, S22).
No upper slope bound: M1 200 dB/s, X16 240, S9 115, X12b 100 are caught at 98–250 ms by growth or clip.

**P. SUSTAINED lane** (l.814-826) — the plateau lane that replaces the override. Require now: not `step_onset`; wander ≤ 0.35;
current level ≥ max(last `sustain_frames`) − `sustain_drop_db` (3 dB) (plucked/struck notes decay 3–15 dB/s [L §2.1]; an
intact loop does not [L §1.4]); not frozen (fewer than half the intervals in the window move by > `frozen_eps_db` 0.002 dB ⇒
RTA peak-hold display, S16 [L §0, A §2]); narrow; `level ≥ sustain_min_level_db` (−45); and age ≥ `sustain_frames` (6 =
300 ms) if max(prominence, cluster prominence) ≥ `sustain_strong_prominence_db` (18) else `sustain_moderate_frames` (12 =
600 ms) [A §4.3: below ~20 dB absence of family proves little; N: MODERATE waits K2 = 12]. Blocked by loose MUSICAL.
`sustain_min_level_db` is the one absolute level left and it is confined to this lane on purpose: with no onset and no growth
observed, level is the only passive evidence separating X20's −50 dBFS whine (present before arm, no family) from X7's −30
dBFS compressor-held howl [C §7.10]; −45 is the M7 number and is honest about being one; ring_out resolves quieter lines
with the probe, growth has no level gate at all (X23's modes are caught from −60).

**C. CLIP lane**: `level ≥ clip_level_db` (−6) on `persistence_frames` consecutive frames and narrow ⇒ emit whatever the
family/onset [L §1.4(3), A §1 "treat level ≥ −0.5 as its own predicate"; −6 leaves room for a limiter ceiling under 0 dBFS
and a howl arriving in 2 frames at > 400 dB/s]. Programme lines at −6 dBFS per 1/10-oct band do not occur at a sanely
staged pre-fader tap (X16's loud band peaks at −13); −13 would catch X11 and FP on X16 (measured, §5).

**R. PROBE lane** (ring-out active probe; `note_gain_step` l.875, l.828-835): at each server master step every candidate stores
base = median(recent levels) − ref; a candidate not MUSICAL, with no arrival since the step, whose min(last 3 levels) − ref
exceeds base + step + `probe_excess_db` (2 dB) within `probe_window_s` (1.2 s) scores a hit; `probe_confirmations` (2) hits
⇒ emit "probe" [L §1.5: ≤ 1 dB/dB for anything not regenerating, ≥ +3 dB/dB within 4 dB of threshold; C §2.3: with noise
excitation the over-response is ≥ 2 dB/dB only within ~3 dB and noisy ⇒ two confirmations, 3-frame hold]. Newcomers born
inside the window take base = min of their seeded history (a line that *appears* after a step is the strongest response).
Result: X23 mode 1 caught 1.15 s *before* onset (EARLY), S6b/M2 unchanged, 0 probe FPs on organ programme.

**Verdict** (l.838-851): frames ≥ `persistence_frames` (3) and (CLIP ∨ (GROWTH ∧ ¬strict ∧ ¬fam_wait) ∨ (SUSTAINED ∧
¬MUSICAL) ∨ (PROBE ∧ ¬MUSICAL)) and not in cooldown (±1 band, 1 s). `reasons` records the lanes plus `step`, `frozen`,
`family`, `pair`, `wander`, `sustained@arm`. Confidence (dashboard only) = normalised 0.3·min(1,P/24) + 0.2·min(1,frames/6)
+ 0.5·(1 if verdict else ½·growth score), clamped to ≥ 0.7 iff verdict.

## 3. Metrics (device.yaml config; `tests/rtasim/run_eval_disc.py`; cell = TP/events m(iss) FP [lat min/med/max ms] P|F seeds-passed, c = cuts)

| scenario | bud ms | baseline (old, open) | auto/open (seeds 1-3) | auto/closed | all-ringout/open | auto+LF opt-in/open | hold-out 4-6 | hold-out 7-9 |
|---|---|---|---|---|---|---|---|---|
| C0_silent_room | 300 | 0/0 m0 FP0 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| C1_music_bed_drums | 300 | 0/0 m0 FP0 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| S1_bass_under_quiet_music | 300 | 0/0 m0 FP32 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| S2a_established_ring_8k | 300 | 3/3 m0 FP0 [252] | 3/3 m0 FP0 [251/252/253] P3 | 3/3 m0 FP0 [251/252/253] P3 c3 | 3/3 m0 FP0 [251/252/253] P3 | 3/3 m0 FP0 [251/252/253] P3 | 3/3 m0 FP0 [247/249/252] P3 | 3/3 m0 FP0 [250/251/252] P3 |
| S2b_established_ring_8k_steep | 300 | 3/3 m0 FP0 [252] | 3/3 m0 FP0 [251/252/253] P3 | 3/3 m0 FP0 [251/252/253] P3 c3 | 3/3 m0 FP0 [251/252/253] P3 | 3/3 m0 FP0 [251/252/253] P3 | 3/3 m0 FP0 [247/249/252] P3 | 3/3 m0 FP0 [250/251/252] P3 |
| S2c_established_clipped_2k4 | 300 | 3/3 m0 FP0 [252] | 3/3 m0 FP0 [98/100/100] P3 | 3/3 m0 FP0 [98/100/100] P3 c3 | 3/3 m0 FP0 [98/100/100] P3 | 3/3 m0 FP0 [98/100/100] P3 | 3/3 m0 FP0 [98/100/101] P3 | 3/3 m0 FP0 [100/102/102] P3 |
| S3_ring_during_music | 300 | 3/3 m0 FP5 [502] | 3/3 m0 FP0 [100/148/602] F2 | 3/3 m0 FP0 [100/148/602] F2 c3 | 3/3 m0 FP0 [100/148/602] F2 | 3/3 m0 FP0 [100/148/602] F2 | 3/3 m0 FP0 [100/502/550] F1 | 3/3 m0 FP0 [98/498/502] F1 |
| S4a_vocal_vibrato | 300 | 0/0 m0 FP40 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| S4b_vocal_vibrato_band_edge | 300 | 0/0 m0 FP31 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| S5_guitar_note_decays_to_sine | 300 | 0/0 m0 FP13 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| S6_master_ramp_feedback_watch | 300 | 0/0 m0 FP7 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| S6b_ringout_steps_latent_loop | 300 | 3/3 m0 FP2 [148] | 3/3 m0 FP0 [99/202/250] P3 | 3/3 m0 FP0 [99/202/250] P3 c3 | 3/3 m0 FP0 [99/202/250] P3 | 3/3 m0 FP0 [99/202/250] P3 | 3/3 m0 FP0 [100/103/249] P3 | 3/3 m0 FP0 [99/102/151] P3 |
| S7_808_sub_bassline | 300 | 0/0 m0 FP19 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| S8a_organ_melody | 300 | 0/0 m0 FP13 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| S8b_flute_held_note | 300 | 0/0 m0 FP20 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| S8c_whistle | 300 | 0/0 m0 FP28 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| S9_clipped_howl_fast | 300 | 3/3 m0 FP0 [252] | 3/3 m0 FP0 [99/102/103] P3 | 3/3 m0 FP0 [99/102/103] P3 c3 | 3/3 m0 FP0 [99/102/103] P3 | 3/3 m0 FP0 [99/102/103] P3 | 3/3 m0 FP0 [99/99/100] P3 | 3/3 m0 FP0 [98/99/101] P3 |
| S10_ring_between_bands | 300 | 3/3 m0 FP0 [699] | 3/3 m0 FP0 [148/200/251] P3 | 3/3 m0 FP0 [148/200/251] P3 c3 | 3/3 m0 FP0 [148/200/251] P3 | 3/3 m0 FP0 [148/200/251] P3 | 3/3 m0 FP0 [197/199/200] P3 | 3/3 m0 FP0 [151/152/200] P3 |
| S11a_two_rings | 300 | 6/6 m0 FP0 [376] | 6/6 m0 FP0 [98/175/698] F1 | 6/6 m0 FP0 [98/175/1001] F1 c6 | 6/6 m0 FP0 [98/175/698] F1 | 6/6 m0 FP0 [98/175/698] F1 | 6/6 m0 FP0 [97/98/149] P3 | 6/6 m0 FP0 [101/125/449] F2 |
| S11b_two_rings_near_octave | 300 | 6/6 m0 FP0 [300] | 6/6 m0 FP0 [99/124/152] P3 | 6/6 m0 FP0 [99/151/352] F2 c6 | 6/6 m0 FP0 [99/124/152] P3 | 6/6 m0 FP0 [99/124/152] P3 | 6/6 m0 FP0 [98/99/150] P3 | 6/6 m0 FP0 [97/127/601] F2 |
| S12_acoustic_guitar_wedge_ring_196Hz | 600 | 3/3 m0 FP5 [349] | 3/3 m0 FP0 [300/301/302] P3 | 3/3 m0 FP0 [300/301/302] P3 c3 | 3/3 m0 FP0 [300/301/302] P3 | 3/3 m0 FP0 [300/301/302] P3 | 3/3 m0 FP0 [200/249/251] P3 | 3/3 m0 FP0 [298/300/302] P3 |
| S13_slow_ring_3dB_s | 1000 | 0/3 m3 FP0 [–] | 3/3 m0 FP0 [747/999/1602] F2 | 3/3 m0 FP0 [747/999/1602] F2 c3 | 3/3 m0 FP0 [747/999/1602] F2 | 3/3 m0 FP0 [747/999/1602] F2 | 3/3 m0 FP0 [1001/1251/2000] F0 | 3/3 m0 FP0 [1248/1301/1549] F0 |
| S14_ring_masked_by_cymbal | 300 | 3/3 m0 FP0 [198] | 3/3 m0 FP0 [99/150/198] P3 | 3/3 m0 FP0 [99/150/198] P3 c3 | 3/3 m0 FP0 [99/150/198] P3 | 3/3 m0 FP0 [99/150/198] P3 | 3/3 m0 FP0 [100/149/152] P3 | 3/3 m0 FP0 [97/102/148] P3 |
| S15_long_rta_decay_tails | 300 | 3/3 m0 FP1 [202] | 3/3 m0 FP0 t19 [98/102/102] P3 | 3/3 m0 FP0 [98/102/102] P3 c3 | 3/3 m0 FP0 t19 [98/102/102] P3 | 3/3 m0 FP0 t19 [98/102/102] P3 | 3/3 m0 FP0 t18 [97/99/151] P3 | 3/3 m0 FP0 t19 [97/98/98] P3 |
| S16_peak_hold_on | 300 | 0/0 m0 FP12 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| S17_kick_pattern | 300 | 0/0 m0 FP0 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| S18_vibrato_on_band_edge | 300 | 0/0 m0 FP9 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| S19_driven_room_mode | 300 | 0/0 m0 FP40 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP1 [–] F2 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| S20_song_start_stop_crowd | 300 | 0/0 m0 FP6 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| S21_synth_pad_swell | 300 | 0/0 m0 FP66 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| S22_speech_ringing_then_feedback | 300 | 3/3 m0 FP98 [102] | 3/3 m0 FP0 [97/150/151] P3 | 3/3 m0 FP0 [97/150/151] P3 c3 | 3/3 m0 FP0 [97/150/151] P3 | 3/3 m0 FP0 [97/150/151] P3 | 3/3 m0 FP0 [98/148/250] P3 | 3/3 m0 FP0 [148/150/349] F2 |
| S23a_autogain_drift | 300 | 0/0 m0 FP18 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| S23b_gain_offset_clip | 300 | 0/0 m0 FP16 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP19 [–] F0 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| S24_bells_triangle_glock | 300 | 0/0 m0 FP8 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| M1_loud_band_wedge_ring | 300 | 3/3 m0 FP0 [251] | 3/3 m0 FP0 [102/200/202] P3 | 3/3 m0 FP0 [102/200/202] P3 c3 | 3/3 m0 FP0 [102/200/202] P3 | 3/3 m0 FP0 [102/200/202] P3 | 3/3 m0 FP0 [200/201/201] P3 | 3/3 m0 FP0 [198/200/202] P3 |
| M2_quiet_music_ringout_two_modes | 300 | 6/6 m0 FP8 [201] | 6/6 m0 FP0 [97/102/152] P3 | 11/11 m0 FP0 [97/99/248] P3 c11 | 6/6 m0 FP0 [97/102/152] P3 | 6/6 m0 FP0 [97/102/152] P3 | 6/6 m0 FP0 [98/101/102] P3 | 6/6 m0 FP0 [98/100/102] P3 |
| M3_jazz_trio_lav_ring_400Hz | 300 | 3/3 m0 FP0 [302] | 3/3 m0 FP0 [98/101/249] P3 | 3/3 m0 FP0 [98/101/249] P3 c3 | 3/3 m0 FP0 [98/101/249] P3 | 3/3 m0 FP0 [98/101/249] P3 | 3/3 m0 FP0 [150/152/198] P3 | 3/3 m0 FP0 [198/198/352] F2 |
| X1_organ_held_notes | 300 | 0/0 m0 FP9 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| X2_flute_held_vibrato | 300 | 0/0 m0 FP20 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| X3_whistle_held_drift | 300 | 0/0 m0 FP21 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| X4_sine_lead_portamento | 300 | 0/0 m0 FP26 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| X5_808_bassline_40_60Hz | 300 | 0/0 m0 FP32 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| X6_soprano_closed_vowel_band_edge | 300 | 0/0 m0 FP7 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| X7_plateaued_ring_under_music_from_t0 | 1000 | 0/3 m3 FP4 [–] | 3/3 m0 FP0 [251/252/253] P3 | 3/3 m0 FP0 [251/252/253] P3 c3 | 3/3 m0 FP0 [251/252/253] P3 | 3/3 m0 FP0 [251/252/253] P3 | 3/3 m0 FP0 [247/249/252] P3 | 3/3 m0 FP0 [250/251/252] P3 |
| X8_slow_ring_midband_under_chords | 1000 | 3/3 m0 FP11 [10648] | 3/3 m0 FP0 [650/1948/2099] F1 | 3/3 m0 FP0 [650/1948/2099] F1 c3 | 3/3 m0 FP0 [650/1948/2099] F1 | 3/3 m0 FP0 [650/1948/2099] F1 | 3/3 m0 FP0 [1802/1950/3000] F0 | 3/3 m0 FP0 [2448/2652/3250] F0 |
| X9_ring_rta_midpoint_525Hz_speech | 300 | 3/3 m0 FP95 [151] | 3/3 m0 FP0 [1000/1152/1301] F0 | 3/3 m0 FP0 [1000/1152/1301] F0 c3 | 3/3 m0 FP0 [1000/1152/1301] F0 | 3/3 m0 FP0 [1000/1152/1301] F0 | 3/3 m0 FP0 [899/1001/1051] F0 | 3/3 m0 FP0 [652/652/1200] F0 |
| X10_two_rings_exact_octave | 300 | 6/6 m0 FP0 [224] | 6/6 m0 FP0 [98/102/150] P3 | 6/6 m0 FP0 [98/125/150] P3 c6 | 6/6 m0 FP0 [98/102/150] P3 | 6/6 m0 FP0 [98/102/150] P3 | 6/6 m0 FP0 [99/126/153] P3 | 6/6 m0 FP0 [98/100/151] P3 |
| X11_amp_clipped_howl_minus12dBFS | 300 | 3/3 m0 FP2 [152] | 0/3 m3 FP0 [–] F0 | 0/3 m3 FP0 [–] F0 c0 | 0/3 m3 FP0 [–] F0 | 0/3 m3 FP0 [–] F0 | 0/3 m3 FP0 [–] F0 | 1/3 m2 FP0 [99/99/99] F1 |
| X12a_master_drop20_raise_channel | 300 | 6/6 m0 FP5 [253] | 6/6 m0 FP0 [149/225/253] P3 | 3/3 m0 FP0 [251/252/253] P3 c3 | 6/6 m0 FP0 [149/225/253] P3 | 6/6 m0 FP0 [149/225/253] P3 | 6/6 m0 FP0 [148/200/252] P3 | 6/6 m0 FP0 [147/201/252] P3 |
| X12b_master_drop20_raise_busmaster | 300 | 6/6 m0 FP4 [251] | 6/6 m0 FP0 [99/176/253] P3 | 3/3 m0 FP0 [251/252/253] P3 c3 | 6/6 m0 FP0 [99/176/253] P3 | 6/6 m0 FP0 [99/176/253] P3 | 6/6 m0 FP0 [100/174/252] P3 | 6/6 m0 FP0 [98/176/252] P3 |
| X13_decay16_jazz_lav_ring | 600 | 3/3 m0 FP0 [352] | 3/3 m0 FP0 [98/199/250] P3 | 3/3 m0 FP0 [98/199/250] P3 c6 | 3/3 m0 FP0 [98/199/250] P3 | 3/3 m0 FP0 [98/199/250] P3 | 3/3 m0 FP0 [151/152/452] P3 | 3/3 m0 FP0 [152/248/252] P3 |
| X14_peakhold_loud_band_wedge_ring | 300 | 3/3 m0 FP0 [250] | 3/3 m0 FP0 [147/200/202] P3 | 3/3 m0 FP0 [147/200/202] P3 c6 | 3/3 m0 FP0 [147/200/202] P3 | 3/3 m0 FP0 [147/200/202] P3 | 3/3 m0 FP0 [200/201/201] P3 | 3/3 m0 FP0 [148/152/200] P3 |
| X15_kick_bass_unison_55Hz | 300 | 0/0 m0 FP41 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| X16_wedge_ring_315Hz_loud_band | 300 | 3/3 m0 FP0 [101] | 3/3 m0 FP0 [98/101/103] P3 | 3/3 m0 FP0 [98/101/103] P3 c3 | 3/3 m0 FP0 [98/101/103] P3 | 3/3 m0 FP0 [98/101/103] P3 | 3/3 m0 FP0 [100/100/102] P3 | 3/3 m0 FP0 [99/148/150] P3 |
| X17_ring_122Hz_acoustic_guitar_body | 600 | 3/3 m0 FP18 [148] | 0/3 m3 FP0 [–] F0 | 0/3 m3 FP0 [–] F0 c0 | 3/3 m0 FP0 [97/103/348] P3 | 3/3 m0 FP0 [97/103/348] P3 | 0/3 m3 FP0 [–] F0 | 0/3 m3 FP0 [–] F0 |
| X18_applause_crowd_30s | 300 | 0/0 m0 FP35 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| X19_handheld_ring_stalls_and_hops | 300 | 3/3 m0 FP54 [252] | 3/3 m0 FP0 [200/250/497] F2 | 3/3 m0 FP0 [200/250/497] F2 c3 | 3/3 m0 FP0 [200/250/497] F2 | 3/3 m0 FP0 [200/250/497] F2 | 3/3 m0 FP0 [252/499/602] F1 | 3/3 m0 FP0 [98/100/101] P3 |
| X20_mains_hum_and_hvac_whine | 300 | 0/0 m0 FP55 [–] | 0/0 m0 FP0 [–] P3 | – | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 | 0/0 m0 FP0 [–] P3 |
| X21_reverberant_area_mic_slow_ring | 600 | 0/3 m3 FP8 [–] | 2/3 m1 FP0 [399/526/652] F1 | 2/3 m1 FP0 [399/526/652] F1 c2 | 2/3 m1 FP0 [399/526/652] F1 | 2/3 m1 FP0 [399/526/652] F1 | 1/3 m2 FP0 [501/501/501] F1 | 3/3 m0 FP0 [452/502/1102] F2 |
| X22_kick_mic_sub_ring_65Hz | 1000 | 3/3 m0 FP4 [103] | 0/3 m3 FP0 [–] F0 | 0/3 m3 FP0 [–] F0 c0 | 0/3 m3 FP0 [–] F0 | 3/3 m0 FP0 [351/648/700] P3 | 0/3 m3 FP0 [–] F0 | 0/3 m3 FP0 [–] F0 |
| X23_ringout_quiet_room_two_modes | 300 | 6/6 m0 FP0 [501] | 6/6 m0 FP0 e1 [-1152/175/250] P3 | 8/8 m0 FP0 e1 [97/174/250] P3 c9 | 6/6 m0 FP0 e1 [-1152/175/250] P3 | 6/6 m0 FP0 e1 [-1152/175/250] P3 | 6/6 m0 FP0 e3 [-1147/-450/201] P3 | 6/6 m0 FP0 e2 [-1151/100/148] P3 |
| **baseline totals** | | | ev 117 TP 108 miss 9 FP 948; pass 12/61 | | | | | |
| **auto/open totals** | | | ev 117 TP 107 miss 10 FP 0; pass 51/61 | | | | | |
| **auto/closed totals** | | | ev 118 TP 108 miss 10 FP 0; pass 21/32 | | | | | |
| **ringout/open totals** | | | ev 117 TP 110 miss 7 FP 0; pass 52/61 | | | | | |
| **auto+lf/open totals** | | | ev 117 TP 113 miss 4 FP 20; pass 51/61 | | | | | |
| **holdout4-6 totals** | | | ev 117 TP 106 miss 11 FP 0; pass 52/61 | | | | | |
| **holdout7-9 totals** | | | ev 117 TP 109 miss 8 FP 0; pass 49/61 | | | | | |

"auto" = each scenario in its declared mode (S6b, S19, M2, X23 ring_out with the probe fed the scenario's own master steps, as
cfs.ring_out now does; all others watch). "all-ringout" forces mode='ringout' (100 Hz window; probe where steps exist).
"auto+LF" = `lf_feedback_possible=1` (40 Hz window) everywhere — what the opt-in buys (X17 122 Hz 97–348 ms, X22 65 Hz
350–700 ms, S12 unchanged) and costs on LF-heavy programme *without* the arm-time pref fixes: S23b (RTA gain +24 dB → 808
sub-bass pinned at the 0.0 clip flag → CLIP lane, 19 FP), S19 40 Hz driven mode 1 FP; i.e. LF opt-in is for kick/bass-cab
ring-outs, not for watch over electronic music. Modes summary (seeds 1-3): watch/open identical to auto/open (FP 0);
ringout/open FP 0, X17 found (in its 100 Hz window). EARLY: X23 1 (probe, −1152 ms). TAIL: S15 19 (decay 16 s display of a
killed ring re-detected — in closed loop these would be wasted deepens; arm must force decay 0.25 [A §2]). HARM 0 (S2c/S9
clip partials are family-vetoed or below narrowness).

**Analyser sweep** (seed 1, all 61, auto): default/u_slow(attack_k 1)/bq/skirt_n2/skirt_steep/RMS/flat_beds/decay_fast/
release_law_240: **FP 0** each, TP 36/39, pass 55/55/55/55/53/51/55/55/55; decay_4s and release_law_17: 1 FP each (X16 loud
band: a vocal line held prominent by the slow display release) — the prefs the arm step must force [A §2, L §0].

**Latency distribution** (auto/open, 107 TPs, ms after the corpus's visibility instant t_prom): min −1152 (probe EARLY), p25 101,
median 152, p75 252, p90 650, p95 1000, max 2099; ≤150 ms: 46, ≤300: 90, ≤600: 95, ≤1000: 102; 96/107 within the scenario's
own budget. Closed loop: median 175, same tail. By lane (first detection, seed 1): growth 14 scenarios (98–300 ms), clip 4
(M1, X14, X16, S2c: 98–200 ms), sustained@arm 5 (S2a/b, X7, X12a/b: 250 ms = sustain_frames), sustained 2 (S13, X8),
probe/EARLY 1 (X23).

**Cost per frame** (Python 3.13, M-series Mac, 100 bands, `time.perf_counter` around `feed`): mean 126 µs (silent room) –
207 µs (X7 dense music), p95 ≤ 260 µs, max 343 µs; old detector 50–58 µs. O(bands) for prominence/narrowness/steps/clusters
+ O(peaks × 30) cluster look-ups for the family test. 0.4 % of one core at 20 fps.

## 4. Failure analysis (every non-pass in auto/open, seeds 1-3; what it would take)

* **X11 amp-clipped howl at −12 dBFS (miss 3/3; hold-out seeds: 1/3 caught at 99–248 ms).** 150 dB/s from a −28 dBFS
  programme seed to a −12 plateau is 16 dB = two frames: by the step definition an *arrival*; it then carries H2–H5 (acoustic
  clip) = family; prominence 19–24 in a −33 dBFS mix; below the −6 clip lane. Passively it is a loud odd-harmonic note that
  appeared. `clip_level_db: −13` catches it 3/3 at 150 ms and produces 4 FP on X16's −13 dBFS vocal lines (measured) — the
  examiner placed it exactly under any sane clip guard [C §3 "P1 impostor below the clip flag"]. Real fix: ring_out probe
  (it is loop-gain dependent) or the operator; in watch a −3 dB "tier-B" cut on loud ambiguous lines [L §4.3] is a policy
  choice I did not take because it is the X16 false positive by construction.
* **X17 122 Hz, X22 65 Hz (miss, by design of the watch window).** Both are `lf_optin` scenarios [C §7.11]; with
  `lf_feedback_possible=1` X17 is caught 3/3 at 97–348 ms (budget 600) and X22 3/3 at 350–700 ms (budget 1000); in ringout
  mode X17 is inside the 100 Hz window and caught. The harness does not pass the opt-in, so they count as misses here.
* **X9 lav ring at the RTA midpoint under speech (3/3 found, 1.0–1.3 s vs 300 ms).** The ring sits at speech H4 with speech
  H2/H3 two bands either side: single-band prominence < 12 and narrowness < 10 until the syllable gaps; every syllable is an
  arrival on the ring's own bands (restart); growth then needs 3 clean frames. Baseline "found" it at 151 ms on 2 seeds with
  32 FP/seed on the same speech. Faster requires trusting cluster prominence at 8–10 dB inside speech formants — I did not.
* **X8 slow ring under organ chords (3/3, 650/1948/2099 vs 1000), S13 3 dB/s (747/999/1602 vs 1000), X21 reverberant
  (2/3, 399–652 vs 600; seed 2 plateaus at prominence 9–11 between choir partials and is never a qualifying line).**
  Moderate-prominence plateau lines wait 600 ms by design and threshold flicker (hats, chord partials at ±1–2 bands) restarts
  young tracks; X8 additionally coincides with chord partials (E4×5, C4 family) for whole 2.4 s chords. These are the cases
  the corpus gives 1 s budgets; the detector is 0–1 s late on the unlucky seeds. A per-band slow baseline (P5 in [N]) would
  not help (the ring IS the baseline); only the probe or P12-style "outlasts the chord change" logic would, at more state.
* **S3 seeds 2/3 (602 ms), S11a seed 1 (698 ms), X19 seed 1 (497 ms).** S3: a crash cymbal lands on the frame the ring
  becomes visible and masks it for 450 ms; detection is 100 ms after it re-emerges. S11a: ring 1's realised rate is 9 dB/s
  at 13–16 dB prominence → growth needs 0.65 s for a 6 dB rise (rise threshold is what keeps expressive swells out).
  X19: after the 11 dB sag the regrowth window restarts; the hop (+125 c) starts a new track (new budget line in closed
  loop, as the corpus warns; `merge_adjacent_bands` absorbs it when the hop stays within one GEQ band).
* Hold-out seeds 4-9: no new FP after the two fixes they prompted (portamento arrivals ≤ 3 frames; wander 0.35); remaining
  misses/lates are the same scenarios. RMS preset: X8 3.6 s late (lower floor → more coincident partials qualify).
* Things the corpus cannot test that the design depends on [C §7]: the X32's real LF skirts/rise (the growth window scales
  with 1/Δf, not with the simulator's τ_a; the step test needs arrivals ≤ 3 frames, true for any analyser above ~150 Hz);
  RTA prefs forced at arm (decay 0.25, peak-hold off, gain known) — S15/S16/S23 show what happens otherwise and the frozen
  guard only covers peak-hold.

## 5. The override is redundant (proof) and what each old key now means

`override_prominence_db`/`override_persistence_frames` are parsed and validated but read nowhere in the decision
(`grep override_ src/x32mcp/detector.py` → config/docs only; `test_deprecated_override_keys_still_parse` sets it to 0 and the
60 dB plateaued ring is still emitted). Everything the override bought on the corpus [C §6: S2a/b/c, M1, X14, half of
X11/X12] is caught by SUSTAINED (S2a/b, X7, X12a/b at 250 ms), CLIP (S2c, M1, X14, X16 at 100–200 ms) or GROWTH; everything it
cost (+114 FP: whistles, flutes, organ, sine lead, 808, bells, vocal H2) is rejected by step memory / family / wander / window.
`growth_max_db_per_s` likewise (no rate cap; validated only). `w_*`, `growth_ref_db_per_s`, `confidence_threshold`: reported
confidence only (threshold still separates emitted from not-emitted for the dashboard and the existing tests).
`min_level_db`: still the qualification gate, default −90 in code / −80 in yaml; `cfs._calibrate_floor` still raises it (I left
that path untouched; recommendation: point the calibration at `sustain_min_level_db`, the only lane where level is evidence,
and make it per-band — [N] P5). All other pre-existing keys keep their exact meaning.

## 6. Config keys added (device.yaml `detector:`, all with defaults in `DetectorConfig` so the repo's current yaml loads)

narrow_db 10 · window_lo_hz 160 · ringout_window_lo_hz 100 · lf_window_lo_hz 40 · window_hi_hz 12500 · lf_feedback_possible 0 ·
harmonic_presence_db 6 · harmonic_rel_db 18 · harmonic_tol_bands 0.5 · family_min_partials 2 · parent_excess_low_db 8 ·
parent_excess_high_db 2 · single_partial_rel_db 15 · single_partial_tol_bands 0.35 · single_partial_comove_db 1.5 ·
family_veto_frac 0.4 · family_window_frames 12 · family_lifetime_frac 0.5 · centroid_wander_bands 0.35 · step_db 9 ·
step_continue_frac 0.3 · step_max_frames 3 · step_release_db 3 · comove_frames 6 · comove_db 5 · history_frames 8 · gap_frames 2 ·
growth_min_frames 3 · growth_lf_periods 4 · growth_rise_db 6 · growth_step_share 0.7 · growth_late_share 0.25 · growth_min_steps 3 ·
growth_step_min_share 0.5 · sustain_frames 6 · sustain_strong_prominence_db 18 · sustain_moderate_frames 12 · sustain_drop_db 3 ·
sustain_min_level_db −45 · clip_level_db −6 · frozen_eps_db 0.002 · ref_lo_band 25 · ref_hi_band 85 · probe_excess_db 2 ·
probe_window_s 1.2 · probe_confirmations 2 · mode watch. Changed value: min_level_db −45 → −80 (its −45 role moved to
sustain_min_level_db). `from_dict` now also accepts str-typed fields (`mode`).

## 7. Tests changed (tests/test_detector.py) and why; results

* `SyntheticRta.add_note(..., partials=((10,−6),(16,−10)))`: notes now carry H2/H3 like the file's own melody generator. A
  musical note has a harmonic family [L §2.1]; the old family-less, 80 dB/s dB-linear fade-in-then-hold "note" is physically the
  signature of a loop with 0.4 dB excess on a 5 ms path and was rejected only by the onset guard that also rejected real howls.
  `test_held_note_not_detected` keeps its assertion on the realistic note; new `test_family_less_ramp_is_a_ring` asserts the
  family-less variant IS reported by the growth lane within 6 frames.
* `test_config_from_descriptor`: dropped `w_p + w_s < threshold` (it asserted the bug); asserts the new keys/window/mode instead.
* `test_vibrato_rejected_with_longer_persistence`: the first assertion documented a flaw (default config detects vibrato); now
  asserts the default config rejects it too; persistence 6 still catches the ring ≤ 9 frames (unchanged).
* `_plateaued_ring` fixture: the ring band gets ±0.3 dB deterministic breathing + ±0.05 dB jitter and the floor ±1 dB noise. A
  bit-identical level frame after frame is the RTA peak-hold display [L §0, A §2, corpus S16] and is refused on purpose
  (new `test_frozen_display_is_not_a_plateau`); real plateaus wander ±0.2–0.5 dB [A §4.3 v, C §2.1].
  `test_an_established_plateaued_howl_is_caught`: slope tolerance 0.5 → 3 dB/s for the breathing; additionally asserts reason
  `sustained@arm`, no growth, emitted ≤ 300 ms after arm.
* `test_the_override_needs_real_prominence_not_just_patience`: now models what its docstring says (a held vocal note: instant
  onset + H2/H3, 15 dB, 5.5 s → 0 detections) and keeps the onset-less family-less −70 dBFS variant silent via the level floor.
* `test_override_is_disabled_by_zero` → `test_deprecated_override_keys_still_parse` (override 0 changes nothing; ring caught).
Unchanged and passing: clean music 0, ring ≤ 6 frames after crossing with slope ≈ 15 ± 3, two rings, noisy ring ≤ 12,
plateau+transient 0, candidates/reset, all NotchController tests, e2e first cut = (3, 22, −3).
Results: `tests/test_detector.py tests/test_rtasim.py tests/test_detector_corpus.py` 43 passed (2 baseline-dump tests
deselected per CORPUS §1); `tests/integration/test_cfs.py tests/integration/test_server_tools.py -k "cfs or ring or watch"`
24 passed; whole suite (minus TCP) 936 passed, 1 pre-existing failure `test_settings_defaults_and_env` (asserts the checkout
directory is named `x32-mcp`; fails identically on wt/corpus-critic).

## 8. cfs.py: nothing had to change; what I changed anyway (+10 lines, src/x32mcp/cfs.py:891, 1197-1203, 1397)

All 24 CFS integration tests pass with cfs.py untouched (default mode 'watch', no probe). To give ring_out its wider window and
the active probe: `_open_session` sets `cfg.mode` from `CfsMode`; the RAISE loop calls `ses.det.note_gain_step(step_done,
ses.last_ts)` after each successful master write; the session report gains `detector: {mode, window_hz, lf_feedback_possible}`
[L §3.4 "the report must print the window"]. Not done (belongs to the arm/RTA-prefs work stream, [A §2]): forcing
`/-prefs/rta/decay=0.25, peakhold=OFF, gain` at arm and restoring at disarm; deriving `window_lo_hz` from open-channel HPFs;
an `lf_feedback_possible` tool argument.

## 9. The brief's four questions, as this design answers them

1. **Can a harmonic-structure test replace growth? Latency?** It replaces growth *as a gate*: nothing requires growth any
   more, and the family/pair test (single frame, judged over a 6-frame co-movement window) is what rejects notes. It cannot
   *confirm* feedback below ~20 dB prominence [A §4.3] and it is blind to family-less programme (whistle, flute, organ 8',
   sine lead, 808, bells) — those are rejected by the arrival (step) memory, centroid wander, decay and the window, not by
   harmonics. Growth is kept as the fast positive lane with no rate ceiling: median latency 152 ms, 100–250 ms for every
   ring that actually ramps in view (S9 115 dB/s, M1 200, X16 240 included), 250 ms (= sustain_frames) for rings already
   plateaued at arm, 600 ms + for 12–18 dB plateaued lines. The binding latency is qualification (masking), not the test.
2. **Weighted sum or predicates?** Predicates. The sum let 30 dB of bass prominence buy missing physics and made a region
   unreachable; here each lane is sufficient, each veto is physical, each threshold is set from filter skirts / harmonic
   offsets / loop growth law and can be unit-tested alone, and `Detection.reasons` says which facts justified the cut. The
   sum survives only as a monotone dashboard number.
3. **Frequency window: prior or hack?** A legitimate prior *per source class and per mode*, a hack as a constant. Watch
   default 160 Hz–12.5 kHz (vocal/lav/lectern mics into tops/wedges), ring_out 100 Hz (no programme, missing an LF mode is the
   failure that matters), 40 Hz only on explicit `lf_feedback_possible` (kick/tom/bass-cab/acoustic pickup on a bus reaching
   subs) — with the measured cost that LF programme under mis-set RTA gain then false-triggers the clip lane. The bounds
   come from mic far-field roll-off × HPF (0.7·fc) × box cut-off at the bottom and mic HF roll-off at the top [L §3].
4. **Uncertain ⇒ ?** Mode-dependent and built in: in watch the detector never cuts on moderate evidence before 600 ms of
   stable, non-decaying, family-free, onset-free line (and never below −45 dBFS without growth), publishes the candidate
   with its reasons immediately (`candidates`, confidence < 0.7) for the human, and cuts at once on growth/clip; in ring_out
   the same uncertainty is resolved actively — the server's own +step is the probe and an over-responding line is cut before
   it runs away (X23: 1.15 s early), while stationary lines (S19 driven mode, organ programme) respond ≤ 1 dB/dB and are left
   alone. What I did not build: the −1 dB reverse probe / back-off-and-see on budget exhaustion ([N], [L §4.3]) — that is a
   cfs policy change, not a detector one.
