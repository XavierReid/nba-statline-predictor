"""Gap 3.2 instrumentation — mid-game dispersion / mean-reversion (instrument-first).

Hypothesis: the sim lacks in-game mean-reversion, so runs over-extend -> too many
blowouts, too few close games / lead changes. This tool measures it directly against
the real 2024-25 quarter line scores, WITHOUT changing any mechanism:

1. Quarter margin walk (re-baseline current state): mean |cumulative margin| end of each Q.
2. Per-quarter differential variance: var of the point differential scored IN each quarter.
   (too-swingy quarters inflate dispersion)
3. Mean-reversion test: residual quarter-to-quarter autocorrelation. Per game, take the
   four per-quarter differentials, subtract the game's own mean (removes team strength),
   correlate consecutive residuals. Real near 0 or negative = mean reversion; if sim is
   MORE POSITIVE than real, the sim over-persists runs -> the smoking gun.
4. Comeback rate: of games with a >=12 halftime lead, how often does the trailing team
   end regulation within 5 / win. Real vs sim.

Usage:
    python scratch/dispersion_diagnostics.py [--games 400]
"""
import argparse
import os
import sys
from statistics import mean, pvariance

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models.game import Game
from app.models.team import Team
from app.services.game_simulator import simulate_game
from app.services.roster import load_roster
from app.services.sim_config import DRAMA_M3
from sqlalchemy import select

MATCHUPS = [
    ("BOS", "LAL"), ("OKC", "GSW"), ("DEN", "MIL"), ("PHX", "MIA"),
    ("HOU", "MIN"), ("LAL", "CHA"), ("BOS", "DET"), ("GSW", "WAS"),
    ("OKC", "UTA"), ("DEN", "SAS"),
]


def _pearson(pairs):
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx, my = mean(xs), mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in pairs)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    return cov / (vx * vy) ** 0.5 if vx and vy else float("nan")


def analyze(quarter_diffs):
    """quarter_diffs: list of [d1,d2,d3,d4] per-quarter point differentials (home persp)."""
    # 1. margin walk
    walk = []
    for qi in range(4):
        cum = [abs(sum(g[:qi + 1])) for g in quarter_diffs]
        walk.append(mean(cum))
    # 2. per-quarter differential variance — broken out by quarter (a Q4-specific
    # tightening is invisible in the pooled figure)
    per_q_var = [pvariance([g[qi] for g in quarter_diffs]) for qi in range(4)]
    all_d = [d for g in quarter_diffs for d in g]
    qvar = pvariance(all_d)
    # Q4 differential variance split by entering-Q4 state (competitive vs decided)
    comp = [g[3] for g in quarter_diffs if abs(g[0]+g[1]+g[2]) <= 8]
    decided = [g[3] for g in quarter_diffs if abs(g[0]+g[1]+g[2]) >= 15]
    q4_comp = pvariance(comp) if len(comp) > 2 else float("nan")
    q4_decided = pvariance(decided) if len(decided) > 2 else float("nan")
    # 3. residual lag-1 autocorrelation
    pairs = []
    for g in quarter_diffs:
        m = mean(g)
        r = [d - m for d in g]
        pairs += [(r[i], r[i + 1]) for i in range(3)]
    autocorr = _pearson(pairs)
    # 4. comeback: halftime |lead| >= 12, trailing team ends within 5 / wins
    big_half = [g for g in quarter_diffs if abs(g[0] + g[1]) >= 12]
    within5 = sum(1 for g in big_half if abs(sum(g)) <= 5)
    comeback_win = sum(1 for g in big_half
                       if (sum(g) > 0) != ((g[0] + g[1]) > 0) or sum(g) == 0)
    return {
        "walk": walk, "qvar": qvar, "per_q_var": per_q_var, "autocorr": autocorr,
        "q4_comp": q4_comp, "q4_decided": q4_decided, "n_comp": len(comp), "n_decided": len(decided),
        "n_bighalf": len(big_half),
        "within5_rate": within5 / max(len(big_half), 1),
        "comeback_rate": comeback_win / max(len(big_half), 1),
    }


def main(n_games):
    db = SessionLocal()

    real = db.execute(
        select(Game).where(Game.id.like("00224%"), Game.status == "final",
                           Game.home_q1.isnot(None))
    ).scalars().all()
    real_diffs = [[g.home_q1 - g.away_q1, g.home_q2 - g.away_q2,
                   g.home_q3 - g.away_q3, g.home_q4 - g.away_q4] for g in real]

    # Simulate the REAL schedule (paired, composition-free): each real game's matchup
    # + home team, 2025-26 rosters. Removes the fixed-matchup lopsidedness bias.
    import zlib
    rosters = {}
    def _roster(tid):
        if tid not in rosters:
            rosters[tid] = load_roster(db, tid, "2025-26")
        return rosters[tid]

    sim_diffs = []
    for g in real:
        h_ros, a_ros = _roster(g.home_team_id), _roster(g.away_team_id)
        if not h_ros or not a_ros:
            continue
        seed = zlib.crc32(str(g.id).encode())
        r = simulate_game(h_ros, a_ros, seed=seed, season="2025-26",
                          config=DRAMA_M3, home_team_id=g.home_team_id,
                          away_team_id=g.away_team_id, db=db)
        hq, aq = r["quarter_scores"]["home"][:4], r["quarter_scores"]["away"][:4]
        sim_diffs.append([hq[qi] - aq[qi] for qi in range(4)])
    db.close()

    R, S = analyze(real_diffs), analyze(sim_diffs)
    print(f"\n{'='*64}\n  Mid-game dispersion — REAL {len(real_diffs)} vs SIM {len(sim_diffs)} games\n{'='*64}")
    print(f"  {'metric':<38} {'real':>10} {'sim':>10}")
    for qi in range(4):
        print(f"  {'|margin| end of Q'+str(qi+1):<38} {R['walk'][qi]:>10.2f} {S['walk'][qi]:>10.2f}")
    for qi in range(4):
        print(f"  {'differential variance Q'+str(qi+1):<38} {R['per_q_var'][qi]:>10.1f} {S['per_q_var'][qi]:>10.1f}")
    print(f"  {'Q4 var | competitive entering Q4 (<=8)':<38} {R['q4_comp']:>10.1f} {S['q4_comp']:>10.1f}")
    print(f"  {'Q4 var | decided entering Q4 (>=15)':<38} {R['q4_decided']:>10.1f} {S['q4_decided']:>10.1f}")
    print(f"    (competitive n: real {R['n_comp']} sim {S['n_comp']}; decided n: real {R['n_decided']} sim {S['n_decided']})")
    print(f"  {'residual Q-to-Q autocorr (MEAN REVERSION)':<38} {R['autocorr']:>10.3f} {S['autocorr']:>10.3f}")
    print(f"    (real <= sim would confirm sim over-persists runs)")
    print(f"  {'games w/ 12+ halftime lead':<38} {R['n_bighalf']:>10} {S['n_bighalf']:>10}")
    print(f"  {'  -> end within 5':<38} {R['within5_rate']*100:>9.1f}% {S['within5_rate']*100:>9.1f}%")
    print(f"  {'  -> trailing team comeback win/tie':<38} {R['comeback_rate']*100:>9.1f}% {S['comeback_rate']*100:>9.1f}%")
    print(f"{'='*64}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=400)
    args = parser.parse_args()
    main(args.games)
