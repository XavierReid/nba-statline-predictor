"""Roster snapshots (Step 3a): who is on which team as of a date, pinned and immutable once sealed.

Additive tables only. Nothing in the loader or the sim reads these yet.
"""
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (JSON, CheckConstraint, Date, DateTime, Float, ForeignKey,
                        Integer, String, func)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

ASSIGNMENT_STATUSES = ("standard", "two_way", "exhibit10", "inactive", "free_agent")


class DataFetch(Base):
    """One external pull. The raw payload lives in the gitignored data/raw store."""
    __tablename__ = "data_fetches"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    endpoint: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict] = mapped_column(JSON, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    store_path: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class PlayerBio(Base):
    """Time-invariant facts only. Position, team and status are time-varying and live in snapshots."""
    __tablename__ = "player_bio"

    # NBA player id. No FK to players: rookies and new signees may not have a players row yet.
    player_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    birth_date: Mapped[Optional[date]] = mapped_column(Date)
    height_in: Mapped[Optional[float]] = mapped_column(Float)
    weight_lb: Mapped[Optional[int]] = mapped_column(Integer)
    school: Mapped[Optional[str]] = mapped_column(String(128))
    first_nba_season: Mapped[Optional[str]] = mapped_column(String(8))
    draft_year: Mapped[Optional[int]] = mapped_column(Integer)    # filled by the rookie work, not 3a
    draft_round: Mapped[Optional[int]] = mapped_column(Integer)
    draft_pick: Mapped[Optional[int]] = mapped_column(Integer)
    source_fetch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("data_fetches.id"))


class RosterSnapshot(Base):
    __tablename__ = "roster_snapshots"
    __table_args__ = (
        CheckConstraint("kind IN ('live_pull','from_stats')", name="ck_roster_snapshot_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    season: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_fetch_ids: Mapped[List[int]] = mapped_column(JSON, nullable=False, default=list)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("roster_snapshots.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    sealed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class RosterAssignment(Base):
    __tablename__ = "roster_assignments"
    __table_args__ = (
        CheckConstraint("status IN ('standard','two_way','exhibit10','inactive','free_agent')",
                        name="ck_roster_assignment_status"),
        CheckConstraint("team_id IS NOT NULL OR status IN ('free_agent','inactive')",
                        name="ck_roster_assignment_team"),
    )

    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("roster_snapshots.id", ondelete="CASCADE"), primary_key=True)
    player_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    position: Mapped[Optional[str]] = mapped_column(String(8))
    jersey: Mapped[Optional[str]] = mapped_column(String(4))
