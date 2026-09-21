"""100-band 1/10-octave RTA model (test support).

Pipeline per band i, per internal substep dt = FRAME_S/substeps:

  input power  P_i = Σ_tones 10^((L+W_i(f))/10)   (W_i = filter skirt of band i at f, dB ≤ 0)
             + Σ_noise 10^(L_i/10)                 (noise-like content is specified per band)
  envelope     e_i += (P_i - e_i)·(1 - exp(-dt/τ(i)))   one-pole on power, τ = τ_a rising / τ_r falling
               (slow at LF, ~instant at HF; symmetric by default — see AnalyserSettings.filter_release_k)
               (three parallel paths are kept — noise-like, programme tones, feedback tones — which is legal
               because the filter is linear in power; it lets ground truth ask "does the ring dominate?")
  fluctuation  the noise-like path is multiplied by the band's estimation noise (χ²(2ν) at LF, Gaussian-dB
               at HF, AR(1)-correlated over τ_a; analyser brief §1)
  display      PEAK: d_i = max(10·log10(max over the frame of total_i), d_i − R_rel·T)  [+ optional peak-hold]
  output       + gain_offset, quantised to 1/256 dB, clamped to [-128, 0]

See :class:`rtasim.physics.AnalyserSettings` for every constant and its provenance.
"""

from __future__ import annotations

import math
import random
from typing import Iterable, Sequence

from .physics import (
    FRAME_S, RTA_BANDS, RTA_BAND_HZ, RTA_FLOOR_DB, RTA_CLIP_DB, FILTER_Q, AnalyserSettings, DEFAULT_ANALYSER,
)

_LOG2 = math.log(2.0)
_F0 = RTA_BAND_HZ[0]


def band_position(f_hz: float) -> float:
    """Continuous band coordinate u of a frequency: band i centre is u = i (u = 10·log2(f/f_0))."""
    return 10.0 * math.log(f_hz / _F0) / _LOG2


def nearest_band(f_hz: float) -> int:
    return min(RTA_BANDS - 1, max(0, int(round(band_position(f_hz)))))


def skirt_db(delta_bands: float, order: float) -> float:
    """Attenuation (dB ≤ 0) of a band's filter at a tone ``delta_bands`` away from its centre:
    Butterworth band-pass of order 2N, g = Q·(2^δ − 2^−δ), |H|² = 1/(1+g^2N)  [analyser brief §3].
    N=3: -3.0 @ 0.5 band (edge), -18.1 @ 1, -36.1 @ 2, -46.7 @ 3."""
    d_oct = delta_bands / 10.0
    g = abs(FILTER_Q * (2.0 ** d_oct - 2.0 ** (-d_oct)))
    return -10.0 * math.log10(1.0 + g ** (2.0 * order))


_SKIRT_CACHE: dict[tuple[int, float, int], tuple[int, tuple[float, ...]]] = {}


def skirt_weights(f_hz: float, order: float = 3.0, reach: int = 10) -> tuple[int, tuple[float, ...]]:
    """(first_band, linear power weights) of a tone at ``f_hz`` into bands first..first+len-1.
    Cached at 1-cent resolution. Frequencies outside ~15 Hz..21 kHz return an empty tuple."""
    if not (14.0 < f_hz < 21000.0):
        return 0, ()
    u = band_position(f_hz)
    key = (int(round(u * 120.0)), order, reach)
    hit = _SKIRT_CACHE.get(key)
    if hit is not None:
        return hit
    uq = key[0] / 120.0
    lo = max(0, int(math.floor(uq)) - reach)
    hi = min(RTA_BANDS - 1, int(math.ceil(uq)) + reach)
    ws = tuple(10.0 ** (skirt_db(i - uq, order) / 10.0) for i in range(lo, hi + 1))
    out = (lo, ws)
    if len(_SKIRT_CACHE) > 200_000:
        _SKIRT_CACHE.clear()
    _SKIRT_CACHE[key] = out
    return out


