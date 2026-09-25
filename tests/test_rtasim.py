"""Sanity tests of the RTA simulator itself (tests/rtasim). These check the physics that drives the
discriminating features, not musical realism."""

from __future__ import annotations

import math

import pytest

from rtasim import FRAME_S, RTA_BAND_HZ, AnalyserSettings, Analyser, Renderer, Scene, SCENARIOS, frames, ground_truth
from rtasim.analyser import band_position, nearest_band, skirt_db, skirt_weights
from rtasim.physics import band_bandwidth_hz, peaking_gain_db, regen_boost_db, GEQ_BAND_HZ, harmonic_band_offset
from rtasim.render import prominence_at, ring_episodes
from rtasim.sources import (
    CommonModeGain, FeedbackRing, HarmonicNote, PinkBed, band_centre_hz, note_hz,
)

FLAT = AnalyserSettings(noise_sd_scale=0.0, frame_jitter_s=0.0)      # dead-flat beds, exact timestamps: arithmetic checks


def settle(an: Analyser, tones=(), noise=(), n_frames=40):
    return an.render_static(list(tones), list(noise), frames=n_frames)


# -- geometry -----------------------------------------------------------------------------------------
def test_band_grid_is_the_corpus_grid_not_yet_the_desks():
    # the corpus stays on the grid it was baselined on until the corpus revision (rtasim/physics.py); the product's grid is
    # the measured one, a third of a band higher
    from x32mcp.meters import RTA_BAND_HZ as DESK_BAND_HZ
    assert DESK_BAND_HZ[90] == pytest.approx(10240.0) and DESK_BAND_HZ[0] == pytest.approx(20.0)
    assert all(d / c == pytest.approx(2.0 ** 0.0342, rel=1e-3) for d, c in zip(DESK_BAND_HZ, RTA_BAND_HZ))
    assert RTA_BAND_HZ[90] == pytest.approx(10000.0)
    assert RTA_BAND_HZ[60] == pytest.approx(1250.0)
    assert band_position(10000.0) == pytest.approx(90.0)
    assert nearest_band(412.0) == 44 and nearest_band(8100.0) == 87
    assert band_bandwidth_hz(10) == pytest.approx(2.71, abs=0.02)      # analyser brief §1 table
    assert band_bandwidth_hz(67) == pytest.approx(140.8, abs=0.5)
    assert harmonic_band_offset(2) == pytest.approx(10.0) and harmonic_band_offset(4) == pytest.approx(20.0)
    assert round(harmonic_band_offset(3)) == 16


def test_skirt_shape_default_n3():
    assert skirt_db(0.0, 3) == pytest.approx(0.0)
    assert skirt_db(0.5, 3) == pytest.approx(-3.0, abs=0.05)      # band edge: -3 dB for every order
    assert skirt_db(0.5, 2) == pytest.approx(-3.0, abs=0.05)
    assert skirt_db(1.0, 3) == pytest.approx(-18.1, abs=0.3)     # analyser brief §3: N=3 -18/-36/-47
    assert skirt_db(2.0, 3) == pytest.approx(-36.1, abs=0.5)
    assert skirt_db(3.0, 3) == pytest.approx(-46.7, abs=0.7)
    assert skirt_db(1.0, 2) == pytest.approx(-12.3, abs=0.3)     # N=2 -12/-24/-31
    assert skirt_db(1.0, 5) == pytest.approx(-30.1, abs=0.4)     # steep -30/-60


def test_tone_on_centre_and_between_centres_splits_correctly():
    an = Analyser(FLAT, seed=1)
    v = settle(an, tones=[(RTA_BAND_HZ[60], -20.0)])
    assert v[60] == pytest.approx(-20.0, abs=0.05)
    assert v[59] == pytest.approx(-38.1, abs=0.4) and v[61] == pytest.approx(-38.1, abs=0.4)
    assert v[58] < -55 and v[62] < -55
    # exactly on the 70/71 edge: two equal bands at -3, next pair ~-29
    an = Analyser(FLAT, seed=1)
    v = settle(an, tones=[(band_centre_hz(70.5), -20.0)])
    assert v[70] == pytest.approx(-23.0, abs=0.1) and v[71] == pytest.approx(-23.0, abs=0.1)
    assert v[69] == pytest.approx(v[72], abs=0.1) and -52 < v[69] < -46
    # 30 cents off centre: asymmetric skirts are the normal case
    an = Analyser(FLAT, seed=1)
    v = settle(an, tones=[(band_centre_hz(60, 30.0), -20.0)])
    assert v[60] > -20.2 and v[61] > v[59] + 8.0


def test_harmonics_land_at_plus_10_16_20_bands():
    an = Analyser(FLAT, seed=1)
    f0 = RTA_BAND_HZ[35]   # 220.97 Hz
    note = HarmonicNote(f0_hz=f0, t_on=-10.0, dur=100.0, level_db=-30.0, timbre=(0.0, 0.0, 0.0, 0.0))
    v = settle(an, tones=note.tones(0.0))
    peaks = sorted(sorted(range(100), key=lambda i: v[i])[-4:])
    assert peaks == [35, 45, 51, 55]
    assert v[45] == pytest.approx(-30.0, abs=0.1)           # H2 exactly +10 bands
    assert v[51] > v[50] and v[51] > v[52]                   # H3 at +15.85 -> band 51 (-18 c)


