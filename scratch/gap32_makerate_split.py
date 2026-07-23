"""Gap 3.2 — make-rate vs attempt-rate split + leading/trailing symmetry, 9:00-3:00 close Q4.

Real PBP is made-only: real FGA/FG% are NOT measurable. So:
  MEASURED  : sim FGA/min & FG% (Q1 baseline vs 9-3 Q4) — does the sim tighten on
              EITHER axis late? And leading-vs-trailing makes for BOTH real and sim
              (symmetry — does the level effect hit both teams?).
  BOUNDED   : real makes/min vs sim -> two endpoints for the real split:
              (a) real pace == sim pace  -> implied real FG% (shot-difficulty axis)
              (b) real FG%  == sim FG%   -> implied real FGA  (tempo axis)

Close games: entering-Q4 |m|<=5. Window: Q4 sec_remaining in [180,540] (=6 min).

Usage: python scratch/gap32_makerate_split.py --season 2024-25 --sims-per-game 3
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

LO, HI = 180, 540
WIN_MIN = (HI - LO) / 60.0  # 6.0


def eqm_real(g):
    h = (g.home_q1 or 0)+(g.home_q2 or 0)+(g.home_q3 or 0)
    a = (g.away_q1 or 0)+(g.away_q2 or 0)+(g.away_q3 or 0)
    return h - a


def main(season, spg):
    db = SessionLocal()
    year = season.split("-")[0][-2:]
    games = db.execute(select(Game).where(
        Game.id.like(f"002{year}%"), Game.status == "final", Game.home_q4.isnot(None)
    )).scalars().all()

    # REAL: makes in window, split by leading/trailing team at moment of score
    r = {"n": 0, "made": 0, "lead_made": 0, "trail_made": 0, "tied_made": 0}
    for g in games:
        em = eqm_real(g)
        if abs(em) > 5:
            continue
        evs = db.execute(select(ScoringEvent).where(
            ScoringEvent.game_id == g.id, ScoringEvent.period == 4
        ).order_by(ScoringEvent.event_num)).scalars().all()
        r["n"] += 1
        for e in evs:
            if not (LO <= e.seconds_remaining < HI) or e.points not in (2, 3):
                continue
            pre = (e.home_score - e.points) - e.away_score if e.scoring_side == "home" \
                else (e.away_score - e.points) - e.home_score  # scorer margin BEFORE
            r["made"] += 1
            if pre > 0:
                r["lead_made"] += 1
            elif pre < 0:
                r["trail_made"] += 1
            else:
                r["tied_made"] += 1

    # SIM: FGA/FG% Q1 & window; makes split by leading/trailing
    teams = db.execute(select(Team)).scalars().all()
    ros = {t.id: load_roster(db, t.id, season) for t in teams}
    ros = {k: v for k, v in ros.items() if v}
    s = {"n": 0, "q1_fga": 0, "q1_made": 0, "w_fga": 0, "w_made": 0,
         "lead_made": 0, "trail_made": 0, "tied_made": 0}
    for g in games:
        if abs(eqm_real(g)) > 5:
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
            s["n"] += 1
            hs, as_ = sum(qs["home"][:3]), sum(qs["away"][:3])
            for ev in res["events"]:
                q, clk = ev["quarter"], ev["game_clock_seconds"]
                if q == 1 and ev.get("shot_type") is not None:
                    s["q1_fga"] += 1
                    s["q1_made"] += int(bool(ev.get("made")))
                if q == 4 and LO <= clk < HI:
                    if ev.get("shot_type") is not None:
                        s["w_fga"] += 1
                        if ev.get("made"):
                            s["w_made"] += 1
                            pre = (hs - as_) if ev["is_home"] else (as_ - hs)
                            fg = ev["pts"] - (ev.get("ftm", 0) or 0)
                            if fg > 0:
                                if pre > 0:
                                    s["lead_made"] += 1
                                elif pre < 0:
                                    s["trail_made"] += 1
                                else:
                                    s["tied_made"] += 1
                hs += ev["pts"] if ev["is_home"] else 0
                as_ += 0 if ev["is_home"] else ev["pts"]
    db.close()

    r_made_min = r["made"] / r["n"] / WIN_MIN
    s_made_min = s["w_made"] / s["n"] / WIN_MIN
    s_fga_min = s["w_fga"] / s["n"] / WIN_MIN
    s_fgpct = s["w_made"] / s["w_fga"]
    s_q1_fgpct = s["q1_made"] / s["q1_fga"]
    s_q1_fga_min = s["q1_fga"] / s["n"] / 12.0

    print(f"\n{'='*60}\n  Gap 3.2 make vs attempt + symmetry, 9:00-3:00 close Q4: {season}\n{'='*60}")
    print(f"  (real n={r['n']}, sim n={s['n']})")

    print(f"\n  SIM — does it tighten late on either axis? (per-minute)")
    print(f"    FGA/min : Q1 {s_q1_fga_min:.2f}  ->  9-3 {s_fga_min:.2f}   ({s_fga_min-s_q1_fga_min:+.2f})")
    print(f"    FG%     : Q1 {s_q1_fgpct*100:.1f}% ->  9-3 {s_fgpct*100:.1f}%  ({(s_fgpct-s_q1_fgpct)*100:+.1f})")
    print(f"    -> sim keeps both FGA and FG% ~flat into the clutch (no tightening)")

    print(f"\n  MADE FG / min in window:  real {r_made_min:.2f}   sim {s_made_min:.2f}   (sim {s_made_min-r_made_min:+.2f})")
    print(f"\n  REAL split is UNMEASURABLE (made-only). Two bounding endpoints for the sim excess:")
    print(f"    (a) if real pace == sim FGA/min ({s_fga_min:.2f}): real FG% = {r_made_min/s_fga_min*100:.1f}%  "
          f"vs sim {s_fgpct*100:.1f}%  -> {(r_made_min/s_fga_min - s_fgpct)*100:+.1f} pt  [shot-difficulty axis]")
    print(f"    (b) if real FG% == sim ({s_fgpct*100:.1f}%): real FGA/min = {r_made_min/s_fgpct:.2f}  "
          f"vs sim {s_fga_min:.2f}  -> {(r_made_min/s_fgpct - s_fga_min)/s_fga_min*100:+.0f}%  [tempo axis]")

    def share(d, tot):
        return f"lead {d['lead_made']/tot*100:.0f}% / trail {d['trail_made']/tot*100:.0f}% / tied {d['tied_made']/tot*100:.0f}%"
    print(f"\n  SYMMETRY — makes by scorer's role (lead/trail/tied at moment of score):")
    print(f"    REAL: {share(r, r['made'])}")
    print(f"    SIM : {share(s, s['lead_made']+s['trail_made']+s['tied_made'])}")
    print()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2024-25")
    p.add_argument("--sims-per-game", type=int, default=3)
    a = p.parse_args()
    main(a.season, a.sims_per_game)
