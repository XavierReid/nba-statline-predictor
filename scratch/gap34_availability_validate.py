"""Gap 3.4 availability — re-measure after implementing the availability layer.

Runs DRAMA_M3 + use_availability (depth 14) over the schedule and checks:
  - active players/team-game: mean + distribution (vs real 10.59, 9-13)
  - minutes by tier PER ACTIVE GAME vs real MPG (the deficit that motivated this)
  - star & bench minutes
  - total team minutes/game (identity ~240)

Usage: python scratch/gap34_availability_validate.py --season 2016-17 --max-games 400
"""
import argparse
import os
import sys
import zlib
from collections import Counter, defaultdict
from dataclasses import replace
from statistics import mean

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import select
from app.database import SessionLocal
from app.models.game import Game
from app.models.team import Team
from app.analysis.player_accounting import real_accounts, TIERS
from app.services.game_simulator import simulate_game
from app.services.roster import load_roster
from app.services.sim_config import DRAMA_M3


def main(season, max_games, depth, min_active):
    db = SessionLocal()
    real = real_accounts(db, season)
    tiers = {pid: a.tier for pid, a in real.items()}
    real_min = defaultdict(list)
    for pid, a in real.items():
        real_min[a.tier].append(a.minutes)
    real_min = {t: (mean(v) if v else 0) for t, v in real_min.items()}

    rosters = {}
    for t in db.execute(select(Team)).scalars().all():
        r = load_roster(db, t.id, season, depth=depth)
        if r:
            rosters[t.id] = r

    cfg = replace(DRAMA_M3, use_availability=True, roster_depth=depth,
                  availability_min_active=min_active)
    year = season.split("-")[0][-2:]
    games = db.execute(select(Game).where(
        Game.id.like(f"002{year}%"), Game.status == "final", Game.home_score.isnot(None))
    ).scalars().all()
    if max_games and len(games) > max_games:
        games = games[::len(games) // max_games]

    active_counts = []
    team_min = []                       # total minutes per team-game
    pmin = defaultdict(lambda: [0.0, 0])  # pid -> [sum_min, active_games]
    for g in games:
        if g.home_team_id not in rosters or g.away_team_id not in rosters:
            continue
        res = simulate_game(rosters[g.home_team_id], rosters[g.away_team_id],
                            seed=zlib.crc32(str(g.id).encode()), season=season, config=cfg,
                            home_team_id=g.home_team_id, away_team_id=g.away_team_id, db=db)
        box = res["box_score"]
        for side_ids in (set(p["id"] for p in rosters[g.home_team_id]),
                         set(p["id"] for p in rosters[g.away_team_id])):
            act = 0
            tmin = 0.0
            for pid in side_ids:
                m = box.get(pid, {}).get("min", 0)
                if m > 0:
                    act += 1
                    pmin[pid][0] += m
                    pmin[pid][1] += 1
                    tmin += m
            active_counts.append(act)
            team_min.append(tmin)
    db.close()

    print(f"\n{'='*60}\n  Gap 3.4 availability re-measure: {season} (depth {depth})\n{'='*60}")
    print(f"  active players/team-game: mean {mean(active_counts):.2f}   (real 10.59)")
    dist = Counter(active_counts)
    n = len(active_counts)
    print("  distribution: " + "  ".join(f"{c}:{dist.get(c,0)/n*100:.0f}%" for c in range(7, 15) if dist.get(c)))
    print(f"  total team minutes/game: {mean(team_min):.1f}   (identity 240)")

    print(f"\n  minutes by tier PER ACTIVE GAME (sim vs real MPG):")
    print(f"  {'tier':<10}{'real':>8}{'sim':>8}{'diff':>8}")
    tier_min = defaultdict(list)
    for pid, (s, gp) in pmin.items():
        if gp and pid in tiers:
            tier_min[tiers[pid]].append(s / gp)
    for t in TIERS:
        if tier_min[t]:
            sm = mean(tier_min[t])
            print(f"  {t:<10}{real_min[t]:>8.1f}{sm:>8.1f}{sm-real_min[t]:>+8.1f}")
    print()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2016-17")
    p.add_argument("--max-games", type=int, default=400)
    p.add_argument("--depth", type=int, default=18)
    p.add_argument("--min-active", type=int, default=9)
    a = p.parse_args()
    main(a.season, a.max_games, a.depth, a.min_active)
