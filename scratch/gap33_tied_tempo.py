"""Gap 3.3 mechanism-2 owner proof — tied-offense late-clock tempo, real vs sim.

Question: does a tied offense inside the final 24s DRAIN the clock (play for the
last shot) in reality, while the sim uses normal tempo? If so, the tied-game
milk omission is the proven owner of tie survival.

Data asymmetry: real PBP is made-only, so no possession DURATION — but the clock
when a tied offense SCORES (shot time) is directly comparable to the sim's. A
milking offense scores at low seconds_remaining.

  REAL: Q4 made FGs where the scoring side was TIED before the shot, clock<=24s
        -> distribution of seconds_remaining (shot time).
  SIM : Q4 possessions STARTING tied with start_clock<=24s -> consumed seconds,
        shot time (end clock), % ending with <6s left. Plus the made-FG shot-time
        subset for an apples-to-apples comparison with real.

Usage: python scratch/gap33_tied_tempo.py --season 2024-25 --sims-per-game 3
"""
import argparse
import os
import sys
import zlib
from statistics import mean, median

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


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100 * len(xs)))] if xs else 0


def hist(xs, label):
    print(f"    {label} (n={len(xs)}):")
    for lo, hi in [(0, 3), (4, 6), (7, 9), (10, 14), (15, 20), (21, 24)]:
        n = sum(1 for c in xs if lo <= c <= hi)
        bar = "#" * int(n / max(1, len(xs)) * 50)
        print(f"      {lo:>2}-{hi:<2}s: {n:>4}  {bar}")


def real_shot_times(season):
    db = SessionLocal()
    year = season.split("-")[0][-2:]
    gids = [g.id for g in db.execute(
        select(Game).where(Game.id.like(f"002{year}%"), Game.status == "final")
    ).scalars().all()]
    times = []
    for gid in gids:
        evs = db.execute(select(ScoringEvent).where(
            ScoringEvent.game_id == gid, ScoringEvent.period == 4
        ).order_by(ScoringEvent.event_num)).scalars().all()
        for e in evs:
            if e.points not in (2, 3):
                continue
            if e.scoring_side == "home":
                pre_off, pre_def = e.home_score - e.points, e.away_score
            else:
                pre_off, pre_def = e.away_score - e.points, e.home_score
            if pre_off == pre_def and e.seconds_remaining <= 24:  # tied before scoring
                times.append(e.seconds_remaining)
    db.close()
    return times


def sim_tied(season, sims_per_game):
    db = SessionLocal()
    year = season.split("-")[0][-2:]
    games = db.execute(select(Game).where(
        Game.id.like(f"002{year}%"), Game.status == "final", Game.home_q4.isnot(None)
    )).scalars().all()
    teams = db.execute(select(Team)).scalars().all()
    ros = {t.id: load_roster(db, t.id, season) for t in teams}
    ros = {k: v for k, v in ros.items() if v}

    consumed, end_clock, made_shot_time, start_clocks = [], [], [], []
    for g in games:
        if g.home_team_id not in ros or g.away_team_id not in ros:
            continue
        for k in range(sims_per_game):
            r = simulate_game(ros[g.home_team_id], ros[g.away_team_id],
                              seed=zlib.crc32(str(g.id).encode()) + k, season=season,
                              config=DRAMA_M3, home_team_id=g.home_team_id,
                              away_team_id=g.away_team_id, db=db, capture_descriptions=True)
            hs = as_ = 0
            prev_end = None  # end clock of previous Q4 possession
            for ev in r["events"]:
                if ev["quarter"] != 4:
                    hs += ev["pts"] if ev["is_home"] else 0
                    as_ += 0 if ev["is_home"] else ev["pts"]
                    prev_end = None
                    continue
                start_clk = prev_end if prev_end is not None else 720.0
                ec = ev["game_clock_seconds"]
                if hs == as_ and start_clk <= 24:  # possession STARTED tied, late
                    consumed.append(max(0.0, start_clk - ec))
                    end_clock.append(ec)
                    start_clocks.append(start_clk)
                    if ev.get("shot_type") is not None:
                        made_shot_time.append(ec)
                hs += ev["pts"] if ev["is_home"] else 0
                as_ += 0 if ev["is_home"] else ev["pts"]
                prev_end = ec
    db.close()
    return consumed, end_clock, made_shot_time, start_clocks


def main(season, sims_per_game):
    real = real_shot_times(season)
    consumed, end_clock, sim_shot, start_clocks = sim_tied(season, sims_per_game)

    print(f"\n{'='*60}\n  Gap 3.3 tied-offense late tempo (<=24s, Q4): {season}\n{'='*60}")

    print(f"\n  SIM tied-start possessions: n={len(end_clock)}")
    print(f"    avg seconds consumed     : {mean(consumed):.1f}   (median {median(consumed):.0f})")
    print(f"    shot/end clock median    : {median(end_clock):.0f}s   p25={pct(end_clock,25)} p75={pct(end_clock,75)}")
    print(f"    % ending with <6s left   : {sum(1 for c in end_clock if c<6)/len(end_clock)*100:.0f}%")
    print(f"    START clock median       : {median(start_clocks):.0f}s   p25={pct(start_clocks,25)} p75={pct(start_clocks,75)}   (when the tie is REACHED)")
    print(f"    % tied-start with >12s   : {sum(1 for c in start_clocks if c>12)/len(start_clocks)*100:.0f}%  (had real time to milk)")
    # for tied possessions that START with >12s, do they still drain? (isolates a milk DECISION)
    milkable = [(s, e) for s, e in zip(start_clocks, end_clock) if s > 12]
    if milkable:
        print(f"    of those >12s starts: median consumed={median([s-e for s,e in milkable]):.0f}s, median end={median([e for s,e in milkable]):.0f}s")

    print(f"\n  SHOT-TIME comparison (clock when a tied offense SCORES) — apples-to-apples:")
    print(f"    real median={median(real):.0f}s  <=6s={sum(1 for c in real if c<=6)/len(real)*100:.0f}%   (n={len(real)})")
    print(f"    sim  median={median(sim_shot):.0f}s  <=6s={sum(1 for c in sim_shot if c<=6)/len(sim_shot)*100:.0f}%   (n={len(sim_shot)})")
    print()
    hist(real, "REAL tied-offense scores")
    hist(sim_shot, "SIM  tied-offense scores")
    print()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2024-25")
    p.add_argument("--sims-per-game", type=int, default=3)
    a = p.parse_args()
    main(a.season, a.sims_per_game)
