# Design proposal — 2026-27 rosters and player-rating initialization

**Status: PROPOSAL for review. Nothing here is implemented; no production change was made.**
Written 2026-10-23 from a read-only audit of the repo and database, live read-only calls to the
NBA stats API, and a licensing check of the primary sources. Claims are tagged **[verified]**
(checked this session in code, DB or a primary source) or **[unverified]** (background knowledge or
third-party description; must be confirmed before relying on it).

Two deliverables are kept separate on purpose, because they fail independently:

* **A. Roster accuracy** — who is on which team, and who exists at all (rookies, free agents).
* **B. Rating accuracy** — what each player's attributes, tendencies and minutes should be.

A perfect roster with poor rookie ratings is a valid and expected intermediate state.

---

## 1. Summary of findings

1. **The pipeline cannot simulate a new season as built.** Every skill rating is a percentile
   within the *current* season among players with >= 20 GP and >= 15 MPG
   (`rating_engine.SKILL_CONFIGS`, `compute_ratings_for_attribute`). At the start of a season nobody
   passes, the pool is empty, and the code returns the flat default (40 or 50) for **every** player.
   Ratings only become meaningful around game 20 and then use **no prior-season information**.
   [verified: code]
2. **A player with no current-season stats row cannot be loaded at all.** `load_roster` inner-joins
   attributes, tendencies *and* `PlayerSeasonStats` for the season. Rookies and returning-from-absence
   players have no row until they play. [verified: code]
3. **`Player.team_id` cannot be trusted as "current roster".** 1,821 of 2,403 players carry a
   `team_id` but have no 2025-26 stats row, and `ingest_active_players` never clears a player who
   left a roster, so a waived or released player keeps a ghost team. [verified: DB + code]
4. **We discard data we already download.** The roster call (`CommonTeamRoster`) returns birth
   date, age, height, weight, experience (`R` = rookie) and school; we store only id, name, team,
   position. `Player` has no age, size, draft, college or country fields. [verified: code + live call]
5. **Nothing is versioned.** No table has `ingested_at`, source or version columns (only
   `simulation_runs`/`myleague_*` have `created_at`). Ingestion upserts in place, `seed_player_attributes`
   overwrites, and MyLeague replays re-simulate from the **live** database. A re-ingest or re-seed can
   therefore silently change what a past run "was". [verified: DB + code]