def test_lf_attack_is_slow_and_hf_attack_is_subframe():
    # loop brief §1.6 / analyser brief §1: an INSTANT onset renders as a decelerating rise whose last 10 dB
    # (-10 -> -1 re plateau) take ~216 ms (4.3 frames) at band 10, ~108 ms at band 20, sub-frame above ~300 Hz.
    st = FLAT
    for band, min_frames_last10, max_frames_last10 in ((10, 4, 7), (20, 2, 4), (67, 0, 1), (87, 0, 1)):
        an = Analyser(st, seed=1)
        settle(an, noise=[(b, -80.0) for b in range(100)], n_frames=10)
        f = RTA_BAND_HZ[band]
        levels = []
        for k in range(60):
            for _ in range(an.substeps):
                an.substep([(f, -20.0)], (), [(b, -80.0) for b in range(100)])
            v, _ = an.end_frame()
            levels.append(v[band])
        final = levels[-1]
        assert final == pytest.approx(-20.0, abs=0.3)
        first_within_1 = next(i for i, x in enumerate(levels) if x >= final - 1.0)
        first_within_10 = next(i for i, x in enumerate(levels) if x >= final - 10.0)
        span = first_within_1 - first_within_10
        assert min_frames_last10 <= span <= max_frames_last10, (band, span, levels[:10])
    # the analyser-manufactured 'growth': LS slope of an instant 39 Hz onset over frames 3..10 lies in 6..60 dB/s
    an = Analyser(st, seed=1)
    settle(an, noise=[(b, -80.0) for b in range(100)], n_frames=10)
    lv = []
    for k in range(14):
        for _ in range(an.substeps):
            an.substep([(RTA_BAND_HZ[10], -20.0)], (), [(b, -80.0) for b in range(100)])
        v, _ = an.end_frame()
        lv.append(v[10])
    ts = [i * FRAME_S for i in range(1, 6)]
    ys = lv[1:6]
    tm, ym = sum(ts) / len(ts), sum(ys) / len(ys)
    slope = sum((t - tm) * (y - ym) for t, y in zip(ts, ys)) / sum((t - tm) ** 2 for t in ts)
    assert 6.0 < slope < 60.0, slope
    assert lv[2] - lv[1] > lv[5] - lv[4] > 0.0      # decelerating (negative curvature), unlike a dB-linear ring


def test_display_release_follows_decay_pref_and_peak_hold():
    for decay_s, rate in ((0.25, 240.0), (1.0, 60.0), (4.0, 15.0)):
        st = AnalyserSettings(noise_sd_scale=0.0, decay_s=decay_s)
        an = Analyser(st, seed=1)
        f = RTA_BAND_HZ[70]
        settle(an, tones=[(f, -20.0)], noise=[(b, -90.0) for b in range(100)], n_frames=20)
        v0, _ = an.end_frame() if False else (None, None)
        levels = []
        for k in range(8):
            for _ in range(an.substeps):
                an.substep((), (), [(b, -90.0) for b in range(100)])
            v, _ = an.end_frame()
            levels.append(v[70])
        per_frame = [(a - b) for a, b in zip(levels, levels[1:])]
        assert all(x == pytest.approx(rate * FRAME_S, abs=0.05) for x in per_frame[:4]), (decay_s, per_frame)
    # peak hold: flat for hold_s, then releases
    st = AnalyserSettings(noise_sd_scale=0.0, decay_s=1.0, peak_hold_s=0.5)
    an = Analyser(st, seed=1)
    settle(an, tones=[(RTA_BAND_HZ[70], -20.0)], n_frames=20)
    levels = []
    for k in range(20):
        for _ in range(an.substeps):
            an.substep((), (), ())
        v, _ = an.end_frame()
        levels.append(v[70])
    assert levels[0] == levels[8] == pytest.approx(-20.0, abs=0.01)   # held (dead flat: Δ = 0.000)
    assert levels[15] < -25.0                                            # then released


def test_lf_noise_statistics_are_ragged_hf_smooth():
    an = Analyser(AnalyserSettings(), seed=3)
    bed = PinkBed(level_1k_db=-50.0, tilt_db_per_oct=0.0, f_lo=20.0, f_hi=20000.0)
    lf, hf = [], []
    for k in range(200):
        for _ in range(an.substeps):
            an.substep((), (), bed.noise(0.0))
        v, _ = an.end_frame()
        if k > 40:
            lf.append(v[15]); hf.append(v[88])
    import statistics
    assert statistics.pstdev(lf) > 2.0        # ragged, correlated LF band on noise
    assert statistics.pstdev(hf) < 1.2        # smooth HF band
    assert statistics.mean(hf) > -50.0        # PEAK rides above the nominal (RMS-ish) bed level


