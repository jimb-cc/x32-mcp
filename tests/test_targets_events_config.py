import asyncio
from pathlib import Path

import pytest

from x32mcp.config import Settings
from x32mcp.events import EventBus
from x32mcp.targets import Target, TargetError, parse_target


@pytest.mark.parametrize(
    "text,key,prefix,label",
    [
        ("ch.5", "ch.5", "/ch/05", "Ch 5"),
        ("CH5", "ch.5", "/ch/05", "Ch 5"),
        ("channel 12", "ch.12", "/ch/12", "Ch 12"),
        ("7", "ch.7", "/ch/07", "Ch 7"),
        (7, "ch.7", "/ch/07", "Ch 7"),
        ("bus.3", "bus.3", "/bus/03", "Bus 3"),
        ("aux 3", "bus.3", "/bus/03", "Bus 3"),
        ("Bus 16", "bus.16", "/bus/16", "Bus 16"),
        ("auxin.2", "auxin.2", "/auxin/02", "Aux In 2"),
        ("fxrtn.1", "fxrtn.1", "/fxrtn/01", "FX Rtn 1"),
        ("mtx.6", "mtx.6", "/mtx/06", "Matrix 6"),
        ("matrix 1", "mtx.1", "/mtx/01", "Matrix 1"),
        ("dca.8", "dca.8", "/dca/8", "DCA 8"),
        ("main", "main.st", "/main/st", "Main LR"),
        ("main.st", "main.st", "/main/st", "Main LR"),
        ("LR", "main.st", "/main/st", "Main LR"),
        ("main.m", "main.m", "/main/m", "Main M/C"),
        ("mono", "main.m", "/main/m", "Main M/C"),
    ],
)
def test_parse_target(text, key, prefix, label):
    t = parse_target(text)
    assert t.key == key
    assert t.osc_prefix == prefix
    assert t.label == label
    assert parse_target(t) is t


@pytest.mark.parametrize("bad", ["", "ch.0", "ch.33", "bus.17", "dca.9", "main.x", "foo.1", "ch.x", True, 3.5, "bus"])
def test_parse_target_rejects(bad):
    with pytest.raises(TargetError):
        parse_target(bad)


def test_target_validation():
    with pytest.raises(TargetError):
        Target("ch", 0)
    with pytest.raises(TargetError):
        Target("main", "lr")
    assert Target("main", "m").is_main


def test_event_bus_publish_subscribe_recent():
    bus = EventBus(history=3, clock=lambda: 1.0)
    seen = []
    unsub = bus.subscribe(lambda e: seen.append(e.type), types={"a"})
    bus.publish("a", x=1)
    bus.publish("b")
    bus.publish("meters.frame", values=[0.0])  # transient: not retained
    assert seen == ["a"]
    assert [e.type for e in bus.recent()] == ["a", "b"]
    unsub()
    bus.publish("a")
    assert seen == ["a"]
    assert [e.type for e in bus.recent(types={"a"})] == ["a", "a"]
    assert bus.recent()[0].to_dict() == {"ts": 1.0, "type": "a", "data": {"x": 1}}


def test_event_bus_bad_subscriber_does_not_break_publish():
    bus = EventBus()

    def boom(_):
        raise RuntimeError("x")

    bus.subscribe(boom)
    ev = bus.publish("a")
    assert ev.type == "a"


async def test_event_bus_stream():
    bus = EventBus()

    async def consume():
        out = []
        async for ev in bus.stream(types={"a"}):
            out.append(ev.data["n"])
            if len(out) == 2:
                return out

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    bus.publish("a", n=1)
    bus.publish("b", n=99)
    bus.publish("a", n=2)
    assert await asyncio.wait_for(task, 2) == [1, 2]


def test_settings_defaults_and_env(tmp_path: Path):
    s = Settings.from_env({})
    assert s.home.name == "x32-mcp"
    assert s.device_yaml == s.home / "device.yaml"
    assert s.x32_host is None and s.x32_port == 10023
    assert s.dash_host == "0.0.0.0" and s.dash_port == 8032 and s.dash_enabled
    s2 = Settings.from_env({"X32MCP_HOME": str(tmp_path), "X32_HOST": "10.0.0.5", "X32_PORT": "bad", "X32MCP_DASH": "off", "X32MCP_LOG": "debug"})
    assert s2.home == tmp_path.resolve()
    assert s2.snapshot_dir == tmp_path.resolve() / "snapshots"
    assert s2.x32_host == "10.0.0.5" and s2.x32_port == 10023
    assert s2.dash_enabled is False and s2.log_level == "DEBUG"
    s2.ensure_dirs()
    assert s2.report_dir.is_dir()
