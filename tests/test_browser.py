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
        except Exception as exc:  # noqa: BLE001 - any launch failure means skip
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
    assert page.locator(".row").count() == 200
    assert page.errors == []
    assert page.locator("#bestAvail .ba-item").first.inner_text().startswith("1")


def test_click_crosses_off_and_updates_best_available(page):
    page.locator('.row[data-rk="1"]').click()
    assert page.locator('.row[data-rk="1"]').get_attribute("aria-pressed") == "true"
    assert page.locator(".row.gone").count() == 1
    assert "Bijan Robinson" in page.locator("#bestAvail .ba-item").first.inner_text()
    assert page.locator("#tally").inner_text().startswith("199")


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
    assert visible.count() == 25
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
    assert page.locator(".row:not(.hide)").count() == 200

    search.fill("brown")  # Chase Brown, A.J. Brown, Amon-Ra St. Brown
    search.press("Enter")
    assert page.locator(".row.gone").count() == 1, "ambiguous match must not cross anyone off"
    assert "players match — keep typing" in _live(page)

    search.fill("zzzz")
    search.press("Enter")
    assert "No player matches" in _live(page)


def test_pick_counter_and_odds_follow_slot(page):
    info = page.locator("#pickinfo")
    assert info.inner_text() == "Set your slot for pick odds"
    assert page.locator(".odds").count() == 0
    page.locator("#slot").fill("5")
    assert info.inner_text() == "Pick 1 · yours in 4 (#5)"
    # Every undrafted player with an ADP shows odds at picks 5 and 20.
    first = page.locator('.row[data-rk="1"] .odds')
    assert first.count() == 1
    txt = first.inner_text()
    assert "%" in txt and "·" in txt
    # Gibbs (ADP ~1.5, sd .7) is essentially gone by pick 5.
    assert txt.startswith("0%")
    for rk in (1, 2, 3, 4):
        page.locator(f'.row[data-rk="{rk}"]').click()
    assert info.inner_text() == "Pick 5 · your pick"
    assert "mine" in info.get_attribute("class")
    # Odds recompute for picks 20 and 29 now; rail shows odds too.
    assert page.locator("#bestAvail .odds").count() >= 1
    page.reload()
    page.wait_for_selector(".row")
    assert page.locator("#slot").input_value() == "5", "slot persists per board"


def test_odds_match_python_math(page):
    from fantasyleague import board as board_mod
    from fantasyleague import draft

    page.locator("#slot").fill("5")  # next pick 5, then 20
    data = board_mod.load()
    p = next(x for x in data.players if x.adp is not None and 15 <= x.adp <= 25)
    shown = page.locator(f'.row[data-rk="{p.rank}"] .odds').inner_text()
    a5 = round(draft.availability(p.adp, p.adp_sd, 5) * 100)
    a20 = round(draft.availability(p.adp, p.adp_sd, 20) * 100)
    want = f"{a5}% · {a20}%"
    assert shown == want


def test_toast_offers_undo_for_cross_off(page):
    page.locator('.row[data-rk="1"]').click()
    toast = page.locator("#toast")
    assert toast.is_visible()
    assert page.locator("#toastMsg").inner_text() == "Jahmyr Gibbs crossed off"
    page.locator("#toastUndo").click()
    assert page.locator('.row[data-rk="1"]').get_attribute("aria-pressed") == "false"
    assert not toast.is_visible()
    assert page.locator(".row.gone").count() == 0


def test_reset_is_undoable(page):
    for rk in (1, 2, 3):
        page.locator(f'.row[data-rk="{rk}"]').click()
    page.locator("#reset").click()
    assert page.locator(".row.gone").count() == 0
    assert "3 picks cleared" in page.locator("#toastMsg").inner_text()
    page.locator("#toastUndo").click()
    assert page.locator(".row.gone").count() == 3
    page.reload()
    page.wait_for_selector(".row")
    assert page.locator(".row.gone").count() == 3, "undo must persist"


def test_positional_run_marks_the_chip(page):
    # RB, WR, RB, RB -> 3 of the last 4 are RB.
    for rk in (1, 3, 2, 6):
        page.locator(f'.row[data-rk="{rk}"]').click()
    assert "run" in page.locator('.chip[data-pos="RB"]').get_attribute("class")
    assert "run" not in page.locator('.chip[data-pos="WR"]').get_attribute("class")
    assert "RB run: 3 of the last 4 picks." in _live(page)
    # Two WRs later the window has moved on and the run clears.
    for rk in (4, 5):
        page.locator(f'.row[data-rk="{rk}"]').click()
    assert "run" not in page.locator('.chip[data-pos="RB"]').get_attribute("class")


