# Trade value model — validation protocol and results (M-6a increment 4)

**Status: PROTOCOL (pre-registered). Results are appended below only after the protocol and the
frozen model are committed.** Commit order is the audit trail: protocol -> frozen model ->
holdout results. Nothing in this increment adds an endpoint, UI, or changes the locked CPU rule.

## 1. Question

Before player values drive CPU trade decisions, show that they predict **actual simulated team
impact**. Three layers, each stricter than the last:

* **G6a — ordering.** Does value rank players like the simulator's measured impact does?
* **G6b — packages.** Does the sum of values predict the simulated effect of removing a *set* of
  players? This is where convexity is decided; rank correlation of single players cannot decide it.
* **G6c — decisions.** Do the locked CPU rule's verdicts on realistic proposals agree with the
  simulated outcome for the CPU team, and is it safe only because it rejects almost everything?

## 2. Locked inputs (not revisited here)

CPU rule: from the CPU team's side, `incoming` = summed value of players it receives, `outgoing` =
summed value it gives up; per-player value clamped >= 0; missing stats -> value 0 (reason-coded).
Accept iff `incoming >= 1.1 * outgoing`; if `outgoing == 0` accept iff `incoming > 0`; both 0 ->
reject `ZERO_VALUE_TRADE`. Deterministic, no RNG. `overall_rating` is never an input (guardrail #1).

## 3. Value model (provisional until Section 8)

Per player-season, from `PlayerSeasonStats` only:

1. **Production** `G` = Hollinger Game Score per game:
   `PTS + 0.4*FGM - 0.7*FGA - 0.4*(FTA-FTM) + 0.5*REB + STL + 0.7*AST + 0.7*BLK - 0.4*PF - TOV`.
   (PSS stores total rebounds only, so REB takes the average of Hollinger's 0.7 ORB / 0.3 DRB.)
2. **Replacement level** `R` (per season, data-derived): mean `G` of the players ranked 11th-13th by
   MPG on each team, among players with GP >= 10, pooled across all teams that have >= 13 such players.
3. **Variants** (compared, not assumed):
   * shrink: off, or on — `G' = R + (G - R) * GP / (GP + 20)` (thin samples regress to replacement)
   * availability: off, or on — multiply by `min(1, GP / team_max_GP)`
   * convexity `k`: `V = (max(0, G' - R))^k * availability`
4. Values are fully auditable: `explain()` returns production, replacement, GP weight, availability
   and the final value for any player.

## 4. Replacement-level audit (reported before any fitting)

Per-team means of ranks 11-13 (spread across teams, n), sensitivity of `R` to the rank window
(10-14, 12-15) and the GP filter, and behaviour for low-minute players: value by MPG bucket
(<5, 5-10, 10-15, 15-20, 20-25, 25+): n, share with V=0, median, max, and the highest-valued
players under 10 MPG listed by name. Anomalies are reviewed by hand and reported, not silently fixed.

## 5. Ground truth: simulated with/without impact

* **Config / rosters.** `DRAMA_M3` (single-game preset: availability off, depth 10), `pre_negation`
  and `paint_mid_split` as that preset requires. *In* = the team's top-10 by MPG. *Out* = load
  `10 + n_removed` players and drop the removed ones, exactly as `advance_to` backfills OUT players.
* **How minutes and possessions redistribute (documented, and measured in the report).** Possessions
  per game are fixed by the team-pace budget, so removing a player never changes possession count.
  His minutes are re-absorbed by the rotation's deficit-based backfill (whoever is furthest below
  their target minutes absorbs first) and his shots by the on-court players. The report lists the
  top absorbers of minutes for sample removals and the mean team possessions in each condition.
* **Pairing.** For each team, a fixed list of `G` games (opponent, home/away, seed) is generated once
  and reused for the baseline and every removal condition. Opponents cycle through the league's other
  29 teams at full strength; venue alternates. Metric = team point margin per game. (See Amendment 1:
  reusing the *seed* removes almost no variance, because the random streams diverge as soon as the
  rosters differ; the real pairing benefit is the shared opponent/venue schedule.)
* **Impact** of removing set `S`: `I(S) = mean_g[margin_in(g) - margin_out_S(g)]` (positive = the
  team is worse without them), with the paired standard error.
* **Repeat variance and noise ceiling.** Every measurement is made twice with independent seed sets
  A and B (`G = 1500` each; see Amendment 1). Reliability `r` = Spearman(A, B) over all conditions; the ceiling on any
  predictor's attainable correlation with the averaged estimate is `sqrt(2r / (1 + r))`
  (Spearman-Brown). Reported next to every rho. The analysis uses the A/B average.
* **Individual vs interaction.** Singles are measured alone. For packages, `interaction(S) = I(S) -
  sum_{i in S} I({i})`; its distribution is reported, and the additive-impact prediction
  `sum I({i})` is itself scored as a package predictor (an upper-bound-ish reference for any
  per-player value model).

Selection per team (ranks by MPG within the top 10): **singles** at ranks 1, 2, 3, 5, 8;
**packages** {1,2}, {3,5}, {5,8}, {1,8}, {2,5,8}.

## 6. Splits (fixed before any run)

The 30 teams are shuffled with seed `20261020` and split 15 development / 15 holdout.
* **Development** = dev teams, 2024-25. All fitting happens here and only here.
* **Holdout A** = holdout teams, 2024-25 (unseen teams, same season).
* **Holdout B** = holdout teams, 2016-17 (unseen teams *and* unseen season/era).
*Assumption (Xavier's fork left open): 2016-17 as the held-out season; revisable until the freeze.*

## 7. Fitting and freezing rules

* **Stage 1** — at `k = 1`, choose `(shrink, availability)` maximizing the dev single-player
  Spearman (G6a). Ties within 0.01 -> prefer shrink on, availability off (fewer moving parts).
* **Stage 2** — with that choice, choose `k` from `{1, 1.25, 1.5, 1.75, 2, 2.5, 3}` maximizing the
  dev **package** Spearman (G6b). Ties within 0.01 -> smaller `k`.
* The holdout candidates are exactly: **linear** `k=1`; **modest** `k=1.5`; **best-fit** `k*` from
  Stage 2. They are written to `validation/trade_value/results/frozen_model.json` and committed
  *before* any holdout number is computed. No re-tuning after holdout results; if the holdout
  disagrees we report the disagreement, we do not refit and re-report the same data as validation.

## 8. Pass criteria (proposed; Xavier may revise until the freeze commit)

* **G6a** — holdout single-player Spearman >= 0.60 for the recommended model, on Holdout A and B,
  reported with the noise ceiling and as a fraction of it.
* **G6b** — holdout package Spearman >= 0.60 for the recommended model. **Convexity is supported
  iff** `k*` (or 1.5) beats `k=1` on holdout package Spearman by >= 0.03 with a team-clustered
  bootstrap 95% interval excluding 0; otherwise recommend the simplest model within 0.02 of the best.
* **G6c** — see Section 9. Proposed pass: `P(preserve | accepted) >= 0.85` **and** lift over the
  all-proposals base rate `>= +0.10` **and** acceptance rate within `[10%, 70%]` **and**
  `P(accepted | beneficial) >= 0.40`. These guard against both over-accepting and "safe because it
  rejects everything".
* A model that passes G6a but fails G6b/G6c is **not** promoted; the fallback is a measured-impact
  table (Section 10) or returning to Xavier with the evidence.

## 9. G6c design — decisions

* **Proposals** (holdout teams only; the CPU team is the *receiving* team B, proposer A):
  1. *CPU gives a star for depth*: B gives its MPG-rank-1/2 player, receives 2-3 players ranked 3-8.
  2. *CPU gets a star for depth*: A's rank-1/2 player for 2 B players ranked 4-9.
  3. *Balanced 1-for-1*: ranks within +/-1 of each other, both in 1-8.
  4. *Balanced 2-for-2*: two players each from similar rank bands.
  30 per type = 120 proposals, drawn with seed `77`; holdout teams 2024-25 (Holdout A) and the same
  recipe on 2016-17 (Holdout B), 120 each.
* **Measured effect** = change in the CPU team's mean margin, `Delta_B = margin_after - margin_before`,
  `G = 1500` paired games per proposal arm (see Amendment 1), the trade applied with the shipped `RosterOverlay`
  (`load_roster(overlay=...)`), which also exercises the production ownership machinery.
* **Classes** (default `tau = 1.0` point/game; sensitivity at 0.5 and 1.5): *preserve* =
  `Delta_B >= -tau`; *beneficial* = `Delta_B >= +tau`; *harmful* = `Delta_B < -tau`. Each estimate's
  paired SE is reported and the share of trades with `|Delta_B| < 2 SE` (unresolvable at this `G`)
  is stated.
* **Reported:** for accepted and for rejected trades — predicted value change (`incoming - outgoing`)
  and ratio, measured `Delta_B`; `P(preserve | accepted)`; `P(beneficial | rejected)`;
  `P(accepted | beneficial)`; the **base rate** `P(preserve)` over all proposals, the acceptance
  rate, and a "reject-everything" / "accept-everything" / "random at the same rate" comparison;
  everything broken down by trade type; Spearman of predicted change vs `Delta_B` overall and by
  type; and a binned calibration table. Bootstrap 95% intervals throughout.

## 10. Recommendation format

The results section ends with: the recommended `(shrink, availability, k)` or "none", which gates
passed/failed on which holdout, the strongest evidence against it, and what decision Xavier needs
to make next. If the formula fails, the fallback is a per-season measured-impact table built with
the Section 5 machinery; that is a separate decision, not part of this increment.

## Amendment 1 — sample sizes (made after a 2-team smoke test, before any real measurement)

A 150-game smoke test on two dev teams showed (a) the per-game margin has SD ~16.8 points, and (b)
seed-pairing barely helps: paired SE 1.78 vs unpaired 1.94 points. At the originally planned
`G = 300` a single condition's SE would be ~1.3 points against typical star effects of 3-6 points,
which would make the noise ceiling uninformatively low. Changes, all made before any dev/holdout
run: `G_MEASURE` 300 -> 1500 and `G_PROPOSAL` 600 -> 1500 (paired SE ~0.6 per seed set, ~0.43 after
averaging sets A and B; Delta_B in G6c SE ~0.6). Nothing else in the protocol changed: the splits,
the variants, the selection and freeze rules and the pass criteria stand as committed in the
protocol commit. The smoke test also confirmed: results are bit-for-bit reproducible, and a
removal does not change per-game possession counts (217.8 vs 218.1), as Section 5 assumed.

---
# RESULTS

## R1. Development results (15 dev teams, 2024-25; the holdout has NOT been touched)

Measured: 15 teams x 2 seed sets x 11 conditions x 1500 games (838 s). `results/measure_dev.json`,
`results/dev_report.json`, `results/frozen_model.json` (the pre-registered rule's output).

**Ground truth quality.** Repeat reliability (Spearman, set A vs set B): singles r=0.749 (noise
ceiling 0.926), packages r=0.857 (ceiling 0.961); mean SE 0.39 points per condition. The measurement
is informative enough to judge a model.

**Mean simulated impact of removing a player (points of margin per game, 15 dev teams):**
rank 1: +1.08, rank 2: +1.65, rank 3: +0.71, rank 5: +0.67, rank 8: +0.37; packages {1,2}: +3.66,
{2,5,8}: +3.35, {1,8}: +2.23, {3,5}: +1.55, {5,8}: +1.45. **In this engine one player barely moves a
team** (a star is worth ~1-2 points, versus ~5+ in the real league).
Redistribution, interaction and replacement-level audits are in `dev_report.json`; interaction is
+0.59 points (sd 0.93): removing several players costs slightly more than the sum of removing each.
The additive reference (summing each member's separately *measured* impact) predicts package impact
with rho 0.864, so an additive per-player value is not the limiting factor.

**Pre-registered selection (applied exactly as written).**
* Stage 1 (k=1, single-player rho): shrink=F/avail=F 0.462; F/T 0.469; T/F 0.465; T/T 0.469 ->
  differences <= 0.007, so the tie rule picks **shrink on, availability off**.
* Stage 2 (package rho by k): 1.0: 0.538, 1.25: 0.535, 1.5: 0.534, 1.75: 0.524, 2.0: 0.511,
  2.5: 0.496, 3.0: 0.486 -> **k\* = 1.0**. Convexity is *not* supported on the development data;
  package rho falls monotonically as k rises. (Single-player rho is, as expected, identical for every k.)

**Against the pass criteria (development data):** G6a single-player rho = 0.465 (< 0.60, ceiling
0.926); G6b package rho = 0.538 (< 0.60). The Game Score formula, as registered, does not meet
G6a/G6b on the data it was fit on.

**Exploratory diagnostic (post-hoc, dev only, NOT a registered test; picked best-of-12 so inflated).**
Spearman of each stat with the simulated single-player impact (n=75): TS% above league 0.600;
Game Score 0.462; PPG 0.381; REB 0.346; MPG 0.310; usage 0.273; STL+BLK 0.228; real +/- 0.205;
AST 0.131. Hypothesis: the engine fixes possessions per game, so a scorer's volume is simply
redistributed when he sits, and what the simulator rewards is efficiency per possession used rather
than raw production. Untested on the holdout.

---
## Amendment 2 — efficiency candidates (registered after R1; the holdout is still unopened)

*Decision (Xavier, 2026-10-22):* value is **sim-coherent** (how much a player matters in this
simulation); the engine's muted star impact is **banked as a separate limitation** and is never
compensated for by inflating star values or tuning the acceptance rule. Hypotheses are generated on
development data only; the holdout validates, and does not influence design.

**Provenance, stated plainly.** The candidate idea comes from the exploratory R1 diagnostic on the
development teams (TS% tracked simulated impact better than Game Score). That diagnostic was run
*before* this amendment and is the only reason these candidates exist. Nothing here has been seen
on the holdout. **No further candidates may be added after this amendment**; any new idea needs a
new increment with fresh holdout data.

### A2.1 Registered production specs (per game; no fitted weights)

Per player-season, with `TSA = 2*(FGA + 0.44*FTA)`, `TS% = PTS / TSA` (league value if `TSA = 0`),
`lgTS = sum(PTS*GP) / sum(TSA*GP)` over the season's league-wide rows, and
`NS = 0.5*REB + STL + 0.7*AST + 0.7*BLK - 0.4*PF - TOV` (Hollinger's non-shooting terms, same REB convention):

| id | spec | formula | role |
|---|---|---|---|
| GS | `game_score` | Hollinger Game Score | registered baseline |
| TSP | `ts_points` | `(TS% - lgTS) * TSA` = efficiency x attempts | eligible |
| TSP_NS | `ts_points_nonshooting` | `(TS% - lgTS) * TSA + NS` | eligible |
| NS | `nonshooting` | `NS` | **attribution control, never selected** |

*Playing time* is carried by the per-game attempts and counting stats (a 34-minute starter has more
of both than a 12-minute reserve); *usage* by `TSA` (volume) multiplying efficiency; no separate
minute or usage terms are added, because the dev diagnostic gave them no support (MPG 0.31,
usage 0.27) and adding them would be fishing.

### A2.2 Replacement level, shrinkage, availability, convexity

* **Replacement** `X_R` = mean of the *same spec's* production over each team's 11th-13th men by MPG
  (GP >= 10; teams with < 13 such players skipped), pooled across teams; value = `max(0, X' - X_R)^k`.
  One implementation serves every spec (`replacement_level(score=...)`), so the calculation cannot
  differ between candidates. A replacement-level audit per spec is part of the dev report.
* **Shrink on, availability off** for every candidate (the Stage-1 result of R1; availability moved
  rho by <= 0.007 and is dropped from the search).
* **Convexity** stays empirical on the grid `{1, 1.25, 1.5, 1.75, 2, 2.5, 3}`; selected by the dev
  rule only. R1 already found no support (k*=1.0 for Game Score); that is not assumed to carry over.

### A2.3 Selection rule (applied once, on the development teams, before the holdout)

1. **Spec**: among the eligible set {GS, TSP, TSP_NS} at `k = 1`, choose the highest **mean of the
   dev single-player rho (G6a) and the dev package rho (G6b)**. A candidate must beat the current
   best by more than 0.01 to displace it; ties keep the earlier one in the order GS, TSP, TSP_NS
   (conservative: do not switch unless clearly better).
2. **k**: for the selected spec, the grid value with the highest dev package rho; ties within 0.01 -> smaller k.
3. Frozen to `results/frozen_model_v2.json` and **committed before any holdout measurement**.

### A2.4 Decisive vs descriptive

* **Decisive candidate** = the selected spec at `k*`, plus its `k = 1` and `k = 1.5` versions for the
  convexity test (Section 8 rules unchanged). **Pass/fail is judged only here.**
* **Descriptive** = every registered spec (including NS) at `k = 1`, reported for attribution.
  If a descriptive candidate beats the decisive one on the holdout it is **not** promoted and the
  holdout is **not** used to re-select; the finding is reported and would need fresh data.
* If no decisive candidate passes G6a, G6b and G6c, the result is reported as **negative**, and the
  options (ship a limited version, use measured impact directly, prioritise the engine work) go back
  to Xavier. Nothing is selected after seeing holdout performance.

### A2.5 Measured-impact benchmark ("oracle")

Holdout measurements add single-player conditions for **every MPG rank 1-10** (the registered ranks
{1,2,3,5,8} still define G6a/G6b; the other ranks are used only for the benchmark). From these:

* **G6a/G6b reference:** the additive-measured predictor (sum of members' measured single impacts)
  is reported next to every candidate, so "the formula" can be separated from "per-player additive
  valuation".
* **G6c oracle:** the same proposals judged by (i) the locked 1.1x ratio rule on clamped measured
  impacts and (ii) a difference rule (accept iff sum of incoming measured impact >= sum of outgoing).
  Measured impacts have SE ~0.4; the oracle is a benchmark, not a rule to ship.

### A2.6 Pass criteria and reporting (unchanged; restated so they are frozen)

G6a >= 0.60 single-player rho, G6b >= 0.60 package rho (holdout A and B, with noise ceiling and
fraction of ceiling), convexity supported only if k* or 1.5 beats k=1 by >= 0.03 with a team-clustered
95% interval excluding 0, and G6c: `P(preserve | accepted) >= 0.85`, lift >= +0.10, acceptance rate in
[10%, 70%], `P(accepted | beneficial) >= 0.40` (tau = 1.0 point/game; sensitivity at 0.5 and 1.5).
G6c reports accepted-trade success rate, rejected beneficial trades, acceptance rate, the
accept-all / reject-all / random-at-same-rate baselines, and results by trade type with the
star-for-depth types called out. Correlation alone never constitutes success.
Scope: no endpoint, UI, or production trade-behaviour change until the frozen holdout results are reviewed.

## R2. Amendment 2 — development selection and FREEZE (holdout still unopened)

Applied the A2.3 rule once to `results/measure_dev.json` (15 dev teams, 2024-25; shrink on,
availability off). `results/frozen_model_v2.json` (sha256[:16] = `9fa84021077cf027`) is committed together
with the holdout scripts *before* any holdout measurement exists.

| spec (k=1) | dev single rho | dev package rho | mean | eligible |
|---|---|---|---|---|
| game_score (baseline) | 0.465 | 0.538 | 0.502 | yes |
| **ts_points** | **0.596** | **0.596** | **0.596** | **yes — selected** |
| ts_points_nonshooting | 0.554 | 0.436 | 0.495 | yes |
| nonshooting (control) | 0.304 | 0.274 | 0.289 | no |

**Selected spec: `ts_points`; `k\*` = 1.0.** Package rho across the k grid for ts_points is flat
and, if anything, falls: 1.0: 0.596, 1.25: 0.591, 1.5: 0.594, 1.75: 0.593, 2.0: 0.591, 2.5: 0.588,
3.0: 0.587. Convexity is again not supported on development data. Decisive candidates: ts_points at
k=1 (linear), k=1.5 (modest), k=1.0 (best-fit, identical to linear). Baseline: game_score k=1.

**Replacement-level audit notes (read before interpreting results).**
* `ts_points` replacement level is **-0.23** (the 11th-13th men are slightly *below* league-average
  efficiency); the per-team means have SD 0.34. For `game_score` it is 4.79 (SD 1.30).
* `ts_points` value is nearly **independent of playing time** (rank correlation with MPG 0.22,
  versus 0.92 for game_score): efficiency x attempts is small for everyone. About 40% of players with
  25+ MPG have value 0 (inefficient starters are below replacement on efficiency), and 44% of
  <5 MPG players do too. Highest-valued player under 10 MPG: M. Brown 1.25.
* Adding Hollinger's non-shooting terms *reduced* package rho (0.596 -> 0.436); the control
  (non-shooting alone) is weak (0.30/0.27). So the efficiency spec deliberately ignores rebounding,
  passing and defense; its failure modes there are not corrected for.
* A dry run of the full pipeline on *development* data reproduces these numbers (and, in-sample,
  ts_points has single/package rho 0.596 with 95% intervals ~[0.42, 0.75] over 75 conditions): a true
  rho near 0.60 would fall below the 0.60 bar roughly half the time, which is a property of the
  frozen threshold and sample size, not something this protocol changes.

Holdout run plan (frozen): `run_measure --split holdoutA|holdoutB` (adds ranks 1-10 for the oracle),
then `run_holdout` and `run_g6c` for each split. No parameter above is revisited afterwards.

## R3. Holdout results (frozen model `9fa84021077cf027`; nothing was re-tuned after seeing them)

Holdout A = 15 unseen teams, 2024-25. Holdout B = the same 15 teams in the unseen 2016-17 season.
Each: 15 teams x 2 seed sets x 16 conditions x 1500 games, plus G6c = 120 proposals x 2 arms x 1500
games. Decisive model: **ts_points, k=1** (k=1.5 and k\*=1.0 reported alongside). Files:
`measure_holdout{A,B}.json`, `holdout{A,B}_report_v2.json`, `g6c_holdout{A,B}_{raw,report_v2}.json`.

### Gate results (frozen bars; 95% team-clustered intervals in brackets)

| gate | holdout A (2024-25) | holdout B (2016-17) | bar | met on both? |
|---|---|---|---|---|
| G6a single-player rho | **0.394** [0.08, 0.67] (44% of ceiling 0.90) | 0.697 [0.50, 0.84] (73% of 0.96) | >= 0.60 | **No** (A) |
| G6b package rho | **0.541** [0.29, 0.75] (55% of 0.98) | 0.657 [0.47, 0.79] (67% of 0.98) | >= 0.60 | **No** (A) |
| convexity (k=1.5 vs 1, package rho) | +0.008 [-0.016, +0.044] | -0.001 [-0.017, +0.016] | >= +0.03, CI > 0 | not supported |
| G6c P(preserve \| accepted) | 0.95 [0.90, 1.00] | 0.94 [0.86, 1.00] | >= 0.85 | yes |
| G6c lift over base rate | +0.15 [+0.07, +0.24] | +0.20 [+0.10, +0.34] | >= +0.10 | yes |
| G6c acceptance rate | 52% [39, 65] | 43% [30, 57] | 10-70% | yes |
| G6c P(accepted \| beneficial) | 0.88 [0.72, 0.97] | 0.75 [0.52, 0.93] | >= 0.40 | yes |

**Formal outcome (A2.4): the decisive candidate does NOT pass all pre-registered gates** — G6a and
G6b are below 0.60 on holdout A — so the result is reported as **negative on the strict criteria**,
with a split decision: it passes every gate on holdout B and all of G6c on both holdouts.
The two holdouts disagree on the ranking gates and their intervals overlap the bar on both sides
(A: [0.08, 0.67] for G6a; B: [0.50, 0.84]); with 75 conditions per split the evidence cannot
distinguish a true rho of ~0.55 from ~0.60. The threshold is a point-estimate bar and was not moved.

### Against the baseline and the oracle (descriptive)

* **ts_points beats Game Score in all four ranking comparisons** (G6a A 0.394 vs 0.365, B 0.697 vs
  0.531; G6b A 0.541 vs 0.407, B 0.657 vs 0.576). In G6c the Game Score lift is +0.04 [-0.02, +0.12]
  on A (indistinguishable from zero) and +0.15 [+0.05, +0.25] on B.
* **Measured-impact oracle** (CPU rule on measured single-player impacts): lift A +0.09 [+0.03, +0.17],
  B +0.18 [+0.10, +0.28] (ratio rule); +0.07 / +0.21 (difference rule). The formula is not worse than
  the oracle on A and about equal on B (recall on B: formula 0.75, oracle 0.89-0.93, intervals
  overlap). The additive-measured package predictor reaches rho 0.825 (A) / 0.781 (B): the
  limiting factor is the formula's ranking, not additivity. Interaction (package minus sum of
  singles) is +0.37 on B and -0.16 on A with sd ~1.1, i.e. no stable non-additivity.
* Other registered specs (descriptive only, never promotable): ts_points_nonshooting G6a 0.365/0.649,
  G6b 0.503/0.602; non-shooting control 0.206/0.389 and 0.282/0.365.

### What G6c does and does not show

* Calibration of predicted value change against the measured margin change is close to 1:1 on A
  (quintiles: -1.05/-0.21/+0.08/+0.67/+1.63 predicted vs -1.16/-0.41/+0.22/+0.90/+1.74 measured) but
  only at the extremes on B (-1.27/-0.34/0.00/+0.43/+1.42 vs -2.14/-0.25/-0.15/-0.10/+1.52; the
  middle quintiles are flat).
* **Most trades barely matter in this engine**: 57% (A) and 52% (B) of proposals are statistically
  unresolvable (|change| < 2 SE), and 80% / 74% of all proposals do not hurt the CPU team. A rule
  can look safe for that reason alone, which is why the lift over the base rate, not the raw success
  rate, is the headline. Tau sensitivity (P(preserve|accepted), lift, recall): A 0.5: .89/.21/.80,
  1.5: .98/.12/.95; B 0.5: .83/.21/.74, 1.5: .94/.09/.81.
* **Star-for-depth is context dependent, not a general engine rule.** When the CPU gives its star for
  2-3 depth players, the measured change averages +0.77 on A (the rule accepts 80% of these) but
  **-1.51 on B** (the rule accepts 37%; accepted ones still average -0.72, P(preserve|accepted) 82%).
  "A star is worth little in this engine" (Section R1) holds for 2024-25 rosters and is weaker in
  2016-17. This is the banked engine limitation showing up unevenly; it was not compensated for.
* Measurement noise: SE per proposal ~0.55; effects are 1-2 points. Classification at tau = 1.0 is
  therefore fuzzy for roughly half the proposals.

### Known limitations of this evidence

Two seasons and 15 teams each; one rule threshold (1.1x) evaluated, not tuned; replacement level for
ts_points is slightly negative (-0.23) and ~40% of 25+ MPG players have value 0 on efficiency alone;
the spec ignores rebounding, passing and defense by construction; ground truth is a healthy-roster
counterfactual in `DRAMA_M3`, not `DRAMA_M3_SEASON` with random availability; the oracle is itself
noisy (SE ~0.4 per player). No CPU-to-CPU trades, endpoint, UI or production behaviour was touched.

## R4. Options (decision for Xavier; none taken)

1. **Ship a limited version** on the strength of G6c (both holdouts) and the efficiency spec beating
   the baseline everywhere, with the strict-gate miss and the engine limitation disclosed. This
   requires an explicit override of the pre-registered ranking bars.
2. **Gather confirming evidence first**: register the identical criteria on one more unseen season
   (and/or the remaining teams) *before* running it, keep the model frozen, and decide on the
   combined holdouts. No tuning; costs ~35 min of compute per season.
3. **Use the measured-impact table directly** (per-season with/without values): ground truth by
   construction, but opaque and expensive, and it performed no better than the formula in G6c.
4. **Prioritise the engine work** (muted/variable star impact) before trades matter, then re-validate.
