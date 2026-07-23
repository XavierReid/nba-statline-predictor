"""Gap 3.2 — run/trade structure in the 9:00-3:00 window of close games.

Onset located at ~9:00. Now characterize WHAT changes: is real lower-VARIANCE
(more alternating scores, shorter runs, tighter margin walk) or lower-LEVEL
(just fewer points), or both? NET already matches, so it's not a one-team bias.

Window: Q4 seconds_remaining in [180, 540]. Close games: entering-Q4 |m|<=5.
Comparable real<->sim (made-only PBP): scoring sequence -> run sizes, trade
(alternation) rate, lead changes, margin-transition variance, points (level).
Sim-only (no real possessions): per-possession point variance, empty-possession
streaks.

Usage: python scratch/gap32_run_trade.py --season 2024-25 --sims-per-game 3
"""
import argparse
import os
import sys
import zlib
from statistics import mean, pvariance

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models.game import Game
from app.models.scoring_event import ScoringEvent
from app.models.team import Team
from app.services.game_simulator import simulate_game
from app.services.roster import load_roster
from app.services.sim_config import DRAMA_M3
from sqlalchemy import select

LO, HI = 180, 540  # 3:00 .. 9:00 remaining


def entering_q4_margin_real(g):
    h = (g.home_q1 or 0) + (g.home_q2 or 0) + (g.home_q3 or 0)
    a = (g.away_q1 or 0) + (g.away_q2 or 0) + (g.away_q3 or 0)
    return h - a


def seq_metrics(scores, m_start):
    """scores: ordered list of (side, points) in window. m_start: home-away margin
    entering the window. Returns per-game metrics."""
    if not scores:
        return None
    sides = [s for s, _ in scores]
    # trade (alternation) rate: adjacent scoring events by opposite teams
    if len(sides) > 1:
        trades = sum(1 for i in range(1, len(sides)) if sides[i] != sides[i-1])
        trade_rate = trades / (len(sides) - 1)
    else:
        trade_rate = None
    # runs in POINTS: group consecutive same-side, sum points
    runs = []
    cur_side, cur_pts = sides[0], scores[0][1]
    for s, p in scores[1:]:
        if s == cur_side:
            cur_pts += p
        else:
            runs.append(cur_pts)
            cur_side, cur_pts = s, p
    runs.append(cur_pts)
    # lead changes within window + signed margin change
    m = m_start
    lead_changes = 0
    prev_sign = (m > 0) - (m < 0)
    for s, p in scores:
        m += p if s == "home" else -p
        sign = (m > 0) - (m < 0)
        if sign != 0 and prev_sign != 0 and sign != prev_sign:
            lead_changes += 1
        if sign != 0:
            prev_sign = sign
    return {
        "trade_rate": trade_rate,
        "runs": runs,
        "lead_changes": lead_changes,
        "signed_dm": m - m_start,     # signed margin change over window
        "pts": sum(p for _, p in scores),
    }


def agg(label, per_games):
    valid = [g for g in per_games if g is not None]
    n = len(valid)
    all_runs = [r for g in valid for r in g["runs"]]
    tr = [g["trade_rate"] for g in valid if g["trade_rate"] is not None]
    print(f"\n  {label} (n={n})")
    print(f"    points in window          : {mean(g['pts'] for g in valid):5.2f}")
    print(f"    trade (alternation) rate  : {mean(tr):5.3f}")
    print(f"    mean run size (pts)       : {mean(all_runs):5.2f}")
    print(f"    runs >= 6 pts (share)     : {sum(1 for r in all_runs if r>=6)/len(all_runs)*100:5.1f}%")
    print(f"    lead changes / game       : {mean(g['lead_changes'] for g in valid):5.2f}")
    print(f"    margin-transition VAR     : {pvariance([g['signed_dm'] for g in valid]):6.2f}  "
          f"(signed Δmargin over window)")
    return valid


def main(season, spg):
    db = SessionLocal()
    year = season.split("-")[0][-2:]
    games = db.execute(select(Game).where(
        Game.id.like(f"002{year}%"), Game.status == "final", Game.home_q4.isnot(None)
    )).scalars().all()

    # REAL
    real_pg = []
    for g in games:
        eqm = entering_q4_margin_real(g)
        if abs(eqm) > 5:
            continue
        evs = db.execute(select(ScoringEvent).where(
            ScoringEvent.game_id == g.id, ScoringEvent.period == 4
        ).order_by(ScoringEvent.event_num)).scalars().all()
        # margin entering the window (at 9:00) = running score at last event with sec>=HI, else eqm
        m_start = eqm
        window = []
        for e in evs:
            if e.seconds_remaining >= HI:
                m_start = e.home_score - e.away_score
            if LO <= e.seconds_remaining < HI and e.points > 0:
                window.append((e.scoring_side, e.points))
        real_pg.append(seq_metrics(window, m_start))

    # SIM
    teams = db.execute(select(Team)).scalars().all()
    ros = {t.id: load_roster(db, t.id, season) for t in teams}
    ros = {k: v for k, v in ros.items() if v}
    sim_pg = []
    poss_pts_all = []       # sim-only: per-possession points in window
    empty_streaks = []      # sim-only: consecutive empty (0-pt) possessions
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
            if abs(sum(qs["home"][:3]) - sum(qs["away"][:3])) > 5:
                continue
            hpre, apre = sum(qs["home"][:3]), sum(qs["away"][:3])
            m_start = hpre - apre
            hs, as_ = hpre, apre
            window = []
            cur_empty = 0
            for ev in res["events"]:
                if ev["quarter"] != 4:
                    continue
                clk = ev["game_clock_seconds"]
                if clk >= HI:
                    m_start = hs - as_
                if LO <= clk < HI:
                    poss_pts_all.append(ev["pts"])
                    if ev["pts"] == 0:
                        cur_empty += 1
                    else:
                        if cur_empty:
                            empty_streaks.append(cur_empty)
                        cur_empty = 0
                        window.append(("home" if ev["is_home"] else "away", ev["pts"]))
                hs += ev["pts"] if ev["is_home"] else 0
                as_ += 0 if ev["is_home"] else ev["pts"]
            sim_pg.append(seq_metrics(window, m_start))
    db.close()

    print(f"\n{'='*60}\n  Gap 3.2 run/trade structure, 9:00-3:00, close games: {season}\n{'='*60}")
    agg("REAL", real_pg)
    agg("SIM ", sim_pg)
    print(f"\n  SIM-ONLY (no real possessions):")
    print(f"    per-possession point VAR  : {pvariance(poss_pts_all):5.3f}  (mean {mean(poss_pts_all):.3f}, n={len(poss_pts_all)})")
    print(f"    mean empty-poss streak    : {mean(empty_streaks):5.2f}  (n streaks={len(empty_streaks)})")
    print()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2024-25")
    p.add_argument("--sims-per-game", type=int, default=3)
    a = p.parse_args()
    main(a.season, a.sims_per_game)
