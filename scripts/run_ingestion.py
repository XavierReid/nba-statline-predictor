"""CLI entrypoint for the nightly ingestion job.

Usage:
    python -m scripts.run_ingestion --season 2024-25
    python -m scripts.run_ingestion --season 2025-26 --games-only
"""

import argparse
import logging
import sys

from app.config import settings
from app.database import SessionLocal
from app.ingestion.jobs import ingest_games_for_season, ingest_team_season_stats, run_full_ingestion


def main() -> int:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Run NBA data ingestion.")
    parser.add_argument("--season", required=True, help="Season string like '2024-25'.")
    parser.add_argument("--games-only", action="store_true",
                        help="Only re-ingest the games schedule (skips players, stats, attributes).")
    parser.add_argument("--team-stats-only", action="store_true",
                        help="Only re-ingest team season stats (pace, ratings).")
    args = parser.parse_args()

    if args.team_stats_only:
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
