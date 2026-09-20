"""FakeDesk driven through a real X32Connection over loopback UDP (DESIGN.md §18, §20).

Every assertion here is about behaviour the real console has (docs/research/*.md); the fake
desk is only as useful as it is faithful.
"""

from __future__ import annotations

import asyncio
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import wait_for_state, wait_until
from x32mcp.connection import ConnectionState, NotConnected, RequestTimeout, X32Connection
from x32mcp.events import EventBus
from x32mcp.fakedesk import FakeDesk
from x32mcp.meters import RTA_METER_TYPE, LiveMeters, average_frames, parse_meter_blob, set_rta_source
from x32mcp.nodes import dump_desk_state, parse_node_line, split_node_line, tokenize
from x32mcp.scales import quantize
from x32mcp.targets import Target

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------------------- identity


async def test_connect_info_xinfo_status(conn, fakedesk):
    c = conn.status.console
    assert (c.name, c.model, c.firmware, c.server_version) == ("X32-FAKE", "X32RACK", "4.06", "V2.07")
    assert conn.state is ConnectionState.CONNECTED
    info = await conn.request("/info")
    assert info.typetags == ",ssss" and info.args == ("V2.07", "osc-server", "X32RACK", "4.06")  # transport.md §3.1
    xinfo = await conn.request("/xinfo")
    assert xinfo.typetags == ",ssss" and xinfo.args == ("127.0.0.1", "X32-FAKE", "X32RACK", "4.06")  # §3.2
    status = await conn.request("/status")
    assert status.args == ("active", "127.0.0.1", "osc-server")  # §3.3
    assert conn.local_addr[1] != fakedesk.port
    assert fakedesk.stats["rx"] >= 3


async def test_discover_via_unicast_loopback(conn, fakedesk):
    found = await conn.discover(timeout_s=0.5, port=fakedesk.port)
    hits = [c for c in found if c.host == "127.0.0.1" and c.port == fakedesk.port]
    assert hits and hits[0].name == "X32-FAKE" and hits[0].model == "X32RACK" and hits[0].server_version == ""


# ---------------------------------------------------------------------------------------- get / set


async def test_get_set_roundtrip_and_quantisation(conn, fakedesk, descriptor):
    fader = descriptor.scale("fader")
    assert await conn.get("/ch/01/mix/fader") == pytest.approx(fader.to_raw(0.0), abs=1e-6)  # 0 dB default
    await conn.set("/ch/01/mix/fader", 0.5001)
    v = await conn.get("/ch/01/mix/fader")
    assert v == pytest.approx(quantize(0.5001, 1024), abs=1e-6)  # 1024-step grid (scales_params.md §2.3)
    assert abs(v - 0.5001) > 1e-5
    assert fakedesk.get("/ch/01/mix/fader") == pytest.approx(v, abs=1e-7)
    assert fakedesk.value("/ch/01/mix/fader") == pytest.approx(-10.0, abs=0.05)
    await conn.set("/ch/01/mix/03/level", 0.7001)
    assert await conn.get("/ch/01/mix/03/level") == pytest.approx(quantize(0.7001, 161), abs=1e-6)  # 161 grid
    await conn.set("/ch/01/eq/1/f", 0.2651)
    assert await conn.get("/ch/01/eq/1/f") == pytest.approx(quantize(0.2651, 201), abs=1e-6)  # 201 steps
    await conn.set("/ch/01/mix/pan", 0.751)
    assert await conn.get("/ch/01/mix/pan") == pytest.approx(0.75, abs=1e-6)
    # ints, bools and strings
    await conn.set("/ch/01/mix/on", 0)
    assert await conn.get("/ch/01/mix/on") == 0
    await conn.set("/ch/01/config/name", "Vox Tony")
    assert await conn.get("/ch/01/config/name") == "Vox Tony"
    await conn.set("/ch/01/config/color", 6)
    assert await conn.get("/ch/01/config/color") == 6
    # enums may be set as strings (transport.md §5.2) and read back as ,i
    await conn.set("/ch/01/gate/mode", "DUCK")
    assert await conn.get("/ch/01/gate/mode") == 4
    # an out-of-range enum index is ignored, like the desk
    await conn.set("/ch/01/gate/mode", 99)
    assert await conn.get("/ch/01/gate/mode") == 4
    assert fakedesk.value("/ch/01/gate/mode") == "DUCK"


