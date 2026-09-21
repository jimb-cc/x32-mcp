# disc-predicates — CFS² feedback discriminator as explicit physical predicates

Worktree `…/scratchpad/worktrees/disc-predicates` (branch `wt/disc-predicates`, from `wt/corpus-critic` @ 1be8603).
Code: `src/x32mcp/detector.py` (FeedbackDetector rewritten; NotchController unchanged), `src/x32mcp/cfs.py:891-892,1194-1197`
(mode + probe hook-up), `device.yaml` `detector:` (new keys, legacy keys kept), `tests/test_detector.py` (4 assertions changed,
listed in §8), `tests/test_detector_predicates.py` (15 new per-predicate tests). Metrics: `reports/disc-predicates/metrics.json`
(all 8 runs), `final/tables.md`, `sweeps/*.log`, `variants/`. Citations: [L §x] loop-physics brief, [A §x] analyser brief,
[C §x] CORPUS.md.

## 0. Headline (corpus of 61 scenarios × seeds 1-3, harness exactly as CORPUS.md §1; ground truth = 12 dB visibility)

| run | scen pass | events | TP | miss | **FP** | EARLY | TAIL | HARM | cuts | lat p50 / p75 / p90 / max ms | ≤300 ms | ≤budget |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline current detector, open (corpus-critic) | 12/61 | 117 | 108 | 9 | **948** | 9 | 21 | 143 | – | – | – | – |
| baseline, closed loop (32 fb scen.) | 10/32 | 105 | 96 | 9 | **286** | | | | 334 | | | |
| **watch, open** | 48/61 | 117 | 105 | 12 | **0** | 2 | 0 | 0 | – | 202 / 301 / 702 / 4252 | 77/105 | 86/105 |
| watch, closed | 18/32 | 118 | 106 | 12 | **0** | 1 | 0 | 0 | 108 | 202 / 398 / 853 / 4252 | 71/106 | 80/106 |
| **ringout, open** (probe fed on the 4 ring_out scenarios) | 50/61 | 117 | 111 | 6 | **0** | 2 | 0 | 0 | – | 203 / 302 / 702 / 4252 | 79/111 | 92/111 |
| ringout, closed | 20/32 | 118 | 112 | 6 | **0** | 1 | 0 | 0 | 114 | 203 / 450 / 802 / 4252 | 73/112 | 86/112 |
| watch + lf_feedback_possible, open | 50/61 | 117 | 111 | 6 | **0** | 2 | 0 | 0 | – | as ringout | | |
| ringout + lf, open / closed | 50/61 / 20/32 | | 111/112 | 6 | **0** | | | | 114 | as ringout | | |

Zero false positives in every mode, open and closed loop, on all 61 scenarios (music, notes, controls, prefs traps, LF programme —
including with the LF window opened to 40 Hz); 0 TAIL (no wasted deepening on the 16 s RTA-decay scenario), 0 HARM (a clipped howl's
own harmonics are never cut). The remaining failures are latency on slow or programme-seeded rings and 4 misses, analysed one by one in §5;
growth is not required anywhere (an established howl at arm is emitted at 200 ms; S2a/b/c, X7, X12a). Cost: mean 239 µs, p99 768 µs,
max 958 µs per frame (pure Python 3.13, busy music frames; §9).

Where the 300 ms criterion is missed the cause is physical and stated: a passive detector that refuses to cut a family-less steady
NOTE must see ≥ 6 dB of a ring's own rise (or find it loud, or find it established at arm, or probe it); a ring growing at R dB/s
delivers that 6/R s after it becomes trackable, so 7-13 dB/s rings (S10, S11a, M3 seeds) land at 300-600 ms and 1.5-5 dB/s rings
(X8, S13) at seconds. Hold-out seeds 4-6 and the analyser sweeps (§6) keep FP at 0 for attack_k 1.0, BQ, skirt 2, decay 4 s, RMS and
1.5× noise; residual FPs appear only on hold-out seed 5 of the saw-pad swell (1) and under peak-hold (the pref that must be forced off).

## 1. Decision logic in full (`src/x32mcp/detector.py`; defaults = `device.yaml detector:`; K1 = 5 frames = 250 ms)

