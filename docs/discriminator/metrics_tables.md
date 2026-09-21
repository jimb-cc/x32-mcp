# disc-sequential-evidence — corpus metrics

generated 2026-09-21 13:59:42; seeds [1, 2, 3]; config from device.yaml + defaults (emit 3.0/1.4 nats watch/ring-out, dismiss -2.5/-3.5, window 160.0/100.0/40.0–12500.0 Hz, track ≥6.0 dB, emit ≥12.0 dB, min_level -45.0)

### scenario's own mode (watch / ring-out as cfs would arm it; probe + note_cut in ring-out), open loop

| scenario | ev | TP | miss | FP | early | tail | harm | dup | cuts | lat ms min/med/max | budget | FP at GEQ (Hz) | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C0_silent_room | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| C1_music_bed_drums | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S1_bass_under_quiet_music | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S2a_established_ring_8k | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 21 | 0 | 48/48/52 | 300 |  | PASS 3/3 |
| S2b_established_ring_8k_steep | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 21 | 0 | 48/48/52 | 300 |  | PASS 3/3 |
| S2c_established_clipped_2k4 | 3 | 3 | 0 | 0 | 1 | 0 | 0 | 20 | 0 | -2/1/1 | 300 |  | PASS 3/3 |
| S3_ring_during_music | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 18 | 0 | 53/502/602 | 300 |  | FAIL 1/3 |
| S4a_vocal_vibrato | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S4b_vocal_vibrato_band_edge | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S5_guitar_note_decays_to_sine | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S6_master_ramp_feedback_watch | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S6b_ringout_steps_latent_loop | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 9 | 0 | 51/52/300 | 300 |  | PASS 3/3 |
| S7_808_sub_bassline | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S8a_organ_melody | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S8b_flute_held_note | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S8c_whistle | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S9_clipped_howl_fast | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 9 | 0 | 99/102/103 | 300 |  | PASS 3/3 |
| S10_ring_between_bands | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 18 | 0 | 148/200/200 | 300 |  | PASS 3/3 |
| S11a_two_rings | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 30 | 0 | 1/77/351 | 300 |  | FAIL 2/3 |
| S11b_two_rings_near_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 30 | 0 | -2/100/152 | 300 |  | PASS 3/3 |
| S12_acoustic_guitar_wedge_ring_196Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 21 | 0 | 398/500/551 | 600 |  | PASS 3/3 |
| S13_slow_ring_3dB_s | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 0 | 0/549/1198 | 1000 |  | FAIL 2/3 |
| S14_ring_masked_by_cymbal | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 14 | 0 | 150/198/200 | 300 |  | PASS 3/3 |
| S15_long_rta_decay_tails | 3 | 3 | 0 | 0 | 0 | 8 | 0 | 14 | 0 | 50/52/102 | 300 |  | PASS 3/3 |
| S16_peak_hold_on | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S17_kick_pattern | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S18_vibrato_on_band_edge | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S19_driven_room_mode | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S20_song_start_stop_crowd | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S21_synth_pad_swell | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S22_speech_ringing_then_feedback | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 9 | 0 | 2/102/202 | 300 |  | PASS 3/3 |
| S23a_autogain_drift | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S23b_gain_offset_clip | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S24_bells_triangle_glock | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| M1_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 18 | 0 | 49/52/102 | 300 |  | PASS 3/3 |
| M2_quiet_music_ringout_two_modes | 6 | 6 | 0 | 0 | 1 | 0 | 0 | 47 | 0 | -1100/51/350 | 300 |  | FAIL 2/3 |
| M3_jazz_trio_lav_ring_400Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 24 | 0 | 101/350/602 | 300 |  | FAIL 1/3 |
| X1_organ_held_notes | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X2_flute_held_vibrato | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X3_whistle_held_drift | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X4_sine_lead_portamento | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X5_808_bassline_40_60Hz | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X6_soprano_closed_vowel_band_edge | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X7_plateaued_ring_under_music_from_t0 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 14 | 0 | 1603/5450/6452 | 1000 |  | FAIL 0/3 |
| X8_slow_ring_midband_under_chords | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 28 | 0 | 103/801/2750 | 1000 |  | FAIL 2/3 |
| X9_ring_rta_midpoint_525Hz_speech | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 14 | 0 | 1200/1299/1898 | 300 |  | FAIL 0/3 |
| X10_two_rings_exact_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 36 | 0 | 99/125/148 | 300 |  | PASS 3/3 |
| X11_amp_clipped_howl_minus12dBFS | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 15 | 0 | 351/399/399 | 300 |  | FAIL 0/3 |
| X12a_master_drop20_raise_channel | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 15 | 0 | 98/126/201 | 300 |  | PASS 3/3 |
| X12b_master_drop20_raise_busmaster | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 15 | 0 | 100/125/151 | 300 |  | PASS 3/3 |
| X13_decay16_jazz_lav_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 24 | 0 | 1/147/150 | 600 |  | PASS 3/3 |
| X14_peakhold_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 18 | 0 | 49/52/98 | 300 |  | PASS 3/3 |
| X15_kick_bass_unison_55Hz | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X16_wedge_ring_315Hz_loud_band | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 21 | 0 | 50/203/402 | 300 |  | FAIL 2/3 |
| X17_ring_122Hz_acoustic_guitar_body | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 12 | 0 | 350/549/601 | 600 |  | FAIL 2/3 |
| X18_applause_crowd_30s | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X19_handheld_ring_stalls_and_hops | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 18 | 0 | 51/248/252 | 300 |  | PASS 3/3 |
| X20_mains_hum_and_hvac_whine | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X21_reverberant_area_mic_slow_ring | 3 | 2 | 1 | 0 | 0 | 0 | 0 | 11 | 0 | 298/398/498 | 600 |  | FAIL 2/3 |
| X22_kick_mic_sub_ring_65Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 18 | 0 | 998/1148/1149 | 1000 |  | FAIL 1/3 |
| X23_ringout_quiet_room_two_modes | 6 | 6 | 0 | 0 | 4 | 0 | 0 | 32 | 0 | -1298/-401/150 | 300 |  | PASS 3/3 |
| **61 scenarios, 48 pass** | 117 | 116 | 1 | 0 | 6 | 8 | 0 | 617 | | -1298/148/6452 | | | wall 33.5 s |

### scenario's own mode (watch / ring-out as cfs would arm it; probe + note_cut in ring-out), CLOSED loop (NotchController −3 dB steps, 1-frame actuation)

| scenario | ev | TP | miss | FP | early | tail | harm | dup | cuts | lat ms min/med/max | budget | FP at GEQ (Hz) | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S2a_established_ring_8k | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 48/48/52 | 300 |  | PASS 3/3 |
| S2b_established_ring_8k_steep | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 48/48/52 | 300 |  | PASS 3/3 |
| S2c_established_clipped_2k4 | 3 | 3 | 0 | 0 | 1 | 0 | 0 | 0 | 3 | -2/1/1 | 300 |  | PASS 3/3 |
| S3_ring_during_music | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 53/502/602 | 300 |  | FAIL 1/3 |
| S6b_ringout_steps_latent_loop | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 51/52/300 | 300 |  | PASS 3/3 |
| S9_clipped_howl_fast | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 99/102/103 | 300 |  | PASS 3/3 |
| S10_ring_between_bands | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 148/200/200 | 300 |  | PASS 3/3 |
| S11a_two_rings | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 0/75/397 | 300 |  | FAIL 2/3 |
| S11b_two_rings_near_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 6 | -2/127/398 | 300 |  | FAIL 1/3 |
| S12_acoustic_guitar_wedge_ring_196Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 398/500/551 | 600 |  | PASS 3/3 |
| S13_slow_ring_3dB_s | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0/549/1198 | 1000 |  | FAIL 2/3 |
| S14_ring_masked_by_cymbal | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 150/198/200 | 300 |  | PASS 3/3 |
| S15_long_rta_decay_tails | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 50/52/102 | 300 |  | PASS 3/3 |
| S22_speech_ringing_then_feedback | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 2/102/202 | 300 |  | PASS 3/3 |
| M1_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 49/52/102 | 300 |  | PASS 3/3 |
| M2_quiet_music_ringout_two_modes | 11 | 11 | 0 | 0 | 0 | 0 | 0 | 0 | 11 | -2/2/350 | 300 |  | FAIL 2/3 |
| M3_jazz_trio_lav_ring_400Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 101/350/602 | 300 |  | FAIL 1/3 |
| X7_plateaued_ring_under_music_from_t0 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 1603/5450/6452 | 1000 |  | FAIL 0/3 |
| X8_slow_ring_midband_under_chords | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 103/801/2750 | 1000 |  | FAIL 2/3 |
| X9_ring_rta_midpoint_525Hz_speech | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 1200/1299/1898 | 300 |  | FAIL 0/3 |
| X10_two_rings_exact_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 102/148/150 | 300 |  | PASS 3/3 |
| X11_amp_clipped_howl_minus12dBFS | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 351/399/399 | 300 |  | FAIL 0/3 |
| X12a_master_drop20_raise_channel | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 98/100/151 | 300 |  | PASS 3/3 |
| X12b_master_drop20_raise_busmaster | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 100/150/151 | 300 |  | PASS 3/3 |
| X13_decay16_jazz_lav_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 1/147/150 | 600 |  | PASS 3/3 |
| X14_peakhold_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 6 | 49/52/98 | 300 |  | PASS 3/3 |
| X16_wedge_ring_315Hz_loud_band | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 50/203/402 | 300 |  | FAIL 2/3 |
| X17_ring_122Hz_acoustic_guitar_body | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 350/549/601 | 600 |  | FAIL 2/3 |
| X19_handheld_ring_stalls_and_hops | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 51/248/252 | 300 |  | PASS 3/3 |
| X21_reverberant_area_mic_slow_ring | 3 | 2 | 1 | 0 | 0 | 0 | 0 | 0 | 2 | 298/398/498 | 600 |  | FAIL 2/3 |
| X22_kick_mic_sub_ring_65Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 998/1148/1149 | 1000 |  | FAIL 1/3 |
| X23_ringout_quiet_room_two_modes | 6 | 6 | 0 | 0 | 3 | 0 | 0 | 0 | 9 | -1/75/102 | 300 |  | PASS 3/3 |
| **32 scenarios, 18 pass** | 116 | 115 | 1 | 0 | 4 | 0 | 0 | 3 | | -2/103/6452 | | | wall 21.4 s |

