"""OSC 1.0 codec for the X32 (DESIGN.md §5; wire facts from docs/research/transport.md §1).

Pure, synchronous, side-effect free. Only the four basic OSC types the X32 uses exist here:
``i`` int32, ``f`` float32, ``s`` string, ``b`` blob. Everything is big-endian and 4-byte
aligned on the wire. Blob *contents* are returned raw — the little-endian words inside
``/meters`` blobs are decoded in ``meters.py``.

Decisions not fixed by DESIGN.md:

* ``encode(addr)`` with no arguments and no explicit ``typetags`` emits the *bare* padded
  address (``/info~~~``, 8 bytes): the form every Maillot tool sends and the desk accepts
  (transport.md §1.2). Pass ``typetags=","`` for the OSC-1.0-compliant empty tag string.
* ``decode`` returns ``typetags=","`` when the wire carries an empty tag string and ``""`` when
  it carries none at all; both give ``args=()``. Anything after the address that is not a
  ``,`` is treated as "no arguments" (the emulator's own rule, transport.md §1.2).
* Addresses must be printable ASCII (0x20-0x7E) but need not start with ``/``: the X32's
  ``/node`` reply address is ``node`` (transport.md §6.2).
* Strings are UTF-8 on the wire; decoding uses ``errors="replace"`` so a stray byte in a
  desk name can never raise. Strings may not contain NUL.
* OSC 1.1 no-payload tags (``T F N I``) and everything else raise :class:`OscError`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

__all__ = ["OscError", "OscMessage", "decode", "encode"]

_INT = struct.Struct(">i")
_UINT = struct.Struct(">I")
_FLOAT = struct.Struct(">f")
_TAGS = frozenset("ifsb")
_COMMA = 0x2C


class OscError(ValueError):
    """Malformed OSC bytes, or an argument that cannot be encoded."""


@dataclass(frozen=True)
class OscMessage:
    """One decoded datagram.

    ``args`` holds ``int`` / ``float`` / ``str`` / ``bytes`` (blob) in wire order.
    ``typetags`` is the tag string *including* its leading comma (``",sif"``), ``","`` for an
    empty tag string, or ``""`` when the datagram carried no tag string at all.
    """

    address: str
    args: tuple[Any, ...]
    typetags: str

    def __str__(self) -> str:
        parts = [self.address]
        if self.typetags:
            parts.append(self.typetags)
        for a in self.args:
            if isinstance(a, bytes):
                parts.append(f"<blob {len(a)} B>")
            elif isinstance(a, str):
                parts.append(f'"{a}"')
            elif isinstance(a, float):
                parts.append(format(a, ".6g"))
            else:
                parts.append(str(a))
        return " ".join(parts)


# -- helpers --------------------------------------------------------------------------


def _pad(n: int) -> int:
    """Bytes of NUL padding needed to round ``n`` up to a multiple of 4."""
    return (-n) & 3


def _pack_string(s: str) -> bytes:
    if "\x00" in s:
        raise OscError("string contains NUL")
    b = s.encode("utf-8") + b"\x00"
    return b + b"\x00" * _pad(len(b))


def _infer_tag(a: Any) -> str:
    if isinstance(a, (bool, int)):
        return "i"
    if isinstance(a, float):
        return "f"
    if isinstance(a, str):
        return "s"
    if isinstance(a, (bytes, bytearray, memoryview)):
        return "b"
    raise OscError(f"unsupported argument type {type(a).__name__}")


def _pack_arg(tag: str, a: Any) -> bytes:
    if tag == "i":
        if isinstance(a, bool):
            a = int(a)
        elif isinstance(a, float):
            if not a.is_integer():
                raise OscError(f"cannot send {a!r} as int32")
            a = int(a)
        elif not isinstance(a, int):
            raise OscError(f"tag 'i' needs an int, got {type(a).__name__}")
        try:
            return _INT.pack(a)
        except struct.error as e:
            raise OscError(f"int32 out of range: {a}") from e
    if tag == "f":
        if not isinstance(a, (int, float)):  # bool is an int: True -> 1.0
            raise OscError(f"tag 'f' needs a number, got {type(a).__name__}")
        try:
            return _FLOAT.pack(float(a))
        except (OverflowError, struct.error) as e:
            raise OscError(f"float32 out of range: {a}") from e
    if tag == "s":
        if not isinstance(a, str):
            raise OscError(f"tag 's' needs a str, got {type(a).__name__}")
        return _pack_string(a)
    if tag == "b":
        if not isinstance(a, (bytes, bytearray, memoryview)):
            raise OscError(f"tag 'b' needs bytes, got {type(a).__name__}")
        b = bytes(a)
        if len(b) > 0x7FFFFFFF:
            raise OscError("blob too large")
        return _UINT.pack(len(b)) + b + b"\x00" * _pad(len(b))
    raise OscError(f"unsupported typetag {tag!r}")


def _check_address(address: Any) -> None:
    if not isinstance(address, str) or not address:
        raise OscError("address must be a non-empty string")
    if not all(0x20 <= ord(c) < 0x7F for c in address):
        raise OscError(f"address must be printable ASCII: {address!r}")


# -- public API -----------------------------------------------------------------------


def encode(address: str, *args: int | float | str | bytes, typetags: str | None = None) -> bytes:
    """Build one OSC datagram.

    Tags are inferred (bool/int -> ``i``, float -> ``f``, str -> ``s``, bytes -> ``b``) unless
    ``typetags`` is given (with or without the leading comma), which must have one tag per
    argument and coerces: an int/integral float under ``f``/``i`` is converted, anything else
    raises :class:`OscError`. int32 range is -2**31..2**31-1.

    With no args and ``typetags=None`` the bare padded address is returned (a GET request,
    transport.md §1.2). ``typetags=","`` appends the empty tag string instead.
    """
    _check_address(address)
    out = bytearray(_pack_string(address))
    if typetags is None:
        if not args:
            return bytes(out)
        tags = "".join(_infer_tag(a) for a in args)
    else:
        tags = typetags[1:] if typetags.startswith(",") else typetags
        if len(tags) != len(args):
            raise OscError(f"typetags {typetags!r} do not match {len(args)} argument(s)")
        unknown = set(tags) - _TAGS
        if unknown:
            raise OscError(f"unsupported typetag(s) {''.join(sorted(unknown))!r}")
    out += _pack_string("," + tags)
    for tag, a in zip(tags, args):
        out += _pack_arg(tag, a)
    return bytes(out)


def decode(data: bytes) -> OscMessage:
    """Parse one datagram. Tolerant of a missing tag string, missing final padding and
    trailing garbage; raises :class:`OscError` (and nothing else) on anything malformed,
    on unknown tags, and on ``#bundle`` packets (``OscError("bundle")``).
    """
    try:
        buf = bytes(data)
    except TypeError as e:
        raise OscError(f"expected bytes, got {type(data).__name__}") from e
    n = len(buf)
    if n == 0:
        raise OscError("empty datagram")
    if buf.startswith(b"#bundle"):
        raise OscError("bundle")  # never sent by the X32 or its tools (transport.md §1.3)

    end = buf.find(b"\x00")
    if end < 0:
        raise OscError("unterminated address")
    if end == 0:
        raise OscError("empty address")
    raw_addr = buf[:end]
    if not all(0x20 <= c < 0x7F for c in raw_addr):
        raise OscError("address is not printable ASCII")
    address = raw_addr.decode("ascii")
    pos = end + 1
    pos += _pad(pos)

    # per docs/research/transport.md §1.2: a bare padded address is a valid GET; anything that
    # does not start with ',' here is treated the same way (the emulator's own rule).
    if pos >= n or buf[pos] != _COMMA:
        return OscMessage(address, (), "")

    end = buf.find(b"\x00", pos)
    if end < 0:
        raise OscError("unterminated typetag string")
    typetags = buf[pos:end].decode("ascii", errors="replace")
    pos = end + 1
    pos += _pad(pos)

    args: list[Any] = []
    for tag in typetags[1:]:
        if tag == "i":
            if pos + 4 > n:
                raise OscError("truncated int32 argument")
            args.append(_INT.unpack_from(buf, pos)[0])
            pos += 4
        elif tag == "f":
            if pos + 4 > n:
                raise OscError("truncated float32 argument")
            args.append(_FLOAT.unpack_from(buf, pos)[0])
            pos += 4
        elif tag == "s":
            end = buf.find(b"\x00", pos)
            if end < 0:
                raise OscError("unterminated string argument")
            args.append(buf[pos:end].decode("utf-8", errors="replace"))
            pos = end + 1
            pos += _pad(pos)
        elif tag == "b":
            if pos + 4 > n:
                raise OscError("truncated blob length")
            size = _UINT.unpack_from(buf, pos)[0]
            pos += 4
            if pos + size > n:
                raise OscError(f"truncated blob: {size} B declared, {n - pos} B present")
            args.append(buf[pos : pos + size])  # raw; contents are never inspected here
            pos += size
            pos += _pad(pos)
        else:
            raise OscError(f"unsupported typetag {tag!r} in {typetags!r}")
    return OscMessage(address, tuple(args), typetags)
