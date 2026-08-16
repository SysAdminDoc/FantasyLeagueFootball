# Roadmap

Single task tracker for FantasyLeagueFootball. Drain top to bottom. Shipped work lives in CHANGELOG.md.

## Next

- [ ] **Live ADP fetch** — `fantasyleague refresh` pulling current ADP rather than hand-maintaining
      the JSON. Cache to disk; fall back to packaged data when offline.
      *Research 2026-08-16:* Sleeper's public API exposes **no ADP** (verified against `players/nfl`
      and docs); real sources are FantasyPros API v2 (`adp`, paid key), Yahoo `players;sort=AR` /
      `draft_analysis.average_pick` (OAuth), or scraping. Design the provider interface around that.
- [ ] **Auto-flag values** — derive `value` / `avoid` from `adp - projected_rank` instead of
      hardcoding the flag. Needs a `projected` field on each player.
      *Research 2026-08-16:* FantasyPros ECR supplies `rank_ecr` + `adp` in one call; BeatADP shows
      "your rank vs ADP" as a first-class column — surface the delta, not just the flag.
- [ ] **Roster tracking** — mark which crossed-off players *you* took, then show positional
      needs against a configurable lineup (1QB/2RB/2WR/1TE/1FLEX by default).
      *Research 2026-08-16:* Yahoo's default lineup is 1QB/2RB/2WR/1TE/1FLEX/**1K/1DEF**/6BN/2IR
      (help.yahoo.com SLN22673) — land the K/DST positions item below first or needs math is wrong.
- [ ] **Board expansion to 200** — currently 75 deep, which runs out around round 7 in a
      12-team league. Late rounds are where the sleeper rail is doing the work instead.
      *Research 2026-08-16:* Boris Chen and FantasyPros both publish top-200; Sleeper `search_rank`
      is a free ordering proxy for depth once the ID crosswalk exists.

## Later

- [ ] Superflex and full-PPR variants of the ranking set.
- [ ] Bye-week column, and a warning when a roster stacks too many byes in one week.
      *Research 2026-08-16:* Sleeper `players/nfl` has **no bye field** (verified). Sources: nflverse
      `import_schedules(2026)` (byes run W5–W14 in 2026), Yahoo player `bye_weeks`, FantasyPros
      `player_bye_week`.
- [ ] Keeper/dynasty mode — age and contract context on each row.
      *Research 2026-08-16:* Sleeper picks carry `is_keeper`; Sleeper players carry `age`, `years_exp`.
- [ ] Export a drafted roster to CSV.
      *Research 2026-08-16:* BeatADP sells CSV export "for printing physical cheat sheets" — pair
      with the print stylesheet item below.
- [ ] Auction values as an alternate mode ($200 budget default).
      *Research 2026-08-16:* standard method is VBD points-over-replacement → share of total budget
      (FantasyPros calculator; DraftExpert publishes Fair/Target/Max). Yahoo `settings.is_auction_draft`
      and `draft_analysis.average_cost` exist for later sync.

## Research-Driven Additions

Added 2026-08-16 from RESEARCH.md. Ordered P0 → P3; within a tier: root-cause fixes, then trust/reliability, then quick wins, then larger bets.

### P1

- [ ] P1 — `fantasyleague serve`: local HTTP + Server-Sent Events
  Why: a stable `http://localhost` origin fixes storage, enables Screen Wake Lock, lets a phone on the LAN be the second screen, and is the transport for live sync without a browser extension.
  Evidence: MDN Screen Wake Lock (needs secure context; localhost qualifies; ≥94 % support); Draft Caddie/BeatADP/DraftKick are all desktop-only Chrome extensions — the gap is a phone-friendly board.
  Touches: new `src/fantasyleague/serve.py` (stdlib `http.server` + `ThreadingHTTPServer`, `/` serves the rendered board, `/events` SSE, `/state` JSON), `board.js` (`EventSource` when `DATA.live` is set; apply `pick` events; request `navigator.wakeLock` on load), `cli.py` (`serve --port 8765 --host 0.0.0.0`), tests (server round-trip with `http.client`).
  Acceptance: `fantasyleague serve` prints a URL; opening it on a phone shows the board; posting `{"pick":{"rank":3}}` to `/state` crosses Gibbs off in every open tab within 1 s; screen stays awake on a phone for 10 min idle.
  Complexity: M

