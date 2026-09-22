# Current-detector baselines over the extended corpus (corpus-critic)

generated 2026-09-21 12:46:51 — seeds [1, 2, 3]; detector config from device.yaml: prominence 12.0/±3, min_level -45.0, persistence 3, growth 6.0..60.0 ref 20.0, weights 0.3/0.2/0.5, threshold 0.7, override 25.0 dB / 6 fr, budget 6

### current FeedbackDetector (device.yaml), open loop

| scenario | ev | TP | miss | FP | early | tail | harm | cuts | lat ms min/med/max | budget | FP at GEQ (Hz) | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C0_silent_room | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| C1_music_bed_drums | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S1_bass_under_quiet_music | 0 | 0 | 0 | 32 | 0 | 0 | 0 | 0 | – | 300 | 80,100,160 | FAIL 0/3 |
| S2a_established_ring_8k | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 251/252/253 | 300 |  | PASS 3/3 |
| S2b_established_ring_8k_steep | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 251/252/253 | 300 |  | PASS 3/3 |
| S2c_established_clipped_2k4 | 3 | 3 | 0 | 0 | 0 | 0 | 96 | 0 | 251/252/253 | 300 |  | PASS 3/3 |
| S3_ring_during_music | 3 | 3 | 0 | 5 | 0 | 0 | 0 | 0 | 148/502/602 | 300 | 630,800,1000 | FAIL 1/3 |
| S4a_vocal_vibrato | 0 | 0 | 0 | 40 | 0 | 0 | 0 | 0 | – | 300 | 400,500,800,1000,1250,1600 | FAIL 0/3 |
| S4b_vocal_vibrato_band_edge | 0 | 0 | 0 | 31 | 0 | 0 | 0 | 0 | – | 300 | 400,500,800,1000,1250,1600 | FAIL 0/3 |
| S5_guitar_note_decays_to_sine | 0 | 0 | 0 | 13 | 0 | 0 | 0 | 0 | – | 300 | 160,315,500,630 | FAIL 0/3 |
| S6_master_ramp_feedback_watch | 0 | 0 | 0 | 7 | 0 | 0 | 0 | 0 | – | 300 | 200,315,400 | FAIL 0/3 |
| S6b_ringout_steps_latent_loop | 3 | 3 | 0 | 2 | 0 | 0 | 0 | 0 | 147/148/202 | 300 | 125,200 | FAIL 2/3 |
| S7_808_sub_bassline | 0 | 0 | 0 | 19 | 0 | 0 | 0 | 0 | – | 300 | 50,63,80 | FAIL 0/3 |
| S8a_organ_melody | 0 | 0 | 0 | 13 | 0 | 0 | 0 | 0 | – | 300 | 630,800,1000 | FAIL 0/3 |
| S8b_flute_held_note | 0 | 0 | 0 | 20 | 0 | 0 | 0 | 0 | – | 300 | 630,800,1000,1250,1600 | FAIL 0/3 |
| S8c_whistle | 0 | 0 | 0 | 28 | 0 | 0 | 0 | 0 | – | 300 | 1250,1600,2000 | FAIL 0/3 |
| S9_clipped_howl_fast | 3 | 3 | 0 | 0 | 0 | 0 | 47 | 0 | 248/252/253 | 300 |  | PASS 3/3 |
| S10_ring_between_bands | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 502/699/798 | 300 |  | FAIL 0/3 |
| S11a_two_rings | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 301/376/549 | 300 |  | FAIL 0/3 |
| S11b_two_rings_near_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 202/300/353 | 300 |  | FAIL 1/3 |
| S12_acoustic_guitar_wedge_ring_196Hz | 3 | 3 | 0 | 5 | 5 | 0 | 0 | 0 | 302/349/398 | 600 | 100,160 | FAIL 1/3 |
| S13_slow_ring_3dB_s | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | – | 1000 |  | FAIL 0/3 |
| S14_ring_masked_by_cymbal | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 198/198/200 | 300 |  | PASS 3/3 |
| S15_long_rta_decay_tails | 3 | 3 | 0 | 1 | 0 | 21 | 0 | 0 | 198/202/298 | 300 | 50 | FAIL 2/3 |
| S16_peak_hold_on | 0 | 0 | 0 | 12 | 0 | 0 | 0 | 0 | – | 300 | 400,630,800,1000,1250 | FAIL 0/3 |
| S17_kick_pattern | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S18_vibrato_on_band_edge | 0 | 0 | 0 | 9 | 0 | 0 | 0 | 0 | – | 300 | 800,1600 | FAIL 0/3 |
| S19_driven_room_mode | 0 | 0 | 0 | 40 | 0 | 0 | 0 | 0 | – | 300 | 40,80,100 | FAIL 0/3 |
| S20_song_start_stop_crowd | 0 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | – | 300 | 1600,2500 | FAIL 0/3 |
| S21_synth_pad_swell | 0 | 0 | 0 | 66 | 0 | 0 | 0 | 0 | – | 300 | 100,125,160,200,250,315,400 | FAIL 0/3 |
| S22_speech_ringing_then_feedback | 3 | 3 | 0 | 98 | 0 | 0 | 0 | 0 | 97/102/151 | 300 | 125,160,250,315,400,500,630 | FAIL 0/3 |
| S23a_autogain_drift | 0 | 0 | 0 | 18 | 0 | 0 | 0 | 0 | – | 300 | 80,100,160 | FAIL 0/3 |
| S23b_gain_offset_clip | 0 | 0 | 0 | 16 | 0 | 0 | 0 | 0 | – | 300 | 50,63,80 | FAIL 0/3 |
| S24_bells_triangle_glock | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | – | 300 | 2000,5000 | FAIL 0/3 |
| M1_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 248/251/252 | 300 |  | PASS 3/3 |
| M2_quiet_music_ringout_two_modes | 6 | 6 | 0 | 8 | 0 | 0 | 0 | 0 | 98/201/301 | 300 | 630,800 | FAIL 0/3 |
| M3_jazz_trio_lav_ring_400Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 202/302/398 | 300 |  | FAIL 1/3 |
| X1_organ_held_notes | 0 | 0 | 0 | 9 | 0 | 0 | 0 | 0 | – | 300 | 500,630 | FAIL 1/3 |
| X2_flute_held_vibrato | 0 | 0 | 0 | 20 | 0 | 0 | 0 | 0 | – | 300 | 800,1250 | FAIL 0/3 |
| X3_whistle_held_drift | 0 | 0 | 0 | 21 | 0 | 0 | 0 | 0 | – | 300 | 1600,2500 | FAIL 0/3 |
| X4_sine_lead_portamento | 0 | 0 | 0 | 26 | 0 | 0 | 0 | 0 | – | 300 | 800,1000,1250 | FAIL 0/3 |
| X5_808_bassline_40_60Hz | 0 | 0 | 0 | 32 | 0 | 0 | 0 | 0 | – | 300 | 40,50,63 | FAIL 0/3 |
| X6_soprano_closed_vowel_band_edge | 0 | 0 | 0 | 7 | 0 | 0 | 0 | 0 | – | 300 | 630,800,1250 | FAIL 0/3 |
| X7_plateaued_ring_under_music_from_t0 | 3 | 0 | 3 | 4 | 0 | 0 | 0 | 0 | – | 1000 | 630,800 | FAIL 0/3 |
| X8_slow_ring_midband_under_chords | 3 | 3 | 0 | 11 | 0 | 0 | 0 | 0 | 9952/10648/12649 | 1000 | 500,630,800,1000,2000,3150 | FAIL 0/3 |
| X9_ring_rta_midpoint_525Hz_speech | 3 | 3 | 0 | 95 | 0 | 0 | 0 | 0 | -200/151/902 | 300 | 125,160,250,315,400,500,630 | FAIL 0/3 |
| X10_two_rings_exact_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 150/224/303 | 300 |  | FAIL 2/3 |
| X11_amp_clipped_howl_minus12dBFS | 3 | 3 | 0 | 2 | 0 | 0 | 0 | 0 | 100/152/1649 | 300 | 630,1250 | FAIL 1/3 |
| X12a_master_drop20_raise_channel | 6 | 6 | 0 | 5 | 0 | 0 | 0 | 0 | 251/253/301 | 300 | 200,630,1250 | FAIL 0/3 |
| X12b_master_drop20_raise_busmaster | 6 | 6 | 0 | 4 | 0 | 0 | 0 | 0 | 249/251/253 | 300 | 160 | FAIL 1/3 |
| X13_decay16_jazz_lav_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 298/352/499 | 600 |  | PASS 3/3 |
| X14_peakhold_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 248/250/251 | 300 |  | PASS 3/3 |
| X15_kick_bass_unison_55Hz | 0 | 0 | 0 | 41 | 0 | 0 | 0 | 0 | – | 300 | 80,100,160,200 | FAIL 0/3 |
| X16_wedge_ring_315Hz_loud_band | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 98/101/103 | 300 |  | PASS 3/3 |
| X17_ring_122Hz_acoustic_guitar_body | 3 | 3 | 0 | 18 | 3 | 0 | 0 | 0 | 103/148/348 | 600 | 80,160,200,250,315 | FAIL 0/3 |
| X18_applause_crowd_30s | 0 | 0 | 0 | 35 | 0 | 0 | 0 | 0 | – | 300 | 400,500,630,800,1000,1250,1600,2000,… | FAIL 0/3 |
| X19_handheld_ring_stalls_and_hops | 3 | 3 | 0 | 54 | 0 | 0 | 0 | 0 | 103/252/497 | 300 | 315,400,500,630,800,1000,1250,1600,… | FAIL 0/3 |
| X20_mains_hum_and_hvac_whine | 0 | 0 | 0 | 55 | 0 | 0 | 0 | 0 | – | 300 | 125,160,250,315,400,500 | FAIL 0/3 |
| X21_reverberant_area_mic_slow_ring | 3 | 0 | 3 | 8 | 0 | 0 | 0 | 0 | – | 600 | 200,315,400 | FAIL 0/3 |
| X22_kick_mic_sub_ring_65Hz | 3 | 3 | 0 | 4 | 1 | 0 | 0 | 0 | 99/103/103 | 1000 | 100,200 | FAIL 1/3 |
| X23_ringout_quiet_room_two_modes | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 303/501/852 | 300 |  | FAIL 0/3 |
| **61 scenarios, 12 pass** | 117 | 108 | 9 | 948 | 9 | 21 | 143 | | | | | wall 23.1 s |

