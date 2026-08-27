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
from typing import Optional

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
    PlayerMyLeagueStatsResponse,
    PreviewRosterPlayer,
    RecentGameRow,
    TeamDrillInRecord,
    TeamDrillInResponse,
    TeamDrillInRosterPlayer,
    UpcomingGameRow,
)
from app.models.player import Player
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
from app.models.player_season_stats import PlayerSeasonStats
from app.models.simulation import SimulatedGame, SimulationRun
from app.models.team import Team
from sqlalchemy import func

from app.services.league_simulator import compute_standings, season_bounds, validate_season_schedule
from app.services.myleague_engine import (
    MonotonicTimeError,
    MyLeagueError,
    RetroactiveEventError,
    advance_to,
    append_event,
    create_run,
    load_state,
)


def _supported_seasons(db: Session) -> list[str]:
    """Seasons with a schedule that passes the integrity gate.

    Used to enrich MyLeague-create failure messages so users can see which
    seasons are actually usable. Runs one integrity validation per ingested
    season; only called on the error path, so cost is fine.
    """
    seasons = [
        s for (s,) in db.execute(
            select(PlayerSeasonStats.season).distinct().order_by(PlayerSeasonStats.season.desc())
        ).all()
        if s
    ]
    return [s for s in seasons if validate_season_schedule(db, s).ok]
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
        if team:
            # Season-accurate identity — SEA/Sonics for 2007-08 not OKC/Thunder.
            from app.services.franchise import team_identity
            _, _, team_abbr = team_identity(
                team.id, st.season, (team.city, team.nickname, team.abbreviation)
            )
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
        supported = _supported_seasons(db)
        hint = f" Supported seasons right now: {', '.join(supported)}." if supported else ""
        raise HTTPException(status_code=422, detail=f"{e}{hint}")

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
    # Era-accurate abbreviations — SEA for 2007-08 Sonics, not modern OKC.
    # Every place that renders an abbreviation to the UI (standings / recent /
    # upcoming / NextGameCard) reads from this map, so all four surfaces stay
    # consistent with the season's actual identity.
    from app.services.franchise import team_identity as _team_identity
    abbr_by_id: dict[int, str] = {}
    if team_ids:
        for t in db.execute(select(Team).where(Team.id.in_(team_ids))).scalars().all():
            _, _, era_abbr = _team_identity(
                t.id, sim.season, (t.city, t.nickname, t.abbreviation)
            )
            abbr_by_id[t.id] = era_abbr
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
                _, _, era_abbr = _team_identity(
                    t.id, sim.season, (t.city, t.nickname, t.abbreviation)
                )
                abbr_by_id[t.id] = era_abbr
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

    # Bulk-load real-season averages for both top-8s so the card can show
    # ppg/rpg/apg alongside mpg. Explicitly labeled "Real" in the UI — the
    # MyLeague running-averages replacement is a separate design session.
    top_players = _top8(controlled_avail) + _top8(opponent_avail)
    pss_by_pid = {
        row.player_id: row
        for row in db.execute(
            select(PlayerSeasonStats)
            .where(PlayerSeasonStats.season == st.season)
            .where(PlayerSeasonStats.player_id.in_([p["id"] for p in top_players]))
        ).scalars()
    }
    def _rd(v):
        return round(float(v), 1) if v is not None else None
    def _to_preview(p):
        pss = pss_by_pid.get(p["id"])
        return PreviewRosterPlayer(
            player_id=p["id"],
            name=p["name"],
            position=p.get("position", "F"),
            mpg=round(float(p.get("mpg", p.get("minutes", 0))), 1),
            is_starter=bool(p.get("is_starter", False)),
            ppg=_rd(pss.points) if pss else None,
            rpg=_rd(pss.rebounds) if pss else None,
            apg=_rd(pss.assists) if pss else None,
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


# ---------------------------------------------------------------------------
# GET /myleague/{sim_id}/player/{player_id}
# ---------------------------------------------------------------------------

@myleague_router.get(
    "/{sim_id}/player/{player_id}",
    response_model=PlayerMyLeagueStatsResponse,
)
def get_myleague_player_stats(
    sim_id: int, player_id: int, db: Session = Depends(get_db),
):
    """Sim vs. real stat contract for one player in a MyLeague run.

    Sim block is DERIVED at request time from SimulatedPlayerLine rows —
    no cache, fully replayable. Aggregate rates are totals-first-then-
    derive; never averages-of-averages. `by_team` preserves per-team
    splits so a future "MyLeague career split" is a UI change, not a
    backend rewrite.

    Real block is the same-season PlayerSeasonStats row, unchanged. Null
    on rookies / retired / un-ingested — no cross-season substitution.

    See project-myleague-stats-contract memo for the locked design.
    """
    sim = db.get(SimulationRun, sim_id)
    if not sim or sim.scope != "myleague":
        raise HTTPException(
            status_code=404, detail=f"MyLeague simulation {sim_id} not found."
        )
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(
            status_code=404, detail=f"Player {player_id} not found."
        )
    from app.services.player_stats import derive_player_stats
    return derive_player_stats(db, sim, player)


# ---------------------------------------------------------------------------
# GET /myleague/{sim_id}/team/{team_abbr}  — M-3 read-only team drill-in
# ---------------------------------------------------------------------------

@myleague_router.get(
    "/{sim_id}/team/{team_abbr}", response_model=TeamDrillInResponse,
)
def get_myleague_team(
    sim_id: int, team_abbr: str, db: Session = Depends(get_db),
):
    """Read-only team drill-in for the MyLeague roster inspection surface.

    Returns team identity + record + roster (with per-player sim/real
    blocks) + recent games — all derived from THIS sim's persisted
    state. Roster-at-date shape (as_of_date + resolution via
    resolve_team_roster_at_date) lets M-6 slot in trade-period roster
    filtering without a contract change.

    See project-next-session-focus M-3 for the locked design.
    """
    sim = db.get(SimulationRun, sim_id)
    if not sim or sim.scope != "myleague":
        raise HTTPException(
            status_code=404, detail=f"MyLeague simulation {sim_id} not found."
        )
    state_row = db.execute(
        select(MyLeagueState).where(MyLeagueState.simulation_id == sim_id)
    ).scalar_one_or_none()
    if state_row is None:
        raise HTTPException(
            status_code=404, detail=f"MyLeague state {sim_id} missing."
        )

    # Team lookup — resolve era-accurate abbr via team_identity so
    # 07-08 SEA / 04-08 CHA-Bobcats work. Also accepts the modern abbr as
    # a fallback so an old bookmark or a stale UI reference (e.g. "OKC"
    # while browsing 2007-08) still resolves to the right team.
    from app.services.franchise import team_identity
    all_teams = db.execute(select(Team)).scalars().all()
    match = None
    era_city = era_nick = era_abbr_val = None
    normalized = team_abbr.upper()
    for t in all_teams:
        era_c, era_n, era_a = team_identity(
            t.id, sim.season, (t.city, t.nickname, t.abbreviation)
        )
        if era_a.upper() == normalized or t.abbreviation.upper() == normalized:
            match = t
            era_city, era_nick, era_abbr_val = era_c, era_n, era_a
            break
    if match is None:
        raise HTTPException(
            status_code=404,
            detail=f"Team '{team_abbr}' not found for season {sim.season!r}.",
        )

    # --- roster @ as_of_date
    from app.services.roster_at_date import resolve_team_roster_at_date, sort_roster_depth_chart
    from app.services.player_stats import derive_bulk_player_stats
    as_of = state_row.current_calendar_date
    members = resolve_team_roster_at_date(
        db, sim_id=sim_id, team_id=match.id, season=sim.season, as_of_date=as_of,
    )
    members = sort_roster_depth_chart(members)
    stats = derive_bulk_player_stats(
        db, sim, player_ids=[m.player_id for m in members],
    )
    roster = [
        TeamDrillInRosterPlayer(
            player_id=m.player_id,
            name=m.name,
            position=m.position,
            is_starter=m.is_starter,
            availability="AVAILABLE" if m.is_available else "OUT",
            sim=stats.get(m.player_id, (None, None))[0],
            real=stats.get(m.player_id, (None, None))[1],
        )
        for m in members
    ]

    # --- record derived from THIS sim's persisted games.
    #
    # Chronological over the sim's game rows so streak computation works.
    sim_games = db.execute(
        select(SimulatedGame, Game.home_team_id, Game.away_team_id, Game.game_date)
        .join(Game, Game.id == SimulatedGame.game_id)
        .where(SimulatedGame.simulation_id == sim_id)
        .where((Game.home_team_id == match.id) | (Game.away_team_id == match.id))
        .order_by(Game.game_date.asc(), SimulatedGame.game_id.asc())
    ).all()
    wins = losses = home_wins = home_losses = away_wins = away_losses = 0
    pts_for_total = pts_against_total = 0
    streak_letter = "-"
    streak_len = 0
    last_result: Optional[str] = None
    for sg, home_id, away_id, _gd in sim_games:
        is_home = home_id == match.id
        pts_for = sg.home_score if is_home else sg.away_score
        pts_against = sg.away_score if is_home else sg.home_score
        pts_for_total += pts_for
        pts_against_total += pts_against
        win = pts_for > pts_against
        if win:
            wins += 1
            if is_home:
                home_wins += 1
            else:
                away_wins += 1
        else:
            losses += 1
            if is_home:
                home_losses += 1
            else:
                away_losses += 1
        result = "W" if win else "L"
        if last_result == result:
            streak_len += 1
        else:
            streak_len = 1
        last_result = result
    total_games = wins + losses
    pct = round(wins / total_games, 3) if total_games else 0.0
    streak = f"{last_result}{streak_len}" if last_result else "-"
    record = TeamDrillInRecord(
        wins=wins,
        losses=losses,
        pct=pct,
        streak=streak,
        home_wins=home_wins,
        home_losses=home_losses,
        away_wins=away_wins,
        away_losses=away_losses,
        ppg_scored=round(pts_for_total / total_games, 1) if total_games else 0.0,
        ppg_allowed=round(pts_against_total / total_games, 1) if total_games else 0.0,
    )

    # --- recent games (last 10 for this team)
    recent_rows = list(sim_games)[-10:][::-1]  # newest first
    recent = []
    for sg, home_id, away_id, gd in recent_rows:
        home_abbr = _era_abbr(db, home_id, sim.season)
        away_abbr = _era_abbr(db, away_id, sim.season)
        recent.append(RecentGameRow(
            game_id=sg.game_id, game_date=gd,
            home_team=home_abbr, away_team=away_abbr,
            home_score=sg.home_score, away_score=sg.away_score,
            went_to_ot=sg.went_to_ot,
        ))

    return TeamDrillInResponse(
        team_id=match.id,
        team_abbr=era_abbr_val,
        team_city=era_city,
        team_nickname=era_nick,
        as_of_date=as_of,
        record=record,
        roster=roster,
        recent_games=recent,
    )


def _era_abbr(db: Session, team_id: int, season: str) -> str:
    """Season-accurate abbreviation for a team_id — SEA in 2007-08 not OKC."""
    from app.services.franchise import team_identity
    t = db.get(Team, team_id)
    if not t:
        return "?"
    _, _, abbr = team_identity(t.id, season, (t.city, t.nickname, t.abbreviation))
    return abbr
