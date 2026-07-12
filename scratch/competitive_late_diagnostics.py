"""Gap 3.2 — what is the SIM doing in competitive-late possessions? (instrument-first)

Measured target: competitive-Q4 point-differential variance is ~76.5 in the sim vs
60.6 real (+26%). It is a per-possession decision-quality effect, not pace. Before
modeling any behavior, decompose what the sim ACTUALLY does on competitive-late
possessions vs normal ones, and which shot types drive the per-possession points
variance. No mechanism change here.

COMPETITIVE_LATE proxy: quarter 4, |running margin| <= 8 (concession is margin>=20,
so it never overlaps). NORMAL: quarters 1-3.

Usage:
    python scratch/competitive_late_diagnostics.py [--games 400]
"""
import argparse
import os
import sys
from collections import defaultdict
from statistics import mean, pvariance

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models.team import Team
from app.services.game_simulator import simulate_game
from app.services.roster import load_roster
from app.services.sim_config import DRAMA_M3
from sqlalchemy import select

MATCHUPS = [
    ("BOS", "LAL"), ("OKC", "GSW"), ("DEN", "MIL"), ("PHX", "MIA"),
    ("HOU", "MIN"), ("LAL", "CHA"), ("BOS", "DET"), ("GSW", "WAS"),
    ("OKC", "UTA"), ("DEN", "SAS"),
]


def main(n_games):
    db = SessionLocal()
    rosters, ids = {}, {}
    for h, a in MATCHUPS:
        for ab in (h, a):
            if ab not in rosters:
                t = db.execute(select(Team).where(Team.abbreviation == ab)).scalar_one()
                rosters[ab] = load_roster(db, t.id, "2025-26")
                ids[ab] = t.id
    n_per = max(1, n_games // len(MATCHUPS))

    # bucket -> per-possession point samples + behavior counters
    pts = defaultdict(list)
    beh = defaultdict(lambda: {"poss": 0, "shots": 0, "threes": 0, "rim": 0, "mid": 0,
                               "fta": 0, "tov": 0, "fastbreak": 0, "made": 0})
    k = 0
    for h, a in MATCHUPS:
        for i in range(n_per):
            r = simulate_game(rosters[h], rosters[a], seed=k * 7919 + i, season="2025-26",
                              config=DRAMA_M3, capture_descriptions=True,
                              home_team_id=ids[h], away_team_id=ids[a], db=db)
            k += 1
            run_h = run_a = 0
            for e in r["events"]:
                q = e["quarter"]
                margin = run_h - run_a
                if q <= 3:
                    bucket = "NORMAL"
                elif q == 4 and abs(margin) <= 8:
                    bucket = "COMPETITIVE_LATE"
                else:
                    bucket = None
                if bucket:
                    pts[bucket].append(e.get("pts", 0))
                    b = beh[bucket]
                    b["poss"] += 1
                    st = e.get("shot_type")
                    if st:
                        b["shots"] += 1
                        if "three" in st:
                            b["threes"] += 1
                        elif st in ("layup", "dunk", "floater", "close"):
                            b["rim"] += 1
                        else:
                            b["mid"] += 1
                        if e.get("made"):
                            b["made"] += 1
                    if e.get("fta"):
                        b["fta"] += e["fta"]
                    if e.get("turnover_by"):
                        b["tov"] += 1
                    if e.get("is_fastbreak"):
                        b["fastbreak"] += 1
                if e["is_home"]:
                    run_h += e.get("pts", 0)
                else:
                    run_a += e.get("pts", 0)
    db.close()

    print(f"\n{'='*66}\n  Sim competitive-late behavior — {k} games\n{'='*66}")
    print(f"  {'metric':<34} {'NORMAL':>12} {'COMP_LATE':>12}")

    def row(label, fn):
        print(f"  {label:<34} {fn('NORMAL'):>12} {fn('COMPETITIVE_LATE'):>12}")

    row("possessions sampled", lambda b: f"{beh[b]['poss']}")
    row("pts/possession (mean)", lambda b: f"{mean(pts[b]):.3f}")
    row("pts/possession VARIANCE", lambda b: f"{pvariance(pts[b]):.3f}")
    row("3PA % of shots", lambda b: f"{beh[b]['threes']/max(beh[b]['shots'],1)*100:.1f}%")
    row("rim % of shots", lambda b: f"{beh[b]['rim']/max(beh[b]['shots'],1)*100:.1f}%")
    row("mid % of shots", lambda b: f"{beh[b]['mid']/max(beh[b]['shots'],1)*100:.1f}%")
    row("FG% (shots made)", lambda b: f"{beh[b]['made']/max(beh[b]['shots'],1)*100:.1f}%")
    row("FTA per 100 poss", lambda b: f"{beh[b]['fta']/max(beh[b]['poss'],1)*100:.1f}")
    row("TOV per 100 poss", lambda b: f"{beh[b]['tov']/max(beh[b]['poss'],1)*100:.1f}")
    row("fastbreak % of poss", lambda b: f"{beh[b]['fastbreak']/max(beh[b]['poss'],1)*100:.1f}%")
    print(f"\n  Real competitive-Q4 point-differential variance target: ~60.6 (sim game-level ~76.5)")
    print(f"{'='*66}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=400)
    args = parser.parse_args()
    main(args.games)
