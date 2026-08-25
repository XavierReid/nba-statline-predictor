# RFC: NBA Franchise Simulator

**Status:** In Progress  
**Last Updated:** 2026-07-25

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
- [ ] Committed

#### Post-M3 Calibration Diagnostic Arc (2026-07-07 → 2026-07-08) — COMPLETE

Full evidence trail in SIMULATION_GAPS.md; architecture in ARCHITECTURE.md. Summary:

| Gap | Finding | Fix | Status |
|---|---|---|---|
| 1.4 Possession inflation | Pace budget correct; features added uncompensated short possessions; strategic fouls fired Q1-Q3 | Mixture compensation (measured constants) + possession accounting + Q4 guard | ✅ scoring exact (115.5 vs 115.6) |
| 1.3 Margin dispersion | Hypothesis REVERSED: engine compressed team strength (5 dead attributes; stage B attenuation) | Attribute Derivation v2 + `signal_gain=1.25` | ✅ top-10 strength slope 0.88-1.03 |
| 1.1 OT engine | OT was a separate no-modifier path | `_run_clock_period` — OT is a real timed period | ✅ |
| 1.2 Late-game compression | No clock-stopping/urgency behavior; only 26.7% close entering final 2 min | `LateGameContext` + incentive pacing | ✅ scope met: OT 2.7→3.7%, tie conversion 9.2→12.2% |
| 2.1 Static rotation | Stars played full minutes in blowouts | Rotation modes + asymmetric `should_concede` + `lineup_quality.py` | ✅ scope met; behavior realistic |

