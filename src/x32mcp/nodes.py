"""``/node`` text lines: parse / render, desk-state dumps, snapshot files and diffs (DESIGN.md §9).

The desk answers ``/node ,s ch/01/mix`` with one text line in engineering units —
``/ch/01/mix ON  +2.1 ON +0 OFF   -oo`` — and accepts the same text back through the ``/``
write (docs/research/transport.md §6). This module owns that text form. Everything here is pure
and synchronous except :func:`dump_desk_state` (which only awaits ``conn.node_many``).

Ground truth is docs/research/transport.md §6.2–§6.6 (envelope, quoting, padding, token counts)
and docs/research/scales_params.md §0, §2.4, §13 (number formats, ``k`` notation, verbatim
console lines). Decisions where DESIGN.md is silent, or where the verified research overrides it:

* **Format vocabulary.** ``NODE_FORMATS`` is the 18-token set ``device.yaml`` actually uses
  (``str int sint onoff enum db1 db2 float1 float2 freq pct sfloat1 sig3 token bits6 bits8 bits9
  bits18``); DESIGN's ``float`` and ``hex`` are accepted as aliases of free-form float / bits
  parsing but unused. ``int`` is polymorphic: with an enum scale it is the enum *index* on the
  wire text (``config/source``, ``gate/keysrc``, ``-prefs/rta/source``), with a bool scale it is
  ``0``/``1`` (``panFollow``), with lin/log scales it is the rounded engineering value (HPF Hz,
  attack/release ms, RTA gain dB) and with an int scale a plain int.
* **Column padding.** The console right-aligns some numeric columns (research transport.md §6.3,
  VERIFIED from a console-saved scene file): levels (``db1``) in a 5-char field, ``sig3``
  (hold/mgain) in 4, release (``int`` on the ``ms_release`` scale) in 4, HPF (``int`` on ``hpf``)
  in 3, delay times (``float1`` on ``delay_ms``) in 5. :func:`render_node_line` reproduces this
  (``pad=True``, the default, so the fake desk looks real) and :func:`parse_node_line` splits on
  runs of whitespace so both padded and single-spaced text parse identically. The widths are
  cosmetic evidence from FW 2.x, not a contract — the parser never relies on them. The Q maximum
  prints as ``10`` (not ``10.0``) on the console; reproduced for the ``q`` scale.
* **Restore lines are single-spaced.** :func:`restore_plan` renders with ``pad=False``: the DOC's
  ``/`` examples are single-spaced and padded input to ``/`` is UNCONFIRMED on a real desk
  (transport.md §6.6). Tokens and values are identical to the desk's own text; only the column
  padding differs (deviation from the "bit-faithful" wording in DESIGN §9).
* **Parsing tolerance** (DESIGN §9): positional, missing trailing tokens → ``None``, extra tokens
  ignored (``/-show/showfile/show`` carries a trailing firmware string), any numeric token is read
  free-form (``0.03``, ``538``, ``8.00``, ``-0.0``, ``1k39``, ``-oo``). A token that cannot be
  read at all yields ``None`` and a DEBUG log unless ``strict=True`` (then
  :class:`NodeParseError`). Enum tokens unknown to the descriptor are kept verbatim.
* **The first token is the node path.** ``parse_node_line`` expects the line as the desk prints
  it / the ``/`` write accepts it (``/ch/01/mix …`` or ``ch/01/mix …``) and skips that token.
  Use :func:`split_node_line` to read the path out first.
* **Keys.** ``ParamSpec.relpath`` is a *template* (``mix/{send:02d}/level``), so both parse and
  render accept a :class:`~x32mcp.descriptor.NodePath` (whose ``fields`` are the concrete relpaths
  used as ``DeskState`` section keys) or a ``keys=`` sequence; with bare specs the keys default to
  ``spec.relpath``.
* **−∞ in JSON.** ``DeskState.to_json`` writes ``float('-inf')`` as the string ``"-oo"`` (and
  ``+inf`` as ``"+oo"``); ``from_json`` turns those strings back into floats for every section
  value. A *name* literally spelled ``-oo`` would therefore come back as −∞; the ``str``/``token``
  renderers print −∞ as ``-oo`` again and comparisons treat the two as equal, so nothing breaks,
  but it is a known ambiguity of the DESIGN-mandated encoding.
* **Diff semantics.** A field that is ``None`` on either side (older firmware printed fewer
  tokens) is never reported; floats compare with a 1e-6 tolerance; sections missing from either
  state are skipped. Labels use a true minus sign (U+2212) and ``→``.
* **Snapshot ids** are ``YYYYMMDD-HHMMSS[-slug]`` from the save time; a collision within the same
  second gets a ``-2``, ``-3`` … suffix. Files are ``<id>.json`` envelopes
  ``{"format", "id", "label", "created", "scene", "state"}``.
* Extra API beyond DESIGN §9: :func:`split_node_line`, :func:`unquote`, :func:`format_token` /
  :func:`parse_token` (single-field helpers), :class:`SnapshotMeta`, :class:`SnapshotError`,
  ``Change.section`` / ``Change.strip``, ``DeskState.to_dict`` / ``from_dict`` / ``addresses`` /
  ``items``, ``dump_desk_state(concurrency=…)``, ``render_node_line(pad=…)``,
  ``parse_node_line(keys=…, strict=…)``.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .descriptor import Descriptor, NodePath, ParamSpec
from .scales import LEVEL_MIN_DB, NEG_INF_DB, NEG_INF_TOKEN, ScaleError, enum_to_index, format_db, index_to_enum
from .targets import Target, TargetError, parse_target

__all__ = [
    "NODE_FORMATS",
    "NodeParseError",
    "tokenize",
    "unquote",
    "split_node_line",
    "parse_token",
    "format_token",
    "parse_node_line",
    "render_node_line",
    "DeskState",
    "dump_desk_state",
    "Snapshot",
    "SnapshotMeta",
    "SnapshotError",
    "SnapshotStore",
    "Change",
    "diff_states",
    "describe_changes",
    "restore_plan",
]

log = logging.getLogger(__name__)

NODE_FORMATS: frozenset[str] = frozenset({
    "str", "int", "sint", "onoff", "enum", "db1", "db2", "float1", "float2", "freq", "pct",
    "sfloat1", "sig3", "token", "bits6", "bits8", "bits9", "bits18",
    "float", "hex",  # DESIGN §9 names; unused by device.yaml, accepted as aliases
})

# Console column widths (transport.md §6.3, scales_params.md §13; FW 2.x scene file). Cosmetic only.
_PAD_BY_FORMAT: dict[str, int] = {"db1": 5, "sig3": 4}
_PAD_BY_SCALE: dict[str, int] = {"hpf": 3, "ms_release": 4, "delay_ms": 5}

_RESTORE_EXCLUDED_FAMILIES: frozenset[str] = frozenset({"show", "stat", "prefs", "action"})
_SECTION_RANK: dict[str, int] = {
    "config": 0, "delay": 1, "preamp": 2, "mix": 3, "send": 4, "eq": 5, "dyn": 6, "gate": 7,
    "insert": 8, "grp": 9, "automix": 10,
}
_FLOAT_TOL = 1e-6
_MINUS = "−"
_ARROW = "→"
_SNAPSHOT_FORMAT = "x32mcp-snapshot/1"
_FREQ_K = re.compile(r"^([+-]?\d*)k(\d*)$")
_BITS = re.compile(r"^%([01]+)$")


class NodeParseError(ValueError):
    """A node line (or one of its tokens) cannot be read or rendered."""


# -- helpers -------------------------------------------------------------------------------


def _round_half_away(v: float) -> int:
    a = abs(v)
    n = math.floor(a)
    if a - n >= 0.5:
        n += 1
    return n if v >= 0 else -n


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("on", "1", "true", "yes"):
            return True
        if s in ("off", "0", "false", "no", ""):
            return False
    raise NodeParseError(f"expected ON/OFF, got {v!r}")


def _parse_number(tok: str) -> float:
    """Free-form numeric token: ``-oo`` → −∞, ``1k39`` → 1390.0, else ``float(tok)``."""
    s = tok.strip()
    if s == NEG_INF_TOKEN or s == "-inf":
        return NEG_INF_DB
    if s in ("+oo", "oo", "inf", "+inf"):
        return math.inf
    try:
        f = float(s)
    except ValueError:
        m = _FREQ_K.match(s)
        if not m:
            raise NodeParseError(f"not a number: {tok!r}") from None
        return _parse_freq_k(m)
    if math.isnan(f):
        raise NodeParseError(f"not a number: {tok!r}")
    return f


def _parse_freq_k(m: re.Match[str]) -> float:
    # scales_params.md §0 (X32.c Xr_float): digits before k ×1000; after k: 1 digit ×100, 2 ×10, 3 ×1.
    a, b = m.group(1), m.group(2)
    v = float(int(a) if a not in ("", "+", "-") else 0) * 1000.0
    if b:
        if len(b) <= 3:
            v += int(b) * {1: 100.0, 2: 10.0, 3: 1.0}[len(b)]
        else:
            v += int(b) * 1000.0 / 10 ** len(b)
    return v


def _as_number(v: Any) -> float:
    """Engineering value (float/int/bool/str) → float for rendering."""
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        return _parse_number(v)
    raise NodeParseError(f"expected a number, got {v!r}")


def _format_freq(hz: float) -> str:
    """f201[] style: ``124.7`` below 1 kHz, ``1k02`` / ``20k00`` above (scales_params.md §13)."""
    if math.isinf(hz):
        return NEG_INF_TOKEN if hz < 0 else "+oo"
    if hz < 999.95:
        return f"{_round_half_away(hz * 10) / 10.0:.1f}"
    k, r = divmod(_round_half_away(hz / 10.0), 100)
    return f"{k}k{r:02d}"


def _format_sig3(v: float) -> str:
    """Console adaptive precision (research §4.4/§13): ``0.02`` ``2.00`` ``79.6`` ``14.0`` ``100`` ``502``."""
    if math.isinf(v):
        return NEG_INF_TOKEN if v < 0 else "+oo"
    if abs(v) < 9.995:
        return f"{_round_half_away(v * 100) / 100.0:.2f}"
    if abs(v) < 99.95:
        return f"{_round_half_away(v * 10) / 10.0:.1f}"
    return f"{_round_half_away(v):d}"


def _format_signed(v: float, decimals: int) -> str:
    """``%+.<decimals>f`` with an exact zero printed ``+0.0`` (never ``-0.0``); −∞ → ``-oo``."""
    if math.isinf(v):
        return NEG_INF_TOKEN if v < 0 else "+oo"
    scale = 10 ** decimals
    t = _round_half_away(v * scale)
    if t == 0:
        return f"{0.0:+.{decimals}f}"
    return f"{t / scale:+.{decimals}f}"


def _format_unsigned(v: float, decimals: int) -> str:
    if math.isinf(v):
        return NEG_INF_TOKEN if v < 0 else "+oo"
    scale = 10 ** decimals
    t = _round_half_away(v * scale)
    return f"{t / scale:.{decimals}f}"


def _bits_width(fmt: str) -> int:
    return int(fmt[4:]) if fmt.startswith("bits") and fmt[4:].isdigit() else 0


def _pad_width(spec: ParamSpec) -> int:
    w = _PAD_BY_FORMAT.get(spec.node_fmt, 0)
    if spec.scale_name in _PAD_BY_SCALE:
        w = max(w, _PAD_BY_SCALE[spec.scale_name])
    return w


def _coerce_number(spec: ParamSpec, x: float) -> Any:
    """Parsed numeric text → the engineering type the spec's scale uses."""
    k = spec.scale.kind
    if k == "enum":
        try:
            return index_to_enum(int(_round_half_away(x)), spec.scale.values)
        except (ScaleError, OverflowError, ValueError) as e:
            raise NodeParseError(str(e)) from None
    if k == "bool":
        return x != 0
    if k in ("int", "pan"):
        if math.isinf(x):
            raise NodeParseError(f"{spec.key}: infinite value in an int field")
        return _round_half_away(x)
    return x  # level / lin / log / str-ish numeric


