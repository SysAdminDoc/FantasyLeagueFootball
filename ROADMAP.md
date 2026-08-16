# Roadmap

Single task tracker for FantasyLeagueFootball. Drain top to bottom. Shipped work lives in CHANGELOG.md.

## Next


## Later

- [ ] Superflex and full-PPR variants of the ranking set.
- [ ] Keeper/dynasty mode — age and contract context on each row.
      *Research 2026-08-16:* Sleeper picks carry `is_keeper`; Sleeper players carry `age`, `years_exp`.

## Research-Driven Additions

Added 2026-08-16 from RESEARCH.md. Ordered P0 → P3; within a tier: root-cause fixes, then trust/reliability, then quick wins, then larger bets.

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
