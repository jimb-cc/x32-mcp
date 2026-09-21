# Appendix G — Read-after-write: site inventory, three designs, two judgments

## G1. Site inventory
# Read-after-write / write-then-conclude inventory — x32-mcp @ a408a2a (src/x32mcp)

Saved to: <review-reports>/raw-inventory/inventory.md

Scope: every place a write (conn.set / slash / send_raw / Desk._write / _write_value / set_geq_band / provisioning / scene load-save / restore / ramps / RTA prefs) is followed — immediately, or within a bounded time by design — by a read of the same or a dependent address whose result feeds a decision, verification, returned value or cache fill. Protocol ground truth (docs/research/transport.md): SET has no ack (§5.2); the sender never sees its own /xremote echo (§4.2, quirk 8); `/ ,s` IS echoed verbatim to the sender and is recommended as flow control (§6.6); real-desk reply latency and whether a SET is applied before the next datagram is served are UNCONFIRMED (§5.4). FakeDesk applies every write synchronously and in order, so none of the races below can currently be reproduced.

Severity key: **A** = wrong action on the desk (audible, possibly loud); **B** = wrong action but bounded/benign (redundant write, one extra −3 dB step, safe-direction back-off); **C** = wrong report / wrong refusal; **D** = latent. Gap key: **0** = read datagram follows the write with nothing but Python between; **RL** = plus ≤ one rate-limiter slot (≤20 ms); **RT** = separate MCP tool calls (0.3–3 s with an LLM; ~10 ms when pipelined/scripted, as M5 was driven). "5/50/500" = desk applies the write 5/50/500 ms after receipt (or serves the read from pre-write state).

**Amplifier that applies to every Desk-cache read below (§2.1):** `Desk._read_sections` (desk.py:303-353) caches whatever the desk answered for `read_cache_ttl_s` = 2.0 s (device.yaml:739). Our own write drops the section *before* the read, so the first post-write read goes to the wire — and if the desk answers pre-write state, that stale section is served from `Desk._cache` for the next 2 s. A 50 ms desk apply latency becomes a 2 s staleness window; nothing retires it early (the desk never pushes our own change back, quirk 8).

## 1. Sites

### 1.1 provision.py — GEQ setup / validation / preflight (the observed GEQ_VALIDATION_FAILED)

