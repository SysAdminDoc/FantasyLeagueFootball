"""League file, valuation, and the lineup maths every in-season command sits on."""

from __future__ import annotations

import json

import pytest

from fantasyleague import board, league
from fantasyleague.league import (
    League,
    Spot,
    Team,
    Valuation,
    best_lineup,
    lineup_points,
    replacement_by_position,
    slot_name,
    team_strength,
)


def spot(name, pos, slot="BN", team="", status=None):
    return Spot(name=name, pos=pos, team=team, slot=slot, status=status)


def val(**points):
    """Valuation from {"first last": points}; keys are joined suffix-blind like everything else."""
    return Valuation({board.join_key(k): v for k, v in points.items()})


@pytest.fixture
def me():
    return Team("Mine", [
        spot("QB One", "QB", "QB"), spot("QB Two", "QB"),
        spot("RB One", "RB", "RB"), spot("RB Two", "RB", "RB"), spot("RB Three", "RB"),
        spot("WR One", "WR", "WR"), spot("WR Two", "WR", "WR"), spot("WR Three", "WR", "FLEX"),
        spot("TE One", "TE", "TE"), spot("K One", "K", "K"), spot("Def One D/ST", "DST", "DST"),
        spot("IR Guy", "RB", "IR"),
    ])


@pytest.fixture
def points():
    return val(**{
        "QB One": 300, "QB Two": 250, "RB One": 200, "RB Two": 150, "RB Three": 170,
        "WR One": 190, "WR Two": 120, "WR Three": 160, "TE One": 140, "K One": 100, "Def One D/ST": 90,
        "IR Guy": 400,
    })


# ---------------------------------------------------------------- slots & model

@pytest.mark.parametrize("raw, expected", [
    ("W/R/T", "FLEX"), ("WR/RB/TE", "FLEX"), ("flex", "FLEX"), ("DEF", "DST"), ("D/ST", "DST"),
    ("IR", "IR"), ("IR-R", "IR"), ("BN", "BN"), ("qb", "QB"),
])
def test_slot_aliases_from_the_sites_collapse_to_one_vocabulary(raw, expected):
    assert slot_name(raw) == expected


def test_unknown_slot_and_position_are_rejected():
    with pytest.raises(ValueError):
        slot_name("DL")
    with pytest.raises(ValueError):
        Spot(name="X", pos="OL")


def test_league_round_trips_through_json(tmp_path, me):
    lg = League("Test", 2026, [me, Team("Theirs", [spot("Some Guy", "WR", "WR", status="Questionable")])],
                me="Mine", scoring="ppr", source={"site": "yahoo", "league_id": 1})
    path = league.save(lg, tmp_path / "league.json")
    back = league.load(path)
    assert back.name == "Test" and back.me == "Mine" and back.scoring == "ppr"
    assert [t.name for t in back.teams] == ["Mine", "Theirs"]
    assert back.teams[1].roster[0].status == "Questionable"
    assert back.teams[0].roster[-1].slot == "IR"
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == league.LEAGUE_SCHEMA_VERSION


