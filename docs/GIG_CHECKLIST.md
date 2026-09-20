# x32-mcp — gig checklist and manual verification protocols

Print this. The first four sections are the operating routine (pre-show, changeover, mid-set,
post-show). The last two are the one-off manual verification protocols for the milestones that
need the real desk: **M5** (real-desk verification of reads/writes/safety) and **M7** (CFS² in
the studio). Tick boxes are meant to be ticked; note anything odd in the margin and carry it
into `docs/research/` or an issue afterwards.

Golden rule: **emergency actions never go through the conversational path.** The reflex when
something goes wrong mid-set is the physical mute / fader or X32-Edit on the tablet. `panic()`
exists (Tier 1, never blocked, mutes all outputs in one go) but it is a backstop, not the plan.

---

## Pre-show

Power and network

- [ ] X32 on a **wall socket**, not on a strip shared with the PC or anything switching.
- [ ] Desk, PC and tablet on the same LAN/subnet; static or reserved IPs for the desk and the PC.
- [ ] X32-Edit on the tablet connects to the desk (proves the network before we blame the server).
- [ ] Windows firewall: TCP 8032 rule present; the UDP prompt for the interpreter was allowed
  on Private networks (README → Dashboard → Windows firewall).

Server

- [ ] Claude Desktop / Claude Code started; the `x32` server appears in the tool list.
- [ ] `connection_status` → `ok: true`, correct console name and firmware, RTT a few ms.
- [ ] `dashboard_status` → running; open `http://<pc-ip>:8032/` on the tablet, RTA moving.
- [ ] `show_mode(false)` for setup work (it is off by default).
- [ ] `snapshot_desk("pre-show")` — the reference to diff against all night.
- [ ] `list_scenes` — every scene for tonight is there with the expected name; note the indices.
- [ ] Patch plan for tonight exists under `patches/` (`export_patch_plan` from the desk and edit
  `mic`/`owner`/`monitor_bus` if it does not) — `apply_patch_plan` if names/colours drifted.

Soundcheck / CFS² (before doors — never during the set)

- [ ] `validate_ringout_eqs([wedge buses…, "main"])` → all OK; else `setup_ringout_eqs` (confirm).
- [ ] `discover_mics(bus)` per wedge — the `include` list matches reality (who is on which
  wedge, talkback excluded). Fix the patch plan or the desk, not the list.
- [ ] Stage empty and silent; ring out each wedge (`ring_out(bus)` with the confirmation), then
  Main LR, or one `ring_out_system` run. Read each report summary; the notches make sense
  (a handful, −3 to −9 dB, plausible frequencies for that mic/wedge).
- [ ] `list_ringout_reports` shows tonight's sessions; the dashboard waterfall showed the rings.
- [ ] If a run aborted: read `abort_reason`, back off the offending wedge by hand, decide.

Doors

- [ ] `show_mode(true)` — refuses scene recall/save and ring-outs, ±3 dB per move.
- [ ] `snapshot_desk("doors")`.
- [ ] Tablet: X32-Edit in one app, dashboard in a browser tab, screen lock off, charger in.

## Changeover (e.g. The Molecules → GravelAxe)

- [ ] `snapshot_desk("pre-<band>")` — the undo for the whole changeover.
- [ ] `show_mode(false)` (scene recall is refused in show mode, by design).
- [ ] Outputs down or muted the way you normally do a changeover (physically or X32-Edit).
- [ ] `recall_scene("<band>")` → read the `action_summary` back to yourself: right slot, right
  name, right "currently loaded" → confirm → result says **verified**.
- [ ] `get_current_scene` agrees; `get_main` — mains fader/mute as the scene expects.
- [ ] Spot-check three strips against X32-Edit (`get_channel`, `get_channel_sends`).
- [ ] `diff_snapshot("latest")` against the pre-changeover snapshot lists what the scene
  changed — if it lists *more* than the band's differences, something is off.
- [ ] Patch plan for the new band: `discover_mics` on their wedges if a ring-out is planned
  before they start (only if there is time and the stage is silent — otherwise skip).
- [ ] Tweaks during line check are ordinary Tier-1 moves; if the scene should keep them,
  `save_scene(index, name)` (confirm; it says whether it overwrites) **before** doors.
- [ ] `show_mode(true)` again. `snapshot_desk("<band>-doors")`.

## Mid-set

- [ ] Show mode is **on**. Moves are ±3 dB per call; `force` only when you explicitly say so.
- [ ] Useful and safe: `adjust_fader`, `adjust_send` ("more kick in Tony's ears"), `mute`/`unmute`
  on channels, `set_eq_band` small cuts, `get_meters`/`get_rta` to look.
