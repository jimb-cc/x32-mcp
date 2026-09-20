"""descriptor.py — loads the real device.yaml; expected values come from docs/research (scales_params.md,
transport.md §6.3-§6.5, fx_routing_scenes.md) and DESIGN.md §7."""

from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest
import yaml

from x32mcp.descriptor import Descriptor, DescriptorError, NodePath, NodeSection, ParamSpec, StripFamily
from x32mcp.scales import NEG_INF_DB, Scale, ScaleError
from x32mcp.targets import Target

ROOT = Path(__file__).resolve().parent.parent
DEVICE_YAML = ROOT / "device.yaml"


@pytest.fixture(scope="module")
def d() -> Descriptor:
    return Descriptor.load()


@pytest.fixture(scope="module")
def raw() -> dict:
    with DEVICE_YAML.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def mutated(raw: dict) -> dict:
    return copy.deepcopy(raw)


# ---------------------------------------------------------------------------------------------- loading


def test_loads_real_file_default_and_explicit_path(d):
    assert d.path == DEVICE_YAML
    assert Descriptor.load(DEVICE_YAML).all_node_paths() == d.all_node_paths()
    assert Descriptor.load(str(DEVICE_YAML)).meta["model"] == "X32"
    assert d.meta["osc_port"] == 10023
    assert "X32" in repr(d) and "2103 nodes" in repr(d)


def test_counts(d):
    assert len(d.scales) == 29 and all(isinstance(s, Scale) for s in d.scales.values())
    assert len(d.enums) == 37
    assert list(d.strips) == ["ch", "auxin", "fxrtn", "bus", "mtx", "main", "dca"]
    assert d.families == ("ch", "auxin", "fxrtn", "bus", "mtx", "main", "dca", "headamp", "fx", "config", "show", "stat", "prefs", "action")
    assert len(d.params["fx"]) == 67 and len(d.params["headamp"]) == 2 and len(d.params["action"]) == 3
    assert len(d.params["ch"]) == 63 and len(d.params["dca"]) == 5
    assert len(list(d.iter_params())) == sum(len(p) for p in d.params.values()) == 491
    assert len(d.node_sections()) == 90
    assert d.roots == {
        "headamp": "/headamp/{n:03d}", "fx": "/fx/{n}", "config": "/config", "show": "/-show",
        "stat": "/-stat", "prefs": "/-prefs", "action": "/-action",
    }


def test_scales_and_enums_are_typed(d):
    assert d.scales["fader"] == Scale("level", steps=1024, unit="dB")
    assert d.scales["send"] == Scale("level", steps=161, unit="dB")
    assert d.scales["q"] == Scale("log", lo=10, hi=0.3, steps=72)  # decreasing
    assert d.scale("freq") == Scale("log", lo=20, hi=20000, steps=201, unit="Hz")
    assert d.enum("color")[1] == "RD" and len(d.enum("insert_sel")) == 23
    assert d.enums["fx_type_14"][27] == "GEQ2" and d.enums["fx_type_58"][0] == "GEQ2"
    with pytest.raises(DescriptorError):
        d.enum("nope")
    with pytest.raises(DescriptorError):
        d.scale("nope")


def test_strips(d):
    ch = d.strips["ch"]
    assert isinstance(ch, StripFamily)
    assert ch.count == 32 and ch.sends == 16 and ch.eq_bands == 4 and ch.preamp and ch.gate and ch.dyn and ch.insert
    assert ch.indices == tuple(range(1, 33)) and ch.root(5) == "/ch/05" and ch.label_for(5) == "Ch 5"
    assert ch.roots()[0] == ("/ch/01", {"n": 1})
    main = d.strips["main"]
    assert main.ids == ("st", "m") and main.count is None and main.send_target == "mtx"
    assert main.root("st") == "/main/st" and main.label_for("m") == "Main M/C"
    assert main.roots() == [("/main/st", {"id": "st"}), ("/main/m", {"id": "m"})]
    assert d.strips["auxin"].preamp and not d.strips["auxin"].gate and not d.strips["fxrtn"].preamp
    assert d.strips["mtx"].sends is None and d.strips["dca"].eq_bands is None
    assert d.strips["bus"].send_target == "mtx" and d.strips["bus"].sends == 6


def test_strip_targets(d):
    assert d.strip_targets("main") == [Target("main", "st"), Target("main", "m")]
    chs = d.strip_targets("ch")
    assert len(chs) == 32 and chs[4] == Target("ch", 5) and chs[4].osc_prefix == "/ch/05"
    assert [t.key for t in d.strip_targets("dca")] == [f"dca.{i}" for i in range(1, 9)]
    assert len(d.strip_targets("mtx")) == 6
    with pytest.raises(DescriptorError):
        d.strip_targets("headamp")


def test_accessor_dicts(d):
    assert d.policy["ch_fader_max_db"] == 5 and d.policy["read_cache_ttl_s"] == 2.0 and d.policy["show_mode_default"] is False
    assert d.rta["meter_type"] == 15 and len(d.rta["band_hz"]) == 100 and d.rta["band_hz"][90] == 10000
    assert d.rta["source_param"] == "/-prefs/rta/source" and d.rta["stat_param"] == "/-stat/rtasource"
    assert len(d.geq["band_hz"]) == 31 and d.geq["gain_scale"] == "geq_gain" and d.geq["insert_slots_preferred"] == [5, 6, 7, 8]
    assert d.geq["fx_types_dual"] == ["GEQ2", "TEQ2"]
    assert d.detector["prominence_db"] == 12 and d.detector["weights"]["growth"] == 0.5
    assert d.ringout["master_ceiling_db"] == 0 and d.mics["mute_group_convention"] == 6
    assert d.guarded[0] == "/main/st/mix/*" and "/save" in d.guarded and "/load" in d.guarded


# ---------------------------------------------------------------------------------------------- params


