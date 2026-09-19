"""Golden byte vectors, round trips and fuzz for x32mcp.osc (DESIGN.md §5).

Every expected byte string below is written out by hand from docs/research/transport.md
(§1 wire format, §3 /info //status captures, §6 /node, §8 /meters blob) — never derived
from ``encode``. ``~`` in the research notation is one NUL byte.
"""

from __future__ import annotations

import math
import random

import pytest

from x32mcp.osc import OscError, OscMessage, decode, encode

NUL = b"\x00"

# ---------------------------------------------------------------------------------------
# Golden vectors
# ---------------------------------------------------------------------------------------

# /info request, bare form: "/info~~~" = 8 bytes (transport.md §1.2, §3.1)
INFO_REQ = b"/info" + NUL * 3

# OSC-1.0-compliant form with an empty tag string: "/info~~~,~~~" = 12 bytes
INFO_REQ_COMPLIANT = b"/info" + NUL * 3 + b"," + NUL * 3

# "/ch/01/mix/fader~~~~,f~~[0.75]" = 28 bytes (README capture in transport.md §5.1)
# 0.75 as IEEE-754 single, big-endian: sign 0, exp 126, mantissa .1 -> 0x3F400000
FADER_075 = b"/ch/01/mix/fader" + NUL * 4 + b",f" + NUL * 2 + b"\x3f\x40\x00\x00"

# "/node~~~,s~~ch/01/config~~~~" = 28 bytes (transport.md §6.1)
NODE_REQ = b"/node" + NUL * 3 + b",s" + NUL * 2 + b"ch/01/config" + NUL * 4

# ,b blob of 8 bytes: address (12) + ",b~~" (4) + BE length 8 (4) + payload (8), no padding
BLOB8 = bytes(range(8))
BLOB8_MSG = b"/meters/6" + NUL * 3 + b",b" + NUL * 2 + b"\x00\x00\x00\x08" + BLOB8

# ,b blob of 5 bytes needs 3 bytes of NUL padding after the payload
BLOB5_MSG = b"/x" + NUL * 2 + b",b" + NUL * 2 + b"\x00\x00\x00\x05" + b"hello" + NUL * 3

# Verbatim real-desk /meters/6 capture, transport.md §8 (DOC 831): 40 bytes, 20-byte blob
METERS6_HEX = "2f6d65746572732f360000002c6200000000001404000000fd1d2137fdff7f3f0000803f6ebbd534"

# "/ch/01/gate/mode~~~~,i~~[3]" verbatim, transport.md §1.1 (DOC 604-655): 28 bytes
GATE_MODE_I_HEX = "2f63682f30312f676174652f6d6f6465000000002c69000000000003"
GATE_MODE_I = b"/ch/01/gate/mode" + NUL * 4 + b",i" + NUL * 2 + b"\x00\x00\x00\x03"

# same enum as a string: "/ch/01/gate/mode~~~~,s~~GATE~~~~"
GATE_MODE_S_HEX = "2f63682f30312f676174652f6d6f6465000000002c7300004741544500000000"

# "/ch/01/eq/1/q~~~,f~~[0.4648]" verbatim (DOC 604): float bits 0x3eedfa44
EQ_Q_HEX = "2f63682f30312f65712f312f710000002c6600003eedfa44"

# Several args: "/ch/01/eq/1 ,ifff 2 0.25 0.5 1.0" (shape from transport.md §1.4)
#   address 11 chars + NUL = 12 (aligned); ",ifff" + NUL + 2 pad = 8
#   0.25 = 0x3E800000, 0.5 = 0x3F000000, 1.0 = 0x3F800000
MULTI = (
    b"/ch/01/eq/1" + NUL
    + b",ifff" + NUL * 3
    + b"\x00\x00\x00\x02"
    + b"\x3e\x80\x00\x00"
    + b"\x3f\x00\x00\x00"
    + b"\x3f\x80\x00\x00"
)

# /info reply from an X32 Rack, transport.md §3.1: "/info~~~,ssss~~~V2.05~~~osc-server~~X32RACK~2.12~~~~"
INFO_REPLY = (
    b"/info" + NUL * 3
    + b",ssss" + NUL * 3
    + b"V2.05" + NUL * 3
    + b"osc-server" + NUL * 2
    + b"X32RACK" + NUL
    + b"2.12" + NUL * 4
)

