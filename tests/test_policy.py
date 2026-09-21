"""policy.py — clamps, relative limits, tokens, rate limiter (fake clock), show mode, notches, ramps
(DESIGN.md §10). Limits come from the real device.yaml."""

from __future__ import annotations

import asyncio
import copy
import math
import time

import pytest

from x32mcp.descriptor import Descriptor
from x32mcp.events import EventBus
from x32mcp.policy import (
    FADER_FLOOR_DB,
    SHOW_MODE_BLOCKED,
    Clamped,
    PendingConfirmation,
    Policy,
    PolicyError,
    Tier,
)
from x32mcp.scales import NEG_INF_DB
from x32mcp.targets import Target

INF = NEG_INF_DB


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture(scope="module")
def d() -> Descriptor:
    return Descriptor.load()


def with_policy(d: Descriptor, **overrides) -> Descriptor:
    """Shallow copy of the descriptor with some `policy` values replaced (the shared one is untouched)."""
    d2 = copy.copy(d)
    d2.policy = {**d.policy, **overrides}
    return d2


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def pol(d, bus, clock) -> Policy:
    return Policy(d, bus, clock=clock)


# ---------------------------------------------------------------------------------------------- construction


def test_reads_limits_from_descriptor(pol, d):
    p = d.policy
    assert pol.ch_fader_max_db == p["ch_fader_max_db"] == 5
    assert pol.bus_fader_max_db == p["bus_fader_max_db"] == 0
    assert pol.main_fader_max_db == 0 and pol.send_max_db == 0
    assert pol.eq_gain_abs_max_db == 15 and pol.relative_max_db == 6 and pol.relative_max_db_show_mode == 3
    assert pol.ramp_default_ms == 300 and pol.ramp_step_ms == 20 and pol.ramp_step_s == 0.02
    assert pol.writes_per_second == 50 and pol.confirm_token_ttl_s == 300  # 5 min: conversational round trip (M5)
    assert pol.notch_max_db == -9
    assert pol.show_mode is False  # show_mode_default
    assert pol.snapshot_before_write is True
    assert "show_mode=False" in repr(pol)


def test_show_mode_default_and_event(d, bus, clock):
    p = Policy(with_policy(d, show_mode_default=True), bus, clock=clock)
    assert p.show_mode is True
    seen = []
    bus.subscribe(lambda e: seen.append(e), types={"policy.show_mode"})
    p.show_mode = True  # no change → no event
    assert seen == []
    p.show_mode = False
    assert len(seen) == 1 and seen[0].data == {"on": False}


def test_bad_writes_per_second_rejected(d, bus):
    with pytest.raises(ValueError):
        Policy(with_policy(d, writes_per_second=0), bus)


# ---------------------------------------------------------------------------------------------- tiers


def test_tier_for(pol):
    assert pol.tier_for("/ch/05/mix/fader") is Tier.MIX
    assert pol.tier_for("/ch/05/config/name") == Tier.MIX == 1
    assert pol.tier_for("/config/solo/level") is Tier.READ
    # guarded globs
    assert pol.tier_for("/main/st/mix/fader") is Tier.GUARDED
    assert pol.tier_for("/main/m/mix/on") is Tier.GUARDED
    assert pol.tier_for("/headamp/005/phantom") is Tier.GUARDED
    assert pol.tier_for("/ch/05/config/source") is Tier.GUARDED
    assert pol.tier_for("/ch/01/insert/sel") is Tier.GUARDED
    assert pol.tier_for("/-action/goscene") is Tier.GUARDED
    assert pol.tier_for("/save") is Tier.GUARDED and pol.tier_for("/load") is Tier.GUARDED
    # unknown addresses are at least a mix move
    assert pol.tier_for("/nope/xyz") is Tier.MIX
    assert isinstance(pol.tier_for("/ch/05/mix/fader"), Tier)


# ---------------------------------------------------------------------------------------------- clamps


