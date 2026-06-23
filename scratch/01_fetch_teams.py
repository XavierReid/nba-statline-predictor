from nba_api.stats.static import teams
from pprint import pprint

print(len(teams.get_teams()))
pprint(teams.get_teams()[0]['id'])