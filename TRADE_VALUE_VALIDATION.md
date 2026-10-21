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
