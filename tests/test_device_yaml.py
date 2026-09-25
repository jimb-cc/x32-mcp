"""Pure-YAML sanity tests for device.yaml (DESIGN.md §7).

No x32mcp runtime modules are imported (only the already-written ``targets`` table); everything here
checks the descriptor *data*: schema shape, enum/scale references, node field references and order,
the RTA/GEQ band tables, and the size of a full ``/node`` sweep (printed with ``-s``).
"""

from __future__ import annotations

import itertools
import re
import shlex
from pathlib import Path

import pytest
import yaml

from x32mcp.targets import families as target_families

ROOT = Path(__file__).resolve().parent.parent
DEVICE_YAML = ROOT / "device.yaml"

TOP_LEVEL_KEYS = ["meta", "scales", "enums", "strips", "params", "nodes", "guarded", "policy", "rta", "geq", "detector", "ringout", "mics"]
OPTIONAL_TOP_LEVEL_KEYS = ["cfs_policy"]   # the CFS² policy layer's block (docs/CFS_POLICY.md); absent = all defaults
SCALE_KINDS = {"level", "lin", "log", "enum", "int", "bool", "str", "pan"}
# DESIGN.md §9 vocabulary + the research-driven additions documented at the top of device.yaml.
NODE_FORMATS_DESIGN = {"str", "int", "sint", "onoff", "enum", "db1", "db2", "float1", "float2", "freq", "pct", "float", "hex"}
NODE_FORMATS_ADDED = {"sfloat1", "sig3", "token", "bits6", "bits8", "bits9", "bits18"}
NODE_FORMATS = NODE_FORMATS_DESIGN | NODE_FORMATS_ADDED
SPEC_KEYS = {"osc", "scale", "enum", "node", "tier", "clamp", "inverted_mute"}
NON_STRIP_FAMILIES = {"headamp", "fx", "outputs", "config", "show", "stat", "prefs", "action"}

_TEMPLATE_VAR = re.compile(r"\{(\w+)(?::[^}]*)?\}")


@pytest.fixture(scope="module")
def dev() -> dict:
    with DEVICE_YAML.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------------------------- helpers

def template_vars(s: str) -> set[str]:
    return set(_TEMPLATE_VAR.findall(s))


def for_ranges(sec: dict) -> dict[str, list[int]]:
    return {k: list(range(lo, hi + 1)) for k, (lo, hi) in (sec.get("for") or {}).items()}


def strip_roots(fam: str, strips: dict) -> list[tuple[str, dict]]:
    """Concrete root paths of a strip family with the template vars that produced them."""
    st = strips[fam]
    if "ids" in st:
        return [(st["path"].format(id=i), {"id": i}) for i in st["ids"]]
    return [(st["path"].format(n=n), {"n": n}) for n in range(1, st["count"] + 1)]


def expand_section(sec: dict, strips: dict) -> list[tuple[str, list[str]]]:
    """Expand one `nodes` entry into (concrete node path, concrete field relpaths) tuples.

    Mirrors what Descriptor.all_node_paths() must do: strip sections join the strip root and the
    section template; `path` sections are absolute templates; `for` ranges are inclusive; `fields_even`
    replaces `fields` when the (single) `for` var is even.
    """
    ranges = for_ranges(sec)
    var_names = list(ranges)
    combos = [dict(zip(var_names, vals)) for vals in itertools.product(*ranges.values())] if ranges else [{}]
    out: list[tuple[str, list[str]]] = []
    if "section" in sec:
        roots = strip_roots(sec["family"], strips)
    else:
        roots = [("", {})]
    for root, root_vars in roots:
        for combo in combos:
            v = {**root_vars, **combo}
            even = bool(combo) and len(combo) == 1 and next(iter(combo.values())) % 2 == 0
            fields = sec.get("fields_even") if (even and sec.get("fields_even")) else sec["fields"]
            if "section" in sec:
                section = sec["section"].format(**v)
                path = f"{root}/{section}" if section else root
            else:
                path = sec["path"].format(**v)
            out.append((path, [f.format(**v) for f in fields]))
    return out


def all_node_paths(dev: dict) -> list[tuple[str, list[str]]]:
    out = []
    for sec in dev["nodes"]:
        out.extend(expand_section(sec, dev["strips"]))
    return out


def tokenize(line: str) -> list[str]:
    """Split a desk node line on whitespace runs, honouring double quotes ("Reverb L", "")."""
    return shlex.split(line, posix=True)


# ---------------------------------------------------------------------------------------------- schema

def test_loads_with_fixed_top_level_keys(dev):
    assert list(dev) == TOP_LEVEL_KEYS + OPTIONAL_TOP_LEVEL_KEYS
    assert dev["meta"]["model"] == "X32" and dev["meta"]["osc_port"] == 10023
    assert set(dev["meta"]["roots"]) == NON_STRIP_FAMILIES
    for fam, root in dev["meta"]["roots"].items():
        assert root.startswith("/"), (fam, root)


def test_no_unresolved_merge_keys(dev):
    """YAML anchors/merges must be fully resolved by safe_load (no literal '<<' keys anywhere)."""
    def walk(o):
        if isinstance(o, dict):
            assert "<<" not in o
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(dev)


