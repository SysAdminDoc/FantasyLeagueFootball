# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.1] — 2026-08-16

Initial release.

### Added
- Packaged 2026 half-PPR draft board: 75 ranked players across 7 tiers, built from the
  Rotoworld/NBC Sports top-200 consensus.
- Value / reach flags comparing Yahoo ADP against Yahoo's projected finish.
- Rails: do-not-draft list, training-camp injury board, late-round targets, positional plan.
- `fantasyleague build` — renders a self-contained HTML board with no external requests.
  Click-to-cross-off with browser-storage persistence, position filters, name search,
  live best-available, and tier-break warnings at two players remaining.
- `fantasyleague list` / `values` / `next` — the same board from the terminal.
- Dataset validation: contiguous ranks, unique names, no orphan tiers, known positions
  and flags. Bad data fails at load rather than rendering a broken board.
- Dark-first theme with a mirrored light palette; both `prefers-color-scheme` and an
  explicit `data-theme` stamp resolve correctly.
- 42 tests covering data integrity, draft-time queries, render self-containment, the
  template/script DOM contract, and a real-browser smoke suite (Playwright; skipped when
  unavailable).
- README screenshots (dark, light, mid-draft) captured from the rendered board.
- Published to GitHub: https://github.com/SysAdminDoc/FantasyLeagueFootball

[0.0.1]: https://github.com/SysAdminDoc/FantasyLeagueFootball/releases/tag/v0.0.1