### current FeedbackDetector (device.yaml), closed loop (NotchController, 1-frame actuation delay)

| scenario | ev | TP | miss | FP | early | tail | harm | cuts | lat ms min/med/max | budget | FP at GEQ (Hz) | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S2a_established_ring_8k | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 251/252/253 | 300 |  | PASS 3/3 |
| S2b_established_ring_8k_steep | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 251/252/253 | 300 |  | PASS 3/3 |
| S2c_established_clipped_2k4 | 3 | 3 | 0 | 0 | 0 | 0 | 12 | 15 | 251/252/253 | 300 |  | PASS 3/3 |
| S3_ring_during_music | 3 | 3 | 0 | 4 | 0 | 0 | 0 | 7 | 148/150/502 | 300 | 630,800,1000 | FAIL 1/3 |
| S6b_ringout_steps_latent_loop | 3 | 3 | 0 | 2 | 0 | 0 | 0 | 5 | 147/148/252 | 300 | 125,200 | FAIL 2/3 |
| S9_clipped_howl_fast | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 248/252/253 | 300 |  | PASS 3/3 |
| S10_ring_between_bands | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 502/699/798 | 300 |  | FAIL 0/3 |
| S11a_two_rings | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 6 | 302/450/1200 | 300 |  | FAIL 0/3 |
| S11b_two_rings_near_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 6 | 202/373/1897 | 300 |  | FAIL 0/3 |
| S12_acoustic_guitar_wedge_ring_196Hz | 0 | 0 | 0 | 4 | 4 | 0 | 0 | 8 | – | 600 | 100,160,315 | FAIL 1/3 |
| S13_slow_ring_3dB_s | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | – | 1000 |  | FAIL 0/3 |
| S14_ring_masked_by_cymbal | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 198/198/200 | 300 |  | PASS 3/3 |
| S15_long_rta_decay_tails | 3 | 3 | 0 | 1 | 0 | 0 | 0 | 4 | 198/202/298 | 300 | 50 | FAIL 2/3 |
| S22_speech_ringing_then_feedback | 3 | 3 | 0 | 75 | 0 | 0 | 0 | 47 | 102/150/349 | 300 | 125,160,200,250,315,400,500,630 | FAIL 0/3 |
| M1_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 248/251/252 | 300 |  | PASS 3/3 |
| M2_quiet_music_ringout_two_modes | 11 | 11 | 0 | 7 | 0 | 0 | 0 | 17 | 98/101/152 | 300 | 630,800 | FAIL 0/3 |
| M3_jazz_trio_lav_ring_400Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 202/302/398 | 300 |  | FAIL 1/3 |
| X7_plateaued_ring_under_music_from_t0 | 3 | 0 | 3 | 4 | 0 | 0 | 0 | 4 | – | 1000 | 630,800 | FAIL 0/3 |
| X8_slow_ring_midband_under_chords | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 8 | – | 1000 | 630,800,1000,2000,3150 | FAIL 0/3 |
| X9_ring_rta_midpoint_525Hz_speech | 0 | 0 | 0 | 73 | 9 | 0 | 0 | 50 | – | 300 | 125,160,200,250,315,400,500,630,… | FAIL 0/3 |
| X10_two_rings_exact_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 6 | 148/150/201 | 300 |  | PASS 3/3 |
| X11_amp_clipped_howl_minus12dBFS | 3 | 3 | 0 | 4 | 0 | 0 | 0 | 7 | 100/150/1649 | 300 | 630,1000,1250 | FAIL 0/3 |
| X12a_master_drop20_raise_channel | 3 | 3 | 0 | 5 | 0 | 0 | 0 | 8 | 251/252/253 | 300 | 200,630,1250 | FAIL 1/3 |
| X12b_master_drop20_raise_busmaster | 3 | 3 | 0 | 3 | 0 | 0 | 0 | 6 | 251/252/253 | 300 | 160 | FAIL 1/3 |
| X13_decay16_jazz_lav_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 298/352/499 | 600 |  | PASS 3/3 |
| X14_peakhold_loud_band_wedge_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 6 | 248/250/251 | 300 |  | PASS 3/3 |
| X16_wedge_ring_315Hz_loud_band | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 98/101/103 | 300 |  | PASS 3/3 |
| X17_ring_122Hz_acoustic_guitar_body | 0 | 0 | 0 | 26 | 3 | 0 | 0 | 28 | – | 600 | 80,100,160,200,250,315 | FAIL 0/3 |
| X19_handheld_ring_stalls_and_hops | 2 | 2 | 0 | 59 | 1 | 0 | 0 | 46 | 103/653/1203 | 300 | 250,315,400,500,630,800,1000,1250,… | FAIL 0/3 |
| X21_reverberant_area_mic_slow_ring | 3 | 0 | 3 | 8 | 0 | 0 | 0 | 8 | – | 600 | 315,400 | FAIL 0/3 |
| X22_kick_mic_sub_ring_65Hz | 2 | 2 | 0 | 3 | 1 | 0 | 0 | 6 | 103/1451/2799 | 1000 | 100,200 | FAIL 1/3 |
| X23_ringout_quiet_room_two_modes | 9 | 9 | 0 | 0 | 0 | 0 | 0 | 9 | 203/398/948 | 300 |  | FAIL 0/3 |
| **32 scenarios, 10 pass** | 105 | 96 | 9 | 286 | 18 | 0 | 12 | | | | | wall 15.8 s |

<details><summary>cuts per run</summary>

