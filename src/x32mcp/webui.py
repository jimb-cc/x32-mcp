"""Read-only dashboard server (DESIGN.md §17): ``webui/index.html`` over HTTP + JSON push over ``/ws``.

One ``websockets`` server does both jobs so the page and its WebSocket share an origin (the page
connects to ``ws://<location.host>/ws``). ``process_request`` answers plain HTTP (``GET /`` → the
page, re-read from ``settings.webui_dir`` on every request so edits show without a restart;
``GET /healthz`` → JSON) and lets ``GET /ws`` upgrade. The browser never sends anything useful;
inbound WS messages are logged at DEBUG and ignored. Strictly read-only (BRIEF §5a).

Messages (schema fixed by DESIGN.md §17; ``ts`` fields are epoch seconds, ``time.time()``):

* ``hello`` on connect (``band_hz`` 100 RTA centres, ``geq_band_hz`` 31, ``version``, ``fps``),
  immediately followed by the current ``state`` and ``notches`` so the panel is never blank;
* ``rta`` — the *latest* frame only, at most ``fps`` per second (default 20);
* ``state`` — whenever it changes (checked on every EventBus event) and every ``state_period_s``
  regardless (default 1 s);
* ``notches`` — the full list, on every ``cfs.notch`` event and whenever it differs from the
  last one sent;
* ``event`` — every EventBus event except the transient ``meters.frame``.

Decisions DESIGN.md leaves open (documented here, nowhere else):

* **Frame source lifecycle is the caller's.** The server subscribes to ``frames`` while running
  but never starts or stops it — the CFS manager (or ``server.py``) owns that. ``set_frame_source``
  swaps sources at runtime (synthetic → live).
* **Slow clients drop frames.** Each client has a one-slot "latest frame" plus a bounded deque of
  control messages (state/notches/events, oldest dropped when full) and its own send task; a
  client that does not read stalls only its own task while the slot keeps being overwritten.
  Every broadcast is serialised once, not per client.
* **State is derived by polling** a duck-typed provider (``cfs``): anything with a ``state``
  attribute that is a dict, has ``to_dict()``, or is a dataclass (``CfsState``); ``None`` → mode
  ``idle``. ``mode`` is sent as a lowercase token (``CfsMode.RINGOUT`` → ``"ringout"``),
  ``rta_source`` as a target key (``"bus.3"``), ``connection`` from the ``connection_status``
  callable (``ConnectionStatus.to_dict()`` shape → ``{state, console: <name>, rtt_ms}``; without
  one: ``{"state": "disconnected", ...}``).
* **JSON hygiene**: ``-inf`` → ``"-oo"`` (the page renders it as −oo, same spelling as
  ``nodes.py``), ``+inf`` → ``"+oo"``, NaN → ``null``; enums → value, dataclasses → dicts,
  ``Target`` → key. ``rta.db`` values are rounded to 0.01 dB (RTA resolution is 1/256 dB,
  meters.md §4.2; the page draws at −90..0 dB).
* ``start()`` on a port that cannot be bound logs a WARNING and raises :class:`WebUIError`, which
  the caller may ignore (the MCP server must start without the dashboard). ``start``/``stop`` are
  idempotent; ``stop`` is bounded (3 s) and never hangs.
* ``python -m x32mcp.webui`` serves a self-contained demo (synthetic RTA with a scripted ring and
  notch) for filming/tablet checks (BRIEF §7 M8).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
import time
from collections import deque
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable, Sequence

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from x32mcp import __version__
from x32mcp.config import Settings
from x32mcp.events import TRANSIENT_TYPES, Event, EventBus
from x32mcp.meters import FrameSource, MeterFrame
from x32mcp.targets import Target

__all__ = ["DashboardServer", "WebUIError", "dumps", "main"]

log = logging.getLogger(__name__)

DEFAULT_FPS = 20  # the console's own RTA rate (50 ms, meters.md §1.2); the page buffers <= fps rows/s
DEFAULT_STATE_PERIOD_S = 1.0
STOP_TIMEOUT_S = 3.0
CONTROL_QUEUE_MAX = 256  # per-client backlog of state/notches/event messages (the page keeps 200 events)
NOTCH_KEYS = ("bus", "band", "freq_hz", "depth_db", "session_id", "ts")


class WebUIError(Exception):
    """The dashboard could not start (typically: port already in use)."""


# -- JSON -------------------------------------------------------------------------------------


def _jsonable(o: Any) -> Any:
    """Recursively convert ``o`` to plain JSON types (rules in the module docstring)."""
    if o is None or isinstance(o, (bool, int, str)):
        return o
    if isinstance(o, float):
        if math.isnan(o):
            return None
        if math.isinf(o):
            return "-oo" if o < 0 else "+oo"
        return o
    if isinstance(o, Enum):
        return _jsonable(o.value)
    if isinstance(o, Target):
        return o.key
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in o]
    if is_dataclass(o) and not isinstance(o, type):
        return _jsonable(asdict(o))
    to_dict = getattr(o, "to_dict", None)
    if callable(to_dict):
        return _jsonable(to_dict())
    if isinstance(o, Path):
        return str(o)
    return str(o)


def dumps(obj: Any) -> str:
    """Compact JSON text of ``obj`` after :func:`_jsonable`; never emits NaN/Infinity."""
    return json.dumps(_jsonable(obj), separators=(",", ":"), allow_nan=False)


def _mode_token(v: Any) -> str:
    """``CfsMode.RINGOUT`` / ``"RINGOUT"`` / ``"ringout"`` → ``"ringout"``; ``None`` → ``"idle"``."""
    if v is None:
        return "idle"
    v = getattr(v, "value", v)
    s = str(v)
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return s.lower() or "idle"


# -- clients ----------------------------------------------------------------------------------


@dataclass(eq=False)  # identity-hashed: lives in a set
class _Client:
    ws: ServerConnection
    frame: str | None = None  # latest rta JSON not yet sent; overwriting it IS the frame drop
    control: deque[str] = field(default_factory=lambda: deque(maxlen=CONTROL_QUEUE_MAX))
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    sent: int = 0
    frames_dropped: int = 0
    control_dropped: int = 0


class DashboardServer:
    """Serve the dashboard page and push RTA frames, CFS² state and events to browsers.

    Args:
        settings: ``webui_dir`` (page location) and ``dash_host``/``dash_port`` defaults.
        events: bus mirrored to every client (all types except ``meters.frame``).
        cfs: state provider — the ``CfsManager`` (anything with a ``.state``), or ``None``.
        frames: RTA :class:`FrameSource` to mirror (started/stopped by its owner), or ``None``.
        band_hz: 100 RTA band centres (Hz) sent in ``hello`` (``meters.rta_band_hz``).
        geq_band_hz: 31 GEQ band centres (Hz) from ``descriptor.geq["band_hz"]``.
        connection_status: callable returning ``ConnectionStatus`` (or its ``to_dict()``), or ``None``.
        fps: frame push ceiling in frames/s (1..100).
        state_period_s: unconditional ``state`` push period in seconds (>= 0.05).
        host/port: override ``settings.dash_host``/``dash_port`` (``port=0`` → ephemeral; read ``.port``).
    """

    ws_path = "/ws"

    def __init__(
        self,
        settings: Settings,
        events: EventBus,
        cfs: Any | None = None,
        frames: FrameSource | None = None,
        *,
        band_hz: Sequence[float],
        geq_band_hz: Sequence[float],
        connection_status: Callable[[], Any] | None = None,
        fps: int = DEFAULT_FPS,
        state_period_s: float = DEFAULT_STATE_PERIOD_S,
        host: str | None = None,
        port: int | None = None,
        version: str = __version__,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._events = events
        self._provider = cfs
        self._frames: FrameSource | None = frames
        self._connection_status = connection_status
        self.band_hz: tuple[float, ...] = tuple(float(h) for h in band_hz)
        self.geq_band_hz: tuple[float, ...] = tuple(float(h) for h in geq_band_hz)
        self.fps = max(1, min(100, int(fps)))
        self.state_period_s = max(0.05, float(state_period_s))
        self.host: str = settings.dash_host if host is None else host
        self.port: int = settings.dash_port if port is None else int(port)
        self.version = version
        self._clock = clock

        self._server: Server | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: set[_Client] = set()
        self._unsub_events: Callable[[], None] | None = None
        self._unsub_frames: Callable[[], None] | None = None
        self._ticker: asyncio.Task | None = None
        self._started_at: float | None = None
        # frame decimation: latest frame + at most one pending flush (see _on_frame)
        self._latest: MeterFrame | None = None
        self._last_sent_frame: MeterFrame | None = None
        self._last_flush = float("-inf")
        self._flush_handle: asyncio.TimerHandle | None = None
        self._last_state_text: str | None = None
        self._last_notches_text: str | None = None
        self.frames_seen = 0
        self.frames_pushed = 0  # decimated frames broadcast (counted once, not per client)

    # -- lifecycle -----------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in ("", "0.0.0.0", "::") else self.host
        return f"http://{host}:{self.port}/"

    @property
    def clients(self) -> int:
        return len(self._clients)

    async def start(self) -> None:
        """Bind and serve. Idempotent. Raises :class:`WebUIError` (after a WARNING) if the port
        cannot be bound; the caller may ignore that and run without a dashboard."""
        if self._server is not None:
            return
        self._loop = asyncio.get_running_loop()
        # websockets logs every HTTP response and connection open/close at INFO; this module
        # already logs client joins/leaves, so keep its chatter for DEBUG runs only.
        ws_log = logging.getLogger("x32mcp.webui.ws")
        ws_log.setLevel(logging.NOTSET if log.isEnabledFor(logging.DEBUG) else logging.WARNING)
        try:
            server = await serve(
                self._handler,
                self.host,
                self.port,
                process_request=self._process_request,
                close_timeout=2.0,
                logger=ws_log,
            )
        except OSError as exc:
            msg = f"dashboard: cannot bind {self.host}:{self.port}: {exc}"
            log.warning(msg)
            raise WebUIError(msg) from exc
        self._server = server
        for sock in server.sockets:  # port 0 → OS-assigned; publish the real one
            self.port = sock.getsockname()[1]
            break
        self._started_at = self._clock()
        self._unsub_events = self._events.subscribe(self._on_event)
        self._attach_frames(self._frames)
        self._ticker = asyncio.create_task(self._tick_loop(), name="webui-ticker")
        log.info("dashboard serving %s (ws %s, %d fps cap)", self.url, self.ws_path, self.fps)

    async def stop(self) -> None:
        """Close every client and the listener. Idempotent; bounded by ``STOP_TIMEOUT_S``."""
        server, self._server = self._server, None
        if server is None:
            return
        if self._unsub_events is not None:
            self._unsub_events()
            self._unsub_events = None
        self._detach_frames()
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None
        ticker, self._ticker = self._ticker, None
        if ticker is not None:
            ticker.cancel()
            try:
                await asyncio.wait_for(ticker, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        server.close(close_connections=True)
        try:
            await asyncio.wait_for(server.wait_closed(), timeout=STOP_TIMEOUT_S)
        except asyncio.TimeoutError:
            log.warning("dashboard: %d client(s) did not close within %.0f s", len(self._clients), STOP_TIMEOUT_S)
        self._clients.clear()
        log.info("dashboard stopped")

    def set_frame_source(self, frames: FrameSource | None) -> None:
        """Mirror a different source from now on (e.g. synthetic → ``LiveMeters``). Never starts
        or stops either source."""
        self._detach_frames()
        self._frames = frames
        if self._server is not None:
            self._attach_frames(frames)

    def _attach_frames(self, frames: FrameSource | None) -> None:
        if frames is not None and self._unsub_frames is None:
            self._unsub_frames = frames.subscribe(self._on_frame)

    def _detach_frames(self) -> None:
        if self._unsub_frames is not None:
            self._unsub_frames()
            self._unsub_frames = None

    # -- HTTP ----------------------------------------------------------------------------------
    def _process_request(self, connection: ServerConnection, request: Request) -> Response | None:
        path = request.path.split("?", 1)[0]
        if path == self.ws_path:
            return None  # let websockets perform the upgrade
        if request.method.upper() != "GET":
            return self._http(connection, HTTPStatus.METHOD_NOT_ALLOWED, "GET only\n")
        if path in ("/", "/index.html"):
            page = self._settings.webui_dir / "index.html"
            try:
                body = page.read_text(encoding="utf-8")  # re-read every time: edits show without a restart
            except OSError as exc:
                log.warning("dashboard page unreadable: %s (%s)", page, exc)
                return self._http(connection, HTTPStatus.NOT_FOUND, f"dashboard page missing: {page}\n")
            return self._http(connection, HTTPStatus.OK, body, "text/html; charset=utf-8")
        if path == "/healthz":
            return self._http(connection, HTTPStatus.OK, dumps(self.health()) + "\n", "application/json")
        return self._http(connection, HTTPStatus.NOT_FOUND, "not found\n")

    @staticmethod
    def _http(connection: ServerConnection, status: HTTPStatus, text: str, content_type: str | None = None) -> Response:
        resp = connection.respond(status, text)  # text/plain; Headers is a multi-dict, so replace, don't append
        if content_type is not None:
            del resp.headers["Content-Type"]
            resp.headers["Content-Type"] = content_type
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def health(self) -> dict[str, Any]:
        """Content of ``GET /healthz``."""
        return {
            "ok": True,
            "version": self.version,
            "running": self.running,
            "clients": self.clients,
            "fps": self.fps,
            "frames_seen": self.frames_seen,
            "frames_pushed": self.frames_pushed,
            "source": type(self._frames).__name__ if self._frames is not None else None,
            "mode": _mode_token(self._provider_state().get("mode")),
            "uptime_s": None if self._started_at is None else round(self._clock() - self._started_at, 1),
        }

    # -- WebSocket -----------------------------------------------------------------------------
    async def _handler(self, ws: ServerConnection) -> None:
        client = _Client(ws)
        self._clients.add(client)
        log.info("dashboard client %s connected (%d online)", ws.remote_address, len(self._clients))
        sender = asyncio.create_task(self._sender(client), name="webui-send")
        try:
            async for msg in ws:  # the page never sends; anything that arrives is ignored
                log.debug("dashboard: ignoring inbound message from %s: %.80r", ws.remote_address, msg)
        except ConnectionClosed:
            pass
        finally:
            self._clients.discard(client)
            sender.cancel()
            try:
                await asyncio.wait_for(sender, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
            log.info("dashboard client %s left (%d online, %d msgs, %d frames dropped)",
                     ws.remote_address, len(self._clients), client.sent, client.frames_dropped)

    async def _sender(self, client: _Client) -> None:
        """Per-client send loop: hello/state/notches first, then control messages, then the
        latest frame (if any) each time it is woken."""
        ws = client.ws
        try:
            for text in (dumps(self.hello_message()), dumps(self.state_message()), dumps(self.notches_message())):
                await ws.send(text)
                client.sent += 1
            while True:
                await client.wake.wait()
                client.wake.clear()
                while client.control:
                    await ws.send(client.control.popleft())  # backpressure stalls only this client
                    client.sent += 1
                text, client.frame = client.frame, None
                if text is not None:
                    await ws.send(text)
                    client.sent += 1
        except ConnectionClosed:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("dashboard sender for %s failed", ws.remote_address)

    def _broadcast(self, text: str) -> None:
        for c in list(self._clients):
            if len(c.control) == c.control.maxlen:
                c.control_dropped += 1
            c.control.append(text)
            c.wake.set()

    def _broadcast_frame(self, text: str) -> None:
        for c in list(self._clients):
            if c.frame is not None:
                c.frames_dropped += 1
            c.frame = text
            c.wake.set()

    # -- frames --------------------------------------------------------------------------------
    def _on_frame(self, frame: MeterFrame) -> None:
        """FrameSource callback (sync, loop thread). Keeps only the latest frame and flushes it
        as soon as ``1/fps`` has elapsed since the previous flush."""
        self._latest = frame
        self.frames_seen += 1
        if self._flush_handle is not None or self._loop is None or not self._clients:
            return
        due = self._last_flush + 1.0 / self.fps - self._loop.time()
        if due <= 0:
            self._flush_frame()
        else:
            self._flush_handle = self._loop.call_later(due, self._flush_frame)

    def _flush_frame(self) -> None:
        self._flush_handle = None
        frame = self._latest
        if frame is None or frame is self._last_sent_frame or self._loop is None:
            return
        self._last_sent_frame = frame
        self._last_flush = self._loop.time()
        self.frames_pushed += 1
        self._broadcast_frame(self.frame_text(frame))

    @staticmethod
    def frame_text(frame: MeterFrame) -> str:
        """``{"t":"rta","ts":…,"db":[…]}`` — dB (RTA as-is, linear meters via ``MeterFrame.db``)."""
        return json.dumps(
            {"t": "rta", "ts": frame.ts, "db": [round(v, 2) for v in frame.db()]},
            separators=(",", ":"),
        )

    # -- state / events ------------------------------------------------------------------------
    def _on_event(self, ev: Event) -> None:
        if ev.type in TRANSIENT_TYPES:
            return
        self._broadcast(dumps({"t": "event", "ts": ev.ts, "type": ev.type, "data": ev.data}))
        self._check_state(force_notches=(ev.type == "cfs.notch"))

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(self.state_period_s)
            self._check_state(force_state=True)

    def _check_state(self, *, force_state: bool = False, force_notches: bool = False) -> None:
        if not self._clients:
            return
        try:
            state_text = dumps(self.state_message())
            notches_text = dumps(self.notches_message())
        except Exception:
            log.exception("dashboard: state provider failed")
            return
        if force_state or state_text != self._last_state_text:
            self._last_state_text = state_text
            self._broadcast(state_text)
        if force_notches or notches_text != self._last_notches_text:
            self._last_notches_text = notches_text
            self._broadcast(notches_text)

    def _provider_state(self) -> dict[str, Any]:
        p = self._provider
        if p is None:
            return {}
        st = p if isinstance(p, dict) else getattr(p, "state", None)
        if st is None:
            return {}
        if isinstance(st, dict):
            return st
        to_dict = getattr(st, "to_dict", None)
        if callable(to_dict):
            return dict(to_dict())
        if is_dataclass(st) and not isinstance(st, type):
            return {k: getattr(st, k) for k in st.__dataclass_fields__}  # shallow: Target/Enum reach _jsonable
        return dict(vars(st))

    def _connection(self) -> dict[str, Any]:
        d: Any = None
        if self._connection_status is not None:
            try:
                d = self._connection_status()
            except Exception as exc:
                log.debug("dashboard: connection_status failed: %s", exc)
        if d is not None and not isinstance(d, dict):
            to_dict = getattr(d, "to_dict", None)
            d = to_dict() if callable(to_dict) else dict(vars(d))
        if not d:
            return {"state": "disconnected", "console": None, "rtt_ms": None}
        state = getattr(d.get("state"), "value", d.get("state"))
        console = d.get("console")
        if isinstance(console, dict):
            console = console.get("name") or console.get("model")
        elif console is not None and not isinstance(console, str):
            console = getattr(console, "name", None) or str(console)
        rtt = d.get("rtt_ms")
        return {
            "state": str(state or "disconnected").lower(),
            "console": console,
            "rtt_ms": None if rtt is None else round(float(rtt), 1),
        }

    def hello_message(self) -> dict[str, Any]:
        return {
            "t": "hello",
            "band_hz": list(self.band_hz),
            "geq_band_hz": list(self.geq_band_hz),
            "version": self.version,
            "fps": self.fps,
        }

    def state_message(self) -> dict[str, Any]:
        """The ``state`` message (DESIGN.md §17) from the provider's state + connection status."""
        st = self._provider_state()
        return {
            "t": "state",
            "mode": _mode_token(st.get("mode")),
            "session_id": st.get("session_id"),
            "bus": st.get("bus"),
            "bus_name": st.get("bus_name"),
            "master_db": st.get("master_db"),
            "budget_left": st.get("budget_left"),
            "candidate": st.get("candidate"),
            "stage": st.get("stage"),
            "connection": self._connection(),
            "rta_source": st.get("rta_source"),
        }

    def notches_message(self) -> dict[str, Any]:
        """Full replacement list from the provider's ``notches`` (dicts or ``Notch`` dataclasses)."""
        items: list[dict[str, Any]] = []
        for n in self._provider_state().get("notches") or ():
            d = _jsonable(n)
            if isinstance(d, dict):
                for k in NOTCH_KEYS:
                    d.setdefault(k, None)
                items.append(d)
        return {"t": "notches", "items": items}


