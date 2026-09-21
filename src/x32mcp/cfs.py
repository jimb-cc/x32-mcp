"""CFS² session manager (DESIGN.md §15): ``feedback_watch`` / ``ring_out`` / ``ring_out_system``.

One :class:`CfsManager` per server. It owns at most one *session* at a time: an RTA frame
source (:class:`~x32mcp.meters.LiveMeters` on ``/meters/15`` by default, or anything injected
through ``frames``), a :class:`~x32mcp.detector.FeedbackDetector` fed from it, and a
:class:`~x32mcp.detector.NotchController` whose cuts are written through
:meth:`~x32mcp.desk.Desk.set_geq_band` by a small :class:`GeqWriter` adapter. The detector runs
here, in the server — never through the model (BRIEF §5).

Wire facts: the RTA is pointed at the bus with :func:`~x32mcp.meters.set_rta_source`
(meters.md §5.3); GEQ sides follow fx_routing_scenes.md §2 (``FXnL`` = side A = par 1–31,
``FXnR`` = side B = par 33–63). RTA band numbers are 0-based, GEQ bands 1-based (detector.py).

Decisions where DESIGN.md is silent:

* **Bus spelling**: ``bus`` is a number 1..16, or ``"main"`` for Main LR (``ring_out_system``'s
  last stage); reported as such in state/reports. ``Notch.bus`` (an int by contract) carries
  the bus number, ``0`` for Main LR. The Main LR master is moved with ``set_main_level`` (the
  server already did the Tier-2 confirmation for ``ring_out``).
* **Ring-out loop**: the master is raised with single-step writes (``ramp_ms=0``) through
  ``Desk.set_level`` — policy clamps, snapshot-before-write and the rate limiter all apply —
  never above ``min(policy ceiling, ringout.master_ceiling_db, target_gain_db)``. A detection
  during the dwell → HOLD → NOTCH (controller + writer) → VERIFY: the notched RTA band
  (±``band_tolerance``) must drop ≥ ``decay_verify_db`` below its level at the notch, on
  ``decay_verify_frames`` *consecutive* frames, within ``decay_verify_s`` (a single dipping
  frame is noise, not a tamed ring); else the same notch is deepened and verified again; when it
  cannot deepen (−9 dB reached or budget spent) the session ABORTs and backs the master off
  ``abort_backoff_db``. Re-detections of the notched band that queued up during VERIFY are
  dropped (they pre-date the cut). DONE backs off ``safety_margin_db`` from the highest level
  reached — but only when the machine actually raised the master: a run that never raised (target
  already reached, zero budget) leaves the fader exactly where the operator left it.
  ``stop()``/``abort()`` during a ring-out take the abort path (back off, report).
* **Ring-out arguments**: ``step_db`` must be ≥ 0.1 dB (the 0.1 dB fader report grid: a smaller
  step reads back unchanged and looks like a clamp) and ≤ ``policy.relative_limit_db`` (a bigger
  one is refused by the relative clamp on the first raise); a ``target_gain_db`` at or below the
  current master is refused (``BAD_ARGUMENT``) rather than silently backing the fader off.
  Every *lowering* master write (back-off, restore) is ``force``d: dropping a level is always
  safe and the run already carried its Tier-2 confirmation, so the relative clamp (±3 dB in show
  mode) must not be able to leave a bus hot.
* **Refusals while raising**: a ``PolicyError`` (rate limit, relative clamp) is a *local* refusal
  — it aborts the run through the normal BACKOFF path. Only a transport failure
  (``NOT_CONNECTED``/timeout, :class:`~x32mcp.connection.ConnectionError`) counts as a lost
  connection and takes the emergency restore path. Any other exception out of the loop still
  backs the master off and finishes the session (nothing may leave the manager armed).
* **Connection loss** (``connection.state`` = degraded, or a write/read failure while raising):
  the starting master is restored with *raw* fire-and-forget ``conn.send_raw`` writes every
  0.5 s, each followed by a read-back, until the desk confirms it or 10 s pass (this bypasses
  policy and the rate limiter like ``panic()`` — it is the emergency path), then ABORT. A
  watch session simply stops with the reason recorded.
* **Arming** takes the session's pre-write snapshot (``Desk.ensure_pre_write_snapshot``) up front:
  nothing else on the arming path writes through the Desk, so without it the *first* notch would
  pay for the full desk dump — BRIEF §5 budgets < 100 ms detect→cut.
* **Watch mode** notches on every detection (a ringing band is re-emitted once per
  ``cooldown_s`` by the detector, which is how a cut deepens) with no VERIFY stage — the human
  drives the gain.
* **Events**: ``cfs.state`` (session open/close), ``cfs.stage`` {session_id, bus, stage,
  master_db, …}, ``cfs.candidate`` (best current candidate, ≤ ``candidate_rate_hz`` = 5/s),
  ``cfs.notch`` {session_id, bus, band, freq_hz, depth_db, detections, confidence, ts, fx_slot,
  side, rta_band}, ``cfs.abort`` {session_id, bus, reason}, ``cfs.restore`` {session_id, bus,
  master_db, restored, attempts}, ``cfs.report`` {session_id, bus, path}.
* **Reports** (:class:`ReportStore`): ``<session_id>.json`` + ``<session_id>.md`` in
  ``settings.report_dir``; a system run writes one consolidated report holding the per-bus
  reports. ``session_id`` = ``YYYYMMDD-HHMMSS-<mode>-bus03`` (``-main``, ``-system``).
* Extras beyond DESIGN: ``detector_cfg`` (override the descriptor's thresholds, e.g. for
  tests), ``snapshots`` (a :class:`~x32mcp.nodes.SnapshotStore`; when given, the SNAPSHOT stage
  saves the bus's sections as ``ringout-pre-<bus>``), ``CfsState.rta_source``, ``close()``,
  ``ReportStore.load``/``exists``/``markdown``, :class:`CfsError` (``code`` + ``to_dict()``:
  ``PREFLIGHT_FAILED`` (details.preflight), ``BUSY``, ``BAD_ARGUMENT``, ``NOT_CONNECTED``,
  ``NO_STAGES``).
* The frame source (default or injected) is started on arm and stopped on disarm.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Sequence

from .connection import ConnectionError as X32ConnectionError, ConnectionState, NotConnected
from .desk import Desk, DeskError
from .detector import Detection, DetectorConfig, FeedbackDetector, Notch, NotchController
from .events import Event, EventBus
from .meters import FrameSource, LiveMeters, MeterFrame, RtaSourceError, RtaSourceResult, rta_band_hz, set_rta_source, restore_rta_prefs
from .policy import FADER_FLOOR_DB, Policy, PolicyError
from .provision import Preflight, bus_label, bus_target, preflight, validate_ringout_eqs
from .scales import NEG_INF_DB, format_db
from .targets import Target

__all__ = ["CfsError", "CfsMode", "CfsState", "STAGES", "ReportStore", "CfsManager"]

log = logging.getLogger(__name__)

_FLOOR_FIRST_FRAME_S = 0.3  # no RTA frame within this: the source is idle, skip calibration

STAGES: tuple[str, ...] = ("PREFLIGHT", "SNAPSHOT", "ARM", "RAISE", "HOLD", "NOTCH", "VERIFY", "BACKOFF", "DONE", "ABORT")

_RESTORE_RETRY_S = 0.5
_RESTORE_DEADLINE_S = 10.0
_RESTORE_READ_TIMEOUT_S = 1.0
_STOP_WAIT_S = 30.0
_MAX_DETECTIONS_IN_REPORT = 200
_FADER_GRID = 1.0 / 1023  # one fader step (scales_params.md §2.3)
_REPORT_GRID_DB = 0.05  # half the 0.1 dB grid Desk reports levels on (desk.py ``_db1``)
_MIN_STEP_DB = 0.1  # a smaller ring-out step cannot be seen in the read-back at all


class CfsError(Exception):
    """A refused or failed CFS² request; ``code`` is the machine token for the tool envelope."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details
        self._floor_db: float | None = None
        self._gate_db: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        d.update(self.details)
        return d

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class CfsMode(str, Enum):
    IDLE = "idle"
    WATCH = "watch"
    RINGOUT = "ringout"
    SYSTEM = "system"


# -- helpers --------------------------------------------------------------------------------------


