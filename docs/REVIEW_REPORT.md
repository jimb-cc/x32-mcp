# Review report — x32-mcp / CFS² (response to `docs/REVIEW_BRIEF.md`)

Reviewer: Claude, 2026-09-21, working offline against commit `a408a2a` with no hardware.
Companion artefacts: eleven local review branches (§0.3), the offline validation corpus under `tests/rtasim/`, and the
appendices in `docs/review/` (physics briefs, corpus documentation, every finding with its verification record).

> Ten-minute path: §0 → §1.2 (why the detector fails, mechanically) → §1.5–1.6 (the new discriminator and its offline results) →
> §3.1 (safety findings table) → §4.1 (the calibration bug's actual root cause) → §8 (what to do at the desk next).

---

## 0. Summary

### 0.1 What was done

Each of the brief's five workstreams was run as an independent investigation, fanned out to several agents with deliberately
different starting hypotheses or attack angles, and **no finding entered this report without two independent skeptical
verifications** (one reproducing it in code against the repository, one arguing from the source and `docs/research/`). Design
questions (the discriminator, read-after-write) went to several designers with different mandates and were judged comparatively,
with the judges re-running every prototype's tests themselves. In total ~400 agent runs; the record is in the appendices.

The review sandbox has no IP networking, so the FakeDesk integration suites could not bind their loopback socket. A test-only
in-memory UDP shim was written (Appendix C; not in any PR) so that 912 of the 934 tests run here — the other 22 are the TCP
dashboard's. Every review branch was validated against that baseline plus its own new tests; the integration branch that merges
all of them runs **1034 tests green** (the original 912 plus ~120 new regression, predicate and corpus tests).

### 0.2 Headline findings

1. **The detector's growth feature is manufactured by the analyser.** A 1/10-octave band cannot respond faster than ~1/Δf: 370 ms
   at 39 Hz, 185 ms at 78 Hz, 7 ms at 2 kHz. An acoustically instant, perfectly steady bass note therefore renders on the 40–80 Hz
   bands as a 5–8-frame rise at tens of dB/s — inside the detector's 6–60 dB/s "regenerative growth" window — while real rings on
   a wedge grow at ~200 dB/s per dB of excess and are discarded by the 60 dB/s onset guard. Verified numerically against the
   shipped code: a steady tone through a 1/Δf band model is reported as feedback 10/10 times for every band from 45 to 315 Hz.
   That, plus the never-set RTA `decay`/`peakhold` prefs and a score whose sustained-ring region is unreachable by construction,
   is the whole M7 story (§1.2). On the new 61-scenario physics corpus the shipped detector passes 12 scenarios with **948 false
   positives**; the redesigned discriminator (§1.5) passes with **0 false positives** on the corpus, on hold-out seeds and on every analyser variant, catches 115 of 117 rings with the LF window declared (median 200 ms), leaves no detected ring alive after its cuts, and takes 179 rather than 500–950 wrong detections on 68 scenarios written specifically to defeat it — every one in a class documented as physically irreducible on this analyser, handled by policy (publish, one bounded cut, verify) rather than by threshold (§1.6). 
2. **The calibration bug was a cancellation, not a stale read** (§4). `_notch()` commits the cut to the controller *before* the
   GEQ write; `set_geq_band` first reads `/fx/N`'s type through a 2 s cache; the 2 s calibration aged that entry past its TTL, so
   the write path suspended between commit and datagram, and the test's own `feedback_watch_stop` cancelled it there —
   deterministically on Windows + CPython 3.12 (15.6 ms event-loop clock), a 1–3 % race elsewhere, which is why it looked
   inexplicable. `CancelledError` is not in `_notch`'s except tuple, so nothing was logged. Three investigators seeded with
   different hypotheses converged on this independently and two cross-checkers falsified the alternatives. It is a live-desk
   defect (every first notch of a real session has the window), fixed in PR `cfs-write-safety`.
3. **Safety: 58 findings, 58 confirmed** (§3). Seven critical, all now fixed on review branches with regression tests:
   Main LR compressor make-up (+24 dB) and EQ (+15 dB) were Tier 1; `panic()` was undone by the server's own in-flight restore,
   ramps and ring-out; the ring-out answered an operator's emergency pull-down with an absolute write straight back up (+47 dB in
   one datagram). Plus: tokens bound arguments rather than what the user was shown (five variants), no RTA-liveness or
   RTA-source interlock, CFS validated "cuts only" against its own belief (a hand-deepened band was written shallower),
   `show_mode(false)`/`disconnect`/`connect(elsewhere)` unconfirmed.
4. **The tests certify the emulator, and the emulator was written from the same beliefs as the code** (§2). 114 assumption
   findings (81 confirmed, 31 partial, 2 refuted), 11 high; the loop closes on itself in five places: the analyser (memoryless,
   absolute, always on the bus we chose, floor at −50-odd instead of exactly −128), the loop (fixed 15 dB/s growth, brick-wall
   cuts), the console (**Main LR treated as one side of a dual GEQ2 — a notch "on the PA" cuts the left stack only and side B is
   lent to another bus; found by three slices independently, fixed in PR `main-lr-stereo-geq`**; linked pairs; FX loads instant;
   head-amp mapping computed rather than read from `/-ha`), other hands on the desk (four-client `/xremote` cap), and the network
   (only replies are ever lost). Ten FakeDesk "realism switches" are specified; two ship in the PRs.
5. **Read-after-write** (§5): only an observed value proves application; replies and the `/` echo are flow control. Three
   prototype designs were built and judged; the synthesis ships as PR `read-after-write` (a `read_until` settle helper,
   `conn.sync()`, two real cache bugs fixed — an in-flight-join hole and a 2 s stale-answer amplifier — FakeDesk apply latency and
   inbound loss, `setup_ringout_eqs` reporting *not yet verified* instead of a false `GEQ_VALIDATION_FAILED`, and
   `scripts/measure_settle.py` to turn the UNCONFIRMED latencies into a measured `timing:` table at the next desk session).

### 0.3 The review branches (local, ready to push)

The sandbox this review ran in blocks `git push` to GitHub repositories outside its own organisation, so the branches are in
your clone at `/Users/jimb/code/x32-mcp` rather than opened as PRs. Each is self-contained off `main` (`a408a2a`), has a full
commit message written as the PR description (`gh pr create --fill` works), and its tests fail on `main` and pass on the branch.
`review/all-integrated` merges all of them with the (few, mechanical) conflicts already resolved.

```bash
cd /Users/jimb/code/x32-mcp
for b in rtasim-corpus detector detector-cfs rta-ballistics guard-main-processing panic-hardening cfs-write-safety token-binding \
         argument-hardening read-after-write discover-mics-signal main-lr-stereo-geq; do
  git push -u origin review/$b && gh pr create --head review/$b --fill
done
git push -u origin review/all-integrated   # optional: everything, pre-merged
```

| Branch | What | § | Tests added |
|---|---|---|---|
| `review/rtasim-corpus` | `tests/rtasim/`: physics-based RTA/loop simulator, 61-scenario corpus, detector harness, baseline driver, `CORPUS.md` | 1.4 | 24 |
| `review/detector` | The redesigned discriminator (stacked on `rtasim-corpus`): predicates + evidence lanes + probe + `note_cut` verdicts, the 68-scenario adversarial corpus, `docs/DETECTOR.md`, the project `/verify` recipe | 1.5–1.6 | ~70 |
| `review/detector-cfs` | The CFS policy that consumes the detector's hooks (stacked on `all-integrated`): LF edge from the mics' HPFs, ring-out contract check, tier-B one-shot with held-deepen, scribble-strip candidate alerts, at-arm gating in watch, flag handling | 1.5, 1.7 | (see branch) |
| `review/rta-ballistics` | `set_rta_source` forces `decay`→min, `peakhold`→OFF, records `gain` and the prefs it found | 1.2c, 2 A3 | 1 |
| `review/guard-main-processing` | `/main/*/dyn/*`, `/main/*/eq/*` guarded; Tier-1 compressor make-up clamped to 6 dB | 3 S1 | 3 |
| `review/panic-hardening` | panic cancels ramps, aborts restore/CFS, latches outputs (`clear_panic`, T2), re-asserts on reconnect, reads back and re-sends; honest docs on what it cannot silence | 3 S2/S14/S23 | 6 |
| `review/cfs-write-safety` | two-phase notch (propose→write→commit), no hidden read in the cut path, operator-override abort, frame-liveness interlock, GEQ pushes adopted, abort on RTA-source/slot/insert change and on show mode, back-off retried | 3 S3/S7–S10/S19–S20, 4 | 9 |
| `review/token-binding` | tokens bind file digests / resolved stages / open-mic list / scene name; canonical payload compare; one live token per action; cap; hazard-first restore preview in the right direction | 3 S4–S6/S16–S18 | 3 (+2 changed) |
| `review/argument-hardening` | ring-out `max_step_db` 3 / `min_dwell_ms` 250; report ids and export paths confined; `show_mode(false)`, `disconnect`, re-`connect` confirmed; show-mode relative check on absolute moves; DCA ceiling 0; label control chars | 3 S11–S13/S15/S21–S22/S26 | 3 (+3 changed) |
| `review/read-after-write` | `settle.read_until`, `conn.sync()`, cache E0/E1 fixes, FakeDesk apply-delay + inbound loss, setup/RTA-source verified-or-not-yet, `scripts/measure_settle.py` | 5 | 15 |
| `review/discover-mics-signal` | candidates annotated with live input level; "no input signal" warnings; FakeDesk `unplugged` | 2 C6, 6 | 1 |
| `review/main-lr-stereo-geq` | stereo strip takes a whole slot / both GEQ2 sides written / never lent; two-leg main in the fake | 2 C1 | 1 (+1 changed) |
| `review/all-integrated` | all of the above merged, conflicts resolved | | 1034+ pass |

What is deliberately *not* in a branch (recommendations with designs and test recipes in the sections): the head-amp `/-ha`
read (§2 C5), linked bus pairs (C2), insert eviction/flatness/PRE fix-ups in `plan_setup` (C4), `/outputs` taps (B4/S23),
`feedback_watch` on `main` in show mode (S25), strict argument types at the MCP boundary (S27), the remaining read-after-write
items (§5.3 items 3–5), the FakeDesk realism switches (§2.3), and restoring the operator's RTA prefs at disarm (§2 D3).

---

## 1. The feedback discriminator

### 1.1 What the current detector actually decides

`detector.py` qualifies a band when its prominence over the median of its ±3 neighbours is ≥ 12 dB and its level is ≥
`min_level_db` (−45), tracks it as a candidate while a qualifying peak stays within ±1 band, and scores

```
confidence = 0.3·min(1, prom/12) + 0.2·min(1, frames/3) + 0.5·growth,   growth = clip(slope/20 dB/s) if 6 ≤ slope ≤ 60 dB/s else 0
```

emitting at ≥ 0.7 after ≥ 3 frames, plus the M7 override (prominence ≥ 25 dB for ≥ 6 frames emits regardless). Since
0.3 + 0.2 = 0.5 < 0.7, **growth is not one feature among three — it is a mandatory gate**, and the only question the
detector really asks is "is this prominent band currently rising at 6–60 dB/s?".

### 1.2 Why it fails — three mechanisms, not two symptoms

The brief describes a false negative (plateaued howl) and a false positive (bass at 40/80 Hz). They are consequences of
three structural problems; fixing the symptoms (the override, the −45 dB gate, a 250 Hz high-pass on candidates) leaves
the mechanisms in place.

**(a) The score has an unreachable region by construction.** Anything already sustained when first seen — a howl that
plateaued before arming, a ring that saturated the amplifier in under 150 ms, a ring whose growth phase was hidden under a
cymbal crash — can never be emitted by the main path. The override reopens the region above 25 dB prominence and leaves
12–25 dB plateaued rings unreachable. A weighted sum also lets surplus in one physical property *buy* a deficit in another,
which is the shape of the false positive: a bass fundamental 30 dB over the bed is "very prominent, very persistent, and
rising a bit", and out-scores a genuine 14 dB ring that has stopped growing.

**(b) The growth feature is confounded with frequency by the analyser itself.** This is the part the brief does not
mention and, I believe, the actual reason the false positives sat at 40 and 80 Hz rather than anywhere else. The RTA is a
1/10-octave bank, so band *i* has bandwidth Δf ≈ 0.069·f. No analyser can resolve a band faster than roughly 1/Δf,
whatever Behringer's implementation:

| band | centre | Δf | 1/Δf | frames @ 20 fps | a 20 dB instant onset renders as |
|---:|---:|---:|---:|---:|---|
| 10 | 39 Hz | 2.7 Hz | 370 ms | 7.4 | a ~54 dB/s ramp over 7 frames |
| 20 | 78 Hz | 5.4 Hz | 185 ms | 3.7 | a ~100 dB/s ramp over 4 frames, settling tail at 10–40 dB/s |
| 30 | 156 Hz | 10.8 Hz | 92 ms | 1.8 | 2 frames |
| 44 | 412 Hz | 28.6 Hz | 35 ms | 0.7 | sub-frame (trips the 60 dB/s onset guard) |
| 70 | 2.5 kHz | 173 Hz | 6 ms | 0.1 | sub-frame |

So an *instantaneous* bass-note onset necessarily appears on the 40–80 Hz bands as a monotonic rise lasting 4–7 frames at a
few tens of dB/s — which is precisely the detector's definition of regenerative growth (≥ 3 frames, 6–60 dB/s) — while the
same onset at 400 Hz+ is over within a frame and is (correctly) discarded by the onset guard. The exponential settling tail
of the band filter passes through the 6–60 dB/s window at every LF band regardless of how hard the note was hit. Bass
fundamentals are also the most prominent narrow features in any pop mix (20–30 dB over the bed on a 1/10-octave display),
and 80 Hz = 2 × 40 Hz is a harmonic pair from one instrument. The detector is therefore *structurally* biased to call
low-frequency programme "feedback"; the M7 notches at 40/80 Hz were the expected outcome, not bad luck, and no re-weighting
fixes it. Every synthetic stream in the repo renders onsets identically at all frequencies (instant, or a fixed 5-frame
rise), which is why 934 tests could not see this.

**(c) The analyser's ballistics are not under the server's control.** `set_rta_source` (meters.py) forces auto-gain off and
the detector to PEAK — both M7 fixes — but leaves `/-prefs/rta/decay` (0.25 … 16, 19 log steps; `docs/research/meters.md`
§5.1) and `/-prefs/rta/peakhold` (OFF, 1 … 8) at whatever the console last had. With a long decay every band releases
slowly, so notes "persist" long after they stop and the persistence term measures the console preference, not the sound;
with peak-hold on, every past peak is a dead-flat prominent band — bit-for-bit what an established ring looks like, and
exactly what the new override emits on. The FakeDesk's RTA has neither behaviour, so no test can exercise it. CFS² should
force `decay` to its minimum and `peakhold` OFF at arm, verify by read-back, record the values in the session report, and
restore them at disarm (PR, §0.3).

A fourth, smaller point: `min_level_db` is a single absolute gate fitted to one room (and, per the brief, fitted to
background music rather than the room). Prominence is already a *spatial* floor. What is missing is a *temporal* one: is
this energy new since the detector armed? That is what the backed-out calibration was reaching for; §4 covers why it broke
and §1.5 folds a per-band baseline into the design instead of a global gate.

