# STATUS — read this first (refreshed at the end of every session)

**Last updated:** 2026-11-04 · **Branch:** main (pushed through `40aa7c3`)

## The goal
Make a brand-new season (2026-27) playable from day one, and keep old simulations reproducible.
The sim needs a full player stat line for each player; today a fresh season loads **zero players**.

## Done
- Trade-value work: finished, result was a narrow fail. **Parked**, nothing wired in.
- Rating test plan (`RATING_BACKTEST_PROTOCOL.md`, Amendments 1-3) and design (`DESIGN_2026_27_ROSTERS_AND_RATINGS.md`, Revision 3).
- Leak-proof test tools: as-of reader + canary (passes all 12 seasons), coverage report, error-size estimate (MDE ~8-10%).
- Test arms built and checked on season 2021-22: P (predicted), D (no player profile), O_r (real lines, P's minutes), E (today's behavior), O (unchanged).

## Next (in order)
1. Trace which fields the sim really reads (proves the stat-line field list is complete).
2. Compare P vs D vs O_r over a dev season, same seeds (step 6).
3. Only then: fancier prediction methods. Nothing in production until you approve the schema/migration plan.

## Waiting on you
- Schema/migration plan approval (before any production change).
- Whether to fix the silent "empty season finishes as complete" behavior (production change, not done).

## Rules in force
- **Locked seasons 2022-23 to 2025-26 stay closed** until the method and constants are frozen and committed.
- The sim engine (`app/`) is not modified without your approval.
- Handy: `scripts/vrun <database> <module>` runs a module against a database.
