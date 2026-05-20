"""Nightly ingestion job — orchestrates pulls from nba_api and upserts into Postgres.

Designed to be idempotent: running twice for the same season should not duplicate data.
"""

import logging

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.ingestion import nba_client

log = logging.getLogger(__name__)


def ingest_teams(db: Session) -> int:
    """Upsert teams. Returns count inserted/updated."""
    # TODO: call nba_client.fetch_all_teams() and upsert into Team table
    raise NotImplementedError


def ingest_active_players(db: Session) -> int:
    """Upsert active players."""
    # TODO
    raise NotImplementedError


def ingest_games_for_season(db: Session, season: str) -> int:
    """Pull games + box scores for a season. Idempotent."""
    # TODO
    raise NotImplementedError


def run_full_ingestion(season: str) -> dict[str, int]:
    """Top-level entrypoint called by scripts/run_ingestion.py."""
    log.info("Starting full ingestion for season=%s", season)
    counts: dict[str, int] = {}
    db = SessionLocal()
    try:
        counts["teams"] = ingest_teams(db)
        counts["players"] = ingest_active_players(db)
        counts["games"] = ingest_games_for_season(db, season)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    log.info("Ingestion complete: %s", counts)
    return counts