def test_param_template_lookup_and_key(d):
    s = d.param("ch", "mix/{send:02d}/level")
    assert isinstance(s, ParamSpec)
    assert s.key == "ch:mix/{send:02d}/level" and s.family == "ch" and s.relpath == "mix/{send:02d}/level"
    assert s.osc_type == "f" and s.scale_name == "send" and s.enum is None and s.enum_name is None
    assert s.node_fmt == "db1" and s.tier == 1 and s.clamp_max == 0 and s.clamp_min is None
    assert s.inverted_mute is False and s.unit == "dB"
    assert s.template == "/ch/{n:02d}/mix/{send:02d}/level" and s.vars == ("n", "send") and s.is_template
    assert str(s) == s.key
    # concrete relpath resolves to the same template spec
    assert d.param("ch", "mix/03/level") is s
    assert d.param("headamp", "gain").address(n=5) == "/headamp/005/gain"
    assert d.param("show", "showfile/scene/007/name") is d.param("show", "showfile/scene/{idx:03d}/name")
    assert not d.param("config", "routing/IN/1-8").is_template and d.param("config", "routing/IN/1-8").vars == ()
    with pytest.raises(DescriptorError):
        d.param("ch", "nope/x")
    with pytest.raises(DescriptorError):
        d.param("zzz", "mix/fader")
    with pytest.raises(DescriptorError):
        d.param("ch", "mix/17/level")  # send out of range


def test_param_flags_from_yaml(d):
    assert d.param("ch", "mix/on").inverted_mute is True
    assert d.param("ch", "mix/{send:02d}/on").inverted_mute is True
    assert d.param("main", "mix/on").inverted_mute is True and d.param("main", "mix/on").tier == 2
    assert d.param("dca", "on").inverted_mute is True and d.param("dca", "fader").clamp_max == 0
    assert d.param("config", "mute/1").inverted_mute is False and d.param("config", "mute/1").tier == 2
    assert d.param("ch", "mix/fader").clamp_max == 5 and d.param("bus", "mix/fader").clamp_max == 0
    assert d.param("ch", "eq/{band}/g").clamp_min == -15 and d.param("ch", "eq/{band}/g").clamp_max == 15
    assert d.param("ch", "config/icon").clamp_min == 1 and d.param("ch", "config/icon").clamp_max == 74
    assert d.param("action", "goscene").clamp_max == 99 and d.param("action", "goscene").tier == 2
    assert d.param("headamp", "gain").tier == 2 and d.param("headamp", "phantom").tier == 2
    assert d.param("config", "solo/level").tier == 0 and d.param("prefs", "rta/source").tier == 1
    color = d.param("ch", "config/color")
    assert color.enum == d.enums["color"] and color.enum_name == "color" and color.scale_name is None and color.osc_type == "i"
    assert d.param("fx", "type").enum == d.enums["fx_type_14"] and len(d.param("fx", "type").enum) == 61
    assert d.param("bus", "mix/{send:02d}/type").enum_name == "send_type_mtx"
    assert d.param("mtx", "eq/{band}/type").enum_name == "eq_type_mtx"
    assert d.param("ch", "config/name").osc_type == "s" and d.param("ch", "config/name").scale.kind == "str"
    assert d.param("ch", "mix/{send:02d}/panFollow").node_fmt == "int" and d.param("ch", "mix/{send:02d}/panFollow").scale.kind == "bool"


def test_address_rendering(d):
    s = d.param("ch", "mix/{send:02d}/level")
    assert s.address(Target("ch", 5), send=3) == "/ch/05/mix/03/level"
    assert s.address(n=5, send=16) == "/ch/05/mix/16/level"
    assert d.param("main", "mix/fader").address(Target("main", "st")) == "/main/st/mix/fader"
    assert d.param("main", "mix/{send:02d}/level").address(Target("main", "m"), send=6) == "/main/m/mix/06/level"
    assert d.param("dca", "on").address(Target("dca", 3)) == "/dca/3/on"
    assert d.param("headamp", "phantom").address(n=0) == "/headamp/000/phantom"
    assert d.param("headamp", "phantom").address(n=127) == "/headamp/127/phantom"
    assert d.param("fx", "par/07").address(n=5) == "/fx/5/par/07"
    assert d.param("show", "showfile/scene/{idx:03d}/name").address(idx=7) == "/-show/showfile/scene/007/name"
    assert d.param("config", "routing/IN/1-8").address() == "/config/routing/IN/1-8"
    assert d.param("action", "goscene").address() == "/-action/goscene"
    assert d.param("stat", "rtasource").address() == "/-stat/rtasource"
    assert d.address("bus", "eq/{band}/f", Target("bus", 16), band=6) == "/bus/16/eq/6/f"
    # errors: missing var, wrong family, out of range, wrong type
    with pytest.raises(DescriptorError, match="missing template variable 'send'"):
        s.address(Target("ch", 5))
    with pytest.raises(DescriptorError, match="bus.3"):
        s.address(Target("bus", 3), send=1)
    with pytest.raises(DescriptorError, match="out of range"):
        s.address(n=5, send=17)
    with pytest.raises(DescriptorError, match="out of range"):
        d.param("ch", "eq/{band}/g").address(n=1, band=5)
    with pytest.raises(DescriptorError, match="out of range"):
        d.param("main", "eq/{band}/g").address(id="st", band=7)
    with pytest.raises(DescriptorError):
        d.param("main", "mix/fader").address(id="lr")
    with pytest.raises(DescriptorError):
        d.param("headamp", "gain").address(n=128)
    with pytest.raises(DescriptorError):
        d.param("headamp", "gain").address(n=True)
    with pytest.raises(DescriptorError):
        d.param("ch", "mix/fader").address(n="05")


# ---------------------------------------------------------------------------------------------- reverse lookup


