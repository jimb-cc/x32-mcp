# CFS² — what the detector actually sees: the X32 RTA as an instrument, and programme material in RTA terms

Scope: analyser physics + source statistics + corpus spec. Repo HEAD a408a2a. Numeric experiments (scripts + JSON) in
`$SP/reports/physics-analyser/` (`a1_bands.py`, `a2_onset_vs_detector.py`, `a2b_onset_levels.json`, `a3_vibrato.py`). All levels are RTA dB
(`/meters/15`, int16/256, −128 floor, 0.0 = clip, meters.md §4.2); 1 frame = 50 ms.

## 0. Headline findings (verified numerically against the real `FeedbackDetector`)

1. **The growth feature is manufactured by the analyser.** A 1/10-octave band centred at f has Δf = 0.0693·f (Q = 14.4). Nothing that resolves
   that band can respond faster than ~1/Δf (uncertainty bound): 370 ms at 39 Hz, 185 ms at 78 Hz, 46 ms at 312 Hz, 7 ms at 2 kHz. An
   acoustically *instant*, perfectly steady tone therefore appears on band 10 (39 Hz) as a rise spread over 5–8 frames whose least-squares
   slope, measured exactly as `detector.py:331-347` measures it, is 8–30 dB/s — inside the 6..60 dB/s "regenerative growth" window
   (`detector.py:101-103`). Feeding such onsets to the shipped detector with the shipped `device.yaml` config (`a2_onset_vs_detector.py`):
   * plateau prominence ≈ 16–20 dB, τ = 1/Δf model: **bands 13–40 (45–315 Hz) are reported as feedback in 10/10 trials**, latency 0.2–0.4 s,
     reported slope 8–15 dB/s, confidence 0.70–0.88; bands 41–97 in 2–9 of 10 trials (depends only on where the onset lands inside the 50 ms
     frame); single-biquad model (τ = 1/(πΔf), 3× faster): bands 3–23 (24–100 Hz) 10/10, the rest 2–8/10. With no analyser smoothing at all: 0/95.
   * plateau prominence ≥ 25 dB: **95/95 bands, 10/10 trials**, via the M7 override (`detector.py:122-123,360-368`) after 255 ms, any model.
   * Mechanism above ~300 Hz: one partially-integrated first frame x dB below plateau followed by two flat frames gives a 3-point LS slope of
     10·x dB/s; x ∈ [0.6, 6] dB passes the window, and `persistence_frames: 3` (`device.yaml:801`) asks for nothing more. The suite already
     concedes the 3-sample slope is noise (tests/test_detector.py:296-301, 313-316) but every synthetic note it builds rises at exactly 0 dB/s
     (instant, `rise_s=0`) or ≥ 80 dB/s (`NOTE_RISE_DB/NOTE_RISE_FRAMES` = 120 dB/s, meters.py:586-587,691-692; `rise_s=5*FRAME_S` → 80 dB/s,
     tests/test_detector.py:221) — never the 6–60 dB/s that the analyser forces on every LF onset. **This alone reproduces the M7 40 Hz / 80 Hz
     false positives**; no property of "music" is needed, and 80 = 2×40 is additionally a bass note + H2 (or an axial room-mode pair, §6).
2. **Real rings in `ring_out` mostly violate the growth window from the other side.** Above threshold a loop grows at (excess loop-gain dB)/(loop
   period). Loop period = d/343 + ≈1.5 ms electronics: 4.4 ms at 1 m, 10.2 ms at 3 m, 24.8 ms at 8 m → **226 / 98 / 40 dB/s per dB of excess**.
   `ring_out` raises the master in +1 dB steps (`step_db` default 1.0, cfs.py:441,655; step write cfs.py:1174), so the step that crosses threshold leaves 0–1 dB excess, i.e. growth
   uniformly distributed over 0–100 dB/s at 3 m and 0–226 dB/s at 1 m: roughly half of real onsets exceed `growth_max_db_per_s: 60` and are
   discarded as "note onsets" by the guard (`detector.py:335-339`), then plateau at clip (0.0) or limiter and score 0.50 for ever — exactly the
   M7 false negative. "Slow" 3–6 dB/s rings require the loop to sit within 0.03–0.06 dB of threshold (3 m) — they exist (wandering marginal
   loops, performer movement) but are the minority in a stepped ring-out.
3. **Below threshold the loop is already visible and probe-able.** Steady-state regenerative gain at the ring frequency is 1/(1−g):
   +6.0 dB at loop gain −6 dB, +10.7 at −3, +13.7 at −2, +19.3 at −1, +25 at −0.5. A +1 dB master step raises that band by
   **+1.4 dB (g=−12), +2.1 (−6), +3.0 (−4), +4.1 (−3), +6.5 (−2), +10 (−1.5), runaway (≥ −1)** while every programme/noise band rises exactly
   +1.00 dB. In `ring_out` the server owns the step (free active probe); in `feedback_watch` the same arithmetic applies to any common-mode move
   the operator makes (§5) if levels are measured relative to a spectrum-wide reference.
4. **`set_rta_source` leaves three prefs that rewrite the detector's features unset** (meters.py:860-941 sets source, pos, options bit 5,
   autogain=0, det=PEAK; never touches `/-prefs/rta/decay`, `/peakhold`, `/gain`), restores nothing afterwards, and chose det=PEAK on a
   rationale that is backwards (§2).
5. At 1/10-octave resolution **narrowness cannot separate a musical partial from a ring** (both ≪ 1 band); it separates *tones* from *noise/
   broadband bumps*. The music/feedback split must come from harmonic family (usable only when H1 prominence ≳ 20–25 dB, §4.3), frequency
   stability at sub-band precision (centroid), level micro-variance, duration, common-mode/probe response, and a source-dependent frequency prior.

## 1. The RTA as a measuring instrument

Band i centre f_i = 10000·2^((i−90)/10) Hz (meters.md §4.2, device.yaml:747-757). −3 dB edges at ±1/20 oct = ±60 cents; Δf_i = f_i·(2^{1/20}−2^{−1/20})
= 0.06932·f_i. Whatever Behringer implemented (constant-Q IIR bank, multirate FFT, or single FFT + band summation — UNCONFIRMED, no source),
resolving Δf needs an observation window ≥ ~1/Δf; a causal filter of that bandwidth has envelope rise time 0.7/Δf (2nd order) … 1.1/Δf (6th
order); a windowed FFT needs window length ≥ ~2/Δf for a Hann main lobe no wider than the band.

| nominal | band | f_c Hz | Δf Hz | 1/Δf ms | 1/(πΔf) ms (1 biquad env. τ) | 10–90 % rise, 6th-order BP | rise, frames @20 fps | LS slope of an instant onset over 0.5 s after it crosses −12 dB re plateau (τ=1/Δf) | first-3-frame slope |
|---|---|---|---|---|---|---|---|---|---|
| 31.5 | 7 | 31.7 | 2.20 | 455 | 145 | 500 ms | 10 | 17 dB/s | 59 dB/s |
| 40 | 10 | 39.1 | 2.71 | 369 | 118 | 406 | 8.1 | 17 | 66 |
| 50 | 14 | 51.5 | 3.57 | 280 | 89 | 308 | 6.2 | 17 | 74 |
| 63 | 17 | 63.5 | 4.40 | 227 | 72 | 250 | 5.0 | 17 | 80 |
| 80 | 20 | 78.1 | 5.42 | 185 | 59 | 203 | 4.1 | 17 | 86 |
| 125 | 27 | 126.9 | 8.80 | 114 | 36 | 125 | 2.5 | 15 | 98 |
| 250 | 37 | 253.8 | 17.6 | 57 | 18 | 63 | 1.3 | 13 | 111 |
| 500 | 47 | 507.7 | 35.2 | 28 | 9 | 31 | 0.6 | 11* | 118 |
| 1k | 57 | 1015 | 70.4 | 14 | 5 | 16 | 0.3 | 11* | 117 |
| 2k | 67 | 2031 | 141 | 7 | 2 | 8 | 0.2 | 11* | 117 |
| 4k | 77 | 4061 | 282 | 3.6 | 1 | 4 | 0.1 | 10* | 109 |
| 8k | 87 | 8123 | 563 | 1.8 | 0.6 | 2 | 0.04 | 9* | 97 |
| 16k | 97 | 16245 | 1126 | 0.9 | 0.3 | 1 | 0.02 | 9* | 97 |

