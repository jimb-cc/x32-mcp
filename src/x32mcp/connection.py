"""X32 UDP connection: one socket, request/reply matching, heartbeat, watchdog (DESIGN.md §8).

Protocol facts this module relies on (docs/research/transport.md):

* The desk replies to the *source* IP:port of every datagram (§2), so one socket does all
  sending and receiving; pushes and meter blobs arrive on that same port.
* GET = bare padded address; the reply echoes the address with a typetag (§5.1). SET has no
  ack (§5.2). Unknown addresses are ignored — there are no error replies (§5.3) — so a
  missing reply within the timeout is the only failure signal, hence retries.
* ``/info`` → ``,ssss`` server_version, "osc-server", model, firmware (§3.1). ``/xinfo`` →
  desk IP, console name, model, firmware, and it answers broadcast (§3.2).
* ``/xremote`` (12-byte bare form) keeps pushes alive for 10 s and is never acked (§4).
* ``/node ,s path`` is answered on address ``node`` (no slash) with one string
  ``"/path v1 v2 …\\n"`` (§6.2); ``/ ,s "path v1 v2 …"`` is echoed back verbatim (§6.6).
* ``,b`` blobs carry meter data with a little-endian body (§8); they are never parsed here.

Decisions where DESIGN.md is silent (§0.11):

* ``ConsoleInfo.name`` comes from ``/xinfo`` slot 2 (the real console name); ``/info`` slot 2
  is the constant ``osc-server`` on a desk (§9 item 11). ``connect()`` sends ``/xinfo`` once
  after ``/info`` and falls back to the ``/info`` slot if it is not answered.
* ``slash()`` retries like ``request()`` (a node-style write is idempotent); ``timeout`` is
  per attempt.
* ``set()`` accepts an optional ``typetags`` keyword (same meaning as :func:`osc.encode`).
* ``backoff_s`` (reconnect delays, default 1, 2, 4, 8 s, last value repeats) is a constructor
  keyword so tests can shorten it; ``local_addr``/``stats`` are extra read-only properties.
* ``connect()`` resolves ``host`` to a numeric IPv4 address **once** and every datagram goes
  to that address: ``sendto`` would otherwise resolve a name synchronously on the event-loop
  thread for every request, heartbeat and meter renewal. Messages keep the caller's spelling.
* A transport that dies under us (``abort()``, a fatal proactor error) degrades the connection
  instead of wedging it: ``connection_lost`` tells the owner, and the reconnect loop re-binds a
  fresh socket (new ephemeral port) before each probe.
* Any datagram that decodes while DEGRADED counts as recovery (the desk is evidently alive).
* Unmatched ``/info``, ``/xinfo``, ``/status``, ``/`` and ``node`` messages (late replies) are
  dropped, never treated as pushed parameter updates.
* Windows: an ICMP port-unreachable (desk rebooting, nothing on 127.0.0.1:10023 during
  discovery) makes the proactor datagram transport stop reading after ``error_received``;
  :func:`_rearm_reader` re-arms it. It is a guarded no-op on selector loops.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Callable, Sequence

from .events import EventBus
from .osc import OscError, OscMessage, decode, encode

__all__ = [
    "ConnectionError",
    "ConnectionState",
    "ConnectionStatus",
    "ConsoleInfo",
    "NotConnected",
    "RequestTimeout",
    "X32Connection",
]

log = logging.getLogger(__name__)

_INFO_REQ = encode("/info")  # "/info~~~", 8 B (transport.md §3.1)
_XINFO_REQ = encode("/xinfo")  # "/xinfo~~", 8 B (§3.2)
_XREMOTE_REQ = encode("/xremote")  # "/xremote~~~~", 12 B, the form every Maillot tool sends (§4.1)
_NODE_REPLY = "node"  # per transport.md §6.2: the /node reply address has no slash
_NOT_PARAMS = frozenset({"/info", "/xinfo", "/status", "/"})
_RCVBUF = 1 << 20  # transport.md §10: bulk /node bursts drop datagrams with small buffers
_RESOLVE_TIMEOUT_S = 5.0  # getaddrinfo budget in connect(); every network wait has one
_MISSING = object()

UpdateCallback = Callable[[str, tuple], None]
BlobCallback = Callable[[str, bytes], None]


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"  # socket open, desk not answering; reconnect loop running


@dataclass
class ConsoleInfo:
    """Identity of one console. ``server_version`` is ``""`` when only ``/xinfo`` was seen."""

    host: str
    port: int
    name: str  # console name (/-prefs/name), e.g. "X32-02-4A-53"
    model: str  # "X32", "X32RACK", "M32", ...
    firmware: str  # "4.06"
    server_version: str  # osc-server version from /info, e.g. "V2.07"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConnectionStatus:
    state: ConnectionState
    console: ConsoleInfo | None
    last_rx_age_s: float | None  # seconds since the last decodable datagram; None before any
    heartbeat_ok: bool
    pending_requests: int
    rtt_ms: float | None  # last measured request round trip, milliseconds
    reconnect_attempts: int  # probes sent in the current/last DEGRADED episode
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d


class ConnectionError(Exception):  # noqa: A001 - name fixed by DESIGN.md §8 (shadows the builtin here)
    """Base class for connection failures."""


class NotConnected(ConnectionError):
    """No socket, or the desk is not answering and the operation is a write."""


class RequestTimeout(ConnectionError):
    """No reply within timeout × attempts."""


# -- helpers --------------------------------------------------------------------------


def _node_key(path: str) -> str:
    """Pending-request key for a /node reply: ``"node:/ch/01/config"`` (leading slash normalised)."""
    return "node:/" + path.strip().lstrip("/")


def _reply_key(msg: OscMessage) -> str:
    if msg.address == _NODE_REPLY and msg.args and isinstance(msg.args[0], str):
        head = msg.args[0].split(None, 1)
        return _node_key(head[0]) if head else "node:"
    return msg.address


def _value(msg: OscMessage) -> Any:
    """GET semantics: the single argument, a tuple when there are several, None when none."""
    if not msg.args:
        return None
    if len(msg.args) == 1:
        return msg.args[0]
    return msg.args


def _rearm_reader(transport: asyncio.DatagramTransport | None, n_errors: int) -> None:
    """Keep a proactor datagram transport receiving after a socket error.

    CPython's ``_ProactorDatagramTransport._loop_reading`` (Windows default loop) hands an
    OSError raised by the pending recvfrom to ``error_received`` *without* arming a new
    recvfrom, so after one error the transport never delivers another datagram. Windows raises
    exactly that (WSAECONNRESET) when an ICMP port-unreachable answers a datagram we sent —
    i.e. while the desk reboots after a PSU cutout, or when nothing listens on
    127.0.0.1:10023 during discovery. Selector transports keep reading by themselves and have
    no ``_read_fut``, so this is a no-op there; it is also a no-op if the private names it
    guards on ever change. The delay grows with consecutive errors so a broken socket cannot
    spin the loop.
    """
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
    """Adapter from the transport to the owner's handlers; never lets an exception reach the loop."""

    def __init__(
        self,
        on_datagram: Callable[[bytes, Any], None],
        on_error: Callable[[Exception], None],
        on_lost: Callable[[Any, Exception | None], None],
    ) -> None:
        self._on_datagram = on_datagram
        self._on_error = on_error
        self._on_lost = on_lost
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: Any) -> None:
        try:
            self._on_datagram(data, addr)
        except Exception:
            log.exception("datagram handler failed")

    def error_received(self, exc: Exception) -> None:
        try:
            self._on_error(exc)
        except Exception:
            log.exception("socket error handler failed")

    def connection_lost(self, exc: Exception | None) -> None:
        if exc is not None:
            log.debug("datagram transport lost: %r", exc)
        try:
            self._on_lost(self.transport, exc)
        except Exception:
            log.exception("transport-lost handler failed")


