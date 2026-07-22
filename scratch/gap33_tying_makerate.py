"""Gap 3.3 survival owner cut (a) — tying-shot make-rate in the late window.

Mechanism 1 fixed shot SELECTION; the OT rate didn't move and tied-tempo was
falsified. Next candidate owner: the tying shot doesn't GO IN often enough, so
ties never form even when the right shot is taken.

Measure, for the trailing team's late-clock FG attempts (one-score deficit,
clock<=window), split by tying value (2 when down 2, 3 when down 3):
  attempts, make%, contested%, sub_type (location).
Anchor against (a) the sim's OWN non-late make% for the same sub_types (is the
late/contest model depressing makes further than normal?) and (b) known real
clutch rates (contested buzzer 3 ~0.28-0.33, contested 2 ~0.40-0.45). Real PBP
is made-only so a direct real late make% isn't available in-DB.

Usage: python scratch/gap33_tying_makerate.py --season 2024-25 --sims-per-game 4 --window 10
"""
import argparse
import os
import sys
import zlib
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models.game import Game
from app.models.team import Team
from app.services.game_simulator import simulate_game
from app.services.roster import load_roster
from app.services.sim_config import DRAMA_M3
from sqlalchemy import select

THREES = {"corner_three", "above_break_three", "three"}


def is_three(st):
    return st in THREES


def main(season, sims_per_game, window):
    db = SessionLocal()
    year = season.split("-")[0][-2:]
    games = db.execute(select(Game).where(
        Game.id.like(f"002{year}%"), Game.status == "final", Game.home_q4.isnot(None)
    )).scalars().all()
    teams = db.execute(select(Team)).scalars().all()
    ros = {t.id: load_roster(db, t.id, season) for t in teams}
    ros = {k: v for k, v in ros.items() if v}

    # late tying attempts: (value) -> [attempts, makes, contested, Counter(sub_type)]
    late = {2: [0, 0, 0, Counter()], 3: [0, 0, 0, Counter()]}
    # baseline non-late FG: value -> [attempts, makes, contested]
    base = {2: [0, 0, 0], 3: [0, 0, 0]}

    for g in games:
        if g.home_team_id not in ros or g.away_team_id not in ros:
            continue
        for k in range(sims_per_game):
            r = simulate_game(ros[g.home_team_id], ros[g.away_team_id],
                              seed=zlib.crc32(str(g.id).encode()) + k, season=season,
                              config=DRAMA_M3, home_team_id=g.home_team_id,
                              away_team_id=g.away_team_id, db=db, capture_descriptions=True)
            hs = as_ = 0
            for ev in r["events"]:
                st = ev.get("shot_type")
                if st is not None:  # a FG attempt
                    val = 3 if is_three(st) else 2
                    made = bool(ev.get("made"))
                    contested = bool(ev.get("contested"))
                    off = hs if ev["is_home"] else as_
                    dfn = as_ if ev["is_home"] else hs
                    deficit = dfn - off
                    clk = ev["game_clock_seconds"]
                    is_tying = (ev["quarter"] >= 4 and clk <= window
                                and ((deficit == 3 and val == 3) or (deficit == 2 and val == 2)))
                    if is_tying:
                        late[val][0] += 1
                        late[val][1] += int(made)
                        late[val][2] += int(contested)
                        late[val][3][st] += 1
                    else:
                        base[val][0] += 1
                        base[val][1] += int(made)
                        base[val][2] += int(contested)
                # advance score
                hs += ev["pts"] if ev["is_home"] else 0
                as_ += 0 if ev["is_home"] else ev["pts"]
    db.close()

    print(f"\n{'='*62}\n  Gap 3.3 tying-shot make-rate: {season} (clock<={window}s)\n{'='*62}")
    print(f"\n  {'shot':<14} {'n':>6} {'make%':>7} {'contest%':>9}   vs baseline make% (non-late)")
    for val, name in ((3, "tying 3 (dn3)"), (2, "tying 2 (dn2)")):
        a, m, c, sub = late[val]
        ba, bm, bc = base[val]
        mk = m / a * 100 if a else 0
        ct = c / a * 100 if a else 0
        bmk = bm / ba * 100 if ba else 0
        print(f"  {name:<14} {a:>6} {mk:>6.1f}% {ct:>8.1f}%   base {bmk:>5.1f}% (contest {bc/ba*100:>4.0f}%, n={ba})")
    anchors = {3: "real contested buzzer 3 ~28-33%", 2: "real contested buzzer 2 ~40-45%"}
    print(f"\n  real clutch anchors: {anchors[3]}; {anchors[2]}")
    print(f"\n  tying-shot location (sub_type) mix:")
    for val, name in ((3, "tying 3"), (2, "tying 2")):
        sub = late[val][3]
        tot = sum(sub.values()) or 1
        mix = "  ".join(f"{k}:{v/tot*100:.0f}%" for k, v in sub.most_common())
        print(f"    {name}: {mix}")
    print()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2024-25")
    p.add_argument("--sims-per-game", type=int, default=4)
    p.add_argument("--window", type=int, default=10)
    a = p.parse_args()
    main(a.season, a.sims_per_game, a.window)
