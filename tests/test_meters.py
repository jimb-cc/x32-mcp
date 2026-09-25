"""x32mcp.meters (DESIGN.md §11): blob vectors, sources, synthetic RTA, RTA source recipe.

Every byte vector is written out by hand from docs/research/meters.md (§2.2 hex capture, §4.2
RTA short examples, §4.3 /meters/16 coding) — never derived from ``encode_meter_blob``.
"""

from __future__ import annotations

import asyncio
import math
import struct
import time

import pytest

from x32mcp.descriptor import Descriptor
from x32mcp.events import EventBus
from x32mcp.meters import (
    restore_rta_prefs,
    METER_COUNTS,
    METER_GROUPS,
    RTA_BAND_HZ,
    RTA_BANDS,
    RTA_METER_TYPE,
    FixtureSource,
    FrameSource,
    LiveMeters,
    MeterFrame,
    MeterParseError,
    RtaSourceError,
    SyntheticRta,
    average_frames,
    band_for_hz,
    encode_meter_blob,
    encode_meter_datagram,
    lin_to_db,
    parse_meter_blob,
    parse_meter_datagram,
    rta_band_centre,
    rta_band_hz,
    rta_source_index,
    rta_stat_expected,
    set_rta_source,
    target_for_rta_source,
)
from x32mcp.osc import decode, encode
from x32mcp.targets import Target

NUL = b"\x00"

# meters.md §2.2: verbatim /meters/6 capture (channel 17), 40 bytes
METERS6_HEX = "2f6d65746572732f360000002c6200000000001404000000fd1d2137fdff7f3f0000803f6ebbd534"
# meters.md §1.1: verbatim request "/meters ,si /meters/6 16", 28 bytes
METERS_REQ_HEX = "2f6d6574657273002c7369002f6d65746572732f3600000000000010"


@pytest.fixture(scope="module")
def d() -> Descriptor:
    return Descriptor.load()


def _slope(ys: list[float], dt: float) -> float:
    """Least-squares slope of ys against t = i*dt."""
    n = len(ys)
    xs = [i * dt for i in range(n)]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / sxx


# ---------------------------------------------------------------------------------------
# Blob parsing — hand-built vectors
# ---------------------------------------------------------------------------------------


def test_meters6_verbatim_capture_decodes():
    data = bytes.fromhex(METERS6_HEX)
    assert len(data) == 40
    msg = decode(data)
    assert msg.address == "/meters/6" and msg.typetags == ",b"
    blob = msg.args[0]
    assert len(blob) == 20 and blob[:4] == b"\x04\x00\x00\x00"  # LE count 4 (meters.md §2.2)
    f = parse_meter_blob(msg.address, blob, ts=1.0)
    assert f.meter_type == 6 and f.ts == 1.0 and len(f.values) == 4
    assert f.values[0] == pytest.approx(9.6e-6, rel=0.01)  # pre-fade, -100.35 dBFS
    assert f.values[1] == pytest.approx(0.99999982, abs=1e-7)  # gate gain: no reduction
    assert f.values[2] == 1.0  # dyn gain
    assert f.values[3] == pytest.approx(3.98e-7, rel=0.01)  # post-fade: -128 dBFS floor
    db = f.db()
    assert db[0] == -90.0  # -100.35 dBFS floored at the default -90 (DESIGN §11)
    assert db[1] == pytest.approx(0.0, abs=1e-5)
    assert db[2] == 0.0
    assert db[3] == -90.0
    deep = f.db(floor=-130.0)
    assert deep[0] == pytest.approx(-100.35, abs=0.05)
    assert deep[3] == pytest.approx(-128.0, abs=0.01)  # the console's own floor (meters.md §2.2)
    # same via the datagram helper
    assert parse_meter_datagram(data, ts=1.0) == f


def test_meters6_encode_reproduces_capture_bytes():
    data = bytes.fromhex(METERS6_HEX)
    f = parse_meter_datagram(data, ts=0.0)
    assert encode_meter_datagram(6, f.values) == data
    assert encode_meter_blob(6, f.values) == decode(data).args[0]