<details><summary>cuts per run</summary>

- S2a_established_ring_8k s1: cuts=[(0.05, 8000, -3.0)] miss=[] lat=[48.5]
- S2a_established_ring_8k s2: cuts=[(0.05, 8000, -3.0)] miss=[] lat=[52.5]
- S2a_established_ring_8k s3: cuts=[(0.05, 8000, -3.0)] miss=[] lat=[47.8]
- S2b_established_ring_8k_steep s1: cuts=[(0.05, 8000, -3.0)] miss=[] lat=[48.5]
- S2b_established_ring_8k_steep s2: cuts=[(0.05, 8000, -3.0)] miss=[] lat=[52.5]
- S2b_established_ring_8k_steep s3: cuts=[(0.05, 8000, -3.0)] miss=[] lat=[47.8]
- S2c_established_clipped_2k4 s1: cuts=[(0.0, 2500, -3.0)] miss=[] lat=[0.7]
- S2c_established_clipped_2k4 s2: cuts=[(-0.0, 2500, -3.0)] miss=[] lat=[-1.8]
- S2c_established_clipped_2k4 s3: cuts=[(0.0, 2500, -3.0)] miss=[] lat=[1.2]
- S3_ring_during_music s1: cuts=[(5.35, 3150, -3.0)] miss=[] lat=[502.3]
- S3_ring_during_music s2: cuts=[(5.55, 3150, -3.0)] miss=[] lat=[601.7]
- S3_ring_during_music s3: cuts=[(4.8, 3150, -3.0)] miss=[] lat=[52.7]
- S6b_ringout_steps_latent_loop s1: cuts=[(12.7, 2500, -3.0)] miss=[] lat=[51.7]
- S6b_ringout_steps_latent_loop s2: cuts=[(12.85, 2500, -3.0)] miss=[] lat=[299.8]
- S6b_ringout_steps_latent_loop s3: cuts=[(12.7, 2500, -3.0)] miss=[] lat=[50.9]
- S9_clipped_howl_fast s1: cuts=[(2.35, 2000, -3.0)] miss=[] lat=[98.7]
- S9_clipped_howl_fast s2: cuts=[(2.35, 2000, -3.0)] miss=[] lat=[102.6]
- S9_clipped_howl_fast s3: cuts=[(2.35, 2000, -3.0)] miss=[] lat=[101.8]
- S10_ring_between_bands s1: cuts=[(1.85, 2500, -3.0)] miss=[] lat=[199.6]
- S10_ring_between_bands s2: cuts=[(1.75, 2500, -3.0)] miss=[] lat=[200.4]
- S10_ring_between_bands s3: cuts=[(1.8, 2500, -3.0)] miss=[] lat=[147.7]
- S11a_two_rings s1: cuts=[(2.5, 3150, -3.0), (2.7, 1250, -3.0)] miss=[] lat=[397.1, 152.5]
- S11a_two_rings s2: cuts=[(2.1, 1250, -3.0), (2.4, 3150, -3.0)] miss=[] lat=[1.2, 0.2]
- S11a_two_rings s3: cuts=[(2.15, 3150, -3.0), (2.5, 1250, -3.0)] miss=[] lat=[148.2, 2.2]
- S11b_two_rings_near_octave s1: cuts=[(1.95, 1250, -3.0), (2.85, 2500, -3.0)] miss=[] lat=[-1.6, 398.3]
- S11b_two_rings_near_octave s2: cuts=[(2.1, 1250, -3.0), (2.2, 2500, -3.0)] miss=[] lat=[101.2, 51.2]
- S11b_two_rings_near_octave s3: cuts=[(2.05, 1250, -3.0), (2.45, 2500, -3.0)] miss=[] lat=[152.3, 352.0]
- S12_acoustic_guitar_wedge_ring_196Hz s1: cuts=[(5.65, 200, -3.0)] miss=[] lat=[398.4]
- S12_acoustic_guitar_wedge_ring_196Hz s2: cuts=[(5.75, 200, -3.0)] miss=[] lat=[500.5]
- S12_acoustic_guitar_wedge_ring_196Hz s3: cuts=[(5.8, 200, -3.0)] miss=[] lat=[550.6]
- S13_slow_ring_3dB_s s1: cuts=[(8.5, 5000, -3.0)] miss=[] lat=[0.3]
- S13_slow_ring_3dB_s s2: cuts=[(9.45, 5000, -3.0)] miss=[] lat=[548.8]
- S13_slow_ring_3dB_s s3: cuts=[(10.4, 5000, -3.0)] miss=[] lat=[1197.8]
- S14_ring_masked_by_cymbal s1: cuts=[(2.9, 4000, -3.0)] miss=[] lat=[197.7]
- S14_ring_masked_by_cymbal s2: cuts=[(3.0, 4000, -3.0)] miss=[] lat=[200.3]
- S14_ring_masked_by_cymbal s3: cuts=[(2.85, 4000, -3.0)] miss=[] lat=[150.4]
- S15_long_rta_decay_tails s1: cuts=[(3.8, 3150, -3.0)] miss=[] lat=[49.5]
- S15_long_rta_decay_tails s2: cuts=[(3.75, 3150, -3.0)] miss=[] lat=[51.5]
- S15_long_rta_decay_tails s3: cuts=[(3.9, 3150, -3.0)] miss=[] lat=[101.5]
- S22_speech_ringing_then_feedback s1: cuts=[(6.55, 3150, -3.0)] miss=[] lat=[2.0]
- S22_speech_ringing_then_feedback s2: cuts=[(6.5, 3150, -3.0)] miss=[] lat=[102.0]
- S22_speech_ringing_then_feedback s3: cuts=[(6.65, 3150, -3.0)] miss=[] lat=[202.1]
- M1_loud_band_wedge_ring s1: cuts=[(5.2, 3150, -3.0)] miss=[] lat=[51.7]
- M1_loud_band_wedge_ring s2: cuts=[(5.25, 3150, -3.0)] miss=[] lat=[102.0]
- M1_loud_band_wedge_ring s3: cuts=[(5.2, 3150, -3.0)] miss=[] lat=[49.3]
- M2_quiet_music_ringout_two_modes s1: cuts=[(5.15, 5000, -3.0), (9.7, 8000, -3.0), (10.35, 5000, -6.0), (15.6, 5000, -9.0)] miss=[] lat=[0.6, 299.4, -2.4, 2.3]
- M2_quiet_music_ringout_two_modes s2: cuts=[(5.5, 5000, -3.0), (9.7, 8000, -3.0), (10.0, 5000, -6.0)] miss=[] lat=[350.0, 47.6, 2.1]
- M2_quiet_music_ringout_two_modes s3: cuts=[(5.2, 5000, -3.0), (9.75, 8000, -3.0), (9.8, 5000, -6.0), (15.4, 8000, -6.0)] miss=[] lat=[-0.7, -0.4, 98.6, -0.1]
- M3_jazz_trio_lav_ring_400Hz s1: cuts=[(5.15, 400, -3.0)] miss=[] lat=[100.6]
- M3_jazz_trio_lav_ring_400Hz s2: cuts=[(5.55, 400, -3.0)] miss=[] lat=[601.7]
- M3_jazz_trio_lav_ring_400Hz s3: cuts=[(5.15, 400, -3.0)] miss=[] lat=[350.0]
- X7_plateaued_ring_under_music_from_t0 s1: cuts=[(1.6, 1600, -3.0)] miss=[] lat=[1602.7]
- X7_plateaued_ring_under_music_from_t0 s2: cuts=[(5.45, 1600, -3.0)] miss=[] lat=[5449.7]
- X7_plateaued_ring_under_music_from_t0 s3: cuts=[(6.45, 1600, -3.0)] miss=[] lat=[6452.2]
- X8_slow_ring_midband_under_chords s1: cuts=[(7.35, 1250, -3.0)] miss=[] lat=[102.8]
- X8_slow_ring_midband_under_chords s2: cuts=[(6.1, 1250, -3.0)] miss=[] lat=[800.7]
- X8_slow_ring_midband_under_chords s3: cuts=[(10.6, 1250, -3.0)] miss=[] lat=[2750.0]
- X9_ring_rta_midpoint_525Hz_speech s1: cuts=[(4.55, 500, -3.0)] miss=[] lat=[1199.7]
- X9_ring_rta_midpoint_525Hz_speech s2: cuts=[(4.6, 500, -3.0)] miss=[] lat=[1299.0]
- X9_ring_rta_midpoint_525Hz_speech s3: cuts=[(5.65, 500, -3.0)] miss=[] lat=[1898.5]
- X10_two_rings_exact_octave s1: cuts=[(2.55, 1600, -3.0), (2.85, 3150, -3.0)] miss=[] lat=[148.0, 148.3]
- X10_two_rings_exact_octave s2: cuts=[(2.5, 1600, -3.0), (2.8, 3150, -3.0)] miss=[] lat=[102.7, 101.8]
- X10_two_rings_exact_octave s3: cuts=[(2.5, 1600, -3.0), (2.85, 3150, -3.0)] miss=[] lat=[148.2, 150.4]
- X11_amp_clipped_howl_minus12dBFS s1: cuts=[(4.6, 800, -3.0)] miss=[] lat=[398.7]
- X11_amp_clipped_howl_minus12dBFS s2: cuts=[(4.6, 800, -3.0)] miss=[] lat=[399.0]
- X11_amp_clipped_howl_minus12dBFS s3: cuts=[(4.55, 800, -3.0)] miss=[] lat=[350.8]
- X12a_master_drop20_raise_channel s1: cuts=[(0.15, 2000, -3.0)] miss=[] lat=[150.7]
- X12a_master_drop20_raise_channel s2: cuts=[(0.1, 2000, -3.0)] miss=[] lat=[97.6]
- X12a_master_drop20_raise_channel s3: cuts=[(0.1, 2000, -3.0)] miss=[] lat=[100.2]
- X12b_master_drop20_raise_busmaster s1: cuts=[(0.15, 2000, -3.0)] miss=[] lat=[150.7]
- X12b_master_drop20_raise_busmaster s2: cuts=[(0.15, 2000, -3.0)] miss=[] lat=[150.0]
- X12b_master_drop20_raise_busmaster s3: cuts=[(0.1, 2000, -3.0)] miss=[] lat=[100.2]
- X13_decay16_jazz_lav_ring s1: cuts=[(5.15, 400, -3.0)] miss=[] lat=[0.6]
- X13_decay16_jazz_lav_ring s2: cuts=[(5.35, 400, -3.0)] miss=[] lat=[147.0]
- X13_decay16_jazz_lav_ring s3: cuts=[(5.15, 400, -3.0)] miss=[] lat=[150.0]
- X14_peakhold_loud_band_wedge_ring s1: cuts=[(5.25, 3150, -3.0), (6.25, 3150, -6.0)] miss=[] lat=[97.5]
- X14_peakhold_loud_band_wedge_ring s2: cuts=[(5.25, 3150, -3.0), (6.3, 3150, -6.0)] miss=[] lat=[52.0]
- X14_peakhold_loud_band_wedge_ring s3: cuts=[(5.2, 3150, -3.0), (6.2, 3150, -6.0)] miss=[] lat=[49.3]
- X16_wedge_ring_315Hz_loud_band s1: cuts=[(6.3, 315, -3.0)] miss=[] lat=[202.7]
- X16_wedge_ring_315Hz_loud_band s2: cuts=[(6.5, 315, -3.0)] miss=[] lat=[402.0]
- X16_wedge_ring_315Hz_loud_band s3: cuts=[(6.15, 315, -3.0)] miss=[] lat=[50.3]
- X17_ring_122Hz_acoustic_guitar_body s1: cuts=[(7.05, 125, -3.0)] miss=[] lat=[350.0]
- X17_ring_122Hz_acoustic_guitar_body s2: cuts=[(7.0, 125, -3.0)] miss=[] lat=[601.3]
- X17_ring_122Hz_acoustic_guitar_body s3: cuts=[(6.95, 125, -3.0)] miss=[] lat=[549.0]
- X19_handheld_ring_stalls_and_hops s1: cuts=[(2.45, 2500, -3.0)] miss=[] lat=[248.5]
- X19_handheld_ring_stalls_and_hops s2: cuts=[(2.7, 2500, -3.0)] miss=[] lat=[252.4]
- X19_handheld_ring_stalls_and_hops s3: cuts=[(2.65, 2500, -3.0)] miss=[] lat=[51.3]
- X21_reverberant_area_mic_slow_ring s1: cuts=[(4.2, 630, -3.0)] miss=[] lat=[298.2]
- X21_reverberant_area_mic_slow_ring s2: cuts=[] miss=[643] lat=[]
- X21_reverberant_area_mic_slow_ring s3: cuts=[(4.7, 630, -3.0)] miss=[] lat=[498.1]
- X22_kick_mic_sub_ring_65Hz s1: cuts=[(7.45, 63, -3.0)] miss=[] lat=[1149.4]
- X22_kick_mic_sub_ring_65Hz s2: cuts=[(7.25, 63, -3.0)] miss=[] lat=[1148.1]
- X22_kick_mic_sub_ring_65Hz s3: cuts=[(7.0, 63, -3.0)] miss=[] lat=[997.6]
- X23_ringout_quiet_room_two_modes s1: cuts=[(8.05, 630, -3.0), (10.7, 2500, -3.0), (13.65, 630, -6.0)] miss=[] lat=[50.5, 99.8]
- X23_ringout_quiet_room_two_modes s2: cuts=[(8.0, 630, -3.0), (10.65, 2500, -3.0), (13.6, 630, -6.0)] miss=[] lat=[99.5, 50.6]
- X23_ringout_quiet_room_two_modes s3: cuts=[(7.7, 630, -3.0), (10.8, 2500, -3.0), (13.6, 630, -6.0)] miss=[] lat=[-0.7, 101.5]

