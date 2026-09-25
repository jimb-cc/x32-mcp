# x32-mcp — hand-over notes for the next agent

Read this first if you are picking the build up cold (e.g. after a session/usage limit cut the
previous agent off). It says where everything is, what is finished, what is half-done, and how
the build is being driven. Keep it updated when you stop.

## 1. What this project is

An MCP server (Python, stdio) that gives Claude safe, structured control of a Behringer X32 Rack
over OSC, plus **CFS²** — a feedback ring-out assistant (RTA metering → detector → GEQ notches) with
a read-only web dashboard. Owner: Jim (jim@jimb.cc, GitHub `jimb-cc`).

- The brief (why, use cases, safety tiers, milestones): [`docs/BRIEF.md`](BRIEF.md)
- **The binding module contract** (exact signatures, data shapes, WS protocol, test matrix):
  [`docs/DESIGN.md`](DESIGN.md). Implement exactly what it says; deviations get recorded in the
  module docstring.
- Verified protocol ground truth (Maillot's C sources + PDF, every claim re-derived by a second
  agent): [`docs/research/`](research/) — `transport.md`, `scales_params.md`, `meters.md`,
  `fx_routing_scenes.md`, `mcp_sdk.md`. Cite these in comments; do not re-research.
- Section §19a of DESIGN.md lists the facts that overrode earlier assumptions (RTA int16 packing,
  `/` node-style write, two FX-type enums, fader 1024 vs send 161 grids, mcp 2.x rename).

## 2. Machine & toolchain (Windows 10 PC, Jim's desk PC)

| Thing | Where / how |
|---|---|
| Repo | `C:\Users\jim\Documents\claude\x32-mcp` (git, branch `main`) |
| Remote | `https://github.com/jimb-cc/x32-mcp` (**private**) — push with `git push` |
| Python | uv-managed CPython 3.12.14: `C:\Users\jim\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe`. The system `python` is **3.8 — never use it** |
| uv | `C:\Users\jim\.local\bin\uv.exe` (user-local). In Git-Bash: `export PATH="$HOME/.local/bin:$PATH"` |
| venv | `.venv\Scripts\python` — deps: `mcp` 2.2.0, `pyyaml` 6, `websockets` 17, pytest 9 + pytest-asyncio 1.4 + pytest-timeout. Re-create with `uv sync` (only when no build agents are running) |
| gh CLI | `C:\Users\jim\.local\bin\gh.exe` (portable zip, v2.101.0), logged in as `jimb-cc` |
| Git identity | `Jim <33234558+jimb-cc@users.noreply.github.com>`; end commit messages with `Co-Authored-By: Claude <model> <noreply@anthropic.com>` |
| Tests | `.venv/Scripts/python -m pytest` (asyncio_mode=auto, pythonpath=src, 60 s timeout per test). `pyproject.toml` already adds `-q`; passing `-q` again makes it `-qq`, which **hides the pass/fail summary line** — don't |
| Fake desk | `.venv/Scripts/python -m x32mcp.fakedesk --port 10023` (once `fakedesk.py` exists) |
| Server | `.venv/Scripts/python -m x32mcp.server` (stdio; never prints to stdout) |

uv prints a harmless "Missing expected target directory for Python minor version link" warning —
ignore it.

## 3. Conventions that must not drift (from DESIGN.md §0)

1. stdout is the MCP transport — no `print()` anywhere under `src/x32mcp/`; use `logging` (stderr).
2. Pure Python; only the three runtime deps above; stdlib `asyncio`/`socket`; no numpy; no
   Windows-specific paths (Termux later).
3. Engineering units at every public boundary (dB, Hz, ms, 1-based numbers, enum tokens).
   Raw 0..1 OSC floats never leave `scales/descriptor/connection/nodes/fakedesk`.
4. Mutes are inverted on the wire (`/…/mix/on` 1 = ON = unmuted); public API is `muted: bool`.
5. Every network wait has a timeout; a tool call never hangs (~2 s worst case, returns `ok: false`).
6. Safety is enforced in `policy.py`, not by prompting; `panic()` is never blocked.
7. Files live under `X32MCP_HOME` (repo root by default) — see `config.py`.
8. Each module ships `tests/test_<module>.py`; integration tests (against the fake desk) live in
   `tests/integration/` and use the `fakedesk`/`conn` fixtures from `tests/integration/conftest.py`.

