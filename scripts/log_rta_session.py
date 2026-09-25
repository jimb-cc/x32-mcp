#!/usr/bin/env python
"""Passive, long-running log of the console's ``/meters/15`` RTA stream for the real-programme corpus (docs/research/data/).

Reads only: no pref, oscillator or mix write, ever. What the console's RTA is pointed at and how it is set (source, pos,
det, decay, gain, autogain, peakhold, `/-stat/rtasource`, screen page) is read at the start and re-read every
``--prefs-every`` seconds, and written into the log as ``{"prefs": {...}, "ts": ...}`` lines, so a later replay knows what
the analyser was doing when the engineer changed it. Frames are logged at the stream's own resolution (int16/256 dB, 4
decimals) as ``{"ts": <s>, "db": [100]}``; the file is gzipped and rotated every ``--rotate`` minutes. Reconnects after a
lost connection (console power-cycled, IP changed: re-run with the new host).

With ``--wake`` (the one optional write, two screen-page addresses): if the first 20 frames are all identical and flat
(every band the same value: the analyser is dormant — it starts only when the console has shown its RTA page once,
meters.md 2026-09-25 item 1) the script shows the console's METERS/RTA page for a second and restores the screen.

    .venv/Scripts/python scripts/log_rta_session.py 192.168.1.141 --hours 4 --out docs/research/data/programme_molecules_2026-09-25
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
import time

from x32mcp.connection import X32Connection
from x32mcp.descriptor import Descriptor
from x32mcp.events import EventBus
from x32mcp.meters import RTA_METER_TYPE, LiveMeters

PREFS = ["/-prefs/rta/source", "/-prefs/rta/pos", "/-prefs/rta/det", "/-prefs/rta/decay", "/-prefs/rta/gain", "/-prefs/rta/autogain",
         "/-prefs/rta/peakhold", "/-stat/rtasource", "/-stat/screen/screen", "/-stat/screen/METER/page", "/-stat/selidx"]
SCREEN, MPAGE = "/-stat/screen/screen", "/-stat/screen/METER/page"


async def read_prefs(conn: X32Connection, d: Descriptor) -> dict:
    out = {}
    for a in PREFS:
        try:
            raw = await asyncio.wait_for(conn.get(a), 2.0)
            spec = d.param_for_address(a)
            out[a.rsplit("/", 1)[-1] if a.startswith("/-prefs") else a] = {"raw": raw, "value": spec[0].to_value(raw) if spec else None}
        except Exception as e:  # noqa: BLE001 - a missing node must not stop the log
            out[a] = {"error": type(e).__name__}
    return out


def dormant(frames: list) -> bool:
    if len(frames) < 20:
        return False
    first = frames[0].values
    return all(f.values == first for f in frames[1:20]) and max(first) - min(first) < 1e-6


async def wake(conn: X32Connection) -> dict:
    before = {SCREEN: await conn.get(SCREEN), MPAGE: await conn.get(MPAGE)}
    await conn.set(SCREEN, 1)
    await conn.set(MPAGE, 4)
    await asyncio.sleep(1.0)
    for a, v in before.items():
        await conn.set(a, v)
    return before


async def run(a) -> int:
    d = Descriptor.load()
    t_end = time.time() + a.hours * 3600.0
    part = 0
    total = 0
    while time.time() < t_end:
        conn = X32Connection(d, EventBus())
        try:
            info = await conn.connect(a.host, a.port)
        except Exception as e:  # noqa: BLE001
            print(f"connect failed ({type(e).__name__}: {e}); retry in 10 s", file=sys.stderr)
            await asyncio.sleep(10.0)
            continue
        print(f"connected to {info.name} FW {info.firmware}", file=sys.stderr)
        frames: list = []
        events: list[dict] = []
        lm = LiveMeters(conn, RTA_METER_TYPE)
        lm.subscribe(lambda fr: frames.append(fr) if fr.is_rta else None)
        await lm.start()
        try:
            prefs = await read_prefs(conn, d)
            events.append({"ts": time.time(), "prefs": prefs, "console": {"name": info.name, "firmware": info.firmware, "host": a.host}})
            print("prefs:", {k: v.get("value", v) for k, v in prefs.items()}, file=sys.stderr)
            await asyncio.sleep(1.5)
            if a.wake and dormant(frames):
                before = await wake(conn)
                events.append({"ts": time.time(), "wake": {"restored": before}})
                print("analyser was dormant: METERS/RTA page shown for 1 s and the screen restored", file=sys.stderr)
            elif dormant(frames):
                print("WARNING: the stream is a static flat floor (analyser dormant); re-run with --wake or show the RTA page on the console", file=sys.stderr)
            t_rot = time.time()
            t_prefs = time.time()
            last_rx = time.time()
            while time.time() < t_end:
                await asyncio.sleep(1.0)
                if frames:
                    last_rx = time.time()
                if time.time() - last_rx > a.stall:
                    print(f"no RTA frames for {a.stall:.0f} s: reconnecting", file=sys.stderr)
                    break
                if time.time() - t_prefs >= a.prefs_every:
                    t_prefs = time.time()
                    events.append({"ts": t_prefs, "prefs": await read_prefs(conn, d)})
                if time.time() - t_rot >= a.rotate * 60.0 or time.time() >= t_end:
                    part += 1
                    n = flush(a.out, part, frames, events)
                    total += n
                    print(f"{time.strftime('%H:%M:%S')} part {part}: {n} frames written ({total} total)", file=sys.stderr)
                    frames.clear()
                    events.clear()
                    t_rot = time.time()
        finally:
            if frames or events:
                part += 1
                total += flush(a.out, part, frames, events)
            await lm.stop()
            await conn.close()
    print(f"done: {total} frames in {part} part(s)", file=sys.stderr)
    return 0


def flush(prefix: str, part: int, frames: list, events: list[dict]) -> int:
    rows = [{"ts": e["ts"], **{k: v for k, v in e.items() if k != "ts"}} for e in events]
    rows += [{"ts": round(f.ts, 4), "db": [round(v, 4) for v in f.values]} for f in frames]
    rows.sort(key=lambda r: r["ts"])
    path = f"{prefix}_part{part:02d}.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return len(frames)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=10023)
    ap.add_argument("--hours", type=float, default=4.0)
    ap.add_argument("--out", default="rta_session", help="output prefix: <prefix>_partNN.jsonl.gz")
    ap.add_argument("--rotate", type=float, default=30.0, help="minutes per part file")
    ap.add_argument("--prefs-every", type=float, default=10.0, help="seconds between pref re-reads")
    ap.add_argument("--stall", type=float, default=15.0, help="seconds without frames before reconnecting")
    ap.add_argument("--wake", action="store_true", help="show the console's RTA page once if the analyser is dormant (two screen writes)")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
