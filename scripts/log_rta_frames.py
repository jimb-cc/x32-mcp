#!/usr/bin/env python
"""Log every /meters/15 (RTA) frame from the desk to JSONL, with local receive timestamps.

Read-only: it subscribes to the meter stream on its own socket and writes nothing to the desk (the
RTA source / prefs are whatever the console has; point the RTA first with get_rta(target)).
One line per frame: {"ts": <time.time()>, "k": <frame index>, "db": [100 floats]}. Use it for the
analyser rise-time / release measurements (gate the console oscillator on and off while it runs)
and for anything else that needs frame-accurate timing rather than get_rta's averages.

    .venv/Scripts/python scripts/log_rta_frames.py 192.168.1.139 --seconds 120 --out rta_frames.jsonl

Ctrl-C stops it early; the desk-side meter lease lapses by itself.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from x32mcp.connection import X32Connection
from x32mcp.descriptor import Descriptor
from x32mcp.events import EventBus
from x32mcp.meters import RTA_METER_TYPE, LiveMeters


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=10023)
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--out", default="rta_frames.jsonl")
    a = ap.parse_args()

    conn = X32Connection(Descriptor.load(), EventBus())
    info = await conn.connect(a.host, a.port)
    print(f"connected to {info.name} ({info.model} FW {info.firmware}); logging {a.seconds:.0f} s to {a.out}", file=sys.stderr)
    n = 0
    t0 = time.time()
    with open(a.out, "w", encoding="utf-8") as fh:
        def on_frame(fr) -> None:
            nonlocal n
            if not fr.is_rta:
                return
            fh.write(json.dumps({"ts": round(fr.ts, 4), "k": n, "db": [round(v, 1) for v in fr.values]}) + "\n")
            n += 1
            if n % 100 == 0:
                fh.flush()
                peak = max(range(len(fr.values)), key=lambda i: fr.values[i])
                print(f"  {n} frames, {time.time() - t0:5.1f} s, loudest band {peak} at {fr.values[peak]:.1f} dB", file=sys.stderr)

        lm = LiveMeters(conn, RTA_METER_TYPE)
        lm.subscribe(on_frame)
        await lm.start()
        try:
            await asyncio.sleep(a.seconds)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await lm.stop()
            await conn.close()
    dt = time.time() - t0
    print(f"done: {n} frames in {dt:.1f} s ({n / dt if dt else 0:.1f}/s) -> {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
