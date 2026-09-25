"""nodes.py — verbatim lines come from docs/research/transport.md §6.3-§6.4 and
docs/research/scales_params.md §13 (console-saved scene file, FW 2.x) and meters.md §5.1."""

from __future__ import annotations

import copy
import json
import math
import re
from types import SimpleNamespace

import pytest

from x32mcp import nodes
from x32mcp.descriptor import Descriptor, NodePath, ParamSpec
from x32mcp.nodes import (
    NODE_FORMATS,
    Change,
    DeskState,
    NodeParseError,
    SnapshotError,
    SnapshotStore,
    describe_changes,
    diff_states,
    dump_desk_state,
    format_token,
    parse_node_line,
    parse_token,
    render_node_line,
    restore_plan,
    split_node_line,
    tokenize,
    unquote,
)
from x32mcp.scales import NEG_INF_DB
from x32mcp.targets import Target

INF = math.inf
NEG = NEG_INF_DB


@pytest.fixture(scope="module")
def d() -> Descriptor:
    return Descriptor.load()


# ---------------------------------------------------------------------------------------------- helpers


def synth(spec: ParamSpec, i: int):
    """Deterministic engineering value representable at the node text precision."""
    fmt, kind = spec.node_fmt, spec.scale.kind

    def pick(opts):
        return opts[i % len(opts)]

    if fmt == "str":
        return pick(["Vox Tony", "", "1 Diazno", "Kick", "Tony's Vox"])
    if fmt == "token":
        return pick(["0", "-3.0", "11k9", "OFF", "FRONT", "2.4"])
    if fmt == "onoff":
        return bool(i % 2)
    if fmt == "enum":
        return spec.enum[i % len(spec.enum)]
    if fmt.startswith("bits"):
        return (i * 37) % (1 << int(fmt[4:]))
    if fmt == "int":
        if kind == "enum":
            return spec.enum[i % len(spec.enum)]
        if kind == "bool":
            return bool(i % 2)
        if kind == "int":
            return pick([0, 3, 42, 1])
        lo, hi = sorted((spec.scale.lo, spec.scale.hi))
        return float(pick([math.ceil(lo), math.floor(hi), int((lo + hi) / 2)]))
    if fmt == "sint":
        return pick([0, -100, 50, 100, -2])
    if fmt == "db1":
        return pick([NEG, -12.0, 0.0, 3.5, -0.5, -81.0, 10.0])
    if fmt == "db2":
        return pick([-3.25, 0.0, 12.5, -15.0, 1.5])
    if fmt == "sfloat1":
        return pick([8.8, -12.0, 0.0, 30.5])
    if fmt == "float1":
        lo, hi = sorted((spec.scale.lo, spec.scale.hi))
        return pick([lo, hi, round((lo + hi) / 2, 1)])
    if fmt == "float2":
        return pick([1.0, 0.25, 16.0])
    if fmt == "sig3":
        return pick([0.02, 2.0, 79.6, 100.0, 502.0, 14.0, 8.0, 2000.0])
    if fmt == "freq":
        return pick([124.7, 1020.0, 10020.0, 20000.0, 20.0, 5970.0, 632.5])
    if fmt == "pct":
        return pick([100.0, 45.0, 0.0])
    raise AssertionError(f"synth: unknown format {fmt}")


def synth_values(node: NodePath, seed: int = 0) -> dict:
    return {k: synth(s, seed + j) for j, (k, s) in enumerate(node)}


def same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == pytest.approx(b, abs=1e-9)
    return a == b


def full_state(d: Descriptor, seed: int = 0, **leaf_overrides) -> DeskState:
    st = DeskState(created="2026-09-19T12:00:00+02:00")
    for j, node in enumerate(d.nodes()):
        st.sections[node.path] = synth_values(node, seed + j)
    for addr, v in leaf_overrides.items():
        set_leaf(st, d, addr, v)
    return st


def set_leaf(st: DeskState, d: Descriptor, address: str, value) -> None:
    spec, vars = d.param_for_address(address)
    for node in d.nodes():
        if address in node.addresses:
            st.sections.setdefault(node.path, {})[node.fields[node.addresses.index(address)]] = value
            return
    raise AssertionError(address)


class StubConn:
    """Duck-typed stand-in for X32Connection: only node_many + status.console."""

    def __init__(self, lines: dict[str, str], drop=()):
        self.lines, self.drop, self.calls = lines, set(drop), []
        self.status = SimpleNamespace(console=SimpleNamespace(to_dict=lambda: {"name": "X32-FAKE", "model": "X32RACK", "firmware": "4.06"}))

    async def node_many(self, paths, concurrency=16):
        self.calls.append((list(paths), concurrency))
        return {p: (None if p in self.drop else self.lines.get(p)) for p in paths}


# ---------------------------------------------------------------------------------------------- formats


def test_every_device_yaml_format_is_implemented(d):
    used = d.node_formats_used()
    assert used <= NODE_FORMATS
    assert used == {"str", "int", "sint", "onoff", "enum", "db1", "db2", "float1", "float2", "freq", "pct",
                    "sfloat1", "sig3", "token", "bits6", "bits8", "bits9", "bits18"}


def test_round_trip_every_node_section(d):
    """Render → parse for all 2127 concrete node paths, padded and single-spaced."""
    n = 0
    for j, node in enumerate(d.nodes()):
        values = synth_values(node, j)
        for pad in (True, False):
            line = render_node_line(node.path, node, values, pad=pad)
            assert line.startswith(node.path + " ") or line == node.path
            assert len(tokenize(line)) == len(node.fields) + 1, line
            back = parse_node_line(line, node, strict=True)
            assert list(back) == list(node.fields)
            for k in node.fields:
                assert same(back[k], values[k]), (node.path, k, values[k], back[k], line)
        n += 1
    assert n == 2127


def test_single_spaced_render_has_no_padding(d):
    node = d.node("/ch/01/mix")
    line = render_node_line("/ch/01/mix", node, {"mix/on": True, "mix/fader": 2.1, "mix/st": True, "mix/pan": 0, "mix/mono": False, "mix/mlevel": NEG}, pad=False)
    assert line == "/ch/01/mix ON +2.1 ON +0 OFF -oo"
    assert "  " not in line


# ---------------------------------------------------------------------------------------------- verbatim research lines

