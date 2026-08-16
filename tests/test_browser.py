"""Real-browser smoke test: the rendered board must boot, react, and persist.

Skipped when Playwright (or a Chromium build) isn't available so the core
suite stays dependency-free. Install with:

    pip install -e ".[dev]" && python -m playwright install chromium
"""

from __future__ import annotations

import pytest

from fantasyleague import board, render

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def page_url(tmp_path_factory):
    out = tmp_path_factory.mktemp("board") / "draft-board.html"
    render.write(board.load(), out)
    return out.resolve().as_uri()


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # no browser build installed
            pytest.skip(f"chromium unavailable: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser, page_url):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme="dark")
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(page_url)
    pg.wait_for_selector(".row")
    pg.errors = errors  # type: ignore[attr-defined]
    yield pg
    ctx.close()


def test_boots_without_js_errors(page):
    assert page.locator(".row").count() == 75
    assert page.errors == []
    assert page.locator("#bestAvail .ba-item").first.inner_text().startswith("1")


def test_click_crosses_off_and_updates_best_available(page):
    page.locator('.row[data-rk="1"]').click()
    assert page.locator('.row[data-rk="1"]').get_attribute("aria-pressed") == "true"
    assert page.locator(".row.gone").count() == 1
    assert "Bijan Robinson" in page.locator("#bestAvail .ba-item").first.inner_text()
    assert page.locator("#tally").inner_text().startswith("74")


def test_tier_break_fires_at_two_left(page):
    # Tier 2 holds ranks 6-13; take six of eight.
    for rk in range(6, 12):
        page.locator(f'.row[data-rk="{rk}"]').click()
    head = page.locator('.tier[data-tier="2"] .tier-left')
    assert head.inner_text().startswith("2 left")
    assert page.locator('.tier[data-tier="2"] .breakflag').count() == 1
    assert page.locator('.tier[data-tier="1"] .breakflag').count() == 0


def test_state_persists_across_reload(page):
    page.locator('.row[data-rk="3"]').click()
    page.reload()
    page.wait_for_selector(".row")
    assert page.locator('.row[data-rk="3"]').get_attribute("aria-pressed") == "true"


def test_position_filter_and_reset(page):
    page.locator('.chip[data-pos="QB"]').click()
    visible = page.locator(".row:not(.hide)")
    assert visible.count() == 7
    assert all(pos == "QB" for pos in visible.evaluate_all("els => els.map(e => e.dataset.pos)"))
    page.locator("#reset").click()
    page.locator('.chip[data-pos="ALL"]').click()
    assert page.locator(".row.gone").count() == 0


def test_light_theme_paints_its_own_ground(browser, page_url):
    ctx = browser.new_context(color_scheme="light")
    pg = ctx.new_page()
    pg.goto(page_url)
    pg.wait_for_selector(".row")
    bg = pg.evaluate("getComputedStyle(document.body).backgroundColor")
    ctx.close()
    assert bg not in ("rgba(0, 0, 0, 0)", "transparent")
    # Light ground token is #F2EDE4.
    assert bg == "rgb(242, 237, 228)"
