"""DataSource over the isolated backfill database and the raw payload store (read-only)."""
from datetime import date
from typing import List

from sqlalchemy import create_engine, text

from validation.ratings import lockguard, rawstore
from validation.ratings.asof import DataSource

BACKFILL_URL = "postgresql+psycopg2://nba:nba@postgres:5432/nba_backfill"


def _window(season: str):
    y = int(season[:4])
    return date(y, 10, 1), date(y + 1, 7, 15)


class DBSource(DataSource):
    def __init__(self, url: str = BACKFILL_URL):
        assert url.endswith("/nba_backfill"), "DBSource only reads the isolated backfill database"
        self.engine = create_engine(url, future=True)
        self._manifest = [e for e in rawstore.load_manifest() if e.get("status") == "ok"]

    def _rows(self, sql: str, **p) -> List[dict]:
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(text(sql), p)]

    def season_stats(self, season):
        lockguard.check(season)
        return self._rows("select * from player_season_stats where season=:s order by player_id", s=season)

    def team_stats(self, season):
        lockguard.check(season)
        return self._rows("select * from team_season_stats where season=:s order by team_id", s=season)

    def game_logs(self, season):
        lockguard.check(season)
        lo, hi = _window(season)
        games = self._rows("""select id, home_team_id, away_team_id from games
                              where game_date between :lo and :hi and status='final' order by game_date, id""",
                           lo=lo, hi=hi)
        n, gno = {}, {}
        for g in games:
            for t in (g["home_team_id"], g["away_team_id"]):
                n[t] = n.get(t, 0) + 1
                gno[(g["id"], t)] = n[t]
        rows = self._rows("select game_id, player_id, team_id, minutes, pts, reb, ast, fgm, fga, fg3m, fta, ftm, tov "
                          "from player_game_logs where season=:s", s=season)
        out = []
        for r in rows:
            r["game_no"] = gno.get((r["game_id"], r["team_id"]))
            if r["game_no"] is not None:
                out.append(r)
        return out

    def _raw(self, endpoint: str):
        hits = [e for e in self._manifest if e["endpoint"] == endpoint]
        return rawstore.read_payload(hits[-1]) if hits else []

    def bio(self, season):
        lockguard.check(season)
        return self._raw("bio_" + season)

    def draft_class(self, year):
        lockguard.check(f"{year}-{str(year + 1)[2:]}")
        return self._raw(f"draft_{year}-{str(year + 1)[2:]}")

    def first_game(self, season):
        lockguard.check(season)
        lo, hi = _window(season)
        return self._rows("select min(game_date) d from games where game_date between :lo and :hi", lo=lo, hi=hi)[0]["d"]

    def last_game(self, season):
        lockguard.check(season)
        lo, hi = _window(season)
        return self._rows("select max(game_date) d from games where game_date between :lo and :hi and status='final'",
                          lo=lo, hi=hi)[0]["d"]
