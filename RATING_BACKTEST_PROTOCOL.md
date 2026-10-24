# Preseason rating backtest protocol (pre-registered; no method has been fitted)

**Status: PROTOCOL, committed before any rating method is fitted or any evaluation is run.**
Purpose: decide, with evidence, how to initialise player ratings, minutes and availability for a
season for which little or no current-season data exists, without any future information
leaking into the estimate. Companion to `DESIGN_2026_27_ROSTERS_AND_RATINGS.md`.
Commit order is the audit trail: protocol -> frozen method -> locked-test results.

## 1. Rating types and the leakage rule

| kind | derived from | `information_cutoff` | may feed |
|---|---|---|---|
| `observed` | the season's own performance | on or after the season's last game | legacy reproduction; **evaluation targets only** |
| `preseason` | only information that existed before the season's first game | strictly before the first game | simulations of that season; backtests |
| `in_season` | information up to a stated date within the season | on or after the first game, before the season's end | simulations from that date on |

**The rule:** an `observed` rating set of season *T* (or any data from *T* or later) is never an
input to a `preseason` or `in_season` set for season *T*, nor to any rolling-origin evaluation of
season *T* other than as the *target*. This is enforced mechanically, not by convention (Section 6).

## 2. Data window and splits (fixed before fitting)

Contiguous seasons 2013-14 .. 2025-26 (13 seasons), subject to the coverage report in Section 8.
A season is a *target* *T* only if season *T-1* is also present (so 12 targets).

* **Development targets**: 2014-15 .. 2021-22 (8). All fitting, parameter estimation and method
  selection use only these, and only information from seasons < *T* for each.
* **Locked test targets**: 2022-23, 2023-24, 2024-25, 2025-26 (4). Untouched until the method is
  frozen and committed. Evaluated once.
* **COVID seasons** (2019-20 truncated, 2020-21 shortened) are development targets and are reported
  separately; they are not in the locked set.
* Rookie classes follow the same split: a class is "rookie season = *T*".
* Frozen-parameter mode (primary): constants estimated on development data only and used unchanged
  on every locked target. Expanding-window mode (secondary): the *procedure* is frozen and constants
  are re-estimated from all seasons < *T*. Both are reported; the primary mode decides.

## 3. What is predicted (targets), and for whom

Per player-season (*p*, *T*), against what our own pipeline derives from season *T*:

1. **Attribute ratings (0-99)** for each derived skill, versus the `observed` set of *T*, for players
   who meet the pipeline's own volume gate in *T* (>= 20 GP, >= 15 MPG). Predicted ratings are mapped
   through the percentile pool of *T-1* (never *T*).
2. **Underlying raw skill metrics** (3P%, FT%, rim/mid FG%, assist and rebound rates, steal/block
   rates) with their season-*T* attempt volume as weight.
3. **Tendencies** (usage, three-point rate).
4. **Minutes per game and games played**, for **every** player on the preseason roster, not only
   those who clear the gate (minutes are what drives the simulation).
5. **End-to-end** (acceptance stage, after implementation): full-league audit standings rho and
   head-to-head, simulating *T* with (a) the preseason set, (b) the observed set (the look-ahead
   ceiling), (c) the current behaviour (flat defaults). This measures the real preseason accuracy gap.

## 4. Categories (every result is reported per category)

Veterans: (V1) experience >= 4 and age < 30; (V2) age >= 30; (V3) changed team since *T-1*;
(V4) injury/absence return (GP in *T-1* below 40% of team games with a normal *T-2*);
(Y1) second year; (Y2) third year or age <= 24 with experience >= 2;
Rookies by draft bucket: (R-A) picks 1-14, (R-B) picks 15-30, (R-C) second round,
(R-D) undrafted or international/other; and (L) low-information: < 500 minutes in the prior season.
A player can be in several categories; each category is evaluated on its own members, and the
sizes are reported. No single aggregate decides anything.

## 5. Methods under comparison (closed set; additions require an amendment)

