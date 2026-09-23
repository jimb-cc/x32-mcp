#!/usr/bin/env python
"""Re-derive the 2026-09-23 desk measurements from the raw frame logs in docs/research/data/ (no desk needed).

Written for docs/REVIEW_RESPONSE_2026-09-24.md: every number quoted there is printed by this script. Pure stdlib.

    python scripts/reanalyse_rta_logs.py            # all sections
    python scripts/reanalyse_rta_logs.py grid geq   # some: prefs grid display lf geq floor
"""

from __future__ import annotations

import cmath
import gzip
import json
import math
import statistics as st
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "docs" / "research" / "data"
TABLE_F0 = 10000.0 * 2.0 ** -9          # the DOC p.19 table: 19.53125 Hz
FRAME_S = 0.05
REL_BW = 2.0 ** 0.05 - 2.0 ** -0.05     # 1/10-octave band: df = 0.0693 f


def load(name: str) -> list[dict]:
    with gzip.open(DATA / name, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def head(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 96 - len(title)))


# ------------------------------------------------------------------------------------------------ prefs in force
def prefs() -> None:
    head("1. Which prefs were in force during the semitone sweep? (item 8 says det PEAK, decay 0.25)")
    fr = load("rta_bands_semitone_sweep_frames_2026-09-23.jsonl.gz")
    vals = [v for f in fr for v in f["db"]]
    print(f"frames {len(fr)}, lowest value anywhere in the log: {min(vals):.1f} dB  (the log's other captures: -128 in one "
          f"detector state, -97 in the other)")
    falls = []
    prev = 0
    for k, f in enumerate(fr):
        if prev == 1 and f["on"] == 0:
            on = fr[k - 1]["db"]
            b = max(range(100), key=lambda i: on[i])
            seg = [fr[j]["db"][b] for j in range(k, min(len(fr), k + 9))]
            seg = [v for v in seg if v > -96.5]
            i0 = next((i for i, v in enumerate(seg) if v < on[b] - 1.0), None)
            if i0 is not None and len(seg) - i0 >= 4 and RTA20(b) >= 300.0:
                s = seg[i0:]
                falls.append((s[0] - s[-1]) / (len(s) - 1))
        prev = f["on"]
    print(f"release after each tone-off, tones >= 300 Hz: median {st.median(falls):.2f} dB/frame = {st.median(falls) / FRAME_S:.0f} dB/s "
          f"(n = {len(falls)}; decay 0.25 gives 4.0 dB/frame, decay 1.0 gives 1.0)")
    print("-> the sweep ran at decay 1.0 (measure_rta_release.py restores decay to its --restore-decay default, 1.0, and "
          "measure_rta_bands.py never sets decay), with the -97 floor.")


def RTA20(i: int) -> float:
    return 20.0 * 2.0 ** (i / 10.0)


# ------------------------------------------------------------------------------------------------ band grid
def grid() -> None:
    head("2. Band grid: where do the bins sit? (item 2 / PR #15)")
    rows = load("rta_bands_semitone_sweep_2026-09-23.jsonl.gz")
    pts = []
    for r in rows:
        if r["hz"] < 300.0:
            continue
        s, p = r["settled"], r["peak_band"]
        if min(s[p - 1], s[p + 1]) <= -96.5:
            continue
        x = 10.0 * math.log2(r["hz"] / TABLE_F0) - p      # tone position relative to the TABLE centre of its peak band
        pts.append((x, s[p + 1] - s[p - 1], r["hz"]))
    xs, ds = [p[0] for p in pts], [p[1] for p in pts]
    n = len(xs)
    mx, md = sum(xs) / n, sum(ds) / n
    b = sum((x - mx) * (d - md) for x, d in zip(xs, ds)) / sum((x - mx) ** 2 for x in xs)
    a = md - b * mx
    print(f"{n} tones >= 300 Hz; neighbour difference D = (+1) - (-1) regressed on position: zero at {-a / b:+.3f} band "
          f"= {-a / b / 10:+.4f} oct above the table centres")
    print(f"20*2^(i/10) sits {10 * math.log2(20.0 / TABLE_F0):+.3f} band above the table")
    for lo, hi in ((300, 1000), (1000, 3000), (3000, 10000)):
        sub = [p for p in pts if lo <= p[2] < hi]
        sx, sd = [p[0] for p in sub], [p[1] for p in sub]
        m1, m2 = sum(sx) / len(sx), sum(sd) / len(sd)
        bb = sum((x - m1) * (d - m2) for x, d in zip(sx, sd)) / sum((x - m1) ** 2 for x in sx)
        print(f"   {lo:5d}-{hi:<5d} Hz (n = {len(sub)}): zero at {-(m2 - bb * m1) / bb:+.3f} band")
    for name, f0 in (("DOC table 19.53*2^(i/10)", TABLE_F0), ("half a band up (item 5's provisional fit)", TABLE_F0 * 2 ** 0.05),
                     ("20*2^(i/10)", 20.0)):
        ok = sum(1 for r in rows if r["hz"] >= 300.0 and round(10.0 * math.log2(r["hz"] / f0)) == r["peak_band"])
        tot = sum(1 for r in rows if r["hz"] >= 300.0)
        print(f"   peak band == nearest centre under {name}: {ok}/{tot}")
    plat = [r["peak_db"] for r in rows if abs(10.0 * math.log2(r["hz"] / 20.0) - r["peak_band"]) < 0.12]
    print(f"on-centre tones (within 0.12 band of a 20*2^(i/10) centre): {len(plat)} tones, level {st.mean(plat):.2f} +- {st.pstdev(plat):.2f} dB")
    fl = sum(1 for r in rows if r["hz"] >= 320.0 and r["rel_m2"] is not None and r["peak_db"] + r["rel_m2"] <= -96.5)
    tot = sum(1 for r in rows if r["hz"] >= 320.0)
    print(f"+-2-band skirts: in {fl}/{tot} tones >= 320 Hz the -2 band reads the -97 floor itself: the quoted '-55..-59' is the floor, not the skirt")


