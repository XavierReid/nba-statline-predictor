# UAT — PR #9 Event-Sourced PBP

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

## Sign-off

- [ ] All 10 scenarios pass
- [ ] No console errors during the walk
- [ ] Ready to merge PR #9

If any fail, note the scenario # + what you saw in the PR review and I'll dig in.
