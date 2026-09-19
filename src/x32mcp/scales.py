"""Value scaling between OSC floats (0..1) and engineering units (DESIGN.md §6).

Ground truth is docs/research/scales_params.md (verified against Maillot's ``X32.c`` /
``Xscene2X.c`` and the PDF fader tables). Everything here is pure and synchronous; raw OSC
floats never leave this module except through :class:`Scale` / the helpers below.

Decisions taken here (DESIGN.md is silent, or the verified research overrides it):

* Level taper (research §2): four linear segments with knees at 0.0625 / 0.25 / 0.5
  (−60 / −30 / −10 dB); 0.0 = −∞, 0.75 = 0 dB, 1.0 = +10 dB. **−90 dB and −∞ are the same
  float (0.0)** — the desk's 1024-value table has no −90.0 row (research §2.3), so
  ``db_to_fader(-90.0) == 0.0`` and ``fader_to_db(0.0) == -inf``. The −90 round trip therefore
  returns −∞ (deviation from DESIGN.md §6, which asked for −90.0 back).
* ``steps`` is the number of *distinct values* the desk stores (1024 faders, 161 sends,
  201 frequencies, …); the grid is ``i / (steps - 1)``. Quantisation rounds half **away from
  zero** like C ``roundf`` (Python's ``round`` is half-to-even); research §2.3 proves the
  ``round()`` rule (not the PDF's ``int(f*1023.5)``) from the hex fader table.
* ``lin`` / ``log`` / ``pan`` scales that carry ``steps`` snap the raw float to the grid inside
  ``to_value`` so on-grid values come back exact (``73/120`` → ``3.25`` dB, not
  ``3.2500000000000004``). ``level`` never snaps: it is exact maths; callers round for display.
* ``pan`` is fixed at −100..+100 (int) with 101 values (research §3); ``lo``/``hi`` are ignored.
* ``int`` clamps to ``[lo, hi]`` only when the range is not the default ``(0.0, 1.0)``; ``str``
  is never clamped.
* ``to_value(None)`` returns ``None`` (missing trailing node fields); ``to_raw(None)`` raises.
* All errors are :class:`ScaleError` (a ``ValueError``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

__all__ = [
    "NEG_INF_DB",
    "NEG_INF_TOKEN",
    "LEVEL_MAX_DB",
    "LEVEL_MIN_DB",
    "PAN_STEPS",
    "KINDS",
    "ScaleError",
    "Scale",
    "fader_to_db",
    "db_to_fader",
    "quantize",
    "lin_to_value",
    "value_to_lin",
    "log_to_value",
    "value_to_log",
    "enum_to_index",
    "index_to_enum",
    "format_db",
    "parse_db",
]

NEG_INF_DB = float("-inf")
NEG_INF_TOKEN = "-oo"  # the desk's text form of float 0.0 on a level (research §2.4)
LEVEL_MAX_DB = 10.0  # float 1.0
LEVEL_MIN_DB = -90.0  # at or below this the desk stores 0.0 == -oo (research §2.3)
PAN_STEPS = 101  # linf [-100, 100, 2] (research §3)

Kind = Literal["level", "lin", "log", "enum", "int", "bool", "str", "pan"]
KINDS: frozenset[str] = frozenset({"level", "lin", "log", "enum", "int", "bool", "str", "pan"})


class ScaleError(ValueError):
    """Bad scale definition or a value that cannot be converted."""


# -- small numeric helpers ---------------------------------------------------------------


def _clamp01(x: float) -> float:
    # ``<= 0.0`` also folds -0.0 to 0.0 (X32.c: "avoid -0.0 values (0x80000000)").
    return 0.0 if x <= 0.0 else 1.0 if x >= 1.0 else x


def _round_half_away(v: float) -> int:
    """Round like C ``roundf`` (half away from zero). Python's ``round`` is half-to-even."""
    a = abs(v)
    n = math.floor(a)
    if a - n >= 0.5:
        n += 1
    return n if v >= 0 else -n


def _as_float(v: Any) -> float:
    if v is None or isinstance(v, bool):
        raise ScaleError(f"expected a number, got {v!r}")
    if isinstance(v, str):
        s = v.strip()
        if s == NEG_INF_TOKEN:
            return NEG_INF_DB
        try:
            f = float(s)
        except ValueError:
            raise ScaleError(f"expected a number, got {v!r}") from None
    else:
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise ScaleError(f"expected a number, got {v!r}") from None
    if math.isnan(f):
        raise ScaleError("NaN is not a valid value")
    return f


def _as_int(v: Any) -> int:
    if v is None:
        raise ScaleError("expected an integer, got None")
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    return _round_half_away(_as_float(v))


