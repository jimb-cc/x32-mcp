"""Rise/fall times per RTA band from a gated-tone frame log (log_rta_frames.py JSONL).

For every band, find episodes where the level climbs from the floor to a plateau (tone gated on) and falls back
(gated off). Report per episode: band, centre Hz, plateau dB, frames from the first frame >= floor+6 dB to within
1 dB / 3 dB of the plateau, the per-frame increments of the rise, and the fall rate (dB/s) after gating off.
"""
import json, math, sys
from collections import defaultdict

path = sys.argv[1]
rows = [json.loads(l) for l in open(path, encoding="utf-8")]
ts = [r["ts"] for r in rows]
BAND_HZ = [20.0 * 2 ** (i / 10) for i in range(100)]
FLOOR = -97.0
n = len(rows)
print(f"{n} frames, {ts[-1]-ts[0]:.1f} s, mean dt {(ts[-1]-ts[0])/(n-1)*1000:.1f} ms")

episodes = []
for b in range(100):
    lv = [r["db"][b] for r in rows]
    k = 0
    while k < n:
        if lv[k] > FLOOR + 6.0 and (k == 0 or lv[k - 1] <= FLOOR + 6.0):
            # rise starts at k; find the end of the on-period (level back near floor)
            j = k
            while j < n and lv[j] > FLOOR + 6.0:
                j += 1
            seg = lv[k:j]
            if len(seg) < 6:
                k = j
                continue
            plateau = max(seg)
            if plateau < -70.0:          # ignore skirt leakage from a neighbouring band's tone
                k = j
                continue
            within3 = next(i for i, v in enumerate(seg) if v >= plateau - 3.0)
            within1 = next(i for i, v in enumerate(seg) if v >= plateau - 1.0)
            incs = [round(seg[i + 1] - seg[i], 1) for i in range(min(12, len(seg) - 1))]
            # fall: frames after j until back within 6 dB of the floor
            fall = lv[j - 1: min(n, j + 40)]
            fall_rate = None
            if len(fall) > 3:
                drop = fall[0] - fall[min(len(fall) - 1, 10)]
                fall_rate = round(drop / (min(len(fall) - 1, 10) * 0.05), 1)
            episodes.append({"band": b, "hz": round(BAND_HZ[b]), "t_on": round(ts[k] - ts[0], 2), "plateau": plateau,
                             "frames_to_-3": within3, "frames_to_-1": within1, "rise_incs": incs, "on_frames": len(seg),
                             "fall_db_per_s": fall_rate})
            k = j
        else:
            k += 1

# keep the loudest band per on-episode (the tone's own band), drop neighbours
by_t = defaultdict(list)
for e in episodes:
    by_t[round(e["t_on"] / 2.0)].append(e)
print(f"{'t_on':>6} {'band':>4} {'Hz':>6} {'plateau':>8} {'->-3dB':>6} {'->-1dB':>6} {'on':>4} {'fall dB/s':>9}  rise increments (dB/frame)")
for key in sorted(by_t):
    e = max(by_t[key], key=lambda x: x["plateau"])
    print(f"{e['t_on']:6.2f} {e['band']:4d} {e['hz']:6d} {e['plateau']:8.1f} {e['frames_to_-3']:6d} {e['frames_to_-1']:6d} "
          f"{e['on_frames']:4d} {str(e['fall_db_per_s']):>9}  {e['rise_incs']}")
