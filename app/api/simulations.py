from typing import Optional
import random
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import delete, select, update

from app.database import get_db
from app.models.game import Game
from app.models.simulation import SimulatedGame, SimulatedPlayerLine, SimulationRun
from app.models.team import Team
from app.services.game_simulator import load_roster, simulate_game
from app.services.season_simulator import run_season_simulation
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
    plus_minus: int
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
    elapsed_minutes: float
    quarter: int
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
            plus_minus=s.get("plus_minus", 0),
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
        elapsed_minutes=chunk["elapsed_minutes"],
        quarter=chunk["quarter"],
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
    if home_team_abbr.upper() == away_team_abbr.upper():
        raise HTTPException(status_code=422, detail="Home and away teams must be different.")
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
    advance. Sessions expire after 1 hour or when the final step is consumed.

    **Steps reference** — chunks are time-based (`48 / steps` minutes each).
    OT adds proportional extra steps; `total_steps` in the response reflects the
    final count after OT resolution.

    | steps | chunk duration | reg round-trips | best for              |
    |-------|----------------|-----------------|-----------------------|
    | 2     | 24 min         | 2               | halftime split        |
    | 4     | 12 min         | 4               | quarters (default)    |
    | 8     | 6 min          | 8               | scoring runs          |
    | 12    | 4 min          | 12              | TV timeout segments   |
    | 24    | 2 min          | 24              | 2-minute segments     |
    | 48    | 1 min          | 48              | minute-by-minute      |
    | 96    | 30 sec         | 96              | half-minute intervals |
    """
    home_players, away_players = _load_rosters(db, req.home_team, req.away_team, req.season)
    seed = req.seed if req.seed is not None else random.randint(0, 2**31)
    result = simulate_game(home_players, away_players, seed=seed, season=req.season, steps=req.steps)

    token = create_session(
        chunks=result["chunks"],
        chunk_events=result["chunk_events"],
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


# ---------------------------------------------------------------------------
# Season simulation schemas
# ---------------------------------------------------------------------------

class CreateSimulationRequest(BaseModel):
    team: str = Field(..., description="Team abbreviation, e.g. 'BOS'")
    season: str = Field(..., description="Season string, e.g. '2025-26'")
    seed: Optional[int] = Field(None, description="Master RNG seed. Omit for random.")


class SimulationCreatedResponse(BaseModel):
    id: int
    team: str
    season: str
    seed: int
    status: str


class SimulatedGameSummary(BaseModel):
    game_id: str
    game_date: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    went_to_ot: bool
    win: bool  # from the simulated team's perspective


class SimulationStatusResponse(BaseModel):
    id: int
    team: str
    season: str
    seed: int
    status: str
    games_completed: int
    total_games: int
    created_at: datetime
    completed_at: Optional[datetime]
    games: Optional[list[SimulatedGameSummary]] = None


# ---------------------------------------------------------------------------
# Season simulation endpoints
# ---------------------------------------------------------------------------

@router.post("/", response_model=SimulationCreatedResponse, status_code=201)
def create_simulation(req: CreateSimulationRequest, db: Session = Depends(get_db)):
    """Create a season simulation run (status: pending).

    Validates that the team and season exist but does not start the simulation.
    Call POST /simulations/{id}/start to begin.
    """
    team = _get_team(db, req.team)

    # Verify roster data exists for this season
    if not load_roster(db, team.id, req.season):
        raise HTTPException(
            status_code=422,
            detail=f"No roster data for {req.team} in {req.season}. Run ingestion first."
        )

    # Block if another sim is already running
    running = db.execute(
        select(SimulationRun).where(SimulationRun.status == "running")
    ).scalar_one_or_none()
    if running:
        raise HTTPException(
            status_code=409,
            detail=f"Simulation {running.id} is already running. Cancel it before creating a new one."
        )

    seed = req.seed if req.seed is not None else random.randint(0, 2**31)
    sim = SimulationRun(season=req.season, team_id=team.id, seed=seed, status="pending")
    db.add(sim)
    db.commit()
    db.refresh(sim)

    return SimulationCreatedResponse(
        id=sim.id, team=req.team.upper(), season=req.season, seed=seed, status=sim.status
    )


@router.post("/{sim_id}/start", response_model=SimulationCreatedResponse)
def start_simulation(sim_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Start a pending simulation run.

    Transitions status pending → running and enqueues the background task.
    Returns 409 if another run is already in progress.
    Returns 422 if the run is not in pending status.
    """
    sim = db.get(SimulationRun, sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail=f"Simulation {sim_id} not found.")
    if sim.status != "pending":
        raise HTTPException(
            status_code=422,
            detail=f"Simulation {sim_id} is '{sim.status}' — only pending runs can be started."
        )

    # Atomic guard: only flip to running if still pending (prevents double-start race)
    result = db.execute(
        update(SimulationRun)
        .where(SimulationRun.id == sim_id, SimulationRun.status == "pending")
        .values(status="running")
    )
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=409, detail="Another simulation started concurrently.")

    background_tasks.add_task(run_season_simulation, sim_id)

    team = db.get(Team, sim.team_id)
    return SimulationCreatedResponse(
        id=sim.id, team=team.abbreviation, season=sim.season, seed=sim.seed, status="running"
    )


