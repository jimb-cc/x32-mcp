"""Compare two semitone-sweep result files (per-tone rows from measure_rta_bands.py): grid offset, flatness, skirts per
octave band, rise frames. Usage: compare_sweeps.py NEW.jsonl [OLD.jsonl(.gz)]"""
import gzip, json, math, statistics as st, sys

GRID = [20.0 * 2 ** (i / 10) for i in range(100)]


def load(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def summarise(label, rows):
    rows = [r for r in rows if "peak_band" in r]
    print(f"== {label}: {len(rows)} tones; prefs {rows[0].get('prefs')}")
    # grid: offset of the tone from the measured-grid centre of its peak band, and the neighbour ratio symmetry
    offs = [math.log2(r["hz"] / GRID[r["peak_band"]]) for r in rows if r["hz"] >= 300]
    print(f"  offset from 20*2^(i/10) centres (>=300 Hz): mean {st.mean(offs):+.4f} oct, sd {st.pstdev(offs):.4f} (a third of a band = 0.033)")
    lv = [r["peak_db"] for r in rows if r["hz"] >= 40]
    print(f"  peak level: mean {st.mean(lv):.2f} dB, sd {st.pstdev(lv):.2f}, min {min(lv):.1f}, max {max(lv):.1f}")
    bands = [(40, 80), (80, 160), (160, 320), (320, 1000), (1000, 3000), (3000, 10000)]
    print("  skirts rel dB (median over on-centre tones, |offset| < 0.02 oct): -2 / -1 / +1 / +2 ; rise frames to -3 / -1 dB (median)")
    for lo, hi in bands:
        sel = [r for r in rows if lo <= r["hz"] < hi and abs(math.log2(r["hz"] / GRID[r["peak_band"]])) < 0.02]
        if not sel:
            print(f"    {lo:5d}-{hi:5d} Hz: no on-centre tones")
            continue
        def med(k):
            v = [r[k] for r in sel if r.get(k) is not None]
            return st.median(v) if v else None
        print(f"    {lo:5d}-{hi:5d} Hz ({len(sel):2d} tones): {med('rel_m2')} / {med('rel_m1')} / {med('rel_p1')} / {med('rel_p2')} ; "
              f"rise {med('rise_frames_to_-3')} / {med('rise_frames_to_-1')}")


if __name__ == "__main__":
    summarise("NEW " + sys.argv[1].rsplit("/", 1)[-1], load(sys.argv[1]))
    if len(sys.argv) > 2:
        summarise("OLD " + sys.argv[2].rsplit("/", 1)[-1], load(sys.argv[2]))
