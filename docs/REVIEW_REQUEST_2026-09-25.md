# Review request — 2026-09-25: the desk analyser sleeps, and real programme false-cuts

*For the frontier model. Written by the independent reviewer during the studio session of 2026-09-25 (X32RACK-Jim, FW 4.13,
now at 192.168.1.141). Every number below is printed by a script in `scripts/` from a raw log in `docs/research/data/`.*

Repo state: `main` @ `f214b44`. Your review-response bundle is pushed as **PR #16** (`review-response/main` → main, A1–A3),
**PR #17** (`review-response/rta-band-grid-followup` → `review/rta-band-grid`, B1–B2, so PR #15 carries it) and **PR #18**
(`review-response/peq-sim-tier-b` → `review/peq-sim`, C1–C2, so PR #14 carries it). All six are approved by the reviewer;
the merge is Jim's (the reviewer's harness refuses `gh pr merge`). Merge order: #16 → main; #17 → #15's branch, then #15 →
main; #18 → #14's branch, then #14 → main.

---

## 1. `/meters/15` carries a static floor until the console has shown an RTA page once (product bug, highest consequence)

`docs/research/data/rta_dormant_static_floor_2026-09-25.jsonl.gz`, `scripts/replay_rta_log.py`.

The desk had been powered up this morning and left on its HOME screen. With Spotify at −10 dBFS on Ch 2/3 routed to Main
(`get_meters` confirmed the channel levels), RTA source 72 (Main), pos POST, det PEAK (read back 1), decay 0.25 (read back),
**every one of 3597 frames over 180 s read −97.0 in all 100 bands** — the RMS floor, bit-identical, no signal.

Then, from the same script connection, cycling the console's screen remotely:

| write | stream afterwards |
|---|---|
| `/-stat/screen/screen 0`, `/-stat/screen/CHAN/page 3` (channel EQ page) | still −97.0 everywhere |
| `/-stat/screen/screen 1`, `/-stat/screen/METER/page 4` (the METERS "RTA" page) | **alive within 0.8 s**: max −26 dB, 84 distinct values |
| `/-stat/screen/METER/page 0`, then screen/pages restored to 0/0/0 | still alive (max −26, 80–85 distinct values) |

So the analyser behind `/meters/15` is started lazily by the console when its RTA page is displayed, and keeps running after
the page is left (alive for ≥ 12 min so far; whether it ever sleeps again is being watched). Both earlier studio sessions
worked only because Jim had navigated to an RTA/EQ page by hand before the scripts ran. On a gig the operator has no reason to
visit that page, so `feedback_watch` / `ring_out` would arm on a dead stream: no candidates, no cuts, no alert, and today's
detector would report a perfectly quiet room.

**Ask:** a preflight in `cfs.py` (or `LiveMeters.start`) that, when the first ~10 frames are bit-identical AND flat across
all bands (every band equal), saves `/-stat/screen/screen` and `/-stat/screen/METER/page`, writes `1` and `4`, waits for a
live frame (≤ 1 s), restores both, and records the wake in the report / `cfs_status`. The FakeDesk needs a dormant mode
(static floor until METER page 4 is written) so the preflight has a test. The at-arm "frozen display" machinery should also
learn this frame shape: a flat static floor is "analyser not running", not "peak hold".

Also observed this session: frame period **50.0 ms** on both 180 s logs (3598 frames / 179.9 s), not the 52.0 ms of the
2026-09-22/23 logs. Same script path, same desk; the difference is unexplained (the earlier logs were taken with the console
on its RTA page — a display-load effect?). The corpus and the detector's `frame_period_s` assume 50.

## 2. Real programme through the real analyser: 20 STRONG emissions in 180 s (detector, highest consequence)

`docs/research/data/programme_spotify_main_2026-09-25_peak025.jsonl.gz` (3598 frames, det PEAK read back, decay 0.25 read
back, RTA Main post-EQ, Spotify on Ch 2/3 at about −10 dBFS, peak-band p50 / p95 −26.9 / −21.9 dBFS), replayed with
`scripts/replay_rta_log.py` — the product `FeedbackDetector` built as `CfsManager` builds it (`DetectorConfig.from_descriptor`,
watch mode, fed from the first frame):

