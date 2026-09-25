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
from x32mcp.descriptor import Descriptor
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
    # TTL comes from device.yaml (policy.confirm_token_ttl_s) so raising it does not break every dance test
    assert res["confirm_token"] and res["action_summary"], res
    assert res["expires_in_s"] == Descriptor.load().policy["confirm_token_ttl_s"], res
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
        d = await srv.disconnect(confirm_token=assert_pending(await srv.disconnect()))  # disconnecting a live desk is confirmed
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
    assert ch["mono_level_db"] == "-oo" and ch["mono_level"] == "-oo"
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
    assert sends["sends"][2]["bus"] == 3 and sends["sends"][2]["level"] == "-oo" and sends["sends"][2]["level_db"] == "-oo"
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
    assert dump["truncated"] is False and dump["returned"] == dump["section_count"] == len(dump["sections"])
    json.dumps(dump)
    assert_err(await srv.dump_desk_state(["nonsense.99"]), "BAD_ARGUMENT")


async def test_dump_desk_state_whole_desk_is_capped(app):
    """The whole desk is ~2100 sections / ~200 KB — far past any tool-result budget."""
    dump = await srv.dump_desk_state()
    assert dump["ok"] and dump["section_count"] > 1000, dump["section_count"]
    assert dump["truncated"] is True and dump["returned"] == len(dump["sections"]) == srv.MAX_DUMP_SECTIONS
    assert "snapshot_desk" in dump["summary"] and "sections=" in dump["summary"]
    assert len(json.dumps(dump)) < 60_000, "a capped dump still fits in a tool result"


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
    assert down["ok"] and down["after_db"] == "-oo" and down["after"] == "-oo" and down["summary"].endswith("0.0 dB → -oo dB")
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
    assert comp["summary"].startswith("Ch 1 'Ch01' comp: on, threshold −20.0 dB, ratio 4.0, attack 10")  # not "on on"
    await settle(app)
    assert fakedesk.value("/ch/01/dyn/on") is True and fakedesk.value("/ch/01/dyn/ratio") == "4.0"
    gate = await srv.set_gate("ch.1", on=True, threshold_db=-40.0, range_db=30.0)
    assert gate["ok"] and gate["applied"]["on"] is True and gate["applied"]["range_db"] == 30.0
    assert_err(await srv.set_gate("bus.1", on=True), "NOT_SUPPORTED")
    p = await srv.panic()
    assert p["ok"] and p["count"] == 24 and p["delivered"] == "confirmed" and p["summary"].startswith("PANIC: 24 mutes sent in")
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
    # the slot's occupancy is bound into the token: 'slot is empty' confirmed, then someone (X32-Edit)
    # saves 'Main Show' into slot 10 -> the confirmed call must not overwrite it unseen
    pend = await srv.save_scene(10, "Monitors")
    token = assert_pending(pend)
    assert "(slot is empty)" in pend["action_summary"]
    fakedesk.scenes[10] = {"name": "Main Show", "notes": "", "state": fakedesk._scene_state_copy()}
    fakedesk.set_value(fakedesk._scene_addr(10, "name"), "Main Show")
    fakedesk.set_value(fakedesk._scene_addr(10, "hasdata"), 1)
    assert_err(await srv.save_scene(10, "Monitors", confirm_token=token), "BAD_TOKEN")  # not the request that was confirmed
    assert fakedesk.scenes[10]["name"] == "Main Show"  # nothing overwritten
    again = await srv.save_scene(10, "Monitors")
    assert "OVERWRITES the existing scene 'Main Show'" in again["action_summary"]


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
    tok = assert_pending(await srv.show_mode(False))  # turning show mode OFF is confirmed
    assert (await srv.show_mode(False, confirm_token=tok))["show_mode"] is False
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
    off = await srv.show_mode(False, confirm_token=assert_pending(await srv.show_mode(False)))
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


# ---------------------------------------------------------------------------------------- routing, stereo links, solo


