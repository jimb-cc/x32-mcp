"""Harness stand-in for the policy layer's tier B (docs/CFS_POLICY.md §3).

The product cuts on two tiers: the detector's own emissions (tier A) and the session manager's one-shot policy cut on a
MODERATE line (tier B: family-less, loud-ish or >= min_excess_db over its baseline, continuously present >= min_age_s),
followed up on the verdict the detector files for that cut. ``rtasim.harness`` drives the detector alone, so a scene whose
line can only ever be MODERATE (it arrives at its plateau within a frame: nothing was seen to grow) reads as "never cut"
there although the product would cut it. This wrapper closes that gap with the product's OWN pure rule,
``x32mcp.cfs_policy.tier_b_eligible``, and the detector's public hooks (``candidates``, ``note_emission``, ``cut_verdict``):

* engage: the first eligible candidate, one engagement per ``cooldown_s``, reported to the harness as a ``POLICY`` detection
  (the harness plans, writes and ``note_cut``s it like any other);
* ``insufficient`` -> one deeper step at once; ``held`` for ``held_deepen_s`` and still a family-less MODERATE line -> one
  deeper step; ``confirmed`` / ``false_cut`` / ``ambiguous`` -> nothing further.

It is a stand-in, not cfs.py: no ignore list, no operator pushes, no ring_out queueing, no alerts. Use it to ask "is the ring
dead at the end when the policy layer is on?", not to validate the policy layer itself (tests/integration does that).
"""

from __future__ import annotations

from typing import Any

from x32mcp.cfs_policy import TierBConfig, tier_b_eligible
from x32mcp.detector import Detection


class TierBStandIn:
    def __init__(self, det: Any, cfg: TierBConfig | None = None, *, probe_min_hits: int = 2, dwell_s: float = 1.0) -> None:
        self.det = det
        self.cfg = cfg or TierBConfig()
        self.probe_min_hits, self.dwell_s = int(probe_min_hits), float(dwell_s)
        self.open: dict[int, dict[str, Any]] = {}      # id(track) -> {c, v (last verdict acted on), vts}
        self.last_engage = -1e9
        self.log: list[dict[str, Any]] = []            # every policy emission {ts, rule, freq_hz, level_db}

    def __getattr__(self, name: str) -> Any:           # note_cut, note_gain_step, candidates, flags ... pass through
        return getattr(self.det, name)

    def _emit(self, c: Any, ts: float, rule: str) -> Detection:
        self.det.note_emission(c, ts, reason="tier_b")
        self.log.append({"ts": round(ts, 3), "rule": rule, "freq_hz": round(c.freq_hz, 1), "level_db": round(c.level_db, 1)})
        return Detection(ts=ts, band=c.band, freq_hz=c.freq_hz, level_db=c.level_db, prominence_db=c.prominence_db,
                         slope_db_per_s=c.slope_db_per_s, frames=c.frames, confidence=c.confidence,
                         reasons=tuple(c.reasons) + (rule,), klass="POLICY")

    def feed(self, values: Any, ts: float) -> list[Any]:
        out = list(self.det.feed(values, ts))
        if not self.cfg.enabled:
            return out
        live = {id(c) for c in self.det.candidates}
        for k in [k for k in self.open if k not in live]:
            del self.open[k]
        busy = {d.band for d in out}                   # the detector's own emission on a band this frame is served first
        for st in self.open.values():
            c, v = st["c"], st["c"].cut_verdict
            if v != st["v"]:
                st["v"], st["vts"] = v, ts
                if v == "insufficient" and c.band not in busy:
                    out.append(self._emit(c, ts, "tier_b_insufficient"))
                    st["v"] = "pending"
                    continue
            if (v == "held" and ts - st["vts"] >= self.cfg.held_deepen_s and c.klass == "MODERATE" and "no_family" in c.reasons
                    and not c.stationary and not c.common_mode and not c.misses and c.band not in busy):
                out.append(self._emit(c, ts, "tier_b_held"))
                st["v"], st["vts"] = "pending", ts
        if ts - self.last_engage >= self.cfg.cooldown_s:
            mode = "ringout" if str(getattr(self.det, "mode", "watch")).startswith("ring") else "watch"
            for c in self.det.candidates:
                if id(c) in self.open or c.band in busy:
                    continue
                ok, _why = tier_b_eligible(c, cfg=self.cfg, loudish_db=self.det.loudish_threshold_db, mode=mode,
                                           probe_min_hits=self.probe_min_hits, moderate_for_s=0.0, dwell_s=self.dwell_s,
                                           frame_period_s=self.det.cfg.frame_period_s)
                if ok:
                    out.append(self._emit(c, ts, "tier_b"))
                    self.open[id(c)] = {"c": c, "v": "pending", "vts": ts}
                    self.last_engage = ts
                    break
        return out
