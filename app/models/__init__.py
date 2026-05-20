"""Import all models here so Alembic autogenerate can see them."""

from app.models.team import Team  # noqa: F401
from app.models.player import Player  # noqa: F401
from app.models.game import Game, GameStatus  # noqa: F401
from app.models.player_game_stats import PlayerGameStats  # noqa: F401
from app.models.team_defensive_rating import TeamDefensiveRating  # noqa: F401