</details>

### EVERY scenario forced to watch mode (no probe), open loop

| scenario | ev | TP | miss | FP | early | tail | harm | dup | cuts | lat ms min/med/max | budget | FP at GEQ (Hz) | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C0_silent_room | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| C1_music_bed_drums | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S1_bass_under_quiet_music | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S2a_established_ring_8k | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 21 | 0 | 48/48/52 | 300 |  | PASS 3/3 |
| S2b_established_ring_8k_steep | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 21 | 0 | 48/48/52 | 300 |  | PASS 3/3 |
| S2c_established_clipped_2k4 | 3 | 3 | 0 | 0 | 1 | 0 | 0 | 20 | 0 | -2/1/1 | 300 |  | PASS 3/3 |
| S3_ring_during_music | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 18 | 0 | 53/502/602 | 300 |  | FAIL 1/3 |
| S4a_vocal_vibrato | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S4b_vocal_vibrato_band_edge | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S5_guitar_note_decays_to_sine | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S6_master_ramp_feedback_watch | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S6b_ringout_steps_latent_loop | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 9 | 0 | 98/147/300 | 300 |  | PASS 3/3 |
| S7_808_sub_bassline | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S8a_organ_melody | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S8b_flute_held_note | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S8c_whistle | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S9_clipped_howl_fast | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 9 | 0 | 99/102/103 | 300 |  | PASS 3/3 |
| S10_ring_between_bands | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 18 | 0 | 148/200/200 | 300 |  | PASS 3/3 |
| S11a_two_rings | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 30 | 0 | 1/77/351 | 300 |  | FAIL 2/3 |
| S11b_two_rings_near_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 30 | 0 | -2/100/152 | 300 |  | PASS 3/3 |
| S12_acoustic_guitar_wedge_ring_196Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 21 | 0 | 398/500/551 | 600 |  | PASS 3/3 |
| S13_slow_ring_3dB_s | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 0 | 0/549/1198 | 1000 |  | FAIL 2/3 |
| S14_ring_masked_by_cymbal | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 14 | 0 | 150/198/200 | 300 |  | PASS 3/3 |
| S15_long_rta_decay_tails | 3 | 3 | 0 | 0 | 0 | 8 | 0 | 14 | 0 | 50/52/102 | 300 |  | PASS 3/3 |
| S16_peak_hold_on | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S17_kick_pattern | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S18_vibrato_on_band_edge | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S19_driven_room_mode | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S20_song_start_stop_crowd | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S21_synth_pad_swell | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S22_speech_ringing_then_feedback | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 9 | 0 | 2/102/202 | 300 |  | PASS 3/3 |
| S23a_autogain_drift | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S23b_gain_offset_clip | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S24_bells_triangle_glock | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| M1_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 18 | 0 | 49/52/102 | 300 |  | PASS 3/3 |
| M2_quiet_music_ringout_two_modes | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 46 | 0 | 52/175/650 | 300 |  | FAIL 2/3 |
| M3_jazz_trio_lav_ring_400Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 24 | 0 | 101/350/602 | 300 |  | FAIL 1/3 |
| X1_organ_held_notes | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X2_flute_held_vibrato | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X3_whistle_held_drift | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X4_sine_lead_portamento | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X5_808_bassline_40_60Hz | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X6_soprano_closed_vowel_band_edge | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X7_plateaued_ring_under_music_from_t0 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 14 | 0 | 1603/5450/6452 | 1000 |  | FAIL 0/3 |
| X8_slow_ring_midband_under_chords | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 28 | 0 | 103/801/2750 | 1000 |  | FAIL 2/3 |
| X9_ring_rta_midpoint_525Hz_speech | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 14 | 0 | 1200/1299/1898 | 300 |  | FAIL 0/3 |
| X10_two_rings_exact_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 36 | 0 | 99/125/148 | 300 |  | PASS 3/3 |
| X11_amp_clipped_howl_minus12dBFS | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 15 | 0 | 351/399/399 | 300 |  | FAIL 0/3 |
| X12a_master_drop20_raise_channel | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 15 | 0 | 98/126/201 | 300 |  | PASS 3/3 |
| X12b_master_drop20_raise_busmaster | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 15 | 0 | 100/125/151 | 300 |  | PASS 3/3 |
| X13_decay16_jazz_lav_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 24 | 0 | 1/147/150 | 600 |  | PASS 3/3 |
| X14_peakhold_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 18 | 0 | 49/52/98 | 300 |  | PASS 3/3 |
| X15_kick_bass_unison_55Hz | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X16_wedge_ring_315Hz_loud_band | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 21 | 0 | 50/203/402 | 300 |  | FAIL 2/3 |
| X17_ring_122Hz_acoustic_guitar_body | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 12 | 0 | 350/549/601 | 600 |  | FAIL 2/3 |
| X18_applause_crowd_30s | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X19_handheld_ring_stalls_and_hops | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 18 | 0 | 51/248/252 | 300 |  | PASS 3/3 |
| X20_mains_hum_and_hvac_whine | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X21_reverberant_area_mic_slow_ring | 3 | 2 | 1 | 0 | 0 | 0 | 0 | 11 | 0 | 298/398/498 | 600 |  | FAIL 2/3 |
| X22_kick_mic_sub_ring_65Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 18 | 0 | 998/1148/1149 | 1000 |  | FAIL 1/3 |
| X23_ringout_quiet_room_two_modes | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 32 | 0 | 150/248/350 | 300 |  | FAIL 1/3 |
| **61 scenarios, 47 pass** | 117 | 116 | 1 | 0 | 1 | 8 | 0 | 616 | | -2/150/6452 | | | wall 12.7 s |

