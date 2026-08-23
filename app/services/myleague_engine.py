"""MyLeague engine — stateful league simulation over the same game engine.

Layered alongside league_simulator (C-1). C-1 is "run 1230 games in a
background task"; this engine is "advance one calendar day at a time,
mutable state between advances, event-sourced availability."

The game engine (game_simulator.simulate_game) is shared. Reproducibility
is guaranteed by _game_seed(root_seed, game_id) making each game's RNG
independent of execution order — so pause-and-resume of a MyLeague run
produces byte-identical SimulatedGame rows to a batch advance covering
the same range.

Design lock (M-1a) fully documented in myleague_state.py header + the
project-next-session-focus memo.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.game import Game
from app.models.myleague import MyLeagueEvent, MyLeagueState
from app.models.simulation import SimulatedGame, SimulationRun
from app.services.game_simulator import load_roster, simulate_game
from app.services.league_simulator import season_bounds, validate_season_schedule
from app.services.myleague_state import (
    MyLeagueEventPayload,
    SeasonState,
    apply_events,
    build_state,
    filter_available_players,
    ALLOWED_EVENT_TYPES,
)
from app.services.season_simulator import _game_seed, _persist_game
from app.services.sim_config import SimConfig


class MyLeagueError(Exception):
    """Base for MyLeague-specific errors that map to 4xx at the API layer."""


class RetroactiveEventError(MyLeagueError):
    """Raised when an event would mutate state at or before an already-
    simulated game's date — violates the historical-immutability invariant."""


class MonotonicTimeError(MyLeagueError):
    """Raised when advance_to's target date < current_calendar_date."""


# ---------------------------------------------------------------------------
# Create + hydrate
# ---------------------------------------------------------------------------

def create_run(
    db: Session,
    *,
    season: str,
    seed: int,
    controlled_team_id: Optional[int],
    config: Optional[SimConfig] = None,
) -> tuple[SimulationRun, MyLeagueState]:
    """Create a new MyLeague run.

    Validates the season's schedule integrity up-front — MyLeague is only
    supported on seasons that pass the same gate C-1 does (1230/30/82 for
    modern seasons; era-appropriate for others once the validator is
    era-aware). Refusing malformed seasons here saves debugging later.
    """
    integrity = validate_season_schedule(db, season)
    if not integrity.ok:
        raise MyLeagueError(
            f"season {season!r} failed schedule integrity gate: {integrity.summary}"
        )

    params = {"sim_config": config.__dict__} if config else {}
    sim = SimulationRun(
        season=season, scope="myleague", team_id=None,
        status="running", seed=seed, parameters=params,
        games_completed=0,
    )
    db.add(sim)
    db.flush()  # get sim.id

    # Cursor starts the day BEFORE the season's ACTUAL first game so the
    # first advance_to() call picks up day 1. Using season_bounds() start
    # was previously ~3 weeks early (Oct 1 vs Oct 21 for a modern season)
    # which forced the user to click Advance many times before anything
    # happened. Query the schedule for min(game_date) instead; fall back
    # to season_bounds when no games exist (shouldn't happen after the
    # schedule-integrity gate passes, but keeps the fallback honest).
    start, _ = season_bounds(season)
    first_game_date = db.execute(
        select(Game.game_date)
        .where(Game.game_date >= start)
        .order_by(Game.game_date.asc())
        .limit(1)
    ).scalar()
    initial_cursor = (first_game_date or start) - timedelta(days=1)

    state_row = MyLeagueState(
        simulation_id=sim.id,
        controlled_team_id=controlled_team_id,
        current_calendar_date=initial_cursor,
    )
    db.add(state_row)
    db.commit()
    db.refresh(sim)
    db.refresh(state_row)
    return sim, state_row


