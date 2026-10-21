"""Holdout G6a/G6b: python -m validation.trade_value.run_holdout --split holdoutA|holdoutB

Reads results/frozen_model.json and NEVER refits. Reports the three frozen candidates
(linear, modest, best-fit) against the simulator ground truth on unseen teams (A) and on unseen
teams in an unseen season (B), with the noise ceiling and team-clustered bootstrap intervals.
"""
import argparse
import hashlib
import json
import statistics as st
from collections import defaultdict

from app.services.trade_value import build_value_model
from validation.trade_value import analysis as A
from validation.trade_value import harness as H

R = "validation/trade_value/results/"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", required=True, choices=["holdoutA", "holdoutB"])
    args = ap.parse_args()
    raw = open(R + "frozen_model.json", "rb").read()
    frozen = json.loads(raw)
    meas = A.load_measure(R + f"measure_{args.split}.json")
    season = meas["meta"]["season"]
    prods, names = A.load_productions(season)
    idx = A.prod_index(prods)
    rows = A.condition_rows(meas)
    singles = [r for r in rows if r["kind"] == "single"]
    packs = [r for r in rows if r["kind"] == "package"]
    rel, inter = A.reliability(rows), A.interaction(rows)

    cands = {}
    for c in frozen["candidates"]:
        m = build_value_model(prods, k=c["k"], shrink=frozen["shrink"], availability=frozen["availability"])
        vs, vp, va = A.model_values(singles, m, idx), A.model_values(packs, m, idx), A.model_values(rows, m, idx)
        for r, v in zip(singles, vs):
            r.setdefault("pred", {})[c["label"]] = v
        for r, v in zip(packs, vp):
            r.setdefault("pred", {})[c["label"]] = v
        cands[c["label"]] = {"k": c["k"], "model": m, "vs": vs, "vp": vp, "va": va, "beta": c["beta"]}

    by_team_s, by_team_p = defaultdict(list), defaultdict(list)
    for r in singles:
        by_team_s[r["team"]].append(r)
    for r in packs:
        by_team_p[r["team"]].append(r)

    def rho_stat(label, kind):
        key = "pred"
        return lambda rs: H.spearman([x[key][label] for x in rs], [x["imp"] for x in rs])

    out = {"season": season, "split": args.split, "frozen_sha256": hashlib.sha256(raw).hexdigest()[:16],
           "replacement_level": next(iter(cands.values()))["model"].replacement,
           "reliability": rel, "interaction": inter,
           "additive_reference_rho_package": A.additive_reference(rows), "candidates": {}}
    naive_mae = st.mean(abs(r["imp"] - st.mean(x["imp"] for x in rows)) for r in rows)
    for label, c in cands.items():
        g6a = H.cluster_bootstrap(by_team_s, rho_stat(label, "s"), n_boot=2000)
        g6b = H.cluster_bootstrap(by_team_p, rho_stat(label, "p"), n_boot=2000)
        mae = st.mean(abs(c["beta"] * v - r["imp"]) for v, r in zip(c["va"], rows))
        out["candidates"][label] = {
            "k": c["k"], "G6a_rho_single": g6a, "G6a_fraction_of_ceiling": g6a[0] / rel["single"]["ceiling"],
            "G6b_rho_package": g6b, "G6b_fraction_of_ceiling": g6b[0] / rel["package"]["ceiling"],
            "mae_points_frozen_beta": mae, "mae_points_naive_mean": naive_mae,
            "pass_G6a": g6a[0] >= 0.60, "pass_G6b": g6b[0] >= 0.60,
        }
    # convexity test: does extra convexity beat linear on PACKAGE Spearman? (protocol Section 8)
    conv = {}
    for label in ("modest", "best_fit"):
        diff = lambda rs, lab=label: (H.spearman([x["pred"][lab] for x in rs], [x["imp"] for x in rs])
                                      - H.spearman([x["pred"]["linear"] for x in rs], [x["imp"] for x in rs]))
        d = H.cluster_bootstrap(by_team_p, diff, n_boot=2000)
        conv[label] = {"rho_diff_vs_linear": d[0], "ci95": [d[1], d[2]],
                       "supported": d[0] >= 0.03 and d[1] > 0}
    out["convexity_test"] = conv
    supported = [l for l in conv if conv[l]["supported"]]
    pk = {l: out["candidates"][l]["G6b_rho_package"][0] for l in out["candidates"]}
    if supported:
        recommended = max(supported, key=lambda l: pk[l])
    else:
        best = max(pk.values())
        recommended = min((l for l in pk if pk[l] >= best - 0.02), key=lambda l: out["candidates"][l]["k"])
    out["recommended_model"] = recommended
    with open(R + f"{args.split}_report.json", "w") as f:
        json.dump(out, f, indent=1)

    print(f"{args.split.upper()}  season {season}  teams {len(meas['meta']['teams'])}  frozen sha {out['frozen_sha256']}  R={out['replacement_level']:.2f}")
    for kind, v in rel.items():
        print(f"  reliability[{kind}] r_AB={v['r_AB']:.3f} ceiling={v['ceiling']:.3f} mean SE={v['mean_se']:.2f}")
    print(f"  additive-reference package rho {out['additive_reference_rho_package']:.3f}; interaction mean {inter['mean']:+.2f} sd {inter['sd']:.2f}")
    for label, c in out["candidates"].items():
        a, b = c["G6a_rho_single"], c["G6b_rho_package"]
        print(f"  {label:9s} k={c['k']:<5} G6a rho {a[0]:.3f} [{a[1]:.2f},{a[2]:.2f}] ({c['G6a_fraction_of_ceiling']:.0%} of ceiling)"
              f" | G6b rho {b[0]:.3f} [{b[1]:.2f},{b[2]:.2f}] ({c['G6b_fraction_of_ceiling']:.0%})"
              f" | MAE {c['mae_points_frozen_beta']:.2f} vs naive {c['mae_points_naive_mean']:.2f}")
    print("  convexity test:", {k: (round(v["rho_diff_vs_linear"], 3), [round(x, 3) for x in v["ci95"]], v["supported"]) for k, v in conv.items()})
    print("  recommended model:", recommended)


if __name__ == "__main__":
    main()