async def test_get_routing_reads_the_fake(app, fakedesk):
    r = await srv.get_routing()
    assert r["ok"] and r["routswitch"] == "REC" and "play_inputs" not in r
    assert r["inputs"] == {"1-8": "AN1-8", "9-16": "AN9-16", "17-24": "AN17-24", "25-32": "AN25-32", "AUX": "AUX1-4"}
    assert len(r["user_in"]) == 32
    assert r["user_in"][0] == {"slot": 1, "number": 0, "source": "OFF", "description": "off", "active": False, "feeds": None}
    assert r["bus_links"] == {f"{o}-{o + 1}": False for o in range(1, 17, 2)}
    assert r["solo"] == {"channels": "PFL", "buses": "AFL", "dcas": "AFL"}
    assert r["summary"] == ("Inputs (REC): 1-8 AN1-8, 9-16 AN9-16, 17-24 AN17-24, 25-32 AN25-32, AUX AUX1-4; User-In slots live: 0; "
                            "bus links: none; solo channels PFL, buses AFL, DCAs AFL")
    json.dumps(r)
    # a User-In slot is live when an IN block in force reads its UIN group: block 1-8 <- UIN9-16 means slot 12 feeds In 4
    fset(app, fakedesk, "/config/routing/IN/1-8", "UIN9-16")
    fset(app, fakedesk, "/config/routing/IN/AUX", "UIN1-6")
    fset(app, fakedesk, "/config/userrout/in/12", 33)
    fset(app, fakedesk, "/config/userrout/in/02", 168)
    r = await srv.get_routing()
    assert r["user_in"][11] == {"slot": 12, "number": 33, "source": "A01", "description": "AES50-A 1", "active": True, "feeds": "In 4"}
    assert r["user_in"][1] == {"slot": 2, "number": 168, "source": "TBEXT", "description": "talkback external", "active": True, "feeds": "Aux In 2"}
    assert r["user_in"][8]["active"] is True and r["user_in"][16]["active"] is False
    assert "User-In slots live: 14" in r["summary"]
    # PLAY routing: the PLAY table is the one in force
    fset(app, fakedesk, "/config/routing/routswitch", "PLAY")
    r = await srv.get_routing()
    assert r["routswitch"] == "PLAY" and r["play_inputs"]["1-8"] == "AN1-8" and r["inputs"]["1-8"] == "UIN9-16"
    assert r["user_in"][11]["active"] is False and r["summary"].startswith("Inputs (PLAY):")


async def test_set_bus_link_dance(app, fakedesk):
    first = await srv.set_bus_link(4, True)
    token = assert_pending(first)
    assert first["action_summary"] == "Stereo link Bus 3-4 ('Bus03' / 'Bus04'): OFF → ON" and first["current"] is False
    await settle(app)
    assert fakedesk.get("/config/buslink/3-4") == 0 and app.desk.write_count == 0 and app.policy.snapshot_before_write is True  # nothing written
    assert_err(await srv.set_bus_link(4, True, confirm_token="nope"), "BAD_TOKEN")
    # a token minted for another pair cannot execute this request
    other = assert_pending(await srv.set_bus_link(4, True))
    assert_err(await srv.set_bus_link(6, True, confirm_token=other), "BAD_TOKEN")
    token = assert_pending(await srv.set_bus_link(3, True))  # either bus of the pair is the same request
    done = await srv.set_bus_link(4, True, confirm_token=token)
    assert done["ok"] and done["pair"] == "3-4" and done["buses"] == [3, 4] and done["link"] is True and done["was_linked"] is False
    assert done["summary"] == "Stereo link Bus 3-4 ('Bus03' / 'Bus04') ON"
    assert_err(await srv.set_bus_link(4, True, confirm_token=token), "BAD_TOKEN")  # single use
    await settle(app)
    assert fakedesk.get("/config/buslink/3-4") == 1 and app.policy.snapshot_before_write is False
    assert (await srv.get_routing())["bus_links"]["3-4"] is True
    again = await srv.set_bus_link(4, True)
    assert again["action_summary"].endswith("ON → ON (no change)")
    # the token binds the state the user was shown: a front-panel change between the calls voids it
    tok = assert_pending(await srv.set_bus_link(1, True))
    fset(app, fakedesk, "/config/buslink/1-2", True)
    assert_err(await srv.set_bus_link(1, True, confirm_token=tok), "BAD_TOKEN")
    tok = assert_pending(await srv.set_bus_link(1, False))
    off = await srv.set_bus_link(1, False, confirm_token=tok)
    assert off["ok"] and off["link"] is False and off["was_linked"] is True
    await settle(app)
    assert fakedesk.get("/config/buslink/1-2") == 0
    assert_err(await srv.set_bus_link(17, True), "BAD_ARGUMENT")
    assert_err(await srv.set_bus_link(0, False), "BAD_ARGUMENT")


