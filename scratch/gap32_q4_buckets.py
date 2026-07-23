"""Gap 3.2 placement — time-bucketed Q4 scoring profile for close games.

Locate the ONSET of real's clutch de-scoring: does real diverge from sim across
the WHOLE Q4, only from the final ~4-6 min, or only in the last minute? That
decides whether the missing behavior lives inside COMPETITIVE_LATE (final 2 min),
extends to ~4-6 min, or is a broad close-Q4 mechanism.

Buckets by seconds remaining in Q4: 12-9, 9-6, 6-3, 3-1, 1-0 min.
Comparable real<->sim (real PBP is made-only): made FG, points, |margin| transition.
Sim-only (no real FGA/poss in DB): possessions, FGA, FG% — show where the sim
internally fails to compress.
Close games only: entering-Q4 |margin| <= 5 (real from line scores; sim from its
own Q3 margin).

Usage: python scratch/gap32_q4_buckets.py --season 2024-25 --sims-per-game 3
"""
import argparse
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models.game import Game
from app.models.scoring_event import ScoringEvent
from app.models.team import Team
from app.services.game_simulator import simulate_game
from app.services.roster import load_roster
from app.services.sim_config import DRAMA_M3
from sqlalchemy import select

BOUNDS = [720, 540, 360, 180, 60, 0]      # bucket edges (seconds remaining)
LABELS = ["12-9", "9-6", "6-3", "3-1", "1-0"]


def bucket_idx(sec):
    for i in range(len(BOUNDS) - 1):
        if BOUNDS[i] >= sec > BOUNDS[i + 1]:
            return i
    return len(BOUNDS) - 2 if sec == 0 else None


def entering_q4_margin_real(g):
    h = (g.home_q1 or 0) + (g.home_q2 or 0) + (g.home_q3 or 0)
    a = (g.away_q1 or 0) + (g.away_q2 or 0) + (g.away_q3 or 0)
    return h - a


def new_acc():
    return {"madeFG": [0]*5, "pts": [0]*5, "fga": [0]*5, "poss": [0]*5,
            "absm_at": {b: [] for b in BOUNDS}}


def snap_absm(acc, running_margin_by_bound):
    for b in BOUNDS:
        acc["absm_at"][b].append(abs(running_margin_by_bound[b]))


def main(season, spg):
    db = SessionLocal()
    year = season.split("-")[0][-2:]
    games = db.execute(select(Game).where(
        Game.id.like(f"002{year}%"), Game.status == "final", Game.home_q4.isnot(None)
    )).scalars().all()

    R = new_acc(); S = new_acc()
    rn = sn = 0

    # ---- REAL ----
    for g in games:
        eqm = entering_q4_margin_real(g)
        if abs(eqm) > 5:
            continue
        evs = db.execute(select(ScoringEvent).where(
            ScoringEvent.game_id == g.id, ScoringEvent.period == 4
        ).order_by(ScoringEvent.event_num)).scalars().all()
        rn += 1
        # margin at each boundary (running score; start-of-Q4 boundary 720 = entering margin)
        by_bound = {b: eqm for b in BOUNDS}
        for e in evs:
            m = e.home_score - e.away_score
            for b in BOUNDS:
                if e.seconds_remaining >= b:
                    by_bound[b] = m
            bi = bucket_idx(e.seconds_remaining)
            if bi is not None:
                R["pts"][bi] += e.points
                if e.points in (2, 3):
                    R["madeFG"][bi] += 1
        snap_absm(R, by_bound)

    # ---- SIM ----
    teams = db.execute(select(Team)).scalars().all()
    ros = {t.id: load_roster(db, t.id, season) for t in teams}
    ros = {k: v for k, v in ros.items() if v}
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
            qs = res["quarter_scores"]
            eqm_s = sum(qs["home"][:3]) - sum(qs["away"][:3])
            if abs(eqm_s) > 5:
                continue
            sn += 1
            hs = as_ = 0
            # replay to Q4 start to get entering margin, then track Q4
            hpre = sum(qs["home"][:3]); apre = sum(qs["away"][:3])
            by_bound = {b: (hpre - apre) for b in BOUNDS}
            hs, as_ = hpre, apre
            for ev in res["events"]:
                if ev["quarter"] != 4:
                    continue
                clk = ev["game_clock_seconds"]
                bi = bucket_idx(clk)
                if bi is not None:
                    S["poss"][bi] += 1
                    if ev.get("shot_type") is not None:
                        S["fga"][bi] += 1
                        if ev.get("made"):
                            S["madeFG"][bi] += 1
                    S["pts"][bi] += ev["pts"]
                hs += ev["pts"] if ev["is_home"] else 0
                as_ += 0 if ev["is_home"] else ev["pts"]
                for b in BOUNDS:
                    if clk >= b:
                        by_bound[b] = hs - as_
            snap_absm(S, by_bound)
    db.close()

    from statistics import mean
    def avg(lst, n): return lst / n if n else 0
    def absm(acc, b): return mean(acc["absm_at"][b]) if acc["absm_at"][b] else 0

    print(f"\n{'='*74}\n  Gap 3.2 Q4 time-buckets, close games (enter |m|<=5): {season}  (r={rn} s={sn})\n{'='*74}")
    print(f"\n  COMPARABLE (real made-only): made FG / points per game, by bucket")
    print(f"  {'bucket':<7}{'R madeFG':>9}{'S madeFG':>9}{'diff':>7}   {'R pts':>7}{'S pts':>7}{'diff':>7}")
    for i, lab in enumerate(LABELS):
        rm, sm = avg(R["madeFG"][i], rn), avg(S["madeFG"][i], sn)
        rp, sp = avg(R["pts"][i], rn), avg(S["pts"][i], sn)
        print(f"  {lab:<7}{rm:>9.2f}{sm:>9.2f}{sm-rm:>+7.2f}   {rp:>7.2f}{sp:>7.2f}{sp-rp:>+7.2f}")

    print(f"\n  SIM-ONLY pace/efficiency by bucket:")
    print(f"  {'bucket':<7}{'poss':>7}{'FGA':>7}{'FG%':>7}")
    for i, lab in enumerate(LABELS):
        p, f, m = avg(S["poss"][i], sn), avg(S["fga"][i], sn), S["madeFG"][i]
        fgpct = (m / S["fga"][i] * 100) if S["fga"][i] else 0
        print(f"  {lab:<7}{p:>7.2f}{f:>7.2f}{fgpct:>6.1f}%")

    print(f"\n  MARGIN transition |margin| at each boundary (compression check):")
    print(f"  {'@min':<7}{'R |m|':>7}{'S |m|':>7}{'diff':>7}")
    names = {720:"12", 540:"9", 360:"6", 180:"3", 60:"1", 0:"0"}
    for b in BOUNDS:
        print(f"  {names[b]:<7}{absm(R,b):>7.2f}{absm(S,b):>7.2f}{absm(S,b)-absm(R,b):>+7.2f}")
    print(f"\n  per-bucket Δ|margin| (growth within bucket):")
    print(f"  {'bucket':<7}{'R Δ':>7}{'S Δ':>7}{'diff':>7}")
    for i, lab in enumerate(LABELS):
        rd = absm(R, BOUNDS[i+1]) - absm(R, BOUNDS[i])
        sd = absm(S, BOUNDS[i+1]) - absm(S, BOUNDS[i])
        print(f"  {lab:<7}{rd:>+7.2f}{sd:>+7.2f}{sd-rd:>+7.2f}")
    print()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2024-25")
    p.add_argument("--sims-per-game", type=int, default=3)
    a = p.parse_args()
    main(a.season, a.sims_per_game)