def test_common_mode_step_moves_programme_bands_equally_but_not_a_ring():
    organ = HarmonicNote(f0_hz=RTA_BAND_HZ[50], t_on=-5.0, dur=100.0, level_db=-30.0, timbre=(0.0, -3.0))
    master = CommonModeGain(points=((0.0, 0.0), (1.0, 0.0), (1.0, 4.0)))
    ring = FeedbackRing(freq_hz=RTA_BAND_HZ[75], excess_db=-6.0, tau_loop_s=0.01, t_on=-5.0, start_db=-80.0, sat_db=-5.0,
                        excitation_db=-60.0, excite_from_programme=False, wander_db=0.0, excess_wander_db=0.0)
    sc = Scene(2.5, sources=[PinkBed(level_1k_db=-60.0, tilt_db_per_oct=0.0), organ], rings=[ring], master=master, analyser=FLAT)
    r = Renderer(sc, 1)
    fr = r.run()
    before = fr[int(0.9 / FRAME_S)][1]
    after = fr[int(2.3 / FRAME_S)][1]
    assert after[50] - before[50] == pytest.approx(4.0, abs=0.15)     # H1
    assert after[60] - before[60] == pytest.approx(4.0, abs=0.15)     # H2
    assert after[30] - before[30] == pytest.approx(4.0, abs=0.3)      # bed band (flat beds)
    # ring: excess -6 -> -2: regenerative boost 6.0 -> 13.7 dB => the ring band rises ~7.7 dB for a 4 dB step
    assert regen_boost_db(-6.0) == pytest.approx(6.0, abs=0.1) and regen_boost_db(-2.0) == pytest.approx(13.7, abs=0.1)
    assert after[75] - before[75] == pytest.approx(7.7, abs=0.6)


def test_ring_growth_rate_matches_excess_over_tau_and_plateaus():
    for excess, tau in ((0.3, 0.010), (1.0, 0.005), (0.5, 0.035)):
        ring = FeedbackRing(freq_hz=RTA_BAND_HZ[70], excess_db=excess, tau_loop_s=tau, t_on=0.5, start_db=-70.0, sat_db=-10.0,
                            excite_from_programme=False, wander_db=0.0, excess_wander_db=0.0)
        rate = excess / tau
        sc = Scene(0.5 + 62.0 / rate + 1.5, sources=[PinkBed(level_1k_db=-75.0, tilt_db_per_oct=0.0)], rings=[ring], analyser=FLAT)
        r = Renderer(sc, 1)
        fr = r.run()
        rate = excess / tau
        # pick two frames while growing (band 70 is HF: analyser attack is sub-frame)
        t1 = 0.5 + 10.0 / rate
        t2 = 0.5 + min(35.0, rate * 1.0) / rate
        k1, k2 = int(round(t1 / FRAME_S)), int(round(t2 / FRAME_S))
        if k2 > k1:
            measured = (fr[k2][1][70] - fr[k1][1][70]) / ((k2 - k1) * FRAME_S)
            assert measured == pytest.approx(rate, rel=0.08), (excess, tau, measured)
        assert max(v[70] for _, v in fr[-10:]) == pytest.approx(-10.0, abs=0.3)     # plateau at sat
        tr = r.trace[0]
        assert tr[-1].e_eff == pytest.approx(excess)


def test_geq_cut_deeper_than_excess_stops_growth_and_bell_shape():
    # bell: -3 at centre, about -2.5 at 1/6 oct for Q=3 (loop brief §4.1: Q 4.3 -> -1.5, Q 2 -> -2.5)
    assert peaking_gain_db(1000.0, 1000.0, -3.0, 3.0) == pytest.approx(-3.0, abs=0.01)
    assert -2.6 < peaking_gain_db(1000.0 * 2 ** (1 / 6), 1000.0, -3.0, 3.0) < -1.5
    assert peaking_gain_db(2000.0, 1000.0, -3.0, 3.0) > -0.6
    ring = FeedbackRing(freq_hz=2500.0, excess_db=0.5, tau_loop_s=0.010, t_on=0.2, start_db=-80.0, sat_db=-5.0,
                        excite_from_programme=False, excitation_db=-90.0, wander_db=0.0, excess_wander_db=0.0)
    sc = Scene(4.0, sources=[PinkBed(level_1k_db=-75.0, tilt_db_per_oct=0.0)], rings=[ring], analyser=FLAT,
               geq_schedule=[(1.0, 22, -3.0)])     # GEQ band 22 = 2.5 kHz
    r = Renderer(sc, 1)
    fr = r.run()
    k_cut = int(1.0 / FRAME_S)
    lv_cut = fr[k_cut - 1][1][70]
    assert fr[k_cut - 1][1][70] == pytest.approx(fr[k_cut - 7][1][70] + 15.0, abs=1.0)   # growing at 0.5/0.01 = 50 dB/s
    # acoustically the ring collapses at (3-0.5)/0.01 = 250 dB/s ...
    assert r.trace[0][k_cut + 4].level_db < r.trace[0][k_cut - 1].level_db - 30.0   # down to the regenerative floor (-90 + 12)
    assert r.trace[0][k_cut + 5].e_eff == pytest.approx(-2.5, abs=0.05)
    # ... but the DISPLAY can only fall at the RTA release rate (decay 1.0 -> 60 dB/s = 3 dB/frame): what VERIFY sees
    assert fr[k_cut + 4][1][70] == pytest.approx(lv_cut - 5 * 3.0, abs=1.0)
    assert fr[k_cut + 20][1][70] < lv_cut - 30.0
    # a -3 dB cut on a ring with 4 dB excess only slows it
    ring2 = FeedbackRing(freq_hz=2500.0, excess_db=4.0, tau_loop_s=0.010, t_on=0.2, start_db=-70.0, sat_db=-5.0,
                         excite_from_programme=False, wander_db=0.0, excess_wander_db=0.0)
    sc2 = Scene(2.0, sources=[PinkBed(level_1k_db=-75.0, tilt_db_per_oct=0.0)], rings=[ring2], analyser=FLAT, geq_schedule=[(0.3, 22, -3.0)])
    r2 = Renderer(sc2, 1)
    fr2 = r2.run()
    assert fr2[-1][1][70] == pytest.approx(-5.0 - 3.0, abs=0.5)   # still reaches the limiter (seen through the PRE-insert cut)


