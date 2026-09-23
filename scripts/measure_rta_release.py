#!/usr/bin/env python
"""Measure the RTA display release (and attack) per /-prefs/rta/decay setting with the console oscillator.

For each decay value it sets `/-prefs/rta/decay`, gates the oscillator on with `/-stat/osc/on` for ``--on`` seconds,
gates it off and logs `/meters/15` frames for ``--off`` seconds, then reports the fall rate (dB/frame and dB/s over the
first 10 frames after the peak) and the time to reach the floor. The oscillator's frequency/level/destination are
whatever the console has (set them first: e.g. sine, 2 kHz, -40 dB, Main L+R); the RTA source too (get_rta(target)).

Writes exactly two addresses, outside the policy layer (raw connection, like measure_settle.py): the RTA decay pref and
the oscillator on/off. Both are restored at the end (decay to ``--restore-decay`` seconds, oscillator OFF).

    .venv/Scripts/python scripts/measure_rta_release.py 192.168.1.139 --decays 0.25,1,4,16 --out release.jsonl
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

DECAY = "/-prefs/rta/decay"
OSC_ON = "/-stat/osc/on"


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=10023)
    ap.add_argument("--decays", default="0.25,1,4,16", help="seconds, comma-separated")
    ap.add_argument("--on", type=float, default=4.0, help="seconds the tone is on per step")
    ap.add_argument("--off", type=float, default=8.0, help="seconds logged after gating off (16 s decay gets +6)")
    ap.add_argument("--restore-decay", type=float, default=1.0)
    ap.add_argument("--out", default="rta_release.jsonl")
    a = ap.parse_args()

    d = Descriptor.load()
    spec = d.param_for_address(DECAY)
    if spec is None:
        print(f"descriptor has no param for {DECAY}", file=sys.stderr)
        return 2
    to_raw, to_value = spec[0].to_raw, spec[0].to_value
    conn = X32Connection(d, EventBus())
    info = await conn.connect(a.host, a.port)
    print(f"connected to {info.name} (FW {info.firmware})", file=sys.stderr)
    frames: list[dict] = []
    marker = {"decay_s": None, "osc": None}

    def on_frame(fr) -> None:
        if fr.is_rta:
            frames.append({"ts": round(fr.ts, 4), "decay_s": marker["decay_s"], "osc": marker["osc"], "db": [round(v, 1) for v in fr.values]})

    lm = LiveMeters(conn, RTA_METER_TYPE)
    lm.subscribe(on_frame)
    await lm.start()
    decay_before = await conn.get(DECAY)
    osc_before = await conn.get(OSC_ON)
    print(f"before: decay raw {decay_before} (= {to_value(decay_before)} s), osc on = {osc_before}", file=sys.stderr)
    results = []
    try:
        await conn.set(OSC_ON, 0)
        await asyncio.sleep(1.0)
        for dec in [float(x) for x in a.decays.split(",")]:
            raw = to_raw(dec)
            await conn.set(DECAY, raw)
            await asyncio.sleep(0.5)
            got = await conn.get(DECAY)
            marker["decay_s"] = float(to_value(got))
            print(f"decay -> {dec} s (raw {raw:.4f}, desk reads {to_value(got)} s)", file=sys.stderr)
            marker["osc"] = 1
            await conn.set(OSC_ON, 1)
            await asyncio.sleep(a.on)
            k_off = len(frames)
            marker["osc"] = 0
            await conn.set(OSC_ON, 0)
            t_off = time.time()
            await asyncio.sleep(a.off + (6.0 if dec >= 16 else 0.0))
            # analyse this step: the tone band = loudest band in the last on-frame
            on_fr = frames[k_off - 1]["db"]
            band = max(range(100), key=lambda i: on_fr[i])
            peak = on_fr[band]
            fall = [f["db"][band] for f in frames[k_off:]]
            floor = min(fall) if fall else None
            # first frame below peak-1 dB starts the fall; slope over the next 10 frames
            i0 = next((i for i, v in enumerate(fall) if v < peak - 1.0), None)
            slope = None
            t_floor = None
            if i0 is not None:
                seg = fall[i0:i0 + 11]
                if len(seg) >= 2:
                    slope = (seg[0] - seg[-1]) / (len(seg) - 1)
                j = next((i for i, v in enumerate(fall) if floor is not None and v <= floor + 0.5), None)
                t_floor = None if j is None else round((j - i0) * 0.05, 2)
            attack = [round(f["db"][band], 1) for f in frames[max(0, k_off - int(a.on / 0.05)):k_off][:8]]
            results.append({"decay_s": marker["decay_s"], "band": band, "peak_db": peak, "floor_db": floor,
                            "fall_db_per_frame": None if slope is None else round(slope, 2),
                            "fall_db_per_s": None if slope is None else round(slope * 20.0, 1),
                            "s_to_floor": t_floor, "first_frames_after_off": [round(v, 1) for v in fall[:16]], "attack": attack})
            print(f"  band {band} peak {peak} -> floor {floor}: {results[-1]['fall_db_per_frame']} dB/frame = "
                  f"{results[-1]['fall_db_per_s']} dB/s, floor after {t_floor} s; fall: {results[-1]['first_frames_after_off']}", file=sys.stderr)
    finally:
        await conn.set(OSC_ON, 0)
        await conn.set(DECAY, to_raw(a.restore_decay))
        await asyncio.sleep(0.3)
        print(f"restored: osc OFF, decay -> {to_value(await conn.get(DECAY))} s", file=sys.stderr)
        await lm.stop()
        await conn.close()
    with open(a.out, "w", encoding="utf-8") as fh:
        for f in frames:
            fh.write(json.dumps(f) + "\n")
    print(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
