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


def position_counts(data: Dataset, drafted: set[int] | None = None) -> dict[str, int]:
    """How many players remain at each position."""
    taken = drafted or set()
    counts: dict[str, int] = {}
    for p in data.players:
        if p.rank not in taken:
            counts[p.pos] = counts.get(p.pos, 0) + 1
    return counts