# -- tokenizer -------------------------------------------------------------------------------


def tokenize(line: str) -> list[str]:
    """Split a node line on runs of whitespace, keeping ``"…"`` groups (which may contain spaces)
    as single tokens *with* their quotes; ``""`` stays ``'""'``. A trailing newline is dropped; an
    unterminated quote runs to the end of the line. No escape mechanism exists on the desk
    (transport.md §9.2)."""
    text = line.rstrip("\r\n")
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == '"':
            j = text.find('"', i + 1)
            j = n if j < 0 else j + 1
            while j < n and not text[j].isspace():  # glued text after the closing quote
                j += 1
            out.append(text[i:j])
            i = j
        else:
            j = i
            while j < n and not text[j].isspace():
                j += 1
            out.append(text[i:j])
            i = j
    return out


def unquote(tok: str) -> str:
    """Strip one pair of surrounding double quotes (``'"Kick Drum"'`` → ``'Kick Drum'``)."""
    if len(tok) >= 2 and tok[0] == '"' and tok[-1] == '"':
        return tok[1:-1]
    if tok.startswith('"'):  # unterminated
        return tok[1:]
    return tok


def split_node_line(line: str) -> tuple[str, list[str]]:
    """``'/ch/01/config "Vox" 1 CY 1'`` → ``('/ch/01/config', ['"Vox"', '1', 'CY', '1'])``.
    The path is normalised to start with ``/`` (the ``/`` write accepts it without)."""
    toks = tokenize(line)
    if not toks:
        raise NodeParseError("empty node line")
    path = unquote(toks[0])
    if not path.startswith("/"):
        path = "/" + path
    return path, toks[1:]


