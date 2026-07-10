"""Measured calibration targets from real per-quarter line scores.

Replaces memory-based references (Q1 margin ~5.5-6, close-late funnel, etc.)
with values measured from ingested NBA line scores. Run after
ingest_line_scores has backfilled a season.

Usage:
    python scratch/real_quarter_targets.py [--season-prefix 00224]
"""
import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models.game import Game
from sqlalchemy import select


def main(prefix: str) -> None:
    db = SessionLocal()
    games = db.execute(
        select(Game).where(
            Game.id.like(f"{prefix}%"),
            Game.status == "final",
            Game.home_q1.isnot(None),
        )
    ).scalars().all()
    db.close()

    if not games:
        print(f"No line scores for prefix {prefix} — run ingest_line_scores first.")
        return

    # cumulative margins at end of each quarter
    walk = {1: [], 2: [], 3: [], 4: []}
    q_scores = []           # per-team per-quarter scoring (distribution check)
    entering_q4 = []        # |margin| entering Q4
    reg_ties = 0
    close_q4_outcomes = {"tie (OT)": 0, "1-3": 0, "4-5": 0, "6+ (widened)": 0}
    close_q4_n = 0

    for g in games:
        hq = [g.home_q1, g.home_q2, g.home_q3, g.home_q4]
        aq = [g.away_q1, g.away_q2, g.away_q3, g.away_q4]
        run_h = run_a = 0
        for qi in range(4):
            run_h += hq[qi]
            run_a += aq[qi]
            walk[qi + 1].append(run_h - run_a)
            q_scores.append(hq[qi])
            q_scores.append(aq[qi])
        reg_margin = abs(walk[4][-1])
        if reg_margin == 0:
            reg_ties += 1
        m3 = abs(walk[3][-1])
        entering_q4.append(m3)
        if m3 <= 5:
            close_q4_n += 1
            if reg_margin == 0:
                close_q4_outcomes["tie (OT)"] += 1
            elif reg_margin <= 3:
                close_q4_outcomes["1-3"] += 1
            elif reg_margin <= 5:
                close_q4_outcomes["4-5"] += 1
            else:
                close_q4_outcomes["6+ (widened)"] += 1

    n = len(games)
    print(f"\n{'='*62}")
    print(f"  REAL quarter targets — {n} games (prefix {prefix})")
    print(f"{'='*62}")

    print(f"\n  Margin walk (|margin| at end of quarter):")
    print(f"  {'Q':>3} {'mean |m|':>9} {'sigma':>7} {'sqrt(t) pred from Q1':>21}")
    sig1 = None
    for q in (1, 2, 3, 4):
        ms = walk[q]
        mean_abs = sum(abs(m) for m in ms) / n
        mu = sum(ms) / n
        sig = math.sqrt(sum((m - mu) ** 2 for m in ms) / n)
        if q == 1:
            sig1 = sig
        pred = sig1 * math.sqrt(q) * 0.7979  # E|X| for a centered normal
        print(f"  {q:>3} {mean_abs:>9.2f} {sig:>7.2f} {pred:>21.2f}")
    print("  (mean |m| below the sqrt(t) prediction = real compression vs iid)")

    mu_q = sum(q_scores) / len(q_scores)
    var_q = sum((s - mu_q) ** 2 for s in q_scores) / len(q_scores)
    print(f"\n  Per-team quarter scoring: mean {mu_q:.2f}, sigma {math.sqrt(var_q):.2f}")

    print(f"\n  Entering Q4: mean |margin| {sum(entering_q4)/n:.2f}; "
          f"within 5: {sum(1 for m in entering_q4 if m <= 5)/n*100:.1f}%")
    print(f"  Regulation ties (OT games): {reg_ties}/{n} ({reg_ties/n*100:.1f}%)")
    print(f"\n  Games within 5 entering Q4 — outcomes ({close_q4_n} games):")
    for k, v in close_q4_outcomes.items():
        print(f"    {k:<15} {v:>4}  ({v/max(close_q4_n,1)*100:.1f}%)")
    print(f"{'='*62}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--season-prefix", type=str, default="00224")
    args = parser.parse_args()
    main(args.season_prefix)