_TRUE = frozenset({"on", "1", "true", "yes"})
_FALSE = frozenset({"off", "0", "false", "no", ""})


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _TRUE:
            return True
        if s in _FALSE:
            return False
    raise ScaleError(f"expected ON/OFF or bool, got {v!r}")


# -- level taper ---------------------------------------------------------------------------


def fader_to_db(x: float) -> float:
    """OSC level float (0..1) → dB. ``x <= 0`` → −∞; ``x >= 1`` → +10.0. Exact (never rounded).

    Per docs/research/scales_params.md §2.1 (``X32.c Slevel()`` / ``X32Automix.c add_3db()``).
    """
    if x <= 0.0:
        return NEG_INF_DB
    if x >= 1.0:
        return LEVEL_MAX_DB
    if x >= 0.5:
        return x * 40.0 - 30.0
    if x >= 0.25:
        return x * 80.0 - 50.0
    if x >= 0.0625:
        return x * 160.0 - 70.0
    return x * 480.0 - 90.0


def db_to_fader(db: float) -> float:
    """dB → OSC level float clamped to [0.0, 1.0]. −∞ and anything ≤ −90 → 0.0. Not quantised.

    Per docs/research/scales_params.md §2.2 (closed form of ``X32.c XslashSetLevl()``).
    """
    if math.isnan(db):
        raise ScaleError("NaN is not a valid dB value")
    if db <= LEVEL_MIN_DB:  # research §2.3: −90 dB has no row of its own, it *is* -oo
        return 0.0
    if db < -60.0:
        x = (db + 90.0) / 480.0
    elif db < -30.0:
        x = (db + 70.0) / 160.0
    elif db < -10.0:
        x = (db + 50.0) / 80.0
    elif db <= 10.0:
        x = (db + 30.0) / 40.0
    else:
        return 1.0
    return _clamp01(x)


def quantize(x: float, steps: int) -> float:
    """Snap ``x`` (0..1) to the desk's grid of ``steps`` distinct values: ``round(x·(steps−1))/(steps−1)``.

    Rounds half away from zero (C ``roundf``). Research §2.3: 1024 for faders, 161 for sends —
    ``-85.4`` dB on the 1024 grid becomes 10/1023 and reads back as −85.3, as the console does.
    """
    if steps is None or isinstance(steps, bool) or steps < 2:
        raise ScaleError(f"steps must be an int >= 2, got {steps!r}")
    n = steps - 1
    return _round_half_away(_clamp01(x) * n) / n


# -- linear / logarithmic ------------------------------------------------------------------


def lin_to_value(x: float, lo: float, hi: float) -> float:
    """``lo + x·(hi − lo)``; ``x`` is clamped to [0, 1] first (research §1.1)."""
    return lo + _clamp01(x) * (hi - lo)


def value_to_lin(v: float, lo: float, hi: float) -> float:
    """``(v − lo) / (hi − lo)`` clamped to [0, 1]. Not quantised (see :func:`quantize`)."""
    if hi == lo:
        raise ScaleError("lin scale needs hi != lo")
    return _clamp01((v - lo) / (hi - lo))


def _check_log(lo: float, hi: float) -> None:
    if lo <= 0 or hi <= 0 or lo == hi:
        raise ScaleError(f"log scale needs positive lo != hi, got lo={lo!r} hi={hi!r}")


def log_to_value(x: float, lo: float, hi: float) -> float:
    """``lo · (hi/lo)^x``; works for ``hi < lo`` (Q: 10 → 0.3). ``x`` is clamped to [0, 1]."""
    _check_log(lo, hi)
    return lo * (hi / lo) ** _clamp01(x)


def value_to_log(v: float, lo: float, hi: float) -> float:
    """``ln(v/lo) / ln(hi/lo)`` clamped to [0, 1]; ``v`` outside the range folds to 0 or 1.

    Research §1.2 (``X32.c XslashSetLogf()``): the ratio is well defined for decreasing ranges
    because both logs are then negative.
    """
    _check_log(lo, hi)
    a, b = (lo, hi) if lo < hi else (hi, lo)
    v = min(max(v, a), b)  # clamping first keeps log() away from 0
    return _clamp01(math.log(v / lo) / math.log(hi / lo))


# -- enums ---------------------------------------------------------------------------------


