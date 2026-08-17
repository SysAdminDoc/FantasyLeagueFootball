"""Trades, waivers and start/sit — the same lineup maths from three angles."""

from __future__ import annotations

import pytest

from fantasyleague import board, manage
from fantasyleague.league import League, Spot, Team, Valuation


def spot(name, pos, slot="BN", status=None):
    return Spot(name=name, pos=pos, slot=slot, status=status)


def val(**points):
    return Valuation({board.join_key(k): v for k, v in points.items()}, label="test")


@pytest.fixture
def league():
    me = Team("Mine", [
        spot("My QB", "QB", "QB"), spot("My RB1", "RB", "RB"), spot("My RB2", "RB", "RB"), spot("My RB3", "RB"),
        spot("My WR1", "WR", "WR"), spot("My WR2", "WR", "WR"), spot("My WR3", "WR", "FLEX"),
        spot("My TE", "TE", "TE"), spot("My K", "K", "K"), spot("My Def D/ST", "DST", "DST"),
    ])
    them = Team("Theirs", [
        spot("Their QB", "QB", "QB"), spot("Their RB1", "RB", "RB"), spot("Their RB2", "RB", "RB"),
        spot("Their WR1", "WR", "WR"), spot("Their WR2", "WR", "WR"), spot("Their WR3", "WR", "FLEX"),
        spot("Their WR4", "WR"), spot("Their TE", "TE", "TE"), spot("Their K", "K", "K"),
        spot("Their Def D/ST", "DST", "DST"),
    ])
    return League("Test", 2026, [me, them], me="Mine")


@pytest.fixture
def points():
    # I am RB-rich (three good backs) and WR-poor; they are the mirror image.
    return val(**{
        "My QB": 300, "My RB1": 250, "My RB2": 220, "My RB3": 200, "My WR1": 150, "My WR2": 120, "My WR3": 100,
        "My TE": 130, "My K": 100, "My Def D/ST": 90,
        "Their QB": 280, "Their RB1": 150, "Their RB2": 90, "Their WR1": 260, "Their WR2": 240, "Their WR3": 210,
        "Their WR4": 190, "Their TE": 120, "Their K": 95, "Their Def D/ST": 85,
    })


# ---------------------------------------------------------------- start/sit

def test_start_sit_names_the_moves_and_nothing_else(league, points):
    me = league.team()
    call = manage.start_sit(me, points, league.lineup)
    assert call.total == 300 + 250 + 220 + 150 + 120 + 200 + 130 + 100 + 90
    assert [s.name for s in call.start] == ["My RB3"]        # benched today, belongs in FLEX
    assert [s.name for s in call.sit] == ["My WR3"]          # in FLEX today, should sit
    assert not call.byes and not call.unknown


def test_start_sit_flags_byes_and_unknowns():
    data = board.load()
    gibbs = next(p for p in data.players if p.name == "Jahmyr Gibbs")
    team = Team("T", [spot("Jahmyr Gibbs", "RB", "RB"), spot("Nobody Real", "RB", "RB")])
    v = Valuation.from_board(data).with_week(gibbs.bye)
    call = manage.start_sit(team, v)
    assert [s.name for s in call.byes] == ["Jahmyr Gibbs"]
    assert [s.name for s in call.unknown] == ["Nobody Real"]
    assert call.total == 0.0


# ---------------------------------------------------------------- trades

def test_evaluate_trade_reports_both_sides(league, points):
    me, them = league.team(), league.team("Theirs")
    r = manage.evaluate_trade(league, me, them, [me.find("My RB3")], [them.find("Their WR2")], points)
    # I turn a 200-point FLEX RB into a 240-point WR: +40 for me.
    assert r.me_delta == pytest.approx(40)
    # They turn a 90-point RB2 slot into 200 (RB3 starts) and lose WR2 240 -> WR4 190 in the flex chain.
    assert r.them_delta == pytest.approx((200 - 90) + (190 - 240) + (210 - 210))
    assert r.verdict.startswith("win-win")
    assert r.notes == []


def test_evaluate_trade_notes_roster_imbalance_and_kickers(league, points):
    me, them = league.team(), league.team("Theirs")
    r = manage.evaluate_trade(league, me, them, [me.find("My K")], [them.find("Their WR4"), them.find("Their K")],
                              points)
    assert any("must drop 1" in n for n in r.notes)
    assert any("kickers/defenses" in n for n in r.notes)


def test_evaluate_trade_rejects_nonsense(league, points):
    me, them = league.team(), league.team("Theirs")
    with pytest.raises(ValueError, match="at least one"):
        manage.evaluate_trade(league, me, them, [], [them.find("Their WR4")], points)
    with pytest.raises(ValueError, match="not on Theirs"):
        manage.evaluate_trade(league, me, them, [me.find("My RB3")], [me.find("My WR3")], points)
    with pytest.raises(ValueError, match="listed twice"):
        manage.evaluate_trade(league, me, them, [me.find("My RB3"), me.find("My RB3")], [them.find("Their WR4")],
                              points)