### 1.1 Per frame, whole spectrum — `feed()` :1163, `_lines()` :669, `_cm()` :626   (O(bands·k), k = 3)
* `prom[i]` = level − median(±3 neighbours) (unchanged definition).
* **Line** = local maximum with prom ≥ `track_prominence_db` 6. Cluster = peak + a neighbour within `cluster_merge_db` 6 dB (a tone
  between two centres reads −3/−3 dB [A §3]; edge vibrato alternates the louder band [A §4.4]). Per line: cluster power, **cluster
  prominence** (cluster power over the median of the six bands ±2..±4 beyond it — the ground truth's "cluster" measure), **narrowness**
  = peak − max(the two bands just outside the cluster's skirts), and the **centroid** = power-weighted position of peak±1 *after removing
  the local floor* (sub-band frequency to ~±0.1 band; the raw centroid of a weak line is dragged by the bed and broke tracks).
* **Common-mode reference** (`_cm`): spectra of the last 96 frames are kept; the common-mode change between two frames is the MEDIAN of
  the per-band changes of bands 25..85 that sit ≥ `ref_signal_db` 6 above the frame's 10th-percentile floor at both instants, excluding
  the line's own ±4. A fader/autogain/master step moves every signal band equally; a chord, crash or one growing line moves a minority
  [A §5]. (A level-percentile reference was tried first: the median jumps on crashes/chords, a low percentile sits on the analyser noise
  floor under RMS detection and let S6's +9 dB ramp through as "growth" — 14 FP in the RMS sweep. The median-of-changes form is 0 FP in
  both.) Rises are net of max(0, Δcm − `cm_deadband_db` 2): a mix breathes ±1-2 dB; a fader shove that could fake 6 dB is larger.
* **Baseline** `base[i]`: asymmetric one-pole low tracker per band (fast down 0.15/frame, slow up 0.002/frame after a 1 s fast seed).
  P5 excess = peak − base. Replaces `min_level_db` (−45 dBFS fitted to one room [HANDOVER §4b(3)]): S10/X9/X23 lines at −50…−78 dBFS
  are now reachable, C0 rumble and X20 hum/whine are "already there".
* **Tracking**: lines ↔ tracks by centroid within `track_match_bands` 0.6 (a ring moves < 0.05 band/frame; tracks younger than K1 pay
  `track_young_penalty` 0.3 so an established track wins a tie against a fresh speech-partial track); unmatched tracks coast
  `coast_frames` 8 (a hat/crash over a quiet ring must not erase it — S3 s2, S13); a track whose centroid range over the last
  `drift_window_frames` 10 exceeds `drift_max_bands` 1.0 is **re-born** at its new frequency with no history (`_rebirth` :955): glide,
  scoop, melody step, creeping capture of a neighbouring line, or a hand-held ring hopping to the next loop candidate [L §2.3] — everything
  (K1 stability, rise, at-arm) must be re-earned there, and `glided_in` is recorded.

### 1.2 Per track — predicates (`_extend` :820, `_family` :702, `_judge` :1037)

| # | predicate (feedback ⇒ true) | threshold | physics / justification | corpus evidence |
|---|---|---|---|---|
| P1 NARROW | cluster prominence ≥ `prominence_db` **12** AND narrowness ≥ `narrow_db` **8** | 12 / 8 dB | a sinusoid on a 1/10-oct bank clears 24-60 dB at ±2 (2nd..5th-order skirts, [A §3]); formant humps, cymbal wash, guitar-body humps read 0-6 (unit test `test_p1_broad_hump`). The lead's 15 dB was wrong against the corpus: narrowness is bounded by *prominence over the louder of the two ±2 bands*, so 15 demands ~17 dB prominence and cost 150-300 ms on S10/S3/X7 (X7 s3 sat at nar 8.6-9.8 for 300 ms with a chord partial two bands away); 10 → 8 fixed X7/X21 with no FP change. | §5; iter2→iter3, iter35 |
| P2 NO FAMILY | partials H2..H5 at +10, +15.85, +20, +23.2 bands (±`partial_tol_bands` **0.6**) *present as peaks* (own prom ≥ `partial_prominence_db` **6**) whose LEVEL is within `partial_rel_db` **18** of the candidate and which **co-move** (Δlevel over K1 within `comove_tol_db` **3**); family = ≥ `family_min_partials` **2**, or a lone exact H2 within `h2_pair_rel_db` **12** *born within `co_onset_frames` 2* of the line; vetoed also if the line is H2/H3 of a lower peak owning another partial, or H4/H5 of a base ≥ `subharm_hi_margin_db` **3** louder; a LOUD line keeps no veto of its *own* family; a family first seen after the line rose `late_partials_rise_db` **12** is distortion; MUSICAL when vetoed ≥ `family_veto_fraction` **40 %** of the last 20 frames (and not clean for all of the last K1) or on most of the last K1 | see left | presence-as-peak not energy (the bed has energy everywhere), not ratios (timbres vary) — lead P2; offsets exact in bands [A §4.1]; ±0.6 not ±1: true partials sit at the exact offset ± centroid error, ±1 gave 25-35 % coincidental vetoes on S12's ring under guitar chords; level-relative 18 dB: instrument partials run 0…−15 dB re H1 [L §2.1], and a peak 30 dB under a strong ring is not its partial (prominence-relative was tried: flickers inside chords where a partial's neighbours are partials → S6 organ pair missed under RMS); co-movement [L §5 P1]: X19 s1's ring grew 25 dB through a held vocal note's H4 slot — vetoed for 2 s until partners were required to share the envelope; the co-born H2 pair catches 8'+4' organ (S6/S6b, which the ≥2-partial rule alone let through: 29+17 FP in iter1) while two rings an exact octave apart (X10, born 300 ms apart) are both cut [L §2.3: independent candidates do not co-onset]; H4/H5 sub-harmonic only under a louder base: testing them under any base cost X7/M1/X11 0.3-1.6 s and an X8 miss through coincidences, testing none let a speech F1-formant partial through on hold-out seed 6 (X20); the LOUD escape replaces the lead's fixed −6 dBFS: X11's amp-clipped howl sits at −12 with odd partials the mic hears [L §1.4(1), §2.2] — being *somebody's* harmonic never escapes, which is what removes the 143 HARM detections (S2c/S9 H3/H5) of the baseline | X19, S6, S12, X10, S2c, S9, X11 |
| P3 STABLE | centroid range over the last K1 frames ≤ 2·`centroid_tol_bands` = **±0.25 band (±30 c)** and the run is ≥ K1 frames old | 0.25 | a ring's centroid sd is < 0.05 band [A §4.3 v]; vibrato ≥ ±30 c straddling an edge swings the centroid ±0.3-0.8 band at 5-7 Hz [A §4.4]; scoops/portamento/808 glides walk. The lead's ±0.5 let a 2.5 band/s glide hold "stable" for K1; 0.25 changed nothing on the corpus and rejects glides ≥ 3 bands/s outright (slower glides are re-born by the drift rule once per second and must re-earn RISE). | S4a/b, S18, X6, X3, X4 all 0 FP |
| P4 SUSTAINED | not decaying: LS slope over ≤ 12 frames > −`decay_db_per_s` **3** dB/s or drop < `decay_drop_db` 1.5; and sag under the K2-window max ≤ `sag_db` 4 | 3 dB/s | plucked/struck notes decay 3-15 dB/s [L §2.1]; the RTA *release* of a ring already killed by a cut falls at release_law/decay ≥ 3.75 dB/s even at decay 16 s [A §2] — with the lead's 4 dB/s S15 kept 21 TAIL re-detections (each a wasted deepen); 3 dB/s → 0 TAIL. Wander of a real plateau (±0.3-0.5 dB multi-sine [C §2.1]) never sustains −3 dB/s over 0.6 s. | S15, S24, S5 |
| COMMON-MODE | |Δcm| ≥ `common_mode_db` 6 over K2 with the line within `common_mode_tol_db` 2 of it ⇒ MUSICAL (that frame only) | 6 / 2 | programme riding a fader/autogain [A §5]; 3 dB (lead's implied) mis-fired on rings under music because the old level-median jittered ±3 dB; non-latching because a crescendo can coincide with 12 frames of a ring's growth | S6, S23a, X12a |
| CO-GROWTH | ≥ `cogrowth_lines` **2** *other* lines currently risen ≥ `cogrowth_rise_db` 3 while I rise ⇒ MUSICAL (that frame) | 2 / 3 | rings start alone; a pad/fade-in/crescendo lifts several lines together [A §5]; two simultaneous rings (S11, X10) plus me stay allowed. 2 dB was tried and flagged a ring whenever a mix breathed. | S21 (seed 4 FP removed) |
| P5 NEW / AT-ARM | excess ≥ `baseline_excess_db` **10**; a line born within `arm_frames` 3 cannot be new and instead carries AT-ARM evidence iff over its first K1 frames median level ≥ `arm_line_min_level_db` **−40** and median prominence ≥ `strong_prominence_db` **18**, it has been present ≥ `arm_presence` 80 % of frames and steady within `arm_range_db` 6, and (level ≥ `arm_fast_level_db` **−20** or `arm_confirm_s` **0.8 s** have passed since arm) | see left | the lead's P5 as written ("baseline seeded in the first second") would have swallowed the established howl it wants emitted at 250 ms (S2a/X7 are *in* the baseline); so at-arm lines are judged on what they looked like at arm. −40: hum bars/HVAC whines sit at −45…−60 and are also families (X20); a compressor-limited howl sits −30…−10 [L §1.4(2)]. This is the one level threshold in the design and [C §7.10] is right that X7 (−30) vs X20's whine (−50) is a corpus regularity — hence the two guards that ARE physical: an established howl is continuous and flat (a kick pattern present at arm pumps and re-triggers: S17 hold-out seeds gave 4 FP before `arm_presence/arm_range`), and a quiet one must outlast a note (an organ note sounding at arm is identical for as long as it lasts: S8a hold-out seed 5 FP at 250 ms before `arm_confirm_s`; X7 at −30 now lands at 800-850 ms inside its 1000 ms budget, S2a-c/X12a at −8…−12 at 200 ms). | S2a/b/c, X7, X12a ev1, X20, C0, S17, S8a |
| P6 WINDOW | centroid frequency in [`window_low_hz`, `window_high_hz` 12.5 k]: **160 Hz watch / 63 Hz ringout / 40 Hz with `lf_feedback_possible`** | | [L §3.4]: vocal-mic loops cannot ring below ~150 Hz (SM58 far-field −8 dB, HPF, no mic path to subs); ring_out has no programme by contract; kick/tom/acoustic pickups near subs ring at 40-120 Hz [L §3.2 e/f]. **Measured: the window is not what keeps LF programme out** — with the window at 40 Hz the corpus still gives 0 FP on S1/S7/S17/S19/X5/X15/S23 (all seeds) because the analyser-settle logic in RISE does; the window is a free prior and the reason X17/X22 are (by design) not cut in plain watch. cfs can derive the edge from the open mics' HPF (0.7·f_hpf) as the brief proposes; the detector takes `lf_feedback_possible` / `mode` as constructor arguments. | §4 LF rows |

### 1.3 Evidence (any one turns BASE into STRONG; none is required) — `_extend` :820-900, `_judge` :1080-1110, `_probe` :996
| evidence | rule | why |
|---|---|---|
| **RISE** | cluster level now − 20th-percentile of the *presence run's* settled levels, net of common mode beyond 2 dB, ≥ `rise_db` **6 dB** | 6 dB = a doubling of amplitude and 2.4 dB above the largest expressive swell/flutter any BASE-qualified programme line reached on the corpus (3.6 dB, X6 soprano; whistle 3.2; organ Leslie 3.1 — `reports/disc-predicates/scan.py`). 5 dB gave 1 FP (S22 speech). The 20th percentile, not the minimum: on a ±2 dB-fluttering whistle the minimum keeps falling and manufactured a 6.3 dB "rise" after 90 frames (S8c s2). |
| — analyser settle | the run's first ⌈1.5·k/(Δf·T) − 0.5⌉ frames are not scored (k = `analyser_rise_k` 1.0: 1 frame ≥ 300 Hz, 2 @ 200 Hz, 4 @ 100 Hz, 7 @ 63 Hz, 11 @ 39 Hz) | a band of width Δf cannot settle faster than ~1/Δf [L §1.6, A §1]: an instant bass onset renders as a 4-8-frame decelerating ramp = the M7 40/80 Hz mechanism. k = 1.0 is the critically-resolved bound (model U), twice the simulator's 0.5/Δf, so this is not fitted to the simulator's τ_a [C §7.1]; the sweep at attack_k 1.0 and BQ keeps 0 FP. Never 0 frames even at HF: the birth frame of a 20-100 ms acoustic attack (flute, bowed, sung [A §4.2]) must not seed the reference (tried: +3 FP). |
| — run restarts | (i) a > `onset_step_db` 4 dB single-frame jump when the line was not already climbing (two consecutive rising frames, or ≥ 4 contiguous settled samples climbing ≥ `onset_rising_db_per_s` 10 dB/s within 0.75 dB rms); (ii) re-acquisition after > K1 missed frames unless climbing; (iii) below 300 Hz: > `lf_onset_step_db` 6 dB within one settle window out of a window that moved < 2 dB, or a fall of `restart_drop_db` 6 dB under the run max; and there half of the rise must be older than one settle window (`rise_old`) | (i) a note re-struck at the same pitch, a syllable, a shout landing on a noise bump the tracker kept alive: a new source, not loop growth, which is continuous and loses at most one frame [L §1.3] (S8c, X4, X18, X20 FPs in iter1/iter24); (ii) X6/X16 hold-out/sweep FPs: a track coasting on noise re-acquired a different, louder source; (iii) S19's driven room mode and X15/S12's re-struck LF notes rise through τ_a over several frames with no single-frame jump [A §6]; genuine LF loops are long (≥ 15 ms) and low-excess, so they grow for seconds and keep 0.5·rise older than a settle window (X22 65 Hz caught at 550-750 ms of a 1000 ms budget, X17 122 Hz at 200-450 ms). |
| **LOUD** | peak ≥ `clip_db` −1 (the 0.0 clip flag) OR (peak ≥ `loud_line_db` **−10 dBFS** AND ≥ `loud_margin_db` **4** above every band outside its own ±3) — not below 160 Hz | limiter/clip plateau territory [L §1.4, §4.3 tier A]; the margin makes it relative to the mix (X16's −11 dBFS vocal partial in a −16 dBFS/band mix is not "loud"; its −3 dBFS ring is). −10 not the lead's −6: M1/X16/X14 plateau at −3/−4, X12 at −12, and the unit-suite's held note at −14 dBFS must not be cut; −15 (tier B) also catches X11's −12 dBFS amp-clipped howl (variant, §7) at the price of cutting loud solo sine-like notes ≥ −15 dBFS — a policy choice left in config. LF: kick/808 routinely put one band far above everything; LF loops are slow, so LOUD carries no information there [L §3.2 e]. |
| **AT-ARM** | see P5 | the M7 "60 dB line at 0.50 for 15 s" case: emitted 200 ms after arm. |
| **PROBE** (ring_out) | after `note_gain_step(Δ, ts)` a line steady (range ≤ `probe_steady_db` 3) before the step whose median over [ts+0.15 s, ts+1.4 s] rose ≥ Δ + `probe_over_db` **2** while the common mode moved ≤ Δ + 0.5, on `probe_min_hits` **2** steps | regenerative gain 1/(1−g): +3 dB/dB at −4 dB, +6.5 at −2 [L §1.5, A §0.3]; programme at the pre-fader tap moves 0…+1 dB/dB [L §0]. Two hits, not one: S19's room mode coincided with a bass note on one step (3 FP in ringout+LF before). A line answering ≤ Δ+1 dB with no hit ever is STATIONARY (hum/HVAC/playback): reported, never cut; one flat step after a hit does not condemn (X23's 625 Hz mode answered +3.4, −0.5, +4.7, +2.3, +5.9 and was lost to a latched flag in the first version). A TRACK-class line with 2 hits, stable and in window is emitted pre-emptively (klass PROBE) — what a human ring-out does with a line that swells on every nudge. On X23 the 625 Hz mode is emitted 1.25 s **before** it crosses threshold (EARLY), both modes within 150 ms otherwise; no probe FP on S6b/M2/S19. |
| **GROWTH** (optional, OFF) | clean dB-linear climb ≥ 10 dB/s over 8 settled frames with ≥ `growth_rise_db` rise; default `growth_rise_db` = `rise_db` disables it | the lead's "growth as upgrade": at 4 dB it buys 100-200 ms on 10-15 dB/s rings (S10 298 max, S11a med 174, unit-test ring +4 frames) with 0 FP on seeds 1-3, but fired on a 10 dB/s saw-pad partial on hold-out seed 4 (programme margin: whistle swell 6.2 dB/s at 3.1 dB) — so it ships off. |
| **CONFIRMED** | emitted before and still BASE | keeps a surviving ring re-emitting once per `cooldown_s` (deepening) after its rise is history; a killed ring fails P4 instead. |

### 1.4 Classes and emission (`_judge` :1095-1110, `feed` :1215-1250)
```
MUSICAL  := family ∨ common-mode ∨ co-growth                      (per frame, not latched)
BASE     := qualified(P1) ∧ ¬MUSICAL ∧ P3 ∧ P4 ∧ (P5-new ∨ born-at-arm) ∧ P6 ∧ run ≥ K1
STRONG   := BASE ∧ (RISE ∨ LOUD ∨ AT-ARM ∨ PROBE ∨ CONFIRMED)      → Detection (klass STRONG, reasons = predicates + evidence)
MODERATE := BASE only                                              → published in .candidates (cfs.candidate event), NOT cut
STATIONARY := BASE ∧ answered a probe step 1 dB/dB and never over-responded → reported, never cut
watch:   emit STRONG.        ringout: emit STRONG; emit PROBE (TRACK line, 2 hits, stable, in window) pre-emptively;
         MODERATE after K2 only with ringout_emit_moderate=true (verified programme-free room).
Re-emission: same track once per cooldown_s while STRONG (hop → new track → adjacent GEQ band merges in NotchController).
```
MODERATE is deliberately never cut on passive evidence in watch: a dead-steady family-less note (organ flue X1, sine lead X4, whistle
X3/S8c, flute X2, crowd whistles S20/X18) and a compressor-limited ring that was never seen to start, is not loud and was not there at arm
are the same observation [A §4.3, C §7.10]; the human gets the candidate at K1 = 250 ms via `cfs.candidate`. The lead's "STRONG on
prominence ≥ 18 alone" and "MODERATE emits after K2" were both wrong against the corpus: 226 FP (K2) and every whistle/organ/sine note
(prominence 25-33) respectively; `ringout_emit_moderate=true` on the four ring_out scenarios still costs 9 FP on M2's quiet organ (the
M7 situation: music during ring-out) and 1 on S19, while every ring_out ring is already caught by RISE/PROBE — so it ships off.

`confidence` = threshold + margins (prominence, narrowness, run length, evidence) for STRONG, < threshold otherwise: monotone, display
only; the harness and cfs see ≥ 0.7 exactly on emitted lines. `Detection` gained `reasons: tuple[str,...]`, `klass`,
`centroid_band`, `narrow_db`, `rise_db`, `excess_db`; `freq_hz` is the interpolated centroid frequency (a 525 Hz midpoint ring reports
525 Hz, not 507 or 544), which is what NotchController.band_for_freq should be fed. `Candidate.to_dict()` adds class/reasons/
centroid/rise/glided/probe fields for the dashboard. Optional API used by cfs: `FeedbackDetector(cfg, band_hz, mode=, 
lf_feedback_possible=)`, `note_gain_step(delta_db, ts)` (wired at cfs.py:1197 after each master write), `note_cut(...)` (accepted,
reserved for a VERIFY-aware re-emission policy).
## 2. Per-scenario results (seeds 1-3). Cell = TP/miss/FP [eN EARLY, tN TAIL, hN HARM] lat min/med/max ms, P = pass all seeds, Fk = k seeds passed; closed loop: TP/miss/FP, cuts, lat med/max

| scenario | ev | bud | baseline (current detector) TP/miss/FP | watch open TP/miss/FP lat min/med/max | watch closed TP/miss/FP cuts lat med/max | ringout open | watch+LF open |
|---|---|---|---|---|---|---|---|
| C0_silent_room | 0 | 300 | 0/0/0 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| C1_music_bed_drums | 0 | 300 | 0/0/0 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| S1_bass_under_quiet_music | 0 | 300 | 0/0/32 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| S2a_established_ring_8k | 3 | 300 | 3/0/0 | 3/0/0 199/200/202 P | 3/0/0 c3 200/202 P | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P |
| S2b_established_ring_8k_steep | 3 | 300 | 3/0/0 | 3/0/0 199/200/202 P | 3/0/0 c3 200/202 P | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P |
| S2c_established_clipped_2k4 | 3 | 300 | 3/0/0 h96 | 3/0/0 199/200/202 P | 3/0/0 c3 200/202 P | 3/0/0 199/200/202 P | 3/0/0 199/200/202 P |
| S3_ring_during_music | 3 | 300 | 3/0/5 | 3/0/0 199/398/702 F1 | 3/0/0 c3 398/702 F1 | 3/0/0 199/398/702 F1 | 3/0/0 199/398/702 F1 |
| S4a_vocal_vibrato | 0 | 300 | 0/0/40 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| S4b_vocal_vibrato_band_edge | 0 | 300 | 0/0/31 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| S5_guitar_note_decays_to_sine | 0 | 300 | 0/0/13 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| S6_master_ramp_feedback_watch | 0 | 300 | 0/0/7 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| S6b_ringout_steps_latent_loop | 3 | 300 | 3/0/2 | 3/0/0 200/250/300 P | 3/0/0 c3 250/300 P | 3/0/0 200/250/300 P | 3/0/0 200/250/300 P |
| S7_808_sub_bassline | 0 | 300 | 0/0/19 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| S8a_organ_melody | 0 | 300 | 0/0/13 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| S8b_flute_held_note | 0 | 300 | 0/0/20 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| S8c_whistle | 0 | 300 | 0/0/28 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| S9_clipped_howl_fast | 3 | 300 | 3/0/0 h47 | 3/0/0 248/252/253 P | 3/0/0 c3 252/253 P | 3/0/0 248/252/253 P | 3/0/0 248/252/253 P |
| S10_ring_between_bands | 3 | 300 | 3/0/0 | 3/0/0 298/301/502 F1 | 3/0/0 c3 301/502 F1 | 3/0/0 298/301/502 F1 | 3/0/0 298/301/502 F1 |
| S11a_two_rings | 6 | 300 | 6/0/0 | 6/0/0 149/301/598 F1 | 6/0/0 c6 326/1001 F0 | 6/0/0 149/301/598 F1 | 6/0/0 149/301/598 F1 |
| S11b_two_rings_near_octave | 6 | 300 | 6/0/0 | 6/0/0 147/225/302 F2 | 6/0/0 c6 450/1253 F0 | 6/0/0 147/225/302 F2 | 6/0/0 147/225/302 F2 |
| S12_acoustic_guitar_wedge_ring_196Hz | 3 | 600 | 3/0/5 | 3/0/0 500/551/553 P | 3/0/0 c3 551/553 P | 3/0/0 500/551/553 P | 3/0/0 500/551/553 P |
| S13_slow_ring_3dB_s | 3 | 1000 | 0/3/0 | 1/2/0 1048/1048/1048 F0 | 1/2/0 c1 1048/1048 F0 | 1/2/0 1048/1048/1048 F0 | 1/2/0 1048/1048/1048 F0 |
| S14_ring_masked_by_cymbal | 3 | 300 | 3/0/0 | 3/0/0 198/198/200 P | 3/0/0 c3 198/200 P | 3/0/0 198/198/200 P | 3/0/0 198/198/200 P |
| S15_long_rta_decay_tails | 3 | 300 | 3/0/1 t21 | 3/0/0 149/152/202 P | 3/0/0 c3 152/202 P | 3/0/0 149/152/202 P | 3/0/0 149/152/202 P |
| S16_peak_hold_on | 0 | 300 | 0/0/12 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| S17_kick_pattern | 0 | 300 | 0/0/0 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| S18_vibrato_on_band_edge | 0 | 300 | 0/0/9 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| S19_driven_room_mode | 0 | 300 | 0/0/40 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| S20_song_start_stop_crowd | 0 | 300 | 0/0/6 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| S21_synth_pad_swell | 0 | 300 | 0/0/66 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| S22_speech_ringing_then_feedback | 3 | 300 | 3/0/98 | 3/0/0 48/102/148 P | 3/0/0 c3 102/148 P | 3/0/0 48/102/148 P | 3/0/0 48/102/148 P |
| S23a_autogain_drift | 0 | 300 | 0/0/18 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| S23b_gain_offset_clip | 0 | 300 | 0/0/16 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| S24_bells_triangle_glock | 0 | 300 | 0/0/8 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| M1_loud_band_wedge_ring | 3 | 300 | 3/0/0 | 3/0/0 248/251/300 P | 3/0/0 c3 251/300 P | 3/0/0 248/251/300 P | 3/0/0 248/251/300 P |
| M2_quiet_music_ringout_two_modes | 6 | 300 | 6/0/8 | 6/0/0 -0/48/52 P | 11/0/0 c11 147/953 F0 | 6/0/0 -0/48/52 P | 6/0/0 -0/48/52 P |
| M3_jazz_trio_lav_ring_400Hz | 3 | 300 | 3/0/0 | 3/0/0 198/302/550 F1 | 3/0/0 c3 302/550 F1 | 3/0/0 198/302/550 F1 | 3/0/0 198/302/550 F1 |
| X1_organ_held_notes | 0 | 300 | 0/0/9 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| X2_flute_held_vibrato | 0 | 300 | 0/0/20 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| X3_whistle_held_drift | 0 | 300 | 0/0/21 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| X4_sine_lead_portamento | 0 | 300 | 0/0/26 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| X5_808_bassline_40_60Hz | 0 | 300 | 0/0/32 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| X6_soprano_closed_vowel_band_edge | 0 | 300 | 0/0/7 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| X7_plateaued_ring_under_music_from_t0 | 3 | 1000 | 0/3/4 | 3/0/0 801/802/853 P | 3/0/0 c3 802/853 P | 3/0/0 801/802/853 P | 3/0/0 801/802/853 P |
| X8_slow_ring_midband_under_chords | 3 | 1000 | 3/0/11 | 3/0/0 1850/3098/4252 F0 | 3/0/0 c3 3098/4252 F0 | 3/0/0 1850/3098/4252 F0 | 3/0/0 1850/3098/4252 F0 |
| X9_ring_rta_midpoint_525Hz_speech | 3 | 300 | 3/0/95 | 3/0/0 1053/1402/1549 F0 | 3/0/0 c3 1402/1549 F0 | 3/0/0 1053/1402/1549 F0 | 3/0/0 1053/1402/1549 F0 |
| X10_two_rings_exact_octave | 6 | 300 | 6/0/0 | 6/0/0 99/148/150 P | 6/0/0 c6 148/150 P | 6/0/0 99/148/150 P | 6/0/0 99/148/150 P |
| X11_amp_clipped_howl_minus12dBFS | 3 | 300 | 3/0/2 | 0/3/0 – F0 | 0/3/0 c0 – F0 | 0/3/0 – F0 | 0/3/0 – F0 |
| X12a_master_drop20_raise_channel | 6 | 300 | 6/0/5 | 6/0/0 199/225/252 P | 3/0/0 c3 200/202 P | 6/0/0 199/225/252 P | 6/0/0 199/225/252 P |
| X12b_master_drop20_raise_busmaster | 6 | 300 | 6/0/4 | 6/0/0 199/201/249 P | 3/0/0 c3 200/202 P | 6/0/0 199/201/249 P | 6/0/0 199/201/249 P |
| X13_decay16_jazz_lav_ring | 3 | 600 | 3/0/0 | 3/0/0 98/250/350 P | 3/0/0 c3 250/350 P | 3/0/0 98/250/350 P | 3/0/0 98/250/350 P |
| X14_peakhold_loud_band_wedge_ring | 3 | 300 | 3/0/0 | 3/0/0 248/250/298 P | 3/0/0 c4 250/298 P | 3/0/0 248/250/298 P | 3/0/0 248/250/298 P |
| X15_kick_bass_unison_55Hz | 0 | 300 | 0/0/41 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| X16_wedge_ring_315Hz_loud_band | 3 | 300 | 3/0/0 | 3/0/0 201/203/203 P | 3/0/0 c3 203/203 P | 3/0/0 201/203/203 P | 3/0/0 201/203/203 P |
| X17_ring_122Hz_acoustic_guitar_body | 3 | 600 | 3/0/18 | 0/3/0 – F0 | 0/3/0 c0 – F0 | 3/0/0 202/252/450 P | 3/0/0 202/252/450 P |
| X18_applause_crowd_30s | 0 | 300 | 0/0/35 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| X19_handheld_ring_stalls_and_hops | 3 | 300 | 3/0/54 | 3/0/0 200/201/348 F2 | 3/0/0 c3 201/348 F2 | 3/0/0 200/201/348 F2 | 3/0/0 200/201/348 F2 |
| X20_mains_hum_and_hvac_whine | 0 | 300 | 0/0/55 | 0/0/0 – P | – | 0/0/0 – P | 0/0/0 – P |
| X21_reverberant_area_mic_slow_ring | 3 | 600 | 0/3/8 | 2/1/0 399/474/550 F2 | 2/1/0 c2 474/550 F2 | 2/1/0 399/474/550 F2 | 2/1/0 399/474/550 F2 |
| X22_kick_mic_sub_ring_65Hz | 3 | 1000 | 3/0/4 | 0/3/0 – F0 | 0/3/0 c0 – F0 | 3/0/0 548/648/751 P | 3/0/0 548/648/751 P |
| X23_ringout_quiet_room_two_modes | 6 | 300 | 6/0/0 | 6/0/0 e2 -1252/102/150 P | 8/0/0 c9 124/197 P | 6/0/0 e2 -1252/102/150 P | 6/0/0 e2 -1252/102/150 P |
| **watch_open** | 117 | | | pass 48/61: TP 105 miss 12 FP 0 early 2 tail 0 harm 0 cuts 0 | | | |
| **watch_closed** | 118 | | | pass 18/32: TP 106 miss 12 FP 0 early 1 tail 0 harm 0 cuts 108 | | | |
| **ringout_open** | 117 | | | pass 50/61: TP 111 miss 6 FP 0 early 2 tail 0 harm 0 cuts 0 | | | |
| **ringout_closed** | 118 | | | pass 20/32: TP 112 miss 6 FP 0 early 1 tail 0 harm 0 cuts 114 | | | |
| **watch_lf_open** | 117 | | | pass 50/61: TP 111 miss 6 FP 0 early 2 tail 0 harm 0 cuts 0 | | | |
| **watch_lf_closed** | 118 | | | pass 20/32: TP 112 miss 6 FP 0 early 1 tail 0 harm 0 cuts 114 | | | |
| **ringout_lf_open** | 117 | | | pass 50/61: TP 111 miss 6 FP 0 early 2 tail 0 harm 0 cuts 0 | | | |
| **ringout_lf_closed** | 118 | | | pass 20/32: TP 112 miss 6 FP 0 early 1 tail 0 harm 0 cuts 114 | | | |
| **baseline current detector (open)** | 117 | | pass 12/61: TP 108 miss 9 FP 948 harm 143 tail 21 | | | | |

(watch+LF and ringout+LF closed loop = ringout closed: 20/32 pass, 112/6/0, 114 cuts. Full tables: `final/tables.md`; JSON with every
detection, verdict, reasons-free summary and latency: `metrics.json` = `final/metrics.json`.)

Closed loop uses NotchController (−3 dB steps to −9, budget 6, 1-frame actuation). It spends 108-114 cuts for 106-112 TP + re-emissions
(baseline: 334 cuts, 286 of them FP, and in S12/X8/X9/X17 the FP cuts pre-empted the ring). Closed-loop latency is worse only where a first
cut lowers a *second* ring's excess through the bell skirt (S11a/b, M2 re-emergence: 1001-1253 ms max): the second ring then grows
slower and RISE takes proportionally longer — physics, not detector state.

## 3. Latency distribution (open loop, first TP per event, relative to the 12 dB visibility frame)

| mode | n | min | p50 | p75 | p90 | p95 | max | ≤300 ms | ≤ scenario budget |
|---|---|---|---|---|---|---|---|---|---|
| watch | 105 | −1252 | 202 | 301 | 702 | 1053 | 4252 | 77 (73 %) | 86 (82 %) |
| ringout / watch+LF | 111 | −1252 | 203 | 302 | 702 | 1053 | 4252 | 79 (71 %) | 92 (83 %) |
| watch closed | 106 | −1 | 202 | 398 | 853 | 1253 | 4252 | 71 | 80 |

Established-at-arm (S2a/b/c, X12a ev1) 199-202 ms = K1; fast rings (S9, M1, X14, X16, S22, X10, S14, X12b, S15) 48-253 ms; probe
(X23) −1252 ms (pre-threshold) / 102-150; 10-25 dB/s rings 200-600 ms (6/R after visibility); 1.4-5 dB/s rings seconds.

## 4. Robustness: hold-out seeds and analyser sweeps [C §5, §7.9] (watch open unless noted; `sweeps/*.log`)

| variant | pass | TP | miss | FP | notes |
|---|---|---|---|---|---|
| seeds 4,5,6 (hold-out) watch / watch+LF | 44/61 / 45/61 | 105 / 111 | 12 / 6 | **1 / 2** | S21 saw-pad swell seed 5: F3 fundamental swelling 20 dB/s whose partials are not resolvable peaks inside the dense F2-C3-F3 voicing and whose sibling lines are still inside their LF settle windows (no co-growth yet) → one FP at 8.70 s (watch) + its C3 sibling (LF). X21 misses 2/3 (see §5). |
| attack_k 1.0 (analyser twice as slow) | 48 | 104 | 13 | 0 | X21 −1 |
| attack_model BQ (3× faster LF) | 48 | 105 | 12 | 0 | |
| skirt_order 2 (−12/−24 dB skirts) | 46 | 103 | 14 | 0 | X21 all missed (prominence ceiling 24 dB, chord partials leak) |
| skirt_order 5 | 45 | 105 | 12 | 1 | S16 (peak-hold 2 s scenario): frozen plateaus ratchet a held note up 6.0 dB in cluster power |
| decay_s 4 | 49 | 103 | 14 | 0 | S13 all missed (release masks nothing; slower visibility) |
| det RMS | 47 | 107 | 10 | 0 | (was 16 FP with a level-percentile reference; fixed by the median-of-changes common mode) |
| noise_sd_scale 1.5 | 46 | 105 | 12 | 0 | |
| peak_hold_s 1 | 44 | 99 | 18 | 4 | the pref both briefs say must be forced OFF at arm [L §0, A §2]: X7 plateau invisible, held notes ratchet. Not defended against; cfs must write peakhold OFF/decay 0.25 and read back [A §2]. |

## 5. Failure analysis (default config, seeds 1-3)

**Misses**
* **X11 amp-clipped howl at −12 dBFS (3/3, watch and ringout).** In its −26 dBFS/band mix the fundamental is invisible below the bed
  until two frames before its plateau (150 dB/s), so no rise is observable; its acoustic H3/H5 (−14/−20 dB) co-onset with the visible
  part; −12 dBFS is 2 dB under the tier-A LOUD line. It is published as a MODERATE candidate at 250 ms. `loud_line_db: -15` (tier B,
  [L §4.3]) catches it at 249/252/252 ms with still 0 FP on the corpus (variant run) — but would also cut the unit suite's held note at
  −14 dBFS and any loud solo sine-like note ≥ −15 dBFS that stands 4 dB above the whole mix; that is the watch-mode asymmetry decision
  (Q4) and is left to configuration. In ring_out the probe resolves it in one dwell.
* **S13 slow ring 3 dB/s to −38 dBFS (2/3).** Trackable from −42, plateau −37: 5 dB of observable rise < 6; 17 dB prominent, −38 dBFS,
  never seen to start by more than 5 dB = a soft flute note to any passive test. Seed 2 caught at 1048 ms (48 ms over budget).
  MODERATE candidate published; ring_out+probe or `ringout_emit_moderate` (programme-free room) cuts it.
* **X21 reverberant 642 Hz seed 1 (watch/ringout), seeds 1+? under sweeps.** The ring is seeded at −31 by the choir partial whose track
  it inherits; its 11 dB of growth happens while that track is still family-vetoed (the choir note's partials co-move until the ring
  dominates), and at the −20 plateau the inherited run's low quantile is the choir partial's own level → rise 2.6. Seeds 2/3: 399/550 ms.
* **X17 122 Hz, X22 65 Hz in plain watch (by design):** below the 160 Hz watch window. With `lf_feedback_possible` or in ring_out:
  X17 202/252/450 (budget 600), X22 548/648/751 (budget 1000), 0 LF FP.

**Over budget (detected, late)** — all "6/R": S10 13 dB/s realisation on seed 1 (502), S11a 7 dB/s realisation (598), S11b 302/352,
M3 s3 550 (ring re-born when it captured a piano partial's track, +settle), S3 s2 702 (crash masks the onset for 6 frames; the pre-crash
climb had 3 settled samples, one short of "already climbing", so the run restarts on re-acquisition — the rule that removed the X6/X16/X18
sweep FPs), X19 s3 348 (stall/sag then regrow: rise re-earned from the trough), X9 1.05-1.55 s (ring exactly between two RTA bands
under male speech whose H4/H5 land on it every syllable: centroid pulled ±0.3, competing short tracks, intermittent sub-harmonic vetoes
— the corpus's designated nasty case; baseline found it at −200/151/902 with 95 FP), X8 1.9-4.3 s (1.4-5 dB/s under organ+piano: track
repeatedly interrupted, budget 1000; baseline 10-12.6 s with 11 FP).

**What growth is NOT doing:** of the 99 first detections in watch mode (TP + credited EARLY), 18 are AT-ARM (9 of them also LOUD),
9 LOUD alone, 72 RISE — an observed doubling of the line's own amplitude after the analyser settled, at *any* rate (3 dB/s and
240 dB/s rings are both in that 72). No rate window exists anywhere; the 6..60 dB/s window, the onset guard and the 25 dB override
are gone. In ring_out the X23 modes add PROBE.

