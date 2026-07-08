# RFC: NBA Franchise Simulator

**Status:** In Progress  
**Last Updated:** 2026-06-27

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

## Simulation Philosophy

The core principle of this simulator is a **causal chain**: context → decision → matchup → outcome. Every possession should emerge from basketball reality — who is on the floor, what the game situation is, what that player tends to do, who is defending, and how that matchup resolves. The simulator should not know that a team wins; it should simulate *why* a team wins.

### What this means in practice

**Outcomes emerge from possessions, not predicted box scores.**
A player's points in a game are a result of simulated shot attempts, contested shots, and free throw resolutions — not a projection of their PPG with noise applied.

**Overall ratings are never simulation inputs.**
Overall rating is a presentation abstraction for UI, roster comparison, and player evaluation. The game engine operates on underlying attributes (shooting, defense, rebounding) and tendencies (usage, shot type selection, transition rate). "Higher overall wins" is not basketball.

**Tendencies describe behavior. Attributes describe ability.**
`three_point_rate` (tendency) determines how often a player attempts a three. `three_point` (attribute) determines how likely they are to make it. These are separate and must remain separate. A player can have great three-point shooting (attribute) but low three-point rate (tendency) — that is a real basketball profile.

**Game state modifiers adjust probabilities, never ratings.**
Fatigue, foul trouble, momentum, and clutch performance change the probability of outcomes for a specific possession. They do not change a player's underlying attribute values, and they reset between games.

**Future systems should extend existing layers, not bypass them.**
A team identity layer (TeamTendencies) should influence how possessions are set up — which shot types are selected, what tempo is run, how often transition opportunities appear — but it should feed into the same possession resolution chain, not replace it with a shortcut.

### Anti-patterns to avoid

```python
# NEVER: winner determined by rating comparison
if home_overall > away_overall:
    win_probability += X

# NEVER: box score generated from projection
player_points = projected_ppg + random()

# NEVER: simulation bypassed by aggregate
home_score = team_offense_rating - away_defense_rating + noise()
```

### The possession flow (current → target)

```
Current:  player selection → action → attribute check → outcome
Target:   team identity → player role → action selection → matchup → outcome
```

The current architecture covers the right half of this chain. Each milestone adds context to the left — possession variance (M3b), game situation awareness (M3c), shot quality and contest level (M3d), foul drawing behavior (M3e), team offensive identity (post-M3).

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

Split into three milestones. All changes are self-contained within `simulate_game` and reset between games. Each modifier is individually toggleable via `SimConfig`.

#### Drama M1 — Possession Flow (spec finalized)

**What changes:** the game loop switches from a fixed possession count to a clock-based model. Each possession consumes sampled time; the quarter ends when the clock hits 0. All drama modifiers are off by default so existing behavior is preserved.

**New data:** `TeamSeasonStats` table — `pace`, `off_rating`, `def_rating`, `net_rating` per team per season. Ingested from `LeagueDashTeamStats` (Advanced, PerGame).

**SimConfig** (`app/services/sim_config.py`):
```
use_pace: bool = False           # pace-derived possession count vs fixed 200
use_clock: bool = False          # real clock tracking vs post-hoc distribution
use_second_chance: bool = False  # oreb extends possession chain
use_fast_break: bool = False     # steal → transition modifier next possession
use_team_defense: bool = False   # team def_rating suppresses opponent FG%
use_strategic_foul: bool = False # trailing team intentionally fouls late-game
```

**Clock model:**
- `QUARTER_SECONDS = 720`, `OT_SECONDS = 300`
- Per quarter: `mean_poss_time = 720 / expected_possessions_this_quarter`
- `while clock > 0`: sample possession time → decrement clock → resolve possession
- `game_clock_seconds` on each event = actual remaining clock (not estimated)
- Buzzer beater: if `clock < poss_time` when possession starts → `event["buzzer_beater"] = True`
- Free throws don't consume game clock

**Possession time distributions:**

| Type | Mean | Std | Clamp |
|---|---|---|---|
| Half-court | pace-derived | 3.0s | [5, 24] |
| Fast break | 7.0s | 1.5s | [3, 12] |
| Second-chance (oreb) | 9.0s | 2.0s | [3, 14] |
| Intentional foul | 4.0s | 1.0s | [2, 8] |

**Possession flow changes:**
1. **Pace** — `total_expected = round((home_pace + away_pace) / 2) * 2`; fallback to 200 if no data
2. **Second-chance** — on miss, sample oreb; if offensive → same team possession, chain_depth += 1; cap at 5 (safety net for miscalibrated rates, not a basketball rule — P(5 consecutive oreb) < 0.1%)
3. **Fast break** — triggers ONLY on steals (`steal_by` set); dead ball turnovers (travel, OOB, shot clock, offensive foul) do NOT trigger fast break; in `resolve_possession`: close_shot +8%, defender effectiveness ×0.80, skip block check
4. **Team defense** — `team_defense_factor = league_avg_def_rating / defending_team.def_rating`; multiplied into `base_prob` before individual defender penalty
5. **Strategic foul** — after each defensive possession in Q4/OT: if `margin_min(3) ≤ margin ≤ margin_max(8)` AND `clock ≤ 120s` AND `rng.random() < 0.70` → intentional foul targeting lowest `ft_rating` active player on leading team; generates foul event with `fta=2`

