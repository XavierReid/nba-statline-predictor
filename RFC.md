# RFC: NBA Franchise Simulator

**Status:** In Progress  
**Last Updated:** 2026-06-24

---

## Overview

A backend simulation engine inspired by NBA 2K MyLEAGUE/MyNBA. Given real NBA rosters and schedules, it simulates games at box-score level, computes standings, and generates playoff brackets. Built as a portfolio project demonstrating clean backend engineering — schema design, data pipelines, simulation logic, and REST APIs.

---

## Goals

1. Box-score level game simulation (player stat lines, not just final scores)
2. Full season simulation using real NBA schedules
3. Flexible simulation scope: single game, playoff series, full season
4. Persistent simulation results — referenceable after the fact, reproducible by seed
5. One active simulation at a time (v1); multiple coexisting simulations (v2)
6. Kafka producer/consumer layer for event streaming (v2 — resume claim)
7. Multi-season play with player aging and free agency (v2)

**Out of scope (v1):** trades, draft logic, salary cap, injuries, coaching, chemistry, drama/momentum features (see v1.5 below)

---

## Architecture

```
nba_api
  └── Ingestion Jobs
        └── Postgres
              ├── Teams / Players / Games (real schedule)
              ├── PlayerSeasonStats
              ├── PlayerAttributes  ←── RatingEngine
              ├── PlayerTendencies  ←── RatingEngine
              └── SimulationRuns
                    └── SimulatedGames
                          └── SimulatedPlayerLines
                                    ↑
                              GameSimulator (possession-based)
                              SeasonSimulator
                                    ↑
                               FastAPI REST
```

---

## Data Model

### Existing (ingested)

| Table | Key Fields | Source |
|---|---|---|
| `teams` | id, city, nickname, abbreviation | nba_api static |
| `players` | id, full_name, team_id, position | CommonTeamRoster |
| `games` | id, game_date, home/away_team_id, scores, status | LeagueGameFinder |

### Simulation Foundation (migration 0002)

| Table | Key Fields | Notes |
|---|---|---|
| `player_season_stats` | player_id, season, per-game averages | LeagueDashPlayerStats |
| `player_attributes` | player_id, season, 0-100 ratings + overall | Derived by RatingEngine |
| `player_attribute_overrides` | player_id, season, attribute, value | Manual corrections |
| `player_tendencies` | player_id, season, usage/shot/3pt/ast/reb/tov rates | Derived from season stats |

### Planned (migration 0003)

| Table | Key Fields | Notes |
|---|---|---|
| `lineup_players` | simulation_id, team_id, player_id, season, minutes_per_game, is_starter | Seeded from player_season_stats on run creation. Top 10 by minutes per team, normalized to sum to 240 player-minutes. Players with no stats sit out unless overridden. |
| `simulation_runs` | id, season, scope, status, seed, parameters (JSON), games_completed, created_at, completed_at | status: pending/running/paused/failed/complete/cancelled |
| `simulated_games` | id, simulation_id, game_id, home_score, away_score, home/away Q1-Q4 | unique(simulation_id, game_id) |
| `simulated_player_lines` | id, simulated_game_id, player_id, team_id, minutes, pts, reb, ast, stl, blk, to, fgm/a, fg3m/a, ftm/a, plus_minus | unique(simulated_game_id, player_id) |

---

## Player Rating System

### Design Decisions

**Percentile-based, not threshold-based.**  
Ratings are relative to the current player pool, not fixed thresholds.

**Volume-weighted raw scores before percentile ranking.**  
```
raw_score = efficiency * min(1.0, volume / volume_normalizer)
```

**Non-linear percentile → rating curve.**  
99s are rare. Most players cluster 45-75.

| Percentile | Rating |
|---|---|
| 0 | 30 |
| 25 | 45 |
| 50 | 58 |
| 75 | 75 |
| 90 | 88 |
| 99 | 99 |

**Minimum eligibility thresholds.**  
Players below minimums are excluded from the percentile pool and receive position-adjusted defaults instead.

**Configurable per skill via `SkillMetricConfig`.**  
Each attribute has its own `volume_normalizer`, `minimum_attempts`, `minimum_games`, `minimum_minutes`.

**Override mechanism.**  
`player_attribute_overrides` table for manual corrections on attributes box scores cannot capture.