def test_scales_are_well_formed(dev):
    scales = dev["scales"]
    for name, sc in scales.items():
        assert sc["kind"] in SCALE_KINDS, name
        if sc["kind"] in {"lin", "log", "pan"}:
            assert "lo" in sc and "hi" in sc, name
        if "steps" in sc:
            assert isinstance(sc["steps"], int) and sc["steps"] >= 2, name
    # scales_params.md §2.3: faders/main/dca on the i/1023 grid, sends/mlevel on i/160
    assert scales["fader"] == {"kind": "level", "steps": 1024, "unit": "dB"}
    assert scales["send"] == {"kind": "level", "steps": 161, "unit": "dB"}
    assert scales["q"]["lo"] == 10 and scales["q"]["hi"] == 0.3 and scales["q"]["steps"] == 72  # decreasing log
    assert scales["freq"] == {"kind": "log", "lo": 20, "hi": 20000, "steps": 201, "unit": "Hz"}
    assert scales["headamp_gain"]["steps"] == 145 and scales["trim"]["steps"] == 145
    assert scales["eq_gain"]["steps"] == 121 and scales["geq_gain"]["steps"] == 61
    assert scales["pan"] == {"kind": "pan", "lo": -100, "hi": 100, "steps": 101}


def test_enums_are_unique_string_lists(dev):
    for name, toks in dev["enums"].items():
        assert isinstance(toks, list) and toks, name
        assert all(isinstance(t, str) for t in toks), f"{name}: YAML turned a token into a non-string (OFF/ON/Y?)"
        assert len(set(toks)) == len(toks), f"{name}: duplicate tokens"


@pytest.mark.parametrize(
    "name,length,index_checks",
    [
        ("color", 16, {0: "OFF", 1: "RD", 7: "WH", 8: "OFFi", 15: "WHi"}),
        ("eq_type", 6, {0: "LCut", 2: "PEQ", 5: "HCut"}),
        ("eq_type_mtx", 14, {6: "BU6", 13: "LR24"}),
        ("gate_mode", 5, {2: "EXP4", 3: "GATE"}),
        ("dyn_ratio", 12, {0: "1.1", 11: "100"}),
        ("filter_type", 9, {0: "LC6", 8: "10.0"}),
        ("insert_sel", 23, {0: "OFF", 1: "FX1L", 9: "FX5L", 16: "FX8R", 17: "AUX1", 22: "AUX6"}),
        ("fx_type_14", 61, {0: "HALL", 27: "GEQ2", 28: "GEQ", 29: "TEQ2", 30: "TEQ", 39: "CMB", 40: "CMB2", 60: "PIT"}),
        ("fx_type_58", 34, {0: "GEQ2", 1: "GEQ", 2: "TEQ2", 3: "TEQ", 30: "PHAS", 33: "SUB"}),
        ("fx_source", 18, {0: "INS", 1: "MIX1", 16: "MIX16", 17: "M/C"}),
        ("ch_source", 65, {0: "OFF", 1: "IN01", 32: "IN32", 33: "AUX1", 38: "AUX6", 39: "USBL", 40: "USBR", 41: "FX1L", 48: "FX4R", 49: "BUS01", 64: "BUS16"}),
        ("rta_source", 74, {0: "NONE", 1: "MONITOR", 2: "CH01", 33: "CH32", 34: "AUX1", 41: "AUX8", 42: "FX1L", 49: "FX4R", 50: "BUS01", 65: "BUS16", 66: "MTX1", 71: "MTX6", 72: "MAIN", 73: "MONO"}),
        ("send_type", 6, {0: "IN/LC", 5: "GRP"}),
        ("send_type_mtx", 5, {4: "POST"}),
        ("routing_in", 24, {0: "AN1-8", 4: "A1-8", 10: "B1-8", 16: "CARD1-8", 20: "UIN1-8", 23: "UIN25-32"}),
        ("routing_in_aux", 16, {0: "AUX1-4", 15: "UIN1-6"}),
        ("routing_aes", 36, {20: "OUT1-8", 21: "OUT9-16", 24: "AUX1-6/Mon", 35: "UIN25-32"}),
        ("routing_out_a", 36, {20: "OUT1-4", 21: "OUT9-12", 24: "AUX/CR", 25: "AUX/TB"}),
        ("routing_out_b", 36, {20: "OUT5-8", 21: "OUT13-16"}),
        # /outputs/*/NN/src 0..76 (fx_routing_scenes.md §4.7, DOC p.40-42: 1+3+16+6+32+8+8+3 = 77 entries)
        ("output_src", 77, {0: "OFF", 1: "Main L", 2: "Main R", 3: "M/C", 4: "MixBus 01", 19: "MixBus 16", 20: "Matrix 1", 25: "Matrix 6",
                            26: "DirectOut Ch 01", 57: "DirectOut Ch 32", 58: "DirectOut Aux 1", 65: "DirectOut Aux 8", 66: "DirectOut FX 1L",
                            73: "DirectOut FX 4R", 74: "Monitor L", 75: "Monitor R", 76: "Talkback"}),
        ("output_pos", 9, {0: "IN/LC", 1: "IN/LC+M", 2: "<-EQ", 3: "<-EQ+M", 4: "EQ->", 5: "EQ->+M", 6: "PRE", 7: "PRE+M", 8: "POST"}),
        ("solo_source", 7, {6: "AUX78"}),
        ("rta_visibility", 13, {0: "OFF", 1: "25%", 12: "80%"}),
        ("rta_peakhold", 9, {0: "OFF", 8: "8"}),
        ("hpslope", 3, {0: "12", 2: "24"}),
        ("onoff", 2, {0: "OFF", 1: "ON"}),
    ],
)
def test_enum_tables(dev, name, length, index_checks):
    toks = dev["enums"][name]
    assert len(toks) == length, name
    for i, tok in index_checks.items():
        assert toks[i] == tok, f"{name}[{i}] = {toks[i]!r}, expected {tok!r}"


