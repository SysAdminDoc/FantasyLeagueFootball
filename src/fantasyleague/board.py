"""Loading, validation, and draft-time queries over the board dataset."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from .models import Dataset, Player

TIER_BREAK_THRESHOLD = 2
"""A tier with this many or fewer players left is about to break — reach now."""


def load(path: str | Path | None = None) -> Dataset:
    """Load the dataset from *path*, or the packaged 2026 board by default."""
    if path is None:
        text = resources.files(__package__).joinpath("data/players_2026.json").read_text("utf-8")
    else:
        text = Path(path).read_text("utf-8")
    data = Dataset.from_dict(json.loads(text))
    validate(data)
    return data


def validate(data: Dataset) -> None:
    """Raise if the board has structural problems that would corrupt a draft."""
    ranks = [p.rank for p in data.players]
    if ranks != sorted(ranks):
        raise ValueError("players are not in rank order")
    if len(set(ranks)) != len(ranks):
        dupes = sorted({r for r in ranks if ranks.count(r) > 1})
        raise ValueError(f"duplicate ranks: {dupes}")
    if ranks and ranks != list(range(1, len(ranks) + 1)):
        raise ValueError("ranks must run 1..N with no gaps")

    known_tiers = {t.n for t in data.tiers}
    orphans = {p.tier for p in data.players} - known_tiers
    if orphans:
        raise ValueError(f"players reference undefined tiers: {sorted(orphans)}")

    names = [p.name for p in data.players]
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise ValueError(f"duplicate players: {dupes}")


def by_tier(data: Dataset) -> list[tuple[object, list[Player]]]:
    """Tiers paired with their players, in board order."""
    return [(t, [p for p in data.players if p.tier == t.n]) for t in data.tiers]


def filter_players(
    data: Dataset,
    pos: str | None = None,
    flag: str | None = None,
    drafted: set[int] | None = None,
) -> list[Player]:
    """Board slice by position and/or flag, minus anyone already drafted."""
    taken = drafted or set()
    out = [p for p in data.players if p.rank not in taken]
    if pos and pos.upper() != "ALL":
        out = [p for p in out if p.pos == pos.upper()]
    if flag:
        out = [p for p in out if p.flag == flag]
    return out


def best_available(
    data: Dataset,
    pos: str | None = None,
    drafted: set[int] | None = None,
    limit: int = 8,
) -> list[Player]:
    """Highest-ranked undrafted players, optionally at one position."""
    return filter_players(data, pos=pos, drafted=drafted)[:limit]


def tier_breaks(data: Dataset, drafted: set[int] | None = None) -> list[tuple[object, int]]:
    """Tiers down to their last few players — the ones worth reaching into."""
    taken = drafted or set()
    out = []
    for tier, players in by_tier(data):
        left = len([p for p in players if p.rank not in taken])
        if 0 < left <= TIER_BREAK_THRESHOLD:
            out.append((tier, left))
    return out


def resolve(data: Dataset, token: str | int) -> Player:
    """Turn a rank or (part of) a name into exactly one player.

    Digits mean rank. Text is matched case-insensitively: an exact name wins,
    then a unique prefix, then a unique substring. Anything ambiguous raises
    with the candidates listed so the caller can be more specific.
    """
    if isinstance(token, int) or str(token).strip().isdigit():
        rank = int(token)
        for p in data.players:
            if p.rank == rank:
                return p
        raise ValueError(f"no player has rank {rank}")

    q = str(token).strip().lower()
    if not q:
        raise ValueError("empty player name")
    exact = [p for p in data.players if p.name.lower() == q]
    if len(exact) == 1:
        return exact[0]
    prefix = [p for p in data.players if p.name.lower().startswith(q)]
    if len(prefix) == 1:
        return prefix[0]
    within = [p for p in data.players if q in p.name.lower()]
    if len(within) == 1:
        return within[0]
    candidates = prefix or within
    if not candidates:
        raise ValueError(f"no player matches {token!r}")
    names = ", ".join(p.name for p in candidates[:8])
    raise ValueError(f"{token!r} is ambiguous: {names}")


def resolve_many(data: Dataset, tokens: list[str | int]) -> set[int]:
    """Ranks for a mixed list of ranks and names; raises on the first bad token."""
    return {resolve(data, t).rank for t in tokens}


def position_counts(data: Dataset, drafted: set[int] | None = None) -> dict[str, int]:
    """How many players remain at each position."""
    taken = drafted or set()
    counts: dict[str, int] = {}
    for p in data.players:
        if p.rank not in taken:
            counts[p.pos] = counts.get(p.pos, 0) + 1
    return counts
