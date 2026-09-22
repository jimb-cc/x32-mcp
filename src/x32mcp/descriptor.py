"""``device.yaml`` loader → :class:`Descriptor` (DESIGN.md §7).

``device.yaml`` is the only place device knowledge lives. This module turns it into validated,
typed objects: a :class:`~x32mcp.scales.Scale` per named scale, a :class:`ParamSpec` per parameter
*template* (``ch:mix/{send:02d}/level``), a :class:`NodeSection` per ``/node`` block and a
:class:`NodePath` for every concrete node a full sweep visits. Everything is built once in
:meth:`Descriptor.load`; lookups afterwards are dict hits or one pre-compiled regex match.

Decisions where DESIGN.md is silent (or where the verified research overrides it):

* **Reverse lookup is strict.** :meth:`Descriptor.param_for_address` accepts only the canonical
  address form the desk itself uses (``/ch/05/…`` zero-padded, ``/headamp/005``, ``/dca/3``) and
  only in-range indices: ``/ch/33/…``, ``/ch/05/eq/5/g`` or ``/bus/01/mix/07/level`` → ``None``.
  Index ranges come from ``strips`` (count/ids/sends/eq_bands) and the ``for`` ranges in ``nodes``.
* **Policy clamps are not applied by ``to_raw``.** :meth:`ParamSpec.to_raw` performs only the scale
  conversion (which clamps to the scale's own physical range); the policy ceilings from
  ``clamp: {min, max}`` are exposed as :attr:`ParamSpec.clamp_min` / :attr:`ParamSpec.clamp_max` and
  :meth:`ParamSpec.clamp` so that ``policy.py`` can *report* a clamp instead of hiding it.
* **Unknown addresses are tier 1** in :meth:`Descriptor.tier_for`: a write to something we do not
  describe is at least a mix move; a guarded glob still wins with tier 2. A spec without ``tier``
  defaults to 1.
* Guarded globs are matched case-sensitively (``fnmatch.fnmatch`` would be case-insensitive on
  Windows) and ``*`` matches ``/`` (fnmatch semantics), so ``/*/grp/*`` covers ``/ch/01/grp/dca``.
* :meth:`Descriptor.param` accepts a concrete relpath (``mix/03/level``) as well as the template.
* The full sweep is 2101 node paths, not "< 1500" as DESIGN §7 hoped: 48 input strips × 16 sends
  alone are 768 nodes and a console-saved scene file has 2104 node lines
  (docs/research/scales_params.md §13).
* ``-show/prepos`` is swept as its leaf ``/-show/prepos/current`` and the five ``-stat`` RTA leaves
  as single-parameter nodes (docs/research/transport.md §6.4 items 12–14: ``-stat/rta`` is not a
  node and ``/node -stat`` is malformed on FW 4.06). For those the node path *is* the address.
* ``strips`` must agree with the fixed table in :mod:`x32mcp.targets` (paths, counts, ids); a
  mismatch is a :class:`DescriptorError` at load time rather than a surprise in ``strip_targets``.
"""

from __future__ import annotations

import fnmatch
import itertools
import logging
import re
import string
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import yaml

from .config import Settings
from .scales import Scale, ScaleError
from .targets import Target, families as target_families

__all__ = [
    "TOP_LEVEL_KEYS",
    "OPTIONAL_TOP_LEVEL_KEYS",
    "TEMPLATE_VARS",
    "DescriptorError",
    "StripFamily",
    "ParamSpec",
    "NodeSection",
    "NodePath",
    "Descriptor",
]

log = logging.getLogger(__name__)

TOP_LEVEL_KEYS: tuple[str, ...] = (
    "meta", "scales", "enums", "strips", "params", "nodes", "guarded",
    "policy", "rta", "geq", "detector", "ringout", "mics",
)
# optional top-level blocks: absent = an empty mapping (every consumer has defaults for every key)
OPTIONAL_TOP_LEVEL_KEYS: tuple[str, ...] = ("cfs_policy",)
TEMPLATE_VARS: frozenset[str] = frozenset({"n", "id", "band", "send", "idx"})
_SPEC_KEYS = frozenset({"osc", "scale", "enum", "node", "tier", "clamp", "inverted_mute"})
_STRIP_KEYS = frozenset({"count", "ids", "path", "label", "sends", "send_target", "eq_bands", "preamp", "gate", "dyn", "insert"})
_NODE_KEYS = frozenset({"family", "section", "path", "fields", "fields_even", "for"})
_OSC_TYPES = frozenset({"f", "i", "s"})
_TIERS = (0, 1, 2)
_ADDR_CACHE_MAX = 8192

_FORMATTER = string.Formatter()
_FORMAT_SPEC = re.compile(r"(0\d+)?d?")  # {n}, {n:02d}, {n:03d}

VarRange = tuple[int, int] | frozenset[str]
VarRanges = Mapping[str, VarRange]


class DescriptorError(ValueError):
    """Bad ``device.yaml`` content or a bad lookup. ``path`` is the dotted key path of the offending
    entry (``params.ch.mix/fader.scale``, ``nodes[3].fields[1]``) when one applies."""

    def __init__(self, message: str, *, path: str = "") -> None:
        super().__init__(f"{path}: {message}" if path else message)
        self.message = message
        self.path = path


# -- template helpers ----------------------------------------------------------------------


def _template_vars(tmpl: str, where: str = "") -> tuple[str, ...]:
    """Ordered variable names in a ``str.format`` template (``{n:02d}`` → ``n``).

    Only the vocabulary in :data:`TEMPLATE_VARS` with ``d`` / ``0Nd`` format specs is allowed; anything
    else (a stray brace, ``{0}``, ``{name!r}``) is a :class:`DescriptorError`.
    """
    try:
        parsed = list(_FORMATTER.parse(tmpl))
    except ValueError as e:
        raise DescriptorError(f"bad template {tmpl!r}: {e}", path=where) from None
    names: list[str] = []
    for _literal, name, spec, conv in parsed:
        if name is None:
            continue
        if name not in TEMPLATE_VARS or conv is not None or not _FORMAT_SPEC.fullmatch(spec or ""):
            raise DescriptorError(
                f"bad template variable {{{name}{':' + spec if spec else ''}}} in {tmpl!r}; "
                f"allowed: {sorted(TEMPLATE_VARS)} with formats d / 02d / 03d",
                path=where,
            )
        names.append(name)
    return tuple(names)