# -- standalone demo --------------------------------------------------------------------------


class _DemoProvider:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state


async def _demo(host: str, port: int) -> None:
    """Dashboard + SyntheticRta with a scripted ring/notch cycle and fake CFS² state (no desk)."""
    from x32mcp.descriptor import Descriptor
    from x32mcp.meters import SyntheticRta, rta_band_hz

    settings = Settings.from_env()
    d = Descriptor.load(settings.device_yaml)
    events = EventBus()
    rta = SyntheticRta(band_hz=rta_band_hz(d))
    geq_hz = [float(h) for h in d.geq["band_hz"]]
    state: dict[str, Any] = {"mode": "idle", "session_id": None, "bus": 3, "bus_name": "Wedge A", "master_db": -20.0,
                             "budget_left": 6, "candidate": None, "stage": None, "notches": [], "rta_source": "bus.3"}
    dash = DashboardServer(settings, events, _DemoProvider(state), rta, band_hz=rta.band_hz, geq_band_hz=geq_hz,
                           host=host, port=port,
                           connection_status=lambda: {"state": "connected", "console": {"name": "X32-DEMO"}, "rtt_ms": 2.0})
    await rta.start()
    await dash.start()
    log.info("demo dashboard at %s (Ctrl-C to stop)", dash.url)
    try:
        n = 0
        while True:  # 16 s cycle: RAISE → ring at 2.4 kHz → candidate → −3 dB notch at 2.5 kHz → DONE
            n += 1
            sid = f"demo-{n}"
            state.update(mode="ringout", session_id=sid, stage="RAISE", budget_left=6, notches=[], candidate=None)
            events.publish("cfs.stage", stage="RAISE", bus=3, master_db=state["master_db"])
            await asyncio.sleep(3)
            rta.inject_ring(2400.0, 12.0, start_db=-70.0)
            await asyncio.sleep(4)
            band = rta.band_for_hz(2400.0)
            cand = {"band": band, "freq_hz": rta.band_hz[band], "confidence": 0.81, "level_db": rta.ring_level(2400.0)}
            state.update(candidate=cand, stage="HOLD")
            events.publish("cfs.candidate", bus=3, **cand)
            await asyncio.sleep(1)
            rta.set_geq_gain(2500.0, -3.0)
            notch = {"bus": 3, "band": geq_hz.index(2500.0) + 1, "freq_hz": 2500.0, "depth_db": -3.0,
                     "session_id": sid, "ts": time.time()}
            state.update(notches=[notch], budget_left=5, stage="NOTCH")
            events.publish("cfs.notch", **notch)
            await asyncio.sleep(4)
            state.update(stage="DONE", candidate=None)
            events.publish("cfs.stage", stage="DONE", bus=3)
            await asyncio.sleep(2)
            rta.stop_ring(2400.0)
            rta.clear_cuts()
            state.update(mode="idle", stage=None, session_id=None, notches=[])
            events.publish("cfs.state", mode="idle")
            await asyncio.sleep(2)
    finally:
        await dash.stop()
        await rta.stop()


def main(argv: Sequence[str] | None = None) -> None:
    """``python -m x32mcp.webui [--host H] [--port P]`` — standalone synthetic demo (logs to stderr)."""
    ap = argparse.ArgumentParser(description="x32-mcp dashboard demo (synthetic RTA, no desk)")
    ap.add_argument("--host", default=None, help="bind host (default X32MCP_DASH_HOST or 0.0.0.0)")
    ap.add_argument("--port", type=int, default=None, help="bind port (default X32MCP_DASH_PORT or 8032)")
    ap.add_argument("--log", default="INFO")
    args = ap.parse_args(argv)
    logging.basicConfig(level=args.log.upper(), stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = Settings.from_env()
    try:
        asyncio.run(_demo(args.host or s.dash_host, s.dash_port if args.port is None else args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":  # pragma: no cover
    main()