def test_fx_type_58_tokens_all_exist_in_fx_type_14(dev):
    # /node text for /fx/5-8 prints the same token names; only the OSC index differs (fx_routing_scenes.md §1.3)
    assert set(dev["enums"]["fx_type_58"]) <= set(dev["enums"]["fx_type_14"])


def test_strips_match_targets_table(dev):
    strips = dev["strips"]
    fams = target_families()
    assert set(strips) == set(fams)
    for fam, f in fams.items():
        assert strips[fam]["path"] == f["path"], fam
        if "ids" in f:
            assert tuple(strips[fam]["ids"]) == tuple(f["ids"])
        else:
            assert strips[fam]["count"] == f["count"], fam
    assert strips["ch"]["eq_bands"] == 4 and strips["bus"]["eq_bands"] == 6 and strips["main"]["eq_bands"] == 6
    assert strips["ch"]["sends"] == 16 and strips["bus"]["sends"] == 6 and strips["bus"]["send_target"] == "mtx"
    assert "sends" not in strips["mtx"] and "sends" not in strips["dca"]


# ---------------------------------------------------------------------------------------------- params

def test_every_param_spec_is_valid(dev):
    scales, enums = dev["scales"], dev["enums"]
    for fam, params in dev["params"].items():
        assert fam in dev["strips"] or fam in NON_STRIP_FAMILIES, fam
        for rel, spec in params.items():
            where = f"{fam}:{rel}"
            assert isinstance(rel, str) and not rel.startswith("/"), where
            assert set(spec) <= SPEC_KEYS, f"{where}: unknown keys {set(spec) - SPEC_KEYS}"
            assert spec["osc"] in ("f", "i", "s"), where
            assert ("scale" in spec) != ("enum" in spec), f"{where}: exactly one of scale/enum"
            if "scale" in spec:
                assert spec["scale"] in scales, f"{where}: unknown scale {spec['scale']!r}"
            else:
                assert spec["enum"] in enums, f"{where}: unknown enum {spec['enum']!r}"
                assert spec["osc"] == "i", where
            assert spec["node"] in NODE_FORMATS, f"{where}: unknown node format {spec['node']!r}"
            assert spec["tier"] in (0, 1, 2), where
            if "clamp" in spec:
                assert set(spec["clamp"]) <= {"min", "max"} and spec["clamp"], where
            if spec.get("inverted_mute"):
                assert rel.endswith("/on") or rel == "on", where
                assert spec["scale"] == "bool", where
            # string params are node str; bools are onoff (or int for the UNCONFIRMED panFollow token)
            if spec["osc"] == "s":
                assert spec["scale"] == "str" and spec["node"] == "str", where
            if spec.get("scale") == "bool":
                assert spec["node"] in ("onoff", "int"), where
            for var in template_vars(rel):
                assert var in {"n", "id", "band", "send", "idx"}, where


def test_shared_blocks_landed_in_the_right_families(dev):
    p = dev["params"]
    # auxin: preamp trim/invert only, no gate/dyn/insert/delay; fxrtn: no preamp, no config/source (scales_params.md §5)
    assert {"preamp/trim", "preamp/invert"} <= set(p["auxin"]) and "preamp/hpf" not in p["auxin"]
    assert not any(k.startswith(("gate/", "dyn/", "insert/", "delay/")) for k in p["auxin"])
    assert "config/source" in p["auxin"] and "config/source" not in p["fxrtn"]
    assert not any(k.startswith("preamp/") for k in p["fxrtn"])
    # bus: dyn incl keysrc, 6 bands (template), sends to matrix without GRP, 0 dB fader ceiling
    assert "dyn/keysrc" in p["bus"] and p["bus"]["mix/{send:02d}/type"]["enum"] == "send_type_mtx"
    assert p["bus"]["mix/fader"]["clamp"] == {"max": 0} and p["ch"]["mix/fader"]["clamp"] == {"max": 5}
    # mtx/main: no dyn keysrc, 14-entry EQ type enum; mtx mix is on/fader only
    for fam in ("mtx", "main"):
        assert "dyn/keysrc" not in p[fam] and p[fam]["eq/{band}/type"]["enum"] == "eq_type_mtx"
    assert not any(k in p["mtx"] for k in ("mix/st", "mix/pan", "mix/mono", "mix/mlevel", "mix/{send:02d}/level"))
    assert "mix/pan" in p["main"] and "mix/st" not in p["main"]
    # main mix is guarded, mutes inverted everywhere
    assert all(p["main"][k]["tier"] == 2 for k in p["main"] if k.startswith("mix/"))
    for fam in ("ch", "auxin", "fxrtn", "bus", "mtx", "main"):
        assert p[fam]["mix/on"]["inverted_mute"] is True
    assert p["dca"]["on"]["inverted_mute"] is True and p["dca"]["fader"]["scale"] == "fader"
    # sends use the 161-step grid, faders the 1024 grid
    assert p["ch"]["mix/{send:02d}/level"]["scale"] == "send" and p["ch"]["mix/mlevel"]["scale"] == "send"
    assert p["ch"]["mix/fader"]["scale"] == "fader"
    # config/source is an int on the wire and in node text, enum in the API
    assert p["ch"]["config/source"] == {"osc": "i", "enum": "ch_source", "node": "int", "tier": 2}
    # fx: 64 par slots + type + source l/r
    assert all(f"par/{i:02d}" in p["fx"] for i in range(1, 65)) and len(p["fx"]) == 67
    assert p["fx"]["type"]["enum"] == "fx_type_14"
    # -action triggers are guarded ints 0..99
    assert all(p["action"][k]["tier"] == 2 and p["action"][k]["clamp"] == {"min": 0, "max": 99} for k in ("goscene", "gocue", "gosnippet"))
    assert p["headamp"]["phantom"]["tier"] == 2 and p["headamp"]["gain"]["scale"] == "headamp_gain"
    # stereo links: every pair address exists as a guarded bool (set_channel_config(link=...) writes /config/chlink/N-M)
    pairs = [f"{i}-{i + 1}" for i in range(1, 32, 2)]
    assert all(p["config"][f"chlink/{pr}"] == {"osc": "i", "scale": "bool", "node": "onoff", "tier": 2} for pr in pairs)
    assert {k.split("/")[0] for k in p["config"] if k.endswith("link/1-2")} == {"chlink", "auxlink", "fxlink", "buslink", "mtxlink"}
    # mute groups are NOT inverted (1 = group engaged = members muted)
    assert "inverted_mute" not in p["config"]["mute/6"] and p["config"]["mute/6"]["tier"] == 2


