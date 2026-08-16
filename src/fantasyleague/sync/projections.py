"""Season projections from Sleeper.

`GET /projections/nfl/{season}?season_type=regular&position[]=RB&order_by=pts_half_ppr`
returns one row per player with a `stats` block carrying projected points for each
scoring format (`pts_half_ppr`, `pts_ppr`, `pts_std`) plus that format's ADP.

Projected points are what value-based drafting actually needs: a rank is an
ordering, a projection is a quantity, and only a quantity can say how much
better one player is than the replacement at his position.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import replace

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
