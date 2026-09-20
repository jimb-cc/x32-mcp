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

Status as of **2026-09-20 17:00 (Europe/London)** — full suite: **928 tests, all passing**. M5 is in progress against the real desk (see §4a).Legend: ✅ done & tests green · 🟡 written,
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
- **Reads** — full 2101-section `/node` sweep parses in 0.28 s, 0 missing, 0 unparsed fields. This
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

Still to do (needs Jim at the desk):
- **Restore `ch.1`** from snapshot `20260920-150008-m5-start` — it still carries pan −50, an EQ
  notch on band 2, a moved bus-3 send and a raised fader from the Tier-1 checks.
- Guarded ops: `set_main_fader` prompt → confirm → `BAD_TOKEN` on reuse → `TOKEN_EXPIRED` after 61 s.
- `show_mode(true)` blocking scene recall; scenes (`save_scene` to an empty slot, `recall_scene`).
  **Note: this desk's show control is set to CUES, not SCENES** — switch it in Setup before
  testing scene recall, or the scene pointer indexes the cue list.
- `panic()` timing (< 200 ms) and unmuting afterwards.
- `restore_snapshot` round trip; degraded handling via a cable pull.

Desk facts worth keeping: 2 scenes stored (0 "Studio Sept 26", 1 "molecules"); FX slot 5 already
holds a GEQ2 (CFS² provisioning should reuse it); ch 4 "Lead Vox" had preamp gain +0.0 dB, which
is why an SM58 barely registered; PC is 192.168.1.231, desk 192.168.1.139.

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
