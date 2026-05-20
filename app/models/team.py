from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)  # NBA team_id
    abbreviation: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    conference: Mapped[str | None] = mapped_column(String(16))
    division: Mapped[str | None] = mapped_column(String(32))

    def __repr__(self) -> str:
        return f"<Team {self.abbreviation}>"
