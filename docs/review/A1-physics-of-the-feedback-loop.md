# Physics of the electro-acoustic feedback loop, and what it implies for the CFS² discriminator

Scope: docs/REVIEW_BRIEF.md §1 Q3/Q4 background physics; numbers for the redesign of src/x32mcp/detector.py.
Frame = one /meters/15 RTA frame, 50 ms (20 fps). RTA band i centre f_i = 10000·2^((i−90)/10) Hz, 1/10-octave
(docs/research/meters.md §4.2, lines 280-325). GEQ = 31 ISO 1/3-octave bands, cuts −3/−6/−9 (device.yaml:773, 816-817).
All derived numbers below were recomputed (scratchpad/reports/physics-loop/calc.py, calc2.py). UNCERTAIN marks
things I cannot verify from the repo or first principles.

--------------------------------------------------------------------------------------------------

## 0. Facts from the repo this brief relies on (and two it corrects)

* RTA values are dB re full scale, −128 floor, **0.00 = "clipping occurred"** (meters.md §4.2 quoting DOC p.19).
  Whether `/-prefs/rta/gain` (0…60 dB, 6 dB steps) offsets the reported values is UNCONFIRMED (meters.md:311);
  `set_rta_source` forces autogain OFF and det=PEAK but leaves `gain`, `decay` (0.25…16, log, "presumably s")
  and `peakhold` (OFF,1…8) as found (src/x32mcp/meters.py:888-921; prefs table meters.md:366-404). Every
  absolute threshold in the detector (min_level_db −45, device.yaml:797; "prominence ≥25", device.yaml:812)
  therefore floats on three unread prefs. Physics consequence: with peak-hold ON a past transient is a
  dead-flat prominent band forever (indistinguishable from a plateaued ring by any level/persistence test);
  with a long decay every note "outlasts itself" and frame-to-frame variance (predicate P5 below) is destroyed.
  **Arm-time requirement: write decay→0.25 (fastest), peakhold→0, gain→0 dB, restore on close.**
* **The RTA tap is pre-fader.** HANDOVER §4b(4): a −15 dB GEQ cut on a POST insert moved the RTA by ≤1.2 dB, the
  same cut on a PRE insert by −6 dB ⇒ the "post-EQ" RTA tap sits between the bus EQ and the post-insert point,
  i.e. upstream of bus dynamics and the bus/main **fader**. Consequences used below: (i) raising the bus master
  does NOT raise programme at the tap — only energy that has travelled master→amp→air→mic→channel→bus rises;
  (ii) the RTA plateau level of a howl is a bus level, unrelated to SPL; (iii) "operator raises the master ⇒
  every band rises together" (REVIEW_BRIEF §1 stream list) is false at this tap for a *bus-master* move and true
  for a *channel-fader/send* move. (Inference from §4b(4) + X32 signal flow; UNCERTAIN only in that Behringer
  does not document the tap point.)
* ring_out raises the master in `step_db`=1.0 dB steps with `dwell_ms`=1500 (device.yaml:826-827,
  src/x32mcp/cfs.py:1147-1199). That is a known, timed, server-owned gain perturbation = a free active probe.
* Channel HPF is desk-readable: `/ch/NN/preamp/hpon`, `hpslope` {12,18,24 dB/oct}, `hpf` 20…400 Hz log
  (device.yaml:74,103,235-236; docs/research/scales_params.md §4.3 lines 356-363). Patch rows carry `mic: bool|None`
  (src/x32mcp/patches.py:133) but no mic *kind*.
* SyntheticRta models a ring as linear-in-dB growth from −45 dB to a −3 dB cap (meters.py:633, 714-718) — correct
  shape (§1.3) but only the slow, from-silence case; a note as +30 dB over 5 frames then dead flat
  (meters.py:586-587, 690-692) — no partials, no vibrato, no decay; a GEQ cut as a brick-wall ±1/6-oct at full
  depth (meters.py:661-667) — optimistic at the band edge, see §4.1. The test generator's melody does have
  partials at +10 and +16 bands (tests/test_detector.py:120) but instant onsets and flat sustain.

--------------------------------------------------------------------------------------------------

## 1. Loop model

### 1.1 Open-loop gain
Round-trip (open-loop) transfer at frequency f:

  L(f) = M(f,θ_m) · C(f) · B(f) · S(f,θ_s) · A(f)   (complex; |L| in dB is the sum of the terms in dB)

| term | what | typical magnitude structure |
|---|---|---|
| M mic | sensitivity × polar response toward the loudspeaker (NOT toward the talker). SM58 far-field: −10 dB @50 Hz, ~flat 100 Hz–1 kHz, presence +4…+6 dB @4–6 kHz, −10 dB by 15 kHz; cardioid rear rejection −15…−20 dB @1 kHz, degrading to −10 dB at 6–8 kHz and below 150 Hz. **Proximity effect (+10…+12 dB @100 Hz at 5 cm) applies to the talker only; the loudspeaker is metres away, so the loop's LF gain is the far-field (rolled-off) response.** Omni lav/headset condensers: flat 50 Hz–16 kHz, no null. | sets the HF ceiling and much of the "where" |
| C channel | head-amp gain (M7: +44.5 dB, HANDOVER §4a) × HPF × channel EQ × compressor (static gain; above threshold the compressor *reduces incremental loop gain*, see §1.4) × fader/send | HPF sets the LF floor: Butterworth-like n=2/3/4 (12/18/24 dB/oct) is −3 dB at fc, −7/−10/−13 dB at 0.7·fc, −12/−18/−24 dB at 0.5·fc |
| B bus | bus EQ × GEQ insert × bus master | the two things CFS² moves |
| S speaker | amp gain × driver/crossover response × directivity toward the mic × limiter. Tops: −10 dB ≈ 50–60 Hz; subs 35–45 Hz…80–120 Hz LPF; HF horn beams (rear radiation −20…−30 dB at 4 kHz but only −3…−6 dB at 100–300 Hz: **a mic behind the PA sees an LF/low-mid-tilted loudspeaker**) | LF cut-off of the box is a hard floor; directivity reshapes the envelope by ±15 dB |
| A air/room | direct path 1/r (−6 dB per distance doubling) + discrete reflections (comb: a reflection of relative amplitude a and extra delay τ_r adds ripple ±20·log10((1+a)/(1−a)), peaks every 1/τ_r: lectern top 0.3 m → 1.1 kHz spacing; floor bounce 1–2 ms → 500 Hz–1 kHz) + reverberant field (beyond critical distance r_c ≈ 0.057·√(QV/T60): 1.4 m in a 60 m³ studio, 2–3 m in a pub, 5–8 m in a hall) whose transfer function above the Schroeder frequency f_S = 2000·√(T60/V) (183 Hz studio, 126 Hz pub, 55 Hz hall) is quasi-random with maxima ≈10 dB above the mean (Schroeder statistics; exact excess UNCERTAIN, 8–12 dB is the textbook range) spaced on average ≈4/T60 Hz apart (5 Hz at T60 = 0.8 s); below f_S, isolated room modes with Q 10–30 give +10…+20 dB peaks at fixed LF frequencies | decides *which* candidate wins; makes distant mics "ring anywhere" |

