"""Cross-era comparison — run the SAME engine on multiple seasons' rosters and
compare the league distributions it produces.

Unlike replay_schedule.py, this needs NO real games for a season: it plays a
composition-neutral double round-robin (every ordered team pairing, home/away
balanced) so the only thing that varies between eras is the roster data. That
isolates the question the roadmap's cross-era validation asks: does one engine,
fed a season's real players, reproduce that season's distinct league profile?

Where real league averages are known they are printed alongside as a reference
(provenance inline). Seasons without ingested `games`/`team_season_stats` can
still be simulated here — they just have no validated real target yet.

Usage:
    python scratch/cross_era_compare.py --seasons 2005-06 2024-25 2025-26 [--sims 1]
"""
import argparse
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models.team import Team
from app.services.game_simulator import simulate_game
from app.services.roster import load_roster
from app.services.sim_config import DRAMA_M3
from sqlalchemy import select

# Real league reference points (per-team-game unless noted). Source: Basketball
# Reference league averages. Only for eras where we can eyeball; not a validated
# target (that needs ingested games — see replay_schedule.py).
# Validated from ingested real games + team_season_stats (pace) where available.
REAL_REF = {
    "1996-97": {"avg_score": 96.9, "pace": 91.6, "home_win": 57.5},
    "2005-06": {"avg_score": 97.0, "pace": 91.7, "home_win": 60.3},
    "2024-25": {"avg_score": 113.4, "pace": 99.5, "home_win": 55.0},
    "2025-26": {"avg_score": 113.0, "pace": 100.2, "home_win": 54.0},
}


def simulate_season(db, season, sims):
    teams = db.execute(select(Team)).scalars().all()
    abbr = {t.id: t.abbreviation for t in teams}
    rosters = {}
    for t in teams:
        r = load_roster(db, t.id, season)
        if r:
            rosters[t.id] = r
    ids = sorted(rosters)

    margins, scores, paces = [], [], []
    for home in ids:
        for away in ids:
            if home == away:
                continue
            seed0 = zlib.crc32(f"{season}:{home}:{away}".encode())
            for k in range(sims):
                r = simulate_game(
                    rosters[home], rosters[away],
                    seed=seed0 + k, season=season, config=DRAMA_M3,
                    home_team_id=home, away_team_id=away, db=db,
                )
                margins.append(r["home_score"] - r["away_score"])
                scores.append((r["home_score"] + r["away_score"]) / 2)
                # Sim-internal possession events per team. NOT directly comparable
                # to the NBA pace stat (which counts a trip once; the sim's
                # second_chance/fastbreak categories don't map 1:1). Ground truth for
                # calibration is scoring vs real games (replay_schedule.py), not this.
                poss = sum(r["possession_accounting"]["counts"].values())
                paces.append(poss / 2)
    return {
        "n": len(margins),
        "teams": len(ids),
        "avg_score": sum(scores) / len(scores),
        "pace": sum(paces) / len(paces),
        "avg_margin": sum(abs(m) for m in margins) / len(margins),
        "blowout": sum(1 for m in margins if abs(m) >= 20) / len(margins) * 100,
        "close": sum(1 for m in margins if abs(m) <= 5) / len(margins) * 100,
        "home_win": sum(1 for m in margins if m > 0) / len(margins) * 100,
    }


def main(seasons, sims):
    db = SessionLocal()
    results = {}
    for s in seasons:
        print(f"  simulating {s} ...", flush=True)
        results[s] = simulate_season(db, s, sims)
    db.close()

    metrics = [("avg_score", "avg score"), ("pace", "pace (poss/tm)"),
               ("avg_margin", "avg |margin|"), ("blowout", "blowout %"),
               ("close", "close (<=5) %"), ("home_win", "home win %")]

    hdr = "  {:<16}".format("metric") + "".join(f"{s:>12}" for s in seasons)
    print(f"\n{'='*len(hdr)}")
    print(f"  Cross-era comparison — same engine (DRAMA_M3), double round-robin")
    print(f"  games/season: " + "  ".join(f"{s}={results[s]['n']}" for s in seasons))
    print(f"{'='*len(hdr)}")
    print(hdr)
    for key, label in metrics:
        print("  {:<16}".format(label) + "".join(f"{results[s][key]:>12.1f}" for s in seasons))

    print(f"\n  vs real reference (avg_score / pace / home_win; ✎ = Phase-1 roster only):")
    for s in seasons:
        ref = REAL_REF.get(s)
        if not ref:
            print(f"    {s}: no reference")
            continue
        r = results[s]
        print(f"    {s}: score sim {r['avg_score']:.1f} / real {ref['avg_score']:.1f} "
              f"({r['avg_score']-ref['avg_score']:+.1f})   "
              f"pace sim {r['pace']:.1f} / real {ref['pace']:.1f} "
              f"({r['pace']-ref['pace']:+.1f})   "
              f"home% sim {r['home_win']:.0f} / real {ref['home_win']:.0f}")
    print(f"{'='*len(hdr)}\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--seasons", nargs="+", default=["2005-06", "2024-25", "2025-26"])
    p.add_argument("--sims", type=int, default=1)
    args = p.parse_args()
    main(args.seasons, args.sims)