# -- single tokens -----------------------------------------------------------------------------


def parse_token(spec: ParamSpec, tok: str) -> Any:
    """One node-text token → engineering value per ``spec.node_fmt`` (see module doc for the
    format vocabulary). Raises :class:`NodeParseError` on an unreadable token."""
    fmt = spec.node_fmt
    if fmt == "str":
        return unquote(tok)
    if fmt == "token":
        return tok
    if fmt == "onoff":
        return _as_bool(unquote(tok))
    if fmt == "enum":
        t = unquote(tok)
        values = spec.enum or spec.scale.values
        if values:
            if t in values:
                return t
            low = t.lower()
            for v in values:
                if v.lower() == low:
                    return v
        return t  # unknown to the descriptor (newer firmware?) — keep verbatim
    if fmt.startswith("bits") or fmt == "hex":
        t = unquote(tok)
        m = _BITS.match(t)
        if m:
            return int(m.group(1), 2)
        if fmt == "hex" and re.fullmatch(r"0[xX][0-9a-fA-F]+", t):
            return int(t, 16)
        try:  # X32.c XslashSetPerInt also accepts a plain decimal int
            return int(t, 10)
        except ValueError:
            raise NodeParseError(f"{spec.key}: bad bitmap {tok!r}") from None
    if fmt == "int" and spec.scale.kind == "bool":
        # panFollow: 0/1 per the emulator / SetSceneParse, OFF/ON per the PDF wording — UNCONFIRMED
        # on a FW 4.x desk (scales_params.md §4.9), so accept either spelling.
        return _as_bool(unquote(tok))
    x = _parse_number(unquote(tok))
    if fmt == "sint" and spec.scale.kind not in ("int", "pan", "enum", "bool"):
        return x
    return _coerce_number(spec, x)


def format_token(spec: ParamSpec, value: Any, *, pad: bool = True) -> str:
    """Engineering value → the desk's node-text token for ``spec`` (right-padded to the console
    column width when ``pad``). Raises :class:`NodeParseError` for a value the format cannot carry."""
    fmt = spec.node_fmt
    kind = spec.scale.kind
    try:
        if fmt == "str":
            if isinstance(value, float) and math.isinf(value):
                value = NEG_INF_TOKEN if value < 0 else "+oo"
            return f'"{"" if value is None else value}"'
        if fmt == "token":
            if isinstance(value, float) and math.isinf(value):
                return NEG_INF_TOKEN if value < 0 else "+oo"
            if isinstance(value, bool):
                return "ON" if value else "OFF"
            if isinstance(value, float) and value.is_integer():
                return str(int(value))
            return str(value)
        if fmt == "onoff":
            return "ON" if _as_bool(value) else "OFF"
        if fmt == "enum":
            values = spec.enum or spec.scale.values
            if values:
                return values[enum_to_index(value, values)]
            return str(value)
        if fmt.startswith("bits"):
            width = _bits_width(fmt)
            iv = int(_as_number(value))
            if iv < 0:
                raise NodeParseError(f"{spec.key}: negative bitmap {value!r}")
            return "%" + format(iv, f"0{width}b")
        if fmt == "hex":
            return f"0x{int(_as_number(value)):x}"
        if fmt == "int":
            if kind == "enum":
                text = str(enum_to_index(value, spec.scale.values))
            elif kind == "bool":
                text = "1" if _as_bool(value) else "0"
            else:
                x = _as_number(value)
                if math.isinf(x):
                    raise NodeParseError(f"{spec.key}: cannot print {value!r} as an int")
                text = f"{_round_half_away(x):d}"
        elif fmt == "sint":
            x = _as_number(value)
            text = f"{_round_half_away(x):+d}"
        elif fmt == "db1":
            x = _as_number(value)
            # scales_params.md §2.3: −90 dB has no row of its own — the desk stores 0.0 and prints -oo
            text = format_db(NEG_INF_DB if x <= LEVEL_MIN_DB else x)
        elif fmt == "db2":
            text = _format_signed(_as_number(value), 2)
        elif fmt == "sfloat1":
            text = _format_signed(_as_number(value), 1)
        elif fmt == "float1":
            x = _as_number(value)
            # console prints the Q maximum as "10" (scales_params.md §4.7)
            text = "10" if spec.scale_name == "q" and abs(x - 10.0) < 0.05 else _format_unsigned(x, 1)
        elif fmt == "float2":
            text = _format_unsigned(_as_number(value), 2)
        elif fmt == "float":
            text = repr(float(_as_number(value)))
        elif fmt == "pct":
            text = f"{_round_half_away(_as_number(value)):d}"
        elif fmt == "sig3":
            text = _format_sig3(_as_number(value))
        elif fmt == "freq":
            text = _format_freq(_as_number(value))
        else:
            raise NodeParseError(f"{spec.key}: unknown node format {fmt!r}")
    except ScaleError as e:
        raise NodeParseError(f"{spec.key}: {e}") from None
    if pad:
        w = _pad_width(spec)
        if w:
            text = text.rjust(w)
    return text


