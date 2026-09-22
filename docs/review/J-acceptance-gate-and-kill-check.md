# Appendix J — The pre-registered acceptance gate and the kill check, run against old and new

`scripts/accept_discriminator.py` was written by an independent reviewer before any candidate detector existed (branch
`review/acceptance-gate`) and is reproduced here verbatim as run on 2026-09-22 against (1) the detector shipped at `a408a2a` and
(2) the final detector on `review/detector-cfs` (after the kill-check fix), both over the 67-scenario corpus that includes the
reviewer's K series (`review/kill-check`). Discussion: REVIEW_REPORT.md §1.6. The gate stays in the tree; re-run it with
`PYTHONPATH=src:tests python scripts/accept_discriminator.py` (about 12 minutes).

## J1. New detector

```
acceptance gate - candidate: NEW FeedbackDetector on review/detector-cfs (built blind as FeedbackDetector(cfg, band_hz); the script's fixed 'shipped' label replaced here)
  corpus 67 scenarios (38 with feedback), hold-out seeds (4, 5, 6, 7, 8, 9), 7 analyser sweeps, EARLY_CREDIT_S default 2.0

  [FAIL] G1 hold-out seeds, open loop: 48/67 scenarios, TP 255/270, miss 15, FP 0 (EARLY 10, TAIL 0, HARM 0)  [82 s]
         S3_ring_during_music                       FP   0  miss  0  lat max 501 > 300 ms  (1/6 seeds)
         S10_ring_between_bands                     FP   0  miss  0  lat max 401 > 300 ms  (4/6 seeds)
         S11a_two_rings                             FP   0  miss  0  lat max 598 > 300 ms  (3/6 seeds)
         S11b_two_rings_near_octave                 FP   0  miss  0  lat max 549 > 300 ms  (5/6 seeds)
         S13_slow_ring_3dB_s                        FP   0  miss  0  lat max 3599 > 1000 ms  (3/6 seeds)
         S22_speech_ringing_then_feedback           FP   0  miss  0  lat max 402 > 300 ms  (5/6 seeds)
         M3_jazz_trio_lav_ring_400Hz                FP   0  miss  0  lat max 351 > 300 ms  (4/6 seeds)
         X7_plateaued_ring_under_music_from_t0      FP   0  miss  0  lat max 1497 > 1000 ms  (5/6 seeds)
         X8_slow_ring_midband_under_chords          FP   0  miss  0  lat max 4700 > 1000 ms  (0/6 seeds)
         X9_ring_rta_midpoint_525Hz_speech          FP   0  miss  0  lat max 1052 > 300 ms  (0/6 seeds)
         X11_amp_clipped_howl_minus12dBFS           FP   0  miss  1  lat max 1252 > 300 ms  (2/6 seeds)
         X14_peakhold_loud_band_wedge_ring          FP   0  miss  0  lat max 302 > 300 ms  (5/6 seeds)
         ... and 7 more
  [FAIL] G2 attack_k=0.32 (faster LF attack): 47/67 scenarios, TP 255/270, miss 15, FP 1 (EARLY 10, TAIL 0, HARM 0)  [82 s]
         S3_ring_during_music                       FP   0  miss  0  lat max 501 > 300 ms  (1/6 seeds)
         S10_ring_between_bands                     FP   0  miss  0  lat max 401 > 300 ms  (4/6 seeds)
         S11a_two_rings                             FP   0  miss  0  lat max 599 > 300 ms  (3/6 seeds)
         S11b_two_rings_near_octave                 FP   0  miss  0  lat max 549 > 300 ms  (4/6 seeds)
         S13_slow_ring_3dB_s                        FP   0  miss  0  lat max 3599 > 1000 ms  (3/6 seeds)
         S22_speech_ringing_then_feedback           FP   0  miss  0  lat max 402 > 300 ms  (5/6 seeds)
         M3_jazz_trio_lav_ring_400Hz                FP   0  miss  0  lat max 351 > 300 ms  (3/6 seeds)
         X7_plateaued_ring_under_music_from_t0      FP   0  miss  0  lat max 1497 > 1000 ms  (5/6 seeds)
         X8_slow_ring_midband_under_chords          FP   0  miss  0  lat max 4700 > 1000 ms  (0/6 seeds)
         X9_ring_rta_midpoint_525Hz_speech          FP   0  miss  0  lat max 1452 > 300 ms  (0/6 seeds)
         X11_amp_clipped_howl_minus12dBFS           FP   0  miss  1  lat max 1252 > 300 ms  (2/6 seeds)
         X14_peakhold_loud_band_wedge_ring          FP   0  miss  0  lat max 302 > 300 ms  (5/6 seeds)
         ... and 8 more
  [FAIL] G2 attack_k=1.0 (slower LF attack): 47/67 scenarios, TP 254/270, miss 16, FP 0 (EARLY 9, TAIL 0, HARM 0)  [84 s]
         S3_ring_during_music                       FP   0  miss  0  lat max 501 > 300 ms  (1/6 seeds)
         S10_ring_between_bands                     FP   0  miss  0  lat max 401 > 300 ms  (4/6 seeds)
         S11a_two_rings                             FP   0  miss  0  lat max 598 > 300 ms  (3/6 seeds)
         S11b_two_rings_near_octave                 FP   0  miss  0  lat max 549 > 300 ms  (4/6 seeds)
         S13_slow_ring_3dB_s                        FP   0  miss  0  lat max 3599 > 1000 ms  (3/6 seeds)
         S22_speech_ringing_then_feedback           FP   0  miss  0  lat max 402 > 300 ms  (5/6 seeds)
         M3_jazz_trio_lav_ring_400Hz                FP   0  miss  0  lat max 302 > 300 ms  (3/6 seeds)
         X7_plateaued_ring_under_music_from_t0      FP   0  miss  0  lat max 1497 > 1000 ms  (5/6 seeds)
         X8_slow_ring_midband_under_chords          FP   0  miss  0  lat max 4650 > 1000 ms  (0/6 seeds)
         X9_ring_rta_midpoint_525Hz_speech          FP   0  miss  0  lat max 1152 > 300 ms  (0/6 seeds)
         X11_amp_clipped_howl_minus12dBFS           FP   0  miss  1  lat max 1252 > 300 ms  (2/6 seeds)
         X14_peakhold_loud_band_wedge_ring          FP   0  miss  0  lat max 302 > 300 ms  (5/6 seeds)
         ... and 8 more
  [FAIL] G2 skirt_order=2 (shallow skirts, 24 dB prominence ceiling): 46/67 scenarios, TP 250/270, miss 20, FP 0 (EARLY 9, TAIL 0, HARM 0)  [79 s]
         S3_ring_during_music                       FP   0  miss  0  lat max 550 > 300 ms  (1/6 seeds)
         S10_ring_between_bands                     FP   0  miss  0  lat max 351 > 300 ms  (4/6 seeds)
         S11a_two_rings                             FP   0  miss  0  lat max 598 > 300 ms  (3/6 seeds)
         S11b_two_rings_near_octave                 FP   0  miss  0  lat max 549 > 300 ms  (3/6 seeds)
         S13_slow_ring_3dB_s                        FP   0  miss  0  lat max 1501 > 1000 ms  (2/6 seeds)
         S15_long_rta_decay_tails                   FP   0  miss  0  lat max 302 > 300 ms  (5/6 seeds)
         S22_speech_ringing_then_feedback           FP   0  miss  0  lat max 402 > 300 ms  (5/6 seeds)
         M3_jazz_trio_lav_ring_400Hz                FP   0  miss  0  lat max 351 > 300 ms  (4/6 seeds)
         X7_plateaued_ring_under_music_from_t0      FP   0  miss  0  lat max 1497 > 1000 ms  (5/6 seeds)
         X8_slow_ring_midband_under_chords          FP   0  miss  0  lat max 4650 > 1000 ms  (0/6 seeds)
         X9_ring_rta_midpoint_525Hz_speech          FP   0  miss  0  lat max 951 > 300 ms  (0/6 seeds)
         X11_amp_clipped_howl_minus12dBFS           FP   0  miss  1  lat max 1252 > 300 ms  (2/6 seeds)
         ... and 9 more
  [FAIL] G2 skirt_order=5 (steep skirts, 60 dB ceiling): 47/67 scenarios, TP 256/270, miss 14, FP 0 (EARLY 10, TAIL 0, HARM 0)  [83 s]
         S3_ring_during_music                       FP   0  miss  0  lat max 501 > 300 ms  (1/6 seeds)
         S6b_ringout_steps_latent_loop              FP   0  miss  0  lat max 301 > 300 ms  (5/6 seeds)
         S10_ring_between_bands                     FP   0  miss  0  lat max 401 > 300 ms  (4/6 seeds)
         S11a_two_rings                             FP   0  miss  0  lat max 598 > 300 ms  (3/6 seeds)
         S11b_two_rings_near_octave                 FP   0  miss  0  lat max 549 > 300 ms  (5/6 seeds)
         S12_acoustic_guitar_wedge_ring_196Hz       FP   0  miss  0  lat max 603 > 600 ms  (4/6 seeds)
         S13_slow_ring_3dB_s                        FP   0  miss  0  lat max 3599 > 1000 ms  (2/6 seeds)
         S22_speech_ringing_then_feedback           FP   0  miss  0  lat max 402 > 300 ms  (5/6 seeds)
         M3_jazz_trio_lav_ring_400Hz                FP   0  miss  0  lat max 401 > 300 ms  (4/6 seeds)
         X7_plateaued_ring_under_music_from_t0      FP   0  miss  0  lat max 1497 > 1000 ms  (5/6 seeds)
         X8_slow_ring_midband_under_chords          FP   0  miss  0  lat max 4750 > 1000 ms  (0/6 seeds)
         X9_ring_rta_midpoint_525Hz_speech          FP   0  miss  0  lat max 1052 > 300 ms  (0/6 seeds)
         ... and 8 more
  [FAIL] G2 decay_s=16, release 240 dB (slow display release): 50/67 scenarios, TP 245/270, miss 25, FP 0  [77 s]
         S3_ring_during_music                       FP   0  miss  0  lat max 700 > 300 ms  (1/6 seeds)
         S10_ring_between_bands                     FP   0  miss  0  lat max 452 > 300 ms  (3/6 seeds)
         S11a_two_rings                             FP   0  miss  0  lat max 699 > 300 ms  (1/6 seeds)
         S11b_two_rings_near_octave                 FP   0  miss  0  lat max 498 > 300 ms  (1/6 seeds)
         S13_slow_ring_3dB_s                        FP   0  miss  6    (0/6 seeds)
         M3_jazz_trio_lav_ring_400Hz                FP   0  miss  0  lat max 503 > 300 ms  (0/6 seeds)
         X7_plateaued_ring_under_music_from_t0      FP   0  miss  0  lat max 1497 > 1000 ms  (5/6 seeds)
         X8_slow_ring_midband_under_chords          FP   0  miss  0  lat max 3600 > 1000 ms  (0/6 seeds)
         X9_ring_rta_midpoint_525Hz_speech          FP   0  miss  0  lat max 853 > 300 ms  (0/6 seeds)
         X11_amp_clipped_howl_minus12dBFS           FP   0  miss  1  lat max 1353 > 300 ms  (2/6 seeds)
         X14_peakhold_loud_band_wedge_ring          FP   0  miss  0  lat max 302 > 300 ms  (5/6 seeds)
         X17_ring_122Hz_acoustic_guitar_body        FP   0  miss  6    (0/6 seeds)
         ... and 5 more
  [FAIL] G2 peak_hold_s=2: 39/67 scenarios, TP 220/270, miss 50, FP 0  [77 s]
         S2a_established_ring_8k                    FP   0  miss  2  lat max 6098 > 300 ms  (1/6 seeds)
         S2b_established_ring_8k_steep              FP   0  miss  3  lat max 2500 > 300 ms  (0/6 seeds)
         S6b_ringout_steps_latent_loop              FP   0  miss  0  lat max 501 > 300 ms  (3/6 seeds)
         S10_ring_between_bands                     FP   0  miss  0  lat max 351 > 300 ms  (3/6 seeds)
         S11a_two_rings                             FP   0  miss  0  lat max 453 > 300 ms  (4/6 seeds)
         S11b_two_rings_near_octave                 FP   0  miss  0  lat max 450 > 300 ms  (5/6 seeds)
         S12_acoustic_guitar_wedge_ring_196Hz       FP   0  miss  0  lat max 651 > 600 ms  (4/6 seeds)
         S13_slow_ring_3dB_s                        FP   0  miss  6    (0/6 seeds)
         M2_quiet_music_ringout_two_modes           FP   0  miss  0  lat max 550 > 300 ms  (0/6 seeds)
         M3_jazz_trio_lav_ring_400Hz                FP   0  miss  0  lat max 302 > 300 ms  (5/6 seeds)
         X7_plateaued_ring_under_music_from_t0      FP   0  miss  5  lat max 1050 > 1000 ms  (0/6 seeds)
         X8_slow_ring_midband_under_chords          FP   0  miss  0  lat max 4602 > 1000 ms  (0/6 seeds)
         ... and 16 more
  [FAIL] G2 det=RMS: 48/67 scenarios, TP 251/270, miss 19, FP 1 (EARLY 20, TAIL 0, HARM 0)  [84 s]
         S3_ring_during_music                       FP   0  miss  0  lat max 501 > 300 ms  (1/6 seeds)
         S6b_ringout_steps_latent_loop              FP   0  miss  0  lat max 301 > 300 ms  (5/6 seeds)
         S10_ring_between_bands                     FP   0  miss  0  lat max 551 > 300 ms  (2/6 seeds)
         S11b_two_rings_near_octave                 FP   0  miss  0  lat max 399 > 300 ms  (4/6 seeds)
         S13_slow_ring_3dB_s                        FP   0  miss  0  lat max 1400 > 1000 ms  (4/6 seeds)
         S22_speech_ringing_then_feedback           FP   0  miss  0  lat max 649 > 300 ms  (4/6 seeds)
         M3_jazz_trio_lav_ring_400Hz                FP   0  miss  0  lat max 500 > 300 ms  (2/6 seeds)
         X7_plateaued_ring_under_music_from_t0      FP   0  miss  0  lat max 1497 > 1000 ms  (5/6 seeds)
         X8_slow_ring_midband_under_chords          FP   0  miss  0  lat max 4649 > 1000 ms  (0/6 seeds)
         X9_ring_rta_midpoint_525Hz_speech          FP   0  miss  0  lat max 1052 > 300 ms  (0/6 seeds)
         X11_amp_clipped_howl_minus12dBFS           FP   0  miss  3  lat max 401 > 300 ms  (1/6 seeds)
         X17_ring_122Hz_acoustic_guitar_body        FP   1  miss  6    (0/6 seeds)
         ... and 7 more
  [FAIL] G3 EARLY_CREDIT_S = 0: 18/38 scenarios, TP 255/270, miss 15, FP 0 (EARLY 10, TAIL 0, HARM 0)  [22 s]
         S3_ring_during_music                       FP   0  miss  0  lat max 501 > 300 ms  (1/6 seeds)
         S10_ring_between_bands                     FP   0  miss  0  lat max 401 > 300 ms  (4/6 seeds)
         S11a_two_rings                             FP   0  miss  0  lat max 598 > 300 ms  (3/6 seeds)
         S11b_two_rings_near_octave                 FP   0  miss  0  lat max 549 > 300 ms  (5/6 seeds)
         S13_slow_ring_3dB_s                        FP   0  miss  0  lat max 3599 > 1000 ms  (3/6 seeds)
         S22_speech_ringing_then_feedback           FP   0  miss  0  lat max 402 > 300 ms  (5/6 seeds)
         M3_jazz_trio_lav_ring_400Hz                FP   0  miss  0  lat max 351 > 300 ms  (4/6 seeds)
         X7_plateaued_ring_under_music_from_t0      FP   0  miss  0  lat max 1497 > 1000 ms  (5/6 seeds)
         X8_slow_ring_midband_under_chords          FP   0  miss  0  lat max 4700 > 1000 ms  (0/6 seeds)
         X9_ring_rta_midpoint_525Hz_speech          FP   0  miss  0  lat max 1052 > 300 ms  (0/6 seeds)
         X11_amp_clipped_howl_minus12dBFS           FP   0  miss  1  lat max 1252 > 300 ms  (2/6 seeds)
         X14_peakhold_loud_band_wedge_ring          FP   0  miss  0  lat max 302 > 300 ms  (5/6 seeds)
         ... and 8 more
  [FAIL] G4 closed loop (real NotchController): 18/38 scenarios, TP 251/267, miss 16, FP 0 (EARLY 8, TAIL 1, HARM 0)  [58 s]
         S3_ring_during_music                       FP   0  miss  0  lat max 501 > 300 ms  (1/6 seeds)
         S10_ring_between_bands                     FP   0  miss  0  lat max 401 > 300 ms  (4/6 seeds)
         S11a_two_rings                             FP   0  miss  0  lat max 1252 > 300 ms  (2/6 seeds)
         S11b_two_rings_near_octave                 FP   0  miss  0  lat max 798 > 300 ms  (3/6 seeds)
         S13_slow_ring_3dB_s                        FP   0  miss  0  lat max 3599 > 1000 ms  (3/6 seeds)
         S22_speech_ringing_then_feedback           FP   0  miss  0  lat max 402 > 300 ms  (5/6 seeds)
         M2_quiet_music_ringout_two_modes           FP   0  miss  1  lat max 1198 > 300 ms  (1/6 seeds)
         M3_jazz_trio_lav_ring_400Hz                FP   0  miss  0  lat max 351 > 300 ms  (4/6 seeds)
         X7_plateaued_ring_under_music_from_t0      FP   0  miss  0  lat max 1497 > 1000 ms  (5/6 seeds)
         X8_slow_ring_midband_under_chords          FP   0  miss  0  lat max 4700 > 1000 ms  (0/6 seeds)
         X9_ring_rta_midpoint_525Hz_speech          FP   0  miss  0  lat max 1052 > 300 ms  (0/6 seeds)
         X11_amp_clipped_howl_minus12dBFS           FP   0  miss  1  lat max 1252 > 300 ms  (2/6 seeds)
         ... and 8 more

  [INFO] R1 irreducible pairs (G1): {'X4_sine_lead_portamento': 'PASS', 'X7_plateaued_ring_under_music_from_t0': 'FAIL', 'X20_mains_hum_and_hvac_whine': 'PASS', 'S2a_established_ring_8k': 'PASS'}
  [INFO] R2 price (G1): FP would cut GEQ bands none; EARLY 10, TAIL 0, HARM 0

VERDICT: REJECT  (0/10 gates)
new exit=1
```

