"""Run N matched-seed league simulations of one arm and score them against the real season (A2.2).

Dev seasons only (lockguard). One arm per process/database. Real reference values come from the backfill.

    scripts/vrun nba_arm_p_2021_22 validation.ratings.e2e.evaluate 2021-22 P 10
"""
import json
import sys
import time

from sqlalchemy import create_engine, select, text

from app.api.schemas.simulations import DRAMA_M3_SEASON
from app.database import SessionLocal
from app.models.game import Game
from app.models.simulation import SimulatedGame, SimulationRun
from app.models.team import Team
from app.services.game_simulator import load_roster, simulate_game
from app.services.league_simulator import compute_standings, run_league_simulation, season_bounds
from validation.ratings import lockguard

SEED0, H2H_GAMES, TOP_N = 9100, 100, 80
BACKFILL = "postgresql+psycopg2://nba:nba@postgres:5432/nba_backfill"


def standings(db, rows):
    abbr = {t: a for t, a in db.execute(select(Team.id, Team.abbreviation)).all()}
    return {s.team_id: s.wins for s in compute_standings([(g, h, a, hs, as_, abbr[h], abbr[a]) for g, h, a, hs, as_, in rows])}


def rank(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    for k, i in enumerate(order):
        r[i] = float(k)
    return r


def spearman(x, y):
    rx, ry, n = rank(x), rank(y), len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


def sd(v):
    m = sum(v) / len(v)
    return (sum((x - m) ** 2 for x in v) / (len(v) - 1)) ** 0.5


def main(target, arm, n):
    lockguard.check(target)
    db = SessionLocal()
    start, end = season_bounds(target)
    real_rows = db.execute(select(Game.id, Game.home_team_id, Game.away_team_id, Game.home_score, Game.away_score)
                           .where(Game.game_date >= start, Game.game_date <= end, Game.home_score.is_not(None))
                           .order_by(Game.id)).all()
    real_w = standings(db, real_rows)
    teams = sorted(real_w)
    best, worst = max(teams, key=lambda t: real_w[t]), min(teams, key=lambda t: real_w[t])
    with create_engine(BACKFILL, future=True).connect() as c:
        real_p = {r[0]: r[1:] for r in c.execute(text(
            "select player_id, points, rebounds, assists, minutes_per_game, games_played from player_season_stats "
            "where season=:s order by minutes_per_game*games_played desc limit :n"), {"s": target, "n": TOP_N})}
    runs = []
    for i in range(n):
        t0, seed = time.time(), SEED0 + i
        sim = SimulationRun(season=target, scope="league", team_id=None, seed=seed, status="pending",
                            parameters={"arm": arm, "target": target})
        db.add(sim); db.commit(); sid = sim.id
        db.close()
        run_league_simulation(sid, config=DRAMA_M3_SEASON)
        db = SessionLocal()
        srows = db.execute(select(SimulatedGame.game_id, Game.home_team_id, Game.away_team_id, SimulatedGame.home_score,
                                  SimulatedGame.away_score).join(Game, Game.id == SimulatedGame.game_id)
                           .where(SimulatedGame.simulation_id == sid)).all()
        sw = standings(db, srows)
        wins = [sw.get(t, 0) for t in teams]
        lines = {r[0]: r[1:] for r in db.execute(text(
            "select l.player_id, count(*), sum(l.points), sum(l.rebounds), sum(l.assists), sum(l.minutes) "
            "from simulated_player_lines l join simulated_games g on g.id=l.simulated_game_id "
            "where g.simulation_id=:s group by l.player_id"), {"s": sid})}
        have = [p for p in real_p if p in lines]
        mae = {}
        for k, name in enumerate(("ppg", "rpg", "apg")):
            mae[name] = sum(abs(lines[p][1 + k] / lines[p][0] - real_p[p][k]) for p in have) / max(1, len(have))
        mae["mpg"] = sum(abs(lines[p][4] / lines[p][0] - real_p[p][3]) for p in have) / max(1, len(have))
        rho_ppg = spearman([lines[p][1] / lines[p][0] for p in have], [real_p[p][0] for p in have]) if len(have) > 3 else None
        runs.append({"seed": seed, "games": len(srows), "rho": spearman(wins, [real_w[t] for t in teams]),
                     "spread_ratio": sd(wins) / sd([real_w[t] for t in teams]) if len(set(wins)) > 1 else 0.0,
                     "top80_present": len(have), "mae": mae, "rho_ppg": rho_ppg, "elapsed_s": round(time.time() - t0, 1)})
        print(arm, i + 1, "/", n, "games", len(srows), "rho %.3f" % runs[-1]["rho"], "%.0fs" % runs[-1]["elapsed_s"], flush=True)
    wins_h2h = 0
    for i in range(H2H_GAMES):
        home, away = (best, worst) if i % 2 == 0 else (worst, best)
        hp, ap = (load_roster(db, t, target, depth=DRAMA_M3_SEASON.roster_depth, pre_negation=True) for t in (home, away))
        if not hp or not ap:
            break
        r = simulate_game(hp, ap, seed=7000 + i, season=target, config=DRAMA_M3_SEASON, db=db, home_team_id=home, away_team_id=away)
        hs, as_ = r["home_score"], r["away_score"]
        wins_h2h += (hs > as_) == (home == best)
    out = {"arm": arm, "target": target, "runs": runs, "h2h_best_wins": wins_h2h, "h2h_games": H2H_GAMES,
           "real_best": best, "real_worst": worst, "n_top80_real": len(real_p)}
    json.dump(out, open("validation/ratings/results/e2e/eval_%s_%s.json" % (arm, target), "w"), indent=1)
    print(arm, "done; h2h best-team wins", wins_h2h, "/", H2H_GAMES)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]))