# -- lines -----------------------------------------------------------------------------------


def _fields_and_keys(fields: Sequence[ParamSpec] | NodePath, keys: Sequence[str] | None) -> tuple[tuple[ParamSpec, ...], tuple[str, ...]]:
    if isinstance(fields, NodePath):
        specs, default_keys = fields.specs, fields.fields
    else:
        specs = tuple(fields)
        default_keys = tuple(s.relpath for s in specs)
    if keys is None:
        keys = default_keys
    keys = tuple(keys)
    if len(keys) != len(specs):
        raise NodeParseError(f"{len(keys)} keys for {len(specs)} fields")
    return specs, keys


def parse_node_line(
    line: str,
    fields: Sequence[ParamSpec] | NodePath,
    *,
    keys: Sequence[str] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Node line (path first, as the desk prints it) → ``{key: engineering value}``.

    ``fields`` are the specs in desk print order (or a :class:`NodePath`, whose concrete field
    relpaths become the keys); ``keys`` overrides the result keys. Positional and tolerant:
    missing trailing tokens → ``None``, extra tokens ignored, padded or single-spaced text alike.
    An unreadable token → ``None`` (DEBUG log), or :class:`NodeParseError` when ``strict``.
    Levels use ``-oo`` for −∞; ``mix/on`` etc. come back as ``True`` = channel ON (the
    ``inverted_mute`` flag is *not* applied here — ``Desk`` does that).
    """
    specs, keys = _fields_and_keys(fields, keys)
    _path, toks = split_node_line(line)
    out: dict[str, Any] = {}
    for i, (spec, key) in enumerate(zip(specs, keys)):
        if i >= len(toks):
            out[key] = None
            continue
        try:
            out[key] = parse_token(spec, toks[i])
        except NodeParseError as e:
            if strict:
                raise NodeParseError(f"{_path} token {i + 1} ({key}): {e}") from None
            log.debug("%s token %d (%s): %s", _path, i + 1, key, e)
            out[key] = None
    if len(toks) > len(specs):
        log.debug("%s: %d extra token(s) ignored: %r", _path, len(toks) - len(specs), toks[len(specs):])
    return out


def render_node_line(
    path: str,
    fields: Sequence[ParamSpec] | NodePath,
    values: Mapping[str, Any],
    *,
    keys: Sequence[str] | None = None,
    pad: bool = True,
) -> str:
    """``{key: engineering value}`` → the desk-style line ``'/ch/01/mix ON  +2.1 ON +0 OFF   -oo'``.

    Renders the leading run of fields that have a value; a missing / ``None`` value ends the line
    (partial trailing lists are what the ``/`` write accepts, transport.md §6.6). A value *after*
    a hole cannot be expressed positionally and raises :class:`NodeParseError`. ``pad`` reproduces
    the console's right-aligned columns (see module doc); ``pad=False`` single-spaces everything.
    Values may also be keyed by ``spec.relpath`` (template) as a fallback.
    """
    specs, keys = _fields_and_keys(fields, keys)
    p = path.strip()
    if not p.startswith("/"):
        p = "/" + p
    toks: list[str] = [p]
    stopped_at: str | None = None
    for spec, key in zip(specs, keys):
        v = values[key] if key in values else values.get(spec.relpath)
        if v is None:
            if stopped_at is None:
                stopped_at = key
            continue
        if stopped_at is not None:
            raise NodeParseError(f"{p}: cannot render {key!r} after missing field {stopped_at!r}")
        toks.append(format_token(spec, v, pad=pad))
    return " ".join(toks)


# -- desk state ------------------------------------------------------------------------------


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, float):
        if math.isinf(obj):
            return NEG_INF_TOKEN if obj < 0 else "+oo"
        if math.isnan(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def _unjson_value(v: Any) -> Any:
    if v == NEG_INF_TOKEN:
        return NEG_INF_DB
    if v == "+oo":
        return math.inf
    return v


def _root_of(section_path: str, field_rel: str) -> str:
    """Family root such that ``root + '/' + field`` is the leaf address under ``section_path``.

    The field relpath repeats the trailing segments of the node path (``/ch/05/mix/03`` +
    ``mix/03/level``); the longest such overlap wins. With no overlap the root is the node path
    itself (``/dca/1`` + ``on``, ``/headamp/005`` + ``gain``).
    """
    segs = section_path.strip("/").split("/")
    fsegs = field_rel.split("/")
    for k in range(min(len(segs), len(fsegs)), 0, -1):
        if segs[-k:] == fsegs[:k]:
            return "/" + "/".join(segs[:-k])
    return section_path


@dataclass
class DeskState:
    """One full (or scoped) ``/node`` sweep in engineering units; JSON-serialisable.

    ``sections`` maps node path → ``{field relpath: value}`` (``"/ch/01/mix" → {"mix/on": True,
    "mix/fader": -12.0, …}``; keys are the :class:`~x32mcp.descriptor.NodePath` fields, values dB /
    Hz / ms / % floats, enum tokens, bools, strs, ints for bitmaps; ``None`` for a token the
    desk did not print). ``mix/on: True`` means the channel is ON — mute inversion is *not*
    applied here. ``missing`` lists node paths that timed out or could not be parsed.
    """

    created: str  # ISO 8601 with offset
    console: dict[str, Any] = field(default_factory=dict)
    scene: dict[str, Any] | None = None  # {"index": int, "name": str}
    sections: dict[str, dict[str, Any]] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    _index: dict[str, tuple[str, str]] | None = field(default=None, init=False, repr=False, compare=False)

    # -- serialisation -----------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "created": self.created,
            "console": _jsonable(self.console),
            "scene": _jsonable(self.scene),
            "sections": _jsonable(self.sections),
            "missing": list(self.missing),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DeskState":
        if not isinstance(data, Mapping):
            raise NodeParseError(f"desk state must be a mapping, got {type(data).__name__}")
        raw_sections = data.get("sections") or {}
        sections: dict[str, dict[str, Any]] = {}
        for path, sec in raw_sections.items():
            if not isinstance(sec, Mapping):
                raise NodeParseError(f"section {path!r} must be a mapping")
            sections[str(path)] = {str(k): _unjson_value(v) for k, v in sec.items()}
        return cls(
            created=str(data.get("created", "")),
            console=dict(data.get("console") or {}),
            scene=dict(data["scene"]) if data.get("scene") else None,
            sections=sections,
            missing=[str(m) for m in data.get("missing") or []],
        )

    def to_json(self) -> str:
        """JSON text; −∞ is written as ``"-oo"`` (round-trips through :meth:`from_json`)."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=1)

    @classmethod
    def from_json(cls, s: str) -> "DeskState":
        try:
            data = json.loads(s)
        except json.JSONDecodeError as e:
            raise NodeParseError(f"invalid desk-state JSON: {e}") from None
        return cls.from_dict(data)

    # -- access -------------------------------------------------------------------------------
    def _build_index(self) -> dict[str, tuple[str, str]]:
        idx: dict[str, tuple[str, str]] = {}
        for path, sec in self.sections.items():
            for f in sec:
                idx[f"{_root_of(path, f)}/{f}"] = (path, f)
        self._index = idx
        return idx

    def _lookup(self, address: str) -> tuple[str, str] | None:
        idx = self._index if self._index is not None else self._build_index()
        hit = idx.get(address)
        if hit is None or hit[0] not in self.sections or hit[1] not in self.sections[hit[0]]:
            idx = self._build_index()  # sections were edited since the index was built
            hit = idx.get(address)
        return hit

    def get(self, address: str, default: Any = None) -> Any:
        """Leaf value by OSC address (``/ch/01/mix/fader`` → −12.0), ``default`` when unknown."""
        hit = self._lookup(address)
        if hit is None:
            return default
        return self.sections[hit[0]].get(hit[1], default)

    def __contains__(self, address: str) -> bool:
        return self._lookup(address) is not None

    def addresses(self) -> list[str]:
        """Every leaf address the state holds, in section order."""
        return list(self._build_index())

    def items(self) -> Iterator[tuple[str, Any]]:
        """``(address, value)`` pairs in section order."""
        for addr, (path, f) in self._build_index().items():
            yield addr, self.sections[path][f]

    def section_count(self) -> int:
        return len(self.sections)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _family_prefix(d: Descriptor, family: str) -> str:
    tmpl = d.root_template(family)
    return tmpl.split("{", 1)[0].rstrip("/") or "/"


def _scope_prefixes(d: Descriptor, scope: Any) -> tuple[str, ...] | None:
    """``None`` (everything) or the address prefixes a scope selects: a node/address prefix
    (``/ch/05``), a target key (``ch.5``, ``bus 3``), a :class:`Target`, or a family name
    (``ch``, ``headamp``, ``config``). Raises ``ValueError`` for anything else."""
    if scope is None:
        return None
    if isinstance(scope, Target):
        return (scope.osc_prefix,)
    if isinstance(scope, (list, tuple, set, frozenset)):
        out: list[str] = []
        for s in scope:
            out.extend(_scope_prefixes(d, s) or ())
        return tuple(out)
    if not isinstance(scope, str):
        raise ValueError(f"bad scope {scope!r}")
    s = scope.strip()
    if not s:
        return None
    if s.startswith("/"):
        return (s.rstrip("/") or "/",)
    if s in d.families:
        return (_family_prefix(d, s),)
    try:
        return (parse_target(s).osc_prefix,)
    except TargetError:
        raise ValueError(f"unknown scope {scope!r}; use a target (ch.5), a family (bus) or a path (/ch/05)") from None


def _in_scope(path: str, prefixes: tuple[str, ...] | None) -> bool:
    if prefixes is None:
        return True
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def _scene_of(state: DeskState) -> dict[str, Any] | None:
    idx = state.get("/-show/prepos/current")
    if not isinstance(idx, int) or isinstance(idx, bool):
        return None
    name = state.get(f"/-show/showfile/scene/{idx:03d}/name")
    return {"index": idx, "name": "" if name is None else str(name)}


async def dump_desk_state(
    conn: Any,
    d: Descriptor,
    *,
    sections: Iterable[str] | None = None,
    concurrency: int = 16,
) -> DeskState:
    """Sweep every node path of ``d`` (or those under ``sections``: paths, target keys or family
    names, see :func:`_scope_prefixes`) through ``conn.node_many(paths, concurrency)`` and parse the
    replies. ``conn`` is duck-typed: anything with ``async node_many(paths, concurrency) ->
    dict[path, line | None]``; a ``status.console`` (``ConsoleInfo``) is recorded when present.
    Never raises for a bad or missing reply — those paths land in ``DeskState.missing``.
    """
    prefixes = None if sections is None else tuple(p for s in sections for p in (_scope_prefixes(d, s) or ()))
    nodes = [n for n in d.nodes() if _in_scope(n.path, prefixes)]
    paths = [n.path for n in nodes]
    t0 = time.monotonic()
    replies: Mapping[str, str | None] = await conn.node_many(paths, concurrency) if paths else {}
    state = DeskState(created=_now_iso(), console=_console_dict(conn))
    for node in nodes:
        line = replies.get(node.path)
        if line is None:
            state.missing.append(node.path)
            continue
        try:
            state.sections[node.path] = parse_node_line(line, node)
        except NodeParseError as e:
            log.warning("node %s: unparsable reply %r: %s", node.path, line, e)
            state.missing.append(node.path)
    state.scene = _scene_of(state)
    log.info(
        "desk state dumped: %d/%d nodes in %.2f s (%d missing)",
        len(state.sections), len(paths), time.monotonic() - t0, len(state.missing),
    )
    return state


def _console_dict(conn: Any) -> dict[str, Any]:
    status = getattr(conn, "status", None)
    console = getattr(status, "console", None) if status is not None else None
    if console is None:
        return {}
    to_dict = getattr(console, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    if isinstance(console, Mapping):
        return dict(console)
    return {k: v for k, v in vars(console).items() if not k.startswith("_")}


# -- snapshots -------------------------------------------------------------------------------


class SnapshotError(LookupError):
    """Unknown or ambiguous snapshot id/prefix, or an unreadable snapshot file."""


@dataclass(frozen=True)
class SnapshotMeta:
    id: str
    label: str
    created: str
    scene: dict[str, Any] | None
    size: int  # file size in bytes
    path: Path


@dataclass
class Snapshot:
    id: str
    label: str
    path: Path
    created: str
    state: DeskState

    @property
    def scene(self) -> dict[str, Any] | None:
        return self.state.scene


def _slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return s[:40].rstrip("-")


class SnapshotStore:
    """Desk-state snapshots as ``<id>.json`` files in ``dir`` (``Settings.snapshot_dir``)."""

    def __init__(self, dir: Path | str) -> None:
        self.dir = Path(dir)

    def save(self, state: DeskState, label: str = "") -> Snapshot:
        """Write ``state`` as ``YYYYMMDD-HHMMSS[-slug].json`` (slug from ``label``) and return it."""
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        slug = _slug(label)
        base = f"{stamp}-{slug}" if slug else stamp
        sid, path = base, self.dir / f"{base}.json"
        n = 2
        while path.exists():
            sid = f"{base}-{n}"
            path = self.dir / f"{sid}.json"
            n += 1
        created = state.created or _now_iso()
        envelope = {
            "format": _SNAPSHOT_FORMAT, "id": sid, "label": label, "created": created,
            "scene": _jsonable(state.scene), "state": state.to_dict(),
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(envelope, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        log.info("snapshot %s saved (%d sections, %d missing) label=%r", sid, len(state.sections), len(state.missing), label)
        return Snapshot(id=sid, label=label, path=path, created=created, state=state)

    def _read_envelope(self, path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise SnapshotError(f"cannot read snapshot {path.name}: {e}") from None
        if not isinstance(data, dict) or "state" not in data:
            raise SnapshotError(f"{path.name} is not a snapshot file")
        return data

    def list(self) -> list[SnapshotMeta]:
        """All snapshots, newest first (unreadable files are skipped with a warning)."""
        out: list[SnapshotMeta] = []
        if not self.dir.is_dir():
            return out
        for path in self.dir.glob("*.json"):
            try:
                env = self._read_envelope(path)
            except SnapshotError as e:
                log.warning("%s", e)
                continue
            out.append(SnapshotMeta(
                id=str(env.get("id") or path.stem), label=str(env.get("label") or ""),
                created=str(env.get("created") or ""), scene=env.get("scene"),
                size=path.stat().st_size, path=path,
            ))
        out.sort(key=lambda m: (m.id, m.created), reverse=True)
        return out

    def _load_path(self, path: Path) -> Snapshot:
        env = self._read_envelope(path)
        return Snapshot(
            id=str(env.get("id") or path.stem), label=str(env.get("label") or ""), path=path,
            created=str(env.get("created") or ""), state=DeskState.from_dict(env["state"]),
        )

    def load(self, id_or_prefix: str) -> Snapshot:
        """By exact id, unique id prefix, or ``"latest"``. Raises :class:`SnapshotError`."""
        key = (id_or_prefix or "").strip()
        if not key:
            raise SnapshotError("empty snapshot id")
        if key.lower() == "latest":
            snap = self.latest()
            if snap is None:
                raise SnapshotError(f"no snapshots in {self.dir}")
            return snap
        metas = self.list()
        exact = [m for m in metas if m.id == key]
        if exact:
            return self._load_path(exact[0].path)
        matches = [m for m in metas if m.id.startswith(key)]
        if len(matches) == 1:
            return self._load_path(matches[0].path)
        if not matches:
            raise SnapshotError(f"no snapshot matches {key!r}")
        raise SnapshotError(f"{key!r} is ambiguous: " + ", ".join(m.id for m in matches[:8]))

    def latest(self) -> Snapshot | None:
        metas = self.list()
        return self._load_path(metas[0].path) if metas else None


# -- diff ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    """One leaf that differs. ``label`` is the English sentence (``Ch 5 'Vox Tony' fader −4.2 dB →
    −1.0 dB``); ``section`` is the node path and ``strip`` the grouping key (``ch.5``,
    ``headamp``, ``config``)."""

    address: str
    field: str
    before: Any
    after: Any
    label: str
    section: str = ""
    strip: str = ""


def _norm(v: Any) -> Any:
    if isinstance(v, str) and v == NEG_INF_TOKEN:
        return NEG_INF_DB
    return v


def _values_equal(a: Any, b: Any) -> bool:
    a, b = _norm(a), _norm(b)
    if a is None or b is None:
        return True  # unknown on one side (fewer tokens on that firmware) is not a change
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=0.0, abs_tol=_FLOAT_TOL)
    return a == b


def _minus(text: str) -> str:
    return text.replace("-", _MINUS)


def _fmt_hz(hz: float) -> str:
    if hz < 1000:
        return f"{_round_half_away(hz * 10) / 10.0:.1f} Hz".replace(".0 Hz", " Hz")
    k = f"{hz / 1000.0:.2f}".rstrip("0").rstrip(".")
    return f"{k} kHz"


def _fmt_bits(v: Any, width: int) -> str:
    try:
        iv = int(v)
    except (TypeError, ValueError):
        return str(v)
    members = [str(i + 1) for i in range(max(width, iv.bit_length())) if iv >> i & 1]
    return "{" + ", ".join(members) + "}" if members else "none"


def _fmt_value(spec: ParamSpec, v: Any) -> str:
    """Engineering value → display text for a diff label (units, true minus, −∞)."""
    v = _norm(v)
    if v is None:
        return "?"
    fmt = spec.node_fmt
    unit = spec.unit
    unit_sfx = (" " + unit) if unit else ""
    if fmt == "str":
        return f"'{v}'"
    if fmt == "onoff" or spec.scale.kind == "bool":
        b = _as_bool(v)
        if spec.inverted_mute:
            return "unmuted" if b else "muted"
        return "on" if b else "off"
    if fmt == "enum" or spec.scale.kind == "enum":
        if isinstance(v, str):
            return v
        try:
            return index_to_enum(v, spec.scale.values)
        except ScaleError:
            return str(v)
    if fmt.startswith("bits"):
        return _fmt_bits(v, _bits_width(fmt))
    if fmt == "token":
        return str(v)
    if fmt == "freq":
        return _fmt_hz(_as_number(v))
    x = _as_number(v)
    if math.isinf(x):
        return (_MINUS + "∞" if x < 0 else "+∞") + unit_sfx
    if fmt == "db1":
        return _minus(format_db(x)) + unit_sfx
    if fmt == "db2":
        text = _format_signed(x, 2)
        if text.endswith("0"):
            text = text[:-1]
        return _minus(text) + unit_sfx
    if fmt == "sfloat1":
        return _minus(_format_signed(x, 1)) + unit_sfx
    if fmt == "float1":
        return _minus(_format_unsigned(x, 1)) + unit_sfx
    if fmt == "float2":
        return _minus(_format_unsigned(x, 2)) + unit_sfx
    if fmt == "sig3":
        return _minus(_format_sig3(x)) + unit_sfx
    if fmt == "sint":
        return _minus(f"{_round_half_away(x):+d}")
    if fmt in ("int", "pct"):
        return _minus(f"{_round_half_away(x):d}") + unit_sfx
    return _minus(str(v)) + unit_sfx


# field template → phrase (``{band}`` rendered with the node vars). Sentinels for the special forms.
_MUTE, _SEND_LEVEL, _SEND_MUTE = object(), object(), object()
_PHRASES: dict[str, Any] = {
    "config/name": "name", "config/icon": "icon", "config/color": "colour", "config/source": "source",
    "delay/on": "delay", "delay/time": "delay time",
    "preamp/trim": "trim", "preamp/invert": "polarity invert", "preamp/hpon": "HPF",
    "preamp/hpslope": "HPF slope", "preamp/hpf": "HPF",
    "gate/on": "gate", "gate/mode": "gate mode", "gate/thr": "gate threshold", "gate/range": "gate range",
    "gate/attack": "gate attack", "gate/hold": "gate hold", "gate/release": "gate release",
    "gate/keysrc": "gate key source", "gate/filter/on": "gate filter", "gate/filter/type": "gate filter type",
    "gate/filter/f": "gate filter freq",
    "dyn/on": "comp", "dyn/mode": "comp mode", "dyn/det": "comp detector", "dyn/env": "comp envelope",
    "dyn/thr": "comp threshold", "dyn/ratio": "comp ratio", "dyn/knee": "comp knee", "dyn/mgain": "comp makeup",
    "dyn/attack": "comp attack", "dyn/hold": "comp hold", "dyn/release": "comp release", "dyn/pos": "comp position",
    "dyn/keysrc": "comp key source", "dyn/mix": "comp mix", "dyn/auto": "comp auto", "dyn/filter/on": "comp filter",
    "dyn/filter/type": "comp filter type", "dyn/filter/f": "comp filter freq",
    "insert/on": "insert", "insert/pos": "insert position", "insert/sel": "insert",
    "eq/on": "EQ", "eq/{band}/type": "EQ band {band} type", "eq/{band}/f": "EQ band {band} freq",
    "eq/{band}/g": "EQ band {band} gain", "eq/{band}/q": "EQ band {band} Q",
    "mix/on": _MUTE, "mix/fader": "fader", "mix/st": "LR assign", "mix/pan": "pan", "mix/mono": "M/C assign",
    "mix/mlevel": "M/C level",
    "mix/{send:02d}/on": _SEND_MUTE, "mix/{send:02d}/level": _SEND_LEVEL, "mix/{send:02d}/pan": "pan",
    "mix/{send:02d}/type": "tap", "mix/{send:02d}/panFollow": "pan follow",
    "grp/dca": "DCA groups", "grp/mute": "mute groups",
    "automix/group": "automix group", "automix/weight": "automix weight",
    "on": _MUTE, "fader": "fader",  # dca
    "gain": "gain", "phantom": "phantom",  # headamp
    "type": "type", "source/l": "source L", "source/r": "source R",  # fx
    "prepos/current": "current scene", "name": "name",
}
_FAMILY_LABELS = {"headamp": "Headamp", "fx": "FX", "config": "Config", "show": "Show", "stat": "Status", "prefs": "Prefs", "action": "Action"}
_GENERIC_FAMILIES = frozenset({"config", "show", "stat", "prefs", "action"})  # labelled "<Family> <relpath> a → b"


def _strip_label(d: Descriptor, node: NodePath, states: Sequence[DeskState]) -> str:
    """``Ch 5 'Vox Tony'`` / ``Bus 3`` / ``Headamp 005`` / ``FX 2`` / ``Config``."""
    t = node.target
    if t is not None:
        name = _name_of(t, states)
        return f"{t.label} '{name}'" if name else t.label
    fam = _FAMILY_LABELS.get(node.family, node.family.capitalize())
    n = node.vars.get("n")
    if node.family == "headamp" and n is not None:
        return f"{fam} {n:03d}"
    if n is not None:
        return f"{fam} {n}"
    return fam


def _name_of(t: Target, states: Sequence[DeskState]) -> str:
    for st in states:
        name = st.get(f"{t.osc_prefix}/config/name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return ""


def _send_target(d: Descriptor, node: NodePath) -> Target | None:
    send = node.vars.get("send")
    if send is None:
        return None
    fam = d.strips.get(node.family)
    dest = (fam.send_target if fam and fam.send_target else "bus")
    try:
        return Target(dest, int(send))
    except TargetError:
        return None


def _describe(d: Descriptor, node: NodePath, spec: ParamSpec, field_key: str, before: Any, after: Any, states: Sequence[DeskState]) -> str:
    subject = _strip_label(d, node, states)
    phrase = _PHRASES.get(spec.relpath)
    b, a = _fmt_value(spec, before), _fmt_value(spec, after)
    if node.family in _GENERIC_FAMILIES:
        rel = field_key
        m = re.fullmatch(r"mute/(\d+)", rel)
        if node.family == "config" and m:
            rel = f"mute group {m.group(1)}"
        elif node.family == "show" and rel.startswith("showfile/scene/"):
            rel = "scene " + rel[len("showfile/scene/"):].replace("/", " ")
        elif node.family == "show" and rel == "prepos/current":
            rel = "current scene"
        elif node.family == "show" and rel.startswith("showfile/show/"):
            rel = "show " + rel[len("showfile/show/"):]
        elif node.family == "prefs" and rel.startswith("rta/"):
            rel = "RTA " + rel[4:]
        return f"{subject} {rel} {b} {_ARROW} {a}"
    if phrase is _MUTE:
        return f"{subject} {b} {_ARROW} {a}"
    if phrase is _SEND_LEVEL or phrase is _SEND_MUTE or (spec.relpath.startswith("mix/{send:02d}/")):
        dest = _send_target(d, node)
        head = f"{_strip_label_for(dest, states)} send from {subject}" if dest else f"{subject} send {node.vars.get('send')}"
        if phrase is _SEND_LEVEL:
            delta = ""
            if isinstance(before, (int, float)) and isinstance(after, (int, float)) and not isinstance(before, bool) \
                    and math.isfinite(before) and math.isfinite(after):
                delta = f" ({_minus(f'{after - before:+.1f}')} dB)"
            return f"{head} {b} {_ARROW} {a}{delta}"
        if phrase is _SEND_MUTE:
            return f"{head} {b} {_ARROW} {a}"
        return f"{head} {phrase} {b} {_ARROW} {a}"
    if node.family == "fx" and spec.relpath.startswith("par/"):
        phrase = f"par {int(spec.relpath[4:])}"
    if phrase is None:
        phrase = field_key
    else:
        try:
            phrase = phrase.format(**node.vars)
        except (KeyError, ValueError):
            pass
    return f"{subject} {phrase} {b} {_ARROW} {a}"


def _strip_label_for(t: Target, states: Sequence[DeskState]) -> str:
    name = _name_of(t, states)
    return f"{t.label} '{name}'" if name else t.label


def _strip_key(node: NodePath) -> str:
    t = node.target
    if t is not None:
        return t.key
    n = node.vars.get("n")
    return f"{node.family}.{n}" if n is not None else node.family


def diff_states(a: DeskState, b: DeskState, d: Descriptor, *, scope: Any = None) -> list[Change]:
    """Leaves whose value differs between ``a`` (before) and ``b`` (after), in sweep order.
    ``scope`` narrows to a target key (``ch.5``), a family (``bus``) or a path prefix (``/ch/05``).
    Sections absent from either state and fields ``None`` on either side are skipped."""
    prefixes = _scope_prefixes(d, scope)
    states = (b, a)
    out: list[Change] = []
    for node in d.nodes():
        if not _in_scope(node.path, prefixes):
            continue
        sa, sb = a.sections.get(node.path), b.sections.get(node.path)
        if sa is None or sb is None:
            continue
        for key, spec in node:
            va, vb = sa.get(key), sb.get(key)
            if _values_equal(va, vb):
                continue
            addr = f"{node.root}/{key}"
            out.append(Change(
                address=addr, field=key, before=va, after=vb,
                label=_describe(d, node, spec, key, va, vb, states), section=node.path, strip=_strip_key(node),
            ))
    return out


def describe_changes(changes: Sequence[Change]) -> str:
    """Bullet list, one line per change, grouped by strip (first-seen order). Empty → ``No changes.``"""
    if not changes:
        return "No changes."
    order: dict[str, int] = {}
    for c in changes:
        order.setdefault(c.strip, len(order))
    grouped = sorted(changes, key=lambda c: order[c.strip])
    return "\n".join(f"- {c.label}" for c in grouped)


# -- restore ---------------------------------------------------------------------------------


def _section_kind(node: NodePath) -> str:
    rel = node.path[len(node.root):].strip("/")
    if not rel:
        return "mix"  # /dca/N (on fader), /headamp/NNN, /fx/N
    head = rel.split("/", 1)[0]
    if head == "mix" and "/" in rel:
        return "send"
    return head


def _restore_sort_key(d: Descriptor, node: NodePath, sweep_index: int) -> tuple[int, int, int, int]:
    fam_order = list(d.families)
    fam_i = fam_order.index(node.family) if node.family in fam_order else len(fam_order)
    strip_i = 0
    st = d.strips.get(node.family)
    if st is not None:
        idx = node.vars.get("id", node.vars.get("n"))
        try:
            strip_i = list(st.indices).index(idx)
        except ValueError:
            strip_i = 0
        rank = _SECTION_RANK.get(_section_kind(node), 50)
    else:
        rank = 0
    return (fam_i, strip_i, rank, sweep_index)


def _leading_run(values: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in keys:
        v = values.get(k)
        if v is None:
            break
        out[k] = v
    return out


def restore_plan(target_state: DeskState, live: DeskState, d: Descriptor, *, scope: Any = None) -> list[str]:
    """Node-style write lines (``/ ,s`` form, transport.md §6.6) that bring ``live`` to
    ``target_state``: one line per *section* that differs (or is missing from ``live``), rendered
    from the snapshot's values. ``-show``/``-stat``/``-prefs``/``-action`` sections are never
    restored. Order: per strip config, delay, preamp, mix, sends, eq, dyn, gate, insert, grp;
    families ch, auxin, fxrtn, bus, mtx, main, dca, headamp, fx, config. Lines are single-spaced
    (see module doc). A section whose snapshot values have a hole is written up to the hole
    (WARNING logged)."""
    prefixes = _scope_prefixes(d, scope)
    picked: list[tuple[tuple[int, int, int, int], NodePath, dict[str, Any]]] = []
    for i, node in enumerate(d.nodes()):
        if node.family in _RESTORE_EXCLUDED_FAMILIES or not _in_scope(node.path, prefixes):
            continue
        want = target_state.sections.get(node.path)
        if want is None:
            continue
        have = live.sections.get(node.path)
        if have is not None and all(_values_equal(want.get(k), have.get(k)) for k in node.fields):
            continue
        run = _leading_run(want, node.fields)
        if not run:
            continue
        if len(run) < sum(1 for k in node.fields if want.get(k) is not None):
            log.warning("restore %s: snapshot has a hole after %r; writing %d of %d fields", node.path, list(run)[-1], len(run), len(node.fields))
        picked.append((_restore_sort_key(d, node, i), node, run))
    picked.sort(key=lambda t: t[0])
    return [render_node_line(node.path, node, run, pad=False) for _, node, run in picked]
