# Ring-out test on the mains with one open mic — studio, 2026-09-25 afternoon

*The first controlled run of CFS² `ring_out` on the real desk since M7 (2026-09-20), and the first with the analyser on
real PEAK, both GEQ legs written, the measured band grid and the arm-time analyser wake (all merged to `main` @ `7f9ea64`
today). Written by the reviewer for Jim at the desk. Nobody plays during the run.*

## 0. Safety first (two minutes)

* **Ears**: the tool raises the Main master 1 dB every 1.5 s until a ring appears; a ring lasts a few hundred milliseconds
  before the cut lands. Wear ear protection or stand well back from the PA. Keep the target no higher than your normal
  gig level.
* **Limiter**: put the Main LR compressor on as a brick wall for the run (threshold around −6 dB, ratio 10:1 or LIM, fast
  attack). It caps the PA if a ring escapes the detector. Switch it back afterwards.
* **Panic**: `panic` in either Claude session mutes the mains and every bus instantly (no confirmation). `clear_panic`
  afterwards, `restore_snapshot latest` undoes every write the run made.
* **One mic only**: mute Ch 1 (the EMG mic is assigned to LR at −4.8 dB), mute Ch 2/3 (Spotify), leave every other channel
  at −∞. The preflight lists what it sees feeding the mains; confirm it shows Ch 4 alone.

## 1. Desk setup (five minutes)

1. Ch 4 "Lead Vox": fader 0 dB, unmuted, assigned to LR, HPF on (98 Hz is fine), EQ off, no compressor. Set the preamp gain
   by ear: at your usual Main level the mic should be on the edge of ringing when it faces the PA, and quiet when it faces
   away. Then point it at the PA from a couple of metres and leave it on a stand.
2. Main LR: fader to **−30 dB** — the run starts from here and raises; the tool refuses a −∞ start.
3. GEQ: in the Claude session that drives the desk, run `setup_ringout_eqs(["main"])`. It loads a Dual GEQ into a free FX
   slot (5–8) and inserts it on Main LR pre; it asks for a confirmation token because it changes FX types and the insert.
   Then `validate_ringout_eqs(["main"])` must say ok.
4. `discover_mics("main")` should list Ch 4 as the one live mic. If Ch 1 shows up, mute it.

## 2. The run

```
ring_out("main", target_gain_db=-10, step_db=1.0, dwell_ms=1500, notch_budget=6)
```

The first call runs preflight (GEQ ok, mains unmuted, master not −∞, mic list, RTA re-pointed and verified, the analyser
wake) and returns the plan with a `confirm_token`; check the mic list and the start/target levels, then call it again
with the token. Watch the dashboard at `http://127.0.0.1:8032/` or `cfs_status`.

What should happen: the master climbs −30 → −29 → … ; at the first ring the detector emits within ~250 ms, the GEQ band
nearest the ring goes −3 (both sides), the tool verifies the cut over 1.5 s, deepens to −6 / −9 if the line holds, and
carries on climbing; at the target or when six bands are used it backs off 3 dB and writes the report. Total two to
four minutes. `get_ringout_report(<id>)` prints the notches (band, Hz, depth), the gain before the first feedback and the
end level.

## 3. What we learn, and what to look at afterwards

* **Kill**: did each ring stop audibly at −3, or did it take −6 / −9? (M7 needed −9 because only one GEQ leg was cut.)
* **Placement**: are the notched frequencies where you heard the rings? (grid fixed today; the RTA bins are 20·2^(i/10)).
* **False cuts**: with nobody playing there should be none. Any notch you did not hear ringing is a finding.
* **Gain before feedback**: the report's start → first-ring level, the number that matters for the room.
* **Restore**: `restore_snapshot("latest")` puts the desk back (Main level, GEQ bands, insert), or keep the notches if you
  want them for the evening. Switch the limiter back.

## 4. If there is time: a quiet-room watch (five minutes)

`feedback_watch("main")` with Ch 4 still open and the mains at a normal level, then talk and sing into the mic for a
minute, no music. This is the false-positive test the detector failed on Spotify this morning (4 wrong cuts in 3 minutes
under PEAK), but with a voice's harmonics in play, which is the evidence the detector uses to leave a note alone. Any
notch during singing is a finding for the frontier model; `feedback_watch_stop` and `restore_snapshot` afterwards.

## 5. Record

The run's report goes to the server's report store (`list_ringout_reports`); copy its Markdown into
`docs/research/ringout_2026-09-25.md` with a line on what was heard at each notch, and the desk's RTA prefs before and
after (they are restored by the tool). The reviewer replays the session's frames if a false cut appears.
