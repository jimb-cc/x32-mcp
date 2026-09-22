"""Regression tests on rendered corpus scenarios (tests/rtasim): the judge's list for the fixes and grafts of the final
CFS² discriminator (docs/DETECTOR.md). Each renders 1-3 seeds of one scenario (~0.2 s each) and runs the shipped
detector exactly as cfs would (mode from the scenario, master steps fed to note_gain_step in ring_out, LF window per the
scene's declaration)."""

from __future__ import annotations

import pytest

from x32mcp.descriptor import Descriptor
from x32mcp.detector import DetectorConfig

from rtasim import SCENARIOS, evaluate
from rtasim.physics import RTA_BAND_HZ
from rtasim.scenarios_adversarial import ADVERSARIAL
from rtasim.run_detector_eval import candidate_latency, make_factory


@pytest.fixture(scope="module")
def cfg() -> DetectorConfig:
    return DetectorConfig.from_descriptor(Descriptor.load())


def _eval(cfg, name: str, mode: str, seeds=(1, 2, 3)):
    sc = SCENARIOS.get(name) or ADVERSARIAL[name]
    fac, c = make_factory(cfg, sc, mode)
    return evaluate(fac, [sc], seeds=seeds, notch_cfg=c)


def test_x11_amp_clipped_howl_is_caught_within_300ms_on_every_seed(cfg):
    """G1 (back-filled FAST-RISE): the 150 dB/s amp-clipped howl at -12 dBFS with acoustic harmonics -- 0/3 for the
    competition winner -- is caught on all three seeds within the 300 ms budget."""
    res = _eval(cfg, "X11_amp_clipped_howl_minus12dBFS", "watch_tag")
    t = res.totals()
    assert (t["tp"], t["miss"], t["fp"]) == (3, 0, 0)
    assert t["lat_max"] <= 300.0


def test_f1_an_organ_note_sounding_at_arm_that_recurs_is_not_established_at_arm(cfg):
    """F1: AP02 (auditor Y2) -- the note present at arm ends and the melody returns to the same pitch at 3.0 and 7.0 s;
    the winner re-associated the returning line with the at-arm one and cut it (15 FP). A later line is a new source."""
    t = _eval(cfg, "AP02_organ_note_at_arm_recurs", "watch_tag").totals()
    assert t["fp"] == 0


def test_g4_ringout_whine_present_at_arm_is_judged_by_the_probe_not_cut(cfg):
    """G4: AF05 (auditor Y5) -- ring_out, no programme, a -36 dBFS projector whine from before arm that follows the
    server's +1 dB steps 1 dB/dB: STATIONARY, 0 detections (winner: 'established_at_arm' 250 ms after arm, 39-42 FP)."""
    t = _eval(cfg, "AF05_ringout_projector_whine_minus36", "ringout_tag").totals()
    assert t["fp"] == 0


def test_f3_a_2dB_per_second_ring_is_reachable(cfg):
    """F3 (age-unbounded low-water mark): AP07 (auditor Y7) -- 0.02 dB excess on a 10 ms loop creeps at 2 dB/s to a
    -20 dBFS plateau; with the 64-frame rise horizon it was unreachable in every mode. It is now detected on every
    seed (6/R: ~3 s of creep after visibility; the 1 s budget is met on one seed -- stated in DETECTOR.md)."""
    t = _eval(cfg, "AP07_very_slow_ring_2dB_s", "watch_tag").totals()
    assert (t["tp"], t["miss"], t["fp"]) == (3, 0, 0)
    assert t["lat_max"] <= 1600.0