- S2a_established_ring_8k s1: cuts=[(0.25, 8000, -3.0)] miss=[] lat=[251.1]
- S2a_established_ring_8k s2: cuts=[(0.25, 8000, -3.0)] miss=[] lat=[252.3]
- S2a_established_ring_8k s3: cuts=[(0.25, 8000, -3.0)] miss=[] lat=[252.9]
- S2b_established_ring_8k_steep s1: cuts=[(0.25, 8000, -3.0)] miss=[] lat=[251.1]
- S2b_established_ring_8k_steep s2: cuts=[(0.25, 8000, -3.0)] miss=[] lat=[252.3]
- S2b_established_ring_8k_steep s3: cuts=[(0.25, 8000, -3.0)] miss=[] lat=[252.9]
- S2c_established_clipped_2k4 s1: cuts=[(0.25, 2500, -3.0), (0.25, 5000, -3.0), (0.25, 5000, -6.0), (0.25, 12500, -3.0), (0.25, 12500, -6.0)] miss=[] lat=[251.1]
- S2c_established_clipped_2k4 s2: cuts=[(0.25, 2500, -3.0), (0.25, 5000, -3.0), (0.25, 5000, -6.0), (0.25, 12500, -3.0), (0.25, 12500, -6.0)] miss=[] lat=[252.3]
- S2c_established_clipped_2k4 s3: cuts=[(0.25, 2500, -3.0), (0.25, 5000, -3.0), (0.25, 5000, -6.0), (0.25, 12500, -3.0), (0.25, 12500, -6.0)] miss=[] lat=[252.9]
- S3_ring_during_music s1: cuts=[(5.35, 3150, -3.0)] miss=[] lat=[502.3]
- S3_ring_during_music s2: cuts=[(1.05, 800, -3.0), (4.3, 800, -6.0), (5.8, 3150, -3.0), (7.85, 800, -9.0), (11.4, 1000, -3.0)] miss=[] lat=[150.0]
- S3_ring_during_music s3: cuts=[(4.9, 3150, -3.0)] miss=[] lat=[148.1]
- S6b_ringout_steps_latent_loop s1: cuts=[(12.8, 2500, -3.0)] miss=[] lat=[148.5]
- S6b_ringout_steps_latent_loop s2: cuts=[(11.05, 125, -3.0), (11.05, 200, -3.0), (12.8, 2500, -3.0)] miss=[] lat=[252.2]
- S6b_ringout_steps_latent_loop s3: cuts=[(12.8, 2500, -3.0)] miss=[] lat=[147.2]
- S9_clipped_howl_fast s1: cuts=[(2.5, 2000, -3.0)] miss=[] lat=[252.5]
- S9_clipped_howl_fast s2: cuts=[(2.5, 2000, -3.0)] miss=[] lat=[252.7]
- S9_clipped_howl_fast s3: cuts=[(2.5, 2000, -3.0)] miss=[] lat=[248.2]
- S10_ring_between_bands s1: cuts=[(2.45, 2500, -3.0)] miss=[] lat=[798.5]
- S10_ring_between_bands s2: cuts=[(2.25, 2500, -3.0)] miss=[] lat=[698.9]
- S10_ring_between_bands s3: cuts=[(2.15, 2500, -3.0)] miss=[] lat=[502.2]
- S11a_two_rings s1: cuts=[(2.75, 3150, -3.0), (2.8, 1250, -3.0)] miss=[] lat=[498.1, 401.0]
- S11a_two_rings s2: cuts=[(2.45, 1250, -3.0), (2.8, 3150, -3.0)] miss=[] lat=[350.3, 601.8]
- S11a_two_rings s3: cuts=[(2.45, 3150, -3.0), (3.55, 1250, -3.0)] miss=[] lat=[1199.8, 302.0]
- S11b_two_rings_near_octave s1: cuts=[(2.15, 1250, -3.0), (4.15, 2500, -3.0)] miss=[] lat=[201.8, 1897.1]
- S11b_two_rings_near_octave s2: cuts=[(2.25, 1250, -3.0), (2.8, 2500, -3.0)] miss=[] lat=[248.9, 651.8]
- S11b_two_rings_near_octave s3: cuts=[(2.25, 1250, -3.0), (2.5, 2500, -3.0)] miss=[] lat=[347.6, 398.2]
- S12_acoustic_guitar_wedge_ring_196Hz s1: cuts=[(0.65, 160, -3.0), (0.65, 160, -6.0), (2.5, 160, -9.0), (6.65, 100, -3.0)] miss=[] lat=[]
- S12_acoustic_guitar_wedge_ring_196Hz s2: cuts=[(0.65, 100, -3.0), (0.65, 200, -3.0), (6.5, 315, -3.0)] miss=[] lat=[]
- S12_acoustic_guitar_wedge_ring_196Hz s3: cuts=[(2.5, 200, -3.0)] miss=[] lat=[]
- S13_slow_ring_3dB_s s1: cuts=[] miss=[5035] lat=[]
- S13_slow_ring_3dB_s s2: cuts=[] miss=[5035] lat=[]
- S13_slow_ring_3dB_s s3: cuts=[] miss=[5035] lat=[]
- S14_ring_masked_by_cymbal s1: cuts=[(2.9, 4000, -3.0)] miss=[] lat=[197.7]
- S14_ring_masked_by_cymbal s2: cuts=[(3.0, 4000, -3.0)] miss=[] lat=[200.3]
- S14_ring_masked_by_cymbal s3: cuts=[(2.9, 4000, -3.0)] miss=[] lat=[198.5]
- S15_long_rta_decay_tails s1: cuts=[(4.05, 3150, -3.0), (8.65, 50, -3.0)] miss=[] lat=[298.4]
- S15_long_rta_decay_tails s2: cuts=[(3.9, 3150, -3.0)] miss=[] lat=[201.5]
- S15_long_rta_decay_tails s3: cuts=[(4.0, 3150, -3.0)] miss=[] lat=[197.5]
- S22_speech_ringing_then_feedback s1: cuts=[(0.55, 125, -3.0), (0.55, 250, -3.0), (0.9, 125, -6.0), (0.95, 250, -6.0), (1.5, 500, -3.0), (2.0, 125, -9.0), (2.65, 250, -9.0), (3.15, 500, -6.0), (4.05, 160, -3.0), (4.1, 500, -9.0), (4.45, 400, -3.0), (5.35, 160, -6.0), (5.85, 160, -9.0), (6.7, 3150, -3.0), (6.85, 400, -6.0), (7.95, 400, -9.0)] miss=[] lat=[150.4]
- S22_speech_ringing_then_feedback s2: cuts=[(0.55, 160, -3.0), (0.55, 315, -3.0), (0.55, 630, -3.0), (0.6, 315, -6.0), (1.0, 630, -6.0), (1.1, 160, -6.0), (1.55, 315, -9.0), (1.6, 400, -3.0), (1.95, 250, -3.0), (2.25, 160, -9.0), (2.25, 400, -6.0), (2.7, 125, -3.0), (3.15, 400, -9.0), (3.75, 125, -6.0), (4.1, 250, -6.0), (4.2, 630, -9.0), (5.05, 125, -9.0), (5.15, 250, -9.0)] miss=[] lat=[101.7]
- S22_speech_ringing_then_feedback s3: cuts=[(0.55, 250, -3.0), (0.55, 400, -3.0), (0.55, 400, -6.0), (0.6, 125, -3.0), (1.6, 125, -6.0), (1.6, 250, -6.0), (2.65, 400, -9.0), (2.7, 125, -9.0), (3.5, 250, -9.0), (4.15, 500, -3.0), (5.4, 500, -6.0), (6.3, 500, -9.0), (6.8, 3150, -3.0)] miss=[] lat=[349.3]
- M1_loud_band_wedge_ring s1: cuts=[(5.4, 3150, -3.0)] miss=[] lat=[251.1]
- M1_loud_band_wedge_ring s2: cuts=[(5.4, 3150, -3.0)] miss=[] lat=[252.5]
- M1_loud_band_wedge_ring s3: cuts=[(5.4, 3150, -3.0)] miss=[] lat=[248.0]
- M2_quiet_music_ringout_two_modes s1: cuts=[(2.75, 800, -3.0), (5.25, 5000, -3.0), (9.85, 8000, -3.0), (9.95, 5000, -6.0), (15.8, 5000, -9.0)] miss=[] lat=[97.5, 98.1, 101.1, 152.3]
- M2_quiet_music_ringout_two_modes s2: cuts=[(0.5, 630, -3.0), (5.3, 5000, -3.0), (8.95, 630, -6.0), (9.85, 8000, -3.0), (9.9, 5000, -6.0)] miss=[] lat=[97.6, 99.2, 147.2]
- M2_quiet_music_ringout_two_modes s3: cuts=[(4.75, 800, -3.0), (5.35, 5000, -3.0), (6.55, 800, -6.0), (8.6, 800, -9.0), (9.85, 8000, -3.0), (9.95, 5000, -6.0), (10.55, 630, -3.0)] miss=[] lat=[150.1, 99.2, 150.8, 150.5]
- M3_jazz_trio_lav_ring_400Hz s1: cuts=[(5.45, 400, -3.0)] miss=[] lat=[397.9]
- M3_jazz_trio_lav_ring_400Hz s2: cuts=[(5.15, 400, -3.0)] miss=[] lat=[202.2]
- M3_jazz_trio_lav_ring_400Hz s3: cuts=[(5.1, 400, -3.0)] miss=[] lat=[301.6]
- X7_plateaued_ring_under_music_from_t0 s1: cuts=[] miss=[1683] lat=[]
- X7_plateaued_ring_under_music_from_t0 s2: cuts=[(1.1, 800, -3.0), (8.6, 800, -6.0)] miss=[1683] lat=[]
- X7_plateaued_ring_under_music_from_t0 s3: cuts=[(6.7, 630, -3.0), (10.6, 630, -6.0)] miss=[1683] lat=[]
- X8_slow_ring_midband_under_chords s1: cuts=[(2.0, 1000, -3.0), (7.1, 1000, -6.0), (8.45, 1000, -9.0), (14.1, 2000, -3.0), (14.1, 3150, -3.0)] miss=[] lat=[]
- X8_slow_ring_midband_under_chords s2: cuts=[(4.55, 800, -3.0), (14.6, 800, -6.0)] miss=[] lat=[]
- X8_slow_ring_midband_under_chords s3: cuts=[(6.7, 630, -3.0)] miss=[] lat=[]
- X9_ring_rta_midpoint_525Hz_speech s1: cuts=[(0.55, 125, -3.0), (0.55, 250, -3.0), (0.95, 125, -6.0), (0.95, 250, -6.0), (1.5, 500, -3.0), (2.0, 125, -9.0), (2.6, 500, -6.0), (2.65, 250, -9.0), (3.6, 500, -9.0), (4.05, 160, -3.0), (4.85, 400, -3.0), (5.35, 160, -6.0), (5.85, 160, -9.0), (5.85, 400, -6.0), (6.9, 400, -9.0), (7.5, 200, -3.0), (8.6, 200, -6.0)] miss=[] lat=[]
- X9_ring_rta_midpoint_525Hz_speech s2: cuts=[(0.55, 160, -3.0), (0.55, 315, -3.0), (0.55, 630, -3.0), (1.0, 160, -6.0), (1.0, 315, -6.0), (1.0, 630, -6.0), (1.45, 315, -9.0), (2.05, 160, -9.0), (2.05, 250, -3.0), (2.25, 630, -9.0), (2.7, 125, -3.0), (2.7, 250, -6.0), (2.7, 400, -3.0), (3.15, 400, -6.0), (3.75, 125, -6.0), (4.1, 250, -9.0), (4.2, 400, -9.0), (5.05, 125, -9.0)] miss=[] lat=[]
- X9_ring_rta_midpoint_525Hz_speech s3: cuts=[(0.55, 250, -3.0), (0.55, 400, -3.0), (0.55, 400, -6.0), (0.6, 125, -3.0), (1.6, 125, -6.0), (1.6, 250, -6.0), (2.65, 400, -9.0), (2.7, 125, -9.0), (3.5, 250, -9.0), (4.15, 500, -3.0), (4.9, 160, -3.0), (5.4, 500, -6.0), (5.45, 500, -9.0), (6.3, 160, -6.0), (7.4, 160, -9.0)] miss=[] lat=[]
- X10_two_rings_exact_octave s1: cuts=[(2.6, 1600, -3.0), (2.85, 3150, -3.0)] miss=[] lat=[200.8, 148.3]
- X10_two_rings_exact_octave s2: cuts=[(2.55, 1600, -3.0), (2.85, 3150, -3.0)] miss=[] lat=[150.5, 150.3]
- X10_two_rings_exact_octave s3: cuts=[(2.55, 1600, -3.0), (2.85, 3150, -3.0)] miss=[] lat=[198.2, 150.4]
- X11_amp_clipped_howl_minus12dBFS s1: cuts=[(3.1, 1250, -3.0), (4.5, 800, -3.0)] miss=[] lat=[149.6]
- X11_amp_clipped_howl_minus12dBFS s2: cuts=[(5.85, 800, -3.0), (6.4, 800, -6.0), (9.25, 800, -9.0)] miss=[] lat=[1649.3]
- X11_amp_clipped_howl_minus12dBFS s3: cuts=[(4.3, 800, -3.0), (8.55, 800, -6.0)] miss=[] lat=[99.7]
- X12a_master_drop20_raise_channel s1: cuts=[(0.25, 2000, -3.0)] miss=[] lat=[251.1]
- X12a_master_drop20_raise_channel s2: cuts=[(0.25, 2000, -3.0), (7.1, 630, -3.0), (7.1, 1250, -3.0), (7.15, 200, -3.0)] miss=[] lat=[252.3]
- X12a_master_drop20_raise_channel s3: cuts=[(0.25, 2000, -3.0), (7.1, 200, -3.0), (7.1, 630, -3.0)] miss=[] lat=[252.9]
- X12b_master_drop20_raise_busmaster s1: cuts=[(0.25, 2000, -3.0)] miss=[] lat=[251.1]
- X12b_master_drop20_raise_busmaster s2: cuts=[(0.25, 2000, -3.0), (0.3, 160, -3.0), (8.3, 160, -6.0)] miss=[] lat=[252.3]
- X12b_master_drop20_raise_busmaster s3: cuts=[(0.25, 2000, -3.0), (4.25, 160, -3.0)] miss=[] lat=[252.9]
- X13_decay16_jazz_lav_ring s1: cuts=[(5.45, 400, -3.0)] miss=[] lat=[297.9]
- X13_decay16_jazz_lav_ring s2: cuts=[(5.55, 400, -3.0)] miss=[] lat=[351.7]
- X13_decay16_jazz_lav_ring s3: cuts=[(5.5, 400, -3.0)] miss=[] lat=[498.9]
- X14_peakhold_loud_band_wedge_ring s1: cuts=[(5.4, 3150, -3.0), (6.45, 3150, -6.0)] miss=[] lat=[251.1]
- X14_peakhold_loud_band_wedge_ring s2: cuts=[(5.45, 3150, -3.0), (6.5, 3150, -6.0)] miss=[] lat=[249.7]
- X14_peakhold_loud_band_wedge_ring s3: cuts=[(5.4, 3150, -3.0), (6.4, 3150, -6.0)] miss=[] lat=[248.0]
- X16_wedge_ring_315Hz_loud_band s1: cuts=[(6.2, 315, -3.0)] miss=[] lat=[98.5]
- X16_wedge_ring_315Hz_loud_band s2: cuts=[(6.2, 315, -3.0)] miss=[] lat=[103.0]
- X16_wedge_ring_315Hz_loud_band s3: cuts=[(6.2, 315, -3.0)] miss=[] lat=[100.7]
- X17_ring_122Hz_acoustic_guitar_body s1: cuts=[(1.05, 160, -3.0), (4.6, 250, -3.0), (6.95, 250, -6.0), (8.9, 160, -6.0), (10.65, 80, -3.0), (13.05, 160, -9.0)] miss=[] lat=[]
- X17_ring_122Hz_acoustic_guitar_body s2: cuts=[(0.9, 125, -3.0), (1.2, 200, -3.0), (3.0, 125, -6.0), (4.85, 125, -9.0), (5.2, 200, -6.0), (6.65, 200, -9.0), (7.05, 315, -3.0), (8.65, 100, -3.0), (10.6, 315, -6.0), (10.65, 100, -6.0), (11.0, 160, -3.0), (12.85, 160, -6.0)] miss=[] lat=[]
- X17_ring_122Hz_acoustic_guitar_body s3: cuts=[(0.6, 200, -3.0), (0.9, 125, -3.0), (1.1, 125, -6.0), (2.65, 80, -3.0), (3.0, 125, -9.0), (6.65, 200, -6.0), (6.95, 315, -3.0), (9.1, 200, -9.0), (12.85, 160, -3.0), (13.2, 315, -6.0)] miss=[] lat=[]
- X19_handheld_ring_stalls_and_hops s1: cuts=[(0.55, 400, -3.0), (0.55, 800, -3.0), (0.9, 1250, -3.0), (1.65, 800, -6.0), (1.65, 1250, -6.0), (1.65, 1250, -9.0), (1.65, 2500, -3.0), (2.7, 800, -9.0), (2.7, 1600, -3.0), (2.7, 2500, -6.0), (3.2, 1600, -6.0), (3.65, 1000, -3.0), (3.65, 1600, -9.0), (4.9, 400, -6.0), (4.9, 1000, -6.0), (6.5, 400, -9.0), (7.8, 1000, -9.0), (7.8, 2500, -9.0)] miss=[] lat=[]
- X19_handheld_ring_stalls_and_hops s2: cuts=[(0.55, 800, -3.0), (0.8, 500, -3.0), (0.8, 800, -6.0), (1.7, 315, -3.0), (1.7, 500, -6.0), (1.7, 1250, -3.0), (2.7, 315, -6.0), (2.9, 500, -9.0), (3.3, 315, -9.0), (3.3, 800, -9.0), (3.3, 1250, -6.0), (4.5, 1250, -9.0), (5.8, 400, -3.0), (5.8, 1600, -3.0), (7.0, 400, -6.0)] miss=[] lat=[]
- X19_handheld_ring_stalls_and_hops s3: cuts=[(0.55, 315, -3.0), (0.55, 630, -3.0), (0.55, 630, -6.0), (1.5, 315, -6.0), (1.75, 630, -9.0), (2.8, 315, -9.0), (4.25, 2500, -3.0), (6.1, 1250, -3.0), (6.55, 400, -3.0), (6.55, 800, -3.0), (7.3, 400, -6.0), (7.85, 800, -6.0), (8.2, 400, -9.0)] miss=[] lat=[1202.9, 102.9]
- X21_reverberant_area_mic_slow_ring s1: cuts=[] miss=[642] lat=[]
- X21_reverberant_area_mic_slow_ring s2: cuts=[(0.55, 315, -3.0), (0.55, 315, -6.0), (5.3, 315, -9.0), (8.45, 400, -3.0)] miss=[643] lat=[]
- X21_reverberant_area_mic_slow_ring s3: cuts=[(0.55, 315, -3.0), (0.55, 315, -6.0), (4.4, 315, -9.0), (5.5, 400, -3.0)] miss=[642] lat=[]
- X22_kick_mic_sub_ring_65Hz s1: cuts=[(4.15, 100, -3.0), (9.65, 63, -3.0)] miss=[] lat=[2799.3]
- X22_kick_mic_sub_ring_65Hz s2: cuts=[(6.2, 63, -3.0)] miss=[] lat=[103.0]
- X22_kick_mic_sub_ring_65Hz s3: cuts=[(2.5, 63, -3.0), (10.7, 200, -3.0), (11.35, 100, -3.0)] miss=[] lat=[]
- X23_ringout_quiet_room_two_modes s1: cuts=[(9.4, 630, -3.0), (11.55, 2500, -3.0), (13.85, 630, -6.0)] miss=[] lat=[398.1, 252.7, 947.9]
- X23_ringout_quiet_room_two_modes s2: cuts=[(9.3, 630, -3.0), (11.3, 2500, -3.0), (13.8, 630, -6.0)] miss=[] lat=[302.7, 300.3, 701.6]
- X23_ringout_quiet_room_two_modes s3: cuts=[(9.4, 630, -3.0), (11.45, 2500, -3.0), (13.8, 630, -6.0)] miss=[] lat=[400.6, 203.0, 752.5]

