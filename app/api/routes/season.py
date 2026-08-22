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
from app.services.league_simulator import season_bounds, compute_standings, run_league_simulation
from app.services.sim_config import SimConfig
from app.api.helpers import build_box, get_team, sim_game_is_win
from app.api.schemas.simulations import (
    CreateLeagueSimulationRequest,
    CreateSimulationRequest,
    PlayerAveragesRow,
    PossessionEvent,
    QuarterScores,
    SeasonAveragesResponse,
    SimulateGameResponse,
    SimulatedGameSummary,
    SimulationCreatedResponse,
    SimulationStatusResponse,
    SimulationSummary,
    StandingsResponse,
    StandingsRow,
    StartSimulationRequest,
    TeamAveragesResponse,
    resolve_config,
)
from app.models.player import Player
from app.models.player_season_stats import PlayerSeasonStats
from app.models.team_season_stats import TeamSeasonStats

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
        season=req.season, scope="team", team_id=team.id,
        seed=seed, status="pending",
        parameters={"sim_config": asdict(initial_cfg)},
    )
    db.add(sim)
    db.commit()
    db.refresh(sim)

    return SimulationCreatedResponse(
        id=sim.id, team=req.team.upper(), scope="team",
        season=req.season, seed=seed, status=sim.status,
    )


