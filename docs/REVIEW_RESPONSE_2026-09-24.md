# Review response: the desk measurements of 2026-09-22/23

*Answer to `docs/REVIEW_REQUEST_2026-09-23.md`, written 2026-09-24. Reviewed: `main` @ `f214b44`, PR #14 `review/peq-sim` @
`7444c16`, PR #15 `review/rta-band-grid` @ `27cf55e`. No hardware: every number below was recomputed from the raw frame logs
in `docs/research/data/` or from the simulator, and is printed by a script named next to it. Nothing was pushed; the proposed
changes are local branches, listed in section 8 for approval or denial one by one.*

The request asks for each reading to be confirmed or disputed and for the marked decisions to be taken. Two of the six
readings do not survive the raw data, and one of them is the item marked highest consequence.

## 0. Verdicts and decisions at a glance

| Item | Reading in the request | Verdict | Decision |
|---|---|---|---|
| 1 | The Dual GEQ delivers a third of its slider depth | **Disputed.** The four readings are what a full-depth cut on ONE leg of a stereo strip reads through a mono-summed tap, to 0.1 dB | No realised-depth factor, no ladder change, no TruEQ detour. One two-minute desk test first (section 9, test 1) |
| 2 | The RTA bins are `20·2^(i/10)` Hz | **Confirmed** independently | Merge PR #15's product change with two follow-up commits. Do not re-pin or widen: the corpus keeps its grid until the corpus revision |
| 3 | Only `det` and `decay` reach the stream; `decay` is an attack time too | **Confirmed, with a catch**: every capture taken after the server wrote `det` = PEAK shows RMS ballistics. The arm-time PEAK write probably does not take | Check the `det` write on the desk first (test 2); re-scope the frozen-display machinery; state both laws in `DETECTOR.md`; gate v2 beside v1 |
| 4 | Settle rule k = 1, skirts order 5 to 2, "under PEAK the last decibel takes longer" | **Partly wrong.** The semitone sweep ran at decay 1.0 in the RMS state, not at PEAK / 0.25 | Re-run the sweep (script fixed) before cutting the corpus revision; the LF attack is a window filling, not a one-pole with k = 1 |
| 5 | K8 survives 3/3 under both actuators | **Confirmed**, 6/6. It is a policy-layer case the harness cannot run, plus one real product gap | Keep "a re-born track re-earns evidence". Merge PR #14 with the tier-B stand-in. Fix the bystander verdict in `tier_b_eligible` |
| 6 | Two test races; oscillator facts | **Confirmed** | Both race fixes applied as proposed. The insert question is already answered by item 1's own data |

## 1. The GEQ depth (disagree)

`python scripts/reanalyse_rta_logs.py geq`

The request reads 2.6 dB for a -6 slider and 4.1 dB for -12 as the graphic delivering 0.43x to 0.35x of its depth. The
measurement moved the 2k slider of **side A only** of a GEQ2 inserted on Main LR. Main LR is a stereo strip: side A
processes L, side B processes R (`fx_routing_scenes.md` §2.1, `provision.py:340`). The oscillator feeds L and R the same
tone. If the RTA tap on `main.st` reads the sum of the two legs, a cut of gain g on one leg reads `20·log10((1+g)/2)`:

| slider | tone | measured | one leg, summed tap | both legs |
|---|---|---|---|---|
| -6 | 2000 Hz | 2.6 dB | 2.49 dB | 6.00 dB |
| -12 | 2000 Hz | 4.1 dB | 4.07 dB | 12.00 dB |
| -12 | 1888 Hz | 3.8 dB | 3.70 dB | 9.30 dB |
| -12 | 2119 Hz | 3.6 dB | 3.70 dB | 9.30 dB |

The two centre readings have no free parameter. The two off-centre readings have one, the bell's Q, and the least-squares
value is 4.3, a true third-octave bell; rms residual over the four readings 0.09 dB. (The fit is flat: any Q from about
3.3 to 5.2 keeps every residual within 0.25 dB.) A power sum (the legs read separately
and averaged) gives 2.04 / 2.74 dB and does not fit. The "broad, shallow dip" is the sum compressing the dip, not the bell.

