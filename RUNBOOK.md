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
# Default: DEN vs GSW, seed 42, season 2024-25, preset drama-m3
python scratch/03_game_simulator.py

# Custom matchup: HOME AWAY SEED SEASON [PRESET]
python scratch/03_game_simulator.py MIL IND 42 2025-26
python scratch/03_game_simulator.py BOS LAL 99 2025-26 baseline

# Same seed + preset = identical result every time (reproducibility check)
python scratch/03_game_simulator.py DEN GSW 1 2025-26
python scratch/03_game_simulator.py DEN GSW 1 2025-26

# Full play-by-play (possession log with clock, running score, descriptions)
python scratch/03_game_simulator.py BOS LAL 42 2025-26 --pbp
```

Output includes: score by quarter, full box score with PTS/REB/AST/STL/BLK/TOV/PF/FG/3PT/FT.
Players who foul out are annotated with `(FO)`. Home and away must be different
teams (mirror matchups are valid only inside diagnostics scripts — the box score
would merge both sides).

### Viewing play-by-play via the API

```bash
# One-shot game with full play-by-play: set include_pbp and read `events`
curl -s -X POST http://localhost:8000/simulations/game \
  -H "Content-Type: application/json" \
  -d '{"home_team":"BOS","away_team":"LAL","season":"2025-26","seed":42,
       "config":{"preset":"drama-m3"},"include_pbp":true}' \
  | python3 -c "
import sys, json
r = json.load(sys.stdin)
for e in r['events']:
    q, c = e['quarter'], e['game_clock_seconds']
    print(f\"Q{q} {c//60}:{c%60:02d} {r['home_team'] if e['is_home'] else r['away_team']:<4}\"
          f\" {e['running_home_score']}-{e['running_away_score']}  {e['description']}\")"

# Step-through sessions: events for chunks revealed so far
curl -s http://localhost:8000/simulations/game/stepthrough/{token}/events
```

Each event carries: possession number, quarter, clock, running score, scorer,
shot sub-type (corner_three / above_break_three / mid_range / floater / layup /
dunk), assist/rebound/steal/block/foul attribution, FTs, and a human-readable
`description`.

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

### Step-through a game

```bash
# Start a session — returns token + step 1 (default: 4 steps = quarters)
curl -s -X POST http://localhost:8000/simulations/game/stepthrough \
  -H "Content-Type: application/json" \
  -d '{"home_team": "DEN", "away_team": "GSW", "season": "2024-25", "seed": 42}' \
  | jq '{token, step, total_steps, complete, home_score, away_score}'

# Advance to next step (replace <token> with value from above)
curl -s http://localhost:8000/simulations/game/stepthrough/<token>/next \
  | jq '{step, complete, home_score, away_score}'

# Minute-by-minute (48 steps)
curl -s -X POST http://localhost:8000/simulations/game/stepthrough \
  -H "Content-Type: application/json" \
  -d '{"home_team": "MIL", "away_team": "IND", "season": "2024-25", "seed": 7, "steps": 48}'
```

**Steps reference — pick based on how many round-trips you want:**

Chunks are time-based: `chunk_duration = 48 / steps` minutes of game time per step.
OT automatically generates proportional extra chunks (e.g. steps=4 → each OT period adds 1 extra step, steps=48 → each OT adds 5 extra steps).

| steps | chunk duration | reg round-trips | best for                       |
|-------|----------------|-----------------|--------------------------------|
| 2     | 24 min         | 2               | halftime split                 |
| 4     | 12 min         | 4               | quarters (default)             |
| 8     | 6 min          | 8               | scoring runs                   |
| 12    | 4 min          | 12              | TV timeout segments            |
| 24    | 2 min          | 24              | ~2 minute segments             |
| 48    | 1 min          | 48              | minute-by-minute               |
| 96    | 30 sec         | 96              | half-minute intervals          |

The `total_steps` in the response is the actual count after OT resolution — it will exceed `steps` if the game goes to overtime. Sessions expire after 1 hour or when the final step is consumed (`complete: true`).

---

## Simulation configs

### Presets

| Preset | Description | Use when |
|---|---|---|
| `baseline` (default) | All modifiers off. Fixed 200 possessions, simple alternating possession. | Isolating player/rating behavior, fast calibration baseline |
| `drama-m1` | Pace, clock, second-chance, fast break, team defense, strategic foul. | Realistic game flow testing, M1 UAT |
| `drama-m2` | All M1 modifiers + momentum + fatigue + foul trouble + clutch. | Full drama pipeline testing |

### Modifier reference

| Modifier | Toggle | What it does | Calibration impact |
|---|---|---|---|
| `use_pace` | M1 | Sets expected possessions from team pace data (vs fixed 200) | Tightens possession count to real team tempo |
| `use_clock` | M1 | Runs a real clock per quarter (while clock > 0) vs fixed possession count | Enables all clock-dependent modifiers |
| `use_second_chance` | M1 | Offensive rebound keeps possession (chain up to 5); compensates mean possession time to avoid inflation | Adds ~0.5 pts/team from real oreb chains |
| `use_fast_break` | M1 | Steal → next possession is a fast break (85% close shots, +8% make prob, no block check) | More close-shot frequency after steals |
| `use_team_defense` | M1 | Team def_rating suppresses opponent FG% (dampened 50% of raw spread) | Elite defenses allow ~3% fewer makes; weak defenses concede ~4% more |
| `use_strategic_foul` | M1 | Trailing team fouls worst FT shooter when down 3–8, ≤120s left in Q4/OT (p=0.70) | Adds late-game FT possessions, closes margins slightly |
| `use_momentum` | M2b | Per-team momentum from scoring runs/stops/steals decays 20%/possession; adjusts shot prob ±2.5% and TOV prob ±1.5% | Reduces blowout rate; adds realistic variance in game flow |
| `use_fatigue` | M2c | Heavy-minutes lineup efficiency decay (penalty scales from 28→40 min, max −4% shot prob) | Suppresses late-game scoring inflation and blowout compounding |
| `use_foul_trouble` | M2c | Defense softens when 1–2+ players have ≥4 fouls; escalates in Q4 (max +5% shot prob for offense) | Helps offense exploit foul-trouble situations realistically |
| `use_clutch` | M2c | Clutch rating advantage (derived from `LeagueDashPlayerClutch`) boosts shot prob ±3% and reduces TOV ±1.5% in last 2 min ≤5 pts | Improves OT rate by rewarding clutch lineups; makes close games more realistic |

### Using configs via API

```bash
# Named preset
curl -s -X POST http://localhost:8000/simulations/game \
  -H "Content-Type: application/json" \
  -d '{"home_team":"BOS","away_team":"LAL","season":"2025-26","seed":42,
       "config":{"preset":"drama-m1"}}' | jq '{home_score, away_score}'

