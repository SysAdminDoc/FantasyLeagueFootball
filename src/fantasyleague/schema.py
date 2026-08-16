"""Structural checking for custom datasets.

`board.load()` raises on the first problem, which is right for a draft — a bad
board should not render. This module instead collects *every* problem with a
JSON pointer to it, so someone editing a dataset by hand can fix them in one
pass rather than one error per run.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import fields
from pathlib import Path

from .models import (
    FLAGS,
    ID_SOURCES,
    POSITIONS,
    SCHEMA_VERSION,
    SEVERITIES,
    Dataset,
    Player,
    Tier,
)

REQUIRED_TOP = ("season", "scoring", "format", "updated", "tiers", "players")
LIST_FIELDS = {
    "plan": ("position", "guidance"),
    "do_not_draft": ("name", "pos", "team", "why"),
    "injuries": ("name", "team", "severity", "status"),
    "sleepers": ("name", "pos", "team", "why"),
    "sources": ("label", "url"),
}

# Taken from the dataclasses so the checker cannot drift from what actually loads:
# a key the model doesn't accept is a TypeError deep in `Dataset.from_dict`, which
# is a traceback rather than the "every problem, with a pointer" this module promises.
PLAYER_KEYS = frozenset(f.name for f in fields(Player))
TIER_KEYS = frozenset(f.name for f in fields(Tier))
TOP_KEYS = frozenset(f.name for f in fields(Dataset))
ROW_KEYS = {
    "plan": frozenset(LIST_FIELDS["plan"]),
    "do_not_draft": frozenset(LIST_FIELDS["do_not_draft"]),
    "injuries": frozenset(LIST_FIELDS["injuries"]),
    "sleepers": frozenset(LIST_FIELDS["sleepers"]),
    "sources": frozenset(LIST_FIELDS["sources"]),
}
# Fields that must be text when present, per row type.
STRING_FIELDS = {
    "players": ("name", "pos", "team", "note"),
    "tiers": ("name", "range", "note"),
    "plan": ("position", "guidance"),
    "do_not_draft": ("name", "pos", "team", "why"),
    "injuries": ("name", "team", "severity", "status"),
    "sleepers": ("name", "pos", "team", "why"),
    "sources": ("label", "url"),
}


def _t(value: object) -> str:
    return type(value).__name__


def _unknown_keys(row: dict, allowed: frozenset[str], at: str, bad) -> None:
    for key in sorted(set(row) - allowed):
        near = difflib.get_close_matches(key, sorted(allowed), n=1, cutoff=0.7)
        hint = f" (did you mean {near[0]!r}?)" if near else ""
        bad(f"{at}/{key}", f"unknown field{hint}")


def _strings(row: dict, keys: tuple[str, ...], at: str, bad, nullable: bool = True) -> None:
    for key in keys:
        if key not in row:
            continue
        value = row[key]
        if value is None and nullable:
            continue
        if not isinstance(value, str):
            bad(f"{at}/{key}", f"must be text, got {_t(value)}")


def check(raw: dict) -> list[str]:
    """Every structural problem in *raw*, each prefixed with a JSON pointer."""
    problems: list[str] = []

    def bad(pointer: str, msg: str) -> None:
        problems.append(f"{pointer}: {msg}")

    if not isinstance(raw, dict):
        return ["/: top level must be an object"]

    version = raw.get("schema_version", 0)
    if not isinstance(version, int):
        bad("/schema_version", f"must be an integer, got {_t(version)}")
    elif version > SCHEMA_VERSION:
        bad("/schema_version", f"{version} is newer than this build understands ({SCHEMA_VERSION})")

    for key in REQUIRED_TOP:
        if key not in raw:
            bad(f"/{key}", "required field is missing")
    _unknown_keys(raw, TOP_KEYS, "", bad)
    if "season" in raw and not isinstance(raw["season"], int):
        bad("/season", f"must be a year, got {_t(raw['season'])}")
    for key in ("scoring", "format", "updated"):
        if key in raw and not isinstance(raw[key], str):
            bad(f"/{key}", f"must be text, got {_t(raw[key])}")

    tiers = raw.get("tiers")
    tier_numbers: set[int] = set()
    if not isinstance(tiers, list) or not tiers:
        if "tiers" in raw:
            bad("/tiers", "must be a non-empty array")
    else:
        for i, t in enumerate(tiers):
            at = f"/tiers/{i}"
            if not isinstance(t, dict):
                bad(at, f"must be an object, got {_t(t)}")
                continue
            for key in ("n", "name", "range", "note"):
                if key not in t:
                    bad(f"{at}/{key}", "required field is missing")
            _unknown_keys(t, TIER_KEYS, at, bad)
            _strings(t, STRING_FIELDS["tiers"], at, bad)
            n = t.get("n")
            if isinstance(n, int):
                if n in tier_numbers:
                    bad(f"{at}/n", f"duplicate tier number {n}")
                tier_numbers.add(n)
            elif "n" in t:
                bad(f"{at}/n", f"must be an integer, got {_t(n)}")

    players = raw.get("players")
    if not isinstance(players, list) or not players:
        if "players" in raw:
            bad("/players", "must be a non-empty array")
        return problems

    seen_ranks: dict[int, int] = {}
    seen_names: dict[str, int] = {}
    seen_ids: dict[tuple[str, str], int] = {}

    for i, p in enumerate(players):
        at = f"/players/{i}"
        if not isinstance(p, dict):
            bad(at, f"must be an object, got {_t(p)}")
            continue
        for key in ("rank", "name", "pos", "team", "tier"):
            if key not in p:
                bad(f"{at}/{key}", "required field is missing")
        _unknown_keys(p, PLAYER_KEYS, at, bad)
        _strings(p, STRING_FIELDS["players"], at, bad)

        rank, name = p.get("rank"), p.get("name")
        if isinstance(rank, int):
            if rank != i + 1:
                bad(f"{at}/rank", f"expected {i + 1} (ranks must run 1..N in order), got {rank}")
            if rank in seen_ranks:
                bad(f"{at}/rank", f"duplicate rank {rank}, first seen at /players/{seen_ranks[rank]}")
            seen_ranks[rank] = i
        elif "rank" in p:
            bad(f"{at}/rank", f"must be an integer, got {_t(rank)}")

        if isinstance(name, str):
            if name in seen_names:
                bad(f"{at}/name", f"duplicate player, first seen at /players/{seen_names[name]}")
            seen_names[name] = i
        elif "name" in p:
            bad(f"{at}/name", f"must be a string, got {_t(name)}")

        if "pos" in p and p["pos"] not in POSITIONS:
            bad(f"{at}/pos", f"{p['pos']!r} is not one of {', '.join(POSITIONS)}")
        if p.get("flag") is not None and p.get("flag") not in FLAGS:
            bad(f"{at}/flag", f"{p['flag']!r} is not one of {', '.join(FLAGS)} (or null)")
        if "tier" in p and tier_numbers and p.get("tier") not in tier_numbers:
            bad(f"{at}/tier", f"{p['tier']!r} is not a defined tier ({sorted(tier_numbers)})")

        for numeric, low in (("adp", 0.0), ("adp_sd", -0.001), ("projected", -0.001), ("value", 0.0)):
            v = p.get(numeric)
            if v is not None and (not isinstance(v, int | float) or v <= low):
                bad(f"{at}/{numeric}", f"must be a positive number or null, got {v!r}")
        age = p.get("age")
        if age is not None and (not isinstance(age, int) or not 18 <= age <= 50):
            bad(f"{at}/age", f"must be an age between 18 and 50 or null, got {age!r}")
        exp = p.get("exp")
        if exp is not None and (not isinstance(exp, int) or exp < 0):
            bad(f"{at}/exp", f"must be a non-negative number of seasons or null, got {exp!r}")
        bye = p.get("bye")
        if bye is not None and (not isinstance(bye, int) or not 1 <= bye <= 18):
            bad(f"{at}/bye", f"must be a week between 1 and 18 or null, got {bye!r}")

        ids = p.get("ids")
        if "ids" in p:
            # None is not "absent": `Player(ids=None)` raises on `set(self.ids)`.
            if not isinstance(ids, dict):
                bad(f"{at}/ids", f"must be an object, got {_t(ids)}")
            else:
                for source, value in ids.items():
                    if source not in ID_SOURCES:
                        bad(f"{at}/ids/{source}", f"unknown id source (expected {', '.join(ID_SOURCES)})")
                    elif value is not None:
                        key = (source, str(value))
                        if key in seen_ids:
                            bad(f"{at}/ids/{source}", f"duplicate id, first seen at /players/{seen_ids[key]}")
                        seen_ids[key] = i

    for field_name, required in LIST_FIELDS.items():
        rows = raw.get(field_name)
        if rows is None:
            continue
        if not isinstance(rows, list):
            bad(f"/{field_name}", f"must be an array, got {_t(rows)}")
            continue
        for i, row in enumerate(rows):
            at = f"/{field_name}/{i}"
            if not isinstance(row, dict):
                bad(at, f"must be an object, got {_t(row)}")
                continue
            for key in required:
                if key not in row:
                    bad(f"{at}/{key}", "required field is missing")
            _unknown_keys(row, ROW_KEYS[field_name], at, bad)
            # Rail text is not nullable: a null `status` used to validate cleanly,
            # build cleanly, then throw in the browser and render an empty page.
            _strings(row, STRING_FIELDS[field_name], at, bad, nullable=False)
            if field_name == "injuries" and row.get("severity") not in SEVERITIES:
                bad(f"{at}/severity", f"{row.get('severity')!r} is not one of {', '.join(SEVERITIES)}")
            if field_name == "sources" and "url" in row:
                url = row.get("url")
                if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
                    # Anything else (javascript:, data:) would be rendered as a link.
                    bad(f"{at}/url", f"must be an http(s) URL, got {url!r}")

    return problems


def check_file(path: str | Path) -> list[str]:
    """Structural problems in the dataset at *path*; parse errors come back as one."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return [f"/: not valid JSON — {exc}"]
    except OSError as exc:
        return [f"/: cannot read file — {exc}"]
    return check(raw)
