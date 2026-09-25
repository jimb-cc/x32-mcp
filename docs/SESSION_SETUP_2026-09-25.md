# Session setup runbook — The Molecules, 2026-09-25 evening

*Jim's setup prompt of 2026-09-25 14:30 mapped onto the server's tools, including the ones built that afternoon
(`feat/tools-*`, integrated the same day). Run it from a fresh session so the MCP server process carries the new tools;
every Tier 2 step returns a confirmation summary first and needs Jim's yes. Nothing here touches input routing blocks
other than 17–24, channel processing, Main LR or FX.*

## Step 0 — backups (before any write)

* `save_scene(4, "PRE-MCP-BACKUP")` — Tier 2, one yes. Do not use slots 0–3.
* `snapshot_desk("pre-molecules")` — the server's whole-desk JSON (Tier 0); `restore_snapshot` undoes from it.
* `.scn` export: from X32 Edit (or the desk to USB) until `export_scene_file` exists; keep the file with the repo's data.

## Step 1 — bus config (buses 1–6, the IEM mixes)

* `get_routing()` → check `bus_links`; for every linked pair among 1–6: `set_bus_link(<odd>, on=False)` (Tier 2).
* Bus masters stay at −∞ (nothing to do; `get_bus(n)` to confirm).

## Step 2 — send tap points

* For ch in 1..17 and bus in 1, 3, 5: `set_send_tap("ch.<n>", <bus>, "PRE")` — the tap lives on the odd send of each
  pair, so three calls per channel cover buses 1–6 (51 calls). Buses 7–16 untouched.
* Send levels stay at −∞ except channel 17 (step 4). `get_channel_sends("ch.<n>")` to spot-check.

## Step 3 — bus processing (each of buses 1–6)

* EQ: `set_eq_band("bus.<n>", 1, type="LCut", freq_hz=100, on=True)`; then bands 2–6 flat:
  `set_eq_band("bus.<n>", b, gain_db=0)` for b in 2..6.
* Limiter: `set_comp("bus.<n>", on=True, threshold_db=-6, ratio=100, attack_ms=0, release_ms=80, knee=0, makeup_db=0)`
  (ratio 100 is the desk's LIM step).

## Step 4 — talkback on channel 17

* `set_input_block("17-24", "UIN17-24")` — Tier 2.
* `set_user_in(17, "IN04")` (local XLR 4) — Tier 2.
* `set_channel_config(17, source="IN17")` — Tier 2 (channel 17 listens to input slot 17 of its block).
* `label_channel(17, name="TALKBACK", color="RD")`.
* `set_main_assign("ch.17", lr=False)`.
* For bus in 1..6: `set_send("ch.17", bus, -6, on=True)`; `set_send_tap("ch.17", 1, "PRE")`, `(…, 3, "PRE")`, `(…, 5, "PRE")`.
* `set_gate("ch.17", on=False)`; `set_comp("ch.17", on=False)`; EQ flat: `set_eq_band("ch.17", b, gain_db=0)` for b in 1..4.

## Step 5 — output verification (read, fix only if wrong)

* `get_outputs()` → expect out 1–6 = MixBus 1–6 POST, out 7 = Main L, out 8 = Main R; AES50A block 1-8 = OUT1-8.
* Fix with `set_output(<n>, source="MixBus 03", pos="POST")` (Tier 2) or, for the AES50 block, X32 Edit (no tool yet).

## Step 6 — engineer monitoring

* `set_solo_mode(buses="AFL")`.
* `set_aux_output(5, source="Monitor L")`, `set_aux_output(6, source="Monitor R")` — Tier 2.

## Step 7 — verify and save

* `dump_desk_state(sections=[...])` over `/bus/01..06`, `/ch/17`, `/config/routing`, `/config/userrout`, `/outputs`,
  `/config/solo` → the address → expected → actual table (the assistant prints it; mismatches flagged).
* `save_scene(5, "MOLECULES-LIVE")` — Tier 2. `.scn` export as in step 0.

## If anything goes wrong

`restore_snapshot("latest")` (Tier 2) puts the desk back to the step-0 snapshot; `recall_scene(4)` does the same from the
console's own slot; `panic` mutes everything at once.
