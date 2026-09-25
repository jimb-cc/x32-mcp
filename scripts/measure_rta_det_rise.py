"""RTA detector (RMS/PEAK by RAW value), det-switch transient, frozen-line and LF rise checks with the console oscillator into Main L+R.

Written for the 2026-09-25 studio session (docs/research/meters.md, Verification log 2026-09-25 items 4-8); raw OSC.

Writes only RTA prefs and the oscillator (det, decay, osc on/f1/level/dest/type). Everything found is saved to
--state FILE at the start and restored at the end (or with --restore FILE alone).

  A. det write read-back and the det-switch transient: tone on, RMS 3 s -> PEAK 3 s -> RMS 3 s at decay 0.25; every
     switch read back; the frame log shows the floor (-97 RMS / -128 PEAK) and any transient on the peak band.
  B. frozen-line check: during the PEAK plateau, longest run of bit-identical values on the peak band and on a skirt band.
  C. LF rise: 40 / 63 / 100 Hz gated on for 4 s under PEAK/0.25 and RMS/0.25; per-frame rise on the peak band.
"""
import argparse, asyncio, json, math, sys, time
from x32mcp.connection import X32Connection
from x32mcp.descriptor import Descriptor
from x32mcp.events import EventBus
from x32mcp.meters import RTA_METER_TYPE, LiveMeters

P = {"det": "/-prefs/rta/det", "decay": "/-prefs/rta/decay", "gain": "/-prefs/rta/gain", "autogain": "/-prefs/rta/autogain",
     "peakhold": "/-prefs/rta/peakhold", "source": "/-prefs/rta/source", "pos": "/-prefs/rta/pos",
     "osc_on": "/-stat/osc/on", "f1": "/config/osc/f1", "level": "/config/osc/level", "dest": "/config/osc/dest",
     "type": "/config/osc/type", "fsel": "/config/osc/fsel"}
F_LO, F_HI, F_STEPS = 20.0, 20000.0, 121


