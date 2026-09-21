"""pdbg.py SCEN SEED : probe evaluations per step for tracks (ringout mode with note_gain_step)."""
import sys, dataclasses
from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig, FeedbackDetector
from rtasim import frames, ground_truth, SCENARIOS
name, seed = sys.argv[1], int(sys.argv[2])
cfg = dataclasses.replace(DetectorConfig.from_descriptor(Descriptor.load()), mode="ringout")
sc = SCENARIOS[name]; scene = sc.build(seed)
pts = scene.master.points if scene.master else []
steps = [(b[0], b[1]-a[1]) for a, b in zip(pts, pts[1:]) if b[0] == a[0] and b[1] != a[1]]
print("steps", steps)
print([(e['freq_hz'], e['band'], e['t_onset'], e['t_prom']) for e in ground_truth(name, seed)["events"]])
fr = frames(name, seed)
det = FeedbackDetector(cfg, [10000 * 2 ** ((i - 90) / 10) for i in range(100)])
si = 0
for ts, v in fr:
    while si < len(steps) and ts >= steps[si][0]:
        det.note_gain_step(steps[si][1], steps[si][0]); si += 1
    before = {id(t): (t.probe_steps, t.probe_hits, t.probe_linear) for t in det.tracks}
    out = det.feed(v, ts)
    for t in det.tracks:
        b = before.get(id(t))
        if b is not None and t.probe_steps != b[0]:
            print(f"  t={ts:6.2f} probe eval: band {t.band} c={t.centroid:.2f} L={t.level_db:.1f} prom={t.prominence_db:.1f} exc={t.probe_last_excess_db:+.2f} hits={t.probe_hits} lin={t.probe_linear} steps={t.probe_steps}")
    for d in out:
        print(f"EMIT t={ts:.2f} band={d.band} f={d.freq_hz:.0f} L={d.level_db:.1f} reasons={d.reasons}")