def test_rta_blob_hand_built_vector():
    # meters.md §4.2 worked examples: 00 80 00 c0 -> -128.0, -64.0 ; 40 e0 ff ff -> -31.75, -0.0039
    # short 0xE800 (-6144) -> -24.0 ; 0x0000 -> 0.0 = clip
    words = [b"\x00\x80\x00\xc0", b"\x40\xe0\xff\xff", b"\x00\xe8\x00\x00"]
    payload = b"".join(words) + b"\x00\x80" * (100 - 6)  # remaining bands: -128 (no signal)
    assert len(payload) == 200
    blob = b"\x32\x00\x00\x00" + payload  # LE count 50
    f = parse_meter_blob("/meters/15", blob, ts=2.5)
    assert f.meter_type == 15 and f.is_rta and len(f.values) == 100
    assert f.values[0] == -128.0
    assert f.values[1] == -64.0
    assert f.values[2] == -31.75
    assert f.values[3] == pytest.approx(-1 / 256)
    assert f.values[4] == -24.0
    assert f.values[5] == 0.0
    assert all(v == -128.0 for v in f.values[6:])
    assert f.db() == f.values  # already dB: unchanged, not floored

    # whole datagram = 224 bytes: 12-byte address, ",b~~", BE size 204, LE count, 200 B (meters.md §2.1)
    dgram = encode("/meters/15", blob)
    assert len(dgram) == 224
    assert dgram[:12] == b"/meters/15" + NUL * 2
    assert dgram[12:16] == b",b" + NUL * 2
    assert dgram[16:20] == b"\x00\x00\x00\xcc"  # 204 big-endian
    assert dgram[20:24] == b"\x32\x00\x00\x00"  # 50 little-endian
    assert parse_meter_datagram(dgram, ts=0) .values == f.values
    # exact inverse
    assert encode_meter_blob(15, f.values) == blob
    assert encode_meter_datagram(15, f.values) == dgram


def test_meters16_hand_built_vector():
    # meters.md §4.3: 88 shorts /32767 then 8 automix shorts as log2(gain)*256
    shorts = [32767, 16384, 0, -32768] + [32767] * 84 + [0, -256, -1024, 256, 0, 0, 0, 0]
    assert len(shorts) == 96
    blob = b"\x30\x00\x00\x00" + struct.pack("<96h", *shorts)  # LE count 48
    f = parse_meter_blob("/meters/16", blob, ts=0)
    assert f.meter_type == 16 and len(f.values) == 96
    assert f.values[0] == 1.0
    assert f.values[1] == pytest.approx(16384 / 32767)
    assert f.values[2] == 0.0
    assert f.values[3] == pytest.approx(-32768 / 32767)
    assert f.values[88] == 1.0  # 0 dB
    assert f.values[89] == 0.5  # -6.02 dB
    assert f.values[90] == 0.0625  # -24.08 dB
    assert f.values[91] == 2.0
    assert len(encode("/meters/16", blob)) == 216  # meters.md §2.1 size table
    assert encode_meter_blob(16, f.values) == blob


def test_float_meter_sizes_match_research_table():
    # meters.md §2.1: datagram = 24 + 4*count for every id
    sizes = {0: 304, 1: 408, 2: 220, 3: 112, 4: 352, 5: 132, 6: 40, 7: 88, 8: 48, 9: 152, 10: 152, 11: 44, 12: 40, 13: 216, 14: 344, 15: 224, 16: 216}
    for mtype, count in METER_COUNTS.items():
        n_vals = count * 2 if mtype in (15, 16) else count
        vals = [-30.0] * n_vals if mtype == 15 else [0.5] * n_vals
        dg = encode_meter_datagram(mtype, vals)
        assert len(dg) == sizes[mtype] == 24 + 4 * count, mtype
        f = parse_meter_datagram(dg, ts=0)
        assert f.meter_type == mtype and len(f.values) == n_vals


def test_round_trip_random_values():
    import random

    rng = random.Random(3)
    vals = [rng.uniform(0.0, 8.0) for _ in range(70)]
    f = parse_meter_blob("/meters/0", encode_meter_blob(0, vals), ts=0)
    assert all(a == pytest.approx(b, rel=1e-6) for a, b in zip(f.values, vals))
    rta = [rng.uniform(-128.0, 0.0) for _ in range(100)]
    f15 = parse_meter_blob("/meters/15", encode_meter_blob(15, rta), ts=0)
    assert all(abs(a - b) <= 1 / 512 + 1e-9 for a, b in zip(f15.values, rta))  # 1/256 dB grid
    # clamping into the RTA range
    f15b = parse_meter_blob("/meters/15", encode_meter_blob(15, [+5.0, -200.0] * 50), ts=0)
    assert f15b.values[0] == 0.0 and f15b.values[1] == -128.0


@pytest.mark.parametrize(
    "address, blob",
    [
        ("/meters/6", b"\x04\x00\x00\x00" + b"\x00" * 12),  # count 4, only 3 words
        ("/meters/6", b"\x04\x00\x00\x00" + b"\x00" * 20),  # count 4, 5 words
        ("/meters/6", b"\x04\x00\x00"),  # no count at all
        ("/meters/6", b""),
        ("/meters/6", b"\xff\xff\xff\xff"),  # negative count
        ("/meters/15", b"\x32\x00\x00\x00" + b"\x00" * 199),  # RTA one byte short
        ("/meters/15", b"\x00\x00\x00\x32" + b"\x00" * 200),  # count written big-endian (wrong)
        ("/xx", b"\x00\x00\x00\x00"),  # not /meters/N and no meter_type
        ("meters/6", b"\x00\x00\x00\x00"),
    ],
)
def test_wrong_length_or_address_raises(address, blob):
    with pytest.raises(MeterParseError):
        parse_meter_blob(address, blob, ts=0)


