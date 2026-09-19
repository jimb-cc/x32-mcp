"""scales.py — expected values come from docs/research/scales_params.md (worked examples, PDF tables)."""

import math

import pytest

from x32mcp.scales import (
    NEG_INF_DB,
    Scale,
    ScaleError,
    db_to_fader,
    enum_to_index,
    fader_to_db,
    format_db,
    index_to_enum,
    lin_to_value,
    log_to_value,
    parse_db,
    quantize,
    value_to_lin,
    value_to_log,
)

EQ_TYPE = ("LCut", "LShv", "PEQ", "VEQ", "HShv", "HCut")
RATIO = ("1.1", "1.3", "1.5", "2.0", "2.5", "3.0", "4.0", "5.0", "7.0", "10", "20", "100")
COLOR = ("OFF", "RD", "GN", "YE", "BL", "MG", "CY", "WH", "OFFi", "RDi", "GNi", "YEi", "BLi", "MGi", "CYi", "WHi")

FADER = Scale("level", steps=1024, unit="dB")
SEND = Scale("level", steps=161, unit="dB")
PAN = Scale("pan")
FREQ = Scale("log", lo=20, hi=20000, steps=201, unit="Hz")
Q = Scale("log", lo=10, hi=0.3, steps=72)
EQ_GAIN = Scale("lin", lo=-15, hi=15, steps=121, unit="dB")


# -- fader taper ---------------------------------------------------------------------------


@pytest.mark.parametrize("db,raw", [(-60.0, 0.0625), (-30.0, 0.25), (-10.0, 0.5), (0.0, 0.75), (10.0, 1.0)])
def test_taper_knees_round_trip_exactly(db, raw):
    assert db_to_fader(db) == pytest.approx(raw, abs=1e-12)
    assert fader_to_db(raw) == pytest.approx(db, abs=1e-9)
    assert fader_to_db(db_to_fader(db)) == pytest.approx(db, abs=1e-9)


def test_minus_90_is_minus_infinity():
    # research §2.3: -90 dB and -oo are the same float (0.0); the 1024 table has no -90.0 row
    assert db_to_fader(-90.0) == 0.0
    assert db_to_fader(NEG_INF_DB) == 0.0
    assert db_to_fader(-120.0) == 0.0
    assert fader_to_db(0.0) == NEG_INF_DB
    assert fader_to_db(-0.1) == NEG_INF_DB
    assert fader_to_db(db_to_fader(-90.0)) == NEG_INF_DB
    assert math.isinf(FADER.to_value(FADER.to_raw(-90.0)))


@pytest.mark.parametrize(
    "db,raw",
    [(3.0, 0.825), (-6.0, 0.6), (-20.5, 0.36875), (-89.5, 0.5 / 480), (-85.4, (90 - 85.4) / 480)],
)
def test_taper_research_worked_examples(db, raw):
    assert db_to_fader(db) == pytest.approx(raw, abs=1e-12)
    assert fader_to_db(raw) == pytest.approx(db, abs=1e-9)


def test_taper_clamps_and_rejects_nan():
    assert db_to_fader(15.0) == 1.0
    assert db_to_fader(10.0000001) == 1.0
    assert fader_to_db(1.5) == 10.0
    assert fader_to_db(1 / 1023) == pytest.approx(-89.5308, abs=1e-3)  # PDF table index 2 "-89.5"
    with pytest.raises(ScaleError):
        db_to_fader(float("nan"))


def test_taper_monotonic_over_1024_steps_and_round_trips():
    assert fader_to_db(0 / 1023) == NEG_INF_DB
    prev = NEG_INF_DB
    for i in range(1, 1024):
        x = i / 1023
        db = fader_to_db(x)
        assert db > prev
        prev = db
        assert db_to_fader(db) == pytest.approx(x, abs=1e-9)
        assert quantize(x, 1024) == x  # on-grid values are fixed points
    assert prev == 10.0


