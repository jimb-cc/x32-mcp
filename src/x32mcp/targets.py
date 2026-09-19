"""Strip targets: the public naming for "which fader" (DESIGN.md §4).

Public tools speak ``ch.5`` / ``bus.3`` / ``main.st`` etc. with 1-based numbers as printed on the
desk. OSC paths are zero-padded (``/ch/05``) and are derived here in exactly one place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Union

Index = Union[int, str]


class TargetError(ValueError):
    pass


_FAMILIES: dict[str, dict] = {
    "ch": {"count": 32, "path": "/ch/{n:02d}", "label": "Ch {n}"},
    "auxin": {"count": 8, "path": "/auxin/{n:02d}", "label": "Aux In {n}"},
    "fxrtn": {"count": 8, "path": "/fxrtn/{n:02d}", "label": "FX Rtn {n}"},
    "bus": {"count": 16, "path": "/bus/{n:02d}", "label": "Bus {n}"},
    "mtx": {"count": 6, "path": "/mtx/{n:02d}", "label": "Matrix {n}"},
    "dca": {"count": 8, "path": "/dca/{n}", "label": "DCA {n}"},
    "main": {"ids": ("st", "m"), "path": "/main/{id}", "label": {"st": "Main LR", "m": "Main M/C"}},
}

_ALIASES: dict[str, str] = {
    "ch": "ch", "chan": "ch", "channel": "ch", "in": "ch", "input": "ch",
    "bus": "bus", "aux": "bus", "mix": "bus", "mixbus": "bus", "mon": "bus", "wedge": "bus", "iem": "bus",
    "auxin": "auxin", "aux_in": "auxin", "auxinput": "auxin",
    "fxrtn": "fxrtn", "fx": "fxrtn", "fxret": "fxrtn", "fxreturn": "fxrtn",
    "mtx": "mtx", "matrix": "mtx",
    "dca": "dca",
    "main": "main", "lr": "main", "st": "main", "stereo": "main", "master": "main", "mono": "main", "m": "main", "mc": "main",
}

_RE = re.compile(r"^\s*([a-z_]+)\s*[.\-_/ ]?\s*([a-z0-9]+)?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Target:
    family: str
    index: Index

    def __post_init__(self) -> None:
        fam = _FAMILIES.get(self.family)
        if fam is None:
            raise TargetError(f"unknown strip family {self.family!r}")
        if "ids" in fam:
            if self.index not in fam["ids"]:
                raise TargetError(f"{self.family} id must be one of {fam['ids']}, got {self.index!r}")
        else:
            if not isinstance(self.index, int) or not (1 <= self.index <= fam["count"]):
                raise TargetError(f"{self.family} number must be 1..{fam['count']}, got {self.index!r}")

    @property
    def key(self) -> str:
        return f"{self.family}.{self.index}"

    @property
    def osc_prefix(self) -> str:
        fam = _FAMILIES[self.family]
        if "ids" in fam:
            return fam["path"].format(id=self.index)
        return fam["path"].format(n=self.index)

    @property
    def label(self) -> str:
        fam = _FAMILIES[self.family]
        if "ids" in fam:
            return fam["label"][self.index]  # type: ignore[index]
        return fam["label"].format(n=self.index)  # type: ignore[union-attr]

    @property
    def is_main(self) -> bool:
        return self.family == "main"

    def __str__(self) -> str:
        return self.key


def families() -> dict[str, dict]:
    """Read-only view of the family table (counts/paths)."""
    return {k: dict(v) for k, v in _FAMILIES.items()}


def parse_target(s: Union[str, int, Target], *, default_family: str = "ch") -> Target:
    """Parse ``ch.5`` / ``bus 3`` / ``5`` / ``main`` / ``lr`` / ``mono`` … into a :class:`Target`.

    Raises :class:`TargetError` with a helpful message on anything else.
    """
    if isinstance(s, Target):
        return s
    if isinstance(s, bool):
        raise TargetError("target must be a string or channel number")
    if isinstance(s, int):
        return Target(default_family, s)
    if not isinstance(s, str):
        raise TargetError(f"target must be a string like 'ch.5' or 'bus.3', got {type(s).__name__}")
    text = s.strip()
    if not text:
        raise TargetError("empty target")
    if text.isdigit():
        return Target(default_family, int(text))
    m = _RE.match(text)
    if not m:
        raise TargetError(f"cannot parse target {s!r}; use e.g. 'ch.5', 'bus.3', 'main.st', 'main.m', 'dca.1'")
    word = m.group(1).lower()
    rest = (m.group(2) or "").lower()
    fam = _ALIASES.get(word)
    if fam is None:
        raise TargetError(f"unknown strip family {word!r} in {s!r}; known: ch, bus, auxin, fxrtn, mtx, dca, main")
    if fam == "main":
        if word in {"mono", "m", "mc"} or rest in {"m", "mono", "mc", "c"}:
            return Target("main", "m")
        if rest in {"", "st", "lr", "stereo"}:
            return Target("main", "st")
        raise TargetError(f"main target must be 'main.st' (LR) or 'main.m' (mono/centre), got {s!r}")
    if not rest:
        raise TargetError(f"{word!r} needs a number, e.g. '{fam}.3'")
    if not rest.isdigit():
        raise TargetError(f"{word!r} number must be an integer, got {rest!r}")
    return Target(fam, int(rest))
