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

---
## Amendment 1 — what the backfill validation showed (made before any fitting)

Source: `validation/ratings/results/backfill_validation.json` and a follow-up comparison of a fresh
re-derivation (isolated database) against production's stored attributes for overlapping seasons.

**F1. Ingested statistics are reproducible.** For the six seasons that overlap production
(2013-14, 2016-17, 2019-20, 2020-21, 2024-25, 2025-26) every season-stat row, player set and game count
matches exactly; shot-location inputs are identical in every checked season; the raw payload store
verifies (117 payloads, 0 corrupt).

**F2. Observed *attributes* are not a pure function of the stored statistics.** Re-deriving a season
with the current code reproduces ten attributes **bit-for-bit** (three_point, free_throw, mid_range,
passing, steal, block, offensive_rebound, defensive_rebound, plus the raw metrics behind them) but not:
`overall_rating` (differs for ~50% of players), `perimeter_defense` / `interior_defense` (2024-25: mean
absolute difference ~6 points, up to 38), `close_shot` / `layup` / `dunk` (small), and `clutch_rating`
(up to 41). Causes found: (a) these ratings read `players.position`, a **mutable current-snapshot
field**, so a past season's rating depends on today's label (the backfill database has none, production
has them for active players); (b) tracking-defense inputs for the latest season differ between ingestion
times (2024-25: ~550 of 569 rows, max 2.4 attempts per game), while older seasons are identical; (c)
clutch data is not stored with the stats and could not be reconciled. This is direct evidence for the
versioning requirement: **the stored attribute rows are the only faithful record of what a historical
simulation used**, so `observed-v1` must point at them and never be re-derived.

**F3. Season windows.** 2019-20 has only 971 games in the schedule table (the bubble restart falls after
the 15 July season-window cutoff) while its player statistics include them (max 74 GP). 2020-21 has
1,080 games (72 per team). All other seasons have 1,230 games and 82 games per team.

**F4. Identity.** 1,706 distinct players across 13 seasons; 6 ids carry more than one name (renames)
and 2 names are shared by more than one id (homonyms); bio data covers 100% of players with stats in
every season. Ids are stable; names are not a key.

### Changes to this protocol (binding from here on)

1. **Primary targets are the reproducible quantities only:** the eight box-score attributes above, the
   raw skill metrics, usage/tendencies, and minutes/games played. Decisions (adoption, selection,
   the 3% rule) are made on these.
2. **Flagged secondary targets:** `overall_rating`, `perimeter_defense`, `interior_defense`,
   `close_shot`, `layup`, `dunk`, `clutch_rating`. Reported per category, never used to select or adopt
   a method, with the reason stated.
3. **Backtest targets are re-derived uniformly in the isolated database** (one code version, position
   unknown for every player, recorded as `observed-bf1`), not taken from production's stored mix of code
   versions. The backtest therefore measures prediction of a consistently defined target; its relation to
   what production simulations used is documented, not assumed.
4. **Position is an explicit, dated rating-set input.** Wherever a preseason rating needs a position, it
   comes from a position input with provenance (the height/weight proxy for backtests; the roster
   snapshot for live use), never from `players.position`.
5. **2019-20 is excluded from schedule-based end-to-end tests** (its schedule table is truncated). It
   remains a player-level development target (its statistics are complete). 2020-21 stays, flagged.
6. The locked test set (2022-23 .. 2025-26) is unchanged.

---
## Amendment 2 — evaluation contract (Xavier's review, 2026-10-23; before any fitting)

*Supersedes the uncertainty wording in Section 7 and adds the end-to-end, minutes, availability,
as-of and gate definitions. Nothing has been fitted.*

### A2.1 Statistical dependence, and what four locked seasons can support

Player-season rows are **not** independent. Three dependence structures are handled explicitly:
repeated observations of one player (across seasons), shared roster/system within a team-season, and
common shocks within a season. For every comparison "method M vs baseline B" the statistic is the
**paired relative reduction in mean absolute error** (same player-seasons under both methods), and three
interval estimates are reported:

