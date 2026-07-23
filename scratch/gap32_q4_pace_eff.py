"""Gap 3.2 (reopened) — decompose the close-game Q4 over-scoring: pace vs efficiency.

The 0-5 entering-Q4 band over-grows |margin| (+1.25) with NET matched, driven by
+5.5 Q4 total points (sim 60.6 vs real 55.1). Split that into:
  FREQUENCY (pace): scoring events per Q4 (how many makes) — the margin-spread driver
  MAGNITUDE (eff) : points per scoring event
for entering-0-5 games, real vs sim. Real from game_scoring_events (made-only, exact
scoring-event count + points); sim from events. Points/Q4 = events/Q4 x pts/event.

Also: does the sim SLOW its Q4 like real? Compare scoring events Q1 vs Q4 within each
(real can't give possessions, but scoring-event frequency is the shared proxy), and the
sim's true possession count Q1 vs Q4.

Usage: python scratch/gap32_q4_pace_eff.py --season 2024-25 --sims-per-game 3
"""
import argparse
import os
import sys
import zlib
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models.game import Game
from app.models.scoring_event import ScoringEvent
from app.models.team import Team
from app.services.game_simulator import simulate_game
from app.services.roster import load_roster
from app.services.sim_config import DRAMA_M3
from sqlalchemy import select

THREES = {"corner_three", "above_break_three", "three"}


def entering_q4_margin_real(g):
    h = (g.home_q1 or 0) + (g.home_q2 or 0) + (g.home_q3 or 0)
    a = (g.away_q1 or 0) + (g.away_q2 or 0) + (g.away_q3 or 0)
    return h - a


