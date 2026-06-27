from typing import Optional

from sqlalchemy import Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TeamSeasonStats(Base):
    __tablename__ = "team_season_stats"
    __table_args__ = (UniqueConstraint("team_id", "season", name="uq_team_season_stats"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), nullable=False)
    season: Mapped[str] = mapped_column(String(10), nullable=False)
    pace: Mapped[float] = mapped_column(Float, nullable=False)
    off_rating: Mapped[float] = mapped_column(Float, nullable=False)
    def_rating: Mapped[float] = mapped_column(Float, nullable=False)
    net_rating: Mapped[float] = mapped_column(Float, nullable=False)
    oreb_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