### 1.3 What physically distinguishes feedback from programme (and what does not)

Two independent physics briefs were commissioned (electro-acoustic loop; analyser + programme statistics — Appendix A
reproduces both in full, with the calculation scripts). The points that drive the design:

**Growth rate is not a usable primary feature in either direction.** Above threshold a loop grows at *R = e/τ* dB/s (excess
gain over loop delay): 1 dB of excess is 200 dB/s on a wedge at 1.2 m (τ ≈ 5 ms), 90 dB/s on tops at 3 m, 15–30 dB/s only on
long reverberant or sub paths. In `ring_out` the +1 dB step that crosses threshold leaves e ∈ (0, 1] dB, so roughly half to
70 % of genuine ring-out howls exceed the 60 dB/s onset guard, are discarded as "note onsets", plateau at the limiter or at
0 dBFS, and then sit at 0.50 forever — the M7 false negative is the *typical* case, not a corner. Meanwhile (§1.2b) the
analyser manufactures 6–60 dB/s "growth" from any LF onset. Verified numerically against the shipped detector and
`device.yaml`: an acoustically instant, perfectly steady tone rendered through a 1/Δf band model is reported as feedback in
10/10 trials for every band from 45 Hz to 315 Hz at 16–20 dB prominence (latency 0.2–0.4 s, "slope" 8–15 dB/s, confidence
0.70–0.88), and for all 95 testable bands at ≥ 25 dB prominence via the override.

**What does discriminate**, in order of strength, with the latency each costs at 20 fps:

| evidence | feedback | programme | frames | caveats |
|---|---|---|---|---|
| Harmonic family (single frame) | none; a linear loop makes no partials. Only a clipping chain adds odd harmonics, ≥10–30 dB down, *after* H1 passes ≈ −6 dBFS | partials at +10.0, +15.85, +20.0, +23.2, +25.9, +28.1, +30 bands, 0…−15 dB re H1, co-onsetting | 1 | absence of family is only evidence when H1 prominence ≳ 20–25 dB (weaker partials drown in the bed); near-sine sources (808/sine bass, single-drawbar organ, flute top register, whistle, sine leads, E-bow, falsetto closed vowels, test tones) have none. No subharmonics ever: a line at f/2 means f is somebody's H2 (the M7 40/80 pair) |
| Sub-band frequency stability (centroid of peak ±1 band) | σ < 0.05 band; hand-held mics *hop* to a neighbouring loop candidate (±1 band above 1.5 kHz, ±2–3 bands at 300–800 Hz) rather than glide | melody steps 1–2 bands; vibrato ±0.25–0.8 band at 5–7 Hz; glides (808, toms, scoops) | 3–5 | fixed mics and organ notes are also stable |
| Response to a known gain step (ring_out owns +1 dB steps; bus RTA tap is pre-fader) | ≥ 3 dB/dB within 4 dB of threshold, 6.5 dB/dB within 2 dB, runaway above | electrically injected programme 0 dB/dB at the tap; open-mic acoustic spill exactly 1 dB/dB | 5–20 per side | needs medians over frames because programme fluctuates; free in ring_out, passive in watch (channel moves arrive via /xremote) |
| Persistence / "it ends" | never ends above threshold; below, decays only when gain drops | notes 0.1–2 s, pads/organ ≤ 8–16 s | K | corrupted by RTA `decay`/`peakhold` unless forced (§1.2c) |
| Level micro-variance | monotone/exponential path, ±0.2–0.5 dB air-movement wobble < 2 Hz | vibrato/tremolo/beating 0.5–6 dB at 0.5–8 Hz | 5–10 | rings are not *exactly* flat; a corpus that makes them so flatters designs |
| Narrowness (peak − max of ±2 bands) | one band + skirt (−7…−18 dB at ±1) | a *partial* is equally narrow; broadband bumps, cymbals, formant humps are not | 1 | separates tones from noise, not rings from partials |
| Growth 6–60 dB/s | minority of real rings | every LF onset through the analyser | ≥ 3 | keep only as a positive upgrade, normalised by the band's rise time; never required |

