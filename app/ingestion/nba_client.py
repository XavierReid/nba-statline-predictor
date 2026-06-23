"""Thin wrapper around nba_api.

Keep all third-party API calls behind this module so we can swap data source later
(balldontlie.io, basketball-reference, etc.) without touching the ingestion job.

Docs: https://github.com/swar/nba_api
"""

# from nba_api.stats.endpoints import leaguegamefinder, boxscoretraditionalv2
from nba_api.stats.static import teams, players


def fetch_all_teams() -> list[dict]:
    """Return all NBA teams as list of dicts: {id, abbreviation, nickname, city, conference, division}."""
    return teams.get_teams()


def fetch_all_active_players() -> list[dict]:
    """Return all active players as list of dicts: {id, full_name, team_id, position}."""
    # TODO: implement using nba_api.stats.static.players.get_active_players()
    raise NotImplementedError


def fetch_games_for_season(season: str) -> list[dict]:
    """Pull all games (scheduled + completed) for a season like '2024-25'."""
    # TODO: implement using nba_api.stats.endpoints.leaguegamefinder
    raise NotImplementedError


def fetch_box_score(game_id: int) -> list[dict]:
    """Pull per-player box scores for a single game."""
    # TODO: implement using nba_api.stats.endpoints.boxscoretraditionalv2
    raise NotImplementedError