# /status reply, transport.md §3.3: 52 bytes "/status~,sss~~~~active~~192.168.0.64~~~~osc-server~~"
STATUS_REPLY = (
    b"/status" + NUL
    + b",sss" + NUL * 4
    + b"active" + NUL * 2
    + b"192.168.0.64" + NUL * 4
    + b"osc-server" + NUL * 2
)

# No typetag string at all: "/xremote~~~~" = 12 bytes (transport.md §4.1) and a bare GET
XREMOTE = b"/xremote" + NUL * 4
FADER_GET = b"/ch/01/mix/fader" + NUL * 4

# /node reply: address "node" without a slash, string ends in "\n" (transport.md §6.2, README 40 B)
NODE_REPLY = b"node" + NUL * 4 + b",s" + NUL * 2 + b'/ch/01/config "" 0 OFF 0\n' + NUL * 3

# node-style write "/ ,s ch/01/mix/fader 3" (transport.md §6.6): "/~~~,s~~ch/01/mix/fader 3~~"
SLASH_WRITE = b"/" + NUL * 3 + b",s" + NUL * 2 + b"ch/01/mix/fader 3" + NUL * 3


def test_golden_info_request_bare():
    assert len(INFO_REQ) == 8
    assert encode("/info") == INFO_REQ
    assert decode(INFO_REQ) == OscMessage("/info", (), "")


def test_golden_info_request_compliant_empty_typetags():
    assert len(INFO_REQ_COMPLIANT) == 12
    assert encode("/info", typetags=",") == INFO_REQ_COMPLIANT
    assert encode("/info", typetags="") == INFO_REQ_COMPLIANT
    assert decode(INFO_REQ_COMPLIANT) == OscMessage("/info", (), ",")


def test_golden_fader_float():
    assert len(FADER_075) == 28
    assert encode("/ch/01/mix/fader", 0.75) == FADER_075
    m = decode(FADER_075)
    assert m.address == "/ch/01/mix/fader"
    assert m.typetags == ",f"
    assert m.args == (0.75,)
    assert type(m.args[0]) is float


def test_golden_node_request_string():
    assert len(NODE_REQ) == 28
    assert encode("/node", "ch/01/config") == NODE_REQ
    assert decode(NODE_REQ) == OscMessage("/node", ("ch/01/config",), ",s")


def test_golden_blob_8_bytes():
    assert len(BLOB8_MSG) == 12 + 4 + 4 + 8
    assert encode("/meters/6", BLOB8) == BLOB8_MSG
    m = decode(BLOB8_MSG)
    assert m == OscMessage("/meters/6", (BLOB8,), ",b")
    assert type(m.args[0]) is bytes


def test_golden_blob_5_bytes_padded():
    assert len(BLOB5_MSG) % 4 == 0
    assert encode("/x", b"hello") == BLOB5_MSG
    assert decode(BLOB5_MSG).args == (b"hello",)


def test_golden_meters6_verbatim_capture():
    wire = bytes.fromhex(METERS6_HEX)
    assert len(wire) == 40
    m = decode(wire)
    assert m.address == "/meters/6"
    assert m.typetags == ",b"
    (blob,) = m.args
    assert len(blob) == 20
    # Blob is returned raw: LE count 4 then 4 LE floats (decoding is meters.py's job).
    assert blob[:4] == b"\x04\x00\x00\x00"
    assert encode("/meters/6", blob) == wire


def test_golden_int_verbatim_capture():
    assert bytes.fromhex(GATE_MODE_I_HEX) == GATE_MODE_I
    assert encode("/ch/01/gate/mode", 3) == GATE_MODE_I
    m = decode(GATE_MODE_I)
    assert m == OscMessage("/ch/01/gate/mode", (3,), ",i")
    assert type(m.args[0]) is int


def test_golden_enum_string_verbatim_capture():
    wire = bytes.fromhex(GATE_MODE_S_HEX)
    assert encode("/ch/01/gate/mode", "GATE") == wire
    assert decode(wire) == OscMessage("/ch/01/gate/mode", ("GATE",), ",s")


def test_golden_eq_q_float_verbatim_capture():
    m = decode(bytes.fromhex(EQ_Q_HEX))
    assert m.address == "/ch/01/eq/1/q" and m.typetags == ",f"
    assert m.args[0] == pytest.approx(0.4648, abs=1e-6)


