"""Point the RTA at a bus (post) under PEAK / 0.25, log N seconds, print the median spectrum around 1 kHz, restore prefs.
Usage: log_bus_rta.py HOST BUS SECONDS LABEL OUT.jsonl   (writes /-prefs/rta/source,pos,det,decay; restores all four)"""
import asyncio, json, statistics as st, sys, time
from x32mcp.connection import X32Connection
from x32mcp.descriptor import Descriptor
from x32mcp.events import EventBus
from x32mcp.meters import RTA_METER_TYPE, LiveMeters

P = {"source": "/-prefs/rta/source", "pos": "/-prefs/rta/pos", "det": "/-prefs/rta/det", "decay": "/-prefs/rta/decay"}
GRID = [20.0 * 2 ** (i / 10) for i in range(100)]


async def main(host, bus, seconds, label, out):
    d = Descriptor.load(); conn = X32Connection(d, EventBus()); await conn.connect(host, 10023)
    dspec = d.param_for_address(P["decay"])[0]
    before = {k: await asyncio.wait_for(conn.get(a), 2.0) for k, a in P.items()}
    src = 50 + bus - 1                              # enum rta_source: 50-65 = Bus 1-16
    await conn.set(P["source"], src); await conn.set(P["pos"], 1); await conn.set(P["det"], 0); await conn.set(P["decay"], dspec.to_raw(0.25))
    await asyncio.sleep(0.6)
    now = {k: await asyncio.wait_for(conn.get(a), 2.0) for k, a in P.items()}
    stat = await asyncio.wait_for(conn.get("/-stat/rtasource"), 2.0)
    print(f"{label}: prefs before {before}; now {now} (source {src} = BUS{bus:02d}, expect rtasource {src + 98 - 2}); /-stat/rtasource {stat}", file=sys.stderr)
    frames = []
    lm = LiveMeters(conn, RTA_METER_TYPE); lm.subscribe(lambda fr: frames.append(fr) if fr.is_rta else None); await lm.start()
    await asyncio.sleep(seconds); await lm.stop()
    for k, a in P.items():
        await conn.set(a, before[k])
    await asyncio.sleep(0.3)
    print("restored:", {k: await asyncio.wait_for(conn.get(a), 2.0) for k, a in P.items()}, file=sys.stderr)
    await conn.close()
    med = [st.median(f.values[b] for f in frames) for b in range(100)]
    mx = [max(f.values[b] for f in frames) for b in range(100)]
    print(f"{label}: {len(frames)} frames; overall median max {max(med):.1f} dB at band {med.index(max(med))} ({GRID[med.index(max(med))]:.0f} Hz)")
    print("  band:  " + " ".join(f"{b:6d}" for b in range(50, 64)))
    print("  Hz:    " + " ".join(f"{GRID[b]:6.0f}" for b in range(50, 64)))
    print("  median " + " ".join(f"{med[b]:6.1f}" for b in range(50, 64)))
    print("  max    " + " ".join(f"{mx[b]:6.1f}" for b in range(50, 64)))
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"meta": {"label": label, "bus": bus, "prefs": now, "rtasource": stat, "started": time.time() - seconds}}) + "\n")
        for f in frames:
            fh.write(json.dumps({"ts": round(f.ts, 4), "db": [round(v, 4) for v in f.values]}) + "\n")
        fh.write(json.dumps({"median": [round(v, 2) for v in med], "max": [round(v, 2) for v in mx]}) + "\n")

asyncio.run(main(sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), sys.argv[4], sys.argv[5]))
