from typing import Optional
from sqlalchemy import Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PlayerTendencies(Base):
    """How a player plays — derived from season stats per possession/opportunity."""
    __tablename__ = "player_tendencies"
    __table_args__ = (UniqueConstraint("player_id", "season", name="uq_pt_player_season"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True, nullable=False)
    season: Mapped[str] = mapped_column(String(8), nullable=False)

    usage_rate: Mapped[Optional[float]] = mapped_column(Float)       # % of team possessions used
    shot_tendency: Mapped[Optional[float]] = mapped_column(Float)    # FGA per possession used
    three_point_rate: Mapped[Optional[float]] = mapped_column(Float) # FG3A / FGA
    assist_rate: Mapped[Optional[float]] = mapped_column(Float)      # AST per 36 min
    oreb_rate: Mapped[Optional[float]] = mapped_column(Float)        # offensive rebound % (NBA Advanced)
    dreb_rate: Mapped[Optional[float]] = mapped_column(Float)        # defensive rebound % (NBA Advanced)
    rebound_rate: Mapped[Optional[float]] = mapped_column(Float)     # REB per 36 min (fallback)
    turnover_rate: Mapped[Optional[float]] = mapped_column(Float)    # TOV per possession used
    foul_drawing_rate: Mapped[Optional[float]] = mapped_column(Float)  # FTA / FGA — foul drawing tendency

    def __repr__(self) -> str:
        return f"<PlayerTendencies player_id={self.player_id} season={self.season}>"