# -- quantisation --------------------------------------------------------------------------


def test_quantize_matches_console_grid():
    assert quantize(0.75, 1024) == pytest.approx(767 / 1023)  # 0.75*1023 = 767.25 -> row 768 of the PDF table
    assert quantize(0.825, 1024) == pytest.approx(844 / 1023)  # PDF p.13: +3 dB <-> 0.8250
    assert quantize(db_to_fader(-85.4), 1024) == pytest.approx(10 / 1023)
    assert round(fader_to_db(quantize(db_to_fader(-85.4), 1024)), 1) == -85.3  # PDF: "-85.4 will be kept as -85.3"
    assert quantize(db_to_fader(-8.0), 161) == pytest.approx(0.55)  # 161-table "0.5500 -8.0"
    assert quantize(db_to_fader(-87.0), 161) == pytest.approx(1 / 160)  # "0.0063 -87.0"
    assert quantize(db_to_fader(-59.0), 161) == pytest.approx(11 / 160)  # "0.0688 -59.0"


def test_quantize_rounds_half_away_from_zero_like_c_roundf():
    assert quantize(0.625, 5) == 0.75  # 0.625*4 = 2.5 exactly; Python round() would give 2 -> 0.5
    assert quantize(1.2, 1024) == 1.0
    assert quantize(-0.1, 1024) == 0.0
    assert math.copysign(1.0, quantize(-0.0, 1024)) == 1.0  # never -0.0
    for bad in (1, 0, None, True):
        with pytest.raises(ScaleError):
            quantize(0.5, bad)


# -- linear ---------------------------------------------------------------------------------


def test_lin_helpers():
    assert lin_to_value(0.5, -100, 100) == 0.0
    assert lin_to_value(0.0, -18, 18) == -18.0
    assert lin_to_value(1.5, -18, 18) == 18.0  # x clamped
    assert value_to_lin(50, -100, 100) == 0.75
    assert value_to_lin(-3, -18, 18) == pytest.approx(15 / 36)  # trim -3 dB
    assert value_to_lin(30, -12, 60) == pytest.approx(42 / 72)  # headamp 30 dB
    assert value_to_lin(-40, -80, 0) == 0.5  # gate thr
    assert value_to_lin(200, -100, 100) == 1.0
    assert value_to_lin(-200, -100, 100) == 0.0
    with pytest.raises(ScaleError):
        value_to_lin(1, 5, 5)


def test_lin_scale_with_steps_snaps_and_is_exact():
    assert EQ_GAIN.to_raw(3.25) == pytest.approx(73 / 120)  # (3.25+15)/30 = 0.60833
    assert EQ_GAIN.to_value(73 / 120) == 3.25
    assert EQ_GAIN.to_value(0.60833) == 3.25  # float32-ish noise from the desk snaps to the grid
    assert EQ_GAIN.to_raw(3.3) == pytest.approx(73 / 120)  # off-grid request snaps
    assert EQ_GAIN.to_raw(20) == 1.0 and EQ_GAIN.clamp(20) == 15.0
    assert EQ_GAIN.to_raw(-99) == 0.0
    knee = Scale("lin", lo=0, hi=5, steps=6)
    assert knee.to_value(0.4) == 2.0
    attack = Scale("lin", lo=0, hi=120, steps=121, unit="ms")
    assert attack.to_raw(20) == pytest.approx(20 / 120)
    mix = Scale("lin", lo=0, hi=100, steps=21, unit="%")
    assert mix.to_raw(52) == 0.5 and mix.to_value(0.5) == 50.0
    trim = Scale("lin", lo=-18, hi=18)  # unquantised
    assert trim.to_raw(-3) == pytest.approx(15 / 36)
    assert trim.to_value(0.5) == 0.0


# -- logarithmic ----------------------------------------------------------------------------