### Overall Rating

`PlayerAttributes` includes an `overall_rating` computed in two steps:

**Step 1 — Position-weighted group average**

Derived and estimated attributes are kept in separate groups so position-defaults cannot
suppress real measurements. Weights are position-specific (C / F / G):

| Group | Attributes | C | F | G | Type |
|---|---|---|---|---|---|
| shooting | mid_range, three_point, free_throw | 0.12 | 0.20 | 0.28 | derived |
| passing | passing | 0.12 | 0.12 | 0.15 | derived |
| steal_block | steal, block | 0.18 | 0.15 | 0.12 | derived |
| rebounding | offensive_rebound, defensive_rebound | 0.35 | 0.20 | 0.08 | derived |
| finishing | close_shot, layup, dunk | 0.15 | 0.12 | 0.05 | estimated |
| ball_handle | ball_handle | 0.03 | 0.08 | 0.17 | estimated |
| perimeter_def | perimeter_defense | 0.00 | 0.08 | 0.10 | estimated |
| interior_def | interior_defense | 0.05 | 0.05 | 0.05 | estimated |

**Step 2 — Non-linear overall curve**

The weighted average is passed through `_OVERALL_CURVE`, which compresses the middle
and expands separation at the top — the same anchor-point design as `_CURVE_ANCHORS`.
This allows elite players to reach 2K-style ratings (90+) when their best attributes
are genuinely elite, without requiring every group to be strong.

| Raw avg | Overall |
|---|---|
| 50 | 60 |
| 60 | 70 |
| 70 | 80 |
| 75 | 86 |
| 80 | 90 |
| 85 | 94 |
| 90 | 97 |
| 95 | 99 |

