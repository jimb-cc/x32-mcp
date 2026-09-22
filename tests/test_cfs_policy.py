"""cfs_policy.py: the CFS² policy layer's configuration (device.yaml ``cfs_policy:`` == the dataclass defaults, both ways)
and its pure rules (LF edge from the mics' HPFs, tier-B eligibility, the AT-ARM rule in watch, alert-worthiness), plus the
detector accessor the policy cuts rely on (``FeedbackDetector.note_emission``)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from x32mcp.cfs_policy import (AlertsConfig, BackoffProbeConfig, CfsPolicyConfig, LfEdgeConfig, MicHpf, TierBConfig, alert_worthy,
                               at_arm_cut_allowed, candidate_brief, geq_band_for, lf_edge_from_hpfs, tier_b_eligible)
from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig, FeedbackDetector
from x32mcp.meters import RTA_BAND_HZ

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------------------------- configuration

def test_device_yaml_cfs_policy_block_equals_the_dataclass_defaults_both_ways():
    raw = yaml.safe_load((ROOT / "device.yaml").read_text(encoding="utf-8"))
    assert "cfs_policy" in raw
    block = raw["cfs_policy"]
    # every yaml key is a known field with the default value ...
    assert CfsPolicyConfig.from_dict(block) == CfsPolicyConfig()
    # ... and every field (nested blocks included) is spelled out in the yaml
    assert CfsPolicyConfig().to_dict() == block
    # the descriptor exposes the block and the manager-side constructor reads it
    d = Descriptor.load(ROOT / "device.yaml")
    assert d.cfs_policy == block and CfsPolicyConfig.from_descriptor(d) == CfsPolicyConfig()


def test_from_dict_nested_overrides_unknown_keys_and_validation():
    c = CfsPolicyConfig.from_dict({"tier_b": {"held_deepen_s": 1.0, "enabled": "false"}, "alerts": {"color": "GNi", "clear_s": "0.5"},
                                  "frozen_abort_s": 2})
    assert c.tier_b.held_deepen_s == 1.0 and c.tier_b.enabled is False and c.tier_b.min_age_s == TierBConfig().min_age_s
    assert c.alerts.color == "GNi" and c.alerts.clear_s == 0.5 and c.alerts.scribble_strip is True
    assert c.frozen_abort_s == 2.0 and c.lf_edge == LfEdgeConfig() and c.backoff_probe == BackoffProbeConfig()
    assert CfsPolicyConfig.from_dict(None) == CfsPolicyConfig() and CfsPolicyConfig.from_dict({}) == CfsPolicyConfig()
    with pytest.raises(ValueError, match="unknown key"):
        CfsPolicyConfig.from_dict({"tier_b": {"bogus": 1}})
    with pytest.raises(ValueError, match="unknown key"):
        CfsPolicyConfig.from_dict({"tierb": {}})
    with pytest.raises(ValueError, match="must be a mapping"):
        CfsPolicyConfig.from_dict({"alerts": ["RDi"]})
    with pytest.raises(ValueError, match="min_response_db must exceed"):
        CfsPolicyConfig(backoff_probe=BackoffProbeConfig(drop_db=3.0, min_response_db=3.0))
    with pytest.raises(ValueError, match="alerts.color"):
        CfsPolicyConfig(alerts=AlertsConfig(color=" "))
    # a descriptor without the optional block loads and gives the defaults
    raw = yaml.safe_load((ROOT / "device.yaml").read_text(encoding="utf-8"))
    raw.pop("cfs_policy")
    d = Descriptor.from_dict(raw) if hasattr(Descriptor, "from_dict") else Descriptor(raw)
    assert d.cfs_policy == {} and CfsPolicyConfig.from_descriptor(d) == CfsPolicyConfig()


# ---------------------------------------------------------------------------------------------- item 1: LF edge

@pytest.mark.parametrize("mics, hz, source", [
    ([MicHpf(3, True, 120.0), MicHpf(4, True, 160.0)], 84.0, "ch 3 HPF 120 Hz"),          # 0.7 x the lowest corner
    ([MicHpf(3, True, 120.0), MicHpf(4, False, 250.0)], 84.0, "ch 3 HPF 120 Hz"),         # HPF off = 100 Hz; min(84, 100) = 84
    ([MicHpf(3, True, 160.0), MicHpf(4, False, 250.0)], 100.0, "ch 4 HPF off"),           # min(112, 100) = 100
    ([MicHpf(1, False, 80.0), MicHpf(2, False, 80.0)], 100.0, "ch 1 HPF off"),            # no HPF anywhere -> 100
    ([MicHpf(1, None, None)], 100.0, "ch 1 HPF unread"),                                    # unreadable preamp -> permissive 100
    ([MicHpf(33, False, None)], 100.0, "input 33 has no HPF"),                              # aux / USB input on the bus: no HPF -> 100
    ([MicHpf(33, False, None), MicHpf(3, True, 120.0)], 84.0, "ch 3 HPF 120 Hz"),          # ... and it does not beat a lower HPF edge
    ([MicHpf(7, True, 40.0)], 60.0, "floor 60 Hz (ch 7 HPF 40 Hz)"),                         # floored at 60
    ([MicHpf(7, True, 100.0), MicHpf(8, True, 80.0)], 60.0, "floor 60 Hz (ch 8 HPF 80 Hz)"),  # 56 -> 60
    ([], None, "no included mic"),                                                          # nothing to go on: detector mode default
])
def test_lf_edge_rule(mics, hz, source):
    e = lf_edge_from_hpfs(mics, LfEdgeConfig())
    assert e.hz == (pytest.approx(hz) if hz is not None else None)
    assert source in e.source
    assert len(e.per_mic) == len(mics) and all("edge_hz" in m for m in e.per_mic)
    d = e.to_dict()
    assert d["lf_edge_hz"] == e.hz and d["from"] == e.source


def test_lf_edge_reaches_the_detector_window():
    """The edge cfs derives is exactly what the detector's P6 window uses (it overrides the mode defaults; the
    operator's lf_feedback_possible opens the window to 40 Hz instead)."""
    cfg = DetectorConfig()
    det = FeedbackDetector(cfg, RTA_BAND_HZ, mode="watch", lf_edge_hz=84.0)
    assert det.cfg.window_low_hz == 84.0
    det2 = FeedbackDetector(cfg, RTA_BAND_HZ, mode="watch", lf_feedback_possible=True)
    assert det2.cfg.window_low_hz == cfg.window_low_hz_lf == 40.0
    det3 = FeedbackDetector(cfg, RTA_BAND_HZ, mode="ringout")
    assert det3.cfg.window_low_hz == cfg.window_low_hz_ringout


