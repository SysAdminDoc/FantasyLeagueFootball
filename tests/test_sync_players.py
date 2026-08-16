"""Injury/trending refresh from Sleeper's player database — offline."""

from __future__ import annotations

import json
import time
import urllib.error

import pytest

from fantasyleague import board
from fantasyleague.sync import players as players_mod

# Shapes recorded from GET /v1/players/nfl on 2026-08-16.
DB = {
    "4984": {"full_name": "Josh Allen", "position": "QB", "team": "BUF", "injury_status": None},
    "5859": {"full_name": "A.J. Brown", "position": "WR", "team": "NE",
             "injury_status": "Questionable", "injury_body_part": "Hamstring",
             "practice_participation": "Limited Practice"},
    "9493": {"full_name": "Puka Nacua", "position": "WR", "team": "LAR",
             "injury_status": "Out", "injury_body_part": "Groin"},
    "HOU": {"first_name": "Houston", "last_name": "Texans", "position": "DEF", "team": "HOU"},
    "9999": {"full_name": "Nobody Here", "position": "WR", "team": "FA", "injury_status": "IR"},
}


@pytest.fixture
def data():
    return board.load()


def test_describe_builds_a_one_line_status():
    assert players_mod.describe(DB["4984"]) is None
    assert players_mod.describe(DB["5859"]) == "Questionable — hamstring|Limited Practice"
    assert players_mod.describe(DB["9493"]) == "Out — groin"
    assert players_mod.describe({"injury_status": "Out", "injury_body_part": "Not Injury Related"}) == "Out"


def test_apply_status_rebuilds_the_injury_board(data):
    fresh, updated = players_mod.apply_status(data, DB)
    names = {i.name for i in fresh.injuries}
    assert names == {"A.J. Brown", "Puka Nacua"}
    assert len(updated) == 2
    by_name = {i.name: i for i in fresh.injuries}
    assert by_name["Puka Nacua"].severity == "out"
    assert by_name["A.J. Brown"].severity == "risk"
    # sorted worst-first
    assert [i.severity for i in fresh.injuries] == ["out", "risk"]


def test_apply_status_sets_and_clears_watch_but_keeps_editorial_flags(data):
    fresh, _ = players_mod.apply_status(data, DB)
    by_name = {p.name: p for p in fresh.players}
    assert by_name["A.J. Brown"].flag == "watch"
    assert by_name["Puka Nacua"].flag == "watch"
    # A player the refresh reports healthy loses any stale watch flag.
    assert by_name["Emeka Egbuka"].flag is None
    # ...but value/avoid judgements about price survive a health refresh.
    assert by_name["Breece Hall"].flag == "value"
    assert by_name["Joe Burrow"].flag == "avoid"


def test_apply_status_is_idempotent(data):
    once, _ = players_mod.apply_status(data, DB)
    twice, _ = players_mod.apply_status(once, DB)
    assert [p.flag for p in once.players] == [p.flag for p in twice.players]
    assert [i.status for i in once.injuries] == [i.status for i in twice.injuries]


def test_name_trending_resolves_ids_and_drops_unknowns():
    rows = [{"player_id": "4984", "count": 100}, {"player_id": "HOU", "count": 50},
            {"player_id": "nope", "count": 10}]
    out = players_mod.name_trending(rows, DB)
    assert [t["name"] for t in out] == ["Josh Allen", "Houston Texans"]
    assert out[0]["pos"] == "QB" and out[0]["count"] == 100


def test_cache_is_used_within_max_age(tmp_path, monkeypatch):
    cache = tmp_path / "sleeper-players-nfl.json"
    cache.write_text(json.dumps(DB), encoding="utf-8")
    monkeypatch.setattr(players_mod, "cache_path", lambda: cache)

    def no_network(*a, **k):
        raise AssertionError("must not hit the network inside max_age")

    monkeypatch.setattr(players_mod.urllib.request, "urlopen", no_network)
    payload, origin = players_mod.fetch_players()
    assert origin == "cache" and payload == DB


def test_stale_cache_is_used_when_offline(tmp_path, monkeypatch):
    cache = tmp_path / "sleeper-players-nfl.json"
    cache.write_text(json.dumps(DB), encoding="utf-8")
    old = time.time() - (players_mod.MAX_AGE_SECONDS + 60)
    import os

    os.utime(cache, (old, old))
    monkeypatch.setattr(players_mod, "cache_path", lambda: cache)
    monkeypatch.setattr(
        players_mod.urllib.request, "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("offline")),
    )
    payload, origin = players_mod.fetch_players()
    assert origin == "stale-cache" and payload == DB


def test_offline_with_no_cache_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(players_mod, "cache_path", lambda: tmp_path / "missing.json")
    monkeypatch.setattr(
        players_mod.urllib.request, "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("offline")),
    )
    with pytest.raises(urllib.error.URLError):
        players_mod.fetch_players()


def test_cache_dir_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("FANTASYLEAGUE_CACHE", str(tmp_path))
    assert players_mod.cache_dir() == tmp_path


def test_packaged_board_carries_refresh_output():
    data = board.load()
    assert data.refreshed and "UTC" in data.refreshed
    assert data.trending and all(t["name"] and t["count"] for t in data.trending)
    assert data.injuries, "packaged board should ship a refreshed injury list"


def test_save_round_trips(tmp_path):
    data = board.load()
    out = board.save(data, tmp_path / "copy.json")
    again = board.load(out)
    assert [p.name for p in again.players] == [p.name for p in data.players]
    assert again.refreshed == data.refreshed
    assert again.trending == data.trending
    assert [i.status for i in again.injuries] == [i.status for i in data.injuries]


def test_apply_profile_attaches_age_and_experience(data):
    from dataclasses import replace

    # Start from a board with no profile data, so a partial record is visible as partial.
    data = replace(data, players=[replace(p, age=None, exp=None) for p in data.players])
    db = {
        "4984": {"age": 30, "years_exp": 8},
        "9221": {"age": 24},                 # age only
        "5859": {"years_exp": 7},            # experience only
    }
    fresh, hits = players_mod.apply_profile(data, db)
    by_name = {p.name: p for p in fresh.players}
    assert hits == 3
    assert by_name["Josh Allen"].age == 30 and by_name["Josh Allen"].exp == 8
    assert by_name["Jahmyr Gibbs"].age == 24
    assert by_name["A.J. Brown"].exp == 7 and by_name["A.J. Brown"].age is None


def test_apply_profile_leaves_unknown_players_alone(data):
    """An empty database must not wipe profile data the board already has."""
    before = [(p.age, p.exp) for p in data.players]
    fresh, hits = players_mod.apply_profile(data, {})
    assert hits == 0
    assert [(p.age, p.exp) for p in fresh.players] == before


def test_packaged_board_carries_keeper_context():
    d = board.load()
    aged = [p for p in d.players if p.age]
    assert len(aged) >= 150
    assert all(18 <= p.age <= 50 for p in aged)
    rookies = [p for p in d.players if p.exp == 0]
    assert rookies, "expected some rookies on a 200-player board"