VERBATIM = [
    ('/ch/01/config "Diazno" 1 CY 1', {"config/name": "Diazno", "config/icon": 1, "config/color": "CY", "config/source": "IN01"}),
    ('/ch/01/delay OFF   0.3', {"delay/on": False, "delay/time": 0.3}),
    ('/ch/01/preamp +0.0 OFF OFF 24  79', {"preamp/trim": 0.0, "preamp/invert": False, "preamp/hpon": False, "preamp/hpslope": "24", "preamp/hpf": 79.0}),
    ('/ch/01/gate ON EXP4 -46.5 27.0 20  100  576 0', {"gate/on": True, "gate/mode": "EXP4", "gate/thr": -46.5, "gate/range": 27.0, "gate/attack": 20.0, "gate/hold": 100.0, "gate/release": 576.0, "gate/keysrc": "OFF"}),
    ('/ch/01/gate/filter OFF 3.0 1k39', {"gate/filter/on": False, "gate/filter/type": "3.0", "gate/filter/f": 1390.0}),
    ('/ch/01/dyn ON COMP PEAK LIN -26.0 3.0 2 8.00 71 0.03  538 POST 0 100 OFF',
     {"dyn/on": True, "dyn/mode": "COMP", "dyn/det": "PEAK", "dyn/env": "LIN", "dyn/thr": -26.0, "dyn/ratio": "3.0", "dyn/knee": 2.0,
      "dyn/mgain": 8.0, "dyn/attack": 71.0, "dyn/hold": 0.03, "dyn/release": 538.0, "dyn/pos": "POST", "dyn/keysrc": "OFF", "dyn/mix": 100.0, "dyn/auto": False}),
    ('/ch/01/insert ON PRE FX4L', {"insert/on": True, "insert/pos": "PRE", "insert/sel": "FX4L"}),
    ('/ch/01/eq ON', {"eq/on": True}),
    ('/ch/01/eq/1 PEQ 164.4 -3.75 1.8', {"eq/1/type": "PEQ", "eq/1/f": 164.4, "eq/1/g": -3.75, "eq/1/q": 1.8}),
    ('/ch/01/eq/4 VEQ 5k97 +1.50 2.0', {"eq/4/type": "VEQ", "eq/4/f": 5970.0, "eq/4/g": 1.5, "eq/4/q": 2.0}),
    ('/ch/01/mix ON  +2.1 ON +0 OFF   -oo', {"mix/on": True, "mix/fader": 2.1, "mix/st": True, "mix/pan": 0, "mix/mono": False, "mix/mlevel": NEG}),
    ('/ch/01/mix/01 ON   -oo +0 PRE', {"mix/01/on": True, "mix/01/level": NEG, "mix/01/pan": 0, "mix/01/type": "PRE", "mix/01/panFollow": None}),
    ('/ch/01/mix/02 ON   -oo', {"mix/02/on": True, "mix/02/level": NEG}),
    ('/ch/01/mix/15 ON   0.0 +0 POST', {"mix/15/on": True, "mix/15/level": 0.0, "mix/15/pan": 0, "mix/15/type": "POST", "mix/15/panFollow": None}),
    ('/ch/01/grp %00000001 %000000', {"grp/dca": 1, "grp/mute": 0}),
    ('/auxin/01/mix ON   -oo ON -100 OFF   -oo', {"mix/on": True, "mix/fader": NEG, "mix/st": True, "mix/pan": -100, "mix/mono": False, "mix/mlevel": NEG}),
    ('/auxin/01/preamp +8.8 OFF', {"preamp/trim": 8.8, "preamp/invert": False}),
    ('/fxrtn/01/config "Reverb L" 61 MG', {"config/name": "Reverb L", "config/icon": 61, "config/color": "MG"}),
    ('/bus/01/config "1 Diazno" 53 WH', {"config/name": "1 Diazno", "config/icon": 53, "config/color": "WH"}),
    ('/bus/01/mix OFF   0.0 OFF +0 OFF -81.0', {"mix/on": False, "mix/fader": 0.0, "mix/st": False, "mix/pan": 0, "mix/mono": False, "mix/mlevel": -81.0}),
    ('/bus/01/mix/01 ON   -oo +0 POST', {"mix/01/on": True, "mix/01/level": NEG, "mix/01/pan": 0, "mix/01/type": "POST", "mix/01/panFollow": None}),
    ('/bus/01/eq/6 HCut 20k00 -0.50 2.0', {"eq/6/type": "HCut", "eq/6/f": 20000.0, "eq/6/g": -0.5, "eq/6/q": 2.0}),
    ('/mtx/01/preamp OFF', {"preamp/invert": False}),
    ('/mtx/01/dyn OFF COMP RMS LOG 0.0 3.0 1 0.00 10 10.0  151 POST 100 OFF',
     {"dyn/on": False, "dyn/mode": "COMP", "dyn/det": "RMS", "dyn/env": "LOG", "dyn/thr": 0.0, "dyn/ratio": "3.0", "dyn/knee": 1.0, "dyn/mgain": 0.0,
      "dyn/attack": 10.0, "dyn/hold": 10.0, "dyn/release": 151.0, "dyn/pos": "POST", "dyn/mix": 100.0, "dyn/auto": False}),
    ('/mtx/01/mix ON -10.0', {"mix/on": True, "mix/fader": -10.0}),
    ('/main/st/mix ON   -oo +0', {"mix/on": True, "mix/fader": NEG, "mix/pan": 0}),
    ('/main/st/mix/01 ON   0.0 +0 POST', {"mix/01/on": True, "mix/01/level": 0.0, "mix/01/pan": 0, "mix/01/type": "POST", "mix/01/panFollow": None}),
    ('/main/m/mix ON -16.8', {"mix/on": True, "mix/fader": -16.8}),
    ('/main/m/mix/03 ON   -oo -2 POST', {"mix/03/on": True, "mix/03/level": NEG, "mix/03/pan": -2, "mix/03/type": "POST", "mix/03/panFollow": None}),
    ('/dca/1 OFF  -8.3', {"on": False, "fader": -8.3}),
    ('/dca/1/config "Vocals" 43 CY', {"config/name": "Vocals", "config/icon": 43, "config/color": "CY"}),
    ('/dca/8/config "" 1 OFF', {"config/name": "", "config/icon": 1, "config/color": "OFF"}),
    ('/headamp/000 +0.0 OFF', {"gain": 0.0, "phantom": False}),
    ('/headamp/124 +0.0 OFF', {"gain": 0.0, "phantom": False}),
    ('/config/mute OFF OFF OFF OFF OFF OFF', {f"mute/{i}": False for i in range(1, 7)}),
    ('/config/solo   0.0 AUX78 0.0 PFL PFL PFL ON OFF ON -22 OFF OFF OFF   0.3 OFF OFF OFF',
     {"solo/level": 0.0, "solo/source": "AUX78", "solo/sourcetrim": 0.0, "solo/chmode": "PFL", "solo/busmode": "PFL", "solo/dcamode": "PFL",
      "solo/exclusive": True, "solo/followsel": False, "solo/followsolo": True, "solo/dimatt": -22.0, "solo/dim": False, "solo/mono": False,
      "solo/delay": False, "solo/delaytime": 0.3, "solo/masterctrl": False, "solo/mute": False, "solo/dimpfl": False}),
    ('/config/routing/IN A1-8 A9-16 A17-24 B1-8 AUX1-4',
     {"routing/IN/1-8": "A1-8", "routing/IN/9-16": "A9-16", "routing/IN/17-24": "A17-24", "routing/IN/25-32": "B1-8", "routing/IN/AUX": "AUX1-4"}),
    ('/config/routing REC', {"routing/routswitch": "REC"}),
    ('/-show/showfile/scene/001 "AAA" "aaa" %111111110 1',
     {"showfile/scene/001/name": "AAA", "showfile/scene/001/notes": "aaa", "showfile/scene/001/safes": 0b111111110, "showfile/scene/001/hasdata": 1}),
    ('/-show/showfile/show "MyShow" 0 0 0 0 0 0 0 0 0 0 "2.08"',
     {"showfile/show/name": "MyShow", **{f"showfile/show/{k}": 0 for k in ("inputs", "mxsends", "mxbuses", "console", "chan16", "chan32", "return", "buses", "lrmtxdca", "effects")}}),
    ('/-show/prepos/current 0', {"prepos/current": 0}),
    ('/-stat/rtasource 168', {"rtasource": 168}),
    ('/-stat/rtamodeeq BAR', {"rtamodeeq": "BAR"}),
    ('/-prefs/rta 50% 24 ON 72 POST BAR %000100 RMS 1.00 OFF',
     {"rta/visibility": "50%", "rta/gain": 24.0, "rta/autogain": True, "rta/source": "MAIN", "rta/pos": "POST", "rta/mode": "BAR",
      "rta/options": 4, "rta/det": "RMS", "rta/decay": 1.0, "rta/peakhold": "OFF"}),
    ('/fx/1 VREV', {"type": "VREV"}),
    ('/fx/5 GEQ2', {"type": "GEQ2"}),
    ('/fx/1/source MIX15 MIX15', {"source/l": "MIX15", "source/r": "MIX15"}),
    # output taps (fx_routing_scenes.md §4.7, X32.c case OMAIN): src as int, pos token, invert token
    ('/outputs/main/01 4 POST OFF', {"main/01/src": "MixBus 01", "main/01/pos": "POST", "main/01/invert": False}),
    ('/outputs/main/11 6 PRE+M ON', {"main/11/src": "MixBus 03", "main/11/pos": "PRE+M", "main/11/invert": True}),
    ('/outputs/aux/06 76 IN/LC OFF', {"aux/06/src": "Talkback", "aux/06/pos": "IN/LC", "aux/06/invert": False}),
]

