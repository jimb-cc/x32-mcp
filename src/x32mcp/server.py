"""MCP server: the ``x32-mcp`` :class:`MCPServer`, its tools, resources and ``main()`` (DESIGN.md §19).

Tools are thin: parse arguments → :class:`~x32mcp.policy.Policy` → :class:`~x32mcp.desk.Desk`
(or :mod:`~x32mcp.cfs` / :mod:`~x32mcp.provision` / :mod:`~x32mcp.patches`) → an envelope dict.
Everything the tools share lives in one :class:`App` (settings, descriptor, event bus,
connection, policy, snapshot store, desk, report store, CFS² manager, dashboard) that the
lifespan builds once and publishes as the module-level ``app``; the tools are plain ``async``
functions, so tests call them directly with an ``App`` built on the fake desk.

Envelope (fixed by DESIGN §19): every tool returns ``{"ok": bool, "summary": str, …}``; a
failure is ``{"ok": false, "error": {"code", "message", …details}, "summary"}`` (tools never
raise to the client); a Tier-2 first call is ``{"ok": false, "requires_confirmation": true,
"action_summary", "confirm_token", "expires_in_s", "summary"}``. Result fields of a single
object are merged into the envelope; lists sit under a named key (``sends``, ``scenes``,
``snapshots``, ``changes``, ``reports`` …). Levels follow the Desk convention: ``*_db`` floats
(``None`` for −∞) plus text siblings (``fader``, ``before``, ``after``: ``"-oo"``, ``"-6.0"``).
Every envelope passes through :func:`_jsonable` (−∞ → ``"-oo"``, NaN → ``null``, enums →
values, dataclasses/Paths/Targets → plain) so no non-JSON value ever reaches the transport.

Decisions where DESIGN.md is silent:

* **Not connected.** Tools that need the desk return ``NOT_CONNECTED`` while the connection is
  DISCONNECTED/CONNECTING; while DEGRADED they try (reads may still work, writes refuse inside
  ``Desk``). ``connection_status`` reports ``ok: false`` with ``NOT_CONNECTED``/``DEGRADED``.
* **Timeouts.** Every tool runs under ``asyncio.wait_for`` (default 30 s; level moves
  ``ramp_ms`` + 15 s, dumps/diffs 60 s, restores 30 s + one second per 40 sections, ring-outs
  a budget computed from steps × dwell + verify time, system runs 400 s per stage) and reports
  ``TIMEOUT`` instead of hanging. Network waits below that are bounded by the connection.
* **Tier-2 dance.** ``policy.check_show_mode_allows(action)`` runs *before* ``policy.guard``, so
  show mode refuses scene recall/save and ring-outs even when a token is presented; the token
  payload is the normalised request (e.g. the resolved scene index, the concrete setup plan),
  so a token can only execute what its ``action_summary`` described.
* **Bus arguments** of the CFS² tools accept ``1..16`` or ``"main"`` (Main LR) — DESIGN spells
  them ``int``; ``"main"`` is needed for ``ring_out_system``'s last stage.
* **Summaries** name strips (``Ch 5 'Vox Tony'``) using the desk's names (``Desk.get_names``,
  cached 2 s); a failed name read just drops the quote.
* ``set_channel_config(link=…)`` writes ``/config/chlink/N-M`` (Tier 2) through the connection
  with the same snapshot-before-write + rate limiter the Desk applies (Desk has no link writer).
* ``get_meters``/``get_rta`` open a short-lived :class:`~x32mcp.meters.LiveMeters` subscription
  (``/meters/0`` and ``/meters/2`` slices for ``get_meters`` — meters.md §3; ``/meters/15`` for
  ``get_rta``) and average ``n`` frames; while a CFS² session runs, ``get_rta`` reads its
  frames and refuses to re-point the RTA.
* ``ring_out`` returns the report without ``detection_log`` (≤ 200 rows) — ``get_ringout_report``
  has everything; ``diff_snapshot`` returns at most 200 changes (``truncated``) plus ``count``.
* Patch files are looked up as given, then under ``settings.patch_dir`` and ``settings.home``
  (``.yaml``/``.yml``/``.csv`` suffix optional); ``export_patch_plan`` merges the metadata of an
  existing target file (patches/README.md).
* ``App(settings, *, conn_opts=, frames=, detector_cfg=)`` — extras for tests: connection
  options (short timeouts), an injected RTA :class:`~x32mcp.meters.FrameSource` and a detector
  config, all forwarded to :class:`~x32mcp.cfs.CfsManager`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import json
import logging
import math
import sys
import time
from contextlib import asynccontextmanager
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Sequence

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError

from . import __version__
from . import patches as _patches
from . import provision as _prov
from .cfs import CfsError, CfsManager, CfsMode, ReportStore
from .config import Settings
from .connection import ConnectionError as X32ConnectionError, ConnectionState, NotConnected, RequestTimeout, X32Connection
from .descriptor import Descriptor
from .desk import Desk, DeskError
from .events import EventBus
from .meters import METER_GROUPS, LiveMeters, RtaSourceError, average_frames, rta_band_hz, set_rta_source
from .nodes import SnapshotError, SnapshotStore, describe_changes, diff_states
from .patches import PatchError
from .policy import SHOW_MODE_BLOCKED, PendingConfirmation, Policy, PolicyError
from .scales import format_db
from .targets import Target, TargetError
from .webui import DashboardServer, WebUIError

__all__ = ["App", "server", "app", "main", "INSTRUCTIONS"]

log = logging.getLogger(__name__)

DEFAULT_TOOL_TIMEOUT_S = 30.0
CONNECT_TIMEOUT_S = 15.0
DUMP_TIMEOUT_S = 60.0
RESTORE_BASE_TIMEOUT_S = 30.0
RESTORE_SECTIONS_PER_S = 40.0  # restore runs at <= 50 lines/s (desk.py); leave headroom
RING_OUT_BASE_TIMEOUT_S = 60.0
SYSTEM_STAGE_TIMEOUT_S = 400.0
FRAME_PERIOD_S = 0.05  # meters.md §1.2: one frame per 50 ms at time factor 1
MAX_DIFF_CHANGES = 200
_MINUS = "−"  # typographic minus for human summaries
_ARROW = "→"

INSTRUCTIONS = """\
x32-mcp controls one Behringer X32/M32 mixer over OSC. Call connect(host) first (discover_consoles \
finds desks on the LAN), then read with get_channel/get_bus/get_main/get_strip/get_channel_sends/\
get_eq/get_dynamics. Units everywhere: dB for levels and gains ("-oo" = fader fully down), Hz, ms, %, \
pan -100 (L) .. +100 (R), 1-based channel/bus numbers, enum tokens (PEQ, RD, IN05). A target is \
'ch.5', 'bus.3', 'main.st', 'main.m', 'dca.1', 'mtx.2', 'auxin.1', 'fxrtn.1' or a strip name ('Vox Tony').
Safety tiers (enforced in code): Tier 0 reads are always allowed. Tier 1 mix moves (set_fader, \
adjust_fader, mute/unmute, set_send, set_eq_band, set_pan, set_comp, set_gate, label_channel, \
apply_patch_plan names/colours, feedback_watch) are clamped (channels +5 dB, buses/main/sends 0 dB, \
EQ +-15 dB), ramped (default 300 ms) and limited to +-6 dB per call (+-3 dB in show mode); a larger \
move needs force=true, which you pass ONLY when the user explicitly asked for a move of that size. \
The first write of a session automatically snapshots the whole desk (restore_snapshot undoes). \
Tier 2 (set_main_fader, set_main_mute, recall_scene, save_scene, restore_snapshot, set_channel_config, \
apply_patch_plan with include_source, setup_ringout_eqs, ring_out, ring_out_system) uses a confirmation \
dance: the first call returns requires_confirmation=true with an action_summary and a single-use \
confirm_token (60 s). Show the action_summary to the user; only after they agree, call the SAME tool \
with the SAME arguments plus confirm_token. Never invent, reuse or pre-empt a token and never confirm \
on the user's behalf.
Every tool returns {ok, summary, ...}; a failure is {ok: false, error: {code, message}} - tools never \
raise. panic() mutes every mix bus, matrix and the mains at once (Tier 1, never blocked): use it \
whenever the user says stop / kill it / mute everything. show_mode(true) refuses scene recall/save and \
ring-outs and tightens relative moves to +-3 dB. The dashboard (dashboard_status) is a read-only web \
page for the operator; it cannot change the desk. CFS2 feedback tools: validate_ringout_eqs and \
discover_mics first, confirm the open-mic list with the user, then feedback_watch (human drives the \
gain) or ring_out (automatic, Tier 2); feedback_watch_stop / cfs_status / list_ringout_reports report.\
"""


# -- app --------------------------------------------------------------------------------------------


class App:
    """Everything the tools share (module doc). Build it, ``await start()``, ``await close()``."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        conn_opts: dict[str, Any] | None = None,
        frames: Any = None,
        detector_cfg: Any = None,
    ) -> None:
        self.settings = settings if settings is not None else Settings.from_env()
        self.descriptor = Descriptor.load(self.settings.device_yaml)
        self.events = EventBus()
        self.conn = X32Connection(self.descriptor, self.events, **(conn_opts or {}))
        self.policy = Policy(self.descriptor, self.events)
        self.snapshots = SnapshotStore(self.settings.snapshot_dir)
        self.desk = Desk(self.descriptor, self.conn, self.policy, self.events, self.snapshots)
        self.reports = ReportStore(self.settings.report_dir)
        self.cfs = CfsManager(
            self.desk, self.policy, self.events, self.reports, frames=frames,
            detector_cfg=detector_cfg, snapshots=self.snapshots,
        )
        self.dashboard = DashboardServer(
            self.settings, self.events, self.cfs, self.cfs.frames,
            band_hz=rta_band_hz(self.descriptor), geq_band_hz=self.descriptor.geq["band_hz"],
            connection_status=lambda: self.conn.status,
        )
        self.started_at = time.time()

    async def start(self) -> None:
        """Dashboard (if enabled; a bind failure is a warning) and autoconnect (``X32_HOST``;
        a failure is a warning) — the MCP server always comes up."""
        try:
            self.settings.ensure_dirs()
        except OSError as e:
            log.warning("cannot create data directories under %s: %s", self.settings.home, e)
        if self.settings.dash_enabled:
            try:
                await self.dashboard.start()
                log.info("dashboard at %s", self.dashboard.url)
            except WebUIError as e:
                log.warning("dashboard not started: %s", e)
            except Exception:  # never let the dashboard take the MCP server down
                log.exception("dashboard failed to start; continuing without it")
        if self.settings.x32_host:
            try:
                info = await self.connect(self.settings.x32_host, self.settings.x32_port)
                log.info("autoconnected to %s (%s FW %s)", info.name, info.model, info.firmware)
            except Exception as e:
                log.warning("autoconnect to %s:%d failed: %s", self.settings.x32_host, self.settings.x32_port, e)

    async def connect(self, host: str, port: int = 10023) -> Any:
        """``conn.connect`` bounded by :data:`CONNECT_TIMEOUT_S`; drops the desk cache."""
        if self.cfs.active:
            await self.cfs.abort("reconnect requested")
        info = await asyncio.wait_for(self.conn.connect(host, int(port)), timeout=CONNECT_TIMEOUT_S)
        self.desk.invalidate()
        return info

    async def close(self) -> None:
        """Shutdown in dependency order: CFS² (aborts + stops frames), dashboard, desk, connection."""
        for what, step in (
            ("cfs", self.cfs.close), ("dashboard", self.dashboard.stop),
            ("desk", self.desk.close), ("connection", self.conn.close),
        ):
            try:
                await asyncio.wait_for(step(), timeout=35.0)
            except Exception as e:  # keep going: every component must get its shutdown
                log.warning("shutdown: %s failed: %s", what, e)