## 6. Where the lead's numbers were wrong against the corpus (evidence in `iter*/` tables)
1. P1 narrow 15 dB → 8: narrowness ≤ prominence − (bed bump at ±2); 15 delayed S10/S3/X7 by 150-300 ms; humps still read 0-6.
2. P2 tolerance ±1 band → ±0.6 and partials level-relative + co-moving: ±1 with presence-only gave 25-40 % coincidental veto rates on
   rings under chords (S12, X8) and let the organ 8'+4' pair through (needs the co-born H2 rule).
3. P2 escape "level ≥ −6 dBFS" → LOUD (relative): X11 at −12; and the escape must not cover "is somebody's harmonic" (S2c/S9 H3 at −12).
4. P3 ±0.5 → ±0.25 band; "centroid moved > 1 band ⇒ MUSICAL (latched)" → re-birth: latching killed M3 s3 and S12 when a ring captured
   a neighbouring note's creeping track.
5. P4 decay 4 → 3 dB/s (S15 TAIL ×21 at 3.75 dB/s release).
6. P5 "baseline seeded over the first second" contradicts "established ring emitted at 250 ms"; resolved as AT-ARM evidence with
   presence/steadiness/outlast-a-note guards (S17/S8a hold-out FPs).
7. Common-mode via a spectrum level statistic (median or low percentile) fails under RMS/sparse spectra (floor bands do not follow the
   fader: 14 FP on S6) and jitters ±3 dB under music; the median of per-band *changes* over signal bands is the robust form [A §5 m_t].