# Preset + override (disable one modifier)
curl -s -X POST http://localhost:8000/simulations/game \
  -H "Content-Type: application/json" \
  -d '{"home_team":"BOS","away_team":"LAL","season":"2025-26","seed":42,
       "config":{"preset":"drama-m1","overrides":{"use_second_chance":false}}}' \
  | jq '{home_score, away_score}'

# Season sim with drama-m1
curl -s -X POST http://localhost:8000/simulations/ \
  -H "Content-Type: application/json" \
  -d '{"team":"NYK","season":"2025-26","seed":99,"config":{"preset":"drama-m1"}}' | jq .
```

### Calibration targets (2025-26, real data)

Run `python scratch/calibrate_simulator.py --games 500 --season 2025-26 [--drama-m1]` to compare.

| Metric | Real 2025-26 | Baseline | Drama M1 |
|---|---|---|---|
| Avg team score | 115.6 pts | ~112 | ~118.8 |
| Avg margin | 13.3 pts | ~14.1 | ~14.4 |
| Home win rate | 55.4% | ~51% | ~55.7% ✅ |
| Blowout rate (20+) | 22.9% | ~27% | ~27.2% |
| OT rate | ~6% | ~2% | ~2.4% |

---

## Pre-feature sim purge

Run before each feature UAT to ensure test simulations reflect current code only.

```bash
python scripts/purge_sims.py            # preview what will be deleted (dry run)
python scripts/purge_sims.py --confirm  # delete all simulation runs and results
```

Individual sim delete via API (if you want to keep some):
```bash
curl -s -X DELETE http://localhost:8000/simulations/12 | jq .
```

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

---

## Calibration & diagnostics tooling

Three complementary tools, all run inside the api container
(`docker compose run --rm api python scratch/<tool>.py`). All use
deterministic seeds — identical config in, identical results out.

### 1. calibrate_simulator.py — headline metrics

```bash
python scratch/calibrate_simulator.py --drama-m3 --games 1000
```

Fixed 10-matchup set, reports avg score / margin / home win / OT / blowout
vs real season targets pulled from the games table. Use for quick
before/after checks when tuning. Caveats: the matchup set over-represents
mismatches, so distribution metrics (blowout, margin) are less trustworthy
here than in the schedule replay. Use >=1000 games when measuring blowout
rate (sample-sensitive).

### 2. diagnose_calibration.py — mechanism-level diagnostics

```bash
python scratch/diagnose_calibration.py --games 300
```

Reports the WHY behind the headline metrics:

- **[1.4/acct] possession accounting** — counts + avg duration per possession
  category vs the pace budget. Any new mechanic that affects possessions
  must keep "excess vs budget" explainable (CLAUDE.md guardrail 5).
- **[sf] strategic foul sequences** — frequency and length; validate against
  real NBA behavior, don't compensate away.
- **[1.3] quarterly margin walk + lead changes** — dispersion shape.
- **[1.2] the OT funnel** — close-late rate × tie conversion = OT rate.

### 3. replay_schedule.py — real-schedule comparison (gold standard)

```bash
python scratch/replay_schedule.py --sims-per-game 4
```

Simulates every real final game of the season (same matchups, same home
teams) and compares distributions directly — no matchup-composition bias.
Also reports per-team strength calibration:

- **strength slope** = regression of sim on real (win% and net margin);
  1.0 = calibrated, <1 = engine compresses team quality.
- Read the **top-10 tier slope** as the trustworthy signal; the bottom tier
  is confounded by tanking/rest, which the sim deliberately doesn't model.

### Measured constants workflow

SimConfig constants marked "measured" (`fastbreak_poss_frac`,
`catch_up_clock_frac`) carry provenance comments: value, date, sample,
preset. Re-measure via diagnose_calibration.py in measurement mode (set
the constant to 0.0) whenever the mechanics feeding them change (steal
rates, catch-up window, possession times).
