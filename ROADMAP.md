# Roadmap

Single task tracker for FantasyLeagueFootball. Drain top to bottom. Shipped work lives in CHANGELOG.md.

## Next

- [ ] **Board expansion to 200** — currently 75 deep, which runs out around round 7 in a
      12-team league. Late rounds are where the sleeper rail is doing the work instead.
      *Research 2026-08-16:* Boris Chen and FantasyPros both publish top-200; Sleeper `search_rank`
      is a free ordering proxy for depth once the ID crosswalk exists.

## Later

- [ ] Superflex and full-PPR variants of the ranking set.
- [ ] Keeper/dynasty mode — age and contract context on each row.
      *Research 2026-08-16:* Sleeper picks carry `is_keeper`; Sleeper players carry `age`, `years_exp`.
- [ ] Auction values as an alternate mode ($200 budget default).
      *Research 2026-08-16:* standard method is VBD points-over-replacement → share of total budget
      (FantasyPros calculator; DraftExpert publishes Fair/Target/Max). Yahoo `settings.is_auction_draft`
      and `draft_analysis.average_cost` exist for later sync.

## Research-Driven Additions

Added 2026-08-16 from RESEARCH.md. Ordered P0 → P3; within a tier: root-cause fixes, then trust/reliability, then quick wins, then larger bets.

### P2

- [ ] P2 — Boris Chen tier importer with a staleness guard
  Why: GMM-over-consensus tiers are the community standard and his per-position text files are trivially parseable — but they were last modified 2025-12-26 as of 2026-08-16.
  Evidence: `https://s3-us-west-1.amazonaws.com/fftiers/out/text_{RB,WR,TE,FLX}-HALF.txt`, `text_QB.txt`, `text_K.txt`, `text_DST.txt` return 200 (`text_ALL-HALF.txt` is 403); `Last-Modified: Fri, 26 Dec 2025`; borischen.co methodology page.
  Touches: new `sources/borischen.py` (fetch, parse "Tier N: a, b, c", HEAD for Last-Modified, refuse if older than `--max-age-days 14`), `cli.py` (`tiers import borischen --scoring half`), `board.py` (re-tier players by name → ids), tests with fixture text.
  Acceptance: import refuses stale files with the date in the message; when fresh, `list` shows tiers matching the text file for every matched player and lists unmatched names.
  Complexity: S — needs live validation once the 2026 files publish.

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