def test_golden_several_args():
    assert len(MULTI) == 12 + 8 + 16
    assert encode("/ch/01/eq/1", 2, 0.25, 0.5, 1.0) == MULTI
    assert decode(MULTI) == OscMessage("/ch/01/eq/1", (2, 0.25, 0.5, 1.0), ",ifff")


def test_golden_info_reply_four_strings():
    assert len(INFO_REPLY) == 52
    assert encode("/info", "V2.05", "osc-server", "X32RACK", "2.12") == INFO_REPLY
    assert decode(INFO_REPLY) == OscMessage("/info", ("V2.05", "osc-server", "X32RACK", "2.12"), ",ssss")


def test_golden_status_reply():
    assert len(STATUS_REPLY) == 52
    assert decode(STATUS_REPLY) == OscMessage("/status", ("active", "192.168.0.64", "osc-server"), ",sss")
    assert encode("/status", "active", "192.168.0.64", "osc-server") == STATUS_REPLY


def test_golden_no_typetags():
    assert len(XREMOTE) == 12
    assert encode("/xremote") == XREMOTE
    assert decode(XREMOTE) == OscMessage("/xremote", (), "")
    assert len(FADER_GET) == 20
    assert encode("/ch/01/mix/fader") == FADER_GET
    m = decode(FADER_GET)
    assert m.args == () and m.typetags == ""


def test_golden_node_reply_slashless_address():
    assert len(NODE_REPLY) == 40
    m = decode(NODE_REPLY)
    assert m.address == "node"
    assert m.args == ('/ch/01/config "" 0 OFF 0\n',)
    assert encode("node", '/ch/01/config "" 0 OFF 0\n') == NODE_REPLY


def test_golden_slash_write():
    assert len(SLASH_WRITE) == 28
    assert encode("/", "ch/01/mix/fader 3") == SLASH_WRITE
    assert decode(SLASH_WRITE) == OscMessage("/", ("ch/01/mix/fader 3",), ",s")


# ---------------------------------------------------------------------------------------
# encode: inference, override, errors
# ---------------------------------------------------------------------------------------


def test_encode_infers_bool_as_int():
    assert encode("/ch/01/mix/on", True) == b"/ch/01/mix/on" + NUL * 3 + b",i" + NUL * 2 + b"\x00\x00\x00\x01"
    assert encode("/ch/01/mix/on", False)[-4:] == b"\x00\x00\x00\x00"


def test_encode_typetags_override_forces_float():
    expect = b"/ch/01/mix/fader" + NUL * 4 + b",f" + NUL * 2 + b"\x3f\x80\x00\x00"
    assert encode("/ch/01/mix/fader", 1, typetags="f") == expect
    assert encode("/ch/01/mix/fader", 1, typetags=",f") == expect
    assert encode("/ch/01/mix/fader", True, typetags="f") == expect


def test_encode_typetags_override_int_from_integral_float():
    assert encode("/x", 3.0, typetags="i")[-4:] == b"\x00\x00\x00\x03"
    with pytest.raises(OscError):
        encode("/x", 0.5, typetags="i")


def test_encode_typetags_mismatch_and_unknown():
    with pytest.raises(OscError):
        encode("/x", 1, 2, typetags="i")
    with pytest.raises(OscError):
        encode("/x", 1, typetags="T")
    with pytest.raises(OscError):
        encode("/x", "s", typetags="i")
    with pytest.raises(OscError):
        encode("/x", 1, typetags="s")
    with pytest.raises(OscError):
        encode("/x", b"b", typetags="f")


def test_encode_rejects_bad_types_and_addresses():
    with pytest.raises(OscError):
        encode("/x", None)
    with pytest.raises(OscError):
        encode("/x", [1, 2])
    with pytest.raises(OscError):
        encode("")
    with pytest.raises(OscError):
        encode("/bad\x00addr")
    with pytest.raises(OscError):
        encode("/café")
    with pytest.raises(OscError):
        encode("/x", "nul\x00inside")


def test_encode_int32_range():
    assert encode("/x", -1)[-4:] == b"\xff\xff\xff\xff"
    assert encode("/x", 2**31 - 1)[-4:] == b"\x7f\xff\xff\xff"
    assert encode("/x", -(2**31))[-4:] == b"\x80\x00\x00\x00"
    with pytest.raises(OscError):
        encode("/x", 2**31)
    with pytest.raises(OscError):
        encode("/x", -(2**31) - 1)