async def test_unknown_address_is_silent(conn):
    t0 = time.monotonic()
    with pytest.raises(RequestTimeout):
        await conn.get("/ch/99/mix/fader")  # transport.md §5.3: no error replies
    assert time.monotonic() - t0 < 1.5


async def test_multi_arg_set_on_node_address(conn, descriptor):
    await conn.set("/ch/01/eq/1", 2, 0.265, 0.5, 0.4648)  # DOC 604-616 (transport.md §1.4)
    assert await conn.get("/ch/01/eq/1/type") == 2
    assert await conn.get("/ch/01/eq/1/f") == pytest.approx(quantize(0.265, 201), abs=1e-6)
    assert await conn.get("/ch/01/eq/1/g") == pytest.approx(0.5, abs=1e-6)
    assert await conn.get("/ch/01/eq/1/q") == pytest.approx(quantize(0.4648, 72), abs=1e-6)
    # a whole GEQ block in one datagram (fx_routing_scenes.md §1.6)
    await conn.slash("/fx/6 GEQ2")
    await conn.send_raw("/fx/6/par", *([0.4] * 64), typetags="f" * 64)
    assert await conn.get("/fx/6/par/33") == pytest.approx(0.4, abs=1e-6)
    toks = tokenize(await conn.node("/fx/6/par"))[1:]
    assert len(toks) == 64 and toks[0] == "-3.0"
    # a typed GET on a container answers every field (GetSceneName.c, fx_routing_scenes.md §6.2)
    msg = await conn.request("/ch/01/eq/1")
    assert msg.typetags == ",ifff" and msg.args[0] == 2


# ---------------------------------------------------------------------------------------- node sweep


async def test_every_node_path_renders_and_parses(conn, descriptor):
    nodes = descriptor.nodes()
    paths = [n.path for n in nodes]
    t0 = time.perf_counter()
    lines = await conn.node_many(paths, concurrency=16)
    dt = time.perf_counter() - t0
    print(f"\nnode sweep: {len(paths)} paths in {dt:.2f} s")
    assert [p for p in paths if lines[p] is None] == []
    for n in nodes:
        line = lines[n.path]
        assert line.startswith(n.path + " "), line
        values = parse_node_line(line, n, strict=True)  # zero errors
        assert [k for k, v in values.items() if v is None] == [], (n.path, line)
    assert lines["/ch/01/config"] == '/ch/01/config "Ch01" 1 RD 1'
    assert lines["/ch/01/mix"] == "/ch/01/mix ON   0.0 ON +0 OFF   -oo"  # console padding, transport.md §6.3
    assert lines["/-stat/rtasource"] == "/-stat/rtasource 168"
    assert lines["/-show/showfile/show"].endswith(' "4.06"')


async def test_dump_desk_state_complete_and_fast(conn, descriptor):
    t0 = time.perf_counter()
    state = await dump_desk_state(conn, descriptor)
    dt = time.perf_counter() - t0
    print(f"\ndump_desk_state: {len(state.sections)} sections in {dt:.2f} s")
    assert state.missing == []
    assert dt < 5.0
    assert len(state.sections) == len(descriptor.nodes())
    assert state.scene == {"index": 0, "name": "Init"}
    assert state.console["name"] == "X32-FAKE"
    assert state.get("/ch/05/config/name") == "Ch05"
    assert state.get("/ch/01/mix/fader") == pytest.approx(0.0, abs=0.05)
    assert state.get("/bus/03/insert/sel") == "OFF"
    assert state.get("/fx/5/type") == "DES2" and state.get("/fx/1/type") == "HALL"
    assert state.get("/-prefs/rta/source") == "MAIN" and state.get("/-stat/rtasource") == 168
    assert state.get("/-show/showfile/scene/001/name") == "The Molecules"


# ---------------------------------------------------------------------------------------- / writes

