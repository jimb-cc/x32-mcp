"""rtasim — offline X32 RTA simulator + programme/feedback corpus + detector harness (TEST SUPPORT ONLY).

Not production code. Pure Python + stdlib, deterministic given a seed.

Modules
-------
* :mod:`rtasim.physics`   — every physical constant / default in one place, with brief citations.
* :mod:`rtasim.analyser`  — 100-band 1/10-octave RTA model: filter skirts, per-band attack ballistics,
                            PEAK/decay/peak-hold display ballistics, estimation noise, quantisation.
* :mod:`rtasim.sources`   — programme generators (harmonic notes, drums, beds, sequencers, common-mode
                            gain) and the electro-acoustic :class:`FeedbackRing` loop model.
* :mod:`rtasim.render`    — the mixer/renderer: sources + rings + GEQ + analyser → frames + ground-truth trace.
* :mod:`rtasim.scenarios` — the corpus registry ``SCENARIOS`` and ``frames(scenario, seed)``.
* :mod:`rtasim.harness`   — ``evaluate(detector_factory, ...)`` open/closed loop, metrics, table, JSON.
"""

from .physics import FRAME_S, RTA_BAND_HZ, GEQ_BAND_HZ, AnalyserSettings  # noqa: F401
from .analyser import Analyser, skirt_weights, band_position  # noqa: F401
from .sources import (  # noqa: F401
    FeedbackRing, HarmonicNote, TonalBurst, NoiseBurst, PinkBed, DrumHit, DrumPattern, BassLine, Melody,
    ChordPad, CommonModeGain, DrivenResonance, note_hz, TIMBRES,
)
from .render import Renderer, Scene  # noqa: F401
from .scenarios import SCENARIOS, Scenario, frames, ground_truth  # noqa: F401
from .harness import evaluate, Results  # noqa: F401
