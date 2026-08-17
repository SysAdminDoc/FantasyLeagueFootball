"""Season projections from Sleeper.

`GET /projections/nfl/{season}?season_type=regular&position[]=RB&order_by=pts_half_ppr`
returns one row per player with a `stats` block carrying projected points for each
scoring format (`pts_half_ppr`, `pts_ppr`, `pts_std`) plus that format's ADP.

Projected points are what value-based drafting actually needs: a rank is an
ordering, a projection is a quantity, and only a quantity can say how much
better one player is than the replacement at his position.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

from ..models import Dataset, Player
from .sleeper import USER_AGENT

API = "https://api.sleeper.app/projections/nfl"
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
POINTS_KEY = {"half_ppr": "pts_half_ppr", "ppr": "pts_ppr", "standard": "pts_std"}


def fetch(season: int = 2026, positions: tuple[str, ...] = POSITIONS, timeout: float = 60.0) -> list[dict]:
    """Projection rows for *positions*. Raises on network failure."""
    query = "&".join(f"position[]={p}" for p in positions)
    url = f"{API}/{season}?season_type=regular&order_by=pts_half_ppr&{query}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        rows = json.loads(r.read())
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"no projections returned for {season}")
    return rows


def points_by_id(rows: list[dict], scoring: str = "half_ppr") -> dict[str, float]:
    """{sleeper player id: projected points} for one scoring format."""
    key = POINTS_KEY.get(scoring)
    if key is None:
        raise ValueError(f"unknown scoring {scoring!r}; expected one of {', '.join(POINTS_KEY)}")
    out: dict[str, float] = {}
    for row in rows:
        pid = row.get("player_id")
        pts = (row.get("stats") or {}).get(key)
        if pid is not None and isinstance(pts, int | float):
            out[str(pid)] = float(pts)
    return out


def apply(data: Dataset, points: dict[str, float]) -> tuple[Dataset, int]:
    """Attach projected points to players, joined on `ids.sleeper`."""
    players, hits = [], 0
    for p in data.players:
        pts = points.get(p.id_for("sleeper") or "")
        if pts is None:
            players.append(p)
        else:
            hits += 1
            players.append(replace(p, projected=pts))
    return replace(data, players=players), hits


# ---------------------------------------------------------------- value-based

def replacement_levels(players: list[Player], lineup: dict[str, int], teams: int) -> dict[str, float]:
    """Projected points of the last *starter* at each position.

    The baseline is the worst starter, not the last player drafted: deep benches
    full of speculative picks do not change what a starting slot is worth.
    FLEX demand is spread across RB/WR/TE in proportion to how often each fills it.
    """
    flex_share = {"RB": 0.5, "WR": 0.4, "TE": 0.1}
    demand: dict[str, float] = {}
    for pos, slots in lineup.items():
        if pos == "FLEX":
            for fpos, share in flex_share.items():
                demand[fpos] = demand.get(fpos, 0) + slots * share
        else:
            demand[pos] = demand.get(pos, 0) + slots

    levels: dict[str, float] = {}
    for pos, per_team in demand.items():
        pool = sorted(
            (p.projected for p in players if p.pos == pos and p.projected is not None), reverse=True
        )
        if not pool:
            continue
        idx = max(0, min(len(pool) - 1, round(per_team * teams) - 1))
        levels[pos] = pool[idx]
    return levels


def value_over_replacement(data: Dataset, lineup: dict[str, int], teams: int) -> dict[int, float]:
    """{rank: points above the last starter at that position}."""
    levels = replacement_levels(data.players, lineup, teams)
    out: dict[int, float] = {}
    for p in data.players:
        if p.projected is not None and p.pos in levels:
            out[p.rank] = round(p.projected - levels[p.pos], 1)
    return out


def auction_lineup(lineup: dict[str, int], roster_size: int) -> dict[str, float]:
    """Lineup demand stretched to bench depth, for pricing rather than ranking.

    Ranking wants the *worst starter* as the baseline. Pricing does not: only
    ~100 players clear a starter baseline, but 12 x 15 = 180 get bought, so all
    the money piles onto the top of a short list and the best player comes out at
    half the budget. Stretching each skill position toward roster depth puts
    roughly as many players above replacement as there are roster spots. Kickers
    and defenses stay at one — nobody rosters a backup kicker.
    """
    starters = sum(lineup.values())
    stretch = roster_size / starters if starters else 1.0
    return {
        pos: slots if pos in ("K", "DST") else slots * stretch
        for pos, slots in lineup.items()
    }


def auction_values(
    data: Dataset, lineup: dict[str, int], teams: int, budget: int = 200, roster_size: int = 15
) -> dict[int, int]:
    """{rank: dollar value}, priced off a bench-depth baseline.

    Every drafted player costs at least $1, so that money is reserved first and
    the remaining surplus is split in proportion to value over replacement.
    """
    vor = value_over_replacement(data, auction_lineup(lineup, roster_size), teams)
    draftable = sorted(vor.items(), key=lambda kv: kv[1], reverse=True)[: teams * roster_size]
    positive = [(rank, v) for rank, v in draftable if v > 0]
    if not positive:
        return {}
    spendable = teams * budget - teams * roster_size
    total = sum(v for _, v in positive)
    return {rank: max(1, round(v / total * spendable) + 1) for rank, v in positive}


# ---------------------------------------------------------------- weekly

REGULAR_SEASON_WEEKS = 18
WEEKLY_MAX_AGE_SECONDS = 6 * 3600


def _weekly_cache_path(season: int, week: int) -> Path:
    from .players import cache_dir

    return cache_dir() / f"sleeper-projections-{season}-w{week:02d}.json"


def fetch_week(
    season: int, week: int, positions: tuple[str, ...] = POSITIONS, timeout: float = 60.0,
    max_age: float = WEEKLY_MAX_AGE_SECONDS,
) -> list[dict]:
    """Projection rows for one week — `/projections/nfl/{season}/{week}` — cached for a few hours.

    Rows carry `player` (name, position, team, injury_status), `opponent` (None on
    a bye) and the same `stats` block as the season endpoint. Weekly numbers move
    all week as news lands, so the cache is short; the season endpoint's 24h would
    hide a Wednesday injury designation until Thursday.
    """
    path = _weekly_cache_path(season, week)
    try:
        if path.exists() and time.time() - path.stat().st_mtime < max_age:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(cached, list) and cached:
                return cached
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
    query = "&".join(f"position[]={p}" for p in positions)
    url = f"{API}/{season}/{week}?season_type=regular&order_by=pts_half_ppr&{query}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        rows = json.loads(r.read())
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"no projections returned for {season} week {week}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows), encoding="utf-8")
    tmp.replace(path)
    return rows


def weekly_points_by_name(rows: list[dict], scoring: str = "half_ppr") -> dict[str, dict]:
    """{normalised name: {name, pos, team, points, opponent, sleeper_id, injury}} for one week.

    Keyed by name rather than id so a roster typed by hand still joins; a player
    with no game that week (no opponent) is kept with 0 points, which is what a
    bye is worth. Defenses come through as "<Nickname> D/ST" to match the board.
    """
    from ..board import join_key

    key = POINTS_KEY.get(scoring)
    if key is None:
        raise ValueError(f"unknown scoring {scoring!r}; expected one of {', '.join(POINTS_KEY)}")
    out: dict[str, dict] = {}
    for row in rows:
        player = row.get("player") or {}
        pos = player.get("position") or ""
        if pos == "DEF":
            pos, name = "DST", f"{player.get('last_name', row.get('player_id', ''))} D/ST"
        else:
            name = " ".join(filter(None, [player.get("first_name"), player.get("last_name")]))
        if not name or pos not in ("QB", "RB", "WR", "TE", "K", "DST"):
            continue
        pts = (row.get("stats") or {}).get(key)
        opp = row.get("opponent")
        entry = {
            "name": name, "pos": pos, "team": row.get("team") or player.get("team") or "",
            "points": float(pts) if isinstance(pts, int | float) and opp else 0.0,
            "opponent": opp, "sleeper_id": str(row.get("player_id") or ""),
            "injury": player.get("injury_status"),
        }
        n = join_key(name)
        # Two players can share a name (rare); keep the higher-projected one.
        if n not in out or entry["points"] > out[n]["points"]:
            out[n] = entry
    return out


def rest_of_season(
    season: int, from_week: int, scoring: str = "half_ppr", through: int = REGULAR_SEASON_WEEKS - 1,
    fetch: object = None,
) -> tuple[dict[str, dict], list[int]]:
    """Sum weekly projections from *from_week* through *through* (week 17 by default).

    Fantasy playoffs end in week 17, so week 18 is left out. Returns
    ({normalised name: {name, pos, team, points}}, [weeks that could not be fetched]).
    A week that fails to download is skipped rather than aborting the sum — a
    partial rest-of-season is still a far better trade lens than a full-season number.
    """
    getter = fetch or fetch_week
    totals: dict[str, dict] = {}
    missing: list[int] = []
    for week in range(from_week, through + 1):
        try:
            rows = getter(season, week)
        except (OSError, http.client.HTTPException, ValueError):
            missing.append(week)
            continue
        for n, e in weekly_points_by_name(rows, scoring).items():
            slot = totals.setdefault(n, {"name": e["name"], "pos": e["pos"], "team": e["team"], "points": 0.0,
                                         "sleeper_id": e["sleeper_id"], "games": 0})
            slot["points"] += e["points"]
            slot["team"] = e["team"] or slot["team"]
            if e["opponent"]:
                slot["games"] += 1
    return totals, missing