def test_prominence_ceiling_set_by_skirts():
    for order, ceiling in ((2.0, 24.0), (3.0, 36.0), (5.0, 60.0)):
        an = Analyser(AnalyserSettings(noise_sd_scale=0.0, skirt_order=order), seed=1)
        v = settle(an, tones=[(RTA_BAND_HZ[87], -8.0)], noise=[(b, -75.0) for b in range(100)])
        p = prominence_at(v, 87)
        assert p == pytest.approx(min(ceiling, 67.0), abs=1.5), (order, p)


def test_clip_flag_and_floor():
    an = Analyser(FLAT, seed=1)
    v = settle(an, tones=[(RTA_BAND_HZ[67], 3.0)])
    assert v[67] == 0.0
    assert min(v) == -115.0 or min(v) >= -128.0
    an = Analyser(AnalyserSettings(noise_sd_scale=0.0, self_noise_db=-140.0), seed=1)
    v = settle(an)
    assert all(x == -128.0 for x in v)


def test_determinism_and_cache():
    a = frames("S3_ring_during_music", 2)
    b = frames("S3_ring_during_music", 2)
    assert a is b                                  # cached
    sc = SCENARIOS["S3_ring_during_music"]
    r = Renderer(sc.build(2), 2)
    c = r.run()
    assert len(c) == len(a) == int(sc.duration_s / FRAME_S)
    assert all(x[0] == y[0] and x[1] == y[1] for x, y in zip(a, c))   # same seed => identical frames
    d = frames("S3_ring_during_music", 3)
    assert any(x[1] != y[1] for x, y in zip(a, d))
    assert all(len(v) == 100 and all(-128.0 <= x <= 0.0 for x in v) for _, v in a)


def test_ground_truth_is_derived_from_frames():
    gt = ground_truth("S3_ring_during_music", 1)
    ev = gt["events"]
    assert len(ev) == 1
    e = ev[0]
    assert e["t_onset"] == pytest.approx(4.0, abs=0.06)          # loop crosses threshold at t=4
    assert 4.3 < e["t_prom"] < 5.3                                # becomes >= 12 dB prominent ~0.5-1 s later (30 dB/s from -70 under a -45 bed)
    assert abs(e["band"] - 73) <= 1
    fr = frames("S3_ring_during_music", 1)
    k = int(round(e["t_prom"] / FRAME_S))
    from rtasim.render import cluster_prominence_at
    both = lambda vals: max(max(prominence_at(vals, b), cluster_prominence_at(vals, b)) for b in (72, 73, 74))
    assert both(fr[k][1]) >= 12.0
    assert both(fr[k - 4][1]) < 12.0
    # controls have no events; established ring is an event from t=0 with t_prom = 0
    assert ground_truth("S1_bass_under_quiet_music", 1)["events"] == []
    e2 = ground_truth("S2a_established_ring_8k", 1)["events"][0]
    assert e2["t_onset"] == 0.0 and e2["t_prom"] == 0.0 and e2["established"]


def test_every_scenario_renders_and_is_sane():
    assert len(SCENARIOS) >= 25
    for name, sc in SCENARIOS.items():
        fr = frames(name, 1)
        assert len(fr) == int(round(sc.duration_s / FRAME_S)), name
        gt = ground_truth(name, 1)
        if sc.has_feedback:
            assert gt["events"], name
            assert all(e["visible"] for e in gt["events"]), (name, gt["events"])
        else:
            assert gt["events"] == [], name


def test_note_hz():
    assert note_hz("A4") == pytest.approx(440.0)
    assert note_hz("E1") == pytest.approx(41.2, abs=0.05)
    assert note_hz("Bb1") == pytest.approx(58.27, abs=0.05)
    assert note_hz(196.0, 1200.0) == pytest.approx(392.0)