@router.get("/{sim_id}", response_model=SimulationStatusResponse)
def get_simulation(sim_id: int, db: Session = Depends(get_db)):
    """Get simulation status and results.

    While running, returns progress (games_completed / total_games).
    When complete, also returns the per-game results list.
    """
    sim = db.get(SimulationRun, sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail=f"Simulation {sim_id} not found.")

    team = db.get(Team, sim.team_id)
    simulated_games = db.execute(
        select(SimulatedGame)
        .where(SimulatedGame.simulation_id == sim_id)
        .join(Game, SimulatedGame.game_id == Game.id)
        .order_by(Game.game_date)
    ).scalars().all()

    total_games = (sim.parameters or {}).get("total_games", 82)

    games_summary = None
    if sim.status == "complete":
        games_summary = []
        for sg in simulated_games:
            real_game = db.get(Game, sg.game_id)
            is_home = real_game.home_team_id == sim.team_id
            win = (sg.home_score > sg.away_score) if is_home else (sg.away_score > sg.home_score)
            games_summary.append(SimulatedGameSummary(
                game_id=sg.game_id,
                game_date=str(real_game.game_date),
                home_team=real_game.home_team.abbreviation,
                away_team=real_game.away_team.abbreviation,
                home_score=sg.home_score,
                away_score=sg.away_score,
                went_to_ot=sg.went_to_ot,
                win=win,
            ))

    return SimulationStatusResponse(
        id=sim.id,
        team=team.abbreviation,
        season=sim.season,
        seed=sim.seed,
        status=sim.status,
        games_completed=sim.games_completed,
        total_games=total_games,
        created_at=sim.created_at,
        completed_at=sim.completed_at,
        games=games_summary,
    )


@router.post("/{sim_id}/cancel", status_code=200)
def cancel_simulation(sim_id: int, db: Session = Depends(get_db)):
    """Cancel a running or pending simulation.

    Sets status to cancelled. The background task checks this flag before
    each game and stops gracefully on next iteration.
    """
    sim = db.get(SimulationRun, sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail=f"Simulation {sim_id} not found.")
    if sim.status in ("complete", "cancelled"):
        raise HTTPException(
            status_code=422,
            detail=f"Simulation {sim_id} is already '{sim.status}'."
        )

    db.execute(
        update(SimulationRun)
        .where(SimulationRun.id == sim_id)
        .values(status="cancelled")
    )
    db.commit()
    return {"id": sim_id, "status": "cancelled"}


# ---------------------------------------------------------------------------
# List + delete
# ---------------------------------------------------------------------------

class SimulationSummary(BaseModel):
    id: int
    team: str
    season: str
    status: str
    games_completed: int
    total_games: int
    wins: Optional[int] = None
    losses: Optional[int] = None
    created_at: datetime
    completed_at: Optional[datetime]


@router.get("/", response_model=list[SimulationSummary])
def list_simulations(db: Session = Depends(get_db)):
    """List all simulation runs, most recent first."""
    runs = db.execute(
        select(SimulationRun).order_by(SimulationRun.created_at.desc())
    ).scalars().all()

    summaries = []
    for sim in runs:
        team = db.get(Team, sim.team_id)
        total_games = (sim.parameters or {}).get("total_games", 82)
        wins = losses = None
        if sim.status == "complete":
            sim_games = db.execute(
                select(SimulatedGame).where(SimulatedGame.simulation_id == sim.id)
            ).scalars().all()
            wins = sum(
                1 for sg in sim_games
                if _sim_game_is_win(db, sg, sim.team_id)
            )
            losses = len(sim_games) - wins
        summaries.append(SimulationSummary(
            id=sim.id,
            team=team.abbreviation,
            season=sim.season,
            status=sim.status,
            games_completed=sim.games_completed,
            total_games=total_games,
            wins=wins,
            losses=losses,
            created_at=sim.created_at,
            completed_at=sim.completed_at,
        ))
    return summaries


@router.delete("/{sim_id}", status_code=200)
def delete_simulation(sim_id: int, db: Session = Depends(get_db)):
    """Delete a simulation run and all its results.

    Blocked if the simulation is currently running — cancel it first.
    """
    sim = db.get(SimulationRun, sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail=f"Simulation {sim_id} not found.")
    if sim.status == "running":
        raise HTTPException(
            status_code=422,
            detail=f"Simulation {sim_id} is running. Cancel it before deleting."
        )

    db.execute(
        delete(SimulatedPlayerLine).where(
            SimulatedPlayerLine.simulated_game_id.in_(
                select(SimulatedGame.id).where(SimulatedGame.simulation_id == sim_id)
            )
        )
    )
    db.execute(delete(SimulatedGame).where(SimulatedGame.simulation_id == sim_id))
    db.execute(delete(SimulationRun).where(SimulationRun.id == sim_id))
    db.commit()
    return {"id": sim_id, "deleted": True}


def _sim_game_is_win(db: Session, sg: SimulatedGame, team_id: int) -> bool:
    real_game = db.get(Game, sg.game_id)
    is_home = real_game.home_team_id == team_id
    return (sg.home_score > sg.away_score) if is_home else (sg.away_score > sg.home_score)
