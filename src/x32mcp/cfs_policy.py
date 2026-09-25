"""CFS² policy layer — configuration and the pure decision rules (docs/CFS_POLICY.md).

The detector (:mod:`x32mcp.detector`) decides what *is* feedback on evidence it can defend and deliberately leaves
POLICY to the session manager: MODERATE candidates it will not cut (tier B), the verdict it files after every cut
(``note_cut()`` → confirmed / insufficient / held / false_cut / ambiguous), the analyser flags and the ring-out
contract check (``programme_present()``). This module holds the knobs of that policy (``device.yaml cfs_policy:``,
every key equal to the dataclass default — ``tests/test_cfs_policy.py`` asserts it both ways) and the side-effect
free rules; :mod:`x32mcp.cfs` does the wiring (writes, alerts, report).

Nothing here touches the desk. Frequencies are Hz, levels dB (RTA dBFS), times seconds.
"""

from __future__ import annotations

import logging
import math
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from typing import Any, Iterable, Mapping, Sequence

log = logging.getLogger(__name__)

__all__ = [
    "LfEdgeConfig",
    "TierBConfig",
    "AlertsConfig",
    "BackoffProbeConfig",
    "CfsPolicyConfig",
    "MicHpf",
    "LfEdge",
    "lf_edge_from_hpfs",
    "tier_b_eligible",
    "at_arm_cut_allowed",
    "alert_worthy",
]


# ---------------------------------------------------------------------------------------------
# configuration (device.yaml ``cfs_policy:``)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LfEdgeConfig:
    """Item 1: the feedback window's low edge from the open mics' high-pass filters (REVIEW_REPORT §1.7 Q3)."""

    hpf_factor: float = 0.7       # edge_i = hpf_factor × the channel's HPF corner when its HPF is ON (a 24 dB/oct HPF is ~12 dB
                                  #   down there: the loop gain a ring needs is gone below it)
    no_hpf_hz: float = 100.0      # edge_i for an included mic whose HPF is OFF (vocal / instrument mics without an HPF still
                                  #   roll off; kick / sub paths are the operator's lf_feedback_possible declaration)
    floor_hz: float = 60.0        # session edge = max(floor_hz, min(edge_i)); below ~60 Hz the analyser needs > 0.35 s to settle


@dataclass(frozen=True)
class TierBConfig:
    """Item 3: the one-shot cut on a MODERATE line the detector will not cut by itself (docs/DETECTOR.md §4/§9, G7)."""

    enabled: bool = True
    min_excess_db: float = 20.0   # eligible when level >= det.loudish_threshold_db OR excess over the band's baseline >= this
    min_age_s: float = 0.6        # the line has existed this long (outlasts a soft attack + K1; a howl at its limiter does not care)
    cooldown_s: float = 2.0       # at most one NEW tier-B engagement per this many seconds (session-wide)
    held_deepen_s: float = 3.0    # 'held' after a tier-B cut and still a held BASE line this much later -> one more -3 dB (to -9 max)
    verdict_timeout_s: float = 4.0  # no verdict from the detector this long after a tier-B write: give the line up ('no_verdict')
    ringout_probe_wait_dwells: float = 2.0  # ring_out: a line still awaiting probe judgement (steps_seen < probe_min_hits) is cut by
                                  #   tier B only if loud-ish AND MODERATE for >= this many dwells (the probe is the better instrument)
    ringout_hold_raise: bool = True  # ring_out: do not raise the master while a tier-B engagement is unresolved
    ended_drop_db: float = 6.0    # a 'held' line whose track the detector dropped has ENDED (-> ignore-list) only if its band now
                                  #   reads at least this far under the held level; otherwise it was masked / lost (not ignore-listed)


@dataclass(frozen=True)
class AlertsConfig:
    """Item 4: candidate alerts that reach the desk (cfs.candidate on/off events + the bus scribble-strip colour)."""

    events: bool = True
    scribble_strip: bool = True
    color: str = "RDi"            # descriptor enum 'color': OFF RD GN YE BL MG CY WH OFFi RDi GNi YEi BLi MGi CYi WHi (i = inverted)
    clear_s: float = 2.0          # restore the strip colour once no alert-worthy candidate has been live for this long
    min_write_interval_s: float = 1.0  # never more than one colour write per this many seconds
    hold_s: float = 0.5           # a candidate's alert survives this long without its line (a masking transient) before 'off'