def test_load_explains_a_missing_file_and_a_bad_shape(tmp_path):
    with pytest.raises(ValueError, match="league import"):
        league.load(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text('{"name": "x", "season": 2026, "teams": [{"roster": []}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="missing the required field"):
        league.load(bad)


def test_validate_catches_duplicate_teams_players_and_a_bad_me(me):
    with pytest.raises(ValueError, match="same name"):
        league.validate(League("x", 2026, [me, Team("mine")]))
    other = Team("Other", [spot("RB One", "RB")])
    with pytest.raises(ValueError, match="two rosters"):
        league.validate(League("x", 2026, [me, other]))
    with pytest.raises(ValueError, match='"me"'):
        league.validate(League("x", 2026, [me], me="Nobody"))


def test_team_lookup_is_forgiving_but_not_guessy(me):
    lg = League("x", 2026, [me, Team("Bijan Mustardson"), Team("Bijan Fan Club")], me="Mine")
    assert lg.team() is me
    assert lg.team("mustard").name == "Bijan Mustardson"
    with pytest.raises(ValueError, match="ambiguous"):
        lg.team("bijan")
    with pytest.raises(ValueError, match="no team matches"):
        lg.team("zzz")
    assert League("x", 2026, [me]).team("mine") is me
    with pytest.raises(ValueError, match="no team is marked"):
        League("x", 2026, [me]).team()


def test_roster_find_matches_partial_names_and_suffixes(me):
    assert me.find("rb one").name == "RB One"
    assert me.find("QB One Jr.").name == "QB One"  # suffix-blind join
    with pytest.raises(ValueError, match="ambiguous"):
        me.find("one")
    with pytest.raises(ValueError, match="not on"):
        me.find("Nobody")


# ---------------------------------------------------------------- lineups

def test_best_lineup_fills_fixed_slots_then_flex_and_skips_ir(me, points):
    starters, bench = best_lineup(me.roster, points)
    names = {k: s.name for k, s in starters.items()}
    assert names["QB"] == "QB One"
    assert {names["RB1"], names["RB2"]} == {"RB One", "RB Three"}     # RB Three (170) beats RB Two (150)
    assert {names["WR1"], names["WR2"]} == {"WR One", "WR Three"}
    assert names["FLEX"] == "RB Two"                                   # best leftover RB/WR/TE (150 > 120)
    assert names["TE"] == "TE One" and names["K"] == "K One" and names["DST"] == "Def One D/ST"
    assert "IR Guy" not in names.values() and "IR Guy" not in {s.name for s in bench}
    assert {s.name for s in bench} == {"QB Two", "WR Two"}
    assert lineup_points(starters, points) == 300 + 200 + 170 + 190 + 160 + 150 + 140 + 100 + 90
    assert lineup_points(starters, points, skill_only=True) == 300 + 200 + 170 + 190 + 160 + 150 + 140


def test_flex_is_optimal_not_just_greedy_by_position():
    """A WR3 that outscores the RB2 must land in FLEX rather than be lost behind the two-WR cap."""
    roster = [spot("A", "RB"), spot("B", "RB"), spot("C", "WR"), spot("D", "WR"), spot("E", "WR"), spot("F", "TE")]
    v = val(A=100, B=10, C=90, D=80, E=70, F=50)
    starters, _ = best_lineup(roster, v, {"QB": 0, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 0, "DST": 0})
    assert starters["FLEX"].name == "E"           # 70 > any leftover
    assert lineup_points(starters, v) == 100 + 10 + 90 + 80 + 70 + 50


def test_superflex_shape_starts_two_quarterbacks(me, points):
    shape = {"QB": 2, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1}
    starters, _ = best_lineup(me.roster, points, shape)
    assert {starters["QB1"].name, starters["QB2"].name} == {"QB One", "QB Two"}


def test_weekly_valuation_zeroes_a_player_on_bye(me):
    data = board.load()
    v = Valuation.from_board(data)
    gibbs = next(p for p in data.players if p.name == "Jahmyr Gibbs")
    s = spot("Jahmyr Gibbs", "RB")
    assert v.points(s) == gibbs.projected and not v.on_bye(s)
    wk = v.with_week(gibbs.bye)
    assert wk.points(s) == 0.0 and wk.on_bye(s)
    other = v.with_week(gibbs.bye + 1 if gibbs.bye < 14 else gibbs.bye - 1)
    assert other.points(s) == gibbs.projected


def test_valuation_reports_unknown_players_as_zero_but_known_false():
    v = val(**{"Known Guy": 10})
    assert v.points(spot("Unknown Guy", "WR")) == 0.0
    assert not v.known(spot("Unknown Guy", "WR")) and v.known(spot("known guy jr", "WR"))


def test_team_strength_and_league_replacement_levels(me, points):
    assert team_strength(me, points) == 1500
    weaker = Team("Weak", [spot("W QB", "QB"), spot("W RB", "RB"), spot("W RB2", "RB"), spot("W WR", "WR"),
                           spot("W WR2", "WR"), spot("W TE", "TE")])
    v = Valuation({**points._points, **{board.join_key(k): 50 for k in
                                        ("W QB", "W RB", "W RB2", "W WR", "W WR2", "W TE")}})
    lg = League("x", 2026, [me, weaker])
    worst = replacement_by_position(lg, v)
    assert worst["QB"] == 50 and worst["RB"] == 50 and worst["TE"] == 50
    assert worst["K"] == 100          # only one team starts a kicker; that is the floor