Key negative results (documented so we don't revisit): widening late-game windows does
NOT reduce blowouts (margins are built over the first 46 minutes); symmetric benching
preserves margins; the real starter/bench gap is offensive, not defensive.

**Open calibration items:** blowout 26.3% vs 22.9% and close 19.9% vs 24.5% — owned by
residual early-game dispersion (Q1 |margin| 7.0 vs ~5.5-6 real), next investigation after
the cleanup/documentation phase. OT rate 3.7% vs ~6% — expected to improve alongside.
Also flagged: `signal_gain` may reduce slightly now that lineup quality adds differentiation
(slope 1.03); legacy non-clock path is a removal candidate once frozen-tag comparisons
replace the `baseline` preset use case.

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
- [x] Shooting-split ingestion job + `PlayerSeasonStats` columns (migrations a430c45fbf57, 3593e9dc9c82)
- [x] `close_shot`/`layup`/`dunk` in `SKILL_CONFIGS`, derived not estimated (dunk = 0.7 rim + 0.3 layup hybrid)
- [x] Defense: `LeagueDashPtDefend`; perimeter uses NON-RIM defended plus-minus (3PT-only was luck-dominated)
- [x] Stage B recalibrated via single `signal_gain=1.25` (sweep documented, scoring-neutral by construction)
- [x] Validation passed: team stdev 3.6-7.4 (was 0.0-1.2), sanity checks (Jokić/Giannis/Clingan #1s, Trae 55 perim D), top-10 slope 0.88; SIMULATION_GAPS.md 1.3 FIXED; baseline tag `attr-v2-baseline`

#### Multi-Season Support — THE NEXT MILESTONE (spec, 2026-07-09)

**Motivation:** the engine is only compatible with 2024-25 / 2025-26 today (the `players`
table is a current-roster snapshot; `ingest_season_stats` skips unknown players and
`load_roster` filters on the *current* team). Historical seasons matter beyond convenience:
running an old era through the *same* engine and reproducing *that era's* distinct profile
(lower 3PA, different pace) is the strongest generalization test we have — objective evidence
the engine is genuinely data-driven, not overfit to two modern seasons. It also realizes the
platform direction ("the engine shouldn't know who Curry is — only roles, ratings, context").

**Sequence (Stage D done → this is next):**

**Phase 1 — playable infrastructure (behavior-neutral for current seasons).**
Make *any* NBA season ingestible and playable, without changing basketball behavior.
- `ingest_season_stats` CREATES `Player` rows for players not already known (stats rows
  carry PLAYER_ID / PLAYER_NAME / TEAM_ID), instead of skipping them.
- `load_roster` uses the SEASON's team (`PlayerSeasonStats.team_id`) instead of `Player.team_id`.
- Graceful fallback where advanced tracking data doesn't exist for a season (shot-location,
  defensive-matchup, line scores) — players fall back to positional defaults (already the
  below-gate path); ingestion of those datasets is skipped/soft-failed for old seasons.
- DoD: any season (e.g. 2015-16, 2005-06) ingests a full player pool and loads era-correct
  rosters; **current-season schedule replay is IDENTICAL to `demoable-v1`** (behavior-neutral proof).

**Cross-era validation harness (its own step — build the measurement before Phase 2).**
A small suite of league-level metrics — pace, avg score, 3PA rate, FTA rate, avg margin —
computed for the SIM vs REAL for several eras (e.g. 2015-16, 2018-19, 2021-22, 2024-25).
Objective test of whether each era emerges correctly from the same engine. This measurement
drives Phase 2.

**Phase 2 — era fidelity. ✅ COMPLETE (2026-07-14).**
- Era-specific attribute derivation (interior finishing + box-score defense fallback) — done
  in the cross-era reconciliation milestone (SIMULATION_GAPS.md). Old-era strength no longer
  compresses; scoring reconciles within ~1.5 pts across 1996-97 / 2005-06 / 2025-26.
- Relocated-franchise identity — `app/services/franchise.py` (season-aware city/nickname/abbr;
  'SEA' resolves for 2005-06). Instrument-first finding: franchise *mapping* already worked
  (stable NBA franchise ids), so only the *display/input identity* needed fixing.
- Cross-era metric gaps — closed to second-order via the accounting layer.

**Known Phase-2 limitations (documented, low value / need new data):** traded players carry
season-total stats on their final team (the season-stats endpoint pre-resolves them; per-team
splits would need a different source); the ~+0.013 three-point efficiency residual is left as
a second-order limitation, not chased.

**Then:** resume player-realism (3.4) and the shot-model variance investigation (3.2) with
much stronger evidence the engine generalizes across basketball history. (Recommended interim:
a small legacy engine-mode cleanup pass — remove the fixed-200/no-clock/DRAMA_M1-M2 paths,
proven byte-identical via replay — before opening 3.4. See ARCHITECTURE_ROADMAP.md.)

### v2
- [ ] Player inspection tooling: endpoint or CLI to view a player's ratings, attributes, and tendencies side by side (with league percentile context) — makes attribute sanity checks routine instead of ad-hoc scripts (`scratch/explore_ratings.py` is a partial start)
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

---

# Frontend RFC: Player-Detail Modal (Phase 2)

**Status:** ✅ SHIPPED (PR #7, merged 2026-07-24). Second frontend feature after the single-game MVP.

## Overview
Clicking a player's row in a box score opens a modal showing that player's **season averages**,
**derived ratings**, their **line in the game just simulated**, and **their play-by-play from that
game**. It adds depth to the single-game view — you see *why* a player performed as they did (real
profile) and *what* they did (their filtered PBP) without leaving the result.

## Goals
- Turn each box-score row into a drill-in to the player's profile for the game's season.
- Reuse game data already in hand (the box line + the full event list) — no re-simulation, and the
  player-PBP needs **no backend change** (events already carry per-player involvement ids).
- Establish the pattern for player-scoped reads (`/players/{id}/profile`).

## UX / flow
1. Each box-score row (played AND DNP) is clickable.
2. Click → modal opens (loading state) → fetches profile → renders.
3. Sections: **header** (name · position · season-accurate team · season) · **This game** (the row's
   line) · **Season averages** (real) · **Ratings** (labeled 0–100 bars) · **This game — play-by-play**
   (events involving this player).
4. Close on ✕, overlay click, or `Esc`. One modal at a time.

## Backend — `GET /players/{id}/profile?season=<s>`
Returns `{id, full_name, position, team (season-accurate), season, season_averages{gp,min,pts,reb,ast,
stl,blk,tov,fg_pct,fg3_pct,ft_pct}, ratings{overall + curated: three_point, mid_range, layup, passing,
ball_handle, perimeter_defense, interior_defense, offensive_rebound, defensive_rebound, clutch}}`.
- 404 if the player has no `PlayerSeasonStats` for that season.
- Reads existing `PlayerSeasonStats` + `PlayerAttributes` — **no new tables/migrations**.
- `overall_rating` is display-only (guardrail #1: never a sim input).

## Frontend
- `api.ts`: `getPlayerProfile(id, season)`.
- `types.ts`: extend `PossessionEvent` with the involvement ids (`scorer`, `assisted_by`,
  `rebounded_by`, `turnover_by`, `steal_by`, `block_by`, `fouled_by`).
- `App`: holds the selected player (id + clicked box line) and renders `<PlayerModal>`.
- `PlayerModal.tsx`: the five sections. The PBP section filters `game.events` to events where the
  player's id appears in any involvement field, rendered clock + description.
- `BoxScore`: row `onClick` → `onSelectPlayer(line)`.

## Testing
- **Backend**: `/players/{id}/profile` test — 200 shape for a seeded player, 404 for unknown/no-season.
- **Frontend (Vitest)**: `getPlayerProfile` URL; `PlayerModal` renders averages + ratings; the PBP
  filter selects only the player's events; row click invokes the handler.
- **UAT**: click a star → correct identity/averages/ratings + their PBP lines; close 3 ways; DNP row →
  season profile shows with no game line and empty/short PBP; 2005-06 player → season-accurate team.

## Definition of Done
- [x] `/players/{id}/profile` endpoint + test (200/404)
- [x] Modal opens from any box row, closes 3 ways, one-at-a-time
- [x] Season averages + ratings + this-game line + player-filtered PBP render; loading/error/404 handled
- [x] Vitest + backend suite green; `npm run build` clean
- [x] UAT signed off

## Decisions (locked 2026-07-24)
Curated ratings + `overall`; include the this-game line; DNP rows clickable; ratings as bars;
**player PBP added** (filter the game's events by involvement id).

## Future considerations (roadmap, NOT this pass — revisit later)
- Real per-game game logs / "last N games" (needs `/players/{id}/history` implemented + `PlayerGameLog`
  coverage beyond the current 2 seasons).
- Cross-season / career view.
- Attribute editing / what-if overrides.
- **Visual redesign (ESPN-style), team logos, player headshots** — after core functionality (Xavier, 2026-07-24).
- **Real historical positions** — `LeagueDashPlayerStats` carries none, so pre-current players show POS `—`;
  would need a position source (e.g. per-season roster endpoint) to backfill `Player.position`.


# RFC: Event-Sourced PBP (Engine + Frontend, Phase 3)

**Status:** ✅ SHIPPED (PR #9, merged 2026-07-25). Priority #2 (b) per project-next-session-focus; follows PR #8 (priority #2 (a), FGA accounting fix), which deliberately preserved the current event dict shape so (b) can reshape without conflict.

## Overview
Rewrite play-by-play as a stream of granular, typed events (`SHOT` / `FOUL` / `FT` / `REB` / `TOV` / `STL` / `BLK` / `AST`). The box score becomes a **derived** view over that stream (`derive_box_score(events) -> box`) rather than an accumulator populated inside `resolve_possession`. Free throws become their own filterable category. Foul-drawn misses and bonus FTs get correct chip categorization (they stop being miscategorized as "SHOT").

Foundation for player timelines, possession replay, advanced stats derived from events, and real-NBA-PBP export/compare.

## Goals
- One source of truth: a granular event stream. Box score, PBP display, chips, and future analytics all read from it.
- FTs as their own filterable category, per Xavier's stated goal since the modal shipped.
- Fix the chip miscategorization surfaced during PR #8: bonus FTs and (after PR #8) foul-drawn misses are currently tagged `SHOT` because `apply_event` reads `scorer` regardless of whether a shot was attempted.
- **Behavior invariance** — this is a representation change, not a behavior change. Same seeds must produce byte-identical box scores.

## Behavioral invariant (non-negotiable)
Same seeds must produce byte-identical box scores. Per `feedback-refactor-behavior-invariance`: a structural refactor must change representation, not the stochastic process. Achieve this by leaving `resolve_possession` untouched — same RNG draw order, same possession outcomes. Only the OUTPUT of `resolve_possession` is translated: the single possession-result dict is expanded into a sequence of granular events post-resolution.

**Regression fence:** capture pre-(b) box-score fingerprints on 3 fixed pairs × 3 seasons × 10 seeds = 90 games, serialized as a fixture. A test loads the fixture and asserts identical box scores from `derive_box_score(events)` after the refactor.

## Event schema

Every event shares a header:
```
{ type, possession, quarter, game_clock_seconds, is_home, player_id, pts,
  ...type-specific fields }
```

| type    | player_id | pts       | type-specific fields |
|---------|-----------|-----------|----------------------|
| `SHOT`  | shooter   | 2 / 3 / 0 | `shot_type`, `sub_type`, `made` |
| `FOUL`  | fouler    | 0         | `foul_kind` (`shooting` / `non_shooting` / `offensive`), `fouled_on` |
| `FT`    | shooter   | 1 / 0     | `attempt` (1-based), `of` (total in trip), `made` |
| `REB`   | rebounder | 0         | `is_oreb` |
| `TOV`   | committer | 0         | — |
| `STL`   | stealer   | 0         | — (paired with a `TOV` in same possession) |
| `BLK`   | blocker   | 0         | — (paired with a missed `SHOT` in same possession) |
| `AST`   | passer    | 0         | — (paired with a made `SHOT` in same possession) |

**Grouping**: events within a possession share a `possession` int. The frontend collates related events onto one readable row (e.g. a made shot + its assist render as `"P1 makes a 3 (P2 assists)"`) — this is a display concern, not a model concern. Real NBA PBP works the same way: row display is a view over granular stat attributions.

## Canonical event orderings per possession outcome

| outcome | event sequence |
|---|---|
| Clean make | `SHOT(made) [+ AST]` |
| Clean miss (no rebound seen this possession) | `SHOT(missed) [+ BLK]` |
| Clean miss + DREB | `SHOT(missed) [+ BLK] → REB(is_oreb=false)` |
| OREB → next attempt | `SHOT(missed) → REB(is_oreb=true) → SHOT(...)` (same possession) |
| And-1 | `SHOT(made) → FOUL(shooting) → FT(1 of 1)` |
| Foul-drawn miss | `FOUL(shooting) → FT(1 of N) → ... → FT(N of N)` — no SHOT event (this is the PR #8 fix at the event layer) |
| Bonus FTs | `FOUL(non_shooting) → FT(1 of N) → ... → FT(N of N)` |
| Non-shooting foul (no bonus) | `FOUL(non_shooting)` |
| Turnover | `TOV [+ STL]` |
| Offensive foul | `TOV → FOUL(offensive)` (same player id on both) |

## Chip categorization (frontend)

**Chips**: `SCORE`, `SHOT`, `FT`, `AST`, `REB`, `STL`, `BLK`, `TOV`, `FOUL` (9). Involvement is per-event, derived from `type` + `player_id`. This fixes today's miscategorizations automatically:

| case | today (before (b)) | after (b) |
|---|---|---|
| Bonus FTs | shooter tagged `SHOT` (bug) | shooter tagged `FT`; fouler tagged `FOUL` |
| Foul-drawn miss (post PR #8) | shooter tagged `SHOT` on a FGA=0 event | no SHOT event exists; shooter tagged `FT`; fouler tagged `FOUL` |
| And-1 | shooter tagged `SHOT`+`SCORE` (fouled_by field only) | shooter tagged `SHOT`+`SCORE`+`FT`; fouler tagged `FOUL` |

## Engine changes
- New `app/services/possession_events.py`: `possession_to_events(possession_result, header_ctx) -> list[dict]`. Pure translator, no RNG, no state. One test per outcome row above.
- New `derive_box_score(events, roster_ids) -> box_dict`. Pure. Replaces `apply_event`.
- `game_simulator.simulate_game`: after each `resolve_possession`, translate to events, append to `all_events`. The engine still needs a **live** box during simulation for rotation / foul-out patching. Approach: incrementally derive per-event as events are emitted (same order → deterministic), OR fold the foul-out check into event emission (a `FOUL` event whose target's cumulative PF hits 6 flags fouled-out on the event). Decide during implementation; prefer whichever keeps `derive_box_score` a pure function.
- Delete `box_score.apply_event`. Old tests replaced with `possession_to_events` + `derive_box_score` tests.
- `describe_event` → per-type dispatch (one small function per type). Cleaner.

## Frontend changes
- `types.ts`: `PossessionEvent` → discriminated union `SimEvent = ShotEvent | FoulEvent | FTEvent | RebEvent | TovEvent | StlEvent | BlkEvent | AstEvent`. Each has a `type` literal + typed fields.
- PBP renderer: switch on `type` → one line per event, with AST/BLK collated onto the parent SHOT row for display (one readable row per basketball moment, matching NBA official PBP conventions).
- `PlayerModal` involvement: derive tags from `(event.type, event.player_id === modalPlayerId)`, not field-sniffing.
- Chips: add `FT`. Fix `SHOT` involvement (only tag it on `SHOT` events, not "any event where scorer is this player").

## Testing

- **Regression fence** (backs the invariant): `tests/test_box_score_derivation_fixture.py` — loads a serialized fixture of 90 games' box scores captured on the parent commit and asserts `derive_box_score(events)` produces identical dicts. Fixture generation script committed to `scratch/` for future regen.
- **Unit** (`tests/test_possession_events.py`): one test per row in the canonical orderings table above.
- **Unit** (`tests/test_derive_box_score.py`): synthesized event streams → expected box dicts; edge cases (all-DNP roster, foul-out mid-quarter, OT continues correctly).
- **Frontend Vitest**: renderer per event type; FT chip; PlayerModal involvement filter under new shape; AST/BLK collation.
- **UAT**: single game — PBP reads naturally; every chip filters correctly; and-1 shows as one collated row; bonus FTs filter under `FT` and `FOUL` (not `SHOT`).

## Definition of Done
- [x] `possession_to_events` + `derive_box_score` implemented; `apply_event` deleted
- [x] 90-game fixture captured on parent commit; regression test asserts identical box scores
- [x] `describe_event` per-type dispatch (`describe_typed_event`; legacy `describe_event` deleted)
- [x] Frontend `SimEvent` union, PBP renderer, FT chip, collated display
- [x] `PlayerModal` involvement + filters correct under new shape
- [x] Full pytest + Vitest suites green; `npm run build` clean
- [x] UAT: PBP, chips, PlayerModal all correct on drama-m3 single game (2 UAT passes → 7 display fixes A–G)
- [x] Update CLAUDE.md / RFC.md guardrail to state box score is derived, not accumulated (this cleanup pass)

## Decisions (locked 2026-07-25)
- **Full split**: SHOT / FOUL / FT / REB / TOV / STL / BLK / AST each as separate events (not middle-ground; not the "keep AST/BLK inline" variant).
- **Derived box score** via pure function; accumulator removed. Streamed events banked for later, not this pass.
- **One PR** covering backend refactor + frontend PBP + chips.
- **Frontend collates** AST/BLK onto parent SHOT row for display (readable, matches real NBA PBP).
- **Baseline fixture** captured pre-refactor as the regression fence (90 games: 3 pairs × 3 seasons × 10 seeds).

## Future considerations (banked, NOT this pass)
- **Streamed events** for live/replay UI. Emit incrementally via callback/generator; frontend consumes as they arrive. Enables step-through playback and future spectator mode.
- **Real-NBA PBP export/compare** — granular typed events make a comparator against ingested NBA PBP feeds tractable.
- **Advanced stats derived from events** — usage on true FGA, foul-drawn rate on real shots, TS% on honest denominator, etc. Lands cleanly on top of the event stream; the honest-denominator finding from PR #8 becomes actionable here.
- **Per-player timeline UI** — event ribbon in `PlayerModal`.

---

# Season Sim Validation Pass — Session A (spec)

**Owner:** validation session, 2026-08-10.
**Type:** measurement + reporting; no engine changes; no new production code.
**Pipeline:** step 1 (spec) + step 6 (UAT-style verification with Xavier reviewing the report).

## Motivation

Every calibration mechanism shipped so far (`season_context`, `pre_negation_probs`, pre-bonus representation, `tov_scale=0.36`, `steal_rate=0.086`, foul-caution two-phase, PF-weighted foul draw, pace formula w/ foul-reset compensation) has been validated at the single-game or 60–100-game batch level. None have been exercised through the existing team-scoped season-sim scaffold (`app/services/season_simulator.py`) end-to-end against a real 82-game schedule. Before we build a season-sim UI (session B) or expand to full-league (session C), we need to know the current stack behaves coherently at season scale.

## Scope

- **One representative team, 2025-26 season, one seed, 82 games.** Use the existing `run_season_simulation` background task and `SimulationRun` persistence. Default `SimConfig` (which already carries all shipped defaults).
- **Read-only end-to-end.** No engine changes. No calibration tuning. If an aggregate metric is off, record it; only intervene if there's a structural bug preventing the run from completing coherently.
- **Compare where possible** against the single-game calibration numbers already banked in memory (avg team score, home win rate, FTA/tg, PF/tg).

## Team choice

**Boston Celtics (BOS)** — recurring reference team in prior calibration sessions; well-known 2025-26 roster (Tatum, Brown, Holiday, White, Porzingis); real W-L, home/away splits, and per-game scoring are ingested for direct comparison.

## Metrics (structured report)

Output as JSON + human-readable summary. Categories:

### Team season aggregates
- W, L, W%
- Home W/L, Away W/L, home-court boost
- Avg PTS scored (per game, home split, away split)
- Avg PTS allowed
- Avg total score (both teams combined)
- Blowout rate (|margin| ≥ 20)
- Close rate (|margin| ≤ 5)
- OT rate
- Margin distribution: mean, std, percentiles [10/25/50/75/90]

### Team per-game rates
- Avg FGA, FTA, PF, TOV, OREB, DREB, STL, BLK, AST
- Avg possessions (from box-derived accounting)
- Team FG%, 3P%, FT%

### Star player workloads (top 3 by minutes)
- MPG
- PPG, RPG, APG, SPG, BPG, TOPG
- FG%, 3P%, FT%
- Games played (out of 82)

### Structural health
- Games completed / attempted / failed
- Duplicated game IDs (should be 0)
- Persistence errors (log-only, no interrupt)
- Wall-clock time (total + p50/p95 per game)
- Memory footprint at start vs. end (rough proxy for state leak)
- Any `roster_cache` misses beyond the 2 teams

### Structural red-flags (flag but don't tune)
- Impossible standings (W+L != games_played, or games_played > 82)
- Player MIN > 48.5 per-game (regulation ceiling + OT allowance)
- Team score < 60 or > 180 (pathological single game)
- Star player DNP rate > 30% without an availability model
- Missing box lines for players who played
- State drift: any per-team metric that trends monotonically over the 82-game run when it should be stationary

## Comparison anchors (from existing calibration)

Single-game 2024-25 batch (per-team, DRAMA-M3 preset, current shipped defaults):

| Metric | Anchor | Season-scale expectation |
|---|---|---|
| Team score / game | 113.0 | ≈ 113 ± 2 |
| Home win rate | ~55% | ≈ 55% ± 6 |
| FTA / team-game | ~20.5 | ≈ 20.5 ± 1 |
| PF / team-game | 22.7 | ≈ 22.7 ± 1 |
| Blowout rate | ~26% | ≈ 26% ± 5 |

Real 2025-26 BOS anchors (post-ingestion): pulled from `TeamSeasonStats` + `PlayerSeasonStats`.

## Definition of done

- [ ] Script runs to completion (all 82 games persisted, no failures)
- [ ] Structured JSON report saved under `scratch/season_validation_bos_202526.json`
- [ ] Human summary printed to stdout with real-vs-sim table
- [ ] Xavier reads the summary and decides: proceed to B (frontend) / fix a structural bug / re-run with a different team
- [ ] Any structural red-flag is a blocker; statistical drift is a "note and move on"

## Non-goals

- No new engine constants
- No new SimConfig toggles
- No frontend work
- No calibration tuning even if a metric is off
- No full-league (1230-game) run

## Related memories

- [[project-myleague-vision]] — long-term direction shapes B/C design (not this session)
- [[feedback-simulation-engineering-loop]] — define → implement → instrument → validate; this session is the "validate" step for the shipped stack as a whole
- [[feedback-investigation-convergence]] — falsification is a session outcome; a clean pass tells us the stack is coherent, a red-flag tells us to fix an integration bug first

---

# Season Sim UI — Session B1 (spec)

**Owner:** frontend session, 2026-08-10.
**Type:** frontend feature; **one** backend endpoint addition (game-detail read).
**Pipeline:** all 8 steps (backend + frontend). Includes UAT.
**Blocked-by:** Session A DoD signed off (verified 2026-08-10).

## Motivation

The team-scoped season simulator has a complete backend (spec + create + start + poll + cancel + list + delete + per-game events) but zero UI. Session A validated the stack runs coherently. Session B1 wires up a **read-only browse** of an already-completed run — the smallest UI surface that exercises the whole rendering path (season-level aggregates + game list + game drill-in) without adding lifecycle-management state (create/start/cancel), which is B2.

## User flow (B1)

1. User opens the app → new **"Season"** tab in the header (existing single-game view stays as "Single Game" tab).
2. Season tab loads:
   - **Empty state** if no completed runs exist: message + "Season sims start via API for now — UI coming next session." (Placeholder for the B2 create-flow.)
   - Otherwise, auto-loads the **most recent completed run**.
3. Season view shows: team identity header, standings-line, game list.
4. Clicking a game row opens a **game detail view** (in-page swap; back button returns to the list).
5. Game detail reuses existing `LineScore`, `BoxScore`, `PlayByPlay`, `PlayerModal` components — no visual divergence from single-game.

## Components

- **App-level tabs** (new). `SingleGameView` (extracted from current `App.tsx` body) + `SeasonView` (new).
- **`SeasonView`** — top-level container; fetches most-recent completed run, drives the child views.
- **`SeasonHeader`** — team logo + name + season + record + config summary + timestamp.
- **`SeasonStandings`** — one-row summary: W-L (W%), home W-L, away W-L, PPG scored/allowed, blowout%, OT%.
- **`SeasonGameList`** — sortable table (date | opponent | score | W/L | OT badge). Row hover, click to drill in. Uses `TeamLogo` for opponent identity.
- **`SeasonGameDetail`** — hosts the reused `LineScore` + `BoxScore` + `PlayByPlay` + `PlayerModal`.

## Backend gap (one new endpoint)

`GET /simulations/{sim_id}/games/{game_id}` → returns the same `SimulateGameResponse` shape as `POST /simulations/game`.

- Re-simulates deterministically (same seed-derivation as the events endpoint).
- Response includes `home_score / away_score / quarter_scores / went_to_ot / home_box / away_box / events` — everything the existing single-game components already consume.
- 404 on missing sim/game; 422 if sim not complete.
- **Non-goal for B1:** persisting events. Keeping the "re-simulate on demand" approach the events endpoint already uses.

## API client additions (`frontend/src/api.ts`)

- `listSimulations()` — GET `/simulations/`
- `getSimulation(id)` — GET `/simulations/{id}`
- `getSeasonGame(simId, gameId)` — GET `/simulations/{simId}/games/{gameId}`

## State machine (B1)

Simple. No polling.

```
[loading] → fetches listSimulations
    ├─ no completed → [empty]
    └─ picks first completed → fetches getSimulation(id) → [season-loaded]

[season-loaded]
    ├─ user clicks game → fetches getSeasonGame → [game-detail]
    └─ user clicks tab → [single-game]

[game-detail]
    └─ back → [season-loaded] (list cached, no refetch)
```

## Visual conventions (reuse existing)

- Team logos / colors: `TeamLogo` + `franchiseFor` (era-aware).
- Game list W/L colored using existing `.pm-plus` / `.pm-minus` (green/red).
- Winning team's abbr/name renders in `readableOnDark(primaryColor)` in the game list.
- Season header follows the LineScore card treatment (top-border stripe in team primary color).
- Font stack + tabular numerals: already global from prior cosmetic pass.

## Loading / empty / error states

- **Loading (list fetch):** subtle centered spinner in the season card.
- **Empty (no completed runs):** informative message with a "Coming next session: create runs from the UI" note. Not stark.
- **Error (fetch fail):** error banner reusing existing `.error` class.
- **Game-detail loading:** overlay spinner inside the game-detail region; season list stays visible.
- **Game-detail error:** back button + inline error message.

## Tests

- **Backend:** `tests/test_api_season.py` — new endpoint test: 200 with expected shape for a valid sim/game; 404 for unknown IDs; 422 if sim not complete.
- **Frontend:** Vitest — `SeasonGameList` renders + sort behavior; `SeasonStandings` computes record correctly given a mock summary; `SeasonView` state-machine (loading → season-loaded → game-detail → back). No new snapshot tests.
- **Not tested (UAT territory):** visual polish, click interactions on real data.

## UAT scenarios

Xavier runs these against the deployed frontend after commit:

1. **Load season tab with Session A's persisted run.** Standings show 29-53, home/away split, PPG 113.5/119.6, blowout ~29%.
2. **Sort game list by date descending → ascending.** Verify order flips.
3. **Sort by margin.** Verify biggest blowouts float to top.
4. **Click a game row.** Verify LineScore + BoxScore + PlayByPlay render, same look as single-game view.
5. **Click a player in the season game's boxscore.** Verify PlayerModal opens.
6. **Click "Back to season".** Verify list re-renders without refetch, sort state preserved.
7. **Switch to Single Game tab and back.** Season data stays cached (no visible reload).
8. **Try loading with no completed runs** (delete existing runs via API or start with fresh DB): verify empty state renders informatively.
9. **Confirm no console errors, no unstyled flashes.**

## Definition of done

- [ ] Backend endpoint shipped + tested (200/404/422)
- [ ] Frontend components + API client + Vitest tests passing
- [ ] `npm run build` clean, no `tsc` errors, no browser console errors
- [ ] All 9 UAT scenarios pass
- [ ] Session A's persisted run is browseable end-to-end
- [ ] No changes to any existing single-game component (`LineScore`, `BoxScore`, `PlayByPlay`, `PlayerModal` untouched — pure reuse)
- [ ] Xavier sign-off on UAT
- [ ] Commit + push

## Non-goals (deferred)

- **Create/start/cancel flow** — B2.
- **Run picker** (choose which completed run to browse) — B2 or B3.
- **Preset / config UI** — B3.
- **Delete / re-run controls** — B3.
- **Standings across the league** — session C (full-league sim).
- **MyLeague between-games controls** — long-term ([[project-myleague-vision]]).
- **Persisting events** for game detail — stay with re-simulate-on-demand (matches existing events endpoint pattern).

## Related memories

- [[project-season-validation-a]] — the run being browsed
- [[project-myleague-vision]] — long-term direction; B1 doesn't close doors
- [[feedback-session-definition]] — B1 sized to one PR-sized concept
- [[feedback-verify-against-reference]] — game-detail reuses single-game components verbatim

---

# Season Sim UI — Session B2 (spec)

**Owner:** frontend session, 2026-08-10.
**Type:** frontend feature; no backend additions expected (backend endpoints already exist).
**Pipeline:** all 8 steps.
**Blocked-by:** B1 shipped (e00a91e).

## Motivation

B1 gave us a read-only browse of completed runs. B2 closes the loop: users can create and start a new season sim from the UI, watch it progress, and cancel if needed. The season simulator is otherwise inaccessible without curl or the scratch script.

## User flow (B2)

**Season tab, empty state (no completed OR active runs):**
1. Existing empty message replaced with a "Start a new season" call-to-action.
2. User picks team + season + seed (optional) + preset (optional).
3. Clicks "Simulate Season" → POST /simulations/, then POST /simulations/{id}/start.
4. View switches to **running state**: progress bar, X of 82 games, cancel button, live team logo, running record if computable.
5. Poll every ~1s.
6. On complete → auto-transition into the B1 browse view for this run.
7. On failed → error banner, "Try Again" button returns to the form.
8. Cancel button → POST /simulations/{id}/cancel → transitions to cancelled state → "Discard" or "Browse partial" (if any games completed).

**Season tab, existing run:**
- Auto-load most recent run (same as B1).
- Header gets a **"New Season Sim"** button that surfaces the form (inline expand, not a modal) so the user can kick off another without losing the current view.
- If a run is already active (status = pending or running), the button opens directly into the running-state view instead of the form.

## Components (new)

- **`NewSeasonForm`** — team select + season pill row (reuse pattern from GameControls) + seed input + preset select + submit button. Disables submit while a run is active (server enforces this too).
- **`SeasonRunningState`** — big progress bar, X/82 counter, cancel button, elapsed time, ETA (linear from elapsed).
- **`SeasonView`** (extend) — gains state machine for form / running / browse / error transitions.

## State machine (B2)

```
[loading list]
    ├─ any pending/running    → [active-run: fetch + poll]
    ├─ any complete           → [browse: B1 flow] + "New Sim" button available
    └─ nothing                → [form]

[form]
    submit → POST /simulations/ → POST /start → [active-run]
    server 409 (already running) → refetch list → transition to the active run

[active-run]
    poll every 1s → status updates
        complete    → [browse for this run]
        failed      → [error, back-to-form option]
        cancelled   → [browse partial if any games; else form]
    cancel button → POST /cancel → poll continues until status changes
    tab switch away → polling paused, resumes on return

[browse]
    (B1 behavior)
    "New Sim" click → [form] (browse state preserved for cancel/back)
```

## API surface

- `listSimulations()` — existing (B1).
- `getSimulation(id)` — existing (B1).
- `getSeasonGame(id, gameId)` — existing (B1).
- `createSimulation(body)` — new: POST `/simulations/` with `{team, season, seed?, config?}`.
- `startSimulation(id)` — new: POST `/simulations/{id}/start`.
- `cancelSimulation(id)` — new: POST `/simulations/{id}/cancel`.

Existing backend errors to surface cleanly:
- 409 Conflict on create when another sim is already running → refetch and jump into the active run.
- 422 on start if the sim is not pending (race condition) → same recovery.
- 422 on cancel if already complete/cancelled → just refresh.

## Polling

- Interval: **1000ms** (games take ~80ms each, so 12–13 games between polls — enough resolution for a smooth progress bar without overwhelming the server).
- Uses `setInterval` scoped to the active-run state; cleared on state change or unmount.
- Backoff / stop: if the poll returns the same `games_completed` for 30 consecutive seconds AND status != running, assume something's wrong and surface an error.
- Tab-switch: polling pauses when the Season tab is inactive (via `document.visibilityState`).

## Empty / loading / error states

- **Form loading:** disabled submit + spinner while POST + start-POST are in flight.
- **Form validation:** submit disabled until team + season are set (seed optional; preset defaults to `drama-m3`).
- **Progress bar:** styled band matching team-color stripe pattern; % + count both shown.
- **Cancel-in-progress:** cancel button becomes "Cancelling…" until backend reflects the state.
- **Error banner:** reuse `.error` class + include an inline "Try Again" (go back to form) or "View Partial Results" (if some games completed).

## Reuse conventions

- Season pill row from GameControls → same component pattern, single-team variant.
- Team select — same fixed-width native select as single-game view.
- Preset select — same fixed-width native select.
- Progress bar: single new `.progress-bar` primitive; team-color fill.
- No new backend endpoints.

## Tests

- **Frontend Vitest:**
  - `NewSeasonForm` — submit disabled until team + season set; POST called with right body; 409 recovery flow (mock listSimulations → running).
  - `SeasonView` state machine — form → active → browse transitions; cancel button flow.
  - Poll interval mocked with `vi.useFakeTimers()` — verifies polling stops on status change.
- **No new backend tests** — existing 362 covers the endpoints being consumed.

## UAT scenarios

1. **Fresh state** (delete all runs via API): Season tab shows the form. Fill BOS + 2025-26 + seed 26 + drama-m3 → Simulate Season → progress bar advances through 82 games → auto-transitions to B1 browse showing 29-53 record.
2. **Cancel mid-run:** start a run, click Cancel around game 20 → status flips to cancelled → view shows partial-results option OR back-to-form.
3. **Concurrent create:** with a run active, refresh the page. Should land directly in the active-run view (not the form).
4. **Preset switch:** kick off a run with `baseline` preset → verify seed derivation still deterministic (same seed → same result across preset changes ISN'T expected; document it).
5. **Team change from active-run view:** run BOS to completion → click "New Sim" → form re-appears with prior selections but user can change team. Start OKC. Runs cleanly.
6. **Failed run recovery:** simulate a failure (delete the roster mid-run via a scratch call) → error banner + Try Again.
7. **Tab-switch pause:** switch to Single Game while a run is active → verify polling stops (Network tab quiet) → switch back → polling resumes and catches up.
8. **Console clean** through all above.

## Definition of done

- [ ] `NewSeasonForm`, `SeasonRunningState`, `SeasonView` state machine built + tested
- [ ] Poll interval + visibility-based pause working
- [ ] All error paths (409/422/failed) recoverable without page refresh
- [ ] Both create and cancel flows byte-clean through the backend endpoints (already tested backend-side)
- [ ] All 8 UAT scenarios pass
- [ ] Xavier sign-off

## Non-goals

- **Run picker** (choose which past completed run to browse) → B3
- **Delete run** → B3
- **Config overrides beyond preset** → B3
- **Notification when a background run finishes on another tab** → not this session
- **Multi-team runs / full-league** → session C
- **MyLeague between-games flow** → long-term

## Related memories

- [[project-session-b1-shipped]] — the surface being extended
- [[project-myleague-vision]] — B2 should keep doors open (avoid batch-only assumptions)
- [[feedback-session-definition]] — B2 is on the edge; polling + state machine adds real complexity. If it slips, split into B2a (create + start) + B2b (poll + cancel).

---

# Season Sim UI — Session B3 (spec)

**Owner:** frontend session, 2026-08-11.
**Type:** frontend feature; existing backend endpoints only.
**Blocked-by:** B2 shipped (d177057).

## Motivation

B2 gave us create/start/poll/cancel. Users can now kick off runs and browse the most recent completed one — but past runs are inaccessible from the UI (they exist in the DB, `GET /simulations/` returns them, but nothing consumes the list). B3 closes that gap: a compact run picker so users can jump between historical runs, plus destructive controls (delete + re-run) that round out CRUD.

## User flow

- **Season header** gains a "Runs (N)" dropdown or button that opens a compact list of all persisted simulations (most recent first). Each row shows: team abbr / logo · season · status badge · record (if complete) · timestamp.
- Clicking a row loads that run into browse view.
- **Row-level controls** appear on hover: `Delete` (blocked while running per backend), `Re-run` (pre-populates the form with team + season + preset + seed).
- **`Re-run`** transitions to the form (mode=form) with fields populated. User can tweak seed/preset before submitting.
- **`Delete`** confirmation prompt inline; on confirm, removes the row and refreshes; if the deleted run was the currently-loaded one, either load the next most recent or drop to form.

## Components

- **`RunPicker`** — dropdown/menu inside `SeasonHeader`. Reuses existing header space to the right of the identity block.
- **`NewSeasonForm`** — extended to accept optional `prefill` props (team, season, seed, preset).
- **`SeasonView`** — new state actions: `switchRun(id)`, `deleteRun(id)`, `rerunFromRun(id)`.

## API surface

All existing:
- `listSimulations()` — already fetched at init, refetched after delete.
- `getSimulation(id)` — already used.
- New tiny helper: `deleteSimulation(id)` — DELETE `/simulations/{id}`.

## Visual conventions

- Dropdown styled like `.season-timeline` (dark panel, rounded, muted). Compact rows.
- Status badges: `.status-complete` (green dot), `.status-cancelled` (yellow), `.status-failed` (red), `.status-running` (blue with pulse).
- Delete confirmation: inline "Sure? [Yes] [Cancel]" swap, no modal.
- Re-run button = accent color like the form's Simulate button.

## Tests (Vitest)

- `deleteSimulation` — API call fires correct URL.
- `SeasonView` — clicking a picker row calls `getSimulation` with the right id and enters browse mode.
- `SeasonView` — delete of the currently-viewed run drops to next most recent (or form when none left).

## UAT scenarios

1. Multiple completed runs → picker shows all with correct labels + timestamps.
2. Switch between runs — browse view updates to the selected run.
3. Delete a run — disappears from picker, currently-viewed logic handles gracefully.
4. Re-run — form pre-populated; can tweak seed and submit.
5. Console clean.

## Definition of done

- [ ] RunPicker component + delete + re-run flows built
- [ ] Vitest tests green (target 39+ total)
- [ ] `npm run build` clean
- [ ] All 5 UAT scenarios pass
- [ ] No changes to existing browse / running / form components beyond additive props

## Non-goals

- **Sim-vs-real averages** — B4.
- **Full-league expansion** — session C.
- **Advanced filters** (by team / season / preset) — future if list grows.

## Related

- [[project-session-b2-shipped]] — foundation
- [[project-next-session-focus]] — B3 is the "close out season-sim UI CRUD" step

---

# Season Sim UI — Session B4 (spec)

**Owner:** frontend session + one backend addition, 2026-08-11.
**Blocked-by:** B3 shipped (`2dcf90d`).

## Motivation

A completed season sim persists every player's per-game line. Users can see individual game boxscores but there's no season-level view of a player's simulated averages, nor a comparison to their real NBA numbers. Xavier's ask (2026-08-10): "sim averages to correspond with the real life NBA averages for the players/teams" — banked in [[project-sim-vs-real-averages]] and now scheduled as B4.

## User flow

- In the Season browse view, add a compact **"Averages"** view that lists every player who played in the season, with sim season averages (MPG/PPG/RPG/APG/etc.) and real-NBA anchors from ingested `PlayerSeasonStats` side-by-side.
- Sortable by any column (MPG default descending).
- Also expose team-level aggregates (per-game FGA/FTA/TOV/PF/etc.) sim vs real from `TeamSeasonStats` in a smaller strip.
- Toggle in the season header: "Games / Averages" — two views on the same run, no separate page.

## Backend gap (one endpoint)

`GET /simulations/{sim_id}/averages` → structured payload:

```
{
  "sim_id": 30,
  "team": "LAL",
  "season": "2025-26",
  "team_totals": {
    "sim": { "gp": 82, "ppg": 113.5, "opp_ppg": 119.6, "fga": 83.7, "fta": 20.8, "pf": 22.4, ... },
    "real": { "pace": ..., "off_rating": ..., "def_rating": ... }  // whatever TeamSeasonStats carries
  },
  "players": [
    {
      "player_id": 2544, "name": "LeBron James",
      "sim":  { "gp": 82, "mpg": 34.7, "ppg": 22.3, "rpg": 7.8, "apg": 8.1, "spg": ..., "bpg": ..., "topg": ..., "fg_pct": ..., "fg3_pct": ..., "ft_pct": ... },
      "real": { "gp": 66, "mpg": 34.2, "ppg": 24.1, "rpg": 7.5, "apg": 8.3, ... } | null
    },
    ...
  ]
}
```

- Sim aggregates computed from persisted `SimulatedPlayerLine` rows for this simulation_id (game-count divisor uses played rows only, i.e. minutes >= 0.5 — matches existing DNP filter).
- Real anchors from `PlayerSeasonStats` on `(player_id, season)`; `null` if the player has no real season stats (rookie prospects, mid-season debuts, etc.).
- Team totals: sim aggregates from persisted lines; real from `TeamSeasonStats`.

Note: **known persistence-filter quirk** ([[project-season-validation-a]]) — sub-0.5-min lines aren't persisted, so team MIN sum will be ~11 low. Endpoint returns whatever the DB has; the UI can label the sim MIN column with a footnote.

## Components (new)

- **`SeasonAverages`** — top-level container inside SeasonView; toggled via header.
- **`SeasonAveragesTable`** — sortable player table.
- **`SeasonTeamAverages`** — team-level side-by-side strip.

## Frontend layout

- **Header toggle:** two-button pill row like the app-level tabs, but inside the season header: `Games | Averages`.
- **Team strip** at top: MIN, PTS, OPP PTS, FGA, FTA, PF, TOV, +/- side-by-side sim vs real. Simple two-row table.
- **Player table columns:** Player · GP (sim/real) · MPG · PPG · RPG · APG · STL · BLK · TOV · FG% · 3P% · FT% — each column shows both sim + real inline as `X.X / Y.Y` (or just sim if no real).
- Sort by any column; default MPG desc.

## Visual conventions

- Reuse `.box` table styling for the player table.
- Reuse `readableOnDark` for the header team-color stripe.
- No new colors; muted-vs-text pattern for real-vs-sim distinction.

## Tests

**Backend (`tests/test_season_averages.py`):**
- 200 for a completed run — payload shape check.
- 404 for unknown sim.
- Sim averages match sum-of-lines / GP.
- Real anchors present when `PlayerSeasonStats` exists; `null` when absent.

**Frontend (Vitest):**
- Aggregation helper (given a mock payload) produces the expected rendered rows.
- Sortable columns behave (click column → sort desc).
- Toggle switches views without refetching the run.

## UAT scenarios

1. Load a completed run → header shows "Games / Averages" toggle. Games view default.
2. Click Averages → team strip + player table appear.
3. Team strip shows sim PPG/opp PPG matching what the standings row showed.
4. Player rows show sim + real for known players (e.g. LeBron real MPG close to sim MPG within 1-2).
5. Rookies / missing-real players render sim only with "—" in real columns.
6. Sort by PPG desc → top scorer floats to top; click again → asc.
7. Switch back to Games → run picker + game list restored.
8. Console clean.

## Definition of done

- [ ] Backend endpoint + tests green
- [ ] Frontend components + Vitest green
- [ ] `npm run build` clean
- [ ] 8 UAT scenarios pass
- [ ] Xavier sign-off

## Non-goals

- No advanced-stats derivations (TS%, eFG%, PER, etc.) — future.
- No cross-season / career comparisons — future.
- No visual charts / bar plots — text table only.
- No editing (adjusting predictions) — MyLeague territory.
- No historical-run averages comparison across sims — future.

## Related memories

- [[project-sim-vs-real-averages]] — Xavier's original ask
- [[project-season-validation-a]] — the compute path already validated
- [[project-session-b3-shipped]] — the surface we're extending

---

# 7-Fouls Fix — Fouled-out Player Filter (spec)

**Owner:** sim-engine fix session, 2026-08-11.
**Type:** engine behavior change; box_score fixture recapture required.
**Blocked-by:** [[project-bug-7-fouls-jokic]] investigation (root cause identified).

## Bug summary

Fouled-out players remain on the court set until `patch_rotation`'s next-minute effect kicks in. Any on-court consumer that fires in the same minute (or in OT after a Q4 foul-out where `patch_rotation` targets minute 48 but OT clamps back to 47) can re-select the fouled-out player. Two reproduced manifestations:

- **Bug A (OT):** Spencer Jones fouls out at Q4 4s → still on floor in OT → 7th foul at Q5 74s. Run #30 `0022500974`.
- **Bug B (same-minute strategic-foul spam):** Isaiah Stewart fouls out at Q4 52s → strategic-foul picker re-selects him at Q4 29s. Run #36 `0022501210`.

Underlying: `home_active_ids`/`away_active_ids` and `defense_on_court` are computed from rotation lookup, but rotation doesn't reflect the just-fouled-out state.

## Fix (minimal, targeted)

Track a `fouled_out: set[int]` at `_run_clock_period` scope, updated the instant `apply_typed_event` returns `ev_fo`. Every on-court consumer filters against it.

Three consumers:
1. **Regular possession on-court sets** (near `_apply_possession` call in `_run_clock_period`) — `home_active_ids = [pid for pid in rotation_lookup if pid not in fouled_out]`.
2. **Strategic-foul defender picker** (`game_simulator.py:431-439`) — `defense_on_court = [p for p in ... if p["id"] not in fouled_out]`.
3. **Any similar lookup I find during implementation** — grep `home_active_ids`, `away_active_ids`, `on_court`.

Fouled_out is cleared at end of `_run_clock_period` (per period? no — a player who fouled out in Q1 stays out for the rest of the game). Cleared at game start (implicit via new game state).

## Non-goals

- **Not touching `patch_rotation`** — its rotation-schedule semantics are fine; we're layering a fouled-out filter on top so it works between minute boundaries.
- **Not touching foul-out threshold logic** — still 6 PF via `apply_typed_event`.
- **Not adding new tests for existing rotation behavior.**

## Impact hypothesis

Directional predictions to validate:
- **PF/team-game:** slight decrease (a few illegal fouls no longer emitted per season).
- **Foul-out rate:** essentially unchanged (players still fouled out at 6; the illegal 7th event was a re-attribution not an additional foul-out).
- **OT rate:** possibly higher — when trailing team's best fouler is out, strategic-foul path picks a different (weaker) fouler → fewer effective intentional fouls → less clock-management late → possibly more OTs? Or unchanged if effect is small.
- **Late-Q4 close-game texture:** minor scoring shifts. Games where fouled-out player was the strategic-foul target will look different.

**All above deltas should be small.** If aggregates move ≥1 per team-game on any metric, that's evidence the fix has a compensating effect worth thinking about.

## Test plan

1. **New unit tests:**
   - `tests/test_foul_out_filter.py` — direct assertion: given a `_run_clock_period` scenario where a strategic foul fouls out player X, subsequent same-minute strategic fouls do NOT re-select X.
   - Same test for OT: player X fouls out at Q4 last minute → not on court in Q5.
2. **Fixture recapture:**
   - `tests/fixtures/box_score_baseline.json` WILL drift on the affected games. Re-capture via `scratch/capture_box_baseline.py`.
   - Diff the old vs new fixture; document which games / players changed and by how much.
3. **Aggregate impact measurement:**
   - Re-run `scratch/season_validation_bos_202526.py` (Session A instrument) with the fix. Compare PF/tg, foul-out rate, OT rate, avg score to Session A's baseline.
   - If BOS 82-game aggregates move by <0.5 per-game on any metric, ship. Otherwise, investigate the delta before shipping.

## Definition of done

- [ ] Fouled-out set + filter added at all identified consumer sites
- [ ] New unit tests green
- [ ] `tests/test_box_score_derivation_fixture.py` still passes (or is updated with re-captured fixture + explicit note on which cells moved and why)
- [ ] Full backend suite green
- [ ] Re-run of BOS 82-game validation against pre-fix baseline; deltas classified and reported
- [ ] Bug A + Bug B specifically re-verified fixed on runs #30 (LAL@DEN OT) + #36 (ATL@DET)
- [ ] Memory update: [[project-bug-7-fouls-jokic]] → closed with before/after numbers
- [ ] Commit + push

## Related

- [[project-bug-7-fouls-jokic]] — investigation memo (root cause + reproducibility)
- [[feedback-refactor-behavior-invariance]] — refactor discipline; this is a *behavior* change though, so different discipline applies
- [[feedback-accounting-as-validation]] — measure before / after residuals before calling it done
- [[feedback-simulation-engineering-loop]] — define → implement → instrument → validate

---

# Sim-Realism Analysis Session (spec)

**Owner:** measurement session, 2026-08-12.
**Type:** validation/audit; NO engine changes; NO tuning.
**Pipeline:** step 1 (spec) + step 6 (UAT-style review of the report).
**Blocked-by:** 7-fouls fix shipped (`5e14078`); cleanup shipped (`70b867a`).

## Motivation

Session A validated one team's season-scale run coherently. Sessions B1-B4 built the UI to browse runs. During UI work Xavier casually flagged aggregate discrepancies (BOS W-L wildly under real, Brown MPG deficit, home advantage absent). None have been probed cross-team. This session extends the validation to a four-team panel and classifies each observed discrepancy as (a) systemic mechanism gap, (b) team-specific roster interaction, (c) RNG variance / sample-size noise.

**Core philosophy (Xavier 2026-08-12):**
> This is a realism audit, not a hunt for reasons to tune the simulator. If BOS is weird, then OKC/DEN/GSW tell us whether it's team-specific. If all four are weird in the same direction, that's evidence for a systemic mechanism. If only one roster profile is weird, that's much more interesting than simply saying "the simulator is off."

## Scope

- **Four teams, 2025-26, one seed per team (distinct + recorded)**, full 82-game schedule.
- Team profiles chosen for structural contrast:
  - **BOS** — Session A baseline for continuity
  - **OKC** — young rotation-heavy roster; stresses depth allocation
  - **DEN** — single-star (Jokic) usage-concentrated; stresses star model
  - **GSW** — spacing-heavy, high-3PA offense; stresses shooting/pace balance
- **Distinct seeds per team**, recorded in report: BOS=26, OKC=27, DEN=28, GSW=29. Distinct to avoid shared-RNG artifact; deterministic to enable re-run.
- **Preset:** drama-m3 (matches Session A + all prior calibration anchors).

## Metrics + thresholds (defined ahead of run per Xavier)

### Record realism
- Raw W-L record + real W-L (from 2025-26 game outcomes)
- **Win-pct delta** — sim W% − real W% (so 41-41 vs 45-37 is +0.049)
- Absolute wins delta
- Comment on both raw + pct so a 4-game gap in 82 doesn't look bigger than it is

### Home advantage
- Sim home W%
- Sim home-vs-away W% differential
- Real home W% (from real 2025-26 outcomes for the same team)
- Distinguishes "home teams win at the right rate" vs "home/away effect is directionally correct"

### Blowouts
- **Threshold locked pre-run: 20+ point margin.**
- Report: blowout rate (%) sim + real
- Also: full margin distribution (p10, p25, p50, p75, p90) sim + real

### OT
- Sim OT/game rate
- Real OT/game rate for the team
- **Explicit sample-size caveat: 4×82 = 328 games is too few to establish that any OT-rate delta is systemic.** Report as descriptive only.

### Star-usage concentration
- **Top-1 share of team FGA** (top player's FGA / team FGA)
- **Top-2 share of team FGA**
- **Usage-based top-1 share** (top player's FGA / total scoring possessions when on court — approximation from top-player pmg × sim pace)
- Compare against real analogues from PlayerSeasonStats (`fga` per player)

### Score / pace / possession-level
Compare sim season vs single-game calibration anchors (all per team-game):
- PPG scored / allowed
- FGA / FTA / PF / TOV
- stat_poss (approximated via `FGA + 0.44 × FTA + TOV − OREB` from persisted lines)
- Pace (if TeamSeasonStats provides real anchor; otherwise sim only)

### Rotation model spot-checks
- MPG deficit / surplus for top-5 by minutes (real MPG minus sim MPG)
- PF/team-game vs real
- Fouled-out rate (games where any player hit PF=6)

## Classification taxonomy

For each observed discrepancy, tag as one of:
- **`systemic`** — shows up cross-team in the same direction (all four teams under-produce OT, under-serve stars, etc.); likely a mechanism gap
- **`roster-interaction`** — shows up on 1-2 teams, ties to a structural profile difference (young-rotation team's rotation is systemically wrong; single-star team's usage cap misfires)
- **`sample-noise`** — magnitude smaller than expected variance for 4×82; can't be distinguished from RNG
- **`data-gap`** — the discrepancy is against a real anchor we don't have or don't trust
- **`already-known`** — matches a previously-banked residual

## Not this session

- **No tuning.** No SimConfig changes. No calibration constant tweaks. No mechanism edits.
- If a discrepancy screams for a fix, add to `project-next-session-focus` and move on.
- No new teams beyond BOS/OKC/DEN/GSW.
- No new backend / frontend features.

## Deliverable

- `scratch/sim_realism_audit_2025_26.json` — full machine-readable report:
  - per-team block: identity (team, season, seed, run id), record, home split, margin distribution, star concentration, per-team-game rates, top-5 rotation
  - cross-team summary: which metrics show systemic patterns, which vary
  - classification table: metric × team × tag
- Memory memo: `project-sim-realism-audit-a.md` — human-readable narrative of what we learned
- Update `project-next-session-focus` with any actionable items surfaced

## Related memories

- [[project-season-validation-a]] — the single-team baseline this extends
- [[project-sim-vs-real-averages]] — B4's data plumbing (endpoint returns most of what we need)
- [[feedback-simulation-engineering-loop]] — this is the "validate" step for a broader cross-section
- [[feedback-investigation-convergence]] — falsification is a session outcome; if a suspected residual doesn't hold up cross-team, that's a real finding

---

# Star-MPG Minute-Allocation Instrument (spec)

**Owner:** measurement session, 2026-08-13.
**Type:** instrumentation; NO engine changes; NO tuning.
**Pipeline:** step 1 (spec) + step 6 (Xavier review of the report).
**Blocked-by:** realism audit banked (`dd9fa3b`).

## Motivation

The 2026-08-12 realism audit ([[project-sim-realism-audit-a]]) confirmed a CROSS-TEAM SYMPTOM: top-creator minutes are systematically ~4-6 lower than real across all four roster profiles (Jokic -5.6, Brown -4.5, Curry -3.6, Butler -6.1), with compensating bench minutes. The symptom is confirmed; the specific causal mechanism is not. Before touching any rotation code, we instrument the complete minute-allocation chain and answer the question Xavier posed:

> Where do the star's missing minutes actually go?

If the missing minutes flow into scheduled bench rotation, that localizes the mechanism to the pre-game rotation builder. If they flow to game-state triggers (foul trouble, garbage-time subs, fatigue), the mechanism is per-possession. We don't want to invent a "star preference" lever if the true cause is elsewhere.

## Scope

- **Same 4-team panel + seeds as the realism audit** — BOS=26, OKC=27, DEN=28, GSW=29.
- **Same preset (drama-m3), same season (2025-26).**
- Panel-wide minute-decomposition report for the **top-1 by real MPG** on each team (Jokic / Brown / Curry / Butler) plus top-2 for context.
- No engine changes. No SimConfig tweaks. No mechanism proposals in this session.

## Instrument — data to capture, per team

The instrumentation must trace minutes at multiple resolutions. Every metric below is *sim-side only* (per-possession trace) unless labeled real.

### 1. Top-line aggregates
- Top-1 player: MPG total, real MPG anchor, delta
- Top-2 share of team minutes
- Starter (top-5 by MPG) vs bench (rest) minute totals
- Same for real from `PlayerSeasonStats`

### 2. Substitution events per top-1
- Count of substitutions per game (in + out)
- Distribution of sub timing within quarters
- Whether subs cluster on fixed minute boundaries (e.g. Q1 6:00 → out, Q1 3:00 → in) vs. game-state triggers

### 3. Foul / availability contribution
- Top-1 fouled-out rate (games where PF reached 6)
- Games where top-1 hit 4+ fouls (foul-trouble threshold)
- Total minutes lost to foul-trouble subs
- Games with top-1 marked unavailable (`use_availability` config check — probably `False` at drama-m3 default; confirm)

### 4. Quarter-by-quarter allocation
- Top-1 minutes per quarter (Q1/Q2/Q3/Q4)
- Real MPG per quarter (from `PlayerGameLog` if available — otherwise skip with data-gap flag)
- Sim Q4 minutes: close-game (|margin| ≤ 8 with < 5 min in Q4) vs. blowout (|margin| ≥ 20 at end of Q3)

### 5. Rotation schedule inspection
- Load `build_rotation` output for the top-1 pre-game
- Compare scheduled MPG vs actual MPG (does the rotation TELL the sim to play the star ~X min, and the sim delivers that? or is the rotation itself under-allocating?)
- Whether the top-1's rotation slot has a hard-coded cap

### 6. Missing-minute destinations
- For each ~4-6 missing minute, identify which bench player caught them (by comparing sim MPG deltas — real MPG for the bench player minus sim MPG)
- Ranked list of "gainers" — the sim's bench over-servers
- Are the gainers concentrated (1-2 players) or diffuse (5+ players)?

### 7. Fatigue / rest logic
- Is there any `fatigue` or explicit rest mechanic in the current sim (`use_fatigue` config; grep for stamina/rest constants)?
- If yes, how much does it contribute to top-1 minute loss?

### 8. Cross-team comparison table
- 4-team panel: for each metric above, side-by-side.
- Compute cross-team consistency: is the pattern the same on all 4? Or does DEN's Jokic differ from BOS's Brown?

## Deliverable

- `scratch/star_mpg_probe_2025_26.py` — the instrument script.
- `scratch/star_mpg_probe_2025_26.json` — structured per-team + cross-team report.
- Memory memo: `project-star-mpg-probe.md` — findings + causal localization + priority-ordered follow-ups.
- **No** production code changes.

## Key question locking

**Primary question:** Where do the star's missing minutes actually go?

**Secondary questions:**
- Do bench "gainers" match across teams (systemic scheduler bias) or vary by team (roster-interaction)?
- Is the top-1 missing more minutes in close games or blowouts?
- Does the current sim rotation output SCHEDULE the missing minutes to the bench, or does the sim DEVIATE from the scheduled top-1 minutes?

## What this session does NOT do

- No fix. No tuning. No mechanism proposal.
- No changes to `rotations.py`, `game_simulator.py`, `select_active_roster`, or `build_rotation`.
- No new SimConfig field.
- No fixture recapture — instrumentation is add-only.

## Definition of done

- [ ] Instrument runs to completion for all 4 teams
- [ ] JSON report written with all 8 categories
- [ ] Memo written classifying findings (where do the minutes go? scheduled or game-state?)
- [ ] Xavier reviews and decides: (a) proceed to a mechanism fix session with a specific target, (b) probe further before proposing, or (c) bank as understood and pursue another arc
- [ ] Baseline preserved for future comparison

## Related memories

- [[project-sim-realism-audit-a]] — the source of the star-MPG finding
- [[project-season-validation-a]] — Session A baseline includes Brown MPG anchor
- [[feedback-causal-probe-before-mechanism]] — this session IS the falsification step
- [[feedback-investigation-convergence]] — converge on the specific engine decision producing the aggregate

---

# Garbage-Rotation Inversion Fix (spec)

**Owner:** dedicated bug-fix session, 2026-08-13.
**Type:** sim-engine behavior change.
**Pipeline:** all 8 steps.
**Blocked-by:** bug fully diagnosed in [[project-garbage-rotation-inversion]].

## Motivation

`MODE_GARBAGE` in `app/services/rotation.py:114` selects `players_by_min[-5:]` — the last 5 in the roster's ordered list. The assumption is "last 5 = deepest bench." That assumption is false when the roster's `minutes` field reflects **availability-normalized season shares** (`MPG × games_played` → normalized to 240) rather than role hierarchy.

For GSW 2025-26 the roster orders Podziemski (28.4 MPG × 82 games = high share) above Curry (30.9 MPG × ~40 games = lower share). `[-5:]` returns Curry+Butler+Melton+Horford+Porzingis — the actual starting five. **Garbage mode fires correctly and then promotes the stars.**

Confirmed reproduction: run #53, game `0022501060`, Curry played 43.6 min in a +59 blowout.

## Root cause chain

1. `roster.py:330-334`: `minutes` field on each player = `w / total * 240` where `w = MPG × games_played`. Correct for share-of-team-minutes accounting; **incorrect as a role-hierarchy indicator when a star misses games**.
2. `game_simulator.py:194-195`: `home_by_min = sorted(home_players, key=lambda p: p["minutes"], reverse=True)`. Uses the field from (1) — inherits the inversion.
3. `rotation.py:114` (MODE_GARBAGE): `eligible[-5:]` from `players_by_min` — assumes descending-by-hierarchy, gets ascending-by-availability instead.
4. `rotation.py:104-109` (foul-trouble subs "best available replacement"): also traverses `players_by_min`. **Same latent inversion — needs verification whether it bites.**

## Design choices

### Option A — Add a role-hierarchy field alongside the availability field
Introduce `role_mpg` (or `mpg` — already partially set at `roster.py:323` but not used by rotation) that stays constant regardless of availability. Sort `players_by_min` by this field for rotation-decision purposes. Keep the availability-normalized `minutes` for scheduled-minute allocation.

- Pros: minimal blast radius; explicit separation of concerns.
- Cons: introduces a second ordering — every consumer must be audited to pick the right one.

### Option B — Use `mpg` (per-game-played) directly for all rotation ordering
Replace `sorted(..., key=lambda p: p["minutes"], ...)` with `sorted(..., key=lambda p: p["mpg"], ...)` in `game_simulator.py:194-195`. The `mpg` field already exists (line 323) as "raw per-game-played minutes."

- Pros: single field; matches real-world "who is the star" better than availability-adjusted total.
- Cons: `build_rotation` may implicitly rely on the normalized field to build the minute schedule that sums correctly to 240. Need to audit that path too.

### Option C — Reverse the slice direction to `[:5]`
Change `MODE_GARBAGE` to return `eligible[:5]` (first 5, assumed to be starters). Only touches garbage mode itself; hyper-targeted.

- Pros: minimal diff; doesn't touch the roster or ordering.
- Cons: **THIS IS BACKWARDS.** MODE_GARBAGE is meant to bench the stars, not put them in. `[:5]` under a descending sort of the CORRECT ordering would put the starters on the floor — the opposite of what garbage time is for. Documenting for completeness only.

### Option D — Do not slice; explicitly identify starters
Track "starter" flags at roster load (already done: `roster.py:322` sets `is_starter = i < 5`). Have MODE_GARBAGE return all NON-starter eligible players trimmed to 5. Similarly, foul-trouble sub logic could prefer non-starters.

- Pros: explicit; doesn't depend on any ordering assumption.
- Cons: `is_starter` uses the same `enumerate(players)` order (i.e. same availability-normalized field), so it inherits the same bug. Fix must cascade.

## Recommended approach — Option B

**Sort `players_by_min` (in both `home_by_min` / `away_by_min`) by `mpg` descending, not `minutes` descending.**

Rationale:
- `mpg` = raw MPG per game played = correct role-hierarchy indicator.
- `minutes` (availability-normalized) can continue to drive `build_rotation`'s scheduled-minute allocation (that's where it's correct — the schedule SHOULD reflect availability).
- Every downstream consumer of `players_by_min` (garbage mode, foul-trouble replacement, patch_rotation) is looking for role hierarchy, not availability-adjusted share.

`is_starter` should be re-derived from `mpg` for consistency (or scrapped if unused elsewhere).

## Additional audit items (in-scope)

- **`build_rotation`** in `rotation.py:17`: does it also use `p["minutes"]` for anything hierarchy-related? If yes, may or may not need the same swap.
- **Foul-trouble sub replacement** (`rotation.py:104-109`): confirm the "best available" candidate ranking uses the right field.
- **`patch_rotation`** post-foul-out: same check.
- **`select_active_roster`** (availability model): out of scope — that's about who plays, not what order.

## Expected impact

- **GSW +59 reproduction**: Curry minutes should drop to a plausible 20-25 (from 43.6). Podziemski should drop too.
- **Panel star-MPG** (BOS=26/OKC=27/DEN=28/GSW=29):
  - Pattern B (GSW): scheduled Curry MPG should rise closer to real 30.9 as roster ordering fixes.
  - Pattern A (BOS/OKC/DEN): realized-vs-scheduled deficit should shrink some, because garbage mode will no longer promote the stars in those rare cases (though Pattern A's dominant driver is likely still foul-trouble subs / fouled-out, not garbage inversion).
- **Cross-team**: sim top-1 by MPG should equal real top-1 on more teams (currently 1/4 match).
- **Aggregate season stats** (per Xavier's 0.5/team-game bar): expected impact on team-level PPG / FGA / PF is small — this is a within-team minute-shuffle, not a scoring-mechanic change. But butterfly-effect RNG cascade will drift the box_score fixture (same class as the 7-fouls fix).

## Tests

- **Backend unit:** `tests/test_garbage_rotation_ordering.py` — assert that MODE_GARBAGE on a synthetic 10-player roster (5 high-`mpg`, 5 low-`mpg`) returns the LOW-mpg players. Add a case where an injury-limited high-mpg star has lower `minutes` than a durable role player — assert the star is NOT in MODE_GARBAGE's output.
- **Fixture regression:** re-capture `box_score_baseline.json` under the fixed behavior. Document the expected drift.
- **Full pytest suite:** 366+ green.

## UAT scenarios

1. **Reproduction:** re-run GSW seed 758993585 game `0022501060`, verify Curry minutes drop from 43.6 to a plausible ≤25.
2. **Panel re-run:** BOS=26, OKC=27, DEN=28, GSW=29 with fix. Compare per-star MPG to [[project-star-mpg-probe]] baseline. Expected: scheduled vs real gap on Curry shrinks; realized on all 4 improves toward real.
3. **Session A regression:** BOS 82-game seed 26 aggregate metrics stay within 0.5/team-game on PPG / FGA / FTA / PF.
4. **Sim top-1 audit:** on BOS/DEN/GSW, sim top-1 by MPG should now equal or converge toward the real top-1.

## Definition of done

- [ ] Design choice locked with Xavier (Option B recommended)
- [ ] Roster ordering swap applied at `game_simulator.py:194-195` (and cascade if audit finds more sites)
- [ ] Unit test asserting MODE_GARBAGE excludes high-`mpg` injury-limited stars
- [ ] `box_score_baseline.json` recaptured; drift documented per [[project-bug-7-fouls-jokic]] pattern (affected games, biggest cell changes, expected reason)
- [ ] Full pytest suite green
- [ ] UAT scenarios all pass — including the +59 game reproduction and the 4-team panel re-run against baseline
- [ ] Xavier sign-off
- [ ] Commit + push

## Not this session

- Any change to `build_rotation`'s minute-allocation math (that's role-scheduling, separate).
- Reconsidering what "garbage lineup" should mean at a higher level (e.g. G-League-tier players separately from bench 3-4).
- Q3-Q4 realization probe for Pattern A teams — deferred; may or may not still be needed after this fix.

## Related

- [[project-garbage-rotation-inversion]] — the bug diagnosis this session fixes
- [[project-star-mpg-probe]] — should update after fix to reflect residual Pattern A findings
- [[project-sim-realism-audit-a]] — the 4-team panel baseline for UAT
- [[project-bug-7-fouls-jokic]] — precedent for fixture-recapture + aggregate-impact discipline

---

# Build-Rotation + Garbage-Sort Bundled Fix (spec, take 2)

**Owner:** dedicated bug-fix session (next).
**Blocked-by:** design decision locked 2026-08-13 (this document).

## Design decision (locked, Xavier 2026-08-13)

**Per-game-MPG preservation, not total-minutes preservation.**

The purpose of the simulator is to reproduce realistic basketball behavior **when players are available**, not to preserve the historical season's total minutes when those totals were depressed by missed games.

### Conceptual separation (this is the durable framing)

- **Availability** — whether a player is available to play THIS game.
- **Role / workload when available** — how many minutes that player should receive when they DO play.
- **Role hierarchy** — who gets priority for those minutes.
- **Extended roster** — supplies realistic replacement players when higher-ranked players are unavailable.

The current sim conflates the first two — Curry's 40-game real season is compressed into a "23 MPG in every game" schedule, which is not his role, it's his availability discount smeared across appearances.

### What changes

- **`build_rotation` scheduled-minute allocation:** use per-game MPG (`p["mpg"]`) as the role signal, not availability-normalized `p["minutes"]`. Available Curry → ~30.9 MPG target when he plays.
- **`game_simulator.py:194-195` hierarchy sort:** use `p["mpg"]` — same field, propagates through MODE_GARBAGE / patch_rotation / foul-trouble replacement.

### What does NOT change

- Scheduled MPG remains subject to existing constraints — total team minutes, lineup feasibility, substitutions, foul trouble, game state / garbage time, OT handling. **Not a hard minute guarantee.**
- `use_availability=True` semantics are preserved. If availability modeling is enabled, missed games remain actual unavailable games; they don't dilute the player's MPG on the games they do play.

## Scope for the immediate fix

Narrow — five items:

1. `build_rotation` uses per-game MPG for scheduled allocation.
2. Hierarchy sort at `game_simulator.py:194-195` swaps `minutes` → `mpg`.
3. Audit all hierarchy consumers (foul-trouble replacement, patch_rotation, MODE_GARBAGE, is_starter).
4. 4-team panel re-run with correctness gates.
5. Fixture recapture + full pytest.

Gates from earlier RFC entry still apply:
- Panel-level PPG / Opp PPG / FGA / FTA / PF: <0.5/team-game movement.
- Panel Curry realized MPG rises toward real 30.9 (does not regress).
- Real top-1 == sim top-1 on ≥3/4 teams.
- Star deficit decreases across ALL panel teams (not shifted to a different player).

**Additional measurement for this session's report** — per Xavier's per-game-MPG design implication:
- Simulated season TOTAL minutes for each panel star vs real season total.
- Explicitly acknowledge that total-minutes divergence is EXPECTED for injury-limited players under this interpretation and is NOT auto-classified as a regression. The gate is per-game realism, not season-total preservation.

## Explicitly NOT this session (deferred)

Xavier flagged two related design implications during the design lock that will need their own session:

1. **Roster depth for season sims.** Once MPG = "workload when available", capping the sim pool at the top 10 is artificial — real NBA teams reach deeper when injuries, rest, foul trouble, and matchup decisions bite. Extended roster should supply a realistic replacement pool. Deeper players wouldn't get meaningful minutes by default; the hierarchy/rotation logic should naturally concentrate minutes toward primaries and reach deeper only when circumstances require it.

2. **Availability model for season sims.** `use_availability=False` is useful for isolating rotation behavior but insufficient for a realistic 82-game season. Real players don't play all 82 games. Availability is part of the basketball model, not noise to eliminate.

Neither belongs in the immediate hierarchy fix. Documenting here so we don't accidentally treat the current top-10/no-availability configuration as the final season-sim architecture.

## Follow-up session scope (documented for later)

**Session on roster + availability for season sim.** Tests:

- Top-10 pool vs extended roster
- `use_availability=True` vs `False` in a season sim
- Interaction between availability and per-game MPG (does availability correctly gate WHETHER a player plays without diluting HOW MUCH they play when available?)
- Whether deeper roster players receive realistic replacement minutes
- Whether season-level player appearances + minutes become more realistic
- Key invariant to check: **availability must not dilute MPG on games where the player IS available.**

Measurement bar for that session's report:
- Games played / availability rate by player
- MPG conditional on appearing
- Total season minutes
- Team minutes conservation
- Top-10 vs 11+ minute share
- Number of games requiring 11th/12th/etc. players

## Related memories

- [[project-garbage-rotation-inversion]] — diagnosis + failed isolated-fix attempt
- [[project-star-mpg-probe]] — the ordering context
- [[project-season-sim-roster-availability]] — new memo for the follow-up session

---

# C-arc + M-arc summary (2026-08 addendum)

Two arcs shipped after the rotation-realism work above closed. This section
is a summary; individual design locks live in memory (`project-session-c1-shipped`,
`project-session-c2-shipped`, `project-next-session-focus`) rather than in
new RFC sections since the design questions were smaller-scoped.

## C — full-league simulation

**C-1 (`522d0f8`) — backend + gates.**
Added `SimulationRun.scope` enum (`team` / `league`) with a CHECK constraint
ensuring `team_id` is set only for team scope. `run_league_simulation` walks
the season schedule per date, sims each game via the existing `simulate_game`,
persists SimulatedGame + SimulatedPlayerLine rows. Per-game deterministic seed
via `_game_seed(root_seed, game_id)` = SHA256-derived, so pause/resume
produces byte-identical persisted games (reproducibility gate: 10-game slice
byte-identical across two seed-fixed runs). Schedule integrity validator up
front — refuses 2024-25 / 2025-26 seasons until the modern schedule ingestion
bug (fixed later at `064e318`) is resolved. Full 2016-17 season sims in
~19 seconds — sim engine turned out to be ~350× faster than pre-session
estimate.

**C-2 (`6a79cc8`) — standings UI + team drill-in.**
`compute_standings` pure function over persisted games (chronological order
required, since W/L streak was later added — see below). Two GET endpoints:
`/simulations/{id}/standings` returns 30-row response with tie-breakers
(W desc, L asc, team_id asc) + GB formula, `/simulations/{id}/team/{abbr}/games`
returns the team's 82 games. Frontend: new League top-level tab (pragmatic
deviation from the design lock's "extend RunPicker" — retrofitting SeasonView
was disproportionate to the C-2 scope; folded later into the unified Simulate
tab).

**Ingestion fix (`064e318`) — modern schedule.**
2024-25 and 2025-26 were 1225 games instead of 1230. Root cause was NOT the
memo's original hypothesis (`len(rows) != 2` filter). The matchup-string
parser assumed exactly one row per game used `vs.` — neutral-site games
(NBA Cup semifinals, Paris games) show both team rows as `X @ Y` with no
`vs.` variant. Fix: parse the matchup string once, match rows by
`TEAM_ABBREVIATION` instead of relying on `vs.` vs `@` per-row.

## M — MyLeague franchise mode

**M-1a (`5ccbe96`) — engine foundation.**
`SeasonState` is the authoritative object; event-sourced availability
(`SET_UNAVAILABLE` / `SET_AVAILABLE`). `advance_to(state, target_date, db)`
folds one or more games in; state at time T = `fold(events with
applied_at_date <= T, base_state)`. Sibling table `myleague_state` (1:1 with
SimulationRun) + append-only `myleague_events` log. Invariants — first-class
tests, all pass:
- Reproducibility across pause boundaries (batch-vs-day-by-day byte-identical)
- Monotonic time (refuse target < cursor)
- Idempotent advance (calling twice = calling once)
- Off-day cursor semantics (advance-with-no-games commits cursor forward)
- Retroactive-event rejection (no mutation of already-simulated games)
- Same-day event determinism
- C-1 batch mode untouched (regression fence green)

**M-1b (`739d294`) — HTTP endpoints.**
Four endpoints wrapping the engine: `POST /myleague/`, `POST
/myleague/{id}/advance`, `POST /myleague/{id}/events`, `GET /myleague/{id}`.
Error mapping: MyLeagueError/MonotonicTimeError/RetroactiveEventError → 422,
missing/wrong-scope → 404. GET returns state + standings + last 10 games
in one round-trip.

**M-1c (`30912b6` + follow-ups) — minimal UI.**
New MyLeague top-level tab, picker → dashboard, Advance Day button. Post-UAT
polish: cursor init from actual first-game date (not season-window start),
season-complete detection + banner, E/W standings split, controlled-team
highlight, past-runs + Delete, delete-unblock for MyLeague scope, upcoming
games panel.

**M-2 (`df56ae9`) — NextGameCard.**
Rich pre-game preview for the controlled team's next game: opponent identity,
matchup context (Nth of M meetings, series wins so far), top-8 rotations for
both sides. Roster respects future `SET_UNAVAILABLE` events already (folds
availability at the game's date, not the cursor's date) — future
availability-toggle UI (M-4) flows through with zero backend changes.

**M-1 status-semantics bug family (`9a30ac9` delete-unblock, `c3875ed` create
+ drill-in guards).** MyLeague runs sit at `status='running'` indefinitely
(no background task — user drives via Advance clicks). Any endpoint that
gates on status must consider scope: delete, create-simulation ("another sim
running?"), and game-detail drill-in ("only completed runs browseable?") all
needed scope-aware bypasses. Documented as a design lesson in
`project-next-session-focus`.

## Locked product intent — MyLeague statistics contract

Xavier 2026-08-24: the simulated season IS the primary statistical reality
of a MyLeague run. Once enough sim games exist, per-player averages come
from the sim, not from real-life season stats. Real-life stats stay as
reference/context only, visually distinct. Design-lock session upcoming to
pin down sample-size threshold, below-threshold UI, and scope of application
(PlayerModal in MyLeague context, NextGameCard rotations, future roster
inspection). Anti-pattern to avoid: silently substituting real-life averages
when a player has a partial-season sim record.
