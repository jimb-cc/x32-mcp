"""Desk facade: typed reads and policy-enforced writes in engineering units (DESIGN.md §13).

Everything the MCP tools (and CFS²) need from the console goes through one :class:`Desk`. Reads
assemble strips from ``/node`` sections (transport.md §6) through a small TTL cache; writes go
``policy.tier_for → policy checks/clamps → ensure_pre_write_snapshot → policy.acquire_write →
conn.set``; mute inversion (``/…/mix/on`` 1 = ON = unmuted, scales_params.md §4.8) lives here and
nowhere above. All public results are plain dicts of dB / Hz / ms / % / 1-based numbers / enum
tokens — never raw OSC floats.

Decisions where DESIGN.md is silent (or where verified research overrides it):

* **Levels in results.** ``*_db`` fields are floats rounded to 0.1 dB; a fully-down level is
  ``-inf`` (the server renders it ``"-oo"``) and ``None`` means *not read* — the two are never
  conflated. A sibling text field (``fader``, ``level``, ``before``, ``after``) always carries the
  display form (``-oo``, ``-12.3``, ``+2.0``). Frequencies are rounded to 0.1 Hz, gains/Q/ms to 0.01.
* **Node cache.** Parsed ``/node`` sections are cached per node path for
  ``policy.read_cache_ttl_s``; a pushed ``/xremote`` update or one of our own writes drops the
  section that owns the address; a scene recall, a restore or ``invalidate()`` drops everything.
  Concurrent misses on one path share a request. ``dump()`` primes the cache from the sweep.
* **Relative limit.** Only ``adjust_level`` applies ``policy.check_relative`` (±6 dB, ±3 in show
  mode, ``force`` to override) — that is where a typo or a runaway loop does damage. ``set_level``
  is absolute: it is bounded by the family ceiling and ramped, so a big deliberate move (a send
  from off to −12 dB) goes through without ``force``.
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
* **Output taps** (fx_routing_scenes.md §4.7): ``get_outputs``/``get_output`` decode
  ``/outputs/main|aux/NN`` (source token, tap point, polarity, the strip the source taps and
  whether the tap follows its mute) with the ``/config/routing/OUT`` and ``AES50A`` blocks that
  carry them; ``set_output`` writes ``src``/``pos`` (guarded — the confirming tools are
  ``set_output``/``set_aux_output``) and ``invert`` after normalising every argument
  (:func:`normalise_output_source` accepts ``'bus 3'``, ``'main L'``, ``'ch 5'`` …).
* **Routing / links / solo** (``get_routing``, ``set_bus_link``, ``set_input_block``, ``set_user_in``,
  ``set_solo_mode``): the guarded ``/config/buslink``, ``/config/routing/IN`` and ``/config/userrout/in``
  writes are Tier-2 executors like ``set_source``; the three PFL/AFL solo modes are Tier 1. The User-In
  numbering (0 OFF, 1..32 local XLR, 33..80 AES50-A, 81..128 AES50-B, 129..160 card, 161..166 aux in,
  167/168 talkback — fx_routing_scenes.md §4.8) lives in the module-level ``user_in_*`` helpers.

Errors are :class:`DeskError` (``code`` + ``to_dict()`` for the tool envelope) — ``NOT_CONNECTED``,
``TIMEOUT``, ``BAD_ARGUMENT``, ``NOT_SUPPORTED``, ``GUARDED``, ``UNKNOWN_TARGET``, ``AMBIGUOUS_NAME``,
``NOT_A_GEQ``, ``SCENE_FAILED`` — or a :class:`~x32mcp.policy.PolicyError` passed through unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import math
import re
import time
from typing import Any, Iterable, Iterator, Literal, Mapping, Sequence

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
from .settle import read_until
from .targets import Target, TargetError, parse_target

__all__ = [
    "priority_writes", "DeskError", "Desk", "ALL_FAMILIES", "normalise_output_source", "normalise_output_pos", "output_source_target",
    "IN_BLOCKS", "USER_IN_MAX", "user_in_source", "user_in_token", "user_in_number", "in_block", "bus_link_pair", "routing_token"]

log = logging.getLogger(__name__)

# Writes issued inside ``priority_writes()`` take the rate limiter's emergency lane (as panic() does).
# Reserved for *protective, level-lowering* writes that must never wait behind (or be refused because
# of) somebody else's burst: CFS²'s back-off after an abort, its retreat when the operator intervenes.
# A context variable rather than a parameter so it reaches the write through set_level → ramp task
# (asyncio copies the context into the task) without every signature growing a flag.
_PRIORITY_WRITE: contextvars.ContextVar[bool] = contextvars.ContextVar("x32mcp_priority_write", default=False)


@contextlib.contextmanager
def priority_writes(on: bool = True) -> Iterator[None]:
    token = _PRIORITY_WRITE.set(bool(on) or _PRIORITY_WRITE.get())
    try:
        yield
    finally:
        _PRIORITY_WRITE.reset(token)


ALL_FAMILIES: tuple[str, ...] = ("ch", "auxin", "fxrtn", "bus", "mtx", "main", "dca")
_PANIC_FAMILIES: tuple[str, ...] = ("main", "bus", "mtx")  # fx_routing_scenes.md §11 option 1
_PANIC_VERIFY_S = 0.6    # after the sends: read the mutes back until they all show muted, at most this long
_PANIC_REVERIFY_S = 0.3  # after re-sending the ones that did not
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


_SEND_TAP_ALIASES: dict[str, str] = {
    # scales_params.md §4.9: 0 input/low-cut, 1 pre-EQ, 2 post-EQ (pre-fader), 3 pre-fader, 4 post-fader, 5 subgroup
    "in": "IN/LC", "in/lc": "IN/LC", "input": "IN/LC", "lc": "IN/LC", "lowcut": "IN/LC", "low-cut": "IN/LC",
    "pre-eq": "<-EQ", "pre eq": "<-EQ", "pre_eq": "<-EQ", "preeq": "<-EQ",
    "post-eq": "EQ->", "post eq": "EQ->", "post_eq": "EQ->", "posteq": "EQ->",
    "pre": "PRE", "pre-fader": "PRE", "pre fader": "PRE", "pre_fader": "PRE", "prefader": "PRE",
    "post": "POST", "post-fader": "POST", "post fader": "POST", "post_fader": "POST", "postfader": "POST",
    "grp": "GRP", "group": "GRP", "sub": "GRP", "subgroup": "GRP",
}
_SEND_TAP_HINT = "in, pre-eq, post-eq, pre, post or grp"


def send_pair(send: Any, count: int = 16) -> tuple[int, int]:
    """The odd/even pair a send number belongs to: the X32 keeps ONE tap point (``mix/NN/type``) and
    one pan per pair, on the odd send (scales_params.md §4.9), so 3 → (3, 4) and 4 → (3, 4).
    ``send`` must be an integer 1..``count`` (BAD_ARGUMENT otherwise)."""
    n = _int(send, "bus", 1, count)
    lo = n if n % 2 else n - 1
    return lo, min(lo + 1, count)


def normalise_send_tap(tap: Any, tokens: Sequence[str]) -> str:
    """A tap point in any accepted spelling → the ``send_type`` token: the tokens themselves
    (``IN/LC``, ``<-EQ``, ``EQ->``, ``PRE``, ``POST``, ``GRP``; any case) or the aliases ``in``,
    ``pre-eq``, ``post-eq``, ``pre``, ``post``, ``grp`` (``pre-fader``, ``subgroup`` … too). ``tokens`` is
    the enum of the send being written — bus → matrix sends have no GRP — and a token outside it is
    BAD_ARGUMENT, as is anything unrecognised."""
    if isinstance(tap, bool) or not isinstance(tap, str):
        raise DeskError("BAD_ARGUMENT", f"tap must be one of {', '.join(tokens)} ({_SEND_TAP_HINT}), got {tap!r}")
    text = tap.strip()
    token = _SEND_TAP_ALIASES.get(" ".join(text.lower().split()))
    if token is None:
        try:
            token = tokens[enum_to_index(text, tokens)]
        except ScaleError:
            raise DeskError("BAD_ARGUMENT", f"unknown tap {tap!r}; expected one of {', '.join(tokens)} ({_SEND_TAP_HINT})") from None
    if token not in tokens:
        raise DeskError("BAD_ARGUMENT", f"tap {token} is not available on this send; expected one of {', '.join(tokens)}")
    return token


# -- output taps (fx_routing_scenes.md §4.7) ------------------------------------------------------------

# group -> (param relpath template of the tap, its template variable, human label). The tap count comes
# from the descriptor's `for` ranges (main 1..16, aux 1..6), not from here.
_OUTPUT_GROUPS: dict[str, tuple[str, str, str]] = {
    "main": ("main/{n:02d}", "n", "OUT"),
    "aux": ("aux/{idx:02d}", "idx", "AUX OUT"),
}
# tap points that go quiet with the source strip's mute (§4.7: +M variants and POST; the others ignore it)
_OUTPUT_POS_FOLLOWS_MUTE = frozenset({"IN/LC+M", "<-EQ+M", "EQ->+M", "PRE+M", "POST"})
_OUTPUT_SRC_ALIASES: dict[str, str] = {
    "NONE": "OFF", "-": "OFF",
    "L": "Main L", "LEFT": "Main L", "MAIN LEFT": "Main L", "MAINL": "Main L",
    "R": "Main R", "RIGHT": "Main R", "MAIN RIGHT": "Main R", "MAINR": "Main R",
    "MC": "M/C", "M-C": "M/C", "MONO": "M/C", "CENTRE": "M/C", "CENTER": "M/C", "MAIN M": "M/C", "MAIN MC": "M/C",
    "MAIN M/C": "M/C", "MAIN MONO": "M/C", "MAIN C": "M/C",
    "MON L": "Monitor L", "MONITOR LEFT": "Monitor L", "MON R": "Monitor R", "MONITOR RIGHT": "Monitor R",
    "TB": "Talkback", "TALK": "Talkback", "TALK BACK": "Talkback",
}
_OUTPUT_SRC_RE = re.compile(
    r"^(?:DIRECT\s*OUT\s*|DIRECT\s*)?(MIXBUS|MIX|BUS|MATRIX|MTX|CHANNEL|CH|AUXIN|AUX|FX|MONITOR|MON|MAIN)"
    r"\s*[-./]?\s*(\d{1,2})?\s*([LRM])?$"
)
_OUTPUT_POS_ALIASES: dict[str, str] = {
    "IN/LC": "IN/LC", "IN": "IN/LC", "INLC": "IN/LC", "IN LC": "IN/LC", "IN-LC": "IN/LC", "INPUT": "IN/LC", "LC": "IN/LC",
    "LOWCUT": "IN/LC", "LOW CUT": "IN/LC",
    "<-EQ": "<-EQ", "<EQ": "<-EQ", "PREEQ": "<-EQ", "PRE EQ": "<-EQ", "PRE-EQ": "<-EQ", "PRE/EQ": "<-EQ", "BEFORE EQ": "<-EQ",
    "EQ->": "EQ->", "EQ>": "EQ->", "POSTEQ": "EQ->", "POST EQ": "EQ->", "POST-EQ": "EQ->", "POST/EQ": "EQ->", "AFTER EQ": "EQ->",
    "PRE": "PRE", "PREFADER": "PRE", "PRE FADER": "PRE", "PRE-FADER": "PRE", "PRE FDR": "PRE", "PREFDR": "PRE",
    "POST": "POST", "POSTFADER": "POST", "POST FADER": "POST", "POST-FADER": "POST", "POST FDR": "POST", "POSTFDR": "POST",
}
_OUTPUT_POS_MUTE_RE = re.compile(r"^(.*?)\s*(?:\+|\s)\s*(?:M|MUTE|MUTED)$")
_OUTPUT_SRC_HELP = ("OFF, Main L, Main R, M/C, MixBus 01..16 ('bus 3'), Matrix 1..6 ('mtx 2'), DirectOut Ch 01..32 ('ch 5'), "
                    "DirectOut Aux 1..8 ('aux 2'), DirectOut FX 1L..4R ('fx 1L'), Monitor L/R, Talkback, or the desk's index 0..76")


def normalise_output_source(value: Any, tokens: Sequence[str]) -> str:
    """Any accepted spelling of an output-tap source → its ``output_src`` token (``tokens`` is that
    enum): the token itself in any case (``'mixbus 03'``, ``'main l'``), the desk's int index 0..76,
    or a short form — ``'bus 3'``/``'mix 3'`` → ``MixBus 03``, ``'mtx 2'`` → ``Matrix 2``, ``'L'``/``'R'``/
    ``'main L'`` → ``Main L``/``Main R``, ``'M/C'``/``'mono'`` → ``M/C``, ``'ch 5'``/``'direct out ch 5'`` →
    ``DirectOut Ch 05`` (``'in 5'`` is deliberately NOT accepted: that is the input-patch vocabulary), ``'aux 2'`` → ``DirectOut Aux 2``, ``'fx 1L'`` → ``DirectOut FX 1L``, ``'mon L'`` →
    ``Monitor L``, ``'tb'`` → ``Talkback``, ``'off'``/``'none'`` → ``OFF``. ``BAD_ARGUMENT`` otherwise."""
    if isinstance(value, bool) or value is None:
        raise DeskError("BAD_ARGUMENT", f"source must be a token such as 'MixBus 03', got {value!r}")
    if isinstance(value, int) or (isinstance(value, str) and value.strip().isdigit()):
        idx = int(value)
        if 0 <= idx < len(tokens):
            return tokens[idx]
        raise DeskError("BAD_ARGUMENT", f"source index {idx} is out of range 0..{len(tokens) - 1}")
    if not isinstance(value, str):
        raise DeskError("BAD_ARGUMENT", f"source must be a token such as 'MixBus 03', got {value!r}")
    text = " ".join(value.split()).upper()
    by_upper = {t.upper(): t for t in tokens}
    if text in by_upper:
        return by_upper[text]
    candidate = _OUTPUT_SRC_ALIASES.get(text)
    if candidate is None:
        m = _OUTPUT_SRC_RE.match(text)
        if m:
            fam, num, side = m.group(1), m.group(2), m.group(3)
            n = int(num) if num else None
            if fam in ("MIXBUS", "MIX", "BUS") and n is not None and not side:
                candidate = f"MixBus {n:02d}"
            elif fam in ("MATRIX", "MTX") and n is not None and not side:
                candidate = f"Matrix {n}"
            elif fam in ("CHANNEL", "CH") and n is not None and not side:   # not "IN n": that is the input vocabulary, not a direct out
                candidate = f"DirectOut Ch {n:02d}"
            elif fam in ("AUXIN", "AUX") and n is not None and not side:
                candidate = f"DirectOut Aux {n}"
            elif fam == "FX" and n is not None and side in ("L", "R"):
                candidate = f"DirectOut FX {n}{side}"
            elif fam in ("MONITOR", "MON") and n is None and side in ("L", "R"):
                candidate = f"Monitor {side}"
            elif fam == "MAIN" and n is None and side:
                candidate = "M/C" if side == "M" else f"Main {side}"
    if candidate is None or candidate not in tokens:
        raise DeskError("BAD_ARGUMENT", f"unknown output source {value!r}; use {_OUTPUT_SRC_HELP}")
    return candidate


def normalise_output_pos(value: Any, tokens: Sequence[str]) -> str:
    """Tap-point spelling → ``output_pos`` token: the token in any case, the index 0..8, or a
    friendly form (``'pre'``, ``'pre fader'``, ``'pre eq'``, ``'post eq'``, ``'in'``/``'input'``,
    ``'post'``) with an optional ``+M``/``' mute'`` suffix for the mute-following variant
    (``'pre+m'``, ``'pre eq mute'``). There is no ``POST+M``: POST already follows the mute."""
    if isinstance(value, bool) or value is None:
        raise DeskError("BAD_ARGUMENT", f"pos must be a tap point such as 'POST' or 'PRE+M', got {value!r}")
    if isinstance(value, int) or (isinstance(value, str) and value.strip().isdigit()):
        idx = int(value)
        if 0 <= idx < len(tokens):
            return tokens[idx]
        raise DeskError("BAD_ARGUMENT", f"pos index {idx} is out of range 0..{len(tokens) - 1}")
    if not isinstance(value, str):
        raise DeskError("BAD_ARGUMENT", f"pos must be a tap point such as 'POST' or 'PRE+M', got {value!r}")
    text = " ".join(value.split()).upper()
    by_upper = {t.upper(): t for t in tokens}
    if text in by_upper:
        return by_upper[text]
    mute = False
    m = _OUTPUT_POS_MUTE_RE.match(text)
    if m and m.group(1):
        text, mute = m.group(1).strip(), True
    base = _OUTPUT_POS_ALIASES.get(text)
    if base is None:
        raise DeskError("BAD_ARGUMENT", f"unknown tap point {value!r}; use one of {', '.join(tokens)} "
                        "(IN/LC = after the low cut, <-EQ = pre-EQ, EQ-> = post-EQ, PRE = pre-fader, POST = post-fader; "
                        "+M = also follows the mute)")
    if mute and base == "POST":
        raise DeskError("BAD_ARGUMENT", "there is no POST+M: a POST tap already follows the source's mute")
    tok = f"{base}+M" if mute else base
    if tok not in tokens:
        raise DeskError("BAD_ARGUMENT", f"unknown tap point {value!r}; use one of {', '.join(tokens)}")
    return tok


def output_source_target(token: Any) -> Target | None:
    """The strip an ``output_src`` token taps (``MixBus 03`` → bus.3, ``DirectOut FX 1R`` → fxrtn.2,
    ``Main L`` → main.st); ``None`` for OFF, the monitor and talkback taps or an unknown token."""
    if not isinstance(token, str):
        return None
    if token in ("Main L", "Main R"):
        return Target("main", "st")
    if token == "M/C":
        return Target("main", "m")
    head, _, rest = token.rpartition(" ")
    fam = {"MixBus": "bus", "Matrix": "mtx", "DirectOut Ch": "ch", "DirectOut Aux": "auxin"}.get(head)
    try:
        if fam and rest.isdigit():
            return Target(fam, int(rest))
        if head == "DirectOut FX" and len(rest) == 2 and rest[0].isdigit() and rest[1] in "LR":
            return Target("fxrtn", 2 * int(rest[0]) - (1 if rest[1] == "L" else 0))
    except TargetError:
        return None
    return None


_BLOCK_RE = re.compile(r"^(\d+)-(\d+)$")
_OUT_BLOCK_RE = re.compile(r"^OUT(\d+)-(\d+)$")


def _xlr_carries_tap(out_blocks: Mapping[str, Any], tap: int) -> bool | None:
    """``/config/routing/OUT/<block>`` = ``OUT<block>`` means XLR sockets ``block`` carry taps ``block``
    (fx_routing_scenes.md §4.6); ``None`` when the block that owns ``tap`` was not read."""
    for block, tok in out_blocks.items():
        m = _BLOCK_RE.match(block)
        if m and int(m.group(1)) <= tap <= int(m.group(2)):
            return None if tok is None else tok == f"OUT{block}"
    return None


def _aes_channels_for_tap(aes_blocks: Mapping[str, Any], tap: int) -> list[int]:
    """AES50 channels that copy XLR tap ``tap``: every 8-channel block whose token is ``OUT1-8`` /
    ``OUT9-16`` (fx_routing_scenes.md §4.5, §4.7 example: bus 3 on tap 11 with 9-16 = OUT9-16 → channel 11)."""
    out: list[int] = []
    for block, tok in aes_blocks.items():
        mb = _BLOCK_RE.match(block)
        mt = _OUT_BLOCK_RE.match(tok) if isinstance(tok, str) else None
        if mb and mt and int(mt.group(1)) <= tap <= int(mt.group(2)):
            out.append(int(mb.group(1)) + tap - int(mt.group(1)))
    return out
# -- routing tokens (fx_routing_scenes.md §4.3, §4.8) -----------------------------------------------

IN_BLOCKS: tuple[str, ...] = ("1-8", "9-16", "17-24", "25-32", "AUX")  # /config/routing/IN/<block>
USER_IN_MAX = 168  # /config/userrout/in/NN is int 0..168 (fx_routing_scenes.md §4.8, DOC p.21)
# User-In source numbering: (first number, count, token prefix, digits, description) of each range.
_USER_IN_RANGES: tuple[tuple[int, int, str, int, str], ...] = (
    (1, 32, "IN", 2, "local XLR"),
    (33, 48, "A", 2, "AES50-A"),
    (81, 48, "B", 2, "AES50-B"),
    (129, 32, "CARD", 2, "card/USB"),
    (161, 6, "AUX", 1, "Aux In"),
)
_USER_IN_FIXED: dict[int, tuple[str, str]] = {0: ("OFF", "off"), 167: ("TBINT", "talkback internal"), 168: ("TBEXT", "talkback external")}
_USER_IN_BY_TOKEN: dict[str, int] = {"OFF": 0, "TBINT": 167, "TBINTERNAL": 167, "TBEXT": 168, "TBEXTERNAL": 168}
_USER_IN_SPELLINGS: dict[str, tuple[int, int, str]] = {  # prefix spelling -> (first number, count, description)
    "IN": (1, 32, "local XLR"), "INPUT": (1, 32, "local XLR"), "LOCAL": (1, 32, "local XLR"), "LOC": (1, 32, "local XLR"), "XLR": (1, 32, "local XLR"),
    "A": (33, 48, "AES50-A"), "AES50A": (33, 48, "AES50-A"), "AESA": (33, 48, "AES50-A"),
    "B": (81, 48, "AES50-B"), "AES50B": (81, 48, "AES50-B"), "AESB": (81, 48, "AES50-B"),
    "CARD": (129, 32, "card/USB"), "USB": (129, 32, "card/USB"),
    "AUX": (161, 6, "Aux In"), "AUXIN": (161, 6, "Aux In"),
}
_USER_IN_RE = re.compile(r"^(AES50A|AES50B|AESA|AESB|AUXIN|AUX|CARD|USB|INPUT|IN|LOCAL|LOC|XLR|A|B)(\d{1,3})$")
_USER_IN_HELP = f"OFF, IN01..IN32 (local XLR), A01..A48 (AES50-A), B01..B48 (AES50-B), CARD01..CARD32, AUX1..AUX6, TBINT, TBEXT or a number 0..{USER_IN_MAX}"
_IN_BLOCK_PREFIX_RE = re.compile(r"^(?:INPUTS|INPUT|IN|CHANNELS|CHANNEL|CH)?\s*/?\s*")


def user_in_source(n: Any) -> tuple[str, str]:
    """A ``/config/userrout/in`` number 0..168 → ``(token, description)``: ``0 → ("OFF", "off")``,
    ``4 → ("IN04", "local XLR 4")``, ``33 → ("A01", "AES50-A 1")``, ``81 → ("B01", "AES50-B 1")``,
    ``129 → ("CARD01", "card/USB 1")``, ``161 → ("AUX1", "Aux In 1")``, ``167 / 168 → TBINT / TBEXT``
    (fx_routing_scenes.md §4.8). ``ValueError`` outside 0..168 or for a non-int."""
    if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= USER_IN_MAX:
        raise ValueError(f"User-In source number must be 0..{USER_IN_MAX}, got {n!r}")
    if n in _USER_IN_FIXED:
        return _USER_IN_FIXED[n]
    for first, count, prefix, digits, what in _USER_IN_RANGES:
        if first <= n < first + count:
            k = n - first + 1
            return f"{prefix}{k:0{digits}d}", f"{what} {k}"
    raise AssertionError(f"user-in table does not cover {n}")  # unreachable: the ranges tile 0..168


def user_in_token(n: Any) -> str:
    """Token of a User-In source number (see :func:`user_in_source`)."""
    return user_in_source(n)[0]


def user_in_number(source: Any) -> int:
    """A User-In source in any accepted spelling → the desk's number 0..168: ``'IN04'`` / ``'in 4'`` /
    ``'local 4'`` / ``'xlr 4'`` → 4; ``'A01'`` / ``'AES50-A 1'`` → 33; ``'B01'`` → 81; ``'CARD01'`` /
    ``'usb 1'`` → 129; ``'AUX1'`` / ``'aux in 1'`` → 161; ``'TBINT'`` / ``'TBEXT'`` → 167 / 168;
    ``'OFF'`` → 0; an int or a digit string is the raw number. Case, spaces, ``_`` and ``-`` are
    ignored. ``ValueError`` otherwise (a bool, a float, ``'ch 4'``, an out-of-range number)."""
    if isinstance(source, bool) or not isinstance(source, (int, str)):
        raise ValueError(f"User-In source must be one of {_USER_IN_HELP}, got {source!r}")
    if isinstance(source, int):
        n = source
    else:
        text = source.strip()
        if text[:1] in ("-", "+"):  # a signed number is never a source; "-" is only stripped inside spellings (AES50-A 1)
            raise ValueError(f"User-In source number must be 0..{USER_IN_MAX}, got {source!r}")
        key = re.sub(r"[\s_\-]+", "", text.upper())
        if key.isdigit():
            n = int(key)
        elif key in _USER_IN_BY_TOKEN:
            return _USER_IN_BY_TOKEN[key]
        else:
            m = _USER_IN_RE.match(key)
            if not m:
                raise ValueError(f"unknown User-In source {source!r}; use {_USER_IN_HELP}")
            first, count, what = _USER_IN_SPELLINGS[m.group(1)]
            k = int(m.group(2))
            if not 1 <= k <= count:
                raise ValueError(f"{what} {k} is out of range 1..{count} ({source!r})")
            return first + k - 1
    if not 0 <= n <= USER_IN_MAX:
        raise ValueError(f"User-In source number must be 0..{USER_IN_MAX}, got {n}")
    return n


def in_block(block: Any) -> str:
    """An input-block name in any accepted spelling → one of :data:`IN_BLOCKS`: ``'17-24'``,
    ``'ch 17-24'``, ``'IN/17-24'``, ``'inputs 17–24'`` → ``'17-24'``; ``'aux'`` / ``'aux in'`` → ``'AUX'``.
    ``ValueError`` otherwise."""
    if not isinstance(block, str):
        raise ValueError(f"block must be one of {', '.join(IN_BLOCKS)}, got {block!r}")
    key = _IN_BLOCK_PREFIX_RE.sub("", block.strip().upper().replace("–", "-").replace("—", "-"), count=1)
    key = re.sub(r"\s+", "", key)
    if key in ("AUX", "AUXIN", "AUXINS", "AUXINPUTS"):
        return "AUX"
    if key in IN_BLOCKS:
        return key
    raise ValueError(f"unknown input block {block!r}; use one of {', '.join(IN_BLOCKS)}")


def bus_link_pair(bus: Any) -> tuple[int, int]:
    """Mix bus 1..16 → its stereo-link pair ``(odd, even)``: ``1 → (1, 2)``, ``4 → (3, 4)``. ``ValueError`` otherwise."""
    if isinstance(bus, bool) or not isinstance(bus, int) or not 1 <= bus <= 16:
        raise ValueError(f"bus must be 1..16, got {bus!r}")
    return (bus, bus + 1) if bus % 2 else (bus - 1, bus)


def routing_token(source: Any, tokens: Sequence[str]) -> str:
    """A routing-block source in any case or spacing (``'an 1-8'``, ``'a17-24'``, ``'UIN 17-24'``) → its
    enum token; ``ValueError`` when it is not one of ``tokens``."""
    if not isinstance(source, str):
        raise ValueError(f"source must be a token such as {tokens[0]!r}, got {source!r}")
    key = re.sub(r"[\s_]+", "", source.upper().replace("–", "-"))
    for t in tokens:
        if t.upper() == key:
            return t
    raise ValueError(f"unknown source {source!r}; use one of {', '.join(tokens)}")


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
        # A section read this soon after one of our writes to it is answered by the desk but never
        # cached: real hardware served the pre-write value once (HANDOVER §4b), and caching that
        # answer would repeat the lie for read_cache_ttl_s (DESIGN §0.6: the desk is the truth).
        self._settle_window_s = float(d.policy.get("settle_window_s", 0.5))
        self._written_at: dict[str, float] = {}  # node path -> monotonic time of our last write into it
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
        # panic bookkeeping (see panic()): writers that span a panic compare the counter; outputs a
        # panic muted stay latched against Tier-1 re-opening; an unconfirmed panic is re-sent on reconnect
        self.panic_count = 0
        self.last_panic: dict[str, Any] | None = None
        self._panic_latched: set[str] = set()
        self._panic_reassert = False
        self._panic_tasks: set[asyncio.Task] = set()
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
        self._unsub_conn = events.subscribe(self._on_connection_event, types={"connection.state"})

    # -- lifecycle / cache ---------------------------------------------------------------------

    async def close(self) -> None:
        """Cancel running ramps and stop listening for pushes (the connection is not closed)."""
        self._unsub_update()
        self._unsub_write()
        self._unsub_conn()
        for t in list(self._panic_tasks):
            t.cancel()
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
        # A request issued before this invalidation must not serve a reader that arrives after
        # it (read-your-writes): detach it from the join index. Its own waiters keep their
        # future (the fetcher resolves the futures it created, not whatever the index holds).
        if self._inflight.pop(path, None) is not None:
            self._epoch[path] = self._epoch.get(path, 0) + 1
        if self._dropped_since is not None:
            self._dropped_since.add(path)

    def _invalidate_address(self, address: str, *, written: bool = False) -> None:
        hit = self._leaf_index.get(address)
        path = hit[0].path if hit is not None else address if address in self._node_by_path else None
        if path is None:
            return
        self._drop(path)
        if written:
            self._written_at[path] = time.monotonic()

    def _on_push(self, address: str, args: tuple) -> None:
        # DESIGN §0.6: the desk is the source of truth — a pushed change retires our copy.
        self._invalidate_address(address)

    def _on_write_event(self, ev: Any) -> None:
        addr = ev.data.get("address") if isinstance(ev.data, dict) else None
        if isinstance(addr, str):
            self._invalidate_address(addr, written=True)

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
            mine = {p: waits[p] for p in fetch}  # the futures THIS call created and must resolve
            try:
                lines = await self._conn.node_many(fetch, concurrency)
            except BaseException as e:
                for p in fetch:
                    f = mine[p]
                    if self._inflight.get(p) is f:
                        del self._inflight[p]
                    if not f.done():
                        # Never cancel() a shared future: asyncio.shield does not stop an *inner*
                        # cancellation reaching the waiters, so another tool call would die with a
                        # CancelledError it never asked for. Hand it an error it can report instead.
                        f.set_exception(e if isinstance(e, Exception) else DeskError("TIMEOUT", f"read of {p} was abandoned"))
                        f.exception()  # mark retrieved: nobody may await this one
                raise
            t1 = time.monotonic()
            for p in fetch:
                vals = self._parse(p, lines.get(p))
                current = self._epoch.get(p, 0) == epochs[p]  # not invalidated while in flight
                settled = t1 - self._written_at.get(p, float("-inf")) >= self._settle_window_s
                if vals is not None and current and settled:
                    self._cache[p] = (t1, vals)
                if current:
                    self._epoch.pop(p, None)
                f = mine[p]
                if self._inflight.get(p) is f:
                    del self._inflight[p]
                if not f.done():
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
            # a push during the sweep beats the sweep; a section we just wrote is not settled yet
            if p not in self._inflight and p not in dropped and now - self._written_at.get(p, float("-inf")) >= self._settle_window_s:
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

    def geq_par_from_push(self, address: str, args: Sequence[Any], fx_type: str | None) -> tuple[int, str, int, float] | None:
        """Decode a pushed ``/fx/N/par/PP`` update into ``(slot, side, band, gain_db)`` for a GEQ/TEQ of
        ``fx_type`` (band 1..31, 32 = master; side "A"/"B", stereo types report "A"). None for anything
        else. Used by CFS² to learn about cuts the engineer makes by hand during a session."""
        parts = address.strip("/").split("/")
        if len(parts) != 4 or parts[0] != "fx" or parts[2] != "par" or not args:
            return None
        try:
            slot, par = int(parts[1]), int(parts[3])
            raw = float(args[0])
        except (TypeError, ValueError):
            return None
        lay = self._geq_layout
        if fx_type in self._geq_dual:
            sides = (("A", lay["par_a_first"], lay["par_a_master"]), ("B", lay["par_b_first"], lay["par_b_master"]))
        elif fx_type in self._geq_stereo:
            sides = (("A", lay["par_a_first"], lay["par_a_master"]),)
        else:
            return None
        for side, first, master in sides:
            if par == master:
                return slot, side, 32, float(self._geq_scale.to_value(raw))
            if first <= par < first + 31:
                return slot, side, par - first + 1, float(self._geq_scale.to_value(raw))
        return None

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
            return None  # CARD: no head amp. UIN is resolved separately, below.

        if 1 <= src <= 32:
            k = src - 1
            b, off = divmod(k, 8)
            tok = blocks.get(key + ("1-8", "9-16", "17-24", "25-32")[b])
            # Firmware-4.x USER routing: the block reads UIN1-8/UIN9-16/… and the per-input patch
            # lives in /config/userrout/in/NN, one 1-based source number per user input:
            #   1..32 local XLR → head amp 000..031 · 33..80 AES50-A → 032..079
            #   81..128 AES50-B → 080..127 · >128 card/USB → no preamp
            # Found at M5 on X32RACK-Jim (FW 4.13), where every channel previously resolved to None:
            # /config/userrout/in = "1 129 130 33 …", and ch 4's source IN04 → 33 → head amp 032,
            # which is exactly where that channel's +44.5 dB SM58 gain sits. Channels 2-3 map to
            # 129/130 (card), so they correctly report no preamp.
            if isinstance(tok, str) and tok.upper().startswith("UIN"):
                patch = await self._leaf(f"/config/userrout/in/{src:02d}")
                if not isinstance(patch, int) or isinstance(patch, bool) or not 1 <= patch <= 128:
                    return None
                return patch - 1
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
        if target is not None and target.family == "main" and ("/dyn/" in address or "/eq/" in address):
            return (f"{target.label} EQ and dynamics are guarded (Tier 2): a boost or make-up gain on the PA bus is a "
                    "house-wide level jump. There is no confirming tool for them yet; make the change on the console")
        who = f" ({target.label})" if target is not None else ""
        return f"{address}{who} is a guarded (Tier 2) parameter; use the confirming tool for it"

    async def _write(self, address: str, raw: Any, *, value: Any, tool: str, target: Target | None = None, guarded: bool = False) -> None:
        """policy.tier_for → snapshot-before-write → rate limiter → ``conn.set`` → cache + event."""
        tier = self._policy.tier_for(address)
        if tier >= Tier.GUARDED and not guarded:
            raise DeskError("GUARDED", self._guarded_msg(address, target), address=address)
        await self.ensure_pre_write_snapshot()
        await self._policy.acquire_write(panic=_PRIORITY_WRITE.get())
        # panic() may have run while this write was parked in the two awaits above (the pre-write
        # snapshot is a full desk dump; the limiter can sleep). An un-mute that was legal when the tool
        # checked the latch must not land after the panic and re-open what it silenced.
        if (not guarded and target is not None and target.key in self._panic_latched
                and address.endswith("/mix/on") and raw not in (0, False, "OFF")):
            self._check_panic_latch(target)  # confirmed (guarded) re-opens are the operator's decision and pass
        try:
            await self._conn.set(address, raw)
        except NotConnected as e:
            raise DeskError("NOT_CONNECTED", str(e)) from None
        self.write_count += 1
        self._invalidate_address(address, written=True)
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
        # No relative guard on an ABSOLUTE move (Jim's call, 2026-09-20, after M5 on the real desk):
        # the destination is already bounded by the family ceiling above and the move is ramped, so
        # the ±6 dB rule only blocked moves the operator explicitly asked for. It previously exempted
        # a start at exactly −∞, which was too narrow to help: a send resting at −84 dB is finite, so
        # "put kick in Tony's ears at −12 dB" was refused as a +72 dB jump. The guard stays on
        # adjust_level(), where a runaway or a typo is what it is actually defending against.
        # ...except in show mode, which exists precisely to catch accidents mid-set: there an absolute move
        # is held to the same (tighter) relative limit unless the caller passes force — "put kick in Tony's
        # ears" is still one deliberate force=true away, a typo'd 0 dB on a live wedge is not.
        if self._policy.show_mode and not force and before_db is not None:
            a = FADER_FLOOR_DB if before_db == NEG_INF_DB or before_db <= FADER_FLOOR_DB else float(before_db)
            b = FADER_FLOOR_DB if after == NEG_INF_DB else float(after)
            self._policy.check_relative(b - a, force=False)
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
        force: bool = False, kind: Literal["fader", "send", "mlevel"] = "fader", tool: str | None = None,
    ) -> dict[str, Any]:
        """Fader (``kind="fader"``), send level (``send_to`` = destination number) or M/C level
        (``kind="mlevel"``) to ``db`` with a ramp (``ramp_ms`` None → policy default). Clamped to the
        family ceiling (reported), relative limit unless ``force`` (see module doc). ``tool`` names
        the caller in the ``desk.write`` events (default: set_send / set_fader)."""
        t = self._target(t)
        spec, address, pk = self._level_param(t, kind, send_to)
        before = await self._leaf(address)
        res = await self._move_level(t, spec, address, before, _num(db, "db"), ramp_ms=ramp_ms, force=force, policy_kind=pk,
                                     tool=tool or ("set_send" if pk == "send" and send_to else "set_fader"))
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
        if not muted:
            self._check_panic_latch(t)
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

    async def set_send_mute(self, ch: Target | str | int, bus: int, muted: bool, *, tool: str = "set_send_mute") -> dict[str, Any]:
        """Send on/off (``mix/NN/on``, inverted like a strip mute); ``was_muted`` is the state before."""
        t = self._target(ch)
        st = self._d.strips[t.family]
        if not st.sends:
            raise DeskError("NOT_SUPPORTED", f"{t.label} has no sends")
        n = _int(bus, "bus", 1, st.sends)
        spec, address = self._param(t, "mix/{send:02d}/on", send=n)
        was = await self._leaf(address)
        await self._write(address, spec.to_raw(not bool(muted)), value={"muted": bool(muted)}, tool=tool, target=t)
        return {"target": t.key, "label": t.label, "send_to": n, "muted": bool(muted), "was_muted": (not was) if was is not None else None}

    async def set_send_tap(self, ch: Target | str | int, bus: int, tap: str) -> dict[str, Any]:
        """Tap point of the send from ``ch`` to ``bus`` (``mix/NN/type``: IN/LC, <-EQ, EQ->, PRE, POST,
        GRP — see :func:`normalise_send_tap` for the spellings; bus → matrix sends have no GRP). The
        desk keeps one type per odd/even send pair, on the odd send (scales_params.md §4.9), so the
        write goes to the odd partner and ``pair`` says which two destinations it changed."""
        t = self._target(ch)
        st = self._d.strips[t.family]
        if not st.sends:
            raise DeskError("NOT_SUPPORTED", f"{t.label} has no sends")
        n = _int(bus, "bus", 1, st.sends)
        lo, hi = send_pair(n, st.sends)
        spec, address = self._param(t, "mix/{send:02d}/type", send=lo)
        token = normalise_send_tap(tap, spec.enum or ())
        before = await self._leaf(address)
        await self._write(address, spec.to_raw(token), value=token, tool="set_send_tap", target=t)
        return {"target": t.key, "label": t.label, "send_to": n, "pair": [lo, hi], "type_send": lo, "address": address, "tap": token, "before": before}

    async def set_main_assign(
        self, t: Target | str | int, *, lr: bool | None = None, mono: bool | None = None, mono_level_db: float | None = None,
        ramp_ms: int | None = None,
    ) -> dict[str, Any]:
        """Main L/R assign (``mix/st``), M/C assign (``mix/mono``) and M/C level (``mix/mlevel``: a
        ramped level move with the send ceiling, ``ramp_ms`` None → policy default) of a channel,
        aux-in, FX return or bus. An assign being switched OFF is written before the level, one being
        switched ON after it, so the level never jumps through an open path. ``before`` is the state read
        first; ``mono_level`` is the level move's own result when one was made."""
        t = self._target(t)
        try:
            self._d.param(t.family, "mix/st")
        except DescriptorError:
            raise DeskError("NOT_SUPPORTED", f"{t.label} has no main assign (channels, aux-ins, FX returns and buses only)") from None
        if lr is None and mono is None and mono_level_db is None:
            raise DeskError("BAD_ARGUMENT", "nothing to set: give lr, mono and/or mono_level_db")
        mix = await self._section(f"{t.osc_prefix}/mix")
        before = {"lr": mix.get("mix/st"), "mono": mix.get("mix/mono"), "mono_level_db": _db1(mix.get("mix/mlevel")), "mono_level": _db_text(mix.get("mix/mlevel"))}
        switches = (("lr", "mix/st", lr), ("mono", "mix/mono", mono))
        applied: dict[str, Any] = {}
        out: dict[str, Any] = {"target": t.key, "label": t.label, "applied": applied, "before": before}
        for phase in (False, True):  # OFF first, ON last
            if phase and mono_level_db is not None:
                lvl = await self.set_level(t, mono_level_db, ramp_ms=ramp_ms, kind="mlevel", tool="set_main_assign")
                applied["mono_level_db"] = lvl["after_db"]
                out["mono_level"] = lvl
            for key, rel, val in switches:
                if val is not None and bool(val) is phase:
                    _a, applied[key] = await self._write_value(t, rel, bool(val), tool="set_main_assign")
        out["applied"] = {k: applied[k] for k in ("lr", "mono", "mono_level_db") if k in applied}
        return out

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
        clamps: list[dict[str, Any]] = []
        if makeup_db is not None:
            makeup_db, cl = self._policy.clamp_makeup_gain(_num(makeup_db, "makeup_db"))
            if cl:
                clamps.append(cl.to_dict())
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
        out: dict[str, Any] = {"target": t.key, "label": t.label, "applied": applied}
        if clamps:
            out["clamped"] = clamps
        return out

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
            text = "".join(ch if ch.isprintable() else " " for ch in str(name))  # a newline in a name would split a '/' node line later
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

    async def panic(self, *, verify_s: float | None = None) -> dict[str, Any]:
        """Mute Main LR, Main M/C, every bus and every matrix as fast as possible: fire-and-forget
        ``mix/on 0``, no ramps, no snapshot, rate limiter bypassed (Tier 1, never blocked).
        Silences every POST-tapped output (fx_routing_scenes.md §11 option 1). The sends always go
        out first; *afterwards* the 24 mutes are read back until they all show muted or ``verify_s``
        (0.6 s) passes, anything still reading unmuted is sent once more and re-checked, and the
        result says ``delivered`` = ``"confirmed"`` / ``"partial"`` (``unconfirmed`` lists the rest) /
        ``"sent"`` (``verify_s=0``) / ``"unconfirmed"`` (desk degraded). A SET has no ack and a
        datagram can be lost (transport.md §5.2), so "sent" is not "muted".

        Before the mutes go out, everything this server was itself writing is stopped: running
        fader ramps are cancelled and :attr:`panic_count` is bumped so that a ``restore()`` in
        flight and a CFS² session (which subscribes to ``desk.panic.begin``) abort instead of
        re-opening or re-raising what the panic just silenced. The muted outputs are *latched*:
        Tier-1 ``unmute``/``restore`` refuse them with ``PANIC_LATCHED`` until :meth:`clear_panic_latch`
        (a confirmed tool) or a confirmed ``set_main_mute(false)`` says the emergency is over. A panic
        the desk may not have received (DEGRADED, socket gone) is re-sent when the connection returns.
        """
        t0 = time.perf_counter()
        self.panic_count += 1
        targets = [t for fam in _PANIC_FAMILIES for t in self._d.strip_targets(fam)]
        # 1. Our own writers first: nothing this process started may land after the mutes.
        ramps = [task for task in self._ramps.values() if not task.done()]
        self._ramps.clear()
        for task in ramps:
            task.cancel()
        self._panic_latched.update(t.key for t in targets)
        self._events.publish("desk.panic.begin", count=self.panic_count, cancelled_ramps=len(ramps),
                             targets=[t.key for t in targets])
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
        self._panic_reassert = unconfirmed or bool(failed)
        self.last_panic = {"ts": time.time(), "count": self.panic_count, "muted": list(done), "failed": list(failed),
                           "delivered": delivered if done else "not sent", "cancelled_ramps": len(ramps)}
        out: dict[str, Any] = {"muted": done, "count": len(done), "failed": failed, "elapsed_ms": round(elapsed, 1), "delivered": delivered,
                               "cancelled_ramps": len(ramps), "latched": True}
        vs = _PANIC_VERIFY_S if verify_s is None else max(0.0, float(verify_s))
        if done and not unconfirmed and vs > 0:
            try:
                out.update(await self._verify_panic([t for t in targets if t.key in done], vs))
            except asyncio.CancelledError:
                raise
            except Exception as e:  # verification is advice; it must never cost the panic result
                log.warning("panic verification failed: %s", e)
                out["verify_error"] = str(e)
            if out.get("unconfirmed"):
                self._panic_reassert = True  # something still reads open: re-send again on the next reconnect too
        out["reassert_pending"] = self._panic_reassert
        self._events.publish("desk.panic", elapsed_ms=round(elapsed, 1), count=len(done), failed=len(failed), delivered=out["delivered"],
                             cancelled_ramps=len(ramps), reassert_pending=self._panic_reassert,
                             confirmed=out.get("confirmed"), unconfirmed=len(out.get("unconfirmed") or []))
        log.warning("PANIC: %d outputs muted in %.1f ms (%s)%s%s%s%s", len(done), elapsed, out["delivered"],
                    f", {len(out['unconfirmed'])} NOT CONFIRMED: {', '.join(out['unconfirmed'])}" if out.get("unconfirmed") else "",
                    " (desk degraded, unconfirmed; will re-send on reconnect)" if unconfirmed else "",
                    f", {len(failed)} NOT SENT (will re-send on reconnect): {', '.join(failed)}" if failed else "",
                    f"; {len(ramps)} ramp(s) cancelled" if ramps else "")
        if not done:
            raise DeskError("NOT_CONNECTED", f"panic could not reach the desk: {last}; the mutes will be sent the moment it reconnects",
                            muted=done, failed=failed, reassert_pending=True)
        return out

    async def _verify_panic(self, targets: Sequence[Target], verify_s: float) -> dict[str, Any]:
        """Read ``mix/on`` of ``targets`` back until all show muted (bounded); re-send the rest once."""
        paths = {t.key: f"{t.osc_prefix}/mix" for t in targets}
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        attempts = 0

        async def poll(deadline_s: float) -> dict[str, Any]:
            nonlocal attempts
            deadline = loop.time() + deadline_s
            delay = 0.03
            vals: dict[str, Any] = {}
            while True:
                attempts += 1
                for p in paths.values():
                    self._drop(p)  # every attempt asks the desk, never our cache
                try:
                    secs = await asyncio.wait_for(self._read_sections(list(paths.values()), concurrency=24),
                                                  timeout=max(0.05, deadline - loop.time()))
                    vals = {k: (secs.get(p) or {}).get("mix/on") for k, p in paths.items()}  # True = ON = unmuted; None = no answer
                except (asyncio.TimeoutError, DeskError):
                    pass
                if vals and all(v is False for v in vals.values()):
                    return vals
                if loop.time() + delay >= deadline:
                    return vals
                await asyncio.sleep(delay)
                delay = min(0.2, delay * 1.6)

        vals = await poll(verify_s)
        pending = [k for k in paths if vals.get(k) is not False]
        resent: list[str] = []
        if pending:
            for t in targets:
                if t.key in pending:
                    try:
                        await self._conn.send_raw(f"{t.osc_prefix}/mix/on", 0)  # emergency path: no limiter, like the first round
                        resent.append(t.key)
                    except (NotConnected, OSError) as e:
                        log.debug("panic re-send %s failed: %s", t.key, e)
            vals = await poll(_PANIC_REVERIFY_S) or vals
            pending = [k for k in paths if vals.get(k) is not False]
        return {
            "confirmed": len(paths) - len(pending), "unconfirmed": pending, "resent": resent,
            "delivered": "confirmed" if not pending else "partial",
            "verify": {"attempts": attempts, "elapsed_ms": round((loop.time() - t0) * 1000.0, 1)},
        }

    # -- panic latch / re-assert -----------------------------------------------------------------------

    @property
    def panic_latched(self) -> list[str]:
        """Target keys silenced by the last panic that have not been released yet."""
        return sorted(self._panic_latched, key=lambda k: (k.split(".")[0], k))

    def _check_panic_latch(self, t: Target) -> None:
        if t.key in self._panic_latched:
            when = time.strftime("%H:%M:%S", time.localtime(self.last_panic["ts"])) if self.last_panic else "?"
            raise DeskError(
                "PANIC_LATCHED",
                f"{t.label} was silenced by panic() at {when} and stays muted until the operator confirms the emergency is over: "
                "call clear_panic (confirmation required), or re-open Main LR with set_main_mute",
                target=t.key, latched=self.panic_latched,
            )

    def clear_panic_latch(self, keys: Iterable[str] | None = None) -> list[str]:
        """Release the panic latch (all of it, or the given target keys). Unmutes nothing."""
        if keys is None:
            released = self.panic_latched
            self._panic_latched.clear()
        else:
            want = {str(k) for k in keys}
            released = sorted(want & self._panic_latched)
            self._panic_latched -= want
        if released:
            self._events.publish("desk.panic.cleared", released=released, remaining=self.panic_latched)
            log.warning("panic latch released for %d output(s)%s", len(released), "" if not self._panic_latched else f"; {len(self._panic_latched)} still latched")
        return released

    def _on_connection_event(self, ev: Any) -> None:
        if not self._panic_reassert or ev.data.get("state") != "connected":
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._reassert_panic(), name="desk-panic-reassert")
        self._panic_tasks.add(task)
        task.add_done_callback(self._panic_tasks.discard)

    async def _reassert_panic(self) -> None:
        """The desk is back after a panic it may not have received: send the mutes again (latched
        outputs only — anything the operator has since released is left alone)."""
        keys = self.panic_latched
        sent: list[str] = []
        for key in keys:
            try:
                t = parse_target(key)
                await self._conn.set(f"{t.osc_prefix}/mix/on", 0)
                sent.append(key)
            except (NotConnected, OSError, ValueError) as e:
                log.warning("panic re-assert: %s not sent: %s", key, e)
        if sent and len(sent) == len(keys):
            self._panic_reassert = False
        self._events.publish("desk.panic.reasserted", count=len(sent), targets=sent, complete=not self._panic_reassert)
        log.warning("PANIC re-asserted after reconnect: %d/%d outputs muted again", len(sent), len(keys))

    # -- Tier 2 executors (the server does the confirmation dance) ---------------------------------------

    async def set_main_level(self, which: str = "st", db: float = -90.0, *, ramp_ms: int | None = None) -> dict[str, Any]:
        """Main LR (``"st"``) or M/C (``"m"``) fader; clamped to the main ceiling; no relative limit
        (the confirmation token is the gate)."""
        t = self._main_target(which)
        spec, address, pk = self._level_param(t, "fader", None)
        before = await self._leaf(address)
        return await self._move_level(t, spec, address, before, _num(db, "db"), ramp_ms=ramp_ms, force=True, policy_kind=pk, tool="set_main_fader", guarded=True)

    async def set_main_mute(self, which: str, muted: bool) -> dict[str, Any]:
        """Guarded (the server confirmed it), so re-opening a main after a panic is allowed and
        releases that main from the panic latch: the operator has said the emergency is over."""
        t = self._main_target(which)
        spec, address = self._param(t, "mix/on")
        was = await self._leaf(address)
        await self._write(address, spec.to_raw(not bool(muted)), value={"muted": bool(muted)}, tool="set_main_mute", target=t, guarded=True)
        if not muted:
            self.clear_panic_latch([t.key])
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
        if self._panic_latched:
            raise DeskError("PANIC_LATCHED", "outputs are latched by panic(); a restore could re-open them. "
                            "Call clear_panic (confirmation required) first", latched=self.panic_latched)
        await self.ensure_pre_write_snapshot()
        t0 = time.perf_counter()
        panic_gen = self.panic_count
        live = await self.dump(sections=[scope] if scope else None)
        lines = restore_plan(snap.state, live, self._d, scope=scope)
        written = 0
        failed: list[str] = []
        aborted: str | None = None
        for line in lines:
            if self.panic_count != panic_gen:
                aborted = "panic() was called during the restore; the remaining lines were not written"
                log.warning("restore %s aborted by panic after %d/%d line(s)", snap.id, written, len(lines))
                break
            await self._policy.acquire_write()
            if self.panic_count != panic_gen:  # the wait for a write slot is exactly where a panic lands
                aborted = "panic() was called during the restore; the remaining lines were not written"
                log.warning("restore %s aborted by panic after %d/%d line(s)", snap.id, written, len(lines))
                break
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

    # -- routing / stereo links / solo (server: get_routing, set_bus_link, set_input_block, set_user_in, set_solo_mode)

    def _config_param(self, rel: str) -> tuple[ParamSpec, str]:
        spec = self._d.param("config", rel)
        return spec, spec.address()

    @staticmethod
    def _uin_group(tok: Any) -> tuple[int, int] | None:
        """``"UIN9-16"`` → (9, 16), ``"UIN1-6"`` → (1, 6); None for any other routing token."""
        if not isinstance(tok, str) or not tok.upper().startswith("UIN"):
            return None
        lo, _, hi = tok[3:].partition("-")
        return (int(lo), int(hi)) if lo.isdigit() and hi.isdigit() else None

    async def get_routing(self) -> dict[str, Any]:
        """The input side of the desk in one read: ``routswitch`` (REC/PLAY), the five ``inputs`` blocks of
        ``/config/routing/IN`` (plus ``play_inputs`` when PLAY is in force), the 32 ``user_in`` slots
        (``number`` → ``source`` token and ``description``; ``active`` when a block in force reads their
        UIN group, ``feeds`` naming the In / Aux In they then feed), ``bus_links`` (``"1-2"`` → bool) and
        the ``solo`` PFL/AFL modes (``channels``, ``buses``, ``dcas``)."""
        paths = ["/config/routing", "/config/routing/IN", "/config/routing/PLAY", "/config/userrout/in", "/config/buslink", "/config/solo"]
        secs = await self._read_sections(paths)
        switch = self._require(secs, "/config/routing").get("routing/routswitch")
        play = switch == "PLAY"
        inputs = {k: self._require(secs, "/config/routing/IN").get(f"routing/IN/{k}") for k in IN_BLOCKS}
        play_inputs = {k: self._require(secs, "/config/routing/PLAY").get(f"routing/PLAY/{k}") for k in IN_BLOCKS}
        in_force = play_inputs if play else inputs
        feeds: dict[int, list[str]] = {}
        for k in IN_BLOCKS[:4]:  # "UIN9-16" read by block "1-8": user-in slots 9..16 feed In 1..8
            grp = self._uin_group(in_force.get(k))
            if grp:
                first_in = int(k.split("-")[0])
                for i in range(8):
                    feeds.setdefault(grp[0] + i, []).append(f"In {first_in + i}")
        grp = self._uin_group(in_force.get("AUX"))  # UIN1-2 / 1-4 / 1-6: slots 1..n feed Aux In 1..n
        if grp:
            for i in range(grp[0], grp[1] + 1):
                feeds.setdefault(i, []).append(f"Aux In {i}")
        uin = self._require(secs, "/config/userrout/in")
        user_in: list[dict[str, Any]] = []
        for slot in range(1, 33):
            n = uin.get(f"userrout/in/{slot:02d}")
            try:
                tok, desc = user_in_source(n)
            except ValueError:
                tok = desc = None
            user_in.append({"slot": slot, "number": n, "source": tok, "description": desc,
                            "active": slot in feeds, "feeds": " and ".join(feeds[slot]) if slot in feeds else None})
        links = self._require(secs, "/config/buslink")
        bus_links = {f"{o}-{o + 1}": links.get(f"buslink/{o}-{o + 1}") for o in range(1, 17, 2)}
        solo = self._require(secs, "/config/solo")
        out: dict[str, Any] = {
            "routswitch": switch, "inputs": inputs, "user_in": user_in, "bus_links": bus_links,
            "solo": {"channels": solo.get("solo/chmode"), "buses": solo.get("solo/busmode"), "dcas": solo.get("solo/dcamode")},
        }
        if play:
            out["play_inputs"] = play_inputs
        return out

    async def set_bus_link(self, bus: int, on: bool) -> dict[str, Any]:
        """Stereo-link (``on``) or unlink the mix-bus pair holding ``bus`` 1..16 — ``/config/buslink/N-M``
        (guarded; the Tier-2 executor behind ``set_bus_link``: linking re-syncs the pair)."""
        o, e = bus_link_pair(_int(bus, "bus", 1, 16))
        spec, address = self._config_param(f"buslink/{o}-{e}")
        before = await self._leaf(address)
        await self._write(address, spec.to_raw(bool(on)), value=bool(on), tool="set_bus_link", target=Target("bus", o), guarded=True)
        return {"pair": f"{o}-{e}", "buses": [o, e], "link": bool(on), "was_linked": before, "address": address}

    async def set_input_block(self, block: str, source: str) -> dict[str, Any]:
        """Route an 8-channel input block: ``/config/routing/IN/<block>`` (``1-8`` … ``25-32``: enum
        ``routing_in``; ``AUX``: ``routing_in_aux``) to a source token (guarded; Tier-2 executor)."""
        try:
            key = in_block(block)
            tok = routing_token(source, self._d.enum("routing_in_aux" if key == "AUX" else "routing_in"))
        except ValueError as ex:
            raise DeskError("BAD_ARGUMENT", str(ex)) from None
        spec, address = self._config_param(f"routing/IN/{key}")
        before = await self._leaf(address)
        await self._write(address, spec.to_raw(tok), value=tok, tool="set_input_block", guarded=True)
        return {"block": key, "address": address, "before": before, "source": tok}

    async def set_user_in(self, slot: int, source: str | int) -> dict[str, Any]:
        """Patch User-In slot 1..32 (``/config/userrout/in/NN``) to a source (:func:`user_in_number`
        spellings or the raw number 0..168); guarded, the Tier-2 executor behind ``set_user_in``."""
        s = _int(slot, "slot", 1, 32)
        try:
            n = user_in_number(source)
        except ValueError as ex:
            raise DeskError("BAD_ARGUMENT", str(ex)) from None
        spec, address = self._config_param(f"userrout/in/{s:02d}")
        before = await self._leaf(address)
        await self._write(address, spec.to_raw(n), value=n, tool="set_user_in", guarded=True)
        tok, desc = user_in_source(n)
        try:
            before_tok: str | None = user_in_token(before)
        except ValueError:
            before_tok = None
        return {"slot": s, "address": address, "before": before, "before_source": before_tok, "number": n, "source": tok, "description": desc}

    async def set_solo_mode(self, *, channels: str | None = None, buses: str | None = None, dcas: str | None = None) -> dict[str, Any]:
        """PFL/AFL solo mode of the channels, buses and/or DCAs (``/config/solo/chmode|busmode|dcamode``,
        enum ``pfl_afl``; Tier 1). Returns ``{"applied": {...}, "before": {...}}`` for the values given."""
        tokens = self._d.enum("pfl_afl")
        items: list[tuple[str, str, str]] = []
        for rel, key, val in (("solo/chmode", "channels", channels), ("solo/busmode", "buses", buses), ("solo/dcamode", "dcas", dcas)):
            if val is None:
                continue
            tok = val.strip().upper() if isinstance(val, str) else None
            if tok not in tokens:
                raise DeskError("BAD_ARGUMENT", f"{key} must be {' or '.join(tokens)}, got {val!r}")
            items.append((rel, key, tok))
        if not items:
            raise DeskError("BAD_ARGUMENT", f"give channels, buses and/or dcas ({' or '.join(tokens)})")
        cur = await self._section("/config/solo")
        applied: dict[str, str] = {}
        before: dict[str, Any] = {}
        for rel, key, tok in items:
            spec, address = self._config_param(rel)
            before[key] = cur.get(rel)
            await self._write(address, spec.to_raw(tok), value=tok, tool="set_solo_mode")
            applied[key] = tok
        return {"applied": applied, "before": before}

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

    # -- output taps (fx_routing_scenes.md §4.7) ------------------------------------------------------

    def _output_spec(self, group: Any, out: Any, rel: str) -> tuple[ParamSpec, str, str, int]:
        """(spec, concrete address, group, tap number) of ``rel`` (``src``/``pos``/``invert``) on tap
        ``out`` of ``group`` (``main`` = XLR OUT 1-16, ``aux`` = AUX OUT 1-6); BAD_ARGUMENT otherwise."""
        g = str(group).strip().lower() if isinstance(group, str) else ""
        if g not in _OUTPUT_GROUPS:
            raise DeskError("BAD_ARGUMENT", f"output group must be 'main' (XLR OUT 1-16) or 'aux' (AUX OUT 1-6), got {group!r}")
        tmpl, var, label = _OUTPUT_GROUPS[g]
        spec = self._d.param("outputs", f"{tmpl}/{rel}")
        rng = spec.var_ranges.get(var)
        lo, hi = rng if isinstance(rng, tuple) else (1, 1)
        n = _int(out, f"{label} number", lo, hi)
        return spec, spec.address(**{var: n}), g, n

    def _output_node_path(self, group: str, n: int) -> str:
        """``/outputs/main/03`` — the tap's node (its ``src`` address minus the leaf)."""
        _spec, address, _g, _n = self._output_spec(group, n, "src")
        return address.rsplit("/", 1)[0]

    def _tap_dict(self, group: str, n: int, vals: Mapping[str, Any], names: Mapping[str, Mapping[Any, str]]) -> dict[str, Any]:
        tmpl, _var, label = _OUTPUT_GROUPS[group]
        rel = tmpl.format(n=n, idx=n)
        src, pos, inv = vals.get(f"{rel}/src"), vals.get(f"{rel}/pos"), vals.get(f"{rel}/invert")
        tokens = self._d.enum("output_src")
        t = output_source_target(src)
        return {
            "out": n,
            "label": f"{label} {n}",
            "address": self._output_node_path(group, n),
            "source": src,
            "source_index": tokens.index(src) if isinstance(src, str) and src in tokens else None,
            "target": t.key if t else None,
            "name": (names.get(t.family) or {}).get(t.index) if t else None,
            "pos": pos,
            "invert": inv,
            "follows_mute": (pos in _OUTPUT_POS_FOLLOWS_MUTE) if isinstance(pos, str) else None,
        }

    async def _source_names(self, taps: Iterable[Mapping[str, Any]]) -> dict[str, dict[Any, str]]:
        """Strip names for every family the taps draw from (a failed read just drops the names)."""
        fams = sorted({t.family for tap in taps if (t := output_source_target(tap.get("source"))) is not None})
        names: dict[str, dict[Any, str]] = {}
        for fam in fams:
            try:
                names[fam] = await self.get_names(fam)
            except DeskError:
                names[fam] = {}
        return names

    async def get_output(self, group: str, out: int) -> dict[str, Any]:
        """One output tap: ``{out, label, address, source, source_index, target, name, pos, invert,
        follows_mute}`` — ``target``/``name`` are the strip the source taps (bus.3 'Wedge A'),
        ``follows_mute`` whether that strip's mute silences the tap (+M variants and POST)."""
        _spec, _addr, g, n = self._output_spec(group, out, "src")
        path = self._output_node_path(g, n)
        vals = await self._section(path)
        bare = self._tap_dict(g, n, vals, {})
        return self._tap_dict(g, n, vals, await self._source_names([bare]))

    async def get_outputs(self) -> dict[str, Any]:
        """The physical output taps (fx_routing_scenes.md §4.7) and the routing blocks that carry
        them: ``{"main": [16 taps], "aux": [6 taps], "routing": {"OUT": {block: token}, "AES50A":
        {block: token}}}``. Taps are :meth:`get_output` dicts; the XLR taps also say whether their own
        socket carries them (``xlr``: ``/config/routing/OUT`` block = ``OUTn-m``) and which AES50-A
        channels copy them (``aes50a``, from the ``OUT1-8``/``OUT9-16`` blocks)."""
        main_n = [n for n in self._output_range("main")]
        aux_n = [n for n in self._output_range("aux")]
        main_paths = [self._output_node_path("main", n) for n in main_n]
        aux_paths = [self._output_node_path("aux", n) for n in aux_n]
        out_path = self._d.param("config", "routing/OUT/1-4").address().rsplit("/", 1)[0]
        aes_path = self._d.param("config", "routing/AES50A/1-8").address().rsplit("/", 1)[0]
        secs = await self._read_sections(main_paths + aux_paths + [out_path, aes_path])
        main_vals = [self._require(secs, p) for p in main_paths]
        aux_vals = [self._require(secs, p) for p in aux_paths]
        out_blocks = {k.rsplit("/", 1)[1]: v for k, v in self._require(secs, out_path).items()}
        aes_blocks = {k.rsplit("/", 1)[1]: v for k, v in self._require(secs, aes_path).items()}
        bare = [self._tap_dict("main", n, v, {}) for n, v in zip(main_n, main_vals)] + [self._tap_dict("aux", n, v, {}) for n, v in zip(aux_n, aux_vals)]
        names = await self._source_names(bare)
        main = [self._tap_dict("main", n, v, names) for n, v in zip(main_n, main_vals)]
        aux = [self._tap_dict("aux", n, v, names) for n, v in zip(aux_n, aux_vals)]
        for tap in main:
            tap["xlr"] = _xlr_carries_tap(out_blocks, tap["out"])
            tap["aes50a"] = _aes_channels_for_tap(aes_blocks, tap["out"])
        return {"main": main, "aux": aux, "routing": {"OUT": out_blocks, "AES50A": aes_blocks}}

    def _output_range(self, group: str) -> range:
        spec = self._d.param("outputs", f"{_OUTPUT_GROUPS[group][0]}/src")
        rng = spec.var_ranges.get(_OUTPUT_GROUPS[group][1])
        lo, hi = rng if isinstance(rng, tuple) else (1, 1)
        return range(lo, hi + 1)

    async def set_output(
        self, group: str, out: int, *, source: Any = None, pos: Any = None, invert: bool | None = None, tool: str = "set_output",
    ) -> dict[str, Any]:
        """Patch output tap ``out`` of ``group`` (``main`` XLR OUT 1-16 / ``aux`` AUX OUT 1-6):
        ``source`` in any spelling :func:`normalise_output_source` accepts, ``pos`` per
        :func:`normalise_output_pos`, ``invert`` polarity. Every argument is normalised before the
        first write so a bad one leaves the tap untouched. Guarded (``src``/``pos`` are Tier 2 —
        the confirming tools are ``set_output``/``set_aux_output``); ``invert`` is a Tier-1 parameter
        written on the same confirmed call."""
        spec_src, addr_src, g, n = self._output_spec(group, out, "src")
        writes: list[tuple[str, ParamSpec, str, Any]] = []
        if source is not None:
            tok = normalise_output_source(source, self._d.enum("output_src"))
            writes.append(("source", spec_src, addr_src, tok))
        if pos is not None:
            spec, addr, _g, _n = self._output_spec(g, n, "pos")
            writes.append(("pos", spec, addr, normalise_output_pos(pos, self._d.enum("output_pos"))))
        if invert is not None:
            if not isinstance(invert, bool):
                raise DeskError("BAD_ARGUMENT", f"invert must be true/false, got {invert!r}")
            spec, addr, _g, _n = self._output_spec(g, n, "invert")
            writes.append(("invert", spec, addr, bool(invert)))
        if not writes:
            raise DeskError("BAD_ARGUMENT", "nothing to set: give source, pos and/or invert")
        applied: dict[str, Any] = {}
        for key, spec, addr, value in writes:
            await self._write(addr, spec.to_raw(value), value=value, tool=tool, guarded=True)
            applied[key] = value
        return {"group": g, "out": n, "label": f"{_OUTPUT_GROUPS[g][2]} {n}", "address": self._output_node_path(g, n), "applied": applied}

    async def set_geq_band(self, fx_slot: int, side: str, band: int, gain_db: float, *, fx_type: str | None = None) -> None:
        """Raw GEQ write: ``gain_db`` (−15..+15, 0.5 dB grid) to 1-based ``band`` (1..31; 32 = the
        master) of ``side`` ``"A"``/``"B"`` (``"L"``/``"R"`` accepted) of the GEQ/TEQ in ``fx_slot``.
        Stereo types (GEQ/TEQ) use the same pars for both sides. Not a notch policy check — CFS²
        validates with ``policy.validate_notch`` before calling this. ``fx_type``, when the caller has
        already validated the slot (CFS² preflight), skips the ``/fx/N`` read: that read is a network
        round trip whenever the node cache has expired, i.e. a suspension point (and, on a lost
        datagram, timeout × retries) in the middle of the detect → cut path."""
        n = _int(fx_slot, "fx slot", 1, 8)
        b = _int(band, "band", 1, 32)
        s = str(side).strip().upper()
        if s in ("L", "A", "1"):
            s = "A"
        elif s in ("R", "B", "2"):
            s = "B"
        else:
            raise DeskError("BAD_ARGUMENT", f"side must be 'A' or 'B', got {side!r}")
        if fx_type is None:
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