def test_tier2_params_are_consistent_with_guarded_globs(dev):
    """Every param whose concrete address matches a guarded glob is declared tier 2 too (documentation consistency)."""
    import fnmatch

    strips = dev["strips"]
    for fam, params in dev["params"].items():
        if fam in strips:
            root = strip_roots(fam, strips)[0][0]
        else:
            root = dev["meta"]["roots"][fam].format(n=1)
        for rel, spec in params.items():
            addr = root + "/" + rel.format(n=1, band=1, send=1, idx=1)  # outputs: {n}/{idx} live in the relpath, not the root
            if any(fnmatch.fnmatch(addr, g) for g in dev["guarded"]):
                assert spec["tier"] == 2, f"{fam}:{rel} matches a guarded glob but is tier {spec['tier']}"


# ---------------------------------------------------------------------------------------------- nodes

def test_every_node_field_references_a_declared_param(dev):
    params = dev["params"]
    for sec in dev["nodes"]:
        assert ("section" in sec) != ("path" in sec), sec
        fam = sec["family"]
        assert fam in params, sec
        if "section" in sec:
            assert fam in dev["strips"], f"section-relative node for non-strip family: {sec}"
        else:
            assert sec["path"].startswith("/"), sec
        ranges = for_ranges(sec)
        allowed = set(ranges) | ({"n", "id"} if fam in dev["strips"] else set())
        tmpl = sec.get("section", sec.get("path", ""))
        assert template_vars(tmpl) <= allowed, sec
        for key in ("fields", "fields_even"):
            for field in sec.get(key) or []:
                assert field in params[fam], f"{fam} node {tmpl!r}: field {field!r} is not a declared param"
                assert template_vars(field) <= allowed, (sec, field)
        assert sec["fields"], sec
        if "fields_even" in sec:
            assert len(ranges) == 1, "fields_even needs exactly one `for` var"
            assert sec["fields_even"] == sec["fields"][: len(sec["fields_even"])]