```
EMISSIONS (tier A would cut each): 20
  t=   6.75    1088 Hz   -23.0 dB  prom  21.6  slope   -2.1  STRONG  narrow no_family stable sustained new_energy in_window glided_in rise6dB
  t=  13.26    1088 Hz   -24.0 dB  prom  15.2  slope   13.4  STRONG  ... rise7dB
  t=  22.66    1433 Hz   -24.8 dB  prom  15.5  slope    8.8  STRONG  ... rise7dB
  t=  36.47     947 Hz   -28.9 dB  prom  16.3  slope   10.0  STRONG  ... rise7dB
  t=  37.72     722 Hz   -27.0 dB  prom  22.3  slope   -0.4  STRONG  ... rise6dB
  t=  40.72     634 Hz   -29.4 dB  prom  19.1  slope    4.2  STRONG  ... rise6dB
  t=  50.67    1650 Hz   -24.0 dB  prom  15.1  slope   25.4  STRONG  ... rise6dB
  t=  52.52     206 Hz   -27.6 dB  prom  20.5  slope   14.7  STRONG  ... rise7dB
  t=  54.72     583 Hz   -29.9 dB  prom  16.7  slope    6.3  STRONG  ... rise6dB
  t=  70.48    1088 Hz   -22.0 dB  prom  15.8  slope    9.1  STRONG  ... rise6dB
  t=  81.23     360 Hz   -24.6 dB  prom  16.3  slope   36.4  STRONG  ... fastrise16dB@82dB/s
  t=  87.39    1414 Hz   -25.3 dB  prom  14.6  slope   -1.1  STRONG  ... rise6dB
  t=  90.29     719 Hz   -19.3 dB  prom  18.6  slope    9.0  STRONG  ... rise9dB
  t= 104.50     637 Hz   -26.7 dB  prom  17.4  slope    8.3  STRONG  ... rise6dB
  t= 105.09    1088 Hz   -27.0 dB  prom  15.1  slope    7.5  STRONG  ... rise7dB
  t= 115.90    1653 Hz   -30.5 dB  prom  13.8  slope   23.3  STRONG  ... rise6dB
  t= 144.36     676 Hz   -34.0 dB  prom  31.5  slope   42.1  STRONG  ... rise18dB
  t= 150.16     471 Hz   -47.0 dB  prom  28.0  slope    9.8  STRONG  ... rise6dB
  t= 152.41    1015 Hz   -39.4 dB  prom  22.4  slope    2.7  STRONG  ... rise7dB
  t= 155.41    1166 Hz   -29.9 dB  prom  22.3  slope   20.0  STRONG  ... rise17dB
candidate klass-frames: TRACK 30854, MUSICAL 35595, MODERATE 354, STRONG 36; MODERATE/STRONG tracks 125
```

Ring-out mode gives the same 20. Every emission is a sustained musical note (the spectra around them are in the log: e.g.
band 58 at −23…−30 dB with neighbours at −40, wobbling ±3 dB frame to frame for two seconds before the emission), passing
NARROW ∧ NO-FAMILY ∧ STABLE ∧ SUSTAINED ∧ NEW ∧ IN-WINDOW and then earning RISE (6–9 dB) or, twice, FAST-RISE. On a gig with
`feedback_watch` armed on a monitor mix this is one wrong GEQ cut every nine seconds until the notch budget is gone.

The pre-registered gate (G1–G4) reported FP 0 on the synthetic corpus for this same detector. The corpus's programme models
(`loud_band`, the strum/pad sources) evidently do not produce what a 1/10-octave, 20 fps, decay-0.25 analyser shows of a pop
mix: bass-heavy synth/vocal notes whose partials are not present as lines ≥ `partial_prominence_db` at the H2…H5 positions.
No family → no veto → BASE, and a note that swells 6 dB is RISE.

**Asks:**
1. Treat this log as the first real-programme corpus item and add a gate **G5**: replay of every desk-logged programme file
   under `docs/research/data/programme_*.jsonl.gz` must give **0 emissions** in watch and ring-out mode, on the shipped
   detector, before any further detector change is accepted. The reviewer will pre-register the exact rule in
   `scripts/accept_discriminator.py` (`--replay docs/research/data/programme_*.jsonl.gz`) once you confirm the reading.
2. Diagnose which predicate lets these through (the NO-FAMILY leg on the real spectra, or RISE on a swelling note) with
   `scripts/replay_rta_log.py --json` per emission, and say what evidence a real ring has that these notes do not (the
   reviewer's guess: none at 6 dB of RISE — a note and a slow ring are the same line at this resolution; only FAST-RISE,
   LOUD, AT-ARM, PROBE and the post-cut verdict separate them, so RISE alone may have to become tier-B evidence).
3. More logs are coming today: the same desk with the band's own programme tonight (The Molecules, 19:30, in-ears on the mix
   buses) if Jim agrees to a passive log, and a quieter genre if time permits. The reviewer will log at 4 decimals
   (`int16/256` resolution) so the frozen-frame logic can be replayed too; today's log is rounded to 0.01 dB.

## 3. C2 verified at the cfs level; note the GEQ merge

PR #18 now carries `test_h_bystander_verdict_from_a_neighbours_cut_does_not_bar_the_line_from_its_own_tier_b_engagement`
(`tests/integration/test_cfs_policy_breakers.py`): A at 1 kHz cut −3 by tier B with B (1250 Hz, MODERATE, born 0.45 s later)
inside the bell watch; the detector files `held` on B with `emitted: False`; with `a8eb69e` B is engaged 0.5 s after the
verdict; against `7444c16`'s `tier_b_eligible` it never is. Two things the test surfaced:

* B's engagement writes the **1 kHz band to −6**, not 1.25 kHz to −3: `NotchController.propose` deepens the nearest notch
  within `merge_adjacent_bands` (1). For K8's hop (+200 cents = 1/6 octave, i.e. between GEQ centres) the product would
  therefore deepen the old notch, whose bell at 1/6 octave delivers roughly half its depth at the hopped line. C2 makes the
  policy act; the GEQ's geometry limits what the action achieves. The PEQ actuator (merge 0.08 oct) is the real fix, as the
  design says.
* Two family-less notes 0.2 octave apart cannot both be NARROW at this resolution (the test had to move B to 0.3 octave):
  the same limitation affects a real hop while the old mode is still ringing down.

## 4. Pending desk work today (needs silence: Jim is in a meeting)

`scripts/desk_checks.py` (to be committed with its results) then `scripts/measure_rta_bands.py --det PEAK --decay 0.25`
(your fixed version, PR #16): det read-back and the det-switch transient; the frozen-line check on a steady tone at 4
decimals; 40 / 63 / 100 Hz rise under PEAK and RMS; the semitone sweep under verified prefs. The GEQ both-legs test and the
bus tap-order check need Jim's hands and a channel-fed tone; they are queued for when he is free.