def _load_events(db: Session, myleague_state_id: int) -> List[MyLeagueEventPayload]:
    rows = db.execute(
        select(MyLeagueEvent)
        .where(MyLeagueEvent.myleague_state_id == myleague_state_id)
        .order_by(MyLeagueEvent.applied_at_date.asc(), MyLeagueEvent.id.asc())
    ).scalars().all()
    return [
        MyLeagueEventPayload(
            event_type=e.event_type,
            applied_at_date=e.applied_at_date,
            payload=e.payload_json or {},
        )
        for e in rows
    ]


def load_state(db: Session, simulation_id: int) -> SeasonState:
    """Hydrate SeasonState from the DB."""
    sim = db.get(SimulationRun, simulation_id)
    if not sim or sim.scope != "myleague":
        raise MyLeagueError(f"simulation {simulation_id} is not a myleague run")
    state_row = db.execute(
        select(MyLeagueState).where(MyLeagueState.simulation_id == simulation_id)
    ).scalar_one()
    events = _load_events(db, state_row.id)
    return build_state(
        simulation_id=sim.id,
        season=sim.season,
        root_seed=sim.seed,
        controlled_team_id=state_row.controlled_team_id,
        current_calendar_date=state_row.current_calendar_date,
        events=events,
    )


# ---------------------------------------------------------------------------
# Event append with retroactive-mutation guard
# ---------------------------------------------------------------------------

