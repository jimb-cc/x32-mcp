"""patches.py: YAML/CSV loading and equivalence, value normalisation, validation errors, apply
(against a recording stub desk) and export round trips (DESIGN.md §16)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from x32mcp.desk import DeskError
from x32mcp.descriptor import Descriptor
from x32mcp.nodes import DeskState
from x32mcp.patches import (
    COLUMNS,
    PatchError,
    PatchPlan,
    PatchRow,
    apply_patch_plan,
    build_patch_plan,
    dumps_patch_plan,
    export_patch_plan,
    load_patch_plan,
    normalise_color,
    normalise_source,
    parse_mic,
    write_patch_plan,
)
from x32mcp.targets import Target

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "patches" / "example_band.yaml"


@pytest.fixture(scope="module")
def descriptor() -> Descriptor:
    return Descriptor.load()


class StubDesk:
    """The three Desk methods patches.py uses: ``dump(sections=)``, ``label`` and ``set_source``.
    ``config`` is channel → {name, color, source}; ``calls`` records every write."""

    def __init__(self, config: dict[int, dict] | None = None) -> None:
        self.config: dict[int, dict] = config or {}
        self.calls: list[tuple] = []
        self.fail: dict[int, DeskError] = {}
        self.missing: set[int] = set()
        self.dumps = 0

    def _cfg(self, n: int) -> dict:
        return self.config.setdefault(n, {"name": f"Ch{n:02d}", "color": "OFF", "source": f"IN{n:02d}"})

    async def dump(self, *, sections=None):
        self.dumps += 1
        secs: dict[str, dict] = {}
        missing: list[str] = []
        for p in sections:
            n = int(p.split("/")[2])
            if n in self.missing:
                missing.append(p)
                continue
            c = self._cfg(n)
            secs[p] = {"config/name": c["name"], "config/icon": 1, "config/color": c["color"], "config/source": c["source"]}
        st = DeskState(created="", console={}, sections=secs)
        st.missing = missing
        return st

    async def label(self, t: Target, *, name=None, color=None, icon=None):
        if t.index in self.fail:
            raise self.fail[t.index]
        applied: dict = {}
        truncated = False
        if name is not None:
            truncated = len(name) > 12
            applied["name"] = self._cfg(t.index)["name"] = name[:12]
        if color is not None:
            applied["color"] = self._cfg(t.index)["color"] = color
        self.calls.append(("label", t.key, dict(applied)))
        out = {"target": t.key, "label": t.label, "applied": applied}
        if truncated:
            out["truncated"] = True
        return out

    async def set_source(self, t: Target, source: str):
        if t.index in self.fail:
            raise self.fail[t.index]
        self._cfg(t.index)["source"] = source
        self.calls.append(("set_source", t.key, source))
        return {"target": t.key, "label": t.label, "source": source}


# ---------------------------------------------------------------------------------- example file


def test_example_file_is_a_plausible_16_channel_patch(descriptor):
    plan = load_patch_plan(EXAMPLE, descriptor=descriptor)
    assert plan.band == "The Molecules" and plan.venue and plan.source_file == EXAMPLE
    assert [r.channel for r in plan.rows] == list(range(1, 17))
    assert plan.warnings == []
    colours = descriptor.enum("color")
    sources = descriptor.enum("ch_source")
    for r in plan.rows:
        assert r.color in colours and r.source in sources
        assert 1 <= len(r.name) <= 12
        assert r.mic in (True, False)
        assert r.monitor_bus is None or 1 <= r.monitor_bus <= 6
        assert r.owner is None or (" " not in r.owner and r.owner.istitle())  # first names only
    assert plan.row(1).name == "Kick" and plan.row(1).source == "IN01" and plan.row(1).mic is True
    assert plan.row(13).name == "Vox Lead" and plan.row(13).owner == "Tony" and plan.row(13).monitor_bus == 1
    assert plan.row(16).name == "Talkback" and plan.row(16).mic is False and plan.row(16).color == "OFF"
    assert {r.channel for r in plan.mics()} == {1, 2, 3, 4, 5, 6, 7, 9, 10, 13, 14, 15}
    assert [r.owner for r in plan.for_bus(6)] == ["Jo", "Sam"]
    assert plan.to_dict()["channels"][0]["name"] == "Kick"


# ------------------------------------------------------------------------------ yaml / csv forms


def test_yaml_and_csv_forms_load_identically(tmp_path, descriptor):
    plan = load_patch_plan(EXAMPLE, descriptor=descriptor)
    y = write_patch_plan(plan, tmp_path / "a.yaml")
    c = write_patch_plan(plan, tmp_path / "a.csv")
    from_yaml = load_patch_plan(y, descriptor=descriptor)
    from_csv = load_patch_plan(c, descriptor=descriptor)
    assert from_yaml.rows == plan.rows == from_csv.rows
    assert from_yaml.band == "The Molecules" and from_csv.band is None  # CSV has no header block
    assert c.read_text(encoding="utf-8").splitlines()[0] == ",".join(COLUMNS)
    # the YAML writer emits the documented key order and omits unknown/blank fields
    doc = yaml.safe_load(y.read_text(encoding="utf-8"))
    assert list(doc) == ["band", "venue", "channels"]
    assert list(doc["channels"][0]) == ["channel", "name", "color", "source", "mic", "owner", "monitor_bus", "notes"]
    assert "owner" not in doc["channels"][15]  # talkback has no owner


def test_hand_written_csv_with_aliases_and_mic_spellings(tmp_path, descriptor):
    text = (
        "﻿Channel,Name,Colour,Source,Mic,Owner,Monitor Bus,Notes,Extra\n"
        "1,Kick,red,in 1,yes,Ray,2,inside,ignored\n"
        "2,Snare Top,RD,In02,x,Ray,bus 2,,\n"
        "3,Hats,yellow inverted,,no,,,\n"
        "4,Bass DI,gn,aux 2,0,Dee,3,\n"
        "5,Keys L,,USB L,,Kit,,\n"
        ",,,,,,,\n"
        "6,Vox,MAGENTA,fx1l,1,Tony,1,centre\n"
    )
    p = tmp_path / "hand.csv"
    p.write_text(text, encoding="utf-8")
    plan = load_patch_plan(p, descriptor=descriptor)
    assert plan.warnings == ["ignoring unknown CSV column 'Extra'"]
    assert plan.rows == [
        PatchRow(1, "Kick", "RD", "IN01", True, "Ray", 2, "inside"),
        PatchRow(2, "Snare Top", "RD", "IN02", True, "Ray", 2, None),
        PatchRow(3, "Hats", "YEi", None, False, None, None, None),
        PatchRow(4, "Bass DI", "GN", "AUX2", False, "Dee", 3, None),
        PatchRow(5, "Keys L", None, "USBL", None, "Kit", None, None),
        PatchRow(6, "Vox", "MG", "FX1L", True, "Tony", 1, "centre"),
    ]


def test_yaml_bare_list_and_unknown_top_level_key(tmp_path, descriptor):
    p = tmp_path / "bare.yml"
    p.write_text("- {channel: 2, name: Snare}\n- {channel: 1, name: Kick, color: 'OFF'}\n", encoding="utf-8")
    plan = load_patch_plan(p, descriptor=descriptor)
    assert [r.channel for r in plan.rows] == [1, 2] and plan.band is None  # sorted by channel
    assert plan.rows[0].color == "OFF"
    q = tmp_path / "extra.yaml"
    q.write_text("band: X\ndate: today\nchannels:\n  - {channel: 1, name: Kick}\n", encoding="utf-8")
    plan = load_patch_plan(q, descriptor=descriptor)
    assert plan.warnings == ["ignoring unknown top-level key 'date'"] and plan.band == "X"


def test_unquoted_off_in_yaml_is_the_off_token(tmp_path, descriptor):
    # YAML 1.1 turns OFF/off/no into False; the loader reads that as the OFF colour / source
    p = tmp_path / "off.yaml"
    p.write_text("channels:\n  - {channel: 16, name: Talkback, color: OFF, source: off, mic: off}\n", encoding="utf-8")
    r = load_patch_plan(p, descriptor=descriptor).rows[0]
    assert r.color == "OFF" and r.source == "OFF" and r.mic is False


# ------------------------------------------------------------------------------ normalisation


@pytest.mark.parametrize(
    "value,token",
    [
        ("RD", "RD"), ("rd", "RD"), ("Rdi", "RDi"), ("red", "RD"), ("Red", "RD"), ("green", "GN"), ("yellow", "YE"),
        ("blue", "BL"), ("magenta", "MG"), ("cyan", "CY"), ("white", "WH"), ("off", "OFF"), ("OFF", "OFF"), ("none", "OFF"),
        ("redi", "RDi"), ("red inverted", "RDi"), ("cyan-inv", "CYi"), ("inverted white", "WHi"), ("offi", "OFFi"),
        (0, "OFF"), (3, "YE"), (15, "WHi"), (False, "OFF"), ("", None), ("  ", None), (None, None),
    ],
)
def test_colour_normalisation(value, token, descriptor):
    assert normalise_color(value, descriptor=descriptor) == token


@pytest.mark.parametrize("bad", ["pink", "RDX", 16, -1, True, "red red"])
def test_colour_rejects(bad, descriptor):
    with pytest.raises(PatchError):
        normalise_color(bad, descriptor=descriptor)


@pytest.mark.parametrize(
    "value,token",
    [
        ("In01", "IN01"), ("in1", "IN01"), ("IN 12", "IN12"), ("input 32", "IN32"), ("ch 7", "IN07"), ("Aux1", "AUX1"), ("aux 6", "AUX6"),
        ("USBL", "USBL"), ("usb r", "USBR"), ("fx1l", "FX1L"), ("FX 4 R", "FX4R"), ("bus 3", "BUS03"), ("Bus16", "BUS16"), ("mix 1", "BUS01"),
        ("OFF", "OFF"), ("off", "OFF"), (False, "OFF"), (0, "OFF"), (5, "IN05"), ("33", "AUX1"), (64, "BUS16"), ("", None), (None, None),
    ],
)
def test_source_normalisation(value, token, descriptor):
    assert normalise_source(value, descriptor=descriptor) == token


@pytest.mark.parametrize("bad", ["In33", "aux 7", "usb", "fx5l", "bus 17", "card 1", 65, True, "IN"])
def test_source_rejects(bad, descriptor):
    with pytest.raises(PatchError):
        normalise_source(bad, descriptor=descriptor)


@pytest.mark.parametrize(
    "value,expected",
    [(True, True), (False, False), ("true", True), ("YES", True), ("y", True), ("1", True), ("x", True), ("on", True), ("t", True),
     ("false", False), ("No", False), ("n", False), ("0", False), ("off", False), ("f", False), (1, True), (0, False), ("", None), (None, None)],
)
def test_mic_parsing(value, expected):
    assert parse_mic(value) is expected


@pytest.mark.parametrize("bad", ["maybe", 2, "yess"])
def test_mic_rejects(bad):
    with pytest.raises(PatchError):
        parse_mic(bad)


# ------------------------------------------------------------------------------- validation


def test_validation_collects_every_problem(tmp_path, descriptor):
    p = tmp_path / "bad.yaml"
    p.write_text(
        "channels:\n"
        "  - {channel: 1, name: Kick, color: pink}\n"
        "  - {channel: 1, name: Dup}\n"
        "  - {channel: 33, name: TooHigh}\n"
        "  - {channel: 0, name: TooLow}\n"
        "  - {channel: abc, name: NotANumber}\n"
        "  - {channel: 2, name: Bus, monitor_bus: 17}\n"
        "  - {channel: 3, name: Mic, mic: maybe}\n"
        "  - {channel: 4, name: Src, source: 'card 1'}\n"
        "  - {channel: 5, nmae: Typo}\n"
        "  - {channel: 6, name: Ok, colour: red, monitor_bus: 0}\n"
        "  - just a string\n",
        encoding="utf-8",
    )
    with pytest.raises(PatchError) as ei:
        load_patch_plan(p, descriptor=descriptor)
    err = ei.value
    assert err.path == p
    text = "\n".join(err.problems)
    assert "channel 1 appears more than once" in text
    assert "unknown colour 'pink'" in text
    assert "channel 33 is out of range 1..32" in text and "channel 0 is out of range" in text
    assert "channel must be a number" in text
    assert "monitor_bus 17 is out of range 1..16" in text and "monitor_bus 0 is out of range" in text
    assert "mic must be true/false" in text
    assert "unknown source 'card 1'" in text
    assert "unknown field 'nmae'" in text and "name: is required" in text
    assert "row 11: expected a mapping" in text
    assert len(err.problems) >= 11
    assert str(err).startswith(str(p))


def test_top_level_errors(tmp_path, descriptor):
    (tmp_path / "scalar.yaml").write_text("just text\n", encoding="utf-8")
    (tmp_path / "nolist.yaml").write_text("channels: {channel: 1}\n", encoding="utf-8")
    (tmp_path / "empty.yaml").write_text("band: X\nchannels: []\n", encoding="utf-8")
    (tmp_path / "broken.yaml").write_text("channels: [\n", encoding="utf-8")
    (tmp_path / "plan.txt").write_text("channel,name\n1,Kick\n", encoding="utf-8")
    (tmp_path / "noheader.csv").write_text("name,color\nKick,RD\n", encoding="utf-8")
    (tmp_path / "empty.csv").write_text("", encoding="utf-8")
    for f, needle in [
        ("scalar.yaml", "top level must be a mapping"),
        ("nolist.yaml", "'channels' must be a list"),
        ("empty.yaml", "no channels in the plan"),
        ("broken.yaml", "invalid YAML"),
        ("plan.txt", "unsupported patch file type '.txt'"),
        ("noheader.csv", "CSV header must include 'channel' and 'name'"),
        ("empty.csv", "empty CSV"),
    ]:
        with pytest.raises(PatchError, match=needle):
            load_patch_plan(tmp_path / f, descriptor=descriptor)
    with pytest.raises(PatchError, match="missing 'channels'"):
        (tmp_path / "nochannels.yaml").write_text("band: X\n", encoding="utf-8")
        load_patch_plan(tmp_path / "nochannels.yaml", descriptor=descriptor)
    with pytest.raises(OSError):
        load_patch_plan(tmp_path / "missing.yaml", descriptor=descriptor)


def test_long_name_is_a_warning_not_an_error(descriptor):
    plan = build_patch_plan([{"channel": 1, "name": "Lead Vocal Tony SM58"}], descriptor=descriptor)
    assert plan.rows[0].name == "Lead Vocal Tony SM58"
    assert plan.warnings == ["row 1 (channel 1): name 'Lead Vocal Tony SM58' is longer than 12 characters and will be truncated to 'Lead Vocal T'"]


def test_double_quote_in_a_name_is_replaced_and_warned(descriptor):
    """transport.md §6.3: node text has no escape for a quote, and the desk's parser (§6.6) reads
    to the closing one — a quoted name would eat the rest of the config line on the desk."""
    plan = build_patch_plan([{"channel": 1, "name": 'Vox "T"'}], descriptor=descriptor)
    assert plan.rows[0].name == "Vox 'T'"
    assert plan.warnings and "double quote" in plan.warnings[0]
    assert '"' not in dumps_patch_plan(plan, "csv").splitlines()[1]


def test_write_patch_plan_is_atomic(tmp_path, descriptor, monkeypatch):
    """A failed export must not destroy the operator's only copy of the hand-authored columns."""
    import x32mcp.patches as patches

    p = tmp_path / "band.yaml"
    plan = build_patch_plan([{"channel": 1, "name": "Kick", "owner": "Ray", "notes": "beta 91"}], descriptor=descriptor)
    write_patch_plan(plan, p)
    before = p.read_text(encoding="utf-8")
    monkeypatch.setattr(patches, "dumps_patch_plan", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("disk full")))
    with pytest.raises(RuntimeError):
        write_patch_plan(build_patch_plan([{"channel": 2, "name": "Snare"}], descriptor=descriptor), p)
    assert p.read_text(encoding="utf-8") == before  # untouched
    monkeypatch.undo()
    write_patch_plan(build_patch_plan([{"channel": 2, "name": "Snare"}], descriptor=descriptor), p)
    assert load_patch_plan(p, descriptor=descriptor).rows[0].name == "Snare"
    assert not list(tmp_path.glob("*.tmp"))