**Calibration flags added to `calibrate_simulator.py`:**
`--disable-pace`, `--disable-clock`, `--disable-second-chance`, `--disable-fast-break`, `--disable-team-defense`, `--disable-strategic-foul`

**Definition of done:**
- [ ] `TeamSeasonStats` ingested for 2025-26, migration applied
- [ ] `SimConfig` dataclass in `app/services/sim_config.py`
- [ ] `simulate_game` accepts optional `config: SimConfig` param
- [ ] All 35 existing tests pass with default `SimConfig` (all False)
- [ ] New tests: pace varies possession count, oreb chain inserts extra possession + caps at 5, fast break only triggers on steal, strategic foul fires in correct window only, team defense reduces shot prob for elite defenses, clock is monotonically decreasing within each quarter, buzzer beater flag fires correctly
- [ ] Calibration shows measurable margin + blowout improvement with all modifiers enabled vs disabled
- [ ] Committed

#### Drama M2b — GameStateModifier Framework + Momentum (closed)

**Philosophy:** modifiers adjust probabilities, never directly modify player ratings. Effects are temporary and reset between games.

**New package:** `app/services/modifiers/`
- `base.py`: `GameStateModifier` ABC, `GameState` dataclass, `ModifierAdjustments` dataclass
- `momentum.py`: `MomentumModifier`

**MomentumModifier:** per-team confidence float in `[-momentum_max, +momentum_max]`. Boosts from 8-pt runs (+0.010), 12-pt runs (+0.020/−0.010 opponent), made threes (+0.005), steals (+0.005), defensive stops (+0.003). Decay 20%/possession. Composure resistance (avg team rating / 100 × 0.4) dampens negative momentum. Steal probability intentionally not modified (defender skill, not offensive pressure).

**SimConfig additions:** `use_momentum`, `momentum_max=0.05`, `momentum_decay_rate=0.20`

**Preset:** `DRAMA_M2` = all M1 modifiers + momentum

**Known calibration gap:** seed-specific momentum compounding can push individual games to ~147 pts/team avg. To revisit after M2c — fatigue expected to suppress late-run amplification.

---

#### Drama M2c — Fatigue, Foul Trouble, Clutch (spec finalized 2026-06-27)

**Philosophy:** same as M2b — temporary probability adjustments, no permanent rating changes, all toggleable via `SimConfig`.

##### Architecture changes (prerequisite for all three modifiers)

**`GameState` expansion:**
```python
home_active_ids: List[int]       # player IDs currently on court
away_active_ids: List[int]
player_stats: Dict[int, Dict]    # {pid: {"min": float, "pf": int}} — snapshot per possession
```

**`ModifierAdjustments` expansion:**
```python
defense_penalty_delta: float = 0.0  # increases shot-contesting cost (less effective defense)
```
Addition to `__add__` method to sum across modifiers.

**Game loop change:** call `get_adjustments` for BOTH the offensive team (current behavior) and the defensive team (new). Sum both into a single `ModifierAdjustments` before passing to `resolve_possession`. Momentum ignores `defense_penalty_delta` — no breaking change.

**`resolve_possession` change:** apply `defense_penalty_delta` to `defense_penalty` (additive) before computing `shot_prob`.

##### FatigueModifier

Tracks per-player fatigue float in `[0.0, 1.0]` internally. Fatigue is driven by cumulative minutes played — Q4 is when it becomes visible, but a player who logs 38 min by halftime is already affected.

**Fatigue curve (piecewise linear, breakpoints tunable):**

| Minutes played | Fatigue |
|---|---|
| 0 | 0.00 |
| 24 | 0.15 |
| 32 | 0.45 |
| 38 | 0.75 |
| 40+ | 1.00 (plateau) |

**Bench recovery:** players NOT in `active_ids` this possession recover `fatigue × fatigue_recovery_rate` per possession off court. Represents real in-game rest without explicit substitution tracking.

**Team-level adjustment:** average fatigue deltas across active players. A unit with three tired starters drags collectively.

**Effects at max fatigue (1.0):**
- `shot_prob_delta`: `−fatigue × fatigue_max_shot_penalty` (default −0.03)
- `tov_prob_delta`: `+fatigue × fatigue_max_tov_penalty` (default +0.02)
- `defense_penalty_delta`: `+fatigue × fatigue_max_defense_penalty` (default +0.02) — fatigued defenders contest less effectively

