"""Gap 3.3 survival owner cut (b) — the answer possession after a late tie.

Hypothesis (from the make-rate cut): the sim's tied-game last shot is
under-contested and rim-easy, so the offense holding the ball when the game is
tied SCORES too often -> it WINS instead of the game reaching OT. Real defenses
collapse on the known final shot, so it misses more -> OT.

Measure sim possessions that START tied in Q4 with clock<=window (the "answer"):
  make%, contested%, sub_type mix, P(possession scores), P(takes the lead),
  and P(OT) among games that reach a tied answer possession.
Anchor: real clutch last-shot make ~0.40-0.45 (2), ~0.30 (3); a tied game with
one possession left goes to OT ~50-60% of the time (the shot usually misses).

Usage: python scratch/gap33_answer_possession.py --season 2024-25 --sims-per-game 4 --window 8
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

THREES = {"corner_three", "above_break_three", "three"}


def main(season, sims_per_game, window):
    db = SessionLocal()
    year = season.split("-")[0][-2:]
    games = db.execute(select(Game).where(
        Game.id.like(f"002{year}%"), Game.status == "final", Game.home_q4.isnot(None)
    )).scalars().all()
    teams = db.execute(select(Team)).scalars().all()
    ros = {t.id: load_roster(db, t.id, season) for t in teams}
    ros = {k: v for k, v in ros.items() if v}

    n_att = n_make = n_contest = n_score = n_lead = 0
    sub = Counter()
    # per-game: reached a tied answer possession (<=window)? -> did it go to OT?
    games_reached = games_ot = 0

    for g in games:
        if g.home_team_id not in ros or g.away_team_id not in ros:
            continue
        for k in range(sims_per_game):
            r = simulate_game(ros[g.home_team_id], ros[g.away_team_id],
                              seed=zlib.crc32(str(g.id).encode()) + k, season=season,
                              config=DRAMA_M3, home_team_id=g.home_team_id,
                              away_team_id=g.away_team_id, db=db, capture_descriptions=True)
            hs = as_ = 0
            reached = False
            for ev in r["events"]:
                if ev["quarter"] >= 4 and hs == as_ and ev["game_clock_seconds"] <= window:
                    reached = True
                    st = ev.get("shot_type")
                    pts = ev["pts"]
                    if pts > 0:
                        n_score += 1
                        n_lead += 1  # scoring while tied always takes the lead
                    if st is not None:
                        n_att += 1
                        n_make += int(bool(ev.get("made")))
                        n_contest += int(bool(ev.get("contested")))
                        sub[st] += 1
                hs += ev["pts"] if ev["is_home"] else 0
                as_ += 0 if ev["is_home"] else ev["pts"]
            if reached:
                games_reached += 1
                games_ot += int(r["went_to_ot"])
    db.close()

    print(f"\n{'='*60}\n  Gap 3.3 answer possession (tied, Q4, <={window}s): {season}\n{'='*60}")
    print(f"\n  tied answer possessions: n={n_att} FG attempts")
    if n_att:
        print(f"    make%      : {n_make/n_att*100:5.1f}%   (real clutch last shot ~40-45% for 2s, ~30% for 3s)")
        print(f"    contested% : {n_contest/n_att*100:5.1f}%   (real: defense collapses on known last shot, ~70%+)")
        threes = sum(v for k, v in sub.items() if k in THREES)
        rim = sum(v for k, v in sub.items() if k in ("layup", "dunk"))
        print(f"    3-share    : {threes/n_att*100:5.1f}%    rim-share: {rim/n_att*100:5.1f}%")
        print(f"    sub_type   : " + "  ".join(f"{k}:{v/n_att*100:.0f}%" for k, v in sub.most_common()))
    print(f"\n  games reaching a tied answer possession: {games_reached}")
    if games_reached:
        print(f"    -> went to OT: {games_ot} = {games_ot/games_reached*100:.0f}%   (real expectation ~50-60%)")
    print()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2024-25")
    p.add_argument("--sims-per-game", type=int, default=4)
    p.add_argument("--window", type=int, default=8)
    a = p.parse_args()
    main(a.season, a.sims_per_game, a.window)
