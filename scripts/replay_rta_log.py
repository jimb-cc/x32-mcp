#!/usr/bin/env python
"""Replay a logged ``/meters/15`` stream through the product FeedbackDetector, exactly as cfs.py drives it (no desk needed).

The log is JSONL: an optional first ``{"meta": {...}}`` line, then one ``{"ts": <s>, "db": [100 floats]}`` per frame (the
format ``scripts/log_rta_frames.py`` and the 2026-09-25 programme logs use; gzipped files are read transparently). The
detector is built as ``CfsManager`` builds it -- ``DetectorConfig.from_descriptor(Descriptor.load())``, the descriptor's band
grid, ``mode`` watch or ringout -- and fed ``feed(values, ts)`` frame by frame from the first frame (armed on whatever the
tap carried at that moment, as ``feedback_watch`` would be).

Prints every emission (what tier A would have cut), the MODERATE/STRONG tracks the policy layer would have seen (tier B's
input), and the level statistics of the log. Pure product code plus stdlib.

    .venv/Scripts/python scripts/replay_rta_log.py docs/research/data/programme_spotify_main_2026-09-25_peak025.jsonl.gz
    .venv/Scripts/python scripts/replay_rta_log.py LOG --mode ringout --top 30
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import sys
from pathlib import Path

from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig, FeedbackDetector
from x32mcp.meters import rta_band_hz


def load(path: Path) -> tuple[dict | None, list[tuple[float, list[float]]]]:
    opener = gzip.open if path.suffix == ".gz" else open
    meta, frames = None, []
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if "meta" in row:
                meta = row["meta"]
            elif "db" in row:
                frames.append((float(row["ts"]), [float(v) for v in row["db"]]))
    return meta, frames


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("log", type=Path)
    ap.add_argument("--mode", choices=("watch", "ringout"), default="watch")
    ap.add_argument("--top", type=int, default=15, help="MODERATE/STRONG tracks to list (loudest first)")
    ap.add_argument("--json", type=Path, default=None, help="also write the emissions and tracks as JSON")
    a = ap.parse_args()
    meta, frames = load(a.log)
    if not frames:
        print("no frames", file=sys.stderr)
        return 2
    d = Descriptor.load()
    band_hz = rta_band_hz(d)
    cfg = DetectorConfig.from_descriptor(d)
    det = FeedbackDetector(cfg, band_hz, mode=a.mode)
    t0 = frames[0][0]
    emissions = []
    klass_frames: collections.Counter = collections.Counter()
    tracks: dict = {}
    peaks = []
    for ts, db in frames:
        if len(db) != len(band_hz):
            print(f"frame at {ts} has {len(db)} bands, expected {len(band_hz)}", file=sys.stderr)
            return 2
        for e in det.feed(db, ts - t0):
            emissions.append({"t": round(e.ts, 2), "freq_hz": round(e.freq_hz), "band": e.band, "level_db": round(e.level_db, 1),
                              "prominence_db": round(e.prominence_db, 1), "slope_db_per_s": round(e.slope_db_per_s, 1),
                              "klass": e.klass, "reasons": list(e.reasons)})
        for c in det.candidates:
            klass_frames[c.klass] += 1
            if c.klass in ("MODERATE", "STRONG"):
                key = (c.band, round(c.first_ts, 2))
                if key not in tracks or c.level_db > tracks[key]["level_db"]:
                    tracks[key] = {"freq_hz": round(c.freq_hz), "level_db": round(c.level_db, 1), "klass": c.klass,
                                   "t": round(ts - t0, 2), "reasons": list(c.reasons)[:6]}
        peaks.append(max(db))
    n = len(frames)
    dur = frames[-1][0] - t0
    peaks.sort()
    print(f"{a.log.name}: {n} frames, {dur:.1f} s, frame period {dur / max(1, n - 1) * 1000:.1f} ms; meta {meta}")
    print(f"peak-band level p5 / p50 / p95: {peaks[int(0.05 * n)]:.1f} / {peaks[n // 2]:.1f} / {peaks[int(0.95 * n)]:.1f} dBFS; "
          f"loud-ish line {det.loudish_threshold_db:.1f} dBFS; mode {a.mode}")
    print(f"EMISSIONS (tier A would cut each): {len(emissions)}")
    for e in emissions:
        print(f"  t={e['t']:7.2f}  {e['freq_hz']:6d} Hz  {e['level_db']:6.1f} dB  prom {e['prominence_db']:5.1f}  slope {e['slope_db_per_s']:6.1f} dB/s  "
              f"{e['klass']:8s} {' '.join(e['reasons'])}")
    print(f"candidate klass-frames: {dict(klass_frames)}")
    top = sorted(tracks.values(), key=lambda t: -t["level_db"])[:a.top]
    print(f"MODERATE/STRONG tracks: {len(tracks)} (tier B's input); loudest {len(top)}:")
    for t in top:
        print(f"  {t['freq_hz']:6d} Hz  {t['level_db']:6.1f} dB  {t['klass']:8s} at t={t['t']:7.2f}  {' '.join(t['reasons'])}")
    if a.json:
        a.json.write_text(json.dumps({"log": a.log.name, "meta": meta, "mode": a.mode, "frames": n, "seconds": round(dur, 2),
                                      "emissions": emissions, "tracks": sorted(tracks.values(), key=lambda t: t["t"])}, indent=1), encoding="utf-8")
        print(f"written {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
