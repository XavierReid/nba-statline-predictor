from typing import Optional
from sqlalchemy import Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PlayerSeasonStats(Base):
    __tablename__ = "player_season_stats"
    __table_args__ = (UniqueConstraint("player_id", "season", name="uq_pss_player_season"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True, nullable=False)
    season: Mapped[str] = mapped_column(String(8), nullable=False)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"))

    games_played: Mapped[Optional[int]] = mapped_column(Integer)
    minutes_per_game: Mapped[Optional[float]] = mapped_column(Float)

    points: Mapped[Optional[float]] = mapped_column(Float)
    rebounds: Mapped[Optional[float]] = mapped_column(Float)
    assists: Mapped[Optional[float]] = mapped_column(Float)
    steals: Mapped[Optional[float]] = mapped_column(Float)
    blocks: Mapped[Optional[float]] = mapped_column(Float)
    turnovers: Mapped[Optional[float]] = mapped_column(Float)

    fgm: Mapped[Optional[float]] = mapped_column(Float)
    fga: Mapped[Optional[float]] = mapped_column(Float)
    fg_pct: Mapped[Optional[float]] = mapped_column(Float)
    fg3m: Mapped[Optional[float]] = mapped_column(Float)
    fg3a: Mapped[Optional[float]] = mapped_column(Float)
    fg3_pct: Mapped[Optional[float]] = mapped_column(Float)
    ftm: Mapped[Optional[float]] = mapped_column(Float)
    fta: Mapped[Optional[float]] = mapped_column(Float)
    ft_pct: Mapped[Optional[float]] = mapped_column(Float)

    plus_minus: Mapped[Optional[float]] = mapped_column(Float)

    def __repr__(self) -> str:
        return f"<PlayerSeasonStats player_id={self.player_id} season={self.season}>"
