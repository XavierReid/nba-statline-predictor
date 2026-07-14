# Cross-Era Validation Ledger

**Purpose:** state precisely what has and has NOT been empirically validated across eras, so
the engine's generalization is never overstated. The guiding distinction throughout: a
mechanism can be *architecturally* era-agnostic (generalizes **by design**) while its
behavioral *outcome* is only *empirically verified* where the supporting real data exists.
Those are different claims and this document keeps them separate.

Ingested seasons (7): 1996-97, 2000-01, 2005-06, 2013-14, 2016-17, 2019-20, 2024-25 (+ 2025-26
for OREB). Last validated 2026-07-14.

---

## 1. VALIDATED cross-era — box-score & accounting (Gap 3.5, scoring, rebounds, OREB)

Every Gap 3.5 fix was implemented as a **single global behavioral constant operating on
era-derived player attributes** (block_attribution_scale, steal_rate, tov_scale), NOT
season-specific tuning. That decision held up under validation: `team_boxscore.py` run on all
seven ingested seasons (sim / real ratio):

| season | pts | reb | stl | blk | tov |
|---|---|---|---|---|---|
| 1996-97 | 1.01 | 1.02 | 0.91 | 1.10 | 0.91 |
| 2000-01 | 1.03 | 0.99 | 0.97 | 0.84 | 0.95 |
| 2005-06 | 1.02 | 1.01 | 1.03 | 1.04 | 0.97 |
| 2013-14 | 1.01 | 1.00 | 1.01 | 1.03 | 0.98 |
| 2016-17 | 1.01 | 1.00 | 1.02 | 1.00 | 1.03 |
| 2019-20 | 1.00 | 1.00 | 1.07 | 0.96 | 1.03 |
| 2024-25 | 1.03 | 0.94\* | 1.01 | 1.04 | 1.06 |

**Claims we CAN make:**
- **Scoring reconciles 1.00–1.03× in all seven eras** — the steal-rate and turnover-scale
  changes did NOT disturb the broader cross-era calibration.
- **Rebounds reconcile 0.99–1.02× in every era.** (\*2024-25's 0.94× is explained by the
  separate shot-efficiency issue + its rotation-filtered PlayerSeasonStats anchor, NOT rebound
  accounting — see §2 of SIMULATION_GAPS 3.5.)
- **Blocks and steals — which began as catastrophic misses (0.20× / 0.37× on 2024-25) — now
  land within ~10% across every validated modern era from the same engine.**
- OREB: mechanic validated where team data exists (2025-26 0.310 vs 0.305; 2024-25 0.297 vs
  0.293 after the data-gap ingest). Data-driven, not a constant.

This is the one-engine property: one behavioral model whose statistics EMERGE from the
underlying era-specific player data rather than per-era calibration.

---

## 2. ACCEPTED edge residuals (documented, deliberately NOT tuned away)

A single global block/steal/turnover calibration cannot perfectly reproduce the late-1990s /
early-2000s environment, where league block and turnover rates were structurally higher. The
remaining misses are accepted edge effects, in the same category as the modern star-usage and
three-point residuals — we do NOT introduce era-specific constants to erase small residuals:

- 1996-97 blocks ~1.10×, turnovers ~0.91×
- 2000-01 blocks ~0.84×
- (Steals stay within ~9% at the extremes: 1996-97 0.91×, 2019-20 1.07×.)

Rationale: erasing these would trade the stronger property (one engine generalizing from
behaviors) for cosmetic per-season accuracy — see `feedback-accounting-as-validation` and
CLAUDE.md. Revisit only if a real behavioral owner is found, never by fitting a constant.

---

## 3. NOT yet validated cross-era — Gap 3.2 game-texture (the important caveat)

The **entire game-texture milestone** — comfortable-lead behavior, Q4 compression, lead changes,
blowout rates, run/drought analysis — has been directly validated **against 2024-25 ONLY**,
because that is currently the only season with complete quarter-by-quarter line scores AND
play-by-play ingested.

**Precise claim boundary:**
- ✅ **Generalizes by design.** The implementation (comfortable-lead PROTECT in the
  GamePhase/Objectives layer) depends only on GAME STATE — score, clock, margin — not on any
  season-specific constant.
- ✅ **Does not disturb cross-era scoring.** The Q4 behavior changes leave the §1 scoring
  reconciliation intact across all seven eras.
- ❌ **The behavioral OUTCOME is empirically verified in one season only.** Whether real
  fourth-quarter compression, lead-change rates, and run texture actually match the sim in
  OTHER eras is UNMEASURED — we lack the quarter-level real data (line scores / PBP) to check.

So: the *mechanism* generalizes by design; the *outcome* is verified only where the data exists.
Do not claim more than that.

---

## 4. How to close the Gap 3.2 validation gap (data-expansion, NOT implementation)

Explicitly a data task — do **not** modify the engine for this:

1. Ingest quarter line scores (`ingest_line_scores`) and ideally play-by-play
   (`ingest_play_by_play`, CDN feed — works for ~2020+; older seasons may lack the CDN feed)
   for one or two historical seasons.
2. Re-run the existing `game_texture.py` instrumentation UNCHANGED on those seasons.
3. Interpret:
   - If the same Q4 compression / lead-change / run texture emerges → the GamePhase
     architecture generalizes as well as the box-score engine, and we can claim it.
   - If not → we've learned something real about era-specific game texture, WITHOUT having
     baked that assumption into the engine.

Measure first, validate broadly, let the data — not additional tuning — decide whether the
model truly generalizes.

---

## Summary table — claim status

| Milestone | Generalizes by design | Outcome verified cross-era | Notes |
|---|---|---|---|
| Cross-era scoring | ✅ | ✅ (7 eras, 1.00–1.03×) | pre-existing, re-confirmed |
| 3.4a/b/c player allocation | ✅ | ✅ (7-era tier harness) | |
| 3.5 blocks / steals / rebounds | ✅ | ✅ (7 eras, ~≤10%) | edge residuals in §2 |
| OREB | ✅ (data-driven) | ✅ where team data exists | 2024-25 data gap fixed |
| 3.2 game-texture (Q4/blowout/lead-change/runs) | ✅ | ❌ 2024-25 only | §3 — needs data expansion (§4) |
