---
name: verify
description: Drive a CFS² session end to end against the in-process FakeDesk (watch: ring injected -> notch written -> ring killed -> detector verdict) and read the operator-facing outputs. Use before committing changes to detector.py, cfs.py, meters.py or the GEQ/notch path.
---

# Verify CFS² changes by running a session, not the tests

The product surface is an MCP server (`x32-mcp`) talking OSC/UDP to a console; the repo ships a console emulator (`x32-fakedesk`).
With sockets available, run both and call the tools (README "Try it without a console"):

```bash
.venv/bin/x32-fakedesk --port 10023 --ring 2400 --ring-growth 20 &
X32_HOST=127.0.0.1 X32_PORT=10023 .venv/bin/x32-mcp        # then call setup_ringout_eqs / feedback_watch / stop_cfs from the client
```

In a sandbox that forbids IP sockets, drive the same stack in-process — `scripts/verify_watch_fakedesk.py` loads the integration
suite's in-memory UDP loopback (`tests/conftest.py`, installs itself only when binding 127.0.0.1 is refused) and runs
CfsManager.feedback_watch against FakeDesk exactly as cfs does on the desk:

```bash
.venv/bin/python scripts/verify_watch_fakedesk.py
```

What to look at: `cfs.notch` events (time, GEQ band, depth), the fake analyser's ring/note levels after the cut, and the session
report's `detector` block (`flags`, `release_db_per_s`, `arm_p95_db`, `cut_verdicts`: confirmed / insufficient / held / false_cut).
Expected today: scenario A (20 dB/s ring) one −3 dB cut within ~1 s, verdict `confirmed`, ring at the floor; scenario B (held −6 dBFS
line) −3 → −6 → −9 at ~1.5 s spacing, each `held`, then no further writes (notch_max). Scenarios C–E drive the policy layer
(docs/CFS_POLICY.md) on a quiet fake desk: C tier B on a −18 dBFS held line (−3/−6/−9 tagged tier B, strip GN→RDi→GN), D the held
line masked by a loud passage (engagement ends `lost`, NOT ignore-listed, re-engaged) then released by hand on the desk (`operator`,
ignore-listed, never re-cut), E a ring_out whose back-off probe is interrupted by a ring taking off (probe `interrupted`, ring cut
within a frame, run DONE); the script asserts these and ends with `ALL SCENARIOS OK` (exit 0). The fake's SyntheticRta cannot model a
limiter-held howl with excess above the bell; the rtasim corpus can: `PYTHONPATH=src:tests .venv/bin/python -m rtasim.run_verifier_breakers
AV05_howl_e4_limiter_on_geq_centre_watch` (closed loop, prints cuts, verdicts and whether the ring is alive at the end).

Gotchas: `uv` may crash in restricted sandboxes — call `.venv/bin/python` directly; the webui/TCP tests cannot run without sockets
(`--deselect tests/test_webui.py --deselect tests/integration/test_server_tools.py::test_dashboard_status`).
