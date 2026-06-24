from typing import Optional
import random

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.database import get_db
from app.models.team import Team
from app.services.game_simulator import load_roster, simulate_game

router = APIRouter(prefix="/simulations", tags=["simulations"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class SimulateGameRequest(BaseModel):
    home_team: str = Field(..., description="Team abbreviation, e.g. 'DEN'")
    away_team: str = Field(..., description="Team abbreviation, e.g. 'GSW'")
    season: str = Field(..., description="Season string, e.g. '2024-25'")
    seed: Optional[int] = Field(None, description="RNG seed for reproducibility. Omit for a random game.")


class PlayerLine(BaseModel):
    player_id: int
    name: str
    minutes: float
    points: int
    rebounds: int
    assists: int
    steals: int
    blocks: int
    turnovers: int
    personal_fouls: int
    fgm: int
    fga: int
    fg3m: int
    fg3a: int
    ftm: int
    fta: int
    fouled_out: bool


class QuarterScores(BaseModel):
    home: list[int]
    away: list[int]


class SimulateGameResponse(BaseModel):
    season: str
    seed: int
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    quarter_scores: QuarterScores
    home_box: list[PlayerLine]
    away_box: list[PlayerLine]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_team(db: Session, abbr: str) -> Team:
    team = db.execute(select(Team).where(Team.abbreviation == abbr.upper())).scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail=f"Team '{abbr}' not found")
    return team


def _build_box(players: list[dict], box: dict) -> list[PlayerLine]:
    lines = []
    for p in players:
        s = box.get(p["id"], {})
        if s.get("min", 0) < 0.5:
            continue
        lines.append(PlayerLine(
            player_id=p["id"],
            name=p["name"],
            minutes=round(s.get("min", 0), 1),
            points=s.get("pts", 0),
            rebounds=s.get("reb", 0),
            assists=s.get("ast", 0),
            steals=s.get("stl", 0),
            blocks=s.get("blk", 0),
            turnovers=s.get("tov", 0),
            personal_fouls=s.get("pf", 0),
            fgm=s.get("fgm", 0),
            fga=s.get("fga", 0),
            fg3m=s.get("fg3m", 0),
            fg3a=s.get("fg3a", 0),
            ftm=s.get("ftm", 0),
            fta=s.get("fta", 0),
            fouled_out=s.get("fouled_out", False),
        ))
    return sorted(lines, key=lambda l: l.points, reverse=True)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/game", response_model=SimulateGameResponse)
def simulate_standalone_game(req: SimulateGameRequest, db: Session = Depends(get_db)):
    """Simulate a single game between two teams using a given season's stats.

    The result is not persisted — use this for testing matchups and exploring
    player performance without running a full season simulation.
    """
    home_team = _get_team(db, req.home_team)
    away_team = _get_team(db, req.away_team)

    home_players = load_roster(db, home_team.id, req.season)
    away_players = load_roster(db, away_team.id, req.season)

    if not home_players:
        raise HTTPException(
            status_code=422,
            detail=f"No roster data for {req.home_team} in season {req.season}. Run ingestion first.",
        )
    if not away_players:
        raise HTTPException(
            status_code=422,
            detail=f"No roster data for {req.away_team} in season {req.season}. Run ingestion first.",
        )

    seed = req.seed if req.seed is not None else random.randint(0, 2**31)
    result = simulate_game(home_players, away_players, seed=seed, season=req.season)

    return SimulateGameResponse(
        season=req.season,
        seed=seed,
        home_team=req.home_team.upper(),
        away_team=req.away_team.upper(),
        home_score=result["home_score"],
        away_score=result["away_score"],
        quarter_scores=QuarterScores(
            home=result["quarter_scores"]["home"],
            away=result["quarter_scores"]["away"],
        ),
        home_box=_build_box(home_players, result["box_score"]),
        away_box=_build_box(away_players, result["box_score"]),
    )
