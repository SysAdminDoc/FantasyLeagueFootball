"""Refresh injury status and trending adds from Sleeper's public player database.

`GET /v1/players/nfl` is ~15 MB and Sleeper asks that it be fetched at most once
a day, so it is cached on disk (platform cache dir, or FANTASYLEAGUE_CACHE) and
re-used until it ages out. Everything here degrades to the packaged data when
offline — a draft must never block on a refresh.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

from ..models import Dataset, Injury
from .sleeper import API, USER_AGENT

MAX_AGE_SECONDS = 24 * 3600

# Sleeper's designations, worst first. Anything here earns a "watch" flag.
SEVERITY_BY_STATUS = {
    "Out": "out",
    "IR": "out",
    "PUP": "out",
    "NA": "out",
    "Sus": "out",
    "Doubtful": "risk",
    "Questionable": "risk",
    "Probable": "ok",
}


def cache_dir() -> Path:
    env = os.environ.get("FANTASYLEAGUE_CACHE")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / "fantasyleague"
    return Path.home() / ".cache" / "fantasyleague"


def cache_path() -> Path:
    return cache_dir() / "sleeper-players-nfl.json"


def cache_age(path: Path | None = None) -> float | None:
    """Seconds since the cache was written, or None when there is no cache."""
    p = path or cache_path()
    if not p.exists():
        return None
    return time.time() - p.stat().st_mtime


def fetch_players(max_age: float = MAX_AGE_SECONDS, timeout: float = 60.0) -> tuple[dict, str]:
    """Sleeper's whole player database plus where it came from ("cache"/"network").

    Falls back to a stale cache when the network is unreachable — stale beats nothing.
    """
    path = cache_path()
    age = cache_age(path)
    if age is not None and age < max_age:
        return json.loads(path.read_text(encoding="utf-8")), "cache"

    req = urllib.request.Request(
        f"{API}/players/nfl", headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        if age is not None:
            return json.loads(path.read_text(encoding="utf-8")), "stale-cache"
        raise
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload, "network"


def describe(record: dict) -> str | None:
    """One-line status for a Sleeper player record, or None when healthy."""
    status = record.get("injury_status")
    if not status:
        return None
    part = record.get("injury_body_part")
    practice = record.get("practice_participation")
    bits = [status]
    if part and part.lower() not in ("", "not injury related"):
        bits.append(str(part).lower())
    line = " — ".join(bits)
    return f"{line}|{practice}" if practice else line


def apply_status(data: Dataset, players: dict) -> tuple[Dataset, list[str]]:
    """Return *data* with injuries rebuilt from Sleeper and `watch` flags refreshed.

    Only the `watch` flag is touched: `value` and `avoid` are editorial judgements
    about price, not health, and must survive a refresh.
    """
    injuries: list[Injury] = []
    updated: list[str] = []
    new_players = []

    for p in data.players:
        rec = players.get(p.id_for("sleeper") or "") or {}
        status = rec.get("injury_status")
        line = describe(rec)
        severity = SEVERITY_BY_STATUS.get(status or "", "risk" if status else None)
        if line and severity:
            injuries.append(Injury(name=p.name, team=p.team, severity=severity, status=line))
            updated.append(f"{p.name}: {line.replace('|', ' / ')}")
            new_players.append(replace(p, flag="watch" if p.flag in (None, "watch") else p.flag))
        else:
            new_players.append(replace(p, flag=None) if p.flag == "watch" else p)

    order = {"out": 0, "risk": 1, "ok": 2}
    injuries.sort(key=lambda i: (order[i.severity], i.name))
    return replace(data, players=new_players, injuries=injuries), updated


def trending(kind: str = "add", hours: int = 24, limit: int = 10, timeout: float = 15.0) -> list[dict]:
    """Most-added (or -dropped) players in the last *hours*, newest data first."""
    url = f"{API}/players/nfl/trending/{kind}?lookback_hours={hours}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        rows = json.loads(r.read())
    return rows if isinstance(rows, list) else []


def name_trending(rows: list[dict], players: dict) -> list[dict]:
    """Attach name/pos/team to trending rows, dropping ids we can't resolve."""
    out = []
    for row in rows:
        rec = players.get(str(row.get("player_id")))
        if not rec:
            continue
        name = rec.get("full_name") or " ".join(
            filter(None, [rec.get("first_name"), rec.get("last_name")])
        )
        out.append(
            {
                "name": name,
                "pos": rec.get("position") or "",
                "team": rec.get("team") or "",
                "count": row.get("count", 0),
            }
        )
    return out
