# Runbook

Reference for common commands, queries, and scripts. No Claude required.

---

## Environment setup

```bash
# Start Postgres (required for everything below)
docker compose up -d postgres

# Activate virtualenv
source .venv/bin/activate
```

---

## Ingestion

### Check what's ingested

```bash
# Via API (server must be running)
curl -s http://localhost:8000/ingestion/seasons | jq .
# Returns: season, stats_players, attrs_seeded, tends_seeded, ready (bool)
# ready=false means the season can't be simulated yet

# Via SQL (no server needed)
docker exec -i nba-statline-predictor-postgres-1 psql -U nba -d nba_predictor -c "
SELECT s.season,
       COUNT(DISTINCT s.player_id) AS stats,
       COUNT(DISTINCT a.player_id) AS attrs,
       COUNT(DISTINCT t.player_id) AS tends
FROM player_season_stats s
LEFT JOIN player_attributes a ON a.player_id = s.player_id AND a.season = s.season
LEFT JOIN player_tendencies t ON t.player_id = s.player_id AND t.season = s.season
GROUP BY s.season ORDER BY s.season;"
```

### Ingest a new season

```bash
# Via API — runs in background, check /ingestion/seasons for completion
curl -s -X POST http://localhost:8000/ingestion/seasons/2024-25/ingest | jq .

# Via CLI (more reliable for slow NBA API connections)
python - <<'EOF'
from app.database import SessionLocal
from app.ingestion.jobs import ingest_season_stats, seed_player_attributes
db = SessionLocal()
n = ingest_season_stats(db, "2024-25"); db.commit(); print(f"stats: {n}")
n = seed_player_attributes(db, "2024-25"); db.commit(); print(f"attrs: {n}")
db.close()
EOF
```

### Seed (or re-seed) attributes only

Needed when: stats are ingested but attrs/tends show 0, or after rating engine changes.

```bash
# Via API
curl -s -X POST http://localhost:8000/ingestion/seasons/2024-25/seed \
  -H "Content-Type: application/json" \
  -d '{"season": "2024-25", "force": false}' | jq .

# force=true wipes existing attrs/tends before re-seeding (use after engine changes)
curl -s -X POST http://localhost:8000/ingestion/seasons/2024-25/seed \
  -H "Content-Type: application/json" \
  -d '{"season": "2024-25", "force": true}' | jq .

# Via CLI
python - <<'EOF'
from app.database import SessionLocal
from app.ingestion.jobs import seed_player_attributes
db = SessionLocal()
n = seed_player_attributes(db, "2024-25"); db.commit(); print(f"seeded: {n}")
db.close()
EOF
```

---

## Migrations

```bash
# Apply all pending migrations
python -m alembic upgrade head

# Check current migration state
python -m alembic current

# Show migration history
python -m alembic history --verbose
```

---

## Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_rating_engine.py -v
```

---

## Explore ingested data

```bash
# Interactive ratings explorer — top 30 overall, best by skill,
# top 10 per position, distribution, and specific player lookups
python scratch/explore_ratings.py           # defaults to 2024-25
python scratch/explore_ratings.py 2025-26   # pass any ingested season
```

To look up a different player, edit the `lookups` list near the bottom of
`scratch/explore_ratings.py`.

---

## Game simulator (scratch CLI)

```bash
# Default: DEN vs GSW, seed 42, season 2024-25
python scratch/03_game_simulator.py

# Custom matchup: HOME AWAY SEED SEASON
python scratch/03_game_simulator.py MIL IND 42 2024-25
python scratch/03_game_simulator.py BOS LAL 99 2024-25

# Same seed = identical result every time (reproducibility check)
python scratch/03_game_simulator.py DEN GSW 1 2024-25
python scratch/03_game_simulator.py DEN GSW 1 2024-25
```

Output includes: score by quarter, full box score with PTS/REB/AST/STL/BLK/TOV/PF/FG/3PT/FT.
Players who foul out are annotated with `(FO)`.

---

## Useful SQL queries

Connect to Postgres:
```bash
docker exec -it nba-statline-predictor-postgres-1 psql -U nba -d nba_predictor
```

**Top 20 players by overall rating:**
```sql
SELECT p.full_name, p.position, a.overall_rating,
       a.three_point, a.passing, a.steal, a.block
FROM player_attributes a
JOIN players p ON p.id = a.player_id
WHERE a.season = '2024-25'
ORDER BY a.overall_rating DESC
LIMIT 20;
```

**Best 3PT shooters (rated players only):**
```sql
SELECT p.full_name, a.three_point, s.fg3_pct, s.fg3a
FROM player_attributes a
JOIN players p ON p.id = a.player_id
JOIN player_season_stats s ON s.player_id = p.id AND s.season = a.season
WHERE a.season = '2024-25' AND a.three_point > 85
ORDER BY a.three_point DESC;
```

**Player tendencies (usage + shot distribution):**
```sql
SELECT p.full_name, p.position,
       t.usage_rate, t.shot_tendency, t.three_point_rate,
       t.assist_rate, t.rebound_rate, t.turnover_rate
