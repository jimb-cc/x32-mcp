"""All physical parameters of the simulator in one place (test support, not production).

Citations: ``[L §x]`` = loop-physics brief (reports/physics-loop/brief.md), ``[A §x]`` = analyser brief
(reports/physics-analyser/brief.md), ``meters.md`` = docs/research/meters.md. Items marked UNCERTAIN are
not verifiable from the repo or first principles; every one of them is a field of :class:`AnalyserSettings`
(or a keyword of the relevant source) so a scenario or a skeptic can sweep it.

Frame = one ``/meters/15`` RTA frame, 50 ms (device.yaml ``rta.frame_period_s``). Levels are RTA dB re full
scale: −128 = floor, 0.0 = clip (meters.md §4.2). Band i centre = 10000·2^((i−90)/10) Hz: the corpus grid (see RTA_BAND_HZ below).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

# THE CORPUS KEEPS ITS OWN GRID until the corpus revision. The desk's bins are at 20*2^(i/10) (measured 2026-09-23;
# x32mcp.meters.RTA_BAND_HZ, device.yaml); the corpus was built and baselined on the DOC table 10000*2^((i-90)/10), a third
# of a band lower. Its scenes mix frequencies anchored to the bins (band_centre_hz: a ring "on the 64/65 edge") with
# frequencies anchored to the world (notes in Hz, rings at 2500 Hz, GEQ centres): moving the bins alone moves the first
# kind against the second by 41 cents and changes what the scenarios test (X17's ring, written 24 cents under the B2 the
# guitar keeps playing, lands 10 cents above it). The simulator is a self-consistent world whatever its grid -- the
# harness hands the detector this same band_hz -- so the grid moves together with the measured analyser model, the
# re-anchored scenarios and new baselines in ONE revision (docs/REVIEW_RESPONSE_2026-09-24.md, item 2).
RTA_BAND_HZ: tuple[float, ...] = tuple(10000.0 * 2.0 ** ((i - 90) / 10.0) for i in range(100))


def rta_band_centre(i: int) -> float:
    """Centre (Hz) of band ``i`` on the CORPUS grid (see above; the product's is x32mcp.meters.rta_band_centre)."""
    return RTA_BAND_HZ[i]



# -- time base / value range --------------------------------------------------------------------
FRAME_S: float = 0.05                 # /meters/15 frame period (device.yaml rta.frame_period_s)
RTA_BANDS: int = 100
RTA_FLOOR_DB: float = -128.0          # meters.md §4.2: int16/256, 0x8000 = -128 = no signal
RTA_CLIP_DB: float = 0.0              # meters.md §4.2: 0x0000 = "clipping occurred"

# -- band geometry ------------------------------------------------------------------------------
# 1/10-octave bands: -3 dB edges at ±1/20 octave (±60 cents) -> relative bandwidth [A §1]
BAND_REL_BW: float = 2.0 ** (1 / 20) - 2.0 ** (-1 / 20)      # 0.06932  (Δf = 0.0693·f)
FILTER_Q: float = 1.0 / BAND_REL_BW                          # 14.42
BANDS_PER_OCTAVE: float = 10.0
CENTS_PER_BAND: float = 120.0

# Harmonic k sits 10·log2(k) bands above the fundamental [A §4.1]: H2 +10.00, H3 +15.85, H4 +20.00 ...
def harmonic_band_offset(k: int) -> float:
    return 10.0 * math.log2(k)


def band_bandwidth_hz(i: int) -> float:
    """Δf_i = 0.0693·f_i (Hz) [A §1 table: 2.71 Hz @ band 10, 141 Hz @ band 67]."""
    return BAND_REL_BW * RTA_BAND_HZ[i]


# -- GEQ (ring-out actuator) ---------------------------------------------------------------------
# 31 ISO 1/3-octave centres, par 1..31 (device.yaml geq.band_hz); cuts only, -3 dB steps to -9 dB.
GEQ_BAND_HZ: tuple[float, ...] = (
    20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600,
    2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 20000,
)
# X32 GEQ bell Q is UNCERTAIN [L §4.1]: "true 1/3 oct" = 4.3; a conventional graphic at small settings
# behaves like Q≈2..3 (better guess per the brief). RBJ-style peaking prototype, see geq_gain_db_at().
GEQ_Q_DEFAULT: float = 3.0


def peaking_gain_db(f_hz: float, fc_hz: float, gain_db: float, q: float) -> float:
    """Gain (dB, sign of ``gain_db``) of one analog peaking-EQ section (RBJ prototype
    H(s) = (s²+s·A/Q+1)/(s²+s/(A·Q)+1), A = 10^(G/40)) evaluated at ``f_hz`` [L §4.1 table]."""
    if gain_db == 0.0 or f_hz <= 0.0:
        return 0.0
    a = 10.0 ** (gain_db / 40.0)
    w = f_hz / fc_hz
    d = (1.0 - w * w)
    num = d * d + (w * a / q) ** 2
    den = d * d + (w / (a * q)) ** 2
    return 10.0 * math.log10(num / den)


# -- analyser settings ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AnalyserSettings:
    """How the console turns band input power into ``/meters/15`` values. Defaults = the most plausible
    X32 behaviour with the prefs ``set_rta_source`` leaves as found (emulator defaults: decay 1.0,
    peakhold OFF, det forced to PEAK) [A §2]. Everything here is UNCERTAIN to some degree -> sweepable.

    Filter skirts [A §3]: Butterworth band-pass of order 2N evaluated at the neighbouring centres:
      N=2: -12/-24/-31 dB at ±1/±2/±3 bands; N=3 (default): -18/-36/-47; N=5 ("steep", matches the M7
      60 dB-prominent 8 kHz datum): -30/-60/-78; N=1 (single biquad): -7/-12/-16. A tone exactly on a band
      edge reads -3/-3 dB in the two adjacent bands for every N.
    Attack [A §1, L §1.6]: the band envelope cannot settle faster than ~1/Δf_i. Model 'U' (constant-Q bank):
      power one-pole τ_a(i) = attack_k/Δf_i clamped to [attack_min_s, attack_max_s]; default attack_k 0.5 ->
      185 ms @ band 10 (39 Hz), 23 ms @ band 40, 3.5 ms @ 2 kHz (sub-frame above ~300 Hz). attack_k 1.0 =
      critically resolved (370 ms @ band 10); model 'BQ' (one 2nd-order section): 1/(πΔf_i). The filter's own
      ring-down uses filter_release_k (default: same poles as the rise).
    Release [A §2 'decay' row]: PEAK display detector = instant attack (limited only by the filter), then a
      dB-linear fall at release_law_db/decay_s dB/s. The unit/law of /-prefs/rta/decay is UNCERTAIN: with
      release_law_db=60 ("decay = time to fall 60 dB") decay 0.25 -> 240 dB/s, 1.0 -> 60 dB/s, 4 -> 15 dB/s,
      16 -> 3.75 dB/s. Sweep release_law_db ∈ {17, 60, 240} at decay 1.0 per [A §7].
    Peak-hold [A §2]: peak_hold_s > 0 freezes each band at its running max for that long before releasing.
    Detector det [A §2]: levels in scenarios are "as displayed under PEAK" (the reference). det='RMS' lowers
      noise-like paths by rms_noise_offset_db (PEAK reads noise ≈8..11 dB above RMS; tones read the same)
      and scales their fluctuation by rms_noise_sd_scale.
    Estimation noise [A §1 'statistical floor']: a noise-like band power estimate carries ν = Δf_i·T
      (T = max(frame, τ_a)) independent samples: χ²(2ν)/(2ν) statistics for ν < nu_gauss_min (ν≈1 for every
      band below ~45: exponential power -> deep nulls, +6 dB peaks 2 % of the time), Gaussian-in-dB with sd
      4.34/√ν above (2.1 dB @ band 60, 1.0 @ band 80), AR(1)-correlated over τ_a. Applied multiplicatively to
      the noise-like path only (steady tones read steady; their own wander is a source property). The PEAK
      display then rides the upper envelope of that fluctuation (beds display a few dB above nominal at LF).
    Output: int16/256 quantisation, clamp [-128, 0], 20 fps; `substeps` internal steps per frame so that
      onsets land at a sub-frame phase (the "partially integrated first frame" of [A §0.1]).
    """

    skirt_order: float = 3.0
    skirt_reach_bands: int = 10          # bands each side over which a tone's leakage is accumulated
    attack_model: str = "U"              # 'U' | 'BQ'
    attack_k: float = 0.5                # power one-pole τ_a = attack_k/Δf ('U'); 'BQ' forces 1/π.
                                         # 0.5 -> 10-90 % rise = 1.1/Δf (406 ms @ band 10, 203 ms @ band 20), the
                                         # 6th-order-BP figure of [A §1]; last 10 dB of an instant onset take
                                         # 1.47·τ_a = 270 ms @ 39 Hz ([L §1.6]: 216 ms). 1.0 = critically resolved
                                         # bank / long FFT window (slowest plausible); 1/π = single biquad (fastest).
    filter_release_k: float | None = None  # ring-down τ_r = k/Δf; None = same as attack (a linear filter's rise
                                         # and ring-down share poles). 1/π = slowest pole pair of a 6th-order
                                         # Butterworth (37 dB/s @ 39 Hz, 74 dB/s @ 78 Hz); U-model symmetric
                                         # release is 11.7 dB/s @ 39 Hz, 23 @ 78 Hz, 47 @ 156 Hz — below ~150 Hz
                                         # the FILTER, not the decay pref, limits how fast a note disappears.
    attack_min_s: float = 0.005
    attack_max_s: float = 0.600
    decay_s: float = 1.0                 # /-prefs/rta/decay as found (0.25 .. 16, log steps)
    release_law_db: float = 60.0         # fall rate dB/s = release_law_db / decay_s   (UNCERTAIN law)
    peak_hold_s: float = 0.0             # /-prefs/rta/peakhold: 0 = OFF
    det: str = "PEAK"                    # 'PEAK' | 'RMS'
    rms_noise_offset_db: float = -8.0    # noise-like paths under RMS relative to PEAK [A §2 det row]
    rms_noise_sd_scale: float = 0.6
    noise_sd_scale: float = 1.0          # multiply the statistical fluctuation in dB (0 = dead-flat beds)
    nu_gauss_min: float = 4.0            # ν = Δf·T below this -> χ²(2ν) power statistics, else Gaussian-dB
    noise_sd_min_db: float = 0.5
    self_noise_db: float = -115.0        # analyser/electrical floor per band (keeps log10 finite)
    gain_offset_db: float = 0.0          # /-prefs/rta/gain if it offsets /meters/15 (UNCONFIRMED) [A §2]
    quantum_db: float = 1.0 / 256.0      # int16/256 wire format
    substeps: int = 4                    # internal steps per 50 ms frame (12.5 ms)
    pre_roll_s: float = 1.5              # settle beds / established rings before frame 0
    frame_jitter_s: float = 0.003        # timestamp jitter (uniform ±) on the reported ts: /meters/15 arrives over
                                         # UDP through an asyncio loop; exact 50.000 ms spacing is a simulator tell
    drop_frame_prob: float = 0.0         # probability a frame is dropped (ts gap), [A §7 (vii)]

    def with_overrides(self, **kw: Any) -> "AnalyserSettings":
        return replace(self, **kw)

    # derived quantities ------------------------------------------------------------------------
    def attack_tau_s(self, i: int) -> float:
        df = band_bandwidth_hz(i)
        tau = (1.0 / (math.pi * df)) if self.attack_model.upper() == "BQ" else (self.attack_k / df)
        return min(self.attack_max_s, max(self.attack_min_s, tau))

    def filter_release_tau_s(self, i: int) -> float:
        if self.filter_release_k is None:
            return self.attack_tau_s(i)
        return min(self.attack_max_s, max(self.attack_min_s, self.filter_release_k / band_bandwidth_hz(i)))

    @property
    def release_db_per_s(self) -> float:
        return self.release_law_db / max(1e-6, self.decay_s)

    def noise_nu(self, i: int) -> float:
        """ν_i = Δf_i·T_avg: independent samples behind one displayed value (1.0 @ band 10 and @ band 40,
        2.2 @ 50, 4.3 @ 60, 17 @ 80, 35 @ 90)."""
        return max(1.0, band_bandwidth_hz(i) * max(FRAME_S, self.attack_tau_s(i)))

    def noise_scale(self, i: int) -> float:
        sc = self.noise_sd_scale
        if self.det.upper() == "RMS":
            sc *= self.rms_noise_sd_scale
        return sc

    def noise_sd_db(self, i: int) -> float:
        """Gaussian-dB sd used when ν >= nu_gauss_min: 4.34/√ν (2.1 dB @ band 60, 1.05 @ 80, 0.73 @ 90)."""
        sd = max(self.noise_sd_min_db, 4.34 / math.sqrt(self.noise_nu(i)))
        return sd * self.noise_scale(i)


DEFAULT_ANALYSER = AnalyserSettings()

# Presets for the ballistics matrix of [A §7] (use with AnalyserSettings.with_overrides(**PRESET)).
ANALYSER_PRESETS: dict[str, dict[str, Any]] = {
    "default": {},
    "bq": {"attack_model": "BQ"},
    "u_slow": {"attack_k": 1.0},                                       # critically-resolved bank: τ_a = 1/Δf
    "butter3_release": {"filter_release_k": 1.0 / math.pi},           # ring-down of the slowest 6th-order pole: 13.6·Δf dB/s
    "skirt_n2": {"skirt_order": 2.0},
    "skirt_steep": {"skirt_order": 5.0},
    "decay_fast": {"decay_s": 0.25},          # what arm SHOULD force
    "decay_4s": {"decay_s": 4.0},
    "decay_16s": {"decay_s": 16.0},
    "release_law_17": {"release_law_db": 17.0},
    "release_law_240": {"release_law_db": 240.0},
    "rms": {"det": "RMS"},
    "peakhold_2s": {"peak_hold_s": 2.0},
    "flat_beds": {"noise_sd_scale": 0.0},
}

# -- feedback loop defaults [L §1.2-1.5, A §0.2-0.3] -----------------------------------------------
# growth rate above threshold R = excess_dB / τ_loop  (exponential in amplitude = linear in dB)
# loop period τ_loop = d/343 + electronics: wedge 1.2 m ≈ 5 ms, tops 3 m ≈ 10-11 ms, lav 20-25 ms,
# reverberant/distant 35-110 ms.  Sub-threshold regenerative boost = -20·log10(1-10^(e/20)) dB:
# +6.0 @ e=-6, +10.7 @ -3, +19.3 @ -1, +25 @ -0.5.  Ring-down below threshold at |e|/τ dB/s.
TAU_LOOP_WEDGE_S = 0.005
TAU_LOOP_TOPS_S = 0.010
TAU_LOOP_LAV_S = 0.022
TAU_LOOP_REVERB_S = 0.070


def regen_boost_db(excess_db: float, cap_db: float = 60.0) -> float:
    """Closed-loop steady-state boost 1/(1-|L|) at a phase-aligned candidate, excess<0 [L §1.5 table]."""
    if excess_db >= -1e-3:
        return cap_db
    g = 10.0 ** (excess_db / 20.0)
    return min(cap_db, -20.0 * math.log10(1.0 - g))


# Regeneration is frequency-selective: the closed-loop power response of a loop with round-trip gain g<1 and
# delay τ at detuning δ (Hz) from a phase-aligned candidate is the comb
#     |1/(1 − g·e^{−j2πδτ})|² = 1 / ((1−g)² + 4g·sin²(πδτ))            [L §1.2/§1.5: candidates every 1/τ]
# peak 1/(1−g)² on the mode (= regen_boost_db), 1/(1+g)² (< 0 dB) half-way between modes, −3 dB half-width
# ≈ (1−g)/(2πτ√g) Hz: 12 Hz at g=−4 dB, 3.7 Hz at −1 dB for τ=12 ms. A programme line must sit within a few
# Hz (tens of cents at 200 Hz, a few cents at 2 kHz) of the mode to get the full boost; broadband excitation
# gets the MEAN of the comb over the analyser band (→ 1/(1−g²) once the band spans a comb period).
G_MAX_SUBTHRESHOLD: float = 10.0 ** (-0.25 / 20.0)   # cap for the steady-state formulas (e = −0.25 dB)


def comb_gain_lin(g: float, detune_hz: float, tau_s: float) -> float:
    g = min(G_MAX_SUBTHRESHOLD, max(0.0, g))
    s_ = math.sin(math.pi * detune_hz * tau_s)
    return 1.0 / ((1.0 - g) ** 2 + 4.0 * g * s_ * s_)


def comb_band_mean_lin(g: float, band_bw_hz: float, tau_s: float) -> float:
    """Mean of the comb power response over a band of width ``band_bw_hz`` centred on the mode (closed form of
    ∫ dx/((1−g)²+4g sin²x): full periods contribute π/(1−g²) each, the remainder atan((1+g)/(1−g)·tan r)/(1−g²))."""
    g = min(G_MAX_SUBTHRESHOLD, max(0.0, g))
    if g <= 1e-6:
        return 1.0
    X = math.pi * 0.5 * band_bw_hz * tau_s            # half-band in units of x = πδτ (period π)
    if X <= 1e-9:
        return 1.0 / (1.0 - g) ** 2
    one_m_g2 = 1.0 - g * g
    m = math.floor(X / math.pi + 0.5)
    r = X - m * math.pi                                # remainder in [−π/2, π/2)
    F_r = math.atan((1.0 + g) / (1.0 - g) * math.tan(r)) / one_m_g2
    integral_0_X = m * math.pi / one_m_g2 + F_r
    return integral_0_X / X


# Hard-clip harmonics of a howl at 0 dBFS [L §2.2, A §4.3]: odd partials, H3 -12 (≈3-6 dB over), H5 -18;
# a little even-order from driver excursion (H2 -30). Absent until the fundamental is within clip_knee of FS.
CLIP_HARMONICS: tuple[tuple[int, float], ...] = ((2, -30.0), (3, -12.0), (5, -18.0), (7, -24.0))
CLIP_KNEE_DB: float = -10.0

# -- programme timbres: partial levels re H1 (dB) [A §4.2; S-numbers refer to the corpus table A §7] -----
TIMBRES: dict[str, tuple[float, ...]] = {
    # sung open vowel around A3/A4: H2..H4 within -6..+6 of H1, H5.. -10..-25 (S4: -30,-28,-33,-38,-45..-55);
    # partials continue to H16 (-6 dB/oct source slope after the formants) — they matter for what a vocal excites
    "voice": (0.0, 2.0, -3.0, -8.0, -15.0, -18.0, -21.0, -25.0, -28.0, -30.0, -32.0, -34.0, -36.0, -38.0, -40.0, -42.0),
    # closed vowel / head voice: near-sine (H2 -15..-30)
    "voice_closed": (0.0, -20.0, -26.0, -32.0, -40.0, -46.0),
    # speech, F0 110-140 Hz: F1 (k 4-7), F2 (k 10-14), F3 'speaker's formant' 2.4-3.5 kHz (k 19-28), 32 partials —
    # the upper partials glide through any 2-4 kHz loop mode on every syllable (what makes a lectern 'ring')
    "speech": (0.0, -2.0, -4.0, -3.0, -6.0, -8.0, -12.0, -16.0, -18.0, -17.0, -15.0, -14.0, -15.0, -17.0, -20.0, -23.0,
               -26.0, -28.0, -27.0, -25.0, -24.0, -24.0, -25.0, -27.0, -29.0, -31.0, -33.0, -35.0, -37.0, -39.0, -41.0, -43.0),
    # bass guitar through small tops: H2 > H1 (S1: H1 -38, H2 -35, H3 -42, H4 -48)
    "bass_gtr": (0.0, 3.0, -4.0, -10.0, -16.0, -22.0),
    # clean 808 / sine sub: no detectable family (H2 -25..-40)
    "synth_sine_bass": (0.0, -35.0, -45.0),
    "808": (0.0, -35.0, -45.0),
    # plucked electric guitar at onset (S5: -32,-30,-35,-38,-42,-45)
    "el_guitar": (0.0, 2.0, -3.0, -6.0, -10.0, -13.0),
    "ac_guitar": (0.0, -2.0, -6.0, -9.0, -12.0, -16.0, -20.0),
    # Hammond 8' alone / flue stop: near-sine. The first corpus used (0, -24, -30); the brief's S8 spec says H2 -52, H3 -58
    # (tonewheel leakage only). -36/-50 is the middle ground: H2 sits at or below a bed 20-25 dB under H1, i.e. the
    # detector gets NO usable family (analyser brief §4.3) — the honest hard case.
    "organ_flue": (0.0, -36.0, -50.0),
    # organ 8'+4' registration (S6 "C-E-G + H2s")
    "organ_8_4": (0.0, -3.0, -30.0, -12.0),
    "flute": (0.0, -15.0, -28.0, -35.0),           # low/mid register
    "flute_high": (0.0, -26.0, -40.0, -50.0),      # upper register: ≈ pure sine (analyser brief §4.2)
    "whistle": (0.0, -42.0),                        # human whistle: trace of H2 only
    "sine_lead": (0.0, -48.0),                      # synth sine/triangle-ish lead
    "hum": (0.0, -6.0, -3.0, -14.0, -8.0, -20.0, -12.0),   # mains hum + buzz: odd-rich exact family (rectifier buzz)
    "sine": (0.0,),
    "piano": (0.0, -3.0, -6.0, -10.0, -14.0, -18.0, -22.0, -26.0),
    # sawtooth pad: 1/k amplitudes = -6 dB/oct (S21)
    "saw": tuple(-20.0 * math.log10(k) for k in range(1, 9)),
    # square / symmetric clip: odd only (H3 -9.5, H5 -14, H7 -17)
    "square": (0.0, -60.0, -9.5, -60.0, -14.0, -60.0, -17.0),
}


# -- bus PEQ actuator (docs/PEQ_ACTUATOR_DESIGN.md) ---------------------------------------------------
# The X32 bus has six parametric bands (device.yaml: f log 20..20000 Hz in 201 steps = 0.0498 oct/step; q log 10 -> 0.3
# in 72 steps = x1.0506/step; g +-15 dB in 0.25 dB). A notch placed at the detection's interpolated frequency lands
# within +-0.025 oct of the ring, so peaking_gain_db above -- the same RBJ prototype as the detector's
# bell_attenuation_db -- serves the PEQ actuator unchanged. The desk's own PEQ shape is UNCONFIRMED (design S9).
PEQ_BANDS = 6
PEQ_Q_DEFAULT = 6.103            # "Q 6" on the desk grid: the design's first-notch Q (D3)
PEQ_NOTCH_STEP_DB = -3.0
PEQ_NOTCH_MAX_DB = -12.0         # per-actuator cap (D3): a Q 6 -12 removes less programme than a GEQ -9 at any GEQ Q
PEQ_BUDGET_DEFAULT = 4           # distinct bands per session: 4 of the 6, two left to the engineer
PEQ_MERGE_OCT = 0.08             # a detection this close to an owned notch deepens it (peq_merge_oct)
_PEQ_F_LO, _PEQ_F_HI, _PEQ_F_STEPS = 20.0, 20000.0, 201
_PEQ_Q_HI, _PEQ_Q_LO, _PEQ_Q_STEPS = 10.0, 0.3, 72


def peq_grid_hz(f_hz: float) -> float:
    """Snap ``f_hz`` to the desk's frequency grid: the nearest of 201 log steps from 20 to 20000 Hz
    (2331 -> 2349.8, 525.4 -> 532.1, 1788.9 -> 1782.5)."""
    x = math.log(max(_PEQ_F_LO, min(_PEQ_F_HI, float(f_hz))) / _PEQ_F_LO) / math.log(_PEQ_F_HI / _PEQ_F_LO)
    i = round(x * (_PEQ_F_STEPS - 1))
    return _PEQ_F_LO * (_PEQ_F_HI / _PEQ_F_LO) ** (i / (_PEQ_F_STEPS - 1))


def peq_grid_q(q: float) -> float:
    """Snap ``q`` to the desk's Q grid: 72 log steps from 10 down to 0.3 (6 -> 6.103, 4 -> 3.913, 8 -> 7.812)."""
    x = math.log(max(_PEQ_Q_LO, min(_PEQ_Q_HI, float(q))) / _PEQ_Q_HI) / math.log(_PEQ_Q_LO / _PEQ_Q_HI)
    i = round(x * (_PEQ_Q_STEPS - 1))
    return _PEQ_Q_HI * (_PEQ_Q_LO / _PEQ_Q_HI) ** (i / (_PEQ_Q_STEPS - 1))