6. **The headline standings accuracy (rho ~0.91) uses same-season ratings, i.e. it looks ahead.**
   A genuine preseason simulation will be less accurate by construction. The backtest in Section 6
   quantifies that gap; it should reset expectations before anything ships. [verified: harness uses
   the season's own stats; the size of the gap is unknown]
7. **Backtesting needs data we do not have yet.** The ingested seasons are 1996-97, 1998-99,
   2000-01, 2003-04, 2005-06, 2007-08, 2013-14, 2016-17, 2019-20, 2020-21, 2024-25, 2025-26: only two
   consecutive pairs (19-20 to 20-21, 24-25 to 25-26). Predicting a season from the one before needs
   contiguous seasons (about 7 more, 2013-14 to 2025-26). [verified: DB]

---

## 2. Audit — where the workflow assumes historical stats

| # | Location | Assumption | Effect on a brand-new season |
|---|---|---|---|
| 1 | `rating_engine.compute_ratings_for_attribute`, `SKILL_CONFIGS` | >= 20 GP, >= 15 MPG, percentile within the same season | empty pool -> all ratings = defaults; no carry-forward from prior seasons |
| 2 | `jobs.seed_player_attributes` | derives attributes and tendencies only from `PlayerSeasonStats` of that season | rookies/new players get nothing or defaults |
| 3 | `roster.RosterProvider.load` | inner joins attrs + tendencies + `PlayerSeasonStats` (this season) | players without a stats row are dropped |
| 4 | `roster.CURRENT_ROSTER_SEASONS = {"2025-26"}` | hard-coded season set that uses `Player.team_id` | must be edited per season; membership comes from a stale field |
| 5 | `roster._build_roster` | minutes = MPG x GP, normalised to 240 | with few games, minute shares are tiny-sample; with 0 GP the weights are all 0 |
| 6 | `roster._league_zone_prior`, `season_context.build_season_context` | league priors built from this season's stats rows | no priors at season start; falls back to attribute-derived values |
| 7 | `game_simulator._load_team_stats`, `league_avg_def` | pace/def_rating/OREB% from `TeamSeasonStats` | absent -> league-average pace and defense (every team identical) |
| 8 | `availability.season_active_target`, `calibrate_avail_prob` | per-player GP and the season's active-roster target from game logs | no logs yet |
| 9 | `jobs.ingest_active_players` | upserts players on today's rosters; never removes | stale `team_id`; free agents/unsigned players never appear; position only for active-roster players |
| 10 | `jobs.ingest_season_stats` | upserts a stats row in place | no history of a row's earlier values or source |
| 11 | `myleague_*`, `season.py` drill-ins | replays re-run `simulate_game` from live tables | a rating refresh mid-run changes past box scores on replay |
| 12 | `trade_value.py` (M-6 work) | value read from `PlayerSeasonStats` production | needs a *projected* production source in the preseason (the one concrete dependency; see Section 9) |

Identity is **not** a problem: `Player.id` is the NBA `PLAYER_ID`, stable across teams and seasons,
and historical players are already created from stats rows. [verified: code]

---

## 3. Data sources

| need | source | coverage | access | constraints | status |
|---|---|---|---|---|---|
| current rosters + bio (age, height, weight, school, experience, position) | `CommonTeamRoster` (already used) | all 30 teams, incl. rookies (`EXP = R`) | live call returned BOS 2026-27: 17 players, 0.3 s | see licensing note | **[verified]** |
| league-wide directory incl. free agents, draft year/round/pick, college, country, roster status | `PlayerIndex` | 5,198 players; 2026 draftees present (Dybantsa #1, Peterson #2, Boozer #3, Wilson #4) | live call, 0.9 s | `ROSTER_STATUS` (1.0 / None) is the real active signal; `TEAM_ID` is not reliable as a flag | **[verified]** |
| draft history, drafting org (college/club) | `DraftHistory` | every draft by season | library endpoint exists | same | [verified: columns]; not called |
| combine measurements, athletic tests | `DraftCombineStats` | only players who attended | library endpoint exists | sparse; many drafted players missing | [verified: columns]; coverage unverified |
| age per season for historical backtests | `LeagueDashPlayerBioStats` (AGE, draft, college) | per season, players with stats | library endpoint exists | one call per season | [verified: columns] |
| G League stats | `LeagueDashPlayerStats` with `league_id_nullable` | NBA affiliate league | parameter exists in installed library | not called; coverage unverified | **[partially verified]** |
| transactions (signings, waivers, trades) | **no official API found**; third parties RealGM, Spotrac list them | 2026-27 entries through at least mid-July 2026 | web pages only | scraping terms unreviewed | [verified: they exist]; terms **[unverified]** |
| pre-NBA performance (college) | Barttorvik via `cbbdata`/`toRvik` (free key); NCAA stats site; Sports-Reference CBB | D-I since 2007-08 (Barttorvik, per package docs) | free API key / scraping | Barttorvik terms **not found**; Sports-Reference allows <= 20 requests/min and says it cannot provide data downloads | **[unverified]**; do not depend on it for the MVP |
| international (EuroLeague etc.) | EuroLeague public stats | partial | not investigated | not investigated | open |
| preseason projections (DARKO, EPM, etc.) | third-party sites | not investigated | proprietary | licensing unknown | **not recommended**; build our own, testable projection |
| BALLDONTLIE | commercial API | basic endpoints free (5 req/min per a third-party review); injuries need $9.99/mo; contracts $39.99/mo (per third-party guides, which conflict on other prices) | key required | **[unverified]**, no transactions endpoint found | not recommended as a dependency |

**Licensing — read this before deciding anything.** NBA.com's Terms of Use, Section 9 "NBA Statistics"
**[verified, fetched from nba.com/terms]**, say statistics "may only be used, displayed, or published
for legitimate news reporting or private, non-commercial purposes", may not be used "in connection
with any fantasy game or other commercial product or service", and may not be used "in connection with
any website, product, or service that features a database" without consent. The terms contain no
clause about automated access. For a private, non-commercial portfolio project this is workable. It
becomes a real constraint if the application is publicly hosted with an NBA-statistics database, or
monetised. This already applies to everything we ingest today; the new feature adds more of the same
data, not a new kind of exposure. It is not legal advice. Practical rules: do not commit data dumps to
the public repo, keep the database private, and keep the ingestion behind `nba_client.py` so the
source can be swapped.

**Recommendation on sources.** Use only the NBA endpoints we already depend on (roster, directory,
draft, bio) for the MVP; derive transactions from **roster snapshot diffs** instead of ingesting a
transaction feed; treat college/international data as an optional later phase gated on a successful
licensing review *and* a backtest showing it helps.

---

## 4. Proposed design

### 4.1 Deliverable A — roster accuracy

* **Store what we already fetch**: `birth_date`, `height_in`, `weight_lb`, `school`, `country`,
  `draft_year`, `draft_round`, `draft_pick`, `rookie_season`, `roster_status`, with `source` and
  `fetched_at`.
* **Effective-dated membership** (`roster_assignments`: player, team, `effective_from`,
  `effective_to`, status = active / two-way / inactive / free agent, source snapshot id) replacing
  the use of `Player.team_id` and `CURRENT_ROSTER_SEASONS`. A player's team changing creates a new
  interval, never a new player.
* **Snapshots, not overwrites**: each refresh stores a roster snapshot (hash of the raw payload,
  timestamp) and derives assignment changes by diffing against the previous snapshot. That diff
  *is* the transaction log (signed, waived, traded, converted to a two-way) without a third-party feed.
* **Validation of completeness**: 30 teams, each with 13-17 standard players (plus two-way flagged
  separately), no player on two teams, every roster player has a bio row, every 2026 draftee found,
  and a cross-check of `PlayerIndex` against `CommonTeamRoster` with conflicts reported (not silently
  resolved).
* **Compatible with M-6**: the trade overlay composes over a snapshot's membership at the run's start
  date; `canonical_base_team_of` generalises from "PSS row" to "snapshot membership".

### 4.2 Deliverable B — rating initialization

**Principle:** model **raw skill metrics** (3P%, FT%, rim FG%, assist rate, rebound rates, steal/block
rates, usage, minutes, availability), shrink them toward a prior with weight set by how quickly each
metric stabilises *in our own data*, and convert to a 0-99 rating through the **same percentile
mapping** the engine uses today, taken against the **prior season's pool** (so ratings stay on the
existing scale and a new season does not need its own pool).

`projected_i = (n_i * observed_i + n0_s * prior_i) / (n_i + n0_s)`

* `n_i` = the player's volume (attempts/possessions/minutes), pooled over prior seasons with recency
  and aging weights.
* `n0_s` = a per-skill reliability constant **estimated from data** (split-half stability across
  historical seasons), not hand-set. Fast-stabilising skills (FT%, rebounds) get small `n0`; slow ones
  (3P%, steals) large.
* `prior_i` = mean for the player's (position, age band, experience) cell.

| category | approach | fallback |
|---|---|---|
| returning veteran | multi-season shrinkage above, plus an empirically estimated age curve per skill | position/age prior if < 1 prior season |
| established player changing teams | same skill projection (skills travel); **role** handled separately by renormalising minutes to the new roster (below) | none needed |
| returning from injury or long absence | missing games are *no evidence*: volume `n_i` is simply smaller, so the prior carries more weight; never impute a decline from absence; availability prior from multi-year GP | flag `injury_return` |
| second-year / young | same model with age-band priors and a larger `n0` (more uncertainty, more growth) | draft-slot prior for the first NBA year |
| **rookie** | layered, each layer admitted only if the backtest says it helps: **L0** position x draft-slot-bucket prior estimated from past rookie classes; **L1** add age at draft and height/weight by regression; **L2** pre-NBA performance (college/G League), optional | undrafted/unknown: position prior with maximum uncertainty and `rating_source = fallback` |
| no reliable information | explicit, flagged fallback (position-average rotation player, wide uncertainty) | never silent: stored on the rating row and shown in the UI |

**Minutes and availability** are projections too, and matter as much as skill: prior-season MPG,
shrunk, then **renormalised to 240 per team** (with a per-player cap) over the new roster, so a team
that adds a star lowers everyone else's share. Rookies' minutes come from slot and team depth.
Projected games played from multi-year GP.

**Team-level inputs** (pace, defensive rating, OREB%): start from last season's values regressed toward
the league mean, then blend in the current season as games accumulate. (They are franchise-level in the
engine today; see Section 9 on coaching/roster changes.)

### 4.3 Evolution through the season

Treat the preseason projection as a **prior with pseudo-count** and add current-season evidence:

`posterior = (n0 * projection + n_obs * observed) / (n0 + n_obs)`

* At game 0, `n_obs = 0`: pure projection. As volume accumulates, the current season dominates, at a
  rate set by each skill's own stability constant. A hot week moves a fast skill a little and a slow
  skill almost not at all.
* This **replaces the hard volume gate** with graceful weighting, removing the cliff at game 20.
* A player who misses time accumulates little `n_obs`, so he is not penalised.
* Rebuild cadence: on demand or per advance, **never mid-run silently** (Section 7).

---

## 5. What we can and cannot estimate

| | reliability | basis |
|---|---|---|
| who is on which team now | **high** | official endpoints, verified live |
| returning veterans' skills | **high** | years of data; stable for most skills |
| minutes for veterans | **medium** | depends on roster changes and coaching, partly modelled |
| age/aging curves | **medium** | needs contiguous seasons we do not yet have |
| second-year and young players | **medium-low** | wide development variance |
| team-change effects on role | **low-medium** | minutes renormalisation only; no scheme model |
| **rookies' skills** | **low** | with draft slot, age, size only we can place a rookie in a broad band, not rank him precisely; pre-NBA performance might help but is unproven and its source is unverified |
| injuries and sudden changes | **not predictable** | handled by uncertainty, not forecast |

Expect rookie ratings to be **wide-uncertainty central estimates**, and report them that way.

---

## 6. Validation — retrospective, leak-free

**Rolling-origin backtests.** For each target season *T*: build ratings using only information
available before *T*'s first game, then compare with what actually happened.

* **Leak controls.** (a) Features only from seasons < T plus draft/bio facts that exist before the
  season. (b) Percentile mapping uses season *T-1*'s pool. (c) Every fitted constant (reliabilities,
  aging, rookie priors) is estimated from seasons < T only (expanding window). (d) Hyper-parameters
  frozen on early classes, evaluated on later ones, **pre-registered** in the style of
  `TRADE_VALUE_VALIDATION.md` (commit the protocol before running). (e) Preseason rosters are
  approximated by each player's first-appearance team (the game logs), because historical *preseason*
  snapshots do not exist; this approximation is itself documented.