# Lines whose rendering must match the console text byte for byte (padding included).
EXACT = [line for line, _ in VERBATIM if not line.startswith("/-show/showfile/show ")]


@pytest.mark.parametrize("line,expected", VERBATIM, ids=[v[0].split(" ")[0] + f"#{i}" for i, v in enumerate(VERBATIM)])
def test_verbatim_lines_parse(d, line, expected):
    path, _ = split_node_line(line)
    node = d.node(path)
    got = parse_node_line(line, node, strict=True)
    assert list(got) == list(node.fields)
    for k, v in expected.items():
        assert same(got[k], v), (k, got[k], v)
    assert set(got) == set(expected)


@pytest.mark.parametrize("line", EXACT, ids=[l.split(" ")[0] + f"#{i}" for i, l in enumerate(EXACT)])
def test_render_reproduces_console_text_exactly(d, line):
    """Console padding (research transport.md §6.3): levels width 5, hold/release width 4, HPF width 3."""
    path, _ = split_node_line(line)
    node = d.node(path)
    values = parse_node_line(line, node, strict=True)
    assert render_node_line(path, node, values) == line
    assert len(EXACT) >= 5


def test_show_line_extra_firmware_token_ignored(d):
    line = '/-show/showfile/show "MyShow" 0 0 0 0 0 0 0 0 0 0 "2.08"'
    node = d.node("/-show/showfile/show")
    values = parse_node_line(line, node)
    assert render_node_line(node.path, node, values) == '/-show/showfile/show "MyShow" 0 0 0 0 0 0 0 0 0 0'


def test_fw3_send_line_with_panfollow_and_emulator_spacing(d):
    node = d.node("/ch/01/mix/01")
    got = parse_node_line("/ch/01/mix/01 OFF -oo +0 EQ-> 0", node, strict=True)
    assert got == {"mix/01/on": False, "mix/01/level": NEG, "mix/01/pan": 0, "mix/01/type": "EQ->", "mix/01/panFollow": False}
    got = parse_node_line("ch/01/mix/01 ON -20.5 -50 GRP 1", node, strict=True)  # leading slash optional (/ write form)
    assert got["mix/01/panFollow"] is True and got["mix/01/level"] == -20.5 and got["mix/01/pan"] == -50
    assert render_node_line("/ch/01/mix/01", node, got, pad=False) == "/ch/01/mix/01 ON -20.5 -50 GRP 1"


def test_emulator_dyn_defaults_parse(d):
    node = d.node("/ch/01/dyn")
    got = parse_node_line("/ch/01/dyn OFF COMP RMS LOG 0.0 3.0 0 0.0 0 0.02 5 POST 0 100 OFF", node, strict=True)
    assert got["dyn/hold"] == 0.02 and got["dyn/release"] == 5.0 and got["dyn/mgain"] == 0.0 and got["dyn/mix"] == 100.0


def test_fx_par_line_64_tokens_verbatim(d):
    tail = "40 2.4 100 OFF FRONT 0.0 76 11k9 1.12 0.72" + " 0" * 54
    line = "/fx/1/par " + tail
    node = d.node("/fx/1/par")
    got = parse_node_line(line, node, strict=True)
    assert len(got) == 64 and got["par/01"] == "40" and got["par/08"] == "11k9" and got["par/64"] == "0"
    assert render_node_line("/fx/1/par", node, got) == line


def test_automix_section_with_explicit_specs(d):
    specs = [d.param("ch", "automix/group"), d.param("ch", "automix/weight")]
    got = parse_node_line("/ch/01/automix OFF -12.0", specs, strict=True)
    assert got == {"automix/group": "OFF", "automix/weight": -12.0}
    assert render_node_line("/ch/01/automix", specs, got) == "/ch/01/automix OFF -12.0"


# ---------------------------------------------------------------------------------------------- tokens


def test_tokenize_quotes_and_whitespace():
    assert tokenize('/ch/01/config "Vox Tony" 1   CY 1\n') == ['/ch/01/config', '"Vox Tony"', '1', 'CY', '1']
    assert tokenize('/dca/8/config "" 1 OFF') == ['/dca/8/config', '""', '1', 'OFF']
    assert tokenize('  /x  "a  b"   c ') == ['/x', '"a  b"', 'c']
    assert tokenize('/x "unterminated rest') == ['/x', '"unterminated rest']
    assert tokenize("") == []
    assert unquote('"Kick Drum"') == "Kick Drum" and unquote('""') == "" and unquote("PEQ") == "PEQ"


def test_split_node_line_normalises_leading_slash():
    assert split_node_line('ch/01/config "" 1 YE 1') == ("/ch/01/config", ['""', '1', 'YE', '1'])
    assert split_node_line("/fx/1 GEQ2") == ("/fx/1", ["GEQ2"])
    with pytest.raises(NodeParseError):
        split_node_line("   ")


def test_names_with_spaces_apostrophes_and_empty(d):
    node = d.node("/ch/05/config")
    for name in ("Vox Tony", "", "Tony's Vox", "1 Diazno", "  padded  "):
        line = render_node_line("/ch/05/config", node, {"config/name": name, "config/icon": 3, "config/color": "YE", "config/source": "IN05"})
        assert line == f'/ch/05/config "{name}" 3 YE 5'
        assert parse_node_line(line, node, strict=True)["config/name"] == name