def test_build_from_rows_and_numeric_name(descriptor):
    plan = build_patch_plan([PatchRow(2, "B"), {"channel": " ch 1 ", "name": 808, "mic": "x"}], band=" Band ", descriptor=descriptor)
    assert [(r.channel, r.name) for r in plan.rows] == [(1, "808"), (2, "B")] and plan.band == "Band"
    assert plan.rows[0].mic is True and plan.rows[0].target == Target("ch", 1)
    with pytest.raises(PatchError, match="unsupported format"):
        dumps_patch_plan(plan, "json")


# ------------------------------------------------------------------------------------ apply


async def test_apply_labels_only_what_differs(descriptor):
    plan = load_patch_plan(EXAMPLE, descriptor=descriptor)
    desk = StubDesk({1: {"name": "Kick", "color": "RD", "source": "IN01"}, 2: {"name": "Snare Top", "color": "GN", "source": "IN02"}})
    res = await apply_patch_plan(desk, plan)
    assert res["file"] == str(EXAMPLE) and res["band"] == "The Molecules" and res["rows"] == 16
    assert res["include_source"] is False and res["aborted"] is False and res["failed"] == []
    assert res["unchanged"] == [1]  # already labelled exactly like the plan
    assert [i["channel"] for i in res["applied"]] == list(range(2, 17))
    snare = res["applied"][0]
    assert snare == {"channel": 2, "target": "ch.2", "name": "Snare Top", "color": "RD", "changed": ["color"]}
    assert ("label", "ch.2", {"color": "RD"}) in desk.calls  # the name matched, only the colour was written
    assert ("label", "ch.3", {"name": "Hats", "color": "RD"}) in desk.calls
    assert not [c for c in desk.calls if c[0] == "set_source"]
    # ch.2 colour only; ch.3-15 name + colour; ch.16 name only (the stub's default colour is already OFF)
    assert res["skipped_source"] == list(range(1, 17)) and res["writes"] == 1 + 13 * 2 + 1
    assert res["applied"][-1]["changed"] == ["name"]
    assert "15 channel(s) labelled" in res["summary"] and "16 source(s) not patched" in res["summary"]
    for r in plan.rows:
        assert desk.config[r.channel]["name"] == r.name and desk.config[r.channel]["color"] == r.color
    # a second apply is a no-op
    again = await apply_patch_plan(desk, plan)
    assert again["applied"] == [] and again["unchanged"] == list(range(1, 17)) and again["writes"] == 0


