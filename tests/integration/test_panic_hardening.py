"""panic() must not be undone by the server's own writers (ramps, restore, CFS²), latches what it
muted against Tier-1 re-opening, and is re-asserted on reconnect when it went out unconfirmed.

Every scenario here was a reproduced red-team finding against the previous behaviour: a 60 s ramp
kept walking a fader after the panic; a confirmed restore_snapshot in flight re-opened all 24
outputs within 0.3 s; a ring_out kept raising the (muted) bus to its 0 dB target and reported DONE;
and any output could be re-opened straight away with a Tier-1 unmute."""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from conftest import wait_for_state, wait_until
from x32mcp import server as srv
from x32mcp.connection import ConnectionState
from x32mcp.descriptor import Descriptor
from x32mcp.meters import SyntheticRta, rta_band_hz

from test_server_tools import assert_err, assert_pending, fset, make_app, settle

OUTS = ("/main/st/mix/on", "/bus/01/mix/on", "/bus/16/mix/on", "/mtx/06/mix/on")


@pytest_asyncio.fixture
async def app(fakedesk, tmp_path):
    a = await make_app(fakedesk, tmp_path)
    try:
        yield a
    finally:
        await a.close()
        srv.app = None


def all_muted(fakedesk) -> bool:
    return all(fakedesk.get(a) == 0 for a in OUTS)


async def test_panic_cancels_a_running_fader_ramp(app, fakedesk):
    fset(app, fakedesk, "/ch/01/mix/fader", -60.0)
    ramp = asyncio.create_task(srv.set_fader("ch.1", 0.0, ramp_ms=3000))
    await wait_until(lambda: (fakedesk.value("/ch/01/mix/fader") or -90) > -55.0, timeout=3.0, what="ramp under way")
    p = await srv.panic()
    assert p["ok"] and p["cancelled_ramps"] == 1 and p["latched"] is True
    await settle(app)
    level_at_panic = fakedesk.value("/ch/01/mix/fader")
    await asyncio.sleep(0.5)
    assert fakedesk.value("/ch/01/mix/fader") == pytest.approx(level_at_panic, abs=0.01), "the ramp kept writing after panic"
    res = await ramp  # the tool call returns (superseded), it does not hang or raise
    assert res["ok"] and res.get("superseded") is True
    assert all_muted(fakedesk)


async def test_panic_stops_a_restore_in_flight_and_latches_restore(app, fakedesk, tmp_path):
    snap = await srv.snapshot_desk("before")
    assert snap["ok"]
    # change a lot of sections so the restore has many lines to write, and slow the desk's echo so it spans the panic
    for ch in range(1, 33):
        fset(app, fakedesk, f"/ch/{ch:02d}/mix/fader", -30.0 - ch * 0.5)
        fset(app, fakedesk, f"/ch/{ch:02d}/config/name", f"x{ch}")
    for b in range(1, 17):
        fset(app, fakedesk, f"/bus/{b:02d}/mix/on", 0)  # muted now; the snapshot has them ON
    token = assert_pending(await srv.restore_snapshot(snap["id"]))
    fakedesk.latency_ms = 6
    restore = asyncio.create_task(srv.restore_snapshot(snap["id"], confirm_token=token))
    await wait_until(lambda: fakedesk.value("/ch/03/config/name") != "x3" or restore.done(), timeout=15.0, what="restore under way")
    assert not restore.done(), "the restore finished before the panic could interrupt it (test needs a slower desk)"
    p = await srv.panic()
    assert p["ok"]
    res = await restore
    fakedesk.latency_ms = 0
    assert res["ok"] and "panic" in (res.get("aborted") or ""), res
    assert res["written"] < res["lines"]
    await settle(app)
    await asyncio.sleep(0.3)
    assert all_muted(fakedesk), "the restore re-opened outputs after the panic"
    # a fresh restore is refused while the latch is set, even with a valid token dance
    assert_err(await srv.restore_snapshot(snap["id"]), "PANIC_LATCHED")


async def test_latch_blocks_tier1_unmute_until_clear_panic_is_confirmed(app, fakedesk):
    p = await srv.panic()
    assert p["ok"] and p["latched"]
    assert_err(await srv.unmute("bus.3"), "PANIC_LATCHED")
    assert_err(await srv.unmute("mtx.2"), "PANIC_LATCHED")
    un = await srv.unmute("ch.5")  # inputs were never touched by panic and are not latched
    assert un["ok"]
    # the main can be re-opened through its own confirmed tool, which releases just that key
    tok = assert_pending(await srv.set_main_mute("st", False))
    ok = await srv.set_main_mute("st", False, confirm_token=tok)
    assert ok["ok"] and ok["muted"] is False
    assert "main.st" not in app.desk.panic_latched and "bus.3" in app.desk.panic_latched
    assert_err(await srv.unmute("bus.3"), "PANIC_LATCHED")
    # clear_panic: confirmation dance, unmutes nothing, then Tier-1 unmute works again
    pend = await srv.clear_panic()
    tok = assert_pending(pend)
    assert "Release the panic latch" in pend["action_summary"] and "Nothing is unmuted" in pend["action_summary"]
    assert_err(await srv.clear_panic(confirm_token="nope"), "BAD_TOKEN")
    tok = assert_pending(await srv.clear_panic())
    done = await srv.clear_panic(confirm_token=tok)
    assert done["ok"] and "bus.3" in done["released"] and done["latched"] == []
    await settle(app)
    assert fakedesk.get("/bus/03/mix/on") == 0  # still muted: clearing the latch is not an unmute
    un = await srv.unmute("bus.3")
    assert un["ok"]
    await settle(app)
    assert fakedesk.get("/bus/03/mix/on") == 1
    nothing = await srv.clear_panic()
    assert nothing["ok"] and nothing["released"] == []


