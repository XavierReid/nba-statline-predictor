"""Gap 3.2 calibration harness — Q4 NET(lead-trail) by entering-margin band.

Sweeps the comfortable-lead PROTECT constants against the measured real target
(NET ≈ -0.4 / -0.7 / -1.5 / -0.9 for 0-5 / 6-10 / 11-20 / 21+). Real is computed
once from Game quarter columns; sim from a schedule replay under a given config.

  python -m scratch.q4_role_split --cost 0.16 --pace 0.18 --three 0.12 --sims 2
"""
import argparse
import os
import sys
from dataclasses import replace
from statistics import mean

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.analysis.decomposition import simulate_schedule
from app.database import SessionLocal
from app.models.game import Game
from app.services.sim_config import DRAMA_M3
from sqlalchemy import select

BANDS = [(0, 5), (6, 10), (11, 20), (21, 999)]
REAL_NET = {"0-5": -0.43, "6-10": -0.67, "11-20": -1.46, "21+": -0.91}


def _band(m):
    for lo, hi in BANDS:
        if lo <= m <= hi:
            return f"{lo}-{hi}" if hi < 999 else f"{lo}+"


def _collect(rows):
    agg = {}
    for h1, h2, h3, h4, a1, a2, a3, a4 in rows:
        m = (h1 + h2 + h3) - (a1 + a2 + a3)
        if m == 0:
            continue
        b = _band(abs(m))
        lead, trail = (h4, a4) if m > 0 else (a4, h4)
        agg.setdefault(b, {"lead": [], "trail": []})
        agg[b]["lead"].append(lead)
        agg[b]["trail"].append(trail)
    return agg


def _net(agg, b):
    return mean(agg[b]["lead"]) - mean(agg[b]["trail"])


def run(cost, pace, three, sims_per_game, season="2024-25"):
    cfg = replace(DRAMA_M3, comfortable_lead_efficiency_cost=cost,
                  comfortable_lead_pace_bonus=pace, comfortable_lead_three_shift=three)
    db = SessionLocal()
    games = db.execute(select(Game).where(
        Game.id.like("00224%"), Game.status == "final", Game.home_q1.isnot(None)
    )).scalars().all()
    real_rows = [(g.home_q1, g.home_q2, g.home_q3, g.home_q4,
                  g.away_q1, g.away_q2, g.away_q3, g.away_q4)
                 for g in games if None not in (g.home_q4, g.away_q4)]
    sims = simulate_schedule(db, season, cfg, sims_per_game)
    sim_rows = [(*g["quarter_scores"]["home"][:4], *g["quarter_scores"]["away"][:4]) for g in sims]
    db.close()

    R, S = _collect(real_rows), _collect(sim_rows)
    print(f"\n  comfortable_lead: cost={cost} pace={pace} three={three}  (sims={sims_per_game})")
    print(f"  {'band':8}{'real NET':>10}{'sim NET':>10}{'diff':>8}{'Q4 total r/s':>16}")
    for lo, hi in BANDS:
        b = f"{lo}-{hi}" if hi < 999 else f"{lo}+"
        rn, sn = _net(R, b), _net(S, b)
        rt = mean(R[b]["lead"]) + mean(R[b]["trail"])
        st = mean(S[b]["lead"]) + mean(S[b]["trail"])
        print(f"  {b:8}{rn:>10.2f}{sn:>10.2f}{sn - rn:>+8.2f}{rt:>8.1f}/{st:<7.1f}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--cost", type=float, default=DRAMA_M3.comfortable_lead_efficiency_cost)
    p.add_argument("--pace", type=float, default=DRAMA_M3.comfortable_lead_pace_bonus)
    p.add_argument("--three", type=float, default=DRAMA_M3.comfortable_lead_three_shift)
    p.add_argument("--sims", type=int, default=2)
    args = p.parse_args()
    run(args.cost, args.pace, args.three, args.sims)
