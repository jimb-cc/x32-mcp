"""Pure token parsing behind the routing tools (desk.py, no desk needed): User-In source numbers ↔
tokens (fx_routing_scenes.md §4.8: 0 OFF, 1..32 local XLR, 33..80 AES50-A, 81..128 AES50-B, 129..160
card, 161..166 aux in, 167/168 talkback), input block names, bus stereo-link pairs and routing enum tokens."""

from __future__ import annotations

import pytest

from x32mcp.descriptor import Descriptor
from x32mcp.desk import IN_BLOCKS, USER_IN_MAX, bus_link_pair, in_block, routing_token, user_in_number, user_in_source, user_in_token


@pytest.fixture(scope="module")
def d() -> Descriptor:
    return Descriptor.load()


# ---------------------------------------------------------------------------------------- user-in numbers


@pytest.mark.parametrize(
    "source,number",
    [
        ("OFF", 0), ("off", 0), (0, 0), ("0", 0),
        ("IN04", 4), ("in 4", 4), ("In04", 4), ("input 4", 4), ("local 4", 4), ("LOCAL4", 4), ("loc 4", 4), ("xlr 4", 4),
        ("IN01", 1), ("IN32", 32), ("local 32", 32), (4, 4), ("4", 4), ("004", 4),
        ("A01", 33), ("a1", 33), ("AES50-A 1", 33), ("aes50a 1", 33), ("AESA 1", 33), ("A48", 80), ("aes50-a 48", 80),
        ("B01", 81), ("b 1", 81), ("AES50-B 1", 81), ("B48", 128), ("aes50b48", 128),
        ("CARD01", 129), ("card 1", 129), ("usb 1", 129), ("CARD32", 160), ("usb32", 160),
        ("AUX1", 161), ("aux 1", 161), ("aux in 2", 162), ("AUXIN6", 166), ("AUX6", 166),
        ("TBINT", 167), ("tb int", 167), ("TB-INT", 167), ("tb internal", 167), ("TBEXT", 168), ("tb ext", 168), ("tb_external", 168),
        (168, 168), ("168", 168), (33, 33), (129, 129),
    ],
)
def test_user_in_number(source, number):
    assert user_in_number(source) == number


@pytest.mark.parametrize(
    "bad",
    ["", " ", "IN00", "IN33", "local 0", "A00", "A49", "B0", "B49", "CARD0", "CARD33", "usb 33", "AUX0", "AUX7", "TB", "ch 4", "channel 4",
     "USBL", "FX1L", "nope", "169", 169, -1, "-1", "+1", "- 1", True, False, 4.0, 4.5, None, [4], "A", "IN"],
)
def test_user_in_number_rejects(bad):
    with pytest.raises(ValueError):
        user_in_number(bad)


@pytest.mark.parametrize(
    "number,token,description",
    [
        (0, "OFF", "off"), (1, "IN01", "local XLR 1"), (4, "IN04", "local XLR 4"), (32, "IN32", "local XLR 32"),
        (33, "A01", "AES50-A 1"), (80, "A48", "AES50-A 48"), (81, "B01", "AES50-B 1"), (128, "B48", "AES50-B 48"),
        (129, "CARD01", "card/USB 1"), (160, "CARD32", "card/USB 32"), (161, "AUX1", "Aux In 1"), (166, "AUX6", "Aux In 6"),
        (167, "TBINT", "talkback internal"), (168, "TBEXT", "talkback external"),
    ],
)
def test_user_in_source(number, token, description):
    assert user_in_source(number) == (token, description)
    assert user_in_token(number) == token


def test_user_in_tokens_round_trip_and_are_unique():
    assert USER_IN_MAX == 168
    tokens = [user_in_token(n) for n in range(0, USER_IN_MAX + 1)]
    assert len(set(tokens)) == len(tokens) == 169
    for n, tok in enumerate(tokens):
        assert user_in_number(tok) == n and user_in_number(tok.lower()) == n and user_in_number(n) == n


@pytest.mark.parametrize("bad", [-1, 169, 1000, 4.0, True, None, "4", "IN04"])
def test_user_in_source_rejects(bad):
    with pytest.raises(ValueError):
        user_in_source(bad)


# ---------------------------------------------------------------------------------------- input blocks / bus pairs


@pytest.mark.parametrize(
    "block,key",
    [
        ("1-8", "1-8"), ("9-16", "9-16"), ("17-24", "17-24"), ("25-32", "25-32"), ("AUX", "AUX"),
        ("ch 17-24", "17-24"), ("CH17-24", "17-24"), ("IN/17-24", "17-24"), ("in 17-24", "17-24"), ("IN/1-8", "1-8"),
        ("inputs 17–24", "17-24"), ("Input 25 - 32", "25-32"), ("channels 9-16", "9-16"), (" 1-8 ", "1-8"),
        ("aux", "AUX"), ("Aux In", "AUX"), ("IN/AUX", "AUX"), ("auxin", "AUX"), ("in aux", "AUX"),
    ],
)
def test_in_block(block, key):
    assert in_block(block) == key
    assert key in IN_BLOCKS


@pytest.mark.parametrize("bad", ["", "33-40", "1-16", "1", "8", "ch", "in", "aux1-4", "AN1-8", "UIN1-8", 1, None, True, "1–16"])
def test_in_block_rejects(bad):
    with pytest.raises(ValueError):
        in_block(bad)


@pytest.mark.parametrize("bus,pair", [(1, (1, 2)), (2, (1, 2)), (3, (3, 4)), (4, (3, 4)), (15, (15, 16)), (16, (15, 16))])
def test_bus_link_pair(bus, pair):
    assert bus_link_pair(bus) == pair


@pytest.mark.parametrize("bad", [0, 17, -1, True, 1.0, "1", None])
def test_bus_link_pair_rejects(bad):
    with pytest.raises(ValueError):
        bus_link_pair(bad)


# ---------------------------------------------------------------------------------------- routing enum tokens


def test_routing_token_matches_the_descriptor_enums(d):
    tokens = d.enum("routing_in")
    assert routing_token("AN1-8", tokens) == "AN1-8"
    assert routing_token("an 1-8", tokens) == "AN1-8"
    assert routing_token("a17-24", tokens) == "A17-24"
    assert routing_token("UIN 17-24", tokens) == "UIN17-24"
    assert routing_token("card25-32", tokens) == "CARD25-32"
    aux = d.enum("routing_in_aux")
    assert routing_token("aux1-4", aux) == "AUX1-4" and routing_token("uin1-6", aux) == "UIN1-6" and routing_token("AN 1-2", aux) == "AN1-2"
    for bad in ("AUX1-4", "nope", "", "AN1-16", "UIN1-6"):
        with pytest.raises(ValueError):
            routing_token(bad, tokens)  # AUX-list tokens are not IN-block tokens and vice versa
    with pytest.raises(ValueError):
        routing_token("AN9-16", aux)
    with pytest.raises(ValueError):
        routing_token(3, tokens)
