from typing import Optional

from sqlalchemy import Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PlayerGameLog(Base):
    """One player's box line for one real game — the per-game grain the season
    aggregates (PlayerSeasonStats) hide. Ingested for the availability model
    (gap 3.4): real per-game active-roster size and DNP patterns can only be
    measured from per-game minutes. Kept box-minimal but reusable (gap 3.4d).
    """

    __tablename__ = "player_game_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season: Mapped[str] = mapped_column(String(7), nullable=False)      # e.g. "2016-17"
    game_id: Mapped[str] = mapped_column(String(20), nullable=False)     # NBA game_id
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pts: Mapped[Optional[int]] = mapped_column(Integer)
    reb: Mapped[Optional[int]] = mapped_column(Integer)
    ast: Mapped[Optional[int]] = mapped_column(Integer)
    fgm: Mapped[Optional[int]] = mapped_column(Integer)
    fga: Mapped[Optional[int]] = mapped_column(Integer)
    fg3m: Mapped[Optional[int]] = mapped_column(Integer)
    fta: Mapped[Optional[int]] = mapped_column(Integer)
    ftm: Mapped[Optional[int]] = mapped_column(Integer)
    tov: Mapped[Optional[int]] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_player_game_logs_season_game", "season", "game_id"),
        Index("ix_player_game_logs_season_player", "season", "player_id"),
    )
