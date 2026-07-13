#!/usr/bin/env bash
# NBA Franchise Simulator — 5-minute showcase.
# Requires: API up at :8000 (docker compose up), jq, docker compose.
# Run:  bash scratch/demo_showcase.sh
set -euo pipefail

BASE=http://localhost:8000
SEASON="1996-97"; HOME_TEAM=CHI; AWAY_TEAM=UTA; SEED=1997   # 1997 NBA Finals, reproducible
# NOTE: do not name these HOME/host — HOME is the shell's home dir and clobbering
# it breaks the docker CLI (it reads $HOME/.docker).

hr(){ printf '\n\033[1m%s\033[0m\n' "── $* ──────────────────────────────────────"; }

hr "ACT 1  $SEASON  $HOME_TEAM vs $AWAY_TEAM  (one possession-based game, seed $SEED)"
curl -s -X POST "$BASE/simulations/game" -H 'Content-Type: application/json' \
  -d "{\"home_team\":\"$HOME_TEAM\",\"away_team\":\"$AWAY_TEAM\",\"season\":\"$SEASON\",\"seed\":$SEED}" \
| jq '{final: "\(.home_team) \(.home_score) - \(.away_score) \(.away_team)",
       quarters: {home: .quarter_scores.home, away: .quarter_scores.away}}'

hr "ACT 2  Same game, stepped through quarter by quarter (it is SIMULATED, not projected)"
TOKEN=$(curl -s -X POST "$BASE/simulations/game/stepthrough" -H 'Content-Type: application/json' \
  -d "{\"home_team\":\"$HOME_TEAM\",\"away_team\":\"$AWAY_TEAM\",\"season\":\"$SEASON\",\"seed\":$SEED,\"steps\":4}" \
  | tee /tmp/_st.json | jq -r .token)
jq -r '"  end Q\(.step):  \(.home_team) \(.home_score) - \(.away_score) \(.away_team)"' /tmp/_st.json
echo "  opening possessions:"
# fetch events while the session is alive (it expires once the final step is consumed)
curl -s "$BASE/simulations/game/stepthrough/$TOKEN/events" \
  | jq -r '[.[] | select(.description)][:4][] | "    \(.description)"' 2>/dev/null || true
for _ in 2 3 4; do
  curl -s "$BASE/simulations/game/stepthrough/$TOKEN/next" \
  | jq -r '"  end Q\(.step):  \(.home_team) \(.home_score) - \(.away_score) \(.away_team)"'
done

hr "ACT 3  One engine, three generations — each era emerges with its own scoring world"
docker compose exec -T api python scratch/cross_era_compare.py --seasons 1996-97 2005-06 2025-26 --sims 1 \
  | sed -n '/metric/,/vs real/p'

hr "ACT 4  Accounting: where does every point come from?  ($SEASON sim vs real)"
docker compose exec -T api python -m app.analysis.decomposition --season "$SEASON" --sims 1 \
  | sed -n '/Possession decomposition/,$p'

hr "Done"