async def test_panic_aborts_a_running_ring_out_and_returns_the_master(fakedesk, tmp_path):
    rta = SyntheticRta(seed=4, period_s=0.05, band_hz=rta_band_hz(Descriptor.load()))
    a = await make_app(fakedesk, tmp_path, frames=rta)
    try:
        token = assert_pending(await srv.setup_ringout_eqs([1]))
        done = await srv.setup_ringout_eqs([1], confirm_token=token)
        assert done["ok"]
        for ch in (1, 2):
            fset(a, fakedesk, f"/ch/{ch:02d}/mix/01/level", -20.0)
        fset(a, fakedesk, "/bus/01/mix/fader", -30.0)
        pend = await srv.ring_out(1, target_gain_db=-5.0, dwell_ms=150)
        token = assert_pending(pend)
        run = asyncio.create_task(srv.ring_out(1, target_gain_db=-5.0, dwell_ms=150, confirm_token=token))
        await wait_until(lambda: (fakedesk.value("/bus/01/mix/fader") or -90) >= -27.0, timeout=5.0, what="ring-out raising the master")
        p = await srv.panic()
        assert p["ok"]
        at_panic = fakedesk.value("/bus/01/mix/fader")
        rep = await asyncio.wait_for(run, timeout=10.0)
        assert rep["ok"] and rep["final_stage"] == "ABORT" and rep["aborted"] is True
        assert "panic" in rep["abort_reason"]
        await settle(a)
        end = fakedesk.value("/bus/01/mix/fader")
        assert end <= -30.0 + 0.05, f"master parked at {end} dB after a panic abort (started at -30)"
        assert rep["max_master_db"] <= at_panic + 1.0 + 0.05, "the ring-out raised the bus after the panic"
        assert fakedesk.get("/bus/01/mix/on") == 0
        assert (await srv.cfs_status())["mode"] == "idle"
    finally:
        await a.close()
        srv.app = None


async def test_unconfirmed_panic_is_reasserted_when_the_desk_comes_back(app, fakedesk):
    fakedesk.silent = True
    await wait_for_state(app.conn, ConnectionState.DEGRADED, timeout=8.0)
    p = await asyncio.wait_for(srv.panic(), timeout=5.0)
    assert p["ok"] and p["delivered"] == "unconfirmed" and p["reassert_pending"] is True
    # the desk never saw those datagrams (it was deaf); un-deafen it and undo whatever raced through
    fakedesk.silent = False
    for a in OUTS:
        fakedesk.set_value(a, 1)
    await wait_for_state(app.conn, ConnectionState.CONNECTED, timeout=8.0)
    await wait_until(lambda: all_muted(fakedesk), timeout=3.0, what="panic re-asserted after reconnect")
    assert app.desk._panic_reassert is False


async def test_panic_reads_the_mutes_back_and_resends_a_lost_one(app, fakedesk, monkeypatch):
    """A SET has no ack: 'sent' is not 'muted'. After the sends, panic reads the 24 mix/on back;
    an output that still reads open (a lost datagram) is sent again and the result says so."""
    real_set = app.conn.set
    dropped = {"n": 0}

    async def lossy(address, *args, **kw):
        if address == "/bus/07/mix/on" and dropped["n"] == 0:
            dropped["n"] += 1  # this one datagram never reaches the desk
            app.conn.invalidate(address)
            return
        return await real_set(address, *args, **kw)

    monkeypatch.setattr(app.conn, "set", lossy)
    p = await srv.panic()
    assert p["ok"] and p["delivered"] == "confirmed" and p["resent"] == ["bus.7"] and p["confirmed"] == 24
    assert "read back muted" in p["summary"]
    await settle(app)
    assert fakedesk.get("/bus/07/mix/on") == 0
    # opt-out keeps the fire-and-forget contract
    q = await app.desk.panic(verify_s=0)
    assert q["delivered"] == "sent" and "confirmed" not in q


async def test_unmute_in_flight_when_panic_fires_does_not_land_afterwards(app, fakedesk):
    """unmute(bus.1) is parked inside the pre-write snapshot (a full dump) when panic() runs; it must
    not complete afterwards and re-open bus 1."""
    fakedesk.set_value("/bus/01/mix/on", False)
    app.desk.invalidate()
    assert app.policy.snapshot_before_write is True  # the first write of the session pays for the dump
    task = asyncio.create_task(srv.unmute("bus.1"))
    await asyncio.sleep(0.01)
    assert not task.done()
    res = await srv.panic()
    assert res["ok"]
    res2 = await task
    await app.conn.get("/-stat/selidx")
    assert res2["ok"] is False and res2["error"]["code"] == "PANIC_LATCHED", res2
    assert fakedesk.get("/bus/01/mix/on") == 0
