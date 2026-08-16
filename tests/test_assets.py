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

    labels = re.search(r"FLAG_LABEL = \{(.*?)\}", js, re.S).group(1)
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