@pytest.mark.parametrize(
    "target, req, expect_value, expect_limit",
    [
        (Target("ch", 5), 8.0, 5.0, 5.0),
        (Target("ch", 5), 5.0, 5.0, None),
        (Target("ch", 5), 3.2, 3.2, None),
        (Target("auxin", 2), 6.0, 5.0, 5.0),
        (Target("fxrtn", 1), 9.9, 5.0, 5.0),
        (Target("dca", 3), 7.0, 0.0, 0.0),  # DCA: bus-like 0 dB ceiling (it raises every member strip)
        (Target("bus", 3), 2.0, 0.0, 0.0),
        (Target("bus", 3), -3.0, -3.0, None),
        (Target("mtx", 1), 1.0, 0.0, 0.0),
        (Target("main", "st"), 1.0, 0.0, 0.0),
        (Target("main", "m"), 0.0, 0.0, None),
        (Target("ch", 1), -90.0, -90.0, None),
        (Target("ch", 1), -100.0, INF, FADER_FLOOR_DB),
        (Target("ch", 1), INF, INF, None),
        (Target("ch", 1), math.inf, 5.0, 5.0),
    ],
)
def test_clamp_level_faders(pol, target, req, expect_value, expect_limit):
    value, clamped = pol.clamp_level(target, req)
    assert value == expect_value
    if expect_limit is None:
        assert clamped is None
    else:
        assert isinstance(clamped, Clamped)
        assert clamped.value == expect_value and clamped.requested == req and clamped.limit == expect_limit
        assert clamped.reason.startswith("CLAMPED_TO_LIMIT")
        assert clamped.to_dict()["limit"] == expect_limit


def test_clamp_level_sends_use_send_ceiling_for_every_family(pol):
    for t in (Target("ch", 5), Target("auxin", 1), Target("bus", 2), Target("main", "st")):
        value, clamped = pol.clamp_level(t, 3.0, kind="send")
        assert value == 0.0 and clamped is not None
        assert (clamped.value, clamped.requested, clamped.limit) == (0.0, 3.0, 0.0)
        assert pol.clamp_level(t, -12.0, kind="send") == (-12.0, None)
        assert pol.clamp_level(t, INF, kind="send") == (INF, None)
    assert pol.level_ceiling_db(Target("ch", 1), kind="send") == 0.0
    assert pol.level_ceiling_db(Target("ch", 1)) == 5.0


def test_clamp_level_accepts_ints_rejects_nan(pol):
    assert pol.clamp_level(Target("ch", 1), 2) == (2.0, None)
    with pytest.raises(PolicyError) as ei:
        pol.clamp_level(Target("ch", 1), math.nan)
    assert ei.value.code == "NOT_ALLOWED"
    with pytest.raises(PolicyError):
        pol.clamp_level(Target("ch", 1), "loud")  # type: ignore[arg-type]


def test_clamp_eq_gain(pol):
    assert pol.clamp_eq_gain(3.0) == (3.0, None)
    assert pol.clamp_eq_gain(-15.0) == (-15.0, None)
    v, c = pol.clamp_eq_gain(20.0)
    assert v == 15.0 and c is not None and c.limit == 15.0 and c.requested == 20.0
    v, c = pol.clamp_eq_gain(-20.0)
    assert v == -15.0 and c is not None and c.limit == -15.0


# ---------------------------------------------------------------------------------------------- relative limit


def test_check_relative_limits(pol):
    pol.check_relative(6.0)
    pol.check_relative(-6.0)
    pol.check_relative(0.0)
    assert pol.relative_limit_db == 6.0
    for delta in (6.01, -7.0, 40.0, math.inf):
        with pytest.raises(PolicyError) as ei:
            pol.check_relative(delta)
        assert ei.value.code == "RELATIVE_TOO_LARGE"
        assert ei.value.to_dict()["limit_db"] == 6.0
        pol.check_relative(delta, force=True)  # force bypasses


