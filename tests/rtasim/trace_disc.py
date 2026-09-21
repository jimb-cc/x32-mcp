"""Evidence trace for one scenario: per frame, the terms of candidates near the requested bands (or the ring bands).

    PYTHONPATH=src:tests python -m rtasim.trace_disc SCENARIO [seed] [--bands=64,80] [--mode=watch|ringout] [--every=1] [--t0=..] [--t1=..]
"""

from __future__ import annotations

import sys

from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig

from rtasim import SCENARIOS
from rtasim.harness import run_one
from rtasim.run_disc import make_factory
from rtasim.scenarios import render as render_cached
from rtasim.render import ring_episodes


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opts = {a.split("=", 1)[0][2:]: (a.split("=", 1)[1] if "=" in a else "1") for a in sys.argv[1:] if a.startswith("--")}
    name = [n for n in SCENARIOS if n.startswith(args[0])][0]
    seed = int(args[1]) if len(args) > 1 else 1
    cfg = DetectorConfig.from_descriptor(Descriptor.load())
    sc = SCENARIOS[name]
    r = render_cached(sc, seed, None)
    eps = ring_episodes(r)
    print(f"{name} s{seed} mode={sc.mode} lf={sc.lf_optin} episodes={[(round(e.freq_hz), e.band, e.t_onset, e.t_prom, round(e.t_end, 2)) for e in eps]}")
    bands = [int(b) for b in opts["bands"].split(",")] if "bands" in opts else sorted({e.band for e in eps})
    every = int(opts.get("every", "1"))
    t0 = float(opts.get("t0", "-1"))
    t1 = float(opts.get("t1", "1e9"))
    fac = make_factory(cfg, name, seed, opts.get("mode"))
    det = fac(r.band_hz if hasattr(r, "band_hz") else None)
    emitted = []
    for k, (ts, vals) in enumerate(r.frames):
        out = det.feed(vals, ts)
        for d in out:
            emitted.append((round(ts, 2), d.band, round(d.freq_hz), round(d.llr, 2), d.reasons))
            print(f"  ** EMIT t={ts:.2f} band {d.band} {d.freq_hz:.0f} Hz lvl {d.level_db:.1f} prom {d.prominence_db:.1f} llr {d.llr:.2f} {d.reasons}")
        if k % every or ts < t0 or ts > t1:
            continue
        for c in det.candidates:
            if bands and not any(abs(c.band - b) <= 1 for b in bands):
                continue
            tr = " ".join(f"{kk[:3]}{v:+.2f}" for kk, v in c.terms.items())
            print(f"t={ts:5.2f} b{c.band:3d} c{c.centroid:6.2f} lv{c.level_db:6.1f} cl{(c.levels[-1] if c.levels else 0):6.1f} pr{c.prominence_db:5.1f} cp{c.cl_prom_db:5.1f} "
                  f"n2{c.narrow_db:5.1f} fr{c.frames:4d} co{c.coast} llr{c.llr:+6.2f} {c.state[:4]} | {tr} | gr{c.grow_run} ga{c.grow_acc:.1f} sa{c.slow_acc:.1f} "
                  f"da{c.decay_acc:.1f} sp{c.step_pen:+.1f} ba{c.boundary_acc:.1f} pa{c.probe_acc:+.1f} fam{c.fam_frames}/{c.sub_frames}/{c.fam1_frames}/{c.fam_obs}"
                  f"{' FL' if c.family_latched else ''}{' ML' if c.modulated_latched else ''} hop{c.hop_count} ww{c.wobble_windows}")
    print("emitted:", emitted)


if __name__ == "__main__":
    main()
