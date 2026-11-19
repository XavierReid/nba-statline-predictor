# Foundation plan — plain-language version (for approval)

Technical detail lives in `DESIGN_2026_27_ROSTERS_AND_RATINGS.md` (§11–15) and `RATING_BACKTEST_PROTOCOL.md`. This page is the one to approve. Nothing in it has been built. No production change happens until you sign off on each step.

## The goal
Start a 2026-27 MyLeague save on day one and have it play: real rosters, sensible player stat lines, readable 0–99 ratings that change as games are played. Old seasons must still replay exactly as they do today.

## What we checked today
- The NBA's roster feed works from our setup. One call returned 525 players on teams. A team call (BOS) returned 17 players with position, height, weight, birth date, age, experience and how they were acquired.
- It is a snapshot of right now, so we save each pull with its date. It cannot rebuild past rosters (old seasons keep using game logs).
- Likely gaps: two-way players, unsigned picks, very recent moves. The roster editor covers those.
- Licensing: NBA.com terms allow private, non-commercial use. Fine for a personal portfolio project. Not fine if this ever becomes public or paid.

## What gets stored (new tables only; nothing existing changes meaning)
1. **Roster snapshots.** Each pull, with date and source. A save pins one snapshot, so it never shifts under you.
2. **Stat-line sets.** The predicted season line for each player (shooting, turnovers, fouls, assists, usage, minutes). Labeled with how it was made. Today's label: "carry-forward placeholder".
3. **Rating sets.** The 0–99 ratings, always derived from one stat-line set. Each rating has a label and a confidence (thin data shows as low confidence).
4. **Per-save overrides.** Your hand edits. They always win, show a marker, and affect that save only.
5. **Change history.** Each game-day update to a rating, with the reason.
6. **A manifest per save.** Pins which roster snapshot, stat-line set, rating set and league averages the save uses, so a save is reproducible.

## What stays untouched
Old seasons: they keep loading through today's pipeline with one frozen rating snapshot each, built from that season's own stats. Live rating updates apply to MyLeague saves only. Reproducibility is checked with the existing fixtures before and after each step.

Every existing table, the game engine, and all historical runs. New data is added beside the old. The locked test seasons stay closed.

## Steps, each with a demo and an undo
| Step | What you get | Demo | Undo |
|---|---|---|---|
| 1 | This plan, approved | You read it | Nothing built |
| 2 | Safety fix: a season with no player data stops with a clear error instead of finishing "complete" with 0 games | Start a fresh 2026-27 run, see the error message | Revert one commit |
| 3a | The new tables, plus roster snapshot saved from the NBA | Show the snapshot and its date | Drop the new tables (empty of old data) |
| 3b | A new 2026-27 save loads players and plays a full season | Play it, check totals look sane | Delete the save |
| 3c | Roster editor and hand overrides with markers (per save) | Move a player, edit a rating, see the marker | Reset the save's overrides |
| 3d | Ratings as a readable view of the stat line, with labels, confidence and history | Open a player, see why the rating is what it is | Switch back to old ratings view |
| 3e | Ratings update after each game day from the save's own games | Sim a week, watch ratings move with reasons | Turn the setting off |
| 4 | Rookie profile test (draft slot, age, size) | Report whether it beats carry-forward | Discard it |

Each step ends with a manual checklist for you before it counts as done. Schema changes (3a) and anything under `app/` are shown to you before they are applied.

## What is not in this phase
Offseason aging and growth, college or overseas data, and real-world daily syncing. Those come later.

## Known limits we are carrying
- The stat-line forecast is a labeled placeholder (carry-forward). Better methods did not clear our bar on the development seasons.
- Players with no prior NBA season (rookies, international signees, returnees) start from defaults, so their early ratings show low confidence. Veterans who changed teams keep their own stat line; the weak spot is minutes, which carry over from the old role.
- The real 2026-27 season is already underway, so a later step can use real games played so far.

## Decisions I need from you
1. Approve this plan as the Step 1 deliverable?
2. OK to start Step 2 (the safety fix) next? I will show you the code change before applying.
