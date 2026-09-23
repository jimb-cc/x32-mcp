# Review request — desk measurements of 2026-09-22/23 and what they change

*For the frontier model working on the detector, the CFS² policy layer and the PEQ actuator. Written 2026-09-23 by the
independent reviewer after two studio sessions on X32RACK-Jim (FW 4.13). Everything below is measured, not modelled;
the raw frame logs are in `docs/research/data/` and the scripts that produced them are in `scripts/`. Full detail:
`docs/research/meters.md`, Verification log entries dated 2026-09-22 and 2026-09-23 (items 1–8).*

Repo state: `main` @ `1874799` carries every review branch (PRs #1–#13), the kill check and the measurements.
Open: **PR #14** `review/peq-sim` (bus-PEQ actuator in the simulator, K7/K8 hop scenarios) and **PR #15**
`review/rta-band-grid` (the band-centre correction below). On real sockets `main` runs 1108/1111; the three failures are
test-side datagram races (`HANDOVER.md` §4d).

Please review each item, comment where you disagree with the reading, and take the decisions marked **decide**.

---

## 1. The Dual Graphic EQ delivers about a third of its slider depth (highest consequence)

`meters.md` log 2026-09-23 item 7. GEQ2 in FX 5 (side A) inserted PRE on Main LR, RTA post-EQ, oscillator tone at ~2.0 kHz
(oscillator steps are semitones):

| 2k slider | attenuation at 2.00 kHz | at 1.88 kHz | at 2.11 kHz |
|---|---|---|---|
| −6 | **2.6 dB** | — | — |
| −12 | **4.1 dB** | 3.8 dB | 3.6 dB |

A broad, shallow dip: ±0.09 oct within 0.5 dB of the centre, and the centre itself at ~0.43× (−6) to ~0.35× (−12) of
nominal. The Main LR PEQ, by contrast, is the RBJ prototype to within 0.3 dB at Q 6.1 and Q 10, −6 and −12, at 2 kHz and
8 kHz (items 5 and 6) — full depth. Consequences:

* `NotchController`'s −3/−6/−9 ladder delivered ~1.3/2.6/3.9 dB on the desk. M7's 5 kHz howl "needing −9" needed ~4 dB.
* `decay_verify_db 6` cannot be met by one GEQ step on a limiter-held ring; a "held" verdict after a GEQ −3 is the normal
  outcome, not a policy signal.
* The corpus's closed loop writes the nominal depth with an RBJ Q 3 bell: every GEQ kill margin in `CORPUS.md` §6 and
  `DETECTOR.md` is ~2.5× optimistic (noted in `CORPUS.md` §7.8). The K series survives it (a −9 GEQ cut ≈ 3.5 dB real kills
  only e ≤ 3.5, and K1–K8 are killed by the PEQ path in PR #14).
* The PEQ's advantage is a factor ~2.5 in delivered attenuation per written dB, not the 30 % programme-cost figure of
  `PEQ_ACTUATOR_DESIGN.md` §2.2: a PEQ −3 kills what a GEQ −9 kills.

**Decide:** (a) a realised-depth factor (or a measured bell) for the GEQ in `render.py` / `bell_attenuation_db`, and what the
`note_cut` GEQ bracket should be; (b) the GEQ ladder and VERIFY thresholds — deepen faster, or accept that the GEQ is a
coarse tool and let the PEQ be the actuator; (c) whether Dual TruEQ (the band-interaction-corrected type) should be
measured before anything further assumes graphics behave; (d) how `PEQ_ACTUATOR_DESIGN.md` §2.2 should read.

## 2. PR #15 — the RTA band centres are `20 · 2^(i/10)` Hz

`meters.md` item 8 and the PR description. The desk's bins sit a constant **+0.034 oct (a third of a band)** above the DOC
p.19 table that `device.yaml` / `meters.py` reproduced: neighbour-symmetry estimator over 60 tones ≥ 300 Hz crosses zero at
+0.032 oct from the table centres and at −0.002 oct from `20·2^(i/10)`; a tone 0.012 oct below a table centre splits evenly
between two bands; 2000.0 Hz reads 5.7 dB louder in "band 66" than in "band 67". The PR changes the descriptor list, the
fallback formula and the constant-pinned tests. It does **not** re-tune 9 tests that move because the corpus is grid-relative
(every scenario frequency +2.4 %, new noise realisations): 4 in `test_detector_regressions` (X11 now caught 2/3 in plain
watch instead of 3/3 — the missed seed is the seed sensitivity the hold-out run already showed at 2/6; S13 1703 > 1600 ms;
the B2 and loud-show batteries) and 5 in `test_cfs_policy_desk` whose injected round-Hz tones now sit between bins
(`SyntheticRta` puts a tone into its nearest bin, so 1000 Hz lands 30 Hz below bin 56 instead of 15 Hz above bin 57).

**Decide:** confirm the reading; which X11 lane flips on the moved seed; re-pin the regressions or widen them; place the
policy-test tones on `rta_band_hz(d)` centres or give `SyntheticRta` the corpus analyser's two-bin split. Then merge.

## 3. Which RTA prefs reach `/meters/15` — and `decay` is an attack time too

`meters.md` items 1–3 (2026-09-23), scripted with `scripts/measure_rta_release.py`:

* `gain` (0 vs 18), `autogain` (ON 30 s) and `peakhold` (2, tone gated off): **no effect whatsoever** on the stream.
* `det`: levels equal within 0.7 dB, but the floor is **−97 under RMS and −128 under PEAK** (prominence ceilings 57 vs 60+).
* `decay`: release is linear in dB at **≈ 20 / decay dB/s** (80 at 0.25, 20 at 1, 5 at 4, 1.2 at 16 — the corpus law 60/decay is
  3× too fast) **and the attack follows it**: a 2 kHz tone reaches its plateau in 1 frame at 0.25, 3–4 frames at 1, and had not
  arrived after 4 s at 16.

Consequences: the arm-time forcing of gain/autogain/peak-hold is cosmetic; `PEAK_HOLD_SUSPECTED` / `FROZEN_LINES` can never
fire from the stream; the `peak_hold_s` and `gain_offset_db` corpus sweeps model a screen effect; forcing `decay 0.25` at arm
is the one pref write that changes what the detector sees, and the "1 frame above 300 Hz" settle assumption holds only there.

**Decide:** retire or re-scope the frozen-display machinery; state the decay dependency in `DETECTOR.md`; drop the two
sweeps from the gate battery in the next corpus revision.

## 4. The analyser model: settle rule confirmed, skirts frequency-dependent, response flat

`meters.md` items 4 and 8, `scripts/log_rta_frames.py`, `scripts/measure_rta_bands.py`:

* Gated-tone rise (RMS, decay 0.25): 78 Hz five frames (13, 5, 6, 2, 1.5 dB/frame — the M7 "growth" mechanism, measured),
  156 Hz three, ≥ 947 Hz one. The settle rule with **k = 1.0** holds frame for frame; the simulator's own τ_a = 0.5/Δf is too
  fast at LF. Under PEAK the last decibel takes longer (3 frames to −3 dB at HF, 6–7 to −1 dB).
* Skirts: ≈ order 5 (−30/−55 at ±1/±2 bands, symmetric) above ~200 Hz; ≈ 3 at 100 Hz; ≈ 2 at 50 Hz (−12/−25), asymmetric.
  The corpus's uniform `skirt_order 3` is wrong in both directions.
* Response flat: −37.6 ± 0.2 dB from 42 Hz to 9.5 kHz for a −40 dB tone; at gain 0 the stream reads true dBFS.

**Decide:** when to cut the corpus revision (new §6 baselines) with attack_k 1.0, a frequency-dependent skirt order, the
measured release law and the corrected grid — and whether the pre-registered gate's G2 sweep set changes with it.

## 5. PR #14 — the PEQ actuator in the simulator, and the K8 hop gap

`PeqPlanner` (a stand-in for `PeqNotchController`), `--actuator=peq` in the harness and the gate, K7/K8. On the final detector
both actuators kill K1–K7, X9 and S10; the PEQ lands where the ring is and kills with more margin. **K8 survives 3/3 under
both actuators**: after the +200-cent hop the detector emits nothing more (one TP at 3.4 s, silence for 9 s), so no second
band ever opens. X19 cannot show this because it is scored on first detection only.

**Decide:** the hop-after-cut behaviour (re-born track must re-earn evidence — intended? then the policy layer needs a
"cut line moved" rule), and merge #14 as the harness for the PEQ product PRs (design §10, PR1→PR6).

## 6. Housekeeping

* Two real-socket test races remain: `test_cfs_policy_breakers.py::test_e_tier_b_never_writes_a_band_shallower…` (line 303
  waits for the writer to *record* the −3, line 304 sets the fake to −9 while that datagram is in flight — wait for
  `rig.fake.value(PAR_1K) == −3.0` first) and `test_cfs_policy_desk.py::test_colour_the_engineer_sets_mid_session…` (same
  pattern with the `RDi` alert write). `…cut_within_k1` passed on the last two runs; its 0.7 s budget is marginal on real sockets.
* Oscillator facts for the desk-test recipes (`DETECTOR.md` §6, `PEQ_ACTUATOR_DESIGN.md` §10): into a **bus** it replaces the
  bus output downstream of the meter and RTA tap (nothing reads it); into **Main** it enters before the EQ; whether it enters
  before or after the insert send is untested. The bus-EQ tap-order check (design §9 item 2) still needs a channel-fed tone.
* The desk's RTA prefs before all this: gain +18, autogain off, RMS, decay 1 — i.e. every M7 absolute level was true dBFS but
  every M7 attack was 3–4 frames slower than the detector now assumes.
