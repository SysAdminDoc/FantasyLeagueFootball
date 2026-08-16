# Roadmap

Single task tracker for FantasyLeageFootball. Drain top to bottom.

## Next

- [ ] **Live ADP fetch** — `fantasyleague refresh` pulling current ADP from FantasyPros /
      Sleeper rather than hand-maintaining the JSON. Cache to disk; fall back to packaged
      data when offline.
- [ ] **Auto-flag values** — derive `value` / `avoid` from `adp - projected_rank` instead of
      hardcoding the flag. Needs a `projected` field on each player.
- [ ] **Roster tracking** — mark which crossed-off players *you* took, then show positional
      needs against a configurable lineup (1QB/2RB/2WR/1TE/1FLEX by default).
- [ ] **Board expansion to 200** — currently 75 deep, which runs out around round 7 in a
      12-team league. Late rounds are where the sleeper rail is doing the work instead.

## Later

- [ ] Superflex and full-PPR variants of the ranking set.
- [ ] Bye-week column, and a warning when a roster stacks too many byes in one week.
- [ ] Keeper/dynasty mode — age and contract context on each row.
- [ ] Export a drafted roster to CSV.
- [ ] Auction values as an alternate mode ($200 budget default).

## Done

- [x] Packaged 2026 half-PPR dataset with tiers, flags, and rails — v0.0.1
- [x] Self-contained HTML render with click-to-cross-off and tier-break warnings — v0.0.1
- [x] CLI: `build`, `list`, `values`, `next` — v0.0.1
- [x] Dataset validation + 36 tests — v0.0.1