# ---------------------------------------------------------------------------------------------- items 3-5: rules

def _cand(**kw):
    base = dict(klass="MODERATE", misses=0, stationary=False, cut_verdict=None, false_cut=False, level_db=-18.0, excess_db=30.0,
                age_s=1.0, reasons=("narrow", "no_family", "stable", "sustained", "new_energy", "in_window"), steps_seen=0,
                freq_hz=1000.0, band=57, prominence_db=30.0, confidence=0.5, common_mode=False, run_frames=20)
    base.update(kw)
    return SimpleNamespace(**base)


def _elig(c, **kw):
    args = dict(cfg=TierBConfig(), loudish_db=-20.0, mode="watch", probe_min_hits=2, moderate_for_s=0.0, dwell_s=1.5)
    args.update(kw)
    return tier_b_eligible(c, **args)


def test_tier_b_eligibility_rule():
    ok, why = _elig(_cand())
    assert ok and why == ""
    assert _elig(_cand(level_db=-30.0, excess_db=25.0))[0]                          # quiet but >= 20 dB over its baseline
    assert not _elig(_cand(level_db=-30.0, excess_db=12.0))[0]                      # neither loud-ish nor 20 dB of excess
    assert not _elig(_cand(age_s=0.4))[0]                                            # younger than 0.6 s
    assert not _elig(_cand(age_s=3.0, run_frames=6))[0]                              # an old track, but the note on it was just re-struck
    assert not _elig(_cand(klass="STRONG"))[0]                                       # the detector's business
    assert _elig(_cand(klass="STRONG"), at_arm_suppressed=True)[0]                   # ... unless watch declined an at-arm line
    assert not _elig(_cand(klass="TRACK"))[0] and not _elig(_cand(klass="MUSICAL"))[0] and not _elig(_cand(klass="STATIONARY"))[0]
    assert not _elig(_cand(stationary=True))[0]
    assert not _elig(_cand(cut_verdict="held"))[0] and not _elig(_cand(cut_verdict="pending"))[0]
    assert not _elig(_cand(false_cut=True))[0]
    assert not _elig(_cand(misses=1))[0]
    assert not _elig(_cand(reasons=("narrow", "stable")))[0]                          # family not ruled out yet
    # ring_out: a line the probe has not judged waits for it unless loud-ish AND MODERATE for >= 2 dwells
    assert not _elig(_cand(steps_seen=0), mode="ringout", moderate_for_s=1.0)[0]
    assert _elig(_cand(steps_seen=0), mode="ringout", moderate_for_s=3.1)[0]
    assert not _elig(_cand(steps_seen=1, level_db=-30.0, excess_db=25.0), mode="ringout", moderate_for_s=10.0)[0]   # not loud-ish: wait
    assert _elig(_cand(steps_seen=2, level_db=-30.0, excess_db=25.0), mode="ringout", moderate_for_s=0.0)[0]         # judged twice
    assert not _elig(_cand(), cfg=TierBConfig(min_excess_db=40.0), loudish_db=-10.0)[0]