def test_verdict_bands():
    me, them = Team("a"), Team("b")
    mk = lambda md, td: manage.TradeResult(me, them, [], [], 100, 100 + md, 100, 100 + td)
    assert mk(10, 5).verdict.startswith("win-win")
    assert "worth asking" in mk(10, 0).verdict
    assert "counter" in mk(10, -8).verdict
    assert "wash" in mk(0.5, 0).verdict
    assert "decline" in mk(-5, 3).verdict


def test_find_trades_surfaces_the_surplus_for_need_swap(league, points):
    found = manage.find_trades(league, league.team(), points, min_gain=3, partner_floor=0, limit=5)
    assert found, "an RB-rich team facing a WR-rich one must find a win-win"
    best = found[0]
    assert best.me_delta > 0 and best.them_delta >= 0
    assert all(r.me_delta >= 3 and r.them_delta >= 0 for r in found)
    # The headline is the true win-win: my spare back for their spare receiver lifts both lineups,
    # and outranks the "free" bench-for-bench swaps that gain the partner nothing.
    assert any(s.pos == "RB" for s in best.give) and any(s.pos == "WR" for s in best.get) and best.them_delta > 0
    assert [r.score for r in found] == sorted((r.score for r in found), reverse=True)


def test_find_trades_never_offers_kickers_or_ir_players(league, points):
    me = league.team()
    me.roster.append(spot("My IR Star", "WR", "IR"))
    v = Valuation({**points._points, board.join_key("My IR Star"): 999})
    found = manage.find_trades(league, me, v, limit=20)
    for r in found:
        assert all(s.pos in manage.TRADEABLE and s.slot != "IR" for s in r.give + r.get)


def test_find_trades_partner_floor_filters_lopsided_deals(league, points):
    strict = manage.find_trades(league, league.team(), points, partner_floor=0)
    loose = manage.find_trades(league, league.team(), points, partner_floor=-100, limit=50)
    assert len(loose) >= len(strict)
    assert all(r.them_delta >= 0 for r in strict)


def test_find_trades_dedupes_throw_ins(league, points):
    found = manage.find_trades(league, league.team(), points, limit=30, deep=True)
    seen = set()
    for r in found:
        useful = frozenset(s.key for s in r.get if s.key in {
            x.key for x in manage.best_lineup(manage._roster_after(r.me.roster, r.give, r.get), points,
                                              league.lineup)[0].values()})
        assert (r.them.name, useful) not in seen, "the same effective proposal was listed twice"
        seen.add((r.them.name, useful))


# ---------------------------------------------------------------- waivers

def test_waiver_targets_rank_starters_first_and_pair_a_drop(league, points):
    pool = {
        board.join_key("FA Stud WR"): {"name": "FA Stud WR", "pos": "WR", "team": "X", "points": 230},
        board.join_key("FA Depth RB"): {"name": "FA Depth RB", "pos": "RB", "team": "X", "points": 210},
        board.join_key("FA Nobody"): {"name": "FA Nobody", "pos": "WR", "team": "X", "points": 5},
        board.join_key("Their WR1"): {"name": "Their WR1", "pos": "WR", "team": "X", "points": 260},  # rostered
    }
    targets = manage.waiver_targets(league, league.team(), points, pool)
    names = [t.name for t in targets]
    assert names[0] == "FA Stud WR"                    # would start over My WR2 (120): gain > 0
    assert targets[0].gain == pytest.approx(230 - 120)
    assert targets[0].drop is not None and targets[0].drop.pos in ("WR", "RB")
    assert "FA Depth RB" in names                     # beats My RB3 (200) as depth even if he sits...
    depth = next(t for t in targets if t.name == "FA Depth RB")
    assert depth.gain == pytest.approx(10)            # ...actually he'd start in FLEX over My RB3
    assert "Their WR1" not in names and "FA Nobody" not in names


def test_waiver_targets_respect_position_filter_and_limit(league, points):
    pool = {board.join_key(f"FA {i}"): {"name": f"FA {i}", "pos": "WR", "team": "X", "points": 200 + i}
            for i in range(10)}
    pool[board.join_key("FA RB")] = {"name": "FA RB", "pos": "RB", "team": "X", "points": 500}
    got = manage.waiver_targets(league, league.team(), points, pool, pos="WR", limit=3)
    assert len(got) == 3 and all(t.pos == "WR" for t in got)
    assert [t.name for t in got] == ["FA 9", "FA 8", "FA 7"]


def test_strength_table_orders_teams(league, points):
    rows = manage.strength_table(league, points)
    assert [t.name for t, _, _ in rows] == ["Mine", "Theirs"]
    assert rows[0][1] > rows[1][1]