## J2. Shipped detector

```
acceptance gate - candidate: shipped FeedbackDetector (device.yaml)
  corpus 67 scenarios (38 with feedback), hold-out seeds (4, 5, 6, 7, 8, 9), 7 analyser sweeps, EARLY_CREDIT_S default 2.0

  [FAIL] G1 hold-out seeds, open loop: 8/67 scenarios, TP 251/270, miss 19, FP 2300 (EARLY 19, TAIL 40, HARM 286)  [53 s]
         S1_bass_under_quiet_music                  FP  65  miss  0    (0/6 seeds)
         S3_ring_during_music                       FP  18  miss  0  lat max 650 > 300 ms  (0/6 seeds)
         S4a_vocal_vibrato                          FP  75  miss  0    (0/6 seeds)
         S4b_vocal_vibrato_band_edge                FP  68  miss  0    (0/6 seeds)
         S5_guitar_note_decays_to_sine              FP  26  miss  0    (0/6 seeds)
         S6_master_ramp_feedback_watch              FP  10  miss  0    (0/6 seeds)
         S6b_ringout_steps_latent_loop              FP  10  miss  0    (1/6 seeds)
         S7_808_sub_bassline                        FP  44  miss  0    (0/6 seeds)
         S8a_organ_melody                           FP  34  miss  0    (0/6 seeds)
         S8b_flute_held_note                        FP  53  miss  0    (0/6 seeds)
         S8c_whistle                                FP  55  miss  0    (0/6 seeds)
         S9_clipped_howl_fast                       FP   0  miss  0  lat max 302 > 300 ms  (5/6 seeds)
         ... and 47 more
  [FAIL] G2 attack_k=0.32 (faster LF attack): 8/67 scenarios, TP 252/270, miss 18, FP 2112 (EARLY 17, TAIL 40, HARM 286)  [53 s]
         S1_bass_under_quiet_music                  FP  43  miss  0    (0/6 seeds)
         S3_ring_during_music                       FP  20  miss  0  lat max 650 > 300 ms  (0/6 seeds)
         S4a_vocal_vibrato                          FP  77  miss  0    (0/6 seeds)
         S4b_vocal_vibrato_band_edge                FP  69  miss  0    (0/6 seeds)
         S5_guitar_note_decays_to_sine              FP  14  miss  0    (0/6 seeds)
         S6_master_ramp_feedback_watch              FP  11  miss  0    (0/6 seeds)
         S6b_ringout_steps_latent_loop              FP   6  miss  0    (3/6 seeds)
         S7_808_sub_bassline                        FP  39  miss  0    (0/6 seeds)
         S8a_organ_melody                           FP  28  miss  0    (0/6 seeds)
         S8b_flute_held_note                        FP  51  miss  0    (0/6 seeds)
         S8c_whistle                                FP  55  miss  0    (0/6 seeds)
         S9_clipped_howl_fast                       FP   0  miss  0  lat max 302 > 300 ms  (5/6 seeds)
         ... and 47 more
  [FAIL] G2 attack_k=1.0 (slower LF attack): 7/67 scenarios, TP 249/270, miss 21, FP 2415 (EARLY 19, TAIL 40, HARM 286)  [54 s]
         S1_bass_under_quiet_music                  FP  58  miss  0    (0/6 seeds)
         S3_ring_during_music                       FP  27  miss  0  lat max 650 > 300 ms  (0/6 seeds)
         S4a_vocal_vibrato                          FP  80  miss  0    (0/6 seeds)
         S4b_vocal_vibrato_band_edge                FP  74  miss  0    (0/6 seeds)
         S5_guitar_note_decays_to_sine              FP  20  miss  0    (0/6 seeds)
         S6_master_ramp_feedback_watch              FP  12  miss  0    (0/6 seeds)
         S6b_ringout_steps_latent_loop              FP   5  miss  0    (4/6 seeds)
         S7_808_sub_bassline                        FP  32  miss  0    (0/6 seeds)
         S8a_organ_melody                           FP  51  miss  0    (0/6 seeds)
         S8b_flute_held_note                        FP  64  miss  0    (0/6 seeds)
         S8c_whistle                                FP  58  miss  0    (0/6 seeds)
         S9_clipped_howl_fast                       FP   0  miss  0  lat max 302 > 300 ms  (5/6 seeds)
         ... and 48 more
  [FAIL] G2 skirt_order=2 (shallow skirts, 24 dB prominence ceiling): 8/67 scenarios, TP 177/270, miss 93, FP 1569 (EARLY 10, TAIL 0, HARM 4)  [54 s]
         S1_bass_under_quiet_music                  FP  39  miss  0    (0/6 seeds)
         S2a_established_ring_8k                    FP   0  miss  6    (0/6 seeds)
         S2b_established_ring_8k_steep              FP   0  miss  6    (0/6 seeds)
         S2c_established_clipped_2k4                FP   0  miss  6    (0/6 seeds)
         S3_ring_during_music                       FP   8  miss  0  lat max 651 > 300 ms  (1/6 seeds)
         S4a_vocal_vibrato                          FP  57  miss  0    (0/6 seeds)
         S4b_vocal_vibrato_band_edge                FP  60  miss  0    (0/6 seeds)
         S5_guitar_note_decays_to_sine              FP  26  miss  0    (0/6 seeds)
         S6_master_ramp_feedback_watch              FP   6  miss  0    (2/6 seeds)
         S6b_ringout_steps_latent_loop              FP  18  miss  0    (1/6 seeds)
         S7_808_sub_bassline                        FP  12  miss  0    (0/6 seeds)
         S8a_organ_melody                           FP  19  miss  0    (0/6 seeds)
         ... and 47 more
  [FAIL] G2 skirt_order=5 (steep skirts, 60 dB ceiling): 8/67 scenarios, TP 255/270, miss 15, FP 2766 (EARLY 24, TAIL 40, HARM 289)  [55 s]
         S1_bass_under_quiet_music                  FP  76  miss  0    (0/6 seeds)
         S3_ring_during_music                       FP  25  miss  0  lat max 650 > 300 ms  (0/6 seeds)
         S4a_vocal_vibrato                          FP  81  miss  0    (0/6 seeds)
         S4b_vocal_vibrato_band_edge                FP  90  miss  0    (0/6 seeds)
         S5_guitar_note_decays_to_sine              FP  26  miss  0    (0/6 seeds)
         S6_master_ramp_feedback_watch              FP  47  miss  0    (0/6 seeds)
         S6b_ringout_steps_latent_loop              FP   7  miss  0    (3/6 seeds)
         S7_808_sub_bassline                        FP  42  miss  0    (0/6 seeds)
         S8a_organ_melody                           FP  50  miss  0    (0/6 seeds)
         S8b_flute_held_note                        FP  61  miss  0    (0/6 seeds)
         S8c_whistle                                FP  58  miss  0    (0/6 seeds)
         S9_clipped_howl_fast                       FP   0  miss  0  lat max 302 > 300 ms  (5/6 seeds)
         ... and 47 more
  [FAIL] G2 decay_s=16, release 240 dB (slow display release): 8/67 scenarios, TP 247/270, miss 23, FP 1298 (EARLY 22, TAIL 8, HARM 285)  [54 s]
         S1_bass_under_quiet_music                  FP  27  miss  0    (0/6 seeds)
         S3_ring_during_music                       FP  13  miss  0  lat max 601 > 300 ms  (0/6 seeds)
         S4a_vocal_vibrato                          FP  35  miss  0    (0/6 seeds)
         S4b_vocal_vibrato_band_edge                FP  14  miss  0    (0/6 seeds)
         S5_guitar_note_decays_to_sine              FP  22  miss  0    (0/6 seeds)
         S6_master_ramp_feedback_watch              FP   9  miss  0    (0/6 seeds)
         S6b_ringout_steps_latent_loop              FP   4  miss  0    (4/6 seeds)
         S7_808_sub_bassline                        FP  24  miss  0    (0/6 seeds)
         S8a_organ_melody                           FP  27  miss  0    (0/6 seeds)
         S8b_flute_held_note                        FP  17  miss  0    (0/6 seeds)
         S8c_whistle                                FP  40  miss  0    (0/6 seeds)
         S9_clipped_howl_fast                       FP   0  miss  0  lat max 302 > 300 ms  (5/6 seeds)
         ... and 47 more
  [FAIL] G2 peak_hold_s=2: 18/67 scenarios, TP 239/270, miss 31, FP 657 (EARLY 12, TAIL 40, HARM 271)  [54 s]
         S3_ring_during_music                       FP   1  miss  0    (5/6 seeds)
         S4a_vocal_vibrato                          FP  37  miss  0    (0/6 seeds)
         S4b_vocal_vibrato_band_edge                FP  11  miss  0    (0/6 seeds)
         S5_guitar_note_decays_to_sine              FP  17  miss  0    (0/6 seeds)
         S6_master_ramp_feedback_watch              FP  15  miss  0    (0/6 seeds)
         S7_808_sub_bassline                        FP  10  miss  0    (0/6 seeds)
         S8a_organ_melody                           FP  22  miss  0    (0/6 seeds)
         S8b_flute_held_note                        FP  10  miss  0    (0/6 seeds)
         S8c_whistle                                FP  30  miss  0    (0/6 seeds)
         S10_ring_between_bands                     FP   0  miss  0  lat max 449 > 300 ms  (1/6 seeds)
         S11a_two_rings                             FP   0  miss  0  lat max 348 > 300 ms  (5/6 seeds)
         S13_slow_ring_3dB_s                        FP   0  miss  6    (0/6 seeds)
         ... and 37 more
  [FAIL] G2 det=RMS: 5/67 scenarios, TP 262/270, miss 8, FP 5087 (EARLY 17, TAIL 40, HARM 295)  [53 s]
         C1_music_bed_drums                         FP   2  miss  0    (4/6 seeds)
         S1_bass_under_quiet_music                  FP 284  miss  0    (0/6 seeds)
         S3_ring_during_music                       FP  40  miss  0  lat max 597 > 300 ms  (0/6 seeds)
         S4a_vocal_vibrato                          FP  81  miss  0    (0/6 seeds)
         S4b_vocal_vibrato_band_edge                FP  82  miss  0    (0/6 seeds)
         S5_guitar_note_decays_to_sine              FP  40  miss  0    (0/6 seeds)
         S6_master_ramp_feedback_watch              FP 184  miss  0    (0/6 seeds)
         S6b_ringout_steps_latent_loop              FP 162  miss  0    (0/6 seeds)
         S7_808_sub_bassline                        FP  51  miss  0    (0/6 seeds)
         S8a_organ_melody                           FP  80  miss  0    (0/6 seeds)
         S8b_flute_held_note                        FP  88  miss  0    (0/6 seeds)
         S8c_whistle                                FP  59  miss  0    (0/6 seeds)
         ... and 50 more
  [FAIL] G3 EARLY_CREDIT_S = 0: 5/38 scenarios, TP 251/270, miss 19, FP 866 (EARLY 19, TAIL 40, HARM 286)  [3 s]
         S3_ring_during_music                       FP  18  miss  0  lat max 650 > 300 ms  (0/6 seeds)
         S6b_ringout_steps_latent_loop              FP  10  miss  0    (1/6 seeds)
         S9_clipped_howl_fast                       FP   0  miss  0  lat max 302 > 300 ms  (5/6 seeds)
         S10_ring_between_bands                     FP   0  miss  0  lat max 602 > 300 ms  (0/6 seeds)
         S11a_two_rings                             FP   0  miss  0  lat max 652 > 300 ms  (1/6 seeds)
         S11b_two_rings_near_octave                 FP   0  miss  0  lat max 650 > 300 ms  (2/6 seeds)
         S12_acoustic_guitar_wedge_ring_196Hz       FP   7  miss  0    (1/6 seeds)
         S13_slow_ring_3dB_s                        FP   1  miss  6    (0/6 seeds)
         S15_long_rta_decay_tails                   FP   5  miss  0  lat max 303 > 300 ms  (3/6 seeds)
         S22_speech_ringing_then_feedback           FP 194  miss  0  lat max 349 > 300 ms  (0/6 seeds)
         M1_loud_band_wedge_ring                    FP   2  miss  0    (4/6 seeds)
         M2_quiet_music_ringout_two_modes           FP  29  miss  0    (0/6 seeds)
         ... and 21 more
  [FAIL] G4 closed loop (real NotchController): 6/38 scenarios, TP 222/240, miss 18, FP 801 (EARLY 34, TAIL 0, HARM 24)  [41 s]
         S3_ring_during_music                       FP  19  miss  0  lat max 650 > 300 ms  (0/6 seeds)
         S6b_ringout_steps_latent_loop              FP   9  miss  0    (1/6 seeds)
         S9_clipped_howl_fast                       FP   2  miss  0  lat max 302 > 300 ms  (4/6 seeds)
         S10_ring_between_bands                     FP   0  miss  0  lat max 602 > 300 ms  (0/6 seeds)
         S11a_two_rings                             FP   0  miss  0  lat max 1651 > 300 ms  (1/6 seeds)
         S11b_two_rings_near_octave                 FP   0  miss  0  lat max 1800 > 300 ms  (1/6 seeds)
         S12_acoustic_guitar_wedge_ring_196Hz       FP   6  miss  0    (1/6 seeds)
         S13_slow_ring_3dB_s                        FP   1  miss  6    (0/6 seeds)
         S15_long_rta_decay_tails                   FP   5  miss  0  lat max 303 > 300 ms  (3/6 seeds)
         S22_speech_ringing_then_feedback           FP 140  miss  0  lat max 403 > 300 ms  (0/6 seeds)
         M1_loud_band_wedge_ring                    FP   3  miss  0  lat max 302 > 300 ms  (4/6 seeds)
         M2_quiet_music_ringout_two_modes           FP  24  miss  0  lat max 1453 > 300 ms  (0/6 seeds)
         ... and 20 more

  [INFO] R1 irreducible pairs (G1): {'X4_sine_lead_portamento': 'FAIL', 'X7_plateaued_ring_under_music_from_t0': 'FAIL', 'X20_mains_hum_and_hvac_whine': 'FAIL', 'S2a_established_ring_8k': 'PASS'}
  [INFO] R2 price (G1): FP would cut GEQ bands [1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 25, 26]; EARLY 19, TAIL 40, HARM 286

VERDICT: REJECT  (0/10 gates)
shipped exit=1
```

