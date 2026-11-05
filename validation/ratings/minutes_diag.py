"""Where does the carry-forward minutes projection go wrong? (A2.3 diagnostics, dev seasons only)

For each dev season: projected MPG (prior-season MPG, V1) vs actual MPG for players with >= 10 games, split by
simple situations, plus how many of each team's real top-8 minute players the projection roster misses.

    scripts/vrun nba_backfill validation.ratings.minutes_diag
"""
import json
from collections import defaultdict

from validation.ratings.asof import prev_season
from validation.ratings.asof_db import DBSource
from validation.ratings.e2e.arms import roster_minutes_spec

DEV = ["2014-15", "2015-16", "2016-17", "2017-18", "2018-19", "2021-22"]


def main():
    src, agg, miss = DBSource(), defaultdict(list), []
    for t in DEV:
        spec = roster_minutes_spec(t)
        actual = {r["player_id"]: r for r in src.season_stats(t)}
        prior = {r["player_id"]: r for r in src.season_stats(prev_season(t))}
        prior_team = {pid: r["team_id"] for pid, r in prior.items()}
        for pid, s in spec.items():
            a = actual.get(pid)
            if not a or (a["games_played"] or 0) < 10:
                continue
            err = s["mpg"] - a["minutes_per_game"]
            pm = prior[pid]["minutes_per_game"]
            cats = ["all", "prior_mpg_%s" % ("30+" if pm >= 30 else "20-30" if pm >= 20 else "10-20" if pm >= 10 else "<10"),
                    "changed_team" if prior_team[pid] != s["team_id"] else "same_team",
                    "prior_gp_<41" if (prior[pid]["games_played"] or 0) < 41 else "prior_gp_41+"]
            for c in cats:
                agg[c].append((err, abs(err)))
        by_team = defaultdict(list)
        for pid, a in actual.items():
            if (a["games_played"] or 0) >= 10:
                by_team[a["team_id"]].append((a["minutes_per_game"] * a["games_played"], pid))
        tot = missing = 0
        for tid, lst in by_team.items():
            for _, pid in sorted(lst, reverse=True)[:8]:
                tot += 1
                missing += pid not in spec
        miss.append((t, missing, tot))
    print("%-16s %6s %8s %8s" % ("group", "n", "bias", "MAE"))
    out = {}
    for c in sorted(agg):
        v = agg[c]
        out[c] = {"n": len(v), "bias": sum(e for e, _ in v) / len(v), "mae": sum(x for _, x in v) / len(v)}
        print("%-16s %6d %+8.2f %8.2f" % (c, len(v), out[c]["bias"], out[c]["mae"]))
    print("\ntop-8-by-minutes real players missing from the projection roster:")
    print("  " + "  ".join("%s %d/%d" % m for m in miss))
    out["missing_top8"] = miss
    json.dump(out, open("validation/ratings/results/minutes_diag.json", "w"), indent=1)


if __name__ == "__main__":
    main()
