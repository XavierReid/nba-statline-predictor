"""Pooled confirmation analysis (Amendment 3): python -m validation.trade_value.run_pooled

Pools the frozen model's holdout evidence across holdout A, holdout B and the confirmation season,
for G6a, G6b and G6c, with bootstrap clusters = (split, team). The frozen model is read from
results/frozen_model_v2.json and nothing is refit. Per-split numbers are always printed next to
the pooled ones so heterogeneity is visible. (--splits/--tag allow a dry run on development data.)
"""
import argparse
import hashlib
import json
from collections import defaultdict

from app.services.trade_value import build_value_model
from validation.trade_value import analysis as A
from validation.trade_value import harness as H

R = "validation/trade_value/results/"
BAR = 0.60


def load_split(name, frozen, measure_path=None, raw_path=None):
    meas = A.load_measure(measure_path or R + f"measure_{name}.json")
    season = meas["meta"]["season"]
    prods, _ = A.load_productions(season)
    idx = A.prod_index(prods)
    model = build_value_model(prods, k=frozen["decisive_candidates"][0]["k"], shrink=frozen["shrink"],
                              availability=frozen["availability"], spec=frozen["selected_spec"])
    base = build_value_model(prods, k=1.0, shrink=frozen["shrink"], availability=frozen["availability"], spec="game_score")
    rows = A.condition_rows(meas)
    for r in rows:
        if r["kind"] in ("single", "package"):
            r["cluster"] = (name, r["team"])
            r["pred"] = sum(model.value(idx[(pid, r["team"])]) for pid in r["removed"])
            r["pred_gs"] = sum(base.value(idx[(pid, r["team"])]) for pid in r["removed"])
    g6c = None
    try:
        results = json.load(open(raw_path or R + f"g6c_{name}_raw.json"))
        g6c = A.g6c_rows(results, model, idx)
        for r in g6c:
            r["cluster"] = (name, r["cpu_team"])
    except OSError:
        pass
    return {"name": name, "season": season, "rows": rows, "g6c": g6c, "rel": A.reliability(rows)}


def rho(rows, kind, key="pred"):
    sel = [r for r in rows if r["kind"] == kind]
    return H.spearman([r[key] for r in sel], [r["imp"] for r in sel])


def boot(rows, kind, key="pred"):
    by = defaultdict(list)
    for r in rows:
        if r["kind"] == kind:
            by[r["cluster"]].append(r)
    keymap = {c: i for i, c in enumerate(sorted(by))}
    return H.cluster_bootstrap({keymap[c]: v for c, v in by.items()},
                               lambda rs: H.spearman([x[key] for x in rs], [x["imp"] for x in rs]), n_boot=2000)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", nargs="+", default=["holdoutA", "holdoutB", "confirm"])
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    raw = open(R + "frozen_model_v2.json", "rb").read()
    frozen = json.loads(raw)
    splits = [load_split(n, frozen) for n in args.splits]
    pooled = [r for s in splits for r in s["rows"]]
    out = {"frozen_sha256": hashlib.sha256(raw).hexdigest()[:16], "splits": {}, "pooled": {}}
    print(f"frozen sha {out['frozen_sha256']}  spec {frozen['selected_spec']}  splits {[ (s['name'], s['season']) for s in splits ]}")
    print(f"{'':10s}{'G6a single rho':>26s}{'G6b package rho':>26s}   noise ceilings (s/p)")
    for s in splits:
        a, b = boot(s["rows"], "single"), boot(s["rows"], "package")
        out["splits"][s["name"]] = {"season": s["season"], "G6a": a, "G6b": b,
                                    "ceilings": [s["rel"]["single"]["ceiling"], s["rel"]["package"]["ceiling"]]}
        print(f"{s['name']:10s}{a[0]:12.3f} [{a[1]:.2f},{a[2]:.2f}]{b[0]:14.3f} [{b[1]:.2f},{b[2]:.2f}]   "
              f"{s['rel']['single']['ceiling']:.2f}/{s['rel']['package']['ceiling']:.2f}")
    pa, pb = boot(pooled, "single"), boot(pooled, "package")
    pa_gs, pb_gs = rho([r for r in pooled], "single", "pred_gs"), rho([r for r in pooled], "package", "pred_gs")
    out["pooled"]["G6a"], out["pooled"]["G6b"] = pa, pb
    out["pooled"]["baseline_game_score"] = {"G6a": pa_gs, "G6b": pb_gs}
    n_s = sum(r["kind"] == "single" for r in pooled)
    n_p = sum(r["kind"] == "package" for r in pooled)
    print(f"{'POOLED':10s}{pa[0]:12.3f} [{pa[1]:.2f},{pa[2]:.2f}]{pb[0]:14.3f} [{pb[1]:.2f},{pb[2]:.2f}]   (n={n_s} singles, {n_p} packages; Game Score baseline {pa_gs:.3f} / {pb_gs:.3f})")
    out["pooled"]["pass_G6a"], out["pooled"]["pass_G6b"] = pa[0] >= BAR, pb[0] >= BAR

    g6c_ok = all(s["g6c"] is not None for s in splits)
    if g6c_ok:
        rows_all = [r for s in splits for r in s["g6c"]]
        for s in splits:
            o = A.g6c_metrics(s["g6c"], 1.0, boot=False)["overall"]
            print(f"G6c {s['name']:9s} n={o['n']:3d} accept {o['accept_rate']:.0%} P(pres|acc) {o['p_preserve_given_accepted']:.0%} "
                  f"lift {o['lift_vs_base']:+.0%} recall {o['p_accepted_given_beneficial']:.0%}")
        m = A.g6c_metrics(rows_all, 1.0)
        o = m["overall"]
        o["pass"] = {"preserve_given_accepted_ge_0.85": o["p_preserve_given_accepted"] >= 0.85,
                     "lift_ge_0.10": o["lift_vs_base"] >= 0.10,
                     "accept_rate_in_10_70": 0.10 <= o["accept_rate"] <= 0.70,
                     "recall_beneficial_ge_0.40": o["p_accepted_given_beneficial"] >= 0.40}
        o["pass_all"] = all(o["pass"].values())
        out["pooled"]["G6c"] = m
        ci = lambda k: "[%.2f,%.2f]" % tuple(o[k + "_ci"])
        print(f"G6c POOLED  n={o['n']} accept {o['accept_rate']:.0%} {ci('accept_rate')} | P(pres|acc) {o['p_preserve_given_accepted']:.0%} {ci('p_preserve_given_accepted')}"
              f" | lift {o['lift_vs_base']:+.0%} {ci('lift_vs_base')} | recall {o['p_accepted_given_beneficial']:.0%} {ci('p_accepted_given_beneficial')} | pass_all={o['pass_all']}")
        for ty, v in m["by_type"].items():
            print(f"    {ty:15s} n={v['n']:3d} accept {v['accept_rate']:.0%} P(pres|acc) {v['p_preserve_given_accepted']:.0%} "
                  f"mean delta acc {v['mean_delta_accepted']:+.2f} rej {v['mean_delta_rejected']:+.2f}")
    verdict = out["pooled"]["pass_G6a"] and out["pooled"]["pass_G6b"] and g6c_ok and out["pooled"]["G6c"]["overall"]["pass_all"]
    out["pooled"]["PRE_REGISTERED_VERDICT_PASS"] = bool(verdict)
    print("\nPRE-REGISTERED POOLED VERDICT:", "PASS" if verdict else "FAIL (negative)")
    name = f"{args.tag + '_' if args.tag else ''}pooled_report_v2.json"
    json.dump(out, open(R + name, "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
