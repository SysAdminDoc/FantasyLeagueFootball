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


def _payload(html: str) -> dict:
    raw = re.search(r"const DATA = (\{.*?\});", html, re.DOTALL).group(1)
    return json.loads(raw)


def test_data_payload_round_trips(html):
    payload = _payload(html)
    assert len(payload["players"]) == 200
    assert payload["season"] == 2026
    assert payload["players"][0]["name"] == "Jahmyr Gibbs"


def test_payload_carries_tier_break_threshold(html):
    from fantasyleague.board import TIER_BREAK_THRESHOLD

    assert _payload(html)["tier_break"] == TIER_BREAK_THRESHOLD


def test_no_raw_angle_brackets_reach_the_inline_script(html):
    """`</script>` is the obvious break-out; `<!--<script` is the one that blanks the page."""
    body = html.split("const DATA = ", 1)[1].split(";\n", 1)[0]
    assert "</script" not in body
    assert "<" not in body and ">" not in body


def test_template_tokens_in_data_are_not_substituted():
    """Sequential replacement rewrote already-inlined content."""
    import dataclasses

    data = board.load()
    players = [dataclasses.replace(data.players[0], note="see __TITLE__ and __VERSION__"), *data.players[1:]]
    html = render.render(dataclasses.replace(data, players=players), title="My Board")
    assert _payload(html)["players"][0]["note"] == "see __TITLE__ and __VERSION__"
    assert "<title>My Board</title>" in html


def test_hostile_strings_survive_as_data(tmp_path):
    import dataclasses

    data = board.load()
    nasty = "x <!--<script> y </script> & <b>"
    players = [dataclasses.replace(data.players[0], note=nasty), *data.players[1:]]
    html = render.render(dataclasses.replace(data, players=players))
    body = html.split("const DATA = ", 1)[1].split(";\n", 1)[0]
    assert "<" not in body and ">" not in body
    assert _payload(html)["players"][0]["note"] == nasty


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
    assert "<title>Matt&#x27;s Board</title>" in html


def test_title_is_escaped():
    html = render.render(board.load(), title='<img src=x onerror="alert(1)">')
    assert "<img" not in html
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html


def test_league_shapes_title_and_payload():
    html = render.render(board.load(), league="Thursday Night")
    assert "<title>Thursday Night · 2026 Draft War Room</title>" in html
    raw = re.search(r"const DATA = (\{.*?\});", html, re.DOTALL).group(1)
    payload = json.loads(raw.replace("<\\/", "</"))
    assert payload["league"] == "Thursday Night"
    assert payload["board_id"].endswith("-thursday-night")


def test_csp_meta_present(html):
    assert '<meta http-equiv="Content-Security-Policy"' in html
    assert "default-src 'none'" in html


def test_board_id_stable_across_note_edits_but_not_reranks():
    from dataclasses import replace

    data = board.load()
    base = render.board_id(data)
    assert len(base) == 10

    edited = replace(data, players=[replace(data.players[0], note="new note"), *data.players[1:]])
    assert render.board_id(edited) == base, "a note edit must not orphan saved picks"

    swapped = replace(
        data,
        players=[replace(data.players[1], rank=1), replace(data.players[0], rank=2), *data.players[2:]],
    )
    assert render.board_id(swapped) != base, "a re-ranked board must get a fresh key"

    assert render.board_id(data, "League A") != render.board_id(data, "League B")
    assert render.board_id(data, "League A") == base + "-league-a"


def test_guidance_bold_markup_is_double_star_not_html():
    for item in board.load().plan:
        assert "<" not in item.guidance, "guidance must use **bold**, never HTML"