### 1.2 Barkhausen condition and candidate density
Self-oscillation where |L(f)| ≥ 1 (0 dB) and arg L(f) ≡ 0 (mod 2π). Phase is dominated by pure delay:
arg L ≈ −2πfτ + φ_filters(f), τ = electronic + acoustic delay.

* Electronic: X32 local in→out ≈ 0.8 ms; AES50 stage box +~0.1 ms; active-speaker DSP 1–3 ms (Alto TS class,
  UNCERTAIN exact); digital wireless mic 2–7 ms (analog wireless ≈0); sub DSP/FIR + bandpass-box group delay
  5–20 ms at 40–60 Hz.
* Acoustic: d/343 s: 1.2 m wedge 3.5 ms; 3 m 8.7 ms; 10 m 29 ms. Reverberant-dominated loops behave as if
  τ_eff ≈ energy-weighted mean arrival ≈ T60/13.8: 36 ms (T60 0.5 s) … 110 ms (1.5 s).

| situation | τ | candidate spacing 1/τ |
|---|---|---|
| vocal mic → wedge 1.2 m, wired | ≈5 ms | 200 Hz |
| vocal → active tops 3 m | ≈11 ms | 90 Hz |
| lav → tops 6 m + digital RF | ≈20–25 ms | 40–50 Hz |
| distant/area mic, reverberant pub/hall | 35–110 ms | 30 → 9 Hz |
| kick mic → drum-fill sub 1.5 m + sub DSP | 15–30 ms | 30–65 Hz |

So between 250 Hz and 8 kHz there are 40 (wedge) to 800+ (reverberant) phase-eligible frequencies. Which one
oscillates first is purely a |L| ranking: the candidate under the highest point of the magnitude envelope. With a
smooth envelope (direct path, presence peak modelled as +5 dB at 5 kHz, −3 dB at ±½ oct) and 200 Hz spacing the
top five candidates lie within 0.04–0.3 dB of each other (calc2.py) — i.e. **after the first is notched the next
rings at essentially the same master setting** (exactly M7: "pushed the master higher, the next band up rang",
HANDOVER §4b). Real envelopes carry ±3–6 dB fine structure from reflections and transducer ripple, so in practice
each notch buys 0.5–3 dB; and since a band can only go to −9 dB, **a GEQ ring-out can never buy more than ≈9 dB
of gain-before-feedback and typically buys 3–6 dB** before candidates become dense. This is the physical basis for
a small notch budget (device.yaml:818 = 6) and for reporting diminishing returns when successive first-ring
master levels (cfs.py:1038 `first_feedback_master_db`) differ by <1 dB.

### 1.3 Growth above threshold
Each round trip multiplies the amplitude at the oscillating frequency by |L|. With excess e = 20·log10|L| dB > 0:

  **growth rate R = e / τ  dB/s** — exponential in amplitude = exactly linear in dB (the SyntheticRta shape is right).

| e \ τ | 5 ms | 8 ms | 11 ms | 20 ms | 35 ms | 70 ms |
|---|---|---|---|---|---|---|
| 0.1 dB | 20 | 12 | 9 | 5 | 2.9 | 1.4 |
| 0.3 dB | 60 | 38 | 27 | 15 | 8.6 | 4.3 |
| 0.5 dB | 100 | 63 | 45 | 25 | 14 | 7 |
| 1 dB | 200 | 125 | 91 | 50 | 29 | 14 |
| 2 dB | 400 | 250 | 182 | 100 | 57 | 29 |
| 3 dB | 600 | 375 | 273 | 150 | 86 | 43 |
| 6 dB (singer cups the mic / walks in front of a wedge) | 1200 | 750 | 545 | 300 | 171 | 86 |

Implications:
* Real growth spans **≈1 to >500 dB/s**. The detector's growth window 6…60 dB/s (detector.py:101,103;
  device.yaml:802 + default onset guard) covers only near-threshold, long-loop cases. In ring_out with +1 dB steps
  the excess just after the crossing step is ~U(0,1] dB, so on a wedge (τ≈5 ms) R ~U(0,200] dB/s and **≈70 % of
  genuine ring-out howls exceed the 60 dB/s onset guard** and are scored growth = 0 (then wait for the 25 dB/6-frame
  override). On tops at 3 m (τ≈11 ms) ≈35 % exceed it. In feedback_watch gain arrives in human-sized steps (+3…+6 dB
  fader shove, mic cupping +6…+10 dB at 2–4 kHz, performer leaning into a wedge) so e of several dB and R of
  hundreds of dB/s are the *normal* case: −60 → −10 dBFS in 2–5 frames.
