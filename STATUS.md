# STATUS — read this first (refreshed at the end of every session)

**Last updated:** 2026-11-05 · **Branch:** main

## The goal
Make a brand-new season (2026-27) playable from day one, and keep old simulations reproducible.
The sim needs a full player stat line for each player; today a fresh season loads **zero players**.

## Done
- Trade-value work: finished, result was a narrow fail. **Parked**, nothing wired in.
- Rating test plan (`RATING_BACKTEST_PROTOCOL.md`, Amendments 1-3) and design (`DESIGN_2026_27_ROSTERS_AND_RATINGS.md`, Revision 3).
- Leak-proof test tools: as-of reader + canary (passes all 12 seasons), coverage report, error-size estimate (MDE ~8-10%).
- Test arms built and checked on season 2021-22: P (predicted), D (no player profile), O_r (real lines, P's minutes), E (today's behavior), O (unchanged).

- Hard lock on locked seasons (guard + hook); you open it with `scripts/open-locked-set`.
- Field trace: P supplies every field the sim reads that the real-data arm supplies.
- First P vs D vs O_r vs O comparison, 2021-22, 10 seeds each (`results/e2e/compare_2021-22.json`): carry-forward (P) closes about half of the gap between no-profile (D) and real lines on team standing order and scoring; weak on playing time.

## Next (in order)
1. Repeat the comparison on 2 more dev seasons (2016-17, 2018-19); decide what the placeholder is missing (rookies, 9 of top-80 players absent).
2. Fix the minutes/roster projection (biggest remaining gap), then fancier prediction methods.
3. Only then: fancier prediction methods. Nothing in production until you approve the schema/migration plan.

## Waiting on you
- Schema/migration plan approval (before any production change).
- Whether to fix the silent "empty season finishes as complete" behavior (production change, not done).

## Rules in force
- **Locked seasons 2022-23 to 2025-26 stay closed** until the method and constants are frozen and committed.
- The sim engine (`app/`) is not modified without your approval.
- Handy: `scripts/vrun <database> <module>` runs a module against a database.