# -- corpus-critic additions ----------------------------------------------------------------------------
def test_regeneration_is_comb_selective():
    from rtasim.physics import comb_gain_lin, comb_band_mean_lin
    g = 10 ** (-3.0 / 20)                                     # loop 3 dB under threshold
    assert 10 * math.log10(comb_gain_lin(g, 0.0, 0.012)) == pytest.approx(10.7, abs=0.1)      # on the mode: +10.7 dB
    assert comb_gain_lin(g, 0.5 / 0.012, 0.012) == pytest.approx(1.0 / (1 + g) ** 2, rel=1e-6)  # between modes: < 0 dB
    # a band spanning many comb periods sees the mean 1/(1-g^2); a band much narrower than 1/tau sees ~the peak
    assert comb_band_mean_lin(g, 2000.0, 0.012) == pytest.approx(1.0 / (1 - g * g), rel=0.03)
    assert comb_band_mean_lin(g, 0.5, 0.012) == pytest.approx(comb_gain_lin(g, 0.0, 0.012), rel=0.02)
    # a programme line 100 cents (11 Hz at 196 Hz) off a tau=12 ms mode regenerates far less than one 2 Hz off
    ring = FeedbackRing(freq_hz=199.7, excess_db=-3.0, tau_loop_s=0.012, excitation_db=-200.0, wander_db=0.0, excess_wander_db=0.0)
    on = ring.regen_extra_db(-3.0, 199.7, [(197.7, -30.0)], -200.0)
    off = ring.regen_extra_db(-3.0, 199.7, [(188.5, -30.0)], -200.0)
    assert on > off + 8.0
    assert on == pytest.approx(-30.0 - 6.0 + 10 * math.log10(comb_gain_lin(g, 2.0, 0.012) - 1.0), abs=0.05)


def test_plateau_follows_pre_tap_gain_only_when_told():
    ring = FeedbackRing(freq_hz=2000.0, excess_db=1.0, sat_db=-12.0, established=True, wander_db=0.0, excess_wander_db=0.0,
                        excite_from_programme=False)
    ring.step(0.0, 0.0125, common_db=0.0, geq_gain_db=0.0, prog_gain_db=6.0)
    assert ring.sat_now == pytest.approx(-6.0) and ring.level_db <= -6.0
    ring.step(0.1, 0.0125, common_db=0.0, geq_gain_db=0.0, prog_gain_db=20.0)
    assert ring.sat_now == pytest.approx(-0.5)                 # never into the clip flag
    clip = FeedbackRing(freq_hz=2000.0, excess_db=1.0, sat_db=0.0, established=True, wander_db=0.0, excess_wander_db=0.0)
    clip.step(0.0, 0.0125, common_db=0.0, geq_gain_db=0.0, prog_gain_db=-10.0)
    assert clip.sat_now == 0.0                                  # a desk-clip plateau is at the tap: does not move


def test_ring_wander_is_not_a_pure_tone_and_notes_are_not_dead_flat():
    import statistics
    fr = frames("S2a_established_ring_8k", 1)
    lv = [v[87] for _, v in fr]
    d1 = [b - a for a, b in zip(lv, lv[1:])]
    # a pure 0.6 Hz sinusoid has (almost) no sign changes in its first difference within a half period (17 frames);
    # the seeded multi-rate wander changes direction several times per second
    sign_changes = sum(1 for a, b in zip(d1, d1[1:]) if a * b < 0)
    assert sign_changes > 25, sign_changes
    assert 0.03 < statistics.pstdev(lv) < 0.6
    # a sung note flutters and drifts: its peak band level is not constant to the quantum
    fr = frames("X6_soprano_closed_vowel_band_edge", 1)
    seg = [max(v[50:54]) for _, v in fr[40:100]]
    assert statistics.pstdev(seg) > 0.5


def test_examiner_scenarios_ground_truth():
    gt = ground_truth("X12a_master_drop20_raise_channel", 1)["events"]
    assert len(gt) == 2 and gt[0]["t_onset"] == 0.0 and gt[0]["t_end"] == pytest.approx(3.0, abs=0.1)
    assert gt[1]["t_onset"] == pytest.approx(7.0, abs=0.06) and 7.1 < gt[1]["t_prom"] < 7.6
    two = ground_truth("X10_two_rings_exact_octave", 1)["events"]
    assert len(two) == 2 and abs(two[1]["freq_hz"] / two[0]["freq_hz"] - 2.0) < 1e-6
    one = ground_truth("X19_handheld_ring_stalls_and_hops", 1)["events"]
    assert len(one) == 1                                        # a sag and a hop do not make two events
    for name in ("X1_organ_held_notes", "X4_sine_lead_portamento", "X5_808_bassline_40_60Hz", "X18_applause_crowd_30s",
                 "X20_mains_hum_and_hvac_whine", "X15_kick_bass_unison_55Hz"):
        assert ground_truth(name, 2)["events"] == [], name


