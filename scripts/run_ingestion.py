"""CLI entrypoint for the nightly ingestion job.

Usage:
    python -m scripts.run_ingestion --season 2024-25
    python -m scripts.run_ingestion --season 2025-26 --games-only
    python -m scripts.run_ingestion --all           # full ingestion for every SEASONS entry
    python -m scripts.run_ingestion --verify         # audit coverage across all seasons (no writes)
    python -m scripts.run_ingestion --season 2024-25 --shot-defense-only  # backfill shot data
"""

import argparse
import logging
import sys

from app.config import settings
from app.database import SessionLocal
from app.ingestion.jobs import (
    SEASONS, ingest_games_for_season, ingest_shot_defense, ingest_team_season_stats,
    run_full_ingestion, verify_season_coverage,
)


def main() -> int:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Run NBA data ingestion.")
    parser.add_argument("--season", help="Season string like '2024-25'.")
    parser.add_argument("--all", action="store_true",
                        help="Full ingestion for every season in jobs.SEASONS.")
    parser.add_argument("--verify", action="store_true",
                        help="Audit data coverage across all seasons (no writes) and exit non-zero on gaps.")
    parser.add_argument("--games-only", action="store_true",
                        help="Only re-ingest the games schedule (skips players, stats, attributes).")
    parser.add_argument("--team-stats-only", action="store_true",
                        help="Only re-ingest team season stats (pace, ratings).")
    parser.add_argument("--shot-defense-only", action="store_true",
                        help="Only re-ingest shot-location + defense data for --season (targeted backfill).")
    args = parser.parse_args()

    if args.verify:
        db = SessionLocal()
        try:
            all_gaps = [g for s in SEASONS for g in verify_season_coverage(db, s)]
        finally:
            db.close()
        if all_gaps:
            print("COVERAGE GAPS:")
            for g in all_gaps:
                print(f"  - {g}")
            return 1
        print(f"All {len(SEASONS)} seasons have complete shot-location coverage.")
        return 0

    if args.all:
        for s in SEASONS:
            print(f"=== full ingestion: {s} ===")
            print("  ", run_full_ingestion(s))
        return 0

    if not args.season:
        parser.error("--season is required unless --all or --verify is given")

    if args.shot_defense_only:
        db = SessionLocal()
        try:
            count = ingest_shot_defense(db, args.season)
            db.commit()
        finally:
            db.close()
        print(f"Shot-location/defense ingestion complete: {count} rows upserted")
    elif args.team_stats_only:
        db = SessionLocal()
        try:
            count = ingest_team_season_stats(db, args.season)
            db.commit()
        finally:
            db.close()
        print(f"Team season stats ingestion complete: {count} rows upserted")
    elif args.games_only:
        db = SessionLocal()
        try:
            count = ingest_games_for_season(db, args.season)
            db.commit()
        finally:
            db.close()
        print(f"Games ingestion complete: {count} rows upserted")
    else:
        counts = run_full_ingestion(args.season)
        print("Ingestion summary:", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
