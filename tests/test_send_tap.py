"""The pure helpers behind ``set_send_tap`` (desk.py) — the odd/even send-pair mapping and the tap
point spellings — plus the server tools' argument checks that need no desk at all."""

from __future__ import annotations

import pytest

from x32mcp.descriptor import Descriptor
from x32mcp.desk import DeskError, normalise_send_tap, send_pair

SEND_TYPE = ("IN/LC", "<-EQ", "EQ->", "PRE", "POST", "GRP")  # scales_params.md §4.9


@pytest.fixture(scope="module")
def d() -> Descriptor:
    return Descriptor.load()


# ---------------------------------------------------------------------------------------- pair mapping


@pytest.mark.parametrize("send,pair", [(1, (1, 2)), (2, (1, 2)), (3, (3, 4)), (4, (3, 4)), (9, (9, 10)), (15, (15, 16)), (16, (15, 16))])
def test_send_pair_maps_every_send_to_its_odd_partner(send, pair):
    """The X32 keeps one tap (and one pan) per pair, on the odd send: bus 4's tap is /mix/03/type."""
    assert send_pair(send) == pair
    assert send_pair(send)[0] % 2 == 1


def test_send_pair_honours_the_family_send_count():
    assert send_pair(1, 6) == (1, 2) and send_pair(6, 6) == (5, 6)  # bus/main → matrix: 6 sends
    for bad in (0, 17, -1, 3.5, True, "3", None):
        with pytest.raises(DeskError) as ei:
            send_pair(bad)
        assert ei.value.code == "BAD_ARGUMENT", bad
    with pytest.raises(DeskError) as ei:
        send_pair(7, 6)
    assert ei.value.code == "BAD_ARGUMENT" and "1..6" in str(ei.value)


# ---------------------------------------------------------------------------------------- tap spellings


def test_normalise_send_tap_accepts_tokens_aliases_and_any_case(d):
    assert tuple(d.enum("send_type")) == SEND_TYPE
    cases = [
        ("PRE", "PRE"), ("pre", "PRE"), (" Pre ", "PRE"), ("pre-fader", "PRE"), ("prefader", "PRE"),
        ("POST", "POST"), ("post", "POST"), ("Post fader", "POST"), ("post_fader", "POST"),
        ("IN/LC", "IN/LC"), ("in/lc", "IN/LC"), ("in", "IN/LC"), ("IN", "IN/LC"), ("input", "IN/LC"),
        ("<-EQ", "<-EQ"), ("<-eq", "<-EQ"), ("pre-eq", "<-EQ"), ("Pre EQ", "<-EQ"), ("pre_eq", "<-EQ"), ("preeq", "<-EQ"),
        ("EQ->", "EQ->"), ("eq->", "EQ->"), ("post-eq", "EQ->"), ("Post-EQ", "EQ->"), ("post  eq", "EQ->"), ("posteq", "EQ->"),
        ("GRP", "GRP"), ("grp", "GRP"), ("Group", "GRP"), ("subgroup", "GRP"), ("sub", "GRP"),
    ]
    for text, token in cases:
        assert normalise_send_tap(text, SEND_TYPE) == token, text


def test_normalise_send_tap_rejects_unknown_values(d):
    for bad in ("bogus", "", "   ", "pre-post", "eq", "fader", 3, 3.0, None, True, ["pre"]):
        with pytest.raises(DeskError) as ei:
            normalise_send_tap(bad, SEND_TYPE)
        assert ei.value.code == "BAD_ARGUMENT", bad
        assert "PRE" in str(ei.value) and "pre-eq" in str(ei.value)  # the message lists what is accepted


def test_normalise_send_tap_only_offers_what_the_send_has(d):
    """bus/main → matrix sends have no GRP (device.yaml send_type_mtx): the alias resolves but is refused."""
    mtx = tuple(d.enum("send_type_mtx"))
    assert "GRP" not in mtx and mtx == SEND_TYPE[:5]
    assert normalise_send_tap("pre", mtx) == "PRE" and normalise_send_tap("post-eq", mtx) == "EQ->"
    for text in ("grp", "GRP", "subgroup"):
        with pytest.raises(DeskError) as ei:
            normalise_send_tap(text, mtx)
        assert ei.value.code == "BAD_ARGUMENT" and "GRP" in str(ei.value) and "not available" in str(ei.value)


# ---------------------------------------------------------------------------------------- tool argument checks


async def test_tools_refuse_an_empty_request_before_touching_the_desk(monkeypatch):
    """Nothing to set is BAD_ARGUMENT even with no server app at all — the check precedes the desk;
    arguments the desk itself validates (bus number, tap token) wait for one (NOT_READY here)."""
    from x32mcp import server as srv

    monkeypatch.setattr(srv, "app", None)
    for res in (await srv.set_send("ch.1", 3), await srv.set_main_assign("ch.1")):
        assert res["ok"] is False and res["error"]["code"] == "BAD_ARGUMENT" and "nothing to set" in res["error"]["message"], res
    assert (await srv.set_send("ch.1", 3, on=True))["error"]["code"] == "NOT_READY"
    assert (await srv.set_send_tap("ch.1", 4, "pre"))["error"]["code"] == "NOT_READY"
    assert (await srv.label_bus(3, name="x"))["error"]["code"] == "NOT_READY"
