"""Projections, value over replacement, and auction pricing — offline."""

from __future__ import annotations

import pytest

from fantasyleague import board
from fantasyleague.models import DEFAULT_LINEUP, Dataset, Player, Tier
from fantasyleague.sync import projections as pj

# Shape recorded from GET /projections/nfl/2026 on 2026-08-16.
ROWS = [
    {"player_id": "9221", "player": {"first_name": "Jahmyr", "last_name": "Gibbs"},
     "stats": {"pts_half_ppr": 299.9, "pts_ppr": 331.4, "pts_std": 268.4}},
    {"player_id": "4984", "player": {"first_name": "Josh", "last_name": "Allen"},
     "stats": {"pts_half_ppr": 340.0, "pts_ppr": 340.0, "pts_std": 340.0}},
    {"player_id": "nope", "player": {}, "stats": {}},
]


def test_points_by_id_picks_the_right_scoring():
    assert pj.points_by_id(ROWS)["9221"] == 299.9
    assert pj.points_by_id(ROWS, "ppr")["9221"] == 331.4
    assert pj.points_by_id(ROWS, "standard")["9221"] == 268.4
    assert "nope" not in pj.points_by_id(ROWS)


def test_points_by_id_rejects_unknown_scoring():
    with pytest.raises(ValueError, match="unknown scoring"):
        pj.points_by_id(ROWS, "superflex")


def test_apply_joins_on_sleeper_id():
    data = board.load()
    fresh, hits = pj.apply(data, pj.points_by_id(ROWS))
    assert hits == 2
    by_name = {p.name: p for p in fresh.players}
    assert by_name["Jahmyr Gibbs"].projected == 299.9
    assert by_name["Josh Allen"].projected == 340.0


def _synthetic(counts: dict[str, int], step: float = 5.0) -> Dataset:
    """A ladder of players per position, each *step* points below the last."""
    players, rank = [], 1
    for pos, n in counts.items():
        for i in range(n):
            players.append(Player(rank, f"{pos}{i}", pos, "AAA", 1, projected=500 - i * step))
            rank += 1
    return Dataset(season=2026, scoring="half_ppr", format="t", updated="2026-08-16",
                   tiers=[Tier(n=1, name="T", range="", note="")], players=players)


def test_replacement_level_is_the_last_starter():
    # 12 teams, 1 QB each -> the 12th QB is replacement.
    data = _synthetic({"QB": 30})
    levels = pj.replacement_levels(data.players, {"QB": 1}, teams=12)
    assert levels["QB"] == 500 - 11 * 5


def test_flex_demand_is_shared_across_rb_wr_te():
    data = _synthetic({"RB": 60, "WR": 60, "TE": 30})
    levels = pj.replacement_levels(data.players, {"RB": 2, "WR": 2, "TE": 1, "FLEX": 1}, teams=12)
    # RB demand 2 + 0.5 flex = 2.5/team -> 30th RB.
    assert levels["RB"] == 500 - 29 * 5
    # WR 2 + 0.4 = 2.4 -> 29th; TE 1 + 0.1 = 1.1 -> 13th.
    assert levels["WR"] == 500 - 28 * 5
    assert levels["TE"] == 500 - 12 * 5


def test_vor_is_points_above_that_baseline():
    data = _synthetic({"QB": 30})
    vor = pj.value_over_replacement(data, {"QB": 1}, teams=12)
    assert vor[1] == pytest.approx(55.0)    # best QB is 11 steps above QB12
    assert vor[12] == pytest.approx(0.0)
    assert vor[13] < 0


def test_auction_lineup_stretches_skill_positions_but_not_k_dst():
    stretched = pj.auction_lineup(DEFAULT_LINEUP, roster_size=15)
    assert stretched["K"] == 1 and stretched["DST"] == 1
    assert stretched["RB"] > DEFAULT_LINEUP["RB"]
    assert stretched["QB"] == pytest.approx(15 / 9)


def test_auction_values_spend_the_room_and_land_in_a_sane_band():
    data = board.load()
    values = pj.auction_values(data, DEFAULT_LINEUP, teams=12, budget=200, roster_size=15)
    assert values, "expected priced players"
    top = max(values.values())
    # A $200 12-team auction puts the best player in the $50-75 range; a baseline
    # bug that concentrates the money shows up here immediately.
    assert 45 <= top <= 80, f"top auction value {top} is outside a believable band"
    assert len(values) >= 12 * 10, "most roster spots should have a price"
    assert min(values.values()) >= 1
    total = sum(values.values())
    assert total <= 12 * 200, "cannot price more than the money in the room"