* Time available: from a −60 dB gate to −10 dBFS is 50 dB: 10 s at 5 dB/s, 2.5 s at 20, 0.5 s (10 frames) at 100,
  0.25 s (5 frames) at 200, 2 frames at 400 dB/s. A detector that needs an observed slope over ≥3 frames is
  structurally late for e ≥ 1 dB on short loops; single-frame or 2-frame evidence (narrowband line with no
  partials at a level already far above that band's recent history) must be admissible.
* The rate is set by e and τ, **not by frequency** — but what the RTA *shows* is rate-limited by the analyser at
  LF (§1.6), which is where the confound with music comes from.

### 1.4 What stops the growth (plateau)
Growth continues until some element's incremental gain falls so that |L| = 1 exactly (describing-function
equilibrium). In order of where it usually happens:
1. **Loudspeaker DSP limiter / amp clip** (active boxes): acoustic output pinned at the box's max SPL
   (115–125 dB @1 m for a 12" top/wedge). The desk signal is *not* clipped; the RTA (pre-fader bus tap) plateaus at
   whatever bus level drives the amp to limit — anywhere from −30 to 0 dBFS depending on amp gain and master
   position. A limiter plateaus cleanly (few harmonics); a clipping amp radiates harmonics that the mic hears (§2.2).
2. **Channel compressor** on the mic (vocal comp 3:1–6:1): above its threshold the incremental loop gain drops by
   (1−1/ratio); the howl settles a few dB above comp threshold — a **clean, unclipped, rock-steady sine at a moderate
   level** (typ. −25…−10 dBFS at the tap). This is the M7 "60 dB-prominent 8 kHz line at confidence 0.50 for 15 s"
   case (HANDOVER §4b(2)): plateaued, no growth, not at clip.
3. **Desk bus/output clip** at 0 dBFS: RTA band reads exactly 0.00 (the documented clip flag) — trivially decisive,
   and the code must never treat 0.0 as falsy (cfs.py:1204 already notes this).
4. Nothing (rare): only air/speaker nonlinearity; level runs to the physical maximum.

So: **a plateaued howl at an arbitrary level with no growth and no clipping is the physically expected steady
state**, not a corner case; growth is observable only during the first 0.1–3 s.

### 1.5 Below threshold: regeneration and ringing
With deficit d = −20·log10|L| > 0 at a phase-aligned candidate, the closed-loop response to any excitation
(voice, programme spill, room noise) at that frequency is boosted by 1/(1−|L|):

| deficit d | |L| | regenerative boost | ring-down T60 of the loop alone, τ = 5 / 10 / 30 ms (= 60·τ/d) |
|---|---|---|---|
| 6 dB | 0.50 | +6.0 dB | 0.05 / 0.10 / 0.30 s |
| 3 dB | 0.71 | +10.7 dB | 0.10 / 0.20 / 0.60 s |
| 2 dB | 0.79 | +13.7 dB | 0.15 / 0.30 / 0.90 s |
| 1 dB | 0.89 | +19.3 dB | 0.30 / 0.60 / 1.8 s |
| 0.5 dB | 0.94 | +25.0 dB | 0.6 / 1.2 / 3.6 s |
| 0.2 dB | 0.98 | +32.9 dB | 1.5 / 3.0 / 9.0 s |
| 0.1 dB | 0.99 | +38.8 dB | 3 / 6 / 18 s |

This is the audible "ringy" PA: transients acquire a pitched tail at the candidate frequency whose decay time
exceeds the room's once d ≲ 1 dB. Between candidates (phase ≈ π) the same gain change slightly *reduces* level
(1/(1+|L|)). A human ringing out a system cuts at this stage, before oscillation; the RTA shows it as a narrow line
that appears on every syllable/beat and decays linearly in dB at d/τ dB/s.

**Response to a +1 dB master step** (the ring_out probe), band level change at the tap:
* programme injected electrically into the bus (playback, DI, other mics' direct sound): **+0.0 dB** (pre-fader tap);
* the open mic's acoustic pickup of the PA (spill), far from any candidate: **+1.0 dB** (one more dB round the loop);
  the observed programme band change is therefore between 0 and +1 dB depending on the spill fraction;
* a candidate at deficit 4→3 dB: **+3.0 dB**; 3→2: **+4.0**; 2→1: **+6.5**; 1.5→0.5: **+10.1**; 1→0: unbounded
  (slope flips from decaying tails to steady growth at e/τ).
So the differential gain d(band)/d(master) is ≤1 dB/dB for anything that is not regenerating and ≥3 dB/dB within
4 dB of threshold. The reverse probe (−1 dB) is equally informative and *safe*: a regenerating line drops 3–10 dB
or its growth turns into decay at (1−e)/τ ≥ tens of dB/s; programme drops ≤1 dB. Statistical caveat: music band
levels fluctuate ±5–10 dB frame-to-frame, so the comparison needs medians over ≥20 frames each side of the step
(≈1 s + 1 s) unless the room is quiet; with noise-only excitation 5+5 frames suffice. In feedback_watch the server
does not command the gain, but it *does* see channel fader/send moves pushed by `/xremote` (connection.py:831),
so the same differential test is available passively whenever the operator moves something.

### 1.6 What the analyser can and cannot show (time–frequency bound)
A filter that resolves bandwidth Δf cannot settle faster than ~1/Δf. For 1/10-octave bands Δf = 0.0693·f. Using a
2nd-order band-pass envelope (τ_env = 1/(πΔf)) as the *fastest* plausible analyser (a real 4th–6th-order CQ bank or
multirate FFT is slower and more sigmoid; the X32's implementation is UNCERTAIN — but M7's ≥12 dB-prominent lines
at bands 10 and 20 prove the LF bands really are ≲1/3-oct sharp, hence really are slow):

| band | f | Δf | τ_env | instant onset: last 10 dB (−10→−1) takes | apparent slope | last 3 dB slope |
|---|---|---|---|---|---|---|
| 0 | 19.5 Hz | 1.35 Hz | 235 ms | 432 ms = 8.6 frames | 21 dB/s | 6 dB/s |
| 10 | 39 Hz | 2.7 Hz | 118 ms | 216 ms = 4.3 frames | 42 dB/s | 13 dB/s |
| 17 | 63 Hz | 4.4 Hz | 72 ms | 133 ms = 2.7 frames | 68 dB/s | 21 dB/s |
| 20 | 78 Hz | 5.4 Hz | 59 ms | 108 ms = 2.2 frames | 83 dB/s | 26 dB/s |
| 30 | 156 Hz | 10.8 Hz | 29 ms | 54 ms = 1.1 frames | 167 dB/s | 51 dB/s |
| 40 | 313 Hz | 21.7 Hz | 15 ms | 27 ms | sub-frame | 103 dB/s |
| 60 | 1.25 kHz | 87 Hz | 3.7 ms | 7 ms | sub-frame | sub-frame |
| 87 | 8.1 kHz | 563 Hz | 0.6 ms | 1 ms | sub-frame | sub-frame |

So an *instantaneous* bass-note onset is rendered below ~150 Hz as a decelerating 2–9-frame rise whose tail sits at
6–50 dB/s — squarely inside the 6…60 dB/s growth window — and the onset guard (detector.py:103, 335-339) makes it
worse, because it discards the fast early part and scores exactly the slow tail. Above ~300 Hz onsets are sub-frame
(guard fires, growth window restarts, plateau scores 0). **The growth feature is confounded with frequency by the
instrument, independent of the source.** Add the RTA `decay` release (unknown setting at M7) stretching every
release into a slow linear-in-dB fall, PEAK detection riding successive kick/bass hits, and 80 Hz = 2×40 Hz being a
harmonic pair of one bass line, and the M7 40/80 Hz false positives (HANDOVER §4b) need no other explanation.
Corollary for any redesign: a growth-rate feature must be normalised per band by the analyser's own rise time
(ignore slopes the analyser could produce from a step), or dropped.

--------------------------------------------------------------------------------------------------

## 2. Spectral signature of feedback vs programme

### 2.1 Line width and band occupancy
A sustained oscillation is a single sinusoid; its linewidth is set by amplitude/phase noise, ≪1 Hz. While growing
at R dB/s the exponential envelope broadens it to ≈σ/2π with σ = R·ln10/20: R = 50 dB/s → 0.9 Hz, 200 dB/s → 3.7 Hz —
still below the narrowest RTA band (1.35 Hz at band 0, 2.7 Hz at 39 Hz, 140 Hz at 2 kHz). So a ring occupies **one
RTA band plus the analyser's own skirt leakage**: for a centred tone the ±1 neighbours read −7 dB (2nd-order
skirts) to −18 dB (6th-order) below the peak; a tone on a band edge reads −3 dB in two adjacent bands and
−12…−25 dB in the next. The leakage pattern is time-invariant for a ring. (X32 skirt shape UNCERTAIN; SyntheticRta
assumes −10 dB, meters.py:600; the test generator −8 dB, tests/test_detector.py:110-116.) The ±1-band pattern also
lets the true frequency be interpolated to ≈±0.02 oct (log-frequency centroid of the 3 bands), which matters for
GEQ band choice (§4.1).

Programme in the same representation: a musical note is a harmonic family — partials at +10.0 (2f), +15.85 (3f),
+20.0 (4f), +23.2 (5f), +25.85 (6f), +28.1 (7f), +30 (8f) RTA bands with **co-moving envelopes** (common onset within
the analyser rise time, common release) and partial levels within 0…−15 dB of the fundamental for voice, bass, guitar,
piano, brass (bass guitar/piano LF notes often have 2f ≥ f). Vibrato (voice, strings, lead guitar: ±0.3…±1 semitone =
±2…6 % at 5–7 Hz) against a 7 % band width shows as 5–7 Hz anti-correlated modulation between two adjacent bands —
resolvable at 20 fps. Attack transients are broadband for 1–2 frames. Plucked/struck notes decay 3–15 dB/s after the
attack; bowed/blown/sung/organ/synth-pad notes sustain flat.

### 2.2 Harmonics of a howl
A linear loop produces none. Harmonics appear only when something clips:
* symmetric hard clip (amp rails, digital 0 dBFS): odd harmonics only; 3rd at −30 dB for 1 dB overdrive, −17.5 dB
  for 3 dB, −13 dB for 6 dB, → −9.5 dB (square-wave limit); 5th −33…−14 dB;
* asymmetric clip / driver excursion: 2nd and 3rd at −20…−40 dB (3–10 % THD at full tilt);
* DSP limiter (most active boxes): ≤ −40 dB — effectively none.
So a howl's partials are (a) absent until the fundamental is already at limiting, i.e. tens of dB prominent and the
loudest line in the spectrum, and (b) ≥10 dB, typically ≥15–30 dB, below the fundamental — versus 0…−15 dB for
musical partials. A harmonic-family test should therefore count a partial only if its prominence is within ~15 dB
of the fundamental's, and should be overridden by sheer level/prominence anyway. For f ≥ 5 kHz, 2f ≥ 10 kHz (band ≥
i+10 ≥ 90) where programme energy is low and 3f is off the analyser: the test is vacuous but harmless there. No
subharmonics ever (a line at f/2 means f is somebody's 2nd harmonic → programme; that is the 40/80 Hz M7 pair).

### 2.3 Frequency stability, hops, coexistence
* The frequency is fixed by geometry and filters; thermal drift is negligible over a session. Moving the mic by Δd
  slides every candidate by δf = −f·Δd/(c·τ): 12 cm of movement moves a candidate by one full RTA band for τ = 5 ms
  (26 cm for τ = 11 ms) at *any* frequency. But the winner is whichever candidate sits under the envelope maximum,
  so a hand-held mic's ring **hops between neighbouring candidates (200 Hz apart on a wedge) while staying within
  ~±½ candidate spacing of the envelope peak** — net wander ≤ ±1 RTA band above 1.5 kHz, up to ±2–3 bands at
  300–800 Hz; in reverberant loops (candidates a few Hz apart, fine structure re-randomised by any movement) hops of
  1/3 octave occur. Fixed mics (lectern, lav on a still presenter, drum mics, choir mics): stable to ≪1 band.
  `band_tolerance: 1` (device.yaml:814) is right for tracking; the *cooldown/merge* radius should be wider (±2) for
  hand-helds so a hop is treated as the same event, not a new budget line.
* Two or more simultaneous rings: yes — two open mics, or one mic with two loudspeaker paths, or simply two
  candidates within the same fraction of a dB (§1.2 shows how common that is). Once one line reaches limiting the
  nonlinearity usually suppresses the other (gain compression), so *sustained* coexistence is mostly seen just
  above threshold or with a limiter-free chain. Sequential appearance after a notch is the norm (M7).
* Probability that two coexisting rings are an octave apart within RTA resolution (2f ± 1 band): with independent
  candidates ≈3/(number of plausible bands ≈ 40–55) ≈ 6 %, times the modest prior of simultaneous independent rings
  (order 10 % of events) → <1 % of detections. Exception that is NOT coincidence: a single strong reflection or a
  box resonance produces comb peaks at exact multiples of 1/τ_r, so harmonically related *candidates* exist; they
  still do not co-onset and their levels are unrelated, which is what distinguishes them from a note's partials.

--------------------------------------------------------------------------------------------------

## 3. Feasible frequency range

### 3.1 What bounds it
Lowest possible ring: the higher of (i) the loudspeaker path's LF cut-off (tops alone 50–70 Hz; with subs 35–45 Hz;
no LF path at all if the mic is not routed to the subs, e.g. aux-fed subs), (ii) the frequency where the channel
HPF has eaten the margin: relative to the pass-band a Butterworth n = 2/3/4 HPF costs 3 dB at fc, 7/10/13 dB at
0.7·fc, 12/18/24 dB at 0.5·fc — since envelope peaks elsewhere are rarely >10 dB below the global maximum,
**f_low ≈ 0.7·fc (18/24 dB/oct) to 0.6·fc (12 dB/oct)** is a defensible physical floor when the HPF is on,
(iii) the mic's far-field LF roll-off (dynamic vocal mics −6…−10 dB below 80 Hz; condensers flat), offset by
(iv) room modes (+10…+20 dB, Q 10–30, below f_S ≈ 50–180 Hz) and LF-omni loudspeaker radiation toward mics behind
the PA. Highest possible ring: mic HF roll-off (SM58-class −10 dB by 15 kHz → practical ceiling 9–10 kHz; condensers
to 16 kHz+ → 12–13 kHz seen in practice), horn beaming (on-axis only), air absorption (0.1–0.3 dB/m at 10–16 kHz:
minor indoors), and the ear/engineer adding HF boost to lavs. Nothing physical prevents 10–12 kHz feedback with
condenser headsets/lavs on-axis of a horn.