### EVERY scenario forced to watch mode (no probe), CLOSED loop (NotchController −3 dB steps, 1-frame actuation)

| scenario | ev | TP | miss | FP | early | tail | harm | dup | cuts | lat ms min/med/max | budget | FP at GEQ (Hz) | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S2a_established_ring_8k | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 48/48/52 | 300 |  | PASS 3/3 |
| S2b_established_ring_8k_steep | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 48/48/52 | 300 |  | PASS 3/3 |
| S2c_established_clipped_2k4 | 3 | 3 | 0 | 0 | 1 | 0 | 0 | 0 | 3 | -2/1/1 | 300 |  | PASS 3/3 |
| S3_ring_during_music | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 53/502/602 | 300 |  | FAIL 1/3 |
| S6b_ringout_steps_latent_loop | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 98/147/300 | 300 |  | PASS 3/3 |
| S9_clipped_howl_fast | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 99/102/103 | 300 |  | PASS 3/3 |
| S10_ring_between_bands | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 148/200/200 | 300 |  | PASS 3/3 |
| S11a_two_rings | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 0/75/397 | 300 |  | FAIL 2/3 |
| S11b_two_rings_near_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 6 | -2/127/398 | 300 |  | FAIL 1/3 |
| S12_acoustic_guitar_wedge_ring_196Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 398/500/551 | 600 |  | PASS 3/3 |
| S13_slow_ring_3dB_s | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0/549/1198 | 1000 |  | FAIL 2/3 |
| S14_ring_masked_by_cymbal | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 150/198/200 | 300 |  | PASS 3/3 |
| S15_long_rta_decay_tails | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 50/52/102 | 300 |  | PASS 3/3 |
| S22_speech_ringing_then_feedback | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 2/102/202 | 300 |  | PASS 3/3 |
| M1_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 49/52/102 | 300 |  | PASS 3/3 |
| M2_quiet_music_ringout_two_modes | 11 | 11 | 0 | 0 | 0 | 0 | 0 | 0 | 11 | -2/101/650 | 300 |  | FAIL 2/3 |
| M3_jazz_trio_lav_ring_400Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 101/350/602 | 300 |  | FAIL 1/3 |
| X7_plateaued_ring_under_music_from_t0 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 1603/5450/6452 | 1000 |  | FAIL 0/3 |
| X8_slow_ring_midband_under_chords | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 103/801/2750 | 1000 |  | FAIL 2/3 |
| X9_ring_rta_midpoint_525Hz_speech | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 1200/1299/1898 | 300 |  | FAIL 0/3 |
| X10_two_rings_exact_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 102/148/150 | 300 |  | PASS 3/3 |
| X11_amp_clipped_howl_minus12dBFS | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 351/399/399 | 300 |  | FAIL 0/3 |
| X12a_master_drop20_raise_channel | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 98/100/151 | 300 |  | PASS 3/3 |
| X12b_master_drop20_raise_busmaster | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 100/150/151 | 300 |  | PASS 3/3 |
| X13_decay16_jazz_lav_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 1/147/150 | 600 |  | PASS 3/3 |
| X14_peakhold_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 6 | 49/52/98 | 300 |  | PASS 3/3 |
| X16_wedge_ring_315Hz_loud_band | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 50/203/402 | 300 |  | FAIL 2/3 |
| X17_ring_122Hz_acoustic_guitar_body | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 350/549/601 | 600 |  | FAIL 2/3 |
| X19_handheld_ring_stalls_and_hops | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 51/248/252 | 300 |  | PASS 3/3 |
| X21_reverberant_area_mic_slow_ring | 3 | 2 | 1 | 0 | 0 | 0 | 0 | 0 | 2 | 298/398/498 | 600 |  | FAIL 2/3 |
| X22_kick_mic_sub_ring_65Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 998/1148/1149 | 1000 |  | FAIL 1/3 |
| X23_ringout_quiet_room_two_modes | 9 | 9 | 0 | 0 | 0 | 0 | 0 | 0 | 9 | 148/203/350 | 300 |  | FAIL 1/3 |
| **32 scenarios, 17 pass** | 119 | 118 | 1 | 0 | 1 | 0 | 0 | 3 | | -2/150/6452 | | | wall 21.1 s |

<details><summary>cuts per run</summary>

