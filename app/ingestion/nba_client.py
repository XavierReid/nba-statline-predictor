"""Thin wrapper around nba_api.

Keep all third-party API calls behind this module so we can swap data source later
(balldontlie.io, basketball-reference, etc.) without touching the ingestion job.

Docs: https://github.com/swar/nba_api
"""

# from nba_api.stats.endpoints import leaguegamefinder, boxscoretraditionalv2
import time
from nba_api.stats.static import teams
from nba_api.stats.endpoints import commonteamroster

_RATE_LIMIT_DELAY = 0.6  # seconds between per-team requests to avoid throttling

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
        roster = commonteamroster.CommonTeamRoster(team_id=team_id, headers=CUSTOM_HEADERS, timeout=60)
        all_players.extend(roster.get_normalized_dict()['CommonTeamRoster'])
        time.sleep(_RATE_LIMIT_DELAY)
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
        time.sleep(_RATE_LIMIT_DELAY)

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
    """Return per-game averages merged with advanced stats for all players in a season.

    Makes two calls to LeagueDashPlayerStats (PerGame + Advanced) and merges
    by PLAYER_ID so downstream ingestion gets everything in one list of dicts.
    """
    from nba_api.stats.endpoints import leaguedashplayerstats

    per_game = leaguedashplayerstats.LeagueDashPlayerStats(
        season=season,
        season_type_all_star='Regular Season',
        per_mode_detailed='PerGame',
        headers=CUSTOM_HEADERS,
        timeout=120,
    ).get_normalized_dict()['LeagueDashPlayerStats']

    time.sleep(_RATE_LIMIT_DELAY)

    advanced = leaguedashplayerstats.LeagueDashPlayerStats(
        season=season,
        season_type_all_star='Regular Season',
        per_mode_detailed='PerGame',
        measure_type_detailed_defense='Advanced',
        headers=CUSTOM_HEADERS,
        timeout=120,
    ).get_normalized_dict()['LeagueDashPlayerStats']

    advanced_by_id = {r['PLAYER_ID']: r for r in advanced}
    for row in per_game:
        adv = advanced_by_id.get(row['PLAYER_ID'], {})
        row['USG_PCT'] = adv.get('USG_PCT')
        row['AST_PCT'] = adv.get('AST_PCT')
        row['OREB_PCT'] = adv.get('OREB_PCT')
        row['DREB_PCT'] = adv.get('DREB_PCT')

    return per_game


def fetch_team_season_stats(season: str) -> list[dict]:
    """Return pace, off_rating, def_rating, net_rating per team for a season."""
    from nba_api.stats.endpoints import leaguedashteamstats

    result = leaguedashteamstats.LeagueDashTeamStats(
        season=season,
        measure_type_detailed_defense='Advanced',
        per_mode_detailed='PerGame',
        headers=CUSTOM_HEADERS,
        timeout=60,
    )
    rows = result.get_normalized_dict()['LeagueDashTeamStats']
    return [
        {
            'team_id': row['TEAM_ID'],
            'pace': row['PACE'],
            'off_rating': row['OFF_RATING'],
            'def_rating': row['DEF_RATING'],
            'net_rating': row['NET_RATING'],
            'oreb_pct': row.get('OREB_PCT'),
        }
        for row in rows
        if row.get('PACE') is not None
    ]


def fetch_clutch_stats(season: str) -> list[dict]:
    """Return per-player clutch stats (last 5 min, within 5 pts) for a season.

    Fields returned: PLAYER_ID, FG_PCT, FT_PCT, TOV (per game avg), GP (clutch games).
    Players with fewer than 10 clutch possessions are filtered downstream.
    """
    from nba_api.stats.endpoints import leaguedashplayerclutch

    rows = leaguedashplayerclutch.LeagueDashPlayerClutch(
        season=season,
        season_type_all_star='Regular Season',
        clutch_time='Last 5 Minutes',
        point_diff=5,
        per_mode_detailed='PerGame',
        headers=CUSTOM_HEADERS,
        timeout=120,
    ).get_normalized_dict()['LeagueDashPlayerClutch']

    time.sleep(_RATE_LIMIT_DELAY)
    return [
        {
            'player_id': row['PLAYER_ID'],
            'fg_pct': row.get('FG_PCT'),
            'ft_pct': row.get('FT_PCT'),
            'tov': row.get('TOV'),         # turnovers per clutch game
            'fga': row.get('FGA'),         # attempts per clutch game — used as volume filter
            'gp': row.get('GP'),
        }
        for row in rows
        if row.get('GP') is not None
    ]