- [ ] P1 — Sleeper live draft sync
  Why: zero-auth, public, read-only — the cheapest possible live cross-off; Sleeper itself has no custom-rankings import, so a following board is the community's standard workaround.
  Evidence: docs.sleeper.com `GET /v1/draft/{draft_id}/picks` (fields `player_id, picked_by, round, draft_slot, pick_no, metadata, is_keeper`); Draft Caddie polls every 3 s; rate guidance <1000 req/min; fantasyjoes.gg/sleeper-draft (no CSV import on Sleeper).
  Touches: new `src/fantasyleague/sync/sleeper.py` (poll picks, map `player_id` → board rank via `ids.sleeper`, emit to `serve` bus), `cli.py` (`sync sleeper --draft ID [--every 3]`), tests with recorded fixture JSON.
  Acceptance: against a live or mock Sleeper draft, each pick crosses the matching player off within one poll interval; unknown IDs are logged once, not repeatedly; the board's pick counter advances with `pick_no`.
  Complexity: M — depends on **serve** and **ID crosswalk**.

- [ ] P1 — Yahoo live draft sync (optional extra)
  Why: the author drafts on Yahoo; `draftresults` returns picks made so far when called mid-draft, so polling works without a Chrome extension.
  Evidence: yahoo-fantasy-api docs ("if called during the draft, includes players drafted thus far"); Yahoo API `league/{key}/draftresults`, `settings.roster_positions`, `is_auction_draft`; yfpy is GPL-3.0 (keep out of core), `yahoo-fantasy-api` alternative; derekrbreese MCP self-limits to 900 req/hr.
  Touches: `pyproject.toml` (`[project.optional-dependencies] yahoo = [...]`), new `sync/yahoo.py` (OAuth 2.0 token file under user config dir, `--league-key`, poll every 5 s, map `player_key` numeric id → `ids.yahoo`), `cli.py` (`sync yahoo --league-key … `), README (app-registration steps), tests with fixtures.
  Acceptance: after one-time browser auth, `sync yahoo` follows a Yahoo mock draft pick-by-pick into the served board; token refresh survives the 1-hour expiry mid-draft.
  Complexity: L — depends on **serve** and **ID crosswalk**.

- [ ] P1 — Injury/status refresh and Sleeper trending in the rails
  Why: the injury board is typed by hand and goes stale by kickoff; Sleeper publishes `injury_status`, `injury_body_part`, `injury_notes`, `practice_participation`, `news_updated` free of charge, and its trending-adds feed is a live late-round radar.
  Evidence: verified 2026-08-16 against `players/nfl?position=QB&active=true` and `/players/nfl/trending/add?lookback_hours=24` (top add: Darren Waller TE CAR, 53,190); docs say cache `players/nfl` daily.
  Touches: `sync/sleeper.py` (`fetch_players()` with 24 h on-disk cache in the user cache dir), `board.py` (`apply_injuries(data, sleeper_players)` → overrides `Injury` rows and sets `flag: watch` for Q/D/O), `render.py`/`board.js` ("Trending adds (24 h)" card), `cli.py` (`refresh --injuries --trending`), tests.
  Acceptance: `refresh` updates the injury card from live data with a "as of <timestamp>" line; offline it falls back to packaged data and says so; second run within 24 h makes no network call.
  Complexity: M — shares plumbing with **Live ADP fetch** (existing); Sleeper cannot supply ADP.

### P2

- [ ] P2 — `schema_version` + JSON Schema + `validate` subcommand for custom data
  Why: `--data` authors have no contract beyond "match the packaged file"; a future field rename has no migration path.
  Evidence: `models.py` `Dataset.from_dict` (no version key); README "Custom data" section.
  Touches: `data/players_2026.json` (`"schema_version": 1`), new `data/schema.json` (draft-07, stdlib-only validation via a small checker or optional `jsonschema` extra), `cli.py` (`validate PATH`), `board.py` (`load()` warns on older versions and applies migrations), README, tests.
  Acceptance: `fantasyleague validate my.json` prints each violation with a JSON pointer; loading a v0 file (no key) still works and prints an upgrade hint.
  Complexity: M

