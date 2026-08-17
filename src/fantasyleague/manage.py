"""In-season decisions: who to start, who to add, and whether a trade helps.

Every answer here is the same calculation from a different angle: take a roster,
find its best legal lineup under a valuation, and compare totals. A trade is
"good" when my best lineup scores more after it; a waiver add is worth making
when the free agent would start for me (or beats my worst body at his position);
a start/sit call is just the best lineup for one week.

The valuation is injected (`league.Valuation`) so the caller decides the horizon:
this week's projections for lineups, rest-of-season for trades and waivers,
plain season projections when offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from itertools import combinations

from .league import League, Spot, Team, Valuation, best_lineup, lineup_points

# Trading a kicker or a defense is noise; the finder never proposes one and the
# evaluator warns when a hand-typed proposal includes one.
TRADEABLE = ("QB", "RB", "WR", "TE")


# ---------------------------------------------------------------- lineups

@dataclass
class LineupCall:
    team: Team
    starters: dict[str, Spot]
    bench: list[Spot]
    total: float
    start: list[Spot] = field(default_factory=list)   # in the best lineup but benched today
    sit: list[Spot] = field(default_factory=list)     # starting today but not in the best lineup
    byes: list[Spot] = field(default_factory=list)
    unknown: list[Spot] = field(default_factory=list)


def start_sit(team: Team, val: Valuation, lineup: dict | None = None) -> LineupCall:
    """Best lineup for the valuation's horizon, and the moves that get there.

    The roster records which slot each player sits in today; comparing that with
    the optimal lineup turns the answer into "start X, sit Y" rather than a list.
    """
    starters, bench = best_lineup(team.roster, val, lineup)
    total = lineup_points(starters, val)
    optimal = {id(s) for s in starters.values()}
    starting_now = {id(s) for s in team.roster if s.slot not in ("BN", "IR")}
    start = [s for s in starters.values() if id(s) not in starting_now]
    sit = [s for s in team.roster if id(s) in starting_now and id(s) not in optimal]
    byes = [s for s in team.roster if val.on_bye(s)]
    unknown = [s for s in team.roster if s.slot != "IR" and not val.known(s)]
    return LineupCall(team, starters, bench, total, start, sit, byes, unknown)


# ---------------------------------------------------------------- trades

@dataclass
class TradeResult:
    me: Team
    them: Team
    give: list[Spot]
    get: list[Spot]
    me_before: float
    me_after: float
    them_before: float
    them_after: float
    notes: list[str] = field(default_factory=list)

    @property
    def me_delta(self) -> float:
        return self.me_after - self.me_before

    @property
    def them_delta(self) -> float:
        return self.them_after - self.them_before

    @property
    def score(self) -> float:
        """What to rank proposals by: my gain, plus half of theirs.

        A trade the other side also gains from is one they will actually accept;
        one that merely costs them nothing on paper is a favour they may not do.
        """
        return self.me_delta + 0.5 * self.them_delta

    @property
    def verdict(self) -> str:
        if self.me_delta >= 3 and self.them_delta > 0.5:
            return "win-win — propose it"
        if self.me_delta >= 3 and self.them_delta > -0.5:
            return "costs them nothing by projection — worth asking"
        if self.me_delta >= 3:
            return "good for you; they lose by projection, so expect a counter"
        if self.me_delta > -1:
            return "a wash for you"
        return "you get worse — decline"


def _roster_after(roster: list[Spot], out: list[Spot], incoming: list[Spot]) -> list[Spot]:
    gone = {id(s) for s in out}
    kept = [s for s in roster if id(s) not in gone]
    # Incoming players land on the bench; the lineup maths re-seats everyone anyway.
    return kept + [replace(s, slot="BN") for s in incoming]


def evaluate_trade(
    league: League, me: Team, them: Team, give: list[Spot], get: list[Spot], val: Valuation
) -> TradeResult:
    """How both best lineups change if *me* sends *give* to *them* for *get*."""
    if not give or not get:
        raise ValueError("a trade needs at least one player on each side")
    if len({s.key for s in give}) != len(give) or len({s.key for s in get}) != len(get):
        raise ValueError("the same player is listed twice")
    for s in give:
        if s not in me.roster:
            raise ValueError(f"{s.name} is not on {me.name}")
    for s in get:
        if s not in them.roster:
            raise ValueError(f"{s.name} is not on {them.name}")
    shape = league.lineup
    me_before = lineup_points(best_lineup(me.roster, val, shape)[0], val)
    them_before = lineup_points(best_lineup(them.roster, val, shape)[0], val)
    me_after = lineup_points(best_lineup(_roster_after(me.roster, give, get), val, shape)[0], val)
    them_after = lineup_points(best_lineup(_roster_after(them.roster, get, give), val, shape)[0], val)
    notes: list[str] = []
    if len(get) > len(give):
        notes.append(f"you receive {len(get) - len(give)} more than you send — you must drop {len(get) - len(give)} to fit")
    elif len(give) > len(get):
        notes.append(f"they receive {len(give) - len(get)} more than they send — they must drop {len(give) - len(get)} to fit")
    odd = [s.name for s in give + get if s.pos not in TRADEABLE]
    if odd:
        notes.append("kickers/defenses in a trade rarely change anything: " + ", ".join(odd))
    unknown = [s.name for s in give + get if not val.known(s)]
    if unknown:
        notes.append("no projection for: " + ", ".join(unknown) + " (counted as 0)")
    return TradeResult(me, them, list(give), list(get), me_before, me_after, them_before, them_after, notes)


def find_trades(
    league: League,
    me: Team,
    val: Valuation,
    partners: list[Team] | None = None,
    max_give: int = 2,
    max_get: int = 2,
    min_gain: float = 3.0,
    partner_floor: float = 0.0,
    deep: bool = False,
    limit: int = 10,
) -> list[TradeResult]:
    """Trades that raise my best lineup by at least *min_gain* without lowering the partner's below *partner_floor*.

    A win-win by projection is what a partner can actually be talked into: they
    start a better lineup too, usually because my surplus sits where their hole is.
    1-for-1, 2-for-1 and 1-for-2 are searched by default; *deep* adds 2-for-2.
    """
    shape = league.lineup
    my_pieces = [s for s in me.roster if s.pos in TRADEABLE and s.slot != "IR"]
    my_before = lineup_points(best_lineup(me.roster, val, shape)[0], val)
    results: list[TradeResult] = []
    for them in partners or [t for t in league.teams if t is not me]:
        their_pieces = [s for s in them.roster if s.pos in TRADEABLE and s.slot != "IR"]
        their_before = lineup_points(best_lineup(them.roster, val, shape)[0], val)
        for n_give in range(1, max_give + 1):
            for n_get in range(1, max_get + 1):
                if not deep and n_give + n_get > 3:
                    continue
                for give in combinations(my_pieces, n_give):
                    kept = [s for s in me.roster if s not in give]
                    for get in combinations(their_pieces, n_get):
                        me_after = lineup_points(
                            best_lineup(kept + [replace(s, slot="BN") for s in get], val, shape)[0], val
                        )
                        if me_after - my_before < min_gain:
                            continue
                        them_after = lineup_points(
                            best_lineup(_roster_after(them.roster, list(get), list(give)), val, shape)[0], val
                        )
                        if them_after - their_before < partner_floor:
                            continue
                        results.append(TradeResult(me, them, list(give), list(get), my_before, me_after,
                                                   their_before, them_after))
    # Most likely to be worth sending first; among equals, the smallest package.
    results.sort(key=lambda r: (-r.score, len(r.give) + len(r.get), -r.me_delta))
    return _dedupe(results, val, shape, per_partner=max(3, -(-limit // 3)))[:limit]


def _dedupe(results: list[TradeResult], val: Valuation, shape: dict, per_partner: int = 3) -> list[TradeResult]:
    """One proposal per (partner, received players who would actually start for me), a few per partner.

    The raw search returns every way of paying for the same target and every
    throw-in that could ride along; the reader wants each target once, in the
    smallest package that gets it, and a spread of partners rather than eleven
    variations on one. A throw-in who sits on my bench does not make a proposal new.
    """
    kept: list[TradeResult] = []
    seen: set[tuple[int, frozenset[str]]] = set()
    count: dict[int, int] = {}
    for r in results:
        after, _ = best_lineup(_roster_after(r.me.roster, r.give, r.get), val, shape)
        started = {s.key for s in after.values()}
        useful = frozenset(s.key for s in r.get if s.key in started)
        sig = (id(r.them), useful)
        if sig in seen or count.get(id(r.them), 0) >= per_partner:
            continue
        seen.add(sig)
        count[id(r.them)] = count.get(id(r.them), 0) + 1
        kept.append(r)
    return kept


# ---------------------------------------------------------------- waivers

@dataclass
class WaiverTarget:
    name: str
    pos: str
    team: str
    points: float
    gain: float          # how much my best lineup improves if I add him (0 = he'd sit)
    depth_gain: float    # his points minus my worst roster spot at his position
    drop: Spot | None    # who to cut to make room
    trending: int | None = None


def waiver_targets(
    league: League,
    me: Team,
    val: Valuation,
    pool: dict[str, dict],
    pos: str | None = None,
    limit: int = 15,
    trending: dict[str, int] | None = None,
) -> list[WaiverTarget]:
    """Free agents ranked by what they would do for *me*.

    *pool* is {normalised name: {name, pos, team, points}} for every projected
    player, so a breakout backup who was never on the draft board still shows up.
    Anyone on any roster in the league is excluded.
    """
    shape = league.lineup
    taken = league.rostered()
    # The pool carries its own numbers; make sure the valuation can price every candidate.
    val = val.extend({k: float(e.get("points") or 0.0) for k, e in pool.items()})
    starters, bench = best_lineup(me.roster, val, shape)
    before = lineup_points(starters, val)
    out: list[WaiverTarget] = []
    for key, e in pool.items():
        if key in taken or (pos and e["pos"] != pos) or e["pos"] not in ("QB", "RB", "WR", "TE", "K", "DST"):
            continue
        cand = Spot(name=e["name"], pos=e["pos"], team=e.get("team") or "", slot="BN")
        pts = val.points(cand)
        if pts <= 0:
            continue
        # Would he start? Add him without dropping anyone and re-run the lineup.
        with_him = lineup_points(best_lineup([*me.roster, cand], val, shape)[0], val)
        gain = with_him - before
        mine_at_pos = [val.points(s) for s in me.roster if s.pos == cand.pos and s.slot != "IR"]
        depth_gain = pts - (min(mine_at_pos) if mine_at_pos else 0.0)
        if gain <= 0 and depth_gain <= 0:
            continue
        # Drop: worst bench player at his position if that is where he'd sit; else the worst bench body overall.
        bench_sorted = sorted(bench, key=lambda s: val.points(s))
        same_pos = [s for s in bench_sorted if s.pos == cand.pos]
        drop = None
        if same_pos and gain <= 0:
            drop = same_pos[0]
        elif bench_sorted:
            drop = next((s for s in bench_sorted if s.pos not in ("K", "DST")), bench_sorted[0])
        out.append(WaiverTarget(e["name"], e["pos"], e.get("team") or "", pts, gain, depth_gain, drop,
                                (trending or {}).get(key)))
    out.sort(key=lambda t: (-t.gain, -t.depth_gain, -t.points))
    return out[:limit]


# ---------------------------------------------------------------- strength table

def strength_table(league: League, val: Valuation) -> list[tuple[Team, float, dict[str, Spot]]]:
    """Every team's best-lineup total, strongest first."""
    rows = []
    for t in league.teams:
        starters, _ = best_lineup(t.roster, val, league.lineup)
        rows.append((t, lineup_points(starters, val), starters))
    rows.sort(key=lambda r: -r[1])
    return rows
