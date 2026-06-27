"""Game simulation routes — standalone game, step-through, and game events."""
import random

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.events import build_name_map, flatten_and_enrich
from app.services.game_simulator import simulate_game
from app.services.stepthrough_store import create_session, peek_events, pop_next_chunk
from app.api.helpers import build_box, build_stepthrough_response, load_rosters
from app.api.schemas.simulations import (
    PossessionEvent,
    QuarterScores,
    SimulateGameRequest,
    SimulateGameResponse,
    StepThroughRequest,
    StepThroughResponse,
    resolve_config,
)

game_router = APIRouter()


@game_router.post("/game", response_model=SimulateGameResponse)
def simulate_standalone_game(req: SimulateGameRequest, db: Session = Depends(get_db)):
    """Simulate a single game between two teams using a given season's stats.

    The result is not persisted — use this for testing matchups and exploring
    player performance without running a full season simulation.
    """
    home_players, away_players = load_rosters(db, req.home_team, req.away_team, req.season)
    seed = req.seed if req.seed is not None else random.randint(0, 2**31)
    cfg = resolve_config(req.config)
    result = simulate_game(
        home_players, away_players, seed=seed, season=req.season, config=cfg,
        capture_descriptions=req.include_pbp,
    )

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
        home_box=build_box(home_players, result["box_score"]),
        away_box=build_box(away_players, result["box_score"]),
        events=(
            flatten_and_enrich([result["events"]], home_player_ids=set(p["id"] for p in home_players))
            if req.include_pbp else None
        ),
    )


@game_router.post("/game/stepthrough", response_model=StepThroughResponse)
def start_stepthrough(req: StepThroughRequest, db: Session = Depends(get_db)):
    """Start a step-through session for a game.

    Simulates the full game upfront and caches it server-side. Returns a token
    and the first step. Call GET /simulations/game/stepthrough/{token}/next to
    advance. Sessions expire after 1 hour or when the final step is consumed.

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
    home_players, away_players = load_rosters(db, req.home_team, req.away_team, req.season)
    seed = req.seed if req.seed is not None else random.randint(0, 2**31)
    cfg = resolve_config(req.config)
    result = simulate_game(home_players, away_players, seed=seed, season=req.season, steps=req.steps, config=cfg)

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
    return build_stepthrough_response(token, pop_next_chunk(token))


@game_router.get("/game/stepthrough/{token}/next", response_model=StepThroughResponse)
def next_stepthrough(token: str):
    """Advance to the next step in a step-through session.

    Returns 404 if the token is unknown or expired (including after the final step).
    Check complete=true in the response to know when the game has ended.
    """
    data = pop_next_chunk(token)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found or expired.")
    return build_stepthrough_response(token, data)


@game_router.get("/game/stepthrough/{token}/events", response_model=list[PossessionEvent])
def stepthrough_events(token: str):
    """Return all possession events from game start through the current step.

    Read-only — does not advance the cursor. Returns 404 if the token is unknown or expired.
    """
    data = peek_events(token)
    if data is None:
        raise HTTPException(status_code=404, detail="Session not found or expired.")

    home_ids = {p["id"] for p in data["home_players"]}
    name_map = build_name_map(data["home_players"], data["away_players"])
    return flatten_and_enrich(data["chunk_events"], home_ids, name_map)