async def test_apply_with_sources_and_channel_filter(descriptor):
    plan = load_patch_plan(EXAMPLE, descriptor=descriptor)
    desk = StubDesk({16: {"name": "Talkback", "color": "OFF", "source": "AUX1"}})
    res = await apply_patch_plan(desk, plan, include_source=True, channels=[16, 1])
    assert res["rows"] == 2 and res["skipped_source"] == []
    assert [i["channel"] for i in res["applied"]] == [1, 16]
    assert res["applied"][1] == {"channel": 16, "target": "ch.16", "name": "Talkback", "color": "OFF", "changed": ["source"], "source": "IN16"}
    # ch.1's stub source is already IN01, so only ch.16's source is written
    assert desk.calls == [("label", "ch.1", {"name": "Kick", "color": "RD"}), ("set_source", "ch.16", "IN16")]
    assert res["applied"][0]["changed"] == ["name", "color"]
    assert "1 source(s) patched" in res["summary"]


async def test_apply_truncation_failures_and_abort(descriptor):
    plan = build_patch_plan(
        [{"channel": 1, "name": "Lead Vocal Tony SM58"}, {"channel": 2, "name": "Bad"}, {"channel": 3, "name": "Gone"}, {"channel": 4, "name": "Never"}],
        descriptor=descriptor,
    )
    desk = StubDesk()
    desk.fail[2] = DeskError("BAD_ARGUMENT", "nope")
    res = await apply_patch_plan(desk, plan)
    assert res["applied"][0]["truncated"] is True and res["truncated"] == [1] and desk.config[1]["name"] == "Lead Vocal T"
    assert res["failed"] == [{"channel": 2, "target": "ch.2", "code": "BAD_ARGUMENT", "message": "nope"}]
    assert [i["channel"] for i in res["applied"]] == [1, 3, 4] and res["aborted"] is False  # continues past a bad row
    assert "1 name(s) truncated" in res["summary"] and "1 failed" in res["summary"]
    desk = StubDesk()
    desk.fail[3] = DeskError("NOT_CONNECTED", "desk not responding")
    res = await apply_patch_plan(desk, plan)
    assert res["aborted"] is True and [i["channel"] for i in res["applied"]] == [1, 2]
    assert res["failed"][0]["code"] == "NOT_CONNECTED" and "ABORTED" in res["summary"]
    # a config section that did not answer is written unconditionally
    desk = StubDesk({1: {"name": "Lead Vocal T", "color": "OFF", "source": "IN01"}})
    desk.missing.add(1)
    res = await apply_patch_plan(desk, plan, channels=[1])
    assert res["applied"][0]["changed"] == ["name"] and desk.calls == [("label", "ch.1", {"name": "Lead Vocal T"})]