def test_encode_float32_overflow():
    with pytest.raises(OscError):
        encode("/x", 1e300)


def test_encode_accepts_bytearray_and_memoryview_blobs():
    assert encode("/x", bytearray(b"ab")) == encode("/x", b"ab")
    assert encode("/x", memoryview(b"ab")) == encode("/x", b"ab")


def test_encode_empty_string_arg_is_four_nuls():
    assert encode("/x", "") == b"/x" + NUL * 2 + b",s" + NUL * 2 + NUL * 4


# ---------------------------------------------------------------------------------------
# decode: tolerance and errors
# ---------------------------------------------------------------------------------------


def test_decode_ignores_trailing_garbage():
    assert decode(FADER_075 + b"\xde\xad\xbe\xef") == decode(FADER_075)
    assert decode(INFO_REQ + b"garbage") == OscMessage("/info", (), "")


def test_decode_tolerates_missing_final_padding():
    # string without its trailing pad bytes
    assert decode(b"/node" + NUL * 3 + b",s" + NUL * 2 + b"ch/01/config" + NUL).args == ("ch/01/config",)
    # blob without its trailing pad bytes
    assert decode(b"/x" + NUL * 2 + b",b" + NUL * 2 + b"\x00\x00\x00\x05" + b"hello").args == (b"hello",)


def test_decode_non_comma_after_address_means_no_args():
    assert decode(b"/info" + NUL * 3 + b"xyz") == OscMessage("/info", (), "")


def test_decode_bundle_raises():
    with pytest.raises(OscError, match="bundle"):
        decode(b"#bundle" + NUL + b"\x00" * 8)


@pytest.mark.parametrize(
    "bad",
    [
        b"",
        b"/info",  # unterminated address
        NUL * 4,  # empty address
        b"/inf\xe9" + NUL * 3,  # non-ASCII address
        b"/x" + NUL * 2 + b",i",  # unterminated typetags
        b"/x" + NUL * 2 + b",T" + NUL * 2,  # unsupported tag
        b"/x" + NUL * 2 + b",i" + NUL * 2 + b"\x00\x00",  # truncated int
        b"/x" + NUL * 2 + b",f" + NUL * 2,  # missing float
        b"/x" + NUL * 2 + b",s" + NUL * 2 + b"abc",  # unterminated string
        b"/x" + NUL * 2 + b",b" + NUL * 2 + b"\x00\x00",  # truncated blob length
        b"/x" + NUL * 2 + b",b" + NUL * 2 + b"\x00\x00\x00\x09" + b"12345678",  # truncated blob
        b"/x" + NUL * 2 + b",b" + NUL * 2 + b"\xff\xff\xff\xff",  # absurd blob length
    ],
)
def test_decode_malformed_raises_oscerror(bad):
    with pytest.raises(OscError):
        decode(bad)


def test_decode_rejects_non_bytes():
    with pytest.raises(OscError):
        decode("not bytes")  # type: ignore[arg-type]


def test_decode_accepts_bytearray_and_memoryview():
    assert decode(bytearray(FADER_075)) == decode(FADER_075)
    assert decode(memoryview(FADER_075)) == decode(FADER_075)


def test_decode_invalid_utf8_in_string_never_raises():
    m = decode(b"/x" + NUL * 2 + b",s" + NUL * 2 + b"a\xffb" + NUL)
    assert m.args[0].startswith("a") and m.args[0].endswith("b")


def test_oscerror_is_valueerror():
    assert issubclass(OscError, ValueError)


def test_message_str_is_readable():
    assert str(decode(FADER_075)) == "/ch/01/mix/fader ,f 0.75"
    assert str(decode(NODE_REQ)) == '/node ,s "ch/01/config"'
    assert str(decode(BLOB8_MSG)) == "/meters/6 ,b <blob 8 B>"
    assert str(decode(XREMOTE)) == "/xremote"