# -- kill check (K series) ------------------------------------------------------------------------------
def test_limiter_held_howl_answers_a_cut_with_exactly_the_cut_depth_and_dies_only_when_out_cut():
    """A plateaued howl with excess e >= cut depth c: the loop stays super-critical, the saturation point re-pins it,
    the tap reads exactly -c dB and sits flat = what programme through the same EQ does. Only c > e collapses it."""
    ring = FeedbackRing(freq_hz=2500.0, excess_db=4.0, tau_loop_s=0.010, t_on=0.2, start_db=-60.0, sat_db=-13.0,
                        excite_from_programme=False, wander_db=0.0, excess_wander_db=0.0)
    sc = Scene(6.0, sources=[PinkBed(level_1k_db=-75.0, tilt_db_per_oct=0.0)], rings=[ring], analyser=FLAT,
               geq_schedule=[(2.0, 22, -3.0), (4.0, 22, -6.0)])     # GEQ band 22 = 2.5 kHz
    r = Renderer(sc, 1)
    fr = r.run()
    tr = r.trace[0]
    k = lambda t: int(round(t / FRAME_S))
    assert fr[k(1.9)][1][70] == pytest.approx(-13.0, abs=0.5)                       # at the limiter
    assert tr[k(3.9)].e_eff == pytest.approx(1.0, abs=0.05)                          # -3 on 4 dB: still regenerating
    assert fr[k(3.9)][1][70] == pytest.approx(-16.0, abs=0.5)                       # ... exactly 3 dB lower at the tap
    seg = [v[70] for _, v in fr[k(2.6):k(4.0)]]
    assert max(seg) - min(seg) < 1.0                                                 # ... and dead flat
    assert tr[k(5.9)].e_eff == pytest.approx(-2.0, abs=0.05)                         # -6: out-cut
    assert tr[k(5.9)].level_db < tr[k(3.9)].level_db - 20.0                          # ... collapses


def test_survived_verdict_flags_a_ring_that_outlives_its_cuts():
    from rtasim.harness import Det, run_one
    from x32mcp.detector import DetectorConfig

    class Oracle:
        """Test detector: emits on the K1 ring's band from its onset - once, or once per 1.1 s while the band is loud."""
        def __init__(self, band_hz, *, one_shot: bool):
            self.one_shot, self.last = one_shot, None

        def feed(self, values, ts):
            if ts < 3.0 or values[70] < -30.0 or (self.last is not None and (self.one_shot or ts - self.last < 1.1)):
                return []
            self.last = ts
            return [Det(ts=ts, band=70, freq_hz=2500.0, confidence=1.0)]

    cfg = DetectorConfig()
    name = "K1_limiter_held_howl_e4_2k5"
    one = run_one(lambda bh: Oracle(bh, one_shot=True), name, 1, closed_loop=True, notch_cfg=cfg)
    assert one.tp == 1 and not one.fps and [g for _, _, g in one.cuts] == [-3.0]
    assert one.survived == [0] and not one.passed                  # -3 on 4 dB of excess: still howling at the end
    assert one.to_dict()["survived"] == [0]
    many = run_one(lambda bh: Oracle(bh, one_shot=False), name, 1, closed_loop=True, notch_cfg=cfg)
    assert [g for _, _, g in many.cuts][:2] == [-3.0, -6.0]
    assert many.survived == [] and many.passed                    # deepened to -6: dead
    assert run_one(lambda bh: Oracle(bh, one_shot=True), name, 1).survived == []   # open loop: nothing is cut
    # a ring nobody acted on is a miss, not a survivor
    null = run_one(lambda bh: type("Null", (), {"feed": lambda self, v, t: []})(), name, 1, closed_loop=True, notch_cfg=cfg)
    assert len(null.misses) == 1 and null.survived == [] and not null.passed


def test_kill_scenarios_are_survival_cases_by_construction():
    """Every K scenario: one visible episode; a single -3 dB slider at the ring's nearest GEQ centre leaves the loop
    super-critical; the depth the scenario names kills it (Q = the scene's geq_q)."""
    from rtasim.harness import geq_band_for
    need = {"K1_limiter_held_howl_e4_2k5": -6.0, "K2_limiter_held_howl_e7_1k25": -9.0,
            "K3_established_limiter_plateau_e5_5k": -6.0, "K4_compressor_plateau_e6p5_quiet_2k5": -9.0,
            "K5_channel_shove_into_limiter_e5p5_2k5": -6.0, "K6_limiter_held_howl_e3p5_midpoint_1k8": -6.0}
    for name, depth in need.items():
        sc = SCENARIOS[name]
        assert sc.has_feedback and "kill" in sc.tags
        gt = ground_truth(name, 1)
        assert len(gt["events"]) == 1 and gt["events"][0]["visible"], (name, gt["events"])
        scene = sc.build(1)
        ring = scene.rings[0]
        e = ring.excess_db + (scene.master.gain_db(scene.duration_s) * scene.loop_coupling if scene.master else 0.0)
        centre = GEQ_BAND_HZ[geq_band_for(ring.freq_hz) - 1]
        assert e + peaking_gain_db(ring.freq_hz, centre, -3.0, scene.geq_q) > 0.3, name     # survives one -3
        assert e + peaking_gain_db(ring.freq_hz, centre, depth, scene.geq_q) < -0.3, name    # dies at the named depth
        assert gt["events"][0]["t_onset"] == pytest.approx(0.0 if ring.established else 3.0, abs=0.1), name


