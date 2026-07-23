"""Gap 3.4 rotation — is the primary minutes deficit in the INPUT or the engine?

sim primary minutes 27.7 vs real MPG 30.9 (-3.2). The rotation targets p["minutes"],
which roster.py normalizes: 240 team-min redistributed over the top-10 by
(MPG x games_played). This compares the normalized input p["minutes"] to real MPG by
tier. If input ~= sim (27.7), the games-weighting normalization is the owner (upstream);
if input ~= real MPG (30.9), the rotation engine loses the minutes.

Usage: python scratch/gap34_minutes_input.py --season 2025-26
"""
import argparse
import os
import sys
from collections import defaultdict
from statistics import mean

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.analysis.player_accounting import real_accounts, TIERS
from app.services.roster import load_roster
from app.models.team import Team
from sqlalchemy import select


def main(season):
    db = SessionLocal()
    real = real_accounts(db, season)  # real.minutes = MPG
    teams = db.execute(select(Team)).scalars().all()
    inp = {}   # pid -> normalized p["minutes"]; also raw MPG and GP
    for t in teams:
        r = load_roster(db, t.id, season) or []
        for p in r:
            inp[p["id"]] = p.get("minutes")
    db.close()

    by_tier = defaultdict(lambda: {"real_mpg": [], "input": []})
    for pid, a in real.items():
        if pid in inp and inp[pid] is not None:
            by_tier[a.tier]["real_mpg"].append(a.minutes)
            by_tier[a.tier]["input"].append(inp[pid])

    print(f"\n{'='*60}\n  Gap 3.4 rotation input minutes vs real MPG: {season}\n{'='*60}")
    print(f"  {'tier':<10}{'real MPG':>10}{'input min':>12}{'diff':>8}   (input = post games-weight normalize)")
    tot_r = tot_i = 0.0
    for t in TIERS:
        d = by_tier[t]
        if not d["real_mpg"]:
            continue
        rm, im = mean(d["real_mpg"]), mean(d["input"])
        n = len(d["real_mpg"])
        tot_r += rm * n; tot_i += im * n
        print(f"  {t:<10}{rm:>10.1f}{im:>12.1f}{im-rm:>+8.1f}")
    print(f"\n  (top-10 per team; input sums to 240 by construction, real MPG sums to {tot_r/ (tot_r/240 if tot_r else 1):.0f}+ over 240)")
    print()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2025-26")
    a = p.parse_args()
    main(a.season)