**Established players**
* **V0** current behaviour (flat defaults until the volume gate; end-to-end stage only)
* **V1** last season's observed raw metric, unshrunk, mapped through the *T-1* pool
* **V2** V1 shrunk toward a (position-proxy, age-band) prior by volume, with a *generic* fixed constant
* **V3** V2 with per-skill reliability constants **estimated from development data** (split-half stability)
* **V4** V3 plus an empirically estimated age curve per skill
* **V5** V4 plus minutes/games projection renormalised to 240 minutes per team over the preseason roster

**Rookies**
* **R0** position-proxy average of past rookies
* **R1** draft-slot-bucket x position-proxy means, estimated on past classes
* **R2** R1 plus age at draft and height/weight (ridge regression)

Pre-NBA (college/international) data, external projections and any other covariates are out of
scope for this protocol.

**Position proxy.** `players.position` is a *current-snapshot* field (null for 1,873 of 2,403
players) and is **forbidden** in backtests. The proxy is a height/weight class from bio data of
season *T-1* (a static fact), with a documented fallback.

**Preseason roster approximation.** Historical preseason rosters were never captured, so each
player's team in *T* is the team of his first appearance in *T* (game logs). This approximation is
documented and affects only the team-change category and the minutes renormalisation (V5).

## 6. Leakage prevention (enforceable)

1. **As-of access layer.** Backtest code never receives a database session. It receives an
   `AsOfStore(target_season=T)` that exposes only permitted reads (stats and facts from seasons < T,
   static facts known before T) and raises `LeakageError` on anything else, including any
   `observed` rating set of season >= T. Predictions are produced only through this object.
2. **Metamorphic canary.** For every target T, the preseason output must be **bit-identical** after
   replacing all season-T-and-later data with garbage (shuffled or zeroed). A single changed value
   fails the run. This is the primary guard against subtle leakage.
3. **Input manifest.** Every rating set records its inputs, each with a declared `data_through`
   date; a set is rejected at sealing if any input is later than its `information_cutoff`, and a
   sealed set is immutable (database triggers).
4. **Fitted constants** (reliabilities, age curves, rookie priors) are computed by functions that take
   an `AsOfStore`, so a constant used for target T can only have seen seasons < T.
5. **Forbidden inputs list**, asserted by test: current `players.team_id`, `players.position`,
   any `ROSTER_STATUS`/team field from a current directory snapshot, any season-T-or-later stats,
   attributes, tendencies, team stats or game logs.

## 7. Procedure and decision rules

1. Commit this protocol. 2. Build the as-of layer and canary tests; they must pass before fitting.
3. Fit and select on development targets only: rookies by **leave-one-class-out** over development
   classes, veterans by **rolling origin** over development targets.
4. **Selection rule:** among methods in a family, choose the simplest whose development error is
   within one standard error of the best. A method more complex than the baseline is adopted only
   if, on held-out data, its mean absolute error is lower by **>= 3% relative** with a bootstrap 95%
   interval that excludes zero, in the categories it is meant to help.
5. Freeze: commit the method identifiers, all fitted constants and their hashes. 6. Evaluate once on
   the locked targets, report every category (including where it loses), and do not re-tune.
7. If the simple baseline is not beaten, the baseline ships and that is the result.

Uncertainty: any interval the method produces is checked for calibration (empirical coverage of the
stated 50% and 80% intervals), reported by category. Rookie estimates are published as central values
with wide, honest intervals and a `rating_source` flag; nothing is presented as more precise than the
backtest supports.

## 8. Prerequisites (must be reported before any fitting)

The backfill coverage and identity report: for each of the 13 seasons, row counts for players, team
stats, games, attributes, game logs, shot-location and defense coverage, bio and draft data; identity
consistency across seasons (stable ids, name changes, duplicates, multi-team rows); and agreement with
production for the overlapping seasons. Any season that is missing or unreliable is excluded or
flagged here **before** it is used to draw a conclusion.
