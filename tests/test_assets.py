"""The template and the script must agree on the DOM contract.

There is no browser in the test environment, so these checks stand in for the
"does the page actually boot" question: every element board.js reaches for has
to exist in the template, or the first getElementById returns null and the whole
board renders blank.
"""

from __future__ import annotations

import re
from importlib import resources

import pytest


def _asset(name: str) -> str:
    return resources.files("fantasyleague").joinpath("assets", name).read_text("utf-8")


@pytest.fixture(scope="module")
def js():
    return _asset("board.js")


@pytest.fixture(scope="module")
def template():
    return _asset("board.html.template")


@pytest.fixture(scope="module")
def css():
    return _asset("board.css")


def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    channels = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _palette(css: str, selector: str) -> dict[str, str]:
    """Tokens declared in one theme block."""
    body = css.split(selector, 1)[1].split("}", 1)[0]
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", body))


@pytest.mark.parametrize("selector", [":root {", ':root[data-theme="light"] {'])
def test_secondary_text_meets_wcag_aa_on_every_surface(css, selector):
    """--ink-dim carries notes, tier ranges and card headings at 11-12.5px.

    It has to clear 4.5:1 on the darkest/lightest surface it lands on, including
    --raise (the row hover), not just on the page ground.
    """
    p = _palette(css, selector)
    for surface in ("ground", "surface", "surface-2", "raise"):
        ratio = contrast(p["ink-dim"], p[surface])
        assert ratio >= 4.5, f"{selector} --ink-dim on --{surface} is {ratio:.2f}:1"


@pytest.mark.parametrize("selector", [":root {", ':root[data-theme="light"] {'])
def test_status_colours_are_readable_on_their_own_backgrounds(css, selector):
    p = _palette(css, selector)
    for fg, bg in (("good", "good-bg"), ("warn", "warn-bg"), ("avoid", "avoid-bg")):
        ratio = contrast(p[fg], p[bg])
        assert ratio >= 4.5, f"{selector} --{fg} on --{bg} is {ratio:.2f}:1"
    for token in ("accent", "good", "warn", "avoid"):
        ratio = contrast(p[token], p["ground"])
        assert ratio >= 4.5, f"{selector} --{token} on --ground is {ratio:.2f}:1"
    ratio = contrast(p["accent-ink"], p["accent"])
    assert ratio >= 4.5, f"{selector} --accent-ink on --accent is {ratio:.2f}:1"


def test_both_light_palettes_stay_identical(css):
    """The media-query palette and the [data-theme] one must not drift apart."""
    assert _palette(css, ':root:not([data-theme="dark"]) {') == _palette(css, ':root[data-theme="light"] {')


def test_themes_declare_color_scheme(css):
    """Without it the UA paints number spinners and scrollbars in the wrong theme."""
    assert "color-scheme: dark;" in css
    # Both light palettes declare it; `prefers-color-scheme: light)` must not count.
    assert css.count("color-scheme: light;") == 2


def test_toggles_have_a_pressed_style(css):
    assert '.ghost[aria-pressed="true"]' in css, "toggle buttons need a visible on-state"
    assert ".ghost:hover { color: var(--avoid)" not in css, "only Reset should read as destructive"
    assert "#reset:hover" in css


def test_print_flattens_status_colours(css):
    """Screen greens and ambers print at ~2:1 on white."""
    block = css.split("@media print", 1)[1]
    tokens = block.split(':root[data-theme="light"] {', 1)[1].split("}", 1)[0]
    for token in ("--good", "--warn", "--avoid"):
        assert f"{token}: #000" in tokens, f"{token} prints at ~2:1 on white unless flattened"


def test_every_referenced_id_exists_in_template(js, template):
    wanted = set(re.findall(r'getElementById\("([^"]+)"\)', js))
    present = set(re.findall(r'id="([^"]+)"', template))
    assert wanted, "expected board.js to query some elements"
    assert wanted <= present, f"board.js queries missing IDs: {sorted(wanted - present)}"


def test_every_queried_class_exists_somewhere(js, css):
    """Selectors board.js relies on should have styling behind them."""
    for selector in (".row", ".tier", ".chip", ".tier-left"):
        assert selector in css, f"{selector} is queried by JS but unstyled"


def test_template_ids_are_unique(template):
    ids = re.findall(r'id="([^"]+)"', template)
    assert len(ids) == len(set(ids)), "duplicate id attributes in template"


def test_flag_labels_cover_every_flag(js):
    from fantasyleague.models import FLAGS

    labels = re.search(r"FLAG_LABEL = \{(.*?)\}", js, re.DOTALL).group(1)
    for flag in FLAGS:
        assert f"{flag}:" in labels, f"board.js has no label for flag {flag!r}"


def test_severity_classes_cover_every_severity(css):
    from fantasyleague.models import SEVERITIES

    for sev in SEVERITIES:
        assert f".st-{sev}" in css, f"board.css has no style for severity {sev!r}"


def test_tier_break_threshold_comes_from_data(js):
    """JS must read the threshold from the payload, not carry its own copy."""
    assert "DATA.tier_break" in js
    assert not re.search(r"var TIER_BREAK = \d+;", js), "hardcoded threshold reintroduced in board.js"


def test_storage_key_is_board_scoped(js):
    assert 'KEY = "ff-warroom-" + DATA.board_id' in js, "storage key must be scoped to the board identity"


def test_js_escapes_interpolated_text(js):
    assert "function esc(" in js
    assert "&amp;" in js and "&lt;" in js


def test_no_console_or_debugger_left_behind(js):
    assert "console.log" not in js
    assert "debugger" not in js