async def test_apply_with_no_matching_rows_reads_nothing(descriptor):
    plan = load_patch_plan(EXAMPLE, descriptor=descriptor)
    desk = StubDesk()
    res = await apply_patch_plan(desk, plan, channels=[32])
    assert res["rows"] == 0 and res["applied"] == [] and desk.dumps == 0


# ----------------------------------------------------------------------------------- export


async def test_export_round_trips_with_merge(tmp_path, descriptor):
    plan = load_patch_plan(EXAMPLE, descriptor=descriptor)
    desk = StubDesk()
    await apply_patch_plan(desk, plan, include_source=True)
    out = await export_patch_plan(desk, tmp_path / "out" / "export.yaml", merge_with=plan, channels=range(1, 17))
    assert out.source_file == tmp_path / "out" / "export.yaml" and out.band == "The Molecules" and out.venue == plan.venue
    assert out.rows == plan.rows
    assert load_patch_plan(out.source_file, descriptor=descriptor).rows == plan.rows
    csv_plan = await export_patch_plan(desk, tmp_path / "export.csv", merge_with=plan, channels=[1, 13])
    assert [r.channel for r in csv_plan.rows] == [1, 13] and csv_plan.rows[1] == plan.row(13)
    assert load_patch_plan(tmp_path / "export.csv", descriptor=descriptor).rows == csv_plan.rows


