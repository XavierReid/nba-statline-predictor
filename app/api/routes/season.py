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
from app.api.helpers import build_box, get_team, sim_game_is_win
from app.api.schemas.simulations import (
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
