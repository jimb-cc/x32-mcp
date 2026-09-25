"""Python X32 emulator for tests and demos (DESIGN.md §18): ``python -m x32mcp.fakedesk``.

One UDP socket that behaves like a console on the wire. Every wire fact comes from
``docs/research/transport.md`` (replies to the source port, ``/info`` ``/xinfo`` ``/status``
forms, ``/xremote`` pushes, ``/node`` and ``/`` text), ``docs/research/meters.md`` (``/meters``
request forms, lease, blob layout) and ``docs/research/fx_routing_scenes.md`` (scenes,
``/save`` ``/load``, GEQ layout, inserts). Maillot's own emulator is the model wherever the
desk itself is not captured; deviations from it are noted inline.

Decisions where DESIGN.md is silent (or where the verified research overrides it):

* **State** is ``address -> raw OSC value`` (float 0..1 / int / str), built from every
  parameter of the descriptor (all node fields plus the few params no node covers). Floats
  are stored float32-rounded so a value reads back exactly as it went over the wire. Writes
  snap to the parameter's grid like the console (faders 1024 steps, sends 161, GEQ gains
  61, ...). The 0 dB fader default is ``767/1023`` (the 1024 grid has no exact 0.75).
* **Pushes** (``/xremote``) go to every registered client except the sender of the write,
  only when a value actually changed (transport.md §4.2, emulator behaviour). Lease 10 s,
  at most four clients (§2). A multi-argument SET on a node address (``/ch/01/eq/1 ,ifff``)
  is forwarded verbatim, as the emulator does; a string argument on a node address is
  ignored (DOC footnote 7).
* **``/`` writes** parse with :func:`x32mcp.nodes.parse_node_line` (leading slash optional,
  partial trailing lists, ``ch/01`` as an alias of ``ch/01/config`` per DOC 4585) and echo
  the received datagram back verbatim *even when nothing could be applied* (the echo is
  flow control, §6.6).
* **``/node``** answers the descriptor's node paths (console padding via
  :func:`~x32mcp.nodes.render_node_line`) and single leaves (engineering text, unpadded —
  the desk's exact single-leaf format is UNCONFIRMED). The ``/-show/showfile/show`` line
  carries the trailing firmware token a desk prints. A GET on a node *container*
  (``/-show/showfile/scene/001``) answers all its values typed, as GetSceneName.c relies on.
* **FX slots 5–8** use the ``fx_type_58`` enum on the wire (GEQ2 = 0) while the descriptor
  declares one ``fx:type`` spec with ``fx_type_14``; the emulator substitutes the enum per
  slot. GEQ-family ``par`` values print as dB (``-3.0``) in node text and accept dB in ``/``
  writes ((dB+15)/30, 0.5 dB grid); other effects print the raw float. ``par`` GETs always
  answer ``,f`` (per-effect ``,i`` typing is not modelled).
* **RTA mirror**: a write to ``/-prefs/rta/source`` or ``/-prefs/rta/pos`` (or
  ``/-action/setrtasrc``) updates ``/-stat/rtasource`` = ``(source-2) + 98*pos`` (Monitor →
  72, "none" → the selected strip), meters.md §5.2.
* **Closed loop**: the analyser sees the spectrum *through the insert of the RTA-source
  strip*: when that strip's insert is ON and selects an FX side whose slot holds a
  GEQ/TEQ (dual: side A = ``par 1-31`` for ``FXnL``, side B = ``par 33-63`` for ``FXnR``;
  stereo: ``par 1-31``), every RTA band is attenuated by the nearest GEQ band's gain plus
  the master — so a correct notch makes a ring decay (:class:`~x32mcp.meters.SyntheticRta`
  cut semantics). The bus fader/mute is deliberately *not* coupled to ring growth.
* **Meters**: ``,s`` ``,si`` ``,sii`` ``,siii`` forms with ``/meters/N`` (slash optional)
  first and the time factor last (meters.md §1.1); a repeat request re-arms the 10 s lease;
  ``/renew`` (bare or with the name) extends an active lease; ``/unsubscribe`` stops meter
  streams only and never drops the ``/xremote`` registration (transport.md §4.4 is
  UNCONFIRMED for a desk); ``/batchsubscribe`` aliases are honoured; ``/subscribe`` and
  ``/formatsubscribe`` are ignored. Types 0–14 carry synthetic levels that follow each
  strip's fader and mute; type 16 is all "no gain reduction".
* **Scenes**: 0 "Init", 1 "The Molecules", 2 "GravelAxe" hold state (names/colours differ),
  3 "Empty" has no data. ``/-action/goscene ,i`` moves ``/-show/prepos/current`` and applies
  the stored state (changed leaves pushed) and is *not* answered (the desk's reply is
  UNCONFIRMED); ``/load`` ``/save`` ``/delete`` ``/rename`` answer ``,si scene 1|0`` as the
  DOC shows. ``--scene-dir`` persists saved scenes as JSON.
* **Faults**: :meth:`FakeDesk.drop_next` loses the next *n replies* (the requests are still
  processed, like a lost datagram on the way back); ``silent`` swallows every inbound and
  outbound datagram except ``/-fake/*`` control; ``latency_ms`` delays every outbound
  datagram. ``/-fake/*`` commands are acknowledged by echoing the datagram (so
  ``X32Connection.request`` can await them), except ``/-fake/silence ,i 1`` which goes silent
  at once; ``/-fake/drop`` and ``/-fake/latency`` apply *after* their ack.
* ``FakeDesk.set`` (Python side) behaves like a front-panel move: it stores, quantises and
  pushes to every client.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import heapq
import itertools
import json
import logging
import math
import re
import socket
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from .descriptor import Descriptor, DescriptorError, NodePath, ParamSpec
from .meters import METER_COUNTS, RTA_METER_TYPE, SyntheticRta, encode_meter_datagram, rta_band_hz, target_for_rta_source
from .nodes import NodeParseError, parse_node_line, render_node_line, split_node_line
from .osc import OscError, OscMessage, decode, encode
from .scales import NEG_INF_DB, PAN_STEPS, Scale, ScaleError, enum_to_index, fader_to_db, quantize

__all__ = ["FakeDesk", "MeterSub", "main"]

log = logging.getLogger(__name__)

Addr = tuple[str, int]

XREMOTE_LEASE_S = 10.0  # transport.md §4.1: "maintains updates for 10 seconds"
METER_LEASE_S = 10.0  # meters.md §1.3
MAX_XREMOTE_CLIENTS = 4  # transport.md §2: "maximum four active clients"
FRAME_PERIOD_S = 0.05  # meters.md §1.2: 50 ms x time factor
SILENCE_LIN = 1e-5  # meters.md §6.3: silence is never 0.0
_FLOAT32 = struct.Struct(">f")
_MISSING = object()

_FX_PAR_RE = re.compile(r"^/fx/(\d)/par/(\d\d)$")
_FX_TYPE_RE = re.compile(r"^/fx/(\d)/type$")
# meters.md §1.1 (VERIFIED, X32.c 531-551): Xmeters[i].command are the literal strings
# "/meters/0"…"/meters/16" WITH the leading slash, and the emulator matches nothing else.
_METER_NAME_RE = re.compile(r"^/meters/(\d+)$")
RTA_DORMANT_DB = -97.0  # the static frame a console's /meters/15 carries while its analyser has not been started (meters.md 2026-09-25)
_METER_NAME_LOOSE_RE = re.compile(r"^/?meters/(\d+)$")  # only to explain the rejection at DEBUG
_SCENE_FIELD_RE = re.compile(r"^/-show/showfile/scene/(\d{3})/(name|notes)$")
_FREQ_K = re.compile(r"^([+-]?\d*)k(\d*)$")

# Defaults in engineering units by param relpath template (family-independent). Everything
# not listed here is computed in FakeDesk._default_value or falls back by scale kind.
_DEFAULTS: dict[str, Any] = {
    "config/icon": 1,
    "mix/on": True, "mix/fader": 0.0, "mix/st": True, "mix/pan": 0, "mix/mono": False, "mix/mlevel": NEG_INF_DB,
    "on": True, "fader": 0.0,  # dca
    "mix/{send:02d}/on": True, "mix/{send:02d}/level": NEG_INF_DB, "mix/{send:02d}/pan": 0,
    "mix/{send:02d}/type": "POST", "mix/{send:02d}/panFollow": False,
    "eq/on": True, "eq/{band}/type": "PEQ", "eq/{band}/g": 0.0, "eq/{band}/q": 2.0,
    "gate/on": False, "gate/mode": "GATE", "gate/thr": -80.0, "gate/range": 60.0, "gate/attack": 10.0,
    "gate/hold": 10.0, "gate/release": 151.0, "gate/keysrc": "OFF",
    "gate/filter/on": False, "gate/filter/type": "3.0", "gate/filter/f": 1000.0,
    "dyn/on": False, "dyn/mode": "COMP", "dyn/det": "RMS", "dyn/env": "LOG", "dyn/thr": 0.0, "dyn/ratio": "3.0",
    "dyn/knee": 2.0, "dyn/mgain": 0.0, "dyn/attack": 10.0, "dyn/hold": 10.0, "dyn/release": 151.0, "dyn/pos": "POST",
    "dyn/keysrc": "OFF", "dyn/mix": 100.0, "dyn/auto": False,
    "dyn/filter/on": False, "dyn/filter/type": "3.0", "dyn/filter/f": 1000.0,
    "insert/on": False, "insert/pos": "POST", "insert/sel": "OFF",
    "preamp/trim": 0.0, "preamp/invert": False, "preamp/hpon": False, "preamp/hpslope": "24", "preamp/hpf": 100.0,
    "delay/on": False, "delay/time": 0.3,
    "grp/dca": 0, "grp/mute": 0,
    "automix/group": "OFF", "automix/weight": 0.0,
    "gain": 24.0, "phantom": False,  # headamp: 24 dB is raw 0.5 on the -12..60 range
    "routing/routswitch": "REC",
    "routing/IN/1-8": "AN1-8", "routing/IN/9-16": "AN9-16", "routing/IN/17-24": "AN17-24", "routing/IN/25-32": "AN25-32",
    "routing/IN/AUX": "AUX1-4",
    "routing/PLAY/1-8": "AN1-8", "routing/PLAY/9-16": "AN9-16", "routing/PLAY/17-24": "AN17-24",
    "routing/PLAY/25-32": "AN25-32", "routing/PLAY/AUX": "AUX1-4",
    "routing/AES50A/1-8": "OUT1-8", "routing/AES50A/9-16": "OUT9-16", "routing/AES50A/17-24": "P161-8",
    "routing/AES50A/25-32": "P169-16", "routing/AES50A/33-40": "AUX1-6/Mon", "routing/AES50A/41-48": "AuxIN1-6/TB",
    "routing/AES50B/1-8": "OUT1-8", "routing/AES50B/9-16": "OUT9-16", "routing/AES50B/17-24": "P161-8",
    "routing/AES50B/25-32": "P169-16", "routing/AES50B/33-40": "AUX1-6/Mon", "routing/AES50B/41-48": "AuxIN1-6/TB",
    "routing/CARD/1-8": "AN1-8", "routing/CARD/9-16": "AN9-16", "routing/CARD/17-24": "AN17-24", "routing/CARD/25-32": "AN25-32",
    "routing/OUT/1-4": "OUT1-4", "routing/OUT/5-8": "OUT5-8", "routing/OUT/9-12": "OUT9-12", "routing/OUT/13-16": "OUT13-16",
    # output taps (fx_routing_scenes.md §4.7): main/NN/src = MixBus NN is computed in _default_value; every tap POST, not inverted;
    # the rear AUX outs carry nothing until somebody patches them
    "main/{n:02d}/pos": "POST", "main/{n:02d}/invert": False,
    "aux/{idx:02d}/src": "OFF", "aux/{idx:02d}/pos": "POST", "aux/{idx:02d}/invert": False,
    "solo/level": 0.0, "solo/source": "LR", "solo/sourcetrim": 0.0, "solo/chmode": "PFL", "solo/busmode": "AFL",
    "solo/dcamode": "AFL", "solo/exclusive": False, "solo/followsel": True, "solo/followsolo": True, "solo/dimatt": -20.0,
    "solo/dim": False, "solo/mono": False, "solo/delay": False, "solo/delaytime": 0.3, "solo/masterctrl": False,
    "solo/mute": False, "solo/dimpfl": False,
    "talk/enable": False, "talk/source": "INT",
    "talk/A/level": 0.0, "talk/A/dim": False, "talk/A/latch": False, "talk/A/destmap": 0,
    "talk/B/level": 0.0, "talk/B/dim": False, "talk/B/latch": False, "talk/B/destmap": 0,
    "mono/mode": "LR+M", "mono/link": False,
    "prepos/current": 0, "showfile/show/name": "FakeShow",
    "selidx": 0, "solo": False, "talk/A": False, "talk/B": False, "rtasource": 168, "screen/screen": 0, "screen/METER/page": 0,
    "rtamodeeq": "BAR", "rtamodegeq": "BAR", "rtaeqpre": False, "rtageqpost": False,
    "geqonfdr": False, "geqpos": 0, "dcaspill": 0,
    "show_control": "SCENES",
    "rta/visibility": "50%", "rta/gain": 24.0, "rta/autogain": True, "rta/source": "MAIN", "rta/pos": "POST",
    "rta/mode": "BAR", "rta/options": 0, "rta/det": "RMS", "rta/decay": 1.0, "rta/peakhold": "OFF",
    "goscene": 0, "gocue": 0, "gosnippet": 0,
}
_EQ_FREQS: dict[int, tuple[float, ...]] = {4: (100.0, 1000.0, 4000.0, 10000.0), 6: (100.0, 300.0, 1000.0, 3000.0, 6000.0, 10000.0)}
_FX_TYPES: dict[int, str] = {1: "HALL", 2: "DLY", 3: "CRS", 4: "RPLT", 5: "DES2", 6: "P1A", 7: "LIM", 8: "PHAS"}
_STRIP_NAMES: dict[str, str] = {"ch": "Ch{n:02d}", "auxin": "Aux{n}", "bus": "Bus{n:02d}", "mtx": "Mtx{n}", "dca": "DCA{n}"}
_SCENES: dict[int, dict[str, Any]] = {
    0: {"name": "Init", "names": {}},
    1: {"name": "The Molecules", "names": {
        "/ch/01": ("Kick", "RD"), "/ch/02": ("Snare", "YE"), "/ch/03": ("Bass", "BL"), "/ch/04": ("Gtr", "GN"),
        "/ch/05": ("Vox Tony", "CY"), "/ch/06": ("Keys", "MG"), "/bus/01": ("Tony IEM", "CY"), "/bus/02": ("Dave IEM", "GN"),
        "/bus/03": ("Wedge A", "WH")}},
    2: {"name": "GravelAxe", "names": {
        "/ch/01": ("Kick", "RD"), "/ch/02": ("Snare", "YE"), "/ch/03": ("Bass", "BL"), "/ch/04": ("Gtr L", "GN"),
        "/ch/05": ("Gtr R", "GN"), "/ch/06": ("Vox", "CY"), "/bus/01": ("Wedge L", "WH"), "/bus/02": ("Wedge R", "WH"),
        "/bus/03": ("Drum IEM", "RD")}},
    3: {"name": "Empty", "names": None},  # hasdata 0
}
_METER_STRIPS_70: tuple[str, ...] = tuple(
    [f"/ch/{n:02d}" for n in range(1, 33)] + [f"/auxin/{n:02d}" for n in range(1, 9)] + [f"/fxrtn/{n:02d}" for n in range(1, 9)]
    + [f"/bus/{n:02d}" for n in range(1, 17)] + [f"/mtx/{n:02d}" for n in range(1, 7)]
)
_METER_STRIPS_MAIN: tuple[str, ...] = tuple([f"/bus/{n:02d}" for n in range(1, 17)] + [f"/mtx/{n:02d}" for n in range(1, 7)] + ["/main/st", "/main/st", "/main/m"])


def _f32(x: float) -> float:
    """Round to the float32 the wire carries (so state == what a client reads back)."""
    return _FLOAT32.unpack(_FLOAT32.pack(x))[0]


def _clamp01(x: float) -> float:
    return 0.0 if x <= 0.0 else 1.0 if x >= 1.0 else x


def _parse_float_token(tok: Any) -> float:
    """Free-form numeric text (``-3.0``, ``+1.5``, ``1k39``) or number -> float; ValueError otherwise."""
    if isinstance(tok, bool):
        return 1.0 if tok else 0.0
    if isinstance(tok, (int, float)):
        return float(tok)
    s = str(tok).strip().strip('"')
    if s == "-oo":
        return NEG_INF_DB
    try:
        return float(s)
    except ValueError:
        m = _FREQ_K.match(s)
        if not m:
            raise ValueError(f"not a number: {tok!r}") from None
        a, b = m.group(1), m.group(2)
        v = float(int(a) if a not in ("", "+", "-") else 0) * 1000.0
        if b:
            v += int(b) * 1000.0 / 10 ** len(b)
        return v


def _rearm_reader(transport: asyncio.DatagramTransport | None, n_errors: int) -> None:
    """Same workaround as connection.py: a proactor (Windows) datagram transport stops reading
    after ``error_received`` (ICMP port-unreachable when a client went away while we were
    pushing to it); re-arm ``_loop_reading``. No-op on selector loops."""
    if transport is None or transport.is_closing():
        return
    if getattr(transport, "_read_fut", _MISSING) is not None:
        return
    rearm = getattr(transport, "_loop_reading", None)
    if rearm is None:
        return

    def _kick() -> None:
        if not transport.is_closing() and getattr(transport, "_read_fut", _MISSING) is None:
            try:
                rearm()
            except Exception:
                log.debug("could not re-arm datagram transport", exc_info=True)

    asyncio.get_running_loop().call_later(min(0.01 * n_errors, 0.5), _kick)


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, desk: "FakeDesk") -> None:
        self._desk = desk
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: Any) -> None:
        try:
            self._desk._on_datagram(data, (addr[0], addr[1]))
        except Exception:
            log.exception("fakedesk: datagram handler failed")

    def error_received(self, exc: Exception) -> None:
        self._desk._socket_errors += 1
        log.debug("fakedesk: socket error (ignored): %r", exc)
        _rearm_reader(self.transport, self._desk._socket_errors)


@dataclass
class MeterSub:
    """One ``/meters`` (or ``/batchsubscribe``) lease of one client."""

    client: Addr
    meter_type: int
    reply_address: str  # "/meters/N" or the batchsubscribe alias
    args: tuple[int, ...]  # channel/bank ints before the time factor
    interval_s: float
    expiry: float  # loop.time()
    task: asyncio.Task | None = None
    frames_sent: int = 0


class FakeDesk:
    """An X32 on ``host:port`` (port 0 = ephemeral; see :attr:`port` after :meth:`start`).

    ``d`` is the device descriptor; ``rta`` an optional :class:`~x32mcp.meters.SyntheticRta`
    (default: seed 1, the descriptor's band centres). ``model``/``firmware``/``server_version``
    are what ``/info`` and ``/xinfo`` report. ``scene_dir`` persists ``/save``d scenes.
    """

    def __init__(
        self,
        d: Descriptor,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        name: str = "X32-FAKE",
        rta: SyntheticRta | None = None,
        model: str = "X32RACK",
        firmware: str = "4.06",
        server_version: str = "V2.07",
        scene_dir: Path | str | None = None,
        rta_dormant: bool = False,
    ) -> None:
        self.d = d
        # meters.md 2026-09-25 item 1: a real console's analyser is not running after a power-up until its METERS/RTA page has
        # been shown once; /meters/15 carries one static flat frame until then. Writing screen 1 + METER page 4 starts it.
        self.rta_dormant = bool(rta_dormant)
        self.host = host
        self.port = int(port)
        self.name = name
        self.model = model
        self.firmware = firmware
        self.server_version = server_version
        self.scene_dir = Path(scene_dir) if scene_dir else None
        self.rta: SyntheticRta = rta if rta is not None else SyntheticRta(seed=1, band_hz=rta_band_hz(d))

        self.state: dict[str, Any] = {}
        self.scenes: dict[int, dict[str, Any]] = {}
        self.clients: dict[Addr, float] = {}  # /xremote registrations -> expiry (loop.time())
        self.meters: dict[tuple[Addr, int], MeterSub] = {}
        self.xremote_lease_s = XREMOTE_LEASE_S
        self.meter_lease_s = METER_LEASE_S

        # faults
        self.silent = False
        self.latency_ms = 0.0
        # Realism switch: channels with nothing plugged in. A real input with no source reads the preamp's own
        # floor on the meters whatever the fader says; the default fake gives every channel programme.
        self.unplugged: set[int] = set()
        self._drop_left = 0
        self._drop_inbound_left = 0
        # Asynchronous application of writes (REVIEW_BRIEF §5): address prefix -> delay in ms.
        # A delayed write is invisible to GET, /node and the closed loop until it is applied;
        # {} (the default) applies everything instantly, exactly as before.
        self.apply_delay_ms: dict[str, float] = {}
        self._apply_heap: list[tuple[float, int, str, Any, Addr | None]] = []
        self._apply_seq = itertools.count()
        self._apply_timer: asyncio.TimerHandle | None = None
        self.applied_late = 0

        self.rx_count = 0
        self.decode_errors = 0
        self.dropped = 0
        self._socket_errors = 0

        self._transport: asyncio.DatagramTransport | None = None
        self._proto: _Protocol | None = None
        self._specs: dict[str, ParamSpec] = {}
        self._cuts_dirty = True
        self._geq_scale = d.scale(str(d.geq.get("gain_scale", "geq_gain")))
        self._geq_hz: tuple[float, ...] = tuple(float(h) for h in d.geq["band_hz"])
        self._geq_types = frozenset(list(d.geq.get("fx_types_dual", ())) + list(d.geq.get("fx_types_stereo", ())))
        self._geq_dual = frozenset(d.geq.get("fx_types_dual", ()))
        self._fx14 = d.enum("fx_type_14")
        self._fx58 = d.enum("fx_type_58")
        type_spec = d.param("fx", "type")
        self._fx58_type_spec = dataclasses.replace(
            type_spec, scale=Scale("enum", values=self._fx58), enum=self._fx58, enum_name="fx_type_58"
        )
        self._handlers: dict[str, Callable[[OscMessage, bytes, Addr], None]] = {
            "/info": self._h_info, "/xinfo": self._h_xinfo, "/status": self._h_status,
            "/xremote": self._h_xremote, "/unsubscribe": self._h_unsubscribe, "/renew": self._h_renew,
            "/meters": self._h_meters, "/batchsubscribe": self._h_batchsubscribe,
            "/subscribe": self._h_ignored, "/formatsubscribe": self._h_ignored,
            "/node": self._h_node, "/": self._h_slash,
            "/save": self._h_save, "/load": self._h_load, "/delete": self._h_delete, "/rename": self._h_rename,
            "/-action/setrtasrc": self._h_setrtasrc,
        }
        self._build_initial_state()
        self._build_scenes()
        if self.scene_dir is not None:
            self._load_scene_dir()
        self._mirror_rta_stat(None)

    # -- lifecycle ---------------------------------------------------------------------------

    async def start(self) -> tuple[str, int]:
        """Bind the socket and start the RTA generator. Returns ``(host, port)``."""
        if self._transport is not None:
            return self.host, self.port
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setblocking(False)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            except OSError:
                pass
            sock.bind((self.host, self.port))
            self._transport, self._proto = await loop.create_datagram_endpoint(lambda: _Protocol(self), sock=sock)
        except OSError:
            sock.close()
            raise
        self.port = int(self._transport.get_extra_info("sockname")[1])
        await self.rta.start()
        log.info("fakedesk %s (%s FW %s) listening on %s:%d", self.name, self.model, self.firmware, self.host, self.port)
        return self.host, self.port

    async def stop(self) -> None:
        """Stop meter streams and the RTA, close the socket. Idempotent."""
        tasks = [s.task for s in self.meters.values() if s.task is not None]
        self.meters.clear()
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=1.0)
        await self.rta.stop()
        if self._apply_timer is not None:
            self._apply_timer.cancel()
            self._apply_timer = None
        self._apply_heap.clear()
        transport, self._transport = self._transport, None
        if transport is not None:
            transport.close()
        self.clients.clear()

    async def __aenter__(self) -> "FakeDesk":
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.stop()

    @property
    def address(self) -> tuple[str, int]:
        return self.host, self.port

    @property
    def advertised_ip(self) -> str:
        """The IP ``/xinfo`` and ``/status`` report (the bound host, or the machine's address
        when bound to 0.0.0.0)."""
        if self.host not in ("0.0.0.0", ""):
            return self.host
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"

    @property
    def stats(self) -> dict[str, int]:
        return {"rx": self.rx_count, "decode_errors": self.decode_errors, "dropped": self.dropped,
                "socket_errors": self._socket_errors, "clients": len(self.clients), "meter_subs": len(self.meters)}

    # -- Python-side access --------------------------------------------------------------------

    def get(self, address: str) -> Any:
        """Raw OSC value stored at ``address`` (None when unknown)."""
        return self.state.get(address)

    def value(self, address: str) -> Any:
        """Engineering value at ``address`` (dB, Hz, enum token, bool, ...)."""
        spec = self._specs.get(address)
        if spec is None:
            return None
        return self._value_from_raw(address, spec, self.state[address])

    def spec(self, address: str) -> ParamSpec | None:
        """The (slot-corrected) :class:`ParamSpec` behind ``address``."""
        return self._specs.get(address)

    def set(self, address: str, raw: Any) -> bool:
        """Store a raw OSC value like a front-panel move: coerced and quantised, pushed to every
        ``/xremote`` client when it changed. Returns True when the value changed.
        Raises ``KeyError`` for an unknown address, ``ValueError`` for an unusable value."""
        spec = self._specs.get(address)
        if spec is None:
            raise KeyError(address)
        value = self._coerce(address, spec, raw)
        changed = self._store(address, value)
        self._commit([(address, changed)], exclude=None)
        return changed

    def set_value(self, address: str, value: Any) -> bool:
        """Like :meth:`set` but takes an engineering value (``-12.0`` dB, ``"PEQ"``, ``True``)."""
        spec = self._specs.get(address)
        if spec is None:
            raise KeyError(address)
        return self.set(address, self._raw_from_value(address, spec, value))

    def drop_next(self, n: int) -> None:
        """Lose the next ``n`` replies (requests are still applied)."""
        self._drop_left = max(0, int(n))

    def drop_inbound_next(self, n: int) -> None:
        """Lose the next ``n`` inbound datagrams (``/-fake/*`` excepted): a SET lost on the way in."""
        self._drop_inbound_left = max(0, int(n))

    def set_apply_delay(self, prefix: str, ms: float) -> None:
        """Writes to addresses under ``prefix`` ("" = every address) are applied ``ms`` later
        (longest matching prefix wins; 0 removes the delay)."""
        if ms and ms > 0:
            self.apply_delay_ms[str(prefix)] = float(ms)
        else:
            self.apply_delay_ms.pop(str(prefix), None)

    @property
    def pending_writes(self) -> int:
        """Writes received but not yet applied (see ``apply_delay_ms``)."""
        return len(self._apply_heap)

    def flush_pending(self) -> int:
        """Apply every delayed write now, in due order. Returns how many were applied."""
        return self._drain(everything=True)

    def _apply_delay_for(self, address: str) -> float:
        best, best_len = 0.0, -1
        for prefix, ms in self.apply_delay_ms.items():
            if address.startswith(prefix) and len(prefix) > best_len:
                best, best_len = float(ms), len(prefix)
        return best / 1000.0

    def _apply(self, items: Iterable[tuple[str, Any]], exclude: Addr | None, *, verbatim: bytes | None = None) -> None:
        """Apply ``[(address, raw)]`` writes from the wire: now, or after the address's
        ``apply_delay_ms``. ``verbatim`` (a multi-argument node SET) is forwarded as received
        when everything applies now; late leaves are pushed one by one instead."""
        items = list(items)
        now_items: list[tuple[str, bool]] = []
        queued = 0
        for address, raw in items:
            delay = self._apply_delay_for(address) if self.apply_delay_ms else 0.0
            if delay <= 0.0:
                now_items.append((address, self._store(address, raw)))
                continue
            loop = asyncio.get_running_loop()
            heapq.heappush(self._apply_heap, (loop.time() + delay, next(self._apply_seq), address, raw, exclude))
            queued += 1
        if verbatim is not None and queued == 0:
            if any(changed for _, changed in now_items):
                self._push(verbatim, exclude=exclude)
            self._commit(now_items, exclude=exclude, push=False)
        else:
            self._commit(now_items, exclude=exclude)
        if queued:
            self._arm_apply_timer()

    def _arm_apply_timer(self) -> None:
        if self._apply_timer is not None:
            self._apply_timer.cancel()
            self._apply_timer = None
        if self._apply_heap and self._transport is not None:
            loop = asyncio.get_running_loop()
            self._apply_timer = loop.call_later(max(0.0, self._apply_heap[0][0] - loop.time()), self._drain)

    def _drain(self, everything: bool = False) -> int:
        """Apply every queued write that is due (all of them with ``everything``), oldest due first."""
        self._apply_timer = None
        n = 0
        if self._apply_heap:
            now = asyncio.get_running_loop().time()
            while self._apply_heap and (everything or self._apply_heap[0][0] <= now + 1e-9):
                _due, _seq, address, raw, exclude = heapq.heappop(self._apply_heap)
                self._commit([(address, self._store(address, raw))], exclude=exclude)
                n += 1
        if n:
            self.applied_late += n
            self._after_change()
        self._arm_apply_timer()
        return n

    def node_line(self, path: str) -> str:
        """The ``/node`` reply text for ``path`` (without the trailing newline)."""
        line = self._node_text(path)
        if line is None:
            raise DescriptorError(f"{path!r} is not a node path")
        return line

    # -- initial state ---------------------------------------------------------------------------

    def _effective_spec(self, spec: ParamSpec, vars: Mapping[str, Any]) -> ParamSpec:
        # per fx_routing_scenes.md §1.3: /fx/5-8/type has its own 34-entry enum (GEQ2 = 0)
        if spec.key == "fx:type" and int(vars.get("n", 0)) >= 5:
            return self._fx58_type_spec
        return spec

    def _build_initial_state(self) -> None:
        d = self.d
        for node in d.nodes():
            for addr, field, spec in zip(node.addresses, node.fields, node.specs):
                eff = self._effective_spec(spec, node.vars)
                self._specs[addr] = eff
                self.state[addr] = self._to_stored(addr, eff, self._raw_from_value(addr, eff, self._default_value(eff, node.vars)))
        # params no node covers (automix, prefs name/show_control, stat leaves, actions)
        for spec in d.iter_params():
            if "{send" in spec.relpath:
                continue  # even sends have no pan/type/panFollow; the nodes list what exists
            for vars in self._var_combos(spec):
                n = vars.get("n", 0)
                if spec.family == "fx" and spec.relpath.startswith("source/") and n > 4:
                    continue  # slots 5-8 are insert-only (§1.1)
                if spec.family == "ch" and spec.relpath.startswith("automix/") and n > 8:
                    continue  # automix exists on ch 01-08 only (§4.11)
                if spec.family == "main" and vars.get("id") == "m" and spec.relpath == "mix/pan":
                    continue
                try:
                    addr = spec.address(**vars)
                except DescriptorError:
                    continue
                if addr in self.state:
                    continue
                eff = self._effective_spec(spec, vars)
                self._specs[addr] = eff
                self.state[addr] = self._to_stored(addr, eff, self._raw_from_value(addr, eff, self._default_value(eff, vars)))

    @staticmethod
    def _var_combos(spec: ParamSpec) -> list[dict[str, Any]]:
        axes: list[list[Any]] = []
        for name in spec.vars:
            r = spec.var_ranges.get(name)
            if r is None:
                return []
            axes.append(sorted(r) if isinstance(r, frozenset) else list(range(r[0], r[1] + 1)))
        return [dict(zip(spec.vars, combo)) for combo in itertools.product(*axes)]

    def _default_value(self, spec: ParamSpec, vars: Mapping[str, Any]) -> Any:
        """Engineering default for one parameter (see module doc)."""
        rel, fam = spec.relpath, spec.family
        n = vars.get("n")
        sid = vars.get("id")
        if rel == "config/name":
            if fam == "main":
                return "Main" if sid == "st" else "M/C"
            if fam == "fxrtn":
                return f"FX{(int(n) + 1) // 2}{'L' if int(n) % 2 else 'R'}"
            return _STRIP_NAMES.get(fam, "{n}").format(n=n)
        if rel == "config/color":
            colours = self.d.enum("color")
            i = int(n) if n is not None else (1 if sid == "st" else 2)
            return colours[1 + (i - 1) % 7]  # RD GN YE BL MG CY WH, cycling
        if rel == "config/source":
            src = self.d.enum("ch_source")
            return src[int(n)] if fam == "ch" else src[32 + int(n)]  # IN01.. / AUX1..6, USBL, USBR
        if rel == "eq/{band}/f":
            bands = self.d.strips[fam].eq_bands or 4
            table = _EQ_FREQS.get(bands) or _EQ_FREQS[4]
            return table[(int(vars.get("band", 1)) - 1) % len(table)]
        if fam == "fx":
            if rel == "type":
                return _FX_TYPES.get(int(n), "HALL")
            if rel.startswith("source/"):
                return f"MIX{12 + int(n)}"  # factory default: Mix 13-16 feed FX 1-4
            if rel.startswith("par/"):
                return 0.5  # raw 0.5 = 0 dB on a GEQ
        if fam == "outputs" and rel == "main/{n:02d}/src":
            # factory-style patch: XLR OUT n carries MixBus n (output_src index 3 + n, fx_routing_scenes.md §4.7);
            # a real desk's shipped OUT 15/16 = Main L/R is UNCONFIRMED (§13 item 13), so the fake keeps the pattern
            return self.d.enum("output_src")[3 + int(n)]
        if fam == "show" and rel.startswith("showfile/scene/"):
            idx = int(vars.get("idx", 0))
            sc = _SCENES.get(idx)
            if rel.endswith("/name"):
                return sc["name"] if sc else ""
            if rel.endswith("/hasdata"):
                return 1 if sc and sc["names"] is not None else 0
            return 0 if rel.endswith("/safes") else ""
        if spec.key == "prefs:name":
            return self.name
        if rel in _DEFAULTS:
            return _DEFAULTS[rel]
        k = spec.scale.kind
        if k == "level":
            return 0.0
        if k in ("lin", "log"):
            lo, hi = spec.scale.lo, spec.scale.hi
            return 0.0 if min(lo, hi) <= 0.0 <= max(lo, hi) else lo
        if k == "enum":
            return spec.scale.values[0]
        if k == "bool":
            return False
        if k == "str":
            return ""
        return 0

    def _build_scenes(self) -> None:
        base = self._scene_state_copy()
        for idx, sc in _SCENES.items():
            names = sc["names"]
            if names is None:
                continue
            state = dict(base)
            for prefix, (name, colour) in names.items():
                for rel, val in (("config/name", name), ("config/color", colour)):
                    addr = f"{prefix}/{rel}"
                    spec = self._specs.get(addr)
                    if spec is not None:
                        state[addr] = self._to_stored(addr, spec, spec.to_raw(val))
            self.scenes[idx] = {"name": sc["name"], "notes": "", "state": state}

    def _scene_state_copy(self) -> dict[str, Any]:
        return {a: v for a, v in self.state.items() if not a.startswith("/-")}

    # -- value conversion ------------------------------------------------------------------------

    def _fx_slot_token(self, slot: int) -> str | None:
        raw = self.state.get(f"/fx/{slot}/type")
        table = self._fx14 if slot <= 4 else self._fx58
        if isinstance(raw, int) and 0 <= raw < len(table):
            return table[raw]
        return None

    def _is_geq_slot(self, slot: int) -> bool:
        return self._fx_slot_token(slot) in self._geq_types

    def _value_from_raw(self, address: str, spec: ParamSpec, raw: Any) -> Any:
        m = _FX_PAR_RE.match(address)
        if m:
            if self._is_geq_slot(int(m.group(1))):
                g = float(raw) * 30.0 - 15.0  # fx_routing_scenes.md §2.2
                return "0.0" if abs(g) < 0.05 else f"{g:.1f}"
            return format(float(raw), ".4g")
        return spec.to_value(raw)

    def _raw_from_value(self, address: str, spec: ParamSpec, value: Any) -> Any:
        m = _FX_PAR_RE.match(address)
        if m:
            x = _parse_float_token(value)
            if self._is_geq_slot(int(m.group(1))):
                return self._geq_scale.to_raw(x)  # dB -> (dB+15)/30 on the 0.5 dB grid
            return _clamp01(x)
        return spec.to_raw(value)

    def _quantise_raw(self, address: str, spec: ParamSpec, x: float) -> float:
        """Snap a raw float to the parameter's grid (transport.md §5.2: the desk rounds to the
        nearest known value)."""
        sc = spec.scale
        steps: int | None
        if sc.kind == "pan":
            steps = sc.steps or PAN_STEPS
        elif sc.kind in ("level", "lin", "log"):
            steps = sc.steps
        else:
            steps = None
        m = _FX_PAR_RE.match(address)
        if m and self._is_geq_slot(int(m.group(1))):
            steps = self._geq_scale.steps
        x = _clamp01(float(x))
        return quantize(x, steps) if steps else x

    def _coerce(self, address: str, spec: ParamSpec, arg: Any) -> Any:
        """Wire argument of a SET -> stored raw value; ValueError when the desk would ignore it."""
        t = spec.osc_type
        k = spec.scale.kind
        if t == "f":
            if isinstance(arg, str):
                x = _parse_float_token(arg)
            elif isinstance(arg, (int, float)) and not isinstance(arg, bool):
                x = float(arg)
            else:
                raise ValueError(f"{address}: cannot store {arg!r} as a float")
            if math.isnan(x) or math.isinf(x):
                raise ValueError(f"{address}: bad float {arg!r}")
            return self._quantise_raw(address, spec, x)
        if t == "i":
            if isinstance(arg, str):  # enum tokens are accepted as strings (transport.md §5.2)
                if k == "enum":
                    return enum_to_index(arg, spec.scale.values)
                if k == "bool":
                    return 1 if spec.scale.clamp(arg) else 0
                return int(_parse_float_token(arg))
            if isinstance(arg, bool):
                v = int(arg)
            elif isinstance(arg, (int, float)):
                v = int(round(arg))
            else:
                raise ValueError(f"{address}: cannot store {arg!r} as an int")
            if k == "enum":
                if not 0 <= v < len(spec.scale.values):
                    raise ValueError(f"{address}: enum index {v} out of range")
                return v
            if k == "bool":
                return 1 if v else 0
            if spec.node_fmt.startswith("bits"):
                width = int(spec.node_fmt[4:]) if spec.node_fmt[4:].isdigit() else 32
                return max(0, min((1 << width) - 1, v))
            return int(spec.scale.clamp(v))
        return str(arg)

    def _to_stored(self, address: str, spec: ParamSpec, raw: Any) -> Any:
        if spec.osc_type == "f":
            return _f32(float(raw))
        if spec.osc_type == "i":
            return int(raw)
        return str(raw)

    def _store(self, address: str, raw: Any) -> bool:
        """Store a coerced raw value; True when it differs from what was there."""
        spec = self._specs[address]
        new = self._to_stored(address, spec, raw)
        old = self.state.get(address)
        changed = old != new
        self.state[address] = new
        if changed and (address.startswith("/-prefs/rta/") or "/insert/" in address or address.startswith("/fx/") or address == "/-stat/selidx"):
            self._cuts_dirty = True
        if self.rta_dormant and address in ("/-stat/screen/screen", "/-stat/screen/METER/page"):
            if int(self.state.get("/-stat/screen/screen", 0) or 0) == 1 and int(self.state.get("/-stat/screen/METER/page", 0) or 0) == 4:
                self.rta_dormant = False        # the console shows its RTA page: the analyser starts and stays running
        return changed

    # -- node text ---------------------------------------------------------------------------------

    def _node_values(self, node: NodePath) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for addr, field, spec in zip(node.addresses, node.fields, node.specs):
            raw = self.state.get(addr)
            if raw is None:
                break
            out[field] = self._value_from_raw(addr, self._specs.get(addr, spec), raw)
        return out

    def _node_text(self, path: str) -> str | None:
        """Reply text for ``/node ,s path`` (no newline), None when the path is unknown."""
        p = "/" + path.strip().lstrip("/")
        try:
            node = self.d.node(p)
        except DescriptorError:
            node = None
        if node is not None:
            line = render_node_line(node.path, node, self._node_values(node))
            if node.path == "/-show/showfile/show":
                line += f' "{self.firmware}"'  # the desk appends the firmware string (transport.md §6.4)
            return line
        spec = self._specs.get(p)
        if spec is None:
            return None
        return render_node_line(p, [spec], {spec.relpath: self._value_from_raw(p, spec, self.state[p])}, pad=False)

    def _node_for_write(self, path: str) -> NodePath | None:
        for candidate in (path, path + "/config"):  # DOC 4585: "ch/01 newname 10 CY 1" sets /ch/01/config
            try:
                return self.d.node(candidate)
            except DescriptorError:
                continue
        return None

    def _parse_node_write(self, text: str) -> list[tuple[str, Any]]:
        """Parse a ``/`` write into ``[(address, raw)]`` for every leaf that was listed."""
        path, _toks = split_node_line(text)
        node = self._node_for_write(path)
        out: list[tuple[str, Any]] = []
        if node is not None:
            values = parse_node_line(text, node)
            for addr, field, spec in zip(node.addresses, node.fields, node.specs):
                v = values.get(field)
                if v is None:
                    continue  # partial trailing list / unreadable token
                eff = self._specs.get(addr, spec)
                try:
                    out.append((addr, self._raw_from_value(addr, eff, v)))
                except (ScaleError, ValueError, NodeParseError) as e:
                    log.debug("fakedesk: / write %s: %s", addr, e)
            return out
        spec = self._specs.get(path)
        if spec is None:
            raise DescriptorError(f"{path!r} is neither a node nor a parameter")
        values = parse_node_line(text, [spec])
        v = values.get(spec.relpath)
        if v is not None:
            out.append((path, self._raw_from_value(path, spec, v)))
        return out

    # -- receive / dispatch -------------------------------------------------------------------

    def _on_datagram(self, data: bytes, addr: Addr) -> None:
        self.rx_count += 1
        try:
            msg = decode(data)
        except OscError as e:
            self.decode_errors += 1
            log.debug("fakedesk: undecodable datagram from %s (%d B): %s", addr, len(data), e)
            return
        if msg.address.startswith("/-fake/"):
            self._h_fake(msg, data, addr)
            return
        if self.silent:
            return
        if self._drop_inbound_left > 0:
            self._drop_inbound_left -= 1
            self.dropped += 1
            log.debug("fakedesk: dropping inbound %s (%d more)", msg.address, self._drop_inbound_left)
            return
        log.debug("fakedesk: rx %s from %s", msg, addr)
        handler = self._handlers.get(msg.address)
        if handler is not None:
            handler(msg, data, addr)
        else:
            self._h_param(msg, data, addr)
        self._after_change()

    def _send(self, data: bytes, addr: Addr) -> None:
        """Every outbound datagram: honours ``silent`` and ``latency_ms``."""
        if self.silent:
            return
        t = self._transport
        if t is None or t.is_closing():
            return
        if self.latency_ms > 0:
            asyncio.get_running_loop().call_later(self.latency_ms / 1000.0, self._sendto_now, data, addr)
        else:
            self._sendto_now(data, addr)

    def _sendto_now(self, data: bytes, addr: Addr) -> None:
        t = self._transport
        if t is None or t.is_closing() or self.silent:
            return
        try:
            t.sendto(data, addr)
        except OSError as e:
            log.debug("fakedesk: send to %s failed: %s", addr, e)

    def _reply(self, data: bytes, addr: Addr) -> None:
        """A direct reply to a request: subject to :meth:`drop_next`."""
        if self._drop_left > 0:
            self._drop_left -= 1
            self.dropped += 1
            log.debug("fakedesk: dropping reply to %s (%d more)", addr, self._drop_left)
            return
        self._send(data, addr)

    def _push(self, data: bytes, exclude: Addr | None) -> None:
        """Forward to every live ``/xremote`` client except ``exclude`` (transport.md §4.2)."""
        now = asyncio.get_running_loop().time()
        for client, expiry in list(self.clients.items()):
            if expiry <= now:
                del self.clients[client]
                continue
            if client == exclude:
                continue
            self._send(data, client)

    def _push_leaf(self, address: str, exclude: Addr | None) -> None:
        self._push(encode(address, self.state[address]), exclude)

    def _commit(self, stored: Iterable[tuple[str, bool]], exclude: Addr | None, *, push: bool = True) -> None:
        for address, changed in stored:
            if changed and push:
                self._push_leaf(address, exclude)
            if changed or address.startswith("/-action/"):
                self._side_effects(address, exclude)

    def _after_change(self) -> None:
        if self._cuts_dirty:
            self._refresh_cuts()

    # -- handlers: identity -------------------------------------------------------------------

    def _h_info(self, msg: OscMessage, data: bytes, addr: Addr) -> None:
        # transport.md §3.1: ,ssss server_version "osc-server" model firmware
        self._reply(encode("/info", self.server_version, "osc-server", self.model, self.firmware), addr)

    def _h_xinfo(self, msg: OscMessage, data: bytes, addr: Addr) -> None:
        # transport.md §3.2: ,ssss ip name model firmware (also answers broadcast)
        self._reply(encode("/xinfo", self.advertised_ip, self.name, self.model, self.firmware), addr)

    def _h_status(self, msg: OscMessage, data: bytes, addr: Addr) -> None:
        # transport.md §3.3: ,sss "active" ip "osc-server"
        self._reply(encode("/status", "active", self.advertised_ip, "osc-server"), addr)

    def _h_ignored(self, msg: OscMessage, data: bytes, addr: Addr) -> None:
        log.debug("fakedesk: %s not implemented (ignored)", msg.address)

    # -- handlers: xremote / meters -----------------------------------------------------------

    def _h_xremote(self, msg: OscMessage, data: bytes, addr: Addr) -> None:
        now = asyncio.get_running_loop().time()
        for client, expiry in list(self.clients.items()):
            if expiry <= now:
                del self.clients[client]
        if addr in self.clients or len(self.clients) < MAX_XREMOTE_CLIENTS:
            self.clients[addr] = now + self.xremote_lease_s
        else:
            log.warning("fakedesk: no room for xremote client %s (%d registered)", addr, len(self.clients))

    def _meter_name(self, arg: Any) -> tuple[int, str] | None:
        """``"/meters/N"`` -> ``(N, reply address)``, else ``None`` (meters.md §1.1)."""
        if not isinstance(arg, str):
            return None
        text = arg.strip()
        m = _METER_NAME_RE.match(text)
        if not m:
            if _METER_NAME_LOOSE_RE.match(text):
                log.debug("fakedesk: rejecting %r — meters.md §1.1: the console matches the leading-slash form only", arg)
            return None
        mtype = int(m.group(1))
        if mtype not in METER_COUNTS:
            return None
        return mtype, f"/meters/{mtype}"

    def _h_meters(self, msg: OscMessage, data: bytes, addr: Addr) -> None:
        # meters.md §1.1: ,s /meters/N | ,si /meters/N tf | ,sii /meters/6 ch tf | ,siii /meters/5 a b tf
        if not msg.args:
            return
        named = self._meter_name(msg.args[0])
        if named is None:
            log.debug("fakedesk: bad /meters request %s", msg)
            return
        mtype, reply_address = named
        ints = [int(a) for a in msg.args[1:] if isinstance(a, (int, float)) and not isinstance(a, bool)]
        tf = ints[-1] if ints else 1
        if not 1 <= tf <= 99:
            tf = 1  # meters.md §1.2: anything outside 1..99 counts as 1
        self._subscribe_meter(addr, mtype, reply_address, tuple(ints[:-1]), tf)

    def _h_batchsubscribe(self, msg: OscMessage, data: bytes, addr: Addr) -> None:
        # transport.md §7.3: ,ssiii alias /meters/N a0 a1 tf -> replies addressed by the alias
        if len(msg.args) < 2 or not isinstance(msg.args[0], str):
            return
        named = self._meter_name(msg.args[1])
        if named is None:
            log.debug("fakedesk: /batchsubscribe for %r not supported", msg.args[1])
            return
        mtype, _ = named
        alias = msg.args[0] if msg.args[0].startswith("/") else "/" + msg.args[0]
        ints = [int(a) for a in msg.args[2:] if isinstance(a, (int, float)) and not isinstance(a, bool)]
        tf = ints[-1] if ints else 1
        if not 1 <= tf <= 99:
            tf = 1
        self._subscribe_meter(addr, mtype, alias, tuple(ints[:-1]), tf)

    def _subscribe_meter(self, client: Addr, mtype: int, reply_address: str, args: tuple[int, ...], tf: int) -> None:
        loop = asyncio.get_running_loop()
        key = (client, mtype)
        sub = self.meters.get(key)
        expiry = loop.time() + self.meter_lease_s
        if sub is not None and sub.task is not None and not sub.task.done():
            sub.expiry, sub.interval_s, sub.args, sub.reply_address = expiry, FRAME_PERIOD_S * tf, args, reply_address
            return  # a repeat request re-arms the lease, no duplicate stream (meters.md §1.3)
        sub = MeterSub(client, mtype, reply_address, args, FRAME_PERIOD_S * tf, expiry)
        sub.task = loop.create_task(self._meter_stream(key, sub), name=f"fakedesk-meters-{mtype}")
        self.meters[key] = sub

    async def _meter_stream(self, key: tuple[Addr, int], sub: MeterSub) -> None:
        loop = asyncio.get_running_loop()
        try:
            while loop.time() < sub.expiry:
                try:
                    self._send(self._meter_datagram(sub), sub.client)
                    sub.frames_sent += 1
                except Exception:
                    log.exception("fakedesk: meter frame failed")
                await asyncio.sleep(sub.interval_s)
        finally:
            if self.meters.get(key) is sub:
                del self.meters[key]

    def _meter_datagram(self, sub: MeterSub) -> bytes:
        return encode_meter_datagram(sub.meter_type, self._meter_values(sub.meter_type, sub.args), address=sub.reply_address)

    def _strip_level(self, prefix: str, t: float, salt: int, *, pre_fade: bool = False) -> float:
        """Synthetic linear level for one strip. ``pre_fade`` ignores the fader and the mute
        (meters.md §3: ``/meters/6`` word 0 is the post-trim, *pre*-fade level)."""
        wobble = 0.85 + 0.15 * math.sin(2.0 * math.pi * 0.7 * t + salt)
        if prefix.startswith("/ch/") and int(prefix[4:6]) in self.unplugged:
            return SILENCE_LIN  # nothing plugged in: the meter shows the input floor, fader or no fader
        if pre_fade:
            return max(SILENCE_LIN, min(8.0, 0.25 * wobble))
        on = self.state.get(f"{prefix}/mix/on", 1)
        fader = self.state.get(f"{prefix}/mix/fader", 0.0)
        if not on or not isinstance(fader, float) or fader <= 0.0:
            return SILENCE_LIN
        db = fader_to_db(fader)
        return max(SILENCE_LIN, min(8.0, 0.25 * 10.0 ** (db / 20.0) * wobble))

    def _meter_values(self, mtype: int, args: tuple[int, ...]) -> list[float]:
        if mtype == RTA_METER_TYPE:
            if self.rta_dormant:
                return [RTA_DORMANT_DB] * len(self.rta.band_hz)
            frame = self.rta.last_frame or self.rta.tick()
            return list(frame.values)
        count = METER_COUNTS[mtype]
        if mtype == 16:
            return [1.0] * count * 2  # 88 gains of 1.0 (no reduction) + 8 automix gains of 1.0
        t = asyncio.get_running_loop().time()
        # meters.md §3: the gain-reduction words are the linear gain *applied*, 1.0 = no
        # reduction — not levels. (§2.2's verbatim desk capture of /meters/6 decodes to
        # 9.6e-6, 0.99999982, 1.0, 3.98e-7: level, gate gain, dyn gain, level.) So each id
        # gets its own fill; the strip walk must stop where its GR range begins.
        values = [SILENCE_LIN] * count
        if mtype in (0, 13):  # all levels: [0-69] resp. [0-47] of the 70-strip order
            for i, prefix in enumerate(_METER_STRIPS_70[:count]):
                values[i] = self._strip_level(prefix, t, i)
        elif mtype == 1:  # [0-31] ch levels, [32-63] gate GR, [64-95] dyn GR
            for i, prefix in enumerate(_METER_STRIPS_70[:32]):
                values[i] = self._strip_level(prefix, t, i)
            values[32:] = [1.0] * (count - 32)
        elif mtype == 2:  # [0-24] bus/mtx/main levels, [25-48] their dyn GR
            for i, prefix in enumerate(_METER_STRIPS_MAIN):
                values[i] = self._strip_level(prefix, t, i)
            values[len(_METER_STRIPS_MAIN):] = [1.0] * (count - len(_METER_STRIPS_MAIN))
        elif mtype == 6:  # [0] pre-fade level, [1] gate GR, [2] dyn GR, [3] post-fade level
            ch = args[0] if args else 0
            prefix = _METER_STRIPS_70[ch] if 0 <= ch < len(_METER_STRIPS_70) else "/main/st"
            values = [self._strip_level(prefix, t, ch, pre_fade=True), 1.0, 1.0, self._strip_level(prefix, t, ch)]
        return values

    def _h_renew(self, msg: OscMessage, data: bytes, addr: Addr) -> None:
        # meters.md §1.3: bare /renew renews everything of this client; ,s name renews one;
        # a lapsed lease cannot be renewed (it is already gone)
        now = asyncio.get_running_loop().time()
        for sub in self._subs_named(addr, msg.args):
            sub.expiry = now + self.meter_lease_s

    def _h_unsubscribe(self, msg: OscMessage, data: bytes, addr: Addr) -> None:
        for sub in self._subs_named(addr, msg.args):
            if sub.task is not None:
                sub.task.cancel()
            self.meters.pop((sub.client, sub.meter_type), None)

    def _subs_named(self, client: Addr, args: tuple[Any, ...]) -> list[MeterSub]:
        """This client's meter leases: all of them for a bare command, else the one whose
        ``/meters/N`` name or alias matches the string argument (none when it matches nothing)."""
        mine = [s for (c, _), s in self.meters.items() if c == client]
        if not args:
            return mine
        name = args[0] if isinstance(args[0], str) else ""
        named = self._meter_name(name)
        alias = name if name.startswith("/") else "/" + name
        return [s for s in mine if (named is not None and named[0] == s.meter_type) or s.reply_address == alias]

    # -- handlers: node / slash / params ------------------------------------------------------

    def _h_node(self, msg: OscMessage, data: bytes, addr: Addr) -> None:
        if not msg.args or not isinstance(msg.args[0], str):
            return
        try:
            line = self._node_text(msg.args[0])
        except (NodeParseError, ScaleError) as e:
            log.warning("fakedesk: cannot render node %r: %s", msg.args[0], e)
            return
        if line is None:
            log.debug("fakedesk: unknown node %r (no reply)", msg.args[0])
            return
        self._reply(encode("node", line + "\n"), addr)  # transport.md §6.2: address "node", trailing \n

    def _h_slash(self, msg: OscMessage, data: bytes, addr: Addr) -> None:
        if not msg.args or not isinstance(msg.args[0], str) or not msg.args[0].strip():
            return
        items: list[tuple[str, Any]] = []
        try:
            items = self._parse_node_write(msg.args[0])
        except (NodeParseError, DescriptorError, ScaleError, ValueError) as e:
            log.debug("fakedesk: / write %r ignored: %s", msg.args[0], e)
        self._reply(data, addr)  # transport.md §6.6: the desk echoes the / datagram back verbatim
        self._apply(items, exclude=addr)  # the echo proves receipt, not application (HANDOVER §4b)

    def _h_param(self, msg: OscMessage, data: bytes, addr: Addr) -> None:
        a = msg.address
        if not msg.args:  # GET (transport.md §5.1); unknown address -> silence (§5.3)
            if a in self.state:
                self._reply(encode(a, self.state[a]), addr)
                return
            try:
                node = self.d.node(a)
            except DescriptorError:
                log.debug("fakedesk: GET of unknown %s ignored", a)
                return
            # a GET on a node container answers every value typed (GetSceneName.c, fx_routing_scenes.md §6.2)
            self._reply(encode(a, *[self.state[x] for x in node.addresses if x in self.state]), addr)
            return
        spec = self._specs.get(a)
        if spec is not None:  # SET: store, no ack (§5.2); push to the other xremote clients
            try:
                raw = self._coerce(a, spec, msg.args[0])
            except (ScaleError, ValueError) as e:
                log.debug("fakedesk: SET %s %r ignored: %s", a, msg.args, e)
                return
            self._apply([(a, raw)], exclude=addr)
            return
        try:
            node = self.d.node(a)
        except DescriptorError:
            log.debug("fakedesk: SET of unknown %s ignored", a)
            return
        if any(isinstance(x, str) for x in msg.args):
            log.debug("fakedesk: string SET on node %s ignored (DOC fn.7)", a)
            return
        # multi-argument SET on a node address = one SET per leaf (transport.md §1.4)
        items: list[tuple[str, Any]] = []
        for leaf, arg in zip(node.addresses, msg.args):
            leaf_spec = self._specs.get(leaf)
            if leaf_spec is None:
                continue
            try:
                items.append((leaf, self._coerce(leaf, leaf_spec, arg)))
            except (ScaleError, ValueError) as e:
                log.debug("fakedesk: SET %s %r ignored: %s", leaf, arg, e)
        self._apply(items, exclude=addr, verbatim=data)  # the emulator forwards the received datagram verbatim (§4.2)

    def _h_setrtasrc(self, msg: OscMessage, data: bytes, addr: Addr) -> None:
        # meters.md §5.2 / fx_routing_scenes.md §8: /-action/setrtasrc ,i N with the /-stat numbering
        # (0..71 strips, 72 Monitor) = /-prefs/rta/source N+2 (Monitor -> 1). Not a descriptor param.
        if not msg.args or isinstance(msg.args[0], str):
            return
        n = int(msg.args[0])
        src = 1 if n == 72 else n + 2 if 0 <= n <= 71 else None
        if src is None:
            return
        self._apply([("/-prefs/rta/source", src)], exclude=addr)  # _side_effects mirrors /-stat/rtasource

    # -- side effects -----------------------------------------------------------------------------

    def _side_effects(self, address: str, exclude: Addr | None) -> None:
        if address in ("/-prefs/rta/source", "/-prefs/rta/pos"):
            self._mirror_rta_stat(exclude)
        elif address == "/-action/goscene":
            self._recall_scene(int(self.state.get(address, 0)), exclude)
        else:
            m = _SCENE_FIELD_RE.match(address)
            if m:
                sc = self.scenes.get(int(m.group(1)))
                if sc is not None:
                    sc[m.group(2)] = self.state[address]

    def _mirror_rta_stat(self, exclude: Addr | None) -> None:
        """``/-stat/rtasource`` = (source − 2) + 98·pos; Monitor → 72; none → selected strip."""
        src = int(self.state.get("/-prefs/rta/source", 0))
        pos = int(self.state.get("/-prefs/rta/pos", 0))
        base = src - 2 if src >= 2 else 72 if src == 1 else int(self.state.get("/-stat/selidx", 0))
        stat = base + (98 if pos else 0)
        if "/-stat/rtasource" in self._specs and self._store("/-stat/rtasource", stat) and self._transport is not None:
            self._push_leaf("/-stat/rtasource", exclude)

    def _refresh_cuts(self) -> None:
        """Recompute the SyntheticRta cuts from the GEQ inserted on the RTA-source strip."""
        self._cuts_dirty = False
        rta = self.rta
        rta.clear_cuts()
        try:
            target = target_for_rta_source(self.d, int(self.state.get("/-prefs/rta/source", 0)))
        except Exception:
            target = None
        if target is None:
            return
        prefix = target.osc_prefix
        if not self.state.get(f"{prefix}/insert/on"):
            return
        sel = self.state.get(f"{prefix}/insert/sel")
        if not isinstance(sel, int) or not 1 <= sel <= 16:
            return  # OFF or an AUX loop (fx_routing_scenes.md §3.2)
        slot, side_b = (sel + 1) // 2, sel % 2 == 0
        token = self._fx_slot_token(slot)
        if token not in self._geq_types:
            return
        geq = self.d.geq

        def side_gains(b_side: bool) -> tuple[list[float], float]:
            first = int(geq.get("par_b_first", 33)) if (b_side and token in self._geq_dual) else int(geq.get("par_a_first", 1))
            master_par = int(geq.get("par_b_master", 64)) if (b_side and token in self._geq_dual) else int(geq.get("par_a_master", 32))
            g = [float(self.state.get(f"/fx/{slot}/par/{first + i:02d}", 0.5)) * 30.0 - 15.0 for i in range(31)]
            m = float(self.state.get(f"/fx/{slot}/par/{master_par:02d}", 0.5)) * 30.0 - 15.0
            return g, m

        legs = [side_gains(side_b)]
        if prefix == "/main/st" and token in self._geq_dual:
            # A stereo strip runs L through side A and R through side B of a dual GEQ (fx_routing_scenes.md
            # §2.1/§3.4); the analyser hears both legs. A cut on one side only lowers the summed power by
            # about half of its depth (M7: a -15 dB cut on one leg read -4..-6 dB on the RTA).
            legs = [side_gains(False), side_gains(True)]
        log_geq = [math.log(h) for h in self._geq_hz]
        for i, hz in enumerate(rta.band_hz):
            lh = math.log(hz)
            k = min(range(len(log_geq)), key=lambda j: abs(log_geq[j] - lh))  # nearest 1/3-octave band
            lin = sum(10.0 ** ((g[k] + m) / 10.0) for g, m in legs) / len(legs)
            cut = -10.0 * math.log10(lin) if lin > 0 else 0.0
            rta.attenuate(i, 0.0 if abs(cut) < 1e-9 else cut)
        log.debug("fakedesk: RTA cuts refreshed from %s via FX%d%s (%s)", prefix, slot, "R" if side_b else "L", token)

    # -- scenes ----------------------------------------------------------------------------------------

    def _scene_addr(self, idx: int, leaf: str) -> str:
        return f"/-show/showfile/scene/{idx:03d}/{leaf}"

    def _recall_scene(self, idx: int, exclude: Addr | None) -> None:
        if not 0 <= idx <= 99:
            return
        if self._store("/-show/prepos/current", idx):
            self._push_leaf("/-show/prepos/current", exclude)
        sc = self.scenes.get(idx)
        state = sc.get("state") if sc else None
        if not state:
            log.info("fakedesk: goscene %d (%s): no stored data", idx, sc["name"] if sc else "?")
            return
        changed = 0
        for a, raw in state.items():
            if a in self.state and self._store(a, raw):
                self._push_leaf(a, exclude)
                changed += 1
        log.info("fakedesk: scene %d %r recalled, %d parameter(s) changed", idx, sc["name"], changed)

    def _h_save(self, msg: OscMessage, data: bytes, addr: Addr) -> None:
        # fx_routing_scenes.md §6.4: /save ,siss scene idx name notes (,sissi also seen) -> /save ,si scene 1
        args = list(msg.args)
        if len(args) < 2 or not isinstance(args[0], str):
            return
        kind = args[0]
        if kind != "scene":
            self._reply(encode("/save", kind, 0), addr)
            return
        try:
            idx = int(args[1])
        except (TypeError, ValueError):
            return
        if not 0 <= idx <= 99:
            self._reply(encode("/save", "scene", 0), addr)
            return
        name = str(args[2]) if len(args) > 2 else ""
        notes = str(args[3]) if len(args) > 3 else ""
        self.scenes[idx] = {"name": name, "notes": notes, "state": self._scene_state_copy()}
        stored = [(self._scene_addr(idx, "name"), self._store(self._scene_addr(idx, "name"), name)),
                  (self._scene_addr(idx, "notes"), self._store(self._scene_addr(idx, "notes"), notes)),
                  (self._scene_addr(idx, "hasdata"), self._store(self._scene_addr(idx, "hasdata"), 1))]
        self._reply(encode("/save", "scene", 1), addr)
        self._commit(stored, exclude=addr)
        self._save_scene_file(idx)
        log.info("fakedesk: scene %d saved as %r", idx, name)

    def _h_load(self, msg: OscMessage, data: bytes, addr: Addr) -> None:
        # /load ,si scene idx -> /load ,si scene 1 (ok) | 0 (fail); moves prepos/current (§6.2)
        args = list(msg.args)
        if len(args) < 2 or not isinstance(args[0], str):
            return
        kind = args[0]
        try:
            idx = int(args[1])
        except (TypeError, ValueError):
            return
        sc = self.scenes.get(idx) if kind == "scene" else None
        ok = bool(sc and sc.get("state")) and 0 <= idx <= 99
        self._reply(encode("/load", kind, 1 if ok else 0), addr)
        if ok:
            self._recall_scene(idx, addr)

    def _h_delete(self, msg: OscMessage, data: bytes, addr: Addr) -> None:
        args = list(msg.args)
        if len(args) < 2 or not isinstance(args[0], str) or args[0] != "scene":
            return
        try:
            idx = int(args[1])
        except (TypeError, ValueError):
            return
        if not 0 <= idx <= 99:
            self._reply(encode("/delete", "scene", 0), addr)
            return
        self.scenes.pop(idx, None)
        stored = [(self._scene_addr(idx, "name"), self._store(self._scene_addr(idx, "name"), "")),
                  (self._scene_addr(idx, "notes"), self._store(self._scene_addr(idx, "notes"), "")),
                  (self._scene_addr(idx, "hasdata"), self._store(self._scene_addr(idx, "hasdata"), 0))]
        self._reply(encode("/delete", "scene", 1), addr)
        self._commit(stored, exclude=addr)
        self._save_scene_file(idx, delete=True)

    def _h_rename(self, msg: OscMessage, data: bytes, addr: Addr) -> None:
        args = list(msg.args)
        if len(args) < 3 or not isinstance(args[0], str) or args[0] != "scene":
            return
        try:
            idx = int(args[1])
        except (TypeError, ValueError):
            return
        if not 0 <= idx <= 99:
            self._reply(encode("/rename", "scene", 0), addr)
            return
        name = str(args[2])
        if idx in self.scenes:
            self.scenes[idx]["name"] = name
        stored = [(self._scene_addr(idx, "name"), self._store(self._scene_addr(idx, "name"), name))]
        self._reply(encode("/rename", "scene", 1), addr)
        self._commit(stored, exclude=addr)
        self._save_scene_file(idx)

    def _scene_file(self, idx: int) -> Path | None:
        return None if self.scene_dir is None else self.scene_dir / f"scene_{idx:02d}.json"

    def _save_scene_file(self, idx: int, *, delete: bool = False) -> None:
        path = self._scene_file(idx)
        if path is None:
            return
        try:
            if delete:
                path.unlink(missing_ok=True)
                return
            sc = self.scenes.get(idx)
            if sc is None:
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"index": idx, "name": sc["name"], "notes": sc["notes"], "state": sc["state"]}), encoding="utf-8")
        except OSError as e:
            log.warning("fakedesk: cannot write %s: %s", path, e)

    def _load_scene_dir(self) -> None:
        assert self.scene_dir is not None
        if not self.scene_dir.is_dir():
            return
        for path in sorted(self.scene_dir.glob("scene_*.json")):
            try:
                env = json.loads(path.read_text(encoding="utf-8"))
                idx = int(env["index"])
                state = {a: v for a, v in dict(env.get("state") or {}).items() if a in self._specs}
                state = {a: self._to_stored(a, self._specs[a], v) for a, v in state.items()}
                self.scenes[idx] = {"name": str(env.get("name", "")), "notes": str(env.get("notes", "")), "state": state}
                self._store(self._scene_addr(idx, "name"), self.scenes[idx]["name"])
                self._store(self._scene_addr(idx, "notes"), self.scenes[idx]["notes"])
                self._store(self._scene_addr(idx, "hasdata"), 1 if state else 0)
            except (OSError, ValueError, KeyError, TypeError) as e:
                log.warning("fakedesk: skipping scene file %s: %s", path, e)

    # -- /-fake/* control ---------------------------------------------------------------------------

    def _h_fake(self, msg: OscMessage, data: bytes, addr: Addr) -> None:
        cmd = msg.address[len("/-fake/"):]
        args = msg.args
        tags = msg.typetags[1:]
        try:
            if cmd == "ring":
                hz = float(args[0])
                on, growth, start = True, 15.0, -45.0
                if len(args) > 1:
                    if tags[1:2] == "i":
                        on = bool(args[1])  # DESIGN §18 form: ,fi hz on
                    else:
                        growth = float(args[1])
                if len(args) > 2:
                    start = float(args[2])
                if on:
                    band = self.rta.inject_ring(hz, growth, start)
                    log.info("fakedesk: ring at %.0f Hz (band %d) +%.1f dB/s from %.1f dB", hz, band, growth, start)
                else:
                    self.rta.stop_ring(hz)
            elif cmd == "ring/stop":
                self.rta.stop_ring(float(args[0]))
            elif cmd == "note":
                self.rta.inject_note(float(args[0]), float(args[1]) if len(args) > 1 else -20.0,
                                     rise_frames=int(args[2]) if len(args) > 2 else None)
            elif cmd == "note/stop":
                self.rta.stop_note(float(args[0]))
            elif cmd == "silence":
                self.silent = bool(int(args[0])) if args else True
                if self.silent:
                    log.warning("fakedesk: going silent")
                    return  # no ack: the desk is "hung"
                log.info("fakedesk: answering again")
            elif cmd in ("drop", "latency"):
                self._send(data, addr)  # ack first, then apply (the ack must not be the first casualty)
                if cmd == "drop":
                    self.drop_next(int(args[0]) if args else 1)
                else:
                    self.latency_ms = max(0.0, float(args[0])) if args else 0.0
                return
            else:
                log.warning("fakedesk: unknown control %s", msg.address)
                return
        except (IndexError, TypeError, ValueError) as e:
            log.warning("fakedesk: bad control %s: %s", msg, e)
            return
        self._send(data, addr)  # ack by echo


# -- CLI ------------------------------------------------------------------------------------------------


async def _serve(args: argparse.Namespace) -> None:
    d = Descriptor.load(args.device_yaml)
    desk = FakeDesk(
        d, host=args.host, port=args.port, name=args.name, scene_dir=args.scene_dir,
        rta=SyntheticRta(seed=args.seed, band_hz=rta_band_hz(d)),
    )
    host, port = await desk.start()
    log.info("X32 emulator %r ready on %s:%d (Ctrl+C to stop)", args.name, host, port)
    if args.ring is not None:
        desk.rta.inject_ring(args.ring, args.ring_growth)
        log.info("ring injected at %.0f Hz, %.1f dB/s", args.ring, args.ring_growth)
    try:
        await asyncio.Event().wait()
    finally:
        await desk.stop()


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m x32mcp.fakedesk [--port 10023] [--host 127.0.0.1] [--name X32-FAKE] [--scene-dir DIR]``."""
    p = argparse.ArgumentParser(prog="x32-fakedesk", description="Behringer X32 OSC emulator for x32-mcp tests and demos.")
    p.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1; 0.0.0.0 to serve the LAN)")
    p.add_argument("--port", type=int, default=10023, help="UDP port (default 10023, the real desk's port)")
    p.add_argument("--name", default="X32-FAKE", help="console name reported by /xinfo")
    p.add_argument("--scene-dir", type=Path, default=None, help="directory to persist /save'd scenes")
    p.add_argument("--device-yaml", type=Path, default=None, help="descriptor to load (default: device.yaml of the repo)")
    p.add_argument("--seed", type=int, default=1, help="SyntheticRta seed")
    p.add_argument("--ring", type=float, default=None, metavar="HZ", help="start a feedback ring at HZ on startup")
    p.add_argument("--ring-growth", type=float, default=15.0, metavar="DB_PER_S", help="ring growth rate (default 15)")
    p.add_argument("--log-level", default="INFO", help="logging level (stderr)")
    args = p.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(_serve(args))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