def test_v1_storage_array_is_migrated(browser, page_url):
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.goto(page_url)
    pg.wait_for_selector(".row")
    key = pg.evaluate("'ff-warroom-' + DATA.board_id")
    pg.evaluate("([k]) => localStorage.setItem(k, JSON.stringify(['1','2','7']))", [key])
    pg.reload()
    pg.wait_for_selector(".row")
    assert pg.locator(".row.gone").count() == 3
    # first save after migration rewrites in v2 form; trigger one
    pg.locator('.row[data-rk="9"]').click()
    stored = pg.evaluate("([k]) => JSON.parse(localStorage.getItem(k))", [key])
    assert stored["v"] == 2 and [e["rank"] for e in stored["log"]] == [1, 2, 7, 9]
    ctx.close()


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


def test_print_media_is_a_compact_monochrome_sheet(browser, page_url, tmp_path):
    import re

    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.goto(page_url)
    pg.wait_for_selector(".row")
    pg.locator('.row[data-rk="1"]').click()
    pg.emulate_media(media="print")
    assert not pg.locator(".rail").is_visible()
    assert not pg.locator(".controls").is_visible()
    assert pg.locator(".row:visible").count() == 200
    assert pg.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(255, 255, 255)"
    assert pg.evaluate("getComputedStyle(document.body).color") == "rgb(0, 0, 0)"
    # Value flag becomes a glyph, not a coloured pill.
    glyph = pg.evaluate("getComputedStyle(document.querySelector('.f-value'), '::after').content")
    assert glyph == '"▲"'
    pdf = tmp_path / "sheet.pdf"
    pg.pdf(path=str(pdf), format="Letter")
    pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf.read_bytes()))
    # 200 players at a readable size is one double-sided sheet; more than that
    # means the print layout has regressed to something you would not carry.
    assert pages <= 2, f"cheat sheet should fit one double-sided sheet, got {pages} pages"
    ctx.close()


def test_served_board_syncs_across_tabs_and_http(browser):
    import http.client
    import json

    from fantasyleague import serve

    with serve.BoardServer(board.load(), port=0) as s:
        url = f"http://127.0.0.1:{s.port}/"
        ctx = browser.new_context()
        a = ctx.new_page()
        a.goto(url)
        a.wait_for_selector(".row")
        b = ctx.new_page()
        b.goto(url)
        b.wait_for_selector(".row")
        a.wait_for_selector("#livepill:not([hidden])")
        assert "following" in a.locator("#livepill").inner_text().lower()

        # A click in tab A reaches the server and tab B.
        a.locator('.row[data-rk="1"]').click()
        b.wait_for_selector('.row[data-rk="1"].gone', timeout=3000)
        assert s.bus.state()["picks"][0]["rank"] == 1

        # A pick POSTed by an external source (e.g. a sync process) reaches both tabs.
        c = http.client.HTTPConnection("127.0.0.1", s.port, timeout=5)
        c.request("POST", "/state", body=json.dumps({"pick": {"name": "bijan"}}),
                  headers={"Content-Type": "application/json", "X-Source": "sleeper"})
        assert c.getresponse().status == 200
        c.close()
        a.wait_for_selector('.row[data-rk="2"].gone', timeout=3000)
        b.wait_for_selector('.row[data-rk="2"].gone', timeout=3000)
        assert "Bijan Robinson crossed off (sleeper)." in _live(a)

        # Undo in tab B propagates to A and the server.
        b.locator('.row[data-rk="2"]').click()  # restore
        a.wait_for_selector('.row[data-rk="2"]:not(.gone)', timeout=3000)
        assert [p["rank"] for p in s.bus.state()["picks"]] == [1]

        # A fresh tab gets the server state, not stale local storage.
        d = ctx.new_page()
        d.goto(url)
        d.wait_for_selector('.row[data-rk="1"].gone', timeout=3000)
        assert d.locator(".row.gone").count() == 1
        ctx.close()


def test_sleeper_sync_reaches_the_browser(browser, monkeypatch):
    """Full chain: a Sleeper poll crosses a player off in an open tab."""
    from fantasyleague import serve
    from fantasyleague.sync import sleeper

    picks = [{"player_id": "4984", "pick_no": 1}]  # Josh Allen -> rank 31
    monkeypatch.setattr(sleeper, "fetch_picks", lambda _d, timeout=10.0: picks)

    with serve.BoardServer(board.load(), port=0) as s:
        ctx = browser.new_context()
        pg = ctx.new_page()
        pg.goto(f"http://127.0.0.1:{s.port}/")
        pg.wait_for_selector(".row")
        pg.wait_for_selector("#livepill:not([hidden])")

        sync = sleeper.SleeperSync(board.load(), "abc", s.bus, interval=0.05).start()
        try:
            pg.wait_for_selector('.row[data-rk="31"].gone', timeout=5000)
        finally:
            sync.stop()
        assert s.bus.state()["picks"][0]["source"] == "sleeper"
        assert "Josh Allen crossed off (sleeper)." in _live(pg)
        ctx.close()


