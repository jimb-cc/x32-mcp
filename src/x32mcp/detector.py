"""CFS² feedback detector and notch planner (DESIGN.md §12) — pure Python, no I/O, no asyncio.

Two synchronous, side-effect-free pieces used by ``cfs.py``:

* :class:`FeedbackDetector` consumes one RTA frame at a time (100 dB values from ``/meters/15``,
  band ``i`` centred at ``band_hz[i]`` = ``10000 * 2 ** ((i - 90) / 10)`` Hz, docs/research/meters.md §4.2)
  and returns :class:`Detection` objects for bands that look like regenerative feedback.
* :class:`NotchController` turns detections into GEQ band cuts (:class:`Notch`), merging adjacent
  detections, deepening in ``notch_step_db`` steps to ``notch_max_db`` and respecting a per-session budget.
  It never writes anything itself; ``cfs`` applies the returned :class:`Notch` through a :class:`GeqWriter`.

Units and indices at the public boundary
----------------------------------------
* Levels are dB (RTA dB re. full scale, -128 = "no signal"), times are seconds (``time.time()`` style
  floats), slopes are dB/s.
* **RTA band indices are 0-based** (``Detection.band`` indexes ``band_hz``; matches the meters.md formula
  and the dashboard's ``db[]`` array).
* **GEQ band numbers are 1-based** (``Notch.band`` 1..31 == the ``/fx/N/par/NN`` parameter number,
  ``geq_band_hz[band - 1]`` is its centre; docs/research/fx_routing_scenes.md §2.3).

Heuristic (DESIGN §12, thresholds from :class:`DetectorConfig`)
---------------------------------------------------------------
* ``prominence[i] = level[i] - median(level[i-k .. i+k] without i)``, ``k = neighbour_bins`` (the window is
  truncated at the spectrum edges). A band *qualifies* when ``prominence >= prominence_db`` and
  ``level >= min_level_db``. Adjacent qualifying bands are one peak (the loudest of them).
* Peaks are tracked as :class:`Candidate` streaks; a peak within ``±band_tolerance`` of a candidate's band
  continues its streak (drift), a frame with no qualifying peak nearby ends it.
* growth: least-squares slope (dB/s) of level vs time over the candidate's *growth window*;
  ``growth_score = 0`` if the slope is below ``growth_min_db_per_s`` (plateau / decay), else
  ``min(1, slope / growth_ref_db_per_s)``.
* ``confidence = w_p * min(1, prominence / prominence_db) + w_s * min(1, frames / persistence_frames)
  + w_g * growth_score``; a detection is emitted when ``frames >= persistence_frames`` and
  ``confidence >= confidence_threshold`` and the band (±band_tolerance) is not in its ``cooldown_s``
  after a previous emission. A candidate that keeps ringing is re-emitted once per cooldown so the notch
  can be deepened.

Decisions where DESIGN is silent or a literal reading is not workable (see tests/test_detector.py):

* **Growth window restarts instead of freezing.** DESIGN says "monotonic iff every step >= -tolerance"
  over the whole streak. Read literally, one noisy -1.1 dB step would zero the growth score for the rest
  of the streak while the ring keeps growing. Here a step below ``-monotonic_tolerance_db`` restarts the
  growth window at the current frame (the persistence count ``frames`` is unaffected), which is the same
  verdict for that frame and recovers on the next ones. The window is also bounded to
  ``growth_window_frames`` samples (DESIGN's unbounded streak would grow without limit).
* **Onset guard** (``growth_max_db_per_s``, extension, default 60 dB/s): a level rising faster than a
  loop can regenerate (a note onset or a swell: +20 dB in 250 ms = 80 dB/s) restarts the growth window,
  so a note that swells and then holds is judged on its plateau (growth 0) instead of on its attack.
  Regenerative feedback in a ring-out grows at a few to ~30 dB/s (``growth_ref_db_per_s`` = 20).
* Growth needs at least ``persistence_frames`` (>= 2) samples in the window; a 2-sample slope of noisy
  data is meaningless.

Only ``logging`` is used for diagnostics (stdout is the MCP transport).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, fields
from statistics import median
from typing import Any, Callable, Mapping, Protocol, Sequence

log = logging.getLogger(__name__)

__all__ = [
    "DetectorConfig",
    "Detection",
    "Candidate",
    "FeedbackDetector",
    "Notch",
    "GeqWriter",
    "RecordingGeqWriter",
    "NotchController",
]


# ---------------------------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------------------------

_WEIGHT_KEYS = {"prominence": "w_prominence", "persistence": "w_persistence", "growth": "w_growth"}


@dataclass(frozen=True)
class DetectorConfig:
    """Detector + notch thresholds; one field per ``device.yaml`` ``detector:`` key.

    The yaml ``weights: {prominence, persistence, growth}`` mapping is flattened into
    ``w_prominence`` / ``w_persistence`` / ``w_growth``. ``growth_max_db_per_s`` and
    ``growth_window_frames`` are extensions with defaults (see module docstring).
    """

    prominence_db: float = 12.0          # dB above the median of the ±neighbour_bins neighbours
    neighbour_bins: int = 3              # bands each side used for the median
    min_level_db: float = -60.0          # ignore candidates quieter than this (RTA dB)
    persistence_frames: int = 3          # consecutive qualifying frames before a detection
    growth_min_db_per_s: float = 6.0     # slopes below this score 0 (plateau)
    growth_ref_db_per_s: float = 20.0    # slope that scores 1.0
    growth_max_db_per_s: float = 60.0    # faster than this is an onset, not a ring (extension)
    monotonic_tolerance_db: float = 1.0  # a step below -this restarts the growth window
    w_prominence: float = 0.3
    w_persistence: float = 0.2
    w_growth: float = 0.5
    confidence_threshold: float = 0.7
    band_tolerance: int = 1              # drift allowed while tracking / cooldown radius (RTA bands)
    cooldown_s: float = 1.0              # s between emissions for the same band
    notch_step_db: float = -3.0          # per detection (negative)
    notch_max_db: float = -9.0           # deepest cut (negative, <= notch_step_db)
    notch_budget_default: int = 6        # distinct GEQ bands per session
    merge_adjacent_bands: int = 1        # GEQ bands: a detection this close to a notch deepens it
    decay_verify_db: float = 6.0         # used by cfs.py (VERIFY stage)
    decay_verify_s: float = 1.5          # used by cfs.py
    frame_period_s: float = 0.05         # nominal RTA frame period (informational)
    growth_window_frames: int = 60       # bound on the growth window (extension)

    def __post_init__(self) -> None:
        def need(cond: bool, msg: str) -> None:
            if not cond:
                raise ValueError(f"DetectorConfig: {msg}")

        need(self.prominence_db > 0, "prominence_db must be > 0")
        need(self.neighbour_bins >= 1, "neighbour_bins must be >= 1")
        need(self.persistence_frames >= 1, "persistence_frames must be >= 1")
        need(self.growth_ref_db_per_s > 0, "growth_ref_db_per_s must be > 0")
        need(self.growth_max_db_per_s > self.growth_min_db_per_s, "growth_max_db_per_s must exceed growth_min_db_per_s")
        need(self.monotonic_tolerance_db >= 0, "monotonic_tolerance_db must be >= 0")
        need(min(self.w_prominence, self.w_persistence, self.w_growth) >= 0, "weights must be >= 0")
        need(self.band_tolerance >= 0, "band_tolerance must be >= 0")
        need(self.cooldown_s >= 0, "cooldown_s must be >= 0")
        need(self.notch_step_db < 0, "notch_step_db must be negative (cuts only)")
        need(self.notch_max_db <= self.notch_step_db, "notch_max_db must be <= notch_step_db")
        need(self.notch_budget_default >= 0, "notch_budget_default must be >= 0")
        need(self.merge_adjacent_bands >= 0, "merge_adjacent_bands must be >= 0")
        need(self.growth_window_frames >= max(2, self.persistence_frames), "growth_window_frames too small")

    @property
    def weights(self) -> dict[str, float]:
        return {"prominence": self.w_prominence, "persistence": self.w_persistence, "growth": self.w_growth}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DetectorConfig":
        """Build from a ``detector:`` mapping. Unknown keys are ignored (logged at DEBUG)."""
        known = {f.name: f.type for f in fields(cls)}
        kw: dict[str, Any] = {}
        for key, value in data.items():
            if key == "weights":
                if not isinstance(value, Mapping):
                    raise ValueError("DetectorConfig: weights must be a mapping")
                for wk, wv in value.items():
                    if wk not in _WEIGHT_KEYS:
                        raise ValueError(f"DetectorConfig: unknown weight {wk!r}")
                    kw[_WEIGHT_KEYS[wk]] = float(wv)
                continue
            if key not in known:
                log.debug("DetectorConfig: ignoring unknown key %r", key)
                continue
            kw[key] = int(value) if known[key] == "int" else float(value)
        return cls(**kw)

    @classmethod
    def from_descriptor(cls, d: Any) -> "DetectorConfig":
        """``DetectorConfig.from_dict(d.detector)`` for a loaded :class:`x32mcp.descriptor.Descriptor`."""
        return cls.from_dict(d.detector)

    def to_dict(self) -> dict[str, Any]:
        out = {f.name: getattr(self, f.name) for f in fields(self) if not f.name.startswith("w_")}
        out["weights"] = self.weights
        return out


# ---------------------------------------------------------------------------------------------
# detector
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Detection:
    """One feedback verdict. ``band`` is the 0-based RTA band, ``freq_hz`` its centre."""

    ts: float
    band: int
    freq_hz: float
    level_db: float
    prominence_db: float
    slope_db_per_s: float
    frames: int
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "band": self.band,
            "freq_hz": self.freq_hz,
            "level_db": self.level_db,
            "prominence_db": self.prominence_db,
            "slope_db_per_s": self.slope_db_per_s,
            "frames": self.frames,
            "confidence": self.confidence,
        }


@dataclass
class Candidate:
    """A tracked peak. ``levels``/``ts_list`` are the growth window (bounded, may restart; see module doc);
    ``frames`` is the full persistence count since ``first_ts``."""

    band: int
    first_ts: float
    frames: int
    levels: list[float]
    ts_list: list[float]
    confidence: float
    freq_hz: float = 0.0
    level_db: float = -128.0
    prominence_db: float = 0.0
    slope_db_per_s: float = 0.0
    growth_score: float = 0.0
    last_ts: float = 0.0
    emitted: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "band": self.band,
            "freq_hz": self.freq_hz,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "frames": self.frames,
            "level_db": self.level_db,
            "prominence_db": self.prominence_db,
            "slope_db_per_s": self.slope_db_per_s,
            "growth_score": self.growth_score,
            "confidence": self.confidence,
            "emitted": self.emitted,
        }


def _ls_slope(ts: Sequence[float], vs: Sequence[float]) -> float:
    """Least-squares slope of ``vs`` against ``ts`` (dB/s); 0.0 when fewer than two distinct times."""
    n = len(ts)
    if n < 2:
        return 0.0
    tm = sum(ts) / n
    vm = sum(vs) / n
    sxx = sum((t - tm) ** 2 for t in ts)
    if sxx <= 0.0:
        return 0.0
    sxy = sum((t - tm) * (v - vm) for t, v in zip(ts, vs))
    return sxy / sxx


class FeedbackDetector:
    """Frame-by-frame feedback detector over an RTA stream (see module docstring for the heuristic)."""

    def __init__(self, cfg: DetectorConfig, band_hz: Sequence[float]) -> None:
        self.cfg = cfg
        self.band_hz: tuple[float, ...] = tuple(float(h) for h in band_hz)
        if len(self.band_hz) < 2 * cfg.neighbour_bins + 1:
            raise ValueError("band_hz too short for neighbour_bins")
        self._cands: list[Candidate] = []
        self._cooldown: dict[int, float] = {}
        self.frames_seen: int = 0
        self.last_ts: float | None = None

    # -- helpers -------------------------------------------------------------------------------
    def prominences(self, values_db: Sequence[float]) -> list[float]:
        """``level[i] - median(neighbours within ±neighbour_bins, excluding i)`` for every band (dB)."""
        n = len(values_db)
        k = self.cfg.neighbour_bins
        out: list[float] = []
        for i in range(n):
            lo = max(0, i - k)
            hi = min(n, i + k + 1)
            neigh = [values_db[j] for j in range(lo, hi) if j != i]
            out.append(values_db[i] - median(neigh))
        return out

    @staticmethod
    def _peaks(qualifying: Sequence[int], values: Sequence[float]) -> list[int]:
        """Collapse runs of adjacent qualifying bands into their loudest band."""
        peaks: list[int] = []
        run: list[int] = []
        for b in qualifying:
            if run and b != run[-1] + 1:
                peaks.append(max(run, key=lambda j: values[j]))
                run = []
            run.append(b)
        if run:
            peaks.append(max(run, key=lambda j: values[j]))
        return peaks

    def _in_cooldown(self, band: int, ts: float) -> bool:
        tol = self.cfg.band_tolerance
        for b in range(band - tol, band + tol + 1):
            until = self._cooldown.get(b)
            if until is not None and ts < until:
                return True
        return False

    def _extend(self, c: Candidate, band: int, level: float, prom: float, ts: float) -> None:
        cfg = self.cfg
        c.frames += 1
        c.band = band
        c.freq_hz = self.band_hz[band]
        c.level_db = level
        c.prominence_db = prom
        c.last_ts = ts
        if c.levels and level - c.levels[-1] < -cfg.monotonic_tolerance_db:
            # A drop beyond the tolerance breaks the "monotonic rise": restart the growth window here
            # rather than zeroing growth for the rest of the streak (module docstring).
            c.levels.clear()
            c.ts_list.clear()
        c.levels.append(level)
        c.ts_list.append(ts)
        if len(c.levels) > cfg.growth_window_frames:
            del c.levels[0]
            del c.ts_list[0]

    def _score(self, c: Candidate) -> None:
        cfg = self.cfg
        min_samples = max(2, cfg.persistence_frames)
        slope = _ls_slope(c.ts_list, c.levels)
        if len(c.levels) >= min_samples and slope > cfg.growth_max_db_per_s:
            # Onset guard: nothing regenerative rises this fast; judge what follows, not the attack.
            del c.levels[:-1]
            del c.ts_list[:-1]
            slope = 0.0
        growth = 0.0
        if len(c.levels) >= min_samples and slope >= cfg.growth_min_db_per_s:
            growth = min(1.0, slope / cfg.growth_ref_db_per_s)
        c.slope_db_per_s = slope
        c.growth_score = growth
        c.confidence = (
            cfg.w_prominence * min(1.0, c.prominence_db / cfg.prominence_db)
            + cfg.w_persistence * min(1.0, c.frames / cfg.persistence_frames)
            + cfg.w_growth * growth
        )

    # -- API -----------------------------------------------------------------------------------
    def feed(self, values_db: Sequence[float], ts: float) -> list[Detection]:
        """Process one RTA frame (``len(values_db) == len(band_hz)``, dB) taken at time ``ts`` (s).

        Returns the detections emitted on this frame (possibly empty). Raises ``ValueError`` on a
        frame of the wrong length.
        """
        cfg = self.cfg
        vals = [float(v) for v in values_db]
        n = len(self.band_hz)
        if len(vals) != n:
            raise ValueError(f"expected {n} RTA values, got {len(vals)}")
        prom = self.prominences(vals)
        qualifying = [i for i in range(n) if prom[i] >= cfg.prominence_db and vals[i] >= cfg.min_level_db]
        peaks = self._peaks(qualifying, vals)

        used: set[int] = set()
        survivors: list[Candidate] = []
        for c in self._cands:
            best: int | None = None
            for p in peaks:
                if p in used:
                    continue
                dist = abs(p - c.band)
                if dist > cfg.band_tolerance:
                    continue
                if best is None or (dist, -vals[p]) < (abs(best - c.band), -vals[best]):
                    best = p
            if best is None:
                continue  # a frame without the condition ends the streak
            used.add(best)
            self._extend(c, best, vals[best], prom[best], ts)
            survivors.append(c)
        for p in peaks:
            if p in used:
                continue
            c = Candidate(band=p, first_ts=ts, frames=0, levels=[], ts_list=[], confidence=0.0)
            self._extend(c, p, vals[p], prom[p], ts)
            survivors.append(c)
        survivors.sort(key=lambda c: c.band)
        self._cands = survivors

        out: list[Detection] = []
        for c in self._cands:
            self._score(c)
            if (
                c.frames >= cfg.persistence_frames
                and c.confidence >= cfg.confidence_threshold
                and not self._in_cooldown(c.band, ts)
            ):
                det = Detection(
                    ts=ts,
                    band=c.band,
                    freq_hz=c.freq_hz,
                    level_db=c.level_db,
                    prominence_db=c.prominence_db,
                    slope_db_per_s=c.slope_db_per_s,
                    frames=c.frames,
                    confidence=c.confidence,
                )
                out.append(det)
                c.emitted += 1
                self._cooldown[c.band] = ts + cfg.cooldown_s
                log.debug("feedback detected: band %d (%.0f Hz) %.1f dB slope %.1f dB/s conf %.2f",
                          c.band, c.freq_hz, c.level_db, c.slope_db_per_s, c.confidence)
        if self._cooldown and self.frames_seen % 100 == 0:
            self._cooldown = {b: t for b, t in self._cooldown.items() if ts < t}
        self.frames_seen += 1
        self.last_ts = ts
        return out

    @property
    def candidates(self) -> list[Candidate]:
        """Current streaks (live objects; treat as read-only), lowest band first."""
        return list(self._cands)

    def reset(self) -> None:
        self._cands = []
        self._cooldown = {}
        self.frames_seen = 0
        self.last_ts = None


# ---------------------------------------------------------------------------------------------
# notch planning
# ---------------------------------------------------------------------------------------------


@dataclass
class Notch:
    """A GEQ cut. ``band`` is 1-based (par number), ``depth_db`` <= 0. ``session_id == ""`` and
    ``detections == 0`` mark a cut that pre-dates this session (from ``existing``)."""

    bus: int
    band: int
    freq_hz: float
    depth_db: float
    session_id: str
    ts: float
    detections: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "bus": self.bus,
            "band": self.band,
            "freq_hz": self.freq_hz,
            "depth_db": self.depth_db,
            "session_id": self.session_id,
            "ts": self.ts,
            "detections": self.detections,
        }


class GeqWriter(Protocol):
    async def set_band_gain(self, bus: int, band: int, gain_db: float) -> None:
        """Write ``gain_db`` (dB, -15..+15) to 1-based GEQ ``band`` of the GEQ serving ``bus``."""
        ...


class RecordingGeqWriter:
    """:class:`GeqWriter` that only records ``(bus, band, gain_db)`` — simulation and tests."""

    def __init__(self) -> None:
        self.writes: list[tuple[int, int, float]] = []
        self.gains: dict[tuple[int, int], float] = {}

    async def set_band_gain(self, bus: int, band: int, gain_db: float) -> None:
        self.writes.append((bus, band, gain_db))
        self.gains[(bus, band)] = gain_db


class NotchController:
    """Plans GEQ cuts for detections on one bus.

    ``policy_validate(current_db, new_db)`` is called before every planned change (``Policy.validate_notch``)
    and its exceptions propagate unchanged. ``budget`` is the number of distinct GEQ bands this session may
    write; deepening a band already written this session is free. ``existing`` (1-based band -> dB) gives
    the GEQ's current gains so deepening starts from them; its cuts (< 0 dB) are listed in :attr:`notches`
    as pre-existing and are not counted against the budget until touched.
    """

    def __init__(
        self,
        cfg: DetectorConfig,
        geq_band_hz: Sequence[float],
        policy_validate: Callable[[float, float], None],
        *,
        budget: int,
        existing: dict[int, float] | None = None,
    ) -> None:
        self.cfg = cfg
        self.geq_band_hz: tuple[float, ...] = tuple(float(h) for h in geq_band_hz)
        if not self.geq_band_hz or any(h <= 0 for h in self.geq_band_hz):
            raise ValueError("geq_band_hz must be positive frequencies")
        self._log_hz = [math.log(h) for h in self.geq_band_hz]
        self._validate = policy_validate
        self.budget = int(budget)
        self._gains: dict[int, float] = {}
        self._notches: dict[int, Notch] = {}
        self._touched: set[int] = set()
        for band, db in (existing or {}).items():
            band = int(band)
            if not 1 <= band <= len(self.geq_band_hz):
                raise ValueError(f"existing notch band {band} out of range 1..{len(self.geq_band_hz)}")
            self._gains[band] = float(db)
            if db < 0:
                self._notches[band] = Notch(bus=0, band=band, freq_hz=self.geq_band_hz[band - 1],
                                            depth_db=float(db), session_id="", ts=0.0, detections=0)

    def band_for_freq(self, hz: float) -> int:
        """1-based GEQ band whose centre is nearest to ``hz`` in log-frequency."""
        if not hz > 0:
            raise ValueError(f"frequency must be > 0 Hz, got {hz!r}")
        lh = math.log(hz)
        return min(range(len(self._log_hz)), key=lambda i: abs(self._log_hz[i] - lh)) + 1

    def _can_deepen(self, band: int) -> bool:
        return self._gains.get(band, 0.0) > self.cfg.notch_max_db

    def _affordable(self, band: int) -> bool:
        return band in self._touched or self.budget_left > 0

    def plan(self, det: Detection, bus: int, session_id: str) -> Notch | None:
        """Decide the cut for ``det``: deepen the nearest notch within ``merge_adjacent_bands`` (exact band
        first), else open a new notch at the detection's band. Returns the updated :class:`Notch` or None
        when nothing can be done (budget spent, or the band is already at ``notch_max_db``)."""
        cfg = self.cfg
        target = self.band_for_freq(det.freq_hz)
        order = sorted(self._notches, key=lambda b: (abs(b - target), b))
        for band in order:
            if abs(band - target) > cfg.merge_adjacent_bands:
                break
            if self._can_deepen(band) and self._affordable(band):
                return self._apply(band, bus, session_id, det.ts)
        if not self._can_deepen(target) or not self._affordable(target):
            return None
        return self._apply(target, bus, session_id, det.ts)

    def _apply(self, band: int, bus: int, session_id: str, ts: float) -> Notch:
        current = self._gains.get(band, 0.0)
        new = max(self.cfg.notch_max_db, current + self.cfg.notch_step_db)
        self._validate(current, new)  # may raise (BOOST_FORBIDDEN / NOT_ALLOWED) — state untouched
        self._gains[band] = new
        self._touched.add(band)
        n = self._notches.get(band)
        if n is None:
            n = Notch(bus=bus, band=band, freq_hz=self.geq_band_hz[band - 1], depth_db=new,
                      session_id=session_id, ts=ts, detections=0)
            self._notches[band] = n
        n.bus = bus
        n.depth_db = new
        n.session_id = session_id
        n.ts = ts
        n.detections += 1
        log.debug("notch plan: bus %d band %d (%.0f Hz) %.1f -> %.1f dB", bus, band, n.freq_hz, current, new)
        return n

    @property
    def notches(self) -> list[Notch]:
        """All known cuts on the GEQ (pre-existing + this session), lowest band first."""
        return [self._notches[b] for b in sorted(self._notches)]

    @property
    def gains(self) -> dict[int, float]:
        """Known GEQ band gains (1-based band -> dB), including untouched pre-existing values."""
        return dict(self._gains)

    @property
    def touched_bands(self) -> set[int]:
        return set(self._touched)

    @property
    def budget_left(self) -> int:
        return max(0, self.budget - len(self._touched))

    @property
    def spent(self) -> bool:
        return self.budget_left <= 0
