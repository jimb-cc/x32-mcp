"""gdbg.py SCEN SEED BAND T : growth-window analysis for the track near BAND at time T."""
import sys, dataclasses
from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig, FeedbackDetector, _ls_fit, _ls_slope
from rtasim import frames, ground_truth
name, seed, band, T = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])
cfg = DetectorConfig.from_descriptor(Descriptor.load())
fr = frames(name, seed)
print([(e['freq_hz'], e['band'], e['t_onset'], e['t_prom']) for e in ground_truth(name, seed)["events"]])
det = FeedbackDetector(cfg, [10000 * 2 ** ((i - 90) / 10) for i in range(100)])
for ts, v in fr:
    det.feed(v, ts)
    if abs(ts - T) < 0.026:
        for t in det.tracks:
            if abs(t.centroid - band) > 1.5: continue
            n = len(t.levels); gs = t.grow_start
            settle = det._settle_frames[t.band]
            print(f"track b{t.band} c={t.centroid:.2f} n={n} gs={gs} settle={settle} birth={t.birth} musical={t.musical} prom={t.prominence_db:.1f} nar={t.narrow_db:.1f} fam={t.fam_list[-10:]} kf-range={t.kf[0]}..{t.kf[-1]}")
            print("   levels:", [round(x,1) for x in t.levels[-25:]])
            for m in (5, 8, 12, 20, 40, 60, n-1-gs):
                if m < 2 or m > n-1-gs: continue
                i0 = n-1-m
                ts_ = t.ts_list[i0:]; vs = t.levels[i0:]
                s_, c0 = _ls_fit(ts_, vs); Tw = ts_[-1]-ts_[0]
                rise = min(s_*Tw, max(vs[-3:]) - min(vs[:3]))
                sref = _ls_slope(ts_, t.ref_list[i0:]); dref = max(sref*Tw, t.ref_list[-1]-t.ref_list[i0])
                net = rise - max(0, dref)
                resid = max(abs(v_ - (c0 + s_*x)) for x, v_ in zip(ts_, vs))
                steps = [b-a for a,b in zip(vs, vs[1:])]
                h = max(2, len(vs)//3)
                s1 = _ls_slope(ts_[:h+1], vs[:h+1]); s2 = _ls_slope(ts_[h:2*h+1], vs[h:2*h+1]); s3 = _ls_slope(ts_[-(h+1):], vs[-(h+1):])
                print(f"   m={m:3d} slope={s_:6.2f} rise={rise:6.2f} dref={dref:5.2f} net={net:6.2f} resid={resid:5.2f} (lim {max(1.5,0.25*rise):.2f}) maxstep={max(steps):5.2f} thirds={s1:6.2f},{s2:6.2f},{s3:6.2f} gap={t.kf[-1]-t.kf[i0]-m}")
        break
