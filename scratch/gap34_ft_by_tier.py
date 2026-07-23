"""Gap 3.4 FT-trip instrument — is the star points deficit a foul-DRAW (FTA) gap?

The Δpoints waterfall shows a monotonic-by-usage FT term (star -1.24). Pin it: by
tier, FGA / FTA / FT-draw rate (FTA per FGA) / FTM, real vs sim. If FTA/FGA (the
per-attempt foul-draw rate) is low for stars, the owner is per-player foul GENERATION
(stars draw fouls at a higher rate than the flat model gives them), not FT%.

Usage: python scratch/gap34_ft_by_tier.py --season 2025-26 --sims 2
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.analysis.player_accounting import real_accounts, sim_accounts, TIERS
from app.services.sim_config import DRAMA_M3


def main(season, sims):
    db = SessionLocal()
    real = real_accounts(db, season)
    sim = sim_accounts(db, season, {pid: a.tier for pid, a in real.items()}, DRAMA_M3, sims, None)
    db.close()

    tiers = {t: [] for t in TIERS}
    for pid, r in real.items():
        s = sim.get(pid)
        if s:
            tiers[r.tier].append((r, s))

    print(f"\n{'='*88}\n  Gap 3.4 FT trips by tier: {season}  (sims={sims})\n{'='*88}")
    print(f"  {'tier':<10}{'n':>4}{'FGA r/s':>14}{'FTA r/s':>14}{'FTA/FGA r/s':>16}{'FTM r/s':>14}")
    for t in TIERS:
        pairs = tiers[t]
        if not pairs:
            continue
        n = len(pairs)
        def avg(f): return (sum(f(r) for r, s in pairs)/n, sum(f(s) for r, s in pairs)/n)
        fga = avg(lambda a: a.fga)
        fta = avg(lambda a: a.fta)
        ftm = avg(lambda a: a.ftm)
        rate_r = sum(r.fta for r, s in pairs) / max(1e-9, sum(r.fga for r, s in pairs))
        rate_s = sum(s.fta for r, s in pairs) / max(1e-9, sum(s.fga for r, s in pairs))
        print(f"  {t:<10}{n:>4}{fga[0]:>7.1f}/{fga[1]:>5.1f}{fta[0]:>8.1f}/{fta[1]:>4.1f}"
              f"{rate_r:>9.3f}/{rate_s:>5.3f}{ftm[0]:>8.1f}/{ftm[1]:>4.1f}")
    # team totals (FTA reconciliation)
    tr = sum(r.fta for r in real.values()) / len({r.team_id for r in real.values()})
    ts = sum(s.fta for s in sim.values()) / len({s.team_id for s in sim.values()})
    print(f"\n  team FTA/game (sum over roster): real {tr:.1f}  sim {ts:.1f}  ({ts-tr:+.1f})")
    print()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2025-26")
    p.add_argument("--sims", type=int, default=2)
    a = p.parse_args()
    main(a.season, a.sims)