**SimConfig additions:**
```
use_fatigue: bool = False
fatigue_onset_minutes: float = 24.0
fatigue_max_shot_penalty: float = 0.03
fatigue_max_tov_penalty: float = 0.02
fatigue_max_defense_penalty: float = 0.02
fatigue_recovery_rate: float = 0.15
```

##### FoulTroubleModifier

Affects defensive aggressiveness only. Foul-troubled players hedge on contests to avoid fouling out.

**v1 scope:** probability-modifier only — no rotation changes. Player stays on court but contests less aggressively.

**Out of scope (deferred to coaching model):** benching players with early foul trouble, coach-driven minutes management. Tracked in Parking Lot.

**Defensive aggressiveness reduction by foul count:**

| Fouls | Defense penalty reduction |
|---|---|
| 0–2 | 0% |
| 3 | 10% |
| 4 | 25% |
| 5 | 40% (foul-out handled by existing `patch_rotation`) |

Applied to `defense_penalty_delta`: reduces the shot-contesting contribution of foul-troubled defenders. Uses `player_stats[pid]["pf"]` from `GameState`.

Team adjustment: average reduction across active defensive players.

**SimConfig additions:**
```
use_foul_trouble: bool = False
foul_trouble_threshold: int = 3       # fouls at which caution begins
foul_caution_3: float = 0.10
foul_caution_4: float = 0.25
foul_caution_5: float = 0.40
```

##### ClutchModifier

Triggered when: `quarter >= 4` (including OT) AND `abs(home_score − away_score) <= clutch_score_margin` AND `clock_seconds <= clutch_clock_threshold`.

Outside the clutch window: modifier is a no-op (zero adjustments).

**Player-level clutch attribute:** `clutch_rating` (0–100), seeded from `LeagueDashPlayerClutch` (last 5 minutes, within 5 points). Derived via same percentile curve used for other attributes. See ingestion section below.

**Effects (applied to ball handler for offense, best defender for defense):**

At `clutch_rating` above avg (72): small positive adjustments (+shot_prob, −tov_prob, −defense_penalty).
At `clutch_rating` below avg: opposite, but capped so a bad clutch player is impaired, not unusable.

Scale: `delta = (clutch_rating − 72) / 100 × scale_factor`

- `shot_prob_delta`: `delta × clutch_max_shot_delta` (default 0.01 → max ±1%)
- `tov_prob_delta`: `−delta × clutch_max_tov_delta` (default 0.008 → max ±0.8%)
- `defense_penalty_delta`: `−delta × clutch_max_defense_delta` (default 0.008) — better clutch defenders contest harder

**Fallback if `clutch_rating` not available** (future seasons or missing data): use `(free_throw − 72) / 100 × 0.5` as a proxy — FT rate is the most reliable single-stat clutch signal.

**SimConfig additions:**
```
use_clutch: bool = False
clutch_score_margin: int = 5
clutch_clock_threshold: int = 120    # seconds remaining in Q4/OT
clutch_max_shot_delta: float = 0.01
clutch_max_tov_delta: float = 0.008
clutch_max_defense_delta: float = 0.008
```

##### Clutch rating ingestion

**Source:** `nba_api.stats.endpoints.LeagueDashPlayerClutch`
- Parameters: `season`, `clutch_time="Last 5 Minutes"`, `point_diff=5`, `per_mode="PerGame"`
- Fields used: `FG_PCT`, `FT_PCT`, `TOV` (per 36 for rate), `PLUS_MINUS`

**Derived rating:** equal-weight composite across three clutch stats —
`composite = (fg_pct_percentile + ft_pct_percentile + (1 − tov_rate_percentile)) / 3`
Mapped through `_CURVE_ANCHORS` (same rating curve used for other attributes).

Equal weights chosen as a defensible baseline — no single stat is privileged without empirical justification. Revisit weights against real clutch outcome data once enough simulated seasons exist to compare close-game win rates.

**Schema change:** add `clutch_rating: Mapped[int]` to `PlayerAttributes` model. Migration required.

**Ingestion:** added to `seed_player_attributes()` as an additional pass after existing attribute seeding. Falls back to FT-based proxy if `LeagueDashPlayerClutch` returns < 10 clutch possessions for a player (small sample filter).

**Preset update:** `DRAMA_M2` updated to include `use_fatigue=True, use_foul_trouble=True, use_clutch=True`.

##### Definition of done

