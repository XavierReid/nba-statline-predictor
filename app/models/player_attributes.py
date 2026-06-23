from typing import Optional
from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

ESTIMATED_DEFAULT = 50
SHOOTING_DEFAULT = 40


class PlayerAttributes(Base):
    """Season-specific player ratings (0-100 scale).

    Derived attributes are computed by RatingEngine from PlayerSeasonStats.
    Estimated attributes default to ESTIMATED_DEFAULT and should be overridden
    via PlayerAttributeOverride when tracking data becomes available.
    """
    __tablename__ = "player_attributes"
    __table_args__ = (UniqueConstraint("player_id", "season", name="uq_pa_player_season"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True, nullable=False)
    season: Mapped[str] = mapped_column(String(8), nullable=False)

    # Scoring — derived
    three_point: Mapped[int] = mapped_column(Integer, default=SHOOTING_DEFAULT)
    free_throw: Mapped[int] = mapped_column(Integer, default=SHOOTING_DEFAULT)
    mid_range: Mapped[int] = mapped_column(Integer, default=SHOOTING_DEFAULT)

    # Scoring — estimated
    close_shot: Mapped[int] = mapped_column(Integer, default=ESTIMATED_DEFAULT)
    layup: Mapped[int] = mapped_column(Integer, default=ESTIMATED_DEFAULT)
    dunk: Mapped[int] = mapped_column(Integer, default=ESTIMATED_DEFAULT)

    # Creation — derived
    passing: Mapped[int] = mapped_column(Integer, default=ESTIMATED_DEFAULT)

    # Creation — estimated
    ball_handle: Mapped[int] = mapped_column(Integer, default=ESTIMATED_DEFAULT)

    # Defense — derived
    steal: Mapped[int] = mapped_column(Integer, default=ESTIMATED_DEFAULT)
    block: Mapped[int] = mapped_column(Integer, default=ESTIMATED_DEFAULT)

    # Defense — estimated
    perimeter_defense: Mapped[int] = mapped_column(Integer, default=ESTIMATED_DEFAULT)
    interior_defense: Mapped[int] = mapped_column(Integer, default=ESTIMATED_DEFAULT)

    # Physical — estimated
    speed: Mapped[int] = mapped_column(Integer, default=ESTIMATED_DEFAULT)
    acceleration: Mapped[int] = mapped_column(Integer, default=ESTIMATED_DEFAULT)
    strength: Mapped[int] = mapped_column(Integer, default=ESTIMATED_DEFAULT)
    stamina: Mapped[int] = mapped_column(Integer, default=ESTIMATED_DEFAULT)
    vertical: Mapped[int] = mapped_column(Integer, default=ESTIMATED_DEFAULT)

    # Rebounding — derived
    offensive_rebound: Mapped[int] = mapped_column(Integer, default=ESTIMATED_DEFAULT)
    defensive_rebound: Mapped[int] = mapped_column(Integer, default=ESTIMATED_DEFAULT)

    # Composite
    overall_rating: Mapped[int] = mapped_column(Integer, default=ESTIMATED_DEFAULT)

    def __repr__(self) -> str:
        return f"<PlayerAttributes player_id={self.player_id} season={self.season}>"


class PlayerAttributeOverride(Base):
    """Manual overrides for attributes that stats cannot capture."""
    __tablename__ = "player_attribute_overrides"
    __table_args__ = (
        UniqueConstraint("player_id", "season", "attribute", name="uq_pao_player_season_attr"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True, nullable=False)
    season: Mapped[str] = mapped_column(String(8), nullable=False)
    attribute: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<PlayerAttributeOverride {self.player_id} {self.attribute}={self.value}>"