- [ ] P2 — Boris Chen tier importer with a staleness guard
  Why: GMM-over-consensus tiers are the community standard and his per-position text files are trivially parseable — but they were last modified 2025-12-26 as of 2026-08-16.
  Evidence: `https://s3-us-west-1.amazonaws.com/fftiers/out/text_{RB,WR,TE,FLX}-HALF.txt`, `text_QB.txt`, `text_K.txt`, `text_DST.txt` return 200 (`text_ALL-HALF.txt` is 403); `Last-Modified: Fri, 26 Dec 2025`; borischen.co methodology page.
  Touches: new `sources/borischen.py` (fetch, parse "Tier N: a, b, c", HEAD for Last-Modified, refuse if older than `--max-age-days 14`), `cli.py` (`tiers import borischen --scoring half`), `board.py` (re-tier players by name → ids), tests with fixture text.
  Acceptance: import refuses stale files with the date in the message; when fresh, `list` shows tiers matching the text file for every matched player and lists unmatched names.
  Complexity: S — needs live validation once the 2026 files publish.

- [ ] P2 — Optional FantasyPros ECR provider
  Why: one authenticated call yields `rank_ecr`, `tier`, `rank_std`, `adp`, `player_bye_week` — the honest σ for availability odds and a real tier source — for users who hold a HOF key.
  Evidence: fantasypros.com/api-data; support article 49749297704475 (HOF keys); `api.fantasypros.com/v2/docs` (403 unauthenticated).
  Touches: `sources/fantasypros.py` (`FANTASYPROS_API_KEY` env / `--ecr-key`, half-PPR draft rankings endpoint, on-disk cache), `board.py` (adopt `adp`, `rank_std`, `bye`), tests with fixture JSON.
  Acceptance: with a key, `refresh --ecr` populates `adp`/`sd`/`bye` for ≥90 % of board players by id/name; without a key the command explains how to get one and exits 0.
  Complexity: M — feeds **Live ADP fetch** and **Snake-draft awareness**.

- [ ] P2 — Unpin ruff and adopt the 0.16 default rule set
  Why: the 0.8.2 pin exists for an unrelated repo; ruff 0.16.0 (2026-07-23) enables 413 default rules and diff-rendered fixes.
  Evidence: astral.sh/blog/ruff-v0.16.0; `pyproject.toml` `ruff==0.8.2`; global CLAUDE.md note that the pin is NexRay-specific.
  Touches: `pyproject.toml` (`ruff>=0.16`), fix whatever the new defaults flag, CLAUDE.md.
  Acceptance: `python -m ruff check .` clean on ≥0.16.
  Complexity: S

- [ ] P2 — Mobile touch pass
  Why: the phone-at-the-table case (via `serve`) needs rows that are comfortably tappable; rows are ~40 px tall as of v0.0.1.
  Evidence: `board.css` `.row` padding 9 px; Apple HIG 44 pt; WCAG 2.5.8 min 24 px.
  Touches: `board.css` (`@media (pointer: coarse)` → `.row` min-height 44 px, larger chips, sticky controls height), Playwright mobile viewport test.
  Acceptance: iPhone-width viewport: no horizontal scroll, every row ≥44 px, chips reachable one-handed.
  Complexity: S

### P3

- [ ] P3 — Opponent roster-need tracking
  Why: knowing the two managers before you both need a TE changes whether Bowers survives; Draft Caddie and BeatADP both show opponent holes.
  Evidence: draftcaddie.netlify.app feature list; BeatADP "best-available considering roster needs".
  Touches: `board.js` (per-slot roster from the pick log + `teams/slot`), rail card "Picks before yours: needs".
  Acceptance: after picks are logged with slots, the card lists each intervening manager's open starter slots.
  Complexity: L — depends on **Pick log**, **Snake-draft awareness**, **Roster tracking**.

- [ ] P3 — Dynamic re-rank as picks land
  Why: static ranks ignore scarcity that emerges during the draft; Draft Sharks' "3D values" and jjti/ff's live VOR both re-order the board as positions thin.
  Evidence: draftsharks.com/kb/fantasy-football-cheat-sheet; jjti/ff README (VOR vs n+1 baseline; "worst starter" baseline per fantasyfootballanalytics.net).
  Touches: `board.py` (`vor(data, lineup, teams)` from `projected` points), `board.js` (optional "sort by live value" toggle).
  Acceptance: with projections present, draining all TE1s moves the next TE up the overall order; toggle off restores consensus rank.
  Complexity: L — depends on **Auto-flag values** (projections field) and **Roster tracking**.
