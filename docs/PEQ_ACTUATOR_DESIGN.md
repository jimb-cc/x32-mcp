# Design: the bus PEQ as the CFS² notch actuator

*Status: design, not implemented. Produced 2026-09-22 after the review, from the engineer's statement that he applies no EQ to
the mix buses beyond an occasional HPF (so any non-HPF band in a bus PEQ was not put there by him) and the ProSoundWeb
consensus that experienced X32 engineers ring out with the bus's 6-band parametric, not a graphic. Two independent designs
(physics-first, product-first) were written, each attacked by an independent critic, and synthesised here; Appendix A records how
every critique finding was resolved. All existing-code citations are to branch `review/detector-cfs`. Line counts are estimates.*


Worktree `review/detector-cfs` @ 1d7c4fd; all `file:line` refer to it except `docs/REVIEW_REPORT.md` (main repo). Numbers come from
`reports/peq-actuator/synth/probe.py` (run `PYTHONPATH=src:tests`), which first asserts `FeedbackDetector.bell_attenuation_db`
(src/x32mcp/detector.py:807-817) ≡ the corpus's `peaking_gain_db` (tests/rtasim/physics.py:54-64) to 1e-9: one RBJ prototype serves
the desk-side verdict and the simulator. The X32's own PEQ shape is UNCONFIRMED (§9); everything below brackets it rather than assumes it.
The engineer's statement (this task's user message): no EQ on the mix buses except sometimes an HPF, which he does not always remember;
the mains he EQs lightly; anything else found in a bus PEQ was not put there on purpose.

## 0. Decisions