def test_check_relative_show_mode(pol):
    pol.show_mode = True
    assert pol.relative_limit_db == 3.0
    pol.check_relative(3.0)
    with pytest.raises(PolicyError) as ei:
        pol.check_relative(4.0)
    assert ei.value.code == "RELATIVE_TOO_LARGE" and "show mode" in ei.value.message
    pol.check_relative(4.0, force=True)  # DESIGN decision: force bypasses even in show mode
    pol.show_mode = False
    pol.check_relative(4.0)


# ---------------------------------------------------------------------------------------------- tokens


def test_token_lifecycle_single_use(pol, bus):
    events = []
    bus.subscribe(lambda e: events.append(e), types={"policy.confirm_required", "policy.confirmed"})
    pend = pol.require_confirmation("Recall scene 7 'Molecules'", {"index": 7})
    assert isinstance(pend, PendingConfirmation)
    assert pend.requires_confirmation is True and pend.expires_in_s == 300
    assert pend.action_summary == "Recall scene 7 'Molecules'"
    assert len(pend.confirm_token) >= 8
    assert pend.to_dict() == {
        "requires_confirmation": True, "action_summary": "Recall scene 7 'Molecules'",
        "confirm_token": pend.confirm_token, "expires_in_s": 300,
    }
    assert pol.pending_confirmations == 1
    assert pol.consume_token(pend.confirm_token) == {"index": 7}
    assert pol.pending_confirmations == 0
    with pytest.raises(PolicyError) as ei:
        pol.consume_token(pend.confirm_token)  # single use
    assert ei.value.code == "BAD_TOKEN"
    assert [e.type for e in events] == ["policy.confirm_required", "policy.confirmed"]
    assert all(pend.confirm_token not in str(e.data) for e in events)  # token never published


def test_tokens_are_unique_and_wrong_token_rejected(pol):
    a = pol.require_confirmation("a", {"x": 1})
    b = pol.require_confirmation("b", {"x": 2})
    assert a.confirm_token != b.confirm_token
    for bad in ("nope", "", None):
        with pytest.raises(PolicyError) as ei:
            pol.consume_token(bad)  # type: ignore[arg-type]
        assert ei.value.code == "BAD_TOKEN"
    assert pol.consume_token(b.confirm_token) == {"x": 2}
    assert pol.consume_token(a.confirm_token) == {"x": 1}


def test_token_ttl_uses_injected_clock(pol, clock):
    pend = pol.require_confirmation("save", {"index": 3})
    clock.advance(299.9)
    other = pol.require_confirmation("save again", {"index": 4})
    clock.advance(0.2)  # first token is now 300.1 s old, second 0.2 s
    with pytest.raises(PolicyError) as ei:
        pol.consume_token(pend.confirm_token)
    assert ei.value.code == "TOKEN_EXPIRED"
    with pytest.raises(PolicyError) as ei:  # deleted on failure too
        pol.consume_token(pend.confirm_token)
    assert ei.value.code == "BAD_TOKEN"
    assert pol.consume_token(other.confirm_token) == {"index": 4}


def test_expired_tokens_are_swept(pol, clock):
    for i in range(5):
        pol.require_confirmation(f"t{i}", {"i": i})
    assert pol.pending_confirmations == 5
    clock.advance(301)
    assert pol.pending_confirmations == 0


def test_guard_dance(pol):
    payload = {"index": 12, "name": "GravelAxe"}
    first = pol.guard("scene_save", "Save scene 12 as 'GravelAxe'", payload, None)
    assert isinstance(first, PendingConfirmation)
    assert first.action_summary == "Save scene 12 as 'GravelAxe'"
    empty = pol.guard("scene_save", "Save scene 12 as 'GravelAxe'", payload, "")  # "" counts as no token
    assert isinstance(empty, PendingConfirmation)
    second = pol.guard("scene_save", "Save scene 12 as 'GravelAxe'", payload, first.confirm_token)
    assert second == payload and not isinstance(second, PendingConfirmation)
    with pytest.raises(PolicyError) as ei:  # single use
        pol.guard("scene_save", "…", payload, first.confirm_token)
    assert ei.value.code == "BAD_TOKEN"