# Verbatim console lines (transport.md §6.3 / tests/test_nodes.py VERBATIM): written with `/`, read
# back with /node, compared field by field (grid rounding tolerated).
GOLDEN = [
    '/ch/01/config "Diazno" 1 CY 1',
    "/ch/01/delay OFF   0.3",
    "/ch/01/preamp +0.0 OFF OFF 24  79",
    "/ch/01/gate ON EXP4 -46.5 27.0 20  100  576 0",
    "/ch/01/gate/filter OFF 3.0 1k39",
    "/ch/01/dyn ON COMP PEAK LIN -26.0 3.0 2 8.00 71 0.03  538 POST 0 100 OFF",
    "/ch/01/insert ON PRE FX4L",
    "/ch/01/eq/1 PEQ 164.4 -3.75 1.8",
    "/ch/01/mix ON  +2.1 ON +0 OFF   -oo",
    "/ch/01/mix/01 ON   -oo +0 PRE",
    "/ch/01/grp %00000001 %000000",
    "/bus/01/mix OFF   0.0 OFF +0 OFF -81.0",
    "/main/st/mix ON   -oo +0",
    "/dca/1 OFF  -8.3",
    "/headamp/000 +0.0 OFF",
    "/config/mute OFF OFF OFF OFF OFF OFF",
    "/-prefs/rta 50% 24 ON 72 POST BAR %000100 RMS 1.00 OFF",
    "/fx/1/source MIX15 MIX15",
    "/fx/5 GEQ2",
]


@pytest.mark.parametrize("line", GOLDEN, ids=[g.split(" ")[0] for g in GOLDEN])
async def test_golden_console_lines_write_and_read_back(conn, descriptor, line):
    await conn.slash(line)  # resolves on the verbatim echo (transport.md §6.6)
    path, _ = split_node_line(line)
    node = descriptor.node(path)
    back = await conn.node(path)
    want, got = parse_node_line(line, node), parse_node_line(back, node)
    for key, v in want.items():
        if v is None:
            continue
        g = got[key]
        if isinstance(v, float) and not isinstance(v, bool):
            if math.isinf(v):
                assert g == v, (key, back)
            else:
                assert g == pytest.approx(v, rel=0.02, abs=0.06), (key, back)
        else:
            assert g == v, (key, back)


async def test_slash_write_forms(conn, fakedesk, descriptor):
    fader = descriptor.scale("fader")
    await conn.slash("ch/02/mix/fader -20.5")  # single leaf, no leading slash (DOC 709)
    assert await conn.get("/ch/02/mix/fader") == pytest.approx(fader.to_raw(-20.5), abs=1e-6)
    await conn.slash('/ch/03/config "Vox Tony" 5 RD')  # partial trailing list: source untouched
    assert await conn.get("/ch/03/config/name") == "Vox Tony"
    assert await conn.get("/ch/03/config/icon") == 5
    assert await conn.get("/ch/03/config/color") == 1
    assert await conn.get("/ch/03/config/source") == 3
    await conn.slash("ch/04 Kick 10 CY 1")  # DOC 4585: strip root aliases its config node
    assert await conn.get("/ch/04/config/name") == "Kick" and await conn.get("/ch/04/config/icon") == 10
    await conn.slash("/ch/01/mix/fader -85.4")  # DOC 4566: snapped to the grid, reads back -85.3
    assert fakedesk.value("/ch/01/mix/fader") == pytest.approx(-85.3, abs=0.05)
    await conn.slash("/no/such/node 1 2 3")  # still echoed (flow control), nothing applied
    assert await conn.node("/ch/04/config") == '/ch/04/config "Kick" 10 CY 1'


# ---------------------------------------------------------------------------------------- xremote


