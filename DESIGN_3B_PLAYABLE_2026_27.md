# Step 3b design — a 2026-27 save that loads players and plays a full season

Status: PROPOSAL. Nothing here is built. Technical background: `DESIGN_2026_27_ROSTERS_AND_RATINGS.md` Revision 3 (§15) and `RATING_BACKTEST_PROTOCOL.md` Amendment 3.

## What we found (checked 2026-11-19)
- The loader builds a team by joining a player to **that season's own stat rows**. A season with no rows loads zero players. 2026-27 has none, so nothing can play.
- Team pace, defense and rebounding numbers are also read per season. With no row, those effects are quietly switched off.
- The database has **no 2026-27 games**. The NBA's game-finder feed only returns games already played (15 per team so far). The NBA's static schedule file refused our request (403), so a real full schedule is not available automatically.
- Roster snapshot 2 (sealed) already tells us who is on each team.

## The idea in one paragraph
Build a **pinned "starting point" for 2026-27** out of last season: each player's forecast stat line (carry-forward, the labeled placeholder), ratings derived from that line, and league/team defaults from the prior season. Store it in new tables, never in the existing season tables, so predicted numbers can never be mistaken for real ones. Give the loader a second path that reads this starting point for 2026-27 only. Every existing season keeps loading exactly as today.

## What gets built (new tables; nothing existing changes meaning)
| New thing | Plain meaning |
|---|---|
| Stat-line set | The forecast season line for every rostered player, each field tagged with where it came from (carried forward, rookie default, fallback) |
| Rating set | The 0–99 ratings computed from one stat-line set, tied to it so they can't be mixed up |
| League context set | League and team averages the engine needs (shot-zone averages, team pace/defense/rebounding), taken from last season |
| Run pin (2 new empty columns on simulation_runs) | Records which roster snapshot, stat-line set, rating set and context set a run used, so it can be reproduced |

Ratings labels, confidence display, change history and the UI come later (3d). 3b only needs the ratings to exist and be correct.

## Rules for the forecast (placeholder, honest, measurable)
- **Veterans:** last season's line, even if they changed teams. Minutes carry over from the old role (known weak spot).
- **Rookies and anyone with no NBA history:** a replacement-level line for their position at about 12 minutes a game, flagged as a default. Draft-slot-based starting points wait for Step 4.
- **Players who left the league or aren't in the snapshot:** dropped.
- **Team minutes:** the loader's existing logic already rescales each team to 240 minutes.
- **No future information:** inputs are 2025-26 and earlier only, with the information cutoff before the season's first game. A check confirms every input traces to a date at or before the cutoff.

## Schedule
No real schedule is obtainable automatically, so 3b generates one: 1,230 games, 82 per team, 41 home and 41 away, with the NBA's division/conference/other-conference game counts, spread from late October to mid-April, from a fixed random seed. It is stored as ordinary game rows with ids marked "generated" and is **not** presented as real. A real schedule can replace it later if you download the NBA's schedule file and drop it in (I would build the importer then). Runs already made on the generated schedule keep their game rows.

## Loader change
For a season with a pinned starting point, a new roster path reads the sealed snapshot for who is on each team and the stat-line and rating sets for how they play. It plugs into the existing roster builder, so the engine is untouched. It also works with the MyLeague trade overlay. Seasons without a pinned set take today's path unchanged.

## Sub-steps (each ends with a demo and your OK)
1. **3b-1: build the starting point.** New tables and the generator. Demo: print a team's forecast lines, ratings and which fields are defaults. No sim yet.
2. **3b-2: schedule + loader path.** Generated schedule, the loader branch, run pins. Demo: league run and MyLeague save for 2026-27 play all 1,230 games. This also gives the real test of the Step 2 safety check.

## Gates (pass before 3b counts as done)
- Every snapshot player loads, none dropped silently (30 teams).
- Historical runs unchanged: the 198-hash game fence, the box-score fixture, the full test suite, the 60-day MyLeague snapshot and a full-league audit with the same seeds, before and after.
- No future information in the starting point (cutoff check plus the scramble test).
- Schedule valid: 1,230 games, 82 per team, 41 home each.
- Standings sane: spread and team order compared with the known compression of past full-league runs (documented, not tuned).
- Reproducible: re-running a save from its pinned ids gives identical box scores.
- Defaults audit: the share of players and fields coming from defaults is reported, not hidden.

## Undo
Additive tables and columns only. Dropping them and reverting the loader branch returns the system to today's behavior. A pre-migration backup is taken and the migration is rehearsed on a scratch copy first, as in 3a.

## Risks and limits
- Team pace/defense/rebounding for 2026-27 are last season's values, so they ignore roster changes.
- Minutes for veterans who changed roles will be off.
- Real 2026-27 results so far (about 15 games each) are ignored by the baseline. Using them is a later step.
- Using 2025-26 as an input is production use, not validation. We will not measure accuracy against it, and the locked-set rules for the final exam are unchanged.

## Decisions needed
1. Generated schedule now, real one later by file drop?
2. May the production starting point read 2025-26 (a locked season) as an input, with no accuracy testing on it?
3. Rookie placeholder: replacement-level line at about 12 minutes. OK for now?
4. Include a minimal rating set in 3b (needed for the loader), with labels and history in 3d?