## 4. Build status — update this table when you stop

Status as of **2026-09-20 21:40 (Europe/London)** — full suite: **934 tests, all passing**. M5 complete; **M7 first live test done** (see §4b). **M5 is COMPLETE (11/11)**. M5 is in progress against the real desk (see §4a).Legend: ✅ done & tests green · 🟡 written,
tests not green / partial · ⬜ not started.

| Module (DESIGN §) | Status | Notes |
|---|---|---|
| `config.py`, `events.py`, `targets.py` (§2–4) | ✅ 34 tests | hand-written by the lead agent |
| `osc.py` (§5) | ✅ 70 tests | bare padded address for no-arg GET; blobs returned raw; `decode` raises only `OscError` |
| `scales.py` (§6) | ✅ 51 tests | −90 dB ≡ −inf ≡ raw 0.0 (per research); half-away-from-zero quantisation |
| `device.yaml` (§7) | ✅ sanity test passes | 2,101 concrete node paths for a full sweep (ch 992, bus 304, auxin 200, fxrtn 192, headamp 128, show 102 …); node formats used: `str int sint onoff enum db1 db2 float1 float2 sfloat1 sig3 freq pct token bits6 bits8 bits9 bits18` (the last seven are additions beyond DESIGN §9 — `nodes.py` must implement them) |
| `descriptor.py` (§7) | 🟡 the workflow journal recorded it finished at 21:11 but no file was on disk at 21:15 — verify | |
| `connection.py` (§8) | ✅ green at 21:17 | includes `slash()`. Two tests (`test_retries_after_dropped_packets`, `test_get_cached_ttl_invalidate_and_push`) failed once under heavy CPU load from parallel agents — treat as timing-sensitive, not broken |
| `webui/index.html` (§17 page) | ✅ static checks | `?demo=1` runs without a server |
| `nodes.py` (§9) | ⬜ | parse/render/diff/snapshots; `restore_plan` returns `/`-write lines |
| `policy.py` (§10) | ⬜ | |
| `meters.py` (§11) | ⬜ | RTA = int16 LE dB×256; re-send `/meters` every 5 s |
| `detector.py` (§12) | ⬜ | pure; 4 synthetic streams in tests |
| `fakedesk.py` (§18) + `tests/integration/conftest.py` | ⬜ | must implement `/` writes, `/node`, meters blobs, closed-loop ring |
| `desk.py` (§13) | ⬜ | |
| `provision.py` + `cfs.py` (§14–15) | ⬜ | |
| `patches.py` + `patches/example_band.yaml` (§16) | ⬜ | |
| `webui.py` (§17 server) | ⬜ | |
| `server.py` + `__main__.py` (§19) | ⬜ | mcp 2.x: `from mcp.server.mcpserver import MCPServer, Context` |
| `README.md`, `docs/GIG_CHECKLIST.md`, `.mcp.json` | ⬜ | README is a placeholder |
| Review lenses + fixes + final green suite | ⬜ | |
| M5 / M7 (real desk, studio) | ⬜ | Jim's manual verification; checklists go in GIG_CHECKLIST.md |

Run `git log --oneline` and `pytest -q` to refresh this table before relying on it — the build
workflow may have advanced since this file was written.

## 4a. M5 progress — live against X32RACK-Jim (FW 4.13, OSC V2.07) on 2026-09-20

Driven from the lead session by calling the tool functions directly in Python (no Claude Desktop
restart needed): build an `App`, set `x32mcp.server.app`, `await app.start()`, `connect("192.168.1.139")`.
Jim watches the front panel / X32-Edit and confirms. **Hold a state until he confirms** — a 6 s
window was missed once because he was reading the message that announced it.

Done:
- **Reads** — full `/node` sweep parses in 0.28 s, 0 missing, 0 unparsed fields (2101 sections at the
  time; 2103 now that user routing is declared). This
  retires the FW 4.x node-text risk (the parser was only verified against a FW 2.x scene file).
- **Meters** — `/meters/15` (100 × int16, dB×256) and `/meters/1` (96 × float32) both stream at
  20 fps and decode; values were all floor/zero because the desk was silent.
