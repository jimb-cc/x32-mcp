# CFS² offline validation corpus (`tests/rtasim`)

Test support only (pure Python, stdlib + `x32mcp`), deterministic per seed. Everything lives under `tests/rtasim/`.
Citations: **[L §x]** = loop-physics brief, **[A §x]** = analyser brief (docs/review/A1, A2);
`meters.md` = `docs/research/meters.md`. Frame = one `/meters/15` frame = 50 ms; levels are RTA dB (−128 floor, 0.0 = clip flag);
band *i* centre = 10000·2^((i−90)/10) Hz.

---

## 1. How to use the harness

```bash
# simulator + harness sanity (~8 s)
python -m pytest tests/test_rtasim.py tests/test_detector_corpus.py -k "not baseline and not closed_loop"

# the shipped-detector baselines of §6 (open / closed loop, seeds 1-3; JSON + baseline_tables.md)
PYTHONPATH=src:tests python -m rtasim.run_baseline [out_dir]

# ad hoc: any subset, current detector, table on stdout; --adversarial runs the auditors' breaker set
PYTHONPATH=src:tests python -m rtasim.harness [--closed] [--adversarial] [--json=out.json] X7_plateaued_ring_under_music_from_t0 S1_bass_under_quiet_music

# the full measurement battery used for docs/DETECTOR.md §6 (main, hold-out, sweeps, adversarial, cost)
PYTHONPATH=src:tests python -m rtasim.run_detector_eval --help

# pytest wrapper that writes baseline_current_detector*.json (RTASIM_REPORT_DIR overrides the output dir)
python -m pytest tests/test_detector_corpus.py -s
```

**Detector-factory protocol** (`tests/rtasim/harness.py:1-30, 243`): `evaluate(factory, scenarios=None|[names], seeds=(1,2,3),
closed_loop=False, analyser_overrides={...}, notch_cfg=None, actuation_delay_frames=1) -> Results`.
`factory(band_hz: Sequence[float]) -> det` is called once per (scenario, seed); the harness then calls
`det.feed(values: list[float] /*100 dB values*/, ts: float /*s, ±3 ms jitter*/)` once per frame and expects an iterable of
objects with `.ts, .band, .freq_hz, .confidence` (optional `.level_db, .prominence_db, .slope_db_per_s` are recorded).
`x32mcp.detector.FeedbackDetector(cfg, band_hz)` qualifies as is. `Results.table()` prints the summary; `Results.dump(path)` writes
per-run detections with verdicts, episodes, cuts, latencies; `Results.summary_rows()` is the machine form.
Frames without a detector: `rtasim.frames(name, seed, **analyser_overrides) -> [(ts, values[100])]` (cached; frame 0 follows
1.5 s of unrecorded pre-roll) and `rtasim.ground_truth(name, seed)`. A candidate that wants the ring_out step times reads
`SCENARIOS[name].build(seed).master.step_times()` (the server knows its own writes; a detector may be given them).

**Scoring** (`harness.py:10-30, 167-240`; `render.py:275-330`). Per ring an *episode*: `t_onset` = first frame with effective
excess e_eff > 0 (0.0 if established before arm); `t_prom` = first frame in the episode at which a band within ±1 of the ring is
ring-dominated (≥ −3 dB of the displayed power) and its prominence ≥ 12 dB by the **earlier** of (a) the detector's own definition
(level − median of ±3) and (b) cluster prominence (powersum b−1..b+1 over median of b±2..±4, counted only if the ring dominates
the cluster too); `t_end` = loop sub-threshold and (line −20 dB from peak, or was visible and prominence < 6). A detection is
attributed to a ring if within ±1 band or 1/6 oct of it AND the ring dominated a band within the previous 1 s. Verdicts: TP (first
in [t_onset, t_end+1 s]; latency = ts − t_prom, may be slightly negative when the detector fired on programme energy sharing the
band with an emerging ring), DUP, EARLY (same loop ringing sub-threshold before onset; satisfies the event if ≤ 2 s early),
TAIL (after the episode: stale display), HARM (±1 band of a harmonic of a howl that is producing harmonics — desk clip or
acoustic clip), FP (everything else; reported with the 1/3-oct GEQ band it would have cut). A run passes iff FP = 0, miss = 0 and
every latency ≤ the scenario budget — and, in closed loop, no ring **survived**: still regenerating (e_eff > 0) at the last
frame although the detector saw it or a cut landed within one GEQ band of it (`RunResult.survived`, table column `surv`;
misses are not survivors, open loop has none; every applied cut is also handed to the detector's `note_cut(freq_hz, depth_db, None)`
when it has one, as `cfs.py` does after each GEQ write, so verdict-gated deepening counts). Closed loop: each detection → `x32mcp.detector.NotchController.plan` (device.yaml: −3 dB
steps to −9, budget 6) → RBJ bell (Q 3) written into the live renderer **one frame later** (OSC write lands during the next
frame); programme, beds and the ring's excess all see the PRE-insert cut.

---

## 2. Audit of the builder's simulator: what was changed and why (commit 83d7cc0, touch-ups in 3686fd0)

Things that made the detector's life unrealistically **easy** (E) or **hard**/hack-inducing (H), with the fix:

1. **E — ring wander was one pure sinusoid** (`wander_db·sin(2π·wander_hz·t)`, S2a: 0.3 dB @ 0.6 Hz; frame-difference sd 0.04 dB):
   a template a detector could lock onto, and smoother than any real plateau. Now a seeded sum of incommensurate slow
   sinusoids (0.2–2 Hz) plus a 2–7 Hz flutter component at ⅓ depth (`sources.py FeedbackRing.randomize`); realisation differs per
   seed and per ring. [A §4.3(v): "air movement ±0.2–0.5 dB < 2 Hz — not zero"]
2. **E — growth was exactly dB-linear** (constant excess). Real loop gain wanders by tenths of a dB (air, head/mic motion) so the
   rate varies and a marginal loop flickers about threshold. New `excess_wander_db` (default 0.08 dB, 0.05–0.6 Hz, seeded).
   S13's "0.03 dB for 10 s" was itself unphysical; it is now 0.035 ± 0.02 dB (1.5–5.5 dB/s, irregular, never stalls). [L §1.3, §2.3]
3. **E — regeneration was not frequency-selective**: any programme line within ±0.1 oct (±120 cents) of a loop mode, and the whole
   bed power of the band, received the full on-mode boost 1/(1−g)². The closed-loop response is a comb,
   |1/(1−g e^{−j2πδτ})|² = 1/((1−g)² + 4g sin²(πδτ)) [L §1.2/§1.5]: half-width ≈ (1−g)/(2πτ√g) Hz (12 Hz at −4 dB, 3.7 Hz at −1 dB
   for τ = 12 ms); broadband excitation gets the *band mean* of the comb (→ 1/(1−g²) once the band spans a comb period: +3 dB at
   −3, +6.9 at −1, +9.6 at −0.5 — not +10.7/+19/+25). Implemented exactly (`physics.comb_gain_lin`, closed-form
   `comb_band_mean_lin`; `FeedbackRing.regen_extra_db`). Consequences the designers must know: the ring_out "+1 dB probe"
   over-response on an HF band excited by noise is ≥ 2 dB/dB only within ~2–3 dB of threshold and is noisy (S6b band 70 medians per
   step: +1.1, +1.6, +0.5, +3.4, −0.4, +4.1, +4.2 dB for e = −6.5 … −0.5 while programme moves +1.0); it is strong for a tonal
   excitation on the mode and at LF where a band holds < 1 comb period. The old model also planted a permanent "latent ring line"
   6 dB under the bed in every latent-loop scenario (target = excitation − 6 dB even at g → 0); the extra-power formulation removes it.
