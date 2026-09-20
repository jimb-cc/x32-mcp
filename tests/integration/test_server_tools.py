"""server.py tools against the FakeDesk (DESIGN.md §19, §20): envelope shape, units, the Tier-2
confirmation dance (token round trip, wrong/reused token, show mode), scenes, snapshots, patch
plans, CFS² tools with an injected synthetic RTA, dashboard status, and an end-to-end stdio
smoke test that spawns ``python -m x32mcp.server`` and talks MCP to it.

The tools are plain async functions reading the module-level ``x32mcp.server.app``; ``make_app``
builds an :class:`~x32mcp.server.App` on the fake desk with tmp_path data directories and the
short integration connection timeouts, then connects through the ``connect`` tool itself.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import sys
from pathlib import Path

import pytest
import pytest_asyncio

from conftest import CONN_OPTS, wait_until
from x32mcp import server as srv
from x32mcp.config import Settings
from x32mcp.meters import SyntheticRta, rta_band_hz
from x32mcp.patches import load_patch_plan
from x32mcp.server import App

REPO = Path(__file__).resolve().parent.parent.parent
EXAMPLE = REPO / "patches" / "example_band.yaml"
GEQ_BAND_2K5 = 22  # 1-based GEQ band at 2.5 kHz (fx_routing_scenes.md §2.3)


# ---------------------------------------------------------------------------------------- helpers


def make_settings(tmp_path: Path) -> Settings:
    return dataclasses.replace(
        Settings.from_env(),
        snapshot_dir=tmp_path / "snapshots", report_dir=tmp_path / "ringout_reports", patch_dir=tmp_path / "patches",
        dash_enabled=False, x32_host=None,
    )


async def make_app(fakedesk, tmp_path: Path, *, frames=None, connect: bool = True) -> App:
    """An App on the fake desk (tmp_path data dirs, no dashboard), published as ``server.app``."""
    a = App(make_settings(tmp_path), conn_opts=CONN_OPTS, frames=frames)
    srv.app = a
    if connect:
        res = await srv.connect(fakedesk.host, fakedesk.port)
        assert res["ok"], res
    return a


@pytest_asyncio.fixture
async def app(fakedesk, tmp_path):
    a = await make_app(fakedesk, tmp_path)
    try:
        yield a
    finally:
        await a.close()
        srv.app = None


async def settle(a: App) -> None:
    """SETs are fire-and-forget: one GET round trip guarantees the fake desk processed them."""
    await a.conn.get("/-stat/selidx")


def fset(a: App, fakedesk, address: str, value) -> None:
    """A front-panel change on the fake; the desk cache is dropped so the next read sees it."""
    fakedesk.set_value(address, value)
    a.desk.invalidate()


def assert_err(res: dict, code: str) -> None:
    assert res["ok"] is False and res["error"]["code"] == code, res
    assert isinstance(res["summary"], str) and res["summary"]


def assert_pending(res: dict) -> str:
    assert res["ok"] is False and res["requires_confirmation"] is True, res
    assert res["confirm_token"] and res["expires_in_s"] == 60 and res["action_summary"], res
    assert "Confirmation required" in res["summary"]
    return res["confirm_token"]


# ---------------------------------------------------------------------------------------- connection


async def test_connection_status_before_and_after_connect(fakedesk, tmp_path):
    a = await make_app(fakedesk, tmp_path, connect=False)
    try:
        st = await srv.connection_status()
        assert_err(st, "NOT_CONNECTED")
        assert st["state"] == "disconnected" and st["console"] is None and st["cfs_mode"] == "idle"
        assert_err(await srv.get_channel(1), "NOT_CONNECTED")
        assert_err(await srv.panic(), "NOT_CONNECTED")
        assert_err(await srv.set_fader("ch.1", -3.0), "NOT_CONNECTED")
        json.dumps(st)  # envelopes are always JSON-safe
        bad = await srv.connect("127.0.0.1", 1)  # nothing listens there: fails within the short timeouts
        assert_err(bad, "CONNECT_FAILED")
        res = await srv.connect(fakedesk.host, fakedesk.port)
        assert res["ok"] and res["state"] == "connected" and res["console"]["name"] == "X32-FAKE"
        assert res["summary"].startswith("Connected to X32-FAKE (X32RACK FW 4.06")
        st = await srv.connection_status()
        assert st["ok"] and st["state"] == "connected" and st["show_mode"] is False and st["dashboard"]["enabled"] is False
        assert "Connected to X32-FAKE" in st["summary"] and st["version"] == "0.1.0"
        again = await srv.connect(fakedesk.host, fakedesk.port)  # reconnect replaces the connection
        assert again["ok"]
        d = await srv.disconnect()
        assert d["ok"] and d["state"] == "disconnected" and "Disconnected from X32-FAKE" in d["summary"]
        assert_err(await srv.connection_status(), "NOT_CONNECTED")
    finally:
        await a.close()
        srv.app = None


async def test_discover_consoles_finds_the_fake(app, fakedesk):
    res = await srv.discover_consoles(timeout_s=0.5, port=fakedesk.port)  # the fake listens on an ephemeral port
    assert res["ok"] and res["timeout_s"] == 0.5
    assert any(c["port"] == fakedesk.port and c["name"] == "X32-FAKE" for c in res["consoles"]), res
    assert "X32-FAKE" in res["summary"]


# ---------------------------------------------------------------------------------------- reads


async def test_read_tools_envelope_and_units(app):
    ch = await srv.get_channel(1)
    assert ch["ok"] and ch["target"] == "ch.1" and ch["label"] == "Ch 1" and ch["name"] == "Ch01"
    assert ch["fader_db"] == 0.0 and ch["fader"] == "0.0" and ch["muted"] is False and ch["source"] == "IN01"
    assert ch["mono_level_db"] is None and ch["mono_level"] == "-oo"
    assert ch["eq"]["bands"][0]["type"] == "PEQ" and ch["comp"]["ratio"] == "3.0" and ch["gate"]["threshold_db"] == -80.0
    assert ch["summary"].startswith("Ch 1 'Ch01': fader 0.0 dB, unmuted, pan C, source IN01")
    json.dumps(ch)
    bus = await srv.get_bus(3)
    assert bus["ok"] and bus["name"] == "Bus03" and len(bus["eq"]["bands"]) == 6 and bus["gate"] is None
    main = await srv.get_main()
    assert main["ok"] and main["st"]["name"] == "Main" and main["m"]["label"] == "Main M/C" and "Main LR 'Main'" in main["summary"]
    strip = await srv.get_strip("dca.2")
    assert strip["ok"] and strip["target"] == "dca.2" and strip["fader_db"] == 0.0
    assert_err(await srv.get_strip("nobody"), "UNKNOWN_TARGET")
    assert_err(await srv.get_channel(33), "BAD_ARGUMENT")
    assert_err(await srv.get_channel(0), "BAD_ARGUMENT")
    sends = await srv.get_channel_sends(1)
    assert sends["ok"] and sends["target"] == "ch.1" and len(sends["sends"]) == 16
    assert sends["sends"][2]["bus"] == 3 and sends["sends"][2]["level"] == "-oo" and sends["sends"][2]["level_db"] is None
    assert sends["summary"] == "Ch 1 'Ch01' sends: none above -oo (16 at -oo)"
    eq = await srv.get_eq("ch.2")
    assert eq["ok"] and eq["on"] is True and len(eq["bands"]) == 4 and eq["target"] == "ch.2"
    assert eq["summary"].startswith("Ch 2 'Ch02' EQ on: 1 PEQ") and "Q 2.0" in eq["summary"]
    dyn = await srv.get_dynamics("ch.2")
    assert dyn["ok"] and dyn["comp"]["on"] is False and dyn["gate"]["threshold_db"] == -80.0 and "comp off" in dyn["summary"]
    dyn_bus = await srv.get_dynamics("bus.2")
    assert dyn_bus["ok"] and dyn_bus["gate"] is None
    assert_err(await srv.get_eq("dca.1"), "NOT_SUPPORTED")


async def test_scene_listing_and_dump(app):
    scenes = await srv.list_scenes()
    assert scenes["ok"] and scenes["scenes"][0] == {"index": 0, "name": "Init", "notes": "", "has_data": True}
    assert scenes["current"]["index"] == 0 and 3 in scenes["empty_slots"] and 50 in scenes["empty_slots"]
    assert "1 'The Molecules'" in scenes["summary"]
    cur = await srv.get_current_scene()
    assert cur["ok"] and cur["index"] == 0 and cur["summary"] == "Current scene: 0 'Init'"
    dump = await srv.dump_desk_state(["ch.1"])
    assert dump["ok"] and "/ch/01/mix" in dump["sections"] and dump["missing"] == [] and dump["scope"] == ["ch.1"]
    assert dump["sections"]["/ch/01/mix"]["mix/fader"] == 0.0 and dump["sections"]["/ch/01/mix"]["mix/mlevel"] == "-oo"
    json.dumps(dump)
    assert_err(await srv.dump_desk_state(["nonsense.99"]), "BAD_ARGUMENT")


# ---------------------------------------------------------------------------------------- Tier 1 moves


async def test_tier1_moves_and_summaries(app, fakedesk):
    res = await srv.set_fader("ch.1", -6.0, ramp_ms=300)
    assert res["ok"] and res["before_db"] == 0.0 and res["after_db"] == -6.0 and res["steps"] == 15 and res["ramp_ms"] == 300
    assert res["summary"] == "Ch 1 'Ch01' fader 0.0 dB → −6.0 dB (ramped 300 ms)"
    assert app.policy.snapshot_before_write is False  # the first write took the auto snapshot
    lab = await srv.label_channel(5, name="Vox Tony", color="cyan", icon=3)
    assert lab["ok"] and lab["applied"] == {"name": "Vox Tony", "color": "CY", "icon": 3}
    assert lab["summary"] == "Ch 5 named 'Vox Tony', colour CY, icon 3"
    res = await srv.set_fader("tony", -4.0, ramp_ms=0)  # name resolution
    assert res["ok"] and res["target"] == "ch.5" and res["summary"] == "Ch 5 'Vox Tony' fader 0.0 dB → −4.0 dB"
    res = await srv.adjust_fader("ch.5", 2.0, ramp_ms=0)
    assert res["ok"] and res["after_db"] == -2.0 and res["delta_db"] == 2.0 and "(+2.0 dB)" in res["summary"]
    assert_err(await srv.adjust_fader("ch.5", -9.0, ramp_ms=0), "RELATIVE_TOO_LARGE")
    forced = await srv.adjust_fader("ch.5", -9.0, ramp_ms=0, force=True)
    assert forced["ok"] and forced["after_db"] == -11.0
    clamp = await srv.set_fader("ch.6", 8.0, ramp_ms=0)
    assert clamp["ok"] and clamp["after_db"] == 5.0 and clamp["clamped"]["limit"] == 5.0 and "clamped to the +5.0 dB limit" in clamp["summary"]
    down = await srv.set_fader("ch.7", -90, ramp_ms=0)
    assert down["ok"] and down["after_db"] is None and down["after"] == "-oo" and down["summary"].endswith("0.0 dB → -oo dB")
    m = await srv.mute("ch.5")
    assert m["ok"] and m["muted"] is True and m["was_muted"] is False and m["summary"] == "Ch 5 'Vox Tony' muted"
    await settle(app)
    assert fakedesk.get("/ch/05/mix/on") == 0  # 0 = OFF = muted on the wire (scales_params.md §4.8)
    u = await srv.unmute("ch.5")
    assert u["ok"] and u["muted"] is False and u["summary"] == "Ch 5 'Vox Tony' unmuted"
    await settle(app)
    assert fakedesk.get("/ch/05/mix/on") == 1
    assert_err(await srv.mute("main.st"), "GUARDED")
    assert_err(await srv.set_fader("main.st", -3.0, ramp_ms=0), "GUARDED")
    s = await srv.set_send("ch.1", 3, -10.0, ramp_ms=0)
    assert s["ok"] and s["send_to"] == 3 and s["kind"] == "send" and s["after_db"] == -10.0
    assert s["summary"] == "Bus 3 'Bus03' send from Ch 1 'Ch01' -oo dB → −10.0 dB"
    s2 = await srv.adjust_send("ch.1", 3, 3.0, ramp_ms=0)
    assert s2["ok"] and s2["after_db"] == -7.0 and s2["summary"].endswith("−10.0 dB → −7.0 dB (+3.0 dB)")
    assert_err(await srv.set_send("ch.1", 17, -3.0, ramp_ms=0), "BAD_ARGUMENT")
    eq = await srv.set_eq_band("ch.1", 2, freq_hz=1000, gain_db=-3.0, q=4.0, type="PEQ")
    assert eq["ok"] and eq["band"] == 2 and eq["applied"]["gain_db"] == -3.0 and eq["applied"]["type"] == "PEQ"
    assert eq["summary"].startswith("Ch 1 'Ch01' EQ band 2: type PEQ, freq ") and "gain −3.0 dB, q 3.9" in eq["summary"]  # desk grid
    eqc = await srv.set_eq_band("ch.1", 2, gain_db=20.0)
    assert eqc["ok"] and eqc["applied"]["gain_db"] == 15.0 and "clamped" in eqc["summary"]
    assert_err(await srv.set_eq_band("ch.1", 2), "BAD_ARGUMENT")
    assert_err(await srv.set_eq_band("ch.1", 5, gain_db=0.0), "BAD_ARGUMENT")
    pan = await srv.set_pan("ch.1", -50)
    assert pan["ok"] and pan["pan"] == -50 and pan["summary"] == "Ch 1 'Ch01' pan → L50"
    assert_err(await srv.set_pan("ch.1", 150), "BAD_ARGUMENT")
    comp = await srv.set_comp("ch.1", on=True, threshold_db=-20.0, ratio=4, attack_ms=10, release_ms=100, makeup_db=3.0)
    assert comp["ok"] and comp["applied"]["ratio"] == "4.0" and comp["applied"]["threshold_db"] == -20.0
    assert comp["summary"].startswith("Ch 1 'Ch01' comp: on on, threshold −20.0 dB, ratio 4.0, attack 10")
    await settle(app)
    assert fakedesk.value("/ch/01/dyn/on") is True and fakedesk.value("/ch/01/dyn/ratio") == "4.0"
    gate = await srv.set_gate("ch.1", on=True, threshold_db=-40.0, range_db=30.0)
    assert gate["ok"] and gate["applied"]["on"] is True and gate["applied"]["range_db"] == 30.0
    assert_err(await srv.set_gate("bus.1", on=True), "NOT_SUPPORTED")
    p = await srv.panic()
    assert p["ok"] and p["count"] == 24 and p["delivered"] == "sent" and p["summary"].startswith("PANIC: 24 outputs muted in")
    await settle(app)
    assert fakedesk.get("/main/st/mix/on") == 0 and fakedesk.get("/bus/16/mix/on") == 0 and fakedesk.get("/mtx/06/mix/on") == 0
    assert fakedesk.get("/ch/01/mix/on") == 1  # inputs are left alone


# ---------------------------------------------------------------------------------------- Tier 2 dance


async def test_set_main_fader_dance(app, fakedesk):
    first = await srv.set_main_fader("st", -6.0, ramp_ms=0)
    token = assert_pending(first)
    assert first["action_summary"] == "Set Main LR fader from 0.0 dB to −6.0 dB (0 ms ramp)" and first["current"] == "0.0"
    await settle(app)
    assert fakedesk.value("/main/st/mix/fader") == pytest.approx(0.0, abs=0.05)  # nothing written yet
    assert_err(await srv.set_main_fader("st", -6.0, ramp_ms=0, confirm_token="nope"), "BAD_TOKEN")
    # a token minted for different arguments cannot execute this request
    other = assert_pending(await srv.set_main_fader("st", -6.0, ramp_ms=0))
    assert_err(await srv.set_main_fader("st", -3.0, ramp_ms=0, confirm_token=other), "BAD_TOKEN")
    # a token minted for another action is refused as well
    mute_tok = assert_pending(await srv.set_main_mute("st", True))
    assert_err(await srv.set_main_fader("st", -6.0, ramp_ms=0, confirm_token=mute_tok), "BAD_TOKEN")
    token = assert_pending(await srv.set_main_fader("st", -6.0, ramp_ms=0))
    done = await srv.set_main_fader("st", -6.0, ramp_ms=0, confirm_token=token)
    assert done["ok"] and done["after_db"] == -6.0 and done["target"] == "main.st"
    assert done["summary"] == "Main LR 'Main' fader 0.0 dB → −6.0 dB"
    assert_err(await srv.set_main_fader("st", -6.0, ramp_ms=0, confirm_token=token), "BAD_TOKEN")  # single use
    await settle(app)
    assert fakedesk.value("/main/st/mix/fader") == pytest.approx(-6.0, abs=0.05)
    # main ceiling clamp on the confirmed path, 'lr' / 'mono' spellings
    token = assert_pending(await srv.set_main_fader("lr", 3.0, ramp_ms=0))
    up = await srv.set_main_fader("lr", 3.0, ramp_ms=0, confirm_token=token)
    assert up["ok"] and up["after_db"] == 0.0 and up["clamped"]["limit"] == 0.0
    assert_err(await srv.set_main_fader("left", 0.0), "BAD_ARGUMENT")
    # mute dance
    token = assert_pending(await srv.set_main_mute("mono", True))
    mm = await srv.set_main_mute("mono", True, confirm_token=token)
    assert mm["ok"] and mm["muted"] is True and mm["target"] == "main.m" and mm["summary"] == "Main M/C 'M/C' muted"
    await settle(app)
    assert fakedesk.get("/main/m/mix/on") == 0


async def test_recall_scene_by_index_and_name_and_save_dance(app, fakedesk):
    pend = await srv.recall_scene("the molecules")
    token = assert_pending(pend)
    assert pend["scene"]["index"] == 1 and pend["current"]["index"] == 0
    assert pend["action_summary"].startswith("Recall scene 1 'The Molecules' (currently loaded: 0 'Init')")
    res = await srv.recall_scene("The Molecules", confirm_token=token)
    assert res["ok"] and res["index"] == 1 and res["verified"] is True and res["previous_index"] == 0
    assert res["summary"] == "Recalled scene 1 'The Molecules' (verified; previous 0)"
    assert (await srv.get_channel(5))["name"] == "Vox Tony"
    assert (await srv.get_current_scene())["index"] == 1
    token = assert_pending(await srv.recall_scene(0))
    res = await srv.recall_scene(0, confirm_token=token)
    assert res["ok"] and res["index"] == 0
    token = assert_pending(await srv.recall_scene("1"))  # digit strings are indices
    assert (await srv.recall_scene("1", confirm_token=token))["index"] == 1
    assert_err(await srv.recall_scene("nonexistent"), "UNKNOWN_SCENE")
    assert_err(await srv.recall_scene(3), "BAD_ARGUMENT")  # empty slot
    assert_err(await srv.recall_scene(100), "BAD_ARGUMENT")
    # save: first call describes the slot, second call writes
    pend = await srv.save_scene(9, "Soundcheck", "after line check")
    token = assert_pending(pend)
    assert pend["action_summary"] == "Save the current desk state to scene 9 as 'Soundcheck' (slot is empty)"
    assert 9 not in fakedesk.scenes
    res = await srv.save_scene(9, "Soundcheck", "after line check", confirm_token=token)
    assert res["ok"] and res == {**res, "index": 9, "name": "Soundcheck", "notes": "after line check"}
    assert fakedesk.scenes[9]["name"] == "Soundcheck"
    scenes = await srv.list_scenes()
    assert any(s["index"] == 9 and s["name"] == "Soundcheck" and s["has_data"] for s in scenes["scenes"])
    pend = await srv.save_scene(9, "Soundcheck 2")
    assert "OVERWRITES the existing scene 'Soundcheck'" in pend["action_summary"]
    assert_err(await srv.save_scene(9, "   "), "BAD_ARGUMENT")


# ---------------------------------------------------------------------------------------- snapshots


async def test_snapshot_list_diff_and_restore_dance(app, fakedesk):
    snap = await srv.snapshot_desk("before show")
    assert snap["ok"] and snap["id"].endswith("-before-show") and snap["sections"] > 1000 and snap["missing"] == []
    assert Path(snap["path"]).exists() and snap["summary"].startswith(f"Snapshot {snap['id']} saved")
    lst = await srv.list_snapshots()
    assert lst["ok"] and lst["count"] == 1 and lst["snapshots"][0]["id"] == snap["id"] and lst["snapshots"][0]["label"] == "before show"
    assert (await srv.set_fader("ch.1", -6.0, ramp_ms=0))["ok"]
    assert (await srv.mute("ch.2"))["ok"]
    assert (await srv.list_snapshots())["count"] == 2  # the auto pre-write snapshot joined it
    diff = await srv.diff_snapshot(snap["id"])
    assert diff["ok"] and diff["snapshot"] == snap["id"] and diff["count"] >= 2 and diff["truncated"] is False
    labels = [c["label"] for c in diff["changes"]]
    assert any("Ch 1" in lb and "fader" in lb and "→" in lb for lb in labels), labels
    assert any("Ch 2" in lb for lb in labels), labels
    assert diff["text"].startswith("- ") and f"{diff['count']} change(s) since snapshot {snap['id']}" in diff["summary"]
    one = await srv.diff_snapshot(snap["id"], scope="ch.1")
    assert one["ok"] and one["count"] == 1 and one["changes"][0]["address"] == "/ch/01/mix/fader"
    assert one["changes"][0]["before"] == 0.0 and one["changes"][0]["after"] == -6.0 and "6.0" in one["changes"][0]["label"]
    assert_err(await srv.diff_snapshot("no-such-snapshot"), "NOT_FOUND")
    # restore: preview, then the write
    pend = await srv.restore_snapshot(snap["id"], scope="ch.1")
    token = assert_pending(pend)
    assert pend["count"] == 1 and pend["sections"] == 1 and "Restore snapshot" in pend["action_summary"] and "would change" in pend["action_summary"]
    assert (await srv.get_channel(1))["fader_db"] == -6.0  # nothing restored yet
    res = await srv.restore_snapshot(snap["id"], scope="ch.1", confirm_token=token)
    assert res["ok"] and res["written"] == 1 and res["failed"] == [] and res["scope"] == "ch.1"
    assert res["summary"].startswith(f"Restored snapshot {snap['id']} (before show) scope ch.1: 1/1 section(s) written")
    assert (await srv.get_channel(1))["fader_db"] == 0.0 and (await srv.get_channel(2))["muted"] is True
    # whole-desk restore of the same snapshot puts ch.2 back too
    token = assert_pending(await srv.restore_snapshot(snap["id"]))
    res = await srv.restore_snapshot(snap["id"], confirm_token=token)
    assert res["ok"] and res["written"] >= 1
    assert (await srv.get_channel(2))["muted"] is False
    assert (await srv.diff_snapshot(snap["id"]))["count"] == 0


# ---------------------------------------------------------------------------------------- show mode


async def test_show_mode_blocks_scenes_even_with_token_and_tightens_moves(app):
    sm = await srv.show_mode(True)
    assert sm["ok"] and sm["show_mode"] is True and sm["relative_limit_db"] == 3.0 and "scene_recall" in sm["blocked"]
    assert_err(await srv.recall_scene(1), "SHOW_MODE_BLOCKS")
    assert_err(await srv.save_scene(9, "x"), "SHOW_MODE_BLOCKS")
    assert_err(await srv.ring_out(1), "SHOW_MODE_BLOCKS")
    assert_err(await srv.ring_out_system(), "SHOW_MODE_BLOCKS")
    assert_err(await srv.setup_ringout_eqs([1]), "SHOW_MODE_BLOCKS")
    # a token minted before show mode went on does not help
    assert (await srv.show_mode(False))["show_mode"] is False
    token = assert_pending(await srv.recall_scene(1))
    await srv.show_mode(True)
    assert_err(await srv.recall_scene(1, confirm_token=token), "SHOW_MODE_BLOCKS")
    assert (await srv.get_current_scene())["index"] == 0
    big = await srv.adjust_fader("ch.1", 4.0, ramp_ms=0)
    assert_err(big, "RELATIVE_TOO_LARGE")
    assert big["error"]["limit_db"] == 3
    ok = await srv.adjust_fader("ch.1", 2.0, ramp_ms=0)
    assert ok["ok"] and ok["after_db"] == 2.0
    st = await srv.connection_status()
    assert st["show_mode"] is True and "SHOW MODE ON" in st["summary"]
    # the undo stays available in show mode
    token = assert_pending(await srv.restore_snapshot("latest", scope="ch.1"))
    res = await srv.restore_snapshot("latest", scope="ch.1", confirm_token=token)
    assert res["ok"] and (await srv.get_channel(1))["fader_db"] == 0.0
    off = await srv.show_mode(False)
    assert off["show_mode"] is False and off["relative_limit_db"] == 6.0


# ---------------------------------------------------------------------------------------- labels / patches / config


async def test_label_channel(app, fakedesk):
    res = await srv.label_channel(7, name="Kick In", color="red", icon=2)
    assert res["ok"] and res["applied"] == {"name": "Kick In", "color": "RD", "icon": 2} and res["target"] == "ch.7"
    await settle(app)
    assert fakedesk.get("/ch/07/config/name") == "Kick In" and fakedesk.get("/ch/07/config/color") == 1 and fakedesk.get("/ch/07/config/icon") == 2
    long = await srv.label_channel(8, name="A name that is far too long")
    assert long["ok"] and long["truncated"] is True and long["applied"]["name"] == "A name that " and "truncated to 12 characters" in long["summary"]
    inv = await srv.label_channel(8, color="blue inverted")
    assert inv["ok"] and inv["applied"] == {"color": "BLi"}
    assert_err(await srv.label_channel(8, color="pink"), "BAD_ARGUMENT")
    assert_err(await srv.label_channel(8), "BAD_ARGUMENT")
    assert_err(await srv.label_channel(40, name="x"), "BAD_ARGUMENT")
    assert (await srv.get_strip("kick in"))["target"] == "ch.7"


async def test_apply_and_export_patch_plan(app, fakedesk, tmp_path):
    res = await srv.apply_patch_plan(str(EXAMPLE))
    assert res["ok"] and res["rows"] == 16 and len(res["applied"]) == 16 and res["failed"] == [] and res["include_source"] is False
    assert res["skipped_source"] == list(range(1, 17)) and res["band"] == "The Molecules" and "16 channel(s) labelled" in res["summary"]
    await settle(app)
    assert fakedesk.get("/ch/13/config/name") == "Vox Lead" and fakedesk.get("/ch/13/config/source") == 13  # sources untouched
    again = await srv.apply_patch_plan(str(EXAMPLE))
    assert again["ok"] and again["applied"] == [] and again["unchanged"] == list(range(1, 17))
    # bare names resolve under settings.patch_dir, suffix optional
    (tmp_path / "patches").mkdir(exist_ok=True)
    shutil.copy(EXAMPLE, tmp_path / "patches" / "band.yaml")
    byname = await srv.apply_patch_plan("band")
    assert byname["ok"] and byname["file"].endswith("band.yaml")
    assert_err(await srv.apply_patch_plan("nope"), "NOT_FOUND")
    (tmp_path / "patches" / "bad.yaml").write_text("channels:\n  - {channel: 99, name: x, colour: puce}\n", encoding="utf-8")
    bad = await srv.apply_patch_plan("bad")
    assert_err(bad, "BAD_PATCH")
    assert bad["error"]["problems"]
    # sources need the Tier-2 dance
    moved = tmp_path / "patches" / "moved.csv"
    moved.write_text("channel,name,color,source,mic,owner,monitor_bus,notes\n16,Talkback,OFF,AUX1,false,,,\n", encoding="utf-8")
    pend = await srv.apply_patch_plan("moved", include_source=True)
    token = assert_pending(pend)
    assert "patch 1 input source(s): ch 16 → AUX1" in pend["action_summary"] and pend["rows"] == 1
    await settle(app)
    assert fakedesk.value("/ch/16/config/source") == "IN16"
    done = await srv.apply_patch_plan("moved", include_source=True, confirm_token=token)
    assert done["ok"] and done["include_source"] is True and done["applied"][0]["changed"] == ["source"]
    await settle(app)
    assert fakedesk.value("/ch/16/config/source") == "AUX1"
    # export merges the metadata of an existing file and reads the desk fresh
    fakedesk.set_value("/ch/01/config/name", "Kick In")
    exp = await srv.export_patch_plan("band")
    assert exp["ok"] and exp["merged"] is True and exp["rows"] == 32 and exp["band"] == "The Molecules" and exp["file"].endswith("band.yaml")
    plan = load_patch_plan(tmp_path / "patches" / "band.yaml", descriptor=app.descriptor)
    assert plan.row(13).owner == "Tony" and plan.row(13).monitor_bus == 1 and plan.row(1).name == "Kick In" and plan.row(16).source == "AUX1"
    assert plan.row(20).name == "Ch20" and plan.row(20).mic is None
    fresh = await srv.export_patch_plan("fresh.csv")
    assert fresh["ok"] and fresh["merged"] is False and fresh["file"].endswith("fresh.csv") and Path(fresh["file"]).is_file()
    assert len(load_patch_plan(fresh["file"], descriptor=app.descriptor).rows) == 32


async def test_set_channel_config_dance(app, fakedesk):
    pend = await srv.set_channel_config(1, source="in 5", link=True)
    token = assert_pending(pend)
    assert pend["action_summary"] == "Ch 1 'Ch01': input source IN01 → IN05, stereo link Ch 1-2 ON"
    await settle(app)
    assert fakedesk.get("/ch/01/config/source") == 1
    done = await srv.set_channel_config(1, source="in 5", link=True, confirm_token=token)
    assert done["ok"] and done["applied"] == {"source": "IN05", "link": True, "link_pair": "1-2"}
    await settle(app)
    assert fakedesk.get("/ch/01/config/source") == 5 and fakedesk.get("/config/chlink/1-2") == 1
    assert (await srv.get_channel(1))["source"] == "IN05"
    assert_err(await srv.set_channel_config(1), "BAD_ARGUMENT")
    assert_err(await srv.set_channel_config(2, source="nope"), "BAD_ARGUMENT")


# ---------------------------------------------------------------------------------------- meters


async def test_get_meters_and_get_rta(app):
    m = await srv.get_meters("channels", duration_ms=200)
    assert m["ok"] and m["meter_type"] == 0 and m["frames"] == 4 and len(m["items"]) == 32
    assert m["items"][0] == {"index": 1, "target": "ch.1", "name": "Ch01", "db": m["items"][0]["db"]} and isinstance(m["items"][0]["db"], float)
    assert m["summary"].startswith("channels levels over 200 ms (32 meters): loudest")
    main = await srv.get_meters("main", duration_ms=100)
    assert main["ok"] and [it["name"] for it in main["items"]] == ["Main L", "Main R", "Main M/C"]
    buses = await srv.get_meters("buses", duration_ms=50)
    assert buses["ok"] and len(buses["items"]) == 16 and buses["items"][15]["target"] == "bus.16"
    assert_err(await srv.get_meters("nope"), "BAD_ARGUMENT")
    rta = await srv.get_rta("bus.1", frames=4)
    assert rta["ok"] and rta["rta_source"] == "bus.1" and rta["frames"] == 4 and len(rta["db"]) == 100 and len(rta["band_hz"]) == 100
    assert rta["rta"]["verified"] is True and rta["rta"]["stat_actual"] == 146  # Bus 1 post-EQ (meters.md §5.2)
    assert len(rta["peaks"]) == 5 and rta["peaks"][0]["db"] >= rta["peaks"][1]["db"] and "RTA on bus.1" in rta["summary"]
    assert_err(await srv.get_rta("dca.1", frames=1), "BAD_ARGUMENT")
    json.dumps(rta)


# ---------------------------------------------------------------------------------------- CFS²


async def test_cfs_tools_with_injected_synthetic_rta(fakedesk, tmp_path, descriptor):
    rta = SyntheticRta(seed=2, period_s=0.05, band_hz=rta_band_hz(descriptor))
    a = await make_app(fakedesk, tmp_path, frames=rta)
    try:
        v = await srv.validate_ringout_eqs([1, 2])
        assert v["ok"] and v["all_ok"] is False and v["buses"]["1"]["ok"] is False and "Bus 1: NOT OK" in v["summary"]
        assert any("no FX insert" in r for r in v["buses"]["1"]["reasons"])
        assert_err(await srv.ring_out_system(), "NO_STAGES")  # nothing validates yet: no token is minted
        assert_err(await srv.validate_ringout_eqs([]), "BAD_ARGUMENT")
        assert_err(await srv.validate_ringout_eqs([17]), "BAD_ARGUMENT")
        pend = await srv.setup_ringout_eqs([1, 2])
        token = assert_pending(pend)
        assert "load GEQ2 into FX slot 5" in pend["action_summary"] and "insert FX5L on bus.1" in pend["action_summary"]
        assert pend["plan"]["needs_tier2"] is True and len(pend["plan"]["inserts"]) == 2
        assert_err(await srv.setup_ringout_eqs([1, 2], confirm_token="bad"), "BAD_TOKEN")
        token = assert_pending(await srv.setup_ringout_eqs([1, 2]))
        done = await srv.setup_ringout_eqs([1, 2], confirm_token=token)
        assert done["ok"] and done["changed"] == 3 and done["geq"]["1"]["ok"] and done["geq"]["2"]["ok"]
        await settle(a)
        assert fakedesk.value("/bus/01/insert/sel") == "FX5L" and fakedesk.value("/bus/02/insert/sel") == "FX5R"
        again = await srv.setup_ringout_eqs([1, 2])  # idempotent: no dance, nothing written
        assert again["ok"] and again["changed"] == 0 and "already set up" in again["summary"]
        v = await srv.validate_ringout_eqs([1, 2])
        assert v["all_ok"] is True and v["buses"]["1"]["insert"]["sel"] == "FX5L" and v["buses"]["2"]["flat"] is True
        # route two mics to bus 1 and set a sensible master
        for ch in (1, 2):
            fset(a, fakedesk, f"/ch/{ch:02d}/mix/01/level", -20.0)
        fset(a, fakedesk, "/bus/01/mix/fader", -20.0)
        mics = await srv.discover_mics(1)
        assert mics["ok"] and mics["bus"] == 1 and mics["included"] == [1, 2] and mics["summary"].startswith("Bus 1: 2 candidate(s), 2 included: Ch 1 'Ch01' −20.0 dB")
        mics_p = await srv.discover_mics(1, patch_file=str(EXAMPLE))
        assert mics_p["ok"] and mics_p["mics"][0]["owner"] == "Ray" and any("bus 2" in n for n in mics_p["mics"][0]["notes"])
        assert_err(await srv.discover_mics(1, patch_file="missing"), "NOT_FOUND")
        st = await srv.cfs_status()
        assert st["ok"] and st["mode"] == "idle" and st["summary"] == "CFS² idle" and st["frames"]["source"] == "SyntheticRta"
        fw = await srv.feedback_watch(1, notch_budget=4)
        assert fw["ok"] and fw["bus"] == 1 and fw["rta"]["verified"] is True and fw["geq"] == {"fx_slot": 5, "side": "A", "sel": "FX5L", "existing_cuts": []}
        assert fw["summary"].startswith("Feedback watch armed on Bus 1 'Bus01': GEQ FX5L (FX 5A), master −20.0 dB, RTA verified; open mics: Ch 1 'Ch01', Ch 2 'Ch02'")
        assert fw["summary"].endswith("budget 4 notch(es)")
        assert_err(await srv.feedback_watch(2), "BUSY")
        st = await srv.cfs_status()
        assert st["mode"] == "watch" and st["budget_left"] == 4 and st["rta_source"] == "bus.1" and st["session_id"] == fw["session_id"]
        assert st["summary"].startswith("CFS² watch on Bus 1 'Bus01': master −20.0 dB, budget left 4")
        live = await srv.get_rta()
        assert live["ok"] and live["rta_source"] == "bus.1" and live["rta"] is None
        assert_err(await srv.get_rta("bus.2"), "BUSY")
        rta.inject_ring(2400.0, 20.0, start_db=-34.0)
        await wait_until(lambda: len(a.cfs.state.notches) >= 1, timeout=3.0, what="a notch from the injected ring")
        st = await srv.cfs_status()
        assert st["notches"][0]["band"] == GEQ_BAND_2K5 and st["notches"][0]["depth_db"] == -3.0 and st["budget_left"] == 3
        stop = await srv.feedback_watch_stop()
        assert stop["ok"] and stop["stopped"] is True and stop["mode"] == "watch" and stop["session_id"] == fw["session_id"]
        assert stop["report"]["notches"][0]["freq_hz"] == 2500 and "detection_log" not in stop["report"] and Path(stop["path"]).exists()
        assert stop["summary"].startswith(f"Stopped CFS² watch session {fw['session_id']}: 1 notch(es) (2.50 kHz −3.0 dB)")
        rta.stop_ring(2400.0)
        idle = await srv.feedback_watch_stop()
        assert idle["ok"] and idle["stopped"] is False and idle["summary"] == "Nothing to stop: CFS² is idle"
        reports = await srv.list_ringout_reports(1)
        assert reports["ok"] and reports["count"] == 1 and reports["reports"][0]["mode"] == "watch" and reports["reports"][0]["notches"] == 1
        rep = await srv.get_ringout_report(fw["session_id"])
        assert rep["ok"] and rep["mode"] == "watch" and rep["notches"][0]["band"] == GEQ_BAND_2K5 and "2500 Hz" in rep["markdown"]
        assert_err(await srv.get_ringout_report("zzz-nope"), "NOT_FOUND")
        assert_err(await srv.list_ringout_reports("nonsense.9"), "BAD_ARGUMENT")
        # ring_out dance: preflight in the first call, the run only with the token (no ring: DONE at the target)
        pend = await srv.ring_out(1, target_gain_db=-19.0, dwell_ms=10)
        token = assert_pending(pend)
        assert pend["preflight"]["ok"] is True and pend["target_db"] == -19.0
        assert pend["action_summary"].startswith("Ring out Bus 1 'Bus01': raise its master from −20.0 dB to −19.0 dB in 1 dB steps (dwell 10 ms), cutting up to 6 notch(es) on GEQ FX5L; open mics: Ch 1 'Ch01', Ch 2 'Ch02'")
        assert "already carries" in pend["action_summary"]  # the watch session's cut is reported
        assert_err(await srv.ring_out(1, target_gain_db=-19.0, dwell_ms=10, confirm_token="bad"), "BAD_TOKEN")
        assert (await srv.cfs_status())["mode"] == "idle"
        rep = await srv.ring_out(1, target_gain_db=-19.0, dwell_ms=10, confirm_token=token)
        assert rep["ok"] and rep["mode"] == "ringout" and rep["final_stage"] == "DONE" and rep["aborted"] is False
        assert rep["start_master_db"] == -20.0 and rep["max_master_db"] == -19.0 and rep["end_master_db"] == -22.0  # target - 3 dB margin
        assert "detection_log" not in rep and rep["detection_log_entries"] == 0 and rep["summary"]
        await settle(a)
        assert fakedesk.value("/bus/01/mix/fader") == pytest.approx(-22.0, abs=0.1)
        assert (await srv.list_ringout_reports())["count"] == 2
        assert_err(await srv.ring_out(1, step_db=0), "BAD_ARGUMENT")
        # preflight failure returns the blockers instead of a token
        pf = await srv.ring_out(2)  # no mic feeds bus 2
        assert_err(pf, "PREFLIGHT_FAILED")
        assert pf["error"]["preflight"]["ok"] is False and pf["error"]["preflight"]["blockers"]
        # system ring-out: one confirmation for the whole plan
        pend = await srv.ring_out_system({"stages": [{"bus": 1, "target_gain_db": -19.0, "dwell_ms": 10}, {"bus": "main"}]})
        token = assert_pending(pend)
        assert pend["action_summary"].startswith("System ring-out of 2 stage(s) in order: Bus 1, Main LR")
        assert [s["bus"] for s in pend["stages"]] == [1, "main"]
        auto = await srv.ring_out_system()  # no plan: every bus whose GEQ validates (bus 1 and 2 here)
        assert_pending(auto)
        assert [s["bus"] for s in auto["stages"]] == [1, 2] and "Bus 1, Bus 2" in auto["action_summary"]
        assert_err(await srv.ring_out_system({"stages": []}), "BAD_ARGUMENT")
        assert_err(await srv.ring_out_system({"stages": [{"bus": "dca.1"}]}), "BAD_ARGUMENT")
    finally:
        await a.close()
        srv.app = None


# ---------------------------------------------------------------------------------------- dashboard


async def test_dashboard_status(app):
    ds = await srv.dashboard_status()
    assert ds["ok"] and ds["enabled"] is False and ds["running"] is False and ds["url"] is None and "disabled" in ds["summary"]
    app.dashboard.port = 0  # ephemeral port for the test
    await app.dashboard.start()
    try:
        ds = await srv.dashboard_status()
        assert ds["running"] is True and ds["url"] == f"http://127.0.0.1:{app.dashboard.port}/" and ds["clients"] == 0
        assert ds["summary"].startswith(f"Dashboard running at {ds['url']}") and ds["mode"] == "idle"
    finally:
        await app.dashboard.stop()


# ---------------------------------------------------------------------------------------- stdio end to end


async def test_stdio_smoke_spawns_the_server():
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    # INFO logging on purpose: every log line must go to stderr, never onto the stdout wire
    env = {"X32MCP_DASH": "0", "X32MCP_LOG": "INFO", "X32_HOST": "", "PYTHONPATH": str(REPO / "src")}
    params = StdioServerParameters(command=sys.executable, args=["-m", "x32mcp.server"], env=env, cwd=str(REPO))
    async with Client(params, read_timeout_seconds=30.0) as c:
        assert c.instructions and "confirm_token" in c.instructions and "panic" in c.instructions
        tools = (await c.list_tools()).tools
        names = {t.name for t in tools}
        assert len(tools) >= 40 and {"panic", "recall_scene", "connect", "ring_out"} <= names
        recall = next(t for t in tools if t.name == "recall_scene")
        assert "confirm_token" in recall.input_schema["properties"] and "TIER 2" in (recall.description or "")
        rr = await c.read_resource("x32://device")
        text = rr.contents[0].text
        assert "meta:" in text and "policy:" in text
        state = json.loads((await c.read_resource("x32://cfs/state")).contents[0].text)
        assert state["mode"] == "idle" and state["notches"] == []
        json.loads((await c.read_resource("x32://snapshot/latest")).contents[0].text)  # a snapshot file or {"error": ...}
        assert "The Molecules" in (await c.read_resource("x32://patches/example_band")).contents[0].text
        r = await c.call_tool("connection_status", {})
        assert r.is_error is False
        sc = r.structured_content
        assert sc["ok"] is False and sc["error"]["code"] == "NOT_CONNECTED" and sc["state"] == "disconnected"
        assert sc["dashboard"]["enabled"] is False
        r = await c.call_tool("panic", {})
        assert r.structured_content["ok"] is False and r.structured_content["error"]["code"] == "NOT_CONNECTED"
        r = await c.call_tool("show_mode", {"on": True})
        assert r.structured_content["ok"] is True and r.structured_content["show_mode"] is True