async def test_xremote_push_reaches_other_clients_not_the_writer(conn, conn_factory, fakedesk):
    other = await conn_factory()
    got_other: list[tuple[str, tuple]] = []
    got_self: list[tuple[str, tuple]] = []
    other.on_update(lambda a, args: got_other.append((a, args)))
    conn.on_update(lambda a, args: got_self.append((a, args)))
    await wait_until(lambda: len(fakedesk.clients) == 2, what="both /xremote registrations")  # sent by the heartbeat task

    await conn.set("/ch/05/mix/fader", 0.5)
    await wait_until(lambda: any(a == "/ch/05/mix/fader" for a, _ in got_other), what="push to other client")
    args = next(args for a, args in got_other if a == "/ch/05/mix/fader")
    snapped = quantize(0.5, 1024)  # 512/1023: the push carries the desk's own grid value
    assert args[0] == pytest.approx(snapped, abs=1e-6)
    await asyncio.sleep(0.2)
    assert not any(a == "/ch/05/mix/fader" for a, _ in got_self)  # transport.md §4.2: never to the writer
    assert await other.get_cached("/ch/05/mix/fader") == pytest.approx(snapped, abs=1e-6)

    # node-style writes push every changed leaf (transport.md §6.6)
    await conn.slash("/ch/06/mix OFF -20.0")
    await wait_until(lambda: {"/ch/06/mix/on", "/ch/06/mix/fader"} <= {a for a, _ in got_other}, what="leaf pushes")
    assert not any(a.startswith("/ch/06/") for a, _ in got_self)

    # an unchanged write is not pushed (emulator EPSILON rule)
    n = len(got_other)
    await conn.set("/ch/05/mix/fader", 0.5)
    await asyncio.sleep(0.2)
    assert len(got_other) == n

    # a desk-side change (front panel) reaches everyone, incl. mute inversion semantics on the wire
    fakedesk.set_value("/ch/07/mix/on", False)
    await wait_until(lambda: ("/ch/07/mix/on", (0,)) in got_self and ("/ch/07/mix/on", (0,)) in got_other, what="desk push")

    # /-stat/rtasource mirrors a prefs write and is pushed too (meters.md §5.2)
    await other.set("/-prefs/rta/source", 52)
    await wait_until(lambda: ("/-stat/rtasource", (148,)) in got_self, what="rtasource mirror push")

    # the lease lapses: after expiry nothing is pushed any more
    fakedesk.xremote_lease_s = 0.3
    await other.close()
    await asyncio.sleep(0.4)
    await conn.set("/ch/08/mix/fader", 0.25)
    await asyncio.sleep(0.2)
    assert not any(a == "/ch/08/mix/fader" for a, _ in got_other)


# ---------------------------------------------------------------------------------------- meters


async def test_meter_frames_decode(conn, fakedesk):
    rta = LiveMeters(conn, RTA_METER_TYPE)
    await rta.start()
    try:
        frame = await average_frames(rta, 3, timeout_s=2.0)
        assert frame is not None and frame.meter_type == 15 and len(frame.values) == 100
        assert all(-128.0 <= v <= 0.0 for v in frame.values)
        assert rta.frames_received >= 3 and rta.parse_errors == 0
        assert frame.values[fakedesk.rta.band_for_hz(100.0)] > frame.values[90]  # pink-ish slope
    finally:
        await rta.stop()
    chans = LiveMeters(conn, 0)
    await chans.start()
    try:
        f = await average_frames(chans, 2, timeout_s=2.0)
        assert f is not None and len(f.values) == 70 and all(v > 0.0 for v in f.values)
        assert f.values[0] > 0.1  # ch 1 at 0 dB, unmuted
        fakedesk.set("/ch/02/mix/on", 0)
        f2 = await average_frames(chans, 2, timeout_s=2.0)
        assert f2 is not None and f2.values[1] < 1e-4  # muted: silence level (meters.md §6.3)
    finally:
        await chans.stop()
    # ,sii /meters/6 <channel> <tf> (meters.md §1.1) and the raw datagram decodes byte for byte
    blobs: list[tuple[str, bytes]] = []
    unsub = conn.on_blob(lambda a, b: blobs.append((a, b)))
    await conn.send_raw("/meters", "/meters/6", 4, 1)
    await wait_until(lambda: any(a == "/meters/6" for a, _ in blobs), what="/meters/6 blob")
    unsub()
    a, b = next(x for x in blobs if x[0] == "/meters/6")
    assert len(parse_meter_blob(a, b, ts=0.0).values) == 4
    assert len(b) == 4 + 4 * 4


