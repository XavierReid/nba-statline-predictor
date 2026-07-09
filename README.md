# NBA Franchise Simulator

A possession-based NBA simulation engine — think MyLeague in NBA 2K, but as a REST API. Real rosters and season data become player attributes; game outcomes emerge from ~200 simulated possessions per game (never from projected box scores), producing per-player stat lines, play-by-play, standings, and a calibration suite that validates the engine against real NBA distributions.

This is a personal portfolio project demonstrating clean backend engineering: schema design, data ingestion pipelines, simulation logic, and REST API design over a domain I find genuinely interesting.

---

## Architecture

```
┌─────────────────┐      ┌──────────────────┐      ┌─────────────────┐
│   Ingestion     │      │    Postgres      │      │   FastAPI       │
│   (nba_api)     │ ───▶ │   (source of     │ ◀─── │   REST API      │
│   teams/players │      │    truth)        │      │                 │
│   /schedule     │      └──────────────────┘      └────────┬────────┘
└─────────────────┘                                         │
                                                            ▼
                                                   ┌─────────────────┐
                                                   │   Simulator     │
                                                   │   game/season   │
                                                   └─────────────────┘
```

- **Ingestion** — pulls real teams, rosters, and schedules from `nba_api`. Idempotent, upsert-based.
- **Simulator** — possession-based game engine: attributes → per-possession probabilities → emergent box scores. Game-state modifiers (momentum, fatigue, clutch, garbage time), late-game incentive modeling, and state-aware rotations, all behind config toggles. Season simulator loops the real schedule through the game engine.
- **REST API** — kick off simulations, query standings, browse results.

**v2 (planned):** Kafka producer/consumer for real-time simulation event streaming.

---

## Data model

```
teams               players             games (real schedule)
─────               ───────             ─────────────────────
id                  id                  id (string, NBA format)
abbreviation        full_name           game_date
city                team_id ──────┐     home_team_id
nickname            position      │     away_team_id
conference                        │     home_score
division                          │     away_score
                                  └──▶  status

simulation_runs         simulated_games
───────────────         ───────────────
id                      id
season                  simulation_id
created_at              game_id (real schedule ref)
status                  home_score
                        away_score
                        (+ simulated player box scores)
```

---

## Running locally

**Prerequisites:** Docker (for Postgres) + Python 3.9+

```bash
# Start Postgres
docker compose up -d postgres

# Set up virtualenv
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run migrations
alembic upgrade head

# Run ingestion (seeds teams, players, schedule)
python -m scripts.run_ingestion --season 2024-25

# Start the API
uvicorn app.main:app --reload
```

---

## Project structure

```
app/
├── api/           route handlers
├── ingestion/     nba_api client + ingestion jobs
├── models/        SQLAlchemy ORM models
├── services/      game engine, rating engine, late-game logic, diagnostics
├── config.py
├── database.py
└── main.py
alembic/           migrations
scratch/           calibration tooling + exploration scripts
scripts/           CLI entrypoints
```

---

## Roadmap

- [x] Scaffold (FastAPI, Postgres, Alembic, Docker)
- [x] Ingestion: teams, players, schedule (2024-25)
- [x] Ingestion: season stats + player attribute/tendency seeding
- [x] Rating engine: percentile-based; interior finishing + individual defense derived from shot-location and defensive-matchup data (Attribute v2)
- [x] Game simulator: possession-based with clock, rotations, OT, game-state modifiers, late-game engine
- [x] Season simulator + persistence
- [x] REST API: run simulations, step-through, query results
- [x] Calibration suite: schedule replay vs real 2025-26 distributions (scoring exact, strength slope ~1.0)
- [ ] **v2**: Kafka event streaming layer
- [ ] **v2**: multi-season with player aging, free agency

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the end-to-end walkthrough.  
See [`RUNBOOK.md`](RUNBOOK.md) for commands, queries, and calibration tooling.  
See [`RFC.md`](RFC.md) for specs and design history, and [`SIMULATION_GAPS.md`](SIMULATION_GAPS.md) for the calibration evidence trail.

---

*Built by Xavier Reid — [github.com/xavierreid](https://github.com/xavierreid) · [https://www.linkedin.com/in/xavier-reid-246814115/](https://www.linkedin.com/in/xavier-reid-246814115/)*