def test_bare_string_token_accepted(d):
    node = d.node("/ch/05/config")
    assert parse_node_line("/ch/05/config Kick 3 YE 5", node, strict=True)["config/name"] == "Kick"


# ---------------------------------------------------------------------------------------------- single-token formats


@pytest.mark.parametrize("hz,text", [(20.0, "20.0"), (124.7, "124.7"), (632.5, "632.5"), (990.9, "990.9"), (1020.0, "1k02"), (2510.0, "2k51"),
                                     (5970.0, "5k97"), (10020.0, "10k02"), (11900.0, "11k90"), (20000.0, "20k00")])
def test_freq_format_and_parse(d, hz, text):
    spec = d.param("ch", "eq/{band}/f")
    assert format_token(spec, hz) == text
    assert parse_token(spec, text) == pytest.approx(hz)


def test_freq_parse_k_notation_digit_rules(d):
    # scales_params.md §0: 1 digit after k ×100, 2 digits ×10, 3 digits ×1
    spec = d.param("ch", "eq/{band}/f")
    assert parse_token(spec, "11k9") == 11900.0
    assert parse_token(spec, "1k02") == 1020.0
    assert parse_token(spec, "10k37") == 10370.0
    assert parse_token(spec, "2k005") == 2005.0
    assert parse_token(spec, "k5") == 500.0
    assert parse_token(spec, "1000") == 1000.0


@pytest.mark.parametrize("db,text", [(NEG, "  -oo"), (-90.0, "  -oo"), (0.0, "  0.0"), (-0.0, "  0.0"), (-0.04, "  0.0"), (2.1, " +2.1"),
                                     (-9.9, " -9.9"), (-48.3, "-48.3"), (10.0, "+10.0"), (-85.31, "-85.3")])
def test_db1_render_padded(d, db, text):
    spec = d.param("ch", "mix/fader")
    assert format_token(spec, db) == text
    assert format_token(spec, db, pad=False) == text.strip()


@pytest.mark.parametrize("text,db", [("-oo", NEG), ("0.0", 0.0), ("+0.0", 0.0), ("-0.0", 0.0), ("+2.1", 2.1), ("-85.3", -85.3), ("3", 3.0), ("  -9.9", -9.9)])
def test_db1_parse_accepts_all_console_spellings(d, text, db):
    assert parse_token(d.param("ch", "mix/fader"), text) == db


def test_db2_sfloat1_float1_float2_pct_sint(d):
    assert format_token(d.param("ch", "eq/{band}/g"), 0.0) == "+0.00"
    assert format_token(d.param("ch", "eq/{band}/g"), -0.001) == "+0.00"
    assert format_token(d.param("ch", "eq/{band}/g"), -3.25) == "-3.25"
    assert format_token(d.param("ch", "preamp/trim"), 0.0) == "+0.0"
    assert format_token(d.param("headamp", "gain"), 30.5) == "+30.5"
    assert format_token(d.param("ch", "gate/thr"), -46.5) == "-46.5"
    assert format_token(d.param("ch", "gate/range"), 27.0) == "27.0"
    assert format_token(d.param("ch", "eq/{band}/q"), 10.0) == "10"  # console prints the Q maximum as 10
    assert format_token(d.param("ch", "eq/{band}/q"), 0.3) == "0.3"
    assert format_token(d.param("ch", "delay/time"), 0.3) == "  0.3"
    assert format_token(d.param("prefs", "rta/decay"), 1.0) == "1.00"
    assert format_token(d.param("ch", "dyn/mix"), 100.0) == "100"
    assert format_token(d.param("ch", "mix/pan"), 0) == "+0"
    assert format_token(d.param("ch", "mix/pan"), -100) == "-100"
    assert parse_token(d.param("ch", "mix/pan"), "+50") == 50


@pytest.mark.parametrize("v,text", [(0.02, "0.02"), (0.03, "0.03"), (2.0, "2.00"), (8.0, "8.00"), (14.0, "14.0"), (79.6, "79.6"), (100.0, " 100"), (502.0, " 502"), (2000.0, "2000")])
def test_sig3_console_precision(d, v, text):
    assert format_token(d.param("ch", "gate/hold"), v) == text
    assert parse_token(d.param("ch", "gate/hold"), text) == pytest.approx(v)


def test_int_format_polymorphism(d):
    # enum scale: the desk prints the index
    assert format_token(d.param("ch", "config/source"), "IN05") == "5"
    assert parse_token(d.param("ch", "config/source"), "5") == "IN05"
    assert parse_token(d.param("ch", "gate/keysrc"), "0") == "OFF"
    assert format_token(d.param("prefs", "rta/source"), "BUS03") == "52"
    # bool scale (panFollow)
    assert format_token(d.param("ch", "mix/{send:02d}/panFollow"), True) == "1"
    assert parse_token(d.param("ch", "mix/{send:02d}/panFollow"), "0") is False
    assert parse_token(d.param("ch", "mix/{send:02d}/panFollow"), "ON") is True  # PDF spelling, UNCONFIRMED on FW 4.x
    assert parse_token(d.param("ch", "mix/{send:02d}/panFollow"), "OFF") is False
    # log/lin scales: rounded, padded per console column
    assert format_token(d.param("ch", "preamp/hpf"), 79.3) == " 79"
    assert format_token(d.param("ch", "preamp/hpf"), 144.0) == "144"
    assert parse_token(d.param("ch", "preamp/hpf"), "89.4") == 89.4  # emulator prints f101[] decimals
    assert format_token(d.param("ch", "gate/release"), 9.0) == "   9"
    assert format_token(d.param("ch", "gate/attack"), 20.0) == "20"
    assert format_token(d.param("config", "solo/dimatt"), -22.0) == "-22"
    # int scale
    assert format_token(d.param("ch", "config/icon"), 3) == "3"
    assert parse_token(d.param("ch", "config/icon"), "3") == 3


def test_onoff_enum_bits_token(d):
    on = d.param("ch", "mix/on")
    assert parse_token(on, "ON") is True and parse_token(on, "off") is False and parse_token(on, "1") is True
    assert format_token(on, True) == "ON" and format_token(on, 0) == "OFF"
    with pytest.raises(NodeParseError):
        parse_token(on, "maybe")
    color = d.param("ch", "config/color")
    assert parse_token(color, "ye") == "YE"
    assert parse_token(color, "NEWTOKEN") == "NEWTOKEN"  # unknown tokens kept verbatim
    with pytest.raises(NodeParseError):
        format_token(color, "NEWTOKEN")
    dca = d.param("ch", "grp/dca")
    assert parse_token(dca, "%00000011") == 3 and parse_token(dca, "5") == 5
    assert format_token(dca, 3) == "%00000011"
    assert format_token(d.param("ch", "grp/mute"), 32) == "%100000"
    assert format_token(d.param("show", "showfile/scene/{idx:03d}/safes"), 0x106) == "%100000110"
    assert format_token(d.param("config", "talk/A/destmap"), 1 << 17) == "%100000000000000000"
    with pytest.raises(NodeParseError):
        parse_token(dca, "%0x1")
    par = d.param("fx", "par/01")
    assert parse_token(par, "11k9") == "11k9" and format_token(par, "-3.0") == "-3.0" and format_token(par, 0.0) == "0"