* **Targets.** Primary: rating error versus the attributes our own pipeline derives from season *T*
  (MAE and Spearman per attribute, on players above the volume gate). Also: minutes-share error,
  games-played error. **End-to-end:** run the existing full-league audit with the preseason rating
  set and compare standings rho and H2H against (i) the oracle (same-season ratings) and (ii) today's
  behaviour (defaults) — this gives the real preseason accuracy gap.
* **Veteran strategies compared:** S0 current behaviour; S1 last-season carry-forward; S2 shrinkage +
  aging; S3 S2 + minutes renormalisation to the new roster.
* **Rookie strategies compared:** R0 position average; R1 slot x position prior; R2 R1 + age and size;
  (R3 with pre-NBA data only if admitted). Evaluated on every available rookie class using
  leave-one-class-out, against the rookie's actual first-season attributes and minutes. Report error
  stratified by draft slot (lottery / first round / second round / undrafted), because averages hide
  that lottery picks and fringe players behave differently.
* **Calibration of uncertainty**: the stored uncertainty must be honest (a rookie interval that covers
  the truth about as often as advertised).
* **Decision rule.** A method ships only if it beats the simpler baseline on held-out classes with an
  interval excluding zero; otherwise the simpler method stays and we report that.

---

## 7. Schema, reproducibility and behaviour

