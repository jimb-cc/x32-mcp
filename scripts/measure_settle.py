#!/usr/bin/env python
"""Measure the X32's read-after-write settle time per address class (REVIEW_BRIEF §5).

We do not know how long after a SET a read may still return the old value (transport.md §5.4:
real-desk latency UNCONFIRMED; HANDOVER §4b: an insert/on read-back and a name read-back were
both stale). This one-off script measures it and prints a ``timing:`` block for device.yaml.

For every probe address (one per class) it runs N trials, alternating two values so each write
is a real change, and records:

* **set_get_ms**  — SET, then back-to-back GETs of the same leaf until the reply matches:
  time from send to the first matching reply, and how many *stale* replies came first.
  0 stale replies on every trial means the desk applies that class before serving the next
  datagram (the ordering-barrier hypothesis holds for it); >0 means asynchronous application.
* **set_node_ms** — SET, then ``/node`` of the owning section until the printed field matches
  (the Desk facade reads node text; the firmware may serve it from a different copy).
* **slash_ms**    — ``/`` node-style write: time to the echo, and whether the first GET after the
  echo already matches (does the echo mean *applied* or only *received*? DOC 4591 is silent).
* **push_ms**     — with ``--second-socket``: a second client holding ``/xremote`` times the desk's
  push of our write (and confirms quirk 8: the writing socket gets no echo).
* **derived_ms**  — ``/-prefs/rta/source`` → ``/-stat/rtasource`` follow time.

SAFETY: it only touches one scratch channel (``--ch``, default 32: fader, mute, name, insert/on
with the insert point OFF), the RTA source preference, and — only with ``--fx-slot N`` — the type
of that FX slot (it loads GEQ2 then restores the previous type: audible if the slot is in use!).
Every touched value is read first and written back at the end. Nothing on a bus, matrix or main
is written. It bypasses Policy on purpose (raw connection): run it with the PA muted anyway.

    .venv/bin/python scripts/measure_settle.py 192.168.1.139 --trials 30 --second-socket
    .venv/bin/python scripts/measure_settle.py 192.168.1.139 --fx-slot 8 --yes > settle.json

The JSON goes to stdout, progress to stderr; the last thing on stderr is the YAML to paste under
``timing:`` in device.yaml (settle_ms = 2 × p95 rounded up, deadline_ms = 4 × max, floor 250).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from x32mcp.connection import RequestTimeout, X32Connection  # noqa: E402
from x32mcp.descriptor import Descriptor  # noqa: E402
from x32mcp.events import EventBus  # noqa: E402
from x32mcp.nodes import parse_node_line  # noqa: E402

POLL_BUDGET_S = 2.0


@dataclass
class Probe:
    klass: str
    address: str  # leaf written with SET
    values: tuple[Any, Any]  # raw OSC values alternated
    node_path: str  # owning section for the /node poll
    node_field: str
    slash_line: Callable[[Any], str] | None = None  # engineering "/"-line for value index 0/1 (None: skip)
    derived: tuple[str, Callable[[Any], Any]] | None = None  # (address, expected raw for written raw)


@dataclass
class Trial:
    set_get_ms: float | None = None
    stale_get_replies: int = 0
    set_node_ms: float | None = None
    stale_node_replies: int = 0
    slash_echo_ms: float | None = None
    slash_applied_at_echo: bool | None = None
    slash_apply_ms: float | None = None
    push_ms: float | None = None
    derived_ms: float | None = None


def _stats(xs: list[float]) -> dict[str, Any]:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    xs.sort()
    p95 = xs[min(len(xs) - 1, int(math.ceil(0.95 * len(xs))) - 1)]
    return {"n": len(xs), "min": round(xs[0], 2), "p50": round(statistics.median(xs), 2), "p95": round(p95, 2), "max": round(xs[-1], 2)}


async def _poll_get(conn: X32Connection, address: str, want: Any, t0: float, same: Callable[[Any, Any], bool]) -> tuple[float | None, int]:
    stale = 0
    while time.monotonic() - t0 < POLL_BUDGET_S:
        try:
            msg = await conn.request(address, timeout=0.1, retries=0)
        except RequestTimeout:
            continue
        got = msg.args[0] if msg.args else None
        if same(got, want):
            return (time.monotonic() - t0) * 1000.0, stale
        stale += 1
    return None, stale


async def _poll_node(conn: X32Connection, d: Descriptor, path: str, fld: str, want_text: Callable[[Any], bool], t0: float) -> tuple[float | None, int]:
    stale = 0
    node = d.node(path)
    while time.monotonic() - t0 < POLL_BUDGET_S:
        try:
            line = await conn.node(path)
        except RequestTimeout:
            continue
        vals = parse_node_line(line, node)
        if want_text(vals.get(fld)):
            return (time.monotonic() - t0) * 1000.0, stale
        stale += 1
    return None, stale


def _same_raw(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(float(a) - float(b)) <= 2e-3
    return a == b


async def measure(conn: X32Connection, d: Descriptor, probes: list[Probe], trials: int, *, second: X32Connection | None = None,
                  log: Callable[[str], None] = lambda s: None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    pushes: dict[str, tuple[float, Any]] = {}
    if second is not None:
        second.on_update(lambda addr, args: pushes.__setitem__(addr, (time.monotonic(), args[0] if args else None)))
    for pr in probes:
        spec_hit = d.param_for_address(pr.address)
        spec = spec_hit[0] if spec_hit else None
        original = None
        try:
            original = (await conn.request(pr.address, timeout=0.3, retries=1)).args[0]
        except RequestTimeout:
            log(f"  {pr.klass}: {pr.address} does not answer; skipped")
            continue
        derived_original = None
        if pr.derived:
            try:
                derived_original = (await conn.request(pr.derived[0], timeout=0.3, retries=1)).args[0]
            except RequestTimeout:
                derived_original = None
        results: list[Trial] = []
        log(f"  {pr.klass}: {pr.address} ({trials} trials, was {original!r})")
        for i in range(trials):
            want = pr.values[i % 2]
            other = pr.values[(i + 1) % 2]
            # make sure the desk holds `other` first so the write is a change
            await conn.set(pr.address, other)
            await _poll_get(conn, pr.address, other, time.monotonic(), _same_raw)
            await asyncio.sleep(0.05)
            tr = Trial()
            # A: SET -> GET poll
            pushes.pop(pr.address, None)
            t0 = time.monotonic()
            await conn.set(pr.address, want)
            tr.set_get_ms, tr.stale_get_replies = await _poll_get(conn, pr.address, want, t0, _same_raw)
            if second is not None:
                await asyncio.sleep(0.05)
                hit = pushes.get(pr.address)
                tr.push_ms = (hit[0] - t0) * 1000.0 if hit and _same_raw(hit[1], want) else None
            if pr.derived:
                daddr, expect = pr.derived
                tr.derived_ms, _ = await _poll_get(conn, daddr, expect(want), t0, _same_raw)
            # B: SET -> /node poll (write `other` back first)
            await conn.set(pr.address, other)
            await _poll_get(conn, pr.address, other, time.monotonic(), _same_raw)
            await asyncio.sleep(0.05)
            want_val = spec.to_value(want) if spec is not None else want
            t0 = time.monotonic()
            await conn.set(pr.address, want)

            def _matches(v: Any, wv: Any = want_val) -> bool:
                if isinstance(wv, float) and isinstance(v, (int, float)):
                    return abs(float(v) - wv) <= 0.15 or (math.isinf(wv) and isinstance(v, float) and math.isinf(v))
                return str(v).strip().upper() == str(wv).strip().upper()

            tr.set_node_ms, tr.stale_node_replies = await _poll_node(conn, d, pr.node_path, pr.node_field, _matches, t0)
            # C: "/" write -> echo -> GET
            if pr.slash_line is not None:
                await conn.set(pr.address, other)
                await _poll_get(conn, pr.address, other, time.monotonic(), _same_raw)
                await asyncio.sleep(0.05)
                t0 = time.monotonic()
                try:
                    await conn.slash(pr.slash_line(want_val), timeout=0.5)
                    tr.slash_echo_ms = (time.monotonic() - t0) * 1000.0
                    try:
                        first = (await conn.request(pr.address, timeout=0.2, retries=0)).args[0]
                        tr.slash_applied_at_echo = _same_raw(first, want)
                    except RequestTimeout:
                        tr.slash_applied_at_echo = None
                    tr.slash_apply_ms, _ = (0.0, 0) if tr.slash_applied_at_echo else await _poll_get(conn, pr.address, want, t0, _same_raw)
                except RequestTimeout:
                    tr.slash_echo_ms = None
            results.append(tr)
        # restore
        await conn.set(pr.address, original)
        summary = {
            "class": pr.klass, "address": pr.address, "trials": len(results),
            "set_get_ms": _stats([t.set_get_ms for t in results]),
            "stale_get_replies_max": max((t.stale_get_replies for t in results), default=0),
            "applied_before_next_datagram": all(t.stale_get_replies == 0 and t.set_get_ms is not None for t in results),
            "set_node_ms": _stats([t.set_node_ms for t in results]),
            "stale_node_replies_max": max((t.stale_node_replies for t in results), default=0),
            "unconfirmed_trials": sum(1 for t in results if t.set_get_ms is None),
        }
        if pr.slash_line is not None:
            summary["slash_echo_ms"] = _stats([t.slash_echo_ms for t in results])
            summary["slash_applied_at_echo"] = sum(1 for t in results if t.slash_applied_at_echo) / max(1, len(results))
            summary["slash_apply_ms"] = _stats([t.slash_apply_ms for t in results])
        if second is not None:
            summary["push_ms"] = _stats([t.push_ms for t in results])
            summary["own_echo_seen"] = False  # filled below
        if pr.derived:
            summary["derived"] = {"address": pr.derived[0], "ms": _stats([t.derived_ms for t in results])}
        out[pr.klass] = summary
        log(f"    set->get p50 {summary['set_get_ms'].get('p50')} ms p95 {summary['set_get_ms'].get('p95')} ms, "
            f"stale replies max {summary['stale_get_replies_max']}, node p95 {summary['set_node_ms'].get('p95')} ms")
    return out


def timing_yaml(results: dict[str, Any], console: dict[str, Any]) -> str:
    """The block to paste under ``timing:`` in device.yaml (settle deadlines per address class)."""
    lines = ["timing:", f"  measured: {{date: \"{time.strftime('%Y-%m-%d')}\", console: \"{console.get('name')}\", firmware: \"{console.get('firmware')}\", trials: {console.get('trials')}}}",
             "  settle_ms: {default: %d}" % _window(results.get("param")), "  classes:"]
    match = {
        "scene": ["/-action/*", "/load"], "fx_type": ["/fx/*/type"], "insert": ["/*/insert/*", "/*/*/insert/*"],
        "routing": ["/config/routing/*", "/config/userrout/*", "/*/config/source", "/*/*/config/source"],
        "config": ["/*/config/*", "/*/*/config/*", "/headamp/*"], "prefs": ["/-prefs/*", "/-stat/*"], "fx_par": ["/fx/*/par/*"], "param": ["/*"],
    }
    for k, globs in match.items():
        r = results.get(k)
        w, dl = (_window(r), _deadline(r)) if r else (None, None)
        note = "" if r else "   # not measured: prior kept"
        w = w if w is not None else {"scene": 500, "fx_type": 400, "insert": 150, "routing": 150, "config": 150, "prefs": 250, "fx_par": 60, "param": 60}[k]
        dl = dl if dl is not None else {"scene": 2000, "fx_type": 1500}.get(k, 1000 if w > 60 else 500)
        lines.append(f"    {k}: {{settle_ms: {w}, deadline_ms: {dl}, match: {json.dumps(globs)}}}{note}")
    lines.append("  verify: {first_delay_ms: 5, backoff: 2.0, max_interval_ms: 200}")
    return "\n".join(lines)


def _window(r: dict[str, Any] | None) -> int:
    if not r:
        return 60
    worst = max(r["set_get_ms"].get("p95", 0) or 0, r["set_node_ms"].get("p95", 0) or 0, (r.get("derived", {}).get("ms", {}) or {}).get("p95", 0) or 0)
    return max(20, int(math.ceil(2 * worst / 10.0) * 10))


def _deadline(r: dict[str, Any] | None) -> int:
    if not r:
        return 500
    worst = max(r["set_get_ms"].get("max", 0) or 0, r["set_node_ms"].get("max", 0) or 0, (r.get("derived", {}).get("ms", {}) or {}).get("max", 0) or 0)
    return max(250, int(math.ceil(4 * worst / 50.0) * 50))


def default_probes(d: Descriptor, ch: int, fx_slot: int | None, fx_restore_type: int | None = None) -> list[Probe]:
    c = f"{ch:02d}"
    fader = d.param("ch", "mix/fader")
    probes = [
        Probe("param", f"/ch/{c}/mix/fader", (float(fader.to_raw(-40.0)), float(fader.to_raw(-20.0))), f"/ch/{c}/mix", "mix/fader",
              slash_line=lambda v, c=c: f"ch/{c}/mix/fader {v:.1f}"),
        Probe("mute", f"/ch/{c}/mix/on", (0, 1), f"/ch/{c}/mix", "mix/on"),
        Probe("config", f"/ch/{c}/config/name", ("SETTLE-A", "SETTLE-B"), f"/ch/{c}/config", "config/name",
              slash_line=lambda v, c=c: f'ch/{c}/config "{v}"'),
        Probe("insert", f"/ch/{c}/insert/on", (0, 1), f"/ch/{c}/insert", "insert/on"),
        Probe("prefs", "/-prefs/rta/source", (2, 3), "/-prefs/rta", "rta/source",
              derived=("/-stat/rtasource", lambda raw: int(raw) - 2 + 98 * 1)),  # assumes rta/pos = POST(1); adjusted in main()
    ]
    if fx_slot is not None:
        geq2 = 0 if fx_slot >= 5 else d.enum("fx_type_14").index("GEQ2")
        other = fx_restore_type if fx_restore_type is not None and fx_restore_type != geq2 else geq2 + 1
        probes.append(Probe("fx_type", f"/fx/{fx_slot}/type", (other, geq2), f"/fx/{fx_slot}", "type"))
        probes.append(Probe("fx_par", f"/fx/{fx_slot}/par/22", (0.5, 0.4), f"/fx/{fx_slot}/par", "par/22"))
    return probes


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=10023)
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--ch", type=int, default=32, help="scratch input channel (default 32)")
    ap.add_argument("--fx-slot", type=int, default=None, help="also measure an FX type load in this slot (audible if the slot is in use!)")
    ap.add_argument("--second-socket", action="store_true", help="hold /xremote on a second socket to time the desk's push of our writes")
    ap.add_argument("--device-yaml", default=None)
    ap.add_argument("--yes", action="store_true", help="do not ask before writing")
    args = ap.parse_args(argv)

    def log(s: str) -> None:
        print(s, file=sys.stderr, flush=True)

    d = Descriptor.load(args.device_yaml) if args.device_yaml else Descriptor.load()
    conn = X32Connection(d, EventBus(), timeout_s=0.25, retries=1)
    info = await conn.connect(args.host, args.port)
    log(f"connected to {info.name} ({info.model} FW {info.firmware}) at {info.host}:{info.port}")
    fx_restore = None
    if args.fx_slot is not None:
        fx_restore = int((await conn.request(f"/fx/{args.fx_slot}/type")).args[0])
    probes = default_probes(d, args.ch, args.fx_slot, fx_restore)
    pos = int((await conn.request("/-prefs/rta/pos", timeout=0.3, retries=1)).args[0])
    for pr in probes:
        if pr.klass == "prefs":
            pr.derived = ("/-stat/rtasource", lambda raw, pos=pos: int(raw) - 2 + 98 * pos)
    log("will write (and restore): " + ", ".join(p.address for p in probes))
    if not args.yes:
        log("type YES to proceed: ")
        if sys.stdin.readline().strip() != "YES":
            await conn.close()
            return 1
    second = None
    if args.second_socket:
        second = X32Connection(d, EventBus(), timeout_s=0.25, retries=1, heartbeat_s=5.0)
        await second.connect(args.host, args.port)
    own_echo: list[str] = []
    conn.on_update(lambda addr, a: own_echo.append(addr))
    try:
        results = await measure(conn, d, probes, args.trials, second=second, log=log)
    finally:
        if second is not None:
            await second.close()
        await conn.close()
    written = {p.address for p in probes}
    echo_seen = sorted(set(own_echo) & written)
    console = {"name": info.name, "model": info.model, "firmware": info.firmware, "trials": args.trials}
    doc = {"console": console, "results": results, "own_xremote_echo_for_written_addresses": echo_seen,
           "note": "own_xremote_echo non-empty would contradict transport.md §4.2 quirk 8 (sender excluded from pushes)"}
    print(json.dumps(doc, indent=2))
    log("\n# ---- paste under device.yaml ----\n" + timing_yaml(results, console))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