@dataclass(frozen=True)
class BackoffProbeConfig:
    """Item 6: the optional back-off probe on a STATIONARY 'backoff_advised' line in ring_out (docs/DETECTOR.md §3 PROBE, §11 N1)."""

    enabled: bool = True
    drop_db: float = 3.0          # lower the master this much for one dwell
    min_response_db: float = 4.5  # the line fell by MORE than this (or died) -> regenerative (1/(1-g)) -> cut; ~drop_db -> a room source
    settle_s: float = 0.3         # ignore this long after each master write before measuring


@dataclass(frozen=True)
class CfsPolicyConfig:
    """``device.yaml cfs_policy:`` (nested blocks ``lf_edge`` / ``tier_b`` / ``alerts`` / ``backoff_probe``)."""

    lf_edge: LfEdgeConfig = field(default_factory=LfEdgeConfig)
    programme_check_s: float = 5.0        # G8: programme_present() is evaluated over the first >= this many seconds of frames
    refresh_arm_reference_on_programme: bool = True   # watch: armed in silence and programme starts later -> det.refresh_arm_reference()
    tier_b: TierBConfig = field(default_factory=TierBConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    at_arm_watch_min_prominence_db: float = 30.0   # item 5: an AT-ARM line in watch is cut only if LOUD or at least this prominent
    reforce_ballistics_on_freeze: bool = True      # item 6: PEAK_HOLD_SUSPECTED / FROZEN_LINES -> write decay 0 / peakhold OFF once more ...
    frozen_reforce_s: float = 1.0                  # ... once the flag has stood this long (a live line's skirts can repeat a code briefly) ...
    frozen_abort_s: float = 5.0                    # ... and abort the session if the display still looks frozen this long after
    backoff_probe: BackoffProbeConfig = field(default_factory=BackoffProbeConfig)

    def __post_init__(self) -> None:
        def need(cond: bool, msg: str) -> None:
            if not cond:
                raise ValueError(f"CfsPolicyConfig: {msg}")

        need(0.0 < self.lf_edge.hpf_factor <= 2.0, "lf_edge.hpf_factor must be in (0, 2]")
        need(self.lf_edge.no_hpf_hz > 0 and self.lf_edge.floor_hz > 0, "lf_edge frequencies must be > 0")
        need(self.programme_check_s >= 0, "programme_check_s must be >= 0")
        need(self.tier_b.min_excess_db > 0, "tier_b.min_excess_db must be > 0")
        need(self.tier_b.min_age_s >= 0 and self.tier_b.cooldown_s >= 0 and self.tier_b.held_deepen_s >= 0,
             "tier_b times must be >= 0")
        need(self.tier_b.verdict_timeout_s > 0, "tier_b.verdict_timeout_s must be > 0")
        need(self.tier_b.ringout_probe_wait_dwells >= 0, "tier_b.ringout_probe_wait_dwells must be >= 0")
        need(self.tier_b.ended_drop_db > 0, "tier_b.ended_drop_db must be > 0")
        need(isinstance(self.alerts.color, str) and self.alerts.color.strip() != "", "alerts.color must be a colour token")
        need(self.alerts.clear_s >= 0 and self.alerts.min_write_interval_s >= 0 and self.alerts.hold_s >= 0, "alerts times must be >= 0")
        need(self.at_arm_watch_min_prominence_db >= 0, "at_arm_watch_min_prominence_db must be >= 0")
        need(self.frozen_abort_s > 0 and self.frozen_reforce_s >= 0, "frozen_abort_s must be > 0 and frozen_reforce_s >= 0")
        need(self.backoff_probe.drop_db > 0 and self.backoff_probe.min_response_db > self.backoff_probe.drop_db,
             "backoff_probe.min_response_db must exceed backoff_probe.drop_db (a 1 dB/dB source drops by exactly the back-off)")
        need(self.backoff_probe.settle_s >= 0, "backoff_probe.settle_s must be >= 0")

    # -- (de)serialisation ---------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "CfsPolicyConfig":
        """Build from a ``cfs_policy:`` mapping (missing keys take the defaults; unknown keys are an error: a typo in a
        safety policy must not be silently ignored)."""
        return _from_mapping(cls, data or {}, "cfs_policy")

    @classmethod
    def from_descriptor(cls, d: Any) -> "CfsPolicyConfig":
        return cls.from_dict(getattr(d, "cfs_policy", None) or {})

    def to_dict(self) -> dict[str, Any]:
        return _to_mapping(self)


def _coerce(typ: str, value: Any, path: str) -> Any:
    t = typ.replace("| None", "").replace("None |", "").strip()
    if "None" in typ and (value is None or (isinstance(value, str) and value.strip().lower() in ("", "none", "null"))):
        return None
    if t == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if t == "int":
        return int(value)
    if t == "float":
        return float(value)
    if t == "str":
        return str(value)
    raise ValueError(f"{path}: unsupported field type {typ!r}")


def _from_mapping(cls: Any, data: Mapping[str, Any], path: str) -> Any:
    if not isinstance(data, Mapping):
        raise ValueError(f"{path}: must be a mapping, got {type(data).__name__}")
    known = {f.name: f for f in fields(cls)}
    unknown = [k for k in data if k not in known]
    if unknown:
        raise ValueError(f"{path}: unknown key(s) {sorted(unknown)}")
    kw: dict[str, Any] = {}
    for name, f in known.items():
        if name not in data:
            continue
        value = data[name]
        factory = f.default_factory
        if factory is not MISSING and isinstance(factory, type) and is_dataclass(factory):
            kw[name] = _from_mapping(factory, {} if value is None else value, f"{path}.{name}")   # nested block
        else:
            kw[name] = _coerce(str(f.type), value, f"{path}.{name}")
    return cls(**kw)


def _to_mapping(obj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in fields(obj):
        v = getattr(obj, f.name)
        out[f.name] = _to_mapping(v) if is_dataclass(v) else v
    return out


# ---------------------------------------------------------------------------------------------
# item 1: LF edge from the open mics' HPFs
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MicHpf:
    """One included mic's high-pass filter as read from ``/ch/NN/preamp`` (``hpf_hz`` None = not read)."""

    ch: int
    hpf_on: bool | None
    hpf_hz: float | None


@dataclass(frozen=True)
class LfEdge:
    """The session's LF edge decision: ``hz`` (None = the detector's mode default), ``source`` (report text) and the
    per-mic edges it was taken over."""

    hz: float | None
    source: str
    per_mic: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"lf_edge_hz": self.hz, "from": self.source, "mics": [dict(m) for m in self.per_mic]}


def lf_edge_from_hpfs(mics: Sequence[MicHpf], cfg: LfEdgeConfig) -> LfEdge:
    """edge_i = ``hpf_factor`` × hpf when the mic's HPF is on, else ``no_hpf_hz``; session edge = max(``floor_hz``,
    min(edge_i)). No included mic -> None (the detector keeps its mode default). A mic whose preamp could not be read,
    or an input without a channel HPF (aux / USB, ch > 32), counts as 'HPF off' (the permissive reading: it can only
    lower the edge towards ``no_hpf_hz``)."""
    per: list[dict[str, Any]] = []
    best: tuple[float, str] | None = None
    for m in mics:
        if m.hpf_on and m.hpf_hz is not None and m.hpf_hz > 0:
            edge = cfg.hpf_factor * float(m.hpf_hz)
            why = f"ch {m.ch} HPF {float(m.hpf_hz):.0f} Hz"
        else:
            edge = float(cfg.no_hpf_hz)
            if int(m.ch) > 32:
                why = f"input {m.ch} has no HPF (aux/USB; {cfg.no_hpf_hz:g} Hz default)"
            elif m.hpf_on is not None:
                why = f"ch {m.ch} HPF off ({cfg.no_hpf_hz:g} Hz default)"
            else:
                why = f"ch {m.ch} HPF unread ({cfg.no_hpf_hz:g} Hz default)"
        per.append({"ch": m.ch, "hpf_on": m.hpf_on, "hpf_hz": None if m.hpf_hz is None else round(float(m.hpf_hz), 1), "edge_hz": round(edge, 1)})
        if best is None or edge < best[0]:
            best = (edge, why)
    if best is None:
        return LfEdge(None, "no included mic: detector mode default", tuple(per))
    edge, why = best
    if edge < cfg.floor_hz:
        return LfEdge(float(cfg.floor_hz), f"floor {cfg.floor_hz:g} Hz ({why})", tuple(per))
    return LfEdge(round(edge, 1), why, tuple(per))


# ---------------------------------------------------------------------------------------------
# items 3-5: candidate rules (duck-typed on detector.Candidate / Detection)
# ---------------------------------------------------------------------------------------------


def tier_b_eligible(
    c: Any, *, cfg: TierBConfig, loudish_db: float, mode: str, probe_min_hits: int, moderate_for_s: float, dwell_s: float,
    at_arm_suppressed: bool = False, frame_period_s: float = 0.05,
) -> tuple[bool, str]:
    """Is candidate ``c`` (a :class:`~x32mcp.detector.Candidate`) a tier-B case right now? Returns (eligible, why-not).
    The session-level gates (budget, band at notch_max, ignore list, cooldown, abort) are the caller's.

    A MODERATE line -- BASE with no evidence the detector may act on -- that is loud-ish (>= the detector's loud-ish
    line) or stands >= ``min_excess_db`` over its band's baseline, has existed AND been continuously present (the
    detector's presence run: it restarts when a note is re-struck onto the track) >= ``min_age_s``, carries no cut
    verdict, is not STATIONARY (followed the ring-out steps 1 dB/dB) -- gets ONE -3 dB cut whose verdict then drives
    the rest. A note re-struck every beat at one pitch keeps its track (and its age) but never its run.
    In watch an AT-ARM line that the at-arm policy declined to cut (``at_arm_suppressed``) is the same case. In ring_out
    a line the probe has not judged yet (steps_seen < probe_min_hits) waits for it unless it is loud-ish AND has been
    MODERATE for ``ringout_probe_wait_dwells`` dwells."""
    klass = getattr(c, "klass", None)
    if not (klass == "MODERATE" or (at_arm_suppressed and klass == "STRONG")):
        return False, f"klass {klass}"
    if getattr(c, "misses", 0):
        return False, "line not present this frame"
    if getattr(c, "stationary", False):
        return False, "stationary (followed the gain steps)"
    verdict = getattr(c, "cut_verdict", None)
    if verdict is not None and (verdict == "pending" or int(getattr(c, "emitted", 0) or 0) > 0):
        # a verdict on a line somebody CUT is that cut's business (the detector deepens its own, an open engagement follows
        # up its own). A line that was never emitted can still carry one: note_cut() judges every line within reach of a
        # bell, so a neighbour's cut files 'held' on a bystander -- typically the same howl after a mode hop, standing
        # 1/6 octave from the notch that was meant for it. That verdict says the neighbour's bell did not kill it, which
        # is no reason to leave it alone: once settled (not 'pending') it does not bar the line from its own engagement.
        return False, f"cut verdict {verdict}"
    if getattr(c, "false_cut", False):
        return False, "false_cut"
    level = float(getattr(c, "level_db", -128.0))
    excess = float(getattr(c, "excess_db", 0.0))
    loudish = level >= loudish_db
    if not (loudish or excess >= cfg.min_excess_db):
        return False, f"level {level:.1f} < loud-ish {loudish_db:.1f} and excess {excess:.1f} < {cfg.min_excess_db:g}"
    if float(getattr(c, "age_s", 0.0)) < cfg.min_age_s:
        return False, f"age {c.age_s:.2f} s < {cfg.min_age_s:g}"
    run_frames = getattr(c, "run_frames", None)
    if isinstance(run_frames, int) and not isinstance(run_frames, bool) and run_frames * float(frame_period_s) < cfg.min_age_s:
        return False, f"present run {run_frames * float(frame_period_s):.2f} s < {cfg.min_age_s:g} (re-struck / re-acquired)"
    if "no_family" not in tuple(getattr(c, "reasons", ()) or ()) and klass != "STRONG":
        return False, "family not ruled out yet"
    if mode == "ringout" and int(getattr(c, "steps_seen", 0)) < int(probe_min_hits):
        if not (loudish and moderate_for_s >= cfg.ringout_probe_wait_dwells * dwell_s):
            return False, "awaiting probe judgement"
    return True, ""


_OWN_EVIDENCE = ("rise", "fastrise", "probe", "growth")        # changes the line made on its own (Detection reason prefixes)
_PLATEAU_EVIDENCE = frozenset({"fastrise", "loud", "probe"})    # Candidate.emit_evidence names that earn a detector-side deepen


def at_arm_cut_allowed(det: Any, *, min_prominence_db: float, cand: Any = None) -> bool:
    """Item 5 (watch): a Detection carrying ``established_at_arm`` is cut only when it is tier A -- LOUD, or at least
    ``min_prominence_db`` prominent (the M7 60 dB howl) -- or when the detector also holds evidence the line made on its
    own since (RISE / FAST-RISE / PROBE: then the cut does not rest on the passive at-arm observation). A -36 dBFS whine,
    an organ note at -30 and an established quiet howl look the same at arm [DETECTOR §7.4]: those are alerted, not cut.

    A ``deepen_held`` / ``deepen_insufficient`` re-emission is the detector exercising a deepen right it earned from
    PLATEAU-CLASS evidence; it passes only when that evidence (``cand.emit_evidence``, the live Candidate the Detection is
    about) is FAST-RISE / LOUD / PROBE -- not the at-arm observation the watch declined (its deepening is the policy's
    ``held_deepen_s`` cadence, docs/CFS_POLICY.md §3/§5). Without ``cand`` a deepen re-emission is trusted."""
    reasons = tuple(getattr(det, "reasons", ()) or ())
    if "established_at_arm" not in reasons:
        return True
    if "loud" in reasons:
        return True
    if any(r.startswith(_OWN_EVIDENCE) for r in reasons):
        return True
    if any(r.startswith("deepen_") for r in reasons):
        if cand is None:
            return True
        if _PLATEAU_EVIDENCE & set(getattr(cand, "emit_evidence", ()) or ()):
            return True
    return float(getattr(det, "prominence_db", 0.0)) >= float(min_prominence_db)


def alert_worthy(c: Any, *, k1_s: float, engaged: bool = False, at_arm_suppressed: bool = False) -> str | None:
    """Item 4: which live candidates the operator should hear about. Returns the alert class or None:
    ``MODERATE`` (BASE line the detector will not cut, aged >= K1), ``HELD`` (a cut landed and the line dropped by the
    bell and stayed: a note through the EQ or a limiter-held howl), ``STATIONARY`` (followed the steps; with
    ``backoff_advised`` a back-off probe is due), ``AT_ARM`` (watch declined to cut an at-arm line), ``TIER_B`` (a policy
    engagement is open on it), ``FALSE_CUT`` is NOT alert-worthy (the line ended: nothing to do)."""
    if getattr(c, "misses", 0):
        return None
    klass = getattr(c, "klass", None)
    verdict = getattr(c, "cut_verdict", None)
    reasons = tuple(getattr(c, "reasons", ()) or ())
    if klass == "FALSE_CUT":
        return None
    if verdict == "held":
        return "HELD"
    if engaged:
        return "TIER_B"
    if at_arm_suppressed and klass in ("STRONG", "MODERATE"):
        return "AT_ARM"
    if klass == "STATIONARY" and "backoff_advised" in reasons:
        return "STATIONARY"
    if klass == "MODERATE" and float(getattr(c, "age_s", 0.0)) >= k1_s:
        return "MODERATE"
    return None


def geq_band_for(freq_hz: float, geq_band_hz: Sequence[float]) -> int:
    """1-based GEQ band nearest ``freq_hz`` in log-frequency (same rule as NotchController.band_for_freq)."""
    lh = math.log(max(1e-9, float(freq_hz)))
    return min(range(len(geq_band_hz)), key=lambda i: abs(math.log(geq_band_hz[i]) - lh)) + 1


def candidate_brief(c: Any, alert: str | None = None) -> dict[str, Any]:
    """Compact dict of a Candidate for events / status / report."""
    d = {
        "freq_hz": round(float(getattr(c, "freq_hz", 0.0)), 1), "band": int(getattr(c, "band", 0)), "klass": getattr(c, "klass", None),
        "level_db": round(float(getattr(c, "level_db", -128.0)), 1), "prominence_db": round(float(getattr(c, "prominence_db", 0.0)), 1),
        "excess_db": round(float(getattr(c, "excess_db", 0.0)), 1), "age_s": round(float(getattr(c, "age_s", 0.0)), 2),
        "reasons": list(getattr(c, "reasons", ()) or ()), "cut_verdict": getattr(c, "cut_verdict", None),
        "stationary": bool(getattr(c, "stationary", False)), "confidence": round(float(getattr(c, "confidence", 0.0)), 3),
    }
    rf = getattr(c, "run_frames", None)
    if isinstance(rf, int) and not isinstance(rf, bool):
        d["run_frames"] = rf
    if alert is not None:
        d["alert"] = alert
    return d


def flags_of(det: Any) -> set[str]:
    return set(getattr(det, "flags", ()) or ())


def first(items: Iterable[Any], default: Any = None) -> Any:
    for x in items:
        return x
    return default
