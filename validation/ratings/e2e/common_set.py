"""Player-production error on ONE fixed set of players for every arm and variant (fair comparison).

M3 puts more players on the roster, so the top-80 real players present differ between variants and a plain MAE is
not like-for-like. This restricts to the top-80 real players that the V1 roster already contained (set S), and
separately reports the extra players M3 adds (set R). Read-only on the arm databases. Dev seasons only.

    scripts/vrun nba_backfill validation.ratings.e2e.common_set 2016-17
"""
import json
import sys

from sqlalchemy import create_engine, text

from validation.ratings import lockguard

URL = "postgresql+psycopg2://nba:nba@postgres:5432/%s"
LABELS = ["D", "P", "O_r", "D_m3", "P_m3", "O_r_m3", "O"]


def db(label, target):
    return create_engine(URL % ("nba_arm_%s_%s" % (label.lower(), target.replace("-", "_"))), future=True)


def main(target):
    lockguard.check(target)
    with create_engine(URL % "nba_backfill", future=True).connect() as c:
        real = {r[0]: r[1:] for r in c.execute(text(
            "select player_id, points, rebounds, assists, minutes_per_game from player_season_stats where season=:s "
            "order by minutes_per_game*games_played desc limit 80"), {"s": target})}
    with db("P", target).connect() as c:
        v1 = {r[0] for r in c.execute(text("select player_id from arm_spec"))}
    with db("P_m3", target).connect() as c:
        added = {r[0] for r in c.execute(text("select player_id from arm_spec where rookie"))}
    S, R = [p for p in real if p in v1], [p for p in real if p in added]
    out = {"target": target, "n_S": len(S), "n_R": len(R), "arms": {}}
    for label in LABELS:
        with db(label, target).connect() as c:
            sims = [r[0] for r in c.execute(text("select id from simulation_runs where parameters->>'arm' = :a order by id"), {"a": label})]
            per = []
            for sid in sims:
                lines = {r[0]: r[1:] for r in c.execute(text(
                    "select l.player_id, count(*), sum(l.points), sum(l.rebounds), sum(l.assists), sum(l.minutes) "
                    "from simulated_player_lines l join simulated_games g on g.id=l.simulated_game_id "
                    "where g.simulation_id=:s group by l.player_id"), {"s": sid})}
                row = {}
                for name, grp in (("S", S), ("R", R)):
                    have = [p for p in grp if p in lines]
                    if not have:
                        continue
                    for k, st in enumerate(("ppg", "rpg", "apg")):
                        row["%s_%s" % (name, st)] = sum(abs(lines[p][1 + k] / lines[p][0] - real[p][k]) for p in have) / len(have)
                    row["%s_mpg" % name] = sum(abs(lines[p][4] / lines[p][0] - real[p][3]) for p in have) / len(have)
                    row["%s_n" % name] = len(have)
                per.append(row)
        keys = sorted({k for r in per for k in r})
        out["arms"][label] = {k: sum(r[k] for r in per if k in r) / len([r for r in per if k in r]) for k in keys}
    print("%s: set S = %d top-80 players already on the V1 roster; set R = %d top-80 players M3 adds" % (target, len(S), len(R)))
    print("%-9s %8s %8s %8s %8s | %s" % ("arm", "S ppg", "S rpg", "S apg", "S mpg", "R (added players) ppg/rpg/apg/mpg, n"))
    for label in LABELS:
        a = out["arms"][label]
        r = "%.2f/%.2f/%.2f/%.2f n=%d" % (a["R_ppg"], a["R_rpg"], a["R_apg"], a["R_mpg"], a["R_n"]) if "R_ppg" in a else "-"
        print("%-9s %8.2f %8.2f %8.2f %8.2f | %s" % (label, a["S_ppg"], a["S_rpg"], a["S_apg"], a["S_mpg"], r))
    json.dump(out, open("validation/ratings/results/e2e/common_set_%s.json" % target, "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1])
