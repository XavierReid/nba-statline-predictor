"""Import all models here so Alembic autogenerate can see them."""

from app.models.team import Team  # noqa: F401
from app.models.player import Player  # noqa: F401
from app.models.game import Game, GameStatus  # noqa: F401
from app.models.player_season_stats import PlayerSeasonStats  # noqa: F401
from app.models.player_attributes import PlayerAttributes, PlayerAttributeOverride  # noqa: F401
from app.models.player_tendencies import PlayerTendencies  # noqa: F401
from app.models.simulation import SimulationRun, LineupPlayer, SimulatedGame, SimulatedPlayerLine  # noqa: F401
from app.models.team_season_stats import TeamSeasonStats  # noqa: F401
from app.models.scoring_event import ScoringEvent  # noqa: F401
from app.models.player_game_log import PlayerGameLog  # noqa: F401
