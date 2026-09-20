# X32 MCP Server — Build Brief (verbatim from Jim, 2026-09-19)

> **This is the original brief, kept unedited as the historical record.** Some decisions have
> since changed in the light of testing against real hardware — notably the confirmation token
> TTL (60 s here, now 300 s) and the ±6 dB guard (applied to all moves here, now only to relative
> ones). Where this document and [`DESIGN.md`](DESIGN.md) or the
> [README](../README.md) disagree, those are current and this is not.

Project codename: `x32-mcp`. Owner: Jim. Target: Windows PC on the same LAN as a Behringer X32 Rack (+ SD16 stagebox via AES50). Goal: A local MCP server exposing safe, structured control of the X32 over its OSC protocol, designed so the tool surface can later be re-skinned as a Model Hardware Standard (MHS) driver.

## 1. Context & intent
The X32 speaks OSC over UDP port 10023. The protocol is undocumented by Behringer but fully reverse-engineered by Patrick-Gilles Maillot ("Unofficial X32/M32 OSC Protocol" — the de facto spec, versioned against firmware releases). Every parameter on the desk is addressable: channel strips, buses, FX, routing, scenes, metering.

This server wraps that protocol in an MCP tool surface so Claude (Desktop or Claude Code) can:
* Read and report desk state ("what's the EQ on channel 5?")
* Make mix moves under a safety policy ("pull the vocal down 2 dB")
* Manage the scene library ("recall The Molecules scene", "back up everything before the festival")
* Eventually: assist soundcheck via metering (RTA/feedback ringing)

Real-world use cases driving this (design for these, not abstractions):
1. **Beerfestival 2026 changeover** — The Molecules play Friday, GravelAxe Saturday, per-band scenes on the X32+SD16. Scene recall, verification, and pre-show state backup via natural language.
2. **Studio sessions** — The Molecules run on the X32+SD16 with mono IEM mixes from the SD16 outputs. Quick per-musician IEM send tweaks ("more kick in Tony's ears").
3. **Content** — Instagram Reels demonstrating AI-assisted mixing. Fader moves should be ramped, not stepped, partly for audio smoothness, partly because motorised faders gliding on camera is the money shot.