# ---------------------------------------------------------------------------------------------- tolerance


def test_missing_trailing_tokens_and_extra_tokens(d):
    node = d.node("/ch/01/mix")
    got = parse_node_line("/ch/01/mix ON -12.0", node)
    assert got == {"mix/on": True, "mix/fader": -12.0, "mix/st": None, "mix/pan": None, "mix/mono": None, "mix/mlevel": None}
    got = parse_node_line("/ch/01/mix ON -12.0 ON +0 OFF -oo EXTRA 1 2", node, strict=True)
    assert got["mix/mlevel"] == NEG and len(got) == 6


def test_unreadable_token_is_none_unless_strict(d, caplog):
    node = d.node("/ch/01/mix")
    got = parse_node_line("/ch/01/mix ON abc ON +0 OFF -oo", node)
    assert got["mix/fader"] is None and got["mix/pan"] == 0
    with pytest.raises(NodeParseError, match="mix/fader"):
        parse_node_line("/ch/01/mix ON abc ON +0 OFF -oo", node, strict=True)


def test_render_partial_and_hole(d):
    node = d.node("/ch/01/mix/01")
    assert render_node_line("/ch/01/mix/01", node, {"mix/01/on": True, "mix/01/level": NEG, "mix/01/pan": 0, "mix/01/type": "PRE"}) == "/ch/01/mix/01 ON   -oo +0 PRE"
    assert render_node_line("ch/01/mix/01", node, {"mix/01/on": True}) == "/ch/01/mix/01 ON"
    assert render_node_line("/ch/01/mix/01", node, {}) == "/ch/01/mix/01"
    with pytest.raises(NodeParseError, match="after missing field"):
        render_node_line("/ch/01/mix/01", node, {"mix/01/on": True, "mix/01/pan": 0})
    # template keys are accepted as a fallback
    assert render_node_line("/ch/01/mix/01", node, {"mix/{send:02d}/on": False, "mix/{send:02d}/level": -3.0}) == "/ch/01/mix/01 OFF  -3.0"


def test_render_keys_and_bad_key_count(d):
    node = d.node("/dca/1")
    assert render_node_line("/dca/1", node.specs, {"on": False, "fader": -8.3}) == "/dca/1 OFF  -8.3"
    assert render_node_line("/dca/1", node.specs, {"a": True, "b": 10.0}, keys=["a", "b"]) == "/dca/1 ON +10.0"
    with pytest.raises(NodeParseError):
        parse_node_line("/dca/1 ON +10.0", node.specs, keys=["only-one"])


# ---------------------------------------------------------------------------------------------- DeskState


def test_desk_state_json_round_trips_minus_infinity(d):
    st = full_state(d)
    set_leaf(st, d, "/ch/05/mix/fader", NEG)
    set_leaf(st, d, "/ch/05/mix/03/level", NEG)
    set_leaf(st, d, "/ch/05/config/name", "Vox Tony")
    st.scene = {"index": 7, "name": "Molecules"}
    st.console = {"name": "X32-FAKE"}
    st.missing = ["/fx/8/par"]
    text = st.to_json()
    data = json.loads(text)
    assert data["sections"]["/ch/05/mix"]["mix/fader"] == "-oo"
    assert data["sections"]["/ch/05/mix/03"]["mix/03/level"] == "-oo"
    assert "-Infinity" not in text and "Infinity" not in text
    back = DeskState.from_json(text)
    assert back.get("/ch/05/mix/fader") == NEG and math.isinf(back.get("/ch/05/mix/03/level"))
    assert back.get("/ch/05/config/name") == "Vox Tony"
    assert back.sections == st.sections and back.scene == st.scene and back.console == st.console and back.missing == st.missing
    assert back.created == st.created
    assert DeskState.from_json(back.to_json()) == back


def test_desk_state_get_joins_section_and_field(d):
    st = full_state(d)
    set_leaf(st, d, "/ch/05/mix/03/level", -17.0)
    set_leaf(st, d, "/dca/1/on", False)
    set_leaf(st, d, "/-stat/rtasource", 168)
    set_leaf(st, d, "/main/st/mix/pan", 12)
    set_leaf(st, d, "/config/routing/IN/1-8", "A1-8")
    set_leaf(st, d, "/-show/showfile/scene/007/name", "Molecules")
    set_leaf(st, d, "/headamp/005/gain", 30.5)
    set_leaf(st, d, "/fx/1/par/12", "-3.0")
    set_leaf(st, d, "/ch/05/eq/2/g", -3.0)
    set_leaf(st, d, "/ch/05/gate/filter/f", 1390.0)
    assert st.get("/ch/05/mix/03/level") == -17.0
    assert st.get("/dca/1/on") is False
    assert st.get("/-stat/rtasource") == 168
    assert st.get("/main/st/mix/pan") == 12
    assert st.get("/config/routing/IN/1-8") == "A1-8"
    assert st.get("/-show/showfile/scene/007/name") == "Molecules"
    assert st.get("/headamp/005/gain") == 30.5
    assert st.get("/fx/1/par/12") == "-3.0"
    assert st.get("/ch/05/eq/2/g") == -3.0 and st.get("/ch/05/gate/filter/f") == 1390.0
    assert st.get("/ch/99/mix/fader") is None and st.get("/nope", "dflt") == "dflt"
    assert "/ch/05/mix/fader" in st and "/ch/05/mix" not in st
    # every leaf address of the descriptor resolves
    addrs = set(st.addresses())
    for node in d.nodes():
        for a in node.addresses:
            assert a in addrs, a
    assert len(addrs) == sum(len(n.fields) for n in d.nodes())
    # index follows later edits
    st.sections["/ch/05/mix"]["mix/fader"] = -1.0
    assert st.get("/ch/05/mix/fader") == -1.0
    del st.sections["/ch/05/mix"]
    assert st.get("/ch/05/mix/fader") is None


def test_desk_state_from_dict_rejects_garbage():
    with pytest.raises(NodeParseError):
        DeskState.from_json("{not json")
    with pytest.raises(NodeParseError):
        DeskState.from_dict({"sections": {"/ch/01/mix": "nope"}})
    empty = DeskState.from_dict({})
    assert empty.sections == {} and empty.scene is None and empty.get("/x") is None


# ---------------------------------------------------------------------------------------------- dump


