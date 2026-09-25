"""Meter blob decoding, ``/meters`` subscriptions and pluggable frame sources (DESIGN.md §11).

Every wire fact here comes from ``docs/research/meters.md`` (cited inline as "meters.md §n").
The one thing to hold on to: **two length fields, two endiannesses** (meters.md §6.1). The OSC
blob size is big-endian and is consumed by :mod:`x32mcp.osc`; the X32's own *word count* — the
first four bytes *inside* the blob — and every payload word after it are little-endian.

Decisions not fixed by DESIGN.md (all documented here, nowhere else):

* :func:`parse_meter_blob` takes an optional ``meter_type`` override for ``/batchsubscribe``
  aliases, whose reply address is the alias rather than ``/meters/N`` (meters.md §1.3).
* The word count is decoded and checked against the blob's own length, but *not* against the
  per-id table :data:`METER_COUNTS` — older firmware or the emulator may differ and the values
  decode regardless. Only a blob that disagrees with its own count raises.
* :meth:`MeterFrame.db` returns RTA (type 15) values unchanged (they are already dB, floor
  −128); linear meters are converted with 20·log10 and floored at ``floor`` (default −90 dB).
* :class:`LiveMeters` re-sends the identical ``/meters`` request every ``renew_s`` seconds
  (meters.md §1.3: the lease is 10 s and ``/renew`` is a no-op on Maillot's emulator). It never
  sends ``/unsubscribe`` on stop — on the emulator that also drops the ``/xremote`` client
  (transport.md §11) — the lease simply lapses within 10 s while stray frames are ignored.
* :func:`average_frames` averages in the frame's native domain (dB for RTA, linear otherwise)
  and returns the mean of whatever arrived when the timeout hits before ``n`` frames; ``None``
  only when nothing arrived at all.
* :func:`set_rta_source` returns an :class:`RtaSourceResult` (DESIGN says ``None``) so callers
  can see whether the ``/-stat/rtasource`` read-back agreed; a mismatch is logged, never raised.
  A DCA target raises :class:`RtaSourceError` (DCAs carry no audio).
* :class:`SyntheticRta` — the signal model is described in its docstring.
* Extras beyond DESIGN: :func:`encode_meter_blob` / :func:`encode_meter_datagram` (exact
  inverses, for ``fakedesk``), :func:`parse_meter_datagram`, :data:`METER_COUNTS`,
  :data:`METER_GROUPS`, :func:`rta_band_centre`, :data:`RTA_BAND_HZ`, :func:`band_for_hz`,
  :func:`lin_to_db`, :func:`rta_source_index`, :func:`rta_stat_expected`,
  :func:`target_for_rta_source`.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import re
import struct
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, Protocol, Sequence, runtime_checkable

from x32mcp.descriptor import Descriptor
from x32mcp.events import EventBus
from x32mcp.osc import OscError, decode, encode
from x32mcp.settle import read_until
from x32mcp.targets import Target

__all__ = [
    "DEFAULT_FRAME_PERIOD_S",
    "FixtureSource",
    "FrameSource",
    "LiveMeters",
    "METER_COUNTS",
    "METER_GROUPS",
    "MeterFrame",
    "MeterParseError",
    "RTA_BANDS",
    "RTA_BAND_HZ",
    "RTA_FLOOR_DB",
    "RTA_METER_TYPE",
    "RtaSourceError",
    "RtaSourceResult",
    "SOLO_PRIORITY_BIT",
    "SyntheticRta",
    "average_frames",
    "band_for_hz",
    "encode_meter_blob",
    "encode_meter_datagram",
    "force_rta_ballistics",
    "lin_to_db",
    "parse_meter_blob",
    "parse_meter_datagram",
    "rta_band_centre",
    "rta_band_hz",
    "rta_source_index",
    "rta_stat_expected",
    "restore_rta_prefs",
    "set_rta_source",
    "target_for_rta_source",
]

log = logging.getLogger(__name__)

RTA_METER_TYPE = 15
RTA_BANDS = 100
RTA_FLOOR_DB = -128.0  # 0x8000 / 256: "no signal" (meters.md §4.2)
DEFAULT_FRAME_PERIOD_S = 0.05  # 50 ms x time_factor 1 (meters.md §1.2)
SOLO_PRIORITY_BIT = 1 << 5  # /-prefs/rta/options bit 5 (meters.md §5.3)

#: 32-bit words per meter id (meters.md §3, triple-confirmed). Informational only.
METER_COUNTS: dict[int, int] = {
    0: 70, 1: 96, 2: 49, 3: 22, 4: 82, 5: 27, 6: 4, 7: 16, 8: 6, 9: 32, 10: 32, 11: 5,
    12: 4, 13: 48, 14: 80, 15: 50, 16: 48,
}

#: Named slices for ``get_meters``: name -> (meter_type, first index, count), linear values.
#: /meters/0 = 32 ch, 8 aux, 8 fx rtn, 16 bus, 6 mtx; /meters/2 [22..24] = main L, R, M/C
#: (meters.md §3).
METER_GROUPS: dict[str, tuple[int, int, int]] = {
    "channels": (0, 0, 32),
    "auxins": (0, 32, 8),
    "fxrtns": (0, 40, 8),
    "buses": (0, 48, 16),
    "matrices": (0, 64, 6),
    "main": (2, 22, 3),
}

_ADDR_RE = re.compile(r"^/meters/(\d+)$")
_LE_INT = struct.Struct("<i")


class MeterParseError(ValueError):
    """A meter blob whose shape disagrees with its own word count, or an unknown address."""


class RtaSourceError(ValueError):
    """The target cannot be selected as the console's RTA source."""


# -- dB helpers -------------------------------------------------------------------------------


def lin_to_db(v: float, floor: float = -90.0) -> float:
    """Linear amplitude -> dBFS (20·log10), floored. Silence on the desk is ~1e-5..4e-7, never
    0.0 (meters.md §4.1) but 0.0 is guarded anyway."""
    if v <= 0.0:
        return floor
    return max(floor, 20.0 * math.log10(v))