@pytest.mark.parametrize("name", [
    "AM01_fast_howl_limiter_plateau_minus14",      # 417 dB/s -> -14 dBFS limiter plateau (auditor A1)
    "AT02_fast_wedge_ring_600dBps_plateau_m15",     # 600 dB/s -> -15 (auditor B2)
    "AF03_mic_cupped_wedge_howl_plateau_m13",       # 800 dB/s -> -13 (auditor Y3)
    "AS02_cupped_mic_howl_compressor_minus22",      # 1200 dB/s -> -22 compressor plateau (auditor A2)
])
def test_tier_b_fast_howl_to_a_quiet_plateau_is_published_as_moderate_within_250ms(cfg, name):
    """The fast howl to a sub -10 dBFS limiter/compressor plateau (<= 2 visible increments) is passively identical to a
    sine-lead note onset [judge §5.1]: the detector does not cut it, but publishes it as a MODERATE candidate within
    K1 = 250 ms (+ frame jitter) of visibility with the fields cfs needs for the tier-B one-shot policy."""
    sc = ADVERSARIAL[name]
    pub = candidate_latency(cfg, sc, (1, 2, 3))
    ms = [m for r in pub["runs"] for m in r["published_ms"]]
    assert all(r["fields_ok"] for r in pub["runs"])
    assert all(m is not None for m in ms), ms
    assert sorted(ms)[len(ms) // 2] <= 255.0, ms          # median seed within K1 (+3 ms frame jitter)
    assert max(ms) <= 450.0, ms                            # worst seed: a coincidental family veto costs a few frames (AS02 s2)


def test_lf_howl_pinned_at_the_clip_flag_inside_a_declared_lf_window_is_cut(cfg):
    """AF14 (auditor Y14): an un-HPF'd mic howls at 101 Hz into DESK CLIP (0.0) for 6 s; with the LF window declared the
    clip flag is LOUD evidence below 160 Hz too (a kick or an 808 does not sit at 0.0 with a stable centroid)."""
    t = _eval(cfg, "AF14_lf_howl_to_desk_clip_100Hz", "watch_tag").totals()
    assert (t["tp"], t["miss"], t["fp"]) == (3, 0, 0)


def test_established_edge_ring_at_arm_next_to_an_organ_harmonic_slot(cfg):
    """AS08 (auditor A8): an established -14 dBFS ring exactly on the 64/65 band edge, 53 cents from the H3 slot of an
    organ note 18 dB quieter: a line that far ABOVE its would-be fundamental is not its harmonic -> caught at K1; the
    bus-master -20/+20 dB episode regrows and is caught again."""
    t = _eval(cfg, "AS08_edge_ring_busmaster_drop20", "watch_tag").totals()
    assert (t["tp"], t["miss"], t["fp"]) == (6, 0, 0)


# -- verifier findings on the final integration (B1-B3, blocking #4) ---------------------------------------------

def test_b1_at_arm_evidence_is_not_inherited_by_a_note_that_starts_after_arm(cfg):
    """B1: AV03 -- a staccato organ note sounding at arm re-struck at the same pitch after a 0.3 s rest, and fresh notes
    starting 0.35-0.7 s after arm over a humpy bed, inherited 'born at arm' from coasting at-arm tracks and were cut as
    'established_at_arm' (5 FP / 9 seeds); X1 seed 11 (an organ note landing on an at-arm bed bump) likewise. A run restart
    (onset jump / long gap) now clears born_at_arm: what is on the track afterwards was seen to start."""
    assert _eval(cfg, "AV03_organ_notes_start_within_1s_of_arm", "watch_tag", seeds=(1, 2, 4, 7, 8)).totals()["fp"] == 0
    assert _eval(cfg, "X1_organ_held_notes", "watch_tag", seeds=(11,)).totals()["fp"] == 0
    # the at-arm TPs are untouched
    t = _eval(cfg, "X7_plateaued_ring_under_music_from_t0", "watch_tag", seeds=(1,)).totals()
    assert (t["tp"], t["fp"]) == (1, 0) and t["lat_max"] <= 1000.0


def test_b2_fast_rise_needs_three_observed_increments_unless_the_line_is_loudish(cfg):
    """B2: the step out of the bed is an inferred increment; with it two OBSERVED increments (100-150 ms of climb = a soft /
    breath attack too) sufficed. Below the loud-ish line (max(-20 dBFS, arm p95 + 10)) three observed increments are needed:
    AV02's flute breath attacks (43 FP / 15 seeds) drop to the >= 150 ms-attack residue, the strummed-choir partial of X21
    (hold-out s6/s11) and AV01 s6 are gone, and the loud-ish howls that show only two increments (M1, X11, X14) keep theirs."""
    assert _eval(cfg, "AV02_flute_soft_attacks_120ms", "watch_tag", seeds=(2, 3)).totals()["fp"] == 0
    assert _eval(cfg, "AV01_voice_chord_onsets_strummed", "watch_tag", seeds=(6,)).totals()["fp"] == 0
    x21 = _eval(cfg, "X21_reverberant_area_mic_slow_ring", "watch_tag", seeds=(6, 11)).totals()
    assert x21["fp"] == 0 and x21["tp"] == 2
    for name in ("M1_loud_band_wedge_ring", "X11_amp_clipped_howl_minus12dBFS", "AS03_amp_clipped_howl_minus24dBFS"):
        t = _eval(cfg, name, "watch_tag").totals()
        assert (t["tp"], t["miss"], t["fp"]) == (3, 0, 0), name
        assert t["lat_max"] <= 300.0, name


def test_b3_limiter_held_howl_with_excess_above_the_bell_is_deepened_and_killed_in_watch(cfg):
    """B3: AV05 -- +4 dB excess, 180 dB/s to a -16 dBFS limiter plateau at a GEQ centre: the first -3 dB cut takes the tap down by
    exactly the bell while the loop howls on (e = +1). note_cut() now files 'held' (not 'false_cut'); the line was cut on
    FAST-RISE (plateau-class evidence) and is loud-ish, so ONE deepen is re-emitted per verdict: -6 dB kills it. AV06 (GEQ
    midpoint, cut lands mid-climb) is killed via regrowth; in both the ring must be dead at the end of the closed-loop run."""
    for name in ("AV05_howl_e4_limiter_on_geq_centre_watch", "AV06_howl_geq_midpoint_e2p5_limiter_watch"):
        sc = ADVERSARIAL[name]
        fac, c = make_factory(cfg, sc, "watch_tag")
        res = evaluate(fac, [sc], seeds=(1, 2, 3), closed_loop=True, notch_cfg=c)
        t = res.totals()
        assert (t["tp"], t["miss"], t["fp"], t["alive"]) == (3, 0, 0, 0), (name, t)
        for run in res.runs:
            assert len(run.cuts) == 2 and run.cuts[-1][2] == -6.0, (name, run.seed, run.cuts)


def test_b3_a_marginal_ring_the_cut_just_kills_is_neither_false_cut_nor_deepened(cfg):
    """AV07: e = 2.85 dB on a 70 ms reverberant loop; after -3 dB it rings DOWN slowly (T60 ~ 28 s). One cut, no 'false_cut'
    verdict (that would tell cfs to release the band and bring the ring straight back), ring not alive at the end."""
    sc = ADVERSARIAL["AV07_marginal_ring_e2p85_reverberant_cut"]
    fac, c = make_factory(cfg, sc, "watch_tag")
    res = evaluate(fac, [sc], seeds=(1, 2, 3), closed_loop=True, notch_cfg=c)
    t = res.totals()
    assert (t["tp"], t["fp"], t["alive"]) == (3, 0, 0), t
    assert all(len(run.cuts) == 1 for run in res.runs)


def test_loud_show_howl_near_full_scale_is_loud_and_a_whistle_below_the_arm_maximum_is_not(cfg):
    """Blocking #4 (G6 + HOT): in a show whose arm p95 is -14..-20 dBFS the LOUD line max(-10, p95 + 20) was unreachable and
    HOT_SPECTRUM disabled the clip flag too, so a 240 dB/s howl reaching -3 dBFS (X16, X14 hold-out) was tier-B only. The
    absolute leg now stops rising at loud_ceiling_db -6, and under HOT the line must also stand 3 dB above the loudest arm-window
    cell: X16 hold-out and X14 hold-out are cut at K1, while AM03's whistles (-4..-2 dBFS in a show that reached -5 during the arm
    window) do not clear the arm maximum and stay MODERATE."""
    t = _eval(cfg, "X16_wedge_ring_315Hz_loud_band", "watch_tag", seeds=(4, 5, 6)).totals()
    assert (t["tp"], t["miss"], t["fp"]) == (3, 0, 0) and t["lat_max"] <= 300.0
    t = _eval(cfg, "X14_peakhold_loud_band_wedge_ring", "watch_tag", seeds=(4, 5, 8)).totals()
    assert (t["tp"], t["miss"], t["fp"]) == (3, 0, 0)
    # display-gain offsets stay bounded (the G6 point): an organ held at -9 display dBFS under +18 dB of RTA gain is not LOUD
    assert _eval(cfg, "AP03_rta_gain_offset_18_organ_held", "watch_tag").totals()["fp"] == 0
    assert _eval(cfg, "AP04_rta_gain_offset_clip_organ_chord", "watch_tag").totals()["fp"] == 0
    assert _eval(cfg, "AM03_loud_whistle_near_full_scale", "watch_tag").totals()["fp"] == 0