This is not a new idea. `REVIEW_REPORT.md` §2 C1 raised it from the M7 datum (a -15 cut read about -6 on programme; one
leg gives -4.6 and can never exceed -6.02, so that reading is consistent in kind rather than a match) and §8 item 5 asked
for exactly this experiment with both legs. The
session cut one leg and read the result as the GEQ's depth.

Why the Main LR PEQ read full depth: it is one EQ on the stereo strip and acts on both legs. Why a bus would too: a mix
bus is mono, side A is the whole bus.

Consequences if this reading is right, each the opposite of the request's:

* `NotchController`'s ladder delivers -3 / -6 / -9 today, because `main` writes both sides for the mains
  (`review/main-lr-stereo-geq`). What read 1.3 / 2.6 / 3.9 was M7's pre-fix behaviour: the left stack cut in full, the
  right not at all.
* A realised-depth factor of 0.35 to 0.43 in `render.py`, followed by "deepen faster" on the desk, would cut 2.5x deeper
  than intended on both stacks. This is the one decision in the request that could do damage, so it is refused until test 1.
* The corpus's closed-loop GEQ kill margins are not 2.5x optimistic. `CORPUS.md` §7.8's added paragraph should be withdrawn.
* The PEQ's advantage is what `PEQ_ACTUATOR_DESIGN.md` §2.2 says it is (placement and programme cost), not a factor in
  delivered attenuation. If Q is near 4.3, the design's own table puts a Q 6 -6 notch at 30 % MORE programme cost than a
  GEQ -3. §2.2 stays as written.

**Decisions.** (a) No depth factor; `note_cut`'s GEQ bracket stays `geq_q_min..geq_q_max` = 2 to 4.3. The four readings
barely constrain Q (about 3.3 to 5.2); test 6 in section 9 measures it. (b) Ladder and VERIFY thresholds unchanged.
The observation that a "held" verdict after a -3 is the normal outcome on a limiter-held ring with more excess than the
cut stands on its own and needs no depth argument. (c) Dual TruEQ: not before test 1. If the GEQ2 bell is ordinary there
is nothing for the TruEQ to fix. (d) §2.2 unchanged.

What would change my mind: both sliders at -12 reading about 4 dB (test 1). Then the graphic really is shallow and the
request's decisions (a) to (d) come back on the table as written.

## 2. PR #15, the band grid (agree with the reading, disagree with the test plan)

`python scripts/reanalyse_rta_logs.py grid`

**The reading holds.** Neighbour-difference estimator over the 60 tones at or above 300 Hz: zero at +0.319 band
(+0.032 oct) above the table centres; `20·2^(i/10)` sits at +0.342. By thirds of the range: +0.348, +0.309, +0.299. The
peak band is the nearest centre for 60/60 tones on `20·2^(i/10)`, 40/60 on the DOC table, 51/60 on the provisional
half-band fit. On-centre tones read -37.72 ± 0.08 dB (25 tones, 42 Hz to 9.5 kHz). The oscillator's frequency scale agrees with the PEQ's:
the 09-22 bell fit put the tone 0.034 oct under the band, and the grid step nearest 2.04 kHz is 2046 Hz, 0.033 oct above 2000.

**"The corpus is grid-relative" does not hold.** Scenes mix bin-anchored frequencies (`band_centre_hz()`) with
world-anchored ones (notes, rings written in Hz, GEQ centres). Moving the bins alone moves the first kind against the
second by 41 cents and changes what scenarios test. X17's loop is written to sit just under the B2 the guitar keeps
playing (121.3 Hz against 123.5); on PR #15 it sits above it (124.2 Hz). So the nine moved tests are not nine re-drawn seeds.

What the corpus reads when only the bins move (`watch_tag` / `ringout_tag`, open loop, seeds 1 to 3):

| corpus | grid | events | TP | miss | FP | scenarios passing all seeds |
|---|---|---|---|---|---|---|
| main (67) | table | 135 | 133 | 2 | **0** | 57 |
| main (67) | `20·2^(i/10)` | 135 | 130 | 5 | **3** | 52 |
| adversarial (68) | table | 120 | 79 | 41 | 170 | 29 |
| adversarial (68) | `20·2^(i/10)` | 120 | 75 | 45 | 168 | 26 |

And what the four pinned regressions read over 12 seeds instead of the 2 or 3 they are pinned on:

