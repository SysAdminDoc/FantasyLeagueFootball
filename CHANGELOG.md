# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- A.J. Brown listed as PHI; he was traded to New England this offseason (caught by the
  Sleeper crosswalk — the only team mismatch across all 99 players).
- Tier-break badge is computed from the whole tier, not the rows left visible by a
  position filter or search — filtering to TE no longer marks a full tier "Tier break".
  When a filter hides part of a tier the header now reads e.g. `1 left · 7 in tier`.

### Added
- Undo. Every cross-off, restore, and Reset shows a toast with an Undo button for six
  seconds — the safety net in place of confirmation dialogs. Reset undo restores the whole
  log and persists.
- Positional-run detection: when 3 of the last 4 picks share a position, that position's
  filter chip is marked "run" and the live region says so once.
- Picks are stored as an ordered log (`{v: 2, log: [{rank, ts}]}`); the previous bare
  rank array is migrated in place on first load.
- Print stylesheet + a "Print sheet" button: Ctrl+P (or the button) produces a one-page,
  three-column, monochrome cheat sheet — all 99 players with tier rules, flags as glyphs
  (▲ value, ▼ reach, ! watch), crossed-off players struck through, rails and controls dropped.
- Kickers and defenses: 12 K (tier 8, FantasyPros tiers 2026-08-14) and 12 D/ST (tier 9,
  FantasyLife rankings 2026-08-08) at ranks 76–99, with K and DST position chips and CLI
  filters. Yahoo's default lineup starts one of each; the board can finally represent it.
- Every player carries external ids (`ids.sleeper`, `ids.yahoo`, `ids.espn`) resolved from
  Sleeper's public player database, so sync and refresh can join on ids rather than names.
  `board.by_external_id()` exposes the map; validation rejects duplicate ids.
- `next --drafted` accepts player names as well as ranks (`--drafted gibbs bijan "ja'marr" 4`).
  Matching is case-insensitive — exact name, then unique prefix, then unique substring —
  and an ambiguous name is rejected with the candidates listed.
- `build --league NAME` — shows the league in the header and page title, and keeps that
  board's saved picks separate from any other board opened in the same browser.
- A visible notice in the controls bar when the browser refuses to persist picks
  (e.g. storage disabled), instead of failing silently.

### Accessibility
- A polite live region announces cross-offs, restores, the new best available, position
  filter changes, resets, and the moment a tier breaks (once per tier), so screen-reader
  users hear what sighted users see change (WCAG 2.1 SC 4.1.3).
- Pressing Enter in the search box crosses off (or restores) the single visible match and
  clears the search; ambiguous or empty matches are announced instead. The placeholder now
  says what the box does: "Search, then Enter to cross off".

### Security
- `--title`, the dataset `updated`/`season` fields, and every text field rendered by
  the page are HTML-escaped; a `Content-Security-Policy` meta tag (`default-src 'none'`,
  inline script/style only, `img-src data:`) is emitted in every build.
- Plan guidance no longer accepts raw HTML. Emphasis is written as `**bold**` and
  promoted after escaping, so an untrusted `--data` file cannot inject markup.

### Changed
- Browser storage is keyed by board identity (season + ordered roster + league) rather than
  season alone. Saved picks survive note edits and are dropped when the board is re-ranked,
  which is the only case where rank-based state would be wrong.
- The tier-break threshold is injected into the page payload from `board.TIER_BREAK_THRESHOLD`;
  `board.js` no longer carries its own copy.

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
