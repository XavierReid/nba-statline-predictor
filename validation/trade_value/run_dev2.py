"""Amendment 2 development analysis: apply the registered selection rule ONCE, on dev teams only,
and write results/frozen_model_v2.json (committed before the holdout is opened).

Reads results/measure_dev.json (dev teams, 2024-25) and the R1 shrink/availability choice. The
holdout files are not read here.
"""
import json

from validation.trade_value import analysis as A

R = "validation/trade_value/results/"


def main():
    meas = A.load_measure(R + "measure_dev.json")
    season = meas["meta"]["season"]
    prods, names = A.load_productions(season)
    rows = A.condition_rows(meas)
    v1 = json.load(open(R + "frozen_model.json"))
    shrink, avail = v1["shrink"], v1["availability"]            # R1 Stage-1 result, carried over

    spec_sel = A.select_spec(rows, prods, shrink, avail)
    spec = spec_sel["selected"]
    s2 = A.select_stage2(rows, prods, shrink, avail, spec)
    k_star = s2["k_star"]
    audits = {sp: A.replacement_audit(prods, names, sp) for sp in A.ALL_SPECS}
    rel = A.reliability(rows)

    def cand(label, k):
        return {"label": label, "spec": spec, "k": k, "beta": s2["table"][str(k)]["beta"]}

    frozen = {
        "frozen_from": "dev teams, 2024-25, results/measure_dev.json, Amendment 2 rule (A2.3)",
        "shrink": shrink, "availability": avail, "selected_spec": spec, "k_star": k_star,
        "decisive_candidates": [cand("linear", 1.0), cand("modest", 1.5), cand("best_fit", k_star)],
        "baseline": {"label": "game_score_linear", "spec": "game_score", "k": 1.0,
                     "beta": A.variant_scores(rows, prods, 1.0, shrink, avail, "game_score")["beta"]},
        "descriptive_specs_k1": {sp: {"spec": sp, "k": 1.0, "beta": spec_sel["table"][sp]["beta"]} for sp in A.ALL_SPECS},
        "dev_replacement_level": {sp: audits[sp]["replacement"] for sp in A.ALL_SPECS},
        "dev_rho": {"spec_table_mean_single_package": {sp: spec_sel["table"][sp]["mean_single_package"] for sp in A.ALL_SPECS}},
    }
    report = {"season": season, "spec_selection": spec_sel, "stage2_k": s2, "replacement_audits": audits,
              "reliability": rel}
    json.dump(report, open(R + "dev_report_v2.json", "w"), indent=1)
    json.dump(frozen, open(R + "frozen_model_v2.json", "w"), indent=1)

    print(f"AMENDMENT-2 DEV  season {season}  shrink={shrink} availability={avail}")
    print(f"{'spec':24s} {'single':>7s} {'package':>8s} {'mean':>7s}  eligible")
    for sp, v in spec_sel["table"].items():
        print(f"{sp:24s} {v['rho_single']:7.3f} {v['rho_package']:8.3f} {v['mean_single_package']:7.3f}  {v['eligible']}")
    print("SELECTED SPEC:", spec)
    print(f"k grid (package rho) for {spec}:", {k: round(v["rho_package"], 3) for k, v in s2["table"].items()}, "-> k* =", k_star)
    print("noise ceilings (dev): single", round(rel["single"]["ceiling"], 3), "package", round(rel["package"]["ceiling"], 3))
    for sp in A.ALL_SPECS:
        a = audits[sp]
        zero = {b: round(v["share_zero"], 2) for b, v in a["by_mpg_bucket"].items()}
        print(f"replacement[{sp}] = {a['replacement']:.2f} (team-mean sd {a['team_mean_sd']:.2f}, "
              f"teams {a['teams_used']}); share V=0 by MPG bucket {zero}; corr(value, MPG) {a['corr_value_mpg']:.2f}")
    low = audits[spec]["highest_valued_under_10_mpg"]
    print(f"highest-valued players under 10 MPG ({spec}):", [(x['name'], x['mpg'], x['value']) for x in low[:3]])


if __name__ == "__main__":
    main()