def fetch_shot_locations(season: str) -> dict[int, dict]:
    """Return {player_id: zone shooting observations} for a season.

    One call covers the league. Zone columns arrive as a flat row:
    6 leading player columns, then FGM/FGA/FG_PCT triplets in order:
    Restricted Area, Paint (Non-RA), Mid-Range, Left Corner 3,
    Right Corner 3, Above the Break 3, Backcourt, Corner 3.
    """
    from nba_api.stats.endpoints import leaguedashplayershotlocations

    result = leaguedashplayershotlocations.LeagueDashPlayerShotLocations(
        season=season,
        season_type_all_star='Regular Season',
        per_mode_detailed='PerGame',
        headers=CUSTOM_HEADERS,
        timeout=120,
    ).get_dict()['resultSets']

    time.sleep(_RATE_LIMIT_DELAY)

    def triplet(row, zone_idx):
        base = 6 + zone_idx * 3
        return row[base], row[base + 1], row[base + 2]  # fgm, fga, fg_pct

    out: dict[int, dict] = {}
    for row in result['rowSet']:
        ra_fgm, ra_fga, ra_pct = triplet(row, 0)
        paint_fgm, paint_fga, paint_pct = triplet(row, 1)
        _, mid_fga, mid_pct = triplet(row, 2)
        _, corner3_fga, _ = triplet(row, 7)  # combined Corner 3 column
        out[row[0]] = {
            'ra_fgm': ra_fgm, 'ra_fga': ra_fga, 'ra_fg_pct': ra_pct,
            'paint_fgm': paint_fgm, 'paint_fga': paint_fga, 'paint_fg_pct': paint_pct,
            'mid_fga': mid_fga, 'mid_fg_pct': mid_pct,
            'corner3_fga': corner3_fga,
        }
    return out


def fetch_defense_stats(season: str) -> dict[int, dict]:
    """Return {player_id: defensive matchup observations} for a season.

    Two LeagueDashPtDefend calls (rim + threes). PLUSMINUS = defended FG%
    minus those same shooters' season-normal FG% — negative is good defense.
    """
    from nba_api.stats.endpoints import leaguedashptdefend

    out: dict[int, dict] = {}

    rim = leaguedashptdefend.LeagueDashPtDefend(
        season=season,
        season_type_all_star='Regular Season',
        defense_category='Less Than 6Ft',
        per_mode_simple='PerGame',
        headers=CUSTOM_HEADERS,
        timeout=120,
    ).get_normalized_dict()['LeagueDashPTDefend']
    time.sleep(_RATE_LIMIT_DELAY)
    for row in rim:
        out.setdefault(row['CLOSE_DEF_PERSON_ID'], {}).update({
            'd_lt6_fga': row.get('FGA_LT_06'),
            'd_lt6_plusminus': row.get('PLUSMINUS'),
        })

    threes = leaguedashptdefend.LeagueDashPtDefend(
        season=season,
        season_type_all_star='Regular Season',
        defense_category='3 Pointers',
        per_mode_simple='PerGame',
        headers=CUSTOM_HEADERS,
        timeout=120,
    ).get_normalized_dict()['LeagueDashPTDefend']
    time.sleep(_RATE_LIMIT_DELAY)
    for row in threes:
        out.setdefault(row['CLOSE_DEF_PERSON_ID'], {}).update({
            'd_fg3a': row.get('FG3A'),
            'd_fg3_plusminus': row.get('PLUSMINUS'),
        })

    overall = leaguedashptdefend.LeagueDashPtDefend(
        season=season,
        season_type_all_star='Regular Season',
        defense_category='Overall',
        per_mode_simple='PerGame',
        headers=CUSTOM_HEADERS,
        timeout=120,
    ).get_normalized_dict()['LeagueDashPTDefend']
    time.sleep(_RATE_LIMIT_DELAY)
    for row in overall:
        out.setdefault(row['CLOSE_DEF_PERSON_ID'], {}).update({
            'd_fga': row.get('D_FGA'),
            'd_plusminus': row.get('PCT_PLUSMINUS'),
        })

    return out


def fetch_line_score(game_id: str) -> dict:
    """Per-quarter scores for one game: {team_id: {"q": [q1..q4], "ot": total}}.

    Empty dict when the API has no line score (some seasons lack coverage).
    """
    from nba_api.stats.endpoints import boxscoresummaryv2

    rows = boxscoresummaryv2.BoxScoreSummaryV2(
        game_id=str(game_id), headers=CUSTOM_HEADERS, timeout=60
    ).get_normalized_dict()['LineScore']
    time.sleep(_RATE_LIMIT_DELAY)

    out: dict = {}
    for row in rows:
        if row.get('PTS_QTR1') is None:
            continue
        ot = sum(row.get(f'PTS_OT{i}') or 0 for i in range(1, 11))
        out[row['TEAM_ID']] = {
            'q': [row['PTS_QTR1'], row['PTS_QTR2'], row['PTS_QTR3'], row['PTS_QTR4']],
            'ot': ot,
        }
    return out


def fetch_box_score(game_id: int) -> list[dict]:
    """Pull per-player box scores for a single game."""
    # TODO: implement using nba_api.stats.endpoints.boxscoretraditionalv2
    raise NotImplementedError