| scenario | pinned claim | table grid, 12 seeds | moved bins, 12 seeds |
|---|---|---|---|
| X11 amp-clipped howl -12 dBFS | caught ≤ 300 ms "on every seed" | 10/12 caught, 7 within 300 ms, worst 1252 ms | 9/12, 7 within 300 ms |
| AS03 amp-clipped howl -24 dBFS | 3/3 ≤ 300 ms | 9/12 | **5/12** |
| AP07 2 dB/s ring | 3/3, ≤ 1600 ms | 12/12, worst 2702 ms | 12/12, worst 2151 ms |
| AV02 flute soft attacks, seeds 2 and 3 | 0 FP | 7 FP over 12 seeds | 6 FP over 12 seeds |
| M1 loud band wedge ring | 3/3 ≤ 300 ms | 12/12 ≤ 203 ms | 12/12 ≤ 203 ms |
| X17 ring under guitar | (passes) | 12/12, 0 FP | 11/12, **6 FP** (243 Hz, after the howl plateaus) |

Two things follow. The pins were true of their seeds, not of the detector: X11 misses 2 of 12 seeds on the grid it was
tuned on. And "0 FP on the corpus" is the result on one realisation; a perturbed one reads 3.

**Decisions.**

* Confirm the reading. Merge the product change (`device.yaml`, `meters.py`, labels, scripts). It matters most for the PEQ:
  today's frequency labels are off by up to 0.065 oct (section 7.5), beyond the design's 0.055 worst case.
* Which X11 lane flips: `watch_tag` seed 3 misses on the moved bins; seeds 4 and 11 miss on both grids. Over 12 seeds the
  rate is 10/12 against 9/12. There is no lane to fix, there is a pin to retire.
* Re-pin or widen: **neither.** Re-pinning on the new draw repeats the mistake; widening hides it. In PR #15 the simulator
  keeps the grid it was built and baselined on (`rtasim.physics.RTA_BAND_HZ` defined locally, decoupled from
  `x32mcp.meters`). The simulator is a self-consistent world on any grid, because the harness hands the detector the same
  `band_hz`. All nine tests then pass unchanged. The grid moves in the corpus revision together with the measured analyser
  model and re-anchored scenarios, and that revision reports rates over 12 seeds, not pins on 3.
* Policy-test tones: make the expectations grid-relative (`HZ_1K = RTA_BAND_HZ[band_for_hz(1000.0)]`), done. Giving
  `SyntheticRta` a two-bin split is worth doing separately: today no integration test exercises frequency interpolation.
* One miss in the PR: `webui/index.html:97` still carries the old formula as the dashboard's default grid. Fixed.

## 3. Which prefs reach the stream (agree on the list; the detector is probably not running on PEAK)

`python scripts/reanalyse_rta_logs.py display floor`

**In the -97 state the display is one pole on power, and `decay` is its T20.** With τ = `decay` / ln 100, both directions:

| decay | τ | release measured | one pole predicts | attack rms error, frames 3 to 12 |
|---|---|---|---|---|
| 0.25 | 54 ms | 4.05 dB/frame | 4.00 | 0.06 dB |
| 1.00 | 217 ms | 0.97 | 1.00 | 0.06 dB |
| 4.00 | 869 ms | 0.25 | 0.25 | 0.07 dB |
| 16.0 | 3.47 s | 0.06 | 0.06 | not fitted (the tone is off before it settles) |

"20 / decay dB per second" is this law (10·log10(e)·ln 100 = 20), and "decay slows the attack too" is the same pole seen
from the other side. A pole on amplitude does not fit (0.7 to 1.0 dB at decay 4 and 16). Two footnotes: frames arrive
every 52.0 ms in all five logs, not 50, so rates per second are 4 % lower than rates per frame suggest (77 dB/s at 0.25);
and below about 300 Hz the band's own response is slower than the pole (2.7 dB/frame at 78 Hz, 3.5 at 156 Hz, against 4.0).

**In the -128 state the attack is instant.** The one attack captured in that state (`rta_peakhold_det`, decay 0.25) reads
-4.0, -0.1, 0.0 dB against its plateau. A 54 ms pole cannot be closer than 0.7 dB on its second frame, and in the -97
state at the same decay the second frame is 1.4 to 3.0 dB short. Release is 4 dB/frame in both. Instant attack with a
slow release is a peak detector; averaging both ways is an RMS detector. So the log's attribution is right: -128 is PEAK,
-97 is RMS.

