"""Build the shadow database for one end-to-end arm (Amendment 3, A3.2 / A3.5).

The engine is not modified. Each arm gets its own database, cloned from the isolated backfill, in which the
rows the engine reads for season T are exactly the arm's inputs and nothing else about season T survives
(player lines, tendencies, attributes, team stats, game logs for seasons >= T are removed first). The real
schedule (`games`) stays. Because every arm runs in its own process against its own database, the
season-keyed module caches in the engine cannot carry one arm's data into another.

Arms: P (V1 carry-forward placeholder), D (P's roster/minutes, no player profile), O_r (observed T lines on
P's roster/minutes), E (nothing for T, no repair), O (an UNMODIFIED clone of the backfill, so simulation runs
are written to a throwaway copy rather than the isolated backfill itself).

    python -m validation.ratings.e2e.arms 2022-23 P      # prints the database url and writes the manifest
"""
import hashlib
import json
import os
import sys

from sqlalchemy import create_engine, text

from validation.ratings.asof import AsOfReader, prev_season
from validation.ratings.asof_db import DBSource
from validation.ratings.predictors import v1_carry_forward

BASE_DB = "nba_backfill"
ADMIN = "postgresql+psycopg2://nba:nba@postgres:5432/{}"
ARMS = ("P", "D", "O_r", "E", "O")
PLAYER_TABLES = ("player_season_stats", "player_tendencies", "player_attributes")
PLACEHOLDER = "V1 carry-forward (baseline placeholder, not a fitted model)"


def split_label(label: str):
    """'P_m3' -> ('P', 'm3'); 'P' -> ('P', '')."""
    return (label[:-3], "m3") if label.endswith("_m3") else (label, "")


def arm_db_name(arm: str, target: str) -> str:
    return "nba_arm_%s_%s" % (arm.lower(), target.replace("-", "_"))


def roster_minutes_spec(target: str, m3: bool = False) -> dict:
    """P's roster and minutes, shared by P, D and O_r: R-early team (identity-only proxy) x V1 minutes.
    With m3, early-roster players V1 cannot project get their draft-bucket expected minutes and games."""
    src = DBSource()
    reader = AsOfReader(src, target)
    proxy = reader.preseason_roster_proxy("early", 10)
    preds = v1_carry_forward(reader)
    spec = {}
    for pid, team in proxy.items():
        p = preds.get(pid)
        if p and p["mpg"]:
            spec[pid] = {"team_id": team, "mpg": p["mpg"], "gp": int(round(p["gp_share"] * 82)), "rookie": False}
    if m3:
        from validation.ratings.rookie_fit import priors
        from validation.ratings.rookie_minutes import rookie_defaults
        for pid, r in rookie_defaults(reader, set(spec), priors(src, target)).items():
            spec[pid] = {"team_id": r["team_id"], "mpg": r["mpg"], "gp": r["gp"], "rookie": True}
    return spec


def _cols(conn, table):
    rows = conn.execute(text("select column_name from information_schema.columns where table_name=:t "
                             "order by ordinal_position"), {"t": table}).all()
    return [r[0] for r in rows if r[0] != "id"]


def _copy(conn, table, src_where, overrides, params):
    """INSERT ... SELECT every non-id column from `src_where`, replacing columns named in `overrides`."""
    cols = _cols(conn, table)
    sel = ", ".join(overrides.get(c, c) for c in cols)
    conn.execute(text("insert into %s (%s) select %s from %s_src where %s" %
                      (table, ", ".join(cols), sel, table, src_where)), params)


def field_manifest(label: str, target: str) -> dict:
    arm, variant = split_label(label)
    prev = prev_season(target)
    carry = {"source": "prior_season_carry_forward", "from_season": prev, "available_at_cutoff": True}
    obs = {"source": "observed_season_T", "from_season": target, "available_at_cutoff": False,
           "note": "look-ahead oracle input"}
    omitted = {"source": "omitted_engine_default", "available_at_cutoff": True}
    roster = {"source": "roster_proxy_R_early", "available_at_cutoff": True,
              "note": "identity and team of first appearance within 10 games; no outcome values"}
    minutes = {"source": "v1_prior_season_minutes_and_games", "from_season": prev, "available_at_cutoff": True}
    default = {"source": "column_default", "available_at_cutoff": True}
    team = {"source": "prior_season_team_stats", "from_season": prev, "available_at_cutoff": True}
    m = {"arm": arm, "target": target, "method": PLACEHOLDER if arm == "P" else None,
         "player_season_stats": {"*": omitted if arm == "D" else (obs if arm == "O_r" else carry),
                                 "team_id": roster, "minutes_per_game": minutes, "games_played": minutes},
         "player_tendencies": {"*": omitted if arm == "D" else (obs if arm == "O_r" else carry)},
         "player_attributes": {"*": default if arm == "D" else (obs if arm == "O_r" else carry)},
         "team_season_stats": {"*": team}, "player_game_logs": {"*": {"source": "removed", "note": "availability target off"}},
         "schedule": {"source": "real_schedule", "available_at_cutoff": True}}
    if variant == "m3":
        m["m3_rookies"] = {"source": "draft_bucket_mean_minutes_and_games", "available_at_cutoff": True,
                           "note": "players with no prior season: presence, team, minutes and games only; profile at engine defaults"}
    if arm == "E":
        m = {"arm": "E", "target": target, "note": "no season-T rows of any kind; no repair"}
    return m