# ------------------------------------------------------------------------------------------------ display law
def display() -> None:
    head("3. The display law: one pole on POWER, T20 = decay, attack and release alike (item 3)")
    fr = load("rta_release_decay_sweep_2026-09-23.jsonl.gz")
    band = 67
    segs: dict[float, list] = {}
    for f in fr:
        if f["decay_s"] is not None:
            segs.setdefault(round(f["decay_s"], 2), []).append((f["osc"], f["db"][band]))
    plateau = -41.3
    print("decay   tau = decay/ln(100)   release measured   one-pole predicts   attack rms error (frames 3..12)")
    for dec, rows in segs.items():
        tau = dec / math.log(100.0)
        on = [v for o, v in rows if o == 1]
        i0 = next(i for i, v in enumerate(on) if v > -96.0)
        att = on[i0:i0 + 12]
        best = None
        for p in range(100):
            model = [plateau + 10.0 * math.log10(max(1e-12, 1.0 - math.exp(-((k + 1 - p / 100) * FRAME_S) / tau))) for k in range(len(att))]
            err = math.sqrt(sum((x - m) ** 2 for x, m in zip(att[2:], model[2:])) / len(att[2:]))
            if best is None or err < best[0]:
                best = (err, p / 100)
        off = [v for o, v in rows if o == 0]
        j0 = next(i for i, v in enumerate(off) if v < off[0] - 0.5)
        rel = [v for v in off[j0:j0 + 12] if v > -96.5]
        slope = (rel[0] - rel[-1]) / (len(rel) - 1)
        print(f"{dec:5.2f}   {tau * 1000:8.1f} ms          {slope:5.2f} dB/frame     {10 * math.log10(math.e) * FRAME_S / tau:5.2f} dB/frame"
              f"      {best[0]:.2f} dB (onset {best[1]:.2f} frame into its frame)")
    print("(the tone is 2.12 kHz, band 67 of the measured grid, 3.6 dB down through the GEQ 2k slider at -12 on one leg)")


# ------------------------------------------------------------------------------------------------ LF attack shape
def lf() -> None:
    head("4. LF attack: a window filling, not a one-pole (item 4: 'attack_k 1.0')")
    fr = load("rta_rise_gated_tones_2026-09-23.jsonl.gz")
    eps = {}
    for band in (20, 30, 56, 65):
        lv = [f["db"][band] for f in fr]
        k = 1
        while k < len(lv):
            if lv[k - 1] <= -96.5 and lv[k] > -96.5 and max(lv[k:k + 12]) > -45.0:
                plateau = st.median(lv[k + 20:k + 60])
                eps.setdefault(band, []).append([round(v - plateau, 1) for v in lv[k:k + 7]])
                k += 60
            else:
                k += 1
    tau_d = 0.25 / math.log(100.0)

    def sim(f: float, form: str, par: float, phase: float) -> list[float]:
        df, dt = REL_BW * f, 0.0005
        e = y = 0.0
        t, nxt, out = 0.0, FRAME_S * (1.0 - phase), []
        while len(out) < 9:
            t += dt
            if form == "onepole":
                e += (1.0 - e) * (1.0 - math.exp(-dt / (par / df)))
                p = e
            else:
                x = min(1.0, t / (par / df))
                p = (x - math.sin(2 * math.pi * x) / (2 * math.pi)) ** 2       # a Hann window filling with the tone
            y += (p - y) * (1.0 - math.exp(-dt / tau_d))                        # then the display pole (section 3)
            if t >= nxt - 1e-12:
                out.append(10.0 * math.log10(max(y, 1e-13)))
                nxt += FRAME_S
        return out

    for band, seqs in eps.items():
        for s in seqs:
            print(f"   measured band {band} ({RTA20(band):.0f} Hz): {s}  increments {[round(b - a, 1) for a, b in zip(s, s[1:])]}")
    lf_eps = [(RTA20(b) / 2 ** 0.0342, s) for b in (20, 30) for s in eps.get(b, [])]      # the tones were 78.1 / 156.3 Hz
    for form, grid_ in (("onepole", [x / 20 for x in range(4, 41)]), ("window", [x / 20 for x in range(10, 81)])):
        best = None
        for par in grid_:
            tot = 0.0
            for f, m in lf_eps:
                e = min(sum(abs(a - c) for a, c in zip(sim(f, form, par, ph / 20)[off:off + len(m)], m)) / len(m)
                        for ph in range(20) for off in (0, 1))
                tot += e
            if best is None or tot / len(lf_eps) < best[0]:
                best = (tot / len(lf_eps), par)
        what = "one pole on power, tau = k/df" if form == "onepole" else "Hann window of length c/df filling"
        print(f"   best {what}: {'k' if form == 'onepole' else 'c'} = {best[1]:.2f}, mean |error| {best[0]:.2f} dB over {len(lf_eps)} LF episodes")