def main(season, spg):
    db = SessionLocal()
    year = season.split("-")[0][-2:]
    games = db.execute(select(Game).where(
        Game.id.like(f"002{year}%"), Game.status == "final", Game.home_q4.isnot(None)
    )).scalars().all()

    # ---- REAL: close-entering games, Q1 & Q4 scoring-event freq + pts/event ----
    r_close = [g for g in games if abs(entering_q4_margin_real(g)) <= 5]
    # real FTs are logged individually (points==1); points in {2,3} = a field goal.
    r = {"q1_ev": 0, "q4_ev": 0, "q4_pts": 0, "q4_fg_pts": 0, "q4_ft_pts": 0, "q4_fg": 0, "n": 0}
    for g in r_close:
        evs = db.execute(select(ScoringEvent).where(ScoringEvent.game_id == g.id)).scalars().all()
        r["n"] += 1
        for e in evs:
            if e.period == 1:
                r["q1_ev"] += 1
                if e.points in (2, 3):
                    r["q1_fg"] = r.get("q1_fg", 0) + 1
            elif e.period == 4:
                r["q4_ev"] += 1
                r["q4_pts"] += e.points
                if e.points in (2, 3):
                    r["q4_fg_pts"] += e.points
                    r["q4_fg"] += 1
                else:
                    r["q4_ft_pts"] += e.points

    # ---- SIM: same, plus possession counts ----
    teams = db.execute(select(Team)).scalars().all()
    ros = {t.id: load_roster(db, t.id, season) for t in teams}
    ros = {k: v for k, v in ros.items() if v}
    s = {"q1_ev": 0, "q4_ev": 0, "q4_pts": 0, "q4_fg_pts": 0, "q4_ft_pts": 0, "q4_fg": 0,
         "q1_poss": 0, "q4_poss": 0, "n": 0}
    for g in games:
        if abs(entering_q4_margin_real(g)) > 5:
            continue
        if g.home_team_id not in ros or g.away_team_id not in ros:
            continue
        for k in range(spg):
            res = simulate_game(ros[g.home_team_id], ros[g.away_team_id],
                                seed=zlib.crc32(str(g.id).encode()) + k, season=season,
                                config=DRAMA_M3, home_team_id=g.home_team_id,
                                away_team_id=g.away_team_id, db=db, capture_descriptions=True)
            # sim's own entering-Q4 margin (should be close since we matched schedule close games,
            # but the sim can diverge — filter on the SIM's Q3 margin for a clean 0-5 comparison)
            qs = res["quarter_scores"]
            simeq4 = abs(sum(qs["home"][:3]) - sum(qs["away"][:3]))
            if simeq4 > 5:
                continue
            s["n"] += 1
            for ev in res["events"]:
                q = ev["quarter"]
                if q == 1:
                    s["q1_poss"] += 1
                    if ev.get("shot_type") is not None:
                        s["q1_fga"] = s.get("q1_fga", 0) + 1
                        if ev.get("made"):
                            s["q1_fg"] = s.get("q1_fg", 0) + 1
                elif q == 4:
                    s["q4_poss"] += 1
                    ftm = ev.get("ftm", 0) or 0
                    fg_pts = ev["pts"] - ftm  # points from the field goal (0/2/3)
                    s["q4_pts"] += ev["pts"]
                    s["q4_ft_pts"] += ftm
                    s["q4_fg_pts"] += fg_pts
                    if ev.get("shot_type") is not None:
                        s["q4_fga"] = s.get("q4_fga", 0) + 1
                    if fg_pts > 0:
                        s["q4_fg"] += 1
                    if ev["pts"] > 0:
                        s["q4_ev"] += 1
    db.close()

    def per(d, key):
        return d[key] / d["n"] if d["n"] else 0

    print(f"\n{'='*66}\n  Gap 3.2 close-game (enter Q4 |m|<=5) Q4 pace vs efficiency: {season}\n{'='*66}")
    print(f"  real close games: {r['n']}   sim close (own Q3 |m|<=5): {s['n']}")
    print(f"\n  {'metric (per game)':<30}{'REAL':>9}{'SIM':>9}{'diff':>9}")
    rows = [
        ("Q4 total points", per(r, "q4_pts"), per(s, "q4_pts")),
        ("  Q4 points from FIELD GOALS", per(r, "q4_fg_pts"), per(s, "q4_fg_pts")),
        ("  Q4 points from FREE THROWS", per(r, "q4_ft_pts"), per(s, "q4_ft_pts")),
        ("Q4 made FG (freq)", per(r, "q4_fg"), per(s, "q4_fg")),
    ]
    for name, rv, sv in rows:
        print(f"  {name:<30}{rv:>9.2f}{sv:>9.2f}{sv-rv:>+9.2f}")
    print(f"\n  -> FT points explain {(per(r,'q4_ft_pts')-per(s,'q4_ft_pts')):+.2f} of the gap; "
          f"FG points {(per(s,'q4_fg_pts')-per(r,'q4_fg_pts')):+.2f}")
    print(f"\n  Q1 -> Q4 made-FG trend (does the sim over-elevate Q4 scoring vs its own Q1?):")
    print(f"    REAL made FG: Q1 {per(r,'q1_fg'):.2f} -> Q4 {per(r,'q4_fg'):.2f}  ({per(r,'q4_fg')-per(r,'q1_fg'):+.2f})")
    print(f"    SIM  made FG: Q1 {per(s,'q1_fg'):.2f} -> Q4 {per(s,'q4_fg'):.2f}  ({per(s,'q4_fg')-per(s,'q1_fg'):+.2f})")
    print(f"\n  SIM pace vs efficiency (Q1 vs Q4, close games):")
    q1fga, q4fga = per(s, "q1_fga"), per(s, "q4_fga")
    print(f"    FGA : Q1 {q1fga:.1f} -> Q4 {q4fga:.1f}  ({q4fga-q1fga:+.1f})")
    print(f"    FG% : Q1 {s['q1_fg']/s['q1_fga']*100:.1f}% -> Q4 {s['q4_fg']/s['q4_fga']*100:.1f}%")
    print(f"    possessions: Q1 {per(s,'q1_poss'):.1f} -> Q4 {per(s,'q4_poss'):.1f}  ({per(s,'q4_poss')-per(s,'q1_poss'):+.1f})")
    print()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2024-25")
    p.add_argument("--sims-per-game", type=int, default=3)
    a = p.parse_args()
    main(a.season, a.sims_per_game)