**Reproducibility requirement.** A simulation must be reproducible from an identifiable **roster
snapshot + rating set**, not from "whatever the tables say today".

* New concept `rating_sets` (`id`, `season`, `kind` = observed / preseason_projection / in_season_blend,
  `as_of_date`, `method_version`, `input_manifest_hash`, `created_at`); player attribute/tendency rows
  gain `rating_set_id` (and provenance: `rating_source`, uncertainty / effective sample size).
* New `roster_snapshots` + `roster_assignments` (Section 4.1).
* `simulation_runs.parameters` records `roster_snapshot_id` and `rating_set_id`; every consumer
  (`load_roster`, MyLeague `advance_to`, drill-in replays, the preview) resolves rosters and ratings
  **through those ids**. Ratings for a run are **pinned at creation**; a refresh is an explicit new
  rating set with an effective date, and replays use the set that was in force on the game date.
* **Historical seasons are untouched.** A migration backfills one `observed-v1` rating set per existing
  season from the current rows and defaults every existing code path to it, so behaviour is identical.
  The gates: the 198-hash `simulate_game` fence, the box-score fixture, a no-change MyLeague advance,
  and the full-league audit (rho 0.913) must be byte/number-identical after the migration.
* Ingestion becomes **append-only** (raw payload hash + `fetched_at`); `force` re-seeding writes a *new*
  rating set rather than overwriting.
