# disc-track-and-group: FeedbackDetector over the rtasim corpus

generated 2026-09-21 13:49:46 — seeds [1, 2, 3]; config from device.yaml + defaults: prominence 12.0/±3, track floor 8.0, min_level -45.0, K1 5, K2 15, window watch 160/ringout 100/LF 40..12500 Hz, loud -10.0, budget 6

### informed open loop

| scenario | ev | TP | miss | FP | early | tail | harm | cuts | lat ms min/med/max | budget | FP at GEQ (Hz) | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C0_silent_room | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| C1_music_bed_drums | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S1_bass_under_quiet_music | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S2a_established_ring_8k | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 199/200/202 | 300 |  | PASS 3/3 |
| S2b_established_ring_8k_steep | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 199/200/202 | 300 |  | PASS 3/3 |
| S2c_established_clipped_2k4 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 98/100/100 | 300 |  | PASS 3/3 |
| S3_ring_during_music | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 1/102/352 | 300 |  | FAIL 2/3 |
| S4a_vocal_vibrato | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S4b_vocal_vibrato_band_edge | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S5_guitar_note_decays_to_sine | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S6_master_ramp_feedback_watch | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S6b_ringout_steps_latent_loop | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 98/99/202 | 300 |  | PASS 3/3 |
| S7_808_sub_bassline | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S8a_organ_melody | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S8b_flute_held_note | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S8c_whistle | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S9_clipped_howl_fast | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 49/99/150 | 300 |  | PASS 3/3 |
| S10_ring_between_bands | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 148/200/251 | 300 |  | PASS 3/3 |
| S11a_two_rings | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 52/100/202 | 300 |  | PASS 3/3 |
| S11b_two_rings_near_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 48/174/447 | 300 |  | FAIL 2/3 |
| S12_acoustic_guitar_wedge_ring_196Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 402/451/652 | 600 |  | FAIL 2/3 |
| S13_slow_ring_3dB_s | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0/150/351 | 1000 |  | PASS 3/3 |
| S14_ring_masked_by_cymbal | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 48/50/148 | 300 |  | PASS 3/3 |
| S15_long_rta_decay_tails | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 2/52/102 | 300 |  | PASS 3/3 |
| S16_peak_hold_on | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S17_kick_pattern | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S18_vibrato_on_band_edge | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S19_driven_room_mode | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S20_song_start_stop_crowd | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S21_synth_pad_swell | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S22_speech_ringing_then_feedback | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 50/102/251 | 300 |  | PASS 3/3 |
| S23a_autogain_drift | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S23b_gain_offset_clip | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S24_bells_triangle_glock | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| M1_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 49/98/102 | 300 |  | PASS 3/3 |
| M2_quiet_music_ringout_two_modes | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 47/102/152 | 300 |  | PASS 3/3 |
| M3_jazz_trio_lav_ring_400Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | -1/150/202 | 300 |  | PASS 3/3 |
| X1_organ_held_notes | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X2_flute_held_vibrato | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X3_whistle_held_drift | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X4_sine_lead_portamento | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X5_808_bassline_40_60Hz | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X6_soprano_closed_vowel_band_edge | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X7_plateaued_ring_under_music_from_t0 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 199/200/202 | 1000 |  | PASS 3/3 |
| X8_slow_ring_midband_under_chords | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 502/2000/2502 | 1000 |  | FAIL 1/3 |
| X9_ring_rta_midpoint_525Hz_speech | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 899/1100/1102 | 300 |  | FAIL 0/3 |
| X10_two_rings_exact_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 48/125/298 | 300 |  | PASS 3/3 |
| X11_amp_clipped_howl_minus12dBFS | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 100/849/849 | 300 |  | FAIL 1/3 |
| X12a_master_drop20_raise_channel | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 152/200/202 | 300 |  | PASS 3/3 |
| X12b_master_drop20_raise_busmaster | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 99/175/202 | 300 |  | PASS 3/3 |
| X13_decay16_jazz_lav_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 1/2/102 | 600 |  | PASS 3/3 |
| X14_peakhold_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 52/200/202 | 300 |  | PASS 3/3 |
| X15_kick_bass_unison_55Hz | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X16_wedge_ring_315Hz_loud_band | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 101/153/203 | 300 |  | PASS 3/3 |
| X17_ring_122Hz_acoustic_guitar_body | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 52/202/400 | 600 |  | PASS 3/3 |
| X18_applause_crowd_30s | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X19_handheld_ring_stalls_and_hops | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 51/248/400 | 300 |  | FAIL 2/3 |
| X20_mains_hum_and_hvac_whine | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X21_reverberant_area_mic_slow_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 298/298/603 | 600 |  | FAIL 2/3 |
| X22_kick_mic_sub_ring_65Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 148/303/401 | 1000 |  | PASS 3/3 |
| X23_ringout_quiet_room_two_modes | 6 | 6 | 0 | 0 | 5 | 0 | 0 | 0 | -1152/-624/102 | 300 |  | PASS 3/3 |
| **61 scenarios, 53 pass** | 117 | 117 | 0 | 0 | 5 | 0 | 0 | | | | | wall 34.7 s |

### informed closed loop