async def test_dump_with_stub_node_many(d):
    lines = {n.path: render_node_line(n.path, n, synth_values(n, j)) for j, n in enumerate(d.nodes())}
    lines["/-show/prepos/current"] = "/-show/prepos/current 7"
    lines["/-show/showfile/scene/007"] = '/-show/showfile/scene/007 "Molecules" "friday" %000000000 1'
    lines["/ch/03/mix"] = "/ch/03/mix ON garbage ON +0 OFF -oo"  # unreadable token -> None, not a failure
    lines["/bus/02/eq/3"] = "/bus/02/eq/3"  # path only; all fields None but still a section
    drop = {"/fx/8/par", "/ch/32/grp", "/headamp/127"}
    conn = StubConn(lines, drop=drop)
    st = await dump_desk_state(conn, d, concurrency=4)
    assert conn.calls and conn.calls[0][1] == 4 and len(conn.calls[0][0]) == 2127
    assert set(st.missing) == drop
    assert len(st.sections) == 2127 - len(drop)
    assert st.scene == {"index": 7, "name": "Molecules"}
    assert st.console == {"name": "X32-FAKE", "model": "X32RACK", "firmware": "4.06"}
    assert st.get("/ch/03/mix/fader") is None and st.get("/ch/03/mix/pan") == 0
    assert st.get("/bus/02/eq/3/type") is None
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$", st.created)
    assert st.get("/ch/01/config/name") == synth(d.param("ch", "config/name"), 0)
    # re-imports cleanly
    again = DeskState.from_json(st.to_json())
    assert again.sections == st.sections and again.missing == st.missing


async def test_dump_sections_filter_and_missing_scene(d):
    lines = {n.path: render_node_line(n.path, n, synth_values(n, j)) for j, n in enumerate(d.nodes())}
    conn = StubConn(lines)
    st = await dump_desk_state(conn, d, sections=["ch.5", "/bus/03", "dca"])
    paths = conn.calls[0][0]
    assert all(p.startswith(("/ch/05/", "/bus/03/", "/dca/")) or p in ("/ch/05", "/bus/03") for p in paths)
    assert {p for p in paths if p.startswith("/ch/05")} == {n.path for n in d.node_paths_for(target=Target("ch", 5))}
    assert any(p.startswith("/dca/") for p in paths) and not any(p.startswith("/ch/06") for p in paths)
    assert st.scene is None and st.missing == [] and len(st.sections) == len(paths)
    conn2 = StubConn({})
    empty = await dump_desk_state(conn2, d, sections=["/-stat"])
    assert len(empty.missing) == 7 and empty.sections == {}
    with pytest.raises(ValueError):
        await dump_desk_state(conn2, d, sections=["not-a-scope"])


async def test_dump_without_status_attribute(d):
    class Bare:
        async def node_many(self, paths, concurrency):
            return {p: None for p in paths}

    st = await dump_desk_state(Bare(), d, sections=["dca"])
    assert st.console == {} and len(st.missing) == 16


# ---------------------------------------------------------------------------------------------- snapshots


def test_snapshot_store_save_list_load_latest(d, tmp_path, monkeypatch):
    store = SnapshotStore(tmp_path / "snaps")
    assert store.list() == [] and store.latest() is None
    with pytest.raises(SnapshotError):
        store.load("latest")
    stamps = iter(["20260919-120000", "20260919-120000", "20260919-120500", "20260920-090000"])
    monkeypatch.setattr(nodes, "time", SimpleNamespace(strftime=lambda fmt: next(stamps), monotonic=lambda: 0.0))
    s1 = store.save(full_state(d, 1), label="auto pre-write")
    s2 = store.save(full_state(d, 2), label="auto pre-write")  # same second, same label -> suffix
    s3 = store.save(full_state(d, 3), label="Before Molecules! (Fri)")
    st4 = full_state(d, 4)
    st4.scene = {"index": 3, "name": "GravelAxe"}
    s4 = store.save(st4)
    assert s1.id == "20260919-120000-auto-pre-write" and s2.id == "20260919-120000-auto-pre-write-2"
    assert s3.id == "20260919-120500-before-molecules-fri" and s4.id == "20260920-090000"
    for s in (s1, s2, s3, s4):
        assert re.fullmatch(r"\d{8}-\d{6}(-[a-z0-9-]+)?", s.id) and s.path.exists() and s.path.name == s.id + ".json"
    metas = store.list()
    assert [m.id for m in metas] == [s4.id, s3.id, s2.id, s1.id]
    assert metas[0].scene == {"index": 3, "name": "GravelAxe"} and metas[0].size > 1000 and metas[1].label == "Before Molecules! (Fri)"
    assert metas[0].created == st4.created
    assert store.latest().id == s4.id and store.load("latest").id == s4.id
    assert store.load("20260920").id == s4.id
    assert store.load(s3.id).state.sections == s3.state.sections
    assert store.load("20260919-120000-auto-pre-write").id == s1.id  # exact match wins over the -2 sibling
    assert store.load("20260919-120000-auto-pre-write-2").id == s2.id
    with pytest.raises(SnapshotError, match="ambiguous"):
        store.load("20260919-12")
    with pytest.raises(SnapshotError, match="no snapshot"):
        store.load("2025")
    with pytest.raises(SnapshotError):
        store.load("")
    loaded = store.load(s4.id)
    assert loaded.label == "" and loaded.created == st4.created and loaded.scene == st4.scene
    assert loaded.state.get("/ch/01/mix/fader") == st4.get("/ch/01/mix/fader")
    env = json.loads(s4.path.read_text(encoding="utf-8"))
    assert env["format"] == "x32mcp-snapshot/1" and env["id"] == s4.id and env["state"]["sections"]


def test_snapshot_store_skips_unreadable_files(d, tmp_path):
    store = SnapshotStore(tmp_path)
    (tmp_path / "junk.json").write_text("{", encoding="utf-8")
    (tmp_path / "other.json").write_text('{"x": 1}', encoding="utf-8")
    s = store.save(full_state(d), label="ok")
    assert [m.id for m in store.list()] == [s.id]
    assert store.load("latest").id == s.id


# ---------------------------------------------------------------------------------------------- diff / describe


def sections_for(d: Descriptor, paths: list[str], seed: int = 0) -> DeskState:
    st = DeskState(created="2026-09-19T12:00:00+02:00")
    for j, p in enumerate(paths):
        node = d.node(p)
        st.sections[p] = synth_values(node, seed + j)
    return st


def test_diff_and_describe_exact_english(d):
    a = sections_for(d, ["/ch/05/config", "/ch/05/mix", "/ch/02/config", "/ch/02/mix/03", "/bus/03/config"])
    set_leaf(a, d, "/ch/05/config/name", "Vox Tony")
    set_leaf(a, d, "/ch/05/mix/fader", -4.2)
    set_leaf(a, d, "/ch/02/config/name", "")
    set_leaf(a, d, "/bus/03/config/name", "")
    set_leaf(a, d, "/ch/02/mix/03/level", -20.0)
    b = copy.deepcopy(a)
    set_leaf(b, d, "/ch/05/mix/fader", -1.0)
    set_leaf(b, d, "/ch/02/mix/03/level", -17.0)
    changes = diff_states(a, b, d)
    assert [c.label for c in changes] == [  # sweep order: strip-major, Ch 2 before Ch 5
        "Bus 3 send from Ch 2 −20.0 dB → −17.0 dB (+3.0 dB)",
        "Ch 5 'Vox Tony' fader −4.2 dB → −1.0 dB",
    ]
    c = changes[1]
    assert c.address == "/ch/05/mix/fader" and c.field == "mix/fader" and c.before == -4.2 and c.after == -1.0
    assert c.section == "/ch/05/mix" and c.strip == "ch.5"
    assert changes[0].address == "/ch/02/mix/03/level" and changes[0].strip == "ch.2"
    assert describe_changes(changes) == (
        "- Bus 3 send from Ch 2 −20.0 dB → −17.0 dB (+3.0 dB)\n"
        "- Ch 5 'Vox Tony' fader −4.2 dB → −1.0 dB"
    )
    # grouping: an interleaved list is re-ordered so each strip's changes sit together
    shuffled = [changes[0], changes[1], changes[0]]
    assert describe_changes(shuffled).split("\n") == ["- " + changes[0].label, "- " + changes[0].label, "- " + changes[1].label]
    assert describe_changes([]) == "No changes."


