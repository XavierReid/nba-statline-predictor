from typing import Optional
import random

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.database import get_db
from app.models.team import Team
from app.services.game_simulator import load_roster, simulate_game
from app.services.stepthrough_store import create_session, pop_next_chunk

router = APIRouter(prefix="/simulations", tags=["simulations"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class SimulateGameRequest(BaseModel):
    home_team: str = Field(..., description="Team abbreviation, e.g. 'DEN'")
    away_team: str = Field(..., description="Team abbreviation, e.g. 'GSW'")
    season: str = Field(..., description="Season string, e.g. '2024-25'")
    seed: Optional[int] = Field(None, description="RNG seed for reproducibility. Omit for a random game.")


class StepThroughRequest(BaseModel):
    home_team: str = Field(..., description="Team abbreviation, e.g. 'DEN'")
    away_team: str = Field(..., description="Team abbreviation, e.g. 'GSW'")
    season: str = Field(..., description="Season string, e.g. '2024-25'")
    seed: Optional[int] = Field(None, description="RNG seed for reproducibility.")
    steps: int = Field(4, ge=1, le=200, description="Number of steps to split the game into. Default 4 (quarters).")


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


class StepThroughResponse(BaseModel):
    token: str
    step: int
    total_steps: int
    complete: bool
    season: str
    seed: int
    home_team: str
    away_team: str
    home_score: int
    away_score: int
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


def _build_stepthrough_response(token: str, data: dict) -> StepThroughResponse:
    chunk = data["chunk"]
    return StepThroughResponse(
        token=token,
        step=data["step"],
        total_steps=data["total_steps"],
        complete=data["complete"],
        season=data["season"],
        seed=data["seed"],
        home_team=data["home_team"],
        away_team=data["away_team"],
        home_score=chunk["home_score"],
        away_score=chunk["away_score"],
        home_box=_build_box(data["home_players"], chunk["box"]),
        away_box=_build_box(data["away_players"], chunk["box"]),
    )


def _load_rosters(db: Session, home_team_abbr: str, away_team_abbr: str, season: str) -> tuple:
    home_team = _get_team(db, home_team_abbr)
    away_team = _get_team(db, away_team_abbr)
    home_players = load_roster(db, home_team.id, season)
    away_players = load_roster(db, away_team.id, season)
    if not home_players:
        raise HTTPException(422, detail=f"No roster data for {home_team_abbr} in season {season}. Run ingestion first.")
    if not away_players:
        raise HTTPException(422, detail=f"No roster data for {away_team_abbr} in season {season}. Run ingestion first.")
    return home_players, away_players


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/game", response_model=SimulateGameResponse)
def simulate_standalone_game(req: SimulateGameRequest, db: Session = Depends(get_db)):
    """Simulate a single game between two teams using a given season's stats.

    The result is not persisted — use this for testing matchups and exploring
    player performance without running a full season simulation.
    """
    home_players, away_players = _load_rosters(db, req.home_team, req.away_team, req.season)
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


@router.post("/game/stepthrough", response_model=StepThroughResponse)
def start_stepthrough(req: StepThroughRequest, db: Session = Depends(get_db)):
    """Start a step-through session for a game.

    Simulates the full game upfront and caches it server-side. Returns a token
    and the first step. Call GET /simulations/game/stepthrough/{token}/next to
    advance through the game. Sessions expire after 1 hour or when complete.
    """
    home_players, away_players = _load_rosters(db, req.home_team, req.away_team, req.season)
    seed = req.seed if req.seed is not None else random.randint(0, 2**31)
    result = simulate_game(home_players, away_players, seed=seed, season=req.season, steps=req.steps)

    token = create_session(
        chunks=result["chunks"],
        home_players=home_players,
        away_players=away_players,
        home_team=req.home_team.upper(),
        away_team=req.away_team.upper(),
        season=req.season,
        seed=seed,
    )
    return _build_stepthrough_response(token, pop_next_chunk(token))


@router.get("/game/stepthrough/{token}/next", response_model=StepThroughResponse)
def next_stepthrough(token: str):
    """Advance to the next step in a step-through session.

    Returns 404 if the token is unknown or expired (including after the final step).
    Check complete=true in the response to know when the game has ended.
    """
    data = pop_next_chunk(token)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found or expired.")
    return _build_stepthrough_response(token, data)