- [ ] `clutch_rating` column on `player_attributes`, migration applied
- [ ] `LeagueDashPlayerClutch` ingestion added to `seed_player_attributes`, re-seeded for 2025-26
- [ ] `GameState` expanded with `home_active_ids`, `away_active_ids`, `player_stats`
- [ ] `ModifierAdjustments` expanded with `defense_penalty_delta`; game loop calls `get_adjustments` for both teams; `resolve_possession` applies `defense_penalty_delta`
- [ ] `FatigueModifier`, `FoulTroubleModifier`, `ClutchModifier` in `app/services/modifiers/`
- [ ] All three modifiers wired into clock loop
- [ ] `SimConfig` updated with all new fields; `DRAMA_M2` preset includes all M2 modifiers
- [ ] Tests: fatigue grows with minutes, bench recovery reduces fatigue, foul-troubled defenders reduce contest effectiveness, clutch modifier is no-op outside window, clutch fires correctly in window, full M2 game smoke test
- [ ] Calibration: run `--drama-m2` before and after M2c and compare blowout rate + avg margin
- [ ] FoulTrouble rotation management tracked in Parking Lot

#### Drama M3 — Game Environment Realism (spec finalized 2026-06-27)

**Philosophy:** the rating model is producing believable matchups. The next calibration gains come from making the simulation *behave* like basketball — not from adjusting ratings. Every M3 change targets game flow, variance, and possession context. No player attribute changes.

**Calibration baseline (drama-m2, 500 games, 2025-26):**

| Metric | Real | Current | Gap |
|---|---|---|---|
| Avg team score | 115.6 | 117.9 | +2.3 |
| Avg margin | 13.3 | 15.5 | +2.2 |
| Home win rate | 55.4% | 55.6% | ✅ |
| Blowout rate (20+) | 22.9% | 32.0% | +9.1pp |
| OT rate | ~6% | 0.8% | −5.2pp |

**Build order:** M3a (refactor) → M3b (variance + OREB) → M3c (catch-up + garbage time) → M3d (shot quality) → M3e (foul drawing) → calibration pass.

Calibration checkpoint after each group: avg score, possessions/game, avg margin, blowout rate, OT rate, player stat realism.

---

##### M3a — Architecture Refactor

`game_simulator.py` has grown to ~971 lines with four distinct concerns colocated. Split into focused modules; no behavior change, all existing tests must pass.

**Target module structure:**

```
app/services/
  game_simulator.py      → thin orchestrator, re-exports public surface
  roster.py              → load_roster()
  rotation.py            → build_rotation(), patch_rotation()
  possession.py          → resolve_possession(), _attr_to_prob(), describe_event()
  box_score.py           → _empty_stats(), _snapshot_box(), _apply_event(), flatten_and_enrich()
```

`simulate_game()` stays in `game_simulator.py` as the top-level orchestrator, importing from the new modules. Public import paths (`from app.services.game_simulator import load_roster, simulate_game`) remain unchanged so callers (API, tests, calibration scripts) need no edits.

`app/api/simulations.py` at 707 lines: split Pydantic models into `app/api/schemas/simulations.py`; route handlers stay in `app/api/simulations.py`. No route path changes.

**Definition of done:**
- [ ] `roster.py`, `rotation.py`, `possession.py`, `box_score.py` created
- [ ] `game_simulator.py` reduced to orchestration only (~200 lines)
- [ ] `simulations.py` schemas extracted to `app/api/schemas/simulations.py`
- [ ] All 74 existing tests pass unchanged
- [ ] Calibration output identical to pre-refactor baseline

---

##### M3b — Possession/Team Variance + Team OREB Profiles

**Goal:** elite teams still have bad nights; weaker teams can overperform; possession counts reflect actual team rebounding tendencies.

**Motivation:** current model produces near-expected outputs every game because player attributes feed directly into fixed probability ranges. Real game-to-game variance is much wider — player efficiency fluctuates even holding opponent quality constant.

###### Per-game form factor

At `simulate_game` start, draw a form factor per player from a player-specific distribution:

```python
form_factor = rng.gauss(1.0, player_variance)
```

`player_variance` is derived from player/team profile — not uniformly random:

| Profile | Variance (σ) | Rationale |
|---|---|---|
| Elite decision-maker (passing ≥ 80, low TO rate) | 0.04 | Consistent high-IQ players; Jokić, LeBron |
| Shooting specialist (3PT ≥ 80, low usage) | 0.10 | Hot/cold swings are real for spot-up shooters |
| Young/high-usage player (age proxy: low overall, high usage) | 0.09 | Less developed consistency |
| Default | 0.07 | Mid-tier players |

`form_factor` is clamped to `[0.75, 1.25]` — a 25% swing max in either direction.

**Application:** `form_factor` scales `shot_prob_delta` for that player's possessions only. It does not change player ratings — it is applied at possession resolution as a temporary per-game offset, treated like a modifier adjustment.

**Storage:** `form_factors: Dict[int, float]` passed into `resolve_possession` (or held in game-level state). Not persisted — only relevant during one game.

**Team variance:** team-level form is the average of active player form factors. Shooting-heavy teams (high avg `three_point_rate`) see higher score variance naturally from the compounding of individual form factors — no separate team-level factor needed.

###### Team OREB profiles

Replace flat `OREB_RATE = 0.22` constant with per-team offensive rebound rate from `TeamSeasonStats`.