def _jsonable(v: Any) -> Any:
    if isinstance(v, float):
        if math.isinf(v):
            return "-oo" if v < 0 else "+oo"
        if math.isnan(v):
            return None
        return v
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_jsonable(x) for x in v]
    if isinstance(v, Target):
        return v.key
    if isinstance(v, Path):
        return str(v)
    to_dict = getattr(v, "to_dict", None)
    if callable(to_dict):
        return _jsonable(to_dict())
    return v


def _db1(v: Any) -> float | None:
    """dB rounded to 0.1. −∞ is preserved (``webui.dumps`` renders it ``"-oo"``); ``None`` means
    *not read*.

    These used to be collapsed together, so a bus master sitting fully down reached the dashboard
    as ``master_db: null`` and rendered as "—" (unknown) rather than "−oo" — the same conflation
    that made ``fader_db`` ambiguous in :mod:`desk` (fixed at M5).
    """
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if math.isinf(v):
        return float(v)
    return round(float(v), 1)


def _db_text(v: Any) -> str | None:
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return format_db(float(v))


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def _fmt(v: Any, spec: str = "g", unit: str = "") -> str:
    """Number → text with ``spec`` (``g`` / ``+.1f`` …); anything else → its str or ''."""
    if v is None:
        return ""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    return format(v, spec) + unit


def _rta_dict(r: RtaSourceResult | None) -> dict[str, Any] | None:
    if r is None:
        return None
    return {"target": r.target.key, "source_index": r.source_index, "post_eq": r.post_eq, "stat_expected": r.stat_expected,
            "stat_actual": r.stat_actual, "verified": r.verified, "options_cleared": r.options_cleared,
            "autogain_cleared": r.autogain_cleared, "detector_set_peak": r.detector_set_peak,
            "decay_set_min": r.decay_set_min, "peakhold_cleared": r.peakhold_cleared, "gain_set": r.gain_set,
            "prefs_before": r.prefs_before}


# -- state / reports -------------------------------------------------------------------------------


@dataclass
class CfsState:
    """Dashboard/status view (DESIGN §15). ``master_db`` is the bus master (None for −∞),
    ``candidate`` the strongest current detector candidate, ``notches`` every cut known on the
    session's GEQ, ``stage`` the last ring-out stage, ``plan`` the system plan while one runs."""

    mode: CfsMode = CfsMode.IDLE
    session_id: str | None = None
    bus: int | str | None = None
    bus_name: str | None = None
    master_db: float | None = None
    budget_left: int | None = None
    candidate: dict[str, Any] | None = None
    notches: list[dict[str, Any]] = field(default_factory=list)
    stage: str | None = None
    started: float | None = None
    plan: dict[str, Any] | None = None
    rta_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value, "session_id": self.session_id, "bus": self.bus, "bus_name": self.bus_name,
            "master_db": self.master_db, "budget_left": self.budget_left, "candidate": self.candidate,
            "notches": [dict(n) for n in self.notches], "stage": self.stage, "started": self.started,
            "plan": self.plan, "rta_source": self.rta_source,
        }