- S2a_established_ring_8k s1: cuts=[(0.05, 8000, -3.0)] miss=[] lat=[48.5]
- S2a_established_ring_8k s2: cuts=[(0.05, 8000, -3.0)] miss=[] lat=[52.5]
- S2a_established_ring_8k s3: cuts=[(0.05, 8000, -3.0)] miss=[] lat=[47.8]
- S2b_established_ring_8k_steep s1: cuts=[(0.05, 8000, -3.0)] miss=[] lat=[48.5]
- S2b_established_ring_8k_steep s2: cuts=[(0.05, 8000, -3.0)] miss=[] lat=[52.5]
- S2b_established_ring_8k_steep s3: cuts=[(0.05, 8000, -3.0)] miss=[] lat=[47.8]
- S2c_established_clipped_2k4 s1: cuts=[(0.0, 2500, -3.0)] miss=[] lat=[0.7]
- S2c_established_clipped_2k4 s2: cuts=[(-0.0, 2500, -3.0)] miss=[] lat=[-1.8]
- S2c_established_clipped_2k4 s3: cuts=[(0.0, 2500, -3.0)] miss=[] lat=[1.2]
- S3_ring_during_music s1: cuts=[(5.35, 3150, -3.0)] miss=[] lat=[502.3]
- S3_ring_during_music s2: cuts=[(5.55, 3150, -3.0)] miss=[] lat=[601.7]
- S3_ring_during_music s3: cuts=[(4.8, 3150, -3.0)] miss=[] lat=[52.7]
- S6b_ringout_steps_latent_loop s1: cuts=[(12.75, 2500, -3.0)] miss=[] lat=[97.7]
- S6b_ringout_steps_latent_loop s2: cuts=[(12.85, 2500, -3.0)] miss=[] lat=[299.8]
- S6b_ringout_steps_latent_loop s3: cuts=[(12.8, 2500, -3.0)] miss=[] lat=[147.2]
- S9_clipped_howl_fast s1: cuts=[(2.35, 2000, -3.0)] miss=[] lat=[98.7]
- S9_clipped_howl_fast s2: cuts=[(2.35, 2000, -3.0)] miss=[] lat=[102.6]
- S9_clipped_howl_fast s3: cuts=[(2.35, 2000, -3.0)] miss=[] lat=[101.8]
- S10_ring_between_bands s1: cuts=[(1.85, 2500, -3.0)] miss=[] lat=[199.6]
- S10_ring_between_bands s2: cuts=[(1.75, 2500, -3.0)] miss=[] lat=[200.4]
- S10_ring_between_bands s3: cuts=[(1.8, 2500, -3.0)] miss=[] lat=[147.7]
- S11a_two_rings s1: cuts=[(2.5, 3150, -3.0), (2.7, 1250, -3.0)] miss=[] lat=[397.1, 152.5]
- S11a_two_rings s2: cuts=[(2.1, 1250, -3.0), (2.4, 3150, -3.0)] miss=[] lat=[1.2, 0.2]
- S11a_two_rings s3: cuts=[(2.15, 3150, -3.0), (2.5, 1250, -3.0)] miss=[] lat=[148.2, 2.2]
- S11b_two_rings_near_octave s1: cuts=[(1.95, 1250, -3.0), (2.85, 2500, -3.0)] miss=[] lat=[-1.6, 398.3]
- S11b_two_rings_near_octave s2: cuts=[(2.1, 1250, -3.0), (2.2, 2500, -3.0)] miss=[] lat=[101.2, 51.2]
- S11b_two_rings_near_octave s3: cuts=[(2.05, 1250, -3.0), (2.45, 2500, -3.0)] miss=[] lat=[152.3, 352.0]
- S12_acoustic_guitar_wedge_ring_196Hz s1: cuts=[(5.65, 200, -3.0)] miss=[] lat=[398.4]
- S12_acoustic_guitar_wedge_ring_196Hz s2: cuts=[(5.75, 200, -3.0)] miss=[] lat=[500.5]
- S12_acoustic_guitar_wedge_ring_196Hz s3: cuts=[(5.8, 200, -3.0)] miss=[] lat=[550.6]
- S13_slow_ring_3dB_s s1: cuts=[(8.5, 5000, -3.0)] miss=[] lat=[0.3]
- S13_slow_ring_3dB_s s2: cuts=[(9.45, 5000, -3.0)] miss=[] lat=[548.8]
- S13_slow_ring_3dB_s s3: cuts=[(10.4, 5000, -3.0)] miss=[] lat=[1197.8]
- S14_ring_masked_by_cymbal s1: cuts=[(2.9, 4000, -3.0)] miss=[] lat=[197.7]
- S14_ring_masked_by_cymbal s2: cuts=[(3.0, 4000, -3.0)] miss=[] lat=[200.3]
- S14_ring_masked_by_cymbal s3: cuts=[(2.85, 4000, -3.0)] miss=[] lat=[150.4]
- S15_long_rta_decay_tails s1: cuts=[(3.8, 3150, -3.0)] miss=[] lat=[49.5]
- S15_long_rta_decay_tails s2: cuts=[(3.75, 3150, -3.0)] miss=[] lat=[51.5]
- S15_long_rta_decay_tails s3: cuts=[(3.9, 3150, -3.0)] miss=[] lat=[101.5]
- S22_speech_ringing_then_feedback s1: cuts=[(6.55, 3150, -3.0)] miss=[] lat=[2.0]
- S22_speech_ringing_then_feedback s2: cuts=[(6.5, 3150, -3.0)] miss=[] lat=[102.0]
- S22_speech_ringing_then_feedback s3: cuts=[(6.65, 3150, -3.0)] miss=[] lat=[202.1]
- M1_loud_band_wedge_ring s1: cuts=[(5.2, 3150, -3.0)] miss=[] lat=[51.7]
- M1_loud_band_wedge_ring s2: cuts=[(5.25, 3150, -3.0)] miss=[] lat=[102.0]
- M1_loud_band_wedge_ring s3: cuts=[(5.2, 3150, -3.0)] miss=[] lat=[49.3]
- M2_quiet_music_ringout_two_modes s1: cuts=[(5.2, 5000, -3.0), (9.8, 8000, -3.0), (10.1, 5000, -6.0), (15.6, 5000, -9.0)] miss=[] lat=[51.7, 250.5, -2.4, 100.9]
- M2_quiet_music_ringout_two_modes s2: cuts=[(5.8, 5000, -3.0), (9.8, 8000, -3.0), (9.85, 5000, -6.0)] miss=[] lat=[650.0, 47.2, 98.0]
- M2_quiet_music_ringout_two_modes s3: cuts=[(5.35, 5000, -3.0), (9.85, 5000, -6.0), (9.85, 8000, -3.0), (15.55, 8000, -6.0)] miss=[] lat=[150.1, 50.8, 200.8, 147.7]
- M3_jazz_trio_lav_ring_400Hz s1: cuts=[(5.15, 400, -3.0)] miss=[] lat=[100.6]
- M3_jazz_trio_lav_ring_400Hz s2: cuts=[(5.55, 400, -3.0)] miss=[] lat=[601.7]
- M3_jazz_trio_lav_ring_400Hz s3: cuts=[(5.15, 400, -3.0)] miss=[] lat=[350.0]
- X7_plateaued_ring_under_music_from_t0 s1: cuts=[(1.6, 1600, -3.0)] miss=[] lat=[1602.7]
- X7_plateaued_ring_under_music_from_t0 s2: cuts=[(5.45, 1600, -3.0)] miss=[] lat=[5449.7]
- X7_plateaued_ring_under_music_from_t0 s3: cuts=[(6.45, 1600, -3.0)] miss=[] lat=[6452.2]
- X8_slow_ring_midband_under_chords s1: cuts=[(7.35, 1250, -3.0)] miss=[] lat=[102.8]
- X8_slow_ring_midband_under_chords s2: cuts=[(6.1, 1250, -3.0)] miss=[] lat=[800.7]
- X8_slow_ring_midband_under_chords s3: cuts=[(10.6, 1250, -3.0)] miss=[] lat=[2750.0]
- X9_ring_rta_midpoint_525Hz_speech s1: cuts=[(4.55, 500, -3.0)] miss=[] lat=[1199.7]
- X9_ring_rta_midpoint_525Hz_speech s2: cuts=[(4.6, 500, -3.0)] miss=[] lat=[1299.0]
- X9_ring_rta_midpoint_525Hz_speech s3: cuts=[(5.65, 500, -3.0)] miss=[] lat=[1898.5]
- X10_two_rings_exact_octave s1: cuts=[(2.55, 1600, -3.0), (2.85, 3150, -3.0)] miss=[] lat=[148.0, 148.3]
- X10_two_rings_exact_octave s2: cuts=[(2.5, 1600, -3.0), (2.8, 3150, -3.0)] miss=[] lat=[102.7, 101.8]
- X10_two_rings_exact_octave s3: cuts=[(2.5, 1600, -3.0), (2.85, 3150, -3.0)] miss=[] lat=[148.2, 150.4]
- X11_amp_clipped_howl_minus12dBFS s1: cuts=[(4.6, 800, -3.0)] miss=[] lat=[398.7]
- X11_amp_clipped_howl_minus12dBFS s2: cuts=[(4.6, 800, -3.0)] miss=[] lat=[399.0]
- X11_amp_clipped_howl_minus12dBFS s3: cuts=[(4.55, 800, -3.0)] miss=[] lat=[350.8]
- X12a_master_drop20_raise_channel s1: cuts=[(0.15, 2000, -3.0)] miss=[] lat=[150.7]
- X12a_master_drop20_raise_channel s2: cuts=[(0.1, 2000, -3.0)] miss=[] lat=[97.6]
- X12a_master_drop20_raise_channel s3: cuts=[(0.1, 2000, -3.0)] miss=[] lat=[100.2]
- X12b_master_drop20_raise_busmaster s1: cuts=[(0.15, 2000, -3.0)] miss=[] lat=[150.7]
- X12b_master_drop20_raise_busmaster s2: cuts=[(0.15, 2000, -3.0)] miss=[] lat=[150.0]
- X12b_master_drop20_raise_busmaster s3: cuts=[(0.1, 2000, -3.0)] miss=[] lat=[100.2]
- X13_decay16_jazz_lav_ring s1: cuts=[(5.15, 400, -3.0)] miss=[] lat=[0.6]
- X13_decay16_jazz_lav_ring s2: cuts=[(5.35, 400, -3.0)] miss=[] lat=[147.0]
- X13_decay16_jazz_lav_ring s3: cuts=[(5.15, 400, -3.0)] miss=[] lat=[150.0]
- X14_peakhold_loud_band_wedge_ring s1: cuts=[(5.25, 3150, -3.0), (6.25, 3150, -6.0)] miss=[] lat=[97.5]
- X14_peakhold_loud_band_wedge_ring s2: cuts=[(5.25, 3150, -3.0), (6.3, 3150, -6.0)] miss=[] lat=[52.0]
- X14_peakhold_loud_band_wedge_ring s3: cuts=[(5.2, 3150, -3.0), (6.2, 3150, -6.0)] miss=[] lat=[49.3]
- X16_wedge_ring_315Hz_loud_band s1: cuts=[(6.3, 315, -3.0)] miss=[] lat=[202.7]
- X16_wedge_ring_315Hz_loud_band s2: cuts=[(6.5, 315, -3.0)] miss=[] lat=[402.0]
- X16_wedge_ring_315Hz_loud_band s3: cuts=[(6.15, 315, -3.0)] miss=[] lat=[50.3]
- X17_ring_122Hz_acoustic_guitar_body s1: cuts=[(7.05, 125, -3.0)] miss=[] lat=[350.0]
- X17_ring_122Hz_acoustic_guitar_body s2: cuts=[(7.0, 125, -3.0)] miss=[] lat=[601.3]
- X17_ring_122Hz_acoustic_guitar_body s3: cuts=[(6.95, 125, -3.0)] miss=[] lat=[549.0]
- X19_handheld_ring_stalls_and_hops s1: cuts=[(2.45, 2500, -3.0)] miss=[] lat=[248.5]
- X19_handheld_ring_stalls_and_hops s2: cuts=[(2.7, 2500, -3.0)] miss=[] lat=[252.4]
- X19_handheld_ring_stalls_and_hops s3: cuts=[(2.65, 2500, -3.0)] miss=[] lat=[51.3]
- X21_reverberant_area_mic_slow_ring s1: cuts=[(4.2, 630, -3.0)] miss=[] lat=[298.2]
- X21_reverberant_area_mic_slow_ring s2: cuts=[] miss=[643] lat=[]
- X21_reverberant_area_mic_slow_ring s3: cuts=[(4.7, 630, -3.0)] miss=[] lat=[498.1]
- X22_kick_mic_sub_ring_65Hz s1: cuts=[(7.45, 63, -3.0)] miss=[] lat=[1149.4]
- X22_kick_mic_sub_ring_65Hz s2: cuts=[(7.25, 63, -3.0)] miss=[] lat=[1148.1]
- X22_kick_mic_sub_ring_65Hz s3: cuts=[(7.0, 63, -3.0)] miss=[] lat=[997.6]
- X23_ringout_quiet_room_two_modes s1: cuts=[(9.3, 630, -3.0), (10.8, 2500, -3.0), (13.8, 630, -6.0)] miss=[] lat=[298.9, 201.4, 197.4]
- X23_ringout_quiet_room_two_modes s2: cuts=[(9.35, 630, -3.0), (10.75, 2500, -3.0), (13.75, 630, -6.0)] miss=[] lat=[349.0, 247.1, 148.3]
- X23_ringout_quiet_room_two_modes s3: cuts=[(9.35, 630, -3.0), (10.85, 2500, -3.0), (13.8, 630, -6.0)] miss=[] lat=[349.8, 203.0, 150.4]

