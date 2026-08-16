"""Command line interface for the draft board."""

from __future__ import annotations

import argparse
import csv
import http.client
import sys
import time
import webbrowser
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from . import board as board_mod
from . import draft as draft_mod
from . import render as render_mod
from . import schema as schema_mod
from . import serve as serve_mod
from . import variant as variant_mod
from .models import DEFAULT_LINEUP, DEFAULT_ROSTER_SIZE
from .sync import adp as adp_mod
from .sync import borischen as borischen_mod
from .sync import players as players_mod
from .sync import projections as proj_mod
from .sync import sleeper as sleeper_mod

DEFAULT_OUT = Path("dist/draft-board.html")

_COLS = "{:>4}  {:<24} {:<3} {:<4} {:>4}  {}"


def _bounded_int(text: str, low: int, what: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < low:
        raise argparse.ArgumentTypeError(f"{what} must be at least {low}, got {value}")
    return value


def _positive(text: str) -> int:
    return _bounded_int(text, 1, "value")


def _team_count(text: str) -> int:
    return _bounded_int(text, 2, "league size")


def _interval(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if value < 1.0:
        # Below a second this hammers a public API for no benefit; Sleeper picks
        # do not arrive faster than that.
        raise argparse.ArgumentTypeError(f"polling interval must be at least 1 second, got {value:g}")
    return value


def _check_slot(args: argparse.Namespace) -> None:
    """A slot outside the league silently produced a board with no picks at all."""
    slot = getattr(args, "slot", None)
    teams = getattr(args, "teams", None)
    if slot is not None and teams is not None and not 1 <= slot <= teams:
        raise ValueError(f"--slot must be between 1 and {teams} (--teams is {teams})")


def _print_players(players, header: str) -> None:
    print(f"\n{header}\n")
    print(_COLS.format("RANK", "PLAYER", "POS", "TEAM", "TIER", "FLAG"))
    print("-" * 62)
    for p in players:
        print(_COLS.format(p.rank, p.name, p.pos, p.team, p.tier, (p.flag or "").upper()))
    print()


def _print_odds(players, teams: int, slot: int, current: int, rounds: int) -> None:
    """Best available with survival odds at your next two picks."""
    mine = draft_mod.next_picks(teams, slot, current, count=3, rounds=rounds)
    if not mine:
        print(f"\nPick {current} · slot {slot} has finished drafting ({rounds} rounds)\n")
        _print_players(players, "Best available")
        return
    yours = "your pick now" if mine[0] == current else f"yours in {mine[0] - current}"
    print(f"\nPick {current} · {yours} · your next picks: {', '.join(map(str, mine))}")
    two = len(mine) > 1
    heading = f"Best available — odds of surviving to pick {mine[0]}" + (f" / {mine[1]}" if two else "")
    print(f"\n{heading}\n")
    cols = f"{'RANK':>4}  {'PLAYER':<24} {'POS':<3} {'TEAM':<4} {'ADP':>5}  {'@' + str(mine[0]):>6}"
    if two:
        cols += f"  {'@' + str(mine[1]):>6}"
    print(cols + "  CALL")
    print("-" * (len(cols) + 6))
    for p in players:
        if p.adp is None:
            odds = "  ".join(["   n/a"] * (2 if two else 1))
            call = ""
        else:
            probs = [draft_mod.player_availability(p, k) for k in mine[:2]]
            odds = "  ".join(f"{pr:>6.0%}" for pr in probs)
            call = draft_mod.band(probs[0])
        adp = f"{p.adp:>5.1f}" if p.adp is not None else "    -"
        print(f"{p.rank:>4}  {p.name:<24} {p.pos:<3} {p.team:<4} {adp}  {odds}  {call}")
    print()


def cmd_build(args: argparse.Namespace) -> int:
    _check_slot(args)
    data = board_mod.load(args.data)
    path = render_mod.write(
        data, args.out, title=args.title, league=args.league, teams=args.teams, slot=args.slot
    )
    size = path.stat().st_size
    print(f"Built {path} ({size:,} bytes, {len(data.players)} players)")
    if args.open:
        webbrowser.open(path.resolve().as_uri())
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    data = board_mod.load(args.data)
    players = board_mod.filter_players(data, pos=args.pos, flag=args.flag)
    # Not .capitalize(): that lowercases the rest, so --pos RB read "Rb players".
    label = " ".join(filter(None, [args.pos or "All", args.flag or "", "players"]))
    _print_players(players[: args.limit], label[:1].upper() + label[1:])
    return 0


def cmd_values(args: argparse.Namespace) -> int:
    data = board_mod.load(args.data)
    _print_players(board_mod.filter_players(data, flag="value"), "Value picks — ADP later than projection")
    _print_players(board_mod.filter_players(data, flag="avoid"), "Reaches — ADP earlier than projection")
    print("Do not draft\n")
    for e in data.do_not_draft:
        print(f"  {e.name} ({e.pos} {e.team}) — {e.why}")
    print()
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    _check_slot(args)
    data = board_mod.load(args.data)
    drafted = board_mod.resolve_many(data, args.drafted or [])
    avail = board_mod.best_available(data, pos=args.pos, drafted=drafted, limit=args.limit)

    if args.slot:
        rounds = draft_mod.rounds_for(len(data.players), args.teams)
        _print_odds(avail, args.teams, args.slot, args.pick or len(drafted) + 1, rounds)
    else:
        _print_players(avail, "Best available")
    breaks = board_mod.tier_breaks(data, drafted=drafted)
    if breaks:
        print("Tier breaks — reach here rather than wait for the turn\n")
        for tier, left in breaks:
            print(f"  Tier {tier.n} · {tier.name} — {left} left")
        print()
    counts = board_mod.position_counts(data, drafted=drafted)
    print("Remaining: " + "  ".join(f"{k} {v}" for k, v in sorted(counts.items())) + "\n")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    _check_slot(args)
    data = board_mod.load(args.data)
    if getattr(args, "sleeper", None):
        # Check the draft id before serving: otherwise a typo shows as "poll
        # failed … retrying" every few seconds with no explanation.
        try:
            meta = sleeper_mod.fetch_draft(args.sleeper)
            settings = meta.get("settings") or {}
            teams = settings.get("teams")
            print(
                f"Sleeper draft {args.sleeper}: {meta.get('type', 'unknown')} draft, "
                f"{teams or '?'} teams, status {meta.get('status', '?')}"
            )
            if teams and teams != args.teams:
                print(f"  using the draft's league size ({teams}) instead of --teams {args.teams}")
                args.teams = teams
                _check_slot(args)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            print(f"error: could not read Sleeper draft {args.sleeper} ({exc})", file=sys.stderr)
            return 1
    try:
        server = serve_mod.BoardServer(
            data, host=args.host, port=args.port, title=args.title, league=args.league,
            teams=args.teams, slot=args.slot,
        )
    except OSError as exc:
        print(
            f"error: could not bind {args.host}:{args.port} ({exc.strerror or exc}). "
            "Another board may already be serving — pass --port 0 to pick a free one.",
            file=sys.stderr,
        )
        return 1
    server.start()
    urls = server.urls()
    print("Serving the board (Ctrl+C to stop):")
    for u in urls:
        print("  " + u)

    sync = None
    if getattr(args, "sleeper", None):
        sync = sleeper_mod.SleeperSync(
            data, args.sleeper, server.bus, interval=args.every,
            on_event=lambda m: print(f"  sleeper: {m}", flush=True),
        )
        print(f"Following Sleeper draft {args.sleeper} every {args.every:g}s.")
    # flush: the process then sleeps forever, and piped stdout is block-buffered,
    # so a supervising process would otherwise see nothing at all.
    print("Picks made in any open tab, or POSTed to /state, appear everywhere within a second.", flush=True)
    if sync:
        sync.start()
    if args.open:
        webbrowser.open(urls[0])
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        if sync:
            sync.stop()
        server.stop()
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    """Refresh market data: ADP, projections, auction values, injuries and trending."""
    data = board_mod.load(args.data)
    try:
        players, origin = players_mod.fetch_players(max_age=0 if args.force else players_mod.MAX_AGE_SECONDS)
    except (OSError, http.client.HTTPException, ValueError) as exc:
        print(f"error: could not reach Sleeper and no cache is available ({exc})", file=sys.stderr)
        return 1
    print(f"Player database: {origin} ({len(players):,} records)")
    if origin == "stale-cache":
        age = players_mod.cache_age()
        print(f"  offline — using a cache {age / 3600:.1f}h old")

    if not args.no_adp:
        try:
            payload = adp_mod.fetch(scoring=args.adp_format, teams=args.teams, year=data.season)
            data, changed, unmatched = adp_mod.apply(data, payload, reflag=args.reflag)
            print(f"ADP: {data.adp['format']}, {data.adp['window']}")
            print(
                f"  {len(data.players) - len(unmatched)} matched, {len(changed)} moved, "
                f"{len(unmatched)} without a listing"
            )
            for line in unmatched[:5]:
                print("    no ADP: " + line)
            if args.reflag:
                flags = {}
                for pl in data.players:
                    flags[pl.flag] = flags.get(pl.flag, 0) + 1
                print(f"  flags recomputed: {flags.get('value', 0)} value, {flags.get('avoid', 0)} reach")
        except (OSError, http.client.HTTPException, ValueError) as exc:
            print(f"  ADP unavailable ({exc}); keeping the stored numbers")

    if not args.no_projections:
        try:
            rows = proj_mod.fetch(season=data.season)
            scoring = data.scoring if data.scoring in proj_mod.POINTS_KEY else "half_ppr"
            points = proj_mod.points_by_id(rows, scoring=scoring)
            data, hits = proj_mod.apply(data, points)
            print(f"Projections: {hits} of {len(data.players)} players ({scoring})")

            values = proj_mod.auction_values(
                data, data.lineup or DEFAULT_LINEUP, args.teams,
                budget=args.budget, roster_size=args.roster_size,
            )
            data = replace(
                data,
                players=[replace(p, value=values.get(p.rank)) for p in data.players],
                auction={"budget": args.budget, "roster_size": args.roster_size, "teams": args.teams},
            )
            priced = [p for p in data.players if p.value]
            print(f"Auction: {len(priced)} players priced on a ${args.budget} budget")
            for p in priced[:5]:
                print(f"  ${p.value:>3}  {p.name} ({p.pos} {p.team})")
        except (OSError, http.client.HTTPException, ValueError) as exc:
            print(f"  projections unavailable ({exc}); keeping the stored numbers")

    data, profiled = players_mod.apply_profile(data, players)
    print(f"Profiles: age and experience for {profiled} players")

    data, updated = players_mod.apply_status(data, players)
    print(f"Injury board: {len(data.injuries)} players carrying a designation")
    for line in updated[: args.limit]:
        print("  " + line)
    if len(updated) > args.limit:
        print(f"  … and {len(updated) - args.limit} more")

    if not args.no_trending:
        try:
            rows = players_mod.trending(hours=args.hours, limit=args.trending_limit)
            data = replace(
                data,
                trending=players_mod.name_trending(rows, players),
                trending_hours=args.hours,
            )
            print(f"Trending adds ({args.hours}h): {len(data.trending)}")
            for t in data.trending[:5]:
                print(f"  +{t['count']:,} {t['name']} ({t['pos']} {t['team']})")
        except OSError as exc:
            print(f"  trending unavailable ({exc}); keeping the previous list")

    data = replace(data, refreshed=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"))
    out = args.out or args.data or _packaged_data_path()
    board_mod.save(data, out)
    print(f"Wrote {out}")
    return 0


CSV_COLUMNS = (
    "rank", "name", "pos", "team", "tier", "flag", "adp", "adp_sd", "bye",
    "projected", "value", "age", "exp", "note",
)


def cmd_export(args: argparse.Namespace) -> int:
    """Write the board as CSV — for a spreadsheet, or to paste into another tool."""
    data = board_mod.load(args.data)
    rows = board_mod.filter_players(data, pos=args.pos, flag=args.flag)[: args.limit]

    def write_rows(handle) -> None:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for p in rows:
            writer.writerow([getattr(p, c) if getattr(p, c) is not None else "" for c in CSV_COLUMNS])

    if args.out:
        with Path(args.out).open("w", newline="", encoding="utf-8") as fh:
            write_rows(fh)
        print(f"Wrote {args.out} ({len(rows)} players)")
    else:
        write_rows(sys.stdout)
    return 0


def cmd_tiers(args: argparse.Namespace) -> int:
    """Re-tier the board from Boris Chen's published consensus tiers."""
    data = board_mod.load(args.data)
    fetched: dict[str, dict[str, int]] = {}
    ages: list[float] = []
    for pos in ("QB", "RB", "WR", "TE", "K", "DST"):
        try:
            mapping, age = borischen_mod.fetch(
                pos, scoring=args.scoring, max_age_days=args.max_age_days, allow_stale=args.allow_stale
            )
        except borischen_mod.StaleTiers as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        except (OSError, http.client.HTTPException, ValueError) as exc:
            print(f"error: could not fetch {pos} tiers ({exc})", file=sys.stderr)
            return 1
        fetched[pos] = mapping
        if age is not None:
            ages.append(age)
        print(f"  {pos}: {len(mapping)} players, {max(mapping.values())} tiers"
              + (f", published {age:.0f}d ago" if age is not None else ""))

    data, unmatched = borischen_mod.apply(data, fetched)
    print(f"Re-tiered {len(data.players) - len(unmatched)} of {len(data.players)} players "
          f"into {len(data.tiers)} tiers")
    for name in unmatched[: args.limit]:
        print(f"  no published tier: {name}")
    if len(unmatched) > args.limit:
        print(f"  ... and {len(unmatched) - args.limit} more")

    out = args.out or args.data or _packaged_data_path()
    board_mod.save(data, out)
    print(f"Wrote {out}")
    return 0


def cmd_variant(args: argparse.Namespace) -> int:
    """Write a board re-ranked for another scoring format."""
    data = board_mod.load(args.data)
    try:
        built, notes = variant_mod.build(
            data, args.scoring, teams=args.teams, budget=args.budget, roster_size=args.roster_size
        )
    except (OSError, http.client.HTTPException, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for note in notes:
        print("  " + note)
    top = built.players[:5]
    print("\nTop of the board:")
    for p in top:
        money = f" ${p.value}" if p.value else ""
        print(f"  {p.rank:>2}  {p.name} ({p.pos} {p.team}) ADP {p.adp}{money}")
    out = args.out or f"board-{args.scoring}.json"
    board_mod.save(built, out)
    print(f"\nWrote {out} ({len(built.players)} players, {len(built.tiers)} tiers)")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Report every structural problem in a dataset, not just the first."""
    target = args.path or args.data or _packaged_data_path()
    problems = schema_mod.check_file(target)
    if not problems:
        try:
            data = board_mod.load(target)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            print(f"{target}: {exc}", file=sys.stderr)
            return 1
        print(f"{target}: valid — {len(data.players)} players in {len(data.tiers)} tiers")
        return 0
    print(f"{target}: {len(problems)} problem{'s' if len(problems) != 1 else ''}", file=sys.stderr)
    for line in problems:
        print(f"  {line}", file=sys.stderr)
    return 1


def _packaged_data_path() -> Path:
    from importlib import resources

    return Path(str(resources.files("fantasyleague").joinpath("data/players_2026.json")))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fantasyleague",
        description="Draft-day board for Yahoo half-PPR fantasy football.",
    )
    p.add_argument("--version", action="version", version=f"FantasyLeagueFootball v{__version__}")
    p.add_argument("--data", metavar="PATH", help="alternate dataset JSON (default: packaged board)")

    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="render the HTML draft board")
    b.add_argument("-o", "--out", default=DEFAULT_OUT, help=f"output path (default: {DEFAULT_OUT})")
    b.add_argument("--title", help="override the page title")
    b.add_argument(
        "--league",
        help="league name; shown in the header and keeps this board's saved picks separate "
        "from other boards in the same browser",
    )
    b.add_argument("--teams", type=_team_count, default=draft_mod.DEFAULT_TEAMS, help="league size (default 12)")
    b.add_argument("--slot", type=_positive, help="your draft slot, 1-based; enables pick odds on the board")
    b.add_argument("--open", action="store_true", help="open the board in a browser when done")
    b.set_defaults(func=cmd_build)

    ls = sub.add_parser("list", help="print the board as a table")
    ls.add_argument("--pos", choices=["QB", "RB", "WR", "TE", "K", "DST", "ALL"], help="filter by position")
    ls.add_argument("--flag", choices=["value", "avoid", "watch"], help="filter by flag")
    ls.add_argument("--limit", type=_positive, default=100, help="max rows (default: 100)")
    ls.set_defaults(func=cmd_list)

    v = sub.add_parser("values", help="show values, reaches, and the do-not-draft list")
    v.set_defaults(func=cmd_values)

    n = sub.add_parser("next", help="best available given who is already gone")
    n.add_argument(
        "--drafted",
        nargs="*",
        metavar="PLAYER",
        help="players already off the board — ranks or names (case-insensitive, partial OK; "
        "ambiguous names are rejected with the candidates listed)",
    )
    n.add_argument("--pos", choices=["QB", "RB", "WR", "TE", "K", "DST", "ALL"], help="limit to one position")
    n.add_argument("--limit", type=_positive, default=10, help="how many to show (default: 10)")
    n.add_argument("--teams", type=_team_count, default=draft_mod.DEFAULT_TEAMS, help="league size (default 12)")
    n.add_argument("--slot", type=_positive, help="your draft slot, 1-based; adds survival odds at your next picks")
    n.add_argument("--pick", type=_positive, help="current overall pick (default: number drafted + 1)")
    n.set_defaults(func=cmd_next)

    sv = sub.add_parser("serve", help="serve the board over HTTP so every tab (and phone) stays in sync")
    sv.add_argument(
        "--host", default="127.0.0.1", help="bind address; use 0.0.0.0 to reach it from a phone on the LAN"
    )
    sv.add_argument("--port", type=int, default=8765, help="port (default 8765; 0 picks a free one)")
    sv.add_argument("--title", help="override the page title")
    sv.add_argument("--league", help="league name (see build --league)")
    sv.add_argument("--teams", type=_team_count, default=draft_mod.DEFAULT_TEAMS, help="league size (default 12)")
    sv.add_argument("--slot", type=_positive, help="your draft slot, 1-based")
    sv.add_argument(
        "--sleeper", metavar="DRAFT_ID", help="follow this Sleeper draft and cross picks off live"
    )
    sv.add_argument(
        "--every", type=_interval, default=sleeper_mod.DEFAULT_INTERVAL,
        help=f"seconds between Sleeper polls (default {sleeper_mod.DEFAULT_INTERVAL:g})",
    )
    sv.add_argument("--open", action="store_true", help="open the board in a browser when up")
    sv.set_defaults(func=cmd_serve)

    rf = sub.add_parser("refresh", help="pull current ADP, projections, auction values, injuries and trending")
    rf.add_argument("-o", "--out", help="write here instead of updating the dataset in place")
    rf.add_argument("--force", action="store_true", help="ignore the 24h cache and re-download")
    rf.add_argument("--hours", type=_positive, default=24, help="trending look-back window (default 24)")
    rf.add_argument("--trending-limit", type=_positive, default=10, help="how many trending adds to keep")
    rf.add_argument("--no-trending", action="store_true", help="only refresh injuries")
    rf.add_argument("--limit", type=int, default=15, help="how many injury lines to print")
    rf.add_argument("--no-adp", action="store_true", help="skip the ADP refresh")
    rf.add_argument(
        "--adp-format", default="half-ppr", choices=list(adp_mod.FORMATS), help="ADP scoring format"
    )
    rf.add_argument("--teams", type=_team_count, default=draft_mod.DEFAULT_TEAMS, help="ADP league size (default 12)")
    rf.add_argument(
        "--reflag",
        action="store_true",
        help="recompute value/reach from ADP vs this board's rank (skill positions only); "
        "off by default so curated flags are not silently replaced",
    )
    rf.add_argument("--no-projections", action="store_true", help="skip projections and auction values")
    rf.add_argument("--budget", type=_positive, default=200, help="auction budget per team (default 200)")
    rf.add_argument(
        "--roster-size", type=_positive, default=DEFAULT_ROSTER_SIZE, help="roster size for auction maths"
    )
    rf.set_defaults(func=cmd_refresh)

    ti = sub.add_parser("tiers", help="re-tier the board from Boris Chen's published tiers")
    ti.add_argument("-o", "--out", help="write here instead of updating the dataset in place")
    ti.add_argument(
        "--scoring", default="half", choices=list(borischen_mod.FILES), help="scoring format"
    )
    ti.add_argument(
        "--max-age-days", type=float, default=borischen_mod.MAX_AGE_DAYS,
        help=f"refuse files older than this (default {borischen_mod.MAX_AGE_DAYS:g})",
    )
    ti.add_argument(
        "--allow-stale", action="store_true",
        help="import even if the published files are older than --max-age-days",
    )
    ti.add_argument("--limit", type=int, default=10, help="how many unmatched names to print")
    ti.set_defaults(func=cmd_tiers)

    vr = sub.add_parser("variant", help="build a board for another scoring format")
    vr.add_argument(
        "scoring", choices=list(variant_mod.VARIANTS), help="target format (2qb is superflex)"
    )
    vr.add_argument("-o", "--out", help="output path (default: board-<scoring>.json)")
    vr.add_argument("--teams", type=_team_count, default=draft_mod.DEFAULT_TEAMS, help="league size")
    vr.add_argument("--budget", type=_positive, default=200, help="auction budget per team")
    vr.add_argument("--roster-size", type=_positive, default=15, help="roster size for pricing")
    vr.set_defaults(func=cmd_variant)

    va = sub.add_parser("validate", help="check a dataset and report every problem found")
    va.add_argument("path", nargs="?", help="dataset to check (default: --data, else the packaged board)")
    va.set_defaults(func=cmd_validate)

    ex = sub.add_parser("export", help="write the board as CSV (stdout by default)")
    ex.add_argument("-o", "--out", help="write to this file instead of stdout")
    ex.add_argument("--pos", choices=["QB", "RB", "WR", "TE", "K", "DST", "ALL"], help="filter by position")
    ex.add_argument("--flag", choices=["value", "avoid", "watch"], help="filter by flag")
    ex.add_argument("--limit", type=_positive, default=1000, help="max rows")
    ex.set_defaults(func=cmd_export)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