| scenario | ev | TP | miss | FP | early | tail | harm | cuts | lat ms min/med/max | budget | FP at GEQ (Hz) | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S2a_established_ring_8k | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 199/200/202 | 300 |  | PASS 3/3 |
| S2b_established_ring_8k_steep | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 199/200/202 | 300 |  | PASS 3/3 |
| S2c_established_clipped_2k4 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 98/100/100 | 300 |  | PASS 3/3 |
| S3_ring_during_music | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 1/102/352 | 300 |  | FAIL 2/3 |
| S6b_ringout_steps_latent_loop | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 98/99/202 | 300 |  | PASS 3/3 |
| S9_clipped_howl_fast | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 49/99/150 | 300 |  | PASS 3/3 |
| S10_ring_between_bands | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 148/200/251 | 300 |  | PASS 3/3 |
| S11a_two_rings | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 6 | 52/100/202 | 300 |  | PASS 3/3 |
| S11b_two_rings_near_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 6 | 48/174/1451 | 300 |  | FAIL 2/3 |
| S12_acoustic_guitar_wedge_ring_196Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 402/451/652 | 600 |  | FAIL 2/3 |
| S13_slow_ring_3dB_s | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 0/150/351 | 1000 |  | PASS 3/3 |
| S14_ring_masked_by_cymbal | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 48/50/148 | 300 |  | PASS 3/3 |
| S15_long_rta_decay_tails | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 2/52/102 | 300 |  | PASS 3/3 |
| S22_speech_ringing_then_feedback | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 50/102/251 | 300 |  | PASS 3/3 |
| M1_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 49/98/102 | 300 |  | PASS 3/3 |
| M2_quiet_music_ringout_two_modes | 11 | 11 | 0 | 0 | 0 | 0 | 0 | 11 | -2/47/102 | 300 |  | PASS 3/3 |
| M3_jazz_trio_lav_ring_400Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | -1/150/202 | 300 |  | PASS 3/3 |
| X7_plateaued_ring_under_music_from_t0 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 199/200/202 | 1000 |  | PASS 3/3 |
| X8_slow_ring_midband_under_chords | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 502/2000/2502 | 1000 |  | FAIL 1/3 |
| X9_ring_rta_midpoint_525Hz_speech | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 899/1100/1102 | 300 |  | FAIL 0/3 |
| X10_two_rings_exact_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 6 | 48/102/150 | 300 |  | PASS 3/3 |
| X11_amp_clipped_howl_minus12dBFS | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 100/849/849 | 300 |  | FAIL 1/3 |
| X12a_master_drop20_raise_channel | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 199/200/202 | 300 |  | PASS 3/3 |
| X12b_master_drop20_raise_busmaster | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 199/200/202 | 300 |  | PASS 3/3 |
| X13_decay16_jazz_lav_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 1/2/102 | 600 |  | PASS 3/3 |
| X14_peakhold_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 52/200/202 | 300 |  | PASS 3/3 |
| X16_wedge_ring_315Hz_loud_band | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 101/153/203 | 300 |  | PASS 3/3 |
| X17_ring_122Hz_acoustic_guitar_body | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 52/202/400 | 600 |  | PASS 3/3 |
| X19_handheld_ring_stalls_and_hops | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 51/248/400 | 300 |  | FAIL 2/3 |
| X21_reverberant_area_mic_slow_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 298/298/603 | 600 |  | FAIL 2/3 |
| X22_kick_mic_sub_ring_65Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 148/303/401 | 1000 |  | PASS 3/3 |
| X23_ringout_quiet_room_two_modes | 6 | 6 | 0 | 0 | 3 | 0 | 0 | 9 | 51/101/201 | 300 |  | PASS 3/3 |
| **32 scenarios, 24 pass** | 116 | 116 | 0 | 0 | 3 | 0 | 0 | | | | | wall 22.2 s |

<details><summary>cuts per run</summary>