def test_log_frequency_endpoints_and_midpoint():
    assert log_to_value(0.0, 20, 20000) == 20.0
    assert log_to_value(1.0, 20, 20000) == pytest.approx(20000.0)
    mid = log_to_value(0.5, 20, 20000)
    assert mid == pytest.approx(20 * math.sqrt(1000), abs=1e-9)  # 632.4555…
    assert round(mid, 1) == 632.5  # f201[100] = " 632.5"
    assert value_to_log(mid, 20, 20000) == pytest.approx(0.5, abs=1e-12)
    assert value_to_log(20, 20, 20000) == 0.0
    assert value_to_log(20000, 20, 20000) == pytest.approx(1.0)
    assert value_to_log(5, 20, 20000) == 0.0  # out of range folds, no log(0)
    assert value_to_log(1000, 20, 20000) == pytest.approx(0.5663, abs=5e-5)


def test_log_scale_research_worked_examples():
    assert FREQ.to_raw(1000) == pytest.approx(0.565)  # round(113.26)/200 -> reads back 990.9
    assert round(FREQ.to_value(0.565), 1) == 990.9  # f201[113]
    assert round(FREQ.to_value(0.265), 1) == 124.7  # f201[53]; PDF p.12 example
    assert FREQ.to_raw(20) == 0.0 and FREQ.to_raw(20000) == 1.0
    assert FREQ.to_raw(5) == 0.0 and FREQ.to_raw(50000) == 1.0
    assert FREQ.clamp(50000) == 20000.0
    hpf = Scale("log", lo=20, hi=400, steps=101)
    assert hpf.to_raw(100) == pytest.approx(0.54)
    hold = Scale("log", lo=0.02, hi=2000, steps=101, unit="ms")
    assert hold.to_raw(100) == pytest.approx(0.74)
    release = Scale("log", lo=5, hi=4000, steps=101, unit="ms")
    assert release.to_raw(100) == pytest.approx(0.45)


def test_log_q_is_inverted():
    assert log_to_value(0.0, 10, 0.3) == 10.0
    assert log_to_value(1.0, 10, 0.3) == pytest.approx(0.3)
    assert value_to_log(10, 10, 0.3) == 0.0
    assert math.copysign(1.0, value_to_log(10, 10, 0.3)) == 1.0  # not -0.0
    assert value_to_log(0.3, 10, 0.3) == pytest.approx(1.0)
    assert value_to_log(2.0, 10, 0.3) == pytest.approx(math.log(0.2) / math.log(0.03))  # research: "0.4589" (truncated)
    assert Q.to_raw(2.0) == pytest.approx(33 / 71)  # PDF Q table row "0.4648 2.0"
    assert Q.to_value(0.4648) == pytest.approx(2.0, abs=0.05)
    assert Q.to_value(0.0) == 10.0 and Q.to_value(1.0) == pytest.approx(0.3)
    assert Q.clamp(20) == 10.0 and Q.clamp(0.1) == 0.3
    assert Q.to_raw(20) == 0.0 and Q.to_raw(0.1) == 1.0
    qs = [Q.to_value(i / 71) for i in range(72)]
    assert all(a > b for a, b in zip(qs, qs[1:]))


# -- enums -----------------------------------------------------------------------------------


def test_enum_helpers():
    assert enum_to_index("PEQ", EQ_TYPE) == 2
    assert enum_to_index("peq", EQ_TYPE) == 2
    assert enum_to_index(" hcut ", EQ_TYPE) == 5
    assert enum_to_index(3, EQ_TYPE) == 3  # int = index
    assert enum_to_index("10", RATIO) == 9  # numeric token
    assert enum_to_index(10, RATIO) == 10  # numeric index
    assert index_to_enum(2, EQ_TYPE) == "PEQ"
    assert index_to_enum(2.0, EQ_TYPE) == "PEQ"
    assert index_to_enum(15, COLOR) == "WHi"
    for bad in ("GEQ", 6, -1, None, True, 2.5):
        with pytest.raises(ScaleError):
            enum_to_index(bad, EQ_TYPE)
    for bad in (6, -1, None, True, 2.5):
        with pytest.raises(ScaleError):
            index_to_enum(bad, EQ_TYPE)


