# NBA Franchise Simulator

A backend simulation engine that simulates NBA seasons at box-score level — think MyLeague in NBA 2K, but as a REST API. Given real team rosters and schedules, it simulates every game, produces per-player stat lines, computes standings, and generates a playoff bracket.

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
- **Simulator** — given two rosters, simulates a box-score-level game result. Season simulator loops the real schedule through the game simulator.
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
├── schemas/       Pydantic response models
├── services/      game simulator, season simulator
├── config.py
├── database.py
└── main.py
alembic/           migrations
scratch/           Phase 1 throwaway scripts
scripts/           CLI entrypoints
```

---

## Roadmap

- [x] Scaffold (FastAPI, Postgres, Alembic, Docker)
- [x] Ingestion: teams
- [ ] Ingestion: players + schedule
- [ ] Game simulator (box-score level)
- [ ] Season simulator + persistence
- [ ] REST API: run simulations, query standings
- [ ] **v2**: Kafka event streaming layer
- [ ] **v3**: multi-season with player aging, free agency

---

*Built by Xavier Reid — [github.com/xavierreid](https://github.com/xavierreid) · [linkedin.com/in/xavierreid](https://linkedin.com/in/xavierreid)*