- S2a_established_ring_8k s1: cuts=[(0.2, 8000, -3.0)] miss=[] lat=[198.8]
- S2a_established_ring_8k s2: cuts=[(0.2, 8000, -3.0)] miss=[] lat=[200.3]
- S2a_established_ring_8k s3: cuts=[(0.2, 8000, -3.0)] miss=[] lat=[201.8]
- S2b_established_ring_8k_steep s1: cuts=[(0.2, 8000, -3.0)] miss=[] lat=[198.8]
- S2b_established_ring_8k_steep s2: cuts=[(0.2, 8000, -3.0)] miss=[] lat=[200.3]
- S2b_established_ring_8k_steep s3: cuts=[(0.2, 8000, -3.0)] miss=[] lat=[201.8]
- S2c_established_clipped_2k4 s1: cuts=[(0.1, 2500, -3.0)] miss=[] lat=[100.1]
- S2c_established_clipped_2k4 s2: cuts=[(0.1, 2500, -3.0)] miss=[] lat=[97.6]
- S2c_established_clipped_2k4 s3: cuts=[(0.1, 2500, -3.0)] miss=[] lat=[100.2]
- S3_ring_during_music s1: cuts=[(5.2, 3150, -3.0)] miss=[] lat=[351.7]
- S3_ring_during_music s2: cuts=[(4.95, 3150, -3.0)] miss=[] lat=[0.6]
- S3_ring_during_music s3: cuts=[(4.85, 3150, -3.0)] miss=[] lat=[101.8]
- S6b_ringout_steps_latent_loop s1: cuts=[(12.75, 2500, -3.0)] miss=[] lat=[97.7]
- S6b_ringout_steps_latent_loop s2: cuts=[(12.75, 2500, -3.0)] miss=[] lat=[201.6]
- S6b_ringout_steps_latent_loop s3: cuts=[(12.75, 2500, -3.0)] miss=[] lat=[99.3]
- S9_clipped_howl_fast s1: cuts=[(2.35, 2000, -3.0)] miss=[] lat=[98.7]
- S9_clipped_howl_fast s2: cuts=[(2.4, 2000, -3.0)] miss=[] lat=[150.2]
- S9_clipped_howl_fast s3: cuts=[(2.3, 2000, -3.0)] miss=[] lat=[49.4]
- S10_ring_between_bands s1: cuts=[(1.9, 2500, -3.0)] miss=[] lat=[251.3]
- S10_ring_between_bands s2: cuts=[(1.75, 2500, -3.0)] miss=[] lat=[200.4]
- S10_ring_between_bands s3: cuts=[(1.8, 2500, -3.0)] miss=[] lat=[147.7]
- S11a_two_rings s1: cuts=[(2.45, 3150, -3.0), (2.5, 1250, -3.0)] miss=[] lat=[202.5, 98.5]
- S11a_two_rings s2: cuts=[(2.2, 1250, -3.0), (2.3, 3150, -3.0)] miss=[] lat=[101.2, 101.0]
- S11a_two_rings s3: cuts=[(2.25, 3150, -3.0), (2.4, 1250, -3.0)] miss=[] lat=[51.8, 97.6]
- S11b_two_rings_near_octave s1: cuts=[(2.3, 1250, -3.0), (3.7, 2500, -3.0)] miss=[] lat=[347.7, 1451.2]
- S11b_two_rings_near_octave s2: cuts=[(2.2, 1250, -3.0), (2.2, 2500, -3.0)] miss=[] lat=[201.2, 51.2]
- S11b_two_rings_near_octave s3: cuts=[(1.95, 1250, -3.0), (2.25, 2500, -3.0)] miss=[] lat=[47.6, 147.6]
- S12_acoustic_guitar_wedge_ring_196Hz s1: cuts=[(5.9, 200, -3.0)] miss=[] lat=[652.1]
- S12_acoustic_guitar_wedge_ring_196Hz s2: cuts=[(5.65, 200, -3.0)] miss=[] lat=[402.0]
- S12_acoustic_guitar_wedge_ring_196Hz s3: cuts=[(5.7, 200, -3.0)] miss=[] lat=[451.2]
- S13_slow_ring_3dB_s s1: cuts=[(8.5, 5000, -3.0)] miss=[] lat=[0.3]
- S13_slow_ring_3dB_s s2: cuts=[(9.25, 5000, -3.0)] miss=[] lat=[350.6]
- S13_slow_ring_3dB_s s3: cuts=[(9.35, 5000, -3.0)] miss=[] lat=[149.8]
- S14_ring_masked_by_cymbal s1: cuts=[(2.85, 4000, -3.0)] miss=[] lat=[148.3]
- S14_ring_masked_by_cymbal s2: cuts=[(2.85, 4000, -3.0)] miss=[] lat=[50.3]
- S14_ring_masked_by_cymbal s3: cuts=[(2.75, 4000, -3.0)] miss=[] lat=[47.7]
- S15_long_rta_decay_tails s1: cuts=[(3.75, 3150, -3.0)] miss=[] lat=[1.6]
- S15_long_rta_decay_tails s2: cuts=[(3.8, 3150, -3.0)] miss=[] lat=[102.1]
- S15_long_rta_decay_tails s3: cuts=[(3.85, 3150, -3.0)] miss=[] lat=[52.2]
- S22_speech_ringing_then_feedback s1: cuts=[(6.6, 3150, -3.0)] miss=[] lat=[50.5]
- S22_speech_ringing_then_feedback s2: cuts=[(6.5, 3150, -3.0)] miss=[] lat=[102.0]
- S22_speech_ringing_then_feedback s3: cuts=[(6.7, 3150, -3.0)] miss=[] lat=[251.4]
- M1_loud_band_wedge_ring s1: cuts=[(5.25, 3150, -3.0)] miss=[] lat=[97.5]
- M1_loud_band_wedge_ring s2: cuts=[(5.25, 3150, -3.0)] miss=[] lat=[102.0]
- M1_loud_band_wedge_ring s3: cuts=[(5.2, 3150, -3.0)] miss=[] lat=[49.3]
- M2_quiet_music_ringout_two_modes s1: cuts=[(5.2, 5000, -3.0), (9.8, 8000, -3.0), (9.85, 5000, -6.0), (15.6, 5000, -9.0)] miss=[] lat=[51.7, 2.3, -2.4, 100.9]
- M2_quiet_music_ringout_two_modes s2: cuts=[(5.25, 5000, -3.0), (9.8, 8000, -3.0), (9.85, 5000, -6.0)] miss=[] lat=[102.0, 47.2, 98.0]
- M2_quiet_music_ringout_two_modes s3: cuts=[(5.25, 5000, -3.0), (9.75, 8000, -3.0), (9.8, 5000, -6.0), (15.4, 8000, -6.0)] miss=[] lat=[47.4, -0.4, 98.6, -0.1]
- M3_jazz_trio_lav_ring_400Hz s1: cuts=[(5.05, 400, -3.0)] miss=[] lat=[-1.1]
- M3_jazz_trio_lav_ring_400Hz s2: cuts=[(5.1, 400, -3.0)] miss=[] lat=[150.1]
- M3_jazz_trio_lav_ring_400Hz s3: cuts=[(5.0, 400, -3.0)] miss=[] lat=[201.9]
- X7_plateaued_ring_under_music_from_t0 s1: cuts=[(0.2, 1600, -3.0)] miss=[] lat=[198.8]
- X7_plateaued_ring_under_music_from_t0 s2: cuts=[(0.2, 1600, -3.0)] miss=[] lat=[200.3]
- X7_plateaued_ring_under_music_from_t0 s3: cuts=[(0.2, 1600, -3.0)] miss=[] lat=[201.8]
- X8_slow_ring_midband_under_chords s1: cuts=[(7.75, 1250, -3.0)] miss=[] lat=[502.1]
- X8_slow_ring_midband_under_chords s2: cuts=[(7.3, 1250, -3.0)] miss=[] lat=[2000.0]
- X8_slow_ring_midband_under_chords s3: cuts=[(10.35, 1250, -3.0)] miss=[] lat=[2501.6]
- X9_ring_rta_midpoint_525Hz_speech s1: cuts=[(4.25, 500, -3.0)] miss=[] lat=[898.8]
- X9_ring_rta_midpoint_525Hz_speech s2: cuts=[(4.4, 500, -3.0)] miss=[] lat=[1099.7]
- X9_ring_rta_midpoint_525Hz_speech s3: cuts=[(4.85, 500, -3.0)] miss=[] lat=[1101.8]
- X10_two_rings_exact_octave s1: cuts=[(2.45, 1600, -3.0), (2.8, 3150, -3.0)] miss=[] lat=[48.5, 48.1]
- X10_two_rings_exact_octave s2: cuts=[(2.5, 1600, -3.0), (2.8, 3150, -3.0)] miss=[] lat=[102.7, 101.8]
- X10_two_rings_exact_octave s3: cuts=[(2.5, 1600, -3.0), (2.85, 3150, -3.0)] miss=[] lat=[148.2, 150.4]
- X11_amp_clipped_howl_minus12dBFS s1: cuts=[(5.05, 800, -3.0)] miss=[] lat=[848.9]
- X11_amp_clipped_howl_minus12dBFS s2: cuts=[(4.3, 800, -3.0)] miss=[] lat=[99.9]
- X11_amp_clipped_howl_minus12dBFS s3: cuts=[(5.05, 800, -3.0)] miss=[] lat=[848.8]
- X12a_master_drop20_raise_channel s1: cuts=[(0.2, 2000, -3.0)] miss=[] lat=[198.8]
- X12a_master_drop20_raise_channel s2: cuts=[(0.2, 2000, -3.0)] miss=[] lat=[200.3]
- X12a_master_drop20_raise_channel s3: cuts=[(0.2, 2000, -3.0)] miss=[] lat=[201.8]
- X12b_master_drop20_raise_busmaster s1: cuts=[(0.2, 2000, -3.0)] miss=[] lat=[198.8]
- X12b_master_drop20_raise_busmaster s2: cuts=[(0.2, 2000, -3.0)] miss=[] lat=[200.3]
- X12b_master_drop20_raise_busmaster s3: cuts=[(0.2, 2000, -3.0)] miss=[] lat=[201.8]
- X13_decay16_jazz_lav_ring s1: cuts=[(5.15, 400, -3.0)] miss=[] lat=[0.6]
- X13_decay16_jazz_lav_ring s2: cuts=[(5.2, 400, -3.0)] miss=[] lat=[2.2]
- X13_decay16_jazz_lav_ring s3: cuts=[(5.1, 400, -3.0)] miss=[] lat=[101.6]
- X14_peakhold_loud_band_wedge_ring s1: cuts=[(5.35, 3150, -3.0)] miss=[] lat=[202.3]
- X14_peakhold_loud_band_wedge_ring s2: cuts=[(5.25, 3150, -3.0)] miss=[] lat=[52.0]
- X14_peakhold_loud_band_wedge_ring s3: cuts=[(5.35, 3150, -3.0)] miss=[] lat=[200.1]
- X16_wedge_ring_315Hz_loud_band s1: cuts=[(6.25, 315, -3.0)] miss=[] lat=[152.6]
- X16_wedge_ring_315Hz_loud_band s2: cuts=[(6.3, 315, -3.0)] miss=[] lat=[203.0]
- X16_wedge_ring_315Hz_loud_band s3: cuts=[(6.2, 315, -3.0)] miss=[] lat=[100.7]
- X17_ring_122Hz_acoustic_guitar_body s1: cuts=[(6.9, 125, -3.0)] miss=[] lat=[201.7]
- X17_ring_122Hz_acoustic_guitar_body s2: cuts=[(6.8, 125, -3.0)] miss=[] lat=[399.5]
- X17_ring_122Hz_acoustic_guitar_body s3: cuts=[(6.45, 125, -3.0)] miss=[] lat=[52.2]
- X19_handheld_ring_stalls_and_hops s1: cuts=[(2.45, 2500, -3.0)] miss=[] lat=[248.5]
- X19_handheld_ring_stalls_and_hops s2: cuts=[(2.85, 2500, -3.0)] miss=[] lat=[400.3]
- X19_handheld_ring_stalls_and_hops s3: cuts=[(2.65, 2500, -3.0)] miss=[] lat=[51.3]
- X21_reverberant_area_mic_slow_ring s1: cuts=[(4.2, 630, -3.0)] miss=[] lat=[298.2]
- X21_reverberant_area_mic_slow_ring s2: cuts=[(4.5, 630, -3.0)] miss=[] lat=[298.2]
- X21_reverberant_area_mic_slow_ring s3: cuts=[(4.8, 630, -3.0)] miss=[] lat=[602.7]
- X22_kick_mic_sub_ring_65Hz s1: cuts=[(6.45, 63, -3.0)] miss=[] lat=[147.6]
- X22_kick_mic_sub_ring_65Hz s2: cuts=[(6.4, 63, -3.0)] miss=[] lat=[302.7]
- X22_kick_mic_sub_ring_65Hz s3: cuts=[(6.4, 63, -3.0)] miss=[] lat=[400.8]
- X23_ringout_quiet_room_two_modes s1: cuts=[(7.85, 630, -3.0), (10.7, 2500, -3.0), (13.8, 630, -6.0)] miss=[] lat=[201.4, 99.8]
- X23_ringout_quiet_room_two_modes s2: cuts=[(7.85, 630, -3.0), (10.65, 2500, -3.0), (13.7, 630, -6.0)] miss=[] lat=[197.8, 50.6]
- X23_ringout_quiet_room_two_modes s3: cuts=[(8.15, 630, -3.0), (10.8, 2500, -3.0), (13.7, 630, -6.0)] miss=[] lat=[99.9, 101.5]

