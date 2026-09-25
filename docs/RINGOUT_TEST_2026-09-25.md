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

---

## 6. Results (13:21–13:39, reports in `docs/research/ringout/`)

Setup as in §1: SM58-type mic on Ch 4 (SD16 input A01, head amp +50 dB, HPF 101 Hz), one metre in front of an Alto PA
speaker; Main LR limiter on (−6 dB, 100:1); Dual GEQ in FX 5 inserted PRE on Main LR, both sides written. The MCP server
was the process started at 10:33, i.e. **this morning's code**: analyser on RMS (the enum fix was merged after it started),
no arm-time wake, no push-based takeover. Three climbs, 1 dB per 1.5 s, six-notch budget.

| run | mic | Main start → end (max) | first ring at | notches | outcome |
|---|---|---|---|---|---|
| 1 | 2 m, facing PA | −30.6 → −13.0 (−10.0 target) | none | 0 | DONE, no feedback up to −10 |
| 2 | 1 m, facing PA | −25.1 → −11.1 (−5.1) | −9.1 dB | 5 kHz −3, 8 kHz −9 | ABORT at −5.1: the 8.85 kHz ring returned with the 8 kHz band at −9 |
| 3 | as 2, plus a hand-set Main PEQ notch 8.73 kHz −6 Q 6.1 | −25.1 → −3.1 (−0.1) | −8.1 dB | 500 −3, 1k −3, 3.15k −6, 5k −9, 8k −9, 10k −6 | DONE at the 0 dB ceiling: budget spent |

**What worked.** Every ring was caught while still 60–80 dB below full scale (levels −82 to −65 dBFS at the tap; the loud
10.7 kHz ring in run 3 at −9 then −3 dBFS was the limiter holding it), the GEQ write landed on both sides, and every cut
verified with a 6–12 dB drop within 0.3–1.5 s; the detector's own verdicts were `confirmed` on 15 of 16 cuts. The one
`insufficient` (run 3, 8.89 kHz, −3 on the 8 kHz band: the line rose 6 dB) triggered the ladder's deepen at once and the −6
confirmed. Gain before feedback −9.1 / −8.1 dB; end level backed off 3–6 dB below the last ring.

**What the GEQ cannot do.** The dominant mode of this room with this placement sits at 8.82–8.92 kHz, 0.15 oct above the
8 kHz band and 0.18 below 10 kHz. In run 2 each −3 step on 8 kHz bought exactly one more decibel of master before the
mode re-lit, and the ladder ran out at −9. With a parametric notch near it (run 3: 8.73 kHz −6 Q 6.1, 0.03 oct off the
mode) the same climb reached the 0 dB ceiling, that mode did not ring until −4.1 (5 dB later) and needed only −6 on the
graphic on top. This is the PEQ actuator's case (`docs/PEQ_ACTUATOR_DESIGN.md`), measured on the desk.

**Two defects for the frontier model.**
1. **Adjacent-band merge is too coarse.** Run 3 at −3.1: a 10.73 kHz ring (RTA band 91) was written as **8 kHz → −9**,
   0.42 oct away, because `NotchController.propose` deepens the nearest existing notch within `merge_adjacent_bands` 1
   before opening the detection's own band (10 kHz). The verify "passed" (the ring happened to collapse) and the next
   10.7 kHz return had to open 10 kHz anyway. Merge only when the detection lies within the existing notch's own
   third-octave (or half a band), never across a whole band.
2. **A limiter-held ring trips `PROGRAMME_PRESENT`.** At t+32 s the 10.7 kHz ring at −9 dBFS set the programme flag,
   after which the report says "MODERATE lines are not cut". A single full-scale line is not programme; the heuristic
   should require broadband activity outside the ring's own neighbourhood.

**Not yet run**: the identical climb on real PEAK (needs the MCP server restarted on today's code: a new session starts
it) and the quiet-room watch with a voice (§4).
