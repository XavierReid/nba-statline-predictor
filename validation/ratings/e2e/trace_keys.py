"""Which per-player fields does the engine actually read? (A3.3 field audit)

Wraps every loaded roster dict in a recording dict, simulates a batch of real-schedule games, and reports
the keys read, split into 'present in the roster' and 'read but absent' (the engine silently falls back).
Run against an arm database; dev seasons only.

    scripts/vrun nba_arm_p_2021_22 validation.ratings.e2e.trace_keys 2021-22 P
"""
import json
import sys

from dataclasses import replace

from sqlalchemy import select

from app.api.schemas.simulations import DRAMA_M3_SEASON
from app.database import SessionLocal
from app.models.game import Game
from app.services.game_simulator import simulate_game
from app.services.league_simulator import season_bounds
from app.services.roster import load_roster
from validation.ratings import lockguard

N_GAMES = 40
# availability copies each player dict (dict(p)), which would drop the recording wrapper, so it is traced off;
# its own reads (games_played, mpg, minutes, avail_prob, is_starter) are listed from availability.py.
CFG = replace(DRAMA_M3_SEASON, use_availability=False, roster_depth=10)
AVAILABILITY_KEYS = ["avail_prob", "games_played", "is_starter", "minutes", "mpg"]


class Traced(dict):
    reads = set()

    def __getitem__(self, k):
        Traced.reads.add(k)
        return dict.__getitem__(self, k)

    def get(self, k, d=None):
        Traced.reads.add(k)
        return dict.get(self, k, d)

    def __contains__(self, k):
        Traced.reads.add(k)
        return dict.__contains__(self, k)

    def copy(self):
        return Traced(self)


def main(target, arm):
    lockguard.check(target)
    db = SessionLocal()
    start, end = season_bounds(target)
    games = db.execute(select(Game).where(Game.game_date >= start, Game.game_date <= end)
                       .order_by(Game.game_date, Game.id).limit(400)).scalars().all()
    cache, present, played = {}, set(), 0

    def roster(tid):
        if tid not in cache:
            cache[tid] = [Traced(p) for p in load_roster(db, tid, target, depth=10, pre_negation=True)]
            for p in cache[tid]:
                present.update(p.keys())
        return cache[tid]

    for g in games[::10]:
        h, a = roster(g.home_team_id), roster(g.away_team_id)
        if not h or not a:
            continue
        simulate_game(h, a, seed=g.id.__hash__() % 100000, season=target, config=CFG, db=db,
                      home_team_id=g.home_team_id, away_team_id=g.away_team_id)
        played += 1
        if played >= N_GAMES:
            break
    reads = Traced.reads
    out = {"arm": arm, "target": target, "games_simulated": played, "keys_read": sorted(reads | set(AVAILABILITY_KEYS)), "availability_keys_static": AVAILABILITY_KEYS,
           "read_and_present": sorted(reads & present), "read_but_absent": sorted(reads - present),
           "present_never_read": sorted(present - reads)}
    json.dump(out, open("validation/ratings/results/e2e/trace_%s_%s.json" % (arm, target), "w"), indent=1)
    print(arm, "games", played, "| read", len(reads), "| read+present", len(out["read_and_present"]),
          "| read but absent", out["read_but_absent"])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
