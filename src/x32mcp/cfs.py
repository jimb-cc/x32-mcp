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
  master_db, …}, ``cfs.candidate`` (best current candidate, ≤ ``candidate_rate_hz`` = 5/s; plus
  ``alert: "on"|"off"`` state changes for alert-worthy candidates, see the policy layer),
  ``cfs.notch`` {session_id, bus, band, freq_hz, depth_db, detections, confidence, ts, fx_slot,
  side, rta_band, tier ("A" detector / "B" policy), policy}, ``cfs.abort`` {session_id, bus, reason},
  ``cfs.restore`` {session_id, bus, master_db, restored, attempts}, ``cfs.report`` {session_id, bus, path},
  ``cfs.alert`` {session_id, bus, on, color, candidates}, ``cfs.programme_present``, ``cfs.policy``
  {session_id, bus, what, …} (tier-B verdict steps, flag actions).
* **Policy layer** (docs/CFS_POLICY.md, :mod:`x32mcp.cfs_policy` for the knobs and pure rules; the
  wiring is ``_policy_frame`` here, run after every ``feed()``): the LF edge from the open mics' HPFs,
  the ring-out contract check (``programme_present``), the tier-B one-shot cut on MODERATE lines and its
  verdict-driven follow-up, candidate alerts (events + the bus scribble-strip colour, always restored),
  the AT-ARM rule in watch, the analyser-flag actions (re-force ballistics, abort on a frozen display)
  and the back-off probe on a STATIONARY ``backoff_advised`` line in ring_out. Policy writes go through
  the same Desk / Policy paths as everything else and stop the moment an abort begins.
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
import re
import time
from dataclasses import dataclass, field
from statistics import median
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Sequence

from .cfs_policy import (CfsPolicyConfig, LfEdge, MicHpf, alert_worthy, at_arm_cut_allowed, candidate_brief, lf_edge_from_hpfs,
                         tier_b_eligible)
from .connection import ConnectionError as X32ConnectionError, ConnectionState, NotConnected
from .desk import Desk, DeskError, priority_writes
from .detector import Candidate, Detection, DetectorConfig, FeedbackDetector, Notch, NotchController
from .events import Event, EventBus
from .meters import (FrameSource, LiveMeters, MeterFrame, RtaSourceError, RtaSourceResult, force_rta_ballistics, rta_band_hz,
                     set_rta_source, restore_rta_prefs)
from .policy import FADER_FLOOR_DB, Policy, PolicyError
from .provision import geq_sides_for, Preflight, bus_label, bus_target, preflight, validate_ringout_eqs
from .scales import NEG_INF_DB, format_db
from .targets import Target

__all__ = ["CfsError", "CfsMode", "CfsState", "STAGES", "ReportStore", "CfsManager"]

log = logging.getLogger(__name__)

_PANIC_REASON = "panic: outputs muted by the operator's emergency stop"

STAGES: tuple[str, ...] = ("PREFLIGHT", "SNAPSHOT", "ARM", "RAISE", "HOLD", "NOTCH", "VERIFY", "PROBE", "BACKOFF", "DONE", "ABORT")
_FROZEN_FLAGS = frozenset({"PEAK_HOLD_SUSPECTED", "FROZEN_LINES"})
_MAX_POLICY_LOG = 400

_RESTORE_RETRY_S = 0.5
_RESTORE_DEADLINE_S = 10.0
_RESTORE_READ_TIMEOUT_S = 1.0
_STOP_WAIT_S = 30.0
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
_NOTCH_INFLIGHT_WAIT_S = 3.0  # disarm lets a GEQ write already on its way finish (> connection timeout × retries)
_INTERVENTION_DB = 0.5  # the master read back this far from where we put it = somebody else moved it
_FRAME_STALL_S = 1.0    # no RTA frame for this long: do not raise (the detector is blind)
_FRAME_STALL_ABORT_S = 3.0  # ... and after this long, abort and back off
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
            "prefs_before": r.prefs_before, "settle_attempts": r.settle_attempts, "settle_ms": r.settle_ms}


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
    candidates: list[dict[str, Any]] = field(default_factory=list)   # live alert-worthy candidates (policy layer)
    alert: bool = False                                                # the bus scribble strip shows the alert colour
    policy: dict[str, Any] | None = None                               # {lf_edge_hz, window_low_hz, programme_present, tier_b_open, flags}

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value, "session_id": self.session_id, "bus": self.bus, "bus_name": self.bus_name,
            "master_db": self.master_db, "budget_left": self.budget_left, "candidate": self.candidate,
            "notches": [dict(n) for n in self.notches], "stage": self.stage, "started": self.started,
            "plan": self.plan, "rta_source": self.rta_source, "candidates": [dict(c) for c in self.candidates],
            "alert": self.alert, "policy": self.policy,
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
        if not _SESSION_ID_RE.match(sid):  # an id, never a path: '../x' must not read files outside the report dir
            raise CfsError("NOT_FOUND", f"no ring-out report {session_id!r} (not a report id)")
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
            lines.append("| Freq | GEQ band | Depth | Confidence | Detections | Tier |")
            lines.append("|---|---|---|---|---|---|")
            for n in notches:
                conf = n.get("confidence")
                lines.append(f"| {_fmt(n.get('freq_hz'), 'g', ' Hz')} | {n.get('band')} | {_fmt(n.get('depth_db'), '+.1f', ' dB')} | "
                             f"{_fmt(conf, '.2f')} | {n.get('detections')} | {'+'.join(n.get('tiers') or []) or 'A'} |")
        else:
            lines.append("none")
        existing = r.get("existing_cuts") or []
        if existing:
            lines.append("")
            lines.append("Pre-existing cuts: " + ", ".join(f"{_fmt(e.get('freq_hz'), 'g', ' Hz')} {_fmt(e.get('depth_db'), '+.1f', ' dB')}" for e in existing))
        warnings = (r.get("preflight") or {}).get("warnings") or []
        if warnings:
            lines.append("")
            lines.append("## Warnings (preflight and policy)")
            lines.append("")
            lines.extend(f"- {w}" for w in warnings)
        pol = r.get("policy") or {}
        if pol:
            lines.append("")
            lines.append("## Policy")
            lines.append("")
            le = pol.get("lf_edge") or {}
            lines.append(f"- Feedback window from {_fmt(le.get('window_low_hz'), 'g', ' Hz')}: "
                         + (f"LF edge {_fmt(le.get('lf_edge_hz'), 'g', ' Hz')} ({le.get('from')})" if le.get("lf_edge_hz") else str(le.get("from") or "detector default"))
                         + (" — lf_feedback_possible declared" if le.get("lf_feedback_possible") else ""))
            pp = pol.get("programme_present") or {}
            if pp.get("detected"):
                lines.append(f"- Programme detected at t+{pp.get('first_seen_s')} s" + (" (armed in silence)" if pp.get("armed_in_silence") else "")
                             + (f"; detector level reference refreshed at t+{pp.get('arm_reference_refreshed_s')} s" if pp.get("arm_reference_refreshed_s") is not None else ""))
            else:
                lines.append("- Programme: none detected" + (" (armed in silence)" if pp.get("armed_in_silence") else ""))
            tb = pol.get("tier_b") or []
            if tb:
                lines.append(f"- Tier-B (policy) steps: {len(tb)}")
                lines.append("")
                lines.append("| t (s) | Freq | GEQ band | Depth | Action | Verdict | Next |")
                lines.append("|---|---|---|---|---|---|---|")
                for e in tb[:40]:
                    lines.append(f"| {_fmt(e.get('t'), '.2f')} | {_fmt(e.get('freq_hz'), 'g', ' Hz')} | {e.get('band') if e.get('band') is not None else ''} | "
                                 f"{_fmt(e.get('depth_db'), '+.1f', ' dB')} | {e.get('action') or ''} | {e.get('verdict') or ''} | {e.get('next_action') or ''} |")
                lines.append("")
            ign = pol.get("ignore") or []
            if ign:
                lines.append("- Ignore-listed (a cut went through the line like programme through an EQ; the band stays where it is until "
                             "released by hand or by a later session): " + ", ".join(f"{_fmt(i.get('freq_hz'), 'g', ' Hz')} (band {i.get('band')}, {i.get('why')})" for i in ign))
            aa = pol.get("at_arm_suppressed") or []
            if aa:
                lines.append(f"- At-arm lines NOT cut in watch (alerted): " + ", ".join(f"{_fmt(a.get('freq_hz'), 'g', ' Hz')} {_fmt(a.get('level_db'), '.0f', ' dBFS')} "
                                                                                     f"{_fmt(a.get('prominence_db'), '.0f', ' dB prominent')}" for a in aa[:12]))
            al = pol.get("alerts") or []
            if al:
                on = sum(1 for a in al if a.get("alert") == "on")
                sc = pol.get("strip_color") or {}
                lines.append(f"- Alerts: {on} raised / {len(al) - on} cleared" + (f"; strip colour {sc.get('original')} → {sc.get('alert')} ×{sc.get('writes')}, "
                                                                               f"restored: {sc.get('restored')}" if sc.get("writes") else ""))
            fl = pol.get("flags_seen") or []
            if fl:
                lines.append("- Analyser flags: " + ", ".join(f"{f.get('flag')} (t+{f.get('first_seen_s')} s)" for f in fl))
            if pol.get("ballistics_reforced"):
                lines.append(f"- RTA ballistics re-forced at t+{(pol['ballistics_reforced'] or {}).get('t')} s (decay 0 / peak-hold OFF)")
            bo = pol.get("backoff_probes") or []
            if bo:
                lines.append("- Back-off probes: " + ", ".join(f"{_fmt(b.get('freq_hz'), 'g', ' Hz')} drop {_fmt(b.get('drop_db'), '.1f', ' dB')} → {b.get('verdict')}" for b in bo))
            for w in pol.get("warnings") or []:
                lines.append(f"- {w}")
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


@dataclass
class _TierB:
    """One policy engagement on a line (tier B, an at-arm line acted on, a back-off-probe cut): the live detector
    Candidate, what was written, and the verdict-driven follow-up (docs/CFS_POLICY.md §3)."""

    key: int                       # id() of the detector Candidate
    cand: Any                      # the live Candidate (kept referenced so the id stays unique)
    first_ts: float                # Candidate.first_ts at engagement: a re-born track is a different line
    freq_hz: float
    rta_band: int
    reason: str = "tier_b"         # tier_b | at_arm | backoff_probe
    level_db: float | None = None
    geq_band: int | None = None
    depth_db: float | None = None
    cuts: int = 0
    last_cut_ts: float | None = None
    last_cut_mono: float | None = None
    verdicts: list[str] = field(default_factory=list)
    held_since_ts: float | None = None
    state: str = "new"             # new | queued | pending (verdict awaited) | held | done
    outcome: str | None = None     # confirmed | false_cut | ended | ambiguous | max_depth | no_verdict | no_write | musical | not_base | aborted
    started_mono: float = 0.0

    def brief(self) -> dict[str, Any]:
        return {"freq_hz": round(self.freq_hz, 1), "rta_band": self.rta_band, "band": self.geq_band, "depth_db": self.depth_db,
                "cuts": self.cuts, "verdicts": list(self.verdicts), "state": self.state, "outcome": self.outcome, "reason": self.reason}


