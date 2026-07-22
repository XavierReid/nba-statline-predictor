"""Gap 3.3 reach->convert decomposition — live margin entering the final ~8s.

The earlier instrument measured margin at the FINAL BUZZER (end-of-regulation),
which counts games decided earlier. This measures the LIVE state entering the
final decision window: |margin| with ~8s remaining in Q4, and P(OT | that state).

  REACH   = P(one-score state at 8s | game)      -> is the sim arriving at a
                                                     last-possession-decides state
                                                     as often as real?
  CONVERT = P(OT | margin at 8s = m)              -> from a given close state, how
                                                     often does it become OT?

Real: reconstruct Q4 running score from game_scoring_events (made-only, exact —
misses don't change score) to get score at 8s; OT flag from line scores
(end-of-regulation margin == 0). Both exist for 2024-25.
Sim: margin at 8s from events; who has the ball (offense of the possession live
at 8s); OT flag from went_to_ot.

Usage: python scratch/gap33_reach.py --season 2024-25 --sims-per-game 4 --at 8
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


def real_reach(season, at):
    db = SessionLocal()
    year = season.split("-")[0][-2:]
    games = db.execute(select(Game).where(
        Game.id.like(f"002{year}%"), Game.status == "final", Game.home_q4.isnot(None)
    )).scalars().all()
    margins = []          # |margin| at ~`at`s remaining
    ot_by_absm = defaultdict(lambda: [0, 0])   # |margin| -> [ot, total]
    for g in games:
        end_reg = ((g.home_q1 or 0)+(g.home_q2 or 0)+(g.home_q3 or 0)+(g.home_q4 or 0)) - \
                  ((g.away_q1 or 0)+(g.away_q2 or 0)+(g.away_q3 or 0)+(g.away_q4 or 0))
        went_ot = (end_reg == 0)
        evs = db.execute(select(ScoringEvent).where(
            ScoringEvent.game_id == g.id, ScoringEvent.period == 4
        ).order_by(ScoringEvent.event_num)).scalars().all()
        # score at `at`s remaining = running score after the last make with sec_rem >= at
        hs = as_ = None
        for e in evs:
            if e.seconds_remaining >= at:
                hs, as_ = e.home_score, e.away_score
        if hs is None:
            # no Q4 scoring before the window (rare) — fall back to end of Q3 = 0/0 tie state
            hs = as_ = 0
        m = abs(hs - as_)
        margins.append(m)
        ot_by_absm[min(m, 6)][0] += int(went_ot)
        ot_by_absm[min(m, 6)][1] += 1
    db.close()
    return margins, ot_by_absm, len(games)


def sim_reach(season, sims_per_game, at):
    db = SessionLocal()
    year = season.split("-")[0][-2:]
    games = db.execute(select(Game).where(
        Game.id.like(f"002{year}%"), Game.status == "final", Game.home_q4.isnot(None)
    )).scalars().all()
    teams = db.execute(select(Team)).scalars().all()
    ros = {t.id: load_roster(db, t.id, season) for t in teams}
    ros = {k: v for k, v in ros.items() if v}
    margins = []
    ot_by_absm = defaultdict(lambda: [0, 0])
    ball_trailing = 0   # of one-score (1-3) states, offense is the TRAILING team
    one_score_n = 0
    n = 0
    for g in games:
        if g.home_team_id not in ros or g.away_team_id not in ros:
            continue
        for k in range(sims_per_game):
            r = simulate_game(ros[g.home_team_id], ros[g.away_team_id],
                              seed=zlib.crc32(str(g.id).encode()) + k, season=season,
                              config=DRAMA_M3, home_team_id=g.home_team_id,
                              away_team_id=g.away_team_id, db=db, capture_descriptions=True)
            qs = r["quarter_scores"]
            went_ot = sum(qs["home"][:4]) == sum(qs["away"][:4])
            hs = as_ = 0
            snap = None  # (margin_home, offense_is_home) live at `at`s
            for ev in r["events"]:
                if ev["quarter"] == 4 and ev["game_clock_seconds"] >= at:
                    snap = (hs - as_, ev["is_home"])
                hs += ev["pts"] if ev["is_home"] else 0
                as_ += 0 if ev["is_home"] else ev["pts"]
            if snap is None:
                snap = (0, True)
            margin_home, off_is_home = snap
            m = abs(margin_home)
            margins.append(m)
            ot_by_absm[min(m, 6)][0] += int(went_ot)
            ot_by_absm[min(m, 6)][1] += 1
            if 1 <= m <= 3:
                one_score_n += 1
                off_trailing = (margin_home < 0) == off_is_home
                ball_trailing += int(off_trailing)
            n += 1
    db.close()
    return margins, ot_by_absm, n, one_score_n, ball_trailing


def report(label, margins, ot, denom):
    n = len(margins)
    print(f"\n  {label} (n={n})")
    tie = sum(1 for m in margins if m == 0) / n * 100
    le3 = sum(1 for m in margins if m <= 3) / n * 100
    le5 = sum(1 for m in margins if m <= 5) / n * 100
    print(f"    |margin|@window: ==0 {tie:.1f}%   <=3 {le3:.1f}%   <=5 {le5:.1f}%")
    print(f"    {'|m|':>4} {'reach%':>7} {'P(OT|m)':>8}")
    for am in range(0, 7):
        o, t = ot[am]
        rp = t / n * 100
        cp = o / t * 100 if t else 0
        tag = f"{am}" if am < 6 else "6+"
        print(f"    {tag:>4} {rp:>6.1f}% {cp:>7.1f}%")


def main(season, sims_per_game, at):
    rm, rot, rg = real_reach(season, at)
    sm, sot, sn, os_n, bt = sim_reach(season, sims_per_game, at)
    print(f"\n{'='*58}\n  Gap 3.3 REACH: live |margin| at {at}s remaining — {season}\n{'='*58}")
    report("REAL", rm, rot, rg)
    report("SIM ", sm, sot, sn)
    print(f"\n  SIM possession state @ {at}s (one-score 1-3): trailing team has ball "
          f"{bt}/{os_n} = {bt/os_n*100:.0f}%")
    print()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2024-25")
    p.add_argument("--sims-per-game", type=int, default=4)
    p.add_argument("--at", type=int, default=8, help="seconds remaining snapshot")
    a = p.parse_args()
    main(a.season, a.sims_per_game, a.at)
