"""Yahoo page parsers — pure Python over HTML shaped like the real pages (recorded 2026-08-16)."""

from __future__ import annotations

from fantasyleague.league import Spot
from fantasyleague.sync import yahoo


def row(slot, name, pid, teampos, status=None, link="players"):
    """One roster row in Yahoo's markup. Empty slots have no player link at all."""
    tag = (f'<span class="ysf-player-status Nowrap F-injury Fz-xxs"><span class="Pstart-sm" title="{status}" '
           f'alt="{status}">Q</span></span>') if status else ""
    return (
        f'<tr><td class="Alt Ta-c pos headcol"><div><span class="pos-label Miwpx-40 Block Nowrap" data-pos="{slot}">'
        f'{slot}</span></div></td>'
        f'<td class="Ta-start player Bdrstart"><div class="Ov-h"><div class="D-f Jc-sb Ai-c"><div class="Ta-start Truncate">'
        f'<div class="ysf-player-name Nowrap Relative Lh-xs">'
        f'<a class="Nowrap name F-link playernote" href="https://sports.yahoo.com/nfl/{link}/{pid}" target="_blank" '
        f'data-ys-playerid="{pid}" title="{name}">{name}</a>{tag}'
        f'<span class="player-status D-ib Pstart-sm Nowrap"><span class="ysf-player-video-link Ta-start">'
        f'<a href="#" class="playernote Ta-start yfa-icon yfa-video-forecast" data-ys-playerid="{pid}">Video Forecast</a>'
        f'</span></span> <span class="D-b"><span class="Fz-xxs">{teampos}</span> </span></div>'
        f'<div class="ysf-player-detail Nowrap Fz-xxs Lh-xs"><span class="ysf-game-status">Sun 1:00 pm vs Chi</span></div>'
        f'</div></div></div></td><td class="Alt Ta-end Bdrstart"><div>6</div></td></tr>'
    )


def empty_row(slot):
    return (f'<tr><td class="Alt Ta-c pos headcol"><div><span class="pos-label" data-pos="{slot}">{slot}</span></div></td>'
            f'<td class="Ta-start player"><div class="Ov-h">(Empty)</div></td></tr>')


TEAM_PAGE = (
    "<html><head><title>Broseph&#x2019;s - Matt&#39;s AI Picks | Fantasy Football | Yahoo! Sports</title></head><body>"
    '<div>Waiver Priority: 3rd</div><a href="/f1/1/10/editteaminfo">Edit Team Settings</a>'
    '<table id="statTable0" class="Table"><thead><tr><th>Pos</th><th>Offense</th></tr></thead><tbody>'
    + row("QB", "Jayden Daniels", 40896, "Was - QB")
    + row("RB", "Jonathan Taylor", 32711, "Ind - RB")
    + row("W/R/T", "Parker Washington", 40234, "Jax - WR")
    + row("BN", "Chuba Hubbard", 33514, "Car - RB", status="Questionable")
    + row("BN", "Travis Etienne Jr.", 33517, "NO - RB")
    + empty_row("IR")
    + '</tbody></table>'
    '<table id="statTable1"><tbody>' + row("K", "Eddy Pineiro", 31482, "SF - K") + '</tbody></table>'
    '<table id="statTable2"><tbody>' + row("DEF", "Ravens", 100033, "Bal - DEF", link="teams/baltimore") + '</tbody></table>'
    '<table id="otherTable"><tbody>' + row("QB", "Not A Roster Row", 1, "Was - QB") + '</tbody></table>'
    "</body></html>"
)