</details>

### EVERY scenario forced to ring-out mode (probe where the scene has server steps), open loop

| scenario | ev | TP | miss | FP | early | tail | harm | dup | cuts | lat ms min/med/max | budget | FP at GEQ (Hz) | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C0_silent_room | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| C1_music_bed_drums | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S1_bass_under_quiet_music | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S2a_established_ring_8k | 3 | 3 | 0 | 0 | 1 | 0 | 0 | 20 | 0 | -2/1/1 | 300 |  | PASS 3/3 |
| S2b_established_ring_8k_steep | 3 | 3 | 0 | 0 | 1 | 0 | 0 | 20 | 0 | -2/1/1 | 300 |  | PASS 3/3 |
| S2c_established_clipped_2k4 | 3 | 3 | 0 | 0 | 1 | 0 | 3 | 20 | 0 | -2/1/1 | 300 |  | PASS 3/3 |
| S3_ring_during_music | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 18 | 0 | 53/100/550 | 300 |  | FAIL 2/3 |
| S4a_vocal_vibrato | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S4b_vocal_vibrato_band_edge | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S5_guitar_note_decays_to_sine | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S6_master_ramp_feedback_watch | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S6b_ringout_steps_latent_loop | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 9 | 0 | 51/52/300 | 300 |  | PASS 3/3 |
| S7_808_sub_bassline | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S8a_organ_melody | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | – | 300 | 630 | FAIL 2/3 |
| S8b_flute_held_note | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | – | 300 | 3150 | FAIL 2/3 |
| S8c_whistle | 0 | 0 | 0 | 20 | 0 | 0 | 0 | 0 | 0 | – | 300 | 1250,1600,2000 | FAIL 0/3 |
| S9_clipped_howl_fast | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 9 | 0 | 48/49/51 | 300 |  | PASS 3/3 |
| S10_ring_between_bands | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 18 | 0 | 98/102/150 | 300 |  | PASS 3/3 |
| S11a_two_rings | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 30 | 0 | 1/2/301 | 300 |  | FAIL 2/3 |
| S11b_two_rings_near_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 30 | 0 | -2/24/148 | 300 |  | PASS 3/3 |
| S12_acoustic_guitar_wedge_ring_196Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 24 | 0 | 349/351/500 | 600 |  | PASS 3/3 |
| S13_slow_ring_3dB_s | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 19 | 0 | 0/301/502 | 1000 |  | PASS 3/3 |
| S14_ring_masked_by_cymbal | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 14 | 0 | 48/148/153 | 300 |  | PASS 3/3 |
| S15_long_rta_decay_tails | 3 | 3 | 0 | 0 | 0 | 18 | 0 | 15 | 0 | 0/2/52 | 300 |  | PASS 3/3 |
| S16_peak_hold_on | 0 | 0 | 0 | 7 | 0 | 0 | 0 | 0 | 0 | – | 300 | 630,800,1000 | FAIL 0/3 |
| S17_kick_pattern | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S18_vibrato_on_band_edge | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S19_driven_room_mode | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S20_song_start_stop_crowd | 0 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0 | – | 300 | 1600,2500 | FAIL 0/3 |
| S21_synth_pad_swell | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | – | 300 | 400 | FAIL 2/3 |
| S22_speech_ringing_then_feedback | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 9 | 0 | 2/48/97 | 300 |  | PASS 3/3 |
| S23a_autogain_drift | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S23b_gain_offset_clip | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | – | 300 | 63 | FAIL 1/3 |
| S24_bells_triangle_glock | 0 | 0 | 0 | 20 | 0 | 0 | 0 | 0 | 0 | – | 300 | 1000,1250,1600,2000,5000,6300,8000 | FAIL 0/3 |
| M1_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 18 | 0 | 49/52/52 | 300 |  | PASS 3/3 |
| M2_quiet_music_ringout_two_modes | 6 | 6 | 0 | 0 | 1 | 0 | 0 | 47 | 0 | -1100/51/350 | 300 |  | FAIL 2/3 |
| M3_jazz_trio_lav_ring_400Hz | 3 | 3 | 0 | 6 | 0 | 0 | 0 | 24 | 0 | 49/302/302 | 300 | 3150 | FAIL 0/3 |
| X1_organ_held_notes | 0 | 0 | 0 | 7 | 0 | 0 | 0 | 0 | 0 | – | 300 | 630 | FAIL 1/3 |
| X2_flute_held_vibrato | 0 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0 | – | 300 | 800,1250 | FAIL 0/3 |
| X3_whistle_held_drift | 0 | 0 | 0 | 17 | 0 | 0 | 0 | 0 | 0 | – | 300 | 1250,1600,2500 | FAIL 0/3 |
| X4_sine_lead_portamento | 0 | 0 | 0 | 7 | 0 | 0 | 0 | 0 | 0 | – | 300 | 800,1000 | FAIL 0/3 |
| X5_808_bassline_40_60Hz | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X6_soprano_closed_vowel_band_edge | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | – | 300 | 630,1250 | FAIL 0/3 |
| X7_plateaued_ring_under_music_from_t0 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 31 | 0 | 199/200/202 | 1000 |  | PASS 3/3 |
| X8_slow_ring_midband_under_chords | 3 | 3 | 0 | 1 | 0 | 0 | 0 | 29 | 0 | -2/700/1748 | 1000 | 315 | FAIL 2/3 |
| X9_ring_rta_midpoint_525Hz_speech | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 20 | 0 | 1148/1150/1299 | 300 |  | FAIL 0/3 |
| X10_two_rings_exact_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 36 | 0 | 48/51/52 | 300 |  | PASS 3/3 |
| X11_amp_clipped_howl_minus12dBFS | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 15 | 0 | 99/100/100 | 300 |  | PASS 3/3 |
| X12a_master_drop20_raise_channel | 6 | 6 | 0 | 0 | 1 | 0 | 0 | 14 | 0 | -2/26/100 | 300 |  | PASS 3/3 |
| X12b_master_drop20_raise_busmaster | 6 | 6 | 0 | 0 | 1 | 0 | 0 | 14 | 0 | -2/50/102 | 300 |  | PASS 3/3 |
| X13_decay16_jazz_lav_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 24 | 0 | 1/2/102 | 600 |  | PASS 3/3 |
| X14_peakhold_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 18 | 0 | 2/49/52 | 300 |  | PASS 3/3 |
| X15_kick_bass_unison_55Hz | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X16_wedge_ring_315Hz_loud_band | 3 | 3 | 0 | 3 | 0 | 0 | 0 | 21 | 0 | 3/3/50 | 300 | 800,1250 | FAIL 1/3 |
| X17_ring_122Hz_acoustic_guitar_body | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 19 | 0 | 198/350/601 | 600 |  | FAIL 2/3 |
| X18_applause_crowd_30s | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | – | 300 | 1000,2000,2500 | FAIL 1/3 |
| X19_handheld_ring_stalls_and_hops | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 18 | 0 | 2/100/248 | 300 |  | PASS 3/3 |
| X20_mains_hum_and_hvac_whine | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | – | 300 | 630 | FAIL 1/3 |
| X21_reverberant_area_mic_slow_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 27 | 0 | 298/452/501 | 600 |  | PASS 3/3 |
| X22_kick_mic_sub_ring_65Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 18 | 0 | 801/901/901 | 1000 |  | PASS 3/3 |
| X23_ringout_quiet_room_two_modes | 6 | 6 | 0 | 0 | 4 | 0 | 0 | 32 | 0 | -1298/-401/150 | 300 |  | PASS 3/3 |
| **61 scenarios, 38 pass** | 117 | 117 | 0 | 119 | 10 | 18 | 3 | 680 | | -1298/52/1748 | | | wall 12.9 s |

### EVERY scenario forced to ring-out mode (probe where the scene has server steps), CLOSED loop (NotchController −3 dB steps, 1-frame actuation)

