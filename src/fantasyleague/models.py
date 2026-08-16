"""Typed records for the draft board dataset."""

from __future__ import annotations

from dataclasses import dataclass, field

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")
FLAGS = ("value", "avoid", "watch")
SEVERITIES = ("out", "risk", "ok")
ID_SOURCES = ("sleeper", "yahoo", "espn")


@dataclass(frozen=True)
class Player:
    rank: int
    name: str
    pos: str
    team: str
    tier: int
    flag: str | None = None
    note: str = ""
    # External identities keyed by source (sleeper/yahoo/espn). Sync and refresh
    # join on these; the display name is never used as a key.
    ids: dict = field(default_factory=dict)
    # Market data. adp is the average overall pick, adp_sd its spread; both feed
    # the "will he last to my pick" odds. None when the source has no record.
    adp: float | None = None
    adp_sd: float | None = None
    bye: int | None = None

    def __post_init__(self) -> None:
        if self.pos not in POSITIONS:
            raise ValueError(f"{self.name}: unknown position {self.pos!r}")
        if self.flag is not None and self.flag not in FLAGS:
            raise ValueError(f"{self.name}: unknown flag {self.flag!r}")
        if self.rank < 1:
            raise ValueError(f"{self.name}: rank must be positive")
        unknown = set(self.ids) - set(ID_SOURCES)
        if unknown:
            raise ValueError(f"{self.name}: unknown id source(s) {sorted(unknown)}")
        if self.adp is not None and self.adp <= 0:
            raise ValueError(f"{self.name}: adp must be positive")
        if self.adp_sd is not None and self.adp_sd < 0:
            raise ValueError(f"{self.name}: adp_sd must be non-negative")
        if self.bye is not None and not 1 <= self.bye <= 18:
            raise ValueError(f"{self.name}: bye week {self.bye} out of range")

    def id_for(self, source: str) -> str | None:
        """External id as a string, or None when the source has none for this player."""
        v = self.ids.get(source)
        return None if v is None else str(v)


@dataclass(frozen=True)
class Tier:
    n: int
    name: str
    range: str
    note: str


@dataclass(frozen=True)
class Entry:
    """A rail item — do-not-draft or late-round target."""

    name: str
    pos: str
    team: str
    why: str


@dataclass(frozen=True)
class Injury:
    name: str
    team: str
    severity: str
    status: str

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"{self.name}: unknown severity {self.severity!r}")

    @property
    def lines(self) -> list[str]:
        """Status split on the pipe used to force a line break in the render."""
        return self.status.split("|")


@dataclass(frozen=True)
class PlanItem:
    position: str
    guidance: str


@dataclass(frozen=True)
class Source:
    label: str
    url: str


@dataclass
class Dataset:
    season: int
    scoring: str
    format: str
    updated: str
    tiers: list[Tier] = field(default_factory=list)
    players: list[Player] = field(default_factory=list)
    plan: list[PlanItem] = field(default_factory=list)
    do_not_draft: list[Entry] = field(default_factory=list)
    injuries: list[Injury] = field(default_factory=list)
    sleepers: list[Entry] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    # Provenance of the players' adp fields: {source, format, window, url}. None if unset.
    adp: dict | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> Dataset:
        return cls(
            season=raw["season"],
            scoring=raw["scoring"],
            format=raw["format"],
            updated=raw["updated"],
            tiers=[Tier(**t) for t in raw["tiers"]],
            players=[Player(**p) for p in raw["players"]],
            plan=[PlanItem(**p) for p in raw["plan"]],
            do_not_draft=[Entry(**e) for e in raw["do_not_draft"]],
            injuries=[Injury(**i) for i in raw["injuries"]],
            sleepers=[Entry(**e) for e in raw["sleepers"]],
            sources=[Source(**s) for s in raw["sources"]],
            adp=raw.get("adp"),
        )
