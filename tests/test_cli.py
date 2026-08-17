"""CLI smoke tests — the one place cli.py is actually imported and executed."""

from __future__ import annotations

import json

import pytest

from fantasyleague import __version__, board, cli

HEADER_COLUMNS = cli.CSV_COLUMNS

# Shapes recorded from the public endpoints on 2026-08-16, trimmed to what the
# commands read. These let the whole refresh/tiers/variant orchestration run
# offline — previously none of the three was ever executed through cli.main.
SLEEPER_DB = {
    "9221": {"full_name": "Jahmyr Gibbs", "position": "RB", "team": "DET",
             "injury_status": "Questionable", "injury_body_part": "Ankle",
             "age": 24, "years_exp": 3},
    "5859": {"full_name": "A.J. Brown", "position": "WR", "team": "NE",
             "injury_status": None, "age": 29, "years_exp": 7},
}
ADP_PAYLOAD = {
    "meta": {"type": "Half PPR", "teams": 12, "total_drafts": 2364,
             "start_date": "2026-08-11", "end_date": "2026-08-16"},
    "players": [
        {"name": "Jahmyr Gibbs", "position": "RB", "team": "DET", "adp": 1.5, "stdev": 0.7, "bye": 6},
        {"name": "A.J. Brown", "position": "WR", "team": "NE", "adp": 22.4, "stdev": 6.1, "bye": 10},
    ],
}
PROJECTIONS = [
    {"player_id": "9221", "stats": {"pts_half_ppr": 290.5, "pts_ppr": 320.0, "pts_std": 260.0}},
    {"player_id": "5859", "stats": {"pts_half_ppr": 210.0, "pts_ppr": 240.0, "pts_std": 180.0}},
]
TRENDING = [{"player_id": "5859", "count": 12345}]


@pytest.fixture
def offline(monkeypatch):
    """Point every network call at the recorded shapes above."""
    from fantasyleague.sync import adp as adp_mod
    from fantasyleague.sync import players as players_mod
    from fantasyleague.sync import projections as proj_mod

    monkeypatch.setattr(players_mod, "fetch_players", lambda **kw: (SLEEPER_DB, "cache"))
    monkeypatch.setattr(players_mod, "trending", lambda **kw: TRENDING)
    monkeypatch.setattr(adp_mod, "fetch", lambda **kw: ADP_PAYLOAD)
    monkeypatch.setattr(proj_mod, "fetch", lambda **kw: PROJECTIONS)


def test_refresh_runs_the_whole_pipeline(offline, capsys, tmp_path):
    out_path = tmp_path / "refreshed.json"
    code, out, _ = run(capsys, "refresh", "-o", str(out_path))
    assert code == 0
    for expected in ("Player database:", "ADP:", "Projections:", "Auction:", "Profiles:",
                     "Injury board:", "Trending adds", f"Wrote {out_path}"):
        assert expected in out, f"missing {expected!r} from refresh output"

    fresh = board.load(out_path)                      # must still be a loadable board
    gibbs = next(p for p in fresh.players if p.name == "Jahmyr Gibbs")
    assert gibbs.adp == 1.5 and gibbs.bye == 6 and gibbs.projected == 290.5
    assert gibbs.value and gibbs.age == 24 and gibbs.exp == 3
    assert gibbs.flag == "watch"                       # questionable, so flagged
    assert fresh.adp["url"].endswith("/adp/half-ppr/12-team/all")
    assert fresh.trending and fresh.trending_hours == 24
    assert fresh.refreshed and "UTC" in fresh.refreshed


def test_refresh_keeps_curated_flags_unless_asked(offline, capsys, tmp_path):
    out_path = tmp_path / "r.json"
    run(capsys, "refresh", "-o", str(out_path), "--no-trending")
    kept = {p.name: p.flag for p in board.load(out_path).players}
    assert kept["Breece Hall"] == "value", "a price judgement must survive a health refresh"

    code, out, _ = run(capsys, "refresh", "-o", str(out_path), "--reflag", "--no-trending")
    assert code == 0 and "flags recomputed" in out


def test_tiers_writes_positional_tier_names(monkeypatch, capsys, tmp_path):
    from fantasyleague.sync import borischen as borischen_mod

    data = board.load()
    published = {
        pos: {borischen_mod.normalise(p.name): 1 for p in data.players if p.pos == pos}
        for pos in ("QB", "RB", "WR", "TE", "K", "DST")
    }
    monkeypatch.setattr(borischen_mod, "fetch", lambda pos, **kw: (published[pos], 2.0))

    out_path = tmp_path / "tiered.json"
    code, out, _ = run(capsys, "tiers", "-o", str(out_path))
    assert code == 0 and "Re-tiered" in out
    fresh = board.load(out_path)
    assert not {t.name for t in fresh.tiers} & {t.name for t in data.tiers}
    assert all(t.note == "Boris Chen consensus tiers" for t in fresh.tiers)