* Simulation behaviour changes only where a new season is explicitly selected; the volume gate and
  percentile pool are replaced by the blended method **only for rating sets of kind projection/blend**.
* Cost/risk: a real migration (new tables, new columns, changed uniqueness) and a roster-provider
  rewrite. This is the biggest piece of engineering, and it is mostly mechanical.

---

## 8. Phased plan (each a PR-sized, independently reviewable step)

| phase | scope | validation / gate | rough size |
|---|---|---|---|
| **0 — foundations** (no behaviour change) | store bio/draft fields; `roster_snapshots` + `roster_assignments`; `rating_sets` + provenance columns; backfill `observed-v1`; run records snapshot/set ids; ingest contiguous seasons 2013-14 to 2025-26 for backtests | historical baselines byte-identical; migration reversible | 2-3 sessions |
| **1 — roster refresh (Deliverable A)** | refresh job from `PlayerIndex` + `CommonTeamRoster`; diff-based transaction log; completeness validator; roster provider reads snapshots instead of `Player.team_id`/PSS; players without stats load | completeness checks all pass for 2026-27; conflicts reported | 1-2 sessions |
| **2 — minimum viable ratings (Deliverable B, MVP)** | veterans = last-season carry-forward with simple volume shrinkage mapped on the prior pool; rookies = L0 slot x position prior; explicit fallback flag; basic minutes renormalisation; playable preseason 2026-27 | sanity distributions; sim runs; **no accuracy claim yet** | 2 sessions |
| **3 — backtests + data-driven constants** | pre-registered rolling-origin evaluation; estimate reliabilities and aging curves; compare S0-S3 and R0-R2; measure the preseason accuracy gap | pre-registered criteria; report negative results | 3 sessions |
| **4 — in-season blending** | Bayesian blend replacing the gate; rating-set rebuilds; pinning + effective dates in MyLeague | replay reproducibility tests; no mid-run drift | 2 sessions |
| **5 — rookie refinement, optional** | R2 and, only if licensing and backtest allow, pre-NBA data; EuroLeague/G League | must beat R1 out of sample | open |

The **minimum viable path to a playable 2026-27** is Phases 0, 1 and 2. Phases 3 and 4 convert it from
"plausible" to "measured", and should not be skipped before claiming accuracy.

---

## 9. Dependencies, risks, open questions

* **Trade-valuation dependency (concrete).** `trade_value.py` reads production from `PlayerSeasonStats`.
  In the preseason that table is empty, so the value model needs a *projected production* source from
  the rating set. Otherwise unrelated to the validation work, which stays as is.
* **Coaching / team context** (pace, defence) are franchise-level inputs; they change with new coaches
  and rosters and are not modelled. Flagged, out of scope (links to the banked team-level scheme modifier).
* **API fragility:** the NBA endpoints are unofficial and can change or block; keep them behind
  `nba_client.py` and store raw payload hashes so a break is detected, not silent.
* **Two-way and G League** players need an explicit status and a rule for whether they enter the
  simulated rotation.

**Decisions needed from you**

1. **Licensing posture:** is this strictly private/non-commercial for the foreseeable future? (It
   determines how cautious to be about data in the repo and about any hosted version.)
2. **Backfill scope:** OK to ingest seven additional seasons (2014-15, 2015-16, 2017-18, 2018-19,
   2021-22, 2022-23, 2023-24) for backtests? (Minutes of API time; no schema risk.)