def test_guard_rejects_token_for_other_action_or_payload(pol):
    pend = pol.guard("scene_recall", "Recall scene 7", {"index": 7}, None)
    with pytest.raises(PolicyError) as ei:
        pol.guard("scene_save", "Save scene 7", {"index": 7}, pend.confirm_token)
    assert ei.value.code == "BAD_TOKEN" and "scene_recall" in ei.value.message
    with pytest.raises(PolicyError) as ei:  # deleted on that failure
        pol.guard("scene_recall", "Recall scene 7", {"index": 7}, pend.confirm_token)
    assert ei.value.code == "BAD_TOKEN"

    pend = pol.guard("scene_recall", "Recall scene 7", {"index": 7}, None)
    with pytest.raises(PolicyError) as ei:  # same action, different request
        pol.guard("scene_recall", "Recall scene 8", {"index": 8}, pend.confirm_token)
    assert ei.value.code == "BAD_TOKEN" and "different" in ei.value.message

    # consume_token with expected_action on a token minted without one (require_confirmation) is lenient
    pend = pol.require_confirmation("x", {"a": 1})
    assert pol.consume_token(pend.confirm_token, expected_action="anything") == {"a": 1}


def test_guard_token_expiry(pol, clock):
    pend = pol.guard("set_main_fader", "Main LR to -6 dB", {"db": -6.0}, None)
    clock.advance(301)
    with pytest.raises(PolicyError) as ei:
        pol.guard("set_main_fader", "Main LR to -6 dB", {"db": -6.0}, pend.confirm_token)
    assert ei.value.code == "TOKEN_EXPIRED"


# ---------------------------------------------------------------------------------------------- show mode


def test_show_mode_blocks(pol):
    for action in sorted(SHOW_MODE_BLOCKED) + ["recall_scene", "save_scene", "restore_snapshot", "set_fader"]:
        pol.check_show_mode_allows(action)  # off: everything allowed
    pol.show_mode = True
    for action in sorted(SHOW_MODE_BLOCKED) + ["recall_scene", "save_scene"]:
        with pytest.raises(PolicyError) as ei:
            pol.check_show_mode_allows(action)
        assert ei.value.code == "SHOW_MODE_BLOCKS" and ei.value.to_dict()["action"] == action
    for action in ("restore_snapshot", "set_fader", "panic", "snapshot_desk", "feedback_watch"):
        pol.check_show_mode_allows(action)


# ---------------------------------------------------------------------------------------------- rate limiter


class FakeSleep:
    """Records sleeps; optionally advances a FakeClock (sequential callers) or not (a burst)."""

    def __init__(self, clock: FakeClock | None = None) -> None:
        self.clock = clock
        self.calls: list[float] = []

    async def __call__(self, s: float) -> None:
        self.calls.append(s)
        if self.clock is not None:
            self.clock.advance(s)


async def test_rate_limiter_burst_then_rate_limited(d, bus, clock):
    sleep = FakeSleep()  # clock frozen: a burst of concurrent callers within one instant
    pol = Policy(with_policy(d, writes_per_second=5), bus, clock=clock, sleep=sleep)
    seen = []
    bus.subscribe(lambda e: seen.append(e), types={"policy.rate_limited"})
    for _ in range(5):  # capacity = 5 → free
        await pol.acquire_write()
    assert sleep.calls == []
    assert pol.write_slots_available == 0
    for i in range(1, 6):  # 6th..10th queue at 0.2 s spacing, the 10th waits exactly 1.0 s (allowed)
        await pol.acquire_write()
        assert sleep.calls[-1] == pytest.approx(0.2 * i)
    with pytest.raises(PolicyError) as ei:  # 11th would wait 1.2 s
        await pol.acquire_write()
    assert ei.value.code == "RATE_LIMITED" and ei.value.to_dict()["wait_s"] == pytest.approx(1.2)
    assert len(seen) == 1 and seen[0].data["wait_s"] == pytest.approx(1.2)
    with pytest.raises(PolicyError):  # a refused call reserved nothing → still refused
        await pol.acquire_write()
    assert pol.write_slots_available == pytest.approx(-5)
    await pol.acquire_write(panic=True)  # panic bypasses, touches nothing
    assert pol.write_slots_available == pytest.approx(-5) and len(sleep.calls) == 5
    clock.advance(1.0)  # queued reservations drain
    assert pol.write_slots_available == pytest.approx(0)
    await pol.acquire_write()
    assert sleep.calls[-1] == pytest.approx(0.2)
    clock.advance(10)  # refills to capacity, never beyond
    assert pol.write_slots_available == 5
    await pol.acquire_write()
    assert len(sleep.calls) == 6


