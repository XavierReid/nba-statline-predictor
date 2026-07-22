"""Gap 3.3 instrument — end-of-regulation margin distribution: sim vs real.

Step 1 of the instrument-first plan: does the sim SPIKE at exact ties the way
real basketball does? A game goes to OT iff regulation ends at margin 0. Real
teams engineer the tie on the final possession (shoot to tie), producing more
0-margin endings than a smooth close-game distribution predicts.

Real end-of-regulation margin = sum(home q1..q4) - sum(away q1..q4)  (line scores).
Sim end-of-regulation margin = sum(quarter_scores first 4).

Usage: python scratch/gap33_ot_instrument.py --season 2016-17 --sims-per-game 2
"""
import argparse
import os
import sys
import zlib
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models.game import Game
from app.models.team import Team
from app.services.game_simulator import simulate_game
from app.services.roster import load_roster
from app.services.sim_config import DRAMA_M3
from sqlalchemy import select


def reg_margin_real(g):
    h = (g.home_q1 or 0) + (g.home_q2 or 0) + (g.home_q3 or 0) + (g.home_q4 or 0)
    a = (g.away_q1 or 0) + (g.away_q2 or 0) + (g.away_q3 or 0) + (g.away_q4 or 0)
    return h - a


def summarize(label, margins):
    n = len(margins)
    am = [abs(m) for m in margins]
    tie = sum(1 for m in margins if m == 0) / n * 100
    within1 = sum(1 for m in am if m == 1) / n * 100
    within2 = sum(1 for m in am if m == 2) / n * 100
    within3 = sum(1 for m in am if m == 3) / n * 100
    print(f"\n  {label}  (n={n})")
    print(f"    OT rate (margin==0)     : {tie:5.2f}%")
    print(f"    |margin|==1             : {within1:5.2f}%")
    print(f"    |margin|==2             : {within2:5.2f}%")
    print(f"    |margin|==3             : {within3:5.2f}%")
    print(f"    |margin|<=3 (excl tie)  : {within1+within2+within3:5.2f}%")
    print(f"    |margin|<=5 (excl tie)  : {sum(1 for m in am if 1<=m<=5)/n*100:5.2f}%")
    # fine histogram of signed small margins
    c = Counter(m for m in margins if abs(m) <= 6)
    line = "    hist -6..6: " + " ".join(
        f"{k:+d}:{c.get(k,0)/n*100:.1f}" for k in range(-6, 7))
    print(line)
    return tie


def main(season, sims_per_game):
    db = SessionLocal()
    year = season.split("-")[0][-2:]
    games = db.execute(
        select(Game).where(
            Game.id.like(f"002{year}%"),
            Game.status == "final",
            Game.home_q4.isnot(None),
        )
    ).scalars().all()

    teams = db.execute(select(Team)).scalars().all()
    rosters = {}
    for t in teams:
        r = load_roster(db, t.id, season)
        if r:
            rosters[t.id] = r

    real_margins, sim_margins = [], []
    skipped = 0
    for g in games:
        if g.home_team_id not in rosters or g.away_team_id not in rosters:
            skipped += 1
            continue
        real_margins.append(reg_margin_real(g))
        base_seed = zlib.crc32(str(g.id).encode())
        for k in range(sims_per_game):
            r = simulate_game(
                rosters[g.home_team_id], rosters[g.away_team_id],
                seed=base_seed + k, season=season, config=DRAMA_M3,
                home_team_id=g.home_team_id, away_team_id=g.away_team_id, db=db,
            )
            qs = r["quarter_scores"]
            sim_margins.append(sum(qs["home"][:4]) - sum(qs["away"][:4]))
    db.close()

    print(f"\n{'='*60}\n  Gap 3.3 end-of-regulation margin: {season}  (skipped {skipped})\n{'='*60}")
    rt = summarize("REAL", real_margins)
    st = summarize("SIM ", sim_margins)
    print(f"\n  OT-rate gap: sim {st:.2f}% vs real {rt:.2f}%  (ratio {st/rt:.2f}x)\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2016-17")
    p.add_argument("--sims-per-game", type=int, default=2)
    a = p.parse_args()
    main(a.season, a.sims_per_game)