</details>

### blind open loop

| scenario | ev | TP | miss | FP | early | tail | harm | cuts | lat ms min/med/max | budget | FP at GEQ (Hz) | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C0_silent_room | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| C1_music_bed_drums | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S1_bass_under_quiet_music | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S2a_established_ring_8k | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 199/200/202 | 300 |  | PASS 3/3 |
| S2b_established_ring_8k_steep | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 199/200/202 | 300 |  | PASS 3/3 |
| S2c_established_clipped_2k4 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 98/100/100 | 300 |  | PASS 3/3 |
| S3_ring_during_music | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 1/102/352 | 300 |  | FAIL 2/3 |
| S4a_vocal_vibrato | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S4b_vocal_vibrato_band_edge | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S5_guitar_note_decays_to_sine | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S6_master_ramp_feedback_watch | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S6b_ringout_steps_latent_loop | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 98/99/202 | 300 |  | PASS 3/3 |
| S7_808_sub_bassline | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S8a_organ_melody | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S8b_flute_held_note | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S8c_whistle | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S9_clipped_howl_fast | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 49/99/150 | 300 |  | PASS 3/3 |
| S10_ring_between_bands | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 148/200/251 | 300 |  | PASS 3/3 |
| S11a_two_rings | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 52/100/202 | 300 |  | PASS 3/3 |
| S11b_two_rings_near_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 48/174/447 | 300 |  | FAIL 2/3 |
| S12_acoustic_guitar_wedge_ring_196Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 402/451/652 | 600 |  | FAIL 2/3 |
| S13_slow_ring_3dB_s | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0/150/351 | 1000 |  | PASS 3/3 |
| S14_ring_masked_by_cymbal | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 48/50/148 | 300 |  | PASS 3/3 |
| S15_long_rta_decay_tails | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 2/52/102 | 300 |  | PASS 3/3 |
| S16_peak_hold_on | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S17_kick_pattern | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S18_vibrato_on_band_edge | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S19_driven_room_mode | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S20_song_start_stop_crowd | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S21_synth_pad_swell | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S22_speech_ringing_then_feedback | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 50/102/251 | 300 |  | PASS 3/3 |
| S23a_autogain_drift | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S23b_gain_offset_clip | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S24_bells_triangle_glock | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| M1_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 49/98/102 | 300 |  | PASS 3/3 |
| M2_quiet_music_ringout_two_modes | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 47/127/201 | 300 |  | PASS 3/3 |
| M3_jazz_trio_lav_ring_400Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | -1/150/202 | 300 |  | PASS 3/3 |
| X1_organ_held_notes | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X2_flute_held_vibrato | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X3_whistle_held_drift | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X4_sine_lead_portamento | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X5_808_bassline_40_60Hz | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X6_soprano_closed_vowel_band_edge | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X7_plateaued_ring_under_music_from_t0 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 199/200/202 | 1000 |  | PASS 3/3 |
| X8_slow_ring_midband_under_chords | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 502/2000/2502 | 1000 |  | FAIL 1/3 |
| X9_ring_rta_midpoint_525Hz_speech | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 899/1100/1102 | 300 |  | FAIL 0/3 |
| X10_two_rings_exact_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 48/125/298 | 300 |  | PASS 3/3 |
| X11_amp_clipped_howl_minus12dBFS | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 100/849/849 | 300 |  | FAIL 1/3 |
| X12a_master_drop20_raise_channel | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 152/200/202 | 300 |  | PASS 3/3 |
| X12b_master_drop20_raise_busmaster | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 99/175/202 | 300 |  | PASS 3/3 |
| X13_decay16_jazz_lav_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 1/2/102 | 600 |  | PASS 3/3 |
| X14_peakhold_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 52/200/202 | 300 |  | PASS 3/3 |
| X15_kick_bass_unison_55Hz | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X16_wedge_ring_315Hz_loud_band | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 101/153/203 | 300 |  | PASS 3/3 |
| X17_ring_122Hz_acoustic_guitar_body | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | – | 600 |  | FAIL 0/3 |
| X18_applause_crowd_30s | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X19_handheld_ring_stalls_and_hops | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 51/248/400 | 300 |  | FAIL 2/3 |
| X20_mains_hum_and_hvac_whine | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X21_reverberant_area_mic_slow_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 298/298/603 | 600 |  | FAIL 2/3 |
| X22_kick_mic_sub_ring_65Hz | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | – | 1000 |  | FAIL 0/3 |
| X23_ringout_quiet_room_two_modes | 6 | 6 | 0 | 0 | 2 | 0 | 0 | 0 | -1152/75/298 | 300 |  | PASS 3/3 |
| **61 scenarios, 51 pass** | 117 | 111 | 6 | 0 | 2 | 0 | 0 | | | | | wall 13.7 s |