async def test_rate_limiter_sequential_steady_state(d, bus, clock):
    sleep = FakeSleep(clock)  # sequential caller: the clock advances while we sleep
    pol = Policy(with_policy(d, writes_per_second=5), bus, clock=clock, sleep=sleep)
    t0 = clock()
    for _ in range(25):
        await pol.acquire_write()
    # 5 free, 20 spaced at 0.2 s → exactly 4 s of waiting, never RATE_LIMITED
    assert clock() - t0 == pytest.approx(4.0)
    assert len(sleep.calls) == 20 and all(s == pytest.approx(0.2) for s in sleep.calls)


async def test_rate_limiter_design_numbers(d, bus, clock):
    """DESIGN §10: 50/s. With a frozen clock the 51st write queues (20 ms), the 101st is refused."""
    sleep = FakeSleep()
    pol = Policy(d, bus, clock=clock, sleep=sleep)
    for _ in range(50):
        await pol.acquire_write()
    assert sleep.calls == []
    await pol.acquire_write()
    assert sleep.calls == [pytest.approx(0.02)]
    for _ in range(49):
        await pol.acquire_write()
    assert sleep.calls[-1] == pytest.approx(1.0)
    with pytest.raises(PolicyError) as ei:
        await pol.acquire_write()
    assert ei.value.code == "RATE_LIMITED"


async def test_rate_limiter_real_clock(d, bus):
    pol = Policy(with_policy(d, writes_per_second=20), bus)  # real monotonic clock + asyncio.sleep
    t0 = time.monotonic()
    for _ in range(24):
        await pol.acquire_write()
    elapsed = time.monotonic() - t0
    assert 0.15 <= elapsed < 1.5  # 4 queued writes × 50 ms
    # a concurrent burst of 25 more: 20 fit within the 1 s wait window (some sleep), the rest are refused
    results = await asyncio.gather(*(pol.acquire_write() for _ in range(25)), return_exceptions=True)
    refused = [r for r in results if isinstance(r, PolicyError)]
    assert refused and all(r.code == "RATE_LIMITED" for r in refused)
    assert len(refused) < 25


# ---------------------------------------------------------------------------------------------- notches


def test_validate_notch_cuts_only(pol):
    pol.validate_notch(0.0, -3.0)
    pol.validate_notch(-3.0, -6.0)
    pol.validate_notch(-6.0, -9.0)
    pol.validate_notch(-3.0, -3.0)  # no-op is fine
    pol.validate_notch(0.0, 0.0)
    pol.validate_notch(-9.0, -9.0000000001)  # float noise at the limit
    for cur, new in ((0.0, 1.0), (0.0, 0.5), (-6.0, -3.0), (-3.0, 0.0), (2.0, 1.0)):
        with pytest.raises(PolicyError) as ei:
            pol.validate_notch(cur, new)
        assert ei.value.code == "BOOST_FORBIDDEN", (cur, new)
    for cur, new in ((-9.0, -12.0), (0.0, -9.5), (-6.0, -100.0), (-9.0, INF)):
        with pytest.raises(PolicyError) as ei:
            pol.validate_notch(cur, new)
        assert ei.value.code == "NOT_ALLOWED", (cur, new)
        assert ei.value.to_dict()["limit_db"] == -9