def test_alias_needs_explicit_meter_type():
    blob = b"\x01\x00\x00\x00" + struct.pack("<f", 0.25)
    f = parse_meter_blob("/rr", blob, ts=0, meter_type=5)  # /batchsubscribe alias (meters.md §1.3)
    assert f.meter_type == 5 and f.values == (0.25,)
    empty = parse_meter_blob("/meters/6", b"\x00\x00\x00\x00", ts=0)
    assert empty.values == ()


def test_parse_datagram_rejects_non_blob():
    with pytest.raises(MeterParseError):
        parse_meter_datagram(encode("/ch/01/mix/fader", 0.5), ts=0)
    with pytest.raises(MeterParseError):
        parse_meter_datagram(b"\xff\xff", ts=0)


def test_lin_to_db_and_groups():
    assert lin_to_db(1.0) == 0.0
    assert lin_to_db(0.5) == pytest.approx(-6.0206, abs=1e-3)
    assert lin_to_db(8.0) == pytest.approx(18.06, abs=0.01)
    assert lin_to_db(0.0) == -90.0
    assert lin_to_db(1e-9) == -90.0
    assert lin_to_db(1e-9, floor=-200.0) == pytest.approx(-180.0)
    for name, (mtype, start, count) in METER_GROUPS.items():
        assert start + count <= METER_COUNTS[mtype], name


# ---------------------------------------------------------------------------------------
# RTA bands
# ---------------------------------------------------------------------------------------


def test_rta_band_formula_and_descriptor(d):
    assert len(RTA_BAND_HZ) == RTA_BANDS == 100
    assert rta_band_centre(90) == 10000.0
    assert rta_band_centre(0) == pytest.approx(19.53, abs=0.01)
    assert rta_band_centre(99) == pytest.approx(18660.7, abs=0.1)
    assert rta_band_centre(40) == pytest.approx(312.5, abs=0.01)  # the DOC's "313"
    hz = rta_band_hz(d)
    assert len(hz) == 100 and hz[90] == 10000.0
    assert all(a == pytest.approx(b, abs=0.01) for a, b in zip(hz, RTA_BAND_HZ))
    assert all(hz[i] < hz[i + 1] for i in range(99))
    assert band_for_hz(10000) == 90
    assert band_for_hz(2400) == 69  # 2332.6 Hz is nearer than 2500 on a log axis
    assert band_for_hz(2500) == 70
    assert band_for_hz(1) == 0 and band_for_hz(1e6) == 99
    with pytest.raises(ValueError):
        band_for_hz(0)


# ---------------------------------------------------------------------------------------
# FixtureSource
# ---------------------------------------------------------------------------------------


def _frames(n: int, mtype: int = 15) -> list[MeterFrame]:
    return [MeterFrame(mtype, float(i), tuple([float(-40 - i)] * 4)) for i in range(n)]


async def test_fixture_replays_non_realtime_in_order():
    frames = _frames(25)
    src = FixtureSource(frames, realtime=False)
    assert isinstance(src, FrameSource)
    got: list[MeterFrame] = []
    unsub = src.subscribe(got.append)
    t = time.monotonic()
    await src.start()
    assert await src.wait_done(timeout_s=2.0)
    assert time.monotonic() - t < 1.0  # not 25 x 50 ms
    assert got == frames
    assert src.last_frame == frames[-1] and src.frames_emitted == 25
    unsub()
    await src.stop()  # idempotent after completion
    assert not src.running


async def test_fixture_callable_and_loop_and_stop():
    calls: list[int] = []

    def gen(i: int) -> MeterFrame | None:
        calls.append(i)
        return MeterFrame(15, float(i), (float(i),)) if i < 3 else None

    src = FixtureSource(gen, realtime=False)
    got: list[float] = []
    src.subscribe(lambda f: got.append(f.values[0]))
    await src.start()
    assert await src.wait_done(1.0)
    assert got == [0.0, 1.0, 2.0] and calls == [0, 1, 2, 3]

    looped = FixtureSource(_frames(2), period_s=0.001, realtime=True, loop=True)
    n = 0

    def count(_: MeterFrame) -> None:
        nonlocal n
        n += 1

    looped.subscribe(count)
    await looped.start()
    await asyncio.sleep(0.15)
    await looped.stop()
    assert n > 2 and not looped.running
    seen = n
    await asyncio.sleep(0.02)
    assert n == seen  # really stopped