4. **E/H — over-threshold start level**: the ring used to start from `start_db` (−65…−80, far under the bed) and climb through the
   bed — the textbook "onset from the floor". Physically the oscillation starts from whatever coupled excitation is inside the
   capture range (a sung partial sweeping through the mode seeds it at that partial's level one trip later). New `seed_db`
   (bed power within ≈0.114/τ Hz + comb-weighted nearby lines, no regenerative gain): rings under programme now emerge in a few
   frames from programme level (X9: speech H4/H5 seed the 525 Hz mode), rings in quiet still climb from the floor.
5. **E — sub-threshold build-up too fast**: relax rate was max(|e|, 0.5)/τ; near threshold build-up and ring-down both go at |e| dB
   per trip (critical slowing, T60 = 60τ/|e| [L §1.5]). Floor lowered to 0.1 dB.
6. **E — howl harmonics existed only at the 0.0 clip flag**, so "partials present and level < 0 ⇒ music" was a free rule. A howl
   through a clipping powered speaker/driver carries H2/H3 the mic hears while the desk reads −30…−5 [L §1.4(1), §2.2]. New
   `harmonics=((k, rel_dB),…)`, `harmonics_knee_db` (2 dB/dB fade-in); X11 uses it; HARM scoring extended to it.
7. **E — plateau ignored gain between mic and tap.** A limiter/SPL-set plateau moves with a channel/DCA move and not with the bus
   master (pre-fader tap) [L §0, §1.4]. New `Scene.sat_coupling` (0 = bus master, 1 = channel) → `FeedbackRing.sat_now`; a
   desk-clip plateau never moves. X12a/X12b are the two cases.
8. **E — notes were dead in tune and dead flat** (only deterministic vibrato/AM), partial tables identical for every note of a
   timbre (a fingerprint), vibrato at exactly 5.500 Hz. `HarmonicNote` gained `drift_cents`, `flutter_db`, `timbre_jitter_db`
   (seeded, smooth, non-periodic `Wobble`); the sequencers give every note its own realisation, vary vibrato rate ±12 % and
   depth ±20 %, add a ±1 dB expressive swell/sag per sung note, allow repeated pitches; per-timbre defaults in `sources.HUMANIZE`
   (organ/piano: no drift, 0.15 dB flutter; voice 8 c/1.2 dB; whistle 25 c/1.5 dB). Chords can be strummed (`strum_s`).
9. **E — the bed was the same smooth pink line in every scenario with a 0.37 Hz sinusoidal LFO.** `music_bed(seed=…)` now adds 4
   random ±4 dB humps (σ 2–6 bands), ±0.5 dB/oct tilt, non-periodic dynamics and independent ±1.5 dB slow modulation of
   ~octave-wide regions (spectral flux: the neighbour median is neither flat nor static nor common to all scenarios).
10. **E — organ "near-sine" had H2 at −24 dB** (a usable family at 25 dB prominence); the brief's Hammond-8′ figure is −52.
    `organ_flue` is now (0, −36, −50); added `flute_high` (0, −26, −40, −50), `whistle` (0, −42), `sine_lead` (0, −48), `hum`.
    Speech/voice partial tables extended to 32/16 partials so vocal energy actually reaches 2–4 kHz loop modes.
11. **H→fair — ground-truth visibility used only the single-band median-of-±3 prominence**, which is 3 dB pessimistic for a line
    split across two bands and has a skirt-set ceiling; `t_prom` is now the earlier of that and the cluster measure (with a cluster
    dominance guard so a programme partial next door cannot make a ring "visible"). Threshold flicker no longer splits an
    invisible episode into several.
12. **E — zero actuation latency in closed loop** (cut applied before the next frame). Now one frame later (`ACTUATION_DELAY_FRAMES`).
13. **E — exact 50.000 ms timestamps.** Default `frame_jitter_s = 0.003` (UDP + asyncio); dropped frames remain opt-in.
14. Kept as found but flagged (§7): per-band power one-pole attack τ_a = 0.5/Δf (between the single-biquad 0.32/Δf and the
    critically-resolved 1/Δf), Butterworth-2N skirts, χ²/Gaussian estimation noise independent across bands, PEAK display with
    dB-linear release = release_law_db/decay_s. These are models, not measurements; all sweepable.

---

*(The corpus runs on the grid it was built and baselined on, `10000·2^((i−90)/10)` (`rtasim.physics.RTA_BAND_HZ`), not yet on
the desk's measured `20·2^(i/10)` (`x32mcp.meters`): its scenes mix bin-anchored frequencies (`band_centre_hz`) with
world-anchored ones (notes, rings and GEQ centres in Hz), so the bins cannot move alone without changing what scenarios test.
The grid moves with the measured analyser model and re-anchored scenarios in the corpus revision; see
`docs/REVIEW_RESPONSE_2026-09-24.md` item 2 for what the corpus reads when only the bins are moved.)*

## 3. Scenario table (67 scenarios; ground truth from the rendered trace, seeds 1–3; "0 ev" = nothing may be detected)

Columns: name | dur s | content | ground truth (on = t_onset, vis = t_prom range over seeds, pk = peak prominence) | budget ms |
physical predicates stressed (P-numbers = loop brief §5: P1 no family, P2 not itself a harmonic, P3 stationarity, P4 vibrato,
P5 plateau steadiness, P6 envelope shape/curvature, P7 probe/common-mode response, P8 level, P9 onset-from-floor, P10 frequency
window, P11 occupancy/skirt template, P12 outlasts programme, P13 post-cut, P14 ring-down tails; "τ_a" = analyser-manufactured
LF growth [A §0.1]).

**Controls and the builder's set (S/M; parameters unchanged except where §2 says so):**

| scenario | s | content | ground truth | bud | stresses |
|---|---|---|---|---|---|
| C0_silent_room | 10 | room rumble −56@25 Hz … −80@1 kHz | 0 ev | 300 | noise statistics, −45 gate |
| C1_music_bed_drums | 12 | bed −46 + snare/hats/crash | 0 ev | 300 | synchronous broadband onsets |
| S1_bass_under_quiet_music | 16 | M7 replica: bass E1 A1 D2 G1 (H2>H1), instant onsets, bed −58 | 0 ev | 300 | τ_a growth at 40–160 Hz, P1 (family present), P10 |
| S2a_established_ring_8k | 8 | 8122 Hz at −8, established, N=3 skirts | on 0, vis 0, pk 46 | 300 | P9 must not be required; P5, P8, P11 |
| S2b_…_steep | 8 | same, N=5 skirts (M7 60 dB datum) | on 0, vis 0, pk 57 | 300 | prominence ceiling |
| S2c_established_clipped_2k4 | 8 | 2405 Hz pinned at 0.0 + odd partials | on 0, vis 0, pk 47 | 300 | P8 clip flag vs P1 (odd family) |
| S3_ring_during_music | 12 | organ melody C5–C6 −30, bed −45, crash /2 s; 3123 Hz e 0.3 τ 10 ms from t=4 | on 4.00, vis 4.75–4.95, pk 42 | 300 | P6 ramp under flux, P3 vs moving notes |
| S4a_vocal_vibrato / S4b_…_band_edge | 7 | A4 (+5 c / on 45/46 edge) 4 s, vibrato →±80 c, scoop | 0 ev | 300 | P4, P1 (family), edge hopping |
| S5_guitar_note_decays_to_sine | 9 | E3 6 s, partials decay to near-sine | 0 ev | 300 | P1 lineage memory, P6 decay |
| S6_master_ramp_feedback_watch | 11 | organ chord (8 lines) + +9 dB/0.9 s ramp, −5 step; prog_coupling 1 | 0 ev | 300 | P7 common mode, τ_a lag |
| S6b_ringout_steps_latent_loop | 16 | ring_out +1 dB/1.5 s ×8; latent 2500 Hz loop e −7.5→+0.5 (noise-excited, coupling 0 dB) | on 12.50, vis 12.55–12.65, pk 44; EARLY welcome | 300 | P7 active probe (band-mean comb: weak until −3 dB) |
| S7_808_sub_bassline | 12 | 808 F1–C2 +300 c glide, kick; sustained sine C2/G1 | 0 ev | 300 | P1 absent, glide (P3), τ_a, P10 |
| S8a_organ_melody | 12 | flue organ C5–C6 legato, Leslie AM, H2 −36 | 0 ev | 300 | P1 absent, P12 (notes end/move) |
| S8b_flute_held_note | 12 | flute melody + 3 s A5, vib ±20 c | 0 ev | 300 | P4 small vibrato, P1 weak |
| S8c_whistle | 10 | whistles 1.2–2.2 kHz −22, ±60 c | 0 ev | 300 | P1 absent, P4, P8 (loud) |
| S9_clipped_howl_fast | 6 | 2007 Hz e 0.8 τ 7 ms → 115 dB/s to 0.0 + odd partials | on 2.00, vis 2.25, pk 47 | 300 | onset guard (>60 dB/s), P8 |
| S10_ring_between_bands | 8 | 2588 Hz on the 70/71 edge, 20 dB/s to −15 | on 1.00, vis 1.55–1.65, pk 44 | 300 | P11 split line, single stream |
| S11a_two_rings / S11b_…_near_octave | 8 | 1250 + 3550 Hz / 1250 + 2520 Hz (×2.02), different onsets/rates | 2 ev each: on 1.0/1.6, vis 1.9–2.35 | 300 | tracker capacity; P1/P2 coincidence |
| S12_acoustic_guitar_wedge_ring_196Hz | 14 | ac-guitar chords G C D + body hump; 199.7 Hz loop e −4 (rings on G3) → +0.3 at 5 | on 4.95, vis 5.25, pk 41 (EARLY on G chords) | 600 | P10 LF opt-in, P14 tails, P12 |
| S13_slow_ring_3dB_s | 16 | 5035 Hz e 0.035±0.02 → 1.5–5.5 dB/s irregular, sat −38, hats | on 1.0, vis 8.5–9.2, pk 17–18 | 1000 | growth < window; P3+P5 at low prominence |
| S14_ring_masked_by_cymbal | 8 | crash +22 dB at 2.0; 4200 Hz 40 dB/s from 2.1 | on 2.10, vis 2.70–2.80, pk 44 | 300 | masking, relative growth |
| S15_long_rta_decay_tails | 16 | decay_s 16: bass/808 tails; 3123 Hz ring t=3, −9 dB GEQ at t=8 | on 3.0, vis 3.7–3.8, end 8.0 | 300 | prefs; P13 reads the display release; TAIL |
| S16_peak_hold_on | 10 | peak_hold 2 s on vocal + organ | 0 ev | 300 | prefs; P5 useless; ANALYSER_MISCONFIGURED |
| S17_kick_pattern | 10 | kick 124 bpm 62→50 Hz | 0 ev | 300 | periodic re-trigger, τ_a |
| S18_vibrato_on_band_edge | 6 | 797 Hz on 53/54 edge ±70 c | 0 ev | 300 | P4 edge hopping |
| S19_driven_room_mode | 14 | 42.7 Hz mode driven by bass + ring_out steps | 0 ev | 300 | DRIVEN_RESONANCE: fixed f, 1 dB/dB (P7) |
| S20_song_start_stop_crowd | 16 | mix start/stop, applause + 2 whistles | 0 ev | 300 | common mode, P1-less whistles |
| S21_synth_pad_swell | 13 | saw pads 1.5 s attack (20 dB/s on 15 lines), chorus | 0 ev | 300 | synchronous multi-line growth (P6), P1 |
| S22_speech_ringing_then_feedback | 10 | speech −32 through 2800 Hz loop at −2.5 (pings 6–9 dB prominent), +3 dB at t=6 | on 6.00, vis 6.40–6.55, pk 46 | 300 | P14 early warning, τ_a FP on speech F0/H2 |
| S23a_autogain_drift / S23b_gain_offset_clip | 12/10 | −0.5 dB/s drift + jump / +24 dB offset (808 hits 0.0) | 0 ev | 300 | prefs; absolute gates; clip on programme |
| S24_bells_triangle_glock | 10 | inharmonic idiophones 2–7 kHz | 0 ev | 300 | P1 absent, monotone decay (P6) |
| M1_loud_band_wedge_ring | 12 | loud band + 2831 Hz e 1.0 τ 5 ms (200 dB/s) sat −4 | on 5.00, vis 5.15, pk 34 | 300 | fast onset under dense programme |
| M2_quiet_music_ringout_two_modes | 16 | quiet music + ring_out steps; modes 5035 (e −2.6) / 8076 (e −5.3), prog_coupling 0.5 | on 5.0 & 9.5, vis 5.15–5.2 / 9.55–9.6 | 300 | P7 probe, sequential modes, closed loop |
| M3_jazz_trio_lav_ring_400Hz | 14 | piano + walking bass + ride; 416 Hz e 0.5 τ 22 ms → 23 dB/s, sat −18 | on 4.00, vis 4.8–5.05, pk 30–33 | 300 | ring sharing bands with chord tones |

**Examiner's additions (X, this work; `scenarios.py` "X:" section):**

| scenario | s | content | ground truth | bud | stresses |
|---|---|---|---|---|---|
| X1_organ_held_notes | 12 | electronic flue organ (no Leslie, H2 −36): E5 (between bands 50/51) held **7 s**, then B4 3.5 s; −27 over bed −52; 0.1 dB flutter, zero drift | 0 ev | 300 | P3+P4+P5 all *pass as ring*; only P9 (seen to start at full level), P12 (ends), P7 separate it |
| X2_flute_held_vibrato | 11 | flute upper register (H2 −26): E6 held 6 s, vibrato 5.2 Hz → ±18 c after 0.6 s, 10 c drift, 1 dB flutter, breath noise; A5 2.5 s | 0 ev | 300 | P4 at small depth, P1 marginal |
| X3_whistle_held_drift | 10 | whistle 1423 Hz held 4 s (−21, ~30 dB prominent), 30 c drift, irregular ±35 c vibrato, 1.5 dB flutter; 2.65 kHz 3 s with +60 c entry glide | 0 ev | 300 | P1 absent, P8 loud, in the vocal-mic band; P3 sub-band drift, P4 |
| X4_sine_lead_portamento | 14 | synth sine lead (H2 −48) G5…D6 with 80 ms portamento and LFO vibrato delayed 0.5 s; C6 **held 6 s, vibrato only after 1.5 s** (1.5 s dead flat at −26) | 0 ev (tag irreducible_passive) | 300 | the irreducible window: only P9/P8/P12/context separate it from X7 |
| X5_808_bassline_40_60Hz | 15 | clean 808 sine E1 G1 A1 B♭1 (41–58 Hz) 1.4 s notes, −150 c/100 ms, 8 dB/s, no kick; then sine A1 (55 Hz) sustained 4 s at −28 | 0 ev | 300 | τ_a growth, P1 absent, P10; sustained sub = LF twin of X4 |
| X6_soprano_closed_vowel_band_edge | 10 | closed-vowel soprano (H2 −20) ON the 51/52 edge (717 Hz) 5 s: scoop, 6.2 Hz →±90 c, 12 c drift, 2 dB shimmer; then D5 | 0 ev | 300 | P4 with peak-band hopping most frames; single-band level history meaningless |
| X7_plateaued_ring_under_music_from_t0 | 12 | compressor-limited ring 1683 Hz (b64 +35 c) at **−30 dBFS from frame 0**, ±0.4 dB wander, under organ chords + piano melody + bass + bed −50 (~15–25 dB prominent, partials share its neighbourhood) | on 0, vis 0, pk 24–25 | 1000 | P9 absent, no growth, no clip, moderate level: P1+P3+P5(+P12) must carry it |
| X8_slow_ring_midband_under_chords | 18 | 1287 Hz (b60 +42 c) e 0.035±0.02, τ 11 ms → 1.4–5 dB/s irregular from −62 at t=1 to −24; organ chords /2.4 s + piano + bed −52 | on 1.0, vis 5.3–7.95, pk 28–29 | 1000 | growth below any window; P3 fixed centroid across chord changes, P12 |
| X9_ring_rta_midpoint_525Hz_speech | 12 | lav loop **exactly** between RTA 47/48 (525.4 Hz; also between GEQ 500/630), e 0.3 τ 22 ms → 13.6 dB/s from t=3, sat −16; speech −30 whose H4/H5 sweep the mode (seeds it) | on 3.0, vis 3.3–3.75, pk 38–41 | 300 | P11 two-band line, one stream; τ_a FPs on speech at 110–280 Hz |
| X10_two_rings_exact_octave | 9 | 1587 Hz (50 dB/s from 2.0) and **3174 Hz = 2×** (62 dB/s from 2.3), τ 8 ms, sat −9/−11; hats | 2 ev: on 2.0/2.3, vis 2.35–2.4 / 2.65–2.7 | 300 | P1/P2 coincidence (<1 %): must not dismiss as note+H2 (different onsets/rates, no H3) |
| X11_amp_clipped_howl_minus12dBFS | 10 | 905 Hz e 1.2 τ 8 ms → 150 dB/s from −60 at t=4 to **−12 dBFS with acoustic H2 −22/H3 −14/H4 −30/H5 −20**; medium-loud band | on 4.0, vis 4.15–4.2, pk 24–28 | 300 | P1 impostor below the clip flag; onset guard; modest prominence in a loud mix |
| X12a_master_drop20_raise_channel | 11 | organ chord + bass + established 2048 Hz ring (−12); **−20 dB at t=3, +20 dB at t=7** as a channel/DCA move (prog 1:1, plateau follows) | ev1 on 0 vis 0 end 3.0; ev2 on 7.0 vis 7.3–7.35 | 300 | P7 both signs; every line "grows" at the raise (τ_a); ring dies in a frame / regrows at 100 dB/s |
| X12b_master_drop20_raise_busmaster | 11 | same as X12a as a bus-master move at a pre-fader tap: programme static, only the ring reacts | same | 300 | P7 the ring_out way |
| X13_decay16_jazz_lav_ring | 14 | M3 rendered with decay_s 16 (3.75 dB/s release) | on 4.0, vis 5.0–5.2, pk 26–28 | 600 | prefs: real ring among frozen tails; prominence compressed |
| X14_peakhold_loud_band_wedge_ring | 12 | M1 rendered with peak_hold 1 s | on 5.0, vis 5.15–5.2, pk 30–32 | 300 | prefs: rise survives max-hold, P5 meaningless |
| X15_kick_bass_unison_55Hz | 16 | kick tuned 55 Hz four-on-floor 120 bpm −28 + bass A1 A1 E1 G1 −34 + hats; band 15 re-struck every 250–500 ms | 0 ev | 300 | τ_a, periodic re-trigger, "recurs at fixed f" trap, P1 intermittent |
| X16_wedge_ring_315Hz_loud_band | 14 | LOUD band (bed −26, drums −16, bass −22, gtr −24, vox −18); 318 Hz e 1.2 τ 5 ms → 240 dB/s from −50 at t=6, sat −3 (only ~22 dB prominent) | on 6.0, vis 6.1, pk 22–24 | 300 | low-mid ring amid bass H4–H6/guitar fundamentals; guard; P8 |
| X17_ring_122Hz_acoustic_guitar_body | 14 | fingerstyle ac-guitar (G Em C D, strummed) + body hump; loop 121.8 Hz (24 c under B2) e −3 (rings on every B2) → +0.4 by 6.5 s, τ 12 ms, sat −12 | on 6.4, vis 6.4–6.7, pk 38–40 (EARLY on B2s) | 600 | P10 below any vocal prior; P14; LF opt-in |
| X18_applause_crowd_30s | 30 | applause bed 0.3–6 kHz swelling twice (+12…18 dB/2 s), undulating octave regions; 3 crowd whistles; "woo" shouts with scoops | 0 ev | 300 | broadband swells (relative "growth" everywhere), P1-less whistles, common mode |
| X19_handheld_ring_stalls_and_hops | 9 | 2350 Hz τ 8 ms: grows 31 dB/s from t=2, **stalls/sags 11 dB** (e −0.15 for 0.6 s), regrows 44 dB/s to −10, **hops +125 c** at 4.6 s (6 c drift); vocal melody −28 | 1 ev: on 2.0, vis 2.45–2.75, pk 42–44 | 300 | non-monotone P6; P3 with a hop (continuation, not a new budget line) |
| X20_mains_hum_and_hvac_whine | 12 | 50 Hz hum + buzz 100…350 Hz (−46, exact family, dead steady, from before arm) + family-less 587 Hz whine (−50, ±0.3 dB) + speech 3–9 s | 0 ev (tag irreducible_passive for the whine) | 300 | stationary non-feedback lines: P1 (hum has family), zero growth, 1 dB/dB; speech τ_a FPs |
| X21_reverberant_area_mic_slow_ring | 14 | area/choir condenser, reverberant loop τ 70 ms at 642 Hz (b50 +45 c), e 0.8±0.15 → ~11 dB/s, seeded at −31 by a choir partial, sat −20, hop −130 c at 7 s; sung SATB chord pad (vibrato) around it | 1 ev: on 3.0, vis 3.9–4.2, pk 26 | 600 | long-τ slow growth *inside* the 6–60 window but masked (prominence < 12 while growing); large hop; P3/P12 vs chord tones |
| X22_kick_mic_sub_ring_65Hz | 14 | GENUINE LF feedback: kick mic → drum-fill sub, 64.6 Hz (b17 +30 c), τ 25 ms, e −2 (rings on kicks) → +0.5 at 6 s → 20 dB/s, sat −10; rock kick 62 Hz + bass + bed | on 5.9, vis 6.0–6.3, pk 39–40 (EARLY on kicks) | 1000 | P10: a hard 100–160 Hz floor fails here; τ_a 110 ms; P14; LF opt-in |
| X23_ringout_quiet_room_two_modes | 16 | ring_out with NO programme: room noise only (all via the mic, coupling 0 dB), +1 dB/1.5 s ×9 from 1.5 s; modes 625 Hz (e −5.4) and 2520 Hz (e −6.7), τ 10 ms, cross on steps 6/7 | 2 ev: on 9.0 / 10.5, vis 9.0 / 10.6–10.7, pk 47 | 300 | P7 probe in its home case: band 50 medians per step −67.5, −64.1, −64.6, −59.9, −57.6, −51.7 (ref band +1/step); band 70 +1.2, +1.1, +0.9, +2.8, +2.2, +5.3; everything below the −45 gate until runaway |

Ground truth for all seeds: `reports/corpus-critic/ground_truth_all_seeds.json`.

---

**K — kill check (closed loop: the ring must be DEAD by the end; every ring above has e ≤ 2 dB and dies to one −3):**

| scenario | s | content | ground truth | bud | stresses |
|---|---|---|---|---|---|
| K1_limiter_held_howl_e4_2k5 | 14 | 2.5 kHz on GEQ 22, e 4, τ 40 ms → 100 dB/s to limiter −13; vocal + band | on 3.0; −3 → tap −16 flat (e_eff +1); needs −6 | 300 | P13 (drop ≈ bell ≠ programme), deepening |
| K2_limiter_held_howl_e7_1k25 | 14 | 1.25 kHz on GEQ 19, e 7, τ 70 ms → limiter −10 | on 3.0; −3/−6 both leave it flat; needs −9 | 300 | P13, full-depth deepening |
| K3_established_limiter_plateau_e5_5k | 14 | S2a datum with e 5: 5 kHz on GEQ 25 at −8 from frame 0, quiet music | from t=0; −3 → −11 flat; needs −6 | 300 | P13 on an at-arm line, AT-ARM re-emission |
| K4_compressor_plateau_e6p5_quiet_2k5 | 14 | 2.5 kHz, e 6.5, τ 60 ms → compressor plateau −22 (~30 dB prominent), quiet music | on 3.0; needs −9; never loud/clipped | 300 | P13 below the loud lane, P8 |
| K5_channel_shove_into_limiter_e5p5_2k5 | 14 | loop at −0.5 dB; +6 dB channel/DCA shove at t=3 moves programme, loop and plateau (M7) | on 3.0; 0 FP on the step; needs −6 | 300 | P7 common mode, P13 |
| K6_limiter_held_howl_e3p5_midpoint_1k8 | 14 | 1789 Hz = GEQ 1.6 k/2 k midpoint, e 3.5, limiter −12 | on 3.0; one slider −3 gives ~−2.2 → survives; −6 or both neighbours −3 kills | 300 | interpolated f / flanking pair under survival pressure |
| K7_limiter_howl_hops_100c_after_first_cut_e3p5 | 14 | K1's loop, e 3.5, hops +100 c at t=4.6 (after the first cut) | on 3.0, one episode; GEQ slider still bites; a Q 6 PEQ notch gives 1.9/4.0 dB at −3/−6 | 300 | hop vs notch width, merge-or-second-notch |
| K8_limiter_howl_hops_200c_after_first_cut_e4 | 14 | as K7, +200 c (the next GEQ centre), e 4 | on 3.0, one episode; both actuators need a second band (GEQ: or −9 on the old one) | 300 | second notch / band |
| K8r_limiter_howl_hop_200c_regrows_e4 | 14 | K8 with a physical hop: the old mode loses its excess at 4.6 s, the new mode (200 c up) regrows from its seed at e/τ | on 3.0 and 4.6, two episodes; the detector re-detects the new mode on its own evidence; PEQ: second notch on it, both dead | 300 | hop with growth; K8's instant retune is tier-B material (`policy="tier_b"`) |

## 4. Simulator parameters, defaults, citations (`tests/rtasim/physics.py`, `analyser.py`, `sources.py`, `render.py`)

**Analyser (`AnalyserSettings`, physics.py:68-135)** — skirts: Butterworth-2N at neighbouring centres, `skirt_order` 3 → −18/−36/−47 dB
at ±1/2/3 (N=2 −12/−24/−31, N=5 −30/−60/−78; edge tone −3/−3 for any N) [A §3]; `skirt_reach_bands` 10. Attack: power one-pole per
band, `attack_model` 'U', `attack_k` 0.5 → τ_a = 0.5/Δf clamped [5, 600] ms (185 ms @ band 10, 23 ms @ band 40, sub-frame > 300 Hz;
'BQ' → 1/(πΔf)) [A §1, L §1.6]; `filter_release_k` None (ring-down shares the poles: below ~150 Hz the *filter* limits the fall,
23 dB/s @ 39 Hz). Display: PEAK = max over 4 `substeps` per frame then dB-linear release at `release_law_db/decay_s` = 60/1.0 =
60 dB/s (law UNCERTAIN: sweep 17/60/240) [A §2]; `peak_hold_s` 0; `det` PEAK (`rms_noise_offset_db` −8, `rms_noise_sd_scale` 0.6
for RMS); estimation noise χ²(2ν)/2ν below `nu_gauss_min` 4 else Gaussian-dB 4.34/√ν, AR(1) over τ_a, `noise_sd_scale` 1 [A §1
statistical floor]; `self_noise_db` −115; `gain_offset_db` 0; `quantum_db` 1/256; clamp [−128, 0]; `pre_roll_s` 1.5;
`frame_jitter_s` 0.003; `drop_frame_prob` 0. Presets: `ANALYSER_PRESETS` (bq, u_slow, butter3_release, skirt_n2/steep, decay_fast/4s/16s,
release_law_17/240, rms, peakhold_2s, flat_beds).

**Loop (`FeedbackRing`, sources.py)** — `excess_db` (dB over threshold), `tau_loop_s` (wedge 0.005, tops 0.010, lav 0.022, reverb
0.070; growth = e/τ dB/s, cap 2000) [L §1.3]; `excess_points` schedule; `excess_wander_db` 0.08 @ 0.05–0.6 Hz; `sat_db` plateau
(≥ 0 → clip flag + `clip_harmonics` H2 −30/H3 −12/H5 −18/H7 −24 above `clip_knee_db` −10) [L §1.4, §2.2]; `harmonics`/
`harmonics_knee_db` (acoustic clip, off by default); `sat_tracks_gain` with `Scene.sat_coupling`; sub-threshold: comb regeneration
(`excitation_db` −90 on-mode floor; programme lines within ±0.15 oct comb-weighted and bed band-mean, both × `excite_coupling_db`
−6 dB = share of the tap signal that comes through the open-mic path; 0 dB in S6b, −3 in M2); relax/build-up at max(|e|, 0.1)/τ;
over-threshold seed = coupled excitation within the capture range; `wander_db` 0.3 (multi-rate, seeded), `hop_at_s`/`hop_cents`,
`freq_drift_cents` 0; `established`. Scene: `prog_coupling` (1 = the move is between mic and tap / all programme is mic spill;
0 = bus master at a pre-fader tap [L §0]), `loop_coupling` 1, `sat_coupling` 0, `geq_q` 3 (RBJ peaking; X32 Q UNCERTAIN [L §4.1]),
`geq_init`, `geq_schedule`. `DrivenResonance`: gain 10 dB, Q 20, T60 1.2–1.5 s, drive within 1.5 bands [A §6].

**Programme** — `TIMBRES` (physics.py:213-): voice (H2 +2 … H16 −42), voice_closed (−20, −26 …), speech (32 partials, F1/F2/F3
shape), bass_gtr (H2 +3), 808/synth_sine_bass (H2 −35), el/ac_guitar, organ_flue (−36, −50), organ_8_4, flute (−15…),
flute_high (−26…), whistle (−42), sine_lead (−48), sine, piano, saw, square, hum [A §4.2]. `HarmonicNote`: attack_s (0 = instant;
analyser then imposes τ_a), decay/partial_decay/release dB/s, vibrato rate/depth/delay/ramp, glide, AM, `drift_cents`, `flutter_db`,
`timbre_jitter_db`, seed. Sequencer humanisation: `HUMANIZE` table; BassLine drift 3 c / flutter 0.3 / jitter 3 dB; ChordPad
flutter 0.15, jitter 2, ±1 dB voice spread, `strum_s`. `PinkBed`: level@1k, tilt, f_lo/f_hi (12 dB/oct outside), non-periodic
`lfo_db`, `random_humps`/`hump_db`, `region_mod_db`, `swell`, on/off envelope. Drums: kick (thump + glide 370 c/120 ms + H2 −20 +
click), snare, hat, crash (+level over bands 58–97, 12 dB/s), ride (wash + bell lines).

**Scoring constants** — `render.PROM_VISIBLE_DB` 12, `PROM_PRESENT_DB` 6, `DOMINANCE_MIN` 0.5, `EXCITE_REACH_OCT` 0.15;
`harness.OCT_TOL` 1/6, `ATTRIB_LOOKBACK_S` 1.0, `EPISODE_GRACE_S` 1.0, `EARLY_CREDIT_S` 2.0, `ACTUATION_DELAY_FRAMES` 1; per-scenario
`latency_budget_ms` (300 default; 600 for LF/decay-16; 1000 for slow/plateaued-under-music).

---

## 5. Sweepable uncertainties (pass `analyser_overrides={…}` to `evaluate`/`frames`, or scenario kwargs)

A candidate must hold its pass/fail across at least: `attack_k` {0.32 ('BQ'), 0.5, 1.0} — decides which bands turn onsets into
6–60 dB/s "growth"; `filter_release_k` {None, 1/π}; `skirt_order` {2, 3, 5} — prominence ceilings 24/36/60 dB and how much
vibrato fills ±1; `decay_s` {0.25, 1, 4, 16} × `release_law_db` {17, 60, 240} — what "persistence", "outlasts" and VERIFY mean;
`peak_hold_s` {0, 1, 2}; `det` RMS with `rms_noise_offset_db`; `noise_sd_scale` {0, 0.5, 1, 1.5} and `nu_gauss_min` — LF raggedness
of beds and of the neighbour median; `gain_offset_db` {0, 12, 24}; `substeps` {1, 2, 4, 8} (onset phase within the frame);
`frame_jitter_s`, `drop_frame_prob` {0, 0.01}; `pre_roll_s`. Loop side (scenario kwargs / new scenarios): `tau_loop_s` 0.005–0.07,
`excess_db` 0.03–6 (3–1200 dB/s), `excess_wander_db` 0–0.3, `sat_db` −40…0, `harmonics`, `excite_coupling_db` −12…0 (sets how
visible sub-threshold ringing and the probe over-response are — the single most consequential unknown for any P7/P14 design),
`wander_db`, hops, `Scene.prog_coupling/sat_coupling` {0, 0.5, 1}, `geq_q` {2, 3, 4.3}. Programme side: timbre tables (bass H2 vs H1,
organ/flute H2), note level vs bed (prominence 12–35 dB), vibrato depth/rate/delay, drift/flutter, bed tilt/humps, room-noise
level near the −45 gate, kick tuning, DrivenResonance gain/Q. Scoring: PROM_VISIBLE_DB, DOMINANCE_MIN, OCT_TOL, EARLY_CREDIT_S
(set 0 to stop crediting pre-onset detections in feedback_watch designs), latency budgets.

---

## 6. Baseline: the CURRENT detector on the extended corpus

`x32mcp.detector.FeedbackDetector`, `DetectorConfig.from_descriptor(device.yaml)`: prominence 12 dB over median ±3, min_level −45,
persistence 3, growth window 6–60 dB/s (ref 20), weights 0.3/0.2/0.5, threshold 0.7, override ≥ 25 dB for ≥ 6 frames, budget 6.
Seeds 1–3. Files: `baseline_current_detector.json`, `…_closed_loop.json`, `baseline_preM7_no_override.json`,
`…_no_override_closed_loop.json`, `baseline_tables.md` (all under `reports/corpus-critic/`). Totals — **open loop: 61 scenarios, 12 pass; 117 visible events, 108 TP, 9 miss, 948 FP (+143 HARM, 21 TAIL, 9 EARLY)**; closed loop (32 feedback scenarios × 3 seeds): 10 pass, 105 events, 96 TP, 9 miss, 286 FP, 334 cuts, and FP/EARLY cuts pre-empt the real ring in S12/X8/X9/X17 (0 events left to find); **pre-M7 (override 0): 9 pass, 79 TP, 38 miss, 834 FP** (closed loop 4 pass, 76 TP, 35 miss, 281 FP, 297 cuts) — the override buys S2a/b/c, M1, X14, half of X11/X12 and nothing on X7/X21, at the price of +114 FP on whistles, flutes, sine leads, organ, 808s, bells.

"ovr"/"gro" = detections produced by the override path (slope < 6, prominence ≥ 25) vs the growth path (slope 6–60 → confidence
0.3+0.2+0.5·min(1, slope/20) ≥ 0.7 needs slope ≥ 8 dB/s at full prominence).

| scenario | ev | TP | miss | FP (ovr/gro) | other | lat ms | verdict | pre-M7 | diagnosis (which term) |
|---|---|---|---|---|---|---|---|---|---|
| C0, C1, S17 | 0 | – | – | 0 | | | PASS | PASS | (kick alone: τ_a rise restarts every hit before 3 frames qualify) |
| S1_bass_under_quiet_music | 0 | | | 32 (0/32) | | | FAIL 0/3 | same | growth: τ_a-limited onsets of H2/H3 at 84–146 Hz, slope median 13 dB/s, prominence 15 → conf ≥ 0.7. The M7 40/80 Hz mechanism, reproduced. |
| S2a/S2b/S2c established | 3 | 3 | 0 | 0 | S2c 96 HARM | 251 | PASS | **miss all** | TP only via override (slope 0 → 0.5 without it). S2c: 32 HARM/seed on H2/H3/H5/H7 (closed loop spends 5 cuts there). |
| S3_ring_during_music | 3 | 3 | 0 | 5 (0/5) | | 148/502/602 | FAIL 1/3 | same | FP: organ note onsets (sub-frame partial first frame → 3-point slope 9 dB/s); latency > budget on 2 seeds: −45 dB level gate + the 30 dB/s ramp is discarded once by the guard when the seed jump lands in-frame. |
| S4a/S4b vocal vibrato | 0 | | | 40 (8/32), 31 (2/29) | | | FAIL | FAIL | growth on H1–H4 (vibrato/shimmer re-crossing restarts the window → fresh 3-frame slopes 14 dB/s); override on 25–27 dB-prominent H2/H3. |
| S5_guitar_decays_to_sine | 0 | | | 13 (0/13) | | | FAIL | FAIL | growth: partial onsets 167–670 Hz through τ_a (18 dB/s). |
| S6_master_ramp | 0 | | | 7 (1/6) | | | FAIL | FAIL | growth = the 10 dB/s common-mode ramp on absolute levels (conf exactly ≥ 0.70 at ≥ 8 dB/s). |
| S6b_ringout_latent_loop | 3 | 3 | 0 | 2 (0/2) | | 147/148/202 | FAIL 2/3 | same | ring found via growth after runaway (not via probe); FP: organ lines at 127/192 Hz "growing" +1 dB/step through τ_a. |
| S7_808 / X5_808_40_60 | 0 | | | 19 (6/13), 32 (11/21) | | | FAIL | FAIL (13, 21) | growth: τ_a rise 22–32 dB/s at 42–63 Hz; override: 27 dB-prominent sine sub (no family to be seen). |
| S8a organ / S8b flute / S8c whistle | 0 | | | 13 (3/10), 20 (4/16), 28 (21/7) | | | FAIL | FAIL (10/16/12) | growth on note onsets (first partial frame); override on every whistle (27–31 dB) and held flute/organ notes. |
| S9_clipped_howl_fast | 3 | 3 | 0 | 0 | 47 HARM | 248–253 | PASS | miss 2/3 | 115 dB/s discarded by the 60 dB/s guard → waits for override (6 frames). Pre-M7: found only when the guard's restart leaves an 8–60 dB/s tail. |
| S10_ring_between_bands | 3 | 3 | 0 | 0 | | 502/699/798 | FAIL 0/3 | same | late: −45 dB level gate on a line split −3/−3 over two bands + `_peaks` picks the louder band, which alternates → streak restarts; growth term arrives 0.5–0.8 s after visibility. |
| S11a / S11b two rings | 6 | 6 | 0 | 0 | | 301–549 / 202–353 | FAIL 0/3, 1/3 | same | latency: 15 dB/s ring scores growth 0.75 → conf 0.675 < 0.7 until prominence term saturates; −45 gate. No harmonic logic, so S11b/X10 "pass" by ignorance. |
| S12_guitar_wedge_196 | 3 | 3 | 0 | 5 (0/5) | 5 EARLY | 302–398 | FAIL 1/3 | same | FP: chord-tone onsets at 96/146 Hz (τ_a); EARLY: G3-excited ringing at 196 Hz (defensible). Closed loop: early + FP cuts at 100/160/315 kill the loop before onset. |
| S13_slow_5k / X8_slow_1k29 | 3 | 0 / 3 | 3 / 0 | 0 / 11 (0/11) | | – / 9952–12649 | FAIL | FAIL (X8 miss 3) | growth term 0 below 6 dB/s; S13 peak prominence 17 < 25 → never; X8 reaches 28 dB at the plateau → override 10–12 s late. X8 FP: piano/organ onsets 583–1015 Hz. |
| S14_masked_by_cymbal | 3 | 3 | 0 | 0 | | 198 | PASS | PASS | 40 dB/s inside the window; crash decays too fast to qualify. |
| S15_decay16_tails | 3 | 3 | 0 | 1 | 21 TAIL | 198–298 | FAIL 2/3 | same | TAIL: 7 re-detections/seed of the killed ring's 3.75 dB/s-releasing display (override; each would deepen the cut); FP 48 Hz bass tail. |
| S16_peak_hold_on | 0 | | | 12 (0/12) | | | FAIL | FAIL | growth on note onsets (peak-hold does not stop rises); organ/vocal plateaus < 25 dB so override silent here. |
| S18_vibrato_edge | 0 | | | 9 (3/6) | | | FAIL | FAIL | growth from hop-restarted windows; override on H2 at 1649 Hz. |
| S19_driven_room_mode | 0 | | | 40 (0/40) | | | FAIL | FAIL | growth: 42 Hz mode +10 dB build-up through τ_a (19 dB/s) on every bass note, +1 dB/step. Two GEQ cuts at 40/80 = the M7 outcome. |
| S20_song_crowd | 0 | | | 6 (6/0) | | | FAIL | PASS | override on the two crowd whistles (27–28 dB, 0.8 s ≥ 6 frames). |
| S21_pad_swell | 0 | | | 66 (0/66) | | | FAIL | FAIL | growth: 20 dB/s synchronous attack on 15 lines = textbook ring ×15; no synchrony test. |
| S22_speech_then_feedback | 3 | 3 | 0 | 98 (0/98) | | 97–151 | FAIL 0/3 | same | ring found fast (55 dB/s); FP: every syllable's F0/H2/H3 (118–272 Hz) rises through τ_a at 20 dB/s → 33 FP/seed; closed loop burns 47 cuts at 125–630 Hz. |
| S23a_autogain / S23b_gain+24 | 0 | | | 18, 16 (0/all) | | | FAIL | FAIL | S23a: bass onsets as S1 (drift itself too slow to matter); S23b: 808 at −6 dBFS, 47 dB/s τ_a rise. |
| S24_bells | 0 | | | 8 (8/0) | | | FAIL | PASS | override on 27–30 dB triangle/glock lines (slope negative). |
| M1_loud_band_wedge | 3 | 3 | 0 | 0 | | 248–252 | PASS | **miss all** | 200 dB/s → guard → override at 6 frames (prominence 34). |
| M2_ringout_two_modes | 6 | 6 | 0 | 8 (0/8) | | 98–301 | FAIL 0/3 | miss 1 | modes found via growth after runaway (42/137 dB/s: the second only after a guard restart); FP organ onsets 583–884 Hz; closed loop 17 cuts, 7 on music. |
| M3_jazz_lav_400 / X13 (decay 16) | 3 | 3 | 0 | 0 | | 202–398 / 298–499 | FAIL 1/3 / PASS (600) | same | 23 dB/s ring detected by growth; latency 300–500 ms because prominence builds slowly against piano partials; decay 16 costs +100 ms. |
| X1_organ_held | 0 | | | 9 (9/0) | | | FAIL 1/3 | **PASS** | override only: 25–26 dB-prominent dead-steady E5 for 7 s re-emitted every cooldown second. |
| X2_flute_held / X3_whistle_held | 0 | | | 20 (14/6), 21 (15/6) | | | FAIL | FAIL (13, 9) | override on the held notes (25–31 dB); growth on onsets (15–20 dB/s partial frame + expressive swell). |
| X4_sine_lead | 0 | | | 26 (21/5) | | | FAIL | FAIL (12) | override on every note ≥ 6 frames; growth on portamento arrivals (band entry looks like 20 dB/s). |
| X6_soprano_edge | 0 | | | 7 (5/2) | | | FAIL | FAIL (2) | override on H2/H3 clusters; growth from hop restarts. |
| X7_plateaued_under_music | 3 | **0** | **3** | 4 (0/4) | | – | FAIL | FAIL | **the M7 false negative generalised**: prominence 15–25 (fluctuating with the music) never holds ≥ 25 for 6 frames; growth 0. FP: piano onsets next to it. |
| X9_midpoint_525_speech | 3 | 3 | 0 | 95 (1/94) | | −200/151/902 | FAIL | same | ring found (13.6 dB/s) but seed 3 900 ms late (band alternation + gate); 32 FP/seed on speech F0/H2 (τ_a). |
| X10_exact_octave | 6 | 6 | 0 | 0 | | 150–303 | FAIL 2/3 (303 ms) | 0/3 | 50/62 dB/s: at the guard edge; latency set by which frames the guard discards. |
| X11_amp_clip_−12 | 3 | 3 | 0 | 2 (0/2) | HARM | 100/152/1649 | FAIL 1/3 | miss 1 | 150 dB/s → guard; prominence 24–28 in the mix → override flaky (1.6 s on one seed); pre-M7 misses. FP vocal/guitar onsets. |
| X12a channel drop/raise | 6 | 6 | 0 | 5 (0/5) | | 251–301 | FAIL 0/3 | miss 4 | ring via override both episodes; FP at the +20 dB raise: organ/bass lines rising through τ_a (221, 583, 1166 Hz) at 12–18 dB/s = growth. |
| X12b bus-master drop/raise | 6 | 6 | 0 | 4 (0/4) | | 249–253 | FAIL 1/3 | miss 3 | programme static → only bass-onset FPs (146 Hz); re-grown ring at 100 dB/s → guard → override. |
| X14_peakhold_M1 | 3 | 3 | 0 | 0 | | 248–251 | PASS | miss all | as M1; peak-hold hides nothing the current detector uses. |
| X15_kick_bass_55 | 0 | | | 41 (0/41) | | | FAIL | FAIL | growth: bass H2/H3 (84–110 Hz) re-struck through τ_a, 17 dB/s, 14 FP/seed → GEQ 80/100/160. |
| X16_wedge_315_loud | 3 | 3 | 0 | 0 | | 98–103 | PASS | PASS | 240 dB/s: guard restarts, the last partial frame + plateau wander leaves a 10–60 dB/s 3-point slope → lucky growth TP at 100 ms. Not robust (see X11). |
| X17_ring_122_acoustic | 3 | 3 | 0 | 18 (0/18) | 3 EARLY | 103–348 | FAIL 0/3 | same | ring found by growth (33 dB/s); FP: guitar note onsets 146–292 Hz through τ_a; EARLY = B2-excited ringing. Closed loop: 28 cuts, loop pre-empted, budget gone. |
| X18_applause_30s | 0 | | | 35 (1/34) | | | FAIL | FAIL | growth: shouts/whistle onsets and swell-relative rises 385–1649 Hz; override on one whistle. |
| X19_handheld_stalls_hops | 3 | 3 | 0 | 54 (4/50) | | 103–497 | FAIL 0/3 | same | ring found; 18 FP/seed on the vocal melody (onset slopes 16 dB/s, override on 26 dB notes); after the hop the ring is re-emitted as a new band (new budget line in closed loop: 46 cuts). |
| X20_hum_whine | 0 | | | 55 (0/55) | | | FAIL | FAIL | NOT the hum (dead steady, < 25 dB: never fires) — the speech bursts: F0/H2/H3 at 118–360 Hz through τ_a, 18 FP/seed. The hum/whine remain a trap for any P3/P5-based redesign. |
| X21_reverberant_slow_642 | 3 | **0** | **3** | 8 (0/8) | | – | FAIL | FAIL | seeded at −31 dB by the choir partial it sits next to, the ring ramps 11 dB/s to −20 while its single-band prominence is < 12 (chord partials at ±1–2 bands): no candidate exists during growth; at the plateau prominence 11–18 < 25 → never. X7's mechanism with growth present but masked. FP: choir note onsets 200–400 Hz. |
| X22_kick_sub_65 | 3 | 3 | 0 | 4 (0/4) | 1 EARLY | 99–103 | FAIL 1/3 | same | found by growth (20 dB/s; τ_a at band 17 adds a frame); FP: bass H2/H3 onsets at 100/200 Hz; EARLY: kick-excited ringing at 65 Hz. Note the current detector has NO LF floor — a redesign with a fixed 160 Hz floor turns this row into a miss. |
| X23_ringout_quiet_room | 6 | 6 | 0 | 0 | | 303/501/852 | FAIL 0/3 | same | no probe logic and a −45 dBFS gate: both modes are invisible (−78…−52) through six steps of ≥ 2 dB/dB over-response and are caught only after runaway (63/24–33 dB/s), 300–850 ms after visibility; closed loop then needs 9 cuts for 6 events (re-emergence after −3). |

**Reading across the table:** every FP is one of three mechanisms — (i) the growth term fed by the analyser's own LF rise time or by
the partially-integrated first frame of any onset (S1, S5, S7, S12, S15-17, S19, S21-23, X5, X9, X12, X15, X17-22: 80 % of all FP,
GEQ cuts at 40–315 Hz and 500–1250 Hz); (ii) the override on any ≥ 25 dB family-less or family-ignored line held ≥ 300 ms
(whistles, flute, organ, sine lead, 808, bells, vocal H2: S4, S8, S20, S24, X1-X4, X6); (iii) common-mode rises scored as growth
(S6, X12a). Every miss/late TP is one of: growth outside 6–60 (S9, S13, X8, X10, X11, M1 — rescued or not by the override),
prominence < 25 with no (visible) growth (X7, X21 = M7's 0.50-forever case at realistic levels), or the −45 dBFS gate + band
alternation on split lines (S10, X9, X23).

---

## 7. Residual weaknesses of the corpus (designers must not be allowed to exploit these)

1. **The analyser is a model, not a measurement.** *(Measured 2026-09-22/23, docs/research/meters.md Verification log: rise
   times match the settle rule with k = 1.0 frame for frame (78 Hz: 5 frames, 156 Hz: 3, ≥ 947 Hz: 1) — the simulator's own
   τ_a = 0.5/Δf is too fast at LF; the release is 20/decay dB/s (80 at 0.25, 20 at 1, 5 at 4, 1.2 at 16), not 60/decay —
   TAIL is 3× longer than modelled — and `decay` slows the attack as well (a 2 kHz tone: 1 frame at 0.25, 3–4 at 1); skirts are at the `skirt_order` 5 end (±1 band ≈ −40 dB, ±2 −82, ±3 −100 under PEAK); the
   floor is −97 under RMS and −128 under PEAK, levels equal; gain, autogain and peak-hold never reach the stream, so the
   `peak_hold_s` and `gain_offset_db` sweeps model a screen effect the detector can never see; the band centres are
   `20·2^(i/10)`, a constant +0.034 oct above the DOC table the code uses (fixed in `review/rta-band-grid`); and the
   skirts are uniform (order ≈ 5) above ~200 Hz but widen to order ≈ 3 at 100 Hz and ≈ 2 at 50 Hz. Defaults are kept for
   baseline continuity; a corpus revision should adopt attack_k 1.0, a frequency-dependent skirt order, decay 0.25 → 80 dB/s
   and the corrected grid.)*
   *(2026-09-24, REVIEW_RESPONSE §3 and §4: in the RMS state, which every capture after an arm-time PEAK write is in, the
   display is one pole on power with T20 = decay, attack and release alike (PEAK attacks within a frame); the
   baselines ran at decay 1.0 × 60 dB/s, which is 25 % slower than the 80 dB/s the desk shows at the forced 0.25, not 3×
   faster; the LF attack is a window filling (length ≈ 1.65/Δf), which a one-pole with attack_k 1.0 does not reproduce;
   the ±2 and ±3 skirt figures need the sweep re-run under verified prefs.)*
   Per-band power one-pole (τ_a = 0.5/Δf), Butterworth-2N skirts, dB-linear
   release, guessed peak-hold/decay/gain semantics. A real X32 may smear an LF tone over ±2–4 bands (FFT), have window-shaped
   rises and different skirts. Any design that normalises growth by *this* τ_a(i) curve, matches *this* N=3 skirt template (P11), or
   keys on the exact release slope is fitting the simulator. Claims must hold across `attack_k`∈{0.32,0.5,1}, `skirt_order`∈{2,3,5},
   decay/law and RMS/PEAK sweeps (§5), and the §2.6 oscillator self-test of [A] must replace these numbers before shipping.
2. **Estimation noise is independent across bands and stationary; beds are smooth humps.** Real mixes have dense transient partials
   everywhere; the corpus's FP pressure is a lower bound. "0 FP here" is necessary, not sufficient.
3. **Timbres are static tables (+±3 dB jitter).** No vowel formant filtering, no inharmonic stretch, no brass/sax/bowed strings/
   accordion drones/distorted or deliberately fed-back guitar, no Leslie FM on chords, no crowd singing. A harmonic-family test tuned
   to `TIMBRES` is over-fitted; H2 of organ/flute/whistle/sine lead was deliberately set at or below the bed so P1 cannot rescue them.
4. **Modulations are sums of 3–5 incommensurate sinusoids** (`Wobble`): smooth, band-limited (< 7 Hz), bounded, and with a discrete
   modulation spectrum per note/ring. A detector may test "4–8 Hz vibrato present/absent" or "level sd over N frames"; it must not
   template-match modulation spectra or rely on wander being differentiable.
5. **The loop is scalar and single-delay**: one mode per ring, exact comb, hard `min()` plateau (no limiter attack/release overshoot,
   no compressor ducking of the howl by the programme sharing its channel), hops are instantaneous retunes, growth is e/τ with e
   piecewise-smooth. Real plateaux pump by 1–3 dB with the programme; real growth curves have kinks. Do not require |curvature| ≈ 0
   or plateau sd < 0.2 dB.
6. **Probe (P7) visibility depends on two unmeasured knobs**: `excite_coupling_db` (share of the tap signal that arrives through the
   mic: 0 dB in S6b/X23, −3 in M2/X22, −6 elsewhere) and the comb/band geometry. The corpus says an HF band excited by noise
   over-responds ≥ 2 dB/dB only within ~3 dB of threshold and with ±2 dB step-to-step noise on 1 s medians; LF/tonal excitation is
   stronger. A design that needs +3 dB/dB at −4 dB is fitting the old (flat-boost) simulator; one that needs 20-frame medians per step
   must budget 2 × dwell per decision. Reverberant multi-delay loops (X21) have no clean comb at all — only one such scenario exists.
7. **Scoring generosity**: attribution is ±1 band or 1/6 oct with a 1 s dominance look-back, so junk detections next to a live ring
   score TP/DUP, and TP timing starts at t_onset (negative latencies happen when the detector fires on programme sharing the band).
   EARLY credit (2 s) rewards cutting programme-excited ringing in feedback_watch scenes (S12, X17, X22): report EARLY separately
   and re-run with `EARLY_CREDIT_S = 0` before claiming watch-mode latencies. HARM/TAIL/DUP cuts cost nothing but budget.
8. *(2026-09-25: the 2026-09-23 "GEQ realises a third of its depth" reading is WITHDRAWN — it was one leg of the stereo Main
   through the summed tap, as REVIEW_RESPONSE_2026-09-24 item 1 computed. With both legs cut the Dual GEQ delivers 12.0 dB for
   a −12 slider and 5.5–6.8 dB for −6 (meters.md 2026-09-25 item 13): the renderer's nominal-depth GEQ is right in depth; only
   its bell Q is a guess (RBJ 3 here; ≈ 4.3 by the 09-23 off-centre fit).)*
   **Closed loop is idealised** — RBJ bells of guessed Q on a PRE insert, one-frame actuation, no bus dynamics, VERIFY/deepen/release
   logic of cfs.py not exercised, scenarios end at their duration (the next mode after a cut appears only in M2/X23), no operator
   fighting the system. A notch policy cannot be validated here, only a detector — except for the one policy property
   the K series checks: whether the loop is actually dead when the scenario ends (`survived`, §1).
9. **Three seeds, C-major random walks, hand-placed ring offsets.** 61 × 3 realisations are few enough to over-fit; hold-out seeds
   (4–9) and a sweep of ring offsets across the 120-cent cell (only S10/X9 sit at the midpoint, at 2.6 k/525 Hz) are mandatory before
   any pass claim. Scenario noise seeds are salted by name: renaming changes the realisation.
10. **Irreducible pairs are in the corpus on purpose**: X4's first 1.5 s (dead-flat −26 dBFS sine, seen to start) vs X7 (−30 dBFS
    plateaued ring, not seen to start) vs X20's 587 Hz whine (−50, from before arm) vs S2a (−8, from before arm). Passive physics
    separates them only by onset history, level and duration; a design that passes all four with one threshold has found a corpus
    regularity (levels −8/−26/−30/−50), not a law — vary `sat_db`/note levels before believing it.
11. **LF coverage is thin and the priors are baked into tags**: genuine sub-160 Hz rings exist only in X22 (65 Hz), X17 (122 Hz), S12
    (196 Hz), all tagged `lf_optin`; every other LF line is programme. A detector that reads the tag (or hard-codes "LF = music
    unless opted in") passes by construction; the harness does not pass `lf_optin` to the factory — keep it that way, and add
    un-tagged LF rings before trusting any frequency prior.
12. **No scenario has two open mics with different τ, an operator riding a fader continuously through a ring, autogain ON during a
    ring, RTA source switched mid-session, or frame bursts/duplicates** — all seen or plausible on the real desk.
13. **A plateaued howl answers a cut with exactly the cut depth whenever its excess ≥ the depth** (K series). Limiter, amp/desk
    clip and compressor plateaux are described by `sat_db` as a hard `min()`, and the tap sits after the PRE-insert GEQ, so a −3 on
    a ring with e ≥ 3 reads −3 dB and flat at the tap while the room is unchanged at the limiter ceiling — indistinguishable by
    level from programme through the EQ (loop brief §1.4 says the plateau is the normal steady state; §4.1's "dropped ≈
    attenuation and flat ⇒ programme" holds only for e < attenuation). Until K existed every ring here had e ≤ 2 dB and died to
    one −3, so no closed-loop number said anything about deepening. What the corpus still cannot show: the *transient* a real
    limiter adds (undershoot on the loop's decay rate, recovery on the limiter's release) — the one level-domain signature that
    could separate the two cases, and a desk measurement, not a simulator constant.

## 8. Actuators: GEQ insert (default) and bus PEQ (`actuator="peq"`)

`run_one`/`evaluate`/`python -m rtasim.harness --closed --actuator=peq` cut through a stand-in for the product's
`PeqNotchController` (`docs/PEQ_ACTUATOR_DESIGN.md`): a Q 6 (6.103 on the desk grid) peaking notch on the bus PEQ at the
detection's interpolated frequency snapped to the desk's 201-step log grid, −3 dB steps to −12, deepened when a later
detection lands within 0.08 oct of an owned notch, otherwise a new band (4 of 6). The renderer's `set_peq_notch` uses the same
RBJ bell as the GEQ (`physics.peaking_gain_db`, ≡ the detector's `bell_attenuation_db`); GEQ and PEQ bells sum in dB ahead of
the tap. `RunResult.notches` records every write with actuator, centre, Q and gain; `cuts` keeps its (ts, band, gain) shape
with band = the GEQ band nearest the notch so `survived` and every consumer stay actuator-agnostic. The detector's
`note_cut` gets `q=` once its signature accepts it (design §2.4); until then it brackets the drop with its GEQ Q range, which
a full-depth on-centre PEQ notch satisfies. What the stand-in does NOT model (the product must): band classification at arm,
the engineer's HPF, eq/on, pushes, the two-call open, `off_centre` re-measurement, mixed GEQ+PEQ sessions, Main LR. The X32's
own PEQ shape and Q definition are UNCONFIRMED (design §9): desk test 3′ sets the Q-scale bracket.