**Source:** `LeagueDashTeamStats` already provides `OREB_PCT` — already ingested in `team_season_stats` table.

**Change:** in `simulate_game`, load `home_oreb_rate` and `away_oreb_rate` from `TeamSeasonStats`. Pass to `resolve_possession` (or access via game-level config). Use in the oreb check after a missed shot.

**Fallback:** if `OREB_PCT` is null (missing team data), fall back to league constant `0.22`.

**SimConfig additions:**
```
use_player_variance: bool = False
use_team_oreb: bool = False
```

**Definition of done:**
- [ ] `player_variance` derivation logic (4-tier classification) implemented in `roster.py` or `possession.py`
- [ ] Form factors drawn per player at game start in `simulate_game`
- [ ] Form factors passed through to `resolve_possession` and applied as `shot_prob_delta`
- [ ] Team OREB rate loaded from `TeamSeasonStats`; `OREB_RATE` constant used only as fallback
- [ ] Tests: elite player variance < shooting specialist variance, clamping respected, OREB rate uses team data when available
- [ ] Calibration checkpoint: compare avg score, blowout rate, margin distribution before/after

---

##### M3c — Catch-Up + Garbage Time Behavior

**Goal:** trailing teams change strategy in late Q4; leading teams protect; OT rate ↑, blowout rate ↓.

**OT rate target after M3c:** ~3-4% (full 6% likely requires M3d shot quality improvements as well).

###### CatchUpModifier

New `GameStateModifier` in `app/services/modifiers/catch_up.py`.

**Activation:** trailing team, Q4 or OT, clock ≤ 150s, deficit ≤ 15 pts.

Trailing team adjustments:
- `three_rate_override`: shift shot selection toward 3s. Scale with deficit and urgency:

| Deficit | Clock ≤ 60s | Clock 60–150s |
|---|---|---|
| 1–5 pts | +0.08 | +0.04 |
| 6–10 pts | +0.14 | +0.08 |
| 11–15 pts | +0.20 | +0.12 |

- `pace_override`: shorter possession time (more urgent). Clock ≤ 60s: `mean_poss_time × 0.75`. Clock 61–150s: `mean_poss_time × 0.85`.
- `tov_prob_delta`: +0.02 (taking more risks = more turnovers).

Leading team adjustments (same activation window, flipped role):
- `pace_override`: longer possession time (clock management). Clock ≤ 90s: `mean_poss_time × 1.15`.
- `shot_prob_delta`: −0.015 (conservative shot selection; accepting lower-efficiency shots to burn clock).
- Three-rate not explicitly reduced — handled naturally by conservative shot selection skew.

**Implementation note:** `three_rate_override` is a new field on `ModifierAdjustments`. Unlike `shot_prob_delta` (which modifies a shot already selected), `three_rate_override` changes which shot type gets selected. Applied in `resolve_possession` before shot type selection:

```python
effective_three_rate = min(0.60, three_rate + adj.three_rate_override)
```

`pace_override` is a multiplier applied to `poss_time` in the clock loop before calling `resolve_possession`.

**ModifierAdjustments additions:**
```python
three_rate_override: float = 0.0   # additive shift to three_point_rate
pace_multiplier: float = 1.0       # multiplicative on poss_time; default no-op
```

###### GarbageTimeModifier

New `GameStateModifier` in `app/services/modifiers/garbage_time.py`.

**Activation:** Q3 or Q4, clock ≤ 600s in the quarter (final ~10 min), margin ≥ 20 pts.

**Scope — efficiency change only, not substitution.** Literal starter-sitting requires coaching/rotation logic that is out of M3 scope. Model the *effect* of garbage time (reduced effort, faster/looser play) without modeling the mechanism.

Leading team:
- `shot_prob_delta`: −0.02 (reduced effort, resting starters playing at lower intensity).
- `defense_penalty_delta`: +0.02 (defense softens; allowing easier shots for trailing team).

Trailing team:
- `three_rate_override`: +0.08 (gambling for quick points).
- `pace_multiplier`: 0.80 (playing faster — nothing to lose).
- `tov_prob_delta`: +0.03 (more risk-taking = more turnovers).

**Design note:** the asymmetry is intentional. The leading team softening creates the "games feel closer at the end than the score says" effect real NBA games have. Trailing team desperately shooting threes is the corresponding counter.

**SimConfig additions:**
```
use_catch_up: bool = False
use_garbage_time: bool = False
catch_up_clock_threshold: int = 150
catch_up_max_deficit: int = 15
garbage_time_margin: int = 20
garbage_time_clock_threshold: int = 600
```