app: App | None = None


def _app() -> App:
    if app is None:
        raise DeskError("NOT_READY", "the server has not finished starting; try again")
    return app


def _desk() -> Desk:
    """The desk, or ``NOT_CONNECTED`` when the connection is down (DEGRADED is left to try)."""
    a = _app()
    st = a.conn.state
    if st in (ConnectionState.DISCONNECTED, ConnectionState.CONNECTING):
        err = a.conn.status.error
        raise DeskError(
            "NOT_CONNECTED",
            "not connected to a desk: call connect(host) first" + (f" (last error: {err})" if err else ""),
            state=st.value,
        )
    return a.desk


# -- envelope helpers ----------------------------------------------------------------------------------


def _jsonable(v: Any) -> Any:
    """Make any result JSON-safe: −∞/+∞ → ``"-oo"``/``"+oo"``, NaN → None, enums → values,
    Targets → keys, Paths → str, dataclasses/objects with ``to_dict`` → dicts, tuples → lists."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        if math.isinf(v):
            return "-oo" if v < 0 else "+oo"
        if math.isnan(v):
            return None
        return v
    if isinstance(v, Enum):
        return _jsonable(v.value)
    if isinstance(v, Target):
        return v.key
    if isinstance(v, Path):
        return str(v)
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [_jsonable(x) for x in v]
    to_dict = getattr(v, "to_dict", None)
    if callable(to_dict):
        return _jsonable(to_dict())
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return _jsonable(dataclasses.asdict(v))
    return str(v)


def _ok(summary: str, /, **fields: Any) -> dict[str, Any]:
    """Success envelope; a result's own ``ok``/``summary`` keys are dropped in favour of ours."""
    out: dict[str, Any] = dict(fields)
    out.pop("ok", None)
    out.pop("summary", None)
    return {"ok": True, "summary": summary, **out}


def _error_dict(e: BaseException, tool: str) -> dict[str, Any]:
    if isinstance(e, (DeskError, PolicyError, CfsError)):
        return e.to_dict()
    if isinstance(e, PatchError):
        return {"code": "BAD_PATCH", "message": str(e), "problems": list(e.problems), "path": str(e.path) if e.path else None}
    if isinstance(e, (TargetError, RtaSourceError, ValueError)):
        return {"code": "BAD_ARGUMENT", "message": str(e)}
    if isinstance(e, SnapshotError):
        return {"code": "NOT_FOUND", "message": str(e)}
    if isinstance(e, NotConnected):
        return {"code": "NOT_CONNECTED", "message": str(e)}
    if isinstance(e, (RequestTimeout, asyncio.TimeoutError, TimeoutError)):
        return {"code": "TIMEOUT", "message": str(e) or f"{tool} did not finish in time; the desk is not answering"}
    if isinstance(e, X32ConnectionError):
        return {"code": "CONNECTION", "message": str(e)}
    if isinstance(e, OSError):
        return {"code": "IO_ERROR", "message": str(e)}
    log.exception("tool %s failed unexpectedly", tool)
    return {"code": "INTERNAL", "message": f"{type(e).__name__}: {e}"}


def _fail(e: BaseException, tool: str) -> dict[str, Any]:
    err = _jsonable(_error_dict(e, tool))
    if err["code"] != "INTERNAL":
        log.info("tool %s refused: %s: %s", tool, err["code"], err["message"])
    return {"ok": False, "error": err, "summary": f"{tool} failed: {err['message']}"}


