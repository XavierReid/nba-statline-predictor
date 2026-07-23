"""Gap 3.4 — real per-game active-roster distribution from PlayerGameLog.

The season-GP proxy showed avg ~10.6 active/game and intermittent 11-13. This
measures the actual per-game process (the availability model's calibration target):
  - active players per team-game (min>0): mean + distribution (8/9/10/11/12/13+).
  - how often each season-MPG rank (1..14) is ACTIVE (appearance rate).
  - are inactive players true bench, or do rotation regulars (top-9) miss games?

Usage: python scratch/gap34_active_roster.py --season 2016-17
"""
import argparse
import os
import sys
from collections import Counter, defaultdict
from statistics import mean

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import select
from app.database import SessionLocal
from app.models.player_game_log import PlayerGameLog
from app.models.player_season_stats import PlayerSeasonStats


def main(season):
    db = SessionLocal()
    logs = db.execute(select(PlayerGameLog).where(PlayerGameLog.season == season)).scalars().all()
    # season MPG rank within team
    stats = db.execute(select(PlayerSeasonStats).where(PlayerSeasonStats.season == season)).scalars().all()
    db.close()

    mpg_rank = {}   # (team_id, player_id) -> rank (1 = highest MPG)
    by_team_players = defaultdict(list)
    for s in stats:
        if s.team_id and (s.minutes_per_game or 0) > 0:
            by_team_players[s.team_id].append((s.minutes_per_game, s.player_id))
    for tid, ps in by_team_players.items():
        for rank, (_, pid) in enumerate(sorted(ps, key=lambda x: -x[0]), 1):
            mpg_rank[(tid, pid)] = rank

    # active players per team-game
    per_game = defaultdict(int)            # (game_id, team_id) -> active count
    rank_active = defaultdict(int)         # rank -> games active
    for l in logs:
        if l.minutes > 0:
            per_game[(l.game_id, l.team_id)] += 1
            r = mpg_rank.get((l.team_id, l.player_id))
            if r:
                rank_active[r] += 1

    counts = list(per_game.values())
    n_tg = len(counts)
    team_games = {}   # team_id -> number of games (for appearance rate)
    for (gid, tid) in per_game:
        team_games[tid] = team_games.get(tid, 0) + 1
    total_team_games = sum(team_games.values())

    print(f"\n{'='*58}\n  Gap 3.4 real active-roster per game: {season}\n{'='*58}")
    print(f"  team-games: {n_tg}   logs: {len(logs)}")
    print(f"  ACTIVE players / team-game: mean {mean(counts):.2f}")
    dist = Counter(counts)
    print(f"\n  distribution of active count:")
    for c in range(min(dist), max(dist) + 1):
        pct = dist.get(c, 0) / n_tg * 100
        print(f"    {c:>2} active: {pct:>5.1f}%  {'#'*int(pct/2)}")
    print(f"\n  appearance rate by season-MPG rank (active games / team-games):")
    for r in range(1, 15):
        if r in rank_active:
            print(f"    rank {r:>2}: {rank_active[r]/total_team_games*100:>5.1f}% of team-games active")
    print()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2016-17")
    a = p.parse_args()
    main(a.season)
