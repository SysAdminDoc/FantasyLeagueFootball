"""Average draft position from Fantasy Football Calculator.

Public, no key: GET /api/v1/adp/{format}?teams=N&year=YYYY&position=all
Returns per player `adp`, `stdev`, `bye`, plus a meta block describing the
sample (format, team count, draft count, date window) which is stored as
provenance so the board can say where its numbers came from.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import replace

from ..models import Dataset, Player
from .sleeper import USER_AGENT

API = "https://fantasyfootballcalculator.com/api/v1/adp"
FORMATS = ("half-ppr", "ppr", "standard", "2qb", "dynasty", "rookie")
POS_MAP = {"PK": "K", "DEF": "DST"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# A price gap only means something relative to where a player goes: eight picks
# at the top of round 1 is a chasm, eight picks in round 9 is noise.
MIN_GAP = 8
REL_GAP = 0.25


def normalise(name: str) -> str:
    """Comparable form of a player name: lowercase, no punctuation, no suffix."""
    tokens = re.sub(r"[^a-z0-9 ]", "", name.lower().replace("-", " ")).split()
    return " ".join(t for t in tokens if t not in SUFFIXES)


def fetch(scoring: str = "half-ppr", teams: int = 12, year: int = 2026, timeout: float = 20.0) -> dict:
    """Raw ADP payload. Raises on network failure or an unusable response."""
    if scoring not in FORMATS:
        raise ValueError(f"unknown ADP format {scoring!r}; expected one of {', '.join(FORMATS)}")
    url = f"{API}/{scoring}?teams={teams}&year={year}&position=all"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read())
    if not isinstance(payload, dict) or not payload.get("players"):
        raise ValueError(f"no ADP players returned for {scoring} {year}")
    return payload


def index(payload: dict) -> tuple[dict, dict]:
    """(by (name, pos), by team for defenses) lookups over an ADP payload."""
    by_name: dict[tuple[str, str], list[dict]] = {}
    by_team: dict[str, dict] = {}
    for row in payload["players"]:
        pos = POS_MAP.get(row.get("position"), row.get("position"))
        if pos == "DST":
            by_team[row.get("team")] = row
        by_name.setdefault((normalise(row.get("name", "")), pos), []).append(row)
    return by_name, by_team


# K and DST are ranked *within their position* on this board (they occupy the tail
# ranks but are drafted in the last two rounds), so their rank is not on the same
# scale as an overall ADP. Comparing the two makes every kicker look like a steal.
PRICED_POSITIONS = ("QB", "RB", "WR", "TE")


def price_flag(player: Player) -> str | None:
    """`value` when the market drafts him later than this board ranks him, `avoid` earlier.

    Returns None for K/DST, whose board ranks are positional rather than overall.
    """
    if player.adp is None or player.pos not in PRICED_POSITIONS:
        return None
    gap = player.adp - player.rank
    threshold = max(MIN_GAP, REL_GAP * player.rank)
    if gap >= threshold:
        return "value"
    if -gap >= threshold:
        return "avoid"
    return None


def source_url(scoring: str, teams: int) -> str:
    """The FFC page these numbers came from — not always half-PPR 12-team."""
    return f"https://fantasyfootballcalculator.com/adp/{scoring}/{teams}-team/all"


def apply(data: Dataset, payload: dict, reflag: bool = False) -> tuple[Dataset, list[str], list[str]]:
    """Return *data* with adp/adp_sd/bye refreshed, plus (changed, unmatched) notes.

    *reflag* is opt-in. The packaged board's flags compare market ADP against a
    projected finish, which is a stronger signal than the board's own rank;
    recomputing would quietly replace it. Turn it on for a board you maintain
    yourself, where rank *is* your opinion. `watch` is never overwritten, so an
    injury designation cannot be dropped by a price refresh.
    """
    by_name, by_team = index(payload)
    meta = payload.get("meta", {})
    players: list[Player] = []
    changed: list[str] = []
    unmatched: list[str] = []

    for p in data.players:
        row = None
        if p.pos == "DST":
            row = by_team.get(p.team)
        else:
            cands = by_name.get((normalise(p.name), p.pos), [])
            same_team = [c for c in cands if c.get("team") == p.team]
            if len(same_team) == 1:
                row = same_team[0]
            elif len(cands) == 1:
                row = cands[0]

        if row is None:
            unmatched.append(f"{p.rank:>3} {p.name} ({p.pos} {p.team})")
            players.append(p)
            continue

        adp = float(row["adp"])
        sd = float(row["stdev"]) if row.get("stdev") else None
        updated = replace(p, adp=adp, adp_sd=sd, bye=row.get("bye") or p.bye)
        if reflag and updated.flag != "watch":
            updated = replace(updated, flag=price_flag(updated))
        if p.adp != adp:
            changed.append(f"{p.name}: ADP {p.adp if p.adp is not None else '—'} -> {adp}")
        players.append(updated)

    # Link the sample we actually pulled: this used to point at half-PPR 12-team
    # no matter which format or league size produced the numbers.
    fmt = str(meta.get("type") or "half-ppr").lower().replace("_", "-").replace(" ", "-")
    provenance = {
        "source": "Fantasy Football Calculator",
        "format": f"{meta.get('type', '?')} · {meta.get('teams', '?')}-team · {meta.get('total_drafts', '?')} drafts",
        "window": f"{meta.get('start_date', '?')} to {meta.get('end_date', '?')}",
        "url": source_url(fmt, meta.get("teams") or 12),
    }
    return replace(data, players=players, adp=provenance), changed, unmatched