async def test_bad_subscriber_does_not_break_replay():
    src = FixtureSource(_frames(3), realtime=False)
    good: list[MeterFrame] = []

    def bad(_: MeterFrame) -> None:
        raise RuntimeError("boom")

    src.subscribe(bad)
    src.subscribe(good.append)
    await src.start()
    assert await src.wait_done(1.0)
    assert len(good) == 3


# ---------------------------------------------------------------------------------------
# average_frames
# ---------------------------------------------------------------------------------------


async def test_average_frames_full_partial_and_none():
    src = FixtureSource(_frames(4), realtime=False)
    task = asyncio.ensure_future(average_frames(src, 3, timeout_s=1.0))
    await asyncio.sleep(0)  # let it subscribe first
    await src.start()
    avg = await task
    assert avg is not None and avg.meter_type == 15
    assert avg.values == pytest.approx((-41.0,) * 4)  # mean of -40, -41, -42
    assert avg.ts == 2.0

    src2 = FixtureSource(_frames(2), realtime=False)
    task = asyncio.ensure_future(average_frames(src2, 5, timeout_s=0.2))
    await asyncio.sleep(0)
    await src2.start()
    t = time.monotonic()
    partial = await task
    assert time.monotonic() - t < 1.0
    assert partial is not None and partial.values == pytest.approx((-40.5,) * 4)

    never = FixtureSource([], realtime=False)  # never started
    t = time.monotonic()
    assert await average_frames(never, 2, timeout_s=0.05) is None
    assert time.monotonic() - t < 1.0


# ---------------------------------------------------------------------------------------
# SyntheticRta
# ---------------------------------------------------------------------------------------


def test_synthetic_base_spectrum_and_determinism():
    a = SyntheticRta(seed=7, t0=100.0)
    b = SyntheticRta(seed=7, t0=100.0)
    fa = [a.tick() for _ in range(60)]
    fb = [b.tick() for _ in range(60)]
    assert fa == fb  # same seed -> identical frames
    assert all(f.meter_type == RTA_METER_TYPE and len(f.values) == 100 for f in fa)
    assert all(-128.0 <= v <= 0.0 for f in fa for v in f.values)
    assert fa[0].ts == 100.0 and fa[1].ts == pytest.approx(100.05) and fa[59].ts == pytest.approx(100.0 + 59 * 0.05)
    b100, b10k = band_for_hz(100), band_for_hz(10000)
    m100 = sum(f.values[b100] for f in fa) / 60  # 3 s: wobble averages out over whole periods
    m10k = sum(f.values[b10k] for f in fa) / 60
    assert m100 == pytest.approx(a.base_level_db(b100), abs=1.0)
    assert m10k == pytest.approx(-55.0, abs=1.0)
    assert a.base_level_db(b10k) == -55.0
    b1k = band_for_hz(1000)  # nearest centre is 1015.32 Hz
    assert a.base_level_db(b1k) == pytest.approx(-25.0 - 15.0 * math.log10(RTA_BAND_HZ[b1k] / 100.0), abs=1e-9)  # -15 dB/decade
    # noise +-3 dB and wobble +-2 dB: never more than ~5.5 dB from the base
    assert max(abs(f.values[b100] - a.base_level_db(b100)) for f in fa) < 5.5
    assert isinstance(a, FrameSource)


def test_synthetic_frames_generator_matches_tick():
    s = SyntheticRta(seed=3)
    seen: list[MeterFrame] = []
    s.subscribe(seen.append)
    g = s.frames()
    f0, f1 = next(g), next(g)
    assert seen == [f0, f1] and s.frame_index == 2 and f1.ts > f0.ts
    blob = s.next_blob()
    assert parse_meter_blob("/meters/15", blob, ts=0).values == pytest.approx(s.last_frame.values, abs=1 / 256)


def test_synthetic_ring_grows_linearly_and_caps():
    s = SyntheticRta(seed=11)
    band = s.inject_ring(2400.0, 15.0)  # dB/s from -45
    assert band == 69 and s.rings[0]["level_db"] == -45.0
    levels = [s.tick().values[band] for _ in range(80)]
    # internal level is exactly linear until the cap
    assert s.ring_level(2400.0) == pytest.approx(-3.0)
    # observed band level: fit over frames 12..52 (ring well above the -45 dB base, below the cap)
    slope = _slope(levels[12:52], 0.05)
    assert abs(slope - 15.0) <= 0.2 * 15.0, slope
    assert max(levels) <= -3.0 + 0.1  # cap_db
    assert levels[70] == pytest.approx(-3.0, abs=0.1)
    # leakage: +-1 band sits ~10 dB below the ring once the ring dominates
    f = s.tick()
    assert f.values[band - 1] == pytest.approx(f.values[band] - 10.0, abs=1.5)
    assert f.values[band + 1] == pytest.approx(f.values[band] - 10.0, abs=1.5)
    assert f.values[band - 2] < f.values[band] - 20.0
    assert s.stop_ring(2400.0) and not s.stop_ring(2400.0)
    after = s.tick().values[band]
    assert after < -35.0  # back to the base spectrum


