"""Scoring-format variants: superflex, full PPR, standard."""

from __future__ import annotations

import pytest

from fantasyleague import board, variant
from fantasyleague.models import DEFAULT_LINEUP


# Minimal ADP payloads: superflex drafts quarterbacks first, half-PPR does not.
def payload(order):
    return {
        "meta": {"type": "t", "teams": 12, "total_drafts": 1, "start_date": "a", "end_date": "b"},
        "players": [
            {"name": n, "position": pos, "team": tm, "adp": float(i + 1), "stdev": 1.0, "bye": 5}
            for i, (n, pos, tm) in enumerate(order)
        ],
    }


SUPERFLEX = payload([("Josh Allen", "QB", "BUF"), ("Jahmyr Gibbs", "RB", "DET"),
                     ("Drake Maye", "QB", "NE"), ("Bijan Robinson", "RB", "ATL")])


@pytest.fixture
def offline(monkeypatch):
    """No network: fixed ADP, no projections."""
    monkeypatch.setattr(variant.adp_mod, "fetch", lambda **k: SUPERFLEX)
    monkeypatch.setattr(
        variant.proj_mod, "fetch", lambda **k: (_ for _ in ()).throw(OSError("offline"))
    )


def test_rejects_unknown_variant():
    with pytest.raises(ValueError, match="unknown variant"):
        variant.build(board.load(), "superflex")


def test_reranks_by_the_target_market(offline):
    """Superflex ADP pulls quarterbacks to the top of the board."""
    base = board.load()
    base_qb = next(p for p in base.players if p.name == "Josh Allen").rank
    built, notes = variant.build(base, "2qb")
    assert built.players[0].name == "Josh Allen"
    assert base_qb > 1, "the half-PPR board does not open with a QB"
    assert built.players[1].name == "Jahmyr Gibbs"
    maye = next(p for p in built.players if p.name == "Drake Maye")
    assert maye.rank <= 5
    assert any("Superflex" in n for n in notes)


def test_the_board_is_ordered_by_adp_and_stays_valid(offline):
    built, _ = variant.build(board.load(), "2qb")
    board.validate(built)
    assert [p.rank for p in built.players] == list(range(1, len(built.players) + 1))
    with_adp = [p.adp for p in built.players if p.adp is not None]
    assert with_adp == sorted(with_adp), "players must be ordered by ADP"
    # Anyone the market does not price at all sorts behind everyone it does.
    first_none = next((i for i, p in enumerate(built.players) if p.adp is None), len(built.players))
    assert all(p.adp is None for p in built.players[first_none:])


def test_tiers_are_rebuilt_to_cover_every_player(offline):
    built, _ = variant.build(board.load(), "2qb")
    known = {t.n for t in built.tiers}
    assert {p.tier for p in built.players} <= known
    assert len(built.tiers) == len(variant.TIER_SHAPE)
    counts = [sum(1 for p in built.players if p.tier == t.n) for t in built.tiers]
    assert all(c > 0 for c in counts)
    assert sum(counts) == len(built.players)


def test_scoring_and_format_are_restamped(offline):
    built, _ = variant.build(board.load(), "2qb", teams=10)
    assert built.scoring == "2qb"
    assert "Superflex" in built.format and "10-team" in built.format


def test_superflex_prices_quarterbacks_off_a_two_qb_lineup():
    """The whole point of superflex: replacement is QB24, not QB12."""
    from fantasyleague.sync import projections as pj

    data = board.load()
    one_qb = pj.auction_values(data, DEFAULT_LINEUP, teams=12)
    two_qb = pj.auction_values(data, variant.SUPERFLEX_LINEUP, teams=12)
    allen = next(p for p in data.players if p.name == "Josh Allen")
    assert two_qb[allen.rank] > one_qb[allen.rank], "a QB must cost more in superflex"


def test_build_round_trips_through_save_and_load(offline, tmp_path):
    built, _ = variant.build(board.load(), "2qb")
    out = board.save(built, tmp_path / "sf.json")
    again = board.load(out)
    assert [p.name for p in again.players[:3]] == [p.name for p in built.players[:3]]
    assert again.scoring == "2qb"
