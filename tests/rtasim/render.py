"""Scene mixer + renderer: programme sources + feedback rings + GEQ (PRE insert) + analyser → frames.

Time base: frame k has timestamp ts = k·FRAME_S and integrates the substeps ending at scene time t = ts
(substep j of frame k is at t = ts − FRAME_S + (j+1)·dt). Before frame 0 the scene runs ``pre_roll_s`` of
unrecorded frames so beds, room noise and *established* rings are settled when the detector arms.

The renderer records a per-frame ground-truth trace for every ring (level, effective excess, frequency,
whether the ring dominates its band on the display and how prominent that band is), from which
:func:`ring_episodes` derives feedback events *from the rendered frames* (not by assumption).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Sequence

from .analyser import Analyser, band_position, nearest_band
from .physics import (
    FRAME_S, RTA_BANDS, RTA_BAND_HZ, GEQ_BAND_HZ, GEQ_Q_DEFAULT, AnalyserSettings, DEFAULT_ANALYSER,
    peaking_gain_db,
)
from .sources import CommonModeGain, FeedbackRing, Source

PROM_K = 3                 # neighbour bins for the ground-truth prominence (= detector default, DESIGN §12)
DOMINANCE_MIN = 0.5        # ring supplies >= half the band's power (-3 dB) -> "the band IS the ring"
PROM_VISIBLE_DB = 12.0     # ground-truth 'RTA-visible' threshold (device.yaml prominence_db) [A §7]
PROM_PRESENT_DB = 6.0      # below this (and loop sub-threshold) an episode is over
EXCITE_REACH_OCT = 0.15    # programme lines within ±1.5 bands feed the loop's comb (weighted by detuning)


def prominence_at(values: Sequence[float], i: int, k: int = PROM_K) -> float:
    """Single-band prominence exactly as x32mcp.detector defines it: level − median of the ±k neighbours."""
    lo, hi = max(0, i - k), min(len(values), i + k + 1)
    neigh = [values[j] for j in range(lo, hi) if j != i]
    return values[i] - median(neigh)


def cluster_prominence_at(values: Sequence[float], i: int) -> float:
    """Cluster prominence: power sum of bands i−1..i+1 (a line split over two bands or off-centre counts in full)
    over the median of the six bands at ±2..±4 (outside a single tone's skirts). Ground truth uses the earlier of
    the two definitions so that a detector using cluster power is neither flattered nor penalised (an edge tone is
    'visible' 3 dB earlier by this measure than by the single-band one)."""
    n = len(values)
    p = sum(10.0 ** (values[j] / 10.0) for j in range(max(0, i - 1), min(n, i + 2)))
    neigh = [values[j] for d in (2, 3, 4) for j in (i - d, i + d) if 0 <= j < n]
    if not neigh:
        return float("-inf")
    return 10.0 * math.log10(p) - median(neigh)


@dataclass
class Scene:
    """What is in the room. ``sources`` are programme (moved by ``master``×``prog_coupling``); ``rings`` are
    loops (their excess moved by ``master``×``loop_coupling`` and by GEQ cuts at their frequency)."""

    duration_s: float
    sources: list[Source] = field(default_factory=list)
    rings: list[FeedbackRing] = field(default_factory=list)
    master: CommonModeGain | None = None
    prog_coupling: float = 1.0        # 1.0 = channel/common move or REVIEW_BRIEF framing; 0.0 = pure bus-master at a pre-fader tap [L §0]
    loop_coupling: float = 1.0
    sat_coupling: float = 0.0         # how much of the master move sits between mic and tap and therefore moves a
                                      # downstream-set howl plateau (speaker limiter / SPL): 0.0 for the bus master
                                      # (ring_out: post-tap), 1.0 for a channel fader / DCA / preamp move [L §1.4]
    analyser: AnalyserSettings = DEFAULT_ANALYSER
    geq_q: float = GEQ_Q_DEFAULT
    geq_init: dict[int, float] = field(default_factory=dict)   # 1-based GEQ band -> dB present before frame 0
    geq_schedule: list[tuple[float, int, float]] = field(default_factory=list)  # (t, band, gain_db) scripted cuts
    salt: int = 0                     # mixed into the analyser noise seed so scenarios get independent realisations

    def master_db(self, t: float) -> float:
        return self.master.gain_db(t) if self.master is not None else 0.0


@dataclass
class RingFrame:
    """Ground-truth sample of one ring at one frame."""
    ts: float
    level_db: float        # loop line level at the tap (before the analyser)
    e_eff: float           # effective excess loop gain (dB); > 0 = physically regenerating
    f_hz: float
    band: int              # nearest RTA band
    dom: float             # max feedback share of display power over band±1 (0..1)
    prom_db: float         # best prominence over band±1 among ring-dominated bands (-inf if none)
    disp_db: float         # display value at the nearest band
    active: bool


class Renderer:
    """Streams frames for one (scene, seed). ``set_geq_gain`` may be called between frames (closed loop)."""

    def __init__(self, scene: Scene, seed: int = 1, *, band_hz: Sequence[float] = RTA_BAND_HZ,
                 geq_band_hz: Sequence[float] = GEQ_BAND_HZ) -> None:
        self.scene = scene
        self.seed = int(seed)
        self.band_hz = tuple(band_hz)
        self.geq_band_hz = tuple(geq_band_hz)
        self.an = Analyser(scene.analyser, seed=self.seed * 100003 + int(scene.salt), band_hz=self.band_hz)
        self.rng = random.Random(self.seed * 4099 + 1)
        self.substeps = self.an.substeps
        self.dt = FRAME_S / self.substeps
        self.n_frames = int(round(scene.duration_s / FRAME_S))
        self.k = 0
        self.frames: list[tuple[float, list[float]]] = []
        self.dominance: list[list[float]] = []
        self.trace: dict[int, list[RingFrame]] = {i: [] for i in range(len(scene.rings))}
        self.geq: dict[int, float] = {}
        self._geq_band_gain = [0.0] * RTA_BANDS
        self.geq_log: list[tuple[float, int, float]] = []   # (ts, band, gain_db)
        for i, r in enumerate(scene.rings):
            r.randomize(self.seed * 7 + 13 * i + int(scene.salt))   # wander / loop-gain wander realisations per seed
            r.reset()
        for s in scene.sources:
            if hasattr(s, "reset"):
                s.reset()
        for b, g in scene.geq_init.items():
            self.set_geq_gain(b, g, _log=False)
        self._sched = sorted(scene.geq_schedule)
        self._pre_roll()

    # -- GEQ (PRE insert: the analyser sees the cut on everything, HANDOVER §4b(4)) --------------------
    def set_geq_gain(self, band: int, gain_db: float, _log: bool = True) -> None:
        """Set 1-based GEQ ``band`` to ``gain_db`` (<= 0; cuts only in CFS²)."""
        if not 1 <= band <= len(self.geq_band_hz):
            raise ValueError(f"GEQ band {band} out of range")
        if gain_db == 0.0:
            self.geq.pop(band, None)
        else:
            self.geq[band] = float(gain_db)
        self._geq_band_gain = [self.geq_gain_at(f) for f in self.band_hz]
        if _log:
            self.geq_log.append((self.k * FRAME_S, band, gain_db))

    def geq_gain_at(self, f_hz: float) -> float:
        if not self.geq:
            return 0.0
        q = self.scene.geq_q
        return sum(peaking_gain_db(f_hz, self.geq_band_hz[b - 1], g, q) for b, g in self.geq.items())

    # -- simulation ------------------------------------------------------------------------------
    def _substep(self, t: float) -> None:
        sc = self.scene
        m = sc.master_db(t)
        gp = m * sc.prog_coupling
        geq_on = bool(self.geq)
        gbg = self._geq_band_gain
        tones: list[tuple[float, float]] = []
        noise: list[tuple[int, float]] = []
        for s in sc.sources:
            if s.t0 <= t <= s.t1:
                if geq_on:
                    for f, lv in s.tones(t):
                        tones.append((f, lv + gp + self.geq_gain_at(f)))
                    for b, lv in s.noise(t):
                        noise.append((b, lv + gp + gbg[b]))
                elif gp:
                    for f, lv in s.tones(t):
                        tones.append((f, lv + gp))
                    for b, lv in s.noise(t):
                        noise.append((b, lv + gp))
                else:
                    tones.extend(s.tones(t))
                    noise.extend(s.noise(t))
        fb: list[tuple[float, float]] = []
        if sc.rings:
            an = self.an
            for r in sc.rings:
                f = r.current_hz(t)
                lines: list[tuple[float, float]] = []
                bed_db = -200.0
                if r.excite_from_programme:
                    b = nearest_band(f)
                    # noise-like power in the ring's band at the tap (the analyser's band estimate of the bed —
                    # for a real filter bank the short-time band power IS this fluctuating quantity)
                    pn = an.e_noise[b] * an.nfl_lin[b] * an.noise_off_lin
                    if pn > 0.0:
                        bed_db = 10.0 * math.log10(pn)
                    lf = math.log2(f)
                    for tf, tl in tones:
                        if abs(math.log2(tf) - lf) <= EXCITE_REACH_OCT:
                            lines.append((tf, tl))
                g_ring = self.geq_gain_at(f) if geq_on else 0.0
                r.step(t, self.dt, common_db=m * sc.loop_coupling, geq_gain_db=g_ring, lines=lines, bed_db=bed_db,
                       prog_gain_db=m * sc.sat_coupling)
                if geq_on:
                    # the loop signal passes the PRE-insert GEQ before the RTA tap like everything else
                    fb.extend((tf, tl + self.geq_gain_at(tf)) for tf, tl in r.tones(t))
                else:
                    fb.extend(r.tones(t))
        self.an.substep(tones, fb, noise)

    def _run_frame(self, k: int) -> tuple[list[float], list[float]]:
        ts = k * FRAME_S
        dt = self.dt
        t0 = ts - FRAME_S
        for j in range(self.substeps):
            self._substep(t0 + (j + 1) * dt)
        return self.an.end_frame()

    def _pre_roll(self) -> None:
        n = int(round(self.scene.analyser.pre_roll_s / FRAME_S))
        for k in range(-n, 0):
            self._run_frame(k)

    @property
    def done(self) -> bool:
        return self.k >= self.n_frames

    def step_frame(self) -> tuple[float, list[float]] | None:
        """Render the next frame. Returns ``(ts, values[100])`` or None if the frame was 'dropped'
        (state still advances; see AnalyserSettings.drop_frame_prob). Raises StopIteration when done."""
        if self.done:
            raise StopIteration
        k = self.k
        while self._sched and self._sched[0][0] <= k * FRAME_S + 1e-9:
            _, band, gain = self._sched.pop(0)
            self.set_geq_gain(band, gain)
        values, dom = self._run_frame(k)
        ts = round(k * FRAME_S, 9)
        s = self.scene.analyser
        # ground-truth trace for every ring
        for i, r in enumerate(self.scene.rings):
            f = r.current_hz(ts)
            b = nearest_band(f)
            best_prom = float("-inf")
            best_dom = 0.0
            for bb in (b - 1, b, b + 1):
                if 0 <= bb < RTA_BANDS:
                    d = dom[bb]
                    if d > best_dom:
                        best_dom = d
                    if d >= DOMINANCE_MIN:
                        p = prominence_at(values, bb)
                        # the cluster measure counts only if the ring also dominates the CLUSTER's power (a programme
                        # partial in the adjacent band must not make the ring 'visible' early)
                        lo, hi = max(0, bb - 1), min(RTA_BANDS, bb + 2)
                        pw = [10.0 ** (values[j] / 10.0) for j in range(lo, hi)]
                        share = sum(w * dom[j] for w, j in zip(pw, range(lo, hi))) / max(1e-30, sum(pw))
                        if share >= DOMINANCE_MIN:
                            p = max(p, cluster_prominence_at(values, bb))
                        if p > best_prom:
                            best_prom = p
            self.trace[i].append(RingFrame(ts=ts, level_db=r.level_db, e_eff=r.e_eff, f_hz=f, band=b,
                                           dom=best_dom, prom_db=best_prom, disp_db=values[b], active=r.active(ts)))
        self.dominance.append(dom)
        self.k += 1
        dropped = s.drop_frame_prob > 0.0 and self.rng.random() < s.drop_frame_prob
        if s.frame_jitter_s:
            ts += self.rng.uniform(-s.frame_jitter_s, s.frame_jitter_s)
        if dropped:
            return None
        frame = (ts, values)
        self.frames.append(frame)
        return frame

    def run(self) -> list[tuple[float, list[float]]]:
        while not self.done:
            self.step_frame()
        return self.frames


# ---------------------------------------------------------------------------------------------
# ground truth from the trace
# ---------------------------------------------------------------------------------------------
@dataclass
class Episode:
    """One feedback event of one ring, derived from the rendered trace.

    * ``t_onset``: first frame at which the loop is physically regenerating (e_eff > 0), or 0.0 for a ring
      established before arm;
    * ``t_prom``: first frame within the episode at which a band within ±1 of the ring's nearest centre is
      ring-dominated (>= -3 dB share) AND its prominence — the larger of the single-band (median of ±3,
      detector definition) and the cluster (powersum ±1 over median of ±2..±4) measure — is >= 12 dB on the
      *displayed* values: the earliest moment an RTA-watching detector could see it; latency is judged from
      here [A §7] (the analyser's own LF rise time is upstream of this and is not charged to the detector);
    * ``t_end``: first frame after which the loop is sub-threshold and the line has either fallen 20 dB from
      its episode peak or lost 6 dB prominence (killed by a cut / gain backed off), else the scenario end.
    """
    ring: int
    label: str
    freq_hz: float
    band: int
    t_onset: float
    t_prom: float | None
    t_end: float
    peak_level_db: float
    peak_prom_db: float
    established: bool

    @property
    def visible(self) -> bool:
        return self.t_prom is not None

    def to_dict(self) -> dict[str, Any]:
        return {"ring": self.ring, "label": self.label, "freq_hz": round(self.freq_hz, 1), "band": self.band,
                "t_onset": round(self.t_onset, 4), "t_prom": None if self.t_prom is None else round(self.t_prom, 4),
                "t_end": round(self.t_end, 4),
                "peak_level_db": round(self.peak_level_db, 1), "peak_prom_db": round(self.peak_prom_db, 1),
                "established": self.established, "visible": self.visible}


def ring_episodes(renderer: Renderer) -> list[Episode]:
    out: list[Episode] = []
    for i, r in enumerate(renderer.scene.rings):
        tr = renderer.trace[i]
        if not tr:
            continue
        in_ep = False
        start = 0
        peak_lv = -200.0
        peak_prom = -200.0
        t_prom = None
        for k, fr in enumerate(tr):
            above = fr.active and fr.e_eff > 0.0
            if not in_ep:
                if above or (k == 0 and r.established and fr.active):
                    in_ep = True
                    start = k
                    peak_lv = fr.level_db
                    peak_prom = fr.prom_db
                    t_prom = fr.ts if fr.prom_db >= PROM_VISIBLE_DB else None
                continue
            # inside an episode
            peak_lv = max(peak_lv, fr.level_db)
            peak_prom = max(peak_prom, fr.prom_db)
            if t_prom is None and fr.prom_db >= PROM_VISIBLE_DB:
                t_prom = fr.ts
            # a marginal loop flickers either side of threshold (loop-gain wander): that does not end an episode
            # unless the line has actually gone (20 dB down) or, having been visible, lost its prominence
            ended = (not above) and (fr.level_db <= peak_lv - 20.0 or (t_prom is not None and fr.prom_db < PROM_PRESENT_DB))
            if ended:
                out.append(Episode(i, r.label, tr[start].f_hz, tr[start].band, tr[start].ts, t_prom, fr.ts,
                                   peak_lv, peak_prom, r.established and start == 0))
                in_ep = False
        if in_ep:
            out.append(Episode(i, r.label, tr[start].f_hz, tr[start].band, tr[start].ts, t_prom, tr[-1].ts,
                               peak_lv, peak_prom, r.established and start == 0))
    return out
