"""Explain the family verdict for the candidate nearest BAND over [t0,t1]."""
import sys, math
sys.path.insert(0, "tests"); sys.path.insert(0, "src")
from rtasim import frames, RTA_BAND_HZ
from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig, FeedbackDetector, _HARMONIC_OFFSETS
name, seed, band, t0, t1 = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
cfg = DetectorConfig.from_descriptor(Descriptor.load())
det = FeedbackDetector(cfg, RTA_BAND_HZ)
orig = det._partial_at
log = []
def spy(pos, need, b, age, prom, vals, tol=None, comove_db=None, min_level=-1e9):
    r = orig(pos, need, b, age, prom, vals, tol, comove_db, min_level)
    if r:
        # find which q
        n = len(vals); tol_ = cfg.harmonic_tol_bands if tol is None else tol
        for q in range(max(1, int(math.floor(pos-1))), min(n-1, int(math.ceil(pos+1))+1)):
            if prom[q] >= need and vals[q] >= vals[q-1] and vals[q] >= vals[q+1]:
                cq, _ = det._cluster(vals, q)
                if abs(cq - pos) <= tol_:
                    log.append(f"pos{pos:.2f}->b{q}(c{cq:.2f},P{prom[q]:.1f},L{vals[q]:.1f},need{need:.1f},tol{tol_:.2f})")
                    break
    return r
det._partial_at = spy
for ts, v in frames(name, seed):
    log.clear()
    det.feed(v, ts)
    if t0 <= ts <= t1:
        for c in det.candidates:
            if abs(c.band - band) <= 1 and not c.missed:
                print(f"t={ts:.3f} b{c.band} c{c.centroid:.2f} P{c.prominence_db:.1f} fam={c.family_hist[-1] if c.family_hist else '-'} frac{c.family_frac:.2f} :: " + "; ".join(l for l in log if True))
