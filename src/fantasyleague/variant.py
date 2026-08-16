"""Build a board for a scoring format other than the packaged half-PPR one.

Superflex and full-PPR are not re-skins: in superflex a quarterback is a first-round
pick rather than a round-5 afterthought, and in full PPR a receiving back climbs past
a between-the-tackles one. The ordering has to come from that format's own market,
so a variant re-ranks by the target format's ADP and re-derives everything downstream
(projections, auction values, tier boundaries) from there.
"""

from __future__ import annotations

from dataclasses import replace

from .models import DEFAULT_LINEUP, DEFAULT_ROSTER_SIZE, Dataset, Tier
from .sync import adp as adp_mod
from .sync import projections as proj_mod

# FFC ADP format -> (label, projections scoring key, starting lineup).
# The lineup drives pricing, so superflex must say so: with two quarterback slots
# the replacement QB is QB24 rather than QB12, which is the entire reason a QB is
# worth a first-round price in that format.
SUPERFLEX_LINEUP = {"QB": 2, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1}

VARIANTS = {
    "half-ppr": ("Half-PPR", "half_ppr", DEFAULT_LINEUP),
    "ppr": ("Full PPR", "ppr", DEFAULT_LINEUP),
    "standard": ("Standard", "standard", DEFAULT_LINEUP),
    "2qb": ("Superflex / 2QB", "half_ppr", SUPERFLEX_LINEUP),
    "dynasty": ("Dynasty", "half_ppr", DEFAULT_LINEUP),
}

# Tier boundaries as fractions of the ranked board, so a variant of any depth
# gets sensible cut points rather than the packaged board's hardcoded ones.
TIER_SHAPE = [
    (0.025, "The anchors", "No wrong answer. Take the board."),
    (0.065, "Round 1 turn", "Where the first real preference shows up."),
    (0.10, "Early second", "Last of the every-week workhorses."),
    (0.15, "The crossroads", "Your roster shape gets decided here."),
    (0.21, "Rounds 3-4", "Starters with a question attached."),
    (0.28, "Rounds 4-5", "Third-back deadline. Don't drift past it."),
    (0.38, "Rounds 5-7", "The value pocket. Most of your edge is here."),
    (0.60, "Rounds 7-9", "Bench value with a path to targets."),
    (0.83, "Rounds 10-12", "Upside swings."),
    (1.00, "Last rounds", "Handcuffs, camp risers, Week 1 fill-ins."),
]


def build(
    data: Dataset,
    scoring: str,
    teams: int = 12,
    budget: int = 200,
    roster_size: int = DEFAULT_ROSTER_SIZE,
) -> tuple[Dataset, list[str]]:
    """Re-rank *data* for *scoring* and re-derive its numbers. Returns (board, notes)."""
    if scoring not in VARIANTS:
        raise ValueError(f"unknown variant {scoring!r}; expected one of {', '.join(VARIANTS)}")
    label, points_key, lineup = VARIANTS[scoring]
    notes: list[str] = []

    payload = adp_mod.fetch(scoring=scoring, teams=teams, year=data.season)
    data, _, unmatched = adp_mod.apply(data, payload, reflag=False)
    listed = len(data.players) - len(unmatched)
    notes.append(
        f"{listed} of {len(data.players)} players have {label} ADP; "
        f"the other {len(unmatched)} keep their existing ADP position"
    )

    # Rank by that market. A player the target format does not list keeps the ADP
    # already on the board (half-PPR, normally) and is placed by it — a stale-format
    # estimate puts a deep player far closer to right than dumping him at the bottom
    # would. Formats are mixed only for the tail; everything that matters is listed.
    ranked = sorted(
        data.players,
        key=lambda p: (p.adp is None, p.adp if p.adp is not None else 0, p.rank),
    )
    players = [replace(p, rank=i + 1) for i, p in enumerate(ranked)]

    try:
        rows = proj_mod.fetch(season=data.season)
        points = proj_mod.points_by_id(rows, scoring=points_key)
        staged = replace(data, players=players)
        staged, hits = proj_mod.apply(staged, points)
        players = staged.players
        notes.append(f"projections attached to {hits} players ({points_key})")
    except (OSError, ValueError) as exc:
        notes.append(f"projections unavailable ({exc})")

    players, tiers = _retier(players)
    out = replace(
        data,
        players=players,
        tiers=tiers,
        scoring=scoring.replace("-", "_"),
        format=f"{label} · {teams}-team",
    )

    values = proj_mod.auction_values(out, lineup, teams, budget=budget, roster_size=roster_size)
    out = replace(
        out,
        players=[replace(p, value=values.get(p.rank)) for p in out.players],
        auction={"budget": budget, "roster_size": roster_size, "teams": teams},
    )
    starters = ", ".join(f"{n}{pos}" for pos, n in lineup.items())
    notes.append(f"{len([p for p in out.players if p.value])} players priced on ${budget} ({starters})")
    return out, notes


def _retier(players: list) -> tuple[list, list[Tier]]:
    """Cut the ranked list into tiers at the shape's fractional boundaries."""
    total = len(players)
    out, tiers, start = [], [], 0
    for n, (frac, name, note) in enumerate(TIER_SHAPE, start=1):
        end = round(frac * total)
        if end <= start:
            continue
        block = players[start:end]
        out.extend(replace(p, tier=n) for p in block)
        tiers.append(
            Tier(n=n, name=name, range=f"Picks {block[0].rank}-{block[-1].rank}", note=note)
        )
        start = end
    if start < total:  # rounding leftovers join the last tier
        block = players[start:]
        out.extend(replace(p, tier=tiers[-1].n) for p in block)
        tiers[-1] = replace(tiers[-1], range=f"Picks {out[start].rank}-{block[-1].rank}")
    return out, tiers
