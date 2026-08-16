"""Snake-draft arithmetic and projected availability.

Availability follows the method DraftKick published: treat a player's draft
position as normal with mean = ADP and σ = the market spread (or ADP/4 when
none is known), then P(available at pick X) = 1 − Φ((X − ADP) / σ).
It is a rough model — the spread widens as drafts go on — but it is honest,
explainable, and matches what every commercial pick predictor shows.
"""

from __future__ import annotations

import math

from .models import Player

DEFAULT_TEAMS = 12
MIN_SD = 0.5


def snake_picks(teams: int, slot: int, rounds: int = 16) -> list[int]:
    """Overall pick numbers for *slot* (1-based) in a snake draft of *teams*."""
    if teams < 2:
        raise ValueError("teams must be at least 2")
    if not 1 <= slot <= teams:
        raise ValueError(f"slot must be between 1 and {teams}")
    picks = []
    for rnd in range(rounds):
        offset = slot if rnd % 2 == 0 else teams - slot + 1
        picks.append(rnd * teams + offset)
    return picks


def next_picks(teams: int, slot: int, current_pick: int, count: int = 2, rounds: int = 16) -> list[int]:
    """The next *count* of your picks at or after *current_pick*."""
    return [p for p in snake_picks(teams, slot, rounds) if p >= current_pick][:count]


def phi(z: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def sigma_for(adp: float, adp_sd: float | None) -> float:
    """Spread to use: the market's own if known, else the ADP/4 heuristic."""
    sd = adp_sd if adp_sd is not None else adp / 4.0
    return max(sd, MIN_SD)


def availability(adp: float, adp_sd: float | None, pick: int) -> float:
    """Probability a player with this ADP is still on the board at *pick*."""
    return 1.0 - phi((pick - adp) / sigma_for(adp, adp_sd))


def player_availability(p: Player, pick: int) -> float | None:
    """Same, straight from a Player; None when we have no ADP for them."""
    if p.adp is None:
        return None
    return availability(p.adp, p.adp_sd, pick)


def band(prob: float) -> str:
    """DraftKick's guidance: wait at ≥ .6, draft at ≤ .3, undecided between."""
    if prob >= 0.6:
        return "wait"
    if prob <= 0.3:
        return "now"
    return "toss-up"