(*) above ~300 Hz the rise is sub-frame; the residual "slope" is the single partially-integrated frame described in §0.1 and depends on onset
phase, not on f. **Which bands turn ANY onset into "growth": deterministically bands ≲ 40 (≤ 315 Hz) under τ = 1/Δf, ≲ 23 (≤ 100 Hz) under the
3×-faster single-biquad model; probabilistically (20–90 % per onset) every band, because persistence is 3 frames.** The 60 dB/s onset guard
catches the first 3 frames at LF (59–86 dB/s) and then *restarts the window* (`detector.py:337-338`), after which the remaining concave rise
(8–30 dB/s) is scored as growth — the guard converts an onset into a textbook ring profile.

Plausible implementation models to bracket in the corpus (all UNCERTAIN; measure with the console oscillator, §2.6):
* **U (constant-Q bank, critically resolved):** per-band power one-pole, attack τ_a(i) = max(1/Δf_i, ~5 ms) → 370 ms @ band 10, 46 ms @ band 40.
* **BQ (one 2nd-order section per band):** τ_a(i) = 1/(πΔf_i) → 118 ms @ band 10; skirts only −7 dB at ±1 band (too leaky to give the 60 dB
  prominence observed at M7 at 8 kHz, so if BQ-like at LF it must be multi-section at HF).
* **FFT (single N-point Hann at 48 kHz + band summation):** bin 11.7 Hz (N=4096, 85 ms window) — the 1/10-oct band is *narrower than the Hann
  main lobe below ≈ 340 Hz (band 41)*, so an LF tone would smear over ±2–4 bands (40 Hz tone → bands ≈ 6–14) and LF prominence (median ±3)
  would collapse to a few dB; N=16384 (341 ms window, i.e. the same ~1/Δf latency) is needed to resolve band 21. M7 saw ≥ 12 dB-prominent
  detections at 40 and 80 Hz, which argues against a short single FFT and for a constant-Q/multirate design — i.e. for the long LF time
  constants of model U. Either way the LF latency/smear trade is forced by Δf·T ≥ 1.
* Statistical floor: for noise-like content the band-power estimate fluctuates with sd ≈ 4.34/√(Δf·T_avg) dB: with T = 50 ms that is ±4.3 dB
  for bands < 40, 2.1 dB @ band 60, 1.0 dB @ band 80, 0.7 dB @ band 90 — and autocorrelated over ~τ_a frames at LF (not i.i.d. as in both
  synthetic generators: meters.py:595 `noise_db=3.0` uniform i.i.d.; tests/test_detector.py:73 `floor_noise_db=1.5` i.i.d.). A band dominated
  by a steady tone fluctuates ≈ 0 dB. Level micro-variance over 5–10 frames is therefore a strong *tone-vs-noise* feature (not tone-vs-ring).
* Concentration gain: pink-ish programme at broadband L spreads over ~70 bands → per band ≈ L − 18.5 dB; a sine at broadband L reads L in one
  band. A sub-bass sine or a whistle is ~+18 dB "prominent" over programme of equal loudness before any resonance is involved.
* Clip: 0x0000 = 0.0 dB means the analyser input clipped (meters.md §4.2) — a plateau pinned at 0.0 is a howl at full scale (or RTA gain set
  absurdly); treat `level ≥ −0.5` as its own predicate.

## 2. RTA prefs (meters.md §5.1) — what is set, what is not, what each does to the features

`/-prefs/rta/*`: visibility, gain (linf 0..60 step 6 dB), autogain {OFF,ON}, source, pos {PRE,POST}, mode {BAR,SPEC}, options (6 bits), det
{RMS,PEAK}, decay (logf 0.25..16, 19 steps ×1.26: 0.25 0.31 0.40 0.50 0.63 0.79 1.00 1.26 1.59 2.00 2.52 3.17 4.00 5.04 6.35 8.00 10.08 12.70 16.00),
peakhold {OFF,1..8}. UNCONFIRMED in the research (meters.md §4.2 last bullet, §5.1 table): whether `/meters/15` values include gain/autogain,
the unit of decay, the semantics of peakhold 1..8, which firmware added det/decay/peakhold.

What `set_rta_source` does (meters.py:860-941): writes source, pos; clears options bit 5 if set; **forces autogain=0 (l.906-917) and det=PEAK
(l.918-924)**; verifies `/-stat/rtasource` with 2 reads 100 ms apart. It never reads or writes `decay`, `peakhold`, `gain`; nothing in cfs.py
restores autogain/det/options/source/pos at disarm (grep: no other writer of `/-prefs/rta/*` outside fakedesk) — the operator's RTA is silently
left reconfigured after every session. The emulator defaults (fakedesk.py:159-160: gain 24, autogain ON, det RMS, decay 1.0, peakhold OFF) are
the only values the tests have ever seen, and FakeDesk's SyntheticRta applies none of these ballistics to the stream.

Effect of each unset/as-found pref on the detector's features:

