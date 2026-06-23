from nba_api.stats.static import players, teams
from nba_api.stats.endpoints import commonteamroster, teaminfocommon, leaguegamefinder
from pprint import pprint


headers = {
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

games = leaguegamefinder.LeagueGameFinder(team_id_nullable=1610612748,headers=headers,timeout=60,season_nullable='2025-26',season_type_nullable='Regular Season')
pprint(games.get_normalized_dict()['LeagueGameFinderResults'])