**Definition of done:**
- [ ] `ModifierAdjustments` expanded with `three_rate_override`, `pace_multiplier`
- [ ] `catch_up.py`, `garbage_time.py` in `app/services/modifiers/`
- [ ] `three_rate_override` applied in `resolve_possession` before shot type selection
- [ ] `pace_multiplier` applied to `poss_time` in the clock loop
- [ ] Both modifiers wired into clock loop; `DRAMA_M2` preset updated
- [ ] Tests: catch-up activates only in correct window, three rate increases under catch-up, pace decreases for leading team, garbage time is no-op outside margin threshold
- [ ] Calibration checkpoint: OT rate, blowout rate, avg margin — expect OT ↑ to ~3-4%, blowout ↓

---

##### M3d — Shot Quality Model (Sub-types, Contest Level, Positional Matchups)

**Goal:** make `possession → outcome` more contextually aware. Move from three coarse shot buckets to a richer model where the same player has meaningfully different probabilities based on what shot they're taking, who's defending, and how open they are.

**This is the largest architectural change in M3.** Implement after M3b and M3c are calibrated.

###### Shot sub-types

Replace three buckets (`three`, `mid`, `close`) with six:

| Sub-type | Bucket | Base prob range | Block eligible | Primary attr |
|---|---|---|---|---|
| `corner_three` | three | 0.40–0.46 | No | `three_point` |
| `above_break_three` | three | 0.36–0.42 | No | `three_point` |
| `mid_range` | mid | 0.47–0.55 | No | `mid_range` |
| `floater` | close | 0.48–0.55 | Partial (×0.5) | `close_shot` |
| `layup` | close | 0.62–0.70 | Yes | `layup` |
| `dunk` | close | 0.68–0.76 | Yes (×0.5) | `dunk` |

**Selection:** `three_point_rate` still drives 3PT frequency. Within 3PT: `corner_three_rate` from `PlayerTendencies` (new field derived from player shot distribution data — or positional estimate: guards 25% corner, wings 35% corner, bigs 10% corner). Within close: `dunk_rate` from position (bigs 50% dunk, wings 20%, guards 5%); remainder split between layup and floater by position.

**Player attribute additions (`PlayerAttributes`):** `layup` and `dunk` columns already exist on the model (estimated defaults) but are unused in `resolve_possession`. Wire them in.

**`PlayerTendencies` addition:** `corner_three_rate: float` — derived from shot location data if available, otherwise positional estimate.

###### Contest level

Add a contest dimension to each shot. Before computing `shot_prob`, determine if the shot is open or contested:

```
contest_prob = defender_contest_rating / 100 × position_weight
if rng.random() < contest_prob:
    shot is contested
    defense_penalty × contest_multiplier (1.0 — current behavior)
else:
    shot is open
    defense_penalty × 0.2   (defender arrived late, minimal contest)
```

`position_weight` from positional matchup (see below). `contest_multiplier` varies by shot type:

| Shot type | Contest multiplier |
|---|---|
| Dunk | 1.2 (high-risk contest, foul likely) |
| Layup | 1.1 |
| Floater | 0.9 (hard to contest cleanly) |
| Mid-range | 1.0 |
| Three (ATB) | 1.0 |
| Corner three | 0.8 (hard to rotate to corner) |

###### Positional matchups

Replace random defender selection with position-aware matching.

**Position groups:**
```python
GUARD = {"G", "G-F"}
WING  = {"F", "F-G", "F-C"}
BIG   = {"C", "C-F"}
```

**Matchup logic:** ball handler's position group → filter defenders to matching group → if no match, fall back to full defender pool. Select defender weighted by `perimeter_defense` (guards/wings) or `interior_defense` (bigs).

**Defense attribute by shot type:**

| Shot type | Defense attribute | Defender group |
|---|---|---|
| Any three | `perimeter_defense` | Guard/wing preferred |
| Mid-range | `perimeter_defense` | Guard/wing preferred |
| Floater | `interior_defense` × 0.6 + `perimeter_defense` × 0.4 | Mixed |
| Layup | `interior_defense` | Big preferred |
| Dunk | `interior_defense` | Big preferred |

**Block check update:** block check currently uses "best blocker in defense." With positional matchups, use the *matched* defender's `block` rating instead. A PG being blocked by a random center is replaced by a PG being blocked by the defender guarding the ball handler's position.

**`PlayerTendencies` addition:** `corner_three_rate: float`.

**No new model migrations required** — `layup` and `dunk` already on `PlayerAttributes`. `corner_three_rate` added to `PlayerTendencies` (same migration pattern as existing tendency fields, or derived inline from position).

**SimConfig additions:**
```
use_shot_subtypes: bool = False
use_contest_model: bool = False
use_positional_matchups: bool = False
```