def test_parse_team_page_reads_every_roster_row_and_nothing_else():
    name, roster, extras = yahoo.parse_team_page(TEAM_PAGE)
    assert name == "Matt's AI Picks"
    # The curly apostrophe in the entity (&#x2019;) survives unescaping.
    assert extras == {"league": "Broseph" + chr(0x2019) + "s", "mine": True, "waiver": 3}
    assert [(s.slot, s.name, s.pos, s.team) for s in roster] == [
        ("QB", "Jayden Daniels", "QB", "WAS"),
        ("RB", "Jonathan Taylor", "RB", "IND"),
        ("FLEX", "Parker Washington", "WR", "JAX"),
        ("BN", "Chuba Hubbard", "RB", "CAR"),
        ("BN", "Travis Etienne Jr.", "RB", "NO"),
        ("K", "Eddy Pineiro", "K", "SF"),
        ("DST", "Ravens D/ST", "DST", "BAL"),
    ]
    assert roster[0].ids == {"yahoo": "40896"}
    assert roster[3].status == "Questionable" and roster[0].status is None
    assert all(isinstance(s, Spot) for s in roster)


def test_parse_team_page_on_someone_elses_team_is_not_mine():
    page = TEAM_PAGE.replace('<a href="/f1/1/10/editteaminfo">Edit Team Settings</a>', "").replace(
        "Matt&#39;s AI Picks", "Bijan Mustardson")
    name, _, extras = yahoo.parse_team_page(page)
    assert name == "Bijan Mustardson" and extras["mine"] is False


def test_parse_team_page_survives_an_unrelated_page():
    _name, roster, extras = yahoo.parse_team_page("<html><title>There was a problem | Yahoo</title></html>")
    assert roster == [] and extras["mine"] is False


DRAFT_PAGE = """
<div id="drafttables"><table><thead><tr><th colspan="3" class="Fw-b">Round 1</th></tr></thead><tbody>
<tr> <td class="first">1.</td>
 <td class="player Px-sm"><a href="https://sports.yahoo.com/nfl/players/33393" target="_blank" class="name">Ja'Marr Chase</a>  </td>
 <td class="last Px-sm" title="terri's Outstanding Team">terri's Outs...</td>
</tr>
<tr> <td class="first">2.</td>
 <td class="player Px-sm"><a href="https://sports.yahoo.com/nfl/teams/philadelphia/" target="_blank" class="name">Eagles</a>  </td>
 <td class="last Px-sm" title="Beatatron">Beatatron</td>
</tr></tbody></table>
<table><thead><tr><th colspan="3" class="Fw-b">Round 2</th></tr></thead><tbody>
<tr> <td class="first">1.</td>
 <td class="player Px-sm"><a href="https://sports.yahoo.com/nfl/players/40059" target="_blank" class="name">Jahmyr Gibbs</a>  </td>
 <td class="last Px-sm" title="Wingin&#39; it">Wingin' it</td>
</tr></tbody></table></div>
"""


def test_parse_draft_results_numbers_picks_across_rounds():
    picks = yahoo.parse_draft_results(DRAFT_PAGE, teams=2)
    assert [(p["overall"], p["round"], p["pick"], p["name"], p["manager"]) for p in picks] == [
        (1, 1, 1, "Ja'Marr Chase", "terri's Outstanding Team"),
        (2, 1, 2, "Eagles", "Beatatron"),
        (3, 2, 1, "Jahmyr Gibbs", "Wingin' it"),
    ]
    assert picks[0]["yahoo_id"] == 33393 and picks[1]["is_def"] and picks[1]["yahoo_id"] is None


def test_league_from_draft_builds_rosters_and_resolves_positions():
    picks = yahoo.parse_draft_results(DRAFT_PAGE, teams=2)
    lookup = {"ja'marr chase": ("WR", "CIN"), "jahmyr gibbs": ("RB", "DET")}
    lg = yahoo.league_from_draft(picks, "Broseph's", 2026, me="Wingin' it",
                                 resolve_pos=lambda n: lookup.get(n.lower()))
    by = {t.name: t for t in lg.teams}
    assert by["terri's Outstanding Team"].roster[0].pos == "WR"
    assert by["Beatatron"].roster[0].name == "Eagles D/ST" and by["Beatatron"].roster[0].pos == "DST"
    assert by["Wingin' it"].roster[0].ids == {"yahoo": "40059"}
    assert lg.me == "Wingin' it" and lg.source["from"] == "draft results"


def test_league_url_prefixes_archived_seasons():
    assert yahoo.league_url(358473) == "https://football.fantasysports.yahoo.com/f1/358473"
    assert yahoo.league_url(415823, 2025) == "https://football.fantasysports.yahoo.com/2025/f1/415823"


