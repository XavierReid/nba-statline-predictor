from typing import Optional
from datetime import datetime
from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SimulationRun(Base):
    """A single season simulation run.

    status lifecycle:
        pending → running → complete        (terminal)
                   ├─▶ paused  → running    (resume)
                   │     └─▶ cancelled      (terminal)
                   ├─▶ failed  → running    (retry)
                   │     └─▶ cancelled      (terminal)
                   └─▶ cancelled            (terminal)

    Blocking states (prevent new runs): running, paused, failed.
    Terminal/non-blocking: complete, cancelled.
    """
    __tablename__ = "simulation_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    season: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    # Reproducibility
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    # JSON blob: scope, team_id, home_advantage, variance_factor, sub_variance, etc.
    parameters: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Progress tracking — avoids COUNT(simulated_games) on every poll
    games_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<SimulationRun id={self.id} season={self.season} status={self.status}>"


class LineupPlayer(Base):
    """A player slot in a simulation run's roster.

    Seeded from player_season_stats when the run is created.
    Top 10 players by minutes per team, normalized so the team
    total equals 240 player-minutes (5 players × 48 min).
    Players with no stats for the season have minutes_per_game=0
    and are excluded from rotation unless manually overridden.
    """
    __tablename__ = "lineup_players"
    __table_args__ = (
        UniqueConstraint("simulation_id", "team_id", "player_id", name="uq_lp_sim_team_player"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    simulation_id: Mapped[int] = mapped_column(ForeignKey("simulation_runs.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    season: Mapped[str] = mapped_column(String(8), nullable=False)

    minutes_per_game: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_starter: Mapped[bool] = mapped_column(nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<LineupPlayer sim={self.simulation_id} player={self.player_id} min={self.minutes_per_game}>"


class SimulatedGame(Base):
    """Result of one simulated game within a simulation run."""
    __tablename__ = "simulated_games"
    __table_args__ = (
        UniqueConstraint("simulation_id", "game_id", name="uq_sg_sim_game"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    simulation_id: Mapped[int] = mapped_column(ForeignKey("simulation_runs.id"), nullable=False, index=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), nullable=False)

    home_score: Mapped[int] = mapped_column(Integer, nullable=False)
    away_score: Mapped[int] = mapped_column(Integer, nullable=False)

    # Quarter scores
    home_q1: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    home_q2: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    home_q3: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    home_q4: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    away_q1: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    away_q2: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    away_q3: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    away_q4: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<SimulatedGame sim={self.simulation_id} game={self.game_id} {self.home_score}-{self.away_score}>"


class SimulatedPlayerLine(Base):
    """Per-player box score for one simulated game."""
    __tablename__ = "simulated_player_lines"
    __table_args__ = (
        UniqueConstraint("simulated_game_id", "player_id", name="uq_spl_game_player"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    simulated_game_id: Mapped[int] = mapped_column(ForeignKey("simulated_games.id"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)

    minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rebounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assists: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    steals: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    turnovers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fgm: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fga: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fg3m: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fg3a: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ftm: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    plus_minus: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<SimulatedPlayerLine game={self.simulated_game_id} player={self.player_id} pts={self.points}>"