@pytest.mark.parametrize(
    "address,key,vars",
    [
        ("/ch/05/mix/03/level", "ch:mix/{send:02d}/level", {"n": 5, "send": 3}),
        ("/ch/05/mix/16/on", "ch:mix/{send:02d}/on", {"n": 5, "send": 16}),
        ("/ch/32/mix/fader", "ch:mix/fader", {"n": 32}),
        ("/ch/05/eq/2/g", "ch:eq/{band}/g", {"n": 5, "band": 2}),
        ("/ch/01/eq/on", "ch:eq/on", {"n": 1}),
        ("/ch/01/gate/filter/f", "ch:gate/filter/f", {"n": 1}),
        ("/ch/08/automix/weight", "ch:automix/weight", {"n": 8}),
        ("/auxin/08/preamp/trim", "auxin:preamp/trim", {"n": 8}),
        ("/auxin/01/config/source", "auxin:config/source", {"n": 1}),
        ("/fxrtn/01/config/name", "fxrtn:config/name", {"n": 1}),
        ("/bus/16/mix/06/type", "bus:mix/{send:02d}/type", {"n": 16, "send": 6}),
        ("/bus/03/mix/fader", "bus:mix/fader", {"n": 3}),
        ("/bus/01/eq/6/q", "bus:eq/{band}/q", {"n": 1, "band": 6}),
        ("/mtx/06/mix/fader", "mtx:mix/fader", {"n": 6}),
        ("/mtx/01/preamp/invert", "mtx:preamp/invert", {"n": 1}),
        ("/main/st/mix/fader", "main:mix/fader", {"id": "st"}),
        ("/main/st/mix/pan", "main:mix/pan", {"id": "st"}),
        ("/main/m/mix/02/level", "main:mix/{send:02d}/level", {"id": "m", "send": 2}),
        ("/main/m/eq/6/type", "main:eq/{band}/type", {"id": "m", "band": 6}),
        ("/dca/3/on", "dca:on", {"n": 3}),
        ("/dca/8/fader", "dca:fader", {"n": 8}),
        ("/dca/3/config/name", "dca:config/name", {"n": 3}),
        ("/headamp/000/gain", "headamp:gain", {"n": 0}),
        ("/headamp/005/gain", "headamp:gain", {"n": 5}),
        ("/headamp/127/phantom", "headamp:phantom", {"n": 127}),
        ("/fx/1/type", "fx:type", {"n": 1}),
        ("/fx/8/type", "fx:type", {"n": 8}),
        ("/fx/1/source/l", "fx:source/l", {"n": 1}),
        ("/fx/4/source/r", "fx:source/r", {"n": 4}),
        ("/fx/5/par/07", "fx:par/07", {"n": 5}),
        ("/fx/8/par/64", "fx:par/64", {"n": 8}),
        ("/config/routing/IN/1-8", "config:routing/IN/1-8", {}),
        ("/config/routing/OUT/13-16", "config:routing/OUT/13-16", {}),
        ("/config/chlink/31-32", "config:chlink/31-32", {}),
        ("/config/mute/6", "config:mute/6", {}),
        ("/config/solo/level", "config:solo/level", {}),
        ("/config/talk/A/level", "config:talk/A/level", {}),
        ("/-show/prepos/current", "show:prepos/current", {}),
        ("/-show/showfile/show/name", "show:showfile/show/name", {}),
        ("/-show/showfile/scene/007/name", "show:showfile/scene/{idx:03d}/name", {"idx": 7}),
        ("/-show/showfile/scene/099/hasdata", "show:showfile/scene/{idx:03d}/hasdata", {"idx": 99}),
        ("/-show/showfile/scene/000/safes", "show:showfile/scene/{idx:03d}/safes", {"idx": 0}),
        ("/-stat/rtasource", "stat:rtasource", {}),
        ("/-stat/solo", "stat:solo", {}),
        ("/-prefs/rta/source", "prefs:rta/source", {}),
        ("/-prefs/show_control", "prefs:show_control", {}),
        ("/-action/goscene", "action:goscene", {}),
    ],
)
def test_param_for_address(d, address, key, vars):
    found = d.param_for_address(address)
    assert found is not None, address
    spec, got = found
    assert spec.key == key and got == vars
    assert spec is d.params[spec.family][spec.relpath]
    # round trip through address()
    assert spec.address(**got) == address
    # cached second call gives the same objects
    assert d.param_for_address(address)[0] is spec


@pytest.mark.parametrize(
    "address",
    [
        "/ch/33/mix/fader", "/ch/00/mix/fader", "/ch/5/mix/fader", "/ch/005/mix/fader", "/CH/05/mix/fader",
        "/ch/05/eq/5/g", "/ch/05/eq/0/g", "/ch/05/mix/17/level", "/ch/05/mix/3/level", "/ch/05/mix/03",
        "/bus/01/mix/07/level", "/bus/17/mix/fader", "/bus/01/eq/7/f", "/mtx/07/mix/fader", "/mtx/01/mix/01/level",
        "/main/lr/mix/fader", "/main/st/mix/07/level", "/main/st/mix/st", "/dca/0/on", "/dca/9/on", "/dca/03/on",
        "/headamp/128/gain", "/headamp/05/gain", "/headamp/5/gain", "/fx/0/type", "/fx/9/type", "/fx/01/type",
        "/fx/5/par/65", "/fx/1/par/7", "/-show/showfile/scene/100/name", "/-show/showfile/scene/07/name",
        "/-stat/rta", "/-stat", "/config", "/config/routing", "/ch", "/ch/05", "/", "", "ch/05/mix/fader",
        "/nope", "/xremote", "/info", "/save", "/load", "/ch/05/mix/fader/", "/ch/05/mix/fader/x",
    ],
)
def test_param_for_address_rejects(d, address):
    assert d.param_for_address(address) is None


def test_param_for_address_non_string(d):
    assert d.param_for_address(None) is None  # type: ignore[arg-type]
    assert d.param_for_address(5) is None  # type: ignore[arg-type]


def test_every_param_round_trips_through_reverse_lookup(d):
    """Render each template with the smallest and largest valid index set and look it back up."""
    n_checked = 0
    for spec in d.iter_params():
        for pick in (0, -1):
            vars = {}
            for name in spec.vars:
                r = spec.var_ranges[name]
                vars[name] = sorted(r)[pick] if isinstance(r, frozenset) else r[pick]
            addr = spec.address(**vars)
            found = d.param_for_address(addr)
            assert found is not None and found[0] is spec and found[1] == vars, addr
            n_checked += 1
    assert n_checked == 2 * 491


# ---------------------------------------------------------------------------------------------- tiers


