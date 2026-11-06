"""M3 evidence on development seasons: how much of the real minutes does the projection roster now cover, and how
good is the rookie minutes guess? Rolling origin (bucket means fitted on earlier dev seasons only).

    scripts/vrun nba_backfill validation.ratings.m3_eval
"""
import json
from collections import defaultdict

from validation.ratings.asof import AsOfReader
from validation.ratings.asof_db import DBSource
from validation.ratings.predictors import v1_carry_forward
from validation.ratings.rookie_fit import priors
from validation.ratings.rookie_minutes import rookie_defaults

EVAL = ["2016-17", "2017-18", "2018-19", "2019-20", "2020-21", "2021-22"]


def main():
    src, out, tot_err = DBSource(), {}, defaultdict(list)
    print("%-8s %6s %8s %8s %9s | %s" % ("season", "added", "base%", "+M3%", "gain pts", "rookie mpg MAE: bucket-mean / all-rookie-mean / absent(0)"))
    for t in EVAL:
        reader = AsOfReader(src, t)
        have = {pid for pid, p in v1_carry_forward(reader).items() if p["mpg"]}
        pri = priors(src, t)
        add = rookie_defaults(reader, have, pri)
        rows = {r["player_id"]: r for r in src.season_stats(t)}
        mins = lambda ids: sum((rows[i]["minutes_per_game"] or 0) * (rows[i]["games_played"] or 0) for i in ids if i in rows)
        total = mins(rows)
        on_roster = set(reader.preseason_roster_proxy("early", 10)) & have      # who the projection roster actually contains
        base, extra = mins(on_roster), mins(add)
        e_b = [abs(a["mpg"] - rows[i]["minutes_per_game"]) for i, a in add.items() if i in rows]
        e_a = [abs(pri["ALL"]["mpg"] - rows[i]["minutes_per_game"]) for i in add if i in rows]
        e_0 = [rows[i]["minutes_per_game"] for i in add if i in rows]
        for k, v in (("bucket", e_b), ("all", e_a), ("zero", e_0)):
            tot_err[k] += v
        by = defaultdict(int)
        for a in add.values():
            by[a["bucket"]] += 1
        out[t] = {"added": len(add), "by_bucket": dict(by), "base_share": base / total, "with_m3_share": (base + extra) / total,
                  "mae_bucket": sum(e_b) / len(e_b), "mae_all": sum(e_a) / len(e_a), "mae_zero": sum(e_0) / len(e_0), "priors": pri}
        print("%-8s %6d %7.1f%% %7.1f%% %+8.1f | %.2f / %.2f / %.2f" % (
            t, len(add), 100 * base / total, 100 * (base + extra) / total, 100 * extra / total,
            out[t]["mae_bucket"], out[t]["mae_all"], out[t]["mae_zero"]))
    print("pooled rookie mpg MAE: bucket-mean %.2f | one league-wide rookie mean %.2f | leaving them out %.2f" % tuple(
        sum(tot_err[k]) / len(tot_err[k]) for k in ("bucket", "all", "zero")))
    print("latest bucket means (%s): %s" % (EVAL[-1], {b: (round(v["mpg"], 1), round(v["gp"]), v["n"]) for b, v in out[EVAL[-1]]["priors"].items()}))
    json.dump(out, open("validation/ratings/results/m3_eval.json", "w"), indent=1)


if __name__ == "__main__":
    main()