def f1_raw(hz: float) -> tuple[float, float]:
    i = round((F_STEPS - 1) * math.log(hz / F_LO) / math.log(F_HI / F_LO))
    return i / (F_STEPS - 1), F_LO * (F_HI / F_LO) ** (i / (F_STEPS - 1))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--state", default="desk_state_before.json")
    ap.add_argument("--restore", default=None, help="restore this saved state and exit")
    ap.add_argument("--out", default="desk_checks.jsonl")
    ap.add_argument("--keep-osc", action="store_true", help="leave the oscillator at sine/-40/L+R (off) for a following sweep")
    ap.add_argument("--level-db", type=float, default=-40.0, help="oscillator level in dB (-90..10)")
    a = ap.parse_args()
    d = Descriptor.load()
    dspec = d.param_for_address(P["decay"])[0]
    conn = X32Connection(d, EventBus())
    info = await conn.connect(a.host, 10023)
    print(f"connected to {info.name} FW {info.firmware}", file=sys.stderr)

    async def get(k):
        return await asyncio.wait_for(conn.get(P[k]), 2.0)

    async def setp(k, raw):
        await conn.set(P[k], raw)

    if a.restore:
        st = json.load(open(a.restore, encoding="utf-8"))
        await setp("osc_on", 0)
        for k, v in st.items():
            if k != "osc_on":
                await setp(k, v)
        await asyncio.sleep(0.4)
        print("restored:", {k: await get(k) for k in st}, file=sys.stderr)
        await conn.close()
        return 0

    before = {k: await get(k) for k in P}
    json.dump(before, open(a.state, "w", encoding="utf-8"))
    print("saved state:", before, file=sys.stderr)
    frames: list[dict] = []
    mark = {"phase": None, "det": None, "decay": None, "hz": None, "osc": 0}

    def on_frame(fr):
        if fr.is_rta:
            frames.append({"ts": round(fr.ts, 4), **mark, "db": [round(v, 4) for v in fr.values]})

    lm = LiveMeters(conn, RTA_METER_TYPE)
    lm.subscribe(on_frame)
    await lm.start()
    results: dict = {"before": before}

    async def set_det(want: int) -> int:
        await setp("det", want)
        await asyncio.sleep(0.4)
        got = await get("det")
        mark["det"] = got
        return got

    async def set_decay(want: float) -> float:
        await setp("decay", dspec.to_raw(want))
        await asyncio.sleep(0.4)
        got = float(dspec.to_value(await get("decay")))
        mark["decay"] = got
        return got

    async def tone(hz: float, on: bool):
        if on:
            raw, real = f1_raw(hz)
            await setp("f1", raw)
            mark["hz"] = round(real, 2)
            await asyncio.sleep(0.2)
        mark["osc"] = 1 if on else 0
        await setp("osc_on", 1 if on else 0)

    def seg(phase):
        return [f for f in frames if f["phase"] == phase]

    try:
        await setp("osc_on", 0)
        await setp("type", 0)                       # SINE
        await setp("level", d.scale("send").to_raw(a.level_db))   # /config/osc/level follows the FADER law (scales_params.md S10), not [-90..10] linear
        await setp("dest", 18)                      # L+R
        await asyncio.sleep(0.3)
        lvl, dest, typ = await get("level"), await get("dest"), await get("type")
        print(f"oscillator: level raw {lvl} ({a.level_db:g} dB expected {d.scale('send').to_raw(a.level_db):.4f}), dest {dest} (18 = L+R), type {typ} (0 = SINE)", file=sys.stderr)
        dec = await set_decay(0.25)
        print(f"decay -> 0.25 read back {dec:.3g}", file=sys.stderr)

        # ---- A: det read-back + switch transient, tone 2 kHz on throughout
        rb = {}
        rb["det1_a"] = await set_det(1)
        mark["phase"] = "A_det1_a"
        await tone(2000.0, True)
        await asyncio.sleep(3.0)
        mark["phase"] = "A_det0"
        rb["det0"] = await set_det(0)
        await asyncio.sleep(3.0)
        mark["phase"] = "A_det1_b"
        rb["det1_b"] = await set_det(1)
        await asyncio.sleep(3.0)
        await tone(2000.0, False)
        mark["phase"] = "A_off_det1"
        await asyncio.sleep(2.5)
        rb["det0_off"] = await set_det(0)
        mark["phase"] = "A_off_det0"
        await asyncio.sleep(2.5)
        print(f"A: det read-backs {rb} (raw 0 = PEAK, raw 1 = RMS: the desk's own /node label, 2026-09-25)", file=sys.stderr)
        results["A_readback"] = rb
        for ph in ("A_det1_a", "A_det0", "A_det1_b", "A_off_det1", "A_off_det0"):
            s = seg(ph)
            if len(s) < 4:
                continue
            pk = max(range(100), key=lambda b: s[-1]["db"][b])
            lv = [f["db"][pk] for f in s]
            floor = [min(f["db"]) for f in s]
            print(f"  {ph:11s}: {len(s):3d} frames, peak band {pk} {lv[0]:7.2f} .. {lv[-1]:7.2f} dB (max {max(lv):.2f}), "
                  f"floor {min(floor):.1f}..{max(floor):.1f}; first 8: {[round(v, 1) for v in lv[:8]]}", file=sys.stderr)
            results[ph] = {"frames": len(s), "peak_band": pk, "peak_first8": lv[:8], "peak_max": max(lv), "floor_min": min(floor), "floor_max": max(floor)}

        # ---- B: frozen-line check on the PEAK plateau (values are int16/256: a steady tone may repeat exactly)
        s = seg("A_det0")[10:]
        if len(s) >= 10:
            pk = max(range(100), key=lambda b: s[-1]["db"][b])
            for name, b in (("peak", pk), ("skirt-1", pk - 1), ("skirt+1", pk + 1), ("far", (pk + 30) % 100)):
                vals = [f["db"][b] for f in s]
                run, best = 1, 1
                for x, y in zip(vals, vals[1:]):
                    run = run + 1 if x == y else 1
                    best = max(best, run)
                distinct = len(set(vals))
                print(f"  B frozen check {name:8s} band {b:2d}: {len(vals)} frames, {distinct} distinct values, longest identical run {best}, "
                      f"spread {max(vals) - min(vals):.3f} dB", file=sys.stderr)
                results[f"B_{name}"] = {"band": b, "frames": len(vals), "distinct": distinct, "longest_run": best, "spread": round(max(vals) - min(vals), 4)}

        # ---- C: LF rise 40 / 63 / 100 Hz, PEAK then RMS, decay 0.25
        for det_want in (1, 0):
            got = await set_det(det_want)
            for hz in (40.0, 63.0, 100.0, 2000.0):
                ph = f"C_det{det_want}_{int(hz)}"
                mark["phase"] = ph
                await asyncio.sleep(1.5)          # tone off: let the band fall to the floor
                k0 = len(frames)
                await tone(hz, True)
                await asyncio.sleep(4.0)
                await tone(hz, False)
                s = frames[k0:]
                if len(s) < 10:
                    continue
                settled = [sum(f["db"][b] for f in s[-8:]) / 8.0 for b in range(100)]
                pk = max(range(100), key=lambda b: settled[b])
                lv = [f["db"][pk] for f in s]
                first = next((i for i, v in enumerate(lv) if v > -120.0 and v > lv[0] + 3.0), None)
                r3 = next((i for i, v in enumerate(lv) if v >= settled[pk] - 3.0), None)
                r1 = next((i for i, v in enumerate(lv) if v >= settled[pk] - 1.0), None)
                rise = [round(v, 1) for v in lv[first:first + 14]] if first is not None else []
                print(f"  {ph:12s} (det read {got}): band {pk} settled {settled[pk]:.1f} dB; frames to -3/-1 dB: "
                      f"{None if r3 is None or first is None else r3 - first}/{None if r1 is None or first is None else r1 - first}; rise {rise}", file=sys.stderr)
                results[ph] = {"det_read": got, "band": pk, "settled": round(settled[pk], 2), "to_m3": None if r3 is None or first is None else r3 - first,
                               "to_m1": None if r1 is None or first is None else r1 - first, "rise": rise,
                               "skirts": [round(settled[pk + k] - settled[pk], 1) if 0 <= pk + k < 100 else None for k in (-2, -1, 1, 2)]}
    finally:
        await setp("osc_on", 0)
        await lm.stop()
        if a.keep_osc:
            await setp("det", 1)
            await setp("decay", dspec.to_raw(0.25))
            print("leaving oscillator at sine/-40/L+R (OFF), det PEAK, decay 0.25 for the sweep; restore later with --restore", file=sys.stderr)
        else:
            for k, v in before.items():
                if k != "osc_on":
                    await setp(k, v)
        await asyncio.sleep(0.4)
        print("state now:", {k: await get(k) for k in P}, file=sys.stderr)
        await conn.close()
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"results": results}) + "\n")
        for f in frames:
            fh.write(json.dumps(f) + "\n")
    print(f"written {a.out} ({len(frames)} frames)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
