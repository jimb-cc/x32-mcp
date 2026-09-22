## Appendix C — the in-memory UDP shim used to run the integration suites in a network-less sandbox

Drop-in `tests/conftest.py`; activates only when binding 127.0.0.1 raises `PermissionError`, otherwise inert. Not part of any PR.

```python
"""In-memory UDP loopback for sandboxes that forbid AF_INET sockets (local review tooling).

Activates only when binding 127.0.0.1 raises PermissionError. It replaces ``socket.socket`` for
IPv4 datagram sockets with a fake that never touches the kernel, and routes
``loop.create_datagram_endpoint`` through an in-process port registry so ``FakeDesk`` and
``X32Connection`` talk to each other exactly as they would over loopback UDP (unordered delivery
is not simulated; loss is still injectable through FakeDesk.drop_next). Not part of the product.
"""

from __future__ import annotations

import asyncio
import itertools
import socket as _socket
from typing import Any

_REAL_SOCKET = _socket.socket


def _sandboxed() -> bool:
    s = _REAL_SOCKET(_socket.AF_INET, _socket.SOCK_DGRAM)
    try:
        s.bind(("127.0.0.1", 0))
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    finally:
        s.close()


_REGISTRY: dict[int, "_MemTransport"] = {}
_PORTS = itertools.count(41000)


class _MemUdpSocket:
    family = _socket.AF_INET
    type = _socket.SOCK_DGRAM
    proto = 0

    def __init__(self, *a: Any, **k: Any) -> None:
        self._addr: tuple[str, int] | None = None
        self._closed = False
        self._opts: dict[tuple[int, int], int] = {}

    # kernel-ish surface used by connection.py / fakedesk.py
    def setblocking(self, flag: bool) -> None: ...
    def settimeout(self, t: Any) -> None: ...
    def setsockopt(self, level: int, opt: int, value: Any) -> None: self._opts[(level, opt)] = value
    def getsockopt(self, level: int, opt: int, *a: Any) -> int: return int(self._opts.get((level, opt), 0))
    def fileno(self) -> int: return -1
    def getsockname(self) -> tuple[str, int]: return self._addr or ("0.0.0.0", 0)
    def getpeername(self) -> tuple[str, int]: raise OSError("not connected")
    def bind(self, addr: tuple[str, int]) -> None:
        host, port = addr[0] or "0.0.0.0", int(addr[1])
        if port == 0:
            port = next(_PORTS)
            while port in _REGISTRY:
                port = next(_PORTS)
        elif port in _REGISTRY and not _REGISTRY[port].is_closing():
            raise OSError(98, f"Address already in use: {port}")
        self._addr = (host if host != "0.0.0.0" else "127.0.0.1", port)
    def sendto(self, data: bytes, addr: tuple[str, int]) -> int:
        if self._addr is None:
            self.bind(("0.0.0.0", 0))
        _deliver(bytes(data), self._addr, addr)  # type: ignore[arg-type]
        return len(data)
    def close(self) -> None: self._closed = True
    def detach(self) -> int: return -1
    def __enter__(self) -> "_MemUdpSocket": return self
    def __exit__(self, *exc: Any) -> None: self.close()


def _is_broadcast(host: str) -> bool:
    return host == "255.255.255.255" or host.endswith(".255") or host == "<broadcast>"


def _deliver(data: bytes, src: tuple[str, int], dst: tuple[str, int]) -> None:
    loop = asyncio.get_event_loop()
    host, port = dst[0], int(dst[1])
    if _is_broadcast(str(host)):
        targets = [t for p, t in list(_REGISTRY.items()) if p != src[1]]
    else:
        t = _REGISTRY.get(port)
        targets = [t] if t is not None else []
    for t in targets:
        if t.is_closing() or t._protocol is None:
            continue
        loop.call_soon(t._protocol_datagram, data, (src[0], src[1]))


class _MemTransport(asyncio.DatagramTransport):
    def __init__(self, loop: asyncio.AbstractEventLoop, protocol: asyncio.DatagramProtocol, sock: _MemUdpSocket,
                 remote: tuple[str, int] | None) -> None:
        super().__init__()
        self._loop = loop
        self._protocol = protocol
        self._sock = sock
        self._remote = remote
        self._closing = False
        if sock._addr is None:
            sock.bind(("0.0.0.0", 0))
        _REGISTRY[sock._addr[1]] = self  # type: ignore[index]

    def _protocol_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        if self._closing or self._protocol is None:
            return
        try:
            self._protocol.datagram_received(data, addr)
        except Exception:  # mirror asyncio: handler errors never kill the transport
            import logging
            logging.getLogger(__name__).exception("datagram_received raised")

    def sendto(self, data: Any, addr: Any = None) -> None:  # type: ignore[override]
        if self._closing:
            return
        dst = addr or self._remote
        if dst is None:
            raise ValueError("sendto needs an address")
        _deliver(bytes(data), self._sock._addr, (dst[0], int(dst[1])))  # type: ignore[arg-type]

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        if name == "sockname":
            return self._sock.getsockname()
        if name == "socket":
            return self._sock
        if name == "peername":
            return self._remote
        return default

    def is_closing(self) -> bool:
        return self._closing

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        port = self._sock._addr[1] if self._sock._addr else None
        if port is not None and _REGISTRY.get(port) is self:
            del _REGISTRY[port]
        proto, self._protocol = self._protocol, None
        if proto is not None:
            self._loop.call_soon(proto.connection_lost, None)

    def abort(self) -> None:
        self.close()

    def get_protocol(self) -> Any:
        return self._protocol

    def set_protocol(self, protocol: Any) -> None:
        self._protocol = protocol


def _install() -> None:
    def fake_socket(family: int = _socket.AF_INET, type: int = _socket.SOCK_STREAM, proto: int = 0, fileno: Any = None):
        if family == _socket.AF_INET and (type & 0xF) == _socket.SOCK_DGRAM and fileno is None:
            return _MemUdpSocket()
        return _REAL_SOCKET(family, type, proto, fileno)

    _socket.socket = fake_socket  # type: ignore[misc,assignment]

    async def create_datagram_endpoint(self, protocol_factory, local_addr=None, remote_addr=None, *, family=0,
                                       proto=0, flags=0, reuse_port=None, allow_broadcast=None, sock=None,
                                       **_kw):
        if sock is None:
            sock = _MemUdpSocket()
            if local_addr is not None:
                sock.bind((local_addr[0], int(local_addr[1])))
        if not isinstance(sock, _MemUdpSocket):
            return await _orig_cde(self, protocol_factory, local_addr, remote_addr, family=family, proto=proto,
                                   flags=flags, reuse_port=reuse_port, allow_broadcast=allow_broadcast, sock=sock)
        remote = (remote_addr[0], int(remote_addr[1])) if remote_addr else None
        protocol = protocol_factory()
        transport = _MemTransport(self, protocol, sock, remote)
        self.call_soon(protocol.connection_made, transport)
        await asyncio.sleep(0)
        return transport, protocol

    _orig_cde = asyncio.BaseEventLoop.create_datagram_endpoint
    asyncio.BaseEventLoop.create_datagram_endpoint = create_datagram_endpoint  # type: ignore[assignment]

    async def getaddrinfo(self, host, port, *, family=0, type=0, proto=0, flags=0):
        h = host if isinstance(host, str) else host.decode()
        try:
            _socket.inet_aton(h)
            ip = h
        except OSError:
            if h in ("localhost", _socket.gethostname(), ""):
                ip = "127.0.0.1"
            else:
                raise _socket.gaierror(8, f"nodename nor servname provided, or not known: {h}")
        return [(_socket.AF_INET, _socket.SOCK_DGRAM, 17, "", (ip, int(port or 0)))]

    asyncio.BaseEventLoop.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    _socket.gethostbyname = lambda name: "127.0.0.1"  # type: ignore[assignment]


if _sandboxed():
    _install()
    MEM_UDP_ACTIVE = True
else:
    MEM_UDP_ACTIVE = False
```