def enum_to_index(token: str | int, values: Sequence[str]) -> int:
    """Token → OSC int index. Exact match first, then case-insensitive. An ``int`` is taken as an
    index and range-checked (so ``10`` is index 10, while ``"10"`` is the ratio token)."""
    if isinstance(token, bool) or token is None:
        raise ScaleError(f"enum token must be a string or index, got {token!r}")
    if isinstance(token, int):
        if 0 <= token < len(values):
            return token
        raise ScaleError(f"enum index {token} out of range 0..{len(values) - 1}")
    if not isinstance(token, str):
        raise ScaleError(f"enum token must be a string or index, got {token!r}")
    try:
        return list(values).index(token)
    except ValueError:
        pass
    t = token.strip().lower()
    for i, v in enumerate(values):
        if v.lower() == t:
            return i
    raise ScaleError(f"unknown token {token!r}; expected one of {', '.join(values)}")


def index_to_enum(i: int, values: Sequence[str]) -> str:
    """OSC int index → token; raises :class:`ScaleError` when out of range."""
    if isinstance(i, bool) or i is None:
        raise ScaleError(f"enum index must be an int, got {i!r}")
    if isinstance(i, float):
        if not i.is_integer():
            raise ScaleError(f"enum index must be an int, got {i!r}")
        i = int(i)
    if not isinstance(i, int) or not 0 <= i < len(values):
        raise ScaleError(f"enum index {i!r} out of range 0..{len(values) - 1}")
    return values[i]


# -- dB text --------------------------------------------------------------------------------


def format_db(db: float) -> str:
    """One-decimal dB for tool output: ``-oo`` for −∞, explicit ``+`` when positive, ``0.0`` for
    anything that rounds to zero (never ``-0.0``). E.g. ``-12.3``, ``+2.0``, ``+10.0``.

    Ties round half away from zero, like the desk: the 161-level grid row ``0.5437`` is exactly
    −8.25 dB and the PDF table prints it ``-8.3`` (research §2.3); Python's ``round`` would give −8.2.
    """
    if math.isnan(db):
        raise ScaleError("NaN is not a valid dB value")
    if math.isinf(db):
        return NEG_INF_TOKEN if db < 0 else "+oo"
    tenths = _round_half_away(db * 10.0)
    if tenths == 0:
        return "0.0"
    return f"{tenths / 10.0:+.1f}"


def parse_db(text: str | float) -> float:
    """Inverse of :func:`format_db`: ``"-oo"`` → −∞, otherwise ``float(text)`` (``+2.1`` ok)."""
    return _as_float(text)