class Analyser:
    """Stateful RTA. Feed substeps with :meth:`substep`, read a frame with :meth:`end_frame`.

    ``tones_*`` arguments are iterables of ``(f_hz, level_db)``; ``noise`` is an iterable of
    ``(band, level_db)`` (per-band noise-like power, e.g. a pink bed or a cymbal wash).
    """

    def __init__(self, settings: AnalyserSettings = DEFAULT_ANALYSER, seed: int = 1,
                 band_hz: Sequence[float] = RTA_BAND_HZ) -> None:
        self.s = settings
        self.band_hz = tuple(band_hz)
        n = self.n = len(self.band_hz)
        self.rng = random.Random(seed * 7919 + 13)
        self.substeps = max(1, int(settings.substeps))
        self.dt = FRAME_S / self.substeps
        self.tau_a = [settings.attack_tau_s(i) for i in range(n)]
        self.tau_r = [settings.filter_release_tau_s(i) for i in range(n)]
        self.alpha = [1.0 - math.exp(-self.dt / t) for t in self.tau_a]      # rise coefficient per substep
        self.beta = [1.0 - math.exp(-self.dt / t) for t in self.tau_r]       # ring-down coefficient
        # estimation noise of noise-like content [A §1 'statistical floor']: a band-power estimate with
        # ν = Δf·T_avg independent samples is χ²(2ν)/(2ν)-distributed (ν≈1 below band ~45: exponential —
        # deep nulls, modest +3..+6 dB peaks); for ν >= nu_gauss_min it is modelled as Gaussian in dB with
        # sd 4.34/√ν. Both AR(1)-correlated frame to frame with ρ_i = exp(-T/τ_a(i)).
        self.rho = [math.exp(-FRAME_S / max(1e-4, self.tau_a[i])) for i in range(n)]
        self.rho_c = [math.sqrt(max(0.0, 1.0 - r * r)) for r in self.rho]
        self.nu = [settings.noise_nu(i) for i in range(n)]
        self.sd_scale = [settings.noise_scale(i) for i in range(n)]
        self.sigma = [settings.noise_sd_db(i) for i in range(n)]          # used on the Gaussian branch
        self.chi_states: list[list[float] | None] = []
        for i in range(n):
            if self.nu[i] < settings.nu_gauss_min and self.sd_scale[i] > 0.0:
                dof = 2 * max(1, int(round(self.nu[i])))
                self.chi_states.append([self.rng.gauss(0.0, 1.0) for _ in range(dof)])
            else:
                self.chi_states.append(None)
        self.nfl_db = [0.0] * n
        for i in range(n):
            self.nfl_db[i] = self._noise_db(i, init=True)
        self.nfl_lin = [10.0 ** (x / 10.0) for x in self.nfl_db]
        self.noise_off_lin = 10.0 ** ((settings.rms_noise_offset_db if settings.det.upper() == "RMS" else 0.0) / 10.0)
        self.self_lin = 10.0 ** (settings.self_noise_db / 10.0)
        self.rel_db_per_frame = settings.release_db_per_s * FRAME_S
        self.order = settings.skirt_order
        self.reach = int(settings.skirt_reach_bands)
        # state
        self.e_noise = [0.0] * n
        self.e_prog = [0.0] * n
        self.e_fb = [0.0] * n
        self.frame_max = [0.0] * n           # max over substeps of total power (PEAK semantics)
        self.frame_max_fb = [0.0] * n
        self.disp_db = [RTA_FLOOR_DB] * n
        self.disp_dom = [0.0] * n            # feedback share of the power that SET the displayed value
        self.hold_left = [0.0] * n
        self.frames_out = 0
        self.last_total = [self.self_lin] * n

    # -- estimation noise ------------------------------------------------------------------------
    def _noise_db(self, i: int, init: bool = False) -> float:
        sc = self.sd_scale[i]
        if sc <= 0.0:
            return 0.0
        rng = self.rng
        st = self.chi_states[i]
        if st is not None:
            if not init:
                r, c = self.rho[i], self.rho_c[i]
                for j in range(len(st)):
                    st[j] = st[j] * r + c * rng.gauss(0.0, 1.0)
            p = sum(g * g for g in st) / len(st)
            return 10.0 * math.log10(max(1e-6, p)) * sc
        # Gaussian-in-dB branch (ν large enough for the CLT)
        sd = self.sigma[i]
        if init:
            return rng.gauss(0.0, sd)
        return self.nfl_db[i] * self.rho[i] + self.rho_c[i] * rng.gauss(0.0, sd)

    # -- input accumulation ----------------------------------------------------------------------
    def _accumulate_tones(self, acc: list[float], tones: Iterable[tuple[float, float]]) -> None:
        order, reach = self.order, self.reach
        for f, lv in tones:
            if lv <= -140.0:
                continue
            lo, ws = skirt_weights(f, order, reach)
            if not ws:
                continue
            a = 10.0 ** (lv / 10.0)
            j = lo
            for w in ws:
                acc[j] += a * w
                j += 1

    def substep(self, tones_prog: Iterable[tuple[float, float]], tones_fb: Iterable[tuple[float, float]],
                noise: Iterable[tuple[int, float]]) -> None:
        n = self.n
        p_prog = [0.0] * n
        p_fb = [0.0] * n
        p_noise = [0.0] * n
        self._accumulate_tones(p_prog, tones_prog)
        self._accumulate_tones(p_fb, tones_fb)
        for b, lv in noise:
            if 0 <= b < n and lv > -140.0:
                p_noise[b] += 10.0 ** (lv / 10.0)
        al, be = self.alpha, self.beta
        en, ep, ef = self.e_noise, self.e_prog, self.e_fb
        fm, fmf = self.frame_max, self.frame_max_fb
        nl, off, sl = self.nfl_lin, self.noise_off_lin, self.self_lin
        for i in range(n):
            a, bb = al[i], be[i]
            x = en[i]; d = p_noise[i] - x; x += d * (a if d > 0.0 else bb); en[i] = x
            y = ep[i]; d = p_prog[i] - y; y += d * (a if d > 0.0 else bb); ep[i] = y
            z = ef[i]; d = p_fb[i] - z; z += d * (a if d > 0.0 else bb); ef[i] = z
            tot = x * nl[i] * off + y + z + sl
            if tot > fm[i]:
                fm[i] = tot
                fmf[i] = z

    # -- frame output ----------------------------------------------------------------------------
    def end_frame(self) -> tuple[list[float], list[float]]:
        """Finish the current frame: returns (display values dB[100], feedback dominance[100] in 0..1
        = share of the power behind each DISPLAYED value that came from the feedback path; a released/held
        peak keeps the provenance of whatever set it). Advances the estimation-noise process."""
        s = self.s
        n = self.n
        rel = self.rel_db_per_frame
        hold = s.peak_hold_s
        q = s.quantum_db
        goff = s.gain_offset_db
        out = [0.0] * n
        d = self.disp_db
        dd = self.disp_dom
        hl = self.hold_left
        fm, fmf = self.frame_max, self.frame_max_fb
        for i in range(n):
            tot = fm[i]
            dom_now = fmf[i] / tot if tot > 0.0 else 0.0
            tdb = 10.0 * math.log10(tot) if tot > 0.0 else RTA_FLOOR_DB
            cur = d[i]
            if tdb >= cur:
                cur = tdb
                hl[i] = hold
                dd[i] = dom_now
            else:
                if hl[i] > 0.0:
                    hl[i] -= FRAME_S
                else:
                    cur = max(tdb, cur - rel)
                    if cur == tdb:
                        dd[i] = dom_now
                # while releasing/holding, the displayed value still 'belongs' to whatever set the peak
            d[i] = cur
            v = cur + goff
            v = round(v / q) * q
            if v > RTA_CLIP_DB:
                v = RTA_CLIP_DB
            elif v < RTA_FLOOR_DB:
                v = RTA_FLOOR_DB
            out[i] = v
        dom = list(dd)
        self.last_total = list(fm)
        # reset frame peak trackers, advance the estimation-noise process
        for i in range(n):
            fm[i] = 0.0
            fmf[i] = 0.0
        nd = self.nfl_db
        for i in range(n):
            nd[i] = self._noise_db(i)
        self.nfl_lin = [10.0 ** (x / 10.0) for x in nd]
        self.frames_out += 1
        return out, dom

    def render_static(self, tones: Sequence[tuple[float, float]], noise: Sequence[tuple[int, float]] = (),
                      frames: int = 40) -> list[float]:
        """Convenience for tests: feed a constant input for ``frames`` frames and return the last frame."""
        last: list[float] = []
        for _ in range(frames):
            for _ in range(self.substeps):
                self.substep(tones, (), noise)
            last, _ = self.end_frame()
        return last