def test_tiers_refuses_stale_files(monkeypatch, capsys):
    from fantasyleague.sync import borischen as borischen_mod

    def stale(pos, **kw):
        raise borischen_mod.StaleTiers("text_RB-HALF.txt was last published 233 days ago")

    monkeypatch.setattr(borischen_mod, "fetch", stale)
    code, _, err = run(capsys, "tiers")
    assert code == 1 and "233 days ago" in err


def test_variant_2qb_prices_off_two_quarterbacks(offline, capsys, tmp_path):
    out_path = tmp_path / "sf.json"
    code, out, _ = run(capsys, "variant", "2qb", "-o", str(out_path))
    assert code == 0 and "2QB" in out
    fresh = board.load(out_path)
    assert fresh.lineup["QB"] == 2
    assert "Superflex" in fresh.format and fresh.provenance
    assert json.loads(out_path.read_text(encoding="utf-8"))["lineup"]["QB"] == 2


def run(capsys, *argv) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert f"FantasyLeagueFootball v{__version__}" in capsys.readouterr().out


def test_list_filters_by_position(capsys):
    code, out, _ = run(capsys, "list", "--pos", "K", "--limit", "3")
    assert code == 0
    assert "Brandon Aubrey" in out
    assert "Jahmyr Gibbs" not in out


def test_list_heading_keeps_the_position_uppercase(capsys):
    """`.capitalize()` lowercased the rest, giving "Rb players"."""
    _, out, _ = run(capsys, "list", "--pos", "RB", "--limit", "1")
    assert "RB players" in out
    _, out, _ = run(capsys, "list", "--limit", "1")
    assert "All players" in out


@pytest.mark.parametrize(
    "argv",
    [
        ["build", "--slot", "20"],                       # outside a 12-team league
        ["next", "--slot", "13", "--teams", "12"],
        ["serve", "--slot", "0"],                        # rejected by the type
        ["next", "--teams", "1"],                        # a one-team draft is not a draft
        ["refresh", "--budget", "0"],
        ["variant", "2qb", "--roster-size", "0"],
        ["serve", "--every", "0"],
        ["list", "--limit", "0"],
    ],
)
def test_nonsense_arguments_are_refused(argv, capsys, tmp_path):
    """These used to be accepted and produce quietly wrong output."""
    if argv[0] == "build":
        argv = [*argv, "-o", str(tmp_path / "b.html")]
    try:
        code = cli.main(argv)
    except SystemExit as exc:            # argparse type errors exit 2
        code = exc.code
    assert code != 0
    err = capsys.readouterr().err
    assert "Traceback" not in err


def test_values_lists_do_not_draft(capsys):
    code, out, _ = run(capsys, "values")
    assert code == 0
    assert "Do not draft" in out and "Justin Herbert" in out


def test_next_by_names_and_ranks(capsys):
    code, out, _ = run(capsys, "next", "--drafted", "gibbs", "bijan", "3", "--limit", "2")
    assert code == 0
    assert "Puka Nacua" in out and "Jahmyr Gibbs" not in out


def test_next_ambiguous_name_fails_cleanly(capsys):
    code, _, err = run(capsys, "next", "--drafted", "brown")
    assert code == 1
    assert "ambiguous" in err and "Brown" in err


def test_next_with_slot_prints_odds(capsys):
    code, out, _ = run(capsys, "next", "--teams", "12", "--slot", "5", "--limit", "3")
    assert code == 0
    assert "Pick 1 · yours in 4 · your next picks: 5, 20, 29" in out
    assert "odds of surviving to pick 5 / 20" in out
    assert "%" in out and "CALL" in out


def test_next_with_slot_on_your_pick(capsys):
    code, out, _ = run(capsys, "next", "--slot", "5", "--pick", "5", "--limit", "1")
    assert code == 0
    assert "Pick 5 · your pick now" in out


def test_build_writes_file_with_league_and_slot(capsys, tmp_path):
    out_path = tmp_path / "b.html"
    argv = ["build", "-o", str(out_path), "--league", "Test", "--teams", "10", "--slot", "3"]
    code, out, _ = run(capsys, *argv)
    assert code == 0 and out_path.exists()
    assert "Built" in out
    html = out_path.read_text("utf-8")
    assert '"draft": {"teams": 10, "slot": 3}' in html


def test_export_writes_csv_to_stdout(capsys):
    code, out, _ = run(capsys, "export", "--pos", "TE", "--limit", "2")
    assert code == 0
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines[0] == "rank,name,pos,team,tier,flag,adp,adp_sd,bye,projected,value,age,exp,note"
    assert lines[1].startswith("17,Brock Bowers,TE,LV,3,")
    assert len(lines) == 3