@dataclass
class _Policy:
    """Per-session state of the policy layer (``CfsManager._policy_frame``)."""

    cfg: CfsPolicyConfig
    lf_edge: LfEdge
    lf_feedback_possible: bool = False
    first_frame_ts: float | None = None
    programme_present: bool = False
    programme_first_seen_s: float | None = None
    armed_in_silence: bool | None = None
    arm_ref_refreshed_s: float | None = None
    emit_moderate_forced_off: bool = False
    tier_b: dict[int, _TierB] = field(default_factory=dict)
    tier_b_log: list[dict[str, Any]] = field(default_factory=list)
    tier_b_last_engage_mono: float | None = None
    actions: list[tuple[str, Any, str]] = field(default_factory=list)   # ring_out queue: (kind, obj, reason)
    ignore: list[dict[str, Any]] = field(default_factory=list)
    moderate_since: dict[int, tuple[Any, float]] = field(default_factory=dict)      # id(cand) -> (cand, ts MODERATE since)
    at_arm_suppressed: dict[int, dict[str, Any]] = field(default_factory=dict)     # id(cand) -> {"cand": cand, ...record}
    at_arm_log: list[dict[str, Any]] = field(default_factory=list)
    alerts_live: dict[int, dict[str, Any]] = field(default_factory=dict)
    alerts_log: list[dict[str, Any]] = field(default_factory=list)
    last_alert_live_mono: float | None = None
    color_orig: Any = None          # strip colour token found at arm (None: not read -> scribble-strip alerts off)
    color_is_alert: bool = False
    color_dirty: bool = False       # a colour write raised: the strip's state is unknown until the next successful write
    color_last_write_mono: float = -math.inf
    color_writes: int = 0
    color_restored: bool | None = None
    flags_seen: dict[str, float] = field(default_factory=dict)
    flags_log: list[dict[str, Any]] = field(default_factory=list)
    frozen_since_mono: float | None = None
    ballistics_reforced: dict[str, Any] | None = None
    backoff_done: dict[int, Any] = field(default_factory=dict)                    # id(cand) -> cand already probed
    backoff_log: list[dict[str, Any]] = field(default_factory=list)
    warned: set[str] = field(default_factory=set)


