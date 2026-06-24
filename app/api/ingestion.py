from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select, func, distinct

from app.database import get_db, SessionLocal
from app.models.player_season_stats import PlayerSeasonStats
from app.models.player_attributes import PlayerAttributes
from app.models.player_tendencies import PlayerTendencies

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class SeasonCoverage(BaseModel):
    season: str
    stats_players: int
    attrs_seeded: int
    tends_seeded: int
    ready: bool  # True when all three counts match


class SeedRequest(BaseModel):
    season: str
    force: bool = False  # if True, re-seeds even if already seeded


# ---------------------------------------------------------------------------
# Background task helper — runs with its own DB session so it outlives the request
# ---------------------------------------------------------------------------
def _run_seed(season: str, force: bool) -> None:
    from app.ingestion.jobs import seed_player_attributes
    db = SessionLocal()
    try:
        if force:
            db.query(PlayerAttributes).filter(PlayerAttributes.season == season).delete()
            db.query(PlayerTendencies).filter(PlayerTendencies.season == season).delete()
            db.commit()
        seed_player_attributes(db, season)
        db.commit()
    finally:
        db.close()


def _run_ingest_and_seed(season: str) -> None:
    from app.ingestion.jobs import ingest_season_stats, seed_player_attributes
    db = SessionLocal()
    try:
        ingest_season_stats(db, season)
        db.commit()
        seed_player_attributes(db, season)
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/seasons", response_model=list[SeasonCoverage])
def list_seasons(db: Session = Depends(get_db)):
    """Show which seasons have been ingested and whether attributes are seeded.

    Use this to diagnose why a simulation returns 'no roster data' errors.
    A season is ready to simulate when ready=true.
    """
    rows = db.execute(
        select(
            PlayerSeasonStats.season,
            func.count(distinct(PlayerSeasonStats.player_id)).label("stats_players"),
            func.count(distinct(PlayerAttributes.player_id)).label("attrs_seeded"),
            func.count(distinct(PlayerTendencies.player_id)).label("tends_seeded"),
        )
        .outerjoin(PlayerAttributes, (PlayerAttributes.player_id == PlayerSeasonStats.player_id)
                   & (PlayerAttributes.season == PlayerSeasonStats.season))
        .outerjoin(PlayerTendencies, (PlayerTendencies.player_id == PlayerSeasonStats.player_id)
                   & (PlayerTendencies.season == PlayerSeasonStats.season))
        .group_by(PlayerSeasonStats.season)
        .order_by(PlayerSeasonStats.season)
    ).all()

    return [
        SeasonCoverage(
            season=row.season,
            stats_players=row.stats_players,
            attrs_seeded=row.attrs_seeded,
            tends_seeded=row.tends_seeded,
            ready=row.stats_players > 0
                  and row.attrs_seeded == row.stats_players
                  and row.tends_seeded == row.stats_players,
        )
        for row in rows
    ]


@router.post("/seasons/{season}/seed", status_code=202)
def seed_season(season: str, req: SeedRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Seed (or re-seed) player attributes and tendencies for a season.

    Requires stats to already be ingested. Use force=true to wipe and
    recompute existing attributes (e.g. after rating engine changes).

    Runs in the background — check GET /ingestion/seasons to confirm completion.
    """
    stats_count = db.execute(
        select(func.count(distinct(PlayerSeasonStats.player_id)))
        .where(PlayerSeasonStats.season == season)
    ).scalar()

    if not stats_count:
        raise HTTPException(
            status_code=422,
            detail=f"No stats found for season '{season}'. Run a full ingestion first.",
        )

    background_tasks.add_task(_run_seed, season, req.force)
    return {"message": f"Seeding {season} in background ({stats_count} players). Check GET /ingestion/seasons for status."}


@router.post("/seasons/{season}/ingest", status_code=202)
def ingest_season(season: str, background_tasks: BackgroundTasks):
    """Fetch season stats from NBA API and seed attributes/tendencies.

    Makes two HTTP calls to stats.nba.com (PerGame + Advanced), then seeds.
    Runs in the background — check GET /ingestion/seasons to confirm completion.

    Note: stats.nba.com can be slow or throttled. If this times out, run
    ingestion via the CLI instead (see RUNBOOK.md).
    """
    background_tasks.add_task(_run_ingest_and_seed, season)
    return {"message": f"Ingesting {season} in background. Check GET /ingestion/seasons for status."}
