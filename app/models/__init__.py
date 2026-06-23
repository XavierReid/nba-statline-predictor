"""Import all models here so Alembic autogenerate can see them."""

from app.models.team import Team  # noqa: F401
from app.models.player import Player  # noqa: F401
from app.models.game import Game, GameStatus  # noqa: F401
