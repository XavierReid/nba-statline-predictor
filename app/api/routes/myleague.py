"""MyLeague HTTP surface (M-1b).

Thin router over app/services/myleague_engine. Four endpoints:

  POST   /myleague/                 create a new run
  POST   /myleague/{id}/advance     move cursor to target_date, simulate any games in range
  POST   /myleague/{id}/events      append a state-mutation event to the log
  GET    /myleague/{id}             full state hydration (state + standings + recent games)

Error mapping:
  MyLeagueError            → 422 (invalid input / scope mismatch / unknown event type)
  MonotonicTimeError       → 422 (target_date < current_calendar_date)
  RetroactiveEventError    → 422 (event would affect an already-simulated game)
  missing SimulationRun    → 404
"""
import random

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas.myleague import (
    AdvanceRequest,
    AppendEventRequest,
    CreateMyLeagueRequest,
    MyLeagueEventResponse,
    MyLeagueStateResponse,
    MyLeagueSummaryResponse,
    RecentGameRow,
    UpcomingGameRow,
)
from app.api.schemas.simulations import StandingsRow, resolve_config
from app.database import get_db
from app.models.game import Game
from app.models.simulation import SimulatedGame, SimulationRun
from app.models.team import Team
from sqlalchemy import func

from app.services.league_simulator import compute_standings, season_bounds
from app.services.myleague_engine import (
    MonotonicTimeError,
    MyLeagueError,
    RetroactiveEventError,
    advance_to,
    append_event,
    create_run,
    load_state,
)
from app.services.sim_config import SimConfig


myleague_router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_state(db: Session, simulation_id: int) -> MyLeagueStateResponse:
    """Hydrate the base-state block from DB — used by every response.

    Includes controlled_team_abbr so the UI can render the team's identity
    (name, logo, colors) without a separate teams-list lookup and without
    waiting for standings to populate (empty at run-creation time).
    """
    st = load_state(db, simulation_id)
    sim = db.get(SimulationRun, simulation_id)
    team_abbr = None
    if st.controlled_team_id is not None:
        team = db.get(Team, st.controlled_team_id)
        team_abbr = team.abbreviation if team else None
    total_games = db.execute(
        select(func.count(Game.id))
        .where(Game.game_date.between(*season_bounds(st.season)))
    ).scalar() or 0
    return MyLeagueStateResponse(
        simulation_id=st.simulation_id,
        season=st.season,
        root_seed=st.root_seed,
        controlled_team_id=st.controlled_team_id,
        controlled_team_abbr=team_abbr,
        current_calendar_date=st.current_calendar_date,
        games_completed=sim.games_completed if sim else 0,
        total_games=total_games,
        status=sim.status if sim else "unknown",
    )


# ---------------------------------------------------------------------------
# POST /myleague/  — create a new run
# ---------------------------------------------------------------------------

@myleague_router.post("/", response_model=MyLeagueStateResponse, status_code=201)
def create_myleague(req: CreateMyLeagueRequest, db: Session = Depends(get_db)):
    seed = req.seed if req.seed is not None else random.randint(0, 2**31)
    config = resolve_config(req.config) if req.config else SimConfig()

    if req.controlled_team_id is not None:
        team = db.get(Team, req.controlled_team_id)
        if not team:
            raise HTTPException(
                status_code=422,
                detail=f"controlled_team_id={req.controlled_team_id} does not exist",
            )

    try:
        sim, _state = create_run(
            db,
            season=req.season,
            seed=seed,
            controlled_team_id=req.controlled_team_id,
            config=config,
        )
    except MyLeagueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return _base_state(db, sim.id)


# ---------------------------------------------------------------------------
# POST /myleague/{id}/advance
# ---------------------------------------------------------------------------

@myleague_router.post("/{sim_id}/advance", response_model=MyLeagueStateResponse)
def advance_myleague(
    sim_id: int, req: AdvanceRequest, db: Session = Depends(get_db),
):
    sim = db.get(SimulationRun, sim_id)
    if not sim or sim.scope != "myleague":
        raise HTTPException(
            status_code=404, detail=f"MyLeague simulation {sim_id} not found."
        )
    stored = (sim.parameters or {}).get("sim_config")
    config = SimConfig(**stored) if stored else SimConfig()
    try:
        advance_to(db, simulation_id=sim_id, target_date=req.target_date, config=config)
    except MonotonicTimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except MyLeagueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _base_state(db, sim_id)


# ---------------------------------------------------------------------------
# POST /myleague/{id}/events
# ---------------------------------------------------------------------------

@myleague_router.post(
    "/{sim_id}/events", response_model=MyLeagueEventResponse, status_code=201,
)
def append_myleague_event(
    sim_id: int, req: AppendEventRequest, db: Session = Depends(get_db),
):
    sim = db.get(SimulationRun, sim_id)
    if not sim or sim.scope != "myleague":
        raise HTTPException(
            status_code=404, detail=f"MyLeague simulation {sim_id} not found."
        )
    try:
        ev = append_event(
            db,
            simulation_id=sim_id,
            event_type=req.event_type,
            applied_at_date=req.applied_at_date,
            payload=req.payload,
        )
    except RetroactiveEventError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except MyLeagueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return MyLeagueEventResponse(
        id=ev.id,
        event_type=ev.event_type,
        applied_at_date=ev.applied_at_date,
        payload=ev.payload_json or {},
        created_at=ev.created_at,
    )