def leak_free(manifest: dict) -> bool:
    if manifest["arm"] not in ("P", "D", "E"):
        return True
    return all(f.get("available_at_cutoff", True) for t, v in manifest.items() if isinstance(v, dict) for f in
               ([v] if "source" in v else v.values()) if isinstance(f, dict))


def build_arm(label: str, target: str) -> str:
    arm, variant = split_label(label)
    assert arm in ARMS and (not variant or arm in ("P", "D", "O_r"))
    name = arm_db_name(label, target)
    prev = prev_season(target)
    spec = roster_minutes_spec(target, m3=variant == "m3")
    admin = create_engine(ADMIN.format("postgres"), isolation_level="AUTOCOMMIT", future=True)
    with admin.connect() as c:
        c.execute(text("select pg_terminate_backend(pid) from pg_stat_activity where datname in (:a, :b) and pid <> pg_backend_pid()"),
                  {"a": name, "b": BASE_DB})
        c.execute(text('drop database if exists "%s"' % name))
        c.execute(text('create database "%s" template "%s"' % (name, BASE_DB)))
    if arm == "O":
        json.dump({"database": name, "note": "unmodified clone of the backfill"},
                  open("validation/ratings/results/e2e/arm_O_%s.json" % target, "w"), indent=1)
        return name
    eng = create_engine(ADMIN.format(name), future=True)
    manifest = field_manifest(label, target)
    with eng.begin() as c:
        c.execute(text("create table arm_spec (player_id int primary key, team_id int, mpg double precision, gp int, rookie boolean default false)"))
        for pid, s in spec.items():
            c.execute(text("insert into arm_spec values (:p, :t, :m, :g, :r)"), {"p": pid, "t": s["team_id"], "m": s["mpg"], "g": s["gp"], "r": s["rookie"]})
        for t in PLAYER_TABLES + ("team_season_stats",):
            c.execute(text("create table %s_src as select * from %s where season in (:p, :t)" % (t, t)), {"p": prev, "t": target})
            c.execute(text("delete from %s where season >= :t" % t), {"t": target})
        c.execute(text("delete from player_game_logs where season >= :t"), {"t": target})
        if arm != "E":
            _copy(c, "team_season_stats", "season = :p", {"season": ":t"}, {"p": prev, "t": target})
            for t in PLAYER_TABLES:
                _copy_player(c, arm, t, prev, target)
            if arm == "P" and variant == "m3":       # no prior line to carry: same identity-only rows as D
                _insert_identity_rows(c, target, "where a.rookie")
    eng.dispose()
    d = {"database": name, "rookies_added": sum(1 for v in spec.values() if v["rookie"]), "spec_players": len(spec), "spec_sha256": hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest(),
         "manifest": manifest, "leak_free": leak_free(manifest)}
    os.makedirs("validation/ratings/results/e2e", exist_ok=True)
    json.dump(d, open("validation/ratings/results/e2e/arm_%s_%s.json" % (arm, target), "w"), indent=1, default=str)
    return name


def _insert_identity_rows(c, target, where, only=None):
    """Identity + minutes/games rows with no player profile: the engine reads nulls and column defaults."""
    for table in ([only] if only else list(PLAYER_TABLES)):
        if table == "player_season_stats":
            ins = "insert into player_season_stats (player_id, season, team_id, minutes_per_game, games_played) " \
                  "select a.player_id, :t, a.team_id, a.mpg, a.gp from arm_spec a %s" % where
        else:   # tendencies / attributes: identity only
            ins = "insert into %s (player_id, season) select a.player_id, :t from arm_spec a %s" % (table, where)
        c.execute(text(ins), {"t": target})


def _copy_player(c, arm, table, prev, target):
    """Insert the arm's season-T rows for one player table from the _src copy."""
    cols = _cols(c, table)
    src_season = target if arm == "O_r" else prev
    join = "join arm_spec a on a.player_id = s.player_id"
    if arm == "D":
        _insert_identity_rows(c, target, "", table)
        return
    over = {"season": ":t"}
    if table == "player_season_stats":
        over.update({"team_id": "a.team_id", "minutes_per_game": "a.mpg", "games_played": "a.gp"})
    sel = ", ".join(("s." + x if x not in over else over[x]) if x != "season" else ":t" for x in cols)
    c.execute(text("insert into %s (%s) select %s from %s_src s %s where s.season = :src" %
                   (table, ", ".join(cols), sel, table, join)), {"t": target, "src": src_season})


if __name__ == "__main__":
    print(build_arm(sys.argv[2], sys.argv[1]))