def append_event(
    db: Session,
    *,
    simulation_id: int,
    event_type: str,
    applied_at_date: date,
    payload: dict,
) -> MyLeagueEvent:
    """Insert one event into the log with historical-immutability enforced.

    Rejects any event whose applied_at_date is <= the game_date of any
    already-simulated game in this run. Rationale: an event that would
    have affected a game we've already simulated would silently make
    history mutable — instead we surface a hard error so callers know to
    time-travel is a separate architectural feature (not M-1a).
    """
    if event_type not in ALLOWED_EVENT_TYPES:
        raise MyLeagueError(f"unknown event_type {event_type!r}")
    sim = db.get(SimulationRun, simulation_id)
    if not sim or sim.scope != "myleague":
        raise MyLeagueError(f"simulation {simulation_id} is not a myleague run")
    state_row = db.execute(
        select(MyLeagueState).where(MyLeagueState.simulation_id == simulation_id)
    ).scalar_one()

    # Historical-immutability check. Locked rule: reject any event whose
    # applied_at_date is <= the game_date of any already-simulated game
    # in this run. Rationale: even if the new event would "legitimately"
    # only apply to unsimulated games on that date, the state fold at
    # date D would then show a player as OUT while the completed game on
    # D shows them playing — history+state disagree. Simpler to keep the
    # invariant "history is what the fold at completion time produced."
    completed_dates = db.execute(
        select(Game.game_date)
        .join(SimulatedGame, SimulatedGame.game_id == Game.id)
        .where(SimulatedGame.simulation_id == simulation_id)
    ).scalars().all()
    offending = [d for d in completed_dates if d >= applied_at_date]
    if offending:
        raise RetroactiveEventError(
            f"event with applied_at_date={applied_at_date} would affect "
            f"{len(offending)} already-simulated game(s) starting at "
            f"{min(offending)}"
        )

    ev = MyLeagueEvent(
        myleague_state_id=state_row.id,
        event_type=event_type,
        applied_at_date=applied_at_date,
        payload_json=payload,
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


# ---------------------------------------------------------------------------
# advance_to — the main advance protocol
# ---------------------------------------------------------------------------

def _scheduled_games_between(
    db: Session, season: str, after_date: date, through_date: date,
    already_completed: set,
) -> List[Game]:
    """Games with after_date < game_date <= through_date not yet simulated."""
    start, end = season_bounds(season)
    # Clamp to season window for safety.
    lo = max(after_date, start - timedelta(days=1))
    hi = min(through_date, end)
    rows = db.execute(
        select(Game)
        .where(Game.game_date > lo, Game.game_date <= hi)
        .order_by(Game.game_date.asc(), Game.id.asc())
    ).scalars().all()
    return [g for g in rows if g.id not in already_completed]


def advance_to(
    db: Session,
    *,
    simulation_id: int,
    target_date: date,
    config: Optional[SimConfig] = None,
) -> SeasonState:
    """Advance the run's calendar cursor to target_date, simulating every
    scheduled game in (current_date, target_date].

    Idempotent: calling twice with the same target is a no-op the second
    time. Monotonic: refuses target < current_calendar_date. Off-days
    simply move the cursor without simulating anything.
    """
    sim = db.get(SimulationRun, simulation_id)
    if not sim or sim.scope != "myleague":
        raise MyLeagueError(f"simulation {simulation_id} is not a myleague run")
    state_row = db.execute(
        select(MyLeagueState).where(MyLeagueState.simulation_id == simulation_id)
    ).scalar_one()

    if target_date < state_row.current_calendar_date:
        raise MonotonicTimeError(
            f"target_date={target_date} < current_calendar_date="
            f"{state_row.current_calendar_date}"
        )

    if config is None:
        stored = (sim.parameters or {}).get("sim_config")
        config = SimConfig(**stored) if stored else SimConfig()

    completed_ids = set(db.execute(
        select(SimulatedGame.game_id).where(SimulatedGame.simulation_id == simulation_id)
    ).scalars().all())

    events = _load_events(db, state_row.id)

    games = _scheduled_games_between(
        db, sim.season, state_row.current_calendar_date, target_date, completed_ids,
    )

    # Cache rosters per (team_id, availability-fingerprint) — a team's
    # base roster is stable, availability changes between games only when
    # events fire on the boundary date.
    base_roster_cache: dict[int, list] = {}
    def _base_roster(team_id: int) -> list:
        if team_id not in base_roster_cache:
            base_roster_cache[team_id] = load_roster(
                db, team_id, sim.season,
                depth=config.roster_depth,
                pre_negation=config.use_pre_negation_probs,
            )
        return base_roster_cache[team_id]

    completed_this_advance = 0
    for game in games:
        # Availability at THIS game's date (deterministic per game).
        unavailable = apply_events(events, game.game_date)
        home_players = filter_available_players(
            _base_roster(game.home_team_id), game.home_team_id, unavailable,
        )
        away_players = filter_available_players(
            _base_roster(game.away_team_id), game.away_team_id, unavailable,
        )
        if len(home_players) < 5 or len(away_players) < 5:
            # Not enough eligible players — skip (surfaces later as a
            # missing game). Alternative would be to error; deferred to a
            # follow-up when injuries/DNP make this common.
            continue

        seed = _game_seed(sim.seed, game.id)
        result = simulate_game(
            home_players, away_players, seed=seed, season=sim.season,
            config=config, db=db,
            home_team_id=game.home_team_id, away_team_id=game.away_team_id,
        )
        _persist_game(db, simulation_id, game, result, home_players, away_players)
        completed_this_advance += 1

    # Update cursor + audit timestamp even on a zero-game advance.
    db.execute(
        update(MyLeagueState)
        .where(MyLeagueState.id == state_row.id)
        .values(current_calendar_date=target_date, updated_at=datetime.now(timezone.utc))
    )
    if completed_this_advance:
        db.execute(
            update(SimulationRun)
            .where(SimulationRun.id == simulation_id)
            .values(games_completed=SimulationRun.games_completed + completed_this_advance)
        )
    db.commit()

    # Season-complete detection: mark run.status='complete' when every
    # game in the season window is persisted. Frontend uses this to lock
    # the Advance button and shift into final-standings view.
    total_games = db.execute(
        select(func.count(Game.id))
        .where(Game.game_date.between(*season_bounds(sim.season)))
    ).scalar()
    persisted_games = db.execute(
        select(func.count(SimulatedGame.id))
        .where(SimulatedGame.simulation_id == simulation_id)
    ).scalar()
    if total_games and persisted_games >= total_games:
        db.execute(
            update(SimulationRun)
            .where(SimulationRun.id == simulation_id, SimulationRun.status != "complete")
            .values(status="complete", completed_at=datetime.now(timezone.utc))
        )
        db.commit()

    return load_state(db, simulation_id)