def _template_regex(tmpl: str) -> str:
    """Regex source matching what ``tmpl.format(...)`` can produce. Numeric vars match ``\\d+`` (padding
    is checked afterwards by re-rendering), ``id`` matches one path segment; a repeated var must repeat."""
    out: list[str] = []
    seen: set[str] = set()
    for literal, name, _spec, _conv in _FORMATTER.parse(tmpl):
        out.append(re.escape(literal))
        if name is None:
            continue
        if name in seen:
            out.append(f"(?P={name})")
        else:
            seen.add(name)
            out.append(f"(?P<{name}>[^/]+)" if name == "id" else rf"(?P<{name}>\d+)")
    return "".join(out)


def _render(tmpl: str, vars: Mapping[str, Any], where: str) -> str:
    try:
        return tmpl.format(**vars)
    except KeyError as e:
        raise DescriptorError(f"{tmpl!r} needs template variable {e.args[0]!r}", path=where) from None
    except (ValueError, TypeError) as e:
        raise DescriptorError(f"cannot render {tmpl!r} with {dict(vars)!r}: {e}", path=where) from None


def _var_in_range(ranges: VarRanges, name: str, value: Any) -> bool:
    r = ranges.get(name)
    if name == "id":
        return isinstance(value, str) and (r is None or value in r)
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    if r is None or isinstance(r, frozenset):
        return True
    return r[0] <= value <= r[1]


def _describe_range(ranges: VarRanges, name: str) -> str:
    r = ranges.get(name)
    if r is None:
        return "an int" if name != "id" else "a str"
    if isinstance(r, frozenset):
        return "one of " + ", ".join(sorted(r))
    return f"{r[0]}..{r[1]}"


def _glob_pattern(glob: str) -> re.Pattern[str]:
    # fnmatch.translate is case-sensitive; fnmatch.fnmatch() would normcase() on Windows.
    return re.compile(fnmatch.translate(glob))


# -- validation helpers --------------------------------------------------------------------


