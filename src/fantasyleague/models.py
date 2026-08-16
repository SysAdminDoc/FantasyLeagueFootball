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
    # Projected season points for the board's scoring format, and the auction
    # dollar value derived from them. Both None until `refresh` fills them in.
    projected: float | None = None
    value: int | None = None
    # Keeper/dynasty context: age in years and completed NFL seasons.
    age: int | None = None
    exp: int | None = None

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
        if self.projected is not None and self.projected < 0:
            raise ValueError(f"{self.name}: projected points cannot be negative")
        if self.value is not None and self.value < 1:
            raise ValueError(f"{self.name}: auction value must be at least $1")
        if self.age is not None and not 18 <= self.age <= 50:
            raise ValueError(f"{self.name}: age {self.age} is out of range")
        if self.exp is not None and self.exp < 0:
            raise ValueError(f"{self.name}: experience cannot be negative")

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


# Yahoo's default starting lineup (help.yahoo.com/kb/SLN22673).
DEFAULT_LINEUP = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1}
DEFAULT_ROSTER_SIZE = 15
LINEUP_SLOTS = ("QB", "RB", "WR", "TE", "FLEX", "K", "DST")

SCHEMA_VERSION = 1
"""Bump when a change to the dataset shape needs a migration in `Dataset.from_dict`."""


@dataclass
class Dataset:
    schema_version: int = field(default=SCHEMA_VERSION, kw_only=True)
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
    # Starting lineup this board is built and priced for. None means Yahoo's default.
    # A superflex board is not a re-skin: with two QB slots the replacement is QB24,
    # which is why the page, the roster card and value-over-replacement all have to
    # read the lineup rather than assume one.
    lineup: dict | None = None
    # One line on where the ranks and flags came from, shown in the page footer.
    provenance: str | None = None
    # Provenance of the players' adp fields: {source, format, window, url}. None if unset.
    adp: dict | None = None
    # Auction context {budget, roster_size, teams} when values were computed.
    auction: dict | None = None
    # Most-added players in the last day, from the last `refresh`. [{name, pos, team, count}]
    trending: list = field(default_factory=list)
    # ISO-8601 timestamp of the last injury/trending refresh, or None.
    refreshed: str | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> Dataset:
        version = raw.get("schema_version", 0)
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"dataset schema_version {version} is newer than this build understands "
                f"({SCHEMA_VERSION}) — upgrade fantasyleague"
            )
        # v0 (no key) is v1 without the stamp: every field it lacks already defaults.
        try:
            return cls(
                schema_version=SCHEMA_VERSION,
                season=raw["season"],
                scoring=raw["scoring"],
                format=raw["format"],
                updated=raw["updated"],
                tiers=[Tier(**t) for t in raw["tiers"]],
                players=[Player(**p) for p in raw["players"]],
                # Rails are optional: a hand-written board may carry none of them.
                plan=[PlanItem(**p) for p in raw.get("plan") or []],
                do_not_draft=[Entry(**e) for e in raw.get("do_not_draft") or []],
                injuries=[Injury(**i) for i in raw.get("injuries") or []],
                sleepers=[Entry(**e) for e in raw.get("sleepers") or []],
                sources=[Source(**s) for s in raw.get("sources") or []],
                lineup=raw.get("lineup"),
                provenance=raw.get("provenance"),
                adp=raw.get("adp"),
                auction=raw.get("auction"),
                trending=raw.get("trending") or [],
                refreshed=raw.get("refreshed"),
            )
        except KeyError as exc:
            raise ValueError(
                f"dataset is missing the required field {exc.args[0]!r} — "
                "run `fantasyleague validate` for the full list"
            ) from exc
        except TypeError as exc:
            # An unknown or mistyped key: a raw TypeError traceback is no use to
            # someone hand-editing a board.
            raise ValueError(
                f"dataset does not match the expected shape ({exc}) — "
                "run `fantasyleague validate` for the full list"
            ) from exc
