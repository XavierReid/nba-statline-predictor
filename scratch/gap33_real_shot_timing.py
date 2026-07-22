"""Gap 3.3 mechanism-2 calibration — when do real teams take the decisive late shot?

From real made-only PBP (game_scoring_events), find Q4 made field goals where the
scoring side was TRAILING by 1-3 before the shot (a tying or go-ahead score) and
report the distribution of seconds_remaining. This calibrates the "hold for the
last shot" window: real teams convert these decisive shots at LOW clock (they hold
so a make leaves no time to answer). The sim currently converts them at ~15s.

Usage: python scratch/gap33_real_shot_timing.py --season 2024-25
"""
import argparse
import os
import sys
from statistics import median

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models.game import Game
from app.models.scoring_event import ScoringEvent
from sqlalchemy import select


def main(season):
    db = SessionLocal()
    year = season.split("-")[0][-2:]
    gids = [g.id for g in db.execute(
        select(Game).where(Game.id.like(f"002{year}%"), Game.status == "final")
    ).scalars().all()]

    # decisive = scoring side was trailing by 1-3 before this FG (tying or go-ahead)
    clocks = []          # seconds_remaining of decisive late FGs
    clocks_by_def = {1: [], 2: [], 3: []}
    for gid in gids:
        evs = db.execute(
            select(ScoringEvent).where(
                ScoringEvent.game_id == gid, ScoringEvent.period == 4
            ).order_by(ScoringEvent.event_num)
        ).scalars().all()
        for e in evs:
            if e.points not in (2, 3):
                continue
            if e.scoring_side == "home":
                pre_off, pre_def = e.home_score - e.points, e.away_score
            else:
                pre_off, pre_def = e.away_score - e.points, e.home_score
            deficit = pre_def - pre_off
            if 1 <= deficit <= 3 and e.seconds_remaining <= 35:
                clocks.append(e.seconds_remaining)
                clocks_by_def[deficit].append(e.seconds_remaining)
    db.close()

    def pct(xs, p):
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(p / 100 * len(xs)))]

    print(f"\n{'='*58}\n  Real decisive late FGs (trailing 1-3, Q4, <=35s): {season}\n{'='*58}")
    print(f"  total decisive FGs: {len(clocks)}")
    print(f"  seconds_remaining: median={median(clocks):.0f}  p25={pct(clocks,25)}  p75={pct(clocks,75)}  p90={pct(clocks,90)}")
    print(f"  share shot with <=8s left : {sum(1 for c in clocks if c<=8)/len(clocks)*100:.0f}%")
    print(f"  share shot with <=5s left : {sum(1 for c in clocks if c<=5)/len(clocks)*100:.0f}%")
    print(f"  share shot with >12s left : {sum(1 for c in clocks if c>12)/len(clocks)*100:.0f}%")
    print(f"\n  {'deficit':>7} {'n':>4} {'median':>7} {'p75':>5} {'<=8s%':>6}")
    for d in (1, 2, 3):
        cs = clocks_by_def[d]
        if cs:
            print(f"  {d:>7} {len(cs):>4} {median(cs):>7.0f} {pct(cs,75):>5} {sum(1 for c in cs if c<=8)/len(cs)*100:>5.0f}%")
    # coarse histogram
    print(f"\n  clock histogram (decisive FGs):")
    bins = [(0,3),(4,6),(7,9),(10,14),(15,20),(21,35)]
    for lo,hi in bins:
        n = sum(1 for c in clocks if lo<=c<=hi)
        print(f"    {lo:>2}-{hi:<2}s: {n:>4}  {'#'*int(n/max(1,len(clocks))*60)}")
    print()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2024-25")
    a = p.parse_args()
    main(a.season)
