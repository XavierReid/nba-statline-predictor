"""Purge all season simulation data from the database.

Run before each feature UAT to ensure test simulations reflect current code only.
Player/team/game ingestion data is NOT affected.

Usage:
    python scripts/purge_sims.py             # dry run — shows what would be deleted
    python scripts/purge_sims.py --confirm   # executes the purge
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import func, select
from app.database import SessionLocal
from app.models.simulation import SimulatedGame, SimulatedPlayerLine, SimulationRun


def count_rows(db) -> dict:
    return {
        "simulation_runs": db.execute(select(func.count()).select_from(SimulationRun)).scalar(),
        "simulated_games": db.execute(select(func.count()).select_from(SimulatedGame)).scalar(),
        "simulated_player_lines": db.execute(select(func.count()).select_from(SimulatedPlayerLine)).scalar(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Purge all season simulation data.")
    parser.add_argument("--confirm", action="store_true", help="Execute the purge. Omit for dry run.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        counts = count_rows(db)

        print("\nSimulation data currently in DB:")
        for table, n in counts.items():
            print(f"  {table:<28} {n:>6} rows")

        if not any(counts.values()):
            print("\nNothing to purge.")
            return 0

        if not args.confirm:
            print("\nDry run — pass --confirm to execute.")
            return 0

        print("\nPurging...")
        # Delete in FK-safe order: player lines → games → runs
        db.query(SimulatedPlayerLine).delete()
        db.query(SimulatedGame).delete()
        db.query(SimulationRun).delete()
        db.commit()

        print("  simulated_player_lines  deleted")
        print("  simulated_games         deleted")
        print("  simulation_runs         deleted")
        print("\nDone. Ingestion data (players, teams, games, ratings) untouched.")
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