def test_node_field_order_matches_the_desk(dev):
    """Field order per the verbatim console lines in scales_params.md §13 / transport.md §6.3."""
    by_key = {(s["family"], s.get("section", s.get("path"))): s for s in dev["nodes"]}
    assert by_key[("ch", "config")]["fields"] == ["config/name", "config/icon", "config/color", "config/source"]
    assert by_key[("ch", "preamp")]["fields"] == ["preamp/trim", "preamp/invert", "preamp/hpon", "preamp/hpslope", "preamp/hpf"]
    assert by_key[("ch", "gate")]["fields"] == ["gate/on", "gate/mode", "gate/thr", "gate/range", "gate/attack", "gate/hold", "gate/release", "gate/keysrc"]
    assert by_key[("ch", "dyn")]["fields"] == ["dyn/on", "dyn/mode", "dyn/det", "dyn/env", "dyn/thr", "dyn/ratio", "dyn/knee", "dyn/mgain", "dyn/attack", "dyn/hold", "dyn/release", "dyn/pos", "dyn/keysrc", "dyn/mix", "dyn/auto"]
    assert by_key[("mtx", "dyn")]["fields"] == [f for f in by_key[("ch", "dyn")]["fields"] if f != "dyn/keysrc"]
    assert by_key[("main", "dyn")]["fields"] == by_key[("mtx", "dyn")]["fields"]
    assert by_key[("ch", "insert")]["fields"] == ["insert/on", "insert/pos", "insert/sel"]
    assert by_key[("ch", "eq/{band}")]["fields"] == ["eq/{band}/type", "eq/{band}/f", "eq/{band}/g", "eq/{band}/q"]
    assert by_key[("ch", "mix")]["fields"] == ["mix/on", "mix/fader", "mix/st", "mix/pan", "mix/mono", "mix/mlevel"]
    sends = by_key[("ch", "mix/{send:02d}")]
    assert sends["fields"] == ["mix/{send:02d}/on", "mix/{send:02d}/level", "mix/{send:02d}/pan", "mix/{send:02d}/type", "mix/{send:02d}/panFollow"]
    assert sends["fields_even"] == ["mix/{send:02d}/on", "mix/{send:02d}/level"]
    assert sends["for"] == {"send": [1, 16]} and by_key[("bus", "mix/{send:02d}")]["for"] == {"send": [1, 6]}
    assert by_key[("ch", "grp")]["fields"] == ["grp/dca", "grp/mute"]
    assert by_key[("auxin", "preamp")]["fields"] == ["preamp/trim", "preamp/invert"]
    assert by_key[("mtx", "preamp")]["fields"] == ["preamp/invert"]
    assert by_key[("mtx", "mix")]["fields"] == ["mix/on", "mix/fader"]
    assert by_key[("main", "/main/st/mix")]["fields"] == ["mix/on", "mix/fader", "mix/pan"]
    assert by_key[("main", "/main/m/mix")]["fields"] == ["mix/on", "mix/fader"]
    assert by_key[("dca", "/dca/{n}")]["fields"] == ["on", "fader"]
    assert by_key[("headamp", "/headamp/{n:03d}")]["fields"] == ["gain", "phantom"]
    assert by_key[("headamp", "/headamp/{n:03d}")]["for"] == {"n": [0, 127]}
    assert by_key[("fx", "/fx/{n}/source")]["for"] == {"n": [1, 4]} and by_key[("fx", "/fx/{n}")]["for"] == {"n": [1, 8]}
    assert len(by_key[("fx", "/fx/{n}/par")]["fields"]) == 64
    assert by_key[("config", "/config/routing/OUT")]["fields"] == ["routing/OUT/1-4", "routing/OUT/5-8", "routing/OUT/9-12", "routing/OUT/13-16"]
    assert by_key[("config", "/config/routing/IN")]["fields"][-1] == "routing/IN/AUX"
    # output taps: "/outputs/main/01 4 POST OFF" = src pos invert (X32.c case OMAIN); 16 XLR taps, 6 aux taps
    assert by_key[("outputs", "/outputs/main/{n:02d}")]["fields"] == ["main/{n:02d}/src", "main/{n:02d}/pos", "main/{n:02d}/invert"]
    assert by_key[("outputs", "/outputs/main/{n:02d}")]["for"] == {"n": [1, 16]}
    assert by_key[("outputs", "/outputs/aux/{idx:02d}")]["fields"] == ["aux/{idx:02d}/src", "aux/{idx:02d}/pos", "aux/{idx:02d}/invert"]
    assert by_key[("outputs", "/outputs/aux/{idx:02d}")]["for"] == {"idx": [1, 6]}
    outs = dev["params"]["outputs"]
    assert outs["main/{n:02d}/src"] == {"osc": "i", "enum": "output_src", "node": "int", "tier": 2}  # the desk prints the int
    assert outs["main/{n:02d}/pos"] == {"osc": "i", "enum": "output_pos", "node": "enum", "tier": 2}
    assert outs["main/{n:02d}/invert"] == {"osc": "i", "scale": "bool", "node": "onoff", "tier": 1}
    assert all(outs[f"aux/{{idx:02d}}/{leaf}"] == outs[f"main/{{n:02d}}/{leaf}"] for leaf in ("src", "pos", "invert"))
    assert "/outputs/*/src" in dev["guarded"] and "/outputs/*/pos" in dev["guarded"]
    assert len(by_key[("config", "/config/solo")]["fields"]) == 17
    # the solo section is read-only except the three PFL/AFL mode switches (set_solo_mode, Tier 1)
    solo_tiers = {f: dev["params"]["config"][f]["tier"] for f in by_key[("config", "/config/solo")]["fields"]}
    assert {f for f, t in solo_tiers.items() if t == 1} == {"solo/chmode", "solo/busmode", "solo/dcamode"} and set(solo_tiers.values()) == {0, 1}
    assert by_key[("prefs", "/-prefs/rta")]["fields"] == ["rta/visibility", "rta/gain", "rta/autogain", "rta/source", "rta/pos", "rta/mode", "rta/options", "rta/det", "rta/decay", "rta/peakhold"]
    # -show/prepos is swept as its leaf (the only form confirmed anywhere, transport.md §6.4 item 12)
    assert by_key[("show", "/-show/prepos/current")]["fields"] == ["prepos/current"] and ("show", "/-show/prepos") not in by_key
    assert by_key[("show", "/-show/showfile/scene/{idx:03d}")]["for"] == {"idx": [0, 99]}
    # -stat/rta is not a node: the five RTA leaves are single-parameter nodes whose path is the leaf address
    for leaf in ("rtamodeeq", "rtamodegeq", "rtaeqpre", "rtageqpost", "rtasource"):
        assert by_key[("stat", f"/-stat/{leaf}")]["fields"] == [leaf]
    assert ("stat", "/-stat") not in by_key and ("stat", "/-stat/rta") not in by_key
    # stereo links: 16/4/4/8/3 pairs + linkcfg, all tier 2
    assert len(by_key[("config", "/config/chlink")]["fields"]) == 16 and by_key[("config", "/config/chlink")]["fields"][-1] == "chlink/31-32"
    assert len(by_key[("config", "/config/buslink")]["fields"]) == 8 and len(by_key[("config", "/config/mtxlink")]["fields"]) == 3
    assert by_key[("config", "/config/linkcfg")]["fields"] == ["linkcfg/hadly", "linkcfg/eq", "linkcfg/dyn", "linkcfg/fdrmute"]
    assert all(dev["params"]["config"][f]["tier"] == 2 for f in by_key[("config", "/config/chlink")]["fields"])
    # required coverage per task: every strip family has config/mix/eq/grp; ch has gate/dyn/preamp/insert; bus has dyn/insert
    for fam in ("ch", "auxin", "fxrtn", "bus", "mtx", "main"):
        assert (fam, "config") in by_key and (fam, "eq") in by_key and (fam, "grp") in by_key, fam
    for fam in ("ch", "auxin", "fxrtn", "bus", "mtx"):
        assert (fam, "mix") in by_key
    for fam in ("ch", "bus", "mtx", "main"):
        assert (fam, "dyn") in by_key and (fam, "insert") in by_key
    assert (fam := "ch") and (fam, "gate") in by_key and (fam, "preamp") in by_key
    assert ("config", "/config/mute") in by_key and ("config", "/config/routing/AES50A") in by_key