# -- bus PEQ actuator (docs/PEQ_ACTUATOR_DESIGN.md, step 2: prove it offline first) ----------------------
def test_peq_grids_match_the_desk():
    from rtasim.physics import peq_grid_hz, peq_grid_q
    assert peq_grid_q(6.0) == pytest.approx(6.103, abs=0.002)
    assert peq_grid_q(4.0) == pytest.approx(3.913, abs=0.002)
    assert peq_grid_q(8.0) == pytest.approx(7.812, abs=0.002)
    assert peq_grid_q(10.0) == pytest.approx(10.0) and peq_grid_q(0.3) == pytest.approx(0.3)
    assert peq_grid_hz(2331.0) == pytest.approx(2349.8, abs=0.3)      # the design's worked offsets (S2.1)
    assert peq_grid_hz(525.4) == pytest.approx(532.1, abs=0.3)
    assert peq_grid_hz(2588.0) == pytest.approx(2606.3, abs=0.3)
    assert peq_grid_hz(1788.9) == pytest.approx(1782.5, abs=0.3)
    assert peq_grid_hz(20.0) == pytest.approx(20.0) and peq_grid_hz(20000.0) == pytest.approx(20000.0)


def test_peq_notch_in_the_renderer_is_narrow_and_stacks_with_the_geq():
    sc = Scene(2.0, sources=[PinkBed(level_1k_db=-75.0, tilt_db_per_oct=0.0)], rings=[], analyser=FLAT)
    r = Renderer(sc, 1)
    r.set_peq_notch(2, 2349.8, 6.103, -6.0)
    assert r.geq_gain_at(2349.8) == pytest.approx(-6.0, abs=0.01)
    assert r.geq_gain_at(2349.8 * 2 ** 0.03) == pytest.approx(-5.62, abs=0.1)     # design S2.1: -6, Q 6, 0.03 oct off
    assert r.geq_gain_at(2349.8 * 2 ** 0.167) == pytest.approx(-2.07, abs=0.15)   # 1/6 oct off
    assert abs(r.geq_gain_at(2349.8 * 2 ** 0.5)) < 0.5                              # half an octave away: gone
    assert r._geq_band_gain[nearest_band(2349.8)] < -5.0                            # the RTA band cache sees it
    r.set_geq_gain(22, -3.0)                                                          # GEQ 2.5 kHz on top
    assert r.geq_gain_at(2349.8) == pytest.approx(-6.0 + peaking_gain_db(2349.8, 2500.0, -3.0, sc.geq_q), abs=0.01)
    r.set_peq_notch(2, 2349.8, 6.103, 0.0)                                            # freed
    assert 2 not in r.peq and r.geq_gain_at(2349.8) == pytest.approx(peaking_gain_db(2349.8, 2500.0, -3.0, sc.geq_q), abs=0.01)
    with pytest.raises(ValueError):
        r.set_peq_notch(2, 1000.0, 6.0, +3.0)
    with pytest.raises(ValueError):
        r.set_peq_notch(7, 1000.0, 6.0, -3.0)


def test_peq_actuator_closed_loop_notches_the_ring_itself_and_kills_the_midpoint_case():
    from rtasim.harness import Det, run_one
    from x32mcp.detector import DetectorConfig
    name = "K6_limiter_held_howl_e3p5_midpoint_1k8"       # 1788.9 Hz: between GEQ 1.6 k and 2 k, RTA band 65

    class Oracle:
        def __init__(self, band_hz, *, one_shot: bool):
            self.one_shot, self.last = one_shot, None

        def feed(self, values, ts):
            if ts < 3.0 or values[65] < -30.0 or (self.last is not None and (self.one_shot or ts - self.last < 1.1)):
                return []
            self.last = ts
            return [Det(ts=ts, band=65, freq_hz=1788.9, confidence=1.0)]

    cfg = DetectorConfig()
    rr = run_one(lambda bh: Oracle(bh, one_shot=False), name, 1, closed_loop=True, notch_cfg=cfg, actuator="peq")
    assert rr.notches and rr.notches[0]["actuator"] == "peq" and rr.notches[0]["band"] == 2
    assert rr.notches[0]["notch_hz"] == pytest.approx(1782.5, abs=0.3) and rr.notches[0]["q"] == pytest.approx(6.103, abs=0.002)
    assert [n["gain_db"] for n in rr.notches][:2] == [-3.0, -6.0]                  # -3 leaves e 3.5 - 3.0 alive; -6 kills
    assert rr.survived == [] and rr.passed
    assert [b for _, b, _ in rr.cuts] == [20] * len(rr.cuts)                         # reported as the nearest GEQ band (1.6 k = 20)
    one = run_one(lambda bh: Oracle(bh, one_shot=True), name, 1, closed_loop=True, notch_cfg=cfg, actuator="peq")
    assert one.survived == [0] and not one.passed
    geq = run_one(lambda bh: Oracle(bh, one_shot=False), name, 1, closed_loop=True, notch_cfg=cfg)
    assert geq.notches[0]["actuator"] == "geq" and geq.survived == []               # the GEQ path is byte-identical to before
    with pytest.raises(ValueError):
        run_one(lambda bh: Oracle(bh, one_shot=True), name, 1, closed_loop=True, notch_cfg=cfg, actuator="deq")


