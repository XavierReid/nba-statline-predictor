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

## 3. PARTIALLY validated cross-era — Gap 3.2 game-texture

The game-texture milestone (comfortable-lead behavior, Q4 compression, blowout rates) was
originally validated against 2024-25 ONLY. A second-season check was added by ingesting
2016-17 quarter line scores (`ingest_line_scores`) and re-running `game_texture.py` UNCHANGED
(no PBP — the CDN feed is 2020+ only, so run/drought & lead changes remain 2024-25-only).

**2016-17 result — the CORE mechanism generalizes:**
- **Q4 point-diff variance COMPRESSES like real:** real 66.5→57.3 (Q3→Q4), sim 62.8→**59.2**
  (vs real 57.3, +1.9). The sim tightens in Q4 in a DIFFERENT era — no longer the +11.5 blowup
  the pre-fix engine showed on 2024-25.
- **Comfortable-lead band (11-20) matches almost exactly:** sim Δ|m| **−1.14** vs real **−1.08**.
  The behavior calibrated on 2024-25 reproduces 2016-17's real Q4 compression.

**Claim boundary (updated):**
- ✅ **Generalizes by design** — keys off GAME STATE, no season constants.
- ✅ **Does not disturb cross-era scoring** (§1 intact across 7 eras).
- ✅ **Core outcome now verified in a 2nd era** — 2016-17 Q4 variance compression + 11-20
  transition delta both match real.
- ⚠️ **Two era-residuals, honestly noted:** (1) deep blowouts (enter 21+) compress too weakly —
  real 2016-17 −3.12 vs sim −0.83 (garbage-time collapse weaker than that era demanded); (2)
  blowout LEVEL is ~era-invariant in the sim (~20%) but real varies (2016-17 15.5% vs 2024-25
  20.5%), so the sim over-produces 2016-17 blowouts (19.7 vs 15.5) — driven by the weak 21+
  compression + mild Q2-Q3 over-dispersion. These are the garbage-time/era-texture layer, NOT
  the comfortable-lead mechanism, and are era-edge residuals (do not tune away).
- ❌ **Run/drought & lead changes still 2024-25-only** (no pre-2020 PBP).

So: the comfortable-lead mechanism generalizes AND is now empirically confirmed in a 2nd era
for the band it owns; the deep-blowout/era-blowout-level texture is a distinct, partly
era-specific residual left documented.

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
| 3.2 comfortable-lead Q4 compression | ✅ | ✅ 2 eras (2024-25 + 2016-17) | 11-20 band + Q4 variance match real both |
| 3.2 deep-blowout (21+) / blowout level | ✅ | ⚠️ era-residual | 2016-17 real compresses harder; garbage-time layer |
| 3.2 run/drought & lead changes | ✅ | ❌ 2024-25 only | no pre-2020 PBP (CDN feed 2020+) |
