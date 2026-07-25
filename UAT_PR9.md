# UAT — PR #9 Event-Sourced PBP

**Status: complete (2026-07-25).** First pass surfaced 6 display-layer gaps (A–F below); all fixed on branch. Re-UAT after fixes → pass.

**Per CONTEXT_PRIMER.md step 6:** run `python scripts/purge_sims.py --confirm` before starting.

Then open `localhost:5173` and walk through each scenario. Mark ✅ / ❌ / notes in the "Result" column.

Prereqs already verified in Build (not part of this UAT — noted so you don't repeat):
- `pytest tests/` → **354/354** (includes the 90-game byte-identical fence)
- `npm test -- --run` → **20/20**
- `npm run build` → clean, 0 tsc errors
- Browser smoke: no console errors on load or after a game sim

---

## Golden path (5 scenarios)

| # | Setup | Action | Expected | Result |
|---|-------|--------|----------|--------|
| 1 | `2024-25`, `DEN@GSW`, seed **42**, preset `drama-m3` | Click **Simulate**, wait for result | Line score renders 4 quarters, both totals plausible (~110–125 each); box scores populate; PBP toggle appears at the bottom of the page with an event count in the label. | |
| 2 | Same game as #1 | Click **Show play-by-play** | Rows render with clock (`Q1 11:45`), running score, and a description. First few rows read like real NBA PBP (shots + rebounds + turnovers). No `undefined` / `null` in any description. | |
| 3 | Same game | Scroll the PBP; find one made shot with an assist | Row reads `"<Shooter> hits a <shot> (assisted by <Assister>)"` — one row, not two. There should be no standalone `"<Player> assist"` rows immediately after a SHOT event in the same possession. | |
| 4 | Same game | Scroll to find at least one free-throw sequence | You should see per-FT rows: `"<Shooter> makes free throw 1 of 2"`, `"<Shooter> makes/misses free throw 2 of 2"`. Two-shot and three-shot trips both present. And-1 should appear as `"...hits a <shot>"` followed by `"<Shooter> makes free throw 1 of 1"`. | |
| 5 | Same game | Click a box-score row for a **high-FTA player** (Jokic or a guard with ≥ 5 FTA) | Modal opens. Chips row shows **`FT`** as a distinct chip (not folded into SHOT). Modal PBP rows for that player include per-FT rows tagged `FT`, not `SHOT`. | |

## Edge cases (5 scenarios)

| # | Setup | Action | Expected | Result |
|---|-------|--------|----------|--------|
| 6 | Any `drama-m3` game (seed of your choice) that goes to OT — try seed `26 OKC@BOS 2025-26` (previously used in modal UAT) | Simulate | Line score shows OT column(s). PBP contains OT possessions (Q ≥ 5 in the clock label). | |
| 7 | Any drama-m3 Q4 game with a late foul | Simulate; scroll PBP to Q4 final ~30s if a close margin | You should see at least one row `"Intentional foul on <Player> — N/M FTs"` (strategic-foul path). No garbage `null`/`undefined` on it. | |
| 8 | Same game as #7 | Open the fouler's modal (player who committed the intentional foul) | Their box score should NOT change from PR #8 baseline (strategic-foul is a known pre-existing box-omission — documented follow-up, deliberately not fixed here). Intentional-foul row won't appear under the FOUL chip for them either. This is the expected behavior, not a bug. | |
| 9 | Any game, open a modal, toggle chips off | Click `FT` chip off | Only FT rows disappear from the modal's PBP; other rows unaffected. Click FT back on, they return. Try the same with `FOUL`, `REB`, `AST`. | |
| 10 | Any game with a foul-drawn miss (very common — every game has these) | Find a shooting foul row `"<Defender> commits a shooting foul on <Shooter>"` followed by FT rows | The **shooter's FGA should NOT be incremented** for that trip (visible in the box: FGA count matches only countable attempts). Open the shooter's modal — the row should be tagged `FT` (or `FOUL` for the fouler's involvement), never `SHOT`. This is the PR #8 accounting fix carried through to the event layer. | |

---

## What to watch for outside the scenario list

- **Empty rows / `undefined` / `null`** anywhere in a description — should never happen.
- **Duplicated rows** for the same event (shouldn't happen, but the collation is new).
- **A SHOT row followed by a standalone AST row in the same possession** — collation bug if it appears.
- **Missing events at OT boundaries** — check quarter labels stay consistent.
- **Console errors** in the browser devtools during any scenario.

## First-pass findings (2026-07-25)

Xavier ran `OKC@BOS 2025-26 seed=26` and `OKC@SAS 2025-26 seed=23`. Scenarios 1–6 + 9 passed as written. The remaining findings became fixes A–F below (all display-layer, invariance fence untouched).

| Fix | Scenario | Finding | Resolution |
|-----|----------|---------|------------|
| A | 5 (modal FT chip), and modal AST rows generally | Assist row in the player modal reads `"Shai assist"` with no context about whose shot it enabled. Same failure mode would hit BLK for shot-blockers. | AST/BLK events now carry `shot_by` + `shot_type`. `describe_typed_event` renders `"Shai assists Hartenstein's mid-range jumper"` / `"Blocker blocks Shooter's layup"`. Falls back to the old form if `shot_by` isn't set (defensive). |
| B | 7 (intentional foul) | Description was `"Intentional foul on X — 2/2 FTs"` — no fouler name, and the "N/M FTs" tail duplicated info the FT rows already carry. | Strategic-foul path now picks a fouler (deterministic — highest `foul_rate` defender, matches real coaching pattern of sending a bench player with fouls to spare). FOUL event gets `player_id=fouler_id` and `intentional=True`. `describe_foul` renders `"Bench Guy commits an intentional foul on Isaiah Hartenstein"`. The FT tail is gone — each FT event describes itself as `"Hartenstein makes free throw 1 of 2"`. |
| C | 7 (fouler in FOUL chip) | Once B lands, the fouler's modal shows the intentional foul under the FOUL chip. | Free with (B) — `player_id` is now set on the event, involvement logic tags it. |
| D | 7 (FT tail format) | The "N/N FTs" tail assumed FTs would always be awarded. | Removed the tail entirely — FT events describe themselves. Robust to a future pre-bonus intentional foul that awards 0 FTs. |
| E | 3 (STL collation) | STL rendered as its own row after the parent TOV: `"Sam Hauser turnover"` then `"Isaiah Joe steal"`. | Collation extended: STL folds onto its parent TOV as `"Sam Hauser turns it over (Isaiah Joe steals)"` in the main PBP. STL still shows standalone in the player modal (filtered view). |
| F | 3 (offensive foul collation) | `TOV(P) + FOUL(offensive, same P)` rendered as two rows. | Collation extended: the TOV row is dropped and the FOUL row becomes the single `"P commits an offensive foul"` line. |

**Known limitation carried into this PR (documented follow-up):**

The strategic-foul fouler's PF is **not** credited to their box score. In real NBA, an intentional foul is a personal foul on the fouler. The sim never credited it (pre-existing pre-refactor behavior), and preserving that omission is what keeps the 90-game byte-identical fence green. Naming the fouler for display without applying to the box is a halfway state we accept for now — flagged in the strategic-foul path in `app/services/game_simulator.py` with a comment linking to this note. Correcting it fully = re-capture the fixture with the new correct behavior. Left for a follow-up PR.

## Feature asks parked as follow-ups (NOT in this PR)

- Quarter filter on the main PBP (dropdown / chip row for Q1/Q2/Q3/Q4/OT).
- Search box on the main PBP for descriptions (`"Shai"`, `"intentional foul"`, etc.).
- Chip toggles on the main PBP (same tag row as PlayerModal, per event type not per player).
- Running per-player stat inline in the modal PBP (`"Shai makes free throw 1 of 2 [PTS 15]"`).

All four are natural next-iteration features. None affect correctness of the event-sourced PBP — they extend PBP navigation UX. Own PR when they land.

## Sign-off

- [x] All 10 scenarios pass (post-fix re-UAT)
- [x] No console errors during the walk
- [x] Ready to merge PR #9

If any fail, note the scenario # + what you saw in the PR review and I'll dig in.