async def test_set_input_block_dance(app, fakedesk):
    first = await srv.set_input_block("ch 17-24", "a17-24")
    token = assert_pending(first)
    assert first["action_summary"] == "Input block 17-24 (In 17-24): AN17-24 → A17-24" and first["current"] == "AN17-24"
    await settle(app)
    assert fakedesk.value("/config/routing/IN/17-24") == "AN17-24" and app.desk.write_count == 0 and app.policy.snapshot_before_write is True
    assert_err(await srv.set_input_block("17-24", "A17-24", confirm_token="nope"), "BAD_TOKEN")
    token = assert_pending(await srv.set_input_block("IN/17-24", "A17-24"))  # spellings normalise to the same request
    done = await srv.set_input_block("17-24", "A17-24", confirm_token=token)
    assert done["ok"] and done["block"] == "17-24" and done["before"] == "AN17-24" and done["source"] == "A17-24"
    assert done["summary"] == "Input block 17-24 (In 17-24) AN17-24 → A17-24" and done["address"] == "/config/routing/IN/17-24"
    assert_err(await srv.set_input_block("17-24", "A17-24", confirm_token=token), "BAD_TOKEN")  # single use
    await settle(app)
    assert fakedesk.value("/config/routing/IN/17-24") == "A17-24" and app.policy.snapshot_before_write is False
    assert (await srv.get_routing())["inputs"]["17-24"] == "A17-24"
    # the AUX block has its own source list
    tok = assert_pending(await srv.set_input_block("aux", "uin1-6"))
    aux = await srv.set_input_block("aux in", "UIN1-6", confirm_token=tok)
    assert aux["ok"] and aux["block"] == "AUX" and aux["source"] == "UIN1-6" and aux["summary"] == "Input block AUX (Aux In 1-6) AUX1-4 → UIN1-6"
    await settle(app)
    assert fakedesk.value("/config/routing/IN/AUX") == "UIN1-6"
    assert_err(await srv.set_input_block("aux", "AN9-16"), "BAD_ARGUMENT")  # not an AUX-block source
    assert_err(await srv.set_input_block("1-8", "AUX1-4"), "BAD_ARGUMENT")  # not an IN-block source
    assert_err(await srv.set_input_block("33-40", "AN1-8"), "BAD_ARGUMENT")
    assert_err(await srv.set_input_block("1-8", "nope"), "BAD_ARGUMENT")
    # a front-panel change between the calls voids the token
    tok = assert_pending(await srv.set_input_block("1-8", "B1-8"))
    fset(app, fakedesk, "/config/routing/IN/1-8", "CARD1-8")
    assert_err(await srv.set_input_block("1-8", "B1-8", confirm_token=tok), "BAD_TOKEN")
    await settle(app)
    assert fakedesk.value("/config/routing/IN/1-8") == "CARD1-8"
    # PLAY routing is flagged in the summary
    fset(app, fakedesk, "/config/routing/routswitch", "PLAY")
    pend = await srv.set_input_block("9-16", "A9-16")
    assert_pending(pend)
    assert pend["action_summary"].startswith("Input block 9-16 (In 9-16): AN9-16 → A9-16 — note: the desk is in PLAY routing")


async def test_set_user_in_dance(app, fakedesk):
    first = await srv.set_user_in(4, "AES50-A 1")
    token = assert_pending(first)
    assert first["action_summary"] == "User-In slot 4: OFF (off) → A01 (AES50-A 1) [not live: no input block reads UIN1-8 now]"
    assert first["current"]["slot"] == 4 and first["current"]["number"] == 0
    await settle(app)
    assert fakedesk.get("/config/userrout/in/04") == 0 and app.desk.write_count == 0 and app.policy.snapshot_before_write is True
    assert_err(await srv.set_user_in(4, "A01", confirm_token="nope"), "BAD_TOKEN")
    token = assert_pending(await srv.set_user_in(4, 33))  # the raw number is the same request
    done = await srv.set_user_in(4, "a01", confirm_token=token)
    assert done["ok"] and done["slot"] == 4 and done["number"] == 33 and done["source"] == "A01" and done["description"] == "AES50-A 1"
    assert done["before"] == 0 and done["before_source"] == "OFF" and done["active"] is False and done["address"] == "/config/userrout/in/04"
    assert done["summary"] == "User-In slot 4 OFF (off) → A01 (AES50-A 1)"
    assert_err(await srv.set_user_in(4, "a01", confirm_token=token), "BAD_TOKEN")  # single use
    await settle(app)
    assert fakedesk.get("/config/userrout/in/04") == 33 and app.policy.snapshot_before_write is False
    assert (await srv.get_routing())["user_in"][3]["source"] == "A01"
    # once the block reads UIN1-8 the slot is live, the summary says which In it feeds, and the
    # head-amp resolution follows the patch (A01 = AES50-A 1 = head amp 032)
    fset(app, fakedesk, "/config/routing/IN/1-8", "UIN1-8")
    assert await app.desk.headamp_index_for("ch.4") == 32
    pend = await srv.set_user_in(4, "card 2")
    tok = assert_pending(pend)
    assert pend["action_summary"] == "User-In slot 4: A01 (AES50-A 1) → CARD02 (card/USB 2) [live: feeds In 4]"
    fset(app, fakedesk, "/config/userrout/in/04", 34)  # a front-panel change between the calls voids the token
    assert_err(await srv.set_user_in(4, "card 2", confirm_token=tok), "BAD_TOKEN")
    await settle(app)
    assert fakedesk.get("/config/userrout/in/04") == 34
    tok = assert_pending(await srv.set_user_in(4, "CARD02"))
    card = await srv.set_user_in(4, "CARD02", confirm_token=tok)
    assert card["ok"] and card["number"] == 130 and card["before_source"] == "A02" and card["feeds"] == "In 4"
    assert await app.desk.headamp_index_for("ch.4") is None  # a card source has no preamp
    for bad in ("A49", "nope", 999, -1, "ch 4", "USBL", 4.5):
        assert_err(await srv.set_user_in(4, bad), "BAD_ARGUMENT")
    assert_err(await srv.set_user_in(33, "IN01"), "BAD_ARGUMENT")
    assert_err(await srv.set_user_in(0, "IN01"), "BAD_ARGUMENT")


