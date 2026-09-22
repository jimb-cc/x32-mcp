# CFS² policy layer — what cfs does with the detector's hooks

`src/x32mcp/cfs_policy.py` (configuration `CfsPolicyConfig` = `device.yaml cfs_policy:`, every key equal to the dataclass default,
asserted both ways by `tests/test_cfs_policy.py`; and the pure rules), `src/x32mcp/cfs.py` (the wiring: `_policy_frame` runs after
every `feed()`, the "policy layer" section of `CfsManager`), tests in `tests/test_cfs_policy.py` (rules, config, the detector accessor)
and `tests/integration/test_cfs_policy_desk.py` (end to end against the FakeDesk with a quiet synthetic bed), the end-to-end drive
`scripts/verify_watch_fakedesk.py` scenario C.

The detector (`docs/DETECTOR.md`) decides what *is* feedback on evidence it can defend and leaves POLICY to the session manager: it
publishes MODERATE candidates it will not cut (§4 "tier-B hook"), files a verdict after every cut (`note_cut()` → confirmed /
insufficient / held / false_cut / ambiguous), raises analyser flags and offers the ring-out contract check (`programme_present()`).
This layer turns those into desk actions under the session's ordinary write discipline: every write goes through `Desk` / `Policy`
(tiers, clamps, the rate limiter), GEQ bands are only ever cut and never written shallower, lowering master writes take the priority
lane, colour writes are Tier 1 `label`, and no policy action starts once `abort_reason` is set (panic, operator override, connection
loss, frozen display, stop).

## 1. LF edge from the open mics' high-pass filters (`cfs_policy.lf_edge`, REVIEW_REPORT §1.7 Q3)

At `_open_session` the `/ch/NN/preamp` sections of the preflight's *included* mics are read in one scoped `/node` sweep
(`Desk.dump(sections=[...])`, not raw gets). Per mic: edge = `hpf_factor` 0.7 × the HPF corner when `preamp/hpon` is on, else
`no_hpf_hz` 100 Hz (a mic whose preamp could not be read counts as HPF off). Session `lf_edge_hz` = max(`floor_hz` 60, min over the
mics); no included mic → `None` (the detector's mode default: 160 Hz watch / 63 Hz ring_out). It is passed as
`FeedbackDetector(cfg, band_hz, mode=…, lf_edge_hz=…)` and overrides the mode defaults (P6). Two mics with 120 / 160 Hz HPFs → 84 Hz
(the desk quantises the corner on its 101-step log scale and prints integer Hz, so 120 reads back 121 → 84.7); one of them switched
off → still 84 (min(84, 100)); no HPF anywhere → 100; a 40 Hz corner → floored at 60.

`feedback_watch(..., lf_feedback_possible=True)` / `ring_out(..., lf_feedback_possible=True)` (server tools too, default False; the
ring_out confirmation payload binds it) is the operator's declaration of an LF-capable rig (kick / floor-tom mic into subs, a drum
fill): it opens the window to the detector's `window_low_hz_lf` 40 Hz and takes precedence over the HPF-derived edge, which is still
reported. The arm result and the report carry `lf_edge: {lf_edge_hz, from: "ch 3 HPF 120 Hz", mics: [...], lf_feedback_possible,
window_low_hz}`; the feedback_watch tool summary says "feedback window from 84.7 Hz (ch 1 HPF 121 Hz)".

## 2. The ring-out contract check (G8; `programme_check_s` 5 s)