3. **MyLeague refresh policy:** ratings pinned for the life of a run, with refreshes only as explicit
   user actions, or an automatic periodic refresh?
4. **Pre-NBA data:** appetite for the college/international data work in Phase 5, given the
   licensing/engineering cost and unproven benefit?
5. **Priority** against the M-6 trade work and the engine's muted star impact (the roster refresh is
   the only part with a calendar deadline: the season is already under way).

---
# Revision 2 (2026-10-23) — decisions, schema, migration, acceptance gates, sequence

*Supersedes Sections 7-8 and the "decisions needed" list. Still a proposal: nothing below is
implemented, and no production behaviour changes before the schema, migration plan and gates are
approved.*

## 10. Decisions recorded (Xavier, 2026-10-23)

1. **Licensing:** private, non-commercial for the foreseeable future. NBA.com Terms s.9 restrictions
   are documented (Section 3); **any public hosting, distribution or monetisation triggers a separate
   licensing review.** Raw payloads live in `data/raw/` (gitignored) and are never committed.
2. **Historical backfill approved**, isolated from production (database `nba_backfill`), with
   coverage and identity validation reported before any conclusion is drawn.
3. **MyLeague pinning:** a run's roster snapshot and rating set are pinned for its life. A refresh is
   an explicit user action that creates new sealed snapshots; it never mutates an existing run.
   Provenance is sufficient to reproduce a run from its original inputs and configuration.
4. **Pre-NBA (college/international) data deferred.** Establish the draft/age/size/position baseline
   first; add external data only if it improves held-out performance meaningfully and access/usage
   are acceptable.
5. **Priority:** after the confirmation run reaches its decision, the foundations and a minimum
   viable 2026-27 take precedence over expanding M-6. The trade work's findings and next steps stay
   in the backlog (`project-session-m6-design-lock`, `TRADE_VALUE_VALIDATION.md`).
6. **Versioning and leakage prevention are non-negotiable foundations,** even if the first rookie
   ratings are crude. A simple, honestly uncertain preseason estimate beats a sophisticated one that
   secretly depends on future performance.

## 11. Proposed schema (additive; no existing column changes meaning)

**Provenance**
* `data_fetches(id, endpoint, params jsonb, fetched_at, payload_sha256, row_count, store_path, status)`
  — append-only. Raw payloads in the content-addressed store (prototyped in
  `validation/ratings/rawstore.py`). Every derived row below carries `source_fetch_id`.
* `season_seals(season PK, sealed_at, content_sha256, sealed_by)` — a sealed season's rows in
  `player_season_stats`, `player_attributes`, `player_tendencies`, `team_season_stats`, `games`
  cannot be UPDATEd/DELETEd (trigger) except via an explicit, logged unseal. This stops a re-ingest or
  re-seed from silently rewriting history. The live season stays unsealed.

**Players and rosters**
* `player_bio(player_id PK, birth_date, height_in, weight_lb, school, country, draft_year, draft_round,
  draft_pick, first_nba_season, source_fetch_id)` — **time-invariant facts only**. Position, team and
  roster status are time-varying and live in snapshots.
* `roster_snapshots(id, season, kind, as_of_date, content_sha256, source_fetch_ids int[], parent_id,
  created_at, sealed_at)`; `roster_assignments(snapshot_id, player_id, team_id NULL, status
  {standard, two_way, exhibit10, inactive, free_agent}, position, jersey)` with
  `UNIQUE(snapshot_id, player_id)` and `CHECK(team_id IS NOT NULL OR status IN ('free_agent','inactive'))`.
  Sealed snapshots are immutable (trigger). The transaction log is a **view** over the diff of
  consecutive snapshots. `Player.team_id` and `CURRENT_ROSTER_SEASONS` stop being used by loaders.

**Rating sets** (the three types are separate in the data model, not by naming)
* `rating_sets(id, season, kind {observed, preseason, in_season}, information_cutoff date NOT NULL,
  season_first_game date NOT NULL, season_last_game date NOT NULL, method_id, method_version,
  input_manifest_sha256, parent_id, created_at, sealed_at)` with
  `CHECK(kind <> 'preseason' OR information_cutoff < season_first_game)`,
  `CHECK(kind <> 'in_season' OR (information_cutoff >= season_first_game AND information_cutoff < season_last_game))`,
  `CHECK(kind <> 'observed' OR information_cutoff >= season_last_game)`.
