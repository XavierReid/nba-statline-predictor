"""Gap 3.4 rotation disambiguation — separate roster REPRESENTATION from rotation POLICY.

The 15-player experiment only falsified "load more and change nothing else." Separate
the owners with a 2-factor sweep (rotation logic held fixed = DRAMA_M3):
  Factor A: loaded depth (10 / 12 / 15)
  Factor B: minutes weighting (GP = MPGxGP [production] vs MPG-only [availability-neutral])

Measure sim minutes by tier vs real MPG. Watch STAR minutes for the garbage-time
regression that sank the 15-player run. Attribute the primary deficit to each component.

Usage: python scratch/gap34_minutes_factorial.py --season 2016-17 --max-games 250
"""
import argparse
import os
import sys
from collections import defaultdict
from statistics import mean

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import select
from app.database import SessionLocal
from app.models.player import Player
from app.models.player_attributes import PlayerAttributes
from app.models.player_tendencies import PlayerTendencies
from app.models.player_season_stats import PlayerSeasonStats
from app.services.roster import roster_provider_for, _build_roster, _league_zone_prior
import app.analysis.player_accounting as pa
from app.analysis.player_accounting import real_accounts, sim_accounts, TIERS


def make_loader(depth, gp_weight):
    def custom_load(db, team_id, season):
        prov = roster_provider_for(season)
        rows = db.execute(
            select(Player, PlayerAttributes, PlayerTendencies, PlayerSeasonStats)
            .join(PlayerAttributes, PlayerAttributes.player_id == Player.id)
            .join(PlayerTendencies, PlayerTendencies.player_id == Player.id)
            .join(PlayerSeasonStats, PlayerSeasonStats.player_id == Player.id)
            .where(prov._team_membership(team_id))
            .where(PlayerAttributes.season == season)
            .where(PlayerTendencies.season == season)
            .where(PlayerSeasonStats.season == season)
            .order_by(PlayerSeasonStats.minutes_per_game.desc())
            .limit(depth)
        ).all()
        if not rows:
            return []
        raw = {p.id: (s.minutes_per_game or 0.0, s.games_played or 0) for (p, a, t, s) in rows}
        roster = _build_roster(rows, _league_zone_prior(db, season))
        ws = [(raw[pd["id"]][0] * raw[pd["id"]][1]) if gp_weight else raw[pd["id"]][0]
              for pd in roster]
        total = sum(ws) or 1.0
        for pd, w in zip(roster, ws):
            pd["minutes"] = round(w / total * 240, 1)
        order = sorted(range(len(roster)), key=lambda i: -roster[i]["minutes"])
        for rank, i in enumerate(order):
            roster[i]["is_starter"] = rank < 5
        return roster
    return custom_load


def minutes_by_tier(db, season, tiers, max_games):
    sim = sim_accounts(db, season, tiers, sims_per_game=1, max_games=max_games)
    agg = defaultdict(list)
    for pid, a in sim.items():
        t = tiers.get(pid)
        if t:
            agg[t].append(a.minutes)
    return {t: (mean(v) if v else 0) for t, v in agg.items()}


def main(season, max_games):
    db = SessionLocal()
    real = real_accounts(db, season)
    tiers = {pid: a.tier for pid, a in real.items()}
    real_min = {}
    ra = defaultdict(list)
    for pid, a in real.items():
        ra[a.tier].append(a.minutes)
    for t in TIERS:
        real_min[t] = mean(ra[t]) if ra[t] else 0

    configs = [(10, True), (10, False), (12, True), (12, False), (15, True)]
    print(f"\n{'='*78}\n  Gap 3.4 minutes factorial (rotation fixed=DRAMA_M3): {season}  (max_games={max_games})\n{'='*78}")
    print(f"  {'config':<18}" + "".join(f"{t[:4]:>11}" for t in TIERS))
    print(f"  {'REAL MPG':<18}" + "".join(f"{real_min[t]:>11.1f}" for t in TIERS))
    print("  " + "-" * 74)
    orig = pa.load_roster
    for depth, gpw in configs:
        pa.load_roster = make_loader(depth, gpw)
        m = minutes_by_tier(db, season, tiers, max_games)
        lab = f"depth{depth} {'GP' if gpw else 'MPG'}"
        print(f"  {lab:<18}" + "".join(f"{m.get(t,0):>11.1f}" for t in TIERS))
        print(f"  {'  vs real':<18}" + "".join(f"{m.get(t,0)-real_min[t]:>+11.1f}" for t in TIERS))
    pa.load_roster = orig
    db.close()
    print()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2016-17")
    p.add_argument("--max-games", type=int, default=250)
    a = p.parse_args()
    main(a.season, a.max_games)
