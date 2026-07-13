"""Calibration script for game_simulator blowout tuning.

Runs N games across multiple matchups and prints a margin distribution.
Use this before and after tuning to measure the effect of drama modifiers.

Usage:
    python scratch/calibrate_simulator.py [--games 500] [--season 2025-26]
    python scratch/calibrate_simulator.py --drama-m1          # all M1 modifiers on
    python scratch/calibrate_simulator.py --disable-pace --disable-clock  # selective
"""
import argparse
import sys
import os
import zlib
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models.game import Game
from app.models.team import Team
from app.services.game_simulator import load_roster, simulate_game
from app.services.sim_config import SimConfig, DRAMA_M3, DRAMA_M3_NO_SUBTYPES
from sqlalchemy import case, func, select

# Representative matchups: strong vs strong, weak vs weak, mixed
MATCHUPS = [
    ("BOS", "LAL"),
    ("OKC", "GSW"),
    ("DEN", "MIL"),
    ("PHX", "MIA"),
    ("HOU", "MIN"),
    ("LAL", "CHA"),
    ("BOS", "DET"),
    ("GSW", "WAS"),
    ("OKC", "UTA"),
    ("DEN", "SAS"),
]


def fetch_real_targets(db, season: str) -> Optional[dict]:
    """Pull actual season averages from the games table for data-grounded targets.
    Returns None if no final games exist for the season (e.g. future seasons)."""
    year = season.split("-")[0][-2:]
    prefix = f"002{year}"
    rows = db.execute(
        select(
            func.avg((Game.home_score + Game.away_score) / 2.0).label("avg_team_score"),
            func.avg(func.abs(Game.home_score - Game.away_score)).label("avg_margin"),
            func.avg(case((func.abs(Game.home_score - Game.away_score) >= 20, 1), else_=0)).label("blowout_rate"),
            func.avg(case((func.abs(Game.home_score - Game.away_score) <= 5, 1), else_=0)).label("close_rate"),
            func.avg(case((Game.home_score > Game.away_score, 1), else_=0)).label("home_win_rate"),
            func.count().label("total"),
        ).where(
            Game.id.like(f"{prefix}%"),
            Game.status == "final",
            Game.home_score.isnot(None),
        )
    ).one()
    if not rows.total:
        return None
    return {
        "avg_team_score": round(float(rows.avg_team_score), 1),
        "avg_margin": round(float(rows.avg_margin), 1),
        "blowout_rate": round(float(rows.blowout_rate) * 100, 1),
        "close_rate": round(float(rows.close_rate) * 100, 1),
        "home_win_rate": round(float(rows.home_win_rate) * 100, 1),
        "total_games": rows.total,
    }


