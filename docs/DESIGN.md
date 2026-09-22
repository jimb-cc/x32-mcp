# x32-mcp — module contract (DESIGN.md)

This is the binding contract between modules. Implementers build **exactly** these
interfaces; anything not specified here is the implementer's call, but must be
documented in the module docstring. Protocol facts come from `docs/research/*.md`
(verified against Maillot's C sources) — cite them in comments where behaviour is
non-obvious. The build brief is `docs/BRIEF.md`.

## 0. Cross-cutting rules (non-negotiable)

1. **stdout is the MCP transport.** Nothing in `src/x32mcp/` may `print()` to stdout.
   Use `logging` (root logger configured to stderr in `server.main()`); tests may print.
2. **Pure Python only.** Deps are fixed: `mcp` (2.x), `pyyaml`, `websockets`. No numpy,
   no compiled extras, no new dependencies without a note in DESIGN.md. stdlib
   `asyncio` + `socket` for all networking. No Windows-specific paths/APIs.
3. **Engineering units at every public boundary.** dB (float), Hz, ms, %, 1-based
   channel/bus numbers, enum *tokens* (`"PEQ"`, `"RD"`). Raw OSC floats (0..1) exist
   only inside `scales.py`, `descriptor.py`, `connection.py`, `nodes.py`, `fakedesk.py`.
   Tool responses never contain raw floats.
4. **Mutes are inverted on the wire** (`/…/mix/on`: 1 = ON = unmuted). Public API uses
   `muted: bool` everywhere; the translation happens in `Desk`/`nodes` only.
5. **Never hang a tool call.** Every network wait has a timeout; the worst case for any
   tool is ~2 s (`request` = 500 ms × 3 attempts) and it returns `ok: false`.
6. **Desk is the source of truth.** Read cache TTL ≤ 2 s; invalidated by pushed
   `/xremote` updates and by our own writes.
7. **Policy is enforced in code** (`policy.py`), not by prompt. Every write path goes
   through `Policy`. `panic()` is Tier 1 and bypasses rate limiting and show-mode.
8. Files live under `X32MCP_HOME` (default: repo root, derived from `__file__`).
   See `config.py`.
9. Async everywhere in the runtime (`asyncio`); pure modules (`osc`, `scales`,
   `nodes` parse/render/diff, `detector`) are synchronous and side-effect free.
10. Type hints on all public functions; docstrings state units and ranges.
11. Tests: `pytest` (asyncio_mode=auto). Each module ships its own `tests/test_<mod>.py`.
    Run with `.venv/Scripts/python -m pytest tests/test_<mod>.py`. Never run `uv sync`.

## 1. Package layout & ownership

```
src/x32mcp/
  __init__.py       version string only
  config.py         paths + env (Settings)                       [written]
  events.py         EventBus (pub/sub, ring buffer)              [written]
  targets.py        Target parser (ch.5 / bus.3 / main.st …)     [written]
  osc.py            OSC codec
  scales.py         value scaling (fader taper, log, lin, enum)
  descriptor.py     device.yaml loader → Descriptor
  connection.py     X32Connection (single UDP socket, heartbeat, request/reply)
  nodes.py          /node line parse/render, state dump, snapshot files, diff
  policy.py         tiers, clamps, ramps params, confirm tokens, rate limiter, show mode
  meters.py         meter blob parsing, subscriptions, FrameSource implementations
  detector.py       CFS² FeedbackDetector (pure) + NotchController (pure) + GeqWriter protocol
  desk.py           Desk facade: high-level typed reads/writes (used by server + cfs)
  provision.py      GEQ insert setup/validation, mic discovery, preflight
  cfs.py            CFS² session manager: feedback_watch / ring_out / ring_out_system state machines, reports
  patches.py        patch plans (yaml/csv) apply/export
  webui.py          read-only dashboard (websockets: static page + WS push)
  fakedesk.py       Python X32 emulator (OSC endpoint) for tests/demos: `python -m x32mcp.fakedesk`
  server.py         MCPServer app + tools (thin) + `main()`
device.yaml         descriptor
webui/index.html    dashboard page (vanilla JS, canvas)
patches/example_band.yaml
tests/…             unit tests; tests/integration/… run against fakedesk
```

## 2. `config.py` (written)

```python
class Settings:            # frozen dataclass, built by Settings.from_env()
    home: Path             # X32MCP_HOME, default repo root
    device_yaml: Path      # X32MCP_DEVICE_YAML, default home/device.yaml
    snapshot_dir: Path     # home/snapshots
    report_dir: Path       # home/ringout_reports
    patch_dir: Path        # home/patches
    webui_dir: Path        # home/webui
    x32_host: str | None   # X32_HOST (autoconnect at startup if set)
    x32_port: int          # X32_PORT default 10023
    dash_host: str         # X32MCP_DASH_HOST default "0.0.0.0"
    dash_port: int         # X32MCP_DASH_PORT default 8032
    dash_enabled: bool     # X32MCP_DASH default "1"
    log_level: str         # X32MCP_LOG default "INFO"
```

## 3. `events.py` (written)

```python
@dataclass(frozen=True)
class Event:
    ts: float              # time.time()
    type: str              # dotted: "connection.state", "write", "cfs.state", "cfs.candidate",
                           # "cfs.notch", "cfs.stage", "cfs.abort", "cfs.report", "meters.frame"
    data: dict[str, Any]   # JSON-serialisable
class EventBus:
    def publish(self, type: str, **data) -> Event
    def subscribe(self, callback: Callable[[Event], None], *, types: set[str] | None = None) -> Callable[[], None]  # returns unsubscribe
    def recent(self, n: int = 200, types: set[str] | None = None) -> list[Event]
    async def stream(self, types=None) -> AsyncIterator[Event]   # asyncio.Queue based
```
`meters.frame` events carry `{"meter_type": int, "values": list[float]}` and are NOT kept in `recent()`.

## 4. `targets.py` (written)

```python
@dataclass(frozen=True)
class Target:
    family: str      # "ch" | "auxin" | "fxrtn" | "bus" | "mtx" | "main" | "dca"
    index: int|str   # 1-based int, or "st"/"m" for main
    @property
    def key(self) -> str          # "ch.5", "main.st"
    @property
    def osc_prefix(self) -> str   # "/ch/05", "/bus/03", "/main/st", "/dca/1"
    @property
    def label(self) -> str        # "Ch 5", "Bus 3", "Main LR", "Main M/C", "DCA 1", "Aux In 2", "FX Rtn 1", "Matrix 1"
def parse_target(s: str | int, *, default_family: str = "ch") -> Target   # raises TargetError
```
Accepted spellings (case-insensitive): `ch.5`, `ch5`, `channel 5`, `5`, `bus.3`, `bus3`, `aux 3`
(aux = bus, the X32 calls monitor mixes "Mix Bus"; `auxin.2` is the aux *input*), `main`, `main.st`, `lr`,
`main.m`, `mono`, `mtx.1`, `matrix 1`, `dca.1`, `auxin.2`, `fxrtn.1`.

## 5. `osc.py`

```python
@dataclass(frozen=True)
class OscMessage:
    address: str
    args: tuple[Any, ...]       # int | float | str | bytes (blob)
    typetags: str               # ",sif" etc. ("" if none on the wire)
class OscError(ValueError): ...
def encode(address: str, *args: int | float | str | bytes, typetags: str | None = None) -> bytes
def decode(data: bytes) -> OscMessage
```
- Standard OSC 1.0 4-byte alignment; big-endian int32/float32; strings NUL-terminated + padded; blob =
  big-endian int32 length + bytes + padding.
- `encode` infers typetags: bool→`i`, int→`i`, float→`f`, str→`s`, bytes→`b`. `typetags` overrides (e.g. to
  force `f` for an int value).
- `decode` is tolerant: missing typetag string → `args=()`, `typetags=""`; unknown tags raise `OscError`;
  trailing garbage ignored; never raises on blob content. Bundles (`#bundle`) → `OscError("bundle")`.
- Blob payloads are returned raw (`bytes`); meter decoding lives in `meters.py`.
- Tests: golden byte vectors for `/info`, `/ch/01/mix/fader ,f 0.75`, `/node ,s ch/01/config`, a `,b` blob,
  a message with no typetags, round-trips, and a fuzz-ish "decode never raises anything but OscError".

## 6. `scales.py`

```python
NEG_INF_DB = float("-inf")
def fader_to_db(x: float) -> float        # X32 taper; x<=0 → -inf; result rounded to 0.1? NO — exact; callers round for display
def db_to_fader(db: float) -> float       # clamps to [0.0, 1.0]; -inf/<-90 → 0.0
def quantize(x: float, steps: int) -> float   # round(x*(steps-1))/(steps-1); desk-side snapping
def lin_to_value(x, lo, hi) / value_to_lin(v, lo, hi)
def log_to_value(x, lo, hi) / value_to_log(v, lo, hi)      # v = lo*(hi/lo)**x ; works for hi<lo (Q)
def enum_to_index(token, values) / index_to_enum(i, values)
@dataclass(frozen=True)
class Scale:                       # built by descriptor from device.yaml `scales`/inline spec
    kind: Literal["level","lin","log","enum","int","bool","str","pan"]
    lo: float = 0.0; hi: float = 1.0; steps: int | None = None; unit: str = ""; values: tuple[str,...] = ()
    def to_value(self, raw) -> Any          # OSC arg → engineering value (level→dB, enum int→token, bool→bool)
    def to_raw(self, value) -> Any          # engineering → OSC arg (quantised to steps when given)
    def clamp(self, value) -> Any
```
Fader taper per Maillot (verify in `docs/research/scales_params.md`, correct if research differs):
```
float→dB: f>=0.5: 40f-30 | f>=0.25: 80f-50 | f>=0.0625: 160f-70 | f>0: 480f-90 | f==0: -inf
dB→float: d<-60: (d+90)/480 | d<-30: (d+70)/160 | d<-10: (d+50)/80 | d<=10: (d+30)/40
```
Round-trip tests at −90 (→0.0 exactly, back to -inf or -90 — define: `fader_to_db(0.0)` returns `-inf`;
`fader_to_db(db_to_fader(-90.0))` is `-90.0`), −60, −30, −10, 0, +10 exactly (float tolerance 1e-9),
and monotonicity over 0..1 in 1/1023 steps. `pan`: 0..1 ↔ −100..+100. Also `format_db(db)` → `"-oo"` / `"-12.3"` / `"+2.0"`.

## 7. `device.yaml` + `descriptor.py`

`device.yaml` is the only place device knowledge lives. Schema (top level keys are fixed):

```yaml
meta: {model: X32, osc_port: 10023, protocol_ref: "Maillot Unofficial X32 OSC v4.x", firmware_tested: "4.x"}
scales:            # named scales: {kind, lo, hi, steps, unit} — see scales.Scale
  fader: {kind: level, steps: 1024, unit: dB}      # faders/main/dca: grid i/1023 (scales_params.md §2.3)
  send:  {kind: level, steps: 161, unit: dB}       # sends & mlevel: grid i/160
  freq:  {kind: log, lo: 20, hi: 20000, steps: 201, unit: Hz}
  ...
enums:             # name → ordered token list (index = OSC int)
  color: [OFF, RD, GN, YE, BL, MG, CY, WH, OFFi, RDi, GNi, YEi, BLi, MGi, CYi, WHi]
  eq_type: [LCut, LShv, PEQ, VEQ, HShv, HCut]
  ch_source: [...]   # 0..64 per research
  insert_sel: [...]
  fx_type: [...]
  ...
strips:            # families → count/path/features
  ch:    {count: 32, path: "/ch/{n:02d}", label: "Ch", sends: 16, eq_bands: 4, preamp: true, gate: true, dyn: true, insert: true}
  auxin: {count: 8,  path: "/auxin/{n:02d}", label: "Aux In", sends: 16, eq_bands: 4}
  fxrtn: {count: 8,  path: "/fxrtn/{n:02d}", label: "FX Rtn", sends: 16, eq_bands: 4}
  bus:   {count: 16, path: "/bus/{n:02d}", label: "Bus", sends: 6, send_target: mtx, eq_bands: 6, dyn: true, insert: true}
  mtx:   {count: 6,  path: "/mtx/{n:02d}", label: "Matrix", eq_bands: 6, dyn: true, insert: true}
  main:  {ids: [st, m], path: "/main/{id}", label: {st: "Main LR", m: "Main M/C"}, sends: 6, send_target: mtx, eq_bands: 6, dyn: true, insert: true}
  dca:   {count: 8,  path: "/dca/{n}", label: "DCA"}
params:            # family → relative path (may contain {send:02d}, {band}) → spec
  ch:
    config/name:   {osc: s, node: str}
    config/icon:   {osc: i, node: int}
    config/color:  {osc: i, enum: color, node: enum}
    config/source: {osc: i, enum: ch_source, node: int, tier: 2}
    mix/on:        {osc: i, scale: bool, node: onoff, tier: 1, inverted_mute: true}
    mix/fader:     {osc: f, scale: fader, node: db1, tier: 1, clamp: {max: 5}}
    mix/pan:       {osc: f, scale: pan, node: sint, tier: 1}
    "mix/{send:02d}/level": {osc: f, scale: fader, node: db1, tier: 1, clamp: {max: 0}}
    "eq/{band}/type": {osc: i, enum: eq_type, node: enum, tier: 1}
    "eq/{band}/f":    {osc: f, scale: freq, node: freq, tier: 1}
    ...
  bus: ...   main: ...   dca: ...   headamp: ...   fx: ...   config: ...   show: ...   stat: ...   prefs: ...
nodes:             # /node sections for dump/snapshot, in sweep order. `fields` = param rel-paths in the
                   # order the desk prints them. `for` expands template vars.
  - {family: ch, section: "config", fields: [config/name, config/icon, config/color, config/source]}
  - {family: ch, section: "mix", fields: [mix/on, mix/fader, mix/st, mix/pan, mix/mono, mix/mlevel]}
  - {family: ch, section: "mix/{send:02d}", for: {send: [1, 16]}, fields: [...], fields_even: [...]}   # odd sends have pan+type; even sends: on, level only (per research)
  - {family: ch, section: "eq/{band}", for: {band: [1, 4]}, fields: [eq/{band}/type, eq/{band}/f, eq/{band}/g, eq/{band}/q]}
  ...
  - {path: "/-show/prepos", fields: [...]}      # non-strip sections use absolute `path`
guarded:           # Tier 2 address globs (fnmatch on concrete address), in addition to per-param `tier: 2`
  - "/main/st/mix/*"
  - "/main/m/mix/*"
  - "/headamp/*/phantom"
  - "/config/routing/*"
  - "/config/mute/*"
  - "/*/insert/*"
  - "/fx/*/type"
  - "/fx/*/source/*"
  - "/*/config/source"
  - "/*/grp/*"
  - "/-action/*"
  - "/save"
  - "/load"
policy:
  ch_fader_max_db: 5
  bus_fader_max_db: 0        # bus masters (also the ring-out ceiling)
  main_fader_max_db: 0
  send_max_db: 0
  eq_gain_abs_max_db: 15
  relative_max_db: 6
  relative_max_db_show_mode: 3
  ramp_default_ms: 300
  ramp_step_ms: 20
  writes_per_second: 50
  confirm_token_ttl_s: 300   # 5 min: a confirmation is a conversational round trip (M5)
  read_cache_ttl_s: 2.0
  show_mode_default: false
rta:
  meter_type: 15
  bands: 100
  band_hz: [...]             # 100 centres: 10000 * 2 ** ((i - 90) / 10)  (meters.md §4.2, VERIFIED)
  source_param: "/-prefs/rta/source"     # enum rta_source: 0 none, 1 Monitor, 2-33 Ch, 34-41 Aux, 42-49 FX, 50-65 Bus, 66-71 Mtx, 72 Main, 73 Mono
  pos_param: "/-prefs/rta/pos"           # 0 PRE, 1 POST
  options_param: "/-prefs/rta/options"   # bit 5 = Solo Priority (clear it)
  stat_param: "/-stat/rtasource"         # read-back: 0-72 pre-EQ, +98 post-EQ
  frame_period_s: 0.05
geq:
  fx_types_dual: [GEQ2, TEQ2]   # dual-mono: par 1-31 = side A bands, 32 = master A, 33-63 = side B bands, 64 = master B (fx_routing_scenes.md §2)
  fx_types_stereo: [GEQ, TEQ]   # par 1-31 bands, 32 master
  band_hz: [20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 20000]
  par_a_first: 1
  par_a_master: 32
  par_b_first: 33
  par_b_master: 64
  gain_scale: geq_gain          # linf -15..+15 dB (0.5 dB grid → 61 steps)
  insert_slots_preferred: [5, 6, 7, 8]   # fx 5-8 are insert-only; NOTE fx 5-8 use a DIFFERENT type enum (fx_type_58: GEQ2 = 0) from fx 1-4 (fx_type_14: GEQ2 = 27)
  insert_sel_enum: insert_sel   # 0 OFF, 1 FX1L, 2 FX1R, … 16 FX8R, 17-22 AUX1-6 (23 entries)
detector:                    # one key per DetectorConfig field; every value == the dataclass default (tested). Excerpt -- the full
  prominence_db: 12          # block with every threshold's physical origin is in device.yaml itself and docs/DETECTOR.md §2-§4
  neighbour_bins: 3
  track_prominence_db: 6
  narrow_db: 8
  stable_frames: 5           # K1 = 250 ms
  confirm_frames: 12         # K2
  rise_db: 6                 # RISE evidence (own rise after the band settled, net of common mode)
  fast_rise_db: 15           # FAST-RISE (>= 3 watched increments into the plateau)
  loud_line_db: -10          # LOUD: >= max(this, min(arm p95 + loud_above_arm_db, loud_ceiling_db)) and loud_margin_db above all else
  loud_above_arm_db: 20
  loud_ceiling_db: -6
  loudish_level_db: -20      # tier B's level line (FAST-RISE bed step, deepen-on-held)
  arm_line_min_level_db: -40 # AT-ARM
  window_low_hz_watch: 160   # P6 (63 ring_out / 40 with lf_feedback_possible / lf_edge_hz from the channels' HPF)
  cut_verify_s: 1.5          # note_cut() verdicts: confirmed / insufficient / held / false_cut / ambiguous
  confidence_threshold: 0.7  # display contract only
  band_tolerance: 1
  cooldown_s: 1.0
  notch_step_db: -3
  notch_max_db: -9
  notch_budget_default: 6
  merge_adjacent_bands: 1
  decay_verify_db: 6         # cfs ring_out VERIFY: ring must drop ≥ this within decay_verify_s after a notch
  decay_verify_s: 1.5
  frame_period_s: 0.05
  min_level_db: -45          # legacy keys (accepted, not used by the decision): min_level_db, persistence_frames, growth_*,
  weights: {prominence: 0.3, persistence: 0.2, growth: 0.5}   #   monotonic_tolerance_db, weights, override_*
ringout:
  step_db: 1.0
  dwell_ms: 1500
  safety_margin_db: 3
  abort_backoff_db: 6
  master_ceiling_db: 0
  start_warn_db: -10         # warn if bus master starts above this
mics:
  send_floor_db: -40
  mute_group_convention: 6
```

```python
class DescriptorError(ValueError)
@dataclass(frozen=True)
class ParamSpec:
    key: str                # "ch:mix/fader"  (family:relpath template)
    family: str; relpath: str
    osc_type: str           # "f" | "i" | "s"
    scale: Scale
    node_fmt: str           # str|int|sint|onoff|enum|db1|db2|float1|float2|freq|pct|... (vocabulary fixed by nodes.py)
    tier: int               # 0/1/2 (default 1 for writable, 0 for read-only)
    clamp_min: float|None; clamp_max: float|None
    inverted_mute: bool
    enum: tuple[str,...] | None
    def address(self, target: Target | None = None, **vars) -> str   # concrete OSC address
    def to_value(self, raw) / to_raw(self, value)
@dataclass(frozen=True)
class NodeSection:
    family: str | None; section: str; path_template: str; fields: tuple[str,...]; fields_even: tuple[str,...] | None; for_vars: dict
    def expand(self, strips) -> list[tuple[str, dict]]       # concrete node paths with their field lists
class Descriptor:
    @classmethod
    def load(cls, path: Path | None = None) -> "Descriptor"      # validates; raises DescriptorError with path of the bad key
    meta, scales: dict[str, Scale], enums: dict[str, tuple[str,...]], strips: dict[str, StripFamily]
    policy: dict, rta: dict, geq: dict, detector: dict, ringout: dict, mics: dict
    def param(self, family: str, relpath: str) -> ParamSpec          # template lookup, e.g. ("ch", "mix/{send:02d}/level")
    def param_for_address(self, address: str) -> tuple[ParamSpec, dict] | None   # reverse lookup → (spec, vars incl. n/id/send/band)
    def node_sections(self) -> list[NodeSection]
    def all_node_paths(self) -> list[tuple[str, tuple[str,...]]]     # every concrete node path for a full sweep, with fields
    def tier_for(self, address: str) -> int                          # max(param tier, guarded glob match)
    def strip_targets(self, family) -> list[Target]
```
Tests: yaml loads; every `nodes` field references a declared param; every enum referenced exists;
`param_for_address("/ch/05/mix/03/level")` → spec + `{"n":5,"send":3}`; tier_for on guarded globs;
the count of concrete node paths (report it; must be < 1500).

## 8. `connection.py`

```python
class ConnectionState(str, Enum): DISCONNECTED, CONNECTING, CONNECTED, DEGRADED
@dataclass
class ConsoleInfo: host: str; port: int; name: str; model: str; firmware: str; server_version: str
@dataclass
class ConnectionStatus: state, console: ConsoleInfo|None, last_rx_age_s: float|None, heartbeat_ok: bool,
                        pending_requests: int, rtt_ms: float|None, reconnect_attempts: int, error: str|None
class ConnectionError(Exception); class NotConnected(ConnectionError); class RequestTimeout(ConnectionError)

class X32Connection:
    def __init__(self, descriptor: Descriptor, events: EventBus, *, timeout_s=0.5, retries=2, heartbeat_s=8.0, watchdog_s=12.0)
    async def connect(self, host: str, port: int = 10023) -> ConsoleInfo   # binds ONE UDP socket (asyncio DatagramProtocol), /info round-trip, starts heartbeat+watchdog tasks
    async def close(self) -> None
    @property
    def status(self) -> ConnectionStatus
    @property
    def connected(self) -> bool          # CONNECTED or DEGRADED-but-socket-open? → CONNECTED only
    async def request(self, address: str, *args, reply_address: str | None = None, timeout: float | None = None, retries: int | None = None) -> OscMessage
        # send; await first reply whose address == (reply_address or address). Retries re-send. Raises RequestTimeout / NotConnected.
    async def get(self, address: str) -> Any                 # request(address) → args[0] (or tuple if >1)
    async def set(self, address: str, *args) -> None         # fire-and-forget write (+ read-cache invalidation + publishes "write" event {address, args})
    async def node(self, path: str) -> str                   # request("/node", path) with reply matched on address "node" AND payload startswith "/"+path+" " (or == "/"+path). Returns the line WITHOUT trailing newline.
    async def slash(self, line: str, *, timeout: float | None = None) -> None
        # Node-style WRITE (docs/research/transport.md §6.6, CONFIRMED): send address "/" with one string arg
        # "<path> <v1> <v2> …" in node-reply text form (engineering units, enum tokens, quoted strings; leading "/" optional;
        # partial trailing lists allowed). The desk ECHOES the "/" message back — await that echo (reply address "/", FIFO)
        # as the ack/flow control; raise RequestTimeout otherwise. Used by snapshot restore. Policy is enforced by the caller.
    async def node_many(self, paths: list[str], concurrency: int = 16) -> dict[str, str | None]   # pipelined sweep; None on timeout (never raises)
    async def get_cached(self, address: str) -> Any          # read-through cache with descriptor.policy.read_cache_ttl_s
    def invalidate(self, address: str | None = None) -> None
    def on_update(self, callback: Callable[[str, tuple], None]) -> Callable[[], None]    # pushed /xremote updates (address, args)
    def on_blob(self, callback: Callable[[str, bytes], None]) -> Callable[[], None]      # any ",b" message, e.g. "/meters/15"
    async def send_raw(self, address: str, *args, typetags: str | None = None) -> None   # no cache/policy; used by meters (/meters, /renew) and /xremote
    async def discover(self, timeout_s: float = 2.0, port: int = 10023) -> list[ConsoleInfo]   # broadcast /xinfo to 255.255.255.255 (and 127.0.0.1 for the fake desk) using a temporary socket with SO_BROADCAST
```
Semantics:
- **Single socket**: one `DatagramProtocol` bound to `("0.0.0.0", 0)`; the X32 replies to the source port.
- Request matching: `dict[str, deque[asyncio.Future]]` keyed by reply address; `/node` uses key
  `"node:/ch/01/config"` derived from the reply payload prefix (the first whitespace-delimited token of the
  string). Unmatched inbound messages with args → `on_update` callbacks + cache update + `events.publish("update", ...)`
  (only if not a blob). Blobs → `on_blob` only (never parsed as text, never logged at INFO).
- Heartbeat task sends `/xremote` every `heartbeat_s`. Watchdog: if no inbound packet for `watchdog_s`,
  send `/info`; if still nothing after `timeout_s*(retries+1)`, state → DEGRADED, `events.publish("connection.state", ...)`,
  reconnect loop with backoff 1,2,4,8 s (max 8) — re-`/info` until it answers, then CONNECTED.
- `set()` while DEGRADED raises `NotConnected("desk not responding …")`. `request()` while DEGRADED still
  tries (it is how we recover) but with a single attempt.
- Every inbound datagram passes through `osc.decode` inside try/except; decode failures are counted and
  logged at DEBUG, never crash the loop.
- Tests use an in-process `asyncio` UDP responder implemented in the test (NOT fakedesk) that answers
  `/info`, echoes `/ch/01/mix/fader`, answers `/node`, drops the first N requests (retry path), goes silent
  (degraded path), and sends a blob. Assert: single local port used for send & receive; timeout raises
  within budget; degraded → reconnect.

## 9. `nodes.py`

```python
NODE_FORMATS = {"str","int","sint","onoff","enum","db1","db2","float1","float2","freq","pct","float","hex"}  # + any research-driven additions, documented
class NodeParseError(ValueError)
def parse_node_line(line: str, fields: Sequence[ParamSpec]) -> dict[str, Any]     # {relpath: engineering value}; tolerant of multiple spaces; quoted strings; "-oo"
def render_node_line(path: str, fields: Sequence[ParamSpec], values: dict[str, Any]) -> str   # exact desk style (used by fakedesk; round-trip with parse)
def tokenize(line: str) -> list[str]
async def dump_desk_state(conn: X32Connection, d: Descriptor, *, sections: Iterable[str] | None = None) -> DeskState
@dataclass
class DeskState:            # JSON-serialisable
    created: str            # ISO 8601 (local, with offset)
    console: dict           # ConsoleInfo as dict
    scene: dict | None      # {"index": int, "name": str}
    sections: dict[str, dict[str, Any]]    # "/ch/01/mix" → {"mix/on": True, "mix/fader": -12.0, ...} (engineering values; mute NOT inverted here — store raw semantics? NO: store engineering: "mix/on": True means channel ON)
    missing: list[str]      # node paths that timed out
    def to_json(self) -> str; @classmethod from_json(cls, s)
    def get(self, address: str) -> Any      # "/ch/01/mix/fader" → -12.0 (joins section + field)
@dataclass
class Snapshot: id: str; label: str; path: Path; created: str; state: DeskState
class SnapshotStore:
    def __init__(self, dir: Path)
    def save(self, state: DeskState, label: str = "") -> Snapshot      # id = "YYYYMMDD-HHMMSS[-slug]"
    def list(self) -> list[SnapshotMeta]      # newest first; (id, label, created, scene, size)
    def load(self, id_or_prefix: str) -> Snapshot     # accepts unique prefix or "latest"
    def latest(self) -> Snapshot | None
@dataclass(frozen=True)
class Change: address: str; field: str; before: Any; after: Any; label: str   # label = English, e.g. "Ch 5 'Vox' fader −4.2 dB → −1.0 dB"
def diff_states(a: DeskState, b: DeskState, d: Descriptor, *, scope: str | None = None) -> list[Change]   # scope = target key or family prefix
def describe_changes(changes: list[Change]) -> str    # bullet list; groups by strip; sends read "Bus 3 send from Ch 2 −20 dB → −17 dB (+3 dB)"
def restore_plan(target_state: DeskState, live: DeskState, d: Descriptor, *, scope=None) -> list[str]
    # Node-style write lines (the "/" form, transport.md §6.6) for every SECTION that differs, e.g.
    # '/ch/01/mix ON -12.0 ON +0 OFF -oo' — rendered with render_node_line from the snapshot's values, so a restore
    # writes back exactly the text the desk itself produced (bit-faithful). Ordered: config, preamp, mix, sends, eq,
    # dyn, gate, insert, grp, then bus/main/dca/headamp/fx/config. Excludes -show/-stat/-prefs/-action sections.
```
Parsing tolerance (transport.md §6.3/§9, VERIFIED on a console-saved scene file): token counts are firmware-dependent
(`/ch/01/mix/01` has 4 tokens on FW 2.x, 5 with `panFollow` on FW ≥ 3) → parse positionally, missing trailing fields
→ `None`, extra tokens ignored. Numbers have adaptive precision (`0.03`, ` 538`, `8.00`, `-0.0`, `10`) → always parse
numerics as free-form floats/ints; levels are right-aligned in a 5-char field, HPF is an integer Hz in width 3,
kHz tokens look like `1k02`/`11k9`/`20k0`. `render_node_line` reproduces the console's padded style (so the fake desk
looks real) and `parse_node_line` must accept both padded and single-spaced text.
Engineering-value conventions inside `DeskState.sections`: floats in dB/Hz/ms as floats (`-inf` serialised as
`"-oo"` string → `to_json` must handle), enums as tokens, on/off as bool, names as str. The
`inverted_mute` flag is NOT applied here (`mix/on: True` = channel on) — `Desk` applies it.
Freq node format: X32 prints `124.7`, `1k00`, `4k00`, `10k0`, `20k0` — parse and render both (per research).