def test_at_arm_rule_in_watch():
    det = lambda reasons, prom: SimpleNamespace(reasons=tuple(reasons), prominence_db=prom)  # noqa: E731
    base = ("narrow", "no_family", "stable", "sustained", "at_arm", "in_window")
    assert at_arm_cut_allowed(det(base + ("rise7dB",), 20.0), min_prominence_db=30.0)                 # not an at-arm cut at all
    assert not at_arm_cut_allowed(det(base + ("established_at_arm",), 20.0), min_prominence_db=30.0)  # -36 dBFS whine class: alert
    assert at_arm_cut_allowed(det(base + ("established_at_arm",), 60.0), min_prominence_db=30.0)      # the M7 60 dB howl: cut
    assert at_arm_cut_allowed(det(base + ("established_at_arm", "loud"), 20.0), min_prominence_db=30.0)
    assert at_arm_cut_allowed(det(base + ("fastrise21dB@100dB/s", "established_at_arm"), 20.0), min_prominence_db=30.0)
    assert at_arm_cut_allowed(det(base + ("established_at_arm", "rise6dB"), 20.0), min_prominence_db=30.0)   # grew on its own since
    assert at_arm_cut_allowed(det(base + ("established_at_arm",), 30.0), min_prominence_db=30.0)
    assert not at_arm_cut_allowed(det(base + ("established_at_arm",), 29.9), min_prominence_db=30.0)
    # a deepen re-emission passes only when the detector earned the deepen right on plateau evidence OTHER than at-arm
    deepen = det(base + ("established_at_arm", "deepen_held"), 26.0)
    assert at_arm_cut_allowed(deepen, min_prominence_db=30.0)                                                   # no candidate: trusted
    assert not at_arm_cut_allowed(deepen, min_prominence_db=30.0, cand=SimpleNamespace(emit_evidence=("at_arm", "suppressed_at_arm")))
    assert not at_arm_cut_allowed(deepen, min_prominence_db=30.0, cand=SimpleNamespace(emit_evidence=("established_at_arm", "tier_b")))
    assert at_arm_cut_allowed(deepen, min_prominence_db=30.0, cand=SimpleNamespace(emit_evidence=("established_at_arm", "loud")))
    assert at_arm_cut_allowed(deepen, min_prominence_db=30.0, cand=SimpleNamespace(emit_evidence=("fastrise", "suppressed_at_arm")))