def test_enum_scale():
    s = Scale("enum", values=list(EQ_TYPE))  # list is normalised to a tuple
    assert s.values == EQ_TYPE and hash(s)
    assert s.to_value(3) == "VEQ"
    assert s.to_value("peq") == "PEQ"  # ",s" reply → canonical token
    assert s.to_raw("HCut") == 5
    assert s.to_raw("hshv") == 4
    assert s.to_raw(1) == 1
    assert s.clamp("peq") == "PEQ"
    assert s.to_value(None) is None
    with pytest.raises(ScaleError):
        s.to_raw("GEQ")
    with pytest.raises(ScaleError):
        s.to_value(9)
    with pytest.raises(ScaleError):
        Scale("enum")


# -- pan ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw,pan", [(0.0, -100), (0.5, 0), (1.0, 100), (0.75, 50), (0.6, 20)])
def test_pan_round_trip(raw, pan):
    v = PAN.to_value(raw)
    assert v == pan and isinstance(v, int)
    assert PAN.to_raw(pan) == pytest.approx(raw)


def test_pan_clamps_and_quantises():
    assert PAN.to_raw(150) == 1.0
    assert PAN.to_raw(-250) == 0.0
    assert PAN.clamp(150) == 100 and PAN.clamp(-250) == -100
    assert PAN.clamp(12.4) == 12
    assert PAN.to_value(0.7549) == 50  # snaps to the 0.01 grid


# -- level scales --------------------------------------------------------------------------


def test_fader_scale_1024():
    assert FADER.to_raw(0.0) == pytest.approx(767 / 1023)
    assert FADER.to_raw(3.0) == pytest.approx(844 / 1023)
    assert FADER.to_raw(-85.4) == pytest.approx(10 / 1023)
    assert round(FADER.to_value(10 / 1023), 1) == -85.3
    assert FADER.to_value(0.75) == 0.0
    assert FADER.to_value(1.0) == 10.0
    assert FADER.to_value(0) == NEG_INF_DB
    assert FADER.to_value(None) is None
    assert FADER.to_raw(NEG_INF_DB) == 0.0
    assert FADER.to_raw("-oo") == 0.0
    assert FADER.to_raw(-90.0) == 0.0
    assert FADER.to_raw(25.0) == 1.0
    assert FADER.clamp(25.0) == 10.0
    assert FADER.clamp(-95.0) == NEG_INF_DB
    assert FADER.clamp(-3.5) == -3.5
    with pytest.raises(ScaleError):
        FADER.to_raw(None)
    with pytest.raises(ScaleError):
        FADER.to_raw("loud")


def test_send_scale_161_and_unquantised_level():
    assert SEND.to_raw(-8.0) == pytest.approx(0.55)
    assert SEND.to_raw(-87.0) == pytest.approx(1 / 160)
    assert SEND.to_raw(-60.0) == pytest.approx(0.0625)
    assert SEND.to_raw(-59.0) == pytest.approx(11 / 160)
    assert round(SEND.to_value(SEND.to_raw(-8.3)), 2) == -8.25  # 161 grid: 0.25 dB steps above -10
    exact = Scale("level")
    assert exact.to_raw(0.0) == 0.75
    assert exact.to_raw(-20.5) == 0.36875


# -- int / bool / str ---------------------------------------------------------------------


def test_bool_scale():
    b = Scale("bool")
    assert b.to_value(1) is True and b.to_value(0) is False
    assert b.to_value("ON") is True and b.to_value("off") is False
    assert b.to_raw(True) == 1 and b.to_raw(False) == 0
    assert b.to_raw("on") == 1 and b.to_raw("OFF") == 0 and b.to_raw(0) == 0
    assert b.clamp("yes") is True
    with pytest.raises(ScaleError):
        b.to_raw("maybe")