**Why two steps?** A single weighted average has a mathematical ceiling: a player with
elite derived attributes but weak estimated ones (e.g., Jokić's ball_handle default)
can never reach 90+ regardless of weight tuning. The curve decouples "how good are your
best attributes" from "how bad are your worst", which is how 2K's overall actually works.

**Athleticism excluded from overall.** Speed, acceleration, strength, stamina, and
vertical are position-estimated with no stat signal from box scores. Including them
in the overall suppresses every player uniformly. They remain on the `PlayerAttributes`
model for use in the game simulator (speed affects fast-break probability, etc.) but
do not contribute to overall_rating. Real data sources for v2: `LeagueDashPtStats`
(speed/distance tracking), `DraftCombineStats` (measured vertical/wingspan).

### Attribute Categories

**Derived from season stats:**
`three_point`, `free_throw`, `mid_range`, `steal`, `block`, `offensive_rebound`, `defensive_rebound`, `passing`

**Estimated — position-adjusted defaults (not flat 50):**  
`close_shot`, `layup`, `dunk`, `ball_handle`, `speed`, `acceleration`, `strength`, `stamina`, `vertical`, `perimeter_defense`, `interior_defense`

Position baselines applied before override:

| Attribute | Center | Forward | Guard |
|---|---|---|---|
| strength | +10 | +5 | -5 |
| interior_defense | +10 | 0 | -10 |
| block | +5 | 0 | -5 |
| speed | -10 | -5 | +10 |
| ball_handle | -10 | -5 | +10 |
| perimeter_defense | -5 | 0 | +5 |
| close_shot / layup | +5 | 0 | -5 |

### Tendencies

| Tendency | Formula |
|---|---|
| `estimated_usage` | (FGA + 0.44×FTA + TOV) / team_total — approximation, good enough for v1 |
| `shot_tendency` | FGA per 36 min |
| `three_point_rate` | FG3A / FGA |
| `assist_rate` | AST per 36 min |
| `rebound_rate` | REB per 36 min |
| `turnover_rate` | TOV per 36 min |

Usage rate is critical for shot distribution in the simulator — do not leave as placeholder beyond v1.

---

## Simulation Design

### Simulation Reproducibility

Every simulation run must be reproducible. Requirements:

- Every `SimulationRun` stores a random seed and simulation parameters
- Same seed + same parameters = same results
- Parameters stored as JSON on the run:

```json
{
  "seed": 12345,
  "variance_factor": 0.15,
  "home_advantage": 3.2
}
```

This enables: debugging specific runs, comparing parameter sensitivity, and is a strong portfolio signal of engineering maturity.

### Rotation Model

Before possessions begin, the GameSimulator pre-generates a rotation schedule for each team:

1. Take the team's `lineup_players` rows (top 10 by minutes, normalized to 240 total)
2. For each substitution window, sample timing from `Normal(expected_minute, σ)` rather than fixed boundaries — so the bench unit enters around minute 6 of Q1, not always exactly at minute 6
3. Enforce constraints: exactly 5 on court at all times, minimum ~2-minute rest before a player re-enters, starters bias toward closing Q4
4. `σ` (substitution variance) is a simulation parameter stored in `parameters` JSON

This produces a possession-indexed map of which 5 players are active at any given moment. The schedule is generated once per game from the run's random seed, making results reproducible.

### Game Simulator — Possession-Based (not stat-projection)

The simulator operates at the possession level, not the player-average level. This is the critical design distinction from a stat prediction engine. Rather than asking "what will Luka average tonight?", each possession independently asks "who has the ball, what do they do, and what happens?"

```
Each possession (200 total, ~14.4 sec each):
  ↓ select ball handler (weighted by usage_rate)
  ↓ check bonus foul (~5.5% of possessions → 2 FTs, possession ends)
  ↓ check steal (best defender's steal rating × 0.034)
  ↓ check turnover (player's turnover_rate / league_avg × 13%)
  ↓ check offensive foul (~1.5% of possessions)
  ↓ select shot type (three_point_rate drives 3PT%; remainder split 40/60 mid/close)
  ↓ check block on non-3PT (best blocker's block rating × 0.04)
  ↓ random defender selected from active lineup
  ↓ resolve make/miss (base_prob − defense_penalty ± home_bonus)
  ↓ check shooting foul (3PT: 2%, 2PT: 15%)
  ↓ assign assist if made (65% on 3PT/mid, 50% on close)
  ↓ assign rebound if missed (27% OREB, 73% DREB, weighted by individual rates)
  ↓ accumulate to box score, update plus/minus for all active players
```

**Shot probability ranges (calibrated to NBA averages):**

| Shot type | lo (0-rated) | hi (100-rated) | Avg player (~65) | Real NBA |
|---|---|---|---|---|
| 3PT | 0.38 | 0.44 | ~39% | 36% league avg |
| Mid-range | 0.51 | 0.58 | ~55% | 43–45% |
| Close/paint | 0.65 | 0.72 | ~69% | 62–65% at rim |

Defense suppresses base_prob: perimeter defense × 0.06 (3PT/mid), interior defense × 0.08 (close). A 65-rated defender applies roughly a 4–5pp penalty — the difference between an elite and weak defender is ~3–4pp per shot.

**Free throw model:**

| Scenario | Rate | FTs awarded |
|---|---|---|
| Bonus foul (non-shooting, team over limit) | 5.5% of possessions | 2 FTs |
| 2PT shooting foul | 15% of 2PT attempts | 2 FTs (missed) or 1 FT and-1 (made) |
| 3PT shooting foul | 2% of 3PT attempts | 3 FTs (missed) or 1 FT and-1 (made) |

FT probability: `lo=0.60, hi=0.95` mapped from `free_throw` rating (0–100).

**Home advantage:** flat +3.0 points distributed as a per-possession make-probability boost (`HOME_ADVANTAGE / POSSESSIONS_PER_GAME`). Produces ~54% home win rate, matching NBA historical average.

---

### Design Decisions, Gaps, and Approximations

Every design decision below trades accuracy for simplicity. These are known, deliberate, and documented — not oversights.

**Fixed pace (200 possessions per game)**
Real NBA teams range from ~96 to ~104 possessions per 48 minutes (pace). We simulate exactly 200 possessions (100 per team) regardless of matchup. A fast-breaking team against a slow half-court team produces the same possession count as two equal-pace teams.
*Gap: pace advantages don't exist. Fast teams can't exploit a tired defense.*
*NBA API data source for v2: `LeagueDashTeamStats` → `PACE` column.*

**Shot selection is player-driven, not play-driven**
A player's `three_point_rate` determines how often they shoot threes. There's no pick-and-roll, no off-ball movement, no transition offense. Good and bad play-callers look identical as long as their players' individual tendency rates match.
*Gap: team offensive scheme has no effect. Ball movement quality is not modeled.*
*NBA API data source for v2: `SynergyPlayTypes` for play-type breakdowns.*

**Defense is individual, not schematic**
The defender is selected randomly from the active lineup. There's no zone defense, no double-team, no switching. A team's defense is only as good as its individual defenders.
*Gap: defensive schemes (Heat zone, Celtics switching) are invisible to the simulator.*
*NBA API data source for v2: `LeagueDashPtDefend` for matchup-level defensive data.*

**Best defender always contests steals; best blocker always contests blocks**
`max()` selects the top steal/block player. In reality they may be guarding someone else on the other side of the court.
*Gap: elite defenders have slightly outsized impact vs their real role.*

**Rotation is pre-generated, not adaptive**
Minutes are distributed from season averages before the game starts. A coach won't bench a star who picks up 2 quick fouls in Q1, won't go short rotation in a blowout, and won't adjust matchups based on what's working.
*Gap: no foul trouble management, no hot-hand substitutions, no intentional fouling.*

**No game-state awareness**
The simulator doesn't know the score while running. A team down 20 in Q4 plays identically to a team down 3. This is the primary driver of the ~26% blowout rate in calibration vs the NBA target of ~15–20%.
*Gap: no garbage time compression, no urgency, no rallies.*
*v1.5 fix: momentum/heat multiplier and clutch rating modifier (last 5 min, margin ≤5).*

**Home advantage is a flat probability nudge**
Real home advantage comes from crowd noise affecting free throw concentration, travel fatigue, referee bias, and court familiarity. We approximate all of it as a single constant applied uniformly to every home-team possession.
*Gap: home advantage doesn't vary by arena (historically loud buildings like OKC/Boston), time zone travel, or back-to-back situations.*

**Bonus foul is approximated, not tracked**
Real NBA: after 5 team fouls in a quarter, all non-shooting fouls result in 2 FTs. We approximate this as a flat 5.5% per-possession probability instead of tracking per-quarter foul counts. This means bonus fouls can happen in Q1 possession 1 and may not happen late in a quarter with 4 team fouls.
*Gap: bonus foul timing is not correlated to actual foul accumulation.*
*v1.5 fix: track team fouls per quarter, only apply bonus after threshold.*

**Plus/minus reflects floor time, not causation**
Every active player is credited or charged for every point scored while on the court. This is how real +/- works too — it's a known limitation of the statistic, not unique to our model.

**OT lineups inherit the Q4 end-of-game lineup**
Coaches can't rest players between OT periods or adjust their rotation for a short 5-minute period. The minute-47 lineup plays every OT period.
*Gap: bench depth is less meaningful in OT than it should be.*

---

### Calibration Results (2025-26 season, 500 games)

After tuning, the simulator produces outcomes within acceptable range of NBA baselines:

| Metric | Simulator | NBA target | Notes |
|---|---|---|---|
| Avg team score | ~103 pts | ~108–113 pts | Within range; FT volume and pace approximations account for gap |
| Home win rate | 54% | ~54% | ✓ |
| Blowout rate (20+ margin) | ~26% | ~15–20% | v1 ceiling; requires game-state awareness to close |
| OT rate | ~2–3% | ~5–7% | Improves with momentum/clutch features |
| Avg margin of victory | ~14 pts | ~10–11 pts | Structural floor of possession variance model |

The margin gap (~3pts) and blowout gap (~6pp) are the known, documented limitations of a stateless possession model. Both are targeted in v1.5 with momentum and clutch features.

### Simulation Lifecycle

**Status machine:**
```
pending → running → complete        (terminal, non-blocking)
           ├─▶ paused  → running    (resume)
           │     └─▶ cancelled      (terminal, non-blocking)
           ├─▶ failed  → running    (retry)
           │     └─▶ cancelled      (terminal, non-blocking)
           └─▶ cancelled            (terminal, non-blocking)
```

Blocking states (prevent new simulations): `running`, `paused`, `failed`.
Terminal/non-blocking: `complete`, `cancelled`.

A failed or paused run holds the lock until explicitly retried, resumed, or cancelled.
Partial results from cancelled/failed runs are kept in the DB and remain queryable.

**Control endpoints:**

| Endpoint | From | To |
|---|---|---|
| `POST /simulations` | — | `pending → running` |
| `POST /simulations/{id}/pause` | `running` | `paused` |
| `POST /simulations/{id}/resume` | `paused` | `running` |
| `POST /simulations/{id}/step` | `paused` | `paused` (one game, returns box score immediately) |
| `POST /simulations/{id}/retry` | `failed` | `running` |
| `POST /simulations/{id}/cancel` | `running`, `paused`, `failed` | `cancelled` |

**Simulation scope:**

Stored in `parameters` JSON on `SimulationRun`:
- `"scope": "league"` — simulate all games in the season schedule
- `"scope": "team", "team_id": <id>` — simulate only games involving that team (82 games)

Both scopes produce full box scores for all players in each simulated game.
Full-league with team focus (simulate all 1225, surface one team) deferred to v2.

### Season Simulator

- Fetch regular season games from `games` table (filter to avoid playoff games)
- For team-scoped runs: filter to games where `home_team_id = team_id OR away_team_id = team_id`
- Run GameSimulator for each game using that game's lineup rows from `lineup_players`
- Between each game: poll `SimulationRun.status` — stop if `paused` or `cancelled`
- Persist to `simulated_games` → `simulated_player_lines` after each game
- On completion: set status to `complete`; on unhandled exception: set status to `failed`

### Standalone Game Simulation

A single game can be simulated outside of a season sim — primary use case is testing and ad-hoc matchups.

```
POST /simulations/game
{
  "home_team_id": 15,
  "away_team_id": 2,
  "season": "2024-25",
  "seed": 12345,          ← optional, random if omitted
  "step_mode": true,      ← optional, default false
  "step_by": "quarter"    ← "quarter" | "minute", default "quarter"
}
```

- Lineups auto-built from `player_season_stats` for the given season (top 10 by minutes, normalized to 240 player-minutes). Custom lineup overrides deferred to v2.
- Synchronous — returns box score immediately when `step_mode: false`.
- No DB persistence by default. Results exist only for the lifetime of the step session.

### Step-Through (game level)

Applies to both standalone games and games stepped through within a season sim. The pattern is identical:

1. Game simulates to completion instantly (single game ≈ milliseconds)
2. Result is stored in an **in-memory cache** keyed by a UUID token
3. Results are delivered chunk-by-chunk on subsequent step calls

```
POST /simulations/game          → returns token + first chunk
POST /simulations/game/{token}/step  → returns next chunk
... (repeat until game ends, then cache is cleared)

POST /simulations/{id}/step     → same for season sim games
  { "step_by": "quarter" | "minute" }
```

**Granularity options:**
- `"quarter"` — 4 chunks (default). Each chunk contains all possession outcomes + running box score for that quarter.
- `"minute"` — 48 chunks. Each chunk contains possessions within that game-clock minute.

**Implementation note:** The GameSimulator tags each possession with a game-clock timestamp (running clock, ~14 seconds per possession). Results are stored as 48 minute-buckets internally. Quarter view = aggregate of minutes 1–12, 13–24, 25–36, 37–48. One storage format serves both granularities.

**In-memory cache** (Python dict, keyed by UUID token) is sufficient for v1. Lost on server restart, which is acceptable for a testing tool. Drop-in swap to Redis if cross-session persistence is needed later.

### Validation (before building simulator)

Inspect generated ratings for known players:

| Player | Attribute | Expected |
|---|---|---|
| Nikola Jokić | passing | 95+ |
| Nikola Jokić | defensive_rebound | 90+ |
| Stephen Curry | three_point | 95+ |
| Victor Wembanyama | block | 90+ |
| Role bench player | most ratings | 40-55 |
| Luka Dončić | overall | 93+ |

If these fail the smell test, tune `SkillMetricConfig` before touching simulation.

---

## Build Progression

### Done
- [x] Scaffold: FastAPI, SQLAlchemy 2.0, Alembic, Docker Compose
- [x] Ingestion: teams, players, games (2024-25 — 30 teams, 530 players, 1225 games)
- [x] Models: PlayerSeasonStats, PlayerAttributes (+ overall_rating), PlayerTendencies, PlayerAttributeOverride
- [x] Migrations 0001–0006 applied
- [x] RatingEngine: percentile-based ratings, SkillMetricConfig, position-adjusted defaults, position-specific overall weights, non-linear overall curve
- [x] Unit tests for RatingEngine (8 passing)
- [x] Ingested 2024-25 season stats (431 players); Advanced stats (USG_PCT, AST_PCT, OREB_PCT, DREB_PCT)
- [x] Seeded PlayerAttributes + PlayerTendencies for 2024-25
- [x] Rating validation: Jokić 94, Wemby/Luka/Tatum 86-87, bench 65-74 ✓
- [x] Usage rate fix: real NBA usage formula (team_poss/team_min); Giannis 0.346 ✓
- [x] Rate limiting: 0.6s delay between per-team API requests
- [x] Simulation models: SimulationRun, LineupPlayer, SimulatedGame, SimulatedPlayerLine (migration 0003)
- [x] GameSimulator Phase 1 (scratch/03_game_simulator.py) — possession-based, rotation model with substitution variance, steal/block/foul/offensive-foul checks, foul-out rotation patching
- [x] GameSimulator Phase 2 — extracted to app/services/game_simulator.py
- [x] POST /simulations/game — standalone game endpoint, season-aware, reproducible by seed
- [x] Ingestion diagnostic endpoints: GET /ingestion/seasons, POST /ingestion/seasons/{season}/seed, POST /ingestion/seasons/{season}/ingest
- [x] Step-through: POST /simulations/game/stepthrough + GET /simulations/game/stepthrough/{token}/next; in-memory UUID token store, 1-hour TTL
- [x] GameSimulator enhancements: plus/minus tracking, tip-off randomization (Q3 NBA rule), same-team 422 validation, time-based chunk boundaries (48/steps min), OT support (unlimited periods, new tip per OT, dynamic quarter_scores)

### Next
- [ ] Blowout calibration: tune _attr_to_prob shot probability ranges to reduce blowout frequency
- [ ] POST /simulations — season simulation (background task, persists to DB)
- [ ] Season sim control: pause / resume / cancel / retry
- [ ] POST /simulations/{id}/games/{game_id}/stepthrough
- [ ] Lineup overrides: PUT /simulations/{id}/lineups

### v1.5 — Simulation realism (drama features)
All three are self-contained within `simulate_game`, reset between games (POC scope), and each pairs with a new NBA API ingestion endpoint.

- [ ] **Clutch ratings** — New ingestion job: `LeagueDashPlayerClutch` (last 5 min, margin ≤5). Adds `clutch_rating` to player_attributes. Applied as a rating modifier in the last 5 minutes when margin ≤5. Note: low-sample bench players will need a fallback to overall_rating.
- [ ] **Momentum / heat** — Per-player in-game heat multiplier (rises on consecutive makes, fades on misses/turnovers). No new data needed. Resets each game.
- [ ] **Within-game fatigue** — Rating decay as player minutes accumulate. Resets each game. No new data needed; can use `LeagueDashPlayerBioStats` (age/weight) as a future modifier.

Long-tail (v2+): across-game fatigue (back-to-backs), in-game coach adjustments, intentional foul strategy, player chemistry.

### v2
- [ ] Kafka producer/consumer
- [ ] Multi-season with player aging
- [ ] Free agency
- [ ] CLI interface
- [ ] Expanded NBA API utilization: `LeagueDashPtStats` (speed/distance for athleticism), `PlayerGameLog` (per-game variance), `LeagueDashPlayerBioStats` (age/weight for fatigue)

---

## Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.9 | Use `Optional[X]` not `X \| None` |
| API | FastAPI | |
| ORM | SQLAlchemy 2.0 | Mapped/mapped_column style |
| DB | PostgreSQL 16 | Via Docker Compose |
| Migrations | Alembic | |
| NBA Data | nba_api 1.4.1 | Custom headers required to avoid 403s |
| Tests | pytest | Engine logic only |

---

## Decision Log

Key decisions with rationale — documents what we chose AND what we ruled out, so future sessions and interviewers can reconstruct the thinking.

| Decision | Chose | Ruled out | Reason |
|---|---|---|---|
| Simulation approach | Possession-based (each possession independently resolved) | Stat-projection (sample from player averages) | Projection produces averages, not games. Possession model produces variance, runs, foul-outs — basketball, not math. |
| Chunk boundaries | Time-based (48/steps minutes per chunk) | Possession-based (POSSESSIONS/steps per chunk) | Time-based maps to real basketball moments (Q1=12min). Possession-based produces inconsistent OT behavior. |
| Step-through storage | In-memory UUID token store, 1hr TTL | Redis / DB-backed | 82-game season sims don't need cross-restart persistence. Redis is a deployment dependency we don't need yet. Swap is a one-file change. |
| Per-game seed (season sim) | `hash(master_seed, game_id)` | `master_seed + game_index` | Hash avoids sequential correlation between games. Same master seed always produces same game regardless of schedule reordering. |
| Season sim lineup source | `load_roster()` directly from player_season_stats | `lineup_players` table per sim run | `lineup_players` adds flexibility for overrides but is extra schema. Override capability deferred to v2. |
| Simulation create vs start | Separate `POST /simulations` (create) and `POST /simulations/{id}/start` (execute) | Single endpoint that creates and starts | Separation allows inspection before execution, lineup overrides before start, cleaner conflict detection on start. Maps to job queue pattern. |
| Play-by-play storage | Generate on demand (re-simulate from seed, Option C) | JSON column on SimulatedGame (A) or separate events table (B) | Seed is a compression key — fully describes the game. On-demand is zero storage overhead. Events table added in v2 when cross-game queries are needed. |
| Background task runtime | FastAPI BackgroundTasks | Celery | 82 games ≈ 1-2 seconds. Celery is a deployment dependency (Redis broker) not warranted at this scale. |
| Pause/resume mechanism | Conditional UPDATE (`WHERE status='paused'`) + re-enqueue | Task cancellation / async primitives | FastAPI BackgroundTasks are fire-and-forget — no handle to cancel. Conditional UPDATE prevents double-resume race condition at the DB level. |
| Blowout calibration ceiling | Accept ~26% at v1, fix in v1.5 | Continue tuning lo/hi | Per-matchup data showed teams are near-equal in average scoring. Blowout rate is structural possession variance, not team quality gap. True fix requires game-state awareness (momentum/clutch). |
| Event description generation | Inside `resolve_possession` where player objects are in memory | At API response time via DB lookup | Zero overhead — names already loaded. API-time lookup would be N+1 queries or a join per event. |

---

## Backlog / Parking Lot

Ideas that surfaced mid-build but aren't in active scope. Review when planning the next version.

- **Triggered events in step-through**: force OT, force a substitution, inject a specific play — useful for testing and "what-if" mode
- **Pace as a simulation variable**: fast teams run more possessions, slow teams fewer. Currently fixed at 200.
- **Notable event filtering**: filter chunk_events to "highlight" plays (clutch shots, big runs, foul-outs) for a broadcast-style text sim — raw data already captured
- **Playoff simulation**: bracket generation, best-of-7 series logic, seeding from standings
- **Garbage time compression**: when team up 20+ in Q4, reduce effort. Would cut blowout rate without full momentum system.
- **Full-league season sim**: simulate all 1230 games, compute full standings. Currently team-scoped (82 games) only.
- **Lineup overrides**: `PUT /simulations/{id}/lineup` to swap players or adjust minutes before starting
- **Manual game result override**: user "plays" a game themselves, `POST .../games/{id}/override` replaces sim result
- **OT intentional foul / late-game strategy**: trailing teams foul to stop clock; leading teams milk clock. Requires game-state awareness.
- **Per-quarter foul tracking**: real bonus situation tracking instead of 5.5% approximation

---

## Open Questions

1. **Team-level defense modifier:** how strongly should opposing team's defensive rating suppress individual outputs? Needs empirical tuning post-simulator.
2. **Faux schedule generation:** needed for seasons beyond the API's range. Balanced 82-game schedule respecting conference/division structure. Deferred to v2.
3. **Repo rename:** still `nba-statline-predictor` on GitHub. Renaming to `nba-franchise-simulator` would break resume links — decide before publicizing.
4. **Overall rating weights:** the group weights above are a starting point. Tune after inspecting real player outputs.

---

## Known Constraints

- Python 3.9 — no `X | None` union syntax
- psycopg2 requires PostgreSQL client libs — install after Docker Desktop
- NBA API rate-limits aggressively — custom browser headers required
- `game_status` stored as `String(16)` not Postgres ENUM
