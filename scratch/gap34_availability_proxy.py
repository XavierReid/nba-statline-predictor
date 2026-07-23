"""Gap 3.4 availability — season-GP proxy for the missing per-game availability model.

FULL per-game active-count distribution (8/9/.../13 players logging minutes) needs
PlayerGameLog (not ingested — the "3.4d blocked" item). This is the season-aggregate
proxy, which tests the CORE of the hypothesis:
  - avg active players/game = Σ games_played / team_games  (should be ~9-11 if teams
    rotate a ~12-13 man roster with intermittent 11-13).
  - GP by MPG rank: are ranks 1-9/10 near-full-season (regulars) and 11-13 intermittent?
  - This is WHY top-10 MPG sums > 240: primaries/rotation with GP<82 get games-weighted
    below their MPG; real teams play them their MPG only on active nights.

Not measured here (needs game logs): the per-game count DISTRIBUTION and whether
inactive players are true bench vs rotation players randomly missing.

Usage: python scratch/gap34_availability_proxy.py --season 2016-17
"""
import argparse
import os
import sys
from collections import defaultdict
from statistics import mean

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import select
from app.database import SessionLocal
from app.models.player_season_stats import PlayerSeasonStats

TEAM_GAMES = 82


def main(season):
    db = SessionLocal()
    rows = db.execute(select(PlayerSeasonStats).where(
        PlayerSeasonStats.season == season,
        PlayerSeasonStats.minutes_per_game.isnot(None),
    )).scalars().all()
    db.close()

    by_team = defaultdict(list)
    for s in rows:
        if (s.minutes_per_game or 0) > 0 and s.team_id:
            by_team[s.team_id].append((s.minutes_per_game, s.games_played or 0))

    active_per_game = []           # Σ GP / 82 per team
    gp_by_rank = defaultdict(list)  # MPG-rank (1..) -> GP list
    roster_size = []               # players with MPG>0
    reg = inter = deep = 0         # GP>=70 / 41-69 / <=40, among top-13 by MPG
    for tid, players in by_team.items():
        players.sort(key=lambda x: -x[0])   # by MPG desc
        active_per_game.append(sum(gp for _, gp in players) / TEAM_GAMES)
        roster_size.append(sum(1 for mpg, _ in players if mpg > 0))
        for rank, (mpg, gp) in enumerate(players[:15], 1):
            gp_by_rank[rank].append(gp)
        for mpg, gp in players[:13]:
            if gp >= 70:
                reg += 1
            elif gp >= 41:
                inter += 1
            else:
                deep += 1

    print(f"\n{'='*60}\n  Gap 3.4 availability proxy (season GP): {season}\n{'='*60}")
    print(f"  teams: {len(by_team)}")
    print(f"  avg ACTIVE players/game (Σ GP / 82): {mean(active_per_game):.1f}"
          f"   [range {min(active_per_game):.1f}-{max(active_per_game):.1f}]")
    print(f"  avg roster size (MPG>0):             {mean(roster_size):.1f}")
    print(f"\n  games_played by MPG rank (regulars near {TEAM_GAMES}, tail intermittent):")
    for rank in range(1, 14):
        if gp_by_rank[rank]:
            g = gp_by_rank[rank]
            print(f"    rank {rank:>2}: GP mean {mean(g):>5.1f}  (min {min(g):>2.0f})  n_teams={len(g)}")
    tot = reg + inter + deep
    print(f"\n  among top-13/team by MPG:  regulars (GP>=70) {reg/tot*100:.0f}%  "
          f"intermittent (41-69) {inter/tot*100:.0f}%  deep (<=40) {deep/tot*100:.0f}%")
    print()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2016-17")
    a = p.parse_args()
    main(a.season)
