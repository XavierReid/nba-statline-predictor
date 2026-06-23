# RFC: NBA Franchise Simulator

**Status:** In Progress  
**Last Updated:** 2026-06-23

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

**Out of scope (v1):** trades, draft logic, salary cap, injuries, coaching, chemistry

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

| Table | Purpose |
|---|---|
| `lineups` | Starting 5 + bench + expected minutes per player |
| `simulation_runs` | id, season, seed, parameters (JSON), status, created_at |
| `simulated_games` | simulation_id, game_id (real schedule ref), scores |
| `simulated_player_lines` | Per-player box score per simulated game |

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

### Game Simulator — Possession-Based (not stat-projection)

The simulator operates at the possession level, not the player-average level. This is the critical design distinction from a stat prediction engine.

```
Team A possession:
  ↓ select player (weighted by usage_rate)
  ↓ select action (shoot / pass / drive — weighted by tendencies)
  ↓ select shot type (3PT / mid / paint — weighted by three_point_rate)
  ↓ apply defender impact (perimeter_defense or interior_defense)
  ↓ resolve outcome (make/miss — weighted by relevant attribute)
  ↓ rebound if miss (weighted by offensive_rebound / defensive_rebound)
  ↓ accumulate to box score
```

This means:
- Usage matters — Luka takes more shots than PJ Washington
- Defense matters — a weak perimeter defender gives up more open threes
- Teammates matter — high-passing players generate better shot quality for others

**Do not start by sampling per-player distributions directly.** That produces a stat projection engine, not a basketball simulator.

### Season Simulator

- Loop real game schedule from `games` table
- Run GameSimulator for each game using that game's lineups
- Persist to `simulation_runs` → `simulated_games` → `simulated_player_lines`
- Compute standings after all games complete

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
- [x] Migration 0002: simulation foundation tables
- [x] RatingEngine: percentile-based ratings, SkillMetricConfig, position-adjusted defaults
- [x] Unit tests for RatingEngine (8 passing)
- [x] Ingested 2024-25 season stats (431 players; 138 skipped — not on ingested rosters)
- [x] Seeded PlayerAttributes + PlayerTendencies for 2024-25
- [x] Rating validation: Jokić 94, Wemby/Luka/Tatum 86-87, bench 65-74 ✓
- [x] Overall rating redesign: position-specific group weights + non-linear overall curve

### Next
- [ ] Lineup model (migration 0003)
- [ ] SimulationRun + SimulatedGame + SimulatedPlayerLine models (migration 0003)
- [ ] TeamRatingCalculator service
- [ ] GameSimulator — possession-based (app/services/game_simulator.py)
- [ ] SeasonSimulator (app/services/season_simulator.py)
- [ ] REST endpoints: POST /simulations, GET /simulations/{id}/standings

### v2
- [ ] Kafka producer/consumer
- [ ] Multi-season with player aging
- [ ] Free agency
- [ ] CLI interface

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