async def test_gain_reduction_words_are_unity_not_levels(conn, fakedesk):
    """meters.md §3: the GR words carry the linear gain *applied*, 1.0 = no reduction — not
    levels. §2.2's verbatim desk capture of /meters/6 reads level, 0.99999982, 1.0, level."""
    fakedesk.set("/ch/02/mix/on", 0)  # muted: its post-fade level must bottom out

    async def frame(mtype: int, *args: int):
        src = LiveMeters(conn, mtype, extra_args=args)
        await src.start()
        try:
            f = await average_frames(src, 2, timeout_s=2.0)
        finally:
            await src.stop()
        assert f is not None
        return f.values

    # /meters/1: [0-31] ch levels, [32-63] gate GR, [64-95] dyn GR
    v = await frame(1)
    assert len(v) == 96 and v[0] > 0.1
    assert all(g == pytest.approx(1.0) for g in v[32:]), "GR range must be unity gain, not levels"
    # /meters/2: [0-24] bus/mtx/main levels, [25-48] their dyn GR
    v = await frame(2)
    assert len(v) == 49 and v[0] > 0.1 and v[22] > 0.1
    assert all(g == pytest.approx(1.0) for g in v[25:])
    # /meters/6: [0] pre-fade level, [1] gate GR, [2] dyn GR, [3] post-fade level
    v = await frame(6, 1)  # channel_id 1 = Ch 02, muted above
    assert len(v) == 4
    assert v[1] == pytest.approx(1.0) and v[2] == pytest.approx(1.0)
    assert v[0] > 0.1, "pre-fade level ignores the fader and the mute"
    assert v[3] < 1e-4, "post-fade level follows the mute (meters.md §6.3: silence, never 0.0)"


async def test_meter_lease_renew_unsubscribe(conn, fakedesk):
    fakedesk.meter_lease_s = 0.5
    count = 0

    def on_blob(a: str, b: bytes) -> None:
        nonlocal count
        if a == "/meters/15":
            count += 1

    unsub = conn.on_blob(on_blob)
    try:
        # meters.md §1.1 (VERIFIED): the console matches the leading-slash form only, and so
        # do we — the slash-less spelling buys no subscription at all.
        await conn.send_raw("/meters", "meters/15", 2)
        await asyncio.sleep(0.25)
        assert count == 0 and not fakedesk.meters
        await conn.send_raw("/meters", "/meters/15", 2)  # tf 2 -> 100 ms (meters.md §1.2)
        await wait_until(lambda: count >= 2, what="frames")
        await asyncio.sleep(0.7)
        n = count
        await asyncio.sleep(0.3)
        assert count == n  # lease lapsed after 0.5 s, no keep-alive (meters.md §1.3)
        assert not fakedesk.meters
        # a bare /renew extends an active lease (meters.md §1.3)
        await conn.send_raw("/meters", "/meters/15", 2)
        await asyncio.sleep(0.25)
        await conn.send_raw("/renew")
        await asyncio.sleep(0.35)  # past the original expiry
        n = count
        await asyncio.sleep(0.15)
        assert count > n
        # /unsubscribe ,s /meters/15 stops the stream but keeps the xremote registration
        await conn.send_raw("/unsubscribe", "/meters/15")
        await asyncio.sleep(0.15)
        n = count
        await asyncio.sleep(0.25)
        assert count == n
        assert conn.local_addr[1] in {port for _, port in fakedesk.clients}
        # /batchsubscribe answers on the alias (transport.md §7.3)
        aliased: list[bytes] = []
        unsub2 = conn.on_blob(lambda a, b: aliased.append(b) if a == "/yy" else None)
        await conn.send_raw("/batchsubscribe", "/yy", "/meters/6", 0, 0, 1)
        await wait_until(lambda: aliased, what="alias blob")
        unsub2()
        assert len(parse_meter_blob("/yy", aliased[0], ts=0.0, meter_type=6).values) == 4
    finally:
        unsub()


# ---------------------------------------------------------------------------------------- RTA source