- [ ] Not available and not wanted: scene recall/save, ring-outs, `setup_ringout_eqs`
  (`SHOW_MODE_BLOCKS`). `restore_snapshot` **is** available — it is the undo.
- [ ] **Something is wrong now (feedback, wrong output, loud noise):** physical mute / fader,
  or X32-Edit on the tablet. Then, if you want everything gone at once, say "kill it" →
  `panic()` mutes Main LR, Main M/C, all 16 buses and 6 matrices in one go. Unmute deliberately
  afterwards (`unmute bus.3`, `set_main_mute("st", false)` with confirmation).
- [ ] **Desk cutout / PSU wobble:** audio through the desk is what it is; the server goes
  DEGRADED (`connection_status`), refuses writes with `NOT_CONNECTED` and reconnects by itself
  with 1/2/4/8 s backoff. Do not fight it from the conversation; fix power, wait for
  `connected`, then `diff_snapshot("latest")` to see whether anything changed.
- [ ] A move that "did not happen": read the tool's `summary` — clamped to a limit, refused as
  `RELATIVE_TOO_LARGE`, superseded by a newer move on the same fader, or `RATE_LIMITED` in a burst.

## Post-show

- [ ] `snapshot_desk("post-show")`.
- [ ] Keep tweaks? `show_mode(false)` then `save_scene(index, name, notes)` (confirm).
- [ ] `export_patch_plan("patches/<band>-<date>.yaml")` — names/colours/sources back to the file,
  mic/owner/monitor_bus preserved.
- [ ] Copy `snapshots/` and `ringout_reports/` for the night somewhere safe (they are gitignored).
- [ ] `show_mode(false)`, `disconnect` (or just quit the client), power down in the usual order.
- [ ] Note anything the server got wrong or refused unexpectedly → `docs/research/` UNCONFIRMED
  items or an issue.

---

## M5 — real-desk verification protocol

Goal: prove that what the fake desk taught us holds on the real X32 Rack (+ SD16), with
X32-Edit open beside it as the reference. Do this in the studio with nothing on the PA that
matters. Firmware version: ______ (from `connection_status`).

Setup

- [ ] `snapshot_desk("m5-start")` first. Everything below is undone by `restore_snapshot` at the end.
- [ ] `discover_consoles()` finds the desk (limited broadcast `255.255.255.255` is UNCONFIRMED in
  the research — record whether it answered; if not, `connect(<ip>)` still works).
- [ ] `connect` → console name from `/xinfo`, model, firmware, OSC server version (record
  `server_version`: research expects `V2.07` on FW 4.x — UNCONFIRMED).

Reads match the UI (compare each with X32-Edit)

- [ ] `get_channel(1)`: name, colour, source, fader dB (to 0.1), mute, pan, EQ bands
  (type/Hz/dB/Q), comp, gate, insert.
- [ ] `get_channel_sends(1)`: every send level, on/off, type PRE/POST, pan on odd sends.
- [ ] `get_bus(1)`, `get_main`, `get_strip("dca.1")`, `get_strip("mtx.1")`, `get_eq("bus.1")`
  (6 bands incl. shelf/cut types), `get_dynamics("ch.1")`.
- [ ] Rename a channel from X32-Edit → within 2 s `get_channel` shows the new name (the
  `/xremote` push invalidates the cache). Move a fader on the surface → same.
- [ ] `dump_desk_state()` completes with `missing: []` and in < 5 s (record the time and
  `section_count`, not the 200 sections it returns: ______ / ______ ). Save the lot:
  `snapshot_desk("m5-full")`.
- [ ] Single-leaf `/node` text and FW 4.x column widths: `dump_desk_state(["/ch/01"])` values
  are sane (no `None` where X32-Edit shows a value) — the padded-line parser is FW 2.x-verified.

Writes (Tier 1)

- [ ] `set_fader("ch.1", -10)` from −20: the motor fader **glides** over ~300 ms (not a jump);
  `ramp_ms=2000` glides for 2 s; a second move mid-ramp supersedes the first.
- [ ] `set_fader("ch.1", +8)` → clamped to +5 dB and the summary says so.
- [ ] `adjust_fader("ch.1", -7)` → `RELATIVE_TOO_LARGE`; with `force=true` it moves.
- [ ] `mute("ch.1")` → the channel's mute LED is **on** (wire `/mix/on 0`); `unmute` → off.
  `get_channel` reports `muted` the right way round both times.
