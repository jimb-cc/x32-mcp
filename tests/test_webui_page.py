"""Static checks for webui/index.html (DESIGN.md §17).

The dashboard page is a single self-contained file (inline CSS + vanilla JS + canvas, no CDN) that
consumes the fixed WS message schema and runs a synthetic source with ``?demo=1``. These tests do not
execute JavaScript; they pin the contract a future edit must keep.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "webui" / "index.html"


@pytest.fixture(scope="module")
def html() -> str:
    assert PAGE.is_file(), f"missing {PAGE}"
    return PAGE.read_text(encoding="utf-8")


def test_title_and_meta(html: str) -> None:
    assert "<title>CFS² — x32-mcp</title>" in html
    assert re.search(r'<meta[^>]+charset="utf-8"', html, re.I)
    assert re.search(r'<meta[^>]+name="viewport"', html)


def test_self_contained_no_external_resources(html: str) -> None:
    assert not re.search(r"<script[^>]*\ssrc=", html, re.I), "external <script src> is not allowed"
    assert not re.search(r"<link[^>]*\shref=", html, re.I), "external <link href> is not allowed"
    assert "@import" not in html
    assert not re.search(r"url\(\s*['\"]?https?:", html, re.I)
    assert not re.search(r"https?://", html), "no URLs at all: the page must not reach the network except its own /ws"
    assert "<script>" in html and "<style>" in html


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.counts: dict[str, int] = {}
        self.ids: list[str] = []
        self.stack: list[str] = []
        self.unbalanced: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.counts[tag] = self.counts.get(tag, 0) + 1
        for k, v in attrs:
            if k == "id" and v:
                self.ids.append(v)
        if tag not in {"meta", "br", "img", "input", "link", "hr"}:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.unbalanced.append(tag)


def test_markup_structure(html: str) -> None:
    p = _Tags()
    p.feed(html)
    assert p.counts.get("title") == 1
    assert p.counts.get("script") == 1 and p.counts.get("style") == 1
    assert p.counts.get("canvas", 0) >= 2, "RTA and waterfall canvases"
    assert not p.unbalanced and not p.stack, (p.unbalanced, p.stack)
    assert len(p.ids) == len(set(p.ids)), "duplicate element ids"
    for i in ("rta", "wf", "banner", "modeBadge", "log", "master", "budget", "candBar", "connState", "rtaSrc", "chips"):
        assert i in p.ids, i


@pytest.mark.parametrize("msg_type", ["hello", "rta", "state", "notches", "event"])
def test_ws_message_types_handled(html: str, msg_type: str) -> None:
    assert re.search(rf"case\s*'{msg_type}'\s*:", html), f"dispatcher lacks case '{msg_type}'"


def test_schema_fields_referenced(html: str) -> None:
    for field in ("band_hz", "geq_band_hz", "version", "fps", "master_db", "budget_left", "candidate", "confidence",
                  "level_db", "session_id", "depth_db", "freq_hz", "rta_source", "bus_name", "connection", "rtt_ms",
                  "console", "items", "stage"):
        assert field in html, field


def test_demo_mode(html: str) -> None:
    assert re.search(r"get\(\s*'demo'\s*\)\s*===\s*'1'", html), "?demo=1 switch"
    assert "startDemo" in html
    assert "2400" in html and "630" in html, "ring at 2.4 kHz then 630 Hz"
    # every message type is synthesised by the demo source
    for t in ("'hello'", "'rta'", "'state'", "'notches'", "'event'"):
        assert re.search(r"t:\s*" + t, html), t


def test_websocket_reconnect_and_raf(html: str) -> None:
    assert "'/ws'" in html and "new WebSocket(" in html
    assert "onclose" in html and "onopen" in html and "onmessage" in html
    assert "DISCONNECTED" in html, "visible disconnected banner"
    assert "backoff" in html
    assert "requestAnimationFrame" in html


def test_views_present(html: str) -> None:
    for lab in ("'20'", "'50'", "'100'", "'200'", "'500'", "'1k'", "'2k'", "'5k'", "'10k'", "'20k'"):
        assert lab in html, f"axis tick label {lab}"
    assert "DB_LO = -90" in html and "DB_HI = 0" in html
    assert "drawImage(" in html and "createImageData(" in html, "offscreen-canvas waterfall"
    assert "LOG_MAX = 200" in html
    assert "getMilliseconds" in html, "HH:MM:SS.mmm timestamps"
    for mode in ("IDLE", "WATCH", "RING-OUT", "SYSTEM"):
        assert mode in html


def test_touch_tolerant_and_responsive(html: str) -> None:
    assert ":hover" not in html, "no hover-only information"
    assert "@media (max-width:1000px)" in html and "@media (max-width:600px)" in html
    assert 'content="dark"' in html


def test_size_budget(html: str) -> None:
    lines = html.count("\n") + 1
    assert lines <= 900, f"{lines} lines"
    assert len(html.encode("utf-8")) < 120_000