def test_diff_mute_and_eq_exact_english(d):
    a = sections_for(d, ["/ch/05/config", "/ch/05/mix", "/ch/05/eq/2"])
    set_leaf(a, d, "/ch/05/config/name", "Vox")
    set_leaf(a, d, "/ch/05/mix/on", False)
    b = copy.deepcopy(a)
    set_leaf(b, d, "/ch/05/mix/on", True)
    (c,) = diff_states(a, b, d)
    assert c.label == "Ch 5 'Vox' muted → unmuted"
    a = sections_for(d, ["/ch/05/eq/2"])
    set_leaf(a, d, "/ch/05/eq/2/g", 0.0)
    b = copy.deepcopy(a)
    set_leaf(b, d, "/ch/05/eq/2/g", -3.0)
    (c,) = diff_states(a, b, d)
    assert c.label == "Ch 5 EQ band 2 gain +0.0 dB → −3.0 dB"


def test_diff_more_labels(d):
    paths = ["/ch/02/config", "/ch/02/mix", "/ch/02/mix/03", "/ch/02/eq/1", "/ch/02/preamp", "/ch/02/gate", "/ch/02/grp",
             "/bus/03/config", "/bus/03/mix/02", "/dca/1", "/dca/1/config", "/headamp/005", "/fx/1", "/config/mute", "/-prefs/rta",
             "/-show/prepos/current", "/main/st/mix", "/mtx/02/config"]
    a = sections_for(d, paths)
    edits = {
        "/ch/02/config/name": ("", "Gtr"), "/bus/03/config/name": ("", "Tony IEM"), "/dca/1/config/name": ("Vocals", "Vocals"),
        "/ch/02/mix/03/on": (True, False), "/ch/02/mix/03/pan": (0, -20), "/ch/02/mix/03/type": ("PRE", "POST"), "/ch/02/mix/03/level": (NEG, -6.0),
        "/ch/02/eq/1/f": (124.7, 2500.0), "/ch/02/eq/1/q": (2.0, 4.5), "/ch/02/eq/1/type": ("PEQ", "HShv"),
        "/ch/02/preamp/hpf": (79.0, 120.0), "/ch/02/preamp/hpon": (False, True), "/ch/02/preamp/trim": (0.0, -3.0),
        "/ch/02/gate/thr": (-80.0, -40.0), "/ch/02/gate/hold": (0.02, 100.0), "/ch/02/grp/dca": (1, 3), "/ch/02/grp/mute": (0, 32),
        "/ch/02/mix/pan": (0, 20), "/ch/02/mix/st": (True, False),
        "/bus/03/mix/02/level": (-10.0, -10.0), "/dca/1/on": (False, True), "/dca/1/fader": (-8.3, 0.0),
        "/headamp/005/gain": (0.0, 30.0), "/headamp/005/phantom": (False, True), "/fx/1/type": ("HALL", "GEQ2"),
        "/config/mute/3": (False, True), "/-prefs/rta/source": ("MAIN", "BUS03"), "/-show/prepos/current": (3, 5),
        "/main/st/mix/fader": (0.0, -6.0), "/mtx/02/config/name": ("", "Fills"),
    }
    for addr, (before, _after) in edits.items():
        set_leaf(a, d, addr, before)
    b = copy.deepcopy(a)
    for addr, (_before, after) in edits.items():
        set_leaf(b, d, addr, after)
    labels = {c.address: c.label for c in diff_states(a, b, d)}
    m, arrow = "−", "→"
    assert labels["/ch/02/config/name"] == f"Ch 2 'Gtr' name '' {arrow} 'Gtr'"
    assert labels["/ch/02/mix/03/on"] == f"Bus 3 'Tony IEM' send from Ch 2 'Gtr' unmuted {arrow} muted"
    assert labels["/ch/02/mix/03/pan"] == f"Bus 3 'Tony IEM' send from Ch 2 'Gtr' pan +0 {arrow} {m}20"
    assert labels["/ch/02/mix/03/type"] == f"Bus 3 'Tony IEM' send from Ch 2 'Gtr' tap PRE {arrow} POST"
    assert labels["/ch/02/mix/03/level"] == f"Bus 3 'Tony IEM' send from Ch 2 'Gtr' {m}∞ dB {arrow} {m}6.0 dB"  # no delta from -inf
    assert labels["/ch/02/eq/1/f"] == f"Ch 2 'Gtr' EQ band 1 freq 124.7 Hz {arrow} 2.5 kHz"
    assert labels["/ch/02/eq/1/q"] == f"Ch 2 'Gtr' EQ band 1 Q 2.0 {arrow} 4.5"
    assert labels["/ch/02/eq/1/type"] == f"Ch 2 'Gtr' EQ band 1 type PEQ {arrow} HShv"
    assert labels["/ch/02/preamp/hpf"] == f"Ch 2 'Gtr' HPF 79 Hz {arrow} 120 Hz"
    assert labels["/ch/02/preamp/hpon"] == f"Ch 2 'Gtr' HPF off {arrow} on"
    assert labels["/ch/02/preamp/trim"] == f"Ch 2 'Gtr' trim +0.0 dB {arrow} {m}3.0 dB"
    assert labels["/ch/02/gate/thr"] == f"Ch 2 'Gtr' gate threshold {m}80.0 dB {arrow} {m}40.0 dB"
    assert labels["/ch/02/gate/hold"] == f"Ch 2 'Gtr' gate hold 0.02 ms {arrow} 100 ms"
    assert labels["/ch/02/grp/dca"] == f"Ch 2 'Gtr' DCA groups {{1}} {arrow} {{1, 2}}"
    assert labels["/ch/02/grp/mute"] == f"Ch 2 'Gtr' mute groups none {arrow} {{6}}"
    assert labels["/ch/02/mix/pan"] == f"Ch 2 'Gtr' pan +0 {arrow} +20"
    assert labels["/ch/02/mix/st"] == f"Ch 2 'Gtr' LR assign on {arrow} off"
    assert "/bus/03/mix/02/level" not in labels
    assert labels["/dca/1/on"] == f"DCA 1 'Vocals' muted {arrow} unmuted"
    assert labels["/dca/1/fader"] == f"DCA 1 'Vocals' fader {m}8.3 dB {arrow} 0.0 dB"
    assert labels["/headamp/005/gain"] == f"Headamp 005 gain +0.0 dB {arrow} +30.0 dB"
    assert labels["/headamp/005/phantom"] == f"Headamp 005 phantom off {arrow} on"
    assert labels["/fx/1/type"] == f"FX 1 type HALL {arrow} GEQ2"
    assert labels["/config/mute/3"] == f"Config mute group 3 off {arrow} on"
    assert labels["/-prefs/rta/source"] == f"Prefs RTA source MAIN {arrow} BUS03"
    assert labels["/-show/prepos/current"] == f"Show current scene 3 {arrow} 5"
    assert labels["/main/st/mix/fader"] == f"Main LR fader 0.0 dB {arrow} {m}6.0 dB"
    assert labels["/mtx/02/config/name"] == f"Matrix 2 'Fills' name '' {arrow} 'Fills'"
    # ordering follows the descriptor sweep (ch before bus before dca …) and describe keeps strips together
    addrs = [c.address for c in diff_states(a, b, d)]
    assert addrs.index("/ch/02/config/name") < addrs.index("/dca/1/on") < addrs.index("/headamp/005/gain") < addrs.index("/config/mute/3")
    text = describe_changes(diff_states(a, b, d))
    assert text.startswith("- Ch 2 'Gtr' name") and text.count("\n") == len(labels) - 1