**Where feedback can physically occur** (brief Q3) is a statement about mic × HPF × loudspeaker path, so it differs by
source; a fixed 250 Hz–8 kHz window is right for exactly one class (HPF'd dynamic vocal into tops, no mic path to subs — the
M7 rig) and wrong for kick/tom/bass-cab mics near subs (35–250 Hz is their *only* mode), acoustic-guitar body resonance
(95–110, 180–220 Hz), un-HPF'd instrument mics on 15" wedges (100–160 Hz), and too tight at the top for condenser
lavs/headsets (8–12 kHz routine; even M7's SM58 rang at 8 kHz, band 87). The defensible floor when a channel HPF is on is
≈ 0.6–0.7 × f_c (a 12/18/24 dB/oct Butterworth has lost 12/18/24 dB an octave down), and the HPF state of every open mic is
desk-readable (`preamp.hpf_on/hpf_hz`, already returned by `Desk.get_strip` and already read by `discover_mics`). §1.5 derives
the window per session from that instead of hard-coding it.

**What a −3 dB cut does.** The GEQ attenuates by the slider value only at the band centre; a ring midway between two
1/3-octave centres (RTA bands *i* ≡ 5 mod 10: 442, 884, 1768, 3536, 7071 Hz …) sees −1.5 dB (constant-Q) to −2.5 dB
(proportional-Q) from a −3 dB slider, so `VERIFY → deepen → −9 → "not tamed" → ABORT` can happen to a ring a human would kill
with two −3 dB cuts on the flanking bands. The ±1-band skirt pattern locates a ring to ≈ ±0.02 octave; when it sits > 0.1 oct
from the chosen GEQ centre the planner should cut both neighbours −3 (one budget line) rather than one band −9. The X32 GEQ's
actual bell shape is not documented — one pink-noise measurement (PRE insert, −6 dB on one band, read RTA at 0/±1/±2/±3 bands)
settles it and should replace `SyntheticRta.set_geq_gain`'s brick wall.

**Tap point.** HANDOVER §4b(4) (a POST-insert cut invisible to the RTA, a PRE-insert cut visible) places the bus RTA tap after
the bus EQ and before the POST insert point, i.e. upstream of the bus fader. Consequence the brief's stream list gets wrong:
raising the *bus master* does not raise electrically injected programme at the tap at all — only energy that has been round
the loop (open-mic spill: +1 dB/dB; a regenerating candidate: ≫ 1 dB/dB). "Everything rises together" is true for a
channel-fader or send move, not for the master move `ring_out` makes. This makes the active probe cleaner, not weaker.

### 1.4 The offline validation corpus, and the shipped detector measured against it

Both physics briefs end in a scenario list; a corpus builder turned them into a deterministic simulator and harness, and an
adversarial critic then audited the simulator line by line for anything that made a detector's life unrealistically easy,
fixed what it found, and added 24 hostile scenarios. The result lives in `tests/rtasim/` (test support, stdlib only, PR
`rtasim-corpus`; `tests/rtasim/CORPUS.md` documents every scenario, parameter and citation):

* `analyser.py` — 100 bands, Butterworth-N skirts (a tone between centres splits correctly), per-band attack limited by the
  band's own time resolution (τ_a ∝ 1/Δf: slow at LF, instant at HF), PEAK/RMS detector, `decay` release law, peak-hold, manual
  gain offset, χ² estimation noise, 1/256 dB quantisation, −128 floor. Every constant is a sweepable parameter.
* `sources.py` — harmonic notes with named timbres (voice, bass, 808/sine bass, guitars, organ flue, flute, whistle, piano, saw…),
  ADSR, vibrato/glide/tremolo, drum hits and patterns, bass lines / melodies / pads / speech, pink beds with humps and swells,
  common-mode gain events, driven room modes, and `FeedbackRing` implementing the loop physics: growth = excess/τ, saturation
  (clip with odd harmonics, or a limiter), sub-threshold regeneration through the *comb* |1/(1−g·e^{−j2πδτ})|² (not a flat
  boost), plateau wobble from air movement, excess wander, frequency between band centres, hops, and response to GEQ cuts through
  an RBJ bell and to master steps through a coupling factor.
* `scenarios.py` — **61 scenarios**, each with ground truth derived from the rendered frames (onset = first frame the loop is
  super-critical; t_prom = first frame a ring-dominated band reaches 12 dB prominence). All five streams the brief demanded, the
  M7 replica (`S1`: bass line under quiet music; `M2`: quiet music + ring_out steps with two modes), and the nasty ones: 808 sine
  bass, organ/flute/whistle/sine-lead held notes, soprano closed vowel on a band edge, plateaued ring under music from t = 0, slow
  3 dB/s ring, ring at an RTA midpoint, two rings an exact octave apart, clipped and amp-clipped howls, master −20/+20 both ways,
  `decay` 16 and peak-hold on, kick/bass unison, applause, hand-held ring that stalls and hops, mains hum + HVAC whine,
  reverberant area-mic ring, kick-mic sub ring at 65 Hz, acoustic-guitar body ring at 122 Hz.
* `harness.py` — `evaluate(detector_factory, scenarios, seeds, closed_loop)`: per scenario TP / miss / FP / EARLY / TAIL / HARM,
  latency vs t_prom, the GEQ bands the FPs would have cut; closed loop applies each detection through the real `NotchController`
  and an RBJ bell one frame later. A run passes iff FP = 0, miss = 0 and every latency ≤ budget (300 ms default).

**Baseline of the shipped `FeedbackDetector` + `device.yaml` (3 seeds, open loop): 12 of 61 scenarios pass; 948 false
positives; 9 misses.** Every music/no-feedback scenario except the silent room, a drum bed and a kick pattern fails: the M7 bass
line (32 FPs, cutting 80/100/160 Hz), 808 bass (32, at 40/50/63), vocal vibrato (40), organ (13), flute (20), whistle (28),
synth pad swell (66), speech (98 — formant transitions read as growth), applause (35), mains hum + HVAC whine (55), the driven
room mode (40, at 40/80/100 — *exactly* the M7 notches), auto-gain drift (18). The misses are the slow ring (3 dB/s), the
plateaued 14 dB-prominent ring under music, and the reverberant slow ring — all "growth outside 6–60 and prominence < 25". The
established 8 kHz ring and the fast clipped howl pass *only* through the override at exactly 0.70, and the clipped howl's "pass"
spends five extra cuts on its harmonics at 5 kHz and 12.5 kHz in closed loop. With the override disabled (pre-M7 behaviour) the
established rings are never detected at all. The per-scenario tables are in `tests/rtasim/CORPUS.md` §6 and
`baseline_tables.md`.

The critic's own list of what the corpus still cannot tell us is reproduced in §1.8; the important ones: the analyser model's
constants are guesses until measured (any design that keys on the *exact* τ_a or skirt is fitting the simulator), real mixes
have denser transients than the beds (0 FP here is necessary, not sufficient), and LF-ring coverage is thin.

### 1.5 The proposed discriminator

**How it was chosen.** Five designers were given the two physics briefs, the corpus and harness, the lead's predicate notes,
and deliberately different mandates: *explicit physical predicates* (the lead's P1–P6 as boolean tests), *sequential evidence*
(a log-likelihood accumulator), *minimal delta* (smallest change to the shipped detector that is actually correct), *track and
group* (track spectral lines over time, group them into sources, classify sources), and *free hand*. Each produced a working
`detector.py` scored on the 61-scenario corpus. Five auditors then re-ran every claimed number (all reproduced byte-for-byte),
ran the hold-out seeds and analyser sweeps the designers had skipped, read code against documentation, and wrote 55 new
hostile scenarios between them — every design was broken by its auditor. Two judges then ranked the five on the evidence,
independently, and agreed: **1. predicates, 2. free-hand, 3. track-and-group, 4. minimal-delta, 5. sequential-evidence.**
All five had reached 0 false positives on seeds 1–3 (from 948), so the ranking was decided on *unseen* programme: predicates was
the only design whose zero held on hold-out seeds (1 FP per three unseen seeds, always the same synth-pad swell), on every
analyser variant (attack constant ×2, biquad bank, skirt order 2, `decay` 4 s, RMS detector, noise ×1.5), and — decisively —
with the LF window opened to 40 Hz (the M7 40/80 Hz mechanism stays dead *inside* the window because rises within the band's
own settling time are never scored; track-and-group reproduced the M7 cuts the moment a kick mic was declared). It also has the
smallest decision surface and the right asymmetry: a line that merely *qualifies* is published as a candidate, never cut, until
one piece of positive evidence arrives, and none of the evidence is a growth *rate*. Its weaknesses were all on the miss side and
all local (below). The judges' reports, with per-design tables, are Appendix H.

**Decision logic** (`src/x32mcp/detector.py` on branch `review/detector`; every constant is a `device.yaml detector:` key with
its physical origin in `docs/DETECTOR.md`). Per frame, O(bands·k):

* *Lines.* Prominence = level − median of ±3 neighbours (unchanged). A **line** is a local maximum ≥ 6 dB prominent; a peak and a
  neighbour within 6 dB form a *cluster* (a tone between centres reads −3/−3; edge vibrato alternates the louder band). Per line:
  cluster power, cluster prominence, **narrowness** (peak − the louder of the two bands just outside the cluster's skirts; a
  sinusoid clears 24–60 dB at ±2 bands on a 1/10-octave bank, a formant or cymbal hump reads 0–6), and a **sub-band centroid**
  (power-weighted position of peak±1 after removing the local floor; ±0.1 band) — so a ring at 525 Hz is reported as 525 Hz and
  `NotchController` can choose the right GEQ band or a flanking pair.
* *Common mode.* The median of per-band *changes* over the signal-bearing bands (excluding the line's own ±4) between two instants:
  a fader, auto-gain or master move shifts every signal band equally; a chord, a crash or one growing line moves a minority. All
  rises are measured net of common mode beyond a 2 dB dead-band. (This is the only common-mode formulation on the panel that
  survived the RMS-detector sweep; a level-percentile reference jumps on crashes.)
* *Baseline.* Per band, an asymmetric tracker (fast down, very slow up after a 1 s seed): "excess over baseline" replaces the
  M7-fitted −45 dBFS gate, so quiet rings at −50…−78 dBFS are reachable and hum/HVAC/rumble present from the start are "already
  there".
* *Tracks.* Lines are associated frame-to-frame by centroid (±0.6 band); a track coasts 8 frames through a masking crash; a track
  whose centroid wanders > 1 band in 10 frames is **re-born** with no history (glide, scoop, melody step, a hand-held ring hopping
  to the next loop candidate) and must re-earn everything at the new frequency.
* *Predicates* (feedback ⇒ true): **P1 NARROW** (cluster prominence ≥ 12 dB and narrowness ≥ 8 dB); **P2 NO FAMILY** — partials at
  +10, +15.85, +20, +23.2 bands present *as peaks*, within 18 dB of the candidate, and **co-moving** with it; two such, or a lone
  exact octave *born within 2 frames* of the line (catches 8′+4′ organ; two independent rings an octave apart are not co-born);
  vetoed also if the line is itself H2/H3 of a lower peak that owns another partial; a family that first appears after the line has
  risen 12 dB is distortion of a howl, not an instrument; **P3 STABLE** (centroid range ≤ ±0.25 band ≈ ±30 cents over 250 ms — vibrato
  straddling an edge swings ±0.3–0.8 band, glides walk); **P4 SUSTAINED** (not decaying faster than 3 dB/s, sag under the recent
  max ≤ 4 dB — plucked/struck notes decay 3–15 dB/s, and a ring already killed by a cut falls at ≥ 3.75 dB/s even with `decay` at
  16 s, which is what removes the wasted re-deepening on RTA release tails); **COMMON-MODE** and **CO-GROWTH** vetoes (the line moved
  with the mix; ≥ 2 other lines *born with it* are rising with it — a pad, a fade-in); **P5 NEW / AT-ARM** (≥ 10 dB over the band's
  baseline; or present at arm: continuously, flat, prominent ≥ 18 dB, and either loud or outlasting `arm_confirm_s`); **P6 WINDOW**
  (lower edge 160 Hz in watch, 63 Hz in ring-out, 40 Hz when the caller says LF feedback is possible — and, new, an explicit
  `lf_edge_hz` the CFS layer derives from the open mics' high-pass filters; §1.7 Q3).
* *Evidence* (any one promotes a qualifying line; none is a rate): **RISE** — the line's own level is ≥ 6 dB above the low-water
  mark of its settled presence run, net of common mode, where the first ⌈1.5·k/(Δf·T) − ½⌉ frames of a run are never scored
  (k = 1.0, the critically-resolved bound — 1 frame above 300 Hz, 4 at 100 Hz, 7 at 63 Hz, 11 at 39 Hz; **this is the M7 fix**: an
  instant bass onset renders as a decelerating ramp inside exactly those frames) and a run restarts on a single-frame jump of a line
  that was not already climbing (a re-struck note, a syllable); **LOUD** — at the 0.0 clip flag, or ≥ 6 dB above every band outside
  its own ±3 *and* above a level referenced to the arm-time spectrum (−10 dBFS only as a ceiling; with `/-prefs/rta/gain` now
  pinned, §2 A3); **AT-ARM** (P5's second form: the M7 "60 dB line at 0.50 for 15 s" is emitted 200 ms after arming); **PROBE**
  (ring-out only) — after each `note_gain_step(Δ)` from the CFS layer, a line steady before the step whose median over the next
  0.15–1.4 s rose by ≥ Δ + 2 dB while the common mode moved ≤ Δ + 0.5, on two steps: regenerative gain 1/(1−g) gives +3 dB/dB at
  −4 dB from threshold, programme at the pre-fader tap gives 0…+1 dB/dB; a line answering 1 dB/dB and never over-responding is
  **STATIONARY** (hum, HVAC, playback) — reported, never cut; a line with two hits is emitted *before* it crosses threshold, which
  is what a human ring-out does with a band that swells on every nudge; and, grafted from the runner-up, **back-filled fast rise** —
  when a track is born its band's last 8–16 frames are pulled from the spectrum store and a run of ≥ 3 settled increments each ≥ 2 dB,
  summing ≥ 12 dB, no single increment > 55 % of the rise, counts as RISE (this is what catches the 150–400 dB/s howl whose growth
  frames pre-date its track, without admitting 1–2-frame instrument attacks).
* *Classes and emission.* `MUSICAL` = family ∨ common-mode ∨ co-growth (per frame, not latched). `BASE` = P1 ∧ ¬MUSICAL ∧ P3 ∧ P4 ∧
  P5 ∧ P6 ∧ age ≥ 250 ms. `STRONG` = BASE ∧ (RISE ∨ LOUD ∨ AT-ARM ∨ PROBE) → a `Detection` with `reasons` (the predicates and evidence
  that fired, in words), interpolated `freq_hz`, class, prominence, excess, rise. `MODERATE` = BASE only → published in
  `detector.candidates` (and as a `cfs.candidate` event), **not cut**. In `ringout` mode STRONG and pre-emptive PROBE lines are
  emitted; AT-ARM lines wait for the probe unless LOUD (nothing regenerative can be *established* at ring-out arm unless the system
  is already howling, and then it is loud); in `watch` mode STRONG only. Re-emission (deepening) only while the line is within 1 dB
  of, or above, its level at the previous emission and still STRONG by RISE/LOUD/PROBE — never while decaying, never on BASE alone;
  `note_cut()` from the CFS layer marks a line that dropped by ≈ the bell depth and then sat flat as `FALSE_CUT` (programme through
  an EQ; a loop losing gain falls away by far more than the cut) — never re-emitted, reported. A `PEAK_HOLD_SUSPECTED` flag (many
  bands bit-identical for many frames — live audio through int16/256 never repeats exactly) and `programme_present()` (spectral
  flux/occupancy over 2 s) are exposed for the CFS layer's arming checks.
* *What the CFS layer adds* (`cfs.py`): mode; the +1 dB step fed to `note_gain_step`; `lf_edge_hz` = 0.7 × the lowest HPF corner
  among the open mics (floored at 60 Hz; 100 Hz when none has an HPF on); the ring-out *contract check* — if `programme_present()`
  at arm, the run proceeds under watch policy and says so; the **tier-B one-shot policy** for the one class every design misses
  passively (a howl that arrives in 2–5 frames and plateaus under a limiter *below* the loud lane: `MODERATE`, ≥ 20 dB over its
  band's baseline, survives 600 ms → one −3 dB cut, classified by VERIFY: drops ≫ 3 dB → ring, eligible for deepening only on
  re-growth; drops ≈ 3 dB and sits → `FALSE_CUT`, ignore-listed, released if policy allows) — bounded harm, one band, −3 dB, which is
  the only physically supported answer to an observation that is genuinely identical to a dead-steady family-less synth note; RTA
  prefs pinned at arm and restored at disarm (PR `rta-ballistics`); the dead `_calibrate_floor` removed.

What it deliberately does **not** do: use growth *rate* as evidence (an optional GROWTH lane exists, ships off: at any threshold
that helps latency it fires on a 10 dB/s saw-pad partial on an unseen seed); cut a MODERATE line in watch on passive evidence; latch
any veto; use the simulator's own analyser constant.

### 1.6 Offline results

All numbers below were measured by the implementer, reproduced exactly by two independent verifiers on their own worktrees, and
re-measured a third time after the fix round (Appendix I); `docs/DETECTOR.md` §6 has every per-scenario row. "watch"/"ringout" =
the detector as `cfs.py` constructs it with no LF declaration; "_tag" = with the LF window the CFS layer derives from the channel
HPFs (`review/detector-cfs`). Seeds 1–3 unless stated; latency is measured from the frame a ring becomes 12 dB visible.

**Main corpus (61 scenarios).**

| run | scenarios pass | rings | caught | missed | **false positives** | harmonic cuts | alive after cut (closed loop) | latency p50 / p90 / max | ≤ 300 ms |
|---|---|---|---|---|---|---|---|---|---|
| shipped detector, watch open (baseline) | 12 / 61 | 117 | 108 | 9 | **948** | 143 | – | 252 / 602 / 12 649 | 70 / 108 |
| new, watch open | 49 / 61 | 117 | 107–109 | 8–10 | **0** | 0 | – | 200 / 751 / 3 901 | 81 |
| new, watch_tag open | 51 / 61 | 117 | 115 | 2 | **0** | 0 | – | 200 / 751 / 3 901 | 83 / 115 |
| new, ringout_tag open (probe fed) | 50 / 61 | 117 | 112 | 5 | **0** | 0 | – | 200 / 553 / 3 901 | 83 |
| new, watch_tag closed loop | – | 117 | 115 | 2 | **0** | 0 | **0** | as open | |
| new, ringout_tag closed loop | – | 117 | 112 | 5 | **0** | 0 | **0** | as open | |

The misses are named and physical: X16 (a 240 dB/s howl to −3 dBFS in a show whose drum peaks already read −7 dBFS at arm — two
visible increments; published as a MODERATE candidate at 200 ms for the CFS tier-B policy, which cuts it), X21 seed 1 (a ring that
grows inside the cluster of the choir partial that seeded it), X17/X22 in plain watch (65/122 Hz rings, by design uncut until the LF
window is declared — `_tag` catches both), X7 in *forced* ring-out mode (an established ring with no master steps to probe; the scene
raises `PROGRAMME_PRESENT`, and in watch mode it is cut at 0.8 s). Scenarios that still fail on *latency* alone: S3 (one seed 700 ms),
S10/S11a (300–500 ms: 7–13 dB/s rings deliver their 6 dB of own rise 6/R s after visibility), S13/X8 (1.5–5 dB/s rings: seconds), X9 (ring
exactly between two bands under speech, 1.0–1.4 s), M3 (one seed 550 ms). X11 — the amp-clipped howl every competition design
missed — 3/3 at 151–203 ms.

**Unseen programme.** Hold-out seeds 4–9, watch and watch_tag: **0 FP** (232/234 caught with the LF tag); never-run seeds 10–15:
**1 FP** (a choir partial at 1.13 kHz rising 6 dB, one seed). **Analyser variants** (attack constant ×2, biquad bank, skirt order 2 and 5,
`decay` 4 s, release law, RMS detector, noise ×1.5, peak-hold): **0 FP on every one**; under peak-hold the plateau scenes are missed and
`PEAK_HOLD_SUSPECTED`/`FROZEN_LINES` are raised (the pref is forced off at arm; the flag is for when someone turns it back on). Display
gain +12/+24 dB: 18/25 FP (winner as delivered 53/310) — whistles, crowd whoops and ff soprano pushed to within 6 dB of display full
scale; `set_rta_source` now pins the gain (PR `rta-ballistics`), and whether the pref reaches `/meters/15` at all is item 1 of §8.

**Adversarial corpus** (the 55 auditor breakers + 13 verifier breakers, all written to defeat these designs): 163 detections on
programme (winner as delivered: 505), every one inside the classes `docs/DETECTOR.md` §7 argues are irreducible on a magnitude-only
1/10-octave analyser and lists with what the detector does instead (§1.8); the fast-howl-to-quiet-plateau breakers are published as
MODERATE candidates within 250 ms (17/18) with the fields the tier-B policy needs; the held-howl breaker (excess 4 dB into a limiter,
AV05) that the first build filed as a false cut now goes −3 → `held` → −6 → confirmed dead, 3/3. Closed loop over all 68: what a wrong
cut costs is one GEQ band at −3 dB, walked to −9 only when the programme line keeps *rising* after the cut or was cut as LOUD and held.

**Cost.** 287 / 714 / 899 µs per frame (mean / p99 / max) in pure Python 3.13 on busy music; a 48 000-frame soak shows no structure
growing.

**Same harness, all seven detectors** (the shipped one, the five competition designs as delivered, the final build), blind
construction, watch mode, identical scoring including the survived-cut verdict:

| detector (blind `FeedbackDetector(cfg, band_hz)`, watch) | main 61: pass | caught / 117 | **FP** | HARM | lat p50 / p90 / max ms | ≤300 | closed: caught | **alive after cut** | cuts | hold-out 4–9: caught / 234 | **FP** | adversarial 68: rings caught | **FP** | closed adv: alive |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| shipped detector (a408a2a) | 12/61 | 108 | **948** | 143 | 252 / 602 / 12649 | 70 | 96 | **1** | 821 | 215 | **2189** | 113/120 | **1338** | 1 |
| competition: predicates (winner as delivered) | 48/61 | 105 | **0** | 0 | 202 / 702 / 4252 | 77 | 106 | **0** | 108 | 212 | **2** | 58/120 | **522** | 0 |
| competition: free-hand | 55/61 | 117 | **0** | 24 | 100 / 400 / 2948 | 100 | 113 | **0** | 122 | 227 | **1** | 79/120 | **725** | 0 |
| competition: track-and-group | 51/61 | 111 | **0** | 0 | 152 / 447 / 2502 | 94 | 111 | **0** | 113 | 220 | **11** | 80/120 | **553** | 0 |
| competition: minimal-delta | 51/61 | 107 | **0** | 0 | 152 / 650 / 2099 | 90 | 108 | **0** | 115 | 215 | **0** | 77/120 | **561** | 6 |
| competition: sequential-evidence | 47/61 | 116 | **0** | 0 | 150 / 801 / 6452 | 84 | 118 | **0** | 122 | 233 | **22** | 100/120 | **599** | 9 |
| **final build (review/detector)** | 49/61 | 109 | **0** | 0 | 200 / 752 / 3901 | 81 | 108 | **0** | 114 | 220 | **0** | 73/120 | **179** | 6 |

Reading it: every redesign removes the false positives on the shared corpus; what separates them is unseen programme and hostile
programme. The final build is the only detector with **zero** false positives on the main corpus, the hold-out seeds *and* the fewest
on the 68 hostile scenarios by a factor of three to four (179 vs 522–725), with no harmonic cuts and no detected ring left alive on the
main corpus. Free-hand as delivered is ~100 ms faster at the median and catches all 117 main-corpus rings blind, at the price of 725
detections on hostile programme, 24 harmonic cuts and the structural miss its auditor found (a fast howl below −10 dBFS is never cut);
sequential-evidence catches the most hostile-scenario rings (100/120) at 22 hold-out false positives. The final build's eight blind
misses on the main corpus are the ones itemised above (tier-B, LF declaration), i.e. they are CFS-policy decisions rather than detector
failures; its six "alive" hostile cases are AV08/AV09 — ring-out armed into a system already howling under a limiter or 2:1 compressor,
where the detector cuts once and files `held` and the product's ring-out VERIFY loop (and the back-off probe on `review/detector-cfs`)
does the deepening the harness's detector-only closed loop cannot model. For a system that writes into a live PA the ordering is: no
wrong cuts first, then kill what you cut, then speed — which is the ordering this table ranks the final build first on.


**End to end against the FakeDesk** (`scripts/verify_watch_fakedesk.py`, the project's new `/verify` recipe; in-process UDP): a
20 dB/s ring at 2.4 kHz is cut −3 dB at 1.05 s, verdict `confirmed` (drop 6.8 dB), ring at −104 dBFS; a family-less line arriving at
−6 dBFS and held is cut at 3.4 s and, answering each cut with exactly the bell, goes `held` → −6 → `held` → −9 → stop at the cap — the
bounded price of deepening limiter-held howls. Identical on the fully integrated branch (two-phase notch + `note_cut`).

### 1.7 Answers to the brief's four questions

**Q1 — What property unifies "growing" and "already-sustained" feedback and excludes musical content?** Not one property; a
conjunction, and the conjunction is different in kind from "growth". A feedback mode is (a) *narrowband and stationary in
frequency* — one band, or a fixed pair of adjacent bands with a constant split, for its whole life (vibrato, glides and formant
movement violate this; a room mode does not); (b) *level-autonomous* — its envelope is either a straight line in dB (growth or
decay at excess/τ) or a plateau whose frame-to-frame spread is that of a deterministic tone (≲ 1 dB with PEAK ballistics), and in
neither case does it co-move with the broadband programme level (a sung note, a synth pad, a crowd swell all rise and fall with
their own bed; a ring does not fall when the band stops); (c) *without a harmonic family* — no partials at +10, +16, +20, +23
bands that were born with it and move with it (every pitched instrument has them; a ring acquires 2f/3f only once it clips, and
then they appear *late* and *weaker*, which is distinguishable from a note whose partials arrive together); (d) *persistent beyond
musical time* — still there, unchanged, after any note would have decayed or moved (hundreds of ms at HF, seconds at LF where the
analyser itself is slow); and (e) in `ring_out`, *caused by us*: it appears or steepens within one loop-delay-plus-analyser-τ of
our own +1 dB step and its rate scales with the excess we just added. "Growing" and "sustained" are the same object seen before
and after saturation; (a)–(d) hold for both, growth rate holds for neither reliably. What excludes the sustained *musical* cases
that survive (a)–(d) — organ pedal, a held synth sine, an 808 — is (b)'s co-movement test over a window longer than a bar plus (c);
what nothing on a 1/10-octave magnitude analyser excludes is a pure, unaccompanied, unmodulated sine held for seconds in a silent
room (a tuning reference, a sine pad intro). That residual is irreducible without phase or without the probe (e); the design makes
it explicit: in `feedback_watch` such a line is reported, not cut, unless it is also ≥ the level at which a ring would be
dangerous *and* nothing else is playing — see §1.5 for the exact lanes.

**Q2 — Is ~15 s of pre-roll calibration sound, given `ring_out` then changes the gain structure?** A per-band baseline is the
right idea and the wrong lifetime. Three fixes make it sound. (i) It must be *per band and robust* (median and MAD or a high
percentile per band, not a mean in dB, not a single broadband number) and it must be established from the live stream inside the
consumer, after the RTA source is verified — never as a separate blocking phase (§4.3). (ii) It must be *gain-compensated*: the bus
RTA tap is pre-fader, so raising the **bus master** does not move the programme at the tap at all — only energy that has been
round the loop rises — which means that in `ring_out` on a bus the baseline needs **no** compensation for our own steps, and any
band that rises when we step the master is loop energy by construction (this is the strongest discriminant the system has and the
current code does not use it). Raising a *channel* fader or send does move the tap; those moves are known to the server
(`desk.write` events) and shift the whole baseline by the same dB, so they are compensated exactly. Moves made on the console
surface arrive as `/xremote` pushes and are compensated the same way; unseen moves show up as a *common-mode* shift of all bands,
which the co-movement statistic already discounts. (iii) It must *track*: a rolling window (tens of seconds, frozen for bands
currently flagged as candidates so a ring cannot teach the baseline to accept it) replaces the one-shot pre-roll, so walk-in music
starting, the room filling, the band getting louder are absorbed. With those three, "quiet PA" pre-roll is unnecessary: the first
2–3 s of frames after arming seed the baseline and confidence in it grows with time; detections in the first seconds lean on the
lanes that need no baseline (absolute danger level, probe response).

**Q3 — Should detection be frequency-limited?** Bounded below, yes; above, only softly; and the lower bound should come from the
desk, not a constant. Physics: a loop needs round-trip gain > 1, and below ~100–150 Hz three things make that rare — vocal/
instrument mic sensitivity and PA/wedge response both roll off, the channel HPF (typically 80–150 Hz on every vocal mic) removes
12–24 dB/oct, and room-mode support is narrow and position-dependent — while the analyser is at its slowest and coarsest there
(§1.2), so the false-positive cost is highest exactly where true positives are rarest. The exceptions are real (kick/floor-tom mics
into a drum fill with subs: 60–120 Hz rings happen; lavs and lecterns: 150–300 Hz; hollow-body guitar into a wedge: 100–250 Hz), so
a hard 200 Hz floor would be wrong for a drum-fill bus. The rule that matches practice: **lower bound = the lowest HPF corner among
the open mics feeding this bus** (readable at `/ch/NN/preamp/hpf` + `hpon`), floored at ~60 Hz and defaulting to 100 Hz when no
HPF is on; below it, never cut automatically, report only. Between the bound and ~250 Hz require the longer persistence the
analyser's τ demands anyway. No hard upper bound — 2–8 kHz is where most vocal-mic rings live (presence peaks, cymbal spill) and
10–12 kHz rings occur with condensers — but above ~10 kHz require the level lane too, because air absorption and driver roll-off
make sustained HF rings quieter and hiss/cymbal wash makes narrow HF peaks common. Sabine/dbx-style suppressors do the same thing
implicitly: their detection runs full-range but their musical-content rejection is weakest at LF, and their manuals tell users to
high-pass first.

**Q4 — Different detectors for `ring_out` and `feedback_watch`?** Same feature extraction, different *decision policy and priors* —
one detector class with a `mode`. In `ring_out` the operator has asserted the stage is quiet and the server owns the only gain
change in the room: every +1 dB step is a labelled experiment, a band that responds to the step (rises within τ_loop + τ_analyser
and keeps rising, or settles higher by more than the step) is loop energy with near-certainty, the prior for "programme" is low,
and the cost of a false cut is small (a flat-ish GEQ on a bus nobody is listening to yet) while the cost of a miss is a howl at the
next step — so thresholds are permissive, the probe lane is primary, and the sustain lane may act on lines well below danger
level. In `feedback_watch` there is programme by definition, no probe, the prior for "programme" is high and a false cut damages
the mix for the rest of the night — so the harmonic/co-movement vetoes are mandatory, the sustain lane acts only above a danger
level or with very long persistence, growth must be autonomous (not co-moving) to count, and below the LF bound and for
irreducible cases the output is a *report* ("possible ring at 315 Hz, +9 dB over baseline for 4 s, not cut: harmonic family
present") rather than a write. The M7 log is the illustration: the same 8 kHz line should be cut in 300 ms during a ring-out and
was correctly a harder call during a watch with music — where the right answer was still "cut", but *because* it sat 25 dB
prominent, dead still, family-less and un-moved by the music for seconds, not because of its growth rate.

### 1.8 What the corpus cannot tell us (residual risk for the next hardware test)

Stated plainly, because the M7 lesson is that a green suite certifies the simulator:

1. **The analyser model is a model.** τ_a = k/Δf per band, Butterworth skirts, a dB-linear release law, guessed `decay`/`peakhold`/`gain`
   semantics. The design avoids keying on the exact constants (the settle rule uses k = 1.0, twice the simulator's; 0 FP across the
   attack/skirt/detector/release sweeps), but the *first* thing to do at the desk is §8 item 2: the console oscillator stepped through
   ten bands with the bus master down, which turns every `AnalyserSettings` constant into a measurement and — via a replay loader the
   harness already accepts — lets the corpus score real frames.
2. **Irreducible on a magnitude-only 1/10-octave analyser** (`docs/DETECTOR.md` §7, each with what the detector does instead): a solo
   family-less line swelling dB-linearly (flute/soprano crescendo, sine pad fade-in, organ swell pedal, a fader ride on a held
   family-less note) *is* the ring signature — cut once, deepened only if it keeps rising; a fast howl to a quiet limiter plateau *is* a
   sine-lead onset — published in 250 ms, cut once by tier-B, deepened slowly with an alert if it holds; a loud steady whistle or ff
   closed vowel at the loud line *is* a howl at its limiter — cut, `false_cut` when it ends; the at-arm pair (organ note / projector
   whine vs established howl at −30…−40 dBFS) — resolved by the probe in ring-out, gated to tier-A in watch, alert otherwise; an LF howl
   that plateaus within two settle times; a ring exactly between two bands under speech (~1 s); slow rings inside chords (seconds);
   exact-octave co-onset modes with equal excess; a ≥ 150 ms family-less acoustic attack. None of these is fixable by tuning; they are
   the reason the CFS layer has tiers, alerts and `note_cut()` verdicts rather than a threshold.
3. **Real mixes are denser than the beds.** 0 FP on the corpus and on 68 hostile scenarios is necessary, not sufficient; the first
   `feedback_watch` with a band playing should run with a generous `notch_budget` of 0 (alert-only) for one song and the
   `detection_log` saved — that log is the first labelled real data, and the corpus should be re-scored against it.
4. **The kill side is modelled, not measured.** The corpus's loop is a scalar single-delay comb with a hard plateau; the FakeDesk's
   ring is simpler still. Whether −3 dB at the nearest ISO band kills a real ring between GEQ centres (the RBJ bell says −1.5…−2.2 dB
   at the line) is §8 item 3. The `held` → deepen path exists precisely because the answer will sometimes be no.
5. **The probe assumes the +1 dB step arrives.** Over Wi-Fi a lost master write means the detector correlates against a step that
   never happened; the read-after-write settle (PR) bounds this for the master, and a STATIONARY verdict needs two consistent 1 dB/dB
   answers, but the ring-out should be run wired.
6. **Latency on slow and mid-band-split rings is physics, not a bug**: 6/R seconds for an R dB/s ring that must show 6 dB of its own
   rise. In ring-out the probe pre-empts most of these (X23's second mode is cut 1.25 s *before* it crosses threshold); in watch they
   are the operator's, with an alert on the strip from 250 ms.

---

## 2. Assumptions baked into the tests

### 2.0 Method and the shape of the problem

Eight readers each took a slice (a module, its tests, and the research doc that governs it), plus one agent that walked every
`UNCONFIRMED` in `docs/research/` looking for code that silently depends on the unconfirmed reading, and one that read
`fakedesk.py` top to bottom purely as a *model of the world*. Two skeptical verifiers per finding (one checking the citations,
one arguing the real-gig consequence). **114 findings; 81 confirmed, 31 partially confirmed (real but narrower or milder than
claimed), 2 refuted.** Most came with a committed failing test in the agent's worktree.

The nine M5/M7 defects were not nine bugs; they were one bug — *the emulator and the fixtures were written from the same
beliefs as the code, so the loop closed on itself* — showing up nine times. The audit found the same loop in five more places:

1. **The analyser.** Fixtures model `/meters/15` as memoryless, absolute, always-flowing, always pointed where we pointed it,
   with a floor at −50-odd dB. The real one has ballistics (decay, peak-hold), a manual gain stage of unknown effect, a floor
   at exactly −128, a source that four other actors can move, and a lease that lapses silently.
2. **The loop.** Fixtures inject rings at a fixed 10–20 dB/s from below the bed and kill them with a brick-wall cut on the
   exact band. Real rings grow at excess/τ (200 dB/s per dB on a wedge), start wherever the excitation is, sit between GEQ
   centres, and see a bell-shaped cut.
3. **The console.** Fixtures treat strips as independent mono things with one insert each, FX type loads as instant, DCAs and
   mute groups as inert, Main LR as one GEQ side. The desk links pairs, gangs faders, takes time to load an effect, and runs
   Main LR through *both* sides of a dual GEQ.
4. **Other hands on the desk.** Every fixture is a single actor. At a gig there is an engineer on the surface, X32-Edit on a
   tablet, musicians' phone apps (the desk serves at most four `/xremote` clients and drops the fifth silently), and Solo Priority.
5. **The network.** The fault model loses *replies* only; datagrams *to* the desk always arrive, in order, and are applied
   before the next one is read. UDP on venue Wi-Fi loses both directions, and §5 is about the last clause.

### 2.1 Findings that change what the code does on a real desk

Severity is the verifiers' consensus. **PR** = addressed on a review branch; **rec** = recommendation with a test recipe (the
agents' failing tests are named so they can be lifted).

#### A. The analyser is not what the fixtures say it is

| # | Assumption (where encoded) | Reality (source) | At a gig | Status |
|---|---|---|---|---|
| A1 | `/meters/15` keeps analysing the bus we selected for the whole session; a failed `/-stat/rtasource` read-back is informational (`fakedesk._mirror_rta_stat` is instant and unconditional; `cfs._arm` stores `verified` and proceeds). *high, found by 4 slices* | meters.md §5.3: the source is a console **preference** — encoder 6, X32-Edit following the selected channel, Solo Priority ("replaces the RTA source with the solo bus … affects the RTA tab"), possibly the displayed GEQ page (UNCONFIRMED); §5.2: stale after a console reset. | Engineer PFLs a channel or opens FX5's GEQ page to watch the cuts: the detector scores another signal and writes −3…−9 dB into the wedge GEQ for peaks that bus never had; `ring_out` raises a bus it is no longer hearing to the ceiling. Reproduced: retarget mid-watch → band 22 cut, conf 0.97. | **PR `cfs-write-safety`** aborts on `/-prefs/rta/source` / `/-stat/rtasource` pushes and refuses to arm (`RTA_UNVERIFIED`, after one retry) when the read-back does not verify. rec: pin `/-stat/screen` to METERS/RTA at arm (meters.md §5.3 "safe practice"). |
| A2 | Every band carries broadband energy tens of dB above the floor, so prominence over the ±3 median is a bounded, meaningful number and 12/25 dB thresholds discriminate (SyntheticRta min over 2000 frames = −64 dB; no fixture ever contains −128). *high* | meters.md §4.2: silence is exactly 0x8000 = −128.0; HANDOVER §4a: "values were all floor". Against pinned neighbours any audible isolated band is 60–90 dB "prominent"; the M7 "60 dB-prominent ring" is that, not a property of feedback. | In the quiet room a ring-out requires, the override is "any narrowband sound ≥ −45 dBFS for 300 ms": a tuner tone, phone notification, projector whine, a held organ note (fundamental *and* each harmonic separately) → cut. Reproduced: −40 dB steady tone amid −128 → notched at frame 5, prominence 88. | Addressed by the discriminator redesign (§1.5: baseline excess + harmonic veto + level floor per band); corpus scenario `C0_silent_room` + critic's pinned-floor variants. |
| A3 | The stream has no ballistics and absolute levels: `decay`, `peakhold`, manual `gain` can stay wherever the operator left them; VERIFY's "6 dB in 1.5 s" measures acoustics (SyntheticRta memoryless; fakedesk stores the prefs as inert state). *medium ×5 slices* | meters.md §5.1 lists all three as prefs of the same class as auto-gain, and M7 proved that class reaches the stream. Unit of `decay` presumably seconds (0.25–16); effect of `gain` UNCONFIRMED "probably does". | Peak-hold on: a correctly killed ring never falls on the display → VERIFY fails → −6, −9 into a tamed band → ABORT with back-off, every run. Gain +60: everything 36 dB hotter, the −45 gate passes room noise (M7 §4b#3 returns); gain 0 vs M7's unknown value: a ring must be that much louder to qualify. | **PR `rta-ballistics`** forces decay→min, peakhold→OFF, reports `gain` and the found values. rec: pin `gain` to a declared value once its effect is measured (§1.7 protocol); give SyntheticRta a display stage so VERIFY is tested against ballistics. |
| A4 | Frames keep flowing; "no detection during the dwell" means "no feedback" (`_run_ringout` never consults frame age; `LiveMeters.last_rx_age_s` exists, nothing reads it; test watchdog 1 s hides that the real config took 14 s to notice a pulled cable). *medium* | meters.md §1.3/§6.6: 10 s lease renewed by fire-and-forget UDP every 5 s; §6.7 frame rate "may be variable". | Wi-Fi burst loss / lease lapse: +1 dB per dwell with a blind detector to the target; "no feedback detected; DONE" while the wedge howls. Reproduced. | **PR `cfs-write-safety`** (fresh frame required per raise; hold, then abort). |
| A5 | The frame source is already streaming when a session opens, so arm-time calibration can sample it in `_open_session` (true only for an always-ticking injected SyntheticRta). *medium* | `LiveMeters` starts in `_arm`; at HEAD calibration never runs against a desk and the log never says so (§4). | The single-room −45 gate applies everywhere, silently. | §4 / §1.5 (per-band baseline inside the consumer). |
| A6 | Averaging RTA frames arithmetically in dB gives "the level" (`average_frames`, floor calibration). *low* | Mean of dB = geometric mean of power; for a fluctuating band it reads several dB low (−79 for −30 dB speech in the agent's probe). | Under-estimates programme; a floor derived from it is optimistic. | rec: power-average, or use percentiles (which the baseline design does). |

#### B. The loop is not what the fixtures say it is

| # | Assumption | Reality | At a gig | Status |
|---|---|---|---|---|
| B1 | Feedback grows at a fixed 10–20 dB/s injected by fiat, independent of the fader (SyntheticRta `growth_db_per_s`; fakedesk "bus fader deliberately not coupled to ring growth"), so > 60 dB/s is a note onset and one −3 dB step kills "any ring slower than 60 dB/s". *medium ×3 slices* | Growth = excess/τ: 1 dB past unity is 200 dB/s on a wedge at 1.5 m, 40 dB/s at FOH 8 m (§1.3). M7's successful catch came through the override, consistent with real growth lying outside 6–60. | On wedges (the primary ring-out target) every ring scores growth 0 and is cut only by the override at −10…0 dBFS — a full-scale howl for 300–650 ms per notch, three times per ring-out; with `override_prominence_db: 0` a wedge ring is never detected at all. | Discriminator redesign (§1.5); corpus models loop physics (`FeedbackRing`: excess, τ, saturation, GEQ bell). rec: port the loop model into `SyntheticRta`/FakeDesk so the integration tests stop certifying the 6–60 window. |
| B2 | A GEQ band is a brick wall exactly three RTA bands wide centred on the ring; the ring sits on an RTA centre; a cut ≥ the per-frame growth kills it next frame (`SyntheticRta.set_geq_gain`, `level -= cut` per frame). *low ×3* | RBJ bell: −3 dB slider = −1.5…−2.5 dB half-way between centres (§1.3); decay after a cut is (attenuation − excess)/τ. | Rings on bands ≡ 5 mod 10 (442, 884, 1768, 3536, 7071 Hz) need two flanking cuts or escalate to −9 and "not tamed"; VERIFY timing fitted to an instant model. | rec (§1.6): sub-band frequency estimate → flank-pair cuts; measure the desk's bell once (§1.7). Corpus uses the bell. |
| B3 | After a +1 dB step a ring declares itself within the 1.5 s dwell (fixtures inject it 11 dB above the bed at 20 dB/s). *low* | Near threshold the loop is *sub*-critical for several steps with regenerative gain 1/(1−g) and long ring-down (§1.3 table); at LF the analyser adds 100–400 ms. | Marginal modes are stepped past; the first detection happens 1–2 dB above onset instead of at it. | rec: probe logic in `ring_out` (§1.5); dwell ≥ analyser τ at the lowest band in the window. |
| B4 | The bus master moves the wedge dB-for-dB and muting silences it, so preflight need only check "muted" and "master" (no `/outputs` in device.yaml). *medium ×2* | fx_routing_scenes §4.7/§11: physical outs can be tapped IN, PRE, EQ→, POST; only POST/+M follow fader and mute. | On a PRE-tapped wedge out `ring_out` climbs to the ceiling with zero acoustic effect and certifies a monitor that was never tested; `panic()` does nothing for it. | rec: add `/outputs/*/{src,pos}` to device.yaml; preflight blocker "Bus N feeds XLR 7 tapped PRE — the master does not control it"; connect-time warning for panic (S23). |

#### C. The console is not what the fixtures say it is

| # | Assumption | Reality | At a gig | Status |
|---|---|---|---|---|
| C1 | **Main LR is a mono strip on one side of a dual GEQ2** (`FX5L` = side A); side B is free for another bus; a side-A notch fixes "the PA" (`plan_setup` pairs `main` like any bus; `fakedesk._refresh_cuts` applies side-A pars to the whole mono SyntheticRta; `test_cfs.py:136` asserts exactly this plan). *high, found independently by 3 slices* | fx_routing_scenes §2.1: GEQ2 par 1–31 = side A = **L input**, 33–63 = side B = **R input**; §3.4: "a stereo effect needs both FXnL and FXnR … main L/R uses the pair when `sel = FXnL`". The M7 datum fits: a −15 dB PRE cut read −5.9/−6.0/−3.9 on the RTA ≈ 20·log₁₀((1+10^(−15/20))/2) = −4.6 dB — one leg cut, mono-summed analyser. Jim's desk has GEQ2 in FX5 on Main LR *today*. | `ring_out('main')` notches the **left stack only**; the right keeps ringing; VERIFY sees ~1.4 dB per −3 step, escalates to −9 on L (lopsided PA), aborts. Worse: `plan_setup([main, bus3])` hands FX5R — Main R's graph — to bus 3, so bus 3's "notches" land on the right PA stack: a write to a strip not under test. | **rec, do before the next Main LR ring-out**: for `main.st` (and any bus whose `/config/buslink` pair is ON) load the *stereo* GEQ/TEQ (Desk.set_geq_band already drives both channels for stereo types) or reserve and write both sides; `validate_ringout_eqs` must treat a dual slot on a stereo strip as fully occupied; FakeDesk needs a two-leg main (RTA = power-mean of both sides' gains). Failing tests exist in the agents' worktrees (`test_assumptions_provision.py`). |
| C2 | Strips are independent: bus 1's fader/mute/GEQ side move bus 1 only (`/config/buslink`, `linkcfg/fdrmute` stored inert in the fake; preflight never reads them). *medium ×2* | scales_params §10: linked pairs share fader+mute by default; stereo wedges/IEMs are routinely linked odd/even. | `ring_out(1)` on a linked 1-2 pair drags Bus 2's wedge up in lock-step, unwatched and un-notched; the report says Bus 2 untouched. | rec: preflight blocker (or paired session) when the pair bit is ON; fake mirrors linked writes. |
| C3 | Writing `/fx/N/type` is instant and atomic; the next datagrams may insert the slot and read `/fx/N`+`/fx/N/par` back (fake swaps type synchronously, keeps pars). *medium ×2* | fx_routing_scenes §2: a type load re-initialises 64 pars; latency UNCONFIRMED but it is the slowest thing the DSP does. | A correct `setup_ringout_eqs` returns `GEQ_VALIDATION_FAILED` ("slot 5 holds DES2") or parses the old effect's pars as GEQ dB ("+20 dB boost") — HANDOVER §4b's false failure, from the other end. | **PR `read-after-write`** (validate re-reads until settled; "not yet verified" instead of failed; FakeDesk apply-delay incl. type-load par reset). |
| C4 | A bus needing a ring-out GEQ has nothing else in its one insert point; a free GEQ2 slot is flat; an existing GEQ insert is reusable whatever its `insert/pos` (only inserts *we* create are forced PRE — so on Jim's own desk, FX5 POST on Main LR is reused as-is and validates OK). *medium ×3* | One insert per strip (§3.4); idle slots hold last week's curve; M7 §4b#4: POST inserts are invisible to the tap. | `setup` silently evicts an IEM bus's hearing-protection limiter (the Tier-2 prompt says only "insert FX5L on bus.3"); a reused slot brings −12 dB holes or +6 dB boosts onto a live wedge; on the M7 desk itself VERIFY cannot see its own cuts. | rec: `plan_setup` must (a) name what an insert replaces in the confirmation and block LIM/known-protective types unless forced, (b) flatten or refuse a non-flat slot it loads, (c) treat `pos != PRE` on a reused insert as a fix-up write (confirmed), not "already set up". FakeDesk: apply GEQ cuts to the RTA only when `pos == PRE`. |
| C5 | The head amp behind a channel can be computed client-side from `/config/routing` + `/config/userrout` with UIN block *k* always feeding input block *k* (the M5 fix); "resolves to an index" == "a physical mic exists"; aux-jack/USB/card sources are never mics (research §12.4 algorithm UNCONFIRMED; fake and test derive expected values from the same inference). *medium ×4 slices* | The desk publishes the answer: `/-ha/NN/index` (scales_params §12, not in device.yaml). Routing pages allow "Inputs 9-16 ← User In 1-8". A radio hand-held on Aux In 1 is the most feedback-prone source on a small stage. | `set_phantom('ch.9')` puts 48 V on the wrong XLR (a ribbon mic, a DI) with a thump through an open channel; `discover_mics` calls card feeds physical and drops the radio mic ("AUX1 has no preamp") → "no candidate mics" on a bus one push from howling. | rec: add `/-ha/NN/index` to device.yaml, read it first, fall back to the algorithm only on timeout; treat aux-in strips/sources as candidate mics; FakeDesk serves `/-ha` from an independent table. |
| C6 | A strip's audible state is its own `mix/on` + fader (DCAs, mute groups inert in the fake); every input carries programme at ≈ −12 dBFS (no such thing as an unplugged input). *medium* | DCA mute/level and mute groups gate the strip; HANDOVER §4b: `discover_mics` reported 7 candidates with one mic plugged in. | `ring_out` runs "with 4 mics" whose DCA is down for the changeover: master to the ceiling, nothing rings, report certifies headroom; DCA up at showtime → howl at a certified level. | rec: fold DCA/mute-group state into `include`; annotate candidates with input meter level (`/meters/1`, §6) and warn "no signal — probably unplugged". |
| C7 | Scene recall is verified by polling `/-show/prepos/current` and show control is SCENES (fake default); a recall never touches FX types, inserts, GEQ bands or the bus being rung out; nobody presses GO mid-session. *medium ×2* | Jim's desk is in **CUES** (HANDOVER §4a); a re-recall of the current scene (the commonest "reset") is unverifiable by pointer; snippets on song changes reload FX and inserts. | Every recall on the CUES desk is "cannot be verified"; a snippet mid-watch turns FX5 into the vocal reverb and the next "deepen" writes into it (caught only by the type push after **PR `cfs-write-safety`**). | partly PR; rec: verify recall by content (re-dump a few sentinel sections), not pointer. |
| C8 | Strip and scene names never contain `"` (our writers replace it) and are 12 *characters* of anything; DeskState round-trips are exact. *medium/low* | A floor-tom named `12" Tom` typed on the console reads back with shifted columns → source None → "not a physical input" → dropped from mic discovery; a restore renames it. | Wrong candidate list; silent rename. | rec: quote-aware tokenizer for the *first* string field; `format_token` refuses quote/newline (S26). |

#### D. Other hands on the desk

| # | Assumption | Reality | At a gig | Status |
|---|---|---|---|---|
| D1 | Nobody touches the bus during `ring_out`; nobody touches the GEQ during a session; nothing needs to stop a ramp/restore/ring-out except another of our own ramps; `panic()` is the last word. *high ×3* | The first thing a human does when a wedge howls is grab its master; the engineer works the same GEQ by hand during soundcheck; MCP is turn-based so `panic` cannot even be issued during a 60 s ramp or a minutes-long `ring_out` tool call. | +48 dB jump after a pull-down; +6 dB "cut" over a hand-deepened band; motor fader fights the hand for the rest of a ramp; restore re-opens what panic muted. | **PRs `cfs-write-safety`, `panic-hardening`**. rec: pushed fader moves should supersede a running ramp on that address; long tools (`ring_out`, `restore`) as background sessions like `feedback_watch` so `panic` stays reachable from the same client. |
| D2 | The ≤ 2 s node cache is a safe base for relative moves because `/xremote` pushes invalidate it (tests: ≤ 2 clients on a lossless socket). *high* | transport.md §2: at most **four** push clients, the fifth is ignored silently; pushes are plain UDP. X32-Edit + iPad + two musicians' phone apps = we are fifth. | Musician pulls his IEM send −10 → −40 on his phone; `adjust_send +3` within 2 s of our last read writes −7: +33 dB into his ears, and the ±6 dB guard is defeated because the delta rides a fictional base. Reproduced with 4 dummy clients. | **PR `read-after-write`** stops caching a section for 0.5 s after our own write and fixes the in-flight-join hole; rec: relative moves GET the leaf fresh (one round trip) rather than trust any cache; detect "not registered" (no push seen for N s while others clearly move things) and say so. |
| D3 | The console's RTA and its prefs belong to the server for the duration and after; `restore_snapshot` undoes the session. *medium* | `/-prefs/rta/*` writes go through the raw connection (no snapshot entry); nothing restores source/autogain/det at disarm. | Engineer's RTA left auto-gain OFF, PEAK, on a wedge bus, permanently; the "undo" does not undo it. | **PR `rta-ballistics`** records `prefs_before`; rec: restore them at disarm. |

#### E. The network only loses replies

| # | Assumption | Reality | At a gig | Status |
|---|---|---|---|---|
| E1 | Every datagram we send arrives; only replies are lost (`FakeDesk.drop_next` drops replies; no inbound-loss fault exists), so a sent panic mute / notch / restore line was applied. *medium ×3* | UDP both ways; venue Wi-Fi. | "PANIC: 24 outputs muted (sent)" with Main LR still live; a notch that never arrived is VERIFY-failed into −6/−9; `restore` reports `failed=[]` with a wedge send missing. | **PR `panic-hardening`** reads back and re-sends; **PR `read-after-write`** adds `drop_inbound` to FakeDesk and verifies setup; rec: key `/` echo waiters on the exact line text (today any echo acks the oldest pending `slash()` — reproduced: a lost restore line is reported written). |
| E2 | A reply always arrives within the per-attempt timeout, so "oldest pending waiter for this address" is the right owner (no request ids in OSC); every command is idempotent so `request()` may re-send anything; `/save` answers as fast as a GET. *low* | A late reply to a timed-out GET satisfies the *next* waiter for that address (wrong value attributed); re-sending `/save`, `/load`, `/-action/*` on silence repeats a non-idempotent action. | Rare wrong read; a double scene save. | rec: drop replies older than the current attempt generation per key; never auto-retry `/save`/`/load`/`/-action`. |
| E3 | We are registered for pushes because we *sent* `/xremote` (heartbeat_ok set on send); the desk always has a free client slot; it comes back at the same IP; whatever answers `/info` with four strings is a supported X32 on tested firmware (no model/firmware check against `descriptor.meta`). *low* | Four-client cap; DHCP after a power cycle; X32 Compact/Producer/M32 differ in strip counts and node widths (UNCONFIRMED FW 4.x columns). | Silent loss of pushes (→ D2); reconnect loop probing a dead address after the PSU reboot Jim plans to test; a Compact accepted as a full X32. | rec: warn when no push has been seen for N s while our own reads show changes; re-run discovery on reconnect failure; compare `/xinfo` model/firmware with `meta` and refuse or degrade explicitly. |

#### F. Fixtures that closed the loop on themselves

* **Node text.** `fakedesk` renders `/node` replies with *our own* `nodes.py` formatter, so parser and fake share bugs and the
  "golden line" tests compare us with us. The only external truth in the suite is the handful of verbatim desk lines quoted from
  transport.md §6.4 / scales_params. **The single highest-leverage fixture this repo lacks is a committed `dump_desk_state` JSON
  from the FW 4.13 desk** (`snapshots/` is empty in git): every parser, restore-plan and diff test should run against it.
* **Head-amp resolution.** The test's expected indices come from the same inference as `Desk.headamp_index_for` (C5).
* **RTA source.** The fake serves the one SyntheticRta for `/meters/15` whatever the source, so `verified` is true in every test
  and un-verifiable in principle (A1).
* **Closed loop.** The fake's GEQ→RTA coupling is a brick wall on the mono spectrum regardless of insert `pos` or stereo legs, so
  the M7 POST-insert defect and the Main-LR-one-leg defect (C1) are both *unrepresentable* in the test world even after being
  found on hardware.

### 2.2 The `UNCONFIRMED` ledger

The hunter walked all ~60 `UNCONFIRMED`/`UNCERTAIN` items in `docs/research/`. Code depends on the unconfirmed reading, with no
runtime fallback, in these (highest consequence first): whether `/-prefs/rta/source` alone re-points the live analyser vs
`/-action/setrtasrc` (A1); whether `gain`/`decay`/`peakhold` reach `/meters/15` (A3); which analysis `/meters/15` carries while a
GEQ page is displayed (A1); the §12.4 head-amp algorithm and UIN block mapping (C5); what `sel = FXnL` on a stereo strip does
(C1); FW 4.x node column widths (parser tolerates extra columns — verified — but a *missing* column silently drops the section
from `dump()`); the `/` echo ⇒ applied question (§5); `/meters/9` within-slot order (unused); mute-group behaviour after a panic.
Each is a five-minute measurement on the desk; §1.7 and §5.4 fold them into one checklist.

### 2.3 Ten realism switches for FakeDesk (default off; each named with the test it breaks today)

1. `rta_follows_source` — `/meters/15` content depends on the resolved source, Solo Priority and (optionally) screen (breaks every CFS test that never checks `verified`).
2. `rta_display_stage` — PEAK/RMS, `decay` release, `peakhold`, `gain` offset applied to SyntheticRta from the fake's own prefs (breaks `test_ring_out_closed_loop` VERIFY with peak-hold on).
3. `silent_floor` — quiet bands pinned at exactly −128 (breaks every prominence threshold test).
4. `loop_physics` — ring growth = (master + GEQ bell at f − unity)/τ, saturation, comb regeneration (breaks the growth-window tests; the `rtasim` `FeedbackRing` is the ready-made model).
5. `stereo_main_and_links` — two-leg Main LR through GEQ2 sides A/B; `buslink`/`linkcfg` gang faders and mutes (breaks `plan_setup(['main'])`, `ring_out(1)` on a linked pair).
6. `apply_latency` — per-address-class delay before a write is visible, FX type load resets 64 pars after N ms (**PR `read-after-write`** adds this one).
7. `inbound_loss` — drop the next n / a random fraction of datagrams *to* the desk (**PR** adds `drop_inbound`).
8. `xremote_slots` — pre-occupy 4 client slots; push nothing to us (breaks `adjust_*` base assumptions).
9. `outputs_taps` — `/outputs/*/{src,pos}` with PRE/POST semantics feeding output meters and the ring model (breaks "panic silences everything", "master controls the wedge").
10. `operator` — a scripted second actor: pulls a fader mid-ramp, presses GO on a snippet, solos a channel, edits a GEQ band (breaks every single-actor CFS test; the PRs' new tests are the first of this kind).

---

## 3. Adversarial review of the safety layer

### 3.0 Method and headline

Six attackers, each with a different angle (write-path bypass inventory; confirmation tokens; clamps / show mode / argument
abuse; `panic()`; CFS² autonomous writes; the MCP tool surface as a confused LLM sees it), each required to *run* its exploit
against the real code on a FakeDesk. Every finding then went to two independent verifiers — one re-deriving the exploit from
scratch in its own worktree, one trying to refute the causal chain from source. **58 findings; all 58 confirmed by at least
one verifier, 0 refuted**; consensus severities: 7 critical, 14 high, 28 medium, 8 low, 1 informational. They collapse to
the ~25 distinct issues below (several were found independently from two or three angles, which is itself evidence).

What held (verified, not just asserted): token action+payload binding and single use; Main LR/M-C fader, mute and sends
unreachable from Tier-1 tools (`GUARDED`); `/*/insert/*`, `/fx/*/type`, `/*/config/source`, head-amp phantom/gain guarded; EQ gain
clamp ±15; `panic()` exempt from the rate limiter and show mode; NaN rejected by `_num`; `+inf` clamped to ceilings; the
FakeDesk closed loop.

### 3.1 Findings (deduplicated), with status

Legend — **PR** = fixed on a review branch (§0.3), with a regression test that fails on `main`; **rec** = recommendation only.

| # | Sev | Finding (reproduced) | Where | Status |
|---|---|---|---|---|
| S1 | **crit** | **Main LR/M-C compressor make-up (+24 dB) and 6-band EQ (+15 dB/band) are Tier 1.** `set_comp("main", on, thr 0, ratio 1.1, makeup 24)` is an unconfirmed, unramped +24 dB on the PA (≈+39 dB at one frequency with EQ), in show mode too. The guarded globs cover only `/main/*/mix/*`; `main` inherits the Tier-1 `dyn`/`eq` blocks. Found from three angles. | device.yaml guarded/`blk_dyn_core`; desk.py `set_comp`/`set_eq_band` | **PR `guard-main-processing`**: `/main/*/dyn/*`, `/main/*/eq/*` guarded (yaml re-declares them tier 2); `policy.dyn_makeup_max_db` = 6 clamps Tier-1 make-up on every strip, reported like a fader clamp. rec: asymmetric EQ boost limit on output strips. |
| S2 | **crit** | **`panic()` is defeated by the server's own in-flight writers.** A confirmed `restore_snapshot` running when panic fires re-opens all 24 outputs within 0.3 s; fader ramps (up to 60 s) keep walking; a running `ring_out` keeps raising the muted (therefore feedback-free) bus to its target, reports DONE and parks it there (17 raises after panic observed; −20 → −3 dB) for the operator to unmute into. Three angles. | desk.py `panic`, `_ramps`, `restore`; cfs.py RAISE loop | **PR `panic-hardening`**: panic cancels ramps and bumps `panic_count` (restore aborts per line); CFS subscribes to `desk.panic.begin` and aborts synchronously, handing the master back no higher than it found it; an un-mute already parked in the pre-write awaits when panic runs is refused when it resumes. |
| S3 | **crit** | **Ring-out RAISE writes belief + step as an absolute fader value.** An operator who yanks the bus down mid-run is answered by a jump straight back up on the next step (+47 dB in one datagram demonstrated). | cfs.py `_run_ringout`/`_write_master` | **PR `cfs-write-safety`**: the master is re-read before every step; if it is not where the run left it (±0.5 dB) somebody is at the desk → ABORT, hands off (no further write, not even the back-off). |
| S4 | high | `ring_out_system(plan=None)` binds the token to `{"plan": None}` and re-derives the bus list at redeem time: the confirmed run can include buses never shown in the summary. | server.py `ring_out_system` | **PR `token-binding`**: resolved stage list bound and passed to CFS² explicitly. |
| S5 | high | `restore_snapshot` / `apply_patch_plan(include_source)` bind the snapshot id / file path, not the content: edit the file between the two calls and the confirmed action writes something else (raw Tier-2 sections included). | server.py | **PR `token-binding`**: sha256 of the file (and the source list) in the payload. |
| S6 | high | `ring_out` asks the human to confirm the open-mic list but does not bind it; a Tier-1 `set_send`/`unmute` between the calls changes which mics the run drives into feedback. | server.py `ring_out` | **PR `token-binding`**: `open_mics` in the payload. |
| S7 | high | CFS² validates "cuts only, never shallower" against its **arm-time copy** of the GEQ: a band the engineer deepens by hand to −12 mid-session is later written to −3 (a +9 dB boost); unparsable pars become 0.0. | cfs.py `_DeskGeqWriter`, `_open_session` | **PR `cfs-write-safety`**: `/xremote` pushes for the session's GEQ pars update writer and controller (`observe`); proposals deepen from the desk's value; a deeper hand-made cut is left alone; unreadable pars are *unknown* (a preflight blocker), not flat. |
| S8 | high | **Commit-before-write in `_notch()`** (the §4 root cause, reached here from two more directions): `RATE_LIMITED` from an LLM write loop draining the shared bucket, or a read timeout, lands between `plan()` and the datagram → controller says −9, desk has −6, band can never be deepened again, report wrong. | cfs.py `_notch`; detector.py `NotchController` | **PR `cfs-write-safety`**: two-phase propose → write → commit; `CancelledError` = not written; `_disarm` drains an in-flight write; writer carries the fx type so the hot path has no hidden read. |
| S9 | high | No RTA frame-liveness interlock: when frames stop (subscription lapse, Wi-Fi, desk busy) the ring-out keeps raising the master blind to the ceiling and reports DONE. | cfs.py RAISE loop | **PR `cfs-write-safety`**: a fresh frame since the previous raise is required before the next; hold up to 3 s, then ABORT with back-off. |
| S10 | high | RTA source neither required to verify at arm nor monitored: Solo Priority, the front-panel encoder or X32-Edit re-points `/-prefs/rta/source` and bus N's GEQ is notched to −9 on audio from another strip. GEQ slot type / insert likewise never re-validated (a stereo reload makes side-B writes hit shared pars; a re-patched insert sends cuts to another strip). | cfs.py `_arm` | **PR `cfs-write-safety`** (2nd commit): pushes for `/-prefs/rta/source`, `/-stat/rtasource`, `/fx/N/type`, `<bus>/insert/*` abort the session; arming is refused when `rta.verified` is false (3rd commit). |
| S11 | high | `connect(<other host>)` / `disconnect()` are unconfirmed: one call silently re-targets every later write (panic included) at another console on the LAN, or removes all control; a failed `connect` drops the working link first. | server.py | **PR `argument-hardening`**: both are a confirmation dance while a desk is connected (first connect / same host free). rec: probe the new host on a second socket before dropping the old one. |
| S12 | high | Show mode's ±3 dB guard only covers `adjust_*`; absolute `set_fader`/`set_send` accept any value with `ramp_ms=0` (−∞ → 0 dB on a live wedge). NB: no relative guard on absolute moves is a documented decision of Jim's after M5 — correct outside show mode. | desk.py `_move_level` | **PR `argument-hardening`**: in show mode only, absolute moves are held to the relative limit unless `force=true`. |
| S13 | med | `show_mode(false)` is self-service (Tier 0) and the `SHOW_MODE_BLOCKS` error text tells the model how to lift it. | server.py | **PR `argument-hardening`**: turning show mode off is confirmed. |
| S14 | med | `panic()` is fire-once and unverified: while DEGRADED it reports ok and is never re-asserted on reconnect; when connected a lost datagram is never noticed. No post-panic latch: 22/24 outputs re-openable at once by Tier-1 `unmute`. | desk.py `panic` | **PR `panic-hardening`**: latch (`PANIC_LATCHED` until confirmed `clear_panic` or confirmed `set_main_mute`); re-assert on reconnect; read-back verification with one re-send, `delivered: confirmed/partial`. |
| S15 | med | `ring_out` accepts `dwell_ms=0` and `step_db` up to 6: a confirmed ring-out becomes +20 dB to the ceiling in < 0.5 s, faster than the detector's persistence window can see. | server.py, cfs.py | **PR `argument-hardening`**: `ringout.max_step_db` 3 (refused above, before a token is minted), `ringout.min_dwell_ms` 250 (raised to, with a warning). |
| S16 | med | `ring_out_system` stage args reach CFS unvalidated: `target_gain_db=NaN` passes the dance, raises after `_arm()` outside its try/except and wedges a zombie RINGOUT session. | server.py | **PR `token-binding`**: finite-number validation before minting. |
| S17 | med | Older tokens for the same action stay live 300 s after a newer mint (a declined request can be replayed); `require_confirmation(action=None)` mints a wildcard; payload equality is Python `==` (`True == 1 == 1.0`); minting unbounded. | policy.py | **PR `token-binding`**: one live token per action, no wildcard, canonical comparison, cap 64. |
| S18 | med | `restore_snapshot` preview shows the first 15 changes in sweep order (names, channel faders) rendered snapshot→live, i.e. backwards: "Main LR −40 → 0 dB" either never appears or reads as a cut. | server.py | **PR `token-binding`** (3rd commit): hazards (outputs, head amps, routing, inserts, big upward moves) listed first, live→snapshot. |
| S19 | med | BACKOFF/abort lowering write goes through the rate limiter once; a client burst at that instant leaves the bus parked at its highest point. | cfs.py `_finalize_levels` | **PR `cfs-write-safety`**: retried on transient refusals, and lowering CFS writes take the limiter's emergency lane (`priority_writes()`). |
| S20 | med | `show_mode(true)` during a ring-out does not stop it. | cfs.py | **PR `cfs-write-safety`**: aborts the ring-out (a watch continues by design). |
| S21 | med | DCA ceiling +5 dB (channel ceiling) though a DCA raises every member; yaml says 0. | policy.py | **PR `argument-hardening`**: `dca_fader_max_db` 0. |
| S22 | med | Report ids and patch/export paths escape `X32MCP_HOME` (`get_ringout_report("../x")` reads any JSON; `export_patch_plan` writes anywhere). | cfs.py `ReportStore.load`; server.py `_patch_path` | **PR `argument-hardening`**. |
| S23 | med | "Mutes all outputs" is over-claimed: direct-out taps (P16 IEMs, record splits), AES50/card from input blocks, PRE-tapped physical outs, monitor/phones and talkback survive a "successful" panic, and `/outputs` is not in device.yaml so the server cannot even warn. | docs, device.yaml | **PR `panic-hardening`** (docs/summary now say so). rec: add `/outputs/*/{src,pos}` to device.yaml and warn at connect about outputs panic cannot reach. |
| S24 | med | Main LR (stereo) and linked bus pairs: CFS² writes one GEQ side / raises one fader of a pair — see §2, A1. | provision.py | rec (§2). |
| S25 | med | `feedback_watch` is allowed in show mode on `main` with a 12-notch budget and no timeout; with the current detector's false-positive rate that is up to 12 × −9 dB holes in the PA GEQ with no human in the loop. | server.py | rec: until the new discriminator is proven on hardware, make `feedback_watch` on `main` a confirmation dance in show mode and cap the budget; add a session max-duration. |
| S26 | low | Node-text injection via names: `restore_plan` renders string fields unsanitised; a `"` or newline in a name (typed on the console, or in a crafted snapshot) re-frames the rest of a `/ch/NN/config` line (icon, colour, **source**). | nodes.py, desk.py `label` | **PR `argument-hardening`** strips control characters in `label`. rec: `format_token('str')` must refuse quote/newline; restore should list such sections in `failed`. |
| S27 | low | Argument coercion runs before the desk's validators (`db=True` → +1.0 dB, `on="false"` → True, `int(inf)` → INTERNAL); CFS arming writes `/-prefs/rta/*` through the raw connection (no limiter slot / write event); desk- and LAN-controlled strings (scene names, `/xinfo` console name) are echoed verbatim into summaries (prompt-injection surface — cannot forge a token, but see S11/S13); INSTRUCTIONS said 60 s TTL vs 300 s in yaml. | server.py, cfs.py | TTL text fixed (`token-binding`); rest **rec**: strict pydantic types at the tool boundary; route CFS pref writes through `_RtaPrefsWriter`; fence desk-originated strings in summaries. |

### 3.2 Answers to the brief's five questions

* *Can any write reach the desk without passing Policy?* Yes, three paths, all now documented: `cfs._arm → set_rta_source(self._conn)`
  (RTA prefs, Tier 1, no limiter — S27), `cfs._restore_master` (deliberate raw fader restore after connection loss), and
  `Desk.restore` via `slash()` (rate-limited but no per-line tier/clamp check — which is why S18 matters). No Tier-2 *parameter*
  is writable without a token; the Tier-2 *effects* that were reachable were S1 (processing on the mains) and S11.
* *Can a token be reused, forged, or repurposed?* Not reused or forged. Repurposed: yes, wherever the summary described more than
  the payload pinned down (S4–S6, S16–S18) — fixed by binding what was shown.
* *Can show mode be circumvented / a clamp widened by argument choice?* Show mode: `show_mode(false)` was free (S13) and absolute
  moves ignored it (S12). Clamps: no widening found (`+inf` → ceiling, NaN refused); the DCA ceiling was simply wrong (S21) and
  make-up gain had no clamp at all (S1).
* *Any state in which `panic()` fails to silence the outputs?* It always sent the 24 mutes (0.2–0.4 ms). It failed to *keep* them
  silent (S2), did not know whether they landed (S14), and never covered non-master feeds (S23).
* *Can CFS² be induced to boost, exceed −9, write another strip, or write past budget?* Boost: yes, relative to a hand-deepened
  band (S7). Exceed −9: no. Larger than −3 dB step on the desk: yes (S8). Another strip: yes, after an insert/slot/RTA-source change
  mid-session (S10) and for stereo/linked strips (S24). Past budget: no (deepening is free by design; total writes bounded by
  cooldown × 3 steps × budget). Plus two the brief did not ask: it fought the operator (S3) and raised blind (S9).

---

## 4. The unresolved bug: calibration made `existing_cuts` come back empty

### 4.1 Root cause (reproduced deterministically, two independent investigations converging)

It is not a stale read and the GEQ write did not fail. **The write was never sent, and nothing logged it.**

1. `feedback_watch` → `preflight` → `validate_ringout_eqs` → `Desk.get_fx(5)` reads `/fx/5` and `/fx/5/par` and caches both
   sections at T₀ (`desk.py` `_read_sections`, TTL `policy.read_cache_ttl_s` = 2.0 s).
2. With calibration placed "after the frame source starts" (the only placement in which it does anything), `_calibrate_floor`
   consumes `floor_sample_frames` = 40 × 50 ms = **2.0 s** inside the arm. (At HEAD it runs *before* the frames start, times out
   after 0.3 s and changes nothing — it is dead code that adds 300 ms to every arm; commit `60b86d5` says "backed out" but
   only `device.yaml` changed. The two state attributes it needs were also added to `CfsError.__init__` instead of
   `CfsManager.__init__`, cfs.py:122.)
3. The ring is injected, detected ~0.5–1 s later. `_notch()` calls `ses.nc.plan()` (cfs.py:1073), which **commits** the cut to
   the `NotchController` (`_gains[22] = −3`, `_touched`, `_notches`) — from this instant `cfs_status().notches`, `budget_left`
   and the eventual report all show band 22 at −3 dB — and only then `await ses.writer.set_band_gain()` (cfs.py:1081).
4. `Desk.set_geq_band` first **reads** the slot type: `await self._section("/fx/5")` (desk.py:1417). At HEAD the entry is 1.1 s
   old → cache hit → no suspension → `plan → conn.set` executes in one uninterrupted event-loop step: *atomic by accident*.
   With the 2 s calibration the entry is 3.05 s old → miss → `/node` round trip → **the detector task suspends between the
   commit and the write**.
5. The test's `wait_until(len(state.notches) >= 1)` (10 ms poll) sees the *planned* notch during that suspension, runs
   `cfs_status` (passes — it reads the controller), then `feedback_watch_stop` → `stop` → `_finish` → `_disarm` →
   `task.cancel()` on the detector task (cfs.py:942) while it is parked on the `/fx/5` reply. `CancelledError` is not in
   `_notch`'s `except (DeskError, PolicyError, X32ConnectionError, CfsError)` tuple, so **no "notch write failed" warning is
   logged**; `conn.set("/fx/5/par/22", …)` never executes; nothing rolls the controller back. On Jim's machine (Windows 10,
   CPython 3.12: `time.monotonic()` is GetTickCount64 at 15.625 ms, so asyncio's `_clock_resolution` makes `sleep(0.01)`
   complete on the very next loop iteration) the poll lands in the suspension **every time**; on macOS/Linux or CPython ≥ 3.13 it
   is a 1–3 % race, which is why a naive re-application of the calibration passed here until the desk was given 12 ms of reply
   latency or the loop was given Windows clock resolution — after which it failed 5/5, at exactly and only the `already carries`
   assertion.
6. `_finish` builds the report from `nc.notches` → the stop report lists 2500 Hz −3 dB (passes). `ring_out`'s pending call →
   `preflight` → fresh `/fx/5/par` read → the desk answers 0.0 → `cuts = {}` → no "GEQ already carries cuts" warning.
   `existing_cuts` was empty because the GEQ really was flat.

So the calibration was incidental. It aged one cache entry past its TTL and exposed two pre-existing defects that HEAD only
dodges in this test because the ring arrives < 2 s after preflight:

* **Commit-before-write in `_notch()`** — any exception or cancellation between `plan()` and the datagram (a slow `/fx/N` read,
  `RATE_LIMITED` when other tools are writing — the red team reproduced this one separately, §3 — `NOT_A_GEQ` after a slot
  change, a stop/close/disconnect, the `ring_out` tool timeout) leaves the controller believing in a cut the desk never
  received. The next detection at that band then "deepens" to −6 dB, which the desk receives as its **first** cut: a 6 dB step,
  violating the −3 dB-steps guarantee; the report and `cfs_status` lie; the budget is spent on nothing. In production the first
  ring of a session is always > 2 s after arming, so **every first notch has this window**, widened to the full read timeout
  (0.5 s × 3) when a datagram is lost.
* **`_disarm()` cancels the detector task without draining an in-flight notch write.**

Refuted along the way (with instrumented logs): stale `/fx/5/par` served from `Desk._cache`, `_inflight` or `_FxCache`
(every GEQ write invalidates via the `write` event and the epoch guard holds); tool timeout cancelling the arm; frame-queue
drop-oldest; write to the wrong slot/side. Also refuted: the commit message's rationale that "`_open_session` also runs on
`ring_out`'s preflight call" — the pending call returns from `server.py` before `cfs.ring_out` is ever entered.

### 4.2 The fix (PR)

1. `NotchController.plan()` becomes two-phase: `propose(det, bus, session_id) -> Proposal | None` (pure: the band, the new depth,
   whether it is a new budget line; runs `policy_validate`) and `commit(proposal) -> Notch`. `plan()` stays as propose+commit
   for the existing unit tests. `cfs._notch()` proposes, writes, and commits **only after the write returned**; on any exception
   (including `CancelledError`, re-raised after bookkeeping) nothing is committed and a `cfs.notch_failed` event says so.
2. `Desk.set_geq_band` accepts the already-known `fx_type` from the session (the writer knows slot, side and type from
   preflight), removing the hidden read — and with it the suspension point — from the hot path; it still re-reads when not
   given one.
3. `_disarm()` awaits an in-flight notch (bounded, 1 s) before cancelling the consumer, so an operator's stop never bisects a cut.
4. Regression tests that fail on `main` today: FakeDesk reply latency 12 ms + stop immediately after the first planned notch
   (controller and desk must agree); `FakeDesk.drop_next` on the GEQ write path (nothing committed, warning logged, next
   detection writes −3 not −6); Windows clock-resolution emulation of the original interleaving.

### 4.3 Calibration done right (folded into the discriminator work, §1.5)

The instinct — a fixed gate cannot be right for a studio, a pub and a field — is correct; the statistic and the placement were
not. A single global gate at `max(band) + 8 dB` measured over programme deafens the detector (the SyntheticRta's 20 Hz band at
−9.5 dB pushed the gate to −1.5 dB and no ring was ever seen: the "needs a cap" failure). What the detector actually needs is a
**per-band temporal baseline**: for each band, a slow low-percentile tracker (≈20th percentile over the last few seconds,
seeded from the first ~1 s of frames while detection is held off, updated only downward-fast/upward-slow so a ring cannot
pull its own baseline up), and a predicate "level − baseline ≥ E" alongside prominence. That is room-adaptive without a cap,
ignores mains hum / HVAC / a standing room mode for free (they are in the baseline), runs inside the consumer task on the
frames the detector already receives (no second subscriber, no stall inside the lock, nothing before `frames.start()`), is
meaningful in `ring_out` (no programme by contract) and degrades gracefully in `feedback_watch` (music raises the baseline
where music lives, which is exactly where a modest peak should need more evidence). The arm result reports the hold-off, and
`cfs_status` exposes the baseline so the operator can see what the detector considers "normal here".

---

## 5. Read-after-write over fire-and-forget UDP

### 5.1 What is actually known

* SET has no acknowledgement (transport.md §5.2); the writer never receives its own `/xremote` echo (§4.2, quirk 8); the `/`
  node-style write **is** echoed verbatim to the sender and the X32 documentation recommends the echo as *flow control* (§6.6).
* Whether the desk has *applied* a SET before it serves the next datagram is **UNCONFIRMED**. The two hardware observations
  (n = 1 each) — `insert/on` read stale straight after `setup_ringout_eqs`; a name read stale straight after `label_channel` —
  say that for at least inserts and scribble-strip names it has not. An FX type load (re-initialises 64 pars) is the slowest
  thing the DSP does and is almost certainly in the same class. Fader/mute leaves are probably synchronous (M5: a 3-line
  restore in 9 ms followed by a clean diff) — probably.
* It was **not our cache** in those two cases: `Desk._write` drops the owning section synchronously before the read and the
  leaf index covers both addresses. But the review found two genuine cache defects next door, independent of desk latency:
  **E0** — `_drop()` bumps the epoch but leaves the entry in `_inflight`, so a reader arriving *after* one of our writes can
  join a `/node` request issued *before* it and receive pre-write data; **E1 (the amplifier)** — when the desk does answer a
  post-write read with the old value, that answer is cached for `read_cache_ttl_s` = 2 s, so a 50 ms apply latency becomes a
  2 s lie that nothing retires (the desk never pushes our own change back to us).
* The emulator applies-then-echoes a `/` write (X32.c 3769); whether the desk's echo means *applied* or only *received* is
  UNCONFIRMED. So no design may treat an echo, a GET reply or `/status` as proof of application: **only an observed value proves
  a write took effect.** Replies prove *receipt and ordering* (one socket, served in arrival order — confirmed for the emulator,
  uncontradicted for the desk), which is still useful as flow control.

### 5.2 Where it bites (site inventory, condensed)

An inventory agent enumerated every write→read pair in `src/x32mcp`. The ones that change an *action* rather than a report:

| Site | Write → read | If the read is pre-write |
|---|---|---|
| `provision.apply_setup` → `validate_ringout_eqs` | `/fx/N/type`, `insert/sel,pos,on` → `/node fx/N`, `fx/N/par`, `…/insert` | the HANDOVER false `GEQ_VALIDATION_FAILED`; or the old effect's pars parsed as GEQ dB (bogus `existing_cuts`, a de-esser threshold read as "+20 dB") |
| `preflight` → `_open_session` | a `set_fader` on the bus just before `ring_out` → `master_db` | **`start_master_db` wrong → first RAISE is an absolute jump** (reads −10 while the desk is at −30 → writes −9: +21 dB). Class A. Now bounded by the operator-override read in PR `cfs-write-safety`, but the base should be settled. |
| `adjust_level` / `set_level` ramp start | any earlier write or push → `before` via the 2 s cache | delta applied to a fictional base (+23 dB reproduced by a judge's probe against a 150 ms-late desk); see also §2 D2 |
| `set_rta_source` → `/-stat/rtasource` | prefs → stat | reported unverified; CFS armed anyway (§2 A1) |
| `_arm` → first frames | RTA prefs → `/meters/15` | first 5–20 frames analysed under the old source/ballistics; persistence 3 is satisfiable → a notch on the new bus for a peak on the old one |
| `recall_scene` → `prepos/current`; `restore` → `diff` | | wrong "verified"; a clean diff that is not |
| `panic()` | 24 SETs → nothing | a lost datagram is never noticed (PR `panic-hardening` now reads back) |

Existing ad-hoc settles: `set_rta_source`'s `sleep(0.1)` retry (a guess that predates any hardware test, despite its comment),
`_restore_master`'s poll loop, scene verify's poll, the integration tests' `settle()` helper.

### 5.3 Three designs, two judges, one synthesis

Three designers were briefed from different angles and each built a working prototype with a FakeDesk that can apply writes
late (all three suites green): **barrier** (+1660 lines: pending-write registry, dirty-until-confirmed cache reconcile, settle
classes with deadlines, `/status` fence, resend policy, an "x32ish" latency profile it ran the whole suite under), **read-your-writes
overlay** (+1839: a write journal that answers reads inside a window with our own value, `verify(expect=…)`, a measurement
script), **minimal** (+692: one `read_until` helper, `conn.sync()`, the E0/E1 fixes, FakeDesk per-prefix apply delay + inbound loss,
`setup_ringout_eqs` → verified / *not yet verified*). Two judges re-ran all three suites, wrote probe tests against each
prototype's own late-applying desk, and scored them 46 / 44 / 34 (minimal / barrier / overlay) and 8-9-5-8-9-9 vs … — agreeing
on the ranking and, more usefully, on the facts the three disagreed about:

* *"Positional `/` writes cannot address a single field"* (barrier) — wrong: leaf-path `/` writes are echoed too; the cost
  argument (a lost echo stalls 0.5 s × retries; useless on a 20 ms ramp step) is what limits where to use them.
* *The overlay serves our belief as desk data* — both judges reject it (DESIGN §0.6 inverted; its own prototype hit a projection
  bug and produced false `mismatched` warnings from cache hits). Keep its `verify(expect=)` and its measurement script; do not
  build the overlay.
* *"Only three sites matter"* (minimal) — incomplete: the read-modify-write bases (`adjust_level`, ramp start, `start_master_db`,
  head-amp index before `set_phantom`) are class-A and need a settled base or a refusal, which barrier's `_leaf_settled` provides.
* *`/status` as the fence request* — agreed by all: unused elsewhere, `,sss` reply, does not share the watchdog's `/info` FIFO.
* *Test fixtures:* `conn` gets its own `EventBus()` in `tests/integration/conftest.py` while `Desk` gets the `events` fixture, so
  `Desk._on_write_event` never fires for raw `conn.set` in integration tests — any registry keyed on the `write` event must fix
  the fixture (production `App` shares one bus).

**Synthesis (what to build; the PR delivers the first half):**

1. Rules: only a read-back proves application; no sleeps — poll the value on a short schedule to a deadline; results are
   tri-state (verified / unverified / timeout) and *time never raises*; the cache never stores a section that contradicts a
   pending write of ours; a read that becomes the base of an absolute write is settled first or the operation refuses
   (`BASE_UNVERIFIED`); committing writes (setup, inserts, type loads, labels, scene, restore) verify inline within the tool's
   own budget, Tier-1 DSP leaves and ramps do not (time-to-cut matters more).
2. `settle.read_until(read, predicate, deadline_s, schedule)` → `Settled{verify, value, polls, elapsed_ms}` (**PR**);
   `X32Connection.sync()` — one `/status` round trip as flow control, documented as receipt-not-application (**PR**); `slash()`
   keyed on the echoed text so a late echo of an abandoned line cannot ack the next one (rec).
3. `Desk`: E0 and E1 fixed (**PR**); a small pending-write registry with *dirty-until-confirmed* reconcile in `_read_sections`
   (a fetched section that disagrees with a pending write is returned but not cached; agreement resolves it "observed" for free)
   and `_leaf_settled()` in front of `adjust_level`, ramp start, `set_main_level`, `headamp_index_for` (rec — barrier's
   mechanism, ~150 lines without its fence/resend machinery); `verify(select|expect, deadline)` for `recall_scene`, `restore`,
   `dump` (rec).
4. Sites: `apply_setup` re-validates until settled and reports **NOT YET VERIFIED** (`ok:true` + warning) instead of
   `GEQ_VALIDATION_FAILED` when the only problem is time (**PR**); `set_rta_source` polls `/-stat/rtasource` to a deadline and
   reports attempts (**PR**); CFS discards frames older than the RTA-source settle (rec); `label` returns `verified` (rec).
5. FakeDesk: `apply_delay_ms` per address prefix with a due-time heap, `/` echoed before apply (pessimistic), FX type load resets
   pars after the delay, `drop_inbound_next(n)` (**PR**); a named `x32ish` profile and a CI job that runs the integration suite
   under it (rec — barrier ran it: 30 tests go red, which is the to-do list).
6. `device.yaml timing:` — settle deadlines per class, **measured**: `scripts/measure_settle.py` (**PR**) runs N trials per class
   against the desk (fader, mute, name, insert, RTA source, optionally an FX type load; one scratch channel, everything restored)
   and prints the block. Until then the provisional classes are: default 0.5 s; `insert/config/routing/headamp/-prefs` 1.5 s;
   `fx type/source` 3 s; scene 2 s.

### 5.4 What to measure at the desk (ten minutes, answers §5 and half of §2.2)

`python scripts/measure_settle.py <desk-ip> --trials 30 --second-socket [--fx-slot 8]` with the PA muted. It reports, per
class: SET→first matching GET (and how many *stale* replies preceded it — zero on every trial means that class is applied
before the next datagram is served, i.e. the ordering hypothesis holds for it), SET→matching `/node` text, `/` echo time and
whether the first GET after the echo already matches (does the echo mean applied?), push latency to a second socket, and
`/-prefs/rta/source` → `/-stat/rtasource`. Paste the printed `timing:` block into `device.yaml`.

---

## 6. Other things noticed

Smaller than the five workstreams, each verified by reading the code; most are one-line fixes or documentation.

* **`_calibrate_floor` is live dead code at HEAD** (cfs.py:890): commit `60b86d5` says "backed out" but only `device.yaml`
  changed; the call still runs before the frame source starts, times out after 0.3 s, and adds 300 ms to every arm. Its two
  state attributes were added to `CfsError.__init__` (cfs.py:122) instead of `CfsManager.__init__`. Remove the call (the
  discriminator PR replaces it with the per-band baseline) or move it as §4.3 describes.
* **The commit message's rationale is wrong**: "`_open_session` also runs on `ring_out`'s preflight call" — the pending call returns
  from `server.py` before `cfs.ring_out` is entered. Worth correcting in HANDOVER so the next reader does not build on it.
* **`_write_master`'s `after` is not a read-back** (cfs.py:1186 comment says it is): `_move_level` returns the clamped request.
  The report's start/end/max master and "gain before feedback" are therefore what was *sent*. PR `cfs-write-safety` reads the
  master before each step; the comment should go.
* **`X32Connection.slash()` and `send_raw()` publish no `write` event and invalidate nothing**; `Desk.restore` covers itself with a
  blanket `invalidate()`, `_restore_master` invalidates explicitly, but any future caller of `slash()` inherits a stale section
  for 2 s. The read-after-write synthesis (§5.3 item 2) makes `slash()` publish `write {address:"/", line}`.
* **CFS arming writes `/-prefs/rta/*` through the raw connection** while `get_rta` wraps the identical call in `_RtaPrefsWriter`
  (rate-limiter slot, tier check, `desk.write` event, pre-write snapshot). Use the wrapper in `cfs._arm` too — then the RTA prefs
  land in the pre-write snapshot and `restore_snapshot` really does undo the session (§2 D3).
* **`average_frames` averages dB arithmetically.** Fine for display; biased low as a level estimate of a fluctuating band. The
  detector's baseline should use percentiles (it does in the new design).
* **`det = PEAK` may be the wrong way round.** The M7 rationale ("RMS blunts a narrow tone") is not physics: RMS does not attenuate
  a sine, it averages noise; PEAK inflates noise-like bands by their crest factor and so *reduces* tone-over-bed prominence by
  ~6–9 dB. UNCERTAIN until the RMS window length is measured — do it with the console oscillator (§8 item 3) before flipping it.
* **`existing` GEQ cuts are all treated as previous CFS notches to deepen** (detector.py `NotchController.__init__`): an engineer's
  −4 dB house curve at 2 kHz adjacent to a 2.5 kHz ring is deepened to −7, −9 (two wasted VERIFY cycles, 3 s of ringing, his
  curve mangled) before the ringing band is touched. Only cuts that `preflight` matched to a saved CFS report
  (`geq.matched_session`) should be mergeable; the rest are the operator's and should be left alone (and reported).
* **`recall_scene` on the CUES desk**: HANDOVER §4a notes the desk's show control is CUES; every recall will be "cannot be
  verified" by pointer. Verify by content (re-dump two sentinel sections) instead.
* **`connect()` never compares the answering console with `descriptor.meta`** (model X32, firmware 4.x): a Compact/Producer/M32 or
  an untested firmware is accepted silently. Warn (do not refuse) on mismatch; the node-width UNCONFIRMED items make this matter.
* **The highest-leverage missing fixture** is a committed FW 4.13 `dump_desk_state` JSON from the M5 desk (`snapshots/` is empty
  in git). Every parser/renderer/diff/restore test currently compares our formatter with our parser.
* **`discover_mics`** (HANDOVER §4b: 7 candidates, 1 microphone) — PR `discover-mics-signal` annotates candidates with the live
  input meter and flags "no input signal"; it also gives FakeDesk an `unplugged` switch, the first of §2.3's realism switches.

---

## 7. Method, reproducibility, and what this review could not do

### 7.1 How the review was run

Each workstream of the brief was treated as a separate investigation with its own fan-out of agents, and no finding was
accepted on one agent's word:

| Workstream | Fan-out | Verification |
|---|---|---|
| §1 discriminator | 2 physics briefs (loop physics; analyser + programme statistics) → corpus builder → adversarial corpus critic → 5 competing designs with different mandates → independent metrics audit + "breaker" scenarios per design → 2 judges | every design re-measured by an auditor who also wrote new scenarios to break it |
| §2 test assumptions | 8 readers by slice (each pairing code, its tests and the relevant research doc), incl. an `UNCONFIRMED` hunter and a FakeDesk-fidelity audit | 2 skeptical verifiers per finding (citation check; real-world consequence) |
| §3 safety | 6 attackers by angle (bypass inventory, tokens, clamps/show-mode, panic, CFS² writes, LLM tool surface), each required to run exploit code | 2 verifiers per finding (independent reproduction; source-level refutation) |
| §4 calibration bug | 3 investigators seeded with different hypotheses | 2 cross-checkers, then a designer |
| §5 read-after-write | site inventory + 3 designers (ordering barrier; read-your-writes overlay; minimal helper) | 2 judges → synthesis |

The lead reviewer read the core modules (`detector.py`, `policy.py`, `cfs.py` arm/notch/consume paths, `meters.py`
SyntheticRta and `set_rta_source`, `desk.py` cache and GEQ paths, `provision.py` preflight/validate) and the research on
RTA prefs and the `/` echo first-hand before briefing any agent, and independently derived the analyser time-resolution
argument (§1.2b), the plan-before-write desynchronisation (§4), the RTA decay/peak-hold gap (§1.2c) and the
panic-vs-ring_out interaction (§3) so that the fan-outs could be checked against something.

### 7.2 Running the suite without a network (the shim)

The review sandbox forbids `AF_INET` sockets entirely, and the integration suites bind UDP on 127.0.0.1. Rather than skip
them (bug §4 lives there), a ~150-line test-only `tests/conftest.py` was written that, **only when a real loopback bind
raises `PermissionError`**, replaces `socket.socket` for IPv4 datagram sockets with an in-memory object and routes
`loop.create_datagram_endpoint` through an in-process port registry, delivering datagrams with `call_soon`. `FakeDesk` and
`X32Connection` are untouched and talk to each other exactly as over loopback (ordering preserved; loss still injectable via
`FakeDesk.drop_next`). Result: 912 passed, 21 deselected (`tests/test_webui.py`, TCP) and 1 expected failure
(`test_dashboard_status`, TCP). It is deliberately not in any PR — it is review scaffolding, and on a normal machine it
never activates — but it is reproduced in Appendix C in case it is useful for CI in locked-down environments.

### 7.3 Limits

* No hardware. Every claim about the real desk is either from `docs/research/` (cited), from `HANDOVER.md` §4a/§4b, or
  physics; where those run out the report says so. The corpus models the analyser from first principles (constant-Q time
  resolution, PEAK detector, configurable decay/peak-hold); Behringer's actual RTA implementation is not documented and the
  LF attack constants in particular should be measured once (§1.7 gives the five-minute protocol).
* `uv` could not run in the sandbox; a plain venv with the same pinned versions (mcp 2.2.0, websockets 17, pytest 9) was used.
* The dashboard (`webui.py`, `index.html`) was not reviewed beyond confirming it is read-only.

---

## 8. Next session at the desk: one checklist

Everything below is a measurement or a re-test that this review could not do; each closes an `UNCONFIRMED` the code depends
on or validates a PR against reality. In this order, PA **muted or master down** unless stated (the bus RTA tap is pre-fader,
so most of it needs no sound in the room at all).

1. **RTA prefs reach the stream?** With a steady tone into a channel routed to a bus and the RTA on that bus: toggle
   `/-prefs/rta/gain` 0 ↔ 24, `decay` 0.25 ↔ 16, `peakhold` OFF ↔ 3, `det` RMS ↔ PEAK, and watch `/meters/15` (`get_rta`).
   Record which move the numbers. (Settles §2 A3, §6 det, and whether PR `rta-ballistics` is necessary or merely harmless.)
2. **Analyser response per band** — the console's own oscillator (`/config/osc`: sine, `f1`, `level`, `dest` = a spare bus;
   `/-stat/osc/on`), bus master *down* (tap is pre-fader): step the tone on/off at 40 Hz, 80, 160, 315, 630, 1.25 k, 2.5 k, 5 k,
   10 k and log 40 frames each side. Yields the real attack τ(f), release law, skirt shape at ±1/±2/±3 bands, and the RMS/PEAK
   difference — i.e. every `AnalyserSettings` constant in `tests/rtasim/physics.py`. Ten minutes; makes the corpus true.
3. **GEQ bell shape** — pink noise (oscillator `type` PINK) into the bus, GEQ PRE, one band −6 dB: read the RTA at 0/±1/±2/±3
   RTA bands. Sets the RBJ Q in the corpus and the flank-pair rule (§1.3).
4. **Read-after-write latencies** — `python scripts/measure_settle.py <desk> --trials 30 --second-socket [--fx-slot 8]` (PR
   `read-after-write`); paste the printed `timing:` block into `device.yaml`. Note especially: stale replies after SET (is the
   desk in-order-synchronous per class?), `/` echo vs applied, FX type-load time.
5. **What `sel = FX5L` on Main LR does with a GEQ2** — cut side A −15 dB and side B −15 dB separately with pink noise on Main LR,
   read the RTA each time (§2 C1 predicts ≈ −4.6 dB each, −15 dB together). Decides the Main LR provisioning fix.
6. **`/xremote` push behaviour** — with X32-Edit + iPad + two phone apps connected, does the server still receive pushes? (§2 D2.)
7. **RTA source robustness** — during a `feedback_watch`: press the RTA source encoder, solo a channel with Solo Priority on,
   open FX5's GEQ page on the console; confirm PR `cfs-write-safety` aborts and that `/meters/15` really did switch.
8. **Panic under load** — start a 30 s ramp and a `restore_snapshot`, then `panic()`: confirm nothing re-opens; confirm
   `delivered: confirmed`; pull the network for 5 s around a panic and confirm the re-assert on reconnect (PR `panic-hardening`).
9. **The M7 re-test with music playing**, new discriminator (PR `detector`), `feedback_watch` on the SM58/Alto rig: expected — no
   cut on the bass line or the room mode, the established 8 kHz ring cut within ~300 ms of arming, 5 kHz modes cut as they
   appear, notch report naming the predicates that fired. Save `detection_log`; it is the first real labelled data the corpus
   can be calibrated against.
10. Commit a `dump_desk_state` JSON of the desk to `tests/fixtures/` (§6).

---

## Appendices (in `docs/review/`)

| | |
|---|---|
| [A1](review/A1-physics-of-the-feedback-loop.md) | Physics of the electro-acoustic feedback loop: loop gain, growth rates, spectral signature, feasible frequency range by source, GEQ cut geometry, the asymmetry argument (brief Q3/Q4) |
| [A2](review/A2-analyser-and-programme-material.md) | The RTA as an instrument (time–frequency bound per band, prefs), programme material in RTA terms, the irreducible false-positive set, vibrato through the bands, the scenario list |
| [B1](review/B1-validation-corpus.md) | The `rtasim` corpus and harness: how to run it, every scenario with ground truth and the predicate it stresses, simulator parameters and citations, residual weaknesses |
| [B2](review/B2-current-detector-baseline.md) | The shipped detector's per-scenario baseline (open and closed loop, with and without the M7 override) |
| [C](review/C-sandbox-udp-shim.md) | The in-memory UDP shim used to run the integration suites without a network |
| [D](review/D-safety-findings.md) | All 58 safety findings with both verifier verdicts |
| [E](review/E-assumption-findings.md) | All 114 assumption findings with both verifier verdicts and per-slice coverage notes |
| [F](review/F-bug4-investigation.md) | Bug 4: three investigations, two cross-checks, the calibration/transaction design spec |
| [G](review/G-read-after-write.md) | Read-after-write: site inventory, the three designs, both judgments |
| [H](review/H-discriminator-competition.md) | The discriminator design competition: both judges' verdicts, the five audits, the designers' summaries |
| [I](review/I-detector-build.md) | Building the final discriminator: implementer's report and changelog, both verifications, the fix round, the re-measure |