# ---------------------------------------------------------------------------------------
# round trips
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address,args",
    [
        ("/info", ()),
        ("/ch/01/mix/fader", (0.75,)),
        ("/ch/01/mix/on", (1,)),
        ("/ch/01/config/name", ("Vox Tony",)),
        ("/ch/01/config/name", ("",)),
        ("/ch/01/config/name", ("café ♫",)),
        ("/node", ("ch/01/config",)),
        ("/", ('ch/01/config "Kick In" 1 RD 1',)),
        ("/meters", ("/meters/15", 1)),
        ("/meters", ("/meters/5", 3, 1, 40)),
        ("/formatsubscribe", ("/testme", "/ch/**/mix/on", 6, 9, 80)),
        ("/save", ("scene", 5, "GravelAxe", "sat night")),
        ("/meters/15", (bytes(range(256)) * 2,)),
        ("/x", (b"",)),
        ("/x", (b"a",)),
        ("/x", (b"abc",)),
        ("/x", (-(2**31), 2**31 - 1, 0)),
        ("/x", (float("inf"), float("-inf"), 0.0, -0.0)),
        ("/x", (1, 0.5, "s", b"b", 2, 0.25, "t", b"bb")),
        ("node", ('/ch/01/mix ON  +2.1 ON +0 OFF   -oo\n',)),
    ],
)
def test_round_trip(address, args):
    wire = encode(address, *args)
    assert len(wire) % 4 == 0
    m = decode(wire)
    assert m.address == address
    assert m.args == args
    if not args:
        assert m.typetags == ""  # bare form: no tag string on the wire at all
        assert encode(m.address) == wire
        return
    assert m.typetags == "," + "".join(
        "i" if isinstance(a, int) else "f" if isinstance(a, float) else "s" if isinstance(a, str) else "b"
        for a in args
    )
    assert encode(m.address, *m.args, typetags=m.typetags) == wire


def test_round_trip_float32_precision():
    # Python floats are 64-bit; the wire is float32. Anything representable survives exactly,
    # everything else comes back within float32 epsilon.
    for x in (0.0, 0.0625, 0.25, 0.5, 0.75, 1.0, 1 / 1023, 0.4648, 0.9, 123.456):
        got = decode(encode("/x", x)).args[0]
        assert got == pytest.approx(x, rel=1e-6)


def test_round_trip_nan():
    got = decode(encode("/x", float("nan"))).args[0]
    assert math.isnan(got)


# ---------------------------------------------------------------------------------------
# fuzz: decode returns an OscMessage or raises OscError, nothing else
# ---------------------------------------------------------------------------------------

GOOD = [
    INFO_REQ, INFO_REQ_COMPLIANT, FADER_075, NODE_REQ, BLOB8_MSG, BLOB5_MSG, GATE_MODE_I,
    MULTI, INFO_REPLY, STATUS_REPLY, XREMOTE, NODE_REPLY, SLASH_WRITE, bytes.fromhex(METERS6_HEX),
]


def _check(data: bytes) -> str:
    try:
        m = decode(data)
    except OscError:
        return "err"
    except Exception as e:  # pragma: no cover - the point of the test
        pytest.fail(f"decode raised {type(e).__name__} on {data!r}: {e}")
    assert isinstance(m, OscMessage)
    assert isinstance(m.address, str) and isinstance(m.args, tuple) and isinstance(m.typetags, str)
    return "ok"


def test_fuzz_random_bytes():
    rng = random.Random(20260919)
    seen = {"ok": 0, "err": 0}
    for _ in range(500):
        n = rng.randint(0, 96)
        seen[_check(bytes(rng.getrandbits(8) for _ in range(n)))] += 1
    assert seen["ok"] + seen["err"] == 500


def test_fuzz_mutated_valid_messages():
    rng = random.Random(1023)
    seen = {"ok": 0, "err": 0}
    for _ in range(500):
        data = bytearray(rng.choice(GOOD))
        op = rng.randrange(4)
        if op == 0 and len(data) > 1:  # truncate
            data = data[: rng.randrange(1, len(data))]
        elif op == 1:  # flip a byte
            i = rng.randrange(len(data))
            data[i] = rng.getrandbits(8)
        elif op == 2:  # insert junk
            i = rng.randrange(len(data) + 1)
            data[i:i] = bytes(rng.getrandbits(8) for _ in range(rng.randint(1, 8)))
        else:  # zero a run
            i = rng.randrange(len(data))
            data[i : i + rng.randint(1, 4)] = NUL * rng.randint(1, 4)
        seen[_check(bytes(data))] += 1
    assert seen["ok"] + seen["err"] == 500
    assert seen["ok"] > 0 and seen["err"] > 0  # mutations exercise both outcomes