### blind closed loop

| scenario | ev | TP | miss | FP | early | tail | harm | cuts | lat ms min/med/max | budget | FP at GEQ (Hz) | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S2a_established_ring_8k | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 199/200/202 | 300 |  | PASS 3/3 |
| S2b_established_ring_8k_steep | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 199/200/202 | 300 |  | PASS 3/3 |
| S2c_established_clipped_2k4 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 98/100/100 | 300 |  | PASS 3/3 |
| S3_ring_during_music | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 1/102/352 | 300 |  | FAIL 2/3 |
| S6b_ringout_steps_latent_loop | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 98/99/202 | 300 |  | PASS 3/3 |
| S9_clipped_howl_fast | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 49/99/150 | 300 |  | PASS 3/3 |
| S10_ring_between_bands | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 148/200/251 | 300 |  | PASS 3/3 |
| S11a_two_rings | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 6 | 52/100/202 | 300 |  | PASS 3/3 |
| S11b_two_rings_near_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 6 | 48/174/1451 | 300 |  | FAIL 2/3 |
| S12_acoustic_guitar_wedge_ring_196Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 402/451/652 | 600 |  | FAIL 2/3 |
| S13_slow_ring_3dB_s | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 0/150/351 | 1000 |  | PASS 3/3 |
| S14_ring_masked_by_cymbal | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 48/50/148 | 300 |  | PASS 3/3 |
| S15_long_rta_decay_tails | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 2/52/102 | 300 |  | PASS 3/3 |
| S22_speech_ringing_then_feedback | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 50/102/251 | 300 |  | PASS 3/3 |
| M1_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 49/98/102 | 300 |  | PASS 3/3 |
| M2_quiet_music_ringout_two_modes | 11 | 11 | 0 | 0 | 0 | 0 | 0 | 11 | -2/47/102 | 300 |  | PASS 3/3 |
| M3_jazz_trio_lav_ring_400Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | -1/150/202 | 300 |  | PASS 3/3 |
| X7_plateaued_ring_under_music_from_t0 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 199/200/202 | 1000 |  | PASS 3/3 |
| X8_slow_ring_midband_under_chords | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 502/2000/2502 | 1000 |  | FAIL 1/3 |
| X9_ring_rta_midpoint_525Hz_speech | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 899/1100/1102 | 300 |  | FAIL 0/3 |
| X10_two_rings_exact_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 6 | 48/102/150 | 300 |  | PASS 3/3 |
| X11_amp_clipped_howl_minus12dBFS | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 100/849/849 | 300 |  | FAIL 1/3 |
| X12a_master_drop20_raise_channel | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 199/200/202 | 300 |  | PASS 3/3 |
| X12b_master_drop20_raise_busmaster | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 199/200/202 | 300 |  | PASS 3/3 |
| X13_decay16_jazz_lav_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 1/2/102 | 600 |  | PASS 3/3 |
| X14_peakhold_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 52/200/202 | 300 |  | PASS 3/3 |
| X16_wedge_ring_315Hz_loud_band | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 101/153/203 | 300 |  | PASS 3/3 |
| X17_ring_122Hz_acoustic_guitar_body | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | – | 600 |  | FAIL 0/3 |
| X19_handheld_ring_stalls_and_hops | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 51/248/400 | 300 |  | FAIL 2/3 |
| X21_reverberant_area_mic_slow_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 298/298/603 | 600 |  | FAIL 2/3 |
| X22_kick_mic_sub_ring_65Hz | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | – | 1000 |  | FAIL 0/3 |
| X23_ringout_quiet_room_two_modes | 7 | 7 | 0 | 0 | 2 | 0 | 0 | 9 | 51/102/300 | 300 |  | FAIL 2/3 |
| **32 scenarios, 21 pass** | 117 | 111 | 6 | 0 | 2 | 0 | 0 | | | | | wall 22.0 s |