**Which means the arm-time PEAK write is probably not taking.** Every capture made after the server or a script wrote
`det` = 1 shows the -97 floor and the averaging attack: the 09-22 readings through `get_rta` (item 6 of that log records
the -97 floor), the rise log, the release sweep, and the semitone sweep, whose script writes `det` 1 as its first act.
Only the first half of `rta_peakhold_det` is in the PEAK state. If this holds on the desk, `set_rta_source` reports
`detector_set_peak` and the detector has been reading RMS ballistics since M7. That is not dangerous (at decay 0.25 a
step is within 1 dB by the third frame, and levels agree within 0.7 dB), but every statement in `DETECTOR.md` that
assumes PEAK ballistics describes a state the desk was not in. One capture decides the attribution, so this is likely,
not proven. Test 2 settles it in a minute.

A correction to `CORPUS.md` §7.1's "TAIL is 3x longer than modelled". The law was 3x too fast, but the corpus default is
decay 1.0 at 60 dB/s and production forces decay 0.25, which is about 80 dB/s. The baselines were run with a release 25 %
SLOWER than the desk's, not 3x faster. Only the `decay_fast` preset (240 dB/s) is wrong by 3.

**Decisions.**

* `det` first: read it back on the desk after arming (test 2). If the write does not take, find out why before anything
  else in this section, because it decides which of the two laws the detector lives under.
* Frozen-display machinery: re-scope, do not retire. `SLOW_RELEASE` measures the release from the stream and `decay` does
  reach the stream, so it stays and becomes the trigger for re-forcing `decay`. `PEAK_HOLD_SUSPECTED` loses its meaning as
  a pref tell and its remedy (pref writes, then abort); what it can still detect is a stalled or duplicated stream, for
  which the remedy is renewing the meter subscription. See 7.2 for why the frozen-line veto inside BASE needs a desk check
  before it is trusted.