8. STRONG on "prominence ≥ 18" alone, MODERATE-emits-after-K2 (watch) and MODERATE-emits in ring_out: 100-226 FP (every whistle,
   organ, flute, sine lead; M2's organ during ring-out). Dropped / config-off.
9. Probe: one over-responding step is not enough (S19 bass note coincident with a step); "stationary" must not latch on one flat step.
10. Growth as upgrade: measurable benefit (100-200 ms on 10-15 dB/s rings) but the programme margin is too thin on hold-out seeds; off.

## 7. Variants measured (`variants/`)
| variant | effect |
|---|---|
| `loud_line_db: -15` (tier B) | X11 3/3 at ≤ 252 ms; corpus FP still 0; 49/61 pass; cuts loud solo sines ≥ −15 dBFS (unit held-note test at −14 would fire) |
| `growth_rise_db: 4` | S10 3/3 ≤ 298, S11a med 174, unit ring +4 frames; 0 FP seeds 1-3; 1 FP hold-out seed 4 (S21) |
| `ringout_emit_moderate: true` (4 ring_out scenarios) | +0 TP (all already caught), +9 FP M2 organ, +1 S19 → only for a verified programme-free room |
| ring_out on all 61 scenarios with K2 emission (iter16) | 226 FP — the contract "no programme" is doing all the work |

## 8. Test changes (tests/test_detector.py) — every changed assertion, with the physical reason
1. `test_config_from_descriptor`: dropped `assert w_prominence + w_persistence < confidence_threshold` — it *required* a plateaued line to
   be unreachable without growth (the M7 false negative as a test). Replaced by assertions on the new keys (mode/window/K1/rise/narrow)
   and that a bogus mode is rejected. Weights remain loadable legacy keys.
2. `test_ring_detected_quickly`: `0 <= i - t0 <= 6` → `<= 1 + ceil(rise_db / 15 dB/s / 0.05)` (= 9; observed 8): a 15 dB/s ring supplies
   the 6 dB of observed rise in 8 frames after it becomes trackable, and in this stream a melody note lifts the neighbourhood so the line
   is trackable only 4 frames before its 12 dB crossing; rings ≥ 20 dB/s (wedge/tops norm [L §1.3]) land ≤ 6 on the corpus, and
   `growth_rise_db: 4` restores +4 here. Added: reasons must contain narrow, no_family and rise|growth. slope ≈ 15 ± 3 still asserted.
3. `test_vibrato_rejected_with_longer_persistence`: the first assertion demanded a FALSE POSITIVE from the default config
   (`!= []`, "known limitation"); now both persistence settings must give `[]` (P3 rejects a line hopping a whole band every few frames;
   ±2 dB is not a 6 dB rise); the ring half is unchanged (≤ 9).
4. `test_override_is_disabled_by_zero` → `test_override_key_is_legacy_and_established_ring_is_always_caught`: the old test asserted that
   `override_prominence_db: 0` makes an established 60 dB line undetectable (restoring the M7 miss). There is no such mode any more; the
   key loads and is ignored; the line (−25 dBFS) is emitted with `established_at_arm` within arm_confirm_s.
Unchanged and passing: clean music → 0, held note (−14 dBFS, 20 dB, 5-frame ramp) → 0, plateau+transient → 0, two rings ≤ 6 frames,
noisy ring ≤ 12, established 60 dB howl caught with slope 0, modest 15 dB plateau NOT caught, candidates/reset, all NotchController
tests, e2e first cut at GEQ 22. New file tests/test_detector_predicates.py: 15 tests, one per predicate/evidence (broad hump; split
line = one candidate with interpolated f; note-with-family vs bare line; co-born octave pair vs two rings an octave apart; clipped howl +
H3/H5 never cut; glide; edge vibrato; decaying line not re-emitted; +10 dB common-mode ramp; hum/whine/established ring at arm; LF
onset through a 185/92 ms one-pole at 41/78 Hz with LF enabled → 0; 65 Hz ring needs LF/ringout; probe over-response vs 1 dB/dB whine
→ STATIONARY; Detection contract; cost < 5 ms/frame). Suite: 949 passed, 1 pre-existing failure unrelated (test_settings_defaults_and_env
asserts the checkout directory is named x32-mcp; fails in every worktree), TCP tests deselected. Integration `-k "cfs or ring or watch"`:
24 passed with cfs now constructing the detector with `mode=` and calling `note_gain_step` after each master step.

## 9. Cost per frame (Python 3.13, this sandbox, `time.perf_counter` around `feed`, 7 scenarios × 1 seed = 2040 frames)
mean 239 µs, median 204 µs, p99 768 µs, max 958 µs; quiet room 117 µs mean; densest (X7: organ+piano+bass+ring) 564 µs mean.
O(bands·k) for prominences/lines + O(lines²) partial matching (≤ ~25 lines) + O(61) per rising track for the common-mode median.
No allocation growth: per-track deques bounded (64), spectrum store 96 frames × 100 floats.

## 10. Config keys added to device.yaml `detector:` (all with defaults in DetectorConfig; legacy keys kept and marked)
track_prominence_db 6, narrow_db 8, cluster_merge_db 6, track_match_bands 0.6, coast_frames 8, (track_young_penalty 0.3),
partial_prominence_db 6, partial_rel_db 18, partial_tol_bands 0.6, family_min_partials 2, family_veto_fraction 0.4, comove_tol_db 3,
late_partials_rise_db 12, subharm_hi_margin_db 3, (h2_pair_rel_db 12, co_onset_frames 2, family_window_frames 20, cogrowth_lines 2,
cogrowth_rise_db 3), stable_frames 5, confirm_frames 12, centroid_tol_bands 0.25, drift_max_bands 1.0 (drift_window_frames 10),
decay_db_per_s 3, decay_drop_db 1.5, sag_db 4, (decay_window_frames 12, common_mode_db 6, common_mode_tol_db 2, ref_band_lo/hi 25/85,
ref_floor_quantile 0.1, ref_signal_db 6, ref_min_bands 6), cm_deadband_db 2, baseline_excess_db 10, arm_frames 3,
arm_line_min_level_db −40, strong_prominence_db 18, arm_fast_level_db −20, arm_confirm_s 0.8, arm_range_db 6 (arm_range_frames 16),
arm_presence 0.8, window_low_hz_watch 160, window_low_hz_ringout 63, window_low_hz_lf 40, window_high_hz 12500, lf_feedback_possible
false, rise_db 6, analyser_rise_k 1.0, onset_step_db 4 (onset_flat_db 1, lf_onset_step_db 6, lf_onset_flat_db 2, onset_rising_db_per_s
10, onset_rising_resid_db 0.75, rise_quantile 0.2, restart_drop_db 6), growth_rise_db 6 (= off; growth_frames 8, growth_min_db_per_s 10,
growth_resid_db 1), loud_line_db −10, loud_margin_db 4, clip_db −1, probe_over_db 2, probe_min_hits 2, probe_window_s 1.4
(probe_settle_s 0.15, probe_early_hits 2, probe_steady_db 3), ringout_emit_moderate false, mode watch, level_gate_db −128 (off).
Keys in parentheses have defaults in code and are not spelled out in the yaml. `min_level_db`, `floor_*`, `persistence_frames`,
`growth_min/ref/max`, `monotonic_tolerance_db`, `weights`, `override_*` are loaded and unused (documented as legacy in the yaml).

## 11. The brief's four questions, as this design answers them
**Q1 — can a harmonic-structure test replace growth, and what does it cost in latency?** It replaces growth as the music/feedback
*separator* but cannot stand alone: presence-as-peak at exact partial offsets (±0.6 band, level within 18 dB, co-moving) disposes of
voice, bass, guitar, piano, saw pads, hum and speech in one frame, and of a clipped howl's own harmonics; it is blind to the family-less
programme class (organ flue, flute top register, whistle, sine leads, 808, bells, [A §4.3]) and it produces coincidental vetoes under
dense harmony unless partners are required to co-move. So the decision needs P3/P4/P5 and one piece of positive evidence. Latency is
then not set by the family test (single frame) but by K1 = 250 ms of stability plus, for a ring that is neither loud nor established,
the time to observe 6 dB of its own rise: ~0 extra for ≥ 25 dB/s, 6/R − (visibility lead) otherwise. Median 202 ms, 73 % ≤ 300 ms.

**Q2 — weighted sum or predicates?** Predicates. The sum let 30 dB of prominence buy missing growth (bass note) and made growth
mandatory (plateau unreachable); its weights could only be fitted jointly to data nobody has. Here every threshold is a physical
quantity with a stated origin (skirt depth, partial offsets, a semitone = 0.83 band, 1/Δf, a doubling = 6 dB, tier-A loudness), each
is unit-tested alone, the notch report carries `reasons`, and when the corpus showed a number wrong (§6) exactly one number moved.

**Q3 — is a 250 Hz-8 kHz window legitimate?** As physics it is a statement about one mic, one HPF and one PA [L §3.3]; kick/tom mics
into subs ring at 40-120 Hz, acoustic pickups at 90-250 Hz, condensers to 12 kHz. The design keeps a *session* window (160 Hz watch,
63 Hz ring_out, 40 Hz on `lf_feedback_possible`, 12.5 kHz top; cfs can derive the low edge from the open channels' HPF at 0.7·f_hpf)
but the measured result is that the window is not what stops the M7 false positives: with the window open to 40 Hz the corpus still
yields 0 FP on every bass/808/kick/room-mode/hum scenario, because rises are only counted after the band could have settled (1.5/Δf),
LF re-excitations restart the run, and LOUD is ignored below 160 Hz. The window is a free prior that costs X17/X22 in plain watch by
design and nothing else; it should be derived per session and printed in the report, not hard-coded at 250 Hz.

**Q4 — behaviour when uncertain; watch vs ring_out.** Uncertain = MODERATE (all predicates hold, no evidence). Watch: never cut on
it — publish the candidate at 250 ms for the human, cut only on STRONG; a wrong −3 dB in a show is audible, cumulative and spends one
of six budget lines, and passively a MODERATE line is more often a note than a howl. The one knob that moves this boundary is
`loud_line_db` (−10 tier A default; −15 tier B cuts loud ambiguous lines once — CONFIRMED then deepens only while the line stays
BASE, and a line that decays after the cut is never re-emitted). Ring_out: the server owns the gain, so uncertainty is resolved
actively, not by waiting — every +1 dB step is a probe (`note_gain_step`), two over-responses make STRONG (and emit a sub-threshold line
pre-emptively, X23 −1.25 s), a 1 dB/dB answer makes STATIONARY (never cut, reported); MODERATE-after-K2 is available for a verified
programme-free room but off by default because M7 ran music during ring-out and the corpus's M2 shows what that costs (9 FP).
What remains for cfs (outside this module): force RTA decay 0.25 / peakhold OFF / autogain OFF at arm with read-back [A §2] (the
peak-hold sweep is the one condition this detector does not defend against), derive the window from HPFs, and on an uncertain line in
ring_out prefer a −1 dB back-off probe over a cut [L §4.3].
