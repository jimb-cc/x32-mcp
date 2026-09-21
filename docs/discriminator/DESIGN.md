# disc-sequential-evidence — CFS² feedback discriminator as a sequential hypothesis test

Worktree `…/scratchpad/worktrees/disc-sequential-evidence`, branch `wt/disc-sequential-evidence` (from `wt/corpus-critic` @ 1be8603).
Code: `src/x32mcp/detector.py` (FeedbackDetector rewritten, NotchController untouched), driver `tests/rtasim/run_disc.py`,
trace tool `tests/rtasim/trace_disc.py`, tests `tests/test_detector.py`, config `device.yaml` (`detector:` keys added).
Metrics: `reports/disc-sequential-evidence/metrics.json` (+ `metrics_tables.md`, `harness_dropin_{open,closed}.json`,
`cost_per_frame.json`). Citations: [L §x] loop-physics brief, [A §x] analyser brief, [C §x] CORPUS.md.

Headline (3 seeds, 61 scenarios, corpus as `cfs` would arm it — watch or ring-out per scenario, probe + note_cut in ring-out):
**0 false positives on every music / note / control / trap scenario, open and closed loop; 116/117 rings detected (1 miss:
X21 seed 2); median latency 148 ms, 79/109 TP latencies ≤ 300 ms, 94 ≤ 600 ms.** Baseline (current detector): 936 FP, 6 miss.
Forced-watch (no probe anywhere): 0 FP, 116/117. Forced-ring-out on the whole corpus (contract violated: music under
ring-out thresholds): 119 FP — the cost asymmetry doing exactly what it says.

---------------------------------------------------------------------------------------------------------------------

## 1. Decision logic

### 1.1 Shape of the procedure
Every narrow spectral line ≥ `track_prominence_db` (6 dB) over the ±3 median is tracked as a `Candidate` = one hypothesis
test H_F ("self-oscillating loop") vs H_P ("programme / stationary environmental line"). Per frame the detector measures
nine bounded evidence terms and sums them into `llr` (nats, clamped to [−6, +8]):

    llr = prior + narrow + family + stable + steady + shape + level + persist + probe          (detector.py:954-1345)

Three-way output per candidate per frame (`Candidate.state`):
* `llr ≥ emit_llr` → **EMIT** (a `Detection`; re-emitted once per `cooldown_s` only while the line is undiminished or
  regrowing — a working cut is never deepened, detector.py:1574-1577);
* `llr ≤ dismiss_llr` → **DISMISSED-AS-MUSICAL** (published, never emitted; evidence keeps flowing, so a loop that later
  takes over the band of a chord tone can climb out);
* otherwise **KEEP WATCHING** (published as `cfs.candidate` with the full `terms` breakdown and `reasons`).

Why sums of bounded, saturating terms rather than a running product of per-frame likelihoods: successive observations
of "still stable / still steady / still no partials" on the same line are almost perfectly correlated (an organ note is
stable on its 100th frame because it was on its 10th), so counting them per frame would let *duration* masquerade as
*evidence* — precisely the "override at ≥ 6 frames" failure generalised. Each term is therefore the log-likelihood ratio
of the *state* of that feature given everything seen so far, saturating at the value the physics supports; the genuinely
sequential terms (growth per frame, probe per step, programme boundaries survived) accumulate. The per-frame increment of
`llr` is the innovation. This keeps the SPRT reading (thresholds from costs, §1.4) while being honest about dependence.

### 1.2 Tracking mechanics (what a "line" is) — detector.py:632-950, 1438-1540
* **Cluster** = bands within ±1.6 of the line's previous sub-band position inside b−2..b+2: power sum (`cl_db`) and
  power-weighted centroid. Anchoring keeps an edge tone's pair fixed while its loudest band alternates ([A §3]: a line is
  one band or two adjacent bands; S10/X9 sit on an edge). `freq_hz` is the centroid frequency (GEQ choice, [L §2.1]).
* **Intrusion**: a programme partial landing in b±1 lifts that skirt while the peak band stays on its own trajectory
  (predicted from its last two increments); real FM moves energy *out* of the peak (anti-correlated, [A §4.4]). Intruded
  frames keep the previous centroid and use the peak-band increment (detector.py:884-897).
* **Masking**: when the ±2..±4 neighbourhood floor jumps ≥ 4 dB more than the spectrum reference did (hi-hat, cymbal,
  chord onset next to the line) *and* the line is buried (< prominence_db+3 over it), the frame is skipped — not scored
  either way — and a track without a peak is kept alive up to 12 frames instead of dying (S3/S13/S14, [A §4.2 cymbals]).
* **Re-arm** (detector.py:787-843, 905-921): a ≥ 8 dB newcomer within ≤ 2 frames on top of an established weaker line
  that was not itself rising, or a line that decayed ≥ 12 dB and climbs ≥ 6 dB again, is a *new event*: family/vibrato/
  onset/growth lineage belonged to the previous occupant (speech partial that seeded the 525 Hz mode in X9, the choir
  partial under the 642 Hz mode in X21, the bass partial a band under the 318 Hz wedge ring in X16). FM cannot fake the
  jump: vibrato swings single bands by up to 10 dB but the ±1 power sum by ≤ 0.3 dB [A §4.4].