| pref | most plausible semantics (UNCERTAIN where marked) | effect on prominence | persistence / "outlasts" | growth | plateau / VERIFY |
|---|---|---|---|---|---|
| **decay** 0.25–16 | release ballistic of the bar display, a *time* (DOC: "adjustable decay time"; the 1.26-ratio series is a time series). Whether it is an exponential τ, or time-to-fall-N-dB is UNCERTAIN: fall rate ≈ 4.3/decay dB/s (if τ of power averaging) up to ≈ 20–60/decay dB/s (if "time to fall 20/60 dB"). At decay=1.0 (emulator default): 4–60 dB/s; at 16: 0.3–4 dB/s. | none while a tone sounds; after it stops the band stays prominent for seconds | **every note "outlasts itself"**: a 200 ms bass note at decay ≥ 2 s is still a ≥ 12 dB-prominent, non-growing peak 1–3 s later → passes persistence; a kick at 120 bpm (every 10 frames) never returns to floor → permanent LF peak re-pumped 2×/s | release masks decay, never creates growth; but re-attacks from a partially-decayed level are *slower in dB* (smaller step) → more onsets land in 6..60 | **VERIFY (cfs.py:1201-1240, decay_verify_db 6 within 1.5 s on 2 consecutive frames) measures the RTA release, not the room**: an acoustically killed ring falls on the display at the decay rate; at decay ≳ 2–4 s (fall ≲ 6 dB per 1.5 s under either semantics' slow end) VERIFY fails, the controller deepens −3→−6→−9 needlessly and aborts "not tamed". Force **0.25**. |
| **peakhold** OFF/1..8 | peak-hold overlay; 1..8 = hold seconds or a hold/decay grade (UNCERTAIN). Whether `/meters/15` carries bars or held peaks is UNCONFIRMED. If it affects the stream: every transient leaves a dead-flat prominent line for the hold time | frozen | infinite persistence, "outlasts" everything | 0 (flat) → looks exactly like an established ring → override fires on every loud note ≥ 25 dB prominent | VERIFY impossible (nothing drops). Force **OFF** (costs nothing even if display-only). |
| **det** RMS/PEAK | per-band level detector. A steady sine reads the same ±3 dB either way; PEAK inflates *noise-like* bands by their crest (≈ +8…+11 dB for band-limited Gaussian envelope maxima over a release period, more in wide HF bands) | **PEAK lowers tone-over-programme prominence by ~6–9 dB** and tilts the noise floor up at HF; RMS is the quantity in which "+1 dB per dB", harmonic ratios and power sums are exact | PEAK + slow decay = sticky | PEAK has faster attack (sub-frame at HF either way; at LF the band τ dominates both) | The code's rationale "RMS blunts a narrow tone" (meters.py:918) is not physics: RMS does not attenuate a sine; it averages noise. Recommend **RMS** unless a hardware test shows the RMS averaging window is long (> 100 ms) — UNCERTAIN, measure (§2.6). |
| **autogain** | slow AGC normalising the display to the loudest band | preserved (common-mode) | — | **every band drifts together = growth everywhere** (M7: 337 detections/600 s, HANDOVER §4b) | breaks absolute gates. Correctly forced OFF (meters.py:915). |
| **gain** 0–60 dB | manual display gain, active when autogain is OFF; probably added to `/meters/15` (UNCONFIRMED) | none (relative) | — | none | **shifts every absolute number the detector uses**: `min_level_db −45` (device.yaml:797), the calibrated floor (cfs.py:824-877), `−128` tests, and pushes loud bands into **clip at 0.0 → flat-topped peaks, prominence compressed, growth 0**. After autogain is switched off the manual gain is whatever the operator last left (emulator: 24 dB). Force a known value (0 dB; or 6–12 dB if the bus runs quiet so programme bands sit −60…−30 and a full howl still fits under 0). |
| options bits 0–4 | overlay-only (meters.md §5.3) | — | — | — | leave. bit 5 correctly cleared. |
| pos PRE/POST + GEQ insert PRE | tap point | POST-insert GEQ invisible to RTA (HANDOVER §4b item 4, fixed ddcfeb1) | | | keep POST-EQ tap + PRE insert. |

**Recommendation — force at arm, restore at disarm:** read the whole `/-prefs/rta` group node once (`/node ,s -prefs/rta`, one datagram, order
visibility gain autogain source pos mode options det decay peakhold — device.yaml:703-704), keep it in the session, then write:
`autogain 0`, `gain 0.0` (f=0.0), `det 0 (RMS)` [or 1 if measured better], `decay 0.25` (f=0.0), `peakhold 0 (OFF)`, `options &= ~0x20`, `source`,
`pos 1`; at disarm/abort/close write the saved ten values back (including source/pos so the operator's RTA view returns). **Read-back race:** a
read issued straight after a write returns the old value over fire-and-forget UDP (HANDOVER §4b: `label_channel`, `setup_ringout_eqs`;
REVIEW_BRIEF §5); `set_rta_source` already sleeps 100 ms between two `/-stat/rtasource` reads (meters.py:928-934). Do not "verify" the forced
prefs by immediate read-back; either re-read the group node ≥ 200 ms later (one datagram), or treat the desk's echo of the set (transport.md
§6.6) as the acknowledgement, and start the noise-floor calibration / detector only after that barrier — otherwise the first 5–20 frames are
analysed under the *old* ballistics (e.g. autogain still converging, peak-hold lines still present) and the floor calibration
(`_calibrate_floor`, cfs.py:824-877, currently run in `_open_session` *before* `_arm` → before `set_rta_source`: cfs.py:577-578, 684-697, 890 vs 911) measures
the wrong instrument.

**2.6 Self-calibration without sound (recommended, cheap):** the console has an oscillator — `/config/osc/type 0 (SINE)`, `/f1` logf 20–20k in
121 semitone steps, `/level`, `/dest` MixBus1–16/L/R/M-C/Mtx (fx_routing_scenes.md:690), `/-stat/osc/on`. Route it to an *unassigned* mix bus
(main/matrix sends off, no output patch), point the RTA at that bus, and in ~20 s measure: attack frames per band (osc on), release rate vs
`decay` (osc off), skirts at ±1..±3 bands, off-centre response (semitone steps = 100 c against 120 c bands give offsets 0, ±20, ±40, ±60 c),
RMS vs PEAK offset, whether `gain` shifts `/meters/15`, clip level. That converts every UNCERTAIN in this section into a per-desk table the
detector loads at arm time. Guard: verify the bus really reaches no output (read its main assign + output routing) before unmuting the osc.

## 3. How a pure tone renders across bands

* Position: a tone is in general off-centre; worst case on a band edge (±60 c) it reads −3 dB in *two* adjacent bands equally. 12-TET notes vs
  this grid: band centres are 120 c apart anchored at 10 kHz (= 39.86 semitones above A4-referenced C… i.e. unrelated to A440), so successive
  semitones walk through offsets in 20-cent steps; 1 note in 6 sits within ±10 c of an edge.
* Skirts (Butterworth band-pass of order 2N, evaluated at neighbouring centres): N=1: −7/−12/−16 dB at ±1/±2/±3; **N=2: −12/−24/−31; N=3:
  −18/−36/−47; N=4: −24/−48/−63**. Off-centre by 30 c (N=3): own −0.1, near neighbour −11, far −24, ±2: −33/−39 → **asymmetric skirts are the
  normal case**, not evidence against a tone. On the edge (N=3): −3/−3, then −29/−29, −42. M7 datum: the 8 kHz howl read **60 dB prominent**
  (median of ±3) → at 8 kHz the real skirts are ≤ −60 dB by ±2 bands (N ≥ 5-equivalent or FFT sidelobes); LF skirts unknown (UNCERTAIN).
* Prominence as defined (`detector.py:278-288`, median of 6 neighbours = mean of 3rd/4th lowest) has a **ceiling set by the ±2 skirt**: 24 dB for
  N=2, 36 dB for N=3, 48 dB for N=4, regardless of how loud the tone is (until ±2/±3 hit the −128 floor). With N=2-like LF skirts the M7
  override threshold (25 dB) sits exactly on the ceiling → flaky by construction. Vibrato fills the ±1 skirts (§4.4) but not ±2.
* Repo generators vs this: `meters.SyntheticRta` leaks −10 dB into ±1 only (meters.py:600, 694-700) — softer than N=2 at ±1 yet *nothing* at ±2
  (infinitely steep); `tests/test_detector.SyntheticRta._tone` −8 dB at ±1, nothing at ±2 (tests/test_detector.py:95-101). Both snap every
  tone to the nearest band centre (never between bands, never asymmetric), have no per-band attack/release, and make the tone band dead flat
  (±0.2 dB, l.121). Consequences: prominence in tests saturates at whatever the floor gives; "two equal adjacent bands" (edge tone) never
  occurs, so `_peaks` (detector.py:291-302, loudest-of-run) and ±1 tracking are never exercised on a tone whose peak band alternates.
* Meaningful narrowness tests at this resolution: (a) **single-line test**: peak − max(levels at ±2, ±3) ≥ S dB with S ≈ 15–20 (tone-like;
  rejects broadband bumps, formant humps, cymbal wash, PA response ripple, which are ≥ 3 bands wide); (b) **cluster power**: P = powersum(±1)
  is invariant (±0.1–0.3 dB) to centre offset and vibrato while single-band level swings up to 10 dB (§4.4) — track P and the **centroid**
  c = Σ j·p_j / Σ p_j over ±1 (sub-band frequency estimate, ~5–10 c precision at 20 dB SNR); (c) −3 dB width ≤ 2 bands (edge case) — never
  "≤ 1". None of these distinguishes a sung/played partial from a ring; they distinguish *lines* from *humps*. Symmetry is not usable.

## 4. Programme material in RTA terms

### 4.1 Harmonic positions (exact): Hk sits 10·log2(k) bands above H1
H2 **+10.00** (0 c residual), H3 +15.85 (→ band +16, −18 c), H4 **+20.00**, H5 +23.22 (+23, +26 c), H6 +25.85 (+26, −18 c), H7 +28.07 (+28, +9 c),
H8 **+30.00**, H9 +31.70 (+32, −36 c), H10 +33.22 (+33, +26 c), H12 +35.85, H16 +40. Spacing H7–H8 = 1.93 bands, H9–H10 1.52, H14–H15 1.00:
**above H7 adjacent harmonics are < 2 bands apart and above H14 they share bands** — the series becomes a continuum; a harmonic-family test
should use H2, H3, H4 (H5, H6 as bonus) and the sub-octave (H1 might be the candidate's *own* H2: check −10, −15.85, −20 too). Because the
candidate itself is off-centre by up to ±60 c, Hk lands at offset_1 + residual_k: test bands {+10±1}, {+16,+15}, {+20±1} using cluster power, not
single bands. Hammond drawbars = 16' (−10 bands), 5⅓' (+5.85), 8' (0), 4' (+10), 2⅔' (+15.85), 2' (+20), 1⅗' (+23.2), 1⅓' (+25.85), 1' (+30).

### 4.2 Sources (F0 range → RTA bands; partial levels re H1; durations; modulation)
* **Voice** F0 80–1100 Hz (bands 20–58; male speech/singing 90–350 → 22–42, female 170–1100 → 31–58). Glottal source −12 dB/oct + radiation
  +6 → partials fall ~−6 dB/oct *before* formants; F1 300–900 Hz (bands 39–55), F2 0.9–2.5 k (55–70), F3 2.5–3.5 k (70–75), singer's formant
  2.5–3.2 k. Net: for sung A3 (220, band 35) H2–H4 (bands 45, 51, 55) are typically within −6…+6 dB of H1 (often *above* it on open vowels);
  H5–H10 −10…−25 dB; closed vowels /u/,/i/ at high pitch: H1 dominant, H2 −15…−30 (near-sine risk, esp. falsetto/head voice > 500 Hz and
  soprano > 700 Hz where F1 tracks H1). Notes 0.1–1 s typical, phrase-final holds 2–6 s (up to ~10 s). Vibrato 5–7 Hz, ±30–70 c pop, ±70–150 c
  classical, develops 200–400 ms into the note; onset scoops 100–300 c over 80–200 ms; jitter ±5–15 c; shimmer 0.5–1.5 dB.
* **Bass guitar** E1–G2 open strings 41–98 Hz (bands 11–23), played range to ~400 Hz (band 44). H2/H3 frequently ≥ H1 by 3–10 dB (pickup
  position, fresh strings, small cabs); through tops with 50–70 Hz HPF (Alto TS-class) H1 of low notes is −10…−20 dB → **the RTA peak of a bass
  note is often its H2 (80–200 Hz) with H3 at +5.85 bands above that peak** (a "1.5× partner" is a bass tell). Notes 0.2–2 s, dead-steady
  pitch (±5 c) unless slid; attack 10–30 ms (→ analyser-limited at LF, §1), decay 3–8 dB/s while held.
* **Synth/808 sub-bass**: sine or near-sine 35–65 Hz (bands 8–17), H2 −25…−40 (clean 808) — **no detectable family**; 0.3–2 s with exponential
  decay 10–30 dB/s and usually a pitch glide of −200…−1200 c over 100–400 ms (peak band walks down 2–10 bands — `band_tolerance: 1` per frame,
  detector.py:109/391-396, *follows* it as one candidate); distorted 808s add H2/H3 at −10…−20. Sustained synth sub (reese/sine pad): steady
  for bars (2–8 s), pure → the single hardest LF case; only the frequency prior, "moves with the chords", ends, and probe/common-mode 1:1
  tracking separate it.
* **Electric guitar** E2–E6 82–1320 Hz (bands 21–61). Plucked: H2–H6 within −3…−15 dB at onset, upper partials decay 2–4× faster than H1, so a
  note held > 2–3 s (let ring, compressor, E-bow, neck pickup + tone down) converges to H1 (+H2 −15…−25) = near-sine; finger vibrato ±20–50 c
  @ 4–6 Hz, bends 100–300 c. Deliberate amp feedback *is* regenerative feedback (indistinguishable by physics; only "which channel" differs).
  **Acoustic guitar**: body Helmholtz 90–110 Hz (band 22–24) and top-plate 180–220 Hz (32–35) modes → the classic 100–200 Hz stage howl through
  wedges: LF feedback is real for this source (§4.5).
* **Keys**: piano — hammer partials H1–H8 strong at onset, fast HF decay, slight inharmonic stretch (irrelevant at 120 c); sustain-pedal chords =
  many stable lines 1–5 s. Rhodes/Wurli — tine: H1 + few partials, bark at onset then near-sine sustain 1–4 s. **Hammond**: additive sines per
  drawbar (ratios above); 8' alone or 8'+16' flutey registrations = one or two pure lines held as long as the key (pads of 2–10 s); Leslie adds
  AM+FM 0.8 Hz (chorale) / 6.7 Hz (tremolo), ±1–3 dB, ±10–20 c. Synth leads: saw (all harmonics −6 dB/oct), square (odd only: H3 −9.5, H5 −14,
  H7 −17 — same signature as a *clipped* howl), sine/triangle leads (G-funk whistle lead, theremin-style) = pure; portamento common. Pads:
  attack 0.3–2 s → **10–40 dB/s rise for up to 2 s = the ring signature verbatim**, sustained 2–16 s, often chorus-detuned (beating 0.5–3 Hz,
  2–6 dB level wobble — a useful non-ring tell).
* **Flute/whistle/recorder/piccolo**: flute C4–C7 262–2100 Hz (bands 38–68): H1 dominant, H2 −10…−20 (low register stronger), H3 −20…−35,
  breath noise broadband −30…−45 re H1; top octave ≈ pure sine. Human whistling 0.8–3 kHz (bands 54–73): **pure sine, no family, 0.3–2 s,
  with 5–7 Hz vibrato ±30–80 c or drift/glide** — sits exactly in the vocal-mic feedback band; audience whistles are loud. Tin whistle similar.
* **Cymbals/hi-hat**: dense inharmonic modes 300 Hz–16 kHz; at 1/10 oct above ~2 kHz effectively continuous broadband; crash: +15–25 dB across
  bands 60–97 within 1 frame, decay 1–4 s (10–20 dB/s); ride bell/ping shows 2–5 discrete lines 1.5–5 kHz for 0.5–2 s (multiple simultaneous
  non-harmonic lines = looks like "several rings at once"). A wash raises the HF floor 15–20 dB → masks/reduces prominence of a real 3–8 kHz ring
  and, as it decays at 10–20 dB/s, a *constant* ring underneath gains prominence at +10–20 dB/s → apparent growth without any level change.
* **Kick**: 45–100 Hz thump (bands 12–24; often tuned 55–65 → 15–17), pitch drops 200–700 c through the 50–300 ms decay, beater click 2–5 kHz
  broadband 5–15 ms. Through the analyser (τ ≈ 250–300 ms at band 15) a 100 ms kick never reaches steady state: displayed peak ≈ 4–8 dB below
  true, rise over 3–5 frames (again 20–60 dB/s), fall governed by `decay`. Pattern at 120–128 bpm = every 9–10 frames; four-on-the-floor +
  decay ≥ 1 s → quasi-permanent 50–70 Hz peak with 2 Hz sawtooth AM, no harmonic family (H2 −15…−30) → LF false-positive generator #2 after
  bass. **Snare** 150–250 Hz body (bands 30–37) 100–200 ms + wires 2–10 kHz broadband; **toms** 70–300 Hz decaying near-sines 0.3–1.5 s with
  −100…−300 c pitch drop (floor tom 70–100 Hz: a textbook "ring" to the eye; tom+mic resonance also *causes* real LF feedback on stages).
* **Bowed strings**: full harmonic series (−6…−9 dB/oct), vibrato ±20–35 c @ 5–7 Hz, notes up to 10 s. **Brass/sax**: rich, H2–H6 near H1;
  loud; vibrato/AM. **Accordion/harmonica/bagpipe drones**: steady harmonic-rich lines for 10+ s (harmonic test passes them as music; duration
  and stability would not).
* Inharmonic tonal percussion — bells, glockenspiel, celesta, vibraphone (with motor: 4–8 Hz AM tremolo), steel pan, handpan, triangle
  (lines 2–8 kHz, 1–5 s), crotales, singing bowls, wine glasses: prominent stable lines whose partners are at non-integer ratios (2.76, 5.40… for
  bars) → a ×2/×3/×4 test finds **no family**; durations 1–8 s; no vibrato. Tuning forks/test tones/sine sweeps/DJ risers likewise.

### 4.3 Sources with NO detectable harmonic family at RTA resolution and realistic SNR (irreducible FP set for a harmonic test)
Clean 808/sine sub-bass and sustained sine synth bass; Hammond single-drawbar (8', 16') and soft flute-stop registrations; flute/recorder/
piccolo upper register; whistling (performer or crowd) and tin whistle; sine/triangle synth leads and theremin; late-sustain/E-bow/compressed
clean guitar notes and natural harmonics (flageolets); Rhodes sustain; falsetto/head-voice and soprano closed vowels above ~500–700 Hz;
humming; toms, kick (short but re-triggered and stretched by `decay`); all inharmonic idiophones (bells, glock, celesta, vibes, steel pan,
handpan, triangle, bowls); test tones, risers, sweeps; deliberate guitar-amp feedback; **and any harmonic note whose H2–H4 fall below the local
floor**: Hk prominence ≈ P1 − D_k + (floor tilt, +1…+4 dB over 10–20 bands). With typical D_2..D_4 = 6–20 dB and ±2–4 dB band noise needing
≥ 6 dB (3–5-frame average) to call a partner "present", **absence of family is evidence only when P1 ≳ 20–28 dB**; for 12–20 dB candidates the
harmonic test can confirm "music" (partners happen to be strong) but can never confirm "feedback". Also note the converse impostors: a
**hard-clipped howl** (RTA 0.0 = clip, or amp/speaker limiting) carries odd harmonics H3 −9.5, H5 −14, H7 −17 dB (square-wave limit; soft
clipping −20…−40) → a naive family test calls the worst howl "music"; cues: level within 1 dB of 0.0, partners *odd-only* and appearing *after*
H1 exceeded ≈ −6 dB (distortion products grow 2–3 dB per dB), whereas instrument partials onset together with (or, through the analyser at
LF, *before*) the fundamental. Two simultaneous genuine rings are unrelated room/mic/speaker response peaks; the chance that a second ring falls
within ±1 band of ×2, ×3 or ×4 of the first is ≈ 9/40 ≈ 20 % for rings spread over 1–8 kHz — not negligible; require ≥ 2 partners or H2∧H3.

Secondary evidence that separates the no-family sources from feedback: (i) **they end** — melody/whistle 0.1–2 s, bass 0.2–2 s, toms ≤ 1.5 s,
pads/organ ≤ 8–16 s; a ring below threshold decays only when gain drops, above threshold never; (ii) **they move with the music** — successive
events form scale steps (1–2 bands = 100–200 c) and chord changes every 2–4 s; a ring recurs at the *same* centroid ±10 c every time (keep a
per-session histogram of line centroids: recurrence at a fixed sub-band frequency across different chords is ring/room-mode evidence;
recurrence at 12-TET-related pitches that change with sections is music); (iii) **their level tracks the master exactly** (+1.00 dB per dB,
§0.3/§5) while a loop within 6 dB of threshold over-responds ≥ 2×; (iv) **they sit where instruments play**: sine bass < 100 Hz (bands < 24),
whistles 0.8–3 k; (v) **micro-modulation**: vibrato/beat/tremolo/Leslie give 0.5–6 dB and ±0.2–0.8-band centroid wobble at 0.5–8 Hz; a ring's
centroid sd is < 0.05 band and its level path is monotone/exponential (air movement ±0.2–0.5 dB at < 2 Hz — not zero: the corpus must not
make rings exactly flat either); (vi) pitch glide (808, toms, scoops, portamento) — a ring does not glide (it can hop to another mode: new line,
old one decays).

### 4.4 Vibrato/AM through the bands (simulated, N=3 skirts, τ=1/Δf, 20 fps; `a3_vibrato.py`)
1 band = 120 c. Centred tone: ±30 c invisible (0.0 dB); ±70 c: peak-band swing 1.7 dB, 39 % of frames step down > 1 dB (each restarts the growth
window, detector.py:320-324), ±1 neighbours fill from −18 to −9.6 dB (symmetric skirt widening = the visible vibrato signature); ±100 c: 4.7 dB
swing, peak band alternates every frame (5.5 Hz aliased against 20 fps). **Edge-straddling** (centre on a band edge): ±30 c → 5 dB swing, peak
band hops on 49 % of frames, per-frame slopes to ±100 dB/s; ±70 c → 8 dB swing, hops 59 %, ±140 dB/s; ±100 c → 10 dB, ±180 dB/s. In every case
**powersum(±1) varies ≤ 0.3 dB** and the centroid oscillates ±0.25–0.8 band at the vibrato rate. So: vibrato mostly stays inside one band only
when centred and ≤ ±60 c; the current single-band level history turns edge vibrato into alternating "onset"/"drop" events (guard + restart every
1–2 frames → growth ≈ 0 → *not* detected by the growth path, but a ≥ 25 dB-prominent vibrato note straddling an edge still fires the override
because `band_tolerance: 1` keeps the streak alive across hops). Tremolo (guitar amp 3–8 Hz, up to 100 % depth = tens of dB; Leslie 6.7 Hz
±3 dB; vibraphone motor) is pure AM: cluster power swings, centroid fixed — distinguish from vibrato by centroid, from ring by either.

### 4.5 Frequency prior — physical bounds by source (for REVIEW_BRIEF Q3)
Loop gain(f) = mic far-field response × speaker response × path. SM58 far field: −10 dB @ 50 Hz relative to 1 kHz (proximity boost absent at
feedback distances), presence +4–6 dB @ 4–10 kHz; Alto TS-class tops: −10 dB ≈ 55–65 Hz, horn peaks 2–8 kHz; so for **handheld vocal dynamic
into tops** the loop gain below 100 Hz is ≥ 20–30 dB under the 2–8 kHz maximum: first rings at 40/80 Hz are physically excluded (consistent
with M7: nothing genuine below ~4.5 kHz; wasted cuts at 40/80). Plausible ring range for that rig: ~160 Hz–12 kHz, prior mass 1–8 kHz. But:
lavalier/lectern/headset (omni/condensers, chest resonance, lectern cavity) ring at 150–800 Hz and 2–6 kHz; **acoustic guitar / hollow-body /
upright bass through wedges: 90–250 Hz**; **kick/tom mics (LF-boosted, inside resonant shells) near subs/drum-fill: 50–150 Hz**; turntables:
30–80 Hz rumble feedback; choir/overhead condensers: 250 Hz–4 kHz. So the window is a legitimate *per-source-class* prior (mic type + what the
bus feeds: tops only vs tops+sub vs wedges), not a constant; default for `discover_mics`-style vocal channels on Main: hard floor 120–150 Hz,
soft de-weighting below 250 Hz and above 12 kHz; for a bus flagged "instrument/drum mics + sub" the floor drops to ~45 Hz and the LF
discriminator must then lean on probe response, recurrence-at-fixed-frequency and duration instead.

## 5. Common-mode events
* **Master/bus fader move** of Δ dB: every band carrying signal shifts by Δ; bands at −128 do not; **HF bands shift within one frame, LF bands
  follow their τ_a** (band 10: ~63 % after 7 frames) — so an instant +6 dB master step produces, on bands < 40, a 0.3–0.5 s rise at 13/τ ≈
  35 dB/s (band 10) … 100+ dB/s (band 35): *transient LF "growth" on every LF peak, lagging the HF step*. A −Δ step: HF drops at once, LF and
  everything else fall no faster than the `decay` release. Detect: m_t = median over signal-carrying bands (say those > floor+10 dB in bands
  30–90, where τ_a < 1 frame) of (L_t − L_{t−1}); |m_t| ≥ 0.5 dB with inter-quartile range < 0.5 dB ⇒ common-mode step of m_t. Then either
  (a) measure candidates as level − reference (reference = same median level, or local median ±3..±8 bands, which shares the candidate's τ and
  so cancels the LF lag automatically — **prominence is already common-mode-immune; the absolute `levels` history used for growth
  (detector.py:325) is not**), or (b) hold off growth scoring for 3·τ_a(band) after a detected step. In `ring_out` the step is the server's own
  write: timestamp it and *use* it (§0.3) rather than discount it; expected programme response +1.00±0.3 dB (averaging 10–20 frames each side
  brings band noise of 1–4 dB down to 0.3–1 dB; LF needs the longer average).
* **Operator riding the master in feedback_watch**: +10 dB over 1–2 s = 5–10 dB/s on *every* prominent line simultaneously → with absolute-level
  growth, mass detections at 10 dB/s (growth score 0.5 → confidence 0.3+0.2+0.25 = 0.75 ≥ 0.7 for any ≥ 12 dB-prominent line). Same fix.
* **Autogain** (if left on): identical signature but continuous and level-dependent (slow AGC, seconds) — forced off, keep it so; detect it
  defensively as sustained non-zero m_t with no fader write.
* **Song start/stop**: broadband step ±20–40 dB; HF within a frame, LF smeared (start) or release-limited (stop); dozens of new lines appear at
  once (onset synchrony across ≥ 3 unrelated bands within 2 frames ⇒ programme event, not a ring — rings start alone). **Crowd swell/applause**:
  pink-ish broadband +10–20 dB over 0.5–2 s centred 0.5–4 kHz, few lines (except whistles); raises the floor → lowers every prominence → can
  *end* candidate streaks (a real ring masked for 1–3 s, then "re-onsets" as the swell decays: apparent growth = swell decay rate 5–15 dB/s).
* GEQ writes by CFS² itself: a −3 dB cut at GEQ band n lowers RTA bands within ±1.67 of it by up to 3 dB (PRE insert) — a known, timestamped
  local step; exclude those bands from the common-mode median and from other candidates' neighbour medians for a few frames.

## 6. Room modes and driven resonances
Axial modes f = n·343/2L: L = 4.3 m → **40, 80, 120 Hz**; 5 m → 34, 69, 103; 8.6 m → 20, 40, 60, 80. Mode bandwidth 2.2/T60 = 1.5–3.7 Hz for
T60(LF) 0.6–1.5 s — narrower than or equal to one band at 40–80 Hz (2.7–5.4 Hz): a mode is a **single-band, frequency-fixed, 6–15 dB-prominent
line that reappears whenever bass/kick energy is near it, independent of the note played** — it passes narrowness, stability, recurrence-at-
fixed-frequency, persistence (while music plays) and even "no harmonic family" (the mode selects one partial). It is *driven*: level tracks
the programme (probe: +1.00 dB/dB; a regenerative path over-responds), it decays at 60/T60 = 40–100 dB/s when excitation stops (you will see
the RTA release instead), its level correlates frame-by-frame with broadband LF energy (bands 8–30 powersum) with ~τ_a lag, and it lives
< 150 Hz where the vocal-rig prior already says "not first". The M7 "48 Hz rumble at −51.7 dB" floor and the 40/80 Hz cuts are consistent with
sub + room modes under quiet music (UNCERTAIN which; indistinguishable after the fact). Does it matter: cutting a mode on the Main GEQ is a
legitimate system-EQ move a human might make, but (a) the 1/3-oct GEQ band (40 Hz band spans 35.6–44.9 Hz) is 3× wider than the mode, (b) it
spends 1 of 4–6 notches and up to −9 dB of LF on the mains for zero gain-before-feedback benefit, (c) in `ring_out` the objective is GBF of the
mic loop. Classify as `DRIVEN_RESONANCE` (report frequency, prominence, correlation with LF programme), never auto-cut in `ring_out`; in
`feedback_watch` cut only if the operator opted into "system EQ" behaviour. A regenerative LF loop (kick mic + sub) fails the driven tests: it
keeps ringing after the hit (decay ≪ 40 dB/s, set by loop gain not T60) and over-responds to gain.

## 7. Corpus: scenarios the synthetic streams must contain

Generator requirements (both existing generators lack all of these): tones placed at a continuous frequency (cents), rendered through (i) skirts
(default N=3: −18/−36/−47 dB at ±1/2/3, asymmetric per offset; variants N=2 and "steep" −30/−60/−80 to match the M7 8 kHz datum), (ii) per-band
attack τ_a(i) = clamp(1/Δf_i, 5 ms, 600 ms) (variant BQ: 1/(πΔf_i)), (iii) release = `decay` pref model, parametrised fall rate R_rel ∈ {17, 60,
240} dB/s ÷ decay_s-scaling (UNCERTAIN semantics → sweep), (iv) optional peak-hold (max-hold H s), (v) det offset (PEAK: noise bands +9 dB, tones
+0), (vi) floor = pink-ish programme bed −45 dB/band ±σ(i) with σ from §1 (4 dB LF → 0.7 dB HF) and AR(1) autocorrelation ρ = exp(−0.05/τ_a),
(vii) clip at 0.0, floor −128, int16/256 quantisation, 20 fps with ±5 ms jitter and occasional dropped frame. Feedback model: below threshold a
line at regenerative gain 1/(1−g) over the local floor; above threshold exponential growth at (excess dB)/T_loop until limiter/clip; g follows
the master (ring_out steps) or a scripted operator; ±0.3 dB, < 2 Hz random loop-gain wander. Ground truth per event: {type ∈ FEEDBACK,
NOTE, DRIVEN_RESONANCE, COMMON_MODE, TRANSIENT}, f (Hz), acoustic onset/offset (s), for FEEDBACK also threshold-crossing time and the frame
at which RTA prominence first ≥ 12 dB (latency is judged from the latter; the analyser alone costs 200–450 ms below 80 Hz).
Pass criteria: 0 cuts on non-FEEDBACK events; every FEEDBACK detected ≤ 300 ms (≤ 6 frames) after its RTA prominence crossing (≤ 600 ms for
S12/S17-type masked/slow cases, stated per scenario); correct band ±1; no detection during COMMON_MODE frames.

| # | scenario | parameters (RTA dB, s, Hz) | truth |
|---|---|---|---|
| S1 | **Bass line + harmonics under quiet music** (M7 replica) | bed −50/band LF…−65 HF; bass notes E1 A1 D2 G1 (41.2, 55, 73.4, 49 Hz) 0.4–0.8 s each, 100 bpm, H1 −38, H2 −35, H3 −42, H4 −48 (H2 > H1: small tops), instant acoustic onset → τ_a-limited rise (17–30 dB/s displayed on bands 10–21), 5 dB/s sag while held; decay pref 1.0 | all NOTE; 0 detections. Current detector: FP expected on bands 11–21 (§0.1) |
| S2 | **Ring already established at arm** | 8.1 kHz (band 87) line at −8 dB, prominence 55 dB, flat ±0.3 dB from frame 0; bed −60; no onset ever observed; variant pinned at 0.0 (clip) with H3 24 k absent; variant 2.4 kHz clipped with H3 7.2 k −12, H5 12 k −17 | FEEDBACK from t=0; detect ≤ 300 ms *without* relying on a 25 dB magic number (e.g. line + no family + stability + level near clip); clipped variant must still be FEEDBACK despite odd partners |
| S3 | **Ring emerging during music** | full mix bed −45±σ; melody (S7-like) continues; at t=4 s loop at 3.15 kHz (band 73, +25 c off-centre) crosses threshold with 0.3 dB excess, T_loop 10 ms → 30 dB/s from −70 to limiter −6; cymbal hits every 2 s (+15 dB bands 60–97, 12 dB/s decay) | FEEDBACK onset 4.0 s; prominence ≥ 12 at ≈ 4.9 s; detect ≤ 5.2 s; no cut on melody/cymbals |
| S4 | **Sustained vocal note with vibrato** | A4 440 (band 45, +5 c) held 4 s at H1 −30, H2 −28, H3 −33, H4 −38, H5..H8 −45…−55; vibrato 5.5 Hz ramping 0→±80 c over 0.4 s; shimmer ±1 dB; onset scoop −150 c over 120 ms; variant centred on band edge (452 Hz) | NOTE; 0 detections (edge variant exercises peak-band hopping 50 % of frames) |
| S5 | **Held guitar note, full series decaying to near-sine** | E3 164.8 (band 31, −12 c) 6 s: onset H1 −32, H2 −30, H3 −35, H4 −38, H5 −42, H6 −45; Hk decays at (3+2k) dB/s so after 3 s only H1 (−41), H2 (−51) remain above bed −60; finger vibrato ±25 c 5 Hz from 1 s | NOTE; 0 detections including the late near-sine phase (the family *was* observed earlier — detector must remember lineage of a line) |
| S6 | **Operator raises the master** (feedback_watch) | full mix with 6 stable lines (organ chord C-E-G at 262/330/392 + H2s) prominences 15–25 dB; master +8 dB over 1.0 s at t=3 (8 dB/s common-mode; LF bands lag per τ_a), −5 dB step at t=7; no loop | COMMON_MODE events; 0 detections. Current detector: every line "grows" at 8 dB/s → FP |
| S6b | same in **ring_out with the server's +1 dB steps** every 1.5 s, plus a latent loop at 2.5 kHz with g rising −8 → 0.5 dB over the steps | programme lines +1.00 dB/step; ring band +1.6, +2.1, +3.0, +4.1, +6.5 dB/step then runaway at ~50 dB/s | FEEDBACK detectable *before* runaway via probe over-response (≥ +2 dB/step twice); must not cut organ lines |
| S7 | **808 / sine sub-bass line** | notes 43.7, 49, 58.3, 65.4 Hz (F1 G1 B♭1 C2; bands 11–17), each 0.9 s: attack τ_a-limited, glide −300 c over first 150 ms (starts 3 bands high), then exponential decay 12 dB/s from −25; H2 −35 rel (invisible); kick layered (S16); bed −55 LF | NOTE; 0 detections; peak prominence reaches 28–32 dB → current override fires at 255 ms on every note |
| S8 | **Organ/flute near-sine melody 500 Hz–2 kHz** | Hammond 8' only: notes C5–C6 (523–1047, bands 47–58) 0.3–1.2 s legato, H1 −28, H2 −52, H3 −58 (tonewheel leakage), Leslie chorale AM ±1.5 dB @ 0.8 Hz; flute variant: H2 −15, breath bed −60 broadband, vibrato ±20 c 5 Hz, one 3 s held note; whistle variant 1.2–2.2 kHz ±60 c 6 Hz, 0.5–1.5 s, prominence 35 dB | NOTE; 0 detections (hardest mid-band set: relies on duration/ends, key movement, micro-modulation, probe) |
| S9 | **Hard-clipped howl with harmonics** | 2.0 kHz (band 67, −20 c) crosses threshold at t=2 with 0.8 dB excess, T 7 ms → 115 dB/s (faster than the 60 dB/s guard) from −75 to 0.0 (clip) in 0.65 s; once H1 > −10, H3 (6 kHz) appears at H1−12, H5 (10 k) at H1−18, H2 (4 k, speaker distortion) at H1−30 | FEEDBACK; detect ≤ 300 ms after prominence crossing (≈ t=2.15 s) — i.e. during the 115 dB/s rise, which the current guard discards |
| S10 | **Ring exactly between two band centres** | 2590 Hz (band 70/71 edge, ±60 c): two equal bands at −3 re line, skirts −29/−29; 20 dB/s growth from −65 to −15 then plateau; 1.5 Hz ±0.3 dB wander makes the louder of the two bands alternate | FEEDBACK; one detection stream (not two), GEQ band 2.5 k; latency ≤ 300 ms |
| S11 | **Two simultaneous rings**, non-harmonic and near-harmonic variants | (a) 1.25 kHz (band 60) 15 dB/s from t=1 and 3.55 kHz (band 75) 25 dB/s from t=1.6; (b) 1.25 k and 2.52 k (band 70.1 ≈ ×2.02, within H2 tolerance) | both FEEDBACK in both variants; (b) must not be dismissed as "note + H2" (cues: different onset times, different growth rates, no H3/H4, both over-respond to probe) |
| S12 | **Ring at 100–200 Hz on acoustic guitar through wedges** | guitar strum bed: lines at 82–330 Hz + harmonics changing every 2 s (chords G-C-D), body hump +8 dB wide (bands 21–26, 3-band-wide hump — not a line); loop at 196 Hz (band 33, +32 c; coincides with G3 chord tone!) g from −4 dB → +0.3 dB at t=5 → 25 dB/s (T 12 ms) to limiter −10; τ_a(33) = 75 ms | FEEDBACK at 196 Hz from t=5, detect ≤ 600 ms; must work with the vocal LF prior relaxed (source class "instrument+wedges"); chord tones at 196 Hz before t=5 are NOTE (they end with the chord; the ring does not) |
| S13 | **Slow ring, 3 dB/s** | 5.04 kHz (band 80, +12 c) marginal loop: level −70 → −40 over 10 s (3 dB/s, excess ≈ 0.03 dB), ±0.4 dB wander @ 1 Hz, under a −55 HF bed with hi-hat 8th-notes (+10 dB bands 75–95, 40 ms) | FEEDBACK; acceptable detection window: within 1 s of prominence ≥ 12 dB (growth path useless at 3 < 6 dB/s; needs line+stability+no-family+persistence or probe) |
| S14 | **Ring onset masked under a cymbal wash** | crash at t=2.0: bands 58–97 +22 dB in one frame, decaying 12 dB/s for 2 s; ring 4.2 kHz (band 77) crosses threshold at t=2.1, 40 dB/s from −70; becomes ≥ 12 dB prominent only at ≈ t=3.0 as the wash falls (apparent relative growth = 40+12 dB/s) | FEEDBACK onset 2.1; detect ≤ 300 ms after prominence crossing (~3.3 s); no detection on the crash itself (synchronised multi-band onset) |
| S15 | **RTA decay set long** (pref not forced) | S1 + S7 material rendered with release 4 dB/s (decay ≈ 4–16 s): every bass/808 note leaves a flat, ≥ 12 dB-prominent, harmonic-less tail for 2–5 s; plus one real ring (S3-like) notched at t=8 whose acoustic level drops 20 dB instantly but displays −4 dB/s | NOTE tails: 0 detections; FEEDBACK: detect; **VERIFY must be reported as inconclusive/slow rather than "not tamed → deepen to −9"** (or the arm step must have forced decay=0.25 — test both) |
| S16 | **Peak-hold on** | S4/S8 material with max-hold 2 s per band: dead-flat plateaus after every note peak, prominence up to 35 dB, zero variance | 0 detections is only achievable if arm forces peakhold OFF; scenario exists to prove the detector *refuses to arm / flags ANALYSER_MISCONFIGURED* when ≥ N bands are exactly flat (Δ=0.000 dB) for ≥ 10 frames — real signals never are (int16/256 quantisation still shows ±1 LSB jitter on live bands) |
| S17 | **Kick drum pattern** | 4-on-floor 124 bpm (every 9.7 frames): thump 62 Hz gliding to 50 Hz over 120 ms (bands 17→14), peak −30 (displayed −35 after τ_a), decay 25 dB/s acoustic, click bands 68–78 +12 dB 1 frame; RTA decay 1.0 → band 15–17 never below −48 between hits; bed −58 | TRANSIENT/NOTE; 0 detections (periodic 2.07 Hz re-trigger at a fixed band with no family and 20–30 dB prominence: defeats "recurs at fixed frequency" unless periodicity/tempo-locking and < 100 Hz prior are used) |
| S18 | **Vibrato straddling a band edge** | G5 784 Hz placed at band 53/54 edge (796 Hz −27 c…), ±70 c @ 6 Hz, 3 s, H1 −30, H2 −36, H3 −40; peak band alternates 53↔54 on ~60 % of frames, single-band swing 8 dB, cluster power ±0.2 dB | NOTE; 0 detections; also assert the tracker keeps ONE candidate (centroid) rather than spawning/ending streaks each hop |
| S19 | **Driven room mode under bass** | mode 43 Hz (band 11, +35 c), Q=20: whenever S1/S7 bass energy within ±1.5 bands, band 11 shows +10 dB extra, lagging 4 frames, falling 40 dB/s (release-limited) when the note ends; recurs at identical centroid across all chords; ring_out steps → +1.00 dB/step | DRIVEN_RESONANCE: report, never cut in ring_out; 0 budget spent |
| S20 | **Song start / stop and crowd** | t=1: full mix appears (+35 dB broadband, HF in 1 frame, LF per τ_a); t=20: stops (release-limited fall); t=21–24 applause bed +15 dB 0.5–4 kHz with two crowd whistles 1.8 kHz/2.3 kHz, 0.8 s, prominence 30 dB, ±50 c wobble | COMMON_MODE + NOTE; 0 detections (whistles are the trap: no family, loud, in-band — they end and wobble) |
| S21 | **Synth pad swell** | saw pad chord (A2-E3-A3: 110/165/220 + harmonics to H8 at −6 dB/oct) with 1.5 s linear-in-dB attack = 20 dB/s on ~15 lines simultaneously, sustain 6 s with chorus beating ±3 dB @ 1.2 Hz, release 2 s | NOTE; 0 detections (synchronised multi-line growth + family + beating) |
| S22 | **Sub-threshold ringing tail on speech** (realistic feedback_watch) | speech-like bursts (F0 110–140 Hz + formant-shaped partials, 150–400 ms syllables, gaps 100–300 ms, bed −60); loop at 2.8 kHz with g = −2.5 dB: every syllable's energy near 2.8 k is boosted +12 dB and decays at (2.5 dB / 9 ms) ≈ 280 dB/s → release-limited on display; then operator +3 dB at t=6 → g=+0.5 → 55 dB/s growth | before t=6: FEEDBACK-RISK (report "ringing at 2.8 k, loop ≈ −2.5 dB" — desirable early warning, optional cut policy); after t=6: FEEDBACK, detect ≤ 300 ms |
| S23 | **Autogain accidentally on / gain offset** | S1 material with a slow common-mode drift −0.5 dB/s for 10 s then +8 dB re-normalisation jumps; variant: all levels +24 dB (manual gain) so bass peaks hit 0.0 clip | 0 detections; detector should flag ANALYSER_MISCONFIGURED (sustained common-mode drift with no fader write; clip on programme) |
| S24 | **Inharmonic bell/triangle lines** | triangle hit: lines 2.1, 4.6, 7.3 kHz (ratios 1:2.19:3.48), prominence 25–30 dB, decay 6 dB/s over 4 s, no vibrato; glockenspiel notes 1.3–2.6 kHz 1 s | NOTE/TRANSIENT; 0 detections (no family, in-band, stable: separable only by monotone *decay* from frame 1, synchronised onset of 3 lines, and ending) |

Analyser-ballistics matrix: run S1, S3, S7, S9, S12 under {U, BQ} × decay {0.25, 1.0, 4.0} × det {RMS, PEAK(+9 dB noise bands)} × skirts {N=2, N=3, steep}; the pass criteria must hold across the matrix, or the detector must declare which arm-time-forced settings it requires (and S15/S16/S23 prove it detects when they were not applied).

UNCERTAIN items (to be closed by the §2.6 oscillator self-test on the real desk): analyser architecture and per-band τ_a; LF skirt steepness; `decay` unit/law; `peakhold` 1..8 meaning and whether it reaches `/meters/15`; whether `gain`/autogain shift `/meters/15`; RMS averaging window; exact clip behaviour; frame jitter under load.