* `rating_set_inputs(rating_set_id, input_kind {season_stats, static_fact, roster_snapshot,
  prior_rating_set, fitted_constants}, ref, season, data_through date NOT NULL, content_sha256)`.
  **Trigger:** rejects an input with `data_through > information_cutoff`, rejects a
  `prior_rating_set` input that is `observed` with season >= this set's season, and rejects any insert
  after `sealed_at`.
* `rating_set_attributes`, `rating_set_tendencies`, `rating_set_stat_lines` — created `LIKE` the legacy
  `player_attributes` / `player_tendencies` / `player_season_stats` (so the loader math is unchanged),
  plus `rating_set_id`, `rating_source {carry_forward, shrunk, rookie_prior, position_fallback}`,
  `n_effective`, `uncertainty`. `rating_set_stat_lines` holds the **projected** per-game stat line
  (minutes, games, shot zones, FT, ...) the loader needs when no real line exists. Sealed => immutable.
* **Observed sets reuse the legacy tables.** One `rating_sets` row per existing season
  (`observed-v1`) *points at* the legacy rows; nothing is copied, so the historical path is
  byte-identical by construction.

**Runs**
* `simulation_runs` gains `roster_snapshot_id`, `rating_set_id`, `inputs_manifest jsonb`
  (snapshot id+hash, rating set id+hash, raw fetch hashes, config, seed). A `NOT VALID` CHECK requires
  both ids for new `myleague` runs while legacy rows stay NULL (documented "legacy-live").

**Loader.** A `PlayerSource` abstraction resolves `rating_set_id`: `observed-v1` takes today's join
unchanged; any other set reads the `rating_set_*` tables and **needs no current-season stats row**.
Roster membership comes from the pinned snapshot, then the M-6 trade overlay applies on top.

## 12. Migration and backfill strategy (expand, verify, switch, contract)

1. **Rehearse off-production.** Restore a production dump to a scratch database; apply every step
   there first and run all gates. Production is touched only after a clean rehearsal.
2. **Expand (additive only):** create the new tables/columns/triggers; no reads change. Reversible
   by dropping them.
3. **Backfill without copying:** insert the `observed-v1` rating set per existing season, compute
   `content_sha256` per season, then seal. Record counts before/after; abort on any mismatch.
4. **Switch behind a flag:** the loader defaults to `observed-v1`; run the *byte-identical* gates
   (Section 13, M5). Only on a clean pass is the flag the default.
5. **Contract (later, optional):** tighten `NOT NULL`s and retire `Player.team_id`/
   `CURRENT_ROSTER_SEASONS` usage. Deliberately last.
6. **Backtest data stays in its own database** (`nba_backfill`) until validated; promoting those
   seasons into production is a separate, explicit step.
Rollback at every step before (5) is a drop of additive objects; a pre-migration `pg_dump` is taken.

## 13. 2026-27 MVP acceptance gates (defined before implementation)

| id | gate | how measured | pass |
|---|---|---|---|
| M1 | rosters validated | 30 teams; <= 15 standard + <= 3 two-way each; no player on two teams; every rostered player has bio; every 2026 draftee present or explicitly flagged unsigned; `PlayerIndex` vs `CommonTeamRoster` conflicts listed with a documented resolution | 0 unresolved conflicts |
| M2 | every rostered player loads without a current-season stats row | `load_roster` count equals snapshot standard-roster count for all 30 teams on day zero | 0 silently dropped |
| M3 | preseason ratings differentiated, fallbacks documented | per attribute: share exactly at default < 10% (excluding flagged fallbacks); SD >= 70% of the prior season's observed SD; Spearman vs prior-season observed >= 0.80 for returning veterans; every non-observed row has `rating_source` and `uncertainty` | all hold |
| M4 | a sim starts with zero games | league sim and MyLeague run for 2026-27 start and complete the first week with no current-season stat rows; team-level inputs fall back to last season's regressed values (documented) | runs, no errors |
| M5 | historical baselines unchanged | 198-hash `simulate_game` fence; box-score fixture; full test suite; 60-day MyLeague snapshot; full-league audit (same seeds) before vs after migration | byte/number identical |
| M6 | no future information in preseason ratings | metamorphic canary (outputs bit-identical when season >= T data is garbled), manifest audit (`data_through` <= cutoff), negative tests proving the database rejects a deliberately leaky set | all pass |
| M7 | provenance reproduces a run | rebuild a run's roster snapshot and rating set from the raw store via its `inputs_manifest`, re-run, compare box scores | byte-identical |
| M8 | pinning | refresh creates a new snapshot/set; replaying a pre-refresh game afterwards is unchanged | identical |
| M9 | evaluation discipline in place | `RATING_BACKTEST_PROTOCOL.md` committed, as-of layer and canary exist **before any fitting** | true |
| M10 | honest disclosure | API exposes `rating_source`/`uncertainty`; rookies and fallbacks flagged | present |