class _DeskGeqWriter:
    """:class:`~x32mcp.detector.GeqWriter` for one bus: ``(bus, band)`` → ``Desk.set_geq_band(slot,
    side, band, gain_db)`` after ``policy.validate_notch`` against the last gain it knows."""

    def __init__(self, desk: Desk, policy: Policy, bus: int, fx_slot: int, side: str, existing: dict[int, float],
                 fx_type: str | None = None, sides: Sequence[str] | None = None) -> None:
        self._desk = desk
        self._policy = policy
        self.bus = bus
        self.fx_slot = fx_slot
        self.side = side
        self.fx_type = fx_type  # validated by preflight; keeps the /fx/N type read off the detect→cut path
        # A stereo strip (Main LR) on a dual GEQ2 runs L through side A and R through side B: a notch "on
        # the PA" must be written to both sides or only the left stack is cut (REVIEW_REPORT §2 C1).
        self.sides: tuple[str, ...] = tuple(sides) if sides else (side,)
        self.gains: dict[int, float] = dict(existing)
        self.writes: list[tuple[int, int, float]] = []

    async def set_band_gain(self, bus: int, band: int, gain_db: float) -> None:
        if bus != self.bus:
            raise CfsError("BAD_ARGUMENT", f"notch for bus {bus} on a session for bus {self.bus}")
        self._policy.validate_notch(self.gains.get(band, 0.0), gain_db)  # cuts only, ≤ notch_max_db, never shallower
        for s in self.sides:
            await self._desk.set_geq_band(self.fx_slot, s, band, gain_db, fx_type=self.fx_type)
        self.gains[band] = float(gain_db)  # only once the datagram(s) have left
        self.writes.append((bus, band, float(gain_db)))

    def observe(self, band: int, gain_db: float) -> None:
        """The desk pushed a new value for ``band`` (someone moved it by hand): it is the truth now."""
        self.gains[int(band)] = float(gain_db)


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
    last_frame_mono: float | None = None  # loop time of the last consumed frame (liveness interlock)
    last_raise_mono: float | None = None  # loop time of the last master raise
    operator_override: bool = False       # the master was found somewhere we did not put it: hands off
    unsub_geq: Callable[[], None] | None = None
    frames: int = 0
    ended: float | None = None
    system_id: str | None = None
    expected_mics: list[int] | None = None
    warnings: list[str] = field(default_factory=list)
    policy: _Policy | None = None
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
        cfs_policy: CfsPolicyConfig | None = None,
    ) -> None:
        self._desk = desk
        self._policy = policy
        self._events = events
        self._reports = reports
        self._clock = clock
        self._d = getattr(desk, "descriptor", None) or desk._d  # Desk exposes neither yet
        self._conn = getattr(desk, "conn", None) or desk._conn
        self._cfg = detector_cfg if detector_cfg is not None else DetectorConfig.from_descriptor(self._d)
        self._pcfg = cfs_policy if cfs_policy is not None else CfsPolicyConfig.from_descriptor(self._d)
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
        self._notch_inflight: asyncio.Future | None = None  # set while a GEQ write is between propose and commit
        self._unsub_conn = events.subscribe(self._on_connection_event, types={"connection.state"})
        self._unsub_panic = events.subscribe(self._on_panic_event, types={"desk.panic.begin"})
        self._unsub_policy = events.subscribe(self._on_policy_event, types={"policy.show_mode"})

    # -- status ------------------------------------------------------------------------------------

    @property
    def frames(self) -> FrameSource:
        return self._frames

    @property
    def detector_cfg(self) -> DetectorConfig:
        return self._cfg

    @property
    def policy_cfg(self) -> CfsPolicyConfig:
        return self._pcfg

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
            candidates=([dict(a["brief"], alert=a["alert"]) for a in ses.policy.alerts_live.values()] if (live and ses.policy) else []),
            alert=bool(live and ses.policy and (ses.policy.color_is_alert or ses.policy.alerts_live)),
            policy=(self._policy_status(ses) if ses.policy else None),
        )

    # -- public API ------------------------------------------------------------------------------

    async def feedback_watch(self, bus: int | str | Target, *, notch_budget: int | None = None, patch: Any = None,
                             lf_feedback_possible: bool = False) -> dict[str, Any]:
        """Arm the detector on ``bus`` (Tier 1; the human drives the gain). Runs
        :func:`~x32mcp.provision.preflight` (``CfsError("PREFLIGHT_FAILED")`` with the blockers
        when it fails), points the RTA at the bus, starts the frames and the detector task.
        ``lf_feedback_possible`` declares a rig where LF feedback can happen (kick / floor-tom mic into
        subs, a drum fill): the detector's window opens to 40 Hz instead of the HPF-derived edge.
        Returns ``{session_id, bus, bus_name, preflight, rta, geq, lf_edge, state}``."""
        t = bus_target(bus)
        async with self._lock:
            self._require_idle()
            pf = await preflight(self._desk, t, patch=patch, reports=self._reports)
            if not pf.ok:
                raise CfsError("PREFLIGHT_FAILED", f"cannot arm on {t.label}: " + "; ".join(pf.blockers), preflight=pf.to_dict())
            ses = await self._open_session(CfsMode.WATCH, t, pf, notch_budget, lf_feedback_possible=bool(lf_feedback_possible))
            await self._arm(ses)
        log.info("feedback watch armed on %s (%s), budget %d", t.label, ses.session_id, ses.budget)
        return {
            "session_id": ses.session_id, "bus": ses.bus, "bus_name": ses.bus_name, "preflight": pf.to_dict(),
            "rta": _rta_dict(ses.rta), "geq": {"fx_slot": ses.fx_slot, "side": ses.side, "sel": ses.sel,
                                                "existing_cuts": [self._notch_dict(ses, n) for n in ses.nc.notches]},
            "lf_edge": self._lf_edge_dict(ses), "state": self.state.to_dict(),
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
        lf_feedback_possible: bool = False,
        _system_id: str | None = None,
        _expected_mics: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        """Run the ring-out state machine on ``bus`` to completion (or abort) and return the
        report (module doc). ``target_gain_db`` (default: the ceiling), ``step_db`` / ``dwell_ms``
        / ``notch_budget`` default to ``ringout.*`` / ``detector.notch_budget_default``;
        ``lf_feedback_possible`` as for :meth:`feedback_watch`."""
        self._policy.check_show_mode_allows("ring_out")
        t = bus_target(bus)
        ro = self._d.ringout
        step = float(ro.get("step_db", 1.0)) if step_db is None else float(step_db)
        if not step > 0:
            raise CfsError("BAD_ARGUMENT", f"step_db must be > 0, got {step_db!r}")
        if step < _MIN_STEP_DB:  # a finer step vanishes in the 0.1 dB read-back and reads as a clamp
            raise CfsError("BAD_ARGUMENT", f"step_db must be >= {_MIN_STEP_DB:g} dB (the fader report grid), got {step_db!r}")
        max_step = min(float(ro.get("max_step_db", 3.0)), self._policy.relative_limit_db)
        if step > max_step + 1e-9:  # a bigger step walks past the point where a ring declares itself
            raise CfsError("BAD_ARGUMENT", f"step_db {step:g} exceeds the {max_step:g} dB ring-out step limit "
                                           "(ringout.max_step_db / the relative move limit)")
        dwell = int(ro.get("dwell_ms", 1500)) if dwell_ms is None else int(dwell_ms)
        if dwell < 0:
            raise CfsError("BAD_ARGUMENT", f"dwell_ms must be >= 0, got {dwell_ms!r}")
        min_dwell = int(ro.get("min_dwell_ms", 0) or 0)
        dwell_note: str | None = None
        if dwell < min_dwell:
            # Not refused (the intent is clear) but never honoured: the detector needs its persistence
            # window at every level before the next dB goes in.
            dwell_note = f"dwell_ms {dwell} raised to the {min_dwell} ms floor (ringout.min_dwell_ms): the detector needs that long per step"
            dwell = min_dwell
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
            ses = await self._open_session(CfsMode.RINGOUT, t, pf, notch_budget, lf_feedback_possible=bool(lf_feedback_possible))
            ses.system_id = _system_id
            ses.step_db, ses.dwell_ms = step, dwell
            ses.target_db = target
            if dwell_note:
                ses.warnings.append(dwell_note)
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
                        notch_budget=st.get("notch_budget"), patch=st.get("patch"), lf_feedback_possible=bool(st.get("lf_feedback_possible")),
                        _system_id=sid, _expected_mics=st.get("mics"),
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
        self._unsub_panic()
        self._unsub_policy()
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

    async def _open_session(self, mode: CfsMode, t: Target, pf: Preflight, notch_budget: int | None, *,
                            lf_feedback_possible: bool = False) -> _Session:
        cfg = self._cfg
        budget = cfg.notch_budget_default if notch_budget is None else int(notch_budget)
        if budget < 0:
            raise CfsError("BAD_ARGUMENT", f"notch_budget must be >= 0, got {notch_budget!r}")
        ins = pf.geq.insert
        assert ins is not None and ins.fx_slot is not None and ins.side is not None  # preflight guarantees it
        existing = {i + 1: float(g) for i, g in enumerate(pf.geq.bands_db or []) if g is not None}
        bus_int = 0 if t.family == "main" else int(t.index)
        nc = NotchController(cfg, self._geq_hz, self._policy.validate_notch, budget=budget, existing=existing)
        sides = geq_sides_for(t.key, ins.fx_type, set(self._d.geq.get("fx_types_dual", ())), ins.side)
        writer = _DeskGeqWriter(self._desk, self._policy, bus_int, ins.fx_slot, ins.side, existing, fx_type=ins.fx_type, sides=sides)
        # (no arm-time 'noise floor calibration': the detector's per-band baseline and arm-time spectrum reference
        # replace the absolute min_level_db gate it used to raise, and sampling 2 s here delayed arming under the
        # not-yet-forced RTA ballistics)
        # detector mode: ring_out owns the gain (wider LF window, active probe via note_gain_step); watch does not.
        # LF edge of the feedback window (P6): from the included mics' high-pass filters (~0.7 x the lowest HPF corner,
        # 100 Hz for a mic without one, floored at 60 Hz) unless the operator declared an LF-capable rig
        # (lf_feedback_possible: kick / floor-tom mic into subs), which opens the window to the detector's 40 Hz.
        lf_edge = await self._lf_edge(pf)
        det = FeedbackDetector(cfg, self._band_hz, mode="ringout" if mode is CfsMode.RINGOUT else "watch",
                               lf_feedback_possible=True if lf_feedback_possible else None,
                               lf_edge_hz=None if lf_feedback_possible else lf_edge.hz)
        start = float(pf.master_db)
        ses = _Session(
            session_id=self._new_id(mode.value, t), mode=mode, target=t, bus=bus_label(t), bus_int=bus_int, bus_name=pf.bus_name,
            started=self._clock(), preflight=pf, fx_slot=ins.fx_slot, side=ins.side, sel=ins.sel, nc=nc, det=det, writer=writer,
            budget=budget, start_master_db=start, master_db=start, max_master_db=start, existing=existing,
        )
        ses.policy = _Policy(cfg=self._pcfg, lf_edge=lf_edge, lf_feedback_possible=bool(lf_feedback_possible))
        ses.warnings.extend(pf.warnings)
        if mode is CfsMode.RINGOUT and det.cfg.ringout_emit_moderate:
            ses.warnings.append("detector.ringout_emit_moderate is ON: MODERATE lines will be cut after K2 unless programme is detected "
                                "(the policy layer then forces it off)")
        log.info("CFS² %s: feedback window from %s Hz (%s)%s", ses.session_id, format(det.cfg.window_low_hz, "g"), lf_edge.source,
                 " [lf_feedback_possible declared]" if lf_feedback_possible else "")
        return ses

    async def _lf_edge(self, pf: Preflight) -> LfEdge:
        """Read ``preamp/hpon`` + ``preamp/hpf`` of the included mics (one scoped ``/node`` sweep of their
        ``/ch/NN/preamp`` sections) and derive the session's LF edge (:func:`~x32mcp.cfs_policy.lf_edge_from_hpfs`)."""
        mics = [m for m in pf.included_mics if 1 <= int(m.ch) <= 32]
        if not mics:
            return lf_edge_from_hpfs([], self._pcfg.lf_edge)
        paths = [f"/ch/{int(m.ch):02d}/preamp" for m in mics]
        sections: dict[str, dict[str, Any]] = {}
        try:
            state = await self._desk.dump(sections=paths)
            sections = dict(state.sections)
        except (DeskError, X32ConnectionError, OSError, ValueError) as e:
            log.warning("reading the open mics' HPF settings failed (%s): LF edge falls back to the no-HPF default", e)
        hpfs: list[MicHpf] = []
        for m, path in zip(mics, paths):
            sec = sections.get(path) or {}
            on = sec.get("preamp/hpon")
            hz = sec.get("preamp/hpf")
            hpfs.append(MicHpf(int(m.ch), bool(on) if on is not None else None,
                               float(hz) if isinstance(hz, (int, float)) and not isinstance(hz, bool) else None))
        return lf_edge_from_hpfs(hpfs, self._pcfg.lf_edge)

    def _lf_edge_dict(self, ses: _Session) -> dict[str, Any]:
        pol = ses.policy
        d = pol.lf_edge.to_dict() if pol is not None else {"lf_edge_hz": None, "from": None, "mics": []}
        d["lf_feedback_possible"] = bool(pol and pol.lf_feedback_possible)
        d["window_low_hz"] = float(ses.det.cfg.window_low_hz)
        return d

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
            if not ses.rta.verified:  # the desk did not confirm it re-pointed the analyser: once more, then refuse
                log.warning("RTA source read-back for %s did not verify (%s); retrying once", ses.target.label, ses.rta.stat_actual)
                ses.rta = await set_rta_source(self._conn, self._d, ses.target)
        except RtaSourceError as e:
            raise CfsError("BAD_ARGUMENT", str(e)) from None
        except NotConnected as e:
            raise CfsError("NOT_CONNECTED", str(e)) from None
        self._desk.invalidate("/-prefs/rta")
        if not ses.rta.verified:
            # Every notch and every raise downstream assumes /meters/15 carries THIS bus post-EQ. If the desk
            # says otherwise (or says nothing), the detector would be scoring some other signal and writing
            # its conclusions into this bus's GEQ. Do not arm on a guess.
            raise CfsError("RTA_UNVERIFIED",
                           f"the desk did not confirm the RTA now analyses {ses.target.label} "
                           f"({self._d.rta.get('stat_param', '/-stat/rtasource')} = {ses.rta.stat_actual}, expected {ses.rta.stat_expected}); "
                           "not arming — check the METERS → RTA page on the console and retry",
                           rta=_rta_dict(ses.rta))
        await self._read_strip_color(ses)   # the scribble-strip alert needs the colour to restore; read it before anything can alert
        self._ses = ses
        self._queue = asyncio.Queue(maxsize=16)
        self._bad_frame_logged = False
        # A GEQ band the engineer moves by hand during the session arrives as an /xremote push: adopt it,
        # so the next proposal deepens from the desk's value and never writes a hand-made cut shallower.
        ses.unsub_geq = self._conn.on_update(lambda address, args, _s=ses: self._on_desk_push(_s, address, args))
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
        ses = self._ses
        if ses is not None and ses.unsub_geq is not None:
            ses.unsub_geq()
            ses.unsub_geq = None
        try:
            await self._frames.stop()
        except Exception:
            log.exception("frame source stop failed")
        task, self._consumer = self._consumer, None
        self._queue = None
        inflight = self._notch_inflight
        if inflight is not None and not inflight.done():
            # A cut is between propose and commit: the ring was real, so let the write land (bounded by
            # the connection's own timeout × retries) rather than cancel it half way. Either way the
            # controller stays consistent, because _notch commits only after the write returned.
            log.info("disarm: waiting for the in-flight notch write to finish")
            try:
                await asyncio.wait_for(asyncio.shield(inflight), timeout=_NOTCH_INFLIGHT_WAIT_S)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                if (cur := asyncio.current_task()) is not None and cur.cancelling():
                    raise
            except Exception:
                pass
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
        # A session must never leave the bus strip in the alert colour (the consumer that drives it is stopped now).
        await self._restore_strip_color(ses)
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
                ses.last_frame_mono = asyncio.get_running_loop().time()
                ses.frame_event.set()
                dets = ses.det.feed(frame.values, frame.ts)
                self._publish_candidate(ses)
                for det in dets:
                    entry = {**det.to_dict(), "master_db": _db1(ses.master_db)}
                    ses.detections.append(entry)
                    if ses.mode is CfsMode.WATCH and self._at_arm_suppressed(ses, det, frame.ts):
                        entry["suppressed"] = "at_arm"   # watch: an established-at-arm line that is neither LOUD nor >= 30 dB prominent
                        continue                         # is alerted (and left to tier B), not cut on the passive observation alone
                    if ses.first_feedback_master_db is None:
                        ses.first_feedback_master_db = ses.master_db
                    if ses.mode is CfsMode.WATCH:
                        await self._notch(ses, det)
                    else:
                        ses.pending.append(det)
                        ses.wake.set()
                await self._policy_frame(ses, frame.ts)
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
        tiers = sorted({str(e.get("tier")) for e in ses.notch_log if e.get("band") == n.band and e.get("tier")})
        if tiers:
            d["tiers"] = tiers   # "A" = detector cuts, "B" = policy cuts landed on this band during the session
        return d

    async def _notch(self, ses: _Session, det: Detection, *, tier: str = "A", policy: str | None = None,
                     cand: Candidate | None = None) -> Notch | None:
        """Propose, WRITE, then commit the next cut for ``det``; None when nothing can be done. ``tier`` "A" = the
        detector's own verdict, "B" = a policy cut (``policy`` names the rule: tier_b / tier_b_held / tier_b_insufficient /
        backoff_probe; ``cand`` is the live detector Candidate it acts on, told to the detector via ``note_emission`` so
        the post-cut verdict treats it as the cut line). Both tags travel in the notch log, the report and ``cfs.notch``.

        Two-phase on purpose: nothing is recorded (gains, notch list, budget, report) until the datagram
        has left. The write path can suspend — the ``/fx/N`` type lookup once the node cache expired, the
        rate limiter, a first-write snapshot — and a stop(), a read timeout or RATE_LIMITED landing in that
        window used to leave ``cfs_status``/the report showing a notch the GEQ never received and make the
        next detection "deepen" a flat band straight to −6 dB. A failed or interrupted write is now simply
        not committed; the detector re-emits the band after ``cooldown_s`` and the same first step is
        proposed again."""
        try:
            plan = ses.nc.propose(det, ses.bus_int, ses.session_id)
        except PolicyError as e:
            log.error("notch plan refused by policy: %s", e)
            return None
        if plan is None:
            log.info("no notch for %.0f Hz: budget spent or band at %+.0f dB", det.freq_hz, self._cfg.notch_max_db)
            return None
        inflight = asyncio.get_running_loop().create_future()
        self._notch_inflight = inflight
        try:
            await ses.writer.set_band_gain(plan.bus, plan.band, plan.new_db)
        except (DeskError, PolicyError, X32ConnectionError, CfsError) as e:
            self._desk.invalidate(f"/fx/{ses.fx_slot}")  # our copy of the slot is suspect; the desk's word next time
            log.warning("notch write failed (%s): band %d %+.1f dB NOT applied and not recorded: %s",
                        ses.session_id, plan.band, plan.new_db, e)
            self._events.publish("cfs.notch_failed", session_id=ses.session_id, bus=ses.bus, band=plan.band,
                                 depth_db=plan.new_db, error=str(e))
            if isinstance(e, DeskError) and e.code == "NOT_CONNECTED" or isinstance(e, NotConnected):
                await self._lose_connection(ses, f"desk write failed: {e}")
            return None
        except BaseException:
            # Cancelled (stop/close/tool timeout) while the write path was suspended. Every await on that
            # path sits before conn.set(), so the datagram did not leave: not committing is the truth.
            log.warning("notch write interrupted (%s): band %d %+.1f dB not applied and not recorded",
                        ses.session_id, plan.band, plan.new_db)
            raise
        finally:
            if self._notch_inflight is inflight:
                self._notch_inflight = None
            if not inflight.done():
                inflight.set_result(None)
        n = ses.nc.commit(plan)
        cut_ts = ses.last_ts if ses.last_ts is not None else self._clock()
        if cand is not None:
            # a policy cut: the detector did not emit this line itself; record the emission on the track so its verdict
            # machinery treats it as THE cut line (and any later re-emission needs evidence gathered since)
            ses.det.note_emission(cand, cut_ts, reason=policy or "policy")
        # tell the detector the cut landed: it verifies the line's response against the bell expected at the line and
        # classifies it (confirmed / insufficient / held / false_cut) -- which gates any deepening of this band
        ses.det.note_cut(freq_hz=n.freq_hz, depth_db=n.depth_db, ts=cut_ts)
        ses.confidence[n.band] = max(ses.confidence.get(n.band, 0.0), det.confidence)
        item = self._notch_dict(ses, n)
        item.update(rta_band=det.band, rta_freq_hz=det.freq_hz, level_db=det.level_db, master_db=_db1(ses.master_db),
                    tier=tier, policy=policy, reasons=list(det.reasons))
        ses.notch_log.append(item)
        self._events.publish("cfs.notch", **item)
        log.info("notch: %s band %d (%g Hz) -> %+.1f dB (ring at %.0f Hz, conf %.2f, tier %s%s)", ses.target.label, n.band, n.freq_hz,
                 n.depth_db, det.freq_hz, det.confidence, tier, f" {policy}" if policy else "")
        return n

    # -- policy layer (docs/CFS_POLICY.md) -------------------------------------------------------
    #
    # Everything below consumes the detector's hooks (candidates / cut verdicts / flags / programme_present) and
    # turns them into desk actions under the session's normal write discipline. It runs from ``_policy_frame``
    # after every ``feed()``; in ring_out the writes it decides on are queued to the ring-out task
    # (``_run_policy_action``) so every master / GEQ write of a run stays in one task.

    @staticmethod
    def _t_rel(pol: _Policy, ts: float | None) -> float:
        if ts is None or pol.first_frame_ts is None:
            return 0.0
        return round(float(ts) - pol.first_frame_ts, 2)

    def _policy_status(self, ses: _Session) -> dict[str, Any]:
        pol = ses.policy
        assert pol is not None
        return {
            "lf_edge_hz": pol.lf_edge.hz, "lf_edge_from": pol.lf_edge.source, "window_low_hz": float(ses.det.cfg.window_low_hz),
            "lf_feedback_possible": pol.lf_feedback_possible, "programme_present": pol.programme_present,
            "tier_b_open": [tb.brief() for tb in pol.tier_b.values() if tb.state != "done"],
            "ignore": [dict(i) for i in pol.ignore], "flags": sorted(ses.det.flags), "strip_alert": pol.color_is_alert,
        }

    def _policy_warn(self, ses: _Session, pol: _Policy, key: str, text: str) -> None:
        """A policy finding for the report's warnings (once per ``key``) + a ``cfs.policy`` event."""
        if key in pol.warned:
            return
        pol.warned.add(key)
        ses.warnings.append(text)
        log.warning("CFS² %s: %s", ses.session_id, text)
        self._events.publish("cfs.policy", session_id=ses.session_id, bus=ses.bus, what="warning", key=key, text=text)

    def _policy_report(self, ses: _Session) -> dict[str, Any] | None:
        pol = ses.policy
        if pol is None:
            return None
        return {
            "config": pol.cfg.to_dict(),
            "lf_edge": self._lf_edge_dict(ses),
            "lf_feedback_possible": pol.lf_feedback_possible,
            "programme_present": {"detected": pol.programme_present, "first_seen_s": pol.programme_first_seen_s,
                                  "armed_in_silence": pol.armed_in_silence, "arm_reference_refreshed_s": pol.arm_ref_refreshed_s,
                                  "emit_moderate_forced_off": pol.emit_moderate_forced_off},
            "tier_b": list(pol.tier_b_log), "ignore": [dict(i) for i in pol.ignore],
            "alerts": list(pol.alerts_log),
            "strip_color": {"original": pol.color_orig, "alert": pol.cfg.alerts.color, "writes": pol.color_writes, "restored": pol.color_restored},
            "at_arm_suppressed": list(pol.at_arm_log),
            "flags_seen": [{"flag": f, "first_seen_s": t} for f, t in pol.flags_seen.items()],
            "ballistics_reforced": pol.ballistics_reforced, "backoff_probes": list(pol.backoff_log),
        }

    # -- arming: strip colour ----------------------------------------------------------------------

    async def _read_strip_color(self, ses: _Session) -> None:
        """Remember the bus strip's ``config/color`` so the scribble-strip alert can be undone (item 4b)."""
        pol = ses.policy
        if pol is None or not pol.cfg.alerts.scribble_strip:
            return
        try:
            colours = tuple(self._d.enum("color"))
        except Exception:
            colours = ()
        if colours and pol.cfg.alerts.color not in colours:
            ses.warnings.append(f"cfs_policy.alerts.color {pol.cfg.alerts.color!r} is not a colour of this desk ({', '.join(colours)}): "
                                "scribble-strip alerts are off for this session")
            return
        path = f"{ses.target.osc_prefix}/config"
        tok: Any = None
        try:
            state = await self._desk.dump(sections=[path])
            tok = (state.sections.get(path) or {}).get("config/color")
        except (DeskError, X32ConnectionError, OSError, ValueError) as e:
            log.warning("reading %s's strip colour failed: %s", ses.target.label, e)
        if tok is None:
            ses.warnings.append(f"could not read {ses.target.label}'s scribble-strip colour: colour alerts are off for this session")
            return
        pol.color_orig = tok
        if str(tok) == pol.cfg.alerts.color:
            ses.warnings.append(f"{ses.target.label}'s strip already shows the alert colour {tok}: candidate alerts will not be visible as a colour change")

    def _on_color_push(self, ses: _Session, args: Any) -> None:
        """The engineer re-coloured the strip during the session: that is the colour to give back, not the arm-time one."""
        pol = ses.policy
        if pol is None or pol.color_orig is None:
            return
        try:
            tok = self._d.param(ses.target.family, "config/color").to_value(args[0])
        except Exception:
            return
        if tok is None or str(tok) == pol.cfg.alerts.color:
            return
        if tok != pol.color_orig:
            log.info("CFS² %s: %s's strip colour was changed on the desk to %s; adopting it as the colour to restore", ses.session_id, ses.target.label, tok)
            pol.color_orig = tok
        pol.color_is_alert = False   # whatever we showed, the strip now shows the operator's colour

    async def _write_strip_color(self, ses: _Session, pol: _Policy, *, alert: bool) -> bool:
        color = pol.cfg.alerts.color if alert else pol.color_orig
        pol.color_last_write_mono = asyncio.get_running_loop().time()
        try:
            await self._desk.label(ses.target, color=color)   # Tier 1, rate-limited like any write
        except (DeskError, PolicyError, X32ConnectionError) as e:
            pol.color_dirty = True
            log.warning("CFS² %s: strip colour write (%s) failed: %s", ses.session_id, color, e)
            return False
        pol.color_is_alert = alert
        pol.color_dirty = False
        pol.color_writes += 1
        self._events.publish("cfs.alert", session_id=ses.session_id, bus=ses.bus, on=alert, color=color,
                             candidates=[dict(r["brief"], alert=r["alert"]) for r in pol.alerts_live.values()])
        log.info("CFS² %s: %s strip -> %s (%s)", ses.session_id, ses.target.label, color, "ALERT" if alert else "restored")
        return True

    async def _restore_strip_color(self, ses: _Session) -> None:
        """Give the strip its colour back. Called from ``_finish`` (every exit path: stop, DONE, ABORT, panic, unwind)."""
        pol = ses.policy
        if pol is None or pol.color_orig is None:
            return
        if not (pol.color_is_alert or pol.color_dirty):
            if pol.color_writes:
                pol.color_restored = True
            return
        for attempt in range(3):
            try:
                await self._desk.label(ses.target, color=pol.color_orig)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("CFS² %s: restoring %s's strip colour failed (attempt %d): %s", ses.session_id, ses.target.label, attempt + 1, e)
                await asyncio.sleep(0.3 * (attempt + 1))
                continue
            pol.color_is_alert = False
            pol.color_dirty = False
            pol.color_writes += 1
            pol.color_restored = True
            self._events.publish("cfs.alert", session_id=ses.session_id, bus=ses.bus, on=False, color=pol.color_orig, candidates=[], restored=True)
            log.info("CFS² %s: %s strip colour restored to %s", ses.session_id, ses.target.label, pol.color_orig)
            return
        pol.color_restored = False
        msg = f"could not restore {ses.target.label}'s strip colour to {pol.color_orig}: it may still show the alert colour {pol.cfg.alerts.color}"
        ses.warnings.append(msg)
        log.error("CFS² %s: %s", ses.session_id, msg)

    # -- per frame ---------------------------------------------------------------------------------

    async def _policy_frame(self, ses: _Session, ts: float) -> None:
        """Run the policy layer on the detector's state after this frame's ``feed()``."""
        pol = ses.policy
        if pol is None:
            return
        if pol.first_frame_ts is None:
            pol.first_frame_ts = ts
        now = asyncio.get_running_loop().time()
        t_rel = ts - pol.first_frame_ts
        det = ses.det
        # per-track bookkeeping is keyed by id(Candidate) and validated by identity: a dropped track's id can be reused
        live = {id(c): c for c in det.candidates}
        for key in [k for k, v in pol.moderate_since.items() if live.get(k) is not v[0]]:
            del pol.moderate_since[key]
        for key in [k for k, v in pol.at_arm_suppressed.items() if live.get(k) is not v.get("cand")]:
            del pol.at_arm_suppressed[key]
        for key in [k for k, v in pol.backoff_done.items() if live.get(k) is not v]:
            del pol.backoff_done[key]
        try:
            await self._policy_flags(ses, pol, det, ts, t_rel, now)
            self._policy_programme(ses, pol, det, ts, t_rel)
            if pol.cfg.tier_b.enabled:
                await self._policy_tier_b(ses, pol, det, ts, now)
            if ses.mode is CfsMode.RINGOUT and pol.cfg.backoff_probe.enabled:
                self._policy_backoff_scan(ses, pol, det)
            await self._policy_alerts(ses, pol, det, ts, now)
        except asyncio.CancelledError:
            raise
        except (DeskError, PolicyError, X32ConnectionError, CfsError) as e:
            log.warning("CFS² %s: policy step failed on a frame: %s", ses.session_id, e)

    # -- item 6: analyser flags --------------------------------------------------------------------

    async def _policy_flags(self, ses: _Session, pol: _Policy, det: FeedbackDetector, ts: float, t_rel: float, now: float) -> None:
        flags = set(det.flags)
        for f in sorted(flags):
            if f in pol.flags_seen:
                continue
            pol.flags_seen[f] = round(t_rel, 2)
            pol.flags_log.append({"t": round(t_rel, 2), "flag": f})
            self._events.publish("cfs.policy", session_id=ses.session_id, bus=ses.bus, what="flag", flag=f, t=round(t_rel, 2))
            if f == "SLOW_RELEASE":
                self._policy_warn(ses, pol, "slow_release",
                                  f"RTA display release measured at {det.release_db_per_s or 0:.0f} dB/s (< {det.cfg.slow_release_db_per_s:g}): the analyser's "
                                  "decay looks long although it was forced at arm; growth evidence is restricted to strongly prominent lines")
            elif f == "HOT_SPECTRUM":
                self._policy_warn(ses, pol, "hot_spectrum",
                                  f"the RTA display is hot (arm-time p95 {det.arm_p95_db} dBFS): a display gain offset or a bus at its limiter; "
                                  "absolute levels carry little information, LOUD needs a line above everything the arm window showed")
        frozen = flags & _FROZEN_FLAGS
        if not frozen:
            pol.frozen_since_mono = None
            return
        if pol.frozen_since_mono is None:
            pol.frozen_since_mono = now
        if ses.abort_reason is not None:
            return
        held_for = now - pol.frozen_since_mono
        if pol.cfg.reforce_ballistics_on_freeze and pol.ballistics_reforced is None:
            if held_for < pol.cfg.frozen_reforce_s:
                return                                     # a live line's skirt can repeat its code for a few frames; a display stays frozen
            # the prefs were forced at arm, so a frozen display now means somebody changed them on the console: force them again once
            pol.ballistics_reforced = {"t": round(t_rel, 2), "flags": sorted(frozen), "result": None}
            res = await force_rta_ballistics(self._conn, self._d)
            pol.ballistics_reforced["result"] = res
            self._desk.invalidate("/-prefs/rta")
            pol.frozen_since_mono = asyncio.get_running_loop().time()   # the re-forced display gets frozen_abort_s to come alive
            self._policy_warn(ses, pol, "frozen",
                              f"the RTA display looked frozen at t+{t_rel:.1f} s ({', '.join(sorted(frozen))}): decay / peak-hold were re-forced "
                              f"(found decay={res.get('decay_before')!r}, peakhold={res.get('peakhold_before')!r})")
            self._events.publish("cfs.policy", session_id=ses.session_id, bus=ses.bus, what="ballistics_reforced", t=round(t_rel, 2),
                                 written=res.get("written"), failed=res.get("failed"))
            return
        if held_for >= pol.cfg.frozen_abort_s:
            reason = (f"the RTA display is frozen ({', '.join(sorted(frozen))} for {held_for:.0f} s"
                      + (" after re-forcing decay / peak-hold" if pol.ballistics_reforced else "")
                      + "): the detector cannot see through a frozen display, so the session stops")
            log.warning("CFS² %s: %s", ses.session_id, reason)
            self._abort_from_callback(ses, reason)

    # -- item 2: the ring-out contract check -------------------------------------------------------

    def _policy_programme(self, ses: _Session, pol: _Policy, det: FeedbackDetector, ts: float, t_rel: float) -> None:
        """G8: the ring-out's 'stage is quiet' contract, evaluated on every frame (the detector judges its own 2 s window): the
        session latches the first detection -- during the first ``programme_check_s`` (before and during the first steps) or at
        any later rising edge -- warns (ring_out) and, in a watch armed in silence, re-opens the detector's level reference."""
        cfg = pol.cfg
        pp = bool(det.programme_present())
        first = pp and not pol.programme_present
        if first:
            pol.programme_present = True
            pol.programme_first_seen_s = round(t_rel, 2)
            self._events.publish("cfs.programme_present", session_id=ses.session_id, bus=ses.bus, mode=ses.mode.value, t=round(t_rel, 2))
            if ses.mode is CfsMode.RINGOUT:
                self._policy_warn(ses, pol, "programme",
                                  f"programme detected on {ses.target.label} during the ring-out (t+{t_rel:.1f} s): the 'stage is quiet' contract "
                                  "does not hold, so MODERATE lines are not cut, only probe-confirmed / loud / rising lines (and loud-ish lines by tier B)")
                if det.cfg.ringout_emit_moderate:
                    # never rely on the default: MODERATE-after-K2 emission is only safe in a programme-free room
                    det.cfg = dataclasses.replace(det.cfg, ringout_emit_moderate=False)
                    pol.emit_moderate_forced_off = True
                    log.warning("CFS² %s: detector.ringout_emit_moderate forced OFF (programme present)", ses.session_id)
            else:
                log.info("CFS² %s: programme present on %s (t+%.1f s)", ses.session_id, ses.target.label, t_rel)
            if (ses.mode is CfsMode.WATCH and cfg.refresh_arm_reference_on_programme and pol.arm_ref_refreshed_s is None
                    and t_rel > float(det.cfg.arm_baseline_s)):
                # the detector took its level reference (LOUD / loud-ish legs) over the first arm_baseline_s, before this programme
                # was there: re-open it so the show is not judged against a silent-room snapshot [DETECTOR §9]
                det.refresh_arm_reference()
                pol.arm_ref_refreshed_s = round(t_rel, 2)
                log.info("CFS² %s: programme started after the arm reference window (t+%.1f s): detector level reference re-opened",
                         ses.session_id, t_rel)
                self._events.publish("cfs.policy", session_id=ses.session_id, bus=ses.bus, what="arm_reference_refreshed", t=round(t_rel, 2))
        if pol.armed_in_silence is None and t_rel >= cfg.programme_check_s:
            pol.armed_in_silence = not pol.programme_present

    # -- item 5: AT-ARM in watch -------------------------------------------------------------------

    def _candidate_for(self, ses: _Session, band: int) -> Candidate | None:
        best: Candidate | None = None
        for c in ses.det.candidates:
            if c.misses or abs(int(c.band) - int(band)) > 1:
                continue
            if best is None or abs(int(c.band) - int(band)) < abs(int(best.band) - int(band)):
                best = c
        return best

    def _at_arm_suppressed(self, ses: _Session, det: Detection, ts: float) -> bool:
        """True when a watch Detection rests on the at-arm observation alone and is neither LOUD nor prominent enough to
        cut (:func:`~x32mcp.cfs_policy.at_arm_cut_allowed`): it is recorded, alerted and left to tier B instead."""
        pol = ses.policy
        if pol is None or "established_at_arm" not in det.reasons:
            return False
        if at_arm_cut_allowed(det, min_prominence_db=pol.cfg.at_arm_watch_min_prominence_db):
            return False
        if pol.first_frame_ts is None:
            pol.first_frame_ts = ts
        rec = {"t": self._t_rel(pol, ts), "freq_hz": round(det.freq_hz, 1), "rta_band": det.band, "level_db": round(det.level_db, 1),
               "prominence_db": round(det.prominence_db, 1), "reasons": list(det.reasons)}
        c = self._candidate_for(ses, det.band)
        first = c is None or id(c) not in pol.at_arm_suppressed
        if c is not None:
            pol.at_arm_suppressed[id(c)] = {"cand": c, **rec}
        if first:
            pol.at_arm_log.append(rec)
            log.info("CFS² %s: line at %.0f Hz (%.1f dBFS, %.0f dB prominent) was sounding when the watch armed: NOT cut "
                     "(neither LOUD nor >= %g dB prominent) — alerting", ses.session_id, det.freq_hz, det.level_db, det.prominence_db,
                     pol.cfg.at_arm_watch_min_prominence_db)
            self._events.publish("cfs.policy", session_id=ses.session_id, bus=ses.bus, what="at_arm_not_cut", **rec)
        return True

    # -- item 3: tier B ----------------------------------------------------------------------------

    def _tier_b_log(self, ses: _Session, pol: _Policy, tb: _TierB, ts: float | None, *, action: str, verdict: str | None = None,
                    next_action: str | None = None, depth_db: float | None = None) -> None:
        entry = {"t": self._t_rel(pol, ts), "freq_hz": round(tb.freq_hz, 1), "rta_band": tb.rta_band, "band": tb.geq_band,
                 "depth_db": tb.depth_db if depth_db is None else depth_db, "action": action, "verdict": verdict, "next_action": next_action,
                 "tier": "B", "reason": tb.reason}
        if len(pol.tier_b_log) < _MAX_POLICY_LOG:
            pol.tier_b_log.append(entry)
        self._events.publish("cfs.policy", session_id=ses.session_id, bus=ses.bus, what="tier_b", **entry)

    def _tier_b_ignored(self, pol: _Policy, c: Candidate, geq_band: int) -> bool:
        for i in pol.ignore:
            if i.get("band") == geq_band or abs(int(i.get("rta_band", -99)) - int(c.band)) <= 1:
                return True
        return False

    def _tier_b_ignore(self, ses: _Session, pol: _Policy, tb: _TierB, ts: float | None, why: str) -> None:
        entry = {"t": self._t_rel(pol, ts), "freq_hz": round(tb.freq_hz, 1), "rta_band": tb.rta_band, "band": tb.geq_band,
                 "depth_db": tb.depth_db, "why": why}
        pol.ignore.append(entry)
        log.info("CFS² %s: %.0f Hz ignore-listed for policy cuts (%s); GEQ band %s stays at %s dB until released by hand or a later session",
                 ses.session_id, tb.freq_hz, why, tb.geq_band, tb.depth_db)
        self._events.publish("cfs.policy", session_id=ses.session_id, bus=ses.bus, what="ignore_listed", **entry)

    def _tier_b_finish(self, ses: _Session, pol: _Policy, tb: _TierB, outcome: str, ts: float | None) -> None:
        if tb.state == "done":
            return
        was = tb.state
        tb.state = "done"
        tb.outcome = outcome
        if outcome == "ended" and was == "held":
            # the line held after the cut and then switched itself off: a note went through the EQ (a howl does not end on its own)
            self._tier_b_ignore(ses, pol, tb, ts, "line ended by itself after holding through the cut")
        text = {
            "confirmed": "none: the cut removed the line (regrowth is the detector's business)",
            "false_cut": "ignore-listed: the line ended by itself; the cut stays (never written shallower)",
            "ended": "ignore-listed: the line ended by itself after holding; the cut stays",
            "ambiguous": "none: report only", "max_depth": "none: band at the deepest allowed cut / budget spent",
            "no_write": "none: the write did not happen", "no_verdict": "none: no verdict arrived", "musical": "none: the line turned musical",
            "not_base": "none: the line no longer qualifies", "gone": "none: the line is gone", "aborted": "none: session aborting",
        }.get(outcome, "none")
        self._tier_b_log(ses, pol, tb, ts, action="end", verdict=tb.verdicts[-1] if tb.verdicts else None, next_action=text)

    async def _tier_b_dispatch(self, ses: _Session, pol: _Policy, tb: _TierB, reason: str) -> None:
        if ses.mode is CfsMode.RINGOUT:
            tb.state = "queued"
            pol.actions.append(("tier_b", tb, reason))
            ses.wake.set()
        else:
            await self._tier_b_cut(ses, pol, tb, reason)

    async def _tier_b_cut(self, ses: _Session, pol: _Policy, tb: _TierB, reason: str) -> Notch | None:
        """One policy cut (-3 dB step) on the engaged line through the normal ``_notch`` path (propose -> write -> commit ->
        note_emission -> note_cut). The verdict the detector files afterwards drives the follow-up (``_policy_tier_b``)."""
        c = tb.cand
        ts = ses.last_ts if ses.last_ts is not None else self._clock()
        if ses.abort_reason is not None:
            self._tier_b_finish(ses, pol, tb, "aborted", ts)
            return None
        live = isinstance(c, Candidate) and any(x is c for x in ses.det.candidates) and c.first_ts == tb.first_ts and not c.false_cut
        if not live and reason != "backoff_probe":
            self._tier_b_finish(ses, pol, tb, "gone", ts)
            return None
        if live:
            det_obj = Detection(
                ts=ts, band=int(c.band), freq_hz=float(c.freq_hz), level_db=float(c.level_db), prominence_db=float(c.prominence_db),
                slope_db_per_s=float(c.slope_db_per_s), frames=int(c.frames), confidence=float(c.confidence),
                reasons=tuple(c.reasons) + (reason,), klass="POLICY", centroid_band=float(c.centroid), narrow_db=float(c.narrow_db),
                rise_db=float(c.rise_db), excess_db=float(c.excess_db))
        else:   # a back-off probe killed the line: cut where it stood (pre-emptive, like the detector's PROBE class)
            det_obj = Detection(ts=ts, band=tb.rta_band, freq_hz=tb.freq_hz, level_db=float(tb.level_db if tb.level_db is not None else -128.0),
                                prominence_db=0.0, slope_db_per_s=0.0, frames=0, confidence=0.0, reasons=(reason,), klass="POLICY")
        if ses.mode is CfsMode.RINGOUT:
            await self._stage(ses, "HOLD", freq_hz=round(det_obj.freq_hz, 1), rta_band=det_obj.band, tier="B", policy=reason)
        n = await self._notch(ses, det_obj, tier="B", policy=reason, cand=c if live else None)
        if n is None:
            at_max = tb.geq_band is not None and ses.nc.gains.get(tb.geq_band, 0.0) <= self._cfg.notch_max_db + 1e-9
            self._tier_b_log(ses, pol, tb, ts, action=reason, verdict=None, next_action="no write")
            self._tier_b_finish(ses, pol, tb, "max_depth" if (at_max or ses.nc.spent) else "no_write", ts)
            return None
        if ses.first_feedback_master_db is None:
            ses.first_feedback_master_db = ses.master_db
        tb.cuts += 1
        tb.geq_band = n.band
        tb.depth_db = n.depth_db
        tb.last_cut_ts = ts
        tb.last_cut_mono = asyncio.get_running_loop().time()
        tb.state = "pending"
        tb.held_since_ts = None
        self._tier_b_log(ses, pol, tb, ts, action=reason, verdict=None, next_action="await the detector's verdict")
        if ses.mode is CfsMode.RINGOUT:
            await self._stage(ses, "NOTCH", freq_hz=round(det_obj.freq_hz, 1), rta_band=det_obj.band, band=n.band, depth_db=n.depth_db,
                              tier="B", policy=reason)
        return n

    async def _policy_tier_b(self, ses: _Session, pol: _Policy, det: FeedbackDetector, ts: float, now: float) -> None:
        cfg = pol.cfg.tier_b
        dcfg = det.cfg
        cands = det.candidates
        live = {id(c): c for c in cands}
        for key, c in live.items():   # how long each line has been MODERATE without a break (ring_out probe-wait rule)
            if c.klass == "MODERATE" and not c.misses:
                pol.moderate_since.setdefault(key, (c, ts))
            else:
                pol.moderate_since.pop(key, None)
        # 1. advance the open engagements on the verdicts the detector filed
        for key, tb in list(pol.tier_b.items()):
            c = live.get(key)
            if c is not tb.cand or (c is not None and c.first_ts != tb.first_ts):
                c = None                                   # the track is gone, or was re-born under the engagement: another line
            if tb.state == "done":
                if c is None or tb.outcome == "no_write":  # a failed write may be retried after the cooldown like the detector retries its own
                    del pol.tier_b[key]
                continue
            if ses.abort_reason is not None:
                self._tier_b_finish(ses, pol, tb, "aborted", ts)
                continue
            if tb.state in ("new", "queued"):
                if c is None:
                    self._tier_b_finish(ses, pol, tb, "gone", ts)
                continue
            if c is None:
                # gone without a pending verdict (the detector files its verdict while the track still coasts): after a
                # 'held' that is a note that ended -> ignore-list; otherwise nothing more to do
                self._tier_b_finish(ses, pol, tb, "ended" if tb.state == "held" else "gone", ts)
                continue
            verdict = c.cut_verdict
            if c.false_cut or verdict == "false_cut" or c.klass == "FALSE_CUT":
                tb.verdicts.append("false_cut")
                self._tier_b_log(ses, pol, tb, ts, action="verdict", verdict="false_cut", next_action="ignore-list; no further cuts")
                self._tier_b_ignore(ses, pol, tb, ts, "false_cut: the line ended by itself after the cut (a note through the EQ)")
                self._tier_b_finish(ses, pol, tb, "false_cut", ts)
                continue
            if tb.state == "pending":
                if verdict in (None, "pending"):
                    if tb.last_cut_mono is not None and now - tb.last_cut_mono > cfg.verdict_timeout_s:
                        self._tier_b_finish(ses, pol, tb, "no_verdict", ts)
                    continue
                tb.verdicts.append(str(verdict))
                if verdict == "confirmed":
                    self._tier_b_log(ses, pol, tb, ts, action="verdict", verdict=verdict, next_action="none (confirmed)")
                    self._tier_b_finish(ses, pol, tb, "confirmed", ts)
                elif verdict == "ambiguous":
                    self._tier_b_log(ses, pol, tb, ts, action="verdict", verdict=verdict, next_action="none (report)")
                    self._tier_b_finish(ses, pol, tb, "ambiguous", ts)
                elif verdict == "insufficient":
                    # drop short of the bell: the excess exceeds the cut or the bell missed the line -> one deeper step now (as VERIFY does)
                    self._tier_b_log(ses, pol, tb, ts, action="verdict", verdict=verdict, next_action="deepen now")
                    await self._tier_b_dispatch(ses, pol, tb, "tier_b_insufficient")
                elif verdict == "held":
                    # dropped by the bell and still there: a note through the EQ OR a limiter/compressor-held howl with more excess
                    # than the cut. Never decided on drop == bell alone: keep alerting and give it held_deepen_s
                    tb.state = "held"
                    tb.held_since_ts = ts
                    self._tier_b_log(ses, pol, tb, ts, action="verdict", verdict=verdict,
                                     next_action=f"alert; deepen at +{cfg.held_deepen_s:g} s if still a held family-less BASE line")
                else:
                    self._tier_b_log(ses, pol, tb, ts, action="verdict", verdict=str(verdict), next_action="none")
                    self._tier_b_finish(ses, pol, tb, str(verdict), ts)
                continue
            if tb.state == "held":
                if verdict == "pending":
                    continue                               # a newer cut within reach of the bell (a neighbour's) is being judged: wait for it
                if verdict is not None and verdict != "held":
                    tb.state = "pending"                   # ... and it re-judged this line: act on that verdict (next frame)
                    continue
                waited = ts - (tb.held_since_ts if tb.held_since_ts is not None else ts)
                if waited < cfg.held_deepen_s:
                    continue
                if c.klass == "MUSICAL":
                    self._tier_b_finish(ses, pol, tb, "musical", ts)      # acquired a family / moves with the mix: leave it
                    continue
                base_held = c.klass == "MODERATE" and not c.stationary and not c.common_mode and "no_family" in c.reasons
                if not base_held:
                    if waited >= cfg.held_deepen_s + 2.0:
                        self._tier_b_finish(ses, pol, tb, "not_base", ts)
                    continue
                if tb.geq_band is not None and ses.nc.gains.get(tb.geq_band, 0.0) <= dcfg.notch_max_db + 1e-9:
                    self._tier_b_finish(ses, pol, tb, "max_depth", ts)
                    continue
                await self._tier_b_dispatch(ses, pol, tb, "tier_b_held")
        # 2. at most one new engagement per frame, none within cooldown_s of the previous, never while aborting
        if ses.abort_reason is not None:
            return
        if pol.tier_b_last_engage_mono is not None and now - pol.tier_b_last_engage_mono < cfg.cooldown_s:
            return
        dwell_s = ses.dwell_ms / 1000.0
        for key, c in live.items():
            if key in pol.tier_b:
                continue
            since = pol.moderate_since.get(key)
            ok, _why = tier_b_eligible(c, cfg=cfg, loudish_db=det.loudish_threshold_db, mode=det.mode, probe_min_hits=dcfg.probe_min_hits,
                                       moderate_for_s=(ts - since[1]) if since is not None else 0.0, dwell_s=dwell_s,
                                       at_arm_suppressed=key in pol.at_arm_suppressed, frame_period_s=dcfg.frame_period_s)
            if not ok:
                continue
            geq_band = ses.nc.band_for_freq(c.freq_hz)
            if self._tier_b_ignored(pol, c, geq_band):
                continue
            if ses.nc.gains.get(geq_band, 0.0) <= dcfg.notch_max_db + 1e-9:
                continue                                   # nothing left to cut there
            if ses.nc.budget_left <= 0 and geq_band not in ses.nc.touched_bands:
                continue                                   # budget spent
            tb = _TierB(key=key, cand=c, first_ts=c.first_ts, freq_hz=float(c.freq_hz), rta_band=int(c.band), level_db=float(c.level_db),
                        reason="at_arm" if key in pol.at_arm_suppressed else "tier_b", started_mono=now)
            pol.tier_b[key] = tb
            pol.tier_b_last_engage_mono = now
            log.info("CFS² %s: tier B: %s line at %.0f Hz (%.1f dBFS, %.0f dB over baseline, %.2f s old, loud-ish line %.1f) -> one %+.0f dB cut",
                     ses.session_id, c.klass, c.freq_hz, c.level_db, c.excess_db, c.age_s, det.loudish_threshold_db, dcfg.notch_step_db)
            await self._tier_b_dispatch(ses, pol, tb, "tier_b")
            break

    # -- ring_out: policy actions run in the ring-out task -------------------------------------------

    def _pop_policy_action(self, ses: _Session) -> tuple[str, Any, str] | None:
        pol = ses.policy
        if pol is None or not pol.actions:
            return None
        return pol.actions.pop(0)

    def _policy_busy(self, ses: _Session) -> bool:
        """ring_out: hold the raise while a policy cut is queued / awaits its verdict / waits out a 'held' line."""
        pol = ses.policy
        if pol is None:
            return False
        if pol.actions:
            return True
        if not pol.cfg.tier_b.ringout_hold_raise:
            return False
        lf = ses.last_frame_mono
        if lf is None or asyncio.get_running_loop().time() - lf > _FRAME_STALL_S:
            return False                                   # no frames: the engagements cannot progress; let _frames_alive see the stall
        return any(tb.state in ("queued", "pending", "held") for tb in pol.tier_b.values())

    async def _run_policy_action(self, ses: _Session, act: tuple[str, Any, str]) -> None:
        kind, obj, reason = act
        pol = ses.policy
        assert pol is not None
        if kind == "tier_b":
            await self._tier_b_cut(ses, pol, obj, reason)
        elif kind == "backoff":
            await self._backoff_probe(ses, pol, obj)
        else:
            log.warning("unknown policy action %r", kind)

    # -- item 6: back-off probe (ring_out) ---------------------------------------------------------

    def _policy_backoff_scan(self, ses: _Session, pol: _Policy, det: FeedbackDetector) -> None:
        if ses.abort_reason is not None or any(a[0] == "backoff" for a in pol.actions):
            return
        for c in det.candidates:
            if c.misses or c.klass != "STATIONARY" or "backoff_advised" not in c.reasons:
                continue
            if pol.backoff_done.get(id(c)) is c or id(c) in pol.tier_b:
                continue
            pol.backoff_done[id(c)] = c
            pol.actions.append(("backoff", c, "backoff_probe"))
            ses.wake.set()
            log.info("CFS² %s: %.0f Hz follows the gain steps 1 dB/dB but is %.0f dB prominent and family-less: back-off probe queued",
                     ses.session_id, c.freq_hz, c.prominence_db)
            break

    async def _collect_band_levels(self, ses: _Session, band: int, seconds: float, *, settle_s: float = 0.0) -> list[float]:
        loop = asyncio.get_running_loop()
        t0 = loop.time() + settle_s
        t_end = loop.time() + seconds
        out: list[float] = []
        while ses.abort_reason is None:
            remaining = t_end - loop.time()
            if remaining <= 0:
                break
            ses.frame_event.clear()
            try:
                await asyncio.wait_for(ses.frame_event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if loop.time() < t0:
                continue
            lvl = self._band_level(ses, band)
            if lvl is not None:
                out.append(float(lvl))
        return out

    async def _policy_wait(self, ses: _Session, seconds: float) -> None:
        """Sleep ``seconds`` in the ring-out task while staying responsive to an abort (frame-paced)."""
        loop = asyncio.get_running_loop()
        t_end = loop.time() + max(0.0, seconds)
        while ses.abort_reason is None:
            remaining = t_end - loop.time()
            if remaining <= 0:
                return
            ses.frame_event.clear()
            try:
                await asyncio.wait_for(ses.frame_event.wait(), timeout=min(remaining, 0.25))
            except asyncio.TimeoutError:
                pass

    async def _backoff_probe(self, ses: _Session, pol: _Policy, c: Candidate) -> None:
        """Lower the master ``drop_db`` for one dwell and watch the STATIONARY line: a source in the room drops dB for dB (left
        alone, level restored); a howl whose plateau a compressor holds falls by far more or dies (1/(1-g)) -> cut where it stood
        (reason backoff_probe) and the level restored [DETECTOR §3 PROBE, §11 N1]. Both master moves are told to the detector
        (``note_gain_step``) and kept a probe window clear of the run's own +1 dB steps, so neither move lands in the pre/post
        window the detector judges another step on (a restore jump inside the next step's window reads as an over-response)."""
        cfg = pol.cfg.backoff_probe
        if ses.abort_reason is not None or ses.operator_override or ses.connection_lost:
            return
        if not any(x is c for x in ses.det.candidates) or c.misses:
            return
        loop = asyncio.get_running_loop()
        window_s = float(ses.det.cfg.probe_window_s) + 0.2
        if ses.last_raise_mono is not None:
            await self._policy_wait(ses, window_s - (loop.time() - ses.last_raise_mono))   # let the last +1 dB step be judged first
        if ses.abort_reason is not None or not any(x is c for x in ses.det.candidates) or c.misses:
            return
        band, freq = int(c.band), float(c.freq_hz)
        pre = [float(h[2]) for h in list(c.hist)[-8:]]
        before = median(pre) if pre else float(c.level_db)
        start = ses.master_db
        ts0 = ses.last_ts if ses.last_ts is not None else self._clock()
        if math.isinf(start) or start - cfg.drop_db < FADER_FLOOR_DB:
            pol.backoff_log.append({"t": self._t_rel(pol, ts0), "freq_hz": round(freq, 1), "rta_band": band,
                                    "verdict": f"not run: master at {format_db(start)} dB cannot come down {cfg.drop_db:g} dB"})
            return
        target = start - cfg.drop_db
        await self._stage(ses, "PROBE", freq_hz=round(freq, 1), rta_band=band, from_db=_db1(start), to_db=_db1(target))
        try:
            after = await self._write_master(ses, target, force=True)      # lowering: emergency lane, never clamped
        except (DeskError, PolicyError, X32ConnectionError) as e:
            log.warning("CFS² %s: back-off probe write refused: %s", ses.session_id, e)
            pol.backoff_log.append({"t": self._t_rel(pol, ts0), "freq_hz": round(freq, 1), "rta_band": band, "verdict": f"not run: {e}"})
            return
        ses.det.note_gain_step(after - start, ses.last_ts if ses.last_ts is not None else self._clock())
        ses.master_db = after
        ses.last_raise_mono = loop.time()
        wait_s = max(ses.dwell_ms / 1000.0, window_s)
        levels = await self._collect_band_levels(ses, band, wait_s, settle_s=cfg.settle_s)
        still = any(x is c for x in ses.det.candidates) and not c.misses
        post = median(levels[len(levels) // 2:]) if levels else None
        drop = (before - post) if post is not None else math.inf
        died = (not still) or (post is not None and post <= before - 20.0)
        regenerative = died or drop > cfg.min_response_db
        verdict = "feedback" if regenerative else "stationary"
        restored: float | None = None
        if ses.abort_reason is None and not ses.operator_override and not await self._operator_moved_master(ses):
            try:
                restored = await self._write_master(ses, start)              # back to where the run stood (a <= drop_db raise)
            except (DeskError, PolicyError, X32ConnectionError) as e:
                log.warning("CFS² %s: back-off probe: restoring the master to %s dB failed: %s (the run continues from %s dB)",
                            ses.session_id, format_db(start), e, format_db(ses.master_db))
            else:
                ses.det.note_gain_step(restored - ses.master_db, ses.last_ts if ses.last_ts is not None else self._clock())
                ses.master_db = restored
                ses.last_raise_mono = loop.time()
                await self._policy_wait(ses, wait_s)     # and let the restore be judged before the next +1 dB step goes in
        entry = {"t": self._t_rel(pol, ts0), "freq_hz": round(freq, 1), "rta_band": band, "before_db": round(before, 1),
                 "after_db": None if post is None else round(post, 1), "drop_db": None if post is None else round(drop, 1), "died": died,
                 "verdict": verdict, "master_from_db": _db1(start), "master_probe_db": _db1(after), "master_restored_db": _db1(restored)}
        pol.backoff_log.append(entry)
        self._events.publish("cfs.policy", session_id=ses.session_id, bus=ses.bus, what="backoff_probe", **entry)
        await self._stage(ses, "PROBE", freq_hz=round(freq, 1), rta_band=band, verdict=verdict, drop_db=entry["drop_db"], died=died)
        log.info("CFS² %s: back-off probe at %.0f Hz: %s (drop %s dB for %g dB)", ses.session_id, freq, verdict, entry["drop_db"], cfg.drop_db)
        if regenerative and ses.abort_reason is None:
            tb = _TierB(key=id(c), cand=c, first_ts=c.first_ts, freq_hz=freq, rta_band=band, level_db=before, reason="backoff_probe",
                        started_mono=loop.time())
            pol.tier_b[id(c)] = tb
            await self._tier_b_cut(ses, pol, tb, "backoff_probe")

    # -- item 4: alerts ----------------------------------------------------------------------------

    def _alert_event(self, ses: _Session, pol: _Policy, rec: dict[str, Any], onoff: str, t_rel: float) -> None:
        entry = {"t": t_rel, "freq_hz": rec["freq_hz"], "klass": rec["alert"], "alert": onoff}
        if len(pol.alerts_log) < _MAX_POLICY_LOG:
            pol.alerts_log.append(entry)
        if pol.cfg.alerts.events:
            self._events.publish("cfs.candidate", session_id=ses.session_id, bus=ses.bus, alert=onoff, alert_class=rec["alert"], **rec["brief"])
        log.info("CFS² %s: alert %s: %s line at %.0f Hz (%s dBFS, %s dB prominent%s)", ses.session_id, onoff.upper(), rec["alert"], rec["freq_hz"],
                 rec["brief"].get("level_db"), rec["brief"].get("prominence_db"),
                 f", verdict {rec['brief'].get('cut_verdict')}" if rec["brief"].get("cut_verdict") else "")

    async def _policy_alerts(self, ses: _Session, pol: _Policy, det: FeedbackDetector, ts: float, now: float) -> None:
        acfg = pol.cfg.alerts
        k1_s = det.cfg.stable_frames * det.cfg.frame_period_s
        t_rel = self._t_rel(pol, ts)
        seen: set[int] = set()
        for c in det.candidates:
            key = id(c)
            tb = pol.tier_b.get(key)
            a = alert_worthy(c, k1_s=k1_s, engaged=tb is not None and tb.state != "done", at_arm_suppressed=key in pol.at_arm_suppressed)
            if a is None:
                continue
            seen.add(key)
            brief = candidate_brief(c)
            rec = pol.alerts_live.get(key)
            if rec is None:
                rec = {"alert": a, "since": t_rel, "last_seen_mono": now, "brief": brief, "freq_hz": brief["freq_hz"], "cand": c}
                pol.alerts_live[key] = rec
                self._alert_event(ses, pol, rec, "on", t_rel)
            else:
                rec["last_seen_mono"] = now
                rec["brief"] = brief
                if rec["alert"] != a:                      # e.g. MODERATE -> TIER_B -> HELD: a state change worth an event
                    rec["alert"] = a
                    self._alert_event(ses, pol, rec, "on", t_rel)
        for key, rec in list(pol.alerts_live.items()):
            if key in seen:
                continue
            if now - float(rec["last_seen_mono"]) >= acfg.hold_s:
                del pol.alerts_live[key]
                self._alert_event(ses, pol, rec, "off", t_rel)
        if pol.alerts_live:
            pol.last_alert_live_mono = now
        # the scribble strip: alert colour while anything is live, the original once nothing has been for clear_s
        if not acfg.scribble_strip or pol.color_orig is None or ses.abort_reason is not None:
            return
        want = bool(pol.alerts_live) or (pol.color_is_alert and pol.last_alert_live_mono is not None
                                         and now - pol.last_alert_live_mono < acfg.clear_s)
        if want == pol.color_is_alert and not pol.color_dirty:
            return
        if now - pol.color_last_write_mono < acfg.min_write_interval_s:
            return
        await self._write_strip_color(ses, pol, alert=want)

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
        always safe and must never be refused by show mode (``set_main_level`` forces it already).
        A lowering write also takes the rate limiter's emergency lane (``priority_writes``): the
        back-off after an abort must not queue behind — or be refused because of — somebody else's
        burst of writes, or the bus is left parked at the loudest level of the run."""
        lowering = force or (not math.isinf(db) and not math.isinf(ses.master_db) and db < ses.master_db) or math.isinf(db)
        with priority_writes(lowering):
            if ses.target.family == "main":
                res = await self._desk.set_main_level("st", db, ramp_ms=0)
            else:
                res = await self._desk.set_level(ses.target, db, ramp_ms=0, force=force)
        after = res.get("after_db")
        return NEG_INF_DB if after is None else float(after)

    async def _dwell(self, ses: _Session, seconds: float) -> None:
        ses.wake.clear()
        if ses.pending or ses.abort_reason or (ses.policy is not None and ses.policy.actions):
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
            act = self._pop_policy_action(ses)
            if act is not None:
                # a policy write (tier-B cut / deepen, back-off probe) runs here, between raises, like HOLD -> NOTCH:
                # every master and GEQ write of a ring-out stays in this one task
                await self._run_policy_action(ses, act)
                if ses.abort_reason is not None:
                    break
                if ses.nc.spent and not self._policy_busy(ses):
                    reason = "notch budget spent"
                    break
                continue
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
            if self._policy_busy(ses):
                # a policy cut awaits its verdict (or a held line its deepen): do not put another dB into a bus on which a
                # suspected howl is standing -- bounded by tier_b.verdict_timeout_s / held_deepen_s
                await self._dwell(ses, 0.25)
                continue
            if not await self._frames_alive(ses):
                break
            if await self._operator_moved_master(ses):
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
            # tell the detector about our own gain step: a line that answers a +1 dB step with much more than
            # +1 dB is loop-gain dependent (the active probe, loop brief §1.5); one that follows it 1 dB/dB is not
            ses.det.note_gain_step(after - ses.master_db, ses.last_ts if ses.last_ts is not None else self._clock())
            ses.master_db = after
            ses.max_master_db = max(ses.max_master_db, after)
            ses.last_raise_mono = asyncio.get_running_loop().time()
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
        if ses.operator_override:
            # Somebody is driving this fader by hand: do not write it again, not even to back off.
            ses.end_master_db = ses.master_db
            ses.final_stage = "ABORT"
            await self._stage(ses, "ABORT", reason=ses.abort_reason, hands_off=True)
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
        if ses.abort_reason == _PANIC_REASON:
            # An emergency stop is not a ring-out result: hand the bus back no higher than it was found.
            final = min(final, ses.start_master_db)
        await self._stage(ses, "BACKOFF", to_db=_db1(final), backoff_db=back)
        # Lowering the master is the protective move; a transient refusal (the rate limiter drained by
        # another client's burst, one lost datagram) must not leave the bus parked at its highest point.
        last_err: Exception | None = None
        for attempt in range(4):
            try:
                written = await self._write_master(ses, final, force=True)  # lowering: never let a clamp refuse it
                ses.master_db = written
                ses.end_master_db = written
                last_err = None
                break
            except (DeskError, PolicyError, X32ConnectionError) as e:
                last_err = e
                transient = (isinstance(e, PolicyError) and e.code == "RATE_LIMITED") or (isinstance(e, DeskError) and e.code == "TIMEOUT")
                log.warning("back-off write failed (%s), attempt %d: %s", ses.session_id, attempt + 1, e)
                if not transient or attempt == 3:
                    break
                await asyncio.sleep(0.5 * (attempt + 1))
        if last_err is not None:
            ses.abort_reason = ses.abort_reason or f"back-off failed: {last_err}"
            final_stage = "ABORT"
            ses.end_master_db = ses.master_db
        ses.final_stage = final_stage
        await self._stage(ses, final_stage, reason=ses.abort_reason if final_stage == "ABORT" else reason)

    # -- connection loss ---------------------------------------------------------------------

    def _on_panic_event(self, ev: Event) -> None:
        """``panic()`` has just silenced the outputs. Whatever CFS² is doing must stop *now*: a
        ring-out would otherwise keep raising the (muted, therefore feedback-free) bus to its target
        and park it there for the operator to unmute into. The abort reason is set synchronously,
        before the mutes even leave, so the RAISE loop cannot issue another write; the back-off then
        returns the master to at most its starting level (``_finalize_levels``)."""
        self._stop_system("panic")
        ses = self._ses
        if ses is None or ses.abort_reason is not None:
            return
        log.warning("CFS² %s aborted by panic()", ses.session_id)
        self._abort_from_callback(ses, _PANIC_REASON)

    def _on_desk_push(self, ses: _Session, address: str, args: Any) -> None:
        """Something changed on the console while the session runs (an /xremote push). Three cases
        matter: a GEQ band of our slot moved by hand (adopt it), the GEQ slot / the bus insert itself
        was re-configured (our writes would now land somewhere else: abort), or the RTA was pointed at
        another strip (the detector is now listening to the wrong audio: abort)."""
        if self._ses is not ses or ses.abort_reason is not None:
            return
        why: str | None = None
        if address == f"/fx/{ses.fx_slot}/type":
            why = f"FX slot {ses.fx_slot} was reloaded on the desk during the session; its GEQ pars no longer mean what they did"
        elif address.startswith(f"{ses.target.osc_prefix}/insert/"):
            why = f"{ses.target.label}'s insert was changed on the desk during the session"
        elif ses.rta is not None and address in (self._d.rta.get("source_param", "/-prefs/rta/source"),
                                                 self._d.rta.get("stat_param", "/-stat/rtasource")):
            expected = ses.rta.source_index if address.endswith("/source") else ses.rta.stat_expected
            try:
                got = int(args[0]) if args else None
            except (TypeError, ValueError):
                got = None
            if got is not None and got != expected:
                why = f"the RTA source was changed on the desk ({address} = {got}, this session needs {expected}); the detector no longer hears {ses.target.label}"
        if why is not None:
            log.warning("CFS² %s: %s — aborting", ses.session_id, why)
            self._abort_from_callback(ses, why)
            return
        if address == f"{ses.target.osc_prefix}/config/color":
            self._on_color_push(ses, args)
            return
        self._on_geq_push(ses, address, args)

    def _abort_from_callback(self, ses: _Session, reason: str) -> None:
        """Abort from a synchronous callback: reason set at once (the RAISE loop checks it before every
        write), the rest (back-off / watch stop) scheduled."""
        ses.abort_reason = ses.abort_reason or reason
        ses.wake.set()
        self._events.publish("cfs.abort", session_id=ses.session_id, bus=ses.bus, reason=ses.abort_reason)
        if ses.mode is CfsMode.WATCH and self._ses is ses:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            task = loop.create_task(self.stop(), name="cfs-abort-stop")
            self._bg.add(task)
            task.add_done_callback(self._bg.discard)

    def _on_policy_event(self, ev: Event) -> None:
        """Show mode switched ON mid-run: ring-outs are exactly what it forbids, so a running one stops
        (a feedback watch is the show-time mode and carries on)."""
        ses = self._ses
        if not ev.data.get("on") or ses is None or ses.mode is not CfsMode.RINGOUT or ses.abort_reason is not None:
            return
        self._stop_system("show mode")
        log.warning("CFS² %s: show mode switched on during the ring-out — aborting", ses.session_id)
        self._abort_from_callback(ses, "show mode was switched on during the run")

    def _on_geq_push(self, ses: _Session, address: str, args: Any) -> None:
        """A pushed ``/fx/N/par/PP`` for this session's GEQ side: the engineer moved a band by hand."""
        if self._ses is not ses or not address.startswith(f"/fx/{ses.fx_slot}/par/"):
            return
        try:
            hit = self._desk.geq_par_from_push(address, args, ses.writer.fx_type)
        except Exception:
            log.debug("undecodable GEQ push %s %r", address, args, exc_info=True)
            return
        if hit is None:
            return
        slot, side, band, gain_db = hit
        if side != ses.side or band > 31:
            return
        before = ses.writer.gains.get(band, 0.0)
        ses.writer.observe(band, gain_db)
        ses.nc.observe(band, gain_db)
        if abs(gain_db - before) >= 0.25:
            log.info("CFS² %s: GEQ band %d moved on the desk %+.1f -> %+.1f dB; adopting it", ses.session_id, band, before, gain_db)
            self._events.publish("cfs.geq_external", session_id=ses.session_id, bus=ses.bus, band=band,
                                 was_db=round(before, 2), now_db=round(gain_db, 2))

    async def _read_master(self, ses: _Session) -> float | None:
        """Fresh read of the bus master (dB, -inf for -oo); None when the desk does not answer."""
        t = ses.target
        spec = self._d.param(t.family, "mix/fader")
        address = spec.address(t)
        self._desk.invalidate(address)
        try:
            raw = await asyncio.wait_for(self._conn.get(address), timeout=_RESTORE_READ_TIMEOUT_S)
        except Exception as e:
            log.debug("master read-back failed: %s", e)
            return None
        try:
            v = spec.to_value(raw)
        except Exception:
            return None
        return NEG_INF_DB if v is None else float(v)

    async def _operator_moved_master(self, ses: _Session) -> bool:
        """True (and the session flagged hands-off) when the master reads back away from where this
        session last put it: somebody is at the desk, and a ring-out never argues with a human. The
        previous behaviour wrote belief + step as an absolute value, which turned an operator's
        emergency pull-down into a jump straight back up (tens of dB in one datagram)."""
        actual = await self._read_master(ses)
        if actual is None:
            return False
        belief = ses.master_db
        both_off = math.isinf(actual) and math.isinf(belief)
        if both_off or (not math.isinf(actual) and not math.isinf(belief) and abs(actual - belief) <= _INTERVENTION_DB):
            return False
        ses.operator_override = True
        ses.abort_reason = ses.abort_reason or (
            f"{ses.target.label} master was moved on the desk to {format_db(actual)} dB during the run "
            f"(the ring-out had it at {format_db(belief)} dB); stopped without touching it again")
        ses.master_db = actual
        log.warning("CFS² %s: %s", ses.session_id, ses.abort_reason)
        self._events.publish("cfs.abort", session_id=ses.session_id, bus=ses.bus, reason=ses.abort_reason)
        return True

    async def _frames_alive(self, ses: _Session) -> bool:
        """The detector only protects the room while frames arrive. Never raise again until it has seen
        at least one fresh frame *since the previous raise* (otherwise the level already set has not
        been observed at all); if frames have stopped (subscription lapsed, desk busy, Wi-Fi), hold, and
        if they do not return within a few seconds, abort and back off."""
        loop = asyncio.get_running_loop()

        def seen_since_raise() -> bool:
            lf = ses.last_frame_mono
            if lf is None or loop.time() - lf > _FRAME_STALL_S:
                return False
            return ses.last_raise_mono is None or lf > ses.last_raise_mono

        if seen_since_raise():
            return True
        now = loop.time()
        deadline = now + _FRAME_STALL_ABORT_S
        warned = False
        while loop.time() < deadline and ses.abort_reason is None:
            if not warned and (ses.last_frame_mono is None or loop.time() - ses.last_frame_mono > _FRAME_STALL_S):
                log.warning("CFS² %s: no RTA frames for %.1f s; holding the raise", ses.session_id,
                            loop.time() - (ses.last_frame_mono if ses.last_frame_mono is not None else now))
                warned = True
            ses.frame_event.clear()
            try:
                await asyncio.wait_for(ses.frame_event.wait(), timeout=min(0.25, max(0.0, deadline - loop.time())))
            except asyncio.TimeoutError:
                continue
            if seen_since_raise():
                return True
        if ses.abort_reason is None:
            ses.abort_reason = "RTA frames stopped arriving; the detector cannot see, so the master is not raised further"
            log.warning("CFS² %s: %s", ses.session_id, ses.abort_reason)
            self._events.publish("cfs.abort", session_id=ses.session_id, bus=ses.bus, reason=ses.abort_reason)
        return False

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
            # the detector's own account of the instrument and of each cut: analyser flags at the end (HOT_SPECTRUM /
            # PEAK_HOLD_SUSPECTED / SLOW_RELEASE / PROGRAMME_PRESENT / FROZEN_LINES), its measured display release rate and
            # arm-time level reference, and the post-cut verdicts (confirmed / insufficient / held / false_cut / ambiguous)
            "detector": {"flags": sorted(ses.det.flags), "release_db_per_s": ses.det.release_db_per_s,
                         "arm_p95_db": ses.det.arm_p95_db, "loud_threshold_db": ses.det.loud_threshold_db,
                         "loudish_threshold_db": ses.det.loudish_threshold_db, "window_low_hz": ses.det.cfg.window_low_hz,
                         "cut_verdicts": list(ses.det.cut_log)},
            # what the policy layer did with the detector's hooks (docs/CFS_POLICY.md): LF edge, the ring-out contract
            # check, tier-B steps with their verdicts, alerts, at-arm lines it declined to cut, flags it acted on
            "policy": self._policy_report(ses),
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