# ---------------------------------------------------------------- live draft sync

class FakeBus:
    def __init__(self):
        self.picks = []      # (rank | None, name, slot)
        self.undone = []

    def pick(self, rank, source="manual", slot=None, mine=None):
        if any(p[0] == rank for p in self.picks):
            return False
        self.picks.append((rank, None, slot))
        return True

    def pick_offboard(self, source="manual", name="", slot=None):
        self.picks.append((None, name, slot))
        return {"pick_no": len(self.picks)}

    def undo_pick_no(self, pick_no, source="manual"):
        self.undone.append(pick_no)
        return True


def picks_for(*rows):
    """rows: (round, pick, name, yahoo_id, is_def, manager) -> parse_draft_results shape, 12 teams."""
    return [{"overall": (r - 1) * 12 + p, "round": r, "pick": p, "name": n, "yahoo_id": y, "is_def": d,
             "manager": m} for r, p, n, y, d, m in rows]


def test_yahoo_draft_sync_resolves_by_name_id_and_defense_and_records_off_board():
    from fantasyleague import board
    from fantasyleague.serve import Bus  # real bus: exercises the same contract the CLI wires

    data = board.load()
    bus = Bus(data, teams=12, slot=10)
    events = []
    sync = yahoo.YahooDraftSync(data, 358473, bus, teams=12, on_event=events.append,
                                reader=lambda: picks_for(
                                    (1, 1, "Joe Burrow", 32671, False, "Terri's team"),
                                    (1, 2, "Ravens", None, True, "Girldad"),
                                    (1, 3, "Travis Etienne Jr.", 33517, False, "Cowboys gang"),   # suffix-blind
                                    (1, 4, "Some Rookie Nobody", 99999, False, "Eye of the Tiger"),
                                ))
    assert sync.poll_once() == 4
    st = bus.state()
    names = [next((p.name for p in data.players if p.rank == e["rank"]), None) if e["rank"] else "(off)"
             for e in st["picks"]]
    assert names == ["Joe Burrow", "Ravens D/ST", "Travis Etienne Jr.", "(off)"]
    assert st["current_pick"] == 5
    assert any("Some Rookie Nobody is not on this board" in e for e in events)
    # Polling again with the same page is a no-op.
    assert sync.poll_once() == 0 and bus.state()["current_pick"] == 5


def test_yahoo_draft_sync_marks_snake_slots_and_mirrors_undo():
    from fantasyleague import board

    data = board.load()
    bus = FakeBus()
    page = picks_for((1, 12, "Jahmyr Gibbs", None, False, "A"), (2, 1, "Bijan Robinson", None, False, "A"))
    sync = yahoo.YahooDraftSync(data, 1, bus, teams=12, reader=lambda: page)
    assert sync.poll_once() == 2
    # Round 1 pick 12 is slot 12; round 2 pick 1 is also slot 12 (the snake turns).
    assert [s for _, _, s in bus.picks] == [12, 12]
    # The commissioner undoes pick 13: it vanishes from the page and the bus hears about it.
    page.pop()
    sync.poll_once()
    assert bus.undone == [13]
    # A different player at the same overall number is redone, not ignored.
    page.append({"overall": 13, "round": 2, "pick": 1, "name": "Puka Nacua", "yahoo_id": None, "is_def": False,
                 "manager": "A"})
    assert sync.poll_once() == 1
    assert bus.undone == [13] and bus.picks[-1][0] == next(p.rank for p in data.players if p.name == "Puka Nacua")


def test_yahoo_draft_sync_survives_a_reader_failure(monkeypatch):
    from fantasyleague import board

    events = []

    def boom():
        raise RuntimeError("Request denied")
    sync = yahoo.YahooDraftSync(board.load(), 1, FakeBus(), reader=boom, on_event=events.append)
    assert sync.poll_once() == 0
    assert any("poll failed" in e and "Request denied" in e for e in events)
    sync.start()
    sync.stop()          # thread exits cleanly; no browser was ever opened