@pytest.mark.parametrize(
    "address,tier",
    [
        ("/main/st/mix/fader", 2),
        ("/main/st/mix/on", 2),
        ("/main/st/mix/01/level", 2),
        ("/main/m/mix/fader", 2),
        ("/ch/05/mix/fader", 1),
        ("/ch/05/mix/on", 1),
        ("/ch/05/mix/03/level", 1),
        ("/ch/05/mix/03/on", 1),
        ("/ch/05/config/name", 1),
        ("/ch/05/config/color", 1),
        ("/ch/05/config/source", 2),
        ("/ch/05/eq/1/g", 1),
        ("/ch/05/dyn/thr", 1),
        ("/ch/05/insert/on", 2),
        ("/ch/05/insert/sel", 2),
        ("/ch/05/grp/dca", 2),
        ("/ch/05/grp/mute", 2),
        ("/bus/03/mix/fader", 1),
        ("/bus/03/insert/sel", 2),
        ("/mtx/01/mix/fader", 1),
        ("/dca/1/fader", 1),
        ("/dca/1/on", 1),
        ("/headamp/000/gain", 2),
        ("/headamp/000/phantom", 2),
        ("/fx/1/type", 2),
        ("/fx/5/type", 2),
        ("/fx/1/source/l", 2),
        ("/fx/5/par/01", 1),
        ("/-action/goscene", 2),
        ("/-action/gocue", 2),
        ("/config/routing/IN/1-8", 2),
        ("/config/routing/OUT/1-4", 2),
        ("/config/routing/routswitch", 2),
        ("/config/chlink/1-2", 2),
        ("/config/linkcfg/eq", 2),
        ("/config/mute/6", 2),
        ("/config/solo/level", 0),
        ("/config/talk/A/level", 0),
        ("/config/mono/mode", 0),
        ("/-show/prepos/current", 0),
        ("/-show/showfile/scene/001/name", 0),
        ("/-stat/solo", 0),
        ("/-stat/rtasource", 0),
        ("/-prefs/rta/source", 1),
        ("/-prefs/show_control", 1),
        ("/-prefs/name", 0),
        ("/save", 2),
        ("/load", 2),
        ("/xremote", 1),  # unknown → 1 (module doc)
        ("/nope/x", 1),
    ],
)
def test_tier_for(d, address, tier):
    assert d.tier_for(address) == tier


def test_is_guarded_glob_semantics(d):
    assert d.is_guarded("/ch/01/grp/dca")  # '*' spans '/'
    assert d.is_guarded("/main/st/mix/01/level")
    assert d.is_guarded("/headamp/127/phantom") and not d.is_guarded("/headamp/127/gain")
    assert d.is_guarded("/save") and not d.is_guarded("/saveme")
    assert not d.is_guarded("/MAIN/st/mix/fader")  # case-sensitive even on Windows
    assert not d.is_guarded("/ch/01/mix/fader")


# ---------------------------------------------------------------------------------------------- nodes


def test_all_node_paths_count_and_shape(d, capsys):
    paths = d.all_node_paths()
    concrete = [p for p, _ in paths]
    total = len(concrete)
    per_family: dict[str, int] = {}
    for n in d.nodes():
        per_family[n.family] = per_family.get(n.family, 0) + 1
    with capsys.disabled():
        print(f"\n[descriptor] full /node sweep = {total} node paths: " + ", ".join(f"{k}={v}" for k, v in per_family.items()))
    assert len(set(concrete)) == total
    assert all(p.startswith("/") and not p.endswith("/") and "{" not in p for p in concrete)
    assert all(isinstance(f, tuple) and f and all("{" not in x for x in f) for _, f in paths)
    # research: a console scene file has 2104 node lines; DESIGN's "< 1500" is not reachable (deviation)
    assert total == 2103 and total < 2500
    assert per_family == {
        "ch": 992, "auxin": 200, "fxrtn": 192, "bus": 304, "mtx": 84, "main": 38, "dca": 16,
        "headamp": 128, "fx": 20, "config": 21, "show": 102, "stat": 5, "prefs": 1,
    }
    # sweep order = scene-file order (scales_params.md §13): families as in the yaml, strip-major
    # inside a family, sections in yaml order — /ch/01/config … /ch/01/grp, /ch/02/config …
    assert concrete[0] == "/ch/01/config" and concrete[-1] == "/-prefs/rta"
    assert concrete[:3] == ["/ch/01/config", "/ch/01/delay", "/ch/01/preamp"]
    assert concrete.index("/ch/01/mix/01") < concrete.index("/ch/01/grp") < concrete.index("/ch/02/config")
    assert concrete.index("/ch/32/grp") < concrete.index("/auxin/01/config")
    i = concrete.index("/dca/1")
    assert concrete[i:i + 3] == ["/dca/1", "/dca/1/config", "/dca/2"]
    i = concrete.index("/main/st/eq/6")
    assert concrete[i + 1:i + 3] == ["/main/st/mix", "/main/st/mix/01"]
    assert concrete.index("/main/st/grp") < concrete.index("/main/m/config")
    fam_order = []
    for n in d.nodes():
        if not fam_order or fam_order[-1] != n.family:
            fam_order.append(n.family)
    assert fam_order == ["ch", "auxin", "fxrtn", "bus", "mtx", "main", "dca", "headamp", "fx", "config", "show", "stat", "prefs"]
    assert paths == [(n.path, n.fields) for n in d.nodes()]


