"""Rolling-origin evaluation of the minutes models on DEVELOPMENT targets (selection evidence, protocol Sec. 7).

For each evaluation target T, constants are fitted on dev targets earlier than T only. The locked seasons are
never read (lockguard). Output: per-season MAE for M0/M1/M2, paired relative reductions, bias by group, extremes.

    scripts/vrun nba_backfill validation.ratings.minutes_eval
"""
import json
from collections import defaultdict

from validation.ratings.asof import AsOfReader
from validation.ratings.asof_db import DBSource
from validation.ratings.minutes_models import MODELS, feature_rows, fit, predict

DEV = ["2014-15", "2015-16", "2016-17", "2017-18", "2018-19", "2019-20", "2020-21", "2021-22"]
EVAL = DEV[2:]
T5 = 2.571      # t, 5 d.f.


def main():
    src = DBSource()
    data = {}
    for t in DEV:
        feats = feature_rows(AsOfReader(src, t))
        act = {r["player_id"]: r["minutes_per_game"] for r in src.season_stats(t) if (r["games_played"] or 0) >= 10}
        data[t] = {pid: (f, act[pid]) for pid, f in feats.items() if pid in act}
    res, per, coefs = defaultdict(dict), defaultdict(list), {}
    groups = defaultdict(lambda: defaultdict(list))
    extremes = defaultdict(lambda: [0, 0])
    for t in EVAL:
        train = [v for s in DEV if s < t for v in data[s].values()]
        coefs[t] = {m: fit(m, [f for f, _ in train], [y for _, y in train]) for m in ("M1", "M2")}
        for m in MODELS:
            errs = []
            for pid, (f, y) in data[t].items():
                p = predict(m, coefs[t].get(m), f)
                errs.append(p - y)
                lo, hi = f["m0"] < 20, f["m0"] >= 30
                for g in ("all", "changed" if f["changed"] else "same_team", "low_gp" if f["gp"] < 41 else "gp41+",
                          "prior<10" if f["m0"] < 10 else "prior10-20" if f["m0"] < 20 else "prior20-30" if f["m0"] < 30 else "prior30+"):
                    groups[m][g].append(p - y)
                extremes[m][0] += p > 40
                extremes[m][1] += p < 0
            res[m][t] = sum(abs(e) for e in errs) / len(errs)
            per[m].append((len(errs), sum(e for e in errs) / len(errs)))
    print("MAE by evaluation season (rolling origin; constants fitted on earlier dev targets only)")
    print("%-9s" % "season", *("%8s" % m for m in MODELS))
    for t in EVAL:
        print("%-9s" % t, *("%8.2f" % res[m][t] for m in MODELS))
    out = {"mae": res, "coef": coefs, "extremes_over40_under0": extremes}
    print("\nrelative MAE reduction vs M0 (per season, then mean +/- t-interval over seasons, 5 d.f.)")
    for m in ("M1", "M2"):
        red = [1 - res[m][t] / res["M0"][t] for t in EVAL]
        mean = sum(red) / len(red)
        sd = (sum((x - mean) ** 2 for x in red) / (len(red) - 1)) ** 0.5
        hw = T5 * sd / len(red) ** 0.5
        out["reduction_" + m] = {"per_season": red, "mean": mean, "halfwidth": hw}
        print("%s: " % m + " ".join("%+.1f%%" % (100 * x) for x in red) + "   mean %+.1f%% +/- %.1f%%" % (100 * mean, 100 * hw))
    print("\nbias (projected - actual) by group, pooled:   %s" % "   ".join(MODELS))
    for g in ("all", "prior<10", "prior10-20", "prior20-30", "prior30+", "changed", "same_team", "low_gp", "gp41+"):
        print("  %-11s n=%5d  " % (g, len(groups["M0"][g])) + "   ".join("%+6.2f" % (sum(groups[m][g]) / len(groups[m][g])) for m in MODELS))
    print("\nextremes (projected >40 / <0): " + "  ".join("%s %d/%d" % (m, *extremes[m]) for m in MODELS))
    print("latest fitted constants (%s): M1 %s | M2 %s" % (EVAL[-1], [round(x, 3) for x in coefs[EVAL[-1]]["M1"]],
                                                       [round(x, 3) for x in coefs[EVAL[-1]]["M2"]]))
    json.dump(out, open("validation/ratings/results/minutes_eval.json", "w"), indent=1)


if __name__ == "__main__":
    main()
