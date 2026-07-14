"""Team box-score aggregates (gap 3.5) — league-level per-team-per-game.

The scoring/possession accounting is calibrated, but the non-scoring box-score
aggregates (assists, rebounds, steals, blocks, turnovers, fouls) have only been
spot-checked. This measures each at the league average per team-game: real from
PlayerSeasonStats season totals (stat_pg × games_played, summed over all players,
divided by real team-games), sim from a schedule replay's box scores. Attribution
across teams washes out at the league level, so a divergence here is an engine-wide
rate error in that event, not a roster-mix artifact.
"""
import argparse
import os
import sys
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.analysis.decomposition import simulate_schedule
from app.database import SessionLocal
from app.models.game import Game
from app.models.player_season_stats import PlayerSeasonStats
from app.services.sim_config import DRAMA_M3
from sqlalchemy import func, select

_STATS = ["pts", "ast", "reb", "stl", "blk", "tov", "pf"]
# PlayerSeasonStats field name per box key (pf/fouls not stored real-side).
_REAL_FIELD = {"pts": "points", "ast": "assists", "reb": "rebounds",
               "stl": "steals", "blk": "blocks", "tov": "turnovers"}


def real_per_team_game(db, season: str) -> Dict[str, dict]:
    """Per-team-game real aggregates, each as {raw, adj}.

    `raw` sums PlayerSeasonStats (rotation-filtered, so ~8% low on totals). `adj`
    scales the non-scoring stats by the roster-completeness factor derived from the
    ONE stat we have complete + accurate: team points from the Game table. This
    corrects the uniform undercount (assumes missing fringe players contribute
    proportionally across stats — approximate but far better than raw).
    """
    year = season.split("-")[0][-2:]
    scores = db.execute(
        select(Game.home_score, Game.away_score).where(
            Game.id.like(f"002{year}%"), Game.status == "final",
            Game.home_score.isnot(None),
        )
    ).all()
    team_games = len(scores) * 2
    true_pts = sum(h + a for h, a in scores) / team_games  # complete + accurate

    rows = db.execute(
        select(PlayerSeasonStats).where(PlayerSeasonStats.season == season)
    ).scalars().all()
    raw = {}
    for key, field in _REAL_FIELD.items():
        total = sum((getattr(p, field) or 0.0) * (p.games_played or 0) for p in rows)
        raw[key] = total / team_games
    factor = true_pts / raw["pts"] if raw["pts"] else 1.0
    out = {"_factor": factor}
    out["pts"] = {"raw": raw["pts"], "adj": true_pts}
    for key in _REAL_FIELD:
        if key != "pts":
            out[key] = {"raw": raw[key], "adj": raw[key] * factor}
    return out


def sim_per_team_game(sims: List[dict]) -> Dict[str, float]:
    team_games = len(sims) * 2
    totals = {k: 0.0 for k in _STATS}
    for g in sims:
        for stats in g["box_score"].values():
            for k in _STATS:
                totals[k] += stats.get(k, 0)
    return {k: totals[k] / team_games for k in _STATS}


def run(season: str, sims_per_game: int, config=DRAMA_M3) -> None:
    db = SessionLocal()
    real = real_per_team_game(db, season)
    sims = simulate_schedule(db, season, config, sims_per_game)
    sim = sim_per_team_game(sims)
    db.close()

    w = 64
    print("\n" + "=" * w)
    print(f"  Team box-score aggregates — per team-game — {season}  (sim n={len(sims)})")
    print(f"  real 'adj' = completeness-scaled by ×{real['_factor']:.3f} "
          f"(PlayerSeasonStats is ~{100*(1-1/real['_factor']):.0f}% short on rosters)")
    print("=" * w)
    print(f"  {'stat':6}{'real raw':>10}{'real adj':>10}{'sim':>10}{'sim/adj':>10}")
    for k in _STATS:
        s = sim[k]
        r = real.get(k)
        if r is None:
            print(f"  {k:6}{'—':>10}{'—':>10}{s:>10.2f}{'(no real)':>10}")
            continue
        ratio = s / r["adj"] if r["adj"] else 0.0
        print(f"  {k:6}{r['raw']:>10.2f}{r['adj']:>10.2f}{s:>10.2f}{ratio:>9.2f}x")
    print("=" * w + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2024-25")
    p.add_argument("--sims", type=int, default=2)
    args = p.parse_args()
    run(args.season, args.sims)
