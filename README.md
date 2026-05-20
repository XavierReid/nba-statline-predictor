# NBA Statline Predictor

A backend service that predicts player statlines (points, rebounds, assists, etc.) for upcoming NBA games using **rule-based heuristics** over historical performance data — no machine learning, fully explainable.

This project is a personal portfolio piece. The goal is to demonstrate clean backend engineering — schema design, REST API design, data ingestion pipelines, and explainable prediction logic — over a dataset I find personally interesting.

---

## Why heuristics, not ML?

Predicting NBA statlines accurately is a deep problem that well-funded sportsbooks and analytics shops work on full-time. A weekend ML model would not produce predictions worth defending in interviews. A well-engineered heuristic model is honest, explainable, and demonstrates clear engineering judgment.

Every prediction returns not just a number, but the *factors* that produced it. An interviewer (or me, six months from now) can read the response and immediately understand why the prediction came out the way it did.

---

## The prediction formula

For any player + upcoming game pair, we predict each stat (points, rebounds, assists, etc.) as a weighted blend of three historical baselines, then apply contextual adjustments:

```
predicted_value =
    ( w_recent      * avg_over_last_N_games
    + w_season      * avg_over_current_season
    + w_vs_opponent * avg_in_career_vs_this_opponent )
    * home_away_adjustment
    * rest_days_adjustment
    * opponent_defense_adjustment
```

Default weights and adjustments (all configurable, see `app/config.py`):

| Component | Default | Notes |
|---|---|---|
| `w_recent` (last 10 games) | 0.50 | Captures current form |
| `w_season` | 0.30 | Anchors to season-level baseline |
| `w_vs_opponent` | 0.20 | Some players consistently overperform vs specific teams |
| `home_away_adjustment` | 1.05 home, 0.95 away | League-average home court effect |
| `rest_adjustment` | 1.00 (1 day), 1.02 (2+), 0.97 (back-to-back) | Fatigue effect |
| `opponent_defense_adjustment` | scaled inversely to opponent's defensive rating | Better D → lower predicted output |

If the player has fewer than `N` recent games (e.g., early-season, returning from injury), weights re-distribute to the remaining components.

---

## Architecture

```
┌─────────────────┐      ┌──────────────────┐      ┌─────────────────┐
│   Ingestion     │      │    Postgres      │      │   FastAPI       │
│   (nightly job) │ ───▶ │   (source of     │ ◀─── │   Service       │
│   nba_api       │      │    truth)        │      │   (REST API)    │
└─────────────────┘      └──────────────────┘      └────────┬────────┘
                                                            │
                                                            ▼
                                                   ┌─────────────────┐
                                                   │   Predictor     │
                                                   │   (heuristics)  │
                                                   └─────────────────┘
```

**Three logical components:**

1. **Ingestion service** — `app/ingestion/`: nightly job that pulls game results, box scores, schedules, and team defensive ratings from `nba_api` (a Python wrapper around `stats.nba.com`). Idempotent — safe to re-run.
2. **REST API** — `app/api/`: FastAPI service exposing endpoints for predictions, player history, and a backtest endpoint that re-runs predictions against historical games.
3. **Predictor** — `app/services/predictor.py`: pure functions that compute the formula above. Easy to unit-test, easy to tweak.

**v2 (planned):** add a Kafka layer for live in-game stat updates, so the API can serve mid-game projections in addition to pre-game predictions.

---

## Data model

```
teams                 players                games
─────                 ───────                ─────
id (NBA team_id)      id (NBA player_id)     id (NBA game_id)
abbreviation          full_name              game_date
name                  team_id  ─────────┐    home_team_id  ─────┐
conference            position          │    away_team_id  ─────┤
division              ───────           │    home_score          │
                                        │    away_score          │
                                        │    status              │
                                        │                        │
                                        │                        │
player_game_stats                       │    team_defensive_ratings
─────────────────                       │    ──────────────────────
id                                      │    team_id ─────────────┘
game_id  ────────────────────────┐      │    date
player_id  ──────────────────────┤      │    defensive_rating
team_id  ────────────────────────┘──────┘    pace
minutes, points, rebounds, assists,
steals, blocks, turnovers,
fg_made/attempted, three_made/attempted,
ft_made/attempted, is_home
```

Migrations are managed with **Alembic**. Initial schema lives in `alembic/versions/0001_initial_schema.py`.

---

## REST API endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/health` | Liveness check |
| `GET`  | `/players/{player_id}` | Player metadata |
| `GET`  | `/players/{player_id}/history` | Recent game stats with optional `?vs_team=` filter |
| `GET`  | `/games/{game_id}` | Game metadata |
| `GET`  | `/games/{game_id}/predictions` | Predicted statlines for all players in this game |
| `GET`  | `/predictions/{player_id}/{game_id}` | Single prediction with full factor breakdown |
| `GET`  | `/backtest?date=YYYY-MM-DD` | Re-run predictions for a past date and compare to actuals |

Auto-generated OpenAPI docs are available at `/docs` (Swagger) and `/redoc` (ReDoc) when the service is running.

---

## Running locally

**Prerequisites:** Docker + Docker Compose. Nothing else.

```bash
# Boot Postgres + the API service
docker compose up --build

# In another terminal — run migrations
docker compose exec api alembic upgrade head

# Run the nightly ingestion (one-off)
docker compose exec api python -m scripts.run_ingestion --season 2024-25

# Hit the API
curl http://localhost:8000/health
open http://localhost:8000/docs
```

Without Docker:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env  # edit DATABASE_URL to point at a local Postgres
alembic upgrade head
uvicorn app.main:app --reload
```

---

## Tests

```bash
pytest                    # all tests
pytest tests/test_predictor.py -v   # just the predictor logic
```

The predictor has unit tests that don't require a database — pure-function tests against synthetic stat histories.

---

## Project structure

```
.
├── app/
│   ├── main.py              # FastAPI app entrypoint
│   ├── config.py            # Pydantic settings (env-driven)
│   ├── database.py          # SQLAlchemy engine + session factory
│   ├── models/              # SQLAlchemy ORM models
│   ├── schemas/             # Pydantic request/response models
│   ├── api/                 # Route handlers
│   ├── services/            # Business logic (the predictor lives here)
│   └── ingestion/           # nba_api client + nightly job
├── alembic/                 # Migrations
├── tests/                   # pytest suite
├── scripts/                 # CLI entrypoints (e.g. run_ingestion)
├── .github/workflows/       # CI (lint + test on push)
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

---

## Roadmap

- [x] Repo scaffold (FastAPI, Postgres, Alembic, Docker, CI)
- [ ] Ingestion: pull team & player metadata from `nba_api`
- [ ] Ingestion: pull game results + box scores for one full season
- [ ] Predictor: implement core formula + unit tests against synthetic data
- [ ] API: implement `/predictions` and `/games/{id}/predictions` against real data
- [ ] Backtest endpoint
- [ ] **v2**: Kafka producer for live game updates; consumer that updates Postgres in near-real-time
- [ ] **v3**: Tiny React frontend showing tonight's slate with predictions

---

## Honest disclaimers

- This is a heuristic model. It will not consistently beat sportsbook lines. The point is engineering rigor, not market alpha.
- Public NBA stats sources occasionally rate-limit or change their APIs. The ingestion layer is built to be re-runnable so you can refresh as needed.
- `nba_api` scrapes the NBA's public stats site. Use respectfully.

---

*Built by Xavier Reid — github.com/xavierreid · linkedin.com/in/xavierreid*
