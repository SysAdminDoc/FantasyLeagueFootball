# FantasyLeagueFootball

[![Version](https://img.shields.io/badge/version-0.0.1-E8963C.svg)](CHANGELOG.md)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](pyproject.toml)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#install)

A draft-day board for **Yahoo half-PPR fantasy football**. Renders a single self-contained
HTML page you keep open on a second screen while you draft — click players off as they go,
watch tiers drain, and get warned when a tier is about to break.

Ships with a ranked 2026 board (75 players, 7 tiers) plus value/reach flags, a do-not-draft
list, a live injury board, and late-round targets.

## Why

Rankings sites give you a list. A list doesn't tell you *when to reach*. This board tracks
which tier is about to empty out, because that's the only question that actually matters at
the turn: take the guy now, or gamble he's there in eleven picks.

## Install

```bash
git clone https://github.com/SysAdminDoc/FantasyLeagueFootball
cd FantasyLeagueFootball
pip install -e .
```

No runtime dependencies. Python 3.11 or newer.

## Use

Build the board and open it:

```bash
fantasyleague build --open
```

Writes `dist/draft-board.html` — one file, no network calls, works offline. Your crossed-off
players persist in browser storage, so a refresh mid-draft won't lose your place.

### Other commands

```bash
fantasyleague list --pos RB              # the board as a table, RBs only
fantasyleague list --flag value          # everyone flagged as a value
fantasyleague values                     # values, reaches, and do-not-draft
fantasyleague next --drafted 1 2 3 5 8   # best available given who's gone
fantasyleague next --pos WR --limit 5    # best available at one position
```

`next` also prints any tier down to its last two players, and a remaining-by-position count.

### Custom data

```bash
fantasyleague --data my-league.json build -o dist/my-board.html
```

Any JSON matching [`players_2026.json`](src/fantasyleague/data/players_2026.json) works.
The loader validates it on the way in: ranks must run `1..N` with no gaps or duplicates,
names must be unique, and every player's `tier` must match a defined tier. A bad dataset
fails loudly rather than rendering a board with holes in it.

## Board format

| Field | Meaning |
|---|---|
| `flag: "value"` | Yahoo ADP is later than the projected finish — buy |
| `flag: "avoid"` | ADP is earlier than the projection — let someone else take him |
| `flag: "watch"` | Healthy enough to draft, but there's an active injury note |
| `tier` | Players inside a tier are close enough to be interchangeable |

Tiers are the point. Within a tier, take whoever you like. Between tiers is where value
actually falls off, and that's what the **Tier break** warning is watching for.

## Development

```bash
pip install -e ".[dev]"
pytest          # 36 tests
ruff check .
```

Tests cover dataset integrity (contiguous ranks, no orphan tiers, no duplicate names),
the draft-time queries, and the render — including that the output is fully self-contained,
defines both light and dark themes, and can't be broken by a `</script>` in the data.

There's no browser in the test environment, so a separate suite checks the DOM contract
instead: every ID `board.js` queries must exist in the template, every flag must have a
label, every severity must have a style, and the tier-break threshold must match between
the JS and the Python. Those are the drifts that would silently render a blank board.

## Data sources

Ranks follow the Rotoworld/NBC Sports top-200 consensus. Value and reach flags compare Yahoo
ADP against Yahoo's own projected finish. The injury board is compiled from Yahoo's training
camp report, current through **August 16, 2026**.

Fantasy data goes stale fast. Re-check the injury board before you draft.

## License

MIT — see [LICENSE](LICENSE).
