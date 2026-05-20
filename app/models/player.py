from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.team import Team


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)  # NBA player_id
    full_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), index=True)
    position: Mapped[str | None] = mapped_column(String(8))

    team: Mapped[Team | None] = relationship(lazy="joined")

    def __repr__(self) -> str:
        return f"<Player {self.full_name}>"
