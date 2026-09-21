"""GEQ insert provisioning, validation, mic discovery and ring-out preflight (DESIGN.md §14).

Everything here is built on :class:`~x32mcp.desk.Desk` reads and its Tier-2 executors
(``set_fx_type`` / ``set_insert``); nothing talks OSC directly. Wire facts come from
``docs/research/fx_routing_scenes.md`` (§2 GEQ layout, §3.2 ``insert/sel`` enum, §3.4 one insert
point per strip, §5.1 ``grp/mute`` bitmask, §4.9 channel sources) and are cited inline.

Decisions where DESIGN.md is silent:

* **Bus spelling.** Every function takes ``bus`` as a 1-based bus number (``int``), a
  :class:`~x32mcp.targets.Target` (``bus``/``main`` only) or the strings ``"main"``/``"main.st"``
  for Main LR (``ring_out_system`` ends on the mains). In results the bus is reported as the
  number for buses and ``"main"`` for Main LR (``GeqStatus.bus``, ``MicCandidate`` notes).
* **Free slots** (DESIGN §14): a slot is free iff no strip inserts either side of it — a slot
  whose GEQ is inserted by a bus outside the request is *not* free even when one side is unused.
  A dual slot already serving one of the requested buses may take a second requested bus on its
  other side. Only the insert-only slots ``geq.insert_slots_preferred`` (5–8) are allocated:
  FX 1–4 would also need ``/fx/N/source/l|r = INS`` (§3.4), which is not modelled.
* **Type loads**: a free slot whose type is in ``geq.fx_types_dual`` (GEQ2/TEQ2) is used as is;
  anything else (including a stereo GEQ, whose two sides are linked) is loaded with ``GEQ2``.
  ``/fx/*/type`` and ``/*/insert/*`` are guarded, so a non-empty plan has ``needs_tier2``.
* **Validation blockers** (``GeqStatus.reasons``): no FX insert, the slot does not hold a
  GEQ/TEQ, the insert is bypassed (``insert/on`` OFF), the same insert point is selected on
  another strip, or a *stereo* GEQ is shared with another strip (a cut would hit both). An
  insert at ``PRE`` is accepted. ``matched_session`` names the latest ring-out report for the
  bus whose notches are all still present on the GEQ at the same depth.
* **Mic discovery** (BRIEF §5 "open-loop problem"): ``include`` is the automatic verdict —
  unmuted, send to the bus ≥ ``mics.send_floor_db`` (and the send itself on), and a physical
  preamp behind the channel source (``Desk.headamp_index_for`` is not ``None``: local XLR or
  AES50; USB/card/FX/bus sources are not mics). For Main LR the "send" is the channel fader
  with the LR assignment on. The patch plan's ``mic``/``monitor_bus`` and mute group
  ``mics.mute_group_convention`` (bit 5 of ``grp/mute`` for group 6, §5.1) are cross-checks:
  disagreements go into ``notes`` (per candidate) and ``Preflight.warnings``. Only channels with
  at least one indicator are listed. Nothing is ever unmuted.
* **Preflight** blockers: GEQ validation failed, no candidate mics, the bus master muted, or the
  master at −∞ (a ring-out would climb from −90 dB). Warnings: master above
  ``ringout.start_warn_db``, mic-source disagreements, a GEQ that already carries cuts.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .desk import Desk, DeskError
from .scales import format_db
from .settle import read_until
from .targets import Target, TargetError, parse_target

__all__ = [
    "geq_sides_for",
    "InsertInfo",
    "GeqStatus",
    "SlotAllocation",
    "SetupPlan",
    "MicCandidate",
    "Preflight",
    "bus_target",
    "bus_label",
    "validate_ringout_eqs",
    "plan_setup",
    "apply_setup",
    "discover_mics",
    "preflight",
]

log = logging.getLogger(__name__)

_FLAT_EPS = 0.05  # dB: the GEQ grid is 0.5 dB, node text prints one decimal
# HANDOVER §4b: a real desk answered the read straight after the insert writes with the OLD
# values, so a successful setup reported GEQ_VALIDATION_FAILED. After writing, validation is
# re-read until it agrees or this deadline passes (then it is "not yet verified", not failed).
SETUP_SETTLE_S = 2.0


# -- bus spelling ---------------------------------------------------------------------------------


def bus_target(bus: int | str | Target) -> Target:
    """``3`` / ``"bus.3"`` → ``Target("bus", 3)``; ``"main"`` / ``"main.st"`` / ``"lr"`` → Main LR.
    Raises ``DeskError("BAD_ARGUMENT")`` for anything that cannot carry a ring-out."""
    if isinstance(bus, Target):
        t = bus
    elif isinstance(bus, bool):
        raise DeskError("BAD_ARGUMENT", f"bus must be a number 1..16 or 'main', got {bus!r}")
    elif isinstance(bus, int):
        try:
            t = Target("bus", bus)
        except TargetError as e:
            raise DeskError("BAD_ARGUMENT", str(e)) from None
    elif isinstance(bus, str):
        s = bus.strip().lower()
        if s in ("main", "main.st", "lr", "main lr", "mains"):
            t = Target("main", "st")
        else:
            try:
                t = parse_target(s, default_family="bus")
            except TargetError as e:
                raise DeskError("BAD_ARGUMENT", str(e)) from None
    else:
        raise DeskError("BAD_ARGUMENT", f"bus must be a number 1..16 or 'main', got {bus!r}")
    if t.family == "bus":
        return t
    if t.family == "main" and t.index == "st":
        return t
    raise DeskError("BAD_ARGUMENT", f"{t.label} cannot be rung out; use a bus (1..16) or 'main'")


def bus_label(t: Target) -> int | str:
    """The public spelling of a ring-out bus: the number, or ``"main"`` for Main LR."""
    return "main" if t.family == "main" else int(t.index)


def _db_out(v: Any) -> float | None:
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)) or math.isinf(v):
        return None
    return round(float(v), 1)


def _db_text(v: Any) -> str | None:
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return format_db(float(v))


# -- dataclasses ----------------------------------------------------------------------------------


@dataclass
class InsertInfo:
    """A strip's insert point: ``sel`` token (``FX5L``), ``on`` switch, ``pos`` (``PRE``/``POST``),
    the FX slot/side it resolves to (``side`` ``"A"`` for ``FXnL``, ``"B"`` for ``FXnR``) and the
    type loaded in that slot (fx_routing_scenes.md §3.2)."""

    target: str
    sel: str
    on: bool
    pos: str
    fx_slot: int | None
    side: str | None
    fx_type: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"target": self.target, "sel": self.sel, "on": self.on, "pos": self.pos,
                "fx_slot": self.fx_slot, "side": self.side, "fx_type": self.fx_type}


@dataclass
class GeqStatus:
    """Read-only verdict on one bus's ring-out GEQ. ``bands_db`` are the 31 band gains (dB) of
    the side serving the bus, ``notches`` the bands currently cut (``{band, freq_hz, depth_db}``,
    1-based band), ``matched_session`` the report whose notches are all still present."""

    bus: int | str
    ok: bool
    reasons: list[str]
    insert: InsertInfo | None
    bands_db: list[float] | None
    flat: bool
    notches: list[dict[str, Any]]
    matched_session: str | None
    target: str = ""
    master_db: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bus": self.bus, "target": self.target, "ok": self.ok, "reasons": list(self.reasons),
            "insert": self.insert.to_dict() if self.insert else None,
            "bands_db": None if self.bands_db is None else list(self.bands_db),
            "master_db": self.master_db, "flat": self.flat, "notches": [dict(n) for n in self.notches],
            "matched_session": self.matched_session,
        }


@dataclass
class SlotAllocation:
    """One FX slot in a :class:`SetupPlan`: ``sides`` maps ``"A"``/``"B"`` to the bus (number or
    ``"main"``) that will insert ``FX<slot>L`` / ``FX<slot>R``; ``load_type`` when the slot must
    first be loaded with ``fx_type``."""

    slot: int
    fx_type: str
    current_type: str | None
    load_type: bool
    sides: dict[str, int | str | None]

    def to_dict(self) -> dict[str, Any]:
        return {"slot": self.slot, "fx_type": self.fx_type, "current_type": self.current_type,
                "load_type": self.load_type, "sides": dict(self.sides)}


@dataclass
class SetupPlan:
    """What :func:`apply_setup` would change. ``reuse`` lists buses already carrying a GEQ insert
    (kept as-is, switched on if bypassed); ``type_loads`` (``{slot, fx_type, current_type}``) and
    ``inserts`` (``{target, bus, sel?, on?, pos?}``) are the writes; ``blockers`` explain buses
    that could not be placed."""

    buses: list[int | str]
    reuse: dict[int | str, InsertInfo] = field(default_factory=dict)
    allocations: list[SlotAllocation] = field(default_factory=list)
    type_loads: list[dict[str, Any]] = field(default_factory=list)
    inserts: list[dict[str, Any]] = field(default_factory=list)
    needs_tier2: bool = False
    blockers: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        """True when applying the plan would write nothing."""
        return not self.type_loads and not self.inserts

    def to_dict(self) -> dict[str, Any]:
        return {
            "buses": list(self.buses),
            "reuse": {str(k): v.to_dict() for k, v in self.reuse.items()},
            "allocations": [a.to_dict() for a in self.allocations],
            "type_loads": [dict(x) for x in self.type_loads],
            "inserts": [dict(x) for x in self.inserts],
            "needs_tier2": self.needs_tier2,
            "blockers": list(self.blockers),
            "empty": self.empty,
        }


@dataclass
class MicCandidate:
    """One input channel considered as a stage mic for a bus. ``send_db`` is its send level to
    the bus (``None`` = −∞ or the send switched off); ``physical_input`` = a head amp sits behind
    ``source``; ``in_mute_group`` = member of the mute group ``mics.mute_group_convention``;
    ``patch_mic``/``owner`` come from the patch plan (``None`` = unknown). ``include`` is the
    automatic suggestion; ``notes`` list disagreements between the three sources."""

    ch: int
    name: str
    muted: bool
    send_db: float | None
    source: str
    physical_input: bool
    in_mute_group: bool | None
    patch_mic: bool | None
    owner: str | None
    include: bool
    notes: list[str] = field(default_factory=list)
    input_db: float | None = None      # live input meter (dBFS) when it was sampled; None = not sampled
    signal: bool | None = None         # input_db above mics.signal_floor_db; None = unknown

    def to_dict(self) -> dict[str, Any]:
        d = {
            "ch": self.ch, "target": f"ch.{self.ch}", "name": self.name, "muted": self.muted,
            "send_db": self.send_db, "send": "-oo" if self.send_db is None else format_db(self.send_db),
            "source": self.source, "physical_input": self.physical_input, "in_mute_group": self.in_mute_group,
            "patch_mic": self.patch_mic, "owner": self.owner, "include": self.include, "notes": list(self.notes),
        }
        if self.input_db is not None:
            d["input_db"] = round(self.input_db, 1)
            d["signal"] = self.signal
        return d


@dataclass
class Preflight:
    """Arming verdict for one bus: ``ok`` iff ``blockers`` is empty. ``master_db`` is the bus
    master (``-inf`` when fully down); ``master`` its text form."""

    ok: bool
    blockers: list[str]
    warnings: list[str]
    mics: list[MicCandidate]
    geq: GeqStatus
    master_db: float
    bus_name: str
    bus: int | str = 0
    target: str = ""
    muted: bool = False

    @property
    def master(self) -> str:
        return format_db(self.master_db)

    @property
    def included_mics(self) -> list[MicCandidate]:
        return [m for m in self.mics if m.include]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "bus": self.bus, "target": self.target, "bus_name": self.bus_name,
            "master_db": _db_out(self.master_db), "master": self.master, "muted": self.muted,
            "blockers": list(self.blockers), "warnings": list(self.warnings),
            "mics": [m.to_dict() for m in self.mics], "included": [m.ch for m in self.included_mics],
            "geq": self.geq.to_dict(),
        }


# -- helpers --------------------------------------------------------------------------------------


def _descriptor(desk: Desk) -> Any:
    d = getattr(desk, "descriptor", None)
    return d if d is not None else desk._d  # Desk has no public descriptor accessor yet


# The ring-out GEQ must be inserted PRE, not POST. The RTA taps the strip post-EQ, and a POST
# insert sits downstream of that tap: measured on a real X32 at M7, a -15 dB cut through a POST
# insert moved the RTA by -0.2/+0.4/+1.2 dB (i.e. not at all), while the same cut PRE showed as
# -5.9/-6.0/-3.9 dB. With a POST insert the detector is blind to its own notches, so it can never
# verify decay and drives every ring to the -9 dB maximum whether the cut worked or not.
_RINGOUT_INSERT_POS = "PRE"


def _sel_token(slot: int, side: str) -> str:
    """``(5, "A")`` → ``"FX5L"`` (fx_routing_scenes.md §3.2: L = side A, R = side B)."""
    return f"FX{slot}{'L' if side == 'A' else 'R'}"


def _bus_of_key(key: str) -> int | str | None:
    """Bus spelling of a strip key (``"bus.3"`` → 3, ``"main.st"`` → ``"main"``, else None)."""
    try:
        return bus_label(bus_target(parse_target(key)))
    except (DeskError, TargetError):
        return None


class _FxCache:
    """Per-call memo of ``Desk.get_fx`` (each call is two /node reads)."""

    def __init__(self, desk: Desk) -> None:
        self._desk = desk
        self._fx: dict[int, dict[str, Any]] = {}

    async def get(self, slot: int) -> dict[str, Any]:
        fx = self._fx.get(slot)
        if fx is None:
            fx = await self._desk.get_fx(slot)
            self._fx[slot] = fx
        return fx


def _is_stereo_strip(key: str) -> bool:
    """Main LR is a stereo strip: on a dual GEQ2/TEQ2 its L runs through side A and its R through side B.
    (Linked bus pairs are the same in principle; they are not modelled yet — see REVIEW_REPORT §2 C2.)"""
    return key == "main.st"


def geq_sides_for(key: str, fx_type: str | None, dual: set[str] | frozenset[str], side: str | None) -> tuple[str, ...]:
    """The GEQ sides a session on strip ``key`` must write: both for a stereo strip on a dual type,
    otherwise just its own."""
    if _is_stereo_strip(key) and fx_type in dual:
        return ("A", "B")
    return ((side or "A"),)


def _side_gains(fx: dict[str, Any], side: str | None) -> tuple[list[float] | None, float | None]:
    """(31 band gains dB, master dB) of ``side`` (``"A"``/``"B"``) of a decoded GEQ slot."""
    geq = fx.get("geq")
    if not geq:
        return None, None
    part = geq.get("b") if (side == "B" and geq.get("kind") == "dual") else geq.get("a")
    if not part:
        return None, None
    bands = [0.0 if b is None else float(b) for b in part.get("bands_db") or []]
    master = part.get("master_db")
    return bands, (None if master is None else float(master))


def _matched_session(reports: Any, bus: int | str, cuts: dict[int, float]) -> str | None:
    """The latest report for ``bus`` whose notches are all still on the GEQ at the same depth."""
    if reports is None or not cuts:
        return None
    try:
        rep = reports.latest_for_bus(bus)
    except Exception:  # a broken report dir must not block validation
        log.debug("report lookup for bus %s failed", bus, exc_info=True)
        return None
    if not rep:
        return None
    notches = rep.get("notches") or []
    if not notches:
        return None
    for n in notches:
        try:
            band, depth = int(n["band"]), float(n["depth_db"])
        except (KeyError, TypeError, ValueError):
            return None
        if band not in cuts or abs(cuts[band] - depth) > _FLAT_EPS:
            return None
    sid = rep.get("session_id")
    return str(sid) if sid else None


# -- validation -----------------------------------------------------------------------------------


async def validate_ringout_eqs(desk: Desk, buses: Sequence[int | str | Target], reports: Any = None) -> dict[int | str, GeqStatus]:
    """Read-only check that every bus in ``buses`` has a usable GEQ insert (see module doc for
    the reasons). Keyed by the bus number (``"main"`` for Main LR). ``reports`` (a
    ``ReportStore`` or ``None``) is only used to fill ``matched_session``."""
    d = _descriptor(desk)
    geq_hz = [float(h) for h in d.geq["band_hz"]]
    dual = set(d.geq.get("fx_types_dual", ()))
    stereo = set(d.geq.get("fx_types_stereo", ()))
    inserts = await desk.get_inserts()
    fxs = _FxCache(desk)
    out: dict[int | str, GeqStatus] = {}
    for bus in buses:
        t = bus_target(bus)
        key = bus_label(t)
        ins = inserts.get(t.key)
        reasons: list[str] = []
        info: InsertInfo | None = None
        bands: list[float] | None = None
        master: float | None = None
        notches: list[dict[str, Any]] = []
        matched: str | None = None
        if ins is None:
            reasons.append(f"{t.label} has no insert point")
        else:
            slot, side = ins.get("fx_slot"), ins.get("side")
            fx_type: str | None = None
            if slot is None:
                reasons.append(f"no FX insert on {t.label} (insert/sel is {ins.get('sel')!r}); run setup_ringout_eqs")
            else:
                fx = await fxs.get(slot)
                fx_type = fx.get("type")
                if fx_type not in dual and fx_type not in stereo:
                    reasons.append(f"FX slot {slot} holds {fx_type!r}, not a GEQ/TEQ")
                else:
                    if not ins.get("on"):
                        reasons.append(f"insert bypassed on {t.label} (insert/on is OFF)")
                    for other_key, other in inserts.items():
                        if other_key == t.key or other.get("fx_slot") != slot:
                            continue
                        other_label = parse_target(other_key).label
                        if fx_type in stereo:
                            reasons.append(f"stereo {fx_type} in FX slot {slot} is shared with {other_label}: a cut would hit both")
                        elif other.get("side") == side:
                            reasons.append(f"{ins.get('sel')} is also inserted on {other_label} (one insert point per strip, fx_routing_scenes.md §3.4)")
                        elif _is_stereo_strip(t.key) or _is_stereo_strip(other_key):
                            # a stereo strip on a dual GEQ runs L through side A and R through side B: the "other" side is
                            # not free, it is that strip's right channel (fx_routing_scenes.md §2.1/§3.4)
                            who = t.label if _is_stereo_strip(t.key) else other_label
                            reasons.append(f"dual {fx_type} in FX slot {slot}: {who} uses BOTH sides (L=A, R=B), so it cannot be shared with "
                                           f"{other_label if _is_stereo_strip(t.key) else t.label}")
                    bands, master = _side_gains(fx, side)
                    if bands is not None and _is_stereo_strip(t.key) and fx_type in dual:
                        b_bands, _bm = _side_gains(fx, "B")
                        if b_bands is not None and any(abs(a - b) > _FLAT_EPS for a, b in zip(bands, b_bands)):
                            reasons.append(f"dual {fx_type} in FX slot {slot} on {t.label}: sides A (L) and B (R) differ — "
                                           "even them up on the console first; CFS² writes both sides together")
                    if bands is not None:
                        cuts = {i + 1: g for i, g in enumerate(bands) if g < -_FLAT_EPS}
                        notches = [{"band": b, "freq_hz": geq_hz[b - 1], "depth_db": g} for b, g in sorted(cuts.items())]
                        matched = _matched_session(reports, key, cuts)
            info = InsertInfo(target=t.key, sel=str(ins.get("sel")), on=bool(ins.get("on")), pos=str(ins.get("pos")),
                              fx_slot=slot, side=side, fx_type=fx_type)
        flat = bands is not None and all(abs(g) <= _FLAT_EPS for g in bands) and (master is None or abs(master) <= _FLAT_EPS)
        out[key] = GeqStatus(bus=key, ok=not reasons, reasons=reasons, insert=info, bands_db=bands, flat=flat,
                             notches=notches, matched_session=matched, target=t.key, master_db=master)
    return out


# -- setup ----------------------------------------------------------------------------------------


async def plan_setup(desk: Desk, buses: Sequence[int | str | Target]) -> SetupPlan:
    """Decide how to give every bus a dual-mono GEQ side without touching the desk (rules in the
    module doc). Buses already inserting a GEQ are reused; the rest are paired L/R on free
    ``geq.insert_slots_preferred`` slots."""
    d = _descriptor(desk)
    dual = set(d.geq.get("fx_types_dual", ()))
    stereo = set(d.geq.get("fx_types_stereo", ()))
    preferred = [int(s) for s in d.geq.get("insert_slots_preferred", (5, 6, 7, 8))]
    load_type = str(d.geq.get("fx_types_dual", ["GEQ2"])[0])
    targets: list[Target] = []
    for b in buses:
        t = bus_target(b)
        if t not in targets:
            targets.append(t)
    plan = SetupPlan(buses=[bus_label(t) for t in targets])
    inserts = await desk.get_inserts()
    fxs = _FxCache(desk)
    ours = {t.key for t in targets}
    # who inserts what: (slot, side) -> strip key
    used: dict[tuple[int, str], str] = {}
    for key, ins in inserts.items():
        if ins.get("fx_slot") is not None and ins.get("side") is not None:
            used[(int(ins["fx_slot"]), str(ins["side"]))] = key
            if _is_stereo_strip(key):  # L through side A, R through side B: the whole slot is spoken for
                used.setdefault((int(ins["fx_slot"]), "B" if str(ins["side"]) == "A" else "A"), key)
    need: list[Target] = []
    for t in targets:
        ins = inserts.get(t.key)
        if ins is None:
            plan.blockers.append(f"{t.label} has no insert point")
            continue
        slot, side = ins.get("fx_slot"), ins.get("side")
        fx_type = (await fxs.get(slot)).get("type") if slot is not None else None
        if slot is not None and (fx_type in dual or fx_type in stereo):
            plan.reuse[bus_label(t)] = InsertInfo(t.key, str(ins.get("sel")), bool(ins.get("on")), str(ins.get("pos")), slot, side, fx_type)
            if not ins.get("on"):
                plan.inserts.append({"target": t.key, "bus": bus_label(t), "on": True})
            continue
        need.append(t)
    # partial reuse: a dual slot one of OUR buses already sits on has its other side free
    for t in list(need):
        if _is_stereo_strip(t.key):
            continue  # a stereo strip cannot live on half a slot
        for info in plan.reuse.values():
            if info.fx_slot is None or info.fx_type not in dual:
                continue
            other = "B" if info.side == "A" else "A"
            if (info.fx_slot, other) in used:
                continue
            used[(info.fx_slot, other)] = t.key
            plan.inserts.append({"target": t.key, "bus": bus_label(t), "sel": _sel_token(info.fx_slot, other), "on": True, "pos": _RINGOUT_INSERT_POS})
            plan.allocations.append(SlotAllocation(info.fx_slot, str(info.fx_type), str(info.fx_type), False,
                                                   {info.side or "A": _bus_of_key(info.target), other: bus_label(t)}))
            need.remove(t)
            break
    # fresh slots, two buses per dual GEQ
    for slot in preferred:
        if not need:
            break
        if (slot, "A") in used or (slot, "B") in used:
            continue  # inserted by some strip: not free (DESIGN §14)
        fx_type = (await fxs.get(slot)).get("type")
        stereo_t = need[0] if _is_stereo_strip(need[0].key) else None
        if stereo_t is not None:
            # Main LR (a stereo strip) takes the whole slot: a stereo GEQ, one set of pars driving L and R.
            need.remove(stereo_t)
            st_type = str(d.geq.get("fx_types_stereo", ["GEQ"])[0])
            load = fx_type != st_type
            plan.allocations.append(SlotAllocation(slot, st_type if load else str(fx_type), fx_type, load,
                                                   {"A": bus_label(stereo_t), "B": bus_label(stereo_t)}))
            if load:
                plan.type_loads.append({"slot": slot, "fx_type": st_type, "current_type": fx_type})
            used[(slot, "A")] = used[(slot, "B")] = stereo_t.key
            plan.inserts.append({"target": stereo_t.key, "bus": bus_label(stereo_t), "sel": _sel_token(slot, "A"), "on": True, "pos": _RINGOUT_INSERT_POS})
            continue
        pair = [t for t in need if not _is_stereo_strip(t.key)][:2]
        for t in pair:
            need.remove(t)
        sides: dict[str, int | str | None] = {"A": bus_label(pair[0]), "B": bus_label(pair[1]) if len(pair) > 1 else None}
        load = fx_type not in dual
        plan.allocations.append(SlotAllocation(slot, load_type if load else str(fx_type), fx_type, load, sides))
        if load:
            plan.type_loads.append({"slot": slot, "fx_type": load_type, "current_type": fx_type})
        for side, t in zip(("A", "B"), pair):
            used[(slot, side)] = t.key
            plan.inserts.append({"target": t.key, "bus": bus_label(t), "sel": _sel_token(slot, side), "on": True, "pos": _RINGOUT_INSERT_POS})
    for t in need:
        plan.blockers.append(f"no free insert-only FX slot ({', '.join(map(str, preferred))}) left for {t.label}")
    plan.needs_tier2 = not plan.empty
    return plan


async def apply_setup(desk: Desk, plan: SetupPlan, *, settle_deadline_s: float | None = None) -> dict[str, Any]:
    """Execute a :class:`SetupPlan` idempotently: a type load or insert field that already
    matches is skipped, so a second apply writes nothing. The caller (server) has already done
    the Tier-2 confirmation. Returns ``{buses, writes, skipped, changed, ok, verified, settle,
    geq}`` where ``geq`` is a fresh :func:`validate_ringout_eqs` result. When anything was
    written, validation is re-read (dropping the cached sections this apply touched) until it
    passes or ``settle_deadline_s`` (:data:`SETUP_SETTLE_S`) elapses: ``verified`` is then
    ``False`` and ``ok`` stays ``False`` — *not yet verified*, which the caller must not report
    as a failure (the desk applies inserts/type loads after it answers, HANDOVER §4b). With no
    writes ``verified`` is ``None`` and ``ok`` is the plain validation verdict."""
    if plan.blockers:
        raise DeskError("NOT_SUPPORTED", "setup plan has blockers: " + "; ".join(plan.blockers), blockers=list(plan.blockers))
    writes: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for tl in plan.type_loads:
        slot, fx_type = int(tl["slot"]), str(tl["fx_type"])
        current = (await desk.get_fx(slot)).get("type")
        if current == fx_type:
            skipped.append({"slot": slot, "fx_type": fx_type})
            continue
        res = await desk.set_fx_type(slot, fx_type)
        writes.append({"slot": slot, "fx_type": res.get("type", fx_type), "was": current})
    inserts = await desk.get_inserts() if plan.inserts else {}
    for item in plan.inserts:
        key = str(item["target"])
        cur = inserts.get(key) or {}
        kw = {k: item[k] for k in ("sel", "on", "pos") if k in item and item[k] is not None and cur.get(k) != item[k]}
        if not kw:
            skipped.append({"target": key})
            continue
        res = await desk.set_insert(key, **kw)
        writes.append({"target": key, **res.get("applied", kw)})
    touched = [f"/fx/{int(tl['slot'])}" for tl in plan.type_loads] + [
        f"{parse_target(str(item['target'])).osc_prefix}/insert" for item in plan.inserts]
    verified: bool | None = None
    settle: dict[str, Any] | None = None
    if writes:
        async def revalidate() -> dict[int | str, GeqStatus]:
            for path in touched:  # a stale answer must not be served again from our own cache
                desk.invalidate(path)
            return await validate_ringout_eqs(desk, plan.buses)

        deadline = SETUP_SETTLE_S if settle_deadline_s is None else float(settle_deadline_s)
        settled = await read_until(revalidate, lambda st: all(g.ok for g in st.values()), deadline_s=deadline,
                                   first_delay_s=0.05, retry_on=(), what="ring-out GEQ setup")
        status = settled.value if settled.value is not None else await validate_ringout_eqs(desk, plan.buses)
        verified = settled.ok
        settle = settled.to_dict()
        if not verified:
            for path in touched:  # leave nothing stale behind for the next validate
                desk.invalidate(path)
    else:
        status = await validate_ringout_eqs(desk, plan.buses)
    ok = all(s.ok for s in status.values())
    log.info("ring-out GEQ setup: %d write(s), %d skipped, valid=%s%s", len(writes), len(skipped), ok,
             "" if settle is None else f" (verified={verified} after {settle['attempts']} read(s), {settle['elapsed_ms']:.0f} ms)")
    return {
        "buses": list(plan.buses), "writes": writes, "skipped": skipped, "changed": len(writes), "ok": ok,
        "verified": verified, "settle": settle,
        "geq": {str(k): v.to_dict() for k, v in status.items()},
    }


# -- mic discovery ---------------------------------------------------------------------------------


def _patch_rows(patch: Any) -> dict[int, Any]:
    rows: dict[int, Any] = {}
    for row in getattr(patch, "rows", None) or ():
        ch = getattr(row, "channel", None)
        if isinstance(ch, int) and not isinstance(ch, bool):
            rows[ch] = row
    return rows


async def discover_mics(
    desk: Desk, bus: int | str | Target, *, patch: Any = None, floor_db: float | None = None,
    input_levels: Mapping[int, float] | None = None,
) -> list[MicCandidate]:
    """Which channels look like stage mics feeding ``bus`` (rules in the module doc). ``patch``
    is duck-typed: anything with ``rows`` whose items have ``channel``/``mic``/``owner``/
    ``monitor_bus``. ``floor_db`` overrides ``mics.send_floor_db`` (−40 dB). ``input_levels``
    ({channel: dBFS}, e.g. an average of ``/meters/1``) lets discovery say what routing cannot: an
    unmuted, routed channel with a physical preamp looks the same whether or not anything is plugged
    into it (HANDOVER §4b: seven "candidates", one microphone). A candidate whose input reads below
    ``mics.signal_floor_db`` gets a note and ``signal=False``; it is *not* excluded — a mic in a dead
    quiet room at low gain can read that low too — but the operator is told."""
    d = _descriptor(desk)
    sig_floor = float(d.mics.get("signal_floor_db", -80.0))
    t = bus_target(bus)
    key = bus_label(t)
    floor = float(d.mics.get("send_floor_db", -40.0)) if floor_db is None else float(floor_db)
    group = int(d.mics.get("mute_group_convention", 6))
    bit = 1 << (group - 1)  # fx_routing_scenes.md §5.1: bit 0 = mute group 1 … bit 5 = group 6
    rows = _patch_rows(patch)
    channels = d.strip_targets("ch")
    grp_paths = [f"{c.osc_prefix}/grp" for c in channels]
    grp_state = await desk.dump(sections=grp_paths)
    out: list[MicCandidate] = []
    any_in_group = False
    for c in channels:
        n = int(c.index)
        strip = await desk.get_strip(c)
        muted = bool(strip.get("muted"))
        source = str(strip.get("source") or "OFF")
        send_db: float | None
        send_on = True
        if t.family == "main":
            send_db = strip.get("fader_db") if strip.get("lr_assigned") else None
            send_on = bool(strip.get("lr_assigned"))
        else:
            sends = await desk.get_sends(c)
            s = sends[int(t.index) - 1]
            send_db = s.get("level_db")
            send_on = not bool(s.get("muted"))
        physical = (await desk.headamp_index_for(c)) is not None
        mask = grp_state.get(f"{c.osc_prefix}/grp/mute")
        in_group = (bool(int(mask) & bit)) if isinstance(mask, int) and not isinstance(mask, bool) else None
        any_in_group = any_in_group or bool(in_group)
        row = rows.get(n)
        patch_mic = getattr(row, "mic", None) if row is not None else None
        owner = getattr(row, "owner", None) if row is not None else None
        patch_bus = getattr(row, "monitor_bus", None) if row is not None else None
        sending = send_on and send_db is not None and send_db >= floor
        include = (not muted) and sending and physical
        notes: list[str] = []
        listed_here = bool(patch_mic) and (patch_bus is None or patch_bus == key)
        if not (include or listed_here or in_group or sending):
            continue
        if not physical and (sending or listed_here or in_group):
            notes.append(f"source {source} has no preamp (not a physical input)")
        if muted and (listed_here or in_group):
            who = "the patch" if listed_here else f"mute group {group}"
            notes.append(f"muted on the desk but {who} lists it as a stage mic")
        elif muted and sending:
            notes.append(f"sends to {t.label} but is muted")
        if not send_on and (listed_here or in_group):
            notes.append(f"send to {t.label} is switched off")
        elif send_db is None and (listed_here or in_group):
            notes.append(f"send to {t.label} is -oo")
        elif send_db is not None and send_db < floor and (listed_here or in_group):
            notes.append(f"send to {t.label} is {format_db(send_db)} dB, below the {floor:g} dB floor")
        if include and patch_mic is False:
            notes.append("routed and unmuted, but the patch says it is not a mic")
        if include and row is not None and patch_bus is not None and patch_bus != key:
            notes.append(f"patch assigns it to bus {patch_bus}, not {t.label}")
        input_db: float | None = None
        signal: bool | None = None
        if input_levels is not None and n in input_levels:
            input_db = float(input_levels[n])
            signal = input_db >= sig_floor
            if include and not signal:
                notes.append(f"no input signal ({format_db(input_db)} dBFS): nothing plugged in, a closed gate, or a dead-quiet source")
        out.append(MicCandidate(ch=n, name=str(strip.get("name") or ""), muted=muted, send_db=_db_out(send_db), source=source,
                                physical_input=physical, in_mute_group=in_group, patch_mic=patch_mic, owner=owner,
                                include=include, notes=notes, input_db=input_db, signal=signal))
    if any_in_group:  # the group convention is in use: an included mic outside it is worth a note
        for m in out:
            if m.include and m.in_mute_group is False:
                m.notes.append(f"not in mute group {group}")
    log.info("mic discovery for %s: %d candidate(s), %d included", t.label, len(out), sum(m.include for m in out))
    return out


# -- preflight ------------------------------------------------------------------------------------


async def preflight(desk: Desk, bus: int | str | Target, *, patch: Any = None, reports: Any = None,
                    input_levels: Mapping[int, float] | None = None) -> Preflight:
    """Step zero of ``feedback_watch``/``ring_out``: GEQ validation, mic discovery and the bus
    master, folded into blockers/warnings (module doc). ``input_levels`` as for :func:`discover_mics`."""
    d = _descriptor(desk)
    t = bus_target(bus)
    key = bus_label(t)
    strip = await desk.get_strip(t)
    fader = strip.get("fader_db")
    master = float("-inf") if fader is None else float(fader)
    muted = bool(strip.get("muted"))
    geq = (await validate_ringout_eqs(desk, [t], reports))[key]
    mics = await discover_mics(desk, t, patch=patch, input_levels=input_levels)
    blockers: list[str] = []
    warnings: list[str] = []
    if not geq.ok:
        blockers.extend(f"GEQ: {r}" for r in geq.reasons)
    included = [m for m in mics if m.include]
    if not included:
        blockers.append(f"no candidate mics: no unmuted physical input sends to {t.label} above {float(d.mics.get('send_floor_db', -40)):g} dB")
    if muted:
        blockers.append(f"{t.label} is muted — unmute it yourself first (CFS² never unmutes)")
    if math.isinf(master):
        blockers.append(f"{t.label} master is -oo — set a starting level first")
    warn_db = float(d.ringout.get("start_warn_db", -10.0))
    if not math.isinf(master) and master > warn_db:
        warnings.append(f"{t.label} master starts at {format_db(master)} dB, above the {warn_db:g} dB warning level")
    silent = [m for m in included if m.signal is False]
    if included and silent and len(silent) == len(included):
        warnings.append(f"none of the {len(included)} open mic(s) shows any input signal — is anything plugged in? "
                        "A ring-out with no live microphone measures nothing")
    elif silent:
        warnings.append(f"{len(silent)} of {len(included)} open mic(s) show no input signal (ch " + ", ".join(str(m.ch) for m in silent) + ")")
    for m in mics:
        for note in m.notes:
            warnings.append(f"ch.{m.ch} '{m.name}': {note}")
    if geq.ok and not geq.flat:
        cuts = ", ".join(f"{n['freq_hz']:g} Hz {n['depth_db']:+.1f} dB" for n in geq.notches)
        warnings.append(f"GEQ already carries cuts ({cuts or 'boosts/master offset'})"
                        + (f" matching ring-out session {geq.matched_session}" if geq.matched_session else ""))
    ok = not blockers
    log.info("preflight %s: %s (%d blocker(s), %d warning(s))", t.label, "ok" if ok else "BLOCKED", len(blockers), len(warnings))
    return Preflight(ok=ok, blockers=blockers, warnings=warnings, mics=mics, geq=geq, master_db=master,
                     bus_name=str(strip.get("name") or ""), bus=key, target=t.key, muted=muted)