`det.programme_present()` is evaluated on every frame. The session latches "programme detected" the first time it is True within the
first `programme_check_s` (before and during the first RAISE steps) or on any later rising edge, publishes `cfs.programme_present`
{session_id, bus, mode, t}, and in ring_out adds the report warning *"programme detected on Bus N during the ring-out (t+X s): the
'stage is quiet' contract does not hold, so MODERATE lines are not cut, only probe-confirmed / loud / rising lines (and loud-ish lines
by tier B)"* — and forces `detector.ringout_emit_moderate` OFF if a descriptor had switched it on (it is off by default; the layer
asserts rather than relies: `dataclasses.replace` on the live detector's cfg, reported as `emit_moderate_forced_off`). The run is never
aborted for it. In `feedback_watch`, when that first detection comes after the detector's own `arm_baseline_s` 2 s reference window
(the session armed in silence and the show started later) `det.refresh_arm_reference()` re-opens the 2 s level reference so LOUD /
loud-ish are not judged against a silent-room snapshot (DETECTOR §9/§11), once, reported as `arm_reference_refreshed_s`;
`armed_in_silence` in the report says whether programme was seen within `programme_check_s`.

## 3. Tier B — the one-shot policy cut and its verdict-driven follow-up (G7; `cfs_policy.tier_b`, ON in both modes)

After each `feed()` the layer scans `det.candidates`. A candidate is tier-B material (`tier_b_eligible`) when it is `MODERATE` (BASE
with no evidence the detector may act on — in watch also an AT-ARM line the at-arm rule of §5 declined), present this frame, not
`stationary`, without a `cut_verdict`, not `false_cut`, "no_family" among its reasons, loud-ish (`level_db >= det.loudish_threshold_db`)
or `excess_db >= min_excess_db` 20 over its band's baseline, `age_s >= min_age_s` 0.6 **and its presence run at least as long**
(`run_frames`: a note re-struck every beat at one pitch keeps its track and its age but restarts its run — without this a strummed
chord became eligible after two strums), its GEQ band not already at `notch_max_db`, budget left (or the band already opened), not
on the session's ignore list, and no other tier-B engagement started within `cooldown_s` 2 s. In ring_out a line the probe has not
judged (`steps_seen < probe_min_hits`) waits for the probe — the better instrument there — unless it is loud-ish AND has been
MODERATE for `ringout_probe_wait_dwells` 2 dwells.

The cut is ONE −3 dB step through the normal `_notch` path — `NotchController.propose` → GEQ write → `commit` →
`det.note_emission(candidate, ts, reason="tier_b")` → `det.note_cut(...)` — with a `Detection` built from the candidate (klass
`POLICY`, reason tag `tier_b`). `note_emission` is the one detector-side accessor this layer added: it records the emission on the track
exactly as `feed()` does for its own (emitted count, level / probe hits at emission, band cooldown) so the post-cut verdict treats the
policy-cut line as *the* cut line (a ring that collapses time-locked to the write is `confirmed`, not a verdict-less bystander) and any
later re-emission by the detector needs fresh evidence; `tier_b` is not plateau-class evidence, so the detector never deepens a tier-B
cut itself. Every policy cut carries `tier: "B"` and `policy: <rule>` in the notch log, the report and the `cfs.notch` event (detector
cuts are `tier: "A"`; a report notch lists the `tiers` that touched its band); policy cuts count against the notch budget.

Then the verdict the detector files for that line drives the engagement (`_policy_tier_b`, one `_TierB` record per line):