Tests: parse/render round trip for every node section in `device.yaml` using synthetic values; the verbatim
example lines from `docs/research/transport.md`; diff on a scripted change set produces the exact English
strings; snapshot save/list/load/latest; `-oo` handling; a section with a name containing spaces and quotes.

## 10. `policy.py`

```python
class Tier(IntEnum): READ = 0; MIX = 1; GUARDED = 2
class PolicyError(Exception): code: str   # "CLAMPED_TO_LIMIT" (soft, not raised), "RELATIVE_TOO_LARGE", "SHOW_MODE_BLOCKS", "NEEDS_CONFIRMATION", "BAD_TOKEN", "TOKEN_EXPIRED", "RATE_LIMITED", "NOT_ALLOWED", "BOOST_FORBIDDEN"
@dataclass
class Clamped: value: float; requested: float; limit: float; reason: str
@dataclass
class PendingConfirmation: requires_confirmation: bool = True; action_summary: str; confirm_token: str; expires_in_s: float
class Policy:
    def __init__(self, d: Descriptor, events: EventBus, *, clock: Callable[[], float] = time.monotonic)
    show_mode: bool
    def tier_for(self, address: str) -> Tier
    def clamp_level(self, target: Target, db: float, *, kind: Literal["fader","send"] = "fader") -> tuple[float, Clamped | None]   # applies ch/bus/main/send ceilings; never raises
    def check_relative(self, delta_db: float, *, force: bool = False) -> None      # raises RELATIVE_TOO_LARGE (limit 6, or 3 in show mode; force bypasses the 6 but NOT show-mode 3? → force bypasses both; show mode is about accidents, not intent) — DECISION: force bypasses relative limit even in show mode; scene recall/save are refused in show mode regardless of force.
    def clamp_eq_gain(self, gain_db: float) -> tuple[float, Clamped | None]
    def require_confirmation(self, action_summary: str, payload: dict) -> PendingConfirmation   # token = secrets.token_urlsafe(8); single use; TTL from descriptor
    def consume_token(self, token: str, *, expected_action: str | None = None) -> dict          # returns payload; raises BAD_TOKEN/TOKEN_EXPIRED; token deleted on success or failure
    def guard(self, action: str, summary: str, payload: dict, confirm_token: str | None) -> dict | PendingConfirmation   # helper: returns PendingConfirmation if token is None, else the validated payload
    def check_show_mode_allows(self, action: str) -> None      # raises SHOW_MODE_BLOCKS for "scene_recall", "scene_save", "restore_snapshot"? (restore allowed — it is the undo), "setup_ringout_eqs", "ring_out", "ring_out_system"
    async def acquire_write(self, *, panic: bool = False) -> None   # token bucket writes_per_second; panic bypasses; raises RATE_LIMITED if it would wait > 1 s (never blocks longer)
    def validate_notch(self, current_db: float, new_db: float) -> None   # cuts only (new_db < 0 and new_db <= current_db), depth ≥ notch_max_db, else raises BOOST_FORBIDDEN/NOT_ALLOWED
    def ramp_steps(self, from_db: float, to_db: float, ramp_ms: int | None) -> list[float]   # dB values incl. final; step every ramp_step_ms; ramp_ms None → default; 0 → [to_db]; -inf handled (start ramp from -90)
    snapshot_before_write: bool   # True until the first Tier≥1 write's pre-snapshot is taken; `mark_snapshot_taken()`
```
Tests: clamp maths for each family; relative limit incl. force & show mode; token lifecycle (single use,
TTL via injected clock, wrong token); rate limiter (51 writes in 1 s → last one raises; panic bypasses);
`ramp_steps` lengths/endpoints; `validate_notch` rejects boosts and > −9 dB depth; tier_for on guarded globs.