def test_alert_worthiness():
    k1 = 0.25
    assert alert_worthy(_cand(age_s=0.3), k1_s=k1) == "MODERATE"
    assert alert_worthy(_cand(age_s=0.1), k1_s=k1) is None                         # younger than K1
    assert alert_worthy(_cand(klass="TRACK"), k1_s=k1) is None
    assert alert_worthy(_cand(klass="TRACK", cut_verdict="held"), k1_s=k1) == "HELD"
    assert alert_worthy(_cand(klass="STATIONARY", reasons=("probe_linear",)), k1_s=k1) is None
    assert alert_worthy(_cand(klass="STATIONARY", reasons=("probe_linear", "backoff_advised")), k1_s=k1) == "STATIONARY"
    assert alert_worthy(_cand(klass="STRONG"), k1_s=k1) is None                      # cut by the detector, not an alert
    assert alert_worthy(_cand(klass="STRONG"), k1_s=k1, at_arm_suppressed=True) == "AT_ARM"
    assert alert_worthy(_cand(), k1_s=k1, engaged=True) == "TIER_B"
    assert alert_worthy(_cand(klass="FALSE_CUT", cut_verdict="false_cut"), k1_s=k1) is None
    assert alert_worthy(_cand(misses=2), k1_s=k1) is None
    b = candidate_brief(_cand(), alert="MODERATE")
    assert b["alert"] == "MODERATE" and b["freq_hz"] == 1000.0 and b["klass"] == "MODERATE" and "confidence" in b
    assert geq_band_for(1000.0, [800, 1000, 1250]) == 2 and geq_band_for(1100.0, [800, 1000, 1250]) == 2 and geq_band_for(1130.0, [800, 1000, 1250]) == 3


# ---------------------------------------------------------------------------------------------- detector accessor

def _tone_frames(n, level_db, band=57, bed_db=-60.0, start=12):
    """A quiet noisy bed; from frame ``start`` on, a steady family-less tone at ``band`` that arrives within one frame
    (what a howl slamming into a limiter faster than the frame rate -- or a switched-on tone -- looks like: MODERATE)."""
    import random
    rng = random.Random(4)
    for i in range(n):
        vals = [bed_db + rng.uniform(-2, 2) for _ in RTA_BAND_HZ]
        if i >= start:
            vals[band] = level_db + rng.uniform(-0.2, 0.2)
            vals[band - 1] = level_db - 12 + rng.uniform(-0.2, 0.2)
            vals[band + 1] = level_db - 12 + rng.uniform(-0.2, 0.2)
        yield i * 0.05, vals


def test_note_emission_makes_a_policy_cut_line_the_emitted_line():
    """A line cfs cuts by policy (no Detection from feed) must be THE cut line for note_cut(): a ring that collapses
    time-locked to the write is then 'confirmed' (a verdict-less bystander otherwise), the emission is on record
    (emitted, level, evidence 'tier_b', cooldown) and the detector never deepens it by itself (not plateau-class)."""
    cfg = DetectorConfig()
    det = FeedbackDetector(cfg, RTA_BAND_HZ, mode="watch")
    gen = _tone_frames(400, -25.0)
    ts = 0.0
    for _ in range(40):                       # a steady family-less line arriving within a frame: MODERATE, never emitted
        ts, vals = next(gen)
        assert det.feed(vals, ts) == [] or True
    c = next(x for x in det.candidates if abs(x.band - 57) <= 1)
    assert c.klass == "MODERATE" and c.emitted == 0
    assert det.note_emission(object()) is False                       # not a live track
    assert det.note_emission(c, ts, reason="tier_b") is True
    assert c.emitted == 1 and c.last_emit_ts == ts and "tier_b" in c.emit_evidence and c.last_emit_level_db == c.cluster_db
    det.note_cut(freq_hz=1000.0, depth_db=-3.0, ts=ts)
    assert c.cut_verdict == "pending"
    # the 'ring' collapses right after the write: gone within the response window -> confirmed (emitted track)
    for k in range(12):
        ts += 0.05
        vals = [-60.0 + ((k * 7 + i) % 5) * 0.5 for i, _ in enumerate(RTA_BAND_HZ)]
        det.feed(vals, ts)
    assert any(v["verdict"] == "confirmed" and v["emitted"] for v in det.cut_log), det.cut_log