### 3.2 By source (typical practice; ranges are where sustained feedback is physically plausible / most likely)

| source | mic & placement | τ (direct) | usual HPF | plausible range | most likely bands | notes |
|---|---|---|---|---|---|---|
| (a) hand-held dynamic vocal → FOH tops (mic 2–6 m behind/beside PA), subs aux-fed or vocal HPF'd out of them | SM58 cardioid, presence +5 dB @4–6 kHz, far-field LF roll-off | 7–20 ms; reverberant in small rooms | 100–150 Hz, 12–24 dB/oct | **≈150 Hz – 9 kHz** (floor = 0.7·fc ≈ 70–105 Hz only if a room mode AND an LF path exist; else ~150–200) | 200–500 Hz (rear-radiated low-mid + room gain), 2–6 kHz (presence peak via reflections/sidefill); 6–8 kHz when the mic is in front of/under a horn (M7: 5 and 8 kHz, nothing below 4.5 kHz, HANDOVER §4b) | 40/80 Hz feedback from this rig is not possible: HPF −10…−30 dB, mic −8 dB, and (M7) the sub path carries playback, not the HPF'd mic |
| (a') same mic → stage wedge at 1–1.5 m in the rear null | | ≈5 ms → candidates every ~200 Hz | same | **≈150 Hz – 10 kHz** | 250–500 Hz (wedge/floor coupling), 800 Hz–1.25 kHz, 2–4 kHz (null is shallow off-axis, presence peak), 6.3–8 kHz (rear-lobe HF) — the classic ring-out set | growth fast (τ small): 1 dB excess = 200 dB/s |
| (b) lavalier / headset omni condenser → FOH | flat, omni (no null), chest lav +3…+5 dB @600–800 Hz and HF shadowed → engineer boosts HF; needs 15–25 dB more gain than a hand-held, so it lives within a few dB of threshold | 10–25 ms (+2–7 ms digital RF) | 120–160 Hz | **≈100 Hz – 12 kHz** | lav: 200–800 Hz in reflective rooms, 2–8 kHz; headset: 1.5–8 kHz, occasionally 10–12 kHz | sub-threshold ringing (§1.5) is chronic; hops large (reverberant) |
| (c) lectern gooseneck cardioid condenser, talker at 30–50 cm | comb from lectern surface (1/τ_r ≈ 1 kHz ripple) | 10–25 ms | 100–150 Hz | **≈100 Hz – 11 kHz** | 160–400 Hz (room + lectern cavity), 2–5 kHz | fixed geometry → very stable frequency |
| (d) wedges generally (any vocal/instrument mic) | | 4–6 ms | per channel | **≈125 Hz – 10 kHz** | 250 Hz–4 kHz + 6–8 kHz | 15" wedges reach 60 Hz: an un-HPF'd instrument mic can ring a wedge at 100–160 Hz |
| (e) kick / floor-tom / bass-cab mic near subs or a drum-fill sub | Beta52/D112-class: LF-boosted, usually no HPF (or 30–50 Hz), often gated (gate opens on every hit and a growing LF ring holds it open); tom shells and the kick's resonant head are acoustic resonators at 60–200 Hz that sustain the loop | 15–30 ms incl. sub DSP + box group delay → candidates every 30–65 Hz, growth slow (0.5 dB → 15–30 dB/s) | off / 30–60 Hz | **≈35 Hz – 250 Hz** for the LF mode (plus 2–5 kHz via tops from the mic's click peak, rarely, gain is low) | 40–120 Hz (sub pass-band × room modes × shell resonance) | LF feedback here is real and common ("tom rumble", drum-fill howl); a 250 Hz floor would blind the detector to the *only* mode this source has |
| (f) acoustic guitar (sound-hole pickup / internal mic / external SDC), also upright bass, violin-family | body air resonance 90–110 Hz (dreadnought; smaller bodies 110–130), top-plate modes 180–230 Hz; upright bass 60–150 Hz | 4–10 ms (wedge/amp) | often none on DI; 80–100 Hz | **≈80 Hz – 3 kHz** | 95–110 Hz and 180–220 Hz dominate (why sound-hole plugs exist); 1–3 kHz for internal mics | the resonator's Q also narrows and stabilises the line — looks exactly like a ring on the RTA because it is one |
| (g) choir / area / overhead condensers at 1–3 m | cardioid SDC/LDC, +45…+55 dB gain, mic in the PA's reverberant field | τ_eff 30–100 ms → dense candidates, slow growth (1 dB → 10–30 dB/s), big hops | 80–150 Hz | **≈100 Hz – 10 kHz** | 150–500 Hz low-mid build-up (room gain × long LF T60), 800 Hz–4 kHz | textbook "ring the room" case; envelope = room gain curve |
| stationary non-feedback lines to expect | mains hum 50/60 Hz + harmonics to ~1 kHz (ground loop, dimmer buzz), HVAC/fan tones 100–600 Hz, projector/moving-light whine 1–4 kHz, test tones | — | — | — | — | prominent, persistent, zero growth, respond ≤1 dB/dB to gain → the 25 dB/6-frame override (detector.py:361-367, justified at 355-360 by "musical content is harmonically spread, so tens of dB of prominence in a single band is feedback by construction" — false for hum, 808s, organ, whistle, test tones) fires on a −40 dBFS hum bar; the harmonic-family test correctly calls hum "programme" (exact integer family) |