def test_export_writes_a_file_and_quotes_commas(capsys, tmp_path):
    out_path = tmp_path / "board.csv"
    code, out, _ = run(capsys, "export", "-o", str(out_path))
    assert code == 0 and "Wrote" in out
    text = out_path.read_text(encoding="utf-8")
    assert text.startswith("rank,name,pos,team,tier,flag,adp,adp_sd,bye,projected,value,age,exp,note")
    # A note containing a comma must be quoted, not split into extra columns.
    import csv as _csv

    rows = list(_csv.reader(text.splitlines()))
    width = len(HEADER_COLUMNS)
    assert all(len(r) == width for r in rows), f"every row must have exactly {width} columns"
    assert len(rows) == 201  # header + 200 players


def test_export_filters_compose(capsys):
    code, out, _ = run(capsys, "export", "--flag", "value", "--pos", "RB")
    assert code == 0
    import csv as _csv

    rows = list(_csv.DictReader(out.splitlines()))
    assert rows and all(r["flag"] == "value" and r["pos"] == "RB" for r in rows)


# ---------------------------------------------------------------- in-season

def _league_file(tmp_path):
    """A two-team league drawn from real board names so projections join without the network."""
    from fantasyleague import league as league_mod
    from fantasyleague.league import League, Spot, Team

    def s(name, pos, slot="BN"):
        return Spot(name=name, pos=pos, slot=slot)

    me = Team("Matt's AI Picks", [
        s("Jayden Daniels", "QB", "QB"), s("Jonathan Taylor", "RB", "RB"), s("Ashton Jeanty", "RB", "RB"),
        s("Rashee Rice", "WR", "WR"), s("Mike Evans", "WR", "WR"), s("Trey McBride", "TE", "TE"),
        s("Parker Washington", "WR", "FLEX"), s("Chuba Hubbard", "RB"), s("Jordan Addison", "WR"),
        s("Eddy Pineiro", "K", "K"), s("Ravens D/ST", "DST", "DST"),
    ])
    them = Team("Girldad", [
        s("Brock Purdy", "QB", "QB"), s("Bijan Robinson", "RB", "RB"), s("Jaylen Warren", "RB", "RB"),
        s("Nico Collins", "WR", "WR"), s("Chris Olave", "WR", "WR"), s("Brock Bowers", "TE", "TE"),
        s("Tetairoa McMillan", "WR", "FLEX"), s("Davante Adams", "WR"), s("Rachaad White", "RB"),
        s("Tyler Loop", "K", "K"), s("Packers D/ST", "DST", "DST"),
    ])
    path = tmp_path / "league.json"
    league_mod.save(League("Broseph's", 2026, [me, them], me="Matt's AI Picks", scoring="ppr"), path)
    return path


@pytest.fixture
def season_only(monkeypatch):
    """No network: the calendar and every projection endpoint are unreachable."""
    from fantasyleague.sync import projections as proj_mod
    from fantasyleague.sync import sleeper as sleeper_mod

    def down(*a, **kw):
        raise OSError("offline")

    monkeypatch.setattr(sleeper_mod, "current_week", down)
    monkeypatch.setattr(proj_mod, "fetch_week", down)


def test_league_show_ranks_teams_offline(capsys, tmp_path, season_only):
    path = _league_file(tmp_path)
    code, out, _ = run(capsys, "league", "show", "--league", str(path), "--rosters")
    assert code == 0
    assert "note:" in out and "assuming week 1" in out          # honest about the fallback
    assert "Girldad" in out and "Matt's AI Picks" in out and "<- you" in out
    assert out.index("Girldad") < out.index("Matt's AI Picks")   # stronger lineup listed first
    assert "Ravens D/ST" in out                                  # --rosters prints everyone


