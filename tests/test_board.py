"""Dataset integrity and draft-time query behaviour."""

from __future__ import annotations

import pytest

from fantasyleague import board
from fantasyleague.models import Dataset, Player, Tier


@pytest.fixture(scope="module")
def data():
    return board.load()


# --- packaged dataset --------------------------------------------------------


def test_packaged_board_loads_and_validates(data):
    assert data.season == 2026
    assert data.scoring == "half_ppr"
    assert len(data.players) == 99  # 75 skill players + 12 K + 12 DST
    assert len(data.tiers) == 9


def test_every_player_belongs_to_a_defined_tier(data):
    known = {t.n for t in data.tiers}
    assert {p.tier for p in data.players} <= known


def test_ranks_are_contiguous_from_one(data):
    assert [p.rank for p in data.players] == list(range(1, len(data.players) + 1))


def test_rails_are_populated(data):
    assert data.do_not_draft and data.injuries and data.sleepers and data.sources
    assert len(data.plan) == 4


def test_every_position_is_represented(data):
    counts = board.position_counts(data)
    assert set(counts) == {"QB", "RB", "WR", "TE", "K", "DST"}
    assert counts["K"] == 12 and counts["DST"] == 12
    assert sum(counts.values()) == len(data.players)


# --- validation --------------------------------------------------------------


def _mk(players, tiers=(1,)):
    return Dataset(
        season=2026,
        scoring="half_ppr",
        format="test",
        updated="2026-08-16",
        tiers=[Tier(n=n, name=f"T{n}", range="", note="") for n in tiers],
        players=players,
    )


def test_validate_rejects_gapped_ranks():
    d = _mk([Player(1, "A", "RB", "DET", 1), Player(3, "B", "WR", "LAR", 1)])
    with pytest.raises(ValueError, match="no gaps"):
        board.validate(d)


def test_validate_rejects_duplicate_ranks():
    d = _mk([Player(1, "A", "RB", "DET", 1), Player(1, "B", "WR", "LAR", 1)])
    with pytest.raises(ValueError, match="duplicate ranks"):
        board.validate(d)


def test_validate_rejects_duplicate_names():
    d = _mk([Player(1, "A", "RB", "DET", 1), Player(2, "A", "WR", "LAR", 1)])
    with pytest.raises(ValueError, match="duplicate players"):
        board.validate(d)


def test_validate_rejects_orphan_tier():
    d = _mk([Player(1, "A", "RB", "DET", 9)])
    with pytest.raises(ValueError, match="undefined tiers"):
        board.validate(d)


def test_player_rejects_unknown_position():
    with pytest.raises(ValueError, match="unknown position"):
        Player(1, "A", "P", "DET", 1)


def test_player_rejects_unknown_flag():
    with pytest.raises(ValueError, match="unknown flag"):
        Player(1, "A", "RB", "DET", 1, flag="sleeper")


# --- queries -----------------------------------------------------------------


def test_best_available_skips_drafted(data):
    top = board.best_available(data, limit=3)
    assert [p.rank for p in top] == [1, 2, 3]

    after = board.best_available(data, drafted={1, 2}, limit=3)
    assert [p.rank for p in after] == [3, 4, 5]


def test_best_available_respects_position(data):
    qbs = board.best_available(data, pos="QB", limit=5)
    assert all(p.pos == "QB" for p in qbs)
    assert qbs[0].name == "Josh Allen"


def test_filter_by_flag(data):
    values = board.filter_players(data, flag="value")
    assert values, "expected at least one value pick"
    assert all(p.flag == "value" for p in values)
    assert "Breece Hall" in {p.name for p in values}


def test_tier_breaks_fire_at_threshold(data):
    tier1 = [p.rank for p in data.players if p.tier == 1]
    # Drain tier 1 to exactly two remaining.
    drafted = set(tier1[: len(tier1) - 2])
    hits = dict(board.tier_breaks(data, drafted=drafted))
    assert 1 in {t.n for t in hits}
    assert next(iter(hits.values())) == 2


def test_tier_breaks_ignore_empty_tiers(data):
    tier1 = {p.rank for p in data.players if p.tier == 1}
    hits = board.tier_breaks(data, drafted=tier1)
    assert 1 not in {t.n for t in hits}


def test_resolve_by_rank_and_name(data):
    assert board.resolve(data, 1).name == "Jahmyr Gibbs"
    assert board.resolve(data, "1").name == "Jahmyr Gibbs"
    assert board.resolve(data, "gibbs").rank == 1
    assert board.resolve(data, "JAHMYR GIBBS").rank == 1
    assert board.resolve(data, "ja'marr").name == "Ja'Marr Chase"
    assert board.resolve(data, "bijan").name == "Bijan Robinson"


def test_resolve_prefers_exact_then_prefix_over_substring(data):
    # "chase" is a prefix of Chase Brown and a substring of Ja'Marr Chase — prefix wins.
    assert board.resolve(data, "chase").name == "Chase Brown"


def test_resolve_rejects_ambiguous_and_unknown(data):
    with pytest.raises(ValueError, match=r"ambiguous: .*Brown"):
        board.resolve(data, "brown")
    with pytest.raises(ValueError, match="no player matches"):
        board.resolve(data, "zzzz")
    with pytest.raises(ValueError, match="no player has rank 999"):
        board.resolve(data, 999)


def test_resolve_many_mixes_ranks_and_names(data):
    assert board.resolve_many(data, ["1", "bijan", 3]) == {1, 2, 3}


def test_position_counts_drop_as_players_go(data):
    before = board.position_counts(data)
    rb = next(p for p in data.players if p.pos == "RB")
    after = board.position_counts(data, drafted={rb.rank})
    assert after["RB"] == before["RB"] - 1


# --- external ids ------------------------------------------------------------


def test_every_player_has_a_sleeper_id(data):
    missing = [p.name for p in data.players if p.id_for("sleeper") is None]
    assert not missing, f"players without a Sleeper id: {missing}"


def test_external_ids_are_unique(data):
    for source in ("sleeper", "yahoo", "espn"):
        ids = [p.id_for(source) for p in data.players if p.id_for(source) is not None]
        assert len(ids) == len(set(ids)), f"duplicate {source} ids"


def test_by_external_id_joins_on_string_keys(data):
    m = board.by_external_id(data, "sleeper")
    assert m["4984"].name == "Josh Allen"
    assert m["HOU"].pos == "DST"
    assert "yahoo" in data.players[0].ids


def test_validate_rejects_duplicate_external_ids():
    a = Player(1, "A", "RB", "DET", 1, ids={"sleeper": "1"})
    b = Player(2, "B", "WR", "LAR", 1, ids={"sleeper": 1})  # int and str must collide
    d = _mk([a, b])
    with pytest.raises(ValueError, match="duplicate sleeper id 1"):
        board.validate(d)


def test_player_rejects_unknown_id_source():
    with pytest.raises(ValueError, match="unknown id source"):
        Player(1, "A", "RB", "DET", 1, ids={"nfl": "x"})


def test_k_and_dst_sit_in_their_own_tiers(data):
    assert {p.tier for p in data.players if p.pos == "K"} == {8}
    assert {p.tier for p in data.players if p.pos == "DST"} == {9}
    assert board.best_available(data, pos="K", limit=1)[0].name == "Brandon Aubrey"
    assert board.best_available(data, pos="DST", limit=1)[0].team == "HOU"
