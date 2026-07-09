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
    SHOW_PBP = "--pbp" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--pbp"]
    HOME_ABBR = args[0] if len(args) > 0 else "DEN"
    AWAY_ABBR = args[1] if len(args) > 1 else "GSW"
    SEED      = int(args[2]) if len(args) > 2 else 42
    SEASON    = args[3] if len(args) > 3 else "2024-25"
    PRESET    = args[4] if len(args) > 4 else "drama-m3"

    from app.api.schemas.simulations import _PRESETS
    if PRESET not in _PRESETS:
        print(f"Unknown preset '{PRESET}'. Valid: {list(_PRESETS)}")
        sys.exit(1)
    config = _PRESETS[PRESET]

    if HOME_ABBR.upper() == AWAY_ABBR.upper():
        # Mirror matchups are valid ONLY in diagnostics (dispersion isolation);
        # here both teams would share player ids and the box score merges sides.
        print("Home and away must be different teams.")
        sys.exit(1)

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

    if not home_players or not away_players:
        print(f"Could not load rosters for season {SEASON}. Make sure stats are ingested.")
        sys.exit(1)

    print(f"Simulating with seed={SEED}, preset={PRESET}...\n")
    result = simulate_game(home_players, away_players, seed=SEED, season=SEASON,
                           config=config, capture_descriptions=SHOW_PBP,
                           home_team_id=home_team.id, away_team_id=away_team.id, db=db)

    home_ids = {p["id"] for p in home_players}
    away_ids = {p["id"] for p in away_players}
    all_by_id = {p["id"]: p for p in home_players + away_players}

    qs = result["quarter_scores"]
    print(f"  {'':25} {'Q1':>4} {'Q2':>4} {'Q3':>4} {'Q4':>4} {'TOT':>5}")
    print(f"  {home_team.city + ' ' + home_team.nickname:<25} {qs['home'][0]:>4} {qs['home'][1]:>4} {qs['home'][2]:>4} {qs['home'][3]:>4} {result['home_score']:>5}")
    print(f"  {away_team.city + ' ' + away_team.nickname:<25} {qs['away'][0]:>4} {qs['away'][1]:>4} {qs['away'][2]:>4} {qs['away'][3]:>4} {result['away_score']:>5}")

    print_box_score(all_by_id, result["box_score"], f"{home_team.city} {home_team.nickname} (Home)", home_ids)
    print_box_score(all_by_id, result["box_score"], f"{away_team.city} {away_team.nickname} (Away)", away_ids)

    if SHOW_PBP:
        print("\n  Play-by-play")
        print(f"  {'#':>4} {'Q':>2} {'clock':>6} {'team':<4} {'score':>9}  description")
        run_h = run_a = 0
        for e in result["events"]:
            run_h += e["pts"] if e["is_home"] else 0
            run_a += e["pts"] if not e["is_home"] else 0
            clock = f"{e['game_clock_seconds'] // 60}:{e['game_clock_seconds'] % 60:02d}"
            team = HOME_ABBR if e["is_home"] else AWAY_ABBR
            print(f"  {e['possession']:>4} {e['quarter']:>2} {clock:>6} {team:<4} {run_h:>4}-{run_a:<4}  {e.get('description') or ''}")