def test_synthetic_ring_internal_linearity():
    s = SyntheticRta(seed=1)
    s.inject_ring(1000.0, 6.0, start_db=-50.0, cap_db=-10.0)
    for k in range(1, 30):
        s.tick()
        assert s.ring_level(1000.0) == pytest.approx(-50.0 + k * 6.0 * 0.05, abs=1e-9)


def test_synthetic_note_rises_fast_then_plateaus():
    s = SyntheticRta(seed=5)
    band = s.inject_note(330.0, -20.0)
    levels = [s.tick().values[band] for _ in range(60)]
    assert levels[0] < -28.0  # starts 30 dB down (masked by the base)
    assert levels[6] > -21.0  # up within ~5 frames
    plateau = levels[10:]
    assert max(plateau) - min(plateau) < 1.5
    assert abs(_slope(plateau, 0.05)) < 1.0  # dB/s: no growth -> not a ring
    assert all(-20.0 - 0.5 <= v <= -19.0 for v in plateau)
    assert s.notes[0]["age_frames"] == 60
    assert s.stop_note(330.0)


def test_attenuate_makes_ring_decay():
    s = SyntheticRta(seed=9)
    band = s.inject_ring(2400.0, 15.0)  # margin per frame = 15 * 0.05 = 0.75 dB
    for _ in range(20):
        s.tick()
    assert s.ring_level(2400.0) == pytest.approx(-30.0)
    bands = s.set_geq_gain(2500.0, -3.0)  # GEQ cut: 2500 Hz band covers RTA bands 69..71
    assert bands == (69, 70, 71) and s.cuts == {69: 3.0, 70: 3.0, 71: 3.0}
    before = s.tick().values[band]
    for _ in range(9):
        s.tick()
    after_level = s.ring_level(2400.0)
    assert after_level == pytest.approx(-30.0 + 10 * (0.75 - 3.0))  # -52.5: decays 2.25 dB/frame
    assert s.tick().values[band] < before - 15.0
    # the displayed spectrum at a cut band is lowered by the cut
    quiet = SyntheticRta(seed=9)
    quiet.attenuate(70, 6.0)
    frames = [quiet.tick().values[70] for _ in range(40)]
    assert sum(frames) / 40 == pytest.approx(quiet.base_level_db(70) - 6.0, abs=1.5)
    # a cut smaller than the margin only slows the ring
    slow = SyntheticRta(seed=2)
    slow.inject_ring(2400.0, 15.0)
    slow.attenuate(69, 0.5)
    for _ in range(10):
        slow.tick()
    assert slow.ring_level(2400.0) == pytest.approx(-45.0 + 10 * (0.75 - 0.5))
    # remove the cut and it grows again; loop_period_s scales the margin
    slow.attenuate(69, 0.0)
    assert slow.cuts == {}
    with pytest.raises(ValueError):
        slow.attenuate(100, 1.0)
    lp = SyntheticRta(seed=2, loop_period_s=0.1)
    lp.inject_ring(2400.0, 15.0)
    lp.attenuate(69, 3.0)
    lp.tick()
    assert lp.ring_level(2400.0) == pytest.approx(-45.0 + 0.75 - 3.0 * 0.05 / 0.1)


async def test_synthetic_realtime_start_stop():
    s = SyntheticRta(seed=4, period_s=0.005)
    got: list[MeterFrame] = []
    s.subscribe(got.append)
    await s.start()
    await s.start()  # idempotent
    await asyncio.sleep(0.12)
    await s.stop()
    assert len(got) >= 3 and not s.running
    n = len(got)
    await asyncio.sleep(0.02)
    assert len(got) == n
    assert all(b.ts - a.ts == pytest.approx(0.005, abs=1e-5) for a, b in zip(got, got[1:]))


# ---------------------------------------------------------------------------------------
# LiveMeters against a fake connection
# ---------------------------------------------------------------------------------------


class FakeConn:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, tuple, str | None]] = []
        self.datagrams: list[bytes] = []
        self.blob_cbs: list = []
        self.fail = fail

    def on_blob(self, cb):
        self.blob_cbs.append(cb)

        def _unsub():
            self.blob_cbs.remove(cb)

        return _unsub

    async def send_raw(self, address, *args, typetags=None):
        if self.fail:
            raise ConnectionError("not connected")
        self.sent.append((address, args, typetags))
        self.datagrams.append(encode(address, *args, typetags=typetags))

    def push(self, address: str, blob: bytes) -> None:
        for cb in list(self.blob_cbs):
            cb(address, blob)


