from datetime import date

from sqlalchemy import Date, Float, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TeamDefensiveRating(Base):
    """Snapshot of a team's defensive rating on a given date.

    Defensive rating = points allowed per 100 possessions.
    Updated nightly by the ingestion job.
    """

    __tablename__ = "team_defensive_ratings"
    __table_args__ = (UniqueConstraint("team_id", "date", name="uq_tdr_team_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True, nullable=False)
    date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    defensive_rating: Mapped[float] = mapped_column(Float, nullable=False)
    pace: Mapped[float | None] = mapped_column(Float)
