"""Import tiers from Boris Chen's published per-position files.

He clusters FantasyPros expert-consensus ranks with a Gaussian mixture model and
publishes the result as plain text on S3:

    Tier 1: Christian McCaffrey, Jahmyr Gibbs, Bijan Robinson
    Tier 2: Saquon Barkley, De'Von Achane, ...

The files are not always current — as of 2026-08-16 every one still carried a
`Last-Modified` of 2025-12-26, i.e. the end of the previous season. Importing
those silently would re-tier a 2026 board with last year's opinions, so the age
check is the point of this module, not an afterthought: `fetch` refuses anything
older than *max_age_days* unless the caller explicitly allows it.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from ..models import Dataset
from .adp import normalise
from .sleeper import USER_AGENT

BASE = "https://s3-us-west-1.amazonaws.com/fftiers/out"
MAX_AGE_DAYS = 14

# text_ALL-HALF.txt is 403; the per-position files are the usable ones.
FILES = {
    "half": {"QB": "text_QB.txt", "RB": "text_RB-HALF.txt", "WR": "text_WR-HALF.txt",
             "TE": "text_TE-HALF.txt", "K": "text_K.txt", "DST": "text_DST.txt"},
    "ppr": {"QB": "text_QB.txt", "RB": "text_RB-PPR.txt", "WR": "text_WR-PPR.txt",
            "TE": "text_TE-PPR.txt", "K": "text_K.txt", "DST": "text_DST.txt"},
    "standard": {"QB": "text_QB.txt", "RB": "text_RB.txt", "WR": "text_WR.txt",
                 "TE": "text_TE.txt", "K": "text_K.txt", "DST": "text_DST.txt"},
}
LINE = re.compile(r"^\s*Tier\s+(\d+)\s*:\s*(.+?)\s*$", re.MULTILINE)


class StaleTiers(RuntimeError):
    """The published file is too old to be trusted for this season."""


def parse(text: str) -> dict[str, int]:
    """{normalised player name: tier number} from one published file."""
    out: dict[str, int] = {}
    for tier, names in LINE.findall(text):
        for name in names.split(","):
            name = name.strip()
            if name:
                out.setdefault(normalise(name), int(tier))
    return out


def _age_days(last_modified: str | None, now: datetime | None = None) -> float | None:
    if not last_modified:
        return None
    try:
        stamp = parsedate_to_datetime(last_modified)
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return ((now or datetime.now(UTC)) - stamp).total_seconds() / 86400


def fetch(
    pos: str,
    scoring: str = "half",
    max_age_days: float = MAX_AGE_DAYS,
    allow_stale: bool = False,
    timeout: float = 20.0,
) -> tuple[dict[str, int], float | None]:
    """({name: tier}, age in days). Raises StaleTiers when the file is too old."""
    files = FILES.get(scoring)
    if files is None:
        raise ValueError(f"unknown scoring {scoring!r}; expected one of {', '.join(FILES)}")
    name = files.get(pos)
    if name is None:
        raise ValueError(f"no published tiers for position {pos!r}")

    req = urllib.request.Request(f"{BASE}/{name}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode("utf-8", "replace")
        age = _age_days(r.headers.get("Last-Modified"))

    if age is not None and age > max_age_days and not allow_stale:
        raise StaleTiers(
            f"{name} was last published {age:.0f} days ago "
            f"(limit {max_age_days:g}); re-tiering a current board with it would be wrong. "
            "Pass allow_stale to override."
        )
    return parse(text), age


def apply(data: Dataset, tiers_by_pos: dict[str, dict[str, int]]) -> tuple[Dataset, list[str]]:
    """Re-tier players from published per-position tiers.

    Positional tiers are mapped onto the board's tier numbers by offsetting each
    position into its own block, so a "Tier 1 RB" and a "Tier 1 WR" stay distinct.
    Returns the new dataset and the names that had no published tier.
    """
    offsets: dict[str, int] = {}
    base = 0
    for pos in ("QB", "RB", "WR", "TE", "K", "DST"):
        mapping = tiers_by_pos.get(pos)
        if not mapping:
            continue
        offsets[pos] = base
        base += max(mapping.values())

    players = []
    unmatched: list[str] = []
    for p in data.players:
        mapping = tiers_by_pos.get(p.pos)
        tier = mapping.get(normalise(p.name)) if mapping else None
        if tier is None:
            unmatched.append(f"{p.name} ({p.pos})")
            players.append(p)
        else:
            players.append(replace(p, tier=offsets[p.pos] + tier))

    used = sorted({p.tier for p in players})
    names = {t.n: t for t in data.tiers}
    rebuilt = []
    for n in used:
        members = [p for p in players if p.tier == n]
        old = names.get(n)
        rebuilt.append(
            replace(old, range=f"Picks {members[0].rank}-{members[-1].rank}")
            if old
            else type(data.tiers[0])(
                n=n,
                name=f"{members[0].pos} tier {n - offsets.get(members[0].pos, 0)}",
                range=f"Picks {members[0].rank}-{members[-1].rank}",
                note="Boris Chen consensus tiers",
            )
        )
    return replace(data, players=players, tiers=rebuilt), unmatched