@season_router.post("/league", response_model=SimulationCreatedResponse, status_code=201)
def create_league_simulation(
    req: CreateLeagueSimulationRequest, db: Session = Depends(get_db),
):
    """Create a full 30-team 1230-game league simulation (status: pending).

    Root seed derives per-game seeds deterministically. Config is captured at
    creation time. Call POST /simulations/{id}/start to begin.
    """
    running = db.execute(
        select(SimulationRun).where(SimulationRun.status == "running")
    ).scalar_one_or_none()
    if running:
        raise HTTPException(
            status_code=409,
            detail=f"Simulation {running.id} is already running. Cancel it first."
        )

    seed = req.seed if req.seed is not None else random.randint(0, 2**31)
    from dataclasses import asdict
    # Default to drama-m3-season if no config supplied (the audit-validated
    # season preset — availability ON, roster_depth=15).
    if req.config is None:
        from app.api.schemas.simulations import SimConfigRequest
        req_config = SimConfigRequest(preset="drama-m3-season")
    else:
        req_config = req.config
    initial_cfg = resolve_config(req_config)

    sim = SimulationRun(
        season=req.season, scope="league", team_id=None,
        seed=seed, status="pending",
        parameters={"sim_config": asdict(initial_cfg)},
    )
    db.add(sim)
    db.commit()
    db.refresh(sim)

    return SimulationCreatedResponse(
        id=sim.id, team=None, scope="league",
        season=req.season, seed=seed, status=sim.status,
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
    # Dispatch to the appropriate service based on scope
    if sim.scope == "league":
        background_tasks.add_task(run_league_simulation, sim_id, cfg)
        return SimulationCreatedResponse(
            id=sim.id, team=None, scope="league",
            season=sim.season, seed=sim.seed, status="running",
        )
    else:
        background_tasks.add_task(run_season_simulation, sim_id, cfg)
        team = db.get(Team, sim.team_id)
        return SimulationCreatedResponse(
            id=sim.id, team=team.abbreviation, scope="team",
            season=sim.season, seed=sim.seed, status="running",
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

    team = db.get(Team, sim.team_id) if sim.team_id else None
    simulated_games = db.execute(
        select(SimulatedGame)
        .where(SimulatedGame.simulation_id == sim_id)
        .join(Game, SimulatedGame.game_id == Game.id)
        .order_by(Game.game_date)
    ).scalars().all()

    default_total = 1230 if sim.scope == "league" else 82
    total_games = (sim.parameters or {}).get("total_games", default_total)

    wins = losses = None
    games_summary = None
    if sim.status == "complete":
        games_summary = []
        # Team-scope: compute the team's W-L. League-scope: leave wins/losses
        # None (standings are a C-2 endpoint) but still list every game with
        # its full home/away score.
        if sim.scope == "team":
            wins = losses = 0
        for sg in simulated_games:
            real_game = db.get(Game, sg.game_id)
            win_flag = False
            if sim.scope == "team":
                is_home = real_game.home_team_id == sim.team_id
                win_flag = (sg.home_score > sg.away_score) if is_home else (sg.away_score > sg.home_score)
                if win_flag:
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
                win=win_flag,
            ))

    return SimulationStatusResponse(
        id=sim.id,
        team=team.abbreviation if team else None,
        scope=sim.scope,
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
    """List all simulation runs, most recent first.

    Fetches everything the response needs in three bulk queries (runs, all
    games for team-scope sims, all Game rows those reference) instead of
    the original N+1 pattern (per-sim games query + per-game db.get(Game)),
    which took 3.7s on ~20 completed sims and dominated the Season Sim tab
    load time.
    """
    runs = db.execute(
        select(SimulationRun).order_by(SimulationRun.created_at.desc())
    ).scalars().all()

    # Bulk-load teams referenced by any sim (usually all 30).
    team_ids = {sim.team_id for sim in runs if sim.team_id is not None}
    team_by_id = (
        {t.id: t for t in db.execute(select(Team).where(Team.id.in_(team_ids))).scalars()}
        if team_ids else {}
    )

    # Bulk-load SimulatedGame rows for every completed TEAM-scope sim in one query.
    completed_team_sim_ids = [
        sim.id for sim in runs
        if sim.status == "complete" and sim.scope == "team"
    ]
    sim_games_by_sim: dict[int, list[SimulatedGame]] = {sid: [] for sid in completed_team_sim_ids}
    if completed_team_sim_ids:
        rows = db.execute(
            select(SimulatedGame).where(SimulatedGame.simulation_id.in_(completed_team_sim_ids))
        ).scalars().all()
        for sg in rows:
            sim_games_by_sim.setdefault(sg.simulation_id, []).append(sg)

        # Bulk-load Game rows once (need home_team_id only, to know if the sim's
        # team was home). db.get() inside the loop was the N+1 hot spot.
        game_ids = {sg.game_id for sg in rows}
        game_home_by_id = dict(
            db.execute(
                select(Game.id, Game.home_team_id).where(Game.id.in_(game_ids))
            ).all()
        )
    else:
        game_home_by_id = {}

    summaries = []
    for sim in runs:
        team = team_by_id.get(sim.team_id) if sim.team_id else None
        default_total = 1230 if sim.scope == "league" else 82
        total_games = (sim.parameters or {}).get("total_games", default_total)
        wins = losses = None
        if sim.status == "complete" and sim.scope == "team":
            sim_games = sim_games_by_sim.get(sim.id, [])
            wins = 0
            for sg in sim_games:
                is_home = game_home_by_id.get(sg.game_id) == sim.team_id
                if (sg.home_score > sg.away_score) if is_home else (sg.away_score > sg.home_score):
                    wins += 1
            losses = len(sim_games) - wins
        summaries.append(SimulationSummary(
            id=sim.id,
            team=team.abbreviation if team else None,
            scope=sim.scope,
            season=sim.season,
            seed=sim.seed,
            status=sim.status,
            games_completed=sim.games_completed,
            total_games=total_games,
            wins=wins,
            losses=losses,
            created_at=sim.created_at,
            completed_at=sim.completed_at,
        ))
    return summaries


@season_router.get(
    "/{sim_id}/team/{team_abbr}/games", response_model=list[SimulatedGameSummary],
)
def get_league_team_games(sim_id: int, team_abbr: str, db: Session = Depends(get_db)):
    """League-sim team drill-in: the team's game list from a league simulation.

    Returns the same SimulatedGameSummary shape as the team-scoped
    `games` array, so the existing team-season UI can consume it unchanged.
    Rejects scope=team (that's what GET /simulations/{id} already exposes).
    """
    sim = db.get(SimulationRun, sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail=f"Simulation {sim_id} not found.")
    if sim.scope != "league":
        raise HTTPException(
            status_code=422,
            detail=f"Simulation {sim_id} has scope={sim.scope!r}; "
                   "this endpoint is only for league-scope sims.",
        )

    team = db.execute(
        select(Team).where(Team.abbreviation == team_abbr.upper())
    ).scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail=f"Team '{team_abbr}' not found.")

    # Games the team participated in (home OR away), in date order.
    rows = db.execute(
        select(SimulatedGame, Game)
        .join(Game, SimulatedGame.game_id == Game.id)
        .where(SimulatedGame.simulation_id == sim_id)
        .where((Game.home_team_id == team.id) | (Game.away_team_id == team.id))
        .order_by(Game.game_date)
    ).all()

    out: list = []
    for sg, g in rows:
        is_home = g.home_team_id == team.id
        win = (sg.home_score > sg.away_score) if is_home else (sg.away_score > sg.home_score)
        out.append(SimulatedGameSummary(
            game_id=sg.game_id,
            game_date=str(g.game_date),
            home_team=g.home_team.abbreviation,
            away_team=g.away_team.abbreviation,
            home_score=sg.home_score,
            away_score=sg.away_score,
            went_to_ot=sg.went_to_ot,
            win=win,
        ))
    return out