Explicitly **not** MVP gates: any accuracy claim. Those come from the backtest (Phase 4), and the MVP
ships as "playable and honest, accuracy unmeasured" until then.

## 14. Dependency-ordered implementation sequence

| # | step | depends on | notes / gates |
|---|---|---|---|
| **1. Versioning and reproducibility foundations** | | | |
| 1a | `data_fetches` + raw store wired into ingestion (opt-in) | none | prototype exists |
| 1b | `season_seals` + `observed-v1` rating-set backfill + `PlayerSource` abstraction, default unchanged | 1a | **M5 must pass** |
| 1c | `rating_sets`/`rating_set_inputs` with CHECKs and immutability/leak triggers; `rating_set_*` tables | 1b | negative tests (M6) |
| 1d | run pinning columns + manifest; loader honours pinned ids | 1c | M7, M8 |
| 1e | as-of layer + metamorphic canary harness | protocol (done) | must precede any fitting (M9) |
| **2. Roster refresh and identity/assignment cleanup** | | | |
| 2a | `player_bio` ingestion (data already fetched and discarded) | 1a | identity checks |
| 2b | roster snapshots + assignments + refresh job + completeness validator + derived-transactions view | 1a, 2a | M1 |
| 2c | loaders read snapshots (stop using `Player.team_id`/`CURRENT_ROSTER_SEASONS`); players without stats rows load | 1d, 2b | M2, M5 |
| **3. Preseason rating initialisation incl. rookie fallbacks** | | | |
| 3a | **baseline-0 method** (no fitted constants): last-season carry-forward mapped on the prior pool, position-proxy x draft-slot-bucket rookie prior, explicit fallback, basic minutes renormalisation | 1c, 2c | M3, M4, M10 |
| 3b | generate and seal the 2026-27 preseason rating set + roster snapshot; start a run | 3a | M6, M7 |
| **4. Historical backtesting and acceptance gates** | | | |
| 4a | coverage/identity report on the backfilled seasons | backfill (running) | before any conclusion |
| 4b | execute the protocol: fit V2-V5, R1-R2 on development targets; freeze; locked test once | 1e, 4a, 3a | pre-registered rules |
| 4c | end-to-end preseason-vs-observed-vs-default standings test | 4b | quantifies the preseason accuracy gap |
| 4d | adopt a fitted method only if it beats baseline-0 under the protocol's rule | 4b | otherwise baseline-0 stays |
| **5. In-season updates** | | | |
| 5a | pseudo-count blending replacing the volume gate; `in_season` sets with effective dates; explicit refresh | 4d, 1d | replay-stability tests |
| **6. Optional advanced rookie projection** | | | |
| 6a | age/size refinement (R2); external pre-NBA data only if licensed and backtest-proven | 4b | out of scope for now |

**Minimum subset for a credible, playable 2026-27 that keeps the foundations intact:**
1a-1e, 2a-2c, 3a-3b, plus step 4a, i.e. gates M1-M10. That delivers a playable season on day zero with
reproducible, leak-proof, honestly-flagged ratings, and *no unmeasured accuracy claim*. Steps 4b-4d
then upgrade the ratings with evidence; 5 and 6 follow.

Rough size (PR-sized sessions): step 1 about 3-4; step 2 about 2-3; step 3 about 2; step 4 about 3.
The MVP subset is about 8-10 sessions, none of which touches the trade-valuation work except the one
dependency noted in Section 9 (projected production for the value model).
