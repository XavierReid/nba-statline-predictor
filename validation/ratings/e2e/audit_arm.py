"""Structural leakage audit of one arm database (A3.5). Read-only. Exit code 1 on any failure.

    docker compose run --rm -e DATABASE_URL=.../nba_arm_p_2021_22 api python -m validation.ratings.e2e.audit_arm 2021-22 P
"""
import json
import sys

from sqlalchemy import create_engine, text

from app.config import settings
from validation.ratings.asof import prev_season
from validation.ratings.e2e.arms import PLAYER_TABLES, field_manifest, leak_free, split_label

OVERRIDES = {"player_season_stats": {"id", "season", "team_id", "minutes_per_game", "games_played"}}


def main(target, label):
    arm, variant = split_label(label)
    prev, eng = prev_season(target), create_engine(settings.database_url, future=True)
    checks = {}
    with eng.connect() as c:
        q = lambda sql, **p: c.execute(text(sql), p).scalar()
        spec = q("select count(*) from arm_spec")
        for t in PLAYER_TABLES + ("team_season_stats",):
            checks["%s: no season after T" % t] = q("select count(*) from %s where season > :t" % t, t=target) == 0
        checks["game logs for seasons >= T removed"] = q("select count(*) from player_game_logs where season >= :t", t=target) == 0
        if arm == "E":
            for t in PLAYER_TABLES + ("team_season_stats",):
                checks["E: no season-T rows in %s" % t] = q("select count(*) from %s where season = :t" % t, t=target) == 0
        else:
            for t in PLAYER_TABLES:
                checks["%s: T rows are exactly the spec players" % t] = q(
                    "select count(*) from %s s where season=:t and player_id not in (select player_id from arm_spec)" % t, t=target) == 0 \
                    and q("select count(*) from %s where season=:t" % t, t=target) == spec
            same_team = q("select count(*) from team_season_stats a join team_season_stats_src b on b.team_id=a.team_id and b.season=:p "
                          "where a.season=:t and a.pace=b.pace and a.def_rating=b.def_rating", p=prev, t=target)
            checks["team_season_stats T == prior-season values (30 teams)"] = same_team == 30
            checks["minutes/games/team equal the shared spec"] = q(
                "select count(*) from player_season_stats s join arm_spec a using (player_id) where s.season=:t and "
                "s.minutes_per_game=a.mpg and s.games_played=a.gp and s.team_id=a.team_id", t=target) == spec
            if variant == "m3" and arm == "P":
                checks["M3: rookie rows carry no profile (stat columns NULL)"] = q(
                    "select count(*) from player_season_stats s join arm_spec a using (player_id) where s.season=:t and a.rookie "
                    "and (s.points is not null or s.fga is not null or s.fta is not null)", t=target) == 0
            if arm in ("P", "D"):
                cols = [r[0] for r in c.execute(text("select column_name from information_schema.columns where table_name='player_season_stats' order by 1"))
                        if r[0] not in OVERRIDES["player_season_stats"] and r[0] != "player_id"]
                if arm == "D":
                    nonnull = q("select count(*) from player_season_stats where season=:t and (%s)" %
                                " or ".join("%s is not null" % x for x in cols), t=target)
                    checks["D: every non-spec stat-line column is NULL"] = nonnull == 0
                else:
                    diff = q("select count(*) from player_season_stats s join player_season_stats_src b on b.player_id=s.player_id and b.season=:p "
                             "where s.season=:t and (%s)" % " or ".join("s.%s is distinct from b.%s" % (x, x) for x in cols), p=prev, t=target)
                    checks["P: stat line equals the prior-season line (non-spec columns)"] = diff == 0
    m = field_manifest(label, target)
    checks["manifest: every P/D/E input available at the cutoff"] = leak_free(m)
    ok = all(checks.values())
    json.dump({"arm": arm, "target": target, "checks": checks, "pass": ok},
              open("validation/ratings/results/e2e/audit_%s_%s.json" % (arm, target), "w"), indent=1)
    for k, v in checks.items():
        print("PASS" if v else "FAIL", k)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