@season_router.get("/{sim_id}/standings", response_model=StandingsResponse)
def get_standings(sim_id: int, db: Session = Depends(get_db)):
    """League-sim standings. Derived from persisted SimulatedGame rows.

    Provisional when the run is incomplete — `is_complete=False` in the
    response and `games_completed<total_games`. UI should label as such.
    Rejects scope=team with 422.
    """
    sim = db.get(SimulationRun, sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail=f"Simulation {sim_id} not found.")
    if sim.scope != "league":
        raise HTTPException(
            status_code=422,
            detail=f"Simulation {sim_id} has scope={sim.scope!r}; "
                   "standings are only defined for league-scope simulations.",
        )

    # Pull all persisted games for the sim in one shot; join to Game for team ids.
    rows = db.execute(
        select(
            SimulatedGame.game_id, Game.home_team_id, Game.away_team_id,
            SimulatedGame.home_score, SimulatedGame.away_score,
        )
        .join(Game, SimulatedGame.game_id == Game.id)
        .where(SimulatedGame.simulation_id == sim_id)
    ).all()

    # Team abbreviations in one query (30 teams max in a league sim).
    team_ids = set()
    for _, hid, aid, _, _ in rows:
        team_ids.add(hid); team_ids.add(aid)
    abbr_by_id = {
        t.id: t.abbreviation for t in db.execute(
            select(Team).where(Team.id.in_(team_ids))
        ).scalars().all()
    } if team_ids else {}

    # Feed compute_standings as (game_id, home_id, away_id, hscore, ascore,
    # home_abbr, away_abbr) tuples.
    tuples = [
        (gid, hid, aid, hs, as_, abbr_by_id.get(hid, "?"), abbr_by_id.get(aid, "?"))
        for gid, hid, aid, hs, as_ in rows
    ]
    computed = compute_standings(tuples)

    total_games = (sim.parameters or {}).get("total_games", 1230)
    return StandingsResponse(
        sim_id=sim.id,
        season=sim.season,
        is_complete=(sim.status == "complete"),
        games_completed=sim.games_completed,
        total_games=total_games,
        standings=[
            StandingsRow(
                rank=s.rank, team_id=s.team_id, team_abbr=s.team_abbr,
                wins=s.wins, losses=s.losses, pct=s.pct, gb=s.gb,
            )
            for s in computed
        ],
    )


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


