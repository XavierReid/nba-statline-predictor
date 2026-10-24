"""Coverage, identity and production-agreement report for the isolated backfill database.

    docker compose run --rm -e DATABASE_URL=postgresql+psycopg2://nba:nba@postgres:5432/nba_backfill \\
        api python -m validation.ratings.validate_backfill

Reads the isolated database (default connection) and, READ-ONLY, production (for the overlap
diff). Writes validation/ratings/results/backfill_validation.json. Reports problems; fixes none.
"""
import json
from collections import defaultdict

from sqlalchemy import create_engine, text

from app.config import settings

assert settings.database_url.endswith("/nba_backfill"), "must run against the isolated database"
from app.database import engine                       # noqa: E402
from validation.ratings import rawstore                # noqa: E402

PROD = create_engine("postgresql+psycopg2://nba:nba@postgres:5432/nba_predictor", future=True)
SEASONS = [f"{y}-{str(y + 1)[2:]}" for y in range(2013, 2026)]
OVERLAP = ["2013-14", "2016-17", "2019-20", "2020-21", "2024-25", "2025-26"]
KEY_COLS = ["games_played", "minutes_per_game", "points", "rebounds", "assists", "fga", "fta", "fg3m", "usg_pct"]


def season_window(season):
    y = int(season[:4])
    return f"{y}-10-01", f"{y + 1}-07-15"


def q(conn, sql, **p):
    return conn.execute(text(sql), p).all()


