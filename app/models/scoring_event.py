from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ScoringEvent(Base):
    """One made-score play from real play-by-play, distilled for game-texture
    analysis (runs, droughts, lead changes). The full PBP is NOT stored — only the
    ordered scoring plays with the running score, which is all the run/drought and
    lead-change instruments need. Real analog of the sim's scoring events.
    """

    __tablename__ = "game_scoring_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), nullable=False)
    event_num: Mapped[int] = mapped_column(Integer, nullable=False)  # PBP EVENTNUM (ordering)
    period: Mapped[int] = mapped_column(Integer, nullable=False)
    seconds_remaining: Mapped[int] = mapped_column(Integer, nullable=False)  # within period
    scoring_side: Mapped[str] = mapped_column(String(4), nullable=False)     # 'home' | 'away'
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    home_score: Mapped[int] = mapped_column(Integer, nullable=False)
    away_score: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (Index("ix_scoring_events_game_order", "game_id", "event_num"),)
