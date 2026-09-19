"""Patch plans: who is on which channel, as data (DESIGN.md §16, BRIEF.md §5 "Patch plans are data").

A patch plan is a YAML or CSV file under ``patches/`` describing the input list of one band:
channel number, strip name, colour, input source and the CFS² metadata the desk cannot hold
(``mic`` — a live stage microphone that can feed back, ``owner`` — whose instrument/voice,
``monitor_bus`` — the mix bus that owner listens to, free-text ``notes``). ``apply_patch_plan``
labels the strips (Tier 1: name + colour) and, only with ``include_source=True``, patches the
input sources (Tier 2 — the server guards that with a confirmation token, this module does not).
``export_patch_plan`` reads names/colours/sources back from the desk and merges the metadata
from an earlier plan so the file stays the single place the human-only facts live.

Two forms, documented in ``patches/README.md``::

    band: The Molecules
    venue: Festival main stage
    channels:
      - {channel: 1, name: Kick, color: RD, source: In01, mic: true, owner: Ray, monitor_bus: 2, notes: "…"}

    channel,name,color,source,mic,owner,monitor_bus,notes      # CSV header (colour / "monitor bus" accepted)

Decisions where DESIGN.md is silent:

* ``mic`` is ``bool | None`` (DESIGN §16 says ``bool``): a blank cell / missing key means *unknown*
  so ``provision.discover_mics`` can tell "declared not a mic" from "nobody said". Accepted
  spellings: true/false, yes/no, y/n, on/off, 1/0, x (= true), t/f.
* Validation is strict on rows (unknown keys, bad numbers, duplicate channels are errors, all
  problems reported in one :class:`PatchError`) and lenient on the file (unknown top-level keys
  and unknown CSV columns are warnings on ``PatchPlan.warnings``). Names longer than the desk's
  12 characters (scales_params.md §4.1) are a warning, not an error: ``Desk.label`` truncates
  and reports ``truncated``.
* Colours are normalised to the descriptor's ``color`` enum tokens (scales_params.md §11.1):
  tokens case-insensitively (``rd`` → ``RD``, ``rdi`` → ``RDi``), the friendly names
  red/green/yellow/blue/magenta/cyan/white/off (+ ``i``/``inv``/``inverted`` suffix for the
  inverted variants) and the raw 0..15 index. Sources are normalised to the ``ch_source`` tokens
  (§11.3): ``In01``/``in 1`` → ``IN01``, ``aux 2`` → ``AUX2``, ``usb l`` → ``USBL``, ``fx1l`` →
  ``FX1L``, ``bus 3`` → ``BUS03``, or the raw 0..64 index the desk prints in its node text.
* ``apply_patch_plan`` first sweeps the 32 ``/ch/NN/config`` sections in one ``desk.dump`` and
  writes only what differs (re-applying a plan is free, and an X32-Edit operator sees no churn);
  a section that did not answer is written unconditionally. Per-row ``DeskError`` failures are
  collected in ``failed`` and the run continues; ``NOT_CONNECTED`` aborts (``aborted: True``);
  ``PolicyError`` propagates unchanged (session-level refusals are not per-row problems).
* ``export_patch_plan`` exports all 32 channels unless ``channels=`` narrows it, and writes YAML
  or CSV by the target's suffix; the returned plan's ``source_file`` is the written path.
* The descriptor is only needed for the two enums; ``load_patch_plan`` loads the default
  ``device.yaml`` lazily (memoised) unless a ``descriptor=`` is passed.
"""

from __future__ import annotations

import csv
import functools
import io
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from .descriptor import Descriptor
from .desk import DeskError
from .targets import Target

__all__ = [
    "COLUMNS",
    "PatchError",
    "PatchRow",
    "PatchPlan",
    "load_patch_plan",
    "build_patch_plan",
    "dumps_patch_plan",
    "write_patch_plan",
    "apply_patch_plan",
    "export_patch_plan",
    "normalise_color",
    "normalise_source",
    "parse_mic",
]

log = logging.getLogger(__name__)