def main():
    manifest = rawstore.load_manifest()
    by_ep = defaultdict(list)
    for e in manifest:
        if e.get("status") == "ok":
            by_ep[e["endpoint"]].append(e)
    report = {"seasons": {}, "identity": {}, "overlap_vs_production": {}, "raw_store": rawstore.verify_all()}
    with engine.connect() as c:
        for s in SEASONS:
            lo, hi = season_window(s)
            r = {}
            r["pss_rows"] = q(c, "select count(*) from player_season_stats where season=:s", s=s)[0][0]
            r["pss_players"] = q(c, "select count(distinct player_id) from player_season_stats where season=:s", s=s)[0][0]
            r["pss_teams"] = q(c, "select count(distinct team_id) from player_season_stats where season=:s and team_id is not null and team_id<>0", s=s)[0][0]
            r["pss_tot_or_null_team_rows"] = q(c, "select count(*) from player_season_stats where season=:s and (team_id is null or team_id=0)", s=s)[0][0]
            r["attributes"] = q(c, "select count(*) from player_attributes where season=:s", s=s)[0][0]
            r["tendencies"] = q(c, "select count(*) from player_tendencies where season=:s", s=s)[0][0]
            r["team_season_stats"] = q(c, "select count(*) from team_season_stats where season=:s", s=s)[0][0]
            r["games_final"] = q(c, "select count(*) from games where game_date between :lo and :hi and status='final'", lo=lo, hi=hi)[0][0]
            r["games_all"] = q(c, "select count(*) from games where game_date between :lo and :hi", lo=lo, hi=hi)[0][0]
            r["games_last_date"] = str(q(c, "select max(game_date) from games where game_date between :lo and :hi and status='final'", lo=lo, hi=hi)[0][0])
            r["game_logs"] = q(c, "select count(*) from player_game_logs l join games g on g.id=l.game_id where g.game_date between :lo and :hi", lo=lo, hi=hi)[0][0]
            r["shot_location_coverage"] = round(q(c, "select avg((ra_fga is not null)::int) from player_season_stats where season=:s", s=s)[0][0] or 0, 3)
            r["defense_coverage"] = round(q(c, "select avg((d_fga is not null)::int) from player_season_stats where season=:s", s=s)[0][0] or 0, 3)
            r["rated_players_passing_gate"] = q(c, "select count(*) from player_season_stats where season=:s and games_played>=20 and minutes_per_game>=15", s=s)[0][0]
            teams_with_82 = q(c, """select count(*) from (select t, count(*) n from (
                   select home_team_id t from games where game_date between :lo and :hi and status='final'
                   union all select away_team_id from games where game_date between :lo and :hi and status='final') x group by t) y where n>=82""", lo=lo, hi=hi)[0][0]
            r["teams_with_>=82_final_games"] = teams_with_82
            for ep in ("bio_" + s, "draft_" + s):
                r[ep.split("_")[0] + "_raw_rows"] = (by_ep.get(ep) or [{}])[-1].get("rows")
            report["seasons"][s] = r

        # ---- identity: names per id across seasons, ids per name, continuity
        names = defaultdict(set)
        for e in by_ep.get("fetch_season_stats", []):
            for row in rawstore.read_payload(e):
                names[row["PLAYER_ID"]].add(row["PLAYER_NAME"])
        multi_name = {pid: sorted(n) for pid, n in names.items() if len(n) > 1}
        by_name = defaultdict(set)
        for pid, ns in names.items():
            for n in ns:
                by_name[n].add(pid)
        dup_name = {n: sorted(p) for n, p in by_name.items() if len(p) > 1}
        report["identity"] = {
            "players_total": len(names), "ids_with_multiple_names": len(multi_name),
            "multi_name_examples": dict(list(multi_name.items())[:5]),
            "names_shared_by_multiple_ids": len(dup_name), "dup_name_examples": dict(list(dup_name.items())[:5]),
        }
        cont = {}
        prev = None
        for s in SEASONS:
            ids = {r[0] for r in q(c, "select distinct player_id from player_season_stats where season=:s", s=s)}
            cont[s] = {"n": len(ids), "carried_from_prev": len(ids & prev) if prev else None,
                       "new": len(ids - prev) if prev else None}
            prev = ids
        report["identity"]["continuity"] = cont
        # bio coverage of stats players
        bio_cov = {}
        for s in SEASONS:
            ents = by_ep.get("bio_" + s)
            if not ents:
                bio_cov[s] = None
                continue
            bio_ids = {row["PLAYER_ID"] for row in rawstore.read_payload(ents[-1])}
            ids = {r[0] for r in q(c, "select distinct player_id from player_season_stats where season=:s and games_played>=1", s=s)}
            bio_cov[s] = round(len(ids & bio_ids) / max(len(ids), 1), 3)
        report["identity"]["bio_covers_share_of_stats_players"] = bio_cov

    # ---- overlap vs production (read-only)
    with engine.connect() as c, PROD.connect() as p:
        for s in OVERLAP:
            cols = ", ".join(KEY_COLS)
            new = {(r[0], r[1]): r[2:] for r in q(c, f"select player_id, coalesce(team_id,-1), {cols} from player_season_stats where season=:s", s=s)}
            old = {(r[0], r[1]): r[2:] for r in p.execute(text(f"select player_id, coalesce(team_id,-1), {cols} from player_season_stats where season=:s"), {"s": s}).all()}
            both = new.keys() & old.keys()
            maxdiff, ndiff = defaultdict(float), 0
            for k in both:
                d = False
                for name, a, b in zip(KEY_COLS, new[k], old[k]):
                    if a is None and b is None:
                        continue
                    if a is None or b is None or abs(float(a) - float(b)) > 1e-9:
                        maxdiff[name] = max(maxdiff[name], abs(float(a or 0) - float(b or 0)))
                        d = True
                ndiff += d
            attr_new = {(r[0]): r[1:] for r in q(c, "select player_id, overall_rating, three_point, free_throw, passing from player_attributes where season=:s", s=s)}
            attr_old = {(r[0]): r[1:] for r in p.execute(text("select player_id, overall_rating, three_point, free_throw, passing from player_attributes where season=:s"), {"s": s}).all()}
            ab = attr_new.keys() & attr_old.keys()
            games_old = p.execute(text("select count(*) from games where game_date between :lo and :hi and status='final'"), dict(zip(("lo", "hi"), season_window(s)))).scalar()
            report["overlap_vs_production"][s] = {
                "stat_rows_backfill": len(new), "stat_rows_production": len(old),
                "only_in_backfill": len(new.keys() - old.keys()), "only_in_production": len(old.keys() - new.keys()),
                "rows_with_any_key_stat_difference": ndiff, "max_abs_diff_by_column": {k: round(v, 4) for k, v in maxdiff.items()},
                "attribute_rows_compared": len(ab), "attribute_rows_identical": sum(attr_new[k] == attr_old[k] for k in ab),
                "final_games_backfill": report["seasons"][s]["games_final"], "final_games_production": games_old,
            }
    json.dump(report, open("validation/ratings/results/backfill_validation.json", "w"), indent=1, default=str)

    print(f"{'season':8s} {'pss':>4s} {'tms':>3s} {'tot/nul':>7s} {'attr':>5s} {'tsS':>3s} {'games':>6s} {'last game':>10s} {'t>=82':>5s} {'logs':>6s} {'ra%':>5s} {'def%':>5s} {'gate':>4s} {'bio':>4s} {'draft':>5s}")
    for s, r in report["seasons"].items():
        print(f"{s:8s} {r['pss_rows']:4d} {r['pss_teams']:3d} {r['pss_tot_or_null_team_rows']:7d} {r['attributes']:5d} {r['team_season_stats']:3d} "
              f"{r['games_final']:6d} {r['games_last_date']:>10s} {r['teams_with_>=82_final_games']:5d} {r['game_logs']:6d} "
              f"{r['shot_location_coverage']:5.2f} {r['defense_coverage']:5.2f} {r['rated_players_passing_gate']:4d} {str(r['bio_raw_rows']):>4s} {str(r['draft_raw_rows']):>5s}")
    i = report["identity"]
    print(f"\nIDENTITY: {i['players_total']} players; ids with >1 name: {i['ids_with_multiple_names']}; names shared by >1 id: {i['names_shared_by_multiple_ids']}")
    print("  continuity (n / carried-from-previous / new):", {s: (v['n'], v['carried_from_prev'], v['new']) for s, v in i["continuity"].items()})
    print("  bio covers share of stats players:", i["bio_covers_share_of_stats_players"])
    print("\nOVERLAP vs PRODUCTION:")
    for s, o in report["overlap_vs_production"].items():
        print(f"  {s}: rows {o['stat_rows_backfill']}/{o['stat_rows_production']} (only-bf {o['only_in_backfill']}, only-prod {o['only_in_production']}), "
              f"rows with any stat diff {o['rows_with_any_key_stat_difference']}, attrs identical {o['attribute_rows_identical']}/{o['attribute_rows_compared']}, "
              f"final games {o['final_games_backfill']}/{o['final_games_production']}, max diff {o['max_abs_diff_by_column']}")
    print("\nRAW STORE integrity:", report["raw_store"]["checked"], "payloads checked,", len(report["raw_store"]["bad"]), "bad")


if __name__ == "__main__":
    main()
