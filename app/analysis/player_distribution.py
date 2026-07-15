"""Per-player game-level distribution guard (gap 3.4d sanity check).

Team-level box aggregates (team_boxscore.py) can be exactly right while the
distribution ACROSS players and games is wrong — e.g. a team's whole steal/block
total funneled onto one player (the concentration bug found 2026-07-14, invisible to
every team-level check but obvious in a single box score). This measures per-player-game
EXTREMES and flags any beyond a realistic sanity ceiling.

It is a GUARDRAIL, not a precise calibration: true per-game calibration needs
PlayerGameLog ingestion (gap 3.4d). Until then the ceilings below are documented
approximations of real NBA single-game behavior — generous enough that only genuinely
unrealistic output (16-steal lines, 3+ foul-outs/game) trips them.
"""
import argparse
import os
import sys
from statistics import mean

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.analysis.decomposition import simulate_schedule
from app.database import SessionLocal
from app.services.sim_config import DRAMA_M3

_MIN_MINUTES = 8.0  # ignore end-of-bench cameos for rate stats


def _pct(xs, thresh):
    return 100.0 * sum(1 for x in xs if x >= thresh) / len(xs) if xs else 0.0


def measure(sims) -> dict:
    ng = len(sims)
    stl, blk, pts, ast, reb, tov, pf36 = [], [], [], [], [], [], []
    foulouts_per_game = []
    for g in sims:
        fo = 0
        for st in g["box_score"].values():
            if st.get("fouled_out"):
                fo += 1
            m = st.get("min", 0)
            if m < _MIN_MINUTES:
                continue
            stl.append(st.get("stl", 0)); blk.append(st.get("blk", 0))
            pts.append(st.get("pts", 0)); ast.append(st.get("ast", 0))
            reb.append(st.get("reb", 0)); tov.append(st.get("tov", 0))
            pf36.append(st.get("pf", 0) * 36 / m)
        foulouts_per_game.append(fo)
    return {
        "player_games": len(stl), "games": ng,
        "stl_max": max(stl), "stl_ge5_pct": _pct(stl, 5), "stl_ge3_pct": _pct(stl, 3),
        "blk_max": max(blk), "blk_ge5_pct": _pct(blk, 5), "blk_ge3_pct": _pct(blk, 3),
        "pts_max": max(pts), "ast_max": max(ast), "reb_max": max(reb),
        "tov_max": max(tov), "tov_ge8_pct": _pct(tov, 8),
        "pf36_mean": mean(pf36), "pf36_p90": sorted(pf36)[int(0.9 * len(pf36))],
        "foulouts_per_game": mean(foulouts_per_game),
        "foulouts_ge3_pct": _pct(foulouts_per_game, 3),
    }


# (label, key, ceiling, format) — ceilings are generous real-NBA sanity bounds.
_CHECKS = [
    ("max steals (one game)",      "stl_max",          10,  "{:.0f}"),
    ("≥5-steal player-games %",    "stl_ge5_pct",      1.0, "{:.2f}"),
    ("max blocks (one game)",      "blk_max",          10,  "{:.0f}"),
    ("≥5-block player-games %",    "blk_ge5_pct",      1.0, "{:.2f}"),
    ("max points (one game)",      "pts_max",          70,  "{:.0f}"),
    # TOV: a rate is robust; max-over-24k player-games is noisy and TOV is legitimately
    # concentrated on ball-handlers (≥10 TOV ~0.02% = the real once-a-season tail).
    ("≥8-TOV player-games %",      "tov_ge8_pct",      0.5, "{:.2f}"),
    ("PF/36 mean",                 "pf36_mean",        3.5, "{:.2f}"),
    ("foul-outs / game",           "foulouts_per_game",0.6, "{:.2f}"),
    ("3+ foul-outs game %",        "foulouts_ge3_pct", 0.5, "{:.2f}"),
]


def report(m: dict) -> bool:
    w = 62
    print("\n" + "=" * w)
    print(f"  Per-player distribution guard  (games={m['games']}, player-games={m['player_games']})")
    print("=" * w)
    print(f"  {'metric':30}{'value':>10}{'ceiling':>10}{'':>6}")
    ok = True
    for label, key, ceil, fmt in _CHECKS:
        val = m[key]
        flag = val > ceil
        ok = ok and not flag
        print(f"  {label:30}{fmt.format(val):>10}{fmt.format(ceil):>10}{'  FLAG' if flag else '  ok':>6}")
    print(f"\n  context (no ceiling): ≥3 stl {m['stl_ge3_pct']:.1f}% / ≥3 blk {m['blk_ge3_pct']:.1f}%"
          f" / max pts {m['pts_max']:.0f} ast {m['ast_max']:.0f} reb {m['reb_max']:.0f} tov {m['tov_max']:.0f}")
    print(f"  {'PASS' if ok else 'FLAGS RAISED — investigate distribution'}")
    print("=" * w + "\n")
    return ok


def run(season: str, sims_per_game: int, config=DRAMA_M3) -> None:
    db = SessionLocal()
    sims = simulate_schedule(db, season, config, sims_per_game)
    db.close()
    report(measure(sims))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2024-25")
    p.add_argument("--sims", type=int, default=1)
    args = p.parse_args()
    run(args.season, args.sims)
