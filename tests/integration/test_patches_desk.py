"""patches.py against a real Desk on the FakeDesk (DESIGN.md §16; unique basename because tests/ has
no __init__.py and pytest's prepend import mode rejects two test_patches.py). Applying the example plan
writes names/colours (not sources) to the desk, re-applying is free, ``include_source`` patches
sources, and an export merged with the plan round-trips through YAML and CSV."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from x32mcp.desk import Desk
from x32mcp.events import EventBus
from x32mcp.nodes import SnapshotStore
from x32mcp.patches import PatchRow, apply_patch_plan, build_patch_plan, export_patch_plan, load_patch_plan
from x32mcp.policy import Policy

EXAMPLE = Path(__file__).resolve().parent.parent.parent / "patches" / "example_band.yaml"


@pytest.fixture
def events() -> EventBus:
    return EventBus()


@pytest.fixture
def policy(descriptor, events) -> Policy:
    return Policy(descriptor, events)


@pytest_asyncio.fixture
async def desk(descriptor, conn, policy, events, tmp_path) -> Desk:
    d = Desk(descriptor, conn, policy, events, SnapshotStore(tmp_path / "snapshots"))
    try:
        yield d
    finally:
        await d.close()


async def settle(conn) -> None:
    """SETs are fire-and-forget: one GET round trip guarantees the fake desk processed them."""
    await conn.get("/-stat/selidx")


async def test_apply_example_labels_the_fakedesk(desk, conn, fakedesk, descriptor):
    plan = load_patch_plan(EXAMPLE, descriptor=descriptor)
    colours = descriptor.enum("color")
    sources = descriptor.enum("ch_source")
    before_sources = {r.channel: fakedesk.get(f"/ch/{r.channel:02d}/config/source") for r in plan.rows}
    res = await apply_patch_plan(desk, plan)
    await settle(conn)
    assert res["failed"] == [] and res["aborted"] is False and res["rows"] == 16
    assert [i["channel"] for i in res["applied"]] == list(range(1, 17))  # fake names are Ch01.. so every name differs
    for r in plan.rows:
        assert fakedesk.get(f"/ch/{r.channel:02d}/config/name") == r.name
        assert fakedesk.get(f"/ch/{r.channel:02d}/config/color") == colours.index(r.color)
        assert fakedesk.get(f"/ch/{r.channel:02d}/config/source") == before_sources[r.channel]  # Tier 2: untouched
    assert res["skipped_source"] == list(range(1, 17))
    assert desk.pre_write_snapshot is not None  # first Tier-1 write of the session took the auto snapshot
    ch = await desk.get_channel(13)
    assert ch["name"] == "Vox Lead" and ch["color"] == "MG" and ch["source"] == "IN13"
    # re-applying reads the desk and writes nothing
    writes = desk.write_count
    again = await apply_patch_plan(desk, plan)
    assert again["applied"] == [] and again["unchanged"] == list(range(1, 17)) and desk.write_count == writes
    # sources are patched only with include_source
    moved = build_patch_plan([PatchRow(16, "Talkback", "OFF", "AUX1", False, None, None, "moved to a local aux")], descriptor=descriptor)
    res = await apply_patch_plan(desk, moved)
    await settle(conn)
    assert res["unchanged"] == [16] and res["skipped_source"] == [16]
    assert fakedesk.get("/ch/16/config/source") == sources.index("IN16")
    res = await apply_patch_plan(desk, moved, include_source=True)
    await settle(conn)
    assert res["applied"] == [{"channel": 16, "target": "ch.16", "name": "Talkback", "color": "OFF", "changed": ["source"], "source": "AUX1"}]
    assert fakedesk.get("/ch/16/config/source") == sources.index("AUX1")
    assert (await desk.get_channel(16))["source"] == "AUX1"


async def test_export_round_trips_through_yaml_and_csv(desk, conn, fakedesk, descriptor, tmp_path):
    plan = load_patch_plan(EXAMPLE, descriptor=descriptor)
    await apply_patch_plan(desk, plan)
    await settle(conn)
    out = await export_patch_plan(desk, tmp_path / "export.yaml", merge_with=plan, channels=range(1, 17))
    assert out.rows == plan.rows and out.band == plan.band and out.venue == plan.venue
    assert load_patch_plan(tmp_path / "export.yaml", descriptor=descriptor).rows == plan.rows
    out_csv = await export_patch_plan(desk, tmp_path / "export.csv", merge_with=plan, channels=range(1, 17))
    assert load_patch_plan(tmp_path / "export.csv", descriptor=descriptor).rows == plan.rows
    assert out_csv.rows == plan.rows
    # a front-panel rename between export and re-export is picked up (fresh sweep, not the cache)
    fakedesk.set_value("/ch/01/config/name", "Kick In")
    fresh = await export_patch_plan(desk, tmp_path / "export2.yaml", merge_with=plan, channels=[1])
    assert fresh.rows == [PatchRow(1, "Kick In", "RD", "IN01", True, "Ray", 2, "inside kick, dynamic")]
    # default export: all 32 channels, desk values, no metadata
    full = await export_patch_plan(desk, tmp_path / "full.yaml")
    assert len(full.rows) == 32 and full.band is None
    assert full.row(20).name == "Ch20" and full.row(20).mic is None and full.row(20).source == "IN20"