* `confirmed` → done (regrowth is the detector's business: RISE / re-emission on `reemit_rise_db`).
* `false_cut` (the line ENDED by itself after the response window — a loop that survived a cut does not switch itself off) → the
  line's GEQ band / RTA band ±1 go on the session **ignore list**; no further policy cut lands there; the −3 dB **stays**
  (`Policy.validate_notch` forbids shallower writes; releasing it is the operator's or a later session's decision) — reported under
  `policy.ignore` and in the markdown. The same happens when a line that had `held` later ends on its own without a pending verdict.
* `held` (dropped by ≈ the bell and still there, flat) → this is EITHER a note through the EQ OR a limiter/compressor-held howl with
  more excess than the cut, passively identical — so it is never decided on drop ≈ bell alone: the line keeps **alerting** (§4, class
  `HELD`), and if after `held_deepen_s` 3 s it is still a held family-less BASE line (klass MODERATE, "no_family", not stationary, not
  common-mode) one more −3 dB step goes in (`policy: tier_b_held`), verified again through `note_cut`; after another `held_deepen_s`
  still held → −9; it stops at `notch_max_db` (outcome `max_depth`). A line that turned MUSICAL meanwhile is left (`musical`).
* `insufficient` (drop short of the bell: the excess exceeds the cut, or the bell missed the line) → one deeper step immediately
  (`policy: tier_b_insufficient`, what ring_out's VERIFY does), then re-evaluate on the next verdict.
* `ambiguous` → nothing further, reported. No verdict within `verdict_timeout_s` 4 s → the engagement is given up (`no_verdict`).
  A policy write that did not happen (rate limit, timeout: `no_write`) is retried after `cooldown_s` like the detector retries its own.

In ring_out the writes the layer decides on are queued to the ring-out task (`_run_policy_action`: HOLD → NOTCH stages tagged
`tier: "B"`), so every master and GEQ write of a run stays in one task, and with `ringout_hold_raise` the master is not raised while an
engagement is unresolved (queued / awaiting its verdict / waiting out a held line): the run resumes when it is done. Every step is one
entry of `report["policy"]["tier_b"]` = `{t, freq_hz, rta_band, band, depth_db, action (tier_b | tier_b_held | tier_b_insufficient |
backoff_probe | verdict | end), verdict, next_action, tier: "B", reason}` and one `cfs.policy {what: "tier_b", …}` event.

Measured against the FakeDesk (bus 1, GEQ2 on FX5, quiet bed ≈ −50 dBFS at 1 kHz, `held_deepen_s` 1.5): a −18 dBFS family-less line
arriving within one frame is MODERATE at K1, cut −3 dB 0.7 s after it appeared, `held` at +1.55 s, −6 dB at +3.1 s, `held`, −9 dB at
+6.2 s, `held`, end (`max_depth`); the strip is RDi from 0.37 s after the line appeared until 2.4 s after it ended (`hold_s` + `clear_s`). The note ending 1 s
after the first cut files `false_cut`, the band is ignore-listed at −3 dB and the same note returning is alerted, not cut.

What tier B costs when it is wrong: one −3 dB band on a loud-ish (or ≥ 20 dB-over-baseline) family-less note held longer than 0.6 s
(the documented irreducible class, DETECTOR §7.2/§7.9); −9 dB only if the note is also held flat for > 2 × `held_deepen_s` after the cut.

## 4. Alerts that reach the desk (`cfs_policy.alerts`)

"Reported, not cut" is a no-op unless it reaches the engineer at the desk within a second. (a) **Events**: `cfs.candidate` with
`alert: "on" | "off"` and `alert_class` (`MODERATE` — a BASE line the detector will not cut, aged ≥ K1; `HELD` — a cut landed and the
line dropped by the bell and stayed; `STATIONARY` — followed the steps and carries `backoff_advised`; `AT_ARM` — watch declined to cut
an at-arm line; `TIER_B` — a policy engagement is open on it) plus the candidate's `freq_hz, band, klass, level_db, prominence_db,
excess_db, age_s, reasons, cut_verdict, confidence` — only on state changes (first qualifying frame, class change, cleared after
`hold_s` 0.5 s without the line); the periodic best-candidate `cfs.candidate` publication is unchanged. (b) **Scribble strip**
(`scribble_strip: true`): the bus strip's `config/color` is read at arm (a colour the engineer sets during the session is adopted from
the `/xremote` push as the one to restore); while ≥ 1 alert-worthy candidate is live the strip is written to `color` `RDi` (inverted
red; validated against the descriptor's colour enum, an unknown token disables the feature with a warning), restored to the original
once none has been live for `clear_s` 2 s, never more than one write per `min_write_interval_s` 1 s, only on state change, through
`Desk.label(t, color=…)` (Tier 1); and ALWAYS restored in `_finish` — the single exit of stop / DONE / ABORT / operator override /
panic / connection loss / `close()` (`_unwind` ends there too) — with three attempts, `cfs.alert {on: false, restored: true}` and a report
warning if it could not be written. (c) `cfs_status` / `CfsState` gain `candidates` (the live alert list), `alert` (bool) and `policy`
(`{lf_edge_hz, window_low_hz, programme_present, tier_b_open, ignore, flags, strip_alert}`); the tool summary appends
"ALERT: n suspicious line(s) not cut: 1015 Hz HELD −21 dBFS (cut held)". Both watch and ring_out.

## 5. AT-ARM in watch (`at_arm_watch_min_prominence_db` 30)

A family-less steady line already sounding when a watch arms — an established quiet howl, an organ note, a projector whine — is one
passive observation (DETECTOR §6.4/§7.4: a −36 dBFS whine was cut once at 0.85 s). In `feedback_watch` a Detection whose reasons
contain `established_at_arm` is cut only when it is tier A: `loud` in its reasons, or `prominence_db >= 30` (the M7 60 dB howl at
−8 dBFS is still cut at K1), or the detector also holds evidence the line made on its own since (`rise…` / `fastrise…` / `probe…`:
then the cut does not rest on the at-arm observation). Otherwise it is NOT cut: it is logged (`policy.at_arm_suppressed`, the
detection is kept in `detection_log` with `suppressed: "at_arm"`), published (`cfs.policy {what: "at_arm_not_cut"}`), alerted (class
`AT_ARM`, strip colour) and eligible for tier B under §3's conditions (loud-ish or ≥ 20 dB excess — a −36 dBFS whine is neither). The
detector marked it emitted, so it re-emits only on fresh evidence (regrowth, LOUD). In ring_out the detector's own
probe-before-AT-ARM behaviour is untouched.

## 6. Analyser flags (`reforce_ballistics_on_freeze`, `frozen_abort_s` 5, `cfs_policy.backoff_probe`)

* `PEAK_HOLD_SUSPECTED` / `FROZEN_LINES` mid-session: the prefs were forced at arm, so a frozen display means somebody changed them on
  the console. Once the flag has stood `frozen_reforce_s` 1 s (a live line's skirt band can repeat its int16 code for a few frames; a
  frozen display stays frozen) the layer re-forces the ballistics once (`meters.force_rta_ballistics`: `decay` 0.0 and `peakhold` 0,
  unconditional writes with the previous values read for the report), logs, reports (`policy.ballistics_reforced`, a warning,
  `cfs.policy {what: "ballistics_reforced"}`) and gives the display `frozen_abort_s` to come alive; if the flag then persists that
  long the session is aborted ("the RTA display is frozen (…): the detector cannot see through a frozen display") — a ring-out backs
  off and finishes ABORT, a watch stops. Measured: the FakeDesk repeating its last analyser frame → PEAK_HOLD_SUSPECTED at +0.5 s →
  re-forced 1 s later → abort `frozen_abort_s` after that (1.5 s in the test).
* `SLOW_RELEASE` → report warning with `det.release_db_per_s`; `HOT_SPECTRUM` → report warning (arm p95); every flag's first
  appearance is in `policy.flags_seen` with its time and a `cfs.policy {what: "flag"}` event.
* `backoff_advised` on a STATIONARY candidate in ring_out (≥ 30 dB prominent family-less line that follows the +1 dB steps 1 dB/dB:
  a room source — or a howl whose plateau a compressor holds, DETECTOR §3 PROBE / §11 N1) → one **back-off probe** per line
  (`_backoff_probe`, queued to the ring-out task): wait until the last +1 dB step is a probe window old, note the line's level, lower
  the master `drop_db` 3 dB (`force`d lowering write, priority lane, `note_gain_step(−3)`), watch the band for one dwell (≥ the probe
  window), then restore the level (`note_gain_step(+3)`) and wait another window before the next step so neither move lands in the
  window the detector judges a +1 dB step on. Fell by more than `min_response_db` 4.5 dB or died → regenerative → cut where it stood
  (`_notch`, `policy: backoff_probe`, tier B; pre-emptive like the detector's PROBE class); fell ≈ 3 dB → a source in the room → left
  alone. `PROBE` stages, `policy.backoff_probes` = `{t, freq_hz, before_db, after_db, drop_db, died, verdict, master_from/probe/restored}`.
  Measured with a synthetic room whose bed and line follow the master: room source → drop 3.0 → "stationary", level restored, run DONE
  with no notch; a howl with < 2 dB of excess over its plateau → dies → cut −3 dB at its band, run DONE. Known limitation: on a rig
  where the bed at the tap does NOT move with the master (electrical programme), the 3 dB dip becomes the line's low-water mark and 3 dB
  of later following can complete a 6 dB RISE for the detector — bounded to one band, and the same accumulation the +1 dB steps produce
  on their own; disable with `backoff_probe.enabled: false` if a venue shows it.

## 7. Report

`report["detector"]` (flags, release, arm p95, loud / loud-ish thresholds, window, cut verdicts) plus `report["policy"]` =
`{config, lf_edge {lf_edge_hz, from, mics, lf_feedback_possible, window_low_hz}, programme_present {detected, first_seen_s,
armed_in_silence, arm_reference_refreshed_s, emit_moderate_forced_off}, tier_b [...], ignore [...], alerts [{t, freq_hz, klass,
alert on|off}], strip_color {original, alert, writes, restored}, at_arm_suppressed [...], flags_seen [{flag, first_seen_s}],
ballistics_reforced, backoff_probes [...]}`; policy warnings join the report's warnings. `ReportStore.markdown` gains a **Policy**
section (window / LF edge, programme, the tier-B step table, ignore list, at-arm lines, alert counts and the strip colour, flags,
re-force, back-off probes) and the notch table a Tier column. The server's tool envelope trims the per-event alert log and the config
copy (`policy.alerts_entries`, `policy.config_entries`); the saved JSON keeps them.

## 8. Configuration (`device.yaml cfs_policy:`; `CfsManager(..., cfs_policy=CfsPolicyConfig)` overrides it, as `detector_cfg` does)

| key | default | meaning |
|---|---|---|
| `lf_edge.hpf_factor` / `no_hpf_hz` / `floor_hz` | 0.7 / 100 / 60 | §1 |
| `programme_check_s` | 5.0 | §2 arm-window length |
| `refresh_arm_reference_on_programme` | true | §2 watch: refresh the detector's level reference when programme starts after arming in silence |
| `tier_b.enabled` | true | §3 (both modes) |
| `tier_b.min_excess_db` / `min_age_s` / `cooldown_s` | 20 / 0.6 / 2.0 | eligibility |
| `tier_b.held_deepen_s` / `verdict_timeout_s` | 3.0 / 4.0 | follow-up timing |
| `tier_b.ringout_probe_wait_dwells` / `ringout_hold_raise` | 2.0 / true | ring_out interaction |
| `alerts.events` / `scribble_strip` / `color` | true / true / RDi | §4 |
| `alerts.clear_s` / `min_write_interval_s` / `hold_s` | 2.0 / 1.0 / 0.5 | §4 timing |
| `at_arm_watch_min_prominence_db` | 30 | §5 |
| `reforce_ballistics_on_freeze` / `frozen_reforce_s` / `frozen_abort_s` | true / 1.0 / 5.0 | §6 |
| `backoff_probe.enabled` / `drop_db` / `min_response_db` / `settle_s` | true / 3.0 / 4.5 / 0.3 | §6 |

Unknown keys are an error (a typo in a safety policy is not silently ignored); the block itself is optional (absent = defaults).

## 9. What was not done / known limits

* The back-off probe's low-water-mark interaction (§6) is documented, not solved; it needs the tap-point measurement of HANDOVER §4b.
* Ignore-listed bands are never released by the server (cuts only); the report says which bands to flatten by hand.
* The alert colour is one token for the whole strip; the X32 scribble strip cannot blink over OSC, and a strip that already is RDi
  shows nothing (warned at arm).
* Tier B's `min_age_s` / `held_deepen_s` and the at-arm 30 dB line are the same single-room guesses as the detector's tier constants
  (DETECTOR §8); the report's `policy.tier_b` table is the log to calibrate them from.