@season_router.get("/{sim_id}/averages", response_model=SeasonAveragesResponse)
def season_averages(sim_id: int, db: Session = Depends(get_db)):
    """Sim season averages side-by-side with real NBA anchors for the run's team.

    Sim aggregates from persisted `SimulatedPlayerLine` rows (game-count divisor
    is played rows only, since sub-0.5-min lines aren't persisted per the
    intentional filter in season_simulator._persist_game). Real anchors from
    PlayerSeasonStats + TeamSeasonStats when available; None/dict-of-real-keys
    when the ingestion doesn't cover the player.
    """
    sim = db.get(SimulationRun, sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail=f"Simulation {sim_id} not found.")
    if sim.status not in ("complete", "cancelled"):
        raise HTTPException(
            status_code=422,
            detail=f"Simulation {sim_id} is '{sim.status}' — averages only available for complete or cancelled runs.",
        )

    team = db.get(Team, sim.team_id)
    # Persisted lines for this sim, filtered to the team's players.
    game_ids = [g.id for g in db.execute(
        select(SimulatedGame).where(SimulatedGame.simulation_id == sim_id)
    ).scalars().all()]
    lines = list(db.execute(
        select(SimulatedPlayerLine).where(
            SimulatedPlayerLine.simulated_game_id.in_(game_ids),
            SimulatedPlayerLine.team_id == sim.team_id,
        )
    ).scalars().all()) if game_ids else []

    # Team-level sim aggregates (sum across the team's persisted lines, per-game).
    n_games = len(game_ids)
    team_scored = 0
    team_allowed = 0
    for sg in db.execute(select(SimulatedGame).where(SimulatedGame.simulation_id == sim_id)).scalars().all():
        real_game = db.get(Game, sg.game_id)
        if real_game.home_team_id == sim.team_id:
            team_scored += sg.home_score
            team_allowed += sg.away_score
        else:
            team_scored += sg.away_score
            team_allowed += sg.home_score

    def _sum(attr: str) -> int:
        return sum(getattr(l, attr) for l in lines)

    team_sim = {
        "gp": n_games,
        "ppg": round(team_scored / n_games, 2) if n_games else 0,
        "opp_ppg": round(team_allowed / n_games, 2) if n_games else 0,
        "fga": round(_sum("fga") / n_games, 2) if n_games else 0,
        "fgm": round(_sum("fgm") / n_games, 2) if n_games else 0,
        "fta": round(_sum("fta") / n_games, 2) if n_games else 0,
        "ftm": round(_sum("ftm") / n_games, 2) if n_games else 0,
        "fg3a": round(_sum("fg3a") / n_games, 2) if n_games else 0,
        "fg3m": round(_sum("fg3m") / n_games, 2) if n_games else 0,
        "pf": round(_sum("personal_fouls") / n_games, 2) if n_games else 0,
        "tov": round(_sum("turnovers") / n_games, 2) if n_games else 0,
        "stl": round(_sum("steals") / n_games, 2) if n_games else 0,
        "blk": round(_sum("blocks") / n_games, 2) if n_games else 0,
        "ast": round(_sum("assists") / n_games, 2) if n_games else 0,
        "reb": round(_sum("rebounds") / n_games, 2) if n_games else 0,
    }

    team_real_row = db.execute(
        select(TeamSeasonStats).where(
            TeamSeasonStats.team_id == sim.team_id,
            TeamSeasonStats.season == sim.season,
        )
    ).scalar_one_or_none()
    team_real = {}
    if team_real_row:
        team_real = {
            "pace": team_real_row.pace,
            "off_rating": team_real_row.off_rating,
            "def_rating": team_real_row.def_rating,
            "oreb_pct": team_real_row.oreb_pct,
        }

    # Per-player aggregates.
    per_player: dict[int, dict[str, float]] = {}
    per_player_gp: dict[int, int] = {}
    for l in lines:
        agg = per_player.setdefault(l.player_id, {
            "minutes": 0.0, "points": 0, "rebounds": 0, "assists": 0,
            "steals": 0, "blocks": 0, "turnovers": 0, "personal_fouls": 0,
            "fgm": 0, "fga": 0, "fg3m": 0, "fg3a": 0, "ftm": 0, "fta": 0,
        })
        agg["minutes"] += l.minutes
        agg["points"] += l.points
        agg["rebounds"] += l.rebounds
        agg["assists"] += l.assists
        agg["steals"] += l.steals
        agg["blocks"] += l.blocks
        agg["turnovers"] += l.turnovers
        agg["personal_fouls"] += l.personal_fouls
        agg["fgm"] += l.fgm
        agg["fga"] += l.fga
        agg["fg3m"] += l.fg3m
        agg["fg3a"] += l.fg3a
        agg["ftm"] += l.ftm
        agg["fta"] += l.fta
        per_player_gp[l.player_id] = per_player_gp.get(l.player_id, 0) + 1

    player_ids = list(per_player.keys())
    name_map = {p.id: p.full_name for p in db.execute(
        select(Player).where(Player.id.in_(player_ids))
    ).scalars().all()} if player_ids else {}
    real_rows = db.execute(
        select(PlayerSeasonStats).where(
            PlayerSeasonStats.player_id.in_(player_ids),
            PlayerSeasonStats.season == sim.season,
        )
    ).scalars().all() if player_ids else []
    real_by_pid = {r.player_id: r for r in real_rows}

    players: list[PlayerAveragesRow] = []
    for pid, agg in per_player.items():
        gp = per_player_gp[pid]
        sim_row = {
            "gp": gp,
            "mpg": round(agg["minutes"] / gp, 2) if gp else 0,
            "ppg": round(agg["points"] / gp, 2) if gp else 0,
            "rpg": round(agg["rebounds"] / gp, 2) if gp else 0,
            "apg": round(agg["assists"] / gp, 2) if gp else 0,
            "spg": round(agg["steals"] / gp, 2) if gp else 0,
            "bpg": round(agg["blocks"] / gp, 2) if gp else 0,
            "topg": round(agg["turnovers"] / gp, 2) if gp else 0,
            "pf_per_game": round(agg["personal_fouls"] / gp, 2) if gp else 0,
            "fg_pct": round(agg["fgm"] / agg["fga"], 3) if agg["fga"] else None,
            "fg3_pct": round(agg["fg3m"] / agg["fg3a"], 3) if agg["fg3a"] else None,
            "ft_pct": round(agg["ftm"] / agg["fta"], 3) if agg["fta"] else None,
        }
        r = real_by_pid.get(pid)
        real_row = None
        if r is not None:
            real_row = {
                "gp": r.games_played,
                "mpg": r.minutes_per_game,
                "ppg": r.points,          # PSS stores these as per-game averages
                "rpg": r.rebounds,
                "apg": r.assists,
                "spg": r.steals,
                "bpg": r.blocks,
                "topg": r.turnovers,
                "pf_per_game": r.pf_per_game,
                "fg_pct": r.fg_pct,
                "fg3_pct": r.fg3_pct,
                "ft_pct": r.ft_pct,
            }
        players.append(PlayerAveragesRow(
            player_id=pid,
            name=name_map.get(pid, str(pid)),
            sim=sim_row,
            real=real_row,
        ))

    # Sort by sim MPG descending so the top of the table is the most-played rotation.
    players.sort(key=lambda p: -p.sim["mpg"])

    return SeasonAveragesResponse(
        sim_id=sim_id,
        team=team.abbreviation,
        season=sim.season,
        team_totals=TeamAveragesResponse(sim=team_sim, real=team_real),
        players=players,
    )