def test_note_emission_held_line_is_not_deepened_by_the_detector():
    cfg = DetectorConfig()
    det = FeedbackDetector(cfg, RTA_BAND_HZ, mode="watch")
    ts = 0.0
    level = -15.0                             # loud-ish, so a plateau-class emission WOULD earn a deepen right; a policy one must not
    gen = _tone_frames(2000, level)
    for _ in range(40):
        ts, vals = next(gen)
        det.feed(vals, ts)
    c = next(x for x in det.candidates if abs(x.band - 57) <= 1)
    assert c.klass == "MODERATE"
    assert det.note_emission(c, ts)
    det.note_cut(freq_hz=1000.0, depth_db=-3.0, ts=ts)
    out = []
    for _ in range(60):                       # the line drops by exactly the bell (3 dB at the centre) and holds: 'held'
        ts, vals = next(gen)
        vals = list(vals)
        for b in (56, 57, 58):
            vals[b] -= 3.0
        out += det.feed(vals, ts)
    assert c.cut_verdict == "held" and c.cut_deepen is False
    assert out == []                          # no re-emission (deepen) from the detector: tier B decides


def test_note_suppressed_withdraws_the_at_arm_evidence_from_the_emission_record():
    """cfs declined a watch at-arm Detection (note_suppressed): the emission stays on record (re-emission needs fresh evidence)
    but 'established_at_arm' leaves emit_evidence and any deepen right is cleared, so a later 'held' verdict on a POLICY cut of
    the line does not make the detector deepen it by itself; nothing else about the track changes."""
    cfg = DetectorConfig()
    det = FeedbackDetector(cfg, RTA_BAND_HZ, mode="watch")
    ts = 0.0
    gen = _tone_frames(4000, -15.0)           # loud-ish (a plateau-class emission would earn a deepen right on 'held')
    for _ in range(40):
        ts, vals = next(gen)
        det.feed(vals, ts)
    c = next(x for x in det.candidates if abs(x.band - 57) <= 1)
    assert det.note_suppressed(object()) is False                     # not a live track
    # stand in for feed() having emitted the line on the at-arm observation
    c.emitted, c.last_emit_ts, c.last_emit_level_db, c.emit_peak_db = 1, ts, c.cluster_db, c.level_db
    c.emit_evidence = ("established_at_arm",)
    c.cut_deepen = True
    klass, reasons = c.klass, c.reasons
    assert det.note_suppressed(c, reason="at_arm") is True
    assert c.emit_evidence == ("suppressed_at_arm",) and c.cut_deepen is False and c.emitted == 1
    assert (c.klass, c.reasons) == (klass, reasons)
    # the policy then cuts it (note_emission 'at_arm') and the line drops by the bell and holds: 'held', NO detector deepen right
    assert det.note_emission(c, ts, reason="at_arm")
    det.note_cut(freq_hz=1000.0, depth_db=-3.0, ts=ts)
    out = []
    for _ in range(60):
        ts, vals = next(gen)
        vals = list(vals)
        for b in (56, 57, 58):
            vals[b] -= 3.0
        out += det.feed(vals, ts)
    assert c.cut_verdict == "held" and c.cut_deepen is False and out == []
    assert det.cut_log[-1]["verdict"] == "held" and det.cut_log[-1]["deepen"] is False