| # | Decision | Source |
|---|---|---|
| D1 | `PeqNotchController` beside `NotchController`, same public surface, per-notch `f`/`q`, octave merge; `_Session.nc` typed as a `NotchPlanner` Protocol | A §1, critic-B flaw 4 |
| D2 | Writer contract `write(plan)`; `NotchPlan`/`Notch` carry `actuator, notch_hz, q, type_before, f_before, q_before, opens` | B §1, critic-A flaw 4 |
| D3 | Q 6 (grid 6.103) first, −3 dB steps, ladder −3→−6→−9→−12, per-actuator cap `peq_notch_max_db −12` wired in controller, policy and tier-B | A §2.4, B D2, critic-B flaw 8 |
| D4 | `note_cut(q=, offset_unc_oct=, key=, bell=)`; bracket = corners over offset ±u **and** Q-scale 0.8..1.25, `sorted()` idiom (never A's inverted form) | A §2.5, critic-A flaw 1 |
| D5 | Buses only; Main LR stays GEQ (Tier 2 `/main/*/eq/*`, device.yaml:262-266; `Desk._write` refuses unguarded, desk.py:956-957; `set_eq_band` never passes `guarded`, desk.py:1147-1178) | both |
| D6 | `detector.actuator: geq` ships first (byte-identical behaviour); `auto`/`peq` opt-in per call; default flips to `auto` (PEQ on buses with ≥ 1 free band, GEQ fallback) only after desk test (b) shows the bus EQ at the post-EQ tap; a first-notch visibility interlock stays either way | critic-B flaw 9 |
| D7 | eq/on: OFF at arm + any band with g ≠ 0 → PEQ path blocked (never a warning); ON written as the first datagram of the first notch; OFF push aborts unconditionally; restored OFF iff `eq_on_before` False and no notch committed | critic-A flaw 2, critic-B flaw 2, B D5 |
| D8 | All six bands observed from pushes regardless of ownership + one re-read of the band before its first write | critic-B flaw 1 |
| D9 | Notches left in place, recorded; release only through `release_cfs_notches` with an identity check (slice 2) | B §7, critic-A flaw 3 |
| D10 | Engagements, ignore list, `at_max` keyed `(actuator, band)` and per-actuator cap from slice 1 | critic-A flaw 5 |

## 1. Actuator model (brief item 1)

**Why a second controller.** `NotchPlan`/`Notch` are band-centric — `freq_hz` is `geq_band_hz[band-1]` (detector.py:2459-2465,
:2532, :2547), `propose` merges by band-index distance ≤ `merge_adjacent_bands` 1 (:2494-2501) and clamps to `cfg.notch_max_db` (:2507;
`_can_deepen` :2475-2476) — so B's "keep the algorithm, take a BandMap" would deepen band 2's 2331 Hz notch for a 500 Hz ring allocated
to band 3. **`PeqNotchController`** keeps the surface cfs.py uses (`propose/commit/observe/notches/gains/touched_bands/budget_left/
spent/band_for_freq`; call sites cfs.py:1351, 1382, 1884, 1904, 1906, 2046, 2067-2072, 2689-2690, 2871-2872) plus `max_db` and `owned`;
`band_for_freq(hz)` = the owned notch within `peq_merge_oct`, else the next free band (possibly unallocated, as cfs.py:1884 already
tolerates); `propose` returns None with a `propose_reason` ("no_band" | "at_max") so slice-2 overflow falls to the GEQ only on the
former. `NotchPlan` (frozen, :2409-2427) and `Notch` (:2366-2388) gain defaulted `actuator "geq"`, `notch_hz`, `q`, `type_before`,
`f_before`, `q_before` (emitted by `to_dict`); for the PEQ `freq_hz == notch_hz`, so every `freq_hz` consumer keeps working.
`GeqWriter.set_band_gain` (:2391-2394) becomes `NotchWriter.write(plan)`; `_DeskGeqWriter.write` wraps today's `set_band_gain`
(cfs.py:573-580); `RecordingGeqWriter` (:2397-2407) records plans.

**Allocation at arm** from `Desk.get_eq(t)` (desk.py:558-568, decoded by `_eq_dict` :877-891). g is "flat" iff `|g| < 0.125` (half a 0.25
grid step, device.yaml:71).

| band at arm (bus) | class | session may |
|---|---|---|
| type PEQ/VEQ, g flat | **free** | allocate: retype VEQ→PEQ, write f/Q, then g |
| type LShv/HShv, g flat | **reclaimable** (buses only, `peq_reclaim_flat_shelves: true`) | allocate; `type_before` recorded; warning "band 6 was a 0 dB HShv" (his words make a flat bus shelf unintentional) |
| type LCut/HCut, any g, any f | **engineer's HPF/LPF** | never touched; preflight "HPF 120 Hz (band 1) kept". The real scene has `/bus/01/eq/6 HCut 20k00 -0.50 2.0` (scales_params.md:468, :579): a cut filter parked at the rail is inert but stays reserved in slice 1 (the rule "your cut filters are never touched" must be simple); reported as `inert_cut_filter`, reclaimable in slice 2 behind `peq_reclaim_rail_cuts` |
| g > +0.125, any type | **engineer's boost** | never touched; warning; with eq/on OFF it blocks the PEQ path (§4.2) |
| g < −0.125, type PEQ/VEQ/LShv/HShv, matches a row of the latest report for this bus (`actuator peq`, same band, type PEQ, \|Δf\| ≤ 1 grid step 0.05 oct, Q within 1 grid step ×1.05, \|Δg\| < 0.125) | **pre-existing CFS² notch** | adopted as `existing` (`session_id ""`, `detections 0`, the pattern of detector.py:2459-2465); deepenable; charged to the budget only when touched |
| g < −0.125, no row matches (any type) | **unexpected cut** | never allocated, never deepened; warning "band 3 carries −4.5 dB at 1.2 kHz not from a CFS² report — left alone"; counts against the six, not the budget |
| Main LR | every band reserved | GEQ path (D5) |

Matching is **per row** for the PEQ (a row the engineer has since flattened is simply not ours any more; the others still are) — a
deliberate change from `_matched_session`'s all-or-nothing (provision.py:373-397, :392-393), which keeps its meaning for
`GeqStatus.matched_session`; `PeqStatus.matched_session` is filled only when every row matches. Allocation order: lowest free band
first, **band 1 last** (the desk's HPF slot; on a bus with no HPF a later hand-made LCut lands on an empty band).

**Budget.** One counter across actuators (`_Session.budget` cfs.py:603, `budget_left` detector.py:2573-2575): distinct notches this
session, deepening free (as :2478-2479). `peq_notch_budget_default: 4` on the PEQ path (his "> ~6 → start over"; 4 leaves two bands),
`notch_budget_default` 6 unchanged for the GEQ; hard ceiling `min(budget, free)`. The server tools' `notch_budget: int = 6`
(server.py:1867, :1952, :2045) becomes `None` → cfs picks per actuator; an explicit value is honoured up to the ceiling.

**Merge.** A detection within `peq_merge_oct` **0.08** of an owned notch deepens it, else a new band opens. Any value in
[0.055, 0.12] is defensible — ≥ u + grid (0.03 + 0.025) so a stationary line always deepens its own notch instead of opening a second
band on centroid jitter, ≤ the Q 6 half-depth half-width 0.12 so the deepened notch still reaches the line (§2.1). 0.08 makes B's
hop rule ("> 0.08 oct → second notch") the same rule, removing the overlap critic-B found between B's 2a and the merge.

**Session end.** Notches stay, exactly as GEQ cuts do (`_finish` cfs.py:1208-1250 restores the strip colour and RTA prefs, never
the actuator; `mine`/`pre` cfs.py:2871-2872, :2896), recorded with `actuator, band, notch_hz, q, depth_db, type_before, f_before,
q_before, eq_on_before, session_id, ts, detections, tiers`. Types are never restored (the notch needs PEQ). `eq/on` per D7 (§4.2).

## 2. Notch shape and depth policy (item 2)

### 2.1 The bell
RBJ analog prototype (detector.py:816). The "Q" bandwidth Δf/f = 1/Q is the width between the **half-depth** points (|H| = G/2 dB
exactly at w − 1/w = ±1/Q; probe T8: 3.00 dB at the edge of a −6 notch for every Q) = **1.443/Q oct: Q 6 → 0.240, Q 8 → 0.180,
Q 10 → 0.144** — the brief's "1/11–1/14 oct" is wrong in the safe direction. Attenuation at the line vs centring error (dB, probe T1):

| depth | Q | 0 | 0.03 | 0.055 | 0.10 | 0.167 |
|---|---|---|---|---|---|---|
| −3 | 4 | 3.00 | 2.92 | 2.74 | 2.29 | 1.61 |
| −3 | **6** | 3.00 | **2.82** | **2.48** | 1.77 | 1.03 |
| −3 | 8 | 3.00 | 2.70 | 2.18 | 1.35 | 0.68 |
| −3 | 10 | 3.00 | 2.55 | 1.89 | 1.03 | 0.48 |
| −6 | 4 | 6.00 | 5.83 | 5.46 | 4.54 | 3.21 |
| −6 | **6** | 6.00 | **5.62** | **4.92** | 3.52 | 2.07 |
| −6 | 10 | 6.00 | 5.07 | 3.77 | 2.08 | 0.98 |
| −9 | **6** | 9.00 | 8.39 | 7.30 | 5.25 | 3.17 |
| −9 | 10 | 9.00 | 7.53 | 5.60 | 3.18 | 1.55 |
| −12 | **6** | 12.00 | 11.11 | 9.59 | 6.95 | 4.32 |
| −12 | 10 | 12.00 | 9.91 | 7.39 | 4.34 | 2.21 |

The detector's centroid error ±0.03 oct (docs/DETECTOR.md:231) costs ≤ 7.5 % of the depth at Q 6 (6.0/6.3/6.7/7.5 % at −3/−6/−9/−12).
The desk's frequency grid (`freq: log 20..20000 steps 201`, device.yaml:73; 0.0498 oct/step, ±0.025) is a **known offset**, not
uncertainty: 2331 → 2349.8 Hz (+0.012 oct), 525.4 → 532.1 (+0.018), 2588 → 2606.3 (+0.010), 1788.9 → 1782.5 (−0.005). Q grid
(device.yaml:75, raw decreasing, ×1.0506/step; scales_params.md:448-472): 6 → 6.103, 4 → 3.913, 8 → 7.812, 10 → 10.0. Worst-case miss
= 0.055 oct: Q 6 keeps 82 % of a −3, Q 10 63 %.

### 2.2 Programme cost (∫(1 − 10^(−att/10)) d log₂f over ±1.5 oct, probe T2)

| actuator | −3 | −6 | −9 | −12 | −15 |
|---|---|---|---|---|---|
| GEQ Q 2 | 0.533 | 0.913 | 1.206 | 1.453 | 1.672 |
| GEQ Q 3 (corpus, physics.py:51) | 0.383 | 0.664 | 0.891 | 1.092 | 1.281 |
| GEQ Q 4.3 (true 1/3 oct) | 0.280 | 0.489 | 0.663 | 0.821 | 0.976 |
| PEQ Q 4 | 0.298 | 0.521 | 0.705 | 0.871 | 1.033 |
| **PEQ Q 6** | **0.207** | **0.364** | **0.496** | **0.619** | 0.742 |
| PEQ Q 8 | 0.158 | 0.279 | 0.382 | 0.479 | 0.578 |
| PEQ Q 10 | 0.128 | 0.226 | 0.311 | 0.391 | 0.473 |

The GEQ's Q is UNCONFIRMED between 2 and 4.3 (device.yaml:950-951, detector.py:331-332), so the honest comparison is a bracket: a
Q 6 −3 notch (0.207) costs less than one GEQ step at **every** Q guess (0.280–0.533); a Q 6 −6 (0.364) costs less than a GEQ −3 at
Q 2–3 but **30 % more** than at Q 4.3 (0.280); the PEQ's −12 (0.619) is under the GEQ's own −9 at every guess (0.663–1.206) and
under its −6 at Q 2–3. Collateral 1/6 oct away (probe T7): PEQ Q 6 −12 = 4.33 dB vs GEQ −9 = 7.27 / 5.96 / 4.52 dB at Q 2 / 3 / 4.3 —
the −12 cap is justified on the *margin* (§2.3), not on the Q 2 end of the bracket.

### 2.3 Q and depth (decisions)
* **Q 6 (6.103) for every first notch, on every bus, at every frequency** — the prototype is scale-free in octaves and the error budget
  is in octaves. Q 8–10 buys 25–40 % less cost but loses 0.5–1.1 dB of kill at the error budget and widens the verdict bracket 2–4×;
  Q 4 buys 0.3–0.5 dB (2.74 vs 2.48 at −3; 5.46 vs 4.92 at −6) for 43 % more cost. One `if`: a candidate whose centroid sd over
  `Candidate.hist` exceeds 0.25 band (0.025 oct; a wandering hand-held ring) opens at Q 4 (3.913). Q never increases on a live notch.
* **−3 dB steps, ladder −3 → −6 → −9 → −12**, verdict-gated by the existing machinery (deepen right detector.py:936-944,
  `tier_b_insufficient` cfs.py:2006-2013, `tier_b_held` after `held_deepen_s` 3 s cfs.py:2022-2051, ring_out VERIFY cfs.py:2490-2510).
  Not −6 first: a ring cut before it plateaus has e < 3 dB (DETECTOR.md:217-221), so −3 at Q 6 kills the common case for 0.207/oct and
  a wrong cut costs 0.207 instead of 0.364. `peq_first_step_loud_db: −6` for LOUD/FAST-RISE emissions is a slice-2 option (off).
* **Cap −12 for the PEQ** (`peq_notch_max_db`), in slice 1. Reason (probe T4): on the on-centre K scenes the −9 kill margin shrinks from
  the GEQ's +2.0 (K2, e 7) / +2.5 (K4, e 6.5) to +1.39 / +1.89 at the ±0.03 budget and **+0.30 / +0.80 at the 0.055 worst case**; −12
  restores +2.59 / +3.09. −12 at Q 6 removes less programme than the GEQ policy already allows (§2.2); −15 buys 3 dB that his rule
  says should come from placement. Wired in all three readers of the cap: `NotchController._can_deepen`/`propose`
  (detector.py:2475-2476, :2507 → `self.max_db`), `Policy.validate_notch(current, new, limit_db=None)` (policy.py:442-459; today
  `self.notch_max_db` from `d.detector["notch_max_db"]`, :214), and tier-B `at_max` (cfs.py:1904, :2046, :2070; ring-out abort text :2421-2422).

### 2.4 Verdict semantics under a narrow notch — the `note_cut()` change
Today `note_cut` brackets the expected drop over Q ∈ [`geq_q_min`, `geq_q_max`] via `sorted()` (detector.py:869-870), keys `_cut_depth`
by semitone (:856-859: 2350 and 2450 Hz both key to 83 — probe T6) and skips lines > 1/3 oct away (:865).

```python
def note_cut(self, freq_hz=None, depth_db=0.0, ts=None, *, band=None, step_db=None,
             q: float | None = None,                # the notch's Q as written (grid); None = GEQ semantics, unchanged
             offset_unc_oct: float | None = None,   # u; default cfg.peq_offset_unc_oct 0.03
             key: Hashable | None = None,           # _cut_depth identity, e.g. ("peq", bus, band); None = semitone bucket as today
             bell: Callable[[float, float], tuple[float, float]] | None = None) -> None  # measured shape: bell(depth, off) -> (lo, hi)
```
With `q`: for each candidate with |δ| = |log₂(c.freq_hz / freq_hz)| ≤ `peq_reach_oct` 0.5 (the `hi < 0.5 dB → skip` filter at :873
still prunes), `qs = (q·peq_q_scale_min, q, q·peq_q_scale_max)` (0.8 / 1.25 until desk test (a) sets them),
`att_lo = min(bell(depth, |δ|+u, qq) for qq in qs)`, `att_hi = max(bell(depth, max(0,|δ|−u), qq) for qq in qs)`, likewise for
`depth − step`; `lo`/`hi` as at :871-872. The minimum is at the **narrowest** Q (a narrower bell attenuates less off centre); A evaluated `att_lo` at
the wider Q and got a bound that is not one (probe T3, −9 with the grid offset: A's lo 7.78 vs true 6.58 — a desk notch 25 % narrower
than RBJ, line 0.055 off, drops 6.64 and A files the spurious 'insufficient' its own §2.5 said `q=` prevents). Brackets (probe T3, u 0.03):

| depth | δ known 0 | δ known 0.025 (grid) | scale 1.0 only (after desk test) |
|---|---|---|---|
| −3 | [2.72, 3.00] | [2.24, 3.00] | [2.46, 3.00] |
| −6 | [5.42, 6.00] | [4.44, 6.00] | [4.89, 6.00] |
| −9 | [8.07, 9.00] | [6.58, 9.00] | [7.25, 9.00] |
| −12 | [10.64, 12.00] | [8.65, 12.00] | [9.53, 12.00] |

With `cut_false_tol_db` 1.0 (device.yaml:945) the 'held' window is [lo−1, hi+1]: at −9 with the grid offset a desk Q of 8 or 10 (6.42 /
5.60 dB) still reads 'held', never a false 'insufficient'. The price of the Q-scale corners is a wider window until measured: a
limiter-held howl with e > att reads 'held' and is deepened after `held_deepen_s` (3 s) instead of immediately — slower, never
wrong; after desk test (a) the scale collapses to the right-hand column. **confirmed / held / false_cut / ambiguous keep their
definitions and thresholds** (detector.py:898-920). `insufficient` gains a sub-cause in slice 2: the controller re-reads the
candidate's centroid; if |log₂(f_line/f_notch)| > u + grid 0.055 the notch *missed* → `reason "off_centre"` → a second notch at the fresh
centroid, not a deeper one. `_cut_depth[key]` replaces the semitone bucket when `key` is given (cfs passes `key=("peq", bus, band)` and
`step_db=plan.new_db − plan.current_db` explicitly). `cut_log` rows (:947-949) gain `actuator`, `q`. GEQ calls are byte-identical.

### 2.5 Deepen vs widen vs second notch (line 0.055 oct off a Q 6 −3 notch; probe T5)

| action | att at the line | removed/oct |
|---|---|---|
| deepen → −6 | 4.92 (+2.44) | 0.364 |
| widen → Q 4, −3 | 2.74 (+0.26) | 0.298 |
| move f to the fresh centroid, −3 | 3.00 (+0.52) | 0.207 (un-cuts the old f) |
| second notch at the fresh centroid, −3 | 2.48 + 3.00 in series | 0.414 |
| deepen → −9 | 7.30 (+4.82) | 0.496 |

Deepening dominates while the line is inside the error budget; a move (B's 2a) gains 0.3–0.6 dB, fires inside the ±0.055 noise band,
un-cuts the old frequency and restarts the verdict; widening (B's 2c) gains 0.16–0.30 dB. Ladder: (1) **deepen** on `insufficient`/`held`
(existing rules); (2) **second notch** when the re-measured centroid is > 0.055 oct off (`off_centre`) or a re-born track appears >
`peq_merge_oct` away — a hop (X19 +125 c, scenarios.py:780; X21 −130 c, :806): a Q 6 −6 notch still gives 4.0 dB at 100 c and 2.1 dB
at 200 c (GEQ Q 3: 5.3 / 4.0), so a 100-cent hop is usually still dead and a 200-cent hop needs a second band under either actuator;
(3) **widen** only at the cap with the line inside the notch, opt-in `peq_widen_at_max` (Q 6.10 → 4.54 → 3.37 on the grid, never
narrower); beyond that his rule applies (placement). B's rules 2a/2c are dropped; 2d survives as (2).

## 3. Interplay with the GEQ path (item 3)

| case | actuator | notes |
|---|---|---|
| bus, `actuator auto`, ≥ 1 free band | PEQ (warning when free < 2) | a GEQ insert, if present, is validated and reported, not written; `validate_ringout_eqs` failures (provision.py:401-476, "no FX insert" :428) become warnings, not blockers (:752-753) |
| bus, `auto`, 0 free | GEQ, or preflight blocker with the bands named | |
| bus, `peq` explicit, 0 free | blocker | never silently GEQ |
| bus, `geq` explicit | GEQ | today, byte for byte (slice-1 default, D6) |
| bus, PEQ bands exhausted mid-session | slice 2: fall to the GEQ controller under the one budget counter | only on `propose_reason == "no_band"`, never on "at_max" (that stays a deepen refusal) |
| Main LR | GEQ, both sides (`geq_sides_for` provision.py:346-351) | PEQ on Main only via `ring_out`'s Tier-2 confirmation (server.py:1984-2000 binds the payload) — slice 3; the "< 2 free → GEQ2 pair" rule lives there |

One actuator per session in slice 1. Slice 2: `_Session` holds `nc_peq/writer_peq` and optional `nc_geq/writer_geq`; `_notch` asks
the primary, falls to the secondary; `state.notches` (cfs.py:744) and the report concatenate both; engagements/ignore/at_max keyed
`(actuator, band)` (D10). **Bell per actuator in `note_cut`:** GEQ → the Q bracket as today; PEQ → §2.4. **Existing GEQ cuts** from
earlier sessions coexist untouched as `existing_cuts` (cfs.py:1038 builds `existing` from `pf.geq.bands_db`, :2872 reports them): they are
static and cancel out of every change-based verdict; the RTA sees the product of the PRE insert and the bus EQ.

## 4. Safety (item 4)

**4.1 Writer discipline.** Tier 1 with the ±15 clamp (device.yaml:207-212, `clamp_eq_gain` policy.py:291-299) is not cuts-only; the
writer enforces it as `_DeskGeqWriter` does (cfs.py:576): `_DeskPeqWriter.write(plan)` calls `validate_notch(self.gains[band],
plan.new_db, limit_db=peq_notch_max_db)` before every g write; `type`/`f`/`q` are written **only when the band opens** (g_cur flat) and
only on a band classified free/reclaimable at arm and still so (D8) — any other band raises `CfsError("BAD_ARGUMENT")` before a
datagram; `Q_new ≤ Q_cur` (compare in Q: the raw grid is decreasing, device.yaml:75). **Write order:** `set_eq_band` writes on → type
→ f → g → q (desk.py:1160-1175), so a one-call open lands g before q and the notch sits at the band's previous Q (2.0 on FakeDesk,
fakedesk.py:122; whatever a reclaimed shelf held) until the q datagram — the hazard is ordering on the wire, not a "20 ms slot": the
limiter is a token bucket with capacity 50 (policy.py:219-221, :412-433) and a 5-datagram open leaves back-to-back. Open in **two
calls**: `set_eq_band(type="PEQ", freq_hz=, q=)` then `set_eq_band(gain_db=)`; deepen = `gain_db` only. First notch = 4 datagrams (5 with
eq/on); the desk's per-datagram apply time is UNCONFIRMED (desk test 4′); no pre-positioning of unused bands. `applied` (desk.py:978-979,
:1178) is the **grid-quantised request**, not a read-back (the X32 does not ack SETs); the writer records `notch_hz`/`q` from it.
**Partial open:** if call 2 raises or is cancelled after call 1 landed, `_notch` does not commit (cfs.py:1362-1382) but the desk holds a
retyped, moved band: the writer restores `(type_before, f_before, q_before)` best-effort under the same exception handling, and either
way the band goes on `report["orphaned_bands"]` and is not reused this session without a re-read. `_notch` gets an `abort_reason` check
at its top (the watch consumer at cfs.py:1299-1300 has none; `_abort_from_callback` only schedules `stop()`, :2650-2663) and on a PEQ
failure invalidates `f"{osc_prefix}/eq"` instead of `/fx/{fx_slot}` (:1363).

**4.2 eq/on.** Read at arm. OFF at arm: the PEQ path is eligible only if every band is LCut/HCut or flat; **any band with g ≠ 0 under a
bypassed EQ is a preflight blocker for the PEQ** (auto → GEQ, peq → refuse, band named) — a +6 dB HShv parked under a bypassed EQ
would go live the moment we wrote eq/on ON (A filed this as a warning; it is a boost by our action). When eligible, eq/on ON is the
first datagram of the first notch (`eq_on_before: false` recorded); switching it on also enables his LCut, which is reported ("turning
Bus 3 EQ on also enables its LCut 120 Hz") — it is his filter. `eq/on` is tracked live from pushes for the **whole** session: an OFF
push aborts unconditionally (the same reading as the insert change, cfs.py:2630-2631; a notch under a bypassed EQ is invisible at the
tap and the verdict chain would walk lines to −12), and the writer refuses a g write while its eq/on belief is OFF except on the
turn-on path. Restore: OFF in `_finish` iff `eq_on_before is False and not mine`; otherwise leave ON and warn ("EQ switched ON by this
session; its notches need it"). No eq/on write on Main LR ever.

**4.3 Pushes** (`_on_desk_push` cfs.py:2620-2648 sees every `/xremote` address, subscribed at :1141). A `Desk.eq_from_push(address,
args) -> (target, band | None, field, value)` beside `geq_par_from_push` (desk.py:754-778) decodes `/bus/NN/eq/on` and
`/bus/NN/eq/B/{type,f,g,q}` through the descriptor's `ParamSpec.to_value`. Rules: **observe all six bands regardless of ownership** (as
`_on_geq_push` observes every band of our side, :2688-2690): a push to a not-yet-owned band re-classifies it out of the free pool and is
reported — closing the stale-pool hazard where an engineer's mid-session −6 at 3 kHz on a "free" band would be retyped, moved and
written −3 (a +3 dB boost at his frequency; the case provision.py:457-462 guards on the GEQ). Because push delivery with many clients is
UNCONFIRMED (REVIEW_REPORT §8 item 6), `peq_reread_before_open: true` adds one invalidated `/node` read of `/bus/NN/eq/B` before a band's
first write, refused unless still as classified (one RTT on the first-notch path only; measured in desk test 4′). Echo guard: a pushed
`(type, f, q, g)` equal to the writer's last-written values files no `cfs.peq_external` (as :2691-2692 uses ≥ 0.25 dB); whether the desk
echoes our writes is desk test 3‴. Owned band: g deeper → adopt (`observe`); g shallower by ≥ `_GEQ_RELEASE_DB` 0.2 (cfs.py:122) → the
release path (`_on_geq_release` :2698-2720 generalised to `(actuator, band)`: engagement ends `operator`, band ignore-listed, leaves
`owned`); f/q/type changed → released and reported ("band 4 moved by hand 2350 → 1800 Hz"); a scene recall is many releases, reported.
`owned` is populated at **commit**, never at propose.

**4.4 Panic, snapshot, undo.** No panic interplay (a notch is not a level; `_on_panic_event` cfs.py:2607-2618 aborts as now). The
pre-write snapshot is taken at arm (cfs.py:1113 → `ensure_pre_write_snapshot` desk.py:925-940 → `dump` :700) and the bus dump
sections include `eq` and `eq/{band}` 1..6 (device.yaml:641-642) — verified; `restore_snapshot(id, scope="bus.3")` (server.py:1386 →
`Desk.restore` desk.py:1573-1611) resolves the scope through `_scope_prefixes` (nodes.py:674-699, `parse_target` at :697) and writes the
EQ sections back: the undo exists. `release_cfs_notches(bus, session_id=None)` (slice 2, Tier 1): writes g = 0 **only** to bands
whose current `(type, f, q, g)` still match that session's report row within one grid step, through a `validate_release(current,
row)` (not by bypassing `validate_notch`); any other band is skipped and reported; refused in show mode; Main LR excluded;
`released_by` recorded. Writing 0 by report row without the identity check would be a +6 dB step over a cut the engineer has since
made his own — the one write class the policy forbids.

**4.5 Preflight** (provision.py:737-780) adds on the PEQ path: `eq_on`; the band table with classes; `free_bands`; `hpf: {band, f}`
kept (LF edge unchanged: it derives from the *channel* HPFs, cfs.py:1050/:1069 — a bus HPF is downstream of the mics and says nothing
about the loop); **no LCut on a monitor bus → warning** "no HPF on Bus 3: consider LCut 100–120 Hz (band 1 is left free for it)", never an
action (his own words: he does not always remember); the unexpected-cut and boost warnings; the OFF-with-non-flat-band blocker; `eq`
sections unreadable → blocker (the same reason `bands is None` blocks the GEQ, :457-462); `actuator` chosen. `PeqStatus` beside
`GeqStatus` (:151-175) → `{eq_on, bands[...], free, reclaimable, engineer, unexpected, matched_session}`; `Preflight` (:263-296) gains
`peq: PeqStatus | None`, `actuator: str`.

## 5. Detector and harness changes (item 5)

`DetectorConfig` (detector.py:168) gains `actuator "geq"`, `peq_q_default 6.0`, `peq_q_wander 4.0`, `peq_offset_unc_oct 0.03`,
`peq_q_scale_min 0.8`, `peq_q_scale_max 1.25`, `peq_notch_max_db −12`, `peq_notch_budget_default 4`, `peq_merge_oct 0.08`,
`peq_reach_oct 0.5`, `peq_reclaim_flat_shelves true`, `peq_reread_before_open true`, validated at :412-415, mirrored in the
device.yaml `detector:` block (:972-976 neighbourhood; `test_detector.py:170` asserts yaml == defaults). `cfs._notch` (cfs.py:1390)
passes `q=n.q, key=("peq", ses.bus_int, n.band), step_db=plan.new_db − plan.current_db` for PEQ notches.

**rtasim.** `Renderer.set_peq_notch(band, f_hz, gain_db, q)` beside `set_geq_gain` (render.py:125-135); `geq_gain_at` (:137-141) sums
both sets, each notch at its own Q; the actuator gate `geq_on = bool(self.geq)` (:149, gating tones :152-156 and the ring's loop
:181-186) becomes `bool(self.geq) or bool(self.peq)` (critic-A had to override `dict.__bool__` to get a PEQ-only run applied). `Scene`
(:69-71) gains `peq_q`, `peq_init`. `harness.run_one` (:298-366) takes `actuator="geq"|"peq"`: for peq a `PeqNotchController(budget=
cfg.peq_notch_budget_default)` plans on `Detection.freq_hz`, snaps f to the 201-grid and Q to 6.103, applies after
`ACTUATION_DELAY_FRAMES` (:61) and calls `det.note_cut(f, g, None, q=q, key=("peq", 0, band), step_db=…)`; `cuts` tuples extend to
`(ts, band, gain, f_hz, q)`; `_survivors` (:280-296) tests "a cut within 0.25 oct of the ring" instead of `geq_band_for ± 1`.
`run_detector_eval` (run_detector_eval.py:236) gets `--actuator` and a `cost` column = Σ removed/oct per run (§2.2 integral) **labelled
with the GEQ Q it was rendered at**, so the corpus scores the quantity the PEQ is for without passing the Q 3 guess off as a fact.

**Predictions, closed loop, strict `survived`** (today's numbers DETECTOR.md:463-487; verified by critic A's independent Q 6.10 sim):

| scenario | ring / e | GEQ (corpus Q 3) | PEQ Q 6 (err 0.03 / worst 0.055) | verdict |
|---|---|---|---|---|
| S10 (scenarios.py:274) | 2588 Hz, e 0.2 | GEQ 2500 (δ 0.05): −3 → 2.87, dead | 2606 Hz: −3 → 2.82 / 2.48, dead, 'confirmed' | same kill and latency; cost 0.207 vs 0.383 (bracket 0.280–0.533) |
| X9 (:640) | 525.4 Hz, e 0.3, speech | GEQ 500 (δ 0.071): −3 → 2.75, dead | 532 Hz: −3 → 2.82 / 2.48, dead | same; speech formants lose 0.207 instead of 0.383/oct; bystander at 0.2 oct moves 0.80 dB vs 1.24–2.29 |
| K6 (:918) | 1788.9 Hz, e 3.5, midpoint | −3 → 2.06, −6 → 4.10, dead, margin +0.6 | −3 → 2.82, −6 → 5.62 / 4.92, dead, margin +2.1 / +1.4 | **both two steps**; the second step comes from 'held' (deepen right :936-944) or 'insufficient' (cfs.py:2006-2013) depending on seed; latency unchanged; end e_eff −2.5 vs −0.61 |
| K1/K2/K3/K4/K5 (on centres) | e 4–7 | −9 kills, margin +2.0 (K2) / +2.5 (K4) | −9: +1.39 / +1.89 at 0.03, **+0.30 / +0.80 at 0.055**; −12: +2.59 / +3.09 | three steps at −9 in the sim (centroid ≈ 0.014 oct off → +1.86 on K2); the −12 cap covers the worst case |
| M2 seed 2 (:485) | +0.05 dB re-cross at the end | survived | survived | detector-limited, unchanged |
| 68 breakers wrong-cut cost (§6.4b) | — | −3 GEQ each | −3 Q 6 each | 0.207 vs 0.280–0.533 per wrong cut; deepen chains to the cap 0.619 vs 0.891 (Q 3) |

TP/FP/latency identical (the detector's decision is untouched); `survived` 2 → 2. Add `K7_hop_200c_after_peq_cut` (hop 200 c at +1 s
after the first cut) expecting a second notch and survived 0.

## 6. FakeDesk and tests (item 6)

`_refresh_cuts` (fakedesk.py:1200-1246) applies only the inserted GEQ; `_store` marks cuts dirty only for `/-prefs/rta/`, `/insert/`,
`/fx/`, selidx (:775-776). Add `"/eq/" in address` to the dirty test; in `_refresh_cuts`, after the GEQ legs, when
`state[f"{prefix}/eq/on"]`: for bands 1..6 with type PEQ/VEQ and g ≠ 0 add `peaking_gain_db(hz, f, g, q)` per RTA band; shelves via the
RBJ shelf formulas; LCut/HCut as 2nd-order Butterworth (slope UNCONFIRMED, documented); **sum GEQ + PEQ before `SyntheticRta.attenuate`**
because it is absolute per band (meters.py:658-667). The ring's loop cut is taken at its band (meters.py:722-728): ≤ 0.05 oct off,
2.55 vs 3.0 dB at Q 6 — acceptable in slice 1; B's `attenuation_at_hz` callable at the ring's true Hz is slice 2. FakeDesk defaults
every band to PEQ / g 0 / Q 2 (fakedesk.py:122, `_EQ_FREQS` :164, :620-623), so tests never see the real desk's pool: add a scene knob
(`--bus-eq-preset real`) that plants `HCut 20k00 -0.50` on band 6 and, for the protection tests, `LCut 120` on band 1 and a −4 dB
unexpected cut. Test lists per slice in §10(c).

## 7. Report and operator surface (item 7)

`_notch_dict` (cfs.py:1325-1334) gains `actuator, notch_hz, q, type_before`; `fx_slot`/`side` (:1329-1330) are `None` on the PEQ path;
`report["geq"]` (:2894) is `None` without an insert; new `report["actuator"]` and `report["peq"] = {bands, free_at_arm, eq_on_before,
orphaned_bands}`; `existing_cuts` splits into ours / unexpected / engineer. Markdown (:391-398): `| Freq | Actuator | Band | Q | Depth |
Confidence | Detections | Tier |` — "2350 Hz | PEQ | Bus 3 band 4 | 6.1 | −6.0 dB" vs "2500 Hz | GEQ | 22 | – | −6.0 dB"; header "PEQ: bus
EQ bands 2,4 (HPF band 1 kept); GEQ FX5L fallback, unused". `cfs_status` (server.py:1912-1935) "3 notch(es) (PEQ 2, GEQ 1)";
`_watch_summary` (:1846-1861) "PEQ (4 free bands, HPF 120 Hz kept), GEQ FX5L fallback"; `ring_out`'s "cutting up to N notch(es) on GEQ …"
(:1994) names the actuator. Dashboard: `NOTCH_KEYS` (webui.py:80, :552) adds `actuator`, `q`; `applyNotches` (webui/index.html:295-307)
falls back to `geqHz[band-1]` without `freq_hz` (:296), so a PEQ notch must always carry it (it does: `freq_hz == notch_hz`); chips
(:301-306) get a PEQ/GEQ prefix, the marker label (:168-181) shows Q. Events: `cfs.notch` gains `actuator, q, notch_hz`;
`cfs.peq_external` mirrors `cfs.geq_external`. Undo: `restore_snapshot(scope="bus.N")` now, `release_cfs_notches` in slice 2 (§4.4).
Docs: README.md:731-737 (the "−3 dB cut on the nearest band of a 31-band GEQ2" and "bus-PEQ fallback … not built" paragraphs);
GIG_CHECKLIST.md:38-44, :214-236 (X32-Edit shows the cut on the **bus EQ page**; GEQ setup optional on buses), :250 (flatten = release
tool or restore); DESIGN.md §12 (:512-590), §14 (:636-653), §15 (:654-687); DETECTOR.md §4 (:197-231) / §9 (:638); CFS_POLICY.md §3 keys,
§8 table.

## 8. Migration, defaults, failure modes (item 8)

**Defaults.** Slice 1 ships `detector.actuator: geq` — nothing changes for anyone until they pass `actuator="auto"|"peq"` on
`feedback_watch`/`ring_out` (and `ring_out_system` plan items) or set it in device.yaml. After desk test (b) passes, one line flips
the default to `auto` = PEQ on buses (≥ 1 free band), GEQ on Main LR. GEQ provisioning (`setup_ringout_eqs`) stays for Main LR and as
the bus fallback; a bus without a GEQ insert but with free PEQ bands now arms (today it fails, README.md:736-737; `_open_session`'s
`assert ins is not None` at cfs.py:1037 goes; `_Session.fx_slot/side/sel` :597-599 become Optional). Reports from GEQ sessions keep
matching (matching is per actuator); existing reports without `actuator` read as "geq". **Visibility interlock** (both defaults): if the
first PEQ notch on a bus produces no drop ≥ 0.5 dB on any line within 0.1 oct of it by `cut_verify_s` while the line is still present
(nothing at the tap moved) → abort with "bus EQ not visible at the RTA tap"; under `auto` the session's remaining notches go to the
GEQ (slice 2), under `peq` it aborts. Note a *killed* ring vanishes at any tap (docs/HANDOVER.md:165-169), so the interlock only fires
on held lines — the exact case the deepen chain would otherwise walk to −12 into a bypassed EQ.

**Failure modes specific to the PEQ.** (a) *Wrong frequency is inaudible and useless*: a Q 6 −3 notch 0.10 / 0.15 oct off gives 1.77 /
1.17 dB → 'insufficient' within ≈ 2 s (`cut_settle_s + cut_response_s + cut_verify_s`) → slice 1 deepens (3.52 at 0.10), slice 2's
`off_centre` opens a second notch instead; the missed −3 stays (0.207/oct, inaudible). (b) *Hopping hand-held ring*: Q 6 and the wander
rule (Q 4); 200-cent hops need a second band under any actuator — two bands per line, then alert. (c) *His HPF activated by eq/on*:
reported. (d) *Small pool*: the real bus has 4–5 usable bands (default HCut + an HPF); budget ≤ free; > 4 notches → "placement" in the
report. (e) *Two sessions in an evening*: matched rows adopted and deepened. (f) *Scene recall*: releases, reported. (g) *Q-definition
mismatch*: absorbed by the bracket; a wider-than-RBJ desk notch kills conservatively and costs at most the Q 4.88 column (0.74 at −12,
under GEQ Q 3 −9). (h) *Missed pushes*: the re-read before open catches a hand edit on a free band; a late push on an owned band is
adopted late (never written shallower).

## 9. Residual uncertainties only the desk can settle

1. The X32's PEQ shape and Q definition (RBJ half-gain vs −3 dB-bandwidth vs constant-Q) — sets `peq_q_scale_*` and possibly `bell=`;
   and its symmetry above ~6 kHz (bilinear warping at 48 kHz compresses the upper flank; bracketed by `offset_unc_oct` until measured).
2. Whether the bus EQ is upstream of the post-EQ RTA tap (stat 146+N−1, meters.md:427; the channel overlay text at :439 supports it;
   HANDOVER.md:165-169 measured only PRE vs POST *insert*) — gates the default flip (D6).
3. Whether `/xremote` echoes our own `eq/B/{f,g,q,type}` writes (echo guard, §4.3) and whether pushes reach us with many clients.
4. Per-datagram apply latency of a 4–5-datagram open (`measure_settle.py`) — the < 100 ms detect→cut budget on the first notch.
5. LCut/HCut slope (FakeDesk model only) and whether a rail-parked cut filter is audibly inert (slice-2 reclaim rule).

## 10. Slices, tests, desk checklist

### (a) Minimal shippable slice (buses only, one actuator per session, default `geq`, opt-in `auto`/`peq`)

| file | change | ~lines |
|---|---|---|
| src/x32mcp/detector.py | `DetectorConfig` +12 keys and validation (:168, :412-415); `note_cut(q, offset_unc_oct, key, bell)` with the corner bracket, `peq_reach_oct`, `cut_log` actuator/q (:819-880, :947); `NotchPlan`/`Notch` fields, `NotchWriter.write(plan)`, `NotchPlanner` Protocol; `PeqNotchController` (allocation classes, octave merge, wander rule, `max_db`, `owned`, `propose_reason`) | 380 |
| src/x32mcp/policy.py | `validate_notch(current, new, limit_db=None)` (:442-459) | 10 |
| src/x32mcp/desk.py | `eq_from_push()` beside :754-778 | 30 |
| src/x32mcp/provision.py | `PeqStatus`, `classify_peq_bands()`, per-row report matching, preflight PEQ branch (blockers/warnings of §4.5), `Preflight.peq/actuator` | 170 |
| src/x32mcp/cfs.py | `write(plan)` on `_DeskGeqWriter`; `_DeskPeqWriter` (two-call open, cap, owned, partial-open restore, eq/on turn-on, reread-before-open); `_Session` (actuator, Optional fx fields, `eq_on_before`); `_open_session` actuator choice + per-actuator budget; `_notch` (abort check, `write(plan)`, per-actuator invalidation, `note_cut(q, key, step_db)`, log text :1356); `_on_desk_push` eq branch (observe all bands, eq/on abort, echo compare, release on f/q/type, `(actuator, band)` keys in `_TierB`/ignore/at_max :494, :1805-1817, :1904, :2046, :2070, :2421); `_finish` eq/on restore; visibility interlock; `_notch_dict`/`_build_report`/markdown | 450 |
| src/x32mcp/server.py | `actuator` argument on `feedback_watch`/`ring_out`/`ring_out_system` items, `notch_budget=None`, summaries (:1846-1861, :1867, :1952, :1994, :2045) | 50 |
| device.yaml | `detector:` keys (:972-976 neighbourhood) | 14 |
| src/x32mcp/fakedesk.py | dirty on `/eq/` (:775-776), PEQ/shelf/cut bells in `_refresh_cuts` (:1200-1246), `--bus-eq-preset` | 80 |
| src/x32mcp/webui.py, webui/index.html | `NOTCH_KEYS` (:80), chip prefix and Q label (:168-181, :301-306) | 20 |
| tests/rtasim/{physics,render,harness,run_detector_eval}.py | `set_peq_notch`, gate fix, `actuator=`, `_survivors` by octave, `--actuator`, cost column, K7 scenario | 220 |
| tests | unit 320, integration 380, rtasim 60 (see (c)) | 760 |
| docs | README, GIG_CHECKLIST, DESIGN §12/§14/§15, DETECTOR §4/§9, CFS_POLICY §3/§8 | — |

≈ 1.4 k lines of code, 0.76 k of tests. PR order, each green alone and GEQ behaviour byte-identical until PR6: **PR1** detector
(`note_cut`, cfg, `PeqNotchController`, dataclass fields) + unit tests; **PR2** rtasim actuator + harness + eval + the §5 table as the
gate; **PR3** FakeDesk bells + preset; **PR4** provision + `eq_from_push` + `validate_notch(limit_db)`; **PR5** cfs writer/session/push/
report + server + webui + integration tests; **PR6** docs, and the default flip to `auto` gated on desk test (b).

### (b) Follow-on slices
**Slice 2:** `off_centre` second notch; mixed sessions (PEQ overflow → GEQ under one counter; two controllers); `release_cfs_notches`
with `validate_release`; `peq_first_step_loud_db`; rail-parked cut-filter reclaim; FakeDesk ring cut at true Hz; `verify_watch_fakedesk.py`
scenario F (A through the PEQ path). **Slice 3:** Main LR PEQ via `ring_out`'s Tier-2 confirmation only (never `feedback_watch`), the
"< 2 free → GEQ2 pair" rule; `peq_widen_at_max`; measured `bell=` from desk test (a).

### (c) Tests per slice
*Slice 1, unit* (`tests/test_peq_notch.py`, `test_detector.py`, `test_cfs_policy.py`, `test_device_yaml.py`): the §1 classification
table (LCut g=0 reserved, HCut 20 kHz reserved-and-flagged, flat shelf reclaimable with `type_before`, boost reserved, Main all reserved,
per-row report match vs unmatched); grid rounding (2331 → 2349.8, Q 6 → 6.103, g −3 exact, Q compared in Q not raw); cuts-only and cap
(`validate_notch(limit_db=−12)` refuses boost / shallower / < −12; the writer refuses Q up, an f move on a live notch, type ≠ PEQ);
budget = min(budget, free), band 1 last, deepen free; merge 0.07 deepens / 0.09 opens; wander rule → Q 3.913; `note_cut(q=)` brackets
equal §2.4 **with lo ≤ the true drop for a Q 7.5 desk notch**; `key` keeps 2350/2450 Hz apart; a Q 6 −3 notch 0.15 oct from a flat line
files no false 'insufficient'; `bell=` override; `propose_reason`; `(actuator, band)` keys; yaml == defaults. *Slice 1, integration*
(FakeDesk; `tests/integration/test_cfs.py`, `test_cfs_write_safety.py`): watch through PEQ (20 dB/s ring at 2400 Hz → band 2 PEQ 2400 Hz
Q 6.1 −3 within ~1 s, collapses, 'confirmed', report row carries actuator/notch_hz/q); ring_out NOTCH→VERIFY deepens on one band; a held
line walks −3 → −12 and stops (the PEQ twin of DETECTOR.md:231); engineer bands protected with the real preset (free 4, warnings name
them); eq/on rules (OFF + flat → ON with the first notch, OFF at end when no notch; OFF + a +6 HShv → PEQ blocked, auto → GEQ; OFF push →
abort, ring_out backs off); hand edits (g −6 adopted; g 0 released, ignore-listed; f push released; a push on a free band leaves the
pool); missed-push guard (edit FakeDesk state without a push → the re-read refuses); q before g on the wire; failed g write after
(type,f,q) → not committed, restore attempted, `orphaned_bands`, budget uncharged, re-proposed after cooldown; `restore_snapshot(scope=
"bus.3")` flattens bands 1–6; a second session adopts matched rows and reports an unmatched cut; Main LR under `auto` arms on GEQ and
never touches `/main/st/eq`; `actuator: geq` byte-identical to today's tests; visibility interlock (PEQ bell disabled → abort). *Slice 1,
corpus:* `run_detector_eval main --actuator peq` (67 + K, seeds 1–3, both modes) and `adversarial_closed` against the §5 table: TP/FP/
latency identical, survived 2 (M2 s2), K1–K6 0, cost below the GEQ's at every Q label; K7 gets a second notch. *Slice 2:* off_centre
second notch (a line 0.10 oct off gets a new band, not a deeper one); PEQ exhausted → GEQ under one counter; `release_cfs_notches` skips a
moved band and refuses in show mode; rail-cut reclaim; scenario F. *Slice 3:* Main LR PEQ only through the confirmed path; widen at cap.

### (d) Additions to REVIEW_REPORT.md §8 (docs/REVIEW_REPORT.md:1013-1040, beside items 3, 4, 6)
* **3′ PEQ bell shape.** Oscillator sine into the bus, RTA on the bus post-EQ (stat 146+N−1), PEQ band 2 `PEQ` at the tone, g −6 and −12,
  Q 6.10 and 10.0, at 2 kHz and 8 kHz: read the RTA with the tone at 0 / ±1 / ±2 / ±3 bands (±0.1 / 0.2 / 0.3 oct) and compare with
  §2.1 — fixes `peq_q_scale_*` (or `bell=`) and shows the warping above ~6 kHz. Pink noise as a cross-check (B's (a)).
* **3″ Tap order.** Same notch with the bus EQ on/off, and with a PRE and a POST GEQ insert: the post-EQ tap must move with the bus EQ
  (gates the `auto` default). Also confirm eq/on is the only bypass (no per-band on: device.yaml:207-212).
* **3‴ Push behaviour.** Does `/xremote` echo our own `/bus/NN/eq/B/{type,f,g,q}` writes (echo guard)? Turn a bus EQ encoder on the
  console mid-session → arrives as a `/bus/NN/eq/B/g` push and is adopted; switch the bus EQ off on the console → the session aborts;
  repeat with X32-Edit + iPad connected (item 6) — decides whether `peq_reread_before_open` may default off.
* **4′ Latency.** `measure_settle.py` with `/bus/NN/eq/4/{type,f,q,g}`: per-datagram apply time and the 4/5-datagram open end to end.
* **10′ Fixture.** `dump_desk_state` of a bus with the real defaults (the band-6 `HCut 20k00 -0.50`) into `tests/fixtures/` for the FakeDesk preset.

## Appendix A. Critique dispositions

**Design A** — (1, major) inverted Q-scale bracket: fixed, §2.4 takes min/max over both Q corners with the `sorted()` idiom; regression
test in (c). (2, major) eq/on written ON over a boost: §4.2 makes any non-flat band under a bypassed EQ a PEQ blocker, never a warning.
(3, major) `release_cfs_notches` without identity check: §4.4 releases only rows that still match, through `validate_release`, refused
in show mode. (4, major) writer contract / fx-slot fields unspecified: §1 `write(plan)` with the plan carrying actuator/f/q/type_before,
Optional `fx_slot/side/sel`, per-actuator invalidation, `report["actuator"]`/`["peq"]`, `NotchPlanner` Protocol. (5, major) slice-2
keys on bare band numbers / `_cfg.notch_max_db`: D10, `(actuator, band)` and per-actuator cap from slice 1; `band_for_freq` defined
in §1. (6, major) K-margin loss unstated: §2.3/§5 print it; −12 in slice 1. (7–9, minor) cost headline, 6 %/0.3 dB overstatements,
K6 narrative: §2.2 brackets over Q 2–4.3 with a labelled cost column, "≤ 7.5 %", "0.3–0.5 dB", "held or insufficient (seed-dependent)".
(10, minor) rate-limiter: §4.1 "g precedes q on the wire", datagram count stated, latency measured in 4′. (11, minor) two-call failure:
§4.1 restore + `orphaned_bands` + test. (12, minor) allocation gaps: §1 table covers negative shelves/VEQ, records `type_before`, flags
the rail-parked HCut, FakeDesk preset. (13, minor) `_matched_session`/`existing` citations: per-row semantics stated; `existing` is
cfs.py:1038. (14, minor) push compare/decoding: §4.3 echo guard and `eq_from_push` via `ParamSpec.to_value`. (15, minor) rtasim gate
and `bell` signature: §5. (16, minor) eq/on restore: D7. (17, minor) citation drift: re-anchored throughout (write :1361, invalidate
:1363, commit :1382, deepen right :936-944, `_tier_b_finish` :1819).

**Design B** — (1, blocking) stale free pool → boost: D8/§4.3 observes all six bands, re-classifies on any push, re-reads before the first
write, populates `owned` at commit. (2, major) eq/on hole before the first notch: §4.2 tracks eq/on live, aborts on any OFF push, the
writer refuses g under an OFF belief. (3, major) false write-order claim: §4.1 cites desk.py:1160-1175 (on, type, f, g, q) and mandates
the two-call open. (4, major) BandMap keeps the algorithm: rejected; D1 separate controller with octave merge, and `__init__/observe/
_apply/existing` listed as untouched on the GEQ side. (5, major) rules 2a/2c: dropped (§2.5); 2d kept as the second-notch rule. (6, minor)
K6 chain: "both two steps", margin ×3. (7, minor) collateral at Q 2: §2.2 quotes all three Q ends and A's integral. (8, major) −12 cap
wired only in Policy: §2.3 names the three readers and the end-to-end −9 → −12 test. (9, major) `auto` default before the desk test: D6
ships `geq`, the flip is gated on 3″, and the visibility interlock stays. (10, minor) no Q-margin, no `key=`: §2.4. (11, minor)
"`_notch` changes at one line" / abort check: §4.1 lists the edits and adds the check. (12, minor) "read-after-write": §4.1 calls it the
grid-quantised request. (13, minor) partial write: §4.1 `orphaned_bands`, `owned` at commit, bus-eq invalidation. (14, minor) citations:
hops are DETECTOR.md:33 (§0 item 4); held cadence 3 s (CFS_POLICY.md:211); the GEQ bracket at the centre is 0.00 wide; write :1361.

**Kept from both** (all verified here): the RBJ numbers; Q 6 first with the wander rule; the −3 ladder; deepen > second notch > widen;
the allocation core; notches left in place; writer-enforced `validate_notch`; the push rules; snapshot and scoped restore; Main LR on the
GEQ; the harness/FakeDesk plan; the S10/X9/K6 predictions; the desk-test additions.