* **Association**: a peak within `band_tolerance`+0.55 of the centroid continues the track, except a ≥ 6 dB louder peak
  ≥ 0.7 band away (a new line, not a hop — a hop keeps its level, [L §2.3]).
* **Spectrum reference** `ref` = 3-frame median of the median of bands 25..85 (τ_a < 1 frame there): master/common-mode
  moves cancel in every trajectory feature ([A §5]); a `ref` jump ≥ 3 dB marks a common-mode step.

### 1.3 Evidence terms (value → physical justification → citation). `promI = clamp((prominence−14)/10, 0, 1)`.

| term | measurement | LLR contribution (nats) | physics / citation |
|---|---|---|---|
| **prior** (967) | mode + frequency | `prior_llr_watch` −1.0 / `prior_llr_ringout` 0.0; −`lf_penalty_per_third_oct` (1.2) per 1/3-oct below f_low (160 Hz watch / 100 Hz ring-out / 40 Hz with `lf_feedback_possible`, then −`lf_strict_penalty` 1.0 below 100 Hz) and above `f_high_hz` 12.5 kHz; ring-out: −min(2, 0.7·ln(1+N_other established lines)) | A prior, not a gate: SM58 far-field −10 dB @ 50 Hz × HPF × tops LF cut-off put a vocal rig's loop gain ≥ 20–30 dB under its 2–8 kHz maximum below 100 Hz [L §3.1-3.4, A §4.5]; kick/tom/acoustic pickups near subs/wedges genuinely ring at 40–200 Hz [L §3.2 e,f] → operator opt-in. Ring-out's "no programme" contract sets even prior odds; every extra established line visibly violates it and lowers the base rate. |
| **narrow** (970-987) | n2 = peak − max(±2); cluster prominence cp over median(±2..±4) | +0.4 if n2 ≥ 15 & cp ≥ 15; −1.0 if n2 < 8 & cp < 12 (hump); smoothed. **Isolation**: +0.08/dB of cp above 24 dB, cap +2.4 (n2 ≥ 15 only) | A 1/10-oct bank cannot tell a partial from a ring (both ≪ 1 band) — only lines from humps [A §0.5, §3]. But a line standing ≥ 25–30 dB over *everything* within ±4 bands is, short of a test tone or a lone sine synth in silence, a howl [L §4.3 tier A]; M7's line read 60 dB. Whistle/flute/organ reach 20–33 dB → +0..0.7, decided by the other terms. |
| **family** (989-1066) | presence-as-peak (≥ 6 dB prominent local max within ±0.75 band of centroid+10, +15.85, +20, +23.22; "weak" = local max ≥ 4 dB over min of ±2 in dense chords; ≥ 3 weak ≡ 2 strong); `sub` = a ≥ −12 dB-relative peak at −10/−15.85/−20 that owns another partial and **co-moves** (owner moved ≥ 3 dB and this line followed within 60 %) | ≥ 2 partials on ≥ 50 % of frames → −3.0·frac; `sub` → −2.0·(frac−0.3)/0.7; one partial → −0.6·frac; **latched** (−3.0) after 4 family frames/≥50 % or 10 obs/≥80 % sub; absence → +(1.0·promI + 0.6·clamp((prom−24)/12)) × clean. Void (0) at the clip flag, or level ≥ `harmonics_void_level_db` (−16) after a ≥ 2-frame fast rise, or an odd-dominant pattern (L(H3) ≥ L(H2)+3, L(H5) ≥ L(H4)). With LF opt-in below 160 Hz the negative weight is halved. Unlatched when the line rises ≥ 8 dB above the owner's level with no partials now (→ re-arm), or grows ≥ 5 dB at a loop's rate with no partials now. | Partials of voice/guitar/bass/piano sit 0…−15 dB → visible whenever H1 ≳ 20 dB prominent; a linear loop has none; a clipping loop grows odd harmonics ≥ 10 dB down only near limiting [L §2.2, A §4.1-4.3]. Absence is informative only above ~20 dB prominence [A §4.3]. Coincidence for a fixed line under running speech/music is frequent per frame but does not co-move — a true partial always does (X9: speech H4/H5 sweep the 525 Hz mode). The bass H2..H5 grid fills 40–160 Hz under any rhythm section, hence the LF discount when the operator has declared an LF loop possible (X22). |
| **stable** (1068-1106) | centroid range over last 8 observed frames, one extreme dropped; a single-frame jump ≥ 0.55 band with stability before = hop (re-centred, counted) | < 0.12 band → +0.5 (full at 6 samples); > 0.30 → −1.5, and after 6 such windows `modulated_latched` → ≤ −2.5 for the track's life; 3+ hops → −1.0 | Loop frequency is fixed by geometry and delay; a hand-held hops between candidates but does not glide [L §2.3]. Vibrato ±30–100 c / portamento / 808 pitch envelopes move the ±1 centroid 0.25–0.8 band at 5–7 Hz; a ring's centroid sd < 0.05 band [A §4.3 v-vi, §4.4]. |
| **steady** (1108-1156) | gap-normalised first differences of `cl_db` over 8 frames excluding onset/hop-sized steps (|Δ| ≥ 2.5), largest dropped → rms; residual sd about a line fitted after the last such step | rms < 0.25 & res < 0.30 → +0.5; rms > 0.6 or res > 0.45 → −2.0 (−0.8 below 16 dB prominence); frozen display (detector-wide ≥ 35 % of live bands exactly repeated AND this band's steps mostly exactly 0) → 0 and `analyser_suspect` | A sinusoid through PEAK/RMS is deterministic; noise-like content in Δf fluctuates 4.3/√(Δf·T) dB; voice/whistle/flute flutter 1–1.5 dB, Leslie/chorus beat 1–6 dB; a ring wanders ±0.3–0.5 dB below 2 Hz [A §1, §4.3 v; L P5; C §2.1]. Exactly repeated frames are RTA peak-hold, which makes every plateau meaningless [A §2, §7 S16]. |
| **shape** (1158-1303) | trajectory of `cl_db` vs `ref`: (i) *multi-frame fast*: ≥ 3 consecutive frames each +≥ 2.5 dB (the pre-birth frames of the band count) → `ev_growth_multi` 1.2/frame; a 2-frame rise that only stops within 16 dB of FS → +1.0 once; (ii) *fast*: consistent per-frame increments ≥ 0.9 dB (≥ 18 dB/s) for ≥ minrun(b) frames → `ev_growth_fast` 1.1·gw; (iii) *medium*: 8-frame LS fit of raw level minus max(0, ref slope): ≥ 6 dB/s, rise ≥ 2.5 dB, res sd ≤ max(0.35, 0.1·rise), **both halves rising** (s2 ≥ 0.45 s1) → `ev_growth_medium` 0.8·gw; (iv) *slow*: 12–20-frame fit on a 3-frame median: ≥ 1.5 dB/s, rise ≥ 1.2, res sd ≤ 0.35, both halves rising → `ev_growth_slow` 0.35·gw. Per frame the **max** of the paths, not the sum; growth total capped at 4.5; +0.7 once for a line born within 4 dB of its band's previous level that then rises ("from the floor"). gw(b) = clamp((0.85 − e^{−T/τ_a})/0.5) recovering to 1 once the linear run exceeds 3 τ_a; minrun(b) = max(2, 3τ_a/T). **Synchrony**: ≥ 2 other lines rising (ref-corrected) now or within 0.6 s, except ≤ 0.5 s after a common-mode step → no growth credit for anyone. **Onset step**: reached its plateau within the band's rise time from ≥ 6 dB below (peak-band pre-birth level, so a legato neighbour does not hide it) and sat flat, at a level below −10 dBFS → −1.9 (watch) / −2.4 (ring-out) × clamp((−10−L)/5); cancelled if it later grows ≥ 4 dB like a loop. **Decay**: 12-frame slope ≤ −2 dB/s while ref not falling and ≥ 2 dB under its max → −0.35/frame (recovers when the line regrows). FM-latched lines get 0.3× growth credit. | Growth = e/τ dB/s: 1…>500 dB/s, linear in dB (exponential amplitude), until limiter/clip/compressor — never attack–decay [L §1.3-1.4, P6]. The analyser turns any instant onset below ~150 Hz into a *concave* 2–9-frame rise (τ_a ≈ 0.5/Δf: 185 ms @ 78 Hz), hence gw/minrun and the "both halves" test [A §0.1, §1; L §1.6]. Above ~300 Hz an instrument onset is one partially integrated frame then full [A §0.1], so ≥ 3 rising frames, or 2 that stop only near full scale, is a loop with e ≳ 0.5 dB [L §1.3 "2-frame evidence must be admissible"]. A note arriving at a moderate level inside one rise time is the normal onset; a loop reaching a moderate plateau that fast needs several dB of excess *and* a compressor — uncommon in watch, ~impossible after a +1 dB ring-out step [L §1.3-1.4]. Pads/song starts raise many lines together; loops start alone [A §5]. Plucked/struck notes decay 3–15 dB/s; a loop does not decay while intact [L §2.1, A §4.2]. |
| **level** (1305-1322) | peak dB; loudest other established line | ≥ −1 (clip flag; edge tone: cluster ≥ −1.5) → +3.5; −10…−1 → +1.5…+3.5; −20…−10 → 0…+1.5; ×0.25 unless it is ≥ 3 dB above every other established line; +0.9 while persistently (leaky 8-frame count) the loudest line by ≥ 2 dB in a spectrum with other established lines | 0.0 dB is the desk's clip flag [meters.md §4.2]; a narrow line within 10 dB of FS is where a limiter/clip plateau sits [L §1.4, P8] — but in a mix already running at −15 it says little unless it is the outlier; a compressor-caught howl is the loudest line in the spectrum [L §2.2]. |
| **persist** (1324-1329) | frames tracked; programme-boundary events survived (≥ 2 genuine births with ≥ 6 dB onsets / deaths with ≥ 6 dB drops of established lines, or a ref jump, while this line held ±1 dB) | 0.5·ln(1+k/10) capped 0.6 (0 while frozen); +0.5/+0.3/+0.15 per big/medium/small boundary, cap 1.2 | Conditional on already being narrow/stable/steady/family-less the survivors are organ/synth/hum/HVAC whose durations are long, so plain duration is weak [A §4.3 i]; *outlasting programme structure* (chord changes, notes ending) while unchanged is what a ring does and a chord tone does not [L P12, A §4.3 ii]. |
| **probe** (1347-1405; API 1408-1436) | ring-out: at `note_gain_step(Δ, ts)` every line's median level/ref over the last 8 frames is snapshotted; after settle 3 frames (+2τ_a) the next 8-frame median is compared: over = Δline − max(0, Δref) | over ≥ 2 → +2.0 (+1.2 if the line was noisy); ≥ 1 → +0.8/0.4; |Δline−Δ| ≤ 0.5 or tracks Δref → −0.9/−0.5; did not move at all → −0.3 only if < −20 dBFS and never grew; born 0.1–1.2 s after a step, rising, nothing else established → +1.2 (once, that step only); a −Δ step that made it fall more than the programme → +0.8. Clamp [−4, +5]. In ring-out a line with no growth, no probe evaluation yet, not loud, family not strongly absent is held in KEEP WATCHING ("uncertain ⇒ probe, don't cut"). | Regenerative gain 1/(1−g): a +1 dB step raises a line 4 dB below threshold by +3, at −2 by +6.5; programme at the pre-fader tap moves 0 (electrical) … +1 dB/dB (spill); a driven room mode/hum exactly +1 dB/dB [L §0, §1.5, P7; A §0.3, §6; C §2.3 for the noisy band-mean case]. A limited howl's plateau is pinned downstream and does not move either [L §1.4] — hence "no movement" is only weakly negative. |

### 1.4 Thresholds from the cost asymmetry (brief Q4) — `emit_llr_*`, `dismiss_llr_*`
Bayes: act when posterior odds F:P exceed C_FP / C_FN(one decision epoch ≈ 300 ms).
* **watch** (human on the fader, programme present): C_FP = a −3 dB × ⅓-octave dip on running programme for the rest of
  the show (cuts-only), possibly deepened to −9 by re-detection, 1 of 6 budget lines, and — M7 — the budget the real
  event needed [L §4.2, HANDOVER §4b] ≈ 10 units; C_FN = 300 ms more of a howl that a present human catches in 1–3 s
  anyway ≈ 0.5 unit → odds 20:1 → **emit at +3.0 nats**; dismiss at −2.5 (odds 1:12; dismissal is revocable).
* **ring-out** (server owns the gain, no programme by contract, nobody on the fader): C_FP = a wrong report line + a
  budget line, no audience ≈ 3; C_FN = an unattended howl growing e/τ per second until the abort/back-off path ≈ 3 per
  epoch → odds ≈ 1:1, plus the option value of one more frame of fast-arriving evidence → **emit at +1.4 (≈ 4:1)**;
  dismiss at −3.5 (dismissing wrongly is a miss with nobody there). And because the server owns the gain, *uncertain and
  not loud ⇒ wait for the next step's probe* rather than cut on structure alone (detector.py:1554-1558) [L §4.3].
* Both clamp to [−6, +8] so any state can be left again (a chord tone's band taken over by a loop; a killed ring).
`confidence` for the dashboard is a monotone map of `llr` that reads ≥ `confidence_threshold` exactly when EMIT fires.

### 1.5 Graceful degradation
Every term returns 0 when it cannot be measured: partial positions beyond band 99 (≥ 5 kHz: H3+ off the analyser —
family then rests on H2 alone or is neutral), ±2/±4 neighbours missing at the spectrum edges (narrow/cluster prominence
neutral), fewer than 5 samples (stable/steady neutral — the first 200 ms of any line are decided by prior + level + shape
+ family only), masked frames (nothing), frozen display (steady/persist/slow-growth withheld and `analyser_suspect` set for
`cfs` to report ANALYSER_MISCONFIGURED), no `note_gain_step` calls (probe 0 — watch mode never depends on it). LF bands
degrade *by physics*: growth weight gw → 0.17 at 39 Hz and minrun 11 frames, so an LF loop is carried by probe (ring-out),
prior opt-in, family absence and eventually long linear growth — 1.0–1.2 s at 65 Hz (X22), stated in §3.

---------------------------------------------------------------------------------------------------------------------

## 2. Metrics (seeds 1,2,3; `python -m rtasim.run_disc <out>`; JSON `metrics.json` → runs.{native,watch,ringout}_{open,closed})

Totals:

| configuration | scen | pass | events | TP | miss | **FP** | early | tail | harm | lat p50 / p90 / max ms |
|---|---|---|---|---|---|---|---|---|---|---|
| native (cfs arming: watch or ring-out per scenario, probe+note_cut in ring-out), open | 61 | 48 | 117 | 116 | 1 | **0** | 6 | 8 | 0 | 148 / 801 / 6452 |
| native, CLOSED loop (NotchController −3 dB steps, 1-frame actuation), 32 feedback scenarios | 32 | 18 | 116 | 115 | 1 | **0** | 4 | 0 | 0 | 103 / 801 / 6452 |
| forced watch everywhere (no probe), open | 61 | 47 | 117 | 116 | 1 | **0** | 1 | 8 | 0 | 150 / 801 / 6452 |
| forced watch, closed | 32 | 17 | 119 | 118 | 1 | **0** | 1 | 0 | 0 | 150 / 801 / 6452 |
| drop-in `FeedbackDetector(cfg, band_hz)` via `python -m rtasim.harness` (= watch, no note_cut), open / closed | 61 / 32 | 47 / 17 | 117 / 119 | 116 / 118 | 1 / 1 | **0 / 0** | | | | as forced watch |
| forced ring-out everywhere (music under ring-out thresholds — contract violated), open | 61 | 38 | 117 | 117 | 0 | 119 | 10 | 18 | 3 | 52 / 501 / 1748 |
| forced ring-out, closed | 32 | 22 | 116 | 116 | 0 | 11 | 8 | 2 | 3 | 51 / 501 / 1748 |
| *baseline: current detector (C §6)* | 58 | 12 | 105 | 99 | 6 | 936 | | 21 | 143 | |

Forced-ring-out FPs are all near-sine programme (whistles S8c/X3 37, bells S24 20, organ/flute/sine X1/X2/X4/S8 23,
peak-hold S16 7, crowd 9, …): the ring-out prior/threshold assume the contract; with music playing the operator must
use watch (or `cfs` should detect programme at arm and say so — see §6 Q4).

Per scenario, native open loop (ev/TP/miss/FP over 3 seeds; latency = detection − first frame ≥ 12 dB prominent):

| scenario | ev | TP | miss | FP | early/tail | lat min/med/max ms | budget | verdict |
|---|---|---|---|---|---|---|---|---|
| C0 silent, C1 bed+drums, S1 bass (M7 replica), S4a/b vocal vibrato, S5 guitar→sine, S6 master ramp, S7 808, S8a organ, S8b flute, S8c whistle, S16 peak-hold, S17 kick, S18 edge vibrato, S19 driven room mode (ring-out), S20 song+crowd, S21 pad swell, S23a autogain, S23b +24 dB/clip, S24 bells, X1 organ held, X2 flute held, X3 whistle held, X4 sine lead, X5 808 40–60 Hz, X6 soprano edge, X15 kick+bass 55 Hz, X18 applause 30 s, X20 hum+whine+speech | 0 | – | – | **0 each** | 0/0 | – | 300 | PASS 3/3 (all 28) |
| S2a established 8k / S2b steep | 3 / 3 | 3 / 3 | 0 | 0 | | 48/48/52 | 300 | PASS |
| S2c established clipped 2.4k (+odd partials) | 3 | 3 | 0 | 0 | 1 early | −2/1/1 | 300 | PASS (0 HARM: harmonics suppressed) |
| S3 ring during music (crash at onset) | 3 | 3 | 0 | 0 | | 53/502/602 | 300 | FAIL 1/3 |
| S6b ring-out latent loop (probe) | 3 | 3 | 0 | 0 | | 51/52/300 | 300 | PASS |
| S9 clipped howl 115 dB/s | 3 | 3 | 0 | 0 | | 99/102/103 | 300 | PASS (0 HARM) |
| S10 ring between bands | 3 | 3 | 0 | 0 | | 148/200/200 | 300 | PASS |
| S11a two rings / S11b near-octave | 6 / 6 | 6 / 6 | 0 | 0 | | 1/77/351 · −2/100/152 | 300 | FAIL 2/3 (351) · PASS |
| S12 ac-guitar wedge 196 Hz (LF opt-in) | 3 | 3 | 0 | 0 | | 398/500/551 | 600 | PASS |
| S13 slow 3 dB/s @ 5 kHz + hats | 3 | 3 | 0 | 0 | | 0/549/1198 | 1000 | FAIL 2/3 |
| S14 masked by cymbal | 3 | 3 | 0 | 0 | | 150/198/200 | 300 | PASS |
| S15 decay 16 s tails (+ scripted cut) | 3 | 3 | 0 | 0 | 8 tail | 50/52/102 | 300 | PASS (baseline: 21 TAIL) |
| S22 speech ringing → feedback | 3 | 3 | 0 | 0 | | 2/102/202 | 300 | PASS |
| M1 loud band wedge 200 dB/s | 3 | 3 | 0 | 0 | | 49/52/102 | 300 | PASS |
| M2 quiet music ring-out two modes | 6 | 6 | 0 | 0 | 1 early | −1100/51/350 | 300 | FAIL 2/3 (350) |
| M3 jazz lav 416 Hz | 3 | 3 | 0 | 0 | | 101/350/602 | 300 | FAIL 1/3 |
| X7 plateaued −30 dBFS ring at arm under music | 3 | 3 | 0 | 0 | | 1603/5450/6452 | 1000 | FAIL 0/3 (§3) |
| X8 slow 1.4–5 dB/s under chords | 3 | 3 | 0 | 0 | | 103/801/2750 | 1000 | FAIL 2/3 |
| X9 525 Hz midpoint + speech | 3 | 3 | 0 | 0 | | 1200/1299/1898 | 300 | FAIL 0/3 (§3) |
| X10 two rings exact octave | 6 | 6 | 0 | 0 | | 99/125/148 | 300 | PASS |
| X11 amp-clipped howl −12 dBFS + H2..H5 | 3 | 3 | 0 | 0 | | 351/399/399 | 300 | FAIL (399) |
| X12a channel −20/+20 · X12b bus-master | 6 / 6 | 6 / 6 | 0 | 0 | | 98/126/201 · 100/125/151 | 300 | PASS · PASS |
| X13 decay-16 jazz · X14 peak-hold M1 | 3 / 3 | 3 / 3 | 0 | 0 | | 1/147/150 · 49/52/98 | 600/300 | PASS · PASS |
| X16 wedge 315 Hz loud band | 3 | 3 | 0 | 0 | | 50/203/402 | 300 | FAIL 2/3 (402) |
| X17 122 Hz ac-guitar body (LF opt-in) | 3 | 3 | 0 | 0 | | 350/549/601 | 600 | FAIL 2/3 (601) |
| X19 hand-held stalls + hops | 3 | 3 | 0 | 0 | | 51/248/252 | 300 | PASS |
| X21 reverberant slow ring + hop under choir | 3 | 2 | **1** | 0 | | 298/398/498 | 600 | FAIL 2/3 |
| X22 kick-mic sub ring 65 Hz (LF opt-in) | 3 | 3 | 0 | 0 | | 998/1148/1149 | 1000 | FAIL 1/3 |
| X23 ring-out quiet room two modes (probe) | 6 | 6 | 0 | 0 | 4 early | −1298/−401/150 | 300 | PASS (pre-runaway via probe) |

Latency distribution (native open, the 109 non-negative TP latencies): p10 48, p25 98, **p50 148**, p75 350, p90 998,
p95 1299, max 6452 ms; ≤ 300 ms: 79/109; ≤ 600: 94; ≤ 1000: 99. The 7 negative ones are probe/growth detections before
the single-band 12 dB crossing (ring-out modes found sub-threshold, X23/M2/S6b). Closed loop native: 121 cuts over 96
runs, 0 on programme, 0 on howl harmonics; S15's decaying display tail is never re-cut (TAIL 0 in closed loop).

Cost per frame (`cost_per_frame.json`, Python 3.13, this laptop, 100 bands): mean 160 µs (silent room, 8 tracked lines)
… 236 µs (bass + bed) … 530–545 µs (X7 dense music, 16 lines; S6b ring-out with probe); p95 ≤ 720 µs, max 800 µs.
O(bands·k + candidates·(k + partial checks)); no allocation growth (histories bounded at 24 frames).

---------------------------------------------------------------------------------------------------------------------

## 3. Failure analysis (what is missed or late, and whether it is the simulator or physics)

* **X7 — plateaued −30 dBFS ring present at arm, 15–25 dB prominent, under organ+piano+bass, watch mode: 1.6–6.5 s
  (budget 1 s).** Real limitation, and I claim irreducible at the stated cost ratio: with no onset, no growth, no clip,
  moderate level and prominence, the passive evidence (narrow .4 + family ≈ +0.2 (partials of the music coincide 20 % of
  frames) + stable .5 + steady .5 + duration .6 + dominant .9 − prior 1.0 ≈ 2.1) is *exactly* what a held organ/synth
  line under the same music shows (X1/X4 differ only by their observed instant onset, which X7 lacks by construction);
  what separates them is *outlasting programme structure* (+0.15…0.5 per boundary), which accrues as fast as the music
  changes. At 20:1 the detector waits for it; in ring-out mode the same line is emitted in ~300 ms (threshold 1.4), and an
  operator "cut it" affordance (or a one-shot −3 dB *probe cut* whose VERIFY outcome classifies it, [L §4.3 tier B]) is
  the right product answer. A 60 dB-prominent version (M7 itself, S2a/b) is caught in 48–150 ms via isolation evidence.
* **X9 — ring exactly between bands under continuous speech whose H4/H5 sweep it: 1.2–1.9 s.** Mostly simulator-hard by
  design: the speech is −30 dBFS with 32 partials and the mode is seeded by them, so for the first second the ring *is*
  a speech partial by every single-frame test; co-movement gating of `sub` and re-arm fix the lineage but the intruded
  ±1 cluster still costs growth linearity. Physically a real talker pauses; the corpus talker does not for 6 s.
* **X21 seed 2 miss; X8/S13 seed 3 late (2.7 s / 1.2 s); M3/S3/S11a/X16/X11 50–300 ms over budget.** Common cause: a
  broadband or partial-rich programme event coincides with the decisive frames (crash on the S3 onset at seed 1/2, hats on
  S13, chord partials inside X21's cluster, piano attacks in M3). Masked frames are not scored, so evidence resumes after
  the transient — correct but late. X11 (2 visible rising frames then a −12 dBFS plateau with H2–H5): 350–400 ms; the
  brief's own tier-A rule would want −10 dBFS. X16 402 ms on one seed: the vocal line a band away had to be re-armed.
* **X22 (65 Hz, opt-in) 1.0–1.15 s, X17 (122 Hz) 0.35–0.6 s.** Physics: τ_a ≈ 110 ms at 65 Hz forces ≥ 7-frame linear
  runs before growth there can be believed [A §1], and the bass/kick partial grid coincides with the ring's H2..H5.
* **Forced ring-out on music: 119 FP** — by construction of the cost model, not a defect; see Q4.
* Not observed but expected on hardware: `analyser_attack_k`, skirt order and the decay law are UNCERTAIN [A §1-2]; the
  design reads them from config and degrades (growth weight, frozen-display detection) rather than assuming them.

---------------------------------------------------------------------------------------------------------------------

## 4. Tests changed (tests/test_detector.py) and why — all 21 pass; rtasim sanity 21 pass; integration `-k "cfs or ring or watch"` 24 pass; full suite 935 pass (1 pre-existing env failure: `test_settings_defaults_and_env` asserts the checkout directory is named `x32-mcp`)

1. `test_config_from_descriptor`: kept the legacy `w_p + w_s < threshold` assertion (documents the old ceiling) and added
   assertions on the new keys' ordering and `with_mode()`; the CURRENT device.yaml (no new keys) loads with defaults.
2. `test_held_note_not_detected`: the fixture rose **linearly in dB at 80 dB/s for 250 ms with no partials, then sat
   dead flat at −14 dBFS** — an exponential amplitude envelope on a family-less, vibrato-less line that stops at a moderate
   level is a compressor-caught loop by every passive measure [L §1.3-1.4]; the test's own sibling
   (`test_plateau_and_transient_do_not_detect`) already says "a linear 20 dB/s crescendo … IS the ring signature". Changed
   the note to a physical onset (instant at 330 Hz, τ_a ≈ 15–45 ms [A §1]) with its H2/H3, plus a family-less instant
   note held 3 s; added `test_partialless_exponential_swell_is_a_ring` asserting the original envelope IS reported ≤ 6
   frames after crossing.
3. `test_two_rings_both_detected`: `0 ≤ i − t0 ≤ 6` → `−6 ≤ i − t0 ≤ 6`: the 630 Hz ring is emitted 3 frames *before*
   the single-band 12 dB crossing (cluster prominence leads by ~3 dB [C §2.11]); earlier is not wrong.
4. `test_ring_detected_with_noise`: the ±2 dB i.i.d. jitter was applied to the ring's own bin; a tone-dominated band
   fluctuates ≈ 0 dB [A §1] and the test's docstring says the *floor* is what is noisy — moved the jitter to the floor
   (±3.5 dB). Bound unchanged (≤ 12 frames).
5. `test_vibrato_rejected_with_longer_persistence`: asserted the old heuristic's known FP (vibrato detected at persistence
   3). A line hopping between adjacent bands at 5 Hz is FM = musical at any setting [A §4.4, L §2.3]; now asserts no
   detection for both configs, ring still caught ≤ 9 frames.
6. `_plateaued_ring` fixture (M7 regression tests): floor and ring were *exactly* constant frame after frame, which is what
   RTA peak-hold produces and what the detector must treat as a misconfigured analyser [A §2, §7 S16]; added ±1 dB floor /
   ±0.2 dB ring jitter (int16/256 quantisation, air movement). Assertions unchanged: the 60 dB ring is emitted with slope
   ≈ 0 and confidence ≥ threshold; the 15 dB one never.
7. `test_override_is_disabled_by_zero`: the legacy override key no longer gates anything (the plateau is reached on
   evidence); now asserts override 0 does NOT re-break the M7 case and that an emit threshold at the clamp ceiling turns
   the detector into a pure candidate publisher.

---------------------------------------------------------------------------------------------------------------------

## 5. Config / API added
`device.yaml detector:` (all optional, commented in the file): `track_prominence_db 6`, `emit_min_level_db −100`,
`coast_frames 3`, `f_low_watch_hz 160`, `f_low_ringout_hz 100`, `f_low_lf_optin_hz 40`, `f_high_hz 12500`,
`lf_penalty_per_third_oct 1.2`, `lf_strict_penalty 1.0`, `prior_llr_watch −1.0`, `prior_llr_ringout 0.0`,
`emit_llr_watch 3.0`, `emit_llr_ringout 1.4`, `dismiss_llr_watch −2.5`, `dismiss_llr_ringout −3.5`, `analyser_attack_k 0.5`,
`probe_settle_frames 3`, `probe_window_frames 8`, `probe_over_db 2.0`, `ev_growth_{multi,fast,medium,slow} 1.2/1.1/0.8/0.35`,
`iso_nats_per_db 0.08`, `iso_nats_max 2.4`, `harmonics_void_level_db −16` (+ uncommented dataclass fields `mode`,
`lf_feedback_possible`, `llr_floor/ceil`, `ref_band_lo/hi`, feature constants). Legacy keys parse; `min_level_db` is no
longer applied (documented in the yaml). API: `FeedbackDetector(cfg, band_hz, *, mode=None, lf_feedback_possible=None)`,
`cfg.with_mode(mode, lf_feedback_possible=)`, `det.note_gain_step(delta_db, ts)`, `det.note_cut(freq_hz, depth_db, ts)`,
`det.analyser_suspect`; `Detection.llr`, `Detection.reasons`; `Candidate.llr/state/terms/centroid` in `to_dict()`.
`cfs` should: pass `mode` and the LF opt-in at `_open_session`, call `note_gain_step` in `_run_ringout` right after
`_write_master`, call `note_cut` in `_notch`, and surface `analyser_suspect` + `terms` in the report/dashboard.

---------------------------------------------------------------------------------------------------------------------

## 6. The brief's four questions, as this design answers them

**Q1 — Can a harmonic test replace growth? Latency?** It replaces growth as the *music veto* (S1/S4/S5/S21/S22/X15/X20:
the 40–315 Hz onsets that produced 80 % of the baseline's 936 FPs are dismissed in 1–4 frames by family/sub, never by a
window), but it cannot *confirm* feedback: absence of partials is informative only above ~20 dB prominence [A §4.3] and
the near-sine programme set (organ, flute top, whistle, sine lead, 808, bells, hum) has no family either — those are
separated by onset shape, FM/steadiness, decay, LF prior and, in ring-out, the probe. Growth stays as positive evidence
(never required): it is what makes an emerging ring 50–250 ms instead of ~300 ms, and it is the only passive thing that
distinguishes a loop taking over a chord tone's band. Latency: single-frame terms (family, level, isolation) act on frame
1; stable/steady need 5 samples → an established 30+ dB ring is emitted 48–150 ms after arm; a growing ring 50–250 ms after
visibility when nothing masks it; LF and masked cases as in §3.

**Q2 — Weighted sum or predicates?** Neither: a sum of *log-likelihood ratios* with physically set magnitudes and hard
saturation, and thresholds set by costs. It differs from the old weighted sum in the two ways that matter — no term is
required (no unreachable region: a plateau reaches EMIT on isolation+family+stability, growth reaches it alone), and no
term can buy more than its physics allows (growth capped at 4.5, structure at ~2, so 30 dB of "prominence" cannot
outvote a visible harmonic family at −3 latched) — and from pure predicates in that weak cues combine (X11: 2-frame rise
+ −12 dBFS + odd-dominant partials + steadiness) and every emission carries `reasons` naming the terms that paid for it.

**Q3 — The 250 Hz–8 kHz window.** A legitimate prior for one rig (HPF'd dynamic vocal mic into tops) and wrong as a
constant [L §3.2-3.4]: kick/tom mics into subs ring at 40–120 Hz, acoustic pickups through wedges at 90–250 Hz, headset
condensers at 10–12 kHz. Implemented as prior odds, not a gate: −1.2 nats per ⅓-octave below f_low (160 Hz watch, 100 Hz
ring-out, 40 Hz with the operator's `lf_feedback_possible`, which also halves the family penalty below 160 Hz because the
bass partial grid fills that region) and above 12.5 kHz. On the corpus: 0 detections on S1/S7/X5/X15/S17/S19/S23 (the M7
40/80 Hz mechanism), while S12 (196 Hz), X17 (122 Hz) and X22 (65 Hz) are found with the opt-in. `cfs` should derive
f_low per session from the open channels' HPF (0.7·f_hpf) and mic kind as the loop brief proposes; the keys are there.

**Q4 — Behaviour when uncertain; watch vs ring-out.** Encoded in the thresholds (§1.4) and two mode rules. Watch: emit
at 20:1, otherwise publish the candidate with its evidence breakdown and keep watching — the human is the actuator for
the ambiguous middle (X7-type lines sit at llr ≈ 2 with `state='watching'` and a readable reason). Ring-out: emit at 4:1,
*and* an uncertain, not-loud, not-growing line is held until one probe evaluation exists, because the server's own next
+1 dB step is a free, decisive experiment [L §1.5, §4.3]; sub-threshold modes are found by the probe before they run away
(X23: 4 EARLY detections at −1.3 s, S6b/M2 0 FP on the organ). The forced-ring-out run (119 FP on music) is the
quantitative statement of why ring-out must not run over programme — `cfs` should measure spectral occupancy for 2 s at
arm and refuse/downgrade to watch thresholds when the "no programme" contract is visibly violated (the N-lines prior
adjustment is the soft version of that already in the detector).
