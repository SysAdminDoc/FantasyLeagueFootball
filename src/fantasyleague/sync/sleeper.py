"""Follow a live Sleeper draft and cross players off the board.

Sleeper's API is public, read-only, and needs no key:
    GET https://api.sleeper.app/v1/draft/{draft_id}/picks
      -> [{player_id, picked_by, roster_id, round, draft_slot, pick_no, ...}]

Picks are joined to the board on `ids.sleeper`, never on the display name.
Polling is idempotent: the same pick arriving twice is a no-op, so a dropped
connection just resyncs on the next tick.
"""

from __future__ import annotations

import http.client
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
        # ValueError, not TypeError: callers convert it to a logged retry.
        raise ValueError(f"unexpected response for draft {draft_id!r}")  # noqa: TRY004
    return sorted(picks, key=lambda p: p.get("pick_no") or 0)


def fetch_draft(draft_id: str, timeout: float = 10.0) -> dict:
    """Draft metadata: type, status, settings (teams, rounds), season."""
    return _get(f"{API}/draft/{draft_id}", timeout=timeout)


def current_week(timeout: float = 10.0) -> tuple[int, int]:
    """(season, week) the NFL is in right now, per `/state/nfl`.

    Before the regular season starts (`season_type` "pre" or "off") the answer is
    week 1 of the upcoming season, which is what every in-season command wants.
    """
    state = _get(f"{API}/state/nfl", timeout=timeout)
    if not isinstance(state, dict):
        raise ValueError("unexpected response for /state/nfl")  # noqa: TRY004
    season = int(state.get("season") or state.get("league_season") or 0)
    week = int(state.get("week") or 1)
    if state.get("season_type") != "regular":
        week = 1
    return season, max(1, min(week, 18))


@dataclass
class SleeperSync:
    """Polls a Sleeper draft and pushes picks into a `serve.Bus`.

    The bus must offer `pick`, `pick_offboard` and `undo_pick_no`: a live draft
    includes players this board never ranked, and commissioners undo picks.
    """

    data: Dataset
    draft_id: str
    bus: object
    interval: float = DEFAULT_INTERVAL
    on_event: object = None  # optional callable(str) for CLI logging

    _index: dict = field(default_factory=dict, init=False)
    # pick_no -> the sleeper player_id applied for it, so a commissioner's undo
    # (the pick vanishes from the feed) can be mirrored instead of sticking.
    _applied: dict = field(default_factory=dict, init=False)
    _unknown: set = field(default_factory=set, init=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._index = by_external_id(self.data, "sleeper")

    # ---- one pass ----------------------------------------------------------

    def resolve(self, pick: dict) -> Player | None:
        """Board player for a Sleeper pick, or None when they're off our board."""
        return self._index.get(str(pick.get("player_id")))

    @staticmethod
    def _display_name(pick: dict) -> str:
        meta = pick.get("metadata") or {}
        name = " ".join(filter(None, [meta.get("first_name"), meta.get("last_name")])).strip()
        return name or f"sleeper {pick.get('player_id')}"

    def _undo_missing(self, picks: list[dict]) -> int:
        """Mirror picks that disappeared from the feed (commissioner undo / reset)."""
        live = {p.get("pick_no") for p in picks}
        undone = 0
        for no in sorted(self._applied.keys() - live, reverse=True):
            self._applied.pop(no, None)
            if self.bus.undo_pick_no(no, source="sleeper"):
                undone += 1
                self._log(f"pick {no}: undone in Sleeper")
        return undone

    def apply(self, picks: list[dict]) -> int:
        """Sync *picks* into the bus; returns how many were newly applied."""
        self._undo_missing(picks)
        applied = 0
        for pick in picks:
            no = pick.get("pick_no")
            pid = str(pick.get("player_id"))
            if self._applied.get(no) == pid:
                continue
            if no in self._applied:               # same slot, different player: redo it
                self.bus.undo_pick_no(no, source="sleeper")
            self._applied[no] = pid
            slot = pick.get("draft_slot")
            player = self.resolve(pick)
            if player is None:
                # Still a pick: it burns an overall selection, and dropping it left
                # the counter — and therefore "your pick" — behind for good.
                if pid not in self._unknown:      # log once, not every poll
                    self._unknown.add(pid)
                    self._log(f"pick {no}: {self._display_name(pick)} is not on this board")
                self.bus.pick_offboard(source="sleeper", name=self._display_name(pick), slot=slot)
                applied += 1
                continue
            if self.bus.pick(player.rank, source="sleeper", slot=slot):
                applied += 1
                self._log(f"pick {no}: {player.name} ({player.pos} {player.team})")
        return applied

    def poll_once(self) -> int:
        """Fetch and apply. Network errors are logged, never raised — a draft
        must not stop because one request timed out.

        OSError, not just URLError: `RemoteDisconnected` is a ConnectionResetError,
        and `IncompleteRead` is an HTTPException — neither is a URLError, so both
        used to escape and kill the polling thread silently.
        """
        try:
            picks = fetch_picks(self.draft_id)
        except (OSError, http.client.HTTPException, ValueError, json.JSONDecodeError) as exc:
            self._log(f"poll failed ({exc.__class__.__name__}: {exc}); retrying")
            return 0
        return self.apply(picks)

    # ---- background loop ---------------------------------------------------

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001 - a live draft outlives any one bug
                self._log(f"poll crashed ({exc.__class__.__name__}: {exc}); retrying")
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