async def test_live_request_bytes_match_research():
    conn = FakeConn()
    lm = LiveMeters(conn, 6, renew_s=10.0, time_factor=16)
    await lm.start()
    await asyncio.sleep(0.01)
    assert conn.sent == [("/meters", ("/meters/6", 16), None)]
    assert conn.datagrams[0] == bytes.fromhex(METERS_REQ_HEX)  # meters.md §1.1 verbatim
    assert decode(conn.datagrams[0]).typetags == ",si"
    await lm.stop()


async def test_live_extra_args_forms():
    conn = FakeConn()
    six = LiveMeters(conn, 6, extra_args=(16,), renew_s=10.0)
    five = LiveMeters(conn, 5, extra_args=(3, 1), renew_s=10.0, time_factor=40)
    rta = LiveMeters(conn, 15, renew_s=10.0)
    for lm in (six, five, rta):
        await lm.start()
    await asyncio.sleep(0.01)
    assert conn.sent == [
        ("/meters", ("/meters/6", 16, 1), None),  # ,sii /meters/6 <channel_id> tf
        ("/meters", ("/meters/5", 3, 1, 40), None),  # ,siii /meters/5 <chn> <grp> tf
        ("/meters", ("/meters/15", 1), None),  # ,si /meters/15 tf (no source argument)
    ]
    assert [decode(dg).typetags for dg in conn.datagrams] == [",sii", ",siii", ",si"]
    for lm in (six, five, rta):
        await lm.stop()
    assert conn.blob_cbs == []


async def test_live_renews_and_dispatches_frames():
    conn = FakeConn()
    events = EventBus()
    seen: list = []
    events.subscribe(seen.append, types={"meters.frame"})
    lm = LiveMeters(conn, 15, renew_s=0.01, events=events)
    got: list[MeterFrame] = []
    lm.subscribe(got.append)
    assert not lm.running and lm.last_rx_age_s is None
    await lm.start()
    assert lm.running and len(conn.blob_cbs) == 1
    await asyncio.sleep(0.15)
    assert len(conn.sent) >= 3  # initial + renewals (identical request each time)
    assert all(s == ("/meters", ("/meters/15", 1), None) for s in conn.sent)
    assert lm.requests_sent == len(conn.sent)

    blob = encode_meter_blob(15, [-30.0] * 100)
    conn.push("/meters/15", blob)
    conn.push("/meters/6", encode_meter_blob(6, [0.5] * 4))  # other meter: ignored
    conn.push("/meters/15", b"\x32\x00\x00\x00" + b"\x00" * 10)  # malformed: counted, not raised
    assert len(got) == 1 and got[0].meter_type == 15 and got[0].values[0] == -30.0
    assert lm.frames_received == 1 and lm.parse_errors == 1
    assert lm.last_frame is got[0] and lm.last_rx_age_s is not None and lm.last_rx_age_s < 5.0
    assert len(seen) == 1 and seen[0].data == {"meter_type": 15, "values": [-30.0] * 100}
    assert events.recent() == []  # meters.frame is transient (events.py)

    await lm.stop()
    assert not lm.running and conn.blob_cbs == []
    n = len(conn.sent)
    conn.push("/meters/15", blob)
    await asyncio.sleep(0.05)
    assert len(got) == 1 and len(conn.sent) == n  # nothing after stop
    await lm.stop()  # idempotent


async def test_live_send_failure_is_logged_not_raised():
    conn = FakeConn(fail=True)
    lm = LiveMeters(conn, 15, renew_s=0.01, time_factor=500)  # bad tf -> clamped to 1
    assert lm.time_factor == 1
    await lm.start()
    await asyncio.sleep(0.05)
    assert lm.running and conn.sent == []
    conn.fail = False
    await asyncio.sleep(0.05)
    assert len(conn.sent) >= 1  # recovers on the renew cadence
    await lm.stop()


# ---------------------------------------------------------------------------------------
# RTA source recipe
# ---------------------------------------------------------------------------------------