| # | file:line | write | read (dependent) | gap | 5 / 50 / 500 ms | mitigation | sev |
|---|---|---|---|---|---|---|---|
| P1 | provision.py:518 → 530 (→377, 398) | `desk.set_fx_type(slot,"GEQ2")` = `/fx/N/type ,i` (desk.py:1399) | `validate_ringout_eqs` → `_FxCache.get` → `desk.get_fx(slot)` reads `/node fx/N` + `/node fx/N/par` (desk.py:673-679); verdict "FX slot N holds 'X', not a GEQ/TEQ" (provision.py:400-401) | 0/RL (insert writes between) | 5: usually passes; 50/500: type reads old effect → `ok=False` → server raises **GEQ_VALIDATION_FAILED** (server.py:1601-1607) for a setup that succeeded. An FX load is the slowest apply on the desk — at least as likely as insert/on. Also `/fx/N` may read new type while `/fx/N/par` still prints the old effect's raw floats → `_geq_token_db` (desk.py:702-709) parses "0.5" as +0.5 dB → `flat=False`, bogus `notches`, garbage `bands_db` → C3. | none | C (B via C3) |
| P2 | provision.py:528 → 530 (→377, 403-404) | `desk.set_insert(key, sel, pos="PRE", on=True)` = `/…/insert/sel`, `/pos`, `/on` (desk.py:1380-1384) | `validate_ringout_eqs` → `desk.get_inserts()` → `/node …/insert` for ~80 strips (desk.py:711-720); verdicts "no FX insert" (sel) / "insert bypassed (insert/on is OFF)" | 0 (target strip's /node goes out 5–30 ms after the SET on LAN) | 5: mostly passes; 50: flaky; 500: **the HANDOVER §4b defect**. The stale `/…/insert` section is then cached 2 s, so an immediate `validate_ringout_eqs` retry fails too. | none | C |
| P3 | provision.py:514, 520-524 (idempotency) | previous apply/set_insert/set_fx_type (RT) | `get_fx().type == fx_type` → skip; `cur[k] != item[k]` → write | RT | stale → redundant writes reported as writes (B); or skips a needed write if operator changed the insert <2 s ago and it is cached → validate fails (C) | none | B/C |
| P4 | provision.py:633-636 (`preflight`) | earlier `set_fader`/`unmute` of the bus (RT), or previous ring-out BACKOFF | `desk.get_strip(bus)` → `master_db`, `muted` → blockers; `master_db` becomes CFS `start_master_db` (cfs.py:892) | RT | wrong blocker (C) **or** wrong `start_master_db` → C1 (A) | none | C→A |
| P5 | provision.py:637 | `setup_ringout_eqs` just before `feedback_watch`/`ring_out`; notches of a just-stopped watch | GEQ verdict + `bands_db` | RT | PREFLIGHT_FAILED "insert bypassed" after a successful setup (C); stale par → `existing_cuts` empty/wrong → C3 | none | C |
| P6 | provision.py:564-582 (`discover_mics`) | earlier `unmute`/`set_send`/`set_source`/`set_fader` | `dump(grp)`, `get_strip`, `get_sends`, `headamp_index_for` → `include`, "no candidate mics", the open-mic list the operator confirms | RT | wrong candidate list / blocker | none | C |
| P7 | provision.py:444-459 (`plan_setup`, recomputed in the confirmed call, server.py:1589) | inserts/types changed by a previous setup or the operator | `get_inserts`, `get_fx` → slot allocation | RT | plans a load into a slot already GEQ2 (B) or believes free a slot the operator just took → overwrites an in-use FX after confirmation (A edge) | Tier-2 summary only | B/A(edge) |

### 1.2 cfs.py — CFS² sessions

| # | file:line | write | read (dependent) | gap | 5 / 50 / 500 ms | mitigation | sev |
|---|---|---|---|---|---|---|---|
| C1 | cfs.py:669 → 892 → 1174-1176 → 1119-1124 (→ desk.py:991, 966-969) | (a) a `set_fader bus.N` issued just before the confirmed `ring_out` (the tool tells the model to "lower the master or raise the target", cfs.py:679-680); (b) previous RAISE | (a) `preflight` → `get_strip` → `pf.master_db` → `ses.start_master_db`; first RAISE writes **absolute** `master_db + step` via `set_level(ramp_ms=0)` (no relative guard on absolute moves, desk.py:959-964) | (a) RT; (b) dwell 1.5 s | (a) stale master (reads −10 while desk is at −30) → first raise writes −9 dB = **+21 dB jump on a live PA**. 5/50: pipelined calls; 500: fast agent loop; certain whenever a stale post-write read of `/bus/NN/mix` was cached <2 s earlier. (b) `before` in `set_level` only feeds `before_db` (ramp_ms=0), loop immune. | none; loop trusts `ses.master_db` belief | **A** |
| C2 | cfs.py:1176-1194 | RAISE `set_level(bus, nxt, ramp_ms=0)` | none — `after_db` is **not a read-back**: `_move_level` returns the clamped request (desk.py:970-974); the comment at cfs.py:1186-1187 is a wrong belief. Conclusions: `ses.master_db`, "master clamped", `max_master_db`, GBF | write-then-conclude | late/lost SET invisible; report's start/end/max/GBF are what we sent; on loss BACKOFF writes an absolute level 3 dB under a believed max = a real raise of up to n·step−3 dB in one write | none | C, A on loss |
| C3 | cfs.py:886-889 | notches from an earlier session (RT) or P1 garbage | `existing = pf.geq.bands_db` → `NotchController(existing)` and `_DeskGeqWriter.gains` → `validate_notch(gains.get(band,0), gain)` baseline (cfs.py:413), `nc.plan` depth | RT | stale-flat: re-writes −3 on a band already −3 → no change → VERIFY fails → deepen: one wasted 1.5 s howl window, phantom notch in report, budget off by one; stale-garbage: baseline positive → legitimate cut refused (C). Same code path as REVIEW_BRIEF §4. | none | B/C |
| C4 | cfs.py:911 → 916-923 (`_arm`) → 1016-1044 (`_consume`) | `set_rta_source`: `/-prefs/rta/source`, `/pos`, maybe `/options`, `/autogain`, `/det` (meters.py:888-923) | `/meters/15` frames start immediately, every frame attributed to `ses.target`, detector armed on frame 1 | 0 | 50: first frame from old source; 500: ~10 frames of the **previous RTA source** (auto-gain possibly still on, det RMS) → persistence 3 / override 6 frames satisfiable → NOTCH on the new bus's GEQ for a ring that is not on it; burns budget + a VERIFY cycle | `rta.verified` computed (C5) but **ignored**; `_arm` proceeds regardless | **B**, C |
| C5 | meters.py:888-889 → 926-935 | `/-prefs/rta/source`, `/pos` | `/-stat/rtasource` (firmware-mirrored) vs `rta_stat_expected` → `verified` → `cfs.state rta_verified`, `get_rta().rta.verified`, warning | 0, then retry after **sleep(0.1)** | 500: `verified=False` though it switched (C); a previous session on the same bus makes a stale read look verified when the write was lost | ad-hoc retry ×2 / 0.1 s | C |
| C6 | meters.py:901-903, 913-915, 921-923 | RMW of `/options` bit 5, `/autogain`→0, `/det`→1 | reads precede writes; `*_cleared` flags are write-then-conclude; a second `set_rta_source` in the window (get_rta→feedback_watch) re-reads pre-write → rewrites | RT | benign duplicates, flags reported twice | none | B |
| C7 | cfs.py:1206-1213, 1221-1252 | `set_geq_band` = `/fx/N/par/NN ,f` (desk.py:1431) | RTA frames: drop ≥6 dB on 2 consecutive frames within 1.5 s of the write; `level0` from last pre-write frame | 1.5 s by design | 500: a third of the window gone before the cut exists → false "not tamed" → **deepen −3 dB** (B); at −9/budget → **ABORT**, 6 dB master back-off, wrong report (C). POST insert → read never reflects the write. | window + consecutive-frame rule | B/C |
| C8 | cfs.py:1338-1348 (`_restore_master`) | `send_raw(fader, raw)` | `conn.get(fader)` right after each send, match within 1/1023 → `restored=True` | 0, every 0.5 s ≤10 s | pre-write served → one more round (fine). False positive: if our raises were never applied / a matching old value is served, `restored=True` on the first read while late raise datagrams may still land → master left raised, report says restored | the only read-your-writes loop in the repo | C (A edge) |
| C9 | cfs.py:1352 | same | `Desk.invalidate(address)` only after the loop; reads during it cache 2 s | — | dashboard/model sees and caches intermediate level | invalidate at end | C |
| C10 | cfs.py:1109 (`_snapshot_stage`) | writes from prep (RT) | `desk.dump([bus])` → `ringout-pre-<bus>` snapshot = undo point | RT | stale pre-snapshot → restoring after abort re-applies a level the operator had changed (A deferred, bus-scoped) | none | C/A-deferred |
| C11 | cfs.py:849-877 (`_calibrate_floor`, called from `_open_session`:890 — before `_arm`) | today none (frames idle → returns after 0.3 s); with an injected running source it samples the **previous** RTA source | max peak → `min_level_db` for the session | 0.3 s / ≤2 s | if moved after `_arm` (REVIEW_BRIEF §4) it becomes C4-class: first 10 of 40 frames from the old source → gate from the wrong signal → deaf or trigger-happy detector | first-frame timeout only | C→B (future) |
| C12 | cfs.py:1412 / server.py:1795-1797 | `setup_ringout_eqs` just before `ring_out_system()` | validate 16 buses + main → stage list | RT | NO_STAGES or a bus silently omitted | none | C |

### 1.3 desk.py — facade (read-before-write = read-after-the-previous-write)

| # | file:line | write (earlier) | read | gap | 5 / 50 / 500 ms | mitigation | sev |
|---|---|---|---|---|---|---|---|
| D1 | desk.py:991 (`set_level`) → 967 `ramp_steps(before_db, after, ms)` → 937-946 | previous level write/ramp final step/CFS raise on the same address | `before = _leaf(address)` (section invalidated → wire read → cached 2 s) = **ramp start** | RT (0 when pipelined "set −40" then "set −38") | pre-write served (0 dB while desk at −40): `ramp_steps(0,−38,300ms)` → first step ≈ −2.5 → **fader jumps −40 → −2.5 then glides down**. Same for `set_main_level` (desk.py:1229; token does not protect), sends, mlevel; and for 2 s after any stale read | none | **A** |
| D2 | desk.py:1007-1009 (`adjust_level`) | same | `before = _leaf(address)` = **base of the relative move** | RT | after `set_fader ch −30`, `adjust_fader +3` reading pre-write 0 dB writes **+3 dB**; two quick +3 nudges → second lands on −7 again. The ±6 dB guard checks `delta`, not the resulting jump | none | **A** |
| D3 | desk.py:1021, 1235 (`set_mute`, `set_main_mute`) | previous mute | `was = _leaf(mix/on)` → `was_muted` / "(was already muted)" (server.py:925, 936) | RT | wrong "was" text | none | C |
| D4 | desk.py:1256-1262 → 1264-1278 (`recall_scene`) | `/-action/goscene ,i` | poll `conn.get("/-show/prepos/current")` (uncached) every 50 ms ≤2 s → `verified` | 0…2 s | (i) recalling the already-current scene: satisfied by the **pre-write** value on poll 1; (ii) `prepos/current` flipping does not prove ~2000 params applied (order UNCONFIRMED) → reads right after may show and cache mid-recall state; (iii) show_control ≠ SCENES → unverified | poll + wholesale `invalidate()` (1279) | C |
| D5 | desk.py:1318-1319 (`restore`) | any tool write shortly before "undo"; the pending call's `_diff` (server.py:1248) uses the same dump | `live = dump(scope)` (wire, primes cache) → `restore_plan(snap, live)` **skips sections that look equal** | RT | pre-write served for the section to undo → "matches" → **not written**; result `written n/n, failed []` → undo silently didn't undo (A by omission); preview says "0 setting(s) would change" (C) | none | **A/C** |
| D6 | desk.py:1323-1334 | `conn.slash(line)` per section, awaiting echo (connection.py:542-554) | `written += 1`; `invalidate()`; model's follow-up `diff_snapshot` (M5 did this) | echo-gated; RT | if echo = "received" not "applied" (UNCONFIRMED; emulator applies first) → phantom remaining diffs (C), re-run rewrites (B); echo lost ×3 → `failed` though applied (C); `invalidate()` not in `finally` → cancelled restore leaves dump-primed pre-restore sections cached ≤2 s | echo await + retries | C/B |
| D7 | desk.py:1298-1305 (`save_scene`) | `/save ,siss` (real reply) | reply → `ok`; `_drop` slot; follow-up `list_scenes`/`_resolve_scene` by name (server.py:578, 1162) | reply; RT | slot node lags → `recall_scene("<new name>")` → BAD_ARGUMENT or resolves the old holder of the name (C; A if operator confirms); retry re-sends `/save` (benign) | reply | C |
| D8 | desk.py:1399-1400 → 1417-1424 (`set_fx_type` → `set_geq_band`) | `/fx/N/type` | `_section(/fx/N).type` decides dual/stereo par layout or NOT_A_GEQ | RT | stale → NOT_A_GEQ right after setup (C); stereo↔dual confusion → wrong par range = cut on the other bus's side (A edge). `_drop(/fx/N/par)` fixes the cache, not the desk | dependent-section drop | C/A(edge) |
| D9 | desk.py:728, 736, 765 (`headamp_index_for`) → 1363-1369 (`set_phantom(target)`) | `set_source`/`set_channel_config`/user-routing change | `config/source`, `/config/routing/*`, `/config/userrout/in/NN` → head-amp index → **`/headamp/NNN/phantom` write** | RT | stale → **48 V on the wrong preamp**; 500 ms class or within the 2 s cached-stale window; Tier-2 summary names the channel, not the head amp | none | **A** |
| D10 | desk.py:543-583 (`resolve` by name → `get_names`) | `label`/`apply_patch_plan` | every `/node …/config` in the family → which strip a name means → target of the next write | RT | after re-label ("Tony" moved ch6→ch5) "bring Tony up" resolves to the **old** strip (A) or UNKNOWN/AMBIGUOUS (C); 33 stale config sections cached 2 s | none | A/C |
| D11 | desk.py:420-464 etc. (all Tier-0 getters) | any write | value returned to the model; **HANDOVER "read after label_channel returned the previous name"** = `label` (desk.py:1170) → `get_channel` → `/node ch/NN/config` | RT | wrong report, cached 2 s so a re-read confirms it; model re-writes (B) or reports failure (C) | none | C |
| D12 | desk.py:871-884 `_write` / 886-895 / 1050-1081 `set_eq_band` / 1154-1182 `label` / 1372-1387 `set_insert` / 1403-1431 `set_geq_band` / 1184-1220 `panic` | the write itself | none — `applied = to_value(to_raw(value))` = what we sent | write-then-conclude | lost datagram / desk-side snap or refusal reported as applied; `panic()` honest about `delivered` but never reads back the 24 mutes | none (no ack exists) | C (A for panic on loss) |
| D13 | desk.py:648-667 `dump` (+ `snapshot_desk` server.py:1185, `dump_desk_state`, `export_patch_plan` patches.py:633+, `_diff` server.py:1211, `ensure_pre_write_snapshot` 846-862) | any write shortly before (RT; ~0 when scripted, as M5's snapshot→restore→diff) | full sweep → snapshot file / diff / exported patch; primes Desk._cache for everything | RT | stale sections persisted: a post-soundcheck snapshot that missed the last write **re-applies the pre-write value on restore** (A deferred); phantom/missing diffs (C); exported plan with old names (C→B) | none | C/A-deferred |
| D14 | desk.py:922-946 (`_run_ramp`) | step i | no read; next step 20 ms later; dedupe by our quantisation | 20 ms | not a RAW race; the one place assuming the desk keeps up with 50 unacked writes/s; dropped final step → fader lands short, reported landed | final step always written | C |

### 1.4 server.py / patches.py

| # | file:line | write | read | gap | effect | mitigation | sev |
|---|---|---|---|---|---|---|---|
| S1 | server.py:1510 → 1514-1517 (`get_rta(target)`) | `set_rta_source` via `_RtaPrefsWriter` | `LiveMeters.start()` + `average_frames(n=10)` = next 500 ms of `/meters/15`, labelled `rta_source=target` | 0 | 500: **whole average is the previous source** (maybe auto-gain-normalised) reported as the target's spectrum with "peaks" to EQ | `verified` returned, frames not gated on it | C (→A via operator) |
| S2 | server.py:1381 → 1395-1402 (`set_channel_config`) | `set_source`, raw `conn.set(/config/chlink/x-y)` | `get_strip` before (summary); after the link write nothing invalidates the *partner* channel's sections (desk syncs the pair) | RT | partner reads stale ≤2 s (C); D9 if `set_phantom(target)` follows | `write` event drops `/config/chlink` only | C |
| S3 | server.py:1092-1098, 1112-1118, 1134, 1162 (Tier-2 summaries) | earlier writes | reads that build `action_summary` | RT | operator confirms a summary misstating current state | none | C |
| S4 | patches.py:546 → 560-591 (`apply_patch_plan`) | `label_channel`/previous apply | `dump(/ch/NN/config…)` → per-row differs? | RT | redundant label writes reported as changes | none | B |
| S5 | server.py:1599-1607 (`setup_ringout_eqs`) | P1/P2 | turns `ok=False` into a hard GEQ_VALIDATION_FAILED envelope | 0 | user-visible face of P1/P2 | none | C |
| S6 | server.py:198-201 vs connection.py:879-889 | — | `App.connect` invalidates Desk cache; `_recover()` (auto-reconnect, desk may have rebooted) does not; TTL bounds to 2 s | — | ≤2 s pre-reboot values | TTL | C |

### 1.5 connection.py

| # | file:line | what | effect | sev |
|---|---|---|---|---|
| X1 | connection.py:520-530 `set` | fire-and-forget; `invalidate(address)` + synchronous `write` event (events.py:45-66) — the only "barrier" is "drop my caches" | no ordering/ack; every site above inherits the race | — |
| X2 | connection.py:542-554 `slash` | send `/ ,s`, await verbatim echo, retry | already send-and-await-ack; but (i) no `write` event, no invalidation (Desk.restore compensates wholesale); (ii) waiters FIFO-matched on bare key `/` (`_reply_key` 139-143): fine until a loss, then echo B satisfies waiter A and B is re-sent — a barrier should match the echoed text; (iii) retry re-applies (idempotent) | D/C |
| X3 | connection.py:574-577 `send_raw` | no cache/policy/events; `/meters`, `/xremote`, panic fallback (desk.py:1203), CFS restore (cfs.py:1338) | invisible to Desk._cache unless caller invalidates (panic does; `_restore_master` only at end) | C |
| X4 | connection.py:747-769 retries + 806-834 `_handle_datagram` | a timed-out GET is re-sent; late reply 1 satisfies waiter 2; reply 2 arrives unmatched → treated as a push → `_cache[address]=value` (832) after `set()` invalidated it, and `on_update` → `Desk._on_push` drops the section, spurious `update` event | X32Connection._cache refilled with pre-write data (D: `get_cached` unused in src); spurious section drop (benign) | D |
| X5 | connection.py:581-605 `get_cached` | gen-guarded read-through cache | unused by product code → latent; same TTL amplifier + X4 hole if adopted | D |

## 2. Cache angle

**2.1 Desk._cache (desk.py:213, 303-353).** Own writes via `Desk._write` (879-883): `conn.set` → `conn.invalidate` + synchronous `write` event → `_on_write_event` → `_invalidate_address` → `_drop`; `_write` also invalidates directly. `_leaf_index` covers all 8453 node leaves (incl. `/fx/N/par/NN`, `/…/insert/on`, `/-prefs/rta/*`, `/-stat/rtasource`, `/config/chlink/*`, `/config/userrout/in/NN`, `/headamp/NNN/*`, `/dca/N/*`); `/-action/goscene`, `/-prefs/show_control` have no node (recall invalidates wholesale; show_control read uncached). So the section is always dropped before the next read; the cache does not *cause* violations but **amplifies desk apply latency from N ms to 2 s** (stale reply cached fresh at 336-340) — P2, D1, D2, D9, D10, D11. **Real hole independent of the desk — in-flight join:** `_drop` (268-273) pops the entry and bumps `_epoch` so the in-flight reply is not cached, but leaves the future in `_inflight`; a read starting *after* our write while a pre-write `/node` for that section is in flight joins it (316-321) and receives the pre-write reply (needs concurrency: parallel tool calls; same for `invalidate(None)` 252-256). Raw-connection writes (cfs `self._conn` set_rta_source, `_RtaPrefsWriter`, link write, panic) all pass through `X32Connection.set` → event → dropped (cfs.py:916 invalidate is redundant). `slash()`: no event; `restore` primes with the pre-restore dump (1318) and clears at 1334, not in a `finally`. `send_raw()`: panic invalidates by hand (1209); `_restore_master` after ≤10 s. Pushes disabled/lease lapsed: irrelevant to own writes (never echoed), only lengthens others' staleness to TTL; `_recover` doesn't flush (S6). `dump()` priming guarded by `_dropped_since`/`_inflight`, stamps post-sweep time (TTL + sweep). Dependent addresses not invalidated: `chlink` → partner channel (S2); `/-prefs/rta/*` → `/-stat/rtasource` section (read uncached today); handled: goscene (wholesale), `/fx/N/type`→par (1400), `/save`→slot (1304).

**2.2 X32Connection._cache (connection.py:270, 581-613, 832):** filled by pushes, late replies (X4), `get_cached`; `set()` invalidates per address; gen guard; **no product reader** → latent; holes X4 + TTL amplifier; `slash`/`send_raw` don't invalidate.

**2.3 provision._FxCache (313-325):** per-call memo in validate/plan_setup; never spans a write (apply_setup's final validate builds a fresh one).

**2.4 Descriptor-derived:** static; the dynamic dependency is `/fx/N/par` token meaning decided by the `type` read in the same `get_fx` (678-694) → can disagree during a late FX load (P1); `set_geq_band` layout from a ≤2 s-old cached `/fx/N` (D8).

**2.5 CFS beliefs:** `ses.master_db/max` (C2), writer `gains` + NotchController (C3), `RtaSourceResult` (C5) — write-then-conclude; only `_restore_master` reads back.

## 3. Existing ad-hoc settle / sleep / retry / poll

| where | what | purpose | RAW? |
|---|---|---|---|
| connection.py:747-769 `_request_bytes` | 0.5 s × (retries 2+1); DEGRADED → 1 attempt | lost-datagram retry (GET, /node, /info, /save, slash) | duplicates; X4 |
| connection.py:542-554 `slash` | await `/` echo, retried | only ack-await write | candidate barrier |
| connection.py:318,331 | `/info` retries, `/xinfo` 0 retries | handshake | no |
| connection.py:699-707 `discover` | 2 rounds, sleep half/remaining | broadcast loss | no |
| connection.py:891-899 | heartbeat sleep 8 s | lease | no |
| connection.py:909-954 | watchdog 12 s → `/info` probe; backoff 1,2,4,8 s; `_sleep_or_wake` | liveness | no |
| connection.py:155-183 | `_rearm_reader` call_later ≤0.5 s | Windows proactor | no |
| connection.py:385-390, 720-722 | getaddrinfo wait_for 5 s / 1 s | bounded resolve | no |
| desk.py:1264-1278 `recall_scene` | poll prepos/current 50 ms ≤ 2 s | **verify a write whose reply is UNCONFIRMED** | yes (D4) |
| desk.py:943-946 `_run_ramp` | sleep to step slot (20 ms) | pacing | no |
| policy.py:375-396 `acquire_write` | bucket 50/s, sleep ≤ max_wait_s 1.0 else RATE_LIMITED | budget | incidental |
| meters.py:928-934 `set_rta_source` | verify_attempts 2, sleep(0.1), reads wait_for 1.0 | **RAW settle ("desk applies prefs asynchronously")** | yes (C5) |
| meters.py:437-447 | `/meters` renew 5 s; send wait_for 1.0 | lease | no |
| meters.py:766-792 `average_frames` | n frames or timeout | aggregation | S1 window |
| meters.py:351-364, 526-540, 753-760 | task cancel 1 s; frame pacing | housekeeping | no |
| cfs.py:1324-1351 `_restore_master` | send_raw + read-back every 0.5 s ≤10 s, read wait_for 1.0 | **read-your-writes loop (emergency)** | yes (C8) |
| cfs.py:1126-1133 `_dwell` | dwell_ms 1500 | settle between raise and decision | C1b/C2 |
| cfs.py:1221-1252 `_verify_decay` | ≤1.5 s, 2 consecutive frames ≥6 dB | **write-then-observe verify** | yes (C7) |
| cfs.py:849-863 `_calibrate_floor` | first frame ≤0.3 s else skip; then 40 frames or 2.0 s | floor sample | C11 |
| cfs.py:609,790,944 | stop/close ≤30 s; disarm 1 s | shutdown | no |
| detector.py:100-124 | persistence 3, cooldown 1.0 s, override 6 frames, growth window 60 | detection timing | sets how fast C4 misfires |
| server.py:94-102, 312-321, 440-462, 1724 | tool wait_for 30 s; `_level_timeout`; CONNECT 15; DUMP 60; RESTORE 30+n/40; PREFLIGHT 60; ring-out formula; SYSTEM 400/stage; close 35 | bounding | no |
| webui.py:457, 588-609, 277/282/363 | state poll; demo sleeps; stop timeouts | dashboard | no |
| fakedesk.py:771-781 `_send` | call_later(latency_ms) per outbound datagram | **reply-latency injection** | §4 |
| fakedesk.py:914-922 | meter sleep 50 ms × tf | cadence | no |

No general settle exists; the three real mitigations (recall poll, rtasource retry, restore-master loop) each re-implement "write, read until match or deadline" with different constants (50 ms/2 s; 100 ms/×2; 500 ms/10 s).

## 4. FakeDesk: `latency_ms` is reply latency; where apply latency must go

* `latency_ms` (fakedesk.py:312; `/-fake/latency` 1310-1316) is honoured only in `_send` (771-781): it `call_later`s **already-rendered** bytes. Every inbound datagram is fully processed in `_on_datagram` (750-769) before the next: `_h_param` SET → `_coerce` → `_store` (675-690) → `_commit` (815-820); `_h_slash` → `_apply_node_text` (722-746) → `_store` → echo → `_commit`; goscene → `_side_effects` → `_recall_scene` (1142-1157); `/save` (1159+); `_h_setrtasrc`/`_mirror_rta_stat` (1068-1101); `_after_change` → `_refresh_cuts` (1103-1140) in the same call. A `/node`/GET processed after a SET always renders post-write state even when delayed; meter frames are delayed equally. tests/integration/test_fakedesk.py:581-586 confirms (delays replies). **No §1 race is reproducible today.**
* Injection point: `_store()` (675) is the single choke point for every write path (wire SET, multi-arg node SET 1052-1066, `/` text, scene recall, `/save`, setrtasrc, the `/-stat` mirror, `FakeDesk.set/set_value` 432-450). Split into accept/commit: keep `self.state` as the committed view read by `_h_param` GET (1023-1035), `_node_text`/`_node_values` (697-720), `_meter_values`/`_strip_level` (929-975), `_refresh_cuts`; schedule commit with `call_later(apply_delay(address))` (pushes, `_side_effects`, `_cuts_dirty` at commit time); preserve per-address FIFO; optionally answer GETs from "accepted" state to model a desk that reads-its-writes on the OSC thread.
* Address classes needed: (1) `config/name|icon|color` (D10/D11, HANDOVER label case); (2) `…/insert/on|sel|pos` (P2); (3) `/fx/N/type` — longest; while pending `/fx/N/par` keeps rendering the old effect's tokens (P1, D8); (4) `/fx/N/par/NN` — commit drives `_refresh_cuts`, making C7 testable; (5) `/-prefs/rta/*` plus a *separate extra* delay for `/-stat/rtasource` (`_mirror_rta_stat` 1094-1101, C5) and for `_refresh_cuts`' source strip (C4, S1 — SyntheticRta has one spectrum regardless of source; reproducing "frames from the old source" needs a per-source spectrum or marker); (6) `mix/fader|level|on` (D1, D2, C1, C8); (7) `config/source`, `/config/routing/*`, `/config/userrout/*`, `chlink` (D9, S2); (8) `/-action/goscene`: spread the ~2000 leaf commits and make the `prepos/current` flip order configurable (D4 ii); (9) `/save`: reply vs slot-node commit order (D7).
* `/` echo: today `_h_slash` stores → echoes → pushes (1012-1021) = "echo ⇒ applied". Make it a switch (`on_receipt`/`on_apply`) — that UNCONFIRMED fact decides whether `X32Connection.slash()` can be the general write barrier (§6.6) or only flow control.
* Add a knob to **drop inbound SETs** (`drop_next` 451-453 / `_reply` 792-799 only drop replies; requests are always applied) — C2/C8/D12/D14 concern lost writes, which read-your-writes must also catch.
* Control surface: `/-fake/apply ,sf <class> <ms>` (ack-then-apply like `drop`/`latency`) + `FakeDesk.apply_latency: dict[str,float]`; tests/integration/conftest.py fixtures need no change beyond exposure.

## 5. What should drive the fix (ranked)

1. **A-class — a stale read decides an absolute write:** D1 (`set_level` ramp start), D2 (`adjust_level` base), C1 (ring-out start master → absolute +step), D9 (`set_phantom` by target), D10 (name→target), D5 (restore diff skips the section to undo), P7-edge. Need read-your-writes on the addresses just written (or: never derive an absolute write from a read younger than the last write to that address without a barrier).
2. **The 2 s amplifier (§2.1):** a section read inside the apply window must not be cached as fresh — tag reads within X ms of our own write as provisional, or re-read after the barrier.
3. **C4/S1/C11:** RTA-source changes need their own barrier (`/-stat/rtasource` == expected AND ≥1 frame period elapsed; drop older frames) before frames are attributed to the target — `verified` is computed today and ignored.
4. **P1/P2/S5 (observed GEQ_VALIDATION_FAILED), C5, D4, D7, D11, S3:** report-level; vanish with a generic "await applied" after Tier-2 executor writes (`set_fx_type`, `set_insert`) plus a validate that retries to consistency or deadline.
5. **Write-then-conclude with no read (C2, C3, D12, D14, panic):** decide per site; at minimum fix cfs.py:1186's "read-back" belief and give BACKOFF/ABORT a real master read before writing an absolute level derived from belief.
6. In-flight join hole in `Desk._read_sections` (§2.1) and `slash()` echo keying (X2 ii) — small, real, independent of the desk.

---
## G2. Design: barrier

The design document is complete and saved, and the prototype validates it. Final deliverable follows.

# Read-after-write over fire-and-forget UDP: an ordering-barrier design for x32-mcp

Saved to `<review-reports>/raw-design-barrier/design.md` (670 lines; full text there — this is the dense version). Prototype: worktree `<review-worktrees>/ryw-barrier`, branch `wt/ryw-barrier`, commits `e04bbd1`, `6533d38`, `4126639`, `6896eb4` on a408a2a (+1733/−105, 949 tests green; audit logs `x32ish_run{,2,3}.txt` beside the doc).

## 0. Decisions
1. **Two facts, two primitives.** A reply to request R proves *receipt* of every datagram sent before R (ordering fence) — not *application*. `X32Connection.barrier()` = receipt (nearly free); `settle.poll_until` / Desk *confirm* = observe value until deadline, tri-state. No sleeps.
2. **Not all writes become `/` echo writes.** Leaf SET stays default. `/` only where a node's *leading run* is committed at once (insert `on pos sel`, restore lines, `config "name"`): one atomic datagram, echo distinguishes lost from pending. Ramps/mutes/pans/sends/EQ/GEQ notches stay SET (positional `/` can't address field k without 1..k−1; a lost-echo retry stalls 0.5 s = audible on a 20 ms ramp).
3. **Every reply credits a sequence fence**: `set()`→`tx_seq`; a matched reply advances `acked_seq` to the *first attempt's* seq; `barrier(after)` is free if already acked, else one **`/status`** round trip (transport.md §3.3; unused elsewhere — `/info` belongs to the watchdog, connection.py:923/948, sharing its FIFO would cross-satisfy).
4. **Committing writes confirm by default, streaming never**: Tier-2 executors (insert, fx type, source, phantom, link), labels, RTA prefs, provision, scenes, restore, GEQ notches, final ramp step. Not: ramp intermediate steps, panic phase 1.
5. **Tri-state `Verify = VERIFIED|UNVERIFIED|TIMEOUT` (+SKIPPED), reported not raised.** `GEQ_VALIDATION_FAILED` only when read-back is VERIFIED-fresh **and** wrong; UNVERIFIED → `ok:true, verified:"unverified", stale_possible`, naming leaves the desk hasn't shown; TIMEOUT → `DeskError TIMEOUT`.
6. **Cache: dirty-until-confirmed, never write-through.** Written section dropped + `PendingWrite` registered; a fetched section disagreeing with a live pending write is returned but **not cached**; agreement confirms for free; expiry → WARNING + `desk.verify unverified`, then a passive late-watch keeps it uncacheable while it disagrees and records real latency when it lands.
7. **FakeDesk gets a receipt→visibility latency model** (per-glob delay, `/` echo immediate, pushes at apply, fx-type apply resets 64 pars, inbound-drop fault, `instant`/`x32ish` profiles); the integration suite runs under both — that is the regression net for the whole class.
8. **Resend once, never loop**: a SET not visible after the fence provably passed is re-sent once through the limiter; then UNVERIFIED; desk health is the watchdog's call.

## 1. Model and evidence
H1 (in-order service of one socket) → fence semantics. H2 (synchronous application) is **false on the real desk**: in `apply_setup` (provision.py:503-539) the insert SETs (desk.py:1372-1387→`_write` 871-884→`sendto`) were on the wire before `validate_ringout_eqs`' `/node` reads (provision.py:369-423→desk.py:711-720→303-353; cache correctly dropped via `_leaf_index`, 275-280 — verified it was a fresh wire read), yet the desk answered pre-write state; same for `label` (1154-1182). So a barrier alone would **not** have fixed §4b; observe-until-deadline does. The tests encode H2 too: `settle(conn)` helpers at tests/integration/test_desk.py:41-43, test_cfs.py:75-77. Write states: SENT→RECEIVED(fence/echo)→VISIBLE(read-back)→EFFECTIVE(RTA, cfs.py:1221-1260). Classes (initial deadline/first poll): **S** DSP leaves 0.25 s/0; **insert** 1.0/10 ms; **name** 1.0/10; **struct** `/fx/N/type|source` 3.0/50 + quiesce 0.3 s, no resend, verify type AND par node; **route** 1.0; **link** 1.0 (invalidate both strips); **preamp** 1.5/20; **prefs** 1.0, derived `/-stat/rtasource`; **quiesce** goscene/load 2.0/50, derived `prepos/current`, then `barrier()` must succeed; **none** `-action/*`,`-stat/*` barrier only.

## 2. APIs (implemented in prototype; worktree file:line)
- `settle.py` (new): `Verify` :44 (`ok`, `worst()`), `SettleClass(name, deadline_s, schedule_s, resend_after_polls|None, confirm_default, quiesce_s, match, invalidate)` :67, `DEFAULT_SETTLE` :80, `SettleTable.from_config/for_address` :94 (device.yaml optional `settle:` block, descriptor.py), `PollResult(verify,value,polls,answered,elapsed_ms,stalled,error)` :140, `poll_until(fetch, predicate, *, deadline_s, schedule_s, on_stall, stall_after, timeout_exc, fatal_exc, clock, sleep)` :150, `raw_matches(spec, sent, got)` :213 (half grid step from `spec.scale.steps` + f32 slack; int/enum/str exact).
- `connection.py`: `tx_seq`/`acked_seq` :543-552; `set()->int` :580 (event gains `seq`); `send_raw()->int`; `slash()->SlashReceipt(seq,rtt_ms)` :604, echo keyed by text `"/:<line>"` (`_slash_key` :174), late echo of an abandoned line dropped (:900/:958) with FIFO fallback for non-verbatim echoes, publishes `write`; `barrier(after=None,*,timeout,retries)->BarrierResult(sent,rtt_ms,acked_seq)` :650 (lock-serialised `/status`); `poll(address, predicate,*,deadline_s,schedule_s,per_poll_timeout_s,on_stall,stall_after)->PollResult` :673 (single-attempt GETs; the schedule is the retry policy). Raises only `RequestTimeout`/`NotConnected` from `barrier`; `poll` never raises.
- `desk.py`: `WriteResult(address,value,seq,verify,observed,sends,polls,settle_ms)` :232; `PendingWrite` :256; `_write(..., confirm: bool|float|None, predicate, verify_address, spec)->WriteResult` :1163; `_confirm` :585 (poll + one resend via `policy.acquire_write`, never for slash/derived); `_write_section(t, section, values)->list[WriteResult]` :1785 (one `/` datagram, per-leaf tier check, one limiter slot, confirm by one `/node` per poll); `_reconcile` :541 (cache rule); `_leaf_settled` :467; `settle(prefix=None,*,deadline_s)->{verify,verified,unverified[{address,expected,desk_reports_raw}],timeout,elapsed_ms}` :623; `pending_writes` :488; `quiesce_until`; `_register_line` :1689; events `desk.write{+seq,confirming,via}`, `desk.verify{address,tool,verify,source(poll|observed|push|late),expected,observed_raw,settle_ms,sends,polls,cls}`; `verify_stats`.

## 3. Site changes
`label`→per-field back-to-back + `settle(config)` (or section write); `set_insert`→`_write_section` when fields form a leading run else leaf writes+settle, `verify`/`desk_reports` in result; `set_fx_type`→struct confirm, drop `/fx/N`,`/fx/N/par`, quiesce `/fx/N/par`; `set_geq_band(confirm=0.15)->WriteResult` honouring quiesce; `set_mute/set_send_mute/set_pan/set_main_mute` confirm; `set_eq_band`/`_write_params` settle their section; `_run_ramp` intermediate `confirm=False`, final step confirmed, `verify` in `_move_level`; `before`/`was` reads via `_leaf_settled` (set_level/adjust_level/set_main_level/set_mute/set_main_mute); `dump()` settles first; `restore` registers every leaf of each echoed line and settles → `verify/residual/unanswered`; `apply_setup` (provision.py:518-545) collects verifies, settles, **then** validates; `setup_ringout_eqs` mapping (server.py:1605-1622). Design-only (not prototyped): `recall_scene`, `cfs._restore_master` (cfs.py:1324-1360) and `meters.set_rta_source` (meters.py:926-934 ad-hoc sleep) re-expressed on `poll_until` with derived predicates; `set_source/set_phantom`, server's direct `conn.set` for chlink (server.py:1396-1407) and `_RtaPrefsWriter` (1459-1482) moved into Desk; panic phase 2 (≤0.4 s `node_many` verify + `send_raw` resend of still-ON outputs, never raises); CFS notch `verify` in reports (UNVERIFIED→proceed to acoustic VERIFY, TIMEOUT→ABORT).

## 4. Async-apply hole
Poll the value not the clock (class schedule, first poll delayed for A/Q classes, capped intervals — a 600 ms load on `[…0.2,0.4]` was only seen at 800 ms); derived predicates (`verify_address`); resend only for SET and only after answered-but-stale polls (never for `/`, struct, quiesce); quiesce dependents after structural applies (the one sanctioned, observed-event-keyed delay); class U → `verify:"skipped", receipt confirmed`.

## 5. FakeDesk (fakedesk.py)
`PROFILES` :129; `apply_delay_ms=` :325; `drop_inbound(n, match, writes_only)` :507; `apply_delay_for` :517; `flush()` :534; `_write_leaf` :545 (heap by due, FIFO); `_apply_one` :574 (fx type resets pars); `/` echo immediate; `/-fake/applydelay|flush|dropin`; CLI `--profile`; `X32_FAKE_PROFILE` env in tests/integration/conftest.py.

## 6. Tests (added)
`tests/test_settle.py` (11, fake clock), `tests/test_connection.py` +13 (seq/ack, barrier free/sent/first-attempt credit/silent, blobs don't credit, slash-by-text, late-echo-doesn't-ack-B, non-verbatim fallback, poll tri-state, on_stall once), `tests/integration/test_settle_desk.py` (14): §4b reproduction fixed (verified+valid under x32ish, 0.6–2 s bounded by observation), 5 s delay→unverified-not-failed and nothing pinned stale, real stereo-GEQ misconfig still fails, label waits, stale post-write read not cached + free confirmation, one-datagram insert, partial-field fallback, lost SET resent exactly once / two drops → UNVERIFIED sends==2, silent→TIMEOUT bounded, sync=1 poll, ramp adds ≤1 GET, notch survives type-load par reset, push confirms pending, barrier free after reads.

## 7. Migration order
FakeDesk model+profiles (no product change) → settle.py + connection fence → Desk registry/confirm/settle + `settle:` yaml → §4b (insert/fx type/apply_setup/server) → names/prefs/routing/links into Desk → scenes/restore/panic-2 → CFS verify → level moves → make `x32ish` required in CI + DESIGN.md §8/§13/§14/§18/§19/§0.5 + HANDOVER §4b → M5 measurement replaces provisional deadlines (transport.md §5.4).

## 8. What NOT to do
No blanket `sleep` settles (too long, too short, prove nothing); no `/` echo or per-step confirm on ramps; no write-through overlay; never treat fence/echo as verification; never use `/info` for the barrier; no second `/xremote` socket; no unbounded resend/poll; UNVERIFIED is not an exception; no confirm in panic phase 1.

## 9. Measured (prototype, loopback)
instant: confirmed `set_mute` 0.3 ms, `label`+read 12 ms, `apply_setup`(2 buses) 76 ms/8 datagrams, 300 ms ramp 281 ms; x32ish: 10.8 ms, 83 ms, 1185 ms/20 datagrams, 292 ms; 0 resends, 0 unverified; suite 64→70 s. **Audit under `x32ish`**: 50→29 failures; product races found and fixed beyond §4b: relative fader move from a pre-write `before` (**wrote +2.0 dB where −2.0 was meant**), `dump`/`diff_snapshot`/snapshots recording pre-write state, `restore` "written"=echoed, unconfirmed EQ leaves; remaining 29 = 24 emulator self-tests needing `flush()`, 3 panic read-backs (phase 2), 1 server chlink bypass, 1 test writing raw `conn.set`.

## 10. M5 measurement
`scripts/measure_apply_latency.py`: per class 50× write→2 ms GET-poll→`visible_ms`, "first answered poll stale" count (direct H2 evidence), `/status` RTT, `/` echo verbatim?, echo on unapplicable line?, goscene reply? — sets `settle:` deadlines and closes three UNCONFIRMED items; the same numbers accrue passively from `desk.verify` events.

---
## G2. Design: ryw-overlay

# Read-after-write over fire-and-forget UDP — design from the "read-your-writes overlay" angle

Saved to `<review-reports>/raw-design-overlay/design.md`.
Repo: <repo> @ a408a2a. Prototype (all of §3–§6 below, working, 934 tests green): worktree
`<review-worktrees>/ryw-overlay`,
branch `wt/ryw-overlay`, commit `b061b7b` (13 files, +1839/−51). Line numbers prefixed `wt:` refer to that commit; all
others to a408a2a.

## 0. Verdict first

* The overlay hypothesis is **right about the rule** ("never conclude from a read inside the settle window of your own
  write") and **right that verification must be a first-class, tri-state operation**. It is **over-engineering in one
  specific part**: answering reads from the journal (the *overlay*) is only worth its second-cache complexity for
  read‑modify‑write on levels (`adjust_level`/`set_level` ramp start, desk.py:982-1013) and for honest labelling of
  read-backs in tool envelopes. Everything that draws a *conclusion* (validate, preflight, dump/diff/snapshot,
  `set_rta_source`, restore) must **settle first, then read the desk** — an overlay must never feed a conclusion, or
  `validate_ringout_eqs` would "pass" on our own assumptions.
* What should be built regardless of which angle wins: (1) FakeDesk **apply latency** (today no test can show the
  race), (2) `Desk.verify()/settle()` with `{confirmed, mismatched, unanswered}`, (3) the **journal as bookkeeping**
  (statuses, `desk.write_settled` events, never-silent expiry, and — for free — the real-desk apply-latency
  measurements we lack, logged at INFO), (4) `GEQ_SETUP_UNCONFIRMED` ≠ `GEQ_VALIDATION_FAILED`, (5) the one-off
  `scripts/measure_settle.py` + `timing:` in device.yaml. The read overlay itself should ship behind
  `timing.overlay_reads` and be kept **on** only for classes where the desk is measured to apply asynchronously.
* Relative to the barrier approach: `verify()` as specified does **not depend on** the UNCONFIRMED ordering
  hypothesis (a GET reply after a SET proves the SET was parsed). If the desk is synchronous, the first poll (≈1 RTT,
  < 2 ms on this LAN per HANDOVER §4a numbers) confirms and the cost equals the barrier's; if a class is applied
  asynchronously (FX type load, inserts, scribble-strip names, `-prefs`→`-stat`, scenes), the barrier is silently wrong
  and verify keeps polling to a per-class deadline. The barrier is the cheaper *explanation*; poll-until-match is the
  cheaper *guarantee*.

## 1. Problem model (what a "stale read" can actually be)

Four indistinguishable-on-the-wire causes, and the code today only handles the first:

| # | cause | evidence | today |
|---|---|---|---|
| C1 | **our own cache** serves pre-write data | Desk section cache + epoch guard (desk.py:250-289, 303-353), `conn.set` invalidates + publishes `write` (connection.py:520-530), Desk hooks it (desk.py:235, 286-289). `slash()`/`send_raw()` publish nothing (connection.py:542-577); `restore` invalidates wholesale (desk.py:1333); `cfs._restore_master` invalidates after (cfs.py:1353). In-flight read started before a write returns pre-write data to *its own* caller un-cached (desk.py:339 — harmless today). | handled |
| C2 | **receipt ordering**: the desk has not parsed our SET when it serves the GET | UNCONFIRMED either way (transport.md §5.4; single-threaded `select()` loop in Maillot's emulator suggests in-order). FakeDesk stores synchronously in `_h_param` (fakedesk.py:1023-1045) before the next datagram, and its `latency_ms` (fakedesk.py:771-781) delays **replies only** — it cannot produce a stale read. The test helpers `settle()` (tests/integration/test_cfs.py:75-77, test_server_tools.py:66-68: "one GET round trip guarantees the fake processed them") encode the barrier assumption, true for the fake only. | not testable |
| C3 | **asynchronous application**: SET stored/echoed, but the readable state (or a *derived* state) changes later on another firmware task | HANDOVER §4b: `insert/on` read-back stale → false `GEQ_VALIDATION_FAILED`; name read-back stale after `label_channel`. Candidates by construction: FX type load (re-initialises 64 pars), insert routing, scribble strips (name/icon/colour), `/-prefs/*` mirrored into `/-stat/*` (`rtasource`), `/-action/goscene`, `/load`. meters.py:926-934 already sleeps 0.1 s and re-reads `/-stat/rtasource` (speculative, predates hardware: commit 846849b). | ad hoc |
| C4 | **lost datagram** (no ack, transport.md §5.2/§5.3) | looks exactly like C3 until a deadline passes | invisible |

Consequence for semantics: "assumed" (inside window) can legitimately become **confirmed** (C2/C3 resolved) or
**mismatched/unanswered** (C4, or the desk snapped/refused/was overridden). It must never silently become "true".

Protocol facts used (all CONFIRMED in docs/research/transport.md unless marked): SET no ack §5.2; sender excluded
from `/xremote` pushes §4.2 + quirk 8 (emulator `Xsend` code + DOC wording "from another client"); `/` write echoed
verbatim §6.6, DOC recommends it as *flow control* (receipt) — whether echo ⇒ applied is **not stated** (emulator
applies then echoes, X32.c 3769-3771; desk UNCONFIRMED); `/subscribe ,si addr tf` pushes the value to the *subscriber*
every 50·tf ms for 10 s regardless of who wrote (§7.1) — a desk-side poll; no error replies exist §5.3; real-desk
latency UNCONFIRMED §5.4 (Maillot: 1–10 ms pacing, 100 ms probe timeouts).

## 2. Settle windows: what we know, what to measure, where it lives

**Data we have (HANDOVER §4a/§4b): none that measures apply latency.** Indirect: full `/node` sweep 2103 sections in
0.28 s at concurrency 16 (≈0.13 ms/section ⇒ the OSC server answers thousands of datagrams/s; RTT ≪ 2 ms wired);
`restore` wrote 3 `/` lines in 9 ms awaiting each echo (≈3 ms per echoed line incl. our limiter); panic put 24
datagrams on the wire in 0.4 ms; scene verify polls `prepos/current` ≤ 2 s at 50 ms (desk.py:1264-1277) and passed
live; the two stale read-backs were "immediate" (same tool call, < 5 ms after the SET). So every window below is a
**prior**, chosen so that (a) synchronous classes cost one poll, (b) plausible firmware-task latencies (one UI/DSP
tick, 10–100 ms; an FX load, a few hundred ms) fall inside the deadline.

`device.yaml` gets an optional top-level `timing:` (wt:device.yaml:836-858; descriptor accepts it as OPTIONAL_KEYS,
wt:descriptor.py:70, :518; `SettleTiming` wt:journal.py:101-135 merges defaults wt:journal.py:79-98):

```yaml
timing:
  measured: {date, console, firmware, trials}      # present only after measure_settle.py ran
  settle_ms: {default: 60}
  classes:            # first match wins; `*` = one path segment, trailing `*` = rest
    scene:   {settle_ms: 500, deadline_ms: 2000, match: ["/-action/*", "/load"]}
    fx_type: {settle_ms: 400, deadline_ms: 1500, match: ["/fx/*/type"]}
    insert:  {settle_ms: 150, deadline_ms: 1000, match: ["/*/insert/*", "/*/*/insert/*"]}
    routing: {settle_ms: 150, deadline_ms: 1000, match: ["/config/routing/*", "/config/userrout/*", "/*/config/source", "/*/*/config/source"]}
    config:  {settle_ms: 150, deadline_ms: 1000, match: ["/*/config/*", "/*/*/config/*", "/headamp/*"]}
    prefs:   {settle_ms: 250, deadline_ms: 1000, match: ["/-prefs/*", "/-stat/*"]}
    fx_par:  {settle_ms: 60,  deadline_ms: 500,  match: ["/fx/*/par/*"]}
    param:   {settle_ms: 60,  deadline_ms: 500,  match: ["/*"]}
  verify: {first_delay_ms: 5, backoff: 2.0, max_interval_ms: 200}
  overlay_reads: true
  auto_settle: true
```
Class coverage over the 8453 descriptor leaves (counted): param 7135, config 496, fx_par 512, insert 168, routing 119,
prefs 15, fx_type 8. `settle_ms` = how long a *differing* read is presumed stale; `deadline_ms` = how long `verify()`
polls before reporting. All deadlines ≤ 2 s (DESIGN §0.5) except that tools already budgeted longer keep their budget
(`setup_ringout_eqs` 120 s, dump 60 s, default tool 30 s — server.py:94-96 — so a 1.5 s verify never trips a tool
timeout).

**Measurement (one evening, ~5 min, PA muted):** `scripts/measure_settle.py <ip> --trials 30 --second-socket
[--fx-slot 8]` (wt:scripts/measure_settle.py, validated in CI against a fake with known latency:
`test_measure_settle_script_recovers_the_fake_apply_latency`). Per probe class (fader, mute, name, insert/on with
`sel` OFF on scratch ch 32, `/-prefs/rta/source` with derived `/-stat/rtasource`, optional FX type load + par) it
alternates two values N times and records: **set→GET** first-match latency and the number of *stale replies before
the match* (0 on every trial ⇒ that class is applied before the next datagram ⇒ barrier holds for it; >0 ⇒ async);
**set→/node** first-match latency (Desk reads node text; may be served from another copy); **`/` write**: echo time
and whether the first GET after the echo already matches (settles "echo ⇒ applied?"); **push latency** on a second
`/xremote` socket and whether the *writing* socket ever saw its own echo (re-confirms quirk 8 on FW 4.13);
**derived** `-prefs`→`-stat` follow time. It restores every touched value, prints JSON to stdout and the exact
`timing:` block to paste (settle = 2×p95 rounded up to 10 ms, deadline = 4×max, floor 250 ms, `measured:` stamp).
It bypasses Policy deliberately (raw connection, scratch channel only, nothing on bus/main; `--fx-slot` is opt-in and
audible if the slot is in use; asks for `YES` unless `--yes`).

## 3. Design

### 3.1 Data structures (wt:src/x32mcp/journal.py — new module, 550 lines incl. docs)

```python
UNPROJECTABLE                                   # marker: raw known, /node text form unknown (token fields)
class SettleTiming:                             # wt:journal.py:101
    def __init__(cfg: Mapping|None); class_for(addr)->str; window_s(addr)->float; deadline_s(addr)->float
    first_delay_s; backoff; max_interval_s; overlay_reads: bool; auto_settle: bool; measured: dict|None

@dataclass class PendingWrite:                  # wt:journal.py:211
    address: str; raw: Any|None                 # raw OSC value sent (None for "/" lines)
    value: Any|UNPROJECTABLE                    # engineering value AS THE OWNING /node SECTION PRINTS IT
    node_path: str|None; field: str|None; spec: ParamSpec|None
    tool: str|None; via: "set"|"slash"|"expect"; t_sent: float; window_s; deadline_s; klass; seq
    status: "sent"|"echoed" -> "confirmed"|"mismatched"|"unanswered"|"superseded"
    actual: Any; t_settled; polls; stale_reads; first_stale_ms; confirm_source
    def to_dict(now) -> {address,status,class,via,tool,value,raw,age_ms,window_ms,polls,stale_reads,actual?,settle_ms?,source?,first_stale_ms?}

def same_value(spec, ours, theirs) -> bool      # wt:journal.py:169  compare in RAW space via spec.to_raw, ½ grid step;
def same_raw(spec, a, b) -> bool                #   token fields numerically (±0.051) else case-insensitive text

class WriteJournal:                             # wt:journal.py:268   synchronous, no I/O, one per Desk
    def project(address, raw) -> (node_path, field, spec, section_value|UNPROJECTABLE)   # incl. fx slot 5-8 enum table
    def record(address, *, raw=None, value=UNPROJECTABLE, tool=None, via="set") -> PendingWrite   # coalesces per address
    def annotate(address, *, value=..., tool=...)          # Desk._write upgrades the entry with the tool's own engineering value
    def record_line(line, *, tool) -> list[PendingWrite]   # "/" line after echo -> one 'echoed' entry per listed field (parse_node_line)
    def pending(addresses=None); pending_in(path); entry(addr); overdue(now=None); __len__; recent(n); stats
    def observe_raw(address, raw, *, source, decide=True) -> PendingWrite|None    # GET reply / push / poll
    def observe_section(path, vals, *, t_read=None) -> (vals_out, assumed: list[str])   # parsed /node section
    def settle(entry, status, *, actual=None, source=None)   # -> terminal; event desk.write_settled + log
    def clear(reason) -> int                                  # connection lost: all pending -> unanswered, ONE warning + desk.writes_abandoned
```
Rules inside `observe_*` (wt:journal.py:417-495): equal ⇒ `confirmed` (source recorded: `node`/`push`/`verify`/
`settle`/`restore`/`rta_source`); differ & age ≤ window ⇒ presumed stale: `stale_reads += 1`, first occurrence logged
at INFO with `age_ms` (**this is the real-desk measurement accumulating in gig logs for free**), section field
overlaid with our value when projectable and `overlay_reads`; differ & age > window ⇒ `mismatched` (desk value stands,
WARNING); differ via **push** ⇒ `superseded` at INFO (transport §4.2: never our own echo, so it is someone's newer
move — the human at the console wins); a poller that owns a deadline passes `decide=False` and settles at its deadline.
Bounded: `max_pending` 2048 (oldest force-settled `unanswered`), history ring 200.

### 3.2 Desk integration (wt:src/x32mcp/desk.py)

* `Desk.__init__` (wt:295-301): `self.journal = WriteJournal(d, events, SettleTiming(d.timing))`; subscribes
  `connection.state`.
* **Every write is journaled**, including raw `conn.set` by `meters.set_rta_source`/`server._RtaPrefsWriter`/ramps/
  panic: `_on_write_event` (wt:364-370) — the existing hook on the connection's synchronous `write` event
  (events.py:44-63) — records `raw=args[0]` with the descriptor projection, then `Desk._write` (wt:1114, new kwarg
  `section_value=None`) `annotate`s with the tool's own engineering value (per-slot FX enum token, GEQ dB for a
  `token` field via `set_geq_band(section_value=round(gain,1))`). Ramps coalesce to the last step per address.
* `slash()` writes: `Desk.restore` records each echoed line with `journal.record_line(line, tool="restore")`
  (wt:1582) — status `echoed` = receipt proven, application unverified — then `verify(touched, reason="restore")`
  (wt:1590) and returns `verified: {status, confirmed, mismatched[], unanswered[], polls, elapsed_ms}`.
  (`X32Connection.slash` itself stays event-free; only Desk knows the descriptor. If other slash callers appear, add
  `events.publish("write.slash", line=...)` in connection.py:555 and journal it in the same hook.)
* `send_raw` stays unjournaled (meters `/meters` `/renew` `/xremote` traffic, panic's degraded fallback,
  `cfs._restore_master` which polls by design — see §3.7).
* **Reads** — `_read_sections` (desk.py:303-353) is unchanged up to the return; the cache keeps **desk truth**; the
  overlay is applied *on the way out, every call* by `_overlay()` (wt:569-586) → `journal.observe_section` per section.
  So: an agreeing read confirms for free; a confirmed/mismatched entry stops overlaying immediately even inside the
  cache TTL; the in-flight-before-write hole (C1 note) is closed because the overlay runs after the await. Addresses
  answered from the journal go to `Desk.assumed_reads` and to a per-call `ContextVar` collector
  (`assumed_reads_collector`, wt:99-113) so concurrent tool calls and the CFS task do not mix.
* **Pushes** — `_on_push` (wt:356-362): invalidate as before + `journal.observe_raw(addr, args[0], source="push")`.
* **Connection state** — `_on_connection_event` (wt:372-378): `degraded|disconnected|connecting` ⇒
  `journal.clear(...)` (pending cannot be told from lost; a desk that comes back may have rebooted — HANDOVER §4a PSU
  note).
* **verify** (wt:419-493):
  ```python
  async def verify(self, addresses: Iterable[str]|None=None, *, expect: Mapping[str,Any]|None=None,
                   deadline_s: float|None=None, first_delay_s: float|None=None, reason="verify") -> VerifyResult
  @dataclass VerifyResult: confirmed: dict[addr->value]; mismatched: list[Mismatch(address, expected, actual, age_ms)];
                           unanswered: list[addr]; polls; elapsed_ms; ok: bool; status: "confirmed"|"unconfirmed"|"mismatched"
  ```
  Semantics: entries = pending journal entries for `addresses` (default all) + `expect` (addresses we did not write
  but whose raw value should follow, e.g. `{"/-stat/rtasource": 168}`, recorded `via="expect"`). Rounds of concurrent
  raw GETs (`conn.request(addr, timeout=min(0.25, remaining), retries=0)`, bypassing every cache and the overlay);
  delay `first_delay_s` (5 ms) then ×`backoff` up to `max_interval` (200 ms); stop when all confirmed or `deadline_s`
  (default = max class deadline among entries). At the deadline: answered-but-different ⇒ `mismatched` (with the
  last `actual`, engineering units when the spec is known), never answered ⇒ `unanswered`. Never raises on timeout;
  `DeskError("NOT_CONNECTED")` only without a socket. Drops the cached sections it touched. Logs one line (INFO if
  ok, WARNING otherwise). Cost when the desk is synchronous: 1 datagram pair per address, ≈ first_delay + RTT.
* **settle(deadline_s=None)** (wt:495) = `verify(None, reason="settle")` — the call every *concluding* read makes
  first: `dump()` (wt:887-893, hence `snapshot_desk`, `diff_snapshot`, `restore`'s live dump, CFS SNAPSHOT stage,
  `export_patch_plan`), `provision.apply_setup` before `validate_ringout_eqs` (wt:provision.py:538).
* **Settle loop** (wt:381-417): started lazily on the first pending write; sleeps to the earliest window expiry, then
  `verify(overdue, reason="settle")`. Guarantees every entry reaches a terminal state with a `desk.write_settled`
  event — **the "never silent" requirement** — at the cost of one GET per distinct written address that no read/push
  confirmed earlier. `timing.auto_settle: false` disables it (then expiry happens at the next observation or verify).
* `pending_writes(prefix=None)` (wt:500) and `stats["journal"]` for `connection_status`/dashboard.

### 3.3 Can we get confirmation for free from `/xremote`?

No, not for our own writes: the sender is excluded from pushes (transport §4.2, quirk 8, CONFIRMED on the emulator
code path and by DOC wording; `measure_settle.py --second-socket` re-checks it on FW 4.13 and records
`own_xremote_echo_for_written_addresses`). What pushes *do* give us: (a) another client/front panel touching an
address we just wrote ⇒ `superseded` (their value wins, entry closed, INFO); (b) another client writing the *same*
value ⇒ `confirmed`; (c) leaf pushes caused by *our* `/` writes go to *other* clients only as well (emulator
`XslashSetString … Xsend(S_REM)`), so no. Alternatives considered and rejected as defaults: a **second socket**
holding `/xremote` would see our echoes (research §5.2 suggests it) but costs one of the desk's **4** xremote slots
(X32-Edit, tablets compete) and a second heartbeat — kept for the measurement script only; **`/subscribe ,si addr 1`**
delivers the value to us every 50 ms for 10 s regardless of writer (§7.1) — it is polling done by the desk with lease
management on top; a GET poll is strictly simpler and ~50× faster.

### 3.4 Reporting vocabulary (tool envelopes)

| state | meaning | where it shows |
|---|---|---|
| `sent` | datagram out, nothing known | `pending_writes()`; never in a summary as success |
| `echoed` | `/` line echoed: desk **received** it | restore result before verify |
| **assumed** (read-side) | a value in this result is *our pending write*, read inside its window, not the desk's report | envelope key `unconfirmed: [addresses]` + summary suffix `" [unconfirmed: N value(s) just written, not yet read back from the desk]"` added centrally in `_tool` (wt:server.py:312-333) |
| `confirmed` | a read/poll/push returned what we wrote | summaries say "confirmed by the desk" (setup: wt:server.py:1629-1632); `verified.status` |
| `unconfirmed` | deadline passed, **no answer** | `DeskError("GEQ_SETUP_UNCONFIRMED")` for setup; `verified.unanswered`; WARNING + event |
| `mismatched` | deadline passed, desk **reports something else** (lost, snapped, refused, overridden) | `verified.mismatched[{address, expected, actual, age_ms}]`; WARNING + event |
| `superseded` | someone else moved it after us | INFO + event; never an error |

Wording rule for the LLM-facing summary: *written* ("set", "sent") is not *done*; only `confirmed` may be phrased as
a fact about the desk; `unconfirmed` must carry the instruction "check / re-validate, do **not** assume failure and do
not blindly re-run" (wt:server.py:1617-1621) — re-running a Tier-2 setup because of a false FAILED is exactly the
M7 failure mode amplified by an agent.

### 3.5 Site inventory → change (file:line at a408a2a)

| site | today | change (overlay design) | default verify? |
|---|---|---|---|
| `provision.apply_setup` → `validate_ringout_eqs` (provision.py:503-536, read at :530 via `get_inserts`/`get_fx`) | immediate read-back; live false `GEQ_VALIDATION_FAILED` | `vr = await desk.settle()` before validate; result `verified/verify/unconfirmed/mismatched` (wt:provision.py:534-552); server maps `not ok and not verified` → `GEQ_SETUP_UNCONFIRMED`, `not ok and verified` → `GEQ_VALIDATION_FAILED` "after N confirmed write(s)" (wt:server.py:1608-1632) | yes (Tier 2) |
| `Desk.set_fx_type` (desk.py:1389-1401) then `set_geq_band` reads `/fx/N` type (desk.py:1417) | would raise `NOT_A_GEQ` if the load has not landed | journal window `fx_type` 400 ms overlays the type for reads; `apply_setup` settles anyway; `set_geq_band` unchanged | via setup |
| `Desk.label` (desk.py:1154-1182) → next `get_channel` | live stale name | overlay answers `name` inside `config` window and flags `unconfirmed`; settle loop confirms/mismatches within 1 s (test `test_label_then_read…`, `test_tool_envelope_marks…`) | settle loop |
| `patches.apply_patch_plan` (patches.py:551-610): reads config, writes label/source per row; a quick re-apply reads stale and re-writes | harmless double write; summary may lie | `_current_config` goes through Desk reads ⇒ overlay makes the idempotence check see our writes; add `verified = (await desk.settle()).to_dict()` to the result (not in prototype; 3 lines) | yes (labels are "committing") |
| `Desk.set_level`/`adjust_level` read `before` (desk.py:991, :1008) then ramp from it (`ramp_steps(before_db, …)` desk.py:967) | back-to-back moves: stale base ⇒ **lost update and an audible jump to the old level at ramp start** | overlay: `before` = our last write inside the `param` window (test `test_read_modify_write_uses_our_last_write_not_a_stale_read`: set −20 then +3 ⇒ −17 with the desk 150 ms behind) | no (latency); settle loop |
| `cfs._write_master` (cfs.py:1115-1124) — comment says "read-back" but `after_db` is what was *written* (desk.py:970-975) | no read-back; RAISE every 1.5 s reads `before` via `set_level` | unchanged; journal confirms each step via the next step's read (1.5 s ≫ window) | no |
| `meters.set_rta_source` (meters.py:860-940; retry+sleep 0.1 at :926-934) | one re-read of a **derived** address | `verify=[Desk.verify]` kwarg: `verify([src,pos,(+opt,ag,det if written)], expect={stat: expected}, deadline_s=read_timeout_s)` (wt:meters.py:931-945); cfs.py:911 and server.py:1518 pass `desk.verify`; stub-conn unit tests keep the old path | yes |
| `Desk.recall_scene` poll (desk.py:1262-1277) | bespoke 2 s/50 ms poll of `prepos/current` | keep (it is verify with `expect={"/-show/prepos/current": idx}`; fold in later: `verify(expect=…, deadline_s=2.0)`), plus `journal.clear`-like `invalidate()` stays | yes |
| `Desk.restore` (desk.py:1312-1341) → `diff_snapshot` right after | echo = receipt; diff could show phantom changes | record echoed lines + `verify(touched)`; `dump()` settles first so a diff/snapshot is desk truth (tests `test_restore_says…`, `test_dump_settles…`) | yes |
| `Desk.save_scene` (desk.py:1290-1310) | has a real reply `,si scene 1` | nothing (acknowledged protocol op) | — |
| `Desk.panic` (desk.py:1184-1221) | `delivered: sent|unconfirmed`, no read-back | sends stay first and unthrottled; **add** `vr = await self.verify([...24 mix/on], deadline_s=0.3)` *after* the loop when CONNECTED, re-send the unconfirmed once (idempotent), report `muted_confirmed: n/24` (M5 did this by eye: "24/24 verified muted"). Not in prototype (10 lines); the journal already auto-settles the 24 writes and would WARN on any that did not land | yes, post-hoc |
| `cfs._restore_master` (cfs.py:1324-1359) | send_raw + GET every 0.5 s ≤ 10 s | keep (must work while DEGRADED, where `verify` refuses); optionally express as `verify(expect={fader: raw}, deadline_s=10)` once verify tolerates DEGRADED single-shot reads | — |
| CFS notch write → VERIFY (cfs.py:1200-1219, `_verify_decay` :1221-1250) | RTA decay is the verification (correct: physical truth) | see §3.7: confirm the par landed **only when VERIFY fails**, before deepening | conditional |
| `preflight`/`validate` reading GEQ bands after `feedback_watch` notches (provision.py:409-413) | could miss just-written cuts (`existing_cuts`) | overlay covers `fx_par` window (GEQ dB projected via `section_value`); `ring_out` preflight may call `desk.settle()` first (1 line) | settle |
| `discover_mics`/`get_*` tools | pure reads | envelope `unconfirmed` when they happen to read our pending writes | — |

### 3.6 `setup_ringout_eqs` / `validate_ringout_eqs` — exact result

`apply_setup` → `{buses, writes, skipped, changed, ok, verified: bool, verify: {status, confirmed, mismatched[],
unanswered[], polls, elapsed_ms}, unconfirmed: [addr], mismatched: [{address, expected, actual, age_ms}], geq: {...}}`
where `geq` is read **after** settle. Server: `ok` ⇒ summary "`N write(s) confirmed by the desk; all validate`";
`!ok && !verified` ⇒ error `GEQ_SETUP_UNCONFIRMED` ("writes … were sent but the desk has not confirmed them (<addrs>);
run validate_ringout_eqs in a moment — do NOT assume it failed and do not re-run setup blindly"); `!ok && verified` ⇒
`GEQ_VALIDATION_FAILED … after N confirmed write(s): <reasons>` (a real blocker: shared stereo GEQ, slot taken).
`validate_ringout_eqs` (the read-only tool) stays a pure desk read — it never overlays into `ok` because the
Tier-2 path settled first; if called standalone inside a window its envelope carries `unconfirmed`.

### 3.7 CFS: do notch writes need confirmation before VERIFY?

No — and yes in one branch. The RTA decay (`_verify_decay`, cfs.py:1221-1250, ≥ `decay_verify_db` on N consecutive
frames within `decay_verify_s`) is the only verification that matters: a confirmed par value with a still-ringing room
is a failure, an unconfirmed par with a silent room is a success. Adding a GET before VERIFY only adds ≥ 1 RTT to the
time-to-cut. **But** when VERIFY *fails*, today the loop deepens (−3 → −6 → −9, cfs.py:1213-1218) — if the cause was a
**lost datagram** (C4) we spend 3 dB of a bounded −9 dB budget and one of 4–6 notches' worth of headroom for nothing.
Change: in `_notch_and_verify`, on `not ok`, `vr = await desk.verify([par_address], deadline_s=0.15,
reason="cfs-notch")`; if `unanswered or mismatched` ⇒ re-send the *same* depth (`desk.set_geq_band`, idempotent) and
re-run `_verify_decay` once before allowing `nc.plan` to deepen; log/`cfs.notch` event gets `write: confirmed|resent`.
Cost: one GET on the failure path only. (Specified, not in the prototype — it touches the state machine; the journal
already flags such a lost write as `mismatched` after 500 ms with a WARNING, which is the diagnostic half of this.)
RAISE steps need no confirmation (next step reads the fader 1.5 s later; a lost raise step under-raises, the safe
direction). BACKOFF/restore already poll.

### 3.8 Failure semantics (never silent)

* Entry inside window: reads overlaid + flagged; nothing logged above INFO.
* Window passed, unobserved: settle loop polls it to its class deadline ⇒ `confirmed` (DEBUG) | `mismatched`
  (WARNING `write X = v NOT confirmed (mismatched via settle after N ms; desk reports w)` + `desk.write_settled`) |
  `unanswered` (WARNING + event). Dashboard mirrors events already (events bus), so the operator sees it.
* Push differing ⇒ `superseded` (INFO + event). Connection degraded/disconnected ⇒ all pending `unanswered`, **one**
  WARNING + one `desk.writes_abandoned {count, reason, addresses[:50]}` (not 500 events after a cable pull mid-ramp).
* Journal overflow ⇒ oldest force-settled `unanswered` (source `overflow`) — bounded memory under a runaway writer.
* `verify()` never raises for time; callers get a value they must report. A verify that cannot run (no socket) raises
  `NOT_CONNECTED` like every other Desk op.

## 4. FakeDesk changes (wt:src/x32mcp/fakedesk.py; defaults keep today's instant behaviour)

* `apply_delay_ms: float = 0` (global) and `apply_delay_rules: list[(prefix, ms)]` (first match; `*` in a prefix =
  one path segment, `_prefix_match` wt:204-211), `apply_delay_for(address)` (wt:455-461).
* `_h_param` SET (wt:1120-1145) and multi-arg node SET (wt:1160-1176): coerce now, **apply later** via
  `_defer(delay, fn)` (`loop.call_later`, FIFO per address since equal delays; wt:463-478); until it lands, GET and
  `/node` serve the old state — exactly C2/C3 as seen from the wire. `_after_change()` (RTA cut refresh) runs when the
  deferred write lands, so a delayed GEQ notch also delays the synthetic ring's decay (realistic).
* `slash_apply: "sync"|"deferred"` (`_h_slash`, wt:1090-1118): `sync` = store then echo (Maillot's emulator);
  `deferred` = echo immediately (receipt), apply after the delay of the line's first leaf — the pessimistic reading of
  DOC 4591.
* Controls: `/-fake/applydelay ,f ms` | `,sf prefix ms` | no args = clear; `/-fake/slashapply ,s sync|deferred`
  (wt:1427-1438); Python: `pending_applies`, `flush_applies()`, `deferred_count`, `stats`.
* `latency_ms` is untouched and remains *reply* latency (documented as such, wt:62-76).
* Recommended CI matrix: run `tests/integration` once with defaults and once with
  `apply_delay_rules=[("/*/insert/",150),("/fx/",300),("/*/config/",150),("/-prefs/",250)]` via a fixture param, so
  every future read-after-write regression shows up without anyone remembering to add a delay.

## 5. Tests (all in the prototype; the two marked † fail on a408a2a+FakeDesk-apply-latency without the Desk changes)

`tests/integration/test_read_after_write.py` (16 tests):
lever — `test_apply_delay_makes_an_immediate_read_stale`, `test_apply_delay_rules_are_per_address_class_and_ordered`,
`test_slash_deferred_mode_echoes_before_applying`, `test_fake_control_datagrams_configure_apply_delay`;
defect — †`test_apply_setup_does_not_report_failure_while_the_desk_is_still_applying` (reproduces M7: on main it
returns `ok: False, "no FX insert on Bus 1 (insert/sel is 'OFF')"`), †`test_label_then_read_is_answered_from_the_journal_and_flagged_assumed`,
`test_read_modify_write_uses_our_last_write_not_a_stale_read`, `test_verify_reports_mismatched_when_the_desk_does_not_apply_in_time`,
`test_verify_reports_unanswered_when_the_desk_stops_talking`, `test_every_write_settles_by_itself_with_an_event`,
`test_restore_says_whether_the_desk_holds_the_restored_values`, `test_dump_settles_pending_writes_first_so_a_diff_is_desk_truth`,
`test_tool_envelope_marks_values_read_back_from_our_own_pending_writes` (server layer), `test_device_yaml_timing_drives_the_journal_windows`,
`test_set_rta_source_verifies_the_derived_stat_through_desk_verify`, `test_measure_settle_script_recovers_the_fake_apply_latency`.
`tests/test_journal.py` (7, pure): timing classes; **projection sweep** — for every one of the 7941 projectable
descriptor leaves, `journal.project(addr, raw)` must `same_value` the field parsed from the fake's rendered `/node`
text (`test_projection_matches_node_text_for_every_leaf_of_the_descriptor`); coalescing/stale/confirm; mismatch after
window with WARNING; push confirm/supersede; slash lines → `echoed` + token fields never overlaid; `clear()`.
Suite: 934 passed (912 baseline + 22 new + 1 pre-existing worktree-name failure `test_settings_defaults_and_env`,
which fails in any worktree not named `x32-mcp`; TCP tests deselected as instructed).

## 6. Cost, risks, and where this is over-engineering vs the barrier

**Size:** journal.py 550 lines (≈300 code), desk.py +230, fakedesk.py +100, provision/server/meters +70, script 340,
tests 440. The barrier/minimal alternative (a `read_until` helper + FakeDesk apply delay + 5 call sites) is roughly a
third of that and fixes every *reported* defect.

**What only the overlay buys:** (1) correct read-modify-write on levels under back-to-back tool calls (a real,
audible class: `set_level` builds its ramp from the read `before`; a stale base jumps the fader back before gliding —
the barrier design must either add a settle before *every* level read, costing an RTT per move, or ignore it);
(2) honest `unconfirmed` labelling of any read-back in any tool without per-tool code; (3) free field data: every
`stale read of X N ms after our write` INFO line from a gig is a measurement of C2/C3 on real firmware; (4) uniform
terminal accounting of every write (the M5 "24/24 verified by eye" becomes a log fact).

**Risks (one materialised during the prototype):** the overlay writes *our projection* of a raw value into a parsed
section; if projection and node text disagree, the overlay **replaces correct desk data with a wrong value for a whole
window**. It happened: `/fx/5..8/type` projected through `fx_type_14` instead of `fx_type_58` turned a correct `GEQ`
read into `AMBI` for 400 ms and broke `test_validate_reports_bypassed_insert_and_preflight_refuses` until (a) `_write`
annotated entries with the tool's own engineering value and (b) `project()` learned the slot rule; the projection
sweep test now guards the whole descriptor, and `token` fields (512 FX pars) are `UNPROJECTABLE` ⇒ flagged, never
overlaid (GEQ pars get their dB via `section_value`). Second risk: a second cache layer with its own invalidation
story (pushes, reconnects, coalescing) — mitigated by keeping the section cache as desk truth and overlaying on the way
out, and by clearing on any connection state change. Third: masking a genuinely refused/snapped write for ≤ window —
bounded (60–400 ms) and always followed by a `mismatched` WARNING. Fourth: auto-settle adds one GET per distinct
written address (≤ 50/s by the limiter; the desk served 7.5k `/node`/s at M5) — negligible, but it is traffic the
barrier design does not generate.

**Honest cut line.** If `measure_settle.py` shows `applied_before_next_datagram: true` for `param`, `fx_par` and
`config` (plausible for a single-threaded OSC task), set `overlay_reads: false` for those classes (or globally) and
the design degenerates to exactly "journal-as-bookkeeping + verify at concluding sites" — i.e. the barrier/minimal
design with better reporting. Keep the overlay only for classes with measured stale replies (my bet: `fx_type`,
`insert`, `prefs→stat`, `scene`, maybe `config`). Do **not** build: per-tool "verify by default" flags on Tier-1 moves
(latency for no decision), `/subscribe`-based confirmation, a second socket in the server, or blanket sleeps anywhere.

**Migration order:** FakeDesk apply latency + failing tests → `Desk.verify/settle` + `apply_setup`/server wording +
`set_rta_source(verify=)` → journal bookkeeping + settle loop + events → run `measure_settle.py` at the desk, paste
`timing:` → enable/trim `overlay_reads` per class → CFS notch resend-on-unconfirmed → panic post-hoc verify → fold
`recall_scene` polling into `verify(expect=…)`.

## 7. What to capture at the desk next session (beyond the script)

Run the script twice (idle desk; X32-Edit connected and busy) and keep both JSONs; note whether `/` echo ⇒ applied for
names (decides whether `restore` needs its verify); toggle an FX type in a free slot with `--fx-slot` and read
`/node fx/N/par` until the GEQ2 `0.0 … 0.0` row appears (that latency is the `fx_type` deadline); after a normal
tools session grep the server log for `stale read of` and `NOT confirmed` — zero lines with `overlay_reads: true`
and non-trivial traffic is itself the evidence that the desk applies synchronously.

---
## G2. Design: minimal

# Read-after-write over fire-and-forget UDP — the smallest correct change

Saved to `<review-reports>/raw-design-minimal/design.md`. Repo `<repo>` @ `a408a2a`; line numbers are against that HEAD unless marked **WT** (prototype worktree `wt/raw-minimal`, commit `2acb8bd`, at `<review-worktrees>/raw-minimal`; 13 files, +692/−69; suite 928 passed — the one failure, `tests/test_targets_events_config.py::test_settings_defaults_and_env`, asserts the checkout dir is named `x32-mcp` and fails in any worktree).

## 0. Verdict on the hypothesis

It holds, with one correction. There are exactly **three** places where a read straight after a write changes a DECISION or REPORT inside the same operation (A `apply_setup`→validate, B `set_rta_source` verify, J `panic`'s "24 muted" claim — J reads nothing today, which is the defect), **two** that already poll correctly (C `recall_scene`, D `cfs._restore_master`), and **zero** tools that read back their own write (every `applied` value is computed locally, §7). The `label_channel` observation was cross-tool, and chasing it found two local read-your-writes bugs in `Desk` that produce the same symptom with no desk asynchrony (E0 reproduced and fixed, E1). General answer: one helper (`settle.read_until`) + one barrier (`conn.sync`) + a FakeDesk that applies writes late + two ~10-line `Desk` cache fixes, applied at A/B/J, results worded *verified / not yet verified*.

What the two hardware observations were **not**: our cache. `Desk._write` drops the owning section synchronously (desk.py:883 → `_invalidate_address` :275-280; `EventBus.publish` is synchronous, events.py:44-63) and the leaf index covers every address involved (verified by script: `/main/st/insert/on`→`/main/st/insert`, `/ch/04/config/name`→`/ch/04/config`, `/fx/5/type`→`/fx/5`, `/-prefs/rta/source`→`/-prefs/rta`, `/-stat/rtasource`→`/-stat/rtasource`); both sequences were sequential awaits so E0 cannot explain them. The desk answered a `/node` request it received *after* the SET (same socket, dozens of datagrams later in the `get_inserts` sweep) with the pre-SET value ⇒ the X32 applies at least inserts, names and `/-stat/*` mirrors **after** its OSC server has answered later datagrams. Consequence: **no barrier can prove application** — not a GET round trip, not the `/` echo (§6.6 calls it flow control), not `/xremote` (writer never sees its own echo, §4.2). Only observing the value proves it; a barrier proves receipt/dispatch order (still useful as flow control).

## 1. Site inventory

| # | Site (HEAD) | Write → read | Decision/report | Today | Change |
|---|---|---|---|---|---|
| **A** | `provision.apply_setup` provision.py:503-537 (validate :530); server maps `ok:false`→`GEQ_VALIDATION_FAILED` server.py:1599-1608 | `set_fx_type`, `set_insert` via `conn.set` → `validate_ringout_eqs` reads 56 `*/insert` sections + `/fx/N`,`/fx/N/par` (:377, :397) | tool success vs error; `ring_out_system` stage list (cfs.py:1411-1413) | one immediate read; **false FAILED observed on hardware** | `read_until(revalidate, all ok, 2.0 s)`; `verified`/`settle` in result; server: `verified is False` → `ok:true` + "NOT YET VERIFIED" warning; `GEQ_VALIDATION_FAILED` only when nothing was written (**WT** provision.py:74, :503-575; server.py:1605-1633) |
| **B** | `meters.set_rta_source` meters.py:860-941, loop :923-931 | `conn.set` prefs → `conn.get(/-stat/rtasource)` | `RtaSourceResult.verified` → `cfs.state rta_verified`, report, WARNING | 2 reads, `sleep(0.1)`, stops on first read failure | `read_until(get stat, ==expected, verify_deadline_s=1.0, first_delay 0.05)`; `settle_attempts/settle_ms` in result + `cfs._rta_dict` (**WT** meters.py:859-870, :928-947; cfs.py:206) |
| **J** | `Desk.panic` desk.py:1184-1220; tool server.py:1062-1073 | 24× `conn.set(mix/on,0)` → *nothing* | "24 outputs muted (sent)" (README:873's "verified" was Jim's eyes) | a lost datagram leaves an output open while the tool reports it muted | after the sends: `read_until(24 */mix fresh, all mix/on False, 0.6 s)`; still-open outputs re-sent once (`send_raw`) + re-check 0.3 s; `delivered: confirmed|partial|sent|unconfirmed`, `confirmed`, `unconfirmed[]`, `resent[]`, `verify{}`; skipped while DEGRADED and for `verify_s=0`; never raises (**WT** desk.py:94-96, :1206-1302; server.py:1064-1077) |
| C | `Desk.recall_scene` desk.py:1251-1288 (poll :1264-1278) | goscene → `get(prepos/current)` | `verified` T/F/None | already bounded poll, UNVERIFIED not failure | none (optional refactor onto `read_until`); characterisation test with `set_apply_delay("/-action/",300)` passes today |
| D | `cfs._restore_master` cfs.py:1324-1361 | `send_raw` → `get` every 0.5 s ≤ 10 s | `restored`, ABORT report | already write+poll | none |
| E | `Desk.label` desk.py:1154-1182 and every `_write_value` user (`set_eq_band` :1050, `set_pan` :1043, `set_comp/gate` :1111/:1134, `set_source` :1343, `set_insert` :1372, `set_fx_type` :1389, `set_geq_band` :1403, `set_phantom` :1352); server `label_channel` :1290-1312, `set_channel_config` link :1405-1416; `patches.apply_patch_plan` patches.py:551-600 | write → **no read**; `applied = spec.to_value(spec.to_raw(v))` desk.py:886-895 | summaries | "previous name" came from the *next* tool. Two local bugs: **E0** `_drop` (desk.py:268-273) bumps the epoch but leaves `_inflight[path]`, so a reader arriving after a write joins a `/node` request issued before it and receives the pre-write reply (reproduced with `latency_ms=150`: late reader got `Ch01`); **E1** the first post-write read is cached for `read_cache_ttl_s` (desk.py:335-339, dump :662-666), turning one stale answer into 2 s of stale answers (violates DESIGN §0.6) | **E0**: `_drop` pops `_inflight[path]`; fetcher resolves the futures it created (`mine`) (**WT** desk.py:276-284, :335-366). **E1**: `_written_at[path]` stamped by `_write`/`panic`/`write` events; `_read_sections`/`dump` don't cache a section younger than `policy.settle_window_s`=0.5 s (**WT** desk.py:216-221, :286-292, :356-358, :684-687). E2 optional (§5). No in-tool read-back (§7). |
| F | scene recall then `dump`/`snapshot` | goscene → sweep | snapshot content | cross-tool; C gates; load duration UNCONFIRMED | none; README note |
| G | `Desk.restore` desk.py:1312-1341; tool server.py:1240-1267 | N× `slash` (echo) → `invalidate()`; **no read** | "written 3/3" = 3 echoes; echo ≠ applied | overstates | recommended: `read_until(dump(touched, fresh), restore_plan(snap, live)==[], RESTORE_VERIFY_S=2.0)` → `verified`, `unverified_sections[]`; budget `RESTORE_BASE_TIMEOUT_S + n/RESTORE_SECTIONS_PER_S + 2.0` (server.py:97-98, :1260). Not prototyped. |
| R | `cfs._arm` cfs.py:901-929 | `set_rta_source` → `frames.start()` → detector | first detections / floor calibration | early frames may describe the previous RTA source (meter-domain instance; magnitude UNCONFIRMED) | recommended: discard frames until `rta.verified` + `arm_discard_frames` (4 ≈ 200 ms); report `discarded_frames`. Not prototyped (belongs with BRIEF §4). |
| — | `save_scene` :1290-1310 | `/save` request/reply | ok | acked | none |
| — | `set_mute` `was`, `set_level` `before`, `set_channel_config` `strip`, `apply_patch_plan` `_current_config`, `plan_setup`, `preflight` | read **before** write | — | not RAW | none (E0/E1 cover cross-tool) |
| — | CFS NOTCH→VERIFY cfs.py:1200-1252 | `set_geq_band` → RTA frames over `decay_verify_s` | tamed/deepen/abort | already observation over a window | none |

`X32Connection.get_cached` (connection.py:581-605) has E0's flaw too (`invalidate` :607-613 leaves `_inflight`); it has **no callers** in `src/` — delete or fix identically.

## 2. The helper — `src/x32mcp/settle.py` (**WT**, 128 lines; `tests/test_settle.py`, 8 cases)

```python
@dataclass(frozen=True)
class Settled(Generic[T]):
    ok: bool; value: T | None; attempts: int; elapsed_ms: float; error: str | None = None
    def to_dict(self) -> dict   # {"verified": ok, "attempts", "elapsed_ms"[, "error"]}

async def read_until(read: Callable[[], Awaitable[T]], predicate: Callable[[T], bool], *,
    deadline_s: float,                       # wall clock from entry, covers reads AND sleeps
    first_delay_s=0.04, backoff=1.6, max_delay_s=0.4,   # 40, 64, 102, 164, 262, 400… ms
    read_timeout_s: float | None = None,     # each read gets min(read_timeout_s, remaining)
    min_read_s=0.02,                         # a further read only starts with ≥ this budget
    retry_on: tuple[type[BaseException],...] | None = None,  # default (RequestTimeout,); asyncio.TimeoutError ALWAYS = failed attempt
    clock=None, sleep=asyncio.sleep, what="value") -> Settled[T]
```
Semantics (all tested): first read immediately (synchronous desk = one RTT, no sleep); sleeps grow and never pass the deadline; `deadline_s=0` ⇒ exactly one read; per-read cap timeout or `retry_on` exception = failed attempt recorded in `error`, polling continues; anything else propagates (`NotConnected`, `DeskError` unless listed, predicate bugs); **never raises for a timeout**; **cancellation-transparent** (nothing shielded; < 100 ms in sleep and in read); no jitter; DEBUG per attempt. Caveats: cancelling `_read_sections` mid-flight hands co-waiters `DeskError("TIMEOUT")` (existing, test_desk.py:126-150); reply matching is FIFO per address (connection.py:782-795) so a late reply to poll k can satisfy poll k+1 — delays, never fakes, a match. Free function over a `read` closure (not `read_until(desk|conn, reads…)`) because the three sites read different things through two layers with different freshness rules; follow-up polish: `Desk._read_sections(paths, fresh=True)` instead of `_drop`/`invalidate` before each attempt (prototype: **WT** provision.py:545-548, desk.py:1268-1271).

## 3. The barrier — `X32Connection.sync()` (**WT** connection.py:556-568)

`await self.request("/status")`, returns RTT ms; retried like `request`, one attempt DEGRADED, raises `RequestTimeout`/`NotConnected`. `/status` is constant, unused by heartbeat/watchdog, and late unmatched replies are already discarded (`_NOT_PARAMS` connection.py:71). Docstring states the guarantee honestly: prior datagrams **received and dispatched**, **not applied**. `test_sync_is_a_barrier_for_fire_and_forget_sets` (**WT** test_fakedesk.py:383-397) pins both halves. Uses: flow control after bursts (`apply_patch_plan`, `panic` — J's read doubles as barrier), replacing tests' ad-hoc `settle()` (test_cfs.py:76-78, test_desk.py:45-47). Never a substitute for `read_until`. `slash()` (connection.py:542-554) is already a per-line barrier of equal strength; `restore` gains observation (G), not more echoes.

## 4. FakeDesk: writes applied late per address class (**WT** fakedesk.py:315-323, :465-546, :812-836, :1083-1152)

`apply_delay_ms: dict[prefix → ms]` (longest prefix wins, `""`=all, default `{}` ⇒ unchanged behaviour, 912 existing tests untouched), `set_apply_delay(prefix, ms)`, `pending_writes`, `flush_pending() -> int`, `applied_late`. One `_apply([(address, raw)], exclude, verbatim=None)` replaces `_store`+`_commit` in the wire write paths: single SET (HEAD :1037-1045), multi-arg node SET (:1052-1066; verbatim forward only when all leaves applied now), `/` writes (`_h_slash` :1012-1021 — **echo immediate, application late**, modelling §0; `_apply_node_text` → parse-only `_parse_node_write`), `/-action/setrtasrc` (:1068-1078; `/-stat/rtasource` mirror runs from `_side_effects` when the prefs write lands). Heap keyed `(due, seq)` (FIFO for equal dues — asyncio timers don't guarantee it), one `TimerHandle` drains all due then `_after_change()` once; `stop()` cancels/clears. GET, `/node`, meters and the closed loop see applied state only; delayed `/-action/goscene` delays the recall (exercises C). Front-panel `set/set_value` and request/reply verbs stay immediate. Plus `drop_inbound_next(n)` — the "SET lost on the way in" fault `drop_next` (replies) cannot express; needed for J.

## 5. Per-site spec (prototyped unless marked)

**A** `apply_setup(desk, plan, *, settle_deadline_s=None)`: if `writes`: `touched = /fx/N (type loads) + <prefix>/insert`; `revalidate()` = invalidate touched, `validate_ringout_eqs`; `read_until(revalidate, all ok, SETUP_SETTLE_S=2.0, first_delay 0.05, retry_on=())`; result `verified: bool|None`, `settle`; not verified → invalidate touched again (deadline 2 s > E1 window). Server: `not ok and verified is False` → `_ok("… NOT YET VERIFIED — desk still reported <reasons> after K read(s) in T ms. Run validate_ringout_eqs …", warnings=[…])`; `not ok` with nothing written → `GEQ_VALIDATION_FAILED` (existing test_server_tools.py:495-505 passes unchanged). Rationale: preflight re-validates and is the real gate (provision.py:637); per attempt ≈ 56+2 `/node` first time, only touched sections afterwards, ~5-10 ms on LAN.
**B** `set_rta_source(..., verify_deadline_s=1.0)` replaces unused `verify_attempts`; `retry_on=(Exception,)` keeps "read failures logged, never raised" but a single lost reply no longer ends verification; existing test_meters.py:664-694 pass unchanged.
**J** as §1; only behavioural contract change in the set: `delivered=="sent"`→`"confirmed"` (test_desk.py:360-362, test_server_tools.py:244).
**E0/E1** as §1 (one existing expectation adjusted, test_desk.py:116-121). **E2 (optional)**: `_expect[address]=(raw, now+0.3)`; on a fetched section contradicting a live expectation (`spec.to_raw(parsed)` vs raw, grid tolerance), re-read via `read_until` within the remaining window, desk's final answer wins; ≤ 0.3 s only on contradiction; makes `get_channel` after `label_channel` right first time. **G, R** recommended follow-ups. Docs: DESIGN §0.5/§1/§13/§14/§18, README :624 and tool table, HANDOVER §4b, optional `device.yaml policy.settle_window_s`, cfs.py:1186-1187 comment.

## 6. Regression tests (fail on `a408a2a` product code with the new FakeDesk; pass on WT)

| Test (**WT**) | Fault | Asserts | HEAD failure |
|---|---|---|---|
| `tests/integration/test_cfs.py:704 test_apply_setup_waits_for_the_desk_to_apply_inserts_and_type_loads` | `/bus/`,`/fx/` 150 ms | `ok, verified, attempts>=2, 100<=ms<1500, applied_late==7` | `ok False` |
| `test_cfs.py:718 test_apply_setup_not_yet_verified_is_reported_not_failed` | `/bus/01/insert` 10 s, deadline 0.3 | `ok False, verified False`; after `flush_pending()` validate ok (not cached stale) | `KeyError`; follow-up validate stale from cache |
| `tests/integration/test_server_tools.py:509 test_setup_ringout_eqs_on_a_desk_that_applies_late_is_ok_or_not_yet_verified` | 150 ms; then `SETUP_SETTLE_S=0.3` + 60 s delay | ok+verified; then `ok True, verified False, "NOT YET VERIFIED", warnings, no error`; validate ok after flush | `GEQ_VALIDATION_FAILED` (the M7 defect) |
| `tests/integration/test_fakedesk.py:400 test_set_rta_source_verifies_when_the_stat_mirror_lags` | `/-prefs/rta/` 300 ms; then 60 s + deadline 0.3 | `verified, 148, attempts>=2, 250<=ms<1000`; then `not verified`, `<900 ms` | `verified False` |
| `test_fakedesk.py:383 test_sync_is_a_barrier_for_fire_and_forget_sets` | latency 30 ms; apply delay 200 | SETs visible after `sync()`; delayed write not | no `sync` |
| `tests/integration/test_desk.py:377 test_panic_detects_and_resends_a_lost_mute` | `drop_inbound_next(1)` | `resent==["main.st"]`, confirmed 24, fake `/main/st/mix/on==0` | Main LR stays open while result says 24 muted |
| `test_desk.py:390 test_panic_confirms_mutes_the_desk_applies_late_and_reports_the_rest` | `/bus/` 200; then `/mtx/` 60 s, `verify_s=0.3` | confirmed, attempts>=2; then `partial`, 18, `unconfirmed==mtx.1..6==resent` | no keys |
| `test_desk.py:775 test_reader_after_a_write_never_joins_a_request_issued_before_it` (E0) | `latency_ms=150`, concurrent early reader | read issued after `label()` = `NEWNAME` | late reader gets `Ch01` — no desk asynchrony involved |
| `test_desk.py:791 test_a_read_straight_after_a_write_is_not_cached` (E1) | `/ch/02/config` 150 | first read `Ch02` (desk truth), 200 ms later `LATE`, cached again after window | second read still `Ch02` from our cache |
| `tests/test_settle.py` ×8 | fake reads | immediate/backoff/deadline/zero/retry/propagate/cap/cancel | n/a |

## 7. `Desk._write`'s "applied value": never read back (checked: `_write_value` desk.py:886-895, `_move_level.after_db` :966-979, `set_fx_type` `tokens[i]`, `label` text). **Keep local; stop calling it a read-back.** A read-back is exactly the unreliable read at issue (would report "refusals" for values not yet shown, or cost a settle poll per Tier-1 write — ×8 in `set_comp`, ×50/s in `_run_ramp` :922-946, and on the <100 ms notch path); its job is the deterministic grid mapping, tested against scales_params.md and confirmed at M5; decisions that follow writes verify their own inputs (A). Edits: docstring "quantised value sent" (desk.py:887); fix cfs.py:1186-1187, which calls `_write_master`'s `after` "the read-back" — it is the clamped request, so the no-progress clamp branch (:1190-1193) can only come from `policy.clamp_level`. Optional later: `verify=False` knob on rare Tier-2 single writes (`set_fx_type/insert/source/phantom`) via `read_until(fresh leaf, ==raw, 1 s)`.

## 8. Budgets (DESIGN §0.5): a site adds at most its deadline (+ one ≤0.4 s capped pause in the degenerate case). `setup_ringout_eqs` +≤2.0 s in 120 s; `feedback_watch`/`ring_out` arm +≤1.0 s (was ≤2.1 s) in 30 s/run formula (server.py:1724); `panic` +≤0.6 s (+0.3 s + one re-send round only when something reads open) in 10 s, sends unchanged at ~0.4 ms and always first; `recall_scene` unchanged; proposed G +2 s in the restore formula. E0/E1 add no waits. **No tool needs a larger budget.** Proposed §0.5 text: "a plain request ≤ ~2 s; a tool that verifies its own write adds at most its settle deadline (≤ 2 s) and reports `verified:false` — never an error — when the desk has not shown the value in time."

## 9. Rejected: barrier-only (would have reproduced M7's false FAILED, §0); second `/xremote` socket (echo ≠ rendered state, costs 1 of 4 client slots, doubles reconnect logic); fixed `sleep` settles (guess at UNCONFIRMED latency, paid always); expected-value overlay serving our belief (contradicts §0.6; E2 is the bounded desk-wins form); read-back in `_write` (§7). Test-infra note: `desk` fixtures use a different `EventBus` than the `conn` fixture (tests/integration/conftest.py:45-47 vs test_desk.py:36-38), so `Desk._on_write_event` never fires there for foreign `conn.set` writes; production `App` shares one bus — align when E1's foreign-write stamping matters.

---
## G3.1 Judge 1

# Judgment: read-after-write over fire-and-forget UDP — three designs, one synthesis

Repo `<repo>` @ a408a2a. Prototypes verified by running the full suite (TCP tests deselected) in each worktree: `wt/ryw-barrier` 949 passed, `wt/ryw-overlay` 934 passed, `wt/raw-minimal` 928 passed; the single failure in all three is the pre-existing worktree-name assertion `tests/test_targets_events_config.py::test_settings_defaults_and_env` (logs: `<review-reports>/raw-judge/suite-*.txt`).

## 1. Facts the three disagree on (resolved from docs/research and the code)

| # | Claim | Who | Resolution |
|---|---|---|---|
| F1 | "Positional `/` can't address field k without 1..k−1" (so echoed writes only fit leading runs) | barrier §0.2 | **Wrong as stated.** transport.md §6.6 first examples are *leaf-path* `/` writes (`/ ,s "ch/01/mix/fader 3"`, DOC 709; `/ch/02/mix/pan 50`). Any single leaf is addressable by its full path and is echoed; only fields *inside one node line* are positional. The cost argument (a lost echo stalls `timeout_s`=0.5 s × retries; useless on a 20 ms ramp) still stands and decides where to use it. |
| F2 | `/-stat/rtasource` lag was "shown by real-hardware testing" | minimal (settle.py docstring) | **No.** The `sleep(0.1)` retry and its comment "the desk applies prefs asynchronously" (meters.py:926-934) are in the initial commit 846849b (2026-09-19 23:17), before M5/M7. It is a guess (overlay is right). Only `insert/on` (HANDOVER §4b) and `config/name` (REVIEW_BRIEF §5) are hardware observations, n=1 each. |
| F3 | §4b might have been our own cache | task text | **Not the Desk cache** for the sequences that ran: `Desk._write` drops the owning section synchronously (desk.py:883 → `_invalidate_address` 275-280; `EventBus.publish` is synchronous, events.py:44-63; `/main/st/insert/on`, `/ch/NN/config/name`, `/fx/N/type`, `/-prefs/rta/*` are all in `_leaf_index`) before `validate_ringout_eqs` reads (provision.py:530 → desk.py:711-720 → 303-353). The only cache route is the in-flight-join hole (E0: `_drop` desk.py:268-273 bumps `_epoch` but leaves `_inflight[path]`, so a reader arriving after the write joins a `/node` issued before it) and that needs a *concurrent* reader; the M5/M7 driver was sequential and the dashboard's state provider reads CFS state, not `/node` (webui.py:526-541). The SET was not lost either (the setup did take). Working hypothesis: the desk's receipt→visibility latency is non-zero for at least inserts and scribble-strip names (H2 false for those classes). UNCONFIRMED for every other class → measure (§9). |
| F4 | "Exactly three RAW sites inside one operation (A,B,J); `set_level`/`adjust_level` `before` reads are not RAW, E0/E1 cover them" | minimal §1/§0 | **Incomplete.** E1 (don't cache a section read < 0.5 s after our write) stops the *amplifier* but not the first stale answer: `adjust_level` (desk.py:1007-1009) and `set_level`'s ramp start (desk.py:991 → 967) still compute an absolute write from a pre-write read if fader visibility lags the inter-call gap. Barrier's audit under its `x32ish` profile reproduced exactly this ("wrote +2.0 dB where −2.0 was meant"). Whether faders lag on the real desk is UNCONFIRMED (M5: restore of 3 `/` lines in 9 ms then a clean diff, HANDOVER §4a, suggests DSP leaves are visible within ms). A pending-write-aware read costs nothing when nothing is pending, so D1/D2/C1/D9/D10/D5 belong in scope. |
| F5 | The read overlay "closes the in-flight-before-write hole" | overlay §3.2 | **Masks, does not close.** Only for projectable fields, inside the window, with `overlay_reads` on; `_drop` still leaves `_inflight` in both the overlay and barrier prototypes. Minimal's fix (pop `_inflight[path]` in `_drop`; the fetcher resolves the futures it created — WT desk.py:276-284, 335-366) is the real one and is desk-independent. |
| F6 | Overlay does not fix E1 and then judges from cache | (found while judging) | In `wt/ryw-overlay`, `_read_sections` still caches the pre-write reply for 2 s; after the 60–400 ms window `journal.observe_section` (journal.py:445-475) marks the entry `mismatched` from that *cached* section, not from a fresh desk read → false WARNING + `desk.write_settled mismatched`, racing the settle loop's `verify`. Barrier's rule (a fetched section that disagrees with a live pending write is returned but **not cached**, WT desk.py:541-583) and minimal's E1 both avoid this. |
| F7 | `/` echo ⇒ applied? | barrier/minimal FakeDesk hard-code echo-before-apply; overlay makes it a switch | Emulator applies then echoes (X32.c 3769-3771 after `XslashSet*`, transport.md §6.6); DOC 4591-4593 calls the echo *flow control*; desk UNCONFIRMED. Keep it a FakeDesk switch, pessimistic in the latency profile; measure. None of the three *relies* on echo ⇒ applied. |
| F8 | `/status` as the fence request | barrier, minimal | Agree and correct: `,sss` reply (transport.md §3.3, DOC 632-635), unused by product code (connection.py:71 only lists it in `_NOT_PARAMS`), does not share the watchdog's `/info` FIFO (connection.py:920-948). |
| F9 | Test fixtures: `conn` gets its own `EventBus()` (tests/integration/conftest.py:46) while `Desk` gets the `events` fixture (test_desk.py:36-42) | minimal §9 | **Confirmed.** `Desk._on_write_event` never fires for raw `conn.set` in integration tests; production `App` shares one bus. Any registry keyed on the `write` event (all three designs) must fix the fixtures. |
| F10 | `_write_master`'s `after` is "the read-back" (cfs.py:1186-1187) | code comment | False: `_move_level` returns the clamped request (desk.py:966-974). All three agree; fix the comment and the no-progress branch's rationale. |

No design relies on an UNCONFIRMED fact *for a verdict*: all three prove application only by observation. Barrier and minimal use in-order service (H1, CONFIRMED for the emulator, UNCONFIRMED-but-uncontradicted for the desk) only for receipt/flow control and say so.

## 2. Scores (0–10)

| Criterion | barrier | ryw-overlay | minimal |
|---|---|---|---|
| Correctness vs protocol facts | **9** — receipt vs visibility cleanly separated; conservative fence crediting (first-attempt seq, connection.py WT 889-896); slash echo keyed by text fixes X2(ii); resend-once is idempotent-safe. −1 for F1. | **7** — `verify()` is sound (raw GETs, no cache, tri-bucket); `superseded` on differing push is right per §4.2. −2 for serving our belief as read data (DESIGN §0.6; the projection bug it already hit) and −1 for F6. | **8** — observation-only, barrier documented honestly, E0/E1 are real desk-independent bugs found and fixed. −1 F2 overclaim, −1 revalidate-loop cannot tell "pending" from "written-but-wrong" (reports NOT YET VERIFIED for a genuine misconfig after 2 s). |
| Coverage of the inventory | **9** — P1/P2/S5, D11, D1/D2 (`_leaf_settled`), D5/D13 (dump settles), D6 (restore registers lines), D3, D8 (quiesce), D12 partial (confirm mutes/EQ/final ramp step), X2; design-only for C5/C7/C8/D4/D9/S2/panic-2. Misses E0. | **8** — P1/P2/S5, D11, D1/D2 (via overlay), C5 (derived `expect`), D6, D13, envelope-wide `unconfirmed`; spec-only C7/panic; misses E0/E1, C4/S1 frame attribution. | **5** — A (P1/P2/S5), B (C5), J (panic), E0, E1, C (test); G/R recommended only; explicitly leaves D1/D2/D5/D9/D10/C1 (A-class) to chance. |
| Honesty of reporting | **9** — VERIFIED/UNVERIFIED/TIMEOUT/SKIPPED; FAILED only when verified-fresh ∧ wrong; UNVERIFIED → ok:true + `stale_possible` + named leaves with `desk_reports_raw`; TIMEOUT → error; `desk.verify` events with `source`. | **7** — best vocabulary (sent/echoed/assumed/confirmed/unconfirmed/mismatched/superseded, `GEQ_SETUP_UNCONFIRMED`, never-silent expiry, `writes_abandoned`), but a `get_channel` that returns *our* value with a side-list is the least honest read of the three. | **7** — verified / not-yet-verified, never an error for time; panic `confirmed|partial|sent|unconfirmed` + `resent[]` is exemplary; lacks the verified-and-wrong distinction for setup. |
| Testability with FakeDesk | **10** — glob→ms `apply_delay_ms`, `PROFILES{instant,x32ish}`, fx-type apply resets 64 pars, `drop_inbound(n, match, writes_only)`, `flush`, `/-fake/applydelay|flush|dropin`, `--profile`, `X32_FAKE_PROFILE` for the whole integration suite, and it actually ran the suite under `x32ish` and triaged 50 failures. | **8** — prefix rules, `slash_apply sync|deferred` switch (best fidelity to F7), `flush_applies`, measure script validated against the fake, projection sweep; no inbound-drop fault. | **8** — prefix map, `(due,seq)` heap FIFO, echo-immediate `/`, `drop_inbound_next`, 9 regression tests that fail on HEAD; no profile/CI matrix. |
| Complexity cost | **5** — +1660/−105: settle table in yaml, `_pending`+`_late`+`_confirming`+`_quiesce` registries, `_write_section` second write path. | **4** — +1839: 550-line journal incl. a raw→node-text projection for 7941 leaves (a third renderer that must agree with nodes.py and fakedesk forever), ContextVar collector, background settle task, second state layer. | **9** — +692/−69: one helper, one barrier, two ~10-line cache fixes, three sites. |
| Fit with connection.py/desk.py, §0.5, limiter | **8** — resend via `policy.acquire_write`; deadlines inside tool budgets (struct 3.0 s > §0.5's "~2 s" but within the 120 s tool); `write` event from `slash`; `settle:` optional yaml key via descriptor. | **7** — `set_rta_source(verify=)` injection and `timing:` yaml are clean; auto-settle loop adds unbudgeted background GETs; overlay threads through every read. | **9** — mirrors `recall_scene`'s poll, per-tool budget table (§8), `policy.settle_window_s`, nothing else moves. |
| **Total /60** | **50** | **41** | **46** |

Verdict: build barrier's *mechanisms* at minimal's *size discipline*, with overlay's `verify(expect=…)` API shape, connection-state clearing and measurement script; do **not** build the read overlay, the projection layer, or a background settle loop.

## 3. SYNTHESIS — what to build

### 3.0 Principles (all three agree; stated once)
1. Four indistinguishable causes of a stale read: our cache (C1), receipt ordering (C2), asynchronous application (C3), lost datagram (C4). Only **observing the value** (or a derived value) proves application; a reply to a later request or a `/` echo proves **receipt** only. No sleeps anywhere; poll the value, not the clock, to a per-class deadline.
2. Outcomes are tri-state and reported, never raised for time: `verified` | `unverified` (desk answered, value never matched: pending, snapped, refused, or lost) | `timeout` (nothing answered). A validation verdict is trustworthy only over `verified` inputs.
3. The section cache holds desk truth only: a section that disagrees with a live pending write of ours is returned to the caller but never cached (generalises E1); E0 fixed outright.
4. Never derive an absolute write, a target, or a snapshot from a read of an address that has a live pending write of ours without settling it first (kills the A-class: D1, D2, C1, D5, D9, D10, D13).

### 3.1 `src/x32mcp/settle.py` (new, pure asyncio; take minimal's `read_until` contract + barrier's types)
```python
class Verify(str, Enum): VERIFIED="verified"; UNVERIFIED="unverified"; TIMEOUT="timeout"; SKIPPED="skipped"
    ok: bool (property); @staticmethod worst(values) -> Verify   # TIMEOUT > UNVERIFIED > SKIPPED > VERIFIED

@dataclass class PollResult: verify: Verify; value: Any=None; polls: int=0; answered: int=0
                             elapsed_ms: float=0.0; stalled: bool=False; error: str|None=None
    def to_dict() -> {"verified": verify.value, "polls", "elapsed_ms"[, "error"]}

async def poll_until(fetch: Callable[[], Awaitable[T]], predicate: Callable[[T], bool], *,
    deadline_s: float,                 # wall clock from entry; covers fetches and sleeps; 0 => exactly one fetch
    first_delay_s: float = 0.0,        # first fetch immediate by default (a synchronous desk costs one RTT)
    backoff: float = 2.0, min_interval_s: float = 0.01, max_interval_s: float = 0.2,
    per_fetch_timeout_s: float | None = None,   # each fetch bounded by min(this, remaining)
    on_stall: Callable[[], Awaitable[None]] | None = None, stall_after: int | None = None,  # run once after N *answered* non-matching fetches
    retry_exc: tuple[type[BaseException], ...] = (asyncio.TimeoutError, RequestTimeout),  # = unanswered poll, keep going
    fatal_exc: tuple[type[BaseException], ...] = (NotConnected,),                         # => TIMEOUT now, error recorded
    clock=time.monotonic, sleep=asyncio.sleep) -> PollResult
    # never raises for time; predicate exceptions propagate; cancellation transparent (nothing shielded)

def raw_matches(spec: ParamSpec | None, sent_raw, got_raw) -> bool   # barrier's: half grid step from spec.scale.steps + f32 slack; int/enum/str exact; names pre-truncated to 12

@dataclass(frozen=True) class SettleClass: name; deadline_s; first_delay_s; resend: bool; quiesce_s: float
                                          derived: str | None; match: tuple[str, ...]
class SettleTable: from_config(cfg | None) -> SettleTable; for_address(addr) -> SettleClass   # fnmatch globs, first match wins, unmatched => "dsp"
```
Provisional table (device.yaml optional top-level `settle:`; descriptor OPTIONAL_KEYS; every number replaced by §9's measurement):

| class | match | deadline_s | first_delay_s | resend | quiesce_s | derived |
|---|---|---|---|---|---|---|
| dsp (default) | — (fader, on, pan, sends, eq, dyn, `/fx/*/par/*`) | 0.25 | 0 | yes | 0 | — |
| insert | `/*/*/insert/*` | 1.0 | 0 | yes | 0 | — |
| config | `/*/*/config/name|icon|color`, `/dca/*/config/*` | 1.0 | 0 | yes | 0 | — |
| route | `/*/*/config/source`, `/config/routing/*`, `/config/userrout/*`, `/config/*link/*`, `/outputs/*` | 1.0 | 0 | yes | 0 | link: invalidate partner strip |
| preamp | `/headamp/*`, `/-ha/*` | 1.5 | 0 | yes | 0 | — |
| prefs | `/-prefs/*` | 1.0 | 0 | yes | 0 | `/-prefs/rta/source|pos` → `/-stat/rtasource` (predicate from `rta_stat_expected`) |
| fxtype | `/fx/*/type` | 3.0 | 0.05 | **no** | 0.3 | par node renders as the new type (see 3.5 P1) |
| scene | `/-action/goscene|gocue|gosnippet`, `/load` | 2.0 | 0.05 | no | 0.5 | `/-show/prepos/current` |
| none | other `/-action/*`, `/-stat/*` | 0 | — | no | 0 | — (fence only) |

### 3.2 `connection.py` (barrier's fence, minimal's docstring honesty)
- `_send() -> int` increments `_tx_seq`; `_request_bytes` credits `_acked_seq = max(_acked_seq, first_attempt_seq)` on a matched reply (WT barrier 870-896). Reset both in `close()`/`_recover()`.
- `set(...) -> int`, `send_raw(...) -> int` return the seq; `write` event becomes `{address, args, seq, via: "set"}`.
- `slash(line, *, timeout=None) -> SlashReceipt(seq, rtt_ms)`: pending key `"/:" + text` (`_reply_key` maps a `/` message by `args[0]`); a late echo of an abandoned line is dropped, an echo matching no waiter falls back to the oldest `/` waiter (desk may normalise — UNCONFIRMED); publishes `write {address:"/", line, seq, via:"slash"}`. Docstring: "proof of receipt of this line, not of application".
- `barrier(after: int | None = None, *, timeout=None, retries=None) -> BarrierResult(sent, rtt_ms, acked_seq)`: free if `acked_seq >= after`, else one lock-serialised `/status` round trip; raises `RequestTimeout`/`NotConnected` only. Uses: flow control after bursts (`apply_patch_plan`, `panic` phase 2's first read doubles as it), replacing the tests' `settle(conn)` helpers, and as the "receipt proven" precondition for resend.
- `poll(address, predicate, *, deadline_s, first_delay_s=0, per_poll_timeout_s=min(timeout_s,0.25), on_stall=None, stall_after=None) -> PollResult` = `poll_until` over `request(addr, retries=0)` (the schedule is the retry policy).
- Delete `get_cached`/`_inflight` in the connection (no product caller; same E0 flaw, connection.py:581-605) — or apply the identical fix; prefer delete.

### 3.3 `desk.py`
```python
@dataclass class PendingWrite: address; node_path; field; spec; raw; value; seq; tool; via: "set"|"slash"|"raw"|"expect"
                              t_sent; deadline_at; cls: SettleClass; sends=1; done: asyncio.Future[WriteResult]|None
@dataclass class WriteResult: address; value; seq; verify: Verify=SKIPPED; observed_raw=None; sends=1; polls=0; settle_ms: float|None
@dataclass class VerifyResult: verified: dict[addr, value]; unverified: list[{address, expected, desk_reports, age_ms, sends}]
                              timeout: list[addr]; resent: list[addr]; polls; elapsed_ms
    status -> "verified"|"unverified"|"timeout" (worst); ok -> status=="verified"; to_dict()
```
- Registry `self._pending: dict[str, PendingWrite]`, filled by **every** write: `_write` (with the tool's engineering `value`), `_on_write_event` for foreign `conn.set`/`slash` through the shared bus (meters `set_rta_source`, server `_RtaPrefsWriter`, chlink, ramps — coalesce per address, newer supersedes older → older resolves `SKIPPED`), `restore`'s echoed lines (`via="slash"`, one entry per listed field via `parse_node_line`), `panic`'s `send_raw` fallback (`via="raw"`). Bounded (2048; oldest expires `unverified`, source `overflow`).
- `_drop(path)`: **E0** — `self._inflight.pop(path, None)` and bump epoch; `_read_sections` resolves the futures *it* created (minimal WT 276-284, 335-366).
- `_reconcile(path, vals, now) -> (cacheable: bool, stale: list[PendingWrite])` called on every fetched section before caching (barrier WT 541-583, without the `_late` second registry): field agrees (`raw_matches(spec, pw.raw, spec.to_raw(vals[field]))`, token fields compared numerically ±0.051) → resolve `VERIFIED, source="observed"`, free; disagrees ∧ `now < deadline_at` → not cacheable, entry counts a `stale_read` (first one logged INFO `"stale read of %s %.0f ms after our write (class %s)"` — this is the passive field measurement); disagrees ∧ past deadline → resolve `UNVERIFIED, source="observed"` (WARNING + event), not cacheable this once, desk value stands. `dump()` priming (desk.py:662-666) obeys the same rule.
- `_read_sections(paths, *, concurrency=16, settle_pending=True)`: after the fetch, if `stale` is non-empty and `settle_pending`, `await self.verify([pw.address ...])` (bounded by their remaining deadlines) and refetch those sections once. This makes `get_channel` after `label`, `adjust_level` after `set_level`, `set_phantom` after `set_source`, `resolve()` after a re-label, and `restore_plan`'s live dump right first time, at zero cost when nothing is pending and ≤ class deadline otherwise. `_leaf()` inherits it. Hot paths that must not wait pass `settle_pending=False` (none today: the CFS notch path does not read).
- `async verify(addresses: Iterable[str] | None = None, *, expect: Mapping[str, Any] | None = None, deadline_s: float | None = None, resend: bool | None = None, reason: str = "verify") -> VerifyResult` (overlay's shape, barrier's mechanics): entries = pending under `addresses` (None = all) + `expect` (raw values for addresses we did not write, registered `via="expect"`, never resent); one `poll_until` per entry, run concurrently, `fetch = conn.request(pw.cls.derived or addr, retries=0)`; `deadline_s` default = max remaining class deadline; **resend once** (`on_stall`) only when `via=="set"`, `cls.resend`, `resend is not False`, `conn.acked_seq >= pw.seq` (receipt of later traffic proven, so it is not mere reordering) and ≥ 2 answered non-matching polls — through `policy.acquire_write()` (skip silently on `RATE_LIMITED`), `pw.sends = 2`, never twice, never for fxtype/scene/slash/expect. Resolution publishes `desk.verify {address, tool, verify, source: "poll"|"observed"|"push", expected, observed_raw, settle_ms, sends, polls, cls}` and drops the entry's cached section. Raises only `DeskError NOT_CONNECTED` when there is no socket; a `NotConnected` mid-poll → that entry `TIMEOUT`.
- `async settle(prefix: str | None = None, *, deadline_s=None) -> VerifyResult` = `verify` over pending entries under an address/node-path prefix. `pending_writes(prefix=None) -> list[dict]` for `connection_status`/dashboard.
- `_on_push(address, args)`: invalidate as today + pending entry: equal → `VERIFIED source="push"`; different → resolve `SKIPPED reason="superseded"` at INFO (transport.md §4.2: never our echo, so someone else moved it; they win).
- `_on_connection_state` (subscribe `connection.state`): `degraded|disconnected|connecting` → resolve everything `TIMEOUT`, **one** WARNING + one `desk.writes_abandoned {count, reason, addresses[:50]}` (overlay). `App._recover` path also `invalidate()` (S6).
- `_write(address, raw, *, value, tool, target=None, guarded=False, confirm: bool | None = None) -> WriteResult`: registers pending; `confirm=None` → class default (True for insert/config/route/preamp/prefs/fxtype/scene, False for dsp); when confirming, `await self.verify([address])` inline and return its `WriteResult`; honours `quiesce`: a write under a prefix whose structural change (fxtype) is pending/just-visible first settles that prefix and waits out `quiesce_s` (the one sanctioned, event-keyed hold-off). `_write_value` returns `(addr, applied, WriteResult)`; docstring says "quantised value **sent**", not "read-back" (minimal §7).
- No `_write_section`/one-datagram insert write in v1 (F1 makes per-leaf `/` possible later; not needed once verify exists).

### 3.4 FakeDesk (`fakedesk.py`) — receipt→visibility latency model (barrier's, plus overlay's echo switch)
- `apply_delay_ms: dict[glob, ms]` (fnmatch, first match wins; `{}` = today's instant behaviour), `PROFILES = {"instant": {}, "x32ish": {"/fx/*/type": 600, "/*/*/insert/*": 120, "/*/*/config/*": 80, "/dca/*/config/*": 80, "/config/routing/*": 150, "/config/userrout/*": 150, "/config/*link/*": 100, "/headamp/*": 200, "/-prefs/*": 60, "/-stat/*": 120, "*": 5}}`; constructor `apply_delay_ms: Mapping | str | None`, CLI `--profile`, env `X32_FAKE_PROFILE` read by `tests/integration/conftest.py` so the whole integration suite runs under both profiles in CI (pytest param or two invocations).
- Single choke point: every *client* write path (SET `_h_param` 1037-1045, multi-arg node SET 1052-1066, `/` text `_h_slash` 1012-1021 via a parse-only `_parse_node_write`, `/-action/setrtasrc`) goes through `_write_leaf(address, raw, exclude)` → immediate `_apply_one` or `heapq` keyed `(due, counter)` (FIFO for equal dues) drained by one `TimerHandle`; `_apply_one` = `_store` + push to other clients + `_side_effects` + (for a changed `/fx/N/type`) reset the slot's 64 pars; `_after_change()` (RTA cut refresh) runs at apply time so a delayed notch delays the synthetic ring's decay; the `/-stat/rtasource` mirror is itself a delayed write under the `/-stat/*` rule (C5). GET, `/node`, meters and the closed loop read applied state only. Front-panel `set/set_value` stay immediate.
- `slash_echo: "on_receipt" | "on_apply"` (default `on_receipt` under x32ish, `on_apply` under instant = emulator behaviour) — F7 stays a switch until measured.
- Faults: `drop_inbound(n, match: str | None = None, *, writes_only=True)` (a lost SET; `drop_next` only drops replies), `flush() -> int`, `pending_applies`, `stats{applied_late, dropped_inbound, pending_applies}`; control datagrams `/-fake/applydelay ,sf glob ms | ,s profile | (none = instant)`, `/-fake/slashecho ,s`, `/-fake/dropin ,i[s]`, `/-fake/flush`.
- Follow-up (needed for C4/S1 tests, not blocking): `SyntheticRta` per-source marker so "frames from the previous source" is observable.
- Test infra: `conn_factory` must take the shared `events` fixture (F9); replace `settle(conn)` helpers (test_desk.py:45-47, test_cfs.py:76-78, test_server_tools.py:66-68) with `await conn.barrier(); fakedesk.flush()`.

### 3.5 Call sites — exact changes (file:line @ a408a2a)
| Site | Change |
|---|---|
| **P1/P2/S5** `provision.apply_setup` 503-537; `server.setup_ringout_eqs` 1599-1615 | `set_fx_type`/`set_insert` now return `WriteResult`s (class-default confirm). After the loop: `vr = await desk.settle()` (everything this apply left pending), **then** `validate_ringout_eqs` (touched sections are uncached by the reconcile rule). Result gains `verified: vr.status`, `verify: vr.to_dict()`. Server mapping: `ok ∧ verified` → success "…; all validate (confirmed by the desk)"; `¬ok ∧ verified` → `GEQ_VALIDATION_FAILED "…after N confirmed write(s): reasons"` (real blocker: shared stereo GEQ, slot taken); `status=="unverified"` → `_ok(summary="… N write(s) sent; the desk has NOT yet shown: <addr expects X, desk reports Y>…; run validate_ringout_eqs — do not assume failure, do not re-run setup", verified="unverified", warnings=[…])`; `status=="timeout"` → `DeskError TIMEOUT "desk stopped answering; state unknown — run validate_ringout_eqs"`. Budget +≤3.0 s inside 120 s. |
| **P1 par lag / D8** `desk.set_fx_type` 1389-1401, `set_geq_band` 1417 | fxtype class: verify the type leaf; on VERIFIED set `_quiesce["/fx/N"]`, `_drop("/fx/N")`, `_drop("/fx/N/par")`; `get_fx` treats a par section whose tokens do not parse as one-decimal dB while type is GEQ/TEQ as "not settled" (refetch once after quiesce); `set_geq_band` reads `/fx/N` through the settling `_section` so NOT_A_GEQ right after setup cannot happen. |
| **D11/S4** `desk.label` 1154-1182; `patches.apply_patch_plan` 551-600 | config class confirm by default → `label` returns `verify` per field; `apply_patch_plan` collects them, `await conn.barrier()` after the burst, result `verified: settle().to_dict()`; `_current_config` reads are pending-aware so idempotence sees our writes. |
| **D1/D2/C1** `set_level` 991, `adjust_level` 1008, `set_main_level` 1229, `preflight` provision.py:633-636 | nothing site-specific: `_leaf`/`get_strip` are pending-aware (3.3). `_run_ramp` intermediate steps `confirm=False`, registered (coalesced); final step registered with the dsp deadline (no inline poll); `_move_level` result gains `verify` only when a caller asks (`confirm=True` kwarg on `set_level`, used by CFS BACKOFF). |
| **D9/S2** `set_phantom` 1352-1369, `set_source` 1343, server chlink `conn.set` 1395-1407 | route/preamp confirm by default; move the chlink write into `Desk.set_link()` so it registers and invalidates **both** strips' sections; `headamp_index_for` reads are pending-aware. |
| **D10** `resolve` 543-583 | pending-aware `get_names` (free via 3.3). |
| **D5/D6/D13/G** `dump` 648-667, `restore` 1312-1341, `_diff` server.py:1211, `snapshot_desk` 1185 | `dump(settle=True)`: `await self.settle()` first when anything is pending (snapshots/diffs/undo points are desk truth). `restore`: register each echoed line's fields (`via="slash"`), `invalidate()` in `finally`, then `vr = await self.settle(deadline_s=RESTORE_VERIFY_S=2.0)` → result `written` (kept, = echoed), `verified: vr.to_dict()`, `unverified_sections`; server budget `RESTORE_BASE_TIMEOUT_S + n/RESTORE_SECTIONS_PER_S + 2.0` (server.py:97-98, 1260); summary says "N echoed, M confirmed on the desk". |
| **C5/C4/S1/C11** `meters.set_rta_source` 860-941; `cfs._arm` 901-929; `server.get_rta` 1510-1518 | `set_rta_source(conn, d, target, *, post_eq=True, verify: Callable | None = None, verify_deadline_s=1.0)`: drop `verify_attempts`/`sleep(0.1)`; when `verify` (=`desk.verify`) is given: `vr = await verify([src, pos, (+options/autogain/det if written)], expect={stat_addr: expected}, deadline_s=verify_deadline_s)`, `verified = vr.ok`, result gains `settle_ms/polls`; stub-conn unit tests keep a plain `poll_until(get stat)` fallback. `_arm` and `get_rta` pass `desk.verify`, **start frames only after** the call returns, and discard the first `arm_discard_frames=4` frames (also before `_calibrate_floor` once it moves after `_arm`); `rta_verified:false` → `cfs.state` + report warning + `get_rta` warning "spectrum may still be the previous source". |
| **C7** `cfs._notch_and_verify` 1200-1219 | on `_verify_decay` failure only: `vr = await desk.verify([par_addr], deadline_s=0.15, reason="cfs-notch")`; not VERIFIED → re-send the same depth once (`set_geq_band`, idempotent), re-run `_verify_decay` once, only then let `nc.plan` deepen; `cfs.notch` event/report row gains `write: "confirmed"|"resent"|"unverified"`. No GET on the success path. |
| **C2** cfs.py:1186-1187 | fix the comment ("`after` is the clamped request, not a read-back"); BACKOFF/`_finalize_levels` compute from `await desk._leaf(fader)` (pending-aware) rather than `ses.master_db` belief when connected. |
| **C8/C9** `cfs._restore_master` 1324-1361 | keep the loop (must run while DEGRADED where `verify` refuses); express as `poll_until(fetch=get, predicate=within grid, deadline_s=10, first_delay 0, max_interval 0.5, on_stall=resend every round)`; `desk.invalidate(address)` each round, not only at the end. |
| **J / panic** desk.py:1184-1220, server.py:1062-1073 | minimal's phase 2 verbatim (WT desk.py:1206-1302): sends first and unthrottled; then `poll_until(read 24 */mix fresh, all muted, 0.6 s)`; still-open outputs re-sent once via `send_raw`; re-check 0.3 s; `delivered: confirmed|partial|sent|unconfirmed`, `confirmed`, `unconfirmed[]`, `resent[]`, `verify{}`; skipped while DEGRADED or `verify_s=0`; never raises. |
| **D4** `recall_scene` 1262-1278 | `poll_until` on `prepos/current` with `deadline 2.0, first_delay 0.05`; when `previous == idx` report `verified: None, note: "recalling the current scene cannot be verified by the scene pointer"`; then `barrier()`; keep wholesale `invalidate()` + registry clear for everything (scene rewrites ~2000 leaves). |
| **D7** `save_scene` 1290-1310 | after the `/save` reply: `verify(expect={"/-show/showfile/scene/NNN/name": name}, deadline_s=1.0)` via node fetch predicate; `ok` unchanged, `verified` added. |
| **D3** `set_mute`/`set_main_mute` 1021, 1235 | `was` via pending-aware `_leaf` (free). |
| **S6** connection `_recover` 879-889 / `App.connect` 198-201 | publish `connection.state`; Desk clears pending + `invalidate()` on it. |
| **X2/X4** connection.py 139-143, 806-834 | slash key by text (3.2); late unmatched GET replies keep being dropped from Desk decisions (Desk never reads `conn._cache`). |
| tests' `settle(conn)` helpers | `conn.barrier()` + `fakedesk.flush()`. |

### 3.6 What the user/LLM sees
- Every confirming tool: `verified: "verified"|"unverified"|"timeout"` (+ `verify: {verified: n, unverified: [{address, expected, desk_reports, age_ms, sends}], timeout: [...], resent: [...], polls, elapsed_ms}`). Wording rule: "set/sent/echoed" ≠ "done"; only `verified` may be phrased as a fact about the desk; `unverified` always carries "check with validate_*/get_* in a moment; do not assume failure, do not re-run blindly"; `timeout` is the error envelope (`TIMEOUT`).
- Read tools: values are always the desk's. When a returned section holds a field with a live pending write that still disagrees *after* the bounded settle, the envelope gains `unconfirmed: [{address, wrote, desk_reports, age_ms}]` and the summary suffix "(N value(s) written moments ago not yet shown by the desk)". No value substitution.
- `panic`: "PANIC: 24 mutes sent in 0.4 ms (confirmed); all 24 read back muted" | "(partial); 18 read back muted, NOT CONFIRMED: mtx.1…6 — check them on X32-Edit or the front panel NOW".
- `setup_ringout_eqs`: as 3.5 row 1. `restore_snapshot`: "3 section(s) echoed by the desk in 9 ms; 3 confirmed" | "…; NOT confirmed: /ch/04/config (expects "Tony", desk reports "Ch04")".
- `connection_status`/dashboard: `pending_writes`, `verify_stats {verified, unverified, timeout, resent, observed, max_settle_ms per class}`; events `desk.verify`, `desk.writes_abandoned`.
- Logs: INFO "stale read of X N ms after our write (class C)" and `desk.verify … settle_ms` = the real-desk apply-latency data set, collected at every gig for free.

### 3.7 Tests to add (all runnable on the FakeDesk; † must fail on a408a2a product code under the x32ish/apply-delay fake)
Unit: `tests/test_settle.py` (poll_until: immediate first fetch, backoff never past deadline, `deadline_s=0` = one fetch, retry_exc counted, fatal → TIMEOUT, predicate raise propagates, cancel transparent, `Verify.worst`, `raw_matches` grid); `tests/test_connection.py` (+ seq/ack: reply credits first-attempt seq, blobs/pushes never credit, `barrier` free vs sent vs silent, `/status` not confused with `/info`; slash keyed by text, late echo of abandoned line does not ack another, non-verbatim echo fallback; `poll` tri-state; `on_stall` once).
Integration (`tests/integration/test_read_after_write.py`, run under both profiles): FakeDesk lever tests (delay makes an immediate read stale; rules ordered; `slash_echo` modes; `dropin`; `flush`); †apply_setup under x32ish → `verified ∧ ok`, 2 ≤ polls, elapsed < 2 s; †insert delay 10 s + deadline 0.3 → `unverified`, `ok False` reported as NOT YET SHOWN (server: `ok:true, verified:"unverified"`, no error), nothing cached stale, `validate_ringout_eqs` ok after `flush()`; real stereo-GEQ-shared misconfig still `GEQ_VALIDATION_FAILED`; †label then `get_channel` returns the new name (waited ≤ deadline) and under a 10 s delay returns the old name flagged `unconfirmed`; †`set_fader −20` then `adjust_fader +3` with faders 150 ms behind lands at −17; †E0: reader arriving after `label()` never joins a `/node` issued before it (`latency_ms=150`); †E1/reconcile: post-write stale section not cached, agreement confirms with 0 polls; lost SET (`drop_inbound(1)`) resent exactly once → `verified, sends==2`; two drops → `unverified, sends==2`; silent desk → `timeout` within deadline; fxtype: notch written right after `set_fx_type` survives the par reset (quiesce); †panic lost Main LR mute resent and confirmed; panic partial with `/mtx/` delayed 60 s; †`set_rta_source` with `/-stat/` 300 ms → verified after ≥2 polls, 60 s → unverified < 1.1 s; `feedback_watch` does not arm the detector on the first 4 frames; restore reports verified/unverified per section; `dump` settles first so `diff_snapshot` after a write is clean; `recall_scene` verified under `/-action/` 300 ms and `verified None` for the current scene; push of a different value supersedes (no WARNING), push of the same value confirms; degrade mid-pending → one `writes_abandoned`; `measure_apply_latency.py` recovers the fake's configured delays (±20 %).

### 3.8 Budgets and docs
DESIGN §0.5 → "a plain request ≤ ~2 s; a tool that confirms its own writes adds at most the largest settle deadline among them (≤ 3 s, fxtype) and reports `verified:"unverified"` — never an error — when the desk has not shown the value in time." §0.6 add the no-cache-on-disagreement rule and E0. §8 (fence, slash key, `write` event shape), §13 (registry/verify/settle, pending-aware reads), §14/§15 (apply_setup, CFS notch resend, arm discard), §18 (latency model, profiles, faults), §19 (envelope `verified`/`unconfirmed`), HANDOVER §4b closed with the mechanism, README tool table + panic wording, `device.yaml settle:` with `measured:` stamp once §9 ran. No tool needs a larger timeout (setup +3 s/120 s; watch/ring_out arm +1 s; panic +0.9 s worst/10 s; restore +2 s added to its formula).

### 3.9 Measure on the real desk next session (`scripts/measure_apply_latency.py <ip> --trials 50 [--second-socket] [--fx-slot N] --yes`; PA muted, scratch ch 32, restores everything, bypasses Policy deliberately, prints JSON + the `settle:` block to paste)
1. Per class (fader, mix/on, pan, eq/1/g, `config/name`, `insert/on` with sel OFF, `config/source` on the scratch ch, headamp gain ±0.5 dB on an unused preamp, `/-prefs/rta/source`→`/-stat/rtasource`, optional `/fx/N/type` in a free slot + `/node fx/N/par` until GEQ tokens appear): alternate two values; after each SET, GET every 2 ms → `visible_ms` p50/p95/max **and the count of answered-but-stale polls before the match** (0 on every trial ⇒ that class is applied before the next datagram is served, H2 true for it; >0 ⇒ asynchronous — this settles F3/F4 per class). Repeat reading via `/node` of the owning section (may be served from a different copy).
2. `/ ,s "<leaf> <value>"`: echo RTT; echo verbatim?; does the first GET after the echo already match (F7)?; is an unknown/ill-formed line echoed?
3. `/status` RTT ×100 (fence cost); `/info` vs `/status` interleaved (no cross-talk).
4. `--second-socket` holding `/xremote`: push latency for the first socket's SETs; confirm the writing socket never receives its own echo on FW 4.13 (quirk 8); does a `/` write's leaf push reach the writer?
5. `/-action/goscene`: any reply?; `prepos/current` flip latency vs the visibility of a sentinel parameter the scene changes (order, D4-ii); total recall duration.
6. `/save`: reply latency vs scene-slot name node visibility (D7).
7. Loss under burst: 200 SETs at 50/s and unthrottled, then read all back — lost count (sizes the resend policy and `writes_per_second`).
8. Run once idle and once with X32-Edit connected and busy; keep both JSONs. Afterwards grep a normal session's log for "stale read of" / `desk.verify unverified`: zero lines with real traffic is itself evidence of synchronous application for the classes exercised.

### 3.10 Migration order and what not to build
Order: FakeDesk latency model + profiles + fixture bus fix (no product change; run the suite under x32ish to get the failing list) → `settle.py` + connection fence/slash-key/poll → Desk E0 + registry + reconcile + pending-aware reads + `verify/settle` → §4b path (fxtype/insert/apply_setup/server wording) → rta prefs + arm discard → panic phase 2 → restore/dump/recall/save → CFS notch resend + C2 comment → chlink/phantom into Desk → measurement at the desk → paste `settle:` and tighten deadlines → make x32ish a required CI leg.
Do not build: read overlays or any structure that answers a read with our belief; a raw→node-text projection layer; a background auto-settle loop (expiry is lazy: on next observation, on `settle()`, or on connection-state change); per-step confirmation or `/` echoes on ramps; fixed `sleep` settles; a second `/xremote` socket or `/subscribe`-based confirmation in the server (measurement script only); resend more than once, or of fxtype/scene/`/` lines; `GEQ_VALIDATION_FAILED` from an unverified read; confirm in panic phase 1.

Relevant paths: `docs/research/transport.md` (§3.3, §4.2, §5.2, §5.4, §6.6, §7.1, §9), `docs/HANDOVER.md` (§4a/§4b), `src/x32mcp/{connection,desk,provision,meters,cfs,server,fakedesk}.py`, prototypes under `<review-worktrees>/{ryw-barrier,ryw-overlay,raw-minimal}` (take `settle.py`/`connection.py`/`fakedesk.py` from ryw-barrier, the `_drop`/`_read_sections` E0/E1 fix and `panic` phase 2 from raw-minimal, `Desk.verify(expect=)`/`VerifyResult`/connection-state clearing/`scripts/measure_settle.py` from ryw-overlay).

---
## G3.2 Judge 2

# Judge 2 — read-after-write over fire-and-forget UDP (REVIEW_BRIEF §5)

Repo `<repo>` @ a408a2a. I re-ran all three prototypes' suites (barrier 949, overlay 934, minimal 928 passed; the single failure in each is the worktree-name assertion `tests/test_targets_events_config.py::test_settings_defaults_and_env`), ran the barrier integration suite under `X32_FAKE_PROFILE=x32ish` (30 failed / 98 passed — matches its "29 + dashboard" claim, i.e. x32ish is an audit mode, not yet a CI net), and wrote three probe tests against each prototype's own late-applying FakeDesk (saved as `<review-reports>/judge2/probe-{ryw-barrier,ryw-overlay,raw-minimal}.py`; suite logs beside them). Probe results drive the scores:

| probe (FakeDesk apply latency) | barrier | overlay | minimal |
|---|---|---|---|
| `label ch2 "LATE"`, desk applies later than the design's window/deadline; read at +0.6 s and after apply | `label` returns `verify:"unverified"` at 1.0 s (re-sent once), reads `Ch02 → LATE → LATE`: **not pinned**, late-watch logs "visible after 1604 ms" | read #1 overlaid `LATE`; read #2 at +251 ms is a **Desk._cache hit of the pre-write section** yet is treated as a fresh observation → journal settles **`mismatched` + WARNING "NOT confirmed"** for a write that lands at 400 ms (also with `auto_settle` on: the cache hit ends verify's polling early); read #3 after the desk applied still **`Ch02` from cache** (2 s TTL) | E1 window 0.5 s: read at +0.6 s is cached → **pinned `Ch02` until +2.6 s** although the desk applied at 1.4 s |
| `set_level ch3 −20; adjust_level +3` (fader applies 150 ms late) | desk ends at **−17.0** (`_leaf_settled`) | (design: overlay gives −17; not probed) | desk ends at **+3.0 dB** — a +23 dB error from a pre-write `before` (inventory D2, dismissed as "not RAW") |
| 10 × `set_mute` with a uniform 30 ms apply latency | **10/10 writes re-sent** (`sync` class `resend_after_polls=2` on a 0/10/20 ms schedule) — doubles write traffic against the 50/s limiter on any desk that is merely 30 ms slow | n/a | n/a |

## 1. Scores (0–10)

| criterion | barrier | ryw-overlay | minimal |
|---|---|---|---|
| Correctness vs protocol facts | **7** — never treats fence/echo as application; polls the value not the clock. But (a) the namesake `barrier()`/`tx_seq`/`acked_seq` rests on H1 (in-order single-socket service: emulator yes, desk UNCONFIRMED) and inherits X4 (a late reply to a timed-out same-key request credits the next waiter's seq, connection.py:782-795); (b) resend-at-~10 ms assumes DSP leaves apply synchronously (UNCONFIRMED; probe above); (c) 10 class deadlines are guesses (admitted). | **5** — `verify()` (raw GET rounds, `expect=` derived address) is sound and independent of H1/H2, but the overlay serves *our belief* as desk data inside a window (its own `/fx/5..8` `fx_type_14` incident) and the journal mistakes Desk._cache hits for observations (probe: false `mismatched`, then 2 s of stale cache). E0/E1 untouched. "Desk has 4 xremote slots" is the emulator's `MAX_CLIENTS 4` (transport.md §11 table), not a desk fact. | **7** — nothing rests on an UNCONFIRMED fact; `read_until` semantics exact; `sync()` documented as receipt-only; the only design that root-causes and fixes E0 (`_drop` leaves `_inflight`, desk.py:268-273). But E1 is a fixed 0.5 s guess at an UNCONFIRMED latency (probe: pinned 2 s), and read-modify-write is left unguarded (probe: +23 dB). |
| Coverage of the inventory | **9** — §4b P1/P2/S5, D1/D2/C1 (settled base), §2.1 amplifier (dirty-until-confirmed), D5/D13 (dump/restore settle), D10/D11 labels, EQ/dyn; panic-2, recall, CFS notch, rta only specified. | **7** — P1/P2, D11, D1/D2 (via overlay), D5/D13, C5 (`expect=`), restore; panic/CFS/patches specified; C4/S1 frame gating absent. | **5** — A (P1/P2/S5), B (C5), J (panic, D12) done well; C/D kept; G (restore), R (C4 frame discard) specified; D1/D2/C1/D5/D9/D10/D13 explicitly out of scope ("cross-tool") although M5 was driven pipelined. |
| Honesty of reporting | **9** — `Verify` tri-state + SKIPPED, UNVERIFIED never raised, `GEQ_VALIDATION_FAILED` only when verified-and-wrong, `desk.verify{settle_ms,source}` events incl. `late`, `pending_writes`. | **6** — richest vocabulary (`sent/echoed/assumed/confirmed/unconfirmed/mismatched/superseded`, `unconfirmed:[…]` in every envelope, one `writes_abandoned` on disconnect) but the mechanism emits false `mismatched` and hands the model assumed values; `GEQ_SETUP_UNCONFIRMED` as an *error* envelope contradicts its own "don't make the agent re-run" argument (server.py:1599-1608 turns errors into `ok:false`). | **8** — `verified` / "NOT YET VERIFIED" as `ok:true`+warning, panic `delivered: confirmed|partial|sent|unconfirmed` + `resent[]`, stops calling `_write_value`'s `applied` a read-back (desk.py:886-895, cfs.py:1186-1187); no accounting for writes outside A/B/J. |
| Testability on FakeDesk | **8** — per-glob apply delay, heap FIFO, `drop_inbound`, fx-type commit resets 64 pars, `/` echo immediate, profiles + env switch; but `x32ish` numbers are its own priors (hence "0 resends" measured) and 29 tests stay red under it. | **8** — `apply_delay_rules`, `slash_apply sync|deferred` switch, `measure_settle.py` validated against the fake, projection sweep over 7941 leaves; no inbound-drop fault. | **8** — per-prefix delay, `(due,seq)` heap, `drop_inbound_next`, `/` echo-before-apply, delayed goscene, tests that fail on a408a2a; no profile matrix, no measurement script. |
| Complexity cost | **4** — +1660/−105, desk.py +581: registry + late-watch + `_confirming` task map + quiesce timers + `_write_section` `/` conversion + 10-row table + fence + abandoned-echo bookkeeping. The fence is the part that buys least: every answered poll already is one, and H2 being false means it never proves anything a poll does not. | **3** — +1839: 550-line journal (raw→section projection, coalescing, statuses, overflow, history), background settle task, ContextVar collector, second invalidation story; author concedes `overlay_reads` should end up off for most classes. | **9** — +692, one helper, no background tasks, `_written_at` the only new state. |
| Fit with connection.py/desk.py, §0.5, limiter | **7** — `_write`/event conventions kept, resends via `policy.acquire_write`, `/status` reserved sensibly; but inline confirm on every committing Tier-1 write changes every tool's latency, `label` blocks ≤ 1 s, struct 3 s needs the §0.5 amendment. | **5** — a belief layer over Desk._cache is DESIGN §0.6 inverted for the window; background task lifecycle in `Desk`; `timing:` yaml block is good. | **9** — per-tool budgets worked out against §0.5 (no tool needs a larger budget), two deliberate contract changes only. |
| **Total /60** | **44** | **34** | **46** |

Verdict: minimal is the right skeleton and reporting stance; barrier has the two mechanisms that actually close the A-class holes the probes exposed (dirty-until-confirmed cache reconcile, settled base before read-modify-write) and the better `slash` keying and FakeDesk fault model; overlay contributes `verify(expect=…)`, the disconnect accounting, the `timing:` block and the measurement script — and its read overlay must not be built.

## 2. Disagreements on FACTS, resolved

1. **"The M7/M5 stale reads might have been our cache."** All three say no; confirmed: `Desk._write` → `_invalidate_address` → `_drop(node.path)` is synchronous (desk.py:883, 275-280; `EventBus.publish` synchronous, events.py:44-63) and the leaf index covers `/main/st/insert/on`, `/ch/NN/config/name`, `/fx/N/type`, `/-prefs/rta/*`, `/-stat/rtasource`. The only local hole is E0 (in-flight join, desk.py:268-273 + 316-321), which needs a concurrent reader and cannot explain sequential awaits. So the desk served `/node main/st/insert` — issued tens of datagrams after the SET inside `get_inserts()`' 80-strip sweep (desk.py:711-720) — from pre-write state. N≈2 observations cannot distinguish *slow apply* from *lost SET*; the design must handle both (poll to deadline; opt-in single resend late in the window).
2. **What a reply/echo proves.** Barrier: receipt of everything earlier (H1). Overlay: "C2 UNCONFIRMED either way". Minimal: "received and dispatched, not applied". transport.md: SET no ack (§5.2), `/` echo is *flow control* (§6.6, DOC 4591–4593), emulator applies-then-echoes (X32.c 3769–3771), desk UNCONFIRMED; in-order service is the emulator's single `select()` loop, desk UNCONFIRMED. Resolution: replies/echoes are flow control only; **only an observed value proves application**; nothing safety-relevant may rest on H1.
3. **"4 xremote client slots."** Emulator constant (`MAX_CLIENTS 4`, X32.c 79, transport.md §11); desk UNCONFIRMED. Irrelevant anyway: no second socket is built.
4. **"Zero tools read back their own write / only three RAW sites" (minimal) vs barrier's audit finding "+2.0 dB written where −2.0 was meant".** Both true: no tool reads back *inside* one call, but `adjust_level`/`set_level`/CFS RAISE derive an absolute write from `_leaf()` (desk.py:991, 1008) which, pipelined after a write to the same address, is a pre-write wire read. Probe reproduces +23 dB on minimal. It is in scope.
5. **FX-type/insert/name/prefs latencies (600/400/150/… ms).** All invented; UNCONFIRMED (transport.md §5.4). Only HANDOVER §4a numbers exist: sweep 2103 sections in 0.28 s, restore 3 echoed lines in 9 ms, panic 24 sends in 0.4 ms, scene poll ≤ 2 s passed. Deadlines below are provisional and carried in `device.yaml timing:` until measured.
6. **cfs.py:1186-1187 "read-back".** Wrong belief, agreed by all: `_move_level` returns the clamped request (desk.py:966-975).
7. **`UNVERIFIED` setup: error or not?** Not taste — it changes agent behaviour. DESIGN §19 has two `ok:false` shapes and the M7 lesson is that a false FAILED makes the operator/agent re-run a Tier-2 op. Resolution: `ok:true, verified:false`, summary led by "NOT YET VERIFIED", preflight (provision.py:637) remains the gate.

## 3. SYNTHESIS — what to build

### 3.1 Rules
R1 Only a read-back proves application; `/` echo, GET replies and `/status` are flow control. R2 No sleeps: poll the value on a short schedule to a deadline; result is tri-state; time never raises. R3 Desk._cache never stores a section that contradicts a write of ours that is pending or in late-watch. R4 A read that becomes the base of an absolute write (`before`, master, head-amp index, name→strip) is settled first; if our own write to it is unresolved, refuse or treat the base as unknown — never compute from it. R5 Concluding reads (validate, preflight, dump/snapshot/diff/export, restore plan) settle first, read fresh, and retry composite verdicts to consistency or deadline. R6 Resend is opt-in per site, once, only for plain absolute SETs, only after ≥ 2 answered stale polls **and** ≥ half the deadline. R7 Tier-1 DSP leaves are not confirmed inline (time-to-cut, ramps); committing/Tier-2 executors are.

### 3.2 APIs

`src/x32mcp/settle.py` (new, pure asyncio; minimal's file + barrier's types):
```python
class Verify(str, Enum): VERIFIED="verified"; UNVERIFIED="unverified"; TIMEOUT="timeout"; SKIPPED="skipped"
    ok: bool (property); @staticmethod worst(Iterable[Verify]) -> Verify
@dataclass(frozen=True) class Settled(Generic[T]):
    verify: Verify; value: T|None; polls: int; answered: int; elapsed_ms: float; resent: bool=False; error: str|None=None
    def to_dict(self) -> dict            # {"verified": bool, "verify", "polls", "elapsed_ms"[, "resent", "error"]}
async def read_until(read: Callable[[], Awaitable[T]], predicate: Callable[[T], bool], *, deadline_s: float,
    schedule_s: Sequence[float]=(0.0, 0.02, 0.04, 0.08, 0.15, 0.25),   # delay before poll i; last repeats; never past deadline
    read_timeout_s: float|None=0.25, retry_on=(RequestTimeout, asyncio.TimeoutError), fatal=(NotConnected,),
    on_stall: Callable[[], Awaitable[None]]|None=None, stall_when: Callable[[int, float], bool]|None=None,  # (answered, elapsed) -> resend now?
    clock=time.monotonic, sleep=asyncio.sleep, what="value") -> Settled[T]
def raw_matches(spec: ParamSpec|None, sent_raw, got_raw) -> bool   # barrier settle.py:213-236 verbatim (½ grid step + f32 slack; int/enum/str exact)
@dataclass(frozen=True) class SettleClass: name: str; deadline_s: float; match: tuple[str,...]=()
class SettleTable: from_config(cfg|None); for_address(addr) -> SettleClass   # fnmatch, first match, unmatched -> default
```
Provisional table (`device.yaml` optional `timing:` → `descriptor.OPTIONAL_KEYS`, with a `measured:{date,console,firmware,trials}` stamp once the script ran): `default 0.5 s` (`*`); `slow 1.5 s` (`/*/*/insert/*`, `/*/*/config/*`, `/dca/*/config/*`, `/config/routing/*`, `/config/userrout/*`, `/config/*link/*`, `/headamp/*`, `/-prefs/*`); `struct 3.0 s` (`/fx/*/type`, `/fx/*/source/*`); `scene 2.0 s` (`/-action/go*`, `/load` — verified via `expect`); `none 0` (`/-action/*`, `/-stat/*`, `/-undo/*`). Amend DESIGN §0.5: "a tool that verifies its own writes adds at most the class deadline (≤ 3 s, inside its own budget) and reports `verified:false`, never an error, when the desk has not shown the value."

`connection.py`:
- `slash()` (542-554): key the echo by text (`_reply_key`: `"/:"+line`, barrier `_slash_key`), drop a late echo of an abandoned line, FIFO fallback to the oldest `/` waiter for a non-verbatim echo (UNCONFIRMED normalisation); publish `write {address:"/", line}` so Desk registers/invalidates the line's leaves (removes `restore`'s dependence on wholesale `invalidate()`, desk.py:1333).
- `async def sync(self, *, timeout=None) -> float` (rtt ms): one `/status` round trip (§3.3; unused elsewhere; late replies already discarded via `_NOT_PARAMS`, :71/:827); docstring: "earlier datagrams were received and dispatched if the desk serves one socket in order (emulator: yes; desk: UNCONFIRMED) — never proof of application". Uses: test helpers (replace `settle()` in tests/integration/test_cfs.py:75-77, test_desk.py:45-47), flow control after `apply_patch_plan` bursts. No `tx_seq`/`acked_seq`/`barrier()`.
- Delete `get_cached`/`_inflight` (581-605; no product caller, same E0 flaw); keep `_cache` fill from pushes only if the dashboard needs it, else delete with it. `set()`/`send_raw()` signatures unchanged.

`desk.py`:
- **E0**: `_drop` pops `_inflight[path]` and bumps `_epoch`; the fetcher resolves the futures it created (`mine: dict[path, Future]`), not whatever `_inflight` holds (minimal WT desk.py:276-284, 335-366).
- **Registry**: `_pending: dict[addr, PendingWrite(address, node_path, field, spec, raw, value, tool, written_at, deadline_at, via: "set"|"slash"|"expect", sends=1)]`, `_late: dict[addr, (PendingWrite, until)]`, `verify_stats`. Registered in `_write` (871-884) and in `_on_write_event` (286-289) for foreign `conn.set` (meters, server chlink :1402, `_RtaPrefsWriter` :1480, cfs) and for `write{line}` (parse_node_line → one `via="slash"` entry per listed field, never resent). A newer write to the same address supersedes (old → SKIPPED, silent). On `connection.state` ∈ {degraded, disconnected, connecting}: all pending → TIMEOUT, **one** WARNING + one `desk.writes_abandoned{count, addresses[:50]}` (overlay).
- **Reconcile** in `_read_sections` (after parse at :336, before caching) and in `dump()` priming (:662-666): `cacheable = _reconcile(path, vals, t1)` with barrier desk.py:541-583 semantics — field matches (`raw_matches(spec, raw, spec.to_raw(parsed))`) → resolve VERIFIED (`source:"observed"`, `settle_ms`), free; differs & before deadline → return but don't cache; differs & past deadline → resolve UNVERIFIED (one WARNING), move to `_late` for `max(5 s, 5×deadline)`; while in `_late` and differing → don't cache; late match → `desk.verify{source:"late", settle_ms}` + WARNING "tune timing:". Sections are **always returned as the desk answered** — no overlay, no `assumed`.
- `async def verify(self, select: str | Iterable[str] | None = None, *, expect: Mapping[str, Any] | None = None, deadline_s: float | None = None, resend: bool = False) -> VerifyResult` where `VerifyResult{verify: Verify(worst), verified: int, unverified: [{address, expected, desk_reports_raw, age_ms}], timeout: [address], resent: [address], polls: int, elapsed_ms}` (+`ok`, `to_dict`). `select` = prefix or address list over `_pending ∪ _late`; `expect` adds `via="expect"` entries (raw values, e.g. `{"/-stat/rtasource": 168}`, `{"/-show/prepos/current": idx}`). Rounds of concurrent `conn.request(addr, timeout=min(0.25, remaining), retries=0)` on the schedule; classify at `deadline_s` (default: max class deadline of the selection): answered-but-different → UNVERIFIED, never answered → TIMEOUT. `resend=True`: entries with `via=="set"` still differing after ≥ 2 answered polls and ≥ deadline/2 → `policy.acquire_write()` + `conn.set(addr, raw)` once. Drops touched sections at the end. Raises only `NOT_CONNECTED` (no socket). `settle(deadline_s=None)` ≡ `verify(None)`; O(0) when nothing pending. Events: `desk.verify{address, tool, verify, source: poll|observed|push|late|expect, expected, observed_raw, settle_ms, sends, polls, cls}`.
- `async def _leaf_settled(self, address) -> tuple[Any, Verify]`: if `address` in `_pending`/`_late` → `verify([address])` first; returns the desk's value and the outcome. **Use at** `set_level` :991 / `set_main_level` :1229 (`UNVERIFIED|TIMEOUT` → `before=None` → `ramp_steps(None, after, ms)` = single step to the requested absolute level, result `before: null, base_unverified: true`), `adjust_level` :1008 (→ `DeskError("BASE_UNVERIFIED", "…fader state uncertain N ms after our last write; retry shortly")`, `ok:false`, nothing written), `set_mute`/`set_main_mute` `was` (:1021, :1235; report `was_muted: null`), CFS `_write_master`/BACKOFF (via `set_level`).
- R4 sites: `headamp_index_for` (:728-765, used by `set_phantom(target)` :1363-1369) → `verify(select=["/config/routing", "/config/userrout", f"{prefix}/config"])` first; any UNVERIFIED → refuse `BASE_UNVERIFIED` (48 V on the wrong preamp is A-class). `resolve()` by name (:543-583) → `verify(select=config prefixes)` first (no-op normally); UNVERIFIED name → `AMBIGUOUS` with the reason.
- R5 sites: `dump()` (:648) `await self.settle()` first and returns `unverified:[…]` alongside the state (snapshot files record it; `_diff` server.py:1211 and `export_patch_plan` patches.py:633 surface it); `restore()` (:1312-1341): live dump settles; slash lines register leaves; `vr = await self.verify(select=touched, deadline_s=RESTORE_VERIFY_S=2.0)` → result `verified`, `unverified_sections`; `invalidate()` moves into `finally`; server budget `RESTORE_BASE_TIMEOUT_S + n/RESTORE_SECTIONS_PER_S + 2.0` (server.py:1260).
- `recall_scene` (:1251-1288): read `prepos/current` **before** the write; if already `idx` → `verified: None, note "already current; recall not observable"`; else `verify(expect={prepos: idx}, deadline_s=2.0)`; then `invalidate()` and clear the registry (SKIPPED).
- Committing executors verify before returning and carry `verify` in their dict: `label` (:1154-1182, deadline slow), `set_insert` (:1372-1387), `set_fx_type` (:1389-1401; struct; for GEQ/TEQ types additionally `read_until(node fx/N/par parses as 64 GEQ tokens)` so P1/D8 cannot see the old effect's floats; drop `/fx/N` and `/fx/N/par`), `set_source` (:1343), `set_phantom` (:1352), chlink (move server.py:1402 raw `conn.set` into `Desk.set_link`, invalidate **both** strips, S2), `apply_patch_plan` (patches.py:551-600: one `verify(select=touched config prefixes)` at the end, `verified` in result). Tier-1 leaves (`set_mute/pan/eq/comp/gate/sends`, ramps, `set_geq_band`) register only.
- `panic()` (:1184-1220): minimal's J verbatim (sends first and unthrottled; then `read_until(24 × mix/on fresh, all muted, 0.6 s)`; still-open outputs re-sent once via `send_raw`, re-check 0.3 s; `delivered: confirmed|partial|sent|unconfirmed`, `confirmed`, `unconfirmed[]`, `resent[]`; skipped while DEGRADED or `verify_s=0`; never raises).
- Docstring `_write_value` (:887): "quantised value sent", not "applied/read-back".

`provision.py`:
- `apply_setup` (:503-537): after the writes, `vr = await desk.verify(select=[f"/fx/{n}" for loads] + [f"{prefix}/insert" for inserts], deadline_s=SETUP_SETTLE_S=2.0, resend=True)`; then `st = await read_until(revalidate, lambda s: all(v.ok for v in s.values()), deadline_s=max(0.3, SETUP_SETTLE_S − vr.elapsed_ms/1000), schedule_s=(0, .05, .1, .2, .4), retry_on=())` where `revalidate` invalidates the touched sections and calls `validate_ringout_eqs` (so the verdict is taken from the same `/node` copy it will later be judged by — GET-visible ≠ node-visible is UNCONFIRMED); result adds `verified: vr.ok and st.verify.ok`, `verify: vr.to_dict()`, `settle: st.to_dict()`; on `not ok` invalidate touched again.
- `preflight` (:633-637): `await desk.settle()` first; GEQ validation through the same `read_until` with 0.5 s (P5); bus master via `get_strip` is then post-settle (P4/C1).
- `plan_setup` (:444-459), `discover_mics` (:564-582): `await desk.settle()` first (P3/P6/P7).

`server.py`: `setup_ringout_eqs` (:1599-1608): `ok` → "N write(s) confirmed by the desk; all validate"; `not ok and changed>0 and not verified` → **`_ok`** envelope, summary "NOT YET VERIFIED — T ms after N write(s) the desk still reports: <reasons>; unverified: <addresses>. Run validate_ringout_eqs shortly; do not re-run setup.", `warnings=[…]`; otherwise (nothing written, or every write visible and the verdict still wrong, e.g. shared stereo GEQ) → `GEQ_VALIDATION_FAILED` as today. `get_rta` (:1489-1517): pass `desk.verify` into `set_rta_source`; discard frames until verified + 4 frames; `rta.verified=false` → warning "spectrum may be the previous RTA source". `panic` tool (:1064-1077): surface `delivered/confirmed/unconfirmed/resent`. `restore_snapshot` (:1240-1267): surface `verified/unverified_sections`. `connection_status`: `pending_writes`, `verify_stats`. Tier-2 summaries (S3): built after `settle()`.

`meters.py set_rta_source` (:860-941): new kwarg `verify: Callable[..., Awaitable[VerifyResult]] | None = None` replacing `verify_attempts`; with it: `vr = await verify([src, pos] + written(opt, ag, det), expect={stat_addr: expected}, deadline_s=read_timeout_s)`, `verified = vr.ok`, result gains `settle_ms`, `polls`; without it (stub conns in unit tests): `read_until(_read(stat), ==expected, deadline_s=read_timeout_s, schedule from 0.05)`. Delete the `sleep(0.1)`.

`cfs.py`: `_arm` (:901-929) — pass `self._desk.verify` at :911; after `frames.start()` **discard frames until `ses.rta.verified` and `arm_discard_frames`=4 further frames**, report `discarded_frames`; run `_calibrate_floor` (:824) after the discard (fixes C4/C11/S1 ordering for BRIEF §4). `_notch_and_verify` (:1200-1219): when `_verify_decay` fails, `vr = await desk.verify([par_addr], deadline_s=0.15, resend=True)`; if `vr.resent` or the par only became visible during `vr`, re-run `_verify_decay` once before `nc.plan` deepens; `cfs.notch` event gains `write: verified|resent|unverified`. Fix comment :1186-1187. BACKOFF/ABORT absolute levels go through `set_level` → settled base (C2 on loss). `_restore_master` (:1324-1361) unchanged (must run DEGRADED via `send_raw`).

### 3.3 FakeDesk apply-latency feature (`fakedesk.py`; defaults reproduce today, 912 tests untouched)
- `apply_delay_ms: dict[glob, ms]` (fnmatch, first match, `"*"` floor), ctor kwarg, `PROFILES = {"instant": {}, "x32ish": {...provisional, replaced by measured numbers}}`, `X32_FAKE_PROFILE` env read in `tests/integration/conftest.py`; wire control `/-fake/applydelay ,sf <glob> <ms>` (no args = clear), `/-fake/flush`, `/-fake/dropin ,i n`.
- Split `_store` (:675-690) into accept → `_write_leaf(address, raw, exclude)` → immediate `_apply_one` or heap `(due, seq)` (FIFO for equal dues) drained by one timer, `_after_change()` once per drain; GET (:1023-1035), `/node` (:697-720), meters/`_refresh_cuts` (:929-975, 1103-1140), `_mirror_rta_stat` (:1094-1101) read committed `state`; pushes at commit. Every wire write path routes through it: single SET (:1037-1045), multi-arg node SET (:1052-1066), `/` text (`_h_slash` :1012-1021 → parse-only then per-field `_write_leaf`), `/-action/setrtasrc` (:1068-1078), goscene (delay the whole `_recall_scene`; knob `prepos_flip: "first"|"last"`), `/save` (reply immediate, slot-node commit delayed). Front-panel `set/set_value` stay immediate.
- Knobs: `slash_echo: "on_receipt"|"on_apply"` (x32ish = on_receipt, the pessimistic reading of DOC 4591); `reset_pars_on_type_change=True` (a `/fx/N/type` commit re-initialises `par/01..64`, barrier `_apply_one`); `stat_mirror_extra_ms` (extra lag of `/-stat/rtasource`); `SyntheticRta` per-source marker (spectrum offset or tag band keyed by committed `/-prefs/rta/source`) so "frames from the old source" is observable; `drop_inbound(n, match=None, writes_only=True)`; `pending_applies`, `flush() -> int`, `applied_late`; `stop()` cancels the timer. `latency_ms` stays *reply* latency (document at :312 and :771-781).
- CI: run `tests/integration` twice (instant, x32ish) once the x32ish run is green; until then x32ish is a required job for the new tests only.

### 3.4 Tests to add (each fails on a408a2a product code against the new FakeDesk, passes after)
`tests/test_settle.py` (fake clock: immediate first poll, schedule never passes deadline, `deadline_s=0` = one poll, retry_on vs fatal, cancellation-transparent, stall rule fires once and only after ≥2 answered + ≥ half deadline). `tests/test_connection.py`: slash keyed by text, late echo of abandoned line does not ack another, non-verbatim fallback, `sync()` receipt-only (delayed apply still invisible after it). `tests/integration/test_read_after_write.py`: (1) `apply_setup` under insert 150/fx 300 ms → `ok, verified, settle.polls≥2, <1.5 s`; (2) insert 10 s, `SETUP_SETTLE_S=0.3` → `ok False, verified False`, server envelope `ok True` + "NOT YET VERIFIED", and after `flush()` `validate_ringout_eqs` ok (not cached stale); (3) real misconfig (stereo GEQ shared) still `GEQ_VALIDATION_FAILED`; (4) label then read: first read honest desk value + `pending_writes`, read after apply = new name, nothing pinned (my probe 1); (5) apply beyond deadline → `unverified`, late-watch event with `settle_ms`, cache not pinned; (6) `set_level −20; adjust +3` under fader 150 ms → desk −17 (my probe 2); `adjust` with the base unresolved past deadline → `BASE_UNVERIFIED`, nothing written; (7) E0: reader arriving after `label()` never joins a pre-write `/node` (minimal test_desk.py:775); (8) `set_rta_source` with `/-prefs` 300 ms + stat extra 100 ms → verified via `expect`, `polls≥2`; deadline 0.3 vs 60 s → `verified False` in < 0.9 s; (9) `feedback_watch` arm with per-source RTA marker: no detection attributed to the new bus from old-source frames; `discarded_frames ≥ 4`; (10) panic with `drop_inbound(1, "/main/st/mix/on")` → `resent==["main.st"]`, 24 confirmed, fake shows 0; `/mtx/` 60 s → `partial`, `unconfirmed == mtx.1..6`; (11) restore with `slash_echo=on_receipt` + 200 ms → `verified`, `diff_snapshot` right after is clean; 60 s → `unverified_sections` listed, `written n/n` no longer claimed as done; (12) `dump()` right after a delayed write carries `unverified`, snapshot restore does not re-apply the pre-write value; (13) `set_phantom(target)` after a delayed `set_source` → `BASE_UNVERIFIED`, no `/headamp/*/phantom` datagram (count fake rx); (14) recall of the already-current scene → `verified: None`; (15) CFS notch with `drop_inbound(1, "/fx/*/par/*")` → one resend, no deepen, budget unchanged; (16) no resends for 10 mutes under a uniform 30 ms latency (my probe 3); (17) disconnect mid-pending → one `desk.writes_abandoned`.

### 3.5 What the user/model is told
`verified: true` → summaries may state desk facts ("confirmed by the desk"). `verified: false` + `unverified:[{address, expected, desk_reports}]`/`timeout:[…]` → summary prefixed "NOT YET VERIFIED", instruction to re-check, never an error, never "failed". `verified: null` → not observable (already-current scene, `verify_s=0`, DEGRADED). Errors only for verified-and-wrong (`GEQ_VALIDATION_FAILED`) and for refusing to act on an uncertain base (`BASE_UNVERIFIED`). `panic`: `delivered` + counts. `connection_status`: `pending_writes`, `verify_stats{verified, unverified, timeout, resent, observed, late}`. Log lines: one WARNING per UNVERIFIED/TIMEOUT/late, one per abandonment.

### 3.6 Measure on the real desk next session (one script, ~10 min, PA muted; start from overlay's `scripts/measure_settle.py`, which is already validated against a fake with known latency)
Per class × 30–50 trials alternating two values on scratch strips (ch 32, a free bus, opt-in `--fx-slot`): (a) SET→GET first-match ms and **number of answered stale polls before the match** (0 always ⇒ that class applies before the next datagram; >0 ⇒ async — sets `timing:` and settles H2 per class); (b) SET→`/node` first-match ms (GET copy vs node copy); (c) `/` line: echo RTT, does the first GET after the echo already match (echo ⇒ applied?), is an unapplicable line echoed; (d) `/-prefs/rta/source` → `/-stat/rtasource` lag and first `/meters/15` frame that reflects the new source (use two sources with different live content); (e) FX type load → time until `/node fx/N/par` shows the GEQ row, and whether pars reset; (f) `goscene`: any reply? `prepos/current` flip vs a sentinel parameter's change order, total recall duration; (g) `/save` reply vs slot-node visibility; (h) second socket with `/xremote`: does the writing socket ever see its own echo on FW 4.13 (quirk 8), push latency; (i) `/status` RTT distribution idle vs X32-Edit connected; (j) loss: 500 SETs at the limiter rate, count never-visible (inbound loss rate — decides whether `resend` earns its keep). Emit JSON + the `timing:` block (deadline = 4 × max observed, floor 250 ms, cap 3 s) + `x32ish` profile numbers; close transport.md §5.4/§6.6 UNCONFIRMED items and HANDOVER §4b. Afterwards the same numbers accrue passively from `desk.verify settle_ms` in gig logs.

### 3.7 Order and exclusions
Order: FakeDesk apply latency + faults (no product change) → `settle.py` + `slash` keying + `sync()` + delete `get_cached` → E0 + registry/reconcile/verify/`_leaf_settled` → §4b (`apply_setup`, server wording, `preflight`) → `set_rta_source(verify=)` + CFS arm discard → panic → restore/dump/recall → executors' `verify` + chlink into Desk + patches → CFS notch resend → measure at the desk, paste `timing:`/profile → make x32ish green and required → DESIGN §0.5/§0.6/§8/§13/§14/§18/§19 + HANDOVER §4b.
Do **not** build: `tx_seq`/`acked_seq`/`barrier()`; the read overlay/journal projection and `assumed` values; inline confirmation of Tier-1 leaves or ramp steps; resend-by-default; quiesce timers; `set_insert` as a `/` section write (revisit only if (c) shows echo ⇒ applied); a second `/xremote` socket or `/subscribe` confirmation; any `sleep` settle; `GEQ_SETUP_UNCONFIRMED` as an error code.

Relevant paths: `src/x32mcp/{connection,desk,provision,meters,cfs,server,fakedesk,patches}.py`, `docs/research/transport.md`, `docs/HANDOVER.md`; prototypes `<review-worktrees>/{ryw-barrier,ryw-overlay,raw-minimal}`; probes and suite logs `<review-reports>/judge2/`.