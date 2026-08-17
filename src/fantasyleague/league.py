"""In-season league state: every team's roster, and the lineup maths on top of it.

The draft board answers "who should I take"; once the season starts the questions
change to "who do I start", "who do I add", "is this trade good for me". All three
need the same two things — everyone's roster, and a number per player — so both
live here. The number is supplied by a `Valuation`, which the callers build from
the board's season projections, from Sleeper's weekly projections, or from a
rest-of-season sum of them; the lineup maths never cares which.

`league.json` is deliberately plain: team names, and for each player a name,
position, NFL team and the slot he currently sits in. Anything a fantasy site
exports, or a person types in, can produce it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .board import join_key
from .models import DEFAULT_LINEUP, POSITIONS, Dataset

SLOTS = ("QB", "RB", "WR", "TE", "FLEX", "K", "DST", "BN", "IR")
FLEX_POSITIONS = ("RB", "WR", "TE")
# What a fantasy site calls the flex slot; all mean "RB/WR/TE".
FLEX_ALIASES = {"W/R/T", "W/R", "WR/RB/TE", "RB/WR/TE", "FLEX", "W/T"}
DST_ALIASES = {"DEF", "D/ST", "DST", "D"}
LEAGUE_SCHEMA_VERSION = 1


def slot_name(raw: str) -> str:
    """Normalise a site's slot label to one of SLOTS."""
    s = raw.strip().upper()
    if s in FLEX_ALIASES:
        return "FLEX"
    if s in DST_ALIASES:
        return "DST"
    if s.startswith("IR"):
        return "IR"
    if s in SLOTS:
        return s
    raise ValueError(f"unknown roster slot {raw!r}")


@dataclass(frozen=True)
class Spot:
    """One player on one roster."""

    name: str
    pos: str
    team: str = ""
    slot: str = "BN"
    ids: dict = field(default_factory=dict)
    # The site's injury designation when the roster was read ("Questionable", "Out"...).
    status: str | None = None

    def __post_init__(self) -> None:
        if self.pos not in POSITIONS:
            raise ValueError(f"{self.name}: unknown position {self.pos!r}")
        if self.slot not in SLOTS:
            raise ValueError(f"{self.name}: unknown slot {self.slot!r}")

    @property
    def key(self) -> str:
        return join_key(self.name)

    def to_dict(self) -> dict:
        d = {"name": self.name, "pos": self.pos, "team": self.team, "slot": self.slot}
        if self.ids:
            d["ids"] = dict(self.ids)
        if self.status:
            d["status"] = self.status
        return d


@dataclass
class Team:
    name: str
    roster: list[Spot] = field(default_factory=list)
    manager: str | None = None
    waiver: int | None = None
    # The site's own id for this team (Yahoo team number, Sleeper roster id).
    site_id: str | None = None

    def find(self, token: str) -> Spot:
        """One roster spot from (part of) a name; ambiguous or missing raises."""
        q = join_key(token)
        exact = [s for s in self.roster if s.key == q]
        if len(exact) == 1:
            return exact[0]
        hits = [s for s in self.roster if q in s.key]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise ValueError(f"{token!r} is not on {self.name}")
        raise ValueError(f"{token!r} is ambiguous on {self.name}: {', '.join(s.name for s in hits)}")

    def to_dict(self) -> dict:
        d: dict = {"name": self.name, "roster": [s.to_dict() for s in self.roster]}
        if self.manager:
            d["manager"] = self.manager
        if self.waiver is not None:
            d["waiver"] = self.waiver
        if self.site_id:
            d["site_id"] = self.site_id
        return d


