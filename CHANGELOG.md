# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- The phone layout leads with the readouts you use on the clock. Collapsing to one
  column used to append the rail *after* the board, putting Best available, your
  roster and the managers picking ahead of you some 15,000 px down; they now sit
  directly under the controls, and the four-cell strategy strip became one
  swipeable row rather than a full screen of reading.
- The rail no longer hides its own heading behind the sticky controls bar (which
  wraps to two rows at some widths), and it scrolls inside itself, so the injury
  board and late-round targets are reachable without scrolling past 200 rows.

### Fixed
- Network failures are caught as a family rather than by name. `RemoteDisconnected`
  is a `ConnectionResetError` and `IncompleteRead` is an `HTTPException`, so neither
  was a `URLError`: a server closing a keep-alive mid-draft killed the Sleeper
  polling thread while the CLI still said it was following the draft, and a cut
  15 MB download skipped the stale-cache fallback. The poll loop also survives an
  unexpected error now instead of ending the session.
- A truncated player cache (Ctrl+C during the 15 MB write) is detected and
  re-downloaded instead of failing every later `refresh` with a JSON error that
  reads like the network is down. The cache is written through a temp file.
- `fantasyleague tiers` rebuilds every tier from the import instead of reusing the
  packaged names, which used to leave a block of quarterbacks labelled "The
  anchors · No wrong answer. Take the board." and dropped players with no
  published tier into whichever imported block inherited their old number; they
  now sit in one clearly named trailing tier.
- Roster claims survive live mode. Ownership is now server state, so a reload or a
  dropped SSE connection no longer empties "Your roster" — and a pick that arrives
  from Sleeper can be claimed as yours, which was impossible before: the automatic
  claim only ever ran for picks made in the browser, so the roster card, lineup
  needs and bye warnings were dead whenever `serve --sleeper` was doing the work.
- Picks of players this board doesn't rank are counted instead of dropped. A deep
  bench pick used to leave the pick counter, "your pick" detection, the survival
  odds and opponent attribution one behind for the rest of the draft. Sleeper
  records them automatically; an **Off-board pick** button does the same by hand.
- A pick undone in Sleeper is undone on the board. Previously the pick stuck, and
  because its number was remembered the replacement pick was ignored for good.
- Snake maths follows board depth instead of assuming 16 rounds. A 10-team league
  drafts 20 rounds off a 200-player board and used to be told "no picks left" from
  pick 161 — with odds and auto-claim switching off — in exactly the late rounds
  the 200-player board was built for.

### Security
- No raw angle bracket reaches the page's inline data any more. Escaping only
  `</` left `<!--<script>` in any dataset string able to blank the whole board:
  it drives the HTML tokenizer into a state where the real closing tag stops
  closing the script, so nothing rendered — from one note, with no error.
- A source URL is only turned into a link when it is `http(s)`. `javascript:` in a
  shared board JSON ran on click; it now shows as plain text, `validate` rejects
  it, and real sources open in a new tab so a mis-click mid-draft cannot navigate
  the board away.
- `POST /state` refuses cross-origin writes. A JSON content type is now required
  (which forces a browser into a CORS preflight that cannot succeed, since no
  `Access-Control-Allow-*` header is ever sent), an `Origin` that disagrees with
  `Host` is rejected, and `Host` must name an address rather than a domain, which
  blunts DNS rebinding. Anything that can reach the socket directly — curl, a sync
  script, a phone on the LAN — still drives the board as before. `X-Source` is
  restricted to a short token instead of being stored verbatim.

### Fixed
- Running `serve` twice on the same port used to bind silently on Windows: the
  second board reported success and never received a request. The port is now
  exclusive there, and the CLI says which port is busy and suggests `--port 0`.
- Malformed live-sync requests return a JSON 400 instead of killing the request
  thread with a traceback and dropping the connection: `{"undo": null}`,
  `{"pick": "gibbs"}`, a non-object body, and an unparseable `Content-Length` are
  all handled, bodies over 64 KB get a 413, and a closed tab no longer prints a
  `ConnectionAbortedError` traceback over the pick log mid-draft.

## [0.1.0] — 2026-08-16

