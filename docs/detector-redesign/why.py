"""why.py SCEN SEED [band] : for each GT ring print track diagnostics from t_prom-0.3 to t_prom+budget; list FPs with diag."""
import sys, dataclasses
from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig, FeedbackDetector
from rtasim import frames, ground_truth, SCENARIOS
from rtasim.harness import run_one
name, seed = sys.argv[1], int(sys.argv[2])
mode = "watch"
cfg = dataclasses.replace(DetectorConfig.from_descriptor(Descriptor.load()), mode=mode)
fr = frames(name, seed)
gt = ground_truth(name, seed)
evs = gt["events"]
bud = gt["latency_budget_ms"] / 1000
bands = [int(sys.argv[3])] if len(sys.argv) > 3 else [e["band"] for e in evs]
wins = [(e["t_prom"] - 0.35 if e["t_prom"] is not None else e["t_onset"], (e["t_prom"] or e["t_onset"]) + bud + 0.1) for e in evs] if len(sys.argv) <= 3 else [(float(sys.argv[4]), float(sys.argv[5]))]
print([(e['freq_hz'], e['band'], e['t_onset'], e['t_prom']) for e in evs])
det = FeedbackDetector(cfg, [10000 * 2 ** ((i - 90) / 10) for i in range(100)])
for ts, v in fr:
    out = det.feed(v, ts)
    for d in out:
        near = any(abs(d.band - b) <= 1 for b in bands)
        print(f"  EMIT t={ts:.2f} b{d.band} f={d.freq_hz:.0f} L={d.level_db:.1f} {'(ring)' if near else '(OTHER)'} reasons={d.reasons}")
    for b, (w0, w1) in zip(bands, wins):
        if w0 <= ts <= w1:
            for t in det.tracks:
                if abs(t.centroid - b) <= 1.6 and t.frames >= 1:
                    dg = t.diag
                    print(f"{ts:6.2f} b{t.band} c={t.centroid:5.2f} L={t.level_db:6.1f} prom={t.prominence_db:4.1f} nar={t.narrow_db:4.1f} fr={t.frames:3d} {t.birth:6s} est={int(t.est)} gs={t.grow_start}/{len(t.levels)} v={t.verdict or '-':9s} fb={int(t.feedback)} " + " ".join(f"{k}={v}" for k, v in dg.items()))