def test_hop_scenarios_are_one_episode_through_the_hop():
    for name, cents in (("K7_limiter_howl_hops_100c_after_first_cut_e3p5", 100.0), ("K8_limiter_howl_hops_200c_after_first_cut_e4", 200.0)):
        sc = SCENARIOS[name]
        ring = sc.build(1).rings[0]
        assert ring.hop_at_s == 4.6 and ring.hop_cents == cents
        gt = ground_truth(name, 1)
        assert len(gt["events"]) == 1 and gt["events"][0]["visible"], (name, gt["events"])
        assert gt["events"][0]["t_onset"] == pytest.approx(3.0, abs=0.1)
        assert ring.current_hz(5.0) == pytest.approx(2500.0 * 2 ** (cents / 1200.0), rel=1e-3)


def test_k8_is_a_policy_layer_case_and_dies_with_tier_b_under_both_actuators():
    """K8: the howl hops 200 cents after the first cut and ARRIVES at its plateau within one frame (the simulator's hop is an
    instantaneous retune). Nothing was seen to grow, so the detector classes the new line MODERATE and leaves it to the policy
    layer by design -- the same class as the fast-howl-to-a-quiet-plateau breakers. The detector alone therefore never kills it;
    with the policy layer's tier B (stand-in: rtasim.policy) it is dead at the end under both actuators, on six seeds -- including
    PEQ seed 5, on which the deepen meant for the old line files a bystander 'held' on the hopped one (without the bystander
    rule in cfs_policy.tier_b_eligible that line is barred from tier B for good and howls at -13 dBFS to the end of the scene)."""
    from rtasim.harness import run_one
    from x32mcp.descriptor import Descriptor
    from x32mcp.detector import DetectorConfig, FeedbackDetector
    cfg = DetectorConfig.from_descriptor(Descriptor.load())
    fac = lambda bh: FeedbackDetector(cfg, bh, mode="watch")  # noqa: E731
    name = "K8_limiter_howl_hops_200c_after_first_cut_e4"
    alone = run_one(fac, name, 1, closed_loop=True, notch_cfg=cfg)
    assert alone.survived == [0] and len(alone.notches) == 1 and alone.policy_log == []
    for actuator in ("geq", "peq"):
        for seed in (1, 2, 3, 4, 5, 6):
            rr = run_one(fac, name, seed, closed_loop=True, notch_cfg=cfg, actuator=actuator, policy="tier_b")
            assert rr.survived == [], (actuator, seed, rr.notches, rr.policy_log)
            assert rr.policy_log and rr.policy_log[0]["rule"] == "tier_b", (actuator, seed)
            assert 0.55 <= rr.policy_log[0]["ts"] - 4.6 <= 1.8, (actuator, seed, rr.policy_log)     # min_age_s after the hop; later only
                                                                                                    # behind a bystander verdict
            assert sum(1 for d in rr.detections if d.verdict == "FP") == 0
    with pytest.raises(ValueError):
        run_one(fac, name, 1, closed_loop=True, notch_cfg=cfg, policy="tier_c")


def test_k8r_a_hop_that_regrows_is_the_detectors_own_catch():
    """K8r: the same event with the new mode regrowing from its seed at e/tau. The detector re-detects it on its own evidence
    (RISE / FAST-RISE), no policy cut needed; under the PEQ the second notch lands on the new mode and both are dead at the end."""
    from rtasim.harness import run_one
    from x32mcp.descriptor import Descriptor
    from x32mcp.detector import DetectorConfig, FeedbackDetector
    cfg = DetectorConfig.from_descriptor(Descriptor.load())
    fac = lambda bh: FeedbackDetector(cfg, bh, mode="watch")  # noqa: E731
    name = "K8r_limiter_howl_hop_200c_regrows_e4"
    for seed in (1, 2, 3):
        rr = run_one(fac, name, seed, closed_loop=True, notch_cfg=cfg, actuator="peq")
        new_mode = [d for d in rr.detections if d.verdict == "TP" and d.ts > 4.6]
        assert new_mode and new_mode[0].klass == "STRONG" and new_mode[0].ts - 4.6 <= 1.0, (seed, rr.detections)
        assert rr.survived == [] and len({n["band"] for n in rr.notches}) == 2, (seed, rr.notches)
