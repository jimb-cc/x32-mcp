"""Desk facade: typed reads and policy-enforced writes in engineering units (DESIGN.md §13).

Everything the MCP tools (and CFS²) need from the console goes through one :class:`Desk`. Reads
assemble strips from ``/node`` sections (transport.md §6) through a small TTL cache; writes go
``policy.tier_for → policy checks/clamps → ensure_pre_write_snapshot → policy.acquire_write →
conn.set``; mute inversion (``/…/mix/on`` 1 = ON = unmuted, scales_params.md §4.8) lives here and
nowhere above. All public results are plain dicts of dB / Hz / ms / % / 1-based numbers / enum
tokens — never raw OSC floats.

Decisions where DESIGN.md is silent (or where verified research overrides it):

* **Levels in results.** ``*_db`` fields are floats rounded to 0.1 dB, or ``None`` when the fader
  is fully down (−∞ is not JSON); a sibling text field (``fader``, ``level``, ``before``, ``after``)
  always carries the display form (``-oo``, ``-12.3``, ``+2.0``). Frequencies are rounded to
  0.1 Hz, gains/Q/ms to 0.01.
* **Node cache.** Parsed ``/node`` sections are cached per node path for
  ``policy.read_cache_ttl_s``; a pushed ``/xremote`` update or one of our own writes drops the
  section that owns the address; a scene recall, a restore or ``invalidate()`` drops everything.
  Concurrent misses on one path share a request. ``dump()`` primes the cache from the sweep.
* **Relative limit.** ``set_level`` applies ``policy.check_relative`` to the *actual* move
  (after clamping) when both endpoints are finite; a fade-in from −∞ or a fade-out to −∞ is
  bounded by the ceiling clamp / silence instead of the ±6 dB rule (a send that starts at −∞
  would otherwise always need ``force``).
* **Ramps** run as a task per address so a newer move on the same address cancels the older one
  (the older call returns ``superseded: True`` with the last level it wrote); the caller awaits
  its own ramp so ``after_db`` is what the desk received. No sleep after the final step.
* **Guarded addresses.** Tier-1 methods refuse a Tier-2 address (``DeskError("GUARDED")``) even
  when a caller aims a main target at ``set_level``; the Tier-2 executors (``set_main_level`` …)
  are the only path that writes them, and they still snapshot-before-write and rate-limit.
  ``recall_scene``/``save_scene`` also call ``policy.check_show_mode_allows`` as defence in depth.
* **Panic** writes 24 mutes (main st/m, bus 1–16, mtx 1–6 — fx_routing_scenes.md §11 option 1)
  with ``acquire_write(panic=True)``, no ramps, no pre-write snapshot (a 2000-node dump would cost
  seconds) and, while DEGRADED, falls back to ``send_raw`` (fire into the void, reported as
  ``delivered: "unconfirmed"``) rather than refusing.
* **Scene recall** uses ``/-action/goscene ,i`` (fx_routing_scenes.md §6.4); its reply is
  UNCONFIRMED, so the result is verified by polling ``/-show/prepos/current`` (≤ 2 s).
* **Restore** writes ``nodes.restore_plan`` lines through ``conn.slash`` (the ``/`` form,
  transport.md §6.6), one rate-limited line at a time, awaiting each echo; a timed-out line is
  listed in ``failed`` and the rest continue; ``NotConnected`` aborts.
* **GEQ** (fx_routing_scenes.md §2): ``set_geq_band(slot, side, band, gain_db)`` writes
  ``/fx/N/par/NN`` with ``descriptor.geq`` (dual types: side A = par 1–31 + master 32, side B =
  33–63 + master 64; stereo types: par 1–31 + 32 for both sides); ``band`` 32 addresses the
  master. ``get_fx`` decodes GEQ node tokens as dB (§2.2: the desk prints one-decimal dB).
* **Events**: ``conn.set`` already publishes ``write {address, args}`` (raw), so this module
  publishes ``desk.write {address, value, tier, tool, target}`` in engineering units instead of a
  second ``write`` event (deviation from DESIGN §13's event name), plus ``desk.snapshot``,
  ``desk.panic``, ``desk.scene`` and ``desk.restore``.
* **resolve()** widens DESIGN's "channel-name match" to every strip family (``families=`` narrows
  it) so ``"Wedge A"`` finds a bus; an exact (case-insensitive) name wins over substring matches;
  several hits → ``DeskError("AMBIGUOUS_NAME")`` listing the candidates.
* **Phantom by target** computes the head-amp index from ``config/source`` and
  ``/config/routing`` per scales_params.md §12.4 (UNCONFIRMED algorithm; user-in patches are not
  modelled) — pass an int head-amp index to bypass it.
* Extras beyond DESIGN §13: ``set_send_mute``, ``headamp_index_for``, ``invalidate``, ``close``,
  ``pre_write_snapshot``, ``stats``; ``get_sends`` items carry ``to``/``name`` beside
  ``bus``/``bus_name`` (bus → matrix sends).

Errors are :class:`DeskError` (``code`` + ``to_dict()`` for the tool envelope) — ``NOT_CONNECTED``,
``TIMEOUT``, ``BAD_ARGUMENT``, ``NOT_SUPPORTED``, ``GUARDED``, ``UNKNOWN_TARGET``, ``AMBIGUOUS_NAME``,
``NOT_A_GEQ``, ``SCENE_FAILED`` — or a :class:`~x32mcp.policy.PolicyError` passed through unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any, Iterable, Literal, Mapping, Sequence

from .connection import ConnectionState, NotConnected, RequestTimeout, X32Connection
from .descriptor import Descriptor, DescriptorError, NodePath, ParamSpec
from .events import EventBus
from .nodes import (
    DeskState,
    NodeParseError,
    Snapshot,
    SnapshotStore,
    dump_desk_state,
    parse_node_line,
    restore_plan,
)
from .policy import FADER_FLOOR_DB, Policy, Tier
from .scales import NEG_INF_DB, ScaleError, enum_to_index, format_db
from .targets import Target, TargetError, parse_target

__all__ = ["DeskError", "Desk", "ALL_FAMILIES"]

log = logging.getLogger(__name__)

ALL_FAMILIES: tuple[str, ...] = ("ch", "auxin", "fxrtn", "bus", "mtx", "main", "dca")
_PANIC_FAMILIES: tuple[str, ...] = ("main", "bus", "mtx")  # fx_routing_scenes.md §11 option 1
_SCENE_VERIFY_S = 2.0
_SCENE_POLL_S = 0.05
_NAME_MAX = 12  # scales_params.md §4.1
_NAME_BAD = '"'  # transport.md §6.3/§6.6: node text has no escape for a quote; the desk's parser stops at it
_SHOW_CONTROL = "/-prefs/show_control"  # fx_routing_scenes.md §6.2: only SCENES makes prepos/current a scene slot


class DeskError(Exception):
    """A refused or failed desk operation; ``code`` is the machine token for the tool envelope."""

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


# -- value helpers -----------------------------------------------------------------------------


_UNSET: Any = object()  # "nothing written yet" in a ramp; None and 0.0 are both real raw values


def _db1(v: Any) -> float | None:
    """dB rounded to 0.1 (half away from zero, like the desk).

    −∞ is preserved as ``-inf`` (the server renders it ``"-oo"``, the convention used by
    snapshots and ``scales.parse_db``); ``None`` means *not read*. Collapsing both to ``None``
    made "fader fully down" indistinguishable from "unknown" — a real strip at −∞ then reported
    ``fader_db: null``, which reads as a failed read rather than a closed fader.
    """
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if math.isinf(v):
        return float(v)
    return float(format_db(float(v)))


def _db_text(v: Any) -> str | None:
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return format_db(float(v))


def _r(v: Any, nd: int = 2) -> Any:
    """Round a numeric engineering value for output; passes everything else through."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return v
    if isinstance(v, int):
        return v
    if math.isinf(v) or math.isnan(v):
        return None
    return round(v, nd)