@season_router.get("/{sim_id}/games/{game_id}", response_model=SimulateGameResponse)
def season_game_detail(sim_id: int, game_id: str, db: Session = Depends(get_db)):
    """Return the full box + line score + PBP for one game in a season sim.

    Re-simulates deterministically using the stored seed — identical shape to
    POST /simulations/game, so the frontend can reuse LineScore/BoxScore/
    PlayByPlay components. Same "no event persistence" approach as the
    events endpoint below.
    """
    sim = db.get(SimulationRun, sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail=f"Simulation {sim_id} not found.")
    if sim.status != "complete":
        raise HTTPException(
            status_code=422,
            detail=f"Simulation {sim_id} is '{sim.status}' — only completed runs have browseable games.",
        )

    sg = db.execute(
        select(SimulatedGame)
        .where(SimulatedGame.simulation_id == sim_id, SimulatedGame.game_id == game_id)
    ).scalar_one_or_none()
    if not sg:
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found in simulation {sim_id}.")

    real_game = db.get(Game, game_id)
    stored = (sim.parameters or {}).get("sim_config")
    cfg = SimConfig(**stored) if stored else SimConfig()

    # Honor stored config's roster_depth + pre_negation so the re-sim uses the
    # same roster shape the season sim used. Pass db + team_ids so game_simulator
    # can reload for availability if the config requests it. Prior to this fix
    # the drill-in silently used load_roster's defaults (depth=10, pre_negation=
    # False) and skipped the availability reload -- producing a completely
    # different game than the persisted season-sim result.
    home_players = load_roster(
        db, real_game.home_team_id, sim.season,
        depth=cfg.roster_depth, pre_negation=cfg.use_pre_negation_probs,
    )
    away_players = load_roster(
        db, real_game.away_team_id, sim.season,
        depth=cfg.roster_depth, pre_negation=cfg.use_pre_negation_probs,
    )

    seed = _game_seed(sim.seed, game_id)
    result = simulate_game(
        home_players, away_players,
        seed=seed, season=sim.season,
        steps=200, capture_descriptions=True,
        config=cfg, db=db,
        home_team_id=real_game.home_team_id,
        away_team_id=real_game.away_team_id,
    )

    home_ids = {p["id"] for p in home_players}

    # Schedule context: matchup# (Nth meeting this season) + each team's game#.
    # Pulls this season's schedule once and ranks by date; game_id is a stable
    # tiebreak so doubleheaders (rare) don't wobble.
    ht_id, at_id = real_game.home_team_id, real_game.away_team_id
    start, end = season_bounds(sim.season)
    season_games = db.execute(
        select(Game.id, Game.game_date, Game.home_team_id, Game.away_team_id)
        .where(Game.game_date >= start, Game.game_date <= end)
    ).all()
    def _dated(rows):
        return sorted(rows, key=lambda r: (r.game_date, r.id))
    home_schedule = _dated([r for r in season_games if ht_id in (r.home_team_id, r.away_team_id)])
    away_schedule = _dated([r for r in season_games if at_id in (r.home_team_id, r.away_team_id)])
    matchup_schedule = _dated([
        r for r in season_games
        if {r.home_team_id, r.away_team_id} == {ht_id, at_id}
    ])
    home_game_no = next((i + 1 for i, r in enumerate(home_schedule) if r.id == game_id), None)
    away_game_no = next((i + 1 for i, r in enumerate(away_schedule) if r.id == game_id), None)
    matchup_index = next((i + 1 for i, r in enumerate(matchup_schedule) if r.id == game_id), None)
    matchup_total = len(matchup_schedule) or None

    return SimulateGameResponse(
        season=sim.season,
        seed=seed,
        home_team=real_game.home_team.abbreviation,
        away_team=real_game.away_team.abbreviation,
        home_score=result["home_score"],
        away_score=result["away_score"],
        quarter_scores=QuarterScores(
            home=result["quarter_scores"]["home"],
            away=result["quarter_scores"]["away"],
        ),
        home_box=build_box(home_players, result["box_score"]),
        away_box=build_box(away_players, result["box_score"]),
        events=flatten_and_enrich(result["chunk_events"], home_ids),
        game_date=real_game.game_date.isoformat(),
        matchup_index=matchup_index,
        matchup_total=matchup_total,
        home_game_no=home_game_no,
        away_game_no=away_game_no,
    )


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
    stored = (sim.parameters or {}).get("sim_config")
    cfg = SimConfig(**stored) if stored else SimConfig()

    # Honor stored config's roster_depth + pre_negation so the re-sim uses the
    # same roster shape the season sim used. Pass db + team_ids so game_simulator
    # can reload for availability if the config requests it. Prior to this fix
    # the drill-in silently used load_roster's defaults (depth=10, pre_negation=
    # False) and skipped the availability reload -- producing a completely
    # different game than the persisted season-sim result.
    home_players = load_roster(
        db, real_game.home_team_id, sim.season,
        depth=cfg.roster_depth, pre_negation=cfg.use_pre_negation_probs,
    )
    away_players = load_roster(
        db, real_game.away_team_id, sim.season,
        depth=cfg.roster_depth, pre_negation=cfg.use_pre_negation_probs,
    )

    seed = _game_seed(sim.seed, game_id)
    result = simulate_game(
        home_players, away_players,
        seed=seed, season=sim.season,
        steps=200, capture_descriptions=True,
        config=cfg, db=db,
        home_team_id=real_game.home_team_id,
        away_team_id=real_game.away_team_id,
    )

    home_ids = {p["id"] for p in home_players}
    return flatten_and_enrich(result["chunk_events"], home_ids)
