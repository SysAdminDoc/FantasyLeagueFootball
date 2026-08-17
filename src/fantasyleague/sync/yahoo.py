"""Read a Yahoo league's rosters and draft results into a `league.json`.

Yahoo's API needs an OAuth app; its web pages only need your login. So this
module drives a real browser profile you sign into once (Playwright, an optional
extra) and parses the pages it renders:

    /f1/{league}/{n}/team      one team's roster: slot, player, NFL team, position
    /f1/{league}/draftresults  every pick, by round

The parsers are plain Python over the HTML string, so they run — and are tested —
without a browser. Only `fetch_*` touch Playwright, and they import it lazily so
the rest of the package stays dependency-free.
"""

from __future__ import annotations

import html
import re
import threading
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

from ..board import join_key
from ..league import League, Spot, Team, slot_name
from ..models import DEFAULT_LINEUP

SITE = "https://football.fantasysports.yahoo.com"
DEFAULT_PROFILE = Path.home() / ".fantasyleague" / "yahoo-profile"

_POS = {"QB", "RB", "WR", "TE", "K", "DEF"}


def league_url(league_id: int, season: int | None = None) -> str:
    # Past seasons are archived under /<year>/f1/, the current one under /f1/.
    prefix = f"/{season}" if season else ""
    return f"{SITE}{prefix}/f1/{league_id}"


# ---------------------------------------------------------------- parsers

class _RosterParser(HTMLParser):
    """Walk the `statTable*` roster tables and collect (slot, name, yahoo id, "Team - Pos")."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict] = []
        self._in_table = False
        self._depth = 0
        self._row: dict | None = None
        self._capture: str | None = None
        self._buf = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table" and (a.get("id") or "").startswith("statTable"):
            self._in_table = True
            self._depth = 0
        if not self._in_table:
            return
        if tag == "tr":
            self._row = {"slot": None, "name": None, "yahoo_id": None, "teampos": None, "status": None}
        elif tag == "span" and self._row is not None and "pos-label" in (a.get("class") or ""):
            self._capture, self._buf = "slot", ""
        elif tag == "a" and self._row is not None and "name" in (a.get("class") or "").split():
            self._row["yahoo_id"] = a.get("data-ys-playerid")
            self._row["name"] = a.get("title")
            self._capture, self._buf = "name", ""
        elif tag == "span" and self._row is not None and self._row["name"] and a.get("title") \
                and "ysf-player-status" not in (a.get("class") or "") and self._row["status"] is None \
                and a.get("alt"):
            # <span title="Questionable" alt="Questionable">Q</span> inside the status span
            self._row["status"] = a.get("title")
        elif tag == "span" and self._row is not None and self._row["name"] and self._row["teampos"] is None \
                and (a.get("class") or "").strip() == "Fz-xxs":
            # exactly "Fz-xxs": the injury tag's span also carries Fz-xxs among other classes
            self._capture, self._buf = "teampos", ""

    def handle_data(self, data):
        if self._capture:
            self._buf += data

    def handle_endtag(self, tag):
        if not self._in_table:
            return
        if self._capture and tag in ("span", "a"):
            value = " ".join(self._buf.split())
            if self._capture == "name" and not self._row["name"]:
                self._row["name"] = value
            elif self._capture == "slot":
                self._row["slot"] = value
            elif self._capture == "teampos":
                self._row["teampos"] = value
            self._capture = None
        elif tag == "tr" and self._row is not None:
            if self._row["slot"] and self._row["name"]:
                self.rows.append(self._row)
            self._row = None
        elif tag == "table":
            self._in_table = False


def parse_team_page(page_html: str) -> tuple[str, list[Spot], dict]:
    """(team name, roster, extras) from one Yahoo team page.

    Extras carries whatever else the page states plainly: waiver priority, the
    league name, and whether this is the signed-in user's own team.
    """
    p = _RosterParser()
    p.feed(page_html)
    roster: list[Spot] = []
    for r in p.rows:
        team, pos = _split_teampos(r.get("teampos") or "")
        name = html.unescape(r["name"]).strip()
        if pos == "DEF" or (pos is None and " D/ST" in name):
            pos = "DST"
            if not name.endswith("D/ST"):
                name = f"{name} D/ST"
        if pos is None:
            continue  # a header or an empty slot row
        ids = {"yahoo": r["yahoo_id"]} if r.get("yahoo_id") and r["yahoo_id"].isdigit() else {}
        try:
            slot = slot_name(r["slot"])
        except ValueError:
            continue
        roster.append(Spot(name=name, pos=pos, team=team or "", slot=slot, ids=ids, status=r.get("status")))

    m = re.search(r"<title>\s*(.*?)\s*</title>", page_html, re.DOTALL)
    title = html.unescape(m.group(1)) if m else ""
    # "Broseph's - Bijan Mustardson | Fantasy Football | Yahoo! Sports"
    league_name, _, rest = title.partition(" - ")
    team_name = rest.split(" | ")[0].strip() if rest else title.split(" | ")[0].strip()
    extras = {"league": league_name.strip(), "mine": "Edit Team Settings" in page_html}
    wm = re.search(r"Waiver Priority:\s*(\d+)", page_html)
    if wm:
        extras["waiver"] = int(wm.group(1))
    return team_name, roster, extras


def _split_teampos(text: str) -> tuple[str | None, str | None]:
    """'Was - QB' -> ('WAS', 'QB'); 'Bal - DEF' -> ('BAL', 'DEF')."""
    m = re.match(r"\s*([A-Za-z]{2,3})\s*-\s*([A-Za-z/]+)", text)
    if not m:
        return None, None
    team, pos = m.group(1).upper(), m.group(2).upper()
    if pos not in _POS:
        return team, None
    return team, pos


DRAFT_ROW = re.compile(
    r"<th[^>]*>\s*Round\s+(\d+)\s*</th>|"
    r'<td class="first">\s*(\d+)\.\s*</td>\s*<td class="player[^"]*">\s*<a href="([^"]*)"[^>]*class="name"[^>]*>(.*?)</a>'
    r'.*?<td class="last[^"]*"(?: title="([^"]*)")?>',
    re.DOTALL,
)


def parse_draft_results(page_html: str, teams: int = 12) -> list[dict]:
    """[{overall, round, pick, name, yahoo_id, is_def, manager}] from the Draft Results page."""
    picks: list[dict] = []
    rnd = None
    for m in DRAFT_ROW.finditer(page_html):
        if m.group(1):
            rnd = int(m.group(1))
            continue
        if rnd is None:
            continue
        pick, href, name, manager = int(m.group(2)), m.group(3), html.unescape(m.group(4)).strip(), m.group(5)
        pid = re.search(r"/players/(\d+)", href)
        picks.append({
            "overall": (rnd - 1) * teams + pick, "round": rnd, "pick": pick, "name": name,
            "yahoo_id": int(pid.group(1)) if pid else None, "is_def": "/teams/" in href,
            "manager": html.unescape(manager or "").strip(),
        })
    picks.sort(key=lambda p: p["overall"])
    return picks


# ---------------------------------------------------------------- browser

def _playwright():
    """The Playwright entry point, or a plain error naming the extra to install."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - only without the optional extra
        raise ValueError(
            'the Yahoo importer needs Playwright: pip install "fantasyleaguefootball[yahoo]" '
            "then `python -m playwright install chromium`"
        ) from exc
    return sync_playwright