def test_validate_notch_limit_from_detector_section(d, bus):
    d2 = copy.copy(d)
    d2.detector = {**d.detector, "notch_max_db": -12}
    p = Policy(d2, bus)
    p.validate_notch(-9.0, -12.0)
    with pytest.raises(PolicyError):
        p.validate_notch(-12.0, -15.0)


# ---------------------------------------------------------------------------------------------- ramps


def test_ramp_steps_default_is_15_steps(pol):
    steps = pol.ramp_steps(0.0, -6.0, 300)
    assert len(steps) == 15 and steps[-1] == -6.0
    assert steps[0] == pytest.approx(-0.4)
    assert all(b < a for a, b in zip(steps, steps[1:]))  # monotonic
    assert pol.ramp_steps(0.0, -6.0, None) == steps  # None → default 300 ms
    assert pol.ramp_steps(-6.0, 0.0, 300)[-1] == 0.0 and len(pol.ramp_steps(-6.0, 0.0, 300)) == 15
    assert len(pol.ramp_steps(-20.0, -19.0, 1000)) == 50


def test_ramp_steps_short_and_degenerate(pol):
    assert pol.ramp_steps(0.0, -6.0, 0) == [-6.0]
    assert pol.ramp_steps(0.0, -6.0, -5) == [-6.0]
    assert pol.ramp_steps(0.0, -6.0, 20) == [-6.0]
    assert pol.ramp_steps(0.0, -6.0, 30) == [-3.0, -6.0]  # 1.5 steps rounds up
    assert pol.ramp_steps(0.0, -6.0, 29) == [-6.0]  # 1.45 rounds down
    assert pol.ramp_steps(-3.0, -3.01, 300) == [-3.01]  # below 0.05 dB → one step
    assert pol.ramp_steps(-3.0, -3.0, 300) == [-3.0]
    assert pol.ramp_steps(None, -6.0, 300) == [-6.0]  # unknown start
    assert pol.ramp_steps(math.nan, -6.0, 300) == [-6.0]
    assert pol.ramp_steps(0.0, -6.0, 300.0) == pol.ramp_steps(0.0, -6.0, 300)  # float ms tolerated


def test_ramp_steps_neg_inf(pol):
    up = pol.ramp_steps(INF, 0.0, 300)
    assert len(up) == 15 and up[0] == pytest.approx(-84.0) and up[-1] == 0.0
    down = pol.ramp_steps(0.0, INF, 300)
    assert len(down) == 15 and down[-2] == pytest.approx(-84.0) and down[-1] == INF
    assert pol.ramp_steps(INF, INF, 300) == [INF]
    assert pol.ramp_steps(-90.0, INF, 300) == [INF]  # -90 and -oo are the same stop
    assert pol.ramp_steps(INF, -6.0, 300)[-1] == -6.0
    with pytest.raises(PolicyError):
        pol.ramp_steps(0.0, math.nan, 300)


def test_ramp_steps_respects_step_ms(d, bus):
    p = Policy(with_policy(d, ramp_step_ms=50, ramp_default_ms=500), bus)
    assert len(p.ramp_steps(0.0, -10.0, None)) == 10
    assert p.ramp_steps(0.0, -10.0, 100) == [-5.0, -10.0]
    assert p.ramp_step_s == 0.05


# ---------------------------------------------------------------------------------------------- snapshot flag


def test_snapshot_before_write_flag(pol):
    assert pol.snapshot_before_write is True
    pol.mark_snapshot_taken()
    assert pol.snapshot_before_write is False
    pol.mark_snapshot_taken()  # idempotent
    assert pol.snapshot_before_write is False


# ---------------------------------------------------------------------------------------------- errors


def test_policy_error_shape():
    e = PolicyError("RATE_LIMITED", "slow down", wait_s=1.2)
    assert e.code == "RATE_LIMITED" and e.message == "slow down"
    assert str(e) == "RATE_LIMITED: slow down"
    assert e.to_dict() == {"code": "RATE_LIMITED", "message": "slow down", "wait_s": 1.2}
    assert PolicyError("X", "y").to_dict() == {"code": "X", "message": "y"}
    assert isinstance(e, Exception)