def test_node_paths_spot_checks(d):
    by = dict(d.all_node_paths())
    assert by["/ch/05/mix/03"] == ("mix/03/on", "mix/03/level", "mix/03/pan", "mix/03/type", "mix/03/panFollow")
    assert by["/ch/05/mix/04"] == ("mix/04/on", "mix/04/level")  # fields_even
    assert by["/ch/05/mix/16"] == ("mix/16/on", "mix/16/level")
    assert by["/ch/05/mix/15"][-1] == "mix/15/panFollow"
    assert by["/ch/01/config"] == ("config/name", "config/icon", "config/color", "config/source")
    assert by["/ch/01/dyn"][12] == "dyn/keysrc" and len(by["/ch/01/dyn"]) == 15
    assert len(by["/mtx/01/dyn"]) == 14 and "dyn/keysrc" not in by["/mtx/01/dyn"] and len(by["/main/st/dyn"]) == 14
    assert by["/bus/16/eq/6"] == ("eq/6/type", "eq/6/f", "eq/6/g", "eq/6/q")
    assert by["/bus/01/mix/06"] == ("mix/06/on", "mix/06/level") and "/bus/01/mix/07" not in by
    assert by["/mtx/01/mix"] == ("mix/on", "mix/fader") and by["/mtx/01/preamp"] == ("preamp/invert",)
    assert by["/main/st/mix"] == ("mix/on", "mix/fader", "mix/pan") and by["/main/m/mix"] == ("mix/on", "mix/fader")
    assert by["/main/m/mix/06"] == ("mix/06/on", "mix/06/level") and by["/main/st/mix/05"][2] == "mix/05/pan"
    assert by["/dca/1"] == ("on", "fader") and by["/dca/8/config"] == ("config/name", "config/icon", "config/color")
    assert by["/headamp/000"] == ("gain", "phantom") and by["/headamp/127"] == ("gain", "phantom") and "/headamp/128" not in by
    assert by["/fx/1"] == ("type",) and by["/fx/8"] == ("type",)
    assert by["/fx/4/source"] == ("source/l", "source/r") and "/fx/5/source" not in by
    assert len(by["/fx/5/par"]) == 64 and by["/fx/5/par"][0] == "par/01" and by["/fx/5/par"][63] == "par/64"
    assert by["/config/routing"] == ("routing/routswitch",)
    assert by["/config/routing/OUT"] == ("routing/OUT/1-4", "routing/OUT/5-8", "routing/OUT/9-12", "routing/OUT/13-16")
    assert by["/config/routing/IN"][-1] == "routing/IN/AUX" and len(by["/config/solo"]) == 17
    assert len(by["/config/chlink"]) == 16 and by["/config/linkcfg"] == ("linkcfg/hadly", "linkcfg/eq", "linkcfg/dyn", "linkcfg/fdrmute")
    assert by["/config/mute"] == tuple(f"mute/{i}" for i in range(1, 7))
    assert by["/-show/prepos/current"] == ("prepos/current",) and "/-show/prepos" not in by
    assert by["/-show/showfile/scene/007"] == ("showfile/scene/007/name", "showfile/scene/007/notes", "showfile/scene/007/safes", "showfile/scene/007/hasdata")
    assert "/-show/showfile/scene/100" not in by and "/-show/showfile/scene/099" in by
    assert by["/-stat/rtasource"] == ("rtasource",) and "/-stat" not in by and "/-stat/rta" not in by
    assert by["/-prefs/rta"] == ("rta/visibility", "rta/gain", "rta/autogain", "rta/source", "rta/pos", "rta/mode", "rta/options", "rta/det", "rta/decay", "rta/peakhold")
    assert "/ch/01/automix" not in by  # only ch 01-08 have it; not swept


def test_every_node_field_resolves_to_a_param_spec(d):
    for node in d.nodes():
        assert isinstance(node, NodePath)
        assert len(node.fields) == len(node.specs) == len(node) == len(node.addresses)
        assert all(isinstance(s, ParamSpec) and s.family == node.family for s in node.specs)
        assert d.node_specs(node.path) == node.specs and d.node(node.path) is node
        for field, spec, addr in zip(node.fields, node.specs, node.addresses):
            assert addr == f"{node.root}/{field}"
            assert addr == node.path or addr.startswith(node.path + "/")
            found = d.param_for_address(addr)
            assert found is not None and found[0] is spec, addr
            assert spec.address(**found[1]) == addr
        # list(node) pairs fields with specs
        assert list(node) == list(zip(node.fields, node.specs))
    with pytest.raises(DescriptorError):
        d.node("/ch/05/mix/fader")
    with pytest.raises(DescriptorError):
        d.node_specs("/nope")


def test_node_path_roots_vars_and_targets(d):
    n = d.node("/ch/05/mix/03")
    assert n.root == "/ch/05" and n.vars == {"n": 5, "send": 3} and n.target == Target("ch", 5)
    assert n.addresses == ("/ch/05/mix/03/on", "/ch/05/mix/03/level", "/ch/05/mix/03/pan", "/ch/05/mix/03/type", "/ch/05/mix/03/panFollow")
    assert n.section.section == "mix/{send:02d}" and n.section.family == "ch"
    n = d.node("/main/st/mix")
    assert n.root == "/main/st" and n.vars == {"id": "st"} and n.target == Target("main", "st") and not n.section.is_strip_section
    n = d.node("/dca/3")
    assert n.root == "/dca/3" and n.vars == {"n": 3} and n.target == Target("dca", 3) and n.addresses == ("/dca/3/on", "/dca/3/fader")
    n = d.node("/headamp/005")
    assert n.root == "/headamp/005" and n.vars == {"n": 5} and n.target is None and n.addresses == ("/headamp/005/gain", "/headamp/005/phantom")
    n = d.node("/fx/5/par")
    assert n.root == "/fx/5" and n.addresses[6] == "/fx/5/par/07"
    n = d.node("/-stat/rtasource")
    assert n.root == "/-stat" and n.addresses == ("/-stat/rtasource",)  # leaf node: address == path
    n = d.node("/-show/prepos/current")
    assert n.addresses == ("/-show/prepos/current",) and n.target is None
    n = d.node("/-show/showfile/scene/007")
    assert n.root == "/-show" and n.vars == {"idx": 7} and n.addresses[0] == "/-show/showfile/scene/007/name"
    n = d.node("/config/routing/OUT")
    assert n.root == "/config" and n.vars == {} and n.addresses[1] == "/config/routing/OUT/5-8"


def test_node_paths_for_filters(d):
    ch5 = d.node_paths_for(target=Target("ch", 5))
    assert len(ch5) == 31 and all(n.path.startswith("/ch/05/") for n in ch5)
    assert [n.path for n in d.node_paths_for(target=Target("dca", 2))] == ["/dca/2", "/dca/2/config"]
    assert len(d.node_paths_for(family="headamp")) == 128 and len(d.node_paths_for(family="main")) == 38
    assert [n.path for n in d.node_paths_for(target=Target("main", "m")) if n.path.endswith("/mix")] == ["/main/m/mix"]
    assert d.node_paths_for(family="ch", target=Target("bus", 1)) == []