- [ ] `set_send("ch.1", 1, -6)` → X32-Edit sends page agrees; `adjust_send` ±2 works.
- [ ] `set_eq_band("ch.1", 2, freq_hz=1000, gain_db=-3, q=4)` → X32-Edit EQ agrees
  (frequency within the desk's 201-step grid).
- [ ] `set_pan("ch.1", -50)`, `set_comp(threshold_db=-20, ratio=4)`, `set_gate(on=True)` → agree.
- [ ] `label_channel(1, name="M5 test", color="RD")` → the scribble strip changes; a 13+ char
  name is truncated and reported (`truncated: true`).
- [ ] The first write of the session produced `snapshots/…-auto-pre-write.json`
  (`connection_status.pre_write_snapshot`).

Guarded operations prompt (Tier 2)

- [ ] `set_main_fader("st", -20)` → `requires_confirmation`; the same call with the token moves
  the main fader (glides). A second use of the token → `BAD_TOKEN`. Wait 61 s → `TOKEN_EXPIRED`.
- [ ] `set_main_mute("st", true)` → prompt → confirm → main mutes; unmute the same way.
- [ ] `set_fader("main.st", -10)` → `GUARDED` (Tier-1 tool refuses a main).
- [ ] `set_channel_config(1, source="IN02")` → prompt; confirm → X32-Edit shows the source;
  set it back. `link=true` on 1-2 → prompt → confirm → the pair links; unlink.
- [ ] `show_mode(true)`: `recall_scene(0)` → `SHOW_MODE_BLOCKS`; `adjust_fader("ch.1", -4)` →
  `RELATIVE_TOO_LARGE`; `show_mode(false)`.

Scenes

- [ ] `list_scenes` matches the desk's scene list; `get_current_scene` matches.
- [ ] `save_scene(<empty slot>, "M5 test")` → prompt → confirm → the slot appears in the desk's
  list with that name.
- [ ] `recall_scene("M5 test")` (by name) → prompt → confirm → `verified: true`; the desk shows
  the scene loaded. Record whether `/-action/goscene` produced any reply (UNCONFIRMED).
- [ ] Delete the test scene from the desk afterwards (the server has no delete tool — by design).

`panic()` < 200 ms

- [ ] With all outputs safe: `panic()` → `elapsed_ms` ______ (target < 200 ms), `count` 24,
  `delivered: "confirmed"`; every bus, matrix and both mains show muted on X32-Edit.
- [ ] Unmute: `unmute("bus.1")` … and `set_main_mute("st", false)` with confirmation.

Snapshot / restore round trip

- [ ] `diff_snapshot("m5-start")` lists exactly the changes made above, in English, nothing else.
- [ ] `restore_snapshot("m5-start")` → preview lists them → confirm → `written == lines`,
  `failed: []`; `diff_snapshot("m5-start")` → "no changes"; X32-Edit agrees.
- [ ] `restore_snapshot("m5-start", scope="ch.1")` after one more fader move restores only ch 1.

Degraded handling

- [ ] Pull the desk's network cable (or power-cycle it): within ~15 s `connection_status` →
  `DEGRADED`; `set_fader` → `NOT_CONNECTED` immediately (no hang); `get_channel` → `TIMEOUT`
  within ~2 s. Reconnect the cable: state returns to `connected` by itself; log shows the backoff.
- [ ] `panic()` while degraded → `delivered: "unconfirmed"` and it returns promptly.

Dashboard

- [ ] `http://<pc-ip>:8032/` on the tablet over Wi-Fi: connection panel says connected with
  the console name; `get_rta("ch.1")` while talking into ch 1 moves the bars; the event log
  shows the writes made above.

Wrap-up

- [ ] `restore_snapshot("m5-start")` if anything is still different; `label_channel` back.
- [ ] Update the UNCONFIRMED items in `docs/research/` with what was observed (broadcast
  discovery, `server_version`, single-leaf node text, `goscene` reply, name truncation).

## M7 — CFS² studio protocol

Goal: the closed loop that works on the fake desk works on real air. Studio, one or two
wedges (or a mono IEM mix played through a speaker), one dynamic vocal mic, nobody playing.
Hearing protection to hand; start with the wedge master low.

Provisioning

- [ ] `snapshot_desk("m7-start")`.
- [ ] Patch plan for the session with `mic`, `owner`, `monitor_bus` filled in for the test mic(s).
- [ ] `validate_ringout_eqs([<wedge bus>])` → NOT OK (fresh desk) with a clear reason.
- [ ] `setup_ringout_eqs([<wedge bus>])` → prompt lists the FX slot (5–8), the GEQ2 load and the
  insert → confirm → X32-Edit shows the GEQ2 in that slot and the bus insert set to `FXnL`/`R`,
  on. Run it again → "already set up", no prompt (idempotent).
- [ ] `validate_ringout_eqs` → OK, flat. With two wedges: both on one dual slot (L and R sides).

Mic discovery and preflight

- [ ] `discover_mics(<bus>, patch_file=…)`: the test mic is `include: true` (unmuted, send
  above −40 dB, physical preamp); a DI or a muted channel is excluded with a reason; mute-group-6
  cross-check reported. Nothing got unmuted.
- [ ] Mute the test mic → `ring_out(<bus>)` first call → `PREFLIGHT_FAILED` ("no open mic").
  Unmute it. Mute the bus master → `PREFLIGHT_FAILED`. Unmute. Master at −∞ → blocked.

Feedback watch (human-driven)

- [ ] `feedback_watch(<bus>, notch_budget=3)` → armed; the dashboard shows mode `watch`, the bus,
  the RTA pointed at it (`rta.verified: true`; if not, record `stat_actual`).
- [ ] Bring the wedge up slowly by hand until it just starts to ring: the ring shows as a
  streak on the waterfall, the candidate confidence climbs, and **within ~100 ms of tripping** a
  notch appears (dashboard marker; X32-Edit shows −3 dB on the GEQ band at that frequency).
  Keep the level: the notch deepens to −6, −9 and no further.
- [ ] Sing a sustained note at the ring frequency, then hold it → **no** notch (plateau = no growth).
- [ ] `feedback_watch_stop()` → report saved; `get_ringout_report(id)` lists the notches; the
  Markdown rendering reads sensibly.

Automatic ring-out

- [ ] Flatten the GEQ (or `restore_snapshot("m7-start", scope="…")` for the FX slot) and put the
  wedge master at about −20 dB.
- [ ] `ring_out(<bus>, target_gain_db=-6, notch_budget=4)` first call → the plan: start level,
  target, steps, the open-mic list, warnings → confirm.
- [ ] Watch: `cfs_status` / dashboard stages `RAISE → HOLD → NOTCH → VERIFY → RAISE …`; the master
  moves in 1 dB steps with 1.5 s dwell; each notch is followed by an audible drop; the run ends at
  the target or the budget, **backs off 3 dB**, and the report lists start/peak/end levels.
- [ ] The bus master never exceeds 0 dB even with `target_gain_db=+3` (clamped).
- [ ] `ring_out(<bus>, target_gain_db=-30)` on a bus already at −20 dB is **refused**
  (`BAD_ARGUMENT`, "already at") and the fader does not move; so are `step_db` below 0.1 dB and
  above 6 dB.
- [ ] `validate_ringout_eqs` now reports `matched_session` = this run.

Abort paths

- [ ] Start another `ring_out`; call `feedback_watch_stop()` mid-run → stage `ABORT`, master
  backed off 6 dB, report saved with `aborted: true`.
- [ ] Start another; pull the desk's network cable during RAISE → the master is restored to its
  starting level (raw retries, check on the surface once the cable is back), `cfs.abort` /
  `cfs.restore` events in the log, report says aborted; the server reconnects.
- [ ] Budget exhaustion: `notch_budget=1` and a stubborn ring → deepen to −9 dB, then `ABORT`
  with backoff rather than a runaway.
- [ ] Turn `show_mode(true)` on *during* a run → the abort back-off still moves the master the
  full 6 dB (lowering writes are forced; the ±3 dB clamp must not leave a wedge ringing).

System run

- [ ] `ring_out_system()` (no plan) → one prompt listing every bus with a valid GEQ then Main LR
  → confirm → stages run in order (`feedback_watch_stop()` between two stages stops the run before
  the next bus is raised), one consolidated report with per-bus sections; a bus that
  fails preflight is skipped and says why.

Show mode and read-only guarantees

- [ ] `show_mode(true)` → `ring_out`, `ring_out_system`, `setup_ringout_eqs` → refused
  (`SHOW_MODE_BLOCKS`, even with a token). `feedback_watch` is Tier 1 and is **not** refused
  by design — confirm that, and that its notches are still cuts only; `show_mode(false)`.
- [ ] The dashboard never changed anything: `diff_snapshot` shows only the GEQ/master/insert
  changes the tools reported.

Wrap-up

- [ ] Decide whether to keep the ring-out GEQs in the studio scene (`save_scene`) or
  `restore_snapshot("m7-start")`.
- [ ] Note the real detect-to-cut latency, false positives/negatives, and any RTA source
  verification mismatches → thresholds in `device.yaml` (`detector`, `ringout`) or research.
