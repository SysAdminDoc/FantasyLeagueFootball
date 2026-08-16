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
    assert page.locator(".row").count() == 99
    assert page.errors == []
    assert page.locator("#bestAvail .ba-item").first.inner_text().startswith("1")


def test_click_crosses_off_and_updates_best_available(page):
    page.locator('.row[data-rk="1"]').click()
    assert page.locator('.row[data-rk="1"]').get_attribute("aria-pressed") == "true"
    assert page.locator(".row.gone").count() == 1
    assert "Bijan Robinson" in page.locator("#bestAvail .ba-item").first.inner_text()
    assert page.locator("#tally").inner_text().startswith("98")


def test_tier_break_fires_at_two_left(page):
    # Tier 2 holds ranks 6-13; take six of eight.
    for rk in range(6, 12):
        page.locator(f'.row[data-rk="{rk}"]').click()
    head = page.locator('.tier[data-tier="2"] .tier-left')
    assert head.inner_text().startswith("2 left")
    assert page.locator('.tier[data-tier="2"] .breakflag').count() == 1
    assert page.locator('.tier[data-tier="1"] .breakflag').count() == 0


def test_tier_break_ignores_position_filter(page):
    # Tier 3 holds exactly one TE (Bowers) among seven players. Filtering to TE must
    # show the filtered count without claiming the tier is breaking.
    page.locator('.chip[data-pos="TE"]').click()
    head = page.locator('.tier[data-tier="3"] .tier-left')
    assert head.inner_text().startswith("1 left · 7 in tier")
    assert page.locator('.tier[data-tier="3"] .breakflag').count() == 0
    # Drain the tier for real (all positions) and the badge appears even while filtered.
    page.locator('.chip[data-pos="ALL"]').click()
    for rk in (14, 15, 16, 18, 19):
        page.locator(f'.row[data-rk="{rk}"]').click()
    page.locator('.chip[data-pos="TE"]').click()
    assert page.locator('.tier[data-tier="3"] .breakflag').count() == 1
    assert page.locator('.tier[data-tier="3"] .tier-left').inner_text().startswith("1 left · 2 in tier")


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


def _live(page) -> str:
    # The announcer clears then sets on a 0 ms timer; give it a tick.
    page.wait_for_timeout(50)
    return page.locator("#live").text_content()


def test_live_region_is_empty_on_load_and_announces_actions(page):
    assert page.locator("#live").get_attribute("aria-live") == "polite"
    assert page.locator("#live").text_content() == ""
    page.locator('.row[data-rk="1"]').click()
    assert _live(page) == "Jahmyr Gibbs crossed off. Best available: Bijan Robinson."
    page.locator('.row[data-rk="1"]').click()
    assert _live(page) == "Jahmyr Gibbs restored. Best available: Jahmyr Gibbs."


def test_live_region_announces_tier_break_once(page):
    for rk in (1, 2, 3):
        page.locator(f'.row[data-rk="{rk}"]').click()
    assert "Tier 1 is down to 2 — tier break." in _live(page)
    page.locator('.row[data-rk="4"]').click()
    assert "tier break" not in _live(page), "a tier already broken must not re-announce"


def test_enter_in_search_crosses_off_single_match(page):
    search = page.locator("#search")
    assert search.get_attribute("placeholder") == "Search, then Enter to cross off"
    search.fill("gibbs")
    search.press("Enter")
    assert page.locator('.row[data-rk="1"]').get_attribute("aria-pressed") == "true"
    assert search.input_value() == "", "a successful cross-off clears the search"
    assert page.locator(".row:not(.hide)").count() == 99

    search.fill("brown")  # Chase Brown, A.J. Brown, Amon-Ra St. Brown
    search.press("Enter")
    assert page.locator(".row.gone").count() == 1, "ambiguous match must not cross anyone off"
    assert "players match — keep typing" in _live(page)

    search.fill("zzzz")
    search.press("Enter")
    assert "No player matches" in _live(page)


def test_guidance_renders_bold_and_neutralises_markup(browser, tmp_path):
    from dataclasses import replace

    from fantasyleague.models import PlanItem

    data = board.load()
    hostile = replace(
        data,
        plan=[PlanItem("Running back", 'Get **three** RBs <img src=x onerror="window.pwned=1"> now')],
    )
    out = tmp_path / "hostile.html"
    render.write(hostile, out)
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.goto(out.resolve().as_uri())
    pg.wait_for_selector(".plan-cell")
    cell = pg.locator(".plan-cell p").first
    assert cell.locator("strong").inner_text() == "three"
    assert cell.locator("img").count() == 0
    assert '<img src=x onerror="window.pwned=1">' in cell.inner_text()
    assert pg.evaluate("window.pwned") is None
    ctx.close()


def test_storage_failure_shows_notice_and_board_still_works(browser, page_url):
    ctx = browser.new_context()
    ctx.add_init_script(
        "Object.defineProperty(window, 'localStorage', { get() { throw new Error('blocked'); } });"
    )
    pg = ctx.new_page()
    pg.goto(page_url)
    pg.wait_for_selector(".row")
    note = pg.locator("#storenote")
    assert note.is_visible()
    assert "isn't saving your picks" in note.inner_text()
    pg.locator('.row[data-rk="1"]').click()
    assert pg.locator(".row.gone").count() == 1
    ctx.close()


def test_two_leagues_keep_separate_state(browser, tmp_path):
    data = board.load()
    a = tmp_path / "a.html"
    b = tmp_path / "b.html"
    render.write(data, a, league="Alpha")
    render.write(data, b, league="Beta")
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.goto(a.resolve().as_uri())
    pg.wait_for_selector(".row")
    pg.locator('.row[data-rk="1"]').click()
    pg.goto(b.resolve().as_uri())
    pg.wait_for_selector(".row")
    assert pg.locator(".row.gone").count() == 0
    assert "Beta" in pg.locator("#eyebrow").text_content()  # inner_text is CSS-uppercased
    ctx.close()


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