def test_diff_scope_none_and_tolerance(d):
    a = sections_for(d, ["/ch/01/mix", "/ch/02/mix", "/bus/01/mix", "/ch/01/mix/01", "/main/st/mix"])
    set_leaf(a, d, "/ch/01/mix/fader", -6.0)
    set_leaf(a, d, "/ch/02/mix/fader", -6.0)
    set_leaf(a, d, "/bus/01/mix/fader", -6.0)
    set_leaf(a, d, "/ch/01/mix/01/panFollow", None)
    set_leaf(a, d, "/main/st/mix/fader", NEG)
    b = copy.deepcopy(a)
    set_leaf(b, d, "/ch/01/mix/fader", -6.0000001)  # float noise is not a change
    set_leaf(b, d, "/ch/02/mix/fader", -5.0)
    set_leaf(b, d, "/bus/01/mix/fader", -5.0)
    set_leaf(b, d, "/ch/01/mix/01/panFollow", True)  # None on one side is not a change
    set_leaf(b, d, "/main/st/mix/fader", "-oo")  # string form equals -inf
    b.sections.pop("/ch/01/mix/01")  # missing section skipped
    assert [c.address for c in diff_states(a, b, d)] == ["/ch/02/mix/fader", "/bus/01/mix/fader"]
    assert [c.address for c in diff_states(a, b, d, scope="ch.2")] == ["/ch/02/mix/fader"]
    assert [c.address for c in diff_states(a, b, d, scope=Target("bus", 1))] == ["/bus/01/mix/fader"]
    assert [c.address for c in diff_states(a, b, d, scope="bus")] == ["/bus/01/mix/fader"]
    assert [c.address for c in diff_states(a, b, d, scope="/ch")] == ["/ch/02/mix/fader"]
    assert diff_states(a, b, d, scope="ch.1") == []
    with pytest.raises(ValueError):
        diff_states(a, b, d, scope="bogus")
    assert isinstance(diff_states(a, b, d)[0], Change)


# ---------------------------------------------------------------------------------------------- restore


def test_restore_plan_lines_order_and_exclusions(d):
    target = full_state(d, 7)
    set_leaf(target, d, "/ch/01/mix/01/panFollow", None)  # FW 2.x snapshot: 4 tokens on this send
    live = copy.deepcopy(target)
    for addr, v in {
        "/ch/01/gate/thr": -12.5, "/ch/01/config/name": "changed", "/ch/01/mix/fader": -60.0, "/ch/01/eq/2/g": 1.25,
        "/ch/01/mix/03/level": -30.0, "/ch/01/mix/01/level": -30.0, "/bus/02/eq/3/f": 632.5, "/-prefs/rta/decay": 16.0,
        "/-show/prepos/current": 42, "/-stat/rtasource": 3, "/dca/2/fader": -20.0, "/fx/3/type": "AMBI", "/config/mute/2": True,
        "/ch/01/dyn/thr": -12.5, "/ch/01/insert/on": True, "/ch/01/grp/dca": 129, "/ch/01/preamp/trim": -1.5, "/ch/01/delay/time": 20.0,
    }.items():
        set_leaf(live, d, addr, v)
    del live.sections["/headamp/005"]
    lines = restore_plan(target, live, d)
    paths = [split_node_line(l)[0] for l in lines]
    assert paths == [
        "/ch/01/config", "/ch/01/delay", "/ch/01/preamp", "/ch/01/mix", "/ch/01/mix/01", "/ch/01/mix/03", "/ch/01/eq/2",
        "/ch/01/dyn", "/ch/01/gate", "/ch/01/insert", "/ch/01/grp", "/bus/02/eq/3", "/dca/2", "/headamp/005", "/fx/3", "/config/mute",
    ]
    for line in lines:
        path, toks = split_node_line(line)
        node = d.node(path)
        assert "  " not in line and line == render_node_line(path, node, target.sections[path], pad=False)
        back = parse_node_line(line, node, strict=True)
        for k in node.fields:
            assert same(back[k], target.sections[path][k]), (path, k)
    assert lines[paths.index("/ch/01/mix/01")].count(" ") == 4  # partial: no panFollow token
    assert not any(p.startswith(("/-prefs", "/-show", "/-stat")) for p in paths)
    assert restore_plan(target, live, d, scope="ch.1") == lines[:11]
    assert [split_node_line(l)[0] for l in restore_plan(target, live, d, scope="bus")] == ["/bus/02/eq/3"]
    assert restore_plan(target, target, d) == []


def test_restore_plan_hole_writes_leading_run(d, caplog):
    target = sections_for(d, ["/ch/01/mix/01"])
    target.sections["/ch/01/mix/01"] = {"mix/01/on": True, "mix/01/level": None, "mix/01/pan": 0, "mix/01/type": "PRE", "mix/01/panFollow": None}
    live = DeskState(created="x")
    with caplog.at_level("WARNING", logger="x32mcp.nodes"):
        assert restore_plan(target, live, d) == ["/ch/01/mix/01 ON"]
    assert any("hole" in r.message for r in caplog.records)
    target.sections["/ch/01/mix/01"] = {k: None for k in d.node("/ch/01/mix/01").fields}
    assert restore_plan(target, live, d) == []


def test_restore_plan_from_json_snapshot_round_trip(d, tmp_path):
    """Snapshot → JSON → restore lines reproduce the desk text (bit-faithful values, -oo kept)."""
    target = full_state(d, 3)
    set_leaf(target, d, "/ch/07/mix/fader", NEG)
    store = SnapshotStore(tmp_path)
    snap = store.load(store.save(target, "pre").id)
    live = copy.deepcopy(target)
    set_leaf(live, d, "/ch/07/mix/fader", 0.0)
    (line,) = restore_plan(snap.state, live, d)
    assert line.startswith("/ch/07/mix ") and " -oo " in line + " "