| scenario | ev | TP | miss | FP | early | tail | harm | dup | cuts | lat ms min/med/max | budget | FP at GEQ (Hz) | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S2a_established_ring_8k | 3 | 3 | 0 | 0 | 1 | 0 | 0 | 0 | 3 | -2/1/1 | 300 |  | PASS 3/3 |
| S2b_established_ring_8k_steep | 3 | 3 | 0 | 0 | 1 | 0 | 0 | 0 | 3 | -2/1/1 | 300 |  | PASS 3/3 |
| S2c_established_clipped_2k4 | 3 | 3 | 0 | 0 | 1 | 0 | 3 | 0 | 6 | -2/1/1 | 300 |  | PASS 3/3 |
| S3_ring_during_music | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 53/100/550 | 300 |  | FAIL 2/3 |
| S6b_ringout_steps_latent_loop | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 51/52/300 | 300 |  | PASS 3/3 |
| S9_clipped_howl_fast | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 48/49/51 | 300 |  | PASS 3/3 |
| S10_ring_between_bands | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 98/102/150 | 300 |  | PASS 3/3 |
| S11a_two_rings | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 0/25/397 | 300 |  | FAIL 2/3 |
| S11b_two_rings_near_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 6 | -2/1/151 | 300 |  | PASS 3/3 |
| S12_acoustic_guitar_wedge_ring_196Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 349/351/500 | 600 |  | PASS 3/3 |
| S13_slow_ring_3dB_s | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0/301/502 | 1000 |  | PASS 3/3 |
| S14_ring_masked_by_cymbal | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 48/148/153 | 300 |  | PASS 3/3 |
| S15_long_rta_decay_tails | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 2 | 5 | 0/2/52 | 300 |  | PASS 3/3 |
| S22_speech_ringing_then_feedback | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 2/48/97 | 300 |  | PASS 3/3 |
| M1_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 1 | 0 | 0 | 4 | 49/52/52 | 300 |  | PASS 3/3 |
| M2_quiet_music_ringout_two_modes | 11 | 11 | 0 | 0 | 0 | 0 | 0 | 0 | 11 | -2/2/350 | 300 |  | FAIL 2/3 |
| M3_jazz_trio_lav_ring_400Hz | 3 | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 6 | -1/202/302 | 300 | 3150 | FAIL 0/3 |
| X7_plateaued_ring_under_music_from_t0 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 199/200/202 | 1000 |  | PASS 3/3 |
| X8_slow_ring_midband_under_chords | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | -2/700/1748 | 1000 |  | FAIL 2/3 |
| X9_ring_rta_midpoint_525Hz_speech | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 1148/1150/1299 | 300 |  | FAIL 0/3 |
| X10_two_rings_exact_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 1/51/102 | 300 |  | PASS 3/3 |
| X11_amp_clipped_howl_minus12dBFS | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 99/100/100 | 300 |  | PASS 3/3 |
| X12a_master_drop20_raise_channel | 3 | 3 | 0 | 0 | 1 | 0 | 0 | 0 | 3 | -2/1/1 | 300 |  | PASS 3/3 |
| X12b_master_drop20_raise_busmaster | 3 | 3 | 0 | 0 | 1 | 0 | 0 | 0 | 3 | -2/1/1 | 300 |  | PASS 3/3 |
| X13_decay16_jazz_lav_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 1 | 4 | 1/2/102 | 600 |  | PASS 3/3 |
| X14_peakhold_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 6 | 2/49/52 | 300 |  | PASS 3/3 |
| X16_wedge_ring_315Hz_loud_band | 3 | 3 | 0 | 6 | 0 | 1 | 0 | 0 | 10 | 3/3/50 | 300 | 100,125,160,630,800,1250 | FAIL 0/3 |
| X17_ring_122Hz_acoustic_guitar_body | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 198/350/601 | 600 |  | FAIL 2/3 |
| X19_handheld_ring_stalls_and_hops | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 2/100/248 | 300 |  | PASS 3/3 |
| X21_reverberant_area_mic_slow_ring | 3 | 3 | 0 | 1 | 0 | 0 | 0 | 0 | 4 | 298/452/501 | 600 | 400 | FAIL 2/3 |
| X22_kick_mic_sub_ring_65Hz | 3 | 3 | 0 | 1 | 0 | 0 | 0 | 0 | 4 | 801/901/901 | 1000 | 125 | FAIL 2/3 |
| X23_ringout_quiet_room_two_modes | 6 | 6 | 0 | 0 | 3 | 0 | 0 | 0 | 9 | -1/75/102 | 300 |  | PASS 3/3 |
| **32 scenarios, 22 pass** | 116 | 116 | 0 | 11 | 8 | 2 | 3 | 6 | | -2/51/1748 | | | wall 21.5 s |

<details><summary>cuts per run</summary>