# Verbatim real-console node lines (scales_params.md §13, transport.md §6.3/§6.4, fx_routing_scenes.md §6.3).
# `slack` = extra trailing tokens the desk may print that we do not declare (FW-dependent panFollow is the
# opposite case: we declare one MORE than a FW 2.x line shows, so `missing` allows that).
VERBATIM = [
    ('/ch/01/config "Diazno" 1 CY 1', 0, 0),
    ("/ch/01/delay OFF   0.3", 0, 0),
    ("/ch/01/preamp +0.0 OFF OFF 24  79", 0, 0),
    ("/ch/01/gate ON EXP4 -46.5 27.0 20  100  576 0", 0, 0),
    ("/ch/01/gate/filter OFF 3.0 1k39", 0, 0),
    ("/ch/01/dyn ON COMP PEAK LIN -26.0 3.0 2 8.00 71 0.03  538 POST 0 100 OFF", 0, 0),
    ("/ch/01/dyn/filter ON 3.0 1k17", 0, 0),
    ("/ch/01/insert ON PRE FX4L", 0, 0),
    ("/ch/01/eq ON", 0, 0),
    ("/ch/01/eq/1 PEQ 164.4 -3.75 1.8", 0, 0),
    ("/ch/01/eq/4 VEQ 5k97 +1.50 2.0", 0, 0),
    ("/ch/01/mix ON  +2.1 ON +0 OFF   -oo", 0, 0),
    ("/ch/01/mix/01 ON   -oo +0 PRE", 0, 1),          # FW 2.x: no panFollow yet
    ("/ch/01/mix/02 ON   -oo", 0, 0),
    ("/ch/01/mix/15 ON   0.0 +0 POST", 0, 1),
    ("/ch/01/grp %00000001 %000000", 0, 0),
    ('/auxin/01/config "" 55 GN 33', 0, 0),
    ("/auxin/01/preamp +8.8 OFF", 0, 0),
    ("/auxin/01/mix ON   -oo ON -100 OFF   -oo", 0, 0),
    ('/fxrtn/01/config "Reverb L" 61 MG', 0, 0),
    ("/fxrtn/01/mix/03 OFF   -oo -100 POST", 0, 1),
    ('/bus/01/config "1 Diazno" 53 WH', 0, 0),
    ("/bus/01/dyn ON COMP RMS LOG -22.5 2.0 0 0.00 35 0.25  226 POST 0 100 OFF", 0, 0),
    ("/bus/01/eq/6 HCut 20k00 -0.50 2.0", 0, 0),
    ("/bus/01/mix OFF   0.0 OFF +0 OFF -81.0", 0, 0),
    ("/bus/01/mix/01 ON   -oo +0 POST", 0, 1),
    ("/bus/01/mix/02 ON   -oo", 0, 0),
    ("/mtx/01/preamp OFF", 0, 0),
    ("/mtx/01/dyn OFF COMP RMS LOG 0.0 3.0 1 0.00 10 10.0  151 POST 100 OFF", 0, 0),
    ("/mtx/01/eq/1 LCut 182.4 +0.00 2.9", 0, 0),
    ("/mtx/01/mix ON -10.0", 0, 0),
    ("/main/st/dyn ON COMP RMS LOG -15.5 2.5 1 0.00 49 10.0  295 POST 100 OFF", 0, 0),
    ("/main/st/eq/1 LShv 79.6 +0.00 2.0", 0, 0),
    ("/main/st/mix ON   -oo +0", 0, 0),
    ("/main/st/mix/01 ON   0.0 +0 POST", 0, 1),
    ("/main/st/mix/02 ON   0.0", 0, 0),
    ("/main/m/mix ON -16.8", 0, 0),
    ("/main/m/mix/03 ON   -oo -2 POST", 0, 1),
    ("/dca/1 OFF  -8.3", 0, 0),
    ('/dca/1/config "Vocals" 43 CY', 0, 0),
    ("/headamp/000 +0.0 OFF", 0, 0),
    ("/headamp/127 +0.0 OFF", 0, 0),
    ("/config/mute OFF OFF OFF OFF OFF OFF", 0, 0),
    ("/config/solo   0.0 AUX78 0.0 PFL PFL PFL ON OFF ON -22 OFF OFF OFF   0.3 OFF OFF OFF", 0, 0),
    ("/config/routing/IN A1-8 A9-16 A17-24 B1-8 AUX1-4", 0, 0),
    ("/config/routing/PLAY AN1-8 AN1-8 AN1-8 AN1-8 AUX1-4", 0, 0),
    ("/config/routing REC", 0, 0),
    ("/fx/1 VREV", 0, 0),
    ("/fx/1/source MIX15 MIX15", 0, 0),
    ("/fx/5 GEQ2", 0, 0),
    ("/outputs/main/01 4 POST OFF", 0, 0),                                # fx_routing_scenes.md §4.7 node form (X32.c case OMAIN)
    ("/outputs/aux/01 0 POST OFF", 0, 0),
    ("/fx/1/par 40 2.4 100 OFF FRONT 0.0 76 11k9 1.12 0.72 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0", 0, 0),
    ('/-show/showfile/scene/001 "AAA" "aaa" %111111110 1', 0, 0),
    ('/-show/showfile/show "MyShow" 0 0 0 0 0 0 0 0 0 0 "2.08"', 1, 0),   # trailing firmware string not declared
    ("/-show/prepos/current 0", 0, 0),                                    # single-leaf node (emulator + DOC list)
    ("/-prefs/rta 50% 24 ON 72 POST BAR %000100 RMS 1.00 OFF", 0, 0),
    ("/-stat/rtasource 168", 0, 0),                                       # Main LR post-EQ (meters.md §5.2)
    ("/-stat/rtamodeeq BAR", 0, 0),
    ("/-stat/rtageqpost ON", 0, 0),
    # link nodes: emulator-shaped (OFF/ON per pair, X32CfgMain.h order), not a captured desk line
    ("/config/chlink OFF ON OFF OFF OFF OFF OFF OFF OFF OFF OFF OFF OFF OFF OFF OFF", 0, 0),
    ("/config/buslink OFF OFF OFF OFF OFF OFF OFF ON", 0, 0),
    ("/config/linkcfg ON ON ON ON", 0, 0),
]


