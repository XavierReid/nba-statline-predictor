"""Replay the real season schedule and compare sim vs real distributions.

Eliminates matchup-composition bias: every real final game (matchup + home team)
is simulated with a deterministic per-game seed, then sim and real margin/scoring
distributions are compared directly. Also reports per-team strength calibration
(sim win% and net margin vs real) to test whether team-strength effects are too
strong in the engine (SIMULATION_GAPS.md gap 1.3).

Usage:
    python scratch/replay_schedule.py [--season 2025-26] [--sims-per-game 2]
"""
import argparse
import os
import sys
import zlib
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models.game import Game
from app.models.team import Team
from app.services.game_simulator import simulate_game
from app.services.roster import load_roster
from app.services.sim_config import DRAMA_M3
from sqlalchemy import select


def dist_stats(margins, scores):
    n = len(margins)
    return {
        "avg_score": sum(scores) / len(scores),
        "avg_margin": sum(abs(m) for m in margins) / n,
        "blowout": sum(1 for m in margins if abs(m) >= 20) / n * 100,
        "close": sum(1 for m in margins if abs(m) <= 5) / n * 100,
        "home_win": sum(1 for m in margins if m > 0) / n * 100,
    }


def main(season: str, sims_per_game: int) -> None:
    db = SessionLocal()
    year = season.split("-")[0][-2:]
    games = db.execute(
        select(Game).where(
            Game.id.like(f"002{year}%"),
            Game.status == "final",
            Game.home_score.isnot(None),
        )
    ).scalars().all()

    teams = db.execute(select(Team)).scalars().all()
    abbr = {t.id: t.abbreviation for t in teams}
    rosters = {}
    for t in teams:
        r = load_roster(db, t.id, season)
        if r:
            rosters[t.id] = r

    real_margins, real_scores = [], []
    sim_margins, sim_scores = [], []
    # per-team: [real_wins, real_games, real_net_margin, sim_wins, sim_games, sim_net_margin]
    team_acc = defaultdict(lambda: [0, 0, 0.0, 0, 0, 0.0])
    skipped = 0

    for g in games:
        if g.home_team_id not in rosters or g.away_team_id not in rosters:
            skipped += 1
            continue
        rm = g.home_score - g.away_score
        real_margins.append(rm)
        real_scores.append((g.home_score + g.away_score) / 2)
        for side, won, net in ((g.home_team_id, rm > 0, rm), (g.away_team_id, rm < 0, -rm)):
            team_acc[side][0] += int(won)
            team_acc[side][1] += 1
            team_acc[side][2] += net

        base_seed = zlib.crc32(str(g.id).encode())
        for k in range(sims_per_game):
            r = simulate_game(
                rosters[g.home_team_id], rosters[g.away_team_id],
                seed=base_seed + k, season=season, config=DRAMA_M3,
                home_team_id=g.home_team_id, away_team_id=g.away_team_id, db=db,
            )
            sm = r["home_score"] - r["away_score"]
            sim_margins.append(sm)
            sim_scores.append((r["home_score"] + r["away_score"]) / 2)
            for side, won, net in ((g.home_team_id, sm > 0, sm), (g.away_team_id, sm < 0, -sm)):
                team_acc[side][3] += int(won)
                team_acc[side][4] += 1
                team_acc[side][5] += net

    db.close()

    real = dist_stats(real_margins, real_scores)
    sim = dist_stats(sim_margins, sim_scores)

    print(f"\n{'='*66}")
    print(f"  Schedule replay: {len(real_margins)} real games x{sims_per_game} sims  ({season}, DRAMA_M3)")
    if skipped:
        print(f"  Skipped (no roster): {skipped}")
    print(f"{'='*66}")
    print(f"  {'metric':<16} {'real':>8} {'sim':>8} {'diff':>8}")
    for key, label in [("avg_score", "avg score"), ("avg_margin", "avg |margin|"),
                       ("blowout", "blowout %"), ("close", "close (<=5) %"),
                       ("home_win", "home win %")]:
        print(f"  {label:<16} {real[key]:>8.1f} {sim[key]:>8.1f} {sim[key]-real[key]:>+8.1f}")

    # Margin histogram comparison
    buckets = [(1, 5), (6, 10), (11, 15), (16, 20), (21, 29), (30, 99)]
    print(f"\n  {'|margin| bucket':<16} {'real %':>8} {'sim %':>8}")
    for lo, hi in buckets:
        rp = sum(1 for m in real_margins if lo <= abs(m) <= hi) / len(real_margins) * 100
        sp = sum(1 for m in sim_margins if lo <= abs(m) <= hi) / len(sim_margins) * 100
        print(f"  {f'{lo}-{hi}':<16} {rp:>8.1f} {sp:>8.1f}")

    # Team strength calibration: sim vs real win% — slope > 1 means the engine
    # amplifies team strength differences beyond reality.
    rows = []
    for tid, (rw, rg, rnet, sw, sg, snet) in team_acc.items():
        if rg and sg:
            rows.append((abbr[tid], rw / rg * 100, sw / sg * 100, rnet / rg, snet / sg))
    rows.sort(key=lambda x: -x[1])
    print(f"\n  {'team':<6} {'real W%':>8} {'sim W%':>8} {'real net':>9} {'sim net':>8}")
    for ab, rwp, swp, rn, sn in rows:
        print(f"  {ab:<6} {rwp:>8.1f} {swp:>8.1f} {rn:>+9.1f} {sn:>+8.1f}")

    def fit_slope(pairs):
        mr = sum(p[0] for p in pairs) / len(pairs)
        ms = sum(p[1] for p in pairs) / len(pairs)
        cov = sum((p[0] - mr) * (p[1] - ms) for p in pairs)
        var = sum((p[0] - mr) ** 2 for p in pairs)
        return cov / var if var else float("nan")

    # rows sorted by real W% desc. Bottom tier is confounded by tanking/rest
    # (sim plays full-effort top-10 rosters); top tier is the trustworthy signal.
    top10, bot10 = rows[:10], rows[-10:]
    wp_pairs = lambda rs: [(r[1], r[2]) for r in rs]
    net_pairs = lambda rs: [(r[3], r[4]) for r in rs]
    spread_r = max(r[1] for r in rows) - min(r[1] for r in rows)
    spread_s = max(r[2] for r in rows) - min(r[2] for r in rows)
    print(f"\n  Strength slopes (1.0 = calibrated; bottom tier tank-confounded):")
    print(f"    win%%   : all {fit_slope(wp_pairs(rows)):.2f}   top-10 {fit_slope(wp_pairs(top10)):.2f}   bottom-10 {fit_slope(wp_pairs(bot10)):.2f}")
    print(f"    net mgn: all {fit_slope(net_pairs(rows)):.2f}   top-10 {fit_slope(net_pairs(top10)):.2f}   bottom-10 {fit_slope(net_pairs(bot10)):.2f}")
    print(f"  Win%% spread: real {spread_r:.0f} pts, sim {spread_s:.0f} pts")
    print(f"{'='*66}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=str, default="2025-26")
    parser.add_argument("--sims-per-game", type=int, default=2)
    args = parser.parse_args()
    main(args.season, args.sims_per_game)
