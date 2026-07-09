"""Q4 divergence diagnostics — the last calibration gap (SIMULATION_GAPS.md).

Real Q4 adds +0.81 to |margin| (compresses vs iid); sim adds +2.30 (expands).
This tool decomposes WHERE, in three views:

1. Transition matrix: entering-Q4 margin bucket -> end-of-regulation bucket,
   real (line scores) vs sim, row-normalized.
2. Possession-level decomposition of sim Q4 by entering bucket: possessions,
   PPP for the leading vs trailing side, margin delta.
3. Hypothesis toggles: same measurements with use_garbage_rotation /
   use_lineup_quality / use_team_defense disabled, one at a time.

Usage:
    python scratch/q4_diagnostics.py [--games 300]
"""
import argparse
import os
import sys
from collections import defaultdict
from dataclasses import replace

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

BUCKETS = [(0, 5, "0-5"), (6, 10, "6-10"), (11, 20, "11-20"), (21, 99, "21+")]


def bucket_of(m):
    for lo, hi, label in BUCKETS:
        if lo <= abs(m) <= hi:
            return label
    return "21+"


def print_matrix(title, matrix, deltas=None):
    labels = [b[2] for b in BUCKETS]
    print(f"\n  {title}")
    header = "".join(f"{l:>8}" for l in labels)
    print(f"    enter\\end {header}   n   Q4 |m| delta")
    for row_label in labels:
        row = matrix.get(row_label, {})
        n = sum(row.values())
        cells = "".join(f"{row.get(c, 0) / max(n, 1) * 100:>7.1f}%" for c in labels)
        d = ""
        if deltas is not None and deltas.get(row_label):
            ds = deltas[row_label]
            d = f"{sum(ds) / len(ds):>+8.2f}"
        print(f"    {row_label:<9} {cells} {n:>4}{d}")


def real_matrix(db):
    games = db.execute(
        select(Game).where(Game.id.like("00224%"), Game.status == "final",
                           Game.home_q1.isnot(None))
    ).scalars().all()
    matrix = defaultdict(lambda: defaultdict(int))
    deltas = defaultdict(list)
    for g in games:
        q3 = (g.home_q1 + g.home_q2 + g.home_q3) - (g.away_q1 + g.away_q2 + g.away_q3)
        reg = q3 + (g.home_q4 - g.away_q4)
        matrix[bucket_of(q3)][bucket_of(reg)] += 1
        deltas[bucket_of(q3)].append(abs(reg) - abs(q3))
    return matrix, deltas


def sim_run(db, rosters, ids, cfg, n_per, collect_poss=False):
    matrix = defaultdict(lambda: defaultdict(int))
    deltas = defaultdict(list)
    poss = defaultdict(lambda: {"n": 0, "lead_pts": 0, "lead_poss": 0,
                                "trail_pts": 0, "trail_poss": 0, "tied_poss": 0})
    # behavioral decomposition: side -> bucket -> counters
    beh = defaultdict(lambda: defaultdict(lambda: {
        "poss": 0, "pts": 0, "dur": 0.0, "dur_n": 0, "threes": 0, "rim": 0,
        "shots": 0, "tov": 0, "oreb": 0, "fouled": 0, "fastbreak": 0}))
    n = 0
    for h, a in MATCHUPS:
        for i in range(n_per):
            r = simulate_game(rosters[h], rosters[a], seed=n * 7919 + i, season="2025-26",
                              config=cfg, capture_descriptions=collect_poss,
                              home_team_id=ids[h], away_team_id=ids[a], db=db)
            n += 1
            hq, aq = r["quarter_scores"]["home"][:4], r["quarter_scores"]["away"][:4]
            q3 = sum(hq[:3]) - sum(aq[:3])
            reg = q3 + (hq[3] - aq[3])
            b = bucket_of(q3)
            matrix[b][bucket_of(reg)] += 1
            deltas[b].append(abs(reg) - abs(q3))

            if collect_poss:
                run_h = sum(hq[:3])
                run_a = sum(aq[:3])
                prev_clock = None
                for e in r["events"]:
                    if e["quarter"] != 4:
                        if e["quarter"] < 4:
                            continue
                        break
                    margin = run_h - run_a
                    offense_leading = (margin > 0) == e["is_home"] and margin != 0
                    p = poss[b]
                    p["n"] += 1
                    if margin == 0:
                        p["tied_poss"] += 1
                        side = None
                    elif offense_leading:
                        p["lead_poss"] += 1
                        p["lead_pts"] += e["pts"]
                        side = "lead"
                    else:
                        p["trail_poss"] += 1
                        p["trail_pts"] += e["pts"]
                        side = "trail"

                    if side:
                        s = beh[side][b]
                        s["poss"] += 1
                        s["pts"] += e["pts"]
                        if prev_clock is not None and prev_clock >= e["game_clock_seconds"]:
                            s["dur"] += prev_clock - e["game_clock_seconds"]
                            s["dur_n"] += 1
                        st = e.get("shot_type")
                        if st:
                            s["shots"] += 1
                            if "three" in st:
                                s["threes"] += 1
                            if st in ("layup", "dunk", "floater", "close"):
                                s["rim"] += 1
                        if e.get("turnover_by"):
                            s["tov"] += 1
                        if e.get("is_oreb"):
                            s["oreb"] += 1
                        if e.get("fta"):
                            s["fouled"] += 1
                        if e.get("is_fastbreak"):
                            s["fastbreak"] += 1
                    prev_clock = e["game_clock_seconds"]

                    if e["is_home"]:
                        run_h += e["pts"]
                    else:
                        run_a += e["pts"]
    return matrix, deltas, poss, beh