def test_league_show_with_season_horizon_needs_no_network(capsys, tmp_path, monkeypatch):
    from fantasyleague.sync import projections as proj_mod

    monkeypatch.setattr(proj_mod, "fetch_week", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no fetch")))
    path = _league_file(tmp_path)
    code, out, _ = run(capsys, "league", "show", "--league", str(path), "--horizon", "season", "--week", "4")
    assert code == 0 and "by season" in out


def test_lineup_uses_weekly_projections_and_names_moves(capsys, tmp_path, monkeypatch):
    from fantasyleague.sync import projections as proj_mod
    from fantasyleague.sync import sleeper as sleeper_mod

    monkeypatch.setattr(sleeper_mod, "current_week", lambda **kw: (2026, 3))

    def week_rows(season, week, **kw):
        assert (season, week) == (2026, 3)
        def row(pid, first, last, pos, pts, opp="X"):
            return {"player_id": pid, "opponent": opp, "team": "T",
                    "player": {"first_name": first, "last_name": last, "position": pos},
                    "stats": {"pts_ppr": pts, "pts_half_ppr": pts, "pts_std": pts}}
        return [row("1", "Jayden", "Daniels", "QB", 20), row("2", "Jonathan", "Taylor", "RB", 18),
                row("3", "Ashton", "Jeanty", "RB", 5), row("4", "Chuba", "Hubbard", "RB", 15),
                row("5", "Rashee", "Rice", "WR", 14), row("6", "Mike", "Evans", "WR", 0, opp=None),  # bye
                row("7", "Parker", "Washington", "WR", 9), row("8", "Jordan", "Addison", "WR", 12),
                row("9", "Trey", "McBride", "TE", 13), row("10", "Eddy", "Pineiro", "K", 7),
                row("BAL", "Baltimore", "Ravens", "DEF", 8)]

    monkeypatch.setattr(proj_mod, "fetch_week", week_rows)
    path = _league_file(tmp_path)
    code, out, _ = run(capsys, "lineup", "--league", str(path))
    assert code == 0, out
    assert "best lineup for week 3" in out
    assert "START Chuba Hubbard" in out and "START Jordan Addison" in out
    assert "SIT   Ashton Jeanty" in out and "SIT   Mike Evans" in out
    assert "No projection" not in out


def test_lineup_falls_back_to_season_over_17_with_byes(capsys, tmp_path, season_only):
    path = _league_file(tmp_path)
    code, out, _ = run(capsys, "lineup", "--league", str(path), "--week", "6")
    assert code == 0
    assert "season ÷ 17" in out
    # Every starter shows a per-game number, not a season total.
    import re
    nums = [float(x) for x in re.findall(r"\s(\d+\.\d)\n", out)]
    assert nums and max(nums) < 40


def test_waivers_lists_free_agents_from_the_pool(capsys, tmp_path, monkeypatch):
    from fantasyleague.sync import projections as proj_mod
    from fantasyleague.sync import sleeper as sleeper_mod

    monkeypatch.setattr(sleeper_mod, "current_week", lambda **kw: (2026, 1))
    monkeypatch.setattr(proj_mod, "rest_of_season", lambda season, week, scoring, **kw: ({
        "free agent wr": {"name": "Free Agent WR", "pos": "WR", "team": "FA", "points": 400.0},
        "rashee rice": {"name": "Rashee Rice", "pos": "WR", "team": "KC", "points": 250.0},
        "jayden daniels": {"name": "Jayden Daniels", "pos": "QB", "team": "WAS", "points": 300.0},
        "bench guy": {"name": "Bench Guy", "pos": "RB", "team": "FA", "points": 1.0},
    }, []))
    path = _league_file(tmp_path)
    code, out, _ = run(capsys, "waivers", "--league", str(path), "--no-trending")
    assert code == 0, out
    assert "Free Agent WR" in out and "+400" in out          # would start: gain shown
    assert "Rashee Rice" not in out.split("Best available")[0]  # rostered players are never targets
    assert "DROP" in out


def test_trade_evaluates_both_sides_and_trades_finds_partners(capsys, tmp_path, monkeypatch):
    from fantasyleague.sync import projections as proj_mod
    from fantasyleague.sync import sleeper as sleeper_mod

    monkeypatch.setattr(sleeper_mod, "current_week", lambda **kw: (2026, 1))
    monkeypatch.setattr(proj_mod, "rest_of_season", lambda *a, **kw: ({}, list(range(1, 18))))
    path = _league_file(tmp_path)
    code, out, _ = run(capsys, "trade", "--league", str(path), "--with", "girldad",
                       "--give", "hubbard", "--get", "adams")
    assert code == 0, out
    assert "note: rest-of-season projections unavailable" in out
    assert "Matt's AI Picks sends" in out and "Girldad sends" in out and "Verdict:" in out

    code, out, _ = run(capsys, "trades", "--league", str(path), "--allow-loss", "50", "--limit", "5")
    assert code == 0, out
    assert "Trades that improve Matt's AI Picks" in out


def test_trade_rejects_a_player_not_on_the_roster(capsys, tmp_path, season_only):
    path = _league_file(tmp_path)
    code, _, err = run(capsys, "trade", "--league", str(path), "--with", "girldad",
                       "--give", "hubbard", "--get", "nobody")
    assert code == 1 and "not on Girldad" in err


def test_in_season_commands_explain_a_missing_league_file(capsys, tmp_path):
    code, _, err = run(capsys, "lineup", "--league", str(tmp_path / "missing.json"))
    assert code == 1 and "league import" in err
