# Review brief for a frontier model

You have the repository and no hardware. That constraint is fine: the most valuable open problem
here is a signal-processing question that can be reasoned about and validated entirely offline,
using test infrastructure that already exists.

Read [`README.md`](../README.md) first (it explains the domain for a non-audio reader), then
[`docs/DESIGN.md`](DESIGN.md) for the module contract and [`docs/HANDOVER.md`](HANDOVER.md) §4a/§4b
for what real-hardware testing found. The protocol research in [`docs/research/`](research/) is
verified ground truth — treat it as authoritative over the code.

**Context that matters:** this server drives a live mixing console. A bad write is heard instantly
by a room full of people and cannot be undone. On 2026-09-20 it was tested against a real X32 Rack
for the first time and nine defects surfaced that 934 passing tests had not — every one of them a
wrong belief about the world that the emulator faithfully reproduced. Assume more of those remain.

---

## 1. The main problem: the feedback discriminator (highest value by far)

`src/x32mcp/detector.py` decides when to cut a frequency on a live PA. It scores each candidate:

```
confidence = 0.3·prominence + 0.2·persistence + 0.5·growth
```

where growth is the monotonic dB/s rise. **Both of its failure modes have now been observed on
real hardware:**

- **False negative.** Without growth the score caps at `0.3 + 0.2 = 0.50`, under the 0.7 threshold.
  A howl that has already plateaued is therefore *mathematically unreachable* — a 60 dB-prominent
  8 kHz ring sat at exactly 0.50 for 15 seconds while the operator held it. Patched with a
  prominence override (≥ 25 dB for ≥ 6 frames), which works but is a threshold bolted onto a
  threshold.
- **False positive.** Quiet background *music* produced confident detections at 40 Hz and 80 Hz —
  frequencies where a Shure SM58 into Alto tops cannot physically produce feedback. Bass notes are
  prominent, persistent, and do rise, so they satisfy all three terms. This spent the notch budget
  before the real feedback arrived, which is what actually ended the test.

### The challenge

**Design a better discriminator, and prove it offline.**

The growth term is doing a job it is bad at: separating feedback from music. Consider what actually
differs, physically:

- A musical note at *f* has a **harmonic family** — energy at 2*f*, 3*f*, 4*f*. Acoustic feedback
  sits at a single frequency set by the room/mic/speaker response peak and has **no harmonic
  family**. This is checkable in a *single frame* and needs no temporal history at all.
- Feedback is **frequency-stable** to a fraction of a semitone; music moves.
- Feedback is **extremely narrow-band**; notes carry vibrato and attack transients.
- Feedback **outlasts** any musical note.

The RTA is 100 bands at 1/10 octave over 20 Hz–20 kHz (`10000·2^((i−90)/10)` Hz), ~20 frames/s,
values in dB. That is enough resolution to test harmonic structure directly.

Specific questions:

1. Can a harmonic-structure test replace the growth term outright? What does it cost in latency
   (growth needs ≥ 3 frames; a harmonic check needs 1)?
2. Is the weighted-sum-of-normalised-scores shape right at all, or should this be a small decision
   procedure with explicit physical predicates?
3. A frequency window (~250 Hz–8 kHz) would have prevented both false positives outright, since
   vocal-mic feedback does not occur below a few hundred Hz. Is that a legitimate physical prior
   or a hack that will fail on a kick-drum mic or a bass cab? What are the real bounds, and what
   determines them?
4. What is the right behaviour when the discriminator is *uncertain*? Cutting is destructive but
   bounded (−3 dB, cuts only, max −9 dB); not cutting risks a howl. Where should that asymmetry
   sit, and should it change between `feedback_watch` (human on the fader) and `ring_out`
   (server driving the gain)?

### How to validate without hardware

`src/x32mcp/meters.py` has `SyntheticRta` (injectable rings, notes, per-band attenuation) and
`tests/test_detector.py` drives the detector frame-by-frame with scripted streams. Both are
deterministic. **Build the streams the current tests are missing** — that gap is the actual root
cause of tonight's failures:

- music with a bass line and harmonics, at realistic levels
- a ring that is *already established* when the detector arms (no onset to observe)
- a ring emerging *during* music
- a sustained vocal note with vibrato, and a held guitar note with a full harmonic series
- the operator raising the master (every band rises together — does your discriminator care?)

