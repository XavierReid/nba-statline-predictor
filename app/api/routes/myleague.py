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
    NextGamePreview,
    PreviewRosterPlayer,
    RecentGameRow,
    UpcomingGameRow,
)
from app.services.game_simulator import load_roster
from app.services.myleague_state import (
    MyLeagueEventPayload,
    apply_events,
    filter_available_players,
)
from app.api.schemas.simulations import StandingsRow, resolve_config
from app.database import get_db
from app.models.game import Game
from app.models.myleague import MyLeagueEvent, MyLeagueState
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
    # Chronological order so compute_standings can derive per-team streaks.
    game_rows = db.execute(
        select(
            SimulatedGame.game_id, Game.home_team_id, Game.away_team_id,
            SimulatedGame.home_score, SimulatedGame.away_score,
        )
        .join(Game, SimulatedGame.game_id == Game.id)
        .where(SimulatedGame.simulation_id == sim_id)
        .order_by(Game.game_date.asc(), SimulatedGame.game_id.asc())
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
            streak=s.streak,
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

    # Next-game preview — the very next scheduled game for the controlled
    # team, with series context and top-8 rotations for both teams.
    # M-2 read-only surface; roster respects any future availability
    # events so M-4's SET_UNAVAILABLE flows through automatically.
    next_game_preview = _build_next_game_preview(db, sim_id, upcoming_rows, abbr_by_id) \
        if state.controlled_team_id is not None and upcoming_rows else None

    return MyLeagueSummaryResponse(
        state=state,
        standings=standings,
        recent_games=recent_games,
        upcoming_games=upcoming_games,
        next_game_preview=next_game_preview,
    )


def _build_next_game_preview(db, sim_id, upcoming_rows, abbr_by_id) -> NextGamePreview:
    """Build the NextGamePreview for the controlled team's next game.

    Extracted from the endpoint body to keep the read-path focused.
    Roster lookups use load_roster + apply future availability events
    from myleague_events (fold over events with applied_at_date <= game_date).
    """
    st = load_state(db, sim_id)
    controlled_id = st.controlled_team_id
    assert controlled_id is not None  # caller-guaranteed

    # Refold availability at THIS game's date, not the cursor's date, so a
    # SET_UNAVAILABLE with applied_at_date=game_date correctly hides the
    # player from the preview.
    next_gid, next_gd, next_hid, next_aid = upcoming_rows[0]
    events = db.execute(
        select(MyLeagueEvent).where(MyLeagueEvent.myleague_state_id == db.execute(
            select(MyLeagueState.id).where(MyLeagueState.simulation_id == sim_id)
        ).scalar_one())
    ).scalars().all()
    unavailable = apply_events(
        [
            MyLeagueEventPayload(
                event_type=e.event_type,
                applied_at_date=e.applied_at_date,
                payload=e.payload_json or {},
            )
            for e in events
        ],
        next_gd,
    )

    is_home = next_hid == controlled_id
    opponent_id = next_aid if is_home else next_hid
    opponent_abbr = abbr_by_id.get(opponent_id, "?")

    # Series context: matchup Nth of M, and W-L between the two teams so far.
    all_matchups = db.execute(
        select(Game.id, Game.game_date)
        .where(Game.game_date.between(*season_bounds(st.season)))
        .where(
            ((Game.home_team_id == controlled_id) & (Game.away_team_id == opponent_id)) |
            ((Game.home_team_id == opponent_id) & (Game.away_team_id == controlled_id))
        )
        .order_by(Game.game_date.asc(), Game.id.asc())
    ).all()
    matchup_ids_in_order = [gid for gid, _ in all_matchups]
    matchup_total = len(matchup_ids_in_order)
    matchup_index = matchup_ids_in_order.index(next_gid) + 1 if next_gid in matchup_ids_in_order else 0

    # Series wins so far — only completed games between the two teams.
    played_matchups = db.execute(
        select(
            SimulatedGame.game_id, Game.home_team_id,
            SimulatedGame.home_score, SimulatedGame.away_score,
        )
        .join(Game, SimulatedGame.game_id == Game.id)
        .where(SimulatedGame.simulation_id == sim_id)
        .where(SimulatedGame.game_id.in_(matchup_ids_in_order))
    ).all()
    series_wins_controlled = 0
    series_wins_opponent = 0
    for _, home_id, hs, as_ in played_matchups:
        home_won = hs > as_
        controlled_was_home = home_id == controlled_id
        controlled_won = (home_won and controlled_was_home) or (not home_won and not controlled_was_home)
        if controlled_won:
            series_wins_controlled += 1
        else:
            series_wins_opponent += 1

    # Rosters — top 8 by mpg, availability-filtered.
    stored = (db.get(SimulationRun, sim_id).parameters or {}).get("sim_config")
    cfg = SimConfig(**stored) if stored else SimConfig()
    controlled_roster_full = load_roster(
        db, controlled_id, st.season,
        depth=cfg.roster_depth, pre_negation=cfg.use_pre_negation_probs,
    )
    opponent_roster_full = load_roster(
        db, opponent_id, st.season,
        depth=cfg.roster_depth, pre_negation=cfg.use_pre_negation_probs,
    )
    controlled_avail = filter_available_players(controlled_roster_full, controlled_id, unavailable)
    opponent_avail = filter_available_players(opponent_roster_full, opponent_id, unavailable)
    def _top8(players):
        return sorted(players, key=lambda p: p.get("mpg", p.get("minutes", 0)), reverse=True)[:8]
    def _to_preview(p):
        return PreviewRosterPlayer(
            player_id=p["id"],
            name=p["name"],
            position=p.get("position", "F"),
            mpg=round(float(p.get("mpg", p.get("minutes", 0))), 1),
            is_starter=bool(p.get("is_starter", False)),
        )

    return NextGamePreview(
        game_id=next_gid,
        game_date=next_gd,
        is_home=is_home,
        opponent_abbr=opponent_abbr,
        matchup_index=matchup_index,
        matchup_total=matchup_total,
        series_wins_controlled=series_wins_controlled,
        series_wins_opponent=series_wins_opponent,
        controlled_roster=[_to_preview(p) for p in _top8(controlled_avail)],
        opponent_roster=[_to_preview(p) for p in _top8(opponent_avail)],
    )