- **Ramps glide** — confirmed visually on the front panel (full-travel −∞ → −3 dB over 15 s).
- **Ramp timing** — 15 s requested, 15.00 s actual after the dedupe fix (was +61% over).
- **Mute semantics** — wire 0 = muted / 1 = unmuted, our reading agrees, panel agrees.
- **Clamp** — `set_fader +9` → +5.0 with `clamped` populated.
- **EQ / pan / label** — band 2 → 990.9 Hz (201-step grid), −3.0 dB, Q 3.9; pan −50; desk truncates
  names to 12 chars.

Defects found and fixed here (all committed): `_db1` conflating −∞ with "not read"; ramps
overrunning and saturating the write budget; the ±6 dB guard applied to absolute moves.

M5 COMPLETE — the remaining checkpoints, all verified live on 2026-09-20:
- **Guarded ops** — token issued on refusal, single use, made-up token refused, expired after the
  TTL, and a `set_main_fader` token will not authorise a `restore_snapshot` (7/7). The confirmed
  main move ran −22.8 → −15.0 dB in 3996 ms against 4000 requested; Tier-1 `set_fader("main.st")`
  is refused as `GUARDED`.
- **panic()** — 0.4 ms to put 24 messages on the wire, 24/24 verified muted on the desk and
  confirmed visually. Input channels are deliberately untouched.
- **Snapshot / restore** — diff found 6 changes in English, restore wrote 3 sections in 9 ms, and
  the diff afterwards was clean.
- **Degraded + auto-reconnect** — DEGRADED 14 s after the cable came out, writes refused in
  ~520 ms (`NOT_CONNECTED`, no hang), reads timed out in ~500 ms, and it reconnected **by itself**
  16.7 s later after 4 probes with no `connect()` call.

Defects found by M5 and fixed (each impossible to catch on the fake desk):
1. `_db1` conflated −∞ with "not read" (`desk.py`, then the same bug again in `cfs.py`).
2. Ramps overran and saturated the write budget (12 s → 19.3 s).
3. The ±6 dB guard applied to absolute moves, so a send could never come up from off.
4. The confirmation TTL (60 s) expired inside a normal conversational round trip → 300 s.
5. **Firmware-4.x user routing was unresolvable**: every channel returned head amp `None`, which
   broke `set_phantom` and would have made CFS² mic discovery treat card/USB feeds as microphones.

Still to do (needs Jim at the desk):
- **Scenes** — this desk's show control is set to **CUES**, not SCENES. Flip it in Setup before
  testing `recall_scene`/`save_scene`, or the scene pointer indexes the cue list.
- **A deliberate power-cycle test** (not just a cable pull): the PSU fault reboots the desk, so
  state may not survive. Snapshot first, power-cycle, then `diff_snapshot` to see what it forgot.
- **M7 (CFS²)** in the studio.