async def test_set_solo_mode_is_tier1(app, fakedesk):
    res = await srv.set_solo_mode(channels="afl", dcas="PFL")
    assert res["ok"] and res["applied"] == {"channels": "AFL", "dcas": "PFL"} and res["before"] == {"channels": "PFL", "dcas": "AFL"}
    assert res["summary"] == "Solo mode: channels PFL → AFL, DCAs AFL → PFL"
    assert app.policy.snapshot_before_write is False  # a Tier-1 write: auto snapshot taken, no token asked for
    await settle(app)
    assert fakedesk.value("/config/solo/chmode") == "AFL" and fakedesk.value("/config/solo/dcamode") == "PFL"
    assert fakedesk.value("/config/solo/busmode") == "AFL"  # untouched
    assert (await srv.get_routing())["solo"] == {"channels": "AFL", "buses": "AFL", "dcas": "PFL"}
    one = await srv.set_solo_mode(buses="pfl")
    assert one["ok"] and one["applied"] == {"buses": "PFL"} and one["summary"] == "Solo mode: buses AFL → PFL"
    assert_err(await srv.set_solo_mode(), "BAD_ARGUMENT")
    assert_err(await srv.set_solo_mode(buses="XFL"), "BAD_ARGUMENT")
    assert_err(await srv.set_solo_mode(channels=1), "BAD_ARGUMENT")
    await settle(app)
    assert fakedesk.value("/config/solo/busmode") == "PFL"


async def test_ramp_ms_is_bounded_and_budgeted_for_the_real_cadence(app):
    """ramp_ms is 0..60000 in every docstring — and the budget must outlast the ramp it covers."""
    for call in (
        srv.set_fader("ch.1", -3.0, ramp_ms=300_000),
        srv.adjust_fader("ch.1", -1.0, ramp_ms=300_000),
        srv.set_send("ch.1", 3, -10.0, ramp_ms=-1),
        srv.adjust_send("ch.1", 3, 1.0, ramp_ms=60_001),
        srv.set_main_fader("st", -10.0, ramp_ms=300_000),
    ):
        assert_err(await call, "BAD_ARGUMENT")
    assert_err(await srv.set_fader("ch.1", -3.0, ramp_ms=1.5), "BAD_ARGUMENT")
    # a ramp of N = ramp_ms/ramp_step_ms steps costs at least a step interval and a write slot each,
    # and on Windows the 20 ms sleep rounds up to the ~15.6 ms timer tick (measured ~1.67 x ramp_ms)
    for ms in (4_000, 30_000, 60_000):
        steps = app.policy.ramp_steps(0.0, -5.0, ms)
        assert srv._level_timeout(ms) > len(steps) * app.policy.ramp_step_s * 1.7, ms
    assert srv._level_timeout(0) == srv._level_timeout(None) == 15.0 + app.policy.ramp_step_s * 2.5
    res = await srv.set_fader("ch.5", -4.0, ramp_ms=1000)  # a real ramp finishes inside its budget
    assert res["ok"] and res["after_db"] == -4.0


async def test_a_ramp_cut_short_reports_ramp_aborted_not_timeout(app, monkeypatch):
    """The desk was answering; the move simply did not finish — say so, and say where it stopped."""
    monkeypatch.setattr(srv, "_level_timeout", lambda ms: 0.05)
    res = await srv.set_fader("ch.8", -6.0, ramp_ms=2000)
    assert_err(res, "RAMP_ABORTED")
    assert res["error"]["ramp_ms"] == 2000 and "read it back" in res["error"]["message"]
    assert "not answering" not in res["summary"]