## J3. Kill check (K series), closed loop, strict `survived` scoring, new detector

```
scenario                                ev  TP miss   FP erly tail harm cuts surv  lat ms min/med/max  bud   verdict
K1_limiter_held_howl_e4_2k5              3   3    0    0    0    0    0    6    0         151/152/198  300   PASS (3/3)
K2_limiter_held_howl_e7_1k25             3   3    0    0    0    0    0    9    0          98/101/250  300   PASS (3/3)
K3_established_limiter_plateau_e5_5k     3   3    0    0    0    0    0    6    0         199/200/202  300   PASS (3/3)
K4_compressor_plateau_e6p5_quiet_2k5     3   3    0    0    0    0    0    9    0         197/200/249  300   PASS (3/3)
K5_channel_shove_into_limiter_e5p5_2k5   3   3    0    0    0    0    0    6    0          48/100/200  300   PASS (3/3)
K6_limiter_held_howl_e3p5_midpoint_1k8   3   3    0    0    0    0    0    6    0          49/148/148  300   PASS (3/3)
6 scenarios, 6 pass; events 18, TP 18, miss 0, FP 0, survived 0        (identical in ring_out mode)
```
Before the fix K4 read: TP 3, cuts 3, survived 3 — one −3 dB cut, verdict `held`, no deepen right below the loud-ish line.
The shipped detector also kills all six (blind persistence re-emission) with 42 programme cuts on the same scenes.
