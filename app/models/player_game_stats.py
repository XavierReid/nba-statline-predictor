from sqlalchemy import Boolean, Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PlayerGameStats(Base):
    """Box-score line for one player in one game."""

    __tablename__ = "player_game_stats"
    __table_args__ = (UniqueConstraint("game_id", "player_id", name="uq_pgs_game_player"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True, nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True, nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True, nullable=False)

    minutes: Mapped[float | None] = mapped_column(Float)
    points: Mapped[int | None] = mapped_column(Integer)
    rebounds: Mapped[int | None] = mapped_column(Integer)
    assists: Mapped[int | None] = mapped_column(Integer)
    steals: Mapped[int | None] = mapped_column(Integer)
    blocks: Mapped[int | None] = mapped_column(Integer)
    turnovers: Mapped[int | None] = mapped_column(Integer)

    fg_made: Mapped[int | None] = mapped_column(Integer)
    fg_attempted: Mapped[int | None] = mapped_column(Integer)
    three_made: Mapped[int | None] = mapped_column(Integer)
    three_attempted: Mapped[int | None] = mapped_column(Integer)
    ft_made: Mapped[int | None] = mapped_column(Integer)
    ft_attempted: Mapped[int | None] = mapped_column(Integer)

    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    game = relationship("Game", lazy="joined")
    player = relationship("Player", lazy="joined")