def run_calibration(n_games: int, season: str, config: SimConfig) -> None:
    db = SessionLocal()
    targets = fetch_real_targets(db, season)

    # Pre-load all rosters and team IDs
    rosters: dict = {}
    team_ids: dict = {}
    for home_abbr, away_abbr in MATCHUPS:
        for abbr in (home_abbr, away_abbr):
            if abbr not in rosters:
                team = db.execute(
                    select(Team).where(Team.abbreviation == abbr)
                ).scalar_one_or_none()
                if not team:
                    print(f"Team {abbr} not found — skipping")
                    continue
                players = load_roster(db, team.id, season)
                if not players:
                    print(f"No roster for {abbr} in {season} — skipping")
                    continue
                rosters[abbr] = players
                team_ids[abbr] = team.id

    margins = []
    home_wins = 0
    ot_games = 0
    games_played = 0
    matchup_avgs = []

    games_per_matchup = max(1, n_games // len(MATCHUPS))

    for home_abbr, away_abbr in MATCHUPS:
        if home_abbr not in rosters or away_abbr not in rosters:
            continue
        home_players = rosters[home_abbr]
        away_players = rosters[away_abbr]

        matchup_home_scores = []
        matchup_away_scores = []
        # crc32 is deterministic across processes; hash() is randomized per process
        # (PYTHONHASHSEED) and made calibration runs non-reproducible.
        matchup_hash = zlib.crc32(f"{home_abbr}:{away_abbr}".encode()) % 100000
        for i in range(games_per_matchup):
            seed = matchup_hash * 1000 + i
            result = simulate_game(
                home_players, away_players, seed=seed, season=season,
                config=config,
                home_team_id=team_ids.get(home_abbr),
                away_team_id=team_ids.get(away_abbr),
                db=db,
            )
            margin = result["home_score"] - result["away_score"]
            margins.append(margin)
            matchup_home_scores.append(result["home_score"])
            matchup_away_scores.append(result["away_score"])
            if margin > 0:
                home_wins += 1
            if result["went_to_ot"]:
                ot_games += 1
            games_played += 1

        matchup_avgs.append((
            home_abbr, away_abbr,
            sum(matchup_home_scores) / len(matchup_home_scores),
            sum(matchup_away_scores) / len(matchup_away_scores),
        ))

    db.close()

    if not margins:
        print("No games completed — check season/roster availability")
        return

    total = len(margins)
    abs_margins = [abs(m) for m in margins]
    avg_margin = sum(abs_margins) / total

    buckets = [
        ("1–5 (very close)",    1,  5),
        ("6–10 (close)",        6, 10),
        ("11–15 (moderate)",   11, 15),
        ("16–20 (comfortable)",16, 20),
        ("21–29 (blowout)",    21, 29),
        ("30+ (blowout)",      30, 999),
    ]

    # Show which modifiers are active
    all_toggles = (
        "use_second_chance","use_fast_break",
        "use_team_defense","use_strategic_foul","use_momentum",
        "use_fatigue","use_foul_trouble","use_clutch",
        "use_player_variance","use_team_oreb",
        "use_catch_up","use_garbage_time",
        "use_shot_subtypes","use_contest_model","use_positional_matchups",
    )
    active = [f for f in all_toggles if getattr(config, f, False)]
    modifier_label = ", ".join(active) if active else "none (baseline)"

    if targets:
        t_margin = f"real {season}: {targets['avg_margin']}"
        t_home = f"real {season}: {targets['home_win_rate']}%"
        t_blowout = f"real {season}: {targets['blowout_rate']}%"
        t_score = f"real {season}: {targets['avg_team_score']}"
    else:
        t_margin = "no real data (future season)"
        t_home = "historical ~54%"
        t_blowout = "historical ~22%"
        t_score = "historical ~113"

    avg_sim_score = sum(
        (h + a) / 2
        for _, _, h, a in matchup_avgs
    ) / len(matchup_avgs) if matchup_avgs else 0

    print(f"\n{'='*60}")
    print(f"  Calibration: {total} games  |  Season: {season}")
    print(f"  Active modifiers: {modifier_label}")
    print(f"{'='*60}")
    print(f"  Avg team score        : {avg_sim_score:.1f} pts  ({t_score})")
    print(f"  Avg margin of victory : {avg_margin:.1f} pts  ({t_margin})")
    print(f"  Home win rate         : {home_wins/total*100:.1f}%  ({t_home})")
    print(f"  OT rate               : {ot_games/total*100:.1f}%  (historical ~6%)")
    print()
    print(f"  {'Margin bucket':<26} {'Count':>6}  {'%':>6}  Bar")
    print(f"  {'-'*55}")
    for label, lo, hi in buckets:
        count = sum(1 for m in abs_margins if lo <= m <= hi)
        pct = count / total * 100
        bar = "█" * int(pct / 2)
        print(f"  {label:<26} {count:>6}  {pct:>5.1f}%  {bar}")

    blowout_count = sum(1 for m in abs_margins if m >= 20)
    print(f"\n  Blowout rate (20+)    : {blowout_count/total*100:.1f}%  ({t_blowout})")

    print(f"\n  {'Matchup':<12} {'Home avg':>9}  {'Away avg':>9}  {'Diff':>6}")
    print(f"  {'-'*42}")
    for home_abbr, away_abbr, home_avg, away_avg in matchup_avgs:
        print(f"  {home_abbr} vs {away_abbr:<6} {home_avg:>9.1f}  {away_avg:>9.1f}  {abs(home_avg-away_avg):>5.1f}pt")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=500)
    parser.add_argument("--season", type=str, default="2025-26")
    parser.add_argument("--drama-m1", action="store_true", help="Enable all Drama M1 modifiers")
    parser.add_argument("--drama-m2", action="store_true", help="Enable all Drama M2 modifiers (M1 + momentum)")
    parser.add_argument("--drama-m3", action="store_true", help="Enable all Drama M3 modifiers (M2 + variance + team OREB + catch-up + subtypes)")
    parser.add_argument("--drama-m3-no-subtypes", action="store_true", help="Drama M3 without M3d sub-type flags (isolation baseline)")
    parser.add_argument("--disable-pace", action="store_true")
    parser.add_argument("--disable-clock", action="store_true")
    parser.add_argument("--disable-second-chance", action="store_true")
    parser.add_argument("--disable-fast-break", action="store_true")
    parser.add_argument("--disable-team-defense", action="store_true")
    parser.add_argument("--disable-strategic-foul", action="store_true")
    parser.add_argument("--disable-momentum", action="store_true")
    args = parser.parse_args()

    if args.drama_m3:
        config = DRAMA_M3
    elif args.drama_m3_no_subtypes:
        config = DRAMA_M3_NO_SUBTYPES
    elif args.drama_m2:
        from dataclasses import replace
        config = replace(
            DRAMA_M3,
            use_second_chance=not args.disable_second_chance,
            use_fast_break=not args.disable_fast_break,
            use_team_defense=not args.disable_team_defense,
            use_strategic_foul=not args.disable_strategic_foul,
            use_momentum=not args.disable_momentum,
        )
    elif args.drama_m1:
        config = SimConfig(
            use_second_chance=not args.disable_second_chance,
            use_fast_break=not args.disable_fast_break,
            use_team_defense=not args.disable_team_defense,
            use_strategic_foul=not args.disable_strategic_foul,
        )
    else:
        config = SimConfig(
            use_second_chance=False,
            use_fast_break=False,
            use_team_defense=False,
            use_strategic_foul=False,
        )

    run_calibration(args.games, args.season, config)
