"""Gap 3.3 reach/instability half — margin transition 24s -> 8s in Q4.

The reach cut showed the sim DIPS at exactly 0 at 8s (2.2% vs real 4.7%) — tied
states are unstable/under-produced. Decompose:
  UPSTREAM reach : P(state at 24s)        — does the sim get close by 24s?
  STABILITY      : P(|m|<=1 at 8s | state at 24s) — does it HOLD close/tie to 8s,
                   or does easy scoring blow it open?

Real reconstructed from game_scoring_events running score (exact; misses don't
move score) at 24s and 8s remaining. Sim from events.

Usage: python scratch/gap33_reach_transition.py --season 2024-25 --sims-per-game 4
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


def bucket(m):
    a = abs(m)
    if a == 0:
        return "tied"
    if a <= 3:
        return "1-3"
    if a <= 6:
        return "4-6"
    return "7+"


def real_pairs(season):
    db = SessionLocal()
    year = season.split("-")[0][-2:]
    games = db.execute(select(Game).where(
        Game.id.like(f"002{year}%"), Game.status == "final", Game.home_q4.isnot(None)
    )).scalars().all()
    pairs = []  # (margin@24, margin@8)
    for g in games:
        evs = db.execute(select(ScoringEvent).where(
            ScoringEvent.game_id == g.id, ScoringEvent.period == 4
        ).order_by(ScoringEvent.event_num)).scalars().all()
        m24 = m8 = 0
        for e in evs:
            if e.seconds_remaining >= 24:
                m24 = e.home_score - e.away_score
            if e.seconds_remaining >= 8:
                m8 = e.home_score - e.away_score
        pairs.append((m24, m8))
    db.close()
    return pairs


def sim_pairs(season, spg):
    db = SessionLocal()
    year = season.split("-")[0][-2:]
    games = db.execute(select(Game).where(
        Game.id.like(f"002{year}%"), Game.status == "final", Game.home_q4.isnot(None)
    )).scalars().all()
    teams = db.execute(select(Team)).scalars().all()
    ros = {t.id: load_roster(db, t.id, season) for t in teams}
    ros = {k: v for k, v in ros.items() if v}
    pairs = []
    for g in games:
        if g.home_team_id not in ros or g.away_team_id not in ros:
            continue
        for k in range(spg):
            r = simulate_game(ros[g.home_team_id], ros[g.away_team_id],
                              seed=zlib.crc32(str(g.id).encode()) + k, season=season,
                              config=DRAMA_M3, home_team_id=g.home_team_id,
                              away_team_id=g.away_team_id, db=db, capture_descriptions=True)
            hs = as_ = 0
            m24 = m8 = 0
            for ev in r["events"]:
                if ev["quarter"] == 4:
                    if ev["game_clock_seconds"] >= 24:
                        m24 = hs - as_
                    if ev["game_clock_seconds"] >= 8:
                        m8 = hs - as_
                hs += ev["pts"] if ev["is_home"] else 0
                as_ += 0 if ev["is_home"] else ev["pts"]
            pairs.append((m24, m8))
    db.close()
    return pairs


def report(label, pairs):
    n = len(pairs)
    print(f"\n  {label} (n={n})")
    # upstream reach at 24s
    for b in ("tied", "1-3", "4-6", "7+"):
        cnt = sum(1 for m24, _ in pairs if bucket(m24) == b)
        print(f"    @24s {b:>5}: {cnt/n*100:5.1f}%", end="")
        # stability: of these, P(|m8|<=1) and P(m8==0)
        sub = [(m24, m8) for m24, m8 in pairs if bucket(m24) == b]
        if sub:
            tie8 = sum(1 for _, m8 in sub if m8 == 0) / len(sub) * 100
            close8 = sum(1 for _, m8 in sub if abs(m8) <= 1) / len(sub) * 100
            print(f"   -> @8s tied {tie8:4.1f}%  |m8|<=1 {close8:4.1f}%")
        else:
            print()


def main(season, spg):
    rp = real_pairs(season)
    sp = sim_pairs(season, spg)
    print(f"\n{'='*62}\n  Gap 3.3 reach/stability: margin 24s -> 8s (Q4) — {season}\n{'='*62}")
    report("REAL", rp)
    report("SIM ", sp)
    print()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2024-25")
    p.add_argument("--sims-per-game", type=int, default=4)
    a = p.parse_args()
    main(a.season, a.sims_per_game)