async def test_setup_ringout_eqs_validation_failure_is_an_error_envelope(app, monkeypatch):
    """DESIGN §19 knows two ok:false shapes; a failed post-write check must use the error one."""
    async def failing_apply(desk, plan):
        return {"ok": False, "changed": 3, "geq": {"1": {"ok": False, "reasons": ["insert switched off"]}}}

    monkeypatch.setattr(srv._prov, "apply_setup", failing_apply)
    token = assert_pending(await srv.setup_ringout_eqs([1]))
    res = await srv.setup_ringout_eqs([1], confirm_token=token)
    assert_err(res, "GEQ_VALIDATION_FAILED")
    assert "insert switched off" in res["error"]["message"] and res["error"]["changed"] == 3
    assert "ok" not in res["error"] and "requires_confirmation" not in res


async def test_setup_ringout_eqs_on_a_desk_that_applies_late_is_ok_or_not_yet_verified(app, fakedesk, monkeypatch):
    """HANDOVER §4b: a successful setup reported GEQ_VALIDATION_FAILED because the desk showed the
    inserts only after it had answered the read-back. Late-but-in-time → plain ok (verified);
    later than the settle deadline → ok with verified False and a warning, never the error."""
    fakedesk.set_apply_delay("/bus/", 150)
    fakedesk.set_apply_delay("/fx/", 150)
    token = assert_pending(await srv.setup_ringout_eqs([1, 2]))
    done = await srv.setup_ringout_eqs([1, 2], confirm_token=token)
    assert done["ok"] is True and done["verified"] is True and done["changed"] == 3 and "all validate" in done["summary"]
    assert done["settle"]["attempts"] >= 2
    monkeypatch.setattr(srv._prov, "SETUP_SETTLE_S", 0.3)
    fakedesk.set_apply_delay("/bus/03/insert", 60_000)
    token = assert_pending(await srv.setup_ringout_eqs([3]))
    late = await srv.setup_ringout_eqs([3], confirm_token=token)
    assert late["ok"] is True and late["verified"] is False and "NOT YET VERIFIED" in late["summary"]
    assert late["warnings"] and "insert" in late["warnings"][0] and "error" not in late
    fakedesk.flush_pending()
    v = await srv.validate_ringout_eqs([3])
    assert v["all_ok"] is True  # and the stale answer was not left in the cache


async def test_reconnect_rearms_the_snapshot_before_write_net(app, fakedesk):
    """BRIEF §4 ties the undo net to the desk in front of us, not to the process."""
    assert (await srv.set_fader("ch.1", -6.0, ramp_ms=0))["ok"]
    assert app.policy.snapshot_before_write is False and app.desk.pre_write_snapshot is not None
    first = app.desk.pre_write_snapshot.id
    assert (await srv.connect(fakedesk.host, fakedesk.port))["ok"]
    assert app.policy.snapshot_before_write is True and app.desk.pre_write_snapshot is None
    assert (await srv.connection_status())["pre_write_snapshot"] is None
    assert (await srv.set_fader("ch.2", -6.0, ramp_ms=0))["ok"]
    assert app.desk.pre_write_snapshot is not None and app.desk.pre_write_snapshot.id != first


async def test_get_rta_target_writes_the_prefs_through_the_policy(app):
    """Pointing the RTA writes three Tier-1 desk parameters: they take the policy path."""
    writes: list = []
    unsub = app.events.subscribe(lambda ev: writes.append(ev.data), types={"desk.write"})
    try:
        rta = await srv.get_rta("bus.1", frames=2)
    finally:
        unsub()
    assert rta["ok"] and rta["rta_source"] == "bus.1"
    addrs = [w["address"] for w in writes]
    assert "/-prefs/rta/source" in addrs and "/-prefs/rta/pos" in addrs, addrs
    assert all(w["tier"] == 1 and w["tool"] == "get_rta" and w["target"] == "bus.1" for w in writes), writes
    assert app.policy.snapshot_before_write is False and app.desk.pre_write_snapshot is not None
    plain = await srv.get_rta(frames=2)  # no target: nothing is written
    assert plain["ok"] and plain["rta"] is None


