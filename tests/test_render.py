"""The rendered board must be self-contained and safe to open offline."""

from __future__ import annotations

import json
import re

import pytest

from fantasyleague import __version__, board, render


@pytest.fixture(scope="module")
def html():
    return render.render(board.load())


def test_no_unreplaced_tokens(html):
    leftovers = re.findall(r"__[A-Z]+__", html)
    assert not leftovers, f"template tokens survived: {set(leftovers)}"


def test_is_a_complete_document(html):
    assert html.lstrip().startswith("<!doctype html>")
    assert html.rstrip().endswith("</html>")
    assert "<title>2026 Draft War Room</title>" in html


def test_assets_are_inlined_not_linked(html):
    assert "<link" not in html
    assert 'src="' not in html
    assert "--ground:" in html, "CSS did not inline"
    assert "buildBoard" in html, "JS did not inline"


def test_no_external_requests(html):
    for scheme in ("http://", "https://cdn", "//fonts."):
        assert scheme not in html.replace("https://www.", "").replace("https://sports.", "")


def test_data_payload_round_trips(html):
    raw = re.search(r"const DATA = (\{.*?\});", html, re.S).group(1)
    payload = json.loads(raw.replace("<\\/", "</"))
    assert len(payload["players"]) == 75
    assert payload["season"] == 2026
    assert payload["players"][0]["name"] == "Jahmyr Gibbs"


def test_script_tags_cannot_break_out_of_payload(html):
    body = html.split("const DATA = ", 1)[1].split(";\n", 1)[0]
    assert "</script" not in body


def test_both_themes_are_defined(html):
    assert "prefers-color-scheme: light" in html
    assert ':root[data-theme="light"]' in html
    assert "background: var(--ground)" in html, "body must paint its own ground"


def test_version_is_stamped(html):
    assert f"FantasyLeagueFootball v{__version__}" in html


def test_write_creates_parent_dirs(tmp_path):
    out = tmp_path / "nested" / "deeper" / "board.html"
    path = render.write(board.load(), out)
    assert path.exists()
    assert path.read_text("utf-8").startswith("<!doctype html>")


def test_custom_title(tmp_path):
    html = render.render(board.load(), title="Matt's Board")
    assert "<title>Matt's Board</title>" in html
