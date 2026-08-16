"""Render the dataset into a single self-contained HTML draft board."""

from __future__ import annotations

import hashlib
import html as htmlmod
import json
import re
from dataclasses import asdict
from datetime import date
from importlib import resources
from pathlib import Path

from . import __version__
from .board import TIER_BREAK_THRESHOLD
from .models import DEFAULT_LINEUP, Dataset


def _asset(name: str) -> str:
    return resources.files(__package__).joinpath("assets", name).read_text("utf-8")


def _pretty_date(iso: str) -> str:
    """Format an ISO date for display, falling back to the raw string."""
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    # Built by hand: %-d is glibc-only and %#d is Windows-only.
    return f"{d:%A, %B} {d.day}, {d.year}"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def board_id(data: Dataset, league: str | None = None) -> str:
    """Stable identity for the browser-storage key.

    Crossed-off state is stored by rank, so it may only carry over between builds
    whose player order is identical. Hash the season plus the ordered roster —
    note edits keep the id, any re-ranking changes it — and suffix the league so
    two leagues drafting from the same board never share a key.
    """
    digest = hashlib.sha1(
        (str(data.season) + "|" + "|".join(p.name for p in data.players)).encode("utf-8")
    ).hexdigest()[:10]
    return f"{digest}-{_slug(league)}" if league else digest


def render(
    data: Dataset,
    title: str | None = None,
    league: str | None = None,
    teams: int | None = None,
    slot: int | None = None,
    live: bool = False,
) -> str:
    """Return the complete HTML document for *data*. *live* marks a served page that
    should follow the server's pick log over Server-Sent Events."""
    payload = {
        "tier_break": TIER_BREAK_THRESHOLD,
        "live": live,
        "board_id": board_id(data, league),
        "league": league or "",
        "draft": {"teams": teams or 12, "slot": slot},
        "adp": data.adp,
        "auction": data.auction,
        "trending": data.trending,
        "refreshed": data.refreshed,
        "season": data.season,
        "scoring": data.scoring,
        "format": data.format,
        # The page builds its roster slots, lineup needs and live value-over-
        # replacement from this, so a superflex board prices and plans as one.
        "lineup": data.lineup or DEFAULT_LINEUP,
        "provenance": data.provenance,
        "updated": data.updated,
        "tiers": [asdict(t) for t in data.tiers],
        "players": [asdict(p) for p in data.players],
        "plan": [asdict(p) for p in data.plan],
        "do_not_draft": [asdict(e) for e in data.do_not_draft],
        "injuries": [asdict(i) for i in data.injuries],
        "sleepers": [asdict(e) for e in data.sleepers],
        "sources": [asdict(s) for s in data.sources],
    }

    default_title = f"{data.season} Draft War Room"
    if league:
        default_title = f"{league} · {default_title}"

    html = _asset("board.html.template")
    replacements = {
        "__CSS__": _asset("board.css"),
        "__JS__": _asset("board.js"),
        # No raw angle bracket may reach the inline <script>. `</script>` closing the
        # host tag is the obvious case, but `<!--` followed by `<script` is worse: it
        # puts the tokenizer into the double-escaped state, the real closing tag stops
        # closing anything, and one string in one note renders a blank page. <
        # and friends are valid JSON *and* valid JS string escapes, so the payload
        # parses identically.
        "__DATA__": (
            json.dumps(payload, ensure_ascii=False)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
        ),
        # Everything below lands in markup as text and must be escaped: --title and
        # the dataset's `updated`/`season` can come from an untrusted --data file.
        "__VERSION__": htmlmod.escape(__version__),
        "__SEASON__": htmlmod.escape(str(data.season)),
        "__UPDATED__": htmlmod.escape(_pretty_date(data.updated)),
        "__TITLE__": htmlmod.escape(title or default_title),
    }
    for token, value in replacements.items():
        html = html.replace(token, value)
    return html


def write(
    data: Dataset,
    out: str | Path,
    title: str | None = None,
    league: str | None = None,
    teams: int | None = None,
    slot: int | None = None,
) -> Path:
    """Render *data* and write it to *out*, creating parent directories."""
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(data, title=title, league=league, teams=teams, slot=slot), encoding="utf-8")
    return path