def test_verbatim_console_lines_have_matching_field_counts(dev):
    concrete = {path: fields for path, fields in all_node_paths(dev)}
    for line, slack, missing in VERBATIM:
        toks = tokenize(line)
        path, values = toks[0], toks[1:]
        assert path in concrete, f"no node section expands to {path}"
        declared = len(concrete[path])
        assert declared - missing <= len(values) <= declared + slack, f"{line!r}: {len(values)} tokens vs {declared} declared fields"


def test_enum_tokens_in_verbatim_lines_exist(dev):
    """Every enum-format field in the verbatim lines carries a token from its declared enum."""
    concrete = {path: fields for path, fields in all_node_paths(dev)}
    params, enums = dev["params"], dev["enums"]

    def family_of(path: str) -> str:
        for fam, st in dev["strips"].items():
            if re.match(st["path"].replace("{n:02d}", r"\d\d").replace("{n}", r"\d").replace("{id}", r"(st|m)") + "(/|$)", path):
                return fam
        for fam, root in dev["meta"]["roots"].items():
            if re.match(root.replace("{n:03d}", r"\d\d\d").replace("{n}", r"\d") + "(/|$)", path):
                return fam
        raise AssertionError(path)

    for line, _slack, _missing in VERBATIM:
        toks = tokenize(line)
        path = toks[0]
        fam = family_of(path)
        for rel, tok in zip(concrete[path], toks[1:]):
            # reverse-template lookup: the concrete relpath itself, else a templated key that matches it
            spec = params[fam].get(rel)
            if spec is None:
                for key in params[fam]:
                    if template_vars(key) and re.fullmatch(_TEMPLATE_VAR.sub(r"[^/]+", key), rel):
                        spec = params[fam][key]
                        break
            assert spec is not None, (path, rel)
            if spec["node"] == "enum":
                assert tok in enums[spec["enum"]], f"{line!r}: token {tok!r} not in enum {spec['enum']} for {rel}"
            elif spec["node"] == "onoff":
                assert tok in ("OFF", "ON"), (line, rel, tok)


def test_full_sweep_node_count(dev, capsys):
    """Count every concrete node path a full snapshot sweep needs and print the breakdown."""
    paths = all_node_paths(dev)
    concrete = [p for p, _ in paths]
    assert len(set(concrete)) == len(concrete), "duplicate node paths"
    assert all(p.startswith("/") and not p.endswith("/") and "{" not in p for p in concrete)
    per_family: dict[str, int] = {}
    for sec in dev["nodes"]:
        per_family[sec["family"]] = per_family.get(sec["family"], 0) + len(expand_section(sec, dev["strips"]))
    total = len(concrete)
    with capsys.disabled():
        print(f"\n[device.yaml] full /node sweep = {total} concrete node paths: " + ", ".join(f"{k}={v}" for k, v in per_family.items()))
    # 48 input strips x 16 sends = 768 nodes alone; a faithful sweep is ~2100 — a console-saved scene file has
    # 2104 node lines (scales_params.md §13), so DESIGN §7's "< 1500" is not reachable without dropping sections.
    assert per_family["ch"] == 32 * 31 and per_family["headamp"] == 128 and per_family["fx"] == 20
    assert per_family["show"] == 1 + 1 + 100 and per_family["dca"] == 16   # prepos leaf, show line, scene 000-099
    assert per_family["config"] == 21 and per_family["stat"] == 7 and per_family["prefs"] == 1
    assert per_family["auxin"] == 8 * 25 and per_family["fxrtn"] == 8 * 24 and per_family["bus"] == 16 * 19
    assert per_family["mtx"] == 6 * 14 and per_family["main"] == 38
    assert per_family["outputs"] == 16 + 6   # XLR OUT taps + AUX OUT taps
    assert total == sum(per_family.values()) == 2127
    assert 2000 <= total <= 2500
    # spot-check a few concrete paths + their concrete fields
    d = dict(paths)
    assert d["/ch/05/mix/03"] == ["mix/03/on", "mix/03/level", "mix/03/pan", "mix/03/type", "mix/03/panFollow"]
    assert d["/ch/05/mix/04"] == ["mix/04/on", "mix/04/level"]
    assert d["/bus/16/eq/6"] == ["eq/6/type", "eq/6/f", "eq/6/g", "eq/6/q"]
    assert d["/headamp/000"] == ["gain", "phantom"] and "/headamp/128" not in d
    assert d["/-show/showfile/scene/099"][0] == "showfile/scene/099/name"
    assert "/fx/5/source" not in d and "/fx/4/source" in d
    assert d["/-show/prepos/current"] == ["prepos/current"] and d["/-stat/rtasource"] == ["rtasource"]
    assert d["/config/chlink"][0] == "chlink/1-2" and d["/config/mtxlink"] == ["mtxlink/1-2", "mtxlink/3-4", "mtxlink/5-6"]