def _jsonable(v: Any) -> Any:
    if isinstance(v, float):
        if math.isinf(v):
            return "-oo" if v < 0 else "+oo"
        if math.isnan(v):
            return None
        return v
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def _num(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        if isinstance(value, str) and value.strip() in ("-oo", "-inf"):
            return NEG_INF_DB
        raise DeskError("BAD_ARGUMENT", f"{what} must be a number, got {value!r}")
    f = float(value)
    if math.isnan(f):
        raise DeskError("BAD_ARGUMENT", f"{what} must be a number, got NaN")
    return f


def _int(value: Any, what: str, lo: int | None = None, hi: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not value.is_integer()):
        raise DeskError("BAD_ARGUMENT", f"{what} must be an integer, got {value!r}")
    v = int(value)
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise DeskError("BAD_ARGUMENT", f"{what} must be {lo}..{hi}, got {v}")
    return v


def _insert_side(sel: Any) -> tuple[int | None, str | None]:
    """``"FX5L"`` → (5, "A"), ``"FX5R"`` → (5, "B"); OFF/AUXn → (None, None). fx_routing_scenes.md §3.2."""
    if isinstance(sel, str) and len(sel) == 4 and sel.startswith("FX") and sel[2].isdigit() and sel[3] in "LR":
        return int(sel[2]), "A" if sel[3] == "L" else "B"
    return None, None


class Desk:
    """High-level, policy-enforced view of one console (see module doc).

    ``d`` is the descriptor, ``conn`` a connected :class:`~x32mcp.connection.X32Connection`,
    ``policy`` the session's :class:`~x32mcp.policy.Policy`, ``events`` the bus and ``snapshots``
    the store the pre-write snapshot goes to. Targets may be passed as :class:`Target` objects
    or spellings :func:`~x32mcp.targets.parse_target` accepts; name matching is :meth:`resolve`.
    """

    def __init__(self, d: Descriptor, conn: X32Connection, policy: Policy, events: EventBus, snapshots: SnapshotStore) -> None:
        self._d = d
        self._conn = conn
        self._policy = policy
        self._events = events
        self._snapshots = snapshots
        self._ttl = float(d.policy.get("read_cache_ttl_s", 2.0))
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._epoch: dict[str, int] = {}  # invalidations of in-flight paths
        self._inflight: dict[str, asyncio.Future] = {}
        self._dropped_since: set[str] | None = None
        self._node_by_path: dict[str, NodePath] = {n.path: n for n in d.nodes()}
        self._leaf_index: dict[str, tuple[NodePath, str]] = {}
        for n in d.nodes():
            for addr, field in zip(n.addresses, n.fields):
                self._leaf_index.setdefault(addr, (n, field))
        self._ramps: dict[str, asyncio.Task] = {}
        self._snapshot_lock = asyncio.Lock()
        self.pre_write_snapshot: Snapshot | None = None
        self.write_count = 0
        geq = d.geq
        self._geq_dual = frozenset(geq.get("fx_types_dual", ()))
        self._geq_stereo = frozenset(geq.get("fx_types_stereo", ()))
        self._geq_hz: tuple[float, ...] = tuple(float(h) for h in geq["band_hz"])
        self._geq_scale = d.scale(str(geq.get("gain_scale", "geq_gain")))
        self._geq_layout = {k: int(geq.get(k, v)) for k, v in (("par_a_first", 1), ("par_a_master", 32), ("par_b_first", 33), ("par_b_master", 64))}
        self._unsub_update = conn.on_update(self._on_push)
        # conn.set() publishes "write" {address, args}: writes by other modules through the same
        # connection (meters.set_rta_source, provision, …) are never pushed back to us by the desk.
        self._unsub_write = events.subscribe(self._on_write_event, types={"write"})

    # -- lifecycle / cache ---------------------------------------------------------------------

    async def close(self) -> None:
        """Cancel running ramps and stop listening for pushes (the connection is not closed)."""
        self._unsub_update()
        self._unsub_write()
        ramps = [t for t in self._ramps.values() if not t.done()]
        self._ramps.clear()
        for t in ramps:
            t.cancel()
        if ramps:
            await asyncio.wait(ramps)

    def invalidate(self, prefix: str | None = None) -> None:
        """Drop cached sections under ``prefix`` (an address or node path), or everything."""
        if prefix is None:
            for p in list(self._cache):
                self._drop(p)
            for p in list(self._inflight):
                self._drop(p)
            return
        pre = prefix.rstrip("/")
        for p in list(self._cache) + list(self._inflight):
            if p == pre or p.startswith(pre + "/"):
                self._drop(p)
        self._invalidate_address(pre)

    @property
    def stats(self) -> dict[str, int]:
        return {"cached_sections": len(self._cache), "inflight": len(self._inflight), "writes": self.write_count, "ramps": len(self._ramps)}

    def _drop(self, path: str) -> None:
        self._cache.pop(path, None)
        if path in self._inflight:
            self._epoch[path] = self._epoch.get(path, 0) + 1
        if self._dropped_since is not None:
            self._dropped_since.add(path)

    def _invalidate_address(self, address: str) -> None:
        hit = self._leaf_index.get(address)
        if hit is not None:
            self._drop(hit[0].path)
        elif address in self._node_by_path:
            self._drop(address)

    def _on_push(self, address: str, args: tuple) -> None:
        # DESIGN §0.6: the desk is the source of truth — a pushed change retires our copy.
        self._invalidate_address(address)

    def _on_write_event(self, ev: Any) -> None:
        addr = ev.data.get("address") if isinstance(ev.data, dict) else None
        if isinstance(addr, str):
            self._invalidate_address(addr)

    def _parse(self, path: str, line: str | None) -> dict[str, Any] | None:
        if line is None:
            return None
        node = self._node_by_path.get(path)
        if node is None:
            return None
        try:
            return parse_node_line(line, node)
        except NodeParseError as e:
            log.warning("node %s: unparsable reply %r: %s", path, line, e)
            return None

    async def _read_sections(self, paths: Sequence[str], *, concurrency: int = 16) -> dict[str, dict[str, Any] | None]:
        """Parsed sections for ``paths`` (cache → shared in-flight request → ``conn.node_many``).
        A path that did not answer maps to ``None``."""
        now = time.monotonic()
        out: dict[str, dict[str, Any] | None] = {}
        waits: dict[str, asyncio.Future] = {}
        fetch: list[str] = []
        loop = asyncio.get_running_loop()
        for p in dict.fromkeys(paths):
            hit = self._cache.get(p)
            if hit is not None and now - hit[0] <= self._ttl:
                out[p] = hit[1]
                continue
            fut = self._inflight.get(p)
            if fut is None:
                fut = loop.create_future()
                self._inflight[p] = fut
                fetch.append(p)
            waits[p] = fut
        if fetch:
            epochs = {p: self._epoch.get(p, 0) for p in fetch}
            try:
                lines = await self._conn.node_many(fetch, concurrency)
            except BaseException as e:
                for p in fetch:
                    f = self._inflight.pop(p, None)
                    if f is not None and not f.done():
                        # Never cancel() a shared future: asyncio.shield does not stop an *inner*
                        # cancellation reaching the waiters, so another tool call would die with a
                        # CancelledError it never asked for. Hand it an error it can report instead.
                        f.set_exception(e if isinstance(e, Exception) else DeskError("TIMEOUT", f"read of {p} was abandoned"))
                        f.exception()  # mark retrieved: nobody may await this one
                raise
            t1 = time.monotonic()
            for p in fetch:
                vals = self._parse(p, lines.get(p))
                if vals is not None and self._epoch.get(p, 0) == epochs[p]:
                    self._cache[p] = (t1, vals)
                self._epoch.pop(p, None)
                f = self._inflight.pop(p, None)
                if f is not None and not f.done():
                    f.set_result(vals)
        for p, fut in waits.items():
            try:
                out[p] = await asyncio.shield(fut)
            except asyncio.CancelledError:
                cur = asyncio.current_task()
                if cur is not None and cur.cancelling():
                    raise  # we really are being cancelled
                raise DeskError("TIMEOUT", f"read of {p} was abandoned by another caller") from None
        return out

    def _unreachable(self, path: str) -> DeskError:
        state = self._conn.state
        if state is not ConnectionState.CONNECTED:
            err = self._conn.status.error
            return DeskError("NOT_CONNECTED", f"desk is {state.value}{': ' + err if err else ''}")
        return DeskError("TIMEOUT", f"no /node reply for {path} from the desk")

    def _require(self, sections: Mapping[str, dict[str, Any] | None], path: str) -> dict[str, Any]:
        vals = sections.get(path)
        if vals is None:
            raise self._unreachable(path)
        return vals

    async def _section(self, path: str) -> dict[str, Any]:
        return self._require(await self._read_sections([path]), path)

    async def _leaf(self, address: str) -> Any:
        """Engineering value of one leaf address via its node section (``None`` if not printed)."""
        hit = self._leaf_index.get(address)
        if hit is None:
            raise DeskError("NOT_SUPPORTED", f"{address} is not a parameter of this desk")
        node, field = hit
        return (await self._section(node.path)).get(field)

    # -- targets / addresses ---------------------------------------------------------------------

    def _target(self, t: Target | str | int) -> Target:
        try:
            return parse_target(t)
        except TargetError as e:
            raise DeskError("BAD_ARGUMENT", str(e)) from None

    def _param(self, t: Target, rel: str, **vars: Any) -> tuple[ParamSpec, str]:
        """(spec, concrete address) of ``rel`` on strip ``t``; NOT_SUPPORTED when the family lacks it."""
        try:
            spec = self._d.param(t.family, rel)
            return spec, spec.address(t, **vars)
        except DescriptorError as e:
            raise DeskError("NOT_SUPPORTED", f"{t.label} has no {rel.format(**vars) if vars else rel} ({e.message})") from None

    def _section_path(self, t: Target, section: str) -> str | None:
        path = f"{t.osc_prefix}/{section}" if section else t.osc_prefix
        return path if path in self._node_by_path else None

    def _level_param(self, t: Target, kind: str, send_to: int | None) -> tuple[ParamSpec, str, str]:
        """(spec, address, policy kind) for a fader / send / M-C level of ``t``."""
        if send_to is not None or kind == "send":
            if send_to is None:
                raise DeskError("BAD_ARGUMENT", "send_to (destination bus number) is required for kind='send'")
            st = self._d.strips[t.family]
            if not st.sends:
                raise DeskError("NOT_SUPPORTED", f"{t.label} has no sends")
            n = _int(send_to, "send_to", 1, st.sends)
            spec, addr = self._param(t, "mix/{send:02d}/level", send=n)
            return spec, addr, "send"
        if kind == "mlevel":
            spec, addr = self._param(t, "mix/mlevel")
            return spec, addr, "send"
        if kind != "fader":
            raise DeskError("BAD_ARGUMENT", f"kind must be 'fader', 'send' or 'mlevel', got {kind!r}")
        spec, addr = self._param(t, "fader" if t.family == "dca" else "mix/fader")
        return spec, addr, "fader"

    # -- reads ---------------------------------------------------------------------------------

    async def get_strip(self, t: Target | str | int) -> dict[str, Any]:
        """Everything a mixer wants to know about one strip, in engineering units (see module doc
        for the level convention). Sections the family lacks are ``None``."""
        t = self._target(t)
        fam = self._d.strips[t.family]
        root = t.osc_prefix
        wanted: dict[str, str | None] = {
            "config": self._section_path(t, "config"),
            "mix": self._section_path(t, "" if t.family == "dca" else "mix"),
            "preamp": self._section_path(t, "preamp"),
            "eq": self._section_path(t, "eq"),
            "dyn": self._section_path(t, "dyn"),
            "gate": self._section_path(t, "gate"),
            "insert": self._section_path(t, "insert"),
        }
        bands = [self._section_path(t, f"eq/{b}") for b in range(1, (fam.eq_bands or 0) + 1)]
        paths = [p for p in list(wanted.values()) + bands if p]
        secs = await self._read_sections(paths)
        cfg = self._require(secs, wanted["config"]) if wanted["config"] else {}
        mix = self._require(secs, wanted["mix"]) if wanted["mix"] else {}
        on_key, fader_key = ("on", "fader") if t.family == "dca" else ("mix/on", "mix/fader")
        out: dict[str, Any] = {
            "target": t.key,
            "label": t.label,
            "family": t.family,
            "index": t.index,
            "name": cfg.get("config/name"),
            "icon": cfg.get("config/icon"),
            "color": cfg.get("config/color"),
            "source": cfg.get("config/source"),
            "fader_db": _db1(mix.get(fader_key)),
            "fader": _db_text(mix.get(fader_key)),
            "muted": (not mix[on_key]) if mix.get(on_key) is not None else None,  # wire 1 = ON = unmuted
            "pan": mix.get("mix/pan"),
            "lr_assigned": mix.get("mix/st"),
            "mono_assigned": mix.get("mix/mono"),
            "mono_level_db": _db1(mix.get("mix/mlevel")),
            "mono_level": _db_text(mix.get("mix/mlevel")),
            "preamp": self._preamp_dict(secs.get(wanted["preamp"]) if wanted["preamp"] else None),
            "eq": self._eq_dict(secs, wanted["eq"], bands) if wanted["eq"] else None,
            "comp": self._comp_dict(secs.get(wanted["dyn"]) if wanted["dyn"] else None),
            "gate": self._gate_dict(secs.get(wanted["gate"]) if wanted["gate"] else None),
            "insert": self._insert_dict(secs.get(wanted["insert"]) if wanted["insert"] else None),
        }
        return out

    async def get_channel(self, ch: int) -> dict[str, Any]:
        return await self.get_strip(Target("ch", _int(ch, "channel", 1, 32)))

    async def get_bus(self, bus: int) -> dict[str, Any]:
        return await self.get_strip(Target("bus", _int(bus, "bus", 1, 16)))

    async def get_main(self) -> dict[str, Any]:
        """``{"st": …, "m": …}`` — Main LR and Main M/C strips."""
        return {"st": await self.get_strip(Target("main", "st")), "m": await self.get_strip(Target("main", "m"))}

    async def get_sends(self, t: Target | str | int) -> list[dict[str, Any]]:
        """Sends of an input strip (16, to buses) or of a bus/main (6, to matrices):
        ``[{bus, bus_name, to, name, level_db, level, muted, pan, type}]``. Even-numbered sends
        report the pan/type of their odd partner (the stereo pair shares them, scales_params.md §4.9)."""
        t = self._target(t)
        st = self._d.strips[t.family]
        if not st.sends:
            raise DeskError("NOT_SUPPORTED", f"{t.label} has no sends")
        dest = st.send_target or "bus"
        n = st.sends
        send_paths = [f"{t.osc_prefix}/mix/{i:02d}" for i in range(1, n + 1)]
        name_paths = [f"{Target(dest, i).osc_prefix}/config" for i in range(1, n + 1)]
        secs = await self._read_sections(send_paths + name_paths)
        out: list[dict[str, Any]] = []
        for i in range(1, n + 1):
            v = self._require(secs, send_paths[i - 1])
            odd = v if i % 2 else (secs.get(send_paths[i - 2]) or {})
            j = i if i % 2 else i - 1
            cfg = secs.get(name_paths[i - 1]) or {}
            to = Target(dest, i)
            level = v.get(f"mix/{i:02d}/level")
            on = v.get(f"mix/{i:02d}/on")
            out.append({
                "bus": i, "bus_name": cfg.get("config/name"), "to": to.key, "name": cfg.get("config/name"),
                "level_db": _db1(level), "level": _db_text(level),
                "muted": (not on) if on is not None else None,
                "pan": odd.get(f"mix/{j:02d}/pan"), "type": odd.get(f"mix/{j:02d}/type"),
            })
        return out

    async def get_eq(self, t: Target | str | int) -> dict[str, Any]:
        """``{"on": bool, "bands": [{band, type, freq_hz, gain_db, q}]}``."""
        t = self._target(t)
        fam = self._d.strips[t.family]
        eq = self._section_path(t, "eq")
        if eq is None or not fam.eq_bands:
            raise DeskError("NOT_SUPPORTED", f"{t.label} has no EQ")
        bands = [self._section_path(t, f"eq/{b}") for b in range(1, fam.eq_bands + 1)]
        secs = await self._read_sections([eq] + [b for b in bands if b])
        self._require(secs, eq)
        return self._eq_dict(secs, eq, bands)  # type: ignore[return-value]

    async def get_dynamics(self, t: Target | str | int) -> dict[str, Any]:
        """``{"gate": {...} | None, "comp": {...}}`` (gate only on channels)."""
        t = self._target(t)
        dyn, gate = self._section_path(t, "dyn"), self._section_path(t, "gate")
        if dyn is None:
            raise DeskError("NOT_SUPPORTED", f"{t.label} has no dynamics")
        secs = await self._read_sections([p for p in (dyn, gate) if p])
        self._require(secs, dyn)
        return {"target": t.key, "gate": self._gate_dict(secs.get(gate)) if gate else None, "comp": self._comp_dict(secs.get(dyn))}

    async def get_names(self, family: str = "ch") -> dict[Any, str]:
        """Strip names of a family (``{1: "Kick", …}``; ``{"st": "Main", "m": "M/C"}`` for main)."""
        if family not in self._d.strips:
            raise DeskError("BAD_ARGUMENT", f"unknown strip family {family!r}; known: {list(self._d.strips)}")
        targets = self._d.strip_targets(family)
        paths = [f"{t.osc_prefix}/config" for t in targets]
        secs = await self._read_sections(paths)
        out: dict[Any, str] = {}
        for t, p in zip(targets, paths):
            v = secs.get(p)
            if v is None:
                raise self._unreachable(p)
            out[t.index] = str(v.get("config/name") or "")
        return out

    async def resolve(self, s: Target | str | int, *, families: Sequence[str] = ALL_FAMILIES) -> Target:
        """A target spelling (``ch.5``, ``bus 3``, ``5``) or a unique case-insensitive strip-name
        match (``"tony"`` → ch.5 'Vox Tony'; an exact name wins over substrings). Raises
        ``DeskError`` ``UNKNOWN_TARGET`` / ``AMBIGUOUS_NAME`` (with ``candidates``)."""
        if isinstance(s, Target):
            return s
        if isinstance(s, bool) or not isinstance(s, (int, str)):
            raise DeskError("BAD_ARGUMENT", f"target must be a string or channel number, got {s!r}")
        if isinstance(s, int):
            return self._target(s)
        text = s.strip()
        if not text:
            raise DeskError("BAD_ARGUMENT", "empty target")
        try:
            return parse_target(text)
        except TargetError as e:
            parse_err = e
        needle = text.lower()
        exact: list[tuple[Target, str]] = []
        partial: list[tuple[Target, str]] = []
        for fam in families:
            for idx, name in (await self.get_names(fam)).items():
                n = name.strip().lower()
                if not n:
                    continue
                t = Target(fam, idx)
                if n == needle:
                    exact.append((t, name))
                elif needle in n:
                    partial.append((t, name))
        hits = exact if len(exact) == 1 else (exact or partial)
        if len(hits) == 1:
            return hits[0][0]
        if not hits:
            raise DeskError("UNKNOWN_TARGET", f"{s!r} is not a strip spelling ({parse_err}) and no strip name contains it")
        cands = [{"target": t.key, "label": t.label, "name": name} for t, name in hits]
        raise DeskError(
            "AMBIGUOUS_NAME",
            f"{s!r} matches {len(hits)} strips: " + ", ".join(f"{c['target']} '{c['name']}'" for c in cands),
            candidates=cands,
        )

    async def list_scenes(self) -> list[dict[str, Any]]:
        """All 100 scene slots: ``[{index, name, notes, has_data}]``."""
        paths = [f"/-show/showfile/scene/{i:03d}" for i in range(100)]
        secs = await self._read_sections(paths)
        out = []
        for i, p in enumerate(paths):
            v = self._require(secs, p)
            k = f"showfile/scene/{i:03d}/"
            out.append({"index": i, "name": v.get(k + "name") or "", "notes": v.get(k + "notes") or "", "has_data": bool(v.get(k + "hasdata"))})
        return out

    async def _show_control(self) -> str | None:
        """``/-prefs/show_control`` as a token (``CUES``/``SCENES``/``SNIPPETS``), ``None`` when the
        desk did not answer. Read off the bare address: no ``/node`` section covers this leaf, and
        X32GetLib.c/X32SetLib.c read and restore it exactly this way (fx_routing_scenes.md §6.2)."""
        try:
            raw = await self._conn.get(_SHOW_CONTROL)
        except RequestTimeout:
            return None
        except NotConnected as e:
            raise DeskError("NOT_CONNECTED", str(e)) from None
        hit = self._d.param_for_address(_SHOW_CONTROL)
        if hit is None:
            return None
        try:
            return str(hit[0].to_value(raw))
        except (ScaleError, ValueError, TypeError):
            log.warning("unreadable %s: %r", _SHOW_CONTROL, raw)
            return None

    def _not_scenes(self, sc: str) -> str:
        return (f"the desk's scene page is showing {sc}, not SCENES: /-show/prepos/current indexes that list, "
                "not the scenes (fx_routing_scenes.md §6.2) — set Setup/show control to SCENES on the desk")

    async def current_scene(self) -> dict[str, Any]:
        """``{index, name, has_data, show_control}`` of ``/-show/prepos/current``
        (fx_routing_scenes.md §6.2). The pointer only holds a scene slot while
        ``/-prefs/show_control`` is SCENES; with CUES/SNIPPETS it indexes those lists instead, so
        ``index`` is ``None`` rather than a wrong scene number. DESIGN.md is silent on the
        preference: we read it and report, we do not write it (GetSceneName.c forces it to SCENES)
        — a Tier 0 read must not change what the operator sees on the desk."""
        sc = await self._show_control()
        if sc is not None and sc != "SCENES":
            return {"index": None, "name": "", "has_data": False, "show_control": sc, "note": self._not_scenes(sc)}
        idx = await self._leaf("/-show/prepos/current")
        if not isinstance(idx, int) or isinstance(idx, bool):
            raise DeskError("TIMEOUT", "desk did not report /-show/prepos/current")
        if idx < 0:
            # Observed on a real X32 Rack (FW 4.13) that had been powered up without recalling a
            # scene: the pointer reads -1. Reporting "scene -1" reads as a real slot number to
            # both the model and the operator, so say plainly that nothing is loaded.
            return {"index": None, "name": "", "has_data": False, "show_control": sc,
                    "note": "no scene is currently loaded (the desk's scene pointer is -1)"}
        return {"index": idx, **(await self._scene_info(idx)), "show_control": sc}

    async def _scene_info(self, idx: int) -> dict[str, Any]:
        if not 0 <= idx <= 99:
            return {"name": "", "has_data": False}
        p = f"/-show/showfile/scene/{idx:03d}"
        v = (await self._read_sections([p])).get(p) or {}
        k = f"showfile/scene/{idx:03d}/"
        return {"name": v.get(k + "name") or "", "has_data": bool(v.get(k + "hasdata"))}

    async def dump(self, *, sections: Iterable[str] | None = None) -> DeskState:
        """Full (or scoped) ``/node`` sweep via :func:`nodes.dump_desk_state`; primes the cache."""
        mine = self._dropped_since is None
        if mine:
            self._dropped_since = set()
        dropped = self._dropped_since
        try:
            state = await dump_desk_state(self._conn, self._d, sections=sections)
        except ValueError as e:  # bad scope spelling
            raise DeskError("BAD_ARGUMENT", str(e)) from None
        finally:
            if mine:
                self._dropped_since = None
        if not state.sections:
            raise self._unreachable("desk state")
        now = time.monotonic()
        for p, vals in state.sections.items():
            if p not in self._inflight and p not in dropped:  # a push during the sweep beats the sweep
                self._cache[p] = (now, vals)
        return state

    async def get_fx(self, slot: int) -> dict[str, Any]:
        """``{slot, type, source, params, geq}`` — ``geq`` decodes GEQ/TEQ bands to dB
        (``{"kind": "dual"|"stereo", "band_hz", "a": {"bands_db", "master_db"}, "b": …|None}``)."""
        n = _int(slot, "fx slot", 1, 8)
        paths = [f"/fx/{n}", f"/fx/{n}/par"] + ([f"/fx/{n}/source"] if n <= 4 else [])
        secs = await self._read_sections(paths)
        head = self._require(secs, paths[0])
        par = secs.get(paths[1]) or {}
        src = secs.get(paths[2]) if n <= 4 else None
        fx_type = head.get("type")
        tokens = [par.get(f"par/{i:02d}") for i in range(1, 65)]
        geq = None
        if fx_type in self._geq_dual or fx_type in self._geq_stereo:
            def _side(first: int, master: int) -> dict[str, Any]:
                return {
                    "bands_db": [self._geq_token_db(tokens[first - 1 + i]) for i in range(31)],
                    "master_db": self._geq_token_db(tokens[master - 1]),
                }
            lay = self._geq_layout
            dual = fx_type in self._geq_dual
            geq = {
                "kind": "dual" if dual else "stereo",
                "band_hz": list(self._geq_hz),
                "a": _side(lay["par_a_first"], lay["par_a_master"]),
                "b": _side(lay["par_b_first"], lay["par_b_master"]) if dual else None,
            }
        return {
            "slot": n, "type": fx_type,
            "source": {"l": src.get("source/l"), "r": src.get("source/r")} if src else None,
            "is_geq": geq is not None, "geq": geq, "params": tokens,
        }

    @staticmethod
    def _geq_token_db(tok: Any) -> float | None:
        # fx_routing_scenes.md §2.2: the desk prints GEQ pars as one-decimal dB
        if tok is None:
            return None
        try:
            return float(str(tok).strip())
        except ValueError:
            return None

    async def get_inserts(self) -> dict[str, dict[str, Any]]:
        """``{target key: {on, pos, sel, fx_slot, side}}`` for every strip that has an insert."""
        targets = [t for fam in self._d.strips.values() if fam.insert for t in fam.targets()]
        paths = [f"{t.osc_prefix}/insert" for t in targets]
        secs = await self._read_sections(paths)
        out: dict[str, dict[str, Any]] = {}
        for t, p in zip(targets, paths):
            v = self._require(secs, p)
            out[t.key] = self._insert_dict(v) or {}
        return out

    async def headamp_index_for(self, t: Target | str | int) -> int | None:
        """Head-amp index (0..127) behind an input strip's source, or ``None`` when no preamp sits
        there (USB, card, FX return…). scales_params.md §12.4 (UNCONFIRMED, no user-in patches)."""
        t = self._target(t)
        if t.family not in ("ch", "auxin"):
            return None
        source = await self._leaf(f"{t.osc_prefix}/config/source")
        if not isinstance(source, str):
            return None
        tokens = self._d.enum("ch_source")
        try:
            src = tokens.index(source)
        except ValueError:
            return None
        routing = await self._read_sections(["/config/routing", "/config/routing/IN", "/config/routing/PLAY"])
        play = (routing.get("/config/routing") or {}).get("routing/routswitch") == "PLAY"
        blocks = self._require(routing, "/config/routing/PLAY" if play else "/config/routing/IN")
        key = "routing/PLAY/" if play else "routing/IN/"
        in_tokens = self._d.enum("routing_in")
        aux_tokens = self._d.enum("routing_in_aux")

        def block_base(r: int) -> int | None:  # 8-channel block enum → first head-amp index
            if 0 <= r <= 3:
                return r * 8
            if 4 <= r <= 9:
                return 32 + (r - 4) * 8
            if 10 <= r <= 15:
                return 80 + (r - 10) * 8
            return None  # CARD / UIN: no head amp we can name

        if 1 <= src <= 32:
            k = src - 1
            b, off = divmod(k, 8)
            tok = blocks.get(key + ("1-8", "9-16", "17-24", "25-32")[b])
            r = in_tokens.index(tok) if tok in in_tokens else -1
            base = block_base(r)
            return None if base is None else base + off
        if 33 <= src <= 38:
            a = src - 33
            tok = blocks.get(key + "AUX")
            r = aux_tokens.index(tok) if tok in aux_tokens else -1
            for lo, base in ((1, 0), (4, 32), (7, 80)):
                if lo <= r <= lo + 2:
                    return base + a if a < (2, 4, 6)[r - lo] else None
            return None
        return None

    # -- section decoders --------------------------------------------------------------------------

    @staticmethod
    def _preamp_dict(v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is None:
            return None
        out: dict[str, Any] = {}
        if "preamp/trim" in v:
            out["trim_db"] = _r(v.get("preamp/trim"), 2)
        if "preamp/invert" in v:
            out["invert"] = v.get("preamp/invert")
        if "preamp/hpon" in v:
            out.update(hpf_on=v.get("preamp/hpon"), hpf_slope=v.get("preamp/hpslope"), hpf_hz=_r(v.get("preamp/hpf"), 1))
        return out

    @staticmethod
    def _eq_dict(secs: Mapping[str, dict[str, Any] | None], eq_path: str, band_paths: Sequence[str | None]) -> dict[str, Any] | None:
        head = secs.get(eq_path)
        if head is None:
            return None
        bands = []
        for i, p in enumerate(band_paths, start=1):
            v = secs.get(p) if p else None
            if v is None:
                continue
            k = f"eq/{i}/"
            bands.append({
                "band": i, "type": v.get(k + "type"), "freq_hz": _r(v.get(k + "f"), 1),
                "gain_db": _r(v.get(k + "g"), 2), "q": _r(v.get(k + "q"), 2),
            })
        return {"on": head.get("eq/on"), "bands": bands}

    @staticmethod
    def _comp_dict(v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is None:
            return None
        return {
            "on": v.get("dyn/on"), "mode": v.get("dyn/mode"), "detector": v.get("dyn/det"), "envelope": v.get("dyn/env"),
            "threshold_db": _r(v.get("dyn/thr"), 1), "ratio": v.get("dyn/ratio"), "knee": _r(v.get("dyn/knee"), 0),
            "makeup_db": _r(v.get("dyn/mgain"), 2), "attack_ms": _r(v.get("dyn/attack"), 1), "hold_ms": _r(v.get("dyn/hold"), 2),
            "release_ms": _r(v.get("dyn/release"), 1), "position": v.get("dyn/pos"), "key_source": v.get("dyn/keysrc"),
            "mix_pct": _r(v.get("dyn/mix"), 0), "auto": v.get("dyn/auto"),
        }

    @staticmethod
    def _gate_dict(v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is None:
            return None
        return {
            "on": v.get("gate/on"), "mode": v.get("gate/mode"), "threshold_db": _r(v.get("gate/thr"), 1),
            "range_db": _r(v.get("gate/range"), 1), "attack_ms": _r(v.get("gate/attack"), 1), "hold_ms": _r(v.get("gate/hold"), 2),
            "release_ms": _r(v.get("gate/release"), 1), "key_source": v.get("gate/keysrc"),
        }

    @staticmethod
    def _insert_dict(v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is None:
            return None
        sel = v.get("insert/sel")
        slot, side = _insert_side(sel)
        return {"on": v.get("insert/on"), "pos": v.get("insert/pos"), "sel": sel, "fx_slot": slot, "side": side}

    # -- write core ---------------------------------------------------------------------------------

    async def ensure_pre_write_snapshot(self) -> Snapshot | None:
        """Before the session's first Tier ≥ 1 write, dump the desk to ``snapshots`` with the label
        ``auto-pre-write`` (BRIEF §4). Idempotent; returns the snapshot only when it was taken now."""
        if not self._policy.snapshot_before_write:
            return None
        async with self._snapshot_lock:
            if not self._policy.snapshot_before_write:
                return None
            state = await self.dump()
            if state.missing:
                log.warning("pre-write snapshot: %d node(s) missing", len(state.missing))
            snap = self._snapshots.save(state, label="auto-pre-write")
            self._policy.mark_snapshot_taken()
            self.pre_write_snapshot = snap
            self._events.publish("desk.snapshot", id=snap.id, label=snap.label, sections=len(state.sections), missing=len(state.missing))
            log.info("pre-write snapshot %s taken", snap.id)
            return snap

    def _guarded_msg(self, address: str, target: Target | None) -> str:
        """Refusal text that names the tool to use — a raw OSC address is not a tool argument."""
        if target is not None and target.family == "main" and address.endswith("/mix/on"):
            return f"{target.label} mute is a guarded (Tier 2) parameter; use set_main_mute"
        who = f" ({target.label})" if target is not None else ""
        return f"{address}{who} is a guarded (Tier 2) parameter; use the confirming tool for it"

    async def _write(self, address: str, raw: Any, *, value: Any, tool: str, target: Target | None = None, guarded: bool = False) -> None:
        """policy.tier_for → snapshot-before-write → rate limiter → ``conn.set`` → cache + event."""
        tier = self._policy.tier_for(address)
        if tier >= Tier.GUARDED and not guarded:
            raise DeskError("GUARDED", self._guarded_msg(address, target), address=address)
        await self.ensure_pre_write_snapshot()
        await self._policy.acquire_write()
        try:
            await self._conn.set(address, raw)
        except NotConnected as e:
            raise DeskError("NOT_CONNECTED", str(e)) from None
        self.write_count += 1
        self._invalidate_address(address)
        self._events.publish("desk.write", address=address, value=_jsonable(value), tier=int(tier), tool=tool, target=target.key if target else None)

    async def _write_value(self, t: Target, rel: str, value: Any, *, tool: str, guarded: bool = False, **vars: Any) -> tuple[str, Any]:
        """Engineering ``value`` → raw via the spec → :meth:`_write`; returns (address, applied value)."""
        spec, addr = self._param(t, rel, **vars)
        try:
            raw = spec.to_raw(value)
            applied = spec.to_value(raw)
        except ScaleError as e:
            raise DeskError("BAD_ARGUMENT", f"{t.label} {rel}: {e}") from None
        await self._write(addr, raw, value=applied, tool=tool, target=t, guarded=guarded)
        return addr, applied

    async def _ramp(self, address: str, steps: Sequence[float], spec: ParamSpec, *, tool: str, target: Target, guarded: bool) -> tuple[float | None, bool]:
        """Write ``steps`` (dB) one per ``policy.ramp_step_s`` as a task keyed by address; a newer
        ramp on the same address cancels this one. Returns (last value written, superseded)."""
        prev = self._ramps.pop(address, None)
        if prev is not None and not prev.done():
            prev.cancel()
            await asyncio.wait({prev})
        holder: dict[str, float | None] = {"last": None}
        task = asyncio.create_task(self._run_ramp(address, steps, spec, holder, tool, target, guarded), name=f"ramp {address}")
        self._ramps[address] = task
        try:
            await asyncio.wait({task})
        except asyncio.CancelledError:
            task.cancel()
            raise
        finally:
            if self._ramps.get(address) is task:
                del self._ramps[address]
        if task.cancelled():
            return holder["last"], True
        exc = task.exception()
        if exc is not None:
            raise exc
        return holder["last"], False

    async def _run_ramp(self, address: str, steps: Sequence[float], spec: ParamSpec, holder: dict, tool: str, target: Target, guarded: bool) -> None:
        """Walk ``steps`` on the policy's step clock, writing only when the desk's *quantised*
        value actually changes.

        A ramp emits one step per 20 ms = 50 writes/s, which is exactly the global write budget,
        so on real hardware the rate limiter stretched a 12 s ramp to 19.3 s and a ramp crowded
        out every other write (a CFS² notch included). Most of those writes were no-ops anyway:
        the fader grid is 1024 values, so a 5 dB move has ~50 distinct raw values however many
        steps we slice it into. Skipping unchanged raw values keeps the timing honest and the
        motion identical. The final step is always written so the ramp lands exactly on target.
        """
        last = len(steps) - 1
        t0 = time.perf_counter()
        period = self._policy.ramp_step_s
        sent: Any = _UNSET
        for i, db in enumerate(steps):
            raw = spec.to_raw(db)
            if i == last or raw != sent:
                await self._write(address, raw, value=db, tool=tool, target=target, guarded=guarded)
                sent = raw
                holder["last"] = db
            if i < last:
                # Sleep to the step's wall-clock slot rather than a fixed period, so skipped
                # writes do not make the ramp finish early.
                await asyncio.sleep(max(0.0, t0 + (i + 1) * period - time.perf_counter()))

    async def _move_level(
        self, t: Target, spec: ParamSpec, address: str, before: Any, db: float, *,
        ramp_ms: int | None, force: bool, policy_kind: str, tool: str, guarded: bool = False,
    ) -> dict[str, Any]:
        tier = self._policy.tier_for(address)
        if tier >= Tier.GUARDED and not guarded:
            raise DeskError("GUARDED", f"{t.label} level is a guarded (Tier 2) parameter; use set_main_fader", address=address)
        after, clamped = self._policy.clamp_level(t, db, kind=policy_kind)  # type: ignore[arg-type]
        if after <= FADER_FLOOR_DB:
            after = NEG_INF_DB  # −90 dB is the bottom stop = -oo on the desk (scales_params.md §2.3)
        before_db = before if isinstance(before, (int, float)) and not isinstance(before, bool) else None
        if before_db is not None and math.isfinite(before_db) and math.isfinite(after):
            self._policy.check_relative(after - before_db, force=force)
        ms = self._policy.ramp_default_ms if ramp_ms is None else _int(ramp_ms, "ramp_ms", 0, 60_000)
        await self.ensure_pre_write_snapshot()
        steps = self._policy.ramp_steps(before_db, after, ms)
        t0 = time.perf_counter()
        last, superseded = await self._ramp(address, steps, spec, tool=tool, target=t, guarded=guarded)
        written = after if not superseded else (last if last is not None else before_db)
        return {
            "target": t.key, "label": t.label, "address": address, "kind": policy_kind,
            "before_db": _db1(before_db), "before": _db_text(before_db),
            "after_db": _db1(written), "after": _db_text(written),
            "requested_db": _db1(db), "clamped": clamped.to_dict() if clamped else None,
            "ramp_ms": ms if len(steps) > 1 else 0, "steps": len(steps), "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            "superseded": superseded,
        }

    # -- Tier 1 writes ----------------------------------------------------------------------------------

    async def set_level(
        self, t: Target | str | int, db: float, *, ramp_ms: int | None = None, send_to: int | None = None,
        force: bool = False, kind: Literal["fader", "send", "mlevel"] = "fader",
    ) -> dict[str, Any]:
        """Fader (``kind="fader"``), send level (``send_to`` = destination number) or M/C level
        (``kind="mlevel"``) to ``db`` with a ramp (``ramp_ms`` None → policy default). Clamped to the
        family ceiling (reported), relative limit unless ``force`` (see module doc)."""
        t = self._target(t)
        spec, address, pk = self._level_param(t, kind, send_to)
        before = await self._leaf(address)
        res = await self._move_level(t, spec, address, before, _num(db, "db"), ramp_ms=ramp_ms, force=force, policy_kind=pk, tool="set_send" if pk == "send" and send_to else "set_fader")
        if send_to is not None:
            res["send_to"] = int(send_to)
        return res

    async def adjust_level(
        self, t: Target | str | int, delta_db: float, *, ramp_ms: int | None = None, send_to: int | None = None, force: bool = False,
        kind: Literal["fader", "send", "mlevel"] = "fader",
    ) -> dict[str, Any]:
        """Relative move; ``|delta_db|`` beyond the policy limit needs ``force``. A fader at −∞ is
        taken as −90 dB for the arithmetic (the taper's floor)."""
        t = self._target(t)
        delta = _num(delta_db, "delta_db")
        self._policy.check_relative(delta, force=force)
        spec, address, pk = self._level_param(t, kind, send_to)
        before = await self._leaf(address)
        base = before if isinstance(before, (int, float)) and not isinstance(before, bool) and math.isfinite(before) else FADER_FLOOR_DB
        res = await self._move_level(t, spec, address, before, base + delta, ramp_ms=ramp_ms, force=force, policy_kind=pk, tool="adjust_send" if pk == "send" and send_to else "adjust_fader")
        res["delta_db"] = round(delta, 2)
        if send_to is not None:
            res["send_to"] = int(send_to)
        return res

    async def set_mute(self, t: Target | str | int, muted: bool) -> dict[str, Any]:
        """Mute/unmute a strip. On the wire ``mix/on`` 1 = ON = unmuted (DESIGN §0.4,
        scales_params.md §4.8) — the inversion happens here only."""
        t = self._target(t)
        rel = "on" if t.family == "dca" else "mix/on"
        spec, address = self._param(t, rel)
        was = await self._leaf(address)
        raw = spec.to_raw(not bool(muted))  # muted → 0 (OFF), unmuted → 1 (ON)
        await self._write(address, raw, value={"muted": bool(muted)}, tool="mute" if muted else "unmute", target=t)
        return {"target": t.key, "label": t.label, "muted": bool(muted), "was_muted": (not was) if was is not None else None}

    async def set_send(self, ch: Target | str | int, bus: int, db: float, *, ramp_ms: int | None = None, force: bool = False) -> dict[str, Any]:
        return await self.set_level(ch, db, ramp_ms=ramp_ms, send_to=bus, force=force, kind="send")

    async def adjust_send(self, ch: Target | str | int, bus: int, delta_db: float, *, ramp_ms: int | None = None, force: bool = False) -> dict[str, Any]:
        return await self.adjust_level(ch, delta_db, ramp_ms=ramp_ms, send_to=bus, force=force, kind="send")

    async def set_send_mute(self, ch: Target | str | int, bus: int, muted: bool) -> dict[str, Any]:
        """Send on/off (``mix/NN/on``, inverted like a strip mute)."""
        t = self._target(ch)
        st = self._d.strips[t.family]
        if not st.sends:
            raise DeskError("NOT_SUPPORTED", f"{t.label} has no sends")
        n = _int(bus, "bus", 1, st.sends)
        spec, address = self._param(t, "mix/{send:02d}/on", send=n)
        await self._write(address, spec.to_raw(not bool(muted)), value={"muted": bool(muted)}, tool="set_send_mute", target=t)
        return {"target": t.key, "send_to": n, "muted": bool(muted)}

    async def set_pan(self, t: Target | str | int, pan: int) -> dict[str, Any]:
        """Pan −100 (left) .. +100 (right); the desk keeps 101 values (scales_params.md §3)."""
        t = self._target(t)
        p = _int(pan, "pan", -100, 100)
        _addr, applied = await self._write_value(t, "mix/pan", p, tool="set_pan")
        return {"target": t.key, "label": t.label, "pan": applied}

    async def set_eq_band(
        self, t: Target | str | int, band: int, *, freq_hz: float | None = None, gain_db: float | None = None,
        q: float | None = None, type: str | None = None, on: bool | None = None,
    ) -> dict[str, Any]:
        """Set any of type/freq/gain/Q of EQ band ``band`` (1-based) and/or the EQ on switch. Gain
        is clamped to ±policy.eq_gain_abs_max_db (reported); values come back as the desk's grid."""
        t = self._target(t)
        fam = self._d.strips[t.family]
        if not fam.eq_bands:
            raise DeskError("NOT_SUPPORTED", f"{t.label} has no EQ")
        b = _int(band, "band", 1, fam.eq_bands)
        applied: dict[str, Any] = {}
        clamps: list[dict[str, Any]] = []
        if on is not None:
            _a, applied["on"] = await self._write_value(t, "eq/on", bool(on), tool="set_eq_band")
        if type is not None:
            _a, applied["type"] = await self._write_value(t, "eq/{band}/type", str(type), tool="set_eq_band", band=b)
        if freq_hz is not None:
            _a, applied["freq_hz"] = await self._write_value(t, "eq/{band}/f", _num(freq_hz, "freq_hz"), tool="set_eq_band", band=b)
            applied["freq_hz"] = _r(applied["freq_hz"], 1)
        if gain_db is not None:
            g, cl = self._policy.clamp_eq_gain(_num(gain_db, "gain_db"))
            if cl:
                clamps.append(cl.to_dict())
            _a, applied["gain_db"] = await self._write_value(t, "eq/{band}/g", g, tool="set_eq_band", band=b)
            applied["gain_db"] = _r(applied["gain_db"], 2)
        if q is not None:
            _a, applied["q"] = await self._write_value(t, "eq/{band}/q", _num(q, "q"), tool="set_eq_band", band=b)
            applied["q"] = _r(applied["q"], 2)
        if not applied:
            raise DeskError("BAD_ARGUMENT", "nothing to set: give freq_hz, gain_db, q, type and/or on")
        return {"target": t.key, "label": t.label, "band": b, "applied": applied, "clamped": clamps or None}

    async def _write_params(self, t: Target, tool: str, items: Sequence[tuple[str, str, Any]]) -> dict[str, Any]:
        """``(result key, relpath, engineering value)`` triples → writes; returns ``{key: applied}``."""
        applied: dict[str, Any] = {}
        for key, rel, value in items:
            if value is None:
                continue
            _a, applied[key] = await self._write_value(t, rel, value, tool=tool)
            applied[key] = _r(applied[key], 2)
        if not applied:
            raise DeskError("BAD_ARGUMENT", f"{tool}: nothing to set")
        return applied

    def _ratio_token(self, ratio: Any) -> str:
        """A ratio number (``4`` / ``4.0``) or token (``"4.0"``, ``"10"``) → the ``dyn_ratio`` token."""
        tokens = self._d.enum("dyn_ratio")
        if isinstance(ratio, str):
            s = ratio.strip().replace(":1", "")
            try:
                return tokens[enum_to_index(s, tokens)]
            except ScaleError:
                try:
                    ratio = float(s)
                except ValueError:
                    raise DeskError("BAD_ARGUMENT", f"ratio {ratio!r} is not one of {', '.join(tokens)}") from None
        r = _num(ratio, "ratio")
        best = min(tokens, key=lambda tok: abs(float(tok) - r))
        return best

    async def set_comp(
        self, t: Target | str | int, *, on: bool | None = None, threshold_db: float | None = None, ratio: float | str | None = None,
        attack_ms: float | None = None, release_ms: float | None = None, knee: int | None = None, makeup_db: float | None = None,
        mix_pct: int | None = None,
    ) -> dict[str, Any]:
        """Compressor (``dyn``) settings: threshold −60..0 dB, ratio 1.1..100 (nearest desk token),
        attack 0..120 ms, release 5..4000 ms, knee 0..5, makeup 0..24 dB, mix 0..100 %."""
        t = self._target(t)
        if self._section_path(t, "dyn") is None:
            raise DeskError("NOT_SUPPORTED", f"{t.label} has no compressor")
        items = [
            ("on", "dyn/on", None if on is None else bool(on)),
            ("threshold_db", "dyn/thr", None if threshold_db is None else _num(threshold_db, "threshold_db")),
            ("ratio", "dyn/ratio", None if ratio is None else self._ratio_token(ratio)),
            ("attack_ms", "dyn/attack", None if attack_ms is None else _num(attack_ms, "attack_ms")),
            ("release_ms", "dyn/release", None if release_ms is None else _num(release_ms, "release_ms")),
            ("knee", "dyn/knee", None if knee is None else _num(knee, "knee")),
            ("makeup_db", "dyn/mgain", None if makeup_db is None else _num(makeup_db, "makeup_db")),
            ("mix_pct", "dyn/mix", None if mix_pct is None else _num(mix_pct, "mix_pct")),
        ]
        applied = await self._write_params(t, "set_comp", items)
        return {"target": t.key, "label": t.label, "applied": applied}

    async def set_gate(
        self, t: Target | str | int, *, on: bool | None = None, threshold_db: float | None = None, range_db: float | None = None,
        attack_ms: float | None = None, hold_ms: float | None = None, release_ms: float | None = None,
    ) -> dict[str, Any]:
        """Gate settings (channels only): threshold −80..0 dB, range 3..60 dB, attack 0..120 ms,
        hold 0.02..2000 ms, release 5..4000 ms."""
        t = self._target(t)
        if self._section_path(t, "gate") is None:
            raise DeskError("NOT_SUPPORTED", f"{t.label} has no gate")
        items = [
            ("on", "gate/on", None if on is None else bool(on)),
            ("threshold_db", "gate/thr", None if threshold_db is None else _num(threshold_db, "threshold_db")),
            ("range_db", "gate/range", None if range_db is None else _num(range_db, "range_db")),
            ("attack_ms", "gate/attack", None if attack_ms is None else _num(attack_ms, "attack_ms")),
            ("hold_ms", "gate/hold", None if hold_ms is None else _num(hold_ms, "hold_ms")),
            ("release_ms", "gate/release", None if release_ms is None else _num(release_ms, "release_ms")),
        ]
        applied = await self._write_params(t, "set_gate", items)
        return {"target": t.key, "label": t.label, "applied": applied}

    async def label(self, t: Target | str | int, *, name: str | None = None, color: str | int | None = None, icon: int | None = None) -> dict[str, Any]:
        """Name (≤ 12 characters, longer is truncated and reported), colour token (``RD``, ``CYi`` …
        or its index) and icon number (1..74)."""
        t = self._target(t)
        applied: dict[str, Any] = {}
        truncated = False
        requoted = False
        if name is not None:
            text = str(name)
            if _NAME_BAD in text:
                # transport.md §6.3: node text has no escape for a quote, and the desk's parser
                # (XslashSetString, §6.6) reads to the closing '"' — a name carrying one truncates
                # the rest of the line on the desk. Downgrade it to an apostrophe and say so.
                text, requoted = text.replace(_NAME_BAD, "'"), True
            if len(text) > _NAME_MAX:
                text, truncated = text[:_NAME_MAX], True
            _a, applied["name"] = await self._write_value(t, "config/name", text, tool="label")
        if color is not None:
            _a, applied["color"] = await self._write_value(t, "config/color", color, tool="label")
        if icon is not None:
            _a, applied["icon"] = await self._write_value(t, "config/icon", _int(icon, "icon", 1, 74), tool="label")
        if not applied:
            raise DeskError("BAD_ARGUMENT", "nothing to set: give name, color and/or icon")
        out = {"target": t.key, "label": t.label, "applied": applied}
        if truncated:
            out["truncated"] = True
        if requoted:
            out["quote_replaced"] = True
        return out

    async def panic(self) -> dict[str, Any]:
        """Mute Main LR, Main M/C, every bus and every matrix as fast as possible: fire-and-forget
        ``mix/on 0``, no ramps, no snapshot, rate limiter bypassed (Tier 1, never blocked).
        Silences every POST-tapped output (fx_routing_scenes.md §11 option 1)."""
        t0 = time.perf_counter()
        targets = [t for fam in _PANIC_FAMILIES for t in self._d.strip_targets(fam)]
        await self._policy.acquire_write(panic=True)
        unconfirmed = False
        done: list[str] = []
        failed: list[str] = []
        last: Exception | None = None
        for t in targets:
            address = f"{t.osc_prefix}/mix/on"
            # One bad address must never cost the other 23 outputs (BRIEF §4: panic is never
            # blocked) — the reconnect loop can drop the socket mid-loop, so every send is on its own.
            try:
                await self._conn.set(address, 0)  # 0 = OFF = muted (inversion, DESIGN §0.4)
            except (NotConnected, OSError) as e:
                try:
                    await self._conn.send_raw(address, 0)  # try anyway: the desk may just be slow
                    unconfirmed = True
                except (NotConnected, OSError) as e2:
                    last = e2 if isinstance(e2, Exception) else e
                    failed.append(t.key)
                    continue
            self._invalidate_address(address)
            done.append(t.key)
        elapsed = (time.perf_counter() - t0) * 1000.0
        self.write_count += len(done)
        delivered = "unconfirmed" if unconfirmed else "sent"
        self._events.publish("desk.panic", elapsed_ms=round(elapsed, 1), count=len(done), failed=len(failed), delivered=delivered)
        log.warning("PANIC: %d outputs muted in %.1f ms%s%s", len(done), elapsed,
                    " (desk degraded, unconfirmed)" if unconfirmed else "",
                    f", {len(failed)} NOT SENT: {', '.join(failed)}" if failed else "")
        if not done:
            raise DeskError("NOT_CONNECTED", f"panic could not reach the desk: {last}", muted=done, failed=failed)
        return {"muted": done, "count": len(done), "failed": failed, "elapsed_ms": round(elapsed, 1), "delivered": delivered}

    # -- Tier 2 executors (the server does the confirmation dance) ---------------------------------------

    async def set_main_level(self, which: str = "st", db: float = -90.0, *, ramp_ms: int | None = None) -> dict[str, Any]:
        """Main LR (``"st"``) or M/C (``"m"``) fader; clamped to the main ceiling; no relative limit
        (the confirmation token is the gate)."""
        t = self._main_target(which)
        spec, address, pk = self._level_param(t, "fader", None)
        before = await self._leaf(address)
        return await self._move_level(t, spec, address, before, _num(db, "db"), ramp_ms=ramp_ms, force=True, policy_kind=pk, tool="set_main_fader", guarded=True)

    async def set_main_mute(self, which: str, muted: bool) -> dict[str, Any]:
        t = self._main_target(which)
        spec, address = self._param(t, "mix/on")
        was = await self._leaf(address)
        await self._write(address, spec.to_raw(not bool(muted)), value={"muted": bool(muted)}, tool="set_main_mute", target=t, guarded=True)
        return {"target": t.key, "label": t.label, "muted": bool(muted), "was_muted": (not was) if was is not None else None}

    def _main_target(self, which: Any) -> Target:
        if isinstance(which, Target):
            if which.family != "main":
                raise DeskError("BAD_ARGUMENT", f"{which.key} is not a main target")
            return which
        w = str(which).strip().lower()
        if w in ("st", "lr", "stereo", "main", ""):
            return Target("main", "st")
        if w in ("m", "mono", "mc", "m/c", "c"):
            return Target("main", "m")
        raise DeskError("BAD_ARGUMENT", f"which must be 'st' (Main LR) or 'm' (Main M/C), got {which!r}")

    async def recall_scene(self, index: int) -> dict[str, Any]:
        """``/-action/goscene ,i index`` (fx_routing_scenes.md §6.4), then verify by reading
        ``/-show/prepos/current`` back (its reply is UNCONFIRMED). Drops the whole cache."""
        self._policy.check_show_mode_allows("scene_recall")
        idx = _int(index, "scene index", 0, 99)
        info = await self._scene_info(idx)
        sc = await self._show_control()
        # The pointer only tracks scenes while show_control is SCENES (fx_routing_scenes.md §6.2);
        # otherwise it indexes the cue/snippet list, so polling it would just burn 2 s and lie.
        indexes_scenes = sc is None or sc == "SCENES"
        previous = await self._leaf("/-show/prepos/current") if indexes_scenes else None
        await self._write("/-action/goscene", idx, value=idx, tool="recall_scene", guarded=True)
        verified: bool | None = None if not indexes_scenes else False
        if indexes_scenes:
            deadline = time.monotonic() + _SCENE_VERIFY_S
            while True:
                try:
                    cur = await self._conn.get("/-show/prepos/current")
                except RequestTimeout:
                    cur = None
                except NotConnected as e:
                    raise DeskError("NOT_CONNECTED", str(e)) from None
                if cur == idx:
                    verified = True
                    break
                if time.monotonic() >= deadline:
                    break
                await asyncio.sleep(_SCENE_POLL_S)
        self.invalidate()
        self._events.publish("desk.scene", action="recall", index=idx, name=info["name"], verified=verified)
        log.info("scene %d %r recalled (%s)", idx, info["name"], "verified" if verified else "UNVERIFIED")
        out: dict[str, Any] = {
            "index": idx, "name": info["name"], "has_data": info["has_data"],
            "verified": verified, "previous_index": previous, "show_control": sc,
        }
        if not indexes_scenes:
            out["note"] = "recall sent, but it cannot be verified: " + self._not_scenes(str(sc))
        return out

    async def save_scene(self, index: int, name: str, notes: str = "") -> dict[str, Any]:
        """``/save ,siss scene index name notes`` → expects ``,si scene 1`` (fx_routing_scenes.md §6.4)."""
        self._policy.check_show_mode_allows("scene_save")
        idx = _int(index, "scene index", 0, 99)
        text = str(name)
        await self.ensure_pre_write_snapshot()
        await self._policy.acquire_write()
        try:
            reply = await self._conn.request("/save", "scene", idx, text, str(notes))
        except RequestTimeout as e:
            raise DeskError("TIMEOUT", f"no reply to /save: {e}") from None
        except NotConnected as e:
            raise DeskError("NOT_CONNECTED", str(e)) from None
        self.write_count += 1
        self._drop(f"/-show/showfile/scene/{idx:03d}")
        ok = len(reply.args) >= 2 and reply.args[0] == "scene" and reply.args[1] == 1
        self._events.publish("desk.scene", action="save", index=idx, name=text, ok=ok)
        if not ok:
            raise DeskError("SCENE_FAILED", f"desk refused to save scene {idx} ({reply.args!r})", index=idx)
        log.info("scene %d saved as %r", idx, text)
        return {"index": idx, "name": text, "notes": str(notes), "ok": True}

    async def restore(self, snap: Snapshot, *, scope: str | None = None) -> dict[str, Any]:
        """Bring the desk back to ``snap`` (optionally only ``scope``: a target key, family or path):
        :func:`nodes.restore_plan` lines written with ``conn.slash`` (transport.md §6.6), one per
        rate-limiter slot, each awaiting the desk's echo. Timed-out lines are listed in ``failed``."""
        await self.ensure_pre_write_snapshot()
        t0 = time.perf_counter()
        live = await self.dump(sections=[scope] if scope else None)
        lines = restore_plan(snap.state, live, self._d, scope=scope)
        written = 0
        failed: list[str] = []
        aborted: str | None = None
        for line in lines:
            await self._policy.acquire_write()
            try:
                await self._conn.slash(line)
                written += 1
            except RequestTimeout:
                failed.append(line)
            except NotConnected as e:
                aborted = str(e)
                break
        self.write_count += written
        self.invalidate()
        elapsed = round((time.perf_counter() - t0) * 1000.0, 1)
        self._events.publish("desk.restore", snapshot=snap.id, lines=len(lines), written=written, failed=len(failed), scope=scope)
        log.info("restore %s: %d/%d section(s) written in %.0f ms%s", snap.id, written, len(lines), elapsed, f" ({len(failed)} failed)" if failed else "")
        out = {"snapshot": snap.id, "label": snap.label, "scope": scope, "lines": len(lines), "written": written, "failed": failed, "duration_ms": elapsed}
        if aborted:
            out["aborted"] = aborted
        return out

    async def set_source(self, t: Target | str | int, source: str) -> dict[str, Any]:
        """Input source token (``IN05``, ``AUX1``, ``USBL``, ``FX1L``, ``BUS03``, ``OFF``; enum
        ``ch_source``) of a channel or aux-in (guarded: ``/*/config/source``)."""
        t = self._target(t)
        if not isinstance(source, str):
            raise DeskError("BAD_ARGUMENT", f"source must be a token such as 'IN05', got {source!r}")
        _a, applied = await self._write_value(t, "config/source", source.strip(), tool="set_source", guarded=True)
        return {"target": t.key, "label": t.label, "source": applied}

    async def set_phantom(self, headamp: int | Target | str, on: bool) -> dict[str, Any]:
        """``/headamp/NNN/phantom`` (guarded). ``headamp`` is an index 0..127 (000–031 local XLR,
        032–079 AES50-A, 080–127 AES50-B; scales_params.md §12.2) or a channel/aux-in target whose
        head amp is derived from its source and routing (:meth:`headamp_index_for`)."""
        target: Target | None = None
        if isinstance(headamp, bool):
            raise DeskError("BAD_ARGUMENT", "headamp must be an index or a target")
        if isinstance(headamp, int):
            idx = _int(headamp, "headamp", 0, 127)
        else:
            target = self._target(headamp)
            found = await self.headamp_index_for(target)
            if found is None:
                raise DeskError("NOT_SUPPORTED", f"{target.label} has no head amp behind its source (USB/card/FX/bus source or unknown routing)")
            idx = found
        spec = self._d.param("headamp", "phantom")
        address = spec.address(n=idx)
        await self._write(address, spec.to_raw(bool(on)), value={"phantom": bool(on)}, tool="set_phantom", target=target, guarded=True)
        return {"headamp": idx, "address": address, "on": bool(on), "target": target.key if target else None}

    async def set_insert(self, t: Target | str | int, *, sel: str | None = None, on: bool | None = None, pos: str | None = None) -> dict[str, Any]:
        """Insert point (``OFF``, ``FX1L`` … ``FX8R``, ``AUX1``–``AUX6``), on switch and position
        (``PRE``/``POST``) of a channel/bus/matrix/main strip (guarded ``/*/insert/*``)."""
        t = self._target(t)
        if self._section_path(t, "insert") is None:
            raise DeskError("NOT_SUPPORTED", f"{t.label} has no insert")
        applied: dict[str, Any] = {}
        if sel is not None:
            _a, applied["sel"] = await self._write_value(t, "insert/sel", str(sel).strip(), tool="set_insert", guarded=True)
        if pos is not None:
            _a, applied["pos"] = await self._write_value(t, "insert/pos", str(pos).strip(), tool="set_insert", guarded=True)
        if on is not None:
            _a, applied["on"] = await self._write_value(t, "insert/on", bool(on), tool="set_insert", guarded=True)
        if not applied:
            raise DeskError("BAD_ARGUMENT", "nothing to set: give sel, on and/or pos")
        return {"target": t.key, "label": t.label, "applied": applied}

    async def set_fx_type(self, slot: int, fx_type: str) -> dict[str, Any]:
        """Load an effect into slot 1..8 (guarded). Slots 5–8 use the ``fx_type_58`` enum (GEQ2 = 0),
        slots 1–4 ``fx_type_14`` (GEQ2 = 27) — fx_routing_scenes.md §1.2/§1.3."""
        n = _int(slot, "fx slot", 1, 8)
        tokens = self._d.enum("fx_type_14" if n <= 4 else "fx_type_58")
        try:
            i = enum_to_index(str(fx_type).strip(), tokens)
        except ScaleError:
            raise DeskError("BAD_ARGUMENT", f"{fx_type!r} is not an effect available in slot {n}; choose from {', '.join(tokens)}") from None
        address = self._d.param("fx", "type").address(n=n)
        await self._write(address, i, value=tokens[i], tool="set_fx_type", guarded=True)
        self._drop(f"/fx/{n}/par")  # the par semantics change with the effect
        return {"slot": n, "type": tokens[i], "index": i}

    async def set_geq_band(self, fx_slot: int, side: str, band: int, gain_db: float) -> None:
        """Raw GEQ write: ``gain_db`` (−15..+15, 0.5 dB grid) to 1-based ``band`` (1..31; 32 = the
        master) of ``side`` ``"A"``/``"B"`` (``"L"``/``"R"`` accepted) of the GEQ/TEQ in ``fx_slot``.
        Stereo types (GEQ/TEQ) use the same pars for both sides. Not a notch policy check — CFS²
        validates with ``policy.validate_notch`` before calling this."""
        n = _int(fx_slot, "fx slot", 1, 8)
        b = _int(band, "band", 1, 32)
        s = str(side).strip().upper()
        if s in ("L", "A", "1"):
            s = "A"
        elif s in ("R", "B", "2"):
            s = "B"
        else:
            raise DeskError("BAD_ARGUMENT", f"side must be 'A' or 'B', got {side!r}")
        fx_type = (await self._section(f"/fx/{n}")).get("type")
        if fx_type in self._geq_dual:
            first = self._geq_layout["par_b_first" if s == "B" else "par_a_first"]
            master = self._geq_layout["par_b_master" if s == "B" else "par_a_master"]
        elif fx_type in self._geq_stereo:
            first, master = self._geq_layout["par_a_first"], self._geq_layout["par_a_master"]
        else:
            raise DeskError("NOT_A_GEQ", f"FX slot {n} holds {fx_type!r}, not a GEQ/TEQ", slot=n, type=fx_type)
        par = master if b == 32 else first + b - 1
        try:
            raw = self._geq_scale.to_raw(_num(gain_db, "gain_db"))  # (dB + 15) / 30, fx_routing_scenes.md §2.2
        except ScaleError as e:
            raise DeskError("BAD_ARGUMENT", str(e)) from None
        address = self._d.param("fx", f"par/{par:02d}").address(n=n)
        await self._write(address, raw, value={"gain_db": self._geq_scale.to_value(raw), "band": b, "side": s}, tool="set_geq_band")