async def test_rta_source_mirror_and_set_rta_source(conn, descriptor):
    await conn.set("/-prefs/rta/source", 52)  # Bus 3
    await conn.set("/-prefs/rta/pos", 1)
    assert await conn.get("/-stat/rtasource") == 148  # meters.md §5.2: 146 + N - 1 post-EQ
    await conn.set("/-prefs/rta/pos", 0)
    assert await conn.get("/-stat/rtasource") == 50
    await conn.set("/-prefs/rta/source", 1)  # Monitor
    assert await conn.get("/-stat/rtasource") == 72
    res = await set_rta_source(conn, descriptor, Target("main", "st"))
    assert res.verified and res.stat_actual == 168
    res = await set_rta_source(conn, descriptor, Target("bus", 3), post_eq=False)
    assert res.verified and res.stat_actual == 50
    await conn.set("/-prefs/rta/options", 0b100000)
    res = await set_rta_source(conn, descriptor, Target("ch", 5))
    assert res.verified and res.options_cleared and await conn.get("/-prefs/rta/options") == 0
    await conn.set("/-action/setrtasrc", 48)  # Bus 1 pre-EQ, action numbering
    assert await conn.get("/-prefs/rta/source") == 50


async def test_geq_notch_kills_ring_closed_loop(conn, fakedesk, descriptor):
    rta = fakedesk.rta
    geq = descriptor.scale("geq_gain")

    async def settle() -> None:  # SET is fire-and-forget: a round trip guarantees it was processed
        await conn.get("/-stat/selidx")

    await conn.slash("/fx/5 GEQ2")  # provisioning: a dual GEQ in an insert-only slot
    await conn.slash("/bus/03/insert ON POST FX5L")  # side A on bus 3
    await set_rta_source(conn, descriptor, Target("bus", 3))
    assert rta.cuts == {}  # flat GEQ: nothing to cut
    await conn.request("/-fake/ring", 2400.0, 15.0)  # ring at 2.4 kHz, +15 dB/s; the echo is the ack
    l0 = rta.ring_level(2400.0)
    assert l0 is not None
    await asyncio.sleep(0.4)
    l1 = rta.ring_level(2400.0)
    assert l1 > l0 + 3.0  # growing
    # notch: GEQ band 22 = 2.5 kHz (geq.band_hz[21]) -> par/22 on side A, -6 dB
    assert descriptor.geq["band_hz"][21] == 2500
    await conn.set("/fx/5/par/22", geq.to_raw(-6.0))
    await settle()
    band = rta.band_for_hz(2400.0)
    assert rta.cuts.get(band) == pytest.approx(6.0)
    await asyncio.sleep(0.4)
    l2 = rta.ring_level(2400.0)
    assert l2 < l1 - 3.0  # decaying: the cut exceeds the per-frame growth (meters.py cut model)
    # side B (FX5R) uses par 33-63: the same notch there does not touch side A's bus
    await conn.set("/fx/5/par/54", geq.to_raw(-9.0))
    await settle()
    assert rta.cuts.get(band) == pytest.approx(6.0)
    await conn.slash("/bus/03/insert ON POST FX5R")
    assert rta.cuts.get(band) == pytest.approx(9.0)
    # bypassing the insert, or pointing the RTA at a bus without a GEQ, removes the cut
    await conn.set("/bus/03/insert/on", 0)
    await settle()
    assert rta.cuts == {}
    await conn.set("/bus/03/insert/on", 1)
    await settle()
    assert rta.cuts.get(band) == pytest.approx(9.0)
    await set_rta_source(conn, descriptor, Target("bus", 4))
    assert rta.cuts == {}
    await conn.request("/-fake/ring/stop", 2400.0)
    assert rta.ring_level(2400.0) is None
    await conn.request("/-fake/note", 330.0, -20.0)
    assert rta.notes and rta.notes[0]["freq_hz"] == 330.0
    await conn.request("/-fake/note/stop", 330.0)
    assert not rta.notes


# ---------------------------------------------------------------------------------------- scenes