<details><summary>cuts per run</summary>

- S2a_established_ring_8k s1: cuts=[(0.2, 8000, -3.0)] miss=[] lat=[198.8]
- S2a_established_ring_8k s2: cuts=[(0.2, 8000, -3.0)] miss=[] lat=[200.3]
- S2a_established_ring_8k s3: cuts=[(0.2, 8000, -3.0)] miss=[] lat=[201.8]
- S2b_established_ring_8k_steep s1: cuts=[(0.2, 8000, -3.0)] miss=[] lat=[198.8]
- S2b_established_ring_8k_steep s2: cuts=[(0.2, 8000, -3.0)] miss=[] lat=[200.3]
- S2b_established_ring_8k_steep s3: cuts=[(0.2, 8000, -3.0)] miss=[] lat=[201.8]
- S2c_established_clipped_2k4 s1: cuts=[(0.1, 2500, -3.0)] miss=[] lat=[100.1]
- S2c_established_clipped_2k4 s2: cuts=[(0.1, 2500, -3.0)] miss=[] lat=[97.6]
- S2c_established_clipped_2k4 s3: cuts=[(0.1, 2500, -3.0)] miss=[] lat=[100.2]
- S3_ring_during_music s1: cuts=[(5.2, 3150, -3.0)] miss=[] lat=[351.7]
- S3_ring_during_music s2: cuts=[(4.95, 3150, -3.0)] miss=[] lat=[0.6]
- S3_ring_during_music s3: cuts=[(4.85, 3150, -3.0)] miss=[] lat=[101.8]
- S6b_ringout_steps_latent_loop s1: cuts=[(12.75, 2500, -3.0)] miss=[] lat=[97.7]
- S6b_ringout_steps_latent_loop s2: cuts=[(12.75, 2500, -3.0)] miss=[] lat=[201.6]
- S6b_ringout_steps_latent_loop s3: cuts=[(12.75, 2500, -3.0)] miss=[] lat=[99.3]
- S9_clipped_howl_fast s1: cuts=[(2.35, 2000, -3.0)] miss=[] lat=[98.7]
- S9_clipped_howl_fast s2: cuts=[(2.4, 2000, -3.0)] miss=[] lat=[150.2]
- S9_clipped_howl_fast s3: cuts=[(2.3, 2000, -3.0)] miss=[] lat=[49.4]
- S10_ring_between_bands s1: cuts=[(1.9, 2500, -3.0)] miss=[] lat=[251.3]
- S10_ring_between_bands s2: cuts=[(1.75, 2500, -3.0)] miss=[] lat=[200.4]
- S10_ring_between_bands s3: cuts=[(1.8, 2500, -3.0)] miss=[] lat=[147.7]
- S11a_two_rings s1: cuts=[(2.45, 3150, -3.0), (2.5, 1250, -3.0)] miss=[] lat=[202.5, 98.5]
- S11a_two_rings s2: cuts=[(2.2, 1250, -3.0), (2.3, 3150, -3.0)] miss=[] lat=[101.2, 101.0]
- S11a_two_rings s3: cuts=[(2.25, 3150, -3.0), (2.4, 1250, -3.0)] miss=[] lat=[51.8, 97.6]
- S11b_two_rings_near_octave s1: cuts=[(2.3, 1250, -3.0), (3.7, 2500, -3.0)] miss=[] lat=[347.7, 1451.2]
- S11b_two_rings_near_octave s2: cuts=[(2.2, 1250, -3.0), (2.2, 2500, -3.0)] miss=[] lat=[201.2, 51.2]
- S11b_two_rings_near_octave s3: cuts=[(1.95, 1250, -3.0), (2.25, 2500, -3.0)] miss=[] lat=[47.6, 147.6]
- S12_acoustic_guitar_wedge_ring_196Hz s1: cuts=[(5.9, 200, -3.0)] miss=[] lat=[652.1]
- S12_acoustic_guitar_wedge_ring_196Hz s2: cuts=[(5.65, 200, -3.0)] miss=[] lat=[402.0]
- S12_acoustic_guitar_wedge_ring_196Hz s3: cuts=[(5.7, 200, -3.0)] miss=[] lat=[451.2]
- S13_slow_ring_3dB_s s1: cuts=[(8.5, 5000, -3.0)] miss=[] lat=[0.3]
- S13_slow_ring_3dB_s s2: cuts=[(9.25, 5000, -3.0)] miss=[] lat=[350.6]
- S13_slow_ring_3dB_s s3: cuts=[(9.35, 5000, -3.0)] miss=[] lat=[149.8]
- S14_ring_masked_by_cymbal s1: cuts=[(2.85, 4000, -3.0)] miss=[] lat=[148.3]
- S14_ring_masked_by_cymbal s2: cuts=[(2.85, 4000, -3.0)] miss=[] lat=[50.3]
- S14_ring_masked_by_cymbal s3: cuts=[(2.75, 4000, -3.0)] miss=[] lat=[47.7]
- S15_long_rta_decay_tails s1: cuts=[(3.75, 3150, -3.0)] miss=[] lat=[1.6]
- S15_long_rta_decay_tails s2: cuts=[(3.8, 3150, -3.0)] miss=[] lat=[102.1]
- S15_long_rta_decay_tails s3: cuts=[(3.85, 3150, -3.0)] miss=[] lat=[52.2]
- S22_speech_ringing_then_feedback s1: cuts=[(6.6, 3150, -3.0)] miss=[] lat=[50.5]
- S22_speech_ringing_then_feedback s2: cuts=[(6.5, 3150, -3.0)] miss=[] lat=[102.0]
- S22_speech_ringing_then_feedback s3: cuts=[(6.7, 3150, -3.0)] miss=[] lat=[251.4]
- M1_loud_band_wedge_ring s1: cuts=[(5.25, 3150, -3.0)] miss=[] lat=[97.5]
- M1_loud_band_wedge_ring s2: cuts=[(5.25, 3150, -3.0)] miss=[] lat=[102.0]
- M1_loud_band_wedge_ring s3: cuts=[(5.2, 3150, -3.0)] miss=[] lat=[49.3]
- M2_quiet_music_ringout_two_modes s1: cuts=[(5.2, 5000, -3.0), (9.8, 8000, -3.0), (9.85, 5000, -6.0), (15.6, 5000, -9.0)] miss=[] lat=[51.7, 2.3, -2.4, 100.9]
- M2_quiet_music_ringout_two_modes s2: cuts=[(5.25, 5000, -3.0), (9.8, 8000, -3.0), (9.85, 5000, -6.0)] miss=[] lat=[102.0, 47.2, 98.0]
- M2_quiet_music_ringout_two_modes s3: cuts=[(5.25, 5000, -3.0), (9.75, 8000, -3.0), (9.8, 5000, -6.0), (15.4, 8000, -6.0)] miss=[] lat=[47.4, -0.4, 98.6, -0.1]
- M3_jazz_trio_lav_ring_400Hz s1: cuts=[(5.05, 400, -3.0)] miss=[] lat=[-1.1]
- M3_jazz_trio_lav_ring_400Hz s2: cuts=[(5.1, 400, -3.0)] miss=[] lat=[150.1]
- M3_jazz_trio_lav_ring_400Hz s3: cuts=[(5.0, 400, -3.0)] miss=[] lat=[201.9]
- X7_plateaued_ring_under_music_from_t0 s1: cuts=[(0.2, 1600, -3.0)] miss=[] lat=[198.8]
- X7_plateaued_ring_under_music_from_t0 s2: cuts=[(0.2, 1600, -3.0)] miss=[] lat=[200.3]
- X7_plateaued_ring_under_music_from_t0 s3: cuts=[(0.2, 1600, -3.0)] miss=[] lat=[201.8]
- X8_slow_ring_midband_under_chords s1: cuts=[(7.75, 1250, -3.0)] miss=[] lat=[502.1]
- X8_slow_ring_midband_under_chords s2: cuts=[(7.3, 1250, -3.0)] miss=[] lat=[2000.0]
- X8_slow_ring_midband_under_chords s3: cuts=[(10.35, 1250, -3.0)] miss=[] lat=[2501.6]
- X9_ring_rta_midpoint_525Hz_speech s1: cuts=[(4.25, 500, -3.0)] miss=[] lat=[898.8]
- X9_ring_rta_midpoint_525Hz_speech s2: cuts=[(4.4, 500, -3.0)] miss=[] lat=[1099.7]
- X9_ring_rta_midpoint_525Hz_speech s3: cuts=[(4.85, 500, -3.0)] miss=[] lat=[1101.8]
- X10_two_rings_exact_octave s1: cuts=[(2.45, 1600, -3.0), (2.8, 3150, -3.0)] miss=[] lat=[48.5, 48.1]
- X10_two_rings_exact_octave s2: cuts=[(2.5, 1600, -3.0), (2.8, 3150, -3.0)] miss=[] lat=[102.7, 101.8]
- X10_two_rings_exact_octave s3: cuts=[(2.5, 1600, -3.0), (2.85, 3150, -3.0)] miss=[] lat=[148.2, 150.4]
- X11_amp_clipped_howl_minus12dBFS s1: cuts=[(5.05, 800, -3.0)] miss=[] lat=[848.9]
- X11_amp_clipped_howl_minus12dBFS s2: cuts=[(4.3, 800, -3.0)] miss=[] lat=[99.9]
- X11_amp_clipped_howl_minus12dBFS s3: cuts=[(5.05, 800, -3.0)] miss=[] lat=[848.8]
- X12a_master_drop20_raise_channel s1: cuts=[(0.2, 2000, -3.0)] miss=[] lat=[198.8]
- X12a_master_drop20_raise_channel s2: cuts=[(0.2, 2000, -3.0)] miss=[] lat=[200.3]
- X12a_master_drop20_raise_channel s3: cuts=[(0.2, 2000, -3.0)] miss=[] lat=[201.8]
- X12b_master_drop20_raise_busmaster s1: cuts=[(0.2, 2000, -3.0)] miss=[] lat=[198.8]
- X12b_master_drop20_raise_busmaster s2: cuts=[(0.2, 2000, -3.0)] miss=[] lat=[200.3]
- X12b_master_drop20_raise_busmaster s3: cuts=[(0.2, 2000, -3.0)] miss=[] lat=[201.8]
- X13_decay16_jazz_lav_ring s1: cuts=[(5.15, 400, -3.0)] miss=[] lat=[0.6]
- X13_decay16_jazz_lav_ring s2: cuts=[(5.2, 400, -3.0)] miss=[] lat=[2.2]
- X13_decay16_jazz_lav_ring s3: cuts=[(5.1, 400, -3.0)] miss=[] lat=[101.6]
- X14_peakhold_loud_band_wedge_ring s1: cuts=[(5.35, 3150, -3.0)] miss=[] lat=[202.3]
- X14_peakhold_loud_band_wedge_ring s2: cuts=[(5.25, 3150, -3.0)] miss=[] lat=[52.0]
- X14_peakhold_loud_band_wedge_ring s3: cuts=[(5.35, 3150, -3.0)] miss=[] lat=[200.1]
- X16_wedge_ring_315Hz_loud_band s1: cuts=[(6.25, 315, -3.0)] miss=[] lat=[152.6]
- X16_wedge_ring_315Hz_loud_band s2: cuts=[(6.3, 315, -3.0)] miss=[] lat=[203.0]
- X16_wedge_ring_315Hz_loud_band s3: cuts=[(6.2, 315, -3.0)] miss=[] lat=[100.7]
- X17_ring_122Hz_acoustic_guitar_body s1: cuts=[] miss=[121] lat=[]
- X17_ring_122Hz_acoustic_guitar_body s2: cuts=[] miss=[121] lat=[]
- X17_ring_122Hz_acoustic_guitar_body s3: cuts=[] miss=[121] lat=[]
- X19_handheld_ring_stalls_and_hops s1: cuts=[(2.45, 2500, -3.0)] miss=[] lat=[248.5]
- X19_handheld_ring_stalls_and_hops s2: cuts=[(2.85, 2500, -3.0)] miss=[] lat=[400.3]
- X19_handheld_ring_stalls_and_hops s3: cuts=[(2.65, 2500, -3.0)] miss=[] lat=[51.3]
- X21_reverberant_area_mic_slow_ring s1: cuts=[(4.2, 630, -3.0)] miss=[] lat=[298.2]
- X21_reverberant_area_mic_slow_ring s2: cuts=[(4.5, 630, -3.0)] miss=[] lat=[298.2]
- X21_reverberant_area_mic_slow_ring s3: cuts=[(4.8, 630, -3.0)] miss=[] lat=[602.7]
- X22_kick_mic_sub_ring_65Hz s1: cuts=[] miss=[65] lat=[]
- X22_kick_mic_sub_ring_65Hz s2: cuts=[] miss=[65] lat=[]
- X22_kick_mic_sub_ring_65Hz s3: cuts=[] miss=[65] lat=[]
- X23_ringout_quiet_room_two_modes s1: cuts=[(7.85, 630, -3.0), (10.7, 2500, -3.0), (13.8, 630, -6.0)] miss=[] lat=[201.4, 99.8]
- X23_ringout_quiet_room_two_modes s2: cuts=[(7.85, 630, -3.0), (10.65, 2500, -3.0), (13.8, 630, -6.0)] miss=[] lat=[300.3, 50.6]
- X23_ringout_quiet_room_two_modes s3: cuts=[(9.3, 630, -3.0), (10.8, 2500, -3.0), (13.7, 630, -6.0)] miss=[] lat=[298.3, 99.9, 101.5]

</details>