async def test_ring_out_system_stages_obey_the_single_bus_limits(app):
    """A plan stage is not a back door around the notch budget / step size the tool enforces."""
    assert_err(await srv.ring_out_system({"stages": [{"bus": 1, "notch_budget": 40}]}), "BAD_ARGUMENT")
    assert_err(await srv.ring_out_system({"stages": [{"bus": 1, "step_db": 12.0}]}), "BAD_ARGUMENT")
    assert_err(await srv.ring_out_system({"stages": [{"bus": 1, "step_db": 0}]}), "BAD_ARGUMENT")
    assert_err(await srv.ring_out_system({"stages": [{"bus": 1, "dwell_ms": 90_000}]}), "BAD_ARGUMENT")
    ok = await srv.ring_out_system({"stages": [{"bus": 1, "notch_budget": 6, "step_db": 1.0, "dwell_ms": 10}]})
    assert assert_pending(ok) and ok["stages"][0]["notch_budget"] == 6


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


async def test_stdio_smoke_spawns_the_server(tmp_path):
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    from x32mcp.nodes import DeskState, SnapshotStore

    # a controlled X32MCP_HOME: the snapshot resource must read a file this test wrote, not
    # whatever the developer's repo happens to hold (on a fresh clone snapshots/ is empty)
    (tmp_path / "patches").mkdir(parents=True, exist_ok=True)
    shutil.copy(EXAMPLE, tmp_path / "patches" / EXAMPLE.name)
    state = DeskState(created="2026-09-20T09:00:00+00:00", console={"name": "X32-FAKE"},
                      scene={"index": 0, "name": "Init"}, sections={"/ch/01/mix": {"mix/on": True, "mix/fader": -6.0}})
    saved = SnapshotStore(tmp_path / "snapshots").save(state, "stdio smoke")
    # INFO logging on purpose: every log line must go to stderr, never onto the stdout wire
    env = {"X32MCP_DASH": "0", "X32MCP_LOG": "INFO", "X32_HOST": "", "PYTHONPATH": str(REPO / "src"),
           "X32MCP_HOME": str(tmp_path), "X32MCP_DEVICE_YAML": str(REPO / "device.yaml")}
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
        snap = json.loads((await c.read_resource("x32://snapshot/latest")).contents[0].text)
        assert "error" not in snap and snap["id"] == saved.id and snap["label"] == "stdio smoke"
        assert snap["state"]["sections"]["/ch/01/mix"]["mix/fader"] == -6.0
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


async def test_main_configures_stderr_so_unicode_log_records_survive(tmp_path):
    """A cp1252 stderr must not silently drop log lines containing − → ² (Windows default).

    logging discards any record its stream cannot encode, so without the reconfigure in
    main() the most interesting diagnostics (confirmations, notches, refusals) disappear.
    The subprocess runs the REAL entry point with only the transport stubbed out, so deleting
    the reconfigure from server.main() fails this test.
    """
    import os, subprocess, textwrap

    script = textwrap.dedent(
        """
        import io, logging, sys
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="cp1252", errors="strict")
        from x32mcp import server
        server.server.run = lambda *a, **k: None   # the stdio transport must not start
        server.main([])                            # the real entry point does the reconfigure
        logging.getLogger("x32mcp.t").info("fader \u22126.0 dB \u2192 \u22124.0 dB CFS\u00b2")
        """
    )
    env = {**os.environ, "PYTHONPATH": str(REPO / "src"), "X32MCP_DASH": "0", "X32MCP_LOG": "INFO",
           "X32_HOST": "", "X32MCP_HOME": str(tmp_path), "X32MCP_DEVICE_YAML": str(REPO / "device.yaml")}
    out = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, encoding="utf-8", timeout=60, env=env,
    )
    assert out.returncode == 0, out.stderr
    assert "Logging error" not in out.stderr, out.stderr
    assert "fader −6.0 dB → −4.0 dB CFS²" in out.stderr, out.stderr


async def test_main_bus_processing_is_guarded_and_makeup_is_clamped(app, fakedesk):
    """Red-team finding: +24 dB of compressor make-up (or +15 dB EQ) on Main LR through a Tier-1
    tool was one unconfirmed call. The PA bus's processing is guarded; make-up elsewhere is clamped."""
    assert_err(await srv.set_comp("main", makeup_db=24.0), "GUARDED")
    assert_err(await srv.set_comp("main.m", on=False), "GUARDED")
    assert_err(await srv.set_eq_band("main", 1, gain_db=15.0), "GUARDED")
    assert_err(await srv.set_eq_band("main.st", 2, on=False), "GUARDED")
    await settle(app)
    assert fakedesk.value("/main/st/dyn/mgain") != 24.0 and float(fakedesk.value("/main/st/eq/1/g")) != 15.0
    bus = await srv.set_comp("bus.1", makeup_db=24.0)
    assert bus["ok"] and bus["applied"]["makeup_db"] == 6.0 and bus["clamped"][0]["requested"] == 24.0
    ch = await srv.set_comp("ch.3", makeup_db=4.0)
    assert ch["ok"] and ch["applied"]["makeup_db"] == 4.0 and "clamped" not in ch