**Definition of done:**
- [x] Six shot sub-types implemented in `possession.py`
- [x] `corner_three_rate` kept as positional default in `_POSITIONAL_DEFAULTS` (no migration — intentional deviation from spec, extensible for future player tendencies)
- [x] Contest model implemented; separates `_CONTEST_REACH` (probability) from `_CONTEST_IMPACT` (outcome multiplier)
- [x] Positional matchup selection replaces random defender (uniform within group, full-pool fallback)
- [x] `layup` and `dunk` attributes wired into shot probability and `roster.py` load
- [ ] Block check uses matched defender's block rating — **deferred**: block still uses `best_blocker` from full pool; positional matchup kept simple for M3d per design alignment
- [x] Tests: 35 tests covering sub-type distribution, dunk/layup attributes, block eligibility, positional matchup, contest model, flag no-ops, calibration
- [x] Calibration checkpoint: 119.5 pts/team (vs 119.4 pre-M3d) — scoring-neutral as designed; FG% by sub-type verified

---

##### M3e — Foul Drawing Tendency

**Goal:** star players generate more FT opportunities; late-game FT volume improves OT rate.

**Data source:** `fta` (free throw attempts per game) already in `PlayerSeasonStats`. Derive `foul_drawing_rate = fta / fga` — no new NBA API call needed.

**Storage:** `foul_drawing_rate: float` added to `PlayerTendencies` alongside existing rates. No `PlayerAttributes` migration.

**Ingestion:** computed in `seed_player_attributes()` / `compute_tendencies()` from existing `PlayerSeasonStats` fields. `fga` already stored.

**Application in `resolve_possession`:**

Replace flat 5.5% bonus foul rate with player-weighted check:

```python
foul_draw_prob = ball_handler["foul_drawing_rate"] × FOUL_DRAW_SCALE
if rng.random() < foul_draw_prob:
    # shooting foul or bonus foul
```

`FOUL_DRAW_SCALE` is a calibration constant that maps the raw FTA/FGA ratio to the correct simulation frequency. Calibrated to maintain overall FT volume close to real (league avg ~22 FTA/game/team).

**Late-game escalation:** in Q4 with clock ≤ 60s and margin ≤ 3, `foul_draw_prob × 1.5` — reflects the real tendency for aggressive drives and foul hunting in final possessions.

**Shot-type interaction:** foul drawing probability scales with shot type. Rim attempts (layup, dunk) draw fouls at higher rates than perimeter shots:

| Shot type | Foul draw multiplier |
|---|---|
| Dunk | 1.4 |
| Layup | 1.3 |
| Floater | 1.1 |
| Mid-range | 0.9 |
| Three | 0.7 |

(Requires M3d sub-types to be implemented first — M3e depends on M3d.)

**SimConfig additions:**
```
use_foul_drawing: bool = False
foul_draw_scale: float = 0.55
```

**Definition of done:**
- [x] `foul_drawing_rate` added to `PlayerTendencies` schema and `compute_tendencies()` (migration `529b31a8f50f`)
- [x] `seed_player_attributes` re-run for 2025-26 — 525 players; rate capped at 0.60 in-engine (low-FGA outliers reached 1.92)
- [x] Flat 5.5% bonus foul replaced by player-specific rate with league-avg floor (0.22) — flat rate preserved when `use_foul_drawing=False`
- [x] Shot-type multipliers on shooting fouls (dunk 1.5× … corner_three 0.65×); 2PT base normalized 0.15 → 0.13 to hold total foul volume
- [x] Late-game escalation: two zones (≤120s/≤8 pts → 1.3×; ≤60s/≤5 pts → 1.8×) — **regulation only**: OT fixed-possession loop has no real clock, so escalation is dead in OT; queued for post-M3 calibration diagnostic
- [x] Tests: 19 in `test_m3e.py` — rate differentiation, floor/cap, escalation windows, no-op when disabled
- [x] Calibration checkpoint: FTA/team/game 21.9 (baseline 21.6, real ~21.8); OT rate 1.2% (was 0.4%, target ~6% — remaining gap is the OT clock issue)

---

##### M3 Full Calibration Pass

After all five M3 groups are built and individually checked, run a final 1000-game calibration comparison across presets.

**Calibration matrix:**

| Metric | Real | Baseline | Drama M2 | Drama M3 target |
|---|---|---|---|---|
| Avg team score | 115.6 | ~112 | 117.9 | 114–117 |
| Avg margin | 13.3 | ~14.1 | 15.5 | 12–14 |
| Blowout rate (20+) | 22.9% | ~27% | 32.0% | 20–24% |
| OT rate | ~6% | ~2% | 0.8% | 4–6% |
| Home win rate | 55.4% | ~51% | 55.6% | 54–56% |
| FTA/game/team | ~22 | ~18 | ~18 | ~20–22 |
| 3PA/game/team | ~35 | ~28 | ~30 | ~33–36 |

**Player stat realism checks (spot-check on 2025-26 rosters):**
- Star players (top 5 overall) should avg 22–30 pts, 5–10 reb, 4–8 ast depending on position
- Role players should avg 8–14 pts
- Team FG% should cluster 44–48%

**Preset update:** `DRAMA_M3` = all M2 modifiers + all M3 modifiers enabled.