# ---------------------------------------------------------------------------------------------- tables

def test_rta_section(dev):
    rta = dev["rta"]
    assert rta["meter_type"] == 15 and rta["bands"] == 100 and rta["frame_period_s"] == 0.05
    hz = rta["band_hz"]
    assert len(hz) == 100
    assert all(b > a for a, b in zip(hz, hz[1:])), "band_hz must be ascending"
    for i, v in enumerate(hz):
        assert v == round(20 * 2 ** (i / 10), 2), i
    assert hz[0] == 20.0 and hz[90] == 10240 and hz[99] == 19108.52
    assert rta["source_param"] == "/-prefs/rta/source" and rta["stat_param"] == "/-stat/rtasource"
    assert rta["pos_param"] == "/-prefs/rta/pos" and rta["options_param"] == "/-prefs/rta/options"
    # the rta params exist in the descriptor
    assert "rta/source" in dev["params"]["prefs"] and dev["params"]["prefs"]["rta/source"]["enum"] == "rta_source"
    assert "rtasource" in dev["params"]["stat"]


def test_geq_section(dev):
    geq = dev["geq"]
    hz = geq["band_hz"]
    assert hz == [20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 20000]
    assert len(hz) == 31 and all(b > a for a, b in zip(hz, hz[1:]))
    assert (geq["par_a_first"], geq["par_a_master"], geq["par_b_first"], geq["par_b_master"]) == (1, 32, 33, 64)
    assert geq["gain_scale"] in dev["scales"] and dev["scales"][geq["gain_scale"]] == {"kind": "lin", "lo": -15, "hi": 15, "steps": 61, "unit": "dB"}
    assert geq["insert_sel_enum"] in dev["enums"] and len(dev["enums"][geq["insert_sel_enum"]]) == 23
    assert set(geq["fx_types_dual"] + geq["fx_types_stereo"]) <= set(dev["enums"]["fx_type_58"])
    assert geq["insert_slots_preferred"] == [5, 6, 7, 8]


def test_policy_detector_ringout_mics_values(dev):
    assert dev["policy"] == {
        "ch_fader_max_db": 5, "bus_fader_max_db": 0, "main_fader_max_db": 0, "send_max_db": 0, "dca_fader_max_db": 0, "eq_gain_abs_max_db": 15,
        "dyn_makeup_max_db": 6, "relative_max_db": 6, "relative_max_db_show_mode": 3, "ramp_default_ms": 300, "ramp_step_ms": 20, "writes_per_second": 50,
        "confirm_token_ttl_s": 300, "read_cache_ttl_s": 2.0, "show_mode_default": False,
    }
    det = dev["detector"]
    assert det["prominence_db"] == 12 and det["neighbour_bins"] == 3 and det["persistence_frames"] == 3
    assert det["weights"] == {"prominence": 0.3, "persistence": 0.2, "growth": 0.5} and det["confidence_threshold"] == 0.7
    assert det["notch_step_db"] == -3 and det["notch_max_db"] == -9 and det["notch_budget_default"] == 6
    assert dev["ringout"] == {"step_db": 1.0, "dwell_ms": 1500, "max_step_db": 3.0, "min_dwell_ms": 250, "safety_margin_db": 3, "abort_backoff_db": 6, "master_ceiling_db": 0, "start_warn_db": -10}
    assert dev["mics"] == {"send_floor_db": -40, "signal_floor_db": -80, "mute_group_convention": 6}


def test_guarded_globs(dev):
    for g in ["/main/st/mix/*", "/main/m/mix/*", "/headamp/*/phantom", "/config/routing/*", "/config/mute/*", "/*/insert/*",
              "/fx/*/type", "/fx/*/source/*", "/*/config/source", "/*/grp/*", "/-action/*", "/save", "/load"]:
        assert g in dev["guarded"], g


def test_node_formats_used(dev, capsys):
    used = sorted({spec["node"] for params in dev["params"].values() for spec in params.values()})
    with capsys.disabled():
        print("[device.yaml] node formats used: " + " ".join(used) + " | additions beyond DESIGN.md s.9: " + " ".join(sorted(set(used) & NODE_FORMATS_ADDED)))
    assert set(used) <= NODE_FORMATS
    assert "hex" not in used
