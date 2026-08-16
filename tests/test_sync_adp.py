"""ADP refresh from Fantasy Football Calculator — offline."""

from __future__ import annotations

import pytest

from fantasyleague import board
from fantasyleague.models import Player
from fantasyleague.sync import adp

# Shape recorded from GET /api/v1/adp/half-ppr?teams=12&year=2026 on 2026-08-16.
PAYLOAD = {
    "status": "Success",
    "meta": {"type": "Half-PPR", "teams": 12, "rounds": 15, "total_drafts": 2364,
             "start_date": "2026-08-11", "end_date": "2026-08-16"},
    "players": [
        {"player_id": 5672, "name": "Jahmyr Gibbs", "position": "RB", "team": "DET",
         "adp": 1.5, "stdev": 0.7, "bye": 6},
        {"player_id": 5859, "name": "A.J. Brown", "position": "WR", "team": "NE",
         "adp": 40.0, "stdev": 6.0, "bye": 14},
        {"player_id": 1, "name": "Brandon Aubrey", "position": "PK", "team": "DAL",
         "adp": 128.8, "stdev": 12.0, "bye": 10},
        {"player_id": 2, "name": "Houston Defense", "position": "DEF", "team": "HOU",
         "adp": 110.0, "stdev": 14.0, "bye": 7},
    ],
}


@pytest.fixture
def data():
    return board.load()


def test_normalise_strips_punctuation_and_suffixes():
    assert adp.normalise("Ja'Marr Chase") == "jamarr chase"
    assert adp.normalise("Travis Etienne Jr.") == "travis etienne"
    assert adp.normalise("Amon-Ra St. Brown") == "amon ra st brown"


def test_fetch_rejects_unknown_format():
    with pytest.raises(ValueError, match="unknown ADP format"):
        adp.fetch(scoring="superflex")


def test_fetch_rejects_empty_payload(monkeypatch):
    class FakeResponse:
        def read(self):
            return b'{"status":"Success","players":[]}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(adp.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    with pytest.raises(ValueError, match="no ADP players"):
        adp.fetch()


def test_apply_updates_adp_sd_and_bye(data):
    fresh, changed, unmatched = adp.apply(data, PAYLOAD)
    by_name = {p.name: p for p in fresh.players}
    assert by_name["A.J. Brown"].adp == 40.0
    assert by_name["A.J. Brown"].adp_sd == 6.0
    assert by_name["A.J. Brown"].bye == 14
    assert by_name["Brandon Aubrey"].adp == 128.8, "PK maps to K"
    assert by_name["Texans D/ST"].adp == 110.0, "DEF matches on team"
    assert len(unmatched) == len(data.players) - 4
    assert any("A.J. Brown" in c for c in changed)


def test_apply_records_provenance(data):
    fresh, _, _ = adp.apply(data, PAYLOAD)
    assert fresh.adp["source"] == "Fantasy Football Calculator"
    assert "2364 drafts" in fresh.adp["format"]
    assert fresh.adp["window"] == "2026-08-11 to 2026-08-16"


def test_apply_leaves_flags_alone_by_default(data):
    before = {p.name: p.flag for p in data.players}
    fresh, _, _ = adp.apply(data, PAYLOAD)
    assert {p.name: p.flag for p in fresh.players} == before


def test_price_flag_uses_the_gap_between_rank_and_market():
    # Ranked 10, drafted at 30 -> the market is late on him.
    assert adp.price_flag(Player(10, "A", "RB", "DET", 1, adp=30.0)) == "value"
    # Ranked 30, drafted at 10 -> the market pays more than we would.
    assert adp.price_flag(Player(30, "B", "WR", "LAR", 1, adp=10.0)) == "avoid"
    # Inside the threshold either way.
    assert adp.price_flag(Player(10, "C", "RB", "DET", 1, adp=14.0)) is None
    assert adp.price_flag(Player(1, "D", "RB", "DET", 1)) is None


def test_price_flag_skips_kickers_and_defenses():
    """Their board ranks are positional, so an overall ADP is a different scale."""
    assert adp.price_flag(Player(76, "K1", "K", "DAL", 8, adp=128.8)) is None
    assert adp.price_flag(Player(88, "D1", "DST", "HOU", 9, adp=110.0)) is None


def test_threshold_scales_with_rank():
    # 10 picks is decisive at the top...
    assert adp.price_flag(Player(4, "A", "RB", "DET", 1, adp=16.0)) == "value"
    # ...and noise deep in the board.
    assert adp.price_flag(Player(60, "B", "RB", "DET", 1, adp=70.0)) is None


def test_reflag_recomputes_but_never_clobbers_watch(data):
    from dataclasses import replace

    injured = replace(data, players=[replace(p, flag="watch") for p in data.players])
    fresh, _, _ = adp.apply(injured, PAYLOAD, reflag=True)
    assert all(p.flag == "watch" for p in fresh.players), "an injury flag must survive a price refresh"

    clean = replace(data, players=[replace(p, flag=None) for p in data.players])
    fresh, _, _ = adp.apply(clean, PAYLOAD, reflag=True)
    by_name = {p.name: p for p in fresh.players}
    assert by_name["A.J. Brown"].flag == "value"  # rank 21, ADP 40
    assert by_name["Brandon Aubrey"].flag is None
    assert by_name["Texans D/ST"].flag is None


def test_packaged_board_keeps_its_curated_flags():
    """The shipped flags compare ADP with a projected finish, not with rank."""
    data = board.load()
    flagged = [p for p in data.players if p.flag in ("value", "avoid")]
    assert flagged, "packaged board should carry curated price flags"
    assert not any(p.pos in ("K", "DST") and p.flag in ("value", "avoid") for p in data.players)
