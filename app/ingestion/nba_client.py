"""Thin wrapper around nba_api.

Keep all third-party API calls behind this module so we can swap data source later
(balldontlie.io, basketball-reference, etc.) without touching the ingestion job.

Docs: https://github.com/swar/nba_api
"""

# from nba_api.stats.endpoints import leaguegamefinder, boxscoretraditionalv2
from nba_api.stats.static import teams
from nba_api.stats.endpoints import commonteamroster

CUSTOM_HEADERS  = {
"Host": "stats.nba.com",
"Connection": "keep-alive",
"Accept": "application/json, text/plain, /",
"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
"Referer": "https://www.nba.com/",
"Origin": "https://www.nba.com",
"x-nba-stats-origin": "stats",
"x-nba-stats-token": "true",
"Accept-Language": "en-US,en;q=0.9"
}


def fetch_all_teams() -> list[dict]:
    """Return all NBA teams as list of dicts: {id, abbreviation, nickname, city, conference, division}."""
    return teams.get_teams()


def fetch_all_active_players() -> list[dict]:
    all_players = []
    for team in teams.get_teams():
        team_id = team['id']
        roster = commonteamroster.CommonTeamRoster(team_id=team_id,headers=CUSTOM_HEADERS,timeout=60)
        all_players.extend(roster.get_normalized_dict()['CommonTeamRoster'])
    return all_players


def fetch_games_for_season(season: str) -> list[dict]:
    """Return deduplicated games for a season. Status is 'final' or 'scheduled'."""
    from nba_api.stats.endpoints import leaguegamefinder

    rows_by_game: dict[str, list] = {}
    for team in teams.get_teams():
        finder = leaguegamefinder.LeagueGameFinder(
            team_id_nullable=team['id'],
            season_nullable=season,
            season_type_nullable='Regular Season',
            headers=CUSTOM_HEADERS,
            timeout=60,
        )
        for row in finder.get_normalized_dict()['LeagueGameFinderResults']:
            rows_by_game.setdefault(row['GAME_ID'], []).append(row)

    games = []
    for game_id, rows in rows_by_game.items():
        if len(rows) != 2:
            continue
        home = next((r for r in rows if 'vs.' in r['MATCHUP']), None)
        away = next((r for r in rows if '@' in r['MATCHUP']), None)
        if not home or not away:
            continue
        is_final = home['WL'] is not None
        games.append({
            'id': game_id,
            'game_date': home['GAME_DATE'],
            'home_team_id': home['TEAM_ID'],
            'away_team_id': away['TEAM_ID'],
            'home_score': home['PTS'] if is_final else None,
            'away_score': away['PTS'] if is_final else None,
            'status': 'final' if is_final else 'scheduled',
        })
    return games


def fetch_season_stats(season: str) -> list[dict]:
    """Return per-game averages for all players in a season via LeagueDashPlayerStats."""
    from nba_api.stats.endpoints import leaguedashplayerstats
    response = leaguedashplayerstats.LeagueDashPlayerStats(
        season=season,
        season_type_all_star='Regular Season',
        per_mode_simple='PerGame',
        headers=CUSTOM_HEADERS,
        timeout=60,
    )
    return response.get_normalized_dict()['LeagueDashPlayerStats']


def fetch_box_score(game_id: int) -> list[dict]:
    """Pull per-player box scores for a single game."""
    # TODO: implement using nba_api.stats.endpoints.boxscoretraditionalv2
    raise NotImplementedError