| id | resamples | captures |
|---|---|---|
| E1 | **players** (all of a player's seasons together), 2000 draws | repeated measures of the same player |
| E2 | **(season, team) units**, 2000 draws | shared roster and system effects |
| E3 | the **per-season** relative reductions: mean +/- t(df = n_seasons - 1) interval (3 d.f. on the 4 locked seasons, t = 3.18; 7 d.f. on the 8 development seasons, t = 2.36) | common season effects |

The **primary interval is the widest of E1, E2, E3.** Row counts never feed the interval directly.
The per-season table is always printed (every season's own reduction), and **leave-one-season-out**
ranges are reported.

**Stated limitation.** Four locked seasons give 3 d.f. for the season-level component, so a modest true
improvement cannot be resolved there. Before the locked set is opened, the minimum detectable effect
(MDE) for 4 seasons is computed from the development seasons' between-season spread and published. If
the MDE exceeds the 3% threshold, the statement is simply that the locked set cannot support adoption of
a small effect, and **the simpler baseline ships.** No interval is narrowed by counting correlated rows
as independent samples.

**Adoption requires all of:** (i) pooled relative reduction >= 3%; (ii) the primary (widest) interval
excludes zero; (iii) the reduction is positive in >= 3 of the 4 locked seasons (and >= 6 of 8 development
seasons, as development evidence); (iv) the category rule in A2.7. The locked set is the confirmation;
the 8 development seasons (rolling origin) are reported alongside as the larger but selection-exposed
sample, and are labelled as such.

### A2.2 Rating accuracy is separate from simulation accuracy

Predicting the pipeline's own attributes does not show realistic simulations. They are judged by
**separate criteria** (A2.7, groups B and D). End-to-end evaluation uses **matched seeds and the real
schedule** of season T (2019-20 excluded: truncated schedule table) and four arms:

* **D** flat-default ratings: the roster and projected minutes of arm P, with every skill rating at the
  pipeline's flat default (what the unmodified gate produces at season start);
* **P** the frozen preseason model;
* **O_r** observed season-T ratings with arm P's roster and projected minutes (isolates the *ratings*);
* **O** observed season-T ratings and observed stat lines (today's historical pipeline).

Metrics, per season and pooled, over N = 10 matched league runs: standings Spearman rho against the real
standings, win-total spread (share of the real spread retained), head-to-head of the real best vs worst
team (100 games), and player production (simulated vs real PPG/RPG/APG/MPG for the top-80 players by
real minutes: MAE and rank correlation; star MPG). The remaining gap is quantified as
(P - D) / (O_r - D) and (P - D) / (O - D) with interval from the run seeds.
**O and O_r use season-T outcomes. They are look-ahead comparisons, not deployable forecasts, and not a
guaranteed ceiling:** P could beat them on a metric (observed ratings carry that season's noise, and the
engine has its own errors). They are reference points only.

### A2.3 Minutes and roster reconstruction

The 240-minute normalisation is a construction, not a validation. Reported for every season: player-level
projected vs actual MPG (MAE for players with >= 10 games, bias by role/rank, rank correlation); team-level
rotation shape (top-5/top-8 minute share vs actual); **extremes** (any projected MPG above 40 or below 0;
share of players projected >= 15 MPG who actually played < 5; players who played >= 20 MPG but were
projected < 10); and reconstruction quality.

**Reconstruction.** Historical preseason rosters were never captured; the proxy is a player's team in his
first appearance in season T (from game logs). Two variants are reported: **R-early** (first appearance
within the team's first 10 games; the primary variant) and **R-all**. Diagnostics, per season: share of
the reconstructed roster who are late arrivals (first appearance after game 10) and their minute share;
**minutes played by players not on R-early** (unprojectable minutes, as a share of team minutes); and the
fact that **players who were on the real preseason roster but never played are invisible** (the game logs
list only players who appeared), so rosters are systematically missing deep-bench and injured players.
These limitations are printed next to every V5 and end-to-end result and are never described as roster
accuracy.

### A2.4 Availability: a separate, minimal baseline

Availability (probability and share of games played) is **in scope at the smallest validatable level** and
is never inferred from expected minutes. Expected minutes per team game decompose as
`P(plays) x MPG when playing`; each factor and the product are evaluated separately.

* **A0** last season's games-played share (clipped to [0.05, 1]); league mean for players with no prior
  season. No fitted constants.
* **A1** A0 shrunk toward an (experience band) mean with a data-estimated weight.
Targets: realised games-played share (MAE), calibration (decile reliability of predicted vs realised
share), and Brier score for "plays >= 50% of team games". Injury timing, load management and in-season
updates are out of scope. A1 is adopted only under the A2.1 rule; otherwise A0 ships.

### A2.5 As-of correctness: when a fact was true vs when it was knowable

Every input declares `data_through` (the date its content describes) **and** `knowable_from` (the earliest
date the system could have had it), plus a fidelity class: **exact**, **retrospective proxy**, or
**revision risk**. The leak rule is `knowable_from <= information_cutoff` (cutoff = the day before the
first game of T). Input registry:

| input | true time | knowable from | fidelity | note and sensitivity run |
|---|---|---|---|---|
| season-(T-1) box-score stats | last game of T-1 | day after it | exact | none |
| season-(T-1) tracking defense, shot location | same | same | **revision risk** | latest-season values were revised after the fact (R-F2); sensitivity: box-score-only features |
| season-(T-1) team stats | same | same | exact (low revision risk) | none |
| birth date, draft year/round/pick | birth / draft night | draft night | exact (fetched retrospectively, immutable) | none |
| height, weight | listing at season (T-1) | that preseason | **retrospective proxy** (fetched now for that season) | sensitivity: drop size features |
| position | n/a | n/a | **forbidden**; size-derived proxy only | `players.position` is a current-snapshot field |
| preseason roster membership | preseason | first-game date | **retrospective proxy** (first appearance, A2.3) | sensitivity: R-early vs R-all |
| player id mapping | n/a | n/a | retrospective, stable (identity audit) | none |
| transactions, injuries, depth charts | various | not captured | **not used** | cannot be backtested |

The report lists, for every result, which non-exact input classes it depends on, and the sensitivity runs
show how much changes without them. The metamorphic canary (A2.6) guarantees no season-T-or-later *data*
is used; this registry covers what the canary cannot: whether the information used was knowable *then*.

### A2.6 Production compatibility

`observed-bf1` (the backtest target) is re-derived uniformly in the isolated database with one code version
and position unknown for every player. It differs from the attributes production actually stored, as
measured in `validation/ratings/results/observed_bf1_vs_production.json`: eight box-score attributes are
identical; `overall_rating`, `perimeter_defense`, `interior_defense`, `close_shot`, `layup`, `dunk` and
`clutch_rating` differ (R-F2). **No legacy data is overwritten or "cleaned" for the backtest:** production
rows are untouched, historical run inputs remain reproducible from them (`observed-v1` points at the
stored rows), and the backtest never writes to the production database. Backtest conclusions concern the
consistent target; they are not claims about the stored historical ratings.

### A2.7 Consolidated acceptance gates (HARD = blocks adoption/shipping; DIAG = reported, never selects)

**A. Leakage and reproducibility**
| gate | kind |
|---|---|
| A1 metamorphic canary: predictions bit-identical when all data of season >= T is replaced by garbage, for every target | HARD |
| A2 as-of reader raises on any read of season >= T or of a forbidden input; negative tests (deliberately leaky predictors) are caught | HARD |
| A3 input manifest audit: every input's `knowable_from` <= cutoff; no observed set of season >= T used | HARD |
| A4 production data unchanged by backtest work (row hashes before/after) | HARD |
| A5 raw-store integrity (hashes verify); the run reproducible from the manifest | HARD |
| A6 proxy sensitivity runs executed and reported (A2.5) | DIAG |

**B. Player-level rating and raw-metric prediction (primary targets: eight box-score attributes, raw skill
metrics, usage/tendencies)**
| gate | kind |
|---|---|
| B1 adoption: pooled MAE reduction >= 3% vs the baseline in the family, primary interval excludes 0, >= 3 of 4 locked seasons positive (A2.1) | HARD |
| B2 category rule: no **critical category** (V1, Y1, R-A, R-B, L; each with >= 50 player-seasons) is worse than the baseline by more than 2% (point estimate); if one is, the method is **not adopted for that category** and the regression is shown beside the aggregate | HARD |
| B3 secondary targets (overall, defense, close shot/layup/dunk, clutch) | DIAG |
| B4 results for all categories and per season, including where the method loses | DIAG |

**C. Minutes and availability**
| gate | kind |
|---|---|
| C1 team minutes sum to 240 +/- 1 and no projected MPG < 0 or > 40 | HARD |
| C2 V5 minutes adopted only under B1/B2-style rule for MPG MAE; availability A1 only if it beats A0 under the same rule | HARD |
| C3 minutes plausibility report (extremes, false starters, missed rotation, unprojectable minutes, late arrivals, unobservable players) | DIAG, must accompany any result |
| C4 availability calibration (decile reliability, Brier) | DIAG |

**D. End-to-end simulation**
| gate | kind |
|---|---|
| D1 P beats D on standings rho with matched seeds, pooled and in >= 3 of 4 locked seasons | HARD |
| D2 P is not materially worse than D on standings spread, head-to-head or player-production error (point estimate worse by > 5% => flagged and blocks) | HARD |
| D3 gap to O_r and O (fraction of the D-to-O gap closed), spread/H2H/player-production vs O | DIAG (look-ahead reference) |

**E. Uncertainty calibration and category regressions**
| gate | kind |
|---|---|
| E1 if intervals are published, empirical coverage of the stated 50% and 80% intervals within +/-10 percentage points, pooled | HARD (if published) |
| E2 coverage per category, and any category whose regression B2 triggered | DIAG, shown beside aggregates |

If a method improves the aggregate but fails B2, C or D2, the report leads with the failing item. The
aggregate is never allowed to hide it.

### A2.8 Freeze checklist (the locked set stays closed until every item is done)

1. As-of reader, canary and negative tests pass (A1-A3). 2. Coverage and reconstruction diagnostics for
all targets are reported (this amendment's data checks). 3. Development fitting and the MDE for 4 seasons
are complete. 4. Method identifiers, fitted constants and their hashes are committed. 5. Only then is the
locked set evaluated, once.

## Amendment 2 findings (recorded 2026-11-01, before any fitting; locked set still closed)

**Gates A1–A3 (as-of guard, metamorphic canary, manifest audit):** pass on all 12 targets with the V1 reference predictor (`results/canary_run.json`).

**Coverage (`results/coverage_checks.json`):** critical categories below the 50 gated-player-season floor: R-A locked 45; R-B dev 55 / locked 34. Reported as limitations with wide intervals; not pooled across categories. R-C (locked 29) and R-D (locked 10) are too thin for any claim.

**Roster reconstruction:** 13–27% of players first appear after game 10, carrying 3–7% of minutes; 6–15 top-10-minute players per season (summed over teams) are absent from the early-roster proxy. The end-to-end roster arm must report these limits.

**Production compatibility (A2.6, `results/observed_bf1_vs_production.json`):** box-score attributes are identical in 2013-14, 2016-17, 2019-20, 2020-21 and 2024-25. In 2025-26, 64 rows differ, **all in `mid_range`**. Cause: production's 2025-26 `player_season_stats` has 57 players with null `mid_fga` (shot-location data incomplete), so `_raw_mid_range` falls back to the 2PT% proxy; the backfill has shot-location data for all 582. This is a production data-completeness gap, not a backfill defect. Consequence: observed-bf1 for 2025-26 is the better reference, and production-stored 2025-26 mid_range should not be treated as comparable. Not a blocker for using 2025-26 as a locked target, provided `mid_range` is read from the backfill.