def test_rta_source_index_matches_research_numbering(d):
    # meters.md §5.1: 2-33 Ch, 34-41 Aux, 42-49 FX rtn, 50-65 Bus, 66-71 Mtx, 72 Main, 73 Mono
    assert rta_source_index(d, Target("ch", 1)) == 2
    assert rta_source_index(d, Target("ch", 32)) == 33
    assert rta_source_index(d, Target("auxin", 1)) == 34
    assert rta_source_index(d, Target("auxin", 8)) == 41
    assert rta_source_index(d, Target("fxrtn", 1)) == 42  # FX1L
    assert rta_source_index(d, Target("fxrtn", 2)) == 43  # FX1R
    assert rta_source_index(d, Target("fxrtn", 8)) == 49  # FX4R
    assert rta_source_index(d, Target("bus", 1)) == 50
    assert rta_source_index(d, Target("bus", 3)) == 52  # 49 + N
    assert rta_source_index(d, Target("bus", 16)) == 65
    assert rta_source_index(d, Target("mtx", 1)) == 66
    assert rta_source_index(d, Target("mtx", 6)) == 71
    assert rta_source_index(d, Target("main", "st")) == 72
    assert rta_source_index(d, Target("main", "m")) == 73
    with pytest.raises(RtaSourceError):
        rta_source_index(d, Target("dca", 1))
    # /-stat/rtasource: Bus N post-EQ = 146+N-1, Main LR post = 168, pre = 48+N-1 / 70 (meters.md §5.3)
    assert rta_stat_expected(52, True) == 148
    assert rta_stat_expected(52, False) == 50
    assert rta_stat_expected(72, True) == 168
    assert rta_stat_expected(72, False) == 70
    assert rta_stat_expected(73, True) == 169
    assert rta_stat_expected(1, False) == 72  # Monitor
    assert target_for_rta_source(d, 52) == Target("bus", 3)
    assert target_for_rta_source(d, 72) == Target("main", "st")
    assert target_for_rta_source(d, 43) == Target("fxrtn", 2)
    assert target_for_rta_source(d, 0) is None and target_for_rta_source(d, 1) is None
    for fam in d.strip_families:
        for t in d.strip_targets(fam):
            if fam == "dca":
                continue
            assert target_for_rta_source(d, rta_source_index(d, t)) == t


class FakeDeskConn:
    """Answers get() from a dict; set() records and (optionally) mirrors /-stat/rtasource the
    way the console does (prefs index - 2, +98 when POST)."""

    def __init__(self, state: dict, *, mirror: bool = True, get_fail: set[str] | None = None) -> None:
        self.state = dict(state)
        self.sets: list[tuple[str, tuple]] = []
        self.mirror = mirror
        self.get_fail = get_fail or set()

    async def get(self, address):
        if address in self.get_fail:
            raise TimeoutError(address)
        return self.state[address]

    async def set(self, address, *args):
        self.sets.append((address, args))
        self.state[address] = args[0]
        if self.mirror:
            src = self.state.get("/-prefs/rta/source", 0)
            pos = self.state.get("/-prefs/rta/pos", 0)
            if src >= 2:
                self.state["/-stat/rtasource"] = src - 2 + (98 if pos else 0)


async def test_set_rta_source_bus_post_eq_clears_solo_priority(d):
    conn = FakeDeskConn({"/-prefs/rta/source": 0, "/-prefs/rta/pos": 0, "/-prefs/rta/options": 0b100001, "/-stat/rtasource": 0})
    res = await set_rta_source(conn, d, Target("bus", 3))
    assert conn.sets == [
        ("/-prefs/rta/source", (52,)),
        ("/-prefs/rta/pos", (1,)),
        ("/-prefs/rta/options", (0b000001,)),  # only bit 5 cleared; bit 0 (PEQ overlay pre) kept
    ]
    assert res.verified and res.stat_actual == 148 == res.stat_expected
    assert res.source_index == 52 and res.post_eq and res.options_cleared and res.target == Target("bus", 3)


async def test_set_rta_source_main_pre_eq_no_options_write(d):
    conn = FakeDeskConn({"/-prefs/rta/source": 0, "/-prefs/rta/pos": 1, "/-prefs/rta/options": 0b000100, "/-stat/rtasource": 0})
    res = await set_rta_source(conn, d, Target("main", "st"), post_eq=False)
    assert conn.sets == [("/-prefs/rta/source", (72,)), ("/-prefs/rta/pos", (0,))]
    assert res.verified and res.stat_actual == 70 and not res.options_cleared


async def test_set_rta_source_unverified_and_read_failures_do_not_raise(d):
    stale = FakeDeskConn({"/-prefs/rta/source": 0, "/-prefs/rta/pos": 0, "/-prefs/rta/options": 0, "/-stat/rtasource": 5}, mirror=False)
    t = time.monotonic()
    res = await set_rta_source(stale, d, Target("bus", 1))
    assert time.monotonic() - t < 2.0
    assert not res.verified and res.stat_actual == 5 and res.stat_expected == 146
    deaf = FakeDeskConn({"/-prefs/rta/source": 0, "/-prefs/rta/pos": 0}, get_fail={"/-prefs/rta/options", "/-stat/rtasource"})
    res = await set_rta_source(deaf, d, Target("ch", 5))
    assert not res.verified and res.stat_actual is None and res.source_index == 6
    assert [a for a, _ in deaf.sets] == ["/-prefs/rta/source", "/-prefs/rta/pos"]
    with pytest.raises(RtaSourceError):
        await set_rta_source(stale, d, Target("dca", 2))


