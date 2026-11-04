"""Load every team's roster through the unmodified production loader against ONE arm database and record
what came out. Run once per arm, in its own process (DATABASE_URL points at the arm database).

    docker compose run --rm -e DATABASE_URL=.../nba_arm_p_2021_22 api python -m validation.ratings.e2e.verify_arm 2021-22 P
    (for E, add --league to also run the real run_league_simulation and record its outcome)
"""
import hashlib
import json
import sys

from sqlalchemy import select

from app.database import SessionLocal
from app.models.team import Team
from app.services.roster import load_roster

LOADER_KEYS = ("minutes", "games_played", "ft_prob", "rim_fg_prob", "three_fg_prob", "nonrim_fg_prob", "tov_per_poss",
               "usage_rate", "three_point", "free_throw", "overall", "foul_rate", "assist_rate")


def main(target, arm, league):
    db = SessionLocal()
    out = {"arm": arm, "target": target, "teams": {}}
    for t in db.execute(select(Team).order_by(Team.id)).scalars():
        r = load_roster(db, t.id, target, depth=10, pre_negation=True)
        out["teams"][t.id] = [{"id": p["id"], **{k: p.get(k) for k in LOADER_KEYS}} for p in r]
    flat = json.dumps(out["teams"], sort_keys=True, default=str)
    out["roster_sha256"] = hashlib.sha256(flat.encode()).hexdigest()
    out["ids_minutes_sha256"] = hashlib.sha256(json.dumps(
        {t: [(p["id"], p["minutes"]) for p in r] for t, r in out["teams"].items()}, sort_keys=True).encode()).hexdigest()
    out["players_loaded"] = sum(len(r) for r in out["teams"].values())
    if league:
        from app.models.simulation import SimulationRun
        from app.services.league_simulator import run_league_simulation
        sim = SimulationRun(season=target, scope="league", team_id=None, seed=1, status="running", parameters={})
        db.add(sim); db.commit(); sid = sim.id
        run_league_simulation(sid)
        db.expire_all()
        sim = db.get(SimulationRun, sid)
        out["league_run"] = {"status": sim.status, "games_completed": sim.games_completed,
                             "total_games": (sim.parameters or {}).get("total_games"),
                             "failure_reason": (sim.parameters or {}).get("failure_reason")}
    json.dump(out, open("validation/ratings/results/e2e/verify_%s_%s.json" % (arm, target), "w"), indent=1, default=str)
    print(arm, "players_loaded", out["players_loaded"], "ids_minutes", out["ids_minutes_sha256"][:12], out.get("league_run", ""))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], "--league" in sys.argv)