Success criterion: **zero false positives on the music/note streams, and detection of every ring
within ~300 ms**, without a growth term doing the heavy lifting.

---

## 2. Find the assumptions baked into the tests

The deeper lesson from real-hardware testing was not any single bug: it was that **every synthetic
stream modelled a ring growing from a quiet background**, which is the easy case, and the tests
therefore certified a detector that could not handle the normal one.

Audit `tests/` for the same class of error elsewhere. Where does a fixture encode an assumption
about the world that reality need not honour? Some known examples to calibrate against, all of
which passed their tests:

- levels were assumed never to be exactly −∞ (a real desk has strips at −∞ constantly)
- a channel was assumed to map to head amp N−1 (firmware-4.x user routing breaks this)
- `set_rta_source` was assumed to leave the analyser in a usable state (auto-gain was on)
- a GEQ insert was assumed to be visible to the RTA (POST inserts are not)

What else is like this? You cannot test against hardware, but you can read
[`docs/research/`](research/) — which documents real-desk behaviour in detail, including items
explicitly marked `UNCONFIRMED` — and find places where the code assumes something the research
does not support.

---

## 3. Adversarial review of the safety layer

`src/x32mcp/policy.py` is the guardrail, and the threat model is an LLM that is confused,
jailbroken, or in a runaway loop. **Try to get something dangerous past it.** In particular:

- Can any write reach the desk without passing `Policy`? (`grep` for `conn.set` / `send_raw`
  outside the desk facade.)
- Can a confirmation token be reused, forged, or made to authorise a different action?
- Can `show_mode` be circumvented? Can a clamp be widened by argument choice?
- `panic()` deliberately bypasses the rate limiter and show mode. Is there any state in which it
  fails to silence the outputs?
- CFS² writes EQ cuts autonomously. Can it be induced to boost, to exceed −9 dB, to write to a
  strip other than the one under test, or to keep writing after the budget is spent?

A finding here matters more than anywhere else in the repo: everything else is recoverable from a
snapshot, and this is the part that protects someone's hearing.

---

## 4. One unresolved bug, bounded and concrete

`tests/integration/test_server_tools.py::test_cfs_tools_with_injected_synthetic_rta` asserts
`"already carries" in pend["action_summary"]` — that `ring_out`'s preflight reports a notch left by
an earlier `feedback_watch`. Adding arm-time noise-floor calibration to `CfsManager` made that
assertion fail while every other assertion in the test (including the notch being applied at the
right band and depth) continued to pass. `existing_cuts` came back empty.

I backed the calibration out rather than ship a change I did not understand into the signal path.
The attempt is described in `HANDOVER.md` §4b and recoverable from the git history around commit
`60b86d5`. **Root-cause it.** The calibration itself is worth having — a fixed candidate gate
cannot be right for a studio, a pub and a festival field — but it must run after the frame source
starts, needs a cap so a loud room cannot deafen the detector, and must not break that reporting
path.

---

## 5. A systemic issue worth a proper fix

Several places read a value back immediately after writing it, over fire-and-forget UDP, and draw
a conclusion from the stale answer:

- `setup_ringout_eqs` validates before the desk has applied `insert/on`, so **a successful setup
  reports `GEQ_VALIDATION_FAILED`**
- a read straight after `label_channel` returned the previous name

Is there a general answer — a write barrier, a read-your-writes token, using the desk's `/` echo as
an acknowledgement (see `docs/research/transport.md` §6.6) — rather than sprinkling settles?

---

## What not to spend time on

Coverage is already heavy (934 tests) and style is consistent. Generic code review, more tests of
already-tested paths, and refactoring for its own sake are not useful here. The value is in the
places tests cannot reach: wrong beliefs about the physical world, and the discriminator.

## Be skeptical of the existing work

Much of this repo, including this brief, was written by a less capable model. Specific things to
doubt:

- The fader taper, meter blob layout and node text formats in `scales.py` / `meters.py` /
  `nodes.py` are derived from reverse-engineered documentation. `docs/research/` cites sources —
  check the code against them rather than trusting either.
- The prominence override (25 dB / 6 frames) and the −45 dB candidate gate are **single-room
  numbers fitted to one evening's observations**. The −45 dB figure was derived from a noise floor
  that turned out to be background music, i.e. it is right for the wrong reason.
- `DESIGN.md` is a contract written before any of the code existed. Where it and reality disagree,
  reality wins and the document should be corrected.
