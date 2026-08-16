"""Boris Chen tier import — the staleness guard is the point."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fantasyleague import board
from fantasyleague.sync import borischen

SAMPLE = """Tier 1: Christian McCaffrey, Jahmyr Gibbs, Bijan Robinson
Tier 2: Saquon Barkley, De'Von Achane, Jonathan Taylor
Tier 3: Travis Etienne Jr., Kyren Williams
"""


class FakeResponse:
    def __init__(self, text: str, last_modified: str | None):
        self._text = text.encode("utf-8")
        self.headers = {"Last-Modified": last_modified} if last_modified else {}

    def read(self):
        return self._text

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _serve(monkeypatch, text=SAMPLE, days_old=1.0):
    stamp = datetime.now(UTC) - timedelta(days=days_old)
    header = stamp.strftime("%a, %d %b %Y %H:%M:%S GMT")
    monkeypatch.setattr(
        borischen.urllib.request, "urlopen", lambda *a, **k: FakeResponse(text, header)
    )


def test_parse_reads_the_published_format():
    tiers = borischen.parse(SAMPLE)
    assert tiers["christian mccaffrey"] == 1
    assert tiers["jahmyr gibbs"] == 1
    assert tiers["saquon barkley"] == 2
    assert tiers["travis etienne"] == 3, "suffixes are normalised away"
    assert len(tiers) == 8


def test_parse_ignores_noise():
    assert borischen.parse("nonsense\n\nTier 4: A, B\n") == {"a": 4, "b": 4}
    assert borischen.parse("") == {}


def test_fetch_refuses_a_stale_file(monkeypatch):
    _serve(monkeypatch, days_old=233)
    with pytest.raises(borischen.StaleTiers, match="233 days ago"):
        borischen.fetch("RB")


def test_fetch_allows_stale_when_asked(monkeypatch):
    _serve(monkeypatch, days_old=233)
    mapping, age = borischen.fetch("RB", allow_stale=True)
    assert mapping["jahmyr gibbs"] == 1
    assert age == pytest.approx(233, abs=1)


def test_fetch_accepts_a_fresh_file(monkeypatch):
    _serve(monkeypatch, days_old=2)
    mapping, age = borischen.fetch("RB")
    assert mapping and age < 3


def test_fetch_rejects_unknown_scoring_and_position():
    with pytest.raises(ValueError, match="unknown scoring"):
        borischen.fetch("RB", scoring="superflex")
    with pytest.raises(ValueError, match="no published tiers for position"):
        borischen.fetch("P")


def test_missing_last_modified_is_not_treated_as_stale(monkeypatch):
    monkeypatch.setattr(
        borischen.urllib.request, "urlopen", lambda *a, **k: FakeResponse(SAMPLE, None)
    )
    mapping, age = borischen.fetch("RB")
    assert age is None and mapping


def test_apply_offsets_positions_so_tier_1s_stay_distinct():
    data = board.load()
    fresh, unmatched = borischen.apply(
        data, {"RB": {"jahmyr gibbs": 1}, "WR": {"jamarr chase": 1}}
    )
    by_name = {p.name: p for p in fresh.players}
    assert by_name["Jahmyr Gibbs"].tier != by_name["Ja'Marr Chase"].tier
    assert "Josh Allen (QB)" in unmatched


def test_apply_keeps_the_board_loadable(tmp_path):
    data = board.load()
    fresh, _ = borischen.apply(data, {"RB": borischen.parse(SAMPLE)})
    board.validate(fresh)
    out = board.save(fresh, tmp_path / "t.json")
    again = board.load(out)
    assert len(again.players) == len(data.players)
    assert {p.tier for p in again.players} <= {t.n for t in again.tiers}


def test_published_files_are_still_stale_for_2026():
    """A canary: when Boris Chen publishes 2026 files this starts failing, which is
    the signal to drop --allow-stale from the docs."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"{borischen.BASE}/text_RB-HALF.txt", headers={"User-Agent": borischen.USER_AGENT}, method="HEAD"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            age = borischen._age_days(r.headers.get("Last-Modified"))
    except (urllib.error.URLError, TimeoutError) as exc:
        pytest.skip(f"offline: {exc}")
    assert age is not None
    if age <= borischen.MAX_AGE_DAYS:
        pytest.fail(f"tiers are fresh again ({age:.0f}d) — update the docs and drop --allow-stale")