# -- connection -------------------------------------------------------------------------


class X32Connection:
    """One UDP socket to one console.

    All timeouts are seconds. ``timeout_s`` is per attempt and ``retries`` the number of
    re-sends (``retries=2`` → 3 attempts, worst case 1.5 s at the default 0.5 s). The heartbeat
    sends ``/xremote`` every ``heartbeat_s``; the watchdog probes ``/info`` after
    ``watchdog_s`` of inbound silence and drops to DEGRADED when the probe fails, then retries
    with ``backoff_s`` delays until the desk answers again.

    Only ``descriptor.policy["read_cache_ttl_s"]`` is read from ``descriptor`` (any object
    with a ``.policy`` mapping will do).
    """

    def __init__(
        self,
        descriptor: Any,
        events: EventBus,
        *,
        timeout_s: float = 0.5,
        retries: int = 2,
        heartbeat_s: float = 8.0,
        watchdog_s: float = 12.0,
        backoff_s: Sequence[float] = (1.0, 2.0, 4.0, 8.0),
    ) -> None:
        self._d = descriptor
        self._events = events
        self._timeout_s = float(timeout_s)
        self._retries = max(0, int(retries))
        self._heartbeat_s = float(heartbeat_s)
        self._watchdog_s = float(watchdog_s)
        self._backoff_s = tuple(float(b) for b in backoff_s) or (1.0,)

        self._state = ConnectionState.DISCONNECTED
        self._console: ConsoleInfo | None = None
        self._host: str | None = None  # resolved numeric IPv4 address (never a name)
        self._host_spelling: str | None = None  # what connect() was given, for messages
        self._port = 10023
        self._transport: asyncio.DatagramTransport | None = None
        self._tasks: list[asyncio.Task] = []
        self._closed = False  # close() ran: the loops must stop instead of re-binding
        self._wake = asyncio.Event()  # kicks the watchdog out of its sleep when we degrade

        self._pending: dict[str, deque[asyncio.Future[OscMessage]]] = defaultdict(deque)
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_gen = 0
        self._inflight: dict[str, asyncio.Future] = {}
        self._update_cbs: list[UpdateCallback] = []
        self._blob_cbs: list[BlobCallback] = []

        self._last_rx: float | None = None
        self._rtt_ms: float | None = None
        self._reconnect_attempts = 0
        self._error: str | None = None
        self._heartbeat_ok = False
        self._rx_count = 0
        self._decode_errors = 0
        self._socket_errors = 0
        self._consecutive_errors = 0

    # -- lifecycle -----------------------------------------------------------------

    async def connect(self, host: str, port: int = 10023) -> ConsoleInfo:
        """Resolve ``host``, bind the socket, do the ``/info`` round trip, start the loops.

        ``host`` is an IP address or a hostname; it is resolved to IPv4 **once**, here, and
        every later datagram goes to that numeric address. Raises :class:`ConnectionError`
        when the name does not resolve or the socket cannot be opened, and
        :class:`RequestTimeout` (socket closed again, state DISCONNECTED) when the desk does
        not answer ``/info`` within ``timeout_s × (retries + 1)``.
        """
        if self._transport is not None or self._tasks:
            await self.close()  # also when the socket died but the loops are still probing
        self._closed = False
        self._host_spelling = host
        self._host, self._port = host, int(port)
        self._error = None
        self._reconnect_attempts = 0
        self._last_rx = None
        self._rtt_ms = None
        self._set_state(ConnectionState.CONNECTING)

        self._host = await self._resolve(host)

        try:
            await self._bind_socket()
        except OSError as e:
            self._error = f"cannot open UDP socket: {e}"
            self._set_state(ConnectionState.DISCONNECTED)
            raise ConnectionError(self._error) from e

        try:
            info = await self._request_bytes(_INFO_REQ, "/info", self._timeout_s, self._retries, what="/info")
        except ConnectionError as e:
            await self._teardown()
            self._error = f"no /info reply from {host}:{self._port}"
            self._set_state(ConnectionState.DISCONNECTED)
            raise RequestTimeout(self._error) from e

        # per transport.md §3.1: ,ssss server_version "osc-server" model firmware
        fields = [str(a) for a in info.args] + [""] * 4
        server_version, name, model, firmware = fields[:4]
        # The console name lives in /xinfo slot 2; /info slot 2 is "osc-server" on a real desk
        # (transport.md §3.2, §9 item 11). Not answering /xinfo is not fatal.
        try:
            xinfo = await self._request_bytes(_XINFO_REQ, "/xinfo", self._timeout_s, 0, what="/xinfo")
            if len(xinfo.args) >= 2:
                name = str(xinfo.args[1])
        except RequestTimeout:
            log.debug("no /xinfo reply; using /info slot 2 (%r) as the console name", name)

        self._console = ConsoleInfo(
            host=host, port=self._port, name=name, model=model, firmware=firmware, server_version=server_version
        )
        loop = asyncio.get_running_loop()
        self._tasks = [
            loop.create_task(self._heartbeat_loop(), name="x32-heartbeat"),
            loop.create_task(self._watchdog_loop(), name="x32-watchdog"),
        ]
        self._set_state(ConnectionState.CONNECTED)
        log.info(
            "connected to %s %s (FW %s, %s) at %s:%d from local port %s",
            model, name, firmware, server_version, host, self._port, (self.local_addr or ("?", "?"))[1],
        )
        return self._console

    async def close(self) -> None:
        """Stop the tasks, fail pending requests with :class:`NotConnected`, close the socket."""
        self._closed = True  # tells the loops this is a shutdown, not a socket to re-bind
        self._wake.set()
        tasks, self._tasks = self._tasks, []
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._teardown()
        self._console = None
        self._heartbeat_ok = False
        if self._state is not ConnectionState.DISCONNECTED:
            self._set_state(ConnectionState.DISCONNECTED)

    async def _teardown(self) -> None:
        transport, self._transport = self._transport, None
        if transport is not None:
            transport.close()
        self._fail_pending(NotConnected("connection closed"))
        self._cache.clear()
        self._cache_gen += 1

    async def _resolve(self, host: str) -> str:
        """``host`` (name or IP) -> a numeric IPv4 address, resolved once.

        ``DatagramTransport.sendto`` hands a non-numeric address straight to
        ``socket.sendto``, which resolves it *synchronously on the event-loop thread* — once
        per request, heartbeat and meter renewal. Worse, on the Windows proactor loop the
        ``gaierror`` from a name that does not resolve arrives at ``error_received`` and is
        swallowed, so the failure would surface as a misleading ``/info`` timeout.
        """
        try:
            infos = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(
                    host, self._port, family=socket.AF_INET, type=socket.SOCK_DGRAM
                ),
                _RESOLVE_TIMEOUT_S,
            )
        # gaierror is an OSError; a malformed name ("x32..local") raises UnicodeError from the
        # IDNA codec instead, and that must not escape connect() raw either.
        except (OSError, UnicodeError, asyncio.TimeoutError) as e:
            self._error = f"cannot resolve {host!r}: {e}"
            self._set_state(ConnectionState.DISCONNECTED)
            raise ConnectionError(self._error) from e
        if not infos:
            self._error = f"cannot resolve {host!r}: no IPv4 address"
            self._set_state(ConnectionState.DISCONNECTED)
            raise ConnectionError(self._error)
        return str(infos[0][4][0])

    async def _bind_socket(self) -> None:
        """Bind one UDP socket on an ephemeral port and install :class:`_Protocol` on it.

        Raises :class:`OSError`; what a bind failure means is the caller's business (fatal in
        ``connect()``, "try again next backoff" in the reconnect loop).
        """
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setblocking(False)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, _RCVBUF)
            except OSError:
                pass
            # One socket, any port: the desk answers to the source port (transport.md §2).
            sock.bind(("0.0.0.0", 0))
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _Protocol(self._handle_datagram, self._handle_socket_error, self._handle_transport_lost),
                sock=sock,
            )
        except OSError:
            sock.close()
            raise
        self._transport = transport

    async def _ensure_socket(self) -> bool:
        """True when a usable socket is bound, re-binding one when the old transport was
        closed under us (``abort()``, a fatal proactor error, the loop shutting it down).
        False when it cannot be bound right now — the caller retries after its backoff.
        """
        if self._closed:
            return False
        transport = self._transport
        if transport is not None and not transport.is_closing():
            return True
        if transport is not None:
            self._transport = None
            transport.close()
        try:
            await self._bind_socket()
        except OSError as e:
            log.debug("cannot re-bind UDP socket: %s", e)
            return False
        log.info("re-bound UDP socket on local port %s after the old one closed", (self.local_addr or ("?", "?"))[1])
        return True

    # -- status ------------------------------------------------------------------------

    @property
    def status(self) -> ConnectionStatus:
        now = time.monotonic()
        tasks_alive = bool(self._tasks) and not any(t.done() for t in self._tasks)
        return ConnectionStatus(
            state=self._state,
            console=self._console,
            last_rx_age_s=None if self._last_rx is None else now - self._last_rx,
            heartbeat_ok=self._heartbeat_ok and tasks_alive and self._state is ConnectionState.CONNECTED,
            pending_requests=sum(1 for dq in self._pending.values() for f in dq if not f.done()),
            rtt_ms=self._rtt_ms,
            reconnect_attempts=self._reconnect_attempts,
            error=self._error,
        )

    @property
    def connected(self) -> bool:
        """True only in CONNECTED (not DEGRADED)."""
        return self._state is ConnectionState.CONNECTED

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def _where(self) -> str:
        """``host:port`` for messages, in the spelling the caller used (not the resolved IP)."""
        return f"{self._host_spelling or self._host}:{self._port}"

    @property
    def local_addr(self) -> tuple[str, int] | None:
        """``(ip, port)`` the socket is bound to — the port the desk replies to."""
        if self._transport is None:
            return None
        name = self._transport.get_extra_info("sockname")
        return (name[0], name[1]) if name else None

    @property
    def stats(self) -> dict[str, int]:
        """Counters for diagnostics: decodable datagrams, decode failures, socket errors."""
        return {"rx": self._rx_count, "decode_errors": self._decode_errors, "socket_errors": self._socket_errors}

    # -- request / reply ---------------------------------------------------------------

    async def request(
        self,
        address: str,
        *args: Any,
        reply_address: str | None = None,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> OscMessage:
        """Send ``address`` (+ raw OSC args) and await the first reply on ``reply_address``
        (default: the same address). Each retry re-sends. While DEGRADED only one attempt is
        made. Raises :class:`NotConnected` without a socket, :class:`RequestTimeout` otherwise.
        """
        if self._transport is None:
            raise NotConnected("not connected — call connect() first")
        per_attempt = self._timeout_s if timeout is None else float(timeout)
        n_retries = self._retries if retries is None else max(0, int(retries))
        if self._state is ConnectionState.DEGRADED:
            n_retries = 0  # the reconnect loop is already probing; one shot is how we notice recovery
        data = encode(address, *args)
        return await self._request_bytes(data, reply_address or address, per_attempt, n_retries, what=address)

    async def get(self, address: str) -> Any:
        """GET: the raw OSC value (``args[0]``; a tuple when the reply has several args)."""
        return _value(await self.request(address))

    async def set(self, address: str, *args: Any, typetags: str | None = None) -> None:
        """Fire-and-forget write (no ack exists, transport.md §5.2). Invalidates the read cache
        for ``address`` and publishes ``write`` ``{address, args}``. Refused with
        :class:`NotConnected` while DEGRADED or without a socket. Raw OSC values: pass a float
        (or ``typetags="f"``) for float parameters.
        """
        self._require_writable()
        self._send(encode(address, *args, typetags=typetags))
        self.invalidate(address)
        log.debug("set %s %r", address, args)
        self._events.publish("write", address=address, args=list(args))

    async def node(self, path: str) -> str:
        """``/node ,s path`` → the reply line without its trailing newline, e.g.
        ``'/ch/01/config "Vox" 1 CY 1'``. ``path`` may carry a leading slash (stripped: the
        desk wants ``ch/01/config``, transport.md §6.1).
        """
        p = path.strip().lstrip("/")
        msg = await self.request("/node", p, reply_address=_node_key(p))
        line = msg.args[0] if msg.args and isinstance(msg.args[0], str) else ""
        return line.rstrip("\r\n")

    async def slash(self, line: str, *, timeout: float | None = None) -> None:
        """Node-style WRITE (transport.md §6.6): send ``/ ,s "<path> <v1> <v2> …"`` (engineering
        units, enum tokens, quoted strings; leading slash optional; partial trailing lists
        allowed) and await the desk's verbatim echo as the ack. ``timeout`` is per attempt
        (default ``timeout_s``), retried like :meth:`request`. Raises :class:`RequestTimeout`
        when no echo arrives, :class:`NotConnected` while DEGRADED. Policy is the caller's job.
        """
        self._require_writable()
        text = line.strip()
        if not text:
            raise ValueError("empty node line")
        per_attempt = self._timeout_s if timeout is None else float(timeout)
        await self._request_bytes(encode("/", text), "/", per_attempt, self._retries, what=f"/ {text!r}")

    async def node_many(self, paths: list[str], concurrency: int = 16) -> dict[str, str | None]:
        """Pipelined :meth:`node` sweep with at most ``concurrency`` requests in flight.
        A path that times out (or is unknown to the desk) maps to ``None``; never raises for
        transport failures.
        """
        sem = asyncio.Semaphore(max(1, int(concurrency)))

        async def one(p: str) -> str | None:
            async with sem:
                try:
                    return await self.node(p)
                except (ConnectionError, OscError) as e:
                    log.debug("node %s: %s", p, e)
                    return None

        results = await asyncio.gather(*(one(p) for p in paths))
        return dict(zip(paths, results))

    async def send_raw(self, address: str, *args: Any, typetags: str | None = None) -> None:
        """Send without cache/policy/events (``/meters``, ``/renew``, ``/xremote``). Allowed
        while DEGRADED; raises :class:`NotConnected` without a socket."""
        self._send(encode(address, *args, typetags=typetags))

    # -- cache ---------------------------------------------------------------------------

    async def get_cached(self, address: str) -> Any:
        """Read-through cache with TTL ``descriptor.policy["read_cache_ttl_s"]`` (default 2 s).
        Filled by reads and by pushed ``/xremote`` updates; concurrent misses on one address
        share a single request."""
        ttl = self._cache_ttl()
        entry = self._cache.get(address)
        if entry is not None and time.monotonic() - entry[0] <= ttl:
            return entry[1]
        fut = self._inflight.get(address)
        if fut is None:
            fut = asyncio.ensure_future(self.get(address))
            self._inflight[address] = fut

            def _done(f: asyncio.Future, a: str = address) -> None:
                if self._inflight.get(a) is f:
                    del self._inflight[a]
                if not f.cancelled():
                    f.exception()  # retrieved: no "exception was never retrieved" noise

            fut.add_done_callback(_done)
        gen = self._cache_gen
        value = await asyncio.shield(fut)
        if self._cache_gen == gen:  # not invalidated by a write while we were waiting
            self._cache[address] = (time.monotonic(), value)
        return value

    def invalidate(self, address: str | None = None) -> None:
        """Drop one cached address, or everything when ``address`` is None."""
        self._cache_gen += 1
        if address is None:
            self._cache.clear()
        else:
            self._cache.pop(address, None)

    def _cache_ttl(self) -> float:
        policy = getattr(self._d, "policy", None)
        try:
            return float(policy.get("read_cache_ttl_s", 2.0)) if policy else 2.0
        except (AttributeError, TypeError, ValueError):
            return 2.0

    # -- callbacks -----------------------------------------------------------------------

    def on_update(self, callback: UpdateCallback) -> Callable[[], None]:
        """Pushed parameter updates (and unmatched replies) as ``(address, args)``."""
        return self._add_cb(self._update_cbs, callback)

    def on_blob(self, callback: BlobCallback) -> Callable[[], None]:
        """Every ``,b`` message as ``(address, blob_bytes)``, e.g. ``/meters/15``."""
        return self._add_cb(self._blob_cbs, callback)

    @staticmethod
    def _add_cb(cbs: list, callback: Callable) -> Callable[[], None]:
        cbs.append(callback)

        def _remove() -> None:
            try:
                cbs.remove(callback)
            except ValueError:
                pass

        return _remove

    @staticmethod
    def _dispatch(cbs: list, *args: Any) -> None:
        for cb in list(cbs):
            try:
                cb(*args)
            except Exception:  # a bad subscriber must never break the receive loop
                log.exception("callback %r failed", cb)

    # -- discovery -----------------------------------------------------------------------

    async def discover(self, timeout_s: float = 2.0, port: int = 10023) -> list[ConsoleInfo]:
        """Broadcast ``/xinfo`` from a temporary SO_BROADCAST socket and collect every reply
        for ``timeout_s``. Targets: 255.255.255.255, each local /24 directed broadcast (what
        X32_Command does, transport.md §3.2), 127.0.0.1 (fake desk) and the connected host.
        Returns ``[]`` (never raises) when the network is unusable."""
        loop = asyncio.get_running_loop()
        found: dict[tuple[str, int], ConsoleInfo] = {}
        errors = 0
        proto: _Protocol | None = None

        def on_datagram(data: bytes, addr: Any) -> None:
            try:
                msg = decode(data)
            except OscError:
                return
            if msg.address != "/xinfo" or len(msg.args) < 4:
                return
            # per transport.md §3.2: ,ssss desk_ip console_name model firmware
            _ip, name, model, firmware = (str(a) for a in msg.args[:4])
            key = (addr[0], addr[1])
            found.setdefault(
                key, ConsoleInfo(host=addr[0], port=addr[1], name=name, model=model, firmware=firmware, server_version="")
            )

        def on_error(exc: Exception) -> None:
            nonlocal errors
            errors += 1
            log.debug("discovery socket error (ignored): %r", exc)
            _rearm_reader(proto.transport if proto else None, errors)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setblocking(False)
            sock.bind(("0.0.0.0", 0))
            transport, proto = await loop.create_datagram_endpoint(
                lambda: _Protocol(on_datagram, on_error, lambda _t, _e: None), sock=sock  # short-lived: nothing to rebind
            )
        except OSError as e:
            sock.close()
            log.warning("discovery: cannot open broadcast socket: %s", e)
            return []
        try:
            targets = await self._discovery_targets(int(port))
            deadline = loop.time() + max(0.0, float(timeout_s))
            for round_no in (0, 1):  # X32_Command re-sends up to 5×; two rounds cover one lost datagram
                for dest in targets:
                    try:
                        transport.sendto(_XINFO_REQ, dest)
                    except OSError as e:
                        log.debug("discovery: send to %s failed: %s", dest, e)
                remaining = deadline - loop.time()
                if remaining > 0:
                    await asyncio.sleep(remaining / 2 if round_no == 0 else remaining)
        finally:
            transport.close()
        consoles = sorted(found.values(), key=lambda c: (c.host, c.port))
        log.info("discovery found %d console(s)", len(consoles))
        return consoles

    async def _discovery_targets(self, port: int) -> list[tuple[str, int]]:
        hosts = ["255.255.255.255", "127.0.0.1"]
        if self._host:
            hosts.append(self._host)
        loop = asyncio.get_running_loop()
        try:
            infos = await asyncio.wait_for(
                loop.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET, type=socket.SOCK_DGRAM), 1.0
            )
            for info in infos:
                ip = info[4][0]
                if not ip.startswith("127."):
                    hosts.append(ip.rsplit(".", 1)[0] + ".255")  # /24 directed broadcast (transport.md §3.2)
        except (OSError, asyncio.TimeoutError):
            pass
        return [(h, port) for h in dict.fromkeys(hosts)]

    # -- internals: sending and matching -------------------------------------------------

    def _send(self, data: bytes) -> None:
        t = self._transport
        if t is None or t.is_closing() or self._host is None:
            raise NotConnected("not connected")
        t.sendto(data, (self._host, self._port))

    def _require_writable(self) -> None:
        if self._transport is None:
            raise NotConnected("not connected — call connect() first")
        if self._state is ConnectionState.DEGRADED:
            raise NotConnected(f"desk not responding ({self._error}); write refused until it answers again")
        if self._state is not ConnectionState.CONNECTED:
            raise NotConnected(f"connection is {self._state.value}")

    async def _request_bytes(self, data: bytes, key: str, timeout: float, retries: int, *, what: str) -> OscMessage:
        loop = asyncio.get_running_loop()
        attempts = retries + 1
        for attempt in range(attempts):
            fut: asyncio.Future[OscMessage] = loop.create_future()
            self._pending[key].append(fut)
            t0 = time.monotonic()
            try:
                self._send(data)
                msg = await asyncio.wait_for(fut, timeout)
            except asyncio.TimeoutError:
                self._discard(key, fut)
                if attempt + 1 < attempts:
                    log.debug("no reply to %s (attempt %d/%d), re-sending", what, attempt + 1, attempts)
                continue
            except BaseException:
                self._discard(key, fut)
                raise
            self._rtt_ms = (time.monotonic() - t0) * 1000.0
            return msg
        raise RequestTimeout(
            f"no reply to {what} from {self._where} after {attempts} attempt(s) ({timeout * attempts:.1f} s)"
        )

    def _discard(self, key: str, fut: asyncio.Future) -> None:
        dq = self._pending.get(key)
        if dq is None:
            return
        try:
            dq.remove(fut)
        except ValueError:
            pass
        if not dq:
            del self._pending[key]

    def _pop_waiter(self, key: str) -> asyncio.Future | None:
        dq = self._pending.get(key)
        if not dq:
            self._pending.pop(key, None)
            return None
        waiter = None
        while dq:
            fut = dq.popleft()
            if not fut.done():
                waiter = fut
                break
        if not dq:
            del self._pending[key]
        return waiter

    def _fail_pending(self, exc: Exception) -> None:
        pending, self._pending = self._pending, defaultdict(deque)
        for dq in pending.values():
            for fut in dq:
                if not fut.done():
                    fut.set_exception(exc)

    # -- internals: receiving --------------------------------------------------------------

    def _handle_datagram(self, data: bytes, addr: Any) -> None:
        try:
            msg = decode(data)
        except OscError as e:
            self._decode_errors += 1
            log.debug("undecodable datagram from %s (%d B): %s", addr, len(data), e)
            return
        self._rx_count += 1
        self._consecutive_errors = 0
        self._last_rx = time.monotonic()
        if self._state is ConnectionState.DEGRADED:
            self._recover()
        log.debug("rx %s", msg)  # OscMessage.__str__ renders blobs as "<blob N B>", never their bytes

        waiter = self._pop_waiter(_reply_key(msg))
        if waiter is not None:
            waiter.set_result(msg)
            return
        blob = next((a for a in msg.args if isinstance(a, bytes)), None)
        if blob is not None:
            self._dispatch(self._blob_cbs, msg.address, blob)
            return
        if msg.address == _NODE_REPLY or msg.address in _NOT_PARAMS or not msg.args or not msg.address.startswith("/"):
            log.debug("ignoring unmatched %s", msg)
            return
        # Pushed /xremote update (or a late reply): the desk is the source of truth.
        self._cache[msg.address] = (self._last_rx, _value(msg))
        self._dispatch(self._update_cbs, msg.address, msg.args)
        self._events.publish("update", address=msg.address, args=list(msg.args))

    def _handle_socket_error(self, exc: Exception) -> None:
        self._socket_errors += 1
        self._consecutive_errors += 1
        log.debug("socket error (ignored): %r", exc)
        _rearm_reader(self._transport, self._consecutive_errors)

    def _handle_transport_lost(self, transport: Any, exc: Exception | None) -> None:
        """The socket died under us: ``abort()``, a fatal proactor error, or the loop tearing
        the transport down. Drop it, fail what is in flight and degrade, so the watchdog's
        reconnect loop re-binds a socket — otherwise ``_send`` would raise
        :class:`NotConnected` forever while the state still claimed CONNECTED.
        """
        if self._closed or transport is not self._transport:
            return  # close() or a fresh connect() already replaced it
        self._transport = None
        self._heartbeat_ok = False
        self._fail_pending(NotConnected(f"socket closed ({exc!r})" if exc is not None else "socket closed"))
        if self._state in (ConnectionState.CONNECTING, ConnectionState.CONNECTED):
            self._degrade(0.0, reason="socket closed under us; re-binding")

    # -- internals: state, heartbeat, watchdog ---------------------------------------------

    def _set_state(self, state: ConnectionState) -> None:
        self._state = state
        self._events.publish(
            "connection.state",
            state=state.value,
            host=self._host,
            port=self._port,
            console=self._console.name if self._console else None,
            error=self._error,
        )

    def _degrade(self, idle_s: float, *, reason: str | None = None) -> None:
        if self._state is ConnectionState.DEGRADED:
            return
        self._error = reason or f"desk not responding (no packets for {idle_s:.1f} s)"
        self._reconnect_attempts = 0
        self._heartbeat_ok = False
        log.warning("%s %s — degraded, reconnecting", self._where, self._error)
        self._set_state(ConnectionState.DEGRADED)
        self._wake.set()  # start the reconnect loop now, not after the rest of the watchdog nap

    def _recover(self) -> None:
        if self._state is not ConnectionState.DEGRADED:
            return
        self._error = None
        log.info("%s answering again after %d probe(s)", self._where, self._reconnect_attempts)
        self._set_state(ConnectionState.CONNECTED)
        try:
            self._send(_XREMOTE_REQ)  # re-register for pushes immediately; the desk may have rebooted
            self._heartbeat_ok = True
        except NotConnected:
            pass

    async def _heartbeat_loop(self) -> None:
        # per transport.md §4.1: pushes live 10 s per /xremote; Maillot's tools renew every 9 s
        while True:
            try:
                self._send(_XREMOTE_REQ)
                self._heartbeat_ok = True
            except NotConnected:
                self._heartbeat_ok = False
            await asyncio.sleep(self._heartbeat_s)

    async def _sleep_or_wake(self, delay: float) -> None:
        """Sleep ``delay`` seconds, returning early once :meth:`_degrade` rings the bell."""
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), delay)
        except asyncio.TimeoutError:
            pass

    async def _watchdog_loop(self) -> None:
        while not self._closed:
            if self._state is ConnectionState.DEGRADED:
                await self._reconnect_loop()  # re-binds the socket too, if it died
                continue
            now = time.monotonic()
            last = self._last_rx if self._last_rx is not None else now
            due_in = last + self._watchdog_s - now
            if due_in > 0:
                await self._sleep_or_wake(due_in)
                continue
            # Silence: /xremote is never acked, so probe with /info (transport.md §3.1).
            probe_started = time.monotonic()
            try:
                await self._request_bytes(_INFO_REQ, "/info", self._timeout_s, self._retries, what="/info")
                continue
            except RequestTimeout:
                if self._last_rx is not None and self._last_rx > probe_started:
                    continue  # something else got a reply meanwhile; the desk is alive
            except NotConnected:
                if self._closed:
                    return
                # A dead transport is a reason to degrade and keep probing, not to give up:
                # returning here would leave the connection in CONNECTED with nothing
                # reconnecting it, and no socket would ever be re-created.
            self._degrade(time.monotonic() - last)
            await self._reconnect_loop()

    async def _reconnect_loop(self) -> None:
        n = 0
        while self._state is ConnectionState.DEGRADED and not self._closed:
            await asyncio.sleep(self._backoff_s[min(n, len(self._backoff_s) - 1)])
            if self._state is not ConnectionState.DEGRADED or self._closed:
                return  # a datagram arrived in the meantime, or we are shutting down
            n += 1
            self._reconnect_attempts = n
            if not await self._ensure_socket():
                continue  # nothing to probe from; try to bind again after the next backoff
            try:
                await self._request_bytes(_INFO_REQ, "/info", self._timeout_s, 0, what="/info")
            except RequestTimeout:
                log.debug("reconnect probe %d to %s unanswered", n, self._where)
                continue
            except NotConnected:
                continue  # the socket died between the check and the send; re-bind next round
            self._recover()