## 2. Architecture
```
Claude (Desktop / Claude Code)
        │  MCP (stdio)
        ▼
x32-mcp server (Python 3.11+, Windows)
  ├── mcp layer        — official `mcp` Python SDK, stdio transport
  ├── policy layer     — safety tiers, clamps, confirmation gates, rate limiting
  ├── device layer     — X32Connection: single UDP socket, send/receive, /xremote heartbeat
  └── descriptor       — device.yaml: capabilities, parameter map, limits, guarded paths
        │  OSC / UDP :10023
        ▼
   X32 Rack  ──AES50──  SD16
```
Key design decisions
* Python 3.11+, official `mcp` SDK, raw UDP socket for OSC (not python-osc's client/server split). Hand-rolled codec.
* stdio transport — server launched by the MCP client on the same Windows machine. Config entry in Claude Desktop's `claude_desktop_config.json` (and identically as a project-scoped `.mcp.json` for Claude Code).
* Descriptor-driven, MHS-shaped. All device knowledge lives in `device.yaml`, not code. The Python code is a generic OSC read/write engine + policy enforcer.
* Single source of truth for desk state is the desk. No shadow state beyond a short-TTL cache (~2 s). Anyone might be touching X32-Edit or the front panel concurrently.

## 3. OSC protocol — implementation notes (get these right)
1. **Single socket, matched ports.** The X32 replies to the source port of the request. Bind ONE UDP socket for send and receive.
2. **Reads = send the address with no arguments.** Match request→reply by address with a per-request future and timeout (~500 ms, 2 retries).
3. **`/xremote` heartbeat** every 8 s; desk pushes parameter changes for ~10 s after each.
4. **Bulk state via `/node`.** `/node ,s ch/05/config` → whole config line in one reply on address `node`.
5. **Fader taper is NOT linear dB** — piecewise-linear, breakpoints 0.0625/0.25/0.5. Unit-test round-trips at −90, −60, −30, −10, 0, +10 dB. All user-facing tools speak dB.
6. Similar mappings for other params (log freq, Q, linear gain) — encode scale type + range in the descriptor.
7. Discovery: `/xinfo` broadcast to 255.255.255.255:10023 → `discover_consoles`.
8. Scenes: `/-show/prepos/current`, `/-action/goscene`, `/-show/showfile/scene/NNN/name`. Scene saves are guarded.
9. Meters: `/meters` subscription → binary blob replies, renewed with `/renew`. Receive loop must not crash on blobs.
10. **Mutes are inverted.** `/ch/NN/mix/on`: 1 = ON (unmuted). Tools are `mute`/`unmute`.
11. OSC 4-byte alignment; X32 tolerates missing type-tag strings on requests, always sends them on replies.

Development without the desk: Maillot's X32 emulator (Windows binaries) — first milestone runs against an emulator; final verification against the real X32 Rack with X32-Edit open.
References: https://sites.google.com/site/patrickmaillot/x32 and GitHub `pmaillot/X32-Behringer`.

## 4. Safety model (the policy layer) — enforced in code, not by prompting
* **Tier 0 — Read** (always allowed).
* **Tier 1 — Mix moves** (clamped and shaped): fader/send levels, mutes, EQ/dyn, pan. Clamps: ch faders max +5 dB, bus sends max 0 dB, EQ gain ±15 dB, single-call relative change ≤ ±6 dB (larger needs `force=true`, only after the user explicitly asks). Ramped fades (default 300 ms, ~20 ms steps). Rate limit ~50 writes/s.
* **Tier 2 — Guarded** (confirmation token): phantom, main LR/mono fader+mute, scene recall/save, routing/AES50/preamp source, mute groups, `/config/routing`. Guarded tools return `{"requires_confirmation": true, "action_summary", "confirm_token"}`; second call with the token executes. Tokens single-use, 60 s TTL.
* **Panic & recovery**: `panic()` mutes all output buses + main — Tier 1, never blocked. Snapshot-before-write: before the first Tier 1/2 write in a session, dump full desk state to `./snapshots/`. `snapshot_desk`, `list_snapshots`, `restore_snapshot` (Tier 2). `show_mode(on)`: refuses scene recall/save, tightens relative clamps to ±3 dB.

## 5. Tool surface — verbs, channel-first, dB, 1-based; JSON with `summary`
Phase 1: `discover_consoles`, `connect`/`connection_status`, `get_channel`, `get_bus`/`get_main`, `get_channel_sends`, `get_eq`, `get_dynamics`, `list_scenes`/`get_current_scene`, `dump_desk_state`.
Phase 2 (Tier 1): `set_fader(target, db, ramp_ms)`, `adjust_fader`, `mute`/`unmute`, `set_send`/`adjust_send`, `set_eq_band`, `set_pan`, `set_comp`, `panic`. Main → Tier 2 `set_main_fader`.
Phase 3: `recall_scene` (T2), `save_scene` (T2), `snapshot_desk`/`list_snapshots`/`restore_snapshot` (T2), `diff_snapshot` (plain-English diff), `show_mode`.
Phase 4: `label_channel`, `apply_patch_plan`/`export_patch_plan`, `set_channel_config` (source = T2), MCP resources for `device.yaml` and latest snapshot.

### Phase 5 — CFS² (Claude Feedback Suppression System): first-class deliverable
Metering: RTA via `/meters/15` (100 log-spaced bands, ~50 ms frames, `/renew`), RTA source via `/-prefs/rta`. `get_meters(type, duration_ms)`, `get_rta(target, frames)`.
Detector runs INSIDE the server (never through the model; <100 ms detect→cut). Heuristic on the RTA stream, thresholds in `device.yaml`: prominence ≥ 12 dB over median of ±3 neighbours; persistence ≥ 3 consecutive frames at a stable band (±1); growth: monotonic rise with ~linear dB/s slope (a held note plateaus). Confidence = weighted score; notch only above threshold. Cuts only.
Notch policy (enforced in `policy.py`): cuts only; only on the bus under test; preferred target a dual-mono 31-band GEQ inserted from the FX rack on the wedge buses (one stereo slot serves two buses); fallback bus PEQ Q 8–10 bell. −3 dB per action, deepening to −9 dB max; adjacent-bin detections merge; max notches per bus (6); halt on connection loss and restore starting fader level.
Provisioning: `setup_ringout_eqs(buses)` (idempotent; T2 on first provisioning), `validate_ringout_eqs(buses)` (read-only; auto step-zero of watch/ring_out; refuse to arm if it fails).
Mic discovery (open-loop problem): (1) automatic from desk state — unmuted, send to bus N above −40 dB floor, physical preamp source; (2) patch metadata (`mic`, `owner`, `monitor_bus`); (3) mute group 6 = "all stage mics" convention. Preflight reports discrepancies and asks; never auto-unmute.
Patch plans are data: `patches/*.yaml` or CSV with columns channel, name, colour, source, mic, owner, monitor bus. `apply_patch_plan`, `export_patch_plan`. Documented schema + one example.
Three levels: `feedback_watch(bus, notch_budget=6, arm=True)` (T1; human drives gain; `feedback_watch_stop()`); `ring_out(bus, target_gain_db=None, step_db=1.0, dwell_ms=1500, notch_budget=6)` (T2; state machine: snapshot → set RTA source → raise master in steps with dwell → on detection hold/notch/verify decay/resume → stop at budget or target → back off 3 dB → report; abort: back off 6 dB and stop; connection loss restores snapshot level; bus master never above descriptor ceiling, default 0 dB); `ring_out_system(plan=None)` (T2, one confirmation; each monitor bus then main LR; consolidated report).
Physical preconditions verified/confirmed: intended mics unmuted and routed to the bus; warn if master starts high; no programme audio during ring-out.
Reporting: `ringout_reports/` JSON + human-readable summary per session.
TODO deferred (do not build in v1): "sentry mode" mid-performance backstop.

### 5a. Observability dashboard (read-only web UI)
Python server also serves a small local web app (one HTML page, vanilla JS + canvas, no build toolchain) on `http://localhost:PORT` with a WebSocket pushing meter frames and detector events. Views: live RTA (100-band bars), spectrogram waterfall, notch overlay markers, detector state (mode, bus, confidence, budget, master level), scrolling event log. ~20 fps, decimate gracefully. Frame source is the same pluggable interface as `meters.py` (synthetic streams for demos/filming). Strictly read-only.

## 6a. Deployment targets & gig operating concept
* Primary: Windows PC on the desk LAN (stdio MCP into Claude Desktop / Claude Code).
* Gig concept: operator at FOH with an Android tablet (X32-Edit + dashboard in a browser tab); Windows PC at the desk runs server + Claude client; instructions conversational, likely by voice from a phone. Dashboard reachable on the LAN (bind `0.0.0.0`, document Windows firewall rule); dark theme, high contrast, large glanceable elements, touch-tolerant. Emergency actions never routed through the conversational path — `panic()` exists but the reflex is X32-Edit/physical (README gig checklist).
* Secondary (deferred): Android tablet via Termux. Constraints from day one: pure-Python deps, no Windows-specific paths/APIs, dirs relative to project root; README documents Termux survival settings.
* Not supported: exposing the MCP server to the internet.

## 6. Repo layout
```
x32-mcp/
├── pyproject.toml, device.yaml, src/x32mcp/{server,policy,connection,osc,scales,nodes,meters,detector,provision,webui,descriptor}.py
├── webui/ (index.html + JS), patches/ (data files), snapshots/ (gitignored), ringout_reports/ (gitignored)
├── tests/ (unit) + tests/integration/ (against emulator), README.md (setup incl. config snippets, firewall note)
```

## 7. Build order & acceptance criteria
1. **M1** Codec + connection: `/info` round-trip against an emulator. Accept: `connect` + `get_channel(1)` correct; scales tests incl. taper round-trips.
2. **M2** Read surface: all Phase 1 tools + `dump_desk_state`. Accept: full dump < 5 s, re-imports cleanly.
3. **M3** Policy + writes: Phase 2 with tiers, clamps, ramps, snapshot-before-write, rate limit. Accept: policy unit tests; ramped fade visibly smooth.
4. **M4** Scenes/snapshots: Phase 3. Accept: recall requires confirmation; `diff_snapshot` correct English diff.
5. **M5** Real-desk verification (manual checklist: reads match UI; ramp glides; mute inverted semantics; guarded ops prompt; `panic()` < 200 ms; snapshot/restore round-trip).
6. **M6** Meters + detector offline: `/meters/15` parsing, RTA tools, detector unit-tested against synthetic streams (clean music, held 330 Hz note, regenerating ring at 2.4 kHz, two rings). Accept: flags rings, ignores note, notch state machine produces correct GEQ writes in simulation. Frame source pluggable.
7. **M7** CFS² on the real desk (studio) — manual protocol.
8. **M8** Dashboard against synthetic streams then live; verified from a tablet over LAN. Accept: waterfall shows a scripted ring as a streak before the detector trips.
9. **M9** Phase 4 QoL. Backlog: Termux host, sentry mode, headset talkback.

## 8. Non-goals (v1)
Not a mid-set automatic feedback suppressor. No HTTP/remote MCP transport. No writes to `/config/routing` beyond guarded source select. No FX deep-editing (read-only summaries fine). No multi-desk. Not an X32-Edit replacement.

## 9. Operational notes
* Desk had intermittent PSU-suspected cutouts — heartbeat timeout → `connection_status` degraded, writes refuse with a clear error, auto-reconnect with backoff. Never hang a tool call.
* Windows firewall prompts on first run; README documents the exact rule (inbound UDP on the bound port from the desk's IP).
* Keep the X32 on a wall socket; don't share a strip with the PC's switching loads.