async def test_export_defaults_to_all_32_without_metadata(tmp_path, descriptor):
    desk = StubDesk({5: {"name": "Vox Tony", "color": "MGi", "source": "AUX2"}})
    plan = await export_patch_plan(desk, tmp_path / "all.yaml", band="Tonight")
    assert len(plan.rows) == 32 and plan.band == "Tonight" and plan.venue is None
    assert plan.row(5) == PatchRow(5, "Vox Tony", "MGi", "AUX2", None, None, None, None)
    assert plan.row(20) == PatchRow(20, "Ch20", "OFF", "IN20", None, None, None, None)
    assert load_patch_plan(tmp_path / "all.yaml", descriptor=descriptor).rows == plan.rows


async def test_export_errors(tmp_path, descriptor):
    desk = StubDesk()
    with pytest.raises(PatchError, match="unsupported patch file type"):
        await export_patch_plan(desk, tmp_path / "plan.json")
    with pytest.raises(PatchError, match="out of range"):
        await export_patch_plan(desk, tmp_path / "plan.yaml", channels=[40])
    desk.missing.add(7)
    with pytest.raises(DeskError) as ei:
        await export_patch_plan(desk, tmp_path / "plan.yaml", channels=[6, 7])
    assert ei.value.code == "TIMEOUT" and ei.value.details == {"channels": [7]}
    assert not (tmp_path / "plan.yaml").exists()