COLUMNS: tuple[str, ...] = ("channel", "name", "color", "source", "mic", "owner", "monitor_bus", "notes")
CH_COUNT = 32
BUS_COUNT = 16
NAME_MAX = 12  # scales_params.md §4.1: /ch/NN/config/name is <= 12 characters

# Friendly colour names → descriptor tokens (scales_params.md §11.1; "i" suffix = inverted).
_COLOR_ALIASES: dict[str, str] = {
    "red": "RD", "green": "GN", "yellow": "YE", "blue": "BL",
    "magenta": "MG", "cyan": "CY", "white": "WH", "off": "OFF", "none": "OFF",
}
_INVERTED_SUFFIXES = (" inverted", "-inverted", "_inverted", " inv", "-inv", "_inv", "i")
_INVERTED_PREFIXES = ("inverted ", "inverted-", "inverted_", "inv ", "inv-", "inv_")
_SOURCE_RE = re.compile(r"^(IN|INPUT|CH|AUX|AUXIN|USB|FX|BUS|MIX)[\s_.-]*(\d{1,2})?[\s_.-]*([LR])?$")
_NUMBER_RE = re.compile(r"^(?:[A-Za-z]+)?[\s_.-]*(\d{1,3})$")
_MIC_TRUE = frozenset({"true", "yes", "y", "1", "x", "on", "t"})
_MIC_FALSE = frozenset({"false", "no", "n", "0", "off", "f"})
_COLUMN_ALIASES: dict[str, str] = {
    "channel": "channel", "ch": "channel", "chan": "channel",
    "name": "name",
    "color": "color", "colour": "color",
    "source": "source", "input": "source",
    "mic": "mic",
    "owner": "owner",
    "monitor_bus": "monitor_bus", "monitor bus": "monitor_bus", "monitorbus": "monitor_bus", "monitor-bus": "monitor_bus", "bus": "monitor_bus",
    "notes": "notes", "note": "notes",
}


class PatchError(ValueError):
    """A patch file that cannot be used. ``problems`` lists every issue found (the message joins them)."""

    def __init__(self, problems: Sequence[str] | str, *, path: Path | None = None) -> None:
        items = [problems] if isinstance(problems, str) else list(problems)
        prefix = f"{path}: " if path is not None else ""
        super().__init__(prefix + "; ".join(items))
        self.problems: list[str] = items
        self.path = path


@dataclass
class PatchRow:
    """One channel of a patch plan. ``channel`` 1..32; ``color`` a descriptor token (``RD``) or None;
    ``source`` a ``ch_source`` token (``IN05``) or None; ``mic`` True/False/None (unknown);
    ``monitor_bus`` 1..16 or None."""

    channel: int
    name: str
    color: str | None = None
    source: str | None = None
    mic: bool | None = None
    owner: str | None = None
    monitor_bus: int | None = None
    notes: str | None = None

    @property
    def target(self) -> Target:
        return Target("ch", self.channel)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PatchPlan:
    """A loaded plan: ``rows`` sorted by channel, ``band``/``venue`` from the YAML header (None for
    CSV), ``source_file`` the file it came from (or was written to), ``warnings`` non-fatal notes."""

    rows: list[PatchRow]
    band: str | None
    venue: str | None
    source_file: Path
    warnings: list[str] = field(default_factory=list)

    def row(self, channel: int) -> PatchRow | None:
        """The row for ``channel`` (1..32) or None."""
        for r in self.rows:
            if r.channel == channel:
                return r
        return None

    @property
    def by_channel(self) -> dict[int, PatchRow]:
        return {r.channel: r for r in self.rows}

    def mics(self) -> list[PatchRow]:
        """Rows declared ``mic: true``."""
        return [r for r in self.rows if r.mic is True]

    def for_bus(self, bus: int) -> list[PatchRow]:
        """Rows whose owner listens on monitor bus ``bus`` (1..16)."""
        return [r for r in self.rows if r.monitor_bus == bus]

    def to_dict(self) -> dict[str, Any]:
        return {
            "band": self.band,
            "venue": self.venue,
            "source_file": str(self.source_file),
            "channels": [r.to_dict() for r in self.rows],
            "warnings": list(self.warnings),
        }


