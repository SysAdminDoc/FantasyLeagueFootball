"""Follow a live Sleeper draft and cross players off the board.

Sleeper's API is public, read-only, and needs no key:
    GET https://api.sleeper.app/v1/draft/{draft_id}/picks
      -> [{player_id, picked_by, roster_id, round, draft_slot, pick_no, ...}]

Picks are joined to the board on `ids.sleeper`, never on the display name.
Polling is idempotent: the same pick arriving twice is a no-op, so a dropped
connection just resyncs on the next tick.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from ..board import by_external_id
from ..models import Dataset, Player

API = "https://api.sleeper.app/v1"
DEFAULT_INTERVAL = 3.0  # Draft Caddie polls at this rate; well under Sleeper's limits
USER_AGENT = "FantasyLeagueFootball (+https://github.com/SysAdminDoc/FantasyLeagueFootball)"


def _get(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fetch_picks(draft_id: str, timeout: float = 10.0) -> list[dict]:
    """Every pick made so far in *draft_id*, oldest first."""
    picks = _get(f"{API}/draft/{draft_id}/picks", timeout=timeout)
    if not isinstance(picks, list):
        raise ValueError(f"unexpected response for draft {draft_id!r}")
    return sorted(picks, key=lambda p: p.get("pick_no") or 0)


def fetch_draft(draft_id: str, timeout: float = 10.0) -> dict:
    """Draft metadata: type, status, settings (teams, rounds), season."""
    return _get(f"{API}/draft/{draft_id}", timeout=timeout)


@dataclass
class SleeperSync:
    """Polls a Sleeper draft and pushes picks into a Bus.

    `bus` is anything with `.pick(rank, source=...)` — the serve.Bus in practice.
    """

    data: Dataset
    draft_id: str
    bus: object
    interval: float = DEFAULT_INTERVAL
    on_event: object = None  # optional callable(str) for CLI logging

    _index: dict = field(default_factory=dict, init=False)
    _seen: set = field(default_factory=set, init=False)
    _unknown: set = field(default_factory=set, init=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._index = by_external_id(self.data, "sleeper")

    # ---- one pass ----------------------------------------------------------

    def resolve(self, pick: dict) -> Player | None:
        """Board player for a Sleeper pick, or None when they're off our board."""
        return self._index.get(str(pick.get("player_id")))

    def apply(self, picks: list[dict]) -> int:
        """Cross off everyone in *picks*; returns how many were newly applied."""
        applied = 0
        for pick in picks:
            no = pick.get("pick_no")
            if no in self._seen:
                continue
            self._seen.add(no)
            player = self.resolve(pick)
            if player is None:
                pid = str(pick.get("player_id"))
                if pid not in self._unknown:      # log once, not every poll
                    self._unknown.add(pid)
                    self._log(f"pick {no}: sleeper player {pid} is not on this board")
                continue
            if self.bus.pick(player.rank, source="sleeper"):
                applied += 1
                self._log(f"pick {no}: {player.name} ({player.pos} {player.team})")
        return applied

    def poll_once(self) -> int:
        """Fetch and apply. Network errors are logged, never raised — a draft
        must not stop because one request timed out."""
        try:
            picks = fetch_picks(self.draft_id)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            self._log(f"poll failed ({exc.__class__.__name__}: {exc}); retrying")
            return 0
        return self.apply(picks)

    # ---- background loop ---------------------------------------------------

    def run_forever(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.interval)

    def start(self) -> SleeperSync:
        self._thread = threading.Thread(target=self.run_forever, name="sleeper-sync", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 2)

    def _log(self, msg: str) -> None:
        if callable(self.on_event):
            self.on_event(msg)