def test_node_sections_and_expand(d):
    secs = d.node_sections()
    assert all(isinstance(s, NodeSection) for s in secs)
    sends = next(s for s in secs if s.family == "ch" and s.section == "mix/{send:02d}")
    assert sends.for_vars == {"send": (1, 16)} and sends.path_template == "/ch/{n:02d}/mix/{send:02d}"
    assert sends.fields_even == ("mix/{send:02d}/on", "mix/{send:02d}/level") and sends.is_strip_section
    assert len(sends.var_combos()) == 16
    expanded = sends.expand(d.strips)
    assert len(expanded) == 32 * 16 and expanded[0] == ("/ch/01/mix/01", {"n": 1, "send": 1})
    assert expanded[-1] == ("/ch/32/mix/16", {"n": 32, "send": 16})
    assert sends.fields_for({"n": 1, "send": 3}) == ("mix/03/on", "mix/03/level", "mix/03/pan", "mix/03/type", "mix/03/panFollow")
    assert sends.fields_for({"n": 1, "send": 4}) == ("mix/04/on", "mix/04/level")
    assert sends.uses_even_fields({"send": 2}) and not sends.uses_even_fields({"send": 3})
    assert sends.field_templates_for({"send": 2}) == sends.fields_even
    main_mix = next(s for s in secs if s.path_template == "/main/st/mix")
    assert not main_mix.is_strip_section and main_mix.section == "" and main_mix.for_vars == {}
    assert main_mix.expand(d.strips) == [("/main/st/mix", {"id": "st"})]
    dca = next(s for s in secs if s.family == "dca" and not s.is_strip_section)
    assert dca.expand(d.strips) == [(f"/dca/{i}", {"n": i}) for i in range(1, 9)]
    ha = next(s for s in secs if s.family == "headamp")
    assert ha.for_vars == {"n": (0, 127)} and ha.expand(d.strips)[0] == ("/headamp/000", {"n": 0})
    eq = next(s for s in secs if s.family == "bus" and s.section == "eq/{band}")
    assert len(eq.expand(d.strips)) == 16 * 6 and eq.fields_for({"band": 6}) == ("eq/6/type", "eq/6/f", "eq/6/g", "eq/6/q")
    with pytest.raises(DescriptorError):
        eq.fields_for({})  # missing band
    assert sends.key == "ch:mix/{send:02d}" and main_mix.key == "main:/main/st/mix"


def test_node_formats_used(d):
    assert d.node_formats_used() == {
        "str", "int", "sint", "onoff", "enum", "db1", "db2", "float1", "float2", "freq", "pct",
        "sfloat1", "sig3", "token", "bits6", "bits8", "bits9", "bits18",
    }


# ---------------------------------------------------------------------------------------------- values


def test_fader_encode_decode(d):
    fader = d.param("ch", "mix/fader")
    assert fader.to_raw(0.0) == pytest.approx(767 / 1023)  # research §2.3: 0.75·1023 rounds down
    assert fader.to_raw(3.0) == pytest.approx(844 / 1023)
    assert fader.to_raw(-85.4) == pytest.approx(10 / 1023) and fader.to_value(10 / 1023) == pytest.approx(-85.3, abs=0.05)  # console shows -85.3
    assert fader.to_value(0.75) == 0.0 and fader.to_value(0.5) == -10.0 and fader.to_value(1.0) == 10.0
    assert fader.to_value(0.0) == NEG_INF_DB and fader.to_raw(NEG_INF_DB) == 0.0 and fader.to_raw("-oo") == 0.0
    assert fader.to_raw(-90.0) == 0.0 and fader.to_raw(50) == 1.0  # scale range only
    assert fader.to_value(None) is None
    with pytest.raises(ScaleError):
        fader.to_raw(None)
    # policy clamp is NOT applied by to_raw; clamp() reports it
    assert fader.to_raw(8.0) == pytest.approx(972 / 1023) and fader.to_raw(8.0) > fader.to_raw(5.0)
    assert fader.clamp(8.0) == 5 and fader.clamp(-3.2) == -3.2 and fader.clamp(NEG_INF_DB) == NEG_INF_DB
    assert fader.clamp("-oo") == NEG_INF_DB and fader.clamp(-120) == NEG_INF_DB
    assert d.param("bus", "mix/fader").clamp(2.5) == 0 and d.param("dca", "fader").clamp(1) == 0
    assert d.param("main", "mix/fader").clamp(-6.0) == -6.0


def test_send_level_161_grid(d):
    send = d.param("ch", "mix/{send:02d}/level")
    assert send.scale.steps == 161
    assert send.to_raw(-8.3) == pytest.approx(87 / 160) and send.to_value(87 / 160) == pytest.approx(-8.25)
    assert send.to_raw(0.0) == 0.75 and send.to_value(0.75) == 0.0
    assert send.clamp(3.0) == 0 and d.param("ch", "mix/mlevel").clamp(1.0) == 0
    assert d.param("main", "mix/{send:02d}/level").scale is d.scales["send"]


def test_freq_encode_decode(d):
    f = d.param("ch", "eq/{band}/f")
    assert f.to_raw(1000) == pytest.approx(0.565) and f.to_value(0.565) == pytest.approx(990.9, abs=0.05)
    assert f.to_value(0.5) == pytest.approx(632.455, abs=0.001)
    assert f.to_value(0.0) == 20.0 and f.to_value(1.0) == pytest.approx(20000.0)
    assert f.to_raw(5) == 0.0 and f.to_raw(50000) == 1.0
    q = d.param("ch", "eq/{band}/q")
    assert q.to_value(0.0) == 10.0 and q.to_value(1.0) == pytest.approx(0.3)  # decreasing log
    hpf = d.param("ch", "preamp/hpf")
    assert hpf.to_value(0.0) == 20.0 and hpf.to_value(1.0) == pytest.approx(400.0) and hpf.node_fmt == "int"