async def test_scenes_recall_save_load_delete(conn, conn_factory, fakedesk):
    other = await conn_factory()
    pushed: list[tuple[str, tuple]] = []
    other.on_update(lambda a, args: pushed.append((a, args)))
    assert await conn.node("/-show/showfile/scene/001") == '/-show/showfile/scene/001 "The Molecules" "" %000000000 1'
    assert await conn.node("/-show/showfile/scene/002") == '/-show/showfile/scene/002 "GravelAxe" "" %000000000 1'
    assert await conn.get("/-show/showfile/scene/003/name") == "Empty"
    assert await conn.get("/-show/showfile/scene/003/hasdata") == 0
    assert await conn.get("/-show/showfile/scene/004/hasdata") == 0
    assert await conn.get("/-show/prepos/current") == 0
    msg = await conn.request("/-show/showfile/scene/002")  # typed container GET (GetSceneName.c)
    assert msg.args[0] == "GravelAxe" and msg.typetags == ",ssii"

    await conn.set("/-action/goscene", 1)  # fx_routing_scenes.md §6.4
    await wait_until(lambda: fakedesk.get("/-show/prepos/current") == 1, what="goscene")
    assert await conn.get("/ch/05/config/name") == "Vox Tony"
    assert await conn.get("/bus/01/config/name") == "Tony IEM"
    await wait_until(lambda: ("/-show/prepos/current", (1,)) in pushed and ("/ch/05/config/name", ("Vox Tony",)) in pushed, what="recall pushes")

    # save the live state as scene 5, change a fader, recall -> restored
    await conn.set("/ch/01/mix/fader", 0.25)
    reply = await conn.request("/save", "scene", 5, "Soundcheck", "notes here")
    assert reply.args == ("scene", 1)
    assert await conn.node("/-show/showfile/scene/005") == '/-show/showfile/scene/005 "Soundcheck" "notes here" %000000000 1'
    await conn.set("/ch/01/mix/fader", 0.9)
    reply = await conn.request("/load", "scene", 5)
    assert reply.args == ("scene", 1)
    assert await conn.get("/ch/01/mix/fader") == pytest.approx(quantize(0.25, 1024), abs=1e-6)
    assert await conn.get("/-show/prepos/current") == 5
    reply = await conn.request("/load", "scene", 3)  # "Empty": nothing to load
    assert reply.args == ("scene", 0)
    reply = await conn.request("/rename", "scene", 5, "Line check")
    assert reply.args == ("scene", 1) and await conn.get("/-show/showfile/scene/005/name") == "Line check"
    reply = await conn.request("/delete", "scene", 5)
    assert reply.args == ("scene", 1)
    assert await conn.get("/-show/showfile/scene/005/hasdata") == 0
    assert 5 not in fakedesk.scenes
    # goscene 0 brings the Init names back
    await conn.set("/-action/goscene", 0)
    await wait_until(lambda: fakedesk.get("/ch/05/config/name") == "Ch05", what="goscene 0")


async def test_scene_dir_persists_saves(descriptor, conn_factory, tmp_path):
    desk = FakeDesk(descriptor, scene_dir=tmp_path)
    await desk.start()
    try:
        c = X32Connection(descriptor, EventBus(), timeout_s=0.25, retries=1)
        await c.connect(desk.host, desk.port)
        try:
            await c.set("/ch/01/config/name", "Persisted")
            reply = await c.request("/save", "scene", 7, "Seven", "n")
            assert reply.args == ("scene", 1)
        finally:
            await c.close()
    finally:
        await desk.stop()
    assert (tmp_path / "scene_07.json").exists()
    desk2 = FakeDesk(descriptor, scene_dir=tmp_path)  # a restart finds the saved scene
    assert desk2.scenes[7]["name"] == "Seven" and desk2.get("/-show/showfile/scene/007/hasdata") == 1
    assert desk2.scenes[7]["state"]["/ch/01/config/name"] == "Persisted"


# ---------------------------------------------------------------------------------------- FX slots