### Fixed
- A dataset saved by Notepad or PowerShell's `Out-File` (both write a UTF-8 BOM) failed to
  load with "Unexpected UTF-8 BOM"; datasets are now read as `utf-8-sig`.
- `validate` accepted a dataset with no rails but `load` then raised `KeyError` on them —
  the rails are genuinely optional now, so anything that validates will open.
- The position-chip row overflowed a 390px phone once K and DST were added, scrolling the
  whole page sideways; chips now wrap.
- Injury board rebuilt from live data: the hand-typed list is replaced by 11 current
  designations, and stale `watch` flags on healthy players are cleared.
- A.J. Brown listed as PHI; he was traded to New England this offseason (caught by the
  Sleeper crosswalk — the only team mismatch across all 99 players).
- Tier-break badge is computed from the whole tier, not the rows left visible by a
  position filter or search — filtering to TE no longer marks a full tier "Tier break".
  When a filter hides part of a tier the header now reads e.g. `1 left · 7 in tier`.

### Added
- **Live value** toggle: value over replacement recomputed against the players actually
  left, so a thinning position lifts everyone still on the board at it. Rows resort inside
  their tier and show `+104`; a static rank cannot tell you every startable tight end is gone.
- **Before your next pick**: the managers picking between now and your turn, each with the
  lineup slots they still need. A position two or more of them need is marked in red — that
  is the run that will not wait for you.
- Keeper/dynasty context. `refresh` attaches age and completed seasons from Sleeper
  (188 of 200 players), and a **Keeper info** toggle puts age on every row — green below
  the position's age cliff, amber at or past it (RB 27, WR 29, TE 30, QB 34), with the
  full "29 years old · 7 seasons" on hover. The setting persists with your draft settings.
- CSV export gains `projected`, `value`, `age` and `exp` columns.
- `fantasyleague variant {half-ppr,ppr,standard,2qb,dynasty}` builds a board for another
  scoring format: re-ranked by that format's own ADP, re-projected in its scoring, re-tiered,
  and re-priced. Superflex prices off a two-QB lineup — with QB24 as replacement instead of
  QB12, Josh Allen goes from $24 to $38 and opens the board, which is the entire point of
  the format.
- Projections and auction values. `refresh` pulls Sleeper's public season projections
  (`pts_half_ppr`/`pts_ppr`/`pts_std`, matched for 199 of 200 players) and prices every
  draftable player against a $200 budget; the dollar value shows on each row and the
  footer states the budget, roster size and league size the numbers assume.
  `--budget`/`--roster-size`/`--no-projections` control it.
- `fantasyleague tiers` imports Boris Chen's published consensus tiers (Gaussian-mixture
  clustering over FantasyPros ECR), mapping each position into its own tier block so a
  "tier 1 RB" and a "tier 1 WR" stay distinct. **It refuses files older than 14 days**:
  as of 2026-08-16 every published file is still from 2025-12-26, and re-tiering a 2026
  board with last season's opinions would be worse than not importing at all.
  `--allow-stale` overrides; a test acts as a canary for when the 2026 files land.
- Board expanded from 99 to **200 players**. Ranks 1–75 stay curated; 76–176 are filled in
  live half-PPR ADP order from Fantasy Football Calculator (all 101 matched to Sleeper ids),
  in three new tiers — Rounds 7–9, Rounds 10–12, Last rounds. Kickers and defenses move to
  tiers 11 and 12. A 12-team draft no longer runs off the end of the board in round 7.
- `fantasyleague export` writes the board as CSV to stdout or a file, with the same
  `--pos`/`--flag` filters as `list`. On the board, "Copy as CSV" puts your drafted roster
  on the clipboard (lineup slot, rank, name, pos, team, bye, ADP), falling back to a
  copyable prompt where the clipboard API is unavailable.
- `refresh` now also pulls ADP, its spread, and bye weeks from Fantasy Football Calculator's
  public API (`--adp-format`, `--teams`, `--no-adp`), storing the sample's provenance.
  `--reflag` recomputes value/reach from the gap between this board's rank and the market —
  off by default, because the packaged flags compare ADP with a *projected finish*, which is
  a stronger signal than rank, and K/DST are excluded entirely since their board ranks are
  positional rather than overall.