async def test_tokens_bind_what_the_user_was_shown(app, fakedesk, tmp_path):
    """A confirmation executes what its summary described. Reproduced red-team findings: the
    restore/patch tokens bound a path (not the file shown), ring_out_system(plan=None) bound
    {'plan': None} and re-derived the bus list at redeem time, ring_out did not bind the open-mic
    list the user is asked to check, and NaN stage arguments passed the dance."""
    # restore_snapshot: editing the snapshot file between the calls voids the token
    snap = await srv.snapshot_desk("t")
    tok = assert_pending(await srv.restore_snapshot(snap["id"]))
    p = Path(snap["path"])
    p.write_text(p.read_text().replace('"label": "t"', '"label": "t2"'))
    assert_err(await srv.restore_snapshot(snap["id"], confirm_token=tok), "BAD_TOKEN")
    # apply_patch_plan(include_source): same for the patch file
    plan = tmp_path / "patches" / "band.yaml"
    plan.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(EXAMPLE, plan)
    tok = assert_pending(await srv.apply_patch_plan(str(plan), include_source=True))
    plan.write_text(plan.read_text().replace("Ray", "NotRay", 1))
    assert_err(await srv.apply_patch_plan(str(plan), include_source=True, confirm_token=tok), "BAD_TOKEN")
    # ring_out_system(plan=None): a bus that becomes valid between the calls voids the token
    tok1 = assert_pending(await srv.setup_ringout_eqs([1]))
    assert (await srv.setup_ringout_eqs([1], confirm_token=tok1))["ok"]
    pend = await srv.ring_out_system()
    tok = assert_pending(pend)
    assert pend["stages"] == [{"bus": 1}]
    tok2 = assert_pending(await srv.setup_ringout_eqs([1, 2]))
    assert (await srv.setup_ringout_eqs([1, 2], confirm_token=tok2))["ok"]
    assert_err(await srv.ring_out_system(confirm_token=tok), "BAD_TOKEN")
    # ring_out_system stage arguments are validated before a token is minted
    assert_err(await srv.ring_out_system({"stages": [{"bus": 1, "target_gain_db": float("nan")}]}), "BAD_ARGUMENT")
    assert_err(await srv.ring_out_system({"stages": [{"bus": 1, "step_db": "lots"}]}), "BAD_ARGUMENT")
    # ring_out: the open-mic set is part of what was confirmed
    for ch in (1, 2):
        fset(app, fakedesk, f"/ch/{ch:02d}/mix/01/level", -20.0)
    fset(app, fakedesk, "/bus/01/mix/fader", -20.0)
    pend = await srv.ring_out(1, target_gain_db=-19.0, dwell_ms=10)
    tok = assert_pending(pend)
    assert "Ch 1" in pend["action_summary"] and "Ch 2" in pend["action_summary"]
    fset(app, fakedesk, "/ch/03/mix/01/level", -10.0)  # a third mic is opened onto the bus after the user said yes
    assert_err(await srv.ring_out(1, target_gain_db=-19.0, dwell_ms=10, confirm_token=tok), "BAD_TOKEN")


async def test_restore_preview_lists_hazards_first_and_reads_in_the_restore_direction(app, fakedesk):
    snap = await srv.snapshot_desk("safe")
    assert snap["ok"]
    for ch in range(1, 25):  # plenty of harmless changes that would fill a first-15 preview in sweep order
        fset(app, fakedesk, f"/ch/{ch:02d}/config/name", f"n{ch}")
    fset(app, fakedesk, "/main/st/mix/fader", -40.0)  # live is DOWN; the restore would bring it UP to 0 dB
    pend = await srv.restore_snapshot(snap["id"])
    assert_pending(pend)
    assert pend["hazardous"] >= 1
    first = pend["preview"].splitlines()[0]
    assert "Main LR" in first and "−40.0 dB → 0.0 dB" in first, first  # listed first, and reads live → snapshot
    assert "listed first" in pend["action_summary"]