# -- value normalisation --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _default_descriptor() -> Descriptor:
    return Descriptor.load()


def _tokens(descriptor: Descriptor | None, enum: str) -> tuple[str, ...]:
    return (descriptor or _default_descriptor()).enum(enum)


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def normalise_color(value: Any, *, descriptor: Descriptor | None = None) -> str | None:
    """A colour in any accepted spelling → descriptor token (``RD``, ``CYi`` …); blank → None.
    Raises :class:`PatchError` for anything else."""
    if _blank(value):
        return None
    tokens = _tokens(descriptor, "color")
    if isinstance(value, bool):
        # PyYAML (YAML 1.1) reads an unquoted OFF/off/no as False: that is the OFF colour.
        if value is False and "OFF" in tokens:
            return "OFF"
        raise PatchError(f"colour must be a name or token, not {value!r} (quote it in YAML)")
    if isinstance(value, int):
        if 0 <= value < len(tokens):
            return tokens[value]
        raise PatchError(f"colour index {value} is out of range 0..{len(tokens) - 1}")
    text = str(value).strip()
    lower = text.lower()
    by_lower = {t.lower(): t for t in tokens}
    if lower in by_lower:
        return by_lower[lower]
    base, inverted = lower, False
    for pre in _INVERTED_PREFIXES:
        if base.startswith(pre) and base[len(pre):] in _COLOR_ALIASES:
            base, inverted = base[len(pre):], True
            break
    else:
        for suf in _INVERTED_SUFFIXES:
            if base.endswith(suf) and base[: -len(suf)] in _COLOR_ALIASES:
                base, inverted = base[: -len(suf)], True
                break
    token = _COLOR_ALIASES.get(base)
    if token is not None and inverted:
        token += "i"
    if token is None or token not in tokens:
        raise PatchError(
            f"unknown colour {text!r}; use one of {', '.join(tokens)} or "
            + "/".join(k for k in _COLOR_ALIASES if k != "none")
        )
    return token


def normalise_source(value: Any, *, descriptor: Descriptor | None = None) -> str | None:
    """An input source in any accepted spelling → ``ch_source`` token (``IN05``, ``AUX2``, ``USBL``,
    ``FX1L``, ``BUS03``, ``OFF``); blank → None. A bare integer is the desk's own 0..64 index
    (scales_params.md §11.3). Raises :class:`PatchError` otherwise."""
    if _blank(value):
        return None
    tokens = _tokens(descriptor, "ch_source")
    if isinstance(value, bool):
        if value is False and "OFF" in tokens:  # unquoted OFF in YAML 1.1 is False
            return "OFF"
        raise PatchError(f"source must be a token such as 'In01', not {value!r} (quote it in YAML)")
    text = str(value).strip()
    if isinstance(value, int) or text.isdigit():
        idx = int(text)
        if 0 <= idx < len(tokens):
            return tokens[idx]
        raise PatchError(f"source index {idx} is out of range 0..{len(tokens) - 1}")
    upper = text.upper()
    if upper in tokens:
        return upper
    m = _SOURCE_RE.match(upper)
    candidate: str | None = None
    if m:
        fam, num, side = m.group(1), m.group(2), m.group(3)
        if fam in ("IN", "INPUT", "CH") and num and not side:
            candidate = f"IN{int(num):02d}"
        elif fam in ("AUX", "AUXIN") and num and not side:
            candidate = f"AUX{int(num)}"
        elif fam == "USB" and side and not num:
            candidate = f"USB{side}"
        elif fam == "FX" and num and side:
            candidate = f"FX{int(num)}{side}"
        elif fam in ("BUS", "MIX") and num and not side:
            candidate = f"BUS{int(num):02d}"
    if candidate is None or candidate not in tokens:
        raise PatchError(f"unknown source {text!r}; use OFF, In01..In32, Aux1..6, USBL/USBR, FX1L..FX4R or Bus01..16")
    return candidate