- S2a_established_ring_8k s1: cuts=[(0.0, 8000, -3.0)] miss=[] lat=[0.7]
- S2a_established_ring_8k s2: cuts=[(-0.0, 8000, -3.0)] miss=[] lat=[-1.8]
- S2a_established_ring_8k s3: cuts=[(0.0, 8000, -3.0)] miss=[] lat=[1.2]
- S2b_established_ring_8k_steep s1: cuts=[(0.0, 8000, -3.0)] miss=[] lat=[0.7]
- S2b_established_ring_8k_steep s2: cuts=[(-0.0, 8000, -3.0)] miss=[] lat=[-1.8]
- S2b_established_ring_8k_steep s3: cuts=[(0.0, 8000, -3.0)] miss=[] lat=[1.2]
- S2c_established_clipped_2k4 s1: cuts=[(0.0, 2500, -3.0), (0.0, 8000, -3.0)] miss=[] lat=[0.7]
- S2c_established_clipped_2k4 s2: cuts=[(-0.0, 2500, -3.0), (-0.0, 8000, -3.0)] miss=[] lat=[-1.8]
- S2c_established_clipped_2k4 s3: cuts=[(0.0, 2500, -3.0), (0.0, 8000, -3.0)] miss=[] lat=[1.2]
- S3_ring_during_music s1: cuts=[(4.95, 3150, -3.0)] miss=[] lat=[100.2]
- S3_ring_during_music s2: cuts=[(5.5, 3150, -3.0)] miss=[] lat=[550.0]
- S3_ring_during_music s3: cuts=[(4.8, 3150, -3.0)] miss=[] lat=[52.7]
- S6b_ringout_steps_latent_loop s1: cuts=[(12.7, 2500, -3.0)] miss=[] lat=[51.7]
- S6b_ringout_steps_latent_loop s2: cuts=[(12.85, 2500, -3.0)] miss=[] lat=[299.8]
- S6b_ringout_steps_latent_loop s3: cuts=[(12.7, 2500, -3.0)] miss=[] lat=[50.9]
- S9_clipped_howl_fast s1: cuts=[(2.3, 2000, -3.0)] miss=[] lat=[47.7]
- S9_clipped_howl_fast s2: cuts=[(2.3, 2000, -3.0)] miss=[] lat=[51.0]
- S9_clipped_howl_fast s3: cuts=[(2.3, 2000, -3.0)] miss=[] lat=[49.4]
- S10_ring_between_bands s1: cuts=[(1.8, 2500, -3.0)] miss=[] lat=[150.5]
- S10_ring_between_bands s2: cuts=[(1.65, 2500, -3.0)] miss=[] lat=[98.1]
- S10_ring_between_bands s3: cuts=[(1.75, 2500, -3.0)] miss=[] lat=[101.6]
- S11a_two_rings s1: cuts=[(2.4, 3150, -3.0), (2.7, 1250, -3.0)] miss=[] lat=[397.1, 47.1]
- S11a_two_rings s2: cuts=[(2.1, 1250, -3.0), (2.4, 3150, -3.0)] miss=[] lat=[1.2, 0.2]
- S11a_two_rings s3: cuts=[(2.15, 3150, -3.0), (2.4, 1250, -3.0)] miss=[] lat=[51.8, 2.2]
- S11b_two_rings_near_octave s1: cuts=[(1.95, 1250, -3.0), (2.6, 2500, -3.0)] miss=[] lat=[-1.6, 150.8]
- S11b_two_rings_near_octave s2: cuts=[(2.05, 1250, -3.0), (2.3, 2500, -3.0)] miss=[] lat=[48.3, 1.0]
- S11b_two_rings_near_octave s3: cuts=[(1.9, 1250, -3.0), (2.4, 2500, -3.0)] miss=[] lat=[-2.2, 1.8]
- S12_acoustic_guitar_wedge_ring_196Hz s1: cuts=[(5.6, 200, -3.0)] miss=[] lat=[349.3]
- S12_acoustic_guitar_wedge_ring_196Hz s2: cuts=[(5.6, 200, -3.0)] miss=[] lat=[350.8]
- S12_acoustic_guitar_wedge_ring_196Hz s3: cuts=[(5.75, 200, -3.0)] miss=[] lat=[500.1]
- S13_slow_ring_3dB_s s1: cuts=[(8.5, 5000, -3.0)] miss=[] lat=[0.3]
- S13_slow_ring_3dB_s s2: cuts=[(9.2, 5000, -3.0)] miss=[] lat=[300.9]
- S13_slow_ring_3dB_s s3: cuts=[(9.7, 5000, -3.0)] miss=[] lat=[502.0]
- S14_ring_masked_by_cymbal s1: cuts=[(2.85, 4000, -3.0)] miss=[] lat=[148.3]
- S14_ring_masked_by_cymbal s2: cuts=[(2.95, 4000, -3.0)] miss=[] lat=[152.9]
- S14_ring_masked_by_cymbal s3: cuts=[(2.75, 4000, -3.0)] miss=[] lat=[47.7]
- S15_long_rta_decay_tails s1: cuts=[(3.75, 3150, -3.0)] miss=[] lat=[1.6]
- S15_long_rta_decay_tails s2: cuts=[(3.7, 3150, -3.0), (4.7, 3150, -6.0)] miss=[] lat=[0.2]
- S15_long_rta_decay_tails s3: cuts=[(3.85, 3150, -3.0), (4.9, 3150, -6.0)] miss=[] lat=[52.2]
- S22_speech_ringing_then_feedback s1: cuts=[(6.55, 3150, -3.0)] miss=[] lat=[2.0]
- S22_speech_ringing_then_feedback s2: cuts=[(6.45, 3150, -3.0)] miss=[] lat=[48.0]
- S22_speech_ringing_then_feedback s3: cuts=[(6.55, 3150, -3.0)] miss=[] lat=[97.3]
- M1_loud_band_wedge_ring s1: cuts=[(5.2, 3150, -3.0), (10.2, 3150, -6.0)] miss=[] lat=[51.7]
- M1_loud_band_wedge_ring s2: cuts=[(5.2, 3150, -3.0)] miss=[] lat=[52.2]
- M1_loud_band_wedge_ring s3: cuts=[(5.2, 3150, -3.0)] miss=[] lat=[49.3]
- M2_quiet_music_ringout_two_modes s1: cuts=[(5.15, 5000, -3.0), (9.7, 8000, -3.0), (10.35, 5000, -6.0), (15.6, 5000, -9.0)] miss=[] lat=[0.6, 299.4, -2.4, 2.3]
- M2_quiet_music_ringout_two_modes s2: cuts=[(5.5, 5000, -3.0), (9.7, 8000, -3.0), (10.0, 5000, -6.0)] miss=[] lat=[350.0, 47.6, 2.1]
- M2_quiet_music_ringout_two_modes s3: cuts=[(5.2, 5000, -3.0), (9.75, 8000, -3.0), (9.8, 5000, -6.0), (15.4, 8000, -6.0)] miss=[] lat=[-0.7, -0.4, 98.6, -0.1]
- M3_jazz_trio_lav_ring_400Hz s1: cuts=[(2.4, 3150, -3.0), (5.05, 400, -3.0)] miss=[] lat=[-1.1]
- M3_jazz_trio_lav_ring_400Hz s2: cuts=[(1.55, 3150, -3.0), (5.15, 400, -3.0)] miss=[] lat=[202.2]
- M3_jazz_trio_lav_ring_400Hz s3: cuts=[(2.1, 3150, -3.0), (5.1, 400, -3.0)] miss=[] lat=[301.6]
- X7_plateaued_ring_under_music_from_t0 s1: cuts=[(0.2, 1600, -3.0)] miss=[] lat=[198.8]
- X7_plateaued_ring_under_music_from_t0 s2: cuts=[(0.2, 1600, -3.0)] miss=[] lat=[200.3]
- X7_plateaued_ring_under_music_from_t0 s3: cuts=[(0.2, 1600, -3.0)] miss=[] lat=[201.8]
- X8_slow_ring_midband_under_chords s1: cuts=[(7.25, 1250, -3.0)] miss=[] lat=[-2.4]
- X8_slow_ring_midband_under_chords s2: cuts=[(6.0, 1250, -3.0)] miss=[] lat=[700.0]
- X8_slow_ring_midband_under_chords s3: cuts=[(9.6, 1250, -3.0)] miss=[] lat=[1747.5]
- X9_ring_rta_midpoint_525Hz_speech s1: cuts=[(4.5, 500, -3.0)] miss=[] lat=[1149.6]
- X9_ring_rta_midpoint_525Hz_speech s2: cuts=[(4.6, 500, -3.0)] miss=[] lat=[1299.0]
- X9_ring_rta_midpoint_525Hz_speech s3: cuts=[(4.9, 500, -3.0)] miss=[] lat=[1148.1]
- X10_two_rings_exact_octave s1: cuts=[(2.45, 1600, -3.0), (2.75, 3150, -3.0)] miss=[] lat=[48.5, 1.0]
- X10_two_rings_exact_octave s2: cuts=[(2.45, 1600, -3.0), (2.8, 3150, -3.0)] miss=[] lat=[50.3, 101.8]
- X10_two_rings_exact_octave s3: cuts=[(2.4, 1600, -3.0), (2.8, 3150, -3.0)] miss=[] lat=[51.8, 50.9]
- X11_amp_clipped_howl_minus12dBFS s1: cuts=[(4.3, 800, -3.0)] miss=[] lat=[99.4]
- X11_amp_clipped_howl_minus12dBFS s2: cuts=[(4.3, 800, -3.0)] miss=[] lat=[99.9]
- X11_amp_clipped_howl_minus12dBFS s3: cuts=[(4.3, 800, -3.0)] miss=[] lat=[99.7]
- X12a_master_drop20_raise_channel s1: cuts=[(0.0, 2000, -3.0)] miss=[] lat=[0.7]
- X12a_master_drop20_raise_channel s2: cuts=[(-0.0, 2000, -3.0)] miss=[] lat=[-1.8]
- X12a_master_drop20_raise_channel s3: cuts=[(0.0, 2000, -3.0)] miss=[] lat=[1.2]
- X12b_master_drop20_raise_busmaster s1: cuts=[(0.0, 2000, -3.0)] miss=[] lat=[0.7]
- X12b_master_drop20_raise_busmaster s2: cuts=[(-0.0, 2000, -3.0)] miss=[] lat=[-1.8]
- X12b_master_drop20_raise_busmaster s3: cuts=[(0.0, 2000, -3.0)] miss=[] lat=[1.2]
- X13_decay16_jazz_lav_ring s1: cuts=[(5.15, 400, -3.0)] miss=[] lat=[0.6]
- X13_decay16_jazz_lav_ring s2: cuts=[(5.2, 400, -3.0), (6.2, 400, -6.0)] miss=[] lat=[2.2]
- X13_decay16_jazz_lav_ring s3: cuts=[(5.1, 400, -3.0)] miss=[] lat=[101.6]
- X14_peakhold_loud_band_wedge_ring s1: cuts=[(5.2, 3150, -3.0), (6.25, 3150, -6.0)] miss=[] lat=[51.7]
- X14_peakhold_loud_band_wedge_ring s2: cuts=[(5.2, 3150, -3.0), (6.2, 3150, -6.0)] miss=[] lat=[2.2]
- X14_peakhold_loud_band_wedge_ring s3: cuts=[(5.2, 3150, -3.0), (6.2, 3150, -6.0)] miss=[] lat=[49.3]
- X16_wedge_ring_315Hz_loud_band s1: cuts=[(2.05, 1250, -3.0), (6.1, 315, -3.0), (8.4, 125, -3.0), (10.7, 125, -6.0), (10.7, 315, -6.0)] miss=[] lat=[2.9]
- X16_wedge_ring_315Hz_loud_band s2: cuts=[(6.15, 315, -3.0), (9.2, 630, -3.0), (13.2, 100, -3.0)] miss=[] lat=[50.0]
- X16_wedge_ring_315Hz_loud_band s3: cuts=[(6.1, 315, -3.0), (11.25, 800, -3.0)] miss=[] lat=[2.8]
- X17_ring_122Hz_acoustic_guitar_body s1: cuts=[(7.05, 125, -3.0)] miss=[] lat=[350.0]
- X17_ring_122Hz_acoustic_guitar_body s2: cuts=[(7.0, 125, -3.0)] miss=[] lat=[601.3]
- X17_ring_122Hz_acoustic_guitar_body s3: cuts=[(6.6, 125, -3.0)] miss=[] lat=[198.4]
- X19_handheld_ring_stalls_and_hops s1: cuts=[(2.45, 2500, -3.0)] miss=[] lat=[248.5]
- X19_handheld_ring_stalls_and_hops s2: cuts=[(2.55, 2500, -3.0)] miss=[] lat=[100.5]
- X19_handheld_ring_stalls_and_hops s3: cuts=[(2.6, 2500, -3.0)] miss=[] lat=[1.8]
- X21_reverberant_area_mic_slow_ring s1: cuts=[(4.2, 630, -3.0), (10.8, 400, -3.0)] miss=[] lat=[298.2]
- X21_reverberant_area_mic_slow_ring s2: cuts=[(4.7, 630, -3.0)] miss=[] lat=[501.1]
- X21_reverberant_area_mic_slow_ring s3: cuts=[(4.65, 630, -3.0)] miss=[] lat=[452.3]
- X22_kick_mic_sub_ring_65Hz s1: cuts=[(7.1, 63, -3.0)] miss=[] lat=[801.4]
- X22_kick_mic_sub_ring_65Hz s2: cuts=[(7.0, 63, -3.0), (9.7, 125, -3.0)] miss=[] lat=[901.3]
- X22_kick_mic_sub_ring_65Hz s3: cuts=[(6.9, 63, -3.0)] miss=[] lat=[900.6]
- X23_ringout_quiet_room_two_modes s1: cuts=[(8.05, 630, -3.0), (10.7, 2500, -3.0), (13.65, 630, -6.0)] miss=[] lat=[50.5, 99.8]
- X23_ringout_quiet_room_two_modes s2: cuts=[(8.0, 630, -3.0), (10.65, 2500, -3.0), (13.6, 630, -6.0)] miss=[] lat=[99.5, 50.6]
- X23_ringout_quiet_room_two_modes s3: cuts=[(7.7, 630, -3.0), (10.8, 2500, -3.0), (13.6, 630, -6.0)] miss=[] lat=[-0.7, 101.5]

</details>