@dataclass
class League:
    name: str
    season: int
    teams: list[Team] = field(default_factory=list)
    me: str | None = None
    lineup: dict = field(default_factory=lambda: dict(DEFAULT_LINEUP))
    scoring: str = "half_ppr"
    # Where the rosters came from and when: {"site", "league_id", "fetched"}.
    source: dict | None = None
    schema_version: int = LEAGUE_SCHEMA_VERSION

    # ---- lookup -----------------------------------------------------------

    def team(self, token: str | None = None) -> Team:
        """A team by (part of) its name; None means my team."""
        if token is None:
            if not self.me:
                raise ValueError("no team is marked as yours — set \"me\" in the league file or pass --team")
            token = self.me
        q = token.casefold()
        exact = [t for t in self.teams if t.name.casefold() == q]
        if len(exact) == 1:
            return exact[0]
        hits = [t for t in self.teams if q in t.name.casefold()]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise ValueError(f"no team matches {token!r}; teams: {', '.join(t.name for t in self.teams)}")
        raise ValueError(f"{token!r} is ambiguous: {', '.join(t.name for t in hits)}")

    def rostered(self) -> dict[str, str]:
        """{normalised player name: team name} across the league."""
        out: dict[str, str] = {}
        for t in self.teams:
            for s in t.roster:
                out[s.key] = t.name
        return out

    # ---- serialisation ----------------------------------------------------

    def to_dict(self) -> dict:
        d: dict = {
            "schema_version": self.schema_version,
            "name": self.name,
            "season": self.season,
            "scoring": self.scoring,
            "lineup": dict(self.lineup),
            "me": self.me,
            "teams": [t.to_dict() for t in self.teams],
        }
        if self.source:
            d["source"] = dict(self.source)
        return d

    @classmethod
    def from_dict(cls, raw: dict) -> League:
        version = raw.get("schema_version", 1)
        if version > LEAGUE_SCHEMA_VERSION:
            raise ValueError(f"league schema_version {version} is newer than this build understands")
        try:
            teams = []
            for t in raw["teams"]:
                roster = [
                    Spot(
                        name=s["name"], pos=s["pos"], team=s.get("team") or "",
                        slot=slot_name(s.get("slot") or "BN"), ids=dict(s.get("ids") or {}),
                        status=s.get("status"),
                    )
                    for s in t.get("roster") or []
                ]
                teams.append(Team(name=t["name"], roster=roster, manager=t.get("manager"),
                                  waiver=t.get("waiver"), site_id=t.get("site_id")))
            return cls(
                name=raw["name"], season=int(raw["season"]), teams=teams, me=raw.get("me"),
                lineup=dict(raw.get("lineup") or DEFAULT_LINEUP), scoring=raw.get("scoring") or "half_ppr",
                source=raw.get("source"), schema_version=LEAGUE_SCHEMA_VERSION,
            )
        except KeyError as exc:
            raise ValueError(f"league file is missing the required field {exc.args[0]!r}") from exc
        except TypeError as exc:
            raise ValueError(f"league file does not match the expected shape ({exc})") from exc


def load(path: str | Path) -> League:
    p = Path(path)
    if not p.exists():
        raise ValueError(f"no league file at {p} — build one with `fantasyleague league import`")
    with p.open(encoding="utf-8-sig") as fh:
        raw = json.load(fh)
    league = League.from_dict(raw)
    validate(league)
    return league


