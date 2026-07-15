"""Season simulation routes — create, start, inspect, list, delete, and events."""
import random
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.game import Game
from app.models.simulation import SimulatedGame, SimulatedPlayerLine, SimulationRun
from app.models.team import Team
from app.services.events import flatten_and_enrich
from app.services.game_simulator import load_roster, simulate_game
from app.services.season_simulator import _game_seed, run_season_simulation
from app.services.sim_config import SimConfig
from app.api.helpers import get_team, sim_game_is_win
from app.api.schemas.simulations import (
    CreateSimulationRequest,
    SimulatedGameSummary,
    SimulationCreatedResponse,
    SimulationStatusResponse,
    SimulationSummary,
    StartSimulationRequest,
    PossessionEvent,
    resolve_config,
)

season_router = APIRouter()


@season_router.post("/", response_model=SimulationCreatedResponse, status_code=201)
def create_simulation(req: CreateSimulationRequest, db: Session = Depends(get_db)):
    """Create a season simulation run (status: pending).

    Validates that the team and season exist but does not start the simulation.
    Call POST /simulations/{id}/start to begin.
    """
    team = get_team(db, req.team, req.season)

    if not load_roster(db, team.id, req.season):
        raise HTTPException(
            status_code=422,
            detail=f"No roster data for {req.team} in {req.season}. Run ingestion first."
        )

    running = db.execute(
        select(SimulationRun).where(SimulationRun.status == "running")
    ).scalar_one_or_none()
    if running:
        raise HTTPException(
            status_code=409,
            detail=f"Simulation {running.id} is already running. Cancel it before creating a new one."
        )

    seed = req.seed if req.seed is not None else random.randint(0, 2**31)
    from dataclasses import asdict
    initial_cfg = resolve_config(req.config)
    sim = SimulationRun(
        season=req.season, team_id=team.id, seed=seed, status="pending",
        parameters={"sim_config": asdict(initial_cfg)},
    )
    db.add(sim)
    db.commit()
    db.refresh(sim)

    return SimulationCreatedResponse(
        id=sim.id, team=req.team.upper(), season=req.season, seed=seed, status=sim.status
    )


@season_router.post("/{sim_id}/start", response_model=SimulationCreatedResponse)
def start_simulation(
    sim_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    req: Optional[StartSimulationRequest] = None,
):
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

    result = db.execute(
        update(SimulationRun)
        .where(SimulationRun.id == sim_id, SimulationRun.status == "pending")
        .values(status="running")
    )
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=409, detail="Another simulation started concurrently.")

    from dataclasses import asdict
    if req and req.config:
        cfg = resolve_config(req.config)
        db.execute(
            update(SimulationRun)
            .where(SimulationRun.id == sim_id)
            .values(parameters={"sim_config": asdict(cfg)})
        )
        db.commit()
    else:
        stored = (sim.parameters or {}).get("sim_config")
        cfg = SimConfig(**stored) if stored else SimConfig()
    background_tasks.add_task(run_season_simulation, sim_id, cfg)

    team = db.get(Team, sim.team_id)
    return SimulationCreatedResponse(
        id=sim.id, team=team.abbreviation, season=sim.season, seed=sim.seed, status="running"
    )


@season_router.get("/{sim_id}", response_model=SimulationStatusResponse)
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

    wins = losses = None
    games_summary = None
    if sim.status == "complete":
        wins = losses = 0
        games_summary = []
        for sg in simulated_games:
            real_game = db.get(Game, sg.game_id)
            is_home = real_game.home_team_id == sim.team_id
            win = (sg.home_score > sg.away_score) if is_home else (sg.away_score > sg.home_score)
            if win:
                wins += 1
            else:
                losses += 1
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
        wins=wins,
        losses=losses,
        created_at=sim.created_at,
        completed_at=sim.completed_at,
        games=games_summary,
    )


@season_router.post("/{sim_id}/cancel", status_code=200)
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


@season_router.get("/", response_model=list[SimulationSummary])
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
            wins = sum(1 for sg in sim_games if sim_game_is_win(db, sg, sim.team_id))
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


@season_router.delete("/{sim_id}", status_code=200)
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


@season_router.get("/{sim_id}/games/{game_id}/events", response_model=list[PossessionEvent])
def season_game_events(sim_id: int, game_id: str, db: Session = Depends(get_db)):
    """Return the full play-by-play for a game from a completed season simulation.

    Re-simulates the game on demand using the stored seed — deterministic,
    no event storage required.
    """
    sim = db.get(SimulationRun, sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail=f"Simulation {sim_id} not found.")

    sg = db.execute(
        select(SimulatedGame)
        .where(SimulatedGame.simulation_id == sim_id, SimulatedGame.game_id == game_id)
    ).scalar_one_or_none()
    if not sg:
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found in simulation {sim_id}.")

    real_game = db.get(Game, game_id)
    home_players = load_roster(db, real_game.home_team_id, sim.season)
    away_players = load_roster(db, real_game.away_team_id, sim.season)

    stored = (sim.parameters or {}).get("sim_config")
    cfg = SimConfig(**stored) if stored else SimConfig()

    seed = _game_seed(sim.seed, game_id)
    result = simulate_game(
        home_players, away_players,
        seed=seed, season=sim.season,
        steps=200, capture_descriptions=True,
        config=cfg,
    )

    home_ids = {p["id"] for p in home_players}
    return flatten_and_enrich(result["chunk_events"], home_ids)
