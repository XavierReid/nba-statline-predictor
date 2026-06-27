"""Season simulation background task.

Runs all games in a team's schedule for the given season, persisting
results incrementally (one commit per game) for crash recovery.
"""
import hashlib
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import select, update

from app.database import SessionLocal
from app.models.game import Game
from app.models.simulation import SimulatedGame, SimulatedPlayerLine, SimulationRun
from app.models.team import Team
from app.services.game_simulator import load_roster, simulate_game

log = logging.getLogger(__name__)


def _game_seed(master_seed: int, game_id: str) -> int:
    """Derive a deterministic per-game seed from the master seed and game ID."""
    raw = f"{master_seed}:{game_id}".encode()
    return int(hashlib.sha256(raw).hexdigest(), 16) % (2**31)


def _season_game_prefix(season: str) -> str:
    """Map '2025-26' → '00225', used to filter games by season via ID prefix."""
    year = season.split("-")[0][-2:]
    return f"002{year}"


def _fetch_schedule(db: Session, team_id: int, season: str) -> list[Game]:
    """Return all games for the team in date order, filtered to the given season."""
    prefix = _season_game_prefix(season)
    return list(db.execute(
        select(Game)
        .where(
            ((Game.home_team_id == team_id) | (Game.away_team_id == team_id))
            & Game.id.like(f"{prefix}%")
        )
        .order_by(Game.game_date)
    ).scalars().all())


def _persist_game(
    db: Session,
    simulation_id: int,
    game: Game,
    result: dict,
    home_players: list[dict],
    away_players: list[dict],
) -> None:
    """Write one simulated game and its player lines, then commit."""
    sim_game = SimulatedGame(
        simulation_id=simulation_id,
        game_id=game.id,
        home_score=result["home_score"],
        away_score=result["away_score"],
        went_to_ot=result["went_to_ot"],
        quarter_scores=result["quarter_scores"],
    )
    db.add(sim_game)
    db.flush()  # get sim_game.id without committing

    box = result["box_score"]
    lines = []
    for p in home_players + away_players:
        s = box.get(p["id"])
        if not s or s.get("min", 0) < 0.5:
            continue
        lines.append(SimulatedPlayerLine(
            simulated_game_id=sim_game.id,
            player_id=p["id"],
            team_id=game.home_team_id if p in home_players else game.away_team_id,
            minutes=round(s["min"], 1),
            points=s["pts"],
            rebounds=s["reb"],
            assists=s["ast"],
            steals=s["stl"],
            blocks=s["blk"],
            turnovers=s["tov"],
            personal_fouls=s["pf"],
            fouled_out=s["fouled_out"],
            fgm=s["fgm"],
            fga=s["fga"],
            fg3m=s["fg3m"],
            fg3a=s["fg3a"],
            ftm=s["ftm"],
            fta=s["fta"],
            plus_minus=s["plus_minus"],
        ))

    db.bulk_save_objects(lines)
    db.commit()


def run_season_simulation(simulation_id: int, config: Optional["SimConfig"] = None) -> None:
    """Background task: simulate all games and persist results.

    Opens its own DB session (background tasks run outside the request session).
    Marks the run failed if an unhandled exception occurs.
    """
    db = SessionLocal()
    sim = None
    try:
        sim = db.get(SimulationRun, simulation_id)
        if not sim:
            log.error("SimulationRun %d not found", simulation_id)
            return

        schedule = _fetch_schedule(db, sim.team_id, sim.season)
        if not schedule:
            _mark_failed(db, sim, "No schedule found for this team and season")
            return

        # Store total_games so the status endpoint can report accurate progress
        db.execute(
            update(SimulationRun)
            .where(SimulationRun.id == simulation_id)
            .values(parameters={**(sim.parameters or {}), "total_games": len(schedule)})
        )
        db.commit()

        # Cache rosters — load each team once, reuse across all their games
        roster_cache: dict[int, Optional[list[dict]]] = {}

        def get_roster(team_id: int) -> Optional[list[dict]]:
            if team_id not in roster_cache:
                roster_cache[team_id] = load_roster(db, team_id, sim.season)
            return roster_cache[team_id]

        completed = 0
        for game in schedule:
            # Check for cancellation before each game
            db.refresh(sim)
            if sim.status == "cancelled":
                log.info("SimulationRun %d cancelled at game %d/%d", simulation_id, completed, len(schedule))
                return

            home_players = get_roster(game.home_team_id)
            away_players = get_roster(game.away_team_id)

            if not home_players or not away_players:
                log.warning("Missing roster for game %s — skipping", game.id)
                continue

            seed = _game_seed(sim.seed, game.id)
            result = simulate_game(home_players, away_players, seed=seed, season=sim.season, config=config)

            _persist_game(db, simulation_id, game, result, home_players, away_players)

            completed += 1
            db.execute(
                update(SimulationRun)
                .where(SimulationRun.id == simulation_id)
                .values(games_completed=completed)
            )
            db.commit()

        db.execute(
            update(SimulationRun)
            .where(SimulationRun.id == simulation_id)
            .values(status="complete", completed_at=datetime.utcnow())
        )
        db.commit()
        log.info("SimulationRun %d complete — %d games", simulation_id, completed)

    except Exception as exc:
        log.exception("SimulationRun %d failed: %s", simulation_id, exc)
        _mark_failed(db, sim, str(exc))
    finally:
        db.close()


def _mark_failed(db: Session, sim: Optional[SimulationRun], reason: str) -> None:
    if sim:
        db.execute(
            update(SimulationRun)
            .where(SimulationRun.id == sim.id)
            .values(status="failed")
        )
        db.commit()
    log.error("SimulationRun marked failed: %s", reason)