* `DETECTOR.md` §0: state both laws. PEAK: instant attack, release 20 / decay dB/s. RMS: one
  pole on power with T20 = decay, in both directions, so the one-frame settle above 300 Hz holds at `decay` 0.25 only (at
  the desk's resting 1.0 a step is still 3 to 4 dB short after three frames).
* Gate battery: drop `peak_hold_s`. Keep `det` RMS and the slow display, both can happen on the desk. Keep the
  `gain_offset_db` sweeps but rename them bus-level offsets: display gain never reaches the stream, a hot bus does, and the
  detector's level invariance is a real property. `accept_discriminator.py` was pre-registered so that the bar cannot move
  after the numbers are in; leave it untouched as gate v1, register v2 beside it before running anything on it, and report
  the shipped detector on both.

## 4. The analyser model (the sweep did not run under the heading it was filed under)

`python scripts/reanalyse_rta_logs.py prefs lf`

`meters.md` item 8 is headed "det PEAK, decay 0.25". The frame log says otherwise:

* release after each of the 60 tones at or above 300 Hz: median 1.00 dB/frame. That is decay **1.0**.
  `measure_rta_release.py` restores decay to its `--restore-decay` default of 1.0 on exit, it ran four minutes earlier, and
  `measure_rta_bands.py` never sets decay.
* the lowest value anywhere in 4956 frames is -97.0: the RMS state (section 3).

What this does to the four findings:

| finding | status |
|---|---|
| Band centres, flat response | **Stand.** Both are static; decay does not move a settled level |
| Skirts at ±1 band | **Stand** (-30 to -34 dB above 320 Hz) |
| Skirts at ±2 bands "-55 to -59, steeper" | **Floor-limited in part.** The -2 band reads the -97 floor itself in 31 of 59 tones, the +2 band in 8. Where it does not, on-centre tones read -53 to -58. The one PEAK capture gives -57 (upper) and -68 (lower) at about ±2.5 bands. Order 5 stands; "steeper at ±2" needs the re-run |
| "Under PEAK the last decibel takes longer: 3 frames to -3 dB, 6 to 7 to -1 dB" | **Wrong as attributed.** It is the RMS pole at decay 1.0: section 3 predicts -3.0 dB after 3 frames and -1.0 after 7. The only capture in the PEAK state shows a 2 kHz tone within 0.1 dB of its plateau on the second frame |

**The LF attack is a window filling.** A 78 Hz tone reads -28, -15, -10, -3, -1, 0 dB on successive frames. No one-pole
starts 28 dB down and arrives in six frames. Fitted to the four LF episodes in the rise log, behind the display pole:

| model | best fit | mean error |
|---|---|---|
| one pole on power, τ = k/Δf (the simulator's form) | k = 0.8 to 1.4 | 1.3 to 3.2 dB |
| Hann window of length c/Δf filling with the tone | c = 1.3 to 1.65 | 0.6 to 1.4 dB |

(Ranges: two independent implementations that align the first frame differently. The window wins in both; four episodes
are too few to fix c.)

So the detector's settle allowance (`analyser_rise_k` 1.0, six frames at 78 Hz) is confirmed as a bound, and the
simulator with `attack_k` 1.0 is still not the desk: its onset increments at 78 Hz are 6, 2, 1 dB where the desk shows
13, 5, 6, 2, 1.5. The corpus understates exactly the manufactured growth that made M7's 40 and 80 Hz false positives.

**Decisions.** Cut the corpus revision after the sweep is re-run under verified prefs (test 3, five minutes), not from
item 8 as it stands. One revision, containing: the grid; the display pole of section 3; a window-fill band response with
c fitted per octave; measured skirts; scenarios re-anchored one by one (bin or world); ring positions sampled across the
120-cent cell instead of hand-placed; results as rates over 12 seeds. G2's sweep set changes with it, as gate v2.

## 5. PR #14 and K8 (agree with the reading; the cause is not where it looks)

`K8` survives 6/6 over six seeds under both actuators, reproduced. Three separate things are involved.

**The detector is doing what it was designed to do.** The simulator's hop is an instantaneous retune (`CORPUS.md` §7.5):
the new line arrives at its plateau within one frame. Nothing was seen to grow, so it is MODERATE, the same class as the
fast-howl-to-a-quiet-plateau breakers that `DETECTOR.md` §4 hands to tier B by design. When the hop is modelled physically
(old mode loses its excess, new mode regrows from its seed at e/τ), the detector re-detects it on RISE evidence within
0.45 to 0.85 s with no policy help (new scenario `K8r`).

**The harness has no policy layer.** `run_one` drives the detector and a planner, nothing else. With a stand-in built
from the product's own pure rule `cfs_policy.tier_b_eligible`:

| scenario | actuator | detector alone | with tier B |
|---|---|---|---|
| K7 (100 cents) | GEQ / PEQ | 0/6 survive | 0/6 |
| K8 (200 cents, instant) | GEQ | 6/6 survive | 0/6 |
| K8 | PEQ | 6/6 survive | 1/6, then 0/6 with the fix below |
| K8r (200 cents, regrows) | PEQ | 0/6 survive | 0/6 |
| K8r | GEQ | 2/6 survive | 2/6 (see 7.4) |

Tier B engages 0.6 s after the hop (`min_age_s`) and follows up on "held" 3 s after the verdict.

**One real gap in the product.** PEQ seed 5: the first cut's verdict comes back "insufficient" in the very frame of the
hop, so the detector's deepen lands on the old notch. `note_cut` then judges every line within reach of that bell,
including the hopped line 0.17 oct away, which has never been emitted. It is filed "held". From then on it is barred from
tier B ("without a cut verdict"), has no open engagement to follow it up, and has no evidence of its own. Nothing will
ever cut it: a -13 dBFS howl stands to the end of the scene. `cfs.py` uses the same rule, so the product has the gap too.

**Decisions.**

* Re-born track must re-earn evidence: **intended, keep.** A line 200 cents away is a new source to a magnitude-only
  analyser; inheriting evidence across a sixth of an octave is how a detector starts chasing a melody.
* A "cut line moved" rule: not as an actuation rule. Tier B already is that rule. What is needed is narrower: a verdict
  filed on a never-emitted line by a neighbour's cut bars it from tier B only while pending (proposed, with tests).
* Reporting only, later: the old line is filed "confirmed" when it left rather than died, which counts towards "gain
  before feedback". A "moved" verdict (line gone, family-less line of like level born within a third of an octave in the
  same two frames) would keep the report honest. Not urgent.
* Merge PR #14 as the harness for the PEQ product PRs, with the stand-in and K8r added.

## 6. Housekeeping

* **The two races**: fixed as the request proposes, by waiting until the fake carries our own write before playing the
  engineer's hand. `cut_within_k1` measures wall-clock time from arm to cut; it should assert on frame timestamps and keep
  only a loose wall-clock bound. Not changed, since it cannot be validated here. On Linux loopback `main` runs 1114 passed,
  0 failed.
* **Whether the oscillator enters before or after the insert send**: item 1 answers it. The GEQ sits in the insert and its
  cut was seen at the post-EQ tap, so the oscillator enters upstream of the insert send.
* **M7's attack**: agreed, and section 3 quantifies it. At the desk's resting decay 1.0 every line is still 3 to 4 dB short
  of its level three frames after it starts.

## 7. Found on the way (not asked, all consequential)

**7.1 Switching `det` throws a +22.5 dB transient across the whole spectrum.** In `rta_peakhold_det` at the switch, a
steady -39.1 dB tone reads -16.6, -16.6, -19.6, -23.9 and is back within 1.5 dB after nine frames. `set_rta_source` writes
`det` at arm and the frame stream starts a few round trips later, inside that window. The arm reference (`arm_p95_db`,
`arm_max_db`, 2 s) would then be inflated for the session, which raises the LOUD and loud-ish lines. By section 3 this
is the PEAK to RMS direction; the direction the server writes is untested. Proposal: discard frames for 0.6 s after any pref write at arm.

**7.2 The frozen-line veto can fire without any peak-hold: on a steady electronic source.** The request concludes that
`PEAK_HOLD_SUSPECTED` and `FROZEN_LINES` "can never fire from the stream". The oscillator's tone and its skirts are constant
to the log's 0.1 dB over hundreds of frames (sd 0.0). The predicate's premise, "a live band through int16/256 quantisation
always jitters by 1 LSB", is false for any source more than about 67 dB above the noise in its band. A frozen line is
excluded from BASE, and the policy layer aborts the session after `frozen_abort_s`. So a watch armed on a bus that carries
a test tone, a DI'd drone or the console oscillator may stop itself. The logs round to 0.1 dB, so this is likely, not
proven; the fixed scripts log 3 decimals (test 5).

**7.3 Regression pins are seed-lucky** (table in section 2).

**7.4 The GEQ planner deepens a neighbour instead of opening the nearer band.** K8's new mode at 2806 Hz is the geometric
mean of 2.5 k and 3.15 k. `merge_adjacent_bands` 1 sends every cut to the existing 2.5 k notch, where -6 delivers 4.0 dB
against an excess of 4.0. That is the 2/6 survival in K8r. Worth a rule: merge into a neighbour only when its bell
delivers more than half its depth at the line.

**7.5 The centroid barely interpolates on the desk's skirts; a neighbour difference does.** With skirts of order 5 the
3-band power centroid snaps towards the bin centre. On held-out tones (fit on 30, test on 30):

| estimator | sd | worst |
|---|---|---|
| power centroid, as today, labelled on the DOC grid | 0.024 oct about a mean of -0.030 | 0.065 oct |
| power centroid on the measured grid (after PR #15) | 0.024 oct | 0.035 oct |
| neighbour difference, offset = (L₊₁ − L₋₁ − 2.3) / 81 band | 0.003 to 0.004 oct | 0.006 to 0.010 oct by split |

The PEQ design budgets ±0.03 oct of centroid error and 0.055 worst case. After PR #15 the centroid alone uses the whole
budget. The neighbour difference needs both neighbours clear of the bed, so it suits exactly the lines worth a PEQ notch.
Proposed for PEQ product PR 1, with the centroid as fallback.

## 8. Proposed changes: approve or deny each

Nothing is pushed. All are commits on local branches, exported as `review-response.bundle` and as patches.

| # | Change | Branch, commit | Touches | Tests |
|---|---|---|---|---|
| A1 | The two real-socket race fixes | `review-response/main` `270433f` | 2 test files | both pass |
| A2 | Measurement scripts set AND read back `det` / `decay`, refuse to run on a mismatch, record them in every row, restore what they found, log 3 decimals; `scripts/reanalyse_rta_logs.py` | `review-response/main` `43fc3ff` | `scripts/` only | exercised against the fake desk (it has no oscillator: addresses substituted) |
| A3 | This document, plus dated notes on the disputed paragraphs in `meters.md`, `CORPUS.md` and `HANDOVER.md` | `review-response/main` | docs only | full suite on the branch: 1113 passed, 1 failed (see note) |
| B1 | PR #15 follow-up: grid-relative expectations in the policy tests; dashboard default grid | `review-response/rta-band-grid-followup` `3a26ac1` | 3 test files, `webui/index.html` | see B2 |
| B2 | PR #15 follow-up: the simulator keeps its grid until the corpus revision | same branch, `da98bde` | `tests/rtasim/physics.py`, `CORPUS.md`, 1 test | full suite on the branch: 1113 passed, 1 failed (see note); the nine moved tests pass |
| C1 | PR #14 follow-up: tier-B stand-in in the harness (`policy="tier_b"`), scenario K8r, K8 asserted dead with the policy on | `review-response/peq-sim-tier-b` `9509428` | `tests/rtasim/` only | see C2 |
| C2 | **Product change.** `tier_b_eligible`: a bystander verdict bars a never-emitted line only while pending | same branch, `a8eb69e` | `src/x32mcp/cfs_policy.py` (one condition), 2 tests | full suite on the branch: 1119 passed, 1 failed (see note) |

C2 is the only change to product code and the only one that makes the system cut where it did not before. It removes an
accidental immunity rather than adding a new class of cut: the line was tier-B eligible before the neighbour's cut and is
eligible again after the verdict settles. It is validated in the simulator and by unit test; `cfs.py`'s engagement has no
integration test with a bystander verdict yet, and should get one before C2 ships.

Note on the one failure, the same on all three branches: `test_settings_defaults_and_env` asserts that the checkout
directory is named `x32-mcp`. It fails in any git worktree or renamed clone and passes in the main checkout (where `main`
runs 1114 passed); it is not caused by these changes.

To take the branches: `git fetch review-response.bundle 'refs/heads/review-response/*:refs/heads/review-response/*'`.

Not proposed as patches, because each needs a decision or a desk result first: the arm-time settle (7.1), the frozen-line
re-scope (7.2), the GEQ merge rule (7.4), the neighbour-difference estimator (7.5), gate v2 and the corpus revision.

## 9. Next desk session: 20 minutes, in this order

| # | Test | Time | Settles |
|---|---|---|---|
| 1 | Oscillator 2 kHz -40 dB to Main L+R, GEQ2 PRE, RTA post-EQ. Side A 2k at -12, side B at 0: expect 4.1. Then side B at -12 too: **12 dB if the one-leg reading is right, about 4 if the graphic is shallow.** Cross-check: side A -12 alone with the oscillator destination L only (expect 12) and R only (expect 0) | 2 min | Item 1 outright |
| 2 | Arm a watch, read `/-prefs/rta/det` back, note the floor with the oscillator on (-128 = PEAK, -97 = RMS), gate the tone once | 1 min | Whether the arm-time `det` write takes |
| 3 | `measure_rta_bands.py --det PEAK --decay 0.25` (it now refuses to run unless the desk confirms both) | 5 min | ±2 skirts, the PEAK attack, LF skirts and rise, per band |
| 4 | With a steady tone, switch `det` RMS to PEAK and back while `log_rta_frames.py` runs | 1 min | 7.1: the transient in the direction the server writes |
| 5 | Read test 3's log at 3 decimals: does the tone's band repeat its code exactly? | 0 min | 7.2 |
| 5b | Note the frame timestamps: the five logs show 52.0 ms per frame, the code assumes 50 | 0 min | Whether rates per second need a 4 % correction |
| 6 | Both GEQ legs at -12, tones at 1.78 / 1.88 / 2.00 / 2.11 / 2.24 k | 3 min | The GEQ bell's Q directly, for `geq_q_min/max` |
| 7 | Still open from the request: bus-EQ tap order with a channel-fed tone; 40 Hz rise time | 8 min | PEQ design §9 item 2 |