FROM player_tendencies t
JOIN players p ON p.id = t.player_id
WHERE t.season = '2024-25'
ORDER BY t.usage_rate DESC
LIMIT 20;
```

**Full attribute breakdown for one player:**
```sql
SELECT a.*
FROM player_attributes a
JOIN players p ON p.id = a.player_id
WHERE p.full_name ILIKE '%joki%' AND a.season = '2024-25';
```

**Rating distribution by position:**
```sql
SELECT
  SPLIT_PART(p.position, '-', 1) AS pos,
  COUNT(*) AS players,
  ROUND(AVG(a.overall_rating)) AS avg_ovr,
  MAX(a.overall_rating) AS max_ovr,
  MIN(a.overall_rating) AS min_ovr
FROM player_attributes a
JOIN players p ON p.id = a.player_id
WHERE a.season = '2024-25'
GROUP BY SPLIT_PART(p.position, '-', 1)
ORDER BY avg_ovr DESC;
```

**Players on a specific team:**
```sql
SELECT p.full_name, p.position, a.overall_rating
FROM players p
JOIN teams t ON t.id = p.team_id
JOIN player_attributes a ON a.player_id = p.id AND a.season = '2024-25'
WHERE t.abbreviation = 'DEN'
ORDER BY a.overall_rating DESC;
```

**Year-over-year overall rating change (requires both seasons ingested):**
```sql
SELECT p.full_name, p.position,
       a1.overall_rating AS ovr_2425,
       a2.overall_rating AS ovr_2526,
       a2.overall_rating - a1.overall_rating AS delta
FROM player_attributes a1
JOIN player_attributes a2 ON a2.player_id = a1.player_id AND a2.season = '2025-26'
JOIN players p ON p.id = a1.player_id
WHERE a1.season = '2024-25'
ORDER BY delta DESC
LIMIT 20;
```

**Season schedule (how many games per team):**
```sql
SELECT t.abbreviation,
       COUNT(*) FILTER (WHERE g.home_team_id = t.id) AS home_games,
       COUNT(*) FILTER (WHERE g.away_team_id = t.id) AS away_games
FROM teams t
JOIN games g ON g.home_team_id = t.id OR g.away_team_id = t.id
WHERE g.game_date BETWEEN '2024-10-01' AND '2025-04-30'
GROUP BY t.abbreviation
ORDER BY t.abbreviation;
```

---

## API

```bash
# Start the API server
uvicorn app.main:app --reload

# Interactive docs (Swagger UI)
open http://localhost:8000/docs

# Health check
curl http://localhost:8000/health
```

### Simulate a standalone game

```bash
# DEN vs GSW, 2024-25 stats, seed 42 (reproducible)
curl -s -X POST http://localhost:8000/simulations/game \
  -H "Content-Type: application/json" \
  -d '{"home_team": "DEN", "away_team": "GSW", "season": "2024-25", "seed": 42}' | jq .

# Random seed (different result each call)
curl -s -X POST http://localhost:8000/simulations/game \
  -H "Content-Type: application/json" \
  -d '{"home_team": "MIL", "away_team": "IND", "season": "2024-25"}' | jq .

# Just the score line
curl -s -X POST http://localhost:8000/simulations/game \
  -H "Content-Type: application/json" \
  -d '{"home_team": "BOS", "away_team": "LAL", "season": "2024-25", "seed": 1}' \
  | jq '{season, seed, home_team, away_team, home_score, away_score, quarter_scores}'

# Top scorer from each team
curl -s -X POST http://localhost:8000/simulations/game \
  -H "Content-Type: application/json" \
  -d '{"home_team": "DEN", "away_team": "MIL", "season": "2024-25", "seed": 7}' \
  | jq '{home: .home_box[0], away: .away_box[0]}'
```

**Error cases to know:**
- Unknown team abbreviation → `404 Team 'XYZ' not found`
- Season not ingested → `422 No roster data for DEN in season 2030-31. Run ingestion first.`

---

## Rating engine notes

The rating engine lives in `app/services/rating_engine.py`. Key things to know:

- Individual attributes (3PT, passing, etc.) use a **percentile → rating curve** (`_CURVE_ANCHORS`). The 50th percentile maps to 72, 90th to 92.
- Overall rating is computed in two steps: position-weighted group average → non-linear overall curve (`_OVERALL_CURVE`). See `RFC.md` for full design rationale.
- Athleticism attributes (speed, stamina, etc.) are position-estimated defaults — they appear on the model but do **not** contribute to `overall_rating`. Real data sources for v2: `LeagueDashPtStats`, `DraftCombineStats`.
- Manual corrections go in the `player_attribute_overrides` table — use this for known exceptions like Jokić's ball handle before v2 tracking data is available.

To re-seed ratings after changing the engine (e.g., tuning weights):
```bash
python - <<'EOF'
from app.database import SessionLocal
from app.models.player_attributes import PlayerAttributes
from app.models.player_tendencies import PlayerTendencies
from app.ingestion.jobs import seed_player_attributes
db = SessionLocal()
db.query(PlayerAttributes).filter(PlayerAttributes.season == "2024-25").delete()
db.query(PlayerTendencies).filter(PlayerTendencies.season == "2024-25").delete()
db.commit()
n = seed_player_attributes(db, "2024-25")
db.commit()
print(f"Re-seeded {n} players")
db.close()
EOF
```