def _mine_label(page) -> str:
    # .toast-mine is text-transform: uppercase, so inner_text() would be "THAT'S MINE".
    return page.locator("#toastMine").text_content()


def test_bye_weeks_show_on_rows(page):
    bye = page.locator('.row[data-rk="1"] .bye')
    assert bye.count() == 1
    assert bye.inner_text().startswith("B")
    assert bye.get_attribute("title").startswith("Bye week ")


def test_roster_tracks_your_picks_and_shows_needs(page):
    card = page.locator("#rostercard")
    assert card.is_hidden(), "no roster card until you own a pick"

    # With no slot set, a cross-off is somebody else's pick.
    page.locator('.row[data-rk="1"]').click()
    assert card.is_hidden()

    # Claim it via the toast.
    page.locator("#toastMine").click()
    assert card.is_visible()
    assert "Jahmyr Gibbs" in page.locator("#roster").inner_text()
    needs = page.locator("#needs").inner_text()
    assert needs.startswith("Still needs") and "QB" in needs and "TE" in needs
    assert "mine" in page.locator('.row[data-rk="1"]').get_attribute("class")

    # And un-claim it.
    page.locator("#toastMine").click()
    assert card.is_hidden()


def test_your_pick_is_claimed_automatically_when_the_slot_is_known(page):
    page.locator("#slot").fill("1")          # slot 1 => pick 1 is yours
    page.locator('.row[data-rk="1"]').click()
    assert page.locator("#rostercard").is_visible()
    assert "Jahmyr Gibbs" in page.locator("#roster").inner_text()
    assert "added to your roster" in _live(page)
    # Pick 2 belongs to somebody else and is not claimed.
    page.locator('.row[data-rk="2"]').click()
    assert "Bijan Robinson" not in page.locator("#roster").inner_text()


def test_flex_fills_only_after_dedicated_slots(page):
    page.locator("#slot").fill("1")
    # Three RBs: two fill RB/RB, the third takes FLEX — not the reverse.
    for rk in (1, 2, 6):
        row = page.locator(f'.row[data-rk="{rk}"]')
        row.click()
        if _mine_label(page) == "That's mine":
            page.locator("#toastMine").click()
    text = page.locator("#roster").inner_text()
    assert text.count("RB") >= 2
    lines = [ln for ln in text.split("\n") if ln.strip()]
    flex = next(ln for ln in lines if ln.startswith("FLEX"))
    assert "—" not in flex, "third RB should occupy FLEX"


def test_bye_cluster_warning(page):
    page.locator("#slot").fill("1")
    data = board.load()
    # Find three players sharing a bye week.
    from collections import defaultdict

    weeks = defaultdict(list)
    for p in data.players:
        if p.bye:
            weeks[p.bye].append(p)
    week, group = next((w, g) for w, g in weeks.items() if len(g) >= 3)

    warn = page.locator("#byewarn")
    for p in group[:3]:
        page.locator(f'.row[data-rk="{p.rank}"]').click()
        if _mine_label(page) == "That's mine":
            page.locator("#toastMine").click()
    assert warn.is_visible()
    assert f"Week {week}" in warn.inner_text()
    assert "3 of your players are on bye" in warn.inner_text()


def test_roster_survives_reload(page):
    page.locator("#slot").fill("1")
    page.locator('.row[data-rk="1"]').click()
    page.reload()
    page.wait_for_selector(".row")
    assert page.locator("#rostercard").is_visible()
    assert "Jahmyr Gibbs" in page.locator("#roster").inner_text()


def test_touch_targets_are_large_enough_on_a_phone(browser, page_url):
    """WCAG 2.5.8 wants 24px minimum; rows are the primary target and get 44."""
    ctx = browser.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=3
    )
    pg = ctx.new_page()
    pg.goto(page_url)
    pg.wait_for_selector(".row")

    assert not pg.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    ), "the page must never scroll sideways on a phone"

    heights = pg.locator(".row").evaluate_all("els => els.map(e => e.getBoundingClientRect().height)")
    assert min(heights) >= 44, f"smallest row is {min(heights)}px"

    for sel in (".chip", ".ghost", "#search"):
        box = pg.locator(sel).first.bounding_box()
        assert box["height"] >= 34, f"{sel} is only {box['height']}px tall"

    # 16px inputs stop iOS zooming the whole page on focus.
    assert pg.evaluate("getComputedStyle(document.querySelector('#search')).fontSize") == "16px"
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