### 3.3 Verdict on a fixed 250 Hz–8 kHz window
It is a correct prior for exactly one class — HPF'd dynamic vocal mics into tops with no mic path to the subs — and
it happens to be the M7 rig. It is wrong at the bottom for (e), (f), un-HPF'd instrument mics on wedges (d) and
marginal for (b), (c), (g) (real rings at 125–250 Hz), and 2–4 kHz too tight at the top for condensers (8–12 kHz
rings are routine with headsets/lavs; even M7's SM58 rang at 8 kHz = band 87, on the edge). As physics it is a
statement about M·C·S for one mic and one HPF setting, so it should be *derived per session* from those, not fixed.

### 3.4 Proposed per-session window (from facts the server knows or can read)
Inputs: for the bus under test B, the contributing open channels C_B (already enumerated by preflight/discover:
unmuted, routed, send > `mics.send_floor_db` −40, device.yaml:834); per channel `hpon/hpslope/hpf`
(device.yaml:235-236), gate on/off, patch `mic` (patches.py:133) plus a new optional patch column
`kind ∈ {vocal_dyn, vocal_cond, lav, headset, lectern, inst_dyn, kick, tom, bass_cab, acoustic_pickup, area_cond,
playback}`; per bus an operator-declared `lf_path` (does this bus reach subs / full-range boxes below 100 Hz?) —
not reliably desk-derivable (matrix/aux sub feeds and outboard crossovers are invisible), so it must be asked,
defaulting to *false* for mix-bus (wedge) targets and *unknown→false* for Main LR unless a `kind ∈ {kick, tom,
bass_cab, acoustic_pickup}` channel is open on it.

Rule: f_low(c) = max(K_low(kind_c), r(slope_c)·hpf_c if hpon_c else K_low(kind_c)), r = 0.6/0.7/0.7 for 12/18/24
dB/oct; K_low = 160 Hz vocal_dyn, 125 vocal_cond/lav/headset/lectern/inst_dyn, 100 area_cond, 80 acoustic_pickup,
40 kick/tom/bass_cab (only if `lf_path`), unknown → mode default. f_low(B) = min over c ∈ C_B; f_high(B) = 10 kHz if
every open mic is `*_dyn`, else 12.5 kHz (band 93). Express the HPF as a *soft* prior as well: bands where the
least-filtered open channel is attenuated 3–10 dB require the strict evidence tier (§4.3), >10 dB are excluded.
Playback-only channels (kind=playback, or `mic: false`) contribute nothing to the window but their presence flags
"programme present" for the probe statistics.

Defaults when nothing is known: **feedback_watch 160 Hz–12.5 kHz (bands 30–93)**; **ring_out 100 Hz–12.5 kHz
(bands 24–93)** — wider in ring_out because programme should be absent, the ±1 dB probe can veto stationary
lines, and a missed LF mode is precisely what a ring-out exists to find. **LF opt-in**: an explicit argument
`lf_feedback_possible=True` (or `window_hz=(40, …)`) on `ring_out`/`feedback_watch`, or a patch `kind` in
{kick, tom, bass_cab, acoustic_pickup} on an open channel of that bus with `lf_path` true, extends f_low to 40 Hz
(band 10) *and* switches bands <160 Hz to the strict tier (no harmonic family + stationarity + probe-confirmed in
ring_out; + level ≥ −20 dBFS in watch). The session report must print the window and the facts it was derived from
("f_low 105 Hz = 0.7 × ch04 HPF 150 Hz/18; f_high 10 kHz: all open mics dynamic").

--------------------------------------------------------------------------------------------------

## 4. What a −3 dB 1/3-octave cut does, and where the operating point should sit

### 4.1 To a ring
Loop gain at the ring frequency drops by the GEQ filter's attenuation *at that frequency*, which equals the slider
value only at the band centre. RBJ peaking cut, attenuation vs offset from centre (octaves):

| filter Q (X32 GEQ shape UNCERTAIN) | −3 dB slider: 0 / 0.05 / 0.10 / 0.167 / 0.333 oct | −6 dB slider | −9 dB slider |
|---|---|---|---|
| 4.3 (true 1/3-oct, constant-Q) | −3.0 / −2.75 / −2.2 / **−1.5** / −0.6 | −6 / −5.5 / −4.4 / **−3.0** / −1.2 | −9 / −8.2 / −6.5 / **−4.5** / −1.9 |
| 2 (typical proportional-Q graphic at small settings) | −3.0 / −2.9 / −2.8 / **−2.5** / −1.6 | −6 / −5.9 / −5.5 / **−4.9** / −3.2 | −9 / −8.8 / −8.3 / **−7.3** / −4.8 |
| 1 (very wide) | −3.0 / −3.0 / −2.9 / −2.8 / −2.5 | … | … |

(0.167 oct = exactly between two GEQ centres; 0.333 = the neighbouring GEQ centre.) Behringer's "TEQ/TrueEQ"
exists to correct band interaction of the plain GEQ, implying the GEQ is a conventional interacting
(moderate-Q) design — so Q≈2–3 at −3 dB is the better guess, UNCERTAIN; **measure it once on the real desk**
(pink noise, PRE insert, −6 dB on one band, read the RTA at 0, ±1, ±2, ±3 RTA bands) and put the result in
device.yaml; SyntheticRta.set_geq_gain's brick wall (meters.py:661-667) should then use that bell.

Consequences:
* A ring with excess e dies iff attenuation at f_ring > e. Right after a +1 dB ring_out step e ≤ ~1 dB, so −3 dB at
  centre (−1.5…−2.5 at the midpoint) suffices *if applied within the same dwell*; every further +1 dB step before the
  cut adds 1 dB to the required depth. In watch mode e is unknown and often 3–6 dB → expect to need −6/−9 and
  sometimes to fail at −9 (abort path cfs.py:1160-1163 is physically necessary, not defensive).
* RTA→GEQ mapping: GEQ centres fall on RTA bands only at i ≡ 0 (mod 10) (…,40=313,50=625,60=1.25k,70=2.5k,80=5k,
  90=10k); 31 of the 100 RTA bands lie ≥0.12 oct from the nearest GEQ centre, and bands i ≡ 5 (mod 10) (45=442,
  55=884, 65=1768, 75=3536, 85=7071 Hz, …) sit at the exact midpoint (0.144–0.167 oct). For those, with Q=4.3 a single
  −3 gives −1.5 dB at the ring; two flanking −3 cuts give −3.1 dB; one −6 gives −3.0 dB at the ring but −6 at a
  centre where nothing is wrong. Rule: interpolate f_ring from the ±1 RTA neighbours (§2.1); if |offset| > 0.1 oct
  from the chosen GEQ centre, prefer cutting both flanking bands −3 (costs 2 budget lines but spreads only −3) over
  deepening one to −6/−9; `merge_adjacent_bands: 1` (device.yaml:819) already treats the pair as one event for
  deepening — it should also account both to one *budget* line when they bracket one interpolated frequency.
* After the cut, decay is at (attenuation − e)/τ dB/s: e.g. 2 dB net on τ = 10 ms → −200 dB/s, gone in <0.3 s; on a
  reverberant τ_eff = 70 ms → −29 dB/s, 6 dB takes 0.2 s. `decay_verify_db: 6` within `decay_verify_s: 1.5`
  (device.yaml:820-821) is physically sound: programme can *never* show 6 dB from a 3 dB cut, a killed ring always
  shows ≫6 dB, and a ring with e > attenuation shows <3 dB and keeps growing → deepen. The one ambiguous outcome is
  "dropped ≈ attenuation and flat" = it was programme (or a stationary line): that should be recorded as a false
  cut, not deepened, and is the trigger for releasing the band if policy allows a return toward 0 dB.

### 4.2 To programme
The same bell is applied to everything on the bus: −3 dB over ~1/3 oct (Q 4.3) to ~2/3 oct (Q 2). Audibility: a
1/3-octave −3 dB dip is around the just-noticeable level on running music for casual listeners (1–2 dB for
critical listeners); −6/−9 dB is plainly audible — at 2–4 kHz it costs vocal intelligibility/brightness, at
80–125 Hz on mains it thins kick and bass. Cuts are bounded (≤9 dB, ≤6 bands) and persistent for the session
(cuts-only), so the damage is cumulative dulling plus the budget line — at M7 two wrong LF cuts consumed half of a
4-line budget before the real 5 kHz modes arrived (HANDOVER §4b). A wrong cut in ring_out additionally corrupts
the report the engineer will trust for the show.

### 4.3 The asymmetry (brief Q4) and recommended operating points
Cost of a missed/late howl: exposure = SPL × time. At limiting a wedge delivers 115–125 dB SPL at the performer, FOH
100–110 dB(A) at the audience, usually at 2–5 kHz where the ear is most sensitive and most damage-prone; NIOSH
daily allowance is 28 s at 115 dB(A), 9 s at 120, 3 s at 125. Lateness does not make the howl harder to kill (loop
gain is level-independent until limiting; the same −3 dB works at any level) — it costs seconds of exposure and
show disruption. Each frame of delay adds R×0.05 dB: 1 dB/frame at 20 dB/s, 5 dB/frame at 100, 10 dB/frame at 200.

**ring_out** (server owns the gain, pre-show, ideally no programme):
* The dominant safety actuator is the master, not the notch: a −6 dB back-off (device.yaml:829) turns any e ≤ 5 dB
  into decay at ≥(6−e)/τ ≥ 100 dB/s on a wedge — silence in <100 ms, one write, zero budget, fully reversible.
  So the detector can afford to be **sensitive and patient**: trigger HOLD (stop raising) on weak evidence, then
  resolve with the probe rather than with a cut.
* Uncertain ⇒ **probe, don't cut, don't just wait**: step master −1 dB, compare 10–20-frame medians; regenerative
  (Δ ≤ −3 dB or slope sign flip) → restore +1, cut −3, VERIFY; else → tag the band "stationary/programme" on a
  session ignore-list (hum, HVAC, playback) and resume. Cost ≈1–2 s per false candidate, no audio damage.
* Also act on *sub-threshold* evidence: a line whose level rises >2 dB per +1 dB step over two consecutive steps is
  within ~3 dB of threshold (§1.5) — notch it pre-emptively (that is what a human ring-out does) or at least
  pre-arm it so the first growth frame after the next step triggers immediately.
* Certain (any of: RTA band = 0.00 dB clip flag; no-family line ≥30 dB prominent and stationary ≥3 frames; growth
  onset time-locked to the server's own step) ⇒ cut −3 immediately, deepen only on failed VERIFY with the line still
  present.
* Precondition worth enforcing at ARM: measure spectral flux for 2 s; if programme is present (M7 ran music
  "as deliberate background noise"), warn that probe statistics need longer dwells and LF bands go strict-tier.

**feedback_watch** (human owns the gain, show running, programme present, server cannot back the fader off):
* False positives are expensive (audible, cumulative, budget) and the base rate of feedback-like programme events
  is high; a human is present and reacts in 1–3 s. Operating point: **conservative on cutting, instant on alerting.**
* Tier A (cut −3 now): clip flag; or no-family stationary line ≥25–30 dB prominent for ≥3 frames inside the window;
  or in-window line that grew ≥15 dB over ≤10 frames with no co-onset partials and is still rising; or level ≥
  −10 dBFS narrowband. Deepen only when VERIFY fails AND the line is still growing/at limit.
* Tier B (uncertain): publish `cfs.candidate` immediately (latency matters more than certainty for the human), keep
  watching ≤6 frames (300 ms) for tier-A evidence; if the line is loud (≥ −20 dBFS or ≥20 dB above that band's
  30 s baseline) and still ambiguous at 6 frames → **one −3 dB cut, never deepened on tier-B evidence** (bounded
  harm: one band, −3, which also *buys certainty*: the VERIFY outcome classifies it post hoc). Quiet + ambiguous →
  alert only.
* Tier C (harmonic family present, pitch moving >1 band, outside window, HPF-attenuated >10 dB) → ignore, log.
So the answer to "uncertain ⇒ ?" is mode- and level-dependent: ring_out → probe; watch+loud → cut −3 once, no
deepening without tier-A evidence; watch+quiet → alert and wait ≤6 frames.

--------------------------------------------------------------------------------------------------

## 5. Discriminating predicates that follow from the physics

Latency in frames at 20 fps. "Fails on" lists programme that passes as feedback (FP) or feedback that fails (FN).

| # | predicate (feedback ⇒ true) | type | latency | fails on / caveats |
|---|---|---|---|---|
| P1 | **No harmonic family**: none of bands i+10, i+16, i+20, i+23 (±1) carries a peak whose prominence is within 15 dB of the candidate's and whose envelope co-moves (onset within the analyser rise time, correlated frame-to-frame) | single-frame (co-movement: 2–3 frames) | 1 (3) | FP: near-sinusoidal sources — 808/sub-bass sine (40–60 Hz, 0.3–1 s, usually with a downward pitch glide → P3 catches), organ flue/stopped pipes (odd partials weak but present), flute >800 Hz (2f at −20…−30 dB), whistling (1–3 kHz, vibrato/glide), sine synth pad, alignment test tone (1 kHz: indistinguishable by P1; P6/P7 separate it), hum (has an exact family → correctly rejected). FN: hard-clipped howl grows odd partials at −10…−17 dB — but only once the fundamental is at limiting and ≥40 dB prominent, where P8 decides; vacuous above 5 kHz (2f off the useful range) — harmless |
| P2 | **Not itself a harmonic**: no co-moving peak at i−10 (f/2) or i−16 (f/3) of comparable or greater level | single-frame | 1 | FP: none of note. FN: coincidence with an unrelated lower line (<1 %); a real ring at 2× a hum component (100/120 Hz region) — use co-movement, hum does not move |
| P3 | **Absolute frequency stationarity**: peak stays within ±1 band (interpolated centroid within ±0.03 oct) for its whole life; no glide | temporal | 3–5 | FP: any held note without vibrato (organ, pad, sustained bass, piano with pedal) for its duration (0.5–5 s). FN: hand-held mic hops (usually ≤1 band ≥1.5 kHz; up to 1/3 oct in reverberant loops) — treat a hop as continuation within ±2 bands if the old line vanishes the same frame the new one appears |
| P4 | **No vibrato**: absence of 4–8 Hz anti-correlated level modulation between the peak band and one neighbour | temporal | 8–10 (need ≥2 vibrato cycles) | FP: straight-tone singers, most instruments other than voice/strings/lead guitar. FN: none (a ring has no FM). Useful specifically for the "sustained vocal note" stream |
| P5 | **Level steadiness at plateau**: frame-to-frame std < 0.5 dB over ≥6 frames while prominent (a sine through a PEAK detector is deterministic; noise-like programme in a band of width Δf fluctuates ≈4.3/√(Δf·0.05 s) dB: ≈1.6 dB at 2 kHz, ≈4 dB at 200 Hz; music far more) | temporal | 6–10 | FP: sustained electronic tones, organ, test tone, hum, HVAC; **everything if RTA peak-hold is on or decay is long** (must be forced off, §0). FN: ring during growth (use P6 instead), ring beating against a second line |
| P6 | **Envelope shape = linear-in-dB ramp then flat, or flat; never attack–decay**: monotone within ±1 dB, no decay segment while the loop is intact; slope, if measurable, constant (exponential growth) rather than decelerating (analyser step response, §1.6) | temporal | 4–8 | FP: bowed/blown/sung crescendi (dB-linear swells of 10–40 dB/s exist), sidechain-pumped synths, LF note onsets seen through the slow analyser bands (decelerating — test the curvature: a ring's second difference ≈ 0, an analyser-limited onset's is negative). FN: fast rings (e ≥ 1 dB, τ ≤ 10 ms) reach plateau in 2–5 frames — too few points; plateaued rings have no ramp at all (M7) |
| P7 | **Super-linear response to a known gain change** (ring_out active probe; watch: passive on observed `/xremote` fader/send moves): Δband − Δgain ≥ +2 dB per dB, or slope sign flips time-locked (±2 frames + τ) to the step; programme at the pre-fader tap moves 0 dB (electrical) to +1 dB/dB (acoustic spill) | temporal, needs the step | 5+5 frames quiet room; 20+20 with music | FP: a musical crescendo coincident with the step (repeat the probe, or use −1 dB then +1 dB: programme cannot anti-correlate twice by chance, p ≈ few %). FN: candidate >4 dB below threshold responds only +1…+2 dB/dB (fine — it is not a threat yet); needs excitation energy at f (room noise suffices at ≤2 dB deficit, regenerative boost ≥14 dB) |
| P8 | **Level implausibility**: narrowband line within 10 dB of full scale, or RTA = 0.00 dB (clip flag), or ≥25–30 dB above the band's own 30 s rolling baseline with P1 true | single-frame (+ baseline) | 1 | FP: solo sine-like instrument in an otherwise silent mix at very high level (rare in a live band context; likely in electronic music: 808s → gate by window/HPF prior and P1's glide check); test tone. FN: compressor-limited howl at −25 dBFS (M7-like) — P1+P3+P5 must carry that case |
| P9 | **Onset from the floor, not from silence-to-full in one analyser rise time**: first appearance ≤ prominence threshold and then continuous rise (a note appears at full level within 1 frame above 300 Hz, within the §1.6 rise time below) | temporal | 2–3 | FP: fade-ins, swells, reverse-reverb effects. FN: **ring already established at arm time** (no onset observed — must not be required), fast rings at HF (−60→−20 in 2 frames looks like an onset: do not veto on speed, §1.3) |
| P10 | **Inside the session's feasible window and not HPF-starved** (§3.4): f ≥ f_low(B), ≤ f_high(B), least-filtered open channel attenuates <10 dB there | static prior | 0 | FN: undeclared LF-capable source (kick/tom/acoustic) on a bus without opt-in — mitigated by ring_out's wider default and the report stating the window. FP: none (it only removes candidates) |
| P11 | **Band-occupancy = analyser skirt**: peak + neighbours match the single-tone leakage template (centre/−k/−k or edge −3/−3 with steep outer skirts), time-invariant; programme partials are broader (vibrato, chorus, detuned unison, formant clusters, cymbals = many adjacent bands) | single-frame | 1–2 | needs the X32 skirt template measured once (UNCERTAIN now). FP: clean sine sources again. FN: two rings in adjacent bands; ring + programme in the neighbour band |
| P12 | **Outlasts programme structure**: line persists across ≥2 note/beat boundaries of the surrounding programme (spectral-flux minima) with unchanged level | temporal | 10–40 (tempo-dependent) | too slow to be primary; good as a deepening/second-cut criterion in watch mode. FP: drone/pedal notes, hum |
| P13 | **Post-cut response** (VERIFY, exists): drop ≫ attenuation (≥6 dB for −3) within (6 dB)/((3−e)/τ) s — typically <0.3 s; programme drops by exactly the bell (≤3 dB, PRE insert required to see even that, HANDOVER §4b(4)) | temporal, needs the cut | 2–30 | FN-like: e > 3 dB → drop <3 dB and still rising → deepen (correct behaviour). "Drop ≈ 3 dB and flat" ⇒ the cut was wrong ⇒ record, do not deepen |
| P14 | **Ring-down tails below threshold** (pre-howl): after each transient the candidate band decays linearly in dB markedly slower than its neighbours (loop T60 = 60τ/d: 0.6 s at d = 1 dB, τ = 10 ms vs programme release), same frequency every time | temporal, multi-event | 20–60 | ring_out early warning / pre-emptive notch; FP: a reverberant room mode excited by kick (it IS a high-Q resonance — but responds 1 dB/dB to the probe, P7) |

Combination logic that the physics supports (not a weighted sum): candidate := in-window (P10) ∧ prominent;
FEEDBACK if [P8-clip] ∨ [P1 ∧ P2 ∧ (P3 over ≥3 frames) ∧ (P5 ∨ P6-ramp ∨ P7)] with P7 mandatory for bands below
160 Hz unless LF opt-in; PROGRAMME if family (¬P1 or ¬P2) ∨ glide (¬P3) ∨ vibrato (¬P4) ∨ probe-linear (¬P7);
else UNCERTAIN → §4.3 policy. Expected latency for a clean HF ring: 3 frames (150 ms) from prominence; for an
already-plateaued ring at arm: 3–6 frames; for an LF ring with opt-in in ring_out: one probe cycle (1–2 s) —
acceptable because LF growth is slow (τ_eff 15–30 ms and small e → 5–30 dB/s).

Coincidence risks quantified: two simultaneous independent rings an exact octave apart (defeats P1/P2) <1 % of
events; a musical note with *no* partial within 15 dB in any of 2f…5f: essentially only synthetic/sine sources and
flute/whistle in their top register; a programme band rising ≥3 dB/dB time-locked to two opposite-sign probe
steps by chance: ≲ few % with 20-frame medians (music variance UNCERTAIN — measure on the M7 recording if frames
were logged).

--------------------------------------------------------------------------------------------------

## 6. UNCERTAIN / to measure on the real desk (each is one GIG_CHECKLIST line)
1. X32 RTA implementation (filter bank vs multirate FFT), skirt shape (neighbour leakage for a centred and an edge
   tone), per-band rise time to a gated sine at 40 / 80 / 160 / 1k Hz — fixes §1.6, P6, P11 constants.
2. Semantics/units of `/-prefs/rta/decay` and whether `/-prefs/rta/gain` offsets /meters/15 (meters.md:311, 400).
3. GEQ bell Q at −3/−6/−9 (pink noise, PRE insert) — fixes §4.1 and SyntheticRta.set_geq_gain.
4. Exact RTA tap point relative to bus dynamics/fader (inferred pre-fader from HANDOVER §4b(4)); if a bus
   compressor sits between tap and fader the probe arithmetic in §1.5 changes by its ratio.
5. FX-insert and AES50 added latency (small; affects τ by <1 ms).
6. Frame-to-frame level statistics of real programme per band (needed to set P5/P7 thresholds) — log raw frames in
   the next session; none of tonight's numbers should be fitted to one room again (REVIEW_BRIEF "be skeptical").
