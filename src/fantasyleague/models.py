"""Typed records for the draft board dataset."""

from __future__ import annotations

from dataclasses import dataclass, field

POSITIONS = ("QB", "RB", "WR", "TE")
FLAGS = ("value", "avoid", "watch")
SEVERITIES = ("out", "risk", "ok")


@dataclass(frozen=True)
class Player:
    rank: int
    name: str
    pos: str
    team: str
    tier: int
    flag: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.pos not in POSITIONS:
            raise ValueError(f"{self.name}: unknown position {self.pos!r}")
        if self.flag is not None and self.flag not in FLAGS:
            raise ValueError(f"{self.name}: unknown flag {self.flag!r}")
        if self.rank < 1:
            raise ValueError(f"{self.name}: rank must be positive")


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
        )