# ------------------------------------------------------------------------------------------------ GEQ depth
def geq() -> None:
    head("5. The GEQ readings against a ONE-LEG cut on a stereo strip read through a mono-summed tap (item 1)")

    def h(f: float, f0: float, g_db: float, q: float) -> complex:
        a = 10.0 ** (g_db / 40.0)
        s = 1j * f / f0
        return (s * s + s * (a / q) + 1.0) / (s * s + s / (a * q) + 1.0)

    meas = [(-6.0, 2000.0, 2.6), (-12.0, 2000.0, 4.1), (-12.0, 1888.12, 3.8), (-12.0, 2118.51, 3.6)]
    print("slider  tone Hz   measured   one leg, coherent sum (Q 4.3)   one leg, power sum   both legs (full depth)")
    for g, f, m in meas:
        hh = h(f, 2000.0, g, 4.3)
        print(f"{g:5.0f}  {f:8.1f}   {m:5.1f} dB   {-20 * math.log10(abs(1 + hh) / 2):14.2f} dB              "
              f"{-10 * math.log10((1 + abs(hh) ** 2) / 2):8.2f} dB        {-20 * math.log10(abs(hh)):8.2f} dB")
    best = min((sum((-20 * math.log10(abs(1 + h(f, 2000.0, g, q / 100)) / 2) - m) ** 2 for g, f, m in meas), q / 100) for q in range(100, 1500, 5))
    print(f"least squares over the four readings, coherent model: Q = {best[1]:.2f}, rms residual {math.sqrt(best[0] / 4):.2f} dB "
          f"(the centre readings have no free parameter: 2.49 and 4.07 dB)")
    print("what the tap shows for the ladder:   " + "   ".join(
        f"{s:.0f}: one leg {-20 * math.log10((1 + 10 ** (s / 20)) / 2):.2f}, both {-s:.0f}" for s in (-3.0, -6.0, -9.0, -12.0, -15.0)))
    fr = load("rta_release_decay_sweep_2026-09-23.jsonl.gz")
    on = [f["db"][67] for f in fr if f["decay_s"] is not None and round(f["decay_s"], 2) == 0.25 and f["osc"] == 1]
    sw = {r["i"]: r for r in load("rta_bands_semitone_sweep_2026-09-23.jsonl.gz")}
    print(f"raw-log cross-check: the 2.12 kHz tone reads {max(on):.1f} with the slider in (release sweep) and {sw[81]['peak_db']:.1f} "
          f"with it out (semitone sweep): {sw[81]['peak_db'] - max(on):.1f} dB")


# ------------------------------------------------------------------------------------------------ floors / det
def floor() -> None:
    head("6. The two detector states in rta_peakhold_det (item 3: 'floor -97 under RMS, -128 under PEAK')")
    fr = load("rta_peakhold_det_2026-09-23.jsonl.gz")
    for name, (a, b) in {"state A (floor -128), tone on ": (100, 450), "state B (floor -97),  tone on ": (950, 1170)}.items():
        print(f"   {name}: " + "  ".join(f"b{i} {st.mean(r['db'][i] for r in fr[a:b]):7.1f}" for i in range(63, 71)))
    for name, (a, b) in {"state A, tone off": (480, 670), "state B, tone off": (1200, 1440)}.items():
        v = [r["db"][16] for r in fr[a:b]]
        print(f"   {name}: band 16 (60.6 Hz, the only band above both floors) mean {st.mean(v):.1f} sd {st.pstdev(v):.1f} dB")
    k = next(i for i in range(1, len(fr)) if fr[i]["db"][66] - fr[i - 1]["db"][66] > 15.0 and fr[i - 1]["db"][66] > -60.0)
    print(f"   at the switch A -> B (frame {k}): band 66 reads " + ", ".join(f"{fr[j]['db'][66]:.1f}" for j in range(k - 1, k + 10)))
    print("   captures taken right after the server or a script wrote det = PEAK (rise log, release sweep, semitone sweep) all "
          "show the -97 floor: either that write does not take, or the floors are attributed the wrong way round.")


SECTIONS = {"prefs": prefs, "grid": grid, "display": display, "lf": lf, "geq": geq, "floor": floor}

if __name__ == "__main__":
    for key in (sys.argv[1:] or list(SECTIONS)):
        SECTIONS[key]()