def test_enum_encode_decode(d):
    color = d.param("ch", "config/color")
    assert color.to_raw("RD") == 1 and color.to_raw("rd") == 1 and color.to_raw(7) == 7
    assert color.to_value(1) == "RD" and color.to_value(15) == "WHi" and color.to_value("CY") == "CY"
    assert color.clamp("wh") == "WH"
    with pytest.raises(ScaleError):
        color.to_raw("PURPLE")
    with pytest.raises(ScaleError):
        color.to_value(16)
    eq_type = d.param("ch", "eq/{band}/type")
    assert eq_type.to_raw("PEQ") == 2 and eq_type.to_value(5) == "HCut"
    assert d.param("mtx", "eq/{band}/type").to_value(13) == "LR24"
    assert d.param("ch", "config/source").to_value(1) == "IN01" and d.param("ch", "config/source").to_raw("BUS16") == 64
    assert d.param("fx", "type").to_raw("GEQ2") == 27 and d.param("ch", "insert/sel").to_raw("FX8R") == 16
    assert d.param("ch", "dyn/ratio").to_raw("10") == 9 and d.param("ch", "dyn/ratio").to_raw(10) == 10  # int = index
    assert d.param("prefs", "rta/source").to_value(72) == "MAIN" and d.param("prefs", "rta/source").to_raw("BUS03") == 52


def test_bool_str_int_pan_encode_decode(d):
    on = d.param("ch", "mix/on")
    assert on.to_raw(True) == 1 and on.to_raw(False) == 0 and on.to_raw("ON") == 1 and on.to_raw("off") == 0
    assert on.to_value(1) is True and on.to_value(0) is False  # NOT mute-inverted here (Desk does that)
    assert on.inverted_mute is True
    name = d.param("ch", "config/name")
    assert name.to_raw("Vox Tony") == "Vox Tony" and name.to_value("Vox Tony") == "Vox Tony" and name.to_raw("") == ""
    icon = d.param("ch", "config/icon")
    assert icon.to_raw(5) == 5 and icon.to_value(5) == 5 and icon.clamp(0) == 1 and icon.clamp(200) == 74
    pan = d.param("ch", "mix/pan")
    assert pan.to_raw(0) == 0.5 and pan.to_raw(-100) == 0.0 and pan.to_value(1.0) == 100 and pan.to_value(0.5) == 0
    assert pan.to_raw(150) == 1.0 and pan.clamp(150) == 100
    g = d.param("ch", "eq/{band}/g")
    assert g.to_raw(3.25) == pytest.approx(73 / 120) and g.to_value(73 / 120) == 3.25
    assert g.clamp(20) == 15 and g.clamp(-20) == -15 and g.to_raw(20) == 1.0
    scene = d.param("action", "goscene")
    assert scene.to_raw(7) == 7 and scene.clamp(150) == 99 and scene.clamp(-1) == 0 and isinstance(scene.clamp(150), int)
    raw01 = d.param("fx", "par/01")
    assert raw01.to_raw(0.25) == 0.25 and raw01.to_value(0.5) == 0.5 and raw01.clamp(2) == 1.0
    delay = d.param("ch", "delay/time")
    assert delay.to_value(1.0) == pytest.approx(500.0) and math.isclose(delay.to_value(0.0), 0.3)


# ---------------------------------------------------------------------------------------------- validation


def _err(raw_mut: dict) -> DescriptorError:
    with pytest.raises(DescriptorError) as ei:
        Descriptor.from_dict(raw_mut)
    return ei.value


def test_error_unknown_scale(raw):
    m = mutated(raw)
    m["params"]["ch"]["mix/fader"]["scale"] = "nope"
    e = _err(m)
    assert e.path == "params.ch.mix/fader.scale" and "nope" in e.message and str(e).startswith("params.ch.mix/fader.scale: ")


def test_error_scale_and_enum_both(raw):
    m = mutated(raw)
    m["params"]["ch"]["mix/fader"]["enum"] = "color"
    assert _err(m).path == "params.ch.mix/fader"


def test_error_enum_with_float_osc(raw):
    m = mutated(raw)
    m["params"]["ch"]["config/color"]["osc"] = "f"
    assert _err(m).path == "params.ch.config/color.osc"


def test_error_unknown_enum(raw):
    m = mutated(raw)
    m["params"]["ch"]["config/color"]["enum"] = "colour"
    e = _err(m)
    assert e.path == "params.ch.config/color.enum" and "colour" in e.message


def test_error_bad_tier_and_osc(raw):
    m = mutated(raw)
    m["params"]["ch"]["mix/fader"]["tier"] = 3
    assert _err(m).path == "params.ch.mix/fader.tier"
    m = mutated(raw)
    m["params"]["ch"]["mix/fader"]["osc"] = "x"
    assert _err(m).path == "params.ch.mix/fader.osc"
    m = mutated(raw)
    m["params"]["ch"]["config/name"]["osc"] = "i"
    assert _err(m).path == "params.ch.config/name.osc"


def test_error_unknown_spec_key_and_bad_clamp(raw):
    m = mutated(raw)
    m["params"]["ch"]["mix/fader"]["bogus"] = 1
    assert _err(m).path == "params.ch.mix/fader"
    m = mutated(raw)
    m["params"]["ch"]["mix/fader"]["clamp"] = {"min": 6, "max": 5}
    assert _err(m).path == "params.ch.mix/fader.clamp"
    m = mutated(raw)
    m["params"]["ch"]["config/color"]["clamp"] = {"max": 5}
    assert _err(m).path == "params.ch.config/color.clamp"
    m = mutated(raw)
    m["params"]["ch"]["mix/fader"]["inverted_mute"] = True
    assert _err(m).path == "params.ch.mix/fader.inverted_mute"


def test_error_bad_template_var(raw):
    m = mutated(raw)
    m["params"]["ch"]["foo/{x}"] = {"osc": "i", "scale": "int", "node": "int", "tier": 1}
    e = _err(m)
    assert e.path == "params.ch.foo/{x}" and "x" in e.message


def test_error_param_family_without_root(raw):
    m = mutated(raw)
    m["params"]["zzz"] = {"a": {"osc": "i", "scale": "int", "node": "int", "tier": 1}}
    assert _err(m).path == "params.zzz"


def test_error_node_field_not_a_param(raw):
    m = mutated(raw)
    m["nodes"][0]["fields"].append("config/nope")
    e = _err(m)
    assert e.path == "nodes[0].fields[4]" and "config/nope" in e.message