def _power_sum_db(levels: Sequence[float]) -> float:
    """Incoherent (power) sum of dB levels."""
    total = 0.0
    for lv in levels:
        total += 10.0 ** (lv / 10.0)
    return 10.0 * math.log10(total) if total > 0.0 else RTA_FLOOR_DB


# -- frames -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class MeterFrame:
    """One decoded meter message.

    ``values``: dB (−128..0) for the RTA (type 15); linear amplitude (0..1, headroom to 8.0,
    meters.md §4.1) for types 0–14; type 16 is 88 linear gains (1.0 = no reduction) then 8
    automix gains (meters.md §4.3). ``ts`` is the local receive time (meters.md §6.7: never
    derive timing from the desk).
    """

    meter_type: int
    ts: float
    values: tuple[float, ...]

    @property
    def is_rta(self) -> bool:
        return self.meter_type == RTA_METER_TYPE

    def db(self, *, floor: float = -90.0) -> tuple[float, ...]:
        """Values in dB: RTA frames are returned unchanged; linear meters use
        :func:`lin_to_db` with ``floor``."""
        if self.is_rta:
            return self.values
        return tuple(lin_to_db(v, floor) for v in self.values)


def _meter_type_of(address: str, meter_type: int | None) -> int:
    if meter_type is not None:
        return int(meter_type)
    m = _ADDR_RE.match(address)
    if not m:
        raise MeterParseError(f"cannot infer the meter type from address {address!r}; pass meter_type=")
    return int(m.group(1))


def parse_meter_blob(address: str, blob: bytes, *, ts: float, meter_type: int | None = None) -> MeterFrame:
    """Decode the raw ``,b`` payload of a ``/meters/N`` reply (what :func:`x32mcp.osc.decode`
    hands back: the bytes *after* the big-endian OSC blob size).

    Layout per meters.md §2.1: ``count`` int32 little-endian, then ``count`` little-endian
    32-bit words. Raises :class:`MeterParseError` when the blob is not ``4 + 4*count`` bytes,
    the count is negative, or the meter type is unknown (address not ``/meters/N`` and no
    ``meter_type`` given).
    """
    mtype = _meter_type_of(address, meter_type)
    blob = bytes(blob)
    if len(blob) < 4:
        raise MeterParseError(f"{address}: blob of {len(blob)} B has no word count")
    count = _LE_INT.unpack_from(blob, 0)[0]  # per meters.md §2: the count is LITTLE-endian
    if count < 0:
        raise MeterParseError(f"{address}: negative word count {count}")
    expected = 4 + 4 * count
    if len(blob) != expected:
        raise MeterParseError(f"{address}: blob is {len(blob)} B but count {count} needs {expected} B")
    payload = blob[4:]
    try:
        if mtype == RTA_METER_TYPE:
            # per meters.md §4.2: each word = 2 x int16 LE, band 2w in the low half; dB = s/256
            values = tuple(s / 256.0 for s in struct.unpack_from(f"<{2 * count}h", payload))
        elif mtype == 16:
            # per meters.md §4.3: int16 LE; all but the last 8 are /32767 linear gains, the last 8
            # are automix gains coded log2(gain)*256
            shorts = struct.unpack_from(f"<{2 * count}h", payload)
            n_amix = 8 if len(shorts) > 8 else 0
            k = len(shorts) - n_amix
            values = tuple([s / 32767.0 for s in shorts[:k]] + [2.0 ** (s / 256.0) for s in shorts[k:]])
        else:
            # per meters.md §4.1: float32 LITTLE-endian linear amplitude
            values = tuple(struct.unpack_from(f"<{count}f", payload))
    except struct.error as e:  # unreachable after the length check, kept for safety
        raise MeterParseError(f"{address}: {e}") from None
    return MeterFrame(mtype, ts, values)


def parse_meter_datagram(data: bytes, *, ts: float, meter_type: int | None = None) -> MeterFrame:
    """Decode a whole datagram (``/meters/N ,b <blob>``) — :func:`x32mcp.osc.decode` followed by
    :func:`parse_meter_blob`. Raises :class:`MeterParseError` for anything that is not a
    single-blob message."""
    try:
        msg = decode(data)
    except OscError as e:
        raise MeterParseError(f"not an OSC message: {e}") from None
    if msg.typetags != ",b" or len(msg.args) != 1 or not isinstance(msg.args[0], bytes):
        raise MeterParseError(f"{msg.address}: expected a single ,b argument, got {msg.typetags!r}")
    return parse_meter_blob(msg.address, msg.args[0], ts=ts, meter_type=meter_type)


def _clamp_int(v: float, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(round(v))))


def encode_meter_blob(meter_type: int, values: Sequence[float]) -> bytes:
    """Exact inverse of :func:`parse_meter_blob`: LE word count + LE payload words, in the
    console's own encoding for ``meter_type`` (meters.md §2.1, §4). ``values`` for type 15 are
    dB (clamped to −128..0, even length required); type 16 takes linear gains (last 8 = automix);
    others take linear floats. Wrap with :func:`x32mcp.osc.encode` or use
    :func:`encode_meter_datagram`."""
    if meter_type == RTA_METER_TYPE:
        if len(values) % 2:
            raise ValueError("RTA blobs pack 2 bands per word: even number of values required")
        shorts = [_clamp_int(v * 256.0, -32768, 0) for v in values]
        count = len(shorts) // 2
        payload = struct.pack(f"<{len(shorts)}h", *shorts)
    elif meter_type == 16:
        if len(values) % 2:
            raise ValueError("/meters/16 packs 2 shorts per word: even number of values required")
        n_amix = 8 if len(values) > 8 else 0
        k = len(values) - n_amix
        shorts = [_clamp_int(v * 32767.0, -32768, 32767) for v in values[:k]]
        shorts += [_clamp_int(math.log2(v) * 256.0, -32768, 32767) if v > 0 else -32768 for v in values[k:]]
        count = len(shorts) // 2
        payload = struct.pack(f"<{len(shorts)}h", *shorts)
    else:
        count = len(values)
        payload = struct.pack(f"<{count}f", *[float(v) for v in values])
    return _LE_INT.pack(count) + payload


