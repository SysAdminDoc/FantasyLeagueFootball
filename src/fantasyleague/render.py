"""Render the dataset into a single self-contained HTML draft board."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from importlib import resources
from pathlib import Path

from . import __version__
from .models import Dataset


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


def render(data: Dataset, title: str | None = None) -> str:
    """Return the complete HTML document for *data*."""
    payload = {
        "season": data.season,
        "scoring": data.scoring,
        "format": data.format,
        "updated": data.updated,
        "tiers": [asdict(t) for t in data.tiers],
        "players": [asdict(p) for p in data.players],
        "plan": [asdict(p) for p in data.plan],
        "do_not_draft": [asdict(e) for e in data.do_not_draft],
        "injuries": [asdict(i) for i in data.injuries],
        "sleepers": [asdict(e) for e in data.sleepers],
        "sources": [asdict(s) for s in data.sources],
    }

    html = _asset("board.html.template")
    replacements = {
        "__CSS__": _asset("board.css"),
        "__JS__": _asset("board.js"),
        # </script> inside a JSON string would close the host <script> tag early.
        "__DATA__": json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"),
        "__VERSION__": __version__,
        "__SEASON__": str(data.season),
        "__UPDATED__": _pretty_date(data.updated),
        "__TITLE__": title or f"{data.season} Draft War Room",
    }
    for token, value in replacements.items():
        html = html.replace(token, value)
    return html


def write(data: Dataset, out: str | Path, title: str | None = None) -> Path:
    """Render *data* and write it to *out*, creating parent directories."""
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(data, title=title), encoding="utf-8")
    return path
