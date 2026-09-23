#!/usr/bin/env python
"""Sweep the console oscillator in semitone steps and record the RTA's settled spectrum and rise time per tone.

Per tone: oscillator off, ``/config/osc/f1`` set, oscillator on for ``--on`` seconds (``--on-lf`` below 200 Hz) while every
``/meters/15`` frame is logged; the settled spectrum is the mean of the last 8 frames, the rise time the first frame within
1 dB of it. From one pass: the real band centres (which band a tone of known frequency lands in, and the neighbour
ratios), the skirt shape and asymmetry per band, and the per-band attack curve behind the detector's settle rule.

Writes ``/config/osc/f1``, ``/-stat/osc/on`` and ``/-prefs/rta/det`` (PEAK during the run, restored) — outside the policy
layer (raw connection, like measure_settle.py). Oscillator level/type/destination are left as the console has them
(set them first: sine, -40 dB, Main L+R); the RTA source too (get_rta("main.st")). Oscillator OFF at the end.

    .venv/Scripts/python scripts/measure_rta_bands.py 192.168.1.139 --from 40 --to 10000 --out bands.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time

from x32mcp.connection import X32Connection
from x32mcp.descriptor import Descriptor
from x32mcp.events import EventBus
from x32mcp.meters import RTA_METER_TYPE, LiveMeters

OSC_F1 = "/config/osc/f1"
OSC_ON = "/-stat/osc/on"
OSC_DEST = "/config/osc/dest"
OSC_LEVEL = "/config/osc/level"
RTA_DET = "/-prefs/rta/det"
F_LO, F_HI, F_STEPS = 20.0, 20000.0, 121          # logf [20, 20000, 121]: semitones
BAND_HZ = [20.0 * 2 ** (i / 10) for i in range(100)]


def f1_raw(i: int) -> float:
    return i / (F_STEPS - 1)


def f1_hz(i: int) -> float:
    return F_LO * (F_HI / F_LO) ** (i / (F_STEPS - 1))


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=10023)
    ap.add_argument("--from", dest="f_from", type=float, default=40.0)
    ap.add_argument("--to", dest="f_to", type=float, default=10000.0)
    ap.add_argument("--step", type=int, default=1, help="oscillator grid steps per tone (1 = semitone)")
    ap.add_argument("--on", type=float, default=2.0)
    ap.add_argument("--on-lf", type=float, default=3.0, help="on-time below 200 Hz")
    ap.add_argument("--gap", type=float, default=0.4, help="seconds off between tones")
    ap.add_argument("--out", default="rta_bands.jsonl")
    a = ap.parse_args()

    d = Descriptor.load()
    conn = X32Connection(d, EventBus())
    info = await conn.connect(a.host, a.port)
    print(f"connected to {info.name} (FW {info.firmware})", file=sys.stderr)
    frames: list[dict] = []
    cur = {"i": None, "on": 0}

    def on_frame(fr) -> None:
        if fr.is_rta:
            frames.append({"ts": fr.ts, "i": cur["i"], "on": cur["on"], "db": [round(v, 1) for v in fr.values]})

    lm = LiveMeters(conn, RTA_METER_TYPE)
    lm.subscribe(on_frame)
    await lm.start()
    det_before = await conn.get(RTA_DET)
    dest = await conn.get(OSC_DEST)
    level = await conn.get(OSC_LEVEL)
    print(f"osc dest raw {dest}, level raw {level}; det before {det_before}", file=sys.stderr)
    i_from = max(0, math.ceil((F_STEPS - 1) * math.log(a.f_from / F_LO) / math.log(F_HI / F_LO)))
    i_to = min(F_STEPS - 1, math.floor((F_STEPS - 1) * math.log(a.f_to / F_LO) / math.log(F_HI / F_LO)))
    steps = list(range(i_from, i_to + 1, a.step))
    print(f"{len(steps)} tones from {f1_hz(i_from):.1f} to {f1_hz(i_to):.1f} Hz", file=sys.stderr)
    results = []
    t0 = time.time()
    try:
        await conn.set(RTA_DET, 1)          # PEAK: -128 floor, full skirts
        await conn.set(OSC_ON, 0)
        await asyncio.sleep(1.0)
        for n, i in enumerate(steps):
            hz = f1_hz(i)
            await conn.set(OSC_F1, f1_raw(i))
            cur["i"] = i
            await asyncio.sleep(a.gap)
            k_on = len(frames)
            cur["on"] = 1
            await conn.set(OSC_ON, 1)
            await asyncio.sleep(a.on_lf if hz < 200.0 else a.on)
            k_off = len(frames)
            cur["on"] = 0
            await conn.set(OSC_ON, 0)
            seg = [f["db"] for f in frames[k_on:k_off]]
            if len(seg) < 10:
                continue
            settled = [round(sum(s[b] for s in seg[-8:]) / 8.0, 1) for b in range(100)]
            peak = max(range(100), key=lambda b: settled[b])
            lv = [s[peak] for s in seg]
            rise1 = next((j for j, v in enumerate(lv) if v >= settled[peak] - 1.0), None)
            rise3 = next((j for j, v in enumerate(lv) if v >= settled[peak] - 3.0), None)
            first = next((j for j, v in enumerate(lv) if v > -120.0), None)
            row = {"i": i, "hz": round(hz, 2), "peak_band": peak, "peak_nominal_hz": round(BAND_HZ[peak], 1),
                   "offset_oct": round(math.log2(hz / BAND_HZ[peak]), 3), "peak_db": settled[peak],
                   "rel_m2": round(settled[peak - 2] - settled[peak], 1) if peak >= 2 else None,
                   "rel_m1": round(settled[peak - 1] - settled[peak], 1) if peak >= 1 else None,
                   "rel_p1": round(settled[peak + 1] - settled[peak], 1) if peak <= 98 else None,
                   "rel_p2": round(settled[peak + 2] - settled[peak], 1) if peak <= 97 else None,
                   "rise_frames_to_-3": None if (rise3 is None or first is None) else rise3 - first,
                   "rise_frames_to_-1": None if (rise1 is None or first is None) else rise1 - first,
                   "rise": [round(v, 1) for v in lv[first:first + 10]] if first is not None else [],
                   "settled": settled}
            results.append(row)
            print(f"  [{n + 1:3d}/{len(steps)}] {hz:8.1f} Hz -> band {peak:2d} ({BAND_HZ[peak]:7.1f} nominal, {row['offset_oct']:+.3f} oct) "
                  f"{settled[peak]:6.1f} dB  -1:{row['rel_m1']} +1:{row['rel_p1']}  rise -3/-1 dB: {row['rise_frames_to_-3']}/{row['rise_frames_to_-1']} fr",
                  file=sys.stderr)
    finally:
        await conn.set(OSC_ON, 0)
        await conn.set(RTA_DET, det_before)
        await asyncio.sleep(0.3)
        print(f"restored: osc OFF, det -> {await conn.get(RTA_DET)}; {time.time() - t0:.0f} s", file=sys.stderr)
        await lm.stop()
        await conn.close()
    with open(a.out, "w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r) + "\n")
    with open(a.out.replace(".jsonl", "_frames.jsonl"), "w", encoding="utf-8") as fh:
        for f in frames:
            fh.write(json.dumps(f) + "\n")
    print(f"written {a.out} ({len(results)} tones) and the frame log", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