def test_error_node_section_shape(raw):
    m = mutated(raw)
    i = len(m["nodes"])
    m["nodes"].append({"family": "headamp", "section": "x", "fields": ["gain"]})
    assert _err(m).path == f"nodes[{i}].section"
    m = mutated(raw)
    m["nodes"].append({"family": "ch", "section": "eq/{band}", "fields": ["eq/{band}/f"]})  # no `for`
    assert _err(m).path == f"nodes[{i}].section"
    m = mutated(raw)
    m["nodes"].append({"family": "ch", "section": "eq", "path": "/x", "fields": ["eq/on"]})
    assert _err(m).path == f"nodes[{i}]"
    m = mutated(raw)
    m["nodes"].append({"family": "nope", "section": "eq", "fields": ["eq/on"]})
    assert _err(m).path == f"nodes[{i}].family"
    m = mutated(raw)
    m["nodes"].append({"family": "ch", "section": "eq", "fields": ["eq/on"], "for": {"band": [4, 1]}})
    assert _err(m).path == f"nodes[{i}].for.band"
    m = mutated(raw)
    m["nodes"].append({"family": "ch", "section": "eq", "fields": ["eq/on"], "for": {"id": [1, 2]}})
    assert _err(m).path == f"nodes[{i}].for.id"
    m = mutated(raw)
    m["nodes"].append({"family": "ch", "section": "eq", "fields": ["eq/on"], "fields_even": ["eq/on"]})
    assert _err(m).path == f"nodes[{i}].fields_even"
    m = mutated(raw)
    m["nodes"].append({"family": "ch", "section": "eq", "fields": []})
    assert _err(m).path == f"nodes[{i}].fields"
    m = mutated(raw)
    m["nodes"].append({"family": "headamp", "path": "/other/{n:03d}", "for": {"n": [0, 1]}, "fields": ["gain"]})
    assert _err(m).path == f"nodes[{i}]"  # not under the family root


def test_error_fields_even_needs_single_for_var(raw):
    m = mutated(raw)
    sends = next(s for s in m["nodes"] if s["family"] == "ch" and s.get("section") == "mix/{send:02d}")
    sends["for"]["band"] = [1, 4]
    e = _err(m)
    assert e.path.endswith(".fields_even")


def test_error_duplicate_node_path(raw):
    m = mutated(raw)
    i = len(m["nodes"])
    m["nodes"].append(copy.deepcopy(m["nodes"][0]))
    e = _err(m)
    assert e.path == f"nodes[{i}]" and "/ch/01/config" in e.message and "nodes[0]" in e.message


def test_error_top_level_keys(raw):
    m = mutated(raw)
    del m["mics"]
    e = _err(m)
    assert e.path == "<root>" and "mics" in e.message
    m = mutated(raw)
    m["extra"] = {}
    assert _err(m).path == "<root>"
    with pytest.raises(DescriptorError):
        Descriptor.from_dict([])  # type: ignore[arg-type]


def test_error_strips_must_match_targets(raw):
    m = mutated(raw)
    m["strips"]["ch"]["count"] = 33
    assert _err(m).path == "strips.ch.count"
    m = mutated(raw)
    m["strips"]["ch"]["path"] = "/ch/{n}"
    assert _err(m).path == "strips.ch.path"
    m = mutated(raw)
    del m["strips"]["dca"]
    del m["params"]["dca"]
    m["nodes"] = [s for s in m["nodes"] if s["family"] != "dca"]
    assert _err(m).path == "strips"
    m = mutated(raw)
    m["strips"]["bus"]["send_target"] = "matrix"
    assert _err(m).path == "strips.bus.send_target"
    m = mutated(raw)
    m["strips"]["ch"]["gate"] = "yes"
    assert _err(m).path == "strips.ch.gate"


def test_error_guarded_and_enums_and_scales(raw):
    m = mutated(raw)
    m["guarded"][0] = "main/st/mix/*"
    assert _err(m).path == "guarded[0]"
    m = mutated(raw)
    m["enums"]["color"].append("RD")
    assert _err(m).path == "enums.color"
    m = mutated(raw)
    m["enums"]["onoff"] = ["OFF", True]
    assert _err(m).path == "enums.onoff[1]"
    m = mutated(raw)
    m["scales"]["freq"]["lo"] = 0
    assert _err(m).path == "scales.freq"
    m = mutated(raw)
    m["scales"]["freq"]["kind"] = "exp"
    assert _err(m).path == "scales.freq"
    m = mutated(raw)
    m["meta"]["roots"]["ch"] = "/ch/{n:02d}"
    assert _err(m).path == "meta.roots.ch"
    m = mutated(raw)
    m["meta"]["roots"]["show"] = "-show"
    assert _err(m).path == "meta.roots.show"


def test_error_tables(raw):
    m = mutated(raw)
    m["rta"]["source_param"] = "/-prefs/rta/nope"
    assert _err(m).path == "rta.source_param"
    m = mutated(raw)
    m["rta"]["band_hz"] = m["rta"]["band_hz"][:99]
    assert _err(m).path == "rta.band_hz"
    m = mutated(raw)
    m["geq"]["gain_scale"] = "nope"
    assert _err(m).path == "geq.gain_scale"
    m = mutated(raw)
    m["geq"]["insert_sel_enum"] = "nope"
    assert _err(m).path == "geq.insert_sel_enum"
    m = mutated(raw)
    del m["policy"]["ramp_step_ms"]
    assert _err(m).path == "policy.ramp_step_ms"


def test_load_errors(tmp_path):
    with pytest.raises(DescriptorError):
        Descriptor.load(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("meta: [unclosed", encoding="utf-8")
    with pytest.raises(DescriptorError):
        Descriptor.load(bad)
    bad.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(DescriptorError):
        Descriptor.load(bad)


def test_from_dict_round_trip_matches_load(raw, d):
    d2 = Descriptor.from_dict(raw)
    assert d2.path is None and d2.all_node_paths() == d.all_node_paths()
    assert [s.key for s in d2.iter_params()] == [s.key for s in d.iter_params()]
    assert d2.param_for_address("/ch/05/mix/03/level")[0] == d.param_for_address("/ch/05/mix/03/level")[0]