class ReportStore:
    """Ring-out / watch reports as ``<session_id>.json`` (+ ``.md`` summary) in ``dir``."""

    def __init__(self, dir: Path | str) -> None:
        self.dir = Path(dir)

    def exists(self, session_id: str) -> bool:
        return (self.dir / f"{session_id}.json").exists()

    def save(self, report: dict[str, Any]) -> Path:
        """Write the JSON (atomically) and a Markdown summary; returns the JSON path.
        ``report["session_id"]`` names the files (generated when missing)."""
        sid = str(report.get("session_id") or time.strftime("%Y%m%d-%H%M%S")).strip() or time.strftime("%Y%m%d-%H%M%S")
        sid = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in sid)
        report = dict(report)
        report["session_id"] = sid
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{sid}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_jsonable(report), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        try:
            (self.dir / f"{sid}.md").write_text(self.markdown(report), encoding="utf-8")
        except OSError as e:
            log.warning("cannot write markdown summary for %s: %s", sid, e)
        log.info("ring-out report %s saved (%d notch(es))", sid, len(report.get("notches") or []))
        return path

    def _read(self, path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("cannot read report %s: %s", path.name, e)
            return None
        if not isinstance(data, dict):
            return None
        data.setdefault("session_id", path.stem)
        data["path"] = str(path)
        return data

    def list(self, bus: int | str | None = None) -> list[dict[str, Any]]:
        """Report summaries, newest first: ``{session_id, mode, bus, bus_name, started, ended,
        notches, final_stage, aborted, path}``. ``bus`` filters (a number or ``"main"``)."""
        out: list[dict[str, Any]] = []
        if not self.dir.is_dir():
            return out
        for path in self.dir.glob("*.json"):
            rep = self._read(path)
            if rep is None:
                continue
            if bus is not None and rep.get("bus") != bus:
                continue
            out.append({
                "session_id": rep.get("session_id"), "mode": rep.get("mode"), "bus": rep.get("bus"),
                "bus_name": rep.get("bus_name"), "started": rep.get("started"), "ended": rep.get("ended"),
                "notches": len(rep.get("notches") or []), "final_stage": rep.get("final_stage"),
                "aborted": bool(rep.get("aborted")), "path": str(path),
            })
        out.sort(key=lambda r: (str(r.get("started") or ""), str(r.get("session_id") or "")), reverse=True)
        return out

    def load(self, session_id: str) -> dict[str, Any]:
        """A report by id (a unique prefix is accepted). Raises ``CfsError("NOT_FOUND")``."""
        sid = str(session_id).strip()
        path = self.dir / f"{sid}.json"
        if not path.exists():
            hits = [m for m in self.list() if str(m["session_id"]).startswith(sid)] if self.dir.is_dir() else []
            if len(hits) != 1:
                raise CfsError("NOT_FOUND", f"no ring-out report {session_id!r}" + (f" ({len(hits)} match the prefix)" if hits else ""))
            path = Path(hits[0]["path"])
        rep = self._read(path)
        if rep is None:
            raise CfsError("NOT_FOUND", f"report {session_id!r} is unreadable")
        return rep

    def latest_for_bus(self, bus: int | str) -> dict[str, Any] | None:
        metas = self.list(bus)
        return self.load(str(metas[0]["session_id"])) if metas else None

    @staticmethod
    def markdown(report: dict[str, Any]) -> str:
        """Human-readable summary of a report (also used for system reports)."""
        r = _jsonable(report)
        mode = str(r.get("mode") or "ringout")
        if mode == "system":
            lines = [f"# CFS² system ring-out — {r.get('session_id')}", ""]
            lines.append(f"- Started {r.get('started')} · ended {r.get('ended')} · {len(r.get('stages') or [])} stage(s)")
            lines.append(f"- Result: {'ABORT' if r.get('aborted') else 'DONE'}" + (f" — {r['abort_reason']}" if r.get("abort_reason") else ""))
            lines.append("")
            lines.append("| Bus | Name | Start | End | Notches | Result |")
            lines.append("|---|---|---|---|---|---|")
            for s in r.get("stages") or []:
                if s.get("skipped"):
                    lines.append(f"| {s.get('bus')} | | | | | skipped: {s.get('error', {}).get('message', '')} |")
                    continue
                lines.append(f"| {s.get('bus')} | {s.get('bus_name') or ''} | {s.get('start_master') or ''} | {s.get('end_master') or ''} | "
                             f"{len(s.get('notches') or [])} | {s.get('final_stage')}{' — ' + str(s.get('abort_reason')) if s.get('abort_reason') else ''} |")
            lines.append("")
            for s in r.get("stages") or []:
                if not s.get("skipped"):
                    lines.append(ReportStore.markdown(s))
            return "\n".join(lines) + "\n"
        title = "feedback watch" if mode == "watch" else "ring-out"
        bus = r.get("bus")
        name = r.get("bus_name") or ""
        lines = [f"# CFS² {title} — {'Main LR' if bus == 'main' else f'Bus {bus}'}{f' “{name}”' if name else ''}", ""]
        lines.append(f"- Session `{r.get('session_id')}` · started {r.get('started')} · ended {r.get('ended')} · {r.get('duration_s')} s")
        lines.append(f"- Master: {r.get('start_master')} dB → {r.get('end_master')} dB (highest {r.get('max_master') or r.get('start_master')} dB)")
        gbf = r.get("gain_before_feedback")
        lines.append(f"- Gain before feedback: {gbf + ' dB' if gbf else 'no feedback detected'}")
        result = str(r.get("final_stage") or ("ABORT" if r.get("aborted") else "DONE"))
        lines.append(f"- Result: {result}" + (f" — {r['abort_reason']}" if r.get("abort_reason") else ""))
        if r.get("connection_lost"):
            lines.append(f"- Connection lost: starting level {'restored' if r.get('restored') else 'NOT confirmed restored'}")
        geq = r.get("geq") or {}
        if geq:
            lines.append(f"- GEQ: FX slot {geq.get('fx_slot')} side {geq.get('side')} ({geq.get('sel')}); budget {r.get('notch_budget')} → {r.get('budget_left')} left")
        lines.append("")
        notches = r.get("notches") or []
        lines.append(f"## Notches ({len(notches)})")
        lines.append("")
        if notches:
            lines.append("| Freq | GEQ band | Depth | Confidence | Detections |")
            lines.append("|---|---|---|---|---|")
            for n in notches:
                conf = n.get("confidence")
                lines.append(f"| {_fmt(n.get('freq_hz'), 'g', ' Hz')} | {n.get('band')} | {_fmt(n.get('depth_db'), '+.1f', ' dB')} | "
                             f"{_fmt(conf, '.2f')} | {n.get('detections')} |")
        else:
            lines.append("none")
        existing = r.get("existing_cuts") or []
        if existing:
            lines.append("")
            lines.append("Pre-existing cuts: " + ", ".join(f"{_fmt(e.get('freq_hz'), 'g', ' Hz')} {_fmt(e.get('depth_db'), '+.1f', ' dB')}" for e in existing))
        warnings = (r.get("preflight") or {}).get("warnings") or []
        if warnings:
            lines.append("")
            lines.append("## Preflight warnings")
            lines.append("")
            lines.extend(f"- {w}" for w in warnings)
        stages = r.get("stages") or []
        if stages:
            lines.append("")
            lines.append(f"## Stages ({len(stages)})")
            lines.append("")
            for s in stages[:60]:
                extra = {k: v for k, v in s.items() if k not in ("stage", "ts", "master_db")}
                lines.append(f"- {s.get('stage')} @ {s.get('master_db')} dB" + (f" {extra}" if extra else ""))
            if len(stages) > 60:
                lines.append(f"- … {len(stages) - 60} more")
        return "\n".join(lines) + "\n"


# -- session ----------------------------------------------------------------------------------------


class _DeskGeqWriter:
    """:class:`~x32mcp.detector.GeqWriter` for one bus: ``(bus, band)`` → ``Desk.set_geq_band(slot,
    side, band, gain_db)`` after ``policy.validate_notch`` against the last gain it knows."""

    def __init__(self, desk: Desk, policy: Policy, bus: int, fx_slot: int, side: str, existing: dict[int, float]) -> None:
        self._desk = desk
        self._policy = policy
        self.bus = bus
        self.fx_slot = fx_slot
        self.side = side
        self.gains: dict[int, float] = dict(existing)
        self.writes: list[tuple[int, int, float]] = []

    async def set_band_gain(self, bus: int, band: int, gain_db: float) -> None:
        if bus != self.bus:
            raise CfsError("BAD_ARGUMENT", f"notch for bus {bus} on a session for bus {self.bus}")
        self._policy.validate_notch(self.gains.get(band, 0.0), gain_db)  # cuts only, ≤ notch_max_db
        await self._desk.set_geq_band(self.fx_slot, self.side, band, gain_db)
        self.gains[band] = float(gain_db)
        self.writes.append((bus, band, float(gain_db)))


@dataclass
class _Session:
    session_id: str
    mode: CfsMode
    target: Target
    bus: int | str
    bus_int: int
    bus_name: str
    started: float
    preflight: Preflight
    fx_slot: int
    side: str
    sel: str
    nc: NotchController
    det: FeedbackDetector
    writer: _DeskGeqWriter
    budget: int
    start_master_db: float
    master_db: float
    max_master_db: float
    existing: dict[int, float]
    target_db: float | None = None
    step_db: float = 1.0
    dwell_ms: int = 1500
    stage: str | None = None
    final_stage: str | None = None
    stages: list[dict[str, Any]] = field(default_factory=list)
    detections: list[dict[str, Any]] = field(default_factory=list)
    notch_log: list[dict[str, Any]] = field(default_factory=list)
    confidence: dict[int, float] = field(default_factory=dict)
    candidate: Any = None
    rta: RtaSourceResult | None = None
    snapshot_id: str | None = None
    first_feedback_master_db: float | None = None
    end_master_db: float | None = None
    abort_reason: str | None = None
    connection_lost: bool = False
    restored: bool | None = None
    restore_task: asyncio.Task | None = None
    pending: list[Detection] = field(default_factory=list)
    last_values: tuple[float, ...] | None = None
    last_ts: float | None = None
    frames: int = 0
    ended: float | None = None
    system_id: str | None = None
    expected_mics: list[int] | None = None
    warnings: list[str] = field(default_factory=list)
    report: dict[str, Any] | None = None
    report_path: Path | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    frame_event: asyncio.Event = field(default_factory=asyncio.Event)


# -- manager ---------------------------------------------------------------------------------------


class CfsManager:
    """CFS² state machines over one :class:`~x32mcp.desk.Desk` (module doc).

    ``frames``: a started-on-demand :class:`~x32mcp.meters.FrameSource` (default: ``LiveMeters``
    on ``rta.meter_type`` over the desk's connection). ``reports``: the :class:`ReportStore`.
    ``clock`` stamps sessions/reports (``time.time`` style). ``detector_cfg`` overrides
    ``DetectorConfig.from_descriptor``; ``snapshots`` enables the SNAPSHOT stage's file.
    """

    def __init__(
        self,
        desk: Desk,
        policy: Policy,
        events: EventBus,
        reports: ReportStore,
        frames: FrameSource | None = None,
        *,
        clock: Callable[[], float] = time.time,
        detector_cfg: DetectorConfig | None = None,
        snapshots: Any = None,
        candidate_rate_hz: float = 5.0,
    ) -> None:
        self._desk = desk
        self._policy = policy
        self._events = events
        self._reports = reports
        self._clock = clock
        self._d = getattr(desk, "descriptor", None) or desk._d  # Desk exposes neither yet
        self._conn = getattr(desk, "conn", None) or desk._conn
        self._cfg = detector_cfg if detector_cfg is not None else DetectorConfig.from_descriptor(self._d)
        self._band_hz = rta_band_hz(self._d)
        self._geq_hz = [float(h) for h in self._d.geq["band_hz"]]
        self._frames: FrameSource = frames if frames is not None else LiveMeters(self._conn, int(self._d.rta.get("meter_type", 15)))
        self._snapshots = snapshots
        self._cand_period = 1.0 / max(0.1, float(candidate_rate_hz))
        self._cand_last = -math.inf
        self._ses: _Session | None = None
        self._last: _Session | None = None
        self._system: dict[str, Any] | None = None
        self._queue: asyncio.Queue[MeterFrame] | None = None
        self._consumer: asyncio.Task | None = None
        self._unsub_frames: Callable[[], None] | None = None
        self._bg: set[asyncio.Task] = set()
        self._ids: set[str] = set()
        self._lock = asyncio.Lock()
        self._bad_frame_logged = False
        self._unsub_conn = events.subscribe(self._on_connection_event, types={"connection.state"})

    # -- status ------------------------------------------------------------------------------------

    @property
    def frames(self) -> FrameSource:
        return self._frames

    @property
    def detector_cfg(self) -> DetectorConfig:
        return self._cfg

    @property
    def active(self) -> bool:
        return self._ses is not None or self._system is not None

    @property
    def mode(self) -> CfsMode:
        if self._system is not None:
            return CfsMode.SYSTEM
        if self._ses is not None:
            return self._ses.mode
        return CfsMode.IDLE

    @property
    def state(self) -> CfsState:
        ses = self._ses or self._last
        mode = self.mode
        if ses is None:
            return CfsState(mode=mode, plan=self._system["plan"] if self._system else None,
                            session_id=self._system["session_id"] if self._system else None)
        live = self._ses is ses
        return CfsState(
            mode=mode,
            session_id=(self._system["session_id"] if self._system and not live else ses.session_id),
            bus=ses.bus, bus_name=ses.bus_name, master_db=_db1(ses.master_db), budget_left=ses.nc.budget_left,
            candidate=(ses.candidate.to_dict() if (live and ses.candidate is not None) else None),
            notches=[self._notch_dict(ses, n) for n in ses.nc.notches],
            stage=ses.stage, started=ses.started, plan=self._system["plan"] if self._system else None,
            rta_source=ses.target.key,
        )

    # -- public API ------------------------------------------------------------------------------

    async def feedback_watch(self, bus: int | str | Target, *, notch_budget: int | None = None, patch: Any = None) -> dict[str, Any]:
        """Arm the detector on ``bus`` (Tier 1; the human drives the gain). Runs
        :func:`~x32mcp.provision.preflight` (``CfsError("PREFLIGHT_FAILED")`` with the blockers
        when it fails), points the RTA at the bus, starts the frames and the detector task.
        Returns ``{session_id, bus, bus_name, preflight, rta, geq, state}``."""
        t = bus_target(bus)
        async with self._lock:
            self._require_idle()
            pf = await preflight(self._desk, t, patch=patch, reports=self._reports)
            if not pf.ok:
                raise CfsError("PREFLIGHT_FAILED", f"cannot arm on {t.label}: " + "; ".join(pf.blockers), preflight=pf.to_dict())
            ses = await self._open_session(CfsMode.WATCH, t, pf, notch_budget)
            await self._arm(ses)
        log.info("feedback watch armed on %s (%s), budget %d", t.label, ses.session_id, ses.budget)
        return {
            "session_id": ses.session_id, "bus": ses.bus, "bus_name": ses.bus_name, "preflight": pf.to_dict(),
            "rta": _rta_dict(ses.rta), "geq": {"fx_slot": ses.fx_slot, "side": ses.side, "sel": ses.sel,
                                                "existing_cuts": [self._notch_dict(ses, n) for n in ses.nc.notches]},
            "state": self.state.to_dict(),
        }

    async def stop(self) -> dict[str, Any]:
        """Disarm. A watch session is closed and its report saved; a running ring-out is told to
        abort (back-off, report) and awaited; a system run stops after the current stage.
        Returns ``{stopped, mode, report, path}``."""
        self._stop_system("stopped by operator")
        ses = self._ses
        if ses is None:
            if self._system is not None:  # between stages: no session is armed, the run still is
                return {"stopped": True, "mode": CfsMode.SYSTEM.value, "session_id": self._system["session_id"],
                        "report": None, "path": None, "summary": "system ring-out stopping: no further bus will be raised"}
            return {"stopped": False, "mode": self.mode.value, "report": None, "path": None, "summary": "nothing to stop"}
        if ses.mode is CfsMode.WATCH:
            async with self._lock:
                if self._ses is ses:
                    ses.final_stage = ses.stage = "ABORT" if ses.abort_reason else "DONE"
                    await self._finish(ses)
        else:
            if ses.abort_reason is None:
                ses.abort_reason = "stopped by operator"
                self._events.publish("cfs.abort", session_id=ses.session_id, bus=ses.bus, reason=ses.abort_reason)
            ses.wake.set()
            try:
                await asyncio.wait_for(ses.done.wait(), timeout=_STOP_WAIT_S)
            except asyncio.TimeoutError:
                log.warning("ring-out %s did not finish within %.0f s of stop()", ses.session_id, _STOP_WAIT_S)
        return {"stopped": True, "mode": ses.mode.value, "session_id": ses.session_id, "report": ses.report,
                "path": str(ses.report_path) if ses.report_path else None}

    def _stop_system(self, reason: str) -> None:
        """Flag a running ``ring_out_system`` so it stops before the next stage (it may be between
        stages, with no session armed, when the operator asks)."""
        if self._system is not None and not self._system.get("stopped"):
            self._system["stopped"] = str(reason) or "stopped by operator"

    async def abort(self, reason: str) -> None:
        """Abort the running session: a ring-out backs the master off ``abort_backoff_db`` and
        finishes with ABORT (the awaiting ``ring_out`` call returns its report); a watch stops.
        A system run stops after the current stage."""
        self._stop_system(reason)
        ses = self._ses
        if ses is None:
            return
        if ses.abort_reason is None:
            ses.abort_reason = str(reason)
            self._events.publish("cfs.abort", session_id=ses.session_id, bus=ses.bus, reason=ses.abort_reason)
        log.warning("CFS² abort (%s): %s", ses.session_id, reason)
        ses.wake.set()
        if ses.mode is CfsMode.WATCH:
            await self.stop()

    async def ring_out(
        self,
        bus: int | str | Target,
        *,
        target_gain_db: float | None = None,
        step_db: float | None = None,
        dwell_ms: int | None = None,
        notch_budget: int | None = None,
        patch: Any = None,
        _system_id: str | None = None,
        _expected_mics: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        """Run the ring-out state machine on ``bus`` to completion (or abort) and return the
        report (module doc). ``target_gain_db`` (default: the ceiling), ``step_db`` / ``dwell_ms``
        / ``notch_budget`` default to ``ringout.*`` / ``detector.notch_budget_default``."""
        self._policy.check_show_mode_allows("ring_out")
        t = bus_target(bus)
        ro = self._d.ringout
        step = float(ro.get("step_db", 1.0)) if step_db is None else float(step_db)
        if not step > 0:
            raise CfsError("BAD_ARGUMENT", f"step_db must be > 0, got {step_db!r}")
        if step < _MIN_STEP_DB:  # a finer step vanishes in the 0.1 dB read-back and reads as a clamp
            raise CfsError("BAD_ARGUMENT", f"step_db must be >= {_MIN_STEP_DB:g} dB (the fader report grid), got {step_db!r}")
        if step > self._policy.relative_limit_db:  # else the first raise is refused by the relative clamp
            raise CfsError("BAD_ARGUMENT", f"step_db {step:g} exceeds the {self._policy.relative_limit_db:g} dB "
                                           "single-call relative move limit")
        dwell = int(ro.get("dwell_ms", 1500)) if dwell_ms is None else int(dwell_ms)
        if dwell < 0:
            raise CfsError("BAD_ARGUMENT", f"dwell_ms must be >= 0, got {dwell_ms!r}")
        async with self._lock:
            self._require_idle(in_system=_system_id is not None)
            self._events.publish("cfs.stage", session_id=None, bus=bus_label(t), stage="PREFLIGHT", master_db=None)
            pf = await preflight(self._desk, t, patch=patch, reports=self._reports)
            if not pf.ok:
                self._events.publish("cfs.stage", session_id=None, bus=bus_label(t), stage="ABORT", master_db=_db1(pf.master_db),
                                     reason="preflight failed: " + "; ".join(pf.blockers))
                raise CfsError("PREFLIGHT_FAILED", f"cannot ring out {t.label}: " + "; ".join(pf.blockers), preflight=pf.to_dict())
            ceiling = min(self._policy.level_ceiling_db(t), float(ro.get("master_ceiling_db", 0.0)))
            target = ceiling if target_gain_db is None else min(float(target_gain_db), ceiling)
            if target <= float(pf.master_db) + 1e-9:
                # nothing to raise: running anyway would only apply the safety margin to a fader
                # the operator set, i.e. a silent unrequested cut. Refuse before opening a session.
                msg = (f"{t.label} master is already at {format_db(pf.master_db)} dB, at or above the target "
                       f"{format_db(target)} dB; lower the master or raise the target")
                self._events.publish("cfs.stage", session_id=None, bus=bus_label(t), stage="ABORT",
                                     master_db=_db1(pf.master_db), reason=msg)
                raise CfsError("BAD_ARGUMENT", msg, bus=bus_label(t), master_db=_db1(pf.master_db), target_db=_db1(target))
            ses = await self._open_session(CfsMode.RINGOUT, t, pf, notch_budget)
            ses.system_id = _system_id
            ses.step_db, ses.dwell_ms = step, dwell
            ses.target_db = target
            if _expected_mics:
                ses.expected_mics = [int(c) for c in _expected_mics]
                missing = [c for c in ses.expected_mics if not any(m.ch == c and m.include for m in pf.mics)]
                if missing:
                    ses.warnings.append(f"plan expects mics {missing} on {t.label} but they are not routed/unmuted")
            ses.stages.append({"stage": "PREFLIGHT", "ts": self._clock(), "master_db": _db1(ses.start_master_db)})
            try:
                await self._snapshot_stage(ses)
                await self._stage(ses, "ARM", target_db=_db1(ses.target_db), step_db=step, dwell_ms=dwell)
                await self._arm(ses)
            except BaseException:
                self._ses = None
                ses.done.set()
                raise
        log.info("ring-out %s: %s from %s dB to %s dB in %g dB steps, dwell %d ms, budget %d",
                 ses.session_id, t.label, format_db(ses.start_master_db), format_db(ses.target_db), step, dwell, ses.budget)
        try:
            await self._run_ringout(ses)
        except asyncio.CancelledError:
            ses.abort_reason = ses.abort_reason or "cancelled"
            log.warning("ring-out %s cancelled; backing off", ses.session_id)
            await self._unwind(ses)
            raise
        except BaseException as e:  # nothing may leave the session armed and the master raised
            log.exception("ring-out %s failed unexpectedly", ses.session_id)
            ses.abort_reason = ses.abort_reason or f"internal error: {e!r}"
            await self._unwind(ses)
            raise
        else:
            await self._finish(ses)
        assert ses.report is not None
        return ses.report

    async def ring_out_system(self, plan: dict[str, Any] | None = None) -> dict[str, Any]:
        """Ring out every stage of ``plan`` (``{"stages": [{"bus": 3, "target_gain_db": …,
        "mics": [ch…]}, …, {"bus": "main"}]}``) in order; without a plan, every bus (then Main
        LR) whose ring-out GEQ validates. One consolidated report; a stage whose preflight fails
        is skipped, a connection loss stops the run, and ``stop()``/``abort()``/``close()`` stop it
        before the next stage (no further bus is raised)."""
        self._policy.check_show_mode_allows("ring_out_system")
        self._require_idle()
        stages = await self._system_stages(plan)
        if not stages:
            raise CfsError("NO_STAGES", "nothing to ring out: no bus (or Main LR) has a validated ring-out GEQ")
        sid = self._new_id("system", None)
        started = self._clock()
        self._system = {"session_id": sid, "plan": {"stages": stages}, "started": started}
        self._events.publish("cfs.state", mode=CfsMode.SYSTEM.value, session_id=sid, bus=None, stage=None)
        results: list[dict[str, Any]] = []
        abort_reason: str | None = None
        try:
            for st in stages:
                if self._system is not None and self._system.get("stopped"):
                    abort_reason = str(self._system["stopped"])  # stop()/abort()/close() between stages
                    break
                bus = st["bus"]
                try:
                    rep = await self.ring_out(
                        bus, target_gain_db=st.get("target_gain_db"), step_db=st.get("step_db"), dwell_ms=st.get("dwell_ms"),
                        notch_budget=st.get("notch_budget"), patch=st.get("patch"), _system_id=sid, _expected_mics=st.get("mics"),
                    )
                except (CfsError, DeskError, PolicyError) as e:
                    log.warning("system ring-out: stage %s skipped: %s", bus, e)
                    results.append({"bus": bus, "skipped": True, "error": e.to_dict()})
                    if isinstance(e, DeskError) and e.code == "NOT_CONNECTED":
                        abort_reason = f"stage {bus}: {e.message}"
                        break
                    continue
                results.append(rep)
                if rep.get("connection_lost"):
                    abort_reason = f"connection lost during stage {bus}"
                    break
                if rep.get("abort_reason") == "stopped by operator":
                    abort_reason = "stopped by operator"
                    break
        finally:
            self._system = None
        ended = self._clock()
        total = sum(len(r.get("notches") or []) for r in results if not r.get("skipped"))
        report = {
            "session_id": sid, "mode": "system", "bus": None, "started": _iso(started), "ended": _iso(ended),
            "duration_s": round(ended - started, 1), "plan": {"stages": stages}, "stages": results,
            "aborted": abort_reason is not None, "abort_reason": abort_reason, "final_stage": "ABORT" if abort_reason else "DONE",
            "notches": [n for r in results if not r.get("skipped") for n in (r.get("notches") or [])],
            "summary": f"system ring-out: {sum(1 for r in results if not r.get('skipped'))} bus(es) done, "
                       f"{sum(1 for r in results if r.get('skipped'))} skipped, {total} notch(es)"
                       + (f"; ABORT: {abort_reason}" if abort_reason else ""),
        }
        path = self._save_report(report)
        self._events.publish("cfs.report", session_id=sid, bus=None, path=str(path) if path else None, mode="system")
        self._events.publish("cfs.state", mode=self.mode.value, session_id=None, bus=None, stage=None)
        report["path"] = str(path) if path else None
        return report

    async def close(self) -> None:
        """Abort whatever runs, stop the frames and unsubscribe (server shutdown)."""
        self._unsub_conn()
        self._stop_system("shutdown")
        ses = self._ses
        if ses is not None:
            await self.abort("shutdown")
            try:
                await asyncio.wait_for(ses.done.wait(), timeout=_STOP_WAIT_S)
            except asyncio.TimeoutError:
                pass
        await self._disarm()
        # the background tasks (connection-loss handling, a watch stop) touch the desk and write
        # reports: cancel AND await them, or the loop closes under them ("Task was destroyed").
        tasks, self._bg = list(self._bg), set()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=_STOP_WAIT_S)

    # -- sessions ----------------------------------------------------------------------------------

    def _require_idle(self, *, in_system: bool = False) -> None:
        if self._ses is not None:
            s = self._ses
            raise CfsError("BUSY", f"CFS² is already {s.mode.value} on {s.target.label} ({s.session_id}); stop or abort it first",
                           session_id=s.session_id, mode=s.mode.value, bus=s.bus)
        if self._system is not None and not in_system:
            raise CfsError("BUSY", f"a system ring-out is running ({self._system['session_id']}); stop it first",
                           session_id=self._system["session_id"], mode=CfsMode.SYSTEM.value, bus=None)

    def _new_id(self, mode: str, t: Target | None) -> str:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self._clock()))
        where = "system" if t is None else ("main" if t.family == "main" else f"bus{int(t.index):02d}")
        base = f"{stamp}-{mode}-{where}"
        sid, n = base, 2
        while sid in self._ids or self._reports.exists(sid):
            sid = f"{base}-{n}"
            n += 1
        self._ids.add(sid)
        return sid

    async def _calibrate_floor(self, cfg: DetectorConfig) -> DetectorConfig:
        """Raise ``min_level_db`` to sit above the room's measured noise floor.

        A fixed gate cannot work: at M7 the configured -60 dB sat 8 dB *below* the studio's own
        floor (48 Hz rumble peaking at -51.7 dB), so ordinary room noise qualified as a feedback
        candidate and spent the notch budget before the operator touched a fader. A pub, a field
        and a studio all differ, so measure instead of guessing: sample the RTA for
        ``floor_sample_s`` with the loop quiet, take the loudest band, and gate
        ``floor_margin_db`` above it. The configured value is a floor for the floor - calibration
        only ever raises it.
        """
        margin = float(self._d.detector.get("floor_margin_db", 0.0) or 0.0)
        secs = float(self._d.detector.get("floor_sample_s", 0.0) or 0.0)
        want = int(self._d.detector.get("floor_sample_frames", 40) or 40)
        if margin <= 0.0 or secs <= 0.0 or self._frames is None:
            return cfg
        peaks: list[float] = []
        done = asyncio.Event()

        def grab(frame: Any) -> None:
            if frame.values:
                peaks.append(max(frame.values))
                if len(peaks) >= want:
                    done.set()

        first = asyncio.Event()
        unsub = self._frames.subscribe(lambda f: (grab(f), first.set()) and None)
        try:
            # Bail out at once if nothing is streaming: _open_session also runs on the preflight
            # (pending) call of ring_out, where the frame source is idle, and stalling there for
            # the whole window delays a call that does no detection at all.
            try:
                await asyncio.wait_for(first.wait(), timeout=_FLOOR_FIRST_FRAME_S)
            except asyncio.TimeoutError:
                return cfg
            # Then enough frames, or the time limit — whichever comes first.
            try:
                await asyncio.wait_for(done.wait(), timeout=secs)
            except asyncio.TimeoutError:
                pass
        finally:
            unsub()
        if not peaks:
            log.warning("cfs: no RTA frames while calibrating the noise floor; keeping %.1f dB",
                        cfg.min_level_db)
            return cfg
        floor = max(peaks)
        gate = max(cfg.min_level_db, floor + margin)
        self._floor_db, self._gate_db = floor, gate
        if gate > cfg.min_level_db:
            log.info("cfs: room floor %.1f dB over %d frame(s) -> gating candidates at %.1f dB (was %.1f)",
                     floor, len(peaks), gate, cfg.min_level_db)
            return dataclasses.replace(cfg, min_level_db=gate)
        return cfg

    async def _open_session(self, mode: CfsMode, t: Target, pf: Preflight, notch_budget: int | None) -> _Session:
        cfg = self._cfg
        budget = cfg.notch_budget_default if notch_budget is None else int(notch_budget)
        if budget < 0:
            raise CfsError("BAD_ARGUMENT", f"notch_budget must be >= 0, got {notch_budget!r}")
        ins = pf.geq.insert
        assert ins is not None and ins.fx_slot is not None and ins.side is not None  # preflight guarantees it
        existing = {i + 1: float(g) for i, g in enumerate(pf.geq.bands_db or []) if g is not None}
        bus_int = 0 if t.family == "main" else int(t.index)
        nc = NotchController(cfg, self._geq_hz, self._policy.validate_notch, budget=budget, existing=existing)
        writer = _DeskGeqWriter(self._desk, self._policy, bus_int, ins.fx_slot, ins.side, existing)
        cfg = await self._calibrate_floor(cfg)
        det = FeedbackDetector(cfg, self._band_hz)
        start = float(pf.master_db)
        ses = _Session(
            session_id=self._new_id(mode.value, t), mode=mode, target=t, bus=bus_label(t), bus_int=bus_int, bus_name=pf.bus_name,
            started=self._clock(), preflight=pf, fx_slot=ins.fx_slot, side=ins.side, sel=ins.sel, nc=nc, det=det, writer=writer,
            budget=budget, start_master_db=start, master_db=start, max_master_db=start, existing=existing,
        )
        ses.warnings.extend(pf.warnings)
        return ses

    async def _arm(self, ses: _Session) -> None:
        """Pre-write snapshot → RTA source → frames → detector task. Sets the live session."""
        # Nothing else on this path writes through the Desk, so without this the FIRST notch would
        # pay for the whole desk dump (BRIEF §5 budgets < 100 ms detect→cut). A failure here is not
        # fatal: policy.snapshot_before_write stays set and Desk._write tries again (and raises).
        try:
            await self._desk.ensure_pre_write_snapshot()
        except (DeskError, X32ConnectionError, OSError, ValueError) as e:
            log.warning("pre-write snapshot before arming on %s failed: %s", ses.target.label, e)
        try:
            ses.rta = await set_rta_source(self._conn, self._d, ses.target)
        except RtaSourceError as e:
            raise CfsError("BAD_ARGUMENT", str(e)) from None
        except NotConnected as e:
            raise CfsError("NOT_CONNECTED", str(e)) from None
        self._desk.invalidate("/-prefs/rta")
        self._ses = ses
        self._queue = asyncio.Queue(maxsize=16)
        self._bad_frame_logged = False
        self._unsub_frames = self._frames.subscribe(self._on_frame)
        self._consumer = asyncio.create_task(self._consume(ses), name=f"cfs-detector-{ses.session_id}")
        try:
            await self._frames.start()
        except BaseException:
            await self._disarm()
            self._ses = None
            raise
        self._events.publish("cfs.state", mode=self.mode.value, session_id=ses.session_id, bus=ses.bus, stage=ses.stage,
                             rta_source=ses.target.key, rta_verified=bool(ses.rta and ses.rta.verified))

    async def _disarm(self) -> None:
        if self._unsub_frames is not None:
            self._unsub_frames()
            self._unsub_frames = None
        try:
            await self._frames.stop()
        except Exception:
            log.exception("frame source stop failed")
        task, self._consumer = self._consumer, None
        self._queue = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                if (cur := asyncio.current_task()) is not None and cur.cancelling():
                    raise
            except Exception:
                pass

    async def _unwind(self, ses: _Session) -> None:
        """Back the master off and finish the session while the caller unwinds (cancellation or an
        unexpected failure). ``_finalize_levels`` swallows its own write failures and ``_finish``
        is idempotent through ``ses.done``; the session is cleared here whatever happens."""
        try:
            try:
                await asyncio.shield(self._finalize_levels(ses, None))
            except Exception:
                log.exception("ring-out %s: the back-off after the failure did not complete", ses.session_id)
            await asyncio.shield(self._finish(ses))
        except Exception:
            log.exception("ring-out %s: could not be finished cleanly", ses.session_id)
        finally:
            if self._ses is ses:
                self._ses = None
                self._last = ses
            ses.done.set()

    async def _finish(self, ses: _Session) -> None:
        """Disarm, build + save the report, publish, and hand the state to ``_last``."""
        if ses.done.is_set():
            return
        if ses.restore_task is not None and not ses.restore_task.done():
            try:
                await ses.restore_task
            except Exception:
                log.exception("restore task failed")
        await self._disarm()
        # Hand the engineer's RTA back the way we found it (source, ballistics, gain). Best effort, never raises.
        ses.rta_restored = None
        if ses.rta is not None and ses.rta.prefs_before:
            try:
                ses.rta_restored = await restore_rta_prefs(self._conn, self._d, ses.rta.prefs_before)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("restoring the RTA preferences failed")
            self._desk.invalidate("/-prefs/rta")
        ses.ended = self._clock()
        if ses.final_stage is None:
            ses.final_stage = "ABORT" if ses.abort_reason else "DONE"
        if ses.end_master_db is None:
            ses.end_master_db = ses.master_db
        ses.report = self._build_report(ses)
        ses.report_path = self._save_report(ses.report)
        ses.report["path"] = str(ses.report_path) if ses.report_path else None
        self._ses = None
        self._last = ses
        self._events.publish("cfs.report", session_id=ses.session_id, bus=ses.bus, path=ses.report["path"], mode=ses.mode.value,
                             notches=len(ses.report["notches"]), final_stage=ses.final_stage)
        self._events.publish("cfs.state", mode=self.mode.value, session_id=None, bus=None, stage=ses.final_stage)
        log.info("CFS² session %s finished: %s (%d notch(es))%s", ses.session_id, ses.final_stage, len(ses.report["notches"]),
                 f" — {ses.abort_reason}" if ses.abort_reason else "")
        ses.done.set()

    def _save_report(self, report: dict[str, Any]) -> Path | None:
        try:
            return self._reports.save(report)
        except OSError as e:
            log.error("cannot save ring-out report %s: %s", report.get("session_id"), e)
            return None

    # -- frames / detector ------------------------------------------------------------------------

    def _on_frame(self, frame: MeterFrame) -> None:
        q = self._queue
        if q is None:
            return
        if q.full():
            try:
                q.get_nowait()  # keep the newest: the detector prefers fresh frames to complete ones
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(frame)

    async def _consume(self, ses: _Session) -> None:
        q = self._queue
        assert q is not None
        n = len(self._band_hz)
        while True:
            frame = await q.get()
            try:
                if len(frame.values) != n:
                    if not self._bad_frame_logged:
                        log.warning("ignoring meter frames with %d values (expected %d RTA bands)", len(frame.values), n)
                        self._bad_frame_logged = True
                    continue
                if ses.last_ts is not None and frame.ts <= ses.last_ts:
                    continue  # duplicate/reordered frame: the detector needs increasing time
                ses.frames += 1
                ses.last_values = frame.values
                ses.last_ts = frame.ts
                ses.frame_event.set()
                dets = ses.det.feed(frame.values, frame.ts)
                self._publish_candidate(ses)
                for det in dets:
                    ses.detections.append({**det.to_dict(), "master_db": _db1(ses.master_db)})
                    if ses.first_feedback_master_db is None:
                        ses.first_feedback_master_db = ses.master_db
                    if ses.mode is CfsMode.WATCH:
                        await self._notch(ses, det)
                    else:
                        ses.pending.append(det)
                        ses.wake.set()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("detector task failed on a frame")

    def _publish_candidate(self, ses: _Session) -> None:
        cands = ses.det.candidates
        best = max(cands, key=lambda c: c.confidence) if cands else None
        ses.candidate = best
        if best is None:
            return
        now = asyncio.get_running_loop().time()
        if now - self._cand_last < self._cand_period:
            return
        self._cand_last = now
        self._events.publish("cfs.candidate", session_id=ses.session_id, bus=ses.bus, **best.to_dict())

    def _notch_dict(self, ses: _Session, n: Notch) -> dict[str, Any]:
        d = n.to_dict()
        d["bus"] = ses.bus
        d["confidence"] = ses.confidence.get(n.band)
        d["fx_slot"] = ses.fx_slot
        d["side"] = ses.side
        return d

    async def _notch(self, ses: _Session, det: Detection) -> Notch | None:
        """Plan and write the next cut for ``det``; None when nothing can be done."""
        try:
            n = ses.nc.plan(det, ses.bus_int, ses.session_id)
        except PolicyError as e:
            log.error("notch plan refused by policy: %s", e)
            return None
        if n is None:
            log.info("no notch for %.0f Hz: budget spent or band at %+.0f dB", det.freq_hz, self._cfg.notch_max_db)
            return None
        try:
            await ses.writer.set_band_gain(n.bus, n.band, n.depth_db)
        except (DeskError, PolicyError, X32ConnectionError, CfsError) as e:
            log.warning("notch write failed (%s): %s", ses.session_id, e)
            if isinstance(e, DeskError) and e.code == "NOT_CONNECTED" or isinstance(e, NotConnected):
                await self._lose_connection(ses, f"desk write failed: {e}")
            return None
        ses.confidence[n.band] = max(ses.confidence.get(n.band, 0.0), det.confidence)
        item = self._notch_dict(ses, n)
        item.update(rta_band=det.band, rta_freq_hz=det.freq_hz, level_db=det.level_db, master_db=_db1(ses.master_db))
        ses.notch_log.append(item)
        self._events.publish("cfs.notch", **item)
        log.info("notch: %s band %d (%g Hz) -> %+.1f dB (ring at %.0f Hz, conf %.2f)", ses.target.label, n.band, n.freq_hz,
                 n.depth_db, det.freq_hz, det.confidence)
        return n

    # -- ring-out state machine --------------------------------------------------------------

    async def _stage(self, ses: _Session, stage: str, **extra: Any) -> None:
        ses.stage = stage
        entry = {"stage": stage, "ts": self._clock(), "master_db": _db1(ses.master_db), **extra}
        ses.stages.append(entry)
        self._events.publish("cfs.stage", session_id=ses.session_id, bus=ses.bus, stage=stage, master_db=_db1(ses.master_db), **extra)

    async def _snapshot_stage(self, ses: _Session) -> None:
        await self._stage(ses, "SNAPSHOT")
        if self._snapshots is None:
            return
        try:
            state = await self._desk.dump(sections=[ses.target.key])
            snap = self._snapshots.save(state, label=f"ringout-pre-{ses.target.key}")
            ses.snapshot_id = snap.id
        except (DeskError, OSError, ValueError) as e:
            log.warning("ring-out snapshot of %s failed: %s", ses.target.label, e)

    async def _write_master(self, ses: _Session, db: float, *, force: bool = False) -> float:
        """Move the bus master to ``db`` (single step) through the Desk; returns what was written.
        ``force`` bypasses the relative clamp — used for every *lowering* write (back-off), which is
        always safe and must never be refused by show mode (``set_main_level`` forces it already)."""
        if ses.target.family == "main":
            res = await self._desk.set_main_level("st", db, ramp_ms=0)
        else:
            res = await self._desk.set_level(ses.target, db, ramp_ms=0, force=force)
        after = res.get("after_db")
        return NEG_INF_DB if after is None else float(after)

    async def _dwell(self, ses: _Session, seconds: float) -> None:
        ses.wake.clear()
        if ses.pending or ses.abort_reason:
            return
        try:
            await asyncio.wait_for(ses.wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    @staticmethod
    def _pop_pending(ses: _Session) -> Detection | None:
        return ses.pending.pop(0) if ses.pending else None

    def _band_level(self, ses: _Session, band: int) -> float | None:
        vals = ses.last_values
        if vals is None:
            return None
        tol = self._cfg.band_tolerance
        lo, hi = max(0, band - tol), min(len(vals), band + tol + 1)
        return max(vals[lo:hi])

    async def _run_ringout(self, ses: _Session) -> None:
        cfg = self._cfg
        target = float(ses.target_db if ses.target_db is not None else ses.start_master_db)
        reason: str | None = None
        if target <= ses.start_master_db + 1e-9:
            reason = f"master already at the target ({format_db(ses.start_master_db)} dB)"
        while reason is None and ses.abort_reason is None:
            det = self._pop_pending(ses)
            if det is not None:
                await self._stage(ses, "HOLD", freq_hz=round(det.freq_hz, 1), rta_band=det.band, confidence=round(det.confidence, 3))
                tamed = await self._notch_and_verify(ses, det)
                if ses.abort_reason is not None:
                    break
                if not tamed:
                    ses.abort_reason = (f"ring at {det.freq_hz:.0f} Hz not tamed: GEQ band cannot cut deeper than "
                                        f"{cfg.notch_max_db:+.0f} dB or the notch budget is spent")
                    break
                if ses.nc.spent:
                    reason = "notch budget spent"
                    break
                continue
            if ses.master_db >= target - 1e-9:
                reason = "target reached"
                break
            if ses.nc.spent:
                reason = "notch budget spent"
                break
            nxt = min(ses.master_db + ses.step_db, target)
            try:
                after = await self._write_master(ses, nxt)
            except PolicyError as e:  # a LOCAL refusal (rate limit / relative clamp): abort normally
                ses.abort_reason = ses.abort_reason or f"raise refused by policy: {e}"
                break
            except (DeskError, X32ConnectionError) as e:
                if isinstance(e, X32ConnectionError) or (isinstance(e, DeskError) and e.code in ("NOT_CONNECTED", "TIMEOUT")):
                    await self._lose_connection(ses, f"desk write failed while raising: {e}")
                else:
                    ses.abort_reason = ses.abort_reason or f"raise refused: {e}"
                break
            # ``after`` is the read-back rounded to the 0.1 dB report grid, so it may sit just under
            # the request: only a refusal that makes no progress at all is a real clamp.
            if after >= nxt - _REPORT_GRID_DB:
                after = max(after, nxt)
            elif after <= ses.master_db + 1e-9:
                ses.master_db = after
                reason = f"master clamped at {format_db(after)} dB"
                break
            ses.master_db = after
            ses.max_master_db = max(ses.max_master_db, after)
            await self._stage(ses, "RAISE")
            await self._dwell(ses, ses.dwell_ms / 1000.0)
        await self._finalize_levels(ses, reason)

    async def _notch_and_verify(self, ses: _Session, det: Detection) -> bool:
        """NOTCH → VERIFY, deepening until the band decays; False when it cannot be tamed."""
        cfg = self._cfg
        while ses.abort_reason is None:
            lvl = self._band_level(ses, det.band)  # None (no frame yet) — never `or`: 0.0 dB is clip
            level0 = max(det.level_db, lvl) if lvl is not None else det.level_db
            n = await self._notch(ses, det)
            if n is None:
                return False
            await self._stage(ses, "NOTCH", freq_hz=round(det.freq_hz, 1), rta_band=det.band, level_db=round(level0, 1),
                              band=n.band, depth_db=n.depth_db)
            ok, drop = await self._verify_decay(ses, det, level0)
            await self._stage(ses, "VERIFY", band=n.band, depth_db=n.depth_db, ok=ok, drop_db=round(drop, 1),
                              required_db=cfg.decay_verify_db)
            tol = cfg.band_tolerance + 1
            ses.pending = [p for p in ses.pending if abs(p.band - det.band) > tol]  # stale re-detections of this band
            if ok:
                return True
            log.info("ring at %.0f Hz only dropped %.1f dB after %+.0f dB; deepening", det.freq_hz, drop, n.depth_db)
        return False

    async def _verify_decay(self, ses: _Session, det: Detection, level0: float) -> tuple[bool, float]:
        """Wait up to ``decay_verify_s`` for the band to sit ``decay_verify_db`` below ``level0`` on
        ``decay_verify_frames`` consecutive frames. One dipping frame is RTA noise (the synthetic
        source alone wobbles ±3 dB), not a tamed ring — a premature pass resumes raising the master
        under an insufficient cut. Returns (ok, best drop seen in dB)."""
        cfg = self._cfg
        need = max(1, int(cfg.decay_verify_frames))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + cfg.decay_verify_s
        best = 0.0
        ok_frames = 0
        while ses.abort_reason is None:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            ses.frame_event.clear()
            try:
                await asyncio.wait_for(ses.frame_event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            lvl = self._band_level(ses, det.band)
            if lvl is None:
                continue
            drop = level0 - lvl
            best = max(best, drop)
            if drop >= cfg.decay_verify_db:
                ok_frames += 1
                if ok_frames >= need:
                    return True, drop
            else:
                ok_frames = 0
        return False, best

    async def _finalize_levels(self, ses: _Session, reason: str | None) -> None:
        """BACKOFF → DONE, or the abort paths (back-off / restore) → ABORT."""
        ro = self._d.ringout
        if ses.connection_lost:
            if ses.restore_task is not None:
                try:
                    await ses.restore_task
                except Exception:
                    log.exception("restore task failed")
            ses.end_master_db = ses.start_master_db if ses.restored else ses.master_db
            ses.final_stage = "ABORT"
            await self._stage(ses, "ABORT", reason=ses.abort_reason, restored=ses.restored)
            return
        if ses.abort_reason is None and ses.master_db <= ses.start_master_db + 1e-9:
            # the master was never raised (target already reached, zero budget): the safety margin is
            # measured from a level WE pushed up to — never cut a fader the operator set.
            ses.end_master_db = ses.master_db
            ses.final_stage = "DONE"
            await self._stage(ses, "DONE", reason=reason, backoff_db=0.0)
            return
        if ses.abort_reason is not None:
            back = float(ro.get("abort_backoff_db", 6.0))
            final_stage = "ABORT"
        else:
            back = float(ro.get("safety_margin_db", 3.0))
            final_stage = "DONE"
        final = max(FADER_FLOOR_DB, ses.master_db - back)
        await self._stage(ses, "BACKOFF", to_db=_db1(final), backoff_db=back)
        try:
            written = await self._write_master(ses, final, force=True)  # lowering: never let a clamp refuse it
            ses.master_db = written
            ses.end_master_db = written
        except (DeskError, PolicyError, X32ConnectionError) as e:
            log.warning("back-off write failed (%s): %s", ses.session_id, e)
            ses.abort_reason = ses.abort_reason or f"back-off failed: {e}"
            final_stage = "ABORT"
            ses.end_master_db = ses.master_db
        ses.final_stage = final_stage
        await self._stage(ses, final_stage, reason=ses.abort_reason if final_stage == "ABORT" else reason)

    # -- connection loss ---------------------------------------------------------------------

    def _on_connection_event(self, ev: Event) -> None:
        if ev.data.get("state") != ConnectionState.DEGRADED.value:
            return
        ses = self._ses
        if ses is None or ses.connection_lost:
            return
        reason = "connection lost: " + str(ev.data.get("error") or "desk not responding")
        task = asyncio.get_running_loop().create_task(self._lose_connection(ses, reason), name="cfs-connection-lost")
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _lose_connection(self, ses: _Session, reason: str) -> None:
        """Mark the session lost; ring-outs start the start-level restore, watches stop."""
        if ses.connection_lost:
            return
        ses.connection_lost = True
        ses.abort_reason = ses.abort_reason or reason
        ses.wake.set()
        log.warning("CFS² %s: %s", ses.session_id, reason)
        self._events.publish("cfs.abort", session_id=ses.session_id, bus=ses.bus, reason=ses.abort_reason)
        if ses.mode is CfsMode.RINGOUT:
            if ses.restore_task is None:
                ses.restore_task = asyncio.create_task(self._restore_master(ses), name=f"cfs-restore-{ses.session_id}")
        elif self._ses is ses:
            task = asyncio.get_running_loop().create_task(self.stop(), name="cfs-watch-stop")
            self._bg.add(task)
            task.add_done_callback(self._bg.discard)

    async def _restore_master(self, ses: _Session) -> None:
        """Raw fire-and-forget writes of the starting master every 0.5 s, each followed by a
        read-back, until the desk confirms it or 10 s pass (module doc)."""
        t = ses.target
        spec = self._d.param(t.family, "mix/fader")
        address = spec.address(t)
        raw = float(spec.to_raw(ses.start_master_db))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _RESTORE_DEADLINE_S
        attempts = 0
        restored = False
        while True:
            attempts += 1
            try:
                await self._conn.send_raw(address, raw, typetags="f")
            except Exception as e:
                log.debug("restore write %d failed: %s", attempts, e)
            got: Any = None
            try:
                got = await asyncio.wait_for(self._conn.get(address), timeout=_RESTORE_READ_TIMEOUT_S)
            except Exception:
                got = None
            if isinstance(got, (int, float)) and not isinstance(got, bool) and abs(float(got) - raw) <= _FADER_GRID + 1e-6:
                restored = True
                break
            if loop.time() >= deadline:
                break
            await asyncio.sleep(_RESTORE_RETRY_S)
        self._desk.invalidate(address)
        ses.restored = restored
        if restored:
            ses.master_db = ses.start_master_db
        self._events.publish("cfs.restore", session_id=ses.session_id, bus=ses.bus, master_db=_db1(ses.start_master_db),
                             restored=restored, attempts=attempts)
        (log.info if restored else log.error)("%s master %s to %s dB after %d attempt(s)", t.label,
                                              "restored" if restored else "NOT confirmed restored", format_db(ses.start_master_db), attempts)

    # -- reports -----------------------------------------------------------------------------

    def _build_report(self, ses: _Session) -> dict[str, Any]:
        mine = [self._notch_dict(ses, n) for n in ses.nc.notches if n.session_id == ses.session_id]
        pre = [self._notch_dict(ses, n) for n in ses.nc.notches if n.session_id != ses.session_id]
        gbf = ses.first_feedback_master_db
        duration = round((ses.ended or self._clock()) - ses.started, 1)
        aborted = ses.abort_reason is not None
        if ses.mode is CfsMode.WATCH:
            summary = (f"watch on {ses.target.label}: {len(mine)} notch(es) from {len(ses.detections)} detection(s) in {duration} s"
                       + (f"; stopped: {ses.abort_reason}" if aborted else ""))
        else:
            summary = (f"ring-out {ses.target.label}: master {format_db(ses.start_master_db)} -> {format_db(ses.end_master_db)} dB "
                       f"(highest {format_db(ses.max_master_db)}), {len(mine)} notch(es)"
                       + (f", feedback first at {format_db(gbf)} dB" if gbf is not None else ", no feedback detected")
                       + (f"; ABORT: {ses.abort_reason}" if aborted else "; DONE"))
        return {
            "session_id": ses.session_id, "system_id": ses.system_id, "mode": ses.mode.value, "bus": ses.bus,
            "target": ses.target.key, "bus_name": ses.bus_name,
            "started": _iso(ses.started), "ended": _iso(ses.ended), "duration_s": duration,
            "start_master_db": _db1(ses.start_master_db), "start_master": _db_text(ses.start_master_db),
            "end_master_db": _db1(ses.end_master_db), "end_master": _db_text(ses.end_master_db),
            "max_master_db": _db1(ses.max_master_db), "max_master": _db_text(ses.max_master_db),
            "gain_before_feedback_db": _db1(gbf), "gain_before_feedback": _db_text(gbf),
            "target_db": _db1(ses.target_db), "step_db": ses.step_db, "dwell_ms": ses.dwell_ms,
            "notch_budget": ses.budget, "budget_left": ses.nc.budget_left,
            "geq": {"fx_slot": ses.fx_slot, "side": ses.side, "sel": ses.sel},
            "rta": _rta_dict(ses.rta), "rta_restored": getattr(ses, "rta_restored", None), "snapshot": ses.snapshot_id,
            "notches": mine, "existing_cuts": pre,
            "detections": len(ses.detections), "detection_log": ses.detections[-_MAX_DETECTIONS_IN_REPORT:],
            "notch_log": ses.notch_log, "stages": ses.stages, "frames": ses.frames,
            "final_stage": ses.final_stage, "aborted": aborted, "abort_reason": ses.abort_reason,
            "connection_lost": ses.connection_lost, "restored": ses.restored,
            "preflight": {"warnings": list(ses.warnings), "mics": [m.to_dict() for m in ses.preflight.mics],
                          "included": [m.ch for m in ses.preflight.included_mics]},
            "expected_mics": ses.expected_mics, "summary": summary,
        }

    async def _system_stages(self, plan: dict[str, Any] | None) -> list[dict[str, Any]]:
        if plan is not None:
            raw = plan.get("stages") if isinstance(plan, dict) else None
            if not isinstance(raw, list) or not raw:
                raise CfsError("BAD_ARGUMENT", 'plan must be {"stages": [{"bus": 3, ...}, ...]}')
            stages: list[dict[str, Any]] = []
            for st in raw:
                if not isinstance(st, dict) or "bus" not in st:
                    raise CfsError("BAD_ARGUMENT", f"each stage needs a bus, got {st!r}")
                t = bus_target(st["bus"])
                stages.append({**st, "bus": bus_label(t)})
            return stages
        buses: list[int | str] = list(range(1, 17)) + ["main"]
        status = await validate_ringout_eqs(self._desk, buses, self._reports)
        return [{"bus": b, "target_gain_db": None} for b in buses if status[b].ok]