def _tool(timeout: float | None = DEFAULT_TOOL_TIMEOUT_S) -> Callable[[Callable[..., Awaitable[dict[str, Any]]]], Callable[..., Awaitable[dict[str, Any]]]]:
    """Wrap a tool body: bounded by ``timeout`` (None → the body bounds itself), every
    exception → the error envelope, every result → JSON-safe."""

    def deco(fn: Callable[..., Awaitable[dict[str, Any]]]) -> Callable[..., Awaitable[dict[str, Any]]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            try:
                coro = fn(*args, **kwargs)
                res = await (asyncio.wait_for(coro, timeout=timeout) if timeout else coro)
                return _jsonable(res)
            except Exception as e:
                return _fail(e, fn.__name__)

        return wrapper

    return deco


def _confirm(action: str, summary: str, payload: dict[str, Any], confirm_token: str | None, **extra: Any) -> dict[str, Any] | None:
    """The Tier-2 dance: show-mode check, then ``policy.guard``. Returns the pending envelope
    on the first call, ``None`` once a valid token was consumed (raises on a bad one)."""
    a = _app()
    a.policy.check_show_mode_allows(action)
    res = a.policy.guard(action, summary, _jsonable(payload), confirm_token)
    if isinstance(res, PendingConfirmation):
        return {
            "ok": False,
            **res.to_dict(),
            "summary": f"Confirmation required: {summary} — show this to the user; if they agree, call {action} again with the same arguments and this confirm_token.",
            **extra,
        }
    return None


# -- formatting helpers ---------------------------------------------------------------------------------


def _dbs(text: Any) -> str:
    """``"-6.0"`` → ``"−6.0 dB"``, ``"-oo"`` → ``"-oo dB"``, None → ``"?"``."""
    if text is None:
        return "?"
    s = str(text)
    if s == "-oo":
        return "-oo dB"
    if s.startswith("-"):
        s = _MINUS + s[1:]
    return f"{s} dB"


def _db_from_float(v: float | None) -> str:
    return _dbs(None if v is None else format_db(v))


def _pan(p: Any) -> str:
    if not isinstance(p, (int, float)) or isinstance(p, bool):
        return "?"
    p = int(round(p))
    return "C" if p == 0 else (f"L{-p}" if p < 0 else f"R{p}")


def _hz(v: Any) -> str:
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return "?"
    return f"{v / 1000:.2f} kHz".replace(".00 kHz", " kHz") if v >= 1000 else f"{v:g} Hz"


def _who(t: Target, name: str | None) -> str:
    return f"{t.label} '{name}'" if name else t.label


def _who_strip(s: dict[str, Any]) -> str:
    return f"{s['label']} '{s['name']}'" if s.get("name") else str(s.get("label"))


async def _name(desk: Desk, t: Target) -> str:
    """Strip name for a summary (empty when the read fails — a summary is not worth an error)."""
    try:
        return str((await desk.get_names(t.family)).get(t.index) or "")
    except Exception:
        return ""


def _int_arg(v: Any, what: str, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (isinstance(v, float) and not v.is_integer()):
        raise DeskError("BAD_ARGUMENT", f"{what} must be an integer {lo}..{hi}, got {v!r}")
    n = int(v)
    if not lo <= n <= hi:
        raise DeskError("BAD_ARGUMENT", f"{what} must be {lo}..{hi}, got {n}")
    return n


def _main_which(which: Any) -> str:
    w = str(which).strip().lower()
    if w in ("st", "lr", "stereo", "main", "main.st", ""):
        return "st"
    if w in ("m", "mono", "mc", "m/c", "c", "main.m", "centre", "center"):
        return "m"
    raise DeskError("BAD_ARGUMENT", f"which must be 'st' (Main LR) or 'm' (Main M/C), got {which!r}")


def _level_summary(desk_label: str, res: dict[str, Any], *, kind: str = "fader") -> str:
    head = f"{desk_label} {kind}".rstrip()
    s = f"{head} {_dbs(res.get('before'))} {_ARROW} {_dbs(res.get('after'))}"
    notes: list[str] = []
    if res.get("delta_db") is not None:
        notes.append(f"{res['delta_db']:+.1f} dB".replace("-", _MINUS))
    if res.get("ramp_ms"):
        notes.append(f"ramped {res['ramp_ms']} ms")
    cl = res.get("clamped")
    if cl:
        notes.append(f"clamped to the {_db_from_float(cl.get('limit'))} limit, requested {_db_from_float(cl.get('requested'))}")
    if res.get("superseded"):
        notes.append("superseded by a newer move")
    return s + (f" ({'; '.join(notes)})" if notes else "")


def _level_timeout(ramp_ms: Any) -> float:
    try:
        ms = max(0.0, float(ramp_ms))
    except (TypeError, ValueError):
        ms = 0.0
    return ms / 1000.0 + 15.0


def _strip_summary(s: dict[str, Any]) -> str:
    parts = [f"fader {_dbs(s.get('fader'))}", "MUTED" if s.get("muted") else "unmuted"]
    if s.get("pan") is not None:
        parts.append(f"pan {_pan(s['pan'])}")
    if s.get("source"):
        parts.append(f"source {s['source']}")
    if s.get("color"):
        parts.append(f"colour {s['color']}")
    eq = s.get("eq")
    if eq:
        parts.append("EQ " + ("on" if eq.get("on") else "off"))
    comp = s.get("comp")
    if comp:
        parts.append("comp " + (f"on (thr {_db_from_float(comp.get('threshold_db'))}, ratio {comp.get('ratio')})" if comp.get("on") else "off"))
    gate = s.get("gate")
    if gate:
        parts.append("gate " + (f"on (thr {_db_from_float(gate.get('threshold_db'))})" if gate.get("on") else "off"))
    ins = s.get("insert")
    if ins and ins.get("sel") not in (None, "OFF"):
        parts.append(f"insert {ins['sel']} {'on' if ins.get('on') else 'off'}")
    if s.get("lr_assigned") is False:
        parts.append("not assigned to LR")
    return f"{_who_strip(s)}: " + ", ".join(parts)


def _eq_summary(who: str, eq: dict[str, Any]) -> str:
    bands = []
    for b in eq.get("bands") or []:
        g = b.get("gain_db")
        gtxt = f"{g:+.1f} dB".replace("-", _MINUS) if isinstance(g, (int, float)) else "?"
        bands.append(f"{b.get('band')} {b.get('type')} {_hz(b.get('freq_hz'))} {gtxt} Q {b.get('q')}")
    return f"{who} EQ {'on' if eq.get('on') else 'off'}: " + "; ".join(bands)


def _dyn_summary(who: str, dyn: dict[str, Any]) -> str:
    comp, gate = dyn.get("comp"), dyn.get("gate")
    parts = []
    if comp:
        parts.append(
            f"comp {'on' if comp.get('on') else 'off'} ({comp.get('mode')}, thr {_db_from_float(comp.get('threshold_db'))}, "
            f"ratio {comp.get('ratio')}, attack {comp.get('attack_ms')} ms, release {comp.get('release_ms')} ms, "
            f"knee {comp.get('knee')}, makeup {_db_from_float(comp.get('makeup_db'))})"
        )
    if gate:
        parts.append(
            f"gate {'on' if gate.get('on') else 'off'} ({gate.get('mode')}, thr {_db_from_float(gate.get('threshold_db'))}, "
            f"range {gate.get('range_db')} dB, attack {gate.get('attack_ms')} ms, hold {gate.get('hold_ms')} ms, release {gate.get('release_ms')} ms)"
        )
    return f"{who}: " + "; ".join(parts) if parts else f"{who}: no dynamics"


def _applied_text(applied: dict[str, Any]) -> str:
    out = []
    for k, v in applied.items():
        if k.endswith("_db"):
            out.append(f"{k[:-3]} {_db_from_float(v) if isinstance(v, (int, float)) else v}")
        elif k.endswith("_ms"):
            out.append(f"{k[:-3]} {v} ms")
        elif k == "freq_hz":
            out.append(f"freq {_hz(v)}")
        elif k.endswith("_pct"):
            out.append(f"{k[:-4]} {v} %")
        elif isinstance(v, bool):
            out.append(f"{k} {'on' if v else 'off'}")
        else:
            out.append(f"{k} {v}")
    return ", ".join(out)


def _patch_path(file: str, *, must_exist: bool = True) -> Path:
    """Resolve a patch file: as given, then under ``patch_dir`` and ``home``; suffix optional."""
    a = _app()
    raw = Path(str(file)).expanduser()
    bases = [raw] if raw.is_absolute() else [a.settings.patch_dir / raw, a.settings.home / raw, raw]
    tried: list[str] = []
    for base in bases:
        cands = [base] if base.suffix else [base] + [base.with_suffix(s) for s in (".yaml", ".yml", ".csv")]
        for c in cands:
            tried.append(str(c))
            if c.is_file():
                return c
    if must_exist:
        raise DeskError("NOT_FOUND", f"patch file {file!r} not found; looked at " + ", ".join(dict.fromkeys(tried)), file=str(file))
    p = bases[0]
    return p if p.suffix else p.with_suffix(".yaml")


def _load_patch(file: str | None) -> Any:
    if not file:
        return None
    return _patches.load_patch_plan(_patch_path(file), descriptor=_app().descriptor)


def _trim_report(rep: dict[str, Any]) -> dict[str, Any]:
    out = dict(rep)
    if "detection_log" in out:
        out["detection_log_entries"] = len(out.pop("detection_log") or [])
    return out


def _report_summary(rep: dict[str, Any]) -> str:
    return str(rep.get("summary") or f"{rep.get('mode')} on bus {rep.get('bus')}: {rep.get('final_stage')}")


def _bus_target(bus: Any) -> Target:
    return _prov.bus_target(bus)


async def _resolve_scene(desk: Desk, scene: Any) -> dict[str, Any]:
    """``scene`` = index 0..99, a digit string, or a (case-insensitive) scene name → the slot."""
    if isinstance(scene, bool) or scene is None:
        raise DeskError("BAD_ARGUMENT", "scene must be an index 0..99 or a scene name")
    scenes = await desk.list_scenes()
    if isinstance(scene, int) or (isinstance(scene, str) and scene.strip().isdigit()):
        idx = _int_arg(int(scene), "scene index", 0, 99)
        return scenes[idx]
    if not isinstance(scene, str) or not scene.strip():
        raise DeskError("BAD_ARGUMENT", f"scene must be an index 0..99 or a scene name, got {scene!r}")
    needle = scene.strip().lower()
    exact = [s for s in scenes if s["has_data"] and s["name"].strip().lower() == needle]
    if len(exact) == 1:
        return exact[0]
    partial = [s for s in scenes if s["has_data"] and needle in s["name"].lower()]
    hits = exact or partial
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise DeskError("UNKNOWN_SCENE", f"no scene named {scene!r}; list_scenes shows the library", scene=scene)
    raise DeskError(
        "AMBIGUOUS_SCENE", f"{scene!r} matches {len(hits)} scenes: " + ", ".join(f"{s['index']} '{s['name']}'" for s in hits),
        candidates=[{"index": s["index"], "name": s["name"]} for s in hits],
    )


# -- server -------------------------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_server: MCPServer) -> AsyncIterator[App]:
    """Build (or adopt) the :class:`App`, start dashboard/autoconnect, tear everything down on exit."""
    global app
    a = app if app is not None else App()
    app = a
    await a.start()
    log.info("x32-mcp %s ready (home %s)", __version__, a.settings.home)
    try:
        yield a
    finally:
        await a.close()
        log.info("x32-mcp stopped")


server = MCPServer("x32-mcp", instructions=INSTRUCTIONS, version=__version__, lifespan=lifespan)


# -- tools: connection ---------------------------------------------------------------------------------


@server.tool()
@_tool(timeout=None)
async def discover_consoles(timeout_s: float = 2.0, port: int = 10023) -> dict[str, Any]:
    """Find X32/M32 consoles on the LAN by broadcasting /xinfo (to UDP port, default 10023) and
    listening for timeout_s seconds (0.2..30). Returns consoles [{host, port, name, model,
    firmware}]. Tier 0; no connection needed."""
    a = _app()
    ts = min(max(float(timeout_s), 0.2), 30.0)
    found = await asyncio.wait_for(a.conn.discover(ts, port=_int_arg(port, "port", 1, 65535)), timeout=ts + 5.0)
    consoles = [c.to_dict() for c in found]
    if consoles:
        summary = f"Found {len(consoles)} console(s): " + ", ".join(
            f"{c['name']} ({c['model']} FW {c['firmware']}) at {c['host']}:{c['port']}" for c in consoles
        )
    else:
        summary = f"No console answered /xinfo within {ts:g} s (is the desk on the same LAN and UDP 10023 open?)"
    return _ok(summary, consoles=consoles, timeout_s=ts)


@server.tool()
@_tool(timeout=CONNECT_TIMEOUT_S + 10.0)
async def connect(host: str, port: int = 10023) -> dict[str, Any]:
    """Connect to the desk at host:port (UDP, default 10023): /info round trip, then heartbeat.
    Replaces any existing connection. Returns the connection status. Tier 0."""
    a = _app()
    h = str(host).strip()
    if not h:
        raise DeskError("BAD_ARGUMENT", "host must be an IP address or hostname")
    p = _int_arg(port, "port", 1, 65535)
    try:
        info = await a.connect(h, p)
    except X32ConnectionError as e:
        raise DeskError("CONNECT_FAILED", f"cannot connect to {h}:{p}: {e}", host=h, port=p) from None
    except asyncio.TimeoutError:
        raise DeskError("CONNECT_FAILED", f"cannot connect to {h}:{p}: no reply within {CONNECT_TIMEOUT_S:g} s", host=h, port=p) from None
    st = a.conn.status.to_dict()
    return _ok(
        f"Connected to {info.name} ({info.model} FW {info.firmware}, OSC server {info.server_version}) at {h}:{p}"
        + (" — show mode is ON" if a.policy.show_mode else ""),
        **st, show_mode=a.policy.show_mode,
    )


@server.tool()
@_tool()
async def disconnect() -> dict[str, Any]:
    """Close the desk connection (stops any CFS2 session first). Tier 0."""
    a = _app()
    console = a.conn.status.console
    was = a.conn.state.value
    stopped = None
    if a.cfs.active:
        stopped = await asyncio.wait_for(a.cfs.stop(), timeout=35.0)
    await a.conn.close()
    a.desk.invalidate()
    name = console.name if console else "desk"
    return _ok(
        f"Disconnected from {name} (was {was})" + (f"; stopped CFS² {stopped['mode']} session" if stopped and stopped.get("stopped") else ""),
        was=was, cfs_stopped=stopped, state=a.conn.state.value,
    )


def _status_fields(a: App) -> dict[str, Any]:
    snap = a.desk.pre_write_snapshot
    dash = a.dashboard
    st = a.conn.status.to_dict()
    st["last_error"] = st.pop("error", None)  # "error" is the envelope's failure slot
    return {
        **st,
        "show_mode": a.policy.show_mode,
        "relative_limit_db": a.policy.relative_limit_db,
        "pre_write_snapshot": snap.id if snap else None,
        "writes": a.desk.write_count,
        "cfs_mode": a.cfs.mode.value,
        "dashboard": {"enabled": a.settings.dash_enabled, "running": dash.running, "url": dash.url if dash.running else None},
        "version": __version__,
    }


@server.tool()
@_tool()
async def connection_status() -> dict[str, Any]:
    """Connection state (disconnected/connecting/connected/degraded), console identity, round-trip
    time, show mode, CFS2 mode and dashboard URL. ok is false with NOT_CONNECTED or DEGRADED when
    the desk is not (fully) reachable. Tier 0."""
    a = _app()
    fields = _status_fields(a)
    st = a.conn.state
    console = a.conn.status.console
    if st is ConnectionState.CONNECTED and console is not None:
        rtt = fields.get("rtt_ms")
        summary = (
            f"Connected to {console.name} ({console.model} FW {console.firmware}) at {console.host}:{console.port}"
            + (f", rtt {rtt:.1f} ms" if isinstance(rtt, (int, float)) else "")
            + (" — SHOW MODE ON" if a.policy.show_mode else "")
            + (f"; CFS² {a.cfs.mode.value}" if a.cfs.mode is not CfsMode.IDLE else "")
        )
        return _ok(summary, **fields)
    if st is ConnectionState.DEGRADED:
        code, msg = "DEGRADED", f"desk {console.name if console else ''} is not answering (reconnecting; {fields.get('reconnect_attempts')} probe(s)); writes are refused until it is back"
    else:
        code, msg = "NOT_CONNECTED", "not connected to a desk: call connect(host) first" + (f" (last error: {fields.get('last_error')})" if fields.get("last_error") else "")
    return {"ok": False, "error": {"code": code, "message": msg}, "summary": msg, **fields}


# -- tools: reads --------------------------------------------------------------------------------------


@server.tool()
@_tool()
async def get_channel(ch: int) -> dict[str, Any]:
    """Everything about input channel ch (1..32): name, colour, source, fader_db, muted, pan, preamp,
    eq {on, bands}, comp, gate, insert. Levels in dB (fader "-oo" = fully down). Tier 0."""
    desk = _desk()
    s = await desk.get_channel(_int_arg(ch, "ch", 1, 32))
    return _ok(_strip_summary(s), **s)


@server.tool()
@_tool()
async def get_bus(bus: int) -> dict[str, Any]:
    """Everything about mix bus (1..16): name, fader_db, muted, eq (6 bands), comp, insert. Tier 0."""
    desk = _desk()
    s = await desk.get_bus(_int_arg(bus, "bus", 1, 16))
    return _ok(_strip_summary(s), **s)


@server.tool()
@_tool()
async def get_main() -> dict[str, Any]:
    """Main LR ("st") and Main M/C ("m") strips: fader_db, muted, eq, comp, insert. Tier 0."""
    desk = _desk()
    m = await desk.get_main()
    return _ok(_strip_summary(m["st"]) + " | " + _strip_summary(m["m"]), st=m["st"], m=m["m"])


@server.tool()
@_tool()
async def get_strip(target: str) -> dict[str, Any]:
    """Any strip by target ('ch.5', 'bus.3', 'main.st', 'main.m', 'dca.1', 'mtx.2', 'auxin.1',
    'fxrtn.1') or by name ('Vox Tony'); same fields as get_channel. Tier 0."""
    desk = _desk()
    t = await desk.resolve(target)
    s = await desk.get_strip(t)
    return _ok(_strip_summary(s), **s)


@server.tool()
@_tool()
async def get_channel_sends(ch: int) -> dict[str, Any]:
    """The 16 bus sends of channel ch (1..32): [{bus, bus_name, level_db, level, muted, pan, type}]
    (level "-oo" = send fully down; type PRE/POST…). Tier 0."""
    desk = _desk()
    n = _int_arg(ch, "ch", 1, 32)
    t = Target("ch", n)
    sends = await desk.get_sends(t)
    name = await _name(desk, t)
    live = [s for s in sends if s.get("level_db") is not None]
    parts = [
        f"Bus {s['bus']}{' ' + repr(s['bus_name']) if s.get('bus_name') else ''} {_dbs(s['level'])}{' (send off)' if s.get('muted') else ''}"
        for s in live
    ]
    summary = f"{_who(t, name)} sends: " + (", ".join(parts) if parts else "none above -oo") + (
        f" ({len(sends) - len(live)} at -oo)" if len(sends) != len(live) else ""
    )
    return _ok(summary, target=t.key, label=t.label, name=name, sends=sends)


@server.tool()
@_tool()
async def get_eq(target: str) -> dict[str, Any]:
    """EQ of a strip (target or name): {on, bands: [{band, type, freq_hz, gain_db, q}]}; channels have
    4 bands, buses/matrices/mains 6. Types: LCut LShv PEQ VEQ HShv HCut. Tier 0."""
    desk = _desk()
    t = await desk.resolve(target)
    eq = await desk.get_eq(t)
    name = await _name(desk, t)
    return _ok(_eq_summary(_who(t, name), eq), target=t.key, label=t.label, name=name, **eq)


@server.tool()
@_tool()
async def get_dynamics(target: str) -> dict[str, Any]:
    """Compressor (and gate, channels only) of a strip: thresholds in dB, attack/hold/release in ms,
    ratio as the desk token ('4.0'), makeup dB, mix %. Tier 0."""
    desk = _desk()
    t = await desk.resolve(target)
    dyn = await desk.get_dynamics(t)
    name = await _name(desk, t)
    return _ok(_dyn_summary(_who(t, name), dyn), label=t.label, name=name, **dyn)


@server.tool()
@_tool()
async def list_scenes() -> dict[str, Any]:
    """The 100 scene slots: scenes = slots holding data [{index, name, notes, has_data}],
    empty_slots = free indices, current = the loaded scene. Tier 0."""
    desk = _desk()
    scenes = await desk.list_scenes()
    current = await desk.current_scene()
    with_data = [s for s in scenes if s["has_data"]]
    empty = [s["index"] for s in scenes if not s["has_data"]]
    summary = f"{len(with_data)} scene(s) stored (current: {current['index']} '{current['name']}'): " + ", ".join(
        f"{s['index']} '{s['name']}'" for s in with_data
    ) + f"; {len(empty)} empty slot(s)"
    return _ok(summary, scenes=with_data, empty_slots=empty, current=current)


@server.tool()
@_tool()
async def get_current_scene() -> dict[str, Any]:
    """The scene the desk currently has loaded: {index, name, has_data}. Tier 0."""
    desk = _desk()
    cur = await desk.current_scene()
    return _ok(f"Current scene: {cur['index']} '{cur['name']}'" + ("" if cur.get("has_data") else " (empty slot)"), **cur)


@server.tool()
@_tool(timeout=DUMP_TIMEOUT_S)
async def dump_desk_state(sections: list[str] | None = None) -> dict[str, Any]:
    """Raw desk state as /node sections in engineering units. The full desk is ~1500 sections (large):
    pass sections (target keys 'ch.5', families 'bus', or paths '/ch/05') to narrow it. Use
    snapshot_desk to keep a copy on disk instead. Tier 0."""
    desk = _desk()
    state = await desk.dump(sections=sections)
    scope = ", ".join(sections) if sections else "whole desk"
    return _ok(
        f"Dumped {len(state.sections)} section(s) of the {scope}" + (f", {len(state.missing)} missing" if state.missing else ""),
        created=state.created, console=state.console, scene=state.scene, scope=sections,
        section_count=len(state.sections), missing=state.missing, sections=state.sections,
    )


# -- tools: Tier 1 mix moves -----------------------------------------------------------------------------


@server.tool()
@_tool(timeout=None)
async def set_fader(target: str, db: float, ramp_ms: int = 300, force: bool = False) -> dict[str, Any]:
    """Move a fader to db (channels/aux/fx-returns/DCAs clamp at +5 dB, buses/matrices at 0 dB;
    -90 = -oo). Ramped over ramp_ms (0..60000). A move of more than 6 dB (3 dB in show mode)
    is refused unless force=true — pass force only when the user explicitly asked for it. Tier 1;
    main LR/mono need set_main_fader."""
    desk = _desk()
    t = await desk.resolve(target)
    res = await asyncio.wait_for(desk.set_level(t, float(db), ramp_ms=int(ramp_ms), force=bool(force)), timeout=_level_timeout(ramp_ms))
    name = await _name(desk, t)
    return _ok(_level_summary(_who(t, name), res), name=name, **res)


@server.tool()
@_tool(timeout=None)
async def adjust_fader(target: str, delta_db: float, ramp_ms: int = 300, force: bool = False) -> dict[str, Any]:
    """Move a fader by delta_db (e.g. -2 = 2 dB down); same clamps and 6 dB/3 dB relative limit as
    set_fader (force=true only on explicit user request). Tier 1."""
    desk = _desk()
    t = await desk.resolve(target)
    res = await asyncio.wait_for(desk.adjust_level(t, float(delta_db), ramp_ms=int(ramp_ms), force=bool(force)), timeout=_level_timeout(ramp_ms))
    name = await _name(desk, t)
    return _ok(_level_summary(_who(t, name), res), name=name, **res)


@server.tool()
@_tool()
async def mute(target: str) -> dict[str, Any]:
    """Mute a strip (channel, bus, matrix, DCA…). Tier 1; main LR/mono need set_main_mute."""
    desk = _desk()
    t = await desk.resolve(target)
    res = await desk.set_mute(t, True)
    name = await _name(desk, t)
    return _ok(f"{_who(t, name)} muted" + (" (was already muted)" if res.get("was_muted") else ""), name=name, **res)


@server.tool()
@_tool()
async def unmute(target: str) -> dict[str, Any]:
    """Unmute a strip. Tier 1; main LR/mono need set_main_mute."""
    desk = _desk()
    t = await desk.resolve(target)
    res = await desk.set_mute(t, False)
    name = await _name(desk, t)
    return _ok(f"{_who(t, name)} unmuted" + (" (was not muted)" if res.get("was_muted") is False else ""), name=name, **res)


async def _send_summary(desk: Desk, t: Target, res: dict[str, Any]) -> str:
    dest_fam = _app().descriptor.strips[t.family].send_target or "bus"  # bus/main send to matrices
    dest = Target(dest_fam, int(res["send_to"]))
    dest_name = await _name(desk, dest)
    name = await _name(desk, t)
    return _level_summary(f"{_who(dest, dest_name)} send from {_who(t, name)}", res, kind="")


@server.tool()
@_tool(timeout=None)
async def set_send(ch: str, bus: int, db: float, ramp_ms: int = 300, force: bool = False) -> dict[str, Any]:
    """Set the send from input strip ch (target or name) to bus (1..16) to db (clamped at 0 dB;
    -90 = -oo); ramped; 6 dB relative limit unless force (explicit user request only). Tier 1."""
    desk = _desk()
    t = await desk.resolve(ch)
    res = await asyncio.wait_for(desk.set_send(t, int(bus), float(db), ramp_ms=int(ramp_ms), force=bool(force)), timeout=_level_timeout(ramp_ms))
    return _ok(await _send_summary(desk, t, res), **res)


@server.tool()
@_tool(timeout=None)
async def adjust_send(ch: str, bus: int, delta_db: float, ramp_ms: int = 300, force: bool = False) -> dict[str, Any]:
    """Change the send from ch to bus by delta_db ("more kick in Tony's ears" = +2); clamps and the
    6 dB relative limit as set_send. Tier 1."""
    desk = _desk()
    t = await desk.resolve(ch)
    res = await asyncio.wait_for(desk.adjust_send(t, int(bus), float(delta_db), ramp_ms=int(ramp_ms), force=bool(force)), timeout=_level_timeout(ramp_ms))
    return _ok(await _send_summary(desk, t, res), **res)


@server.tool()
@_tool()
async def set_eq_band(
    target: str,
    band: int,
    freq_hz: float | None = None,
    gain_db: float | None = None,
    q: float | None = None,
    type: str | None = None,
    on: bool | None = None,
) -> dict[str, Any]:
    """Set EQ band (1..4 on channels, 1..6 on buses/matrices/mains): freq_hz 20..20000, gain_db
    -15..+15 (clamped), q 0.3..10, type LCut/LShv/PEQ/VEQ/HShv/HCut; on switches the whole EQ.
    Give at least one value. Tier 1."""
    desk = _desk()
    t = await desk.resolve(target)
    res = await desk.set_eq_band(t, int(band), freq_hz=freq_hz, gain_db=gain_db, q=q, type=type, on=on)
    name = await _name(desk, t)
    summary = f"{_who(t, name)} EQ band {res['band']}: {_applied_text(res['applied'])}"
    if res.get("clamped"):
        summary += " (gain clamped to ±15 dB)"
    return _ok(summary, name=name, **res)


@server.tool()
@_tool()
async def set_pan(target: str, pan: int) -> dict[str, Any]:
    """Pan a strip: -100 (hard left) .. 0 (centre) .. +100 (hard right). Tier 1."""
    desk = _desk()
    t = await desk.resolve(target)
    res = await desk.set_pan(t, int(pan))
    name = await _name(desk, t)
    return _ok(f"{_who(t, name)} pan {_ARROW} {_pan(res['pan'])}", name=name, **res)


@server.tool()
@_tool()
async def set_comp(
    target: str,
    on: bool | None = None,
    threshold_db: float | None = None,
    ratio: float | None = None,
    attack_ms: float | None = None,
    release_ms: float | None = None,
    knee: int | None = None,
    makeup_db: float | None = None,
    mix_pct: int | None = None,
) -> dict[str, Any]:
    """Compressor of a strip: threshold -60..0 dB, ratio 1.1..100 (snapped to the desk's steps),
    attack 0..120 ms, release 5..4000 ms, knee 0..5, makeup 0..24 dB, mix 0..100 %. Only the
    values given are written. Tier 1."""
    desk = _desk()
    t = await desk.resolve(target)
    res = await desk.set_comp(
        t, on=on, threshold_db=threshold_db, ratio=ratio, attack_ms=attack_ms, release_ms=release_ms,
        knee=knee, makeup_db=makeup_db, mix_pct=mix_pct,
    )
    name = await _name(desk, t)
    return _ok(f"{_who(t, name)} comp: {_applied_text(res['applied'])}", name=name, **res)


@server.tool()
@_tool()
async def set_gate(
    target: str,
    on: bool | None = None,
    threshold_db: float | None = None,
    range_db: float | None = None,
    attack_ms: float | None = None,
    hold_ms: float | None = None,
    release_ms: float | None = None,
) -> dict[str, Any]:
    """Gate of an input channel: threshold -80..0 dB, range 3..60 dB, attack 0..120 ms, hold
    0.02..2000 ms, release 5..4000 ms. Only the values given are written. Tier 1."""
    desk = _desk()
    t = await desk.resolve(target)
    res = await desk.set_gate(t, on=on, threshold_db=threshold_db, range_db=range_db, attack_ms=attack_ms, hold_ms=hold_ms, release_ms=release_ms)
    name = await _name(desk, t)
    return _ok(f"{_who(t, name)} gate: {_applied_text(res['applied'])}", name=name, **res)


@server.tool()
@_tool(timeout=10.0)
async def panic() -> dict[str, Any]:
    """EMERGENCY: mute Main LR, Main M/C, all 16 mix buses and all 6 matrices as fast as possible
    (no ramp, no confirmation, never blocked by show mode or rate limits). Use when the user says
    stop / kill it / mute everything. Unmute afterwards with unmute / set_main_mute. Tier 1."""
    desk = _desk()
    res = await desk.panic()
    return _ok(
        f"PANIC: {res['count']} outputs muted in {res['elapsed_ms']} ms ({res['delivered']})"
        + ("; the desk is degraded — verify on X32-Edit or the front panel" if res.get("delivered") == "unconfirmed" else ""),
        **res,
    )


# -- tools: Tier 2 (confirmation dance) ----------------------------------------------------------------------


@server.tool()
@_tool(timeout=None)
async def set_main_fader(which: str = "st", db: float = -90, ramp_ms: int = 300, confirm_token: str | None = None) -> dict[str, Any]:
    """Main LR (which='st') or Main M/C ('m') fader to db (ceiling 0 dB; -90 = -oo), ramped over
    ramp_ms. TIER 2: the first call returns requires_confirmation + confirm_token; show the
    action_summary to the user and call again with confirm_token only after they agree."""
    desk = _desk()
    w = _main_which(which)
    t = Target("main", w)
    strip = await desk.get_strip(t)
    payload = {"which": w, "db": float(db), "ramp_ms": int(ramp_ms)}
    summary = f"Set {t.label} fader from {_dbs(strip.get('fader'))} to {_db_from_float(float(db))} ({int(ramp_ms)} ms ramp)"
    pending = _confirm("set_main_fader", summary, payload, confirm_token, current=strip.get("fader"))
    if pending:
        return pending
    res = await asyncio.wait_for(desk.set_main_level(w, float(db), ramp_ms=int(ramp_ms)), timeout=_level_timeout(ramp_ms))
    return _ok(_level_summary(_who(t, strip.get("name")), res), name=strip.get("name"), **res)


@server.tool()
@_tool()
async def set_main_mute(which: str, muted: bool, confirm_token: str | None = None) -> dict[str, Any]:
    """Mute (true) or unmute (false) Main LR ('st') or Main M/C ('m'). TIER 2 confirmation dance
    (see set_main_fader). For an emergency use panic() instead."""
    desk = _desk()
    w = _main_which(which)
    t = Target("main", w)
    strip = await desk.get_strip(t)
    payload = {"which": w, "muted": bool(muted)}
    summary = f"{'Mute' if muted else 'Unmute'} {t.label} (currently {'muted' if strip.get('muted') else 'unmuted'})"
    pending = _confirm("set_main_mute", summary, payload, confirm_token)
    if pending:
        return pending
    res = await desk.set_main_mute(w, bool(muted))
    return _ok(f"{_who(t, strip.get('name'))} {'muted' if res['muted'] else 'unmuted'}", name=strip.get("name"), **res)


@server.tool()
@_tool(timeout=45.0)
async def recall_scene(scene: str | int, confirm_token: str | None = None) -> dict[str, Any]:
    """Load a scene by index (0..99) or by name ('The Molecules'); replaces the whole desk state.
    Refused in show mode. TIER 2 confirmation dance: first call → action_summary + confirm_token;
    call again with the token after the user agrees. The pre-recall state is in the auto snapshot."""
    a = _app()
    desk = _desk()
    a.policy.check_show_mode_allows("scene_recall")
    slot = await _resolve_scene(desk, scene)
    if not slot["has_data"]:
        raise DeskError("BAD_ARGUMENT", f"scene slot {slot['index']} is empty", index=slot["index"])
    cur = await desk.current_scene()
    payload = {"index": int(slot["index"])}
    summary = (
        f"Recall scene {slot['index']} '{slot['name']}' (currently loaded: {cur['index']} '{cur['name']}'); "
        "this replaces every setting on the desk"
    )
    pending = _confirm("recall_scene", summary, payload, confirm_token, scene=slot, current=cur)
    if pending:
        return pending
    res = await desk.recall_scene(int(slot["index"]))
    return _ok(
        f"Recalled scene {res['index']} '{res['name']}' ({'verified' if res.get('verified') else 'NOT verified — check the desk'}; previous {res.get('previous_index')})",
        **res,
    )


@server.tool()
@_tool(timeout=45.0)
async def save_scene(index: int, name: str, notes: str = "", confirm_token: str | None = None) -> dict[str, Any]:
    """Save the current desk state into scene slot index (0..99) as name (with notes); overwrites the
    slot. Refused in show mode. TIER 2 confirmation dance (see recall_scene)."""
    a = _app()
    desk = _desk()
    a.policy.check_show_mode_allows("scene_save")
    idx = _int_arg(index, "index", 0, 99)
    text = str(name).strip()
    if not text:
        raise DeskError("BAD_ARGUMENT", "name must not be empty")
    scenes = await desk.list_scenes()
    slot = scenes[idx]
    payload = {"index": idx, "name": text, "notes": str(notes)}
    summary = f"Save the current desk state to scene {idx} as '{text}'" + (
        f" — OVERWRITES the existing scene '{slot['name']}'" if slot["has_data"] else " (slot is empty)"
    )
    pending = _confirm("save_scene", summary, payload, confirm_token, slot=slot)
    if pending:
        return pending
    res = await desk.save_scene(idx, text, str(notes))
    return _ok(f"Saved scene {idx} '{text}'", **res)


# -- tools: snapshots -------------------------------------------------------------------------------------


@server.tool()
@_tool(timeout=DUMP_TIMEOUT_S)
async def snapshot_desk(label: str = "") -> dict[str, Any]:
    """Dump the whole desk to a snapshot file (snapshots/<timestamp>-<label>.json) for backup or
    later diff/restore. Tier 0 (read-only). Takes a few seconds on a real desk."""
    a = _app()
    desk = _desk()
    state = await desk.dump()
    snap = a.snapshots.save(state, str(label or ""))
    a.events.publish("desk.snapshot", id=snap.id, label=snap.label, sections=len(state.sections), missing=len(state.missing))
    return _ok(
        f"Snapshot {snap.id} saved ({len(state.sections)} sections" + (f", {len(state.missing)} missing" if state.missing else "") + ")",
        id=snap.id, label=snap.label, created=snap.created, path=str(snap.path), scene=state.scene,
        sections=len(state.sections), missing=state.missing,
    )


@server.tool()
@_tool()
async def list_snapshots() -> dict[str, Any]:
    """Snapshots on disk, newest first: [{id, label, created, scene, size_kb}]. 'latest' and unique
    id prefixes are accepted by diff_snapshot/restore_snapshot. Tier 0."""
    a = _app()
    metas = a.snapshots.list()
    items = [
        {"id": m.id, "label": m.label, "created": m.created, "scene": m.scene, "size_kb": round(m.size / 1024.0, 1), "path": str(m.path)}
        for m in metas
    ]
    summary = f"{len(items)} snapshot(s)" + (": " + ", ".join(f"{i['id']}" + (f" ({i['label']})" if i["label"] else "") for i in items[:10]) if items else "")
    return _ok(summary, snapshots=items, count=len(items))


async def _diff(desk: Desk, snap: Any, scope: str | None) -> list[Any]:
    live = await desk.dump(sections=[scope] if scope else None)
    return diff_states(snap.state, live, _app().descriptor, scope=scope)


@server.tool()
@_tool(timeout=DUMP_TIMEOUT_S)
async def diff_snapshot(id: str = "latest", scope: str | None = None) -> dict[str, Any]:
    """Plain-English differences between snapshot id ('latest', full id or unique prefix) and the desk
    now, e.g. "Ch 5 'Vox' fader -4.2 dB → -1.0 dB". scope narrows to 'ch.5', 'bus', '/ch/05'.
    Returns count and up to 200 changes. Tier 0."""
    a = _app()
    desk = _desk()
    snap = a.snapshots.load(str(id))
    changes = await _diff(desk, snap, scope)
    shown = changes[:MAX_DIFF_CHANGES]
    text = describe_changes(shown)
    items = [{"address": c.address, "field": c.field, "before": c.before, "after": c.after, "label": c.label, "strip": c.strip} for c in shown]
    head = f"{len(changes)} change(s) since snapshot {snap.id}" + (f" ({snap.label})" if snap.label else "") + (f" in {scope}" if scope else "")
    summary = head + (":\n" + text if changes else " — no changes")
    if len(changes) > len(shown):
        summary += f"\n… {len(changes) - len(shown)} more"
    return _ok(
        summary, snapshot=snap.id, label=snap.label, created=snap.created, scope=scope,
        count=len(changes), changes=items, text=text, truncated=len(changes) > len(shown),
    )


@server.tool()
@_tool(timeout=None)
async def restore_snapshot(id: str, scope: str | None = None, confirm_token: str | None = None) -> dict[str, Any]:
    """Write snapshot id ('latest', full id or unique prefix) back to the desk — the undo. scope
    restricts it ('ch.5', 'bus', '/ch/05'); a full restore of a very different desk can take a
    minute. Allowed in show mode. TIER 2 confirmation dance: the first call previews the changes
    and returns a confirm_token; call again with it after the user agrees."""
    a = _app()
    desk = _desk()
    snap = a.snapshots.load(str(id))
    changes = await asyncio.wait_for(_diff(desk, snap, scope), timeout=DUMP_TIMEOUT_S)
    sections = len({c.section for c in changes})
    preview = describe_changes(changes[:15]) + (f"\n… {len(changes) - 15} more" if len(changes) > 15 else "")
    payload = {"id": snap.id, "scope": scope}
    summary = (
        f"Restore snapshot {snap.id}" + (f" ({snap.label})" if snap.label else "") + f" from {snap.created}"
        + (f", scope {scope}" if scope else " (whole desk)") + f": {len(changes)} setting(s) in {sections} section(s) would change"
        + (":\n" + preview if changes else "")
    )
    pending = _confirm("restore_snapshot", summary, payload, confirm_token, count=len(changes), sections=sections, preview=preview)
    if pending:
        return pending
    res = await asyncio.wait_for(desk.restore(snap, scope=scope), timeout=RESTORE_BASE_TIMEOUT_S + sections / RESTORE_SECTIONS_PER_S)
    done = f"Restored snapshot {res['snapshot']}" + (f" ({res['label']})" if res.get("label") else "") + (f" scope {scope}" if scope else "")
    done += f": {res['written']}/{res['lines']} section(s) written in {res['duration_ms']:.0f} ms"
    if res.get("failed"):
        done += f", {len(res['failed'])} failed"
    if res.get("aborted"):
        done += f" — ABORTED: {res['aborted']}"
    return _ok(done, **res)


@server.tool()
@_tool()
async def show_mode(on: bool) -> dict[str, Any]:
    """Show mode on/off: when on, scene recall/save, setup_ringout_eqs and ring-outs are refused and
    relative moves are limited to ±3 dB (accident protection during a performance). Tier 0."""
    a = _app()
    a.policy.show_mode = bool(on)
    blocked = sorted(SHOW_MODE_BLOCKED) if a.policy.show_mode else []
    summary = (
        f"Show mode ON: relative moves limited to ±{a.policy.relative_limit_db:g} dB; refused: {', '.join(blocked)}"
        if a.policy.show_mode else f"Show mode off: relative moves limited to ±{a.policy.relative_limit_db:g} dB"
    )
    return _ok(summary, show_mode=a.policy.show_mode, relative_limit_db=a.policy.relative_limit_db, blocked=blocked)


# -- tools: labels, patches, config ------------------------------------------------------------------------


@server.tool()
@_tool()
async def label_channel(ch: int, name: str | None = None, color: str | None = None, icon: int | None = None) -> dict[str, Any]:
    """Name (≤ 12 characters, longer is truncated), colour (RD GN YE BL MG CY WH OFF, +i for inverted,
    or red/green/…/'blue inverted') and icon number (1..74) of channel ch. Tier 1."""
    a = _app()
    desk = _desk()
    t = Target("ch", _int_arg(ch, "ch", 1, 32))
    tok: str | None = None
    if color is not None:
        try:
            tok = _patches.normalise_color(color, descriptor=a.descriptor)
        except PatchError as e:
            raise DeskError("BAD_ARGUMENT", str(e)) from None
    res = await desk.label(t, name=name, color=tok, icon=icon)
    ap = res["applied"]
    parts = []
    if "name" in ap:
        parts.append(f"named '{ap['name']}'" + (" (truncated to 12 characters)" if res.get("truncated") else ""))
    if "color" in ap:
        parts.append(f"colour {ap['color']}")
    if "icon" in ap:
        parts.append(f"icon {ap['icon']}")
    return _ok(f"{t.label} " + ", ".join(parts), **res)


@server.tool()
@_tool(timeout=120.0)
async def apply_patch_plan(file: str, include_source: bool = False, confirm_token: str | None = None) -> dict[str, Any]:
    """Apply a patch plan (patches/<file>.yaml or .csv: channel, name, color, source, mic, owner,
    monitor_bus): writes names and colours that differ (Tier 1). include_source=true also patches
    the input sources — TIER 2 confirmation dance (confirm_token) because it changes routing."""
    a = _app()
    desk = _desk()
    path = _patch_path(file)
    plan = _patches.load_patch_plan(path, descriptor=a.descriptor)
    if include_source:
        rows = [r for r in plan.rows if r.source is not None]
        payload = {"file": str(path), "include_source": True}
        summary = (
            f"Apply patch plan {path.name}" + (f" ('{plan.band}')" if plan.band else "") + f": label {len(plan.rows)} channel(s) AND patch "
            f"{len(rows)} input source(s): " + ", ".join(f"ch {r.channel} {_ARROW} {r.source}" for r in rows[:12])
            + (" …" if len(rows) > 12 else "")
        )
        pending = _confirm("apply_patch_plan", summary, payload, confirm_token, file=str(path), band=plan.band, rows=len(plan.rows))
        if pending:
            return pending
    res = await _patches.apply_patch_plan(desk, plan, include_source=bool(include_source))
    return _ok(res["summary"], warnings=list(plan.warnings), **res)


@server.tool()
@_tool(timeout=DUMP_TIMEOUT_S)
async def export_patch_plan(file: str) -> dict[str, Any]:
    """Write the desk's channel names, colours and sources to a patch plan file (.yaml or .csv; a
    bare name goes under patches/). If the file exists, its mic/owner/monitor_bus/notes/band/venue
    are kept. Tier 0."""
    a = _app()
    desk = _desk()
    path = _patch_path(file, must_exist=False)
    merge = _patches.load_patch_plan(path, descriptor=a.descriptor) if path.is_file() else None
    plan = await _patches.export_patch_plan(desk, path, merge_with=merge)
    summary = f"Exported {len(plan.rows)} channel(s) to {plan.source_file}" + (
        f" (merged mic/owner/monitor_bus metadata from the existing file" + (f", band '{plan.band}'" if plan.band else "") + ")" if merge else ""
    )
    return _ok(summary, file=str(plan.source_file), rows=len(plan.rows), band=plan.band, venue=plan.venue, merged=merge is not None,
               warnings=list(plan.warnings), channels=[r.to_dict() for r in plan.rows])


def _link_pair(ch: int) -> tuple[int, int]:
    return (ch, ch + 1) if ch % 2 else (ch - 1, ch)


@server.tool()
@_tool()
async def set_channel_config(ch: int, source: str | None = None, link: bool | None = None, confirm_token: str | None = None) -> dict[str, Any]:
    """Routing of channel ch: source = input token ('IN05', 'in 5', 'AUX1', 'USBL', 'FX1L', 'BUS03',
    'OFF'); link = stereo-link the channel pair (1-2, 3-4 …). TIER 2 confirmation dance
    (confirm_token) — this changes what the channel listens to."""
    a = _app()
    desk = _desk()
    n = _int_arg(ch, "ch", 1, 32)
    t = Target("ch", n)
    if source is None and link is None:
        raise DeskError("BAD_ARGUMENT", "give source and/or link")
    tok: str | None = None
    if source is not None:
        try:
            tok = _patches.normalise_source(source, descriptor=a.descriptor)
        except PatchError as e:
            raise DeskError("BAD_ARGUMENT", str(e)) from None
        if tok is None:
            raise DeskError("BAD_ARGUMENT", "source must not be blank")
    strip = await desk.get_strip(t)
    pair = _link_pair(n)
    parts = []
    if tok is not None:
        parts.append(f"input source {strip.get('source')} {_ARROW} {tok}")
    if link is not None:
        parts.append(f"stereo link Ch {pair[0]}-{pair[1]} {'ON' if link else 'OFF'}")
    payload = {"ch": n, "source": tok, "link": None if link is None else bool(link)}
    summary = f"{_who(t, strip.get('name'))}: " + ", ".join(parts)
    pending = _confirm("set_channel_config", summary, payload, confirm_token)
    if pending:
        return pending
    applied: dict[str, Any] = {}
    if tok is not None:
        applied["source"] = (await desk.set_source(t, tok))["source"]
    if link is not None:
        spec = a.descriptor.param("config", f"chlink/{pair[0]}-{pair[1]}")
        address = spec.address()
        await desk.ensure_pre_write_snapshot()
        await a.policy.acquire_write()
        try:
            await a.conn.set(address, spec.to_raw(bool(link)))
        except NotConnected as e:
            raise DeskError("NOT_CONNECTED", str(e)) from None
        a.events.publish("desk.write", address=address, value=bool(link), tier=2, tool="set_channel_config", target=t.key)
        applied["link"] = bool(link)
        applied["link_pair"] = f"{pair[0]}-{pair[1]}"
    return _ok(f"{_who(t, strip.get('name'))}: {_applied_text(applied)}", target=t.key, label=t.label, applied=applied)


# -- tools: meters ----------------------------------------------------------------------------------------


_METER_FAMILY = {"channels": "ch", "auxins": "auxin", "fxrtns": "fxrtn", "buses": "bus", "matrices": "mtx"}
_MAIN_METER_ITEMS = (("main.st", "Main L"), ("main.st", "Main R"), ("main.m", "Main M/C"))


@server.tool()
@_tool(timeout=None)
async def get_meters(type: str = "channels", duration_ms: int = 500) -> dict[str, Any]:
    """Signal levels (dBFS, averaged over duration_ms 50..10000) of a group: 'channels' (32),
    'buses' (16), 'main' (L, R, M/C), 'auxins', 'fxrtns', 'matrices'. Items [{index, target, name, db}];
    -90 = silence. Tier 0."""
    a = _app()
    desk = _desk()
    key = str(type).strip().lower()
    if key not in METER_GROUPS:
        raise DeskError("BAD_ARGUMENT", f"type must be one of {', '.join(METER_GROUPS)}, got {type!r}")
    ms = min(max(int(duration_ms), 50), 10_000)
    mtype, first, count = METER_GROUPS[key]
    n = max(1, round(ms / (FRAME_PERIOD_S * 1000)))
    src = LiveMeters(a.conn, mtype)
    await src.start()
    try:
        frame = await average_frames(src, n, timeout_s=ms / 1000.0 + 3.0)
    finally:
        await src.stop()
    if frame is None:
        raise DeskError("TIMEOUT", f"no /meters/{mtype} frames arrived within {ms / 1000.0 + 3.0:g} s")
    dbs = frame.db()[first:first + count]
    items: list[dict[str, Any]] = []
    if key == "main":
        for (tk, label), v in zip(_MAIN_METER_ITEMS, dbs):
            items.append({"index": len(items) + 1, "target": tk, "name": label, "db": round(v, 1)})
    else:
        fam = _METER_FAMILY[key]
        try:
            names = await desk.get_names(fam)
        except Exception:
            names = {}
        for i, v in enumerate(dbs, start=1):
            items.append({"index": i, "target": f"{fam}.{i}", "name": names.get(i) or "", "db": round(v, 1)})
    loud = sorted(items, key=lambda it: it["db"], reverse=True)[:6]
    summary = f"{key} levels over {ms} ms ({len(items)} meters): loudest " + ", ".join(
        f"{it['target']}{' ' + repr(it['name']) if it['name'] else ''} {_db_from_float(it['db'])}" for it in loud
    )
    return _ok(summary, type=key, meter_type=mtype, duration_ms=ms, frames=n, items=items)


@server.tool()
@_tool(timeout=None)
async def get_rta(target: str | None = None, frames: int = 10) -> dict[str, Any]:
    """The console's 100-band RTA (dB per band, 20 Hz..20 kHz) averaged over frames (1..200, 50 ms
    each). target points the RTA at a strip first ('bus.3', 'ch.5', 'main.st'); while a CFS2
    session runs the RTA stays on its bus. Returns band_hz, db and the loudest peaks. Tier 0."""
    a = _app()
    desk = _desk()
    n = min(max(int(frames), 1), 200)
    t: Target | None = None
    rta_info: dict[str, Any] | None = None
    if a.cfs.active:
        if target is not None:
            t = await desk.resolve(target)
            if t.key != a.cfs.state.rta_source:
                raise CfsError("BUSY", f"CFS² is {a.cfs.mode.value} on {a.cfs.state.rta_source}; the RTA cannot be moved to {t.key} until it stops")
        src, own = a.cfs.frames, False
        source_key = a.cfs.state.rta_source
    else:
        if target is not None:
            t = await desk.resolve(target)
            r = await set_rta_source(a.conn, a.descriptor, t)
            rta_info = {"source_index": r.source_index, "post_eq": r.post_eq, "verified": r.verified, "stat_actual": r.stat_actual}
        src, own = LiveMeters(a.conn, int(a.descriptor.rta.get("meter_type", 15))), True
        source_key = t.key if t else None
    if own:
        await src.start()
    try:
        frame = await average_frames(src, n, timeout_s=n * FRAME_PERIOD_S + 3.0)
    finally:
        if own:
            await src.stop()
    if frame is None:
        raise DeskError("TIMEOUT", f"no RTA frames arrived within {n * FRAME_PERIOD_S + 3.0:g} s")
    band_hz = rta_band_hz(a.descriptor)
    vals = [round(v, 1) for v in frame.values]
    peaks = sorted(({"band": i, "freq_hz": round(band_hz[i], 1), "db": vals[i]} for i in range(min(len(vals), len(band_hz)))), key=lambda p: p["db"], reverse=True)[:5]
    summary = f"RTA{(' on ' + source_key) if source_key else ''} over {n} frame(s): peaks " + ", ".join(
        f"{_hz(p['freq_hz'])} {_db_from_float(p['db'])}" for p in peaks
    )
    return _ok(summary, rta_source=source_key, frames=n, band_hz=list(band_hz), db=vals, peaks=peaks, rta=rta_info)


# -- tools: CFS² ------------------------------------------------------------------------------------------


def _geq_status_summary(status: dict[Any, Any]) -> str:
    parts = []
    for k, s in status.items():
        label = "Main LR" if k == "main" else f"Bus {k}"
        if s.ok:
            ins = s.insert
            detail = f"{ins.sel} {ins.fx_type}" if ins else "GEQ"
            cuts = f", {len(s.notches)} cut(s)" if s.notches else ", flat"
            parts.append(f"{label}: OK ({detail}{cuts}" + (f", matches report {s.matched_session}" if s.matched_session else "") + ")")
        else:
            parts.append(f"{label}: NOT OK — " + "; ".join(s.reasons))
    return "; ".join(parts)


@server.tool()
@_tool()
async def validate_ringout_eqs(buses: list[int | str]) -> dict[str, Any]:
    """Check (read-only) that each bus (1..16 or 'main') has a usable ring-out GEQ: an FX insert
    holding a GEQ, switched on, not shared with another strip. Returns per-bus {ok, reasons,
    insert, bands_db, notches, matched_session}. Run before feedback_watch/ring_out. Tier 0."""
    a = _app()
    desk = _desk()
    if not buses:
        raise DeskError("BAD_ARGUMENT", "give at least one bus (1..16 or 'main')")
    status = await _prov.validate_ringout_eqs(desk, list(buses), a.reports)
    all_ok = all(s.ok for s in status.values())
    return _ok(_geq_status_summary(status), all_ok=all_ok, buses={str(k): s.to_dict() for k, s in status.items()})


def _plan_summary(plan: Any) -> str:
    parts = []
    for k, ins in plan.reuse.items():
        parts.append(f"keep {ins.sel} on {'Main LR' if k == 'main' else f'Bus {k}'}" + ("" if ins.on else " (switch it on)"))
    for tl in plan.type_loads:
        parts.append(f"load {tl['fx_type']} into FX slot {tl['slot']} (currently {tl.get('current_type')})")
    for ins in plan.inserts:
        bits = [f"insert {ins['sel']}" if ins.get("sel") else "insert", f"on {ins['target']}"]
        if ins.get("pos"):
            bits.append(ins["pos"])
        parts.append(" ".join(bits))
    return "; ".join(parts) if parts else "nothing to change"


@server.tool()
@_tool(timeout=120.0)
async def setup_ringout_eqs(buses: list[int | str], confirm_token: str | None = None) -> dict[str, Any]:
    """Provision ring-out GEQs for buses (1..16 or 'main'): loads a dual-mono GEQ2 into free FX
    insert slots (5..8, one slot serves two buses) and inserts it on each bus. Idempotent; refused
    in show mode. TIER 2 confirmation dance when anything must change (FX types / inserts)."""
    a = _app()
    desk = _desk()
    if not buses:
        raise DeskError("BAD_ARGUMENT", "give at least one bus (1..16 or 'main')")
    a.policy.check_show_mode_allows("setup_ringout_eqs")
    plan = await _prov.plan_setup(desk, list(buses))
    if plan.blockers:
        raise DeskError("NOT_SUPPORTED", "cannot set up ring-out GEQs: " + "; ".join(plan.blockers), blockers=list(plan.blockers), plan=plan.to_dict())
    labels = ", ".join("Main LR" if b == "main" else f"Bus {b}" for b in plan.buses)
    if plan.needs_tier2:
        payload = {"buses": [str(b) for b in plan.buses], "type_loads": plan.type_loads, "inserts": plan.inserts}
        summary = f"Set up ring-out GEQs on {labels}: {_plan_summary(plan)} (Tier 2: FX types and inserts)"
        pending = _confirm("setup_ringout_eqs", summary, payload, confirm_token, plan=plan.to_dict())
        if pending:
            return pending
    res = await _prov.apply_setup(desk, plan)
    geq = {k: v for k, v in res.get("geq", {}).items()}
    ok = bool(res.get("ok", True))
    summary = (
        f"Ring-out GEQs on {labels}: {res.get('changed', 0)} write(s)" + (" (already set up)" if not res.get("changed") else "")
        + ("; all validate" if ok else "; VALIDATION FAILED — " + "; ".join(f"{k}: {'; '.join(v.get('reasons', []))}" for k, v in geq.items() if not v.get("ok")))
    )
    out = _ok(summary, plan=plan.to_dict(), **res)
    out["ok"] = ok
    return out


@server.tool()
@_tool()
async def discover_mics(bus: int | str, patch_file: str | None = None) -> dict[str, Any]:
    """Which input channels feed bus (1..16 or 'main') and look like live stage mics: unmuted, send
    above the floor, physical preamp source; cross-checked with the patch plan (mic/owner/
    monitor_bus) and mute group 6. 'include' is a suggestion — confirm it with the user; nothing is
    unmuted. Tier 0."""
    a = _app()
    desk = _desk()
    t = _bus_target(bus)
    patch = _load_patch(patch_file)
    mics = await _prov.discover_mics(desk, t, patch=patch)
    inc = [m for m in mics if m.include]
    exc = [m for m in mics if not m.include]
    summary = f"{t.label}: {len(mics)} candidate(s), {len(inc)} included: " + ", ".join(
        f"Ch {m.ch} '{m.name}'" + (f" ({m.owner})" if m.owner else "") + f" {_db_from_float(m.send_db)}" for m in inc
    )
    if exc:
        summary += "; excluded: " + ", ".join(f"Ch {m.ch} '{m.name}' ({'; '.join(m.notes) or 'no indicator'})" for m in exc)
    return _ok(summary, bus=_prov.bus_label(t), target=t.key, patch_file=patch_file, mics=[m.to_dict() for m in mics], included=[m.ch for m in inc])


def _watch_summary(res: dict[str, Any], verb: str) -> str:
    pf = res.get("preflight") or {}
    geq = res.get("geq") or {}
    rta = res.get("rta") or {}
    mics = [m for m in pf.get("mics", []) if m.get("include")]
    s = (
        f"{verb} on Bus {res.get('bus')} '{res.get('bus_name')}': GEQ {geq.get('sel')} (FX {geq.get('fx_slot')}{geq.get('side')}), "
        f"master {_dbs(pf.get('master'))}, RTA {'verified' if rta.get('verified') else 'NOT verified'}; open mics: "
        + (", ".join(f"Ch {m['ch']} '{m['name']}'" for m in mics) or "none")
    )
    if res.get("bus") == "main":
        s = s.replace("Bus main", "Main LR")
    if geq.get("existing_cuts"):
        s += f"; GEQ already carries {len(geq['existing_cuts'])} cut(s)"
    if pf.get("warnings"):
        s += "; warnings: " + "; ".join(pf["warnings"])
    return s


@server.tool()
@_tool()
async def feedback_watch(bus: int | str, notch_budget: int = 6, patch_file: str | None = None) -> dict[str, Any]:
    """Arm the feedback detector on bus (1..16 or 'main'): the operator raises the gain by hand and
    the server cuts up to notch_budget (1..12) GEQ notches (-3 dB steps, max -9 dB) the moment a
    ring is detected. Preflight refuses if the GEQ is missing, the bus is muted or no mic feeds it.
    Stop with feedback_watch_stop. Tier 1."""
    a = _app()
    _desk()
    budget = _int_arg(notch_budget, "notch_budget", 1, 12)
    patch = _load_patch(patch_file)
    res = await a.cfs.feedback_watch(_bus_target(bus), notch_budget=budget, patch=patch)
    return _ok(_watch_summary(res, "Feedback watch armed") + f"; budget {budget} notch(es)", patch_file=patch_file, **res)


@server.tool()
@_tool(timeout=45.0)
async def feedback_watch_stop() -> dict[str, Any]:
    """Disarm the detector / abort a running ring-out (it backs the master off first) and save the
    report. Safe to call when nothing runs. Tier 1."""
    a = _app()
    res = await a.cfs.stop()
    if not res.get("stopped"):
        return _ok("Nothing to stop: CFS² is idle", **res)
    rep = res.get("report") or {}
    notches = rep.get("notches") or []
    summary = f"Stopped CFS² {res.get('mode')} session {res.get('session_id')}: {len(notches)} notch(es)"
    if notches:
        summary += " (" + ", ".join(f"{_hz(n.get('freq_hz'))} {_db_from_float(n.get('depth_db'))}" for n in notches) + ")"
    if rep.get("summary"):
        summary += f"; {rep['summary']}"
    if res.get("path"):
        summary += f"; report {res['path']}"
    out = dict(res)
    if isinstance(out.get("report"), dict):
        out["report"] = _trim_report(out["report"])
    return _ok(summary, **out)


@server.tool()
@_tool()
async def cfs_status() -> dict[str, Any]:
    """CFS2 state: mode (idle/watch/ringout/system), session, bus, master_db, notch budget left, current
    candidate, stage, notches so far. Tier 0."""
    a = _app()
    st = a.cfs.state.to_dict()
    frames = a.cfs.frames
    src = {"source": type(frames).__name__, "running": bool(getattr(frames, "running", False))}
    if st["mode"] == "idle":
        summary = "CFS² idle" + (
            f" (last session {st['session_id']} on bus {st['bus']}: {st.get('stage')}, {len(st.get('notches') or [])} notch(es))" if st.get("session_id") else ""
        )
    else:
        cand = st.get("candidate")
        summary = (
            f"CFS² {st['mode']} on {'Main LR' if st.get('bus') == 'main' else 'Bus ' + str(st.get('bus'))} '{st.get('bus_name')}': master {_db_from_float(st.get('master_db'))}, "
            f"budget left {st.get('budget_left')}, stage {st.get('stage')}, {len(st.get('notches') or [])} notch(es)"
            + (f", candidate {_hz(cand.get('freq_hz'))} confidence {cand.get('confidence'):.2f}" if cand else "")
        )
    return _ok(summary, frames=src, **st)


def _ring_out_timeout(start_db: float | None, target_db: float, step_db: float, dwell_ms: int, budget: int) -> float:
    a = _app()
    start = start_db if isinstance(start_db, (int, float)) and math.isfinite(start_db) else -90.0
    steps = max(1, math.ceil(max(0.0, target_db - start) / max(step_db, 0.1)))
    verify_s = float(a.descriptor.detector.get("decay_verify_s", 1.5))
    return RING_OUT_BASE_TIMEOUT_S + steps * (dwell_ms / 1000.0 + 1.5) + budget * (verify_s + 3.0)


@server.tool()
@_tool(timeout=None)
async def ring_out(
    bus: int | str,
    target_gain_db: float | None = None,
    step_db: float = 1.0,
    dwell_ms: int = 1500,
    notch_budget: int = 6,
    patch_file: str | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Automatic ring-out of bus (1..16 or 'main'): snapshot, then raise the bus master in step_db
    steps (dwell_ms each) up to target_gain_db (default and hard ceiling 0 dB), cutting up to
    notch_budget GEQ notches as rings appear; stops at the target or the budget, backs off 3 dB and
    writes a report. Nobody may play during the run. Refused in show mode. TIER 2 confirmation
    dance: the first call runs preflight and returns the plan + confirm_token; call again with it
    after the user has confirmed the open-mic list. Runs for up to several minutes."""
    a = _app()
    desk = _desk()
    a.policy.check_show_mode_allows("ring_out")
    t = _bus_target(bus)
    step = float(step_db)
    if not step > 0:
        raise DeskError("BAD_ARGUMENT", f"step_db must be > 0, got {step_db!r}")
    dwell = _int_arg(dwell_ms, "dwell_ms", 0, 60_000)
    budget = _int_arg(notch_budget, "notch_budget", 1, 12)
    patch = _load_patch(patch_file)
    pf = await _prov.preflight(desk, t, patch=patch, reports=a.reports)
    if not pf.ok:
        raise CfsError("PREFLIGHT_FAILED", f"cannot ring out {t.label}: " + "; ".join(pf.blockers), preflight=pf.to_dict())
    ceiling = min(a.policy.level_ceiling_db(t), float(a.descriptor.ringout.get("master_ceiling_db", 0.0)))
    target = ceiling if target_gain_db is None else min(float(target_gain_db), ceiling)
    payload = {
        "bus": _prov.bus_label(t), "target_gain_db": None if target_gain_db is None else float(target_gain_db),
        "step_db": step, "dwell_ms": dwell, "notch_budget": budget, "patch_file": patch_file,
    }
    ins = pf.geq.insert
    summary = (
        f"Ring out {t.label} '{pf.bus_name}': raise its master from {_dbs(pf.master)} to {_db_from_float(target)} in {step:g} dB steps "
        f"(dwell {dwell} ms), cutting up to {budget} notch(es) on GEQ {ins.sel if ins else '?'}; open mics: "
        + (", ".join(f"Ch {m.ch} '{m.name}'" + (f" ({m.owner})" if m.owner else "") for m in pf.included_mics) or "none")
        + (". Warnings: " + "; ".join(pf.warnings) if pf.warnings else "")
        + ". Nobody may play or sing during the run."
    )
    pending = _confirm("ring_out", summary, payload, confirm_token, preflight=pf.to_dict(), target_db=target)
    if pending:
        return pending
    timeout = _ring_out_timeout(pf.master_db, target, step, dwell, budget)
    rep = await asyncio.wait_for(
        a.cfs.ring_out(t, target_gain_db=target_gain_db, step_db=step, dwell_ms=dwell, notch_budget=budget, patch=patch),
        timeout=timeout,
    )
    return _ok(_report_summary(rep), **_trim_report(rep))


@server.tool()
@_tool(timeout=None)
async def ring_out_system(plan: dict[str, Any] | None = None, confirm_token: str | None = None) -> dict[str, Any]:
    """Ring out several buses in one confirmed run: plan = {"stages": [{"bus": 3, "target_gain_db": -6,
    "mics": [1, 2], "step_db"?, "dwell_ms"?, "notch_budget"?, "patch_file"?}, …, {"bus": "main"}]};
    without a plan every bus (then Main LR) whose GEQ validates is rung out with defaults. One
    consolidated report; a stage failing preflight is skipped. Refused in show mode. TIER 2
    confirmation dance. Can run for many minutes."""
    a = _app()
    desk = _desk()
    a.policy.check_show_mode_allows("ring_out_system")
    stages: list[dict[str, Any]]
    if plan is None:
        status = await _prov.validate_ringout_eqs(desk, list(range(1, 17)) + ["main"], a.reports)
        stages = [{"bus": k} for k, s in status.items() if s.ok]
        if not stages:
            raise CfsError("NO_STAGES", "nothing to ring out: no bus (or Main LR) has a validated ring-out GEQ; run setup_ringout_eqs first")
        cfs_plan: dict[str, Any] | None = None
    else:
        raw = plan.get("stages") if isinstance(plan, dict) else None
        if not isinstance(raw, list) or not raw:
            raise DeskError("BAD_ARGUMENT", "plan must be {'stages': [{'bus': 3, ...}, ...]}")
        stages = []
        for st in raw:
            if not isinstance(st, dict) or "bus" not in st:
                raise DeskError("BAD_ARGUMENT", f"each stage needs a 'bus', got {st!r}")
            item = dict(st)
            _bus_target(item["bus"])  # validates the spelling early
            if item.get("patch_file"):
                item["patch"] = _load_patch(str(item.pop("patch_file")))
            stages.append(item)
        cfs_plan = {"stages": stages}
    labels = ", ".join(_bus_target(s["bus"]).label for s in stages)
    payload = {"plan": _jsonable(plan)}
    summary = (
        f"System ring-out of {len(stages)} stage(s) in order: {labels} — each bus master is raised to its target "
        "(default 0 dB) with automatic GEQ notches, then backed off 3 dB; one confirmation covers the whole run. "
        "Nobody may play or sing during the run."
    )
    pending = _confirm("ring_out_system", summary, payload, confirm_token, stages=_jsonable([{k: v for k, v in s.items() if k != "patch"} for s in stages]))
    if pending:
        return pending
    rep = await asyncio.wait_for(a.cfs.ring_out_system(cfs_plan), timeout=SYSTEM_STAGE_TIMEOUT_S * len(stages))
    out = dict(rep)
    out["stages"] = [_trim_report(s) if isinstance(s, dict) else s for s in rep.get("stages", [])]
    return _ok(_report_summary(rep), **out)


@server.tool()
@_tool()
async def list_ringout_reports(bus: int | str | None = None) -> dict[str, Any]:
    """Saved CFS2 reports (ring-outs, watches, system runs), newest first: [{session_id, mode, bus,
    bus_name, started, ended, notches, final_stage, aborted, path}]; bus filters. Tier 0."""
    a = _app()
    key: int | str | None = None
    if bus is not None:
        key = _prov.bus_label(_bus_target(bus))
    items = a.reports.list(key)
    summary = f"{len(items)} report(s)" + (f" for {_bus_target(bus).label}" if bus is not None else "") + (
        ": " + ", ".join(f"{r['session_id']} ({r['mode']}, {r['notches']} notch(es), {r['final_stage']})" for r in items[:8]) if items else ""
    )
    return _ok(summary, reports=items, count=len(items))


@server.tool()
@_tool()
async def get_ringout_report(id: str) -> dict[str, Any]:
    """One CFS2 report by session id (or a unique prefix): levels, notches [{band, freq_hz, depth_db}],
    stages, detections, plus a Markdown rendering. Tier 0."""
    a = _app()
    rep = a.reports.load(str(id))
    return _ok(_report_summary(rep), markdown=ReportStore.markdown(rep), **rep)


@server.tool()
@_tool()
async def dashboard_status() -> dict[str, Any]:
    """The read-only operator dashboard (live RTA, waterfall, CFS2 state, event log): whether it runs,
    its URL, connected browsers and frame rate. It never changes the desk. Tier 0."""
    a = _app()
    d = a.dashboard
    health = d.health()
    health.pop("ok", None)
    if d.running:
        summary = f"Dashboard running at {d.url} ({health.get('clients')} client(s), {health.get('fps')} fps, source {health.get('source')}, CFS² {health.get('mode')})"
    elif a.settings.dash_enabled:
        summary = f"Dashboard enabled but not running (port {d.port} could not be bound?)"
    else:
        summary = "Dashboard disabled (X32MCP_DASH=0)"
    return _ok(summary, enabled=a.settings.dash_enabled, url=d.url if d.running else None, host=d.host, port=d.port, **health)


# -- resources ---------------------------------------------------------------------------------------------


@server.resource("x32://device", mime_type="text/yaml")
async def device_resource() -> str:
    """device.yaml — the descriptor: scales, enums, strips, parameters, policy, CFS2 thresholds."""
    settings = app.settings if app is not None else Settings.from_env()
    return settings.device_yaml.read_text(encoding="utf-8")


@server.resource("x32://snapshot/latest", mime_type="application/json")
async def latest_snapshot_resource() -> str:
    """The newest desk snapshot file (JSON), or {"error": ...} when none exists."""
    a = _app()
    snap = a.snapshots.latest()
    if snap is None:
        return json.dumps({"error": f"no snapshots in {a.snapshots.dir}"})
    return snap.path.read_text(encoding="utf-8")


@server.resource("x32://cfs/state", mime_type="application/json")
async def cfs_state_resource() -> str:
    """Current CFS2 state (mode, session, bus, master, budget, candidate, notches, stage) as JSON."""
    a = _app()
    return json.dumps(_jsonable(a.cfs.state.to_dict()))


@server.resource("x32://patches/{name}", mime_type="text/plain")
async def patch_resource(name: str) -> str:
    """A patch plan file from patches/ (name with or without .yaml/.yml/.csv)."""
    a = _app()
    base = Path(name).name  # never leave patch_dir
    root = a.settings.patch_dir
    cands = [root / base] if Path(base).suffix else [root / base] + [(root / base).with_suffix(s) for s in (".yaml", ".yml", ".csv")]
    for c in cands:
        if c.is_file():
            return c.read_text(encoding="utf-8")
    raise ResourceNotFoundError(f"no patch plan {name!r} in {root}")


# -- entry point ---------------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> None:
    """Console entry point: logging to stderr (``X32MCP_LOG``), then the stdio transport.
    stdout is the MCP wire — nothing else may write to it."""
    global app
    settings = Settings.from_env()
    level = getattr(logging, settings.log_level, None)
    logging.basicConfig(
        stream=sys.stderr, level=level if isinstance(level, int) else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True,
    )
    app = App(settings)  # descriptor problems surface here, before the transport starts
    try:
        server.run("stdio")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