def encode_meter_datagram(meter_type: int, values: Sequence[float], *, address: str | None = None) -> bytes:
    """A complete ``/meters/N ,b`` datagram exactly as the console sends it (meters.md §2.1:
    12-byte address, ``,b~~``, BE size, LE count, LE words)."""
    return encode(address or f"/meters/{meter_type}", encode_meter_blob(meter_type, values))


# -- RTA bands --------------------------------------------------------------------------------


def rta_band_centre(i: int) -> float:
    """Centre frequency (Hz) of RTA band ``i`` (0..99): 1/10-octave spacing anchored at band 90
    = 10 kHz (meters.md §4.2, verified against the DOC table)."""
    return 20.0 * 2.0 ** (i / 10.0)


RTA_BAND_HZ: tuple[float, ...] = tuple(rta_band_centre(i) for i in range(RTA_BANDS))


def rta_band_hz(d: Descriptor) -> tuple[float, ...]:
    """The 100 band centres from ``device.yaml`` ``rta.band_hz`` (Hz); falls back to the
    formula when the list is absent. Raises :class:`ValueError` when the list length disagrees
    with ``rta.bands``."""
    bands = int(d.rta.get("bands", RTA_BANDS))
    hz = d.rta.get("band_hz")
    if not hz:
        return tuple(rta_band_centre(i) for i in range(bands))
    if len(hz) != bands:
        raise ValueError(f"rta.band_hz has {len(hz)} entries, rta.bands says {bands}")
    return tuple(float(x) for x in hz)


def band_for_hz(hz: float, band_hz: Sequence[float] = RTA_BAND_HZ) -> int:
    """Index of the band whose centre is nearest ``hz`` on a log axis (clamped to the ends)."""
    if hz <= 0:
        raise ValueError(f"frequency must be > 0 Hz, got {hz}")
    lf = math.log(hz)
    return min(range(len(band_hz)), key=lambda i: abs(math.log(band_hz[i]) - lf))


# -- frame sources ----------------------------------------------------------------------------

FrameCallback = Callable[[MeterFrame], None]