def test_int_scale():
    i = Scale("int")
    assert i.to_value(3) == 3 and i.to_value(3.0) == 3
    assert i.to_raw(3.7) == 4 and i.to_raw(True) == 1
    assert i.clamp(500) == 500  # default range → no clamping
    icon = Scale("int", lo=1, hi=74)
    assert icon.clamp(0) == 1 and icon.clamp(100) == 74 and icon.to_raw(200) == 74
    with pytest.raises(ScaleError):
        i.to_raw("x")


def test_str_scale():
    s = Scale("str")
    assert s.to_value("Vox Tony") == "Vox Tony"
    assert s.to_raw(5) == "5"
    assert s.clamp("") == ""
    with pytest.raises(ScaleError):
        s.to_raw(None)


# -- dB text --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "db,text",
    [
        (NEG_INF_DB, "-oo"),
        (-12.34, "-12.3"),
        (2.0, "+2.0"),
        (10.0, "+10.0"),
        (0.0, "0.0"),
        (-0.04, "0.0"),
        (0.06, "+0.1"),
        (-85.3079, "-85.3"),
        (-89.5308, "-89.5"),
        (-8.25, "-8.3"),  # exact tie on the 161 send grid: PDF row "0.5437 -8.3" (half away from zero)
        (8.25, "+8.3"),
        (-0.05, "-0.1"),
    ],
)
def test_format_db(db, text):
    assert format_db(db) == text


def test_format_db_matches_desk_on_send_grid_ties():
    # 161-grid −10..+10 dB segment is a 0.25 dB grid, so every other row is an exact .x5 tie
    assert format_db(SEND.to_value(SEND.to_raw(-8.3))) == "-8.3"
    assert format_db(SEND.to_value(SEND.to_raw(-8.0))) == "-8.0"
    assert format_db(SEND.to_value(SEND.to_raw(3.0))) == "+3.0"
    with pytest.raises(ScaleError):
        format_db(float("nan"))


def test_parse_db():
    assert parse_db("-oo") == NEG_INF_DB
    assert parse_db(" -oo ") == NEG_INF_DB
    assert parse_db("+2.1") == 2.1
    assert parse_db("-0.0") == 0.0
    assert parse_db(3) == 3.0
    with pytest.raises(ScaleError):
        parse_db("loud")


# -- Scale validation / from_spec -----------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "bogus"},
        {"kind": "enum"},
        {"kind": "lin", "lo": 1, "hi": 1},
        {"kind": "log", "lo": 0, "hi": 100},
        {"kind": "log", "lo": 20, "hi": 20},
        {"kind": "level", "steps": 1},
        {"kind": "level", "steps": True},
    ],
)
def test_scale_validation(kwargs):
    with pytest.raises(ScaleError):
        Scale(**kwargs)


def test_from_spec():
    assert Scale.from_spec({"kind": "level", "steps": 1024, "unit": "dB"}) == FADER
    assert Scale.from_spec({"kind": "log", "lo": 20, "hi": 20000, "steps": 201, "unit": "Hz"}) == FREQ
    assert Scale.from_spec("bool") == Scale("bool")
    assert Scale.from_spec({"kind": "pan"}) == PAN
    e = Scale.from_spec({"enum": "color"}, enums={"color": list(COLOR)})
    assert e.kind == "enum" and e.values == COLOR
    e2 = Scale.from_spec({"values": ["OFF", "ON"]})
    assert e2.kind == "enum" and e2.to_value(1) == "ON"
    with pytest.raises(ScaleError):
        Scale.from_spec({"enum": "nope"}, enums={})
    with pytest.raises(ScaleError):
        Scale.from_spec({"lo": 0, "hi": 1})
    with pytest.raises(ScaleError):
        Scale.from_spec(42)