## 11. `meters.py`

```python
@dataclass(frozen=True)
class MeterFrame: meter_type: int; ts: float; values: tuple[float, ...]   # dB for RTA (type 15); linear 0..1 for others; `db()` helper converts linear→dB (20*log10, floor −90)
class MeterParseError(ValueError)
def parse_meter_blob(address: str, blob: bytes, *, ts: float) -> MeterFrame     # per research: LE int32 count + LE values; type 15 = packed int16 pairs (dB*256) if research confirms
def rta_band_hz(d: Descriptor) -> tuple[float, ...]     # 100 centres
class FrameSource(Protocol):
    async def start(self) -> None; async def stop(self) -> None
    def subscribe(self, cb: Callable[[MeterFrame], None]) -> Callable[[], None]
class LiveMeters(FrameSource):
    def __init__(self, conn: X32Connection, meter_type: int, *, extra_args: tuple = (), renew_s: float = 5.0, events: EventBus | None = None)
    # sends "/meters ,s(i…) /meters/N …" then re-sends every renew_s (per research: subscription lives ~10 s); parses blobs from conn.on_blob; publishes events "meters.frame" only if events given
class FixtureSource(FrameSource):
    def __init__(self, frames: Iterable[MeterFrame] | Callable[[int], MeterFrame | None], *, period_s=0.05, realtime=True)   # replays; realtime=False → as fast as possible (tests)
class SyntheticRta(FrameSource):
    # generates 100-band "music-like" pink-ish spectrum with noise; `inject_ring(freq_hz, growth_db_per_s, start_db=…)`, `inject_note(freq_hz, level_db)` (plateaus), `attenuate(band_idx, db)` (what a GEQ cut does); `stop_ring(freq_hz)`. Deterministic with `seed`. Used by fakedesk, dashboard demo, tests.
async def average_frames(src: FrameSource, n: int, *, timeout_s: float) -> MeterFrame | None
async def set_rta_source(conn, d, target: Target, *, post_eq: bool = True) -> None   # writes the /-stat|-prefs/rta params per research
```
Confirmed facts (docs/research/meters.md — cite it): request `/meters ,si "/meters/N" tf` (tf 1 → 50 ms frames);
lease is 10 s with no keep-alive → **re-send the identical `/meters` request every 5 s** (`/renew` also works on a
real desk but Maillot's emulator ignores it, so re-sending is the portable choice). Reply: address `/meters/N`,
typetag `,b`; blob = big-endian OSC size, then **little-endian int32 word count**, then LE payload words.
Types 0–14: float32 LE linear amplitude (silence ≈ 1e-5, values may exceed 1.0 up to 8.0). **Type 15 (RTA): 50 words =
100 × int16 LE, dB = int16/256 (−128.0 floor = "no signal", 0.0 = clip)**; band i centre = `10000·2^((i−90)/10)` Hz
(band 0 ≈ 19.5 Hz, band 90 = 10 kHz, band 99 ≈ 18.66 kHz). Type 16: 88 × int16/32767 linear + 8 × automix `2^(s/256)`.
`/meters/15` has no source argument — it streams whatever the console's RTA analyses: `set_rta_source` writes
`/-prefs/rta/source ,i` (0 none, 1 Monitor, 2–33 Ch01–32, 34–41 Aux, 42–49 FX rtn, **50–65 Bus 1–16**, 66–71 Mtx,
**72 Main LR, 73 Mono**) and `/-prefs/rta/pos ,i 1` (POST), clears bit 5 (Solo Priority) of `/-prefs/rta/options`,
then verifies by reading `/-stat/rtasource` (expect `146+N−1` for Bus N post-EQ, 168 for Main LR post).

Tests: blob parsing from a hand-built byte vector (and the verbatim layout in `docs/research/meters.md`),
including the type-15 packing; `FixtureSource` replay; `SyntheticRta` produces a ring that grows linearly
in dB and a note that plateaus.

## 12. `detector.py` (pure; no asyncio, no I/O)

Full design, thresholds and evidence: **docs/DETECTOR.md**. Contract:

```python
@dataclass(frozen=True)
class DetectorConfig: ...        # one field per device.yaml `detector:` key; every yaml value == the dataclass default (tested)
    @classmethod from_descriptor(d) / from_dict(mapping)   # unknown keys ignored; `weights` mapping flattened (legacy)
    mode: "watch" | "ringout"; lf_feedback_possible: bool; lf_edge_hz: float | None; window_low_hz (property)
@dataclass(frozen=True)
class Detection: ts; band (0-based RTA peak band); freq_hz (interpolated centroid frequency -> feed NotchController);
    level_db; prominence_db; slope_db_per_s; frames; confidence (>= confidence_threshold, display only);
    reasons: tuple[str, ...]   # predicates + evidence that fired, for the notch report ('rise7dB', 'fastrise17dB@120dB/s',
                               # 'loud', 'established_at_arm', 'probe2', 'pre_emptive', 'glided_in', ...)
    klass: "STRONG" | "PROBE" ("MODERATE" only with ringout_emit_moderate, reason 'confirmed_K2'); a deepen request adds
                               # 'deepen_held' / 'deepen_insufficient'; centroid_band; narrow_db; rise_db; excess_db
@dataclass
class Candidate: band, freq_hz, first_ts, last_ts, frames, level_db, cluster_db, prominence_db, narrow_db, excess_db, rise_db,
    fast_rise_db, klass ("TRACK" | "MUSICAL" | "STATIONARY" | "MODERATE" | "STRONG" | "FALSE_CUT"), reasons, confidence,
    emitted, born_at_arm (cleared when another source lands on the track), onset_fi, glided, probe_hits / probe_linear /
    probe_pinned / steps_seen, stationary, cut_verdict (None | "pending" | "confirmed" | "insufficient" | "held" | "false_cut" |
    "ambiguous"), cut_deepen (a held/insufficient verdict entitles one re-emission), cuts_held, emit_evidence, age_s (property);
    to_dict() carries all of these (keys `klass`, `level_db`, `prominence_db`, `excess_db`, `age_s`, `reasons`, `freq_hz`,
    `cut_verdict`, `cut_deepen`, `cuts_held`, `steps_seen`, `fast_rise_db`, `born_at_arm`, `stationary` are the tier-B hook;
    reasons may carry 'backoff_advised' on a >= 30 dB-prominent STATIONARY line)
class FeedbackDetector:
    def __init__(self, cfg, band_hz, *, mode=None, lf_feedback_possible=None, lf_edge_hz=None)   # overrides replace cfg fields
    def feed(self, values_db: Sequence[float], ts: float) -> list[Detection]   # one call per frame (values clamped to [-128, 0]);
                                                                              # STRONG lines (and ring_out pre-emptive PROBE lines), once per
                                                                              # cooldown, re-emitted only on regrowth / LOUD-and-not-dropped /
                                                                              # a new probe hit / a held|insufficient verdict with deepen right
    def note_gain_step(self, delta_db: float, ts: float) -> None   # ring_out: cfs calls it right after each master write (the probe)
    def note_cut(self, freq_hz: float | None = None, depth_db: float = 0.0, ts: float | None = None, *,
                 band: int | None = None, step_db: float | None = None) -> None
        # after each GEQ write (cfs._notch does it): depth_db = the band's new TOTAL gain (<= 0) at the GEQ centre freq_hz (or RTA
        # band); starts the post-cut watch of every track within 1/3 octave; outcome in Candidate.cut_verdict and det.cut_log
        # ({ts, freq_hz, band, step_db, depth_db, bell_db [lo, hi], drop_db, verdict, deepen, emitted}): confirmed | insufficient |
        # held (dropped by the bell and sits there: note through the EQ OR limiter-held howl -- deepened once per verdict iff the
        # line was emitted on FAST-RISE/LOUD/PROBE/AT-ARM and loud-ish) | false_cut (the line ended by itself: never re-emitted) |
        # ambiguous
    def programme_present(self, ts=None) -> bool                    # the ring_out contract check ('stage is quiet'); informational
    def refresh_arm_reference(self) -> None                         # re-open the 2 s arm-time level reference (LOUD / loud-ish)
    flags: set[str]      # PEAK_HOLD_SUSPECTED | FROZEN_LINES | HOT_SPECTRUM | SLOW_RELEASE | PROGRAMME_PRESENT (refreshed per frame)
    arm_p95_db, loud_threshold_db, loudish_threshold_db, release_db_per_s   # measured references (cfs reports them under "detector")
    candidates -> list[Candidate]; mode; reset()
    @staticmethod bell_attenuation_db(depth_db, offset_oct, q) -> float   # RBJ peaking-cut attenuation at an offset (loop brief 4.1)
```
Decision (see DETECTOR.md): BASE = P1 narrow line ∧ P2 no harmonic family ∧ P3 stable centroid over K1 = 5 frames ∧ P4 sustained
∧ (P5 new energy over the per-band baseline ∨ present at arm) ∧ P6 inside the session window ∧ not a frozen display value;
STRONG = BASE ∧ one of RISE (own rise ≥ 6 dB after the band settled, net of common mode) | FAST-RISE (≥ 3 watched increments
into the plateau, ≥ 15 dB; the step out of the bed counts only for a loud-ish line) | LOUD (clip flag unless HOT, or ≥ max(−10 dBFS,
min(arm p95 + 20, −6)) — under HOT also ≥ arm max + 3 — and 6 dB above all else) | AT-ARM | PROBE. MODERATE (BASE only) is
published, never cut by the detector. No absolute candidate gate, no growth-rate window, no weighted sum.
```python
@dataclass
class Notch: bus: int; band: int; freq_hz: float; depth_db: float; session_id: str; ts: float; detections: int
class GeqWriter(Protocol):
    async def set_band_gain(self, bus: int, band: int, gain_db: float) -> None
class RecordingGeqWriter(GeqWriter): writes: list[tuple[int,int,float]]     # for simulation/tests
class NotchController:
    def __init__(self, cfg: DetectorConfig, geq_band_hz: Sequence[float], policy_validate: Callable[[float, float], None], *, budget: int, existing: dict[int, float] | None = None)
    def band_for_freq(self, hz: float) -> int
    def propose(self, det: Detection, bus: int, session_id: str) -> NotchPlan | None   # PURE: merges into an adjacent existing notch (≤ merge_adjacent_bands), deepens by notch_step_db to notch_max_db, respects budget (None when spent / band at max); runs policy_validate
    def commit(self, plan: NotchPlan) -> Notch                                       # record a plan whose GEQ write has reached the desk (two-phase: propose → write → commit; a failed/cancelled write is never committed)
    def observe(self, band: int, gain_db: float) -> None                             # the desk pushed a hand-made change: adopt it (never write a band shallower than the desk has it)
    def plan(self, det, bus, session_id) -> Notch | None                             # propose+commit, offline/tests only
    @property
    def notches(self) -> list[Notch]; budget_left: int; spent: bool
```
Tests: `tests/test_detector.py` (synthetic streams: clean music 0 detections; held note 0; 15 dB/s ring within 7 frames of its
12 dB crossing in a stream where a melody note masks its early climb, 20 dB/s ring within 6; two rings; noise; established
plateaued howl caught, bit-identical (peak-held) copy flagged not cut; device.yaml == defaults; notch controller),
`tests/test_detector_predicates.py` (one test per predicate / evidence rule / flag / hook), `tests/test_detector_regressions.py`
(rendered corpus scenarios: X11, AP02, AF05, AP07, AS08, AF14, tier-B publication; the verifier round's AV03/X1 at-arm
inheritance, AV02/AV01/X21 fast-rise boundary, AV05/AV06/AV07 closed-loop deepen-on-held, X16/X14 hold-out loud show),
`tests/test_detector_corpus.py` (harness).

## 13. `desk.py` — facade (all public methods return plain dicts/dataclasses in engineering units)

```python
class DeskError(Exception): code: str
class Desk:
    def __init__(self, d: Descriptor, conn: X32Connection, policy: Policy, events: EventBus, snapshots: SnapshotStore)
    # reads
    async def get_strip(self, t: Target) -> dict     # name, color, icon, source(token), fader_db, muted, pan, lr_assigned, mono_level, eq_on, eq summary, dyn summary, gate summary(ch), insert (bus/ch)
    async def get_channel(self, ch: int) -> dict; get_bus(bus) ; get_main() -> {"st": {...}, "m": {...}}
    async def get_sends(self, t: Target) -> list[dict]   # [{bus, bus_name, level_db, muted(on=False), pan, type}]
    async def get_eq(self, t: Target) -> dict            # {"on": bool, "bands": [{band, type, freq_hz, gain_db, q}]}
    async def get_dynamics(self, t: Target) -> dict      # {"gate": {...} | None, "comp": {...}} engineering units
    async def get_names(self, family: str = "ch") -> dict[int, str]   # cached via node config sweep
    async def resolve(self, s: str | int) -> Target      # parse_target OR unique case-insensitive channel-name match ("tony" → ch.5 'Vox Tony')
    async def list_scenes(self) -> list[dict]            # [{index, name, has_data}] (0..99)
    async def current_scene(self) -> dict                # {index, name}
    async def dump(self) -> DeskState
    async def ensure_pre_write_snapshot(self) -> Snapshot | None   # first Tier≥1 write of the session → SnapshotStore.save(label="auto-pre-write")
    # writes (Tier 1; all go through policy + ensure_pre_write_snapshot + rate limiter)
    async def set_level(self, t: Target, db: float, *, ramp_ms: int | None = None, send_to: int | None = None, force: bool = False, kind="fader") -> dict   # {"target","before_db","after_db","clamped":…,"ramp_ms"}
    async def adjust_level(self, t: Target, delta_db: float, *, ramp_ms=None, send_to=None, force=False) -> dict
    async def set_mute(self, t: Target, muted: bool) -> dict
    async def set_send(self, ch: Target, bus: int, db: float, *, ramp_ms=None, force=False) -> dict ; adjust_send(...)
    async def set_pan(self, t: Target, pan: int) -> dict                       # −100..+100
    async def set_eq_band(self, t: Target, band: int, *, freq_hz=None, gain_db=None, q=None, type=None, on: bool | None = None) -> dict
    async def set_comp(self, t: Target, *, on=None, threshold_db=None, ratio=None, attack_ms=None, release_ms=None, knee=None, makeup_db=None, mix_pct=None) -> dict
    async def set_gate(self, t: Target, *, on=None, threshold_db=None, range_db=None, attack_ms=None, hold_ms=None, release_ms=None) -> dict
    async def label(self, t: Target, *, name=None, color=None, icon=None) -> dict
    async def panic(self) -> dict     # mutes main st, main m, all 16 buses, 6 matrices as fast as possible (fire-and-forget sets, no ramps, bypass rate limit), returns elapsed ms + list; Tier 1. Cancels running ramps first and bumps panic_count (restore() and CFS² abort on it); latches the outputs (PANIC_LATCHED on Tier-1 unmute/restore until clear_panic_latch()); re-sent on reconnect if unconfirmed
    def clear_panic_latch(self, keys=None) -> list[str]; panic_latched: list[str]; last_panic: dict | None
    # Tier 2 executors (server does the confirmation dance; these just execute)
    async def set_main_level(self, which: str, db: float, *, ramp_ms=None) -> dict ; set_main_mute(which, muted)
    async def recall_scene(self, index: int) -> dict ; save_scene(index, name, notes="") -> dict
    async def restore(self, snap: Snapshot, *, scope: str | None = None) -> dict   # nodes.restore_plan → conn.slash(line) per section (awaits the echo); rate-limited; returns count + duration + failures
    async def set_source(self, t: Target, source: str) -> dict ; set_phantom(headamp_index|target, on)
    async def set_geq_band(self, fx_slot: int, side: str, band: int, gain_db: float) -> None   # raw GEQ write (used by cfs via a GeqWriter adapter); validated by policy.validate_notch when called from cfs
    async def set_insert(self, t: Target, *, sel: str | None = None, on: bool | None = None, pos: str | None = None)
    async def set_fx_type(self, slot: int, fx_type: str)
    async def get_fx(self, slot: int) -> dict            # {slot, type, params: {...}} (GEQ decoded to bands)
    async def get_inserts(self) -> dict[str, dict]       # every strip's insert {sel, on, pos}
```
Ramping: `set_level` computes `policy.ramp_steps` and writes each step via `conn.set` with `asyncio.sleep(step)`;
concurrent ramps on the same address cancel the previous one (`dict[address, asyncio.Task]`).
Every write publishes `events.publish("write", address=…, value=…, tier=…, tool=…)`.

## 14. `provision.py`

```python
@dataclass class InsertInfo: target: str; sel: str; on: bool; pos: str; fx_slot: int | None; side: str | None; fx_type: str | None
@dataclass class GeqStatus: bus: int; ok: bool; reasons: list[str]; insert: InsertInfo | None; bands_db: list[float] | None; flat: bool; notches: list[dict]; matched_session: str | None
async def validate_ringout_eqs(desk: Desk, buses: list[int], reports: ReportStore) -> dict[int, GeqStatus]   # read-only
async def plan_setup(desk, buses) -> SetupPlan     # what would change: slot allocations (pairs on one GEQ2), type loads, insert patches; `needs_tier2: bool`
async def apply_setup(desk, plan) -> dict          # idempotent
@dataclass class MicCandidate: ch: int; name: str; muted: bool; send_db: float | None; source: str; physical_input: bool; in_mute_group: bool | None; patch_mic: bool | None; owner: str | None; include: bool; notes: list[str]
async def discover_mics(desk, bus: int, *, patch: PatchPlan | None, floor_db: float | None = None) -> list[MicCandidate]
@dataclass class Preflight: ok: bool; blockers: list[str]; warnings: list[str]; mics: list[MicCandidate]; geq: GeqStatus; master_db: float; bus_name: str
async def preflight(desk, bus: int, *, patch=None, reports=None) -> Preflight
```
Rules: never unmute anything automatically; `include` is a suggestion the model must confirm with the user.
Slot allocation prefers `geq.insert_slots_preferred` (5–8); a slot is free iff its type is not a GEQ already
used by another bus and no strip inserts it; dual-mono pairs buses (L, R) on one slot; buses already carrying
a GEQ insert are reused as-is.

## 15. `cfs.py` — CFS² sessions

```python
class CfsMode(str, Enum): IDLE, WATCH, RINGOUT, SYSTEM
@dataclass class CfsState: mode; session_id; bus; bus_name; master_db; budget_left; candidate: dict|None; notches: list[dict]; stage: str|None; started: float|None; plan: dict|None
class ReportStore: dir; def save(report: dict) -> Path (JSON + .md summary); def latest_for_bus(bus) -> dict|None; def list() -> list
class CfsManager:
    def __init__(self, desk: Desk, policy: Policy, events: EventBus, reports: ReportStore, frames: FrameSource | None = None, *, clock=time.time)
    state: CfsState
    async def feedback_watch(self, bus: int, *, notch_budget: int = 6, patch=None) -> dict   # runs preflight (raises with explanation if blocked), sets RTA source, starts LiveMeters (or injected frames) + detector task; returns preflight + state
    async def stop(self) -> dict                                                            # disarm; returns session log + saves report
    async def ring_out(self, bus: int, *, target_gain_db: float | None, step_db, dwell_ms, notch_budget, patch=None) -> dict   # runs to completion (or abort) — the server awaits it; publishes "cfs.stage" events; restores start level on abort/connection loss; returns report
    async def ring_out_system(self, plan: dict) -> dict     # plan = {"stages": [{"bus": 3, "target_gain_db": …, "mics": [ch…]}, …, {"bus": "main"}]}
    async def abort(self, reason: str) -> None
```
Ring-out state machine (events `cfs.stage` with `stage` ∈ PREFLIGHT, SNAPSHOT, ARM, RAISE, HOLD, NOTCH, VERIFY,
BACKOFF, DONE, ABORT): snapshot bus master → set RTA source → loop: raise master by `step_db` (clamped by
`policy.clamp_level(bus)` and `ringout.master_ceiling_db`), dwell `dwell_ms` while detector runs; on detection
→ HOLD (no further raises), NOTCH via NotchController + GeqWriter (policy.validate_notch), VERIFY: wait
`decay_verify_s`, require the band to drop ≥ `decay_verify_db` (else deepen again; if it cannot deepen → ABORT
with backoff `abort_backoff_db`); stop when budget spent or target reached; then back off `safety_margin_db`
→ DONE → report. Connection DEGRADED at any point → restore starting master immediately (fire-and-forget
retries every 500 ms until acked or 10 s) → ABORT.

## 16. `patches.py`

```python
@dataclass class PatchRow: channel: int; name: str; color: str | None; source: str | None; mic: bool; owner: str | None; monitor_bus: int | None; notes: str | None
@dataclass class PatchPlan: rows: list[PatchRow]; band: str | None; venue: str | None; source_file: Path
def load_patch_plan(path: Path) -> PatchPlan     # .yaml or .csv (header: channel,name,color,source,mic,owner,monitor_bus,notes)
async def apply_patch_plan(desk, plan, *, include_source: bool = False) -> dict    # labels only (Tier 1); source changes require include_source (Tier 2 — server guards)
async def export_patch_plan(desk, path: Path, *, merge_with: PatchPlan | None) -> PatchPlan
```

## 17. `webui.py` + `webui/index.html`

- `DashboardServer(settings, events, cfs: CfsManager | None, frames: FrameSource | None, *, band_hz, geq_band_hz)`
  with `async start()`/`stop()`, using `websockets.asyncio.server.serve(handler, host, port, process_request=…)`
  where `process_request` serves `GET /` → `webui/index.html` (and `/healthz` → JSON) and lets `/ws` upgrade.
- Server → client JSON messages (client never sends; inbound WS messages are ignored):
  - `{"t":"hello","band_hz":[…100],"geq_band_hz":[…31],"version":"0.1.0","fps":20}`
  - `{"t":"rta","ts":…,"db":[…100]}` — decimated to ≤ `fps` per second
  - `{"t":"state","mode":"idle|watch|ringout|system","session_id":…,"bus":3,"bus_name":"Wedge A","master_db":-12.0,"budget_left":4,"candidate":{"band":42,"freq_hz":2450,"confidence":0.62,"level_db":…}|null,"stage":"RAISE"|null,"connection":{"state":"connected","console":"X32-RACK","rtt_ms":3},"rta_source":"bus.3"}` — on change and at ≥1 Hz
  - `{"t":"notches","items":[{"bus":3,"band":21,"freq_hz":2000,"depth_db":-6,"session_id":"…","ts":…}]}`
  - `{"t":"event","ts":…,"type":"cfs.notch","data":{…}}` — every non-frame EventBus event
- `index.html`: single file, dark theme, high contrast, large elements, no hover-only info, phone/tablet OK.
  Views: RTA bars (canvas), waterfall (canvas, 2-D heatmap, newest at bottom, colour ramp), notch markers
  overlaid on the RTA at their band with depth label, state panel (mode, bus, master dB, budget, candidate
  confidence meter, connection), scrolling event log (last 200). `?demo=1` runs an in-page synthetic source
  (music + a ring at 2.4 kHz growing then notched) with no server. Reconnects WS with backoff.

## 18. `fakedesk.py` — Python X32 emulator

```
python -m x32mcp.fakedesk [--port 10023] [--host 127.0.0.1] [--name X32-FAKE] [--scene-dir …]
class FakeDesk:
    def __init__(self, d: Descriptor, *, host="127.0.0.1", port=0, name="X32-FAKE", rta: SyntheticRta | None = None)
    async def start() -> tuple[str,int]; async def stop()
    state: dict[str, Any]        # concrete OSC address → raw OSC value (int/float/str), initialised from descriptor defaults (sensible: faders 0.75 (0 dB) for main? NO: main -inf? → main st fader 0.75, ch faders 0.0 (-oo)? Use: ch faders 0.75, buses 0.75, sends 0.0, names "Ch01"…, colours by index)
    def get(address) / set(address, value)      # Python-side access for tests
    clients: dict[addr, expiry]                 # /xremote subscribers (10 s)
    meters: dict[addr, (type, expiry)]          # /meters subscribers (10 s), frames every 50 ms
    scenes: dict[int, dict]                     # index → {"name","notes","state"} ; /-action/goscene loads if stored
    faults: drop_next(n), silence(bool), latency_ms
```
Behaviour: replies to source port; `/info`, `/xinfo` (also answers broadcast), `/status`; get = address no
args → reply `address, typetag, value`; set = address + arg → store (quantise faders to 1023 steps and
`steps`-quantise scaled params), echo to xremote subscribers except sender; `/node ,s path` → reply address
`"node"` `,s` with `render_node_line(...)` + `"\n"`; `/ ,s "<path> v1 v2 …"` → parse with `nodes.parse_node_line`
(leading slash optional, partial trailing lists allowed), store via `to_raw`, echo the whole datagram back to the
sender, push changed leaves to xremote clients (transport.md §6.6); `/-action/goscene ,i` → sets `/-show/prepos/current`
and loads stored state if any; `/save ,siss scene idx name notes` → stores a copy; `/-show/showfile/scene/NNN/name`;
`/meters` → subscribe and stream blobs with the exact real layout (`meters.parse_meter_blob` must decode them);
`/renew` extends; `/-fake/*` control addresses for tests (`/-fake/ring ,fi hz on` toggles a synthetic ring,
`/-fake/drop ,i n`). Closed-loop: the SyntheticRta ring level is attenuated by the GEQ gain (from `state`) of the
FX slot inserted on the RTA-source bus at the ring's band — so a correct notch makes the ring decay.
Tests: start on port 0, drive with `X32Connection`: connect/info, get/set round trip, node line for
every section parses with zero `missing`, xremote push arrives, meters frames decode, scene recall/save.

## 19. `server.py` — tools

`MCPServer("x32-mcp", instructions=…, lifespan=…)`; one `App` object in lifespan state holding
`settings, descriptor, events, conn, policy, snapshots, desk, cfs, dashboard`. Tools are thin: parse args →
policy → desk → response dict. **Every tool returns a dict** with `ok: bool` and `summary: str`; errors are
`{"ok": false, "error": {"code": …, "message": …}, "summary": …}` — tools never raise to the client except
for programming errors. Tier 2 tools accept `confirm_token: str | None = None` and return
`{"ok": false, "requires_confirmation": true, "action_summary": …, "confirm_token": …, "expires_in_s": 300, "summary": …}`
on the first call.

Tool list (names/signatures fixed):
```
discover_consoles(timeout_s: float = 2.0)
connect(host: str, port: int = 10023) ; disconnect() ; connection_status()
get_channel(ch: int) ; get_bus(bus: int) ; get_main() ; get_strip(target: str)
get_channel_sends(ch: int) ; get_eq(target: str) ; get_dynamics(target: str)
list_scenes() ; get_current_scene()
dump_desk_state(sections: list[str] | None = None)       # returns summary + counts + path of a temp snapshot? → returns DeskState dict (may be large) — include `truncated` guidance: returns sections dict
set_fader(target: str, db: float, ramp_ms: int = 300, force: bool = False)
adjust_fader(target: str, delta_db: float, ramp_ms: int = 300, force: bool = False)
mute(target: str) ; unmute(target: str)
set_send(ch: str, bus: int, db: float, ramp_ms: int = 300, force: bool = False) ; adjust_send(ch: str, bus: int, delta_db: float, ramp_ms: int = 300, force: bool = False)
set_eq_band(target: str, band: int, freq_hz: float | None = None, gain_db: float | None = None, q: float | None = None, type: str | None = None, on: bool | None = None)
set_pan(target: str, pan: int)
set_comp(target: str, on: bool | None = None, threshold_db: float | None = None, ratio: float | None = None, attack_ms: float | None = None, release_ms: float | None = None, knee: int | None = None, makeup_db: float | None = None, mix_pct: int | None = None)
set_gate(target: str, on=None, threshold_db=None, range_db=None, attack_ms=None, hold_ms=None, release_ms=None)
panic()
clear_panic(confirm_token=None)                                   # T2: lift the panic latch (unmutes nothing)
set_main_fader(which: str = "st", db: float = -90, ramp_ms: int = 300, confirm_token: str | None = None) ; set_main_mute(which: str, muted: bool, confirm_token=None)
recall_scene(scene: str | int, confirm_token: str | None = None) ; save_scene(index: int, name: str, notes: str = "", confirm_token=None)
snapshot_desk(label: str = "") ; list_snapshots() ; restore_snapshot(id: str, scope: str | None = None, confirm_token=None) ; diff_snapshot(id: str = "latest", scope: str | None = None)
show_mode(on: bool)
label_channel(ch: int, name: str | None = None, color: str | None = None, icon: int | None = None)
apply_patch_plan(file: str, include_source: bool = False, confirm_token=None) ; export_patch_plan(file: str)
set_channel_config(ch: int, source: str | None = None, link: bool | None = None, confirm_token=None)
get_meters(type: str = "channels", duration_ms: int = 500)      # "channels" | "buses" | "main" → averaged dB
get_rta(target: str | None = None, frames: int = 10)
setup_ringout_eqs(buses: list[int], confirm_token=None) ; validate_ringout_eqs(buses: list[int])
discover_mics(bus: int, patch_file: str | None = None)
feedback_watch(bus: int, notch_budget: int = 6, patch_file: str | None = None) ; feedback_watch_stop() ; cfs_status()
ring_out(bus: int, target_gain_db: float | None = None, step_db: float = 1.0, dwell_ms: int = 1500, notch_budget: int = 6, patch_file: str | None = None, confirm_token=None)
ring_out_system(plan: dict | None = None, confirm_token=None)
list_ringout_reports(bus: int | None = None) ; get_ringout_report(id: str)
dashboard_status()
```
Resources: `x32://device` (device.yaml text), `x32://snapshot/latest` (JSON), `x32://cfs/state` (JSON),
`x32://patches/{name}`.
`main()`: configure logging to stderr, build App, `server.run("stdio")`. If `X32_HOST` is set, connect in
lifespan (errors → logged, server still starts). Dashboard starts in lifespan if enabled (bind failure → warning).

## 19a. Known facts that override earlier assumptions (from docs/research, all VERIFIED)

- Maillot's Windows emulator: port hard-coded 10023, meters are zero-filled, `/renew`/`/batchsubscribe` unimplemented,
  no scenes. That is why this repo ships its own `fakedesk.py` (which does implement them) — nothing to download.
- `mcp` 2.2.0: `from mcp.server.mcpserver import MCPServer, Context`; lifespan is entered once; the loop is asyncio
  under anyio, so `asyncio.create_task` inside the lifespan is fine; `ctx.request_context.lifespan_context` holds the
  yielded object. `ToolError` exists but we return envelopes instead.
- Termux: `mcp` needs `pydantic-core` (Rust) — native pure-Python is impossible; document `proot-distro` as the route.
- `/-action/goscene ,i` (0..99) recalls a scene; `/load ,si scene idx` also works and returns a status; `/save ,siss scene idx name notes`.
- `/insert/sel` enum is 23 entries (OFF, FX1L … FX8R, AUX1–6). `/fx/N/type` for N=1–4 is the 61-entry list; for N=5–8 a
  shorter list starting GEQ2=0 — two enums in `device.yaml`.
- Node reply address is `node` (no slash); `/` (node-style write) is echoed back verbatim; every other reply echoes the
  request address.

## 20. Test matrix summary

| module | unit | integration (fakedesk) |
|---|---|---|
| osc | golden vectors, tolerant decode | — |
| scales | taper round-trips, monotonic, lin/log/enum | — |
| descriptor | load/validate, reverse lookup, tiers | — |
| connection | in-test UDP responder: retry, timeout, degraded, blob | connect/info, node sweep |
| nodes | parse/render round-trip, diff English, snapshots | dump < 5 s, `missing == []`, restore round-trip |
| policy | clamps, tokens, rate limit, ramps, notch validation | — |
| meters | blob vectors, sources | frames from fakedesk decode |
| detector | 4 synthetic streams, notch controller | — |
| desk | — | every read/write tool path, ramp step count, mute inversion, panic timing |
| provision/cfs | plan/allocation logic with a fake Desk state | setup+validate+feedback_watch+ring_out closed loop |
| webui | message schema, static serve | WS client receives hello+rta+state from SyntheticRta |
| server | tool envelope shape, confirmation dance, show mode | end-to-end via fakedesk |
