"""Development analysis: audits, the pre-registered Stage 1/2 selection, and the frozen model.

Reads results/measure_dev.json (dev teams, 2024-25 ONLY). Writes results/dev_report.json and
results/frozen_model.json. This is the only place any parameter is chosen; holdout code reads
the frozen file and never refits.
"""
import json
import statistics as st

from validation.trade_value import analysis as A

R = "validation/trade_value/results/"


def main():
    meas = A.load_measure(R + "measure_dev.json")
    season = meas["meta"]["season"]
    prods, names = A.load_productions(season)
    rows = A.condition_rows(meas)

    rel, inter = A.reliability(rows), A.interaction(rows)
    s1 = A.select_stage1(rows, prods)
    s2 = A.select_stage2(rows, prods, s1["shrink"], s1["availability"])
    by_cond = {c: st.mean(r["imp"] for r in rows if r["cond"] == c) for c in sorted({r["cond"] for r in rows})}
    report = {
        "season": season, "teams": meas["meta"]["teams"], "games_per_condition_per_set": meas["meta"]["games"],
        "mean_impact_by_condition": by_cond, "reliability": rel, "interaction": inter,
        "additive_reference_rho_package": A.additive_reference(rows),
        "replacement_audit": A.replacement_audit(prods, names),
        "redistribution": A.redistribution(meas), "stage1": s1, "stage2": s2,
    }
    k_star = s2["k_star"]
    frozen = {
        "frozen_from": "dev teams, 2024-25, results/measure_dev.json — chosen by the pre-registered rules",
        "shrink": s1["shrink"], "availability": s1["availability"], "k_star": k_star,
        "candidates": [
            {"label": "linear", "k": 1.0, "beta": s2["table"]["1.0"]["beta"]},
            {"label": "modest", "k": 1.5, "beta": s2["table"]["1.5"]["beta"]},
            {"label": "best_fit", "k": k_star, "beta": s2["table"][str(k_star)]["beta"]},
        ],
        "dev_replacement_level": report["replacement_audit"]["replacement"],
        "dev_rho": {"single_stage1": s1["best_rho"], "package_stage2": s2["best_rho_package"]},
    }
    with open(R + "dev_report.json", "w") as f:
        json.dump(report, f, indent=1)
    with open(R + "frozen_model.json", "w") as f:
        json.dump(frozen, f, indent=1)

    print(f"DEV  season {season}  teams {len(report['teams'])}  G/cond/set {report['games_per_condition_per_set']}")
    print("mean impact (pts/game) by condition:", {k: round(v, 2) for k, v in by_cond.items()})
    for kind, v in rel.items():
        print(f"reliability[{kind}] n={v['n']} r_AB={v['r_AB']:.3f} ceiling={v['ceiling']:.3f} mean SE={v['mean_se']:.2f}")
    print(f"interaction: mean {inter['mean']:+.2f} sd {inter['sd']:.2f} |z| {inter['mean_abs_z']:.2f} "
          f"beyond 2SE {inter['share_beyond_2se']:.0%};  additive-reference package rho {report['additive_reference_rho_package']:.3f}")
    print("STAGE1 (k=1):", {k: round(v["rho_single"], 3) for k, v in s1["table"].items()},
          "->", {"shrink": s1["shrink"], "availability": s1["availability"]})
    print("STAGE2 package rho by k:", {k: round(v["rho_package"], 3) for k, v in s2["table"].items()}, "-> k* =", k_star)
    print("single rho by k (should be ~constant):", {k: round(v["rho_single"], 3) for k, v in s2["table"].items()})


if __name__ == "__main__":
    main()