def fetch_league(
    league_id: int,
    season: int | None = None,
    profile: Path | None = None,
    headless: bool = True,
    max_teams: int = 20,
    on_event=None,
) -> League:
    """Read every team page of *league_id* into a League (rosters as they stand now).

    Signs you in the first time: with *headless* False a browser window opens
    on Yahoo's login page and the import continues once you are through.
    """
    sync_playwright = _playwright()
    prof = Path(profile or DEFAULT_PROFILE)
    prof.mkdir(parents=True, exist_ok=True)
    say = on_event or (lambda msg: None)
    base = league_url(league_id, season)
    teams: list[Team] = []
    league_name = ""
    me: str | None = None
    with sync_playwright() as pw:
        ctx = _launch(pw, prof, headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        # League pages auto-jump into the draft client while a draft room is open.
        page.route("**/draftclient/**", lambda route: route.abort())
        _login(page, base, say)
        for n in range(1, max_teams + 1):
            page.goto(f"{base}/{n}/team", wait_until="domcontentloaded", timeout=60_000)
            content = page.content()
            if "not in this league" in content or "was not found" in content or "statTable" not in content:
                break
            name, roster, extras = parse_team_page(content)
            league_name = league_name or extras.get("league") or ""
            teams.append(Team(name=name, roster=roster, waiver=extras.get("waiver"), site_id=str(n)))
            if extras.get("mine"):
                me = name
            say(f"  {name}: {len(roster)} players")
        ctx.close()
    if not teams:
        raise ValueError(f"no team pages found for league {league_id} — is the id right and are you signed in?")
    return League(
        name=league_name or f"Yahoo league {league_id}", season=season or datetime.now(UTC).year, teams=teams,
        me=me, lineup=dict(DEFAULT_LINEUP), scoring="half_ppr",
        source={"site": "yahoo", "league_id": league_id, "fetched": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")},
    )


def fetch_draft_results(league_id: int, season: int | None = None, profile: Path | None = None,
                        headless: bool = True, teams: int = 12, on_event=None) -> list[dict]:
    """The Draft Results page as pick dicts (see `parse_draft_results`)."""
    sync_playwright = _playwright()
    prof = Path(profile or DEFAULT_PROFILE)
    prof.mkdir(parents=True, exist_ok=True)
    say = on_event or (lambda msg: None)
    base = league_url(league_id, season)
    with sync_playwright() as pw:
        ctx = _launch(pw, prof, headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.route("**/draftclient/**", lambda route: route.abort())
        _login(page, f"{base}/draftresults", say)
        page.goto(f"{base}/draftresults", wait_until="domcontentloaded", timeout=60_000)
        picks = parse_draft_results(page.content(), teams=teams)
        ctx.close()
    return picks


def _launch(pw, profile: Path, headless: bool):
    launch = {"user_data_dir": str(profile), "headless": headless, "viewport": {"width": 1280, "height": 900}}
    # A real Edge/Chrome build looks like a person to Yahoo's login; the bundled Chromium is the fallback.
    errors: list[str] = []
    for channel in ("msedge", "chrome", None):
        try:
            if channel:
                return pw.chromium.launch_persistent_context(channel=channel, **launch)
            return pw.chromium.launch_persistent_context(**launch)
        except Exception as exc:  # noqa: BLE001 - Playwright raises its own hierarchy; try the next channel
            errors.append(f"{channel or 'chromium'}: {str(exc).splitlines()[0][:80]}")
    raise ValueError(
        "could not start a browser (run `python -m playwright install chromium`): " + "; ".join(errors)
    )


def _login(page, url: str, say, timeout_s: int = 900) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    waited = 0
    while "login.yahoo.com" in page.url:
        if waited == 0:
            say("sign in to Yahoo in the browser window that opened; waiting…")
        time.sleep(2)
        waited += 2
        if waited > timeout_s:
            raise ValueError("gave up waiting for the Yahoo sign-in")
    if waited:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    if "login.yahoo.com" in page.url:
        raise ValueError("still not signed in to Yahoo — run again with --show to sign in")


# ---------------------------------------------------------------- live draft

DEFAULT_POLL_SECONDS = 12.0   # Yahoo rate-limits reloads faster than ~10s ("Request denied")


class YahooDraftSync:
    """Follow a live Yahoo draft through its Draft Results page and push picks into a `serve.Bus`.

    Same contract as `SleeperSync`: the bus must offer `pick`, `pick_offboard` and
    `undo_pick_no`. Picks are joined to the board by name (`board.join_key`), the
    Yahoo id when the board has one, and "<Nickname> D/ST" for defenses. The page
    reader is injectable so the sync logic is testable without a browser.
    """

    def __init__(self, data, league_id: int, bus, teams: int = 12, interval: float = DEFAULT_POLL_SECONDS,
                 profile: Path | None = None, headless: bool = True, on_event=None, reader=None):
        self.data = data
        self.league_id = league_id
        self.bus = bus
        self.teams = teams
        self.interval = interval
        self.profile = Path(profile or DEFAULT_PROFILE)
        self.headless = headless
        self.on_event = on_event
        self._reader = reader                     # callable() -> list[pick dicts]; None = real browser
        self._applied: dict[int, str] = {}        # overall pick -> key applied
        self._unknown: set[str] = set()
        self._stop = threading.Event()
        self._thread = None
        self._by_key = {join_key(p.name): p for p in data.players}
        self._by_yahoo = {str(p.ids["yahoo"]): p for p in data.players if p.ids.get("yahoo")}
        self._by_dst = {
            join_key(p.name.replace(" D/ST", "")): p for p in data.players if p.pos == "DST"
        }

    # ---- resolution ------------------------------------------------------------

    def resolve(self, pick: dict):
        if pick.get("yahoo_id") and str(pick["yahoo_id"]) in self._by_yahoo:
            return self._by_yahoo[str(pick["yahoo_id"])]
        key = join_key(pick["name"])
        if pick.get("is_def"):
            return self._by_dst.get(key)
        return self._by_key.get(key)

    # ---- one pass --------------------------------------------------------------

    def apply(self, picks: list[dict]) -> int:
        """Sync *picks* (from `parse_draft_results`) into the bus; returns how many were new."""
        live = {p["overall"] for p in picks}
        for no in sorted(self._applied.keys() - live, reverse=True):   # commissioner undo
            self._applied.pop(no, None)
            self.bus.undo_pick_no(no, source="yahoo")
            self._log(f"pick {no}: undone on Yahoo")
        applied = 0
        for pick in picks:
            no = pick["overall"]
            key = f"{pick.get('yahoo_id') or ''}|{pick['name']}"
            if self._applied.get(no) == key:
                continue
            if no in self._applied:
                self.bus.undo_pick_no(no, source="yahoo")
            self._applied[no] = key
            player = self.resolve(pick)
            slot = pick["pick"] if pick["round"] % 2 == 1 else self.teams - pick["pick"] + 1
            if player is None:
                if key not in self._unknown:
                    self._unknown.add(key)
                    self._log(f"pick {no}: {pick['name']} is not on this board")
                self.bus.pick_offboard(source="yahoo", name=pick["name"], slot=slot)
                applied += 1
                continue
            if self.bus.pick(player.rank, source="yahoo", slot=slot):
                applied += 1
                self._log(f"pick {no}: {player.name} ({player.pos} {player.team}) <- {pick['manager']}")
        return applied

    def poll_once(self) -> int:
        try:
            picks = self._reader() if self._reader else self._read_page()
        except Exception as exc:  # noqa: BLE001 - Playwright raises its own hierarchy; a draft outlives it
            self._log(f"poll failed ({exc.__class__.__name__}: {str(exc).splitlines()[0][:120]}); retrying")
            return 0
        return self.apply(picks)

    # ---- browser -----------------------------------------------------------------

    def _read_page(self) -> list[dict]:
        if getattr(self, "_page", None) is None:
            sync_playwright = _playwright()
            self.profile.mkdir(parents=True, exist_ok=True)
            self._pw = sync_playwright().start()
            self._ctx = _launch(self._pw, self.profile, self.headless)
            self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
            self._page.route("**/draftclient/**", lambda route: route.abort())
            self._url = f"{league_url(self.league_id)}/draftresults"
            _login(self._page, self._url, self._log)
        self._page.goto(self._url, wait_until="domcontentloaded", timeout=60_000)
        return parse_draft_results(self._page.content(), teams=self.teams)

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001
                self._log(f"poll crashed ({exc.__class__.__name__}: {exc}); retrying")
            self._stop.wait(self.interval)
        ctx = getattr(self, "_ctx", None)
        if ctx is not None:
            try:
                ctx.close()
                self._pw.stop()
            except Exception:  # noqa: BLE001, S110 - shutting down; nothing left to report to
                pass

    def start(self) -> YahooDraftSync:
        self._thread = threading.Thread(target=self.run_forever, name="yahoo-sync", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 5)

    def _log(self, msg: str) -> None:
        if callable(self.on_event):
            self.on_event(msg)


def league_from_draft(picks: list[dict], league_name: str, season: int, me: str | None = None,
                      resolve_pos=None) -> League:
    """A League whose rosters are exactly the draft, for use before any moves are made.

    *resolve_pos* maps a player name to (pos, team) — usually a lookup into the
    board — because the results page does not print positions.
    """
    teams: dict[str, Team] = {}
    for p in picks:
        mgr = p["manager"] or "?"
        t = teams.setdefault(mgr, Team(name=mgr))
        pos, team = ("DST", "") if p.get("is_def") else (None, "")
        if resolve_pos:
            got = resolve_pos(p["name"])
            if got:
                pos, team = got
        if pos is None:
            continue
        name = p["name"] if not p.get("is_def") or p["name"].endswith("D/ST") else f"{p['name']} D/ST"
        ids = {"yahoo": str(p["yahoo_id"])} if p.get("yahoo_id") else {}
        t.roster.append(Spot(name=name, pos=pos, team=team, slot="BN", ids=ids))
    return League(name=league_name, season=season, teams=list(teams.values()), me=me,
                  source={"site": "yahoo", "from": "draft results"})