def parse_mic(value: Any) -> bool | None:
    """true/false/yes/no/y/n/on/off/1/0/x/t/f (any case) → bool; blank → None (unknown)."""
    if _blank(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value in (0, 1):
            return bool(value)
        raise PatchError(f"mic must be true/false, not {value!r}")
    text = str(value).strip().lower()
    if text in _MIC_TRUE:
        return True
    if text in _MIC_FALSE:
        return False
    raise PatchError(f"mic must be true/false/yes/no/1/0/x or blank, not {value!r}")


def _parse_number(value: Any, what: str, lo: int, hi: int, *, required: bool) -> int | None:
    """``5`` / ``"5"`` / ``"ch 5"`` / ``"bus 3"`` → int in ``lo..hi``; blank → None unless required."""
    if _blank(value):
        if required:
            raise PatchError(f"{what} is required")
        return None
    if isinstance(value, bool):
        raise PatchError(f"{what} must be a number, not {value!r}")
    if isinstance(value, int):
        n = value
    elif isinstance(value, float) and value.is_integer():
        n = int(value)
    else:
        m = _NUMBER_RE.match(str(value).strip())
        if not m:
            raise PatchError(f"{what} must be a number 1..{hi}, not {value!r}")
        n = int(m.group(1))
    if not (lo <= n <= hi):
        raise PatchError(f"{what} {n} is out of range {lo}..{hi}")
    return n


def _text(value: Any) -> str | None:
    if _blank(value):
        return None
    return str(value).strip()


# -- building / validating -----------------------------------------------------------------------


def _normalise_keys(raw: Mapping[str, Any], where: str, problems: list[str], warnings: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in raw.items():
        key = _COLUMN_ALIASES.get(str(k).strip().lower())
        if key is None:
            problems.append(f"{where}: unknown field {k!r} (known: {', '.join(COLUMNS)})")
            continue
        if key in out:
            problems.append(f"{where}: field {key!r} given twice")
        out[key] = v
    return out


def _row_from_mapping(
    raw: Mapping[str, Any], where: str, problems: list[str], warnings: list[str], descriptor: Descriptor | None
) -> tuple[PatchRow | None, int | None]:
    """→ (row or None when the row has problems, the parsed channel number when it had one)."""
    data = _normalise_keys(raw, where, problems, warnings)
    errs: list[str] = []

    def take(fn: Any, key: str, *args: Any, **kw: Any) -> Any:
        try:
            return fn(data.get(key), *args, **kw)
        except PatchError as e:
            errs.append(f"{key}: {e.problems[0]}")
            return None

    channel = take(_parse_number, "channel", "channel", 1, CH_COUNT, required=True)
    if "name" not in data:
        errs.append("name: is required (use an empty string to clear the strip name)")
    name = "" if _blank(data.get("name")) else str(data.get("name")).strip()
    color = take(normalise_color, "color", descriptor=descriptor)
    source = take(normalise_source, "source", descriptor=descriptor)
    mic = take(parse_mic, "mic")
    monitor_bus = take(_parse_number, "monitor_bus", "monitor_bus", 1, BUS_COUNT, required=False)
    owner = _text(data.get("owner"))
    notes = _text(data.get("notes"))
    label = where if channel is None else f"{where} (channel {channel})"
    if errs:
        problems.extend(f"{label}: {e}" for e in errs)
        return None, channel
    if len(name) > NAME_MAX:
        warnings.append(f"{label}: name {name!r} is longer than {NAME_MAX} characters and will be truncated to {name[:NAME_MAX]!r}")
    return PatchRow(channel=channel, name=name, color=color, source=source, mic=mic, owner=owner, monitor_bus=monitor_bus, notes=notes), channel


def build_patch_plan(
    rows: Iterable[Mapping[str, Any] | PatchRow],
    *,
    band: str | None = None,
    venue: str | None = None,
    source_file: Path | str = "<memory>",
    descriptor: Descriptor | None = None,
    warnings: Sequence[str] = (),
) -> PatchPlan:
    """Validate row mappings (or ready :class:`PatchRow` objects) into a :class:`PatchPlan`, sorted
    by channel. Every problem is collected and raised together as one :class:`PatchError`."""
    path = Path(source_file)
    problems: list[str] = []
    warns: list[str] = list(warnings)
    out: list[PatchRow] = []
    seen: set[int] = set()
    for i, raw in enumerate(rows, 1):
        where = f"row {i}"
        if isinstance(raw, PatchRow):
            raw = raw.to_dict()
        if not isinstance(raw, Mapping):
            problems.append(f"{where}: expected a mapping of fields, got {type(raw).__name__}")
            continue
        row, channel = _row_from_mapping(raw, where, problems, warns, descriptor)
        if channel is not None:  # duplicates are reported even when one copy has other problems
            if channel in seen:
                problems.append(f"channel {channel} appears more than once")
            seen.add(channel)
        if row is not None:
            out.append(row)
    if not out and not problems:
        problems.append("no channels in the plan")
    if problems:
        raise PatchError(problems, path=path)
    out.sort(key=lambda r: r.channel)
    return PatchPlan(rows=out, band=_text(band), venue=_text(venue), source_file=path, warnings=warns)


def _rows_from_yaml(text: str, path: Path, warnings: list[str]) -> tuple[list[Any], str | None, str | None]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise PatchError(f"invalid YAML: {e}", path=path) from None
    if isinstance(data, list):  # a bare list of rows is accepted
        return data, None, None
    if not isinstance(data, Mapping):
        raise PatchError("top level must be a mapping with 'channels' (or a list of channel rows)", path=path)
    rows = data.get("channels")
    if rows is None:
        raise PatchError("missing 'channels' list", path=path)
    if not isinstance(rows, list):
        raise PatchError(f"'channels' must be a list of rows, got {type(rows).__name__}", path=path)
    for k in data:
        if k not in ("band", "venue", "channels"):
            warnings.append(f"ignoring unknown top-level key {k!r}")
    return rows, _text(data.get("band")), _text(data.get("venue"))


def _rows_from_csv(text: str, path: Path, warnings: list[str]) -> list[dict[str, Any]]:
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise PatchError("empty CSV", path=path) from None
    keys: list[str | None] = []
    for col in header:
        key = _COLUMN_ALIASES.get(col.strip().lower())
        if key is None:
            warnings.append(f"ignoring unknown CSV column {col.strip()!r}")
        keys.append(key)
    if "channel" not in keys or "name" not in keys:
        raise PatchError(f"CSV header must include 'channel' and 'name' (expected: {','.join(COLUMNS)})", path=path)
    rows: list[dict[str, Any]] = []
    for cells in reader:
        if not any(c.strip() for c in cells):
            continue
        row: dict[str, Any] = {}
        for key, cell in zip(keys, cells):
            if key is not None:
                row[key] = cell
        for key in ("channel", "name"):
            row.setdefault(key, "")
        rows.append(row)
    return rows


def load_patch_plan(path: Path | str, *, descriptor: Descriptor | None = None) -> PatchPlan:
    """Load and validate a ``.yaml``/``.yml`` or ``.csv`` patch plan (UTF-8, BOM tolerated).
    Raises :class:`PatchError` (all problems at once) or ``OSError`` for an unreadable file."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix not in (".yaml", ".yml", ".csv"):
        raise PatchError(f"unsupported patch file type {suffix or '(none)'!r}; use .yaml or .csv", path=p)
    text = p.read_text(encoding="utf-8-sig")
    warnings: list[str] = []
    if suffix == ".csv":
        rows, band, venue = _rows_from_csv(text, p, warnings), None, None
    else:
        rows, band, venue = _rows_from_yaml(text, p, warnings)
    plan = build_patch_plan(rows, band=band, venue=venue, source_file=p, descriptor=descriptor, warnings=warnings)
    for w in plan.warnings:
        log.warning("%s: %s", p, w)
    return plan


# -- writing ----------------------------------------------------------------------------------------


def _row_yaml(r: PatchRow) -> dict[str, Any]:
    d: dict[str, Any] = {"channel": r.channel, "name": r.name}
    for k in ("color", "source", "mic", "owner", "monitor_bus", "notes"):
        v = getattr(r, k)
        if v is not None:
            d[k] = v
    return d


def dumps_patch_plan(plan: PatchPlan, fmt: str = "yaml") -> str:
    """Render a plan as ``yaml`` or ``csv`` text (the two forms :func:`load_patch_plan` reads)."""
    f = fmt.lower().lstrip(".")
    if f in ("yaml", "yml"):
        doc: dict[str, Any] = {}
        if plan.band is not None:
            doc["band"] = plan.band
        if plan.venue is not None:
            doc["venue"] = plan.venue
        doc["channels"] = [_row_yaml(r) for r in plan.rows]
        return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False, width=120)
    if f == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(COLUMNS)
        for r in plan.rows:
            mic = "" if r.mic is None else ("true" if r.mic else "false")
            w.writerow([r.channel, r.name, r.color or "", r.source or "", mic, r.owner or "", "" if r.monitor_bus is None else r.monitor_bus, r.notes or ""])
        return buf.getvalue()
    raise PatchError(f"unsupported format {fmt!r}; use 'yaml' or 'csv'")


def write_patch_plan(plan: PatchPlan, path: Path | str) -> Path:
    """Write ``plan`` to ``path`` (format by suffix, parent directories created); returns the path."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix not in (".yaml", ".yml", ".csv"):
        raise PatchError(f"unsupported patch file type {suffix or '(none)'!r}; use .yaml or .csv", path=p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps_patch_plan(plan, suffix), encoding="utf-8")
    return p


# -- desk I/O ---------------------------------------------------------------------------------------


def _config_path(channel: int) -> str:
    return f"/ch/{channel:02d}/config"


async def _current_config(desk: Any, channels: Sequence[int]) -> tuple[Any, set[int]]:
    """One ``/node`` sweep of the wanted ``/ch/NN/config`` sections → (DeskState, channels that did
    not answer)."""
    state = await desk.dump(sections=[_config_path(n) for n in channels])
    missing = {n for n in channels if _config_path(n) in set(state.missing)}
    return state, missing


async def apply_patch_plan(desk: Any, plan: PatchPlan, *, include_source: bool = False, channels: Iterable[int] | None = None) -> dict[str, Any]:
    """Label the plan's channels (name + colour, Tier 1) and, with ``include_source``, patch their
    input sources (Tier 2 — the caller must have confirmed). Only values that differ from the desk
    are written. ``channels`` restricts the rows applied. Returns
    ``{file, band, venue, rows, include_source, applied: [{channel, target, name, color, source?,
    changed: [...], truncated?}], unchanged: [channels], skipped_source: [channels], failed:
    [{channel, target, code, message}], writes, aborted, summary}``."""
    wanted = None if channels is None else {int(c) for c in channels}
    rows = [r for r in plan.rows if wanted is None or r.channel in wanted]
    state, unread = await _current_config(desk, [r.channel for r in rows]) if rows else (None, set())
    applied: list[dict[str, Any]] = []
    unchanged: list[int] = []
    skipped_source: list[int] = []
    failed: list[dict[str, Any]] = []
    writes = 0
    aborted = False
    for r in rows:
        t = r.target
        base = _config_path(r.channel)
        fresh = r.channel not in unread and state is not None
        cur_name = state.get(base + "/name") if fresh else None
        cur_color = state.get(base + "/color") if fresh else None
        cur_source = state.get(base + "/source") if fresh else None
        name = r.name if (not fresh or cur_name != r.name[:NAME_MAX]) else None
        color = r.color if (r.color is not None and (not fresh or cur_color != r.color)) else None
        source = r.source if (include_source and r.source is not None and (not fresh or cur_source != r.source)) else None
        if r.source is not None and not include_source:
            skipped_source.append(r.channel)
        if name is None and color is None and source is None:
            unchanged.append(r.channel)
            continue
        item: dict[str, Any] = {"channel": r.channel, "target": t.key, "name": r.name, "color": r.color, "changed": []}
        try:
            if name is not None or color is not None:
                res = await desk.label(t, name=name, color=color)
                item["changed"] += [k for k in ("name", "color") if k in res.get("applied", {})]
                writes += len(res.get("applied", {}))
                if res.get("truncated"):
                    item["truncated"] = True
            if source is not None:
                res = await desk.set_source(t, source)
                item["source"] = res.get("source", source)
                item["changed"].append("source")
                writes += 1
        except DeskError as e:
            failed.append({"channel": r.channel, "target": t.key, **e.to_dict()})
            if e.code == "NOT_CONNECTED":
                aborted = True
                break
            continue
        applied.append(item)
    truncated = [i["channel"] for i in applied if i.get("truncated")]
    parts = [f"{len(applied)} channel(s) labelled", f"{len(unchanged)} unchanged"]
    if include_source:
        parts.append(f"{sum('source' in i['changed'] for i in applied)} source(s) patched")
    elif skipped_source:
        parts.append(f"{len(skipped_source)} source(s) not patched (include_source=false)")
    if truncated:
        parts.append(f"{len(truncated)} name(s) truncated to {NAME_MAX} chars")
    if failed:
        parts.append(f"{len(failed)} failed")
    if aborted:
        parts.append("ABORTED: desk not connected")
    summary = f"{plan.band or plan.source_file.name}: " + ", ".join(parts)
    log.info("apply_patch_plan %s: %s", plan.source_file, summary)
    return {
        "file": str(plan.source_file),
        "band": plan.band,
        "venue": plan.venue,
        "rows": len(rows),
        "include_source": include_source,
        "applied": applied,
        "unchanged": unchanged,
        "skipped_source": skipped_source,
        "truncated": truncated,
        "failed": failed,
        "writes": writes,
        "aborted": aborted,
        "summary": summary,
    }


async def export_patch_plan(
    desk: Any,
    path: Path | str,
    *,
    merge_with: PatchPlan | None = None,
    channels: Iterable[int] | None = None,
    band: str | None = None,
    venue: str | None = None,
) -> PatchPlan:
    """Read names/colours/sources of ``channels`` (default all 32) from the desk, merge
    ``mic``/``owner``/``monitor_bus``/``notes`` (and band/venue unless given) from ``merge_with``,
    write the plan to ``path`` (YAML or CSV by suffix) and return it. Raises ``DeskError('TIMEOUT')``
    when a channel's config section did not answer."""
    p = Path(path)
    if p.suffix.lower() not in (".yaml", ".yml", ".csv"):
        raise PatchError(f"unsupported patch file type {p.suffix or '(none)'!r}; use .yaml or .csv", path=p)
    if channels is None:
        chans = list(range(1, CH_COUNT + 1))
    else:
        chans = sorted({_parse_number(c, "channel", 1, CH_COUNT, required=True) for c in channels})  # type: ignore[misc]
        if not chans:
            raise PatchError("no channels to export")
    state, missing = await _current_config(desk, chans)
    if missing:
        raise DeskError("TIMEOUT", f"desk did not answer the config of channel(s) {sorted(missing)}", channels=sorted(missing))
    rows: list[PatchRow] = []
    for n in chans:
        base = _config_path(n)
        prev = merge_with.row(n) if merge_with is not None else None
        rows.append(
            PatchRow(
                channel=n,
                name=str(state.get(base + "/name") or ""),
                color=state.get(base + "/color"),
                source=state.get(base + "/source"),
                mic=prev.mic if prev else None,
                owner=prev.owner if prev else None,
                monitor_bus=prev.monitor_bus if prev else None,
                notes=prev.notes if prev else None,
            )
        )
    plan = PatchPlan(
        rows=rows,
        band=_text(band) if band is not None else (merge_with.band if merge_with else None),
        venue=_text(venue) if venue is not None else (merge_with.venue if merge_with else None),
        source_file=p,
    )
    write_patch_plan(plan, p)
    log.info("exported %d channel(s) to %s", len(rows), p)
    return plan