def main(n_games):
    db = SessionLocal()
    rosters, ids = {}, {}
    for h, a in MATCHUPS:
        for ab in (h, a):
            if ab not in rosters:
                t = db.execute(select(Team).where(Team.abbreviation == ab)).scalar_one()
                rosters[ab] = load_roster(db, t.id, "2025-26")
                ids[ab] = t.id
    n_per = max(1, n_games // len(MATCHUPS))

    print("=" * 70)
    rm, rd = real_matrix(db)
    print_matrix("REAL 2024-25 — Q4 transition matrix (row %)", rm, rd)

    sm, sd, sp, sbeh = sim_run(db, rosters, ids, DRAMA_M3, n_per, collect_poss=True)
    print_matrix("SIM DRAMA_M3 — Q4 transition matrix (row %)", sm, sd)

    print("\n  SIM Q4 possession decomposition by entering bucket:")
    print(f"    {'bucket':<8} {'poss/gm':>8} {'PPP lead':>9} {'PPP trail':>10} {'net/game':>9}")
    for _, _, label in BUCKETS:
        p = sp[label]
        games_in = sum(sm[label].values())
        if not games_in or not p["lead_poss"] or not p["trail_poss"]:
            continue
        ppl = p["lead_pts"] / p["lead_poss"]
        ppt = p["trail_pts"] / p["trail_poss"]
        net = (p["lead_pts"] - p["trail_pts"]) / games_in
        print(f"    {label:<8} {p['n']/games_in:>8.1f} {ppl:>9.3f} {ppt:>10.3f} {net:>+9.2f}")

    print("\n  SIM Q4 behavioral decomposition (lead vs trail, per entering bucket):")
    print(f"    {'bucket':<8} {'side':<6} {'PPP':>6} {'dur s':>6} {'3PA%':>6} {'rim%':>6} {'TOV%':>6} {'OREB%':>6} {'foul%':>6} {'fb%':>5}")
    for _, _, label in BUCKETS:
        for side in ("lead", "trail"):
            s = sbeh[side][label]
            if not s["poss"]:
                continue
            print(f"    {label:<8} {side:<6} {s['pts']/s['poss']:>6.3f}"
                  f" {s['dur']/max(s['dur_n'],1):>6.1f}"
                  f" {s['threes']/max(s['shots'],1)*100:>5.1f}%"
                  f" {s['rim']/max(s['shots'],1)*100:>5.1f}%"
                  f" {s['tov']/s['poss']*100:>5.1f}%"
                  f" {s['oreb']/s['poss']*100:>5.1f}%"
                  f" {s['fouled']/s['poss']*100:>5.1f}%"
                  f" {s['fastbreak']/s['poss']*100:>4.1f}%")

    print("\n  Hypothesis toggles (Q4 |m| delta by entering bucket):")
    variants = [
        ("- garbage_rotation", replace(DRAMA_M3, use_garbage_rotation=False)),
        ("- lineup_quality", replace(DRAMA_M3, use_lineup_quality=False)),
        ("- team_defense", replace(DRAMA_M3, use_team_defense=False)),
        ("- garbage_time_mod", replace(DRAMA_M3, use_garbage_time=False)),
    ]
    labels = [b[2] for b in BUCKETS]
    base_line = "".join(f"{sum(sd[l])/max(len(sd[l]),1):>+9.2f}" for l in labels)
    print(f"    {'DRAMA_M3 (base)':<20} {base_line}")
    for name, cfg in variants:
        _, vd, _, _ = sim_run(db, rosters, ids, cfg, n_per)
        line = "".join(f"{sum(vd[l])/max(len(vd[l]),1):>+9.2f}" for l in labels)
        print(f"    {name:<20} {line}")
    rd_line = "".join(f"{sum(rd[l])/max(len(rd[l]),1):>+9.2f}" for l in labels)
    print(f"    {'REAL 2024-25':<20} {rd_line}")
    print("=" * 70)
    db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=300)
    args = parser.parse_args()
    main(args.games)