@runtime_checkable
class FrameSource(Protocol):
    """Anything that pushes :class:`MeterFrame` objects to subscribers."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def subscribe(self, cb: FrameCallback) -> Callable[[], None]: ...


class _Emitter:
    """Subscriber bookkeeping shared by the sources. A failing subscriber is logged, never
    allowed to break the producer."""

    def __init__(self) -> None:
        self._subs: list[FrameCallback] = []
        self._last: MeterFrame | None = None
        self.frames_emitted = 0

    def subscribe(self, cb: FrameCallback) -> Callable[[], None]:
        self._subs.append(cb)

        def _unsub() -> None:
            try:
                self._subs.remove(cb)
            except ValueError:
                pass

        return _unsub

    @property
    def last_frame(self) -> MeterFrame | None:
        return self._last

    def _emit(self, frame: MeterFrame) -> None:
        self._last = frame
        self.frames_emitted += 1
        for cb in list(self._subs):
            try:
                cb(frame)
            except Exception:
                log.exception("meter frame subscriber %r failed", cb)


async def _cancel(task: asyncio.Task | None, timeout_s: float = 1.0) -> None:
    if task is None or task.done():
        return
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=timeout_s)
    except asyncio.CancelledError:
        cur = asyncio.current_task()
        if cur is not None and cur.cancelling():  # the caller itself is being cancelled
            raise
    except asyncio.TimeoutError:
        pass
    except Exception:  # the task's own failure was already logged by the task
        pass


class LiveMeters(_Emitter):
    """Frames from a real (or fake) console over an ``X32Connection``-like object.

    ``conn`` is duck-typed: it needs ``on_blob(cb) -> unsubscribe`` delivering
    ``(address, blob_bytes)`` and ``async send_raw(address, *args, typetags=None)``.

    Request (meters.md §1.1, §7): ``/meters ,si /meters/N tf`` — with ``extra_args`` inserted
    before ``tf`` (``,sii /meters/6 <channel_id> tf``, ``,siii /meters/5 <chn> <grp> tf``).
    ``time_factor`` 1..99 → one frame every 50 ms × tf (values outside the range mean 1 on the
    desk; they are clamped here). The identical request is re-sent every ``renew_s`` (the lease
    is 10 s, meters.md §1.3). Frames are timestamped locally with ``clock``.

    ``events`` (optional) receives ``meters.frame`` ``{meter_type, values}`` per frame.
    Counters: ``frames_received``, ``parse_errors``; ``last_frame``; ``last_rx_age_s``.
    """

    def __init__(
        self,
        conn: Any,
        meter_type: int,
        *,
        extra_args: tuple[int, ...] = (),
        renew_s: float = 5.0,
        events: EventBus | None = None,
        time_factor: int = 1,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__()
        self._conn = conn
        self.meter_type = int(meter_type)
        self.address = f"/meters/{self.meter_type}"  # leading slash: the form proven on the wire (meters.md §1.1)
        self.extra_args = tuple(int(a) for a in extra_args)
        self.renew_s = float(renew_s)
        self._events = events
        tf = int(time_factor)
        if not 1 <= tf <= 99:
            log.warning("time_factor %d outside 1..99; the desk treats it as 1", tf)
            tf = 1
        self.time_factor = tf
        self._clock = clock
        self._task: asyncio.Task | None = None
        self._unsub_blob: Callable[[], None] | None = None
        self.frames_received = 0
        self.parse_errors = 0
        self.requests_sent = 0
        self._last_rx: float | None = None

    @property
    def running(self) -> bool:
        return self._task is not None

    @property
    def last_rx_age_s(self) -> float | None:
        return None if self._last_rx is None else max(0.0, self._clock() - self._last_rx)

    async def start(self) -> None:
        """Subscribe to blobs, send the request now and keep renewing it. Idempotent."""
        if self._task is not None:
            return
        self._unsub_blob = self._conn.on_blob(self._on_blob)
        self._task = asyncio.create_task(self._run(), name=f"meters{self.address}")

    async def stop(self) -> None:
        """Stop renewing and ignore further blobs; the desk-side lease lapses by itself."""
        task, self._task = self._task, None
        if self._unsub_blob is not None:
            self._unsub_blob()
            self._unsub_blob = None
        await _cancel(task)

    async def _run(self) -> None:
        while True:
            await self._send_request()
            await asyncio.sleep(self.renew_s)

    async def _send_request(self) -> None:
        try:
            await asyncio.wait_for(
                self._conn.send_raw("/meters", self.address, *self.extra_args, self.time_factor),
                timeout=1.0,
            )
            self.requests_sent += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:  # not connected / degraded: keep trying on the renew cadence
            log.warning("meters request %s failed: %s", self.address, e)

    def _on_blob(self, address: str, blob: bytes) -> None:
        if address != self.address:
            return
        ts = self._clock()
        try:
            frame = parse_meter_blob(address, blob, ts=ts, meter_type=self.meter_type)
        except MeterParseError as e:
            self.parse_errors += 1
            log.debug("bad meter blob on %s: %s", address, e)
            return
        self.frames_received += 1
        self._last_rx = ts
        self._emit(frame)
        if self._events is not None:
            self._events.publish("meters.frame", meter_type=frame.meter_type, values=list(frame.values))


class FixtureSource(_Emitter):
    """Replays frames: a list/iterable, or ``frames(i) -> MeterFrame | None`` (None ends).

    ``realtime=True`` sleeps ``period_s`` between frames; ``False`` only yields to the loop
    between frames (as fast as possible, for tests). ``loop=True`` restarts at the end (lists
    and callables only; a one-shot iterator ends). Frames are emitted as given (``ts`` is not
    rewritten). ``wait_done`` lets a test await the end without polling.
    """

    def __init__(
        self,
        frames: Iterable[MeterFrame] | Callable[[int], MeterFrame | None],
        *,
        period_s: float = DEFAULT_FRAME_PERIOD_S,
        realtime: bool = True,
        loop: bool = False,
    ) -> None:
        super().__init__()
        self._frames = frames
        self.period_s = float(period_s)
        self.realtime = realtime
        self.loop = loop
        self._task: asyncio.Task | None = None
        self._done = asyncio.Event()
        self._done.set()

    def _iter(self) -> Iterator[MeterFrame]:
        src = self._frames
        if callable(src) and not isinstance(src, (list, tuple)):
            i = 0
            while True:
                f = src(i)
                if f is None:
                    return
                yield f
                i += 1
        else:
            yield from src  # type: ignore[misc]

    @property
    def running(self) -> bool:
        return self._task is not None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._done.clear()
        self._task = asyncio.create_task(self._run(), name="meters-fixture")

    async def stop(self) -> None:
        task, self._task = self._task, None
        await _cancel(task)
        self._done.set()

    async def wait_done(self, timeout_s: float | None = None) -> bool:
        """Await the end of the replay; False on timeout."""
        try:
            await asyncio.wait_for(self._done.wait(), timeout=timeout_s)
            return True
        except asyncio.TimeoutError:
            return False

    async def _run(self) -> None:
        try:
            while True:
                n = 0
                for frame in self._iter():
                    n += 1
                    self._emit(frame)
                    await asyncio.sleep(self.period_s if self.realtime else 0)
                if not self.loop or n == 0:
                    break
        finally:
            self._task = None
            self._done.set()


@dataclass
class _Tone:
    band: int
    hz: float
    level: float  # rings: current dB; notes: target (plateau) dB
    growth: float = 0.0  # rings: dB/s
    cap: float = 0.0  # rings: ceiling dB
    age: int = 0  # notes: frames since injection
    rise_frames: int = 5  # notes: frames the NOTE_RISE_DB onset ramp takes (1 = arrives within one frame)


class SyntheticRta(_Emitter):
    """Deterministic 100-band "music + feedback" RTA generator (type 15 frames, dB).

    Signal model (all levels dB, combined per band as an incoherent power sum, then clamped to
    the RTA range −128..0):

    * **Base spectrum**: a pink-ish slope through ``level_100hz_db`` at 100 Hz and
      ``level_10khz_db`` at 10 kHz (defaults −25 → −55, i.e. −15 dB/decade), plus uniform
      ±``noise_db`` per band per frame and a global ``wobble_db`` sinusoid at ``wobble_hz``.
    * **Ring** (:meth:`inject_ring`): starts at ``start_db`` and rises linearly by
      ``growth_db_per_s × period_s`` per frame until ``cap_db``; leaks into the ±1 neighbouring
      bands at −``leak_db``.
    * **Note** (:meth:`inject_note`): rises 30 dB over 5 frames to ``level_db`` then holds —
      what a held guitar note looks like; leaks like a ring.
    * **Cuts** (:meth:`attenuate` / :meth:`set_geq_gain`): a cut of ``c`` dB at a band lowers
      everything the analyser sees there by ``c`` and, for a ring, lowers the *loop gain*: per
      frame ``level += growth × period_s − c × period_s / loop_period_s``. With the default
      ``loop_period_s = period_s`` that is literally ``level -= c`` per frame, so the ring decays
      as soon as the cut exceeds its per-frame growth margin ``growth × period_s`` (0.75 dB for
      15 dB/s at 20 fps): one −3 dB GEQ step kills any ring slower than 60 dB/s. The ring is
      kept (floored at −128) so removing the cut lets it regrow.

    Driving it: :meth:`tick` produces (and emits) one frame; :meth:`frames` is an endless
    generator over :meth:`tick`; :meth:`start` runs :meth:`tick` every ``period_s`` on the
    event loop. Frame timestamps are the virtual clock ``t0 + frame_index × period_s`` (the
    realtime task paces itself to it). Same ``seed`` ⇒ identical frames.
    """

    NOTE_RISE_FRAMES = 5
    NOTE_RISE_DB = 30.0

    def __init__(
        self,
        *,
        seed: int = 1,
        period_s: float = DEFAULT_FRAME_PERIOD_S,
        band_hz: Sequence[float] | None = None,
        noise_db: float = 3.0,
        wobble_db: float = 2.0,
        wobble_hz: float = 0.5,
        level_100hz_db: float = -25.0,
        level_10khz_db: float = -55.0,
        leak_db: float = 10.0,
        loop_period_s: float | None = None,
        t0: float | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__()
        self.band_hz: tuple[float, ...] = tuple(float(h) for h in (band_hz or RTA_BAND_HZ))
        self.bands = len(self.band_hz)
        self.period_s = float(period_s)
        self.noise_db = float(noise_db)
        self.wobble_db = float(wobble_db)
        self.wobble_hz = float(wobble_hz)
        self.leak_db = float(leak_db)
        self.loop_period_s = float(loop_period_s) if loop_period_s else self.period_s
        self.seed = seed
        self._rng = random.Random(seed)
        self._t0 = float(clock() if t0 is None else t0)
        slope = (level_10khz_db - level_100hz_db) / 2.0  # dB per decade
        self._base = tuple(level_100hz_db + slope * math.log10(h / 100.0) for h in self.band_hz)
        self._rings: dict[int, _Tone] = {}
        self._notes: dict[int, _Tone] = {}
        self._cuts: dict[int, float] = {}
        self.frame_index = 0
        self._task: asyncio.Task | None = None

    # -- scenario controls -------------------------------------------------------------------
    def band_for_hz(self, hz: float) -> int:
        return band_for_hz(hz, self.band_hz)

    def base_level_db(self, band: int) -> float:
        """The noiseless base spectrum at ``band`` (before cuts and wobble)."""
        return self._base[band]

    def inject_ring(self, freq_hz: float, growth_db_per_s: float, start_db: float = -45.0, cap_db: float = -3.0) -> int:
        """Start (or replace) a regenerating ring at the band nearest ``freq_hz``. Returns the band."""
        b = self.band_for_hz(freq_hz)
        self._rings[b] = _Tone(b, float(freq_hz), float(start_db), float(growth_db_per_s), float(cap_db))
        return b

    def stop_ring(self, freq_hz: float) -> bool:
        return self._rings.pop(self.band_for_hz(freq_hz), None) is not None

    def inject_note(self, freq_hz: float, level_db: float, *, rise_frames: int | None = None) -> int:
        """Start (or replace) a held note that rises ``NOTE_RISE_DB`` over ``rise_frames`` frames (default
        ``NOTE_RISE_FRAMES`` = 5: what a held guitar note looks like; 1 = a source that arrives within one frame,
        e.g. a howl slamming into a limiter faster than the frame rate, or a switched-on tone) to ``level_db`` and stays."""
        b = self.band_for_hz(freq_hz)
        n = self.NOTE_RISE_FRAMES if rise_frames is None else max(1, int(rise_frames))
        self._notes[b] = _Tone(b, float(freq_hz), float(level_db), rise_frames=n)
        return b

    def stop_note(self, freq_hz: float) -> bool:
        return self._notes.pop(self.band_for_hz(freq_hz), None) is not None

    def attenuate(self, band_idx: int, db: float) -> None:
        """Set the cut at ``band_idx`` to ``db`` (≥ 0 = attenuation, the size of a GEQ cut;
        negative = boost; 0 removes it). Absolute, not cumulative — like a GEQ band gain."""
        if not 0 <= band_idx < self.bands:
            raise ValueError(f"band {band_idx} outside 0..{self.bands - 1}")
        if db == 0:
            self._cuts.pop(band_idx, None)
        else:
            self._cuts[band_idx] = float(db)

    def set_geq_gain(self, hz: float, gain_db: float) -> tuple[int, ...]:
        """Apply a GEQ band gain (negative = cut) centred at ``hz`` to every RTA band within
        ±1/6 octave (a 1/3-octave GEQ band's nominal width, i.e. 3 RTA bands). Returns them."""
        bands = tuple(i for i, h in enumerate(self.band_hz) if abs(math.log2(h / hz)) <= 1.0 / 6.0 + 1e-9)
        for b in bands:
            self.attenuate(b, -float(gain_db))
        return bands

    def clear_cuts(self) -> None:
        self._cuts.clear()

    def ring_level(self, freq_hz: float) -> float | None:
        """Internal level (dB) of the ring at the band nearest ``freq_hz``; None if none."""
        r = self._rings.get(self.band_for_hz(freq_hz))
        return None if r is None else r.level

    @property
    def rings(self) -> list[dict[str, Any]]:
        return [{"band": r.band, "freq_hz": r.hz, "level_db": r.level, "growth_db_per_s": r.growth, "cap_db": r.cap} for r in self._rings.values()]

    @property
    def notes(self) -> list[dict[str, Any]]:
        return [{"band": n.band, "freq_hz": n.hz, "level_db": n.level, "age_frames": n.age, "rise_frames": n.rise_frames}
                for n in self._notes.values()]

    @property
    def cuts(self) -> dict[int, float]:
        return dict(self._cuts)

    # -- generation --------------------------------------------------------------------------
    def _note_level(self, n: _Tone) -> float:
        rise = max(0.0, 1.0 - n.age / max(1, n.rise_frames))
        return n.level - self.NOTE_RISE_DB * rise

    def _spread(self, contrib: list[list[float]], band: int, level: float) -> None:
        # ``level`` is the tone's post-EQ level at its own band; the analyser smears that same
        # energy into the neighbours at -leak_db (a neighbour's own cut acts on other frequencies).
        contrib[band].append(level)
        for j in (band - 1, band + 1):
            if 0 <= j < self.bands:
                contrib[j].append(level - self.leak_db)

    def _compute(self) -> tuple[float, ...]:
        t = self.frame_index * self.period_s
        wob = self.wobble_db * math.sin(2.0 * math.pi * self.wobble_hz * t)
        rng = self._rng
        nz = self.noise_db
        contrib = [[self._base[i] + wob + rng.uniform(-nz, nz) - self._cuts.get(i, 0.0)] for i in range(self.bands)]
        for r in self._rings.values():
            self._spread(contrib, r.band, r.level)
        for n in self._notes.values():
            self._spread(contrib, n.band, self._note_level(n) - self._cuts.get(n.band, 0.0))
        return tuple(min(0.0, max(RTA_FLOOR_DB, _power_sum_db(c))) for c in contrib)

    def _advance(self) -> None:
        for r in self._rings.values():
            cut = self._cuts.get(r.band, 0.0)
            r.level += r.growth * self.period_s - cut * self.period_s / self.loop_period_s
            r.level = min(r.cap, max(RTA_FLOOR_DB, r.level))
        for n in self._notes.values():
            n.age += 1
        self.frame_index += 1

    def tick(self) -> MeterFrame:
        """Generate one frame, emit it to subscribers, advance the model."""
        frame = MeterFrame(RTA_METER_TYPE, self._t0 + self.frame_index * self.period_s, self._compute())
        self._advance()
        self._emit(frame)
        return frame

    def frames(self) -> Iterator[MeterFrame]:
        """Endless generator over :meth:`tick` (subscribers see these frames too)."""
        while True:
            yield self.tick()

    def next_blob(self) -> bytes:
        """The next frame as the exact ``/meters/15`` blob payload (for ``fakedesk``)."""
        return encode_meter_blob(RTA_METER_TYPE, self.tick().values)

    # -- FrameSource -------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._task is not None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="meters-synthetic")

    async def stop(self) -> None:
        task, self._task = self._task, None
        await _cancel(task)

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        origin = loop.time()
        n0 = self.frame_index
        while True:
            self.tick()
            target = origin + (self.frame_index - n0) * self.period_s
            await asyncio.sleep(max(0.0, target - loop.time()))


# -- aggregation ------------------------------------------------------------------------------


async def average_frames(src: FrameSource, n: int, *, timeout_s: float) -> MeterFrame | None:
    """Mean of the next ``n`` frames from ``src`` (element-wise, native domain: dB for RTA,
    linear otherwise). Returns after ``n`` frames or ``timeout_s``, whichever first; the mean of
    the frames that did arrive, or ``None`` when none did. ``src`` must already be started."""
    got: list[MeterFrame] = []
    done = asyncio.Event()
    n = max(1, int(n))

    def _cb(f: MeterFrame) -> None:
        if len(got) < n:
            got.append(f)
        if len(got) >= n:
            done.set()

    unsub = src.subscribe(_cb)
    try:
        await asyncio.wait_for(done.wait(), timeout=timeout_s)
    except asyncio.TimeoutError:
        pass
    finally:
        unsub()
    if not got:
        return None
    width = min(len(f.values) for f in got)
    k = len(got)
    values = tuple(sum(f.values[i] for f in got) / k for i in range(width))
    return MeterFrame(got[-1].meter_type, got[-1].ts, values)


# -- RTA source -------------------------------------------------------------------------------


def _rta_source_token(target: Target) -> str:
    fam, n = target.family, target.index
    if fam == "ch":
        return f"CH{n:02d}"
    if fam == "auxin":
        return f"AUX{n}"
    if fam == "fxrtn":  # fxrtn 1..8 = FX1L, FX1R, FX2L, ... FX4R
        return f"FX{(int(n) + 1) // 2}{'L' if int(n) % 2 else 'R'}"
    if fam == "bus":
        return f"BUS{n:02d}"
    if fam == "mtx":
        return f"MTX{n}"
    if fam == "main":
        return "MAIN" if n == "st" else "MONO"
    raise RtaSourceError(f"{target.label} cannot be an RTA source (no audio path)")


def rta_source_index(d: Descriptor, target: Target) -> int:
    """``/-prefs/rta/source`` value for a strip (meters.md §5.1: 2–33 Ch, 34–41 Aux, 42–49 FX
    rtn, 50–65 Bus, 66–71 Mtx, 72 Main LR, 73 Mono) via the ``rta_source`` enum tokens.
    Raises :class:`RtaSourceError` for DCAs or an unknown token."""
    tok = _rta_source_token(target)
    try:
        return d.enum("rta_source").index(tok)
    except ValueError:
        raise RtaSourceError(f"token {tok!r} for {target.label} is not in enum rta_source") from None


def rta_stat_expected(source_index: int, post_eq: bool) -> int:
    """The ``/-stat/rtasource`` value that corresponds to a ``/-prefs/rta/source`` index:
    prefs 2..73 ↔ stat 0..71, Monitor (prefs 1) ↔ 72, +98 when post-EQ (meters.md §5.2)."""
    pre = source_index - 2 if source_index >= 2 else 72
    return pre + (98 if post_eq else 0)


def target_for_rta_source(d: Descriptor, source_index: int) -> Target | None:
    """Reverse of :func:`rta_source_index`; None for 0 (none), 1 (Monitor) or unknown."""
    for fam in d.strip_families:
        for t in d.strip_targets(fam):
            try:
                if rta_source_index(d, t) == source_index:
                    return t
            except RtaSourceError:
                break  # whole family unsupported (dca)
    return None


@dataclass(frozen=True)
class RtaSourceResult:
    """What :func:`set_rta_source` wrote and what the desk reported back."""

    target: Target
    source_index: int  # /-prefs/rta/source value written
    post_eq: bool
    stat_expected: int  # /-stat/rtasource value we expect
    stat_actual: int | None  # what the desk answered (None: no answer)
    verified: bool
    options_cleared: bool  # True when bit 5 (Solo Priority) had to be cleared
    autogain_cleared: bool = False  # True when the RTA's auto-gain had to be switched off
    detector_set_peak: bool = False  # True when the RTA detector had to be moved to PEAK
    decay_set_min: bool = False  # True when the RTA release ("decay") had to be shortened to its minimum
    peakhold_cleared: bool = False  # True when the RTA peak-hold had to be switched off
    prefs_before: dict[str, Any] | None = None  # raw pref values as found, for the report / a later restore
    gain_set: bool = False  # True when the manual display gain had to be moved to rta.gain_db
    settle_attempts: int = 1  # reads of stat_param until it agreed (or the deadline passed)
    settle_ms: float = 0.0


async def set_rta_source(
    conn: Any,
    d: Descriptor,
    target: Target,
    *,
    post_eq: bool = True,
    read_timeout_s: float = 1.0,
    verify_deadline_s: float = 1.0,
) -> RtaSourceResult:
    """Point the console's RTA (and hence ``/meters/15``) at ``target``.

    Recipe per meters.md §5.3: write ``rta.source_param`` (``/-prefs/rta/source``) with the
    strip's index, ``rta.pos_param`` (``/-prefs/rta/pos``) 1 = POST / 0 = PRE, clear bit 5
    (Solo Priority) of ``rta.options_param`` if set — bits 0–4 only affect the EQ/GEQ overlay
    views and are left alone — force the analyser settings the detector depends on (auto-gain OFF,
    detector PEAK, decay at its minimum, peak-hold OFF; each only written when it differs), then
    read ``rta.stat_param`` (``/-stat/rtasource``) and compare
    with :func:`rta_stat_expected` (Bus N post-EQ = 146+N−1, Main LR post = 168).

    ``conn`` needs ``async get(address)`` and ``async set(address, value)``. Write failures
    propagate; read failures and a mismatching read-back are logged and reported in the result.
    Every read is bounded by ``read_timeout_s``.
    """
    idx = rta_source_index(d, target)
    rta = d.rta
    src_addr = rta.get("source_param", "/-prefs/rta/source")
    pos_addr = rta.get("pos_param", "/-prefs/rta/pos")
    opt_addr = rta.get("options_param", "/-prefs/rta/options")
    stat_addr = rta.get("stat_param", "/-stat/rtasource")

    async def _read(addr: str) -> Any:
        try:
            return await asyncio.wait_for(conn.get(addr), timeout=read_timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("rta: reading %s failed: %s", addr, e)
            return None

    # The RTA is the engineer's instrument before it is ours: remember where it pointed so the session can put it back.
    src_before = await _read(src_addr)
    pos_before = await _read(pos_addr)
    await conn.set(src_addr, idx)
    await conn.set(pos_addr, 1 if post_eq else 0)

    cleared = False
    opts = await _read(opt_addr)
    if isinstance(opts, int) and opts & SOLO_PRIORITY_BIT:
        await conn.set(opt_addr, opts & ~SOLO_PRIORITY_BIT)
        cleared = True

    # Auto-gain normalises the whole analyser display, so every band's reported level moves
    # together whenever the overall level shifts. The detector reads that as monotonic growth,
    # which is exactly the signature it uses for feedback. At M7 on the real desk this burned the
    # entire notch budget on room noise before any gain was touched. The detector needs absolute
    # levels, so auto-gain must be off (meters.md §5.3 documents the pref; the consequence is ours).
    autogain_cleared = False
    ag_addr = rta.get("autogain_param", "/-prefs/rta/autogain")
    ag = await _read(ag_addr)
    if isinstance(ag, (int, float)) and int(ag) != 0:
        await conn.set(ag_addr, 0)
        autogain_cleared = True

    # RMS blunts a narrow tone; feedback is exactly that. PEAK tracks the onset.
    detector_set_peak = False
    det_addr = rta.get("det_param", "/-prefs/rta/det")
    det = await _read(det_addr)
    if isinstance(det, (int, float)) and int(det) != 1:
        await conn.set(det_addr, 1)
        detector_set_peak = True

    # Ballistics (meters.md §5.1): ``decay`` is the analyser's release (0.25 … 16, log steps, raw 0.0 =
    # shortest) and ``peakhold`` freezes past maxima (OFF, 1 … 8). Both are display preferences the
    # operator may have left anywhere, and both corrupt the detector's temporal features: a long release
    # makes every note linger for seconds after it stops (persistence then measures the preference, not
    # the sound), and a held peak is a dead-flat prominent band — exactly what an established ring looks
    # like. Force the fastest release and no hold so the frames track the signal.
    decay_set_min = False
    decay_addr = rta.get("decay_param", "/-prefs/rta/decay")
    dec = await _read(decay_addr)
    if isinstance(dec, (int, float)) and not isinstance(dec, bool) and float(dec) > 1e-6:
        await conn.set(decay_addr, 0.0)
        decay_set_min = True
    peakhold_cleared = False
    ph_addr = rta.get("peakhold_param", "/-prefs/rta/peakhold")
    ph = await _read(ph_addr)
    if isinstance(ph, (int, float)) and int(ph) != 0:
        await conn.set(ph_addr, 0)
        peakhold_cleared = True
    # Manual display gain (0..60 dB in 6 dB steps) becomes active once auto-gain is off. Whether it offsets the
    # /meters/15 values is UNCONFIRMED (meters.md §4.2) but it is the same display-gain stage whose auto mode was
    # PROVEN to reach the stream at M7, and every absolute level downstream (the clip flag apart) floats on it. So it
    # is pinned to a declared value (rta.gain_db, default 0 dB) for the session and the value found is reported.
    gain_addr = rta.get("gain_param", "/-prefs/rta/gain")
    gain = await _read(gain_addr)  # raw (linf 0..1 over 0..60 dB)
    gain_set = False
    want_gain_db = float(rta.get("gain_db", 0.0))
    hit = d.param_for_address(gain_addr)
    want_gain_raw = hit[0].to_raw(want_gain_db) if hit is not None else want_gain_db / 60.0
    if isinstance(gain, (int, float)) and not isinstance(gain, bool) and abs(float(gain) - float(want_gain_raw)) > 1e-4:
        await conn.set(gain_addr, want_gain_raw)
        gain_set = True
    prefs_before = {k: v for k, v in (("source", src_before), ("pos", pos_before), ("autogain", ag), ("det", det),
                                      ("decay", dec), ("peakhold", ph), ("options", opts), ("gain", gain)) if v is not None}

    # Whether the desk has derived /-stat/rtasource from the prefs by the time it answers the next
    # datagram is UNCONFIRMED (transport.md §5.4; inserts and names were seen to lag at M5/M7), so
    # the read-back is polled until it agrees or verify_deadline_s passes (settle.read_until): a
    # timeout is "not verified" — logged and reported, never raised.
    expected = rta_stat_expected(idx, post_eq)

    async def _read_stat() -> Any:
        return await asyncio.wait_for(conn.get(stat_addr), timeout=read_timeout_s)

    settled = await read_until(_read_stat, lambda v: isinstance(v, int) and v == expected, deadline_s=verify_deadline_s,
                               first_delay_s=0.05, read_timeout_s=read_timeout_s, retry_on=(Exception,), what=f"rta source {target.key}")
    actual = settled.value if isinstance(settled.value, int) and not isinstance(settled.value, bool) else None
    verified = settled.ok
    if not verified:
        log.warning("rta source %s: %s reads %r, expected %d (not verified after %d read(s), %.0f ms%s)", target.key, stat_addr,
                    actual, expected, settled.attempts, settled.elapsed_ms, f"; {settled.error}" if settled.error else "")
    else:
        log.info("rta source -> %s (%s=%d, %s)", target.label, src_addr, idx, "post-EQ" if post_eq else "pre-EQ")
    return RtaSourceResult(target, idx, post_eq, expected, actual, verified, cleared,
                           autogain_cleared, detector_set_peak, decay_set_min, peakhold_cleared, prefs_before or None, gain_set,
                           settled.attempts, round(settled.elapsed_ms, 1))



# prefs_before key -> descriptor rta key of the address it came from
_RESTORABLE_PREFS = (("source", "source_param", "/-prefs/rta/source"), ("pos", "pos_param", "/-prefs/rta/pos"),
                     ("autogain", "autogain_param", "/-prefs/rta/autogain"), ("det", "det_param", "/-prefs/rta/det"),
                     ("decay", "decay_param", "/-prefs/rta/decay"), ("peakhold", "peakhold_param", "/-prefs/rta/peakhold"),
                     ("gain", "gain_param", "/-prefs/rta/gain"))


async def force_rta_ballistics(conn: Any, d: Descriptor, *, read_timeout_s: float = 1.0) -> dict[str, Any]:
    """Re-assert the analyser ballistics the detector depends on -- ``decay`` at its minimum (raw 0.0) and
    ``peakhold`` OFF (0) -- mid-session. :func:`set_rta_source` forces them at arm; this is for when the
    detector's ``PEAK_HOLD_SUSPECTED`` / ``FROZEN_LINES`` flags say somebody changed them on the console since
    (a peak-held display freezes lines into dead-flat prominent bands; a long release stretches every note).
    Unconditional writes (a read that lies is exactly the failure mode), preceded by best-effort reads for the
    report. Returns ``{"decay_before", "peakhold_before", "written": [...], "failed": [...]}``; never raises on a
    failed write (logged and listed)."""
    rta = d.rta
    decay_addr = rta.get("decay_param", "/-prefs/rta/decay")
    ph_addr = rta.get("peakhold_param", "/-prefs/rta/peakhold")

    async def _read(addr: str) -> Any:
        try:
            return await asyncio.wait_for(conn.get(addr), timeout=read_timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.debug("rta: reading %s failed: %s", addr, e)
            return None

    out: dict[str, Any] = {"decay_before": await _read(decay_addr), "peakhold_before": await _read(ph_addr), "written": [], "failed": []}
    for key, addr, value in (("decay", decay_addr, 0.0), ("peakhold", ph_addr, 0)):
        try:
            await conn.set(addr, value)
            out["written"].append(key)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("rta: re-forcing %s=%r failed: %s", addr, value, e)
            out["failed"].append(key)
    log.info("RTA ballistics re-forced (decay 0, peakhold OFF): was decay=%r peakhold=%r", out["decay_before"], out["peakhold_before"])
    return out


async def restore_rta_prefs(conn: Any, d: Descriptor, prefs_before: dict[str, Any] | None) -> dict[str, Any]:
    """Put the console's RTA preferences back to what :func:`set_rta_source` found (source, PRE/POST,
    auto-gain, detector, decay, peak-hold, manual gain). Solo Priority (options bit 5) is deliberately NOT
    re-enabled: it is the setting that silently steals the analyser from a later session. Best effort: a
    failed write is logged and reported, never raised. Returns ``{"restored": [...], "failed": [...]}``."""
    out: dict[str, Any] = {"restored": [], "failed": []}
    if not prefs_before:
        return out
    rta = d.rta
    for key, param, default in _RESTORABLE_PREFS:
        if key not in prefs_before:
            continue
        addr = rta.get(param, default)
        try:
            await conn.set(addr, prefs_before[key])
            out["restored"].append(key)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("rta: restoring %s=%r failed: %s", addr, prefs_before[key], e)
            out["failed"].append(key)
    if out["restored"]:
        log.info("RTA preferences restored to their pre-session values (%s)", ", ".join(out["restored"]))
    return out
