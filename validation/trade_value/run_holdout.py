"""Holdout G6a/G6b under the frozen Amendment-2 model:
   python -m validation.trade_value.run_holdout --split holdoutA|holdoutB

Reads results/frozen_model_v2.json and NEVER refits. Pass/fail is judged on the DECISIVE
candidates only (the selected spec at k=1, 1.5, k*); the Game Score baseline and the other
registered specs are reported as descriptive and can never be promoted by this script.
(--measure/--tag exist only to dry-run the pipeline on development data.)
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


def evaluate(label, cfg, frozen, prods, idx, rows, singles, packs, by_team_s, by_team_p, rel, naive_mae):
    m = build_value_model(prods, k=cfg["k"], shrink=frozen["shrink"], availability=frozen["availability"],
                          spec=cfg["spec"])
    vs, vp = A.model_values(singles, m, idx), A.model_values(packs, m, idx)
    reg = singles + packs
    va = A.model_values(reg, m, idx)
    for r, v in zip(singles, vs):
        r.setdefault("pred", {})[label] = v
    for r, v in zip(packs, vp):
        r.setdefault("pred", {})[label] = v
    g6a = H.cluster_bootstrap(by_team_s, lambda rs: H.spearman([x["pred"][label] for x in rs], [x["imp"] for x in rs]), n_boot=2000)
    g6b = H.cluster_bootstrap(by_team_p, lambda rs: H.spearman([x["pred"][label] for x in rs], [x["imp"] for x in rs]), n_boot=2000)
    mae = st.mean(abs(cfg["beta"] * v - r["imp"]) for v, r in zip(va, reg))
    return {"spec": cfg["spec"], "k": cfg["k"], "replacement": m.replacement,
            "G6a_rho_single": g6a, "G6a_fraction_of_ceiling": g6a[0] / rel["single"]["ceiling"],
            "G6b_rho_package": g6b, "G6b_fraction_of_ceiling": g6b[0] / rel["package"]["ceiling"],
            "mae_points_frozen_beta": mae, "mae_points_naive_mean": naive_mae,
            "pass_G6a": g6a[0] >= 0.60, "pass_G6b": g6b[0] >= 0.60}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", required=True, choices=["holdoutA", "holdoutB", "confirm", "dev"])
    ap.add_argument("--measure", default=None)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    if args.split == "dev" and args.tag != "smoke":
        raise SystemExit("--split dev is only for dry runs: pass --tag smoke")
    raw = open(R + "frozen_model_v2.json", "rb").read()
    frozen = json.loads(raw)
    meas = A.load_measure(args.measure or R + f"measure_{args.split}.json")
    season = meas["meta"]["season"]
    prods, names = A.load_productions(season)
    idx = A.prod_index(prods)
    rows = A.condition_rows(meas)
    singles = [r for r in rows if r["kind"] == "single"]
    packs = [r for r in rows if r["kind"] == "package"]
    rel, inter = A.reliability(rows), A.interaction(rows)
    by_team_s, by_team_p = defaultdict(list), defaultdict(list)
    for r in singles:
        by_team_s[r["team"]].append(r)
    for r in packs:
        by_team_p[r["team"]].append(r)
    mean_imp = st.mean(r["imp"] for r in singles + packs)
    naive_mae = st.mean(abs(r["imp"] - mean_imp) for r in singles + packs)

    ev = lambda label, cfg: evaluate(label, cfg, frozen, prods, idx, rows, singles, packs,
                                     by_team_s, by_team_p, rel, naive_mae)
    out = {"season": season, "split": args.split, "tag": args.tag,
           "frozen_sha256": hashlib.sha256(raw).hexdigest()[:16], "selected_spec": frozen["selected_spec"],
           "reliability": rel, "interaction": inter,
           "additive_measured_reference_rho_package": A.additive_reference(rows),
           "decisive": {c["label"]: ev(c["label"], c) for c in frozen["decisive_candidates"]},
           "baseline_game_score": ev("baseline", frozen["baseline"]),
           "descriptive_specs_k1": {sp: ev("d_" + sp, c) for sp, c in frozen["descriptive_specs_k1"].items()}}
    # convexity test: does extra convexity beat linear on PACKAGE Spearman? (Section 8)
    conv = {}
    for label in ("modest", "best_fit"):
        diff = lambda rs, lab=label: (H.spearman([x["pred"][lab] for x in rs], [x["imp"] for x in rs])
                                      - H.spearman([x["pred"]["linear"] for x in rs], [x["imp"] for x in rs]))
        d = H.cluster_bootstrap(by_team_p, diff, n_boot=2000)
        conv[label] = {"rho_diff_vs_linear": d[0], "ci95": [d[1], d[2]], "supported": d[0] >= 0.03 and d[1] > 0}
    out["convexity_test"] = conv
    pk = {l: v["G6b_rho_package"][0] for l, v in out["decisive"].items()}
    supported = [l for l in conv if conv[l]["supported"]]
    if supported:
        recommended = max(supported, key=lambda l: pk[l])
    else:
        best = max(pk.values())
        recommended = min((l for l in pk if pk[l] >= best - 0.02), key=lambda l: out["decisive"][l]["k"])
    out["recommended_model"] = recommended
    rec = out["decisive"][recommended]
    out["verdict"] = {"model": recommended, "G6a_pass": rec["pass_G6a"], "G6b_pass": rec["pass_G6b"]}
    name = f"{args.tag + '_' if args.tag else ''}{args.split}_report_v2.json"
    with open(R + name, "w") as f:
        json.dump(out, f, indent=1)

    print(f"{args.split.upper()} {args.tag}  season {season}  teams {len(meas['meta']['teams'])}  frozen sha {out['frozen_sha256']}  spec {frozen['selected_spec']}")
    for kind, v in rel.items():
        print(f"  reliability[{kind}] r_AB={v['r_AB']:.3f} ceiling={v['ceiling']:.3f} mean SE={v['mean_se']:.2f}")
    print(f"  additive-MEASURED reference package rho {out['additive_measured_reference_rho_package']:.3f}; interaction mean {inter['mean']:+.2f} sd {inter['sd']:.2f}")
    def line(tag, label, c):
        a, b = c["G6a_rho_single"], c["G6b_rho_package"]
        print(f"  {tag:11s}{label:22s} k={c['k']:<5} G6a {a[0]:.3f} [{a[1]:.2f},{a[2]:.2f}] ({c['G6a_fraction_of_ceiling']:.0%}) | "
              f"G6b {b[0]:.3f} [{b[1]:.2f},{b[2]:.2f}] ({c['G6b_fraction_of_ceiling']:.0%}) | MAE {c['mae_points_frozen_beta']:.2f} vs naive {c['mae_points_naive_mean']:.2f}")
    for label, c in out["decisive"].items():
        line("DECISIVE", label, c)
    line("baseline", "game_score k=1", out["baseline_game_score"])
    for sp, c in out["descriptive_specs_k1"].items():
        line("descriptive", sp, c)
    print("  convexity test:", {k: (round(v["rho_diff_vs_linear"], 3), [round(x, 3) for x in v["ci95"]], v["supported"]) for k, v in conv.items()})
    print(f"  recommended (decisive rule): {recommended}  -> G6a pass={rec['pass_G6a']}  G6b pass={rec['pass_G6b']}")


if __name__ == "__main__":
    main()
