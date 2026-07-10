import enum
from datetime import date
from typing import Optional

from sqlalchemy import Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.team import Team


class GameStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    FINAL = "final"


class Game(Base):
    __tablename__ = "games"

    id: Mapped[str] = mapped_column(primary_key=True)  # NBA game_id
    game_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True, nullable=False)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True, nullable=False)
    home_score: Mapped[Optional[int]] = mapped_column(Integer)
    away_score: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[GameStatus] = mapped_column(
        String(16), nullable=False, default=GameStatus.SCHEDULED
    )

    # Quarter line scores (calibration dataset — real margin-walk/dispersion targets)
    home_q1: Mapped[Optional[int]] = mapped_column(Integer)
    home_q2: Mapped[Optional[int]] = mapped_column(Integer)
    home_q3: Mapped[Optional[int]] = mapped_column(Integer)
    home_q4: Mapped[Optional[int]] = mapped_column(Integer)
    home_ot: Mapped[Optional[int]] = mapped_column(Integer)   # all OT periods summed
    away_q1: Mapped[Optional[int]] = mapped_column(Integer)
    away_q2: Mapped[Optional[int]] = mapped_column(Integer)
    away_q3: Mapped[Optional[int]] = mapped_column(Integer)
    away_q4: Mapped[Optional[int]] = mapped_column(Integer)
    away_ot: Mapped[Optional[int]] = mapped_column(Integer)

    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id], lazy="joined")
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id], lazy="joined")

    def __repr__(self) -> str:
        return f"<Game {self.id} {self.game_date} {self.away_team_id}@{self.home_team_id}>"