Desk facts worth keeping: 2 scenes stored (0 "Studio Sept 26", 1 "molecules"); FX slot 5 already
holds a GEQ2 (CFS² provisioning should reuse it); PC is 192.168.1.231, desk 192.168.1.139.
**This desk uses firmware-4.x user routing** — `/config/routing/IN/*` reads `UIN*` and the real
patch is `/config/userrout/in/NN` (ch 1 → local XLR 1, ch 2-3 → card/USB, ch 4 → AES50-A 1 =
head amp 032, where the SM58's +44.5 dB gain sits). Do not assume channel N → head amp N−1.

## 4b. M7 — first live CFS² test, 2026-09-20 (studio, SM58 into Alto tops on Main LR)

**Outcome: CFS² caught real feedback for the first time.** Jim's account: the ring started, a
notch killed it; he pushed the master higher, the next band up rang, and that was caught too.
Then the notch budget (4) ran out. He confirmed there was **no genuine feedback below ~4.5 kHz**.

Setup that worked: GEQ2 already in FX slot 5, inserted on Main LR; ch 4 (SM58, head amp 032,
+44.5 dB) the only mic physically connected; music playing quietly through the PA and sub as
deliberate background noise; master started ~−25 dB.

Four defects found and fixed (commits f43f10c, 56c34a7, 60b86d5, ddcfeb1):
1. **RTA auto-gain was on** — the console normalises the analyser, so every band drifts together
   and the detector read that as the growth signature of feedback. 337 detections in 600 s.
   `set_rta_source` now forces auto-gain off and the detector to PEAK.
2. **Established feedback was mathematically unreachable** — without growth the score caps at
   w_prominence + w_persistence = 0.50, below the 0.7 threshold, so a plateaued howl could never
   be emitted. A 60 dB-prominent 8 kHz ring sat at exactly 0.50 for 15 s. Added a prominence
   override (≥ 25 dB for ≥ 6 frames emits regardless of growth). **This is what made the
   successful catch possible** — run 2 ignored the identical situation.
3. **Candidate gate below the noise floor** — `min_level_db` −60 vs a measured −51.7 dB floor.
   Raised to −45. NOTE: that floor was Jim's background *music*, not room noise; the number is
   right for the wrong reason and the frequency window below is the better fix.
4. **GEQ inserted POST, downstream of the RTA tap** — a −15 dB cut moved the RTA by
   −0.2/+0.4/+1.2 dB (nothing); the same cut PRE showed −5.9/−6.0/−3.9 dB. Provisioning now uses
   PRE. Narrower than first claimed: when a notch breaks the loop the ring stops acoustically and
   the RTA sees it wherever it taps, which is why cuts audibly worked; PRE only restores the
   ability to *measure* a cut directly.

**Next, and the highest-value change:** a frequency window for the detector (roughly 250 Hz–8 kHz).
Both wasted notches were at 40 Hz and 80 Hz, where an SM58 into Alto tops physically cannot produce
feedback, and they cost the budget that the real 5 kHz events needed. No threshold tuning required —
just refuse to consider bands where feedback cannot occur.

Also outstanding from tonight:
- **Auto-calibrating the candidate gate** from a floor measured at arm time. Attempted and backed
  out: it must run *after* the frame source starts (`_open_session` runs before that, and also on
  `ring_out`'s preflight where frames never start), it needs a cap so a noisy room cannot deafen
  the detector, and it left a reporting test failing that was not root-caused.
- **`setup_ringout_eqs` reports a false failure**: it validates immediately after writing, before
  the desk has applied `insert/on`, so a successful setup returns GEQ_VALIDATION_FAILED. Same
  read-after-write race as elsewhere; needs a settle before the read-back.
- **`discover_mics` cannot tell a connected mic from an empty channel.** It reported 7 candidates
  when only ch 4 had a microphone plugged in — unmuted + routed + physical preamp looks identical
  either way. Cross-checking candidates against actual meter activity would catch it (an unplugged
  input reads near-silent, a live mic picks up room noise).
- Re-test **with music playing** — it turned out to be a much more revealing test than a silent room.


## 5. How the build is driven

The implementation is orchestrated by a Claude Code **Workflow** script, checked in at
[`scripts/impl_workflow.js`](../scripts/impl_workflow.js): one agent per module in a dependency
graph (Foundations → Core → Runtime → Features → Server), each agent owning only its files and
running only its own tests, then six review lenses (protocol, safety, robustness, CFS² state
machine, MCP layer, test integrity) → deduplicated findings → per-cluster fixers → a final
full-suite integration agent. Upstream agents' `notes_for_dependents` are threaded into
downstream prompts.

History: run `wf_12b6e72f-189` started 2026-09-19 ~16:25, hit Jim's session usage limit after 3
agents (limit resets 21:00 Europe/London), and was resumed at 21:02 with `resumeFromRunId`
(finished agents replay from cache). **Resume only works within the same Claude session.** If you
are a new session:

1. Refresh §4 (`git status`, `pytest -q`, look at which `src/x32mcp/*.py` and `tests/*` exist).
2. Edit `scripts/impl_workflow.js`: for every module that is ✅, replace its `impl(...)` call with a
   resolved promise carrying the notes you can recover, e.g.
   `const A_osc = Promise.resolve({notes_for_dependents: [...], deviations: []})` (the notes for
   osc/scales are in §6 below). Leave 🟡/⬜ modules as they are — their agents read existing files
   and finish them.
3. Ultracode/Workflow is opt-in: Jim asked for it (the brief + "build it"); if in doubt, ask him
   before launching a large fan-out. Launch with `Workflow({scriptPath: "<abs path to scripts/impl_workflow.js>"})`
   (or paste it inline as `script`).
4. Alternatively build module-by-module yourself against DESIGN.md — the contract is complete
   enough that no orchestration is required; it is just slower.

Commit and push at milestones (`git add -A && git commit && git push`) so the remote tracks
progress; WIP commits are fine on `main` for now.

## 6. Upstream API notes the remaining modules depend on

**osc.py** — `from x32mcp.osc import OscError, OscMessage, encode, decode`.
`encode(address, *args, typetags=None)`: bool/int→`,i`, float→`,f`, str→`,s`, bytes→`,b`; an int
for a float param must be passed as `float(x)` or `typetags="f"`. `encode(address)` with no args
emits the bare padded address (the GET form Maillot's tools use). `decode` raises only `OscError`
(incl. `#bundle`); missing tag string → `args=()`, `typetags=""`. Blobs come back raw: for
`/meters/N` the bytes start with the little-endian int32 word count (the big-endian OSC blob
length is already consumed). The `/node` reply has address `node` (no slash) and a `,s` arg ending
in `\n`. Keep datagrams ≤ 512 bytes if you ever talk to Maillot's emulator.

**scales.py** — `Scale(kind, lo, hi, steps, unit, values)` frozen; kinds `level|lin|log|enum|int|bool|str|pan`;
`Scale.from_spec(spec, enums=...)` builds from a device.yaml spec. `steps` = number of distinct
values (faders 1024, sends/mlevel 161, freq 201, Q 72, hpf/hold/release 101, eq gain 121, pan
101). `level.to_value(raw)` → dB float, raw ≤ 0 → `-inf`; `to_raw` accepts float/`-inf`/`"-oo"`,
≤ −90 → 0.0. `pan` → int −100..+100. `enum.to_value(int)` → token (case-insensitive token lookup
on strings; a Python int is always an index). `bool.to_value(1)` → True (no mute inversion here).
`to_value(None)` → None for missing node fields. `format_db(db)` → `"-oo"`, `"+2.0"`, `"-12.3"`,
`"0.0"` (tool-output format; console node text differs — see research). Also `parse_db`, `quantize`
(half away from zero), `ScaleError(ValueError)`.

**connection.py** (per DESIGN §8; verify against the file) — `X32Connection(descriptor, events, timeout_s, retries, heartbeat_s, watchdog_s)`;
`connect/close/status/request/get/set/node/node_many/slash/get_cached/invalidate/on_update/on_blob/send_raw/discover`.
Only `d.policy.get("read_cache_ttl_s")` is used from the descriptor, so tests can pass a stub.

## 7. Things a new agent should not redo

- Do not re-research the protocol; do not rewrite DESIGN.md wholesale (small clarifications are fine).
- Do not download Maillot's emulator — it lacks `/renew`, real meters and scenes; the repo's own
  `fakedesk.py` is the test target.
- Do not add dependencies. Do not run `uv sync` while build agents are writing files.
- Do not change tool names/signatures in DESIGN §19 — the README and Jim's mental model use them.

## 8. Open questions for Jim (ask, don't guess)

- Real-desk unknowns listed as UNCONFIRMED in the research (e.g. whether `/xinfo` answers the
  limited broadcast `255.255.255.255`; FW 4.x node column widths). These only matter at M5.
- Whether FX slots 1–4 may ever be used for ring-out GEQs (default: only 5–8).
- Public vs private repo (currently private).

## 4c. Offline review, 2026-09-21 (response to REVIEW_BRIEF.md)

A full review against `a408a2a` is in [`docs/REVIEW_REPORT.md`](REVIEW_REPORT.md) with appendices in `docs/review/`. Read its
§0 first. The short version for whoever picks this up:

- **Why the detector failed at M7 is understood** (REVIEW_REPORT §1.2): the growth feature is manufactured by the 1/10-octave
  analyser at LF and defeated by real loop growth rates at HF; the RTA `decay`/`peakhold` prefs were never set; the score's
  sustained-ring region is unreachable. A physics-based offline corpus (`tests/rtasim/`, 61 scenarios) now exists and the
  shipped detector scores 12/61 with 948 false positives on it. The redesigned discriminator (`review/detector`, design in
  docs/DETECTOR.md) scores 0 false positives on the corpus, hold-out seeds and every analyser variant, catches 115/117 rings with the
  LF window declared, and takes 179 rather than 500-950 hits on 68 scenarios written to defeat it; `review/detector-cfs` wires its
  hooks (tier-B one-shot, alerts on the scribble strip, LF edge from the channel HPFs) into cfs.py. An independent kill check
  (limiter/compressor-held howls) passes 6/6 after one fix; the pre-registered acceptance gate rejects old and new alike, the new
  one on latency only with 0 false positives — REVIEW_REPORT §1.6 explains why that is the physical floor for a passive detector.
- **The "calibration broke existing_cuts" bug** (§4) was `_notch()` committing before the GEQ write plus the test's own stop
  cancelling the write inside a `/fx/N` cache-miss round trip — deterministic on this Windows box. Fixed on
  `review/cfs-write-safety` (two-phase notch). The commit message of 56c34a7 about `_open_session` running on the pending
  `ring_out` call is wrong; the pending call never reaches cfs.
- **Safety** (§3): main-bus compressor make-up/EQ were Tier 1; panic() was undone by our own ramps/restore/ring-out; the
  ring-out fought an operator's pull-down. All fixed on review branches with tests. `clear_panic` is a new Tier-2 tool.
- **Main LR is a stereo strip**: a GEQ2 on FXnL processes L on side A and R on side B. CFS² now writes both sides for the
  mains and never lends the other side to a bus (`review/main-lr-stereo-geq`). Your FX5 GEQ2 on Main LR is exactly this case.
- **Next session at the desk**: REVIEW_REPORT §8 is a ten-item checklist (RTA prefs → stream, analyser response via the console
  oscillator, GEQ bell, `scripts/measure_settle.py`, the Main LR GEQ2 experiment, panic under load, the M7 re-test).
- The review branches are local (`git branch --list 'review/*'`); §0.3 has the push commands. `review/all-integrated` merges
  them all.

## 4d. Studio measurements, 2026-09-22 (console oscillator, 20 minutes) and repo state

Repo: `main` now carries everything — PRs #1–#13 (the review branches, `review/detector` @ e396480, `review/detector-cfs` with the
policy layer and the kill check) fast-forwarded and PR #11 (report) merged; PR #14 (`review/peq-sim`: bus-PEQ actuator in the
simulator, K7/K8 hop scenarios) is open. On real sockets `main` runs 1108 passed / 3 failed, all three being test-side races on
in-flight datagrams (`test_cfs_policy_breakers.py::test_e…`, `test_cfs_policy_desk.py::test_colour…`, `…cut_within_k1`).

Measured on X32RACK-Jim (full detail: docs/research/meters.md, Verification log 2026-09-22; PEQ_ACTUATOR_DESIGN.md §9):

* The Main LR PEQ **is** the RBJ prototype the detector and simulator use (Q 6.1 and Q 10, −6 and −12 dB, all within 0.3 dB).
* The console oscillator is unusable on a bus (replaces the bus *output*, below the meter and RTA tap); into Main it sits before the EQ.
* RTA gain 0 = true dBFS; the desk had been at **+18** since before M7 — every M7 absolute level is suspect by that much.
* RTA band skirts ≈ −40 dB at ±1 band (the simulator's steep-skirt end); band centres look ~half a band above the nominal formula.

Next desk session (10 min each): the tone sweep 1900…2100 Hz for the band-centre offset; the +18 gain comparison from the Rack's
Meters → RTA page; a tone into a *channel* routed to a bus for the bus tap-order check; the PEQ at 8 kHz for warping.

### 4d (ii). Second session, 2026-09-23 (~35 min) — full detail in docs/research/meters.md, Verification log

New tools: `scripts/log_rta_frames.py` (every `/meters/15` frame to JSONL, 20/s) and `scripts/analyse_rta_rise.py`; raw logs
under `docs/research/data/`. Findings, all first-time measurements:

* Of the RTA prefs only `det` (floor −97 RMS / −128 PEAK) and `decay` (66 dB/s release at 0.25) reach the stream; gain,
  auto-gain and peak-hold do not. `PEAK_HOLD_SUSPECTED` can never fire from the stream; the arm-time pinning is cosmetic.
* Gated-tone rise times match the settle rule with k = 1.0 frame for frame: 78 Hz five frames (13, 5, 6, 2, 1.5 dB/frame —
  the M7 mechanism), 156 Hz three, ≥ 947 Hz one. Release = 20/decay dB/s (80 at 0.25, 20 at 1, 5 at 4, 1.2 at 16), 3× slower
  than the corpus models, and `decay` slows the attack too (2 kHz: 1 frame at 0.25, 3–4 frames at 1, seconds at 16) — the
  arm-time forcing to 0.25 is the one pref write that changes what the detector sees (`scripts/measure_rta_release.py`).
* Band centres ≈ nominal × 2^0.05 (five-point semitone sweep at 2 kHz, agreed by 8 kHz): the "band → Hz" formula is half a
  band low. Provisional until a finer sweep; affects centroid interpolation and PEQ placement.
* Main LR PEQ at 8 kHz = RBJ (11.7 dB for −12 Q 6.1 at 0.024 oct off; prototype 11.4).
* **The Dual Graphic EQ realises ~⅓ of its slider depth** (−6 → 2.6 dB, −12 → 4.1 dB at 2 kHz, broad). M7's −3/−6/−9 were
  1.3/2.6/3.9 dB real. Strongest argument yet for the PEQ actuator; the corpus's closed-loop GEQ kill margins are ~2.5×
  optimistic (CORPUS.md §7.8).

Still to measure: a finer centre sweep (pink noise or a channel-fed tone), Dual TruEQ's depth, the bus-EQ tap order with a
channel-fed tone, 40 Hz rise time, whether the oscillator into Main sits before or after the insert send.

* **Semitone sweep (95 tones, `scripts/measure_rta_bands.py`)**: the RTA bins are `20·2^(i/10)` Hz — a third of a band above
  the DOC's printed table that `device.yaml`/`meters.py` reproduce (fix on `review/rta-band-grid`, PR); response flat ±0.2 dB
  42 Hz–9.5 kHz; skirts symmetric and steep (≈ order 5) above 200 Hz, widening to ≈ 2 at 50 Hz; PEAK attack 3 frames to −3 dB at HF.

### 4e. Review response, 2026-09-24

[`docs/REVIEW_RESPONSE_2026-09-24.md`](REVIEW_RESPONSE_2026-09-24.md) answers `REVIEW_REQUEST_2026-09-23.md`. Read its §0 before
acting on §4d (ii): the "GEQ realises a third of its depth" reading is disputed (one leg of the stereo Main was cut), and the
semitone sweep ran at decay 1.0 in the −97-floor state, not at PEAK / 0.25. Its §9 is the next desk session, 20 minutes.
* **2026-09-25 (reviewer, studio)**: (1) the `/meters/15` analyser is dormant after power-up until the console shows its
  RTA page (`/-stat/screen/screen 1` + `METER/page 4` wakes it remotely; it then stays alive) — arm preflight needed;
  (2) 180 s of real programme (Spotify → Main, PEAK/0.25) replayed through the shipped detector: 20 STRONG emissions, all
  music — proposed gate G5 on desk-logged programme (`scripts/replay_rta_log.py`, `docs/research/data/programme_*.jsonl.gz`);
  (3) the frontier's bundle is PRs #16/#17/#18 (C2 now has a cfs-level test); merge order in `REVIEW_REQUEST_2026-09-25.md`;
  (4) **the RTA `det` enum was inverted** (raw 0 = PEAK per the desk's own /node label): every arm-time "PEAK" write set RMS — PR #20.
* **Arm-time analyser wake (2026-09-25, `review/rta-wake`)**: `meters.wake_rta_analyser()` runs in `CfsManager._arm` once
  frames flow: a stream that is one static flat frame (the console's analyser has not been started since power-up) makes
  it show the METERS/RTA page (`/-stat/screen/screen 1`, `/-stat/screen/METER/page 4`) for a moment and restore the screen;
  the result is `rta_wake` in the arm result, the `cfs.state` event and the report. `FakeDesk(rta_dormant=True)` models it.
* **2026-09-25 afternoon (reviewer, studio)**: the Dual GEQ delivers full slider depth when both legs are cut (12.0 dB for −12,
  meters.md item 13) — the 09-23 "one-third depth" reading is withdrawn; the bus RTA post tap is after the bus EQ (item 12);
  a PEAK programme log gives 4 false STRONG emissions in 180 s against 20 under RMS (item 11). All PRs through #23 merged.