# -- Scale ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Scale:
    """A parameter's value mapping, built by the descriptor from ``device.yaml``.

    ``kind``:
      ``level`` — dB (−∞..+10) ↔ fader taper; ``steps`` 1024 (faders) or 161 (sends/mlevel).
      ``lin``   — ``lo..hi`` linear (dB, ms, %, …); ``steps`` = distinct values.
      ``log``   — ``lo..hi`` logarithmic (Hz, ms, Q); ``hi < lo`` allowed (Q 10 → 0.3).
      ``pan``   — int −100..+100 ↔ 0..1 (101 values); ``lo``/``hi`` ignored.
      ``enum``  — token ↔ int index into ``values``.
      ``int`` / ``bool`` / ``str`` — passthrough with coercion (``bool``: on-the-wire 1 = True).
    """

    kind: Kind
    lo: float = 0.0
    hi: float = 1.0
    steps: int | None = None
    unit: str = ""
    values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ScaleError(f"unknown scale kind {self.kind!r}; expected one of {sorted(KINDS)}")
        if self.steps is not None and (isinstance(self.steps, bool) or not isinstance(self.steps, int) or self.steps < 2):
            raise ScaleError(f"steps must be an int >= 2 or None, got {self.steps!r}")
        if self.kind == "enum":
            if not self.values:
                raise ScaleError("enum scale needs a non-empty values list")
            object.__setattr__(self, "values", tuple(str(v) for v in self.values))
        elif self.kind == "lin":
            if self.hi == self.lo:
                raise ScaleError("lin scale needs hi != lo")
        elif self.kind == "log":
            _check_log(self.lo, self.hi)

    @classmethod
    def from_spec(cls, spec: Mapping[str, Any] | str, *, enums: Mapping[str, Sequence[str]] | None = None) -> "Scale":
        """Build from a ``device.yaml`` spec: ``{kind, lo, hi, steps, unit, values | enum}`` or a bare
        kind string. ``enum: name`` is looked up in ``enums``; ``values`` (or ``enum``) implies
        ``kind: enum``. Unknown keys are ignored (the descriptor validates its own schema)."""
        if isinstance(spec, str):
            spec = {"kind": spec}
        if not isinstance(spec, Mapping):
            raise ScaleError(f"scale spec must be a mapping or kind string, got {spec!r}")
        values: Sequence[str] = spec.get("values") or ()
        if "enum" in spec and spec["enum"] is not None:
            name = spec["enum"]
            if enums is None or name not in enums:
                raise ScaleError(f"unknown enum {name!r}")
            values = enums[name]
        kind = spec.get("kind") or ("enum" if values else None)
        if kind is None:
            raise ScaleError(f"scale spec needs a kind: {dict(spec)!r}")
        steps = spec.get("steps")
        return cls(
            kind=str(kind),  # type: ignore[arg-type]
            lo=float(spec.get("lo", 0.0)),
            hi=float(spec.get("hi", 1.0)),
            steps=None if steps is None else int(steps),
            unit=str(spec.get("unit", "") or ""),
            values=tuple(values),
        )

    # -- ranges ----------------------------------------------------------------------------
    @property
    def _n(self) -> int | None:
        """Number of grid intervals (``steps − 1``), or None when unquantised."""
        return None if self.steps is None else self.steps - 1

    def _has_int_range(self) -> bool:
        return (self.lo, self.hi) != (0.0, 1.0)

    # -- conversions -----------------------------------------------------------------------
    def to_value(self, raw: Any) -> Any:
        """OSC arg → engineering value (level → dB float, enum int → token, bool int → bool,
        pan → int, lin/log → float snapped to the ``steps`` grid). ``None`` → ``None``."""
        if raw is None:
            return None
        k = self.kind
        if k == "level":
            return fader_to_db(_as_float(raw))
        if k == "lin":
            x = _clamp01(_as_float(raw))
            n = self._n
            if n is None:
                return lin_to_value(x, self.lo, self.hi)
            # Integer grid arithmetic keeps on-grid values exact (73/120 → 3.25, not 3.2500000004).
            return self.lo + _round_half_away(x * n) * (self.hi - self.lo) / n
        if k == "log":
            x = _as_float(raw)
            if self.steps is not None:
                x = quantize(x, self.steps)
            return log_to_value(x, self.lo, self.hi)
        if k == "pan":
            n = (self.steps or PAN_STEPS) - 1
            i = _round_half_away(_clamp01(_as_float(raw)) * n)
            return _round_half_away(-100.0 + i * 200.0 / n)
        if k == "enum":
            if isinstance(raw, str):
                return self.values[enum_to_index(raw, self.values)]
            return index_to_enum(raw, self.values)
        if k == "int":
            return _as_int(raw)
        if k == "bool":
            return _as_bool(raw)
        return str(raw)

    def to_raw(self, value: Any) -> Any:
        """Engineering value → OSC arg, clamped (see :meth:`clamp`) and quantised to ``steps``
        when given. Level accepts ``-inf`` / ``"-oo"``; enum accepts a token or an index."""
        if value is None:
            raise ScaleError(f"{self.kind} scale: value is required")
        v = self.clamp(value)
        k = self.kind
        if k == "level":
            x = db_to_fader(v)
            return quantize(x, self.steps) if self.steps else x
        if k == "lin":
            x = value_to_lin(v, self.lo, self.hi)
            return quantize(x, self.steps) if self.steps else x
        if k == "log":
            x = value_to_log(v, self.lo, self.hi)
            return quantize(x, self.steps) if self.steps else x
        if k == "pan":
            return quantize((v + 100.0) / 200.0, self.steps or PAN_STEPS)
        if k == "enum":
            return enum_to_index(v, self.values)
        if k == "bool":
            return 1 if v else 0
        return v  # int / str already coerced by clamp()

    def clamp(self, value: Any) -> Any:
        """Coerce and clamp an engineering value into the scale's range:
        level → float dB in (−90, +10] with anything ≤ −90 (or ``"-oo"``) folded to −∞;
        lin/log → float within [lo, hi] (either order); pan → int −100..+100;
        enum → canonical token (raises on unknown); int → int (clamped only with a non-default
        range); bool → bool; str → str."""
        k = self.kind
        if k == "level":
            v = _as_float(value)
            if v <= LEVEL_MIN_DB:
                return NEG_INF_DB
            return LEVEL_MAX_DB if v > LEVEL_MAX_DB else v
        if k in ("lin", "log"):
            v = _as_float(value)
            a, b = (self.lo, self.hi) if self.lo < self.hi else (self.hi, self.lo)
            return min(max(v, a), b)
        if k == "pan":
            return max(-100, min(100, _as_int(value)))
        if k == "enum":
            return self.values[enum_to_index(value, self.values)]
        if k == "int":
            v = _as_int(value)
            if self._has_int_range():
                v = max(int(self.lo), min(int(self.hi), v))
            return v
        if k == "bool":
            return _as_bool(value)
        if value is None:
            raise ScaleError("str scale: value is required")
        return str(value)
