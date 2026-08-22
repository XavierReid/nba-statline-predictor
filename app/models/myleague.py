"""MyLeague models (M-1a).

Sibling tables to SimulationRun for stateful franchise-mode runs. The parent
SimulationRun row uses scope='myleague' and team_id=NULL; the controlled team
lives on MyLeagueState.controlled_team_id (nullable to leave the God-mode
door open per the design lock).

State model is event-sourced: state at time T = fold(events with
applied_at_date <= T, base_state). See project-next-session-focus for the
locked semantics of applied_at_date and the no-retroactive-mutation rule.
"""
from typing import Optional
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MyLeagueState(Base):
    """One-per-simulation-run state row for scope='myleague' sims.

    Only mutable fields (cursor + timestamps) live here. Derivable state
    (standings, completed games, per-player STD stats) is queried from
    SimulatedGame + SimulatedPlayerLine at read time.
    """
    __tablename__ = "myleague_state"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    simulation_id: Mapped[int] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    # Nullable — no controlled team = God mode (future).
    controlled_team_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("teams.id"), nullable=True, index=True,
    )
    # Monotonic schedule cursor. Advancing decreases refused at write time.
    current_calendar_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class MyLeagueEvent(Base):
    """One mutation to the season state (M-1a: SET_UNAVAILABLE / SET_AVAILABLE).

    Immutable history. `applied_at_date` is the in-universe date the event
    takes effect; a fold over events where applied_at_date <= D yields the
    state slice for date D. Retroactive events (applied_at_date <= any
    already-simulated game's game_date) are rejected at write time.
    """
    __tablename__ = "myleague_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    myleague_state_id: Mapped[int] = mapped_column(
        ForeignKey("myleague_state.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    applied_at_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(),
    )
