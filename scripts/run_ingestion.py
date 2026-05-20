"""CLI entrypoint for the nightly ingestion job.

Usage:
    python -m scripts.run_ingestion --season 2024-25
"""

import argparse
import logging
import sys

from app.config import settings
from app.ingestion.jobs import run_full_ingestion


def main() -> int:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Run NBA data ingestion.")
    parser.add_argument("--season", required=True, help="Season string like '2024-25'.")
    args = parser.parse_args()

    counts = run_full_ingestion(args.season)
    print("Ingestion summary:", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