def test_auction_values_scale_with_budget():
    data = board.load()
    cheap = pj.auction_values(data, DEFAULT_LINEUP, teams=12, budget=100)
    rich = pj.auction_values(data, DEFAULT_LINEUP, teams=12, budget=400)
    assert max(rich.values()) > max(cheap.values()) * 2


def test_packaged_board_ships_projections_and_values():
    data = board.load()
    assert sum(1 for p in data.players if p.projected is not None) >= 190
    priced = [p for p in data.players if p.value]
    assert len(priced) >= 100
    assert data.auction and data.auction["budget"] == 200
    assert max(p.value for p in priced) <= 80


# ---------------------------------------------------------------- weekly / rest of season

# Shape recorded from GET /projections/nfl/2026/3 on 2026-08-16: `player` carries the name and
# position, `opponent` is None on a bye, defenses come through with position DEF and a nickname.
WEEK_ROWS = [
    {"player_id": "9488", "week": 3, "opponent": "WAS", "team": "SEA",
     "player": {"first_name": "Jaxon", "last_name": "Smith-Njigba", "position": "WR", "team": "SEA",
                "injury_status": None},
     "stats": {"pts_ppr": 21.43, "pts_half_ppr": 17.76, "pts_std": 14.1, "gp": 1.0}},
    {"player_id": "8155", "week": 3, "opponent": None, "team": "JAX",
     "player": {"first_name": "Travis", "last_name": "Etienne", "position": "RB", "team": "JAX",
                "injury_status": "Questionable"},
     "stats": {"pts_ppr": 0.0, "pts_half_ppr": 0.0, "pts_std": 0.0}},
    {"player_id": "LAR", "week": 3, "opponent": "DEN", "team": "LAR",
     "player": {"first_name": "Los Angeles", "last_name": "Rams", "position": "DEF", "team": "LAR"},
     "stats": {"pts_ppr": 8.94, "pts_half_ppr": 8.94, "pts_std": 8.94}},
    {"player_id": "x", "week": 3, "opponent": "NE", "player": {"first_name": "Some", "last_name": "Lineman",
                                                                "position": "OL"}, "stats": {"pts_ppr": 1}},
]


def test_weekly_points_by_name_keys_suffix_blind_and_zeroes_byes():
    pts = pj.weekly_points_by_name(WEEK_ROWS, "ppr")
    assert pts["jaxon smith njigba"]["points"] == 21.43
    assert pts["jaxon smith njigba"]["opponent"] == "WAS" and pts["jaxon smith njigba"]["team"] == "SEA"
    etienne = pts["travis etienne"]                     # "Travis Etienne Jr." on a roster joins to this
    assert etienne["points"] == 0.0 and etienne["opponent"] is None and etienne["injury"] == "Questionable"
    assert pts["rams dst"]["pos"] == "DST" and pts["rams dst"]["name"] == "Rams D/ST"   # punctuation-blind key
    assert "some lineman" not in pts
    assert pj.weekly_points_by_name(WEEK_ROWS, "half_ppr")["jaxon smith njigba"]["points"] == 17.76
    with pytest.raises(ValueError, match="unknown scoring"):
        pj.weekly_points_by_name(WEEK_ROWS, "ppr6")


def test_rest_of_season_sums_the_weeks_and_reports_gaps():
    def fake(season, week):
        if week == 5:
            raise OSError("down")
        return WEEK_ROWS
    totals, missing = pj.rest_of_season(2026, 3, "ppr", through=6, fetch=fake)
    assert missing == [5]
    jsn = totals["jaxon smith njigba"]
    assert jsn["points"] == pytest.approx(21.43 * 3) and jsn["games"] == 3
    assert totals["travis etienne"]["points"] == 0.0 and totals["travis etienne"]["games"] == 0


def test_fetch_week_caches_on_disk_and_reuses_within_max_age(tmp_path, monkeypatch):
    import json as _json

    monkeypatch.setenv("FANTASYLEAGUE_CACHE", str(tmp_path))
    calls = []

    class Resp:
        def __init__(self, body): self._b = body
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=0):
        calls.append(req.full_url)
        return Resp(_json.dumps(WEEK_ROWS).encode())

    monkeypatch.setattr(pj.urllib.request, "urlopen", fake_urlopen)
    rows = pj.fetch_week(2026, 3)
    again = pj.fetch_week(2026, 3)
    assert rows == again == WEEK_ROWS
    assert len(calls) == 1 and "/projections/nfl/2026/3?" in calls[0]
    assert (tmp_path / "sleeper-projections-2026-w03.json").exists()
    # A corrupt cache is discarded and refetched rather than crashing.
    (tmp_path / "sleeper-projections-2026-w03.json").write_text("{not json", encoding="utf-8")
    assert pj.fetch_week(2026, 3) == WEEK_ROWS and len(calls) == 2