def save(league: League, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(league.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p


def validate(league: League) -> None:
    names = [t.name for t in league.teams]
    if len({n.casefold() for n in names}) != len(names):
        raise ValueError("league has two teams with the same name")
    if league.me and not any(t.name.casefold() == league.me.casefold() for t in league.teams):
        raise ValueError(f"\"me\" is {league.me!r} but no team has that name")
    seen: dict[str, str] = {}
    for t in league.teams:
        for s in t.roster:
            if s.key in seen and seen[s.key] != t.name:
                raise ValueError(f"{s.name} is on two rosters: {seen[s.key]} and {t.name}")
            seen[s.key] = t.name


# ---------------------------------------------------------------- valuation

class Valuation:
    """Points per player for whatever horizon the caller chose.

    `points(spot)` is the number the lineup maths sorts by. `bye_weeks` lets a
    weekly valuation zero out a player who does not play that week; the season
    and rest-of-season horizons leave it empty.
    """

    def __init__(self, points: dict[str, float], label: str = "season", bye_weeks: dict[str, int] | None = None,
                 week: int | None = None):
        self._points = points
        self.label = label
        self.week = week
        self._byes = bye_weeks or {}

    @classmethod
    def from_board(cls, data: Dataset, label: str = "season") -> Valuation:
        pts = {join_key(p.name): float(p.projected) for p in data.players if p.projected is not None}
        byes = {join_key(p.name): p.bye for p in data.players if p.bye}
        return cls(pts, label=label, bye_weeks=byes)

    def points(self, spot: Spot) -> float:
        v = self._points.get(spot.key)
        if v is None:
            return 0.0
        if self.week is not None and self._byes.get(spot.key) == self.week:
            return 0.0
        return v

    def known(self, spot: Spot) -> bool:
        return spot.key in self._points

    def on_bye(self, spot: Spot) -> bool:
        return self.week is not None and self._byes.get(spot.key) == self.week

    def with_week(self, week: int, points: dict[str, float] | None = None, label: str | None = None) -> Valuation:
        """The same valuation pinned to one week (byes apply), optionally with weekly numbers."""
        return Valuation(points if points is not None else self._points, label=label or f"week {week}",
                         bye_weeks=self._byes, week=week)

    def extend(self, points: dict[str, float]) -> Valuation:
        """A copy that also knows *points* (existing entries win) — for valuing free agents."""
        merged = dict(points)
        merged.update(self._points)
        return Valuation(merged, label=self.label, bye_weeks=self._byes, week=self.week)


# ---------------------------------------------------------------- lineups

def best_lineup(
    roster: list[Spot], val: Valuation, lineup: dict | None = None
) -> tuple[dict[str, Spot], list[Spot]]:
    """The highest-scoring legal lineup from *roster*: ({slot label: spot}, bench).

    Fixed slots are filled first with the best at each position; FLEX then takes
    the best leftover RB/WR/TE. Because FLEX accepts a superset of what the fixed
    slots accept, that greedy order is optimal. Players on IR are never started.
    """
    shape = lineup or DEFAULT_LINEUP
    pool = sorted((s for s in roster if s.slot != "IR"), key=lambda s: -val.points(s))
    used: set[int] = set()
    starters: dict[str, Spot] = {}

    def take(pos: str, count: int) -> None:
        got = [s for s in pool if s.pos == pos and id(s) not in used][:count]
        for i, s in enumerate(got, 1):
            used.add(id(s))
            starters[pos if count == 1 else f"{pos}{i}"] = s

    for pos in ("QB", "RB", "WR", "TE"):
        take(pos, int(shape.get(pos, 0)))
    for i in range(1, int(shape.get("FLEX", 0)) + 1):
        left = [s for s in pool if s.pos in FLEX_POSITIONS and id(s) not in used]
        if left:
            used.add(id(left[0]))
            starters["FLEX" if shape.get("FLEX", 0) == 1 else f"FLEX{i}"] = left[0]
    for pos in ("K", "DST"):
        take(pos, int(shape.get(pos, 0)))
    bench = [s for s in pool if id(s) not in used]
    return starters, bench


def lineup_points(starters: dict[str, Spot], val: Valuation, skill_only: bool = False) -> float:
    return sum(val.points(s) for k, s in starters.items() if not (skill_only and k in ("K", "DST")))


def team_strength(team: Team, val: Valuation, lineup: dict | None = None) -> float:
    """Projected points of the team's best lineup — the one number to compare teams by."""
    starters, _ = best_lineup(team.roster, val, lineup)
    return lineup_points(starters, val)


def replacement_by_position(league: League, val: Valuation, lineup: dict | None = None) -> dict[str, float]:
    """The worst *starter* at each position across the league, under *val*.

    Value over this baseline is what a bench player or free agent is worth: a
    third RB who would start for nobody is worth nothing to a trade partner.
    """
    shape = lineup or league.lineup
    worst: dict[str, float] = {}
    for t in league.teams:
        starters, _ = best_lineup(t.roster, val, shape)
        for s in starters.values():
            worst[s.pos] = min(worst.get(s.pos, float("inf")), val.points(s))
    return {k: (0.0 if v == float("inf") else v) for k, v in worst.items()}