</details>

### pre-M7 detector (override_prominence_db=0), open loop

| scenario | ev | TP | miss | FP | early | tail | harm | cuts | lat ms min/med/max | budget | FP at GEQ (Hz) | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C0_silent_room | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| C1_music_bed_drums | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S1_bass_under_quiet_music | 0 | 0 | 0 | 32 | 0 | 0 | 0 | 0 | – | 300 | 80,100,160 | FAIL 0/3 |
| S2a_established_ring_8k | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | FAIL 0/3 |
| S2b_established_ring_8k_steep | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | FAIL 0/3 |
| S2c_established_clipped_2k4 | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | FAIL 0/3 |
| S3_ring_during_music | 3 | 3 | 0 | 5 | 0 | 0 | 0 | 0 | 148/502/602 | 300 | 630,800,1000 | FAIL 1/3 |
| S4a_vocal_vibrato | 0 | 0 | 0 | 38 | 0 | 0 | 0 | 0 | – | 300 | 400,500,800,1000,1250,1600 | FAIL 0/3 |
| S4b_vocal_vibrato_band_edge | 0 | 0 | 0 | 30 | 0 | 0 | 0 | 0 | – | 300 | 400,500,800,1000,1250,1600 | FAIL 0/3 |
| S5_guitar_note_decays_to_sine | 0 | 0 | 0 | 13 | 0 | 0 | 0 | 0 | – | 300 | 160,315,500,630 | FAIL 0/3 |
| S6_master_ramp_feedback_watch | 0 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | – | 300 | 315,400 | FAIL 0/3 |
| S6b_ringout_steps_latent_loop | 3 | 3 | 0 | 2 | 0 | 0 | 0 | 0 | 147/148/202 | 300 | 125,200 | FAIL 2/3 |
| S7_808_sub_bassline | 0 | 0 | 0 | 13 | 0 | 0 | 0 | 0 | – | 300 | 50,63,80 | FAIL 0/3 |
| S8a_organ_melody | 0 | 0 | 0 | 10 | 0 | 0 | 0 | 0 | – | 300 | 630,800,1000 | FAIL 0/3 |
| S8b_flute_held_note | 0 | 0 | 0 | 16 | 0 | 0 | 0 | 0 | – | 300 | 630,800,1000,1250,1600 | FAIL 0/3 |
| S8c_whistle | 0 | 0 | 0 | 12 | 0 | 0 | 0 | 0 | – | 300 | 1250,1600,2000 | FAIL 0/3 |
| S9_clipped_howl_fast | 3 | 1 | 2 | 0 | 0 | 0 | 1 | 0 | 401/401/401 | 300 |  | FAIL 0/3 |
| S10_ring_between_bands | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 502/699/798 | 300 |  | FAIL 0/3 |
| S11a_two_rings | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 301/376/549 | 300 |  | FAIL 0/3 |
| S11b_two_rings_near_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 202/300/353 | 300 |  | FAIL 1/3 |
| S12_acoustic_guitar_wedge_ring_196Hz | 3 | 3 | 0 | 5 | 5 | 0 | 0 | 0 | 302/349/398 | 600 | 100,160 | FAIL 1/3 |
| S13_slow_ring_3dB_s | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | – | 1000 |  | FAIL 0/3 |
| S14_ring_masked_by_cymbal | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 198/198/200 | 300 |  | PASS 3/3 |
| S15_long_rta_decay_tails | 3 | 3 | 0 | 1 | 0 | 0 | 0 | 0 | 198/202/298 | 300 | 50 | FAIL 2/3 |
| S16_peak_hold_on | 0 | 0 | 0 | 12 | 0 | 0 | 0 | 0 | – | 300 | 400,630,800,1000,1250 | FAIL 0/3 |
| S17_kick_pattern | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S18_vibrato_on_band_edge | 0 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | – | 300 | 800,1600 | FAIL 0/3 |
| S19_driven_room_mode | 0 | 0 | 0 | 40 | 0 | 0 | 0 | 0 | – | 300 | 40,80,100 | FAIL 0/3 |
| S20_song_start_stop_crowd | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| S21_synth_pad_swell | 0 | 0 | 0 | 66 | 0 | 0 | 0 | 0 | – | 300 | 100,125,160,200,250,315,400 | FAIL 0/3 |
| S22_speech_ringing_then_feedback | 3 | 3 | 0 | 97 | 0 | 0 | 0 | 0 | 97/102/151 | 300 | 125,160,250,315,400,500,630 | FAIL 0/3 |
| S23a_autogain_drift | 0 | 0 | 0 | 18 | 0 | 0 | 0 | 0 | – | 300 | 80,100,160 | FAIL 0/3 |
| S23b_gain_offset_clip | 0 | 0 | 0 | 16 | 0 | 0 | 0 | 0 | – | 300 | 50,63,80 | FAIL 0/3 |
| S24_bells_triangle_glock | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| M1_loud_band_wedge_ring | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | FAIL 0/3 |
| M2_quiet_music_ringout_two_modes | 6 | 5 | 1 | 8 | 0 | 0 | 0 | 0 | 98/150/552 | 300 | 630,800 | FAIL 0/3 |
| M3_jazz_trio_lav_ring_400Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 202/302/398 | 300 |  | FAIL 1/3 |
| X1_organ_held_notes | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | PASS 3/3 |
| X2_flute_held_vibrato | 0 | 0 | 0 | 13 | 0 | 0 | 0 | 0 | – | 300 | 1250 | FAIL 0/3 |
| X3_whistle_held_drift | 0 | 0 | 0 | 9 | 0 | 0 | 0 | 0 | – | 300 | 1600,2500 | FAIL 0/3 |
| X4_sine_lead_portamento | 0 | 0 | 0 | 12 | 0 | 0 | 0 | 0 | – | 300 | 800,1000,1250 | FAIL 0/3 |
| X5_808_bassline_40_60Hz | 0 | 0 | 0 | 21 | 0 | 0 | 0 | 0 | – | 300 | 40,50,63 | FAIL 0/3 |
| X6_soprano_closed_vowel_band_edge | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | – | 300 | 630,1250 | FAIL 1/3 |
| X7_plateaued_ring_under_music_from_t0 | 3 | 0 | 3 | 4 | 0 | 0 | 0 | 0 | – | 1000 | 630,800 | FAIL 0/3 |
| X8_slow_ring_midband_under_chords | 3 | 0 | 3 | 11 | 0 | 0 | 0 | 0 | – | 1000 | 500,630,800,1000,2000,3150 | FAIL 0/3 |
| X9_ring_rta_midpoint_525Hz_speech | 3 | 3 | 0 | 94 | 0 | 0 | 0 | 0 | -200/151/902 | 300 | 125,160,250,315,400,500,630 | FAIL 0/3 |
| X10_two_rings_exact_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 150/300/548 | 300 |  | FAIL 0/3 |
| X11_amp_clipped_howl_minus12dBFS | 3 | 2 | 1 | 2 | 0 | 0 | 0 | 0 | 100/126/152 | 300 | 630,1250 | FAIL 1/3 |
| X12a_master_drop20_raise_channel | 6 | 2 | 4 | 5 | 0 | 0 | 0 | 0 | 349/350/351 | 300 | 200,630,1250 | FAIL 0/3 |
| X12b_master_drop20_raise_busmaster | 6 | 3 | 3 | 4 | 0 | 0 | 0 | 0 | 300/301/302 | 300 | 160 | FAIL 0/3 |
| X13_decay16_jazz_lav_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 298/352/499 | 600 |  | PASS 3/3 |
| X14_peakhold_loud_band_wedge_ring | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | FAIL 0/3 |
| X15_kick_bass_unison_55Hz | 0 | 0 | 0 | 41 | 0 | 0 | 0 | 0 | – | 300 | 80,100,160,200 | FAIL 0/3 |
| X16_wedge_ring_315Hz_loud_band | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 98/101/103 | 300 |  | PASS 3/3 |
| X17_ring_122Hz_acoustic_guitar_body | 3 | 3 | 0 | 18 | 3 | 0 | 0 | 0 | 103/148/348 | 600 | 80,160,200,250,315 | FAIL 0/3 |
| X18_applause_crowd_30s | 0 | 0 | 0 | 34 | 0 | 0 | 0 | 0 | – | 300 | 400,500,630,800,1000,1250,1600,2000,… | FAIL 0/3 |
| X19_handheld_ring_stalls_and_hops | 3 | 3 | 0 | 51 | 0 | 0 | 0 | 0 | 103/252/497 | 300 | 315,400,500,630,800,1000,1250,1600,… | FAIL 0/3 |
| X20_mains_hum_and_hvac_whine | 0 | 0 | 0 | 55 | 0 | 0 | 0 | 0 | – | 300 | 125,160,250,315,400,500 | FAIL 0/3 |
| X21_reverberant_area_mic_slow_ring | 3 | 0 | 3 | 8 | 0 | 0 | 0 | 0 | – | 600 | 200,315,400 | FAIL 0/3 |
| X22_kick_mic_sub_ring_65Hz | 3 | 3 | 0 | 4 | 1 | 0 | 0 | 0 | 99/103/103 | 1000 | 100,200 | FAIL 1/3 |
| X23_ringout_quiet_room_two_modes | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 303/652/852 | 300 |  | FAIL 0/3 |
| **61 scenarios, 9 pass** | 117 | 79 | 38 | 834 | 9 | 0 | 1 | | | | | wall 2.4 s |

