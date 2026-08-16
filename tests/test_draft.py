"""Snake-draft arithmetic and projected availability."""

from __future__ import annotations

import pytest

from fantasyleague import board, draft


def test_snake_picks_12_team_slot_5():
    assert draft.snake_picks(12, 5)[:6] == [5, 20, 29, 44, 53, 68]


def test_snake_picks_edges():
    assert draft.snake_picks(10, 1)[:4] == [1, 20, 21, 40]
    assert draft.snake_picks(10, 10)[:4] == [10, 11, 30, 31]


def test_snake_picks_rejects_bad_slot():
    with pytest.raises(ValueError):
        draft.snake_picks(12, 13)
    with pytest.raises(ValueError):
        draft.snake_picks(1, 1)


def test_next_picks_from_current():
    assert draft.next_picks(12, 5, 6) == [20, 29]
    assert draft.next_picks(12, 5, 20) == [20, 29]
    assert draft.next_picks(12, 5, 21, count=3) == [29, 44, 53]


def test_availability_matches_published_method():
    # ADP 20, σ = ADP/4 = 5: 1 − Φ((29−20)/5) ≈ 0.036, 1 − Φ((17−20)/5) ≈ 0.726
    assert draft.availability(20, None, 29) == pytest.approx(0.0359, abs=0.005)
    assert draft.availability(20, None, 17) == pytest.approx(0.7257, abs=0.005)
    # Market spread wins when known
    assert draft.availability(20, 2.0, 24) == pytest.approx(0.0228, abs=0.003)


def test_sigma_floor_and_bands():
    assert draft.sigma_for(1.5, 0.0) == draft.MIN_SD
    assert draft.band(0.75) == "wait"
    assert draft.band(0.2) == "now"
    assert draft.band(0.45) == "toss-up"


def test_player_availability_uses_dataset_adp():
    data = board.load()
    gibbs = data.players[0]
    assert gibbs.adp is not None and gibbs.adp < 3
    assert draft.player_availability(gibbs, 5) < 0.02
    no_adp = next(p for p in data.players if p.adp is None)
    assert draft.player_availability(no_adp, 5) is None


def test_dataset_carries_adp_provenance_and_byes():
    data = board.load()
    assert data.adp and data.adp["source"] and data.adp["window"]
    with_adp = [p for p in data.players if p.adp is not None]
    assert len(with_adp) >= 95, "expected ADP on nearly every player"
    assert all(p.adp_sd is None or p.adp_sd >= 0 for p in with_adp)
    assert sum(1 for p in data.players if p.bye) >= 95