def test_roster_csv_export(browser, page_url):
    ctx = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
    pg = ctx.new_page()
    pg.goto(page_url)
    pg.wait_for_selector(".row")
    pg.locator("#slot").fill("1")
    pg.locator('.row[data-rk="1"]').click()          # auto-claimed at slot 1
    pg.locator("#exportRoster").click()
    pg.wait_for_timeout(200)
    csv_text = pg.evaluate("navigator.clipboard.readText()")
    lines = csv_text.splitlines()
    assert lines[0] == "slot,rank,name,pos,team,bye,adp"
    assert lines[1].startswith("RB,1,Jahmyr Gibbs,RB,DET,")
    assert pg.locator("#exportRoster").text_content() == "Copied"
    ctx.close()


def test_auction_values_show_on_rows(page):
    val = page.locator('.row[data-rk="1"] .val')
    assert val.count() == 1
    assert val.inner_text().startswith("$")
    assert val.get_attribute("title") == "Auction value"
    assert "Auction values assume $200" in page.locator("#adpnote").inner_text()


def test_keeper_toggle_shows_age_and_persists(page):
    btn = page.locator("#keeper")
    assert btn.get_attribute("aria-pressed") == "false"
    assert page.locator(".age").count() == 0

    btn.click()
    assert btn.get_attribute("aria-pressed") == "true"
    ages = page.locator(".age")
    assert ages.count() > 100
    first = page.locator('.row[data-rk="1"] .age')
    assert first.inner_text().endswith("y")
    assert "years old" in first.get_attribute("title")

    page.reload()
    page.wait_for_selector(".row")
    assert page.locator("#keeper").get_attribute("aria-pressed") == "true"
    assert page.locator(".age").count() > 100


def test_live_value_resorts_within_tiers_and_persists(page):
    btn = page.locator("#liveval")
    assert btn.get_attribute("aria-pressed") == "false"
    assert page.locator(".vor").count() == 0

    order_before = page.locator('.tier[data-tier="5"] .row').evaluate_all(
        "els => els.map(e => Number(e.dataset.rk))"
    )
    assert order_before == sorted(order_before), "consensus order is by rank"

    btn.click()
    assert btn.get_attribute("aria-pressed") == "true"
    assert page.locator(".vor").count() > 50
    assert page.locator('.row[data-rk="1"] .vor').inner_text().startswith("+")

    order_after = page.locator('.tier[data-tier="5"] .row').evaluate_all(
        "els => els.map(e => Number(e.dataset.rk))"
    )
    assert sorted(order_after) == sorted(order_before), "no player may leave its tier"
    assert order_after != order_before, "live value should reorder a mid board tier"

    page.reload()
    page.wait_for_selector(".row")
    assert page.locator("#liveval").get_attribute("aria-pressed") == "true"


def test_live_value_rises_as_a_position_thins(page):
    page.locator("#liveval").click()
    data = board.load()
    te = next(p for p in data.players if p.pos == "TE" and p.projected)
    before = page.locator(f'.row[data-rk="{te.rank}"] .vor').inner_text()

    # Take every tight end ranked above him; his replacement level must fall.
    for other in [p for p in data.players if p.pos == "TE" and p.rank != te.rank][:12]:
        page.locator(f'.row[data-rk="{other.rank}"]').click()
    after = page.locator(f'.row[data-rk="{te.rank}"] .vor').inner_text()
    assert int(after.lstrip("+")) > int(before.lstrip("+")), (
        f"a thinning position should gain value ({before} -> {after})"
    )


def test_opponent_needs_list_the_picks_before_yours(page):
    card = page.locator("#opponentcard")
    assert card.is_hidden(), "nothing to show until a slot is set"
    page.locator("#slot").fill("4")          # 12 teams: picks 1-3 come before yours
    assert card.is_visible()
    lines = page.locator(".oppline")
    assert lines.count() == 3
    assert lines.first.inner_text().startswith("#1 · T1")
    assert "needs" in lines.first.inner_text()

    # Once your pick is on the clock there is nobody in front of you.
    for rk in (1, 2, 3):
        page.locator(f'.row[data-rk="{rk}"]').click()
    assert card.is_hidden()