### pre-M7 detector (override_prominence_db=0), closed loop

| scenario | ev | TP | miss | FP | early | tail | harm | cuts | lat ms min/med/max | budget | FP at GEQ (Hz) | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S2a_established_ring_8k | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | FAIL 0/3 |
| S2b_established_ring_8k_steep | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | FAIL 0/3 |
| S2c_established_clipped_2k4 | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | FAIL 0/3 |
| S3_ring_during_music | 3 | 3 | 0 | 4 | 0 | 0 | 0 | 7 | 148/150/502 | 300 | 630,800,1000 | FAIL 1/3 |
| S6b_ringout_steps_latent_loop | 3 | 3 | 0 | 2 | 0 | 0 | 0 | 5 | 147/148/252 | 300 | 125,200 | FAIL 2/3 |
| S9_clipped_howl_fast | 3 | 1 | 2 | 0 | 0 | 0 | 1 | 2 | 401/401/401 | 300 |  | FAIL 0/3 |
| S10_ring_between_bands | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 502/699/798 | 300 |  | FAIL 0/3 |
| S11a_two_rings | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 6 | 302/450/1200 | 300 |  | FAIL 0/3 |
| S11b_two_rings_near_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 6 | 202/373/2049 | 300 |  | FAIL 0/3 |
| S12_acoustic_guitar_wedge_ring_196Hz | 0 | 0 | 0 | 4 | 4 | 0 | 0 | 8 | – | 600 | 100,160,315 | FAIL 1/3 |
| S13_slow_ring_3dB_s | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | – | 1000 |  | FAIL 0/3 |
| S14_ring_masked_by_cymbal | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 198/198/200 | 300 |  | PASS 3/3 |
| S15_long_rta_decay_tails | 3 | 3 | 0 | 1 | 0 | 0 | 0 | 4 | 198/202/298 | 300 | 50 | FAIL 2/3 |
| S22_speech_ringing_then_feedback | 3 | 3 | 0 | 74 | 0 | 0 | 0 | 47 | 102/150/349 | 300 | 125,160,200,250,315,400,500,630 | FAIL 0/3 |
| M1_loud_band_wedge_ring | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | FAIL 0/3 |
| M2_quiet_music_ringout_two_modes | 11 | 11 | 0 | 7 | 0 | 0 | 0 | 17 | 98/101/152 | 300 | 630,800 | FAIL 0/3 |
| M3_jazz_trio_lav_ring_400Hz | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 202/302/398 | 300 |  | FAIL 1/3 |
| X7_plateaued_ring_under_music_from_t0 | 3 | 0 | 3 | 4 | 0 | 0 | 0 | 4 | – | 1000 | 630,800 | FAIL 0/3 |
| X8_slow_ring_midband_under_chords | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 8 | – | 1000 | 630,800,1000,2000,3150 | FAIL 0/3 |
| X9_ring_rta_midpoint_525Hz_speech | 0 | 0 | 0 | 73 | 9 | 0 | 0 | 50 | – | 300 | 125,160,200,250,315,400,500,630,… | FAIL 0/3 |
| X10_two_rings_exact_octave | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 6 | 148/150/201 | 300 |  | PASS 3/3 |
| X11_amp_clipped_howl_minus12dBFS | 3 | 2 | 1 | 3 | 0 | 0 | 0 | 5 | 100/125/150 | 300 | 630,1000,1250 | FAIL 0/3 |
| X12a_master_drop20_raise_channel | 6 | 2 | 4 | 5 | 0 | 0 | 0 | 7 | 301/325/349 | 300 | 200,630,1250 | FAIL 0/3 |
| X12b_master_drop20_raise_busmaster | 6 | 3 | 3 | 3 | 0 | 0 | 0 | 6 | 300/301/302 | 300 | 160 | FAIL 0/3 |
| X13_decay16_jazz_lav_ring | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 298/352/499 | 600 |  | PASS 3/3 |
| X14_peakhold_loud_band_wedge_ring | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | – | 300 |  | FAIL 0/3 |
| X16_wedge_ring_315Hz_loud_band | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 3 | 98/101/103 | 300 |  | PASS 3/3 |
| X17_ring_122Hz_acoustic_guitar_body | 0 | 0 | 0 | 26 | 3 | 0 | 0 | 28 | – | 600 | 80,100,160,200,250,315 | FAIL 0/3 |
| X19_handheld_ring_stalls_and_hops | 2 | 2 | 0 | 56 | 1 | 0 | 0 | 44 | 103/653/1203 | 300 | 250,315,400,500,630,800,1000,1250,… | FAIL 0/3 |
| X21_reverberant_area_mic_slow_ring | 3 | 0 | 3 | 8 | 0 | 0 | 0 | 8 | – | 600 | 315,400 | FAIL 0/3 |
| X22_kick_mic_sub_ring_65Hz | 2 | 1 | 1 | 3 | 1 | 0 | 0 | 5 | 103/103/103 | 1000 | 100,200 | FAIL 1/3 |
| X23_ringout_quiet_room_two_modes | 9 | 9 | 0 | 0 | 0 | 0 | 0 | 9 | 203/401/948 | 300 |  | FAIL 0/3 |
| **32 scenarios, 4 pass** | 111 | 76 | 35 | 281 | 18 | 0 | 1 | | | | | wall 15.5 s |

