"""Oscillator tone into Main L+R, RTA Main post under PEAK/0.25: log N seconds, print the bands around the tone, restore.
Usage: tone_spectrum.py HOST HZ LEVEL_DB SECONDS LABEL OUT.jsonl"""
import asyncio, json, math, statistics as st, sys, time
from x32mcp.connection import X32Connection
from x32mcp.descriptor import Descriptor
from x32mcp.events import EventBus
from x32mcp.meters import RTA_METER_TYPE, LiveMeters

P = {"source": "/-prefs/rta/source", "pos": "/-prefs/rta/pos", "det": "/-prefs/rta/det", "decay": "/-prefs/rta/decay",
     "osc_on": "/-stat/osc/on", "f1": "/config/osc/f1", "level": "/config/osc/level", "dest": "/config/osc/dest", "type": "/config/osc/type"}
GRID = [20.0 * 2 ** (i / 10) for i in range(100)]


async def main(host, hz, level_db, seconds, label, out):
    d = Descriptor.load(); conn = X32Connection(d, EventBus()); await conn.connect(host, 10023)
    dspec = d.param_for_address(P["decay"])[0]; lvl = d.scale("send")
    before = {k: await asyncio.wait_for(conn.get(a), 2.0) for k, a in P.items()}
    i = round(120 * math.log(hz / 20.0) / math.log(1000.0)); real = 20.0 * 1000.0 ** (i / 120)
    await conn.set(P["source"], 72); await conn.set(P["pos"], 1); await conn.set(P["det"], 0); await conn.set(P["decay"], dspec.to_raw(0.25))
    await conn.set(P["type"], 0); await conn.set(P["dest"], 18); await conn.set(P["level"], lvl.to_raw(level_db)); await conn.set(P["f1"], i / 120)
    await asyncio.sleep(0.5)
    await conn.set(P["osc_on"], 1)
    frames = []
    lm = LiveMeters(conn, RTA_METER_TYPE); lm.subscribe(lambda fr: frames.append(fr) if fr.is_rta else None); await lm.start()
    await asyncio.sleep(seconds); await lm.stop()
    await conn.set(P["osc_on"], 0)
    for k, a in P.items():
        if k != "osc_on":
            await conn.set(a, before[k])
    await asyncio.sleep(0.3)
    print(f"{label}: tone {real:.1f} Hz at {level_db:g} dB; restored {{k: await asyncio.wait_for(conn.get(a), 2.0) for k, a in P.items()}}", file=sys.stderr)
    await conn.close()
    s = frames[10:]
    med = [st.median(f.values[b] for f in s) for b in range(100)]
    pk = med.index(max(med))
    print(f"{label}: {len(frames)} frames; peak band {pk} ({GRID[pk]:.0f} Hz) median {med[pk]:.2f} dB")
    print("  band:  " + " ".join(f"{b:6d}" for b in range(pk - 4, pk + 5)))
    print("  Hz:    " + " ".join(f"{GRID[b]:6.0f}" for b in range(pk - 4, pk + 5)))
    print("  median " + " ".join(f"{med[b]:6.1f}" for b in range(pk - 4, pk + 5)))
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"meta": {"label": label, "hz": real, "level_db": level_db, "started": time.time() - seconds}}) + "\n")
        for f in frames:
            fh.write(json.dumps({"ts": round(f.ts, 4), "db": [round(v, 4) for v in f.values]}) + "\n")
        fh.write(json.dumps({"median": [round(v, 2) for v in med]}) + "\n")

asyncio.run(main(sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), sys.argv[5], sys.argv[6]))