# ---------------------------------------------------------------------------
# GET /myleague/{id}  — full state hydration
# ---------------------------------------------------------------------------

_RECENT_GAMES_LIMIT = 10
_UPCOMING_GAMES_LIMIT = 5


@myleague_router.get("/{sim_id}", response_model=MyLeagueSummaryResponse)
def get_myleague(sim_id: int, db: Session = Depends(get_db)):
    sim = db.get(SimulationRun, sim_id)
    if not sim or sim.scope != "myleague":
        raise HTTPException(
            status_code=404, detail=f"MyLeague simulation {sim_id} not found."
        )

    # Base state.
    state = _base_state(db, sim_id)

    # Standings: derived from persisted games; reuses league_simulator helper.
    game_rows = db.execute(
        select(
            SimulatedGame.game_id, Game.home_team_id, Game.away_team_id,
            SimulatedGame.home_score, SimulatedGame.away_score,
        )
        .join(Game, SimulatedGame.game_id == Game.id)
        .where(SimulatedGame.simulation_id == sim_id)
    ).all()
    team_ids: set[int] = set()
    for _, hid, aid, _, _ in game_rows:
        team_ids.add(hid); team_ids.add(aid)
    abbr_by_id = {
        t.id: t.abbreviation for t in db.execute(
            select(Team).where(Team.id.in_(team_ids))
        ).scalars().all()
    } if team_ids else {}
    tuples = [
        (gid, hid, aid, hs, ascore, abbr_by_id.get(hid, "?"), abbr_by_id.get(aid, "?"))
        for gid, hid, aid, hs, ascore in game_rows
    ]
    computed = compute_standings(tuples)
    standings = [
        StandingsRow(
            rank=s.rank, team_id=s.team_id, team_abbr=s.team_abbr,
            wins=s.wins, losses=s.losses, pct=s.pct, gb=s.gb,
        )
        for s in computed
    ]

    # Recent games: last N completed, ordered by date DESC then id ASC.
    recent_rows = db.execute(
        select(
            SimulatedGame.game_id, Game.game_date,
            Game.home_team_id, Game.away_team_id,
            SimulatedGame.home_score, SimulatedGame.away_score,
            SimulatedGame.went_to_ot,
        )
        .join(Game, SimulatedGame.game_id == Game.id)
        .where(SimulatedGame.simulation_id == sim_id)
        .order_by(Game.game_date.desc(), SimulatedGame.game_id.asc())
        .limit(_RECENT_GAMES_LIMIT)
    ).all()
    recent_games = [
        RecentGameRow(
            game_id=gid,
            game_date=gd,
            home_team=abbr_by_id.get(hid, "?"),
            away_team=abbr_by_id.get(aid, "?"),
            home_score=hs,
            away_score=ascore,
            went_to_ot=ot,
        )
        for gid, gd, hid, aid, hs, ascore, ot in recent_rows
    ]

    # Upcoming games for the controlled team — the next N scheduled games
    # after the current cursor that involve controlled_team_id, so the
    # dashboard can show a "you play next: OKC in 2 days" preview. Uses
    # the Game table directly (not SimulatedGame) since these haven't been
    # simulated yet. Empty when no controlled team is set.
    upcoming_games: list[UpcomingGameRow] = []
    if state.controlled_team_id is not None:
        from sqlalchemy import or_
        upcoming_rows = db.execute(
            select(Game.id, Game.game_date, Game.home_team_id, Game.away_team_id)
            .where(
                Game.game_date > state.current_calendar_date,
                or_(
                    Game.home_team_id == state.controlled_team_id,
                    Game.away_team_id == state.controlled_team_id,
                ),
            )
            .order_by(Game.game_date.asc(), Game.id.asc())
            .limit(_UPCOMING_GAMES_LIMIT)
        ).all()
        # Add teams referenced by upcoming games to the abbr map if missing
        # (the earlier abbr_by_id only covers teams in completed games).
        missing_ids = {hid for _, _, hid, _ in upcoming_rows} | {aid for _, _, _, aid in upcoming_rows}
        missing_ids -= set(abbr_by_id.keys())
        if missing_ids:
            for t in db.execute(select(Team).where(Team.id.in_(missing_ids))).scalars().all():
                abbr_by_id[t.id] = t.abbreviation
        upcoming_games = [
            UpcomingGameRow(
                game_id=gid,
                game_date=gd,
                home_team=abbr_by_id.get(hid, "?"),
                away_team=abbr_by_id.get(aid, "?"),
            )
            for gid, gd, hid, aid in upcoming_rows
        ]

    return MyLeagueSummaryResponse(
        state=state,
        standings=standings,
        recent_games=recent_games,
        upcoming_games=upcoming_games,
    )