def test_rta_det_enum_raw_0_is_peak(d):
    """The console's own ``/node -prefs/rta`` line prints PEAK with the raw value at 0 and RMS at 1 (X32RACK FW 4.13,
    toggled both ways and read back, meters.md Verification log 2026-09-25 item 5). The DOC and X32.c list RMS first;
    that order made every arm-time "PEAK" write set RMS until 2026-09-25 (3-frame attack, -97 floor, a broadband burst
    on the switch) -- see test_set_rta_source_forces_analyser_ballistics."""
    spec, _ = d.param_for_address("/-prefs/rta/det")
    assert spec.to_raw("PEAK") == 0 and spec.to_raw("RMS") == 1
    assert spec.to_value(0) == "PEAK" and spec.to_value(1) == "RMS"


async def test_set_rta_source_forces_analyser_ballistics(d):
    """Auto-gain, RMS, a long release and peak-hold are all console *display* preferences that
    corrupt the detector's features; each is forced (only when it differs) and reported."""
    conn = FakeDeskConn({
        "/-prefs/rta/source": 0, "/-prefs/rta/pos": 0, "/-prefs/rta/options": 0, "/-stat/rtasource": 0,
        "/-prefs/rta/autogain": 1, "/-prefs/rta/det": 1, "/-prefs/rta/decay": 0.5, "/-prefs/rta/peakhold": 3,
        "/-prefs/rta/gain": 0.4,
    })
    res = await set_rta_source(conn, d, Target("bus", 1))
    assert ("/-prefs/rta/autogain", (0,)) in conn.sets and res.autogain_cleared
    assert ("/-prefs/rta/det", (0,)) in conn.sets and res.detector_set_peak     # raw 0 = PEAK on the desk (2026-09-25)
    assert ("/-prefs/rta/decay", (0.0,)) in conn.sets and res.decay_set_min
    assert ("/-prefs/rta/peakhold", (0,)) in conn.sets and res.peakhold_cleared
    assert res.prefs_before == {"source": 0, "pos": 0, "autogain": 1, "det": 1, "decay": 0.5, "peakhold": 3, "options": 0, "gain": 0.4}
    assert ("/-prefs/rta/gain", (0.0,)) in conn.sets and res.gain_set  # pinned to rta.gain_db for the session
    assert res.verified
    # and the engineer's settings go back afterwards (Solo Priority excepted)
    conn.sets.clear()
    out = await restore_rta_prefs(conn, d, res.prefs_before)
    assert out["failed"] == [] and set(out["restored"]) == {"source", "pos", "autogain", "det", "decay", "peakhold", "gain"}
    assert ("/-prefs/rta/peakhold", (3,)) in conn.sets and ("/-prefs/rta/source", (0,)) in conn.sets and ("/-prefs/rta/gain", (0.4,)) in conn.sets
    assert not any(a == "/-prefs/rta/options" for a, _ in conn.sets)
    # already right: nothing is rewritten
    good = FakeDeskConn({
        "/-prefs/rta/source": 0, "/-prefs/rta/pos": 0, "/-prefs/rta/options": 0, "/-stat/rtasource": 0,
        "/-prefs/rta/autogain": 0, "/-prefs/rta/det": 0, "/-prefs/rta/decay": 0.0, "/-prefs/rta/peakhold": 0,
        "/-prefs/rta/gain": 0.0,
    })
    res = await set_rta_source(good, d, Target("bus", 1))
    assert [a for a, _ in good.sets] == ["/-prefs/rta/source", "/-prefs/rta/pos"]
    assert not (res.autogain_cleared or res.detector_set_peak or res.decay_set_min or res.peakhold_cleared or res.gain_set)


def test_synthetic_note_rise_frames():
    """inject_note(rise_frames=1) lands within one frame (a howl slamming into a limiter, a switched-on tone); the default keeps
    the 5-frame 30 dB onset ramp of a held instrument note."""
    from x32mcp.meters import SyntheticRta
    fast = SyntheticRta(seed=1, noise_db=0.0, wobble_db=0.0, level_100hz_db=-80.0, level_10khz_db=-80.0)
    slow = SyntheticRta(seed=1, noise_db=0.0, wobble_db=0.0, level_100hz_db=-80.0, level_10khz_db=-80.0)
    b = fast.inject_note(1000.0, -20.0, rise_frames=1)
    slow.inject_note(1000.0, -20.0)
    f0, s0 = fast.tick().values[b], slow.tick().values[b]      # age 0: both at the bottom of the ramp (-50 + bed)
    f1, s1 = fast.tick().values[b], slow.tick().values[b]      # age 1: the fast note has arrived, the slow one is 24 dB short
    assert f0 < -45 and s0 < -45
    assert f1 == pytest.approx(-20.0, abs=0.05) and s1 == pytest.approx(-44.0, abs=0.05)
    for _ in range(4):
        s_last = slow.tick().values[b]
    assert s_last == pytest.approx(-20.0, abs=0.05)
    assert fast.notes[0]["rise_frames"] == 1 and slow.notes[0]["rise_frames"] == SyntheticRta.NOTE_RISE_FRAMES
