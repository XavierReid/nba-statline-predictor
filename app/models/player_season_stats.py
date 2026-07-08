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
    usg_pct: Mapped[Optional[float]] = mapped_column(Float)
    ast_pct: Mapped[Optional[float]] = mapped_column(Float)
    oreb_pct: Mapped[Optional[float]] = mapped_column(Float)
    dreb_pct: Mapped[Optional[float]] = mapped_column(Float)

    # Shot-location observations (LeagueDashPlayerShotLocations, per-game)
    ra_fgm: Mapped[Optional[float]] = mapped_column(Float)      # restricted area
    ra_fga: Mapped[Optional[float]] = mapped_column(Float)
    ra_fg_pct: Mapped[Optional[float]] = mapped_column(Float)
    paint_fgm: Mapped[Optional[float]] = mapped_column(Float)   # in the paint (non-RA)
    paint_fga: Mapped[Optional[float]] = mapped_column(Float)
    paint_fg_pct: Mapped[Optional[float]] = mapped_column(Float)
    mid_fga: Mapped[Optional[float]] = mapped_column(Float)     # mid-range zone
    mid_fg_pct: Mapped[Optional[float]] = mapped_column(Float)
    corner3_fga: Mapped[Optional[float]] = mapped_column(Float)

    # Defensive matchup observations (LeagueDashPtDefend, per-game)
    d_lt6_fga: Mapped[Optional[float]] = mapped_column(Float)        # defended rim attempts
    d_lt6_plusminus: Mapped[Optional[float]] = mapped_column(Float)  # defended FG% minus shooters' normal (rim)
    d_fg3a: Mapped[Optional[float]] = mapped_column(Float)           # defended 3PA
    d_fg3_plusminus: Mapped[Optional[float]] = mapped_column(Float)  # defended 3P% minus shooters' normal
    d_fga: Mapped[Optional[float]] = mapped_column(Float)            # all defended FGA (Overall category)
    d_plusminus: Mapped[Optional[float]] = mapped_column(Float)      # overall defended FG% minus shooters' normal

    def __repr__(self) -> str:
        return f"<PlayerSeasonStats player_id={self.player_id} season={self.season}>"