- Roster tracking. When the board knows your slot, a cross-off on your pick is claimed for
  you automatically; otherwise the toast offers "That's mine" (and "Not mine" to undo it).
  A rail card fills Yahoo's default lineup — QB/RB/RB/WR/WR/TE/FLEX/K/DEF then bench, with
  dedicated slots filled before FLEX so a spare RB doesn't eat the flex — lists what the
  lineup still needs, and your picks are marked on the board.
- Bye weeks on every row, and a warning when three or more of *your* players share one.
  Rows show a highlighted bye when two or more of your roster are already out that week.
- `fantasyleague validate [PATH]` — reports **every** structural problem in a dataset at once,
  each with a JSON pointer (`/players/12/pos: 'P' is not one of QB, RB, WR, TE, K, DST`), and
  exits non-zero. Datasets carry `schema_version`; a file from a newer build is refused with
  an explanation rather than failing somewhere deep in the load.
- `fantasyleague refresh` — rebuilds the injury board from Sleeper's player database
  (`injury_status`, body part, practice participation) and adds a **Trending adds · 24h** rail.
  The 15 MB player file is cached for 24 hours in the platform cache dir (`FANTASYLEAGUE_CACHE`
  overrides); offline it falls back to a stale cache and says so. The refresh sets and clears
  the `watch` flag only — `value` and `avoid` are price judgements and survive it. The board
  footer carries an "as of" stamp.
- Sleeper live draft sync: `serve --sleeper DRAFT_ID` polls Sleeper's public API (no key, no
  browser extension) and crosses each pick off in every open tab. Picks join on `ids.sleeper`,
  polling is idempotent, unknown players are logged once rather than every tick, and network
  failures are logged and retried rather than ending the session.
- `fantasyleague serve` — serves the board over HTTP with Server-Sent Events. Every open
  tab shows the same crossed-off state, `--host 0.0.0.0` makes it reachable from a phone on
  the LAN (the URL is printed), the served page keeps the screen awake via the Screen Wake
  Lock API, and a live indicator shows the connection. `POST /state` accepts
  `{"pick": {"rank": N}}` / `{"pick": {"name": "gibbs"}}` / `{"undo": N}` / `{"reset": true}`,
  which is how sync sources will feed picks in without a browser extension. Standard library
  only.
- Snake-draft awareness. Set **Teams** and **Your slot** on the board (or `build --teams N
  --slot S`) and the controls bar shows the pick on the clock and how far away yours is;
  every undrafted player shows the odds of still being there at your next two picks,
  colour-banded wait / toss-up / now. `next --slot S [--teams N] [--pick P]` prints the
  same table in the terminal. Odds use `1 − Φ((pick − ADP)/σ)` with the market's own
  spread when known (else ADP/4) — the method DraftKick published.
- ADP, ADP spread, and bye weeks on 97 of 99 players from Fantasy Football Calculator's
  half-PPR 12-team ADP (2,364 drafts, 2026-08-11 → 2026-08-16); provenance is stored in the
  dataset and printed in the page footer.
- CLI smoke tests (`tests/test_cli.py`) — `cli.py` was previously never imported by the suite.
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
- The printed cheat sheet is now one double-sided sheet rather than one page — 200 players
  at a size you can actually read across a table.
- Touch pass: on coarse pointers rows are at least 44px tall, chips/buttons/inputs grow,
  inputs use 16px so iOS stops zooming on focus, the toast spans the screen, and the
  controls bar stops being sticky (it ate too much of a phone screen).
- Linting moves to ruff >= 0.16 (413 default rules, up from 59) plus RUF/PTH/C4/PIE/RET.
  Fixed what it surfaced: a no-op dict copy in `board.save`, `__enter__` annotated with the
  concrete class instead of `Self`, `re.S` aliases, single-element slices, and list
  concatenation where unpacking reads better.
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

[0.1.0]: https://github.com/SysAdminDoc/FantasyLeagueFootball/releases/tag/v0.1.0
[0.0.1]: https://github.com/SysAdminDoc/FantasyLeagueFootball/releases/tag/v0.0.1
