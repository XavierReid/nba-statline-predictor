"""
Scratch CLI for game simulation — thin wrapper around app.services.game_simulator.

Usage:
    python scratch/03_game_simulator.py [HOME] [AWAY] [SEED] [SEASON]
    python scratch/03_game_simulator.py DEN GSW 42 2024-25
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models.team import Team
from app.services.game_simulator import load_roster, simulate_game
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Helpers (CLI-only — display logic that doesn't belong in the service)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Pretty print (CLI only)
# ---------------------------------------------------------------------------
def print_box_score(players_by_id: dict, box: dict, team_name: str, team_ids: set):
    print(f"\n  {team_name}")
    print(f"  {'Name':<27} {'MIN':>5} {'PTS':>4} {'REB':>4} {'AST':>4} {'STL':>4} {'BLK':>4} {'TOV':>4} {'PF':>3} {'FG':>8} {'3PT':>8} {'FT':>8}")
    rows = sorted(
        [(pid, s) for pid, s in box.items() if pid in team_ids],
        key=lambda x: x[1]["pts"], reverse=True
    )
    for pid, s in rows:
        if s["min"] < 0.5:
            continue
        name = players_by_id.get(pid, {}).get("name", str(pid))
        if s["fouled_out"]:
            name = name + " (FO)"
        fg = f"{s['fgm']}/{s['fga']}"
        three = f"{s['fg3m']}/{s['fg3a']}"
        ft = f"{s['ftm']}/{s['fta']}"
        print(f"  {name:<27} {s['min']:>5.1f} {s['pts']:>4} {s['reb']:>4} {s['ast']:>4} {s['stl']:>4} {s['blk']:>4} {s['tov']:>4} {s['pf']:>3} {fg:>8} {three:>8} {ft:>8}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    HOME_ABBR = sys.argv[1] if len(sys.argv) > 1 else "DEN"
    AWAY_ABBR = sys.argv[2] if len(sys.argv) > 2 else "GSW"
    SEED      = int(sys.argv[3]) if len(sys.argv) > 3 else 42
    SEASON    = sys.argv[4] if len(sys.argv) > 4 else "2024-25"

    db = SessionLocal()
    home_team = db.execute(select(Team).where(Team.abbreviation == HOME_ABBR)).scalar_one_or_none()
    away_team = db.execute(select(Team).where(Team.abbreviation == AWAY_ABBR)).scalar_one_or_none()

    if not home_team or not away_team:
        print(f"Team not found. Check abbreviations.")
        sys.exit(1)

    print(f"\nSeason: {SEASON}")
    print(f"Loading rosters for {home_team.city} {home_team.nickname} vs {away_team.city} {away_team.nickname}...")
    home_players = load_roster(db, home_team.id, SEASON)
    away_players = load_roster(db, away_team.id, SEASON)
    db.close()

    if not home_players or not away_players:
        print(f"Could not load rosters for season {SEASON}. Make sure stats are ingested.")
        sys.exit(1)

    print(f"Simulating with seed={SEED}...\n")
    result = simulate_game(home_players, away_players, seed=SEED, season=SEASON)

    home_ids = {p["id"] for p in home_players}
    away_ids = {p["id"] for p in away_players}
    all_by_id = {p["id"]: p for p in home_players + away_players}

    qs = result["quarter_scores"]
    print(f"  {'':25} {'Q1':>4} {'Q2':>4} {'Q3':>4} {'Q4':>4} {'TOT':>5}")
    print(f"  {home_team.city + ' ' + home_team.nickname:<25} {qs['home'][0]:>4} {qs['home'][1]:>4} {qs['home'][2]:>4} {qs['home'][3]:>4} {result['home_score']:>5}")
    print(f"  {away_team.city + ' ' + away_team.nickname:<25} {qs['away'][0]:>4} {qs['away'][1]:>4} {qs['away'][2]:>4} {qs['away'][3]:>4} {result['away_score']:>5}")

    print_box_score(all_by_id, result["box_score"], f"{home_team.city} {home_team.nickname} (Home)", home_ids)
    print_box_score(all_by_id, result["box_score"], f"{away_team.city} {away_team.nickname} (Away)", away_ids)