<details><summary>cuts per run</summary>

- S2a_established_ring_8k s1: cuts=[] miss=[8122] lat=[]
- S2a_established_ring_8k s2: cuts=[] miss=[8122] lat=[]
- S2a_established_ring_8k s3: cuts=[] miss=[8122] lat=[]
- S2b_established_ring_8k_steep s1: cuts=[] miss=[8122] lat=[]
- S2b_established_ring_8k_steep s2: cuts=[] miss=[8122] lat=[]
- S2b_established_ring_8k_steep s3: cuts=[] miss=[8122] lat=[]
- S2c_established_clipped_2k4 s1: cuts=[] miss=[2405] lat=[]
- S2c_established_clipped_2k4 s2: cuts=[] miss=[2405] lat=[]
- S2c_established_clipped_2k4 s3: cuts=[] miss=[2405] lat=[]
- S3_ring_during_music s1: cuts=[(5.35, 3150, -3.0)] miss=[] lat=[502.3]
- S3_ring_during_music s2: cuts=[(1.05, 800, -3.0), (4.3, 800, -6.0), (5.8, 3150, -3.0), (7.85, 800, -9.0), (11.4, 1000, -3.0)] miss=[] lat=[150.0]
- S3_ring_during_music s3: cuts=[(4.9, 3150, -3.0)] miss=[] lat=[148.1]
- S6b_ringout_steps_latent_loop s1: cuts=[(12.8, 2500, -3.0)] miss=[] lat=[148.5]
- S6b_ringout_steps_latent_loop s2: cuts=[(11.05, 125, -3.0), (11.05, 200, -3.0), (12.8, 2500, -3.0)] miss=[] lat=[252.2]
- S6b_ringout_steps_latent_loop s3: cuts=[(12.8, 2500, -3.0)] miss=[] lat=[147.2]
- S9_clipped_howl_fast s1: cuts=[(2.65, 2000, -3.0)] miss=[] lat=[401.2]
- S9_clipped_howl_fast s2: cuts=[] miss=[2007] lat=[]
- S9_clipped_howl_fast s3: cuts=[(2.7, 4000, -3.0)] miss=[2007] lat=[]
- S10_ring_between_bands s1: cuts=[(2.45, 2500, -3.0)] miss=[] lat=[798.5]
- S10_ring_between_bands s2: cuts=[(2.25, 2500, -3.0)] miss=[] lat=[698.9]
- S10_ring_between_bands s3: cuts=[(2.15, 2500, -3.0)] miss=[] lat=[502.2]
- S11a_two_rings s1: cuts=[(2.75, 3150, -3.0), (2.8, 1250, -3.0)] miss=[] lat=[498.1, 401.0]
- S11a_two_rings s2: cuts=[(2.45, 1250, -3.0), (2.8, 3150, -3.0)] miss=[] lat=[350.3, 601.8]
- S11a_two_rings s3: cuts=[(2.45, 3150, -3.0), (3.55, 1250, -3.0)] miss=[] lat=[1199.8, 302.0]
- S11b_two_rings_near_octave s1: cuts=[(2.15, 1250, -3.0), (4.3, 2500, -3.0)] miss=[] lat=[201.8, 2049.4]
- S11b_two_rings_near_octave s2: cuts=[(2.25, 1250, -3.0), (2.8, 2500, -3.0)] miss=[] lat=[248.9, 651.8]
- S11b_two_rings_near_octave s3: cuts=[(2.25, 1250, -3.0), (2.5, 2500, -3.0)] miss=[] lat=[347.6, 398.2]
- S12_acoustic_guitar_wedge_ring_196Hz s1: cuts=[(0.65, 160, -3.0), (0.65, 160, -6.0), (2.5, 160, -9.0), (6.65, 100, -3.0)] miss=[] lat=[]
- S12_acoustic_guitar_wedge_ring_196Hz s2: cuts=[(0.65, 100, -3.0), (0.65, 200, -3.0), (6.5, 315, -3.0)] miss=[] lat=[]
- S12_acoustic_guitar_wedge_ring_196Hz s3: cuts=[(2.5, 200, -3.0)] miss=[] lat=[]
- S13_slow_ring_3dB_s s1: cuts=[] miss=[5035] lat=[]
- S13_slow_ring_3dB_s s2: cuts=[] miss=[5035] lat=[]
- S13_slow_ring_3dB_s s3: cuts=[] miss=[5035] lat=[]
- S14_ring_masked_by_cymbal s1: cuts=[(2.9, 4000, -3.0)] miss=[] lat=[197.7]
- S14_ring_masked_by_cymbal s2: cuts=[(3.0, 4000, -3.0)] miss=[] lat=[200.3]
- S14_ring_masked_by_cymbal s3: cuts=[(2.9, 4000, -3.0)] miss=[] lat=[198.5]
- S15_long_rta_decay_tails s1: cuts=[(4.05, 3150, -3.0), (8.65, 50, -3.0)] miss=[] lat=[298.4]
- S15_long_rta_decay_tails s2: cuts=[(3.9, 3150, -3.0)] miss=[] lat=[201.5]
- S15_long_rta_decay_tails s3: cuts=[(4.0, 3150, -3.0)] miss=[] lat=[197.5]
- S22_speech_ringing_then_feedback s1: cuts=[(0.55, 125, -3.0), (0.55, 250, -3.0), (0.9, 125, -6.0), (0.95, 250, -6.0), (1.5, 500, -3.0), (2.0, 125, -9.0), (2.65, 250, -9.0), (3.15, 500, -6.0), (4.05, 160, -3.0), (4.1, 500, -9.0), (4.45, 400, -3.0), (5.35, 160, -6.0), (5.85, 160, -9.0), (6.7, 3150, -3.0), (6.85, 400, -6.0), (7.95, 400, -9.0)] miss=[] lat=[150.4]
- S22_speech_ringing_then_feedback s2: cuts=[(0.55, 160, -3.0), (0.55, 315, -3.0), (0.55, 630, -3.0), (0.6, 315, -6.0), (1.0, 630, -6.0), (1.1, 160, -6.0), (1.55, 315, -9.0), (1.6, 400, -3.0), (1.95, 250, -3.0), (2.25, 160, -9.0), (2.25, 400, -6.0), (2.7, 125, -3.0), (3.15, 400, -9.0), (3.75, 125, -6.0), (4.1, 250, -6.0), (4.2, 630, -9.0), (5.05, 125, -9.0), (5.15, 250, -9.0)] miss=[] lat=[101.7]
- S22_speech_ringing_then_feedback s3: cuts=[(0.55, 250, -3.0), (0.55, 400, -3.0), (0.55, 400, -6.0), (0.6, 125, -3.0), (1.6, 125, -6.0), (1.6, 250, -6.0), (2.65, 400, -9.0), (2.7, 125, -9.0), (3.5, 250, -9.0), (4.15, 500, -3.0), (5.4, 500, -6.0), (6.3, 500, -9.0), (6.8, 3150, -3.0)] miss=[] lat=[349.3]
- M1_loud_band_wedge_ring s1: cuts=[] miss=[2831] lat=[]
- M1_loud_band_wedge_ring s2: cuts=[] miss=[2831] lat=[]
- M1_loud_band_wedge_ring s3: cuts=[] miss=[2831] lat=[]
- M2_quiet_music_ringout_two_modes s1: cuts=[(2.75, 800, -3.0), (5.25, 5000, -3.0), (9.85, 8000, -3.0), (9.95, 5000, -6.0), (15.8, 5000, -9.0)] miss=[] lat=[97.5, 98.1, 101.1, 152.3]
- M2_quiet_music_ringout_two_modes s2: cuts=[(0.5, 630, -3.0), (5.3, 5000, -3.0), (8.95, 630, -6.0), (9.85, 8000, -3.0), (9.9, 5000, -6.0)] miss=[] lat=[97.6, 99.2, 147.2]
- M2_quiet_music_ringout_two_modes s3: cuts=[(4.75, 800, -3.0), (5.35, 5000, -3.0), (6.55, 800, -6.0), (8.6, 800, -9.0), (9.85, 8000, -3.0), (9.95, 5000, -6.0), (10.55, 630, -3.0)] miss=[] lat=[150.1, 99.2, 150.8, 150.5]
- M3_jazz_trio_lav_ring_400Hz s1: cuts=[(5.45, 400, -3.0)] miss=[] lat=[397.9]
- M3_jazz_trio_lav_ring_400Hz s2: cuts=[(5.15, 400, -3.0)] miss=[] lat=[202.2]
- M3_jazz_trio_lav_ring_400Hz s3: cuts=[(5.1, 400, -3.0)] miss=[] lat=[301.6]
- X7_plateaued_ring_under_music_from_t0 s1: cuts=[] miss=[1683] lat=[]
- X7_plateaued_ring_under_music_from_t0 s2: cuts=[(1.1, 800, -3.0), (8.6, 800, -6.0)] miss=[1683] lat=[]
- X7_plateaued_ring_under_music_from_t0 s3: cuts=[(6.7, 630, -3.0), (10.6, 630, -6.0)] miss=[1683] lat=[]
- X8_slow_ring_midband_under_chords s1: cuts=[(2.0, 1000, -3.0), (7.1, 1000, -6.0), (8.45, 1000, -9.0), (14.1, 2000, -3.0), (14.1, 3150, -3.0)] miss=[] lat=[]
- X8_slow_ring_midband_under_chords s2: cuts=[(4.55, 800, -3.0), (14.6, 800, -6.0)] miss=[] lat=[]
- X8_slow_ring_midband_under_chords s3: cuts=[(6.7, 630, -3.0)] miss=[] lat=[]
- X9_ring_rta_midpoint_525Hz_speech s1: cuts=[(0.55, 125, -3.0), (0.55, 250, -3.0), (0.95, 125, -6.0), (0.95, 250, -6.0), (1.5, 500, -3.0), (2.0, 125, -9.0), (2.6, 500, -6.0), (2.65, 250, -9.0), (3.6, 500, -9.0), (4.05, 160, -3.0), (4.85, 400, -3.0), (5.35, 160, -6.0), (5.85, 160, -9.0), (5.85, 400, -6.0), (6.9, 400, -9.0), (7.5, 200, -3.0), (8.6, 200, -6.0)] miss=[] lat=[]
- X9_ring_rta_midpoint_525Hz_speech s2: cuts=[(0.55, 160, -3.0), (0.55, 315, -3.0), (0.55, 630, -3.0), (1.0, 160, -6.0), (1.0, 315, -6.0), (1.0, 630, -6.0), (1.45, 315, -9.0), (2.05, 160, -9.0), (2.05, 250, -3.0), (2.25, 630, -9.0), (2.7, 125, -3.0), (2.7, 250, -6.0), (2.7, 400, -3.0), (3.15, 400, -6.0), (3.75, 125, -6.0), (4.1, 250, -9.0), (4.2, 400, -9.0), (5.05, 125, -9.0)] miss=[] lat=[]
- X9_ring_rta_midpoint_525Hz_speech s3: cuts=[(0.55, 250, -3.0), (0.55, 400, -3.0), (0.55, 400, -6.0), (0.6, 125, -3.0), (1.6, 125, -6.0), (1.6, 250, -6.0), (2.65, 400, -9.0), (2.7, 125, -9.0), (3.5, 250, -9.0), (4.15, 500, -3.0), (4.9, 160, -3.0), (5.4, 500, -6.0), (5.45, 500, -9.0), (6.3, 160, -6.0), (7.4, 160, -9.0)] miss=[] lat=[]
- X10_two_rings_exact_octave s1: cuts=[(2.6, 1600, -3.0), (2.85, 3150, -3.0)] miss=[] lat=[200.8, 148.3]
- X10_two_rings_exact_octave s2: cuts=[(2.55, 1600, -3.0), (2.85, 3150, -3.0)] miss=[] lat=[150.5, 150.3]
- X10_two_rings_exact_octave s3: cuts=[(2.55, 1600, -3.0), (2.85, 3150, -3.0)] miss=[] lat=[198.2, 150.4]
- X11_amp_clipped_howl_minus12dBFS s1: cuts=[(3.1, 1250, -3.0), (4.5, 800, -3.0)] miss=[] lat=[149.6]
- X11_amp_clipped_howl_minus12dBFS s2: cuts=[(9.25, 630, -3.0)] miss=[898] lat=[]
- X11_amp_clipped_howl_minus12dBFS s3: cuts=[(4.3, 800, -3.0), (8.55, 800, -6.0)] miss=[] lat=[99.7]
- X12a_master_drop20_raise_channel s1: cuts=[(7.65, 2000, -3.0)] miss=[2048] lat=[349.3]
- X12a_master_drop20_raise_channel s2: cuts=[(7.1, 630, -3.0), (7.1, 1250, -3.0), (7.15, 200, -3.0)] miss=[2048, 2048] lat=[]
- X12a_master_drop20_raise_channel s3: cuts=[(7.1, 200, -3.0), (7.1, 630, -3.0), (7.65, 2000, -3.0)] miss=[2048] lat=[301.2]
- X12b_master_drop20_raise_busmaster s1: cuts=[(7.6, 2000, -3.0)] miss=[2048] lat=[300.2]
- X12b_master_drop20_raise_busmaster s2: cuts=[(0.3, 160, -3.0), (7.65, 2000, -3.0), (8.3, 160, -6.0)] miss=[2048] lat=[302.0]
- X12b_master_drop20_raise_busmaster s3: cuts=[(4.25, 160, -3.0), (7.6, 2000, -3.0)] miss=[2048] lat=[300.9]
- X13_decay16_jazz_lav_ring s1: cuts=[(5.45, 400, -3.0)] miss=[] lat=[297.9]
- X13_decay16_jazz_lav_ring s2: cuts=[(5.55, 400, -3.0)] miss=[] lat=[351.7]
- X13_decay16_jazz_lav_ring s3: cuts=[(5.5, 400, -3.0)] miss=[] lat=[498.9]
- X14_peakhold_loud_band_wedge_ring s1: cuts=[] miss=[2831] lat=[]
- X14_peakhold_loud_band_wedge_ring s2: cuts=[] miss=[2831] lat=[]
- X14_peakhold_loud_band_wedge_ring s3: cuts=[] miss=[2831] lat=[]
- X16_wedge_ring_315Hz_loud_band s1: cuts=[(6.2, 315, -3.0)] miss=[] lat=[98.5]
- X16_wedge_ring_315Hz_loud_band s2: cuts=[(6.2, 315, -3.0)] miss=[] lat=[103.0]
- X16_wedge_ring_315Hz_loud_band s3: cuts=[(6.2, 315, -3.0)] miss=[] lat=[100.7]
- X17_ring_122Hz_acoustic_guitar_body s1: cuts=[(1.05, 160, -3.0), (4.6, 250, -3.0), (6.95, 250, -6.0), (8.9, 160, -6.0), (10.65, 80, -3.0), (13.05, 160, -9.0)] miss=[] lat=[]
- X17_ring_122Hz_acoustic_guitar_body s2: cuts=[(0.9, 125, -3.0), (1.2, 200, -3.0), (3.0, 125, -6.0), (4.85, 125, -9.0), (5.2, 200, -6.0), (6.65, 200, -9.0), (7.05, 315, -3.0), (8.65, 100, -3.0), (10.6, 315, -6.0), (10.65, 100, -6.0), (11.0, 160, -3.0), (12.85, 160, -6.0)] miss=[] lat=[]
- X17_ring_122Hz_acoustic_guitar_body s3: cuts=[(0.6, 200, -3.0), (0.9, 125, -3.0), (1.1, 125, -6.0), (2.65, 80, -3.0), (3.0, 125, -9.0), (6.65, 200, -6.0), (6.95, 315, -3.0), (9.1, 200, -9.0), (12.85, 160, -3.0), (13.2, 315, -6.0)] miss=[] lat=[]
- X19_handheld_ring_stalls_and_hops s1: cuts=[(0.55, 400, -3.0), (0.55, 800, -3.0), (0.9, 1250, -3.0), (1.65, 800, -6.0), (1.65, 1250, -6.0), (1.65, 1250, -9.0), (1.65, 2500, -3.0), (2.7, 800, -9.0), (2.7, 1600, -3.0), (2.7, 2500, -6.0), (3.2, 1600, -6.0), (3.65, 1000, -3.0), (3.65, 1600, -9.0), (4.9, 400, -6.0), (4.9, 1000, -6.0), (6.5, 400, -9.0), (7.8, 1000, -9.0), (7.8, 2500, -9.0)] miss=[] lat=[]
- X19_handheld_ring_stalls_and_hops s2: cuts=[(0.55, 800, -3.0), (0.8, 500, -3.0), (0.8, 800, -6.0), (1.7, 315, -3.0), (1.7, 500, -6.0), (1.7, 1250, -3.0), (2.7, 315, -6.0), (3.3, 315, -9.0), (3.3, 800, -9.0), (3.3, 1250, -6.0), (4.5, 1250, -9.0), (5.8, 500, -9.0), (5.8, 1600, -3.0), (6.25, 1000, -3.0)] miss=[] lat=[]
- X19_handheld_ring_stalls_and_hops s3: cuts=[(0.55, 315, -3.0), (0.55, 630, -3.0), (0.55, 630, -6.0), (1.5, 315, -6.0), (1.75, 630, -9.0), (2.8, 315, -9.0), (4.25, 2500, -3.0), (6.1, 1250, -3.0), (6.55, 400, -3.0), (6.55, 800, -3.0), (7.3, 400, -6.0), (8.2, 400, -9.0)] miss=[] lat=[1202.9, 102.9]
- X21_reverberant_area_mic_slow_ring s1: cuts=[] miss=[642] lat=[]
- X21_reverberant_area_mic_slow_ring s2: cuts=[(0.55, 315, -3.0), (0.55, 315, -6.0), (5.3, 315, -9.0), (8.45, 400, -3.0)] miss=[643] lat=[]
- X21_reverberant_area_mic_slow_ring s3: cuts=[(0.55, 315, -3.0), (0.55, 315, -6.0), (4.4, 315, -9.0), (5.5, 400, -3.0)] miss=[642] lat=[]
- X22_kick_mic_sub_ring_65Hz s1: cuts=[(4.15, 100, -3.0)] miss=[65] lat=[]
- X22_kick_mic_sub_ring_65Hz s2: cuts=[(6.2, 63, -3.0)] miss=[] lat=[103.0]
- X22_kick_mic_sub_ring_65Hz s3: cuts=[(2.5, 63, -3.0), (10.7, 200, -3.0), (11.35, 100, -3.0)] miss=[] lat=[]
- X23_ringout_quiet_room_two_modes s1: cuts=[(9.75, 630, -3.0), (11.55, 2500, -3.0), (13.85, 630, -6.0)] miss=[] lat=[749.1, 252.7, 947.9]
- X23_ringout_quiet_room_two_modes s2: cuts=[(9.3, 630, -3.0), (11.3, 2500, -3.0), (13.8, 630, -6.0)] miss=[] lat=[302.7, 300.3, 701.6]
- X23_ringout_quiet_room_two_modes s3: cuts=[(9.4, 630, -3.0), (11.45, 2500, -3.0), (13.8, 630, -6.0)] miss=[] lat=[400.6, 203.0, 752.5]

</details>
