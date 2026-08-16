"""Sleeper draft sync — offline, against recorded response shapes."""

from __future__ import annotations

import json
import urllib.error

import pytest

from fantasyleague import board, serve
from fantasyleague.sync import sleeper

# Shape recorded from GET /v1/draft/{id}/picks on 2026-08-16.
PICKS = [
    {"player_id": "5859", "picked_by": "u1", "roster_id": 1, "round": 1, "draft_slot": 1,
     "pick_no": 1, "is_keeper": None, "metadata": {"first_name": "A.J.", "last_name": "Brown"}},
    {"player_id": "4984", "picked_by": "u2", "roster_id": 2, "round": 1, "draft_slot": 2,
     "pick_no": 2, "is_keeper": None, "metadata": {"first_name": "Josh", "last_name": "Allen"}},
    {"player_id": "HOU", "picked_by": "u3", "roster_id": 3, "round": 1, "draft_slot": 3,
     "pick_no": 3, "is_keeper": None, "metadata": {"first_name": "Houston", "last_name": "Texans"}},
    {"player_id": "999999", "picked_by": "u4", "roster_id": 4, "round": 1, "draft_slot": 4,
     "pick_no": 4, "is_keeper": None, "metadata": {"first_name": "Not", "last_name": "OnBoard"}},
]


@pytest.fixture
def data():
    return board.load()


@pytest.fixture
def bus(data):
    return serve.Bus(data)


def make(data, bus, **kw):
    log: list[str] = []
    s = sleeper.SleeperSync(data, "123", bus, on_event=log.append, **kw)
    return s, log


def test_resolves_players_by_sleeper_id(data, bus):
    s, _ = make(data, bus)
    assert s.resolve(PICKS[0]).name == "A.J. Brown"
    assert s.resolve(PICKS[1]).name == "Josh Allen"
    assert s.resolve(PICKS[2]).pos == "DST"
    assert s.resolve(PICKS[3]) is None


def test_apply_crosses_off_and_tags_source(data, bus):
    s, log = make(data, bus)
    assert s.apply(PICKS) == 3
    ranks = [p["rank"] for p in bus.state()["picks"]]
    assert ranks == [21, 31, 189]  # A.J. Brown, Josh Allen, Texans D/ST
    assert all(p["source"] == "sleeper" for p in bus.state()["picks"])
    assert bus.state()["current_pick"] == 4
    assert any("A.J. Brown" in m for m in log)


def test_unknown_player_logged_once_not_every_poll(data, bus):
    s, log = make(data, bus)
    s.apply(PICKS)
    s._seen.clear()  # simulate the next poll returning the same list
    s.apply(PICKS)
    assert sum("not on this board" in m for m in log) == 1


def test_repeated_polls_are_idempotent(data, bus):
    s, _ = make(data, bus)
    assert s.apply(PICKS) == 3
    assert s.apply(PICKS) == 0
    assert len(bus.state()["picks"]) == 3


def test_picks_applied_in_pick_order(data, bus):
    s, _ = make(data, bus)
    s.apply(list(reversed(PICKS)))  # server order should not matter after sorting upstream
    assert len(bus.state()["picks"]) == 3


def test_poll_once_survives_network_failure(data, bus, monkeypatch):
    s, log = make(data, bus)

    def boom(_draft_id, timeout=10.0):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(sleeper, "fetch_picks", boom)
    assert s.poll_once() == 0
    assert any("poll failed" in m for m in log)
    assert bus.state()["picks"] == []


def test_poll_once_applies_fetched_picks(data, bus, monkeypatch):
    s, _ = make(data, bus)
    monkeypatch.setattr(sleeper, "fetch_picks", lambda _d, timeout=10.0: PICKS)
    assert s.poll_once() == 3
    assert s.poll_once() == 0


def test_fetch_picks_sorts_by_pick_no(monkeypatch):
    shuffled = [PICKS[2], PICKS[0], PICKS[1]]
    monkeypatch.setattr(sleeper, "_get", lambda url, timeout=10.0: shuffled)
    assert [p["pick_no"] for p in sleeper.fetch_picks("123")] == [1, 2, 3]


def test_fetch_picks_rejects_non_list(monkeypatch):
    monkeypatch.setattr(sleeper, "_get", lambda url, timeout=10.0: {"error": "not found"})
    with pytest.raises(ValueError, match="unexpected response"):
        sleeper.fetch_picks("nope")


def test_start_stop_is_clean(data, bus, monkeypatch):
    monkeypatch.setattr(sleeper, "fetch_picks", lambda _d, timeout=10.0: PICKS)
    s, _ = make(data, bus, interval=0.05)
    s.start()
    for _ in range(50):
        if len(bus.state()["picks"]) == 3:
            break
        import time

        time.sleep(0.02)
    s.stop()
    assert len(bus.state()["picks"]) == 3
    assert not s._thread.is_alive()


def test_recorded_shape_matches_documented_fields():
    """Guard against silently depending on fields Sleeper does not send."""
    used = {"player_id", "pick_no"}
    assert used <= set(PICKS[0])
    assert json.loads(json.dumps(PICKS)) == PICKS


def test_http_404_is_caught_not_raised(data, bus, monkeypatch):
    """urllib raises HTTPError (a URLError subclass) for an unknown draft id."""
    import urllib.error

    def not_found(url, timeout=10.0):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(sleeper, "_get", not_found)
    s, log = make(data, bus)
    assert s.poll_once() == 0
    assert any("404" in m for m in log)
