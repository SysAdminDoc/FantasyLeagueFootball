"""Command line interface for the draft board."""

from __future__ import annotations

import argparse
import sys
import time
import webbrowser
from pathlib import Path

from . import __version__
from . import board as board_mod
from . import draft as draft_mod
from . import render as render_mod
from . import serve as serve_mod
from .sync import sleeper as sleeper_mod

DEFAULT_OUT = Path("dist/draft-board.html")

_COLS = "{:>4}  {:<24} {:<3} {:<4} {:>4}  {}"


def _print_players(players, header: str) -> None:
    print(f"\n{header}\n")
    print(_COLS.format("RANK", "PLAYER", "POS", "TEAM", "TIER", "FLAG"))
    print("-" * 62)
    for p in players:
        print(_COLS.format(p.rank, p.name, p.pos, p.team, p.tier, (p.flag or "").upper()))
    print()


def _print_odds(players, teams: int, slot: int, current: int) -> None:
    """Best available with survival odds at your next two picks."""
    mine = draft_mod.next_picks(teams, slot, current, count=3)
    if not mine:
        print(f"\nPick {current} · no picks left for slot {slot} in a {teams}-team draft\n")
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
            probs = [draft_mod.availability(p.adp, p.adp_sd, k) for k in mine[:2]]
            odds = "  ".join(f"{pr:>6.0%}" for pr in probs)
            call = draft_mod.band(probs[0])
        adp = f"{p.adp:>5.1f}" if p.adp is not None else "    -"
        print(f"{p.rank:>4}  {p.name:<24} {p.pos:<3} {p.team:<4} {adp}  {odds}  {call}")
    print()


def cmd_build(args: argparse.Namespace) -> int:
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
    label = " ".join(filter(None, [args.pos or "All", args.flag or "", "players"]))
    _print_players(players[: args.limit], label.strip().capitalize())
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
    data = board_mod.load(args.data)
    drafted = board_mod.resolve_many(data, args.drafted or [])
    avail = board_mod.best_available(data, pos=args.pos, drafted=drafted, limit=args.limit)

    if args.slot:
        _print_odds(avail, args.teams, args.slot, args.pick or len(drafted) + 1)
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
    data = board_mod.load(args.data)
    server = serve_mod.BoardServer(
        data, host=args.host, port=args.port, title=args.title, league=args.league,
        teams=args.teams, slot=args.slot,
    )
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
    b.add_argument("--teams", type=int, default=draft_mod.DEFAULT_TEAMS, help="league size (default 12)")
    b.add_argument("--slot", type=int, help="your draft slot, 1-based; enables pick odds on the board")
    b.add_argument("--open", action="store_true", help="open the board in a browser when done")
    b.set_defaults(func=cmd_build)

    ls = sub.add_parser("list", help="print the board as a table")
    ls.add_argument("--pos", choices=["QB", "RB", "WR", "TE", "K", "DST", "ALL"], help="filter by position")
    ls.add_argument("--flag", choices=["value", "avoid", "watch"], help="filter by flag")
    ls.add_argument("--limit", type=int, default=100, help="max rows (default: 100)")
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
    n.add_argument("--limit", type=int, default=10, help="how many to show (default: 10)")
    n.add_argument("--teams", type=int, default=draft_mod.DEFAULT_TEAMS, help="league size (default 12)")
    n.add_argument("--slot", type=int, help="your draft slot, 1-based; adds survival odds at your next picks")
    n.add_argument("--pick", type=int, help="current overall pick (default: number drafted + 1)")
    n.set_defaults(func=cmd_next)

    sv = sub.add_parser("serve", help="serve the board over HTTP so every tab (and phone) stays in sync")
    sv.add_argument(
        "--host", default="127.0.0.1", help="bind address; use 0.0.0.0 to reach it from a phone on the LAN"
    )
    sv.add_argument("--port", type=int, default=8765, help="port (default 8765; 0 picks a free one)")
    sv.add_argument("--title", help="override the page title")
    sv.add_argument("--league", help="league name (see build --league)")
    sv.add_argument("--teams", type=int, default=draft_mod.DEFAULT_TEAMS, help="league size (default 12)")
    sv.add_argument("--slot", type=int, help="your draft slot, 1-based")
    sv.add_argument(
        "--sleeper", metavar="DRAFT_ID", help="follow this Sleeper draft and cross picks off live"
    )
    sv.add_argument(
        "--every", type=float, default=sleeper_mod.DEFAULT_INTERVAL,
        help=f"seconds between Sleeper polls (default {sleeper_mod.DEFAULT_INTERVAL:g})",
    )
    sv.add_argument("--open", action="store_true", help="open the board in a browser when up")
    sv.set_defaults(func=cmd_serve)

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