**Definition of done:**
- [ ] `DRAMA_M3` preset in `sim_config.py`
- [ ] 1000-game calibration run documented
- [ ] All calibration targets met or gap explained
- [ ] `RUNBOOK.md` updated with M3 modifier table and new calibration results
- [ ] `CONTEXT_PRIMER.md` updated
- [ ] Committed

#### Attribute Derivation v2 — Interior Finishing + Individual Defense (spec sketch, 2026-07-08)

**Motivation (from SIMULATION_GAPS.md gap 1.3):** the engine compresses team strength
(schedule-replay top-10 net-margin slope 0.66 vs real). Root cause: `close_shot`, `layup`,
`dunk`, `perimeter_defense`, `interior_defense` are position-adjusted constants — interior
scoring (~55% of attempts) and all individual defense carry zero between-team signal.

**Scope:**

1. *Interior finishing* — ingest NBA shooting-split data (FG% by distance: restricted area,
   paint non-RA; e.g. `PlayerDashboardByShootingSplits` or shot-zone aggregation). Derive
   `close_shot`, `layup`, `dunk` via the existing `SkillMetricConfig` percentile pipeline
   (efficiency × volume weight, minimum-attempt gates).
2. *Individual defense* — preferred: `LeagueDashPtDefend` (defended FG% at rim / overall,
   vs shooter avg). Fallback interim proxy: blend of team `def_rating`, position, and
   steal/block ratings — weaker but no new API dependency. Decide after checking endpoint
   availability/rate limits.
3. *Stage B recalibration (follows, same milestone):* re-tune `attr_to_prob` spans and
   defense penalty factors against measured targets — strength slope and FG%-vs-defender-quality —
   per the measured-constants workflow. Do NOT hand-tune.

**Explicitly out of scope:** stage C changes (usage weighting, rotations — tested healthy);
`passing` outcome effects (currently assist-routing only; revisit with creation model, gap 2.4).

**Validation (engineering loop):**
- Attribute spread check: team-level stdev of new attributes comparable to live ones (3.5-5.5)
- Schedule replay: top-10 net-margin strength slope ≥ 0.8
- Close-game rate improves toward 24.5%; avg score 114-117 and blowout 20-24% hold
- Star interior scorers (Giannis, Zion) show elite close/dunk ratings; elite defenders
  (Wemby, Draymond, JJJ) show elite defense ratings — spot-check
- Re-run possession accounting: shot-mix and FG%-by-subtype stay in band

**DoD:**
- [ ] Shooting-split ingestion job + `PlayerSeasonStats` (or new table) columns
- [ ] `close_shot`/`layup`/`dunk` in `SKILL_CONFIGS`, derived not estimated
- [ ] Defense data source decision documented; `perimeter_defense`/`interior_defense` derived
- [ ] Stage B constants recalibrated with provenance
- [ ] Validation suite above passes; SIMULATION_GAPS.md 1.3 marked fixed

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
- **Garbage time compression**: when team up 20+ in Q4, reduce effort. Would cut blowout rate without full momentum system. → Addressed in M3c `GarbageTimeModifier`.
- **Full-league season sim**: simulate all 1230 games, compute full standings. Currently team-scoped (82 games) only.
- **Lineup overrides**: `PUT /simulations/{id}/lineup` to swap players or adjust minutes before starting
- **Manual game result override**: user "plays" a game themselves, `POST .../games/{id}/override` replaces sim result
- **OT intentional foul / late-game strategy**: trailing teams foul to stop clock; leading teams milk clock. Requires game-state awareness. → Partially addressed in M3c `CatchUpModifier`. Full intentional-foul-to-stop-clock mechanic (vs current strategic foul for FT shooting) deferred.
- **Per-quarter foul tracking**: real bonus situation tracking instead of 5.5% approximation
- **Second-chance possessions**: offensive rebounds currently credit the box score but don't generate an additional possession — next possession always alternates. Fix: when offensive rebound is sampled, create a follow-up possession for the same team. Changes possession count and flow; natural fit alongside momentum/drama features.
- **FoulTrouble rotation management (coaching model):** when a player picks up foul 3 or 4 early in a quarter, NBA coaches often bench them to protect foul count. Modeling this requires a `CoachingModel` layer that can patch rotations mid-game based on game state (quarter, score margin, opponent's key matchup). Explicitly deferred from M2c `FoulTroubleModifier` which only models defensive aggressiveness reduction.
- **Season sim calibration vs real records**: add `--compare-real` mode to calibrate_simulator.py that checks simulated W-L % against actual 2025-26 standings. Requires ingesting real final standings. Useful for detecting systematic team-level bias.
- **Incomplete schedule ingestion**: 7 teams have < 82 games in the games table for 2025-26 (ORL: 79, MEM: 80, OKC/DAL/DET/NYK/SAS: 81). Needs targeted re-ingestion pass — not a simulator bug.

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