async def test_fx_slots_5_to_8_use_their_own_type_enum(conn, descriptor):
    fx58, fx14 = descriptor.enum("fx_type_58"), descriptor.enum("fx_type_14")
    assert await conn.node("/fx/5") == "/fx/5 DES2"
    assert await conn.get("/fx/5/type") == fx58.index("DES2")
    await conn.set("/fx/5/type", 0)  # GEQ2 = 0 on slots 5-8 (fx_routing_scenes.md §1.3)
    assert await conn.node("/fx/5") == "/fx/5 GEQ2"
    await conn.set("/fx/1/type", fx14.index("GEQ2"))  # GEQ2 = 27 on slots 1-4 (§1.2)
    assert await conn.node("/fx/1") == "/fx/1 GEQ2"
    await conn.slash("/fx/7 TEQ2")
    assert await conn.get("/fx/7/type") == fx58.index("TEQ2")
    await conn.set("/fx/8/type", "GEQ")
    assert await conn.get("/fx/8/type") == 1
    # GEQ par text is dB; a / write in dB lands on the 0.5 dB grid as raw (dB+15)/30 (§2.2)
    await conn.slash("/fx/5/par 0.0 -3.0 +1.5")
    assert await conn.get("/fx/5/par/02") == pytest.approx(0.4, abs=1e-6)
    assert await conn.get("/fx/5/par/03") == pytest.approx(0.55, abs=1e-6)
    toks = tokenize(await conn.node("/fx/5/par"))
    assert toks[1:4] == ["0.0", "-3.0", "1.5"] and len(toks) == 65
    await conn.set("/fx/5/par/04", 0.31)  # raw floats snap to the 61-step grid on a GEQ
    assert await conn.get("/fx/5/par/04") == pytest.approx(quantize(0.31, 61), abs=1e-6)
    # a non-GEQ effect keeps raw floats in its node text
    assert tokenize(await conn.node("/fx/2/par"))[1] == "0.5"


# ---------------------------------------------------------------------------------------- faults


async def test_drop_next_causes_request_timeout(conn, fakedesk):
    await conn.request("/-fake/drop", 2)  # acked before the budget applies
    t0 = time.monotonic()
    with pytest.raises(RequestTimeout) as ei:
        await conn.get("/ch/01/mix/fader")
    assert "after 2 attempt(s)" in str(ei.value)
    assert time.monotonic() - t0 < 1.5
    assert fakedesk.dropped == 2
    assert await conn.get("/ch/01/mix/fader") is not None  # budget spent
    fakedesk.drop_next(2)
    with pytest.raises(RequestTimeout):
        await conn.node("/ch/01/config")
    assert await conn.node("/ch/01/config") == '/ch/01/config "Ch01" 1 RD 1'
    # a dropped echo still leaves the write applied (the request was received)
    fakedesk.drop_next(2)
    with pytest.raises(RequestTimeout):
        await conn.slash("/ch/09/mix/fader -30.0")
    assert fakedesk.value("/ch/09/mix/fader") == pytest.approx(-30.0, abs=0.05)


async def test_silence_degrades_then_recovers(conn, fakedesk):
    await conn.send_raw("/-fake/silence", 1)  # never acked: the desk is "hung"
    await wait_for_state(conn, ConnectionState.DEGRADED, 6.0)
    with pytest.raises(NotConnected):
        await conn.set("/ch/01/mix/fader", 0.5)
    with pytest.raises(RequestTimeout):
        await conn.get("/ch/01/mix/fader")
    assert not conn.connected and conn.status.error
    fakedesk.silent = False  # power back
    await wait_for_state(conn, ConnectionState.CONNECTED, 6.0)
    assert await conn.get("/ch/01/mix/fader") is not None
    # the OSC form of the same switch
    await conn.request("/-fake/silence", 0)  # acked (already answering)


async def test_latency_delays_replies(conn):
    await conn.request("/-fake/latency", 120)
    t0 = time.monotonic()
    await conn.get("/ch/01/mix/fader")
    assert time.monotonic() - t0 >= 0.1
    await conn.request("/-fake/latency", 0)  # this ack is itself delayed once
    t0 = time.monotonic()
    await conn.get("/ch/01/mix/fader")
    assert time.monotonic() - t0 < 0.1


# ---------------------------------------------------------------------------------------- CLI


def test_cli_help_lists_the_documented_options():
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    r = subprocess.run([sys.executable, "-m", "x32mcp.fakedesk", "--help"], capture_output=True, text=True, timeout=60, env=env, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    for opt in ("--port", "--host", "--name", "--scene-dir", "--ring"):
        assert opt in r.stdout