async def test_argument_hardening_paths_and_ringout_bounds(app, fakedesk, tmp_path):
    """Report ids are ids, not paths; exports stay under the server's dirs; ring_out steps are bounded."""
    outside = tmp_path.parent / "elsewhere.json"
    outside.write_text('{"session_id": "x"}')
    assert_err(await srv.get_ringout_report("../" + outside.stem), "NOT_FOUND")
    assert_err(await srv.get_ringout_report(str(outside)), "NOT_FOUND")
    assert_err(await srv.export_patch_plan(str(tmp_path.parent / "escape.yaml")), "BAD_ARGUMENT")
    assert_err(await srv.export_patch_plan("/etc/passwd"), "BAD_ARGUMENT")
    ok = await srv.export_patch_plan("inside")  # bare name -> patches/inside.yaml
    assert ok["ok"] and ok["file"].endswith("inside.yaml")
    for ch in (1, 2):
        fset(app, fakedesk, f"/ch/{ch:02d}/mix/01/level", -20.0)
    fset(app, fakedesk, "/bus/01/mix/fader", -20.0)
    token = assert_pending(await srv.setup_ringout_eqs([1]))
    assert (await srv.setup_ringout_eqs([1], confirm_token=token))["ok"]
    assert_err(await srv.ring_out(1, step_db=5.0), "BAD_ARGUMENT")  # > ringout.max_step_db (3)


async def test_show_mode_off_disconnect_and_retarget_are_confirmed(app, fakedesk):
    """Red-team findings: show_mode(false) was self-service, disconnect()/connect(elsewhere) silently
    removed or re-targeted all control (panic included), and show mode's ±3 dB only covered adjust_*."""
    on = await srv.show_mode(True)
    assert on["ok"] and on["show_mode"] is True  # turning it ON is free
    # in show mode an absolute move is held to the relative limit too, unless forced
    fset(app, fakedesk, "/ch/07/mix/fader", -40.0)
    assert_err(await srv.set_fader("ch.7", 0.0, ramp_ms=0), "RELATIVE_TOO_LARGE")
    small = await srv.set_fader("ch.7", -38.0, ramp_ms=0)
    assert small["ok"]
    forced = await srv.set_fader("ch.7", -20.0, ramp_ms=0, force=True)
    assert forced["ok"]
    pend = await srv.show_mode(False)
    tok = assert_pending(pend)
    assert "Turn show mode OFF" in pend["action_summary"]
    assert app.policy.show_mode is True  # still on until confirmed
    off = await srv.show_mode(False, confirm_token=tok)
    assert off["ok"] and off["show_mode"] is False
    big = await srv.set_fader("ch.7", 0.0, ramp_ms=0)  # out of show mode absolute moves are free again (Jim's M5 decision)
    assert big["ok"]
    # disconnect while connected needs a token; so does re-targeting to another host
    pend = await srv.disconnect()
    tok = assert_pending(pend)
    assert "no panic()" in pend["action_summary"]
    assert app.conn.connected
    pend = await srv.connect("127.0.0.2", fakedesk.port)
    assert_pending(pend)
    assert "other console" in pend["action_summary"] and app.conn.connected
    same = await srv.connect(fakedesk.host, fakedesk.port)  # same desk again: free
    assert same["ok"]

async def test_discover_mics_flags_open_channels_with_no_input_signal(app, fakedesk):
    """HANDOVER §4b: discover_mics reported 7 candidates with one microphone plugged in — an unmuted,
    routed channel with a preamp looks identical either way. The input meters do not."""
    for ch in (1, 2):
        fset(app, fakedesk, f"/ch/{ch:02d}/mix/01/level", -20.0)
    fset(app, fakedesk, "/bus/01/mix/fader", -20.0)
    fakedesk.unplugged.add(2)  # channel 2: routed, unmuted, physical preamp — and nothing in the XLR
    res = await srv.discover_mics(1)
    assert res["ok"] and res["included"] == [1, 2] and res["silent"] == [2] and res["input_levels_sampled"] is True
    by_ch = {m["ch"]: m for m in res["mics"]}
    assert by_ch[1]["signal"] is True and by_ch[1]["input_db"] > -80
    assert by_ch[2]["signal"] is False and any("no input signal" in n for n in by_ch[2]["notes"])
    assert "NO INPUT SIGNAL" in res["summary"] and "probably nothing plugged in" in res["summary"]
    # the ring_out confirmation the user reads carries the same warning
    token = assert_pending(await srv.setup_ringout_eqs([1]))
    assert (await srv.setup_ringout_eqs([1], confirm_token=token))["ok"]
    pend = await srv.ring_out(1, target_gain_db=-19.0, dwell_ms=10)
    assert_pending(pend)
    assert "no input signal (ch 2)" in pend["action_summary"]
    fakedesk.unplugged.update({1})
    pend = await srv.ring_out(1, target_gain_db=-19.0, dwell_ms=10)
    assert "is anything plugged in?" in pend["action_summary"]