def _expect_mapping(obj: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(obj, Mapping):
        raise DescriptorError(f"expected a mapping, got {type(obj).__name__}", path=where)
    return obj


def _expect_list(obj: Any, where: str, *, allow_empty: bool = False) -> list[Any]:
    if not isinstance(obj, list):
        raise DescriptorError(f"expected a list, got {type(obj).__name__}", path=where)
    if not obj and not allow_empty:
        raise DescriptorError("must not be empty", path=where)
    return obj


def _expect_str(obj: Any, where: str, *, allow_empty: bool = False) -> str:
    if not isinstance(obj, str) or (not obj and not allow_empty):
        raise DescriptorError(f"expected a non-empty string, got {obj!r}", path=where)
    return obj


def _expect_int(obj: Any, where: str, *, lo: int | None = None) -> int:
    if isinstance(obj, bool) or not isinstance(obj, int):
        raise DescriptorError(f"expected an int, got {obj!r}", path=where)
    if lo is not None and obj < lo:
        raise DescriptorError(f"expected an int >= {lo}, got {obj}", path=where)
    return obj


def _expect_bool(obj: Any, where: str) -> bool:
    if not isinstance(obj, bool):
        raise DescriptorError(f"expected true/false, got {obj!r}", path=where)
    return obj


def _expect_number(obj: Any, where: str) -> float:
    if isinstance(obj, bool) or not isinstance(obj, (int, float)):
        raise DescriptorError(f"expected a number, got {obj!r}", path=where)
    return float(obj)


def _no_unknown_keys(m: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = [k for k in m if k not in allowed]
    if unknown:
        raise DescriptorError(f"unknown keys {unknown}; allowed: {sorted(allowed)}", path=where)


# -- strips --------------------------------------------------------------------------------


@dataclass(frozen=True)
class StripFamily:
    """One ``strips.<family>`` entry: how many strips, their OSC root template and feature flags.

    ``count``/``ids`` are mutually exclusive (``main`` has ids ``st``/``m``); ``sends`` and
    ``eq_bands`` are ``None`` when the family has none (``mtx`` has no sends, ``dca`` neither).
    """

    name: str
    path: str  # root template: "/ch/{n:02d}", "/main/{id}", "/dca/{n}"
    label: Any = field(hash=False, compare=False)  # "Ch" or {id: label}
    count: int | None = None
    ids: tuple[str, ...] | None = None
    sends: int | None = None
    send_target: str | None = None
    eq_bands: int | None = None
    preamp: bool = False
    gate: bool = False
    dyn: bool = False
    insert: bool = False

    @property
    def indices(self) -> tuple[int | str, ...]:
        """1-based numbers (``1..count``) or the id tokens, in desk order."""
        return self.ids if self.ids is not None else tuple(range(1, (self.count or 0) + 1))

    def root_vars(self, index: int | str) -> dict[str, Any]:
        return {"id": index} if isinstance(index, str) else {"n": index}

    def root(self, index: int | str) -> str:
        """Concrete OSC root for one strip (``/ch/05``, ``/main/st``)."""
        return self.path.format(**self.root_vars(index))

    def roots(self) -> list[tuple[str, dict[str, Any]]]:
        """Every concrete root with the template vars that produced it."""
        return [(self.root(i), self.root_vars(i)) for i in self.indices]

    def label_for(self, index: int | str) -> str:
        if isinstance(self.label, Mapping):
            return str(self.label[index])
        return f"{self.label} {index}"

    def targets(self) -> list[Target]:
        return [Target(self.name, i) for i in self.indices]


# -- params --------------------------------------------------------------------------------


@dataclass(frozen=True)
class ParamSpec:
    """One parameter template of a family (``ch:mix/{send:02d}/level``), with its scale.

    Units at this boundary are engineering units (dB, Hz, ms, %, enum tokens, bool, str). The raw
    OSC side is ``osc_type`` ``f`` (float 0..1), ``i`` (int) or ``s`` (string).
    """

    key: str  # "ch:mix/fader"
    family: str
    relpath: str  # template relative to the family root
    osc_type: str  # "f" | "i" | "s"
    scale: Scale
    node_fmt: str  # node text format token (vocabulary owned by nodes.py)
    tier: int  # 0 read-only, 1 mix move, 2 guarded
    clamp_min: float | None
    clamp_max: float | None
    inverted_mute: bool  # wire 1 = ON = unmuted (mix/on, dca on, send on)
    enum: tuple[str, ...] | None  # tokens when enum-valued (index = OSC int), else None
    root_template: str  # "/ch/{n:02d}", "/headamp/{n:03d}", "/config"
    scale_name: str | None = None  # name in device.yaml `scales` (None for enum params)
    enum_name: str | None = None  # name in device.yaml `enums` (None for scaled params)
    var_ranges: VarRanges = field(default_factory=dict, hash=False, compare=False, repr=False)

    # -- template ----------------------------------------------------------------------------
    @cached_property
    def template(self) -> str:
        """Absolute address template: root + '/' + relpath."""
        return f"{self.root_template}/{self.relpath}"

    @cached_property
    def vars(self) -> tuple[str, ...]:
        """Template variables the address needs (``('n', 'send')``), root first."""
        seen: dict[str, None] = {}
        for v in _template_vars(self.template, self.key):
            seen.setdefault(v, None)
        return tuple(seen)

    @cached_property
    def regex(self) -> re.Pattern[str]:
        return re.compile("^" + _template_regex(self.template) + "$")

    @property
    def is_template(self) -> bool:
        return bool(self.vars)

    @property
    def unit(self) -> str:
        return self.scale.unit

    def check_var(self, name: str, value: Any) -> None:
        """Raise :class:`DescriptorError` unless ``value`` is a valid ``name`` for this family."""
        if not _var_in_range(self.var_ranges, name, value):
            raise DescriptorError(
                f"{name}={value!r} is out of range; expected {_describe_range(self.var_ranges, name)}", path=self.key
            )

    def address(self, target: Target | None = None, **vars: Any) -> str:
        """Concrete OSC address. ``target`` supplies ``n``/``id`` for strip families; the rest
        (``send``, ``band``, ``idx``, or ``n`` for headamp/fx) come as keywords, all 1-based as
        printed on the desk (headamp ``n`` is 0-based, 0..127). Raises :class:`DescriptorError`
        on a missing / out-of-range variable or a target of another family."""
        v = dict(vars)
        if target is not None:
            if target.family != self.family:
                raise DescriptorError(f"target {target.key} is a {target.family!r} strip", path=self.key)
            v.setdefault("id" if isinstance(target.index, str) else "n", target.index)
        for name in self.vars:
            if name not in v:
                raise DescriptorError(f"missing template variable {name!r}", path=self.key)
            self.check_var(name, v[name])
        return _render(self.template, v, self.key)

    def match(self, address: str) -> dict[str, Any] | None:
        """Template vars if ``address`` is the canonical rendering of this spec, else ``None``."""
        m = self.regex.match(address)
        if not m:
            return None
        vars = {k: (v if k == "id" else int(v)) for k, v in m.groupdict().items()}
        if not all(_var_in_range(self.var_ranges, k, v) for k, v in vars.items()):
            return None
        if _render(self.template, vars, self.key) != address:  # rejects "/ch/5/…", "/headamp/05"
            return None
        return vars

    # -- values ------------------------------------------------------------------------------
    def to_value(self, raw: Any) -> Any:
        """OSC arg → engineering value (see :meth:`Scale.to_value`); ``None`` → ``None``."""
        return self.scale.to_value(raw)

    def to_raw(self, value: Any) -> Any:
        """Engineering value → OSC arg, quantised to the scale grid. Applies the scale's physical
        range only, NOT :attr:`clamp_min`/:attr:`clamp_max` (policy reports those, see module doc)."""
        return self.scale.to_raw(value)

    def clamp(self, value: Any) -> Any:
        """Engineering value limited to the policy ``clamp`` range (after the scale's own coercion).
        ``-inf`` levels pass through untouched; returns the value unchanged when no clamp is declared."""
        v = self.scale.clamp(value)
        if self.clamp_min is None and self.clamp_max is None:
            return v
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return v
        if self.clamp_min is not None and v < self.clamp_min:
            v = self.clamp_min
        if self.clamp_max is not None and v > self.clamp_max:
            v = self.clamp_max
        if self.scale.kind in ("int", "pan"):
            return int(v)
        return v

    def __str__(self) -> str:
        return self.key


# -- nodes ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeSection:
    """One ``nodes`` entry: a ``/node`` block template and its field templates in desk print order.

    ``section`` is relative to the strip root (``"eq/{band}"``; ``""`` means the root itself) for
    strip-relative entries; ``path_template`` is always the absolute template (``"/ch/{n:02d}/eq/{band}"``,
    ``"/headamp/{n:03d}"``). ``for_vars`` holds inclusive ranges; ``fields_even`` replaces ``fields``
    when the single ``for`` variable is even (even sends print ``on level`` only, research §4.9).
    """

    family: str
    section: str
    path_template: str
    fields: tuple[str, ...]
    fields_even: tuple[str, ...] | None
    for_vars: Mapping[str, tuple[int, int]] = field(hash=False, compare=False)
    root_template: str = ""
    is_strip_section: bool = True

    @property
    def key(self) -> str:
        return f"{self.family}:{self.section if self.is_strip_section else self.path_template}"

    def var_combos(self) -> list[dict[str, int]]:
        """Every assignment of the ``for`` variables (``[{}]`` when there are none)."""
        names = list(self.for_vars)
        if not names:
            return [{}]
        ranges = [range(lo, hi + 1) for lo, hi in self.for_vars.values()]
        return [dict(zip(names, vals)) for vals in itertools.product(*ranges)]

    def uses_even_fields(self, vars: Mapping[str, Any]) -> bool:
        if not self.fields_even or len(self.for_vars) != 1:
            return False
        (name,) = self.for_vars
        return isinstance(vars.get(name), int) and vars[name] % 2 == 0

    def field_templates_for(self, vars: Mapping[str, Any]) -> tuple[str, ...]:
        """Field templates (param keys) that apply for this variable assignment."""
        return self.fields_even if self.uses_even_fields(vars) else self.fields  # type: ignore[return-value]

    def fields_for(self, vars: Mapping[str, Any]) -> tuple[str, ...]:
        """Concrete field relpaths (``mix/03/on``) for this variable assignment."""
        return tuple(_render(f, vars, self.key) for f in self.field_templates_for(vars))

    def expand(self, strips: Mapping[str, StripFamily]) -> list[tuple[str, dict[str, Any]]]:
        """Concrete node paths with the template vars that produced each (``n``/``id`` + ``for`` vars).
        Field lists follow from :meth:`fields_for`."""
        combos = self.var_combos()
        out: list[tuple[str, dict[str, Any]]] = []
        if self.is_strip_section:
            fam = strips.get(self.family)
            if fam is None:
                raise DescriptorError(f"unknown strip family {self.family!r}", path=self.key)
            for root, root_vars in fam.roots():
                for combo in combos:
                    v = {**root_vars, **combo}
                    section = _render(self.section, v, self.key)
                    out.append((f"{root}/{section}" if section else root, v))
        else:
            root_re = re.compile("^" + _template_regex(self.root_template) + "(?=/|$)")
            for combo in combos:
                path = _render(self.path_template, combo, self.key)
                m = root_re.match(path)
                if not m:
                    raise DescriptorError(f"path {path!r} is not under the {self.family!r} root {self.root_template!r}", path=self.key)
                v = {**{k: (x if k == "id" else int(x)) for k, x in m.groupdict().items()}, **combo}
                out.append((path, v))
        return out


@dataclass(frozen=True)
class NodePath:
    """One concrete ``/node`` path of a full sweep with its ordered fields and specs.

    ``fields`` are concrete relpaths relative to the family ``root`` (``mix/03/on``) — the keys used
    inside a ``DeskState`` section; ``addresses`` are the leaf OSC addresses (``root + '/' + field``).
    For single-leaf nodes (``/-stat/rtasource``, ``/-show/prepos/current``) the address equals ``path``.
    """

    path: str
    family: str
    root: str
    fields: tuple[str, ...]
    specs: tuple[ParamSpec, ...]
    vars: Mapping[str, Any] = field(hash=False, compare=False)
    section: NodeSection = field(hash=False, compare=False, repr=False)

    @property
    def addresses(self) -> tuple[str, ...]:
        return tuple(f"{self.root}/{f}" for f in self.fields)

    @property
    def target(self) -> Target | None:
        """The strip this node belongs to, or ``None`` for non-strip families."""
        idx = self.vars.get("id", self.vars.get("n"))
        if idx is None or self.family not in target_families():
            return None
        return Target(self.family, idx)

    def __len__(self) -> int:
        return len(self.fields)

    def __iter__(self) -> Iterator[tuple[str, ParamSpec]]:
        return iter(zip(self.fields, self.specs))


# -- descriptor ----------------------------------------------------------------------------


class Descriptor:
    """Validated view of ``device.yaml``. Build with :meth:`load` (file) or :meth:`from_dict`.

    Attributes (all read-only by convention): ``path``, ``meta``, ``roots`` (non-strip family →
    root template), ``scales``, ``enums``, ``strips``, ``params`` (family → relpath template →
    :class:`ParamSpec`), ``guarded`` (globs), and the plain dicts ``policy``, ``rta``, ``geq``,
    ``detector``, ``ringout``, ``mics`` and the optional ``cfs_policy`` (``{}`` when absent).
    """

    def __init__(self, data: Mapping[str, Any], *, path: Path | None = None) -> None:
        self.path: Path | None = path
        data = _expect_mapping(data, "<root>")
        missing = [k for k in TOP_LEVEL_KEYS if k not in data]
        if missing:
            raise DescriptorError(f"missing top-level keys {missing}", path="<root>")
        _no_unknown_keys(data, frozenset(TOP_LEVEL_KEYS) | frozenset(OPTIONAL_TOP_LEVEL_KEYS), "<root>")

        self.meta: dict[str, Any] = dict(_expect_mapping(data["meta"], "meta"))
        self.roots: dict[str, str] = self._build_roots(self.meta)
        self.scales: dict[str, Scale] = self._build_scales(data["scales"])
        self.enums: dict[str, tuple[str, ...]] = self._build_enums(data["enums"])
        self.strips: dict[str, StripFamily] = self._build_strips(data["strips"])
        for fam in self.strips:
            if fam in self.roots:
                raise DescriptorError(f"{fam!r} is a strip family; its root comes from strips, not meta.roots", path=f"meta.roots.{fam}")
        self._var_ranges: dict[str, dict[str, VarRange]] = self._collect_var_ranges(data["nodes"])
        self.params: dict[str, dict[str, ParamSpec]] = self._build_params(data["params"])
        self._sections: tuple[NodeSection, ...] = self._build_sections(data["nodes"])
        self.guarded: tuple[str, ...] = self._build_guarded(data["guarded"])
        self._guard_res: tuple[re.Pattern[str], ...] = tuple(_glob_pattern(g) for g in self.guarded)
        self.policy: dict[str, Any] = dict(_expect_mapping(data["policy"], "policy"))
        self.rta: dict[str, Any] = dict(_expect_mapping(data["rta"], "rta"))
        self.geq: dict[str, Any] = dict(_expect_mapping(data["geq"], "geq"))
        self.detector: dict[str, Any] = dict(_expect_mapping(data["detector"], "detector"))
        self.ringout: dict[str, Any] = dict(_expect_mapping(data["ringout"], "ringout"))
        self.mics: dict[str, Any] = dict(_expect_mapping(data["mics"], "mics"))
        # CFS² policy layer (docs/CFS_POLICY.md): optional; x32mcp.cfs_policy.CfsPolicyConfig validates the keys
        self.cfs_policy: dict[str, Any] = dict(_expect_mapping(data.get("cfs_policy") or {}, "cfs_policy"))

        self._by_head: dict[str, list[ParamSpec]] = {}
        for fam_specs in self.params.values():
            for spec in fam_specs.values():
                self._by_head.setdefault(spec.root_template.split("/")[1], []).append(spec)
        self._addr_cache: dict[str, tuple[ParamSpec, dict[str, Any]] | None] = {}

        self._nodes: tuple[NodePath, ...] = self._expand_nodes()
        self._node_by_path: dict[str, NodePath] = {n.path: n for n in self._nodes}
        self._validate_tables()
        log.debug("descriptor loaded from %s: %d params, %d node paths", path, sum(len(p) for p in self.params.values()), len(self._nodes))

    # -- construction ------------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path | str | None = None) -> "Descriptor":
        """Load and validate ``device.yaml`` (default: ``Settings.from_env().device_yaml``).
        Raises :class:`DescriptorError` naming the offending key path."""
        p = Path(path) if path is not None else Settings.from_env().device_yaml
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as e:
            raise DescriptorError(f"cannot read {p}: {e}") from None
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise DescriptorError(f"{p}: invalid YAML: {e}") from None
        if not isinstance(data, Mapping):
            raise DescriptorError(f"{p}: top level must be a mapping")
        return cls(data, path=p)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, path: Path | None = None) -> "Descriptor":
        """Build from already-parsed YAML data (tests, or a descriptor embedded in a snapshot)."""
        return cls(data, path=path)

    @staticmethod
    def _build_roots(meta: Mapping[str, Any]) -> dict[str, str]:
        roots = _expect_mapping(meta.get("roots"), "meta.roots")
        out: dict[str, str] = {}
        for fam, tmpl in roots.items():
            where = f"meta.roots.{fam}"
            tmpl = _expect_str(tmpl, where)
            if not tmpl.startswith("/") or tmpl.endswith("/"):
                raise DescriptorError(f"root must start (and not end) with '/', got {tmpl!r}", path=where)
            vars = _template_vars(tmpl, where)
            if set(vars) - {"n", "id"}:
                raise DescriptorError(f"a root may only use {{n}}/{{id}}, got {vars}", path=where)
            out[str(fam)] = tmpl
        return out

    @staticmethod
    def _build_scales(raw: Any) -> dict[str, Scale]:
        scales = _expect_mapping(raw, "scales")
        out: dict[str, Scale] = {}
        for name, spec in scales.items():
            where = f"scales.{name}"
            if isinstance(spec, str):
                spec = {"kind": spec}
            spec = _expect_mapping(spec, where)
            if "enum" in spec or "values" in spec:
                raise DescriptorError("named scales cannot be enums; use `enums`", path=where)
            try:
                out[str(name)] = Scale.from_spec(spec)
            except ScaleError as e:
                raise DescriptorError(str(e), path=where) from None
        if not out:
            raise DescriptorError("must not be empty", path="scales")
        return out

    @staticmethod
    def _build_enums(raw: Any) -> dict[str, tuple[str, ...]]:
        enums = _expect_mapping(raw, "enums")
        out: dict[str, tuple[str, ...]] = {}
        for name, toks in enums.items():
            where = f"enums.{name}"
            toks = _expect_list(toks, where)
            for i, t in enumerate(toks):
                if not isinstance(t, str) or not t:
                    raise DescriptorError(f"token must be a non-empty string (quote OFF/ON/Y/N in YAML), got {t!r}", path=f"{where}[{i}]")
            if len(set(toks)) != len(toks):
                dup = sorted({t for t in toks if toks.count(t) > 1})
                raise DescriptorError(f"duplicate tokens {dup}", path=where)
            out[str(name)] = tuple(toks)
        return out

    @staticmethod
    def _build_strips(raw: Any) -> dict[str, StripFamily]:
        strips = _expect_mapping(raw, "strips")
        known = target_families()
        out: dict[str, StripFamily] = {}
        for name, st in strips.items():
            where = f"strips.{name}"
            st = _expect_mapping(st, where)
            _no_unknown_keys(st, _STRIP_KEYS, where)
            if name not in known:
                raise DescriptorError(f"unknown strip family; targets.py knows {sorted(known)}", path=where)
            ref = known[name]
            path = _expect_str(st.get("path"), f"{where}.path")
            if path != ref["path"]:
                raise DescriptorError(f"path {path!r} does not match targets.py ({ref['path']!r})", path=f"{where}.path")
            if ("count" in st) == ("ids" in st):
                raise DescriptorError("exactly one of count / ids", path=where)
            count = ids = None
            if "count" in st:
                count = _expect_int(st["count"], f"{where}.count", lo=1)
                if count != ref.get("count"):
                    raise DescriptorError(f"count {count} does not match targets.py ({ref.get('count')})", path=f"{where}.count")
            else:
                ids_raw = _expect_list(st["ids"], f"{where}.ids")
                ids = tuple(_expect_str(i, f"{where}.ids[{k}]") for k, i in enumerate(ids_raw))
                if ids != tuple(ref.get("ids", ())):
                    raise DescriptorError(f"ids {list(ids)} do not match targets.py ({list(ref.get('ids', ()))})", path=f"{where}.ids")
            label = st.get("label")
            if isinstance(label, Mapping):
                if ids is None or set(label) != set(ids):
                    raise DescriptorError("label mapping must have one entry per id", path=f"{where}.label")
                label = {str(k): _expect_str(v, f"{where}.label.{k}") for k, v in label.items()}
            else:
                label = _expect_str(label, f"{where}.label")
            sends = _expect_int(st["sends"], f"{where}.sends", lo=1) if "sends" in st else None
            eq_bands = _expect_int(st["eq_bands"], f"{where}.eq_bands", lo=1) if "eq_bands" in st else None
            send_target = _expect_str(st["send_target"], f"{where}.send_target") if "send_target" in st else None
            flags = {k: _expect_bool(st[k], f"{where}.{k}") if k in st else False for k in ("preamp", "gate", "dyn", "insert")}
            out[str(name)] = StripFamily(
                name=str(name), path=path, label=label, count=count, ids=ids, sends=sends,
                send_target=send_target, eq_bands=eq_bands, **flags,
            )
        for name, st in out.items():
            if st.send_target is not None and st.send_target not in out:
                raise DescriptorError(f"send_target {st.send_target!r} is not a strip family", path=f"strips.{name}.send_target")
        missing = sorted(set(known) - set(out))
        if missing:
            raise DescriptorError(f"missing strip families known to targets.py: {missing}", path="strips")
        return out

    def _collect_var_ranges(self, nodes_raw: Any) -> dict[str, dict[str, VarRange]]:
        """Per-family template-variable ranges: strips (n/id/send/band) merged with the `for` ranges
        of the node sections (headamp n 0..127, fx n 1..8, show idx 0..99, …). Used to reject
        out-of-range addresses in both directions."""
        ranges: dict[str, dict[str, VarRange]] = {}
        for name, st in self.strips.items():
            r: dict[str, VarRange] = {}
            if st.ids is not None:
                r["id"] = frozenset(st.ids)
            else:
                r["n"] = (1, st.count or 0)
            if st.sends:
                r["send"] = (1, st.sends)
            if st.eq_bands:
                r["band"] = (1, st.eq_bands)
            ranges[name] = r
        for i, sec in enumerate(_expect_list(nodes_raw, "nodes")):
            sec = _expect_mapping(sec, f"nodes[{i}]")
            fam = sec.get("family")
            fr = sec.get("for") or {}
            if not isinstance(fam, str) or not isinstance(fr, Mapping):
                continue  # reported precisely by _build_sections
            r = ranges.setdefault(fam, {})
            for var, rng in fr.items():
                if not (isinstance(rng, list) and len(rng) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in rng)):
                    continue
                lo, hi = int(rng[0]), int(rng[1])
                cur = r.get(var)
                if var == "id" or isinstance(cur, frozenset):
                    continue
                r[var] = (lo, hi) if cur is None else (min(cur[0], lo), max(cur[1], hi))
        return ranges

    def _build_params(self, raw: Any) -> dict[str, dict[str, ParamSpec]]:
        params = _expect_mapping(raw, "params")
        out: dict[str, dict[str, ParamSpec]] = {}
        for fam, specs in params.items():
            where_fam = f"params.{fam}"
            if fam in self.strips:
                root = self.strips[fam].path
            elif fam in self.roots:
                root = self.roots[fam]
            else:
                raise DescriptorError("not a strip family and not in meta.roots", path=where_fam)
            specs = _expect_mapping(specs, where_fam)
            if not specs:
                raise DescriptorError("must not be empty", path=where_fam)
            ranges = self._var_ranges.setdefault(str(fam), {})
            fam_out: dict[str, ParamSpec] = {}
            for rel, spec in specs.items():
                fam_out[str(rel)] = self._build_param(str(fam), str(rel), spec, root, ranges, f"{where_fam}.{rel}")
            out[str(fam)] = fam_out
        return out

    def _build_param(self, fam: str, rel: str, spec: Any, root: str, ranges: VarRanges, where: str) -> ParamSpec:
        if not rel or rel.startswith("/") or rel.endswith("/"):
            raise DescriptorError(f"relpath must be relative and non-empty, got {rel!r}", path=where)
        spec = _expect_mapping(spec, where)
        _no_unknown_keys(spec, _SPEC_KEYS, where)
        osc = spec.get("osc")
        if osc not in _OSC_TYPES:
            raise DescriptorError(f"osc must be one of f/i/s, got {osc!r}", path=f"{where}.osc")
        if ("scale" in spec) == ("enum" in spec):
            raise DescriptorError("exactly one of scale / enum is required", path=where)
        enum_name = scale_name = None
        enum: tuple[str, ...] | None = None
        if "enum" in spec:
            enum_name = _expect_str(spec["enum"], f"{where}.enum")
            if enum_name not in self.enums:
                raise DescriptorError(f"unknown enum {enum_name!r}; known: {sorted(self.enums)}", path=f"{where}.enum")
            if osc != "i":
                raise DescriptorError(f"enum params are always osc 'i', got {osc!r}", path=f"{where}.osc")
            enum = self.enums[enum_name]
            scale = Scale("enum", values=enum)
        else:
            scale_name = _expect_str(spec["scale"], f"{where}.scale")
            if scale_name not in self.scales:
                raise DescriptorError(f"unknown scale {scale_name!r}; known: {sorted(self.scales)}", path=f"{where}.scale")
            scale = self.scales[scale_name]
            if osc == "s" and scale.kind != "str":
                raise DescriptorError(f"osc 's' needs a str scale, got {scale_name!r} ({scale.kind})", path=f"{where}.osc")
            if scale.kind == "str" and osc != "s":
                raise DescriptorError(f"str scale needs osc 's', got {osc!r}", path=f"{where}.osc")
        node_fmt = spec.get("node")
        if not isinstance(node_fmt, str) or not re.fullmatch(r"[a-z][a-z0-9]*", node_fmt):
            raise DescriptorError(f"node format must be a lowercase token, got {node_fmt!r}", path=f"{where}.node")
        tier = spec.get("tier", 1)
        if tier not in _TIERS or isinstance(tier, bool):
            raise DescriptorError(f"tier must be 0, 1 or 2, got {tier!r}", path=f"{where}.tier")
        clamp_min = clamp_max = None
        if "clamp" in spec:
            clamp = _expect_mapping(spec["clamp"], f"{where}.clamp")
            _no_unknown_keys(clamp, frozenset({"min", "max"}), f"{where}.clamp")
            if not clamp:
                raise DescriptorError("clamp needs min and/or max", path=f"{where}.clamp")
            if "min" in clamp:
                clamp_min = _expect_number(clamp["min"], f"{where}.clamp.min")
            if "max" in clamp:
                clamp_max = _expect_number(clamp["max"], f"{where}.clamp.max")
            if clamp_min is not None and clamp_max is not None and clamp_min > clamp_max:
                raise DescriptorError(f"clamp min {clamp_min} > max {clamp_max}", path=f"{where}.clamp")
            if scale.kind in ("enum", "bool", "str"):
                raise DescriptorError(f"clamp makes no sense on a {scale.kind} param", path=f"{where}.clamp")
        inverted = spec.get("inverted_mute", False)
        if inverted is not False:
            inverted = _expect_bool(inverted, f"{where}.inverted_mute")
            if inverted and scale.kind != "bool":
                raise DescriptorError("inverted_mute needs a bool scale", path=f"{where}.inverted_mute")
        _template_vars(rel, where)
        ps = ParamSpec(
            key=f"{fam}:{rel}", family=fam, relpath=rel, osc_type=str(osc), scale=scale, node_fmt=node_fmt,
            tier=int(tier), clamp_min=clamp_min, clamp_max=clamp_max, inverted_mute=bool(inverted), enum=enum,
            root_template=root, scale_name=scale_name, enum_name=enum_name, var_ranges=ranges,
        )
        ps.vars  # validates the joined template early, with the key path in the error
        return ps

    def _build_sections(self, raw: Any) -> tuple[NodeSection, ...]:
        nodes = _expect_list(raw, "nodes")
        out: list[NodeSection] = []
        for i, sec in enumerate(nodes):
            where = f"nodes[{i}]"
            sec = _expect_mapping(sec, where)
            _no_unknown_keys(sec, _NODE_KEYS, where)
            fam = _expect_str(sec.get("family"), f"{where}.family")
            if fam not in self.params:
                raise DescriptorError(f"family {fam!r} has no params", path=f"{where}.family")
            if ("section" in sec) == ("path" in sec):
                raise DescriptorError("exactly one of section / path is required", path=where)
            for_vars: dict[str, tuple[int, int]] = {}
            if "for" in sec:
                fr = _expect_mapping(sec["for"], f"{where}.for")
                for var, rng in fr.items():
                    w = f"{where}.for.{var}"
                    if var not in TEMPLATE_VARS or var == "id":
                        raise DescriptorError(f"bad for-variable {var!r}; allowed: {sorted(TEMPLATE_VARS - {'id'})}", path=w)
                    rng = _expect_list(rng, w)
                    if len(rng) != 2:
                        raise DescriptorError(f"range must be [lo, hi], got {rng!r}", path=w)
                    lo, hi = (_expect_int(x, w) for x in rng)
                    if lo > hi:
                        raise DescriptorError(f"range lo {lo} > hi {hi}", path=w)
                    for_vars[str(var)] = (lo, hi)
            if "section" in sec:
                if fam not in self.strips:
                    raise DescriptorError(f"`section` is only for strip families; {fam!r} needs an absolute `path`", path=f"{where}.section")
                section = _expect_str(sec["section"], f"{where}.section", allow_empty=True)
                if section.startswith("/") or section.endswith("/"):
                    raise DescriptorError(f"section must be relative, got {section!r}", path=f"{where}.section")
                root = self.strips[fam].path
                path_template = f"{root}/{section}" if section else root
                allowed = set(for_vars) | {"n", "id"}
                tmpl_vars = _template_vars(section, f"{where}.section")
                is_strip = True
            else:
                path_template = _expect_str(sec["path"], f"{where}.path")
                if not path_template.startswith("/") or path_template.endswith("/"):
                    raise DescriptorError(f"path must be absolute, got {path_template!r}", path=f"{where}.path")
                root = self.strips[fam].path if fam in self.strips else self.roots[fam]
                section = ""
                allowed = set(for_vars)
                tmpl_vars = _template_vars(path_template, f"{where}.path")
                is_strip = False
            bad = set(tmpl_vars) - allowed
            if bad:
                raise DescriptorError(f"template variables {sorted(bad)} are not provided by `for`", path=f"{where}.{'section' if is_strip else 'path'}")
            fields = self._check_fields(sec.get("fields"), fam, allowed, f"{where}.fields")
            fields_even = None
            if "fields_even" in sec:
                if len(for_vars) != 1:
                    raise DescriptorError("fields_even needs exactly one `for` variable", path=f"{where}.fields_even")
                fields_even = self._check_fields(sec["fields_even"], fam, allowed, f"{where}.fields_even")
            out.append(NodeSection(
                family=fam, section=section, path_template=path_template, fields=fields, fields_even=fields_even,
                for_vars=for_vars, root_template=root, is_strip_section=is_strip,
            ))
        return tuple(out)

    def _check_fields(self, raw: Any, fam: str, allowed: set[str], where: str) -> tuple[str, ...]:
        fields = _expect_list(raw, where)
        out: list[str] = []
        for j, f in enumerate(fields):
            w = f"{where}[{j}]"
            f = _expect_str(f, w)
            if f not in self.params[fam]:
                raise DescriptorError(f"{f!r} is not a declared param of {fam!r}", path=w)
            bad = set(_template_vars(f, w)) - allowed
            if bad:
                raise DescriptorError(f"template variables {sorted(bad)} are not provided by `for`", path=w)
            out.append(f)
        if len(set(out)) != len(out):
            raise DescriptorError("duplicate fields", path=where)
        return tuple(out)

    @staticmethod
    def _build_guarded(raw: Any) -> tuple[str, ...]:
        globs = _expect_list(raw, "guarded", allow_empty=True)
        out: list[str] = []
        for i, g in enumerate(globs):
            g = _expect_str(g, f"guarded[{i}]")
            if not g.startswith("/"):
                raise DescriptorError(f"glob must start with '/', got {g!r}", path=f"guarded[{i}]")
            out.append(g)
        return tuple(out)

    def _expand_nodes(self) -> tuple[NodePath, ...]:
        """Every concrete node in sweep order: families in order of first appearance in ``nodes``;
        within a strip family strip-major (all of ``/ch/01`` then ``/ch/02`` …), sections in file
        order — the order a console-saved scene file prints (scales_params.md §13)."""
        by_family: dict[str, list[tuple[int, NodeSection]]] = {}
        for i, sec in enumerate(self._sections):
            by_family.setdefault(sec.family, []).append((i, sec))
        out: list[NodePath] = []
        seen: dict[str, str] = {}
        for fam, secs in by_family.items():
            expanded: list[tuple[int, NodeSection, list[tuple[str, dict[str, Any]]]]] = []
            for i, sec in secs:
                try:
                    expanded.append((i, sec, sec.expand(self.strips)))
                except DescriptorError as e:
                    raise DescriptorError(e.message, path=f"nodes[{i}]") from None
            if fam in self.strips:
                emitted = 0
                for index in self.strips[fam].indices:
                    key = "id" if isinstance(index, str) else "n"
                    for i, sec, items in expanded:
                        for path, vars in items:
                            if vars.get(key) == index:
                                out.append(self._make_node(i, sec, path, vars, seen))
                                emitted += 1
                total = sum(len(items) for _, _, items in expanded)
                if emitted != total:
                    raise DescriptorError(f"{total - emitted} {fam} node(s) do not belong to any strip", path="nodes")
            else:
                for i, sec, items in expanded:
                    for path, vars in items:
                        out.append(self._make_node(i, sec, path, vars, seen))
        return tuple(out)

    def _make_node(self, i: int, sec: NodeSection, path: str, vars: dict[str, Any], seen: dict[str, str]) -> NodePath:
        where = f"nodes[{i}]"
        if path in seen:
            raise DescriptorError(f"node path {path!r} already produced by {seen[path]}", path=where)
        seen[path] = where
        m = re.match("^" + _template_regex(sec.root_template) + "(?=/|$)", path)
        if not m:
            raise DescriptorError(f"{path!r} is not under root {sec.root_template!r}", path=where)
        root = m.group(0)
        templates = sec.field_templates_for(vars)
        fields = tuple(_render(t, vars, where) for t in templates)
        specs = tuple(self.params[sec.family][t] for t in templates)
        node = NodePath(path=path, family=sec.family, root=root, fields=fields, specs=specs, vars=vars, section=sec)
        for addr, spec in zip(node.addresses, specs):
            if not (addr == path or addr.startswith(path + "/")):
                raise DescriptorError(f"field address {addr!r} is not under node path {path!r}", path=where)
            found = self.param_for_address(addr)
            if found is None or found[0] is not spec:
                raise DescriptorError(f"field address {addr!r} does not resolve back to {spec.key}", path=where)
        return node

    def _validate_tables(self) -> None:
        rta, geq = self.rta, self.geq
        bands = _expect_int(rta.get("bands"), "rta.bands", lo=1)
        hz = _expect_list(rta.get("band_hz"), "rta.band_hz")
        if len(hz) != bands:
            raise DescriptorError(f"band_hz has {len(hz)} entries, rta.bands says {bands}", path="rta.band_hz")
        for key in ("source_param", "pos_param", "options_param", "stat_param"):
            addr = _expect_str(rta.get(key), f"rta.{key}")
            if self.param_for_address(addr) is None:
                raise DescriptorError(f"{addr!r} is not a known parameter address", path=f"rta.{key}")
        ghz = _expect_list(geq.get("band_hz"), "geq.band_hz")
        if len(ghz) != 31:
            raise DescriptorError(f"expected 31 GEQ bands, got {len(ghz)}", path="geq.band_hz")
        gs = _expect_str(geq.get("gain_scale"), "geq.gain_scale")
        if gs not in self.scales:
            raise DescriptorError(f"unknown scale {gs!r}", path="geq.gain_scale")
        ie = _expect_str(geq.get("insert_sel_enum"), "geq.insert_sel_enum")
        if ie not in self.enums:
            raise DescriptorError(f"unknown enum {ie!r}", path="geq.insert_sel_enum")
        for key in ("fx_types_dual", "fx_types_stereo"):
            for j, t in enumerate(_expect_list(geq.get(key), f"geq.{key}")):
                if t not in self.enums.get("fx_type_58", ()) and t not in self.enums.get("fx_type_14", ()):
                    raise DescriptorError(f"{t!r} is not an fx type token", path=f"geq.{key}[{j}]")
        for key in ("ch_fader_max_db", "bus_fader_max_db", "main_fader_max_db", "send_max_db", "eq_gain_abs_max_db",
                    "relative_max_db", "relative_max_db_show_mode", "ramp_default_ms", "ramp_step_ms",
                    "writes_per_second", "confirm_token_ttl_s", "read_cache_ttl_s"):
            _expect_number(self.policy.get(key), f"policy.{key}")
        _expect_bool(self.policy.get("show_mode_default", False), "policy.show_mode_default")

    # -- lookups -----------------------------------------------------------------------------
    @property
    def families(self) -> tuple[str, ...]:
        """Every param family in file order (strip families first, then headamp, fx, config, …)."""
        return tuple(self.params)

    @property
    def strip_families(self) -> tuple[str, ...]:
        return tuple(self.strips)

    def root_template(self, family: str) -> str:
        """Root address template of a family (``/ch/{n:02d}``, ``/headamp/{n:03d}``, ``/config``)."""
        if family in self.strips:
            return self.strips[family].path
        if family in self.roots:
            return self.roots[family]
        raise DescriptorError(f"unknown family {family!r}; known: {list(self.params)}")

    def enum(self, name: str) -> tuple[str, ...]:
        try:
            return self.enums[name]
        except KeyError:
            raise DescriptorError(f"unknown enum {name!r}; known: {sorted(self.enums)}") from None

    def scale(self, name: str) -> Scale:
        try:
            return self.scales[name]
        except KeyError:
            raise DescriptorError(f"unknown scale {name!r}; known: {sorted(self.scales)}") from None

    def param(self, family: str, relpath: str) -> ParamSpec:
        """Spec by family and relpath — the template (``mix/{send:02d}/level``) or a concrete
        rendering of it (``mix/03/level``). Raises :class:`DescriptorError` when unknown."""
        fam = self.params.get(family)
        if fam is None:
            raise DescriptorError(f"unknown family {family!r}; known: {list(self.params)}")
        spec = fam.get(relpath)
        if spec is not None:
            return spec
        root = self.strips[family].roots()[0][0] if family in self.strips else self.root_template(family)
        if "{" in root:  # non-strip templated root (headamp/fx): any in-range index will do
            lo = self._var_ranges.get(family, {}).get("n", (1, 1))
            root = root.format(n=lo[0] if isinstance(lo, tuple) else 1)
        found = self.param_for_address(f"{root}/{relpath}")
        if found is None:
            raise DescriptorError(f"unknown param {relpath!r} in family {family!r}")
        return found[0]

    def param_for_address(self, address: str) -> tuple[ParamSpec, dict[str, Any]] | None:
        """Reverse lookup: ``/ch/05/mix/03/level`` → ``(params.ch['mix/{send:02d}/level'], {'n': 5, 'send': 3})``.
        ``None`` for unknown, non-canonical (``/ch/5/…``) or out-of-range addresses, and for node paths
        that are not themselves a parameter (``/ch/05/mix/03``)."""
        if not isinstance(address, str) or not address.startswith("/"):
            return None
        cache = self._addr_cache
        try:
            return cache[address]
        except KeyError:
            pass
        result: tuple[ParamSpec, dict[str, Any]] | None = None
        head = address.split("/", 2)[1] if "/" in address[1:] else address[1:]
        for spec in self._by_head.get(head, ()):
            vars = spec.match(address)
            if vars is not None:
                result = (spec, vars)
                break
        if len(cache) >= _ADDR_CACHE_MAX:
            cache.clear()
        cache[address] = result
        return result

    def address(self, family: str, relpath: str, target: Target | None = None, **vars: Any) -> str:
        """Shorthand for ``param(family, relpath).address(target, **vars)``."""
        return self.param(family, relpath).address(target, **vars)

    def is_guarded(self, address: str) -> bool:
        """True when a ``guarded`` glob matches (fnmatch, case-sensitive, ``*`` spans ``/``)."""
        return any(p.match(address) for p in self._guard_res)

    def tier_for(self, address: str) -> int:
        """max(param tier, 2 if guarded). Unknown addresses are tier 1 (see module doc)."""
        if self.is_guarded(address):
            return 2
        found = self.param_for_address(address)
        return found[0].tier if found is not None else 1

    def strip_targets(self, family: str) -> list[Target]:
        """Every :class:`Target` of a strip family in desk order (``main`` → ``main.st``, ``main.m``)."""
        st = self.strips.get(family)
        if st is None:
            raise DescriptorError(f"unknown strip family {family!r}; known: {list(self.strips)}")
        return st.targets()

    def node_sections(self) -> list[NodeSection]:
        """The ``nodes`` entries in sweep order."""
        return list(self._sections)

    def nodes(self) -> list[NodePath]:
        """Every concrete node of a full sweep, in sweep order, with fields and specs."""
        return list(self._nodes)

    def node(self, path: str) -> NodePath:
        """The :class:`NodePath` for a concrete node path; :class:`DescriptorError` when unknown."""
        try:
            return self._node_by_path[path]
        except KeyError:
            raise DescriptorError(f"{path!r} is not a node path of this device") from None

    def node_specs(self, path: str) -> tuple[ParamSpec, ...]:
        """Ordered specs of a node path's fields (what ``parse_node_line`` needs)."""
        return self.node(path).specs

    def all_node_paths(self) -> list[tuple[str, tuple[str, ...]]]:
        """``[(node path, concrete field relpaths), …]`` for a full sweep (2101 entries for the X32)."""
        return [(n.path, n.fields) for n in self._nodes]

    def node_paths_for(self, family: str | None = None, target: Target | None = None) -> list[NodePath]:
        """Nodes filtered by family and/or strip (``target``)."""
        out = []
        for n in self._nodes:
            if family is not None and n.family != family:
                continue
            if target is not None and (n.family != target.family or n.vars.get("id", n.vars.get("n")) != target.index):
                continue
            out.append(n)
        return out

    def node_formats_used(self) -> frozenset[str]:
        """Every ``node`` format token the params use (nodes.py must implement all of them)."""
        return frozenset(s.node_fmt for fam in self.params.values() for s in fam.values())

    def iter_params(self) -> Iterator[ParamSpec]:
        for fam in self.params.values():
            yield from fam.values()

    def __repr__(self) -> str:
        return f"Descriptor({self.meta.get('model')!r}, {sum(len(p) for p in self.params.values())} params, {len(self._nodes)} nodes, {self.path})"
